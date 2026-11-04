"""Pinned noVNC assets; no opaque install scripts and no unsafe tar extraction."""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from urllib.request import urlopen
from computer_view.config import private_directory

NOVNC_COMMIT = 'e8b5024d4603587b29ffede4fbd28b1fa2939829'
NOVNC_SHA256 = 'c9a2e4cd3ad21c3283969fe1ae8b9a5fa45ee69030d34d55dbe38605dddd261e'
NOVNC_URL = 'https://codeload.github.com/novnc/noVNC/tar.gz/' + NOVNC_COMMIT
MAX_ARCHIVE = 16777216
MAX_EXPANDED = 67108864


def unpack(data: bytes, destination: Path, expected_sha256: str):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Asset directory already exists; review/remove it explicitly before replacing')
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('noVNC archive checksum or size verification failed')
    private_directory(destination.parent)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 8192 or sum(member.size for member in members) > MAX_EXPANDED:
            raise ValueError('noVNC archive exceeds extraction limits')
        paths = []
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or not path.parts[0].startswith('noVNC-') or not (member.isdir() or member.isfile())):
                raise ValueError('Unsafe archive member rejected')
            relative = Path(*path.parts[1:])
            paths.append((member, relative))
        with tempfile.TemporaryDirectory(prefix='.novnc-', dir=destination.parent) as directory:
            tree = Path(directory) / 'assets'
            tree.mkdir(mode=0o700)
            for member, relative in paths:
                target = tree / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError('Invalid regular archive member')
                with stream, target.open('xb') as output:
                    shutil.copyfileobj(stream, output)
                target.chmod(0o644)
            if not (tree / 'vnc.html').is_file():
                raise ValueError('Archive has no noVNC entry point')
            os.rename(tree, destination)


def install(destination: Path):
    with urlopen(NOVNC_URL, timeout=60) as response:
        data = response.read(MAX_ARCHIVE + 1)
    unpack(data, destination, NOVNC_SHA256)
