"""Pinned-origin capability gateway. No credential-store or Hermes imports."""
import asyncio
import mimetypes
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType

from .control import Controller, Busy
from .store import Store
from .rfb import ClientFilter

MAX_WS_BYTES = 1024 * 1024
MAX_LIVE = 4
HANDSHAKE_TIMEOUT = 5.0
IO_TIMEOUT = 0.5
CLOSE_TIMEOUT = 0.1

COOKIE = '__Host-computer_view'
STATIC = Path(__file__).parent / 'static'
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'self' data: blob:; font-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")


def create_app(data_dir: Path, settings, *, store=None, controller=None):
    parsed = urlsplit(settings.origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or settings.origin != 'https://' + parsed.netloc):
        raise ValueError('A canonical HTTPS origin is required')
    host = parsed.netloc
    store = store if store is not None else Store(Path(data_dir) / 'access.sqlite3')
    if controller is None:
        controller = Controller(data_dir)
        # Only explicit setup provisions a new state. A deleted/corrupt state
        # must never turn an existing human-held desktop back into agent mode.
        controller.status()

    def check_host(request, origin=False):
        if request.headers.getall('Host', []) != [host]:
            raise web.HTTPForbidden()
        if origin and request.headers.getall('Origin', []) != [settings.origin]:
            raise web.HTTPForbidden()

    def session(request):
        check_host(request)
        token = request.cookies.get(COOKIE, '')
        info = store.session_info(token, host)
        if not info or info['owner'] != settings.owner_id:
            raise web.HTTPUnauthorized()
        return token, info

    @web.middleware
    async def security(request, handler):
        try:
            check_host(request)
            response = await handler(request)
        except web.HTTPException as exc:
            headers(exc)
            raise
        except Exception:
            failure = web.HTTPServiceUnavailable(text='Computer View unavailable')
            headers(failure)
            raise failure from None
        headers(response)
        return response

    def headers(response):
        response.headers.update({'Content-Security-Policy': CSP, 'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Strict-Transport-Security': 'max-age=31536000',
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()'})

    app = web.Application(middlewares=[security], client_max_size=4096)

    def read_file(root, relative):
        # Walk with openat+O_NOFOLLOW, including directories; no resolve/open race.
        parts = relative.split('/')
        if not parts or any(not p or p.startswith('.') or '\\' in p or ':' in p for p in parts):
            raise web.HTTPNotFound()
        fd = None
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if index < len(parts)-1:
                    flags |= os.O_DIRECTORY
                newfd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = newfd
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > 16 * 1024 * 1024:
                raise web.HTTPNotFound()
            with os.fdopen(fd, 'rb') as stream:
                fd = None
                body = stream.read(16 * 1024 * 1024 + 1)
            return web.Response(body=body, content_type=mimetypes.guess_type(relative)[0] or 'application/octet-stream')
        except OSError:
            raise web.HTTPNotFound() from None
        finally:
            if fd is not None:
                os.close(fd)

    async def public(request):
        name = {'/':'entry.html', '/open':'entry.html', '/entry.js':'entry.js', '/entry.css':'viewer.css',
                '/static/entry.js':'entry.js', '/static/viewer.css':'viewer.css'}[request.path]
        return read_file(STATIC, name)

    async def desktop(request):
        session(request)
        name = request.path.removeprefix('/static/').removeprefix('/')
        if name == 'desktop':
            name = 'viewer.html'
        return read_file(STATIC, name)

    attempts = []

    async def redeem(request):
        check_host(request, True)
        now = time.monotonic()
        attempts[:] = [stamp for stamp in attempts if now-stamp < 60]
        if len(attempts) >= 30:
            raise web.HTTPTooManyRequests()
        attempts.append(now)
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest() from None
        if not isinstance(body, dict) or set(body) != {'token'} or not isinstance(body['token'], str):
            raise web.HTTPBadRequest()
        token = store.redeem(body['token'], host)
        info = store.session_info(token, host) if token else None
        if not info or info['owner'] != settings.owner_id:
            raise web.HTTPUnauthorized()
        response = web.json_response({'ok': True, 'expires_at': info['expires_at']})
        response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite='Strict', path='/',
                            max_age=max(0, int(info['expires_at'] - time.time())))
        return response

    for path in ('/', '/open', '/entry.js', '/entry.css', '/static/entry.js', '/static/viewer.css'):
        app.router.add_get(path, public)
    for path in ('/desktop', '/viewer.js', '/viewer.css', '/viewer-loader.js',
                 '/static/viewer.js', '/static/viewer-loader.js'):
        app.router.add_get(path, desktop)
    live = {}
    slots = 0
    # Serialize disconnect admission/failure persistence with handback. Never
    # hold an OS lock across an await: cross-process contention is retried below.
    cleanup_lock = asyncio.Lock()

    def release(connection, allowed):
        if allowed:
            data = connection['filter'].release_inputs()
            if data:
                connection['writer'].write(data)

    async def drain_connections(connections):
        async def drain(connection):
            try:
                await asyncio.wait_for(connection['writer'].drain(), IO_TIMEOUT)
                return True
            except Exception:
                connection['writer'].close()
                return False
        return all(await asyncio.gather(*(drain(c) for c in connections)))

    async def record_release_failure(viewer_id, epoch):
        # Retain the failed connection in live until the durable fence is set.
        # A concurrent handback therefore also encounters its failed transport.
        while True:
            try:
                controller.release_failed(viewer_id, epoch)
                return
            except Busy:
                await asyncio.sleep(0.01)

    async def stream(request):
        nonlocal slots
        check_host(request, True)
        token, info = session(request)
        if slots >= MAX_LIVE:
            raise web.HTTPTooManyRequests()
        slots += 1
        writer = None
        ws = None
        tasks = []
        connection = None
        try:
            try:
                async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                    reader, writer = await asyncio.open_unix_connection(str(settings.rfb_socket))
                    banner = await reader.readexactly(12)
                    if banner != b'RFB 003.008\n':
                        raise ValueError('Unsupported RFB server')
            except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                raise web.HTTPBadGateway(text='Desktop unavailable') from None
            # Opening the private socket can await; authorize again immediately before upgrade.
            session(request)
            ws = web.WebSocketResponse(max_msg_size=MAX_WS_BYTES, compress=False,
                                       autoping=False, autoclose=False, timeout=CLOSE_TIMEOUT)
            headers(ws)
            await ws.prepare(request)
            allowed = False
            client_filter = ClientFilter(lambda: allowed)
            connection = {'writer': writer, 'filter': client_filter, 'viewer_id': info['viewer_id'], 'ws': ws,
                          'epoch': None}
            live[id(connection)] = connection
            finished_handshake = asyncio.Event()
            window = time.monotonic()
            frame_count = byte_count = 0

            def still_valid():
                current = store.session_info(token, host)
                return bool(current and current['owner'] == settings.owner_id)

            async def receive():
                nonlocal allowed, window, frame_count, byte_count
                async for message in ws:
                    if not still_valid():
                        return
                    now = time.monotonic()
                    if now - window >= 1:
                        window, frame_count, byte_count = now, 0, 0
                    frame_count += 1
                    byte_count += len(message.data) if isinstance(message.data, (bytes, str)) else 0
                    if frame_count > 240 or byte_count > 4 * MAX_WS_BYTES:
                        return
                    if message.type == WSMsgType.PING:
                        await ws.pong(message.data)
                        continue
                    if message.type == WSMsgType.PONG:
                        continue
                    if message.type != WSMsgType.BINARY:
                        return

                    def forward(may_input):
                        nonlocal allowed
                        allowed = may_input
                        # Session validation, parsing, and writer.write are synchronous inside
                        # the SAME process-wide lock as agent tool admission.
                        if not still_valid():
                            raise ValueError('Session ended')
                        if may_input:
                            connection['epoch'] = controller.status()['epoch']
                        data = client_filter.feed(message.data)
                        if data:
                            writer.write(data)
                    controller.frame(info['viewer_id'], forward)
                    await asyncio.wait_for(writer.drain(), IO_TIMEOUT)

            async def send():
                await ws.send_bytes(banner)
                security = await reader.readexactly(2)
                if security != b'\x01\x01':
                    raise ValueError('Only RFB None security is supported')
                await ws.send_bytes(security)
                result = await reader.readexactly(4)
                if result != b'\0'*4:
                    raise ValueError('RFB security failed')
                await ws.send_bytes(result)
                init = await reader.readexactly(24)
                length = int.from_bytes(init[20:24], 'big')
                if length > 65536:
                    raise ValueError('RFB server name too long')
                await ws.send_bytes(init + await reader.readexactly(length))
                finished_handshake.set()
                while data := await reader.read(65536):
                    if not still_valid():
                        return
                    await asyncio.wait_for(ws.send_bytes(data), IO_TIMEOUT)

            async def watch():
                started = time.monotonic()
                while True:
                    await asyncio.sleep(0.25)
                    if not still_valid():
                        return
                    if not finished_handshake.is_set() and time.monotonic()-started >= HANDSHAKE_TIMEOUT:
                        return

            tasks = [asyncio.create_task(fn()) for fn in (receive, send, watch)]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if connection is not None:
                async with cleanup_lock:
                    epoch = connection['epoch']
                    try:
                        def cleanup(permit):
                            # A recovered/replaced lease must not receive late
                            # writes (or failure state) from this connection.
                            if epoch is None or controller.status()['epoch'] != epoch:
                                return
                            if not permit:
                                raise ValueError('Input release admission denied')
                            release(connection, True)
                        controller.frame(info['viewer_id'], cleanup)
                        if not await drain_connections([connection]):
                            await record_release_failure(info['viewer_id'], epoch)
                    except (OSError, ValueError):
                        await record_release_failure(info['viewer_id'], epoch)
                    finally:
                        # Keep the connection visible until release/drain or its
                        # durable failure fence has settled, not just write().
                        live.pop(id(connection), None)
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), CLOSE_TIMEOUT)
                except (OSError, asyncio.TimeoutError):
                    pass
            if ws is not None:
                try:
                    await asyncio.wait_for(ws.close(code=1008, message=b'Connection ended', drain=False), CLOSE_TIMEOUT)
                except (OSError, asyncio.TimeoutError):
                    if request.transport is not None:
                        request.transport.abort()
            slots -= 1
        return ws

    async def shutdown(application):
        await asyncio.gather(*(c['ws'].close(code=1001) for c in list(live.values())), return_exceptions=True)

    app.on_shutdown.append(shutdown)
    app.router.add_get('/websockify', stream)

    async def status(request):
        _, info = session(request)
        return web.json_response({**controller.status(info['viewer_id']), 'expires_at': info['expires_at']})

    async def control(request):
        async with cleanup_lock:
            return await change_control(request)

    async def change_control(request):
        check_host(request, True)
        _, info = session(request)
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest() from None
        if not isinstance(body, dict) or set(body) != {'action'} or body['action'] not in ('takeover', 'handback'):
            raise web.HTTPBadRequest()
        _, info = session(request)
        connections = [c for c in live.values() if c['viewer_id'] == info['viewer_id']]
        try:
            if body['action'] == 'handback':
                def before_release():
                    for connection in connections:
                        release(connection, True)
                handback_token = controller.begin_handback(info['viewer_id'], before_release=before_release)
                try:
                    if not await drain_connections(connections):
                        raise OSError('Input releases did not drain')
                    result = controller.finish_handback(info['viewer_id'], handback_token)
                except BaseException:
                    # Never hold a blocking OS lock across an await. Epoch checks
                    # prevent a late drain from releasing a recovered/new lease.
                    try:
                        controller.fail_handback(info['viewer_id'], handback_token)
                    except Busy:
                        pass
                    raise
            else:
                result = controller.takeover(info['viewer_id'])
        except Busy:
            raise web.HTTPConflict(text='Control is busy') from None
        except PermissionError:
            raise web.HTTPForbidden() from None
        return web.json_response({**result, 'expires_at': info['expires_at']})

    async def novnc(request):
        session(request)
        relative = request.match_info['path']
        if relative.split('/')[0] not in ('core', 'app', 'vendor'):
            raise web.HTTPNotFound()
        return read_file(settings.novnc_dir, relative)

    app.router.add_get('/api/status', status)
    app.router.add_post('/api/control', control)
    app.router.add_get('/novnc/{path:.*}', novnc)
    app.router.add_post('/auth/redeem', redeem)
    return app
