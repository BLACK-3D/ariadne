"""Exercise owner API boundaries and real offline reports without inference."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class NoModel:
    def status(self):
        return {"available": False, "models": []}

    def chat(self, *args, **kwargs):
        raise AssertionError("Offline report routines must never call a model")


class RoutineAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".core-test-", dir=Path.cwd())
        self.settings = Settings(data_dir=Path(self.temp.name), api_token="routine-api-test-only")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.engine = Engine(self.settings, self.store, NoModel())
        self.app = Application(self.settings, self.store, self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, path, method="GET", data=None, token=True, origin=None):
        body = json.dumps({} if data is None else data).encode("utf-8")
        env = {"HTTP_HOST": "localhost:8080", "REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        if token:
            env["HTTP_AUTHORIZATION"] = "Bearer routine-api-test-only"
        if origin:
            env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            response.update(status=int(status.split()[0]), headers=dict(headers))
        raw = b"".join(self.app(env, start))
        response["data"] = json.loads(raw) if response["headers"]["Content-Type"].startswith("application/json") else raw
        return response

    def create(self, **extra):
        response = self.request("/api/routines", "POST", {"name": "Offline handoff", "steps": ["business_snapshot"], **extra})
        self.assertEqual(response["status"], 201, response)
        return response["data"]["routine"]

    def test_routine_api_requires_owner_auth_and_same_origin(self):
        self.assertEqual(self.request("/api/routines", token=False)["status"], 401)
        self.assertEqual(self.request("/api/routines", "POST", {}, token=False)["status"], 401)
        self.assertEqual(self.request("/api/routines", "POST", {}, origin="https://other.example")["status"], 403)
        self.assertEqual(self.request("/api/routines")["data"]["routines"], [])
        self.assertEqual(self.request("/api/status")["data"]["routines"]["active_runs"], 0)

    def test_local_script_is_exact_bytes_and_existing_csp_is_preserved(self):
        response = self.request("/static/routines.js", token=False)
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["data"], (Path(__file__).resolve().parents[1] / "ariadne/static/routines.js").read_bytes())
        self.assertIn("script-src 'self'", response["headers"]["Content-Security-Policy"])
        self.assertEqual(response["headers"]["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.request("/static/other.js", token=False)["status"], 404)

    def test_manual_report_waits_for_pause_then_publishes_verified_artifacts(self):
        routine = self.create()
        self.assertFalse(routine["enabled"])
        self.store.set_setting("paused", True)
        path = "/api/routines/" + routine["id"] + "/run"
        launched = self.request(path, "POST", {"request_id": "reviewed-report"})
        self.assertEqual(launched["status"], 201)
        self.assertFalse(self.engine.routines.tick())
        self.assertEqual(self.request("/api/routines/runs/reviewed-report")["data"]["run"]["status"], "queued")
        self.store.set_setting("paused", False)
        with patch("socket.create_connection", side_effect=AssertionError("No network")), patch("subprocess.Popen", side_effect=AssertionError("No process")):
            self.assertTrue(self.engine.routines.tick())
        verified = self.request("/api/routines/runs/reviewed-report/verify", "POST")
        self.assertEqual(verified["status"], 200, verified)
        run = verified["data"]["run"]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["model_calls"], 0)
        self.assertTrue(run["artifacts_verified"])
        for artifact in run["artifacts"]:
            data = (self.settings.workspace / artifact["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), artifact["sha256"])
        repeated = self.request(path, "POST", {"request_id": "reviewed-report"})
        self.assertEqual(repeated["data"]["run"]["id"], run["id"])
        self.assertFalse(self.engine.routines.tick())

    def test_invalid_payloads_unknown_receipts_and_active_delete_fail_closed(self):
        self.assertEqual(self.request("/api/routines", "POST", {"name": "x", "steps": ["run_python"]})["status"], 400)
        self.assertEqual(self.request("/api/routines", "POST", {"name": "x", "steps": ["business_snapshot"], "shell": "x"})["status"], 400)
        self.assertEqual(self.request("/api/routines/runs/unknown")["status"], 404)
        routine = self.create()
        path = "/api/routines/" + routine["id"]
        self.assertEqual(self.request(path + "/run", "POST", {"request_id": "../escape"})["status"], 400)
        self.assertEqual(self.request(path + "/run", "POST", {"request_id": "pending"})["status"], 201)
        self.assertEqual(self.request(path, "DELETE")["status"], 409)
        cancelled = self.request("/api/routines/runs/pending/cancel", "POST")
        self.assertEqual(cancelled["data"]["run"]["status"], "cancelled")
        self.assertEqual(self.request(path, "DELETE")["status"], 200)

    def test_explicit_integrity_failure_holds_recurrence_without_rewriting_history(self):
        routine = self.create(enabled=True)
        self.request("/api/routines/" + routine["id"] + "/run", "POST", {"request_id": "integrity"})
        self.engine.routines.tick()
        run = self.engine.routines.detail("integrity")
        (self.settings.workspace / run["artifacts"][0]["path"]).write_text("altered", encoding="utf-8")
        self.assertEqual(self.request("/api/routines/runs/integrity/verify", "POST")["status"], 400)
        self.assertFalse(self.engine.routines.definitions()[0]["enabled"])
        self.assertEqual(self.engine.routines.detail("integrity")["status"], "completed")

    def test_workflow_schedule_api_freezes_selected_specialist_and_checks(self):
        created = self.request("/api/workflows", "POST", {"name": "Checked analysis", "prompt": "Analyze local CSV data", "agent_id": "data", "checks": [{"kind": "file_exists", "path": "analysis.md"}]})
        self.assertEqual(created["status"], 201)
        workflow = created["data"]["workflow"]
        response = self.request("/api/schedules", "POST", {"name": "Saved analysis", "workflow_id": workflow["id"], "interval_minutes": 1440})
        self.assertEqual(response["status"], 201)
        self.request("/api/workflows/" + workflow["id"], "PUT", {"name": "Changed", "prompt": "Changed", "checks": []})
        saved = self.request("/api/schedules")["data"]["schedules"][0]
        self.assertEqual(saved["agent_id"], "data")
        self.assertEqual(saved["checks"], workflow["checks"])
        self.assertEqual(saved["prompt"], workflow["prompt"])
        self.assertEqual(self.request("/api/schedules", "POST", {"name": "Ambiguous", "workflow_id": workflow["id"], "interval_minutes": 5, "prompt": "override"})["status"], 400)

    def test_stale_config_cannot_undo_a_hold_or_another_owner_edit(self):
        routine = self.create(enabled=True)
        path = "/api/routines/" + routine["id"]
        self.request(path + "/run", "POST", {"request_id": "cancelled-before-start"})
        self.request("/api/routines/runs/cancelled-before-start/cancel", "POST")
        body = {key: routine[key] for key in ("name", "steps", "interval_minutes", "enabled")}
        self.assertEqual(self.request(path, "PUT", body)["status"], 400)
        body["expected_revision"] = routine["revision"]
        self.assertEqual(self.request(path, "PUT", body)["status"], 409)
        held = self.engine.routines.definitions()[0]
        self.assertFalse(held["enabled"])
        body["expected_revision"] = held["revision"]
        updated = self.request(path, "PUT", body)
        self.assertEqual(updated["status"], 200, updated)
        self.assertTrue(updated["data"]["routine"]["enabled"])

    def test_routine_read_tool_never_executes_or_verifies_and_validates_arguments(self):
        with patch.object(self.engine.routines, "tick", side_effect=AssertionError("No execution")), patch.object(self.engine.routines, "verify", side_effect=AssertionError("No implicit verification")):
            self.assertEqual(self.engine.execute_internal("routine_read", {"action": "status"})["active_runs"], 0)
            self.assertIsNone(self.engine.execute_internal("routine_read", {"action": "latest"})["run"])
            for args in ({"action": []}, {"action": "run"}, {"action": "latest", "extra": True}, None):
                self.assertFalse(self.engine.execute_internal("routine_read", args)["ok"])

    def test_model_receipt_keeps_parent_identity_all_steps_and_exact_totals_under_budget(self):
        routine = self.create(steps=["readiness_report", "stock_rotation", "power_budget", "rebuilding_plan"])
        self.engine.routines.enqueue(routine["id"], "parent-evidence-request")
        for _ in range(4):
            self.engine.routines.tick()
        saved = self.engine.routines.detail("parent-evidence-request")
        response = self.engine.execute_internal("routine_read", {"action": "latest"})
        encoded = json.dumps(response, ensure_ascii=False)
        self.assertLess(len(encoded), 7600)
        self.assertNotIn("preview", response)
        self.assertEqual(response["run"]["request_id"], saved["id"])
        self.assertEqual(response["run"]["artifact_count"], len(saved["artifacts"]))
        self.assertEqual(response["run"]["artifact_bytes"], sum(row["bytes"] for row in saved["artifacts"]))
        self.assertEqual(len(response["run"]["steps"]), 4)
        self.assertEqual(sum(row["artifact_count"] for row in response["run"]["steps"]), len(saved["artifacts"]))
        self.assertEqual(response["run"]["artifacts"], saved["artifacts"])
        with patch.object(self.engine.routines, "runs", return_value=[{**saved, "warnings": ["x" * 1000] * 100}]):
            bounded = self.engine.execute_internal("routine_read", {"action": "latest"})
        self.assertLess(len(json.dumps(bounded, ensure_ascii=False)), 7600)
        self.assertTrue(bounded["run"]["warnings_truncated"])
        self.assertEqual(bounded["run"]["warnings_omitted"], 94)


if __name__ == "__main__":
    unittest.main()
