"""Durable authority, evidence separation and cross-experiment risk controls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.handoff import HandoffConflict
from ariadne.options_data import canonical
from ariadne.options_workflow import OptionsWorkflow, INBOX
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from test_options_recovery import fixture, through, paper_to


class OptionsWorkflowTests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/"runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="options-workflow-",dir=parent)
        self.addCleanup(temporary.cleanup)
        self.settings=Settings(data_dir=Path(temporary.name)/"data")
        self.store=Store(self.settings.data_dir/"ariadne.sqlite3")
        self.store.set_setting("paused",False)
        self.registry=ToolRegistry(self.settings)
        self.controls={"enabled":True,"paused":False,"revision":1}
        self.service=OptionsWorkflow(self.registry,self.store,controls=lambda:dict(self.controls))
        self.dataset=fixture(14,historical=True)
        self.clock=datetime.fromisoformat(self.dataset["as_of"])
        self.counter=0

    def body(self,**extra):
        self.counter+=1
        return {"request_id":"options-test-"+str(self.counter),"expected_revision":self.service.status(self.clock)["revision"],**extra}

    def enable(self):
        self.service.configure({"enabled":True,"paused":False,"expected_revision":0},self.clock-timedelta(days=1))

    def write(self,value=None,path="imports/options/history.json"):
        destination=self.registry._path(path)
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(canonical(value or self.dataset))
        return path

    def imported(self):
        self.enable()
        return self.service.import_data(self.body(path=self.write()),self.clock)

    def paper(self):
        return self.service.start_paper(self.body(acknowledge_simulated=True),self.clock)

    def test_passive_status_has_no_tables_files_network_or_model(self):
        with patch("socket.create_connection",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No worker")):
            value=self.service.status(self.clock)
        self.assertIsNone(value["paper"])
        self.assertIsNone(value["quality"])
        with self.service.database() as db:self.assertFalse(self.service._exists(db))
        self.assertFalse((self.settings.workspace/INBOX).exists())

    def test_offline_historical_backtest_is_separate_from_forward_cash(self):
        with patch("socket.create_connection",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No worker")):
            self.imported()
            result=self.service.backtest(self.body(),self.clock)
            self.paper()
        status=self.service.status(self.clock)
        self.assertEqual(result["status"],"completed")
        self.assertFalse(result["execution_qualified"])
        self.assertEqual(status["paper"]["state"]["cash"],"500")
        self.assertEqual(status["paper"]["summary"]["trading_pnl"],"0")
        self.assertIsNone(status["paper"]["state"]["position"])
        self.assertIsNone(status["paper"]["state"]["pending"])
        self.assertIsNone(status["forward_quality"])
        self.assertEqual(status["events"],[])

    def test_missing_inbox_is_waiting_and_not_a_failure_or_fake_fill(self):
        self.enable();self.paper()
        result=self.service.refresh(self.body(),self.clock)
        self.assertEqual(result["outcome"],"waiting_for_quotes")
        value=self.service.status(self.clock)
        self.assertIsNone(value["hold"])
        self.assertEqual(value["paper"]["summary"]["trade_count"],0)

    def test_current_recorded_first_inbox_baselines_without_old_trade(self):
        self.enable();self.paper()
        self.write(path=INBOX)
        result=self.service.refresh(self.body(),self.clock)
        value=self.service.status(self.clock)
        self.assertEqual(result["paper_status"],"completed")
        self.assertEqual(value["events"][0]["type"],"baseline")
        self.assertEqual(value["paper"]["state"]["processed_count"],14)
        self.assertIsNone(value["paper"]["state"]["pending"])
        self.assertIsNone(value["quality"])
        self.assertIsNotNone(value["forward_quality"])
        again=self.service.refresh(self.body(),self.clock)
        self.assertEqual(again["paper_events"],0)

    def test_historical_import_never_advances_an_existing_forward_account(self):
        self.enable();self.paper()
        before=self.service.status(self.clock)["paper"]
        self.service.import_data(self.body(path=self.write()),self.clock)
        self.assertEqual(before,self.service.status(self.clock)["paper"])

    def test_assumed_eod_history_works_for_research_but_not_forward_inbox(self):
        self.dataset["source"]["quote_time_basis"]="assumed_eod"
        self.imported();self.paper()
        self.assertEqual(self.service.backtest(self.body(),self.clock)["status"],"completed")
        self.write(path=INBOX)
        with self.assertRaisesRegex(ValueError,"recorded quote timestamps"):
            self.service.refresh(self.body(),self.clock)
        value=self.service.status(self.clock)
        self.assertIsNotNone(value["hold"])
        self.assertIsNone(value["forward_quality"])
        self.assertEqual(value["paper"]["state"]["trade_count"],0)

    def test_synthetic_owner_import_is_rejected_and_leaves_no_data(self):
        self.enable();self.dataset["evidence_kind"]="synthetic_fixture"
        with self.assertRaisesRegex(ValueError,"actual historical quotes"):
            self.service.import_data(self.body(path=self.write()),self.clock)
        value=self.service.status(self.clock)
        self.assertIsNone(value["quality"])
        self.assertEqual(value["hold"]["status"],"failed")

    def test_paths_reject_absolute_traversal_and_links_before_external_read(self):
        self.enable()
        for invalid in ("E:/forbidden.json","C:/outside.json","../outside.json","/outside.json"):
            body = self.body(path=invalid)
            with self.subTest(path=invalid),patch.object(self.service,"_read",side_effect=AssertionError("Must not read")),self.assertRaises(ValueError):
                self.service.import_data(body,self.clock)

    def test_completed_replay_reads_no_source_and_does_not_advance_revision(self):
        self.enable();body=self.body(path=self.write())
        self.service.import_data(body,self.clock)
        revision=self.service.status(self.clock)["revision"]
        with patch.object(self.service,"_read",side_effect=AssertionError("No replay source read")):
            replay=self.service.import_data(body,self.clock)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.service.status(self.clock)["revision"],revision)

    def test_history_mutation_or_source_identity_change_is_held_not_adopted(self):
        self.imported();before=self.service.status(self.clock)["data_sha256"]
        changed=deepcopy(self.dataset);changed["snapshots"][0]["quotes"][0]["volume"]+=1
        with self.assertRaisesRegex(ValueError,"history changed"):
            self.service.import_data(self.body(path=self.write(changed)),self.clock)
        self.assertEqual(self.service.status(self.clock)["data_sha256"],before)
        self.assertIsNotNone(self.service.status(self.clock)["hold"])

    def test_acknowledgment_does_not_replay_failed_work_or_adopt_input(self):
        self.enable();body=self.body(path=self.write())
        with patch.object(self.service,"_read",side_effect=ValueError("fixture failure")),self.assertRaises(ValueError):
            self.service.import_data(body,self.clock)
        revision=self.service.status(self.clock)["revision"]
        self.service.acknowledge({"request_id":body["request_id"],"expected_revision":revision})
        value=self.service.status(self.clock)
        self.assertIsNone(value["quality"]);self.assertIsNone(value["hold"])
        with self.assertRaises(HandoffConflict):self.service.import_data(body,self.clock)

    def test_tampered_report_blocks_replay_and_status_instead_of_regeneration(self):
        self.imported();body=self.body();result=self.service.backtest(body,self.clock)
        report=self.settings.workspace/result["artifacts"][0]["path"]
        report.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError,"missing or changed"):self.service.status(self.clock)
        with self.assertRaisesRegex(ValueError,"missing or changed"):self.service.backtest(body,self.clock)
        self.assertEqual(report.read_bytes(),b"{}")

    def test_revoked_brief_permission_during_read_retains_input_and_holds(self):
        self.enable();body=self.body(path=self.write())
        original=self.service._read
        def revoke(*args,**kwargs):
            raw=original(*args,**kwargs);self.controls.update(paused=True,revision=2);return raw
        with patch.object(self.service,"_read",side_effect=revoke),self.assertRaises(HandoffConflict):self.service.import_data(body,self.clock)
        value=self.service.status(self.clock)
        self.assertIsNone(value["quality"])
        self.assertIsNotNone(value["hold"])

    def test_archive_preserves_all_evidence_and_cannot_reset_same_month(self):
        self.imported();self.paper()
        result=self.service.archive(self.body(reason="Testing retained experiment",archive_data=True),self.clock)
        self.assertFalse(result["synthetic_liquidation"])
        evidence=json.loads((self.settings.workspace/result["artifacts"][0]["path"]).read_bytes())
        self.assertEqual(evidence["paper"]["state"]["cash"],"500")
        self.assertIsNotNone(evidence["data"])
        self.assertIsNone(self.service.status(self.clock)["paper"])
        with self.assertRaisesRegex(HandoffConflict,"already belongs"):
            self.paper()

    def test_archive_does_not_release_unresolved_risk_in_a_later_month(self):
        self.enable();self.paper()
        state=paper_to(fixture(15,historical=True),15)
        self.clock=datetime.fromisoformat(fixture(15,historical=True)["as_of"])
        with self.service.database() as db:db.execute("UPDATE opt_accounts SET state=?",(json.dumps(state),))
        self.service.archive(self.body(reason="Retain unresolved marks",archive_data=False),self.clock)
        self.clock=datetime(2024,2,1,12,tzinfo=timezone.utc)
        with self.assertRaisesRegex(HandoffConflict,"unresolved risk"):
            self.paper()

    def test_missing_inbox_after_position_deadline_suspends_passive_cap_assurance(self):
        self.enable();self.paper()
        state=paper_to(fixture(15,historical=True),15)
        with self.service.database() as db:db.execute("UPDATE opt_accounts SET state=?",(json.dumps(state),))
        self.clock=datetime(2024,2,1,12,tzinfo=timezone.utc)
        self.service.refresh(self.body(),self.clock)
        summary=self.service.status(self.clock)["paper"]["summary"]
        self.assertFalse(summary["cap_qualified"])
        self.assertEqual(summary["hold"]["code"],"unverified_disposal_deadline")
        self.assertIsNotNone(summary["position"])

    def test_global_pause_and_cas_block_mutations_without_resetting(self):
        self.enable();self.store.set_setting("paused",True)
        with self.assertRaises(HandoffConflict):self.paper()
        self.assertTrue(self.service.status(self.clock)["config"]["enabled"])
        self.store.set_setting("paused",False)
        with self.assertRaises(HandoffConflict):self.service.configure({"enabled":True,"paused":False,"expected_revision":0},self.clock)

    def test_daily_owner_missing_inbox_runs_once_with_no_subprocess(self):
        self.enable();clock=self.clock+timedelta(days=1)
        with patch("subprocess.Popen",side_effect=AssertionError("No child")):
            first=self.service.run_due(clock);second=self.service.run_due(clock)
        self.assertEqual(first["outcome"],"waiting_for_quotes")
        self.assertIsNone(second)

    def test_orphaned_receipt_is_held_but_only_live_lock_is_busy(self):
        self.enable()
        with self.service.database() as db:
            receipt={"request_id":"orphan","status":"running","artifacts":[]}
            db.execute("INSERT INTO opt_runs VALUES(?,?,?,?,?,?,?)",("orphan","refresh",None,"a"*64,"running",json.dumps(receipt),100))
        self.assertFalse(self.service.busy())
        self.assertIsNone(self.service.run_due(self.clock+timedelta(days=1)))
        from ariadne.locking import InstanceLock
        lock=InstanceLock(self.settings.data_dir/"options-workflow.lock");lock.acquire()
        try:self.assertTrue(self.service.busy())
        finally:lock.release()


if __name__=="__main__":unittest.main()
