import hashlib
import importlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('openssl') and shutil.which('certutil'), 'openssl and certutil required')
class BrowserTrustTests(unittest.TestCase):
    def test_explicitly_pinned_ca_import_is_opt_in_and_idempotent(self):
        self.assertIsNotNone(importlib.util.find_spec('computer_view.browser_trust'),
                             'Missing opt-in Chromium CA installer')
        trust = importlib.import_module('computer_view.browser_trust')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ca, key = base / 'ca.pem', base / 'ca.key'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', str(key), '-out', str(ca), '-days', '1',
                            '-subj', '/CN=Computer View test CA', '-addext', 'basicConstraints=critical,CA:TRUE'],
                           check=True, capture_output=True)
            fingerprint = hashlib.sha256(ca.read_bytes()).hexdigest()
            db = base / 'nssdb'
            result = trust.configure(ca, fingerprint, db)
            self.assertEqual(result['status'], 'checked')
            self.assertFalse(db.exists(), 'Dry run must not modify trust')
            for _ in range(2):
                self.assertEqual(trust.configure(ca, fingerprint, db, apply=True)['status'], 'installed')
            output = subprocess.run(['certutil', '-L', '-d', 'sql:' + str(db)],
                                    check=True, capture_output=True, text=True).stdout
            self.assertIn('Computer View Proxy CA', output)
            self.assertIn('C,,', output)
            self.assertEqual(db.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(ValueError):
                trust.configure(ca, '0' * 64, base / 'wrong', apply=True)
            with self.assertRaises(ValueError):
                trust.configure(key, hashlib.sha256(key.read_bytes()).hexdigest(), base / 'keydb', apply=True)
            self.assertFalse((base / 'wrong').exists())
            self.assertFalse((base / 'keydb').exists())


if __name__ == '__main__':
    unittest.main()
