"""Explicit profile-scoped settings. This file never contains bot credentials."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    owner_id: str
    profile: str
    owner_uid: int
    origin: str
    rfb_socket: Path
    novnc_dir: Path

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ValueError('Settings must be owner-only and not a symlink')
        data = json.loads(path.read_text())
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError('Invalid settings fields')
        owner = data['owner_id']
        if not isinstance(owner, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', owner):
            raise ValueError('An explicit positive private Telegram owner id is required')
        if type(data['owner_uid']) is not int or data['owner_uid'] != os.getuid():
            raise ValueError('Settings must belong to the current Unix account')
        if not isinstance(data['profile'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',data['profile']):
            raise ValueError('Invalid profile name')
        origin = data['origin']
        if not isinstance(origin, str):
            raise ValueError('A plain HTTPS origin is required')
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
                parsed.query or parsed.fragment or parsed.path not in ('','/') or
                origin != origin.strip() or any(c.isspace() for c in origin)):
            raise ValueError('A plain HTTPS origin is required')
        try:
            parsed.port
        except ValueError:
            raise ValueError('Invalid HTTPS port') from None
        rfb, novnc = Path(data['rfb_socket']), Path(data['novnc_dir'])
        if not rfb.is_absolute() or not novnc.is_absolute():
            raise ValueError('Desktop and asset paths must be absolute')
        return cls(owner,data['profile'],data['owner_uid'],origin.rstrip('/'),rfb,novnc)

    def as_dict(self):
        return {name:str(value) if isinstance(value,Path) else value
                for name,value in self.__dict__.items()}
