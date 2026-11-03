"""Synthetic Public responses only; never credentials, brokerage or network I/O."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import localcontext
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import public_market_data as public
from ariadne.options_liquidity import _book

RECEIVED = '2026-11-03T15:00:02+00:00'
CONTRACT = 'KO261120P00080000'
WATCH = [{'type': 'OPTION', 'symbol': CONTRACT}, {'type': 'EQUITY', 'symbol': 'KO'}]
SECRET = 'fictional-secret-not-a-real-key'
TOKEN = 'fictional-bearer-not-a-real-token'


def quote(symbol=CONTRACT, kind='OPTION'):
    return {'instrument': {'symbol': symbol, 'type': kind}, 'outcome': 'SUCCESS',
        'bid': '1.00', 'bidSize': 7, 'bidTimestamp': '2026-11-03T15:00:00Z',
        'ask': '1.04', 'askSize': 9, 'askTimestamp': '2026-11-03T15:00:01Z',
        'last': '1.02', 'lastTimestamp': '2026-11-03T14:59:58Z',
        'volume': 1234, 'openInterest': 2345}


def fixture():
    equity = quote('KO', 'EQUITY')
    equity.update(last='79.90', lastTimestamp='2026-11-03T15:00:01Z')
    return {'quotes': [quote(), equity]}


def raw(value=None):
    return json.dumps(fixture() if value is None else value, separators=(',', ':')).encode()


class FakeResponse:
    def __init__(self, payload=b'{}', *, status=200, headers=None):
        self.status = status
        self.body = io.BytesIO(payload)
        self.headers = {'Content-Type': 'application/json', **(headers or {})}
        self.closed = False
        self.reads = 0
    def getheader(self, key, default=None):
        return self.headers.get(key, default)
    def read1(self, size):
        self.reads += 1
        return self.body.read(size)
    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False
        self.sock = None
    def request(self, method, route, **kwargs):
        self.requests.append((method, route, kwargs))
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True


class NormalizeTests(unittest.TestCase):
    def normalize(self, value=None, **kwargs):
        return public.normalize_quotes(raw(value), received_at=RECEIVED,
                                       expected_instruments=WATCH, **kwargs)

    def test_preserves_sides_and_uses_oldest_existing_clock_not_receipt_or_last(self):
        value = self.normalize()
        book, observation = value['books'][0], value['observations'][0]
        self.assertEqual(book['quote_at'], '2026-11-03T15:00:00+00:00')
        self.assertEqual(observation['ask_at'], '2026-11-03T15:00:01+00:00')
        self.assertEqual(observation['last_at'], '2026-11-03T14:59:58+00:00')
        self.assertEqual((book['bid'], book['ask'], book['bid_size'], book['ask_size']),
                         ('1.00', '1.04', 7, 9))
        self.assertEqual(book['underlying_quote']['price'], '79.90')
        self.assertIs(value['provider_authenticated'], False)

    def test_does_not_invent_deliverable_activity_or_oi_provenance(self):
        value = self.normalize()
        b = value['books'][0]
        self.assertIs(b['standard_deliverable_verified'], False)
        self.assertIsNone(b['underlying_activity'])
        self.assertEqual(b['underlying_type'], 'unverified')
        self.assertEqual(b['contract_activity'], {'volume': 1234, 'as_of': None, 'published_at': None})
        self.assertEqual(b['open_interest'], {'value': 2345, 'session': None, 'published_at': None})
        self.assertIn('open_interest_session_and_publication_missing', value['blockers'][0]['reasons'])

    def test_emitted_books_match_existing_gate_schema_without_becoming_eligible(self):
        value = self.normalize()
        validated = _book(value['books'][0])
        self.assertFalse(validated['raw']['standard_deliverable_verified'])
        self.assertIsNone(validated['underlying_activity'])

    def test_missing_source_side_clock_stays_missing(self):
        value = fixture()
        del value['quotes'][0]['bidTimestamp']
        b = self.normalize(value)['books'][0]
        self.assertIsNone(b['quote_at'])
        self.assertEqual(b['received_at'], RECEIVED)

    def test_missing_ask_permits_bid_clock_for_exit_evidence(self):
        value = fixture()
        for name in ('ask', 'askTimestamp', 'askSize'):
            value['quotes'][0].pop(name)
        b = self.normalize(value)['books'][0]
        self.assertEqual(b['quote_at'], '2026-11-03T15:00:00+00:00')
        self.assertIsNone(b['ask'])

    def test_zero_values_stay_zero_and_missing_sizes_stay_null(self):
        value = fixture()
        value['quotes'][0].update(bid='0', bidSize=0, askSize=None, volume=0, openInterest=None)
        b = self.normalize(value)['books'][0]
        self.assertEqual((b['bid'], b['bid_size'], b['ask_size']), ('0', 0, None))
        self.assertEqual(b['contract_activity']['volume'], 0)
        self.assertIsNone(b['open_interest']['value'])

    def test_crossed_quote_is_retained_for_gate_rejection_never_repaired(self):
        value = fixture()
        value['quotes'][0]['bid'] = '2.00'
        self.assertEqual(self.normalize(value)['books'][0]['bid'], '2.00')

    def test_source_future_clock_fails_even_if_it_is_last_trade_clock(self):
        for key in ('bidTimestamp', 'askTimestamp', 'lastTimestamp'):
            with self.subTest(key=key):
                value = fixture()
                value['quotes'][0][key] = '2026-11-03T15:00:03Z'
                with self.assertRaises(public.PublicDataError):
                    self.normalize(value)

    def test_invalid_or_naive_clocks_refused(self):
        for clock in ('2026-11-03', '2026-11-03T15:00:00', '2026-11-03T15:00:00+15:00',
                      '2026-02-30T15:00:00Z', '2026-11-03T15:00:00.1234567Z', 1793718000):
            with self.subTest(clock=clock):
                value = fixture()
                value['quotes'][0]['bidTimestamp'] = clock
                with self.assertRaises(public.PublicDataError):
                    self.normalize(value)

    def test_price_and_size_types_are_not_coerced(self):
        for field, bad in (('bid', 1.0), ('ask', '1e0'), ('bid', '-1'), ('ask', 'NaN'), ('bid', '999999999999999'),
                           ('bidSize', True), ('askSize', -1), ('volume', 1.5), ('openInterest', '1000')):
            with self.subTest(field=field, bad=bad):
                value = fixture()
                value['quotes'][0][field] = bad
                with self.assertRaises(public.PublicDataError):
                    self.normalize(value)

    def test_duplicate_missing_extra_and_unsuccessful_quotes_refused(self):
        for change in ('duplicate', 'missing', 'extra', 'unsuccessful'):
            with self.subTest(change=change):
                value = fixture()
                if change == 'duplicate': value['quotes'].append(quote())
                if change == 'missing': value['quotes'].pop()
                if change == 'extra': value['quotes'].append(quote('F', 'EQUITY'))
                if change == 'unsuccessful': value['quotes'][0]['outcome'] = 'NOT_FOUND'
                with self.assertRaises(public.PublicDataError):
                    self.normalize(value)

    def test_duplicate_json_and_nonfinite_json_refused(self):
        for value in (b'{"quotes":[],"quotes":[]}', b'{"quotes":[NaN]}', b'[]', b'{}',
                      b'{"quotes":[],"accountSecret":"must-not-be-exposed"}'):
            with self.subTest(value=value):
                with self.assertRaises(public.PublicDataError):
                    public.normalize_quotes(value, received_at=RECEIVED)

    def test_invalid_occ_identifiers_and_non_us_instrument_kinds_refused(self):
        for symbol, kind in (('KO261132P00080000', 'OPTION'), ('KO991120P00080000', 'OPTION'),
                             ('KO261120P00000000', 'OPTION'), ('KO1  261120P00080000', 'OPTION'),
                             ('BTC', 'CRYPTO'), ('../orders', 'EQUITY')):
            with self.subTest(symbol=symbol, kind=kind):
                with self.assertRaises(public.PublicDataError):
                    public.instruments([{'type': kind, 'symbol': symbol}])

    def test_watchlist_bound_and_duplicate_rejection(self):
        for value in ([], WATCH * 33, WATCH + WATCH, [{'symbol': 'KO', 'type': 'EQUITY', 'extra': 1}]):
            with self.assertRaises(public.PublicDataError):
                public.instruments(value)

    def test_economic_identity_survives_hostile_decimal_precision(self):
        with localcontext() as context:
            context.prec = 1
            self.assertEqual(self.normalize()['books'][0]['strike'], '80.000')

    def test_equity_only_snapshot_has_no_invented_options(self):
        value = public.normalize_quotes(raw({'quotes': [quote('KO', 'EQUITY')]}), received_at=RECEIVED)
        self.assertEqual(value['books'], [])
        self.assertEqual(value['blockers'], [])


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'ARIADNE_OFFLINE': '0'})
        self.env.start()
        self.policy = patch.object(public, 'install_network_policy', return_value={'mode': 'online'})
        self.policy.start()
        self.sleep = patch.object(public.time, 'sleep')
        self.sleep.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.policy.stop)
        self.addCleanup(self.sleep.stop)

    def run_http(self, response=None, *, operation='quotes', **kwargs):
        response = response or FakeResponse(raw())
        connection = FakeConnection(response)
        client = public.PublicMarketDataClient(SECRET, online=True)
        with patch.object(public.http.client, 'HTTPSConnection', return_value=connection) as factory:
            result = client._http(operation, **({'account_id': 'fictional-account',
                'body': {'instruments': WATCH}, 'token': TOKEN} if operation == 'quotes' else kwargs))
        return result, connection, response, factory

    def test_fixed_host_tls_paths_and_request_body(self):
        result, connection, response, factory = self.run_http()
        args, kwargs = factory.call_args
        self.assertEqual(args, ('api.public.com',))
        self.assertEqual(kwargs['port'], 443)
        self.assertTrue(kwargs['context'].check_hostname)
        self.assertEqual(kwargs['context'].verify_mode, public.ssl.CERT_REQUIRED)
        method, route, params = connection.requests[0]
        self.assertEqual((method, route), ('POST', '/userapigateway/marketdata/fictional-account/quotes'))
        self.assertEqual(json.loads(params['body']), {'instruments': WATCH})
        self.assertEqual(params['headers']['Authorization'], 'Bearer ' + TOKEN)
        self.assertTrue(result['transport']['provider_authenticated'])
        self.assertNotIn(SECRET, repr(result))
        self.assertNotIn(TOKEN, repr(result))
        self.assertTrue(connection.closed and response.closed)

    def test_default_client_optout_and_environment_offline_block_before_socket(self):
        for online, env, policy in ((False, '0', 'online'), (True, '1', 'online'),
                                    (True, '0', 'offline')):
            with self.subTest(online=online, env=env, policy=policy):
                client = public.PublicMarketDataClient(SECRET, online=online)
                with patch.dict(os.environ, {'ARIADNE_OFFLINE': env}), \
                     patch.object(public, 'install_network_policy', return_value={'mode': policy}), \
                     patch.object(public.http.client, 'HTTPSConnection') as socket:
                    with self.assertRaises(public.PublicDataUnavailable):
                        client.quotes('fictional-account', WATCH)
                    socket.assert_not_called()

    def test_no_order_generic_path_portfolio_or_balance_surface(self):
        client = public.PublicMarketDataClient(SECRET, online=True)
        with patch.object(public.http.client, 'HTTPSConnection') as connection:
            for operation in ('orders', 'place_order', 'portfolio', 'balances',
                              '/userapigateway/trading/account/x/order', 'https://evil.invalid'):
                with self.subTest(operation=operation):
                    with self.assertRaises(public.PublicDataError):
                        client._http(operation)
            connection.assert_not_called()
        for name in ('place_order', 'order', 'portfolio', 'balance', 'transfer'):
            self.assertFalse(hasattr(client, name))

    def test_path_injection_and_bad_watchlist_fail_before_token_request(self):
        client = public.PublicMarketDataClient(SECRET, online=True)
        with patch.object(client, '_http') as request:
            for account in ('../orders', 'id?x=1', 'id/positions', 'id%2Forders', 'id\nHeader:value'):
                with self.assertRaises(public.PublicDataError):
                    client.quotes(account, WATCH)
            with self.assertRaises(public.PublicDataError):
                client.quotes('fictional-account', [])
            request.assert_not_called()

    def test_redirects_auth_rate_and_server_errors_stop_without_reading_body(self):
        for status in (301, 302, 307, 401, 403, 429, 500):
            with self.subTest(status=status):
                response = FakeResponse((SECRET + TOKEN).encode(), status=status,
                                        headers={'Location': 'https://evil.invalid'})
                connection = FakeConnection(response)
                client = public.PublicMarketDataClient(SECRET, online=True)
                with patch.object(public.http.client, 'HTTPSConnection', return_value=connection) as factory:
                    with self.assertRaises(public.PublicDataUnavailable) as error:
                        client._http('accounts', token=TOKEN)
                self.assertEqual(factory.call_count, 1)
                self.assertEqual(response.reads, 0)
                self.assertNotIn(SECRET, str(error.exception))
                self.assertNotIn(TOKEN, str(error.exception))
                self.assertTrue(response.closed and connection.closed)

    def test_unexpected_encoding_type_and_unbounded_length_refused(self):
        for headers in ({'Content-Encoding': 'gzip'}, {'Content-Type': 'text/html'},
                        {'Content-Length': str(public.MAX_RAW + 1)}, {'Content-Length': '-1'}):
            with self.subTest(headers=headers):
                with self.assertRaises(public.PublicDataUnavailable):
                    self.run_http(FakeResponse(raw(), headers=headers))

    def test_body_limit_and_invalid_json_fail_closed(self):
        for body in (b'x' * (public.MAX_RAW + 1), b'{bad json', b'[]'):
            with self.subTest(size=len(body)):
                with self.assertRaises(public.PublicDataError):
                    self.run_http(FakeResponse(body))

    def test_transport_exception_does_not_echo_secret(self):
        client = public.PublicMarketDataClient(SECRET, online=True)
        with patch.object(public.http.client, 'HTTPSConnection', side_effect=OSError(SECRET + TOKEN)):
            with self.assertRaises(public.PublicDataUnavailable) as error:
                client._http('accounts', token=TOKEN)
        self.assertNotIn(SECRET, str(error.exception))
        self.assertNotIn(TOKEN, str(error.exception))

    def test_key_exchange_then_quote_and_cached_token_have_exact_allowlisted_calls(self):
        token_response = {'payload': raw({'accessToken': TOKEN}), 'received_at': RECEIVED}
        quote_response = {'payload': raw(), 'received_at': RECEIVED,
                          'transport': {'provider_authenticated': True}}
        client = public.PublicMarketDataClient(SECRET, online=True)
        with patch.object(client, '_http', side_effect=[token_response, quote_response, quote_response]) as request:
            client.quotes('fictional-account', WATCH)
            client.quotes('fictional-account', WATCH)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args_list[0].args, ('token',))
        self.assertEqual(request.call_args_list[0].kwargs,
                         {'body': {'secret': SECRET, 'validityInMinutes': 5}})
        self.assertEqual(request.call_args_list[1].args, ('quotes',))

    def test_expired_token_is_reexchanged_and_closed_client_refuses(self):
        client = public.PublicMarketDataClient(SECRET, online=True)
        client._token, client._expires = TOKEN, 0
        with patch.object(client, '_http', return_value={'payload': raw({'accessToken': TOKEN})}) as request:
            self.assertEqual(client._access(), TOKEN)
            self.assertEqual(request.call_count, 1)
        client.close()
        with self.assertRaises(public.PublicDataUnavailable):
            client._access()
        self.assertIsNone(client._secret)
        self.assertIsNone(client._token)

    def test_account_lookup_exposes_only_minimal_ids_and_types(self):
        client = public.PublicMarketDataClient(SECRET, online=True)
        payload = raw({'accounts': [{'accountId': 'fictional-account', 'accountType': 'BROKERAGE',
                                    'balance': SECRET, 'extra': TOKEN}]})
        with patch.object(client, '_access', return_value=TOKEN), \
             patch.object(client, '_http', return_value={'payload': payload}):
            self.assertEqual(client.accounts(), [{'account_id': 'fictional-account', 'account_type': 'BROKERAGE'}])


if __name__ == '__main__':
    unittest.main()
