"""Bounded Alpaca OPRA/SIP quote collection, with no broker host or order surface.

Pure saved-capture normalization does not authenticate a file. Missing activity,
deliverables and source semantics remain missing and block new paper entries.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import http.client
import json
import os
import re
import ssl
import time
from .alpaca_credentials import validate
from .network_policy import install_network_policy

SOURCE_ID = 'alpaca_opra_v1'
HOST = 'data.alpaca.markets'
MAX_RAW = 256 * 1024
MAX_OPTIONS = 16
UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SYMBOL = re.compile(r'[A-Z][A-Z0-9]{0,5}\Z')
_OCC = re.compile(r'([A-Z][A-Z0-9]{0,5})([0-9]{6})([CP])([0-9]{8})\Z')
_CLOCK = re.compile(r'([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})\Z')


class AlpacaDataError(ValueError):
    """Invalid source evidence; error messages contain no response or credentials."""


class AlpacaDataUnavailable(AlpacaDataError):
    """Offline, authentication, entitlement, rate-limit or transport failure."""


def _json(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RAW:
        raise AlpacaDataError('Alpaca responses must be bounded UTF-8 JSON.')
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise AlpacaDataError('Alpaca response JSON is invalid or contains duplicate fields.') from None


def _clock(value, *, nullable=False):
    if value is None and nullable:
        return None
    match = _CLOCK.fullmatch(value) if type(value) is str else None
    if match is None:
        raise AlpacaDataError('Use actual aware RFC3339 clocks with seconds and at most nanosecond precision.')
    try:
        base = datetime.fromisoformat(match[1] + match[3].replace('Z', '+00:00'))
        if abs(base.utcoffset()) > timedelta(hours=14):
            raise ValueError
        base = base.astimezone(UTC)
        fraction = int((match[2] or '').ljust(9, '0'))
        delta = base - EPOCH
        exact_ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000 + fraction
        # Floor, never round forward. Exact ns remains attached to observations;
        # the old gate's microsecond clock slightly overstates age conservatively.
        projected = base + timedelta(microseconds=fraction // 1000)
        return {'raw': value, 'ns': exact_ns, 'iso': projected.isoformat()}
    except (ValueError, OverflowError):
        raise AlpacaDataError('Invalid aware source clock.') from None


def _amount(value):
    if value is None:
        return None
    if type(value) not in (int, Decimal):
        raise AlpacaDataError('Alpaca prices must be finite bounded JSON numbers.')
    amount = Decimal(value)
    if (not amount.is_finite() or amount < 0 or amount > Decimal('100000000000000')
            or amount.as_tuple().exponent < -8):
        raise AlpacaDataError('Alpaca prices exceed ordinary decimal bounds.')
    return format(amount, 'f')


def _count(value):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
        raise AlpacaDataError('Alpaca sizes must be bounded nonnegative integers.')
    return value


def _instrument(value):
    if type(value) is not dict or set(value) != {'symbol', 'type'}:
        raise AlpacaDataError('Use exact canonical symbol/type objects.')
    symbol, kind = value['symbol'], value['type']
    if kind == 'EQUITY' and type(symbol) is str and _SYMBOL.fullmatch(symbol):
        return dict(value)
    if kind == 'OPTION' and type(symbol) is str:
        match = _OCC.fullmatch(symbol)
        if match:
            try:
                expiry = date(2000 + int(match[2][:2]), int(match[2][2:4]), int(match[2][4:]))
                if expiry.year <= 2035 and int(match[4]) > 0:
                    return dict(value)
            except ValueError:
                pass
    raise AlpacaDataError('Only canonical US equity and option symbols are supported.')


def instruments(values):
    """One..sixteen explicit options and exactly their required underlyings."""
    if type(values) not in (list, tuple) or not 2 <= len(values) <= 2 * MAX_OPTIONS:
        raise AlpacaDataError('Request 1..16 explicit options and their underlying stocks.')
    rows = [_instrument(value) for value in values]
    keys = {(r['type'], r['symbol']) for r in rows}
    if len(keys) != len(rows):
        raise AlpacaDataError('Duplicate request instruments are refused.')
    options = [r['symbol'] for r in rows if r['type'] == 'OPTION']
    equities = {r['symbol'] for r in rows if r['type'] == 'EQUITY'}
    required = {_OCC.fullmatch(symbol)[1] for symbol in options}
    if not 1 <= len(options) <= MAX_OPTIONS or equities != required:
        raise AlpacaDataError('Include exactly the underlying stocks required by the explicit options.')
    return rows


class AlpacaMarketDataClient:
    """Fixed HTTPS GET routes; direct keys may have wider provider-side powers.

    This adapter's allowlist is not a broker-enforced read-only credential scope.
    No redirects, environment proxies, automatic retries or SDK dependencies.
    """
    def __init__(self, key_id, secret_key, *, online=False):
        if type(online) is not bool:
            raise AlpacaDataError('Online opt-in must be explicit true or false.')
        self._key_id, self._secret_key = validate(key_id, secret_key)
        self._online = online

    def close(self):
        self._key_id = self._secret_key = None

    def _http(self, operation, symbols):
        if (type(symbols) is not list or not 1 <= len(symbols) <= MAX_OPTIONS
                or any(type(symbol) is not str for symbol in symbols)
                or len(set(symbols)) != len(symbols)):
            raise AlpacaDataError('Use a bounded unique list of explicit quote symbols.')
        if operation == 'options':
            for symbol in symbols:
                _instrument({'type': 'OPTION', 'symbol': symbol})
            route = '/v1beta1/options/quotes/latest?symbols=' + ','.join(symbols) + '&feed=opra'
        elif operation == 'stocks':
            for symbol in symbols:
                _instrument({'type': 'EQUITY', 'symbol': symbol})
            route = '/v2/stocks/quotes/latest?symbols=' + ','.join(symbols) + '&feed=sip'
        else:
            raise AlpacaDataError('Only fixed OPRA option and SIP stock quote GET routes exist.')
        if (self._online is not True or os.environ.get('ARIADNE_OFFLINE') != '0'
                or install_network_policy()['mode'] != 'online'):
            raise AlpacaDataUnavailable('Alpaca collection requires explicit online opt-in and online process policy.')
        if self._key_id is None or self._secret_key is None:
            raise AlpacaDataUnavailable('Alpaca data client has been closed.')
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity',
            'Connection': 'close', 'User-Agent': 'Ariadne-Alpaca-Data/1',
            'APCA-API-KEY-ID': self._key_id, 'APCA-API-SECRET-KEY': self._secret_key}
        connection = response = None
        deadline = time.monotonic() + 15
        try:
            connection = http.client.HTTPSConnection(HOST, port=443, timeout=10,
                context=ssl.create_default_context())
            connection.request('GET', route, headers=headers)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AlpacaDataUnavailable('Alpaca quote collection exceeded its deadline.')
            if connection.sock is not None:
                connection.sock.settimeout(min(10, remaining))
            response = connection.getresponse()
            if response.status != 200:
                if response.status in (401, 403):
                    raise AlpacaDataUnavailable('Alpaca credentials or OPRA/SIP entitlements are unavailable; no indicative fallback.')
                if response.status == 429:
                    raise AlpacaDataUnavailable('Alpaca rate limit reached; collection stopped without retry.')
                raise AlpacaDataUnavailable('Alpaca data request failed; no redirect or retry was attempted.')
            media = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            encoding = response.getheader('Content-Encoding', 'identity').strip().lower()
            length = response.getheader('Content-Length')
            if media != 'application/json' or encoding not in ('', 'identity'):
                raise AlpacaDataUnavailable('Alpaca returned unsupported content.')
            if length is not None and (not length.isdigit() or int(length) > MAX_RAW):
                raise AlpacaDataUnavailable('Alpaca response exceeds its content bound.')
            raw = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AlpacaDataUnavailable('Alpaca quote collection exceeded its deadline.')
                if connection.sock is not None:
                    connection.sock.settimeout(min(10, remaining))
                block = response.read1(min(65536, MAX_RAW + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
                if len(raw) > MAX_RAW:
                    raise AlpacaDataUnavailable('Alpaca response exceeds its content bound.')
            received = datetime.now(UTC).isoformat()
            value = _json(bytes(raw))
            if type(value) is not dict:
                raise AlpacaDataUnavailable('Alpaca returned an unsupported response object.')
            return {'payload': bytes(raw).decode('utf-8'), 'received_at': received}
        except AlpacaDataError:
            raise
        except (OSError, ValueError, http.client.HTTPException, OverflowError):
            raise AlpacaDataUnavailable('Alpaca data transport failed; no retry was attempted.') from None
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    def quotes(self, requested):
        rows = instruments(requested)
        options = self._http('options', [r['symbol'] for r in rows if r['type'] == 'OPTION'])
        stocks = self._http('stocks', [r['symbol'] for r in rows if r['type'] == 'EQUITY'])
        received = max((options['received_at'], stocks['received_at']), key=lambda value: _clock(value)['ns'])
        capture = {'schema': 1, 'source_id': SOURCE_ID, 'received_at': received,
            'options': options, 'stocks': stocks, 'transport': {'source_id': SOURCE_ID,
                'operation': 'latest_quotes', 'provider_authenticated': True,
                'options_feed': 'opra', 'stocks_feed': 'sip'}}
        normalize_quotes(capture, expected_instruments=rows)
        return capture


def _part(part, kind):
    if (type(part) is not dict or set(part) != {'payload', 'received_at'}
            or type(part['payload']) is not str or not 1 <= len(part['payload']) <= MAX_RAW):
        raise AlpacaDataError('Use exact bounded quote-response and actual-receipt objects.')
    try:
        raw = part['payload'].encode('utf-8')
    except UnicodeError:
        raise AlpacaDataError('Alpaca response must be valid UTF-8.') from None
    value = _json(raw)
    if type(value) is not dict or set(value) != {'quotes'} or type(value['quotes']) is not dict:
        raise AlpacaDataError('Alpaca quote response must contain the exact quotes object.')
    if not 1 <= len(value['quotes']) <= MAX_OPTIONS:
        raise AlpacaDataError('Alpaca quote response exceeds its explicit scope.')
    received = _clock(part['received_at'])
    rows = []
    for symbol, row in value['quotes'].items():
        instrument = _instrument({'symbol': symbol, 'type': kind})
        allowed = {'t', 'bp', 'bs', 'bx', 'ap', 'as', 'ax', 'c'}
        if kind == 'EQUITY':
            allowed.add('z')
        if type(row) is not dict or not set(row) <= allowed:
            raise AlpacaDataError('Alpaca quote fields are invalid or unsupported.')
        condition = row.get('c')
        if kind == 'OPTION' and condition != ' ':
            raise AlpacaDataError('Only explicitly regular firm OPRA quote conditions are supported.')
        if kind == 'EQUITY' and condition != ['R']:
            raise AlpacaDataError('Only explicitly regular SIP stock quote conditions are supported.')
        for name in ('bx', 'ax'):
            exchange = row.get(name)
            if (exchange is not None and (type(exchange) is not str or not re.fullmatch(r'[A-Za-z0-9]{1,4}', exchange))):
                raise AlpacaDataError('Quote exchange identifiers are invalid.')
        clock = _clock(row.get('t'), nullable=True)
        if clock is not None and clock['ns'] > received['ns']:
            raise AlpacaDataError('Alpaca quote event occurs after its actual response receipt.')
        observation = {'instrument': instrument, 'bid': _amount(row.get('bp')),
            'ask': _amount(row.get('ap')), 'bid_size': _count(row.get('bs')),
            'ask_size': _count(row.get('as')), 'quote_kind': 'nbbo_message',
            'quote_at': None if clock is None else clock['iso'],
            'quote_at_raw': None if clock is None else clock['raw'],
            'quote_at_ns': None if clock is None else clock['ns'],
            'received_at': received['iso'], 'received_at_raw': received['raw'], 'received_at_ns': received['ns'],
            'condition': condition, 'bid_exchange': row.get('bx'), 'ask_exchange': row.get('ax')}
        rows.append(observation)
    return rows, received, hashlib.sha256(raw).hexdigest()


def normalize_quotes(capture, *, expected_instruments=None):
    """Pure gate projection; source bytes and exact ns clocks remain auditable.

    A supplied transport marker never grants authentication. This feed reports
    one NBBO message clock, not individual bid/ask last-change clocks. All
    deliverable, classification, ADV, contract-volume and OI proofs stay absent.
    """
    keys = {'schema', 'source_id', 'received_at', 'options', 'stocks', 'transport'}
    if (type(capture) is not dict or set(capture) != keys
            or type(capture['schema']) is not int or capture['schema'] != 1
            or capture['source_id'] != SOURCE_ID):
        raise AlpacaDataError('Use the exact Alpaca OPRA capture schema.')
    transport = capture['transport']
    tkeys = {'source_id', 'operation', 'provider_authenticated', 'options_feed', 'stocks_feed'}
    if (type(transport) is not dict or set(transport) != tkeys
            or transport['source_id'] != SOURCE_ID or transport['operation'] != 'latest_quotes'
            or type(transport['provider_authenticated']) is not bool
            or transport['options_feed'] != 'opra' or transport['stocks_feed'] != 'sip'):
        raise AlpacaDataError('Only explicit OPRA options and SIP stock feed captures are supported.')
    options, options_received, options_hash = _part(capture['options'], 'OPTION')
    stocks, stocks_received, stocks_hash = _part(capture['stocks'], 'EQUITY')
    received = _clock(capture['received_at'])
    if received['ns'] != max(options_received['ns'], stocks_received['ns']):
        raise AlpacaDataError('The capture clock must equal its latest actual response receipt.')
    observations = options + stocks
    actual = instruments([row['instrument'] for row in observations])
    if expected_instruments is not None:
        expected = instruments(expected_instruments)
        if {(r['type'], r['symbol']) for r in expected} != {(r['type'], r['symbol']) for r in actual}:
            raise AlpacaDataError('Alpaca response does not match the explicit requested instruments.')
    by_symbol = {row['instrument']['symbol']: row for row in stocks}
    digest_input = json.dumps({'source_id': SOURCE_ID, 'options_sha256': options_hash,
        'stocks_sha256': stocks_hash, 'options_received_at': options_received['raw'],
        'stocks_received_at': stocks_received['raw']}, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(digest_input).hexdigest()
    books, blockers = [], []
    for observation in options:
        symbol = observation['instrument']['symbol']
        match = _OCC.fullmatch(symbol)
        underlying = match[1]
        equity = by_symbol[underlying]
        # Use the actual ask conservatively for the under-$100 requirement,
        # never a midpoint, stale last trade or the cheaper side of a spread.
        underlying_quote = {'price': equity['ask'], 'quote_at': equity['quote_at'],
            'received_at': equity['received_at']}
        book = {'schema': 1, 'source_id': SOURCE_ID, 'snapshot_id': 'alpaca-' + digest[:32],
            'quote_kind': 'top_of_book', 'underlying_symbol': underlying,
            'underlying_type': 'unverified', 'contract_id': symbol,
            'expiry': date(2000 + int(match[2][:2]), int(match[2][2:4]), int(match[2][4:])).isoformat(),
            'type': 'call' if match[3] == 'C' else 'put',
            'strike': str(int(match[4]) // 1000) + '.' + str(int(match[4]) % 1000).zfill(3),
            'multiplier': 100, 'standard_deliverable_verified': False,
            'deliverable_verification_source': None, 'bid': observation['bid'],
            'ask': observation['ask'], 'bid_size': observation['bid_size'],
            'ask_size': observation['ask_size'], 'quote_at': observation['quote_at'],
            'received_at': observation['received_at'], 'underlying_quote': underlying_quote,
            'underlying_activity': None, 'contract_activity': None, 'open_interest': None}
        books.append(book)
        reasons = ['underlying_classification_unverified', 'standard_deliverable_unverified',
            'underlying_adv20_missing', 'contract_volume_clock_missing',
            'open_interest_session_and_publication_missing', 'per_side_last_change_clocks_not_supplied']
        if observation['quote_at'] is None:
            reasons.append('actual_quote_clock_missing')
        if observation['bid_size'] is None or observation['ask_size'] is None:
            reasons.append('displayed_size_missing')
        if equity['ask'] is None or equity['quote_at'] is None:
            reasons.append('underlying_quote_missing')
        blockers.append({'contract_id': symbol, 'reasons': reasons})
    return {'schema': 1, 'source_id': SOURCE_ID, 'raw_sha256': digest,
        'received_at': received['iso'], 'received_at_raw': received['raw'],
        'received_at_ns': received['ns'], 'provider_authenticated': False,
        'books': books, 'observations': observations, 'blockers': blockers}
