"""Pure readiness checks for a hash-bound, completed Theta direct EOD capture.

No source collection, decompression/native imports, timestamp projection, orders,
filesystem access or account access. Raw frame hashes and the stored typed
projection are checked separately: compressed protobuf is NOT redecoded here.
A valid report is evidence of internal consistency, never source authentication
or execution quality. The current single-contract source cannot compare puts
with calls, and its report-generation clock never becomes a quote-event clock.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
import re
from zoneinfo import ZoneInfo

from . import research_window as scope
from .qqq_data import (CALENDAR_START, CALENDAR_END, _nth_weekday,
                       is_session, session_dates)

SOURCE_ID = 'theta_direct_free_eod_research_v1'
CALENDAR_TZIF_SHA256 = 'c27b739ff46a7df0594e120d725b439217e11e44ea9a50cdc49130383b5482e7'
MAX_CAPTURE_BYTES = 24 * 1024 * 1024
MAX_RECEIPT_BYTES = 256 * 1024
MAX_FRAME_BYTES = 512 * 1024
MAX_WIRE_BYTES = 4 * 1024 * 1024
MAX_DECODED_BYTES = 8 * 1024 * 1024
MAX_FRAME_DECODED = 1024 * 1024
MAX_FRAMES, MAX_ROWS = 128, 4096
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')
_CONTRACT = re.compile(r'([A-Z]{1,6})([0-9]{6})([CP])([0-9]{8})\Z')
_SYMBOL = re.compile(r'[A-Z]{1,6}\Z')
_PRICES = frozenset(('open', 'high', 'low', 'close', 'bid', 'ask'))
_COUNTS = frozenset(('volume', 'count', 'bid_size', 'ask_size', 'bid_exchange',
                    'ask_exchange', 'bid_condition', 'ask_condition'))
_VALUE_KEYS = _PRICES | _COUNTS | {'created', 'last_trade'}
_CAPTURE_KEYS = frozenset(('schema', 'kind', 'status', 'source_id', 'selection',
    'allowed_symbols', 'selection_window', 'started_at', 'completed_at',
    'preflight_sha256', 'operations', 'responses', 'rows', 'transport',
    'open_interest_available', 'quote_event_available', 'paper_fills',
    'source_report_clock_basis', 'submillisecond_semantics', 'missing_report_sessions',
    'payload_bytes', 'decoded_bytes', 'empty_response'))
_RECEIPT_REQUIRED = frozenset(('schema', 'status', 'kind', 'ordinal', 'completed_at',
    'authorization_sha256', 'protocol_amendment_sha256', 'operations_reserved',
    'market_evidence', 'reported_rows', 'terminal_hold_preserved',
    'automatic_continuation', 'live_execution_eligible', 'application_launched',
    'paper_fills', 'funding_added'))
_RECEIPT_OPTIONAL = frozenset(('no_new_budget_namespace_created',
    'original_three_attempt_allowance_renewed', 'prior_additional_allowance_renewed'))
_ROW_KEYS = frozenset(('report_day', 'values', 'quote_event_at', 'open_interest',
                       'standard_deliverable_verified', 'live_execution_eligible'))
_SUBMILLI = 'Raw epoch_ms/nanos/zone/precision retained; no reconstructed epoch_ns.'
_STAMP_SUBMILLI = 'preserved_raw_not_reconstructed'
_REPORT_BASIS = 'Provider EOD report generation, documented17:15ET; not quote-event time.'


class ThetaResearchError(ValueError):
    """Invalid or incomplete provenance is rejected without echoing inputs."""


def _require(condition):
    if not condition:
        raise ThetaResearchError('Theta EOD research evidence failed validation.')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    _require(type(value) is str and _HASH.fullmatch(value) is not None)
    return value


def _json(raw, expected, maximum):
    _require(type(raw) is bytes and 1 <= len(raw) <= maximum)
    _require(_hash(raw) == _digest(expected))
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    def integer(text):
        _require(len(text) <= 20)
        return int(text)
    def invalid(_):
        _require(False)
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_int=integer, parse_float=invalid, parse_constant=invalid)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ThetaResearchError('Theta EOD research evidence failed validation.') from None
    _require(type(result) is dict)
    return result


def _day(value):
    _require(type(value) is str and _DATE.fullmatch(value) is not None)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ThetaResearchError('Theta EOD research evidence failed validation.') from None


def _clock(value):
    _require(type(value) is str and 19 <= len(value) <= 64)
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        _require(result.tzinfo is not None and result.utcoffset() is not None)
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ThetaResearchError('Theta EOD research evidence failed validation.') from None


def _int(value, maximum, minimum=0):
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def _price(value):
    _require(type(value) is dict and type(value.get('kind')) is str)
    kind = value['kind']
    if kind == 'unset':
        _require(value == {'kind': 'unset', 'value': None})
        return None
    if kind == 'null':
        _require(value == {'kind': 'null', 'value': None})
        return None
    _require(kind == 'price' and set(value) == {'kind', 'integer', 'scale_type', 'decimal'})
    integer = _int(value['integer'], (1 << 31)-1)
    scale = _int(value['scale_type'], 19)
    digits = tuple(int(digit) for digit in str(integer))
    expected = None if scale == 0 else format(Decimal((0, digits, scale-10)), 'f')
    _require(value['decimal'] is None if expected is None else
             type(value['decimal']) is str and value['decimal'] == expected)
    return None if expected is None else Decimal(expected)


def _number(value):
    _require(type(value) is dict)
    if value == {'kind': 'unset', 'value': None} or value == {'kind': 'null', 'value': None}:
        return None
    _require(set(value) == {'kind', 'value'} and value['kind'] == 'number')
    return _int(value['value'], (1 << 63)-1)


def _stamp(value, optional=False):
    _require(type(value) is dict)
    if optional and (value == {'kind': 'unset', 'value': None} or value == {'kind': 'null', 'value': None}):
        return None
    _require(set(value) == {'kind', 'epoch_ms', 'nanos', 'zone', 'precision',
                           'epoch_ns', 'submillisecond_semantics'} and value['kind'] == 'timestamp')
    millis = _int(value['epoch_ms'], 253402300799999, 1)
    _int(value['nanos'], (1 << 32)-1)
    _int(value['zone'], 1)
    _int(value['precision'], 1)
    _require(value['epoch_ns'] is None and value['submillisecond_semantics'] == _STAMP_SUBMILLI)
    try:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)+timedelta(milliseconds=millis)
    except (ValueError, OverflowError):
        raise ThetaResearchError('Theta EOD research evidence failed validation.') from None


def _selection(value):
    _require(type(value) is dict and value.get('kind') in ('stock', 'option'))
    option = value['kind'] == 'option'
    _require(set(value) == {'kind', 'start_date', 'end_date', 'contract_id' if option else 'symbol'})
    start, end = _day(value['start_date']), _day(value['end_date'])
    _require(start <= end and is_session(end))
    if not option:
        symbol = value['symbol']
        _require(type(symbol) is str and _SYMBOL.fullmatch(symbol) is not None)
        return symbol, None, start, end
    identifier = value['contract_id']
    _require(type(identifier) is str)
    match = _CONTRACT.fullmatch(identifier)
    _require(match is not None)
    try:
        expiry = date(2000+int(match[2][:2]), int(match[2][2:4]), int(match[2][4:]))
    except ValueError:
        raise ThetaResearchError('Theta EOD research evidence failed validation.') from None
    strike_digits = int(match[4])
    strike = Decimal(str(strike_digits//1000)+'.'+str(strike_digits%1000).zfill(3))
    _require(strike > 0)
    return match[1], {'contract_id': identifier, 'expiry': expiry,
        'strike': strike, 'right': 'call' if match[3] == 'C' else 'put'}, start, end


def _path(value):
    _require(type(value) is str and 1 <= len(value) <= 512 and value.isascii()
             and value.startswith('staging/') and value.endswith('/research.json')
             and '\\' not in value and ':' not in value)
    _require(all(part and part not in ('.', '..') and all(32 <= ord(c) <= 126 for c in part)
                 for part in value.split('/')))


def _calendar(raw):
    # The caller supplies and leases the bundled public calendar bytes. This
    # never asks ZoneInfo to discover files on the host or mutate its cache.
    _require(type(raw) is bytes and 1 <= len(raw) <= 4096 and _hash(raw) == CALENDAR_TZIF_SHA256)
    try:
        return ZoneInfo.from_file(io.BytesIO(raw), key='America/Chicago')
    except (ValueError, OSError):
        raise ThetaResearchError('The fixed in-memory market calendar is invalid.') from None


def _exchange_day(clock, zone):
    # Exactly the existing qqq_data calendar convention: Chicago plus one hour.
    return (clock.astimezone(zone)+timedelta(hours=1)).date()


def _window_metadata(registered, zone):
    # Same date/early-close/finalization rules as research_window + qqq_data,
    # with the already loaded in-memory zone instead of their file-reading _tz.
    scope._verify()
    clock = _clock(registered)
    anchor = _exchange_day(clock, zone)
    _require(CALENDAR_START+1 <= anchor.year <= CALENDAR_END)
    try:
        oldest = anchor.replace(year=anchor.year-1)
    except ValueError:
        oldest = anchor.replace(year=anchor.year-1, day=28)
    newest = None
    for offset in range(15):
        candidate = anchor-timedelta(days=offset)
        if not is_session(candidate):
            continue
        thanksgiving = _nth_weekday(candidate.year, 11, 3, 4)
        early = (candidate == thanksgiving+timedelta(days=1)
            or candidate.month == 12 and candidate.day == 24
            or candidate.month == 7 and candidate.day == 3)
        close = datetime(candidate.year, candidate.month, candidate.day,
            12 if early else 15, tzinfo=zone).astimezone(timezone.utc)
        if clock >= close+timedelta(minutes=15):
            newest = candidate
            break
    _require(newest is not None)
    return {'schema':1, 'kind':'rolling_12_calendar_month_research_window',
        'registered_at':clock.isoformat(), 'anchor_day':anchor.isoformat(),
        'oldest':oldest.isoformat(), 'newest':newest.isoformat(),
        'market_timezone':'America/New_York', 'calendar_months':12, 'inclusive':True,
        'rule_sha256':scope.RULE_SHA256, 'pre_window_option_rows_allowed':False,
        'older_benchmark_role':'causal_context_warmup_only', 'source_clocks_rewritten':False}


def _admitted_window(window, zone):
    _require(type(window) is scope.ResearchWindow)
    expected = _window_metadata(window.registered_at, zone)
    _require((window.registered_at, window.anchor_day, window.oldest, window.newest, window.rule_sha256)
        == tuple(expected[key] for key in ('registered_at','anchor_day','oldest','newest','rule_sha256')))
    return expected


def _completed(capture_raw, receipt_raw, capture_sha, receipt_sha, window, zone):
    admitted_window = _admitted_window(window, zone)
    registered = _clock(admitted_window['registered_at'])
    capture = _json(capture_raw, capture_sha, MAX_CAPTURE_BYTES)
    receipt = _json(receipt_raw, receipt_sha, MAX_RECEIPT_BYTES)
    _require(_RECEIPT_REQUIRED <= set(receipt) <= _RECEIPT_REQUIRED | _RECEIPT_OPTIONAL)
    _require(type(receipt['schema']) is int and receipt['schema'] == 1
        and receipt['status'] == 'completed' and receipt['kind'] == 'theta_direct_eod_qualification')
    _int(receipt['ordinal'], 1000, 1)
    for name in ('authorization_sha256', 'protocol_amendment_sha256'):
        _digest(receipt[name])
    _require(receipt['terminal_hold_preserved'] is True and receipt['automatic_continuation'] is False
        and receipt['live_execution_eligible'] is False and receipt['application_launched'] is False
        and type(receipt['paper_fills']) is int and receipt['paper_fills'] == 0 and receipt['funding_added'] == '0')
    for name in _RECEIPT_OPTIONAL & set(receipt):
        _require(receipt[name] is (True if name == 'no_new_budget_namespace_created' else False))
    artifact = receipt['market_evidence']
    _require(type(artifact) is dict and set(artifact) == {'path', 'bytes', 'sha256'})
    _path(artifact['path'])
    _require(type(artifact['bytes']) is int and artifact['bytes'] == len(capture_raw)
        and artifact['sha256'] == capture_sha)
    _require(set(capture) == _CAPTURE_KEYS and type(capture['schema']) is int and capture['schema'] == 1
        and capture['kind'] == 'theta_direct_eod_capture' and capture['status'] == 'completed'
        and capture['source_id'] == SOURCE_ID)
    symbol, contract, first, last = _selection(capture['selection'])
    allowed = capture['allowed_symbols']
    _require(type(allowed) is list and 1 <= len(allowed) <= 4
        and all(type(root) is str and _SYMBOL.fullmatch(root) is not None for root in allowed)
        and len(set(allowed)) == len(allowed) and symbol in allowed)
    original = capture['selection_window']
    _require(type(original) is dict and type(original.get('registered_at')) is str)
    original_window = _window_metadata(original['registered_at'], zone)
    _require(set(original_window) == set(original) and all(type(original[key]) is type(value)
        and original[key] == value for key, value in original_window.items()))
    for admitted in (original_window, admitted_window):
        _require(_day(admitted['oldest']) <= first <= last <= _day(admitted['newest']))
    _require(last < _day(original_window['anchor_day']))
    started, finished = _clock(capture['started_at']), _clock(capture['completed_at'])
    _require(_clock(original_window['registered_at']) <= started <= finished
        and timedelta(0) <= finished-started <= timedelta(seconds=300)
        and finished <= _clock(receipt['completed_at']) <= registered)
    _digest(capture['preflight_sha256'])
    operations = {'authentication': 1, 'eod_rpc': 1}
    _require(capture['operations'] == operations and receipt['operations_reserved'] == operations
        and all(type(n) is int for n in capture['operations'].values())
        and all(type(n) is int for n in receipt['operations_reserved'].values()))
    transport = capture['transport']
    _require(type(transport) is dict and set(transport) == {'kind', 'provider_authenticated',
        'market_data_cryptographically_signed', 'live_execution_eligible',
        'rpc_payload_bytes_exclude_transport_overhead'})
    _require(transport['kind'] in ('vendor_direct_https_grpc', 'synthetic_test')
        and transport['provider_authenticated'] is (transport['kind'] == 'vendor_direct_https_grpc')
        and transport['market_data_cryptographically_signed'] is False
        and transport['live_execution_eligible'] is False
        and transport['rpc_payload_bytes_exclude_transport_overhead'] is True)
    _require(capture['open_interest_available'] is False and capture['quote_event_available'] is False
        and type(capture['paper_fills']) is int and capture['paper_fills'] == 0
        and capture['source_report_clock_basis'] == _REPORT_BASIS
        and capture['submillisecond_semantics'] == _SUBMILLI)
    rows, frames = capture['rows'], capture['responses']
    _require(type(rows) is list and len(rows) <= MAX_ROWS and type(frames) is list and len(frames) <= MAX_FRAMES)
    _require(type(receipt['reported_rows']) is int and receipt['reported_rows'] == len(rows)
        and type(capture['empty_response']) is bool and capture['empty_response'] == (not rows))
    cursor = payload_bytes = decoded_bytes = 0
    previous_receipt = started
    days, stock_closes, temporal, dtes = [], {}, 0, []
    for frame in frames:
        _require(type(frame) is dict and set(frame) == {'received_at', 'payload_base64', 'sha256',
            'bytes', 'rows', 'compression', 'declared_original_size', 'decoded_bytes'})
        received = _clock(frame['received_at'])
        _require(previous_receipt <= received <= finished)
        previous_receipt = received
        count = _int(frame['rows'], MAX_ROWS)
        size = _int(frame['bytes'], MAX_FRAME_BYTES, 1)
        encoded = frame['payload_base64']
        _require(type(encoded) is str and len(encoded) <= ((MAX_FRAME_BYTES+2)//3)*4)
        try:
            body = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError):
            raise ThetaResearchError('Theta EOD research evidence failed validation.') from None
        _require(len(body) == size and _hash(body) == _digest(frame['sha256'])
            and base64.b64encode(body).decode('ascii') == encoded)
        decoded = _int(frame['decoded_bytes'], MAX_FRAME_DECODED, 1)
        _require(frame['compression'] in ('NONE', 'ZSTD')
            and type(frame['declared_original_size']) is int and frame['declared_original_size'] in (0, decoded))
        payload_bytes += size
        decoded_bytes += decoded
        _require(payload_bytes <= MAX_WIRE_BYTES and decoded_bytes <= MAX_DECODED_BYTES and cursor+count <= len(rows))
        for row in rows[cursor:cursor+count]:
            _require(type(row) is dict and set(row) == _ROW_KEYS
                and row['quote_event_at'] is None and row['open_interest'] is None
                and row['standard_deliverable_verified'] is False and row['live_execution_eligible'] is False)
            day = _day(row['report_day'])
            _require(first <= day <= last and is_session(day) and (not days or row['report_day'] > days[-1]))
            values = row['values']
            expected = _VALUE_KEYS | ({'symbol', 'expiration', 'strike', 'right'} if contract else set())
            _require(type(values) is dict and set(values) in (expected, expected | {'symbol'}))
            created, trade = _stamp(values['created']), _stamp(values['last_trade'], optional=True)
            _require(_exchange_day(created, zone) == day and created <= received and (trade is None or trade <= created))
            prices = {key: _price(values[key]) for key in _PRICES}
            for key in _COUNTS:
                _number(values[key])
            if 'symbol' in values:
                _require(values['symbol'] == {'kind': 'text', 'value': symbol})
            if contract:
                _require(values['expiration'] == {'kind': 'text', 'value': contract['expiry'].isoformat()}
                    and type(values['right']) is dict and set(values['right']) == {'kind', 'value'}
                    and values['right']['kind'] == 'text' and type(values['right']['value']) is str
                    and values['right']['value'].lower() in
                        (('put', 'p') if contract['right'] == 'put' else ('call', 'c'))
                    and _price(values['strike']) == contract['strike'])
                dte = (contract['expiry']-day).days
                dtes.append(dte)
                temporal += 7 <= dte <= 21 and contract['expiry'].year == day.year and contract['expiry'].month == day.month
            else:
                stock_closes[row['report_day']] = prices['close']
            days.append(row['report_day'])
        cursor += count
    _require(cursor == len(rows) and type(capture['payload_bytes']) is int and capture['payload_bytes'] == payload_bytes
        and type(capture['decoded_bytes']) is int and capture['decoded_bytes'] == decoded_bytes)
    missing = [day.isoformat() for day in session_dates(first, last) if day.isoformat() not in set(days)]
    _require(capture['missing_report_sessions'] == missing)
    return {'capture': capture, 'symbol': symbol, 'contract': contract, 'days': days,
        'stock_closes': stock_closes, 'temporal_rows': temporal, 'dtes': dtes, 'window': admitted_window}


def assess_capture(capture_raw, completed_raw, *, expected_capture_sha256,
                   expected_completed_sha256, window, calendar_tzif_raw, paired_stock_raw=None,
                   paired_stock_completed_raw=None, expected_stock_sha256=None,
                   expected_stock_completed_sha256=None):
    """Return bounded readiness facts without prices, raw payloads or mutations.

    The caller supplies the existing bundled calendar bytes; this function
    validates their fixed hash and reads only memory. Optional matching stock
    evidence must carry its own completed receipt and
    both exact hashes. It is evidence for this cohort, never a replacement for
    an owner source. Single-contract captures cannot establish a call/put
    comparison; a separate future multi-contract study contract is needed.
    """
    zone = _calendar(calendar_tzif_raw)
    evidence = _completed(capture_raw, completed_raw, expected_capture_sha256,
                          expected_completed_sha256, window, zone)
    _require(evidence['contract'] is not None)
    capture, contract, days = evidence['capture'], evidence['contract'], evidence['days']
    supplied = (paired_stock_raw, paired_stock_completed_raw, expected_stock_sha256,
                expected_stock_completed_sha256)
    _require(all(value is None for value in supplied) or all(value is not None for value in supplied))
    stock = None
    if paired_stock_raw is not None:
        stock = _completed(paired_stock_raw, paired_stock_completed_raw, expected_stock_sha256,
                           expected_stock_completed_sha256, window, zone)
        _require(stock['contract'] is None and stock['symbol'] == evidence['symbol'])
    matching = 0 if stock is None else sum(day in stock['stock_closes']
        and stock['stock_closes'][day] is not None and stock['stock_closes'][day] > 0 for day in days)
    below100 = None if stock is None else sum(day in stock['stock_closes']
        and stock['stock_closes'][day] is not None and 0 < stock['stock_closes'][day] < 100 for day in days)
    reasons = []
    if not days:
        reasons.append('empty_option_history')
    if evidence['temporal_rows'] != len(days) or not days:
        reasons.append('short_dte_same_month_policy_incompatible')
    if stock is None:
        reasons.append('paired_underlying_not_supplied')
    elif matching != len(days):
        reasons.append('paired_underlying_coverage_incomplete')
    if stock is not None and below100 != len(days):
        reasons.append('below_100_underlying_gate_not_established')
    reasons.append('recovery_call_side_missing' if contract['right'] == 'put' else 'stress_put_side_missing')
    reasons.extend(('quote_event_clock_unknown', 'open_interest_unknown',
                    'standard_deliverable_unverified', 'single_contract_is_not_strategy_validation'))
    return {'schema': 1, 'kind': 'theta_direct_eod_research_readiness',
        'calendar_tzif_sha256': CALENDAR_TZIF_SHA256,
        'capture_sha256': expected_capture_sha256, 'completed_receipt_sha256': expected_completed_sha256,
        'source_id': SOURCE_ID, 'source_kind': capture['transport']['kind'],
        'provider_authenticated_claim': capture['transport']['provider_authenticated'],
        'source_authentication_independently_verified': False,
        'completed_artifact_hash_checked': True, 'raw_frame_hashes_checked': True,
        'typed_prices_clocks_identity_checked': True, 'wire_projection_redecoded': False,
        'symbol': evidence['symbol'], 'contract_id': contract['contract_id'], 'right': contract['right'],
        'requested_start': capture['selection']['start_date'], 'requested_end': capture['selection']['end_date'],
        'observed_start': days[0] if days else None, 'observed_end': days[-1] if days else None,
        'option_rows': len(days), 'frames': len(capture['responses']),
        'missing_requested_sessions': len(capture['missing_report_sessions']),
        'minimum_dte': min(evidence['dtes']) if days else None,
        'maximum_dte': max(evidence['dtes']) if days else None,
        'short_dte_same_month_rows': evidence['temporal_rows'],
        'paired_underlying_supplied': stock is not None, 'matching_underlying_rows': matching,
        'below_100_underlying_rows': below100,
        'call_side_present': contract['right'] == 'call', 'put_side_present': contract['right'] == 'put',
        'quote_event_available': False, 'open_interest_available': False,
        'standard_deliverable_verified': False, 'historical_strategy_comparison_ready': False,
        'paper_trading_eligible': False, 'readiness_reasons': reasons,
        'study_window': evidence['window'], 'clocks_projected_or_rewritten': False,
        'financial_authority': False, 'file_operations': 0, 'network_calls': 0,
        'native_provider_imports': 0, 'model_calls': 0, 'owner_operations': 0, 'paper_fills': 0}
