"""Independent adversarial feed review; fictional observations, no owner writes."""
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
from ariadne import alpaca_market_data as source
from ariadne import alpaca_credentials as credentials
from ariadne import options_feed_qualification as bridge
from test_options_liquidity import book as liquidity_book
from test_options_bankroll_eod import state as state6

CONTRACT = 'KO261120C00080000'
WATCH = [{'symbol': CONTRACT, 'type': 'OPTION'}, {'symbol': 'KO', 'type': 'EQUITY'}]
AT = '2026-11-03T15:00:00.100000Z'
SECRET = 'fictional-review-secret-never-a-real-key'
KEY = 'fictional-review-key'


def capture():
    def part(kind):
        row = {'t': '2026-11-03T15:00:00.000000123Z', 'bp': 1, 'ap': 1.04,
               'bs': 20, 'as': 20, 'bx': 'A', 'ax': 'B', 'c': ' '}
        symbol = CONTRACT
        if kind == 'stocks':
            row.update(bp=79.85, ap=79.90, c=['R'], z='A')
            symbol = 'KO'
        return {'payload': json.dumps({'quotes': {symbol: row}}, separators=(',', ':')),
                'received_at': '2026-11-03T15:00:00.050000Z'}
    return {'schema': 1, 'source_id': source.SOURCE_ID,
            'received_at': '2026-11-03T15:00:00.050000Z',
            'options': part('options'), 'stocks': part('stocks'),
            'transport': {'source_id': source.SOURCE_ID, 'operation': 'latest_quotes',
                          'provider_authenticated': True, 'options_feed': 'opra', 'stocks_feed': 'sip'}}


def alter_event(value, at):
    body = json.loads(value['options']['payload'])
    body['quotes'][CONTRACT]['t'] = at
    value['options']['payload'] = json.dumps(body, separators=(',', ':'))
    return value


def snapshot():
    state = state6()
    state_json = bridge.canonical(state).decode('utf-8')
    epoch = {'global_pause_rowid': 31, 'options_revision': 1, 'brief_revision': 13}
    return {'revision': 4, 'account_id': 'fictional-existing-shared-account', 'state': state,
            'state_json': state_json, 'state_sha256': bridge.digest(state), 'policy_sha256': 'b' * 64,
            'epoch': epoch, 'policy': deepcopy(epoch),
            'global_paused': False, 'internet_enabled': True, 'options_enabled': True,
            'options_paused': False, 'brief_enabled': True, 'brief_paused': False,
            'brief_hold': None, 'options_hold': None, 'qqq_hold': None}


def request(owner):
    return {**bridge.binding(owner), 'request_id': 'fictional-review-admission',
            'requests': [{'request_id': 'fictional-one', 'contract_id': CONTRACT,
                          'side': 'buy', 'quantity': 2, 'price_limit': '1.04', 'allow_partial': False}]}


def supplement():
    full = liquidity_book()
    return {'schema': 1, 'contracts': [{'contract_id': CONTRACT,
            **{name: deepcopy(full[name]) for name in ('underlying_type', 'standard_deliverable_verified',
                 'deliverable_verification_source', 'underlying_activity', 'contract_activity', 'open_interest')}}]}


def qualify(owner=None, body=None, normalized=None, *, metadata=None, at=AT):
    owner = snapshot() if owner is None else owner
    return bridge.qualify(owner, request(owner) if body is None else body,
        source.normalize_quotes(capture(), expected_instruments=WATCH) if normalized is None else normalized,
        supplement=metadata, decided_at=at)


class IndependentAlpacaReview(unittest.TestCase):
    def test_transport_label_in_saved_bytes_never_authenticates_normalization(self):
        result = source.normalize_quotes(capture(), expected_instruments=WATCH)
        self.assertIs(result['provider_authenticated'], False)
        self.assertEqual(result['books'][0]['bid_size'], 20)
        self.assertEqual(result['books'][0]['ask_size'], 20)
        self.assertEqual(result['books'][0]['bid'], '1')
        self.assertEqual(result['books'][0]['ask'], '1.04')
        self.assertEqual(result['observations'][0]['quote_kind'], 'nbbo_message')
        self.assertNotIn('bid_at', result['observations'][0])
        self.assertNotIn('ask_at', result['observations'][0])

    def test_one_nanosecond_future_event_cannot_hide_in_microsecond_projection(self):
        value = alter_event(capture(), '2026-11-03T15:00:00.050000001Z')
        with self.assertRaises(source.AlpacaDataError):
            source.normalize_quotes(value, expected_instruments=WATCH)

    def test_submicrosecond_future_event_or_receipt_cannot_be_admitted_at_earlier_decision(self):
        for event in ('2026-11-03T15:00:00.050000001Z', '2026-11-03T15:00:00.000000123Z'):
            value = alter_event(capture(), event)
            value['received_at'] = value['options']['received_at'] = value['stocks']['received_at'] = '2026-11-03T15:00:00.050000999Z'
            with self.subTest(event=event):
                try:
                    result = qualify(normalized=source.normalize_quotes(value), metadata=supplement(),
                                     at='2026-11-03T15:00:00.050000Z')
                except ValueError:
                    continue
                self.assertEqual(result['proposals'][0]['status'], 'blocked')
                self.assertEqual(result['proposals'][0]['proposed_quantity'], 0)

    def test_distinct_nanosecond_events_preserved_even_when_gate_projection_collides(self):
        first = source.normalize_quotes(alter_event(capture(), '2026-11-03T15:00:00.000000001Z'))
        second = source.normalize_quotes(alter_event(capture(), '2026-11-03T15:00:00.000000002Z'))
        self.assertEqual(first['books'][0]['quote_at'], second['books'][0]['quote_at'])
        self.assertEqual(second['observations'][0]['quote_at_ns'] - first['observations'][0]['quote_at_ns'], 1)
        self.assertNotEqual(first['raw_sha256'], second['raw_sha256'])

    def test_missing_event_is_not_replaced_by_actual_receipt(self):
        result = source.normalize_quotes(alter_event(capture(), None))
        self.assertIsNone(result['books'][0]['quote_at'])
        self.assertIsNone(result['observations'][0]['quote_at_ns'])
        self.assertIn('actual_quote_clock_missing', result['blockers'][0]['reasons'])

    def test_missing_liquidity_and_deliverable_evidence_denies_entry(self):
        result = qualify()
        self.assertEqual(result['proposals'][0]['status'], 'blocked')
        self.assertEqual(result['proposals'][0]['proposed_quantity'], 0)
        self.assertEqual(result['paper_fills'], 0)
        self.assertIs(result['paper_submission_ready'], False)

    def test_source_metadata_bridge_admits_only_candidate_and_preserves_every_owner_byte(self):
        owner = snapshot()
        original = deepcopy(owner)
        result = qualify(owner, metadata=supplement())
        self.assertEqual(result['proposals'][0]['status'], 'admission_candidate')
        self.assertEqual(owner, original)
        for key in ('paper_submission_ready', 'ledger_write_performed', 'provider_authenticated',
                    'execution_confirmed', 'bankroll_created', 'automatic_topups'):
            self.assertIs(result[key], False)
        self.assertEqual(result['paper_fills'], 0)
        self.assertEqual(result['funding_added'], '0')
        self.assertEqual(result['bankroll_total'], '25000')
        self.assertIn('versioned_alpaca_paper_ledger_required', result['blockers'])

    def test_fee_inclusive_entry_cap_never_shrinks_quantity_to_force_admission(self):
        owner = snapshot()
        body = request(owner)
        body['requests'][0]['quantity'] = 10
        result = qualify(owner, body, metadata=supplement())
        self.assertEqual(result['proposals'][0]['status'], 'blocked')
        self.assertIn('entry_cap_or_cash_exceeded', result['proposals'][0]['reason_codes'])
        self.assertEqual(result['proposals'][0]['proposed_quantity'], 0)
        self.assertEqual(result['entry_all_in_limit'], '1000')

    def test_supplement_cannot_replace_quote_or_fill_provider_missing_side_clock(self):
        for field, value in [('bid', '5'), ('source_id', 'public'), ('quote_at', AT), ('ask_size', 1000)]:
            metadata = {'schema': 1, 'contracts': [{'contract_id': CONTRACT, field: value}]}
            with self.subTest(field=field), self.assertRaises(ValueError):
                qualify(metadata=metadata)

    def test_owner_pause_and_all_holds_block_before_qualification(self):
        for field, bad in [('global_paused', True), ('internet_enabled', False), ('options_enabled', False),
                           ('options_paused', True), ('brief_enabled', False), ('brief_paused', True),
                           ('brief_hold', {'status': 'failed'}), ('options_hold', {'status': 'interrupted'})]:
            owner = snapshot()
            owner[field] = bad
            before = deepcopy(owner)
            with self.subTest(field=field), self.assertRaises(ValueError):
                qualify(owner, metadata=supplement())
            self.assertEqual(owner, before)

    def test_stale_binding_and_caller_clock_refused(self):
        owner = snapshot()
        body = request(owner)
        body['expected_revision'] += 1
        with self.assertRaises(ValueError):
            qualify(owner, body)
        body = request(owner)
        body['requests'][0]['decision_at'] = AT
        with self.assertRaises(ValueError):
            qualify(owner, body)

    def test_old_permission_epoch_and_qqq_hold_never_become_submission_ready(self):
        owner = snapshot()
        owner['policy']['brief_revision'] = 11
        owner['qqq_hold'] = {'status': 'failed'}
        result = qualify(owner, metadata=supplement())
        self.assertIn('paper_permission_epoch_mismatch_requires_explicit_migration', result['blockers'])
        self.assertIn('qqq_workflow_hold_preserved', result['blockers'])
        self.assertIs(result['paper_submission_ready'], False)
        self.assertEqual(result['paper_fills'], 0)

    def test_stale_actual_event_with_fresh_receipt_is_blocked(self):
        value = capture()
        value['received_at'] = value['options']['received_at'] = value['stocks']['received_at'] = '2026-11-03T15:01:00Z'
        result = qualify(normalized=source.normalize_quotes(value), metadata=supplement(), at='2026-11-03T15:01:01Z')
        self.assertEqual(result['proposals'][0]['status'], 'blocked')
        self.assertIn('quote_stale', result['proposals'][0]['reason_codes'])

    def test_indicative_or_iex_labels_never_reach_gate(self):
        for field, value in [('options_feed', 'indicative'), ('stocks_feed', 'iex')]:
            data = capture()
            data['transport'][field] = value
            with self.subTest(field=field), self.assertRaises(source.AlpacaDataError):
                source.normalize_quotes(data)

    def test_unrequested_symbols_and_response_secret_fields_are_rejected_without_echo(self):
        value = capture()
        payload = json.loads(value['options']['payload'])
        payload['private_secret'] = SECRET
        value['options']['payload'] = json.dumps(payload)
        with self.assertRaises(source.AlpacaDataError) as error:
            source.normalize_quotes(value)
        self.assertNotIn(SECRET, str(error.exception))
        watch = [{'symbol': 'F', 'type': 'EQUITY'}, {'symbol': 'F261120C00010000', 'type': 'OPTION'}]
        with self.assertRaises(source.AlpacaDataError):
            source.normalize_quotes(capture(), expected_instruments=watch)

    def test_inherited_offline_refuses_before_any_https_constructor(self):
        client = source.AlpacaMarketDataClient(KEY, SECRET, online=True)
        with patch.dict(os.environ, {'ARIADNE_OFFLINE': '1'}), \
                patch.object(source.http.client, 'HTTPSConnection') as connection:
            with self.assertRaises(source.AlpacaDataUnavailable):
                client.quotes(WATCH)
            connection.assert_not_called()
        client.close()

    def test_unsupported_operation_refuses_before_any_https_constructor(self):
        client = source.AlpacaMarketDataClient(KEY, SECRET, online=True)
        with patch.object(source.http.client, 'HTTPSConnection') as connection:
            for operation in ('orders', 'accounts', 'https://api.alpaca.markets/v2/orders'):
                with self.subTest(operation=operation), self.assertRaises(source.AlpacaDataError):
                    client._http(operation, ['KO'])
            connection.assert_not_called()
        client.close()

    def test_partial_private_environment_never_falls_back_to_stored_credentials(self):
        with patch.dict(os.environ, {'ARIADNE_ALPACA_KEY_ID': KEY}, clear=True), \
                patch.object(credentials, '_raw') as stored:
            with self.assertRaises(ValueError) as error:
                credentials.load(None)
            stored.assert_not_called()
            self.assertNotIn(KEY, str(error.exception))


if __name__ == '__main__':
    unittest.main()
