#!/usr/bin/env python3
"""Real isolated Xvnc + candidate HTTPS gateway + sandboxed Chromium E2E.

Requires non-root execution, aiohttp/playwright, Xvnc, xmessage, openssl,
certutil, and a Chromium executable. No live desktop or public ingress is used.
The HTTPS TCP loopback listener is test-only; production uses Unix ingress.
"""
import argparse
from contextlib import suppress
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import traceback

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from computer_view.assets import install_novnc, COMMIT, SHA256
from computer_view.config import Settings
from computer_view.control import Controller
from computer_view.server import create_app
from computer_view.store import Store


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


async def exercise(args, report):
    from aiohttp import web
    from playwright.async_api import async_playwright
    if os.getuid() == 0:
        raise RuntimeError('Run as a non-root user with a working Chromium sandbox; no bypass is allowed')
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    def passed(name, **evidence):
        report['checks'].append({'name': name, 'status': 'passed', **evidence})
    def require(value, message):
        if not value:
            raise AssertionError(message)
    with tempfile.TemporaryDirectory(prefix='browser-e2e-') as directory:
        tmp = Path(directory)
        home = tmp/'home'
        home.mkdir()
        assets = install_novnc(tmp/'novnc', Path(args.archive).read_bytes())
        report['novnc'] = {'commit': COMMIT, 'archive_sha256': SHA256}
        for n in range(80, 180):
            if not Path(f'/tmp/.X11-unix/X{n}').exists() and not Path(f'/tmp/.X{n}-lock').exists():
                display = f':{n}'
                break
        else:
            raise RuntimeError('No unused X display')
        rfb = tmp/'rfb.sock'
        # Local Unix transport only: neither X11 nor RFB gets a TCP listener.
        xvnc_log = (tmp/'xvnc.log').open('w')
        xvnc = subprocess.Popen([args.xvnc, display, '-geometry', '1280x800', '-depth', '24',
            '-SecurityTypes', 'None', '-rfbport', '-1', '-rfbunixpath', str(rfb),
            '-rfbunixmode', '384', '-nolisten', 'tcp', '-noreset', '-AlwaysShared'],
            stdout=xvnc_log, stderr=xvnc_log)
        xmessage = None
        runner = None
        browser = None
        context = None
        try:
            for _ in range(100):
                if rfb.exists():
                    break
                require(xvnc.poll() is None, 'Xvnc exited: '+(tmp/'xvnc.log').read_text())
                await asyncio.sleep(.1)
            require(rfb.exists(), 'Xvnc socket not ready')
            xmessage = subprocess.Popen(['xmessage', '-display', display,
                '-geometry', '900x500+20+20', '-fn', 'fixed', '-bg', '#e6f0ff', '-fg', '#102030',
                '-buttons', 'Accept:42', '-default', 'Accept',
                'REAL ISOLATED XVNC DESKTOP\n\nComputer View end-to-end verification\n\nWatch mode must not activate Accept.\nHuman control + Enter must close this real X11 app with exit code 42.'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await asyncio.sleep(.5)
            require(xmessage.poll() is None, 'xmessage did not start')
            passed('isolated_real_desktop', display=display, xvnc_pid=xvnc.pid, application_pid=xmessage.pid,
                   rfb_transport='private Unix socket; no RFB/X11 TCP listeners')
            # Legitimately trust a generated CA in a throwaway HOME, not a TLS exception.
            run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=Computer View disposable test CA', '-keyout', str(tmp/'ca.key'), '-out', str(tmp/'ca.crt'),
                '-addext', 'basicConstraints=critical,CA:TRUE')
            run('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost',
                '-keyout', str(tmp/'server.key'), '-out', str(tmp/'server.csr'))
            (tmp/'extensions').write_text('subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
            run('openssl', 'x509', '-req', '-in', str(tmp/'server.csr'), '-CA', str(tmp/'ca.crt'),
                '-CAkey', str(tmp/'ca.key'), '-CAcreateserial', '-days', '1', '-extfile', str(tmp/'extensions'), '-out', str(tmp/'server.crt'))
            for relative in ('.pki/nssdb', '.local/share/pki/nssdb'):
                db = home/relative
                db.mkdir(parents=True)
                run(args.certutil, '-N', '-d', 'sql:'+str(db), '--empty-password')
                run(args.certutil, '-A', '-d', 'sql:'+str(db), '-n', 'Disposable Computer View CA', '-t', 'C,,', '-i', str(tmp/'ca.crt'))
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(tmp/'server.crt', tmp/'server.key')
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            host = f'localhost:{port}'
            origin = 'https://'+host
            data = tmp/'data'
            data.mkdir(mode=0o700)
            controller = Controller(data)
            controller.initialize()
            store = Store(data/'access.sqlite3')
            settings = Settings('42', 'isolated-e2e', os.getuid(), origin, rfb, assets)
            runner = web.AppRunner(create_app(data, settings, store=store, controller=controller))
            await runner.setup()
            await web.SockSite(runner, listener, ssl_context=tls).start()
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=args.chromium,
                    headless=True, chromium_sandbox=True, env={**os.environ, 'HOME': str(home),
                    'XDG_CONFIG_HOME': str(home/'.config'), 'XDG_DATA_HOME': str(home/'.local/share')},
                    args=['--use-angle=swiftshader'])
                report['browser_version'] = browser.version
                report['security'] = {'chromium_sandbox': True, 'tls_verification': True,
                    'trust': 'temporary NSS CA', 'listener': '127.0.0.1', 'uid': os.getuid()}
                options = {'viewport': {'width':1440,'height':1000}}
                if args.video:
                    options.update(record_video_dir=str(tmp/'recording'), record_video_size={'width':1440,'height':1000})
                context = await browser.new_context(**options)
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                report['browser_console'] = []
                report['http_failures'] = []
                page.on('console', lambda message: report['browser_console'].append({'type':message.type,'text':message.text}))
                page.on('response', lambda response: report['http_failures'].append({'url':response.url,'status':response.status}) if response.status>=400 else None)
                requests = []
                page.on('request', lambda request: requests.append({'url':request.url, 'method':request.method}))
                frames = {'received':0, 'bytes':0, 'closed':False}
                def websocket(ws):
                    def received(payload):
                        frames['received'] += 1
                        frames['bytes'] += len(payload)
                    ws.on('framereceived', received)
                    ws.on('close', lambda: frames.update(closed=True))
                page.on('websocket', websocket)
                token = store.mint('42', host)
                await page.goto(origin+'/open#token='+token)
                await page.wait_for_function('() => location.hash === "" && !document.querySelector("#open-session").disabled')
                require(not any('/auth/redeem' in r['url'] for r in requests), 'Preview redeemed a token')
                require(not any(token in r['url'] for r in requests), 'Token leaked into request URL')
                require(not await context.cookies(), 'Preview created a session cookie')
                passed('preview_token_scrub', url=page.url, redemption_requests=0, token_in_request_url=False)
                await page.locator('#open-session').evaluate('(el) => el.click()')
                await page.wait_for_timeout(200)
                require(not any('/auth/redeem' in r['url'] for r in requests), 'Synthetic click redeemed token')
                passed('untrusted_click_cannot_redeem')
                await page.locator('#open-session').click()
                await page.wait_for_url('**/desktop')
                await page.wait_for_function('() => document.querySelector("#connection").textContent === "Connected"')
                await page.wait_for_function('() => document.querySelector("canvas")?.width === 1280')
                await page.wait_for_timeout(600)
                cookies = await context.cookies()
                require(len(cookies)==1 and cookies[0]['secure'] and cookies[0]['httpOnly'] and cookies[0]['sameSite']=='Strict', 'Incorrect session cookie flags')
                passed('trusted_click_redeems', cookie_secure=True, cookie_httponly=True, cookie_samesite='Strict')
                pixels = await page.locator('canvas').evaluate('c => {const d=c.getContext("2d").getImageData(0,0,c.width,c.height).data; let n=0; for(let i=0;i<d.length;i+=4) if(d[i]||d[i+1]||d[i+2]) n++; return {width:c.width,height:c.height,nonblack:n}}')
                require(frames['bytes']>1000 and pixels['nonblack']>10000, 'No actual framebuffer pixels')
                passed('real_rfb_frame_connection', websocket_frames=frames['received'], websocket_bytes=frames['bytes'], canvas=pixels)
                await page.screenshot(path=str(out/'browser-desktop-watch.png'), full_page=True)
                require(await page.locator('#key-enter').is_disabled(), 'Watch keyboard is enabled')
                await page.locator('canvas').click(position={'x':80,'y':80})
                await page.keyboard.press('Enter')
                # A second actual noVNC transport deliberately requests input while watching.
                # This bypasses only UI viewOnly, to exercise the SERVER input fence.
                await page.evaluate('''async () => {const {default:RFB}=await import('/novnc/core/rfb.js');
                    const div=document.createElement('div'); div.hidden=true; document.body.append(div);
                    await new Promise((resolve,reject)=>{const r=new RFB(div,location.origin.replace('https:','wss:')+'/websockify');
                    r.viewOnly=false; r.addEventListener('connect',()=>{r.sendKey(0xff0d); setTimeout(()=>{r.disconnect();div.remove();resolve();},400)});
                    r.addEventListener('securityfailure',()=>reject(new Error('RFB security failure')));});}''')
                await page.wait_for_timeout(400)
                require(xmessage.poll() is None, 'Watch input reached desktop')
                passed('watch_cannot_input', ui_keyboard_disabled=True, forged_rfb_enter_ignored=True, application_still_running=True)
                await page.locator('#takeover').click()
                await page.wait_for_function('() => document.querySelector("#mode").textContent === "You have control"')
                require(controller.status()['holder']=='human', 'Takeover not persisted')
                await page.screenshot(path=str(out/'browser-desktop-control.png'), full_page=True)
                await page.locator('#key-enter').click()
                for _ in range(30):
                    if xmessage.poll() is not None:
                        break
                    await asyncio.sleep(.1)
                require(xmessage.poll()==42, f'Human Enter did not activate real xmessage: {xmessage.poll()}')
                passed('human_control_real_desktop_input', application_exit_code=xmessage.returncode, action='trusted UI Enter -> RFB -> real xmessage Accept')
                await page.locator('#handback').click()
                await page.wait_for_function('() => document.querySelector("#mode").textContent === "Watch only"')
                state = controller.status()
                require(state['holder']=='agent' and state['needs_observation'], 'Handback did not require observation')
                called = []
                fence = json.loads(controller.execute('browser_exec', {}, lambda args: called.append(args)))
                require(not called and 'Fresh observation required' in fence.get('error',''), 'Input admission was not fenced after handback')
                passed('handback_needs_observation', holder=state['holder'], needs_observation=True, input_admission_refused=True)
                await page.screenshot(path=str(out/'browser-desktop-handback.png'), full_page=True)
                # Restore a visible real application for the mobile evidence.
                xmessage = subprocess.Popen(['xmessage','-display',display,'-geometry','900x500+20+20',
                    '-bg','#e6f0ff','-fg','#102030','-buttons','Done:42',
                    'REAL ISOLATED XVNC DESKTOP\n\nMobile watch viewport\n\nActual pixels delivered over the candidate gateway.'], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                await page.set_viewport_size({'width':390,'height':844})
                await page.wait_for_timeout(700)
                overflow = await page.evaluate('({viewport:innerWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth})')
                require(overflow['document']<=390 and overflow['body']<=390, 'Mobile viewport overflow')
                passed('mobile_viewport_no_overflow', **overflow)
                await page.screenshot(path=str(out/'browser-mobile-watch.png'), full_page=True)
                # Revocation is a real owner-local Store operation, not a mocked HTTP response.
                store.revoke('42')
                await page.wait_for_function('() => document.querySelector("#connection").textContent === "Disconnected"')
                require(frames['closed'], 'Revoked WebSocket remained open')
                require(await page.locator('#takeover').is_disabled(), 'Revoked viewer can take over')
                passed('revoke_active_viewer_disconnect', websocket_closed=True, takeover_disabled=True)
                await page.screenshot(path=str(out/'browser-revoked.png'), full_page=True)
                # Actual fixture expiration uses a persisted deadline and real wall-clock passage.
                await context.clear_cookies()
                token = store.mint('42', host)
                await page.goto(origin+'/open#token='+token)
                await page.locator('#open-session').click()
                await page.wait_for_url('**/desktop')
                await page.wait_for_function('() => document.querySelector("#connection").textContent === "Connected"')
                with store.connect() as db:
                    db.execute('UPDATE sessions SET expires=?', (time.time()+1,))
                await page.wait_for_function('() => document.querySelector("#connection").textContent === "Disconnected"')
                passed('expired_active_viewer_disconnect', deadline_source='isolated SQLite fixture; real wall-clock expiration')
                require(not errors, 'Browser JS errors: '+repr(errors))
                passed('no_browser_javascript_errors', errors=errors)
                video = page.video
                await context.close()
                context = None
                if args.video:
                    shutil.copy2(await video.path(), out/'browser-working.webm')
                    report['video'] = 'browser-working.webm'
                await browser.close()
                browser = None
            report['status'] = 'passed'
        finally:
            if context:
                with suppress(Exception):
                    await context.close()
            if browser:
                with suppress(Exception):
                    await browser.close()
            if runner:
                await runner.cleanup()
            for process in (xmessage, xvnc):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            xvnc_log.close()
            report['desktop_log_tail'] = (tmp/'xvnc.log').read_text()[-3000:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--chromium', required=True)
    parser.add_argument('--xvnc', default=shutil.which('Xvnc'))
    parser.add_argument('--certutil', default=shutil.which('certutil'))
    parser.add_argument('--video', action='store_true', help='Record actual Chromium UI with Playwright bundled ffmpeg')
    parser.add_argument('--output', default=str(REPO/'artifacts'))
    args = parser.parse_args()
    run_started = time.time()
    report = {'status':'blocked', 'checks':[], 'video':'not_done',
        'command': [sys.executable, *sys.argv],
        'source_sha256': {str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((REPO/'computer_view').rglob('*'))
            if p.is_file() and p.suffix in ('.py','.js','.html','.css')},
        'scope':'isolated real Xvnc + real pinned noVNC + candidate HTTPS gateway; not Telegram/native-agent integration',
        'started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    try:
        asyncio.run(exercise(args, report))
    except Exception as exc:
        report['error'] = str(exc)
        report['traceback'] = traceback.format_exc()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    report['artifacts'] = [{'path':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(out.glob('browser-*')) if p.suffix in ('.png', '.webm') and p.stat().st_mtime >= run_started]
    report['passed_checks'] = len([x for x in report['checks'] if x['status']=='passed'])
    (out/'browser-results.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status']=='passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
