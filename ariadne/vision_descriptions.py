"""Explicit, one-frame local descriptions; no acquisition or automatic replay.

Only source hashes, bounded question/answer text and receipts are retained. JPEG
bytes live in one worker closure and the local model request, never an artifact.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import math
import os
import re
import sqlite3
import stat
import threading
import time

from .atomic import publish_bytes
from .config import _allowed_path
from .model import ModelError
from .perception import _bytes, _date, _fields, _identifier, _load, _sha, _stamp
from .vision import VisionConflict

IMPLEMENTATION = "local-vision-description-1"
MAX_ATTEMPTS = 100
MAX_ARTIFACT_BYTES = 65536
MAX_LEDGER_BYTES = 4 * 1024**2
MAX_RETAINED_BYTES = 16 * 1024**2
RESERVED_JOB_BYTES = 128 * 1024
_LOCK = threading.RLock()
_ACTIVE = {}
_HASH = re.compile(r"[a-f0-9]{64}\Z")
UTC = timezone.utc


class VisionDescriptions:
    def __init__(self, vision, model, store, selected_model_callback):
        if not callable(selected_model_callback):
            raise ValueError("Bind the trusted selected-model callback.")
        self.vision, self.model, self.store = vision, model, store
        self.registry = vision.registry
        self.selected_model = selected_model_callback
        self.root = _allowed_path(self.registry.settings.data_dir / "vision-descriptions")
        self.path = self.root / "ledger.sqlite3"
        self.key = str(self.path)
        self.closed = threading.Event()

    def _private_storage(self):
        """New private captions must not be created inside agent file scope."""
        return not self.root.is_relative_to(_allowed_path(self.registry.root))

    def _controls(self):
        paused = self.store.setting("paused", False)
        revision = self.store.setting("perception_revision", 0)
        if type(paused) is not bool or type(revision) is not int or revision < 0:
            raise VisionConflict("Invalid camera-description owner controls.")
        return paused, revision

    @contextmanager
    def _database(self, create=False):
        _allowed_path(self.path)
        if not create and not self.path.exists():
            self.registry._check_ancestors(self.root)
            yield None
            return
        with _LOCK, self.registry._directory(self.root, create=create):
            for suffix in ("", "-journal", "-wal", "-shm"):
                path = _allowed_path(str(self.path)+suffix)
                if path.exists():
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_LEDGER_BYTES:
                        raise ValueError("Description ledger is not a bounded unlinked regular file.")
            db = sqlite3.connect(self.path if create else self.path.as_uri()+"?mode=ro", uri=not create, timeout=5)
            db.row_factory = sqlite3.Row
            deadline = time.monotonic()+5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            try:
                db.execute("PRAGMA trusted_schema=OFF")
                if create:
                    db.executescript("CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL); INSERT OR IGNORE INTO meta VALUES(1,0); CREATE TABLE IF NOT EXISTS attempts(request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,receipt TEXT NOT NULL,receipt_sha256 TEXT,error TEXT); CREATE TABLE IF NOT EXISTS reviews(request_id TEXT PRIMARY KEY,receipt TEXT NOT NULL,sha256 TEXT NOT NULL);")
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
            return db.execute("SELECT revision FROM meta WHERE id=1").fetchone()[0], list(db.execute("SELECT * FROM attempts ORDER BY created_at DESC,request_id LIMIT ?", (MAX_ATTEMPTS,)))

    def _read(self, path):
        _allowed_path(path)
        with self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path, directory), "rb") as source:
                info = os.fstat(source.fileno())
                if info.st_size > MAX_ARTIFACT_BYTES:
                    raise ValueError("Description artifact exceeds its byte bound.")
                raw = source.read(MAX_ARTIFACT_BYTES+1)
                after = os.fstat(source.fileno())
                if len(raw) > MAX_ARTIFACT_BYTES or (info.st_ino,info.st_size,info.st_mtime_ns) != (after.st_ino,after.st_size,after.st_mtime_ns):
                    raise ValueError("Description artifact changed during verification.")
        return raw

    def _publish(self, path, raw):
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("Description artifact exceeds its byte bound.")
        with self.registry._directory(path.parent, create=True):
            publish_bytes(path, raw)

    def _usage(self, rows):
        if not self.root.exists():
            return 0
        ids = {row["request_id"] for row in rows}
        names = {"attempt.json", "receipt.json", "failure.json", "review.json"}
        total = entries = 0
        with self.registry._directory(self.root):
            for path in self.root.iterdir():
                entries += 1
                if entries > MAX_ATTEMPTS+4:
                    raise ValueError("Description inventory exceeds its entry bound.")
                _allowed_path(path)
                if path.name in {"ledger.sqlite3", "ledger.sqlite3-journal", "ledger.sqlite3-wal", "ledger.sqlite3-shm"}:
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_LEDGER_BYTES:
                        raise ValueError("Invalid description ledger file.")
                    continue
                if path.name not in ids or not path.is_dir():
                    raise ValueError("Unexpected retained description path.")
                with self.registry._directory(path):
                    children = 0
                    for child in path.iterdir():
                        children += 1
                        if children > 12:
                            raise ValueError("Description attempt inventory exceeds its bound.")
                        _allowed_path(child)
                        info = child.stat()
                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_ARTIFACT_BYTES
                                or (child.name not in names and not any(re.fullmatch(r"\."+re.escape(name)+r"\.box-[a-f0-9]{24}\.tmp", child.name) for name in names))):
                            raise ValueError("Unexpected retained description artifact.")
                        total += info.st_size
        return total

    def _interrupted(self, row):
        attempt = _load(row["receipt"].encode(), MAX_ARTIFACT_BYTES)
        expected = _sha(_bytes(attempt))
        path = self.root/row["request_id"]/"attempt.json"
        published = path.exists()
        if published and _sha(self._read(path)) != expected:
            raise ValueError("Interrupted description input metadata is invalid.")
        return {"schema_version":1, "request_id":row["request_id"], "digest":row["digest"],
                "status":"interrupted", "created_at":row["created_at"],
                "attempt_sha256":expected,"attempt_metadata_published":published,
                "error":"Accepted description has no active worker. It is held and is never resumed or replayed."}

    def _summary(self, row):
        try:
            summary = _load(row["receipt"].encode(), MAX_ARTIFACT_BYTES)
            if summary.get("request_id") != row["request_id"] or summary.get("digest") != row["digest"]:
                raise ValueError("Description ledger identity mismatch.")
            active = _ACTIVE.get((self.key,row["request_id"]))
            if row["status"] == "accepted":
                if active and not active["finished"]:
                    return {**summary,"status":"running","verified":False}
                failure = self._interrupted(row)
                return {**summary,"status":"interrupted","verified":False,"error":failure["error"],"failure_sha256":_sha(_bytes(failure)),"failure_reviewed":False}
            filename = "receipt.json" if row["status"] == "completed" else "failure.json"
            raw = self._read(self.root/row["request_id"]/filename)
            if _sha(raw) != row["receipt_sha256"] or raw.decode() != row["receipt"]:
                raise ValueError("Description receipt integrity failed.")
            if row["status"] == "completed":
                if summary.get("status") != "completed" or summary.get("image_retained") is not False or summary.get("action_authority") is not False:
                    raise ValueError("Invalid completed description receipt.")
                attempt = self._read(self.root/row["request_id"]/"attempt.json")
                if _sha(attempt) != summary["attempt_sha256"]:
                    raise ValueError("Description input metadata hash mismatch.")
                return {**summary,"verified":True,"historical":True,"live_available":False}
            attempt_path = self.root/row["request_id"]/"attempt.json"
            if summary.get("attempt_metadata_published") or attempt_path.exists():
                attempt = self._read(attempt_path)
                if _sha(attempt) != summary.get("attempt_sha256"):
                    raise ValueError("Description failure input metadata hash mismatch.")
            summary.update(status="failed",verified=False,failure_sha256=row["receipt_sha256"],failure_reviewed=False)
            if row["status"] == "reviewed":
                with self._database() as db:
                    review = db.execute("SELECT receipt,sha256 FROM reviews WHERE request_id=?", (row["request_id"],)).fetchone()
                if review is None:
                    raise ValueError("Missing durable description failure review.")
                raw = self._read(self.root/row["request_id"]/"review.json")
                if raw.decode() != review["receipt"] or _sha(raw) != review["sha256"]:
                    raise ValueError("Description failure review integrity failed.")
                data = _load(raw, MAX_ARTIFACT_BYTES)
                if data.get("failure_sha256") != row["receipt_sha256"] or data.get("request_id") != row["request_id"]:
                    raise ValueError("Description review refers to a different failure.")
                summary.update(status="reviewed",failure_reviewed=True,owner_review=data)
            return summary
        except (ValueError,OSError,KeyError,sqlite3.Error,UnicodeError) as error:
            return {"request_id":row["request_id"],"status":"invalid","verified":False,
                    "error":"Retained description evidence is invalid; inspect the private artifacts.","error_type":type(error).__name__}

    def status(self):
        with _LOCK:
            revision, rows = self._rows()
            paused, controls = self._controls()
            descriptions = [self._summary(row) for row in rows]
            private_storage = self._private_storage()
            if not private_storage:
                selected, identity = None, None
                configured, reason = False, "Private camera-caption storage must remain outside the agent Workspace. No configuration was changed."
            else:
                try:
                    selected = self.selected_model()
                    identity = self.model.vision_identity(selected)
                    configured, reason = True, None
                except (ValueError, ModelError, AttributeError):
                    selected, identity = None, None
                    configured, reason = False, "A downloaded vision model and numeric-loopback local endpoint are required."
            return {"schema_version":1,"implementation":IMPLEMENTATION,"revision":revision,"control_revision":controls,
                    "paused":paused,"hold":any(row["status"] in {"failed","interrupted","invalid"} for row in descriptions),
                    "descriptions":descriptions,"retained_bytes":self._usage(rows),"image_retention":False,
                    "automatic_generation":False,"limits":{"attempts":MAX_ATTEMPTS,"retained_bytes":MAX_RETAINED_BYTES,
                    "image_bytes":65536,"question_characters":2000,"answer_characters":4000,"model_deadline_seconds":60},
                    "configured":configured,"selected_model":selected,"reason":reason,"model_readiness":"verified_on_explicit_request",
                    "private_storage_outside_workspace":private_storage,
                    "recovery":"Review the exact retained failure explicitly before a new request. The failed ID is never retried."}

    def detail(self, request_id):
        _identifier(request_id,"request_id")
        with _LOCK:
            _, rows = self._rows()
            row = next((row for row in rows if row["request_id"] == request_id),None)
            if row is None:
                raise ValueError("Camera description was not found.")
            summary = self._summary(row)
            return {"receipt":summary,"description":summary if summary["status"] == "completed" and summary["verified"] else None}

    def _request(self, request):
        _fields(request,{"request_id","session_id","expected_control_revision","question"},{"expected_frame_id"},"camera description")
        _identifier(request["request_id"],"request_id")
        _identifier(request["session_id"],"session_id")
        revision = request["expected_control_revision"]
        if type(revision) is not int or not 0 <= revision <= 2**53-1:
            raise ValueError("expected_control_revision must be a nonnegative integer.")
        question = request["question"]
        if (type(question) is not str or not 1 <= len(question) <= 2000 or not question.strip()
                or any(ord(c)<32 and c not in "\n\t" for c in question)):
            raise ValueError("Ask a visual question of 1 to 2000 characters.")
        expected = request.get("expected_frame_id")
        if expected is not None and (type(expected) is not str or not _HASH.fullmatch(expected)):
            raise ValueError("expected_frame_id must be a 64-character lowercase frame hash.")
        return dict(request)

    def _frame(self, value):
        if type(value) is not dict:
            raise ValueError("The live preview did not provide a frozen frame.")
        image = value.get("jpeg")
        if (type(image) is not bytes or not 4 <= len(image) <= 65536 or not image.startswith(b"\xff\xd8")
                or not image.endswith(b"\xff\xd9") or _sha(image) != value.get("jpeg_sha256")):
            raise ValueError("The frozen camera JPEG failed its byte/hash contract.")
        for name in ("frame_id","jpeg_sha256","source_frame_sha256"):
            if type(value.get(name)) is not str or not _HASH.fullmatch(value[name]):
                raise ValueError("Invalid frozen camera-frame provenance.")
        fields = {"session_id","frame_id","jpeg_sha256","source_frame_sha256","sample_index","acquired_at",
                  "source_pts_ms","age_ms","control_revision","width","height","source_clock","driver_latency",
                  "timestamp_basis","source_lag_ms","source_age_ms","capture_elapsed_ms","clock_kind","retained"}
        source = {name:field for name,field in value.items() if name in fields}
        if (type(source.get("age_ms")) not in (int,float) or not 0 <= source["age_ms"] <= 3000
                or source.get("retained") is not False):
            raise VisionConflict("A fresh, volatile owner-preview frame is required.")
        for name in ("sample_index","control_revision","width","height"):
            maximum = 2**53-1 if name == "control_revision" else 241 if name == "sample_index" else 640 if name == "width" else 480
            minimum = 0 if name in {"sample_index","control_revision"} else 1
            if type(source.get(name)) is not int or not minimum <= source[name] <= maximum:
                raise ValueError("Invalid frozen-frame integer provenance.")
        if type(source.get("acquired_at")) is not str:
            raise ValueError("A software frame-arrival timestamp is required.")
        _date(source["acquired_at"],"frame acquired_at")
        for name in ("source_pts_ms","source_lag_ms","capture_elapsed_ms"):
            if name in source and (type(source[name]) not in (int,float) or not math.isfinite(source[name]) or not 0 <= source[name] <= 2**53-1):
                raise ValueError("Invalid frozen-frame clock provenance.")
        for name in ("source_clock","driver_latency","timestamp_basis"):
            if name in source and (type(source[name]) is not str or len(source[name])>200):
                raise ValueError("Frozen-frame clock labels exceed their bound.")
        if "clock_kind" in source and (type(source["clock_kind"]) is not str or len(source["clock_kind"])>200):
            raise ValueError("Frozen-frame clock label exceeds its bound.")
        if "source_age_ms" in source and (type(source["source_age_ms"]) not in (int,float)
                or not math.isfinite(source["source_age_ms"]) or not -2000<=source["source_age_ms"]<=3000):
            raise ValueError("Invalid frozen-frame source age.")
        source.update(image_retained=False, frozen_at_request=True,
                      freshness_basis="software_arrival_and_source_pts; physical_driver_latency_unverified")
        if len(_bytes(source))>8192:
            raise ValueError("Frozen-frame metadata exceeds its byte bound.")
        return image, source

    def start(self, request):
        request = self._request(request)
        if not self._private_storage():
            raise VisionConflict("Private camera-caption storage overlaps the agent Workspace; no description was accepted.")
        digest = _sha(_bytes(request))
        with _LOCK:
            revision, rows = self._rows()
            previous = next((row for row in rows if row["request_id"] == request["request_id"]),None)
            if previous is not None:
                if previous["digest"] != digest:
                    raise VisionConflict("Description request ID was already bound to different inputs.")
                summary = self._summary(previous)
                if summary["status"] in {"completed","running"} and (summary["status"] == "running" or summary["verified"]):
                    return summary
                raise VisionConflict("This description ID is failed, interrupted, reviewed or invalid and cannot run again.")
            summaries = [self._summary(row) for row in rows]
            if self.closed.is_set():
                raise VisionConflict("Description service is stopping; no new work was accepted.")
            if any(row["status"] in {"failed","interrupted","invalid"} for row in summaries):
                raise VisionConflict("A retained camera-description failure requires explicit exact-hash review.")
            if any(row["status"] == "running" for row in summaries):
                raise VisionConflict("A camera description is already running; no second request was queued.")
            if len(rows) >= MAX_ATTEMPTS or self._usage(rows)+RESERVED_JOB_BYTES > MAX_RETAINED_BYTES:
                raise VisionConflict("Description history or storage quota is full; no work was accepted.")
            paused, controls = self._controls()
            if paused or controls != request["expected_control_revision"]:
                raise VisionConflict("Camera-description owner controls changed before dispatch.")
            selected = self.selected_model()
            identity = self.model.vision_identity(selected)
            image, source = self._frame(self.vision.frame_for_description(request["session_id"], request.get("expected_frame_id")))
            if source.get("session_id") != request["session_id"] or source.get("control_revision") != controls:
                raise VisionConflict("Frozen camera frame belongs to a different session or owner revision.")
            created = _stamp(datetime.now(UTC))
            attempt = {"schema_version":1,"implementation":IMPLEMENTATION,"request_id":request["request_id"],
                       "digest":digest,"status":"accepted","created_at":created,"question":request["question"],
                       "session_id":request["session_id"],"control_revision":controls,"model":selected,
                       "provider":identity["provider"],"source":source,"image_retained":False,"action_authority":False}
            with self.vision.description_source_guard(request["session_id"], controls), self._database(True) as db:
                if db.execute("SELECT revision FROM meta WHERE id=1").fetchone()[0] != revision:
                    raise VisionConflict("Description revision changed before reservation.")
                db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?)",(request["request_id"],digest,"accepted",created,_bytes(attempt).decode(),None,None))
                db.execute("UPDATE meta SET revision=revision+1 WHERE id=1")
            active = {"finished":False,"stop":threading.Event()}
            _ACTIVE[(self.key,request["request_id"])] = active
            thread = threading.Thread(target=self._execute,args=(request,attempt,image,active),name="description-"+request["request_id"],daemon=True)
            active["thread"] = thread
            thread.start()
            return {**attempt,"status":"running","verified":False}

    def _execute(self, request, attempt, image, active):
        request_id = request["request_id"]
        folder = self.root/request_id
        stage = "input_metadata"
        metadata_published = False
        def revoked():
            return (active["stop"].is_set() or self.closed.is_set() or self.selected_model() != attempt["model"]
                    or not self.vision.description_source_valid(request["session_id"],attempt["control_revision"]))
        try:
            self._publish(folder/"attempt.json",_bytes(attempt))
            metadata_published = True
            if revoked():
                raise VisionConflict("Camera description source authority was revoked before generation.")
            stage = "local_model"
            answer, usage = self.model.describe_image(attempt["model"],request["question"],image,cancelled=revoked)
            if (type(answer) is not str or not answer.strip() or len(answer)>4000 or type(usage) is not dict
                    or usage.get("profile") != "vision_description" or usage.get("generation_requests") != 1
                    or usage.get("image_sha256") != attempt["source"]["jpeg_sha256"]):
                raise ModelError("Local camera description violated its bounded source/usage contract.")
            usage_fields = {"profile","model","model_digest","image_sha256","request_sha256","prompt_tokens","output_tokens",
                            "generation_requests","metadata_requests","duration_ms","load_ms","prefill_ms","decode_ms"}
            if set(usage)-usage_fields or len(_bytes(usage))>8192:
                raise ModelError("Local camera description usage exceeds its metadata contract.")
            stage = "publication"
            receipt = {**attempt,"status":"completed","completed_at":_stamp(datetime.now(UTC)),"verified":True,
                       "attempt_sha256":_sha(_bytes(attempt)),"answer":answer,"usage":usage,
                       "historical":True,"live_available":False,
                       "source_warning":"This answer describes the frozen frame at its recorded software arrival time; it does not prove the current scene is unchanged."}
            raw = _bytes(receipt)
            with _LOCK, self.vision.description_source_guard(request["session_id"],attempt["control_revision"]):
                if revoked():
                    raise VisionConflict("Description owner controls or selected model changed before publication.")
                with self._database(True) as db:
                    row = db.execute("SELECT status,digest FROM attempts WHERE request_id=?",(request_id,)).fetchone()
                    if row is None or row["status"] != "accepted" or row["digest"] != attempt["digest"]:
                        raise VisionConflict("Description ledger changed before publication.")
                    self._publish(folder/"receipt.json",raw)
                    db.execute("UPDATE attempts SET status='completed',receipt=?,receipt_sha256=? WHERE request_id=?",(raw.decode(),_sha(raw),request_id))
                    db.execute("UPDATE meta SET revision=revision+1 WHERE id=1")
        except BaseException as error:
            message = str(error)[:300] if isinstance(error,(ModelError,VisionConflict)) else "Camera description failed during "+stage+"; inspect the private receipt."
            failure = {"schema_version":1,"request_id":request_id,"digest":attempt["digest"],"status":"failed",
                       "created_at":attempt["created_at"],"failed_at":_stamp(datetime.now(UTC)),"stage":stage,
                       "attempt_sha256":_sha(_bytes(attempt)),"attempt_metadata_published":metadata_published,
                       "error":message,"error_type":type(error).__name__,"image_retained":False,"retry_permitted":False}
            try:
                raw = _bytes(failure)
                with _LOCK, self._database(True) as db:
                    self._publish(folder/"failure.json",raw)
                    db.execute("UPDATE attempts SET status='failed',receipt=?,receipt_sha256=?,error=? WHERE request_id=? AND status='accepted'",(raw.decode(),_sha(raw),message,request_id))
                    db.execute("UPDATE meta SET revision=revision+1 WHERE id=1")
            except BaseException:
                pass  # The accepted ledger remains an interrupted/uncertain hold.
        finally:
            image = None
            with _LOCK:
                active["finished"] = True

    def acknowledge_failure(self, request):
        _fields(request,{"request_id","expected_revision","expected_control_revision","failure_sha256"},label="description failure review")
        _identifier(request["request_id"],"request_id")
        for name in ("expected_revision","expected_control_revision"):
            if type(request[name]) is not int or not 0 <= request[name] <= 2**53-1:
                raise ValueError(name+" must be a nonnegative integer.")
        if type(request["failure_sha256"]) is not str or not _HASH.fullmatch(request["failure_sha256"]):
            raise ValueError("Use the exact retained description failure hash.")
        with _LOCK:
            revision, rows = self._rows()
            if revision != request["expected_revision"]:
                raise VisionConflict("Description revision changed before failure review.")
            row = next((row for row in rows if row["request_id"]==request["request_id"]),None)
            if row is None:
                raise ValueError("Description failure was not found.")
            summary = self._summary(row)
            if summary["status"] not in {"failed","interrupted"} or summary["failure_sha256"] != request["failure_sha256"]:
                raise VisionConflict("Review must identify the exact current failed or interrupted description.")
            active = _ACTIVE.get((self.key,request["request_id"]))
            if active and not active["finished"]:
                raise VisionConflict("The accepted description must finish before failure review.")
            with self.vision._owner_guard(request["expected_control_revision"]), self._database(True) as db:
                if db.execute("SELECT revision FROM meta WHERE id=1").fetchone()[0] != revision:
                    raise VisionConflict("Description revision changed during failure review.")
                if row["status"] == "accepted":
                    failure_raw = _bytes(self._interrupted(row))
                    if _sha(failure_raw) != request["failure_sha256"]:
                        raise VisionConflict("Interrupted description metadata changed during failure review.")
                    self._publish(self.root/request["request_id"]/"failure.json",failure_raw)
                    db.execute("UPDATE attempts SET receipt=?,receipt_sha256=? WHERE request_id=?",(failure_raw.decode(),_sha(failure_raw),request["request_id"]))
                review = {"schema_version":1,"request_id":request["request_id"],"failure_sha256":request["failure_sha256"],
                          "reviewed_at":_stamp(datetime.now(UTC)),"retry_permitted":False,"control_revision":request["expected_control_revision"]}
                raw = _bytes(review)
                self._publish(self.root/request["request_id"]/"review.json",raw)
                db.execute("INSERT INTO reviews VALUES(?,?,?)",(request["request_id"],raw.decode(),_sha(raw)))
                db.execute("UPDATE attempts SET status='reviewed' WHERE request_id=?",(request["request_id"],))
                db.execute("UPDATE meta SET revision=revision+1 WHERE id=1")
            return {**review,"status":"reviewed","review_sha256":_sha(raw)}

    def close(self):
        self.closed.set()
        with _LOCK:
            for key,active in _ACTIVE.items():
                if key[0] == self.key and not active["finished"]:
                    active["stop"].set()
