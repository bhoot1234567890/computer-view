"""Owner-local capability store. Only SHA-256 digests are persisted."""
from contextlib import contextmanager
import hashlib
import os
import secrets
import re
import sqlite3
import time
from pathlib import Path

LINK_TTL = 300
SESSION_TTL = 1800


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value):
        return None
    return hashlib.sha256(value.encode('ascii')).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS links (
                    hash TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    host TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    host TEXT NOT NULL, expires REAL NOT NULL
                );
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=0.25)
        try:
            with db:
                yield db
        finally:
            db.close()

    def mint(self, owner, host, now=None):
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('INSERT INTO links VALUES (?,?,?,?)',
                       (digest(token), owner, host, now + LINK_TTL))
        return token

    def redeem(self, token, host, now=None):
        now = time.time() if now is None else now
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
        return session

    def revoke(self, owner):
        with self.connect() as db:
            db.execute('DELETE FROM links WHERE owner=?', (owner,))
            db.execute('DELETE FROM sessions WHERE owner=?', (owner,))

    def valid(self, session, host, now=None):
        now = time.time() if now is None else now
        try:
            with self.connect() as db:
                row = db.execute('SELECT host,expires FROM sessions WHERE hash=?',
                                 (digest(session),)).fetchone()
        except sqlite3.Error:
            return False
        return bool(row and row[0] == host and row[1] > now)
