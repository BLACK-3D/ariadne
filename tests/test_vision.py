"""Offline boundary/lifecycle checks. No devices, downloads, or model stubs in product."""
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.vision import (Vision, VisionConflict, WorkerError, MODEL_SHA256, _ACTIVE,
                            _bytes, _sha, MAX_DURATION_MS)


def frame(index=0, person=True, offset=0):
    return {"sample_index": index, "decoded_frame_index": index * 30, "source_pts_ms": offset + 500,
            "timeline_offset_ms": offset, "timestamp_basis": "container_pts", "acquired_at": None,
            "source_lag_ms": None, "capture_elapsed_ms": None,
            "width": 640, "height": 480, "source_width": 640, "source_height": 480,
            "frame_sha256": hashlib.sha256(str(index).encode()).hexdigest(), "inference_input_sha256": "b" * 64,
            "detections": [{"label": "person", "score": .8, "bbox_normalized": [.1, .1, .2, .4]}] if person else [],
            "motion": {"state": "unknown" if index == 0 else "detected", "method": "grayscale_mean_absolute_difference",
                       "mean_absolute_difference": None if index == 0 else 12., "threshold": 8., "previous_frame_sha256": None if index == 0 else "c" * 64},
            "inference_ms": 10.}


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vision-test-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name) / "private", workspace=Path(self.temp.name) / "workspace")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.manager = Vision(self.registry, self.store)
        self.addCleanup(self.manager.close)
        self.assets = mock.patch("ariadne.vision.vision_assets", return_value={"ready": True, "model_id": "yolox-nano-0.1.1rc0", "model_sha256": MODEL_SHA256})
        self.assets.start()
        self.addCleanup(self.assets.stop)
        self.path = self.registry.root / "clip.mp4"
        self.path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"recorded-fixture" * 10)
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def request(self, identifier="video-one", **extra):
        return {"request_id": identifier, "expected_control_revision": self.store.setting("perception_revision", 0),
                "source_path": "clip.mp4", "source_sha256": self.sha, **extra}

    def run_result(self, payload, **kwargs):
        rows = [frame(), frame(1, person=False, offset=1000)]
        for row in rows:
            kwargs["progress"](row)
        return {"type": "result", "frames": rows, "duration_ms": 1000, "decoded_frames": 60, "decoder": "test-injected-result"}

    def test_unused_status_never_creates_private_vision_or_opens_device(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("No process on status")):
            status = self.manager.status()
        self.assertFalse(self.manager.root.exists())
        self.assertFalse(status["camera_autostart"])
        self.assertEqual(status["microphone"], "off")
        self.assertFalse(status["hold"])
        self.assertEqual(status["sessions"], [])

    def test_source_pinning_rejects_protocols_traversal_links_and_different_hash(self):
        for path in ("https://host/file.mp4", "file:clip.mp4", "../clip.mp4", "E:/clip.mp4", "\\\\host\\clip.mp4", "/clip.mp4", "clip.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.manager.inspect_source({"source_path": path})
        linked = self.registry.root / "linked.mp4"
        try:
            linked.hardlink_to(self.path)
            with self.assertRaises(ValueError):
                self.manager.inspect_source({"source_path": "linked.mp4"})
        finally:
            if linked.exists():
                linked.unlink()
        with self.assertRaises(ValueError):
            self.manager.analyze_video(self.request(source_sha256="0" * 64))
        self.assertFalse(self.manager.root.exists())

    def test_video_keeps_hashes_original_pts_nullable_clock_and_no_live_promotion(self):
        inspected = self.manager.inspect_source({"source_path": "clip.mp4"})
        self.assertEqual(inspected["source_sha256"], self.sha)
        with mock.patch("ariadne.vision.run_vision", side_effect=self.run_result):
            receipt = self.manager.analyze_video(self.request())
        detail = self.manager.detail("video-one")
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["source_sha256"], self.sha)
        self.assertEqual(receipt["model_sha256"], MODEL_SHA256)
        self.assertFalse(receipt["raw_video_retained"])
        self.assertFalse(receipt["whole_video_analyzed"])
        self.assertEqual(receipt["coverage"], "first_bounded_window")
        self.assertFalse((self.manager.root / "video-one" / "input.mp4").exists())
        self.assertEqual(detail["evidence"]["frames"][0]["source_pts_ms"], 500)
        observations = detail["evidence"]["recording"]["observations"]
        self.assertIsNone(observations[0]["observed_at"])
        self.assertEqual(observations[0]["objects"], [{"label": "person", "count": 1}])
        self.assertEqual(observations[0]["presence"], "present")
        self.assertEqual(observations[1]["presence"], "unknown")
        self.assertEqual(observations[1]["motion"], "detected")
        self.assertEqual(detail["live"]["state"], "off")
        self.assertIsNone(self.manager.snapshot("video-one"))

    def test_declared_source_clock_survives_replay_without_becoming_live(self):
        with mock.patch("ariadne.vision.run_vision", side_effect=self.run_result):
            self.manager.analyze_video(self.request(recording_started_at="2026-01-01T00:00:00Z"))
        detail = self.manager.detail("video-one")
        self.assertEqual(detail["evidence"]["recording"]["observations"][1]["observed_at"], "2026-01-01T00:00:01.000Z")
        self.assertEqual(detail["live"]["state"], "off")

    def test_source_changes_after_acceptance_hold_job_and_next_identifiers(self):
        original = self.manager._snapshot_source
        def changed(request, destination):
            self.path.write_bytes(b"changed")
            return original(request, destination)
        with mock.patch.object(self.manager, "_snapshot_source", side_effect=changed), self.assertRaises(ValueError):
            self.manager.analyze_video(self.request())
        self.assertTrue(self.manager.status()["hold"])
        self.assertFalse((self.manager.root / "video-one" / "input.mp4").exists())

    def test_failed_worker_requires_exact_explicit_review_then_only_new_ids(self):
        with mock.patch("ariadne.vision.run_vision", side_effect=WorkerError("camera occupied")), self.assertRaises(WorkerError):
            self.manager.analyze_video(self.request())
        fresh = Vision(self.registry, self.store)
        summary = fresh.detail("video-one")["receipt"]
        self.assertTrue(fresh.status()["hold"])
        self.assertFalse(summary["failure_reviewed"])
        with mock.patch("ariadne.vision.run_vision", side_effect=self.run_result), self.assertRaises(VisionConflict):
            fresh.analyze_video(self.request("different-id"))
        review = {"request_id": "video-one", "expected_revision": fresh.status()["revision"], "expected_control_revision": 0, "failure_sha256": "a" * 64}
        with self.assertRaises(VisionConflict):
            fresh.acknowledge_failure(review)
        review["failure_sha256"] = summary["failure_sha256"]
        self.assertEqual(fresh.acknowledge_failure(review)["status"], "reviewed")
        self.assertFalse(fresh.status()["hold"])
        with mock.patch("ariadne.vision.run_vision", side_effect=self.run_result):
            with self.assertRaises(VisionConflict):
                fresh.analyze_video(self.request())
            self.assertEqual(fresh.analyze_video(self.request("new-id"))["status"], "completed")
        self.assertEqual(fresh.detail("video-one")["receipt"]["status"], "reviewed")

    def test_pause_resume_fence_is_not_defeated_by_new_id(self):
        def revoke(payload, **kwargs):
            self.store.set_setting("paused", True)
            self.store.set_setting("paused", False)
            self.assertTrue(kwargs["cancelled"]())
            return self.run_result(payload, **kwargs)
        with mock.patch("ariadne.vision.run_vision", side_effect=revoke), self.assertRaises(VisionConflict):
            self.manager.analyze_video(self.request())
        self.assertEqual(self.manager.status()["control_revision"], 2)
        self.assertTrue(self.manager.status()["hold"])
        with self.assertRaises(VisionConflict):
            self.manager.analyze_video(self.request("new-after-pause"))

    def test_completed_artifact_tampering_holds_new_work(self):
        with mock.patch("ariadne.vision.run_vision", side_effect=self.run_result):
            self.manager.analyze_video(self.request())
            (self.manager.root / "video-one" / "evidence.json").write_text("{}", encoding="utf-8")
            self.assertTrue(self.manager.status()["hold"])
            with self.assertRaises(ValueError):
                self.manager.analyze_video(self.request("new-after-tamper"))

    def test_interrupted_acceptance_cannot_resume(self):
        request = self.manager._request(self.request(), "video")
        self.manager._reserve(request, "video")
        _ACTIVE.pop((self.manager.key, "video-one"))
        fresh = Vision(self.registry, self.store)
        self.assertEqual(fresh.detail("video-one")["receipt"]["status"], "interrupted")
        with mock.patch("ariadne.vision.run_vision") as worker, self.assertRaises(VisionConflict):
            fresh.analyze_video(self.request("fresh-id"))
        worker.assert_not_called()

    def test_camera_only_explicit_start_current_freshness_and_stop_without_mic(self):
        entered = threading.Event()
        def live(payload, **kwargs):
            row = frame()
            row["acquired_at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            row["timestamp_basis"] = "camera_source_pts"
            row["source_lag_ms"] = 0
            row["capture_elapsed_ms"] = 0
            kwargs["progress"](row)
            entered.set()
            while not kwargs["stop"]():
                if kwargs["cancelled"]():
                    raise VisionConflict("revoked")
                time.sleep(.01)
            return {"type": "result", "frames": [row], "duration_ms": 0, "decoded_frames": 1}
        with mock.patch.object(self.manager, "discover_cameras", return_value={"devices": [{"device_id": "usb-test"}]}), mock.patch("ariadne.vision.run_vision", side_effect=live):
            start = self.manager.start_camera({"request_id": "camera-one", "device_id": "usb-test", "expected_control_revision": 0})
            self.assertTrue(entered.wait(timeout=2))
            self.assertEqual(self.manager.status()["sessions"][0]["live"]["state"], "fresh")
            active = _ACTIVE[(self.manager.key, "camera-one")]
            original_time = active["latest"]["acquired_at"]
            active["latest"]["acquired_at"] = "2026-01-01T00:00:00.000Z"
            self.assertEqual(self.manager.status()["sessions"][0]["live"]["state"], "stale")
            active["latest"]["acquired_at"] = original_time
            active["latest"]["source_lag_ms"] = 5000
            self.assertEqual(self.manager.status()["sessions"][0]["live"]["state"], "stale")
            active["latest"]["source_lag_ms"] = 0
            with self.assertRaises(VisionConflict):
                self.manager.touch_camera({"session_id": "camera-one", "lease_token": "wrong"})
            self.manager.touch_camera({"session_id": "camera-one", "lease_token": start["lease_token"]})
            active["latest_monotonic"] = time.monotonic() - 10
            self.assertEqual(self.manager.status()["sessions"][0]["live"]["presence"], "unknown")
            self.manager.stop_camera({"session_id": "camera-one", "expected_control_revision": -1})
            active["thread"].join(timeout=2)
        self.assertEqual(self.manager.detail("camera-one")["live"]["state"], "off")
        self.assertEqual(self.manager.detail("camera-one")["receipt"]["status"], "completed")
        with self.assertRaises(VisionConflict):
            self.manager.touch_camera({"session_id": "camera-one", "lease_token": start["lease_token"]})

    def test_camera_owner_lease_expiry_stops_and_does_not_resume(self):
        def live(payload, **kwargs):
            active = _ACTIVE[(self.manager.key, "camera-expire")]
            active["lease_deadline"] = time.monotonic() - 1
            self.assertTrue(kwargs["stop"]())
            row = frame()
            row["acquired_at"] = "2026-11-12T00:00:00.000Z"
            return {"frames": [row], "duration_ms": 0, "decoded_frames": 1}
        with mock.patch.object(self.manager, "discover_cameras", return_value={"devices": [{"device_id": "usb-test"}]}), mock.patch("ariadne.vision.run_vision", side_effect=live):
            start = self.manager.start_camera({"request_id": "camera-expire", "device_id": "usb-test", "expected_control_revision": 0})
            _ACTIVE[(self.manager.key, "camera-expire")]["thread"].join(timeout=2)
        self.assertEqual(self.manager.detail("camera-expire")["receipt"]["stop_reason"], "owner_lease_expired")
        with self.assertRaises(VisionConflict):
            self.manager.touch_camera({"session_id": "camera-expire", "lease_token": start["lease_token"]})

    def test_explicit_stop_before_first_frame_is_verified_empty_terminal_not_hold(self):
        def empty(payload, **kwargs):
            active = _ACTIVE[(self.manager.key, "camera-empty")]
            active["stop"].set()
            return {"type": "result", "frames": [], "decoded_frames": 0, "duration_ms": 0}
        with mock.patch.object(self.manager, "discover_cameras", return_value={"devices": [{"device_id": "usb-test"}]}), mock.patch("ariadne.vision.run_vision", side_effect=empty):
            self.manager.start_camera({"request_id": "camera-empty", "device_id": "usb-test", "expected_control_revision": 0})
            _ACTIVE[(self.manager.key, "camera-empty")]["thread"].join(timeout=2)
        detail = self.manager.detail("camera-empty")
        self.assertEqual(detail["receipt"]["status"], "stopped_empty")
        self.assertTrue(detail["receipt"]["verified"])
        self.assertIsNone(detail["evidence"]["recording"])
        self.assertEqual(detail["evidence"]["frames"], [])
        self.assertFalse(self.manager.status()["hold"])
        with mock.patch("ariadne.vision.run_vision", side_effect=self.run_result):
            self.assertEqual(self.manager.analyze_video(self.request("after-empty-stop"))["status"], "completed")

    def test_reviewed_failure_tampering_still_blocks_new_work(self):
        with mock.patch("ariadne.vision.run_vision", side_effect=WorkerError("decoder failed")), self.assertRaises(WorkerError):
            self.manager.analyze_video(self.request())
        summary = self.manager.detail("video-one")["receipt"]
        self.manager.acknowledge_failure({"request_id": "video-one", "expected_revision": self.manager.status()["revision"], "expected_control_revision": 0, "failure_sha256": summary["failure_sha256"]})
        (self.manager.root / "video-one" / "failure.json").write_text("{}", encoding="utf-8")
        self.assertTrue(self.manager.status()["hold"])
        with self.assertRaises(VisionConflict):
            self.manager.analyze_video(self.request("after-failure-tamper"))

    def test_interrupted_temporary_input_requires_explicit_discard_review(self):
        request = self.manager._request(self.request(), "video")
        attempt, active = self.manager._reserve(request, "video")
        folder = self.manager.root / "video-one"
        self.manager._publish(folder / "attempt.json", _bytes(attempt))
        temporary = folder / "input.mp4"
        temporary.write_bytes(b"private interrupted temporary video")
        _ACTIVE.pop((self.manager.key, "video-one"))
        summary = self.manager.detail("video-one")["receipt"]
        self.assertEqual(summary["temporary_source"]["filename"], "input.mp4")
        self.assertTrue(summary["raw_video_retained"])
        review = {"request_id": "video-one", "expected_revision": self.manager.status()["revision"], "expected_control_revision": 0, "failure_sha256": summary["failure_sha256"]}
        with self.assertRaises(VisionConflict):
            self.manager.acknowledge_failure(review)
        self.assertTrue(temporary.exists())
        result = self.manager.acknowledge_failure({**review, "discard_temporary_source": True})
        self.assertEqual(result["discarded_temporary_source"]["bytes"], len(b"private interrupted temporary video"))
        self.assertFalse(temporary.exists())
        self.assertEqual(self.path.read_bytes()[:8], b"\0\0\0\x18ftyp")
        self.assertTrue((folder / "review.json").exists())
        self.assertFalse(self.manager.status()["hold"])

    def test_request_bounds_and_unsupported_fields_fail_before_acceptance(self):
        for extra in ({"duration_ms": MAX_DURATION_MS + 1}, {"sample_interval_ms": 1}, {"thumbnail": 1}, {"microphone": True}, {"model_url": "https://host/model"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.manager.analyze_video(self.request(**extra))
        self.assertFalse(self.manager.root.exists())


class DecoderBoundaryTests(unittest.TestCase):
    def test_camera_monikers_cannot_add_audio_or_change_input_grammar(self):
        from ariadne import _vision_worker as worker
        valid = "@device_pnp_usb#vid_0000&pid_0001#camera"
        class Inventory:
            def __init__(self, identity):
                self.stderr = io.BytesIO(('[dshow @ 0] "USB Camera" (video)\n[dshow @ 0] Alternative name "'+identity+'"\n').encode())
            def wait(self, timeout=None): return 1
            def poll(self): return 1
            def kill(self): pass
        for identity in (valid, valid+":audio=Other", valid+"=Other", valid+'"Other', valid+"'Other", valid+"\x7f"):
            with self.subTest(identity=identity), mock.patch.object(worker.os,"name","nt"), mock.patch.object(worker,"_popen",return_value=Inventory(identity)):
                if identity == valid:
                    self.assertEqual(len(worker.camera_inventory("sealed-ffmpeg")),1)
                else:
                    with self.assertRaises(ValueError):worker.camera_inventory("sealed-ffmpeg")

    def test_container_headers_reject_playlist_even_with_video_extension(self):
        from ariadne._vision_worker import container_header
        for raw in (b"#EXTM3U\nhttp://host/file", b"ffconcat version 1.0", b"http://host/video", b"file:E:/secrets"):
            with self.assertRaises(ValueError):
                container_header(io.BytesIO(raw))
        self.assertEqual(container_header(io.BytesIO(b"\0\0\0\x18ftypisom" + b"\0" * 32)), "mov")

    def test_recorded_native_decoder_uses_seekable_fd_only_and_no_audio(self):
        from ariadne import _vision_worker as worker
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            source = Path(temporary) / "input.mp4"
            source.write_bytes(b"\0\0\0\x18ftypisom" + b"\0" * 32)
            captured = []
            stderr = (b"[showinfo@source @ 0] n: 0 pts: 500 pts_time:0.5 fmt:yuv420p s:640x480\n"
                      b"[showinfo@selected @ 0] n: 0 pts: 500 pts_time:0.5 fmt:yuv420p s:640x480\n")
            class Child:
                def __init__(self):
                    self.stdout, self.stderr = io.BytesIO(b"\0" * worker.FRAME_BYTES), io.BytesIO(stderr)
                    self.returncode = 0
                def poll(self): return 0
                def wait(self, timeout=None): return 0
                def kill(self): pass
            def popen(command, **kwargs):
                self.assertEqual(os.lseek(kwargs["stdin"].fileno(), 0, os.SEEK_CUR), 0)
                captured.append((command, kwargs))
                return Child()
            detector = SimpleNamespace(np=SimpleNamespace(uint8=0, frombuffer=lambda *a, **k: SimpleNamespace(reshape=lambda *a: object())),
                                       infer=lambda value: ([], {"state": "unknown"}, "a" * 64, "b" * 64, 1.))
            with mock.patch.object(worker, "_popen", side_effect=popen), mock.patch.object(worker, "emit") as emit:
                result = worker.decode({"operation": "video", "source_path": str(source), "source_sha256": _sha(source.read_bytes()),
                                        "duration_ms": 30000, "sample_interval_ms": 1000, "thumbnail": False}, "sealed-ffmpeg", detector, threading.Event())
            args, kwargs = captured[0]
            self.assertEqual(args[args.index("-protocol_whitelist") + 1], "fd,pipe")
            self.assertEqual(args[args.index("-format_whitelist") + 1], "mov,matroska,avi")
            self.assertEqual(args[args.index("-i") + 1], "fd:")
            self.assertEqual(args[args.index("-fd") + 1], "0")
            self.assertIn("-an", args)
            self.assertNotIn(str(source), args)
            self.assertTrue(kwargs["stdin"].closed)
            self.assertEqual(result["decoded_frames"], 1)
            self.assertEqual(emit.call_args_list[0].args[0]["frame"]["source_pts_ms"], 500.)


if __name__ == "__main__":
    unittest.main()
