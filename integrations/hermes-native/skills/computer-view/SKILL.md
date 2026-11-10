---
name: computer-view
description: Use for private Computer View links and user handoff.
---

# Computer View

Use `computer_view_open` only in the configured owner's private Telegram session.
The tool privately delivers an inline button; never ask for or print the bearer URL.
`computer_view_status`, `computer_view_revoke`, and `computer_view_handoff` are private owner tools.
Requesting handoff does not itself prove that the user holds control.

After user handback, call `computer_view_observe` and inspect the actual fresh screenshot
before continuing browser or computer actions. Never claim observation without the capture.
Do not bypass the control guard using terminal, alternate automation, or another session.
The guard covers browser_exec, computer_use, and browser_vault, including reads.

## Supported deployment boundary

Control requires the operator to explicitly select `plugins.isolation: in_process`.
The plugin reads this using the public read-only config API; it never changes the
setting. Host-isolated live callbacks and missing/unknown isolation selection deny
protected operations. A private open button proves delivery, not middleware enrollment
or safe takeover. Only use takeover after the operator verifies all designated tool
workers and the target desktop are enrolled. This does not fence native viewers,
direct registry calls, shell/CDP scripts, or arbitrary same-user processes.

Fixed observation is guarded by the plugin Controller itself: public
`ctx.dispatch_tool` does not reenter agent execution middleware. The wrapper forwards
the current task/session context, but actual desktop/browser correspondence must be
verified in the supported deployment. A screenshot path alone is not proof of freshness.

## Maintainer verification workflow

Use the official `register_tool`, `register_skill`, `register_middleware`, and
`dispatch_tool` APIs only. Keep registration inert, resolve plugin_data_dir and scoped
identity/secrets on every invocation, and return explicit redacted denial on middleware
failure: Hermes skips callbacks that raise. For changes, run a focused failing test,
implement the minimum, rerun focused and full tests; then use actual installed-runtime
plugin doctor and agent-path enrollment tests in a disposable profile. A fake ctx test
is not a substitute for host execution or a browser-quiescence proof.
