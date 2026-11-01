"""Offline behavior fixtures, not actual VIX price or investment evidence."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.handoff import HandoffConflict
from ariadne.options_workflow import OptionsWorkflow
from ariadne.qqq_data import is_session
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.vix_signals import SOURCE_URLS


class VixWorkflowTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="vix-workflow-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name)/"data")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.store.set_setting("paused", False)
        self.registry = ToolRegistry(self.settings)
        self.controls = {"enabled":True,"paused":False,"revision":1}
        self.service = OptionsWorkflow(self.registry, self.store, controls=lambda:dict(self.controls))
        self.clock = datetime(2024,2,12,21,tzinfo=timezone.utc)
        self.counter = 0

    def body(self, **extra):
        self.counter += 1
        return {"request_id":"vix-workflow-"+str(self.counter), "expected_revision":self.service.status(self.clock)["revision"], **extra}

    def enable(self):
        self.service.configure({"enabled":True,"paused":False,"expected_revision":0}, self.clock-timedelta(days=1))

    def put(self, relative, raw):
        path = self.registry._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return relative

    def history(self, *, both=True):
        rows = []
        day = date(2024,1,2)
        while day <= date(2024,2,9):
            if is_session(day):
                rows.append(day.strftime("%m/%d/%Y")+",20,20,20,20")
            day += timedelta(days=1)
        raw = ("DATE,OPEN,HIGH,LOW,CLOSE\n"+"\n".join(rows)+"\n").encode()
        manifest = {"schema":1,"kind":"cboe_index_history","files":[]}
        for symbol in (("VIX","VIX9D") if both else ("VIX",)):
            path = self.put("imports/vix/"+symbol+".csv", raw)
            manifest["files"].append({"symbol":symbol,"path":path,"sha256":hashlib.sha256(raw).hexdigest(),
                                      "source_url":SOURCE_URLS[symbol],"retrieved_at":"2024-02-12T20:00:00Z"})
        path = self.put("imports/vix/history.json", json.dumps(manifest).encode())
        return path, manifest

    def test_passive_read_is_unfunded_and_creates_no_options_tables(self):
        with patch("socket.create_connection", side_effect=AssertionError("no network")), patch("subprocess.Popen", side_effect=AssertionError("no worker")):
            value = self.service.status(self.clock)["vix"]
        self.assertEqual(value["paper_status"], "waiting_for_qualified_quotes")
        self.assertFalse(value["paper_fills_enabled"])
        self.assertEqual(value["funding_added"], "0")
        self.assertIsNone(value["shared_account_id"])
        with self.service.database() as db:
            self.assertFalse(self.service._exists(db))

    def test_offline_signal_study_preserves_the_only_active_account_exactly(self):
        self.enable()
        self.service.start_portfolio(self.body(acknowledge_simulated=True), self.clock)
        before = deepcopy(self.service.status(self.clock)["paper"])
        path, _ = self.history()
        with patch("socket.create_connection", side_effect=AssertionError("no network")), patch("subprocess.Popen", side_effect=AssertionError("no worker")):
            receipt = self.service.vix.signals(self.body(path=path), self.clock)
            status = self.service.status(self.clock)
        self.assertEqual(status["paper"], before)
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(len(receipt["artifacts"]), 4)
        self.assertFalse(receipt["execution_qualified"])
        self.assertEqual(receipt["active_funding_added"], "0")
        self.assertEqual(status["vix"]["shared_account_id"], before["id"])
        self.assertEqual(status["vix"]["shared_monthly_remaining"], "500")
        self.assertEqual(status["vix"]["signals"], receipt)
        self.assertEqual(self.service.brief_snapshot(self.clock)["vix"], status["vix"])
        self.assertEqual(status["events"], [])

    def test_packaged_provider_review_is_read_only_and_does_not_authenticate(self):
        self.enable()
        self.service.start_portfolio(self.body(acknowledge_simulated=True), self.clock)
        before = self.service.status(self.clock)
        with patch("socket.create_connection", side_effect=AssertionError("no network")), patch("subprocess.Popen", side_effect=AssertionError("no worker")):
            status = self.service.status(self.clock)
            brief = self.service.brief_snapshot(self.clock)
        route = status["vix"]["connection_readiness"]
        self.assertEqual(route["status"], "awaiting_authenticated_verification")
        self.assertEqual(route["public_no_account"]["status"], "not_qualified")
        self.assertFalse(route["runtime_connection_checked"])
        self.assertTrue(all(gate["status"] == "unverified" for gate in route["gates"]))
        self.assertEqual(brief["vix"]["connection_readiness"], route)
        self.assertEqual(status["paper"], before["paper"])
        self.assertEqual(status["revision"], before["revision"])
        self.assertEqual(status["events"], before["events"])
        self.assertFalse(status["vix"]["paper_fills_enabled"])

    def test_completed_replay_checks_evidence_and_never_repeats_study(self):
        self.enable(); path, _ = self.history(both=False)
        body = self.body(path=path)
        first = self.service.vix.signals(body, self.clock)
        with patch("ariadne.vix_signals.study", side_effect=AssertionError("no replay")):
            again = self.service.vix.signals(body, self.clock)
        self.assertTrue(again["replayed"])
        self.assertEqual(first["artifacts"], again["artifacts"])

    def test_changed_evidence_is_not_adopted_or_regenerated(self):
        self.enable(); path, _ = self.history()
        receipt = self.service.vix.signals(self.body(path=path), self.clock)
        self.registry._path(receipt["artifacts"][0]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            self.service.status(self.clock)

    def test_external_path_is_rejected_before_read_or_reservation(self):
        self.enable()
        body = self.body(path="../outside.json")
        with patch.object(self.service, "_read", side_effect=AssertionError("must not read")), self.assertRaises(ValueError):
            self.service.vix.signals(body, self.clock)
        self.assertIsNone(self.service.status(self.clock)["hold"])

    def test_nested_paths_are_all_checked_before_any_history_read(self):
        self.enable(); path, manifest = self.history()
        manifest["files"][1]["path"] = "../outside.csv"
        self.put(path, json.dumps(manifest).encode())
        body = self.body(path=path)
        original = self.service._read
        opened = []
        def read(relative, *args):
            opened.append(relative)
            return original(relative, *args)
        with patch.object(self.service, "_read", side_effect=read), self.assertRaises(ValueError):
            self.service.vix.signals(body, self.clock)
        self.assertEqual(opened, [path])

    def test_duplicate_manifest_keys_and_csv_hash_mismatch_stop_with_receipt(self):
        self.enable(); path, manifest = self.history()
        manifest["files"][0]["sha256"] = "0"*64
        self.put(path, json.dumps(manifest).encode())
        body = self.body(path=path)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.service.vix.signals(body, self.clock)
        self.assertEqual(self.service.status(self.clock)["hold"]["request_id"], body["request_id"])
        with self.assertRaises(HandoffConflict):
            self.service.vix.signals(body, self.clock)

    def test_global_local_brief_holds_do_not_create_reports(self):
        self.enable(); path, _ = self.history()
        self.controls["paused"] = True
        with self.assertRaises(HandoffConflict):
            self.service.vix.signals(self.body(path=path), self.clock)
        self.assertIsNone(self.service.status(self.clock)["vix"]["signals"])

    def test_stale_revision_and_unknown_funding_fields_are_rejected(self):
        self.enable(); path, _ = self.history()
        body = self.body(path=path)
        with self.assertRaises(ValueError):
            self.service.vix.signals({**body, "funding":"500"}, self.clock)
        with self.assertRaises(HandoffConflict):
            self.service.vix.signals({**body, "expected_revision":0}, self.clock)

    def test_pause_then_resume_during_publication_still_revokes_operation(self):
        from ariadne.atomic import publish_bytes
        self.enable(); path, _ = self.history()
        count = 0
        def changed(*args, **kwargs):
            nonlocal count
            result = publish_bytes(*args, **kwargs)
            count += 1
            if count == 1:
                self.store.set_setting("paused", True)
                self.store.set_setting("paused", False)
            return result
        with patch("ariadne.options_workflow.publish_bytes", side_effect=changed), self.assertRaises(HandoffConflict):
            self.service.vix.signals(self.body(path=path), self.clock)
        self.assertEqual(self.service.status(self.clock)["hold"]["status"], "failed")
        self.assertIsNone(self.service.status(self.clock)["vix"]["signals"])

    def test_shared_report_quota_is_not_expanded_by_vix(self):
        self.enable(); path, _ = self.history()
        with self.service.database() as db:
            for number in range(100):
                db.execute("INSERT INTO opt_reports VALUES(?,?,?)", ("quota-"+str(number),1,"{}"))
        with self.assertRaisesRegex(ValueError, "quota is full"):
            self.service.vix.signals(self.body(path=path), self.clock)
        self.assertIsNotNone(self.service.status(self.clock)["hold"])

    def test_option_backtest_has_no_owner_funding_or_forward_authority(self):
        from test_vix_options import fixture
        data = fixture()
        self.clock = datetime.fromisoformat(data["as_of"])
        self.enable(); self.service.start_portfolio(self.body(acknowledge_simulated=True), self.clock)
        before = self.service.status(self.clock)["paper"]
        # Fabricated import-contract regression only; never actual market evidence.
        data["synthetic"] = False
        data["provenance"]["usage_rights_status"] = "user_supplied_permitted"
        path = self.put("imports/vix/test-contract-only.json", json.dumps(data).encode())
        with patch("socket.create_connection", side_effect=AssertionError("no network")), patch("subprocess.Popen", side_effect=AssertionError("no worker")):
            result = self.service.vix.backtest(self.body(path=path), self.clock)
        after = self.service.status(self.clock)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["execution_qualified"])
        self.assertFalse(result["summary"]["forward_paper_authority"])
        self.assertEqual(after["paper"], before)
        self.assertFalse(after["vix"]["paper_fills_enabled"])
        self.assertEqual(after["vix"]["backtest"], result)

    def test_synthetic_option_owner_import_is_rejected_and_not_replayed(self):
        from test_vix_options import fixture
        data = fixture()
        self.clock = datetime.fromisoformat(data["as_of"])
        self.enable()
        path = self.put("imports/vix/synthetic.json", json.dumps(data).encode())
        body = self.body(path=path)
        with self.assertRaisesRegex(ValueError, "actual historical quotes"):
            self.service.vix.backtest(body, self.clock)
        status = self.service.status(self.clock)
        self.assertIsNone(status["vix"]["backtest"])
        self.assertIsNone(status["paper"])
        self.assertEqual(status["hold"]["request_id"], body["request_id"])

