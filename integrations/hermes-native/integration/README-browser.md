# Real Chromium/noVNC browser E2E

`browser_e2e.py` exercises the unmodified candidate gateway against a fresh real
TigerVNC X server and real X11 `xmessage` applications. It uses the integrity-pinned
noVNC source archive. It does not simulate backend responses, RFB frames, or desktop
input effects. This is browser/gateway integration evidence, **not** evidence of a
Telegram delivery flow or native Hermes middleware registration.

## Recorded result

- `../artifacts/browser-results.json`: 12 passed checks, source SHA-256 fingerprints,
  exact invocation, browser version, transport details, artifact digests.
- `../artifacts/browser-desktop-watch.png`: connected real desktop, watch only.
- `../artifacts/browser-desktop-control.png`: real desktop, human control enabled.
- `../artifacts/browser-desktop-handback.png`: explicit handback, observation required.
- `../artifacts/browser-mobile-watch.png`: 390px viewport, document/body width 390px.
- `../artifacts/browser-revoked.png`: active viewer disconnected after revocation.
- `../artifacts/browser-working.webm`: actual Playwright Chromium UI recording,
  1440×1000, VP9, 25 fps, 6.12 seconds. Fully decoded with ffmpeg, exit code 0,
  153 frames. No reconstructed/synthetic footage.

The input assertion is an actual process effect: watch-mode Enter (including a
second real noVNC connection deliberately requesting input despite watch mode)
leaves `xmessage` alive. After a trusted takeover click, the viewer's Enter button
activates `xmessage`'s default Accept button and the real process exits with code
42. Handback is checked through persisted Controller state and a refused
Controller input-admission call. This does not claim a running native agent acted.
Revocation uses the real Store owner-local revoke operation. Expiry sets a short
persisted deadline **only in the disposable test database**, then waits for real
wall-clock expiration and an actual viewer disconnect.

## Requirements and normal invocation

Run as a non-root account on a machine where Chromium's sandbox works. Requires
`aiohttp`, `playwright`, Chromium, Xvnc, xmessage, openssl, certutil, and the pinned
noVNC archive. Video additionally requires Playwright's cached ffmpeg. No insecure
browser flags, TLS exceptions, system certificate installation, or public listeners
are used.

```sh
python integration/browser_e2e.py \
  --archive /path/to/novnc.tar.gz \
  --chromium /path/to/chrome \
  --xvnc /path/to/Xvnc \
  --certutil /path/to/certutil \
  --video
```

The script refuses root Chromium execution. It starts Xvnc on an unused display,
with RFB over a private mode-0600 Unix socket and X11 TCP disabled. The candidate
app uses a test-only HTTPS listener on `127.0.0.1` and an exact
`https://localhost:PORT` origin. A generated temporary CA signs the localhost SAN
certificate; only throwaway HOME NSS databases trust it. Production ingress is
unchanged. All child processes and temporary CA/state/assets are cleaned up.

## Exact successful invocation in this environment

The Docker tool environment has `NoNewPrivs=1`, seccomp filtering, and denied
unprivileged user namespaces. A real non-root Chromium launch there failed with
`No usable sandbox`; **no sandbox bypass was attempted**. The alternative was a
new sandboxed Chromium process on the host, not the existing default browser.

A disposable directory was prepared under
`/home/aryantest/.hermes/cache/scratch/computer-view-browser-jeqazp84` containing:

- A copy of this integration script and only the candidate `computer_view/` source.
- The existing pinned noVNC archive copied from Docker.
- Xvnc, certutil, and missing `libjpeg.so.62` copied from Docker into this temporary
  directory (no host package installs or existing binary changes).
- A dedicated temporary Python venv with Playwright/aiohttp installed into that
  venv only. Existing cached Chromium and ffmpeg were reused.

Exact successful command, executed as host uid 1000:

```sh
P=/home/aryantest/.hermes/cache/scratch/computer-view-browser-jeqazp84
PYTHONPATH='' \
LD_LIBRARY_PATH="$P" \
PLAYWRIGHT_BROWSERS_PATH=/home/aryantest/.cache/ms-playwright \
"$P/py/bin/python" "$P/repo/integration/browser_e2e.py" \
  --archive "$P/novnc.tar.gz" \
  --chromium /home/aryantest/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome \
  --xvnc "$P/Xtigervnc" \
  --certutil "$P/certutil" \
  --video
```

Exit code: **0**. Results/artifacts were copied back byte-for-byte into this repo's
`artifacts/` directory, then their SHA-256 digests and candidate source fingerprints
were checked against the JSON. The source fingerprints matched Docker's candidate.

Video validation command (host, existing ffmpeg):

```sh
/home/aryantest/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg \
  -i "$P/repo/artifacts/browser-working.webm" -f null -
```

## Diagnostics and implementation notes

- The final run logged one non-blocking browser resource 404. No JavaScript page
  errors occurred; required API/module requests succeeded.
- Playwright string predicates can trigger the strict CSP's eval prohibition;
  function predicates (`() => ...`) preserve CSP without unsafe-eval/bypassCSP.
- TigerVNC's `rfbunixmode` argument is **decimal**: `384` produces `0600`.
  Passing `600` produces mode `1130` and prevents the gateway connecting.
- Video's ffmpeg lookup needed an explicit existing `PLAYWRIGHT_BROWSERS_PATH`;
  this did not require an install or modification of the live desktop cache.
- Failed runs report errors honestly and cannot label stale screenshots as newly
  generated artifacts. Only screenshots/video produced during the current run
  enter that run's artifact manifest.
