"""Fictional event-book evidence only; no provider, broker, model or account calls."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, Inexact, localcontext
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_liquidity as gate
from ariadne.qqq_data import session_dates, session_close_utc

AT = '2026-11-03T15:00:00.100000+00:00'

def book(**changes):
    sessions = session_dates(date(2026, 9, 4), date(2026, 11, 2))[-20:]
    value = {'schema': 1, 'source_id': 'fictional-test-source', 'snapshot_id': 'fictional-event-001',
        'quote_kind': 'top_of_book', 'underlying_symbol': 'KO', 'underlying_type': 'stock',
        'contract_id': 'KO261120C00080000', 'expiry': '2026-11-20', 'type': 'call',
        'strike': '80', 'multiplier': 100, 'standard_deliverable_verified': True,
        'deliverable_verification_source': 'fictional-contract-fixture-only',
        'bid': '1.00', 'ask': '1.04', 'bid_size': 100, 'ask_size': 100,
        'quote_at': '2026-11-03T15:00:00+00:00', 'received_at': '2026-11-03T15:00:00.050000+00:00',
        'underlying_quote': {'price': '79.90', 'quote_at': '2026-11-03T15:00:00+00:00',
            'received_at': '2026-11-03T15:00:00.050000+00:00'},
        'underlying_activity': {'sessions': [{'session': d.isoformat(), 'dollar_volume': '50000000',
            'published_at': (session_close_utc(d) + timedelta(minutes=15)).isoformat()} for d in sessions]},
        'contract_activity': {'volume': 1000, 'as_of': '2026-11-03T15:00:00+00:00',
            'published_at': '2026-11-03T15:00:00.050000+00:00'},
        'open_interest': {'value': 2000, 'session': '2026-11-02', 'published_at': '2026-11-03T14:00:00+00:00'}}
    value.update(changes)
    return value

def request(value, **changes):
    result = {'request_id': 'request-one', 'source_id': value['source_id'], 'snapshot_id': value['snapshot_id'],
        'contract_id': value['contract_id'], 'decision_at': AT, 'side': 'buy', 'quantity': 2,
        'price_limit': '1.04', 'allow_partial': False}
    result.update(changes)
    return result

def assess(value=None, **changes):
    value = book() if value is None else value
    return gate.evaluate_batch([value], [request(value, **changes)])

class StrictLiquidityTests(unittest.TestCase):
    def test_buy_proposes_actual_ask_and_never_claims_execution_or_authentication(self):
        value = assess();p = value['proposals'][0]
        self.assertEqual((p['status'], p['quoted_price'], p['proposed_quantity']), ('paper_proposal', '1.04', 2))
        self.assertEqual(value['depth_consumption'][0]['ask_consumed'], 2)
        self.assertEqual(value['depth_consumption'][0]['bid_reserved_for_entries'], 2)
        for field in ('execution_confirmed', 'provider_authenticated', 'portfolio_controls_checked'):
            self.assertIs(value[field], False);self.assertIs(p[field], False)
        self.assertIs(value['top_of_book_only'], True)

    def test_sell_uses_actual_bid_without_entry_metrics_underlying_or_ask(self):
        value = book(ask=None, ask_size=None, underlying_quote=None, underlying_activity=None,
                     contract_activity=None, open_interest=None)
        p = assess(value, side='sell', price_limit='.99' if False else '0.99')['proposals'][0]
        self.assertEqual((p['status'],p['quoted_price'],p['proposed_quantity']),('paper_proposal','1.00',2))

    def test_sell_bypasses_spread_volume_and_underlying_price_floors(self):
        value = book(ask='10', ask_size=0)
        value['underlying_quote']['price']='150'
        value['contract_activity']['volume']=0;value['open_interest']['value']=0
        value['underlying_activity']['sessions'][0]['dollar_volume']='0'
        self.assertEqual(assess(value,side='sell',price_limit='1')['proposals'][0]['status'],'paper_proposal')
        self.assertEqual(assess(value)['proposals'][0]['status'],'no_fill')

    def test_fixed_price_protection_never_adjusts_quote(self):
        for side,limit,expected in [('buy','1.03','1.04'),('sell','1.01','1.00')]:
            p=assess(side=side,price_limit=limit)['proposals'][0]
            self.assertEqual(p['status'],'no_fill');self.assertEqual(p['quoted_price'],expected)
            self.assertIn('price_protection_not_met',p['reason_codes'])

    def test_buy_requires_requested_full_ask_and_bid_depth_without_auto_shrinking(self):
        for change,code in [({'ask_size':1},'ask_depth_insufficient'),({'bid_size':1},'entry_bid_depth_insufficient')]:
            p=assess(book(**change))['proposals'][0]
            self.assertEqual(p['proposed_quantity'],0);self.assertIn(code,p['reason_codes'])

    def test_unknown_and_zero_depth_never_become_liquidity(self):
        for depth in (None,0):
            p=assess(book(bid_size=depth),side='sell',price_limit='1')['proposals'][0]
            self.assertEqual(p['status'],'no_fill');self.assertIn('bid_depth_unavailable',p['reason_codes'])

    def test_partial_sells_require_explicit_permission_and_consume_actual_bid_once(self):
        value=book(bid_size=3)
        p=assess(value,side='sell',quantity=5,price_limit='1')['proposals'][0]
        self.assertEqual(p['proposed_quantity'],0)
        rows=[request(value,request_id=str(n),side='sell',quantity=5,price_limit='1',allow_partial=True) for n in range(2)]
        result=gate.evaluate_batch([value],rows)
        self.assertEqual([p['proposed_quantity'] for p in result['proposals']],[3,0])
        self.assertEqual(result['depth_consumption'][0]['bid_consumed'],3)

    def test_batch_aggregates_ask_depth_and_reserves_entry_bid_without_blocking_sell(self):
        value=book(bid_size=4,ask_size=10)
        rows=[request(value,request_id='one',quantity=3),request(value,request_id='two',quantity=3),
              request(value,request_id='three',side='sell',quantity=4,price_limit='1')]
        result=gate.evaluate_batch([value],rows)
        self.assertEqual([p['proposed_quantity'] for p in result['proposals']],[3,0,4])
        self.assertEqual(result['depth_consumption'][0]['bid_reserved_for_entries'],3)
        self.assertEqual(result['depth_consumption'][0]['bid_consumed'],4)

    def test_buy_after_sell_cannot_reuse_consumed_bid(self):
        value=book(bid_size=3)
        rows=[request(value,request_id='sell',side='sell',quantity=2,price_limit='1'),request(value,request_id='buy',quantity=2)]
        self.assertEqual([p['proposed_quantity'] for p in gate.evaluate_batch([value],rows)['proposals']],[2,0])

    def test_provider_and_snapshot_aliases_do_not_refresh_same_event_depth(self):
        value=book(bid_size=3);alias=deepcopy(value);alias.update(source_id='other-source',snapshot_id='other-snapshot')
        rows=[request(value,request_id='a',side='sell',price_limit='1'),request(alias,request_id='b',side='sell',price_limit='1')]
        result=gate.evaluate_batch([value,alias],rows)
        self.assertEqual([p['proposed_quantity'] for p in result['proposals']],[2,0])
        self.assertEqual(len(result['depth_consumption']),1)

    def test_contradictory_duplicate_or_cross_provider_event_refuses_whole_batch(self):
        for change in ({'ask_size':99},{'snapshot_id':'renamed','bid':'0.99'},{'source_id':'other','snapshot_id':'new','bid_size':99}):
            value=book();other=deepcopy(value);other.update(change)
            with self.assertRaises(gate.LiquidityError):gate.evaluate_batch([value,other],[request(value)])

    def test_new_actual_event_clock_has_distinct_displayed_depth(self):
        value=book(bid_size=2);later=deepcopy(value);later.update(snapshot_id='new-event',quote_at='2026-11-03T15:00:01+00:00',received_at='2026-11-03T15:00:01.050000+00:00')
        rows=[request(value,request_id='one',side='sell',price_limit='1'),request(later,request_id='two',decision_at='2026-11-03T15:00:01.100000+00:00',side='sell',price_limit='1')]
        result=gate.evaluate_batch([value,later],rows)
        self.assertEqual([p['proposed_quantity'] for p in result['proposals']],[2,2])
        self.assertEqual(len(result['depth_consumption']),2)

    def test_exact_five_second_age_allowed_but_one_microsecond_more_refused(self):
        self.assertEqual(assess(decision_at='2026-11-03T15:00:05+00:00')['proposals'][0]['status'],'paper_proposal')
        p=assess(decision_at='2026-11-03T15:00:05.000001+00:00')['proposals'][0]
        self.assertEqual(p['status'],'no_fill');self.assertIn('quote_stale',p['reason_codes'])

    def test_missing_actual_event_clock_is_no_fill_and_date_only_is_invalid(self):
        p=assess(book(quote_at=None))['proposals'][0]
        self.assertIn('actual_quote_clock_missing',p['reason_codes']);self.assertEqual(p['proposed_quantity'],0)
        with self.assertRaises(gate.LiquidityError):assess(book(quote_at='2026-11-03'))

    def test_future_receive_or_event_clock_cannot_fill(self):
        for changes in ({'received_at':'2026-11-03T15:00:00.100001+00:00'},
                        {'quote_at':'2026-11-03T15:00:01+00:00','received_at':'2026-11-03T15:00:01+00:00'}):
            p=assess(book(**changes))['proposals'][0]
            self.assertIn('quote_not_available_at_decision',p['reason_codes']);self.assertEqual(p['proposed_quantity'],0)

    def test_receive_before_event_is_no_fill(self):
        p=assess(book(received_at='2026-11-03T14:59:59+00:00'))['proposals'][0]
        self.assertIn('quote_clock_inconsistent',p['reason_codes'])

    def test_volume_must_be_same_session_known_before_decision(self):
        for key,value in [('as_of','2026-11-02T20:00:00+00:00'),('published_at','2026-11-03T15:00:00.100001+00:00')]:
            row=book();row['contract_activity'][key]=value
            p=assess(row)['proposals'][0]
            self.assertIn('contract_volume_not_available',p['reason_codes'])
        row=book();row['contract_activity']['volume']=499
        self.assertIn('contract_volume_below_floor',assess(row)['proposals'][0]['reason_codes'])

    def test_oi_requires_latest_previous_session_and_actual_publication(self):
        for key,value in [('session','2026-10-30'),('published_at','2026-11-03T15:01:00+00:00'),('published_at','2026-11-02T20:00:00+00:00')]:
            row=book();row['open_interest'][key]=value
            self.assertIn('open_interest_not_available',assess(row)['proposals'][0]['reason_codes'])
        row=book();row['open_interest']['value']=999
        self.assertIn('open_interest_below_floor',assess(row)['proposals'][0]['reason_codes'])

    def test_adv20_requires_all_consecutive_prior_sessions_and_no_future_publication(self):
        for mode in ('short','duplicate','future','low'):
            row=book();bars=row['underlying_activity']['sessions']
            if mode=='short':bars.pop()
            elif mode=='duplicate':bars[0]=deepcopy(bars[1])
            elif mode=='future':bars[-1]['published_at']='2026-11-03T15:01:00+00:00'
            else:
                for bar in bars:bar['dollar_volume']='24999999'
            self.assertEqual(assess(row)['proposals'][0]['status'],'no_fill')

    def test_threshold_boundaries_are_deterministic_and_not_result_tuned(self):
        row=book(bid='1.95',ask='2.05');row['contract_activity']['volume']=500;row['open_interest']['value']=1000
        for bar in row['underlying_activity']['sessions']:bar['dollar_volume']='25000000'
        self.assertEqual(assess(row,price_limit='2.05')['proposals'][0]['status'],'paper_proposal')
        row['ask']='2.05000001'
        self.assertIn('entry_spread_too_wide',assess(row,price_limit='3')['proposals'][0]['reason_codes'])

    def test_closed_dates_preopen_close_and_early_close_refuse(self):
        for at in ('2026-11-03T14:29:59+00:00','2026-11-03T21:00:00+00:00',
                   '2026-11-07T15:00:00+00:00','2026-11-26T15:00:00+00:00','2026-11-27T18:00:00+00:00'):
            p=assess(decision_at=at)['proposals'][0]
            self.assertEqual(p['status'],'no_fill');self.assertIn('outside_regular_session',p['reason_codes'])

    def test_unverified_nonstandard_expired_or_unsupported_underlying_refuses(self):
        for change in ({'standard_deliverable_verified':False},{'multiplier':10},{'deliverable_verification_source':None},{'underlying_type':'index'}):
            self.assertEqual(assess(book(**change))['proposals'][0]['status'],'no_fill')
        p=assess(decision_at='2026-11-23T15:00:00+00:00')['proposals'][0]
        self.assertIn('contract_expired',p['reason_codes'])

    def test_exact_contract_identity_drift_and_unknown_fields_are_invalid(self):
        for change in ({'contract_id':'F261120C00080000'},{'type':'put'},{'strike':'81'},{'expiry':'2026-11-19'}, {'authenticated':True}):
            with self.assertRaises(gate.LiquidityError):assess(book(**change))

    def test_numeric_booleans_floats_nan_exponents_and_unbounded_values_invalid(self):
        for change in ({'bid':float('nan')},{'ask':'NaN'},{'bid_size':True},{'ask':'1e0'}, {'ask':'100000000000001'},{'multiplier':True}):
            with self.assertRaises(gate.LiquidityError):assess(book(**change))
        for change in ({'quantity':True},{'quantity':0},{'quantity':10001},{'price_limit':1.04},{'price_limit':'0'}, {'allow_partial':True}):
            with self.assertRaises(gate.LiquidityError):assess(**change)

    def test_request_refs_duplicates_and_backward_decisions_refuse_atomically(self):
        value=book();first=request(value)
        for second in ({**first},{**first,'request_id':'second','snapshot_id':'absent'},
                       {**first,'request_id':'second','decision_at':'2026-11-03T15:00:00+00:00'}):
            with self.assertRaises(gate.LiquidityError):gate.evaluate_batch([value],[first,second])

    def test_hostile_decimal_context_and_input_objects_are_not_mutated(self):
        value=book();original=deepcopy(value)
        expected=assess(value)
        with localcontext() as context:
            context.prec=2;context.traps[Inexact]=True
            self.assertEqual(assess(value),expected)
        self.assertEqual(value,original)

    def test_unmet_price_protection_consumes_no_depth(self):
        value=book();rows=[request(value,request_id='refuse',price_limit='1'),request(value,request_id='accept')]
        result=gate.evaluate_batch([value],rows)
        self.assertEqual([p['proposed_quantity'] for p in result['proposals']],[0,2])
        self.assertEqual(result['depth_consumption'][0]['ask_consumed'],2)

    def test_convenience_wrapper_and_batch_bounds(self):
        p=gate.assess_book(book(),at=AT,side='buy',quantity=2,price_limit='1.04')
        self.assertEqual(p['status'],'paper_proposal')
        for books,requests in (([],[]),([book()]*257,[request(book())]),([book()],[request(book())]*257)):
            with self.assertRaises(gate.LiquidityError):gate.evaluate_batch(books,requests)

if __name__=='__main__':unittest.main()
