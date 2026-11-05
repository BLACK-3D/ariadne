"""Bounded free-EOD acquisition and pure evidence import, never paper execution.

The selection uses the existing twelve-calendar-month scope. EOD report creation
and last-trade times remain distinct from the unknown quote-event clock. The
local Terminal authenticates separately; loopback is never proof of provenance.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import base64
from decimal import Decimal
import hashlib
import http.client
import json
import os
import re
import time

from .network_policy import install_network_policy
from . import research_window as scope
from . import theta_market_data as snapshot
from .qqq_data import is_session, session_dates, exchange_day

SOURCE_ID = 'theta_eod_research_v1'
HOST, PORT = '127.0.0.1', 25503
MAX_REQUESTS, MAX_ROOTS = 64, 4
MAX_RESPONSE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_IMPORT_BYTES = 2 * MAX_TOTAL_BYTES + 1024 * 1024
MAX_RESPONSE_ROWS, MAX_TOTAL_ROWS = 4096, 20000
MAX_COLLECTION_SECONDS, MIN_REQUEST_INTERVAL = 300, 3.1
_TRANSPORT = {'source_id': SOURCE_ID, 'operation': 'historical_eod',
    'provider_authenticated': False, 'local_terminal_verified': False,
    'kind': 'local_terminal_unattested'}
_RESPONSE_KEYS = {'request', 'payload', 'received_at', 'sha256', 'bytes'}
_CAPTURE_KEYS = {'schema', 'source_id', 'status', 'selection', 'selection_window',
    'started_at', 'completed_at', 'responses', 'transport'}
_VALUE_KEYS = {'created', 'last_trade', 'open', 'high', 'low', 'close', 'volume',
    'count', 'bid_size', 'bid_exchange', 'bid', 'bid_condition', 'ask_size',
    'ask_exchange', 'ask', 'ask_condition'}


class ThetaHistoryError(ValueError):
    """Invalid bounded historical evidence; no source bodies in messages."""


class ThetaHistoryUnavailable(ThetaHistoryError):
    """Offline, missing entitlement, Terminal failure or collection limit."""


class ThetaHistoryCaptureError(ThetaHistoryUnavailable):
    """A failed attempt retains its bounded private received response prefix."""
    def __init__(self, partial_capture):
        super().__init__('Historical capture stopped; inspect its private partial receipt. No retry was attempted.')
        self.partial_capture = partial_capture


class ThetaHistoryResponseError(ThetaHistoryUnavailable):
    """Received invalid UTF-8 evidence; private base64 bytes, never error text."""
    def __init__(self, response_record):
        super().__init__('Historical response was not valid UTF-8; inspect private failed-response evidence.')
        self.response_record = response_record


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False).encode('ascii')


def _json(raw, maximum):
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        raise ThetaHistoryError('Historical JSON exceeds its bounded UTF-8 byte limit.')
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_float=Decimal,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ThetaHistoryError('Historical JSON is malformed or contains duplicate fields.') from None


def _require(value, message):
    if not value:
        raise ThetaHistoryError(message)


def _date(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ThetaHistoryError('Use exact ISO calendar dates.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ThetaHistoryError('Historical date is invalid.') from None


def _clock(value):
    try:
        return snapshot._clock(value)
    except (ValueError, TypeError):
        raise ThetaHistoryError('Use a valid actual aware historical receipt clock.') from None


def _time(value, *, required=False):
    if value is None and not required:
        return {'raw': None, 'iso': None, 'ns': None, 'basis': 'missing'}
    try:
        return snapshot._timestamp(value)
    except (ValueError, TypeError):
        raise ThetaHistoryError('Historical report/trade clock is invalid.') from None


def _request(value):
    if type(value) is not dict or value.get('kind') not in ('stock', 'option'):
        raise ThetaHistoryError('Use explicit stock or option historical requests.')
    key = 'symbol' if value['kind'] == 'stock' else 'contract_id'
    if set(value) != {'kind', key}:
        raise ThetaHistoryError('Use the exact explicit historical request fields.')
    try:
        snapshot._instrument({'type': 'EQUITY' if key == 'symbol' else 'OPTION',
                              'symbol': value[key]})
    except (ValueError, TypeError):
        raise ThetaHistoryError('Historical request identity is not a canonical stock/option identifier.') from None
    return dict(value)


def validate_manifest(value, window=None):
    """Admit complete explicit request scope; never clip dates or discover symbols.

    ``window`` is trusted host/test injection. The public command derives it from
    its actual clock, never an owner-supplied registration override.
    """
    if type(value) is not dict or set(value) != {'schema', 'start_date', 'end_date', 'requests'}:
        raise ThetaHistoryError('Use exactly schema, start_date, end_date and explicit requests.')
    _require(type(value['schema']) is int and value['schema'] == 1, 'Unsupported historical request schema.')
    window = scope.derive_window() if window is None else window
    scope.require_range(value['start_date'], value['end_date'], window)
    values = value['requests']
    _require(type(values) is list and 1 <= len(values) <= MAX_REQUESTS, 'Use one to64 explicit historical requests.')
    requests = [_request(row) for row in values]
    ids = [tuple(sorted(row.items())) for row in requests]
    _require(len(set(ids)) == len(ids), 'Historical requests must be unique; no repeated requests.')
    stocks = {r['symbol'] for r in requests if r['kind'] == 'stock'}
    roots = {snapshot._OCC.fullmatch(r['contract_id'])[1] for r in requests if r['kind'] == 'option'}
    _require(1 <= len(stocks) <= MAX_ROOTS and roots <= stocks,
        'Use up to4 explicit stocks and include the matching stock for every option root.')
    return {'schema': 1, 'start_date': value['start_date'], 'end_date': value['end_date'], 'requests': requests}


def _window_record(value):
    if type(value) is not dict or type(value.get('registered_at')) is not str:
        raise ThetaHistoryError('The original frozen selection-window record is required.')
    try:
        window = scope.derive_window(value['registered_at'])
        if value != window.as_dict():
            raise ValueError
        return window
    except (ValueError, TypeError, OverflowError):
        raise ThetaHistoryError('The retained selection-window record changed or is invalid.') from None


def _route(request, selection):
    request = _request(request)
    _require(type(selection) is dict and 'start_date' in selection and 'end_date' in selection, 'Explicit historical date bounds required.')
    _require(_date(selection['start_date']) <= _date(selection['end_date']), 'Historical route dates are reversed.')
    if request['kind'] == 'stock':
        route = '/v3/stock/history/eod?symbol=' + request['symbol']
    else:
        contract = snapshot._contract(request['contract_id'])
        route = '/v3/option/history/eod?symbol=' + contract['symbol']
        route += '&expiration=' + contract['expiration'] + '&right=' + contract['right'] + '&strike=' + contract['strike']
    return route + '&start_date=' + selection['start_date'] + '&end_date=' + selection['end_date'] + '&format=json'


class ThetaHistoryClient:
    """Finite sequential requests to the owner-managed numeric-loopback Terminal.

    The conservative20/minute limiter covers one serialized command/client, not
    other applications. The CLI owns its cross-process lock and owner controls.
    """
    def __init__(self, *, online=False):
        if type(online) is not bool:
            raise ThetaHistoryError('Online opt-in must be an exact boolean.')
        self._online, self._closed, self._last_start = online, False, None

    def close(self):
        self._closed = True

    def _allowed(self):
        if (not self._online or os.environ.get('ARIADNE_OFFLINE') != '0'
                or install_network_policy()['mode'] != 'online'):
            raise ThetaHistoryUnavailable('Historical Terminal requests require explicit online opt-in and online process policy.')
        if self._closed:
            raise ThetaHistoryUnavailable('The historical Terminal client is closed.')

    def _http(self, request, selection, deadline, check, *, remaining_bytes=MAX_TOTAL_BYTES):
        # Validate complete identity before opening a socket or resolving a host.
        route = _route(request, selection)
        _require(type(remaining_bytes) is int and 1 <= remaining_bytes <= MAX_TOTAL_BYTES, 'Remaining historical byte budget is invalid.')
        maximum = min(MAX_RESPONSE_BYTES, remaining_bytes)
        self._allowed();check()
        if self._last_start is not None:
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_start)
            if wait > 0:
                if wait >= deadline - time.monotonic():
                    raise ThetaHistoryUnavailable('Historical acquisition exhausted its shared deadline.')
                time.sleep(wait)
        self._allowed();check()
        connection = response = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ThetaHistoryUnavailable('Historical acquisition exhausted its shared deadline.')
            self._last_start = time.monotonic()
            connection = http.client.HTTPConnection(HOST, port=PORT, timeout=min(10, remaining))
            connection.request('GET', route, headers={'Accept': 'application/json',
                'Accept-Encoding': 'identity', 'Connection': 'close', 'User-Agent': 'Ariadne-Theta-EOD-Research/1'})
            check()
            response = connection.getresponse()
            if response.status != 200:
                raise ThetaHistoryUnavailable('Historical Terminal/entitlement request failed; no redirect or retry was attempted.')
            media = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            encoding = response.getheader('Content-Encoding', 'identity').strip().lower()
            length = response.getheader('Content-Length')
            if (media != 'application/json' or encoding not in ('', 'identity')
                    or length is not None and (not length.isdigit() or int(length) > maximum)):
                raise ThetaHistoryUnavailable('Historical Terminal response content or size is unsupported.')
            raw = bytearray()
            while True:
                self._allowed();check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ThetaHistoryUnavailable('Historical acquisition exhausted its shared deadline.')
                if connection.sock is not None:
                    connection.sock.settimeout(min(10, remaining))
                capacity = maximum - len(raw)
                if capacity <= 0:
                    raise ThetaHistoryUnavailable('Historical response reached its strict byte boundary without a complete-length proof.')
                block = response.read1(min(65536, capacity))
                if not block:
                    break
                raw.extend(block)
                if len(raw) > maximum:
                    raise ThetaHistoryUnavailable('Historical response exceeded its byte limit.')
                if length is not None and len(raw) == int(length):
                    break
            received = datetime.now(timezone.utc).isoformat()
            try:
                payload = bytes(raw).decode('utf-8')
            except UnicodeError:
                raise ThetaHistoryResponseError({'request': dict(request),
                    'payload_base64': base64.b64encode(raw).decode('ascii'),
                    'received_at': received, 'sha256': hashlib.sha256(raw).hexdigest(),
                    'bytes': len(raw), 'failure_code': 'invalid_utf8_response'}) from None
            return {'request': dict(request), 'payload': payload, 'received_at': received,
                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        except ThetaHistoryError:
            raise
        except (OSError, ValueError, http.client.HTTPException, OverflowError):
            raise ThetaHistoryUnavailable('Historical Terminal transport failed; no retry was attempted.') from None
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    def capture(self, manifest, *, window=None, check=None):
        window = scope.derive_window() if window is None else window
        selection = validate_manifest(manifest, window)
        self._allowed()
        check = (lambda: None) if check is None else check
        _require(callable(check), 'The owner-control check must be callable.')
        check()
        started = datetime.now(timezone.utc).isoformat()
        capture = {'schema': 1, 'source_id': SOURCE_ID, 'status': 'completed',
            'selection': selection, 'selection_window': window.as_dict(),
            'started_at': started, 'completed_at': started, 'responses': [], 'transport': dict(_TRANSPORT)}
        deadline = time.monotonic() + MAX_COLLECTION_SECONDS
        total, rows, index = 0, 0, 0
        try:
            for index, request in enumerate(selection['requests']):
                check()
                record = self._http(request, selection, deadline, check, remaining_bytes=MAX_TOTAL_BYTES-total)
                capture['responses'].append(record)
                total += record['bytes']
                _require(total <= MAX_TOTAL_BYTES, 'Historical acquisition exceeded its aggregate byte budget.')
                normalized, _ = _normalize_response(record, selection, window)
                rows += len(normalized)
                _require(rows <= MAX_TOTAL_ROWS, 'Historical acquisition exceeded its aggregate row budget.')
                check()
            capture['completed_at'] = datetime.now(timezone.utc).isoformat()
            check()
            normalize_capture(capture)
            return capture
        except Exception as error:
            if isinstance(error, ThetaHistoryResponseError):
                capture['failed_response'] = error.response_record
            capture['status'] = 'failed'
            capture['completed_at'] = datetime.now(timezone.utc).isoformat()
            capture['failed_request_index'] = index
            capture['failure_code'] = 'historical_capture_stopped'
            raise ThetaHistoryCaptureError(capture) from None


def _decimal(value):
    try:
        return snapshot._amount(value)
    except (ValueError, TypeError):
        raise ThetaHistoryError('Historical price is invalid; no repair or coercion is performed.') from None


def _integer(value):
    try:
        return snapshot._count(value)
    except (ValueError, TypeError):
        raise ThetaHistoryError('Historical count is invalid; no repair or coercion is performed.') from None


def _normalize_response(record, selection, window):
    _require(type(record) is dict and set(record) == _RESPONSE_KEYS, 'Exact historical response evidence fields required.')
    request = _request(record['request'])
    _require(type(record['payload']) is str, 'Historical response must retain actual UTF-8 text.')
    try:
        raw = record['payload'].encode('utf-8')
    except UnicodeError:
        raise ThetaHistoryError('Historical response UTF-8 is invalid.') from None
    _require(type(record['bytes']) is int and record['bytes'] == len(raw), 'Historical response byte count changed.')
    _require(type(record['sha256']) is str and hashlib.sha256(raw).hexdigest() == record['sha256'], 'Historical raw response hash changed.')
    received = _clock(record['received_at'])
    values = _json(raw, MAX_RESPONSE_BYTES)
    _require(type(values) is list and len(values) <= MAX_RESPONSE_ROWS, 'Historical response must be a bounded flat array.')
    option = request['kind'] == 'option'
    expected = snapshot._contract(request['contract_id']) if option else None
    rows, sessions = [], set()
    for value in values:
        allowed = _VALUE_KEYS | ({'symbol', 'expiration', 'strike', 'right'} if option else {'symbol'})
        required = {'symbol', 'expiration', 'strike', 'right', 'created'} if option else {'created'}
        _require(type(value) is dict and required <= set(value) <= allowed, 'Historical EOD row has unsupported or missing identity fields.')
        if option:
            _require(value['symbol'] == expected['symbol'] and value['expiration'] == expected['expiration']
                and value['right'] == expected['right'] and _decimal(value['strike']) is not None
                and Decimal(_decimal(value['strike'])) == Decimal(expected['strike']),
                'Historical option identity contradicts its explicit request.')
        elif 'symbol' in value:
            _require(value['symbol'] == request['symbol'], 'Historical stock identity contradicts its explicit request.')
        created = _time(value.get('created'), required=True)
        last = _time(value.get('last_trade'))
        _require(created['raw'] is not None, 'Historical report creation clock is required.')
        if created['ns'] is not None:
            _require(created['ns'] <= received['ns'], 'Report creation is later than its actual receipt.')
            session = exchange_day(datetime.fromisoformat(created['iso']))
        else:
            session = _date(created['raw'][:10])
        _require(is_session(session), 'Historical report date is not a reviewed exchange session.')
        scope.require_range(session.isoformat(), session.isoformat(), window)
        _require(selection['start_date'] <= session.isoformat() <= selection['end_date'], 'Historical row falls outside its explicit selected range.')
        _require(session <= exchange_day(datetime.fromisoformat(received['iso'])), 'Historical report date is after actual receipt date.')
        if option:
            _require(session <= _date(expected['expiration']), 'Historical option report follows contract expiry.')
        _require(session not in sessions, 'Duplicate historical instrument/session rows are refused.')
        sessions.add(session)
        if last['raw'] is not None:
            last_day = exchange_day(datetime.fromisoformat(last['iso'])) if last['iso'] is not None else _date(last['raw'][:10])
            _require(last_day <= session, 'Last-trade date is after its historical report session.')
        if last['ns'] is not None:
            _require(last['ns'] <= received['ns'], 'Last-trade clock is later than actual receipt.')
        if created['ns'] is not None and last['ns'] is not None:
            _require(last['ns'] <= created['ns'], 'Last trade occurs after report creation.')
        elif created['basis'] == last['basis'] == 'unverified-naive':
            _require(datetime.fromisoformat(last['raw']) <= datetime.fromisoformat(created['raw']), 'Naive last trade occurs after its report clock.')
        row = {'kind': request['kind'], 'symbol': expected['symbol'] if option else request['symbol'],
            'contract_id': request['contract_id'] if option else None, 'session': session.isoformat(),
            'created_raw': created['raw'], 'created_at': created['iso'], 'created_at_ns': created['ns'],
            'created_timezone_basis': created['basis'], 'last_trade_raw': last['raw'],
            'last_trade_at': last['iso'], 'last_trade_at_ns': last['ns'],
            'last_trade_timezone_basis': last['basis'], 'received_at': received['iso'],
            'received_at_raw': received['raw'], 'received_at_ns': received['ns'], 'quote_at': None, 'quote_event_clock_verified': False,
            'open_interest': None, 'standard_deliverable_verified': False,
            'research_only': True, 'execution_qualified': False}
        for field in ('open', 'high', 'low', 'close', 'bid', 'ask'):
            row[field] = _decimal(value.get(field))
        for field in ('volume', 'count', 'bid_size', 'ask_size', 'bid_exchange', 'ask_exchange', 'bid_condition', 'ask_condition'):
            row[field] = _integer(value.get(field))
        ohlc = [row[k] for k in ('open', 'high', 'low', 'close')]
        if all(v is not None for v in ohlc):
            opening, high, low, closing = map(Decimal, ohlc)
            _require(low <= opening <= high and low <= closing <= high, 'Historical OHLC values contradict their range.')
        row['issues'] = []
        if row['bid'] is not None and row['ask'] is not None and Decimal(row['bid']) > Decimal(row['ask']):
            row['issues'].append('crossed_eod_quote')
        if row['bid_size'] is None or row['ask_size'] is None:
            row['issues'].append('displayed_size_unknown')
        if row['bid_condition'] != 0 or row['ask_condition'] != 0:
            row['issues'].append('quote_condition_not_verified_regular')
        if created['iso'] is None:
            row['issues'].append('report_timezone_unverified')
        rows.append(row)
    expected_sessions = session_dates(_date(selection['start_date']), _date(selection['end_date']))
    if option:
        expected_sessions = [s for s in expected_sessions if s <= _date(expected['expiration'])]
    missing = [s.isoformat() for s in expected_sessions if s not in sessions]
    return rows, {'request': request, 'reported_sessions': sorted(s.isoformat() for s in sessions),
        'missing_report_sessions': missing, 'rows': len(rows),
        'status': 'no_reports' if not rows else 'incomplete_reports' if missing else 'requested_sessions_reported',
        'completeness_verified': False,
        'note': 'Missing reports are not zero returns, proof of no listed contract, or inferred zero volume. Contract listing dates are not established.'}


def normalize_capture(capture):
    """Pure replay of one original scope, not a new strategy study or live input."""
    _require(type(capture) is dict and set(capture) == _CAPTURE_KEYS, 'Exact completed historical capture schema required.')
    _require(type(capture['schema']) is int and capture['schema'] == 1
        and capture['source_id'] == SOURCE_ID and capture['status'] == 'completed', 'Only completed research-only EOD captures may normalize.')
    transport = capture['transport']
    _require(type(transport) is dict and transport == _TRANSPORT
        and type(transport.get('provider_authenticated')) is bool
        and type(transport.get('local_terminal_verified')) is bool,
        'Historical Terminal provenance must remain explicitly unattested.')
    window = _window_record(capture['selection_window'])
    selection = validate_manifest(capture['selection'], window)
    started, completed = _clock(capture['started_at']), _clock(capture['completed_at'])
    _require(0 <= completed['ns'] - started['ns'] <= MAX_COLLECTION_SECONDS * 1_000_000_000, 'Historical acquisition clocks are reversed or exceed the finite duration.')
    registered = _clock(window.registered_at)
    _require(registered['ns'] <= started['ns'], 'Selection was registered after acquisition began.')
    records = capture['responses']
    _require(type(records) is list and len(records) == len(selection['requests']), 'Every explicit request needs one retained response; missing requests are not silently adopted.')
    rows, coverage, total, previous = [], [], 0, started['ns']
    for request, record in zip(selection['requests'], records):
        _require(type(record) is dict and record.get('request') == request, 'Response request/order differs from the original manifest.')
        observed, diagnostics = _normalize_response(record, selection, window)
        at = _clock(record['received_at'])['ns']
        _require(previous <= at <= completed['ns'], 'Historical receipt clocks are reversed or outside acquisition.')
        previous = at
        total += record['bytes']
        _require(total <= MAX_TOTAL_BYTES, 'Historical capture exceeds its aggregate byte budget.')
        rows.extend(observed);coverage.append(diagnostics)
        _require(len(rows) <= MAX_TOTAL_ROWS, 'Historical capture exceeds its aggregate row budget.')
    return {'schema': 1, 'source_id': SOURCE_ID, 'kind': 'historical_eod_research_evidence',
        'selection': selection, 'selection_window': window.as_dict(),
        'capture_sha256': hashlib.sha256(canonical(capture)).hexdigest(),
        'actual_received_at': records[-1]['received_at'], 'capture_completed_at': capture['completed_at'],
        'acquisition_started_at': capture['started_at'], 'raw_response_bytes': total,
        'rows': rows, 'coverage': coverage, 'source_order_preserved': True,
        'provider_authenticated': False, 'local_terminal_verified': False,
        'research_only': True, 'execution_qualified': False, 'paper_operations': 0, 'model_calls': 0,
        'quote_event_clock_verified': False, 'created_clock_role': 'vendor_report_generation_not_quote_event',
        'declared_report_generation': {'wall_time': '17:15', 'timezone': 'America/New_York',
            'basis': 'Vendor documentation; raw response timezone is not inferred.',
            'url': 'https://thetadata.net/docs/operations/option_history_eod.html'},
        'limitations': ['EOD snapshots cannot establish intraday stop/target ordering or broker fills.',
            'The last reported NBBO may be stale relative to report creation; no quote-event clock is supplied.',
            'Free EOD supplies no dated open interest or verified standard deliverable.',
            'Historical access/entitlement/completeness remain unverified until actual account observations.',
            'A new performance study must separately admit these rows to its actual current research window.',
            'No canonical options dataset, performance result, paper account, policy or funding is created.']}


def import_capture(raw, *, expected_sha256):
    """Hash-pinned local import only; no filesystem, authentication or networking."""
    _require(type(expected_sha256) is str and re.fullmatch(r'[a-f0-9]{64}', expected_sha256) is not None,
        'Supply the exact lowercase SHA-256 of the retained local capture bytes.')
    _require(type(raw) is bytes and len(raw) <= MAX_IMPORT_BYTES
        and hashlib.sha256(raw).hexdigest() == expected_sha256, 'Local capture byte hash differs; no source repair is permitted.')
    return normalize_capture(_json(raw, MAX_IMPORT_BYTES))
