"""Owner-only Public credentials: Windows DPAPI at rest, no bearer-token persistence."""
from __future__ import annotations
import base64
import ctypes
from ctypes import wintypes
import json
import os
import re
from .atomic import publish_bytes
from .config import _allowed_path

PRIVATE_PATH = 'public-market/credential.dpapi'
MAX_BYTES = 32768


def validate(secret, account_id):
    if (type(secret) is not str or not 1 <= len(secret) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in secret)):
        raise ValueError('Public secret is missing or invalid; enter it only at the hidden local prompt.')
    if type(account_id) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', account_id):
        raise ValueError('Use the exact Public account ID returned by the accounts command.')
    return secret, account_id


def _dpapi(raw, *, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Saved Public credentials require Windows CurrentUser DPAPI; use private session environment variables on other platforms.')
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES:
        raise ValueError('Invalid private credential payload.')
    class Blob(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; omit LOCAL_MACHINE so another Windows user cannot decrypt it.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('Windows could not protect/unlock this credential for the current user.')
    try:
        if not 1 <= target.length <= MAX_BYTES:
            raise ValueError('Invalid protected credential size.')
        return ctypes.string_at(target.data, target.length)
    finally:
        if target.data:
            ctypes.memset(target.data, 0, target.length)
            kernel.LocalFree(target.data)
        ctypes.memset(buffer, 0, len(raw))


def _raw(guard):
    from scripts.options_market import read
    return read(guard, PRIVATE_PATH, MAX_BYTES)


def configured(guard):
    try:
        raw = _raw(guard)
    except FileNotFoundError:
        return False
    _envelope(raw)
    return True


def _envelope(raw):
    from scripts.options_market import metadata
    try:
        value = metadata(raw)
        if (type(value) is not dict or set(value) != {'schema', 'protection', 'blob'}
                or type(value['schema']) is not int or value['schema'] != 1
                or value['protection'] != 'windows-current-user-dpapi'
                or type(value['blob']) is not str):
            raise ValueError
        protected = base64.b64decode(value['blob'], validate=True)
        if not 1 <= len(protected) <= MAX_BYTES:
            raise ValueError
        return protected
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ValueError('Saved Public credential container is invalid; inspect it locally, without publishing its contents.') from None


def save(guard, secret, account_id):
    secret, account_id = validate(secret, account_id)
    path = guard._path(PRIVATE_PATH)
    if path.exists():
        raise FileExistsError('A saved Public credential already exists; it was not replaced. Revoke/rotate it deliberately.')
    raw = json.dumps({'secret': secret, 'account_id': account_id}, separators=(',', ':')).encode()
    protected = _dpapi(raw)
    payload = json.dumps({'schema': 1, 'protection': 'windows-current-user-dpapi',
                          'blob': base64.b64encode(protected).decode('ascii')}).encode()
    with guard._directory(path.parent, create=True):
        publish_bytes(path, payload)
        if _raw(guard) != payload:
            raise ValueError('Protected Public credential readback failed.')
    return {'configured': True, 'protection': 'windows-current-user-dpapi', 'network_requests': 0}


def load(guard):
    # Explicit session values are never persisted by this function. Require the pair.
    secret, account_id = os.environ.get('ARIADNE_PUBLIC_SECRET'), os.environ.get('ARIADNE_PUBLIC_ACCOUNT_ID')
    if secret is not None or account_id is not None:
        return validate(secret, account_id)
    protected = _envelope(_raw(guard))
    from scripts.options_market import metadata
    try:
        value = metadata(_dpapi(protected, decrypt=True))
        if type(value) is not dict or set(value) != {'secret', 'account_id'}:
            raise ValueError
        return validate(value['secret'], value['account_id'])
    except (ValueError, KeyError, UnicodeError, RecursionError, TypeError):
        raise ValueError('Saved Public credential could not be unlocked for this Windows user.') from None
