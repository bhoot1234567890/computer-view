"""Static security contract plus executable viewer state-machine checks.
Node's DOM/RFB doubles isolate UI logic; they are not a live desktop test.
"""
from pathlib import Path
from html.parser import HTMLParser
import subprocess
import shutil
import pytest

STATIC = Path(__file__).resolve().parents[1] / 'computer_view' / 'static'

class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.feed(source)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

def test_accessible_csp_safe_responsive_shell():
    path = STATIC / 'viewer.html'
    assert path.exists(), 'viewer shell is not implemented'
    source = path.read_text()
    page = Page(source)
    assert not any(t == 'style' or any(k.startswith('on') or k == 'style' for k in a) for t,a in page.tags)
    assert all(a.get('src', '').startswith('/static/') for t,a in page.tags if t == 'script')
    ids = {a['id'] for _,a in page.tags if 'id' in a}
    assert {'desktop', 'takeover', 'handback', 'disconnect', 'connection', 'mode', 'lease', 'notice', 'keyboard', 'keyboard-input', 'events'} <= ids
    assert any(a.get('name') == 'viewport' for _,a in page.tags)
    assert 'same-user' in source and 'shell' in source and 'fenced' in source
    css = (STATIC / 'viewer.css').read_text()
    assert '@media' in css and ':focus-visible' in css and 'safe-area-inset' in css
    for name in ('viewer.html', 'entry.html'):
        shell=Page((STATIC / name).read_text())
        assert any(t=='link' and a.get('rel')=='icon' and a.get('href','').startswith('data:image/svg+xml,')
                   for t,a in shell.tags), 'Explicit local favicon avoids a default unauthenticated /favicon.ico request'

def run_js(program, file='viewer.js'):
    path = STATIC / file
    assert path.exists(), f'{file} is not implemented'
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for executable viewer contract')
    source = path.read_text().replace("import RFB from '/novnc/core/rfb.js';", 'const RFB = globalThis.FakeRFB;')
    setup = "location.hash='#token=secret-token';" if file == 'entry.js' else ''
    result = subprocess.run([node, '--input-type=module', '-'], input=HARNESS + '\n' + setup + '\n' + source + '\n' + program, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr

HARNESS = r'''
import assert from 'node:assert/strict';
const elements = new Map();
class Element {
 constructor(){this.disabled=false;this.hidden=false;this.textContent='';this.value='';this.checked=true;this.listeners={};this.children=[];this.dataset={};}
 addEventListener(k,fn){this.listeners[k]=fn;}
 setAttribute(){} focus(){this.focused=true;} replaceChildren(...items){this.children=items;} append(...items){this.children.push(...items);}
 async fire(k, extra={}){return this.listeners[k]?.({isTrusted:true,target:this,preventDefault(){},...extra});}
}
globalThis.document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement(){return new Element();},addEventListener(){},hidden:false};
globalThis.window=globalThis;
globalThis.location={protocol:'https:',host:'view.example',origin:'https://view.example',hash:'',pathname:'/desktop',search:'',assign(p){this.destination=p;}};
globalThis.history={replaceState(){location.hash='';}};
globalThis.addEventListener=()=>{};
const timers=[];
globalThis.setInterval=(fn)=>{timers.push(fn);return timers.length;};
globalThis.clearInterval=()=>{};
let state={holder:'agent',mine:false,in_flight:false,uncertain:false,expires_at:Date.now()/1000+300,events:[]};
let pendingResolve=null;let fail=false;let requests=[];
globalThis.fetch=async(url,options={})=>{requests.push({url,options,viewOnly:globalThis.rfbInstance?.viewOnly});if(fail)throw Error('network');if(options.method==='POST'&&pendingResolve===true)return new Promise(resolve=>{pendingResolve=()=>resolve({ok:true,json:async()=>state});});return {ok:true,json:async()=>state};};
globalThis.FakeRFB=class {
 constructor(target,url){this.url=url;this.listeners={};globalThis.rfbInstance=this;this.viewOnly=false;}
 addEventListener(k,fn){this.listeners[k]=fn;} disconnect(){this.closed=true;this.listeners.disconnect?.({detail:{clean:true}});} focus(){this.focused=true;} sendKey(...args){this.lastKey=args;}
};
const flush=async()=>{for(let i=0;i<15;i++)await Promise.resolve();};
const el=id=>document.getElementById(id);
'''

def test_viewer_state_machine_and_fail_closed():
    run_js(r'''
await flush();
assert.equal(rfbInstance.url,'wss://view.example/websockify');
assert.equal(rfbInstance.viewOnly,true);
rfbInstance.listeners.connect({detail:{}});await flush();
assert.equal(el('takeover').disabled,false);
state={...state,holder:'human',mine:true};pendingResolve=true;
const taking=el('takeover').fire('click');await flush();
assert.equal(el('takeover').disabled,true);assert.equal(el('handback').disabled,true);assert.equal(rfbInstance.viewOnly,true);
pendingResolve();await taking;await flush();assert.equal(rfbInstance.viewOnly,false);
assert.equal(el('handback').disabled,false);
state={...state,holder:'agent',mine:false};pendingResolve=null;
await el('handback').fire('click');await flush();
assert.equal(requests.findLast(r=>r.options.method==='POST').viewOnly,true);
assert.equal(rfbInstance.viewOnly,true);
state={...state,holder:'human',mine:false};for(const fn of timers)await fn();await flush();
assert.equal(rfbInstance.viewOnly,true);
state={...state,mine:true,expires_at:Date.now()/1000-1};for(const fn of timers)await fn();await flush();
assert.equal(rfbInstance.viewOnly,true);
state={...state,expires_at:Date.now()/1000+300};for(const fn of timers)await fn();await flush();
assert.equal(rfbInstance.viewOnly,false);
fail=true;for(const fn of timers)await fn();await flush();assert.equal(rfbInstance.viewOnly,true);
const before=requests.filter(r=>r.options.method==='POST').length;
await el('disconnect').fire('click');await flush();assert.equal(rfbInstance.closed,true);
assert.equal(requests.filter(r=>r.options.method==='POST').length,before);
assert.ok(requests.every(r=>!r.url.includes('?')&&r.options.credentials==='same-origin'));
''')

@pytest.mark.parametrize('scenario', ['failed-handback', 'other-tab-handback', 'missing-flags', 'agent-unsettled'])
def test_unsettled_control_is_honest_and_never_enables_input(scenario):
    import json
    run_js('const scenario = ' + json.dumps(scenario) + ';' + r'''
await flush();rfbInstance.listeners.connect({detail:{}});await flush();
state={...state,holder:'human',mine:true};await el('takeover').fire('click');await flush();
assert.equal(rfbInstance.viewOnly,false);
if(scenario==='failed-handback'){
 fail=true;await el('handback').fire('click');await flush();fail=false;
 state={...state,uncertain:true,reason:'Input release failed; owner recovery required'};
}else if(scenario==='other-tab-handback'){
 state={...state,in_flight:true,reason:'Handback pending; input releases are draining'};
}else if(scenario==='missing-flags'){
 delete state.in_flight;delete state.uncertain;delete state.reason;
}else{
 state={...state,holder:'agent',mine:false,in_flight:true,reason:'A desktop action is in flight'};
}
for(const fn of timers)await fn();await flush();
assert.equal(rfbInstance.viewOnly,true);
for(const id of ['keyboard','keyboard-input','key-tab','key-escape','key-enter','key-backspace','takeover','handback']){
 assert.equal(el(id).disabled,true,id+' must be disabled');
}
assert.match(el('mode').textContent,/unsettled|recover|pending|unavailable/i);
assert.doesNotMatch(el('control-description').textContent,/input is enabled|take control to interact/i);
assert.match(el('control-description').textContent,/unsettled|recover|draining|in flight|unavailable/i);
if(state.reason)assert.ok(el('control-description').textContent.includes(state.reason));
const before=requests.length;
await el('takeover').fire('click');await el('handback').fire('click');
await el('key-enter').fire('click');el('keyboard-input').value='x';await el('keyboard-input').fire('input');
assert.equal(requests.length,before);assert.equal(rfbInstance.lastKey,undefined);
state={...state,holder:'human',mine:true,in_flight:false,uncertain:false,reason:'Human took control'};
for(const fn of timers)await fn();await flush();
assert.equal(rfbInstance.viewOnly,false);assert.equal(el('handback').disabled,false);
''')


def test_credentials_never_prompt_and_controls_reject_untrusted_clicks():
    run_js(r'''
await flush();rfbInstance.listeners.connect({detail:{}});await flush();
const before=requests.length;await el('takeover').fire('click',{isTrusted:false});assert.equal(requests.length,before);
rfbInstance.listeners.credentialsrequired({detail:{types:['password']}});await flush();
assert.equal(rfbInstance.closed,true);assert.equal(rfbInstance.viewOnly,true);
assert.match(el('notice').textContent,/credential/i);
''')
    source = (STATIC / 'viewer.js').read_text()
    assert '.innerHTML' not in source and 'prompt(' not in source and 'sendCredentials' not in source
    assert "import RFB from '/novnc/core/rfb.js';" in source

def test_entry_redemption_requires_explicit_trusted_click_and_scrubs_fragment():
    run_js(r'''
await flush();assert.equal(requests.length,0);assert.equal(location.hash,'');
await el('open-session').fire('click',{isTrusted:false});assert.equal(requests.length,0);
await el('open-session').fire('click');await flush();
assert.equal(requests.length,1);assert.equal(requests[0].url,'/auth/redeem');
assert.equal(JSON.parse(requests[0].options.body).token,'secret-token');assert.equal(location.destination,'/desktop');
''', file='entry.js')

def test_loader_reports_missing_local_novnc_without_enabling_input():
    run_js(r'''
await new Promise(resolve=>setTimeout(resolve,40));
assert.equal(el('connection').textContent,'Unavailable');
assert.equal(el('takeover').disabled,true);
assert.match(el('notice').textContent,/noVNC/);
''', file='viewer-loader.js')


def test_mobile_composition_is_preserved_until_committed():
    run_js(r'''
await flush();rfbInstance.listeners.connect({detail:{}});await flush();
state={...state,holder:'human',mine:true};await el('takeover').fire('click');await flush();
el('keyboard-input').value='漢';
await el('keyboard-input').fire('input',{isComposing:true});
assert.equal(el('keyboard-input').value,'漢');
assert.equal(rfbInstance.lastKey,undefined);
await el('keyboard-input').fire('input',{isComposing:false});
assert.equal(el('keyboard-input').value,'');
assert.equal(rfbInstance.lastKey[0],0x01000000|'漢'.codePointAt(0));
''')
    assert 'until handback or lease expiry' not in (STATIC / 'viewer.js').read_text()


def test_entry_has_no_autoforward_or_inline_code():
    page = Page((STATIC / 'entry.html').read_text())
    assert any(a.get('id') == 'open-session' for _,a in page.tags)
    assert not any(t == 'style' or a.get('http-equiv','').lower() == 'refresh' or any(k.startswith('on') for k in a) for t,a in page.tags)
    source = (STATIC / 'entry.js').read_text()
    assert 'isTrusted' in source and 'replaceState' in source and 'localStorage' not in source and 'console.' not in source
