// Scrub the secret before any network request or interaction. Never persist it.
let fragment = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
let token = fragment.startsWith('token=') ? new URLSearchParams(fragment).get('token') : fragment;
fragment = '';
const openButton = document.getElementById('open-session');
const notice = document.getElementById('entry-notice');
let pending = false;
if (token && token.length <= 4096) {
    openButton.disabled = false;
    notice.textContent = 'Ready to open. No session has been redeemed yet.';
} else {
    token = '';
    notice.textContent = 'This link has no valid session token. Ask Hermes for a new Computer View link.';
}
openButton.addEventListener('click', async event => {
    if (!event.isTrusted || pending || !token) return;
    pending = true;
    openButton.disabled = true;
    notice.textContent = 'Opening your session…';
    try {
        const response = await fetch('/auth/redeem', {
            method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token}),
            signal: AbortSignal.timeout(10000),
        });
        if (!response.ok) throw new Error('Redemption failed');
        token = '';
        location.assign('/desktop');
    } catch {
        // Do not retry automatically: the server may already have consumed it.
        token = '';
        notice.textContent = 'This link could not be opened. It may have expired or already been used. Ask Hermes for a new link.';
    }
});
