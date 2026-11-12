"""Finite, owner-dispatched local vision. No inference or device opens on reads.

An accepted interrupted/failed job is a durable hold across identifiers. Camera
sessions have a short owner lease and are never recovered or restarted. Model
scores and frame differences are evidence, never action authority.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import subprocess
import sys
import threading
import time

from .atomic import publish_bytes
from .config import _allowed_path
from .perception import _bytes, _date, _fields, _identifier, _load, _sha, _stamp, _text, validate_bundle
from .worker import WorkerError, _environment, _terminate, _SLOTS

ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION = "local-vision-yolox-1"
MODEL_SHA256 = "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"
MAX_VIDEO_BYTES = 512 * 1024**2
MAX_DURATION_MS = 120_000
MAX_SAMPLES = 241
MAX_OUTPUT_BYTES = 2 * 1024**2
MAX_THUMBNAIL_BYTES = 120_000
MAX_ARTIFACT_BYTES = 4 * 1024**2
MAX_LEDGER_BYTES = 8 * 1024**2
MAX_ATTEMPTS = 100
MAX_RETAINED_BYTES = 64 * 1024**2
MEMORY_BYTES = 2 * 1024**3
CPU_SECONDS = 180
TIMEOUT_SECONDS = 190
LEASE_SECONDS = 5
FRESHNESS_MS = 3_000
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
_LOCK = threading.RLock()
_ACTIVE = {}
UTC = timezone.utc


class VisionConflict(ValueError):
    """A stale owner fence, held attempt, active job, or reused identifier."""


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(name + " is outside its integer bound.")
    return value


def _regular(path, maximum, *, allow_empty=False):
    path = _allowed_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not (0 if allow_empty else 1) <= info.st_size <= maximum:
        raise ValueError("Vision assets and inputs must be bounded, unlinked regular files.")
    return info


def _hash_file(path, maximum):
    before = _regular(path, maximum, allow_empty=True)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        if _identity(os.fstat(source.fileno())) != _identity(before):
            raise ValueError("Vision file identity changed before reading.")
        for block in iter(lambda: source.read(1024**2), b""):
            size += len(block)
            if size > maximum:
                raise ValueError("Vision file grew beyond its byte bound while reading.")
            digest.update(block)
        after = os.fstat(source.fileno())
    if _identity(before) != _identity(after) or _identity(path.stat()) != _identity(before):
        raise ValueError("Vision file changed while reading.")
    return digest.hexdigest()


def vision_assets(*, verify=False, camera=False):
    """Inspect fixed packaged assets; never import optional code into the server."""
    root = _allowed_path(ROOT / "runtime" / "vision")
    shipped = ROOT / "ariadne" / "resources" / "vision" / "model-manifest.json"
    manifest_path = root / "manifest.json"
    try:
        for path in (shipped, manifest_path):
            _regular(path, 2 * 1024**2)
        raw = manifest_path.read_bytes()
        if raw != shipped.read_bytes():
            raise ValueError("Vision runtime manifest differs from its shipped seal.")
        manifest = _load(raw, 2 * 1024**2)
        model, runtime = manifest["model"], manifest["runtime"]
        if (manifest["schema_version"] != 1 or model["sha256"] != MODEL_SHA256
                or model["path"] != "models/yolox_nano.onnx" or model["bytes"] != 3659407
                or runtime["python_dir"] != "python" or runtime["opencv_version"] != "5.0.0"
                or runtime["numpy_version"] != "2.5.4"):
            raise ValueError("Vision runtime or model pin is unsupported.")
        files = runtime["files"]
        if type(files) is not list or not 1 <= len(files) <= 2000:
            raise ValueError("Vision runtime manifest exceeds its file bound.")
        selected = [{"path": model["path"], "bytes": model["bytes"], "sha256": model["sha256"]}]
        selected += files
        ffmpeg = manifest.get("decoder")
        if type(ffmpeg) is not dict or ffmpeg.get("path") != "ffmpeg/ffmpeg.exe":
            raise ValueError("The sealed FD-only video decoder has not been provisioned.")
        decoder_entry = next((entry for entry in files if type(entry) is dict and entry.get("path") == ffmpeg["path"]), None)
        if decoder_entry is None or any(decoder_entry.get(key) != ffmpeg.get(key) for key in ("bytes", "sha256")):
            raise ValueError("The native decoder is missing its matching runtime file seal.")
        _regular(root / ffmpeg["path"], 256 * 1024**2)
        if camera:
            if os.name != "nt" or type(ffmpeg) is not dict:
                raise ValueError("Stable-identity USB capture requires the packaged Windows FFmpeg adapter.")
        seen = set()
        for entry in selected:
            relative = entry["path"]
            if type(relative) is not str or "\\" in relative or ":" in relative or Path(relative).is_absolute() or ".." in Path(relative).parts or relative in seen:
                raise ValueError("Invalid vision asset path.")
            seen.add(relative)
            path = _allowed_path(root / relative)
            if not path.is_relative_to(root) or type(entry["sha256"]) is not str or not _HASH.fullmatch(entry["sha256"]):
                raise ValueError("Invalid vision asset seal.")
            info = _regular(path, 256 * 1024**2, allow_empty=True)
            if info.st_size != entry["bytes"] or (verify and _hash_file(path, 256 * 1024**2) != entry["sha256"]):
                raise ValueError("Vision asset integrity check failed: " + relative)
        # Python may execute an unlisted version-specific config or bytecode
        # before any listed module's __file__ can be checked. Seal membership as
        # well as contents, including native DLL/model/decoder directories.
        expected = {entry["path"] for entry in selected}
        count = 0
        pending = [root / name for name in ("python", "models", "ffmpeg")]
        while pending:
            directory = _allowed_path(pending.pop())
            for path in directory.iterdir():
                count += 1
                if count > 3000:
                    raise ValueError("Vision executable runtime inventory exceeds its bound.")
                _allowed_path(path)
                if path.is_dir():
                    relative = path.relative_to(root).as_posix() + "/"
                    if not any(name.startswith(relative) for name in expected):
                        raise ValueError("Unsealed vision runtime directory: " + relative)
                    pending.append(path)
                elif path.relative_to(root).as_posix() not in expected:
                    raise ValueError("Unsealed vision runtime file: " + path.relative_to(root).as_posix())
        return {"ready": True, "model_id": model["id"], "model_sha256": MODEL_SHA256, "runtime_manifest_sha256": _sha(raw),
                "runtime_root": str(root), "manifest": manifest, "reason": None}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"ready": False, "model_id": "yolox-nano-0.1.1rc0", "model_sha256": MODEL_SHA256,
                "reason": str(error)[:300]}


def run_vision(payload, *, cancelled=None, stop=None, progress=None, camera=False):
    """One finite OS-limited decoder/inference process, bounded streaming output."""
    raw = _bytes(payload)
    if len(raw) > 32_768:
        raise ValueError("Vision worker request exceeds its bound.")
    if not _SLOTS.acquire(blocking=False):
        raise WorkerError("Both local capability workers are busy; wait for a current job to finish.")
    process = None
    directory_guard = None
    threads = []
    overflow = threading.Event()
    messages = []
    error = []
    lock = threading.Lock()
    deadline = time.monotonic() + (payload.get("duration_ms", MAX_DURATION_MS) / 1000 + 30 if camera else TIMEOUT_SECONDS)
    try:
        environment = _environment(None)
        environment.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                            "NUMEXPR_NUM_THREADS": "1", "OPENCV_LOG_LEVEL": "SILENT"})
        from .tools import ToolRegistry
        directory_guard = object.__new__(ToolRegistry)._directory(Path(environment["TEMP"]))
        directory_guard.__enter__()
        if cancelled and cancelled():
            raise VisionConflict("Vision controls changed before worker dispatch.")
        process = subprocess.Popen([sys.executable, "-I", "-B", str(ROOT / "ariadne" / "_worker_bootstrap.py"),
                                    "_vision_worker.py", str(MEMORY_BYTES), str(CPU_SECONDS), "2"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=environment, cwd=str(ROOT), start_new_session=os.name == "posix",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def read():
            total = 0
            try:
                while True:
                    line = process.stdout.readline(128_001)
                    if not line:
                        break
                    total += len(line)
                    if total > MAX_OUTPUT_BYTES or len(line) > 128_000 or not line.endswith(b"\n"):
                        overflow.set()
                        _terminate(process)
                        break
                    value = _load(line, 128_000)
                    if type(value) is not dict:
                        raise ValueError("Invalid vision worker response.")
                    if value.get("type") == "frame" and progress:
                        progress(value["frame"])
                    with lock:
                        messages.append(value)
            except (OSError, ValueError, KeyError) as exc:
                error.append(exc)
                _terminate(process)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        threads.append(reader)
        process.stdin.write(raw)
        process.stdin.flush()
        stop_sent = False
        stop_deadline = None
        next_heartbeat = time.monotonic()
        while process.poll() is None:
            if cancelled and cancelled():
                raise VisionConflict("Vision was revoked by owner pause or control revision.")
            if stop and stop() and not stop_sent:
                try:
                    process.stdin.write(b'{"stop":true}\n')
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                stop_sent, stop_deadline = True, time.monotonic() + 3
            if camera and payload["operation"] == "camera" and not stop_sent and time.monotonic() >= next_heartbeat:
                try:
                    process.stdin.write(b'{"lease":true}\n')
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                next_heartbeat = time.monotonic() + 1
            if overflow.is_set() or error:
                raise WorkerError("Vision worker output is invalid or exceeds its byte limit.")
            if time.monotonic() > deadline or (stop_deadline is not None and time.monotonic() > stop_deadline):
                raise WorkerError("Vision worker exceeded its wall-time or stop deadline.")
            time.sleep(.04)
        reader.join(timeout=2)
        if reader.is_alive() or overflow.is_set() or error or process.returncode:
            raise WorkerError("Vision worker failed or exceeded its resource limits.")
        if cancelled and cancelled():
            raise VisionConflict("Vision controls changed before result publication.")
        if not messages or messages[-1].get("type") != "result":
            raise WorkerError("Vision worker did not return a complete result.")
        result = messages[-1]
        if result.get("error"):
            raise WorkerError(str(result["error"])[:300])
        frames = [row["frame"] for row in messages if row.get("type") == "frame"]
        if len(frames) > MAX_SAMPLES:
            raise WorkerError("Vision output exceeds its sample bound.")
        result["frames"] = frames
        return result
    finally:
        try:
            if process is not None:
                if process.poll() is None or os.name == "posix":
                    _terminate(process)
                process.wait(timeout=5)
                for thread in threads:
                    thread.join(timeout=2)
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()
        finally:
            if directory_guard:
                directory_guard.__exit__(None, None, None)
            _SLOTS.release()


class Vision:
    def __init__(self, registry, store):
        self.registry, self.store = registry, store
        self.root = _allowed_path(registry.settings.data_dir / "vision")
        self.path = self.root / "ledger.sqlite3"
        self.key = str(self.path)

    @contextmanager
    def _database(self, *, create=False):
        _allowed_path(self.path)
        if not create and not self.path.exists():
            self.registry._check_ancestors(self.root)
            yield None
            return
        with _LOCK, self.registry._directory(self.root, create=create):
            for suffix in ("", "-journal", "-wal", "-shm"):
                target = _allowed_path(str(self.path) + suffix)
                if target.exists():
                    _regular(target, MAX_LEDGER_BYTES)
            db = sqlite3.connect(self.path if create else self.path.as_uri() + "?mode=ro", uri=not create, timeout=5)
            db.row_factory = sqlite3.Row
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            try:
                db.execute("PRAGMA trusted_schema=OFF")
                if create:
                    db.executescript("CREATE TABLE IF NOT EXISTS vision_meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL); INSERT OR IGNORE INTO vision_meta VALUES(1,0); CREATE TABLE IF NOT EXISTS vision_attempts(request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,status TEXT NOT NULL,operation TEXT NOT NULL,created_at TEXT NOT NULL,receipt TEXT NOT NULL,receipt_sha256 TEXT,error TEXT);")
                    db.execute("CREATE TABLE IF NOT EXISTS vision_reviews(request_id TEXT PRIMARY KEY,receipt TEXT NOT NULL,sha256 TEXT NOT NULL)")
                    db.execute("BEGIN IMMEDIATE")
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
            return db.execute("SELECT revision FROM vision_meta WHERE id=1").fetchone()[0], list(db.execute("SELECT * FROM vision_attempts ORDER BY created_at DESC,request_id LIMIT ?", (MAX_ATTEMPTS,)))

    def _usage(self, rows):
        """Count physical files, including failed/reviewed partial publications."""
        metadata = transient = 0
        if not self.root.exists():
            return {"retained_bytes": 0, "transient_cache_bytes": 0}
        ids = {row["request_id"] for row in rows}
        names = {"attempt.json", "source.json", "evidence.json", "recording.json", "receipt.json", "failure.json", "review.json", "thumbnail.jpg"}
        inputs = {"input" + suffix for suffix in _VIDEO_SUFFIXES}
        entries = 0
        with self.registry._directory(self.root):
            for path in self.root.iterdir():
                entries += 1
                if entries > MAX_ATTEMPTS + 4:
                    raise ValueError("Vision private inventory exceeds its bound.")
                _allowed_path(path)
                if path.name in {"ledger.sqlite3", "ledger.sqlite3-journal", "ledger.sqlite3-wal", "ledger.sqlite3-shm"}:
                    _regular(path, MAX_LEDGER_BYTES)
                    continue
                if path.name not in ids or not path.is_dir():
                    raise ValueError("Unexpected retained vision path.")
                children = 0
                with self.registry._directory(path):
                    for child in path.iterdir():
                        children += 1
                        if children > 20:
                            raise ValueError("Vision attempt inventory exceeds its bound.")
                        _allowed_path(child)
                        info = child.stat()
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise ValueError("Retained vision files must be unlinked regular files.")
                        if child.name in inputs:
                            if info.st_size > MAX_VIDEO_BYTES:
                                raise ValueError("Vision transient video exceeds its byte bound.")
                            transient += info.st_size
                        elif child.name in names or any(re.fullmatch(r"\." + re.escape(name) + r"\.box-[a-f0-9]{24}\.tmp", child.name) for name in names):
                            if info.st_size > MAX_ARTIFACT_BYTES:
                                raise ValueError("Vision retained artifact exceeds its byte bound.")
                            metadata += info.st_size
                        else:
                            raise ValueError("Unexpected retained vision artifact.")
        return {"retained_bytes": metadata, "transient_cache_bytes": transient}

    def _temporary_source(self, request_id, summary):
        folder = self.root / request_id
        if not folder.exists():
            return None
        expected = "input" + Path(summary.get("source_path") or "").suffix.lower()
        found = None
        with self.registry._directory(folder):
            for suffix in _VIDEO_SUFFIXES:
                path = folder / ("input" + suffix)
                if not path.exists():
                    continue
                _allowed_path(path)
                info = path.stat()
                if path.name != expected or found is not None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_VIDEO_BYTES:
                    raise ValueError("Unexpected private temporary vision source.")
                found = {"filename": path.name, "bytes": info.st_size}
        return found

    def _controls(self):
        paused = self.store.setting("paused", False)
        revision = self.store.setting("perception_revision", 0)
        if type(paused) is not bool or type(revision) is not int or revision < 0:
            raise VisionConflict("Invalid owner vision control state.")
        return paused, revision

    @contextmanager
    def _owner_guard(self, expected):
        with self.store.connect(require_existing=True) as db:
            db.execute("BEGIN IMMEDIATE")
            controls = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key,value FROM settings WHERE key IN ('paused','perception_revision')")}
            if controls.get("paused", False) or controls.get("perception_revision", 0) != expected:
                raise VisionConflict("Vision owner controls changed before dispatch or publication.")
            yield

    def _revoked(self, expected):
        try:
            paused, revision = self._controls()
            return paused or revision != expected
        except (ValueError, OSError, sqlite3.Error):
            return True

    def _read(self, path, maximum=MAX_ARTIFACT_BYTES):
        _allowed_path(path)
        with self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path, directory), "rb") as source:
                before = os.fstat(source.fileno())
                if before.st_size > maximum:
                    raise ValueError("Vision retained artifact exceeds its bound.")
                raw = source.read(maximum + 1)
                if len(raw) > maximum or _identity(before) != _identity(os.fstat(source.fileno())):
                    raise ValueError("Vision artifact changed while reading.")
        return raw

    def _publish(self, path, raw):
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("Vision retained artifact exceeds its bound.")
        with self.registry._directory(path.parent, create=True):
            publish_bytes(path, raw)

    def _verified(self, row):
        raw = self._read(self.root / row["request_id"] / "receipt.json")
        if _sha(raw) != row["receipt_sha256"] or raw.decode("utf-8") != row["receipt"]:
            raise ValueError("Vision receipt integrity verification failed.")
        receipt = _load(raw, MAX_ARTIFACT_BYTES)
        if receipt["request_id"] != row["request_id"] or receipt["status"] not in {"completed", "stopped_empty"}:
            raise ValueError("Vision receipt identity mismatch.")
        names = set()
        files = {}
        for artifact in receipt["artifacts"]:
            name = artifact["name"]
            if name not in {"attempt.json", "source.json", "evidence.json", "recording.json", "thumbnail.jpg"} or name in names:
                raise ValueError("Unexpected vision artifact.")
            names.add(name)
            data = self._read(self.root / row["request_id"] / name)
            if len(data) != artifact["bytes"] or _sha(data) != artifact["sha256"]:
                raise ValueError("Vision artifact integrity verification failed.")
            files[name] = data
        required = {"attempt.json", "source.json", "evidence.json"}
        if receipt["status"] == "completed":
            required.add("recording.json")
        if not required.issubset(names):
            raise ValueError("Missing retained vision artifact.")
        if _sha(files["evidence.json"]) != receipt["derived_sha256"] or ("recording.json" in files and _sha(files["recording.json"]) != receipt["recording_sha256"]):
            raise ValueError("Vision derived evidence hash mismatch.")
        if receipt["status"] == "stopped_empty":
            evidence = _load(files["evidence.json"], MAX_ARTIFACT_BYTES)
            if evidence["frames"] or evidence["recording"] is not None or "recording.json" in files:
                raise ValueError("A stopped empty session cannot contain observation evidence.")
        return {**receipt, "verified": True}, files

    def _live(self, active=None):
        if not active or active.get("operation") != "camera" or active["stop"].is_set() or active.get("finished"):
            return {"state": "off", "observed_at": None, "age_ms": None, "presence": "unknown", "motion": "unknown", "objects": [], "source": None}
        frame = active.get("latest")
        age = int((time.monotonic() - active["latest_monotonic"]) * 1000) if frame else None
        source_age = None if not frame or not frame.get("acquired_at") else int((datetime.now(UTC) - _date(frame["acquired_at"], "acquired_at")).total_seconds() * 1000)
        source_lag = frame.get("source_lag_ms") if frame else None
        fresh = frame is not None and age <= FRESHNESS_MS and source_age is not None and -2000 <= source_age <= FRESHNESS_MS and source_lag is not None and source_lag + max(0, source_age) <= FRESHNESS_MS and not self._revoked(active["control_revision"]) and time.monotonic() <= active["lease_deadline"]
        return {"state": "fresh" if fresh else "stale" if frame else "waiting_for_frames",
                "observed_at": frame.get("acquired_at") if frame else None, "age_ms": age, "source_age_ms": source_age,
                "clock_kind": "producer_unverified", "driver_latency": "unknown",
                "source_lag_ms": source_lag, "lag_method": "source_pts_vs_capture_elapsed_unverified",
                "presence": "present" if fresh and any(obj["label"] == "person" for obj in frame["detections"]) else "unknown",
                "motion": frame["motion"]["state"] if fresh else "unknown",
                "objects": frame["detections"] if fresh else [], "source": "current_session" if fresh else None}

    def _summary(self, row):
        summary = _load(row["receipt"].encode(), MAX_ARTIFACT_BYTES)
        summary = {key: value for key, value in summary.items() if key != "artifacts"}
        summary.update(status=row["status"], verified=False)
        active = _ACTIVE.get((self.key, row["request_id"]))
        if row["status"] in {"completed", "stopped_empty"}:
            try:
                self._verified(row)
                summary["verified"] = True
            except (ValueError, OSError, KeyError) as error:
                summary.update(status="invalid", error=str(error)[:300])
        elif row["status"] == "accepted":
            summary.update(status="running" if active and not active.get("finished") else "interrupted")
        else:
            summary["error"] = row["error"]
            if row["receipt_sha256"]:
                try:
                    failure_raw = self._read(self.root / row["request_id"] / "failure.json")
                    if _sha(failure_raw) != row["receipt_sha256"] or failure_raw.decode() != row["receipt"]:
                        raise ValueError("Retained vision failure integrity verification failed.")
                except (ValueError, OSError) as error:
                    summary.update(status="invalid", error=str(error)[:300])
        if summary["status"] in {"failed", "interrupted", "reviewed"}:
            summary["failure_sha256"] = row["receipt_sha256"] or _sha(_bytes({"request_id": row["request_id"], "digest": row["digest"],
                "error": row["error"], "failure_kind": "failed" if row["error"] else "interrupted"}))
            summary["reviewed"] = row["status"] == "reviewed"
            summary["failure_reviewed"] = summary["reviewed"]
            if summary["reviewed"]:
                try:
                    with self._database() as db:
                        review = db.execute("SELECT receipt,sha256 FROM vision_reviews WHERE request_id=?", (row["request_id"],)).fetchone()
                    raw = self._read(self.root / row["request_id"] / "review.json")
                    if review is None or raw.decode() != review["receipt"] or _sha(raw) != review["sha256"]:
                        raise ValueError("Vision owner review integrity verification failed.")
                    summary["owner_review"] = _load(raw, MAX_ARTIFACT_BYTES)
                except (ValueError, OSError, sqlite3.Error) as error:
                    summary.update(status="invalid", error=str(error)[:300])
        temporary = self._temporary_source(row["request_id"], summary)
        summary["temporary_source"] = temporary
        summary["temporary_source_bytes"] = temporary["bytes"] if temporary else 0
        if temporary is not None:
            summary["raw_video_retained"] = True
        summary["live"] = self._live(active)
        return summary

    def status(self):
        revision, rows = self._rows()
        paused, controls = self._controls()
        summaries = [self._summary(row) for row in rows]
        usage = self._usage(rows)
        backend = vision_assets()
        backend = {key: value for key, value in backend.items() if key not in {"manifest", "runtime_root"}}
        return {"schema_version": 1, "implementation": IMPLEMENTATION, "revision": revision,
                "control_revision": controls, "paused": paused, "ready": backend["ready"], "backend": backend,
                "hold": any(row["status"] in {"failed", "interrupted", "invalid"} for row in summaries),
                "storage": {**usage, "transient_cache_limit_bytes": MAX_VIDEO_BYTES},
                "sessions": [row for row in summaries if row["operation"] == "camera"],
                "recordings": [row for row in summaries if row["operation"] == "video"],
                "limits": {"video_bytes": MAX_VIDEO_BYTES, "duration_ms": MAX_DURATION_MS, "samples": MAX_SAMPLES,
                           "sample_interval_ms_min": 500, "sample_interval_ms_max": 10000, "frame_width": 1920, "frame_height": 1080,
                           "default_camera_duration_ms": 30000, "lease_ms": LEASE_SECONDS * 1000, "freshness_ms": FRESHNESS_MS,
                           "retained_bytes": MAX_RETAINED_BYTES, "attempts": MAX_ATTEMPTS},
                "microphone": "off", "camera_autostart": False, "network": False, "action_authority": False,
                "recovery": "Failed or interrupted accepted work holds new jobs. Capture is never resumed automatically."}

    def _source_path(self, value):
        if type(value) is not str or not 1 <= len(value) <= 500 or ":" in value or "\\" in value or value.startswith("/") or ".." in Path(value).parts or any(ord(c) < 32 for c in value):
            raise ValueError("Choose a relative workspace video path without protocols or traversal.")
        path = self.registry._path(value)
        if path.suffix.lower() not in _VIDEO_SUFFIXES:
            raise ValueError("Choose a local MP4, MOV, MKV, WebM or AVI file.")
        return path

    def inspect_source(self, request):
        _fields(request, {"source_path"}, label="video inspection")
        path = self._source_path(request["source_path"])
        with self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path, directory), "rb") as source:
                before = os.fstat(source.fileno())
                if not 0 < before.st_size <= MAX_VIDEO_BYTES:
                    raise ValueError("Video exceeds its 512 MiB input bound.")
                digest = hashlib.sha256()
                size = 0
                for chunk in iter(lambda: source.read(1024**2), b""):
                    size += len(chunk)
                    if size > MAX_VIDEO_BYTES:
                        raise ValueError("Video grew beyond its 512 MiB input bound.")
                    digest.update(chunk)
                if _identity(before) != _identity(os.fstat(source.fileno())):
                    raise ValueError("Video changed during source inspection.")
        return {"source_path": path.relative_to(self.registry.root).as_posix(), "source_sha256": digest.hexdigest(), "bytes": before.st_size}

    def _request(self, request, operation):
        required = {"request_id", "expected_control_revision"}
        required |= {"source_path", "source_sha256"} if operation == "video" else {"device_id"}
        optional = {"expected_revision", "title", "zone_id", "sample_interval_ms", "thumbnail", "duration_ms"}
        optional |= {"recording_started_at"} if operation == "video" else set()
        _fields(request, required, optional, "vision request")
        request = dict(request)
        _identifier(request["request_id"], "request_id")
        _integer(request["expected_control_revision"], "expected_control_revision", 0, 2**53 - 1)
        if "expected_revision" in request:
            _integer(request["expected_revision"], "expected_revision", 0, 2**53 - 1)
        request.setdefault("title", "Selected recorded video" if operation == "video" else "Owner-started USB camera session")
        _text(request["title"], "title")
        _identifier(request.setdefault("zone_id", "camera-zone"), "zone_id")
        _integer(request.setdefault("sample_interval_ms", 1000), "sample_interval_ms", 500, 10000)
        _integer(request.setdefault("duration_ms", 30000), "duration_ms", 1000, MAX_DURATION_MS)
        if type(request.setdefault("thumbnail", False)) is not bool:
            raise ValueError("thumbnail must be a boolean opt-in.")
        if operation == "video":
            self._source_path(request["source_path"])
            if type(request["source_sha256"]) is not str or not _HASH.fullmatch(request["source_sha256"]):
                raise ValueError("Video analysis requires the inspected source SHA-256.")
            started = request.setdefault("recording_started_at", None)
            if started is not None:
                _date(started, "recording_started_at")
        else:
            _identifier(request["device_id"], "device_id")
        return request

    def _reserve(self, request, operation):
        request_id = request["request_id"]
        identity = {key: value for key, value in request.items() if key not in {"expected_revision", "expected_control_revision"}}
        digest = _sha(_bytes({"operation": operation, **identity}))
        with _LOCK:
            paused, controls = self._controls()
            if paused or controls != request["expected_control_revision"]:
                raise VisionConflict("Vision owner controls changed; refresh before starting a new job.")
            revision, rows = self._rows()
            if "expected_revision" in request and request["expected_revision"] != revision:
                raise VisionConflict("Vision ledger revision changed; refresh before starting a new job.")
            for row in rows:
                if row["request_id"] == request_id:
                    if row["digest"] != digest:
                        raise VisionConflict("A vision request_id cannot be reused for different inputs.")
                    if row["status"] == "completed":
                        return self._verified(row)[0], None
                    raise VisionConflict("Accepted vision jobs cannot be retried or restarted.")
            if any(key[0] == self.key and not value.get("finished") for key, value in _ACTIVE.items()):
                raise VisionConflict("A finite local vision job is already running.")
            if len(rows) >= MAX_ATTEMPTS:
                raise ValueError("Vision attempt capacity reached.")
            usage = self._usage(rows)
            for row in rows:
                if row["status"] == "reviewed":
                    if self._summary(row)["status"] == "invalid":
                        raise VisionConflict("Vision retained failure integrity verification failed.")
                    continue
                if row["status"] not in {"completed", "stopped_empty"}:
                    raise VisionConflict("Vision is held after an accepted failed or interrupted attempt.")
                receipt, _ = self._verified(row)
            if usage["transient_cache_bytes"]:
                raise VisionConflict("An interrupted private video snapshot remains; exact owner failure review must remove it before new work.")
            if usage["retained_bytes"] + MAX_ARTIFACT_BYTES * 3 > MAX_RETAINED_BYTES:
                raise ValueError("Vision retained byte capacity reached.")
            created = _stamp(datetime.now(UTC))
            attempt = {"schema_version": 1, "request_id": request_id, "session_id": request_id if operation == "camera" else None,
                       "operation": operation, "status": "accepted", "created_at": created, "digest": digest,
                       "title": request["title"], "model_sha256": MODEL_SHA256, "thumbnail_opt_in": request["thumbnail"],
                       "source_path": request.get("source_path"), "source_sha256": request.get("source_sha256"),
                       "recording_started_at": request.get("recording_started_at"), "duration_ms": request["duration_ms"],
                       "device_id": request.get("device_id"), "microphone": "off", "action_authority": False}
            with self._owner_guard(controls), self._database(create=True) as db:
                if db.execute("SELECT revision FROM vision_meta WHERE id=1").fetchone()[0] != revision:
                    raise VisionConflict("Vision revision changed before dispatch.")
                db.execute("INSERT INTO vision_attempts VALUES(?,?,?,?,?,?,?,?)", (request_id, digest, "accepted", operation, created, _bytes(attempt).decode(), None, None))
                db.execute("UPDATE vision_meta SET revision=revision+1 WHERE id=1")
            active = {"operation": operation, "stop": threading.Event(), "control_revision": controls,
                      "lease_deadline": time.monotonic() + LEASE_SECONDS, "lease_token": secrets.token_urlsafe(24),
                      "latest": None, "latest_monotonic": None, "finished": False, "request": request}
            _ACTIVE[(self.key, request_id)] = active
            return attempt, active

    def analyze_video(self, request):
        request = self._request(request, "video")
        # Rejected source/model checks never reserve or hold an attempt.
        source = self.inspect_source({"source_path": request["source_path"]})
        if source["source_sha256"] != request["source_sha256"]:
            raise ValueError("Selected video differs from the inspected SHA-256.")
        assets = vision_assets(verify=True)
        if not assets["ready"]:
            raise ValueError(assets["reason"])
        attempt, active = self._reserve(request, "video")
        if active is None:
            return attempt
        return self._execute(request, attempt, active, assets)

    def discover_cameras(self):
        assets = vision_assets(verify=True, camera=True)
        if not assets["ready"]:
            return {"devices": [], "opens_devices": False, "supported": False, "method": "DirectShow alternative device names", "reason": assets["reason"]}
        result = run_vision({"operation": "discover"}, cancelled=lambda: False, camera=True)
        devices = result.get("devices", [])
        if type(devices) is not list or len(devices) > 16:
            raise WorkerError("Camera inventory exceeds its bound.")
        return {"devices": devices, "opens_devices": False, "supported": True, "method": "DirectShow alternative device names", "reason": None}

    def start_camera(self, request):
        request = self._request(request, "camera")
        assets = vision_assets(verify=True, camera=True)
        if not assets["ready"]:
            raise ValueError(assets["reason"])
        devices = self.discover_cameras()["devices"]
        if request["device_id"] not in {device["device_id"] for device in devices}:
            raise ValueError("Select a camera from the current explicit read-only inventory.")
        attempt, active = self._reserve(request, "camera")
        if active is None:
            return attempt
        def execute():
            try:
                self._execute(request, attempt, active, assets)
            except BaseException:
                pass  # Persistent ledger supplies the bounded failure on reads.
        thread = threading.Thread(target=execute, name="vision-" + request["request_id"], daemon=True)
        active["thread"] = thread
        thread.start()
        return {**attempt, "status": "running", "lease_token": active["lease_token"], "lease_ms": LEASE_SECONDS * 1000, "live": self._live(active)}

    def touch_camera(self, request):
        _fields(request, {"session_id", "lease_token"}, label="camera lease")
        _identifier(request["session_id"], "session_id")
        if type(request["lease_token"]) is not str:
            raise VisionConflict("Invalid camera owner lease.")
        with _LOCK:
            active = _ACTIVE.get((self.key, request["session_id"]))
            if not active or active["operation"] != "camera" or active["finished"] or active["stop"].is_set() or time.monotonic() > active["lease_deadline"] or self._revoked(active["control_revision"]) or not secrets.compare_digest(request["lease_token"], active["lease_token"]):
                raise VisionConflict("Camera owner lease expired or was revoked; capture cannot resume.")
            active["lease_deadline"] = time.monotonic() + LEASE_SECONDS
        return {"session_id": request["session_id"], "lease_ms": LEASE_SECONDS * 1000}

    def stop_camera(self, request):
        _fields(request, {"session_id"}, {"expected_control_revision", "expected_revision"}, "camera stop")
        _identifier(request["session_id"], "session_id")
        with _LOCK:
            active = _ACTIVE.get((self.key, request["session_id"]))
            if active and active["operation"] == "camera":
                active["stop"].set()
        return {"session_id": request["session_id"], "stop_requested": True, "live": self._live()}

    def close(self):
        with _LOCK:
            active = [value for key, value in _ACTIVE.items() if key[0] == self.key and not value["finished"]]
            for value in active:
                value["stop"].set()
        for value in active:
            thread = value.get("thread")
            if thread and thread is not threading.current_thread():
                thread.join(timeout=5)

    def acknowledge_failure(self, request):
        """Explicit owner review permits new IDs; it never retries the held ID."""
        _fields(request, {"request_id", "expected_revision", "expected_control_revision", "failure_sha256"}, {"discard_temporary_source"}, label="vision failure review")
        if type(request.get("discard_temporary_source", False)) is not bool:
            raise ValueError("discard_temporary_source must be an explicit boolean.")
        _identifier(request["request_id"], "request_id")
        for name in ("expected_revision", "expected_control_revision"):
            _integer(request[name], name, 0, 2**53 - 1)
        with _LOCK:
            revision, rows = self._rows()
            if revision != request["expected_revision"]:
                raise VisionConflict("Vision revision changed before owner failure review.")
            row = next((row for row in rows if row["request_id"] == request["request_id"]), None)
            if row is None:
                raise ValueError("Vision failure was not found.")
            summary = self._summary(row)
            if summary["status"] not in {"failed", "interrupted"} or request["failure_sha256"] != summary["failure_sha256"]:
                raise VisionConflict("Review must name the exact current failed or interrupted attempt hash.")
            temporary = summary["temporary_source"]
            if temporary and not request.get("discard_temporary_source", False):
                raise VisionConflict("Explicitly choose to discard this failed job's temporary input copy before acknowledging it.")
            active = _ACTIVE.get((self.key, request["request_id"]))
            if active and not active.get("finished"):
                raise VisionConflict("A camera or decoder worker must finish before owner failure review.")
            with self._owner_guard(request["expected_control_revision"]), self._database(create=True) as db:
                if db.execute("SELECT revision FROM vision_meta WHERE id=1").fetchone()[0] != revision:
                    raise VisionConflict("Vision revision changed during owner failure review.")
                if temporary:
                    folder = self.root / request["request_id"]
                    with self.registry._directory(folder):
                        current = self._temporary_source(request["request_id"], summary)
                        if current != temporary:
                            raise VisionConflict("Temporary source changed before owner-authorized discard.")
                        (folder / temporary["filename"]).unlink()
                review = {"schema_version": 1, "request_id": request["request_id"], "reviewed_at": _stamp(datetime.now(UTC)),
                          "failure_sha256": summary["failure_sha256"], "discarded_temporary_source": temporary,
                          "control_revision": request["expected_control_revision"], "reviewed_revision": revision, "retry_permitted": False}
                raw = _bytes(review)
                self._publish(self.root / request["request_id"] / "review.json", raw)
                db.execute("INSERT INTO vision_reviews VALUES(?,?,?)", (request["request_id"], raw.decode(), _sha(raw)))
                db.execute("UPDATE vision_attempts SET status='reviewed' WHERE request_id=? AND status IN ('failed','accepted')", (request["request_id"],))
                db.execute("UPDATE vision_meta SET revision=revision+1 WHERE id=1")
            return {**review, "status": "reviewed", "review_sha256": _sha(raw)}

    def _snapshot_source(self, request, destination):
        source_path = self._source_path(request["source_path"])
        with self.registry._directory(source_path.parent) as directory, self.registry._directory(destination.parent, create=True):
            with os.fdopen(self.registry._open_read(source_path, directory), "rb") as source:
                before = os.fstat(source.fileno())
                if not 0 < before.st_size <= MAX_VIDEO_BYTES:
                    raise ValueError("Video exceeds its byte bound.")
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                digest, size = hashlib.sha256(), 0
                with os.fdopen(descriptor, "wb") as target:
                    for block in iter(lambda: source.read(1024**2), b""):
                        size += len(block)
                        if size > MAX_VIDEO_BYTES:
                            raise ValueError("Video exceeds its byte bound.")
                        digest.update(block)
                        target.write(block)
                    target.flush()
                    os.fsync(target.fileno())
                if _identity(before) != _identity(os.fstat(source.fileno())) or digest.hexdigest() != request["source_sha256"]:
                    raise ValueError("Video changed after selection; no analysis result was published.")
        return {"source_path": request["source_path"], "source_sha256": digest.hexdigest(), "bytes": size,
                "selected_identity": list(_identity(before)), "snapshot_verified": True, "raw_video_retained": False,
                "source_verification": "hashed_at_analysis_only_raw_not_retained", "recording_started_at": request["recording_started_at"], "clock_kind": "producer_unverified" if request["recording_started_at"] else "unknown"}

    def _execute(self, request, attempt, active, assets):
        request_id, operation = request["request_id"], attempt["operation"]
        folder = self.root / request_id
        snapshot = folder / ("input" + Path(request["source_path"]).suffix.lower()) if operation == "video" else None
        try:
            self._publish(folder / "attempt.json", _bytes(attempt))
            source = self._snapshot_source(request, snapshot) if snapshot else {"device_id": request["device_id"], "capture_origin": "live", "raw_video_retained": False, "microphone": "off", "clock_kind": "producer_unverified", "source_verification": "software_frame_arrival_only_raw_not_retained", "driver_latency": "unknown"}
            payload = {"operation": operation, "duration_ms": request["duration_ms"], "sample_interval_ms": request["sample_interval_ms"], "thumbnail": request["thumbnail"]}
            payload.update({"source_path": str(snapshot), "source_sha256": request["source_sha256"]} if snapshot else {"device_id": request["device_id"]})
            def progress(frame):
                self._validate_frame(frame)
                with _LOCK:
                    active["latest"], active["latest_monotonic"] = frame, time.monotonic()
            def stopping():
                if operation == "camera" and time.monotonic() > active["lease_deadline"]:
                    active["stop"].set()
                    active["stop_reason"] = "owner_lease_expired"
                return active["stop"].is_set()
            result = run_vision(payload, cancelled=lambda: self._revoked(active["control_revision"]), stop=stopping, progress=progress, camera=operation == "camera")
            frames = result["frames"]
            stopped_empty = not frames and operation == "camera" and (active["stop"].is_set() or result.get("stop_reason") in {"owner_stop", "worker_lease_expired", "worker_lease_timeout", "parent_disconnected"})
            if not frames and not stopped_empty:
                raise WorkerError("Vision decoded no usable frames.")
            for frame in frames:
                self._validate_frame(frame)
            if snapshot:
                # A completed receipt must never claim deletion which is still
                # pending after its publication. Remove the temporary video now.
                with self.registry._directory(folder):
                    _regular(snapshot, MAX_VIDEO_BYTES)
                    snapshot.unlink()
            bundle = None if stopped_empty else self._recording(request, attempt, frames, result)
            if bundle is not None:
                validate_bundle(_bytes(bundle))
            evidence = {"schema_version": 1, "implementation": IMPLEMENTATION, "operation": operation,
                        "source": source, "model_id": assets["model_id"], "model_sha256": MODEL_SHA256,
                        "runtime_manifest_sha256": assets.get("runtime_manifest_sha256"), "decoder_sha256": assets.get("manifest", {}).get("decoder", {}).get("sha256"),
                        "recording": bundle, "frames": frames,
                        "decoder": result.get("decoder"), "decoded_frames": result.get("decoded_frames"),
                        "duration_ms": result.get("duration_ms"), "processing_mode": "replay" if operation == "video" else "live",
                        "coverage": "first_bounded_window" if operation == "video" else "explicit_bounded_session", "requested_window_ms": request["duration_ms"],
                        "warning": "Objects/persons are uncalibrated model inferences. Motion is frame difference evidence. No identities, behavior conclusions, or action authority."}
            artifacts = {"attempt.json": _bytes(attempt), "source.json": _bytes(source), "evidence.json": _bytes(evidence)}
            if bundle is not None:
                artifacts["recording.json"] = _bytes(bundle)
            thumbnail = result.get("thumbnail")
            if thumbnail is not None:
                if not request["thumbnail"] or type(thumbnail) is not str:
                    raise WorkerError("Unexpected retained camera image.")
                data = base64.b64decode(thumbnail, validate=True)
                if not 0 < len(data) <= MAX_THUMBNAIL_BYTES or not data.startswith(b"\xff\xd8"):
                    raise WorkerError("Vision thumbnail exceeds its bound.")
                artifacts["thumbnail.jpg"] = data
            for name, raw in artifacts.items():
                if name != "attempt.json":
                    self._publish(folder / name, raw)
            terminal_status = "stopped_empty" if stopped_empty else "completed"
            receipt = {**attempt, "status": terminal_status, "completed_at": _stamp(datetime.now(UTC)), "verified": True,
                       "implementation": IMPLEMENTATION, "processing_mode": evidence["processing_mode"],
                       "frames": len(frames), "decoded_frames": result.get("decoded_frames"), "duration_ms": result.get("duration_ms"),
                       "source_sha256": source.get("source_sha256"), "derived_sha256": _sha(artifacts["evidence.json"]),
                       "source_verification": source["source_verification"], "retained_verification": "metadata_and_derived_artifacts_only",
                       "runtime_manifest_sha256": evidence["runtime_manifest_sha256"], "decoder_sha256": evidence["decoder_sha256"],
                       "recording_sha256": _sha(artifacts["recording.json"]) if bundle is not None else None, "thumbnail_available": "thumbnail.jpg" in artifacts,
                       "raw_video_retained": False, "stop_reason": active.get("stop_reason") or result.get("stop_reason") or ("owner_stop" if active["stop"].is_set() else "duration_complete"),
                       "coverage": evidence["coverage"], "requested_window_ms": request["duration_ms"], "whole_video_analyzed": False,
                       "artifacts": [{"name": name, "bytes": len(raw), "sha256": _sha(raw)} for name, raw in artifacts.items()]}
            raw = _bytes(receipt)
            with _LOCK, self._owner_guard(active["control_revision"]), self._database(create=True) as db:
                self._publish(folder / "receipt.json", raw)
                self._verified({"request_id": request_id, "receipt": raw.decode(), "receipt_sha256": _sha(raw)})
                db.execute("UPDATE vision_attempts SET status=?,receipt=?,receipt_sha256=? WHERE request_id=? AND status='accepted'", (terminal_status, raw.decode(), _sha(raw), request_id))
                db.execute("UPDATE vision_meta SET revision=revision+1 WHERE id=1")
            return receipt
        except BaseException as error:
            message = (str(error) or type(error).__name__)[:500]
            failure_raw = _bytes({**attempt, "status": "failed", "failed_at": _stamp(datetime.now(UTC)), "error": message})
            failure_hash = None
            try:
                self._publish(folder / "failure.json", failure_raw)
                failure_hash = _sha(failure_raw)
            except (OSError, ValueError):
                pass  # The accepted ledger reservation still holds the module.
            with self._database(create=True) as db:
                db.execute("UPDATE vision_attempts SET status='failed',error=?,receipt=?,receipt_sha256=? WHERE request_id=? AND status='accepted'", (message, failure_raw.decode(), failure_hash, request_id))
                db.execute("UPDATE vision_meta SET revision=revision+1 WHERE id=1")
            raise
        finally:
            with _LOCK:
                active["finished"] = True
                active["latest"] = None
            if snapshot and snapshot.exists():
                # Only this fixed private temporary filename is ever removed.
                with self.registry._directory(folder):
                    info = _allowed_path(snapshot).stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_VIDEO_BYTES:
                        raise ValueError("Vision temporary snapshot identity is invalid.")
                    snapshot.unlink()

    @staticmethod
    def _validate_frame(frame):
        _fields(frame, {"sample_index", "decoded_frame_index", "source_pts_ms", "timeline_offset_ms", "timestamp_basis", "acquired_at", "source_lag_ms", "capture_elapsed_ms", "width", "height", "source_width", "source_height", "frame_sha256", "inference_input_sha256", "detections", "motion", "inference_ms"}, label="vision frame")
        _integer(frame["sample_index"], "sample_index", 0, MAX_SAMPLES - 1)
        _integer(frame["decoded_frame_index"], "decoded_frame_index", 0, 14_400)
        _integer(frame["timeline_offset_ms"], "timeline_offset_ms", 0, MAX_DURATION_MS)
        _integer(frame["width"], "width", 1, 1920)
        _integer(frame["height"], "height", 1, 1080)
        _integer(frame["source_width"], "source_width", 1, 1920)
        _integer(frame["source_height"], "source_height", 1, 1080)
        if frame["acquired_at"] is not None:
            _date(frame["acquired_at"], "acquired_at")
        for key in ("frame_sha256", "inference_input_sha256"):
            if type(frame[key]) is not str or not _HASH.fullmatch(frame[key]):
                raise WorkerError("Invalid decoded frame hash.")
        maximum_pts = 2**53 - 1
        if frame["source_pts_ms"] is not None and (type(frame["source_pts_ms"]) not in (int, float) or not math.isfinite(frame["source_pts_ms"]) or abs(frame["source_pts_ms"]) > maximum_pts):
            raise WorkerError("Invalid original video timestamp.")
        if frame["timestamp_basis"] not in {"container_pts", "camera_source_pts"}:
            raise WorkerError("Invalid frame clock basis.")
        for key in ("source_lag_ms", "capture_elapsed_ms"):
            if frame[key] is not None:
                _integer(frame[key], key, 0, MAX_DURATION_MS + 30000)
        if type(frame["detections"]) is not list or len(frame["detections"]) > 32:
            raise WorkerError("Vision detection bound exceeded.")
        for obj in frame["detections"]:
            _fields(obj, {"label", "score", "bbox_normalized"}, label="detected object")
            _text(obj["label"], "object label", 40)
            if type(obj["score"]) not in (int, float) or not math.isfinite(obj["score"]) or not 0 <= obj["score"] <= 1:
                raise WorkerError("Invalid uncalibrated detection score.")
            if type(obj["bbox_normalized"]) is not list or len(obj["bbox_normalized"]) != 4 or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in obj["bbox_normalized"]):
                raise WorkerError("Invalid normalized detection box.")
        _fields(frame["motion"], {"state", "method", "mean_absolute_difference", "threshold", "previous_frame_sha256"}, label="frame difference")
        if frame["motion"]["state"] not in {"unknown", "detected", "not_detected"} or frame["motion"]["method"] != "grayscale_mean_absolute_difference":
            raise WorkerError("Invalid motion provenance.")

    @staticmethod
    def _recording(request, attempt, frames, result):
        is_live = attempt["operation"] == "camera"
        started = request.get("recording_started_at") or attempt["created_at"]
        observations = []
        for index, frame in enumerate(frames):
            detections = frame["detections"]
            observed = frame["acquired_at"] if is_live else _stamp(_date(started, "recording_started_at") + timedelta(milliseconds=frame["timeline_offset_ms"])) if request.get("recording_started_at") else None
            score = max((obj["score"] for obj in detections), default=None)
            observations.append({"event_id": "frame-" + str(index), "registered_sensor_alias": "local-camera", "producer_boot_id": attempt["request_id"],
                                 "capture_origin": "live" if is_live else "unknown", "processing_mode": "live" if is_live else "replay",
                                 "observed_at": observed, "producer_at": frame["acquired_at"], "received_at": None,
                                 "sequence": frame["decoded_frame_index"], "replay_offset_ms": frame["timeline_offset_ms"],
                                 "presence": "present" if any(obj["label"] == "person" for obj in detections) else "unknown",
                                 "motion": frame["motion"]["state"], "upstream_source_state": "available",
                                 "clock_kind": "producer_unverified" if observed else "unknown", "objects": [{"label": label, "count": min(100, sum(obj["label"] == label for obj in detections))} for label in sorted(set(obj["label"] for obj in detections))[:16]],
                                 "score": score, "score_kind": "uncalibrated_model_score" if score is not None else "unavailable",
                                 "model_hash": MODEL_SHA256, "local_qualification_status": "unqualified",
                                 "quality_flags": ["model_inference", "motion_frame_difference", "source_clock_unknown"] if observed is None else ["model_inference", "motion_frame_difference", "software_clock_unverified"]})
        return {"schema_version": 1, "recording_id": attempt["request_id"], "title": request["title"], "started_at": started,
                "duration_ms": max(frame["timeline_offset_ms"] for frame in frames), "zones": [request["zone_id"]],
                "sensors": [{"registered_sensor_alias": "local-camera", "modality": "camera", "producer_revision": IMPLEMENTATION, "zone_id": request["zone_id"]}],
                "observations": observations}

    def detail(self, request_id):
        _identifier(request_id, "request_id")
        _, rows = self._rows()
        row = next((row for row in rows if row["request_id"] == request_id), None)
        if row is None:
            raise ValueError("Vision job was not found.")
        if row["status"] not in {"completed", "stopped_empty"}:
            return {"receipt": self._summary(row), "evidence": None, "live": self._live(_ACTIVE.get((self.key, request_id)))}
        receipt, files = self._verified(row)
        return {"receipt": receipt, "evidence": _load(files["evidence.json"], MAX_ARTIFACT_BYTES), "live": self._live()}

    def snapshot(self, request_id):
        _identifier(request_id, "request_id")
        _, rows = self._rows()
        row = next((row for row in rows if row["request_id"] == request_id), None)
        if row is None or row["status"] not in {"completed", "stopped_empty"}:
            return None
        _, files = self._verified(row)
        return files.get("thumbnail.jpg")
