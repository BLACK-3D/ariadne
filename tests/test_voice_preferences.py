"""Speech preferences use one owner snapshot and one route for PC/browser voice.

These are direct WSGI and lifecycle tests with synthetic state. They never load
speech assets, generate model/audio output, open devices, or start services.
"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store
from ariadne.voice import Voice


class NoModel:
    def status(self):
        return {"available": False, "models": []}

    def chat(self, *args, **kwargs):
        raise AssertionError("Speech preference tests must not execute a model")


class FakeVoice:
    def __init__(self):
        self.available = ["natural", "system"]
        self.default_voice = "af_heart"
        self.presets = [{"id": "af_heart", "name": "Heart · American (slower)", "language": "en-us"},
                        {"id": "bf_emma", "name": "Emma · British (slower)", "language": "en-gb"},
                        {"id": "cori", "name": "Cori · British (quick)", "language": "en-gb"},
                        {"id": "af_bella", "name": "Bella · American (slower)", "language": "en-us"},
                        {"id": "bm_george", "name": "George · British (slower)", "language": "en-gb"}]
        self.speak = Mock(return_value=b"synthetic-wave-bytes")
        self.play = Mock()
        self.warm = Mock()
        self.close = Mock()
        self.reset_natural = Mock()

    def status(self):
        return {"speech": bool(self.available), "transcription": False,
                "speech_backends": list(self.available), "speech_backend": "kokoro",
                "voice_presets": list(self.presets), "default_voice": self.default_voice,
                "natural_runtime": {"state": "ready"}, "latency_note": "Synthetic local runtime fixture."}


class VoicePreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".voice-preferences-test-")
        self.settings = Settings(data_dir=Path(self.temp.name), api_token="voice-preference-fixture")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.engine = Engine(self.settings, self.store, NoModel())
        self.voice = FakeVoice()
        with patch("ariadne.server.Voice", return_value=self.voice):
            self.app = Application(self.settings, self.store, self.engine)
        self.media = patch.object(self.engine.tools.media, "status", return_value={"backends": {"kokoro": {"ready": True}}})
        self.media.start()

    def tearDown(self):
        self.media.stop()
        self.app.stop_services()
        self.temp.cleanup()

    def request(self, path, method="GET", data=None, token=True, origin=None):
        body = json.dumps({} if data is None else data).encode("utf-8")
        env = {"HTTP_HOST": "localhost:8080", "PATH_INFO": path, "REQUEST_METHOD": method,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        if token:
            env["HTTP_AUTHORIZATION"] = "Bearer voice-preference-fixture"
        if origin:
            env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            response.update(status=int(status.split()[0]), headers=dict(headers))
        raw = b"".join(self.app(env, start))
        response["data"] = json.loads(raw) if response["headers"]["Content-Type"].startswith("application/json") else raw
        return response

    def test_new_install_prefers_natural_without_persisting_an_implicit_choice(self):
        with patch.object(self.store, "set_setting", wraps=self.store.set_setting) as write:
            self.assertEqual(self.app.speech_preferences(), {"backend": "natural", "voice": self.voice.default_voice, "speed": 1.12})
        write.assert_not_called()

    def test_new_install_uses_cori_when_the_local_adapter_advertises_it_as_default(self):
        self.voice.default_voice = "cori"
        self.assertEqual(self.app.speech_preferences(), {"backend": "natural", "voice": "cori", "speed": 1.12})

    def test_new_install_without_natural_assets_uses_available_system_adapter(self):
        self.voice.available = ["system"]
        self.assertEqual(self.app.speech_preferences()["backend"], "system")

    def test_explicit_legacy_system_preference_remains_effective(self):
        self.store.set_setting("speech_backend", "system")
        self.assertEqual(self.app.speech_preferences(), {"backend": "system", "voice": self.voice.default_voice, "speed": 1.12})

    def test_packed_snapshot_overrides_legacy_and_is_read_as_one_owner_value(self):
        preferences = {"backend": "natural", "voice": "bf_emma", "speed": 1.20}
        self.store.set_setting("speech_backend", "system")
        self.store.set_setting("speech_preferences", preferences)
        with patch.object(self.store, "setting", wraps=self.store.setting) as read:
            self.assertEqual(self.app.speech_preferences(), preferences)
        read.assert_called_once_with("speech_preferences", None)

    def test_status_exposes_confirmed_voice_pace_presets_and_runtime(self):
        self.store.set_setting("speech_preferences", {"backend": "natural", "voice": "bf_emma", "speed": 1.20})
        result = self.request("/api/status")
        self.assertEqual(result["status"], 200)
        self.assertEqual([result["data"][name] for name in ("speech_backend", "speech_voice", "speech_speed")], ["natural", "bf_emma", 1.20])
        self.assertEqual(result["data"]["voice"]["natural_runtime"]["state"], "ready")
        self.assertEqual(result["data"]["voice"]["voice_presets"][1]["id"], "bf_emma")
        self.voice.speak.assert_not_called()
        self.voice.play.assert_not_called()

    def test_valid_preference_put_commits_exactly_one_snapshot_and_returns_all_options(self):
        preferences = {"backend": "natural", "voice": "bf_emma", "speed": 1.20}
        with patch.object(self.store, "set_setting", wraps=self.store.set_setting) as write:
            result = self.request("/api/voice/preferences", "PUT", preferences)
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["data"], {"speech_backend": "natural", "speech_voice": "bf_emma", "speech_speed": 1.20})
        write.assert_called_once_with("speech_preferences", preferences)
        self.voice.reset_natural.assert_called_once_with()
        self.voice.warm.assert_called_once_with(voice="bf_emma", speed=1.20)

    def test_backend_only_legacy_put_preserves_voice_and_pace(self):
        self.store.set_setting("speech_preferences", {"backend": "natural", "voice": "bf_emma", "speed": 1.20})
        result = self.request("/api/voice/preferences", "PUT", {"backend": "system"})
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["data"], {"speech_backend": "system", "speech_voice": "bf_emma", "speech_speed": 1.20})
        self.assertEqual(self.store.setting("speech_preferences"), {"backend": "system", "voice": "bf_emma", "speed": 1.20})
        self.voice.warm.assert_not_called()

    def test_malformed_options_never_partially_persist_or_warm(self):
        original = {"backend": "system", "voice": "af_bella", "speed": 1.10}
        self.store.set_setting("speech_preferences", original)
        invalid = [{}, {"voice": "bf_emma"}, {"backend": "other"}, {"backend": True},
                   {"backend": []}, {"backend": {}}, {"backend": "natural", "extra": "ignored?"}]
        invalid += [{"backend": "natural", "voice": value} for value in ("not_a_voice", "../voice", "", None, True, 3, [], {})]
        invalid += [{"backend": "natural", "speed": value} for value in (True, False, None, 0, 0.84, 1.31, "1.12", float("nan"), float("inf"), [], {})]
        for payload in invalid:
            with self.subTest(payload=payload), patch.object(self.store, "set_setting", wraps=self.store.set_setting) as write:
                result = self.request("/api/voice/preferences", "PUT", payload)
                self.assertEqual(result["status"], 400, result)
                write.assert_not_called()
                self.assertEqual(self.store.setting("speech_preferences"), original)
        self.voice.reset_natural.assert_not_called()
        self.voice.warm.assert_not_called()

    def test_valid_speed_bounds_and_all_curated_voices_are_accepted(self):
        for voice in ("cori", "af_heart", "af_bella", "bf_emma", "bm_george"):
            for speed in (0.85, 1.30):
                with self.subTest(voice=voice, speed=speed):
                    result = self.request("/api/voice/preferences", "PUT", {"backend": "natural", "voice": voice, "speed": speed})
                    self.assertEqual(result["status"], 200)
                    self.assertEqual(result["data"]["speech_voice"], voice)
                    self.assertEqual(result["data"]["speech_speed"], speed)

    def test_missing_pinned_natural_assets_rejects_before_any_setting_write(self):
        self.voice.available = ["system"]
        self.engine.tools.media.status.return_value = {"backends": {"kokoro": {"ready": False}}}
        with patch.object(self.store, "set_setting", wraps=self.store.set_setting) as write:
            self.assertEqual(self.request("/api/voice/preferences", "PUT", {"backend": "natural"})["status"], 400)
        write.assert_not_called()
        self.voice.warm.assert_not_called()

    def test_authenticated_same_origin_required_for_preferences_speech_and_status(self):
        routes = (("/api/voice/preferences", "PUT", {"backend": "natural"}),
                  ("/api/voice/speak", "POST", {"text": "Fixture speech."}), ("/api/status", "GET", {}))
        for path, method, payload in routes:
            with self.subTest(path=path), patch.object(self.store, "set_setting", wraps=self.store.set_setting) as write:
                self.assertEqual(self.request(path, method, payload, token=False)["status"], 401)
                self.assertEqual(self.request(path, method, payload, origin="https://foreign.example")["status"], 403)
                write.assert_not_called()
        self.voice.speak.assert_not_called()
        self.voice.play.assert_not_called()
        self.voice.warm.assert_not_called()

    def test_browser_speech_uses_the_same_saved_natural_and_system_options(self):
        for backend in ("natural", "system"):
            with self.subTest(backend=backend):
                preferences = {"backend": backend, "voice": "bf_emma", "speed": 1.20}
                self.store.set_setting("speech_preferences", preferences)
                result = self.request("/api/voice/speak", "POST", {"text": "Fixture speech."})
                self.assertEqual(result["status"], 200)
                self.assertEqual(result["headers"]["Content-Type"], "audio/wav")
                self.assertEqual(result["data"], b"synthetic-wave-bytes")
                self.voice.speak.assert_called_with("Fixture speech.", **preferences)
        self.voice.play.assert_not_called()

    def test_pc_wake_callback_uses_owner_voice_snapshot_and_existing_voice_lock(self):
        preferences = {"backend": "natural", "voice": "bf_emma", "speed": 1.20}
        self.store.set_setting("speech_preferences", preferences)
        self.assertEqual(self.app.wake_voice.speak, self.app.speak_on_pc)
        def playback(*args, **kwargs):
            self.assertFalse(self.app.voice_lock.acquire(blocking=False))
        self.voice.play.side_effect = playback
        self.app.wake_voice.speak("Listening.")
        self.voice.play.assert_called_once_with("Listening.", **preferences)
        self.assertTrue(self.app.voice_lock.acquire(blocking=False))
        self.app.voice_lock.release()

    def test_busy_browser_voice_returns_429_without_parallel_synthesis(self):
        self.app.voice_lock.acquire()
        try:
            result = self.request("/api/voice/speak", "POST", {"text": "Fixture speech."})
            self.assertEqual(result["status"], 429)
        finally:
            self.app.voice_lock.release()
        self.voice.speak.assert_not_called()

    def test_speech_exception_releases_browser_voice_lock(self):
        self.voice.speak.side_effect = ValueError("Synthetic voice failure")
        self.assertEqual(self.request("/api/voice/speak", "POST", {"text": "Fixture speech."})["status"], 400)
        self.assertTrue(self.app.voice_lock.acquire(blocking=False))
        self.app.voice_lock.release()

    def test_valid_preference_warm_failure_keeps_committed_snapshot_and_returns_warning(self):
        self.voice.warm.side_effect = ValueError("Synthetic warm failure")
        preferences = {"backend": "natural", "voice": "bf_emma", "speed": 1.20}
        result = self.request("/api/voice/preferences", "PUT", preferences)
        self.assertEqual(result["status"], 200)
        self.assertEqual(self.store.setting("speech_preferences"), preferences)
        self.assertEqual(result["data"]["speech_backend"], "natural")
        self.assertEqual(result["data"]["speech_voice"], "bf_emma")
        self.assertEqual(result["data"]["speech_speed"], 1.20)
        self.assertIn("settings saved", result["data"]["speech_warning"].lower())

    def test_startup_prewarms_natural_without_opening_a_microphone_or_playing_audio(self):
        with patch.object(self.app.briefing, "start") as brief, patch.object(self.app.wake_voice, "start") as microphone:
            self.app.start_services()
        brief.assert_called_once_with()
        microphone.assert_not_called()
        self.voice.warm.assert_called_once_with(voice=self.voice.default_voice, speed=1.12)
        self.voice.speak.assert_not_called()
        self.voice.play.assert_not_called()

    def test_startup_respects_saved_microphone_intent_and_system_voice(self):
        self.store.set_setting("speech_preferences", {"backend": "system", "voice": "bf_emma", "speed": 1.20})
        self.store.set_setting("wake_preferences", {"enabled": True})
        with patch.object(self.app.briefing, "start"), patch.object(self.app.wake_voice, "start") as microphone:
            self.app.start_services()
        microphone.assert_called_once_with(mode="pc", session_hours=None)
        self.voice.warm.assert_not_called()

    def test_startup_keeps_application_available_after_a_natural_warm_failure(self):
        self.voice.warm.side_effect = OSError("Synthetic unavailable engine")
        with patch.object(self.app.briefing, "start"), patch.object(self.app.wake_voice, "start") as microphone:
            self.app.start_services()
        microphone.assert_not_called()
        self.voice.play.assert_not_called()

    def test_all_service_cleanup_runs_and_voice_closes_after_any_prior_cleanup_failure(self):
        for failing in ("microphone", "briefing", "internet"):
            with self.subTest(failing=failing):
                calls = []
                def stop(name):
                    def action():
                        calls.append(name)
                        if name == failing:
                            raise RuntimeError("Synthetic cleanup failure")
                    return action
                with patch.object(self.app.wake_voice, "stop", side_effect=stop("microphone")), \
                     patch.object(self.app.briefing, "stop", side_effect=stop("briefing")), \
                     patch.object(self.app.internet, "stop", side_effect=stop("internet")), \
                     patch.object(self.voice, "close", side_effect=stop("voice")):
                    with self.assertRaisesRegex(RuntimeError, "Synthetic cleanup failure"):
                        self.app.stop_services()
                self.assertEqual(calls, ["microphone", "briefing", "internet", "voice"])


class VoiceAvailabilityMetadataTests(unittest.TestCase):
    """Actual status metadata with adapter construction and assets stubbed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".voice-availability-test-")
        self.voice = object.__new__(Voice)
        self.voice.settings = Settings(data_dir=Path(self.temp.name))
        self.voice.windows_speech = None
        self.voice.natural = Mock()
        self.voice.natural.status.return_value = {"state": "idle"}

    def tearDown(self):
        self.voice.natural.warm.assert_not_called()
        self.temp.cleanup()

    def status(self, *, piper=False, kokoro=False):
        def backend(_profile, name):
            return {"ready": {"piper": piper, "kokoro": kokoro, "sherpa": False}[name]}
        with patch("ariadne.media_tools.load_profile", return_value={}), \
             patch("ariadne.media_tools.backend_status", side_effect=backend):
            return self.voice.status()

    def test_only_fast_assets_advertise_cori_as_the_available_default(self):
        status = self.status(piper=True)
        self.assertEqual(status["default_voice"], "cori")
        self.assertEqual([voice["id"] for voice in status["voice_presets"]], ["cori"])
        self.assertEqual(status["speech_backends"], ["natural"])
        self.assertEqual(status["speech_backend"], "piper-neural")

    def test_only_legacy_kokoro_assets_keep_the_four_existing_presets_and_default(self):
        status = self.status(kokoro=True)
        self.assertEqual(status["default_voice"], "af_heart")
        self.assertEqual({voice["id"] for voice in status["voice_presets"]}, {"af_heart", "af_bella", "bf_emma", "bm_george"})
        self.assertEqual(status["speech_backend"], "kokoro")

    def test_both_local_engines_advertise_five_distinct_presets_with_cori_default(self):
        status = self.status(piper=True, kokoro=True)
        self.assertEqual(status["default_voice"], "cori")
        self.assertEqual([voice["id"] for voice in status["voice_presets"]][0], "cori")
        self.assertEqual(len({voice["id"] for voice in status["voice_presets"]}), 5)
        self.assertEqual({voice["backend"] for voice in status["voice_presets"]}, {"piper", "kokoro"})

    def test_missing_neural_assets_hide_their_presets_and_leave_only_a_configured_system_adapter(self):
        status = self.status()
        self.assertEqual(status["voice_presets"], [])
        self.assertEqual(status["speech_backends"], [])
        self.assertFalse(status["speech"])
        self.voice.windows_speech = "configured-fixture-adapter"
        status = self.status()
        self.assertEqual(status["voice_presets"], [])
        self.assertEqual(status["speech_backends"], ["system"])
        self.assertEqual(status["speech_backend"], "windows-sapi")


if __name__ == "__main__":
    unittest.main()
