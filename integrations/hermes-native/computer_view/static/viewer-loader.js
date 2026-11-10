// A failed ES module dependency must not leave the page stuck at “Connecting”.
import('/static/viewer.js').catch(() => {
    document.getElementById('connection').textContent = 'Unavailable';
    document.getElementById('screen-message').textContent = 'Desktop viewer unavailable';
    document.getElementById('notice').textContent = 'Unable to load the local noVNC viewer. Ask the owner to check the installation. No desktop connection was opened.';
    document.getElementById('lease').textContent = 'Not connected';
    for (const id of ['takeover', 'handback', 'keyboard', 'keyboard-input', 'key-tab', 'key-escape', 'key-enter', 'key-backspace', 'disconnect']) {
        document.getElementById(id).disabled = true;
    }
});
