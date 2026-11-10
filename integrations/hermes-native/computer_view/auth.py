"""Fail-closed authorization from Hermes's task-local session identity."""
import os
from collections.abc import Callable


def authorize(read: Callable, *, owner_id: str, profile: str, owner_uid: int) -> str:
    if os.getuid() != owner_uid or not owner_id or not str(owner_id).isdigit():
        raise PermissionError('Computer View owner is not configured for this account')
    platform = read('HERMES_SESSION_PLATFORM', '')
    if read('HERMES_SESSION_PROFILE', '') != profile:
        raise PermissionError('Computer View belongs to a different profile')
    if platform != 'telegram':
        raise PermissionError('Computer View tools require the owner private Telegram session')
    if (read('HERMES_SESSION_USER_ID', '') != owner_id or
            read('HERMES_SESSION_CHAT_ID', '') != owner_id or
            read('HERMES_SESSION_CHAT_TYPE', '') != 'dm' or
            read('HERMES_SESSION_THREAD_ID', '')):
        raise PermissionError('Computer View is restricted to the owner private Telegram chat')
    return owner_id
