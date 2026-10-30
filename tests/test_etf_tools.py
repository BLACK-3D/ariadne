"""Frozen ETF inputs, private quotas, exclusive output and fail-stop journals."""
from contextlib import contextmanager
from datetime import date, timedelta
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne import etf_research
from ariadne.etf_tools import ETFResearchTools, RESERVATION_BYTES
from ariadne.operations_workflows import OperationsWorkflows
from ariadne.store import Store
from ariadne.tools import ToolRegistry


def dataset():
    first = date(2010, 1, 1)
    return {"schema": 1, "as_of": "2026-10-29T00:00:00Z", "currency": "USD", "roles": {"us": "US"},
            "series": {"US": {"kind": "nav_total_return_index", "dividend_policy": "reinvested",
                "sources": [{"url": "https://example.test/synthetic-fixture-not-real-market-data", "sha256": "a" * 64,
                             "retrieved_at": "2026-10-29T00:00:00Z"}],
                "observations": [{"date": (first + timedelta(days=index)).isoformat(), "value": 100 + index * .1}
                                 for index in range(1100)]}}}


class ETFToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".operations-workflows-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name))
        self.store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.addCleanup(self.registry.media.close)
        self.registry.procedures.store = self.store
        self.service = ETFResearchTools(self.registry)
        self.input = self.registry._path("dataset.json")
        self.input.write_text(json.dumps(dataset()), encoding="utf-8")
        self.args = {"action": "study", "path": "dataset.json", "request_id": "fixture-etf"}
        self.output = self.registry._path("reports/etf-research/fixture-etf")

    def run_study(self, **extra):
        return self.service.execute("etf_research", {**self.args, **extra})

    def row(self):
        with self.store.connect(require_existing=True) as db:
            return dict(db.execute("SELECT * FROM etf_runs WHERE request_id=?", (self.args["request_id"],)).fetchone())

    def count(self, name):
        self.assertIn(name, {"etf_runs", "operation_inputs"})
        with self.store.connect(require_existing=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone():
                return 0
            return db.execute("SELECT count(*) FROM " + name).fetchone()[0]

    def assert_unaccepted(self):
        self.assertEqual(self.count("etf_runs"), 0)
        self.assertEqual(self.count("operation_inputs"), 0)
        self.assertFalse(self.output.exists())

    def test_real_engine_offline_artifacts_and_passive_frozen_replay(self):
        original = self.input.read_bytes()
        with mock.patch("socket.socket", side_effect=AssertionError("No sockets")), mock.patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")), mock.patch("subprocess.Popen", side_effect=AssertionError("No processes/models")):
            result = self.run_study()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_calls"], 0)
        self.assertFalse(result["orders"])
        self.assertFalse(result["summary"]["execution_qualified"])
        self.assertFalse(result["summary"]["future_profit_proven"])
        self.assertEqual(result["summary"]["cash_return_assumption"], 0)
        self.assertEqual(result["summary"]["data_last_dates"], {"US": "2013-01-04"})
        self.assertEqual(result["summary"]["retained_last_date"], "2013-01-04")
        self.assertTrue(result["summary"]["analysis_cutoff"].startswith("2026-10-29"))
        self.assertEqual(result["summary"]["dividend_status"], "declared_reinvested_total_return")
        self.assertEqual(result["summary"]["month_end_coverage_status"], "unverified_exchange_calendar")
        self.assertTrue(result["current_source_matches"])
        self.assertEqual({item["path"].split("/")[-1] for item in result["artifacts"]}, {"study.json", "study.md"})
        for item in result["artifacts"]:
            raw = self.registry._path(item["path"]).read_bytes()
            self.assertEqual(len(raw), item["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
        self.assertEqual(self.row()["reserved_bytes"], sum(item["bytes"] for item in result["artifacts"]))
        self.input.write_text("changed after completion", encoding="utf-8")
        with mock.patch.object(etf_research, "run_study", side_effect=AssertionError("Never recalculate a completed ID")):
            replay = self.run_study()
            self.assertTrue(replay["replayed"])
            self.assertFalse(replay["current_source_matches"])
            self.input.unlink()
            self.assertIsNone(self.run_study()["current_source_matches"])
        self.assertEqual(replay["accepted_input"]["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.count("operation_inputs"), 1)

    def test_capture_survives_source_edit_during_computation(self):
        original_raw = self.input.read_bytes()
        implementation = etf_research.run_study
        def changed(accepted, *, cost_bps):
            self.input.write_text("not the accepted dataset", encoding="utf-8")
            return implementation(accepted, cost_bps=cost_bps)
        with mock.patch.object(etf_research, "run_study", side_effect=changed):
            result = self.run_study()
        self.assertFalse(result["current_source_matches"])
        report = json.loads((self.output / "study.json").read_text(encoding="utf-8"))
        self.assertEqual(report["accepted_input"]["sha256"], hashlib.sha256(original_raw).hexdigest())
        self.assertEqual(report["source_as_of"], dataset()["as_of"])
        with self.store.connect(require_existing=True) as db:
            self.assertEqual(bytes(db.execute("SELECT content FROM operation_inputs").fetchone()[0]), original_raw)

    def test_completed_id_changed_arguments_never_recalculate(self):
        self.run_study()
        with mock.patch.object(etf_research, "run_study", side_effect=AssertionError("No replay")):
            with self.assertRaisesRegex(ValueError, "different arguments"):
                self.run_study(cost_bps=10)
            with self.assertRaisesRegex(ValueError, "different arguments"):
                self.run_study(path="other.json")

    def test_completed_artifact_tamper_and_missing_artifact_are_refused(self):
        self.run_study()
        target = self.output / "study.json"
        original = target.read_bytes()
        target.write_bytes(original + b" ")
        with mock.patch.object(etf_research, "run_study", side_effect=AssertionError("No recomputation")):
            with self.assertRaisesRegex(ValueError, "artifacts changed"):
                self.run_study()
            target.write_bytes(original)
            (self.output / "study.md").unlink()
            with self.assertRaises(FileNotFoundError): self.run_study()
        self.assertEqual(self.row()["status"], "completed")

    def test_private_accepted_blob_tamper_refuses_replay(self):
        self.run_study()
        with self.store.connect(require_existing=True) as db:
            db.execute("UPDATE operation_inputs SET content=CAST(zeroblob(bytes) AS BLOB)")
        with mock.patch.object(etf_research, "run_study", side_effect=AssertionError("No recomputation")):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.run_study()

    def test_preexisting_output_directory_is_not_accepted_or_adopted(self):
        self.output.mkdir(parents=True)
        sentinel = self.output / "study.json"
        sentinel.write_bytes(b"old unrelated result")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.run_study()
        self.assertEqual(sentinel.read_bytes(), b"old unrelated result")
        self.assertEqual(self.count("etf_runs"), 0)
        self.assertEqual(self.count("operation_inputs"), 0)

    def test_directory_created_after_acceptance_is_preserved_and_failed_id_never_replays(self):
        implementation = etf_research.run_study
        def race(accepted, *, cost_bps):
            self.output.mkdir(parents=True)
            (self.output / "study.json").write_bytes(b"racing owner file")
            return implementation(accepted, cost_bps=cost_bps)
        with mock.patch.object(etf_research, "run_study", side_effect=race) as called:
            with self.assertRaisesRegex(ValueError, "stopped after acceptance"):
                self.run_study()
            with self.assertRaisesRegex(ValueError, "will not run again"):
                self.run_study()
            self.assertEqual(called.call_count, 1)
        self.assertEqual((self.output / "study.json").read_bytes(), b"racing owner file")
        self.assertEqual(self.row()["status"], "failed")
        self.assertEqual(self.row()["reserved_bytes"], RESERVATION_BYTES)

    def test_second_file_collision_never_overwrites_and_retains_partial_receipt(self):
        publisher = self.registry.procedures.service()
        original = publisher._publish
        def racing_file(destination, payload, descriptor):
            if destination.name == "study.md": destination.write_bytes(b"do not overwrite")
            return original(destination, payload, descriptor)
        with mock.patch.object(publisher, "_publish", side_effect=racing_file):
            with self.assertRaisesRegex(ValueError, "stopped after acceptance"): self.run_study()
        self.assertEqual((self.output / "study.md").read_bytes(), b"do not overwrite")
        row = self.row()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["reserved_bytes"], RESERVATION_BYTES)
        self.assertEqual(len(json.loads(row["receipt"])["artifacts"]), 1)
        with self.assertRaisesRegex(ValueError, "will not run again"): self.run_study()

    def test_partial_readback_change_fails_instead_of_claiming_completion(self):
        publisher = self.registry.procedures.service()
        original = publisher._publish
        def damaged(destination, payload, descriptor):
            original(destination, payload, descriptor)
            destination.write_bytes(b"modified after publication")
        with mock.patch.object(publisher, "_publish", side_effect=damaged):
            with self.assertRaisesRegex(ValueError, "stopped after acceptance"): self.run_study()
        self.assertEqual(self.row()["status"], "failed")
        self.assertFalse((self.output / "study.md").exists())

    def test_process_interruption_after_both_files_keeps_uncertain_marker_without_adoption(self):
        def interrupted():
            if (self.output / "study.md").exists(): raise SystemExit("simulated interruption")
            return False
        with self.assertRaises(SystemExit): self.service.execute("etf_research", self.args, cancelled=interrupted)
        self.assertEqual(self.row()["status"], "running")
        self.assertTrue((self.output / "study.json").is_file())
        self.assertTrue((self.output / "study.md").is_file())
        with mock.patch.object(etf_research, "run_study", side_effect=AssertionError("No adoption")):
            with self.assertRaisesRegex(ValueError, "will not run again"): self.run_study()
        self.assertEqual(self.row()["reserved_bytes"], RESERVATION_BYTES)

    def test_pause_and_cancellation_before_acceptance_do_not_capture_inputs(self):
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.service.execute("etf_research", self.args, cancelled=lambda: True)
        self.store.set_setting("paused", True)
        with self.assertRaisesRegex(ValueError, "pause"):
            self.run_study()
        self.assert_unaccepted()

    def test_pause_after_compute_keeps_failed_reservation_and_no_files(self):
        original = etf_research.run_study
        def paused(accepted, *, cost_bps):
            result = original(accepted, cost_bps=cost_bps)
            self.store.set_setting("paused", True)
            return result
        with mock.patch.object(etf_research, "run_study", side_effect=paused):
            with self.assertRaisesRegex(ValueError, "stopped after acceptance"): self.run_study()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.row()["status"], "failed")
        self.assertEqual(self.row()["reserved_bytes"], RESERVATION_BYTES)
        self.store.set_setting("paused", False)
        with self.assertRaisesRegex(ValueError, "will not run again"): self.run_study()

    def test_cancellation_after_first_file_keeps_partial_and_never_completes(self):
        def cancelled(): return (self.output / "study.json").exists()
        with self.assertRaisesRegex(ValueError, "stopped after acceptance"):
            self.service.execute("etf_research", self.args, cancelled=cancelled)
        self.assertEqual(self.row()["status"], "failed")
        self.assertTrue((self.output / "study.json").exists())
        self.assertFalse((self.output / "study.md").exists())

    def test_strict_arguments_json_and_paths_fail_before_acceptance(self):
        for changes in ({"cost_bps": True}, {"cost_bps": float("nan")}, {"cost_bps": float("inf")}, {"cost_bps": -1}, {"cost_bps": 101}, {"cost_bps": 10**1000}, {"cost_bps": "5"}, {"request_id": "../outside"}, {"request_id": "a" * 65}, {"path": "E:\\excluded.json"}, {"path": "../outside.json"}, {"path": "script.py"}, {"action": "orders"}, {"unknown": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.run_study(**changes)
        for raw in (b'\xff', b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'{"schema":1e999}', b'[' * 1500):
            self.input.write_bytes(raw)
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError): self.run_study()
        self.assert_unaccepted()

    def test_oversized_or_changed_guarded_source_is_not_captured(self):
        self.input.write_bytes(b" " * 4_000_001)
        with self.assertRaisesRegex(ValueError, "byte limit"): self.run_study()
        self.assert_unaccepted()
        self.input.write_text(json.dumps(dataset()), encoding="utf-8")
        original = __import__("os").fstat
        calls = []
        def unstable(fd):
            info = original(fd)
            caller = inspect.currentframe().f_back
            while caller is not None and Path(caller.f_code.co_filename).name == "mock.py":
                caller = caller.f_back
            if caller.f_code.co_name == "_read" and Path(caller.f_code.co_filename).name == "etf_tools.py":
                calls.append(True)
                if len(calls) == 2:
                    return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
                        st_mtime_ns=info.st_mtime_ns + 1, st_ctime_ns=info.st_ctime_ns,
                        st_mode=info.st_mode, st_nlink=info.st_nlink)
            return info
        with mock.patch("ariadne.etf_tools.os.fstat", side_effect=unstable):
            with self.assertRaisesRegex(ValueError, "changed during"): self.run_study()
        self.assert_unaccepted()

    def test_shared_input_quota_blocks_new_blob_and_exact_duplicates_reuse_it(self):
        raw = self.input.read_bytes()
        with self.service.database() as db:
            self.service._schema(db)
            OperationsWorkflows._put(db, b"occupied by another operation")
        with mock.patch("ariadne.operations_workflows.MAX_INPUT_ROWS", 1):
            with self.assertRaisesRegex(ValueError, "frozen-input storage is full"): self.run_study()
        self.assertEqual(self.count("etf_runs"), 0)
        self.assertEqual(self.count("operation_inputs"), 1)
        with self.service.database() as db:
            db.execute("DELETE FROM operation_inputs")
            OperationsWorkflows._put(db, raw)
        with mock.patch("ariadne.operations_workflows.MAX_INPUT_ROWS", 1):
            self.assertTrue(self.run_study()["ok"])
        self.assertEqual(self.count("operation_inputs"), 1)

    def test_primary_zero_custom_and_max_costs_are_forwarded_without_a_search(self):
        for index, cost in enumerate((0, 7.5, 100)):
            with self.subTest(cost=cost):
                result = self.run_study(request_id="cost-case-" + str(index), cost_bps=cost)
                self.assertEqual(result["cost_bps"], float(cost))
                report = json.loads(self.registry._path(result["artifacts"][0]["path"]).read_text(encoding="utf-8"))
                self.assertEqual(report["cost_bps"], float(cost))
        self.assertEqual(self.count("operation_inputs"), 1)

    def test_output_budget_history_and_invalid_accounting_hold_new_requests(self):
        self.run_study()
        used = self.row()["reserved_bytes"]
        with mock.patch("ariadne.etf_tools.REPORT_BUDGET_BYTES", used + RESERVATION_BYTES - 1):
            with self.assertRaisesRegex(ValueError, "budget is full"): self.run_study(request_id="second")
        with mock.patch("ariadne.etf_tools.MAX_RUNS", 1):
            with self.assertRaisesRegex(ValueError, "budget is full"): self.run_study(request_id="second")
        with self.store.connect(require_existing=True) as db:
            db.execute("UPDATE etf_runs SET reserved_bytes='invalid'")
        with self.assertRaisesRegex(ValueError, "accounting is invalid"): self.run_study(request_id="second")
        self.assertEqual(self.count("etf_runs"), 1)

    def test_oversized_output_fails_before_any_file_and_keeps_reservation(self):
        report = {"study_type": "offline_etf_nav_research", "strategies": [], "large": "x" * 4_000_000}
        with mock.patch.object(etf_research, "run_study", return_value=report):
            with self.assertRaisesRegex(ValueError, "stopped after acceptance"): self.run_study()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.row()["status"], "failed")
        self.assertEqual(self.row()["reserved_bytes"], RESERVATION_BYTES)

    def test_missing_main_never_recreates_and_workspace_main_is_rejected(self):
        held = self.store.path.with_suffix(".held")
        self.store.path.rename(held)
        try:
            with self.assertRaisesRegex(ValueError, "main database is missing"): self.run_study()
            self.assertFalse(self.store.path.exists())
        finally: held.rename(self.store.path)
        original_connect = self.store.connect
        observed = []
        @contextmanager
        def disappears(*, require_existing=False):
            self.assertTrue(require_existing)
            self.store.path.rename(held)
            try:
                try:
                    with original_connect(require_existing=True) as db: yield db
                finally: observed.append(self.store.path.exists())
            finally: held.rename(self.store.path)
        with mock.patch.object(self.store, "connect", disappears):
            with self.assertRaises(sqlite3.OperationalError): self.run_study()
        self.assertEqual(observed, [False])
        self.registry.procedures.store = Store(self.registry.root / "model-writable.sqlite3")
        with self.assertRaisesRegex(ValueError, "outside the writable workspace"): self.run_study()

    def test_passive_status_and_fingerprint_do_not_create_private_run_schema(self):
        self.assertTrue(self.service.status()["ready"])
        fingerprint = self.service.fingerprint_material("etf_research", self.args)
        self.assertEqual(fingerprint["source_sha256"], hashlib.sha256(self.input.read_bytes()).hexdigest())
        with self.store.connect(require_existing=True) as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='etf_runs'").fetchone())
        self.registry.procedures.store = None
        self.assertFalse(self.service.status()["ready"])
        with self.assertRaisesRegex(ValueError, "private main store"): self.run_study()

    def test_concurrent_provider_cannot_start_second_owned_study(self):
        settings = Settings(data_dir=self.settings.data_dir)
        other_registry = ToolRegistry(settings)
        self.addCleanup(other_registry.media.close)
        other_registry.procedures.store = self.store
        other = ETFResearchTools(other_registry)
        entered, release = threading.Event(), threading.Event()
        results = []
        original = etf_research.run_study
        def parked(accepted, *, cost_bps):
            entered.set()
            if not release.wait(10): raise AssertionError("Fixture study was not released")
            return original(accepted, cost_bps=cost_bps)
        def first():
            try: results.append(self.run_study())
            except BaseException as error: results.append(error)
        with mock.patch.object(etf_research, "run_study", side_effect=parked):
            thread = threading.Thread(target=first)
            thread.start()
            try:
                self.assertTrue(entered.wait(10))
                with self.assertRaisesRegex(ValueError, "already owns"):
                    other.execute("etf_research", {**self.args, "request_id": "other-provider"})
            finally:
                release.set(); thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], dict, results)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(self.count("etf_runs"), 1)


if __name__ == "__main__": unittest.main()
