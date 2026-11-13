"""Offline WSGI and agent boundary checks; no running owner app or sensors."""
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

TOKEN = "synthetic-perception-api-test-token"


class NoModel:
    def status(self):
        return {"available": False, "models": []}

    def chat(self, *args, **kwargs):
        raise AssertionError("Perception fixtures may not invoke a model")

    chat_fast = chat
    warm = chat


class PassiveVoice:
    def status(self):
        return {"transcription": False, "speech": False, "speech_backends": [],
                "speech_backend": None, "voice_presets": [], "default_voice": None}


class Unreadable:
    def read(self, *args):
        raise AssertionError("Rejected requests must not read a body")


class PerceptionAPITests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(prefix="perception-api-", dir=parent)
        self.addCleanup(tmp.cleanup)
        self.settings = Settings(data_dir=Path(tmp.name)/"data", api_token=TOKEN)
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        for name in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            guard = patch(name, side_effect=AssertionError("Perception tests forbid network and child processes"))
            guard.start()
            self.addCleanup(guard.stop)
        self.engine = Engine(self.settings, self.store, NoModel())
        with patch("ariadne.server.Voice", return_value=PassiveVoice()):
            self.app = Application(self.settings, self.store, self.engine)

    def request(self, suffix="", method="GET", body=None, *, raw=None, auth=True,
                host="localhost:8080", origin=None, query="", stream=None, length=None,
                content_type="application/json"):
        raw = json.dumps(body if body is not None else {}).encode() if raw is None else raw
        env = {"HTTP_HOST": host, "REQUEST_METHOD": method, "PATH_INFO": "/api/perception"+suffix,
               "CONTENT_TYPE": content_type, "CONTENT_LENGTH": str(len(raw) if length is None else length),
               "wsgi.input": io.BytesIO(raw) if stream is None else stream, "QUERY_STRING": query}
        if auth:
            env["HTTP_AUTHORIZATION"] = "Bearer " + TOKEN
        if origin:
            env["HTTP_ORIGIN"] = origin
        result = {}
        def start(status, headers):
            result.update(status=int(status.split()[0]), headers=dict(headers))
        payload = b"".join(self.app(env, start))
        result["body"] = json.loads(payload) if result["headers"]["Content-Type"].startswith("application/json") else payload
        return result

    def state(self):
        r = self.request()
        self.assertEqual(r["status"], 200, r)
        return r["body"]

    def demo(self, request_id="synthetic-demo"):
        r = self.request("/demo", "POST", {"request_id": request_id, "expected_revision": self.state()["revision"]})
        self.assertEqual(r["status"], 200, r)
        return r["body"]

    def test_saved_reads_do_not_create_perception_state_or_change_owner_settings(self):
        before = self.store.setting("paused", False)
        self.state()
        self.state()
        self.assertFalse((self.settings.data_dir/"perception").exists())
        self.assertEqual(self.store.setting("paused", False), before)

    def test_auth_host_origin_are_checked_before_mutation_body(self):
        for changes, expected in (({"auth": False}, 401), ({"host": "foreign.invalid"}, 403),
                                  ({"origin": "https://foreign.invalid"}, 403)):
            r = self.request("/demo", "POST", stream=Unreadable(), **changes)
            self.assertEqual(r["status"], expected, r)

    def test_pause_blocks_dispatch_before_body_and_does_not_clear_it(self):
        self.store.set_setting("paused", True)
        self.assertEqual(self.request("/demo", "POST", stream=Unreadable())["status"], 409)
        self.assertTrue(self.store.setting("paused"))
        self.assertEqual(self.request()["status"], 200)

    def test_strict_mutation_fields_clocks_and_no_capture_routes(self):
        for body in ({"request_id": "x"}, {"request_id": "x", "expected_revision": True},
                     {"request_id": "x", "expected_revision": 0, "live": True}):
            self.assertEqual(self.request("/demo", "POST", body)["status"], 400)
        for suffix in ("/capture", "/configure", "/ruview", "/camera", "/resume"):
            self.assertEqual(self.request(suffix, "POST", stream=Unreadable())["status"], 404)
        self.assertEqual(self.request(query="refresh=true")["status"], 400)

    def test_duplicate_json_keys_and_oversized_body_are_rejected(self):
        raw = b'{"request_id":"first","request_id":"second","expected_revision":0}'
        self.assertEqual(self.request("/demo", "POST", raw=raw)["status"], 400)
        self.assertEqual(self.request("/demo", "POST", stream=Unreadable(), length=8193)["status"], 413)
        self.assertEqual(self.request("/demo", "POST", content_type="text/plain")["status"], 415)

    def test_synthetic_demo_is_explicit_and_readable_without_model_or_devices(self):
        self.demo()
        detail = self.request("/recordings/synthetic-demo")
        self.assertEqual(detail["status"], 200, detail)
        text = json.dumps(detail["body"])
        self.assertIn("synthetic", text)
        self.assertIn("replay", text)
        self.assertIn("Content-Security-Policy", detail["headers"])
        self.assertIn("camera=()", detail["headers"]["Permissions-Policy"])
        self.assertIn("no-store", detail["headers"]["Cache-Control"])

    def test_revision_conflict_prevents_a_second_write(self):
        old = self.state()["revision"]
        self.demo()
        r = self.request("/demo", "POST", {"request_id": "stale-demo", "expected_revision": old})
        self.assertIn(r["status"], {400, 409}, r)
        self.assertNotEqual(r["status"], 200)
        self.assertNotIn("stale-demo", json.dumps(self.state()))

    def test_agent_tool_is_read_only_and_scoped_to_existing_profiles(self):
        tools = self.engine.tools
        names = {s["function"]["name"] for s in tools.schemas()}
        self.assertIn("perception_read", names)
        result = tools.execute("perception_read", {"action": "status"})
        self.assertFalse(result["write_authority"])
        for body in ({"action": "demo"}, {"action": "status", "path": "anything"},
                     {"action": "recording", "recording_id": "../escape"}):
            self.assertFalse(tools.execute("perception_read", body)["ok"])
        self.assertTrue(self.engine.agents.allows("local_controls", "perception_read"))
        self.assertTrue(self.engine.agents.allows("offline_operations", "perception_read"))
        self.assertFalse(self.engine.agents.allows("market_research", "perception_read"))

    def test_pause_resume_atomically_advances_publication_fence(self):
        before = self.store.setting("perception_revision", 0)
        self.store.set_setting("paused", True)
        self.store.set_setting("paused", False)
        self.assertFalse(self.store.setting("paused"))
        self.assertEqual(self.store.setting("perception_revision"), before+2)

    def test_agent_recording_read_is_bounded_and_preserves_current_unknown(self):
        self.demo()
        result = self.engine.tools.execute("perception_read", {"action": "recording", "recording_id": "synthetic-demo"})
        self.assertIn("recording", result, result)
        self.assertFalse(result["recording"]["live_available"])
        self.assertLessEqual(len(result["timeline"]), 8)
        self.assertTrue(all(zone["presence"] == "unknown" for zone in result["current_zones"]))

    def test_http_import_and_replay_bind_identical_frozen_evidence(self):
        fixture = Path(__file__).resolve().parents[1]/"ariadne/resources/perception/example-recording.json"
        selected = self.settings.workspace/"selected-recording.json"
        selected.write_bytes(fixture.read_bytes())
        imported = self.request("/import", "POST", {"path": selected.name, "request_id": "imported",
            "expected_revision": self.state()["revision"]})
        self.assertEqual(imported["status"], 200, imported)
        replayed = self.request("/replay", "POST", {"recording_id": "imported", "request_id": "replayed",
            "expected_revision": self.state()["revision"]})
        self.assertEqual(replayed["status"], 200, replayed)
        self.assertEqual(imported["body"]["canonical_sha256"], replayed["body"]["canonical_sha256"])
        detail = self.request("/recordings/replayed")["body"]
        self.assertTrue(detail["receipt"]["verified"])
        self.assertEqual(detail["evidence"]["processing_mode"], "replay")
        self.assertFalse(detail["evidence"]["live_available"])

    def test_tampered_detail_is_not_published_as_valid_evidence(self):
        self.demo()
        saved = self.settings.data_dir/"perception/synthetic-demo/evidence.json"
        saved.write_bytes(saved.read_bytes()+b" ")
        self.assertEqual(self.request("/recordings/synthetic-demo")["status"], 400)
        state = self.state()
        self.assertTrue(state["hold"])
        self.assertFalse(state["recordings"][0]["verified"])
        self.assertNotEqual(state["recordings"][0]["status"], "completed")

    def test_capability_advertises_only_saved_recording_support(self):
        from ariadne.capabilities import catalog
        item = next(item for item in catalog(self.app)["capabilities"] if item["id"] == "perception_recordings")
        self.assertEqual(item["status"], "ready")
        self.assertTrue(item["offline"])
        self.assertIn("no sensor capture", item["details"])
        self.assertIn("future adapter", item["details"])

    def test_bundled_awareness_assets_are_served_without_new_capture_permission(self):
        for name in ("perception.js", "perception.css"):
            env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/static/"+name, "HTTP_HOST": "localhost:8080"}
            result = {}
            def start(status, headers):
                result.update(status=int(status.split()[0]), headers=dict(headers))
            payload = b"".join(self.app(env, start))
            self.assertEqual(result["status"], 200)
            self.assertGreater(len(payload), 100)
            self.assertIn("camera=()", result["headers"]["Permissions-Policy"])

    def test_ruview_actions_preserve_auth_origin_and_pause_before_body(self):
        for suffix in ("/import-ruview", "/ruview-demo"):
            for changes, expected in (({"auth": False}, 401), ({"host": "foreign.invalid"}, 403),
                                      ({"origin": "https://foreign.invalid"}, 403)):
                self.assertEqual(self.request(suffix, "POST", stream=Unreadable(), **changes)["status"], expected)
            self.store.set_setting("paused", True)
            self.assertEqual(self.request(suffix, "POST", stream=Unreadable())["status"], 409)
            self.assertTrue(self.store.setting("paused"))
            self.store.set_setting("paused", False)

    def test_ruview_route_accepts_only_explicit_recording_fields(self):
        base = {"path": "selected.jsonl", "request_id": "rf-import", "expected_revision": 0,
                "source_policy": "esp32_only"}
        for body in ({key: value for key, value in base.items() if key != "source_policy"},
                     {**base, "endpoint": "http://127.0.0.1:8081"},
                     {**base, "live": True}, {**base, "source_policy": True},
                     {**base, "source_policy": "auto"}, {**base, "expected_revision": True}):
            self.assertEqual(self.request("/import-ruview", "POST", body)["status"], 400)
        for suffix in ("/ruview", "/ruview/capture", "/ruview/connect", "/ruview/configure", "/ruview/flash"):
            self.assertEqual(self.request(suffix, "POST", stream=Unreadable())["status"], 404)
        self.assertFalse((self.settings.data_dir/"perception").exists())

    def test_ruview_synthetic_example_uses_same_recording_journal_and_never_claims_live(self):
        before = self.state()["revision"]
        result = self.request("/ruview-demo", "POST", {"request_id": "rf-fiction", "expected_revision": before})
        self.assertEqual(result["status"], 200, result)
        detail = self.request("/recordings/rf-fiction")
        self.assertEqual(detail["status"], 200, detail)
        saved = detail["body"]
        self.assertTrue(saved["receipt"]["verified"])
        self.assertEqual(saved["receipt"]["operation"], "ruview_demo")
        self.assertFalse(saved["adapter"]["hardware_qualified"])
        self.assertFalse(saved["evidence"]["live_available"])
        self.assertTrue(all(row["presence"] == "unknown" for row in saved["current_zones"]))
        self.assertIn("synthetic", json.dumps(saved["evidence"]))
        self.assertIn("camera=()", detail["headers"]["Permissions-Policy"])

    def test_ruview_import_policy_rejects_synthetic_without_reserving_an_attempt(self):
        fixture = Path(__file__).resolve().parents[1]/"ariadne/resources/perception/ruview-synthetic.jsonl"
        selected = self.settings.workspace/"selected-ruview.jsonl"
        selected.write_bytes(fixture.read_bytes())
        raw = selected.read_bytes()
        revision = self.state()["revision"]
        body = {"path": selected.name, "request_id": "rf-physical-only", "expected_revision": revision,
                "source_policy": "esp32_only"}
        denied = self.request("/import-ruview", "POST", body)
        self.assertEqual(denied["status"], 400, denied)
        self.assertEqual(self.state()["revision"], revision)
        self.assertFalse(self.state()["hold"])
        body.update(request_id="rf-explicit-recording", source_policy="recorded_any")
        accepted = self.request("/import-ruview", "POST", body)
        self.assertEqual(accepted["status"], 200, accepted)
        import hashlib
        self.assertEqual(accepted["body"]["source_sha256"], hashlib.sha256(raw).hexdigest())
        saved = self.request("/recordings/rf-explicit-recording")["body"]
        self.assertEqual(saved["receipt"]["source_policy"], "recorded_any")
        self.assertFalse(saved["adapter"]["hardware_qualified"])

    def test_ruview_replay_keeps_frozen_source_after_workspace_edits(self):
        fixture = Path(__file__).resolve().parents[1]/"ariadne/resources/perception/ruview-synthetic.jsonl"
        selected = self.settings.workspace/"ruview-replay.jsonl"
        selected.write_bytes(fixture.read_bytes())
        imported = self.request("/import-ruview", "POST", {"path": selected.name, "request_id": "rf-before-edit",
            "source_policy": "recorded_any", "expected_revision": self.state()["revision"]})
        self.assertEqual(imported["status"], 200, imported)
        selected.write_text("edited unsupported input", encoding="utf-8")
        replayed = self.request("/replay", "POST", {"recording_id": "rf-before-edit", "request_id": "rf-replayed",
            "expected_revision": self.state()["revision"]})
        self.assertEqual(replayed["status"], 200, replayed)
        self.assertEqual(imported["body"]["canonical_sha256"], replayed["body"]["canonical_sha256"])
        self.assertEqual(imported["body"]["source_sha256"], replayed["body"]["source_sha256"])
        saved = self.request("/recordings/rf-replayed")["body"]
        self.assertEqual(saved["evidence"]["processing_mode"], "replay")
        self.assertEqual(saved["receipt"]["source_policy"], "recorded_any")

    def test_ruview_private_adapter_fields_are_not_forwarded_to_agent_context(self):
        self.request("/ruview-demo", "POST", {"request_id": "rf-owner-only", "expected_revision": self.state()["revision"]})
        private = {"frames": [{"source": "wifi:PRIVATE-SSID-DO-NOT-FORWARD"}], "hardware_qualified": False}
        real = self.app.perception.detail
        def detail(identifier):
            result = real(identifier)
            result["adapter"] = private
            return result
        with patch.object(self.app.perception, "detail", side_effect=detail):
            answer = self.engine.tools.execute("perception_read", {"action": "recording", "recording_id": "rf-owner-only"})
        self.assertIn("recording", answer)
        self.assertNotIn("error", answer)
        self.assertNotIn("PRIVATE-SSID", json.dumps(answer))
        self.assertNotIn("adapter", answer)

    def test_ruview_prompts_discover_only_the_existing_passive_tool_without_calling_it(self):
        for prompt in ("Review the RuView recording", "Show saved Wi-Fi occupancy evidence", "Inspect CSI provenance"):
            names = {item["function"]["name"] for item in self.engine.tools.schemas_for(prompt, [])}
            self.assertIn("perception_read", names)
            self.assertNotIn("ruview_capture", names)
        names = {item["function"]["name"] for item in self.engine.tools.schemas_for("Calculate 3 plus 4", [])}
        self.assertNotIn("perception_read", names)
        self.assertFalse((self.settings.data_dir/"perception").exists())
        self.assertFalse(self.engine.agents.allows("market_research", "perception_read"))


if __name__ == "__main__":
    unittest.main()
