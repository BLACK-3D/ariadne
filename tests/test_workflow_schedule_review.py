"""Independent checks for frozen workflow/specialist schedule authority.

Disposable state only; no real model, network, microphone or owner records.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.agents import resolve_agent
from ariadne.engine import Engine
from ariadne.store import Store


def call(name, args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": args}}]}


class ScriptedModel:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.calls = []

    def chat(self, model, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        return (self.replies.pop(0) if self.replies else
                {"role": "assistant", "content": "Finished local fixture."}), {}


class WorkflowScheduleReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".workflow-schedule-review-", dir=Path.cwd())
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root, api_token="synthetic-schedule-review", max_steps=3)
        self.settings.workspace.mkdir()
        self.store = Store(self.root / "state.sqlite3")

    def tearDown(self):
        self.temporary.cleanup()

    def due_now(self, identifier, store=None):
        with (store or self.store).connect() as database:
            database.execute("UPDATE schedules SET next_run=0 WHERE id=?", (identifier,))

    def schedule(self, identifier):
        return next(row for row in self.store.schedules() if row["id"] == identifier)

    def process(self, engine, identifier):
        claimed = self.store.next_run()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], identifier)
        engine.process(identifier)
        return self.store.details(identifier)

    def test_old_database_rows_migrate_to_general_and_empty_checks(self):
        path = self.root / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as database, database:
            database.executescript("""
                CREATE TABLE workflows(id TEXT PRIMARY KEY,name TEXT,prompt TEXT,checks TEXT,created_at REAL);
                CREATE TABLE schedules(id TEXT PRIMARY KEY,name TEXT,prompt TEXT,interval_minutes INTEGER,
                    enabled INTEGER,next_run REAL,last_run_id TEXT);
                INSERT INTO workflows VALUES('old-workflow','Legacy','Old prompt','[]',1);
                INSERT INTO schedules VALUES('old-schedule','Legacy','Old prompt',5,1,0,NULL);
            """)
        migrated = Store(path)
        self.assertEqual(migrated.workflow("old-workflow")["agent_id"], "general")
        self.assertEqual(migrated.workflow("old-workflow")["checks"], [])
        migrated.due_schedules()
        child = migrated.runs()[0]
        self.assertEqual(child["agent_id"], "general")
        self.assertEqual(migrated.run(child["id"])["checks"], [])

    def test_manual_workflow_keeps_explicit_specialist_and_its_original_checks(self):
        checks = [{"kind": "file_exists", "path": "reports/original.md"}]
        definition = self.store.workflow_save("Local finance", "Transcribe audio", checks, agent_id="business")
        run = self.store.workflow_run(definition["id"])
        self.store.workflow_save("Changed", "Create a PCB", [], definition["id"], agent_id="electronics")
        reopened = Store(self.store.path)
        child = reopened.run(run["id"])
        self.assertEqual(child["agent_id"], "business")
        self.assertEqual(child["prompt"], "Transcribe audio")
        self.assertEqual(child["checks"], checks)
        self.assertEqual(child["verification_status"], "pending")

    def test_auto_workflow_resolves_at_enqueue_and_cannot_reroute_existing_mission(self):
        definition = self.store.workflow_save("Map fixture", "Inspect local GeoJSON", [], agent_id="auto")
        self.assertEqual(definition["agent_id"], "auto")
        first = self.store.workflow_run(definition["id"])
        self.store.workflow_save("Speech fixture", "Transcribe audio", [], definition["id"], agent_id="auto")
        second = self.store.workflow_run(definition["id"])
        self.assertEqual(self.store.run(first["id"])["agent_id"], "geospatial")
        self.assertEqual(self.store.run(second["id"])["agent_id"], "media")

    def test_omitted_workflow_profile_edit_preserves_owner_selection(self):
        workflow = self.store.workflow_save("Finance", "Inspect inventory", [], agent_id="business")
        edited = self.store.workflow_save("Renamed", "Transcribe audio", [], workflow["id"])
        self.assertEqual(edited["agent_id"], "business")
        self.assertEqual(self.store.workflow_run(workflow["id"])["agent_id"], "business")

    def test_workflow_schedule_freezes_all_fields_and_survives_template_deletion(self):
        checks = [{"kind": "json_number", "path": "snapshot.json", "pointer": "/count", "value": "17"}]
        workflow = self.store.workflow_save("Inventory", "Inspect saved local stock", checks, agent_id="business")
        schedule = self.store.schedule_add("Daily inventory", "Caller prompt is not the workflow", 5,
                                           workflow_id=workflow["id"])
        self.store.workflow_save("Different", "Transcribe audio", [], workflow["id"], agent_id="media")
        self.store.workflow_delete(workflow["id"])
        reopened = Store(self.store.path)
        self.due_now(schedule["id"], reopened)
        reopened.due_schedules()
        run = reopened.run(reopened.schedules()[0]["last_run_id"])
        self.assertEqual(run["prompt"], "Inspect saved local stock")
        self.assertEqual(run["checks"], checks)
        self.assertEqual(run["agent_id"], "business")
        self.assertEqual(run["workflow_id"], workflow["id"])

    def test_freeform_schedule_preserves_checks_and_explicit_profile_after_reload(self):
        checks = [{"kind": "file_contains", "path": "analysis.md", "text": "FIXTURE COMPLETE"}]
        schedule = self.store.schedule_add("Local analysis", "Create a PCB", 5, agent_id="data", checks=checks)
        reopened = Store(self.store.path)
        self.due_now(schedule["id"], reopened)
        reopened.due_schedules()
        child = reopened.run(reopened.schedules()[0]["last_run_id"])
        self.assertEqual(child["agent_id"], "data")
        self.assertEqual(child["checks"], checks)
        self.assertEqual(child["prompt"], "Create a PCB")

    def test_auto_schedule_uses_frozen_prompt_even_after_workflow_changed(self):
        workflow = self.store.workflow_save("Map", "Inspect local GeoJSON", [], agent_id="auto")
        schedule = self.store.schedule_add("Map schedule", "Unused", 5, workflow_id=workflow["id"])
        self.store.workflow_save("Voice", "Transcribe audio", [], workflow["id"], agent_id="auto")
        self.due_now(schedule["id"])
        self.store.due_schedules()
        child = self.store.run(self.schedule(schedule["id"])["last_run_id"])
        self.assertEqual(child["agent_id"], "geospatial")
        self.assertEqual(child["prompt"], "Inspect local GeoJSON")

    def test_auto_schedule_freezes_concrete_agent_before_dispatch_rules_change(self):
        workflow = self.store.workflow_save("Map", "Inspect local GeoJSON", [], agent_id="auto")
        schedule = self.store.schedule_add("Frozen agent", "Unused", 5, workflow_id=workflow["id"])
        self.assertEqual(self.schedule(schedule["id"])["agent_id"], "geospatial")
        def different_auto_dispatch(prompt, agent_id=None):
            return "media" if agent_id == "auto" else resolve_agent(prompt, agent_id)
        self.due_now(schedule["id"])
        with patch("ariadne.agents.resolve_agent", side_effect=different_auto_dispatch):
            self.store.due_schedules()
        child = self.store.run(self.schedule(schedule["id"])["last_run_id"])
        self.assertEqual(child["agent_id"], "geospatial")

    def test_unknown_profile_does_not_create_workflow_or_schedule(self):
        for value in ("owner-root", "", True, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.store.workflow_save("Bad", "Prompt", [], agent_id=value)
                with self.assertRaises(ValueError):
                    self.store.schedule_add("Bad", "Prompt", 5, agent_id=value)
        self.assertEqual(self.store.workflows(), [])
        self.assertEqual(self.store.schedules(), [])

    def test_missing_workflow_cannot_create_or_partially_enqueue_schedule(self):
        with self.assertRaises(KeyError):
            self.store.schedule_add("Missing", "Fallback must not run", 5, workflow_id="missing-workflow")
        self.assertEqual(self.store.schedules(), [])
        self.assertEqual(self.store.runs(), [])

    def test_two_store_schedulers_create_one_frozen_child(self):
        workflow = self.store.workflow_save("Stock", "Stock fixture", [], agent_id="business")
        schedule = self.store.schedule_add("Concurrent", "Unused", 5, workflow_id=workflow["id"])
        self.due_now(schedule["id"])
        second = Store(self.store.path)
        barrier = threading.Barrier(2)
        def collect(store):
            barrier.wait(timeout=5)
            store.due_schedules()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [executor.submit(collect, store) for store in (self.store, second)]
            for result in results:
                result.result(timeout=10)
        self.assertEqual(len(self.store.runs()), 1)
        self.assertEqual(self.store.runs()[0]["agent_id"], "business")
        self.assertEqual(self.store.runs()[0]["id"], self.schedule(schedule["id"])["last_run_id"])

    def test_disk_failure_rolls_back_child_and_schedule_advancement_together(self):
        checks = [{"kind": "file_exists", "path": "report.md"}]
        schedule = self.store.schedule_add("Atomic", "Local fixture", 5, agent_id="operations", checks=checks)
        self.due_now(schedule["id"])
        with self.store.connect() as database:
            database.execute("CREATE TRIGGER independent_schedule_disk_failure BEFORE UPDATE ON schedules BEGIN SELECT RAISE(ABORT,'injected disk failure'); END")
        with self.assertRaises(sqlite3.Error):
            self.store.due_schedules()
        self.assertEqual(self.store.runs(), [])
        saved = self.schedule(schedule["id"])
        self.assertIsNone(saved["last_run_id"])
        self.assertEqual(saved["next_run"], 0)
        with self.store.connect() as database:
            database.execute("DROP TRIGGER independent_schedule_disk_failure")
        self.store.due_schedules()
        child = self.store.run(self.schedule(schedule["id"])["last_run_id"])
        self.assertEqual(child["checks"], checks)
        self.assertEqual(child["agent_id"], "operations")

    def test_failed_expected_output_stops_schedule_despite_completed_answer(self):
        checks = [{"kind": "file_exists", "path": "missing-output.md"}]
        workflow = self.store.workflow_save("Expected report", "Produce fixture report", checks, agent_id="documents")
        schedule = self.store.schedule_add("Expected report", "Unused", 5, workflow_id=workflow["id"])
        self.due_now(schedule["id"])
        self.store.due_schedules()
        run_id = self.schedule(schedule["id"])["last_run_id"]
        engine = Engine(self.settings, self.store, ScriptedModel())
        detail = self.process(engine, run_id)
        self.assertEqual(detail["run"]["status"], "completed")
        self.assertEqual(detail["run"]["verification_status"], "failed")
        self.assertFalse(self.schedule(schedule["id"])["enabled"])
        self.due_now(schedule["id"])
        self.store.due_schedules()
        self.assertEqual(len(self.store.runs()), 1)

    def test_scheduled_specialist_cannot_execute_out_of_scope_tool(self):
        schedule = self.store.schedule_add("Scope", "Local finance fixture", 5, agent_id="business")
        self.due_now(schedule["id"])
        self.store.due_schedules()
        run_id = self.schedule(schedule["id"])["last_run_id"]
        model = ScriptedModel([call("run_python", {"code": "raise AssertionError('must not run')"})])
        engine = Engine(self.settings, self.store, model)
        with patch.object(engine.tools, "execute", side_effect=AssertionError("Scope bypass")) as execute:
            detail = self.process(engine, run_id)
            execute.assert_not_called()
        self.assertEqual(detail["run"]["agent_id"], "business")
        self.assertTrue(any("outside the assigned agent" in message.get("content", "") for message in detail["messages"]))

    def test_rejected_scheduled_action_never_executes_and_disables_future_runs(self):
        schedule = self.store.schedule_add("Gated", "Local gated fixture", 5, agent_id="general")
        self.due_now(schedule["id"])
        self.store.due_schedules()
        run_id = self.schedule(schedule["id"])["last_run_id"]
        engine = Engine(self.settings, self.store, ScriptedModel([call("external", {"body": {"fixture": True}})]))
        with patch.object(engine.tools, "policy", return_value="approval"), patch.object(engine.tools, "execute") as execute:
            detail = self.process(engine, run_id)
            self.assertEqual(detail["run"]["status"], "waiting_approval")
            self.store.decide(detail["approvals"][0]["id"], "rejected")
            detail = self.process(engine, run_id)
            execute.assert_not_called()
        self.assertFalse(self.schedule(schedule["id"])["enabled"])
        self.due_now(schedule["id"])
        self.store.due_schedules()
        self.assertEqual(len(self.store.runs()), 1)

    def test_waiting_approval_prevents_schedule_overlap_and_restart_replay(self):
        schedule = self.store.schedule_add("Waiting", "Approval fixture", 5, agent_id="general")
        self.due_now(schedule["id"])
        self.store.due_schedules()
        run_id = self.schedule(schedule["id"])["last_run_id"]
        engine = Engine(self.settings, self.store, ScriptedModel([call("external", {})]))
        with patch.object(engine.tools, "policy", return_value="approval"), patch.object(engine.tools, "execute") as execute:
            self.process(engine, run_id)
            self.store.recover()
            self.due_now(schedule["id"])
            self.store.due_schedules()
            execute.assert_not_called()
        self.assertEqual(len(self.store.runs()), 1)
        self.assertEqual(self.store.run(run_id)["status"], "waiting_approval")

    def test_full_queue_keeps_due_frozen_schedule_without_partial_child(self):
        for number in range(32):
            self.store.new_run("Existing fixture " + str(number))
        workflow = self.store.workflow_save("Report", "Frozen fixture", [], agent_id="operations")
        schedule = self.store.schedule_add("Pending", "Unused", 5, workflow_id=workflow["id"])
        self.due_now(schedule["id"])
        self.store.due_schedules()
        self.assertEqual(len(self.store.runs()), 32)
        self.assertIsNone(self.schedule(schedule["id"])["last_run_id"])
        self.assertEqual(self.schedule(schedule["id"])["next_run"], 0)
        self.store.cancel(self.store.runs()[0]["id"])
        self.store.due_schedules()
        child = self.store.run(self.schedule(schedule["id"])["last_run_id"])
        self.assertEqual(child["agent_id"], "operations")
        self.assertEqual(child["prompt"], "Frozen fixture")


if __name__ == "__main__":
    unittest.main()
