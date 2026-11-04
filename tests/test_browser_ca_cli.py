import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from computer_view.cli import main


@unittest.skipUnless(shutil.which('openssl'), 'openssl required')
class BrowserCACommandTests(unittest.TestCase):
    def test_browser_ca_check_needs_no_gateway_config_and_does_not_import(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ca = base / 'public.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', str(base / 'private.key'), '-out', str(ca), '-days', '1',
                            '-subj', '/CN=CLI test CA', '-addext', 'basicConstraints=critical,CA:TRUE'],
                           check=True, capture_output=True)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = main(['--config', str(base / 'missing.toml'), 'browser-ca',
                                 '--ca-file', str(ca), '--sha256', hashlib.sha256(ca.read_bytes()).hexdigest(),
                                 '--database', str(base / 'nssdb')])
                except SystemExit as error:
                    self.fail('browser-ca command is not registered: ' + str(error))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'checked')
            self.assertFalse((base / 'nssdb').exists())


if __name__ == '__main__':
    unittest.main()
