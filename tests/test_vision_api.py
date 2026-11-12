"""Offline vision HTTP/tool boundaries. Never opens hardware or invokes a model."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store

TOKEN = "synthetic-vision-api-token"


class NoModel:
    def status(self):
        return {"available": False, "models": []}

    def chat(self, *args, **kwargs):
        raise AssertionError("Vision boundary checks may not invoke a model")

    chat_fast = chat
    warm = chat


class PassiveVoice:
    def status(self):
        return {"transcription": False, "speech": False, "speech_backends": [],
                "speech_backend": None, "voice_presets": [], "default_voice": None}


class Unreadable:
    def read(self, *args):
        raise AssertionError("Rejected request must not read body")


class VisionAPITests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(prefix="vision-api-", dir=parent)
        self.addCleanup(tmp.cleanup)
        self.settings = Settings(data_dir=Path(tmp.name)/"data", workspace=Path(tmp.name)/"workspace", api_token=TOKEN)
        self.store = Store(self.settings.data_dir/"state.sqlite3")
        for name in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            guard = patch(name, side_effect=AssertionError("No network, processes or hardware in API fixtures"))
            guard.start()
            self.addCleanup(guard.stop)
        self.engine = Engine(self.settings, self.store, NoModel())
        with patch("ariadne.server.Voice", return_value=PassiveVoice()):
            self.app = Application(self.settings, self.store, self.engine)
        self.addCleanup(self.app.vision.close)

    def request(self, suffix="", method="GET", body=None, *, raw=None, auth=True,
                host="localhost:8080", origin=None, query="", stream=None, length=None,
                content_type="application/json"):
        raw = json.dumps(body if body is not None else {}).encode() if raw is None else raw
        env = {"HTTP_HOST": host, "REQUEST_METHOD": method, "PATH_INFO": "/api/vision"+suffix,
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

    def test_passive_status_does_not_create_vision_private_state(self):
        for _ in range(2):
            result = self.request()
            self.assertEqual(result["status"], 200, result)
            self.assertEqual(result["body"]["microphone"], "off")
            self.assertFalse(result["body"]["camera_autostart"])
        self.assertFalse(self.app.vision.root.exists())

    def test_auth_host_origin_precede_body_and_device_discovery(self):
        with patch.object(self.app.vision, "discover_cameras") as discover:
            for changes, expected in (({"auth": False},401), ({"host":"elsewhere.invalid"},403),
                                      ({"origin":"https://elsewhere.invalid"},403)):
                self.assertEqual(self.request("/camera/start", "POST", stream=Unreadable(), **changes)["status"], expected)
                self.assertEqual(self.request("/cameras", **changes)["status"], expected)
            discover.assert_not_called()

    def test_pause_blocks_work_but_does_not_block_stop(self):
        self.store.set_setting("paused", True)
        with patch.object(self.app.vision, "start_camera") as start, patch.object(self.app.vision, "stop_camera", return_value={"stopped":True}) as stop:
            self.assertEqual(self.request("/camera/start", "POST", stream=Unreadable())["status"],409)
            self.assertEqual(self.request("/video", "POST", stream=Unreadable())["status"],409)
            self.assertEqual(self.request("/camera/stop", "POST", {"session_id":"selected"})["status"],200)
            start.assert_not_called()
            stop.assert_called_once_with({"session_id":"selected"})
        self.assertTrue(self.store.setting("paused"))

    def test_unknown_routes_and_queries_rejected_before_body(self):
        for suffix in ("/capture", "/configure", "/resume", "/ruview", "/camera", "/hardware"):
            self.assertEqual(self.request(suffix,"POST",stream=Unreadable())["status"],404)
        self.assertEqual(self.request(query="refresh=true")["status"],400)
        self.assertEqual(self.request("/video","POST",query="path=x",stream=Unreadable())["status"],400)

    def test_strict_json_limits_precede_dispatch(self):
        with patch.object(self.app.vision,"analyze_video") as analyze:
            for raw in (b'{"request_id":"a","request_id":"b"}', b'{"duration_ms":NaN}'):
                self.assertEqual(self.request("/video","POST",raw=raw)["status"],400)
            self.assertEqual(self.request("/video","POST",stream=Unreadable(),length=16385)["status"],413)
            self.assertEqual(self.request("/video","POST",content_type="text/plain")["status"],415)
            analyze.assert_not_called()

    def test_inspection_hashes_only_regular_workspace_video(self):
        selected = self.settings.workspace/"selected.mp4"
        selected.write_bytes(b"\x00\x00\x00\x18ftypisom"+b"synthetic-api-header"*10)
        result = self.request("/inspect","POST",{"source_path":selected.name})
        self.assertEqual(result["status"],200,result)
        self.assertEqual(result["body"]["source_sha256"],hashlib.sha256(selected.read_bytes()).hexdigest())
        for path in ("../selected.mp4","https://invalid/video.mp4","E:/video.mp4"):
            self.assertEqual(self.request("/inspect","POST",{"source_path":path})["status"],400)
        self.assertFalse(self.app.vision.root.exists())

    def test_video_request_preserves_source_hash_and_explicit_snapshot_choice(self):
        body = {"request_id":"clip-one","source_path":"selected.mp4","source_sha256":"a"*64,
                "expected_control_revision":0,"thumbnail":False,"recording_started_at":None}
        with patch.object(self.app.vision,"analyze_video",return_value={"status":"completed"}) as analyze:
            self.assertEqual(self.request("/video","POST",body)["status"],200)
            analyze.assert_called_once_with(body)

    def test_snapshot_authentication_and_no_opt_in_missing_response(self):
        with patch.object(self.app.vision,"snapshot",return_value=None) as image:
            self.assertEqual(self.request("/snapshots/clip-one",auth=False)["status"],401)
            image.assert_not_called()
            self.assertEqual(self.request("/snapshots/clip-one")["status"],404)
        with patch.object(self.app.vision,"snapshot",return_value=b"synthetic-jpeg"):
            result = self.request("/snapshots/clip-one")
            self.assertEqual(result["body"],b"synthetic-jpeg")
            self.assertIn("no-store",result["headers"]["Cache-Control"])

    def test_agent_read_is_bounded_passive_and_scoped(self):
        with patch.object(self.app.vision,"touch_camera") as lease, patch.object(self.app.vision,"start_camera") as start:
            result = self.engine.tools.execute("vision_read",{"action":"status"})
            self.assertFalse(result["write_authority"])
            lease.assert_not_called()
            start.assert_not_called()
        for body in ({"action":"start"},{"action":"status","lease_token":"x"},{"action":"job","job_id":"../outside"}):
            self.assertFalse(self.engine.tools.execute("vision_read",body)["ok"])
        self.assertTrue(self.engine.agents.allows("local_controls","vision_read"))
        self.assertTrue(self.engine.agents.allows("offline_operations","vision_read"))
        self.assertFalse(self.engine.agents.allows("market_research","vision_read"))
        huge = {"evidence":{"frames":[{"sample_index":i} for i in range(1000)],
                            "recording":{"observations":[{}]*1000}},"live":{"state":"off"}}
        with patch.object(self.app.vision,"detail",return_value=huge):
            detail = self.engine.tools.execute("vision_read",{"action":"job","job_id":"recorded"})
        self.assertEqual(len(detail["evidence"]["frames"]),8)
        self.assertNotIn("observations",detail["evidence"]["recording"])

    def test_shutdown_closes_vision_even_when_later_service_fails(self):
        self.app.vision = Mock()
        self.app.wake_voice = Mock()
        self.app.wake_voice.stop.side_effect = RuntimeError("synthetic shutdown failure")
        self.app.briefing = Mock()
        self.app.internet = Mock()
        self.app.voice = Mock()
        with self.assertRaises(RuntimeError):
            self.app.stop_services()
        self.app.vision.close.assert_called_once()
        self.app.briefing.stop.assert_called_once()
        self.app.internet.stop.assert_called_once()
        self.app.voice.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
