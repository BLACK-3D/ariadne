"""Owner API and existing scheduler integration for accepted operation plans."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class NoModel:
    def chat(self, *args, **kwargs): raise AssertionError("No inference in operation workflows")
    def status(self): return {"available": False, "models": []}


class OperationWorkflowsAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".operations-workflows-test-api-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name), api_token="fixture-owner-key")
        self.store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.app = Application(self.settings, self.store, Engine(self.settings, self.store, NoModel()))
        self.addCleanup(self.app.stop_services)

    def api(self, path="routines", value=None, method="POST", token="fixture-owner-key"):
        payload = json.dumps(value).encode()
        env = {"REQUEST_METHOD": method, "PATH_INFO": "/api/" + path, "HTTP_HOST": "127.0.0.1:8080",
               "HTTP_ORIGIN": "http://127.0.0.1:8080", "HTTP_AUTHORIZATION": "Bearer " + token,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)), "wsgi.input": io.BytesIO(payload)}
        statuses = []
        result = b"".join(self.app(env, lambda status, headers: statuses.append(status)))
        return int(statuses[0].split()[0]), json.loads(result)

    def create(self, steps=None):
        steps = steps or [{"operation": "business_snapshot", "arguments": {}}, {"operation": "device_status", "arguments": {}}]
        code, result = self.api(value={"name": "Local operation watch", "steps": steps, "enabled": False})
        self.assertEqual(code, 201, result)
        return result["routine"]

    def drain(self, request_id):
        for _ in range(10):
            result = self.app.routines.detail(request_id)
            if result["status"] not in {"queued", "running"}: return result
            self.app.routines.tick()
        self.fail("Bounded workflow did not reach a terminal receipt")

    def test_typed_owner_routes_share_existing_pause_and_no_model_lifecycle(self):
        code, result = self.api(value={"name":"No authority", "steps":[{"operation":"device_status","arguments":{}}]}, token="wrong")
        self.assertEqual(code, 401)
        routine = self.create()
        self.assertFalse(routine["enabled"])
        self.store.set_setting("paused", True)
        code, result = self.api("routines/" + routine["id"] + "/run", {"request_id":"held-owner-run"})
        self.assertEqual(code, 201, result)
        self.assertFalse(self.app.routines.tick())
        self.assertEqual(self.app.routines.detail("held-owner-run")["status"], "queued")
        self.store.set_setting("paused", False)
        with mock.patch("socket.socket", side_effect=AssertionError("No network")), mock.patch("subprocess.Popen", side_effect=AssertionError("No process/model")):
            run = self.drain("held-owner-run")
        self.assertEqual(run["status"], "completed", run)
        self.assertEqual(run["model_calls"], 0)
        self.assertEqual(len(run["steps"]), 2)
        self.assertEqual(self.app.routines.verify(run["id"])["status"], "completed")
        self.assertFalse(self.app.routines.definitions()[0]["enabled"])

    def test_market_inputs_are_accepted_once_and_source_edits_do_not_change_run(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("market-demo.csv", "market-demo.json"):
            self.app.engine.tools._file_write({"path":"markets/" + name,"content":(root / "config/examples" / name).read_text(encoding="utf-8")})
        args = {"path":"markets/market-demo.csv", "metadata_path":"markets/market-demo.json"}
        routine = self.create([{"operation":"market_audit","arguments":args}, {"operation":"market_study","arguments":args}])
        original = hashlib.sha256(self.app.engine.tools._path(args["path"]).read_bytes()).hexdigest()
        code, _ = self.api("routines/" + routine["id"] + "/run", {"request_id":"accepted-market-run"})
        self.assertEqual(code, 201)
        self.app.engine.tools._file_write({"path":args["path"],"content":"bad source after acceptance","overwrite":True})
        with mock.patch("socket.socket", side_effect=AssertionError("No network")), mock.patch("subprocess.Popen", side_effect=AssertionError("No process")):
            run = self.drain("accepted-market-run")
        self.assertEqual(run["status"], "completed", run)
        for artifact in run["artifacts"]:
            if artifact["path"].startswith("reports/market-research/"):
                report = json.loads(self.app.engine.tools._path(artifact["path"]).read_text(encoding="utf-8"))
                self.assertEqual(report["inputs"]["csv"]["sha256"], original)

    def test_api_cannot_schedule_device_dispatch_and_stale_edits_do_not_enable(self):
        for kind in ("apply", "refresh", "home_control", "broker_order"):
            self.assertEqual(self.api(value={"name":"Rejected", "steps":[{"operation":kind,"arguments":{}}]})[0], 400)
        routine = self.create()
        value = {key:routine[key] for key in ("name", "steps", "interval_minutes")}
        value.update(enabled=True, expected_revision=routine["revision"] + 1)
        self.assertEqual(self.api("routines/" + routine["id"], value, method="PUT")[0], 409)
        self.assertFalse(self.app.routines.definitions()[0]["enabled"])

    def test_new_workflow_asset_is_available_without_pairing(self):
        rows = []
        content = b"".join(self.app({"REQUEST_METHOD":"GET", "PATH_INFO":"/static/operations_workflows.js", "HTTP_HOST":"127.0.0.1:8080"}, lambda status, headers: rows.append(status)))
        self.assertEqual(rows, ["200 OK"])
        self.assertEqual(content, (self.app.static / "operations_workflows.js").read_bytes())


if __name__ == "__main__": unittest.main()
