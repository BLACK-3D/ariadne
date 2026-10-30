"""Owner-only options routes and saved-evidence daily brief integration."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store
from ariadne.options_data import canonical
from test_options_recovery import fixture


class NoModel:
    def status(self):return {"available":False,"models":[]}
    def chat(self,*args,**kwargs):raise AssertionError("No options inference")


class OptionsAPITests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/"runtime/tmp";parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="options-api-",dir=parent);self.addCleanup(temporary.cleanup)
        self.settings=Settings(data_dir=Path(temporary.name)/"data",api_token="options-fixture-key")
        self.store=Store(self.settings.data_dir/"ariadne.sqlite3")
        with patch("ariadne.server.Voice",return_value=object()):
            self.app=Application(self.settings,self.store,Engine(self.settings,self.store,NoModel()))
        self.store.set_setting("paused",False)
        self.dataset=fixture(14,historical=True)
        self.clock=datetime.fromisoformat(self.dataset["as_of"])
        self.app.briefing.configure({"enabled":True},self.clock-timedelta(days=1),expected_revision=0)
        timer=patch("ariadne.options_workflow._now",side_effect=lambda value=None:value if value is not None else self.clock)
        timer.start();self.addCleanup(timer.stop)
        self.counter=0

    def request(self,path="/api/options",method="GET",body=None,*,raw=None,auth=True,origin=None,host="localhost:8080"):
        raw=json.dumps(body or {}).encode() if raw is None else raw
        env={"HTTP_HOST":host,"REQUEST_METHOD":method,"PATH_INFO":path,"CONTENT_TYPE":"application/json","CONTENT_LENGTH":str(len(raw)),"wsgi.input":io.BytesIO(raw),"QUERY_STRING":""}
        if auth:env["HTTP_AUTHORIZATION"]="Bearer options-fixture-key"
        if origin:env["HTTP_ORIGIN"]=origin
        response={}
        def start(status,headers):response.update(status=int(status.split()[0]),headers=dict(headers))
        payload=b"".join(self.app(env,start))
        response["body"]=json.loads(payload) if response["headers"]["Content-Type"].startswith("application/json") else payload
        return response

    def action(self,name,extra=None):
        self.counter+=1
        body={"request_id":"options-api-"+str(self.counter),"expected_revision":self.request()["body"]["revision"],**(extra or {})}
        result=self.request("/api/options/"+name,"POST",body)
        self.assertEqual(result["status"],200,result)
        return result["body"]

    def ready(self):
        result=self.request("/api/options/config","PUT",{"enabled":True,"paused":False,"expected_revision":0})
        self.assertEqual(result["status"],200,result)
        path=self.settings.workspace/"imports/options/history.json";path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(canonical(self.dataset))
        self.action("import",{"path":"imports/options/history.json"})
        self.action("backtest")
        self.action("paper",{"acknowledge_simulated":True})

    def enable(self):
        result=self.request("/api/options/config","PUT",{"enabled":True,"paused":False,"expected_revision":0})
        self.assertEqual(result["status"],200,result)

    def catalog(self):
        """Two complete immutable fixture chunks; never actual market evidence."""
        from ariadne.qqq_data import exchange_day
        from ariadne.options_data import clock
        rows=self.dataset["snapshots"]
        manifest={"schema":1,"as_of":self.dataset["as_of"],"evidence_kind":"historical_quotes","symbol":"QQQ",
                  "source":deepcopy(self.dataset["source"]),
                  "selection":{"start":exchange_day(clock(rows[0]["observed_at"])).isoformat(),
                               "end":exchange_day(clock(rows[-1]["observed_at"])).isoformat(),
                               "max_dte":None,"reason":"Fixed synthetic API fixture; no market evidence."},
                  "limitations":["Synthetic regression fixtures are not investment evidence."],"chunks":[]}
        for number,selected in enumerate((rows[:7],rows[7:])):
            value=deepcopy(self.dataset);value["snapshots"]=selected
            raw=canonical(value);relative="imports/options/chunk-"+str(number)+".json"
            path=self.settings.workspace/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            manifest["chunks"].append({"path":relative,"sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw),
                "snapshots":len(selected),"quotes":sum(len(row["quotes"]) for row in selected),
                "first_observed_at":selected[0]["observed_at"],"last_observed_at":selected[-1]["observed_at"]})
        relative="imports/options/catalog.json";raw=canonical(manifest)
        (self.settings.workspace/relative).write_bytes(raw)
        return relative,hashlib.sha256(raw).hexdigest()

    def test_auth_same_origin_and_host_for_all_read_write_routes(self):
        for tail,method in (("","GET"),("/config","PUT"),*(("/"+name,"POST") for name in ("import","refresh","backtest","paper","acknowledge","archive","register-archive","compare","portfolio","upgrade-portfolio"))):
            path="/api/options"+tail
            with self.subTest(path=path):
                self.assertEqual(self.request(path,method,auth=False)["status"],401)
                self.assertEqual(self.request(path,method,origin="https://outside.example")["status"],403)
                self.assertEqual(self.request(path,method,host="outside.example")["status"],403)
        self.assertEqual(self.request()["body"]["revision"],0)

    def test_strict_json_boolean_revision_and_global_pause(self):
        self.assertEqual(self.request("/api/options/config","PUT",raw=b'{"enabled":true,"enabled":false,"paused":false,"expected_revision":0}')["status"],400)
        self.assertEqual(self.request("/api/options/config","PUT",{"enabled":True,"paused":False,"expected_revision":True})["status"],400)
        self.store.set_setting("paused",True)
        for action,extra in (("paper",{"acknowledge_simulated":True}),("refresh",{}),("import",{"path":"imports/options/history.json"}),("backtest",{}),("archive",{"reason":"Paused","archive_data":False}),("register-archive",{"path":"imports/options/catalog.json"}),("compare",{}),("portfolio",{"acknowledge_simulated":True}),("upgrade-portfolio",{"acknowledge_simulated":True})):
            result=self.request("/api/options/"+action,"POST",{"request_id":"held","expected_revision":0,**extra})
            self.assertIn(result["status"],(400,409),result)
        self.assertIsNone(self.request()["body"]["paper"])

    def test_expanded_routes_reject_duplicate_keys_boolean_revisions_and_unknown_funding(self):
        self.enable()
        revision=self.request()["body"]["revision"]
        for action,extra in (("register-archive",{"path":"imports/options/catalog.json"}),("compare",{}),("portfolio",{"acknowledge_simulated":True}),("upgrade-portfolio",{"acknowledge_simulated":True})):
            route="/api/options/"+action
            with self.subTest(action=action):
                duplicate=b'{"request_id":"one","request_id":"two","expected_revision":1}'
                self.assertEqual(self.request(route,"POST",raw=duplicate)["status"],400)
                self.assertEqual(self.request(route,"POST",{"request_id":"bad-revision","expected_revision":True,**extra})["status"],400)
                self.assertEqual(self.request(route,"POST",{"request_id":"unexpected-funding","expected_revision":revision,"funding":"500",**extra})["status"],400)
        self.assertEqual(self.request()["body"]["revision"],revision)
        self.assertIsNone(self.request()["body"]["paper"])

    def test_portfolio_route_starts_one_shared_account_without_broker_or_history_adoption(self):
        self.enable()
        with patch("socket.create_connection",side_effect=AssertionError("No sockets")),patch("subprocess.Popen",side_effect=AssertionError("No process")):
            receipt=self.action("portfolio",{"acknowledge_simulated":True})
            value=self.request()["body"]
        state=value["paper"]["state"];summary=value["paper"]["summary"]
        self.assertEqual(receipt["action"],"portfolio")
        self.assertEqual(state["schema"],2)
        self.assertEqual((summary["equity"],summary["contributions"],summary["monthly_budget_remaining"]),("500","500","500"))
        self.assertEqual(len(summary["strategy_stats"]),3)
        self.assertTrue(all(stats["entry_count"]==0 for stats in summary["strategy_stats"].values()))
        self.assertIsNone(summary["position"]);self.assertIsNone(summary["pending"])
        self.assertFalse(value["brokerage_connected"])
        self.assertEqual(self.store.runs(),[])
        second=self.request("/api/options/portfolio","POST",{"request_id":"second-account","expected_revision":value["revision"],"acknowledge_simulated":True})
        self.assertEqual(second["status"],409)
        self.assertEqual(self.request()["body"]["paper"]["id"],receipt["account_id"])

    def test_pristine_upgrade_route_preserves_account_identity_cash_and_monthly_allowance(self):
        self.enable();self.action("paper",{"acknowledge_simulated":True})
        before=self.request()["body"]["paper"]
        receipt=self.action("upgrade-portfolio",{"acknowledge_simulated":True})
        after=self.request()["body"]["paper"]
        self.assertEqual(after["id"],before["id"])
        self.assertEqual(after["started_at"],before["started_at"])
        self.assertEqual(after["state"]["schema"],2)
        for key in ("cash","contributions","month","monthly_committed","monthly_start_equity"):
            self.assertEqual(after["state"][key],before["state"][key],key)
        self.assertEqual(receipt["simulated_funding_added"],"0")
        self.assertIs(receipt["monthly_budget_reset"],False)
        again=self.request("/api/options/upgrade-portfolio","POST",{"request_id":"repeat-upgrade","expected_revision":self.request()["body"]["revision"],"acknowledge_simulated":True})
        self.assertEqual(again["status"],409)

    def test_archive_comparison_routes_bind_manifest_and_keep_history_out_of_forward_account(self):
        self.enable();self.action("portfolio",{"acknowledge_simulated":True})
        before=self.request()["body"]["paper"]
        path,digest=self.catalog()
        with patch("socket.create_connection",side_effect=AssertionError("No sockets")),patch("subprocess.Popen",side_effect=AssertionError("No process")):
            registered=self.action("register-archive",{"path":path})
            compared=self.action("compare")
            saved=self.request()["body"]
        self.assertEqual(registered["manifest_sha256"],digest)
        self.assertEqual(compared["manifest_sha256"],digest)
        self.assertEqual(saved["archive_catalog"]["quality"]["snapshots"],14)
        self.assertEqual(saved["comparison"]["coverage"]["source_snapshots"],14)
        self.assertEqual(len(saved["comparison"]["strategy_results"]),3)
        self.assertIn("never added together",compared["comparison_scope"])
        self.assertFalse(compared["execution_qualified"])
        self.assertEqual(saved["paper"],before)
        self.assertIsNone(saved["forward_quality"])
        self.assertIsNone(saved["quality"])
        self.assertEqual(self.store.runs(),[])

    def test_brief_contains_forward_strategy_attribution_and_separate_historical_coverage(self):
        self.enable();self.action("portfolio",{"acknowledge_simulated":True})
        path,digest=self.catalog();self.action("register-archive",{"path":path});self.action("compare")
        result=self.app.briefing.run("expanded-options-brief",self.clock,collect=False)
        artifacts={Path(item["path"]).suffix:item for item in result["artifacts"]}
        report=json.loads((self.settings.workspace/artifacts[".json"]["path"]).read_bytes())
        markdown=(self.settings.workspace/artifacts[".md"]["path"]).read_text()
        options=report["options_paper"]
        self.assertEqual(options["comparison"]["manifest_sha256"],digest)
        self.assertEqual(options["comparison"]["coverage"]["source_snapshots"],14)
        self.assertEqual(len(options["paper"]["summary"]["strategy_stats"]),3)
        for strategy in ("recovery_call_v1","breakout_call_v1","breakdown_put_v1"):
            self.assertIn("Forward strategy attribution "+strategy,markdown)
            self.assertIn("Historical "+strategy,markdown)
        self.assertIn("separate counterfactual $500 accounts, never combined",markdown)
        self.assertIn("incomplete ledgers are last dated marks",markdown)
        from ariadne.briefing_summary import compact_summary
        compact=compact_summary(report,self.clock)["options_paper"]
        self.assertTrue(compact["shared_portfolio"])
        self.assertEqual(len(compact["strategy_attribution"]),3)
        self.assertEqual(compact["comparison_coverage"]["source_snapshots"],14)
        self.assertEqual(compact["forward_status"],"waiting_for_recorded_quotes")
        self.assertEqual(compact["trading_pnl"],"0")

    def test_complete_offline_flow_preserves_qqq_service_desk_and_runs(self):
        qqq=self.app.briefing.qqq.status(self.clock);desk=self.app.client_handoff.status();demo=self.app.handoff.status()
        with patch("socket.create_connection",side_effect=AssertionError("No sockets")),patch("subprocess.Popen",side_effect=AssertionError("No process")):
            self.ready();value=self.request()["body"]
        self.assertEqual(value["paper"]["summary"]["equity"],"500")
        self.assertEqual(value["paper"]["summary"]["trading_pnl"],"0")
        self.assertEqual(value["paper"]["summary"]["monthly_budget_remaining"],"500")
        self.assertFalse(value["brokerage_connected"])
        self.assertEqual(self.app.briefing.qqq.status(self.clock),qqq)
        self.assertEqual(self.app.client_handoff.status(),desk);self.assertEqual(self.app.handoff.status(),demo)
        self.assertEqual(self.store.runs(),[])

    def test_brief_json_markdown_poster_and_model_summary_include_options_results(self):
        self.ready()
        result=self.app.briefing.run("options-fixture-brief",self.clock,collect=False)
        artifacts={Path(item["path"]).suffix:item for item in result["artifacts"]}
        report=json.loads((self.settings.workspace/artifacts[".json"]["path"]).read_bytes())
        self.assertEqual(report["options_paper"]["paper"]["summary"]["trading_pnl"],"0")
        self.assertEqual(report["options_paper"]["paper"]["summary"]["contributions"],"500")
        markdown=(self.settings.workspace/artifacts[".md"]["path"]).read_text()
        self.assertIn("Aggressive options: $500 monthly simulated loss budget",markdown)
        self.assertIn("External simulated contributions",markdown)
        self.assertIn("not a trailing drawdown guarantee",markdown)
        self.assertIn("waiting_for_recorded_quotes",markdown)
        from ariadne.brief_poster import poster_view
        self.assertIn("OPTIONS PAPER",poster_view(report,self.clock)["accessible_text"])
        self.assertIn("no trading result yet",poster_view(report,self.clock)["accessible_text"])
        from ariadne.briefing_summary import compact_summary
        compact=compact_summary(report,self.clock)["options_paper"]
        self.assertTrue(compact["simulated_only"])
        self.assertEqual(compact["monthly_cap"],"500.00")
        self.assertEqual(compact["forward_status"],"waiting_for_recorded_quotes")
        self.assertFalse(compact["forward_fresh_at_report_generation"])
        repeated=self.app.briefing.run("options-fixture-brief",self.clock,collect=False)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(repeated["artifacts"],result["artifacts"])

    def test_failed_parent_daily_receipt_holds_options_before_import(self):
        self.ready()
        now=datetime.now(timezone.utc)
        from ariadne.briefing import _tz
        day=now.astimezone(_tz("America/Chicago")).date().isoformat()
        with self.app.briefing.database() as db:
            receipt={"status":"failed","created_at":now.timestamp(),"request_id":"failed-parent","artifacts":[]}
            db.execute("INSERT INTO briefing_runs VALUES(?,?,?,?,?)",("failed-parent",day,"failed",now.timestamp(),json.dumps(receipt)))
        with patch.object(self.app.briefing.options,"refresh",side_effect=AssertionError("Held parent")) as collect:
            self.assertIsNone(self.app.briefing.run_due(now));collect.assert_not_called()

    def test_live_options_owner_defers_parent_before_claiming_daily_receipt(self):
        with patch.object(self.app.briefing.qqq,"run_due",return_value=None),patch.object(self.app.briefing.options,"run_due",return_value=None),patch.object(self.app.briefing.options,"busy",return_value=True):
            self.assertIsNone(self.app.briefing.run_due(self.clock))
        with self.app.briefing.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM briefing_runs").fetchone()[0],0)

    def test_static_assets_bundled_self_only_no_remote_runtime(self):
        for name in ("options.js","options.css"):
            result=self.request("/static/"+name,auth=False)
            self.assertEqual(result["status"],200)
            self.assertEqual(result["body"],(self.app.static/name).read_bytes())
            self.assertIn("script-src 'self'",result["headers"]["Content-Security-Policy"])


if __name__=="__main__":unittest.main()
