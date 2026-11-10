# Computer View release gates

This checklist is not evidence that an item has passed. Record commands, timestamps, and actual outputs in VERIFICATION.md before marking a gate complete.

## Local release

- [ ] Plugin doctor passes against the installed Hermes runtime.
- [ ] Catalog validator passes against the exact release source.
- [ ] Full regression suite passes without skipped security tests.
- [ ] Wheel/sdist build and clean virtualenv installation pass.
- [ ] Real noVNC assets are checksum-verified and licenses retained.
- [ ] Real dedicated Xvnc handshake, framebuffer, watching, takeover input, handback, and revocation are exercised through the new gateway.
- [ ] Mobile viewport and actual Chromium interaction pass.
- [ ] For Docker + egress-proxy deployments, follow docs/CHROMIUM_TRUST.md; verify the persisted NSS mounts and actual GitHub/Google HTTPS after a controlled container restart/recreation. A current-container repair or mount-only probe is not full reboot evidence.
- [ ] Independent security review findings resolved or disclosed.
- [ ] Public source contains no credentials, bearer links, owner configuration, databases, screenshots with private data, installed binaries, or virtualenvs.
- [ ] Existing live installation remains unchanged; any migration has separate authorization and rollback.

## Public release and hackathon

- [ ] User authorizes GitHub account and confirms repository ownership.
- [ ] Publish repository, read back exact commit, and verify hosted CI.
- [ ] Add a private security contact chosen by the maintainer.
- [ ] Catalog metadata references the real repository and full release commit, with truthful capabilities and risk disclosure.
- [ ] Open catalog pull request and read back exact target and content.
- [ ] At least two outside users try documented installation; record honest feedback (not automated test clients).
- [ ] Record a real demonstration under 90 seconds; no staged successful backend responses or fake frames.
- [ ] User authorizes X account; post actual demo and tag @NousResearch.
- [ ] Submit real PR and demo links to the hackathon form, inspect any terms before agreeing, and verify submission receipt.
- [ ] Deadline from the supplied announcement: November 11, 2026, 17:00 EST / November 12, 2026, 03:30 IST. Aim to submit November 10.

## Demo storyboard (target ~75 seconds)

1. Show a real safe browser task and the choice requiring human judgment.
2. Ask for Computer View in the owner's private Telegram chat.
3. Open the actual one-time link on a real phone.
4. Show watch mode; demonstrate that input is disabled before takeover.
5. Take control and make the decision; show supported agent input being refused.
6. Hand back. Ask Hermes to continue in Telegram; show its fresh observation and actual task completion. Do not imply that UI handback automatically starts a new agent turn.
7. Revoke access and show the open viewer disconnecting.

Never display secret links in a published recording. Frame or redact the entry link and any bot tokens, cookies, owner IDs, private desktop content, or credentials. Show the product, not credential entry or a CAPTCHA workaround.
