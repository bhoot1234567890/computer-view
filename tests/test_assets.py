import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as stream:
        for name, kind in entries:
            member = tarfile.TarInfo(name)
            if kind == 'link':
                member.type = tarfile.SYMTYPE
                member.linkname = '/etc/passwd'
                stream.addfile(member)
            else:
                content = b'<html>noVNC test fixture</html>'
                member.size = len(content)
                stream.addfile(member, io.BytesIO(content))
    return output.getvalue()


class AssetsTests(unittest.TestCase):
    def test_checked_archive_installs_atomically_without_overwriting(self):
        self.assertIsNotNone(importlib.util.find_spec('computer_view.assets'), 'Verified asset installer is missing')
        from computer_view.assets import unpack
        data = archive([('noVNC-test/vnc.html', 'file')])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'novnc'
            unpack(data, target, hashlib.sha256(data).hexdigest())
            self.assertTrue((target / 'vnc.html').is_file())
            with self.assertRaises(FileExistsError):
                unpack(data, target, hashlib.sha256(data).hexdigest())

    def test_checksum_traversal_and_symlinks_are_rejected(self):
        from computer_view.assets import unpack
        fixtures = [archive([('noVNC-test/vnc.html', 'file')]),
                    archive([('noVNC-test/../../escape', 'file')]),
                    archive([('noVNC-test/vnc.html', 'link')])]
        with tempfile.TemporaryDirectory() as directory:
            for index, data in enumerate(fixtures):
                expected = '0' * 64 if index == 0 else hashlib.sha256(data).hexdigest()
                target = Path(directory) / str(index)
                with self.assertRaises(ValueError):
                    unpack(data, target, expected)
                self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
