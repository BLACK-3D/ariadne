"""Fictional EOD observations only; no Terminal, credentials or account state."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import localcontext, Inexact, Rounded
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from ariadne import theta_history as h
from ariadne.research_window import derive_window
from test_alpaca_market_data import FakeConnection, FakeResponse

CONTRACT='KO261120P00080000'
NOW=datetime(2026,11,5,15,0,tzinfo=timezone.utc)
WINDOW=derive_window(NOW)
STAMP='2026-11-05T15:00:01+00:00'


def manifest():
    return {'schema':1,'start_date':'2026-11-03','end_date':'2026-11-04',
        'requests':[{'kind':'stock','symbol':'KO'},{'kind':'option','contract_id':CONTRACT}]}


def row(day='2026-11-04',option=False):
    value={'created':day+'T17:15:00.000','last_trade':day+'T15:59:59.000',
        'open':1.01,'high':1.12,'low':1,'close':1.04,'volume':700,'count':80,
        'bid':1.03,'ask':1.04,'bid_size':8,'ask_size':9,'bid_exchange':1,
        'ask_exchange':2,'bid_condition':0,'ask_condition':0}
    if option:value.update(symbol='KO',expiration='2026-11-20',strike=80,right='put')
    return value


def record(request,values):
    raw=json.dumps(values,separators=(',',':')).encode()
    return {'request':dict(request),'payload':raw.decode(),'received_at':STAMP,
        'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}


def capture():
    plan=manifest()
    return {'schema':1,'source_id':h.SOURCE_ID,'status':'completed','selection':plan,
        'selection_window':WINDOW.as_dict(),'started_at':STAMP,'completed_at':STAMP,
        'responses':[record(plan['requests'][0],[row('2026-11-03'),row()]),
                     record(plan['requests'][1],[row(option=True)])],
        'transport':dict(h._TRANSPORT)}


def replace_rows(value,index,rows):
    value['responses'][index]=record(value['selection']['requests'][index],rows)
    return value


class HistoryManifestTests(unittest.TestCase):
    def test_actual_scope_and_explicit_request_order_preserved(self):
        value=manifest();self.assertEqual(h.validate_manifest(value,WINDOW),value)
        self.assertEqual(h.validate_manifest(value,WINDOW)['requests'][0]['kind'],'stock')

    def test_unknown_fields_booleans_and_wildcards_are_refused(self):
        cases=[]
        v=manifest();v['schema']=True;cases.append(v)
        v=manifest();v['url']='https://example.invalid';cases.append(v)
        v=manifest();v['requests'][0]['symbol']='*';cases.append(v)
        v=manifest();v['requests'][1]['contract_id']='KO*';cases.append(v)
        for value in cases:
            with self.subTest(value=value),self.assertRaises(ValueError):h.validate_manifest(value,WINDOW)

    def test_whole_out_of_window_future_and_reversed_ranges_rejected(self):
        for start,end in (('2025-11-04','2026-11-04'),('2026-11-04','2026-11-05'),('2026-11-04','2026-11-03')):
            value=manifest();value.update(start_date=start,end_date=end)
            with self.assertRaises(ValueError):h.validate_manifest(value,WINDOW)

    def test_duplicate_requests_and_missing_stock_are_refused(self):
        for requests in ([{'kind':'option','contract_id':CONTRACT}],manifest()['requests']*2):
            value=manifest();value['requests']=requests
            with self.assertRaises(ValueError):h.validate_manifest(value,WINDOW)

    def test_root_count_and_request_limits_are_enforced(self):
        value=manifest();value['requests']=[{'kind':'stock','symbol':s} for s in ('F','KO','MS','SOFI','AAL')]
        with self.assertRaises(ValueError):h.validate_manifest(value,WINDOW)
        value['requests']=[{'kind':'stock','symbol':'KO'}]*65
        with self.assertRaises(ValueError):h.validate_manifest(value,WINDOW)


class HistoryImportTests(unittest.TestCase):
    def test_eod_prices_sizes_and_report_trade_clocks_are_distinct(self):
        value=capture();before=deepcopy(value);result=h.normalize_capture(value)
        option=result['rows'][-1]
        self.assertEqual((option['bid'],option['ask'],option['bid_size'],option['ask_size']),('1.03','1.04',8,9))
        self.assertEqual(option['created_raw'],'2026-11-04T17:15:00.000')
        self.assertEqual(option['last_trade_raw'],'2026-11-04T15:59:59.000')
        self.assertIsNone(option['created_at']);self.assertIsNone(option['quote_at'])
        self.assertIsNone(option['open_interest']);self.assertFalse(option['standard_deliverable_verified'])
        self.assertFalse(result['provider_authenticated']);self.assertFalse(result['execution_qualified'])
        self.assertEqual(value,before)

    def test_hash_pinned_import_is_offline_and_keeps_original_window(self):
        value=capture();raw=h.canonical(value)
        with patch.object(h.scope,'derive_window',wraps=h.scope.derive_window) as derive:
            result=h.import_capture(raw,expected_sha256=hashlib.sha256(raw).hexdigest())
            self.assertEqual(result['selection_window'],WINDOW.as_dict())
            self.assertTrue(all(call.args for call in derive.call_args_list))
        with self.assertRaises(ValueError):h.import_capture(raw+b' ',expected_sha256=hashlib.sha256(raw).hexdigest())

    def test_frozen_window_tampering_and_raw_body_tampering_fail(self):
        value=capture();value['selection_window']['oldest']='2024-11-05'
        with self.assertRaises(ValueError):h.normalize_capture(value)
        value=capture();value['responses'][0]['payload']+=' '
        with self.assertRaises(ValueError):h.normalize_capture(value)

    def test_missing_reports_and_no_reports_are_explicit_not_zero_returns(self):
        value=capture();result=h.normalize_capture(value)
        self.assertEqual(result['coverage'][1]['missing_report_sessions'],['2026-11-03'])
        self.assertFalse(result['coverage'][1]['completeness_verified'])
        replace_rows(value,1,[]);result=h.normalize_capture(value)
        self.assertEqual(result['coverage'][1]['status'],'no_reports')
        self.assertEqual(result['coverage'][1]['missing_report_sessions'],['2026-11-03','2026-11-04'])
        self.assertNotIn('pnl',result)

    def test_duplicates_wrong_identity_and_outside_rows_reject_complete_capture(self):
        rows=[row(option=True),row(option=True)]
        for bad in (rows,[{**row(option=True),'symbol':'F'}],[row('2026-10-30',True)],
                    [{**row(option=True),'strike':81}],[row('2026-11-07',True)]):
            value=replace_rows(capture(),1,bad)
            with self.assertRaises(ValueError):h.normalize_capture(value)

    def test_changed_response_order_missing_request_or_forged_transport_reject(self):
        value=capture();value['responses'].reverse()
        with self.assertRaises(ValueError):h.normalize_capture(value)
        value=capture();value['responses'].pop()
        with self.assertRaises(ValueError):h.normalize_capture(value)
        for flag in (True,0):
            value=capture();value['transport']['provider_authenticated']=flag
            with self.assertRaises(ValueError):h.normalize_capture(value)

    def test_actual_receipt_order_and_future_report_or_trade_are_refused(self):
        value=capture();value['responses'][1]['received_at']='2026-11-05T15:00:00.999999999Z'
        with self.assertRaises(ValueError):h.normalize_capture(value)
        value=capture();future=row(option=True);future['created']='2026-11-05T15:00:01.000000001Z'
        replace_rows(value,1,[future])
        with self.assertRaises(ValueError):h.normalize_capture(value)
        value=capture();future=row(option=True);future['last_trade']='2027-01-01T12:00:00.000'
        replace_rows(value,1,[future])
        with self.assertRaises(ValueError):h.normalize_capture(value)

    def test_null_unknown_and_crossed_quotes_are_retained_as_research_issues(self):
        value=capture();bad=row(option=True);bad.update(bid=2,ask=1,bid_size=None,volume=None)
        replace_rows(value,1,[bad]);result=h.normalize_capture(value);option=result['rows'][-1]
        self.assertIsNone(option['bid_size']);self.assertIsNone(option['volume'])
        self.assertEqual((option['bid'],option['ask']),('2','1'))
        self.assertIn('crossed_eod_quote',option['issues'])
        self.assertFalse(option['execution_qualified'])

    def test_unsupported_fields_nonfinite_duplicate_json_and_bad_numeric_types_refused(self):
        for extra in ({'open_interest':1234},{'bid':True},{'volume':1.5},{'ask_size':-1}):
            value=capture();bad=row(option=True);bad.update(extra);replace_rows(value,1,[bad])
            with self.assertRaises(ValueError):h.normalize_capture(value)
        for raw in (b'{"schema":1,"schema":1}',b'{"value":NaN}'):
            with self.assertRaises(ValueError):h.import_capture(raw,expected_sha256=hashlib.sha256(raw).hexdigest())

    def test_decimal_context_and_no_synthetic_prices(self):
        value=capture()
        with localcontext() as context:
            context.prec=2;context.traps[Inexact]=True;context.traps[Rounded]=True
            result=h.normalize_capture(value)
        self.assertEqual(result['rows'][-1]['ask'],'1.04')


class FrozenDatetime(datetime):
    @classmethod
    def now(cls,tz=None):return datetime.fromisoformat(STAMP)


class FakeClock:
    def __init__(self):self.value=0.0;self.sleeps=[]
    def monotonic(self):return self.value
    def sleep(self,seconds):self.sleeps.append(seconds);self.value+=seconds


class HistoryTransportTests(unittest.TestCase):
    def online(self):
        return patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'})
    def policy(self):
        return patch.object(h,'install_network_policy',return_value={'mode':'online'})

    def test_offline_default_and_closed_client_refuse_before_socket(self):
        with patch.object(h.http.client,'HTTPConnection') as connect:
            with self.assertRaises(h.ThetaHistoryUnavailable):h.ThetaHistoryClient().capture(manifest(),window=WINDOW)
            with patch.dict(os.environ,{'ARIADNE_OFFLINE':'1'}):
                with self.assertRaises(h.ThetaHistoryUnavailable):h.ThetaHistoryClient(online=True).capture(manifest(),window=WINDOW)
            client=h.ThetaHistoryClient(online=True);client.close()
            with self.online(),self.policy(),self.assertRaises(h.ThetaHistoryUnavailable):client.capture(manifest(),window=WINDOW)
            connect.assert_not_called()

    def test_fixed_numeric_get_routes_and_conservative_rate_limit(self):
        plan=manifest();connections=[FakeConnection(FakeResponse(json.dumps([row()]).encode())),
            FakeConnection(FakeResponse(json.dumps([row(option=True)]).encode()))]
        clock=FakeClock();checks=[]
        with self.online(),self.policy(),patch.object(h,'datetime',FrozenDatetime),\
             patch.object(h.time,'monotonic',clock.monotonic),patch.object(h.time,'sleep',clock.sleep),\
             patch.object(h.http.client,'HTTPConnection',side_effect=connections) as connect:
            result=h.ThetaHistoryClient(online=True).capture(plan,window=WINDOW,check=lambda:checks.append(True))
        self.assertEqual(len(result['responses']),2);self.assertGreaterEqual(sum(clock.sleeps),3.1)
        for call in connect.call_args_list:self.assertEqual((call.args[0],call.kwargs['port']),('127.0.0.1',25503))
        for connection in connections:
            method,route,kwargs=connection.requests[0]
            self.assertEqual(method,'GET');self.assertIn('/history/eod?',route);self.assertIn('&format=json',route)
            self.assertNotIn('*',route);self.assertNotIn('Authorization',kwargs['headers']);self.assertTrue(connection.closed)
        self.assertGreater(len(checks),8)

    def test_error_redirect_rate_entitlement_never_retry_or_read_body(self):
        for status in (302,401,403,429,500):
            response=FakeResponse(b'PRIVATE_SENTINEL',status=status);connection=FakeConnection(response)
            with self.online(),self.policy(),patch.object(h,'datetime',FrozenDatetime),\
                 patch.object(h.http.client,'HTTPConnection',return_value=connection) as connect:
                with self.assertRaises(h.ThetaHistoryCaptureError) as caught:
                    h.ThetaHistoryClient(online=True).capture(manifest(),window=WINDOW)
            self.assertEqual(connect.call_count,1);self.assertEqual(response.reads,0)
            self.assertNotIn('PRIVATE_SENTINEL',str(caught.exception))
            self.assertEqual(caught.exception.partial_capture['status'],'failed')

    def test_second_request_failure_retains_first_response_without_claiming_completion(self):
        plan=manifest();first=record(plan['requests'][0],[row()])
        client=h.ThetaHistoryClient(online=True)
        with self.online(),self.policy(),patch.object(h,'datetime',FrozenDatetime),\
             patch.object(client,'_http',side_effect=[first,h.ThetaHistoryUnavailable('PRIVATE_SENTINEL')]):
            with self.assertRaises(h.ThetaHistoryCaptureError) as caught:client.capture(plan,window=WINDOW)
        partial=caught.exception.partial_capture
        self.assertEqual(partial['responses'],[first]);self.assertEqual(partial['failed_request_index'],1)
        self.assertNotIn('PRIVATE_SENTINEL',str(caught.exception))
        with self.assertRaises(ValueError):h.normalize_capture(partial)

    def test_malformed_http200_body_retained_privately_in_failed_prefix(self):
        plan=manifest();bad=record(plan['requests'][0],[{'created':'wrong'}]);client=h.ThetaHistoryClient(online=True)
        with self.online(),self.policy(),patch.object(h,'datetime',FrozenDatetime),patch.object(client,'_http',return_value=bad):
            with self.assertRaises(h.ThetaHistoryCaptureError) as caught:client.capture(plan,window=WINDOW)
        self.assertEqual(caught.exception.partial_capture['responses'],[bad])

    def test_permission_callback_stops_before_network_and_route_injection_fails(self):
        with self.online(),self.policy(),patch.object(h.http.client,'HTTPConnection') as connect:
            def revoked():raise PermissionError('PRIVATE_SENTINEL')
            with self.assertRaises(PermissionError):h.ThetaHistoryClient(online=True).capture(manifest(),window=WINDOW,check=revoked)
            connect.assert_not_called()
        with self.assertRaises(ValueError):h._route({'kind':'stock','symbol':'KO'},{'start_date':'2026-11-03&url=bad','end_date':'2026-11-04'})

    def test_response_type_encoding_and_length_bounds_fail_closed(self):
        for headers in ({'Content-Type':'text/html'},{'Content-Encoding':'gzip'},
                        {'Content-Length':str(h.MAX_RESPONSE_BYTES+1)}):
            response=FakeResponse(b'[]',headers=headers);connection=FakeConnection(response)
            with self.online(),self.policy(),patch.object(h,'datetime',FrozenDatetime),patch.object(h.http.client,'HTTPConnection',return_value=connection):
                with self.assertRaises(h.ThetaHistoryCaptureError):h.ThetaHistoryClient(online=True).capture(manifest(),window=WINDOW)
            self.assertEqual(response.reads,0)


if __name__=='__main__':unittest.main()
