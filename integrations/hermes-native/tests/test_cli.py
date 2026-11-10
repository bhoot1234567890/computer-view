import json
import os
from pathlib import Path
import socket
import pytest


def setup_args(root):
    return ['--data-dir', str(root), 'setup', '--owner-id', '123', '--profile', 'default',
            '--origin', 'https://view.example', '--rfb-socket', str(root / 'rfb.sock'),
            '--novnc-dir', str(root / 'novnc')]


def test_setup_private_and_preserves_human_state(tmp_path):
    from computer_view.cli import main
    from computer_view.control import Controller
    root = tmp_path / 'data'
    assert main(setup_args(root)) == 0
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / 'settings.json').stat().st_mode & 0o777 == 0o600
    Controller(root).takeover('viewer')
    assert main(setup_args(root)) == 1
    assert main(setup_args(root) + ['--force']) == 0
    assert Controller(root).status()['holder'] == 'human'


def test_owner_local_link_status_revoke_recover(tmp_path, capsys, monkeypatch):
    from computer_view.cli import main
    from computer_view.control import Controller
    from computer_view.store import Store
    for key in list(os.environ):
        if key.startswith('HERMES_SESSION_'):
            monkeypatch.delenv(key)
    root = tmp_path / 'data'
    assert main(setup_args(root)) == 0
    capsys.readouterr()
    assert main(['--data-dir', str(root), 'link']) == 1
    assert main(['--data-dir', str(root), 'link', '--local-owner']) == 0
    link = capsys.readouterr().out.strip()
    assert link.startswith('https://view.example/open#')
    token = link.split('#', 1)[1]
    assert main(['--data-dir', str(root), 'status']) == 0
    assert token not in capsys.readouterr().out
    assert main(['--data-dir', str(root), 'revoke', '--local-owner']) == 0
    assert Store(root / 'access.sqlite3').redeem(token, 'view.example') is None
    Controller(root).takeover('viewer')
    assert main(['--data-dir', str(root), 'recover', '--local-owner']) == 0
    assert Controller(root).status()['holder'] == 'agent'


@pytest.mark.parametrize('key', ['HERMES_SESSION_PLATFORM', 'HERMES_SESSION_USER_ID',
    'HERMES_SESSION_CHAT_ID', 'HERMES_SESSION_CHAT_TYPE', 'HERMES_SESSION_THREAD_ID',
    'HERMES_SESSION_SOURCE', 'HERMES_SESSION_USER_ID_ALT', 'HERMES_SESSION_USER_NAME',
    'HERMES_SESSION_CHAT_NAME', 'HERMES_SESSION_SCOPE_ID', 'HERMES_SESSION_MESSAGE_ID',
    'HERMES_SESSION_PARENT_CHAT_ID'])
def test_local_owner_never_overrides_messaging_identity(tmp_path, monkeypatch, key):
    from computer_view.cli import main
    root = tmp_path / 'data'
    assert main(setup_args(root)) == 0
    monkeypatch.setenv(key, 'telegram')
    assert main(['--data-dir', str(root), 'link', '--local-owner']) == 1


def test_doctor_requires_assets_owned_unix_rfb_and_profile(tmp_path, monkeypatch):
    from computer_view.cli import main
    root = tmp_path / 'data'
    assert main(setup_args(root)) == 0
    assert main(['--data-dir', str(root), 'doctor']) == 1
    (root / 'novnc/core').mkdir(parents=True)
    (root / 'novnc/core/rfb.js').write_text('// source')
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(root / 'rfb.sock'))
        assert main(['--data-dir', str(root), 'doctor']) == 0
        monkeypatch.setenv('HERMES_SESSION_PROFILE', 'other')
        assert main(['--data-dir', str(root), 'doctor']) == 1


def test_assets_command_explicitly_calls_pinned_installer(tmp_path, monkeypatch):
    from computer_view.cli import main
    from computer_view import assets
    root = tmp_path / 'data'
    assert main(setup_args(root)) == 0
    called = []
    monkeypatch.setattr(assets, 'install_novnc', lambda path: called.append(path) or path)
    assert main(['--data-dir', str(root), 'assets']) == 0
    assert called == [root / 'novnc']


def test_server_socket_lock_preserves_active_or_non_socket(tmp_path):
    from computer_view.cli import server_socket
    root = tmp_path / 'data'
    root.mkdir(mode=0o700)
    path = root / 'http.sock'
    path.write_text('never delete')
    with pytest.raises(ValueError):
        with server_socket(root):
            pass
    assert path.read_text() == 'never delete'
    path.unlink()
    with server_socket(root) as address:
        assert address == path
        with pytest.raises(RuntimeError):
            with server_socket(root):
                pass
    with socket.socket(socket.AF_UNIX) as stale:
        stale.bind(str(path))
    with server_socket(root):
        assert not path.exists()


def test_serve_private_unix_socket_real_http(tmp_path):
    import subprocess
    import sys
    import time
    from computer_view.cli import main
    root = tmp_path / 'd'
    assert main(setup_args(root)) == 0
    (root / 'novnc/core').mkdir(parents=True)
    (root / 'novnc/core/rfb.js').write_text('// fixture')
    with socket.socket(socket.AF_UNIX) as rfb:
        rfb.bind(str(root / 'rfb.sock'))
        proc = subprocess.Popen([sys.executable, '-m', 'computer_view.cli', '--data-dir', str(root), 'serve'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if (root / 'http.sock').exists() or proc.poll() is not None:
                    break
                time.sleep(.03)
            assert proc.poll() is None, proc.communicate()
            assert (root / 'http.sock').stat().st_mode & 0o777 == 0o600
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                client.connect(str(root / 'http.sock'))
                client.sendall(b'GET /open HTTP/1.1\r\nHost: view.example\r\nConnection: close\r\n\r\n')
                assert b'200 OK' in client.recv(8192)
        finally:
            proc.terminate()
            proc.communicate(timeout=5)
        assert not (root / 'http.sock').exists()


def test_distribution_metadata_pins_dependencies_and_static_assets():
    import tomllib
    data = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())
    assert data['project']['dependencies'] == ['aiohttp==3.14.5']
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    extras = data['project']['optional-dependencies']['test']
    assert len(extras) == 2
    requirements = {requirement.name.lower(): requirement for requirement in map(Requirement, extras)}
    assert set(requirements) == {'pytest', 'pyyaml'}
    pytest_requirement = requirements['pytest']
    assert pytest_requirement.specifier == SpecifierSet('>=9.1.1,<10')
    assert pytest_requirement.url is None and pytest_requirement.marker is None and not pytest_requirement.extras
    for version in ('9.1.1', '9.2.0'):
        assert pytest_requirement.specifier.contains(version)
    for version in ('9.1.0', '10.0.0', '10.1.0'):
        assert not pytest_requirement.specifier.contains(version)
    assert str(requirements['pyyaml']) == 'PyYAML==6.0.3'
    assert data['project']['scripts']['computer-view'] == 'computer_view.cli:main'
    assert data['tool']['setuptools']['package-data']['computer_view'] == ['static/*']


def test_default_storage_refuses_symlink_parent_before_chmod(tmp_path, monkeypatch):
    import sys
    import types
    from computer_view.cli import main
    real = tmp_path / 'real'
    real.mkdir()
    data = real / 'data'
    data.mkdir(mode=0o755)
    (tmp_path / 'alias').symlink_to(real, target_is_directory=True)
    plugins = types.ModuleType('plugins')
    storage = types.ModuleType('plugins.plugin_storage')
    storage.plugin_data_dir = lambda name: tmp_path / 'alias/data'
    monkeypatch.setitem(sys.modules, 'plugins', plugins)
    monkeypatch.setitem(sys.modules, 'plugins.plugin_storage', storage)
    assert main(['status']) == 1
    assert data.stat().st_mode & 0o777 == 0o755
