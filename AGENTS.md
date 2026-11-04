# Repository Guidelines

## Project Overview

Computer View provides owner-scoped, passwordless browser access to an existing Linux desktop. It gates noVNC and a WebSocket-to-RFB bridge behind single-use links; Telegram delivery and a Cloudflare Quick Tunnel are optional. It does not provision the desktop, browser, or VNC server.

## Architecture & Data Flow

- The CLI authorizes the owner and mints a host-bound capability; the browser redeems its URL-fragment token for a secure session cookie. SQLite stores token digests, not bearer tokens.
- Browser traffic reaches an HTTPS proxy or optional Quick Tunnel, then the aiohttp gateway over a private HTTP Unix socket. Authenticated `/websockify` traffic bridges to an existing private raw RFB Unix socket.
- Preserve origin and host checks, single-use redemption, session expiry and revocation, private file permissions, and stream limits. Never expose RFB, CDP, or the gateway directly to the Internet.
- Configuration, SQLite state, and optional tunnel origin are owner-local. `state_dir/public-url.txt` overrides the configured public origin; stale tunnel state affects routing.

## Key Directories

- `src/computer_view/` — CLI, gateway, persistence, configuration, and optional adapters.
- `tests/` — isolated unittest coverage, including an in-process gateway and Unix-socket RFB fixture.
- `docs/`, `examples/systemd/`, `integrations/hermes/` — troubleshooting, adaptable user services, and the portable Hermes skill.

## Development Commands

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
ruff check src tests
python -m build
```

For an actual desktop, as its configured non-root owner: `computer-view init --rfb-socket PATH`, `computer-view assets install`, then `computer-view serve`. Configure an HTTPS proxy or run `computer-view tunnel` separately; use `computer-view --local-owner link` to mint a link. Global options precede the subcommand. See `README.md` for prerequisites and safe setup.

## Code Conventions & Common Patterns

- Python modules and functions use `snake_case`, classes use `PascalCase`, and constants use uppercase. Use `pathlib.Path` for paths and follow nearby type-annotation and formatting patterns; Ruff's line length is 110.
- The CLI emits JSON results and converts expected errors to JSON on stderr with a nonzero exit. Gateway handlers reject invalid requests with explicit HTTP errors; security-sensitive storage checks fail closed. Do not leak tokens or secrets in errors or logs.
- The gateway uses async aiohttp handlers and concurrent stream tasks, cancelling and gathering tasks on termination. Keep socket and task cleanup explicit.
- Prefer the existing injection points: `gateway.create_app` accepts a store, socket, asset directory, and origin provider for isolated operation and tests. Gateway-local rate and concurrency state lives in the application closure; persistent access state lives in `Store`.

## Important Files

- `pyproject.toml` — dependencies, Hatchling build, Ruff configuration, and `computer-view` entry point (`computer_view.cli:main`); `python -m computer_view` uses the same CLI.
- `src/computer_view/cli.py` — command dispatch, owner authorization, link minting, and Telegram delivery.
- `src/computer_view/gateway.py` — authentication, noVNC serving, and WebSocket/RFB bridge; `store.py` — SQLite links and sessions; `config.py` — XDG paths and origin validation.
- `assets.py` — pinned, checksum-verified noVNC installation; `tunnel.py` — optional tunnel lifecycle; `browser_trust.py` — opt-in NSS CA trust helper (all under `src/computer_view/`).
- `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `docs/troubleshooting.md` — operation, contribution, security boundaries, and troubleshooting.

## Runtime/Tooling Preferences

Use Linux and Python 3.11+ with a virtual environment and pip. Runtime dependency: aiohttp; optional `cloudflared` is installed separately. Real NSS integration tests need `openssl` and `certutil` (`libnss3-tools`). `.env.example` is a template, not automatically loaded. CI uses Ubuntu and Python 3.11–3.13.

## Testing & QA

Use standard-library `unittest`; async gateway cases use `IsolatedAsyncioTestCase` and aiohttp test utilities. Follow existing temporary, local fixtures; do not use a production desktop or commit live tokens, cookies, IDs, private keys, or deployment state. Write a failing regression test before changing behavior. No coverage threshold or coverage command is configured.

Before release, verify real HTTPS/WSS/RFB in an isolated deployment and install both the wheel and source archive. Unit tests and `computer-view doctor` do not prove public connectivity or a complete RFB handshake.
