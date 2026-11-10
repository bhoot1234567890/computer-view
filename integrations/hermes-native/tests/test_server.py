"""Gateway tests use real HTTP/WS and private Unix RFB fixtures, never Xvnc mocks."""
import asyncio
import importlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiohttp import ClientSession, DummyCookieJar, WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestServer

from computer_view.store import Store

class UnixRFB:
    """Explicit minimal RFB 3.8/None fixture, not a real desktop server."""
    def __init__(self, path):
        self.path = path
        self.received = asyncio.Queue()
        self.writers = set()
        self.tasks = set()

    async def start(self):
        self.server = await asyncio.start_unix_server(self.handle, path=str(self.path))
        return self

    async def handle(self, reader, writer):
        self.writers.add(writer)
        self.tasks.add(asyncio.current_task())
        try:
            writer.write(b'RFB 003.008\n')
            await writer.drain()
            assert await reader.readexactly(12) == b'RFB 003.008\n'
            writer.write(b'\x01\x01')
            await writer.drain()
            assert await reader.readexactly(1) == b'\x01'
            writer.write(b'\0\0\0\0')
            await writer.drain()
            assert await reader.readexactly(1) == b'\x01'
            writer.write(b'\0\x10\0\x10' + b'\0'*16 + (4).to_bytes(4, 'big') + b'Test')
            await writer.drain()
            while data := await reader.read(65536):
                await self.received.put(data)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.writers.discard(writer)
            self.tasks.discard(asyncio.current_task())

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.writers):
            writer.close()
        if self.tasks:
            await asyncio.wait_for(asyncio.gather(*list(self.tasks), return_exceptions=True), 1)


async def websocket(client, server, session, **kwargs):
    return await client.ws_connect(server.make_url('/websockify'), origin=ORIGIN,
                                   headers={'Cookie':f'{COOKIE}={session}'}, **kwargs)


async def handshake(ws):
    assert (await ws.receive_bytes()) == b'RFB 003.008\n'
    await ws.send_bytes(b'RFB 003.')
    await ws.send_bytes(b'008\n')
    assert (await ws.receive_bytes()) == b'\x01\x01'
    await ws.send_bytes(b'\x01')
    assert (await ws.receive_bytes()) == b'\0\0\0\0'
    await ws.send_bytes(b'\x01')
    assert (await ws.receive_bytes()).endswith(b'Test')


async def test_unix_rfb_watch_takeover_handback_and_revoke(tmp_path):
    server, client, settings, store = await setup(tmp_path)
    fixture = await UnixRFB(settings.rfb_socket).start()
    ws = None
    try:
        s = await redeem(client, server, store)
        assert (await request(client, server, '/websockify', session=s)).status == 403
        ws = await websocket(client, server, s)
        await handshake(ws)
        key = b'\x04\x01\0\0\0\0\0A'
        update = b'\x03\0'+b'\0'*8
        await ws.send_bytes(key + update)
        assert await asyncio.wait_for(fixture.received.get(), 1) == update
        r = await request(client, server, '/api/control', session=s, method='POST',
                          origin=ORIGIN, json={'action':'takeover'})
        assert r.status == 200
        await ws.send_bytes(key[:4])
        await ws.send_bytes(key[4:])
        assert await asyncio.wait_for(fixture.received.get(), 1) == key
        r = await request(client, server, '/api/control', session=s, method='POST',
                          origin=ORIGIN, json={'action':'handback'})
        assert r.status == 200
        assert await asyncio.wait_for(fixture.received.get(), 1) == b'\x04\0\0\0\0\0\0A'
        await ws.send_bytes(key+update)
        assert await asyncio.wait_for(fixture.received.get(), 1) == update
        # Revocation is checked before parsing/writing the very next frame.
        store.revoke('42')
        await ws.send_bytes(key+update)
        assert (await asyncio.wait_for(ws.receive(), 1.5)).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
        assert fixture.received.empty()
    finally:
        if ws is not None:
            await ws.close()
        await client.close()
        await server.close()
        await fixture.close()


async def test_handback_waits_for_all_release_drains_before_agent_admission(tmp_path):
    await release_drain_case(tmp_path, 'success')


async def test_failed_release_drain_refuses_handback_and_retains_uncertain_human(tmp_path):
    await release_drain_case(tmp_path, 'failure')


async def test_release_drain_timeout_refuses_handback(tmp_path):
    await release_drain_case(tmp_path, 'timeout')


async def release_drain_case(tmp_path, outcome):
    """Real HTTP/WS/RFB fixture with explicitly injected transport backpressure."""
    from unittest.mock import patch
    from computer_view.control import Controller, Busy
    module = importlib.import_module('computer_view.server')
    original = asyncio.open_unix_connection
    entered, settle = asyncio.Event(), asyncio.Event()
    first_settle = asyncio.Event()
    writers = []
    key = b'\x04\x01\0\0\0\0\0A'
    released = b'\x04\0\0\0\0\0\0A'
    class Writer:
        def __init__(self, writer):
            self.writer = writer; self.releasing = False
            self.index = len(writers); writers.append(self)
        def __getattr__(self, name): return getattr(self.writer, name)
        def write(self, data):
            if data == released: self.releasing = True
            self.writer.write(data)
        async def drain(self):
            if self.releasing:
                if all(w.releasing for w in writers): entered.set()
                if outcome == 'timeout': await asyncio.Event().wait()
                await (first_settle if self.index == 0 else settle).wait()
                if outcome == 'failure': raise OSError('explicit release-drain test failure')
            await self.writer.drain()
    async def connect(*args, **kwargs):
        reader, writer = await original(*args, **kwargs)
        return reader, Writer(writer)
    server, client, settings, store = await setup(tmp_path)
    fixture = await UnixRFB(settings.rfb_socket).start()
    c = Controller(tmp_path); ws = task = second_ws = None
    try:
        with patch.object(module.asyncio, 'open_unix_connection', connect):
            s = await redeem(client, server, store)
            ws = await websocket(client, server, s); await handshake(ws)
            second_ws = await websocket(client, server, s); await handshake(second_ws)
            assert (await request(client, server, '/api/control', session=s, method='POST',
                origin=ORIGIN, json={'action': 'takeover'})).status == 200
            await ws.send_bytes(key)
            assert await asyncio.wait_for(fixture.received.get(), 1) == key
            await second_ws.send_bytes(key)
            assert await asyncio.wait_for(fixture.received.get(), 1) == key
            task = asyncio.create_task(request(client, server, '/api/control', session=s,
                method='POST', origin=ORIGIN, json={'action': 'handback'}))
            await asyncio.wait_for(entered.wait(), 1)
            # Separate Controller instance reads the persisted fence, without a held flock.
            assert c.status()['holder'] == 'human'
            assert c.status()['in_flight']
            assert not c.may_input(store.session_info(s, HOST)['viewer_id'])
            assert 'error' in c.observe(lambda: pytest.fail('capture before drains settle'))
            assert 'Human' in c.execute('browser_exec', {}, lambda args: pytest.fail('input before drain'))
            with pytest.raises(Busy): c.takeover('other')
            assert not task.done()
            # Verify cross-process admission while the event loop awaits drains.
            import sys
            script = "import json,sys; from computer_view.control import Controller; c=Controller(sys.argv[1]); print(json.dumps({'state':c.status(), 'observe':c.observe(lambda:'MUST-NOT-RUN'), 'execute':c.execute('browser_exec',{},lambda args:'MUST-NOT-RUN')}))"
            proc = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(tmp_path),
                stdout=asyncio.subprocess.PIPE)
            output, _ = await asyncio.wait_for(proc.communicate(), 1)
            child = json.loads(output)
            assert child['state']['holder'] == 'human' and child['state']['in_flight']
            assert 'error' in child['observe'] and 'Human' in child['execute']
            first_settle.set()
            await asyncio.sleep(.02)
            assert not task.done(), 'One drain cannot release the other connection'
            assert c.status()['holder'] == 'human'
            settle.set()
            response = await asyncio.wait_for(task, 2)
            if outcome == 'success':
                assert response.status == 200
                assert c.status()['holder'] == 'agent'
                assert c.status()['needs_observation'] and not c.status()['uncertain']
            else:
                assert response.status >= 400
                assert c.status()['holder'] == 'human'
                assert c.status()['uncertain'] and not c.status()['in_flight']
                retry = await request(client, server, '/api/control', session=s, method='POST',
                    origin=ORIGIN, json={'action': 'handback'})
                assert retry.status >= 400
    finally:
        first_settle.set(); settle.set()
        if task and not task.done(): task.cancel(); await asyncio.gather(task, return_exceptions=True)
        if ws: await ws.close()
        if second_ws: await second_ws.close()
        await client.close(); await server.close(); await fixture.close()


ORIGIN = 'https://desktop.example'
HOST = 'desktop.example'
COOKIE = '__Host-computer_view'


def run(fn):
    def wrapped(tmp_path):
        asyncio.run(fn(tmp_path))
    wrapped.__name__ = fn.__name__
    return wrapped


test_unix_rfb_watch_takeover_handback_and_revoke = run(test_unix_rfb_watch_takeover_handback_and_revoke)
test_handback_waits_for_all_release_drains_before_agent_admission = run(test_handback_waits_for_all_release_drains_before_agent_admission)
test_failed_release_drain_refuses_handback_and_retains_uncertain_human = run(test_failed_release_drain_refuses_handback_and_retains_uncertain_human)
test_release_drain_timeout_refuses_handback = run(test_release_drain_timeout_refuses_handback)


def test_gateway_does_not_recreate_missing_control_state(tmp_path):
    from computer_view.server import create_app
    settings = SimpleNamespace(origin='https://view.example', owner_id='42',
        rfb_socket=tmp_path/'rfb.sock', novnc_dir=tmp_path/'novnc')
    with pytest.raises(FileNotFoundError):
        create_app(tmp_path, settings)


async def setup(tmp_path, controller=None):
    server_module = importlib.import_module('computer_view.server')
    settings = SimpleNamespace(origin=ORIGIN, owner_id='42', profile='test', owner_uid=0,
                               rfb_socket=tmp_path/'rfb.sock', novnc_dir=tmp_path/'novnc')
    store = Store(tmp_path/'access.sqlite3')
    if controller is None:
        from computer_view.control import Controller
        controller = Controller(tmp_path)
        controller.initialize()
    server = TestServer(server_module.create_app(tmp_path, settings, store=store, controller=controller))
    await server.start_server()
    client = ClientSession(cookie_jar=DummyCookieJar(), headers={'Host': HOST})
    return server, client, settings, store


async def request(client, server, path, *, session=None, method='GET', origin=None, **kw):
    headers = dict(kw.pop('headers', {}))
    if session:
        headers['Cookie'] = f'{COOKIE}={session}'
    if origin is not None:
        headers['Origin'] = origin
    return await client.request(method, server.make_url(path), headers=headers, **kw)


async def redeem(client, server, store, owner='42'):
    token = store.mint(owner, HOST)
    response = await request(client, server, '/auth/redeem', method='POST', origin=ORIGIN,
                             json={'token': token})
    assert response.status == 200, await response.text()
    return response.cookies[COOKIE].value


@pytest.mark.parametrize('concurrent', [False, True], ids=['repeat', 'shared-cookie-concurrent'])
@pytest.mark.parametrize('held,released', [
    (b'\x04\x01\0\0\0\0\0A', b'\x04\0\0\0\0\0\0A'),
    *[(bytes([5, mask, 0, 7, 0, 9]), b'\x05\0\0\x07\0\x09')
      for mask in (1, 2, 4, 5)],
], ids=['key', 'left-button', 'middle-button', 'right-button', 'multiple-buttons'])
def test_repeated_takeover_disconnect_drains_release_before_handback(tmp_path, concurrent, held, released):
    asyncio.run(repeated_takeover_disconnect_case(tmp_path, concurrent, held, released))


async def repeated_takeover_disconnect_case(tmp_path, concurrent, held, released):
    from unittest.mock import patch
    from computer_view.control import Controller
    module = importlib.import_module('computer_view.server')
    original = asyncio.open_unix_connection
    entered, settle, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    writes = []
    class Writer:
        def __init__(self, writer): self.writer = writer; self.releasing = False
        def __getattr__(self, name): return getattr(self.writer, name)
        def write(self, data):
            writes.append(data)
            if data == released: self.releasing = True
            self.writer.write(data)
        async def drain(self):
            if self.releasing:
                entered.set(); await settle.wait()
            await self.writer.drain()
            if self.releasing: drained.set()
    async def connect(*args, **kwargs):
        reader, writer = await original(*args, **kwargs)
        return reader, Writer(writer)
    server, client, settings, store = await setup(tmp_path)
    fixture = await UnixRFB(settings.rfb_socket).start()
    ws = closing = handback = None
    try:
        with patch.object(module.asyncio, 'open_unix_connection', connect):
            s = await redeem(client, server, store)
            ws = await websocket(client, server, s); await handshake(ws)
            async def control(action):
                return await request(client, server, '/api/control', session=s, method='POST',
                                     origin=ORIGIN, json={'action': action})
            first = await control('takeover')
            assert first.status == 200
            expected = await first.json()
            persisted = Controller(tmp_path).path.read_bytes()
            await ws.send_bytes(held)
            assert await asyncio.wait_for(fixture.received.get(), 1) == held
            repeats = (await asyncio.gather(*(control('takeover') for _ in range(4)))
                       if concurrent else [await control('takeover')])
            for response in repeats:
                assert response.status == 200
                assert await response.json() == expected
            assert Controller(tmp_path).path.read_bytes() == persisted
            # No subsequent input may refresh the connection's admitted epoch.
            closing = asyncio.create_task(ws.close())
            await asyncio.wait_for(entered.wait(), 1)
            assert writes.count(released) == 1
            assert await asyncio.wait_for(fixture.received.get(), 1) == released
            assert not drained.is_set()
            handback = asyncio.create_task(control('handback'))
            await asyncio.sleep(.03)
            assert not handback.done(), 'Handback must wait for disconnect release drain'
            state = Controller(tmp_path).status()
            assert state['holder'] == 'human' and not state['uncertain']
            settle.set()
            response = await asyncio.wait_for(handback, 2)
            assert drained.is_set() and response.status == 200
            state = await response.json()
            assert state['holder'] == 'agent' and not state['uncertain']
            assert not state['in_flight'] and state['needs_observation']
            await asyncio.wait_for(closing, 2)
            assert writes.count(released) == 1 and fixture.received.empty()
    finally:
        settle.set()
        for task in (closing, handback):
            if task and not task.done():
                task.cancel(); await asyncio.gather(task, return_exceptions=True)
        if ws: await ws.close()
        await client.close(); await server.close(); await fixture.close()


@run
async def test_disconnecting_release_is_included_in_concurrent_handback(tmp_path):
    await disconnect_failure_case(tmp_path, 'disconnect')


@run
async def test_revocation_release_failure_remains_uncertain(tmp_path):
    await disconnect_failure_case(tmp_path, 'revoke')


@run
async def test_expiry_release_failure_remains_uncertain(tmp_path):
    await disconnect_failure_case(tmp_path, 'expiry')


async def disconnect_failure_case(tmp_path, trigger):
    from unittest.mock import patch
    from computer_view.control import Controller
    module = importlib.import_module('computer_view.server')
    original = asyncio.open_unix_connection
    entered, settle = asyncio.Event(), asyncio.Event()
    key = b'\x04\x01\0\0\0\0\0A'
    released = b'\x04\0\0\0\0\0\0A'
    class Writer:
        def __init__(self, writer): self.writer = writer; self.releasing = False
        def __getattr__(self, name): return getattr(self.writer, name)
        def write(self, data):
            if data == released: self.releasing = True
            self.writer.write(data)
        async def drain(self):
            if self.releasing:
                entered.set(); await settle.wait()
                raise OSError('explicit disconnect-release failure')
            await self.writer.drain()
    async def connect(*args, **kwargs):
        reader, writer = await original(*args, **kwargs); return reader, Writer(writer)
    class LockedControl(Controller):
        def __init__(self, path):
            super().__init__(path); self.failure_attempts = 0
        def release_failed(self, viewer_id, epoch=None):
            self.failure_attempts += 1
            if self.failure_attempts == 1:
                from computer_view.control import Busy
                raise Busy('explicit cross-process lock contention fixture')
            return super().release_failed(viewer_id, epoch)
    control = LockedControl(tmp_path); control.initialize()
    server, client, settings, store = await setup(tmp_path, controller=control)
    fixture = await UnixRFB(settings.rfb_socket).start()
    ws = task = closing = None
    try:
        with patch.object(module.asyncio, 'open_unix_connection', connect):
            if trigger == 'expiry':
                now = time.time() - 1799.5
                s = store.redeem(store.mint('42', HOST, now=now), HOST, now=now)
            else:
                s = await redeem(client, server, store)
            ws = await websocket(client, server, s); await handshake(ws)
            assert (await request(client, server, '/api/control', session=s, method='POST',
                origin=ORIGIN, json={'action': 'takeover'})).status == 200
            await ws.send_bytes(key)
            assert await asyncio.wait_for(fixture.received.get(), 1) == key
            if trigger == 'disconnect': closing = asyncio.create_task(ws.close())
            elif trigger == 'revoke': store.revoke('42')
            await asyncio.wait_for(entered.wait(), 1)
            if trigger == 'disconnect':
                task = asyncio.create_task(request(client, server, '/api/control', session=s,
                    method='POST', origin=ORIGIN, json={'action': 'handback'}))
                await asyncio.sleep(.03)
                assert not task.done(), 'Handback must include the disconnecting connection drain'
            assert Controller(tmp_path).status()['holder'] == 'human'
            settle.set()
            if task:
                response = await asyncio.wait_for(task, 2)
                assert response.status >= 400
            if closing: await closing
            for _ in range(50):
                if Controller(tmp_path).status()['uncertain']: break
                await asyncio.sleep(.01)
            assert Controller(tmp_path).status()['uncertain']
            assert Controller(tmp_path).status()['holder'] == 'human'
    finally:
        settle.set()
        if task and not task.done(): task.cancel(); await asyncio.gather(task, return_exceptions=True)
        if ws: await ws.close()
        await client.close(); await server.close(); await fixture.close()


@run
async def test_preview_and_origin_cookie_auth(tmp_path):
    server, client, settings, store = await setup(tmp_path)
    try:
        token = store.mint('42', HOST)
        for path in ('/', '/open', '/open?token='+token, '/entry.js', '/static/entry.js', '/static/viewer.css'):
            response = await request(client, server, path)
            assert response.status == 200
            assert not response.cookies
            assert 'default-src' in response.headers['Content-Security-Policy']
        assert (await request(client, server, '/desktop')).status == 401
        for origin in (None, 'https://evil.example', ORIGIN+'/'):
            r = await request(client, server, '/auth/redeem', method='POST', origin=origin,
                              json={'token':token})
            assert r.status == 403
        r = await request(client, server, '/auth/redeem', method='POST', origin=ORIGIN,
                          headers={'Host':'evil.example','X-Forwarded-Host':HOST}, json={'token':token})
        assert r.status == 403
        r = await request(client, server, '/auth/redeem', method='POST', origin=ORIGIN, json={'token':token})
        assert r.status == 200
        cookie = r.cookies[COOKIE]
        assert cookie['secure'] and cookie['httponly'] and cookie['samesite']=='Strict' and cookie['path']=='/'
        assert not cookie['domain']
        assert (await request(client, server, '/desktop', session=cookie.value)).status == 200
        assert (await request(client, server, '/auth/redeem', method='POST', origin=ORIGIN,
                              json={'token':token})).status == 401
        assert (await request(client, server, '/api/status', session=cookie.value,
                              headers={'Host':'evil.example'})).status == 403
    finally:
        await client.close()
        await server.close()


@run
async def test_status_control_owner_revoke_and_static_safety(tmp_path):
    server, client, settings, store = await setup(tmp_path)
    try:
        session = await redeem(client, server, store)
        other = await redeem(client, server, store)
        response = await request(client, server, '/api/status', session=session)
        assert response.status == 200
        payload = await response.json()
        assert payload['holder'] == 'agent' and 'expires_at' in payload
        assert 'viewer_id' not in json.dumps(payload)
        for body in ({}, {'action':'unknown'}, {'action':'takeover','extra':1}, []):
            assert (await request(client, server, '/api/control', session=session,
                    method='POST', origin=ORIGIN, json=body)).status == 400
        assert (await request(client, server, '/api/control', session=session,
                method='POST', json={'action':'takeover'})).status == 403
        async def control(s, action):
            return await request(client, server, '/api/control', session=s, method='POST',
                                 origin=ORIGIN, json={'action':action})
        assert (await control(session, 'takeover')).status == 200
        assert (await control(other, 'takeover')).status == 409
        assert (await control(other, 'handback')).status == 403
        assert (await control(session, 'handback')).status == 200
        root = settings.novnc_dir
        (root/'core').mkdir(parents=True)
        (root/'core'/'rfb.js').write_text('export default 1')
        (root/'core'/'secret.js').symlink_to(tmp_path/'access.sqlite3')
        (root/'core'/'linked').symlink_to(tmp_path, target_is_directory=True)
        for path in ('/novnc/core/rfb.js','/viewer.js'):
            assert (await request(client, server, path)).status == 401
            assert (await request(client, server, path, session=session)).status == 200
        for suffix in ('core/secret.js','core/linked/access.sqlite3','core/.hidden',
                       'utils/tool.js','core/%2e%2e/access.sqlite3','core/%5csecret.js'):
            assert (await request(client, server, '/novnc/'+suffix, session=session)).status == 404
        foreign = store.redeem(store.mint('99', HOST), HOST)
        assert (await request(client, server, '/api/status', session=foreign)).status == 401
        expired = store.redeem(store.mint('42', HOST, now=1), HOST, now=2)
        assert (await request(client, server, '/api/status', session=expired)).status == 401
        store.revoke('42')
        assert (await request(client, server, '/api/status', session=session)).status == 401
    finally:
        await client.close()
        await server.close()


@run
async def test_active_expiry_disconnect_releases_and_retains_lease(tmp_path):
    server, client, settings, store = await setup(tmp_path)
    fixture = await UnixRFB(settings.rfb_socket).start()
    ws = None
    try:
        now = time.time()
        token = store.mint('42', HOST, now=now-1799.4)
        s = store.redeem(token, HOST, now=now-1799.4)
        ws = await websocket(client, server, s)
        await handshake(ws)
        assert (await request(client, server, '/api/control', session=s, method='POST',
                origin=ORIGIN, json={'action':'takeover'})).status == 200
        key = b'\x04\x01\0\0\0\0\0A'
        await ws.send_bytes(key)
        assert await asyncio.wait_for(fixture.received.get(), 1) == key
        started = time.monotonic()
        assert (await asyncio.wait_for(ws.receive(), 1.5)).type == WSMsgType.CLOSE
        assert time.monotonic()-started < 1
        assert await asyncio.wait_for(fixture.received.get(), 1) == b'\x04\0\0\0\0\0\0A'
        fresh = await redeem(client, server, store)
        status = await (await request(client, server, '/api/status', session=fresh)).json()
        assert status['holder'] == 'human' and status['mine'] is False
    finally:
        if ws:
            await ws.close()
        await client.close()
        await server.close()
        await fixture.close()


@run
async def test_redeem_global_limit_and_invalid_bodies(tmp_path):
    server, client, settings, store = await setup(tmp_path)
    try:
        for body in ({}, [], {'token':4}, {'token':'a','other':2}):
            assert (await request(client, server, '/auth/redeem', method='POST', origin=ORIGIN,
                    json=body)).status == 400
        assert (await request(client, server, '/auth/redeem', method='POST', origin=ORIGIN,
                data='{')).status == 400
        for _ in range(25):
            assert (await request(client, server, '/auth/redeem', method='POST', origin=ORIGIN,
                    json={'token':'invalid'})).status == 401
        assert (await request(client, server, '/auth/redeem', method='POST', origin=ORIGIN,
                json={'token':store.mint('42', HOST)})).status == 429
    finally:
        await client.close()
        await server.close()


@run
async def test_websocket_limits_and_malformed_rfb(tmp_path):
    server, client, settings, store = await setup(tmp_path)
    fixture = await UnixRFB(settings.rfb_socket).start()
    sockets = []
    try:
        s = await redeem(client, server, store)
        for _ in range(4):
            sockets.append(await websocket(client, server, s))
        with pytest.raises(WSServerHandshakeError) as exc:
            await websocket(client, server, s)
        assert exc.value.status == 429
        for ws in sockets:
            await ws.close()
        sockets.clear()
        await asyncio.sleep(0.1)
        for payload in (b'\x07', b'\x06\0\0\0\x7f\xff\xff\xff', b'\0'*(1024*1024+1), 'not binary'):
            ws = await websocket(client, server, s)
            sockets.append(ws)
            await handshake(ws)
            if isinstance(payload, str):
                await ws.send_str(payload)
            else:
                await ws.send_bytes(payload)
            assert (await asyncio.wait_for(ws.receive(), 1.5)).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
            await ws.close()
            assert fixture.received.empty()
        ws = await websocket(client, server, s)
        sockets.append(ws)
        assert (await ws.receive_bytes()) == b'RFB 003.008\n'
        await ws.send_bytes(b'RFB 003.003\n')
        assert (await asyncio.wait_for(ws.receive(), 1.5)).type == WSMsgType.CLOSE
    finally:
        for ws in sockets:
            await ws.close()
        await client.close()
        await server.close()
        await fixture.close()


@run
async def test_profile_stores_and_invalid_upstream_are_isolated(tmp_path):
    server, client, settings, store = await setup(tmp_path/'one')
    second, second_client, _, _ = await setup(tmp_path/'two')
    received = []
    async def invalid(reader, writer):
        writer.write(b'RFB 003.003\n')
        await writer.drain()
        received.append(await reader.read())
        writer.close()
        await writer.wait_closed()
    unix = await asyncio.start_unix_server(invalid, path=str(settings.rfb_socket))
    try:
        s = await redeem(client, server, store)
        assert (await request(second_client, second, '/api/status', session=s)).status == 401
        assert (await request(second_client, second, '/auth/redeem', method='POST', origin=ORIGIN,
                json={'token':store.mint('42', HOST)})).status == 401
        with pytest.raises(WSServerHandshakeError) as exc:
            await websocket(client, server, s)
        assert exc.value.status == 502
        await asyncio.sleep(0.02)
        assert received == [b'']
    finally:
        unix.close()
        await unix.wait_closed()
        await client.close()
        await second_client.close()
        await server.close()
        await second.close()


@run
async def test_control_rechecks_session_after_slow_body(tmp_path):
    server, client, settings, store = await setup(tmp_path)
    try:
        s = await redeem(client, server, store)
        async def body():
            yield b'{"action":'
            await asyncio.sleep(0.05)
            store.revoke('42')
            yield b'"takeover"}'
        response = await request(client, server, '/api/control', session=s, method='POST', origin=ORIGIN,
                                 data=body(), headers={'Content-Type':'application/json'})
        assert response.status == 401
        fresh = await redeem(client, server, store)
        status = await (await request(client, server, '/api/status', session=fresh)).json()
        assert status['holder'] == 'agent'
    finally:
        await client.close()
        await server.close()


@run
async def test_controller_failure_is_closed_and_security_headers_remain(tmp_path):
    class Unavailable:
        def status(self, viewer_id):
            raise OSError('private state inaccessible')
    server, client, settings, store = await setup(tmp_path, controller=Unavailable())
    try:
        s = await redeem(client, server, store)
        r = await request(client, server, '/api/status', session=s)
        assert r.status == 503
        assert 'default-src' in r.headers['Content-Security-Policy']
        assert 'private state' not in await r.text()
    finally:
        await client.close()
        await server.close()


@run
async def test_crossprocess_frame_lock_watch_and_disconnect_release(tmp_path):
    import sys
    server, client, settings, store = await setup(tmp_path)
    fixture = await UnixRFB(settings.rfb_socket).start()
    ws = proc = None
    try:
        s = await redeem(client, server, store)
        ws = await websocket(client, server, s)
        await handshake(ws)
        assert (await request(client, server, '/api/control', session=s, method='POST',
                origin=ORIGIN, json={'action':'takeover'})).status == 200
        script = ('import fcntl,sys; f=open(sys.argv[1],"r+"); '
                  'fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read(1)')
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(tmp_path/'control.lock'),
                      stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        assert await proc.stdout.readline() == b'locked\n'
        key = b'\x04\x01\0\0\0\0\0A'
        update = b'\x03\0'+b'\0'*8
        await ws.send_bytes(key+update)
        assert await asyncio.wait_for(fixture.received.get(), 1) == update
        proc.stdin.write(b'x')
        await proc.stdin.drain()
        await proc.wait()
        await ws.send_bytes(key)
        assert await asyncio.wait_for(fixture.received.get(), 1) == key
        await ws.close()
        assert await asyncio.wait_for(fixture.received.get(), 1) == b'\x04\0\0\0\0\0\0A'
        status = await (await request(client, server, '/api/status', session=s)).json()
        assert status['holder'] == 'human' and status['mine']
    finally:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        if ws:
            await ws.close()
        await client.close()
        await server.close()
        await fixture.close()


@pytest.mark.parametrize('trigger', ['disconnect', 'expiry', 'revoke'])
@pytest.mark.parametrize('held', [b'\x04\x01\0\0\0\0\0A', b'\x05\x01\0\x07\0\x09'])
def test_contended_cleanup_cannot_claim_settled_handback(tmp_path, trigger, held):
    asyncio.run(contended_cleanup_case(tmp_path, trigger, held))


async def contended_cleanup_case(tmp_path, trigger, held):
    import sys
    from computer_view.control import Controller
    server, client, settings, store = await setup(tmp_path)
    fixture = await UnixRFB(settings.rfb_socket).start()
    ws = proc = closing = handback = None
    try:
        if trigger == 'expiry':
            now = time.time() - 1799.5
            s = store.redeem(store.mint('42', HOST, now=now), HOST, now=now)
        else:
            s = await redeem(client, server, store)
        ws = await websocket(client, server, s); await handshake(ws)
        assert (await request(client, server, '/api/control', session=s, method='POST',
            origin=ORIGIN, json={'action': 'takeover'})).status == 200
        await ws.send_bytes(held)
        assert await asyncio.wait_for(fixture.received.get(), 1) == held
        viewer_id = store.session_info(s, HOST)['viewer_id']
        epoch = Controller(tmp_path).status()['epoch']
        script = ('import fcntl,sys; f=open(sys.argv[1],"r+"); '
                  'fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read(1)')
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', script,
            str(tmp_path/'control.lock'), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        assert await proc.stdout.readline() == b'locked\n'
        if trigger == 'disconnect': closing = asyncio.create_task(ws.close())
        elif trigger == 'revoke': store.revoke('42')
        # A real separate-process flock must not block the event loop. The old
        # path drains an unchanged, healthy writer and silently drops the input.
        await asyncio.sleep(.65 if trigger == 'expiry' else .3)
        response = await asyncio.wait_for(request(client, server, '/api/status',
            session=s if trigger == 'disconnect' else await redeem(client, server, store)), 1)
        assert response.status == 200
        if trigger == 'disconnect':
            handback = asyncio.create_task(request(client, server, '/api/control', session=s,
                method='POST', origin=ORIGIN, json={'action': 'handback'}))
            await asyncio.sleep(.02)
        proc.stdin.write(b'x'); await proc.stdin.drain(); await proc.wait()
        if closing: await asyncio.wait_for(closing, 2)
        for _ in range(100):
            if Controller(tmp_path).status()['uncertain']: break
            await asyncio.sleep(.01)
        state = Controller(tmp_path).status()
        assert state['uncertain'], 'Skipped synthetic release must durably fence the retained lease'
        assert state['holder'] == 'human' and not state['in_flight']
        assert state['epoch'] > epoch
        assert not Controller(tmp_path).may_input(viewer_id)
        if handback: assert (await asyncio.wait_for(handback, 2)).status == 409
        assert fixture.received.empty(), 'Admission-denied cleanup must not write outside the control lock'
    finally:
        if proc and proc.returncode is None:
            proc.kill(); await proc.wait()
        if handback and not handback.done():
            handback.cancel(); await asyncio.gather(handback, return_exceptions=True)
        if closing and not closing.done():
            closing.cancel(); await asyncio.gather(closing, return_exceptions=True)
        if ws: await ws.close()
        await client.close(); await server.close(); await fixture.close()


@run
async def test_handshake_timeout_and_frame_flood_close(tmp_path):
    module = importlib.import_module('computer_view.server')
    previous = module.HANDSHAKE_TIMEOUT
    module.HANDSHAKE_TIMEOUT = 0.1
    server, client, settings, store = await setup(tmp_path)
    fixture = await UnixRFB(settings.rfb_socket).start()
    sockets = []
    try:
        s = await redeem(client, server, store)
        ws = await websocket(client, server, s)
        sockets.append(ws)
        await ws.receive_bytes()
        assert (await asyncio.wait_for(ws.receive(), 1)).type == WSMsgType.CLOSE
        module.HANDSHAKE_TIMEOUT = previous
        ws = await websocket(client, server, s)
        sockets.append(ws)
        await handshake(ws)
        for _ in range(250):
            await ws.send_bytes(b'')
        assert (await asyncio.wait_for(ws.receive(), 1)).type == WSMsgType.CLOSE
        assert fixture.received.empty()
    finally:
        module.HANDSHAKE_TIMEOUT = previous
        for ws in sockets:
            await ws.close()
        await client.close()
        await server.close()
        await fixture.close()
