"""The local model receives real clock evidence instead of guessing artifact dates."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.store import Store
from ariadne.capabilities import seed_workflows, SURVIVAL_STARTERS, OFFLINE_STARTERS
from ariadne.verification import validate_checks

INITIAL_HANDOFF = "Use routine_read with action changes to review the latest completed offline routine against its compatible prior invocation. Save {{run_dir}}/handoff.md and {{run_dir}}/evidence.json using real file tools, then read both back. Include exact parent invocation IDs, child source paths and SHA256 identities from the comparison; separate substantive changes from metadata timestamps. Report first-run, unavailable, incomparable or partial evidence honestly, including omissions and lower-bound counts. Do not infer current physical conditions from saved reports or execute a new routine. State missing evidence instead of inventing values. Work entirely from local evidence and take no external action."


class ClockModel:
    def __init__(self):
        self.messages = None

    def chat(self, model, messages, tools):
        self.messages = messages
        return {"role":"assistant","content":"No source date was recorded."}, {}


class WorkflowGroundingTests(unittest.TestCase):
    def test_authoritative_clock_is_in_model_context_and_source_dates_stay_distinct(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".clock-test-") as directory:
            settings = Settings(data_dir=Path(directory),api_token="qualification")
            store = Store(settings.data_dir/"ariadne.sqlite3")
            model = ClockModel()
            engine = Engine(settings,store,model)
            run = store.new_run("Draft a dated handoff, do not guess observation dates.")
            self.assertEqual(store.next_run()["id"],run["id"])
            before = datetime.now(timezone.utc)
            engine.process(run["id"])
            system = model.messages[0]["content"]
            value = system.split("Authoritative server time (UTC): ",1)[1].splitlines()[0]
            observed = datetime.fromisoformat(value)
            self.assertLessEqual(before,observed)
            self.assertLessEqual(observed,datetime.now(timezone.utc))
            self.assertIn("generation time is distinct",system)

    def test_new_workflow_templates_seed_once_and_preserve_owner_edits(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(),prefix=".workflow-seed-") as directory:
            store = Store(Path(directory)/"ariadne.sqlite3")
            seed_workflows(store)
            workflow = next(w for w in store.workflows() if w["name"] == "Business operations handoff")
            self.assertIn("never invent a timestamp",workflow["prompt"])
            store.workflow_save(workflow["name"],"Owner custom instructions",workflow["checks"],workflow["id"])
            before = len(store.workflows())
            seed_workflows(store)
            self.assertEqual(len(store.workflows()),before)
            self.assertEqual(store.workflow(workflow["id"])["prompt"],"Owner custom instructions")
            self.assertEqual(len(SURVIVAL_STARTERS),3)

    def test_offline_starters_enqueue_checked_paths_with_their_intended_local_scope(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".offline-starters-") as directory:
            settings = Settings(data_dir=Path(directory), api_token="starter-fixture")
            store = Store(settings.data_dir / "ariadne.sqlite3")
            engine = Engine(settings, store, ClockModel())
            seed_workflows(store)
            for item in OFFLINE_STARTERS:
                workflow = next(row for row in store.workflows() if row["name"] == item["name"])
                self.assertTrue(workflow["isolated_outputs"])
                self.assertTrue(engine.agents.profile(workflow["agent_id"]).offline)
                self.assertEqual(validate_checks(workflow["checks"], engine.tools), workflow["checks"])
                first, second = store.workflow_run(workflow["id"]), store.workflow_run(workflow["id"])
                self.assertEqual(first["agent_id"], item["agent_id"])
                self.assertNotEqual(first["output_prefix"], second["output_prefix"])
                self.assertTrue(all(check["path"].startswith(first["output_prefix"]+"/") for check in first["checks"]))
                self.assertNotIn("{{run_dir}}", first["prompt"])
                self.assertIn("{{run_dir}}", store.workflow(workflow["id"])["prompt"])
            edited = next(row for row in store.workflows() if row["name"] == OFFLINE_STARTERS[0]["name"])
            store.workflow_save(edited["name"], "Owner notes {{run_dir}}/handoff.md", edited["checks"], edited["id"])
            seed_workflows(store)
            self.assertTrue(store.workflow(edited["id"])["isolated_outputs"])
            self.assertEqual(store.workflow(edited["id"])["prompt"], "Owner notes {{run_dir}}/handoff.md")

    def test_initial_default_prompt_updates_without_rewriting_mission_or_schedule_snapshots(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".starter-migrate-") as directory:
            store = Store(Path(directory)/"state.sqlite3")
            seed_workflows(store)
            template = OFFLINE_STARTERS[0]
            workflow = next(row for row in store.workflows() if row["name"] == template["name"])
            old = store.workflow_save(workflow["name"], INITIAL_HANDOFF, workflow["checks"][:2], workflow["id"])
            run = store.workflow_run(old["id"])
            store.schedule_add("Frozen original definition", "unused", 1440, workflow_id=old["id"])
            schedules = store.schedules()
            with store.connect() as db:
                db.execute("DELETE FROM settings WHERE key='starter_workflows_0_10_bounded'")
            seed_workflows(store)
            updated = store.workflow(old["id"])
            self.assertEqual(updated, {**old, "prompt": template["prompt"], "checks": template["checks"]})
            self.assertEqual(store.run(run["id"]), run)
            self.assertEqual(store.schedules(), schedules)
            seed_workflows(store)
            self.assertEqual(store.workflow(old["id"]), updated)

    def test_initial_prompt_migration_preserves_each_kind_of_owner_definition_edit(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".starter-owner-") as directory:
            store = Store(Path(directory)/"state.sqlite3")
            seed_workflows(store)
            template = OFFLINE_STARTERS[0]
            variants = [{"prompt": INITIAL_HANDOFF+" Owner addition."}, {"name": template["name"]+" owner"},
                        {"checks": [{"kind": "file_contains", "path": "{{run_dir}}/handoff.md", "text": "Owner decision"}]},
                        {"agent_id": "general"}, {"isolated_outputs": False}]
            saved = [store.workflow_save(**{"name": template["name"], "prompt": INITIAL_HANDOFF,
                        "checks": template["checks"][:2], "agent_id": template["agent_id"], "isolated_outputs": True, **variant})
                     for variant in variants]
            with store.connect() as db:
                db.execute("DELETE FROM settings WHERE key='starter_workflows_0_10_bounded'")
            count = len(store.workflows())
            seed_workflows(store)
            self.assertEqual(len(store.workflows()), count)
            for original in saved:
                self.assertEqual(store.workflow(original["id"]), original)
