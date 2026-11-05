"""Independent Theta relay/timezone regressions; fictional captures, no Terminal."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from ariadne import theta_market_data as source
from ariadne import options_feed_qualification as bridge
from test_alpaca_integration_review import snapshot, request, supplement, CONTRACT, WATCH, AT
from test_alpaca_market_data import FakeConnection, FakeResponse


def capture(timestamp='2026-11-03T10:00:00.000'):
    option = {'symbol': 'KO', 'expiration': '2026-11-20', 'right': 'call', 'strike': 80,
              'timestamp': timestamp, 'bid': 1, 'ask': 1.04, 'bid_size': 20, 'ask_size': 20,
              'bid_exchange': 1, 'ask_exchange': 2, 'bid_condition': 0, 'ask_condition': 0}
    stock = {k: deepcopy(v) for k, v in option.items() if k not in ('expiration', 'right', 'strike')}
    stock.update(bid=79.85, ask=79.90)
    def part(instrument, row):
        return {'instrument': instrument, 'payload': json.dumps([row], separators=(',', ':')),
                'received_at': '2026-11-03T15:00:00.050000Z'}
    return {'schema': 1, 'source_id': source.SOURCE_ID,
            'received_at': '2026-11-03T15:00:00.050000Z',
            'options': [part(WATCH[0], option)], 'stocks': [part(WATCH[1], stock)],
            'transport': {'source_id': source.SOURCE_ID, 'operation': 'latest_quotes',
                          'provider_authenticated': False, 'local_terminal_verified': False,
                          'kind': 'local_terminal_unattested', 'options_feed': 'opra_unattested',
                          'stocks_feed': 'nasdaq_basic_unattested'}}


class IndependentThetaReview(unittest.TestCase):
    def test_naive_snapshot_timestamp_never_infers_utc_from_receipt_or_new_york(self):
        result = source.normalize_quotes(capture(), expected_instruments=WATCH)
        observed = result['observations'][0]
        self.assertEqual(observed['quote_at_raw'], '2026-11-03T10:00:00.000')
        self.assertIsNone(observed['quote_at'])
        self.assertIsNone(observed['quote_at_ns'])
        self.assertIsNone(result['books'][0]['quote_at'])
        self.assertIn('source_timestamp_timezone_unverified', result['blockers'][0]['reasons'])

    def test_ambiguous_and_nonexistent_dst_local_times_never_acquire_utc_identity(self):
        for value in ('2026-11-01T01:30:00.000', '2026-03-08T02:30:00.000'):
            with self.subTest(value=value):
                observed = source.normalize_quotes(capture(value))['observations'][0]
                self.assertEqual(observed['quote_at_raw'], value)
                self.assertIsNone(observed['quote_at_ns'])
                self.assertIsNone(observed['quote_at'])

    def test_even_complete_independent_metadata_cannot_fix_missing_option_clock(self):
        owner = snapshot()
        original = deepcopy(owner)
        result = bridge.qualify(owner, request(owner), source.normalize_quotes(capture()),
                                supplement=supplement(), decided_at=AT, source_id=source.SOURCE_ID)
        self.assertEqual(result['proposals'][0]['status'], 'blocked')
        self.assertIn('actual_quote_clock_missing', result['proposals'][0]['reason_codes'])
        self.assertEqual(result['paper_fills'], 0)
        self.assertIs(result['paper_submission_ready'], False)
        self.assertIs(result['provider_authenticated'], False)
        self.assertEqual(result['funding_added'], '0')
        self.assertEqual(owner, original)

    def test_local_capture_cannot_claim_verified_terminal_or_authenticated_upstream(self):
        for field in ('provider_authenticated', 'local_terminal_verified'):
            value = capture()
            value['transport'][field] = True
            with self.subTest(field=field), self.assertRaises(source.ThetaDataError):
                source.normalize_quotes(value)

    def test_loopback_relay_requires_explicit_online_even_when_python_allows_localhost(self):
        client = source.ThetaMarketDataClient(online=True)
        with patch.dict(os.environ, {'ARIADNE_OFFLINE': '1'}), \
                patch.object(source.http.client, 'HTTPConnection') as connect:
            with self.assertRaises(source.ThetaDataUnavailable):
                client.quotes(WATCH)
            connect.assert_not_called()
        client.close()

    def test_only_fixed_numeric_loopback_read_route_no_credentials_or_proxies(self):
        payload = capture()['options'][0]['payload'].encode()
        response = FakeResponse(payload)
        connection = FakeConnection(response)
        client = source.ThetaMarketDataClient(online=True)
        with patch.dict(os.environ, {'ARIADNE_OFFLINE': '0', 'HTTP_PROXY': 'http://untrusted.invalid'}), \
                patch.object(source, 'install_network_policy', return_value={'mode': 'online'}), \
                patch.object(source.http.client, 'HTTPConnection', return_value=connection) as connect:
            client._http(WATCH[0], source.time.monotonic() + 10)
        self.assertEqual(connect.call_args.args, ('127.0.0.1',))
        self.assertEqual(connect.call_args.kwargs['port'], 25503)
        method, route, params = connection.requests[0]
        self.assertEqual(method, 'GET')
        self.assertTrue(route.startswith('/v3/option/snapshot/quote?symbol=KO&'))
        self.assertIn('current_day_only=true', route)
        self.assertNotIn('*', route)
        self.assertFalse(any('auth' in key.lower() or 'secret' in key.lower() for key in params['headers']))
        self.assertTrue(connection.closed and response.closed)
        client.close()

    def test_nonregular_condition_and_response_contract_substitution_refused(self):
        for field, bad in (('bid_condition', 1), ('ask_condition', True), ('symbol', 'F'), ('strike', 81)):
            value = capture()
            row = json.loads(value['options'][0]['payload'])
            row[0][field] = bad
            value['options'][0]['payload'] = json.dumps(row)
            with self.subTest(field=field), self.assertRaises(source.ThetaDataError):
                source.normalize_quotes(value)

    def test_explicit_aware_event_keeps_exact_ns_but_never_attests_provider(self):
        result = source.normalize_quotes(capture('2026-11-03T15:00:00.000000123Z'))
        self.assertEqual(result['observations'][0]['quote_at_ns'] % 1_000_000_000, 123)
        self.assertIs(result['provider_authenticated'], False)
        self.assertIs(result['local_terminal_verified'], False)
        self.assertIn('live_entitlements_unverified', result['blockers'][0]['reasons'])

    def test_submicrosecond_future_receipt_cannot_reach_admission(self):
        value = capture('2026-11-03T15:00:00.050000001Z')
        value['received_at'] = '2026-11-03T15:00:00.050000999Z'
        for part in value['options'] + value['stocks']:
            part['received_at'] = value['received_at']
        owner = snapshot()
        with self.assertRaises(ValueError):
            bridge.qualify(owner, request(owner), source.normalize_quotes(value),
                           supplement=supplement(), decided_at='2026-11-03T15:00:00.050000Z',
                           source_id=source.SOURCE_ID)


if __name__ == '__main__':
    unittest.main()
