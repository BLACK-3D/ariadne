"""Frozen zero-model operations share the canonical routine scheduler/receipts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne.operations_workflows import OperationsWorkflows, OperationInputError
from ariadne.routines import RoutineConflict
from ariadne.store import Store
from ariadne.tools import ToolRegistry


def op(kind, **args):
    return {"operation": kind, "arguments": args}


class OperationsWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".operations-workflows-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.settings = Settings(data_dir=self.data)
        self.store = Store(self.data/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.registry.procedures.store = self.store
        self.registry.local_controls.store = self.store
        self.service = OperationsWorkflows(self.registry.procedures.service(), self.store)
        self.fixtures = Path(__file__).resolve().parent.parent/"config/examples"

    def copy(self, name, path=None):
        path = path or name
        self.registry._file_write({"path": path, "content": (self.fixtures/name).read_text(encoding="utf-8")})
        return path

    def run_plan(self, steps, request_id="workflow-proof", enabled=False):
        definition = self.service.save("Qualification workflow", steps, 1440, enabled)
        run = self.service.enqueue(definition["id"], request_id)
        for _ in range(5):
            if self.service.detail(request_id)["status"] not in {"queued", "running"}:
                break
            self.assertTrue(self.service.tick())
        return definition, self.service.detail(run["id"])

    def test_business_and_saved_device_workflow_runs_offline_with_exact_artifacts(self):
        with mock.patch("socket.socket", side_effect=AssertionError("No sockets")), mock.patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")), mock.patch("subprocess.Popen", side_effect=AssertionError("No model or process")):
            definition, run = self.run_plan([op("business_snapshot"), op("device_status")])
        self.assertEqual(run["status"], "completed", run)
        self.assertEqual(run["model_calls"], 0)
        self.assertEqual(len(run["artifacts"]), 5)
        self.assertTrue(self.service.verify(run["id"])["artifacts_verified"])
        self.assertFalse(definition["enabled"])
        reports = [json.loads(self.registry._path(row["path"]).read_text()) for row in run["artifacts"] if row["path"].endswith("operation.json")]
        self.assertEqual(reports[1]["observation"]["status"], "unavailable")
        self.assertTrue(reports[1]["observation"]["stale"])

    def test_accepted_market_bytes_are_used_after_original_files_deleted(self):
        csv = self.copy("market-demo.csv")
        meta = self.copy("market-demo.json")
        definition = self.service.save("Market qualification", [op("market_audit", path=csv, metadata_path=meta), op("market_study", path=csv, metadata_path=meta)])
        run = self.service.enqueue(definition["id"], "market-accepted")
        original = hashlib.sha256(self.registry._path(csv).read_bytes()).hexdigest()
        self.registry._path(csv).unlink(); self.registry._path(meta).unlink()
        self.assertTrue(self.service.tick()); self.assertTrue(self.service.tick())
        run = self.service.detail(run["id"])
        self.assertEqual(run["status"], "completed", run)
        self.assertEqual(run["steps"][0]["receipt"]["audit_valid"], True)
        for row in run["artifacts"]:
            if row["path"].startswith("reports/market-research/"):
                report = json.loads(self.registry._path(row["path"]).read_text())
                self.assertEqual(report["inputs"]["csv"]["sha256"], original)
        self.assertTrue(self.service.verify(run["id"])["artifacts_verified"])

    def test_acceptance_retrieval_does_not_recapture_changed_sources(self):
        path = self.copy("business-erpnext-demo.json")
        definition = self.service.save("ERP audit", [op("erp_audit", path=path, as_of="today_utc")])
        run = self.service.enqueue(definition["id"], "same-accepted")
        with self.service.database() as db:
            count = db.execute("SELECT count(*) FROM operation_inputs").fetchone()[0]
        self.registry._file_write({"path": path, "content": "modified after acceptance", "overwrite": True})
        again = self.service.enqueue(definition["id"], "same-accepted")
        self.assertEqual(again["plan"], run["plan"])
        with self.service.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM operation_inputs").fetchone()[0], count)
        self.assertTrue(self.service.tick())
        completed = self.service.detail(run["id"])
        self.assertEqual(completed["status"], "completed", completed)
        frozen = completed["plan"]["operation_steps"]["operation_erp_audit"]
        self.assertEqual(frozen["arguments"]["as_of"], datetime.now(timezone.utc).date().isoformat())

    def test_bad_market_audit_holds_study_without_automatic_repair_or_retry(self):
        path = self.copy("market-demo.csv")
        meta = self.copy("market-demo.json")
        raw = self.registry._path(path).read_text()
        self.registry._file_write({"path": path, "content": raw+raw.splitlines()[1]+"\n", "overwrite": True})
        definition, run = self.run_plan([op("market_audit", path=path, metadata_path=meta), op("market_study", path=path, metadata_path=meta)], enabled=True)
        self.assertEqual(run["status"], "failed", run)
        self.assertEqual(run["steps"][0]["status"], "failed")
        self.assertEqual(run["steps"][1]["status"], "pending")
        held = next(row for row in self.service.definitions() if row["id"] == definition["id"])
        self.assertFalse(held["enabled"])
        self.assertTrue(held["last_error"])
        self.assertFalse(self.service.tick())

    def test_due_capture_failure_disables_once_and_rolls_back_partial_inputs(self):
        self.copy("market-demo.csv")
        definition = self.service.save("Missing metadata", [op("market_audit", path="market-demo.csv", metadata_path="missing.json")], 5, True)
        with self.service.database() as db:
            db.execute("UPDATE routine_definitions SET next_run=0 WHERE id=?", (definition["id"],))
        self.assertEqual(self.service.due(), 0)
        held = self.service.definitions()[0]
        self.assertFalse(held["enabled"])
        self.assertIsNone(held["next_run"])
        self.assertTrue(held["last_error"])
        with self.service.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM operation_inputs").fetchone()[0], 0)
        self.assertEqual(self.service.due(), 0)

    def test_failed_capture_and_quota_hold_manual_without_retaining_partial_blobs(self):
        path = self.copy("market-demo.csv")
        meta = self.copy("market-demo.json")
        definition = self.service.save("Bounded input", [op("market_audit", path=path, metadata_path=meta)], 5, True)
        with mock.patch("ariadne.operations_workflows.MAX_INPUT_TOTAL_BYTES", 1):
            with self.assertRaises(OperationInputError):
                self.service.enqueue(definition["id"], "too-large")
        self.assertFalse(self.service.definitions()[0]["enabled"])
        with self.service.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM operation_inputs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM routine_runs").fetchone()[0], 0)

    def test_recurring_identical_input_bytes_are_deduplicated(self):
        path = self.copy("market-demo.csv")
        meta = self.copy("market-demo.json")
        definition, first = self.run_plan([op("market_audit", path=path, metadata_path=meta)], "dedup-first")
        self.assertEqual(first["status"], "completed")
        self.service.enqueue(definition["id"], "dedup-second")
        self.assertTrue(self.service.tick())
        with self.service.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM operation_inputs").fetchone()[0], 2)

    def test_market_audit_and_study_capture_each_selected_file_only_once(self):
        path = self.copy("market-demo.csv")
        meta = self.copy("market-demo.json")
        definition = self.service.save("One accepted dataset", [op("market_audit", path=path, metadata_path=meta), op("market_study", path=path, metadata_path=meta)])
        with mock.patch.object(self.service, "_read_workspace", wraps=self.service._read_workspace) as reader:
            run = self.service.enqueue(definition["id"], "capture-once")
        self.assertEqual(reader.call_count, 2)
        frozen = run["plan"]["operation_steps"]
        self.assertEqual(frozen["operation_market_audit"]["inputs"], frozen["operation_market_study"]["inputs"])

    def test_invalid_private_storage_accounting_holds_new_allocations(self):
        definition = self.service.save("Private accounting", [op("business_snapshot")], 5, True)
        with self.service.database() as db:
            db.execute("INSERT INTO operation_inputs VALUES(?,?,?)", ("0"*64, b"test", -10))
        with self.assertRaises(OperationInputError):
            self.service.enqueue(definition["id"], "corrupt-accounting")
        self.assertFalse(self.service.definitions()[0]["enabled"])

    def test_operation_children_respect_existing_procedure_history_limit(self):
        self.service.procedures.run("business_snapshot", "existing-procedure")
        definition = self.service.save("Existing procedure quota", [op("device_status")], 5, True)
        run = self.service.enqueue(definition["id"], "no-new-procedure")
        with mock.patch("ariadne.operations_workflows.MAX_PROCEDURE_RUNS", 1):
            self.assertTrue(self.service.tick())
        self.assertEqual(self.service.detail(run["id"])["status"], "failed")
        with self.service.procedures.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM procedure_runs").fetchone()[0], 1)

    def test_corrupt_accepted_blob_never_reopens_original_source(self):
        path = self.copy("business-erpnext-demo.json")
        definition = self.service.save("Corrupt source hold", [op("erp_audit", path=path, as_of="2026-10-29")], 1440, True)
        run = self.service.enqueue(definition["id"], "corrupt-private")
        with self.service.database() as db:
            db.execute("UPDATE operation_inputs SET content=?", (b"corrupted",))
        with mock.patch.object(self.service, "_read_workspace", side_effect=AssertionError("No recapture")):
            self.assertTrue(self.service.tick())
        held = self.service.detail(run["id"])
        self.assertEqual(held["status"], "failed")
        self.assertFalse(self.service.definitions()[0]["enabled"])

    def test_missing_frozen_reference_cannot_fall_back_to_mutable_workspace(self):
        path = self.copy("market-demo.csv")
        meta = self.copy("market-demo.json")
        definition = self.service.save("Complete source set", [op("market_audit", path=path, metadata_path=meta)], 5, True)
        run = self.service.enqueue(definition["id"], "missing-reference")
        with self.service.database() as db:
            plan = json.loads(db.execute("SELECT plan FROM routine_runs WHERE id=?", (run["id"],)).fetchone()[0])
            del plan["operation_steps"]["operation_market_audit"]["inputs"]["path"]
            db.execute("UPDATE routine_runs SET plan=? WHERE id=?", (json.dumps(plan), run["id"]))
        with mock.patch.object(self.registry.market_research, "execute", side_effect=AssertionError("No shared mutable reader")):
            self.assertTrue(self.service.tick())
        self.assertEqual(self.service.detail(run["id"])["status"], "failed")

    def test_frozen_quote_uses_accepted_prices_and_contact_after_local_edits(self):
        for action, key, record in [
            ("upsert_contact", "contact-a", {"id": "demo-customer", "name": "Accepted customer"}),
            ("upsert_item", "item-a", {"id": "demo-widget", "name": "Accepted widget", "unit": "Nos", "unit_price_cents": 100}),
        ]:
            self.registry.business.execute("business_records", {"action": action, "request_id": key, "record": record})
        path = self.copy("business-quote-demo.json")
        definition = self.service.save("Accepted quote", [op("quote_draft", path=path)])
        run = self.service.enqueue(definition["id"], "quote-accepted")
        self.registry.business.execute("business_records", {"action": "upsert_item", "request_id": "item-b", "record": {"id": "demo-widget", "name": "Changed widget", "unit": "Nos", "unit_price_cents": 99999}})
        self.assertTrue(self.service.tick())
        completed = self.service.detail(run["id"])
        self.assertEqual(completed["status"], "completed", completed)
        report = json.loads(self.registry._path(next(row["path"] for row in completed["artifacts"] if row["path"].startswith("business-ops/"))).read_text())
        self.assertEqual(report["result"]["lines"][0]["unit_price_cents"], "100")
        self.assertNotIn("Changed widget", json.dumps(report))

    def test_interrupt_after_provider_publication_is_not_adopted_on_restart(self):
        definition = self.service.save("Interrupted publication", [op("business_snapshot")], 5, True)
        run = self.service.enqueue(definition["id"], "after-provider")
        with mock.patch.object(self.service.procedures, "_publish", side_effect=SystemExit("killed after provider artifact")):
            with self.assertRaises(SystemExit): self.service.tick()
        child = self.service.detail(run["id"])["steps"][0]
        self.assertTrue(self.registry._path("business-ops/"+child["request_id"]+".json").exists())
        self.assertEqual(self.service.recover(), 1)
        with mock.patch.object(self.service, "_run_operation", side_effect=AssertionError("No orphan adoption")):
            self.assertFalse(self.service.tick())
        self.assertEqual(self.service.detail(run["id"])["status"], "interrupted")

    def test_action_denylist_sequence_and_typed_arguments_fail_before_enqueue(self):
        cases = [op("apply", action_id="light"), op("refresh"), op("market_study", path="a.csv", metadata_path="a.json"),
            op("business_snapshot", request_id="caller"), op("device_status", action="apply"),
            op("market_audit", path="../outside.csv", metadata_path="a.json"), {"operation": [], "arguments": {}},
            op("erp_audit", path="a.json", as_of=False), op("quote_draft", path="not-json.txt")]
        for value in cases:
            with self.subTest(step=value), self.assertRaises(ValueError):
                self.service.save("Rejected", [value])
        with self.assertRaises(ValueError):
            self.service.save("Duplicate", [op("device_status"), op("device_status")])

    def test_preserved_failure_hold_requires_reviewed_cas_to_enable(self):
        definition = self.service.save("Held configuration", [op("erp_audit", path="missing.json", as_of="today_utc")], 5, True)
        with self.assertRaises(OperationInputError): self.service.enqueue(definition["id"], "missing-input")
        held = self.service.definitions()[0]
        edited = self.service.save("Renamed but held", held["steps"], 5, False, routine_id=held["id"], expected_revision=held["revision"])
        self.assertEqual(edited["last_error"], held["last_error"])
        with self.assertRaises(RoutineConflict):
            self.service.save(edited["name"], edited["steps"], 5, True, routine_id=edited["id"])
        with self.assertRaises(RoutineConflict):
            self.service.save(edited["name"], edited["steps"], 5, True, routine_id=edited["id"], expected_revision=held["revision"])

    def test_pause_and_recovery_never_repeat_started_operation(self):
        definition = self.service.save("Recovery", [op("business_snapshot")], 5, True)
        run = self.service.enqueue(definition["id"], "recover-operation")
        self.store.set_setting("paused", True)
        self.assertFalse(self.service.tick())
        self.store.set_setting("paused", False)
        with mock.patch.object(self.service, "_run_operation", side_effect=SystemExit("interrupted")):
            with self.assertRaises(SystemExit): self.service.tick()
        restarted = OperationsWorkflows(self.registry.procedures.service(), self.store)
        self.assertEqual(restarted.recover(), 1)
        self.assertEqual(restarted.detail(run["id"])["status"], "interrupted")
        with mock.patch.object(restarted, "_run_operation", side_effect=AssertionError("No replay")):
            self.assertFalse(restarted.tick())

    def test_device_export_report_does_not_modify_live_control_cache(self):
        config = json.loads((self.fixtures/"control-home-assistant.json").read_text())
        self.settings.connectors_path.write_text((self.fixtures/"control-connectors.json").read_text(), encoding="utf-8")
        self.registry._connectors = self.registry._load_connectors()
        self.registry.local_controls.config_path.write_text(json.dumps(config), encoding="utf-8")
        path = self.copy("control-states-demo.json")
        definition, run = self.run_plan([op("device_snapshot", source_path=path, observed_at="2026-10-29T12:00:00Z")])
        self.assertEqual(run["status"], "completed", run)
        current = self.registry.local_controls.execute("home_control", {"action": "status"})
        self.assertEqual(current["snapshot"]["status"], "unavailable")
        report = json.loads(self.registry._path(next(row["path"] for row in run["artifacts"] if row["path"].endswith("operation.json"))).read_text())
        self.assertEqual(report["observation"]["observed_at"], "2026-10-29T12:00:00+00:00")
        self.assertEqual(len(report["observation"]["entities"]), 2)

    def test_legacy_and_typed_steps_share_one_report_budget_and_verification(self):
        definition, run = self.run_plan(["business_snapshot", op("device_status")])
        self.assertEqual(run["status"], "completed", run)
        self.assertEqual(run["reserved_bytes"], sum(row["bytes"] for row in run["artifacts"]))
        self.assertTrue(self.service.verify(run["id"])["artifacts_verified"])


if __name__ == "__main__":
    unittest.main()
