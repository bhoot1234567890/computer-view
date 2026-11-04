"""Computer View: one-time-link authentication in front of a private desktop."""
import asyncio
import base64
import hashlib
import os
import signal
import socket
import stat
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType
from computer_view.store import Store, SESSION_TTL
from computer_view.config import Config, private_directory, validate_origin

COOKIE = '__Host-computer_view'
STORE = web.AppKey('store', Store)
BASE = web.AppKey('base', Path)

ENTRY_HTML = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Computer View</title><link rel="stylesheet" href="/entry.css"></head>
<body><main><h1>Computer View</h1><p>Open your private agent desktop.</p>
<button id="open" disabled>Open Computer View</button><p id="status">Checking link…</p>
<p class="note">Single-use link · 30-minute session. Do not forward this link.</p>
</main><script src="/entry.js" defer></script></body></html>'''
ENTRY_JS = ''''use strict';
(() => {
  const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  history.replaceState(null, '', '/open');
  const button = document.getElementById('open');
  const status = document.getElementById('status');
  button.disabled = !token;
  status.textContent = token ? 'Tap to open. This link expires in five minutes.' : 'Ask your agent for a fresh Computer View link.';
  button.addEventListener('click', async (event) => {
    if (!event.isTrusted || !token) return;
    button.disabled = true;
    try {
      const response = await fetch('/auth/redeem', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token})
      });
      if (!response.ok) throw new Error('This link is expired or already used. Ask for a new Computer View link.');
      location.replace('/vnc.html?autoconnect=1&resize=scale');
    } catch (error) {
      status.textContent = error.message;
    }
  });
})();'''
ENTRY_CSS = '''body{margin:0;background:#10131b;color:#e7eaf1;font:17px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}main{max-width:32rem;padding:2rem}button{font:inherit;padding:.9rem 1.2rem;border:0;border-radius:8px;background:#8ab4ff;color:#10131b;cursor:pointer}button:disabled{opacity:.5;cursor:default}.note{font-size:.85rem;color:#a9b3c8}'''


def same_origin(request, base, origin_provider=None):
    try:
        configured = validate_origin(origin_provider() if origin_provider else (base / 'public-url.txt').read_text().strip())
        parsed = urlsplit(configured)
    except (OSError, ValueError):
        return False
    return request.headers.getall('Origin', []) == [configured] and request.host == parsed.netloc


def create_app(base, *, store=None, rfb_socket=None, novnc_dir=None, origin_provider=None):
    base = Path(base)
    rfb_socket = Path(rfb_socket) if rfb_socket is not None else base / 'rfb.sock'
    novnc_dir = Path(novnc_dir) if novnc_dir is not None else base / 'novnc'
    app = web.Application(client_max_size=2048)
    app[BASE] = base
    app[STORE] = store or Store(base / 'access.sqlite3')
    redemption_times = deque()
    live_slots = asyncio.Semaphore(4)

    async def headers(request, response):
        hashes = []
        html_file = novnc_dir / 'vnc.html'
        if html_file.is_file():
            source = html_file.read_text()
            for attributes, script in re.findall(r'<script\b([^>]*)>(.*?)</script>', source, re.S | re.I):
                if not re.search(r'\bsrc\s*=', attributes):
                    encoded = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
                    hashes.append("'sha256-" + encoded + "'")
        response.headers.update({
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'none'; script-src 'self' " + ' '.join(hashes) + "; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
        })

    app.on_response_prepare.append(headers)

    async def entry(request):
        return web.Response(text=ENTRY_HTML, content_type='text/html')

    async def script(request):
        return web.Response(text=ENTRY_JS, content_type='application/javascript')

    async def style(request):
        return web.Response(text=ENTRY_CSS, content_type='text/css')

    async def redeem(request):
        if not same_origin(request, base, origin_provider):
            raise web.HTTPForbidden(text='Origin rejected')
        now = time.monotonic()
        while redemption_times and redemption_times[0] <= now - 60:
            redemption_times.popleft()
        if len(redemption_times) >= 30:
            raise web.HTTPTooManyRequests(text='Try again in a minute', headers={'Retry-After': '60'})
        redemption_times.append(now)
        if request.content_type != 'application/json':
            raise web.HTTPUnsupportedMediaType()
        try:
            data = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest(text='Invalid request')
        token = data.get('token') if isinstance(data, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise web.HTTPUnauthorized(text='Invalid or expired link')
        session = app[STORE].redeem(token, request.host)
        if not session:
            raise web.HTTPUnauthorized(text='Invalid or expired link')
        response = web.json_response({'ok': True, 'session_seconds': SESSION_TTL})
        response.set_cookie(COOKIE, session, max_age=SESSION_TTL,
                            path='/', secure=True, httponly=True, samesite='Strict')
        return response

    async def asset(request):
        session = request.cookies.get(COOKIE, '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', session) or not app[STORE].valid(session, request.host):
            raise web.HTTPUnauthorized(text='Ask your agent for a fresh Computer View link.')
        relative = request.match_info.get('asset', '') or 'vnc.html'
        parts = Path(relative).parts
        if not parts or any(part.startswith('.') for part in parts):
            raise web.HTTPNotFound()
        if parts[0] not in ('vnc.html', 'app', 'core', 'vendor'):
            raise web.HTTPNotFound()
        root = novnc_dir.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def live(request):
        if live_slots.locked():
            raise web.HTTPTooManyRequests(text='Too many Computer View streams')
        async with live_slots:
            return await live_impl(request)

    async def live_impl(request):
        session = request.cookies.get(COOKIE, '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', session) or not app[STORE].valid(session, request.host):
            raise web.HTTPUnauthorized(text='Session missing, expired, or revoked')
        if not same_origin(request, base, origin_provider):
            raise web.HTTPForbidden(text='Origin rejected')
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(rfb_socket)), timeout=5)
        except (OSError, asyncio.TimeoutError):
            raise web.HTTPServiceUnavailable(text='Desktop service not ready')
        public = web.WebSocketResponse(protocols=('binary',), heartbeat=20, timeout=0.5, max_msg_size=1048576)
        try:
            if not app[STORE].valid(session, request.host):
                raise web.HTTPUnauthorized(text='Session expired or revoked during connection')
            await public.prepare(request)

            async def disconnect():
                try:
                    await asyncio.wait_for(public.close(code=1008, message=b'Session expired or revoked'), timeout=0.75)
                except asyncio.TimeoutError:
                    if request.transport is not None:
                        request.transport.close()

            async def input_stream():
                async for message in public:
                    if not app[STORE].valid(session, request.host):
                        await disconnect()
                        return
                    if message.type != WSMsgType.BINARY:
                        await public.close(code=1003, message=b'Binary VNC frames required')
                        return
                    writer.write(message.data)
                    await asyncio.wait_for(writer.drain(), timeout=1)

            async def output_stream():
                while data := await reader.read(65536):
                    await public.send_bytes(data)

            async def expiry_watch():
                while True:
                    await asyncio.sleep(1)
                    if not app[STORE].valid(session, request.host):
                        await disconnect()
                        return

            tasks = [asyncio.create_task(input_stream()), asyncio.create_task(output_stream()),
                     asyncio.create_task(expiry_watch())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=0.75)
                if public.prepared:
                    await asyncio.wait_for(public.close(), timeout=0.75)
            except (asyncio.TimeoutError, ConnectionError):
                writer.transport.abort()
                if request.transport is not None:
                    request.transport.close()
        return public

    app.router.add_get('/open', entry)
    app.router.add_get('/entry.js', script)
    app.router.add_get('/entry.css', style)
    app.router.add_post('/auth/redeem', redeem)
    app.router.add_get('/websockify', live)
    app.router.add_get('/{asset:.*}', asset)
    return app


def prepare_socket(path):
    path = Path(path)
    private_directory(path.parent)
    if path.is_symlink():
        raise ValueError('HTTP socket path may not be a symlink')
    if path.exists():
        info = path.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('Refusing to replace a non-socket or foreign-owned path')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise FileExistsError('Computer View is already listening on this socket')


async def serve(config: Config):
    os.umask(0o077)
    if not (config.novnc_dir / 'vnc.html').is_file():
        raise ValueError('noVNC assets missing: run computer-view assets install')
    if config.http_socket == config.rfb_socket:
        raise ValueError('HTTP and VNC sockets must be different')
    prepare_socket(config.http_socket)
    runner = web.AppRunner(create_app(config.state_dir, rfb_socket=config.rfb_socket,
                          novnc_dir=config.novnc_dir, origin_provider=config.origin), access_log=None)
    await runner.setup()
    await web.UnixSite(runner, str(config.http_socket)).start()
    config.http_socket.chmod(0o600)
    inode = config.http_socket.stat().st_ino
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        if config.http_socket.exists() and config.http_socket.stat().st_ino == inode:
            config.http_socket.unlink()
