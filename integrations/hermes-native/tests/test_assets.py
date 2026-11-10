import hashlib
import io
import tarfile
from pathlib import Path
import pytest


def test_hash_mismatch_never_creates_destination(tmp_path):
    from computer_view.assets import install_novnc
    destination = tmp_path / 'novnc'
    with pytest.raises(ValueError, match='SHA256'):
        install_novnc(destination, b'not a release')
    assert not destination.exists()


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        for name, kind in entries:
            member = tarfile.TarInfo(name)
            if kind == 'link':
                member.type = tarfile.SYMTYPE
                member.linkname = '/tmp/outside'
            else:
                member.size = 1
            tar.addfile(member, io.BytesIO(b'x') if kind != 'link' else None)
    return output.getvalue()


def test_extract_allowlist_and_preserve_license(tmp_path, monkeypatch):
    from computer_view import assets
    data = archive([('root/core/rfb.js','file'), ('root/LICENSE.txt','file'),
                    ('root/vendor/lib.js','file'), ('root/utils/launch.sh','file')])
    monkeypatch.setattr(assets, 'SHA256', hashlib.sha256(data).hexdigest())
    target = assets.install_novnc(tmp_path / 'assets', data)
    assert (target / 'core/rfb.js').read_text() == 'x'
    assert (target / 'LICENSE.txt').is_file()
    assert not (target / 'utils').exists()
    with pytest.raises(FileExistsError):
        assets.install_novnc(target, data)


@pytest.mark.parametrize('name,kind', [('root/../escape','file'), ('/absolute','file'),
    ('root/core/link','link'), ('root/utils/link','link'), ('other/core/file','file')])
def test_malicious_members_fail_closed(tmp_path, monkeypatch, name, kind):
    from computer_view import assets
    data = archive([('root/core/rfb.js','file'), ('root/LICENSE.txt','file'), (name,kind)])
    monkeypatch.setattr(assets, 'SHA256', hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError):
        assets.install_novnc(tmp_path / 'novnc', data)
    assert not (tmp_path / 'novnc').exists()


def test_symlink_parent_refused(tmp_path, monkeypatch):
    from computer_view import assets
    data = archive([('root/core/rfb.js','file'), ('root/LICENSE.txt','file')])
    monkeypatch.setattr(assets, 'SHA256', hashlib.sha256(data).hexdigest())
    real = tmp_path / 'real'
    real.mkdir()
    (tmp_path / 'link').symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match='Symlink'):
        assets.install_novnc(tmp_path / 'link/novnc', data)
    assert not (real / 'novnc').exists()


def test_archive_size_limits(tmp_path, monkeypatch):
    from computer_view import assets
    monkeypatch.setattr(assets, 'MAX_ARCHIVE', 1)
    with pytest.raises(ValueError, match='size'):
        assets.install_novnc(tmp_path / 'novnc', b'xx')


def test_expanded_size_limit(tmp_path, monkeypatch):
    from computer_view import assets
    data = archive([('root/core/rfb.js','file'), ('root/LICENSE.txt','file')])
    monkeypatch.setattr(assets, 'SHA256', hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(assets, 'MAX_EXPANDED', 1)
    with pytest.raises(ValueError, match='size'):
        assets.install_novnc(tmp_path / 'novnc', data)
