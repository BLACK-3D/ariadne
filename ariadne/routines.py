"""Durable zero-model routines over installed fixed local report procedures.

The existing engine supplies the clock tick. Each tick publishes at most one
child report, with a write-ahead marker and a conservative lifetime disk budget.
An interrupted step is never adopted or retried after a process restart.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import threading
import time
import uuid
from contextlib import contextmanager

from .config import _allowed_path
from .locking import InstanceLock
from .procedures import MAX_TOTAL_BYTES

MAX_DEFINITIONS = 32
MAX_STEPS = 4
MAX_QUEUE = 8
MAX_RUNS = 1000
REPORT_BUDGET_BYTES = 256 * 1024 * 1024
STEP_RESERVATION_BYTES = MAX_TOTAL_BYTES
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ACTIVE = {"queued", "running"}
_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_TEMPLATES = (
    {"id": "household_continuity", "name": "Household continuity", "description": "Review recorded reserves, rotation, power assumptions and project dependencies.",
     "steps": ["readiness_report", "stock_rotation", "power_budget", "rebuilding_plan"]},
    {"id": "business_handoff", "name": "Business handoff", "description": "Create a dated local inventory and trial-balance snapshot without ledger changes.", "steps": ["business_snapshot"]},
    {"id": "knowledge_continuity", "name": "Knowledge continuity", "description": "Review imported metadata, reference integrity and offline readiness.",
     "steps": ["knowledge_inventory", "reference_audit", "offline_checklist"]},
)


class RoutineConflict(ValueError):
    def __init__(self, request_id, status, message):
        self.request_id, self.status = request_id, status
        super().__init__(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("Request IDs require 1–64 letters, digits, underscores or hyphens.")
    return value


class Routines:
    def __init__(self, procedures, store):
        self.procedures, self.store = procedures, store
        self.lock = threading.RLock()
        self.lock_path = _allowed_path(store.path.parent/"routines.lock")
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS routine_definitions(
                    id TEXT PRIMARY KEY,name TEXT NOT NULL,steps TEXT NOT NULL,
                    interval_minutes INTEGER NOT NULL,enabled INTEGER NOT NULL,next_run REAL,
                    last_run_id TEXT,last_error TEXT,revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS routine_runs(
                    id TEXT PRIMARY KEY,routine_id TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,
                    created_at REAL NOT NULL,completed_at REAL,updated_at REAL NOT NULL,
                    plan TEXT NOT NULL,steps TEXT NOT NULL,artifacts TEXT NOT NULL,warnings TEXT NOT NULL,
                    error TEXT,trigger TEXT NOT NULL,cancel_requested INTEGER NOT NULL DEFAULT 0,
                    reserved_bytes INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS routine_run_status ON routine_runs(status,created_at);
                CREATE TABLE IF NOT EXISTS routine_meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,updated_at REAL NOT NULL);
                INSERT OR IGNORE INTO routine_meta VALUES(1,0,0);
            """)

    @contextmanager
    def database(self):
        with self.lock, self.procedures.registry._directory(self.store.path.parent):
            for suffix in ("", "-journal", "-wal", "-shm"):
                path = _allowed_path(str(self.store.path)+suffix)
                if path.exists():
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Routine database files must be regular files with one link.")
            with self.store.connect() as db:
                db.execute("PRAGMA trusted_schema=OFF")
                deadline = time.monotonic()+5
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
                yield db

    @staticmethod
    def _bump(db):
        db.execute("UPDATE routine_meta SET revision=revision+1,updated_at=? WHERE id=1", (time.time(),))

    @staticmethod
    def _definition(row):
        if row is None:
            raise KeyError("Routine not found")
        value = dict(row)
        value.update(steps=json.loads(value["steps"]), enabled=bool(value["enabled"]))
        return value

    @staticmethod
    def _run(row):
        if row is None:
            raise KeyError("Routine run not found")
        value = dict(row)
        for name in ("plan", "steps", "artifacts", "warnings"):
            value[name] = json.loads(value[name])
        value.update(model_calls=0, cancel_requested=bool(value["cancel_requested"]))
        return value

    @staticmethod
    def _paused(db):
        row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return bool(json.loads(row[0])) if row else False

    def catalog(self):
        return {"templates": json.loads(_json(_TEMPLATES)), "procedures": self.procedures.catalog()["procedures"],
            "model_calls": 0, "limits": {"definitions": MAX_DEFINITIONS, "steps": MAX_STEPS, "queue": MAX_QUEUE,
                "runs": MAX_RUNS, "report_budget_bytes": REPORT_BUDGET_BYTES, "reserved_bytes_per_step": STEP_RESERVATION_BYTES},
            "scope": "Fixed local reports only; no AI missions, internet, installations, accounts or equipment control.",
            "recovery": "Restarting interrupts every started unfinished routine and disables recurrence. Never-started queued requests remain queued.",
            "edits": "Each run retains its immutable plan. Disabling a definition cancels its scheduled remaining work at the next boundary; manual runs are independent.",
            "storage": "In-flight, failed and interrupted children retain worst-case reservations. Verified successes are charged their actual artifact bytes. Quotas stop further reports; no owner files are deleted automatically."}

    def definitions(self):
        with self.database() as db:
            return [self._definition(row) for row in db.execute("SELECT * FROM routine_definitions ORDER BY name,id")]

    def status(self):
        with self.database() as db:
            count = db.execute("SELECT count(*) FROM routine_runs WHERE status IN ('queued','running')").fetchone()[0]
            running = db.execute("SELECT 1 FROM routine_runs WHERE status='running' LIMIT 1").fetchone()
            meta = db.execute("SELECT * FROM routine_meta WHERE id=1").fetchone()
            reserved = db.execute("SELECT coalesce(sum(reserved_bytes),0) FROM routine_runs").fetchone()[0]
        return {"active_runs": count, "running": bool(running), "revision": meta["revision"],
                "last_updated_at": meta["updated_at"], "reserved_bytes": reserved, "accounted_bytes": reserved,
                "report_budget_bytes": REPORT_BUDGET_BYTES, "model_calls": 0}

    def save(self, name, steps, interval_minutes=1440, enabled=False, routine_id=None, *, expected_revision=None):
        if type(name) is not str or not 1 <= len(name.strip()) <= 120 or any(ord(char) < 32 for char in name):
            raise ValueError("Routine name needs 1–120 printable characters.")
        allowed = {row["id"] for row in self.procedures.catalog()["procedures"]}
        if type(steps) is not list or not 1 <= len(steps) <= MAX_STEPS or any(type(step) is not str or step not in allowed for step in steps) or len(set(steps)) != len(steps):
            raise ValueError("Choose 1–4 different installed fixed procedures.")
        if type(interval_minutes) is not int or not 5 <= interval_minutes <= 525600:
            raise ValueError("Routine intervals must be 5–525,600 whole minutes.")
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean.")
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise ValueError("expected_revision must be a non-negative integer.")
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = None
            if routine_id is not None:
                previous = self._definition(db.execute("SELECT * FROM routine_definitions WHERE id=?", (_identifier(routine_id),)).fetchone())
            elif db.execute("SELECT count(*) FROM routine_definitions").fetchone()[0] >= MAX_DEFINITIONS:
                raise ValueError("At most 32 local routines may be configured.")
            if expected_revision is not None and (previous is None or previous["revision"] != expected_revision):
                raise RoutineConflict(routine_id, "conflict", "Routine settings changed. Refresh and review the current state before saving or enabling recurrence.")
            routine_id = routine_id or uuid.uuid4().hex
            next_run = (previous["next_run"] if previous and previous["enabled"] and previous["interval_minutes"] == interval_minutes
                        else time.time()+interval_minutes*60) if enabled else None
            revision = previous["revision"]+1 if previous else 1
            db.execute("INSERT OR REPLACE INTO routine_definitions VALUES(?,?,?,?,?,?,?,?,?)",
                (routine_id, name.strip(), _json(steps), interval_minutes, int(enabled), next_run,
                 previous["last_run_id"] if previous else None, None, revision))
            if not enabled:
                for row in db.execute("SELECT * FROM routine_runs WHERE routine_id=? AND trigger='scheduled' AND status IN ('queued','running')", (routine_id,)).fetchall():
                    run = self._run(row)
                    self._request_cancel(db, run, "Recurrence disabled; remaining scheduled steps were cancelled.")
            self._bump(db)
            return self._definition(db.execute("SELECT * FROM routine_definitions WHERE id=?", (routine_id,)).fetchone())

    def delete(self, routine_id):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._definition(db.execute("SELECT * FROM routine_definitions WHERE id=?", (routine_id,)).fetchone())
            row = db.execute("SELECT id,status FROM routine_runs WHERE routine_id=? AND status IN ('queued','running') LIMIT 1", (routine_id,)).fetchone()
            if row:
                raise RoutineConflict(row["id"], row["status"], "Cancel the active routine run before deleting its definition.")
            db.execute("DELETE FROM routine_definitions WHERE id=?", (routine_id,))
            self._bump(db)

    def _plan(self, definition):
        outputs = {row["id"]: row["outputs"] for row in self.procedures.catalog()["procedures"]}
        return {"routine_id": definition["id"], "name": definition["name"], "revision": definition["revision"],
                "steps": list(definition["steps"]), "outputs": {step: outputs[step] for step in definition["steps"]},
                "interval_minutes": definition["interval_minutes"], "model_calls": 0}

    def _enqueue(self, db, definition, request_id, trigger):
        plan = self._plan(definition)
        previous = db.execute("SELECT * FROM routine_runs WHERE id=?", (request_id,)).fetchone()
        if previous:
            run = self._run(previous)
            if {key: value for key, value in run["plan"].items() if key != "revision"} != {key: value for key, value in plan.items() if key != "revision"}:
                raise RoutineConflict(request_id, run["status"], "This request ID belongs to a different frozen routine plan.")
            return run
        active = db.execute("SELECT id,status FROM routine_runs WHERE routine_id=? AND status IN ('queued','running') LIMIT 1", (definition["id"],)).fetchone()
        if active:
            raise RoutineConflict(active["id"], active["status"], "This routine already has an active invocation.")
        if db.execute("SELECT count(*) FROM routine_runs WHERE status IN ('queued','running')").fetchone()[0] >= MAX_QUEUE:
            raise ValueError("The local routine queue is full (8).")
        if db.execute("SELECT count(*) FROM routine_runs").fetchone()[0] >= MAX_RUNS:
            raise ValueError("Routine history is full (1,000). Inspect and archive owner reports before continuing.")
        steps = [{"procedure_id": step, "request_id": "routine-"+hashlib.sha256((request_id+":"+str(index)+":"+step).encode()).hexdigest()[:48], "status": "pending"}
                 for index, step in enumerate(plan["steps"])]
        now = time.time()
        db.execute("INSERT INTO routine_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (request_id, definition["id"], definition["name"],
            "queued", now, None, now, _json(plan), _json(steps), "[]", "[]", None, trigger, 0, 0))
        db.execute("UPDATE routine_definitions SET last_run_id=? WHERE id=?", (request_id, definition["id"]))
        self._bump(db)
        return self._run(db.execute("SELECT * FROM routine_runs WHERE id=?", (request_id,)).fetchone())

    def enqueue(self, routine_id, request_id):
        _identifier(request_id)
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            definition = self._definition(db.execute("SELECT * FROM routine_definitions WHERE id=?", (routine_id,)).fetchone())
            return self._enqueue(db, definition, request_id, "manual")

    def due(self):
        count = 0
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._paused(db):
                return 0
            now = time.time()
            for row in db.execute("SELECT * FROM routine_definitions WHERE enabled=1 AND next_run<=? ORDER BY next_run,id", (now,)).fetchall():
                definition = self._definition(row)
                if db.execute("SELECT count(*) FROM routine_runs").fetchone()[0] >= MAX_RUNS:
                    self._disable(db, definition["id"], "Routine history is full; inspect and archive reviewed records before explicitly enabling recurrence.")
                    self._bump(db)
                    continue
                if db.execute("SELECT 1 FROM routine_runs WHERE routine_id=? AND status IN ('queued','running')", (definition["id"],)).fetchone():
                    continue
                request_id = "scheduled-"+definition["id"]+"-"+str(int(definition["next_run"]*1000))
                try:
                    self._enqueue(db, definition, request_id, "scheduled")
                except ValueError:
                    break
                db.execute("UPDATE routine_definitions SET next_run=? WHERE id=?", (now+definition["interval_minutes"]*60, definition["id"]))
                count += 1
        return count

    def runs(self):
        with self.database() as db:
            return [self._run(row) for row in db.execute("SELECT * FROM routine_runs WHERE id IN (SELECT id FROM routine_runs ORDER BY created_at DESC,id LIMIT 100) OR status IN ('queued','running') ORDER BY created_at DESC,id")]

    def detail(self, request_id):
        with self.database() as db:
            return self._run(db.execute("SELECT * FROM routine_runs WHERE id=?", (request_id,)).fetchone())

    def latest_completed_id(self):
        with self.database() as db:
            row = db.execute("SELECT id FROM routine_runs WHERE status='completed' ORDER BY created_at DESC,id LIMIT 1").fetchone()
        return row["id"] if row else None

    def compare(self, current_id, previous_id=None):
        from .routine_changes import compare_routines
        return compare_routines(self, current_id, previous_id)

    def _update(self, db, run):
        run["updated_at"] = time.time()
        db.execute("UPDATE routine_runs SET status=?,completed_at=?,updated_at=?,steps=?,artifacts=?,warnings=?,error=?,cancel_requested=?,reserved_bytes=? WHERE id=?",
            (run["status"], run["completed_at"], run["updated_at"], _json(run["steps"]), _json(run["artifacts"]), _json(run["warnings"]),
             run.get("error"), int(run["cancel_requested"]), run["reserved_bytes"], run["id"]))
        self._bump(db)

    @staticmethod
    def _disable(db, routine_id, reason):
        db.execute("UPDATE routine_definitions SET enabled=0,next_run=NULL,last_error=?,revision=revision+1 WHERE id=?", (reason[:500], routine_id))

    def _finish(self, db, run, status, error=None):
        run.update(status=status, completed_at=time.time(), error=error)
        for step in run["steps"]:
            if step["status"] in {"pending", "running"}:
                step["status"] = "cancelled" if status == "cancelled" else ("interrupted" if step["status"] == "running" else "pending")
        if status in {"failed", "cancelled", "interrupted"}:
            self._disable(db, run["routine_id"], error or "Routine stopped; inspect history before explicitly enabling recurrence.")
        self._update(db, run)

    def _request_cancel(self, db, run, reason):
        run["cancel_requested"] = True
        if run["status"] == "queued":
            self._finish(db, run, "cancelled", reason)
        else:
            run["error"] = reason
            self._disable(db, run["routine_id"], reason)
            self._update(db, run)

    def cancel(self, request_id):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db.execute("SELECT * FROM routine_runs WHERE id=?", (request_id,)).fetchone())
            if run["status"] in _ACTIVE:
                self._request_cancel(db, run, "Owner cancelled the routine; an executing child can finish, but no further step will start.")
        return self.detail(request_id)

    def _verify_step(self, run, step):
        receipt = step.get("receipt")
        if type(receipt) is not dict or receipt.get("status") != "completed" or receipt.get("ok") is not True or type(receipt.get("model_calls")) is not int or receipt.get("model_calls") != 0:
            raise ValueError("Completed child evidence is missing or invalid.")
        if receipt.get("request_id") != step["request_id"] or receipt.get("procedure_id") != step["procedure_id"]:
            raise ValueError("Child receipt does not match the frozen routine step.")
        expected = {"procedures/"+step["request_id"]+"/"+name for name in run["plan"]["outputs"][step["procedure_id"]]}
        artifacts = receipt.get("artifacts")
        if type(artifacts) is not list or len(artifacts) != len(expected) or any(type(row) is not dict for row in artifacts):
            raise ValueError("Child receipt does not contain the complete fixed output set.")
        if any(type(row.get("path")) is not str or type(row.get("bytes")) is not int or not 0 <= row["bytes"] <= 4_000_000
               or type(row.get("sha256")) is not str or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) for row in artifacts):
            raise ValueError("Child receipt contains invalid artifact evidence.")
        if {row["path"] for row in artifacts} != expected or sum(row["bytes"] for row in artifacts) > STEP_RESERVATION_BYTES:
            raise ValueError("Child receipt does not contain the complete fixed output set.")
        self.procedures._verify_receipt(receipt)

    def verify(self, request_id):
        run = self.detail(request_id)
        if run["status"] != "completed":
            raise RoutineConflict(request_id, run["status"], "Only a completed routine can be explicitly verified.")
        try:
            for step in run["steps"]:
                if step["status"] != "completed":
                    raise ValueError("Completed routine has an incomplete step.")
                self._verify_step(run, step)
        except (OSError, ValueError, KeyError, TypeError) as error:
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                self._disable(db, run["routine_id"], "Stored routine artifacts failed explicit integrity review; inspect the original generation receipt.")
                self._bump(db)
            raise ValueError("Routine artifact integrity failed; recurrence disabled. "+str(error)[:250]) from error
        return {**run, "artifacts_verified": True, "verified_at": time.time()}

    @contextmanager
    def _execution(self):
        owner = InstanceLock(self.lock_path)
        try:
            owner.acquire()
        except ValueError as error:
            if str(error) != "Another Ariadne worker already owns this data directory":
                raise
            yield False
            return
        try:
            yield True
        finally:
            owner.release()

    def recover(self):
        with self._execution() as acquired:
            if not acquired:
                return 0
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                count = 0
                for row in db.execute("SELECT * FROM routine_runs WHERE status IN ('queued','running')").fetchall():
                    run = self._run(row)
                    if run["status"] == "running" or any(step["status"] != "pending" for step in run["steps"]):
                        self._finish(db, run, "interrupted", "Routine execution was interrupted. Child outputs may exist; inspect them before starting a new request.")
                        count += 1
                return count

    def _child_failure_receipt(self, request_id):
        try:
            return next((row for row in self.procedures.history()["runs"] if row["request_id"] == request_id), None)
        except (OSError, ValueError, KeyError):
            return None

    def tick(self):
        if self.store.setting("paused", False):
            return False
        with self._execution() as acquired:
            if not acquired:
                return False
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                if self._paused(db):
                    return False
                row = db.execute("SELECT * FROM routine_runs WHERE status IN ('queued','running') ORDER BY created_at,id LIMIT 1").fetchone()
                if row is None:
                    return False
                run = self._run(row)
                if run["status"] == "running" or any(step["status"] == "running" for step in run["steps"]):
                    self._finish(db, run, "interrupted", "A previous child start lacks a durable completion; it will not be replayed.")
                    return True
                definition = db.execute("SELECT enabled FROM routine_definitions WHERE id=?", (run["routine_id"],)).fetchone()
                if run["cancel_requested"] or run["trigger"] == "scheduled" and (not definition or not definition["enabled"]):
                    self._finish(db, run, "cancelled", "Recurrence was disabled before the next child started.")
                    return True
                try:
                    for previous in run["steps"]:
                        if previous["status"] == "completed":
                            self._verify_step(run, previous)
                except (OSError, ValueError, KeyError) as error:
                    self._finish(db, run, "failed", "Earlier child artifacts failed integrity verification: "+str(error)[:250])
                    return True
                used = db.execute("SELECT coalesce(sum(reserved_bytes),0) FROM routine_runs").fetchone()[0]
                if used+STEP_RESERVATION_BYTES > REPORT_BUDGET_BYTES:
                    self._finish(db, run, "failed", "The conservative routine report budget is exhausted; inspect stored reports. No files were deleted.")
                    return True
                step = next((step for step in run["steps"] if step["status"] == "pending"), None)
                if step is None:
                    self._finish(db, run, "failed", "Routine has no runnable pending step; inspect its durable history.")
                    return True
                step.update(status="running", started_at=time.time(), accounted_bytes=STEP_RESERVATION_BYTES)
                run.update(status="running", reserved_bytes=run["reserved_bytes"]+STEP_RESERVATION_BYTES)
                self._update(db, run)
            # Cancellation and pause have a final boundary before the fixed call.
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                fresh = self._run(db.execute("SELECT * FROM routine_runs WHERE id=?", (run["id"],)).fetchone())
                if fresh["cancel_requested"] or self._paused(db):
                    fresh["reserved_bytes"] -= STEP_RESERVATION_BYTES  # The child was provably not invoked.
                    selected = next(item for item in fresh["steps"] if item["request_id"] == step["request_id"])
                    selected["status"] = "pending"
                    selected["accounted_bytes"] = 0
                    if fresh["cancel_requested"]:
                        self._finish(db, fresh, "cancelled", "Routine cancelled before the child started.")
                    else:
                        fresh["status"] = "queued"
                        self._update(db, fresh)
                    return True
            try:
                receipt = self.procedures.run(step["procedure_id"], step["request_id"])
                step.update(status="completed", receipt=receipt, completed_at=time.time())
                self._verify_step(run, step)
                error = None
            except BaseException as failure:
                # Leave the write-ahead running marker untouched on process-level
                # interruption. Recovery will stop, never adopt/replay the child.
                if isinstance(failure, (KeyboardInterrupt, SystemExit)):
                    raise
                receipt = self._child_failure_receipt(step["request_id"])
                step.update(status="failed", completed_at=time.time())
                if receipt:
                    step["receipt"] = receipt
                error = "Fixed routine step failed; inspect its receipt and any partial output. "+str(failure)[:250]
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self._run(db.execute("SELECT * FROM routine_runs WHERE id=?", (run["id"],)).fetchone())
                current["steps"] = [step if item["request_id"] == step["request_id"] else item for item in current["steps"]]
                current["artifacts"] = [dict(artifact) for item in current["steps"] for artifact in item.get("receipt", {}).get("artifacts", [])]
                current["warnings"] = list(dict.fromkeys(str(warning)[:1000] for item in current["steps"] for warning in item.get("receipt", {}).get("warnings", [])))[:100]
                if not error:
                    actual_bytes = sum(artifact["bytes"] for artifact in receipt["artifacts"])
                    step["accounted_bytes"] = actual_bytes
                    current["reserved_bytes"] += actual_bytes-STEP_RESERVATION_BYTES
                if error:
                    self._finish(db, current, "failed", error)
                elif current["cancel_requested"]:
                    self._finish(db, current, "cancelled", "Routine cancelled at a completed child boundary; no further step will start.")
                elif all(item["status"] == "completed" for item in current["steps"]):
                    self._finish(db, current, "completed")
                else:
                    current["status"] = "queued"
                    self._update(db, current)
            return True
