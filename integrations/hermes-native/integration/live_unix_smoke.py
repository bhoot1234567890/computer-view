"""Read-only real-desktop smoke test over the candidate's private HTTP/WS gateway.

This does not test public HTTPS, Telegram delivery, or arbitrary agent dispatch.
It uses isolated fixture-owner capability state and never sends desktop input.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import ClientSession, DummyCookieJar, UnixConnector, WSMsgType, web
from computer_view.config import Settings
from computer_view.control import Controller
from computer_view.server import COOKIE, create_app
from computer_view.store import Store


async def run(rfb_socket, evidence):
    origin = 'https://cv-integration.invalid'
    host = 'cv-integration.invalid'
    with tempfile.TemporaryDirectory(prefix='computer-view-smoke-') as directory:
        root = Path(directory)
        root.chmod(0o700)
        settings = Settings.from_dict(dict(owner_id='42', profile='default', owner_uid=os.getuid(),
            origin=origin, rfb_socket=str(rfb_socket), novnc_dir=str(root/'novnc')))
        controller = Controller(root); controller.initialize()
        store = Store(root/'access.sqlite3')
        runner = web.AppRunner(create_app(root, settings), access_log=None)
        await runner.setup()
        http = root/'http.sock'
        await web.UnixSite(runner, str(http)).start()
        http.chmod(0o600)
        result = {'transport':'private Unix HTTP and WebSocket, not public HTTPS',
                  'fixture_owner':True, 'desktop_input_sent':False}
        try:
            async with ClientSession(connector=UnixConnector(path=str(http)),
                    cookie_jar=DummyCookieJar(), headers={'Host':host}) as client:
                url = 'http://' + host
                denied = await client.get(url+'/desktop')
                assert denied.status == 401
                token = store.mint('42',host)
                preview = await client.get(url+'/open')
                assert preview.status == 200 and not preview.cookies
                redeemed = await client.post(url+'/auth/redeem',json={'token':token},headers={'Origin':origin})
                assert redeemed.status == 200
                cookie = redeemed.cookies[COOKIE]
                assert cookie['secure'] and cookie['httponly'] and cookie['samesite']=='Strict'
                secret = cookie.value
                headers = {'Cookie':COOKIE+'='+secret}
                replay = await client.post(url+'/auth/redeem',json={'token':token},headers={'Origin':origin})
                assert replay.status == 401
                desktop = await client.get(url+'/desktop',headers=headers)
                assert desktop.status == 200
                assert 'Computer View' in await desktop.text()
                ws = await client.ws_connect(url+'/websockify',origin=origin,headers=headers)
                buffered = bytearray()
                async def read(count):
                    while len(buffered)<count:
                        message = await ws.receive(timeout=10)
                        assert message.type == WSMsgType.BINARY, f'Unexpected websocket state {message.type}'
                        buffered.extend(message.data)
                    data = bytes(buffered[:count]); del buffered[:count]
                    return data
                assert await read(12) == b'RFB 003.008\n'
                await ws.send_bytes(b'RFB 003.008\n')
                assert await read(2) == b'\x01\x01'
                await ws.send_bytes(b'\x01')
                assert await read(4) == b'\0'*4
                await ws.send_bytes(b'\x01')
                init = await read(24)
                width,height = struct.unpack('!HH',init[:4])
                length = struct.unpack('!I',init[20:24])[0]
                name = (await read(length)).decode()
                assert name == 'hermes:default', 'This smoke test is restricted to the active dedicated default desktop'
                pixel = struct.pack('!BBBBHHHBBBxxx',32,24,0,1,255,255,255,16,8,0)
                await ws.send_bytes(b'\x00\0\0\0'+pixel)
                await ws.send_bytes(struct.pack('!BBHi',2,0,1,0))
                await ws.send_bytes(struct.pack('!BBHHHH',3,0,0,0,min(width,64),min(height,64)))
                update = await read(4)
                assert update[0] == 0
                rectangles = struct.unpack('!H',update[2:])[0]
                count = 0
                for _ in range(rectangles):
                    _,_,w,h,encoding = struct.unpack('!HHHHi',await read(12))
                    assert encoding == 0
                    count += len(await read(w*h*4))
                assert count>0
                state = await (await client.get(url+'/api/status',headers=headers)).json()
                assert state['holder']=='agent' and not state['mine']
                take = await client.post(url+'/api/control',json={'action':'takeover'},headers={**headers,'Origin':origin})
                assert take.status == 200
                assert (await take.json())['mine']
                calls=[]
                assert 'Human' in controller.execute('browser_exec',{},lambda args:calls.append(args))
                assert not calls
                back = await client.post(url+'/api/control',json={'action':'handback'},headers={**headers,'Origin':origin})
                assert back.status == 200 and (await back.json())['needs_observation']
                assert 'observation' in controller.execute('computer_use',{},lambda args:calls.append(args))
                assert not calls
                started = time.monotonic(); store.revoke('42')
                message = await ws.receive(timeout=3)
                assert message.type in (WSMsgType.CLOSE,WSMsgType.CLOSED)
                elapsed = time.monotonic()-started
                await ws.close()
                final = await client.get(url+'/desktop',headers=headers)
                assert final.status == 401
                result.update(desktop=name,width=width,height=height,framebuffer_bytes=count,
                    preview_safe=True,anonymous_denied=True,replay_denied=True,
                    secure_cookie=True,takeover_confirmed=True,controller_callback_fenced=True,
                    handback_observation_required=True,revocation_seconds=round(elapsed,4),
                    revoked_assets_denied=True)
        finally:
            await runner.cleanup()
        evidence.parent.mkdir(parents=True,exist_ok=True)
        evidence.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rfb-socket',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(run(args.rfb_socket,args.evidence))
