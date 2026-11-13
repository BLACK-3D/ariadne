"""Pure, pinned RuView JSONL translation; never acquires or starts a sensor.

The accepted wire shape is the sensing server's ``main.rs`` at the revision
below. Its timestamp is server creation time, not a radio observation clock.
Consequently every translated observation has unknown physical time and can
only support a historical producer claim, never current room qualification.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
import math

from .perception import (MAX_SOURCE_BYTES, MAX_OBSERVATIONS, MAX_SENSORS,
                         MAX_TIMELINE_MS, _bytes, _fields, _integer, _load,
                         _sha, validate_bundle)

PROFILE = "ruview-sensing-094e140-1"
UPSTREAM_REVISION = "094e1400719ded1283c67d23b9c737f6738a3244"
SOURCE_FORMAT = "ruview_native_jsonl"
SOURCE_CLOCK = "producer_server_timestamp_not_radio_capture"
MAX_MESSAGES = 1024
MAX_LINE_BYTES = 256_000
MAX_NODES = MAX_SENSORS
MAX_RAW_VALUES = 65_536
NODE_STALE_MS = 10_000
# Keep retained counters exactly representable in the JSON owner dashboard.
_MAX_COUNTER = 2**53 - 1
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_CLASSES = frozenset({"active", "present_moving", "present_still", "absent"})
_SYNTHETIC = frozenset({"simulated", "simulate", "synthetic", "test"})
_REQUIRED = {"type", "timestamp", "source", "tick", "nodes", "features",
             "classification", "signal_field"}
_OPTIONAL = {"vital_signs", "calibrated_presence_evidence", "detector_diagnostics",
             "enhanced_motion", "enhanced_breathing", "posture", "signal_quality_score",
             "quality_verdict", "bssid_count", "pose_keypoints", "model_status", "persons",
             "estimated_persons", "node_features", "room_inference", "classifier", "activity",
             # The same pinned revision adds this to GET /sensing/latest only.
             "age_ms"}
_FEATURES = {"mean_rssi", "variance", "motion_band_power", "breathing_band_power",
             "dominant_freq_hz", "change_points", "spectral_power"}
_EDGE_REQUIRED = {"type", "node_id", "presence", "fall_detected", "motion",
                  "breathing_rate_bpm", "heartrate_bpm", "n_persons", "person_count_valid",
                  "motion_energy", "presence_score", "rssi", "calibrated_evidence_authorized",
                  "numeric_vitals_authorized", "abstention_reason"}


def _number(value, label, minimum=None, maximum=None):
    if (type(value) not in (int, float)
            or type(value) is float and not math.isfinite(value)
            or type(value) is int and not -_MAX_COUNTER <= value <= _MAX_COUNTER
            or minimum is not None and value < minimum
            or maximum is not None and value > maximum):
        raise ValueError("Invalid bounded RuView " + label + ".")
    return value


def _string(value, label, maximum=160):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise ValueError("Invalid bounded RuView " + label + ".")
    return value


def _finite_tree(value):
    """Reject exponent overflow even in recognized fields that are discarded."""
    if type(value) is float and not math.isfinite(value):
        raise ValueError("Nonfinite RuView number.")
    if type(value) is dict:
        for child in value.values():
            _finite_tree(child)
    elif type(value) is list:
        for child in value:
            _finite_tree(child)


def _source(value, policy):
    value = _string(value, "source", 160)
    if policy == "esp32_only" and value not in {"esp32", "esp32:offline"}:
        raise ValueError("RuView ESP32-only policy rejects non-ESP32 or simulated sources.")
    if value in _SYNTHETIC:
        return "synthetic", "synthetic", "wifi_csi", False
    if value in {"esp32", "esp32:offline"}:
        return "esp32", "unknown", "wifi_csi", value.endswith(":offline")
    if value == "wifi" or value.startswith("wifi:") and len(value) > 5:
        # The suffix can be a private SSID. It stays in private provenance;
        # aliases, titles, identifiers and errors never derive from it.
        return "wifi", "unknown", "wifi_rssi", value.endswith(":offline")
    for family in ("mediatek", "qualcomm", "realtek_csi"):
        if value in {family, family + ":offline"}:
            return family, "unknown", "wifi_csi", value.endswith(":offline")
        if value == family + ":simulated":
            return family, "synthetic", "wifi_csi", False
    # Raw RTL radar and arbitrary vendor adapters have different semantics.
    raise ValueError("Unsupported RuView source profile.")


def _timestamp(value):
    _number(value, "server timestamp", 0, 253_402_300_799)
    decimal = Decimal(str(value))
    micros = int((decimal * 1_000_000).to_integral_value(rounding=ROUND_FLOOR))
    try:
        stamp = (_EPOCH + timedelta(microseconds=micros)).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise ValueError("RuView server timestamp is outside the supported range.") from None
    return decimal, stamp


def _claim(label, *, room=False):
    if type(label) is not str or label not in _CLASSES | ({"unavailable"} if room else set()):
        raise ValueError("Unsupported RuView classification label.")
    if label == "unavailable":
        return "unknown", "unknown"
    if label == "absent":
        return "absent", "not_detected"
    return "present", "detected" if label in {"active", "present_moving"} else "not_detected"


def _classification(value):
    _fields(value, {"motion_level", "presence", "confidence"}, label="RuView classification")
    _claim(value["motion_level"])
    if type(value["presence"]) is not bool or value["presence"] != (value["motion_level"] != "absent"):
        raise ValueError("Contradictory RuView classification fields.")
    _number(value["confidence"], "classification confidence", 0, 1)


def _edge(value):
    """Recognize the pinned legacy broadcast, but never retain its biometrics."""
    _fields(value, _EDGE_REQUIRED, label="RuView edge_vitals")
    _integer(value["node_id"], "RuView node ID", 255)
    for key in ("presence", "fall_detected", "motion", "person_count_valid",
                "calibrated_evidence_authorized", "numeric_vitals_authorized"):
        if type(value[key]) is not bool:
            raise ValueError("Invalid RuView ignored-message flag.")
    _integer(value["n_persons"], "RuView ignored count", 255)
    _number(value["rssi"], "ignored RSSI", -128, 127)
    _number(value["motion_energy"], "ignored energy", 0)
    _number(value["presence_score"], "ignored score", 0, 1)
    for key in ("breathing_rate_bpm", "heartrate_bpm"):
        if value[key] is not None:
            _number(value[key], "ignored numeric field", 0)
    if value["abstention_reason"] is not None:
        _string(value["abstention_reason"], "ignored reason", 80)


def _node(value):
    _fields(value, {"node_id", "rssi_dbm", "position", "amplitude", "subcarrier_count"},
            {"device_id", "sync", "node_inference", "mediatek_diagnostics"}, "RuView node")
    node_id = _integer(value["node_id"], "RuView node ID", 255)
    _number(value["rssi_dbm"], "RSSI")
    if type(value["position"]) is not list or len(value["position"]) != 3:
        raise ValueError("Invalid RuView ignored node coordinates.")
    for number in value["position"]:
        _number(number, "ignored coordinate")
    if type(value["amplitude"]) is not list or len(value["amplitude"]) > 4096:
        raise ValueError("RuView raw amplitude bound exceeded.")
    for number in value["amplitude"]:
        _number(number, "ignored amplitude")
    _integer(value["subcarrier_count"], "RuView subcarrier count", 4096)
    if value.get("device_id") is not None:
        _string(value["device_id"], "ignored device identity", 80)
    for key in ("sync", "mediatek_diagnostics"):
        if value.get(key) is not None and type(value[key]) is not dict:
            raise ValueError("Invalid RuView ignored node metadata.")
    inference = value.get("node_inference")
    if inference is None:
        return {"node_id": node_id, "classification": None, "confidence": None, "age_ms": None}
    _fields(inference, {"classification", "confidence"}, {"age_ms"}, "RuView node inference")
    _claim(inference["classification"])
    _number(inference["confidence"], "node confidence", 0, 1)
    age = inference.get("age_ms")
    if age is not None:
        _integer(age, "RuView node age", _MAX_COUNTER)
    return {"node_id": node_id, "classification": inference["classification"],
            "confidence": inference["confidence"], "age_ms": age}


def _room(value):
    if value is None:
        return None
    _fields(value, {"classification", "confidence", "contributing_nodes"}, label="RuView room inference")
    _claim(value["classification"], room=True)
    _number(value["confidence"], "room confidence", 0, 1)
    _integer(value["contributing_nodes"], "RuView contributing nodes", MAX_NODES)
    return dict(value)


def _frame(value):
    _fields(value, _REQUIRED, _OPTIONAL, "RuView sensing update")
    _classification(value["classification"])
    _fields(value["features"], _FEATURES, label="RuView features")
    for key, number in value["features"].items():
        if key == "change_points":
            _integer(number, "RuView change points", _MAX_COUNTER)
        else:
            _number(number, "feature")
    _fields(value["signal_field"], {"grid_size", "values"}, label="RuView signal field")
    grid, values = value["signal_field"]["grid_size"], value["signal_field"]["values"]
    if type(grid) is not list or len(grid) != 3:
        raise ValueError("Invalid RuView ignored field geometry.")
    for size in grid:
        _integer(size, "RuView grid size", 4096)
    if type(values) is not list or len(values) > MAX_RAW_VALUES:
        raise ValueError("RuView raw signal-field bound exceeded.")
    for number in values:
        _number(number, "ignored field value")
    nodes = value["nodes"]
    if type(nodes) is not list or len(nodes) > MAX_NODES:
        raise ValueError("At most sixteen RuView nodes are supported.")
    summaries = [_node(node) for node in nodes]
    if len({node["node_id"] for node in summaries}) != len(summaries):
        raise ValueError("Duplicate RuView node identity within a frame.")
    room = _room(value.get("room_inference"))
    age = value.get("age_ms")
    if age is not None:
        _number(age, "server age", 0, _MAX_COUNTER)
    for key in ("signal_quality_score",):
        if value.get(key) is not None:
            _number(value[key], "quality score", 0, 1)
    if value.get("quality_verdict") is not None and (type(value["quality_verdict"]) is not str
            or value["quality_verdict"] not in {"Permit", "Warn", "Deny"}):
        raise ValueError("Unsupported RuView quality verdict.")
    if value.get("classifier") is not None:
        _string(value["classifier"], "classifier", 160)
    if value.get("posture") is not None:
        _string(value["posture"], "ignored posture", 80)
    for key in ("bssid_count", "estimated_persons"):
        if value.get(key) is not None:
            _integer(value[key], "RuView ignored count", _MAX_COUNTER)
    for key in ("vital_signs", "calibrated_presence_evidence", "detector_diagnostics"):
        if value.get(key) is not None and type(value[key]) is not dict:
            raise ValueError("Invalid RuView ignored measurement metadata.")
    for key, limit in (("pose_keypoints", 17), ("persons", MAX_NODES), ("node_features", MAX_NODES)):
        if value.get(key) is not None and (type(value[key]) is not list or len(value[key]) > limit):
            raise ValueError("RuView ignored measurement list exceeds its bound.")
    return summaries, room, age


def _translate_claim(value, nodes, room, source_family, origin, offline, age):
    flags = {"source_clock_unknown", "source_unattested"}
    if origin == "synthetic":
        flags.add("synthetic_source")
    unavailable = False
    if offline:
        flags.add("source_offline")
        unavailable = True
    if age is not None and age >= NODE_STALE_MS:
        flags.add("source_stale")
        unavailable = True
    if room is None:
        flags.add("room_inference_missing")
        unavailable = True
    elif room["classification"] == "unavailable" or room["contributing_nodes"] == 0:
        flags.add("room_unavailable")
        unavailable = True
    fresh = [node for node in nodes if node["classification"] is not None
             and node["age_ms"] is not None and node["age_ms"] < NODE_STALE_MS]
    if not nodes or any(node["classification"] is None or node["age_ms"] is None for node in nodes):
        flags.add("node_evidence_unavailable")
        unavailable = True
    if any(node["age_ms"] is not None and node["age_ms"] >= NODE_STALE_MS for node in nodes):
        flags.add("stale_node")
        unavailable = True
    if room is not None and room["contributing_nodes"] != len(fresh):
        flags.add("contributor_mismatch")
        unavailable = True
    if room is not None and (room["confidence"] == 0 or any(node["confidence"] == 0 for node in fresh)):
        flags.add("score_unavailable")
        unavailable = True
    presence_votes = {_claim(node["classification"])[0] for node in fresh}
    motion_votes = {_claim(node["classification"])[1] for node in fresh}
    if len(presence_votes) > 1:
        flags.add("node_disagreement")
        unavailable = True
    if room is not None:
        room_presence, room_motion = _claim(room["classification"], room=True)
        if presence_votes and room_presence not in presence_votes:
            flags.add("room_node_conflict")
            unavailable = True
        classification = value["classification"]
        if (room["classification"] != "unavailable"
                and classification["motion_level"] != room["classification"]):
            flags.add("classification_conflict")
            unavailable = True
    else:
        room_presence = room_motion = "unknown"
    if value.get("quality_verdict") in {"Warn", "Deny"}:
        flags.add("quality_warning" if value["quality_verdict"] == "Warn" else "quality_denied")
        unavailable = True
    if value.get("classifier") is not None or source_family in {"mediatek", "qualcomm", "realtek_csi"}:
        flags.add("experimental_detector")
        unavailable = True
    if len(motion_votes) > 1 or motion_votes and room_motion not in motion_votes:
        flags.add("motion_disagreement")
        room_motion = "unknown"
    if len(flags) > 16:
        raise ValueError("RuView quality evidence exceeds its declared bound.")
    return ("unknown", "unknown", None, sorted(flags)) if unavailable else (
        room_presence, room_motion, room["confidence"], sorted(flags))


def normalize_ruview(raw: bytes, *, source_policy="esp32_only"):
    """Translate one bounded native recording, preserving unverified clocks.

    ``esp32_only`` rejects simulator/auto/RSSI/vendor fallback anywhere in the
    file. ``recorded_any`` is an explicit recording-only choice for the known
    source families above; it does not authorize an upstream connection.
    """
    if type(source_policy) is not str or source_policy not in {"esp32_only", "recorded_any"}:
        raise ValueError("Choose an explicit supported RuView source policy.")
    if type(raw) is not bytes or not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("RuView recording exceeds its one-million-byte limit or is empty.")
    lines = raw.splitlines()
    if not 1 <= len(lines) <= MAX_MESSAGES:
        raise ValueError("RuView recording exceeds its message-count bound.")
    source_hash = _sha(raw)
    observations, frames, sensors = [], [], {}
    node_ids, origins = set(), set()
    ignored = {"edge_vitals": 0}
    first_timestamp = previous_timestamp = previous_tick = None
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise ValueError("Blank RuView messages are not a supported recording shape.")
        if len(line) > MAX_LINE_BYTES:
            raise ValueError("RuView message exceeds its line-byte bound.")
        value = _load(line)
        _finite_tree(value)
        if type(value) is not dict or type(value.get("type")) is not str:
            raise ValueError("RuView messages need a supported explicit type.")
        if value["type"] == "edge_vitals":
            _edge(value)
            node_ids.add(value["node_id"])
            if len(node_ids) > MAX_NODES:
                raise ValueError("A RuView recording may reference at most sixteen nodes.")
            ignored["edge_vitals"] += 1
            continue
        if value["type"] != "sensing_update":
            raise ValueError("Unsupported RuView recording message kind.")
        if len(observations) >= MAX_OBSERVATIONS:
            raise ValueError("A RuView recording needs at most 512 sensing observations.")
        family, origin, modality, offline = _source(value.get("source"), source_policy)
        nodes, room, age = _frame(value)
        node_ids.update(node["node_id"] for node in nodes)
        if len(node_ids) > MAX_NODES:
            raise ValueError("A RuView recording may reference at most sixteen nodes.")
        timestamp, producer_at = _timestamp(value["timestamp"])
        tick = _integer(value["tick"], "RuView server tick", _MAX_COUNTER)
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ValueError("RuView server timestamp regression requires separate owner review.")
        if previous_tick is not None and tick <= previous_tick:
            raise ValueError("RuView server counter regressed or repeated; boot identity is unknown.")
        if first_timestamp is None:
            first_timestamp = timestamp
            started_at = producer_at
        offset = int(((timestamp - first_timestamp) * 1000).to_integral_value(rounding=ROUND_FLOOR))
        if offset > MAX_TIMELINE_MS:
            raise ValueError("RuView recording exceeds the twenty-four-hour timeline bound.")
        previous_timestamp, previous_tick = timestamp, tick
        alias = "ruview-" + family.replace("_", "-")
        if alias not in sensors:
            sensors[alias] = {"registered_sensor_alias": alias, "modality": modality,
                              "producer_revision": PROFILE, "zone_id": "ruview-room"}
        if len(sensors) > MAX_SENSORS:
            raise ValueError("RuView recording exceeds the sensor-family bound.")
        origins.add(origin)
        presence, motion, score, flags = _translate_claim(value, nodes, room, family, origin, offline, age)
        ordinal = len(observations)
        event_id = "ruview-" + source_hash[:16] + "-" + str(ordinal)
        upstream_state = "disconnected" if offline else "stale" if (
            "source_stale" in flags or "stale_node" in flags) else "unavailable" if presence == "unknown" else "available"
        observations.append({
            "event_id": event_id, "registered_sensor_alias": alias,
            "producer_boot_id": "ruview-boot-unknown", "capture_origin": origin,
            "observed_at": None, "producer_at": producer_at, "sequence": None,
            "source_event_id": "server-tick-" + str(tick), "replay_offset_ms": offset,
            "presence": presence, "motion": motion, "upstream_source_state": upstream_state,
            "processing_mode": "replay", "clock_kind": "unknown", "objects": [],
            "quality_flags": flags, "score": score,
            "score_kind": "unavailable" if score is None else "uncalibrated_model_score",
            "local_qualification_status": "recorded_unverified",
        })
        frames.append({
            "ordinal": ordinal, "event_id": event_id, "source_line": line_number,
            "raw_line_sha256": _sha(line), "source": value["source"], "source_family": family,
            "capture_origin": origin, "server_timestamp": value["timestamp"],
            "producer_at": producer_at, "tick": tick, "age_ms": age,
            "room": room, "nodes": nodes, "presence": presence, "motion": motion,
            "quality_flags": flags, "source_clock": SOURCE_CLOCK,
        })
    if not observations:
        raise ValueError("RuView recording contains no supported sensing observations.")
    title = "RuView synthetic recording (not RF capture)" if origins == {"synthetic"} else (
        "RuView mixed-origin recording (unverified)" if "synthetic" in origins else
        "RuView RF recording (unverified source claims)")
    bundle = {"schema_version": 1, "recording_id": "ruview-" + source_hash[:32], "title": title,
              "started_at": started_at, "duration_ms": observations[-1]["replay_offset_ms"],
              "zones": ["ruview-room"], "sensors": list(sensors.values()), "observations": observations}
    # Check composition with the existing stable recording boundary, but return
    # the raw v1 bundle: core deliberately validates that same shape again.
    validate_bundle(_bytes(bundle))
    bundle_hash = _sha(_bytes(bundle))
    provenance = {
        "schema_version": 1, "profile": PROFILE, "upstream_revision": UPSTREAM_REVISION,
        "upstream_revision_basis": "supported_protocol_pin_not_attested_runtime",
        "source_policy": source_policy, "source_format": SOURCE_FORMAT,
        "source_sha256": source_hash, "normalized_sha256": bundle_hash, "bundle_sha256": bundle_hash,
        "processing_mode": "replay", "source_clock": SOURCE_CLOCK,
        "physical_clock_available": False, "hardware_qualified": False,
        "qualification": "recorded_unverified", "capture_origins": sorted(origins),
        "ignored_message_counts": ignored, "frames": frames,
        "source_message_count": len(lines), "sensing_observation_count": len(observations),
        "source_node_count": len(node_ids), "model_calls": 0, "network_calls": 0,
        "action_authority": False,
    }
    return {"bundle": bundle, "provenance": provenance}
