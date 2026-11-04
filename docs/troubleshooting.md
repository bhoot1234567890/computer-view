# Troubleshooting

## Check the right machine and user

An agent's terminal may run in Docker while its browser helper runs on the host or in another container. A missing host path inside a terminal container does not establish that the host installation is absent. Identify the actual desktop/container, Unix UID, mount namespace, launcher, and sockets before changing anything. Configuration belongs to one Unix owner; do not fix permission errors by running everything as root or making sockets world-readable.

Start with `computer-view --config "$HOME/.config/computer-view/config.toml" doctor`. Global flags go before the action. Interpret its fields narrowly:

- `assets`: `vnc.html` exists in the configured directory.
- `rfb_socket`: the configured path is a socket; this is not an RFB handshake.
- `http_gateway`: local HTTP socket answers the entry-page probe.
- `https_origin`: configured/discovered origin parses; this is not a certificate or Internet reachability check.

Start the existing desktop/RFB service separately, then `computer-view serve`, then `computer-view tunnel` (or your HTTPS proxy). Keep foreground processes alive. A Quick Tunnel URL belongs to its running invocation. Restarting it changes origin and can invalidate access; mint a fresh link afterward. The package does not deploy or provision that desktop for you.

## Missing assets or stale state

`computer-view assets install` downloads a pinned archive, verifies its digest, and rejects unsafe extraction. It refuses to replace an existing destination. Review and back up an old directory before explicitly replacing it; never disable checksum checks. A failed gateway startup can leave a stale socket; the gateway removes a refused, owner-owned socket but refuses foreign or non-socket paths. Do not delete a live socket blindly.

The private `public-url.txt` state file overrides TOML `public_origin`. Check this when switching from a tunnel to your own proxy; stop the adapter and review stale state. Preserve the public Host and Origin through the proxy, use HTTPS and WSS, and do not disable origin or cookie protections to accommodate rewriting.

## Link, authorization, and delivery errors

An ordinary shell needs `computer-view --local-owner link`, `telegram`, or `revoke`. The flag cannot override messaging identity. Do not invent, edit, or strip inherited `HERMES_SESSION_*` values to gain authorization. Check configured UID/profile and that the actual session is the verified owner's private DM, not a group or thread. Telegram also needs a token in the configured environment variable and a private chat the bot can reach. The CLI does not load `.env` automatically.

A consumed or five-minute-old link needs replacement. Revoke invalidates every outstanding link and session, not the desktop process. Avoid sharing bearer URLs, cookies, full environment dumps, or unredacted screenshots/logs. Do not post bot API URLs: they can contain tokens.

## Websites fail through Hermes egress

The following recovery notes reflect verified behavior in a separate Hermes/browser setup. They are operational guidance, **not a claim that this package has been deployed to production** or a reason to change policy without authorization. They concern outbound browser traffic, not the inbound Computer View tunnel.

### `ERR_TUNNEL_CONNECTION_FAILED`: check policy first

Check the active Hermes profile's iron-proxy log (typically `$HERMES_HOME/proxy/iron-proxy.log`, or `~/.hermes/proxy/iron-proxy.log` when using the default home). HTTP 403 with `rejected_by: allowlist` identifies the egress domain allowlist, not a remote website outage. Do not respond by weakening Chromium TLS or sandbox flags.

If egress is stopped, start it on the machine that owns Hermes egress before testing:

```bash
hermes egress start
```

Prefer adding only needed destinations through the authorized policy. **Only on an explicit owner request to permit all domains**, persist the wildcard setting in the intended Hermes profile:

```bash
hermes config set proxy.extra_allowed_hosts '["*"]'
```

Also review the generated `proxy.yaml` used by the active proxy. Back it up with mode 0600 and append the literal `*` to the **allowlist transform's domains list**, preserving the existing policy structure. Do not replace the whole generated file with a snippet. Keep the secrets transform, secret handling, upstream restrictions, and private-network protections intact: allowing all domains must not mean permitting internal/private networks or disclosing secrets. Generated policy changes alone may be overwritten; the persistent config setting alone did not update the running policy in the verified setup. Then apply it:

```bash
hermes egress reload
```

Read back both the persistent setting and generated allowlist, check that egress is running and rejection logs have cleared, and test real browser navigation. Do not include unredacted generated YAML in bug reports; it can contain sensitive deployment information. Hermes versions may change generated policy structure; inspect the installed version rather than applying a blind text replacement.

### `ERR_CERT_AUTHORITY_INVALID`: public CA trust in actual Chromium NSS

After allowlisting, certificate failures can reveal missing trust for the egress proxy CA. Host `curl` success does not prove the browser container trusts that CA. Identify the running Chromium UID and its actual HOME. Verify that the candidate **public CA certificate**, including its fingerprint, matches the active egress public CA and the container's `/etc/ssl/certs/hermes-egress-ca.crt`. Never copy/import a CA private key; never use an arbitrary certificate from an unverified location.

In the actual browser container, install its distribution's `libnss3-tools` package if needed. Run NSS initialization/import as the **browser user**, not root. Set `DB` to the database that Chromium actually uses and `CA` to the verified public certificate path:

```bash
# DB and CA must already name verified paths in the browser user's context.
certutil -L -d "sql:$DB"
certutil -A -d "sql:$DB" -n 'Hermes Egress Proxy CA' -t 'C,,' -i "$CA"
certutil -L -d "sql:$DB" -n 'Hermes Egress Proxy CA'
```

Chromium installations may use `~/.pki/nssdb` **or** `~/.local/share/pki/nssdb`. Importing into an unused DB does not fix trust. In the verified setup, neither existed: initializing `~/.local/share/pki/nssdb` with `certutil -N -d "sql:$DB" --empty-password` and symlinking the absent legacy `~/.pki/nssdb` path to it resolved the path distinction. Inspect existing directories first; never overwrite an existing NSS database or blindly replace an existing directory with a symlink. Back up any existing DB before changes. Do not reinitialize an existing DB.

Chromium may require a controlled restart before all destinations trust the new CA. Never use `--ignore-certificate-errors`, bypass the warning page, disable certificate verification, or disable the sandbox as a fix. Per-container package/trust changes can disappear when the container is recreated; document an authorized persistent image/volume approach separately. Verify HTTPS navigation and page DOM in the real browser after recovery, not only host curl.

## Restarting Chromium without losing the harness

Do **not** issue `Browser.close` through a cached browser harness without a reconnect plan. The harness may cache the old browser WebSocket path and fail even when Chromium restarts successfully. Inspect the real managed launcher, original arguments, UID, DISPLAY, XAUTHORITY, and DevToolsActivePort before a controlled restart, and preserve its security settings and browser profile. Use fresh local `/json/version` and `/json/list` CDP endpoints for independent recovery/verification where appropriate; never expose CDP publicly. A terminal may reach the same browser container even when the cached browser tool cannot attach. Browser restart and harness reconnection are distinct operations.

## Shared-desktop limits

Human and agent input are uncoordinated: agree on who is typing. No takeover lease is integrated here. Audio is not forwarded. Opening the noVNC viewer in the same desktop it streams causes recursive visuals; close that test viewer promptly. This Linux alpha is not a complete desktop provisioning or browser lifecycle manager.
