---
name: computer-view
description: "Use when requesting private Computer View access."
---

# Portable Computer View integration

Install the package in the environment that can reach the configured desktop's private RFB Unix socket. Run gateway and HTTPS adapter separately. Set the owner UID, profile and private Telegram ID with `computer-view init`. Tokens belong in protected environment variables, never this skill.

For an inherited authorized Hermes session, invoke `computer-view telegram` to send a fresh owner-only Telegram button, `computer-view link` only when the authorized owner explicitly requests a link here, or `computer-view revoke` to end all access. Supply a nondefault configuration using global `--config PATH` before the subcommand.

Never fabricate or strip HERMES_SESSION_* identity. Do not use --local-owner to override a messaging identity. Plain owner shell operation requires explicit --local-owner. Never resend cached bearer links. Check exit status and verified delivery response; do not print bot credentials.

See docs/troubleshooting.md for egress policy, NSS trust and host/container checks. Do not widen domain policy without operator approval or bypass TLS validation. This does not register a slash command or install services automatically.
