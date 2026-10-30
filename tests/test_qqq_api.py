"""Owner API and daily-brief integration for simulated QQQ workflows."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import io,json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store
from ariadne.brief_poster import poster_view
from test_qqq_recovery import fixture,through

class NoModel:
    def status(self):return {"available":False,"models":[]}
    def chat(self,*args,**kwargs):raise AssertionError("No inference for paper trading")

class QQQAPITests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/"runtime/tmp";parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="qqq-api-",dir=parent);self.addCleanup(temporary.cleanup)
        self.settings=Settings(data_dir=Path(temporary.name)/"data",api_token="qqq-fixture-key")
        self.store=Store(self.settings.data_dir/"ariadne.sqlite3")
        with patch("ariadne.server.Voice",return_value=object()):
            self.app=Application(self.settings,self.store,Engine(self.settings,self.store,NoModel()))
        self.store.set_setting("paused",False);self.store.set_setting("internet_access",{"enabled":True,"revision":1})
        self.dataset=through(fixture(),90);self.clock=datetime.fromisoformat(self.dataset["as_of"])
        self.app.briefing.configure({"enabled":True},self.clock-timedelta(days=1),expected_revision=0)
        clock=patch("ariadne.qqq_workflow._now",side_effect=lambda value=None:value if value is not None else self.clock)
        clock.start();self.addCleanup(clock.stop)
        collector=patch.object(self.app.briefing.qqq,"_collect",side_effect=lambda *args:{"dataset":deepcopy(self.dataset),"quality":{"fresh":True,"source":"fixture"}})
        collector.start();self.addCleanup(collector.stop)
        self.counter=0

    def request(self,path="/api/qqq",method="GET",body=None,*,raw=None,auth=True,origin=None,host="localhost:8080"):
        raw=json.dumps(body or {}).encode() if raw is None else raw
        env={"HTTP_HOST":host,"REQUEST_METHOD":method,"PATH_INFO":path,"CONTENT_TYPE":"application/json","CONTENT_LENGTH":str(len(raw)),"wsgi.input":io.BytesIO(raw),"QUERY_STRING":""}
        if auth:env["HTTP_AUTHORIZATION"]="Bearer qqq-fixture-key"
        if origin:env["HTTP_ORIGIN"]=origin
        response={}
        def start(status,headers):response.update(status=int(status.split()[0]),headers=dict(headers))
        payload=b"".join(self.app(env,start))
        response["body"]=json.loads(payload) if response["headers"]["Content-Type"].startswith("application/json") else payload
        return response

    def action(self,name,extra=None):
        self.counter+=1
        body={"request_id":"api-paper-"+str(self.counter),"expected_revision":self.request()["body"]["revision"],**(extra or {})}
        response=self.request("/api/qqq/"+name,"POST",body)
        self.assertEqual(response["status"],200,response)
        return response["body"]

    def ready(self):
        response=self.request("/api/qqq/config","PUT",{"enabled":True,"paused":False,"expected_revision":0})
        self.assertEqual(response["status"],200,response)
        self.action("refresh");self.action("backtest")
        self.action("paper",{"replace":False,"acknowledge_simulated":True})

    def test_all_routes_require_owner_same_origin_and_host(self):
        routes=[("","GET"),("/config","PUT"),*(('/'+name,'POST') for name in ('refresh','backtest','paper','acknowledge','archive'))]
        for tail,method in routes:
            path="/api/qqq"+tail
            self.assertEqual(self.request(path,method,auth=False)["status"],401)
            self.assertEqual(self.request(path,method,origin="https://outside.example")["status"],403)
            self.assertEqual(self.request(path,method,host="outside.example")["status"],403)
        self.assertEqual(self.request()["body"]["revision"],0)

    def test_strict_json_and_global_pause_block_every_mutation(self):
        self.assertEqual(self.request("/api/qqq/config","PUT",raw=b'{"enabled":true,"enabled":false,"paused":false,"expected_revision":0}')["status"],400)
        self.assertEqual(self.request("/api/qqq/config","PUT",{"enabled":True,"paused":False,"expected_revision":True})["status"],400)
        self.store.set_setting("paused",True)
        for name,method in (("config","PUT"),("refresh","POST"),("backtest","POST"),("paper","POST"),("archive","POST")):
            response=self.request("/api/qqq/"+name,method,{"enabled":True,"paused":False,"expected_revision":0} if name=="config" else {"request_id":"held","expected_revision":0,**({"replace":False,"acknowledge_simulated":True} if name=="paper" else {"reason":"held","archive_data":False} if name=="archive" else {})})
            self.assertIn(response["status"],(400,409),response)
        self.assertTrue(self.request()["body"]["config"]["enabled"] is False)

    def test_complete_owner_api_path_does_not_touch_demo_ledger_or_inference(self):
        demo=self.app.handoff.status();clients=self.app.client_handoff.status()
        with patch("socket.create_connection",side_effect=AssertionError("No sockets")),patch("subprocess.Popen",side_effect=AssertionError("No native worker")):
            self.ready();value=self.request()["body"]
        self.assertEqual(value["paper"]["state"]["cash"],"10000")
        self.assertIsNone(value["paper"]["state"]["pending"])
        self.assertFalse(value["brokerage_connected"])
        self.assertEqual(self.app.handoff.status(),demo);self.assertEqual(self.app.client_handoff.status(),clients)
        with self.app.engine.tools.business.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM journals").fetchone()[0],0)
        self.assertEqual(self.store.runs(),[])

    def test_new_brief_contains_dated_paper_state_and_poster_result(self):
        self.ready()
        result=self.app.briefing.run("fixture-paper-brief",self.clock,collect=False)
        file=next(item for item in result["artifacts"] if item["path"].endswith("brief.json"))
        report=json.loads((self.settings.workspace/file["path"]).read_bytes())
        self.assertEqual(report["qqq_paper"]["paper"]["state"]["equity"],"10000")
        markup=(self.settings.workspace/next(item["path"] for item in result["artifacts"] if item["path"].endswith("brief.md"))).read_text()
        self.assertIn("QQQ recovery: forward paper account",markup)
        self.assertIn("SIMULATED ONLY",markup)
        self.assertIn("QQQ PAPER",poster_view(report,self.clock)["accessible_text"])
        from ariadne.briefing_summary import compact_summary
        self.assertTrue(compact_summary(report,self.clock)["qqq_paper"]["simulated_only"])
        self.assertEqual(compact_summary(report,self.clock)["qqq_paper"]["session"],self.dataset["symbols"]["QQQ"]["bars"][-1]["date"])
        again=self.app.briefing.run("fixture-paper-brief",self.clock,collect=False)
        self.assertTrue(again["replayed"]);self.assertEqual(again["artifacts"],result["artifacts"])

    def test_archive_explicitly_preserves_old_simulation_without_a_sale(self):
        self.ready()
        self.request("/api/qqq/config","PUT",{"enabled":False,"paused":True,"expected_revision":self.request()["body"]["revision"]})
        result=self.action("archive",{"reason":"Fixture close, no market action","archive_data":True})
        self.assertFalse(result["synthetic_liquidation"])
        self.assertIsNone(self.request()["body"]["paper"])
        self.assertTrue((self.settings.workspace/result["artifacts"][0]["path"]).is_file())

    def test_qqq_static_assets_are_bundled_with_self_only_csp(self):
        for name in ("qqq.js","qqq.css"):
            response=self.request("/static/"+name,auth=False)
            self.assertEqual(response["status"],200)
            self.assertEqual(response["body"],(self.app.static/name).read_bytes())
            self.assertIn("script-src 'self'",response["headers"]["Content-Security-Policy"])

    def test_failed_daily_brief_holds_market_before_any_collection(self):
        self.ready()
        now=datetime.now(timezone.utc)
        from ariadne.briefing import _tz
        day=now.astimezone(_tz("America/Chicago")).date().isoformat()
        with self.app.briefing.database() as db:
            receipt={"status":"failed","created_at":now.timestamp(),"request_id":"failed-parent","artifacts":[]}
            db.execute("INSERT INTO briefing_runs VALUES(?,?,?,?,?)",("failed-parent",day,"failed",now.timestamp(),json.dumps(receipt)))
        with patch.object(self.app.briefing.qqq,"refresh",side_effect=AssertionError("held parent must not advance market")) as refresh:
            self.assertIsNone(self.app.briefing.run_due(now))
            refresh.assert_not_called()
        self.assertTrue(self.app.briefing.config()["paused"])

    def test_another_market_owner_defers_parent_claim_without_replaying_it(self):
        with patch.object(self.app.briefing.qqq,"run_due",return_value=None),patch.object(self.app.briefing.qqq,"busy",return_value=True):
            self.assertIsNone(self.app.briefing.run_due(self.clock))
        with self.app.briefing.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM briefing_runs").fetchone()[0],0)

    def test_orphaned_market_receipt_does_not_suppress_weather_brief(self):
        self.ready()
        qqq=self.app.briefing.qqq
        with qqq.database() as db:
            receipt={"request_id":"orphaned-market","status":"running","artifacts":[]}
            db.execute("INSERT INTO qqq_runs VALUES(?,?,?,?,?,?,?)",("orphaned-market","refresh",None,"a"*64,"running",json.dumps(receipt),len(json.dumps(receipt))))
        self.assertFalse(qqq.busy())
        result=self.app.briefing.run_due(self.clock)
        self.assertEqual(result["status"],"completed")
        self.assertEqual(self.request()["body"]["hold"]["request_id"],"orphaned-market")

    def test_live_market_owner_is_busy_even_before_reserving_a_run(self):
        self.ready()
        from ariadne.locking import InstanceLock
        lock=InstanceLock(self.settings.data_dir/"qqq-workflow.lock");lock.acquire()
        try:self.assertTrue(self.app.briefing.qqq.busy())
        finally:lock.release()

if __name__=="__main__":unittest.main()
