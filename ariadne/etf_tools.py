"""Frozen offline ETF studies with a private, fail-stop publication journal.

This adapter writes research artifacts only. It does not fetch prices, call a
model, submit orders, or interpret dataset text as executable instructions.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time

from .config import _allowed_path
from .locking import InstanceLock

NAMES = {"etf_research"}
VERSION = "etf-tools-1"
MAX_INPUT_BYTES = 4_000_000
MAX_ARTIFACT_BYTES = 4_000_000
MAX_MARKDOWN_BYTES = 262_144
RESERVATION_BYTES = 8_000_000
REPORT_BUDGET_BYTES = 64 * 1024 * 1024
MAX_RUNS = 1000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def _engine():
    from . import etf_research
    return etf_research


def _json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _load(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON keys are not accepted.")
            result[key] = value
        return result

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("JSON numbers must be finite.")
        return parsed

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_float=number,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("JSON numbers must be finite.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("ETF input requires bounded strict UTF-8 JSON.") from None


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _md(value):
    return html.escape(str(value), quote=False).replace("`", "\\`").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


class ETFResearchTools:
    def __init__(self, registry):
        self.registry = registry
        self.lock = threading.RLock()

    def schemas(self):
        from .tools import _schema
        return [_schema("etf_research", "Run fixed offline ETF NAV research from a local JSON dataset. Accepts a frozen private input snapshot and creates exclusive verified study.json/study.md reports. NAV-proxy calculations, zero-return cash and explicit source/dividend limits are not executable-price validation, a proven winner or a broker/order route. Failed or unfinished IDs never run again.", {
            "action": {"type": "string", "enum": ["study"]},
            "path": {"type": "string", "description": "Workspace-relative UTF-8 ETF dataset JSON."},
            "request_id": {"type": "string", "maxLength": 64},
            "cost_bps": {"type": "number", "minimum": 0, "maximum": 100, "default": 5.0}},
            ["action", "path", "request_id"])]

    def status(self):
        bound = getattr(self.registry.procedures, "store", None) is not None
        return {"ready": bound, "offline": True, "model_calls": 0,
                "reason": "Fixed local NAV research; private main-store binding is required. No live feed, model or order connection.",
                "limits": {"input_bytes": MAX_INPUT_BYTES, "artifact_bytes": MAX_ARTIFACT_BYTES,
                           "report_budget_bytes": REPORT_BUDGET_BYTES, "reservation_bytes": RESERVATION_BYTES,
                           "history_rows": MAX_RUNS, "input_budget_scope": "Shared operation_inputs 64 MiB / 512 digests",
                           "report_budget_scope": "Separate ETF lifetime budget; existing routine report accounting is unchanged."}}

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown ETF research tool.")
        return "auto"

    def _arguments(self, name, args):
        if name not in NAMES or type(args) is not dict or set(args) - {"action", "path", "request_id", "cost_bps"} or not {"action", "path", "request_id"} <= set(args):
            raise ValueError("ETF study requires action, path, request_id and optional cost_bps only.")
        if args["action"] != "study" or type(args["request_id"]) is not str or not _ID.fullmatch(args["request_id"]):
            raise ValueError("Choose action study and a request ID of 1–64 letters, digits, underscores or hyphens.")
        path = self.registry._path(args["path"])
        if path.suffix.lower() != ".json":
            raise ValueError("ETF input must be a workspace JSON file.")
        cost = args.get("cost_bps", 5.0)
        if type(cost) not in (int, float) or not 0 <= cost <= 100 or not math.isfinite(cost):
            raise ValueError("cost_bps must be a finite number from 0 through 100; booleans are not accepted.")
        return {"action": "study", "path": path.relative_to(self.registry.root).as_posix(),
                "request_id": args["request_id"], "cost_bps": float(cost)}

    def _store(self):
        store = self.registry.procedures.store
        if store is None:
            raise ValueError("ETF research requires the application's private main store.")
        path = _allowed_path(store.path)
        if path.parent != _allowed_path(self.registry.settings.data_dir) or path.is_relative_to(self.registry.root):
            raise ValueError("ETF history must use the bound private main database outside the writable workspace.")
        return store

    @contextmanager
    def database(self):
        store = self._store()
        with self.lock, self.registry._directory(store.path.parent):
            for suffix in ("", "-journal", "-wal", "-shm"):
                path = _allowed_path(str(store.path) + suffix)
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    if suffix:
                        continue
                    raise ValueError("The initialized ETF main database is missing; owner review is required.") from None
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("ETF database files must be regular files without links or reparse points.")
            with store.connect(require_existing=True) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                deadline = time.monotonic() + 5
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
                yield db

    @staticmethod
    def _schema(db):
        db.execute("CREATE TABLE IF NOT EXISTS operation_inputs(digest TEXT PRIMARY KEY,content BLOB NOT NULL,bytes INTEGER NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS etf_runs(
            request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,source_path TEXT NOT NULL,
            input_sha256 TEXT NOT NULL,input_bytes INTEGER NOT NULL,cost_bps REAL NOT NULL,
            status TEXT NOT NULL,accepted_at REAL NOT NULL,completed_at REAL,
            reserved_bytes INTEGER NOT NULL,receipt TEXT NOT NULL)""")

    @staticmethod
    def _paused(db):
        row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        if row is None:
            return False
        value = json.loads(row[0])
        if type(value) is not bool:
            raise ValueError("Autonomy pause state is invalid; owner review is required.")
        return value

    def _checkpoint(self, cancelled):
        if cancelled is not None and cancelled():
            raise ValueError("ETF research cancelled; no further publication is allowed.")
        with self.database() as db:
            if self._paused(db):
                raise ValueError("Global autonomy pause holds ETF research.")

    def _read(self, relative, cap):
        path = self.registry._path(relative)
        with self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path, directory), "rb") as source:
                before = os.fstat(source.fileno())
                raw = source.read(cap + 1)
                after = os.fstat(source.fileno())
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) > cap:
            raise ValueError("ETF snapshot exceeds its declared byte limit.")
        if (not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(after.st_mode)
                or before.st_nlink != 1 or after.st_nlink != 1
                or identity(before) != identity(after) or len(raw) != before.st_size):
            raise ValueError("ETF source changed during its guarded read; no mixed input was accepted.")
        return raw

    def fingerprint_material(self, name, args):
        prepared = self._arguments(name, args)
        raw = self._read(prepared["path"], MAX_INPUT_BYTES)
        return {"implementation": VERSION, "arguments": prepared,
                "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw)}

    @staticmethod
    def _accepted(db, row):
        size, digest = row["input_bytes"], row["input_sha256"]
        if type(size) is not int or not 0 <= size <= MAX_INPUT_BYTES or type(digest) is not str or not _HASH.fullmatch(digest):
            raise ValueError("Accepted ETF input identity is invalid.")
        saved = db.execute("SELECT bytes,length(content) size,typeof(content) kind,substr(content,1,?) content FROM operation_inputs WHERE digest=?", (MAX_INPUT_BYTES + 1, digest)).fetchone()
        if not saved or saved["kind"] != "blob" or saved["bytes"] != size or saved["size"] != size:
            raise ValueError("Accepted ETF input is missing or has invalid size accounting.")
        raw = bytes(saved["content"])
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Accepted ETF input failed its SHA-256 check.")
        return raw

    @staticmethod
    def _quota(db):
        row = db.execute("""SELECT count(*),coalesce(sum(reserved_bytes),0),count(CASE
            WHEN typeof(reserved_bytes)!='integer' OR reserved_bytes<0 OR reserved_bytes>?
              OR status NOT IN ('running','completed','failed','interrupted')
              OR (status!='completed' AND reserved_bytes!=?)
              OR (status='completed' AND reserved_bytes=0)
            THEN 1 END) FROM etf_runs""", (RESERVATION_BYTES, RESERVATION_BYTES)).fetchone()
        if row[2]:
            raise ValueError("ETF report reservation accounting is invalid; owner review is required.")
        if row[0] >= MAX_RUNS or row[1] + RESERVATION_BYTES > REPORT_BUDGET_BYTES:
            raise ValueError("ETF report history/budget is full; no input or report was deleted.")

    def _verify(self, row, receipt):
        expected = {f"reports/etf-research/{row['request_id']}/study.json",
                    f"reports/etf-research/{row['request_id']}/study.md"}
        artifacts = receipt.get("artifacts")
        if (receipt.get("request_id") != row["request_id"] or receipt.get("status") != "completed"
                or receipt.get("accepted_input") != {"path": row["source_path"], "sha256": row["input_sha256"], "bytes": row["input_bytes"]}
                or type(artifacts) is not list or len(artifacts) != 2
                or {item.get("path") for item in artifacts if type(item) is dict} != expected):
            raise ValueError("Stored ETF receipt has an invalid identity or output contract.")
        total = 0
        for item in artifacts:
            if (set(item) != {"path", "bytes", "sha256"} or type(item["bytes"]) is not int
                    or not 0 < item["bytes"] <= MAX_ARTIFACT_BYTES or type(item["sha256"]) is not str or not _HASH.fullmatch(item["sha256"])):
                raise ValueError("Stored ETF artifact metadata is invalid.")
            raw = self._read(item["path"], MAX_ARTIFACT_BYTES)
            if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("Completed ETF artifacts changed; original results were not replayed.")
            total += len(raw)
            if item["path"].endswith("study.json"):
                report = _load(raw)
                if report.get("request_id") != row["request_id"] or report.get("accepted_input") != receipt["accepted_input"]:
                    raise ValueError("ETF report does not match its accepted source identity.")
        if total != row["reserved_bytes"]:
            raise ValueError("Completed ETF artifact byte accounting differs from its journal.")

    def _current_source_matches(self, path, digest):
        try:
            return hashlib.sha256(self._read(path, MAX_INPUT_BYTES)).hexdigest() == digest
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _summary(study):
        validation = study.get("validation", {})
        symbols = validation.get("symbols", {}) if type(validation) is dict else {}
        if type(symbols) is not dict or len(symbols) > 8:
            raise ValueError("ETF engine returned invalid bounded source coverage.")
        last_dates = {symbol: value.get("last_date") for symbol, value in symbols.items() if type(value) is dict}
        qualification = study.get("qualification", {})
        calendar = study.get("calendar", {})
        if type(qualification) is not dict or type(calendar) is not dict:
            raise ValueError("ETF engine returned invalid qualification coverage.")
        strategies = study.get("strategies", [])
        if type(strategies) is not list or len(strategies) > 8:
            raise ValueError("ETF engine returned an invalid bounded strategy list.")
        rows = []
        for strategy in strategies:
            if type(strategy) is not dict or strategy.get("id") not in {"sma10", "absolute12", "dual12"} or strategy.get("status") not in {"completed", "blocked"}:
                raise ValueError("ETF engine returned invalid strategy metadata.")
            row = {key: strategy[key] for key in ("id", "status")}
            metrics = strategy.get("metrics")
            if type(metrics) is dict and len(_json(metrics)) <= 4000:
                row["metrics"] = metrics
            if type(strategy.get("reason")) is str:
                row["reason"] = strategy["reason"][:500]
                if len(strategy["reason"]) > 500:
                    row["reason_truncated"] = True
            rows.append(row)
        return {"study_type": study.get("study_type"), "status": "research_only", "strategies": rows,
                "analysis_cutoff": study.get("analysis_as_of"),
                "data_last_dates": last_dates, "retained_last_date": calendar.get("last_date"),
                "dividend_status": qualification.get("dividend_status", "unknown"),
                "month_end_coverage_status": calendar.get("month_end_coverage_status", "unknown"),
                "execution_qualified": False, "execution_basis": "NAV proxy; not executable fills",
                "cash_return_assumption": 0.0, "future_profit_proven": False}

    @staticmethod
    def _markdown(receipt, summary):
        lines = ["# Offline ETF research", "", "Research only: NAV execution proxy, zero-return cash, no orders or model calls.", "",
                 "Accepted workspace source: " + _md(receipt["accepted_input"]["path"]),
                 "Accepted SHA-256: " + receipt["accepted_input"]["sha256"],
                 "Dataset as_of (source assertion): " + _md(receipt["source_as_of"]),
                 "Cost assumption (basis points): " + str(receipt["cost_bps"]), "",
                 "The full JSON retains the engine's validation, qualification, source claims and metric limits.",
                 "A byte match does not establish source authenticity or fresh market coverage. No historical result proves future profit.", ""]
        for strategy in summary["strategies"]:
            lines.extend(["## " + _md(strategy["id"]), "Status: " + _md(strategy["status"]), ""])
            if "reason" in strategy:
                lines.append("Reason: " + _md(strategy["reason"]))
            if "metrics" in strategy:
                lines.append(_md(json.dumps(strategy["metrics"], sort_keys=True, ensure_ascii=False, allow_nan=False)))
        return ("\n".join(lines) + "\n").encode("utf-8")

    def execute(self, name, args, *, cancelled=None):
        prepared = self._arguments(name, args)
        if cancelled is not None and not callable(cancelled):
            raise ValueError("Cancellation must be a callable or None.")
        store = self._store()
        with self.registry._lock, self.lock:
            owner = InstanceLock(store.path.parent / "etf-research.lock")
            owner.acquire()
            try:
                return self._execute(prepared, cancelled)
            finally:
                owner.release()

    def _execute(self, args, cancelled):
        self._checkpoint(cancelled)
        request_id = args["request_id"]
        identity = _digest({"implementation": VERSION, **args})
        destination = self.registry._path(f"reports/etf-research/{request_id}")
        with self.database() as db:
            self._schema(db)
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM etf_runs WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous["digest"] != identity:
                    raise ValueError("ETF request ID belongs to different arguments; choose a new ID.")
                if previous["status"] != "completed":
                    raise ValueError("This ETF request previously started, failed or was interrupted; it will not run again or adopt old files.")
                self._accepted(db, previous)
                receipt = _load(previous["receipt"].encode("utf-8"))
                self._verify(previous, receipt)
                return {**receipt, "replayed": True,
                        "current_source_matches": self._current_source_matches(args["path"], previous["input_sha256"]),
                        "source_scope": "verified accepted snapshot; replay is not fresh research"}
            self._quota(db)
            try:
                destination.lstat()
            except FileNotFoundError:
                pass
            else:
                raise ValueError("ETF output directory already exists; it is not adopted or overwritten.")
            raw = self._read(args["path"], MAX_INPUT_BYTES)
            dataset = _load(raw)
            _engine().validate_dataset(dataset)
            if cancelled is not None and cancelled():
                raise ValueError("ETF research cancelled before acceptance.")
            if self._paused(db):
                raise ValueError("Global autonomy pause holds ETF acceptance.")
            from .operations_workflows import OperationsWorkflows
            reference = OperationsWorkflows._put(db, raw)
            accepted = time.time()
            receipt = {"ok": False, "tool": "etf_research", "implementation": VERSION,
                       "request_id": request_id, "status": "running", "replayed": False,
                       "accepted_at_utc": datetime.fromtimestamp(accepted, timezone.utc).isoformat(),
                       "completed_at_utc": None, "source_as_of": dataset["as_of"],
                       "accepted_input": {"path": args["path"], **reference}, "cost_bps": args["cost_bps"],
                       "artifacts": [], "offline": True, "model_calls": 0, "network_calls": 0, "orders": False,
                       "source_scope": "accepted snapshot; not a fresh market-data claim"}
            db.execute("INSERT INTO etf_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (request_id, identity, args["path"], reference["sha256"], reference["bytes"], args["cost_bps"],
                        "running", accepted, None, RESERVATION_BYTES, _json(receipt).decode("utf-8")))
        started = time.monotonic()
        try:
            self._checkpoint(cancelled)
            study = _engine().run_study(dataset, cost_bps=args["cost_bps"])
            self._checkpoint(cancelled)
            if type(study) is not dict:
                raise ValueError("ETF engine returned an invalid report.")
            summary = self._summary(study)
            report = {"schema": 1, "implementation": VERSION, "request_id": request_id,
                      "accepted_at_utc": receipt["accepted_at_utc"], "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                      "accepted_input": receipt["accepted_input"], "source_as_of": receipt["source_as_of"],
                      "cost_bps": args["cost_bps"], "offline": True, "model_calls": 0, "orders": False, "study": study}
            outputs = {"study.json": _json(report), "study.md": self._markdown(receipt, summary)}
            if len(outputs["study.json"]) > MAX_ARTIFACT_BYTES or len(outputs["study.md"]) > MAX_MARKDOWN_BYTES or sum(map(len, outputs.values())) > RESERVATION_BYTES:
                raise ValueError("ETF reports exceed the fixed output bounds.")
            publisher = self.registry.procedures.service()
            with self.registry._directory(destination.parent, create=True) as parent:
                if parent is None:
                    destination.mkdir()
                else:
                    os.mkdir(destination.name, mode=0o700, dir_fd=parent)
                with self.registry._directory(destination) as directory:
                    for filename, payload in outputs.items():
                        self._checkpoint(cancelled)
                        publisher._publish(destination / filename, payload, directory)
                        relative = destination.relative_to(self.registry.root).as_posix() + "/" + filename
                        published = self._read(relative, MAX_ARTIFACT_BYTES)
                        if published != payload:
                            raise ValueError("ETF artifact readback differs from the accepted publication.")
                        receipt["artifacts"].append({"path": relative, "bytes": len(published), "sha256": hashlib.sha256(published).hexdigest()})
            self._checkpoint(cancelled)
            receipt.update(ok=True, status="completed", completed_at_utc=datetime.now(timezone.utc).isoformat(),
                           wall_seconds=round(time.monotonic() - started, 6), summary=summary)
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                if self._paused(db) or cancelled is not None and cancelled():
                    raise ValueError("ETF publication is held before completion.")
                changed = db.execute("UPDATE etf_runs SET status='completed',completed_at=?,reserved_bytes=?,receipt=? WHERE request_id=? AND status='running'",
                                     (time.time(), sum(item["bytes"] for item in receipt["artifacts"]), _json(receipt).decode("utf-8"), request_id)).rowcount
                if changed != 1:
                    raise ValueError("ETF completion journal changed; owner review is required.")
            return {**receipt, "current_source_matches": self._current_source_matches(args["path"], reference["sha256"])}
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise  # The durable running marker remains uncertain and cannot replay.
            receipt.update(ok=False, status="failed", completed_at_utc=datetime.now(timezone.utc).isoformat(),
                           error="ETF study stopped after acceptance; inspect partial artifacts and choose a new ID only after review.",
                           error_type=type(error).__name__)
            try:
                with self.database() as db:
                    db.execute("UPDATE etf_runs SET status='failed',completed_at=?,receipt=? WHERE request_id=? AND status='running'",
                               (time.time(), _json(receipt).decode("utf-8"), request_id))
            except (OSError, ValueError, sqlite3.Error):
                pass  # An unclosed durable running marker also refuses replay.
            raise ValueError(receipt["error"]) from error
