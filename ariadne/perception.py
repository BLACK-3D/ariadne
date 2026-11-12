"""Bounded recording-only perception; no acquisition, services, models or actions.

Input is owner-supplied evidence, not authenticated sensing. Source clocks and
capture origin survive replay. The deterministic timeline is a recording-time
calculation; read-time physical age is reported separately and never refreshed
by replay. Accepted attempts and completed artifacts have exclusive names.
An incomplete attempt is a persistent hold, never a retry instruction.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time

from .atomic import publish_bytes
from .config import _allowed_path

MAX_SOURCE_BYTES = 1_000_000
MAX_ARTIFACT_BYTES = 4_000_000
MAX_TOTAL_BYTES = 32_000_000
MAX_LEDGER_BYTES = 8_000_000
MAX_ATTEMPTS = 100
MAX_OBSERVATIONS = 512
MAX_SENSORS = 16
MAX_ZONES = 16
MAX_TIMELINE_MS = 86_400_000
FRESHNESS_MS = 10_000
IMPLEMENTATION = "recording-perception-1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
UTC = timezone.utc


class PerceptionConflict(ValueError):
    """A denied revision, held attempt or reused request identifier."""


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identifier(value, label="identifier"):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError(label + " must be 1–64 ASCII letters, digits, underscores or hyphens.")
    return value


def _text(value, label, maximum=160):
    if type(value) is not str or not 1 <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise ValueError(label + " is invalid or exceeds its bound.")
    return value


def _integer(value, label, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(label + " must be a bounded nonnegative integer.")
    return value


def _date(value, label):
    if type(value) is not str or not _TIME.fullmatch(value):
        raise ValueError(label + " must be an unambiguous UTC timestamp ending in Z.")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ValueError(label + " is not a valid UTC timestamp.") from None
    return result


def _stamp(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _load(raw, cap=MAX_SOURCE_BYTES):
    """Bound bytes and structural depth before invoking the JSON decoder."""
    if type(raw) is not bytes or len(raw) > cap:
        raise ValueError("Perception JSON exceeds its byte limit.")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("Perception JSON must be UTF-8.") from None
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 12:
                raise ValueError("Perception JSON is nested too deeply.")
        elif char in "]}":
            depth -= 1
    def pairs(rows):
        output = {}
        for key, value in rows:
            if key in output:
                raise ValueError("Duplicate perception JSON key.")
            output[key] = value
        return output
    try:
        return json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite perception number.")))
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("Invalid perception JSON.") from None


def _fields(value, required, optional=(), label="record"):
    if type(value) is not dict or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError(label + " has missing or unsupported fields.")


def validate_bundle(raw):
    """Validate a finite recording contract without reading any external resource."""
    bundle = _load(raw)
    _fields(bundle, {"schema_version", "recording_id", "title", "started_at", "duration_ms", "sensors", "zones", "observations"}, label="bundle")
    if type(bundle["schema_version"]) is not int or bundle["schema_version"] != 1:
        raise ValueError("Unsupported perception schema version.")
    _identifier(bundle["recording_id"], "recording_id")
    _text(bundle["title"], "title")
    start = _date(bundle["started_at"], "started_at")
    duration = _integer(bundle["duration_ms"], "duration_ms", MAX_TIMELINE_MS)
    try:
        start + timedelta(milliseconds=duration)
    except OverflowError:
        raise ValueError("Recording timeline exceeds the timestamp range.") from None
    zones = bundle["zones"]
    if type(zones) is not list or not 1 <= len(zones) <= MAX_ZONES:
        raise ValueError("Choose 1–16 recording zones.")
    for zone in zones:
        _identifier(zone, "zone_id")
    if len(set(zones)) != len(zones):
        raise ValueError("Duplicate recording zone.")
    sensors = bundle["sensors"]
    if type(sensors) is not list or not 1 <= len(sensors) <= MAX_SENSORS:
        raise ValueError("Choose 1–16 registered recording sensors.")
    aliases = {}
    for sensor in sensors:
        _fields(sensor, {"registered_sensor_alias", "modality", "producer_revision", "zone_id"}, {"calibration_revision"}, "sensor")
        alias = _identifier(sensor["registered_sensor_alias"], "registered_sensor_alias")
        if alias in aliases:
            raise ValueError("Duplicate registered sensor alias.")
        if sensor["modality"] not in ("wifi_csi", "wifi_rssi", "camera", "mmwave"):
            raise ValueError("Unsupported sensor modality.")
        _text(sensor["producer_revision"], "producer_revision", 80)
        if sensor["zone_id"] not in zones:
            raise ValueError("Sensor must select a declared zone.")
        if sensor.get("calibration_revision") is not None:
            _identifier(sensor["calibration_revision"], "calibration_revision")
        aliases[alias] = sensor
    observations = bundle["observations"]
    if type(observations) is not list or not 1 <= len(observations) <= MAX_OBSERVATIONS:
        raise ValueError("A recording needs 1–512 observations.")
    required = {"event_id", "registered_sensor_alias", "producer_boot_id", "capture_origin", "observed_at", "sequence", "replay_offset_ms", "presence", "motion", "upstream_source_state"}
    optional = {"schema_version", "source_event_id", "processing_mode", "producer_at", "received_at", "clock_kind", "clock_epoch", "objects", "quality_flags", "score", "score_kind", "calibration_revision", "coordinate_frame", "units", "model_hash", "firmware_hash", "local_qualification_status", "supporting_observation_ids"}
    seen, source_seen, sequences, clocks = set(), set(), {}, {}
    offset = -1
    normalized = []
    for index, row in enumerate(observations):
        _fields(row, required, optional, "observation")
        event = _identifier(row["event_id"], "event_id")
        if event in seen:
            raise ValueError("Duplicate observation event_id.")
        seen.add(event)
        alias = row["registered_sensor_alias"]
        if type(alias) is not str or alias not in aliases:
            raise ValueError("Observation has no registered recording sensor.")
        sensor = aliases[alias]
        boot = _identifier(row["producer_boot_id"], "producer_boot_id")
        source_id = row.get("source_event_id")
        if source_id is not None:
            _identifier(source_id, "source_event_id")
            if (alias, boot, source_id) in source_seen:
                raise ValueError("Duplicate producer source event.")
            source_seen.add((alias, boot, source_id))
        if row["capture_origin"] not in ("live", "synthetic", "unknown"):
            raise ValueError("Invalid capture origin.")
        if row.get("processing_mode", "replay") not in ("live", "replay", "simulation"):
            raise ValueError("Invalid declared processing mode.")
        if "schema_version" in row and (type(row["schema_version"]) is not int or row["schema_version"] != 1):
            raise ValueError("Unsupported observation schema.")
        if row["presence"] not in ("present", "absent", "unknown") or row["motion"] not in ("detected", "not_detected", "unknown"):
            raise ValueError("Invalid presence or motion evidence.")
        if row["upstream_source_state"] not in ("available", "unavailable", "disconnected", "stale", "waiting_for_frames", "invalid"):
            raise ValueError("Invalid upstream source state.")
        current_offset = _integer(row["replay_offset_ms"], "replay_offset_ms", duration)
        if current_offset < offset:
            raise ValueError("Recording offsets must be ordered.")
        offset = current_offset
        sequence = row["sequence"]
        if sequence is not None:
            _integer(sequence, "sequence", 2**53 - 1)
            if (alias, boot) in sequences and sequence <= sequences[(alias, boot)]:
                raise ValueError("Producer sequence must increase within a boot.")
            sequences[(alias, boot)] = sequence
        observed = None if row["observed_at"] is None else _date(row["observed_at"], "observed_at")
        for key in ("producer_at", "received_at"):
            if row.get(key) is not None:
                _date(row[key], key)
        if row.get("clock_kind", "producer_unverified") not in ("hardware_unverified", "producer_unverified", "unknown"):
            raise ValueError("Invalid clock semantics.")
        if row.get("clock_epoch") is not None:
            _text(row["clock_epoch"], "clock_epoch", 80)
        flags = row.get("quality_flags", [])
        if type(flags) is not list or len(flags) > 16 or any(type(flag) is not str or not _ID.fullmatch(flag) for flag in flags) or len(set(flags)) != len(flags):
            raise ValueError("Invalid quality flags.")
        flags = list(flags)
        if alias in clocks:
            previous_boot, previous_epoch, previous_time = clocks[alias]
            if boot != previous_boot or row.get("clock_epoch") != previous_epoch:
                flags.append("clock_reset")
            elif observed is not None and previous_time is not None and observed < previous_time:
                flags.append("clock_regression")
        clocks[alias] = (boot, row.get("clock_epoch"), observed)
        if row.get("clock_kind") == "unknown":
            flags.append("source_clock_unknown")
        if observed is not None and (observed - (start + timedelta(milliseconds=current_offset))).total_seconds() > 2:
            flags.append("future_timestamp")
        calibration = row.get("calibration_revision", sensor.get("calibration_revision"))
        if calibration is not None:
            _identifier(calibration, "calibration_revision")
        if calibration != sensor.get("calibration_revision"):
            flags.append("calibration_mismatch")
        if row.get("coordinate_frame") is not None or row.get("units") is not None:
            raise ValueError("Recording v1 does not accept spatial coordinates or metric units.")
        score = row.get("score")
        if score is not None and (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError("Score must be null or finite from zero to one.")
        score_kind = row.get("score_kind", "unavailable")
        if score_kind not in ("unavailable", "uncalibrated_model_score") or (score is None) != (score_kind == "unavailable"):
            raise ValueError("A model score is uncalibrated, never a probability.")
        if row.get("local_qualification_status", "unqualified") not in ("unqualified", "recorded_unverified"):
            raise ValueError("Imported observations cannot claim local live qualification.")
        for key in ("model_hash", "firmware_hash"):
            if row.get(key) is not None and (type(row[key]) is not str or not _HASH.fullmatch(row[key])):
                raise ValueError("Invalid declared artifact hash.")
        supporting = row.get("supporting_observation_ids", [])
        if type(supporting) is not list or len(supporting) > 16 or any(type(value) is not str for value in supporting) or len(set(supporting)) != len(supporting):
            raise ValueError("Invalid supporting observation IDs.")
        for value in supporting:
            _identifier(value, "supporting_observation_id")
        objects = row.get("objects", [])
        if type(objects) is not list or len(objects) > 16:
            raise ValueError("At most sixteen visible object labels are allowed.")
        for obj in objects:
            _fields(obj, {"label"}, {"count"}, "object")
            _text(obj["label"], "object label", 64)
            if "count" in obj:
                _integer(obj["count"], "object count", 100)
        normalized.append({"schema_version": 1, "ordinal": index, "event_id": event,
            "registered_sensor_alias": alias, "producer_revision": sensor["producer_revision"],
            "producer_boot_id": boot, "source_event_id": source_id, "modality": sensor["modality"],
            "capture_origin": row["capture_origin"], "processing_mode": "replay",
            "upstream_source_state": row["upstream_source_state"], "local_qualification_status": "recorded_unverified",
            "observed_at": row["observed_at"], "producer_at": row.get("producer_at"),
            "source_received_at": row.get("received_at"), "received_at": None,
            "sequence": sequence, "clock_kind": row.get("clock_kind", "producer_unverified"),
            "clock_epoch": row.get("clock_epoch"), "replay_offset_ms": current_offset,
            "zone_id": sensor["zone_id"], "calibration_revision": calibration,
            "coordinate_frame": None, "units": None, "presence": row["presence"], "motion": row["motion"],
            "objects": objects, "quality_flags": sorted(set(flags)), "score": score, "score_kind": score_kind,
            "model_hash": row.get("model_hash"), "firmware_hash": row.get("firmware_hash"),
            "supporting_observation_ids": supporting})
    for row in normalized:
        if any(value not in seen or value == row["event_id"] for value in row["supporting_observation_ids"]):
            raise ValueError("Supporting IDs must identify other observations in the recording.")
    return {**bundle, "observations": normalized}


def fuse_recording(bundle, source_sha256, *, processing_mode="replay"):
    """Deterministic temporal logic. Freshness references the original timeline."""
    if processing_mode not in ("replay", "simulation"):
        raise ValueError("Recording processing can only be replay or simulation.")
    observations = [{**row, "processing_mode": processing_mode, "source_artifact_hash": source_sha256,
                     "derived_artifact_hash": None} for row in bundle["observations"]]
    start = _date(bundle["started_at"], "started_at")
    latest, streaks, timeline = {}, {}, []
    sensors = {row["registered_sensor_alias"]: row for row in bundle["sensors"]}

    def snapshot(offset):
        reference = start + timedelta(milliseconds=offset)
        zones = []
        for zone in bundle["zones"]:
            supports, valid, reasons, ages = [], [], set(), []
            for alias, sensor in sensors.items():
                if sensor["zone_id"] != zone:
                    continue
                row = latest.get(alias)
                if row is None:
                    reasons.add("source_missing")
                    continue
                supports.append(row["event_id"])
                observed = None if row["observed_at"] is None else _date(row["observed_at"], "observed_at")
                age = None if observed is None else max(0.0, (reference - observed).total_seconds())
                if age is not None:
                    ages.append(age)
                row_reasons = set(row["quality_flags"])
                if row["upstream_source_state"] != "available":
                    row_reasons.add("source_" + row["upstream_source_state"])
                if age is None:
                    row_reasons.add("source_age_unknown")
                elif age * 1000 > FRESHNESS_MS or offset - row["replay_offset_ms"] > FRESHNESS_MS:
                    row_reasons.add("source_stale")
                if row["presence"] == "unknown":
                    row_reasons.add("presence_unknown")
                if streaks.get(alias, (None, 0))[1] < 2:
                    row_reasons.add("debouncing")
                # Unrecognized producer quality flags also prevent confident fusion.
                if row_reasons:
                    reasons.update(row_reasons)
                else:
                    valid.append(row)
            presences = {row["presence"] for row in valid}
            motions = {row["motion"] for row in valid if row["motion"] != "unknown"}
            disagreement = len(presences) > 1 or len(motions) > 1
            if disagreement:
                reasons.add("source_disagreement")
            # Missing/unqualified evidence remains visible and prevents absence.
            presence = next(iter(presences)) if len(presences) == 1 and not reasons else "unknown"
            motion = next(iter(motions)) if len(motions) == 1 and not reasons else "unknown"
            state = "disagreement" if disagreement else "stale" if "source_stale" in reasons else "unknown" if reasons or not valid else "known"
            zones.append({"zone_id": zone, "presence": presence, "motion": motion, "state": state,
                "reasons": sorted(reasons), "supporting_observation_ids": sorted(supports),
                "source_age_seconds": max(ages) if len(ages) == len(supports) and supports else None,
                "physical_freshness": "stale" if "source_stale" in reasons else "unknown" if any(reason in reasons for reason in ("source_age_unknown", "source_missing", "future_timestamp", "clock_regression", "clock_reset")) else "fresh",
                "freshness_reference": "original_recording_timeline"})
        return zones

    groups = {}
    for row in observations:
        groups.setdefault(row["replay_offset_ms"], []).append(row)
    groups.setdefault(bundle["duration_ms"], [])
    for offset, rows in sorted(groups.items()):
        for row in rows:
            alias = row["registered_sensor_alias"]
            previous = latest.get(alias)
            signature = (row["producer_boot_id"], row["presence"], row["motion"], row["upstream_source_state"])
            old_signature, count = streaks.get(alias, (None, 0))
            contiguous = previous is not None and row["replay_offset_ms"] - previous["replay_offset_ms"] <= FRESHNESS_MS
            observed = None if row["observed_at"] is None else _date(row["observed_at"], "observed_at")
            event_reference = start + timedelta(milliseconds=offset)
            usable = (row["upstream_source_state"] == "available" and not row["quality_flags"]
                      and row["presence"] != "unknown" and observed is not None
                      and -2 <= (event_reference - observed).total_seconds() <= FRESHNESS_MS / 1000)
            streaks[alias] = (signature, (count + 1 if signature == old_signature and contiguous else 1) if usable else 0)
            latest[alias] = row
        zones = snapshot(offset)
        timeline.extend({"replay_offset_ms": offset, **zone} for zone in zones)
    return {"schema_version": 1, "implementation": IMPLEMENTATION, "recording_id": bundle["recording_id"],
        "title": bundle["title"], "started_at": bundle["started_at"], "duration_ms": bundle["duration_ms"],
        "processing_mode": processing_mode, "capture_origins": sorted({row["capture_origin"] for row in observations}),
        "source_sha256": source_sha256, "freshness_reference": "original_recording_timeline_only",
        "live_available": False, "local_qualification_status": "recorded_unverified", "zones": zones,
        "timeline": timeline, "observations": observations,
        "authority": "Read-only evidence; observations grant no action authority."}


class Perception:
    """Finite owner-selected JSON imports with fail-stop execution receipts."""

    def __init__(self, registry, store):
        self.registry, self.store = registry, store
        self.root = _allowed_path(registry.settings.data_dir / "perception")
        self.path = self.root / "ledger.sqlite3"
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.path), threading.RLock())

    def catalog(self):
        return {"schema_version": 1, "implementation": IMPLEMENTATION,
            "operations": [{"id": "import", "name": "Import selected recording JSON"},
                           {"id": "replay", "name": "Replay a verified recording"},
                           {"id": "synthetic_demo", "name": "Create explicit synthetic demonstration"}],
            "scope": "Owner-selected recording JSON and explicit synthetic fixtures only; no live acquisition, devices, network, model or action authority.",
            "limits": self._limits(), "model_calls": 0,
            "example_path": "ariadne/resources/perception/example-recording.json",
            "schema_path": "ariadne/resources/perception/recording-contract.json",
            "recovery": "An accepted failed or incomplete attempt holds further writes. No automatic retries or reset in v1."}

    @staticmethod
    def _limits():
        return {"source_bytes": MAX_SOURCE_BYTES, "artifact_bytes": MAX_ARTIFACT_BYTES,
            "ledger_bytes": MAX_LEDGER_BYTES,
            "total_bytes": MAX_TOTAL_BYTES, "attempts": MAX_ATTEMPTS,
            "observations": MAX_OBSERVATIONS, "sensors": MAX_SENSORS, "zones": MAX_ZONES,
            "timeline_ms": MAX_TIMELINE_MS, "freshness_ms": FRESHNESS_MS, "debounce_observations": 2}

    @contextmanager
    def _database(self, *, create=False):
        # Reading an unused pack creates no directory, database or recovery writes.
        _allowed_path(self.path)
        if not create and not self.path.exists():
            self.registry._check_ancestors(self.root)
            yield None
            return
        with self.lock, self.registry._directory(self.root, create=create):
            for suffix in ("", "-journal", "-wal", "-shm"):
                path = _allowed_path(str(self.path) + suffix)
                if path.exists():
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_LEDGER_BYTES:
                        raise ValueError("Perception ledger must be a regular private file.")
            if create:
                db = sqlite3.connect(self.path, timeout=5)
            else:
                db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
            db.row_factory = sqlite3.Row
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            try:
                db.execute("PRAGMA trusted_schema=OFF")
                if create:
                    db.executescript("""
                        CREATE TABLE IF NOT EXISTS perception_meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL);
                        INSERT OR IGNORE INTO perception_meta VALUES(1,0);
                        CREATE TABLE IF NOT EXISTS perception_attempts(
                            request_id TEXT PRIMARY KEY,operation TEXT NOT NULL,digest TEXT NOT NULL,
                            status TEXT NOT NULL,created_at TEXT NOT NULL,receipt_sha256 TEXT,
                            receipt TEXT NOT NULL,error TEXT);
                    """)
                yield db
                if create:
                    db.commit()
            except BaseException:
                if create:
                    db.rollback()
                raise
            finally:
                db.close()

    def _rows(self):
        with self._database() as db:
            if db is None:
                return 0, []
            revision = db.execute("SELECT revision FROM perception_meta WHERE id=1").fetchone()[0]
            rows = list(db.execute("SELECT * FROM perception_attempts ORDER BY created_at DESC,request_id LIMIT ?", (MAX_ATTEMPTS,)))
        return revision, rows

    def _read(self, path, cap):
        _allowed_path(path)
        with self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path, directory), "rb") as source:
                before = os.fstat(source.fileno())
                if before.st_size > cap:
                    raise ValueError("Perception artifact exceeds its bound.")
                raw = source.read(cap + 1)
                after = os.fstat(source.fileno())
                if len(raw) > cap or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError("Perception artifact changed while reading.")
        return raw

    def _publish(self, path, raw, *, checkpoint=None):
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("Perception output exceeds its bound.")
        _allowed_path(path)
        with self.registry._directory(path.parent, create=True):
            publish_bytes(path, raw, checkpoint=checkpoint)

    def _verified(self, row):
        receipt_raw = self._read(self.root / row["request_id"] / "receipt.json", MAX_ARTIFACT_BYTES)
        if _sha(receipt_raw) != row["receipt_sha256"] or receipt_raw != row["receipt"].encode("utf-8"):
            raise ValueError("Perception receipt integrity verification failed.")
        receipt = _load(receipt_raw, MAX_ARTIFACT_BYTES)
        if receipt["request_id"] != row["request_id"] or receipt["status"] != "completed":
            raise ValueError("Perception receipt identity verification failed.")
        files = {}
        for artifact in receipt["artifacts"]:
            name = artifact["name"]
            if name not in {"attempt.json", "source.json", "normalized.json", "evidence.json"} or name in files:
                raise ValueError("Unexpected perception artifact.")
            raw = self._read(self.root / row["request_id"] / name, MAX_ARTIFACT_BYTES)
            if len(raw) != artifact["bytes"] or _sha(raw) != artifact["sha256"]:
                raise ValueError("Perception artifact integrity verification failed: " + name)
            files[name] = raw
        if set(files) != {"attempt.json", "source.json", "normalized.json", "evidence.json"}:
            raise ValueError("Missing retained perception artifact.")
        if _sha(files["source.json"]) != receipt["source_sha256"] or _sha(files["evidence.json"]) != receipt["canonical_sha256"]:
            raise ValueError("Perception source or canonical evidence hash mismatch.")
        evidence = _load(files["evidence.json"], MAX_ARTIFACT_BYTES)
        return {**receipt, "verified": True}, evidence, files

    def _summary(self, row):
        summary = _load(row["receipt"].encode("utf-8"), MAX_ARTIFACT_BYTES)
        summary = {key: value for key, value in summary.items() if key != "artifacts"}
        summary.update(status=row["status"], verified=False)
        if row["status"] == "completed":
            try:
                receipt, _, _ = self._verified(row)
                summary.update(verified=True)
            except (ValueError, OSError, KeyError) as error:
                summary.update(status="invalid", error=str(error)[:300])
        elif row["status"] == "accepted":
            summary.update(status="interrupted", error="Incomplete accepted attempt; held for owner review.")
        else:
            summary["error"] = row["error"] or "Attempt failed; held for owner review."
        return summary

    def status(self):
        revision, rows = self._rows()
        records = [self._summary(row) for row in rows]
        held = next((row for row in records if row["status"] != "completed"), None)
        return {"revision": revision, "paused": bool(self.store.setting("paused", False)),
            "hold": held is not None, "hold_reason": None if held is None else held.get("error", "Integrity failure."),
            "mode": "recording_only", "live_available": False, "recordings": records,
            "limits": self._limits(), "model_calls": 0,
            "note": "Saved recording evidence only. Replay cannot refresh physical evidence or grant action authority."}

    def history(self):
        _, rows = self._rows()
        return {"runs": [self._summary(row) for row in rows]}

    def list_recordings(self):
        return {"recordings": self.status()["recordings"]}

    def _row(self, request_id):
        _identifier(request_id, "request_id")
        _, rows = self._rows()
        row = next((row for row in rows if row["request_id"] == request_id), None)
        if row is None:
            raise ValueError("No saved perception attempt has that request_id.")
        return row

    def detail(self, request_id):
        row = self._row(request_id)
        if row["status"] != "completed":
            return {"receipt": self._summary(row), "evidence": None, "current_zones": []}
        receipt, evidence, _ = self._verified(row)
        # Dynamic age is deliberately excluded from the canonical recording hash.
        now = datetime.now(UTC)
        latest = {}
        for observation in evidence["observations"]:
            latest[observation["registered_sensor_alias"]] = observation
        current = []
        for zone in evidence["zones"]:
            sources = [obs for obs in latest.values() if obs["zone_id"] == zone["zone_id"]]
            ages = [None if obs["observed_at"] is None else (now - _date(obs["observed_at"], "observed_at")).total_seconds() for obs in sources]
            known_age = bool(ages) and all(age is not None and age >= -2 for age in ages)
            age = max(max(0, age) for age in ages) if known_age else None
            freshness = "unknown" if age is None else "stale" if age * 1000 > FRESHNESS_MS else "fresh"
            current.append({"zone_id": zone["zone_id"], "source_age_seconds": age,
                "physical_freshness": freshness, "presence": "unknown", "motion": "unknown",
                "state": "stale" if freshness == "stale" else "unknown",
                "reasons": ["saved_recording_is_not_live"], "processing_mode": evidence["processing_mode"]})
        return {"receipt": receipt, "evidence": evidence, "current_zones": current}

    def _controls(self):
        if self.store.setting("paused", False):
            raise PerceptionConflict("Perception writes are paused by the global owner control.")
        external = self.store.setting("perception_revision", 0)
        if type(external) is not int or external < 0:
            raise PerceptionConflict("Invalid perception control revision.")
        return external

    @contextmanager
    def _owner_guard(self, expected):
        # Serialize dispatch/publication with the owner's pause transaction.
        # The main Store advances perception_revision on every pause write.
        # A reserved lock permits our read-only controls checks while preventing
        # another connection from changing the fence until the ledger commits.
        with self.store.connect(require_existing=True) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = {row["key"]: json.loads(row["value"]) for row in db.execute(
                "SELECT key,value FROM settings WHERE key IN ('paused','perception_revision')")}
            if rows.get("paused", False) or type(rows.get("perception_revision", 0)) is not int or rows.get("perception_revision", 0) != expected:
                raise PerceptionConflict("Perception owner controls changed before dispatch or publication.")
            yield

    def _check_revision(self, expected, actual):
        if expected is not None and (type(expected) is not int or expected != actual):
            raise PerceptionConflict("Perception revision changed; review the current saved state.")

    def import_bundle(self, path, request_id, *, expected_revision=None):
        _identifier(request_id, "request_id")
        source = self.registry._path(path)
        if source.suffix.lower() != ".json":
            raise ValueError("Choose a workspace JSON recording bundle.")
        raw = self._read(source, MAX_SOURCE_BYTES)
        bundle = validate_bundle(raw)  # Rejections before acceptance do not hold the pack.
        return self._run("import", request_id, raw, bundle, "replay", expected_revision,
                         {"selected_workspace_path": source.relative_to(self.registry.root).as_posix()})

    def replay(self, import_id, request_id, *, expected_revision=None):
        _identifier(import_id, "recording_id")
        _identifier(request_id, "request_id")
        with self.lock:
            row = self._row(import_id)
            if row["status"] != "completed":
                raise PerceptionConflict("Replay requires a completed verified recording.")
            _, _, files = self._verified(row)
            raw = files["source.json"]
            bundle = validate_bundle(raw)
            return self._run("replay", request_id, raw, bundle, "replay", expected_revision,
                             {"parent_request_id": import_id})

    def synthetic_demo(self, request_id, *, expected_revision=None):
        _identifier(request_id, "request_id")
        fixture = Path(__file__).parent / "resources" / "perception" / "synthetic-demo.json"
        raw = self._read(fixture, MAX_SOURCE_BYTES)
        bundle = validate_bundle(raw)
        if any(row["capture_origin"] != "synthetic" for row in bundle["observations"]):
            raise ValueError("Synthetic fixture must retain synthetic capture origin.")
        return self._run("synthetic_demo", request_id, raw, bundle, "simulation", expected_revision,
                         {"fixture_id": "synthetic-zone-demo-v1"})

    def _checkpoint(self, phase):
        """Test seam for revocation and interrupted publication; never an adapter."""

    def _run(self, operation, request_id, raw, bundle, mode, expected_revision, provenance):
        source_sha = _sha(raw)
        identity = {"operation": operation, "source_sha256": source_sha, "processing_mode": mode, **provenance}
        digest = _sha(_bytes(identity))
        with self.lock:
            external = self._controls()
            revision, rows = self._rows()
            self._check_revision(expected_revision, revision)
            existing = next((row for row in rows if row["request_id"] == request_id), None)
            if existing is not None:
                if existing["digest"] != digest:
                    raise PerceptionConflict("A request_id cannot be reused for different perception inputs.")
                if existing["status"] != "completed":
                    raise PerceptionConflict("Failed or incomplete perception attempts cannot run again.")
                return self._verified(existing)[0]
            for row in rows:
                if row["status"] != "completed":
                    raise PerceptionConflict("Perception is held after an accepted failed or incomplete attempt.")
                self._verified(row)
            if len(rows) >= MAX_ATTEMPTS:
                raise ValueError("Perception attempt capacity reached.")
            total = sum(len(row["receipt"].encode("utf-8")) + sum(artifact["bytes"] for artifact in _load(row["receipt"].encode("utf-8"), MAX_ARTIFACT_BYTES).get("artifacts", [])) for row in rows)
            # Upper-bound outputs before reservation, including dense zone timelines.
            evidence = fuse_recording(bundle, source_sha, processing_mode=mode)
            normalized = {key: value for key, value in bundle.items() if key != "observations"}
            normalized["observations"] = evidence["observations"]
            artifacts = {"source.json": raw, "normalized.json": _bytes(normalized), "evidence.json": _bytes(evidence)}
            if any(len(value) > MAX_ARTIFACT_BYTES for value in artifacts.values()) or total + sum(map(len, artifacts.values())) + 32_768 > MAX_TOTAL_BYTES:
                raise ValueError("Perception retained byte capacity reached.")
            created = _stamp(datetime.now(UTC))
            attempt = {"schema_version": 1, "request_id": request_id, "operation": operation,
                "status": "accepted", "created_at": created, "digest": digest,
                "source_sha256": source_sha, "processing_mode": mode,
                "capture_origins": evidence["capture_origins"], "observations": len(evidence["observations"]),
                "zones": len(evidence["zones"]), "model_calls": 0, **provenance}
            attempt_raw = _bytes(attempt)
            # Commit the reservation before any artifact publication. It alone is
            # sufficient to hold the pack after a crash or a partial filesystem write.
            with self._owner_guard(external), self._database(create=True) as db:
                if db.execute("SELECT COUNT(*) FROM perception_attempts WHERE status!='completed'").fetchone()[0]:
                    raise PerceptionConflict("Perception is held by an incomplete attempt.")
                actual = db.execute("SELECT revision FROM perception_meta WHERE id=1").fetchone()[0]
                self._check_revision(revision, actual)
                if self._controls() != external:
                    raise PerceptionConflict("Perception control revision changed before dispatch.")
                db.execute("INSERT INTO perception_attempts VALUES(?,?,?,?,?,?,?,?)", (request_id, operation, digest, "accepted", created, None, attempt_raw.decode("utf-8"), None))
                db.execute("UPDATE perception_meta SET revision=revision+1 WHERE id=1")
            reserved_revision = revision + 1
            try:
                self._checkpoint("accepted")
                self._publish(self.root / request_id / "attempt.json", attempt_raw)
                artifacts = {"attempt.json": attempt_raw, **artifacts}
                for name, value in artifacts.items():
                    if name != "attempt.json":
                        self._publish(self.root / request_id / name, value)
                receipt = {**attempt, "status": "completed", "completed_at": _stamp(datetime.now(UTC)),
                    "implementation": IMPLEMENTATION, "verified": True, "live_available": False,
                    "canonical_sha256": _sha(artifacts["evidence.json"]),
                    "artifacts": [{"name": name, "bytes": len(value), "sha256": _sha(value)} for name, value in artifacts.items()],
                    "warning": "Owner-supplied recorded evidence; replay is never live, locally qualified sensing or action authority."}
                receipt_raw = _bytes(receipt)
                self._checkpoint("before_commit")
                with self._owner_guard(external), self._database(create=True) as db:
                    actual = db.execute("SELECT revision FROM perception_meta WHERE id=1").fetchone()[0]
                    if actual != reserved_revision or self._controls() != external:
                        raise PerceptionConflict("Perception controls changed before publication.")
                    def commit_control():
                        if self._controls() != external:
                            raise PerceptionConflict("Perception control revision changed at publication.")
                    self._publish(self.root / request_id / "receipt.json", receipt_raw, checkpoint=commit_control)
                    # Re-read all source and derived bytes, even on initial publication.
                    candidate = {"request_id": request_id, "receipt_sha256": _sha(receipt_raw), "receipt": receipt_raw.decode("utf-8")}
                    self._verified(candidate)
                    commit_control()
                    db.execute("UPDATE perception_attempts SET status='completed',receipt_sha256=?,receipt=? WHERE request_id=? AND status='accepted'", (_sha(receipt_raw), receipt_raw.decode("utf-8"), request_id))
                    db.execute("UPDATE perception_meta SET revision=revision+1 WHERE id=1")
                return receipt
            except BaseException as error:
                # No error path publishes a success receipt or resets a held ID.
                # If the final commit succeeded but a later interruption occurred,
                # its completed record remains completed and is verified on reuse.
                with self._database(create=True) as db:
                    db.execute("UPDATE perception_attempts SET status='failed',error=? WHERE request_id=? AND status='accepted'", (str(error)[:500] or type(error).__name__, request_id))
                    db.execute("UPDATE perception_meta SET revision=revision+1 WHERE id=1")
                raise
