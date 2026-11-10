# Computer View

Watch your Hermes desktop from your phone. Take control when the agent needs you. Hand it back when you are done.

Computer View combines native Hermes tools, a private Telegram entry button, a mobile noVNC viewer, and a server-enforced control fence. It shares a dedicated Hermes virtual desktop—not your personal desktop by default.

## The workflow

1. Ask Hermes for Computer View in your private Telegram chat.
2. Tap the single-use button and confirm opening the session.
3. Watch without sending input.
4. Choose **Take control**. An active supported agent action must finish before takeover is granted.
5. Make your decision on the desktop.
6. Choose **Hand back**, then tell Hermes to continue in Telegram. The plugin requires a fresh observation before supported screen tools can act again.
7. Ask Hermes to revoke Computer View when finished. Revocation ends active viewing sessions and unused links.

Closing or losing the viewer does not silently return control to the agent. If your session expires while you hold control, use explicit owner-local recovery after checking the desktop is safe to resume.

## Supported configuration

- Linux with a dedicated, same-user Hermes Xvnc desktop exposing a private Unix RFB socket.
- Hermes 0.22.6 or newer, with this plugin enabled in the relevant in-process tool workers. Earlier releases and host-isolated handoff are not claimed to work.
- The owner's private Telegram DM. Groups and other messaging platforms are not supported in this release.
- A separately managed HTTPS reverse proxy or tunnel forwarding only to Computer View's private Unix HTTP socket. Raw RFB and browser CDP stay private.
- RFB 3.8, security None on the owner-only upstream Unix socket, shared connections. Public authentication is provided by Computer View's expiring capabilities and session cookies.

This is a tool-level coordination mechanism, not an OS sandbox. Shell scripts, direct CDP/XTest clients, third-party dispatch bypasses, native viewers using a different lease, and other same-user processes are outside its fence. See SECURITY.md before deployment.

## Source installation and local service

From a source checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[test]'
.venv/bin/computer-view --help
```

Native plugin installation/discovery requires the repository's root `__init__.py` and `plugin.yaml`; the Python wheel is the standalone service package. The repository must be placed in the active profile's `plugins/computer-view` directory using a source-only Git checkout, or installed from its published Git repository through `hermes plugins install`. Do not copy a working directory containing a virtualenv, owner settings, access databases, or bearer links into a public repository.

Provision and validate before using the agent's screen tools. This plugin deliberately denies protected operations when settings are missing or isolation is not explicitly selected. In the intended active profile, an operator who accepts in-process plugins can select the supported mode with `hermes config set plugins.isolation in_process`; this affects other plugins in that profile too. Do not use this handoff implementation on a deployment that requires host isolation. The plugin never changes isolation itself.

After native installation and enablement, the same CLI is available as `hermes computer-view -- --help`. For leading CLI options, keep the separator: `hermes computer-view -- --data-dir /absolute/private/path status`. Without `--data-dir`, the native CLI uses the active profile's official plugin data directory.

Before enabling it, validate the source:

```sh
hermes plugins doctor /absolute/path/to/computer-view --ci
hermes plugins validate /absolute/path/to/computer-view --install-deps --json
```

The publication-specific install command and catalog entry are release gates, not invented repository URLs. See RELEASE_CHECKLIST.md.

Provision explicit private settings. Replace the example values with your own Telegram ID, dedicated desktop socket, and HTTPS origin:

```sh
DATA="$HOME/.local/share/computer-view"
.venv/bin/computer-view --data-dir "$DATA" setup \
  --owner-id YOUR_PRIVATE_TELEGRAM_ID \
  --profile default \
  --origin https://YOUR_VIEWER_HOST \
  --rfb-socket /absolute/path/to/your/dedicated/rfb.sock \
  --novnc-dir "$DATA/novnc"
.venv/bin/computer-view --data-dir "$DATA" assets
.venv/bin/computer-view --data-dir "$DATA" doctor
.venv/bin/computer-view --data-dir "$DATA" serve
```

For native Hermes use, the service and plugin MUST share the same profile-scoped `plugin_data_dir("computer-view")`. The standalone DATA example above does not automatically connect to the plugin's storage. Running the CLI inside the Hermes runtime without `--data-dir` uses the official plugin storage helper; when using a separate virtualenv, supply the exact native plugin data path explicitly.

Setup creates owner-only settings and state; it does not start a service, install system packages, open a TCP port, or create a tunnel. `assets` explicitly downloads the pinned noVNC source archive, verifies its SHA-256 digest, retains upstream licenses, and refuses to replace an existing destination. There is no import-time network activity.

`serve` binds `$DATA/http.sock` with mode 0600. Configure your chosen HTTPS proxy to forward HTTP and WebSocket requests to that Unix socket and preserve the original Host. Configure its externally visible HTTPS origin exactly in settings; forwarded headers do not select it. Cloudflare Quick Tunnel hostnames are temporary and require explicit settings updates when they change. A named tunnel or stable reverse-proxy hostname avoids that operational issue. Do not expose the Hermes admin dashboard through this viewer tunnel.

The standalone service intentionally does not guess bot credentials. Native Telegram delivery gets the token through Hermes's supported secret API in the calling profile.

### Docker browser certificate trust

If a site inside the streamed Chromium shows `NET::ERR_CERT_AUTHORITY_INVALID`,
diagnose browser egress separately from the viewer's public HTTPS certificate.
For Hermes egress-proxy deployments, follow [persistent Chromium trust setup](docs/CHROMIUM_TRUST.md): verify the proxy CA, keep a dedicated owner-only NSS database on the host, and mount it into the desktop user's certificate-store locations for every new container. A one-time import inside a disposable container does not survive recreation.

This is an explicit operator prerequisite, not an automatic plugin installer.
The plugin never installs trust anchors, changes Docker mounts, or bypasses
certificate verification on load. Do not commit the machine's trust database or
CA private key. Verify actual browser HTTPS after a controlled restart before
claiming reboot-tested installation.

## Owner-local maintenance

These sensitive commands require an explicit owner-local invocation and reject messaging identity. Do not forge or strip inherited session identity to make a rejection pass.

```sh
.venv/bin/computer-view --data-dir "$DATA" status
.venv/bin/computer-view --data-dir "$DATA" revoke --local-owner
.venv/bin/computer-view --data-dir "$DATA" recover --local-owner
```

`recover` returns control only after the owner has verified that no browser command remains active; a fresh observation is still required. A failed or timed-out tool, failed observation, or crashed worker leaves an unsettled state rather than granting a possibly unsafe takeover. Do not use recovery merely to dismiss a warning.

`link --local-owner` prints a fresh bearer URL for explicit local use. Treat it as a secret and never publish its output. Telegram tool delivery does not return the bearer URL to the model.

## Development and verification

```sh
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pip check
```

Install Node.js to run the executable frontend state-machine tests; these use clearly identified DOM/RFB test doubles, not fake desktop demonstrations. Actual installed-Hermes and real-desktop integration are separate release gates. Check VERIFICATION.md when present for commands and observed results rather than treating this README as proof that a deployment was tested.

## Why this is a plugin

A skill can explain a handoff. It cannot provide the authenticated live stream, parse and filter RFB input on the server, serialize supported agent execution against human control across processes, or require a new observation after handback. Computer View implements those mechanisms in code and bundles a skill to make the tools usable.

## Privacy and license

Computer View stores hashed access capabilities and bounded lifecycle events. It does not record sessions, log keystrokes, or persist bot credentials. Desktop pixels can contain sensitive information; share only the dedicated desktop you intend to expose.

Project code is MIT-licensed. Installed noVNC assets retain their separate upstream licenses. No warranties or complete isolation against same-user programs are implied.
