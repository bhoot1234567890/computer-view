# Chromium certificate trust that survives container recreation

This is an operator setup guide for Linux/Docker deployments using Hermes's
HTTPS egress proxy. It is **not an automatic plugin installer**, and is unnecessary
when the desktop browser does not use that proxy. Computer View does not change
Hermes configuration, install trust anchors, or restart browsers on registration.

## Identify the failing layer first

- A certificate warning on the viewer's public HTTPS hostname is an ingress/TLS
  problem. Repair that server certificate; do not apply this guide to the user's
  phone or personal browser.
- A warning for GitHub, Google, or another site **inside** the streamed Chromium
  can be missing proxy-CA trust. Compare the mounted CA in the actual browser
  container with the trusted operator's proxy CA before importing anything.
- In the native gateway, unauthenticated `/` and `/open` serve the public,
  preview-safe entry page (HTTP 200); `/desktop` requires authentication
  (HTTP 401). A root 401 may describe an older standalone/live deployment,
  not the native gateway's current routes. A bearer fragment must never be
  printed in diagnostic logs.

A valid CA file being mounted at `/etc/ssl/certs/hermes-egress-ca.crt` does not by
itself provision Chromium's user-level NSS database. Installing it only inside a
disposable container fixes the current instance, but loses the fix when that
container is replaced. Rebooting does not always replace containers; recreation
is the important boundary.

## Provision a dedicated persistent trust database

Run these commands on the host as the authorized operator, not inside the
throwaway browser container. Resolve the active profile's `HERMES_HOME`; never
reuse another profile's state. Requirements: OpenSSL and NSS `certutil` (the
Debian/Ubuntu package is `libnss3-tools`). Package installation requires the
operator's normal approval. Never fetch a CA from an untrusted website.

```sh
PROFILE_HOME="${HERMES_HOME:-$HOME/.hermes}"
CA_CERT="$PROFILE_HOME/proxy/ca.crt"
TRUST_DIR="$PROFILE_HOME/computer-view/chromium-nssdb"

# Compare this SHA-256 fingerprint with the trusted operator's proxy CA and
# the CA mounted in the actual browser container. Stop on a mismatch/expiry.
openssl x509 -in "$CA_CERT" -noout -subject -issuer -fingerprint -sha256 -dates
openssl x509 -in "$CA_CERT" -noout -checkend 0

umask 077
mkdir -p "$TRUST_DIR"
chmod 700 "$TRUST_DIR"
if [ ! -f "$TRUST_DIR/cert9.db" ]; then
    certutil -N -d "sql:$TRUST_DIR" --empty-password
fi
certutil -A -d "sql:$TRUST_DIR" -n 'Hermes Egress Proxy CA' \
    -t 'C,,' -i "$CA_CERT"
certutil -L -d "sql:$TRUST_DIR" -n 'Hermes Egress Proxy CA'
```

The empty password initializes a dedicated public-CA database; it is not a login
password. Do not copy a personal browser's NSS database or private keys. The
persistent directory must be readable and writable by the actual desktop UID
inside Docker. Verify UID ownership; do not solve it with world-writable modes.

## Mount it for every new browser container

Discover the desktop user's actual HOME inside the sandbox. It can differ from
the terminal user's HOME. Chromium versions can use either of these locations:

- `DESKTOP_HOME/.local/share/pki/nssdb`
- `DESKTOP_HOME/.pki/nssdb`

Bind the **same absolute host TRUST_DIR** to both locations. First inspect the
active profile's existing mounts:

```sh
hermes config get terminal.docker_volumes
```

Use `hermes config set terminal.docker_volumes '<JSON list>'` with the complete
existing list plus the following two entries, replacing all placeholders:

```json
[
  "/ABSOLUTE/HOST/TRUST_DIR:/ABSOLUTE/DESKTOP_HOME/.local/share/pki/nssdb",
  "/ABSOLUTE/HOST/TRUST_DIR:/ABSOLUTE/DESKTOP_HOME/.pki/nssdb"
]
```

The example contains only the new entries: **setting this key replaces the list**,
so retain every existing mount. `hermes config get` renders populated lists as
YAML, not JSON. Never hand-edit configuration or silently remove existing binds.
Read the value back after saving. Do not expose the host HOME, proxy credentials,
or raw browser/RFB sockets through additional mounts or network listeners.

New binds apply when a container is created. To repair an already running
container without replacing it, explicitly import the same verified public CA
into its desktop user's NSS store. Do not invoke a new process as root and
accidentally provision root's store instead. A Chromium restart may be required;
preserve its existing profile, arguments, display, and Xauthority. Coordinate
with the human before interrupting an active login.

## Verification and maintenance

Before declaring success:

1. Read back the saved volume configuration and confirm host-directory ownership.
2. Start a disposable network-disabled container from the deployment image with
   these binds. Verify the desktop UID can read the database at both locations.
3. In the actual desktop Chromium, load GitHub and Google over HTTPS. Confirm the
   real page loads, not `chrome-error://chromewebdata/` or a privacy interstitial.
4. When convenient, perform a controlled restart/recreation and repeat the real
   browser checks. A database mount check alone does not prove a full reboot.

Never use `--ignore-certificate-errors`, certificate-error overrides, or warning
bypass clicks. Preserve the egress proxy and its policy. If its CA is deliberately
rotated, verify the new fingerprint and update this dedicated trust store;
persistence is not automatic CA rotation.

Keep the database, private keys, runtime settings, access cookies, and bearer
links outside the repository. The repository ships this reproducible procedure,
not a copy of a maintainer's machine trust state.
