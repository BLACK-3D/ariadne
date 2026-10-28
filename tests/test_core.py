import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class FakeModel:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def status(self):
        return {"available": True, "models": [{"name": "qwen3:4b", "size": 100}]}

    def chat(self, model, messages, tools):
        self.calls.append(messages)
        result = self.responses.pop(0) if self.responses else {"role": "assistant", "content": "Finished."}
        if callable(result):
            result = result()
        return result, {"prompt_tokens": 10, "output_tokens": 20, "duration_seconds": 0.1}


def tool(name, args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".core-test-", dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.settings = Settings(data_dir=self.root, api_token="test-token", max_steps=4)
        self.settings.workspace.mkdir()
        self.store = Store(self.root / "state.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def engine(self, responses=None):
        return Engine(self.settings, self.store, FakeModel(responses))

    def process(self, engine, run):
        self.assertEqual(self.store.next_run()["id"], run["id"])
        engine.process(run["id"])
        return self.store.details(run["id"])

    def test_real_workspace_tool_loop_persisted(self):
        engine = self.engine([tool("file_write", {"path": "business/plan.md", "content": "Actual plan"}), {"role": "assistant", "content": "Created business/plan.md."}])
        run = self.store.new_run("Create a business plan")
        detail = self.process(engine, run)
        self.assertEqual(detail["run"]["status"], "completed")
        self.assertEqual((self.settings.workspace / "business/plan.md").read_text(), "Actual plan")
        self.assertEqual(detail["run"]["step_count"], 2)
        restored = Store(self.root / "state.sqlite3")
        self.assertEqual(restored.run(run["id"])["status"], "completed")
        self.assertTrue(any(e["kind"] == "tool_result" for e in detail["events"]))

    def test_approval_persists_then_executes_exactly_once(self):
        engine = self.engine([tool("external", {"body": {"action": "create"}})])
        run = self.store.new_run("Create in an external system")
        with patch.object(engine.tools, "policy", return_value="approval"), patch.object(engine.tools, "execute", return_value={"ok": True}) as execute:
            detail = self.process(engine, run)
            self.assertEqual(detail["run"]["status"], "waiting_approval")
            execute.assert_not_called()
            approval = detail["approvals"][0]
            self.store.decide(approval["id"], "approved")
            self.process(engine, run)
            execute.assert_called_once_with("external", {"body": {"action": "create"}})
            with self.assertRaises(ValueError):
                self.store.decide(approval["id"], "approved")
        self.assertEqual(self.store.run(run["id"])["status"], "completed")

    def test_reject_does_not_execute(self):
        engine = self.engine([tool("external", {})])
        run = self.store.new_run("Do a gated action")
        with patch.object(engine.tools, "policy", return_value="approval"), patch.object(engine.tools, "execute") as execute:
            detail = self.process(engine, run)
            self.store.decide(detail["approvals"][0]["id"], "rejected")
            detail = self.process(engine, run)
            execute.assert_not_called()
        self.assertIn("Owner rejected", detail["messages"][-2]["content"])

    def test_cancel_during_model_request_never_executes_result(self):
        run = self.store.new_run("Will be cancelled")
        def cancel():
            self.store.cancel(run["id"])
            return tool("file_write", {"path": "must-not-exist", "content": "bad"})
        engine = self.engine([cancel])
        detail = self.process(engine, run)
        self.assertEqual(detail["run"]["status"], "cancelled")
        self.assertFalse((self.settings.workspace / "must-not-exist").exists())
        self.store.update_run(run["id"], status="completed")
        self.assertEqual(self.store.run(run["id"])["status"], "cancelled")

    def test_restart_interrupts_uncertain_actions_without_replay(self):
        run = self.store.new_run("External action")
        self.store.next_run()
        approval = self.store.approval(run["id"], "1:0", "external", {})
        self.store.approval_status(approval["id"], "executing")
        self.store.recover()
        self.assertEqual(self.store.run(run["id"])["status"], "interrupted")
        self.assertEqual(self.store.details(run["id"])["approvals"][0]["status"], "uncertain")
        self.assertIsNone(self.store.next_run())

    def test_step_budget_bounds_loop(self):
        engine = self.engine([tool("calculate", {"expression": "2+2"})] * 10)
        run = self.store.new_run("Keep calculating")
        detail = self.process(engine, run)
        self.assertEqual(detail["run"]["status"], "exhausted")
        self.assertEqual(len(engine.model.calls), 4)

    def test_pause_queues_without_model_call(self):
        engine, run = self.engine(), self.store.new_run("Wait")
        self.store.set_setting("paused", True)
        detail = self.process(engine, run)
        self.assertEqual(detail["run"]["status"], "queued")
        self.assertEqual(engine.model.calls, [])

    def test_memory_and_knowledge_fts_with_untrusted_queries(self):
        memory = self.store.memory_add("Owner prefers local analytics")
        document = self.store.document_add("Business notes", "Ariadne inventory workflow and warehouse stock")
        self.assertEqual(self.store.search('analytics\" OR *')[0]["id"], memory["id"])
        self.assertEqual(self.store.search("warehouse", knowledge=True)[0]["title"], "Business notes")
        self.store.document_delete(document["id"])
        self.store.memory_delete(memory["id"])
        self.assertEqual(self.store.search("analytics"), [])
        self.assertEqual(self.store.search("warehouse", knowledge=True), [])

    def test_schedule_does_not_overlap_or_catch_up(self):
        schedule = self.store.schedule_add("Routine", "Generate report", 5)
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET next_run=?", (time.time() - 50000,))
        self.store.due_schedules()
        self.assertEqual(len(self.store.runs()), 1)
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET next_run=0")
        self.store.due_schedules()
        self.assertEqual(len(self.store.runs()), 1)

    def test_full_queue_does_not_deadlock_worker(self):
        for i in range(32):
            self.store.new_run(f"Queued {i}")
        self.store.schedule_add("Due", "Schedule", 5)
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET next_run=0")
        engine = self.engine()
        original = engine.process
        def first(run_id):
            original(run_id)
            engine.stopping.set()
        engine.process = first
        engine.loop()
        self.assertEqual(sum(r["status"] == "completed" for r in self.store.runs()), 1)

    def test_schedule_enqueue_and_advancement_rollback_together(self):
        self.store.schedule_add("Crash test", "Create a report", 5)
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET next_run=0")
            db.execute("CREATE TRIGGER simulate_disk_failure BEFORE UPDATE ON schedules BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END")
        with self.assertRaises(Exception):
            self.store.due_schedules()
        self.assertEqual(self.store.runs(), [])
        with self.store.connect() as db:
            db.execute("DROP TRIGGER simulate_disk_failure")
        self.store.due_schedules()
        self.store.recover()
        self.store.due_schedules()
        self.assertEqual(len(self.store.runs()), 1)


class APITests(unittest.TestCase):
    setUp = CoreTests.setUp
    tearDown = CoreTests.tearDown
    engine = CoreTests.engine
    def request(self, path, method="GET", data=None, token="test-token", origin=None, host="localhost:8080"):
        if not hasattr(self, "app"):
            self.app = Application(self.settings, self.store, self.engine())
        body = json.dumps(data or {}).encode()
        env = {"HTTP_HOST": host, "REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        if token:
            env["HTTP_AUTHORIZATION"] = "Bearer " + token
        if origin:
            env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            response["status"], response["headers"] = int(status.split()[0]), dict(headers)
        raw = b"".join(self.app(env, start))
        response["data"] = json.loads(raw) if response["headers"]["Content-Type"].startswith("application/json") else raw
        return response

    def test_auth_origin_host_and_csp(self):
        self.assertEqual(self.request("/api/status", token=None)["status"], 401)
        self.assertEqual(self.request("/api/status", token="wrong")["status"], 401)
        self.assertEqual(self.request("/api/status", origin="https://evil.example")["status"], 403)
        self.assertEqual(self.request("/api/status", host="evil.example")["status"], 403)
        good = self.request("/api/status", origin="http://localhost:8080")
        self.assertEqual(good["status"], 200)
        self.assertIn("frame-ancestors 'none'", good["headers"]["Content-Security-Policy"])
        self.assertNotIn("test-token", json.dumps(good["data"]))

    def test_run_api_and_knowledge(self):
        run = self.request("/api/runs", "POST", {"prompt": "Create a report"})
        self.assertEqual(run["status"], 201)
        self.assertNotIn("context", run["data"]["run"])
        detail = self.request("/api/runs/" + run["data"]["run"]["id"])
        self.assertEqual(detail["status"], 200)
        self.assertEqual(detail["data"]["messages"][0]["content"], "Create a report")
        self.assertEqual(self.request("/api/runs", "POST", {"prompt": ""})["status"], 400)
        self.assertEqual(self.request("/api/knowledge", "POST", {"title": "Guide", "content": "Inventory"})["status"], 201)
        self.assertEqual(len(self.request("/api/knowledge")["data"]["documents"]), 1)

    def test_settings_only_installed_models_and_schedule_validation(self):
        self.assertEqual(self.request("/api/settings", "POST", {"model": "not-installed"})["status"], 400)
        self.assertEqual(self.request("/api/settings", "POST", {"model": "qwen3:4b"})["status"], 200)
        self.assertEqual(self.request("/api/schedules", "POST", {"name": "x", "prompt": "x", "interval_minutes": 0})["status"], 400)
        self.assertEqual(self.request("/api/control", "POST", {"paused": "false"})["status"], 400)


if __name__ == "__main__":
    unittest.main()
