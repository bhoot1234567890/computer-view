"""Portable, owner-scoped configuration. Configuration never contains bot tokens."""
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


def validate_origin(value: str) -> str:
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError('Invalid HTTPS origin port') from error
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/') or '\\' in value):
        raise ValueError('Expected an HTTPS origin without credentials, path, query, or fragment')
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if not all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                   for label in parsed.hostname.split('.')):
            raise ValueError('Invalid public origin hostname')
    return value.rstrip('/')


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError('Private state directory may not be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise PermissionError('State directory must belong to the current user')
    path.chmod(0o700)
    return path


def user_path(value: str | Path) -> Path:
    # Do not resolve /proc/PID/root: that would lose the target mount namespace.
    return Path(value).expanduser().absolute()


def default_config_path() -> Path:
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    return base / 'computer-view/config.toml'


@dataclass(frozen=True)
class Config:
    path: Path
    state_dir: Path
    rfb_socket: Path
    novnc_dir: Path
    http_socket: Path
    public_origin: str | None
    owner_uid: int
    telegram_user_id: str = ''
    telegram_token_env: str = 'TELEGRAM_BOT_TOKEN'
    profile: str = 'default'

    @property
    def owner(self) -> dict:
        return {'unix_uid': self.owner_uid, 'telegram_id': self.telegram_user_id, 'profile': self.profile}

    def origin(self) -> str:
        saved = self.state_dir / 'public-url.txt'
        if saved.exists():
            if saved.is_symlink() or saved.stat().st_uid != self.owner_uid or saved.stat().st_mode & 0o022:
                raise PermissionError('Origin file must be owner-controlled')
            return validate_origin(saved.read_text().strip())
        if self.public_origin:
            return validate_origin(self.public_origin)
        raise ValueError('No public origin: start computer-view tunnel or configure public_origin')

    @classmethod
    def create(cls, path, *, rfb_socket, novnc_dir, state_dir, public_origin=None,
               telegram_user_id='', profile='default'):
        path = user_path(path)
        if public_origin:
            public_origin = validate_origin(public_origin)
        private_directory(path.parent)
        state_dir = private_directory(user_path(state_dir))
        values = {
            'state_dir': str(state_dir), 'rfb_socket': str(user_path(rfb_socket)),
            'novnc_dir': str(user_path(novnc_dir)), 'http_socket': str(state_dir / 'http.sock'),
            'public_origin': public_origin or '', 'owner_uid': os.getuid(),
            'telegram_user_id': str(telegram_user_id), 'telegram_token_env': 'TELEGRAM_BOT_TOKEN',
            'profile': profile,
        }
        text = '# Computer View configuration; secrets belong in the environment.\n'
        text += ''.join(f'{key} = {json.dumps(value)}\n' for key, value in values.items())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
        return cls.load(path)

    @classmethod
    def load(cls, path):
        path = user_path(path)
        if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
            raise PermissionError('Config must be an owner-controlled regular file')
        values = tomllib.loads(path.read_text())
        allowed = {'state_dir', 'rfb_socket', 'novnc_dir', 'http_socket', 'public_origin',
                   'owner_uid', 'telegram_user_id', 'telegram_token_env', 'profile'}
        if set(values) - allowed:
            raise ValueError('Unknown configuration keys: ' + ', '.join(sorted(set(values) - allowed)))
        if values['owner_uid'] != os.getuid():
            raise PermissionError('Run Computer View as the configured owner, not another user/root')
        origin = values.get('public_origin') or None
        if origin:
            origin = validate_origin(origin)
        config = cls(path=path, state_dir=user_path(values['state_dir']),
                     rfb_socket=user_path(values['rfb_socket']), novnc_dir=user_path(values['novnc_dir']),
                     http_socket=user_path(values['http_socket']), public_origin=origin,
                     owner_uid=values['owner_uid'], telegram_user_id=str(values.get('telegram_user_id', '')),
                     telegram_token_env=values.get('telegram_token_env', 'TELEGRAM_BOT_TOKEN'),
                     profile=values.get('profile', 'default'))
        private_directory(config.state_dir)
        return config
