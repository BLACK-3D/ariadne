"""Independent adversarial Public paper tests; all observations are fictional."""
from copy import deepcopy
from decimal import Inexact, ROUND_UP, localcontext
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from ariadne import options_public_engine as engine
from ariadne import public_market_data as source
from ariadne.options_public_paper import OptionsPublicPaper
from test_options_bankroll_eod import state as state6
from test_options_liquidity import book as gate_book

AT = '2026-11-03T15:00:00.100000+00:00'


def state7():
    prior = state6()
    return engine.migrate_flat(prior, activated_at='2026-11-03T14:59:59Z',
        previous_state_sha256=engine.digest(prior), source_cutoff_sha256='b' * 64)


def book(**changes):
    value = gate_book(source_id='public', snapshot_id='fictional-public-event', bid_size=2, ask_size=2)
    value.update(changes)
    return value


def observation(value, *, bid_at=None, ask_at=None):
    return {'instrument': {'symbol': value['contract_id'], 'type': 'OPTION'},
        'bid': value['bid'], 'ask': value['ask'], 'bid_size': value['bid_size'], 'ask_size': value['ask_size'],
        'bid_at': bid_at or value['quote_at'], 'ask_at': ask_at or value['quote_at']}


def request(value, *, side='buy', quantity=2, partial=False, request_id='independent-one'):
    return {'request_id': request_id, 'contract_id': value['contract_id'], 'side': side,
        'quantity': quantity, 'price_limit': '1.04' if side == 'buy' else '1.00', 'allow_partial': partial}


def step(state, value, *, at=AT, side='buy', quantity=2, obs=None, **changes):
    req = request(value, side=side, quantity=quantity, **changes)
    return engine.advance(state, [value], [req], [observation(value) if obs is None else obs], decided_at=at)


class IndependentPublicPaperReview(unittest.TestCase):
    def test_same_account_contributions_and_entry_exit_fees_reconcile(self):
        initial = state7(); snapshot = deepcopy(initial); value = book()
        entry = step(initial, value)
        self.assertEqual(initial, snapshot)
        self.assertEqual(entry['proposals'][0]['status'], 'paper_fill')
        self.assertEqual(entry['state']['cash'], '24790.70')
        self.assertEqual(entry['state']['contributions'], initial['contributions'])
        self.assertEqual(entry['state']['funding_count'], initial['funding_count'])
        self.assertEqual(entry['funding_added'], '0')
        sale = step(entry['state'], value, at='2026-11-03T15:00:01Z', side='sell')
        self.assertEqual(sale['state']['cash'], '24989.40')
        self.assertEqual(sale['state']['fees'], '2.60')
        self.assertEqual(sale['state']['position'], None)
        self.assertEqual(sale['state']['monthly_committed'], '210.60')
        self.assertEqual(sale['state']['contributions'], initial['contributions'])

    def test_new_bid_event_never_replenishes_unchanged_consumed_ask(self):
        value = book(); entry = step(state7(), value)
        sale = step(entry['state'], value, at='2026-11-03T15:00:01Z', side='sell')
        changed = book(snapshot_id='new-alias-old-ask', received_at='2026-11-03T15:00:02.050000Z')
        observed = observation(changed, bid_at='2026-11-03T15:00:02Z')
        retried = step(sale['state'], changed, at='2026-11-03T15:00:03Z', obs=observed)
        proposal = retried['proposals'][0]
        self.assertEqual(proposal['status'], 'no_fill')
        self.assertIn('durable_ask_depth_insufficient', proposal['reason_codes'])
        self.assertEqual(retried['state']['cash'], sale['state']['cash'])
        self.assertEqual(retried['state']['trade_count'], sale['state']['trade_count'])

    def test_new_ask_event_never_replenishes_consumed_bid_or_erases_reservation(self):
        value = book(); entry = step(state7(), value)
        sale = step(entry['state'], value, at='2026-11-03T15:00:01Z', side='sell')
        changed = book(snapshot_id='new-alias-old-bid', received_at='2026-11-03T15:00:02.050000Z')
        observed = observation(changed, ask_at='2026-11-03T15:00:02Z')
        retried = step(sale['state'], changed, at='2026-11-03T15:00:03Z', obs=observed)
        self.assertEqual(retried['proposals'][0]['status'], 'no_fill')
        self.assertIn('durable_entry_bid_depth_insufficient', retried['proposals'][0]['reason_codes'])
        self.assertEqual(retried['state']['cash'], sale['state']['cash'])

    def test_same_event_changed_price_refuses_entire_frame_without_mutation(self):
        value = book(); state = step(state7(), value)['state']; original = deepcopy(state)
        changed = book(bid='1.01', snapshot_id='conflicting-alias')
        with self.assertRaisesRegex(ValueError, 'event changed'):
            step(state, changed, at='2026-11-03T15:00:01Z', side='sell')
        self.assertEqual(state, original)

    def test_requested_sale_cannot_exceed_inventory_even_with_partial_permission(self):
        value = book(bid_size=20, ask_size=20); state = step(state7(), value)['state']
        output = step(state, value, side='sell', quantity=3, partial=True)
        self.assertEqual(output['proposals'][0]['status'], 'no_fill')
        self.assertIn('sell_exceeds_owned_inventory', output['proposals'][0]['reason_codes'])
        self.assertEqual(output['state']['position']['quantity'], 2)
        self.assertEqual(output['state']['cash'], state['cash'])

    def test_fixed_buy_price_and_full_quantity_never_changed_to_force_fill(self):
        state = state7(); value = book(ask_size=1)
        output = step(state, value)
        self.assertEqual(output['proposals'][0]['status'], 'no_fill')
        self.assertEqual(output['proposals'][0]['proposed_quantity'], 0)
        self.assertEqual(output['state']['cash'], state['cash'])
        self.assertEqual(output['state']['monthly_committed'], state['monthly_committed'])

    def test_late_decision_cannot_launder_old_quote_through_fresh_receipt(self):
        state = state7(); value = book(received_at='2026-11-03T15:01:00Z')
        output = step(state, value, at='2026-11-03T15:01:01Z')
        self.assertEqual(output['proposals'][0]['status'], 'no_fill')
        self.assertEqual(output['state']['cash'], state['cash'])
        self.assertIsNone(output['state']['position'])

    def test_request_cannot_supply_an_earlier_decision_clock(self):
        value = book(); req = request(value)
        req['decision_at'] = value['quote_at']
        with self.assertRaises(ValueError):
            engine.advance(state7(), [value], [req], [observation(value)], decided_at=AT)

    def test_public_missing_dated_fields_cannot_become_a_buy(self):
        value = book()
        row = {'instrument': {'symbol': value['contract_id'], 'type': 'OPTION'}, 'outcome': 'SUCCESS',
            'bid': value['bid'], 'ask': value['ask'], 'bidSize': 2, 'askSize': 2,
            'bidTimestamp': value['quote_at'], 'askTimestamp': value['quote_at'], 'volume': 10000, 'openInterest': 10000}
        normalized = source.normalize_quotes(json.dumps({'quotes':[row]}).encode(), received_at=value['received_at'])
        state = state7()
        result = engine.advance(state, normalized['books'], [request(value)], normalized['observations'], decided_at=AT)
        self.assertEqual(result['proposals'][0]['status'], 'no_fill')
        self.assertIsNone(result['state']['position'])
        self.assertEqual(result['state']['cash'], state['cash'])

    def test_summary_and_migration_are_isolated_from_hostile_decimal_context(self):
        state = step(state7(), book())['state']
        expected = engine.summarize(state)
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            self.assertEqual(engine.summarize(state), expected)
            fresh = state7()
            self.assertEqual(fresh['cash'], '25000')


if __name__ == '__main__':
    unittest.main()