"""Owner-only repository opt-in with stale-edit protection and preserved holds."""
import io
import json
from pathlib import Path
import tempfile
import unittest

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class NoModel:
    def chat(self, *args, **kwargs):
        raise AssertionError("Settings must not run inference")

    def status(self):
        return {"available": False, "models": []}


class RepositoryRadarAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".repo-radar-test-api-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name), api_token="fixture-owner-key")
        self.store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.app = Application(self.settings, self.store, Engine(self.settings, self.store, NoModel()))
        self.addCleanup(self.app.stop_services)

    def call(self, value, token="fixture-owner-key"):
        payload = json.dumps(value).encode()
        env = {"REQUEST_METHOD": "PUT", "PATH_INFO": "/api/briefing/config", "HTTP_HOST": "127.0.0.1:8080",
               "HTTP_ORIGIN": "http://127.0.0.1:8080", "HTTP_AUTHORIZATION": "Bearer " + token,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)), "wsgi.input": io.BytesIO(payload)}
        statuses = []
        result = b"".join(self.app(env, lambda status, headers: statuses.append(status)))
        return int(statuses[0].split()[0]), json.loads(result)

    def test_opt_in_requires_owner_and_does_not_enable_other_channels(self):
        before = self.app.briefing.config()
        code, _ = self.call({"repository_updates": True}, token="wrong-key")
        self.assertEqual(code, 401)
        self.assertEqual(self.app.briefing.config(), before)
        code, result = self.call({"repository_updates": True, "expected_revision": before["revision"]})
        self.assertEqual(code, 200)
        self.assertTrue(result["config"]["repository_updates"])
        self.assertFalse(result["config"]["enabled"])
        self.assertFalse(result["config"]["online_sources"])
        self.assertFalse(self.app.wake_status()["active"])
        self.assertEqual(result["config"]["hour"], 6)

    def test_stale_form_conflict_cannot_replace_the_saved_choice(self):
        revision = self.app.briefing.config()["revision"]
        self.assertEqual(self.call({"repository_updates": True, "expected_revision": revision})[0], 200)
        saved = self.app.briefing.config()
        code, result = self.call({"repository_updates": False, "expected_revision": revision})
        self.assertEqual(code, 409)
        self.assertIn("changed", result["error"])
        self.assertEqual(self.app.briefing.config(), saved)
        for invalid in (True, -1, "0"):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.call({"repository_updates": False, "expected_revision": invalid})[0], 400)
                self.assertEqual(self.app.briefing.config(), saved)

    def test_watch_choice_preserves_global_and_briefing_holds(self):
        self.store.set_setting("paused", True)
        self.app.briefing.configure({"paused": True})
        before = self.app.briefing.config()
        code, result = self.call({"repository_updates": True, "expected_revision": before["revision"]})
        self.assertEqual(code, 200)
        self.assertTrue(result["config"]["paused"])
        self.assertTrue(self.store.setting("paused", False))
        self.assertFalse(result["config"]["enabled"])

    def test_non_object_settings_are_a_client_error_without_mutation(self):
        before = self.app.briefing.config()
        for invalid in (None, True, "settings", []):
            with self.subTest(invalid=invalid):
                code, result = self.call(invalid)
                self.assertEqual(code, 400)
                self.assertIn("object", result["error"])
                self.assertEqual(self.app.briefing.config(), before)


if __name__ == "__main__":
    unittest.main()
