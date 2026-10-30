"""Exact documented quote adaptation and the narrow anonymous network boundary."""
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import socket
import time
import unittest
from unittest.mock import patch

from ariadne.options_data import load_dataset
from ariadne.options_feed import (SOURCE_URL, MAX_RAW, OptionsFeedError, OptionsFeedUnavailable,
    NORMALIZATION_POLICY, NORMALIZATION_SHA256, normalize_chain, required_ids, source_url, collect_bundle, _fetch)

UTC=timezone.utc
NOW=datetime(2026,10,30,19,tzinfo=UTC)
DAY=date(2026,10,29)


def fixture():
    return {"s":"ok", "optionSymbol":["AAPL261113C00100000","AAPL261113P00100000"],
        "underlying":["AAPL"]*2,"expiration":[1794603600]*2,"side":["call","put"],"strike":[100]*2,
        "updated":[1793304000]*2,"bid":[0,Decimal("1.02")],"ask":[Decimal("1.10"),Decimal("1.05")],
        "bidSize":[0,10],"askSize":[7,8],"underlyingPrice":[100]*2,"volume":[0,12],"openInterest":[None,33],
        "iv":[None,None]}


def payload(value):
    # Source fixture values deliberately serialize as JSON numeric tokens.
    def encode(item):
        if type(item) is Decimal:return float(item)
        raise TypeError
    return json.dumps(value,default=encode,allow_nan=False,separators=(",",":")).encode()


def response(raw=None,status=203):
    return {"payload":payload(fixture()) if raw is None else raw,"status":status,"url":source_url(DAY),"content_type":"application/json; charset=utf-8"}


class FakeSocket:
    def __init__(self):self.connections=[];self.closed=False
    def settimeout(self,value):self.timeout=value
    def connect(self,address):self.connections.append(address)
    def close(self):self.closed=True


class FakeResponse:
    def __init__(self,*,status=203,body=b'{}',headers=None):
        self.status=status;self.body=io.BytesIO(body);self.closed=False
        self.headers={"Content-Type":"application/json",**(headers or {})}
    def getheader(self,name,default=None):return self.headers.get(name,default)
    def read1(self,count):return self.body.read(count)
    def close(self):self.closed=True


class FakeConnection:
    def __init__(self,response):self.response=response;self.requests=[];self.closed=False
    def request(self,*args,**kwargs):self.requests.append((args,kwargs))
    def getresponse(self):return self.response
    def close(self):self.closed=True


class OptionsFeedTests(unittest.TestCase):
    def parse(self,value=None,**kwargs):
        return normalize_chain(payload(fixture() if value is None else value),retrieved_at=NOW,**kwargs)

    def test_actual_snapshot_receipt_provenance_are_separate_and_exact(self):
        raw=payload(fixture());value=normalize_chain(raw,retrieved_at=NOW)
        dataset=value["dataset"];row=dataset["snapshots"][0]
        self.assertEqual(row["observed_at"],"2026-10-29T20:00:00+00:00")
        self.assertEqual(row["available_at"],NOW.isoformat())
        self.assertEqual(dataset["source"]["retrieved_at"],NOW.isoformat())
        self.assertEqual(dataset["source"]["sha256"],hashlib.sha256(raw).hexdigest())
        self.assertEqual(dataset["source"]["url"],SOURCE_URL)
        self.assertEqual(dataset["source"]["quote_time_basis"],"recorded")
        self.assertFalse(dataset["source"]["independently_verified"])
        self.assertEqual(load_dataset(json.dumps(dataset).encode(),NOW),dataset)

    def test_unrelated_decimal_context_cannot_change_contract_terms_or_strike_scope(self):
        expected=self.parse()
        with localcontext() as context:
            context.prec=2
            self.assertEqual(self.parse(),expected)
            self.assertEqual(required_ids(["AAPL261113P00100000"]),["AAPL261113P00100000"])

    def test_zero_bid_zero_size_and_missing_counts_stay_visible(self):
        value=self.parse();quotes=value["dataset"]["snapshots"][0]["quotes"]
        self.assertEqual(quotes[0]["bid"],"0");self.assertEqual(quotes[0]["bid_size"],0)
        self.assertIsNone(quotes[0]["open_interest"])
        self.assertEqual(value["provenance"]["zero_bid_quotes_retained"],1)
        self.assertEqual(quotes[1]["bid"],"1.02")

    def test_fixed_scope_preserves_required_contract_outside_strike_band(self):
        value=fixture();value["optionSymbol"][1]="AAPL261113P00120000";value["strike"][1]=120
        regular=self.parse(value);self.assertEqual(regular["provenance"]["selected_rows"],1)
        self.assertEqual(regular["provenance"]["excluded"]["outside_price_band"],1)
        held=self.parse(value,required_contracts=["AAPL261113P00120000"])
        self.assertEqual(held["provenance"]["selected_rows"],2)
        with self.assertRaises(OptionsFeedUnavailable):self.parse(required_contracts=["AAPL261113C00101000"])

    def test_invalid_prices_and_crossed_economics_are_never_repaired(self):
        for field,item in (("bid",True),("ask",None),("bid","1.02"),("bid",-1),("bid",101)):
            value=fixture();value[field][1]=item
            with self.subTest(field=field,item=item),self.assertRaises(ValueError):self.parse(value)

    def test_crossed_nonrequired_quotes_are_explicit_unavailable_quarantine_not_repairs(self):
        value=fixture();value["ask"][1]=Decimal("0.10");raw=payload(value)
        result=normalize_chain(raw,retrieved_at=NOW)
        quotes=result["dataset"]["snapshots"][0]["quotes"]
        self.assertEqual([row["contract_id"] for row in quotes],["AAPL261113C00100000"])
        self.assertEqual(result["provenance"]["quarantined_rows"],1)
        self.assertEqual(result["provenance"]["quarantined"],[{"contract_id":"AAPL261113P00100000","bid":"1.02","ask":"0.1","reason":"crossed_bid_greater_than_ask"}])
        self.assertEqual(payload(value),raw)
        self.assertEqual(result["dataset"]["source"]["sha256"],hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["provenance"]["normalization_policy"],NORMALIZATION_POLICY)
        self.assertEqual(result["provenance"]["normalization_policy_sha256"],NORMALIZATION_SHA256)
        self.assertIn(NORMALIZATION_SHA256,result["dataset"]["source"]["usage"])
        self.assertEqual(quotes[0]["bid"],"0");self.assertEqual(quotes[0]["bid_size"],0)

    def test_crossed_held_or_pending_contract_blocks_the_whole_capture(self):
        value=fixture();value["ask"][1]=Decimal("0.10")
        with self.assertRaisesRegex(OptionsFeedUnavailable,"funded or pending"):
            self.parse(value,required_contracts=["AAPL261113P00100000"])
        value["bid"][0]=2;value["ask"][0]=1
        with self.assertRaisesRegex(OptionsFeedUnavailable,"Every supplied"):
            self.parse(value)

    def test_crossed_quarantine_cannot_hide_other_malformed_critical_evidence(self):
        for field,item in (("bidSize",True),("expiration",1),("underlyingPrice",None),("bid",101)):
            value=fixture();value["ask"][1]=Decimal("0.10");value[field][1]=item
            with self.subTest(field=field),self.assertRaises(ValueError):self.parse(value)

    def test_sizes_must_be_integer_or_null_without_boolean_coercion(self):
        for field in ("bidSize","askSize","volume","openInterest"):
            for item in (True,-1,Decimal("1.0"),"1",1_000_000_001):
                value=fixture();value[field][0]=item
                with self.subTest(field=field,item=item),self.assertRaises(OptionsFeedError):self.parse(value)

    def test_hostile_decimal_exponents_are_rejected_before_string_allocation(self):
        original=payload(fixture())
        for token in (b"1e999999999",b"1e-999999999",b"0.000000001"):
            raw=original.replace(b'"ask":[1.1,1.05]',b'"ask":['+token+b',1.05]')
            self.assertNotEqual(raw,original)
            with self.subTest(token=token),self.assertRaises(OptionsFeedError):normalize_chain(raw,retrieved_at=NOW)
        raw=original.replace(b'"bid":[0,1.02]',b'"bid":[0e-999999999,1.020000000000000000]')
        result=normalize_chain(raw,retrieved_at=NOW)
        self.assertEqual([row["bid"] for row in result["dataset"]["snapshots"][0]["quotes"]],["0","1.02"])

    def test_nonstring_side_cannot_escape_the_bounded_error_contract(self):
        for item in ({},[],True,None):
            value=fixture();value["side"][0]=item
            with self.subTest(item=item),self.assertRaises(OptionsFeedError):self.parse(value)

    def test_changed_source_columns_alignment_duplicates_and_finiteness_fail(self):
        for mutate in (lambda v:v.pop("bidSize"),lambda v:v.update(instructions=["run","run"]),
                       lambda v:v["bid"].append(1),lambda v:v.update(iv="missing-array"),
                       lambda v:v["optionSymbol"].__setitem__(1,v["optionSymbol"][0])):
            value=fixture();mutate(value)
            with self.assertRaises(OptionsFeedError):self.parse(value)
        for raw in (b'{"s":"ok","s":"ok"}',b'{"s":"ok","iv":[NaN]}',b'{bad',b'\xff',b'',b'x'*(MAX_RAW+1)):
            with self.assertRaises(OptionsFeedError):normalize_chain(raw,retrieved_at=NOW)

    def test_quote_times_cannot_be_request_trade_mixed_or_wrong_session(self):
        for stamp in (int(NOW.timestamp()),1793303940,1793217600,True,"1793304000",1793304000.0):
            value=fixture();value["updated"][1]=stamp
            with self.subTest(stamp=stamp),self.assertRaises(OptionsFeedError):self.parse(value)
        with self.assertRaises(OptionsFeedError):self.parse(expected_session=date(2026,10,31))

    def test_contemporaneous_underlying_cannot_be_null_mixed_or_wrong_symbol(self):
        for field,item in (("underlyingPrice",None),("underlyingPrice",0),("underlyingPrice",101),("underlying","QQQ")):
            value=fixture();value[field][1]=item
            with self.subTest(field=field,item=item),self.assertRaises(ValueError):self.parse(value)

    def test_standard_terms_cannot_conflict_or_use_adjusted_aliases(self):
        for field,item in (("optionSymbol","AAPL1261113C00100000"),("strike",101),("side","put"),("expiration",1794603600+3600)):
            value=fixture();value[field][0]=item
            with self.subTest(field=field,item=item),self.assertRaises(OptionsFeedError):self.parse(value)

    def test_required_ids_are_fixed_bounded_unique_aapl_only(self):
        self.assertEqual(required_ids(["AAPL261113P00100000","AAPL261113C00100000"]),["AAPL261113C00100000","AAPL261113P00100000"])
        for values in ("AAPL261113P00100000",["QQQ261113P00100000"],["AAPL261113P00100000"]*2,["AAPL261113P00100000"]*3,[None]):
            with self.assertRaises(OptionsFeedError):required_ids(values)

    def test_fixed_url_never_accepts_arbitrary_symbol_host_or_query(self):
        value=source_url(DAY)
        self.assertEqual(value,SOURCE_URL+"?date=2026-10-29&expiration=all&from=2026-10-29&to=2026-12-03&nonstandard=false")
        for bad in ("2026-10-29",NOW,False,date(2026,10,31)):
            with self.assertRaises(ValueError):source_url(bad)
        with self.assertRaises(ValueError):_fetch("https://127.0.0.1/",deadline=1,check=lambda:None)

    def test_collector_one_203_request_bound_raw_and_no_model_or_file_write(self):
        calls=[]
        with patch("socket.getaddrinfo",side_effect=AssertionError("Injected transport, no DNS")),patch("builtins.open",side_effect=AssertionError("Pure collector does not publish")):
            value=collect_bundle(NOW,fetch=lambda url:(calls.append(url) or response()))
        self.assertEqual(calls,[source_url(DAY)])
        self.assertEqual(value["network_requests"],1)
        self.assertEqual(base64.b64decode(value["raw"]["content_base64"]),payload(fixture()))
        self.assertEqual(value["raw"]["http_status"],203)
        self.assertEqual(value["raw"]["sha256"],value["dataset"]["source"]["sha256"])

    def test_collector_200_allowed_but_restrictions_do_not_retry(self):
        for status in (200,203):self.assertEqual(collect_bundle(NOW,fetch=lambda _url:response(status=status))["schema"],1)
        for status in (204,301,302,401,402,403,429,500,True):
            calls=[]
            with self.subTest(status=status),self.assertRaises(OptionsFeedUnavailable):
                collect_bundle(NOW,fetch=lambda url:(calls.append(url) or response(status=status)))
            self.assertEqual(len(calls),1)

    def test_failed_required_quote_keeps_exact_body_and_receipt_without_dataset(self):
        source=fixture();source["ask"][1]=Decimal("0.10");raw=payload(source)
        with self.assertRaises(OptionsFeedUnavailable) as caught:
            collect_bundle(NOW,fetch=lambda _:response(raw),required_contracts=["AAPL261113P00100000"])
        evidence=caught.exception.source_evidence
        self.assertEqual(base64.b64decode(evidence["content_base64"],validate=True),raw)
        self.assertEqual(evidence["sha256"],hashlib.sha256(raw).hexdigest())
        self.assertEqual(evidence["retrieved_at"],NOW.isoformat())
        self.assertEqual(evidence["http_status"],203)
        self.assertFalse(hasattr(caught.exception,"dataset"))

    def test_changed_transport_url_mime_or_schema_fails(self):
        for mutate in (lambda v:v.update(url="https://example.com/"),lambda v:v.update(content_type="text/html"),lambda v:v.update(cookies="none")):
            value=response();mutate(value)
            with self.assertRaises(OptionsFeedUnavailable):collect_bundle(NOW,fetch=lambda _:value)

    def test_revocation_prevents_network_and_postfetch_adoption(self):
        calls=[]
        def denied():raise ValueError("Revoked")
        with self.assertRaisesRegex(ValueError,"Revoked"):collect_bundle(NOW,check=denied,fetch=lambda u:calls.append(u))
        self.assertEqual(calls,[])
        checks=[]
        def after():
            checks.append(1)
            if len(checks)==2:raise ValueError("Revoked after fetch")
        with self.assertRaisesRegex(ValueError,"after fetch"):collect_bundle(NOW,check=after,fetch=lambda _:response())

    def test_native_collection_requires_explicit_child_environment(self):
        for setting in ({},{"ARIADNE_OPTIONS_COLLECTOR":"1","ARIADNE_OFFLINE":"1"}):
            with patch.dict(os.environ,setting,clear=True),patch("socket.getaddrinfo",side_effect=AssertionError("No DNS")):
                with self.assertRaises(OptionsFeedUnavailable):collect_bundle(NOW)

    def test_feed_errors_and_no_data_are_not_successful_quotes(self):
        for value in ({"s":"no_data"},{"s":"error","errmsg":"Sign up"},[],True,None):
            with self.assertRaises(OptionsFeedUnavailable):normalize_chain(payload(value),retrieved_at=NOW)

    def test_early_close_source_timestamp_is_not_silently_moved(self):
        value=fixture();value["updated"]=[int(datetime(2026,11,27,21,tzinfo=UTC).timestamp())]*2
        with self.assertRaises(OptionsFeedError):normalize_chain(payload(value),retrieved_at=datetime(2026,11,30,16,tzinfo=UTC),expected_session=date(2026,11,27))

    def test_reviewed_early_close_expiration_is_a_valid_standard_term(self):
        value=fixture();value["optionSymbol"]=["AAPL261127C00100000","AAPL261127P00100000"]
        value["expiration"]=[int(datetime(2026,11,27,18,tzinfo=UTC).timestamp())]*2
        result=self.parse(value)
        self.assertEqual(result["provenance"]["selected_rows"],2)
        self.assertEqual(result["dataset"]["snapshots"][0]["quotes"][0]["expiry"],"2026-11-27")

    def test_worker_refuses_missing_authorization_before_store_or_network(self):
        path=Path(__file__).resolve().parents[1]/"scripts/options_feed.py"
        spec=importlib.util.spec_from_file_location("options_feed_test_worker",path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        argv=[str(path),"worker","--data-dir","reserved-outside","--workspace","reserved-outside","--options-revision","1","--feed-revision","1","--internet-revision","1","--brief-revision","1"]
        with patch("sys.argv",argv),patch.dict(os.environ,{},clear=True),patch("socket.getaddrinfo",side_effect=AssertionError("No DNS")):
            with self.assertRaises(SystemExit):module.main()

    def test_worker_confines_paths_before_allowed_path_or_store_access(self):
        root=Path(__file__).resolve().parents[1];path=root/"scripts/options_feed.py"
        spec=importlib.util.spec_from_file_location("options_feed_path_test_worker",path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        outside=str(root.parent/"outside-options-worker")
        argv=[str(path),"worker","--data-dir",outside,"--workspace",outside,"--options-revision","1","--feed-revision","1","--internet-revision","1","--brief-revision","1"]
        with patch("sys.argv",argv),patch.dict(os.environ,{"ARIADNE_OPTIONS_COLLECTOR":"1","ARIADNE_OFFLINE":"0"}),patch("ariadne.network_policy.install_network_policy"),patch("ariadne.config._allowed_path",side_effect=AssertionError("Outside path must be refused before access")),patch("ariadne.store.Store",side_effect=AssertionError("No store")):
            with self.assertRaisesRegex(ValueError,"inside this box"):module.main()

    def test_worker_rechecks_all_four_permission_epochs_and_only_serializes_result(self):
        root=Path(__file__).resolve().parents[1];path=root/"scripts/options_feed.py"
        spec=importlib.util.spec_from_file_location("options_feed_policy_test_worker",path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        epochs={"options":1,"feed":2,"internet":3,"brief":4};calls=[]
        @contextmanager
        def database():yield object()
        def policy(db,*,expected_feed_revision):
            self.assertEqual(expected_feed_revision,epochs["feed"]);calls.append("policy")
            return {"revision":epochs["options"]},{"revision":epochs["feed"]},{"enabled":True,"revision":epochs["internet"]},{"revision":epochs["brief"]}
        service=SimpleNamespace(options=SimpleNamespace(database=database,feed=SimpleNamespace(_policy=policy)))
        settings=SimpleNamespace(data_dir=root/"runtime/tmp",workspace=root/"runtime/tmp")
        output=io.BytesIO()
        argv=[str(path),"worker","--data-dir",str(root/"runtime/tmp"),"--workspace",str(root/"runtime/tmp"),"--options-revision","1","--feed-revision","2","--internet-revision","3","--brief-revision","4"]
        def collect(*,check,required_contracts):check();return {"schema":1,"network_requests":1}
        with patch("sys.argv",argv),patch("sys.stdout",SimpleNamespace(buffer=output)),patch.dict(os.environ,{"ARIADNE_OPTIONS_COLLECTOR":"1","ARIADNE_OFFLINE":"0"}),patch("ariadne.network_policy.install_network_policy"),patch("ariadne.config.Settings",return_value=settings),patch("ariadne.config._allowed_path",side_effect=lambda value:value),patch("ariadne.store.Store",return_value=object()),patch("ariadne.tools.ToolRegistry",return_value=object()),patch("ariadne.briefing.BriefingService",return_value=service),patch("ariadne.options_feed.collect_bundle",side_effect=collect):
            module.main()
        self.assertEqual(json.loads(output.getvalue()),{"schema":1,"network_requests":1})
        self.assertEqual(calls,["policy"]*3)

    def test_native_transport_pins_numeric_dns_under_hostname_tls_without_auth(self):
        for status in (200,203):
            raw=FakeSocket();reply=FakeResponse(status=status,body=b'{"s":"ok"}')
            connection=FakeConnection(reply);tls=SimpleNamespace(wrap_socket=lambda sock,server_hostname:(self.assertEqual(server_hostname,"api.marketdata.app") or sock))
            addresses=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("1.1.1.1",443))]
            with patch("ariadne.options_feed.socket.getaddrinfo",return_value=addresses),patch("ariadne.options_feed.socket.socket",return_value=raw),patch("ariadne.internet._tls_context",return_value=tls),patch("ariadne.options_feed.http.client.HTTPSConnection",return_value=connection):
                result=_fetch(source_url(DAY),deadline=time.monotonic()+15,check=lambda:None)
            self.assertEqual(result["status"],status)
            self.assertEqual(raw.connections,[("1.1.1.1",443)])
            self.assertEqual(len(connection.requests),1)
            method,request_path=connection.requests[0][0]
            self.assertEqual(method,"GET");self.assertTrue(request_path.startswith("/v1/options/chain/AAPL/?date="))
            headers=connection.requests[0][1]["headers"]
            self.assertEqual(headers["Accept-Encoding"],"identity")
            self.assertFalse(any(key.lower() in {"authorization","cookie","proxy-authorization"} for key in headers))
            self.assertTrue(raw.closed and connection.closed and reply.closed)

    def test_native_transport_rejects_redirect_auth_rate_and_compression_without_retry(self):
        for status,headers in ((301,{"Location":"https://evil.invalid/"}),(302,{}),(401,{}),(402,{}),(403,{}),(429,{}),(500,{}),(200,{"Content-Encoding":"gzip"}),(203,{"Content-Type":"text/html"}),(200,{"Content-Length":str(MAX_RAW+1)})):
            raw=FakeSocket();reply=FakeResponse(status=status,headers=headers);connection=FakeConnection(reply)
            tls=SimpleNamespace(wrap_socket=lambda sock,server_hostname:sock)
            with patch("ariadne.options_feed.socket.getaddrinfo",return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("1.1.1.1",443))]),patch("ariadne.options_feed.socket.socket",return_value=raw),patch("ariadne.internet._tls_context",return_value=tls),patch("ariadne.options_feed.http.client.HTTPSConnection",return_value=connection):
                with self.subTest(status=status,headers=headers),self.assertRaises(OptionsFeedUnavailable):_fetch(source_url(DAY),deadline=time.monotonic()+15,check=lambda:None)
            self.assertEqual(len(connection.requests),1)
            self.assertTrue(raw.closed and connection.closed and reply.closed)

    def test_native_transport_rejects_mixed_or_private_dns_before_connect(self):
        public=(socket.AF_INET,socket.SOCK_STREAM,6,"",("1.1.1.1",443))
        private=(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))
        for addresses in ([],[private],[public,private]):
            with patch("ariadne.options_feed.socket.getaddrinfo",return_value=addresses),patch("ariadne.options_feed.socket.socket") as create:
                with self.assertRaises(OptionsFeedUnavailable):_fetch(source_url(DAY),deadline=time.monotonic()+15,check=lambda:None)
            create.assert_not_called()

    def test_native_transport_deadline_and_body_bound_close_owned_resources(self):
        raw=FakeSocket();reply=FakeResponse(body=b"12345678901");connection=FakeConnection(reply)
        tls=SimpleNamespace(wrap_socket=lambda sock,server_hostname:sock)
        with patch("ariadne.options_feed.MAX_RAW",10),patch("ariadne.options_feed.socket.getaddrinfo",return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("1.1.1.1",443))]),patch("ariadne.options_feed.socket.socket",return_value=raw),patch("ariadne.internet._tls_context",return_value=tls),patch("ariadne.options_feed.http.client.HTTPSConnection",return_value=connection):
            with self.assertRaises(OptionsFeedUnavailable):_fetch(source_url(DAY),deadline=time.monotonic()+15,check=lambda:None)
        self.assertTrue(raw.closed and connection.closed and reply.closed)
        with patch("ariadne.options_feed.socket.getaddrinfo",side_effect=AssertionError("Expired deadline cannot look up DNS")):
            with self.assertRaises(OptionsFeedUnavailable):_fetch(source_url(DAY),deadline=time.monotonic()-1,check=lambda:None)


if __name__=="__main__":unittest.main()
