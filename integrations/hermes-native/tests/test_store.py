import importlib.util
import sqlite3
import pytest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def load_module():
    path = Path(__file__).parents[1] / 'computer_view' / 'store.py'
    assert path.exists(), 'capability store missing'
    spec = importlib.util.spec_from_file_location('cap_store', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_session_info_and_owner_revocation(tmp_path):
    store = load_module().Store(tmp_path / 'access.sqlite3')
    token = store.mint('profile:alice', 'host', now=100)
    session = store.redeem(token, 'host', now=101)
    info = store.session_info(session, 'host', now=102)
    assert info['owner'] == 'profile:alice'
    assert info['expires_at'] == 1901
    assert len(info['viewer_id']) == 43
    assert info['viewer_id'] not in (session, token)
    assert store.session_info(session, 'host', now=103) == info
    assert store.session_info(session, 'evil', now=103) is None
    assert store.session_info(session, 'host', now=1901) is None
    unused = store.mint('profile:alice', 'host', now=104)
    bob = store.redeem(store.mint('other:bob', 'host', now=104), 'host', now=105)
    store.revoke('profile:alice')
    assert not store.valid(session, 'host', now=106)
    assert store.redeem(unused, 'host', now=106) is None
    assert store.valid(bob, 'host', now=106)
    # The stable private viewer capability is derived, not stored in plaintext.
    assert info['viewer_id'].encode() not in store.path.read_bytes()


@pytest.mark.parametrize('bad_now', [float('nan'), float('inf'), -float('inf'), 'bad', None])
def test_untrusted_times_do_not_grant_access(tmp_path, bad_now):
    if bad_now is None:
        store = load_module().Store(tmp_path / 'db')
        session = store.redeem(store.mint('owner', 'host'), 'host')
        assert store.valid(session, 'host', now=None)
        return
    store = load_module().Store(tmp_path / 'db')
    token = store.mint('owner', 'host', now=1)
    session = store.redeem(store.mint('owner', 'host', now=1), 'host', now=2)
    assert store.redeem(token, 'host', now=bad_now) is None
    assert not store.valid(session, 'host', now=bad_now)
    with pytest.raises(ValueError):
        store.mint('owner', 'host', now=bad_now)


def test_regression_concurrent_redemption_and_expiry(tmp_path):
    store = load_module().Store(tmp_path / 'db')
    token = store.mint('owner', 'host')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.redeem(token, 'host'), range(8)))
    assert sum(value is not None for value in results) == 1
    assert store.redeem(store.mint('owner', 'host', now=1000), 'host', now=1300) is None


@pytest.mark.parametrize('bad', ['', 'short', '💥', None, 'a' * 10000, [], {}, 1])
def test_regression_malformed_capabilities(tmp_path, bad):
    store = load_module().Store(tmp_path / 'db')
    assert store.redeem(bad, 'host') is None
    assert store.session_info(bad, 'host') is None
    assert not store.valid(bad, 'host')


def test_regression_locked_database(tmp_path):
    import time
    store = load_module().Store(tmp_path / 'db')
    token = store.mint('owner', 'host')
    session = store.redeem(token, 'host')
    with sqlite3.connect(store.path) as locked:
        locked.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        assert not store.valid(session, 'host')
        assert store.redeem(token, 'host') is None
        assert time.monotonic() - started < 1
        locked.rollback()


@pytest.mark.parametrize('operation', ['redeem', 'valid', 'session_info'])
def test_database_failure_fails_closed(tmp_path, operation):
    store = load_module().Store(tmp_path / 'access.sqlite3')
    token = store.mint('owner', 'host')
    session = store.redeem(token, 'host')
    store.path.rename(store.path.with_suffix('.offline'))
    result = getattr(store, operation)(session, 'host')
    assert result is (False if operation == 'valid' else None)
    assert not store.path.exists(), 'reads must not recreate absent database'


def test_symlink_database_refused(tmp_path):
    target = tmp_path / 'target'
    target.write_text('do not touch')
    path = tmp_path / 'db'
    path.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        load_module().Store(path)
    assert target.read_text() == 'do not touch'


def test_unsafe_parent_refused(tmp_path):
    directory = tmp_path / 'unsafe'
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    with pytest.raises(PermissionError):
        load_module().Store(directory / 'db')


def test_database_replaced_by_symlink_fails_closed(tmp_path):
    store = load_module().Store(tmp_path / 'db')
    session = store.redeem(store.mint('owner', 'host'), 'host')
    backup = tmp_path / 'backup'
    store.path.rename(backup)
    store.path.symlink_to(backup)
    assert not store.valid(session, 'host')
    assert store.redeem('a' * 43, 'host') is None


def test_bounded_pruning_at_mint(tmp_path):
    store = load_module().Store(tmp_path / 'db')
    with store.connect() as db:
        db.executemany('INSERT INTO links VALUES (?,?,?,?)',
                       [(str(i), 'owner', 'host', 0) for i in range(1500)])
        db.executemany('INSERT INTO sessions VALUES (?,?,?,?)',
                       [(str(i), 'owner', 'host', 0) for i in range(1500)])
    store.mint('owner', 'host', now=1000)
    with store.connect() as db:
        for table in ('links', 'sessions'):
            remaining = db.execute(f'SELECT count(*) FROM {table} WHERE expires<=0').fetchone()[0]
            assert 0 < remaining < 1500


def test_single_use_host_bound_capability(tmp_path):
    store = load_module().Store(tmp_path / 'private' / 'access.sqlite3')
    token = store.mint('profile:owner', 'view.example', now=1000)
    assert len(token) == 43
    assert store.redeem(token, 'other.example', now=1001) is None
    session = store.redeem(token, 'view.example', now=1001)
    assert len(session) == 43
    assert store.redeem(token, 'view.example', now=1002) is None
    assert store.valid(session, 'view.example', now=1002)
    assert not store.valid(session, 'other.example', now=1002)
    assert not store.valid(session, 'view.example', now=2801)
    assert token.encode() not in store.path.read_bytes()
    assert session.encode() not in store.path.read_bytes()
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
