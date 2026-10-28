"""The local model receives real clock evidence instead of guessing artifact dates."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.store import Store
from ariadne.capabilities import seed_workflows, SURVIVAL_STARTERS


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
