from computer_view import cli
import unittest
from pathlib import Path


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.module = cli
        self.owner = {'unix_uid': 1000, 'telegram_id': '1234567'}

    def test_only_owner_private_telegram_identity_can_request_links(self):
        good = {'HERMES_SESSION_PLATFORM': 'telegram', 'HERMES_SESSION_USER_ID': '1234567',
                'HERMES_SESSION_CHAT_ID': '1234567', 'HERMES_SESSION_CHAT_TYPE': 'dm'}
        self.module.authorize(good, self.owner, uid=1000)
        for change in [
            {'HERMES_SESSION_USER_ID': '999'}, {'HERMES_SESSION_CHAT_ID': '-123'},
            {'HERMES_SESSION_CHAT_TYPE': 'group'}, {'HERMES_SESSION_PLATFORM': 'webhook'},
            {'HERMES_SESSION_PROFILE': 'other'}]:
            with self.subTest(change=change), self.assertRaises(PermissionError):
                self.module.authorize(good | change, self.owner, uid=1000)
        with self.assertRaises(PermissionError):
            self.module.authorize(good, self.owner, uid=999)
        with self.assertRaises(PermissionError):
            self.module.authorize({}, self.owner, uid=1000)
        self.module.authorize({}, self.owner, uid=1000, local_owner=True)
        for partial in ({'HERMES_SESSION_PLATFORM': 'local'},
                        {'HERMES_SESSION_USER_ID': '1234567'},
                        {'HERMES_SESSION_PLATFORM': 'telegram'},
                        {'HERMES_SESSION_PLATFORM': 'local', 'HERMES_SESSION_USER_ID': 'uid:999'}):
            with self.subTest(partial=partial), self.assertRaises(PermissionError):
                self.module.authorize(partial, self.owner, uid=1000, local_owner=True)

    def test_telegram_delivery_disables_previews_and_uses_fixed_private_chat(self):
        link = 'https://view.example/open#token=test-only'
        payload = self.module.telegram_payload(self.owner, link)
        self.assertEqual(payload['chat_id'], '1234567')
        self.assertTrue(payload['link_preview_options']['is_disabled'])
        self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0]['url'], link)
        self.assertNotIn(link, payload['text'])


    def test_issued_link_has_secret_only_in_fragment(self):
        import tempfile
        from urllib.parse import urlsplit, parse_qs
        from computer_view.store import Store
        with tempfile.TemporaryDirectory() as directory:
            link = self.module.issue_link(Path(directory), self.owner, 'https://view.example')
            parsed = urlsplit(link)
            self.assertEqual(parsed.path, '/open')
            self.assertEqual(parsed.query, '')
            token = parse_qs(parsed.fragment)['token'][0]
            store = Store(Path(directory) / 'access.sqlite3')
            self.assertIsNotNone(store.redeem(token, 'view.example'))
            with self.assertRaises(ValueError):
                self.module.issue_link(Path(directory), self.owner, 'http://view.example')


    def test_local_revoke_command_invalidates_existing_sessions(self):
        import tempfile
        import subprocess
        import sys
        import json
        import os
        from computer_view.store import Store
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            owner = self.owner | {'unix_uid': os.getuid()}
            from computer_view.config import Config
            config = Config.create(base / 'config.toml', rfb_socket=base / 'rfb.sock', novnc_dir=base / 'novnc', state_dir=base, telegram_user_id=owner['telegram_id'])
            store = Store(base / 'access.sqlite3')
            token = store.mint('uid:' + str(owner['unix_uid']), 'view.example')
            session = store.redeem(token, 'view.example')
            env = {k: v for k, v in os.environ.items() if not k.startswith('HERMES_SESSION_')}
            result = subprocess.run([sys.executable, '-m', 'computer_view', '--config', str(config.path), '--local-owner', 'revoke'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'revoked')
            self.assertFalse(store.valid(session, 'view.example'))


if __name__ == '__main__':
    unittest.main()
