"""Owner-only comparison and own-business cash evidence routes."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store
from test_options_api import NoModel
from test_revenue_planner import fixture


class RevenueAPITests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/"runtime/tmp";root.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="revenue-api-",dir=root);self.addCleanup(temporary.cleanup)
        self.settings=Settings(data_dir=Path(temporary.name)/"data",api_token="revenue-local-fixture")
        self.store=Store(self.settings.data_dir/"ariadne.sqlite3");self.store.set_setting("paused",False)
        with patch("ariadne.server.Voice",return_value=object()):
            self.app=Application(self.settings,self.store,Engine(self.settings,self.store,NoModel()))
        self.counter=0

    def request(self,path="/api/revenue",method="GET",body=None,*,auth=True,origin=None,host="localhost:8080",raw=None):
        raw=json.dumps(body or {}).encode() if raw is None else raw
        env={"HTTP_HOST":host,"REQUEST_METHOD":method,"PATH_INFO":path,"CONTENT_TYPE":"application/json",
             "CONTENT_LENGTH":str(len(raw)),"wsgi.input":io.BytesIO(raw),"QUERY_STRING":""}
        if auth:env["HTTP_AUTHORIZATION"]="Bearer revenue-local-fixture"
        if origin:env["HTTP_ORIGIN"]=origin
        result={}
        payload=b"".join(self.app(env,lambda status,headers:result.update(status=int(status.split()[0]),headers=dict(headers))))
        result["body"]=json.loads(payload) if result["headers"]["Content-Type"].startswith("application/json") else payload
        return result

    def body(self,**extra):
        self.counter+=1
        return {"request_id":"revenue-api-"+str(self.counter),"expected_revision":self.request()["body"]["revision"],**extra}

    def plan(self):
        body=self.body(scenario=fixture());result=self.request("/api/revenue/plan","POST",body)
        self.assertEqual(result["status"],200,result)
        return body,result["body"]

    def test_auth_same_origin_and_host_guard_all_business_and_review_routes(self):
        for path,method in (("/api/revenue","GET"),("/api/revenue/plans/example","GET"),("/api/options/review","GET"),
                            *(("/api/revenue/"+action,"POST") for action in ("plan","evidence","acknowledge"))):
            for options,code in (({"auth":False},401),({"origin":"https://outside.example"},403),({"host":"outside.example"},403)):
                with self.subTest(path=path,options=options):self.assertEqual(self.request(path,method,**options)["status"],code)

    def test_passive_status_does_not_create_a_business_or_cash_history(self):
        with self.store.connect() as db:before=[tuple(row) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        with patch("socket.socket",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No inference/worker")):
            result=self.request()["body"]
            review=self.request("/api/options/review")
        self.assertEqual(result["scenarios"],[]);self.assertEqual(result["evidence"],[])
        self.assertIsNone(result["selected_scenario_id"])
        self.assertEqual(review["status"],200,review)
        self.assertEqual(review["body"]["status"],"not_started")
        with self.store.connect() as db:self.assertEqual(before,[tuple(row) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")])

    def test_comparison_preserves_original_assumptions_and_has_no_implied_customers(self):
        body,receipt=self.plan();status=self.request()["body"]
        self.assertEqual(status["selected_scenario_id"],body["request_id"])
        self.assertEqual(status["plan"]["status"],"hypothetical_scenario_only")
        self.assertEqual(status["cash_summary"]["evidence_count"],0)
        detail=self.request("/api/revenue/plans/"+body["request_id"])
        self.assertEqual(detail["status"],200,detail)
        self.assertEqual(detail["body"]["scenario"]["label"],body["scenario"]["label"])
        again=self.request("/api/revenue/plan","POST",body)
        self.assertEqual(again["status"],200,again);self.assertTrue(again["body"]["replayed"])
        self.assertEqual(len(self.request()["body"]["scenarios"]),1)

    def test_recorded_sale_is_separate_from_received_cash_and_unknown_profit(self):
        body,_=self.plan();scenario_id=body["request_id"]
        from datetime import datetime,timezone
        for kind,amount in (("sale_reported","100.00"),("payout_received","60.00"),("cost_paid","10.00"),("refund_paid","5.00")):
            event={"scenario_id":scenario_id,"kind":kind,"occurred_at":datetime.now(timezone.utc).isoformat(),"amount":amount,
                   "minutes":None,"units":1 if kind=="sale_reported" else None,"note":"Generated own-business evidence regression fixture","reference_path":None}
            result=self.request("/api/revenue/evidence","POST",self.body(event=event))
            self.assertEqual(result["status"],200,result)
        cash=self.request()["body"]["cash_summary"]
        self.assertEqual(cash["sale_reported"],"100.00")
        self.assertEqual(cash["net_reported_cash"],"45.00")
        self.assertTrue(cash["owner_reported"]);self.assertFalse(cash["independently_verified"])
        self.assertFalse(cash["cash_costs_complete"])
        self.assertEqual(cash["timezone"],"UTC")

    def test_strict_json_and_stale_revision_cannot_override_money_or_selection(self):
        duplicate=b'{"request_id":"a","request_id":"b","expected_revision":0,"scenario":{}}'
        self.assertEqual(self.request("/api/revenue/plan","POST",raw=duplicate)["status"],400)
        body,_=self.plan();before=deepcopy(self.request()["body"])
        stale={**body,"request_id":"stale"}
        self.assertEqual(self.request("/api/revenue/plan","POST",stale)["status"],409)
        self.assertEqual(self.request()["body"]["selected_scenario_id"],before["selected_scenario_id"])

    def test_global_pause_blocks_mutations_without_hiding_completed_reports(self):
        body,_=self.plan();self.store.set_setting("paused",True)
        self.assertEqual(self.request("/api/revenue/plan","POST",self.body(scenario=fixture()))["status"],409)
        self.assertEqual(self.request("/api/revenue/plans/"+body["request_id"])["status"],200)

    def test_new_assets_are_bundled_local_files(self):
        for path in ("/static/revenue.js","/static/revenue.css","/static/options_review.js"):
            result=self.request(path,auth=False);self.assertEqual(result["status"],200,result)
