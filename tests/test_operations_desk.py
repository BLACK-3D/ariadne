"""Native owner API, offline examples and privilege boundaries for the desk."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ariadne.agents import resolve_agent
from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class NoModel:
    def chat(self, *args, **kwargs):
        raise AssertionError("Operations must not call a model")

    def status(self):
        return {"available": False, "models": []}


class OperationsDeskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".operations-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name), api_token="fixture-owner-key")
        self.store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.app = Application(self.settings, self.store, Engine(self.settings, self.store, NoModel()))
        self.addCleanup(self.app.stop_services)

    def call(self, value=None, *, token="fixture-owner-key", method="POST", origin="http://127.0.0.1:8080"):
        payload = json.dumps(value).encode()
        env = {"REQUEST_METHOD": method, "PATH_INFO": "/api/operations" + ("/run" if method == "POST" else ""),
               "HTTP_HOST": "127.0.0.1:8080", "HTTP_ORIGIN": origin, "HTTP_AUTHORIZATION": "Bearer " + token,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)), "wsgi.input": io.BytesIO(payload)}
        statuses = []
        result = b"".join(self.app(env, lambda status, headers: statuses.append(status)))
        return int(statuses[0].split()[0]), json.loads(result)

    def test_owner_and_same_origin_required_before_any_operation(self):
        value = {"example": "business", "request_id": "auth-example"}
        self.assertEqual(self.call(value, token="wrong")[0], 401)
        self.assertEqual(self.call(value, origin="https://untrusted.example")[0], 403)
        self.assertFalse((self.settings.workspace / "operation-examples").exists())

    def test_bounded_catalog_does_not_probe_or_infer_connection(self):
        with mock.patch("socket.socket", side_effect=AssertionError("No network")):
            code, result = self.call(method="GET")
        self.assertEqual(code, 200)
        self.assertEqual(result["model_calls"], 0)
        controls = next(row for row in result["tools"] if row["id"] == "home_control")
        self.assertNotIn("apply", controls["actions"])
        self.assertNotIn("refresh", controls["actions"])

    def test_operations_assets_are_served_before_pairing(self):
        for name, mime in (("operations.js", "text/javascript"), ("operations.css", "text/css")):
            rows = []
            env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/static/" + name, "HTTP_HOST": "127.0.0.1:8080"}
            content = b"".join(self.app(env, lambda status, headers: rows.append((status, dict(headers)))))
            self.assertEqual(rows[0][0], "200 OK")
            self.assertTrue(rows[0][1]["Content-Type"].startswith(mime))
            self.assertEqual(content, (self.app.static / name).read_bytes())

    def test_synthetic_examples_run_without_network_or_models_and_preserve_source_hashes(self):
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")), mock.patch("socket.socket", side_effect=AssertionError("No sockets")), mock.patch("subprocess.Popen", side_effect=AssertionError("No model or external program")):
            for example in ("business", "market"):
                code, result = self.call({"example": example, "request_id": "example-" + example})
                self.assertEqual(code, 200, result)
                self.assertTrue(result["ok"], result)
                self.assertTrue(result["example"])
                self.assertEqual(result["model_calls"], 0)
                self.assertTrue(result["artifacts"])
                for row in result["artifacts"]:
                    self.assertTrue(self.app.engine.tools._path(row["path"]).is_file())
                    self.assertEqual(len(row["sha256"]), 64)
                self.assertEqual(self.call({"example": example, "request_id": "example-" + example})[0], 400)

    def test_pause_holds_reports_and_examples(self):
        self.store.set_setting("paused", True)
        self.assertEqual(self.call({"example": "business", "request_id": "paused-demo"})[0], 400)
        self.assertEqual(self.call({"tool": "business_ops", "arguments": {"action": "snapshot", "request_id": "paused-snapshot"}})[0], 400)
        self.assertFalse((self.settings.workspace / "operation-examples").exists())

    def test_no_generic_dispatch_or_device_action_bypass_and_malformed_inputs_are_400(self):
        values = [None, [], "bad", {"tool": [], "arguments": {}}, {"tool": "business_ops", "arguments": []},
                  {"tool": "business_ops", "arguments": {"action": []}},
                  {"tool": "home_control", "arguments": {"action": "apply", "action_id": "light", "request_id": "unsafe"}},
                  {"tool": "home_control", "arguments": {"action": "refresh"}},
                  {"tool": "file_write", "arguments": {"path": "x", "content": "x"}},
                  {"example": "business", "request_id": "../outside"},
                  {"example": "business", "request_id": "x", "overwrite": True}]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(self.call(value)[0], 400)

    def test_registry_profiles_discover_new_tools_without_widening_offline_profiles(self):
        names = {s["function"]["name"] for s in self.app.engine.tools.schemas()}
        self.assertTrue({"business_ops", "market_research", "home_control"} <= names)
        self.assertIs(self.app.engine.tools.local_controls.store, self.store)
        self.assertEqual(resolve_agent("audit an OHLCV day trading strategy", "auto"), "market_research")
        self.assertEqual(resolve_agent("show Home Assistant device status", "auto"), "local_controls")
        offline = self.app.engine.agents.profile("market_research")
        self.assertTrue(offline.offline)
        self.assertNotIn("web_fetch", offline.tools)

    def test_new_workflows_seed_once_without_enabling_schedules(self):
        from ariadne.capabilities import seed_workflows
        before = len(self.store.workflows())
        seed_workflows(self.store)
        self.assertEqual(len(self.store.workflows()), before)
        self.assertEqual(self.store.schedules(), [])
        self.assertIn("Historical strategy qualification", {row["name"] for row in self.store.workflows()})

    def test_packaged_examples_are_exact_public_fixture_copies(self):
        from ariadne import operations
        package = Path(operations.__file__).parent / "resources/operations"
        config = Path(operations.__file__).parent.parent / "config/examples"
        for name in ("business-erpnext-demo.json", "market-demo.csv", "market-demo.json"):
            self.assertEqual((package / name).read_bytes(), (config / name).read_bytes())
        import tomllib
        metadata = tomllib.loads((config.parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertTrue({"resources/market/*", "resources/operations/*"} <= set(metadata["tool"]["setuptools"]["package-data"]["ariadne"]))


if __name__ == "__main__":
    unittest.main()
