"""Independent API and lifecycle checks for the agent/voice/briefing integration."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application, HTTPError
from ariadne.store import Store


class Model:
    def status(self):
        return {"available": True, "models": []}

    def chat(self, *args):
        raise AssertionError("These integration checks must not generate model output")


class AgentIntegrationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".agent-integration-review-")
        self.settings = Settings(data_dir=Path(self.temp.name), api_token="private-fixture")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.engine = Engine(self.settings, self.store, Model())
        self.app = Application(self.settings, self.store, self.engine)

    def tearDown(self):
        self.app.stop_services()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, token="private-fixture", origin=None):
        parsed = urlsplit(path)
        payload = json.dumps(body or {}).encode()
        env = {"HTTP_HOST": "localhost:8080", "PATH_INFO": parsed.path,
               "QUERY_STRING": parsed.query, "REQUEST_METHOD": method, "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(payload)), "wsgi.input": io.BytesIO(payload)}
        if token:
            env["HTTP_AUTHORIZATION"] = "Bearer " + token
        if origin:
            env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            response["status"] = int(status.split()[0])
        result = b"".join(self.app(env, start))
        return response["status"], json.loads(result)

    def test_new_read_routes_preserve_authentication_and_origin_boundaries(self):
        paths = ("/api/agents", "/api/briefing", "/api/briefing/history", "/api/briefing/report?request_id=missing",
                 "/api/voice/wake")
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, token=None)[0], 401)
                self.assertEqual(self.request(path, origin="https://other.example")[0], 403)

    def test_unauthenticated_or_foreign_origin_cannot_start_a_microphone(self):
        with patch.object(self.app.wake_voice, "start") as start:
            self.assertEqual(self.request("/api/voice/wake/start", "POST", {"mode": "pc"}, token=None)[0], 401)
            self.assertEqual(self.request("/api/voice/wake/start", "POST", {"mode": "pc"}, origin="https://other.example")[0], 403)
        start.assert_not_called()
        self.assertFalse(self.app.wake_voice.status()["active"])

    def test_api_persists_explicit_and_automatic_specialist_assignments(self):
        status, response = self.request("/api/runs", "POST", {"prompt": "Make a CAD mounting plate", "agent_id": "auto"})
        self.assertEqual(status, 201)
        self.assertEqual(response["run"]["agent_id"], "mechanical")
        self.assertNotIn("prompt", response["run"])
        status, response = self.request("/api/runs", "POST", {"prompt": "Make a CAD mounting plate", "agent_id": "research"})
        self.assertEqual(status, 201)
        self.assertEqual(response["run"]["agent_id"], "research")
        count = len(self.store.runs())
        self.assertEqual(self.request("/api/runs", "POST", {"prompt": "Do a task", "agent_id": "administrator"})[0], 400)
        self.assertEqual(len(self.store.runs()), count)

    def test_pause_prevents_manual_brief_generation_and_voice_enqueue(self):
        self.store.set_setting("paused", True)
        with patch.object(self.app.briefing, "run") as run:
            self.assertEqual(self.request("/api/briefing/run", "POST", {"request_id": "paused"})[0], 409)
        run.assert_not_called()
        with self.assertRaises(ValueError):
            self.app.submit_voice("What is the weather?")
        self.assertEqual(self.store.runs(), [])

    def test_report_api_and_model_provider_reject_tampered_cached_bytes(self):
        receipt = self.app.briefing.run("review-proof")
        artifact = next(item for item in receipt["artifacts"] if item["path"].endswith("brief.md"))
        status, report = self.request("/api/briefing/report?request_id=review-proof")
        self.assertEqual(status, 200)
        self.assertEqual(report["sha256"], artifact["sha256"])
        self.assertEqual(report["bytes"], artifact["bytes"])
        self.assertTrue(self.engine.execute_internal("briefing_read", {"action": "latest"})["ok"])
        (self.settings.workspace / artifact["path"]).write_text("Replaced forecast; do not trust this.", encoding="utf-8")
        self.assertEqual(self.request("/api/briefing/report?request_id=review-proof")[0], 409)
        with self.assertRaises(HTTPError) as error:
            self.engine.execute_internal("briefing_read", {"action": "latest"})
        self.assertEqual(error.exception.status, 409)

    def test_provider_does_not_verify_then_reopen_via_the_generic_file_tool(self):
        self.app.briefing.run("single-read-proof")
        with patch.object(self.engine.tools, "execute", side_effect=AssertionError("A second generic read would permit a verification race")):
            report = self.engine.execute_internal("briefing_read", {"action": "latest"})
        self.assertTrue(report["ok"])
        self.assertIn("sha256", report)
        self.assertIn("content", report)

    def test_service_cleanup_stops_briefing_even_if_microphone_cleanup_fails(self):
        with patch.object(self.app.wake_voice, "stop", side_effect=RuntimeError("injected teardown failure")), \
             patch.object(self.app.briefing, "stop") as stop:
            with self.assertRaises(RuntimeError):
                self.app.stop_services()
        stop.assert_called_once()

    def test_main_cleans_up_engine_when_auxiliary_service_startup_fails(self):
        from ariadne import __main__ as main_module
        app = Mock()
        app.start_services.side_effect = ValueError("injected service startup failure")
        with patch("sys.argv", ["ariadne", "serve"]), \
             patch("ariadne.box_profiles.bootstrap_profiles"), \
             patch("ariadne.network_policy.install_network_policy"), \
             patch.object(main_module, "load_settings", return_value=self.settings), \
             patch.object(main_module, "Application", return_value=app), \
             patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main_module.main()
        self.assertEqual(error.exception.code, 1)
        app.stop_services.assert_called_once()
        app.engine.stop.assert_called_once()

    def test_main_still_stops_engine_if_auxiliary_cleanup_itself_fails(self):
        from ariadne import __main__ as main_module
        app = Mock()
        app.start_services.side_effect = ValueError("injected startup failure")
        app.stop_services.side_effect = RuntimeError("injected cleanup failure")
        with patch("sys.argv", ["ariadne", "serve"]), \
             patch("ariadne.box_profiles.bootstrap_profiles"), \
             patch("ariadne.network_policy.install_network_policy"), \
             patch.object(main_module, "load_settings", return_value=self.settings), \
             patch.object(main_module, "Application", return_value=app), \
             patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(RuntimeError):
                main_module.main()
        app.engine.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
