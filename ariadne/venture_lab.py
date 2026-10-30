"""Protected, zero-model venture progress: local drafts, never external actions."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
import uuid

from .atomic import publish_bytes
from .config import _allowed_path
from .locking import InstanceLock

UTC = timezone.utc
MAX_SOURCE = 256 * 1024
MAX_INPUT_TOTAL = 16 * 1024 * 1024
MAX_INPUTS = 512
MAX_HISTORY = 1000
MAX_OUTPUT = 128 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_KINDS = {"business_offer_pack", "business_validation_pack", "etf_review_pack"}
_RUN_LOCK = threading.RLock()


class VentureConflict(ValueError):
    def __init__(self, message, *, revision=None):
        self.revision = revision
        super().__init__(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _load(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Duplicate venture JSON keys are not accepted.")
            result[key] = value
        return result
    def finite(value):
        if len(value) > 128:
            raise ValueError("Venture JSON numeric literal is too long.")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Venture JSON numbers must be finite.")
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_float=finite,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite venture JSON.")))


def _text(value, maximum):
    if type(value) is not str or not 1 <= len(value.strip()) <= maximum or "\x00" in value:
        raise ValueError("Use bounded nonempty venture text.")
    return value.strip()


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("Venture IDs require 1–64 letters, digits, underscores or hyphens.")
    return value


def _now(value=None):
    value = value or datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Venture timestamps require a timezone.")
    return value.astimezone(UTC)


def _md(value):
    return html.escape(str(value), quote=False).replace("[", "\\[").replace("]", "\\]").replace("`", "'").replace("|", "\\|")


def _default():
    return {"schema": 1, "revision": 0, "enabled": False, "paused": False,
            "max_drafts_per_day": 2, "projects": [], "tasks": []}


class VentureLab:
    def __init__(self, registry, store):
        self.registry, self.store = registry, store
        self.lock = threading.RLock()
        self.lock_path = _allowed_path(store.path.parent / "venture-lab.lock")
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS venture_settings(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,value TEXT NOT NULL,hold_request TEXT,hold_reason TEXT);
                CREATE TABLE IF NOT EXISTS venture_inputs(digest TEXT PRIMARY KEY,bytes INTEGER NOT NULL,content BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS venture_runs(request_id TEXT PRIMARY KEY,local_day TEXT NOT NULL UNIQUE,status TEXT NOT NULL,created REAL NOT NULL,snapshot TEXT NOT NULL,receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS venture_task_progress(task_id TEXT PRIMARY KEY,task_digest TEXT NOT NULL,status TEXT NOT NULL,request_id TEXT NOT NULL,receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS venture_evidence(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,kind TEXT NOT NULL,observed_at TEXT NOT NULL,note TEXT NOT NULL,source_digest TEXT,created REAL NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO venture_settings VALUES(1,0,?,NULL,NULL)", (_json(_default()),))

    @contextmanager
    def database(self):
        with self.lock, self.registry._directory(self.store.path.parent):
            for suffix in ("", "-journal", "-wal", "-shm"):
                path = _allowed_path(str(self.store.path)+suffix)
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    if suffix:
                        continue
                    raise ValueError("The initialized venture main database is missing.") from None
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Venture database files must be regular, single-link, non-reparse files.")
            with self.store.connect(require_existing=True) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                deadline = time.monotonic()+5
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                yield db

    @staticmethod
    def _settings(db):
        row = db.execute("SELECT * FROM venture_settings WHERE id=1").fetchone()
        return row, _load(row["value"])

    @staticmethod
    def _revision(row, expected):
        if type(expected) is not int or expected < 0 or expected != row["revision"]:
            raise VentureConflict("Venture settings changed; refresh before saving.", revision=row["revision"])

    @staticmethod
    def _global_pause(db):
        row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return bool(_load(row[0])) if row else False

    def _read(self, relative):
        path = self.registry._path(relative)
        with self.registry._directory(path.parent) as descriptor:
            with os.fdopen(self.registry._open_read(path, descriptor), "rb") as source:
                before = os.fstat(source.fileno())
                raw = source.read(MAX_SOURCE+1)
                after = os.fstat(source.fileno())
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if len(raw) > MAX_SOURCE or identity(before) != identity(after) or len(raw) != before.st_size:
            raise ValueError("Venture source is oversized or changed while being accepted.")
        raw.decode("utf-8-sig")
        return raw

    @staticmethod
    def _put(db, raw):
        digest = hashlib.sha256(raw).hexdigest()
        old = db.execute("SELECT bytes,length(content) actual,substr(content,1,?) content FROM venture_inputs WHERE digest=?", (MAX_SOURCE+1, digest)).fetchone()
        if old:
            if old["bytes"] != len(raw) or old["actual"] != len(raw) or bytes(old["content"]) != raw:
                raise ValueError("Accepted venture source identity conflicts.")
            return digest
        count, total = db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM venture_inputs").fetchone()
        if count >= MAX_INPUTS or total+len(raw) > MAX_INPUT_TOTAL:
            raise ValueError("Venture accepted-source quota is full; review retained history.")
        db.execute("INSERT INTO venture_inputs VALUES(?,?,?)", (digest, len(raw), raw))
        return digest

    @staticmethod
    def _task_digest(task, projects):
        return _digest({"task": task, "project": projects[task["project_id"]]})

    def configure(self, board, expected_revision, resume=False):
        if type(resume) is not bool or type(board) is not dict or set(board)-{"schema", "revision", "enabled", "paused", "max_drafts_per_day", "projects", "tasks"}:
            raise ValueError("Unsupported venture configuration fields.")
        cfg = {**_default(), **board}
        if type(cfg["schema"]) is not int or cfg["schema"] != 1 or type(cfg["enabled"]) is not bool or type(cfg["paused"]) is not bool or type(cfg["max_drafts_per_day"]) is not int or not 1 <= cfg["max_drafts_per_day"] <= 2:
            raise ValueError("Use venture schema1, boolean controls and one or two local drafts per day.")
        if type(cfg["projects"]) is not list or len(cfg["projects"]) > 12 or type(cfg["tasks"]) is not list or len(cfg["tasks"]) > 64:
            raise ValueError("At most twelve venture projects and sixty-four tasks are supported.")
        with self.database() as db:
            current, unused = self._settings(db)
            self._revision(current, expected_revision)
        projects, inputs = {}, {}
        for project in cfg["projects"]:
            if type(project) is not dict or set(project) != {"id", "name", "domain", "sources"}:
                raise ValueError("Project fields: id, name, domain, sources.")
            identifier = _identifier(project["id"])
            if identifier in projects or project["domain"] not in {"business", "etf_research"}:
                raise ValueError("Use unique project IDs and supported domains.")
            _text(project["name"], 200)
            if type(project["sources"]) is not list or not 1 <= len(project["sources"]) <= 3:
                raise ValueError("Each venture project needs one to three pinned local sources.")
            roles, seen_paths = set(), set()
            for source in project["sources"]:
                if type(source) is not dict or set(source)-{"role", "path", "sha256", "data_as_of"} or not {"role", "path", "sha256"} <= set(source):
                    raise ValueError("Source fields: role, path, sha256 and optional data_as_of.")
                if source["role"] not in {"plan", "strategy_report"}:
                    raise ValueError("Use a supported venture source role.")
                roles.add(source["role"])
                if type(source["sha256"]) is not str or not _SHA.fullmatch(source["sha256"]):
                    raise ValueError("Source SHA256 must be a lowercase digest.")
                _text(source["path"], 1000)
                if source["path"] in seen_paths:
                    raise ValueError("Duplicate venture source paths are not accepted.")
                seen_paths.add(source["path"])
                if source.get("data_as_of") is not None:
                    if type(source["data_as_of"]) is not str or date.fromisoformat(source["data_as_of"]).isoformat() != source["data_as_of"]:
                        raise ValueError("data_as_of requires an ISO date.")
                raw = self._read(source["path"])
                if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                    raise ValueError("Venture source does not match the proposed registration hash.")
                if source["role"] == "strategy_report":
                    try:
                        document = _load(raw.decode("utf-8-sig"))
                    except (ValueError, RecursionError) as error:
                        raise ValueError("Strategy reports require bounded finite JSON.") from error
                    if type(document) is not dict:
                        raise ValueError("Strategy reports require a JSON object.")
                inputs[source["sha256"]] = raw
            required = "plan" if project["domain"] == "business" else "strategy_report"
            if required not in roles:
                raise ValueError("The project lacks its required plan or strategy-report source.")
            projects[identifier] = project
        tasks = {}
        for value in cfg["tasks"]:
            if type(value) is not dict or set(value)-{"id", "project_id", "kind", "title", "depends_on", "requires_owner_evidence"} or not {"id", "project_id", "kind", "title"} <= set(value):
                raise ValueError("Unsupported venture task fields.")
            task = {"depends_on": [], "requires_owner_evidence": False, **value}
            identifier = _identifier(task["id"])
            if identifier in tasks or task["project_id"] not in projects or task["kind"] not in _KINDS:
                raise ValueError("Task IDs must be unique and reference a supported project/kind.")
            if (task["kind"] == "etf_review_pack") != (projects[task["project_id"]]["domain"] == "etf_research"):
                raise ValueError("Task kind must match its project domain.")
            _text(task["title"], 200)
            if type(task["requires_owner_evidence"]) is not bool or type(task["depends_on"]) is not list or len(task["depends_on"]) > 16 or len(set(task["depends_on"])) != len(task["depends_on"]):
                raise ValueError("Use bounded unique dependencies and boolean evidence controls.")
            tasks[identifier] = task
        visiting, visited = set(), set()
        def visit(identifier):
            if identifier in visiting:
                raise ValueError("Venture task dependency cycles are not accepted.")
            if identifier in visited:
                return
            visiting.add(identifier)
            for parent in tasks[identifier]["depends_on"]:
                if parent not in tasks:
                    raise ValueError("Venture dependency does not name a current task.")
                visit(parent)
            visiting.remove(identifier); visited.add(identifier)
        for identifier in tasks:
            visit(identifier)
        cfg["tasks"] = list(tasks.values())
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row, previous = self._settings(db)
            self._revision(row, expected_revision)
            if "revision" in board and board["revision"] != expected_revision:
                raise VentureConflict("Board revision and expected revision differ.", revision=row["revision"])
            for progress in db.execute("SELECT task_id,task_digest FROM venture_task_progress"):
                if progress["task_id"] in tasks and progress["task_digest"] != self._task_digest(tasks[progress["task_id"]], projects):
                    raise ValueError("Started venture tasks are immutable; use a new task ID for revised work.")
            for raw in inputs.values():
                self._put(db, raw)
            cfg["revision"] = row["revision"]+1
            db.execute("UPDATE venture_settings SET revision=?,value=?,hold_request=?,hold_reason=? WHERE id=1", (cfg["revision"], _json(cfg), None if resume else row["hold_request"], None if resume else row["hold_reason"]))
        return self.status()

    def record_evidence(self, value, expected_revision):
        if type(value) is not dict or set(value)-{"task_id", "kind", "observed_at", "note", "path", "sha256"} or not {"task_id", "kind", "observed_at", "note"} <= set(value):
            raise ValueError("Evidence fields: task_id, kind, observed_at, note and optional path/sha256 pair.")
        identifier = _identifier(value["task_id"])
        if value["kind"] not in {"review", "interview", "customer"}:
            raise ValueError("Choose owner-reported review, interview or customer evidence.")
        observed = _now(datetime.fromisoformat(value["observed_at"]))
        if observed > _now():
            raise ValueError("Owner observations cannot be future dated.")
        note = _text(value["note"], 2000)
        raw = None
        if ("path" in value) != ("sha256" in value):
            raise ValueError("Evidence path and SHA256 must be supplied together.")
        if "path" in value:
            raw = self._read(value["path"])
            if hashlib.sha256(raw).hexdigest() != value["sha256"]:
                raise ValueError("Evidence bytes differ from the proposed digest.")
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row, cfg = self._settings(db); self._revision(row, expected_revision)
            progress = db.execute("SELECT p.status,r.status run_status FROM venture_task_progress p LEFT JOIN venture_runs r ON r.request_id=p.request_id WHERE p.task_id=?", (identifier,)).fetchone()
            if not any(task["id"] == identifier for task in cfg["tasks"]) or not progress or progress["status"] != "draft_ready" or progress["run_status"] != "completed":
                raise ValueError("Record evidence against an existing completed local draft; failed tasks are never adopted.")
            if db.execute("SELECT count(*) FROM venture_evidence").fetchone()[0] >= MAX_HISTORY:
                raise ValueError("Venture evidence history is full.")
            source_digest = self._put(db, raw) if raw is not None else None
            db.execute("INSERT INTO venture_evidence VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex, identifier, value["kind"], observed.isoformat(), note, source_digest, time.time()))
            cfg["revision"] = row["revision"]+1
            db.execute("UPDATE venture_settings SET revision=?,value=? WHERE id=1", (cfg["revision"], _json(cfg)))
        return self.status()

    def resolve_hold(self, request_id, expected_revision):
        _identifier(request_id)
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row, cfg = self._settings(db); self._revision(row, expected_revision)
            if row["hold_request"] != request_id:
                raise VentureConflict("The requested venture hold is no longer current.", revision=row["revision"])
            cfg["revision"] += 1
            db.execute("UPDATE venture_settings SET revision=?,value=?,hold_request=NULL,hold_reason=NULL WHERE id=1", (cfg["revision"], _json(cfg)))
        return self.status()

    @staticmethod
    def _progress(db):
        return {row["task_id"]: dict(row) for row in db.execute("SELECT p.*,r.status run_status FROM venture_task_progress p LEFT JOIN venture_runs r ON r.request_id=p.request_id")}

    @staticmethod
    def _next(cfg, progress, evidence):
        tasks = {task["id"]: task for task in cfg["tasks"]}
        result = []
        for task in cfg["tasks"]:
            if task["id"] in progress:
                continue
            blocked = []
            for parent in task["depends_on"]:
                if progress.get(parent, {}).get("status") != "draft_ready" or progress.get(parent, {}).get("run_status") != "completed":
                    blocked.append(parent+": local draft not ready")
                elif tasks[parent]["requires_owner_evidence"] and parent not in evidence:
                    blocked.append(parent+": owner-reported evidence required")
            result.append({"task_id": task["id"], "project_id": task["project_id"], "title": task["title"], "eligible": not blocked, "blockers": blocked})
        return result

    def status(self, now=None):
        now = _now(now)
        with self.database() as db:
            row, cfg = self._settings(db)
            progress = self._progress(db)
            evidence = [dict(item) for item in db.execute("SELECT id,task_id,kind,observed_at,note,source_digest FROM venture_evidence ORDER BY created DESC LIMIT 100")]
            eligible_evidence = {item[0] for item in db.execute("SELECT DISTINCT task_id FROM venture_evidence")}
            latest = db.execute("SELECT receipt,status FROM venture_runs ORDER BY created DESC LIMIT 1").fetchone()
            state = "held" if row["hold_request"] else "disabled" if not cfg["enabled"] else "paused" if cfg["paused"] or self._global_pause(db) else "ready"
            cached = {}
            for project in cfg["projects"]:
                for source in project["sources"]:
                    saved = db.execute("SELECT bytes,length(content) actual,substr(content,1,?) content FROM venture_inputs WHERE digest=?", (MAX_SOURCE+1, source["sha256"])).fetchone()
                    if saved and saved["actual"] == saved["bytes"] and 0 <= saved["actual"] <= MAX_SOURCE:
                        raw = bytes(saved["content"])
                        if hashlib.sha256(raw).hexdigest() == source["sha256"]:
                            cached[source["sha256"]] = raw
            summaries = self._projects(cfg, cached, now.date().isoformat()) if all(source["sha256"] in cached for project in cfg["projects"] for source in project["sources"]) else []
        return {"schema": 1, "revision": cfg["revision"], "config": cfg, "state": state,
                "hold": {"request_id": row["hold_request"], "reason": row["hold_reason"]} if row["hold_request"] else None,
                "progress": [{key: value for key, value in item.items() if key != "receipt"} for item in progress.values()],
                "evidence": [{**item, "owner_reported": True, "independently_verified": False} for item in evidence],
                "next_actions": self._next(cfg, progress, eligible_evidence),
                "latest": {**_load(latest["receipt"]), "status": latest["status"]} if latest else None,
                "projects": summaries, "registered_sources_only": True, "source_integrity_checked": False,
                "checked_at": now.isoformat(), "model_calls": 0, "network_calls": 0,
                "limits": {"projects": 12, "tasks": 64, "drafts_per_day": 2, "source_bytes": MAX_SOURCE, "accepted_input_bytes": MAX_INPUT_TOTAL, "history": MAX_HISTORY}}

    def _checkpoint(self, revision, permitted):
        if permitted is not None and not permitted():
            raise VentureConflict("Briefing controls changed while the venture pulse was running.")
        with self.database() as db:
            row, cfg = self._settings(db)
            if row["revision"] != revision or row["hold_request"] or not cfg["enabled"] or cfg["paused"] or self._global_pause(db):
                raise VentureConflict("Venture configuration or autonomy controls changed during the pulse.")

    def _sources(self, cfg):
        values = {}
        for project in cfg["projects"]:
            for source in project["sources"]:
                current = self._read(source["path"])
                if hashlib.sha256(current).hexdigest() != source["sha256"]:
                    raise ValueError("Registered venture source changed; explicit owner registration is required.")
                with self.database() as db:
                    row = db.execute("SELECT bytes,length(content) actual,substr(content,1,?) content FROM venture_inputs WHERE digest=?", (MAX_SOURCE+1, source["sha256"])).fetchone()
                if not row or row["bytes"] != len(current) or row["actual"] != len(current) or bytes(row["content"]) != current:
                    raise ValueError("Accepted private venture source is missing or changed.")
                values[source["sha256"]] = bytes(row["content"])
        return values

    @staticmethod
    def _projects(cfg, sources, local_day):
        rows = []
        for project in cfg["projects"]:
            evidence = []
            for source in project["sources"]:
                raw = sources[source["sha256"]].decode("utf-8-sig")
                try:
                    document = _load(raw)
                except (ValueError, RecursionError):
                    document = raw
                summary = document.get("summary", document) if type(document) is dict else document
                excerpt = _json(summary)[:6000] if type(summary) in {dict, list} else str(summary)[:6000]
                as_of = source.get("data_as_of")
                age = (date.fromisoformat(local_day)-date.fromisoformat(as_of)).days if as_of else None
                freshness = "unavailable" if age is None else "future_date" if age < 0 else "stale" if age > 4 else "dated"
                evidence.append({**source, "age_calendar_days": age, "freshness": freshness, "accepted_excerpt": excerpt, "live": False})
            rows.append({"id": project["id"], "name": project["name"], "domain": project["domain"], "sources": evidence,
                         "scope": "NAV next-close research proxy where NAV data is used; not exchange fills, a live signal or proof of future profit." if project["domain"] == "etf_research" else "Research and local drafts; no verified customer, revenue or external action."})
        return rows

    @staticmethod
    def _draft(task, project):
        common = ["Local draft only. No message, purchase, payment, account, broker order or device action occurred."]
        if task["kind"] == "business_offer_pack":
            lines = common+["Define the buyer's specific problem and the measurable deliverable from the accepted plan.",
                "Write a fixed-scope offer, acceptance checklist and price assumptions for owner review.",
                "List fulfillment partners and responsibilities as unverified hypotheses; do not claim agreements.",
                "Separate zero upfront cash assumptions from owner time, taxes, licensing and future delivery costs."]
        elif task["kind"] == "business_validation_pack":
            lines = common+["Owner interview worksheet: current workaround, frequency, cost, buying authority and evidence of willingness to pay.",
                "Log actual owner-conducted interviews and customer evidence before advancing dependent tasks.",
                "Use the accepted plan's declared go/no-go criteria. Missing observations remain unknown.",
                "Prepare a small manual pilot and fulfillment checklist; outreach remains an unsent draft."]
        else:
            lines = common+["Inspect accepted source data dates, cost assumptions, benchmark and holdout results.",
                "NAV-only studies use a next-close NAV proxy; NAV is not an executable ETF quote or exchange fill.",
                "Report losing periods, drawdown, turnover and missing coverage alongside returns. Do not label a guaranteed winner.",
                "A stale or unchanged source cannot establish a new market observation. No paper or live orders were placed."]
        return {"task_id": task["id"], "project_id": task["project_id"], "title": task["title"], "kind": task["kind"],
                "progress": "draft_ready", "requires_owner_evidence": task["requires_owner_evidence"], "checklist": lines,
                "accepted_source_hashes": [source["sha256"] for source in project["sources"]]}

    def _verify(self, receipt):
        expected = {receipt["output_folder"]+"/progress.md", receipt["output_folder"]+"/progress.json"}
        if len(receipt.get("artifacts", [])) != 2 or {item.get("path") for item in receipt["artifacts"]} != expected:
            raise ValueError("The complete venture output contract is missing.")
        for artifact in receipt.get("artifacts", []):
            raw = self._read(artifact["path"])
            if len(raw) != artifact["bytes"] or hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
                raise ValueError("Venture output failed integrity verification; earlier output will not be adopted.")

    @staticmethod
    def _set_hold(db, request_id, reason):
        row, cfg = VentureLab._settings(db)
        cfg["revision"] = row["revision"]+1
        db.execute("UPDATE venture_settings SET revision=?,value=?,hold_request=?,hold_reason=? WHERE id=1", (cfg["revision"], _json(cfg), request_id, reason))

    def _hold(self, request_id, reason, receipt=None, *, local_day=None, now=None):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            if receipt is not None:
                receipt.update(status="failed", error=reason, completed_at=_now().isoformat())
                db.execute("UPDATE venture_runs SET status='failed',receipt=? WHERE request_id=?", (_json(receipt), request_id))
                db.execute("UPDATE venture_task_progress SET status='failed' WHERE request_id=? AND status='running'", (request_id,))
            elif local_day and not db.execute("SELECT 1 FROM venture_runs WHERE request_id=? OR local_day=?", (request_id, local_day)).fetchone():
                row, cfg = self._settings(db)
                if db.execute("SELECT count(*) FROM venture_runs").fetchone()[0] < MAX_HISTORY:
                    failed = {"schema": 1, "request_id": request_id, "local_day": local_day, "status": "failed", "error": reason, "artifacts": [], "model_calls": 0}
                    db.execute("INSERT INTO venture_runs VALUES(?,?,?,?,?,?)", (request_id, local_day, "failed", _now(now).timestamp(), _json(cfg), _json(failed)))
            self._set_hold(db, request_id, reason)

    def run_for_brief(self, request_id, *, local_day, now=None, advance=True, permitted=None):
        _identifier(request_id); now = _now(now)
        if type(local_day) is not str or date.fromisoformat(local_day).isoformat() != local_day or type(advance) is not bool:
            raise ValueError("Use an ISO local day and boolean advance control.")
        if permitted is not None and not permitted():
            return {"status": "held", "reason": "Briefing controls hold venture work.", "model_calls": 0, "artifacts": []}
        state = self.status(now)
        if state["state"] != "ready":
            return {"status": state["state"], "reason": (state["hold"] or {}).get("reason", "Venture work is disabled or paused."), "model_calls": 0, "artifacts": [], "next_actions": state["next_actions"], "projects": state["projects"], "source_integrity_checked": False}
        if not advance:
            return {"status": "review_only", "model_calls": 0, "artifacts": [], "next_actions": state["next_actions"], "advanced_tasks": [], "projects": state["projects"], "source_integrity_checked": False}
        guard = InstanceLock(self.lock_path)
        with _RUN_LOCK:
            try:
                guard.acquire()
            except ValueError as error:
                return {"status": "held", "reason": str(error), "model_calls": 0, "artifacts": []}
            try:
                return self._run(request_id, local_day, now, permitted)
            finally:
                guard.release()

    run = run_for_brief

    def _run(self, request_id, local_day, now, permitted):
        receipt, hold_id = None, request_id
        try:
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                row, cfg = self._settings(db)
                old_running = db.execute("SELECT request_id,receipt FROM venture_runs WHERE status='running' ORDER BY created LIMIT 1").fetchone()
                if old_running:
                    broken = _load(old_running["receipt"])
                    broken.update(status="interrupted", error="An earlier venture pulse started without durable completion; no output will be adopted or replayed.")
                    db.execute("UPDATE venture_runs SET status='interrupted',receipt=? WHERE request_id=?", (_json(broken), old_running["request_id"]))
                    db.execute("UPDATE venture_task_progress SET status='interrupted' WHERE request_id=? AND status='running'", (old_running["request_id"],))
                    self._set_hold(db, old_running["request_id"], broken["error"])
                    return {"status": "held", "request_id": old_running["request_id"], "reason": broken["error"], "model_calls": 0, "artifacts": []}
                if row["hold_request"] or not cfg["enabled"] or cfg["paused"] or self._global_pause(db):
                    return {"status": "held", "reason": "Venture or autonomy controls changed.", "model_calls": 0, "artifacts": []}
                old = db.execute("SELECT * FROM venture_runs WHERE request_id=? OR local_day=?", (request_id, local_day)).fetchone()
                if old:
                    if old["request_id"] == request_id and old["local_day"] != local_day:
                        raise VentureConflict("This venture request ID belongs to another day.")
                    if old["status"] != "completed":
                        raise VentureConflict("The earlier venture request is held; it will not replay.")
                    prior = _load(old["receipt"])
                    hold_id = prior["request_id"]
                else:
                    prior = None
            if prior is not None:
                self._sources(cfg)
                self._verify(prior)
                return {**prior, "replayed": True}
            self._checkpoint(cfg["revision"], permitted)
            sources = self._sources(cfg)
            projects = {project["id"]: project for project in cfg["projects"]}
            # A later local task must not trust a previously completed draft
            # whose actual artifacts were subsequently changed or removed.
            with self.database() as db:
                current_ids = {task["id"] for task in cfg["tasks"]}
                completed_ids = {row["request_id"] for row in self._progress(db).values() if row["task_id"] in current_ids and row["status"] == "draft_ready"}
                earlier = [db.execute("SELECT receipt,status FROM venture_runs WHERE request_id=?", (identifier,)).fetchone() for identifier in completed_ids]
            for previous in earlier:
                if not previous or previous["status"] != "completed":
                    raise ValueError("An earlier venture task lacks a completed parent receipt.")
                self._verify(_load(previous["receipt"]))
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                row, live = self._settings(db)
                if live["revision"] != cfg["revision"] or row["hold_request"] or self._global_pause(db):
                    raise VentureConflict("Venture controls changed before acceptance.")
                if db.execute("SELECT count(*) FROM venture_runs").fetchone()[0] >= MAX_HISTORY or db.execute("SELECT count(*) FROM venture_task_progress").fetchone()[0]+2 > MAX_HISTORY:
                    raise ValueError("Venture progress history is full; owner review is required.")
                progress = self._progress(db)
                evidence = {row[0] for row in db.execute("SELECT DISTINCT task_id FROM venture_evidence")}
                available = self._next(cfg, progress, evidence)
                chosen_ids = [row["task_id"] for row in available if row["eligible"]][:cfg["max_drafts_per_day"]]
                selected = [task for task in cfg["tasks"] if task["id"] in chosen_ids]
                folder = "venture-lab/progress/"+hashlib.sha256((request_id+":"+local_day).encode()).hexdigest()[:32]
                receipt = {"schema": 1, "status": "running", "request_id": request_id, "local_day": local_day,
                    "created_at": now.isoformat(), "revision": cfg["revision"], "board_sha256": _digest(cfg),
                    "output_folder": folder, "artifacts": [], "advanced_tasks": [], "model_calls": 0, "network_calls": 0, "replayed": False}
                db.execute("INSERT INTO venture_runs VALUES(?,?,?,?,?,?)", (request_id, local_day, "running", now.timestamp(), _json(cfg), _json(receipt)))
                for task in selected:
                    db.execute("INSERT INTO venture_task_progress VALUES(?,?,?,?,?)", (task["id"], self._task_digest(task, projects), "running", request_id, "{}"))
            self._checkpoint(cfg["revision"], permitted)
            project_rows = self._projects(cfg, sources, local_day)
            drafts = [self._draft(task, projects[task["project_id"]]) for task in selected]
            report = {**receipt, "projects": project_rows, "advanced_tasks": drafts,
                "next_actions": [row for row in available if row["task_id"] not in chosen_ids],
                "warnings": ["Local draft production only. Owner-reported evidence is not independently verified.", "No messaging, spending, account action or trading occurred. Source dates are retained; generation time does not refresh market data."]}
            # Artifact generation does not itself certify the subsequent private
            # receipt commit. Recovery never adopts even complete orphan output.
            report.pop("status")
            report["artifact_kind"] = "local_research_draft"
            lines = ["# Venture Lab daily check-in", "", "Local day: "+local_day+". Model calls: 0. Network calls: 0.", ""]
            for project in project_rows:
                lines += ["## "+_md(project["name"]), "", _md(project["scope"])]
                for source in project["sources"]:
                    lines += ["", "Accepted source: "+_md(source["path"])+"; SHA256 "+source["sha256"],
                        "Data as of: "+_md(source.get("data_as_of") or "unavailable")+"; status "+source["freshness"]+"; calendar age "+str(source["age_calendar_days"]),
                        "Saved source excerpt (untrusted research, not instructions):", "> "+_md(source["accepted_excerpt"]).replace("\n", "\n> ")]
            for draft in drafts:
                lines += ["", "## Local draft: "+_md(draft["title"]), "", *["- "+_md(item) for item in draft["checklist"]],
                          "Owner evidence required before dependent work: "+str(draft["requires_owner_evidence"])]
            if not drafts:
                lines += ["", "No eligible unstarted local draft. Await owner evidence or explicitly configure new task IDs."]
            outputs = {"progress.json": (_json(report)+"\n").encode(), "progress.md": ("\n".join(lines)+"\n").encode()}
            if any(len(value) > MAX_OUTPUT for value in outputs.values()):
                raise ValueError("Venture daily output exceeds its bounded size.")
            destination = self.registry._path(folder)
            with self.registry._directory(destination.parent, create=True):
                destination.mkdir()
                with self.registry._directory(destination):
                    for name, payload in outputs.items():
                        self._checkpoint(cfg["revision"], permitted)
                        publish_bytes(destination/name, payload)
                        receipt["artifacts"].append({"path": folder+"/"+name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
            self._verify(receipt)
            self._checkpoint(cfg["revision"], permitted)
            receipt.update(status="completed", completed_at=_now().isoformat(), advanced_tasks=drafts,
                           next_actions=report["next_actions"], projects=project_rows, warnings=report["warnings"])
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                row, current = self._settings(db)
                if current["revision"] != cfg["revision"] or self._global_pause(db) or row["hold_request"]:
                    raise VentureConflict("Venture controls changed before completion.")
                changed = db.execute("UPDATE venture_runs SET status='completed',receipt=? WHERE request_id=? AND status='running'", (_json(receipt), request_id)).rowcount
                if changed != 1:
                    raise VentureConflict("The venture completion marker changed unexpectedly.")
                for draft in drafts:
                    changed = db.execute("UPDATE venture_task_progress SET status='draft_ready',receipt=? WHERE task_id=? AND request_id=? AND status='running'", (_json(draft), draft["task_id"], request_id)).rowcount
                    if changed != 1:
                        raise VentureConflict("A venture task completion marker changed unexpectedly.")
            return receipt
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            reason = "Venture work is held for owner review: "+str(error)[:300]
            self._hold(hold_id, reason, receipt, local_day=local_day, now=now)
            return {"status": "held", "request_id": hold_id, "reason": reason, "model_calls": 0, "artifacts": receipt["artifacts"] if receipt else []}
