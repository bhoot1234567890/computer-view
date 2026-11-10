"""Private tool runtime; dependencies resolved per call, never cached across profiles."""
import json
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Telegram redirect refused')


def telegram_request(token, method, payload):
    if method not in {'getChat', 'sendMessage'}:
        raise ValueError('Unsupported Telegram operation')
    request = Request('https://api.telegram.org/bot' + token + '/' + method,
                      data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json'}, method='POST')
    with build_opener(_NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('Telegram response too large')
    return json.loads(raw)


def _data_dir():
    from plugins.plugin_storage import plugin_data_dir
    return plugin_data_dir('computer-view')


def _settings_load(path):
    from .config import Settings
    return Settings.load(path)


def _read(name, default=''):
    from gateway.session_context import get_session_env
    return get_session_env(name, default)


def _secret(name):
    from agent.secret_scope import get_secret
    return get_secret(name)


def _execution_mode():
    from hermes_cli.config import load_config_readonly
    # Require explicit operator selection; never silently alter isolation settings.
    return (load_config_readonly() or {}).get('plugins', {}).get('isolation')


def _controller(data_dir):
    from .control import Controller
    return Controller(data_dir)


class Runtime:
    def __init__(self, ctx, *, data_dir=_data_dir, settings_load=_settings_load,
                 read=_read, secret=_secret, telegram=telegram_request, controller_factory=_controller,
                 execution_mode=_execution_mode):
        self.ctx = ctx
        self.data_dir = data_dir
        self.settings_load = settings_load
        self.read = read
        self.secret = secret
        self.telegram = telegram
        self.controller_factory = controller_factory
        self.execution_mode = execution_mode

    def execute(self, tool_name, args, next_call, **kwargs):
        if tool_name not in {'browser_exec', 'computer_use', 'browser_vault'}:
            return next_call(args)
        if not callable(next_call) or self.execution_mode() != 'in_process':
            return json.dumps({'ok': False, 'error': 'computer_view_requires_in_process'})
        data_dir = self.data_dir()
        self.settings_load(data_dir / 'settings.json')
        return self.controller_factory(data_dir).execute(tool_name, args, next_call, **kwargs)

    def call(self, action, args, **context):
        if not isinstance(args, dict) or args:
            return json.dumps({'ok': False, 'error': 'invalid_arguments'})
        from .auth import authorize
        from .store import Store
        store = None
        delivered = False
        try:
            data_dir = self.data_dir()
            settings = self.settings_load(data_dir / 'settings.json')
            owner_id = authorize(self.read, owner_id=settings.owner_id,
                                 profile=settings.profile, owner_uid=settings.owner_uid)
            owner = owner_id  # Database is already profile-local; matches gateway owner binding.
            if action in {'handoff', 'observe'} and self.execution_mode() != 'in_process':
                return json.dumps({'ok': False, 'error': 'computer_view_requires_in_process'})
            if action == 'revoke':
                Store(data_dir / 'access.sqlite3').revoke(owner)
                return json.dumps({'ok': True, 'status': 'revoked'})
            if action == 'open':
                token = self.secret('TELEGRAM_BOT_TOKEN')
                if not token:
                    raise ValueError('missing credential')
                result = self.telegram(token, 'getChat', {'chat_id': owner_id})
                if (result.get('ok') is not True or type(result['result']['id']) is not int or
                        result['result']['id'] != int(owner_id) or result['result']['type'] != 'private'):
                    raise ValueError('private chat verification failed')
                origin = settings.origin
                host = urlsplit(origin).netloc
                store = Store(data_dir / 'access.sqlite3')
                bearer = store.mint(owner, host)
                result = self.telegram(token, 'sendMessage', {
                    'chat_id': owner_id, 'text': 'Open Computer View. This private link expires shortly.',
                    'protect_content': True,
                    'reply_markup': {'inline_keyboard': [[{'text': 'Open Computer View',
                                                          'url': origin + '/#token=' + bearer}]]},
                })
                if (result.get('ok') is not True or
                        type(result['result']['message_id']) is not int or result['result']['message_id'] <= 0 or
                        type(result['result']['chat']['id']) is not int or
                        result['result']['chat']['id'] != int(owner_id) or
                        result['result']['chat']['type'] != 'private'):
                    raise ValueError('invalid delivery receipt')
                delivered = True
                return json.dumps({'ok': True, 'status': 'delivered', 'message_id': result['result']['message_id']})
            if action == 'handoff':
                self.controller_factory(data_dir).request_handoff()
                return json.dumps({'ok': True, 'status': 'handoff_requested'})
            if action == 'observe':
                dispatch_context = {key: context[key] for key in ('task_id', 'session_id') if key in context}
                return self.controller_factory(data_dir).observe(lambda: self.ctx.dispatch_tool(
                    'browser_exec', {'code': '# Observe Computer View after handback\nprint(page_info()); print(capture_screenshot())'},
                    **dispatch_context))
            if action == 'status':
                state = self.controller_factory(data_dir).status()
                if state['holder'] not in ('agent', 'human') or type(state['needs_observation']) is not bool:
                    raise ValueError('Invalid controller status')
                return json.dumps({'ok': True, 'holder': state['holder'],
                                   'needs_observation': state['needs_observation']})
            raise ValueError('unknown action')
        except PermissionError:
            return json.dumps({'ok': False, 'error': 'owner_private_session_required'})
        except Exception:
            # Never serialize exception text: Telegram errors may contain the bot URL/token,
            # response bodies may echo the bearer button, config paths may contain private data.
            return json.dumps({'ok': False, 'error': 'computer_view_unavailable'})
        finally:
            if store is not None and not delivered:
                try:
                    store.revoke(owner)
                except Exception:
                    pass  # Cleanup must not expose a credential-bearing exception.
