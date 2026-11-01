"""Generated source fixtures only; no provider quotes or owner accounts."""
import base64
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import localcontext
import hashlib
import io
import json
import os
import time
import unittest
from unittest.mock import patch

from ariadne import options_eod_data as legacy
from ariadne import options_root_eod_data as data

DAY = date(2026, 10, 29)
NOW = datetime(2026, 10, 30, 19, tzinfo=timezone.utc)
EXPIRY = "2026-11-13"
ONLINE = {"ARIADNE_OPTIONS_ROOT_EOD_COLLECTOR":"1", "ARIADNE_OFFLINE":"0"}


def raw(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def stock(day=DAY, *, early=False):
    start = datetime(day.year, day.month, day.day, 9, 30)
    return [{"timestamp":(start+timedelta(minutes=n)).strftime("%Y-%m-%d %I:%M %p"),
             "open":20, "high":21, "low":19, "close":20, "volume":5}
            for n in range(211 if early else 391)]


def chain(expiry=EXPIRY, day=DAY):
    prefix = expiry[2:].replace("-", "")
    return [{"warning":"only 1 strikes out of 2 strikes were returned. Upgrade to level 2 to get full chain."}]+[
        {"osi-key":"KO----"+prefix+side+"00020000", "ticker":"KO", "date":day.isoformat(),
         "expiration":expiry, "strike":20, "type":"call" if side == "C" else "put",
         "bid":0, "ask":0.5, "bid_size":0, "ask_size":10, "volume":0, "open_interest":0}
        for side in ("C", "P")]


def evidence(body, url, clock=NOW):
    return {"url":url, "received_at":clock.isoformat(), "sha256":hashlib.sha256(body).hexdigest(),
            "bytes":len(body), "content_base64":base64.b64encode(body).decode(), "http_status":200}


def bundle(*, day=DAY, now=NOW, expiries=None, selected=EXPIRY, required=()):
    expiries = expiries or ["2026-11-06", EXPIRY, "2026-11-20"]
    wanted = {selected}
    for identifier in required:
        wanted.add("20"+identifier[2:4]+"-"+identifier[4:6]+"-"+identifier[6:8])
    return {"session":day.isoformat(),
        "expirations":evidence(raw({"call":expiries,"put":expiries}),data.expiration_url(day),now),
        "chains":[evidence(raw(chain(expiry,day)),data.chain_url(day,expiry),now) for expiry in sorted(wanted)],
        "stock":evidence(raw(stock(day)),data.stock_url(day),now)}


def replace_body(item, rows):
    replacement = evidence(raw(rows),item["url"],datetime.fromisoformat(item["received_at"]))
    item.clear();item.update(replacement)


class RootEODDataTests(unittest.TestCase):
    def test_explicit_identity_and_actual_zeros_are_preserved_without_F_conversion(self):
        supplied=bundle();before=deepcopy(supplied)
        result=data.normalize_bundle(supplied,expected_session=DAY)
        snapshot=result["snapshot"]
        self.assertEqual(data.ROOT,"KO")
        self.assertEqual(data.SOURCE_ID,"onclick_ko_eod_v1")
        self.assertEqual(set(snapshot),legacy.SNAPSHOT_KEYS|{"underlying_symbol"})
        self.assertEqual(snapshot["underlying_symbol"],"KO")
        self.assertEqual(result["provenance"]["symbol"],"KO")
        self.assertEqual(result["provenance"]["source_id"],data.SOURCE_ID)
        self.assertTrue(all(q["contract_id"].startswith("KO") for q in snapshot["quotes"]))
        self.assertTrue(all(q["bid"]=="0" and q["bid_size"]==0 for q in snapshot["quotes"]))
        self.assertTrue(snapshot["partial_chain"])
        self.assertIsNone(snapshot["quote_observed_at"])
        self.assertEqual(supplied,before)
        with self.assertRaises(legacy.OptionsEODDataError):legacy.normalize_bundle(supplied,expected_session=DAY)

    def test_snapshot_requires_explicit_KO_and_rejects_foreign_or_adjusted_contracts(self):
        original=data.normalize_bundle(bundle(),expected_session=DAY)["snapshot"]
        mutations=(lambda s:s.pop("underlying_symbol"),lambda s:s.update(underlying_symbol="F"),
            lambda s:s.update(underlying_symbol="INTC"),lambda s:s.update(extra="KO"),
            lambda s:s["quotes"][0].update(contract_id="F261113C00020000"),
            lambda s:s["quotes"][0].update(contract_id="KO1261113C00020000"))
        for mutate in mutations:
            value=deepcopy(original);mutate(value)
            with self.assertRaises(data.OptionsEODDataError):data.validate_snapshot(value)

    def test_required_identifiers_are_root_bound_unique_and_at_most_two(self):
        valid=["KO261106C00020000","KO261120P00020000"]
        self.assertEqual(data.required_ids(valid[::-1]),valid)
        for bad in ([valid[0]]*2,valid+["KO261113C00020000"],["F261113C00020000"],
                    ["INTC261113C00020000"],["KO1261113C00020000"],{},"KO261113C00020000"):
            with self.subTest(bad=bad),self.assertRaises(data.OptionsEODDataError):data.required_ids(bad)

    def test_raw_root_OSI_terms_and_source_session_are_independently_checked(self):
        for field,value in (("ticker","F"),("osi-key","F-----261113C00020000"),
                            ("osi-key","KO1---261113C00020000"),("strike",21),
                            ("type","put"),("date","2026-10-28"),("expiration","2026-11-06")):
            source=bundle();rows=chain();rows[1][field]=value;replace_body(source["chains"][0],rows)
            with self.subTest(field=field,value=value),self.assertRaises(data.OptionsEODDataError):data.normalize_bundle(source,expected_session=DAY)

    def test_duplicate_crossed_invalid_sizes_and_extra_fields_are_not_repaired(self):
        for mutate in (lambda r:r.append(deepcopy(r[1])),lambda r:r[1].update(bid=1,ask=.5),
                       lambda r:r[1].update(bid_size=None),lambda r:r[1].update(ask_size=True),
                       lambda r:r[1].update(bid_size=1.0),lambda r:r[1].update(ask=-1),
                       lambda r:r[1].update(multiplier=100)):
            source=bundle();rows=chain();mutate(rows);replace_body(source["chains"][0],rows)
            with self.assertRaises(data.OptionsEODDataError):data.normalize_bundle(source,expected_session=DAY)

    def test_raw_hashes_and_exact_KO_evidence_URL_bind_every_source(self):
        for mutation in (lambda e:e.update(url=e["url"].replace("ticker=KO","ticker=F")),
                         lambda e:e.update(sha256="0"*64),lambda e:e.update(bytes=e["bytes"]+1),
                         lambda e:e.update(content_base64=base64.b64encode(b"[]").decode())):
            source=bundle();mutation(source["chains"][0])
            with self.assertRaises(data.OptionsEODDataError):data.normalize_bundle(source,expected_session=DAY)

    def test_no_unknown_snapshot_money_or_event_clock_is_inferred(self):
        original=data.normalize_bundle(bundle(),expected_session=DAY)["snapshot"]
        for mutate in (lambda s:s.update(quote_observed_at=NOW.isoformat()),
                       lambda s:s.update(underlying_close="20.0"),lambda s:s.update(partial_chain=1),
                       lambda s:s.update(stock_sha256="x"),lambda s:s["quotes"][0].update(ask="0.50")):
            value=deepcopy(original);mutate(value)
            with self.assertRaises(data.OptionsEODDataError):data.validate_snapshot(value)

    def test_exact_money_under_hostile_decimal_context(self):
        supplied=bundle();rows=chain();rows[1]["ask"]=.12345678;replace_body(supplied["chains"][0],rows)
        expected=data.normalize_bundle(supplied,expected_session=DAY)
        with localcontext() as context:
            context.prec=2
            self.assertEqual(data.normalize_bundle(supplied,expected_session=DAY),expected)
        self.assertEqual(expected["snapshot"]["quotes"][0]["ask"],"0.12345678")

    def test_candidate_uses_actual_NY_receipt_month_and_fallback_is_explicit(self):
        day=date(2026,10,30);now=datetime(2026,11,1,0,30,tzinfo=timezone.utc)
        result=data.normalize_bundle(bundle(day=day,now=now),expected_session=day)
        self.assertEqual(result["provenance"]["candidate_selection_month"],"2026-10")
        self.assertEqual(result["provenance"]["candidate_selection_scope"],"global_warmup_fallback")
        now=datetime(2026,11,2,14,35,tzinfo=timezone.utc)
        result=data.normalize_bundle(bundle(day=day,now=now),expected_session=day)
        self.assertEqual(result["provenance"]["candidate_selection_month"],"2026-11")
        self.assertEqual(result["provenance"]["candidate_selection_scope"],"actual_receipt_month")

    def test_required_expiries_and_exact_required_contract_are_not_replaced(self):
        required=["KO261106C00020000","KO261120P00020000"]
        result=data.normalize_bundle(bundle(required=required),expected_session=DAY,required_contracts=required)
        self.assertTrue(set(required)<={q["contract_id"] for q in result["snapshot"]["quotes"]})
        with self.assertRaises(data.OptionsEODUnavailable):
            data.normalize_bundle(bundle(required=required),expected_session=DAY,required_contracts=["KO261106C00021000",required[1]])
        with self.assertRaises(data.OptionsEODUnavailable):
            data.normalize_bundle(bundle(),expected_session=DAY,required_contracts=["KO261127P00020000"])

    def test_valid_no_candidate_records_only_stock_with_two_shared_budget_requests(self):
        source=bundle(expiries=["2026-12-18"]);source['chains']=[]
        before=deepcopy(source)
        normalized=data.normalize_bundle(source,expected_session=DAY)
        self.assertEqual(normalized['snapshot']['quotes'],[])
        self.assertEqual(normalized['snapshot']['underlying_symbol'],'KO')
        self.assertEqual(normalized['snapshot']['underlying_close'],'20')
        self.assertIsNone(normalized['snapshot']['quote_observed_at'])
        self.assertIsNone(normalized['provenance']['candidate_expiry'])
        self.assertEqual(normalized['provenance']['candidate_selection_scope'],'no_eligible_common_expiry')
        self.assertEqual(normalized['provenance']['quote_sources'],[])
        self.assertEqual(source,before)
        self.assertEqual(data.validate_snapshot(normalized['snapshot']),normalized['snapshot'])
        # The older source contract still rejects an empty quote list.
        legacy_view={k:v for k,v in normalized['snapshot'].items() if k!='underlying_symbol'}
        with self.assertRaises(legacy.OptionsEODDataError):legacy.validate_snapshot(legacy_view)
        calls=[];mapping={e['url']:e for e in (source['expirations'],source['stock'])}
        def fetch(url,*,deadline,check,budget):
            check();calls.append((url,deadline,id(budget)));return deepcopy(mapping[url])
        with patch.dict(os.environ,ONLINE),patch.object(data,'_now',return_value=NOW),patch.object(data,'_fetch',side_effect=fetch):
            self.assertEqual(data.fetch_bundle(DAY),source)
        self.assertEqual([r[0] for r in calls],[data.expiration_url(DAY),data.stock_url(DAY)])
        self.assertEqual(len({r[1] for r in calls}),1)
        self.assertEqual(len({r[2] for r in calls}),1)

    def test_no_candidate_missing_required_metadata_is_fatal_before_more_requests(self):
        source=bundle(expiries=["2026-12-18"]);source['chains']=[]
        required=["KO261113P00020000"]
        with self.assertRaises(data.OptionsEODUnavailable):
            data.normalize_bundle(source,expected_session=DAY,required_contracts=required)
        calls=[]
        def fetch(url,**kwargs):
            calls.append(url)
            self.assertEqual(url,data.expiration_url(DAY))
            return source['expirations']
        with patch.dict(os.environ,ONLINE),patch.object(data,'_now',return_value=NOW),patch.object(data,'_fetch',side_effect=fetch):
            with self.assertRaises(data.OptionsEODUnavailable):data.fetch_bundle(DAY,required)
        self.assertEqual(calls,[data.expiration_url(DAY)])

    def test_requested_empty_chain_does_not_become_no_candidate_observation(self):
        for rows in ([],[{'warning':'only 1 strikes out of 2 strikes were returned. Upgrade to level 2 to get full chain.'}]):
            source=bundle();replace_body(source['chains'][0],rows)
            with self.subTest(rows=rows),self.assertRaises(data.OptionsEODDataError):
                data.normalize_bundle(source,expected_session=DAY)

    def test_exact_wanted_cardinality_rejects_extra_and_omitted_chains(self):
        no_candidate=bundle(expiries=["2026-12-18"])
        # An unrelated returned chain cannot be laundered into a stock-only
        # observation, even though there is no eligible candidate.
        with self.assertRaises(data.OptionsEODDataError):data.normalize_bundle(no_candidate,expected_session=DAY)
        for chains in ([],bundle()['chains']*2):
            source=bundle();source['chains']=chains
            with self.assertRaises(data.OptionsEODDataError):data.normalize_bundle(source,expected_session=DAY)

    def test_no_candidate_still_requires_every_actual_held_contract_quote(self):
        required=['KO261218P00020000']
        source=bundle(expiries=['2026-12-18'],selected='2026-12-18',required=required)
        result=data.normalize_bundle(source,expected_session=DAY,required_contracts=required)
        self.assertIsNone(result['provenance']['candidate_expiry'])
        self.assertTrue(required[0] in {q['contract_id'] for q in result['snapshot']['quotes']})
        rows=chain(expiry='2026-12-18')
        rows=[r for r in rows if r.get('type')!='put']
        replace_body(source['chains'][0],rows)
        with self.assertRaises(data.OptionsEODUnavailable):
            data.normalize_bundle(source,expected_session=DAY,required_contracts=required)

    def test_baseline_prefix_validates_whole_raw_window_and_binds_whole_hash(self):
        days=[];cursor=date(2026,10,30)
        while len(days)<15:
            if legacy.is_session(cursor):days.append(cursor)
            cursor-=timedelta(days=1)
        days.reverse();rows=[r for day in days for r in stock(day)];body=raw(rows)
        received=datetime(2026,11,2,19,tzinfo=timezone.utc)
        baseline=data.load_baseline(body,received_at=received,through_date=days[-2])
        self.assertEqual(len(baseline),14)
        self.assertEqual(baseline[-1]["source_date"],"2026-10-29")
        self.assertEqual({r["stock_sha256"] for r in baseline},{hashlib.sha256(body).hexdigest()})
        self.assertEqual(body,raw(rows))
        self.assertTrue(all(set(r)=={"source_date","underlying_close","received_at","stock_sha256"} for r in baseline))
        with self.assertRaises(legacy.OptionsEODDataError):legacy.load_baseline(body,received_at=received,through_date=days[-2])
        # Even an unrequested final day must be parsed, not sliced away first.
        rows[-2]["close"]=1000
        with self.assertRaises(data.OptionsEODDataError):data.load_baseline(raw(rows),received_at=received,through_date=days[-2])

    def test_baseline_prefix_missing_day_nonhistorical_tail_and_gaps_rejected(self):
        received=datetime(2026,10,30,19,tzinfo=timezone.utc)
        with self.assertRaises(data.OptionsEODDataError):data.load_baseline(raw(stock()),received_at=received,through_date=date(2026,10,28))
        with self.assertRaises(data.OptionsEODDataError):data.load_baseline(raw(stock()+stock(date(2026,10,30))),received_at=received,through_date=DAY)
        with self.assertRaises(data.OptionsEODDataError):data.load_baseline(raw(stock(date(2026,10,27))+stock(DAY)),received_at=received,through_date=DAY)
        self.assertEqual(data.load_baseline(None,received_at=received,through_date=DAY),[])
        self.assertEqual(data.load_baseline(b"[]",received_at=received,through_date=DAY),[])

    def test_baseline_accepts_early_close_and_keeps_zero_volume_source_rows(self):
        day=date(2026,11,27);rows=stock(day,early=True);rows[-1]["volume"]=0
        received=datetime(2026,11,30,19,tzinfo=timezone.utc);body=raw(rows)
        baseline=data.load_baseline(body,received_at=received,through_date=day)
        self.assertEqual(len(baseline),1)
        self.assertEqual(baseline[0]["stock_sha256"],hashlib.sha256(body).hexdigest())

    def test_url_surface_fixed_KO_single_session_without_keys_or_query_authority(self):
        for good in (data.expiration_url(DAY),data.chain_url(DAY,EXPIRY),data.stock_url(DAY)):
            self.assertEqual(data._approved_url(good).hostname,data.HOST)
            with self.assertRaises(legacy.OptionsEODDataError):legacy._approved_url(good)
        for bad in (legacy.expiration_url(DAY),data.expiration_url(DAY)+"&apikey=x",
                    data.expiration_url(DAY)+"&ticker=KO",data.chain_url(DAY,EXPIRY)+"&type=put",
                    data.chain_url(DAY,EXPIRY)+"&strike=20",data.stock_url(DAY).replace('/v2/','/v2/adj/'),
                    data.stock_url(DAY).replace('extended=false','extended=true'),
                    data.stock_url(DAY).replace('to=2026-10-29','to=2026-10-30'),
                    data.expiration_url(DAY).replace(data.HOST,'127.0.0.1'),
                    data.expiration_url(DAY).replace('https://','http://')):
            with self.subTest(bad=bad),patch.object(legacy.socket,'getaddrinfo') as dns:
                with self.assertRaises(data.OptionsEODDataError):
                    data._fetch(bad,deadline=time.monotonic()+2,check=lambda:None,budget={"requests":0,"remaining":data.MAX_WIRE})
                dns.assert_not_called()

    def test_private_transport_wrappers_use_distinct_code_owned_validators(self):
        with patch.object(legacy,'_fetch_common',return_value='sentinel') as common:
            kwargs={"deadline":1,"check":lambda:None,"budget":{}}
            self.assertEqual(legacy._fetch(legacy.expiration_url(DAY),**kwargs),'sentinel')
            self.assertIs(common.call_args.kwargs['approved_url'],legacy._approved_url)
            self.assertEqual(data._fetch(data.expiration_url(DAY),**kwargs),'sentinel')
            self.assertIs(common.call_args.kwargs['approved_url'],data._approved_url)

    def test_worker_requires_dedicated_root_permit_and_historical_session(self):
        with patch.dict(os.environ,{"ARIADNE_OPTIONS_EOD_COLLECTOR":"1","ARIADNE_OPTIONS_ROOT_EOD_COLLECTOR":"0","ARIADNE_OFFLINE":"0"}),patch.object(data,'_fetch') as fetch:
            with self.assertRaises(data.OptionsEODUnavailable):data.fetch_bundle(DAY)
            fetch.assert_not_called()
        with patch.dict(os.environ,ONLINE),patch.object(data,'_now',return_value=NOW),patch.object(data,'_fetch') as fetch:
            for day in (NOW.date(),date(2025,10,1)):
                with self.assertRaises(data.OptionsEODDataError):data.fetch_bundle(day)
            fetch.assert_not_called()

    def test_daily_three_requests_and_required_max_five_share_one_deadline_and_budget(self):
        for required,count in (((),3),(["KO261106C00020000","KO261120P00020000"],5)):
            source=bundle(required=required)
            mapping={e["url"]:e for e in [source['expirations'],*source['chains'],source['stock']]};calls=[]
            def fetch(url,*,deadline,check,budget):
                check();calls.append((url,deadline,id(budget)));return deepcopy(mapping[url])
            with patch.dict(os.environ,ONLINE),patch.object(data,'_now',return_value=NOW),patch.object(data,'_fetch',side_effect=fetch):
                self.assertEqual(data.fetch_bundle(DAY,required),source)
            self.assertEqual(len(calls),count)
            self.assertEqual(len({x[1] for x in calls}),1)
            self.assertEqual(len({x[2] for x in calls}),1)
            self.assertTrue(all('ticker=KO' in x[0] and 'type=' not in x[0] and 'strike=' not in x[0] for x in calls))
        self.assertEqual(data.WORKER_SECONDS,15)
        self.assertEqual(data.MAX_WIRE,8*1024*1024)

    def test_denial_retains_partial_evidence_and_never_retries_or_fetches_stock(self):
        source=bundle();calls=[]
        def fetch(url,**kwargs):
            calls.append(url)
            if len(calls)==1:return source['expirations']
            error=data.OptionsEODUnavailable('HTTP403');error.source_evidence={'raw':'retained'};raise error
        with patch.dict(os.environ,ONLINE),patch.object(data,'_now',return_value=NOW),patch.object(data,'_fetch',side_effect=fetch):
            with self.assertRaises(data.OptionsEODUnavailable) as caught:data.fetch_bundle(DAY)
        self.assertEqual(len(calls),2)
        self.assertEqual(caught.exception.source_evidence['partial_bundle']['expirations'],source['expirations'])
        self.assertEqual(caught.exception.source_evidence['failed_response'],{'raw':'retained'})
        self.assertIsNone(caught.exception.source_evidence['partial_bundle']['stock'])

    def transport(self,wire,*,budget=None,addresses=None):
        class Channel:
            def __init__(self):self.closed=False;self.sent=[];self.connected=[];self.stream=io.BytesIO(wire)
            def settimeout(self,value):self.timeout=value
            def connect(self,value):self.connected.append(value)
            def sendall(self,value):self.sent.append(bytes(value))
            def makefile(self,*args,**kwargs):return self.stream
            def shutdown(self,mode):pass
            def close(self):self.closed=True
        raw_socket,tls_socket=Channel(),Channel()
        class TLS:
            def wrap_socket(self,channel,*,server_hostname):self.host=server_hostname;return tls_socket
        tls=TLS();budget=budget or {'requests':0,'remaining':data.MAX_WIRE}
        addresses=addresses if addresses is not None else [(2,1,6,'',('8.8.8.8',443))]
        with patch.object(legacy.socket,'getaddrinfo',return_value=addresses),patch.object(legacy.socket,'socket',return_value=raw_socket),patch('ariadne.internet._tls_context',return_value=tls):
            result=data._fetch(data.expiration_url(DAY),deadline=time.monotonic()+5,check=lambda:None,budget=budget)
        return result,raw_socket,tls_socket,tls,budget

    def test_KO_transport_retains_F_wire_deadline_TLS_and_no_credentials_contract(self):
        wire=b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n2\r\n[]\r\n0\r\n\r\n'
        result,raw_socket,tls_socket,tls,budget=self.transport(wire)
        sent=b''.join(tls_socket.sent)
        self.assertEqual(data.MAX_WIRE-budget['remaining'],len(wire)+len(sent))
        self.assertEqual(budget['requests'],1)
        self.assertEqual(base64.b64decode(result['content_base64']),b'[]')
        self.assertIn(b'ticker=KO',sent)
        self.assertNotIn(b'ticker=F',sent)
        self.assertNotIn(b'Authorization',sent)
        self.assertNotIn(b'Cookie',sent)
        self.assertEqual(raw_socket.connected,[('8.8.8.8',443)])
        self.assertEqual(tls.host,data.HOST)
        self.assertTrue(raw_socket.closed and tls_socket.closed)

    def test_KO_transport_rejects_redirect_encoding_nonjson_size_DNS_and_request_budget(self):
        cases=(b'HTTP/1.1 302 Found\r\nLocation: https://other.example/\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n[]',
               b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: gzip\r\nContent-Length: 2\r\n\r\n[]',
               b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 2\r\n\r\n[]',
               b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 99999999\r\n\r\n[]')
        for wire in cases:
            with self.subTest(wire=wire[:40]),self.assertRaises(data.OptionsEODUnavailable):self.transport(wire)
        with self.assertRaises(data.OptionsEODUnavailable):self.transport(cases[0],addresses=[(2,1,6,'',('127.0.0.1',443))])
        for budget in ({'requests':5,'remaining':data.MAX_WIRE},{'requests':0,'remaining':0}):
            with patch.object(legacy.socket,'getaddrinfo') as dns,self.assertRaises(data.OptionsEODUnavailable):
                data._fetch(data.expiration_url(DAY),deadline=time.monotonic()+5,check=lambda:None,budget=budget)
            dns.assert_not_called()
        with self.assertRaises(data.OptionsEODUnavailable):self.transport(cases[0],budget={'requests':0,'remaining':50})


if __name__ == '__main__':
    unittest.main()
