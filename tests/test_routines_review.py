"""Independent failure/race tests for zero-model fixed local routines."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.procedures import Procedures
from ariadne.routines import Routines, RoutineConflict, STEP_RESERVATION_BYTES
from ariadne.store import Store
from ariadne.tools import ToolRegistry


class RoutineReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".routine-review-", dir=Path.cwd())
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root, api_token="synthetic-routine-review")
        self.settings.workspace.mkdir()
        self.store = Store(self.root / "state.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.procedures = Procedures(self.registry, self.store)
        self.service = Routines(self.procedures, self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def definition(self, steps=None, enabled=False):
        return self.service.save("Independent local review", steps or ["readiness_report", "power_budget"], 5, enabled)

    def other(self):
        return Routines(Procedures(ToolRegistry(self.settings), Store(self.store.path)), Store(self.store.path))

    def due_now(self, identifier):
        with self.store.connect() as database:
            database.execute("UPDATE routine_definitions SET next_run=0 WHERE id=?", (identifier,))

    def completed_child(self, run, index=0):
        return next(step for step in run["steps"] if step["procedure_id"] == run["plan"]["steps"][index])

    def assert_one_real_child(self, run):
        self.assertEqual(sum(step["status"] == "completed" for step in run["steps"]), 1)
        child = next(step for step in run["steps"] if step["status"] == "completed")
        for artifact in child["receipt"]["artifacts"]:
            self.assertTrue(self.registry._path(artifact["path"]).is_file())

    def test_real_reports_take_one_child_per_tick_and_no_model_or_network(self):
        with patch("ariadne.model.Ollama.chat", side_effect=AssertionError("Unexpected model")) as model, patch(
                "socket.socket.connect", side_effect=AssertionError("Unexpected network")) as network:
            definition = self.definition()
            queued = self.service.enqueue(definition["id"], "independent-real-reports")
            self.assertEqual(queued["status"], "queued")
            self.assertTrue(self.service.tick())
            middle = self.service.detail(queued["id"])
            self.assertEqual(middle["status"], "queued")
            self.assert_one_real_child(middle)
            verified_bytes = sum(artifact["bytes"] for artifact in middle["artifacts"])
            self.assertGreater(verified_bytes, 0)
            self.assertEqual(middle["reserved_bytes"], verified_bytes)
            self.assertLess(middle["reserved_bytes"], STEP_RESERVATION_BYTES)
            self.assertTrue(self.service.tick())
            completed = self.service.verify(queued["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertTrue(completed["artifacts_verified"])
            self.assertEqual(completed["model_calls"], 0)
            self.assertFalse(self.service.tick())
            model.assert_not_called()
            network.assert_not_called()

    def test_two_services_duplicate_request_produces_one_invocation_and_same_children(self):
        definition = self.definition()
        second = self.other()
        barrier = threading.Barrier(2)
        def enqueue(service):
            barrier.wait(timeout=5)
            return service.enqueue(definition["id"], "same-request")
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(enqueue, service) for service in (self.service, second)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(len(self.service.runs()), 1)
        self.assertEqual(results[0]["steps"], results[1]["steps"])
        self.assertEqual(len({step["request_id"] for step in results[0]["steps"]}), 2)
        different = self.service.save("Another plan", ["knowledge_inventory"], 5, False)
        with self.assertRaises(RoutineConflict):
            second.enqueue(different["id"], "same-request")

    def test_concurrent_ticks_cannot_publish_two_children(self):
        definition = self.definition()
        run = self.service.enqueue(definition["id"], "concurrent-ticks")
        second = self.other()
        entered, release = threading.Event(), threading.Event()
        original = self.procedures.run
        def blocked(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args)
        with patch.object(self.procedures, "run", side_effect=blocked):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.service.tick)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertFalse(second.tick())
                    self.assertEqual(second.recover(), 0)
                finally:
                    release.set()
                self.assertTrue(future.result(timeout=10))
        self.assert_one_real_child(self.service.detail(run["id"]))

    def test_definition_edit_keeps_active_plan_and_new_id_cannot_overlap(self):
        definition = self.definition()
        run = self.service.enqueue(definition["id"], "frozen-plan")
        original_plan = run["plan"]
        self.service.save("New definition", ["knowledge_inventory"], 10, False, definition["id"])
        self.assertEqual(self.service.detail(run["id"])["plan"], original_plan)
        with self.assertRaises(RoutineConflict):
            self.service.enqueue(definition["id"], "second-request")
        self.service.tick()
        self.service.tick()
        complete = self.service.verify(run["id"])
        self.assertEqual([step["procedure_id"] for step in complete["steps"]], ["readiness_report", "power_budget"])

    def test_pause_blocks_due_and_child_execution_without_consuming_reservation(self):
        definition = self.definition(enabled=True)
        self.due_now(definition["id"])
        self.store.set_setting("paused", True)
        with patch.object(self.procedures, "run", side_effect=AssertionError("Paused execution")) as execute:
            self.assertEqual(self.service.due(), 0)
            run = self.service.enqueue(definition["id"], "paused-manual")
            self.assertFalse(self.service.tick())
            self.assertEqual(self.service.detail(run["id"])["reserved_bytes"], 0)
            execute.assert_not_called()

    def test_cancel_during_child_retains_evidence_and_never_starts_next_step(self):
        definition = self.definition(enabled=True)
        run = self.service.enqueue(definition["id"], "cancel-active-child")
        second = self.other()
        entered, release = threading.Event(), threading.Event()
        original = self.procedures.run
        def blocked(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args)
        with patch.object(self.procedures, "run", side_effect=blocked) as execute:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.service.tick)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertTrue(second.cancel(run["id"])["cancel_requested"])
                finally:
                    release.set()
                future.result(timeout=10)
            self.assertFalse(self.service.tick())
            self.assertEqual(execute.call_count, 1)
        stopped = self.service.detail(run["id"])
        self.assertEqual(stopped["status"], "cancelled")
        self.assert_one_real_child(stopped)
        self.assertEqual(stopped["steps"][1]["status"], "cancelled")
        self.assertFalse(self.service.definitions()[0]["enabled"])

    def test_disabling_scheduled_work_stops_remaining_steps_but_not_manual_work(self):
        scheduled = self.definition(enabled=True)
        self.due_now(scheduled["id"])
        self.assertEqual(self.service.due(), 1)
        run = self.service.runs()[0]
        self.service.tick()
        self.service.save(scheduled["name"], scheduled["steps"], 5, False, scheduled["id"])
        self.assertEqual(self.service.detail(run["id"])["status"], "cancelled")
        self.assert_one_real_child(self.service.detail(run["id"]))
        manual = self.service.enqueue(scheduled["id"], "independent-manual")
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.service.verify(manual["id"])["status"], "completed")

    def test_recovery_preserves_never_started_queue_but_stops_partial_sequences(self):
        definition = self.definition(enabled=True)
        run = self.service.enqueue(definition["id"], "partly-completed")
        self.service.tick()
        fresh_definition = self.service.save("Never started", ["knowledge_inventory"], 5, False)
        untouched = self.service.enqueue(fresh_definition["id"], "never-started")
        second = self.other()
        self.assertEqual(second.recover(), 1)
        self.assertEqual(second.detail(run["id"])["status"], "interrupted")
        self.assertEqual(second.detail(untouched["id"])["status"], "queued")
        self.assertEqual(second.detail(untouched["id"])["reserved_bytes"], 0)
        self.assert_one_real_child(second.detail(run["id"]))

    def test_crash_after_real_child_publication_never_adopts_or_replays_it(self):
        definition = self.definition(enabled=True)
        run = self.service.enqueue(definition["id"], "published-before-crash")
        original = self.procedures.run
        def publish_then_exit(*args):
            original(*args)
            raise SystemExit("Injected process stop")
        with patch.object(self.procedures, "run", side_effect=publish_then_exit) as execute:
            with self.assertRaises(SystemExit):
                self.service.tick()
            self.assertEqual(execute.call_count, 1)
        child_history = self.procedures.history()["runs"]
        self.assertEqual(child_history[0]["status"], "completed")
        self.assertEqual(self.service.detail(run["id"])["status"], "running")
        self.assertEqual(self.service.recover(), 1)
        with patch.object(self.procedures, "run", side_effect=AssertionError("Crash replay")) as execute:
            self.service.enqueue(definition["id"], run["id"])
            self.assertFalse(self.service.tick())
            execute.assert_not_called()
        self.assertEqual(self.service.detail(run["id"])["status"], "interrupted")

    def test_final_database_failure_keeps_writeahead_marker_and_stops_replay(self):
        definition = self.definition(enabled=True)
        run = self.service.enqueue(definition["id"], "final-database-failure")
        with self.store.connect() as database:
            database.execute("CREATE TRIGGER routine_final_write_failure BEFORE UPDATE ON routine_runs WHEN OLD.status='running' AND NEW.status IN ('queued','completed') BEGIN SELECT RAISE(ABORT,'injected final write failure'); END")
        with self.assertRaises(sqlite3.Error):
            self.service.tick()
        self.assertEqual(self.procedures.history()["runs"][0]["status"], "completed")
        self.assertEqual(self.service.detail(run["id"])["status"], "running")
        with self.store.connect() as database:
            database.execute("DROP TRIGGER routine_final_write_failure")
        self.service.recover()
        with patch.object(self.procedures, "run", side_effect=AssertionError("Must not replay")) as execute:
            self.assertFalse(self.service.tick())
            execute.assert_not_called()
        self.assertEqual(self.service.detail(run["id"])["status"], "interrupted")

    def test_tampered_prior_output_stops_later_child_without_overwriting_owner_file(self):
        definition = self.definition(enabled=True)
        run = self.service.enqueue(definition["id"], "altered-evidence")
        self.service.tick()
        middle = self.service.detail(run["id"])
        artifact = self.completed_child(middle)["receipt"]["artifacts"][0]
        path = self.registry._path(artifact["path"])
        path.write_bytes(b"owner changed report after publication")
        with patch.object(self.procedures, "run", side_effect=AssertionError("Unverified continuation")) as execute:
            self.service.tick()
            execute.assert_not_called()
        self.assertEqual(self.service.detail(run["id"])["status"], "failed")
        self.assertEqual(path.read_bytes(), b"owner changed report after publication")
        self.assertFalse(self.service.definitions()[0]["enabled"])

    def test_failure_consumes_reservation_and_same_request_never_retries(self):
        definition = self.definition(enabled=True)
        run = self.service.enqueue(definition["id"], "failed-child")
        with patch.object(self.procedures, "run", side_effect=ValueError("Injected child failure")) as execute:
            self.service.tick()
            self.service.enqueue(definition["id"], run["id"])
            self.assertFalse(self.service.tick())
            self.assertEqual(execute.call_count, 1)
        failed = self.service.detail(run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["reserved_bytes"], STEP_RESERVATION_BYTES)
        self.assertFalse(self.service.definitions()[0]["enabled"])

    def test_report_budget_stops_before_child_without_deleting_reports(self):
        definition = self.definition()
        run = self.service.enqueue(definition["id"], "quota-boundary")
        with patch("ariadne.routines.REPORT_BUDGET_BYTES", STEP_RESERVATION_BYTES - 1), patch.object(
                self.procedures, "run", side_effect=AssertionError("Unreserved output")) as execute:
            self.service.tick()
            execute.assert_not_called()
        self.assertEqual(self.service.detail(run["id"])["status"], "failed")
        self.assertEqual(self.service.detail(run["id"])["reserved_bytes"], 0)

    def test_delete_active_definition_cannot_orphan_queued_work(self):
        definition = self.definition()
        run = self.service.enqueue(definition["id"], "protect-definition")
        with self.assertRaises(RoutineConflict):
            self.service.delete(definition["id"])
        self.assertEqual(self.service.detail(run["id"])["status"], "queued")
        self.service.cancel(run["id"])
        self.service.delete(definition["id"])
        self.assertEqual(self.service.definitions(), [])
        self.assertEqual(self.service.detail(run["id"])["status"], "cancelled")

    def test_stale_definition_cannot_reenable_failure_hold_but_reviewed_revision_can(self):
        definition = self.definition(enabled=True)
        run = self.service.enqueue(definition["id"], "failure-before-old-save")
        with patch.object(self.procedures, "run", side_effect=ValueError("Injected substantive failure")):
            self.service.tick()
        held = self.service.definitions()[0]
        self.assertFalse(held["enabled"])
        self.assertGreater(held["revision"], definition["revision"])
        with self.assertRaises(RoutineConflict):
            self.other().save(definition["name"], definition["steps"], 5, True, definition["id"],
                              expected_revision=definition["revision"])
        self.assertFalse(self.service.definitions()[0]["enabled"])
        resumed = self.service.save(held["name"], held["steps"], 5, True, held["id"], expected_revision=held["revision"])
        self.assertTrue(resumed["enabled"])
        self.assertEqual(self.service.detail(run["id"])["status"], "failed")

    def test_two_reviewed_definition_writes_cannot_both_commit_and_revision_is_strict_integer(self):
        definition = self.definition()
        second = self.other()
        barrier = threading.Barrier(2)
        def save(service, name):
            barrier.wait(timeout=5)
            try:
                service.save(name, definition["steps"], 5, False, definition["id"], expected_revision=definition["revision"])
                return "saved"
            except RoutineConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(save, service, name) for service, name in ((self.service, "One"), (second, "Two"))]
            self.assertCountEqual([future.result(timeout=10) for future in futures], ["saved", "conflict"])
        for revision in (True, False, 0, -1, 1.0, "1", [], {}):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.service.save("Invalid revision", definition["steps"], 5, True, definition["id"], expected_revision=revision)

    def test_factory_inventory_preserves_upstream_notice_and_excludes_private_routines(self):
        factory = self.root / "factory-source"
        files = {
            "ariadne/routines.py": b"# source fixture\n",
            "ariadne/static/routines.js": b"// local UI fixture\n",
            "config/erpnext/compose.yaml": b"# adapted upstream compose fixture\n",
            "config/erpnext/images.json": b"{}",
            "config/erpnext/LICENSE": b"Upstream required MIT notice fixture\n",
            "docs/OFFLINE_ROUTINES_0_9.md": b"Local routines documentation\n",
            "data/ariadne.sqlite3": b"private definitions and receipts",
            "data/procedures.sqlite3": b"private procedure history",
            "data/server.token": b"synthetic-not-for-factory-export",
            "data/workspace/procedures/private/report.md": b"owner report",
        }
        for relative, value in files.items():
            destination = factory / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(value)
        from tests.test_box_profiles import public_export_fixture
        public_export_fixture(factory)
        source = Path(__file__).resolve().parent.parent / "scripts/export_offline.py"
        spec = importlib.util.spec_from_file_location("independent_factory_export", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        inventory = {name for name, _, _ in module.inventory(factory)}
        self.assertIn("config/erpnext/LICENSE", inventory)
        self.assertIn("config/erpnext/compose.yaml", inventory)
        self.assertIn("ariadne/routines.py", inventory)
        self.assertIn("ariadne/static/routines.js", inventory)
        self.assertIn("docs/OFFLINE_ROUTINES_0_9.md", inventory)
        self.assertFalse(any(name.startswith("data/") and not name.startswith("data/models/") for name in inventory))

    @staticmethod
    def worst_model_receipt(selection, controls=False):
        parent = "R" * 64
        steps, artifacts = [], []
        for index, item in enumerate(selection):
            child = "routine-" + hashlib.sha256((parent + ":" + str(index) + ":" + item["id"]).encode()).hexdigest()[:48]
            produced = [{"path": "procedures/" + child + "/" + name,
                         "bytes": 8_000_000 // len(item["outputs"]), "sha256": "a" * 64} for name in item["outputs"]]
            artifacts.extend(produced)
            steps.append({"procedure_id": item["id"], "request_id": child,
                          "status": "completed" if index < 3 else "failed", "receipt": {"artifacts": produced}})
        return {"id": parent, "routine_id": "d" * 32, "name": '\\"' * 60, "status": "failed",
                "created_at": 1793232000.123456, "completed_at": 1793232010.123456, "model_calls": 0,
                "error": "Fixed routine step failed; inspect its receipt and any partial output. " + ("\x01" if controls else '"') * 250,
                "steps": steps, "artifacts": artifacts,
                "warnings": [("\x01" if controls else '"') * 997 + str(index).zfill(3) for index in range(100)]}

    def test_model_receipt_bounds_all_four_procedure_combinations_with_escaped_warnings(self):
        definitions = self.procedures.catalog()["procedures"]
        engine = object.__new__(Engine)
        tested, unbounded_maximum = 0, 0
        for selection in itertools.combinations(definitions, 4):
            for controls in (False, True):
                with self.subTest(procedures=[item["id"] for item in selection], controls=controls):
                    saved = self.worst_model_receipt(selection, controls)
                    unbounded_maximum = max(unbounded_maximum, len(json.dumps(saved, ensure_ascii=False)))
                    engine.routines = SimpleNamespace(runs=lambda: [saved])
                    response = engine.execute_internal("routine_read", {"action": "latest"})
                    run = response["run"]
                    size = len(json.dumps(response, ensure_ascii=False))
                    self.assertLess(size, 7600)
                    self.assertEqual(run["request_id"], saved["id"])
                    self.assertNotIn(run["request_id"], [step["request_id"] for step in run["steps"]])
                    self.assertEqual(run["artifact_count"], len(saved["artifacts"]))
                    self.assertEqual(run["artifact_bytes"], sum(artifact["bytes"] for artifact in saved["artifacts"]))
                    self.assertEqual(run["artifacts"], saved["artifacts"])
                    self.assertEqual(sum(step["artifact_count"] for step in run["steps"]), run["artifact_count"])
                    self.assertEqual([step["procedure_id"] for step in run["steps"]], [item["id"] for item in selection])
                    self.assertEqual(run["warnings_total"], 100)
                    self.assertEqual(run["warnings_omitted"], 94)
                    self.assertTrue(run["warnings_truncated"])
                    self.assertTrue(all(len(warning) <= 240 for warning in run["warnings"]))
                    self.assertTrue(all(len(json.dumps(warning, ensure_ascii=False)) <= 240 for warning in run["warnings"]))
                    self.assertLessEqual(len(json.dumps(run["error"], ensure_ascii=False)), 500)
                    self.assertTrue(run["error_truncated"])
                    tested += 1
        self.assertEqual(tested, 140)
        self.assertGreater(unbounded_maximum, 8000)  # Input genuinely requires compacting.

    def test_largest_routine_receipt_reaches_model_as_structured_tool_data_not_preview(self):
        definitions = self.procedures.catalog()["procedures"]
        largest = sorted(definitions, key=lambda row: (len(row["outputs"]), sum(map(len, row["outputs"]))), reverse=True)[:4]
        saved = self.worst_model_receipt(largest, controls=True)
        class Model:
            def __init__(self):
                self.calls = 0
                self.tool_evidence = None
            def chat(self, model, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return {"role": "assistant", "content": "", "tool_calls": [{"function": {
                        "name": "routine_read", "arguments": {"action": "latest"}}}]}, {}
                self.tool_evidence = json.loads(next(message["content"] for message in reversed(messages)
                                                     if message["role"] == "tool"))
                return {"role": "assistant", "content": "Read the saved parent receipt; no fresh verification was requested."}, {}
        model = Model()
        engine = Engine(self.settings, self.store, model)
        mission = self.store.new_run("Read the latest saved local routine receipt", agent_id="general")
        with patch.object(engine.routines, "runs", return_value=[saved]), patch.object(
                engine.routines, "verify", side_effect=AssertionError("No implicit verification")), patch.object(
                engine.routines, "tick", side_effect=AssertionError("Read must not execute")):
            self.store.next_run()
            engine.process(mission["id"])
        self.assertEqual(self.store.run(mission["id"])["status"], "completed")
        self.assertNotIn("preview", model.tool_evidence)
        self.assertNotIn("truncated", model.tool_evidence)
        self.assertEqual(model.tool_evidence["run"]["request_id"], saved["id"])
        self.assertEqual(model.tool_evidence["run"]["artifact_count"], 11)
        self.assertEqual(len(model.tool_evidence["run"]["steps"]), 4)
        self.assertEqual(len(model.tool_evidence["run"]["artifacts"]), 11)


if __name__ == "__main__":
    unittest.main()
