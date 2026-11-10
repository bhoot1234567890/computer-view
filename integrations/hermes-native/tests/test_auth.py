import os
import pytest


def test_owner_private_telegram_is_required():
    from computer_view.auth import authorize
    identity = {'HERMES_SESSION_PLATFORM': 'telegram', 'HERMES_SESSION_USER_ID': '123',
                'HERMES_SESSION_CHAT_ID': '123', 'HERMES_SESSION_CHAT_TYPE': 'dm',
                'HERMES_SESSION_PROFILE': 'research'}
    assert authorize(identity.get, owner_id='123', profile='research', owner_uid=os.getuid()) == '123'
    for change in [{'HERMES_SESSION_USER_ID':'999'}, {'HERMES_SESSION_CHAT_ID':'-123'},
                   {'HERMES_SESSION_CHAT_TYPE':'group'}, {'HERMES_SESSION_PROFILE':'default'},
                   {'HERMES_SESSION_THREAD_ID':'42'}, {'HERMES_SESSION_PLATFORM':'webhook'},
                   {'HERMES_SESSION_USER_ID':''}]:
        with pytest.raises(PermissionError):
            authorize((identity|change).get, owner_id='123', profile='research', owner_uid=os.getuid())
