# Outside-user installation test

This is an invitation and recording template, not completed user feedback. Automated agents and local fixture clients do not count as outside users.

## Who should try it

Two independent people with Linux, Hermes 0.22.6+, a dedicated same-user Hermes Xvnc desktop, and their own private Telegram bot chat. Start in a disposable Hermes profile. Do not use production credentials or share a personal desktop during the test.

Give testers the source release, README.md, and SECURITY.md. Do not guide them through the first attempt; record where the instructions fail them. Never send them the maintainer's runtime directory, tokens, or viewer links.

## Tasks

1. Read the supported deployment boundary. Decide whether in-process plugins are acceptable in this disposable profile.
2. Install the source and run native Plugin Doctor and catalog validation.
3. Provision your own owner ID, profile, dedicated Unix RFB socket, noVNC assets, and HTTPS viewer origin.
4. Start the private gateway with your own HTTPS proxy/tunnel. Record any setup step that needed undocumented knowledge.
5. Ask your agent for Computer View in your private Telegram DM.
6. Open the entry button on a real phone. Observe whether the confirmation and first desktop frame are understandable.
7. Verify watching cannot type or click. Take control, make a harmless choice, and hand back.
8. Tell Hermes to continue. Check that a fresh observation is delivered before it acts again. Handback alone does not start a new agent turn.
9. Revoke access and verify the open viewer disconnects. Test an expired link separately.
10. Try a wrong user/group invocation and verify it is denied without receiving a bearer URL.
11. Stop the test service and remove only this disposable profile's installation/state using the documented paths. Keep your normal Hermes installation unchanged.

## What to record

- Tester identifier (with consent), operating system and Python/Hermes versions.
- Release source digest / exact Git commit, if published.
- Whether this was genuinely a clean install.
- Start/end timestamps and time to first usable frame.
- Commands that failed, redacted errors, and unclear README steps.
- Phone/browser and viewport issues.
- Whether watch, takeover, handback, fresh observation, and revoke behaved as described.
- Whether the security boundary was understandable.
- One thing that would make the tester use it again; one thing that would make them uninstall it.

## Safety

Do not attach bearer links, cookies, bot tokens, owner configuration, credential files, databases, private screenshots, or full request dumps to issues. Return a redacted description. Do not “fix” a refusal by stripping session identity, disabling TLS validation, or bypassing the control fence with shell/CDP scripts.

## Results

Tester A: not yet recruited.
Tester B: not yet recruited.

No outside-user installation success is claimed until actual people complete the test and their feedback is recorded.
