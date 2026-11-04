import importlib.util
import tempfile
import unittest
from pathlib import Path


class ConfigurationTests(unittest.TestCase):
    def test_config_is_portable_and_stores_no_bot_secret(self):
        spec = importlib.util.find_spec('computer_view.config')
        self.assertIsNotNone(spec, 'Portable configuration is not implemented')
        from computer_view.config import Config
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = Config.create(base / 'config.toml', rfb_socket=base / 'rfb.sock',
                                   novnc_dir=base / 'novnc', state_dir=base / 'state',
                                   public_origin='https://view.example', telegram_user_id='1234567')
            loaded = Config.load(config.path)
            self.assertEqual(loaded.rfb_socket, base / 'rfb.sock')
            self.assertEqual(loaded.origin(), 'https://view.example')
            self.assertEqual(loaded.telegram_user_id, '1234567')
            self.assertNotIn('TELEGRAM_BOT_TOKEN=', config.path.read_text())
            self.assertEqual(config.path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                Config.create(config.path, rfb_socket=base / 'other', novnc_dir=base / 'novnc', state_dir=base / 'state')

    def test_unsafe_public_origin_is_rejected(self):
        from computer_view.config import validate_origin
        for value in ('http://example.com', 'https://user:pass@example.com', 'https://example.com/path', 'https://example.com?token=secret', 'https://example.com#secret', 'https://example.com;evil'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_origin(value)
        self.assertEqual(validate_origin('https://view.example/'), 'https://view.example')


if __name__ == '__main__':
    unittest.main()
