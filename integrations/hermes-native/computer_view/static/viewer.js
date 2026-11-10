import RFB from '/novnc/core/rfb.js';

const $ = id => document.getElementById(id);
const inputIds = ['keyboard', 'keyboard-input', 'key-tab', 'key-escape', 'key-enter', 'key-backspace'];
let rfb = null;
let connected = false;
let closed = false;
let pending = false;
let polling = false;
let generation = 0;
let status = null;
let freshAt = 0;
let terminalMessage = '';

function settledControl() {
    return status?.in_flight === false && status?.uncertain === false;
}

function ownsControl() {
    return connected && !closed && !pending && status?.connected !== false &&
        settledControl() &&
        status?.holder === 'human' && status?.mine === true &&
        Number.isFinite(status.expires_at) && status.expires_at * 1000 > Date.now() &&
        Date.now() - freshAt < 6000;
}

function render() {
    const owns = ownsControl();
    if (rfb) rfb.viewOnly = !owns;
    const valid = status && freshAt && Date.now() - freshAt < 6000 && status.connected !== false &&
        Number.isFinite(status.expires_at) && status.expires_at * 1000 > Date.now();
    const unsettled = valid && !settledControl();
    $('takeover').disabled = !connected || closed || pending || !valid || unsettled || status.holder !== 'agent';
    $('handback').disabled = !connected || closed || pending || !valid || unsettled || status.holder !== 'human' || status.mine !== true;
    $('disconnect').disabled = closed;
    for (const id of inputIds) $(id).disabled = !owns;
    if (!owns) {
        $('keyboard-input').value = '';
        $('keyboard-panel').hidden = true;
        $('keyboard').setAttribute('aria-expanded', 'false');
    }
    $('mode').textContent = pending ? 'Updating control…' : unsettled ? 'Control unsettled' : owns ? 'You have control' : 'Watch only';
    $('control-description').textContent = unsettled ? 'Input disabled; control is unsettled. ' +
        (typeof status.reason === 'string' && status.reason ? status.reason.slice(0, 400) :
            'Settled control status unavailable. Wait for confirmation or ask the owner to recover control.') :
        owns ? 'Your input is enabled. Hand back when finished.' :
        status?.holder === 'human' && status.mine !== true ? 'Another viewer holds control. You can keep watching.' :
        closed ? 'This viewer is disconnected.' : 'Input is disabled. Take control to interact.';
    if (status && Number.isFinite(status.expires_at)) {
        const seconds = Math.max(0, Math.ceil(status.expires_at - Date.now() / 1000));
        const minutes = Math.floor(seconds / 60);
        $('lease').textContent = seconds ? `Session expires in ${minutes}:${String(seconds % 60).padStart(2, '0')}` : 'Session expired · input disabled';
    } else $('lease').textContent = 'Session status unavailable';
}

function applyStatus(value) {
    if (!value || !['agent', 'human'].includes(value.holder) || typeof value.mine !== 'boolean' || !Number.isFinite(value.expires_at)) {
        throw new Error('Invalid session status');
    }
    status = value;
    freshAt = Date.now();
    const items = [];
    for (const event of (Array.isArray(value.events) ? value.events.slice(-5).reverse() : [])) {
        const li = document.createElement('li');
        const at = document.createElement('time');
        const date = typeof event.at === 'number' ? new Date(event.at * 1000) : new Date(event.at);
        at.textContent = Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        const text = document.createElement('span');
        text.textContent = typeof event.event === 'string' ? event.event.slice(0, 300) : 'Session updated';
        li.append(at, text);
        items.push(li);
    }
    if (!items.length) {
        const li = document.createElement('li');
        li.textContent = 'No session events yet.';
        items.push(li);
    }
    $('events').replaceChildren(...items);
    $('notice').textContent = terminalMessage || (typeof value.reason === 'string' ? value.reason.slice(0, 400) : '') ||
        (ownsControl() ? 'You hold control. Supported Hermes input remains fenced until explicit handback.' : 'Watching only. Control changes are confirmed by the server.');
    render();
}

async function request(url, options = {}) {
    const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', redirect: 'error', ...options,
        signal: AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error('Session request failed');
    return response.json();
}

async function poll() {
    if (polling || pending || closed) return;
    polling = true;
    const version = generation;
    try {
        const value = await request('/api/status');
        if (version === generation && !closed && !pending) applyStatus(value);
    } catch {
        if (version === generation && !closed) {
            freshAt = 0;
            status = null;
            $('notice').textContent = 'Session status unavailable. Input disabled; retrying automatically.';
            render();
        }
    } finally { polling = false; }
}

async function changeControl(action, event) {
    if (!event.isTrusted || pending || closed || !connected || $(action === 'takeover' ? 'takeover' : 'handback').disabled) return;
    // Close the input gate before any request, including handback failures.
    pending = true;
    generation += 1;
    if (rfb) rfb.viewOnly = true;
    render();
    let value = null;
    try {
        value = await request('/api/control', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})});
    } catch {
        status = null;
        freshAt = 0;
        $('notice').textContent = 'Control change could not be confirmed. Input disabled. Check session status before trying again.';
    } finally {
        pending = false;
        if (!closed && value) {
            try { applyStatus(value); }
            catch { status = null; freshAt = 0; $('notice').textContent = 'Invalid session response. Input disabled.'; }
        }
        render();
    }
}

function disconnect(message) {
    terminalMessage = message;
    closed = true;
    connected = false;
    generation += 1;
    if (rfb) { rfb.viewOnly = true; rfb.disconnect(); }
    $('connection').textContent = 'Disconnected';
    $('screen-placeholder').hidden = false;
    $('screen-message').textContent = 'Desktop disconnected';
    $('notice').textContent = message;
    render();
}

$('takeover').addEventListener('click', event => changeControl('takeover', event));
$('handback').addEventListener('click', event => changeControl('handback', event));
$('disconnect').addEventListener('click', event => {
    if (event.isTrusted) disconnect('This viewer is disconnected. Control has not been handed back. Reload to reconnect; use Hermes to revoke all access.');
});
$('fit').addEventListener('change', () => { if (rfb) rfb.scaleViewport = $('fit').checked; });
$('keyboard').setAttribute('aria-controls', 'keyboard-panel');
$('keyboard').setAttribute('aria-expanded', 'false');
$('keyboard').addEventListener('click', event => {
    if (!event.isTrusted || !ownsControl()) return;
    $('keyboard-panel').hidden = !$('keyboard-panel').hidden;
    $('keyboard').setAttribute('aria-expanded', String(!$('keyboard-panel').hidden));
    if (!$('keyboard-panel').hidden) $('keyboard-input').focus();
});
for (const [id, key] of [['key-tab', 0xff09], ['key-escape', 0xff1b], ['key-enter', 0xff0d], ['key-backspace', 0xff08]]) {
    $(id).addEventListener('click', event => {
        if (event.isTrusted && ownsControl()) { rfb.sendKey(key); rfb.focus(); }
    });
}
// Mobile input is transient; no values are persisted, logged, or sent to our API.
$('keyboard-input').addEventListener('input', event => {
    if (event.isComposing) return;
    const text = $('keyboard-input').value;
    $('keyboard-input').value = '';
    if (!event.isTrusted || !ownsControl()) return;
    for (const character of text) {
        const code = character.codePointAt(0);
        rfb.sendKey(code <= 0xff ? code : 0x01000000 | code);
    }
});
$('keyboard-input').addEventListener('keydown', event => {
    const keys = {Enter: 0xff0d, Backspace: 0xff08, Tab: 0xff09, Escape: 0xff1b};
    if (event.isTrusted && ownsControl() && keys[event.key]) { event.preventDefault(); rfb.sendKey(keys[event.key]); }
});

try {
    const websocket = new URL('/websockify', location.origin);
    websocket.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    rfb = new RFB($('desktop'), websocket.href);
    rfb.viewOnly = true;
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfb.showDotCursor = true;
    rfb.addEventListener('connect', () => {
        if (closed) return;
        connected = true;
        $('connection').textContent = 'Connected';
        $('screen-placeholder').hidden = true;
        render();
        poll();
    });
    rfb.addEventListener('disconnect', () => {
        closed = true;
        connected = false;
        generation += 1;
        rfb.viewOnly = true;
        $('connection').textContent = 'Disconnected';
        $('screen-placeholder').hidden = false;
        $('screen-message').textContent = 'Desktop disconnected';
        $('notice').textContent = terminalMessage || 'Connection closed. No control handback was requested. Reload to reconnect, or use Hermes to manage this session.';
        render();
    });
    rfb.addEventListener('credentialsrequired', () => disconnect('Desktop credentials were requested. This viewer never handles credentials. Ask the owner to configure authenticated access.'));
    rfb.addEventListener('securityfailure', () => disconnect('Desktop security negotiation failed. Ask the owner to check the Computer View setup.'));
} catch {
    disconnect('Unable to initialize the desktop viewer. Ask the owner to check local noVNC installation and the websocket endpoint.');
}
render();
poll();
setInterval(poll, 2000);
setInterval(render, 1000);
addEventListener('pagehide', () => { if (!closed) disconnect('Viewer closed. No control handback requested.'); });
