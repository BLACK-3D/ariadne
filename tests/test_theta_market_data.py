"""Synthetic loopback responses only; no Terminal, network, account or key."""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from ariadne import theta_market_data as theta
from ariadne.options_liquidity import _book
from test_alpaca_market_data import FakeResponse, FakeConnection

CONTRACT = 'KO261120P00080000'
WATCH = [{'symbol': CONTRACT, 'type': 'OPTION'}, {'symbol': 'KO', 'type': 'EQUITY'}]
RECEIVED = '2026-11-05T15:00:02+00:00'


def fixture():
    quote = {'symbol': 'KO', 'timestamp': '2026-11-05T10:00:01.123',
        'bid': 1, 'ask': 1.04, 'bid_size': 8, 'ask_size': 9,
        'bid_exchange': 1, 'ask_exchange': 2, 'bid_condition': 0, 'ask_condition': 0}
    option = {**quote, 'expiration': '2026-11-20', 'strike': 80, 'right': 'put'}
    stock = {**quote, 'bid': 79.98, 'ask': 80.00}
    return {'schema': 1, 'source_id': theta.SOURCE_ID, 'received_at': RECEIVED,
        'options': [{'instrument': WATCH[0], 'payload': json.dumps([option]), 'received_at': RECEIVED}],
        'stocks': [{'instrument': WATCH[1], 'payload': json.dumps([stock]), 'received_at': RECEIVED}],
        'transport': {'source_id': theta.SOURCE_ID, 'operation': 'latest_quotes',
            'provider_authenticated': False, 'local_terminal_verified': False,
            'kind': 'local_terminal_unattested', 'options_feed': 'opra_unattested',
            'stocks_feed': 'nasdaq_basic_unattested'}}


def change(capture, kind, field, value):
    part = capture[kind][0]
    payload = json.loads(part['payload'])
    payload[0][field] = value
    part['payload'] = json.dumps(payload)
    return capture


class NormalizeTests(unittest.TestCase):
    def test_documented_flat_single_contract_identity_and_supplied_prices(self):
        value = theta.normalize_quotes(fixture(), expected_instruments=WATCH)
        book = value['books'][0]
        self.assertEqual((book['contract_id'], book['type'], book['strike'], book['expiry']),
            (CONTRACT, 'put', '80.000', '2026-11-20'))
        self.assertEqual((book['bid'], book['ask'], book['bid_size'], book['ask_size']), ('1', '1.04', 8, 9))
        self.assertEqual(book['underlying_quote']['price'], '80.0')
        self.assertEqual(_book(book)['raw']['source_id'], theta.SOURCE_ID)

    def test_naive_clock_is_not_promoted_to_utc_or_replaced_by_receipt(self):
        value = theta.normalize_quotes(fixture())
        observation = value['observations'][0]
        self.assertEqual(observation['quote_at_raw'], '2026-11-05T10:00:01.123')
        self.assertIsNone(observation['quote_at'])
        self.assertIsNone(observation['quote_at_ns'])
        self.assertEqual(observation['quote_timezone_basis'], 'unverified-naive')
        self.assertIn('source_timestamp_timezone_unverified', value['blockers'][0]['reasons'])
        self.assertNotIn('bid_at', observation)
        self.assertNotIn('ask_at', observation)

    def test_ambiguous_and_nonexistent_naive_dst_times_admit_no_utc_instant(self):
        for clock in ('2026-11-01T01:30:00.000', '2026-03-08T02:30:00.000'):
            with self.subTest(clock=clock):
                value = theta.normalize_quotes(change(fixture(), 'options', 'timestamp', clock))
                self.assertIsNone(value['books'][0]['quote_at'])
                self.assertIn('actual_quote_clock_missing', value['blockers'][0]['reasons'])

    def test_explicit_offset_clock_preserved_exactly_not_assumed_local(self):
        value = theta.normalize_quotes(change(fixture(), 'options', 'timestamp', '2026-11-05T10:00:01.123456789-05:00'))
        observation = value['observations'][0]
        self.assertEqual(observation['quote_at'], '2026-11-05T15:00:01.123456+00:00')
        self.assertEqual(observation['quote_at_ns'] % 1000000000, 123456789)
        self.assertEqual(observation['received_at_ns'], value['received_at_ns'])
        self.assertEqual(value['received_at_raw'], RECEIVED)

    def test_future_event_nanosecond_refused_against_its_own_receipt(self):
        value = change(fixture(), 'options', 'timestamp', '2026-11-05T15:00:02.000000001Z')
        with self.assertRaises(theta.ThetaDataError):
            theta.normalize_quotes(value)

    def test_terminal_never_authenticates_upstream_or_supplies_missing_metadata(self):
        value = theta.normalize_quotes(fixture())
        self.assertFalse(value['provider_authenticated'])
        self.assertFalse(value['local_terminal_verified'])
        book = value['books'][0]
        self.assertFalse(book['standard_deliverable_verified'])
        for key in ('underlying_activity', 'contract_activity', 'open_interest'):
            self.assertIsNone(book[key])
        self.assertIn('stock_feed_is_nasdaq_basic_bbo_not_consolidated_nbbo', value['blockers'][0]['reasons'])

    def test_true_numeric_or_changed_feed_attestations_refused(self):
        for key, bad in (('provider_authenticated', True), ('local_terminal_verified', True),
                         ('provider_authenticated', 0), ('options_feed', 'opra'), ('stocks_feed', 'sip')):
            value = fixture()
            value['transport'][key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(theta.ThetaDataError):
                theta.normalize_quotes(value)

    def test_nonregular_conditions_refused_on_both_sides(self):
        for condition in (1, 17, 21, 70, 71, True, None, '0'):
            for side in ('bid', 'ask'):
                with self.subTest(side=side, condition=condition), self.assertRaises(theta.ThetaDataError):
                    theta.normalize_quotes(change(fixture(), 'options', side + '_condition', condition))

    def test_contract_identity_mismatch_refused(self):
        for key, bad in (('symbol', 'F'), ('expiration', '2026-11-21'), ('strike', 81),
                         ('right', 'call'), ('strike', '80'), ('expiration', '20261120')):
            with self.subTest(key=key, bad=bad), self.assertRaises(theta.ThetaDataError):
                theta.normalize_quotes(change(fixture(), 'options', key, bad))

    def test_crossed_zero_thin_and_missing_values_stay_unrepaired(self):
        value = fixture()
        change(value, 'options', 'bid', 2)
        change(value, 'options', 'bid_size', 0)
        change(value, 'options', 'ask_size', None)
        book = theta.normalize_quotes(value)['books'][0]
        self.assertEqual((book['bid'], book['ask'], book['bid_size'], book['ask_size']), ('2', '1.04', 0, None))

    def test_missing_stock_ask_is_not_filled_from_bid(self):
        value = theta.normalize_quotes(change(fixture(), 'stocks', 'ask', None))
        self.assertIsNone(value['books'][0]['underlying_quote']['price'])
        self.assertIn('underlying_quote_missing', value['blockers'][0]['reasons'])

    def test_unexpected_empty_multirow_nested_nonfinite_duplicate_json_refused(self):
        for payload in ('[]', '[{},{}]', '{}', '[{"contract":{},"data":[]}]',
                        '[{"symbol":"KO","symbol":"F"}]', '[{"bid":NaN}]', 'bad'):
            value = fixture()
            value['options'][0]['payload'] = payload
            with self.subTest(payload=payload), self.assertRaises(theta.ThetaDataError):
                theta.normalize_quotes(value)

    def test_bounds_exact_watch_and_required_underlyings(self):
        for watch in ([], WATCH[:1], WATCH + WATCH, WATCH + [{'type': 'EQUITY', 'symbol': 'F'}],
                      [{'type': 'OPTION', 'symbol': 'KO261132P00080000'}, WATCH[1]],
                      [{'type': 'OPTION', 'symbol': '../snapshot/quote'}, WATCH[1]]):
            with self.subTest(watch=watch), self.assertRaises(theta.ThetaDataError):
                theta.instruments(watch)
        value = fixture()
        value['options'] *= 5
        with self.assertRaises(theta.ThetaDataError):
            theta.normalize_quotes(value)


class TransportTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'ARIADNE_OFFLINE': '0', 'HTTP_PROXY': 'http://forbidden.invalid'})
        env.start()
        self.addCleanup(env.stop)
        policy = patch.object(theta, 'install_network_policy', return_value={'mode': 'online'})
        policy.start()
        self.addCleanup(policy.stop)

    def test_fixed_numeric_loopback_exact_paths_no_keys_and_current_day_only(self):
        sample = fixture()
        responses = [FakeResponse(sample[k][0]['payload'].encode()) for k in ('options', 'stocks')]
        connections = [FakeConnection(response) for response in responses]
        with patch.object(theta.http.client, 'HTTPConnection', side_effect=connections) as factory, patch.object(theta, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat(RECEIVED)
            capture = theta.ThetaMarketDataClient(online=True).quotes(WATCH)
        self.assertEqual(factory.call_count, 2)
        for call in factory.call_args_list:
            self.assertEqual(call.args, ('127.0.0.1',))
            self.assertEqual(call.kwargs['port'], 25503)
        self.assertEqual(connections[0].requests[0][:2], ('GET', '/v3/option/snapshot/quote?symbol=KO&expiration=2026-11-20&right=put&strike=80.000&format=json&current_day_only=true'))
        self.assertEqual(connections[1].requests[0][:2], ('GET', '/v3/stock/snapshot/quote?symbol=KO&venue=nqb&format=json&current_day_only=true'))
        for conn in connections:
            headers = conn.requests[0][2]['headers']
            self.assertNotIn('Authorization', headers)
            self.assertNotIn('APCA-API-KEY-ID', headers)
            self.assertTrue(conn.closed)
        self.assertFalse(capture['transport']['provider_authenticated'])
        self.assertFalse(capture['transport']['local_terminal_verified'])

    def test_offline_and_optout_deny_before_even_loopback_connection(self):
        for online, env, policy in ((False, '0', 'online'), (True, '1', 'online'), (True, '0', 'offline')):
            with self.subTest(online=online, env=env, policy=policy), patch.dict(os.environ, {'ARIADNE_OFFLINE': env}), patch.object(theta, 'install_network_policy', return_value={'mode': policy}), patch.object(theta.http.client, 'HTTPConnection') as socket:
                with self.assertRaises(theta.ThetaDataUnavailable):
                    theta.ThetaMarketDataClient(online=online).quotes(WATCH)
                socket.assert_not_called()

    def test_no_host_port_url_or_key_arguments_and_scope_injection_denied(self):
        for kwargs in ({'host': 'example.invalid'}, {'port': 80}, {'key': 'fictional'}, {'url': 'http://localhost'}):
            with self.assertRaises(TypeError):
                theta.ThetaMarketDataClient(**kwargs)
        with patch.object(theta.http.client, 'HTTPConnection') as socket:
            for symbol in ('*', 'KO,F', 'KO&symbol=*', 'KO?format=csv', 'http://example.invalid'):
                with self.assertRaises(theta.ThetaDataError):
                    theta.ThetaMarketDataClient(online=True)._http({'type': 'EQUITY', 'symbol': symbol}, time.monotonic() + 10)
            socket.assert_not_called()

    def test_redirect_entitlement_and_error_bodies_not_read_or_retried(self):
        for status in (301, 302, 307, 401, 403, 429, 472, 500):
            response = FakeResponse(b'fictional-private-terminal-error', status=status)
            connection = FakeConnection(response)
            with self.subTest(status=status), patch.object(theta.http.client, 'HTTPConnection', return_value=connection) as factory:
                with self.assertRaises(theta.ThetaDataUnavailable) as caught:
                    theta.ThetaMarketDataClient(online=True)._http(WATCH[0], time.monotonic() + 10)
                self.assertEqual(factory.call_count, 1)
                self.assertEqual(response.reads, 0)
                self.assertNotIn('fictional-private-terminal-error', str(caught.exception))
                self.assertTrue(response.closed and connection.closed)

    def test_bad_content_oversize_and_multirow_results_refused(self):
        sample = fixture()['options'][0]['payload'].encode()
        for response in (FakeResponse(sample, headers={'Content-Type': 'text/html'}),
                         FakeResponse(sample, headers={'Content-Encoding': 'gzip'}),
                         FakeResponse(sample, headers={'Content-Length': str(theta.MAX_RAW + 1)}),
                         FakeResponse(b'x' * (theta.MAX_RAW + 1)), FakeResponse(b'[{},{}]')):
            with patch.object(theta.http.client, 'HTTPConnection', return_value=FakeConnection(response)):
                with self.assertRaises(theta.ThetaDataError):
                    theta.ThetaMarketDataClient(online=True)._http(WATCH[0], time.monotonic() + 10)

    def test_shared_deadline_all_requests_and_expiration_before_socket(self):
        sample = fixture()
        parts = sample['options'] + sample['stocks']
        client = theta.ThetaMarketDataClient(online=True)
        with patch.object(client, '_http', side_effect=parts) as http:
            client.quotes(WATCH)
        self.assertEqual(len({call.args[1] for call in http.call_args_list}), 1)
        with patch.object(theta.http.client, 'HTTPConnection') as socket:
            with self.assertRaises(theta.ThetaDataUnavailable):
                client._http(WATCH[0], time.monotonic() - 1)
            socket.assert_not_called()

    def test_transport_exception_redacted_and_close_refuses(self):
        client = theta.ThetaMarketDataClient(online=True)
        with patch.object(theta.http.client, 'HTTPConnection', side_effect=OSError('fictional-private-terminal-error')):
            with self.assertRaises(theta.ThetaDataUnavailable) as caught:
                client.quotes(WATCH)
        self.assertNotIn('fictional-private-terminal-error', str(caught.exception))
        client.close()
        with self.assertRaises(theta.ThetaDataUnavailable):
            client.quotes(WATCH)


if __name__ == '__main__':
    unittest.main()
