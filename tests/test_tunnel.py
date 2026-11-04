import importlib.util
from pathlib import Path
import tempfile
import unittest
from computer_view.config import Config


class TunnelTests(unittest.TestCase):
    def test_only_quick_tunnel_origins_are_saved_owner_only(self):
        self.assertIsNotNone(importlib.util.find_spec('computer_view.tunnel'), 'Portable tunnel adapter is missing')
        from computer_view.tunnel import capture_origin
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = Config.create(base / 'config.toml', rfb_socket=base / 'rfb.sock',
                                   novnc_dir=base / 'novnc', state_dir=base / 'state')
            self.assertIsNone(capture_origin('https://example.com/other', config))
            origin = capture_origin('INF | https://test-fixture.trycloudflare.com |', config)
            self.assertEqual(origin, 'https://test-fixture.trycloudflare.com')
            self.assertEqual(config.origin(), origin)
            self.assertEqual((config.state_dir / 'public-url.txt').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
