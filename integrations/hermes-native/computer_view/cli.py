"""Owner-local setup and supervised foreground service. No tunnel automation."""
import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import signal
import socket
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from .config import Settings
from .control import Controller
from .store import Store


def private_dir(path, *, create=False):
    path = Path(path).absolute()
    # Refuse symlink components rather than silently redirecting state writes.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink data directory refused')
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError('Data directory must belong to current account')
    if info.st_mode & 0o077:
        raise PermissionError('Data directory must have mode 0700')
    return path


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--data-dir', type=Path, help='Private state directory; required without Hermes')
    commands = result.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('setup', help='Create private profile settings; never start services')
    for option in ('owner-id', 'profile', 'origin', 'rfb-socket', 'novnc-dir'):
        setup.add_argument('--' + option, required=True)
    setup.add_argument('--force', action='store_true', help='Replace settings, not control state')
    commands.add_parser('status', help='Show control status without credentials')
    commands.add_parser('serve', help='Foreground private Unix HTTP service; no TCP listener')
    commands.add_parser('doctor', help='Check local prerequisites; no networking')
    commands.add_parser('assets', help='Explicitly download and verify pinned noVNC source')
    for command in ('link', 'revoke', 'recover'):
        owner = commands.add_parser(command)
        owner.add_argument('--local-owner', action='store_true', help='Explicit Unix owner authorization; never messaging')
    return result


def require_local_owner(args, settings):
    if not args.local_owner or os.getuid() != settings.owner_uid:
        raise PermissionError('Explicit --local-owner from the configured Unix account required')
    keys = ('PLATFORM', 'SOURCE', 'USER_ID', 'USER_ID_ALT', 'USER_NAME', 'CHAT_ID',
            'CHAT_TYPE', 'CHAT_NAME', 'THREAD_ID', 'SCOPE_ID', 'MESSAGE_ID', 'PARENT_CHAT_ID')
    read = os.environ.get
    try:
        from gateway.session_context import get_session_env
        read = get_session_env
    except ImportError:
        pass
    if any(os.environ.get('HERMES_SESSION_' + key) or read('HERMES_SESSION_' + key, '') for key in keys):
        raise PermissionError('--local-owner is forbidden in messaging contexts')


def doctor(settings):
    profile = os.environ.get('HERMES_SESSION_PROFILE') or os.environ.get('HERMES_PROFILE')
    if profile and profile != settings.profile:
        raise ValueError('Active profile does not match settings')
    home = os.environ.get('HERMES_HOME')
    if home and Path(home).parent.name == 'profiles' and Path(home).name != settings.profile:
        raise ValueError('HERMES_HOME profile does not match settings')
    socket_info = settings.rfb_socket.lstat()
    if not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid != os.getuid():
        raise PermissionError('RFB must be an owned Unix socket, not a symlink')
    core = settings.novnc_dir / 'core/rfb.js'
    if any(p.is_symlink() for p in (core, *core.parents)) or not core.is_file():
        raise ValueError('Missing or unsafe noVNC core/rfb.js; run assets explicitly')


@contextmanager
def server_socket(root):
    root = private_dir(root)
    fd = os.open(root / 'server.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    path = root / 'http.sock'
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_nlink != 1:
            raise ValueError('Unsafe server lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Computer View server already running') from None
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('Refusing to clobber non-owned or non-socket HTTP path')
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(.2)
                try:
                    probe.connect(str(path))
                except ConnectionRefusedError:
                    path.unlink()
                else:
                    raise RuntimeError('HTTP Unix socket is active')
        yield path
    finally:
        os.close(fd)


async def serve(root, settings):
    from aiohttp import web
    from .server import create_app
    doctor(settings)
    with server_socket(root) as path:
        previous = os.umask(0o077)
        runner = web.AppRunner(create_app(root, settings), access_log=None)
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        inode = None
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stopped.set)
            await runner.setup()
            await web.UnixSite(runner, str(path)).start()
            path.chmod(0o600)
            inode = path.lstat().st_ino
            print('Serving private Unix socket; HTTPS proxy is separately user-managed.', flush=True)
            await stopped.wait()
        finally:
            await runner.cleanup()
            if inode is not None and path.exists() and path.lstat().st_ino == inode:
                path.unlink()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
            os.umask(previous)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.data_dir is None:
            try:
                from plugins.plugin_storage import plugin_data_dir
            except ImportError:
                raise ValueError('Standalone usage requires --data-dir') from None
            args.data_dir = plugin_data_dir('computer-view')
            # Official helper creates a directory using the user's umask.
            if any(p.is_symlink() for p in (args.data_dir, *args.data_dir.parents)):
                raise ValueError('Symlink data directory refused')
            if args.data_dir.stat().st_uid == os.getuid():
                args.data_dir.chmod(0o700)
        root = private_dir(args.data_dir, create=args.command == 'setup')
        if args.command == 'setup':
            settings = Settings.from_dict(dict(owner_id=args.owner_id, profile=args.profile,
                owner_uid=os.getuid(), origin=args.origin, rfb_socket=args.rfb_socket,
                novnc_dir=args.novnc_dir))
            target = root / 'settings.json'
            if target.is_symlink():
                raise ValueError('Symlink settings refused')
            if target.exists() and not args.force:
                raise FileExistsError('Settings exist; use --force explicitly')
            if target.exists():
                Settings.load(target)
            fd, name = tempfile.mkstemp(prefix='.settings-', dir=root)
            try:
                with os.fdopen(fd, 'w') as output:
                    json.dump(settings.as_dict(), output)
                    output.flush()
                    os.fsync(output.fileno())
                if args.force:
                    os.replace(name, target)
                else:
                    os.link(name, target)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            Controller(root).initialize()
            Store(root / 'access.sqlite3')
            print('Settings initialized; no service or tunnel started.')
        else:
            settings = Settings.load(root / 'settings.json')
            if args.command == 'serve':
                asyncio.run(serve(root, settings))
            elif args.command == 'doctor':
                doctor(settings)
                print('Local checks passed. HTTPS proxy/tunnel must be configured separately; not tested.')
            elif args.command == 'assets':
                from .assets import install_novnc
                print(install_novnc(settings.novnc_dir))
            elif args.command == 'status':
                state = Controller(root).status()
                print(json.dumps({key:state[key] for key in ('holder', 'needs_observation', 'epoch')}))
            else:
                require_local_owner(args, settings)
                if args.command == 'link':
                    from urllib.parse import urlsplit
                    token = Store(root / 'access.sqlite3').mint(settings.owner_id, urlsplit(settings.origin).netloc)
                    print(settings.origin + '/open#' + token)
                elif args.command == 'revoke':
                    Store(root / 'access.sqlite3').revoke(settings.owner_id)
                    print('Owner links and sessions revoked.')
                elif args.command == 'recover':
                    Controller(root).recover()
                    print('Control recovered; fresh observation required.')
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f'computer-view: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
