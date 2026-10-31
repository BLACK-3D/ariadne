"""Durable daily research/paper guards, not broker execution tests."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ariadne.config import Settings
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.qqq_workflow import QQQWorkflow
from ariadne.handoff import HandoffConflict
from test_qqq_recovery import fixture,through

UTC=timezone.utc

class QQQWorkflowTests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/"runtime/tmp";parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="qqq-workflow-",dir=parent);self.addCleanup(temporary.cleanup)
        self.settings=Settings(data_dir=Path(temporary.name)/"data")
        self.store=Store(self.settings.data_dir/"ariadne.sqlite3")
        self.store.set_setting("paused",False)
        self.store.set_setting("internet_access",{"enabled":True,"revision":1})
        self.registry=ToolRegistry(self.settings)
        self.controls={"enabled":True,"paused":False,"revision":1}
        self.service=QQQWorkflow(self.registry,self.store,controls=lambda:dict(self.controls))
        self.dataset=through(fixture(),90)
        self.clock=datetime.fromisoformat(self.dataset["as_of"])
        self.counter=0

    def body(self,**extra):
        self.counter+=1
        return {"request_id":"operation-"+str(self.counter),"expected_revision":self.service.status(self.clock)["revision"],**extra}

    def enable(self):
        self.service.configure({"enabled":True,"paused":False,"expected_revision":self.service.status(self.clock)["revision"]},self.clock-timedelta(hours=1))

    def bundle(self,dataset=None,**quality):
        return {"dataset":deepcopy(dataset or self.dataset),"quality":{"fresh":True,"source":"fixture",**quality},"raw_payloads":{}}

    def refresh(self,dataset=None,**kwargs):
        data=dataset or self.dataset
        clock=datetime.fromisoformat(data["as_of"])
        self.clock=clock
        return self.service.refresh(self.body(),clock,collector=lambda *args:self.bundle(data),**kwargs)

    def ready(self):
        self.enable();self.refresh()
        report=self.service.backtest(self.body(),self.clock)
        self.assertEqual(report["status"],"completed")
        return report

    def paper(self):
        return self.service.start_paper(self.body(replace=False,acknowledge_simulated=True),self.clock+timedelta(seconds=1))

    def test_passive_status_is_empty_offline_and_does_not_seed(self):
        with patch("socket.create_connection",side_effect=AssertionError("network")),patch("subprocess.Popen",side_effect=AssertionError("worker")):
            value=self.service.status(self.clock)
        self.assertFalse(value["config"]["enabled"]);self.assertIsNone(value["paper"])
        with self.service.database() as db:self.assertFalse(self.service._exists(db))

    def test_full_offline_fixture_path_backtest_cash_start_and_forward_mark(self):
        with patch("socket.create_connection",side_effect=AssertionError("network")),patch("subprocess.Popen",side_effect=AssertionError("worker")):
            report=self.ready();self.paper()
            state=self.service.status(self.clock)["paper"]["state"]
            self.assertEqual(state["cash"],"10000");self.assertEqual(state["shares"],"0");self.assertIsNone(state["pending"])
            result=self.refresh(through(fixture(),91))
            self.assertEqual(result["paper_status"],"completed")
            status=self.service.status(self.clock)
            self.assertEqual(status["events"][0]["type"],"paper_daily_result")
            self.assertEqual(status["events"][0]["day_pnl"],"0")
            self.assertEqual(status["paper"]["state"]["pending"]["action"],"BUY")
            self.refresh(through(fixture(),92))
            status=self.service.status(self.clock)
            self.assertGreater(float(status["paper"]["state"]["shares"]),0)
            self.assertTrue(any(event["type"]=="paper_fill" and event["simulated"] for event in status["events"]))
            self.assertEqual(status["backtest"]["artifacts"],report["artifacts"])

    def test_exact_replay_has_no_new_data_calls_and_revision_is_cas(self):
        self.enable();body=self.body();calls=[]
        result=self.service.refresh(body,self.clock,collector=lambda *args:(calls.append(1) or self.bundle()))
        replay=self.service.refresh(body,self.clock,collector=lambda *args:(_ for _ in ()).throw(AssertionError("replayed fetch")))
        self.assertTrue(replay["replayed"]);self.assertEqual(calls,[1])
        with self.assertRaises(HandoffConflict):self.service.configure({"enabled":True,"paused":False,"expected_revision":body["expected_revision"]},self.clock)

    def test_midfetch_permission_revocation_retains_old_data_and_holds(self):
        self.enable();self.refresh();before=self.service.status(self.clock)["data_sha256"]
        def revoke(*args):
            self.controls.update(paused=True,revision=2)
            return self.bundle()
        body=self.body()
        with self.assertRaises(HandoffConflict):self.service.refresh(body,self.clock,collector=revoke)
        value=self.service.status(self.clock)
        self.assertEqual(value["data_sha256"],before);self.assertEqual(value["hold"]["request_id"],body["request_id"])

    def test_history_revision_fails_and_acknowledgement_does_not_adopt(self):
        self.enable();self.refresh();before=self.service.status(self.clock)["data_sha256"]
        changed=deepcopy(self.dataset);changed["symbols"]["QQQ"]["bars"][1]["high"]=str(float(changed["symbols"]["QQQ"]["bars"][1]["high"])+1)
        body=self.body()
        with self.assertRaisesRegex(ValueError,"history changed"):self.service.refresh(body,self.clock,collector=lambda *args:self.bundle(changed))
        self.assertEqual(self.service.status(self.clock)["data_sha256"],before)
        value=self.service.acknowledge({"request_id":body["request_id"],"expected_revision":self.service.status(self.clock)["revision"]})
        self.assertEqual(value["status"],"acknowledged");self.assertEqual(self.service.status(self.clock)["data_sha256"],before)

    def test_adjclose_only_revisions_do_not_rebase_paper_or_change_economics(self):
        self.ready();self.paper();before=self.service.status(self.clock)["paper"]["state"]
        changed=deepcopy(self.dataset)
        self.clock+=timedelta(seconds=2)
        changed["as_of"]=self.clock.isoformat()
        for entry in changed["symbols"].values():
            entry["source"]["retrieved_at"]=changed["as_of"]
            for row in entry["bars"]:row["adj_close"]=str(float(row["adj_close"])*.999)
        self.service.refresh(self.body(),self.clock,collector=lambda *args:self.bundle(changed))
        self.assertEqual(self.service.status(self.clock)["paper"]["state"],before)

    def test_archive_recovery_preserves_evidence_before_new_experiment(self):
        self.ready();self.paper()
        old=self.service.status(self.clock)["paper"]["id"]
        result=self.service.archive(self.body(reason="Reviewed fixture source-basis reset",archive_data=True),self.clock+timedelta(seconds=2))
        self.assertEqual(result["status"],"completed");self.assertFalse(result["synthetic_liquidation"])
        state=self.service.status(self.clock)
        self.assertIsNone(state["paper"]);self.assertIsNone(state["data_sha256"])
        self.assertTrue((self.settings.workspace/result["artifacts"][0]["path"]).is_file())
        with self.service.database() as db:self.assertIsNotNone(db.execute("SELECT closed_at FROM qqq_accounts WHERE id=?",(old,)).fetchone()[0])

    def test_pause_epoch_blocks_fill_even_after_resume(self):
        self.ready();self.paper();self.refresh(through(fixture(),91))
        self.store.set_setting("paused",True);self.store.set_setting("paused",False)
        data=through(fixture(),92);self.clock=datetime.fromisoformat(data["as_of"])
        with self.assertRaisesRegex(ValueError,"permission epoch"):self.service.refresh(self.body(),self.clock,collector=lambda *args:self.bundle(data))
        self.assertEqual(self.service.status(self.clock)["paper"]["state"]["shares"],"0")

    def test_offline_retains_status_and_makes_no_attempt(self):
        self.ready();self.store.set_setting("internet_access",{"enabled":False,"revision":2})
        with patch.object(self.service,"_collect",side_effect=AssertionError("network")):
            self.assertIsNone(self.service.run_due(self.clock+timedelta(days=1)))
        self.assertIsNotNone(self.service.status(self.clock)["backtest"])

    def test_false_freshness_future_source_and_incomplete_rows_fail_closed(self):
        self.enable()
        changed=deepcopy(self.dataset)
        changed["symbols"]["QQQ"]["source"]["retrieved_at"]=(self.clock+timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):self.service.refresh(self.body(),self.clock,collector=lambda *args:self.bundle(changed))
        self.assertIsNone(self.service.status(self.clock)["data_sha256"])

    def test_failed_artifact_publication_reservation_stays_charged(self):
        self.enable();self.refresh()
        with patch("ariadne.qqq_workflow.publish_bytes",side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):self.service.backtest(self.body(),self.clock)
        with self.service.database() as db:
            self.assertGreater(db.execute("SELECT sum(bytes) FROM qqq_reports").fetchone()[0],0)
        self.assertIsNotNone(self.service.status(self.clock)["hold"])

    def test_changed_backtest_file_blocks_status_and_paper_start(self):
        report=self.ready()
        artifact=self.settings.workspace/report["artifacts"][0]["path"]
        artifact.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError,"changed"):self.service.status(self.clock)

    def test_daily_refresh_claim_is_unique_and_no_autoreplay(self):
        self.enable()
        self.clock=self.clock+timedelta(days=1)
        # Worker behavior separately tested: no source is called when a day is
        # already reserved/completed, even if another daemon invokes run_due.
        with patch.object(self.service,"refresh",return_value={"status":"completed"}) as refresh:
            result=self.service.run_due(self.clock)
            self.assertEqual(result["status"],"completed")
            self.assertEqual(refresh.call_args.kwargs["day"],self.clock.astimezone(__import__('ariadne.briefing',fromlist=['_tz'])._tz('America/Chicago')).date().isoformat())

if __name__=="__main__":unittest.main()
