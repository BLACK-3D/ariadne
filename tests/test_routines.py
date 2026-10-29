"""Deterministic local routines: durable boundaries, budgets and owner control."""
import hashlib
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from ariadne.config import Settings
from ariadne.procedures import Procedures
from ariadne.routines import Routines, RoutineConflict, STEP_RESERVATION_BYTES
from ariadne.store import Store
from ariadne.tools import ToolRegistry


class RoutineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".tools-test-routines-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(data_dir=Path(self.temporary.name)/"data")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.procedures = Procedures(self.registry, self.store)
        self.service = Routines(self.procedures, self.store)

    def definition(self, steps=None, enabled=False, name="Local review"):
        return self.service.save(name, steps or ["business_snapshot"], enabled=enabled)

    def make_due(self, definition):
        with self.service.database() as db:
            db.execute("UPDATE routine_definitions SET next_run=1 WHERE id=?", (definition["id"],))

    def complete(self, request_id):
        for _ in range(4):
            if self.service.detail(request_id)["status"] not in {"queued", "running"}:
                break
            self.service.tick()
        return self.service.detail(request_id)

    def test_three_original_templates_are_opt_in_and_only_fixed_procedures(self):
        catalog = self.service.catalog()
        self.assertEqual(len(catalog["templates"]), 3)
        self.assertEqual(len(catalog["procedures"]), 8)
        self.assertEqual(self.service.definitions(), [])
        self.assertEqual(self.service.runs(), [])
        self.assertEqual(self.service.status()["active_runs"], 0)
        for template in catalog["templates"]:
            definition = self.definition(template["steps"], name=template["name"])
            self.assertFalse(definition["enabled"])
            self.assertIsNone(definition["next_run"])

    def test_full_household_plan_writes_verified_reports_without_models_or_network(self):
        steps = ["readiness_report", "stock_rotation", "power_budget", "rebuilding_plan"]
        definition = self.definition(steps)
        self.service.enqueue(definition["id"], "household")
        with mock.patch("subprocess.Popen", side_effect=AssertionError("No child process")), mock.patch("socket.create_connection", side_effect=AssertionError("No network")), mock.patch("urllib.request.urlopen", side_effect=AssertionError("No public data")):
            for index in range(4):
                self.assertTrue(self.service.tick())
                run = self.service.detail("household")
                self.assertEqual(sum(step["status"] == "completed" for step in run["steps"]), index+1)
        run = self.service.verify("household")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["model_calls"], 0)
        self.assertTrue(run["artifacts_verified"])
        self.assertEqual(len(run["artifacts"]), 10)
        self.assertEqual(run["reserved_bytes"], sum(row["bytes"] for row in run["artifacts"]))
        for artifact in run["artifacts"]:
            self.assertEqual(hashlib.sha256(self.registry._path(artifact["path"]).read_bytes()).hexdigest(), artifact["sha256"])
        self.assertFalse(self.service.tick())

    def test_same_request_returns_original_plan_and_never_reruns(self):
        definition = self.definition()
        self.service.enqueue(definition["id"], "stable")
        first = self.complete("stable")
        with mock.patch.object(self.procedures, "run", side_effect=AssertionError("No replay")):
            again = self.service.enqueue(definition["id"], "stable")
            self.assertEqual(again["artifacts"], first["artifacts"])
            self.assertFalse(self.service.tick())
        self.service.save("Changed plan", ["power_budget"], routine_id=definition["id"])
        with self.assertRaises(RoutineConflict):
            self.service.enqueue(definition["id"], "stable")

    def test_due_does_not_catch_up_or_overlap_and_two_instances_claim_one_slot(self):
        definition = self.definition(enabled=True)
        self.make_due(definition)
        second = Routines(self.procedures, self.store)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda service: service.due(), (self.service, second)))
        self.assertEqual(sum(claimed), 1)
        self.assertEqual(len(self.service.runs()), 1)
        self.assertEqual(self.service.due(), 0)
        fresh = self.service.definitions()[0]
        self.assertGreater(fresh["next_run"], time.time())
        self.make_due(fresh)
        self.assertEqual(self.service.due(), 0)  # Active invocation still owns the routine.

    def test_pause_blocks_due_and_child_execution_without_discarding_manual_queue(self):
        definition = self.definition(enabled=True)
        self.make_due(definition)
        self.store.set_setting("paused", True)
        self.assertEqual(self.service.due(), 0)
        self.service.enqueue(definition["id"], "paused")
        with mock.patch.object(self.procedures, "run", side_effect=AssertionError("Paused")):
            self.assertFalse(self.service.tick())
        self.assertEqual(self.service.detail("paused")["status"], "queued")
        self.store.set_setting("paused", False)
        self.assertTrue(self.service.tick())

    def test_owner_edit_does_not_replace_an_already_queued_manual_plan(self):
        definition = self.definition(["business_snapshot", "power_budget"])
        queued = self.service.enqueue(definition["id"], "frozen")
        self.service.save("Replacement", ["knowledge_inventory"], routine_id=definition["id"])
        completed = self.complete("frozen")
        self.assertEqual(completed["plan"], queued["plan"])
        self.assertEqual([step["procedure_id"] for step in completed["steps"]], ["business_snapshot", "power_budget"])

    def test_disabling_definition_cancels_scheduled_work_but_manual_runs_remain_explicit(self):
        definition = self.definition(["business_snapshot", "power_budget"], enabled=True)
        self.make_due(definition)
        self.service.due()
        request_id = self.service.runs()[0]["id"]
        self.service.save(definition["name"], definition["steps"], enabled=False, routine_id=definition["id"])
        self.assertEqual(self.service.detail(request_id)["status"], "cancelled")
        self.assertFalse(self.service.tick())
        self.service.enqueue(definition["id"], "explicit-manual")
        self.assertEqual(self.complete("explicit-manual")["status"], "completed")

    def test_cancel_during_child_preserves_its_report_and_stops_remaining_steps(self):
        definition = self.definition(["business_snapshot", "power_budget"], enabled=True)
        self.service.enqueue(definition["id"], "cancel-running")
        started, release = threading.Event(), threading.Event()
        original = self.procedures.run
        def blocked(*args):
            started.set()
            self.assertTrue(release.wait(10))
            return original(*args)
        with mock.patch.object(self.procedures, "run", side_effect=blocked) as worker, ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.service.tick)
            self.assertTrue(started.wait(10))
            self.assertEqual(self.service.status()["reserved_bytes"], STEP_RESERVATION_BYTES)
            self.assertTrue(self.service.status()["running"])
            cancelled = self.service.cancel("cancel-running")
            self.assertTrue(cancelled["cancel_requested"])
            release.set()
            self.assertTrue(pending.result(15))
            self.assertEqual(worker.call_count, 1)
        run = self.service.detail("cancel-running")
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual([step["status"] for step in run["steps"]], ["completed", "cancelled"])
        self.assertTrue(run["artifacts"])
        self.assertLess(run["reserved_bytes"], STEP_RESERVATION_BYTES)
        self.assertFalse(self.service.definitions()[0]["enabled"])

    def test_restart_interrupts_started_plans_but_preserves_never_started_queue(self):
        first = self.definition(["business_snapshot", "power_budget"])
        second = self.definition(["knowledge_inventory"], name="Never started")
        self.service.enqueue(first["id"], "started")
        self.service.enqueue(second["id"], "untouched")
        self.service.tick()
        reopened = Routines(self.procedures, self.store)
        self.assertEqual(reopened.recover(), 1)
        self.assertEqual(reopened.detail("started")["status"], "interrupted")
        self.assertEqual(reopened.detail("untouched")["status"], "queued")
        self.assertEqual(reopened.recover(), 0)

    def test_crash_after_publication_never_adopts_or_replays_child(self):
        definition = self.definition(enabled=True)
        self.service.enqueue(definition["id"], "crashed")
        original = self.procedures.run
        def crash(*args):
            original(*args)
            raise SystemExit("Simulated power loss before parent commit")
        with mock.patch.object(self.procedures, "run", side_effect=crash), self.assertRaises(SystemExit):
            self.service.tick()
        self.assertEqual(self.service.detail("crashed")["status"], "running")
        self.assertEqual(self.service.status()["reserved_bytes"], STEP_RESERVATION_BYTES)
        reopened = Routines(self.procedures, self.store)
        reopened.recover()
        self.assertEqual(reopened.detail("crashed")["status"], "interrupted")
        with mock.patch.object(self.procedures, "run", side_effect=AssertionError("No replay")):
            self.assertFalse(reopened.tick())
            self.assertEqual(reopened.enqueue(definition["id"], "crashed")["status"], "interrupted")

    def test_failed_child_keeps_worst_case_charge_and_disables_recurrence(self):
        definition = self.definition(enabled=True)
        self.service.enqueue(definition["id"], "failed")
        with mock.patch.object(self.procedures, "run", side_effect=ValueError("Uncertain partial output")):
            self.assertTrue(self.service.tick())
        self.assertEqual(self.service.detail("failed")["status"], "failed")
        self.assertEqual(self.service.status()["reserved_bytes"], STEP_RESERVATION_BYTES)
        self.assertFalse(self.service.definitions()[0]["enabled"])
        self.assertEqual(self.service.enqueue(definition["id"], "failed")["status"], "failed")

    def test_forty_tiny_verified_successes_use_actual_bytes_instead_of_worst_case(self):
        definition = self.definition()
        for index in range(40):
            request = "tiny-"+str(index)
            self.service.enqueue(definition["id"], request)
            self.assertTrue(self.service.tick())
            self.assertEqual(self.service.detail(request)["status"], "completed")
        self.assertLess(self.service.status()["reserved_bytes"], 1_000_000)
        self.assertEqual(len(self.service.runs()), 40)

    def test_budget_exhaustion_stops_before_another_child_without_deleting_files(self):
        definition = self.definition()
        self.service.enqueue(definition["id"], "within")
        self.service.tick()
        existing = self.service.detail("within")["artifacts"][0]
        self.service.enqueue(definition["id"], "over-budget")
        with mock.patch("ariadne.routines.REPORT_BUDGET_BYTES", STEP_RESERVATION_BYTES), mock.patch.object(self.procedures, "run", side_effect=AssertionError("Over budget")):
            self.assertTrue(self.service.tick())
        self.assertEqual(self.service.detail("over-budget")["status"], "failed")
        self.assertTrue(self.registry._path(existing["path"]).is_file())

    def test_modified_prior_child_stops_plan_before_any_next_child(self):
        definition = self.definition(["business_snapshot", "power_budget"], enabled=True)
        self.service.enqueue(definition["id"], "tampered")
        self.service.tick()
        artifact = self.service.detail("tampered")["artifacts"][0]
        self.registry._path(artifact["path"]).write_bytes(b"Owner edited this report")
        with mock.patch.object(self.procedures, "run", side_effect=AssertionError("Do not continue altered chain")):
            self.assertTrue(self.service.tick())
        self.assertEqual(self.service.detail("tampered")["status"], "failed")
        self.assertFalse(self.service.definitions()[0]["enabled"])

    def test_explicit_failed_verification_disables_future_schedule_but_preserves_generation(self):
        definition = self.definition(enabled=True)
        self.service.enqueue(definition["id"], "verify-later")
        self.service.tick()
        original = self.service.detail("verify-later")
        self.registry._path(original["artifacts"][0]["path"]).unlink()
        revision = self.service.status()["revision"]
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.service.verify("verify-later")
        self.assertFalse(self.service.definitions()[0]["enabled"])
        self.assertTrue(self.service.definitions()[0]["last_error"])
        self.assertGreater(self.service.status()["revision"], revision)
        self.assertEqual(self.service.detail("verify-later"), original)

    def test_strict_validation_and_caps_preserve_existing_definition(self):
        definition = self.definition()
        for fields in ({"steps": []}, {"steps": ["business_snapshot"]*2}, {"steps": ["web_fetch"]}, {"interval_minutes": True}, {"interval_minutes": 4}, {"enabled": 1}, {"name": ""}):
            args = {"name": "Valid", "steps": ["business_snapshot"], **fields}
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.service.save(**args)
        with mock.patch("ariadne.routines.MAX_DEFINITIONS", 1), self.assertRaises(ValueError):
            self.definition(name="Too many")
        self.service.enqueue(definition["id"], "active")
        with self.assertRaises(RoutineConflict):
            self.service.delete(definition["id"])
        self.service.cancel("active")
        self.service.delete(definition["id"])
        self.assertEqual(self.service.definitions(), [])
        with self.assertRaises(KeyError):
            self.service.detail("missing")

    def test_queue_backpressure_and_history_exhaustion_do_not_launch_or_retry_reports(self):
        first = self.definition(name="First")
        second = self.definition(name="Second")
        self.service.enqueue(first["id"], "first-queued")
        with mock.patch("ariadne.routines.MAX_QUEUE", 1), self.assertRaisesRegex(ValueError, "queue"):
            self.service.enqueue(second["id"], "second-queued")
        self.assertEqual(len(self.service.runs()), 1)
        self.complete("first-queued")
        scheduled = self.service.save("Second", second["steps"], enabled=True, routine_id=second["id"])
        self.make_due(scheduled)
        with mock.patch("ariadne.routines.MAX_RUNS", 1):
            self.assertEqual(self.service.due(), 0)
        current = next(row for row in self.service.definitions() if row["id"] == second["id"])
        self.assertFalse(current["enabled"])
        self.assertIn("history is full", current["last_error"])

    def test_stale_owner_edit_cannot_overwrite_an_edit_or_reenable_after_failure(self):
        initial = self.definition(enabled=True)
        edited = self.service.save("Reviewed edit", initial["steps"], enabled=True,
            routine_id=initial["id"], expected_revision=initial["revision"])
        with self.assertRaises(RoutineConflict) as conflict:
            self.service.save("Stale owner edit", initial["steps"], enabled=True,
                routine_id=initial["id"], expected_revision=initial["revision"])
        self.assertEqual(conflict.exception.status, "conflict")
        self.assertEqual(self.service.definitions()[0], edited)
        self.service.enqueue(initial["id"], "failure-hold")
        with mock.patch.object(self.procedures, "run", side_effect=ValueError("Substantive failure")):
            self.service.tick()
        held = self.service.definitions()[0]
        self.assertFalse(held["enabled"])
        self.assertGreater(held["revision"], edited["revision"])
        with self.assertRaises(RoutineConflict):
            self.service.save(edited["name"], edited["steps"], enabled=True,
                routine_id=held["id"], expected_revision=edited["revision"])
        self.assertEqual(self.service.definitions()[0], held)
        resumed = self.service.save(held["name"], held["steps"], enabled=True,
            routine_id=held["id"], expected_revision=held["revision"])
        self.assertTrue(resumed["enabled"])
        self.assertGreater(resumed["revision"], held["revision"])
        for invalid in (True, -1, "1", 1.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.service.save(resumed["name"], resumed["steps"], enabled=True,
                    routine_id=resumed["id"], expected_revision=invalid)


if __name__ == "__main__":
    unittest.main()
