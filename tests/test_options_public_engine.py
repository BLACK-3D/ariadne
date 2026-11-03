"""Synthetic quote/account fixtures only; no provider requests or return claims."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Inexact, localcontext
import unittest

from ariadne import options_public_engine as engine
from test_options_bankroll_eod import state as old_state
from test_options_liquidity import book as original_book, AT


def state():
    return engine.migrate_flat(old_state(),activated_at='2026-11-03T14:59:00+00:00',previous_state_sha256='1'*64,source_cutoff_sha256='2'*64)


def book(**changes):
    return original_book(source_id='public',**changes)


def observation(value, **changes):
    result = {'instrument':{'symbol':value['contract_id'],'type':'OPTION'},'bid':value['bid'],'ask':value['ask'],
        'bid_size':value['bid_size'],'ask_size':value['ask_size'],'bid_at':value['quote_at'],'ask_at':value['quote_at']}
    result.update(changes);return result


def request(value, **changes):
    result = {'request_id':'first','contract_id':value['contract_id'],'side':'buy','quantity':2,'price_limit':'1.04','allow_partial':False}
    result.update(changes);return result


def step(current, value=None, *, at=AT, observations=None, requests=None, **changes):
    value = book() if value is None else value
    return engine.advance(current,[value],requests or [request(value,**changes)],observations or [observation(value)],decided_at=at)


class PublicPaperEngineTests(unittest.TestCase):
    def test_migrate_preserves_every_financial_with_no_funding(self):
        prior=old_state();before=deepcopy(prior)
        current=engine.migrate_flat(prior,activated_at='2026-11-03T14:59:00Z',previous_state_sha256='1'*64,source_cutoff_sha256='2'*64)
        self.assertEqual(prior,before);self.assertEqual(current['ancestor'],prior)
        for key in ('cash','contributions','fees','funding_count','trade_count','closed_trade_count','monthly_committed','month','started_at'):
            self.assertEqual(current[key],prior[key])
        self.assertEqual(current['activation']['funding_added'],'0')
        self.assertFalse(engine.summarize(current)['automatic_topups'])

    def test_same_money_actual_ask_fee_reserve_and_full_close_bid(self):
        current=state();opened=step(current)['state']
        self.assertEqual(current['cash'],'25000');self.assertEqual(opened['cash'],'24790.70')
        self.assertEqual(opened['monthly_committed'],'210.60');self.assertEqual(opened['fees'],'1.30')
        self.assertEqual(opened['position']['exit_fee_reserved'],'1.30')
        closed=step(opened,side='sell',price_limit='1')['state']
        self.assertEqual(closed['cash'],'24989.40');self.assertEqual(closed['fees'],'2.60')
        self.assertEqual(closed['monthly_committed'],'210.60');self.assertEqual(closed['contributions'],'25000')
        self.assertEqual(closed['realized_pnl'],'-10.60');self.assertIsNone(closed['position'])

    def test_entry_limits_whole_quantity_no_automatic_downsize(self):
        for quantity in (10,1000):
            value=step(state(),quantity=quantity)
            self.assertEqual(value['proposals'][0]['proposed_quantity'],0)
            self.assertIn('entry_cap_or_cash_exceeded',value['proposals'][0]['reason_codes'])
            self.assertEqual(value['state']['cash'],'25000')

    def test_underlying_price_missing_metadata_and_no_buyer_block(self):
        for changed in ({'open_interest':None},{'standard_deliverable_verified':False},{'bid_size':0},{'bid':None}):
            result=step(state(),book(**changed))
            self.assertEqual(result['proposals'][0]['status'],'no_fill');self.assertEqual(result['state']['cash'],'25000')

    def test_sell_cannot_create_short_position_or_exceed_inventory(self):
        for current,quantity in ((state(),1),(step(state())['state'],3)):
            result=step(current,side='sell',quantity=quantity,price_limit='1',allow_partial=True)
            self.assertIn('sell_exceeds_owned_inventory',result['proposals'][0]['reason_codes'])
            self.assertEqual(result['state']['cash'],current['cash'])

    def test_partial_sell_preserves_cost_basis_and_reserved_fees(self):
        opened=step(state())['state'];partial=step(opened,side='sell',quantity=1,price_limit='1',allow_partial=True)['state']
        self.assertEqual(partial['position']['quantity'],1);self.assertEqual(partial['position']['premium_remaining'],'104.00')
        self.assertEqual(partial['position']['entry_fee_remaining'],'0.65');self.assertEqual(partial['position']['exit_fee_reserved'],'0.65')
        closed=step(partial,side='sell',quantity=1,price_limit='1')['state']
        self.assertEqual(closed['realized_pnl'],'-10.60');self.assertEqual(closed['closed_trade_count'],1)

    def test_repeated_quote_after_close_does_not_refill_depth(self):
        b=book(bid_size=2,ask_size=2);opened=step(state(),b)['state'];closed=step(opened,b,side='sell',price_limit='1')['state']
        again=step(closed,b)
        self.assertEqual(again['proposals'][0]['status'],'no_fill')
        self.assertIn('durable_ask_depth_insufficient',again['proposals'][0]['reason_codes'])
        self.assertIn('durable_entry_bid_depth_insufficient',again['proposals'][0]['reason_codes'])

    def test_opposite_side_event_update_never_refills_unchanged_bid(self):
        b=book(bid_size=2,ask_size=2);opened=step(state(),b)['state'];closed=step(opened,b,side='sell',price_limit='1')['state']
        event='2026-11-03T15:00:00.060000+00:00';b['received_at']='2026-11-03T15:00:00.070000+00:00'
        result=step(closed,b,observations=[observation(b,ask_at=event)])
        self.assertIn('durable_entry_bid_depth_insufficient',result['proposals'][0]['reason_codes'])

    def test_opposite_bid_update_never_refills_unchanged_ask(self):
        b=book(bid_size=2,ask_size=2);opened=step(state(),b)['state'];closed=step(opened,b,side='sell',price_limit='1')['state']
        event='2026-11-03T15:00:00.060000+00:00';b['received_at']='2026-11-03T15:00:00.070000+00:00'
        result=step(closed,b,observations=[observation(b,bid_at=event)])
        self.assertIn('durable_ask_depth_insufficient',result['proposals'][0]['reason_codes'])

    def test_conflicting_same_side_event_rejects_complete_frame(self):
        b=book();opened=step(state(),b)['state'];changed=book(bid_size=101)
        with self.assertRaisesRegex(ValueError,'Same Public side event'):
            step(opened,changed,side='sell',price_limit='1')
        self.assertEqual(opened['position']['quantity'],2)

    def test_saved_receipt_time_cannot_replace_event_time(self):
        b=book();b['quote_at']='2026-11-03T14:59:00+00:00'
        result=step(state(),b)
        self.assertIn('quote_stale',result['proposals'][0]['reason_codes'])
        b['quote_at']='2026-11-03T15:00:00+00:00'
        with self.assertRaisesRegex(ValueError,'oldest actual'):
            step(state(),b,observations=[observation(b,bid_at='2026-11-03T14:59:00+00:00')])

    def test_exit_requires_fresh_buyer_but_not_fresh_ask(self):
        opened=step(state())['state'];b=book(quote_at='2026-11-03T14:59:30+00:00')
        obs=observation(b,bid_at='2026-11-03T15:00:00.010000+00:00')
        result=step(opened,b,observations=[obs],side='sell',price_limit='1')
        self.assertEqual(result['proposals'][0]['status'],'paper_fill')
        self.assertEqual(result['proposals'][0]['quote_clock_basis'],'actual_bid_event')
        obs=observation(b,ask_at='2026-11-03T15:00:00.010000+00:00')
        result=step(opened,b,observations=[obs],side='sell',price_limit='1')
        self.assertEqual(result['proposals'][0]['status'],'no_fill')
        self.assertIn('quote_stale',result['proposals'][0]['reason_codes'])

    def test_duplicate_requests_future_clocks_and_input_mutation_refused(self):
        b=book();initial=state();before=deepcopy(initial)
        with self.assertRaises(ValueError):step(initial,b,requests=[request(b),request(b)])
        with self.assertRaises(ValueError):step(initial,b,at='2026-11-03T14:58:00+00:00')
        with self.assertRaises(ValueError):step(initial,b,requests=[{**request(b),'decision_at':AT}])
        self.assertEqual(initial,before)

    def test_decimal_hostile_context_cannot_change_results(self):
        initial=state();expected=step(initial)
        with localcontext() as ctx:
            ctx.prec=2;ctx.traps[Inexact]=True
            self.assertEqual(step(initial),expected)
            self.assertEqual(engine.validate_state(expected['state']),expected['state'])
            self.assertEqual(engine.summarize(expected['state'])['cash'],'24790.70')

    def test_ancestry_money_and_counts_cannot_change_without_transition(self):
        initial=state()
        for key,value in (('cash','25001'),('contributions','50000'),('funding_count',2),('monthly_committed','0')):
            changed=deepcopy(initial);changed[key]=value
            if changed==initial:continue
            with self.assertRaises(ValueError):engine.validate_state(changed)

    def test_month_change_has_zero_added_funding_and_open_prior_month_stops(self):
        result=step(state())
        self.assertEqual(result['events'][0]['type'],'monthly_allowance_reset');self.assertEqual(result['events'][0]['funding_added'],'0')
        with self.assertRaisesRegex(ValueError,'Prior-month open'):
            step(result['state'],at='2026-12-01T15:00:00+00:00')


if __name__=='__main__':unittest.main()
