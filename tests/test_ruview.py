"""Offline contract tests; fictional input is not RF/hardware qualification."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import unittest
from unittest import mock
import urllib.request

from ariadne.perception import _bytes, fuse_recording, validate_bundle
from ariadne.ruview import (MAX_LINE_BYTES, MAX_MESSAGES, PROFILE, UPSTREAM_REVISION,
                            normalize_ruview)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "ariadne/resources/perception/ruview-synthetic.jsonl"


def encode(*rows):
    return b"\n".join(json.dumps(row, allow_nan=False, separators=(",", ":")).encode()
                      for row in rows) + b"\n"


def frame(tick=1, *, source="esp32", timestamp=1_794_484_800):
    value = json.loads(FIXTURE.read_bytes().splitlines()[0])
    value.update(source=source, tick=tick, timestamp=timestamp)
    return value


def edge():
    return json.loads(FIXTURE.read_bytes().splitlines()[2])


class RuViewTests(unittest.TestCase):
    def normalize(self, *rows, policy="esp32_only"):
        return normalize_ruview(encode(*(rows or (frame(),))), source_policy=policy)

    def test_native_fixture_is_explicitly_synthetic_and_default_policy_rejects_it(self):
        raw = FIXTURE.read_bytes()
        with self.assertRaises(ValueError):
            normalize_ruview(raw)
        result = normalize_ruview(raw, source_policy="recorded_any")
        self.assertEqual(result["provenance"]["capture_origins"], ["synthetic"])
        self.assertEqual(result["provenance"]["sensing_observation_count"], 7)
        self.assertEqual(result["provenance"]["ignored_message_counts"], {"edge_vitals": 1})
        self.assertIn("not RF capture", result["bundle"]["title"])
        self.assertTrue(all(row["capture_origin"] == "synthetic" for row in result["bundle"]["observations"]))

    def test_exact_bytes_bind_source_lines_bundle_and_provenance_deterministically(self):
        raw = encode(frame())
        first = normalize_ruview(raw)
        second = normalize_ruview(raw)
        self.assertEqual(first, second)
        metadata = first["provenance"]
        self.assertEqual(metadata["profile"], PROFILE)
        self.assertEqual(metadata["upstream_revision"], UPSTREAM_REVISION)
        self.assertEqual(metadata["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(metadata["frames"][0]["raw_line_sha256"], hashlib.sha256(raw.splitlines()[0]).hexdigest())
        self.assertEqual(metadata["normalized_sha256"], hashlib.sha256(_bytes(first["bundle"])).hexdigest())
        self.assertEqual(metadata["bundle_sha256"], metadata["normalized_sha256"])
        changed = normalize_ruview(encode(frame(timestamp=1_794_484_801)))
        self.assertNotEqual(changed["provenance"]["source_sha256"], metadata["source_sha256"])
        self.assertNotEqual(changed["provenance"]["normalized_sha256"], metadata["normalized_sha256"])

    def test_validated_raw_bundle_does_not_invent_radio_clocks_sequences_or_qualifications(self):
        result = self.normalize(frame(), frame(2, timestamp=1_794_484_801.250))
        bundle = validate_bundle(_bytes(result["bundle"]))
        self.assertEqual(bundle["duration_ms"], 1250)
        self.assertEqual(bundle["observations"][1]["replay_offset_ms"], 1250)
        for row in bundle["observations"]:
            self.assertIsNone(row["observed_at"])
            self.assertIsNone(row["sequence"])
            self.assertIsNone(row["source_received_at"])
            self.assertEqual(row["producer_boot_id"], "ruview-boot-unknown")
            self.assertEqual(row["clock_kind"], "unknown")
            self.assertEqual(row["capture_origin"], "unknown")
            self.assertEqual(row["local_qualification_status"], "recorded_unverified")
            self.assertEqual(row["processing_mode"], "replay")
            self.assertEqual(row["presence"], "present")
        evidence = fuse_recording(bundle, result["provenance"]["source_sha256"])
        self.assertTrue(all(row["presence"] == "unknown" for row in evidence["zones"]))
        self.assertFalse(result["provenance"]["physical_clock_available"])
        self.assertFalse(result["provenance"]["hardware_qualified"])
        self.assertFalse(result["provenance"]["action_authority"])

    def test_default_policy_rejects_fallback_anywhere_without_partial_output(self):
        for source in ("auto", "simulated", "simulate", "synthetic", "test", "wifi:fictional-network",
                       "mediatek", "qualcomm", "realtek_csi", "realtek_csi:simulated", "unrecognized"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.normalize(frame(), frame(2, source=source))

    def test_recorded_any_keeps_rssi_distinct_and_private_label_out_of_bundle(self):
        private = "wifi:fictional-private-network"
        result = self.normalize(frame(source=private), policy="recorded_any")
        self.assertEqual(result["bundle"]["sensors"][0]["modality"], "wifi_rssi")
        self.assertNotIn(private, json.dumps(result["bundle"]))
        self.assertEqual(result["provenance"]["frames"][0]["source"], private)
        with self.assertRaises(ValueError) as error:
            self.normalize(frame(source=private))
        self.assertNotIn(private, str(error.exception))

    def test_recognized_vendor_detector_remains_unavailable_not_verified(self):
        result = self.normalize(frame(source="mediatek"), policy="recorded_any")
        row = result["bundle"]["observations"][0]
        self.assertEqual(row["presence"], "unknown")
        self.assertIn("experimental_detector", row["quality_flags"])
        self.assertEqual(row["capture_origin"], "unknown")

    def test_explicit_offline_source_is_retained_as_disconnected(self):
        row = self.normalize(frame(source="esp32:offline"))["bundle"]["observations"][0]
        self.assertEqual(row["upstream_source_state"], "disconnected")
        self.assertEqual(row["presence"], "unknown")

    def test_unavailable_room_false_presence_never_becomes_absence(self):
        value = frame()
        value["classification"].update(motion_level="absent", presence=False, confidence=0)
        value["room_inference"].update(classification="unavailable", confidence=0, contributing_nodes=0)
        row = self.normalize(value)["bundle"]["observations"][0]
        self.assertEqual((row["presence"], row["motion"], row["score"]), ("unknown", "unknown", None))
        self.assertEqual(row["score_kind"], "unavailable")
        self.assertIn("room_unavailable", row["quality_flags"])

    def test_absence_requires_consistent_nonstale_complete_producer_evidence(self):
        value = frame()
        value["classification"].update(motion_level="absent", presence=False)
        value["room_inference"]["classification"] = "absent"
        value["nodes"][0]["node_inference"]["classification"] = "absent"
        good = self.normalize(value)["bundle"]["observations"][0]
        self.assertEqual(good["presence"], "absent")
        mutations = [lambda x: x.pop("room_inference"),
                     lambda x: x.update(nodes=[]),
                     lambda x: x["nodes"][0].pop("node_inference"),
                     lambda x: x["nodes"][0]["node_inference"].pop("age_ms"),
                     lambda x: x["nodes"][0]["node_inference"].update(age_ms=10_000),
                     lambda x: x["room_inference"].update(contributing_nodes=0),
                     lambda x: x["room_inference"].update(confidence=0),
                     lambda x: x["nodes"][0]["node_inference"].update(confidence=0),
                     lambda x: x.update(age_ms=10_000),
                     lambda x: x.update(quality_verdict="Deny"),
                     lambda x: x.update(quality_verdict="Warn")]
        for mutate in mutations:
            changed = copy.deepcopy(value)
            mutate(changed)
            with self.subTest(change=mutate):
                row = self.normalize(changed)["bundle"]["observations"][0]
                self.assertEqual(row["presence"], "unknown")
                self.assertIsNone(row["score"])

    def test_disagreeing_nodes_and_classification_are_not_laundered_into_room_absence(self):
        value = frame()
        other = copy.deepcopy(value["nodes"][0])
        other["node_id"] = 2
        other["node_inference"]["classification"] = "absent"
        value["nodes"].append(other)
        value["room_inference"].update(classification="absent", contributing_nodes=2)
        value["classification"].update(motion_level="absent", presence=False)
        result = self.normalize(value)
        row = result["bundle"]["observations"][0]
        self.assertEqual(row["presence"], "unknown")
        self.assertIn("node_disagreement", row["quality_flags"])
        self.assertEqual(len(result["provenance"]["frames"][0]["nodes"]), 2)
        value = frame()
        value["classification"].update(motion_level="absent", presence=False)
        row = self.normalize(value)["bundle"]["observations"][0]
        self.assertEqual(row["presence"], "unknown")
        self.assertIn("classification_conflict", row["quality_flags"])

    def test_motion_disagreement_does_not_invent_common_motion(self):
        value = frame()
        other = copy.deepcopy(value["nodes"][0])
        other["node_id"] = 2
        other["node_inference"]["classification"] = "present_still"
        value["nodes"].append(other)
        value["room_inference"]["contributing_nodes"] = 2
        row = self.normalize(value)["bundle"]["observations"][0]
        self.assertEqual(row["presence"], "present")
        self.assertEqual(row["motion"], "unknown")
        self.assertIn("motion_disagreement", row["quality_flags"])

    def test_ignored_biometrics_pose_coordinates_and_raw_csi_are_not_copied(self):
        value, ancillary = frame(), edge()
        ancillary.update(breathing_rate_bpm=991.25, heartrate_bpm=992.25)
        value.update(vital_signs={"breathing_rate_bpm": 993.25},
                     persons=[{"private_measurement": 994.25}],
                     pose_keypoints=[[995.25, 0, 0, 1]])
        result = self.normalize(ancillary, value)
        serialized = json.dumps(result)
        for token in ("991.25", "992.25", "993.25", "994.25", "995.25", "breathing_rate_bpm",
                      "heartrate_bpm", "pose_keypoints", '"persons"', '"amplitude"', '"position"', '"signal_field"'):
            self.assertNotIn(token, serialized)
        self.assertEqual(result["provenance"]["ignored_message_counts"], {"edge_vitals": 1})
        self.assertEqual(result["provenance"]["frames"][0]["source_line"], 2)

    def test_nonfinite_and_exponent_overflow_are_rejected_even_in_ignored_fields(self):
        raw = encode(frame())
        for token in (b"NaN", b"Infinity", b"-Infinity", b"1e999"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                normalize_ruview(raw.replace(b'"tick":1', b'"model_status":{"ignored":' + token + b'},"tick":1'))
            ignored = encode(edge()).replace(b'"motion_energy":0', b'"motion_energy":' + token)
            with self.subTest(ignored=token), self.assertRaises(ValueError):
                normalize_ruview(ignored + raw)

    def test_duplicate_keys_unrecognized_schema_and_message_kinds_fail_before_acceptance(self):
        raw = encode(frame())
        invalid = [raw.replace(b'"tick":1', b'"tick":1,"tick":2'), b"[]\n", b"{}\n", b"\xff\n",
                   b"\n" + raw, encode({"type": "wasm_event"}), encode(edge()),
                   encode({**frame(), "unreviewed_field": True}),
                   encode({**frame(), "source_state": "live_verified"})]
        for changed in invalid:
            with self.subTest(raw=changed[:30]), self.assertRaises(ValueError):
                normalize_ruview(changed)
        nested = {"leaf": 1}
        for _ in range(20):
            nested = {"nested": nested}
        with self.assertRaises(ValueError):
            self.normalize({**frame(), "model_status": nested})

    def test_duplicate_or_regressing_ticks_and_server_time_require_separate_recordings(self):
        for second in (frame(1, timestamp=1_794_484_801), frame(0, timestamp=1_794_484_801),
                       frame(2, timestamp=1_794_484_799)):
            with self.subTest(second=second["tick"]), self.assertRaises(ValueError):
                self.normalize(frame(), second)
        # Multiple accepted updates may share a server millisecond; no radio
        # clock or synthetic time increment is invented to distinguish them.
        result = self.normalize(frame(), frame(2))
        self.assertEqual(result["bundle"]["duration_ms"], 0)
        self.assertEqual(result["provenance"]["frames"][1]["server_timestamp"], 1_794_484_800)

    def test_boolean_numbers_malformed_inference_and_unsafe_counters_are_rejected(self):
        changes = [lambda x: x.update(tick=True), lambda x: x.update(timestamp=True),
                   lambda x: x.update(tick=2**53), lambda x: x.update(timestamp=-1),
                   lambda x: x.update(quality_verdict={}), lambda x: x.update(quality_verdict=[]),
                   lambda x: x["room_inference"].update(confidence=True),
                   lambda x: x["nodes"][0].update(node_id=True),
                   lambda x: x["classification"].update(presence=False),
                   lambda x: x["nodes"][0]["node_inference"].update(classification="unknown-new-label"),
                   lambda x: x["nodes"][0]["node_inference"].update(age_ms=-1)]
        for change in changes:
            value = frame()
            change(value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.normalize(value)
        for policy in (None, [], {}, True, "auto"):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                normalize_ruview(encode(frame()), source_policy=policy)
        with self.assertRaises(ValueError):
            self.normalize(frame(source="wifi:\ud800"), policy="recorded_any")

    def test_byte_message_observation_node_and_timeline_bounds_do_not_truncate(self):
        with self.assertRaises(ValueError):
            normalize_ruview(b" " * 1_000_001)
        with self.assertRaises(ValueError):
            self.normalize({**frame(), "model_status": "x" * MAX_LINE_BYTES})
        with self.assertRaises(ValueError):
            normalize_ruview(encode(*(edge() for _ in range(MAX_MESSAGES + 1))))
        full = [frame(index + 1, timestamp=1_794_484_800 + index) for index in range(512)]
        self.assertEqual(len(self.normalize(*full)["bundle"]["observations"]), 512)
        with self.assertRaises(ValueError):
            self.normalize(*full, frame(513, timestamp=1_794_485_312))
        value = frame()
        value["nodes"] = [{**copy.deepcopy(value["nodes"][0]), "node_id": index} for index in range(17)]
        with self.assertRaises(ValueError):
            self.normalize(value)
        values = []
        for index in range(17):
            value = frame(index + 1, timestamp=1_794_484_800 + index)
            value["nodes"][0]["node_id"] = index
            values.append(value)
        with self.assertRaises(ValueError):
            self.normalize(*values)
        self.assertEqual(self.normalize(frame(), frame(2, timestamp=1_794_571_200))["bundle"]["duration_ms"], 86_400_000)
        with self.assertRaises(ValueError):
            self.normalize(frame(), frame(2, timestamp=1_794_571_200.001))

    def test_duplicate_node_identity_and_ignored_schema_drift_are_rejected(self):
        value = frame()
        value["nodes"].append(copy.deepcopy(value["nodes"][0]))
        with self.assertRaises(ValueError):
            self.normalize(value)
        ignored = edge()
        ignored["unknown_field"] = True
        with self.assertRaises(ValueError):
            self.normalize(ignored, frame())

    def test_no_network_process_device_model_or_filesystem_io_during_translation(self):
        raw = FIXTURE.read_bytes()
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("No network")), \
             mock.patch.object(socket, "create_connection", side_effect=AssertionError("No network")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("No processes/models")), \
             mock.patch("builtins.open", side_effect=AssertionError("No file/device access")):
            result = normalize_ruview(raw, source_policy="recorded_any")
            evidence = fuse_recording(validate_bundle(_bytes(result["bundle"])), result["provenance"]["source_sha256"])
        self.assertTrue(all(zone["presence"] == "unknown" for zone in evidence["zones"]))
        self.assertEqual(result["provenance"]["model_calls"], 0)
        self.assertEqual(result["provenance"]["network_calls"], 0)


if __name__ == "__main__":
    unittest.main()
