"""Owner-only Computer View link minting and private Telegram delivery."""
import argparse
import json
import os
import asyncio
import socket
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from computer_view.store import Store
from computer_view.config import Config, default_config_path, validate_origin
from computer_view import __version__


def authorize(environment, owner, *, uid=None, local_owner=False):
    uid = os.getuid() if uid is None else uid
    if uid != owner['unix_uid']:
        raise PermissionError('Only the Computer View owner may request access')
    if environment.get('HERMES_SESSION_PROFILE', '') not in ('', owner.get('profile', 'default')):
        raise PermissionError('Computer View belongs to a different agent profile')
    platform = environment.get('HERMES_SESSION_PLATFORM')
    if not platform:
        if local_owner and not any(k.startswith('HERMES_SESSION_') and v for k, v in environment.items()):
            return
        raise PermissionError('Missing session identity; owner-local shell use requires --local-owner')
    if platform == 'telegram':
        expected = str(owner['telegram_id'])
        if (environment.get('HERMES_SESSION_USER_ID') != expected or
                environment.get('HERMES_SESSION_CHAT_ID') != expected or
                environment.get('HERMES_SESSION_CHAT_TYPE') != 'dm' or
                environment.get('HERMES_SESSION_THREAD_ID')):
            raise PermissionError('Computer View links are restricted to the owner private Telegram chat')
    elif platform in ('local', 'cli', 'desktop', 'tui'):
        if environment.get('HERMES_SESSION_USER_ID') != 'uid:' + str(owner['unix_uid']):
            raise PermissionError('Incomplete or mismatched local session identity')
    else:
        raise PermissionError('This source may not request Computer View access')


def telegram_payload(owner, link):
    return {
        'chat_id': str(owner['telegram_id']),
        'text': 'Computer View\n\nTap Open Computer View, then confirm on the page. No username or password is needed.\n\nThis single-use link expires in 5 minutes; the session lasts 30 minutes. Do not forward it.',
        'link_preview_options': {'is_disabled': True},
        'reply_markup': {'inline_keyboard': [[{'text': 'Open Computer View', 'url': link}]]},
    }


def issue_link(base, owner, origin):
    origin = validate_origin(origin)
    parsed = urlsplit(origin)
    token = Store(Path(base) / 'access.sqlite3').mint('uid:' + str(owner['unix_uid']), parsed.netloc)
    return origin.rstrip('/') + '/open#token=' + token


def server_ready(config):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            probe.connect(str(config.http_socket))
            probe.sendall(b'GET /open HTTP/1.1\r\nHost: computer-view.invalid\r\nConnection: close\r\n\r\n')
            return b'200' in probe.recv(128).split(b'\r\n', 1)[0]
    except OSError:
        return False


def bot_token(config):
    token = os.environ.get(config.telegram_token_env)
    if not token:
        raise RuntimeError('Set the configured Telegram bot token environment variable; do not put it in config.toml')
    return token


def telegram_api(token, method, payload):
    request = Request('https://api.telegram.org/bot' + token + '/' + method,
                      data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except Exception:
        raise RuntimeError('Telegram API request failed; no credentials were logged') from None
    if not result.get('ok'):
        raise RuntimeError('Telegram rejected the request')
    return result['result']


def run(argv=None):
    parser = argparse.ArgumentParser(description='Private computer access for agents and humans')
    parser.add_argument('--config', type=Path, default=default_config_path())
    parser.add_argument('--local-owner', action='store_true', help='Explicit owner shell mode; never overrides a messaging identity')
    parser.add_argument('--version', action='version', version=__version__)
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init', help='Create portable owner-only configuration')
    init.add_argument('--rfb-socket', type=Path, required=True)
    init.add_argument('--novnc-dir', type=Path)
    init.add_argument('--state-dir', type=Path)
    init.add_argument('--public-origin')
    init.add_argument('--telegram-user-id', default='')
    init.add_argument('--profile', default='default')
    for action in ('serve', 'link', 'telegram', 'revoke', 'status', 'doctor'):
        sub.add_parser(action)
    assets = sub.add_parser('assets', help='Fetch pinned, checksum-verified noVNC assets')
    assets.add_argument('operation', choices=('install',))
    tunnel = sub.add_parser('tunnel', help='Run an optional Cloudflare Quick Tunnel in the foreground')
    tunnel.add_argument('--cloudflared', default='cloudflared')
    browser_ca = sub.add_parser('browser-ca', help='Check or explicitly import a pinned public proxy CA into Chromium NSS')
    browser_ca.add_argument('--ca-file', type=Path, required=True)
    browser_ca.add_argument('--sha256', required=True, help='Trusted SHA-256 of the public PEM file bytes')
    browser_ca.add_argument('--database', type=Path, required=True, help='Actual browser user NSS database directory')
    browser_ca.add_argument('--apply', action='store_true', help='Import the checked CA; default is a read-only check')
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.action == 'browser-ca':
        from computer_view.browser_trust import configure
        print(json.dumps(configure(args.ca_file, args.sha256, args.database, apply=args.apply)))
        return 0
    if args.action == 'init':
        state = args.state_dir or Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'computer-view'
        assets_dir = args.novnc_dir or Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'computer-view/novnc'
        config = Config.create(args.config, rfb_socket=args.rfb_socket, novnc_dir=assets_dir,
                               state_dir=state, public_origin=args.public_origin,
                               telegram_user_id=args.telegram_user_id, profile=args.profile)
        print(json.dumps({'status': 'configured', 'config': str(config.path)}))
        return 0
    config = Config.load(args.config)
    if args.action == 'assets':
        from computer_view.assets import install
        install(config.novnc_dir)
        print(json.dumps({'status': 'installed', 'assets': str(config.novnc_dir)}))
        return 0
    if args.action == 'serve':
        from computer_view.gateway import serve
        asyncio.run(serve(config))
        return 0
    if args.action == 'tunnel':
        from computer_view.tunnel import run_tunnel
        return run_tunnel(config, args.cloudflared)
    if args.action == 'doctor':
        findings = {'assets': (config.novnc_dir / 'vnc.html').is_file(),
                    'rfb_socket': config.rfb_socket.is_socket(), 'http_gateway': server_ready(config)}
        try:
            config.origin()
            findings['https_origin'] = True
        except (ValueError, OSError):
            findings['https_origin'] = False
        print(json.dumps(findings))
        return 0 if all(findings.values()) else 1
    if args.action == 'status':
        print(json.dumps({'status': 'ready' if server_ready(config) else 'stopped',
                          'origin': config.origin(), 'link_seconds': 300, 'session_seconds': 1800}))
        return 0 if server_ready(config) else 1
    authorize(os.environ, config.owner, local_owner=args.local_owner)
    if args.action == 'revoke':
        Store(config.state_dir / 'access.sqlite3').revoke('uid:' + str(config.owner_uid))
        print(json.dumps({'status': 'revoked', 'detail': 'All Computer View links and sessions revoked'}))
        return 0
    if not server_ready(config):
        raise RuntimeError('Computer View is not listening: start computer-view serve')
    origin = config.origin()
    if args.action == 'link':
        print(json.dumps({'url': issue_link(config.state_dir, config.owner, origin),
                          'link_seconds': 300, 'session_seconds': 1800}))
        return 0
    if not config.telegram_user_id:
        raise ValueError('Configure telegram_user_id before Telegram delivery')
    token = bot_token(config)
    chat = telegram_api(token, 'getChat', {'chat_id': config.telegram_user_id})
    if chat.get('type') != 'private' or str(chat.get('id')) != config.telegram_user_id:
        raise PermissionError('Computer View delivery must be a verified private owner chat')
    link = issue_link(config.state_dir, config.owner, origin)
    message = telegram_api(token, 'sendMessage', telegram_payload(config.owner, link))
    if (str(message.get('chat', {}).get('id')) != config.telegram_user_id or
            message.get('chat', {}).get('type') != 'private' or
            message.get('reply_markup', {}).get('inline_keyboard', [[{}]])[0][0].get('url') != link):
        raise RuntimeError('Telegram delivery could not be verified')
    print(json.dumps({'status': 'sent', 'chat_id': config.telegram_user_id,
                      'message_id': message['message_id'], 'link_seconds': 300, 'session_seconds': 1800}))
    return 0


def main(argv=None):
    try:
        return run(argv)
    except KeyboardInterrupt:
        return 130
    except (PermissionError, RuntimeError, OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({'status': 'error', 'error': str(error)}), file=sys.stderr)
        return 1
