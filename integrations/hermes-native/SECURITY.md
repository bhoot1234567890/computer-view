# Computer View security model

Computer View is a private viewer for a dedicated Hermes virtual desktop. It is not a sandbox, credential manager, or boundary against another process running as the same Unix user.

## Authority and transport

- Native access tools authorize the fixed owner's private Telegram identity using Hermes task-local session context, not model arguments or display names.
- Each installation has explicit profile-scoped settings and private data. There is no public endpoint for minting access or revoking all sessions.
- A one-time capability expires after five minutes. It is carried in the fragment, removed from the address bar before explicit redemption, and stored only as a digest locally. Anyone holding an unused capability can redeem it: never forward it.
- Preview GET requests do not redeem capabilities. Redemption requires same-origin POST and creates a Secure, HttpOnly, SameSite=Strict host cookie with an absolute thirty-minute lifetime.
- Public HTTPS must terminate at a separately configured reverse proxy/tunnel. The application binds a private Unix socket; raw RFB and browser CDP must never be exposed publicly.
- WebSocket and state-changing requests are checked against an independently pinned HTTPS origin. Forwarded headers cannot select the origin.

## Control and handback

- Watch mode is enforced on the server by parsing the RFB client stream across arbitrary WebSocket frame boundaries. The frontend's view-only switch is a usability feature, not the authorization boundary.
- Human input and supported agent tool admission share a profile-wide advisory file lock. An already admitted tool finishes before takeover is granted. A busy response is not a successful takeover.
- The public tool-execution middleware fences browser_exec, computer_use, and browser_vault, including captures, while a human controls this plugin's viewer. The plugin must be enabled in each relevant Hermes process. An unprotected process is not covered.
- Handback marks fresh observation required. Supported screen tools remain fenced until the plugin's fixed observation tool succeeds. The tool does not automatically resume a conversation or guarantee task completion; Hermes must receive the observation and continue the task in its next execution.
- A lost phone, closed viewer, revoked cookie, or expired session does not silently hand control back. The owner must explicitly recover control locally when a session can no longer hand back.
- The lock is persisted with an in-flight marker before dispatch. A failed/timed-out action, failed observation, or crashed worker leaves an unsettled state that refuses takeover. Explicit owner recovery requires first verifying that no browser work remains, then a fresh observation; releasing a file lock alone is not treated as proof that a browser daemon stopped.
- Execution middleware is supported in Hermes's in-process plugin mode only. Host-isolated live callbacks are not a supported handoff configuration. Hermes itself skips failed/unavailable middleware, so the plugin catches admission errors and explicitly denies, but cannot enforce a fence when unloaded or disabled.
- This is not an integration with the separate built-in Hermes Bot Desktop takeover lease. Other viewer implementations do not participate in this plugin's control lock.

## Boundaries and disclosures

- Shell scripts, terminal tools, direct CDP clients, third-party plugins that bypass normal tool dispatch, privileged processes, and same-user programs are outside the fence. No claim of complete OS-level exclusion is made.
- The agent's pre-existing screen content may remain in its previous context. Blocking new captures cannot erase prior observations.
- Desktop applications and arbitrary web pages remain untrusted. Computer View does not solve prompt injection or authenticate websites.
- noVNC source assets are installed separately from a pinned upstream commit and verified SHA-256 archive. Third-party licenses must remain alongside the installed assets. There is no import-time downloader.
- Bot credentials use Hermes's supported secret API. Runtime capability storage is plugin-owned session state, not a replacement secret backend.
- Lifecycle history contains only bounded event labels and timestamps. No keystroke log or session recording is collected.

## Security reports

Do not include bearer links, cookies, bot tokens, desktop screenshots containing personal data, or private runtime files in a public issue. Report a reproducible description without those values. A private maintainer contact must be added before public release; no contact is fabricated here.
