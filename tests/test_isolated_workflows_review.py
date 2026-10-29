"""Independent per-invocation checked-output and frozen-schedule regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.store import Store
from ariadne.verification import verify


class FinalModel:
    def __init__(self):
        self.calls = []

    def chat(self, model, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        return {"role": "assistant", "content": "Local report finished."}, {}


class IsolatedWorkflowReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".isolated-workflow-review-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root, api_token="synthetic-isolated-review", max_steps=3)
        self.store = Store(self.root / "state.sqlite3")
        self.checks = [{"kind": "file_contains", "path": "{{run_dir}}/report.md", "text": "LOCAL REPORT"}]
        self.prompt = "Write {{run_dir}}/report.md then inspect {{run_dir}}/report.md."

    def workflow(self, **kwargs):
        return self.store.workflow_save("Isolated local report", self.prompt, self.checks,
            agent_id="offline_operations", isolated_outputs=True, **kwargs)

    def schedule(self, identifier):
        return next(item for item in self.store.schedules() if item["id"] == identifier)

    def due_now(self, identifier):
        with self.store.connect() as database:
            database.execute("UPDATE schedules SET next_run=0 WHERE id=?", (identifier,))

    def test_two_invocations_materialize_distinct_paths_and_user_context_without_mutating_template(self):
        workflow = self.workflow()
        first, second = (self.store.workflow_run(workflow["id"]) for _ in range(2))
        for run in (first, second):
            self.assertTrue(run["isolated_outputs"])
            self.assertEqual(run["output_prefix"], "missions/" + run["id"])
            self.assertEqual(run["checks"][0]["path"], run["output_prefix"] + "/report.md")
            self.assertEqual(run["prompt"], self.prompt.replace("{{run_dir}}", run["output_prefix"]))
            self.assertEqual(run["context"][-1]["content"], run["prompt"])
            self.assertNotIn("{{run_dir}}", run["prompt"])
        self.assertNotEqual(first["output_prefix"], second["output_prefix"])
        self.assertEqual(self.store.workflow(workflow["id"])["checks"], self.checks)
        self.assertEqual(self.store.workflow(workflow["id"])["prompt"], self.prompt)

    def test_only_literal_prompt_macro_and_path_are_expanded_other_check_values_are_literal(self):
        prompt = "Save {{run_dir}}/report.md; preserve {{other}}, ${HOME}, {run_dir}, and %USERPROFILE%."
        checks = [{"kind": "file_contains", "path": "{{run_dir}}/report.md", "text": "{{run_dir}} {{other}} ${HOME}"}]
        run = self.store.new_run(prompt, checks=checks, isolated_outputs=True)
        self.assertEqual(run["checks"][0]["text"], checks[0]["text"])
        self.assertIn("{{other}}, ${HOME}, {run_dir}, and %USERPROFILE%", run["prompt"])
        self.assertEqual(run["checks"][0]["path"], "missions/" + run["id"] + "/report.md")

    def test_preexisting_previous_report_cannot_satisfy_new_invocation_checks(self):
        workflow = self.workflow()
        first = self.store.workflow_run(workflow["id"])
        model = FinalModel()
        engine = Engine(self.settings, self.store, model)
        path = self.settings.workspace / first["checks"][0]["path"]
        path.parent.mkdir(parents=True)
        path.write_text("LOCAL REPORT", encoding="utf-8")
        self.assertEqual(verify(first["checks"], engine.tools)["status"], "passed")
        self.assertEqual(self.store.next_run()["id"], first["id"])
        engine.process(first["id"])
        self.assertEqual(self.store.run(first["id"])["verification_status"], "passed")
        second = self.store.workflow_run(workflow["id"])
        self.assertEqual(verify(second["checks"], engine.tools)["status"], "failed")
        self.assertEqual(self.store.next_run()["id"], second["id"])
        engine.process(second["id"])
        self.assertEqual(self.store.run(second["id"])["verification_status"], "failed")
        self.assertEqual(path.read_text(encoding="utf-8"), "LOCAL REPORT")
        system = model.calls[-1]["messages"][0]["content"]
        self.assertIn(second["output_prefix"], system)
        self.assertIn("not an OS sandbox", system)

    def test_omitted_workflow_edit_preserves_flag_and_explicit_false_restores_legacy_behavior(self):
        workflow = self.workflow()
        edited = self.store.workflow_save("Renamed", self.prompt, self.checks, workflow["id"])
        self.assertTrue(edited["isolated_outputs"])
        self.assertEqual(edited["agent_id"], "offline_operations")
        self.assertTrue(Store(self.store.path).workflow(workflow["id"])["isolated_outputs"])
        edited = self.store.workflow_save("Legacy", "Keep {{run_dir}} literal", [{"kind": "file_exists", "path": "legacy.md"}],
            workflow["id"], isolated_outputs=False)
        run = self.store.workflow_run(workflow["id"])
        self.assertFalse(edited["isolated_outputs"])
        self.assertFalse(run["isolated_outputs"])
        self.assertIsNone(run["output_prefix"])
        self.assertEqual(run["checks"][0]["path"], "legacy.md")
        self.assertEqual(run["prompt"], "Keep {{run_dir}} literal")

    def test_schedule_freezes_isolation_prompt_checks_and_profile_across_template_edit_and_delete(self):
        workflow = self.workflow()
        schedule = self.store.schedule_add("Frozen isolated report", "Unused", 5, workflow_id=workflow["id"])
        saved = self.schedule(schedule["id"])
        self.assertTrue(saved["isolated_outputs"])
        self.assertEqual(saved["checks"], self.checks)
        self.store.workflow_save("Changed", "Legacy changed prompt", [], workflow["id"],
            agent_id="general", isolated_outputs=False)
        self.store.workflow_delete(workflow["id"])
        self.due_now(schedule["id"])
        Store(self.store.path).due_schedules()
        first = self.store.run(self.schedule(schedule["id"])["last_run_id"])
        self.assertEqual(first["agent_id"], "offline_operations")
        self.assertTrue(first["isolated_outputs"])
        self.assertEqual(first["prompt"], self.prompt.replace("{{run_dir}}", first["output_prefix"]))
        self.store.update_run(first["id"], status="completed", verification_status="passed")
        self.due_now(schedule["id"])
        self.store.due_schedules()
        second = self.store.run(self.schedule(schedule["id"])["last_run_id"])
        self.assertNotEqual(first["output_prefix"], second["output_prefix"])
        self.assertEqual(self.schedule(schedule["id"])["checks"], self.checks)
        self.assertEqual(self.schedule(schedule["id"])["prompt"], self.prompt)

    def test_freeform_isolated_schedule_expands_only_at_child_enqueue(self):
        schedule = self.store.schedule_add("Local draft", self.prompt, 5, checks=self.checks,
            agent_id="offline_developer", isolated_outputs=True)
        self.assertEqual(self.schedule(schedule["id"])["prompt"], self.prompt)
        self.due_now(schedule["id"])
        self.store.due_schedules()
        child = self.store.run(self.schedule(schedule["id"])["last_run_id"])
        self.assertTrue(child["isolated_outputs"])
        self.assertEqual(child["agent_id"], "offline_developer")
        self.assertEqual(child["checks"][0]["path"], child["output_prefix"] + "/report.md")

    def test_invalid_non_boolean_flags_do_not_create_or_modify_state(self):
        workflow = self.workflow()
        for value in (0, 1, "true", "false", [], {}, 0.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.store.workflow_save("Bad edit", self.prompt, self.checks, workflow["id"], isolated_outputs=value)
                with self.assertRaises(ValueError):
                    self.store.new_run(self.prompt, checks=self.checks, isolated_outputs=value)
                with self.assertRaises(ValueError):
                    self.store.schedule_add("Bad", self.prompt, 5, checks=self.checks, isolated_outputs=value)
        self.assertEqual(self.store.workflow(workflow["id"])["name"], "Isolated local report")
        self.assertEqual(self.store.runs(), [])
        self.assertEqual(self.store.schedules(), [])

    def test_traversal_absolute_sibling_and_repeated_macro_check_paths_are_refused(self):
        invalid = ("report.md", "missions/old/report.md", "{{run_dir}}", "{{run_dir}}/", "{{run_dir}}/../escape.md",
            "{{run_dir}}/x/./report.md", "{{run_dir}}//report.md", "{{run_dir}}/x\\..\\escape.md",
            "{{run_dir}}/{{run_dir}}/report.md", "{{run_dir}}/file:stream", "{{run_dir}}/a\x00b")
        for path in invalid:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.store.workflow_save("Unsafe", self.prompt, [{"kind": "file_exists", "path": path}], isolated_outputs=True)
        self.assertEqual(self.store.workflows(), [])
        self.assertEqual(self.store.runs(), [])

    def test_isolation_requires_macro_and_actual_bounded_checks(self):
        for prompt, checks in (("No macro", self.checks), (self.prompt, []), (self.prompt, self.checks * 11),
                               (self.prompt, {}), (self.prompt, [None])):
            with self.subTest(prompt=prompt, checks=checks), self.assertRaises(ValueError):
                self.store.new_run(prompt, checks=checks, isolated_outputs=True)
        self.assertEqual(self.store.runs(), [])

    def test_legacy_rows_migrate_to_false_without_changing_literal_prompt_or_paths(self):
        path = self.root / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as database, database:
            database.executescript("""
                CREATE TABLE workflows(id TEXT PRIMARY KEY,name TEXT,prompt TEXT,checks TEXT,created_at REAL);
                CREATE TABLE schedules(id TEXT PRIMARY KEY,name TEXT,prompt TEXT,interval_minutes INTEGER,
                    enabled INTEGER,next_run REAL,last_run_id TEXT);
                INSERT INTO workflows VALUES('legacy-workflow','Legacy','Keep {{run_dir}} literal','[]',1);
                INSERT INTO schedules VALUES('legacy-schedule','Legacy','Keep {{run_dir}} literal',5,1,0,NULL);
            """)
        migrated = Store(path)
        workflow = migrated.workflow("legacy-workflow")
        self.assertIs(workflow["isolated_outputs"], False)
        migrated.due_schedules()
        child = migrated.run(migrated.runs()[0]["id"])
        self.assertIs(child["isolated_outputs"], False)
        self.assertIsNone(child["output_prefix"])
        self.assertEqual(child["prompt"], "Keep {{run_dir}} literal")

    def test_schedule_publication_failure_rolls_back_realized_child_context_and_due_slot(self):
        schedule = self.store.schedule_add("Atomic isolated report", self.prompt, 5, checks=self.checks, isolated_outputs=True)
        self.due_now(schedule["id"])
        with self.store.connect() as database:
            database.execute("CREATE TRIGGER independent_isolated_failure BEFORE UPDATE ON schedules BEGIN SELECT RAISE(ABORT,'injected publication failure'); END")
        with self.assertRaises(sqlite3.Error):
            self.store.due_schedules()
        self.assertEqual(self.store.runs(), [])
        self.assertIsNone(self.schedule(schedule["id"])["last_run_id"])
        self.assertEqual(self.schedule(schedule["id"])["next_run"], 0)
        with self.store.connect() as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
            database.execute("DROP TRIGGER independent_isolated_failure")
        self.store.due_schedules()
        child = self.store.run(self.schedule(schedule["id"])["last_run_id"])
        self.assertEqual(child["checks"][0]["path"], child["output_prefix"] + "/report.md")

    def test_concurrent_schedulers_publish_one_child_with_one_unique_prefix(self):
        schedule = self.store.schedule_add("Concurrent isolated report", self.prompt, 5, checks=self.checks, isolated_outputs=True)
        self.due_now(schedule["id"])
        barrier = threading.Barrier(2)
        def collect(store):
            barrier.wait(timeout=5)
            store.due_schedules()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [executor.submit(collect, item) for item in (self.store, Store(self.store.path))]
            for result in results:
                result.result(timeout=10)
        self.assertEqual(len(self.store.runs()), 1)
        child = self.store.run(self.store.runs()[0]["id"])
        self.assertEqual(child["output_prefix"], "missions/" + child["id"])
        self.assertEqual(child["id"], self.schedule(schedule["id"])["last_run_id"])


if __name__ == "__main__":
    unittest.main()
