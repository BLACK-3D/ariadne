"""Offline native-recording integration, not RF sensing qualification."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne.perception import Perception, PerceptionConflict, MAX_SOURCE_BYTES
from ariadne.store import Store
from ariadne.tools import ToolRegistry


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "ariadne/resources/perception"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode()


class RuViewPerceptionTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="ruview-core-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(data_dir=self.root / "private", workspace=self.root / "workspace")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.manager = Perception(self.registry, self.store)
        self.synthetic = (FIXTURES / "ruview-synthetic.jsonl").read_bytes()
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo",
                       "urllib.request.urlopen", "subprocess.Popen"):
            guard = mock.patch(target, side_effect=AssertionError("No network, sensors, workers or models"))
            guard.start()
            self.addCleanup(guard.stop)
        guard = mock.patch.object(self.registry, "execute", side_effect=AssertionError("No tools or models"))
        guard.start()
        self.addCleanup(guard.stop)

    def native(self):
        # Explicit fictional esp32 producer claims for parser/integrity checks.
        # This is not a physical capture and is never published as one.
        output = []
        for line in self.synthetic.splitlines():
            frame = json.loads(line)
            if frame.get("type") == "sensing_update":
                frame["source"] = "esp32"
            output.append(encoded(frame))
        return b"".join(output)

    def write(self, raw=None, path="perception/selected.jsonl"):
        selected = self.registry.root / path
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_bytes(self.native() if raw is None else raw)
        return path

    def import_native(self, request_id="native-import", **kwargs):
        return self.manager.import_ruview(self.write(), request_id,
            expected_revision=self.manager.status()["revision"], **kwargs)

    def reseal(self, request_id, artifact_name=None, value=None, receipt_update=None, attempt_update=None):
        """Simulate coherent derived-file/hash edits; conversion must still detect it."""
        folder = self.manager.root / request_id
        receipt_path = folder / "receipt.json"
        receipt = json.loads(receipt_path.read_bytes())
        if attempt_update:
            attempt = json.loads((folder / "attempt.json").read_bytes())
            attempt.update(attempt_update)
            raw = encoded(attempt)
            (folder / "attempt.json").write_bytes(raw)
            artifact = next(item for item in receipt["artifacts"] if item["name"] == "attempt.json")
            artifact.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        if artifact_name is not None:
            raw = encoded(value)
            (folder / artifact_name).write_bytes(raw)
            artifact = next(item for item in receipt["artifacts"] if item["name"] == artifact_name)
            artifact.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            if artifact_name == "evidence.json":
                receipt["canonical_sha256"] = artifact["sha256"]
        if receipt_update:
            receipt.update(receipt_update)
        raw = encoded(receipt)
        receipt_path.write_bytes(raw)
        with self.manager._database(create=True) as db:
            db.execute("UPDATE perception_attempts SET receipt=?,receipt_sha256=? WHERE request_id=?",
                       (raw.decode(), hashlib.sha256(raw).hexdigest(), request_id))

    def test_passive_metadata_does_not_create_state_or_advertise_live_hardware(self):
        catalog = self.manager.catalog()
        state = self.manager.status()
        self.assertFalse(self.manager.root.exists())
        self.assertFalse(state["live_available"])
        self.assertFalse(catalog["ruview"]["hardware_qualified"])
        self.assertEqual(catalog["ruview"]["source_policies"], ["esp32_only", "recorded_any"])
        self.assertEqual(state["ruview"], catalog["ruview"])
        self.assertIn("import_ruview", {item["id"] for item in catalog["operations"]})

    def test_synthetic_and_invalid_policy_are_rejected_before_attempt_acceptance(self):
        selected = self.write(self.synthetic)
        with self.assertRaises(ValueError):
            self.manager.import_ruview(selected, "default-cannot-admit-simulator", expected_revision=0)
        for policy in (None, True, [], {}, "auto", "live", "ESP32"):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.manager.import_ruview(selected, "invalid-policy", source_policy=policy, expected_revision=0)
        self.assertEqual(self.manager.status()["revision"], 0)
        self.assertFalse(self.manager.root.exists())

    def test_invalid_policy_is_rejected_before_any_workspace_path_or_read(self):
        with mock.patch.object(self.registry, "_path", side_effect=AssertionError("No selected source access")):
            for policy in (None, True, [], {}, "auto"):
                with self.subTest(policy=policy), self.assertRaises(ValueError):
                    self.manager.import_ruview("missing.jsonl", "invalid-before-file", source_policy=policy)
        self.assertFalse(self.manager.root.exists())

    def test_native_bytes_and_exact_profile_are_retained_with_unknown_physical_clock(self):
        raw = self.native()
        selected = self.write(raw)
        receipt = self.manager.import_ruview(selected, "native-import", expected_revision=0)
        detail = self.manager.detail("native-import")
        folder = self.manager.root / "native-import"
        self.assertEqual((folder / "source.json").read_bytes(), raw)
        self.assertEqual({item["name"] for item in receipt["artifacts"]},
            {"attempt.json", "source.json", "bundle.json", "adapter.json", "normalized.json", "evidence.json"})
        self.assertEqual(receipt["operation"], "import_ruview")
        self.assertEqual(receipt["source_policy"], "esp32_only")
        self.assertEqual(receipt["source_format"], "ruview_native_jsonl")
        self.assertFalse(receipt["hardware_qualified"])
        self.assertEqual(receipt["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(detail["adapter"]["source_sha256"], receipt["source_sha256"])
        self.assertEqual(detail["adapter"]["profile"], receipt["source_profile"])
        self.assertEqual(detail["adapter"]["bundle_sha256"], receipt["bundle_sha256"])
        self.assertTrue(all(row["observed_at"] is None and row["sequence"] is None
                            for row in detail["evidence"]["observations"]))
        self.assertTrue(all(zone["presence"] == zone["motion"] == "unknown"
                            and zone["physical_freshness"] == "unknown" for zone in detail["current_zones"]))
        self.assertFalse(detail["evidence"]["live_available"])
        self.assertTrue(detail["receipt"]["verified"])

    def test_replay_uses_frozen_native_source_after_workspace_file_changes(self):
        original = self.import_native()
        (self.registry.root / "perception/selected.jsonl").write_bytes(b"not a recording")
        replay = self.manager.replay("native-import", "native-replay", expected_revision=2)
        again = self.manager.replay("native-replay", "native-replay-two", expected_revision=4)
        self.assertEqual({item["canonical_sha256"] for item in (original, replay, again)},
                         {original["canonical_sha256"]})
        for item in (replay, again):
            self.assertEqual(item["source_profile"], original["source_profile"])
            self.assertEqual(item["source_sha256"], original["source_sha256"])
            self.assertEqual(item["bundle_sha256"], original["bundle_sha256"])
            self.assertEqual(item["adapter_sha256"], original["adapter_sha256"])
        self.assertEqual(self.manager.detail("native-import")["evidence"],
                         self.manager.detail("native-replay-two")["evidence"])

    def test_completed_same_id_is_read_only_but_policy_changes_cannot_reuse_it(self):
        original = self.import_native()
        before = {path.name: path.read_bytes() for path in (self.manager.root / "native-import").iterdir()}
        repeated = self.manager.import_ruview("perception/selected.jsonl", "native-import", expected_revision=2)
        self.assertEqual(repeated, original)
        self.assertEqual(before, {path.name: path.read_bytes() for path in (self.manager.root / "native-import").iterdir()})
        self.assertEqual(self.manager.status()["revision"], 2)
        with self.assertRaises(PerceptionConflict):
            self.manager.import_ruview("perception/selected.jsonl", "native-import",
                source_policy="recorded_any", expected_revision=2)

    def test_explicit_ruview_demo_and_replay_preserve_synthetic_origin(self):
        receipt = self.manager.ruview_demo("ruview-demo", expected_revision=0)
        self.assertEqual(receipt["operation"], "ruview_demo")
        self.assertEqual(receipt["processing_mode"], "simulation")
        self.assertEqual(receipt["capture_origins"], ["synthetic"])
        self.assertFalse(receipt["hardware_qualified"])
        replay = self.manager.replay("ruview-demo", "demo-replay", expected_revision=2)
        self.assertEqual(replay["processing_mode"], "replay")
        self.assertEqual(replay["capture_origins"], ["synthetic"])
        self.assertEqual(replay["source_sha256"], receipt["source_sha256"])
        self.assertFalse(self.manager.detail("demo-replay")["adapter"]["hardware_qualified"])

    def test_legacy_four_artifact_layout_and_frozen_replay_remain_compatible(self):
        selected = self.registry.root / "legacy.json"
        selected.write_bytes((FIXTURES / "example-recording.json").read_bytes())
        receipt = self.manager.import_bundle("legacy.json", "legacy", expected_revision=0)
        self.assertEqual({a["name"] for a in receipt["artifacts"]},
                         {"attempt.json", "source.json", "normalized.json", "evidence.json"})
        self.assertNotIn("source_profile", receipt)
        self.assertNotIn("adapter", self.manager.detail("legacy"))
        replay = self.manager.replay("legacy", "legacy-replay", expected_revision=2)
        self.assertEqual(replay["canonical_sha256"], receipt["canonical_sha256"])

    def test_raw_source_tampering_invalidates_retained_evidence_and_holds_new_work(self):
        self.import_native()
        source = self.manager.root / "native-import/source.json"
        source.write_bytes(source.read_bytes() + b" ")
        self.assertTrue(self.manager.status()["hold"])
        with self.assertRaises(ValueError):
            self.manager.detail("native-import")
        with self.assertRaises(ValueError):
            self.manager.ruview_demo("cannot-bypass-invalid-source", expected_revision=2)

    def test_rehashed_derived_evidence_still_must_recompute_from_native_source(self):
        self.import_native()
        evidence = self.manager.detail("native-import")["evidence"]
        evidence["title"] = "Coherently rehashed but not source-derived"
        self.reseal("native-import", "evidence.json", evidence)
        with self.assertRaisesRegex(ValueError, "frozen native source"):
            self.manager.detail("native-import")
        self.assertTrue(self.manager.status()["hold"])

    def test_rehashed_converted_bundle_is_not_adopted_as_native_truth(self):
        self.import_native()
        folder = self.manager.root / "native-import"
        bundle = json.loads((folder / "bundle.json").read_bytes())
        bundle["title"] = "Changed converted input"
        self.reseal("native-import", "bundle.json", bundle)
        with self.assertRaisesRegex(ValueError, "frozen native source"):
            self.manager.detail("native-import")

    def test_unknown_adapter_profile_and_forged_live_qualification_are_rejected(self):
        self.import_native()
        self.reseal("native-import", receipt_update={"source_profile": "unreviewed-profile"})
        with self.assertRaisesRegex(ValueError, "adapter profile"):
            self.manager.detail("native-import")

    def test_no_physical_qualification_can_be_injected_even_with_new_receipt_hash(self):
        self.import_native()
        self.reseal("native-import", receipt_update={"hardware_qualified": True})
        with self.assertRaisesRegex(ValueError, "profile or source binding"):
            self.manager.detail("native-import")

    def test_retained_receipt_cannot_claim_live_availability_after_rehash(self):
        self.import_native()
        self.reseal("native-import", receipt_update={"live_available": True})
        with self.assertRaisesRegex(ValueError, "source qualification"):
            self.manager.detail("native-import")

    def test_resealed_attempt_and_receipt_cannot_launder_capture_origin(self):
        self.import_native()
        self.reseal("native-import", receipt_update={"capture_origins": ["live"]},
                    attempt_update={"capture_origins": ["live"]})
        with self.assertRaisesRegex(ValueError, "source qualification"):
            self.manager.detail("native-import")

    def test_pause_and_revision_conflicts_are_preacceptance_and_preserve_controls(self):
        selected = self.write()
        self.store.set_setting("paused", True)
        with self.assertRaises(PerceptionConflict):
            self.manager.import_ruview(selected, "paused-import", expected_revision=0)
        self.assertTrue(self.store.setting("paused"))
        self.assertFalse(self.manager.root.exists())
        self.store.set_setting("paused", False)
        with self.assertRaises(PerceptionConflict):
            self.manager.import_ruview(selected, "wrong-revision", expected_revision=3)
        self.assertFalse(self.manager.root.exists())

    def test_accepted_revocation_survives_new_manager_without_replay_or_hold_clear(self):
        selected = self.write()
        def revoke(phase):
            if phase == "accepted":
                self.store.set_setting("paused", True)
        with mock.patch.object(self.manager, "_checkpoint", side_effect=revoke):
            with self.assertRaises(PerceptionConflict):
                self.manager.import_ruview(selected, "revoked-import", expected_revision=0)
        fresh = Perception(self.registry, self.store)
        self.assertTrue(fresh.status()["hold"])
        self.assertTrue(fresh.status()["paused"])
        self.store.set_setting("paused", False)
        with self.assertRaises(PerceptionConflict):
            fresh.import_ruview(selected, "new-id-not-permission", expected_revision=fresh.status()["revision"])
        self.assertTrue(fresh.status()["hold"])

    def test_input_and_retained_capacity_rejections_leave_no_attempt(self):
        selected = self.write(b" " * (MAX_SOURCE_BYTES + 1))
        with self.assertRaises(ValueError):
            self.manager.import_ruview(selected, "too-large", expected_revision=0)
        selected = self.write()
        with mock.patch("ariadne.perception.MAX_TOTAL_BYTES", 1):
            with self.assertRaises(ValueError):
                self.manager.import_ruview(selected, "no-capacity", expected_revision=0)
        self.assertFalse(self.manager.root.exists())

    def test_paths_and_extensions_cannot_expand_workspace_access(self):
        for path in ("../outside.jsonl", "C:/outside.jsonl", "capture.json", "capture.exe"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.manager.import_ruview(path, "bad-path", expected_revision=0)
        self.assertFalse(self.manager.root.exists())


if __name__ == "__main__":
    unittest.main()
