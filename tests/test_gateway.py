import asyncio
from computer_view import gateway
import tempfile
import unittest
from pathlib import Path
from aiohttp.test_utils import TestClient, TestServer
from computer_view.store import Store


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = gateway
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        (self.base / 'novnc').mkdir()
        (self.base / 'novnc/vnc.html').write_text('<html>noVNC</html>')
        self.store = Store(self.base / 'test.sqlite3')
        self.client = TestClient(TestServer(self.module.create_app(self.base, store=self.store)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.host = self.client.make_url('/').raw_authority
        self.origin = 'https://' + self.host
        (self.base / 'public-url.txt').write_text(self.origin + '\n')

    async def test_preview_does_not_redeem_and_no_password_prompt(self):
        token = self.store.mint('owner', self.host)
        preview = await self.client.get('/open')
        self.assertEqual(preview.status, 200)
        self.assertIn('Computer View', await preview.text())
        denied = await self.client.get('/vnc.html')
        self.assertEqual(denied.status, 401)
        self.assertNotIn('WWW-Authenticate', denied.headers)
        redeemed = await self.client.post('/auth/redeem', json={'token': token}, headers={'Origin': self.origin})
        self.assertEqual(redeemed.status, 200)
        cookie = redeemed.cookies['__Host-computer_view']
        self.assertTrue(cookie['secure'])
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Strict')
        self.assertEqual(cookie['path'], '/')
        asset = await self.client.get('/vnc.html', cookies={'__Host-computer_view': cookie.value})
        self.assertEqual(asset.status, 200)
        replay = await self.client.post('/auth/redeem', json={'token': token}, headers={'Origin': self.origin})
        self.assertEqual(replay.status, 401)


    async def test_websocket_requires_authentication_and_same_origin(self):
        from aiohttp import WSServerHandshakeError
        with self.assertRaises(WSServerHandshakeError) as denied:
            await self.client.ws_connect('/websockify', headers={'Origin': self.origin})
        self.assertEqual(denied.exception.status, 401)
        token = self.store.mint('owner', self.host)
        session = self.store.redeem(token, self.host)
        with self.assertRaises(WSServerHandshakeError) as rejected:
            await self.client.ws_connect('/websockify', headers={
                'Origin': 'https://evil.example', 'Cookie': '__Host-computer_view=' + session})
        self.assertEqual(rejected.exception.status, 403)

    async def start_rfb(self):
        self.upstream_closed = asyncio.Event()
        async def echo(reader, writer):
            try:
                writer.write(b'RFB 003.008\n')
                await writer.drain()
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                writer.close()
                self.upstream_closed.set()
        server = await asyncio.start_unix_server(echo, path=str(self.base / 'rfb.sock'))
        async def stop():
            server.close()
            await server.wait_closed()
        self.addAsyncCleanup(stop)

    async def test_live_websocket_closes_after_revoke(self):
        from aiohttp import WSMsgType
        await self.start_rfb()
        token = self.store.mint('owner', self.host)
        session = self.store.redeem(token, self.host)
        ws = await self.client.ws_connect('/websockify', protocols=('binary',), autoclose=False, headers={
            'Origin': self.origin, 'Cookie': '__Host-computer_view=' + session})
        self.assertEqual((await ws.receive(timeout=3)).data, b'RFB 003.008\n')
        await ws.send_bytes(b'probe')
        self.assertEqual((await ws.receive(timeout=3)).data, b'probe')
        self.store.revoke('owner')
        await ws.send_bytes(b'must-not-be-forwarded-after-revoke')
        message = await ws.receive(timeout=4)
        self.assertIn(message.type, (WSMsgType.CLOSE, WSMsgType.CLOSED))
        await asyncio.wait_for(self.upstream_closed.wait(), timeout=3)
        await ws.close()

    async def test_redeem_rejects_cross_origin_malformed_and_expired_tokens(self):
        import time
        token = self.store.mint('owner', self.host)
        for origin in ('https://evil.example', ''):
            response = await self.client.post('/auth/redeem', json={'token': token}, headers={'Origin': origin})
            self.assertEqual(response.status, 403)
        for data in ({'token': '💥'}, {'token': None}, [], {'token': 'a' * 43}):
            response = await self.client.post('/auth/redeem', json=data, headers={'Origin': self.origin})
            self.assertEqual(response.status, 401)
        expired = self.store.mint('owner', self.host, now=time.time() - 300)
        response = await self.client.post('/auth/redeem', json={'token': expired}, headers={'Origin': self.origin})
        self.assertEqual(response.status, 401)
        response = await self.client.post('/auth/redeem', json={'token': token}, headers={'Origin': self.origin})
        self.assertEqual(response.status, 200)

    async def test_assets_do_not_expose_private_files_or_accept_basic_auth(self):
        import base64
        denied = await self.client.get('/vnc.html', headers={'Authorization': 'Basic ' + base64.b64encode(b'hermes:old-password').decode()})
        self.assertEqual(denied.status, 401)
        token = self.store.mint('owner', self.host)
        session = self.store.redeem(token, self.host)
        cookie = {'__Host-computer_view': session}
        (self.base / 'credentials.json').write_text('private test fixture')
        (self.base / 'novnc/.git').mkdir()
        (self.base / 'novnc/.git/config').write_text('private repo metadata')
        for path in ('/.git/config', '/credentials.json', '/access.sqlite3', '/app/../../credentials.json'):
            result = await self.client.get(path, cookies=cookie)
            self.assertEqual(result.status, 404)
        good = await self.client.get('/vnc.html', cookies=cookie)
        self.assertEqual(good.headers['Cache-Control'], 'no-store')
        self.assertEqual(good.headers['Referrer-Policy'], 'no-referrer')
        self.store.revoke('owner')
        revoked = await self.client.get('/vnc.html', cookies=cookie)
        self.assertEqual(revoked.status, 401)


    async def test_spoofed_host_origin_pair_cannot_override_configured_origin(self):
        token = self.store.mint('owner', 'evil.example')
        response = await self.client.post('/auth/redeem', json={'token': token}, headers={
            'Host': 'evil.example', 'Origin': 'https://evil.example',
            'X-Forwarded-Host': self.host, 'X-Forwarded-Proto': 'https'})
        self.assertEqual(response.status, 403)
        token = self.store.mint('owner', self.host)
        response = await self.client.post('/auth/redeem', json={'token': token}, headers=[
            ('Origin', self.origin), ('Origin', self.origin)])
        self.assertEqual(response.status, 403)


    async def test_redemption_traffic_is_bounded(self):
        statuses = []
        for _ in range(31):
            response = await self.client.post('/auth/redeem', json={'token': 'a' * 43}, headers={'Origin': self.origin})
            statuses.append(response.status)
        self.assertEqual(statuses[0], 401)
        self.assertEqual(statuses[-1], 429)


    async def test_live_connection_count_is_bounded(self):
        from aiohttp import WSServerHandshakeError
        await self.start_rfb()
        token = self.store.mint('owner', self.host)
        session = self.store.redeem(token, self.host)
        headers = {'Origin': self.origin, 'Cookie': '__Host-computer_view=' + session}
        sockets = []
        try:
            for _ in range(4):
                sockets.append(await self.client.ws_connect('/websockify', headers=headers))
            try:
                extra = await self.client.ws_connect('/websockify', headers=headers)
            except WSServerHandshakeError as error:
                self.assertEqual(error.status, 429)
            else:
                await extra.close()
                self.fail('More than four simultaneous Computer View streams were accepted')
        finally:
            for ws in sockets:
                await ws.close()


if __name__ == '__main__':
    unittest.main()
