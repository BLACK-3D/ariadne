"""Read-only Theta Terminal v3 snapshots over one fixed numeric loopback address.

The Terminal authenticates upstream separately. Loopback is not provider
identity: captures always remain unattested and cannot authorize paper fills.
No key, Terminal installer, JVM, process launch or account mutation exists here.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import http.client
import json
import os
import re
import time
from .network_policy import install_network_policy

SOURCE_ID = 'theta_terminal_v3'
HOST = '127.0.0.1'
PORT = 25503
MAX_RAW = 128 * 1024
MAX_OPTIONS = 4
MAX_COLLECTION_SECONDS = 15
UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SYMBOL = re.compile(r'[A-Z][A-Z0-9]{0,5}\Z')
_OCC = re.compile(r'([A-Z][A-Z0-9]{0,5})([0-9]{6})([CP])([0-9]{8})\Z')
_AWARE = re.compile(r'([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})\Z')
_NAIVE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}\Z')
# Reviewed snapshot docs specify a naive millisecond format but not a response
# timezone. An at-time request parameter's ET basis does not prove this field's
# timezone. Preserve it as missing UTC evidence until independently qualified.
SNAPSHOT_TIMEZONE = None
TIMEZONE_DOCUMENTATION = 'https://thetadata.net/docs/operations/option_snapshot_quote.html'
CONDITION_DOCUMENTATION = 'https://thetadata.net/docs/Articles/Errors-Exchanges-Conditions/Quote-Conditions.html'


class ThetaDataError(ValueError):
    """Malformed bounded local evidence; errors never echo source bodies."""


class ThetaDataUnavailable(ThetaDataError):
    """Offline, unavailable Terminal/entitlement or bounded transport failure."""


def _clock(value):
    match = _AWARE.fullmatch(value) if type(value) is str else None
    if not match:
        raise ThetaDataError('Use an actual aware RFC3339 receipt or event clock.')
    try:
        base = datetime.fromisoformat(match[1] + match[3].replace('Z', '+00:00'))
        if abs(base.utcoffset()) > timedelta(hours=14):
            raise ValueError
        base = base.astimezone(UTC)
        fraction = int((match[2] or '').ljust(9, '0'))
        delta = base - EPOCH
        ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000 + fraction
        return {'raw': value, 'ns': ns,
                'iso': (base + timedelta(microseconds=fraction // 1000)).isoformat()}
    except (ValueError, OverflowError):
        raise ThetaDataError('Invalid aware Theta receipt or event clock.') from None


def _timestamp(value):
    if value is None:
        return {'raw': None, 'iso': None, 'ns': None, 'basis': 'missing'}
    if type(value) is str and _NAIVE.fullmatch(value):
        try:
            datetime.fromisoformat(value)
        except ValueError:
            raise ThetaDataError('Invalid naive Terminal source clock.') from None
        # Includes ambiguous/nonexistent DST-local times: no UTC instant is
        # admitted for any naive timestamp without a qualified timezone basis.
        return {'raw': value, 'iso': None, 'ns': None, 'basis': 'unverified-naive'}
    clock = _clock(value)
    return {**clock, 'basis': 'explicit-offset'}


def _json(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RAW:
        raise ThetaDataError('Terminal responses must be bounded UTF-8 JSON.')
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise ValueError
            value[key] = item
        return value
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ThetaDataError('Terminal response JSON is malformed or has duplicate fields.') from None


def _amount(value):
    if value is None:
        return None
    if type(value) not in (int, Decimal):
        raise ThetaDataError('Terminal prices must be bounded finite JSON numbers.')
    amount = Decimal(value)
    if (not amount.is_finite() or amount < 0 or amount > Decimal('100000000000000')
            or amount.as_tuple().exponent < -8):
        raise ThetaDataError('Terminal price exceeds ordinary decimal bounds.')
    return format(amount, 'f')


def _count(value):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
        raise ThetaDataError('Terminal sizes must be nonnegative bounded integers.')
    return value


def _instrument(value):
    if type(value) is not dict or set(value) != {'type', 'symbol'}:
        raise ThetaDataError('Use exact canonical symbol/type instruments.')
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
    raise ThetaDataError('Only explicit canonical US stock/ETF option identifiers are supported.')


def instruments(values):
    if type(values) not in (list, tuple) or not 2 <= len(values) <= 2 * MAX_OPTIONS:
        raise ThetaDataError('Use 1..4 explicit options and exactly their required stocks.')
    rows = [_instrument(value) for value in values]
    if len({(r['type'], r['symbol']) for r in rows}) != len(rows):
        raise ThetaDataError('Duplicate Terminal request instruments are refused.')
    options = [r['symbol'] for r in rows if r['type'] == 'OPTION']
    stocks = {r['symbol'] for r in rows if r['type'] == 'EQUITY'}
    if not 1 <= len(options) <= MAX_OPTIONS or stocks != {_OCC.fullmatch(symbol)[1] for symbol in options}:
        raise ThetaDataError('Include exactly the stocks required by 1..4 explicit option contracts.')
    return rows


def _contract(symbol):
    match = _OCC.fullmatch(symbol)
    expiry = date(2000 + int(match[2][:2]), int(match[2][2:4]), int(match[2][4:])).isoformat()
    strike = str(int(match[4]) // 1000) + '.' + str(int(match[4]) % 1000).zfill(3)
    return {'symbol': match[1], 'expiration': expiry,
            'right': 'call' if match[3] == 'C' else 'put', 'strike': strike}


class ThetaMarketDataClient:
    """Fixed numeric loopback routes; explicit online policy still required.

    A loopback Terminal can relay Internet requests. Ariadne's generic loopback
    exception therefore never substitutes for this connector's online opt-in.
    """
    def __init__(self, *, online=False):
        if type(online) is not bool:
            raise ThetaDataError('Online opt-in must be an explicit boolean.')
        self._online = online
        self._closed = False

    def close(self):
        self._closed = True

    def _http(self, instrument, deadline):
        instrument = _instrument(instrument)
        if instrument['type'] == 'OPTION':
            contract = _contract(instrument['symbol'])
            route = '/v3/option/snapshot/quote?symbol=' + contract['symbol']
            route += '&expiration=' + contract['expiration'] + '&right=' + contract['right']
            route += '&strike=' + contract['strike'] + '&format=json&current_day_only=true'
        else:
            route = '/v3/stock/snapshot/quote?symbol=' + instrument['symbol']
            # Explicit venue avoids silently labelling a delayed consolidated
            # snapshot as live. Nasdaq Basic BBO is not consolidated NBBO.
            route += '&venue=nqb&format=json&current_day_only=true'
        if (self._online is not True or os.environ.get('ARIADNE_OFFLINE') != '0'
                or install_network_policy()['mode'] != 'online'):
            raise ThetaDataUnavailable('Terminal collection requires explicit online opt-in and online process policy.')
        if self._closed:
            raise ThetaDataUnavailable('Theta data client has been closed.')
        connection = response = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ThetaDataUnavailable('Terminal quote collection exceeded its shared deadline.')
            connection = http.client.HTTPConnection(HOST, port=PORT, timeout=min(3, remaining))
            connection.request('GET', route, headers={'Accept': 'application/json',
                'Accept-Encoding': 'identity', 'Connection': 'close', 'User-Agent': 'Ariadne-Theta-ReadOnly/1'})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ThetaDataUnavailable('Terminal quote collection exceeded its shared deadline.')
            if connection.sock is not None:
                connection.sock.settimeout(min(3, remaining))
            response = connection.getresponse()
            if response.status != 200:
                raise ThetaDataUnavailable('Terminal or snapshot entitlement is unavailable; no redirect or retry was attempted.')
            media = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            encoding = response.getheader('Content-Encoding', 'identity').strip().lower()
            length = response.getheader('Content-Length')
            if media != 'application/json' or encoding not in ('', 'identity'):
                raise ThetaDataUnavailable('Terminal returned unsupported snapshot content.')
            if length is not None and (not length.isdigit() or int(length) > MAX_RAW):
                raise ThetaDataUnavailable('Terminal response exceeds its content bound.')
            raw = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ThetaDataUnavailable('Terminal quote collection exceeded its shared deadline.')
                if connection.sock is not None:
                    connection.sock.settimeout(min(3, remaining))
                block = response.read1(min(65536, MAX_RAW + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
                if len(raw) > MAX_RAW:
                    raise ThetaDataUnavailable('Terminal response exceeds its content bound.')
            received = datetime.now(UTC).isoformat()
            value = _json(bytes(raw))
            if type(value) is not list or len(value) != 1:
                raise ThetaDataUnavailable('Terminal must return exactly one explicit snapshot per request.')
            return {'instrument': instrument, 'payload': bytes(raw).decode('utf-8'), 'received_at': received}
        except ThetaDataError:
            raise
        except (OSError, ValueError, http.client.HTTPException, OverflowError):
            raise ThetaDataUnavailable('Terminal transport failed; no retry was attempted.') from None
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    def quotes(self, requested):
        rows = instruments(requested)
        deadline = time.monotonic() + MAX_COLLECTION_SECONDS
        options, stocks = [], []
        for row in rows:
            part = self._http(row, deadline)
            (options if row['type'] == 'OPTION' else stocks).append(part)
        received = max((p['received_at'] for p in options + stocks), key=lambda value: _clock(value)['ns'])
        capture = {'schema': 1, 'source_id': SOURCE_ID, 'received_at': received,
            'options': options, 'stocks': stocks, 'transport': {'source_id': SOURCE_ID,
                'operation': 'latest_quotes', 'provider_authenticated': False,
                'local_terminal_verified': False, 'kind': 'local_terminal_unattested',
                'options_feed': 'opra_unattested', 'stocks_feed': 'nasdaq_basic_unattested'}}
        normalize_quotes(capture, expected_instruments=rows)
        return capture


def _part(part, kind):
    if (type(part) is not dict or set(part) != {'instrument', 'payload', 'received_at'}
            or type(part['payload']) is not str or not 1 <= len(part['payload']) <= MAX_RAW):
        raise ThetaDataError('Use exact bounded Terminal response parts with actual receipts.')
    instrument = _instrument(part['instrument'])
    if instrument['type'] != kind:
        raise ThetaDataError('Terminal response kind does not match its requested instrument.')
    try:
        raw = part['payload'].encode('utf-8')
    except UnicodeError:
        raise ThetaDataError('Terminal response must be valid UTF-8.') from None
    value = _json(raw)
    if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
        raise ThetaDataError('Each Terminal request must return exactly one flat snapshot object.')
    row = value[0]
    allowed = {'symbol', 'timestamp', 'bid', 'ask', 'bid_size', 'ask_size',
               'bid_exchange', 'ask_exchange', 'bid_condition', 'ask_condition'}
    if kind == 'OPTION':
        allowed |= {'expiration', 'strike', 'right'}
    if not set(row) <= allowed:
        raise ThetaDataError('Terminal snapshot contains unsupported fields.')
    if kind == 'OPTION':
        contract = _contract(instrument['symbol'])
        if (row.get('symbol') != contract['symbol'] or row.get('expiration') != contract['expiration']
                or row.get('right') != contract['right'] or _amount(row.get('strike')) is None
                or Decimal(_amount(row['strike'])) != Decimal(contract['strike'])):
            raise ThetaDataError('Terminal option identity contradicts its explicit requested contract.')
    elif row.get('symbol') != instrument['symbol']:
        raise ThetaDataError('Terminal stock identity contradicts its explicit requested symbol.')
    for side in ('bid', 'ask'):
        if type(row.get(side + '_condition')) is not int or row[side + '_condition'] != 0:
            raise ThetaDataError('Only documented regular firm Terminal quote conditions are supported.')
        exchange = row.get(side + '_exchange')
        if exchange is not None and (type(exchange) is not int or not 0 <= exchange <= 10000):
            raise ThetaDataError('Terminal exchange identifier is invalid.')
    received = _clock(part['received_at'])
    clock = _timestamp(row.get('timestamp'))
    if clock['ns'] is not None and clock['ns'] > received['ns']:
        raise ThetaDataError('Terminal quote event occurs after its actual response receipt.')
    observation = {'instrument': instrument, 'bid': _amount(row.get('bid')),
        'ask': _amount(row.get('ask')), 'bid_size': _count(row.get('bid_size')),
        'ask_size': _count(row.get('ask_size')), 'quote_kind': 'nbbo_message' if kind == 'OPTION' else 'bbo_message',
        'quote_at': clock['iso'], 'quote_at_raw': clock['raw'], 'quote_at_ns': clock['ns'],
        'quote_timezone_basis': clock['basis'], 'received_at': received['iso'],
        'received_at_raw': received['raw'], 'received_at_ns': received['ns'],
        'bid_condition': row['bid_condition'], 'ask_condition': row['ask_condition'],
        'bid_exchange': row.get('bid_exchange'), 'ask_exchange': row.get('ask_exchange')}
    return observation, received, hashlib.sha256(raw).hexdigest()


def normalize_quotes(capture, *, expected_instruments=None):
    keys = {'schema', 'source_id', 'received_at', 'options', 'stocks', 'transport'}
    if (type(capture) is not dict or set(capture) != keys
            or type(capture['schema']) is not int or capture['schema'] != 1
            or capture['source_id'] != SOURCE_ID):
        raise ThetaDataError('Use the exact Theta Terminal v3 capture schema.')
    expected_transport = {'source_id': SOURCE_ID, 'operation': 'latest_quotes',
        'provider_authenticated': False, 'local_terminal_verified': False,
        'kind': 'local_terminal_unattested', 'options_feed': 'opra_unattested',
        'stocks_feed': 'nasdaq_basic_unattested'}
    if type(capture['transport']) is not dict or capture['transport'] != expected_transport:
        raise ThetaDataError('A Terminal capture cannot attest upstream authentication or live entitlement.')
    if any(type(capture['transport'][k]) is not bool for k in ('provider_authenticated', 'local_terminal_verified')):
        raise ThetaDataError('Terminal evidence flags must remain explicit false booleans.')
    observations, receipts, hashes = [], [], []
    for name, kind in (('options', 'OPTION'), ('stocks', 'EQUITY')):
        parts = capture[name]
        if type(parts) is not list or not 1 <= len(parts) <= MAX_OPTIONS:
            raise ThetaDataError('Terminal response parts exceed their explicit request bound.')
        for part in parts:
            observation, received, digest = _part(part, kind)
            observations.append(observation)
            receipts.append(received)
            hashes.append({'instrument': observation['instrument'], 'raw_sha256': digest,
                           'received_at': received['raw']})
    actual = instruments([r['instrument'] for r in observations])
    if expected_instruments is not None:
        expected = instruments(expected_instruments)
        if {(r['type'], r['symbol']) for r in actual} != {(r['type'], r['symbol']) for r in expected}:
            raise ThetaDataError('Terminal response does not match the explicit requested instruments.')
    received = _clock(capture['received_at'])
    if received['ns'] != max(r['ns'] for r in receipts):
        raise ThetaDataError('Terminal capture clock must equal its latest actual receipt.')
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    stocks = {r['instrument']['symbol']: r for r in observations if r['instrument']['type'] == 'EQUITY'}
    books, blockers = [], []
    for observation in observations:
        if observation['instrument']['type'] != 'OPTION':
            continue
        symbol = observation['instrument']['symbol']
        contract = _contract(symbol)
        equity = stocks[contract['symbol']]
        books.append({'schema': 1, 'source_id': SOURCE_ID, 'snapshot_id': 'theta-' + digest[:32],
            'quote_kind': 'top_of_book', 'underlying_symbol': contract['symbol'], 'underlying_type': 'unverified',
            'contract_id': symbol, 'expiry': contract['expiration'], 'type': contract['right'],
            'strike': contract['strike'], 'multiplier': 100, 'standard_deliverable_verified': False,
            'deliverable_verification_source': None, 'bid': observation['bid'], 'ask': observation['ask'],
            'bid_size': observation['bid_size'], 'ask_size': observation['ask_size'],
            'quote_at': observation['quote_at'], 'received_at': observation['received_at'],
            'underlying_quote': {'price': equity['ask'], 'quote_at': equity['quote_at'], 'received_at': equity['received_at']},
            'underlying_activity': None, 'contract_activity': None, 'open_interest': None})
        reasons = ['local_terminal_upstream_authentication_unverified', 'live_entitlements_unverified',
            'underlying_classification_unverified', 'standard_deliverable_unverified',
            'underlying_adv20_missing', 'contract_volume_clock_missing',
            'open_interest_session_and_publication_missing', 'per_side_last_change_clocks_not_supplied',
            'stock_feed_is_nasdaq_basic_bbo_not_consolidated_nbbo']
        if observation['quote_at'] is None:
            reasons.append('actual_quote_clock_missing')
        if observation['quote_timezone_basis'] == 'unverified-naive' or equity['quote_timezone_basis'] == 'unverified-naive':
            reasons.append('source_timestamp_timezone_unverified')
        if equity['quote_at'] is None or equity['ask'] is None:
            reasons.append('underlying_quote_missing')
        if observation['bid_size'] is None or observation['ask_size'] is None:
            reasons.append('displayed_size_missing')
        blockers.append({'contract_id': symbol, 'reasons': reasons})
    return {'schema': 1, 'source_id': SOURCE_ID, 'raw_sha256': digest,
        'received_at': received['iso'], 'received_at_raw': received['raw'], 'received_at_ns': received['ns'],
        'provider_authenticated': False, 'local_terminal_verified': False,
        'books': books, 'observations': observations, 'blockers': blockers}
