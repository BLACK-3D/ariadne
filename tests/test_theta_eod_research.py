"""Fictional byte fixtures for pure readiness checks; no provider or owner I/O."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import localcontext
import base64
import builtins
import hashlib
import io
import json
import socket
from pathlib import Path
import unittest
from unittest.mock import patch

from ariadne import research_window as scope
from ariadne import theta_eod_research as m

TZIF = (Path(__file__).resolve().parents[1]/'ariadne/resources/briefing/America_Chicago.tzif').read_bytes()
NOW = '2026-11-05T23:01:00+00:00'
OLD = scope.derive_window('2026-11-05T21:51:44+00:00')
DAYS = ('2026-11-02', '2026-11-03', '2026-11-04')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def price(integer=50, scale=8):
    exact = None if scale == 0 else format(m.Decimal((0, tuple(int(c) for c in str(integer)), scale-10)), 'f')
    return {'kind':'price', 'integer':integer, 'scale_type':scale, 'decimal':exact}


def stamp(day):
    value = datetime.fromisoformat(day+'T22:15:00+00:00')
    millis = (value-datetime(1970,1,1,tzinfo=timezone.utc))//timedelta(milliseconds=1)
    return {'kind':'timestamp','epoch_ms':millis,'nanos':4294967295,'zone':0,'precision':1,
        'epoch_ns':None,'submillisecond_semantics':m._STAMP_SUBMILLI}


def capture(*, stock=False, right='P', expiry='261120', days=DAYS):
    frames, rows = [], []
    for index, day in enumerate(days):
        values = {key:price() for key in m._PRICES}
        values.update({key:{'kind':'number','value':100} for key in m._COUNTS})
        values.update(created=stamp(day), last_trade={'kind':'null','value':None})
        if stock:
            values['close'] = price(1500)
        else:
            values.update(symbol={'kind':'text','value':'F'},
                expiration={'kind':'text','value':'20'+expiry[:2]+'-'+expiry[2:4]+'-'+expiry[4:]},
                strike=price(15000,7), right={'kind':'text','value':'put' if right=='P' else 'call'})
        rows.append({'report_day':day,'values':values,'quote_event_at':None,'open_interest':None,
            'standard_deliverable_verified':False,'live_execution_eligible':False})
        raw = ('fictional-compressed-frame-'+str(index)).encode()
        frames.append({'received_at':'2026-11-05T23:00:01+00:00',
            'payload_base64':base64.b64encode(raw).decode(), 'sha256':digest(raw), 'bytes':len(raw),
            'rows':1, 'compression':'ZSTD','declared_original_size':12,'decoded_bytes':12})
    selection = {'kind':'stock' if stock else 'option','start_date':DAYS[0],'end_date':DAYS[-1]}
    selection['symbol' if stock else 'contract_id'] = 'F' if stock else 'F'+expiry+right+'00015000'
    return {'schema':1,'kind':'theta_direct_eod_capture','status':'completed','source_id':m.SOURCE_ID,
        'selection':selection,'allowed_symbols':['F'],'selection_window':OLD.as_dict(),
        'started_at':'2026-11-05T23:00:00+00:00','completed_at':'2026-11-05T23:00:02+00:00',
        'preflight_sha256':'1'*64,'operations':{'authentication':1,'eod_rpc':1},
        'responses':frames,'rows':rows,'transport':{'kind':'synthetic_test','provider_authenticated':False,
            'market_data_cryptographically_signed':False,'live_execution_eligible':False,
            'rpc_payload_bytes_exclude_transport_overhead':True},
        'open_interest_available':False,'quote_event_available':False,'paper_fills':0,
        'source_report_clock_basis':m._REPORT_BASIS,'submillisecond_semantics':m._SUBMILLI,
        'missing_report_sessions':[day for day in DAYS if day not in days],
        'payload_bytes':sum(f['bytes'] for f in frames),'decoded_bytes':sum(f['decoded_bytes'] for f in frames),
        'empty_response':not rows}


def bundle(value):
    raw = canonical(value)
    receipt = {'schema':1,'status':'completed','kind':'theta_direct_eod_qualification','ordinal':5,
        'completed_at':'2026-11-05T23:00:03+00:00','authorization_sha256':'2'*64,
        'protocol_amendment_sha256':'3'*64,'operations_reserved':{'authentication':1,'eod_rpc':1},
        'market_evidence':{'path':'staging/fictional-eod/slot-5/research.json','bytes':len(raw),'sha256':digest(raw)},
        'reported_rows':len(value['rows']),'terminal_hold_preserved':True,'automatic_continuation':False,
        'live_execution_eligible':False,'application_launched':False,'paper_fills':0,'funding_added':'0',
        'no_new_budget_namespace_created':True,'original_three_attempt_allowance_renewed':False,
        'prior_additional_allowance_renewed':False}
    return raw, receipt


def assess(value=None, receipt_change=None, **kwargs):
    raw, receipt = bundle(capture() if value is None else value)
    if receipt_change:
        receipt_change(receipt)
    completed = canonical(receipt)
    return m.assess_capture(raw, completed, expected_capture_sha256=digest(raw),
        expected_completed_sha256=digest(completed), window=scope.derive_window(NOW),calendar_tzif_raw=TZIF, **kwargs)


class ThetaEODResearchTests(unittest.TestCase):
    def reject(self, change):
        value = capture(); change(value)
        with self.assertRaises(m.ThetaResearchError):assess(value)

    def test_exact_valid_synthetic_evidence_is_not_execution_or_raw_decode_proof(self):
        result = assess()
        self.assertEqual(result['option_rows'],3)
        self.assertEqual(result['short_dte_same_month_rows'],3)
        self.assertFalse(result['wire_projection_redecoded'])
        self.assertFalse(result['provider_authenticated_claim'])
        self.assertFalse(result['source_authentication_independently_verified'])
        self.assertFalse(result['historical_strategy_comparison_ready'])
        self.assertFalse(result['paper_trading_eligible'])
        self.assertFalse(result['clocks_projected_or_rewritten'])
        self.assertIn('paired_underlying_not_supplied',result['readiness_reasons'])
        self.assertIn('recovery_call_side_missing',result['readiness_reasons'])
        for key in ('file_operations','network_calls','native_provider_imports','model_calls','owner_operations','paper_fills'):
            self.assertEqual(result[key],0)

    def test_current_long_expiry_policy_is_blocked_without_rule_relaxation(self):
        result=assess(capture(expiry='270115'))
        self.assertEqual((result['minimum_dte'],result['maximum_dte']),(72,74))
        self.assertEqual(result['short_dte_same_month_rows'],0)
        self.assertIn('short_dte_same_month_policy_incompatible',result['readiness_reasons'])

    def test_call_only_still_has_no_put_comparison(self):
        result=assess(capture(right='C'))
        self.assertTrue(result['call_side_present']);self.assertFalse(result['put_side_present'])
        self.assertIn('stress_put_side_missing',result['readiness_reasons'])

    def test_same_month_is_required_even_when_dte_fits(self):
        value=capture(expiry='261106',days=('2026-10-19','2026-10-20','2026-10-21'))
        value['selection'].update(start_date='2026-10-19',end_date='2026-10-21')
        value['missing_report_sessions']=[]
        result=assess(value)
        self.assertEqual((result['minimum_dte'],result['maximum_dte']),(16,18))
        self.assertEqual(result['short_dte_same_month_rows'],0)
        self.assertIn('short_dte_same_month_policy_incompatible',result['readiness_reasons'])

    def test_sender_watchlist_can_have_four_unique_roots_but_only_one_selection(self):
        value=capture();value['allowed_symbols']=['F','KO','SPY','QQQ']
        self.assertEqual(assess(value)['symbol'],'F')
        self.reject(lambda v:v.__setitem__('allowed_symbols',['F','F']))
        self.reject(lambda v:v.__setitem__('allowed_symbols',['KO']))


    def test_capture_and_completion_hashes_are_both_mandatory(self):
        raw, receipt=bundle(capture());completed=canonical(receipt)
        for kwargs in ({'expected_capture_sha256':'0'*64,'expected_completed_sha256':digest(completed)},
                       {'expected_capture_sha256':digest(raw),'expected_completed_sha256':'0'*64}):
            with self.assertRaises(m.ThetaResearchError):
                m.assess_capture(raw,completed,window=scope.derive_window(NOW),calendar_tzif_raw=TZIF,**kwargs)

    def test_completed_leaf_binds_exact_artifact_hash_and_bytes(self):
        for field,value in (('bytes',1),('sha256','0'*64),('path','../research.json'),('path','staging/../research.json')):
            with self.subTest(field=field,value=value),self.assertRaises(m.ThetaResearchError):
                assess(receipt_change=lambda r:r['market_evidence'].__setitem__(field,value))

    def test_failed_or_partial_capture_and_receipt_are_rejected(self):
        self.reject(lambda v:v.__setitem__('status','failed'))
        self.reject(lambda v:v.__setitem__('failure_code','capture_internal'))
        with self.assertRaises(m.ThetaResearchError):assess(receipt_change=lambda r:r.__setitem__('status','failed'))

    def test_duplicate_keys_nonfinite_float_and_oversized_raw_are_rejected(self):
        for raw in (b'{"schema":1,"schema":1}',b'{"x":NaN}',b'{"x":1.5}',b'{"x":'+b'1'*21+b'}',b'x'*(m.MAX_CAPTURE_BYTES+1)):
            with self.subTest(length=len(raw)),self.assertRaises(m.ThetaResearchError):
                m.assess_capture(raw,b'{}',expected_capture_sha256=digest(raw),expected_completed_sha256=digest(b'{}'),window=scope.derive_window(NOW),calendar_tzif_raw=TZIF)

    def test_extra_unknown_projection_and_type_fields_are_rejected(self):
        for change in (lambda v:v.__setitem__('secret','fictional'),
            lambda v:v['rows'][0].__setitem__('extra',1),
            lambda v:v['rows'][0]['values']['bid'].__setitem__('extra',1)):
            self.reject(change)

    def test_price_scale_decimal_and_contract_identity_are_exact(self):
        for change in (lambda v:v['rows'][0]['values']['bid'].__setitem__('decimal','0.500'),
            lambda v:v['rows'][0]['values']['bid'].__setitem__('scale_type',True),
            lambda v:v['rows'][0]['values']['strike'].__setitem__('decimal','16.000'),
            lambda v:v['rows'][0]['values'].__setitem__('right',{'kind':'text','value':'call'}),
            lambda v:v['rows'][0]['values'].__setitem__('symbol',{'kind':'text','value':'KO'})):
            self.reject(change)

    def test_decimal_context_cannot_change_price_or_contract_interpretation(self):
        with localcontext() as context:
            context.prec=1
            result=assess()
        self.assertEqual(result['contract_id'],'F261120P00015000')

    def test_unknown_scale_remains_unknown_without_coercion(self):
        value=capture();value['rows'][0]['values']['bid']=price(0,0)
        self.assertEqual(assess(value)['option_rows'],3)
        self.reject(lambda v:v['rows'][0]['values']['bid'].__setitem__('decimal',None))

    def test_sender_unset_shapes_and_case_normalized_right_are_admitted(self):
        value=capture();value['rows'][0]['values']['bid']={'kind':'unset','value':None}
        value['rows'][0]['values']['volume']={'kind':'unset','value':None}
        value['rows'][0]['values']['last_trade']={'kind':'unset','value':None}
        value['rows'][0]['values']['right']={'kind':'text','value':'PUT'}
        self.assertEqual(assess(value)['option_rows'],3)
        self.reject(lambda v:v['rows'][0]['values'].__setitem__('bid',{'kind':'unset'}))

    def test_raw_nanos_are_preserved_without_epoch_ns_reconstruction(self):
        value=capture();original=deepcopy(value)
        assess(value);self.assertEqual(value,original)
        self.reject(lambda v:v['rows'][0]['values']['created'].__setitem__('epoch_ns',123))
        self.reject(lambda v:v['rows'][0]['values']['created'].__setitem__('nanos',1<<32))
        self.reject(lambda v:v['rows'][0]['values']['created'].__setitem__('zone',True))

    def test_report_day_is_created_new_york_day_not_receipt_or_trade_day(self):
        self.reject(lambda v:v['rows'][0].__setitem__('report_day','2026-11-03'))
        self.reject(lambda v:v['rows'][0]['values'].__setitem__('last_trade',stamp('2026-11-05')))
        self.reject(lambda v:v['rows'][0]['values'].__setitem__('created',stamp('2026-11-06')))

    def test_report_projection_may_not_claim_event_or_deliverable_or_oi(self):
        for field,value in (('quote_event_at',NOW),('open_interest',100),('standard_deliverable_verified',True),('live_execution_eligible',True)):
            self.reject(lambda v:v['rows'][0].__setitem__(field,value))
        self.reject(lambda v:v.__setitem__('quote_event_available',True))

    def test_frame_hash_length_and_count_are_checked(self):
        for field,value in (('sha256','0'*64),('bytes',1),('rows',True),('payload_base64','%%%'),('decoded_bytes',m.MAX_FRAME_DECODED+1)):
            self.reject(lambda v:v['responses'][0].__setitem__(field,value))
        self.reject(lambda v:v.__setitem__('payload_bytes',0))
        self.reject(lambda v:v.__setitem__('decoded_bytes',0))

    def test_receipt_chronology_and_capture_deadline_are_checked(self):
        self.reject(lambda v:v['responses'][0].__setitem__('received_at','2026-11-05T23:00:03+00:00'))
        self.reject(lambda v:v.__setitem__('started_at','2026-11-05T22:50:00+00:00'))
        with self.assertRaises(m.ThetaResearchError):assess(receipt_change=lambda r:r.__setitem__('completed_at','2026-11-05T23:02:00+00:00'))

    def test_gaps_and_empty_response_are_explicit_never_silently_filled(self):
        value=capture(days=DAYS[1:]);result=assess(value)
        self.assertEqual(result['option_rows'],2);self.assertEqual(result['missing_requested_sessions'],1)
        self.reject(lambda v:v.__setitem__('missing_report_sessions',['2026-11-02']))
        value=capture(days=());result=assess(value)
        self.assertEqual(result['option_rows'],0);self.assertEqual(result['missing_requested_sessions'],3)
        self.assertIn('empty_option_history',result['readiness_reasons'])

    def test_capture_window_booleans_must_not_equal_integer_schema(self):
        self.reject(lambda v:v['selection_window'].__setitem__('schema',True))
        self.reject(lambda v:v['selection_window'].__setitem__('inclusive',1))

    def test_duplicate_or_reverse_report_days_are_rejected(self):
        self.reject(lambda v:v['rows'].__setitem__(1,deepcopy(v['rows'][0])))
        self.reject(lambda v:v['rows'].reverse())

    def test_supplied_stock_requires_own_completion_and_hashes(self):
        with self.assertRaises(m.ThetaResearchError):assess(paired_stock_raw=b'{}')
        raw,receipt=bundle(capture(stock=True));completed=canonical(receipt)
        result=assess(paired_stock_raw=raw,paired_stock_completed_raw=completed,
            expected_stock_sha256=digest(raw),expected_stock_completed_sha256=digest(completed))
        self.assertEqual(result['matching_underlying_rows'],3)
        self.assertEqual(result['below_100_underlying_rows'],3)
        self.assertNotIn('paired_underlying_not_supplied',result['readiness_reasons'])
        self.assertFalse(result['historical_strategy_comparison_ready'])

    def test_partial_or_wrong_symbol_stock_never_establishes_full_pairing(self):
        value=capture(stock=True,days=DAYS[1:]);raw,receipt=bundle(value);completed=canonical(receipt)
        result=assess(paired_stock_raw=raw,paired_stock_completed_raw=completed,expected_stock_sha256=digest(raw),expected_stock_completed_sha256=digest(completed))
        self.assertEqual(result['matching_underlying_rows'],2)
        self.assertIn('paired_underlying_coverage_incomplete',result['readiness_reasons'])
        value=capture(stock=True);value['selection']['symbol']='KO';value['allowed_symbols']=['KO'];raw,receipt=bundle(value);completed=canonical(receipt)
        with self.assertRaises(m.ThetaResearchError):assess(paired_stock_raw=raw,paired_stock_completed_raw=completed,expected_stock_sha256=digest(raw),expected_stock_completed_sha256=digest(completed))

    def test_stock_below_100_gate_is_strict(self):
        value=capture(stock=True);value['rows'][0]['values']['close']=price(10000)
        raw,receipt=bundle(value);completed=canonical(receipt)
        result=assess(paired_stock_raw=raw,paired_stock_completed_raw=completed,expected_stock_sha256=digest(raw),expected_stock_completed_sha256=digest(completed))
        self.assertEqual(result['below_100_underlying_rows'],2)
        self.assertIn('below_100_underlying_gate_not_established',result['readiness_reasons'])

    def test_operations_never_imply_orders_or_new_budget(self):
        self.reject(lambda v:v['operations'].__setitem__('authentication',True))
        self.reject(lambda v:v['operations'].__setitem__('eod_rpc',2))
        with self.assertRaises(m.ThetaResearchError):assess(receipt_change=lambda r:r.__setitem__('prior_additional_allowance_renewed',True))
        with self.assertRaises(m.ThetaResearchError):assess(receipt_change=lambda r:r.__setitem__('paper_fills',1))

    def test_pure_success_opens_no_files_sockets_or_vendor_modules(self):
        value=capture()
        window=scope.derive_window(NOW)
        with (patch.object(builtins,'open',side_effect=AssertionError('No files')),
              patch.object(io,'open',side_effect=AssertionError('No files')),
              patch.object(Path,'open',side_effect=AssertionError('No files')),
              patch.object(socket,'socket',side_effect=AssertionError('No sockets'))):
            raw,receipt=bundle(value);completed=canonical(receipt)
            result=m.assess_capture(raw,completed,expected_capture_sha256=digest(raw),
                expected_completed_sha256=digest(completed),window=window,calendar_tzif_raw=TZIF)
        self.assertEqual(result['network_calls'],0)

    def test_in_memory_calendar_matches_frozen_window_across_market_boundaries(self):
        zone=m._calendar(TZIF)
        clocks=('2006-04-03T19:00:00+00:00','2007-03-12T20:15:00+00:00',
            '2024-02-29T21:15:00+00:00','2025-01-09T22:00:00+00:00',
            '2026-03-08T07:30:00+00:00','2026-07-02T20:15:00+00:00',
            '2026-11-01T06:30:00+00:00','2026-11-27T18:14:59+00:00',
            '2026-11-27T18:15:00+00:00','2026-12-24T18:15:00+00:00')
        for clock in clocks:
            with self.subTest(clock=clock):
                self.assertEqual(m._window_metadata(clock,zone),scope.derive_window(clock).as_dict())

    def test_calendar_bytes_are_fixed_and_required_without_host_discovery(self):
        for raw in (None,b'not a timezone',TZIF+b'changed'):
            with self.assertRaises(m.ThetaResearchError):m._calendar(raw)

    def test_invalid_window_object_cannot_invoke_user_method(self):
        class Hostile:
            def as_dict(self):raise AssertionError('No user method')
        raw,receipt=bundle(capture());completed=canonical(receipt)
        with self.assertRaises(m.ThetaResearchError):m.assess_capture(raw,completed,
            expected_capture_sha256=digest(raw),expected_completed_sha256=digest(completed),window=Hostile(),calendar_tzif_raw=TZIF)


if __name__=='__main__':unittest.main()
