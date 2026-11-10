import json
import os
from pathlib import Path
import pytest


def test_settings_require_explicit_https_origin_and_current_owner(tmp_path):
    from computer_view.config import Settings
    values={'owner_id':'123','profile':'research','owner_uid':os.getuid(),
            'origin':'https://desktop.example', 'rfb_socket':str(tmp_path/'rfb.sock'),
            'novnc_dir':str(tmp_path/'novnc')}
    p=tmp_path/'settings.json'; p.write_text(json.dumps(values)); p.chmod(0o600)
    s=Settings.load(p)
    assert s.origin=='https://desktop.example'
    assert s.rfb_socket==tmp_path/'rfb.sock'
    for change in [{'origin':'http://desktop.example'}, {'origin':'https://user:pass@desktop.example'},
                   {'origin':'https://desktop.example/path'}, {'origin':'https://desktop.example#secret'},
                   {'owner_uid':os.getuid()+1}, {'owner_id':'-123'}, {'rfb_socket':'relative'},
                   {'profile':'../../other'}]:
        p.write_text(json.dumps(values|change))
        with pytest.raises(ValueError): Settings.load(p)
