"""Synthetic OPRA/SIP fixtures only; no market or broker requests."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne import alpaca_market_data as feed
from ariadne.options_liquidity import _book

CONTRACT = 'KO261120P00080000'
WATCH = [{'symbol': CONTRACT, 'type': 'OPTION'}, {'symbol': 'KO', 'type': 'EQUITY'}]
KEY = 'FICTIONAL_ALPACA_KEY_ID'
SECRET = 'fictional-secret-not-a-real-key'
RECEIVED = '2026-11-05T15:00:02+00:00'


def fixture():
    option = {'t': '2026-11-05T15:00:01.123456789Z', 'bp': 1.00, 'bs': 8,
              'bx': 'A', 'ap': 1.04, 'as': 9, 'ax': 'Q', 'c': ' '}
    stock = {'t': '2026-11-05T15:00:01Z', 'bp': 79.98, 'bs': 100,
             'bx': 'A', 'ap': 80.00, 'as': 200, 'ax': 'Q', 'c': ['R'], 'z': 'A'}
    return {'schema': 1, 'source_id': feed.SOURCE_ID, 'received_at': RECEIVED,
        'options': {'payload': json.dumps({'quotes': {CONTRACT: option}}), 'received_at': RECEIVED},
        'stocks': {'payload': json.dumps({'quotes': {'KO': stock}}), 'received_at': RECEIVED},
        'transport': {'source_id': feed.SOURCE_ID, 'operation': 'latest_quotes',
            'provider_authenticated': True, 'options_feed': 'opra', 'stocks_feed': 'sip'}}


def change(capture, key, field, value):
    payload = json.loads(capture[key]['payload'])
    row = next(iter(payload['quotes'].values()))
    row[field] = value
    capture[key]['payload'] = json.dumps(payload)
    return capture


class FakeResponse:
    def __init__(self, payload=b'{"quotes":{}}', status=200, headers=None):
        self.status = status
        self.body = io.BytesIO(payload)
        self.headers = {'Content-Type': 'application/json', **(headers or {})}
        self.reads = 0
        self.closed = False
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
        self.sock = None
        self.requests = []
        self.closed = False
    def request(self, method, route, **kwargs):
        self.requests.append((method, route, kwargs))
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True


class NormalizeTests(unittest.TestCase):
    def test_actual_ns_retained_and_gate_projection_floored(self):
        value = feed.normalize_quotes(fixture(), expected_instruments=WATCH)
        option = value['observations'][0]
        self.assertEqual(option['quote_at_raw'], '2026-11-05T15:00:01.123456789Z')
        self.assertEqual(option['quote_at'], '2026-11-05T15:00:01.123456+00:00')
        self.assertEqual(option['quote_at_ns'] % 1000000000, 123456789)
        self.assertEqual(option['quote_kind'], 'nbbo_message')
        self.assertNotIn('bid_at', option)
        self.assertNotIn('ask_at', option)
        self.assertFalse(value['provider_authenticated'])
        self.assertEqual(_book(value['books'][0])['raw']['bid_size'], 8)

    def test_missing_activity_and_deliverables_stay_missing(self):
        value = feed.normalize_quotes(fixture())
        book = value['books'][0]
        self.assertIsNone(book['underlying_activity'])
        self.assertIsNone(book['contract_activity'])
        self.assertIsNone(book['open_interest'])
        self.assertFalse(book['standard_deliverable_verified'])
        self.assertEqual(book['underlying_type'], 'unverified')
        self.assertIn('per_side_last_change_clocks_not_supplied', value['blockers'][0]['reasons'])

    def test_underlying_uses_actual_ask_not_bid_or_midpoint(self):
        value = fixture()
        change(value, 'stocks', 'bp', 99.90)
        change(value, 'stocks', 'ap', 100.01)
        self.assertEqual(feed.normalize_quotes(value)['books'][0]['underlying_quote']['price'], '100.01')

    def test_future_ns_rejected_even_when_microsecond_projection_matches(self):
        value = fixture()
        change(value, 'options', 't', '2026-11-05T15:00:02.000000001Z')
        with self.assertRaises(feed.AlpacaDataError):
            feed.normalize_quotes(value)

    def test_each_response_receipt_controls_its_own_event(self):
        value = fixture()
        value['options']['received_at'] = '2026-11-05T15:00:01Z'
        with self.assertRaises(feed.AlpacaDataError):
            feed.normalize_quotes(value)

    def test_clock_offsets_compare_exactly_without_losing_raw_value(self):
        value = fixture()
        change(value, 'options', 't', '2026-11-05T09:00:01.123456789-06:00')
        first = feed.normalize_quotes(value)['observations'][0]
        self.assertEqual(first['quote_at_ns'], feed.normalize_quotes(fixture())['observations'][0]['quote_at_ns'])
        self.assertEqual(first['quote_at_raw'], '2026-11-05T09:00:01.123456789-06:00')

    def test_naive_date_invalid_clock_and_more_than_ns_refused(self):
        for clock in ('2026-11-05', '2026-11-05T15:00:00', '2026-02-30T15:00:00Z',
                      '2026-11-05T15:00:00.1234567890Z', '2026-11-05T15:00:00+15:00'):
            with self.subTest(clock=clock), self.assertRaises(feed.AlpacaDataError):
                feed.normalize_quotes(change(fixture(), 'options', 't', clock))

    def test_missing_event_or_size_stays_missing_and_blocked(self):
        value = fixture()
        change(value, 'options', 't', None)
        change(value, 'options', 'as', None)
        book = feed.normalize_quotes(value)['books'][0]
        self.assertIsNone(book['quote_at'])
        self.assertIsNone(book['ask_size'])

    def test_zero_and_crossed_values_are_preserved_for_gate_rejection(self):
        value = fixture()
        change(value, 'options', 'bp', 2)
        change(value, 'options', 'bs', 0)
        book = feed.normalize_quotes(value)['books'][0]
        self.assertEqual((book['bid'], book['ask'], book['bid_size']), ('2', '1.04', 0))

    def test_nonfirm_or_unknown_condition_refused_regular_space_preserved(self):
        for condition in ('A', 'F', '', None, True):
            with self.subTest(condition=condition), self.assertRaises(feed.AlpacaDataError):
                feed.normalize_quotes(change(fixture(), 'options', 'c', condition))
        self.assertEqual(feed.normalize_quotes(fixture())['observations'][0]['condition'], ' ')

    def test_invalid_economic_types_and_bounds_refused(self):
        for field, value in (('bp', True), ('ap', '1.04'), ('bs', False), ('as', -1),
                             ('bp', -1), ('as', 1.5), ('ap', 1e15), ('ap', 0.000000001)):
            with self.subTest(field=field, value=value), self.assertRaises(feed.AlpacaDataError):
                feed.normalize_quotes(change(fixture(), 'options', field, value))

    def test_duplicate_nonfinite_unknownfields_and_extra_symbols_refused(self):
        for payload in ('{"quotes":{},"quotes":{}}', '{"quotes":{"x":NaN}}', '[]',
                        '{"quotes":{},"private":"fictional"}'):
            value = fixture()
            value['options']['payload'] = payload
            with self.assertRaises(feed.AlpacaDataError):
                feed.normalize_quotes(value)
        value = fixture()
        payload = json.loads(value['stocks']['payload'])
        payload['quotes']['F'] = payload['quotes']['KO']
        value['stocks']['payload'] = json.dumps(payload)
        with self.assertRaises(feed.AlpacaDataError):
            feed.normalize_quotes(value)

    def test_indicative_iex_and_boolean_impersonations_refused(self):
        for key, bad in (('options_feed', 'indicative'), ('stocks_feed', 'iex'),
                         ('provider_authenticated', 1)):
            value = fixture()
            value['transport'][key] = bad
            with self.assertRaises(feed.AlpacaDataError):
                feed.normalize_quotes(value)

    def test_watchlists_are_bounded_canonical_and_include_exact_underlyings(self):
        for bad in ([], WATCH + WATCH, WATCH[:1], WATCH + [{'symbol': 'F', 'type': 'EQUITY'}],
                    [{'symbol': '../orders', 'type': 'OPTION'}, WATCH[1]],
                    [{'symbol': 'KO261132P00080000', 'type': 'OPTION'}, WATCH[1]]):
            with self.subTest(bad=bad), self.assertRaises(feed.AlpacaDataError):
                feed.instruments(bad)

    def test_normalization_independent_of_decimal_context(self):
        with localcontext() as context:
            context.prec = 1
            book = feed.normalize_quotes(fixture())['books'][0]
        self.assertEqual((book['strike'], book['ask']), ('80.000', '1.04'))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'ARIADNE_OFFLINE': '0', 'HTTPS_PROXY': 'https://forbidden.invalid'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.policy = patch.object(feed, 'install_network_policy', return_value={'mode': 'online'})
        self.policy.start()
        self.addCleanup(self.policy.stop)

    def test_exact_fixed_tls_get_routes_and_private_headers(self):
        payload = fixture()
        responses = [FakeResponse(payload[k]['payload'].encode()) for k in ('options', 'stocks')]
        connections = [FakeConnection(response) for response in responses]
        client = feed.AlpacaMarketDataClient(KEY, SECRET, online=True)
        with patch.object(feed.http.client, 'HTTPSConnection', side_effect=connections) as factory, patch.object(feed, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat(RECEIVED)
            capture = client.quotes(WATCH)
        self.assertEqual(factory.call_count, 2)
        for call in factory.call_args_list:
            self.assertEqual(call.args, ('data.alpaca.markets',))
            self.assertEqual(call.kwargs['port'], 443)
            self.assertTrue(call.kwargs['context'].check_hostname)
            self.assertEqual(call.kwargs['context'].verify_mode, feed.ssl.CERT_REQUIRED)
        self.assertEqual(connections[0].requests[0][:2], ('GET', '/v1beta1/options/quotes/latest?symbols=' + CONTRACT + '&feed=opra'))
        self.assertEqual(connections[1].requests[0][:2], ('GET', '/v2/stocks/quotes/latest?symbols=KO&feed=sip'))
        for conn in connections:
            headers = conn.requests[0][2]['headers']
            self.assertEqual(headers['APCA-API-KEY-ID'], KEY)
            self.assertEqual(headers['APCA-API-SECRET-KEY'], SECRET)
            self.assertTrue(conn.closed)
        self.assertTrue(capture['transport']['provider_authenticated'])
        self.assertNotIn(KEY, repr(capture))
        self.assertNotIn(SECRET, repr(capture))

    def test_offline_and_optout_stop_before_socket_creation(self):
        for online, env, policy in ((False, '0', 'online'), (True, '1', 'online'), (True, '0', 'offline')):
            with self.subTest(online=online, env=env, policy=policy), patch.dict(os.environ, {'ARIADNE_OFFLINE': env}), patch.object(feed, 'install_network_policy', return_value={'mode': policy}), patch.object(feed.http.client, 'HTTPSConnection') as socket:
                with self.assertRaises(feed.AlpacaDataUnavailable):
                    feed.AlpacaMarketDataClient(KEY, SECRET, online=online).quotes(WATCH)
                socket.assert_not_called()

    def test_forbidden_operations_and_injection_stop_before_network(self):
        client = feed.AlpacaMarketDataClient(KEY, SECRET, online=True)
        with patch.object(feed.http.client, 'HTTPSConnection') as socket:
            for operation in ('orders', 'accounts', 'positions', 'token', 'https://other.invalid'):
                with self.assertRaises(feed.AlpacaDataError):
                    client._http(operation, ['KO'])
            with self.assertRaises(feed.AlpacaDataError):
                client._http('stocks', ['KO&feed=iex'])
            with self.assertRaises(feed.AlpacaDataError):
                client._http('stocks', [{}])
            socket.assert_not_called()
        for name in ('order', 'place_order', 'balance', 'transfer', 'account'):
            self.assertFalse(hasattr(client, name))

    def test_redirect_auth_rate_errors_do_not_read_or_echo_body_or_retry(self):
        for status in (301, 302, 307, 401, 403, 429, 500):
            response = FakeResponse((KEY + SECRET).encode(), status=status)
            connection = FakeConnection(response)
            with self.subTest(status=status), patch.object(feed.http.client, 'HTTPSConnection', return_value=connection) as factory:
                with self.assertRaises(feed.AlpacaDataUnavailable) as caught:
                    feed.AlpacaMarketDataClient(KEY, SECRET, online=True)._http('stocks', ['KO'])
                self.assertEqual(factory.call_count, 1)
                self.assertEqual(response.reads, 0)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertTrue(response.closed and connection.closed)

    def test_bad_content_and_unbounded_body_refused(self):
        for response in (FakeResponse(headers={'Content-Encoding': 'gzip'}),
                         FakeResponse(headers={'Content-Type': 'text/html'}),
                         FakeResponse(headers={'Content-Length': str(feed.MAX_RAW + 1)}),
                         FakeResponse(b'x' * (feed.MAX_RAW + 1)), FakeResponse(b'{bad json')):
            with patch.object(feed.http.client, 'HTTPSConnection', return_value=FakeConnection(response)):
                with self.assertRaises(feed.AlpacaDataError):
                    feed.AlpacaMarketDataClient(KEY, SECRET, online=True)._http('stocks', ['KO'])

    def test_transport_exception_redacted_and_close_drops_keys(self):
        client = feed.AlpacaMarketDataClient(KEY, SECRET, online=True)
        with patch.object(feed.http.client, 'HTTPSConnection', side_effect=OSError(KEY + SECRET)):
            with self.assertRaises(feed.AlpacaDataUnavailable) as caught:
                client._http('stocks', ['KO'])
        self.assertNotIn(KEY, str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        client.close()
        self.assertIsNone(client._key_id)
        self.assertIsNone(client._secret_key)
        with self.assertRaises(feed.AlpacaDataUnavailable):
            client._http('stocks', ['KO'])


if __name__ == '__main__':
    unittest.main()
