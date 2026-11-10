"""Private Computer View capability data; not a core credential store.

Owners must be canonical, profile-qualified identities supplied by the trusted
caller. Host values must likewise be canonical and derived from trusted config.
Only capability SHA-256 digests are stored. No sliding session renewal.
"""
from contextlib import contextmanager
import base64
import hashlib
import os
import math
from pathlib import Path
import re
import secrets
import stat
import sqlite3
import time

LINK_TTL = 300
SESSION_TTL = 1800


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value):
        return None
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def _clock(now):
    value = time.time() if now is None else now
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('time must be finite')
    return value


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = self.path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
            raise PermissionError('database parent must be owned and not writable by others')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS links (
                    hash TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    host TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    host TEXT NOT NULL, expires REAL NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        before = self.path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise OSError('database must be a regular non-symlink file')
        db = sqlite3.connect(self.path.absolute().as_uri() + '?mode=rw',
                             uri=True, timeout=0.25)
        after = self.path.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            db.close()
            raise OSError('database replaced during open')
        try:
            with db:
                yield db
        finally:
            db.close()

    def mint(self, owner, host, now=None):
        now = _clock(now)
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            for table in ('links', 'sessions'):
                db.execute(f'DELETE FROM {table} WHERE hash IN '
                           f'(SELECT hash FROM {table} WHERE expires<=? LIMIT 1000)', (now,))
            db.execute('INSERT INTO links VALUES (?,?,?,?)',
                       (digest(token), owner, host, now + LINK_TTL))
        return token

    def redeem(self, token, host, now=None):
        try:
            now = _clock(now)
        except (ValueError, OverflowError):
            return None
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT owner,host,expires FROM links WHERE hash=?',
                                 (digest(token),)).fetchone()
                if row is None or row[1] != host or row[2] <= now:
                    return None
                db.execute('DELETE FROM links WHERE hash=?', (digest(token),))
                session = secrets.token_urlsafe(32)
                db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                           (digest(session), row[0], host, now + SESSION_TTL))
        except (sqlite3.Error, OSError):
            return None
        return session

    def revoke(self, owner):
        with self.connect() as db:
            db.execute('DELETE FROM links WHERE owner=?', (owner,))
            db.execute('DELETE FROM sessions WHERE owner=?', (owner,))

    def session_info(self, session, host, now=None):
        try:
            now = _clock(now)
        except (ValueError, OverflowError):
            return None
        try:
            with self.connect() as db:
                row = db.execute('SELECT owner,host,expires FROM sessions WHERE hash=?',
                                 (digest(session),)).fetchone()
        except (sqlite3.Error, OSError):
            return None
        if row is None or row[1] != host or row[2] <= now:
            return None
        viewer_id = base64.urlsafe_b64encode(hashlib.sha256(
            b'computer-view/viewer-id/v1\0' + session.encode('ascii')
        ).digest()).decode('ascii').rstrip('=')
        return {'owner': row[0], 'expires_at': row[2], 'viewer_id': viewer_id}

    def valid(self, session, host, now=None):
        return self.session_info(session, host, now) is not None