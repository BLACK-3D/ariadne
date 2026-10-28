"""Independent privacy checks for persistent wake consent and location APIs."""
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class Model:
    def status(self):
        return {"available": True, "models": []}

    def chat(self, *args):
        raise AssertionError("Privacy integration checks must not use a model")


class LocationWakeReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".location-wake-review-")
        self.settings = Settings(data_dir=Path(self.temp.name), api_token="privacy-fixture")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.engine = Engine(self.settings, self.store, Model())
        self.app = Application(self.settings, self.store, self.engine)

    def tearDown(self):
        self.app.stop_services()
        self.temp.cleanup()

    def request(self, path, method="GET", data=None, token="privacy-fixture", origin=None):
        path = urlsplit(path)
        payload = json.dumps(data or {}).encode()
        env = {"HTTP_HOST": "localhost:8080", "PATH_INFO": path.path, "QUERY_STRING": path.query,
               "REQUEST_METHOD": method, "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)),
               "wsgi.input": io.BytesIO(payload)}
        if token:
            env["HTTP_AUTHORIZATION"] = "Bearer " + token
        if origin:
            env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            response.update(status=int(status.split()[0]), headers=dict(headers))
        body = b"".join(self.app(env, start))
        return response["status"], json.loads(body), response["headers"]

    def browser_position(self, age_seconds=0):
        return {"latitude": 38.6, "longitude": -90.2, "accuracy_meters": 204,
                "timestamp_ms": (datetime.now(timezone.utc)-timedelta(seconds=age_seconds)).timestamp()*1000}

    def test_constructing_app_never_starts_microphone_or_detects_location(self):
        self.store.set_setting("wake_preferences", {"enabled": True, "mode": "pc", "session_hours": None})
        with patch("ariadne.wake.WakeService.start") as start, \
             patch("ariadne.location.detect_windows_location") as detect:
            Application(self.settings, self.store, self.engine)
        start.assert_not_called()
        detect.assert_not_called()

    def test_fresh_box_and_nonboolean_preferences_do_not_autostart_capture(self):
        for preference in ({}, {"enabled": False}, {"enabled": "true"}, {"enabled": 1}, []):
            self.store.set_setting("wake_preferences", preference)
            with patch.object(self.app.briefing, "start"), patch.object(self.app.wake_voice, "start") as start:
                self.app.start_services()
            start.assert_not_called()

    def test_saved_explicit_optin_autostarts_only_pc_until_stopped(self):
        self.store.set_setting("wake_preferences", {"enabled": True, "mode": "pc", "session_hours": None})
        with patch.object(self.app.briefing, "start"), patch.object(self.app.wake_voice, "start") as start:
            self.app.start_services()
        start.assert_called_once_with(mode="pc", session_hours=None)

    def test_remembered_api_start_requires_explicit_valid_consent_and_successful_start(self):
        with patch.object(self.app.wake_voice, "start") as start:
            status, response, _ = self.request("/api/voice/wake/start", "POST", {"mode": "pc", "remember": True})
        self.assertEqual(status, 200)
        start.assert_called_once_with(mode="pc", session_hours=None)
        self.assertTrue(response["auto_start"])
        self.assertIs(self.store.setting("wake_preferences")["enabled"], True)
        self.store.set_setting("wake_preferences", {"enabled": False})
        with patch.object(self.app.wake_voice, "start", side_effect=ValueError("No microphone available")):
            self.assertEqual(self.request("/api/voice/wake/start", "POST", {"remember": True})[0], 400)
        self.assertFalse(self.store.setting("wake_preferences")["enabled"])

    def test_transient_start_does_not_enable_reboot_capture_and_invalid_consent_is_rejected(self):
        with patch.object(self.app.wake_voice, "start") as start:
            self.assertEqual(self.request("/api/voice/wake/start", "POST", {"mode": "pc"})[0], 200)
        start.assert_called_once_with(mode="pc", session_hours=8)
        self.assertFalse(self.store.setting("wake_preferences")["enabled"])
        for body in ({"remember": "true"}, {"mode": "browser", "remember": True}, {"remember": True, "session_hours": 8}):
            with self.subTest(body=body), patch.object(self.app.wake_voice, "start") as start:
                self.assertEqual(self.request("/api/voice/wake/start", "POST", body)[0], 400)
            start.assert_not_called()

    def test_normal_service_shutdown_preserves_optin_but_spoken_stop_revokes_it(self):
        self.store.set_setting("wake_preferences", {"enabled": True, "mode": "pc", "session_hours": None})
        self.app.wake_voice.start("browser", session_hours=None)  # No hardware capture in tests.
        self.app.stop_services()
        self.assertTrue(self.store.setting("wake_preferences")["enabled"])
        self.app.wake_voice.start("browser", session_hours=None)
        result = self.app.wake_voice.accept("Ariadne stop listening", source="browser", lease=self.app.wake_voice.lease, sequence=1)
        self.assertEqual(result["event"], "stopped")
        self.assertFalse(self.app.wake_voice.active)
        self.assertFalse(self.store.setting("wake_preferences")["enabled"])
        self.assertEqual(self.store.runs(), [])

    def test_ui_stop_stops_capture_even_if_saving_optout_fails(self):
        self.store.set_setting("wake_preferences", {"enabled": True, "mode": "pc", "session_hours": None})
        self.app.wake_voice.start("browser", session_hours=None)
        with patch.object(self.app, "disable_wake_startup", side_effect=OSError("injected preference storage failure")), \
             patch("ariadne.server.logging.getLogger"):
            status, response, _ = self.request("/api/voice/wake/stop", "POST")
        self.assertIn(status, {400, 500})
        self.assertIn("error", response)
        self.assertFalse(self.app.wake_voice.active)

    def test_preference_storage_failure_after_start_stops_capture(self):
        with patch.object(self.store, "set_setting", side_effect=OSError("injected preference storage failure")), \
             patch("ariadne.server.logging.getLogger"):
            status, _, _ = self.request("/api/voice/wake/start", "POST", {"mode": "browser"})
        self.assertIn(status, {400, 500})
        self.assertFalse(self.app.wake_voice.active)
        self.assertIsNone(self.store.setting("wake_preferences", None))

    def test_location_routes_remain_authenticated_same_origin_and_reads_never_detect(self):
        paths = (("/api/location", "GET"), ("/api/location/detect", "POST"), ("/api/location/browser", "POST"))
        with patch("ariadne.location.detect_windows_location") as detect:
            for path, method in paths:
                with self.subTest(path=path):
                    self.assertEqual(self.request(path, method, token=None)[0], 401)
                    self.assertEqual(self.request(path, method, origin="https://foreign.example")[0], 403)
            status, value, _ = self.request("/api/location")
            self.assertEqual(self.request("/api/location/detect", "POST", {"url": "https://unrelated.example"})[0], 400)
        detect.assert_not_called()
        self.assertEqual(status, 200)
        self.assertIsNone(value["last_device_location"])
        self.assertFalse(value["city_reference"]["device_location"])

    def test_explicit_native_detection_saves_only_fresh_success_without_changing_region(self):
        original = self.app.briefing.config()["region"]
        position = {"ok": True, "source": "windows_location_service", "latitude": 38.6, "longitude": -90.2,
                    "accuracy_meters": 204, "stale": False, "exact": False}
        with patch("ariadne.location.detect_windows_location", return_value=position) as detect:
            self.assertEqual(self.request("/api/location/detect", "POST")[0], 200)
        detect.assert_called_once_with()
        self.assertEqual(self.store.setting("device_location"), position)
        self.assertEqual(self.app.briefing.config()["region"], original)
        for replacement in ({"ok": False, "status": "permission_denied"}, {**position, "stale": True}):
            with patch("ariadne.location.detect_windows_location", return_value=replacement):
                self.request("/api/location/detect", "POST")
            self.assertEqual(self.store.setting("device_location"), position)

    def test_stale_browser_position_cannot_overwrite_a_saved_position(self):
        self.store.set_setting("device_location", {"marker": "preserve current owner position"})
        status, response, _ = self.request("/api/location/browser", "POST", self.browser_position(age_seconds=3600))
        self.assertEqual(status, 400)
        self.assertIn("stale", response["error"])
        self.assertEqual(self.store.setting("device_location"), {"marker": "preserve current owner position"})
        self.assertEqual(self.request("/api/location/browser", "POST", self.browser_position())[0], 200)
        saved = self.store.setting("device_location")
        self.assertEqual(saved["source"], "browser_device_geolocation")
        self.assertFalse(saved["exact"])
        self.assertEqual(saved["accuracy_meters"], 204)

    def test_permissions_policy_allows_only_the_same_origin_location_request(self):
        _, _, headers = self.request("/api/location")
        policy = headers["Permissions-Policy"]
        self.assertIn("geolocation=(self)", policy)
        self.assertIn("camera=()", policy)
        self.assertNotIn("*", policy)


if __name__ == "__main__":
    unittest.main()
