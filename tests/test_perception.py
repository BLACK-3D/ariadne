"""Offline qualification of the recording boundary, not sensing accuracy."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne.perception import (Perception, PerceptionConflict, MAX_SOURCE_BYTES,
                                fuse_recording, validate_bundle)
from ariadne.store import Store
from ariadne.tools import ToolRegistry


FIXTURES = Path(__file__).resolve().parents[1] / "ariadne" / "resources" / "perception"


def encoded(value):
    return json.dumps(value, allow_nan=False).encode("utf-8")


class PerceptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="perception-test-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root / "private", workspace=self.root / "workspace")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.manager = Perception(self.registry, self.store)
        self.bundle = json.loads((FIXTURES / "example-recording.json").read_text(encoding="utf-8"))

    def write(self, value=None, path="recording.json", raw=None):
        destination = self.registry.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw if raw is not None else encoded(self.bundle if value is None else value))
        return path

    def import_recording(self, identifier="import-1", value=None):
        return self.manager.import_bundle(self.write(value), identifier, expected_revision=self.manager.status()["revision"])

    def evidence(self, value=None):
        raw = encoded(self.bundle if value is None else value)
        return fuse_recording(validate_bundle(raw), hashlib.sha256(raw).hexdigest())

    def test_unused_reads_are_passive_and_advertise_no_live_or_models(self):
        self.assertFalse(self.manager.root.exists())
        for method in (self.manager.catalog, self.manager.status, self.manager.history, self.manager.list_recordings):
            self.assertIsInstance(method(), dict)
        self.assertFalse(self.manager.root.exists())
        self.assertEqual(self.manager.status()["revision"], 0)
        self.assertFalse(self.manager.status()["live_available"])
        self.assertEqual(self.manager.catalog()["model_calls"], 0)

    def test_real_import_replay_demo_and_saved_reads_deny_network_devices_models(self):
        source = self.write()
        original_open = os.open
        def guarded_open(path, *args, **kwargs):
            text = str(path).lower()
            if text.startswith(("/dev/", "\\\\.\\")) or "video0" in text:
                raise AssertionError("No device access permitted")
            return original_open(path, *args, **kwargs)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("No network")), \
             mock.patch("socket.create_connection", side_effect=AssertionError("No network")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("No processes or model")), \
             mock.patch("os.open", side_effect=guarded_open), \
             mock.patch.object(self.registry, "execute", side_effect=AssertionError("No tools/models/actions")):
            receipt = self.manager.import_bundle(source, "offline-import", expected_revision=0)
            replay = self.manager.replay("offline-import", "offline-replay", expected_revision=2)
            demo = self.manager.synthetic_demo("explicit-demo", expected_revision=4)
            self.assertTrue(self.manager.detail("offline-replay")["receipt"]["verified"])
            self.assertFalse(self.manager.status()["hold"])
            self.assertEqual(len(self.manager.history()["runs"]), 3)
        self.assertEqual(receipt["canonical_sha256"], replay["canonical_sha256"])
        self.assertEqual(demo["processing_mode"], "simulation")
        self.assertEqual(demo["capture_origins"], ["synthetic"])

    def test_same_frozen_input_has_identical_evidence_across_ids_and_parent_replays(self):
        first = self.import_recording()
        second = self.manager.replay("import-1", "replay-2")
        third = self.manager.replay("replay-2", "replay-3")
        self.assertEqual({row["canonical_sha256"] for row in (first, second, third)}, {first["canonical_sha256"]})
        before = self.manager.detail("import-1")["evidence"]
        after = self.manager.detail("replay-3")["evidence"]
        self.assertEqual(before, after)
        self.assertNotIn("completed_at", before)
        self.assertEqual(before["observations"][0]["received_at"], None)

    def test_live_capture_origin_never_promotes_import_or_replay_to_live(self):
        for row in self.bundle["observations"]:
            row.update(capture_origin="live", processing_mode="live", received_at="2026-01-01T12:00:03Z")
        receipt = self.import_recording()
        replay = self.manager.replay("import-1", "replay")
        detail = self.manager.detail("replay")
        self.assertEqual(receipt["capture_origins"], ["live"])
        self.assertEqual(replay["processing_mode"], "replay")
        self.assertTrue(all(row["processing_mode"] == "replay" for row in detail["evidence"]["observations"]))
        self.assertTrue(all(row["local_qualification_status"] == "recorded_unverified" for row in detail["evidence"]["observations"]))
        self.assertEqual(detail["evidence"]["observations"][0]["source_received_at"], "2026-01-01T12:00:03Z")
        self.assertEqual(detail["current_zones"][0]["presence"], "unknown")
        self.assertEqual(detail["current_zones"][0]["physical_freshness"], "stale")

    def test_synthetic_demo_stays_synthetic_after_replay_and_final_state_is_degraded(self):
        self.manager.synthetic_demo("demo")
        replay = self.manager.replay("demo", "demo-replay")
        detail = self.manager.detail("demo-replay")
        self.assertEqual(replay["capture_origins"], ["synthetic"])
        self.assertEqual(replay["processing_mode"], "replay")
        states = {row["state"] for row in detail["evidence"]["timeline"]}
        self.assertTrue({"known", "disagreement", "unknown", "stale"}.issubset(states))
        self.assertEqual(detail["evidence"]["zones"][0]["presence"], "unknown")

    def test_two_qualified_observations_debounce_presence_and_absence(self):
        for presence in ("present", "absent"):
            with self.subTest(presence=presence):
                data = copy.deepcopy(self.bundle)
                for row in data["observations"]:
                    row.update(presence=presence)
                evidence = self.evidence(data)
                self.assertEqual(evidence["timeline"][0]["presence"], "unknown")
                self.assertEqual(evidence["zones"][0]["presence"], presence)
                self.assertEqual(evidence["zones"][0]["state"], "known")

    def test_invalid_first_event_cannot_count_as_qualified_debounce(self):
        mutations = [
            {"quality_flags": ["occluded"]}, {"observed_at": None},
            {"upstream_source_state": "unavailable"}, {"clock_kind": "unknown"},
            {"observed_at": "2026-01-01T11:59:00Z"},
            {"observed_at": "2026-01-01T12:01:00Z"},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data = copy.deepcopy(self.bundle)
                data["observations"][0].update(mutation)
                zone = self.evidence(data)["zones"][0]
                self.assertEqual(zone["presence"], "unknown")
                self.assertIn("debouncing", zone["reasons"])

    def test_source_loss_false_presence_cannot_assert_an_empty_room(self):
        for state in ("unavailable", "disconnected", "stale", "waiting_for_frames", "invalid"):
            with self.subTest(state=state):
                data = copy.deepcopy(self.bundle)
                for row in data["observations"]:
                    row.update(presence="absent", upstream_source_state=state)
                zone = self.evidence(data)["zones"][0]
                self.assertEqual(zone["presence"], "unknown")
                self.assertIn("source_" + state, zone["reasons"])

    def test_missing_original_time_does_not_borrow_producer_or_reception_or_replay_time(self):
        for row in self.bundle["observations"]:
            row.update(observed_at=None, producer_at="2026-01-01T12:00:01Z", received_at="2026-01-01T12:00:01Z")
        zone = self.evidence()["zones"][0]
        self.assertIsNone(zone["source_age_seconds"])
        self.assertEqual(zone["physical_freshness"], "unknown")
        self.assertEqual(zone["presence"], "unknown")

    def test_stale_source_clock_clock_reset_epoch_and_future_time_fail_unknown(self):
        changes = [
            {"observed_at": "2026-01-01T11:59:00Z"},
            {"producer_boot_id": "new-boot"}, {"clock_epoch": "new-epoch"},
            {"observed_at": "2026-01-01T11:59:59Z"},
            {"observed_at": "2026-01-01T12:01:00Z"},
        ]
        expected = ["source_stale", "clock_reset", "clock_reset", "clock_regression", "future_timestamp"]
        for change, reason in zip(changes, expected):
            with self.subTest(change=change):
                data = copy.deepcopy(self.bundle)
                data["observations"][-1].update(change)
                zone = self.evidence(data)["zones"][0]
                self.assertEqual(zone["presence"], "unknown")
                self.assertIn(reason, zone["reasons"])

    def test_declared_missing_source_prevents_absence_and_moved_calibration_is_unknown(self):
        self.bundle["sensors"].append({"registered_sensor_alias": "missing-rf", "modality": "wifi_csi", "producer_revision": "test-1", "zone_id": "study"})
        self.assertIn("source_missing", self.evidence()["zones"][0]["reasons"])
        self.bundle["sensors"].pop()
        self.bundle["sensors"][0]["calibration_revision"] = "fixed-1"
        self.bundle["observations"][-1]["calibration_revision"] = "moved-2"
        zone = self.evidence()["zones"][0]
        self.assertEqual(zone["presence"], "unknown")
        self.assertIn("calibration_mismatch", zone["reasons"])

    def test_retained_source_normalized_evidence_attempt_and_receipt_tampering_are_rejected(self):
        self.import_recording()
        for name in ("source.json", "normalized.json", "evidence.json", "attempt.json", "receipt.json"):
            with self.subTest(artifact=name):
                path = self.manager.root / "import-1" / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b" ")
                    self.assertEqual(self.manager.status()["recordings"][0]["status"], "invalid")
                    self.assertTrue(self.manager.status()["hold"])
                    with self.assertRaises(ValueError):
                        self.manager.detail("import-1")
                    with self.assertRaises(ValueError):
                        self.manager.replay("import-1", "rejected-" + name.split(".")[0])
                finally:
                    path.write_bytes(original)
        self.assertFalse(self.manager.status()["hold"])

    def test_completed_same_id_returns_verified_receipt_without_writes(self):
        first = self.import_recording()
        with mock.patch.object(self.manager, "_publish", side_effect=AssertionError("Completed request must not republish")):
            second = self.manager.import_bundle("recording.json", "import-1")
        self.assertEqual(first, second)
        self.bundle["title"] = "Different input"
        self.write()
        with self.assertRaises(PerceptionConflict):
            self.manager.import_bundle("recording.json", "import-1")

    def test_revision_conflict_and_pause_before_dispatch_create_no_attempt(self):
        self.write()
        with self.assertRaises(PerceptionConflict):
            self.manager.import_bundle("recording.json", "denied", expected_revision=1)
        self.store.set_setting("paused", True)
        with self.assertRaises(PerceptionConflict):
            self.manager.import_bundle("recording.json", "denied")
        self.assertEqual(self.manager.history()["runs"], [])
        self.assertFalse(self.manager.root.exists())

    def test_pause_at_commit_holds_attempt_across_new_manager_and_new_ids(self):
        self.write()
        def checkpoint(phase):
            if phase == "before_commit":
                self.store.set_setting("paused", True)
        with mock.patch.object(self.manager, "_checkpoint", side_effect=checkpoint):
            with self.assertRaises(PerceptionConflict):
                self.manager.import_bundle("recording.json", "interrupted")
        self.assertFalse((self.manager.root / "interrupted" / "receipt.json").exists())
        self.store.set_setting("paused", False)
        restarted = Perception(self.registry, self.store)
        self.assertTrue(restarted.status()["hold"])
        self.assertEqual(restarted.detail("interrupted")["receipt"]["status"], "failed")
        for identifier in ("interrupted", "new-id"):
            with self.assertRaises(PerceptionConflict):
                restarted.import_bundle("recording.json", identifier)

    def test_external_revision_change_and_pause_then_resume_revoke_commit(self):
        self.write()
        def checkpoint(phase):
            if phase == "before_commit":
                self.store.set_setting("paused", True)
                self.store.set_setting("paused", False)
        with mock.patch.object(self.manager, "_checkpoint", side_effect=checkpoint):
            with self.assertRaises(PerceptionConflict):
                self.manager.import_bundle("recording.json", "revoked")
        self.assertTrue(self.manager.status()["hold"])

    def test_atomic_receipt_checkpoint_rechecks_revocation(self):
        self.write()
        from ariadne import perception
        real_publish = perception.publish_bytes
        revoked = False
        real_controls = self.manager._controls
        def controls():
            return 1 if revoked else real_controls()
        def publish(path, raw, *, checkpoint=None):
            nonlocal revoked
            if path.name == "receipt.json":
                revoked = True
            return real_publish(path, raw, checkpoint=checkpoint)
        with mock.patch("ariadne.perception.publish_bytes", side_effect=publish), \
             mock.patch.object(self.manager, "_controls", side_effect=controls):
            with self.assertRaises(PerceptionConflict):
                self.manager.import_bundle("recording.json", "atomic-revoked")
        self.assertFalse((self.manager.root / "atomic-revoked" / "receipt.json").exists())

    def test_concurrent_pause_is_serialized_with_receipt_publication(self):
        self.write()
        from ariadne import perception
        real_publish = perception.publish_bytes
        publishing, release, pause_done = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        def publish(path, raw, *, checkpoint=None):
            if path.name == "receipt.json":
                publishing.set()
                if not release.wait(5):
                    raise AssertionError("Test did not release receipt publication")
            return real_publish(path, raw, checkpoint=checkpoint)
        def run():
            try:
                results.append(self.manager.import_bundle("recording.json", "serialized"))
            except BaseException as error:
                errors.append(error)
        def pause():
            try:
                self.store.set_setting("paused", True)
            finally:
                pause_done.set()
        with mock.patch("ariadne.perception.publish_bytes", side_effect=publish):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(publishing.wait(5))
            writer = threading.Thread(target=pause)
            writer.start()
            try:
                self.assertFalse(pause_done.wait(0.1), "Pause must serialize after the held commit")
            finally:
                release.set()
                worker.join(5)
                writer.join(5)
        self.assertFalse(errors)
        self.assertEqual(results[0]["status"], "completed")
        self.assertTrue(pause_done.is_set())
        self.assertTrue(self.store.setting("paused"))
        self.assertTrue(self.manager.detail("serialized")["receipt"]["verified"])

    def test_artifact_failure_creates_hold_and_never_overwrites_or_retries(self):
        self.write()
        with mock.patch.object(self.manager, "_publish", side_effect=OSError("disk denied")):
            with self.assertRaises(OSError):
                self.manager.import_bundle("recording.json", "failed")
        self.assertTrue(self.manager.status()["hold"])
        self.assertEqual(self.manager.status()["recordings"][0]["status"], "failed")
        self.assertEqual(self.manager.status()["revision"], 2)
        with self.assertRaises(PerceptionConflict):
            self.manager.import_bundle("recording.json", "another-id")

    def test_crash_marker_is_inferred_as_hold_without_mutating_reads(self):
        self.import_recording()
        db = sqlite3.connect(self.manager.path)
        try:
            db.execute("UPDATE perception_attempts SET status='accepted',receipt_sha256=NULL")
            db.commit()
        finally:
            db.close()
        before = self.manager.path.read_bytes()
        restarted = Perception(self.registry, self.store)
        self.assertTrue(restarted.status()["hold"])
        self.assertEqual(restarted.history()["runs"][0]["status"], "interrupted")
        self.assertEqual(before, self.manager.path.read_bytes())
        with self.assertRaises(PerceptionConflict):
            restarted.import_bundle("recording.json", "after-crash")

    def test_malformed_oversized_duplicate_deep_and_nonfinite_json_are_premarker_rejections(self):
        samples = [b"{", b'{"a":1,"a":2}', b"[" * 13 + b"0" + b"]" * 13,
                   b'{"score":NaN}', b'{"score":Infinity}', b"\xff", b" " * (MAX_SOURCE_BYTES + 1)]
        for index, raw in enumerate(samples):
            with self.subTest(index=index):
                self.write(raw=raw)
                with self.assertRaises(ValueError):
                    self.manager.import_bundle("recording.json", "bad-" + str(index))
        self.assertFalse(self.manager.root.exists())
        self.assertFalse(self.manager.status()["hold"])

    def test_invalid_fields_nonfinite_overflow_sequences_offsets_and_time_reject_before_acceptance(self):
        mutations = [
            lambda b: b.update(extra="unsupported"),
            lambda b: b.update(schema_version=True),
            lambda b: b.update(duration_ms=True),
            lambda b: b.update(started_at="2026-11-12T12:00:00-06:00"),
            lambda b: b["observations"][1].update(event_id=b["observations"][0]["event_id"]),
            lambda b: b["observations"][1].update(sequence=1),
            lambda b: b["observations"][1].update(sequence=True),
            lambda b: b["observations"][0].update(replay_offset_ms=2000),
            lambda b: b["observations"][1].update(replay_offset_ms=-1),
            lambda b: b["observations"][1].update(observed_at="2026-13-01T12:00:01Z"),
            lambda b: b["observations"][1].update(local_qualification_status="live_verified"),
            lambda b: b["observations"][1].update(source_artifact_hash="a" * 64),
            lambda b: b["observations"][1].update(coordinate_frame="metric"),
            lambda b: b["observations"][1].update(score=1e309),
            lambda b: b["observations"][1].update(score=0.7, score_kind="probability"),
            lambda b: b["observations"][1].update(supporting_observation_ids=[{}]),
            lambda b: b["observations"][1].update(supporting_observation_ids=["nonexistent"]),
            lambda b: b["observations"][1].update(objects=[{"label": "chair", "person_id": "forbidden"}]),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                data = copy.deepcopy(self.bundle)
                mutate(data)
                raw = json.dumps(data).encode("utf-8")
                self.write(raw=raw)
                with self.assertRaises(ValueError):
                    self.manager.import_bundle("recording.json", "invalid-" + str(index))
        self.assertEqual(self.manager.history()["runs"], [])

    def test_reordered_offsets_and_duplicate_producer_ids_are_rejected(self):
        self.bundle["observations"].reverse()
        with self.assertRaises(ValueError):
            validate_bundle(encoded(self.bundle))
        self.bundle["observations"].reverse()
        for row in self.bundle["observations"]:
            row["source_event_id"] = "same-producer-id"
        with self.assertRaises(ValueError):
            validate_bundle(encoded(self.bundle))

    def test_traversal_absolute_device_and_nonjson_paths_reject_without_writes(self):
        for path in ("../escape.json", "E:/never.json", "C:/outside.json", "\\\\.\\video0", "/dev/video0", "NUL.json", "recording.txt", "nested/../recording.json"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.manager.import_bundle(path, "denied")
        for identifier in ("../bad", "bad/name", "E:", "", "a" * 65):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    self.manager.synthetic_demo(identifier)
        self.assertFalse(self.manager.root.exists())

    def test_hardlinked_input_and_retained_artifact_are_denied(self):
        original = self.root / "original.json"
        original.write_bytes(encoded(self.bundle))
        linked = self.registry.root / "linked.json"
        linked.parent.mkdir(parents=True, exist_ok=True)
        os.link(original, linked)
        with self.assertRaises(ValueError):
            self.manager.import_bundle("linked.json", "hardlink")
        linked.unlink()
        self.import_recording()
        artifact = self.manager.root / "import-1" / "source.json"
        os.link(artifact, self.root / "alias.json")
        with self.assertRaises(ValueError):
            self.manager.detail("import-1")
        self.assertTrue(self.manager.status()["hold"])

    def test_symlink_or_reparse_input_and_state_are_denied(self):
        source = self.root / "original.json"
        source.write_bytes(encoded(self.bundle))
        link = self.registry.root / "linked.json"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(source)
        except OSError as error:
            self.skipTest("Host does not grant symlink creation: " + str(error))
        with self.assertRaises(ValueError):
            self.manager.import_bundle("linked.json", "symlink")
        state_link = self.manager.root
        state_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.manager.status()

    def test_reparse_attribute_is_denied_before_source_or_state_open(self):
        self.write()
        original_lstat = Path.lstat
        protected = self.registry.root / "recording.json"
        def reparse(path, *args, **kwargs):
            if path == protected:
                return SimpleNamespace(st_mode=0o100600, st_file_attributes=0x400)
            return original_lstat(path, *args, **kwargs)
        with mock.patch.object(Path, "lstat", new=reparse), \
             mock.patch.object(self.manager, "_read", side_effect=AssertionError("Reparse input must be denied before opening")):
            with self.assertRaises(ValueError):
                self.manager.import_bundle("recording.json", "reparse")
        protected = self.manager.root
        with mock.patch.object(Path, "lstat", new=reparse):
            with self.assertRaises(ValueError):
                self.manager.status()
        self.assertFalse(self.manager.root.exists())

    def test_maximum_timestamp_uses_bounded_subtraction_for_clock_skew(self):
        self.bundle.update(started_at="9999-12-31T23:59:59Z", duration_ms=0)
        self.bundle["observations"] = self.bundle["observations"][:1]
        self.bundle["observations"][0].update(observed_at="9999-12-31T23:59:59Z", replay_offset_ms=0)
        evidence = self.evidence()
        self.assertEqual(evidence["zones"][0]["presence"], "unknown")
        self.assertNotIn("future_timestamp", evidence["zones"][0]["reasons"])
        self.bundle["duration_ms"] = 2000
        with self.assertRaises(ValueError):
            validate_bundle(encoded(self.bundle))

    def test_fixed_capacity_denial_is_premarker_and_never_deletes_existing_evidence(self):
        first = self.import_recording()
        with mock.patch("ariadne.perception.MAX_ATTEMPTS", 1):
            with self.assertRaises(ValueError):
                self.manager.replay("import-1", "overflow")
        self.assertEqual(self.manager.detail("import-1")["receipt"]["canonical_sha256"], first["canonical_sha256"])
        with mock.patch("ariadne.perception.MAX_TOTAL_BYTES", 1):
            with self.assertRaises(ValueError):
                self.manager.replay("import-1", "byte-overflow")
        self.assertFalse(self.manager.status()["hold"])

    def test_recording_timeline_and_current_freshness_are_separate(self):
        self.import_recording()
        detail = self.manager.detail("import-1")
        self.assertEqual(detail["evidence"]["zones"][0]["presence"], "present")
        self.assertEqual(detail["evidence"]["zones"][0]["physical_freshness"], "fresh")
        self.assertEqual(detail["current_zones"][0]["physical_freshness"], "stale")
        self.assertEqual(detail["current_zones"][0]["presence"], "unknown")
        self.assertEqual(detail["evidence"]["freshness_reference"], "original_recording_timeline_only")
        self.assertEqual(detail["evidence"]["observations"][0]["observed_at"], "2026-01-01T12:00:00Z")

    def test_unknown_sensor_zone_and_unsupported_capture_source_reject(self):
        for mutate in (
            lambda b: b["observations"][0].update(registered_sensor_alias="unregistered"),
            lambda b: b["sensors"][0].update(zone_id="unregistered"),
            lambda b: b["observations"][0].update(capture_origin="auto"),
            lambda b: b["observations"][0].update(upstream_source_state="synthetic_fallback"),
        ):
            data = copy.deepcopy(self.bundle)
            mutate(data)
            with self.assertRaises(ValueError):
                validate_bundle(encoded(data))


if __name__ == "__main__":
    unittest.main()
