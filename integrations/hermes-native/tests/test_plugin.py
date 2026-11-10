"""Public plugin API contract tests; fake ctx, no Hermes host installation."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import pytest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    name = 'computer_view_plugin_test'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'plugin.py', submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PublicContext:
    """Only documented signatures from hermes_cli/plugins.py + plugins_content.py."""
    def __init__(self):
        self.tools = {}
        self.middleware = {}
        self.skills = {}
        self.cli_commands = {}
        self.calls = []

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None,
                      is_async=False, description='', emoji='', override=False):
        assert not override
        self.tools[name] = handler
        assert schema['name'] == name
        assert schema['parameters']['additionalProperties'] is False

    def register_skill(self, name, path, description='', frontmatter=None):
        self.skills[name] = Path(path)

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=''):
        self.cli_commands[name] = (setup_fn, handler_fn)

    def register_middleware(self, kind, callback):
        self.middleware[kind] = callback

    def dispatch_tool(self, tool_name, args, **kwargs):
        self.calls.append((tool_name, args))
        self.dispatch_kwargs = kwargs
        # Authentic browser envelope, explicitly isolated fixture image data.
        import base64
        self.capture_path.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=='))
        summary = json.dumps({'success': True, 'exit_code': 0, 'output': 'explicit capture fixture',
                              'screenshot_path': str(self.capture_path)})
        self.capture_result = {'_multimodal': True, 'text_summary': summary,
            'meta': {'screenshot_path': str(self.capture_path), 'native_vision': True},
            'content': [{'type': 'text', 'text': summary}, {'type': 'image_url',
                'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(self.capture_path.read_bytes()).decode()}}]}
        return self.capture_result


def test_register_uses_only_public_surfaces_without_runtime_io():
    plugin = load_plugin()
    ctx = PublicContext()
    plugin.register(ctx)
    assert set(ctx.tools) == {'computer_view_open', 'computer_view_status', 'computer_view_revoke',
                              'computer_view_handoff', 'computer_view_observe'}
    assert set(ctx.middleware) == {'tool_execution'}
    assert ctx.skills['computer-view'].is_file()
    assert ctx.calls == []


@pytest.fixture
def runtime(tmp_path):
    plugin = load_plugin()
    module = __import__(plugin.__name__ + '.computer_view.plugin_runtime', fromlist=['Runtime'])
    ctx = PublicContext()
    identity = {'HERMES_SESSION_PLATFORM': 'telegram', 'HERMES_SESSION_PROFILE': 'default',
                'HERMES_SESSION_USER_ID': '12345', 'HERMES_SESSION_CHAT_ID': '12345',
                'HERMES_SESSION_CHAT_TYPE': 'dm'}
    ctx.capture_path = tmp_path / 'explicit-test-capture.png'
    settings = SimpleNamespace(owner_id='12345', profile='default', owner_uid=os.getuid(),
                               origin='https://viewer.example.test')
    calls = []
    def telegram(token, method, payload):
        calls.append((token, method, payload))
        if method == 'getChat':
            return {'ok': True, 'result': {'id': 12345, 'type': 'private'}}
        return {'ok': True, 'result': {'message_id': 42, 'chat': {'id': 12345, 'type': 'private'}}}
    obj = module.Runtime(ctx, data_dir=lambda: tmp_path,
                         settings_load=lambda path: settings, read=identity.get,
                         secret=lambda key: 'secret-bot-token', telegram=telegram)
    obj.execution_mode = lambda: 'in_process'
    return obj, identity, calls, tmp_path, ctx


def test_open_verifies_private_chat_delivers_button_and_returns_only_receipt(runtime):
    obj, identity, calls, path, ctx = runtime
    result = obj.call('open', {})
    assert json.loads(result) == {'ok': True, 'status': 'delivered', 'message_id': 42}
    assert [c[1] for c in calls] == ['getChat', 'sendMessage']
    assert calls[0][2] == {'chat_id': '12345'}
    payload = calls[1][2]
    assert payload['chat_id'] == '12345'
    link = payload['reply_markup']['inline_keyboard'][0][0]['url']
    assert link.startswith('https://viewer.example.test/#token=')
    token = link.split('#token=')[1]
    assert token and token not in result and 'secret-bot-token' not in result
    assert 'viewer.example.test' not in result
    assert (path / 'access.sqlite3').is_file()


def test_registered_open_runs_runtime_with_supported_handler_kwargs(runtime, monkeypatch):
    obj, identity, calls, path, ctx = runtime
    plugin = sys.modules['computer_view_plugin_test']
    module = sys.modules[plugin.__name__ + '.computer_view.plugin_runtime']
    monkeypatch.setattr(module, 'Runtime', lambda context: obj)
    plugin.register(ctx)
    result = json.loads(ctx.tools['computer_view_open']({}, parent_agent=object()))
    assert result == {'ok': True, 'status': 'delivered', 'message_id': 42}


@pytest.mark.parametrize('change', [
    {'HERMES_SESSION_USER_ID': '999'}, {'HERMES_SESSION_CHAT_ID': '-123'},
    {'HERMES_SESSION_CHAT_TYPE': 'group'}, {'HERMES_SESSION_PROFILE': 'work'},
    {'HERMES_SESSION_PROFILE': ''}, {'HERMES_SESSION_PLATFORM': ''},
    {'HERMES_SESSION_THREAD_ID': '42'},
])
@pytest.mark.parametrize('action', ['open', 'status', 'revoke', 'handoff', 'observe'])
def test_wrong_owner_group_profile_and_cli_denied_before_io(runtime, change, action):
    obj, identity, calls, path, ctx = runtime
    identity.update(change)
    result = json.loads(obj.call(action, {}))
    assert result == {'ok': False, 'error': 'owner_private_session_required'}
    assert calls == [] and ctx.calls == []
    assert not (path / 'access.sqlite3').exists()


@pytest.mark.parametrize('reply', [None, [], {}, {'ok': False, 'description': 'secret-bot-token'},
    {'ok': True, 'result': {'id': 999, 'type': 'private'}},
    {'ok': True, 'result': {'id': 12345, 'type': 'group'}},
    {'ok': True, 'result': {'id': '12345', 'type': 'private'}}])
def test_getchat_must_verify_owner_and_private_type_before_mint(runtime, reply):
    obj, identity, calls, path, ctx = runtime
    obj.telegram = lambda *args: reply
    result = obj.call('open', {})
    assert json.loads(result)['ok'] is False
    assert 'secret-bot-token' not in result
    assert not (path / 'access.sqlite3').exists()


@pytest.mark.parametrize('reply', [None, [], {}, {'ok': False},
    {'ok': True, 'result': {'message_id': True, 'chat': {'id': 12345, 'type': 'private'}}},
    {'ok': True, 'result': {'message_id': 42, 'chat': {'id': 999, 'type': 'private'}}}])
def test_malformed_delivery_redacts_and_revokes_minted_capability(runtime, reply):
    import sqlite3
    obj, identity, calls, path, ctx = runtime
    original = obj.telegram
    obj.telegram = lambda token, method, payload: original(token, method, payload) if method == 'getChat' else reply
    result = obj.call('open', {})
    assert json.loads(result)['ok'] is False
    with sqlite3.connect(path / 'access.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM links').fetchone()[0] == 0


def test_network_errors_never_return_or_log_secrets_or_bearers(runtime, caplog):
    obj, identity, calls, path, ctx = runtime
    original = obj.telegram
    leaked = []
    def broken(token, method, payload):
        if method == 'getChat':
            return original(token, method, payload)
        link = payload['reply_markup']['inline_keyboard'][0][0]['url']
        leaked.extend([token, link, link.split('#token=')[1]])
        raise OSError('https://api.telegram.org/bot' + token + '/sendMessage ' + link)
    obj.telegram = broken
    result = obj.call('open', {})
    assert json.loads(result)['ok'] is False
    for value in leaked:
        assert value not in result and value not in caplog.text


def test_telegram_transport_uses_fixed_https_post_bounded_json_no_redirect(monkeypatch):
    plugin = load_plugin()
    module = __import__(plugin.__name__ + '.computer_view.plugin_runtime', fromlist=['telegram_request'])
    observed = []
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            assert limit == 65537
            return b'{"ok":true,"result":{"id":12345,"type":"private"}}'
    class Opener:
        def open(self, request, timeout):
            observed.append(request)
            assert timeout == 15
            return Reply()
    monkeypatch.setattr(module, 'build_opener', lambda *handlers: Opener())
    result = module.telegram_request('secret-bot-token', 'getChat', {'chat_id': '12345'})
    assert result['ok'] is True
    assert observed[0].full_url == 'https://api.telegram.org/botsecret-bot-token/getChat'
    assert observed[0].method == 'POST'
    assert json.loads(observed[0].data) == {'chat_id': '12345'}


def test_status_only_returns_allowlisted_private_state(runtime):
    obj, identity, calls, path, ctx = runtime
    obj.controller_factory = lambda root: SimpleNamespace(status=lambda: {
        'holder': 'human', 'needs_observation': True, 'epoch': 5,
        'reason': 'secret-bot-token', 'events': ['bearer-secret'], 'viewer_id': 'private-session'})
    assert json.loads(obj.call('status', {})) == {
        'ok': True, 'holder': 'human', 'needs_observation': True}
    assert calls == []


def test_revoke_invalidates_only_this_owners_links_and_sessions(runtime):
    obj, identity, calls, path, ctx = runtime
    module = sys.modules[type(obj).__module__]
    store_module = __import__(module.__package__ + '.store', fromlist=['Store'])
    store = store_module.Store(path / 'access.sqlite3')
    token = store.mint('12345', 'viewer.example.test')
    other = store.mint('999', 'viewer.example.test')
    assert json.loads(obj.call('revoke', {})) == {'ok': True, 'status': 'revoked'}
    assert store.redeem(token, 'viewer.example.test') is None
    assert store.redeem(other, 'viewer.example.test') is not None


def test_observe_authentic_native_envelope_through_actual_controller(runtime):
    obj, identity, calls, path, ctx = runtime
    controller = obj.controller_factory(path)
    controller.initialize(); controller.takeover('fixture-viewer'); controller.handback('fixture-viewer')
    result = obj.call('observe', {})
    assert result is ctx.capture_result
    assert not controller.status()['needs_observation']
    assert not controller.status()['uncertain']


def test_observe_native_browser_failure_through_actual_controller(runtime):
    obj, identity, calls, path, ctx = runtime
    controller = obj.controller_factory(path)
    controller.initialize(); controller.takeover('fixture-viewer'); controller.handback('fixture-viewer')
    dispatch = ctx.dispatch_tool
    def failure(*args, **kwargs):
        result = dispatch(*args, **kwargs)
        summary = json.loads(result['text_summary'])
        summary.update(success=False, exit_code=1)
        result['text_summary'] = json.dumps(summary)
        return result
    ctx.dispatch_tool = failure
    assert obj.call('observe', {}) is ctx.capture_result
    assert controller.status()['needs_observation'] and controller.status()['uncertain']


def test_observe_returns_actual_screenshot_dispatch_result_with_fixed_code(runtime):
    obj, identity, calls, path, ctx = runtime
    observed = []
    class Control:
        def observe(self, capture):
            observed.append('locked')
            return capture()
    obj.controller_factory = lambda root: Control()
    result = obj.call('observe', {})
    assert result is ctx.capture_result
    assert observed == ['locked']
    assert ctx.calls == [('browser_exec', {'code': '# Observe Computer View after handback\nprint(page_info()); print(capture_screenshot())'})]


@pytest.mark.parametrize('name', ['browser_exec', 'computer_use', 'browser_vault'])
def test_registered_middleware_delegates_guarded_reads_with_exact_public_kwargs(runtime, monkeypatch, name):
    obj, identity, calls, path, ctx = runtime
    plugin = sys.modules['computer_view_plugin_test']
    module = sys.modules[type(obj).__module__]
    observed = []
    class Control:
        def execute(self, tool_name, args, next_call, **kwargs):
            observed.append((tool_name, args, kwargs))
            return '{"error":"Human has control"}'
    obj.controller_factory = lambda root: Control()
    monkeypatch.setattr(module, 'Runtime', lambda context: obj)
    plugin.register(ctx)
    callback = ctx.middleware['tool_execution']
    result = callback(tool_name=name, args={'read': True}, original_args={'read': True},
                      next_call=lambda args: pytest.fail('must not dispatch while human owns control'), session_id='s1')
    assert json.loads(result)['error'] == 'Human has control'
    assert observed == [(name, {'read': True}, {'original_args': {'read': True}, 'session_id': 's1'})]


@pytest.mark.parametrize('failure', ['data_dir', 'settings_load', 'controller_factory', 'runtime'])
def test_middleware_acquisition_failure_is_explicit_redacted_error_not_skip(runtime, monkeypatch, failure):
    obj, identity, calls, path, ctx = runtime
    plugin = sys.modules['computer_view_plugin_test']
    module = sys.modules[type(obj).__module__]
    def broken(*args):
        raise RuntimeError('secret-bot-token bearer-secret')
    if failure == 'runtime':
        monkeypatch.setattr(module, 'Runtime', broken)
    else:
        setattr(obj, failure, broken)
        monkeypatch.setattr(module, 'Runtime', lambda context: obj)
    plugin.register(ctx)
    result = ctx.middleware['tool_execution'](tool_name='browser_exec', args={},
        next_call=lambda args: pytest.fail('failure must not skip admission'))
    assert json.loads(result)['error'] == 'computer_view_guard_unavailable'
    assert 'secret-bot-token' not in result and 'bearer-secret' not in result


def test_unrelated_tool_bypasses_fence_without_acquiring_runtime(monkeypatch):
    plugin = load_plugin()
    ctx = PublicContext()
    plugin.register(ctx)
    assert ctx.middleware['tool_execution'](tool_name='terminal', args={'command': 'true'},
            next_call=lambda args: args) == {'command': 'true'}


def test_handoff_requests_user_takeover_without_granting_agent_control(runtime):
    obj, identity, calls, path, ctx = runtime
    requested = []
    obj.controller_factory = lambda root: SimpleNamespace(request_handoff=lambda: requested.append('request'))
    result = obj.call('handoff', {})
    assert requested == ['request']
    assert json.loads(result) == {'ok': True, 'status': 'handoff_requested'}


@pytest.mark.parametrize('action', ['open', 'status', 'revoke', 'handoff', 'observe'])
def test_tool_arguments_cannot_override_identity_target_or_observation_code(runtime, action):
    obj, identity, calls, path, ctx = runtime
    result = obj.call(action, {'owner_id': '999', 'code': 'do_evil()'})
    assert json.loads(result) == {'ok': False, 'error': 'invalid_arguments'}
    assert calls == [] and ctx.calls == []


def test_manifest_identifies_tools_and_discloses_actual_risks_without_fake_capabilities():
    import yaml
    manifest = yaml.safe_load((ROOT / 'plugin.yaml').read_text())
    assert manifest['name'] == 'computer-view'
    assert manifest['label'] == 'Computer View'
    assert manifest['author'] == 'Computer View contributors'
    assert manifest['kind'] == 'standalone'
    assert manifest['requires_hermes'] == '>=0.22.6'
    assert manifest['provides_middleware'] == ['tool_execution']
    assert manifest['capabilities'] == []
    assert 'repository' not in manifest and 'homepage' not in manifest
    assert set(manifest['provides_tools']) == {'computer_view_' + action for action in
                                              ('open', 'status', 'revoke', 'handoff', 'observe')}
    for disclosure in ['api.telegram.org', 'plugin-data/computer-view', 'noVNC', 'process', 'local']:
        assert disclosure in manifest['description']


def test_tool_wrapper_redacts_runtime_setup_failures(runtime, monkeypatch):
    obj, identity, calls, path, ctx = runtime
    plugin = sys.modules['computer_view_plugin_test']
    module = sys.modules[type(obj).__module__]
    def broken(context):
        raise RuntimeError('secret-bot-token bearer-secret')
    monkeypatch.setattr(module, 'Runtime', broken)
    plugin.register(ctx)
    result = ctx.tools['computer_view_open']({})
    assert json.loads(result) == {'ok': False, 'error': 'computer_view_unavailable'}
    assert 'secret-bot-token' not in result and 'bearer-secret' not in result


def test_runtime_resolves_data_settings_and_secrets_fresh_for_each_profile(runtime, tmp_path):
    obj, identity, calls, path, ctx = runtime
    active = {'profile': 'default'}
    loaded = []
    secret_reads = []
    def data_dir():
        return tmp_path / active['profile']
    def settings_load(settings_path):
        loaded.append(settings_path)
        return SimpleNamespace(owner_id='12345', profile=active['profile'], owner_uid=os.getuid(),
                               origin='https://viewer.example.test')
    def secret(key):
        secret_reads.append((key, active['profile']))
        return 'token-' + active['profile']
    obj.data_dir = data_dir
    obj.settings_load = settings_load
    obj.secret = secret
    assert json.loads(obj.call('open', {}))['ok'] is True
    active['profile'] = 'work'
    identity['HERMES_SESSION_PROFILE'] = 'work'
    assert json.loads(obj.call('open', {}))['ok'] is True
    assert secret_reads == [('TELEGRAM_BOT_TOKEN', 'default'), ('TELEGRAM_BOT_TOKEN', 'work')]
    assert loaded == [tmp_path / 'default' / 'settings.json', tmp_path / 'work' / 'settings.json']
    assert [c[0] for c in calls] == ['token-default', 'token-default', 'token-work', 'token-work']
    module = sys.modules[type(obj).__module__]
    Store = __import__(module.__package__ + '.store', fromlist=['Store']).Store
    first = calls[1][2]['reply_markup']['inline_keyboard'][0][0]['url'].split('#token=')[1]
    work_store = Store(tmp_path / 'work' / 'access.sqlite3')
    assert work_store.redeem(first, 'viewer.example.test') is None


def test_observe_forwards_current_browser_task_and_session_ids(runtime, monkeypatch):
    obj, identity, calls, path, ctx = runtime
    plugin = sys.modules['computer_view_plugin_test']
    module = sys.modules[type(obj).__module__]
    obj.controller_factory = lambda root: SimpleNamespace(observe=lambda capture: capture())
    monkeypatch.setattr(module, 'Runtime', lambda context: obj)
    plugin.register(ctx)
    result = ctx.tools['computer_view_observe']({}, task_id='browser-task-1', session_id='session-1', parent_agent=object())
    assert result is ctx.capture_result
    assert ctx.dispatch_kwargs == {'task_id': 'browser-task-1', 'session_id': 'session-1'}


@pytest.mark.parametrize('mode', ['host', None, '', 'unknown'])
def test_middleware_requires_explicit_supported_in_process_mode(runtime, mode):
    obj, identity, calls, path, ctx = runtime
    obj.execution_mode = lambda: mode
    obj.controller_factory = lambda root: SimpleNamespace(execute=lambda name,args,next_call,**kw: next_call(args))
    result = obj.execute('browser_exec', {}, lambda args: '{"did_execute":true}')
    assert json.loads(result) == {'ok': False, 'error': 'computer_view_requires_in_process'}


def test_noncallable_host_next_call_is_denied_without_controller_access(runtime):
    obj, identity, calls, path, ctx = runtime
    obj.controller_factory = lambda root: pytest.fail('noncallable next_call must be rejected first')
    result = obj.execute('computer_use', {}, object())
    assert json.loads(result) == {'ok': False, 'error': 'computer_view_requires_in_process'}


@pytest.mark.parametrize('action', ['handoff', 'observe'])
def test_control_tools_reject_unsupported_mode_before_controller(runtime, action):
    obj, identity, calls, path, ctx = runtime
    obj.execution_mode = lambda: 'host'
    obj.controller_factory = lambda root: pytest.fail('unsupported mode must not touch control')
    result = obj.call(action, {})
    assert json.loads(result) == {'ok': False, 'error': 'computer_view_requires_in_process'}


def test_native_directory_entrypoint_exports_registration():
    spec = importlib.util.spec_from_file_location('computer_view_native_entry', ROOT / '__init__.py',
                                                   submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    ctx = PublicContext()
    module.register(ctx)
    assert 'computer_view_open' in ctx.tools


@pytest.mark.parametrize('argv,expected', [
    (['status'], ['status']),
    (['setup', '--owner-id', '12345'], ['setup', '--owner-id', '12345']),
    (['--', '--data-dir', '/tmp/private-cv', 'doctor'], ['--data-dir', '/tmp/private-cv', 'doctor']),
])
def test_native_cli_forwards_to_existing_parser_without_rebuilding_it(monkeypatch, argv, expected):
    import argparse
    plugin = load_plugin()
    cli_module = __import__(plugin.__name__ + '.computer_view.cli', fromlist=['main'])
    calls = []
    monkeypatch.setattr(cli_module, 'main', lambda args: calls.append(args) or 17)
    ctx = PublicContext()
    plugin.register(ctx)
    setup, handler = ctx.cli_commands['computer-view']
    parser = argparse.ArgumentParser()
    setup(parser)
    assert handler(parser.parse_args(argv)) == 17
    assert calls == [expected]


@pytest.mark.parametrize('stage', ['getChat', 'sendMessage'])
def test_boolean_telegram_chat_id_is_not_accepted_as_numeric_owner(runtime, stage):
    obj, identity, calls, path, ctx = runtime
    settings = obj.settings_load(path / 'settings.json')
    settings.owner_id = '1'
    identity.update(HERMES_SESSION_USER_ID='1', HERMES_SESSION_CHAT_ID='1')
    obj.telegram = lambda token, method, payload: {'ok': True, 'result':
        {'id': True if stage == 'getChat' else 1, 'type': 'private'} if method == 'getChat' else
        {'message_id': 42, 'chat': {'id': True, 'type': 'private'}}}
    assert json.loads(obj.call('open', {}))['ok'] is False
    if stage == 'getChat':
        assert not (path / 'access.sqlite3').exists()
