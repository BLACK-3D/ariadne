"""Public market-data adapter, with no broker order or money-operation surface.

Network access requires both an explicit client opt-in and Ariadne's online
process policy. Saved quote normalization is pure and never authenticates an
input file. No API key, bearer token, account balance or order is persisted.
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

from .network_policy import install_network_policy

SOURCE_ID = 'public'
HOST = 'api.public.com'
MAX_RAW = 4 * 1024 * 1024
MAX_INSTRUMENTS = 64
MAX_ROWS = 64
_TOKEN_ROUTE = '/userapiauthservice/personal/access-tokens'
_ACCOUNT_ROUTE = '/userapigateway/trading/account'
_ACCOUNT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_SYMBOL = re.compile(r'[A-Z][A-Z0-9]{0,5}\Z')
_OCC = re.compile(r'([A-Z][A-Z0-9]{0,5})([0-9]{6})([CP])([0-9]{8})\Z')
_CLOCK = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z')
_AMOUNT = re.compile(r'(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,8})?\Z')
UTC = timezone.utc


class PublicDataError(ValueError):
    """Malformed input or source evidence; messages never contain source bodies."""


class PublicDataUnavailable(PublicDataError):
    """Offline, failed authentication, rate limit or unavailable source; no retry."""


def _json(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RAW:
        raise PublicDataError('Public responses must be bounded UTF-8 JSON.')
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
        raise PublicDataError('Public response JSON is invalid or has duplicate fields.') from None


def _secret(value):
    if (type(value) is not str or not 1 <= len(value) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise PublicDataError('Supply a bounded printable Public credential privately.')
    return value


def _account(value):
    if type(value) is not str or not _ACCOUNT_ID.fullmatch(value):
        raise PublicDataError('Use an ordinary bounded Public account identifier.')
    return value


def _clock(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is datetime:
        if value.utcoffset() is None:
            raise PublicDataError('Source clocks must be aware ISO timestamps.')
        result = value
    else:
        if type(value) is not str or not _CLOCK.fullmatch(value):
            raise PublicDataError('Source clocks must be aware ISO timestamps with seconds.')
        try:
            result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, OverflowError):
            raise PublicDataError('Source clock is invalid.') from None
    if abs(result.utcoffset()) > timedelta(hours=14):
        raise PublicDataError('Source clock offset is invalid.')
    return result.astimezone(UTC)


def _iso(value):
    return None if value is None else value.isoformat()


def _amount(value):
    if value is None:
        return None
    if (type(value) is not str or len(value) > 32 or not _AMOUNT.fullmatch(value)
            or Decimal(value) > Decimal('100000000000000')):
        raise PublicDataError('Public prices must be bounded ordinary decimal strings.')
    return value


def _count(value):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
        raise PublicDataError('Public sizes and activity must be bounded nonnegative integers.')
    return value


def _instrument(value):
    if type(value) is not dict or set(value) != {'symbol', 'type'}:
        raise PublicDataError('Use exact symbol/type instrument objects.')
    symbol, kind = value['symbol'], value['type']
    if kind == 'EQUITY' and type(symbol) is str and _SYMBOL.fullmatch(symbol):
        return {'symbol': symbol, 'type': kind}
    if kind == 'OPTION' and type(symbol) is str:
        match = _OCC.fullmatch(symbol)
        if match:
            try:
                expiry = date(2000 + int(match[2][:2]), int(match[2][2:4]), int(match[2][4:]))
                if expiry.year <= 2035 and int(match[4]) > 0:
                    return {'symbol': symbol, 'type': kind}
            except ValueError:
                pass
    raise PublicDataError('Only canonical US equity and option instrument identifiers are supported.')


def instruments(values):
    """Validate the bounded explicit watchlist; no provider-discovered scope growth."""
    if type(values) not in (list, tuple) or not 1 <= len(values) <= MAX_INSTRUMENTS:
        raise PublicDataError('Use 1..64 explicit Public instruments.')
    result = [_instrument(value) for value in values]
    if len({(r['type'], r['symbol']) for r in result}) != len(result):
        raise PublicDataError('Public request instruments must be unique.')
    return result


class PublicMarketDataClient:
    """Small fixed-route synchronous client. Credentials are held only in memory.

    An API secret may have broader broker authority than this adapter exposes.
    This allowlist is a software restriction, not broker-enforced read-only auth.
    Errors are deliberately generic and do not contain URLs, bodies or headers.
    """
    def __init__(self, secret, *, online=False):
        if type(online) is not bool:
            raise PublicDataError('Online opt-in must be explicit true or false.')
        self._secret = _secret(secret)
        self._online = online
        self._token = None
        self._expires = 0.0
        self._last_request = None

    def close(self):
        """Drop credential references; Python does not promise secure memory erasure."""
        self._secret = None
        self._token = None
        self._expires = 0.0

    def _http(self, operation, *, account_id=None, body=None, token=None):
        # Validate operation and route BEFORE making a connection. No URL/path
        # parameter exists and environment proxy variables are not consulted.
        if operation == 'token':
            method, route = 'POST', _TOKEN_ROUTE
            if (type(body) is not dict or set(body) != {'secret', 'validityInMinutes'}
                    or body['secret'] != self._secret or type(body['validityInMinutes']) is not int
                    or body['validityInMinutes'] != 5 or token is not None):
                raise PublicDataError('Invalid fixed token request.')
        elif operation == 'accounts':
            method, route = 'GET', _ACCOUNT_ROUTE
            if body is not None or account_id is not None:
                raise PublicDataError('Invalid fixed account lookup.')
            _secret(token)
        elif operation == 'quotes':
            method, route = 'POST', '/userapigateway/marketdata/' + _account(account_id) + '/quotes'
            if type(body) is not dict or set(body) != {'instruments'}:
                raise PublicDataError('Invalid fixed quote request.')
            body = {'instruments': instruments(body['instruments'])}
            _secret(token)
        else:
            raise PublicDataError('Unsupported Public operation; only token, account lookup and quotes exist.')
        if (self._online is not True or os.environ.get('ARIADNE_OFFLINE') != '0'
                or install_network_policy()['mode'] != 'online'):
            raise PublicDataUnavailable('Public network access requires explicit online opt-in and online process policy.')
        if self._secret is None:
            raise PublicDataUnavailable('Public client has been closed.')
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity',
                   'User-Agent': 'Ariadne-Public-MarketData/1', 'Connection': 'close'}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(',', ':'), allow_nan=False).encode('ascii')
            headers['Content-Type'] = 'application/json'
        # Public documents 10 calls/second/account. Sequential client requests
        # stay below that rate; no retries or concurrent collection are added.
        if self._last_request is not None:
            wait = 0.12 - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
        connection = response = None
        deadline = time.monotonic() + 15
        try:
            self._last_request = time.monotonic()
            connection = http.client.HTTPSConnection(HOST, port=443, timeout=10,
                                                      context=ssl.create_default_context())
            connection.request(method, route, body=payload, headers=headers)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PublicDataUnavailable('Public response exceeded the collection deadline.')
            if connection.sock is not None:
                connection.sock.settimeout(min(10, remaining))
            response = connection.getresponse()
            if response.status != 200:
                # Do not read/echo provider error bodies, which may contain PII.
                if response.status == 401:
                    raise PublicDataUnavailable('Public authentication failed; review the key privately.')
                if response.status == 429:
                    raise PublicDataUnavailable('Public rate limit reached; collection stopped without retry.')
                raise PublicDataUnavailable('Public request failed; no retry was attempted.')
            media = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            encoding = response.getheader('Content-Encoding', 'identity').strip().lower()
            length = response.getheader('Content-Length')
            if media != 'application/json' or encoding not in ('', 'identity'):
                raise PublicDataUnavailable('Public returned unsupported content.')
            if length is not None and (not length.isdigit() or int(length) > MAX_RAW):
                raise PublicDataUnavailable('Public response exceeds its bounded content limit.')
            raw = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise PublicDataUnavailable('Public response exceeded the collection deadline.')
                if connection.sock is not None:
                    connection.sock.settimeout(min(10, deadline - time.monotonic()))
                block = response.read1(min(65536, MAX_RAW + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
                if len(raw) > MAX_RAW:
                    raise PublicDataUnavailable('Public response exceeds its bounded content limit.')
            received = datetime.now(UTC).isoformat()
            parsed = _json(bytes(raw))
            if type(parsed) is not dict:
                raise PublicDataUnavailable('Public returned an unsupported response object.')
            return {'payload': bytes(raw), 'received_at': received,
                    'transport': {'provider_authenticated': True, 'source_id': SOURCE_ID, 'operation': operation}}
        except PublicDataError:
            raise
        except (OSError, ValueError, http.client.HTTPException, OverflowError):
            raise PublicDataUnavailable('Public transport failed; collection stopped without retry.') from None
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    def _access(self):
        if self._secret is None:
            raise PublicDataUnavailable('Public client has been closed.')
        if self._token is None or time.monotonic() >= self._expires:
            self._token = None
            result = self._http('token', body={'secret': self._secret, 'validityInMinutes': 5})
            value = _json(result['payload'])
            if type(value) is not dict or set(value) != {'accessToken'}:
                raise PublicDataUnavailable('Public returned an unsupported token response.')
            self._token = _secret(value['accessToken'])
            self._expires = time.monotonic() + 270
        return self._token

    def accounts(self):
        """Return minimal account IDs/types for owner selection, never balances."""
        result = self._http('accounts', token=self._access())
        value = _json(result['payload'])
        rows = value.get('accounts')
        if type(rows) is not list or not 1 <= len(rows) <= 32:
            raise PublicDataUnavailable('Public returned no supported account list.')
        accounts = []
        for row in rows:
            if type(row) is not dict:
                raise PublicDataUnavailable('Public returned an invalid account list.')
            identifier = _account(row.get('accountId'))
            kind = row.get('accountType')
            if type(kind) is not str or not re.fullmatch(r'[A-Z_]{1,32}', kind):
                raise PublicDataUnavailable('Public returned an invalid account type.')
            accounts.append({'account_id': identifier, 'account_type': kind})
        if len({row['account_id'] for row in accounts}) != len(accounts):
            raise PublicDataUnavailable('Public returned duplicate account identifiers.')
        return accounts

    def quotes(self, account_id, requested):
        """Fetch one explicit watchlist. No order, account mutation or retries."""
        account_id = _account(account_id)
        requested = instruments(requested)
        result = self._http('quotes', account_id=account_id,
                            body={'instruments': requested}, token=self._access())
        # Validate before the caller can persist an unexpected response.
        normalize_quotes(result['payload'], received_at=result['received_at'],
                         expected_instruments=requested)
        return result


def normalize_quotes(payload, *, received_at, expected_instruments=None):
    """Map a saved response into conservative gate books and separate provenance.

    Missing source semantics remain missing: quote values do not establish OI
    session/publication, volume event time, standard deliverable or stock/ETF
    classification. No quote can qualify a BUY without independent evidence.
    Side clocks are never replaced by receipt or last-trade times. The legacy
    single book clock is the oldest existing side clock, not a merged event ID.
    Durable accounting must key each side by its own actual source clock.
    """
    received = _clock(received_at)
    value = _json(payload)
    if type(value) is not dict or set(value) != {'quotes'}:
        raise PublicDataError('Public quote envelope must contain only quotes.')
    rows = value['quotes']
    if type(rows) is not list or not 1 <= len(rows) <= MAX_ROWS:
        raise PublicDataError('Public quote response must contain 1..64 observations.')
    expected = None
    if expected_instruments is not None:
        expected = {(r['type'], r['symbol']) for r in instruments(expected_instruments)}
    observations, by_symbol = [], {}
    for row in rows:
        if type(row) is not dict or row.get('outcome') != 'SUCCESS':
            raise PublicDataError('Public returned a missing or unsuccessful quote.')
        instrument = _instrument(row.get('instrument'))
        key = (instrument['type'], instrument['symbol'])
        if key in by_symbol:
            raise PublicDataError('Duplicate Public quote instruments are refused.')
        observation = {'instrument': instrument, 'bid': _amount(row.get('bid')),
            'ask': _amount(row.get('ask')), 'bid_size': _count(row.get('bidSize')),
            'ask_size': _count(row.get('askSize')), 'last': _amount(row.get('last')),
            'bid_at': _iso(_clock(row.get('bidTimestamp'), nullable=True)),
            'ask_at': _iso(_clock(row.get('askTimestamp'), nullable=True)),
            'last_at': _iso(_clock(row.get('lastTimestamp'), nullable=True)),
            'volume': _count(row.get('volume')), 'open_interest': _count(row.get('openInterest'))}
        for name in ('bid_at', 'ask_at', 'last_at'):
            if observation[name] is not None and _clock(observation[name]) > received:
                raise PublicDataError('Public quote event occurs after its actual receipt.')
        observations.append(observation)
        by_symbol[key] = observation
    if expected is not None and set(by_symbol) != expected:
        raise PublicDataError('Public response does not match the explicit requested instruments.')
    digest = hashlib.sha256(payload).hexdigest()
    books, blockers = [], []
    for observation in observations:
        instrument = observation['instrument']
        if instrument['type'] != 'OPTION':
            continue
        match = _OCC.fullmatch(instrument['symbol'])
        underlying = match[1]
        equity = by_symbol.get(('EQUITY', underlying))
        side_clocks = [_clock(observation[side + '_at']) for side in ('bid', 'ask')
                       if observation[side] is not None and observation[side + '_at'] is not None]
        clocks_complete = all(observation[side] is None or observation[side + '_at'] is not None
                              for side in ('bid', 'ask'))
        quote_at = _iso(min(side_clocks)) if clocks_complete and side_clocks else None
        underlying_quote = None
        if equity is not None:
            underlying_quote = {'price': equity['last'], 'quote_at': equity['last_at'],
                                'received_at': received.isoformat()}
        # The canonical identifier reveals nominal strike and expiry, but does
        # not prove the deliverable/multiplier or absence of corporate actions.
        book = {'schema': 1, 'source_id': SOURCE_ID, 'snapshot_id': 'public-' + digest[:32],
            'quote_kind': 'top_of_book', 'underlying_symbol': underlying, 'underlying_type': 'unverified',
            'contract_id': instrument['symbol'],
            'expiry': date(2000 + int(match[2][:2]), int(match[2][2:4]), int(match[2][4:])).isoformat(),
            'type': 'call' if match[3] == 'C' else 'put',
            'strike': str(int(match[4]) // 1000) + '.' + str(int(match[4]) % 1000).zfill(3),
            'multiplier': 100, 'standard_deliverable_verified': False, 'deliverable_verification_source': None,
            'bid': observation['bid'], 'ask': observation['ask'],
            'bid_size': observation['bid_size'], 'ask_size': observation['ask_size'],
            'quote_at': quote_at, 'received_at': received.isoformat(),
            'underlying_quote': underlying_quote, 'underlying_activity': None,
            'contract_activity': {'volume': observation['volume'], 'as_of': None, 'published_at': None},
            'open_interest': {'value': observation['open_interest'], 'session': None, 'published_at': None}}
        books.append(book)
        missing = ['underlying_classification_unverified', 'standard_deliverable_unverified',
                   'underlying_adv20_missing', 'contract_volume_clock_missing', 'open_interest_session_and_publication_missing']
        if quote_at is None:
            missing.append('actual_quote_clock_missing')
        if observation['bid_size'] is None or observation['ask_size'] is None:
            missing.append('displayed_size_missing')
        if underlying_quote is None or underlying_quote['price'] is None or underlying_quote['quote_at'] is None:
            missing.append('underlying_quote_missing')
        blockers.append({'contract_id': instrument['symbol'], 'reasons': missing})
    return {'schema': 1, 'source_id': SOURCE_ID, 'raw_sha256': digest,
            'received_at': received.isoformat(), 'provider_authenticated': False,
            'books': books, 'observations': observations, 'blockers': blockers}
