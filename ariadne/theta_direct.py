"""One authenticated vendor EOD RPC, exact research evidence, never paper fills.

A caller owns the global setup reservation, credential load and child-process
supervision. No credential discovery, SDK client, installation, activation,
ledger, subscription, OS changes or authentication occurs on import/preflight.
Native gRPC is outside Python's socket policy: this is not an OS sandbox.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import ssl
import stat
import sys
import threading
import time
import types

from . import research_window as scope
from . import theta_market_data as identity
from .network_policy import install_network_policy
from .qqq_data import exchange_day, is_session, session_dates

SOURCE_ID = 'theta_direct_free_eod_research_v1'
ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / 'runtime/theta-direct'
ASSET_MANIFEST_SHA256 = '3dd5a0e517c891dc4aafc23503951058919dd405aa2cd31d6b1513b7bc0ff94f'
# Derived from the reviewed manifest's public roots.pem entry.
ROOTS_SHA256 = '890af1e9222a5a76b0c0beec4c570aa32d1ad592088fcf5610de95478a8de638'
AUTH_HOST = 'nexus-api.thetadata.us'
AUTH_PATH = '/identity/terminal/auth_user'
DATA_HOST = 'mdds-01.thetadata.us'
# Published SDK identifier, not an owner credential; pinned SDK client.py:150.
PUBLIC_SDK_IDENTIFIER = 'cf58ada4-4175-11f0-860f-1e2e95c79e64'
MAX_SECONDS, MAX_AUTH_BYTES = 300, 32768
MAX_MESSAGE_BYTES, MAX_RPC_BYTES = 512 * 1024, 4 * 1024 * 1024
MAX_DECODED_BYTES, MAX_PACKET_DECODED = 8 * 1024 * 1024, 1024 * 1024
MAX_MESSAGES, MAX_ROWS, MAX_CAPTURE_BYTES = 128, 4096, 24 * 1024 * 1024
_VALUE_KEYS = frozenset(('created', 'last_trade', 'open', 'high', 'low', 'close',
    'volume', 'count', 'bid_size', 'bid_exchange', 'bid', 'bid_condition',
    'ask_size', 'ask_exchange', 'ask', 'ask_condition'))
_PRICES = frozenset(('open', 'high', 'low', 'close', 'bid', 'ask'))
_COUNTS = frozenset(('volume', 'count', 'bid_size', 'ask_size', 'bid_exchange',
    'ask_exchange', 'bid_condition', 'ask_condition'))
_RPC = {'stock': '/BetaEndpoints.BetaThetaTerminal/GetStockHistoryEod',
        'option': '/BetaEndpoints.BetaThetaTerminal/GetOptionHistoryEod'}
_CODES = frozenset(('invalid_input', 'invalid_assets', 'offline', 'closed',
    'policy_stopped', 'deadline', 'authentication_refused', 'authentication_shape',
    'authentication_transport', 'authentication_tls_setup',
    'authentication_media', 'authentication_encoding', 'authentication_length',
    'authentication_json', 'authentication_session', 'authentication_user',
    'authentication_email', 'authentication_assignment', 'capture_internal', 'rpc_refused', 'rpc_transport', 'invalid_response',
    'response_limit', 'secret_reflection', 'publication_limit', 'native_unavailable'))
_AUTH_VALIDATION_CODES = frozenset(('authentication_shape','authentication_media',
    'authentication_encoding','authentication_length','authentication_json',
    'authentication_session','authentication_user','authentication_email','authentication_assignment'))
_PARTIAL_OWNER = object()
_CAPTURE_STAGES = frozenset(('native_setup','authentication_reservation','authentication',
    'eod_reservation','eod_stream','message_decode','row_validation','evidence_commit',
    'coverage_finalize','serialization','publication_guard','owner_check'))
_EXCEPTION_CATEGORIES = frozenset(('direct_error','type','value','key','index','attribute',
    'overflow','memory','filesystem','timeout','database','other'))
_EXCEPTION_TYPES = ((TypeError,'type'),(ValueError,'value'),(KeyError,'key'),
    (IndexError,'index'),(AttributeError,'attribute'),(OverflowError,'overflow'),
    (MemoryError,'memory'),(TimeoutError,'timeout'),(OSError,'filesystem'),
    (FileNotFoundError,'filesystem'),(PermissionError,'filesystem'),
    (IsADirectoryError,'filesystem'),(NotADirectoryError,'filesystem'),
    (BlockingIOError,'filesystem'))
_SQLITE_EXCEPTION_NAMES = ('Error','DatabaseError','OperationalError','IntegrityError',
    'InternalError','ProgrammingError','NotSupportedError','DataError','InterfaceError')
_ROUTING_HOST_KEYS = frozenset(('host','server','mddshost','mddsserver'))
_ROUTING_PORT_KEYS = frozenset(('port','mddsport'))
# Finite exact aliases, not a substring match over arbitrary account fields.
_ROUTING_UNSUPPORTED_KEYS = (frozenset(('hostname','endpoint','url')) |
    frozenset(prefix+suffix for prefix in ('server','mdds','data','destination','endpoint','auth')
        for suffix in ('host','hostname','server','port','address','url','endpoint','endpointurl'))
    ) - _ROUTING_HOST_KEYS - _ROUTING_PORT_KEYS
_RPC_CODES = frozenset(('OK', 'CANCELLED', 'UNKNOWN', 'INVALID_ARGUMENT',
    'DEADLINE_EXCEEDED', 'NOT_FOUND', 'ALREADY_EXISTS', 'PERMISSION_DENIED',
    'RESOURCE_EXHAUSTED', 'FAILED_PRECONDITION', 'ABORTED', 'OUT_OF_RANGE',
    'UNIMPLEMENTED', 'INTERNAL', 'UNAVAILABLE', 'DATA_LOSS', 'UNAUTHENTICATED'))


class DirectError(ValueError):
    """Fixed diagnostics only; partial_capture never contains authentication."""
    def __init__(self, code='invalid_response', *, http_status=None, rpc_status=None,
                 partial_capture=None, capture_stage=None, exception_category=None):
        self.code = code if type(code) is str and code in _CODES else 'invalid_response'
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.rpc_status = rpc_status if type(rpc_status) is str and rpc_status in _RPC_CODES else None
        self.partial_capture = partial_capture
        self.capture_stage = capture_stage if type(capture_stage) is str and capture_stage in _CAPTURE_STAGES else None
        self.exception_category = exception_category if type(exception_category) is str and exception_category in _EXCEPTION_CATEGORIES else None
        self._partial_owner = None
        super().__init__('Theta direct research stopped: ' + self.code + '. No retry was attempted.')



def _error_state(error):
    if type(error) is not DirectError:return None
    try:state = object.__getattribute__(error,'__dict__')
    except Exception:return None
    if type(state) is not dict or any(type(key) is not str for key in state):return None
    return state



def _exception_category(error):
    """Exact known types only: no dynamic class names, hooks or exception text."""
    kind = type(error)
    if kind is DirectError:return 'direct_error'
    for expected,category in _EXCEPTION_TYPES:
        if kind is expected:return category
    # Owner checks already use stdlib sqlite3. Inspect an existing module's
    # exact plain dictionary rather than importing any native dependency here.
    database = sys.modules.get('sqlite3')
    if type(database) is types.ModuleType:
        state = object.__getattribute__(database,'__dict__')
        if type(state) is dict and all(type(key) is str for key in state):
            for name in _SQLITE_EXCEPTION_NAMES:
                if kind is state.get(name):return 'database'
    return 'other'


def _market_prefix_available(state):
    """Creation-time facts from admitted market data, not mutable payload walks."""
    if type(state) is not dict or any(type(key) is not str for key in state):return False
    return (state.get('_partial_owner') is _PARTIAL_OWNER
        and type(state.get('partial_capture')) is dict
        and type(state.get('_admitted_market_rows')) is int
        and 1 <= state['_admitted_market_rows'] <= MAX_ROWS
        and type(state.get('_admitted_market_responses')) is int
        and 1 <= state['_admitted_market_responses'] <= MAX_MESSAGES)


def diagnostic_from_error(error):
    """Small safe diagnostic: never exception text, auth bodies or partial data."""
    state = _error_state(error)
    if state is None:
        return {'code':'policy_stopped','http_status':None,'rpc_status':None,
            'retry_attempted':False,'partial_market_evidence_available':False,
            'capture_stage':None,'exception_category':None}
    code,http_status,rpc_status = (state.get(key) for key in ('code','http_status','rpc_status'))
    stage,category = state.get('capture_stage'),state.get('exception_category')
    return {'code':code if type(code) is str and code in _CODES else 'invalid_response',
        'http_status':http_status if type(http_status) is int and 100 <= http_status <= 599 else None,
        'rpc_status':rpc_status if type(rpc_status) is str and rpc_status in _RPC_CODES else None,
        'retry_attempted':False,'partial_market_evidence_available':_market_prefix_available(state),
        'capture_stage':stage if type(stage) is str and stage in _CAPTURE_STAGES else None,
        'exception_category':category if type(category) is str and category in _EXCEPTION_CATEGORIES else None}


def _require(condition, code='invalid_input'):
    if not condition:raise DirectError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False).encode('ascii')


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:raise ValueError
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except Exception:raise DirectError('authentication_json') from None


def _plain(value, maximum):
    return (type(value) is str and 1 <= len(value) <= maximum
        and all(32 <= ord(c) <= 126 for c in value))


def _environment():
    # Refuse rather than mutate the owner environment. Native gRPC, dotenv,
    # proxy, CA, debug and key-log overrides must not enter this worker.
    forbidden = {'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
        'grpc_proxy', 'ssl_cert_file', 'ssl_cert_dir', 'sslkeylogfile'}
    for key in os.environ:
        lower = key.lower()
        if (lower in forbidden or lower.startswith(('grpc_', 'thetadata_',
                'python_zstandard_', 'otel_'))):raise DirectError('invalid_input')


def _no_reparse(path):
    try:
        info = path.lstat()
        _require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400), 'invalid_assets')
        _require(not stat.S_ISREG(info.st_mode) or info.st_nlink == 1, 'invalid_assets')
        return info
    except OSError:raise DirectError('invalid_assets') from None


def _read_asset(root, relative, limit):
    # Reuse the workspace's directory pins, final Windows handle-path check,
    # regular-file/no-hardlink enforcement and bounded stable read.
    from .tools import ToolRegistry
    guard = object.__new__(ToolRegistry);guard.root = root
    path = guard._path(relative)
    with guard._directory(path.parent) as directory:
        with os.fdopen(guard._open_read(path,directory),'rb') as stream:
            before = os.fstat(stream.fileno());raw = stream.read(limit+1);after = os.fstat(stream.fileno())
    fingerprint = lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    _require(len(raw) <= limit and len(raw) == before.st_size and fingerprint(before) == fingerprint(after), 'invalid_assets')
    return raw


def _inventory(root):
    from .tools import ToolRegistry
    guard = object.__new__(ToolRegistry);guard.root = root
    result = set();seen = 0;pending = [root]
    while pending:
        current = pending.pop();_no_reparse(current)
        with guard._directory(current):
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1;_require(seen <= 600, 'invalid_assets')
                    path = Path(entry.path);info = _no_reparse(path)
                    if stat.S_ISDIR(info.st_mode):pending.append(path)
                    elif stat.S_ISREG(info.st_mode):result.add(path.relative_to(root).as_posix())
                    else:raise DirectError('invalid_assets')
    return result


def _verify_assets(root, expected_hash):
    """Pure inspection. Never adds paths, imports packages or creates files."""
    try:
        for path in (ROOT, ROOT/'runtime', root):_no_reparse(path)
        raw = _read_asset(root,'assets.json',128*1024)
        _require(hashlib.sha256(raw).hexdigest() == expected_hash, 'invalid_assets')
        manifest = json.loads(raw)
        _require(manifest['profile'] == 'ariadne-theta-direct-eod-assets-v1'
            and manifest['target'] == 'CPython3.13-win_amd64', 'invalid_assets')
        files = manifest['files'];_require(type(files) is list and 1 <= len(files) <= 400, 'invalid_assets')
        expected, total = set(), 0
        for item in files:
            relative = PurePosixPath(item['path'])
            _require(not relative.is_absolute() and '..' not in relative.parts
                and ':' not in item['path'] and '\\' not in item['path']
                and item['path'] not in expected and type(item['bytes']) is int
                and 0 <= item['bytes'] <= 32*1024*1024, 'invalid_assets')
            expected.add(item['path'])
            content = _read_asset(root,item['path'],item['bytes']);total += len(content)
            _require(total <= 64*1024*1024 and len(content) == item['bytes']
                and hashlib.sha256(content).hexdigest() == item['sha256'], 'invalid_assets')
        _require(_inventory(root) == expected | {'assets.json'}, 'invalid_assets')
        return {'manifest_sha256': expected_hash, 'files_checked': len(files),
            'asset_bytes': total, 'vendor_imported': False, 'target': manifest['target']}
    except DirectError:raise
    except Exception:raise DirectError('invalid_assets') from None


def asset_status():
    return _verify_assets(ASSET_ROOT, ASSET_MANIFEST_SHA256)


def _selection(value, allowed_symbols, window):
    _require(type(value) is dict and value.get('kind') in ('stock', 'option'))
    key = 'symbol' if value['kind'] == 'stock' else 'contract_id'
    _require(set(value) == {'kind', key, 'start_date', 'end_date'})
    try:
        identity._instrument({'type': 'EQUITY' if key == 'symbol' else 'OPTION', 'symbol': value[key]})
        contract = identity._contract(value[key]) if key == 'contract_id' else None
        symbol = contract['symbol'] if contract else value[key]
        _require(type(allowed_symbols) is tuple and 1 <= len(allowed_symbols) <= 4)
        _require(len(set(allowed_symbols)) == len(allowed_symbols))
        for root in allowed_symbols:identity._instrument({'type':'EQUITY', 'symbol':root})
        _require(symbol in allowed_symbols)
        scope.require_range(value['start_date'], value['end_date'], window)
        _require(value['end_date'] < window.anchor_day)
        _require(bool(session_dates(date.fromisoformat(value['start_date']), date.fromisoformat(value['end_date']))))
        if contract:_require(contract['expiration'] >= value['end_date'])
    except DirectError:raise
    except Exception:raise DirectError('invalid_input') from None
    return dict(value), contract


# Strict supported subset of the pinned official protobuf schemas. Unknown
# fields/wire kinds, noncanonical varints, duplicate singular/oneof values and
# groups are rejected before interpretation. No general protobuf execution.
def _varint(raw, offset):
    value = 0;start = offset
    for index in range(10):
        _require(offset < len(raw), 'invalid_response');byte = raw[offset];offset += 1
        _require(index != 9 or byte <= 1, 'invalid_response')
        value |= (byte & 127) << (7 * index)
        if byte < 128:
            _require(offset-start == 1 or byte != 0, 'invalid_response')
            return value, offset
    raise DirectError('invalid_response')


def _wire(raw, schema, repeated=(), repeat_limits=None):
    _require(type(raw) is bytes and len(raw) <= MAX_PACKET_DECODED, 'invalid_response')
    result = {};offset = 0
    while offset < len(raw):
        tag, offset = _varint(raw, offset);number, kind = tag >> 3, tag & 7
        _require(number in schema and kind == schema[number], 'invalid_response')
        _require(number in repeated or number not in result, 'invalid_response')
        if kind == 0:value, offset = _varint(raw, offset)
        else:
            size, offset = _varint(raw, offset);_require(size <= len(raw)-offset, 'invalid_response')
            value = raw[offset:offset+size];offset += size
        bucket = result.setdefault(number, [])
        _require(number not in repeated or len(bucket) < (repeat_limits or {}).get(number,MAX_ROWS), 'response_limit')
        bucket.append(value)
    return result


def _signed(value, bits):
    if value >= 1 << 63:value -= 1 << 64
    _require(-(1 << (bits-1)) <= value < 1 << (bits-1), 'invalid_response')
    return value


def _text(raw, maximum=64):
    try:value = raw.decode('utf-8')
    except Exception:raise DirectError('invalid_response') from None
    _require(_plain(value, maximum), 'invalid_response');return value


def _value(raw):
    fields = _wire(raw, {1:2, 2:0, 3:2, 4:2, 5:0, 6:0})
    _require(len(fields) <= 1, 'invalid_response')
    if not fields:return {'kind':'unset', 'value':None}
    key = next(iter(fields));item = fields[key][0]
    if key == 1:return {'kind':'text', 'value':_text(item)}
    if key == 2:return {'kind':'number', 'value':_signed(item, 64)}
    if key == 3:
        price = _wire(item, {1:0, 2:0});number = _signed(price.get(1, [0])[0], 32)
        scale = _signed(price.get(2, [0])[0], 32)
        _require(0 <= scale <= 19, 'invalid_response')
        digits = tuple(int(digit) for digit in str(abs(number)))
        exact = None if scale == 0 else format(Decimal((int(number < 0), digits, scale-10)), 'f')
        return {'kind':'price', 'integer':number, 'scale_type':scale, 'decimal':exact}
    if key == 4:
        clock = _wire(item, {1:0, 2:0, 3:0, 4:0})
        millis, zone, nanos, precision = (clock.get(k, [0])[0] for k in (1,2,3,4))
        _require(0 < millis <= 253402300799999 and zone in (0,1)
            and 0 <= nanos <= 0xffffffff and precision in (0,1), 'invalid_response')
        return {'kind':'timestamp', 'epoch_ms':millis, 'nanos':nanos,
            'zone':zone, 'precision':precision, 'epoch_ns':None,
            'submillisecond_semantics':'preserved_raw_not_reconstructed'}
    if key == 5:
        _require(item == 0, 'invalid_response');return {'kind':'null', 'value':None}
    _require(item in (0,1), 'invalid_response');return {'kind':'boolean', 'value':bool(item)}


def _scan(raw, secrets):
    _require(not any(secret and secret in raw for secret in secrets), 'secret_reflection')


def _decode(raw, remaining, decompress, secrets):
    _require(type(raw) is bytes and 1 <= len(raw) <= MAX_MESSAGE_BYTES, 'response_limit')
    _scan(raw, secrets)
    envelope = _wire(raw, {1:2, 2:2, 3:0})
    _require(1 in envelope, 'invalid_response')
    compression = _wire(envelope.get(2, [b''])[0], {1:0, 2:0})
    algorithm = compression.get(1, [0])[0]
    _require(algorithm in (0,1), 'invalid_response')
    payload = envelope[1][0];maximum = min(MAX_PACKET_DECODED, remaining)
    _require(maximum > 0, 'response_limit')
    decoded = payload if algorithm == 0 else decompress(payload, maximum)
    _require(type(decoded) is bytes and 1 <= len(decoded) <= maximum, 'response_limit')
    _scan(decoded, secrets)
    original_size = _signed(envelope.get(3, [0])[0], 32)
    _require(original_size in (0, len(decoded)), 'invalid_response')
    table = _wire(decoded, {1:2, 2:2}, (1,2), {1:24,2:MAX_ROWS})
    headers = tuple(_text(value, 32) for value in table.get(1, []))
    _require(1 <= len(headers) <= 24 and len(set(headers)) == len(headers), 'invalid_response')
    rows = []
    for item in table.get(2, []):
        _require(len(rows) < MAX_ROWS, 'response_limit')
        values = _wire(item, {1:2}, (1,), {1:24}).get(1, [])
        _require(len(values) == len(headers), 'invalid_response')
        rows.append(dict(zip(headers, (_value(value) for value in values))))
    return headers, rows, {'compression':'NONE' if algorithm == 0 else 'ZSTD',
        'declared_original_size':original_size, 'decoded_bytes':len(decoded)}


def _clock(value):
    try:return datetime(1970,1,1,tzinfo=timezone.utc) + timedelta(milliseconds=value['epoch_ms'])
    except Exception:raise DirectError('invalid_response') from None


def _row(values, selection, contract, received_ms):
    expected = _VALUE_KEYS | (frozenset(('symbol','expiration','strike','right')) if contract else frozenset())
    _require(set(values) in (expected, expected | {'symbol'}), 'invalid_response')
    created = values['created'];_require(created['kind'] == 'timestamp', 'invalid_response')
    report_day = exchange_day(_clock(created)).isoformat()
    _require(selection['start_date'] <= report_day <= selection['end_date']
        and is_session(date.fromisoformat(report_day)) and created['epoch_ms'] <= received_ms, 'invalid_response')
    last = values['last_trade']
    _require(last['kind'] in ('null','unset','timestamp'), 'invalid_response')
    if last['kind'] == 'timestamp':_require(last['epoch_ms'] <= created['epoch_ms'], 'invalid_response')
    for key in _PRICES:
        item = values[key]
        _require(item['kind'] in ('price','null','unset'), 'invalid_response')
        if item['kind'] == 'price':_require(item['integer'] >= 0, 'invalid_response')
    for key in _COUNTS:
        item = values[key]
        _require(item['kind'] in ('number','null','unset'), 'invalid_response')
        if item['kind'] == 'number':_require(item['value'] >= 0, 'invalid_response')
    root = contract['symbol'] if contract else selection['symbol']
    if 'symbol' in values:_require(values['symbol'] == {'kind':'text','value':root}, 'invalid_response')
    if contract:
        expiry, strike, right = (values[key] for key in ('expiration','strike','right'))
        _require(expiry == {'kind':'text','value':contract['expiration']}, 'invalid_response')
        _require(strike['kind'] == 'price' and strike['decimal'] is not None
            and Decimal(strike['decimal']) == Decimal(contract['strike']), 'invalid_response')
        _require(right['kind'] == 'text' and right['value'].lower() in
            (('put','p') if contract['right'] == 'put' else ('call','c')), 'invalid_response')
    return {'report_day':report_day, 'values':values, 'quote_event_at':None,
        'open_interest':None, 'standard_deliverable_verified':False,
        'live_execution_eligible':False}


class _Session:
    def __init__(self, token, email):self.token, self.email = token, email
    def __repr__(self):return '<Theta session: private>'


def _authentication(raw):
    result = _json(raw)
    _require(type(result) is dict, 'authentication_json')
    _require(_plain(result.get('sessionId'),512), 'authentication_session')
    user = result.get('user');_require(type(user) is dict, 'authentication_user')
    _require(_plain(user.get('email'),320), 'authentication_email')
    # Endpoints are constants, never selected from response fields. Exact
    # normalized routing keys avoid mistaking incidental account metadata such
    # as reportingEnabled/supportLevel for a port assignment. Only the original
    # supported host/port echoes are admitted; other explicit destination hints
    # remain unsupported. Ignored incidental fields are never retained or used.
    for source in (result,user):
        for key,value in source.items():
            normalized = key.lower().replace('_','').replace('-','')
            if normalized in _ROUTING_HOST_KEYS:
                _require(type(value) is str and value == DATA_HOST, 'authentication_assignment')
            elif normalized in _ROUTING_PORT_KEYS:
                _require(type(value) in (str,int) and value in (443,'443'), 'authentication_assignment')
            elif normalized in _ROUTING_UNSUPPORTED_KEYS:
                raise DirectError('authentication_assignment')
    return _Session(result['sessionId'],user['email'])


def _load_native():
    """Called only after online admission; source-verified paths, no SDK import."""
    _require(sys.platform == 'win32' and sys.version_info[:2] == (3,13), 'native_unavailable')
    asset_status();_environment()
    prefixes = ('grpc','google','zstandard','typing_extensions','ariadne_theta_protocol')
    _require(not any(name == prefix or name.startswith(prefix+'.')
        for name in sys.modules for prefix in prefixes), 'native_unavailable')
    site, protocol = ASSET_ROOT/'site-packages', ASSET_ROOT/'protocol'
    def load(name, path, package=False):
        spec = importlib.util.spec_from_file_location(name, path,
            submodule_search_locations=[str(path.parent)] if package else None)
        _require(spec is not None and spec.loader is not None, 'native_unavailable')
        module = importlib.util.module_from_spec(spec);sys.modules[name] = module
        spec.loader.exec_module(module);return module
    # google is a fixed local namespace, not a search of user/site directories.
    google = types.ModuleType('google');google.__path__ = [str(site/'google')];sys.modules['google'] = google
    old_path = list(sys.path);sys.dont_write_bytecode = True
    try:
        sys.path[:0] = [str(site), str(protocol)]
        load('typing_extensions', site/'typing_extensions.py')
        grpc = load('grpc', site/'grpc/__init__.py', True)
        zstd = load('zstandard', site/'zstandard/__init__.py', True)
        load('google.protobuf', site/'google/protobuf/__init__.py', True)
        load('ariadne_theta_protocol', protocol/'ariadne_theta_protocol/__init__.py', True)
        common = load('ariadne_theta_protocol.endpoints_pb2', protocol/'ariadne_theta_protocol/endpoints_pb2.py')
        load('ariadne_theta_protocol.v3grpc', protocol/'ariadne_theta_protocol/v3grpc/__init__.py', True)
        requests = load('ariadne_theta_protocol.v3grpc.endpoints_pb2', protocol/'ariadne_theta_protocol/v3grpc/endpoints_pb2.py')
        for name, module in tuple(sys.modules.items()):
            if any(name == p or name.startswith(p+'.') for p in prefixes) and getattr(module, '__file__', None):
                _require(Path(module.__file__).resolve().is_relative_to(ASSET_ROOT.resolve()), 'native_unavailable')
        return grpc, zstd, common, requests
    except DirectError:raise
    except Exception:raise DirectError('native_unavailable') from None
    finally:sys.path[:] = old_path



def _certificate_pem(raw):
    """Validate UTF-8 bundle comments and every complete ASCII certificate.

    OpenSSL's cadata string requires ASCII PEM. The pinned provider bundle has
    UTF-8 human-readable comments; only validated certificate blocks enter that
    string. Original verified bytes remain unchanged for gRPC's root input.
    """
    try:
        _require(type(raw) is bytes and 1 <= len(raw) <= 512*1024, 'authentication_tls_setup')
        document = raw.decode('utf-8', errors='strict')
        blocks = [];current = None;payload = []
        for line in document.split('\n'):
            if line.endswith('\r'):line = line[:-1]
            if line == '-----BEGIN CERTIFICATE-----':
                _require(current is None and len(blocks) < 256, 'authentication_tls_setup')
                current = [line];payload = []
            elif line == '-----END CERTIFICATE-----':
                _require(current is not None and bool(payload), 'authentication_tls_setup')
                encoded = ''.join(payload)
                _require(len(encoded) <= 64*1024 and bool(base64.b64decode(encoded,validate=True)), 'authentication_tls_setup')
                current.append(line);blocks.append('\n'.join(current));current = None
            elif current is not None:
                _require(1 <= len(line) <= 128 and re.fullmatch(r'[A-Za-z0-9+/]+={0,2}',line) is not None,
                    'authentication_tls_setup')
                payload.append(line);current.append(line)
                _require(sum(map(len,payload)) <= 64*1024, 'authentication_tls_setup')
            else:
                # No nested, orphaned, truncated or other PEM object is ignored.
                _require('-----BEGIN' not in line and '-----END' not in line, 'authentication_tls_setup')
        _require(current is None and 1 <= len(blocks) <= 256, 'authentication_tls_setup')
        pem = '\n'.join(blocks)+'\n';pem.encode('ascii',errors='strict')
        return pem,len(blocks)
    except DirectError:raise
    except Exception:raise DirectError('authentication_tls_setup') from None


def _tls_context(raw):
    try:
        pem,count = _certificate_pem(raw)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl.CERT_REQUIRED;context.check_hostname = True
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cadata=pem)
        _require(context.cert_store_stats()['x509'] == count, 'authentication_tls_setup')
        return context
    except DirectError:raise
    except Exception:raise DirectError('authentication_tls_setup') from None



def tls_status():
    """Guarded pinned public CA readiness; no native transport or credentials."""
    try:
        raw = _read_asset(ASSET_ROOT,'site-packages/grpc/_cython/_credentials/roots.pem',512*1024)
        _require(hashlib.sha256(raw).hexdigest() == ROOTS_SHA256, 'authentication_tls_setup')
        context = _tls_context(raw)
        return {'anchors':context.cert_store_stats()['x509'],'verify_mode':'CERT_REQUIRED',
            'check_hostname':context.check_hostname,'minimum_tls':'TLSv1.2'}
    except DirectError:raise
    except Exception:raise DirectError('authentication_tls_setup') from None


class _NativeTransport:
    def __init__(self):
        self.grpc, self.zstd, self.common, self.requests = _load_native()
        self.connection = self.channel = self.call = None
        self.roots = _read_asset(ASSET_ROOT,'site-packages/grpc/_cython/_credentials/roots.pem',512*1024)
        _require(hashlib.sha256(self.roots).hexdigest() == ROOTS_SHA256, 'invalid_assets')

    def authenticate(self, key, deadline, check):
        connection = response = None;observed_http_200 = False
        try:
            try:
                context = _tls_context(self.roots)
                connection = http.client.HTTPSConnection(AUTH_HOST, 443, context=context,
                    timeout=max(.05, min(5, deadline-time.monotonic())))
                self.connection = connection;connection.set_debuglevel(0)
            except DirectError:raise
            except Exception:raise DirectError('authentication_tls_setup') from None
            body = canonical({'apiKey':key, 'authEnv':{'envType':'PROD'}})
            check();connection.request('POST', AUTH_PATH, body=body,
                headers={'Content-Type':'application/json','Accept':'application/json',
                    'Accept-Encoding':'identity','TD-TERMINAL-KEY':PUBLIC_SDK_IDENTIFIER,
                    'Connection':'close','User-Agent':'Ariadne-Theta-Direct-EOD/1'})
            check();response = connection.getresponse()
            if type(response.status) is not int or response.status != 200:raise DirectError('authentication_refused', http_status=response.status)
            observed_http_200 = True
            media = response.getheader('Content-Type','')
            _require(type(media) is str and media.split(';',1)[0].strip().lower() == 'application/json', 'authentication_media')
            encoding = response.getheader('Content-Encoding','identity')
            _require(type(encoding) is str and encoding.lower() in ('','identity'), 'authentication_encoding')
            length = response.getheader('Content-Length')
            try:
                valid_length = length is None or (type(length) is str and length.isascii()
                    and length.isdigit() and int(length) <= MAX_AUTH_BYTES)
            except Exception:valid_length = False
            _require(valid_length, 'authentication_length')
            raw = bytearray()
            while True:
                check();remaining = deadline-time.monotonic();_require(remaining > 0, 'deadline')
                if connection.sock is not None:connection.sock.settimeout(min(5, remaining))
                block = response.read1(min(4096, MAX_AUTH_BYTES+1-len(raw)))
                if not block:break
                raw.extend(block);_require(len(raw) <= MAX_AUTH_BYTES, 'authentication_length')
            _require(length is None or len(raw) == int(length), 'authentication_length')
            check();return _authentication(bytes(raw))
        except DirectError as error:
            if type(error) is not DirectError:raise DirectError('authentication_transport') from None
            details = diagnostic_from_error(error)
            safe = DirectError(details['code'],http_status=details['http_status'],rpc_status=details['rpc_status'],
                capture_stage=details['capture_stage'],exception_category=details['exception_category'])
            if observed_http_200 and safe.code in _AUTH_VALIDATION_CODES:safe.http_status = 200
            raise safe from None
        except http.client.IncompleteRead:
            raise DirectError('authentication_length',http_status=200 if observed_http_200 else None) from None
        except Exception:raise DirectError('authentication_transport') from None
        finally:
            for item in (response,connection):
                if item is not None:
                    try:item.close()
                    except Exception:pass
            self.connection = None

    def eod(self, session, selection, contract, deadline, check):
        parameters = {'client':'python','start_date':selection['start_date'],'end_date':selection['end_date'],
            'symbol':contract['symbol'] if contract else selection['symbol']}
        if contract:parameters.update({key:contract[key] for key in ('expiration','strike','right')})
        query_info = self.requests.QueryInfo(auth_token=self.common.AuthToken(session_uuid=session.token),
            email_hint=session.email, query_parameters=parameters)
        if contract:
            query = self.requests.OptionHistoryEodRequestQuery(contract_spec=self.common.ContractSpec(
                symbol=contract['symbol'], expiration=contract['expiration'], strike=contract['strike'], right=contract['right']),
                start_date=selection['start_date'], end_date=selection['end_date'])
            request = self.requests.OptionHistoryEodRequest(query_info=query_info, params=query)
        else:
            query = self.requests.StockHistoryEodRequestQuery(symbol=selection['symbol'],
                start_date=selection['start_date'], end_date=selection['end_date'])
            request = self.requests.StockHistoryEodRequest(query_info=query_info, params=query)
        check()
        self.channel = self.grpc.secure_channel(DATA_HOST+':443', self.grpc.ssl_channel_credentials(self.roots),
            options=(('grpc.enable_http_proxy',0),('grpc.enable_retries',0),
                ('grpc.service_config_disable_resolution',1),('grpc.max_receive_message_length',MAX_MESSAGE_BYTES),
                ('grpc.max_send_message_length',16384)))
        function = self.channel.unary_stream(_RPC[selection['kind']], request_serializer=type(request).SerializeToString)
        try:
            check();_require(deadline > time.monotonic(), 'deadline')
            self.call = function(request, timeout=min(60, deadline-time.monotonic()), wait_for_ready=False)
            for raw in self.call:
                check();yield raw
        except DirectError:raise
        except Exception as error:
            status = None
            try:status = error.code().name
            except Exception:pass
            raise DirectError('rpc_refused' if type(status) is str and status in _RPC_CODES else 'rpc_transport', rpc_status=status) from None

    def decompress(self, raw, maximum):
        try:
            _require(raw.startswith(b'\x28\xb5\x2f\xfd'), 'invalid_response')
            parameters = self.zstd.get_frame_parameters(raw)
            _require(parameters.window_size <= MAX_PACKET_DECODED
                and (parameters.content_size in (self.zstd.CONTENTSIZE_UNKNOWN, self.zstd.CONTENTSIZE_ERROR)
                    or parameters.content_size <= maximum), 'response_limit')
            return self.zstd.ZstdDecompressor(max_window_size=MAX_PACKET_DECODED//1024).decompress(
                raw, max_output_size=maximum, allow_extra_data=False)
        except DirectError:raise
        except Exception:raise DirectError('invalid_response') from None

    def close(self):
        for value, operation in ((self.call,'cancel'),(self.connection,'close'),(self.channel,'close')):
            if value is not None:
                try:getattr(value,operation)()
                except Exception:pass
        self.call = self.connection = self.channel = None


class DirectEODClient:
    """Single use; fake transport is trusted test injection, never provenance."""
    def __init__(self, online=False, *, transport=None):
        _require(type(online) is bool)
        self.online, self.transport = online, transport
        self.used = self.closed = self.expired = False

    def preflight(self, selection, *, allowed_symbols, window=None):
        _environment();window = scope.derive_window() if window is None else window
        selected, _ = _selection(selection, allowed_symbols, window)
        assets = asset_status();tls = tls_status()
        return {'schema':1,'source_id':SOURCE_ID,'profile':'one_authentication_one_eod_rpc',
            'selection':selected,'allowed_symbols':list(allowed_symbols),'selection_window':window.as_dict(),
            'assets':assets,'tls':tls,'limits':{'seconds':MAX_SECONDS,'auth_response_bytes':MAX_AUTH_BYTES,
                'rpc_messages':MAX_MESSAGES,'rpc_message_bytes':MAX_MESSAGE_BYTES,'rpc_payload_bytes':MAX_RPC_BYTES,
                'decoded_bytes':MAX_DECODED_BYTES,'rows':MAX_ROWS},
            'authentication_performed':False,'vendor_imported':False,'financial_authority':False}

    def _allowed(self):
        if self.closed:raise DirectError('closed')
        if self.expired:raise DirectError('deadline')
        if (not self.online or os.environ.get('ARIADNE_OFFLINE') != '0'
                or install_network_policy()['mode'] != 'online'):raise DirectError('offline')
        _environment()

    def close(self):
        self.closed = True
        if self.transport is not None:self.transport.close()

    def capture(self, api_key, selection, *, allowed_symbols, window=None, check, before_operation):
        # Offline refusal precedes asset inspection, native imports or key use.
        self._allowed();_require(not self.used, 'closed')
        _require(callable(check) and callable(before_operation))
        window = scope.derive_window() if window is None else window
        preflight = self.preflight(selection, allowed_symbols=allowed_symbols, window=window)
        selected, contract = _selection(selection, allowed_symbols, window)
        _require(_plain(api_key,4096) and len(api_key) >= 16)
        self.used = True;started = datetime.now(timezone.utc);deadline = time.monotonic()+MAX_SECONDS
        result = {'schema':1,'source_id':SOURCE_ID,'kind':'theta_direct_eod_capture','status':'running',
            'selection':selected,'allowed_symbols':list(allowed_symbols),'selection_window':window.as_dict(),
            'started_at':started.isoformat(),'completed_at':None,'preflight_sha256':hashlib.sha256(canonical(preflight)).hexdigest(),
            'operations':{'authentication':0,'eod_rpc':0},'responses':[],'rows':[],
            'transport':{'kind':'vendor_direct_https_grpc' if self.transport is None else 'synthetic_test',
                'provider_authenticated':False,'market_data_cryptographically_signed':False,
                'live_execution_eligible':False,'rpc_payload_bytes_exclude_transport_overhead':True},
            'open_interest_available':False,'quote_event_available':False,'paper_fills':0,
            'source_report_clock_basis':'Provider EOD report generation, documented17:15ET; not quote-event time.',
            'submillisecond_semantics':'Raw epoch_ms/nanos/zone/precision retained; no reconstructed epoch_ns.'}
        secrets = [api_key.encode('ascii')];native = self.transport is None
        stage = 'native_setup'
        def owner_error(error):
            details = diagnostic_from_error(error)
            code = details['code'] if type(error) is DirectError else 'policy_stopped'
            return DirectError(code,http_status=details['http_status'],rpc_status=details['rpc_status'],
                capture_stage='owner_check',exception_category=_exception_category(error))
        def guard():
            self._allowed();_require(time.monotonic() < deadline,'deadline')
            try:check()
            except Exception as error:raise owner_error(error) from None
        def reserve(operation):
            try:before_operation(operation)
            except Exception as error:raise owner_error(error) from None
        def expire():
            self.expired = True
            if self.transport is not None:self.transport.close()
        timer = threading.Timer(MAX_SECONDS, expire);timer.daemon = True;timer.start()
        total_bytes = decoded_bytes = 0;headers = None;seen = set();previous = None
        try:
            guard()
            if native:self.transport = _NativeTransport()
            stage = 'authentication_reservation'
            guard();reserve('authentication');result['operations']['authentication'] = 1
            stage = 'authentication'
            session = self.transport.authenticate(api_key, deadline, guard)
            _require(type(session) is _Session, 'authentication_session')
            _require(_plain(session.token,512), 'authentication_session')
            _require(_plain(session.email,320), 'authentication_email')
            secrets.extend((session.token.encode('ascii'),session.email.encode('ascii')))
            guard();result['transport']['provider_authenticated'] = native
            stage = 'eod_reservation'
            reserve('eod_rpc');result['operations']['eod_rpc'] = 1
            stage = 'eod_stream'
            for raw in self.transport.eod(session, selected, contract, deadline, guard):
                guard();_require(len(result['responses']) < MAX_MESSAGES, 'response_limit')
                _require(type(raw) is bytes and len(raw) <= MAX_RPC_BYTES-total_bytes, 'response_limit')
                received = datetime.now(timezone.utc);received_ms = int((received-datetime(1970,1,1,tzinfo=timezone.utc))//timedelta(milliseconds=1))
                stage = 'message_decode'
                current, values, detail = _decode(raw, MAX_DECODED_BYTES-decoded_bytes, self.transport.decompress, secrets)
                _require(headers is None or current == headers, 'invalid_response');headers = current
                stage = 'row_validation'
                admitted = [_row(value, selected, contract, received_ms) for value in values]
                _require(len(result['rows'])+len(admitted) <= MAX_ROWS, 'response_limit')
                for row in admitted:
                    day = row['report_day'];_require(day not in seen and (previous is None or day > previous), 'invalid_response')
                    seen.add(day);previous = day
                stage = 'evidence_commit'
                guard();total_bytes += len(raw);decoded_bytes += detail['decoded_bytes']
                result['responses'].append({'received_at':received.isoformat(),'payload_base64':base64.b64encode(raw).decode('ascii'),
                    'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'rows':len(admitted),**detail})
                result['rows'].extend(admitted)
                stage = 'eod_stream'
            guard();stage = 'coverage_finalize';result['status'] = 'completed';result['completed_at'] = datetime.now(timezone.utc).isoformat()
            result['missing_report_sessions'] = [d.isoformat() for d in session_dates(date.fromisoformat(selected['start_date']),date.fromisoformat(selected['end_date'])) if d.isoformat() not in seen]
            result['payload_bytes'],result['decoded_bytes'] = total_bytes,decoded_bytes
            result['empty_response'] = not result['rows']
            stage = 'serialization'
            output = canonical(result);_require(len(output) <= MAX_CAPTURE_BYTES,'publication_limit');_scan(output,secrets)
            stage = 'publication_guard'
            guard();return result
        except Exception as error:
            details = diagnostic_from_error(error)
            code = details['code'] if type(error) is DirectError else 'capture_internal'
            safe = DirectError(code,http_status=details['http_status'],rpc_status=details['rpc_status'],
                capture_stage=details['capture_stage'] or stage,
                exception_category=details['exception_category'] or _exception_category(error))
            result['status'] = 'failed';result['completed_at'] = datetime.now(timezone.utc).isoformat()
            result['failure_code'] = safe.code
            failure = DirectError(safe.code,http_status=safe.http_status,rpc_status=safe.rpc_status,
                partial_capture=result,capture_stage=safe.capture_stage,exception_category=safe.exception_category)
            failure._partial_owner = _PARTIAL_OWNER
            failure._admitted_market_rows = len(result['rows'])
            failure._admitted_market_responses = sum(item['rows'] > 0 for item in result['responses'])
            raise failure from None
        finally:
            timer.cancel()
            self.close()
