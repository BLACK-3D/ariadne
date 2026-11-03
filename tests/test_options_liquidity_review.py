"""Independent adversarial checks for saved-book paper proposals.

Fixtures are synthetic; passing them does not authenticate a provider or execute
an order. Tests intentionally do not reuse the implementation agent's fixtures.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext, Inexact, Rounded
import unittest

from ariadne import options_liquidity as gate

CLOCK = '2026-11-03T15:00:00+00:00'


def book():
    start, end = date(2026, 10, 6), date(2026, 11, 2)
    sessions = []
    while start <= end:
        if start.weekday() < 5:
            sessions.append({'session': start.isoformat(), 'dollar_volume': '30000000',
                             'published_at': start.isoformat() + 'T22:00:00+00:00'})
        start += timedelta(days=1)
    assert len(sessions) == 20
    return {'schema': 1, 'source_id': 'independent-synthetic-source',
            'snapshot_id': 'independent-snapshot-1', 'quote_kind': 'top_of_book',
            'underlying_symbol': 'KO', 'underlying_type': 'stock', 'contract_id': 'KO261120C00080000',
            'expiry': '2026-11-20', 'type': 'call', 'strike': '80', 'multiplier': 100,
            'standard_deliverable_verified': True,
            'deliverable_verification_source': 'independent-synthetic-attestation',
            'bid': '1.00', 'ask': '1.02', 'bid_size': 10, 'ask_size': 10,
            'quote_at': '2026-11-03T14:59:58+00:00',
            'received_at': '2026-11-03T14:59:58.500000+00:00',
            'underlying_quote': {'price': '80', 'quote_at': '2026-11-03T14:59:58+00:00',
                                 'received_at': '2026-11-03T14:59:58.500000+00:00'},
            'underlying_activity': {'sessions': sessions},
            'contract_activity': {'volume': 1000, 'as_of': '2026-11-03T14:59:58+00:00',
                                  'published_at': '2026-11-03T14:59:58+00:00'},
            'open_interest': {'value': 2000, 'session': '2026-11-02',
                              'published_at': '2026-11-03T13:30:00+00:00'}}


def request(raw=None, **changes):
    raw = book() if raw is None else raw
    result = {'request_id': 'independent-request-1', 'source_id': raw['source_id'],
              'snapshot_id': raw['snapshot_id'], 'contract_id': raw['contract_id'],
              'decision_at': CLOCK, 'side': 'buy', 'quantity': 5,
              'price_limit': '1.02', 'allow_partial': False}
    result.update(changes)
    return result


class IndependentLiquidityTests(unittest.TestCase):
    def batch(self, books, requests):
        result = gate.evaluate_batch(books, requests)
        self.assertFalse(result['provider_authenticated'])
        self.assertFalse(result['portfolio_controls_checked'])
        self.assertTrue(result['top_of_book_only'])
        self.assertTrue(all(p['execution_confirmed'] is False for p in result['proposals']))
        return result

    def refuse(self, raw, req=None):
        try:
            result = self.batch([raw], [request(raw) if req is None else req])
        except gate.LiquidityError:
            return
        self.assertEqual(result['proposals'][0]['status'], 'no_fill')
        self.assertEqual(result['proposals'][0]['proposed_quantity'], 0)

    def test_buy_uses_ask_and_sell_uses_actual_bid(self):
        raw = book()
        buy = self.batch([raw], [request(raw)])['proposals'][0]
        sell = self.batch([raw], [request(raw, side='sell', price_limit='1.00')])['proposals'][0]
        self.assertEqual((buy['status'], buy['proposed_quantity']), ('paper_proposal', 5))
        self.assertEqual(Decimal(buy['quoted_price']), Decimal('1.02'))
        self.assertEqual((sell['status'], sell['proposed_quantity']), ('paper_proposal', 5))
        self.assertEqual(Decimal(sell['quoted_price']), Decimal('1.00'))

    def test_price_protection_is_not_moved_to_force_fill(self):
        self.refuse(book(), request(price_limit='1.01'))
        self.refuse(book(), request(side='sell', price_limit='1.01'))

    def test_exit_does_not_need_entry_liquidity_metrics(self):
        raw = book(); raw['underlying_quote'] = None; raw['underlying_activity'] = None
        raw['contract_activity'] = None; raw['open_interest'] = None
        out = self.batch([raw], [request(raw, side='sell', price_limit='1.00')])
        self.assertEqual(out['proposals'][0]['status'], 'paper_proposal')
        self.refuse(raw)

    def test_fresh_buyer_remains_usable_when_ask_is_absent(self):
        raw = book(); raw['ask'] = None; raw['ask_size'] = None
        out = self.batch([raw], [request(raw, side='sell', price_limit='1.00')])
        self.assertEqual(out['proposals'][0]['status'], 'paper_proposal')
        self.refuse(raw)

    def test_crossed_quote_is_not_an_executable_book(self):
        raw = book(); raw['bid'] = '1.03'
        self.refuse(raw)
        self.refuse(raw, request(raw, side='sell', price_limit='1.00'))

    def test_missing_or_insufficient_bid_refuses_sell(self):
        for value in (None, 0, 4):
            with self.subTest(value=value):
                raw = book(); raw['bid_size'] = value
                self.refuse(raw, request(raw, side='sell', price_limit='1.00'))

    def test_entry_requires_whole_requested_quantity_on_both_sides(self):
        for side in ('ask_size', 'bid_size'):
            raw = book(); raw[side] = 4
            self.refuse(raw)

    def test_partial_sell_is_explicit_and_uses_only_actual_bid_depth(self):
        raw = book(); raw['bid_size'] = 3
        out = self.batch([raw], [request(raw, side='sell', price_limit='1.00', allow_partial=True)])
        proposal = out['proposals'][0]
        self.assertEqual((proposal['status'], proposal['requested_quantity'], proposal['proposed_quantity'], proposal['remainder_quantity']),
                         ('paper_proposal', 5, 3, 2))
        self.assertEqual(Decimal(proposal['quoted_price']), Decimal('1.00'))
        self.assertEqual(out['depth_consumption'][0]['bid_consumed'], 3)

    def test_partial_buy_is_never_silently_inferred(self):
        with self.assertRaises(gate.LiquidityError):
            self.batch([book()], [request(allow_partial=True)])

    def test_same_snapshot_cannot_supply_repeated_full_depth(self):
        raw = book(); first = request(raw, quantity=6)
        second = request(raw, request_id='second', quantity=6)
        out = self.batch([raw, deepcopy(raw)], [first, second])
        self.assertEqual([p['proposed_quantity'] for p in out['proposals']], [6, 0])
        self.assertEqual(out['depth_consumption'][0]['ask_consumed'], 6)
        self.assertEqual(out['depth_consumption'][0]['ask_remaining'], 4)

    def test_source_and_snapshot_aliases_do_not_replenish_depth(self):
        raw = book(); alias = deepcopy(raw)
        alias.update(source_id='alias-source', snapshot_id='alias-snapshot')
        out = self.batch([raw, alias], [request(raw, quantity=6), request(alias, request_id='second', quantity=6)])
        self.assertEqual([p['proposed_quantity'] for p in out['proposals']], [6, 0])
        self.assertEqual(len(out['depth_consumption']), 1)

    def test_conflicting_aliases_refuse_entire_batch(self):
        raw = book(); alias = deepcopy(raw)
        alias.update(source_id='alias-source', snapshot_id='alias-snapshot', bid_size=11)
        before = deepcopy((raw, alias))
        with self.assertRaises(gate.LiquidityError):
            self.batch([raw, alias], [request(raw), request(alias, request_id='second')])
        self.assertEqual((raw, alias), before)

    def test_future_quote_received_and_metric_clocks_refuse(self):
        variants = [('quote_at', '2026-11-03T15:00:00.000001+00:00'),
                    ('received_at', '2026-11-03T15:00:00.000001+00:00')]
        for key, value in variants:
            with self.subTest(key=key):
                raw = book(); raw[key] = value; self.refuse(raw)
        for section, key in [('contract_activity', 'as_of'), ('contract_activity', 'published_at'),
                             ('open_interest', 'published_at')]:
            with self.subTest(section=section, key=key):
                raw = book(); raw[section][key] = '2026-11-03T15:00:00.000001+00:00'; self.refuse(raw)

    def test_stale_actual_quote_is_not_replaced_by_received_time(self):
        raw = book(); raw['quote_at'] = '2026-11-03T14:59:54.999999+00:00'
        raw['received_at'] = '2026-11-03T14:59:59+00:00'
        self.refuse(raw)

    def test_aware_date_only_and_naive_quote_clocks_are_refused(self):
        for stamp in (None, '2026-11-03', '2026-11-03T14:59:58'):
            with self.subTest(stamp=stamp):
                raw = book(); raw['quote_at'] = stamp; self.refuse(raw)

    def test_current_day_open_interest_is_not_prior_session_evidence(self):
        raw = book(); raw['open_interest']['session'] = '2026-11-03'; self.refuse(raw)

    def test_adv_requires_exact_twenty_completed_sessions(self):
        raw = book(); raw['underlying_activity']['sessions'].pop(); self.refuse(raw)
        raw = book(); raw['underlying_activity']['sessions'][0]['session'] = '2026-10-05'; self.refuse(raw)
        raw = book(); raw['underlying_activity']['sessions'][-1]['published_at'] = '2026-11-03T15:00:01+00:00'; self.refuse(raw)

    def test_low_activity_does_not_fake_liquidity(self):
        raw = book(); raw['contract_activity']['volume'] = 499; self.refuse(raw)
        raw = book(); raw['open_interest']['value'] = 999; self.refuse(raw)
        raw = book()
        for row in raw['underlying_activity']['sessions']: row['dollar_volume'] = '24999999.99'
        self.refuse(raw)

    def test_high_volume_does_not_override_spread_or_depth(self):
        raw = book(); raw['contract_activity']['volume'] = 10000000; raw['ask'] = '1.06'
        self.refuse(raw, request(raw, price_limit='1.06'))
        raw = book(); raw['contract_activity']['volume'] = 10000000; raw['bid_size'] = 0; self.refuse(raw)

    def test_contract_identifier_must_match_expiry_type_strike_and_root(self):
        for key, value in [('expiry', '2026-11-19'), ('type', 'put'), ('strike', '81'), ('underlying_symbol', 'F')]:
            with self.subTest(key=key):
                raw = book(); raw[key] = value; self.refuse(raw)

    def test_standard_deliverable_requires_explicit_attestation(self):
        raw = book(); raw['standard_deliverable_verified'] = False; self.refuse(raw)
        raw = book(); raw['deliverable_verification_source'] = None; self.refuse(raw)
        raw = book(); raw['multiplier'] = 10; self.refuse(raw)

    def test_malformed_numbers_and_boolean_sizes_are_not_admitted(self):
        for key, value in [('bid', 'NaN'), ('ask', 'Infinity'), ('bid_size', True), ('ask_size', 1.5), ('multiplier', True)]:
            with self.subTest(key=key):
                raw = book(); raw[key] = value
                with self.assertRaises(gate.LiquidityError): self.batch([raw], [request(raw)])
        with self.assertRaises(gate.LiquidityError): self.batch([book()], [request(quantity=True)])

    def test_input_and_request_objects_are_immutable(self):
        raw = book(); req = request(raw); before = deepcopy((raw, req))
        self.batch([raw], [req]); self.assertEqual((raw, req), before)

    def test_exact_five_second_boundary_is_accepted_without_rounding(self):
        raw = book(); raw['quote_at'] = '2026-11-03T14:59:55+00:00'
        raw['received_at'] = '2026-11-03T14:59:55.500000+00:00'
        out = self.batch([raw], [request(raw)])
        self.assertEqual(out['proposals'][0]['status'], 'paper_proposal')

    def test_actual_quote_before_open_and_decision_at_close_refuse(self):
        raw = book(); raw['quote_at'] = '2026-11-03T14:29:59+00:00'
        raw['received_at'] = '2026-11-03T14:29:59.500000+00:00'
        out = self.batch([raw], [request(raw, decision_at='2026-11-03T14:30:00+00:00')])
        self.assertIn('quote_outside_decision_session', out['proposals'][0]['reason_codes'])
        raw = book(); raw['quote_at'] = '2026-11-03T20:59:59+00:00'
        raw['received_at'] = '2026-11-03T20:59:59.500000+00:00'
        out = self.batch([raw], [request(raw, side='sell', price_limit='1.00', decision_at='2026-11-03T21:00:00+00:00')])
        self.assertIn('outside_regular_session', out['proposals'][0]['reason_codes'])

    def test_no_fill_does_not_consume_book_depth(self):
        raw = book()
        out = self.batch([raw], [request(raw, quantity=10, price_limit='1.01'),
                                 request(raw, quantity=10, request_id='second')])
        self.assertEqual([p['proposed_quantity'] for p in out['proposals']], [0, 10])
        self.assertEqual(out['depth_consumption'][0]['ask_consumed'], 10)

    def test_entry_bid_reservation_covers_aggregate_buys(self):
        raw = book(); raw['bid_size'] = 6; raw['ask_size'] = 10
        out = self.batch([raw], [request(raw, quantity=5), request(raw, quantity=5, request_id='second')])
        self.assertEqual([p['proposed_quantity'] for p in out['proposals']], [5, 0])
        self.assertEqual(out['depth_consumption'][0]['bid_reserved_for_entries'], 5)
        self.assertEqual(out['depth_consumption'][0]['bid_consumed'], 0)

    def test_entry_reservation_does_not_block_actual_sell(self):
        raw = book(); raw['bid_size'] = 5
        out = self.batch([raw], [request(raw), request(raw, request_id='exit', side='sell', price_limit='1.00')])
        self.assertEqual([p['proposed_quantity'] for p in out['proposals']], [5, 5])
        self.assertEqual(out['depth_consumption'][0]['bid_consumed'], 5)

    def test_exit_remains_possible_above_the_entry_stock_price_gate(self):
        raw = book(); raw['underlying_quote']['price'] = '101'
        self.refuse(raw)
        out = self.batch([raw], [request(raw, side='sell', price_limit='1.00')])
        self.assertEqual(out['proposals'][0]['status'], 'paper_proposal')

    def test_decimal_context_cannot_weaken_spread_or_price_checks(self):
        with localcontext() as context:
            context.prec = 2; context.traps[Inexact] = True; context.traps[Rounded] = True
            out = self.batch([book()], [request()])
            self.assertEqual(out['proposals'][0]['status'], 'paper_proposal')
            raw = book(); raw['ask'] = '1.052'; self.refuse(raw, request(raw, price_limit='1.052'))


if __name__ == '__main__':
    unittest.main()
