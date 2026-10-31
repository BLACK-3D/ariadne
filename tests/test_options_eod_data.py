"""Software-only generated fixtures; no provider market data is redistributed."""
import base64
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import json
import os
import time
import unittest
from unittest.mock import patch

from ariadne import options_eod_data as data

DAY = date(2026, 10, 29)
NOW = datetime(2026, 10, 30, 19, tzinfo=timezone.utc)
EXPIRY = "2026-11-13"


def raw(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def stock(day=DAY, *, early=False):
    start = datetime(day.year, day.month, day.day, 9, 30)
    return [{"timestamp":(start+timedelta(minutes=n)).strftime("%Y-%m-%d %I:%M %p"),
             "open":20, "high":21, "low":19, "close":20, "volume":5}
            for n in range(211 if early else 391)]


def chain(expiry=EXPIRY, partial=True):
    prefix = expiry[2:].replace("-", "")
    rows = [{"osi-key":"F-----"+prefix+side+"00020000", "ticker":"F",
             "date":DAY.isoformat(), "expiration":expiry, "strike":20,
             "type":"call" if side == "C" else "put", "bid":0,
             "ask":0.5, "bid_size":0, "ask_size":10, "last":99,
             "mark":99, "volume":999, "open_interest":999, "implied_volatility":0}
            for side in ("C", "P")]
    return ([{"warning":"only 1 strikes out of 2 strikes were returned. Upgrade to level 2 to get full chain."}] if partial else [])+rows


def evidence(body, url, clock=NOW):
    return {"url":url, "received_at":clock.isoformat(), "sha256":hashlib.sha256(body).hexdigest(),
            "bytes":len(body), "content_base64":base64.b64encode(body).decode(), "http_status":200}


def bundle(*, day=DAY, expiries=None, required=()):
    expiries = expiries or ["2026-11-06", EXPIRY, "2026-11-20"]
    metadata = {"call":expiries, "put":expiries}
    choices = sorted((item for item in expiries if 7 <= (date.fromisoformat(item)-day).days <= 21),
                     key=lambda item:(abs((date.fromisoformat(item)-day).days-14), item))
    wanted = {choices[0]} if choices else set()
    for identifier in required:
        wanted.add("20"+identifier[1:3]+"-"+identifier[3:5]+"-"+identifier[5:7])
    return {"session":day.isoformat(), "expirations":evidence(raw(metadata),data.expiration_url(day)),
            "chains":[evidence(raw(chain(expiry)),data.chain_url(day,expiry)) for expiry in sorted(wanted)],
            "stock":evidence(raw(stock(day)),data.stock_url(day))}


def replace_body(item, value):
    item.update(evidence(raw(value),item["url"]))


def calendar_bundle(day, received, expiries, candidate, *, required=()):
    """Generated source fixtures, never actual quotes or performance evidence."""
    wanted = {candidate} if candidate else set()
    for identifier in required:
        wanted.add("20"+identifier[1:3]+"-"+identifier[3:5]+"-"+identifier[5:7])
    chains = []
    for expiry in sorted(wanted):
        rows = chain(expiry)
        for row in rows:
            if "date" in row:
                row["date"] = day.isoformat()
        chains.append(evidence(raw(rows), data.chain_url(day, expiry), received))
    return {"session":day.isoformat(),
            "expirations":evidence(raw({"call":expiries,"put":expiries}),data.expiration_url(day),received),
            "chains":chains,"stock":evidence(raw(stock(day)),data.stock_url(day),received)}


class EODDataTests(unittest.TestCase):
    def normalize(self, value=None, **kwargs):
        return data.normalize_bundle(bundle() if value is None else value, expected_session=DAY, **kwargs)

    def test_exact_date_only_model_and_retained_partial_warning(self):
        result = self.normalize()
        snapshot = result["snapshot"]
        self.assertEqual(set(snapshot),data.SNAPSHOT_KEYS)
        self.assertEqual(snapshot["source_date"],DAY.isoformat())
        self.assertEqual(snapshot["underlying_close"],"20")
        self.assertIsNone(snapshot["quote_observed_at"])
        self.assertTrue(snapshot["partial_chain"])
        self.assertEqual(snapshot["quotes"][0],{"contract_id":"F261113C00020000", "expiry":EXPIRY,
                         "type":"call", "strike":"20", "bid":"0", "ask":"0.5", "bid_size":0,"ask_size":10})
        self.assertEqual(result["provenance"]["candidate_expiry"],EXPIRY)
        self.assertEqual(result["provenance"]["warnings"][0]["counts"],{"returned_strikes":1,"total_strikes":2})
        self.assertEqual(data.validate_snapshot(snapshot),snapshot)

    def test_candidate_policy_is_explicit_pinned_and_keeps_engine_contract_unchanged(self):
        from ariadne.options_eod import RULE_SHA256
        normalized=self.normalize()
        self.assertEqual(data.CANDIDATE_EXPIRY_POLICY["id"],"onclick_actual_receipt_month_candidate_v2")
        self.assertEqual(data.CANDIDATE_EXPIRY_POLICY_SHA256,
                         "c88e27fc712428fd852ccfc3f741832e1d8323465acfd0d44bb1366e1a416dd4")
        self.assertEqual(data.CANDIDATE_EXPIRY_POLICY_SHA256,
                         hashlib.sha256(data.canonical(data.CANDIDATE_EXPIRY_POLICY)).hexdigest())
        self.assertEqual(normalized["provenance"]["candidate_expiry_policy_sha256"],data.CANDIDATE_EXPIRY_POLICY_SHA256)
        self.assertEqual(normalized["provenance"]["candidate_expiry_policy"],data.CANDIDATE_EXPIRY_POLICY)
        normalized["provenance"]["candidate_expiry_policy"]["id"]="changed returned view"
        self.assertEqual(data.CANDIDATE_EXPIRY_POLICY["id"],"onclick_actual_receipt_month_candidate_v2")
        self.assertEqual(data.SOURCE_ID,"onclick_f_eod_v1")
        self.assertEqual(set(normalized["snapshot"]),data.SNAPSHOT_KEYS)
        self.assertEqual(RULE_SHA256,"22ea96117b89f2b7c38e3853071363449aacac0074bf83a113f4fa36aec3b0f0")

    def test_near_month_end_prefers_current_receipt_month_over_global_nearest(self):
        day=date(2026,10,20);received=datetime(2026,10,21,14,35,tzinfo=timezone.utc)
        expiries=["2026-10-30","2026-11-06","2026-11-13"]
        # The prior global rule would prefer17DTE over10DTE. The frozen
        # engine can consider only the actual October decision-month expiry.
        source=calendar_bundle(day,received,expiries,"2026-10-30")
        before=deepcopy(source)
        result=data.normalize_bundle(source,expected_session=day)
        self.assertEqual(result["provenance"]["candidate_expiry"],"2026-10-30")
        self.assertEqual(result["provenance"]["candidate_selection_scope"],"actual_receipt_month")
        self.assertEqual(result["provenance"]["candidate_selection_month"],"2026-10")
        self.assertEqual(result["provenance"]["candidate_selection_receipt_at"],received.isoformat())
        self.assertEqual({row["expiry"] for row in result["snapshot"]["quotes"]},{"2026-10-30"})
        self.assertEqual(source,before)

    def test_parent_rejects_wrong_global_expiry_instead_of_adopting_or_refetching(self):
        day=date(2026,10,20);received=datetime(2026,10,21,14,35,tzinfo=timezone.utc)
        source=calendar_bundle(day,received,["2026-10-30","2026-11-06"],"2026-11-06")
        with patch.object(data,"_fetch",side_effect=AssertionError("Parent normalization never fetches")),self.assertRaises(data.OptionsEODDataError):
            data.normalize_bundle(source,expected_session=day)

    def test_month_boundary_uses_actual_receipt_month_not_historical_source_month(self):
        day=date(2026,10,30);received=datetime(2026,11,2,14,35,tzinfo=timezone.utc)
        source=calendar_bundle(day,received,["2026-11-06","2026-11-13","2026-11-20"],"2026-11-13")
        result=data.normalize_bundle(source,expected_session=day)
        self.assertEqual(result["provenance"]["candidate_selection_month"],"2026-11")
        self.assertEqual(result["provenance"]["candidate_selection_scope"],"actual_receipt_month")
        self.assertEqual(result["provenance"]["candidate_expiry"],"2026-11-13")

    def test_new_york_month_differs_from_utc_and_empty_month_retains_warmup_subset(self):
        day=date(2026,10,30)
        # November in UTC is still October in New York. There is no eligible
        # October expiry, so these November rows remain warmup-only fallback.
        received=datetime(2026,11,1,0,30,tzinfo=timezone.utc)
        source=calendar_bundle(day,received,["2026-11-06","2026-11-13","2026-11-20"],"2026-11-13")
        result=data.normalize_bundle(source,expected_session=day)
        self.assertEqual(result["provenance"]["candidate_selection_month"],"2026-10")
        self.assertEqual(result["provenance"]["candidate_selection_scope"],"global_warmup_fallback")
        self.assertEqual(result["provenance"]["candidate_expiry"],"2026-11-13")
        self.assertIsNone(result["snapshot"]["quote_observed_at"])

    def test_required_expiries_outside_candidate_month_are_retained_with_same_five_request_bound(self):
        day=date(2026,10,20);received=datetime(2026,10,21,14,35,tzinfo=timezone.utc)
        required=["F261106C00020000","F261113P00020000"]
        source=calendar_bundle(day,received,["2026-10-30","2026-11-06","2026-11-13"],"2026-10-30",required=required)
        mapping={item["url"]:item for item in [source["expirations"],*source["chains"],source["stock"]]}
        calls=[]
        def fetch(url,*,deadline,check,budget):
            check();calls.append((url,deadline,id(budget)));return deepcopy(mapping[url])
        with patch.dict(os.environ,{"ARIADNE_OPTIONS_EOD_COLLECTOR":"1","ARIADNE_OFFLINE":"0"}),patch.object(data,"_now",return_value=received),patch.object(data,"_fetch",side_effect=fetch):
            result=data.fetch_bundle(day,required)
        normalized=data.normalize_bundle(result,expected_session=day,required_contracts=required)
        self.assertEqual(normalized["provenance"]["candidate_expiry"],"2026-10-30")
        self.assertEqual({item["expiry"] for item in normalized["provenance"]["quote_sources"]},
                         {"2026-10-30","2026-11-06","2026-11-13"})
        self.assertTrue(set(required)<={item["contract_id"] for item in normalized["snapshot"]["quotes"]})
        self.assertEqual(len(calls),5)
        self.assertEqual(len({item[1] for item in calls}),1)
        self.assertEqual(len({item[2] for item in calls}),1)
        self.assertEqual(data.WORKER_SECONDS,15)
        self.assertEqual(data.MAX_WIRE,8*1024*1024)
        self.assertTrue(all(data._approved_url(item[0]).hostname==data.HOST for item in calls))
        self.assertTrue(all("type=" not in item[0] and "strike=" not in item[0] for item in calls))

    def test_expiry_subset_does_not_relax_delayed_fill_or_actual_decision_month_gates(self):
        from ariadne.options_eod import _eligible
        day=date(2026,10,22);received=datetime(2026,10,23,14,35,tzinfo=timezone.utc)
        source=calendar_bundle(day,received,["2026-10-30","2026-11-06"],"2026-10-30")
        snapshot=data.normalize_bundle(source,expected_session=day)["snapshot"]
        quote=next(item for item in snapshot["quotes"] if item["type"]=="call")
        quote.update(bid="0.45",bid_size=1)
        self.assertTrue(_eligible(quote,snapshot,"call",received))
        later=deepcopy(snapshot);later["source_date"]="2026-10-26"
        self.assertFalse(_eligible(quote,later,"call",datetime(2026,10,27,14,35,tzinfo=timezone.utc)))
        self.assertFalse(_eligible(quote,snapshot,"call",datetime(2026,11,2,14,35,tzinfo=timezone.utc)))

    def test_candidate_clock_is_explicit_aware_and_ties_remain_deterministic(self):
        metadata=raw({"call":["2026-11-13","2026-11-19"],"put":["2026-11-13","2026-11-19"]})
        day=date(2026,11,2);received=datetime(2026,11,3,14,35,tzinfo=timezone.utc)
        self.assertEqual(data._expirations(metadata,day,received_at=received)[1],"2026-11-13")
        for clock in (None,received.replace(tzinfo=None),"2026-11-03",True):
            with self.subTest(clock=clock),self.assertRaises(data.OptionsEODDataError):
                data._expirations(metadata,day,received_at=clock)
    def test_input_is_not_mutated_and_hashes_bind_each_raw_byte(self):
        value=bundle();original=deepcopy(value)
        result=self.normalize(value)
        self.assertEqual(value,original)
        self.assertEqual(result["snapshot"]["stock_sha256"],value["stock"]["sha256"])
        value["chains"][0]["content_base64"] = base64.b64encode(b'[]').decode()
        with self.assertRaises(data.OptionsEODDataError): self.normalize(value)

    def test_money_is_exact_even_under_hostile_decimal_context(self):
        expected=self.normalize()
        with localcontext() as context:
            context.prec=2
            self.assertEqual(self.normalize(),expected)
        value=bundle();body=raw(chain()).replace(b'"ask":0.5',b'"ask":0.12345678')
        value["chains"][0]=evidence(body,value["chains"][0]["url"])
        self.assertEqual(self.normalize(value)["snapshot"]["quotes"][0]["ask"],"0.12345678")

    def test_duplicate_nonfinite_encoding_depth_and_byte_bounds_fail(self):
        for body in (b'{"x":1,"x":1}', b'[NaN]',b'[Infinity]',b'\xff',b'', b'['*1100+b']'*1100,
                     b'[1e999999999]',b'[0e-999999999]',b'x'*(data.MAX_RAW+1)):
            with self.subTest(body=body[:30]),self.assertRaises(data.OptionsEODDataError):data._json(body)

    def test_malformed_quote_fields_are_not_repaired(self):
        for field,bad in (("bid",True),("ask",None),("bid","0"),("bid",-1),("ask",0.000000001),
                          ("bid_size",None),("ask_size",True),("bid_size",1.0),("ask_size",-1),
                          ("ticker","F1"),("type",[]),("date","2026-10-28"),("expiration","2026-11-06")):
            value=bundle();rows=chain();rows[1][field]=bad;replace_body(value["chains"][0],rows)
            with self.subTest(field=field,bad=bad),self.assertRaises(data.OptionsEODDataError):self.normalize(value)

    def test_crossed_even_nonrequired_or_economically_impossible_reject_capture(self):
        for bid,ask in ((1,0.5),(21,22)):
            value=bundle();rows=chain();rows[1].update(bid=bid,ask=ask);replace_body(value["chains"][0],rows)
            with self.assertRaises(data.OptionsEODDataError):self.normalize(value)

    def test_zero_quotes_remain_zero_and_last_mark_volume_never_supply_prices(self):
        value=bundle();rows=chain();rows[1].update(bid=0,ask=0,bid_size=0,ask_size=0);replace_body(value["chains"][0],rows)
        quote=self.normalize(value)["snapshot"]["quotes"][0]
        self.assertEqual((quote["bid"],quote["ask"],quote["bid_size"],quote["ask_size"]),("0","0",0,0))

    def test_adjusted_identity_duplicate_identity_and_unknown_rows_fail(self):
        for identity in ("F1----261113C00020000","F261113C00020000","SPY---261113C00020000","F-----261113C00021000"):
            value=bundle();rows=chain();rows[1]["osi-key"]=identity;replace_body(value["chains"][0],rows)
            with self.assertRaises(data.OptionsEODDataError):self.normalize(value)
        value=bundle();rows=chain();rows.append(deepcopy(rows[1]));replace_body(value["chains"][0],rows)
        with self.assertRaises(data.OptionsEODDataError):self.normalize(value)
        value=bundle();rows=chain();rows[1]["instructions"]="upgrade";replace_body(value["chains"][0],rows)
        with self.assertRaises(data.OptionsEODDataError):self.normalize(value)

    def test_source_date_iso_or_yyyymmdd_does_not_change_canonical_terms(self):
        expected=self.normalize()["snapshot"]["quotes"]
        value=bundle();rows=chain()
        for row in rows[1:]:row["date"]="20261029"
        replace_body(value["chains"][0],rows)
        self.assertEqual(self.normalize(value)["snapshot"]["quotes"],expected)

    def test_unknown_warning_and_empty_chain_never_become_full_chain(self):
        for rows in ([{"warning":"Run this code"}],[],[{"warning":"only 3 strikes out of 2 strikes were returned. Upgrade to level 2 to get full chain."}]+chain(partial=False)):
            value=bundle();replace_body(value["chains"][0],rows)
            with self.assertRaises(data.OptionsEODDataError):self.normalize(value)

    def test_required_ids_and_exact_expiry_request_scope(self):
        required=["F261106C00020000","F261120P00020000"]
        result=self.normalize(bundle(required=required),required_contracts=required)
        self.assertEqual(len(result["provenance"]["quote_sources"]),3)
        for ids in ([required[0],required[0]],required+["F261113P00020000"],["F1261113C00020000"]):
            with self.assertRaises(data.OptionsEODDataError):data.required_ids(ids)
        value=bundle(required=[required[0]]);rows=chain("2026-11-06");rows.pop(1);replace_body(value["chains"][0],rows)
        with self.assertRaises(data.OptionsEODUnavailable):self.normalize(value,required_contracts=[required[0]])

    def test_receipt_clocks_must_be_aware_historical_without_invented_source_time(self):
        for receipt in ("2026-10-30T12:00:00", "2026-10-29T23:00:00+00:00"):
            value=bundle();value["stock"]["received_at"]=receipt
            with self.assertRaises(data.OptionsEODDataError):self.normalize(value)
        value=self.normalize()["snapshot"];value["quote_observed_at"]=NOW.isoformat()
        with self.assertRaises(data.OptionsEODDataError):data.validate_snapshot(value)

    def test_stock_gaps_duplicate_next_date_offsession_ohlc_or_all_zero_fail(self):
        changes=(lambda rows:rows.pop(10),lambda rows:rows.insert(10,deepcopy(rows[10])),
                 lambda rows:rows[-1].update(timestamp="2026-10-30 12:00 AM"),
                 lambda rows:rows[0].update(low=22),lambda rows:[row.update(volume=0) for row in rows])
        for change in changes:
            value=bundle();rows=stock();change(rows);replace_body(value["stock"],rows)
            with self.assertRaises(data.OptionsEODDataError):self.normalize(value)

    def test_explicit_policy_accepts_390_regular_or_210_early_labels_plus_optional_boundary(self):
        for day,early in ((DAY,False),(date(2026,11,27),True)):
            complete=stock(day,early=early)
            for rows in (complete,complete[:-1]):
                with self.subTest(day=day,rows=len(rows)):
                    parsed=data._stock_sessions(raw(rows),details=True)[day]
                    self.assertEqual(parsed['underlying_close'],'20')
                    self.assertEqual(parsed['observed_rows'],len(rows))
                    self.assertEqual(parsed['closing_boundary_present'],len(rows)==len(complete))
        self.assertEqual(data.SESSION_GRID_POLICY_SHA256,hashlib.sha256(data.canonical(data.SESSION_GRID_POLICY)).hexdigest())
        self.assertIn('does not verify',data.SESSION_GRID_POLICY['limitations'])

    def test_optional_boundary_cannot_replace_a_missing_core_label_or_extend_the_window(self):
        for remove in (0,10,389):
            rows=stock();rows.pop(remove)
            with self.subTest(remove=remove),self.assertRaises(data.OptionsEODDataError):data._stock_sessions(raw(rows))
        rows=stock();outside=deepcopy(rows[-1]);outside['timestamp']='2026-10-29 04:01 PM';rows.append(outside)
        with self.assertRaises(data.OptionsEODDataError):data._stock_sessions(raw(rows))
        early=date(2026,11,27);rows=stock(early,early=True);rows.pop(-2)
        with self.assertRaises(data.OptionsEODDataError):data._stock_sessions(raw(rows))

    def test_last_actual_positive_volume_bar_is_used_without_editing_zero_boundary_or_raw_hash(self):
        value=bundle();rows=stock();rows[-2]['close']=19.5;rows[-1]['volume']=0
        replace_body(value['stock'],rows);before=deepcopy(value)
        result=self.normalize(value)
        self.assertEqual(result['snapshot']['underlying_close'],'19.5')
        self.assertEqual(result['snapshot']['stock_sha256'],value['stock']['sha256'])
        self.assertEqual(result['provenance']['stock_source']['last_positive_volume_label'],'2026-10-29 03:59 PM')
        self.assertEqual(result['provenance']['stock_source']['observed_rows'],391)
        self.assertEqual(result['provenance']['stock_source']['zero_volume_rows'],1)
        self.assertIsNone(result['snapshot']['quote_observed_at'])
        self.assertEqual(value,before)
        result['provenance']['session_grid_policy']['id']='caller edit'
        self.assertEqual(data.SESSION_GRID_POLICY['id'],'onclick_regular_source_labels_v2')

    def test_baseline_14_consecutive_original_minute_sessions_and_early_close(self):
        days=[];cursor=DAY
        while len(days)<14:
            if data.is_session(cursor):days.append(cursor)
            cursor-=timedelta(days=1)
        days.reverse();body=raw([row for day in days for row in stock(day)])
        result=data.load_baseline(body,received_at=NOW,through_date=DAY.isoformat())
        self.assertEqual(len(result),14)
        self.assertEqual(result[-1]["source_date"],DAY.isoformat())
        self.assertTrue(all(row["stock_sha256"]==hashlib.sha256(body).hexdigest() for row in result))
        early=date(2026,11,27);received=datetime(2026,11,30,19,tzinfo=timezone.utc)
        self.assertEqual(len(data.load_baseline(raw(stock(early,early=True)),received_at=received,through_date=early)),1)
        self.assertEqual(data.load_baseline(None,received_at=NOW,through_date=DAY),[])
        with self.assertRaises(data.OptionsEODDataError):data.load_baseline(body,received_at=NOW,through_date=date(2026,10,28))
        with self.assertRaises(data.OptionsEODDataError):data.load_baseline(raw(stock(days[0])+stock(days[2])),received_at=NOW,through_date=days[2])

    def test_mixed_390_and_391_baseline_preserves_all_original_bytes_and_no_historical_trades(self):
        days=[];cursor=DAY
        while len(days)<14:
            if data.is_session(cursor):days.append(cursor)
            cursor-=timedelta(days=1)
        days.reverse()
        body=raw([row for index,day in enumerate(days) for row in (stock(day)[:-1] if index in (0,4) else stock(day))])
        before=bytes(body)
        refs=data.load_baseline(body,received_at=NOW,through_date=DAY)
        self.assertEqual(len(refs),14)
        self.assertEqual(body,before)
        self.assertEqual({row['stock_sha256'] for row in refs},{hashlib.sha256(body).hexdigest()})
        self.assertTrue(all(set(row)=={'source_date','underlying_close','received_at','stock_sha256'} for row in refs))

    def test_exact_snapshot_fields_canonical_strings_and_hashes_required(self):
        snapshot=self.normalize()["snapshot"]
        for change in (lambda value:value.update(extra=True),lambda value:value.update(underlying_close="20.0"),
                       lambda value:value.update(partial_chain=1),lambda value:value.update(stock_sha256="x"),
                       lambda value:value["quotes"][0].update(multiplier=100)):
            value=deepcopy(snapshot);change(value)
            with self.assertRaises(data.OptionsEODDataError):data.validate_snapshot(value)

    def test_fixed_url_surface_rejects_credentials_keys_type_strikes_and_adjustment(self):
        for url in ("http://api.onclickmedia.com/options/",data.expiration_url(DAY)+"&apikey=x",
                    data.chain_url(DAY,EXPIRY)+"&type=call",data.chain_url(DAY,EXPIRY)+"&strike=20",
                    data.stock_url(DAY).replace('/v2/','/v2/adj/'),data.stock_url(DAY).replace('extended=false','extended=true'),
                    data.expiration_url(DAY).replace('api.onclickmedia.com','127.0.0.1')):
            with self.subTest(url=url),self.assertRaises(data.OptionsEODDataError):data._approved_url(url)
        for url in (data.expiration_url(DAY),data.chain_url(DAY,EXPIRY),data.stock_url(DAY)):
            self.assertEqual(data._approved_url(url).hostname,data.HOST)

    def test_worker_guard_prevents_network_and_current_live_never_requested(self):
        with patch.dict(os.environ,{"ARIADNE_OPTIONS_EOD_COLLECTOR":"0","ARIADNE_OFFLINE":"1"}),patch.object(data,'_fetch') as fetch:
            with self.assertRaises(data.OptionsEODUnavailable):data.fetch_bundle(DAY)
            fetch.assert_not_called()
        with patch.dict(os.environ,{"ARIADNE_OPTIONS_EOD_COLLECTOR":"1","ARIADNE_OFFLINE":"0"}),patch.object(data,'_now',return_value=NOW),patch.object(data,'_fetch') as fetch:
            with self.assertRaises(data.OptionsEODDataError):data.fetch_bundle(NOW.date())
            fetch.assert_not_called()

    def test_mock_worker_one_metadata_three_expiries_one_stock_shared_budget(self):
        required=["F261106C00020000","F261120P00020000"];source=bundle(required=required)
        mapping={item["url"]:item for item in [source["expirations"],*source["chains"],source["stock"]]}
        calls=[]
        def fetch(url,*,deadline,check,budget):
            check();calls.append((url,deadline,id(budget)));return deepcopy(mapping[url])
        with patch.dict(os.environ,{"ARIADNE_OPTIONS_EOD_COLLECTOR":"1","ARIADNE_OFFLINE":"0"}),patch.object(data,'_now',return_value=NOW),patch.object(data,'_fetch',side_effect=fetch):
            result=data.fetch_bundle(DAY,required)
        self.assertEqual(result,source)
        self.assertEqual(len(calls),5)
        self.assertEqual(len({item[1] for item in calls}),1)
        self.assertEqual(len({item[2] for item in calls}),1)
        self.assertTrue(all('type=' not in item[0] and 'strike=' not in item[0] for item in calls))

    def test_denial_is_substantive_failure_with_raw_evidence_and_no_retry(self):
        source=bundle();count=0
        def fetch(url,**kwargs):
            nonlocal count
            count+=1
            if count==1:return source["expirations"]
            error=data.OptionsEODUnavailable('HTTP403');error.source_evidence={"raw":"retained"};raise error
        with patch.dict(os.environ,{"ARIADNE_OPTIONS_EOD_COLLECTOR":"1","ARIADNE_OFFLINE":"0"}),patch.object(data,'_now',return_value=NOW),patch.object(data,'_fetch',side_effect=fetch):
            with self.assertRaises(data.OptionsEODUnavailable) as caught:data.fetch_bundle(DAY)
        self.assertEqual(count,2)
        self.assertEqual(caught.exception.source_evidence["partial_bundle"]["expirations"],source["expirations"])
        self.assertEqual(caught.exception.source_evidence["failed_response"],{"raw":"retained"})

    def transport(self, response_bytes, *, budget=None, addresses=None):
        class Channel:
            def __init__(self):
                self.closed=False;self.sent=[];self.connected=[];self.stream=io.BytesIO(response_bytes)
            def settimeout(self,value):self.timeout=value
            def connect(self,value):self.connected.append(value)
            def sendall(self,value):self.sent.append(bytes(value))
            def makefile(self,*args,**kwargs):return self.stream
            def shutdown(self,mode):pass
            def close(self):self.closed=True
        raw_socket, tls_socket=Channel(),Channel()
        class Context:
            def wrap_socket(self,channel,*,server_hostname):
                self.host=server_hostname
                return tls_socket
        context=Context();budget=budget if budget is not None else {"requests":0,"remaining":data.MAX_WIRE}
        addresses = addresses if addresses is not None else [(2,1,6,'',('8.8.8.8',443))]
        with patch.object(data.socket,'getaddrinfo',return_value=addresses),patch.object(data.socket,'socket',return_value=raw_socket),patch('ariadne.internet._tls_context',return_value=context):
            try:
                result=data._fetch(data.expiration_url(DAY),deadline=time.monotonic()+5,check=lambda:None,budget=budget)
                return result,raw_socket,tls_socket,context,budget
            finally:
                self.assertTrue(raw_socket.closed or not addresses or any(row[4][0]=='127.0.0.1' for row in addresses))

    def test_pinned_tls_counts_real_http_chunk_framing_headers_and_request(self):
        wire=b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n2\r\n[]\r\n0\r\n\r\n'
        result,raw_socket,tls_socket,context,budget=self.transport(wire)
        self.assertEqual(base64.b64decode(result['content_base64']),b'[]')
        self.assertEqual(context.host,data.HOST)
        self.assertEqual(raw_socket.connected,[('8.8.8.8',443)])
        request=b''.join(tls_socket.sent)
        self.assertEqual(data.MAX_WIRE-budget['remaining'],len(wire)+len(request))
        self.assertEqual(budget['requests'],1)
        self.assertNotIn(b'Authorization',request)
        self.assertNotIn(b'Cookie',request)
        self.assertIn(b'Accept-Encoding: identity',request)
        self.assertTrue(tls_socket.closed)

    def test_transport_denies_redirect_compression_nonjson_length_and_dns(self):
        cases=(b'HTTP/1.1 302 Found\r\nLocation: https://other.example/\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n[]',
               b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: gzip\r\nContent-Length: 2\r\n\r\n[]',
               b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 2\r\n\r\n[]',
               b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 99999999\r\n\r\n[]')
        for wire in cases:
            with self.subTest(wire=wire[:40]),self.assertRaises(data.OptionsEODUnavailable):self.transport(wire)
        with self.assertRaises(data.OptionsEODUnavailable):self.transport(cases[0],addresses=[(2,1,6,'',('127.0.0.1',443))])

    def test_transport_http_wire_budget_and_expired_deadline_stop_without_retry(self):
        wire=b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n[]'
        with self.assertRaises(data.OptionsEODUnavailable):self.transport(wire,budget={'requests':0,'remaining':50})
        with patch.object(data.socket,'getaddrinfo') as dns:
            with self.assertRaises(data.OptionsEODUnavailable):data._fetch(data.expiration_url(DAY),deadline=time.monotonic()-1,check=lambda:None,budget={'requests':0,'remaining':100})
            dns.assert_not_called()


if __name__ == "__main__":
    unittest.main()
