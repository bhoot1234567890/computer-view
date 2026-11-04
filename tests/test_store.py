from computer_view.store import Store
import tempfile
import unittest
from pathlib import Path


class StoreTests(unittest.TestCase):
    def test_link_is_redeemed_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'access.sqlite3')
            token = store.mint('owner', 'view.example', now=1000)
            session = store.redeem(token, 'view.example', now=1001)
            self.assertIsNotNone(session)
            self.assertIsNone(store.redeem(token, 'view.example', now=1002))
            self.assertTrue(store.valid(session, 'view.example', now=1002))


    def load_store(self, directory):
        return Store(Path(directory) / 'access.sqlite3')

    def test_revoke_ends_sessions_and_unused_links(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.load_store(directory)
            used = store.mint('owner', 'view.example', now=1000)
            session = store.redeem(used, 'view.example', now=1001)
            unused = store.mint('owner', 'view.example', now=1002)
            store.revoke('owner')
            self.assertFalse(store.valid(session, 'view.example', now=1003))
            self.assertIsNone(store.redeem(unused, 'view.example', now=1003))

    def test_expiry_and_host_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.load_store(directory)
            token = store.mint('owner', 'view.example', now=1000)
            self.assertIsNone(store.redeem(token, 'evil.example', now=1001))
            self.assertIsNone(store.redeem(token, 'view.example', now=1300))
            token = store.mint('owner', 'view.example', now=2000)
            session = store.redeem(token, 'view.example', now=2001)
            self.assertTrue(store.valid(session, 'view.example', now=3800))
            self.assertFalse(store.valid(session, 'view.example', now=3801))
            self.assertFalse(store.valid(session, 'evil.example', now=2002))


    def test_concurrent_redemption_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            store = self.load_store(directory)
            token = store.mint('owner', 'view.example')
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: store.redeem(token, 'view.example'), range(8)))
            self.assertEqual(sum(result is not None for result in results), 1)
            raw = (Path(directory) / 'access.sqlite3').read_bytes()
            self.assertNotIn(token.encode(), raw)
            self.assertEqual((Path(directory) / 'access.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_invalid_capabilities_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.load_store(directory)
            for value in ('', 'short', '💥', None, 'a' * 10000):
                with self.subTest(value_type=type(value).__name__):
                    self.assertIsNone(store.redeem(value, 'view.example'))
                    self.assertFalse(store.valid(value, 'view.example'))


    def test_database_failure_invalidates_access(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.load_store(directory)
            token = store.mint('owner', 'view.example')
            session = store.redeem(token, 'view.example')
            store.path.rename(store.path.with_suffix('.offline'))
            self.assertFalse(store.valid(session, 'view.example'))


    def test_locked_database_fails_closed_without_blocking_session_watch(self):
        import sqlite3
        import time
        with tempfile.TemporaryDirectory() as directory:
            store = self.load_store(directory)
            token = store.mint('owner', 'view.example')
            session = store.redeem(token, 'view.example')
            locked = sqlite3.connect(store.path)
            try:
                locked.execute('BEGIN EXCLUSIVE')
                started = time.monotonic()
                self.assertFalse(store.valid(session, 'view.example'))
                self.assertLess(time.monotonic() - started, 1)
            finally:
                locked.rollback()
                locked.close()


if __name__ == '__main__':
    unittest.main()
