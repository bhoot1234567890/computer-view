"""Explicit, fingerprint-pinned import of a public proxy CA into Chromium's NSS store.

Run inside the browser's environment as its actual user. No browser restart,
network-policy changes, or TLS-verification bypass is performed here.
"""
import hashlib
import hmac
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from computer_view.config import private_directory

NICKNAME = 'Computer View Proxy CA'


def _run(arguments):
    try:
        return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('Certificate tool failed; verify NSS ownership and supplied CA') from error


def configure(ca_file, sha256, database, *, apply=False):
    """Check a public CA and optionally trust it. sha256 hashes the PEM file bytes."""
    if not re.fullmatch(r'[a-fA-F0-9]{64}', sha256):
        raise ValueError('Expected a 64-character SHA-256 of the public PEM file')
    ca_file, database = Path(ca_file).expanduser(), Path(database).expanduser().absolute()
    with ca_file.open('rb') as stream:
        data = stream.read(65537)
    if len(data) > 65536 or b'PRIVATE KEY' in data or data.count(b'-----BEGIN CERTIFICATE-----') != 1:
        raise ValueError('Supply one public PEM certificate, never a private key or bundle')
    actual = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual, sha256.lower()):
        raise ValueError('Public CA fingerprint mismatch; no trust settings changed')
    openssl = shutil.which('openssl')
    if not openssl:
        raise RuntimeError('Install openssl in the browser environment first')
    if database.is_symlink():
        raise ValueError('Supply the real NSS database path, not a symlink')
    if database.exists() and (not database.is_dir() or database.stat().st_uid != os.getuid()):
        raise PermissionError('NSS database must belong to the current browser user')
    with tempfile.TemporaryDirectory(prefix='computer-view-ca-') as directory:
        snapshot = Path(directory) / 'public-ca.pem'
        snapshot.write_bytes(data)
        checked = _run([openssl, 'x509', '-in', str(snapshot), '-noout', '-text', '-checkend', '0'])
        if 'CA:TRUE' not in checked.stdout:
            raise ValueError('The supplied certificate is not a CA')
        if apply:
            certutil = shutil.which('certutil')
            if not certutil:
                raise RuntimeError('Install libnss3-tools (certutil) in the browser environment first')
            private_directory(database)
            if not (database / 'cert9.db').exists():
                _run([certutil, '-N', '-d', 'sql:' + str(database), '--empty-password'])
            _run([certutil, '-A', '-d', 'sql:' + str(database), '-n', NICKNAME,
                  '-t', 'C,,', '-i', str(snapshot)])
            exported = _run([certutil, '-L', '-d', 'sql:' + str(database), '-n', NICKNAME, '-a'])
            # Compare DER, since certutil may normalize PEM whitespace.
            import ssl
            if ssl.PEM_cert_to_DER_cert(exported.stdout) != ssl.PEM_cert_to_DER_cert(data.decode('ascii')):
                raise RuntimeError('NSS readback did not match the supplied public CA')
    return {'status': 'installed' if apply else 'checked', 'database': str(database),
            'sha256': actual, 'restart_browser': bool(apply)}
