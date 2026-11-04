# Security

This Linux alpha grants control of the configured desktop. Treat unused links and session cookies as bearer credentials. Links expire after five minutes and redeem once; sessions last thirty minutes. Use `computer-view --local-owner revoke` to invalidate all outstanding access.

Require a valid HTTPS reverse proxy and owner-only HTTP/RFB Unix sockets. Never publish RFB or CDP ports. Do not expose state databases, bot tokens, proxy configuration or CA private keys. Public CA certificates are not private keys, but only trust certificates whose provenance and fingerprint you have independently verified.

The gateway enforces same-origin redemption/WebSockets, authenticated assets, stream/redemption limits, and periodic session checks. This is not protection against root or a compromised process running as the configured owner. Human and agent input are not coordinated, and existing browser logins are accessible to anyone granted a session.

`browser-ca` changes no trust without `--apply`; it must run as the browser user in the browser environment. Trusting a proxy CA permits that proxy to inspect HTTPS. Do not use TLS-warning bypasses or relax private-network protections to solve browsing errors.

Report suspected vulnerabilities privately to the repository owner's designated channel, once configured. Do not post working bearer links, cookies, credentials or exploit details publicly. No audited-production or vulnerability-free guarantee is made.
