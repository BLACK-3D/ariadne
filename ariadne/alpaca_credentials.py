"""Private data-feed credentials; no broker account or trading routes."""
from __future__ import annotations
import base64
import json
import os
import re
from .atomic import publish_bytes
from .public_credentials import _dpapi as _windows_dpapi

PRIVATE_PATH = 'alpaca-market/credential.dpapi'
MAX_BYTES = 32768


def validate(key_id, secret_key):
    if (type(key_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key_id)
            or type(secret_key) is not str or not 1 <= len(secret_key) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in secret_key)):
        raise ValueError('Alpaca data credentials are missing or invalid; use the hidden local prompt.')
    return key_id, secret_key


def _dpapi(raw, *, decrypt=False):
    try:
        return _windows_dpapi(raw, decrypt=decrypt)
    except (ValueError, OSError):
        raise ValueError('Alpaca saved credentials require Windows CurrentUser DPAPI; private session environment variables are available on other platforms.') from None


def _raw(guard):
    from scripts.options_market import read
    return read(guard, PRIVATE_PATH, MAX_BYTES)


def _envelope(raw):
    from scripts.options_market import metadata
    try:
        value = metadata(raw)
        if (type(value) is not dict or set(value) != {'schema', 'source_id', 'protection', 'blob'}
                or type(value['schema']) is not int or value['schema'] != 1
                or value['source_id'] != 'alpaca_opra_v1'
                or value['protection'] != 'windows-current-user-dpapi'
                or type(value['blob']) is not str):
            raise ValueError
        protected = base64.b64decode(value['blob'], validate=True)
        if not 1 <= len(protected) <= MAX_BYTES:
            raise ValueError
        return protected
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ValueError('Saved Alpaca credential container is invalid; inspect it privately.') from None


def configured(guard):
    try:
        raw = _raw(guard)
    except FileNotFoundError:
        return False
    _envelope(raw)
    return True


def save(guard, key_id, secret_key):
    key_id, secret_key = validate(key_id, secret_key)
    path = guard._path(PRIVATE_PATH)
    if path.exists():
        raise FileExistsError('A saved Alpaca credential already exists; deliberately revoke or rotate it before replacement.')
    raw = json.dumps({'source_id': 'alpaca_opra_v1', 'key_id': key_id,
                      'secret_key': secret_key}, separators=(',', ':')).encode()
    protected = _dpapi(raw)
    payload = json.dumps({'schema': 1, 'source_id': 'alpaca_opra_v1',
        'protection': 'windows-current-user-dpapi',
        'blob': base64.b64encode(protected).decode('ascii')}, separators=(',', ':')).encode()
    with guard._directory(path.parent, create=True):
        publish_bytes(path, payload)
        if _raw(guard) != payload:
            raise ValueError('Protected Alpaca credential readback failed.')
    return {'configured': True, 'protection': 'windows-current-user-dpapi', 'network_requests': 0}


def load(guard):
    key_id = os.environ.get('ARIADNE_ALPACA_KEY_ID')
    secret_key = os.environ.get('ARIADNE_ALPACA_SECRET_KEY')
    if key_id is not None or secret_key is not None:
        return validate(key_id, secret_key)
    protected = _envelope(_raw(guard))
    from scripts.options_market import metadata
    try:
        value = metadata(_dpapi(protected, decrypt=True))
        if (type(value) is not dict or set(value) != {'source_id', 'key_id', 'secret_key'}
                or value['source_id'] != 'alpaca_opra_v1'):
            raise ValueError
        return validate(value['key_id'], value['secret_key'])
    except (ValueError, KeyError, UnicodeError, RecursionError, TypeError):
        raise ValueError('Saved Alpaca credential could not be unlocked for this Windows user.') from None
