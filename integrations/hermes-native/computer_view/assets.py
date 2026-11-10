"""Explicit, integrity-pinned noVNC source installation; no import-time I/O."""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from urllib.request import urlopen

COMMIT = 'a8dfd6a3ea3c74244f5ebdaa5a7f1023007a7820'
SHA256 = 'ebb8eabcd3670ce1a02e4025f03976826c289121cf631183647bd8325b003403'
URL = f'https://codeload.github.com/novnc/noVNC/tar.gz/{COMMIT}'
MAX_ARCHIVE = 4 * 1024 * 1024
MAX_EXPANDED = 16 * 1024 * 1024


def install_novnc(destination, archive_bytes=None):
    """Download only when explicitly called, verify, then publish a fresh directory."""
    destination = Path(destination).absolute()
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('Symlink asset path refused')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Asset destination already exists; choose a fresh directory')
    if archive_bytes is None:
        with urlopen(URL, timeout=30) as response:
            archive_bytes = response.read(MAX_ARCHIVE + 1)
    if len(archive_bytes) > MAX_ARCHIVE:
        raise ValueError('Archive size limit exceeded')
    if hashlib.sha256(archive_bytes).hexdigest() != SHA256:
        raise ValueError('noVNC SHA256 mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix='.novnc-', dir=destination.parent))
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode='r:gz') as tar:
            total = 0
            count = 0
            root = None
            seen = set()
            for member in tar:
                count += 1
                total += member.size
                parts = PurePosixPath(member.name).parts
                if (count > 5000 or total > MAX_EXPANDED or member.size < 0 or
                        not parts or member.name.startswith('/') or '\\' in member.name or
                        '..' in parts or not (member.isdir() or member.isfile())):
                    raise ValueError('Unsafe archive member or size limit')
                if root is None:
                    root = parts[0]
                if parts[0] != root:
                    raise ValueError('Archive has multiple roots')
                relative = Path(*parts[1:])
                if len(parts) == 1 or member.isdir():
                    continue
                if relative in seen:
                    raise ValueError('Duplicate archive member')
                seen.add(relative)
                if not (parts[1] in ('core', 'app', 'vendor') or
                        (len(parts) == 2 and (parts[1].startswith('LICENSE') or
                         parts[1] in ('AUTHORS', 'README.md')))):
                    continue
                output = staging / relative
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tar.extractfile(member) as source, output.open('xb') as target:
                    shutil.copyfileobj(source, target)
                output.chmod(0o600)
        if not (staging / 'core/rfb.js').is_file() or not list(staging.glob('LICENSE*')):
            raise ValueError('Missing noVNC core or license')
        # A concurrently created directory must never be replaced (even if empty).
        os.mkdir(destination, 0o700)
        try:
            for item in staging.iterdir():
                os.rename(item, destination / item.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
        return destination
    finally:
        shutil.rmtree(staging)
