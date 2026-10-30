"""Protected local options research, import inbox and simulated monthly risk ledger.

This module has no network, broker, inference or real-order interface. Quotes are
owner-supplied evidence, never execution authority or verified price predictions.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import hashlib
import json
import os
import threading
import uuid

from .atomic import publish_bytes
from .config import _allowed_path
from .handoff import HandoffDesk, HandoffConflict, _identifier, _load
from .locking import InstanceLock

UTC = timezone.utc
MAX_DATA = 32 * 1024 * 1024
MAX_REPORTS = 128 * 1024 * 1024
MAX_RUNS = 10000
MAX_RECEIPTS = 32 * 1024 * 1024
MAX_EVENTS = 30000
INBOX = "market-data/options/current.json"
DEFAULT = {"enabled": False, "paused": False, "revision": 0,
           "activation_at": None, "timezone": "America/Chicago", "hour": 6}


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _now(value=None):
    clock = datetime.now(UTC) if value is None else value
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Use an aware options workflow clock.")
    return clock.astimezone(UTC)


class OptionsWorkflow(HandoffDesk):
    def __init__(self, registry, store, controls=None, publication_lease=None):
        super().__init__(registry, store)
        self.controls = controls or (lambda: {"enabled": False, "paused": True, "revision": 0})
        self.publication_lease = publication_lease
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opt_meta'").fetchone() is not None

    @staticmethod
    def _schema(db):
        db.execute("CREATE TABLE IF NOT EXISTS opt_meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,config TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_data(id INTEGER PRIMARY KEY CHECK(id IN (1,2)),digest TEXT NOT NULL,value BLOB NOT NULL,bytes INTEGER NOT NULL,quality TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_accounts(id TEXT PRIMARY KEY,state TEXT NOT NULL,started_at TEXT NOT NULL,closed_at TEXT,policy TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_events(id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,run_id TEXT NOT NULL,value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_runs(request_id TEXT PRIMARY KEY,action TEXT NOT NULL,day TEXT,digest TEXT NOT NULL,status TEXT NOT NULL,receipt TEXT NOT NULL,bytes INTEGER NOT NULL)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS opt_daily ON opt_runs(day) WHERE day IS NOT NULL")
        db.execute("CREATE TABLE IF NOT EXISTS opt_reports(request_id TEXT PRIMARY KEY,bytes INTEGER NOT NULL,receipt TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO opt_meta VALUES(1,0,?)", (_json(DEFAULT),))

    def _meta(self, db):
        if not self._exists(db):
            return 0, dict(DEFAULT)
        row = db.execute("SELECT revision,config FROM opt_meta WHERE id=1").fetchone()
        if not row:
            raise ValueError("Options metadata is missing; no reset is permitted.")
        cfg = _load(row["config"])
        if set(cfg) != set(DEFAULT) or any(type(cfg[k]) is not bool for k in ("enabled", "paused")) or type(cfg["revision"]) is not int or cfg["revision"] < 0 or cfg["timezone"] != DEFAULT["timezone"] or cfg["hour"] != 6:
            raise ValueError("Options configuration is invalid.")
        if cfg["activation_at"] is not None:
            _now(datetime.fromisoformat(cfg["activation_at"]))
        return row["revision"], cfg

    @staticmethod
    def _cas(actual, expected):
        if type(expected) is not int or expected < 0:
            raise ValueError("expected_revision must be a nonnegative integer.")
        if actual != expected:
            raise HandoffConflict("Options state changed; inspect the latest revision.", revision=actual)

    def _policy(self, db, cfg=None):
        self._unpaused(db)
        current = self._meta(db)[1]
        brief = self.controls()
        if self.stopped.is_set() or not current["enabled"] or current["paused"] or not brief["enabled"] or brief["paused"] or brief.get("execution_hold"):
            raise HandoffConflict("Options work is disabled or held by owner/briefing controls.")
        if cfg is not None and current != cfg:
            raise HandoffConflict("Options permission changed during the operation.")
        return current, brief

    def _epoch(self, db, cfg, brief):
        row = db.execute("SELECT rowid,value FROM settings WHERE key='paused'").fetchone()
        return {"global_pause_rowid": row[0] if row else None, "options_revision": cfg["revision"], "brief_revision": brief["revision"]}

    @contextmanager
    def _committing(self, brief):
        # Keep the established MAIN -> BRIEF lock order. The main commit occurs
        # while the brief lease is held; a permission change cannot slip through.
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            @contextmanager
            def fallback():
                if self.controls() != brief:
                    raise HandoffConflict("Brief permission changed.")
                yield
            with self.publication_lease(brief) if self.publication_lease else fallback():
                try:
                    yield db
                except BaseException:
                    db.rollback()
                    raise
                else:
                    db.commit()

    @contextmanager
    def _ownership(self):
        lock = InstanceLock(_allowed_path(self.registry.settings.data_dir / "options-workflow.lock"))
        try:
            lock.acquire()
        except ValueError:
            raise HandoffConflict("Another options owner is active; no duplicate work was started.") from None
        try:
            yield
        finally:
            lock.release()

    def busy(self):
        lock = InstanceLock(_allowed_path(self.registry.settings.data_dir / "options-workflow.lock"))
        try:
            lock.acquire()
        except ValueError:
            return True
        else:
            lock.release()
            return False

    def _data(self, db, *, forward=False):
        if not self._exists(db):
            return None
        row = db.execute("SELECT digest,substr(value,1,?) value,bytes,quality FROM opt_data WHERE id=?", (MAX_DATA + 1, 2 if forward else 1)).fetchone()
        if not row:
            return None
        raw = bytes(row["value"])
        if len(raw) != row["bytes"] or len(raw) > MAX_DATA or _digest(raw) != row["digest"]:
            raise ValueError("Saved options input failed its integrity/size check.")
        from .options_data import load_dataset
        return {"dataset": load_dataset(raw), "raw": raw, "sha256": row["digest"], "quality": _load(row["quality"])}

    def _info(self, db, clock, *, forward=False):
        """Verify saved bytes; reuse import validation facts for passive reads.

        Do not repeatedly parse/validate tens of thousands of contracts merely
        to display a status card. Every mutation revalidates the actual input.
        """
        if not self._exists(db):
            return None
        row = db.execute("SELECT digest,substr(value,1,?) value,bytes,quality FROM opt_data WHERE id=?", (MAX_DATA+1, 2 if forward else 1)).fetchone()
        if not row:
            return None
        raw = bytes(row["value"])
        if len(raw) != row["bytes"] or len(raw) > MAX_DATA or _digest(raw) != row["digest"]:
            raise ValueError("Saved options input failed its integrity/size check.")
        quality = _load(row["quality"])
        from .qqq_data import exchange_day, last_closed_session
        quality["fresh_now"] = quality["quote_time_basis"] == "recorded" and exchange_day(datetime.fromisoformat(quality["last_observed_at"])) == last_closed_session(clock)
        quality["expected_last_session_now"] = last_closed_session(clock).isoformat()
        quality["integrity"] = "saved bytes match accepted SHA-256; provenance remains unverified"
        return {"sha256": row["digest"], "quality": quality}

    def _account(self, db, now=None):
        if not self._exists(db):
            return None
        row = db.execute("SELECT id,state,started_at,policy FROM opt_accounts WHERE closed_at IS NULL ORDER BY rowid DESC LIMIT 1").fetchone()
        if not row:
            return None
        if len(row["state"].encode()) > 2 * 1024 * 1024:
            raise ValueError("Options account exceeds its protected size bound.")
        from .options_recovery import summarize
        state = _load(row["state"])
        summary = summarize(state, now=now)
        return {"id": row["id"], "state": state, "summary": summary, "started_at": row["started_at"], "policy": _load(row["policy"])}

    @staticmethod
    def _arguments(body, extra=()):
        if type(body) is not dict or set(body) != {"request_id", "expected_revision", *extra}:
            raise ValueError("Use only the documented options operation fields.")
        _identifier(body["request_id"])
        if type(body["expected_revision"]) is not int or body["expected_revision"] < 0:
            raise ValueError("Use a nonnegative expected_revision.")

    def _reserve(self, db, action, body, day, now):
        self._schema(db)
        fingerprint = _digest(_json({"action": action, "body": body}).encode())
        old = db.execute("SELECT digest,status,receipt FROM opt_runs WHERE request_id=?", (body["request_id"],)).fetchone()
        if old:
            if old["digest"] != fingerprint or old["status"] != "completed":
                raise HandoffConflict("The earlier options request differs or is uncertain; it cannot be replayed.")
            receipt = _load(old["receipt"])
            if receipt.get("artifacts"):
                self._verify_artifacts(receipt)
            return {**receipt, "replayed": True}
        if db.execute("SELECT 1 FROM opt_runs WHERE status IN ('running','failed') LIMIT 1").fetchone():
            raise HandoffConflict("An uncertain options request requires explicit inspection and acknowledgement.")
        if day and db.execute("SELECT 1 FROM opt_runs WHERE day=?", (day,)).fetchone():
            raise HandoffConflict("This options day is already owned.")
        total = db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM opt_runs").fetchone()
        if total[0] >= MAX_RUNS or total[1] + 16000 > MAX_RECEIPTS:
            raise ValueError("Options receipt quota is full; retained history was not deleted.")
        self._cas(self._meta(db)[0], body["expected_revision"])
        receipt = {"request_id": body["request_id"], "action": action, "day": day, "status": "running", "created_at": now.isoformat(), "artifacts": [], "model_calls": 0, "replayed": False}
        raw = _json(receipt)
        db.execute("INSERT INTO opt_runs VALUES(?,?,?,?,?,?,?)", (body["request_id"], action, day, fingerprint, "running", raw, len(raw.encode())))
        db.execute("UPDATE opt_meta SET revision=revision+1 WHERE id=1")
        return None

    def _finish(self, db, receipt):
        raw = _json(receipt)
        if len(raw.encode()) > 16000:
            raise ValueError("Options compact receipt exceeds its bound.")
        db.execute("UPDATE opt_runs SET status=?,receipt=?,bytes=? WHERE request_id=?", (receipt["status"], raw, len(raw.encode()), receipt["request_id"]))
        db.execute("UPDATE opt_meta SET revision=revision+1 WHERE id=1")

    def _failed(self, receipt, error):
        if receipt:
            receipt.update(status="failed", reason=str(error)[:1200])
            with self.database() as db:
                self._finish(db, receipt)

    def _verify_artifacts(self, receipt):
        identifier = _identifier(receipt["request_id"])
        base = "reports/options-recovery/" + identifier + "/"
        artifacts = receipt.get("artifacts")
        expected = {base + "backtest.json", base + "accepted-input.json"} if receipt["action"] == "backtest" else {base + "archive.json"}
        if type(artifacts) is not list or {item.get("path") for item in artifacts} != expected or len(artifacts) != len(expected):
            raise ValueError("Options artifact contract is incomplete.")
        for item in artifacts:
            raw = self._read(item["path"], MAX_DATA * 2 + 4 * 1024 * 1024)
            if len(raw) != item["bytes"] or _digest(raw) != item["sha256"]:
                raise ValueError("Options evidence is missing or changed; no regeneration is permitted.")
            if item["path"].endswith("accepted-input.json") and _digest(raw) != receipt["data_sha256"]:
                raise ValueError("Options report input identity changed.")
            if item["path"].endswith("backtest.json"):
                from .options_recovery import RULE_SHA256
                report = _load(raw)
                if report.get("rule_sha256") != RULE_SHA256 or report.get("accepted_input_sha256") != receipt["data_sha256"]:
                    raise ValueError("The options backtest belongs to another rule or input.")

    def _read(self, relative, limit=MAX_DATA):
        # registry._path rejects absolute paths, traversal, links and excluded
        # drives before any OS read. The opened file stays pinned to its parent.
        path = self.registry._path(relative)
        with self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path, directory), "rb") as source:
                raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Options input exceeds its byte bound.")
        return raw

    @staticmethod
    def _merge(old, new):
        if old:
            if old["evidence_kind"] != new["evidence_kind"] or any(old["source"][key] != new["source"][key] for key in ("quote_time_basis", "provider", "url", "usage")):
                raise ValueError("Options source timing/basis changed; archive the old basis explicitly.")
            before, after = old["snapshots"], new["snapshots"]
            if len(after) < len(before) or after[:len(before)] != before:
                raise ValueError("Accepted options quote history changed or moved backwards.")
        return new

    def status(self, now=None):
        from .options_recovery import RULE, RULE_SHA256
        clock = _now(now)
        with self.database() as db:
            revision, cfg = self._meta(db)
            data, account = self._info(db, clock), self._account(db, clock)
            forward = self._info(db, clock, forward=True)
            runs = [dict(row) for row in db.execute("SELECT request_id,action,day,status FROM opt_runs ORDER BY rowid DESC LIMIT 10")] if self._exists(db) else []
            held = db.execute("SELECT request_id,action,status,receipt FROM opt_runs WHERE status IN ('running','failed') ORDER BY rowid LIMIT 1").fetchone() if self._exists(db) else None
            last = db.execute("SELECT receipt FROM opt_runs ORDER BY rowid DESC LIMIT 1").fetchone() if self._exists(db) else None
            report = db.execute("SELECT opt_reports.receipt FROM opt_reports JOIN opt_runs USING(request_id) WHERE opt_runs.action='backtest' AND opt_runs.status='completed' ORDER BY opt_reports.rowid DESC LIMIT 1").fetchone() if self._exists(db) else None
            events = [_load(row[0]) for row in db.execute("SELECT value FROM opt_events WHERE account_id=? ORDER BY id DESC LIMIT 20", (account["id"],))] if account else []
        historical = _load(report[0]) if report else None
        if historical:
            self._verify_artifacts(historical)
        quality = data["quality"] if data else None
        hold = {"request_id": held["request_id"], "action": held["action"], "status": held["status"], "reason": _load(held["receipt"]).get("reason", "Interrupted owner operation; inspect before acknowledgment.")} if held else None
        return {"schema": 1, "revision": revision, "config": cfg, "rule": RULE, "rule_sha256": RULE_SHA256,
                "quality": quality, "data_sha256": data["sha256"] if data else None,
                "forward_quality": forward["quality"] if forward else None,
                "forward_data_sha256": forward["sha256"] if forward else None, "paper": account,
                "events": events, "hold": hold, "last_run": _load(last[0]) if last else None, "history": runs,
                "backtest": historical, "inbox": INBOX, "model_calls": 0, "brokerage_connected": False,
                "scope": "Simulated options only. $500 monthly non-recycling premium/fee allowance; no cross-month positions. Missing exits hold the ledger and invalidate cap assurance. No real orders, auto-exercise or verified future profitability."}

    def configure(self, body, now=None):
        if type(body) is not dict or set(body) != {"enabled", "paused", "expected_revision"} or any(type(body[k]) is not bool for k in ("enabled", "paused")):
            raise ValueError("Use explicit enabled, paused and expected_revision only.")
        clock = _now(now)
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._unpaused(db)
            self._schema(db)
            revision, cfg = self._meta(db)
            self._cas(revision, body["expected_revision"])
            if body["enabled"] and (not cfg["enabled"] or cfg["paused"] and not body["paused"]):
                cfg["activation_at"] = clock.isoformat()
            cfg.update(enabled=body["enabled"], paused=body["paused"], revision=cfg["revision"] + 1)
            db.execute("UPDATE opt_meta SET revision=?,config=? WHERE id=1", (revision + 1, _json(cfg)))
        return self.status(clock)

    def refresh(self, body, now=None, *, day=None):
        return self._import(body, INBOX, now, day=day, optional=True)

    def import_data(self, body, now=None):
        self._arguments(body, {"path"})
        # Validate the path before reserving a receipt or inspecting any file.
        self.registry._path(body["path"])
        return self._import(body, body["path"], now)

    def _import(self, body, path, now=None, *, day=None, optional=False):
        self._arguments(body, () if optional else {"path"})
        clock = _now(now)
        receipt = None
        with self._ownership():
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._schema(db)
                    cfg, brief = self._policy(db)
                    replay = self._reserve(db, "refresh" if optional else "import", body, day, clock)
                    if replay:
                        return replay
                    receipt = {"request_id": body["request_id"], "action": "refresh" if optional else "import", "day": day, "status": "running", "created_at": clock.isoformat(), "artifacts": [], "model_calls": 0, "replayed": False}
                try:
                    raw = self._read(path)
                except FileNotFoundError:
                    if not optional:
                        raise
                    with self._committing(brief) as db:
                        self._policy(db, cfg)
                        receipt.update(status="completed", outcome="waiting_for_quotes", reason="No options inbox file; saved state retained. No new prices or fills were invented.")
                        self._finish(db, receipt)
                    return receipt
                from .options_data import load_dataset, canonical, validate_dataset
                dataset = load_dataset(raw, now=clock)
                if dataset["evidence_kind"] != "historical_quotes":
                    raise ValueError("Owner options imports require actual historical quotes, not synthetic fixtures.")
                if optional and dataset["source"]["quote_time_basis"] != "recorded":
                    raise ValueError("The forward inbox requires recorded quote timestamps; assumed-EOD research belongs in a separate historical import.")
                raw = canonical(dataset)
                quality = validate_dataset(dataset, now=clock)
                quality.update(as_of=dataset["as_of"], provider=dataset["source"]["provider"], source_url=dataset["source"]["url"], retrieved_at=dataset["source"]["retrieved_at"])
                with self._committing(brief) as db:
                    self._policy(db, cfg)
                    old = self._data(db, forward=optional)
                    self._merge(old["dataset"] if old else None, dataset)
                    account, advancement = (self._account(db) if optional else None), None
                    if account:
                        if account["policy"] != self._epoch(db, cfg, brief):
                            raise ValueError("Options permission epoch changed; archive this experiment explicitly before a new start.")
                        from .options_recovery import advance_paper
                        advancement = advance_paper(dataset, account["state"], decided_at=clock.isoformat())
                        events = advancement["events"]
                        if db.execute("SELECT count(*) FROM opt_events").fetchone()[0] + len(events) > MAX_EVENTS:
                            raise ValueError("Options event quota is full; no history was removed.")
                        encoded_state = _json(advancement["state"])
                        if len(encoded_state.encode()) > 2 * 1024 * 1024:
                            raise ValueError("Options state exceeds its protected size bound.")
                        db.execute("UPDATE opt_accounts SET state=? WHERE id=?", (encoded_state, account["id"]))
                        for event in events:
                            db.execute("INSERT INTO opt_events(account_id,run_id,value) VALUES(?,?,?)", (account["id"], body["request_id"], _json(event)))
                    db.execute("INSERT OR REPLACE INTO opt_data VALUES(?,?,?,?,?)", (2 if optional else 1, _digest(raw), raw, len(raw), _json(quality)))
                    receipt.update(status="completed", outcome="unchanged" if old and old["sha256"] == _digest(raw) else "imported", data_sha256=_digest(raw), paper_status=advancement["status"] if advancement else "not_started", paper_events=len(advancement["events"]) if advancement else 0)
                    self._finish(db, receipt)
                return receipt
            except Exception as error:
                self._failed(receipt, error)
                raise

    def backtest(self, body, now=None):
        self._arguments(body)
        clock, receipt = _now(now), None
        with self._ownership():
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._schema(db)
                    cfg, brief = self._policy(db)
                    replay = self._reserve(db, "backtest", body, None, clock)
                    if replay:
                        return replay
                    receipt = {"request_id": body["request_id"], "action": "backtest", "status": "running", "created_at": clock.isoformat(), "artifacts": [], "model_calls": 0, "replayed": False}
                    data = self._data(db)
                    if not data:
                        raise ValueError("Import historical option bid/ask quotes before backtesting.")
                from .options_recovery import backtest
                report = backtest(data["dataset"])
                report.update(request_id=body["request_id"], accepted_input_sha256=data["sha256"])
                payload = _json(report).encode()
                if len(payload) > 8 * 1024 * 1024:
                    raise ValueError("Options backtest report exceeds 8 MiB.")
                receipt.update({key: copy.deepcopy(value) for key, value in report.items() if key in {"metrics", "research_gate", "execution_qualified", "symbol", "evidence_kind", "coverage", "rule_sha256"}})
                receipt["data_sha256"] = data["sha256"]
                self._publish(cfg, brief, receipt, (("backtest.json", payload), ("accepted-input.json", data["raw"])))
                return receipt
            except Exception as error:
                self._failed(receipt, error)
                raise

    def _publish(self, cfg, brief, receipt, files, *, final=None):
        size = sum(len(raw) for _, raw in files)
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._unpaused(db)
            if self._meta(db)[1] != cfg:
                raise HandoffConflict("Options controls changed during evidence publication.")
            used = db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM opt_reports").fetchone()
            if used[0] >= 100 or used[1] + size > MAX_REPORTS:
                raise ValueError("Options report/archive quota is full. Failed reservations remain charged.")
            db.execute("INSERT INTO opt_reports VALUES(?,?,?)", (receipt["request_id"], size, _json(receipt)))
        relative = "reports/options-recovery/" + receipt["request_id"]
        folder = self.registry._path(relative)
        def checkpoint():
            with self.database() as db:
                self._unpaused(db)
                if self.stopped.is_set() or self._meta(db)[1] != cfg or self.controls() != brief:
                    raise HandoffConflict("Options/brief permissions changed during evidence publication.")
        with self.registry._directory(folder.parent, create=True):
            folder.mkdir()
            with self.registry._directory(folder):
                for name, raw in files:
                    publish_bytes(folder / name, raw, checkpoint=checkpoint)
                    item = {"path": relative + "/" + name, "bytes": len(raw), "sha256": _digest(raw)}
                    if self._read(item["path"], MAX_DATA * 2 + 4 * 1024 * 1024) != raw:
                        raise ValueError("Options evidence readback failed; original state retained.")
                    receipt["artifacts"].append(item)
        with self._committing(brief) as db:
            self._unpaused(db)
            if self.stopped.is_set() or self._meta(db)[1] != cfg:
                raise HandoffConflict("Options controls changed before evidence commit.")
            if final:
                final(db)
            receipt["status"] = "completed"
            db.execute("UPDATE opt_reports SET receipt=? WHERE request_id=?", (_json(receipt), receipt["request_id"]))
            self._finish(db, receipt)

    def start_paper(self, body, now=None):
        self._arguments(body, {"acknowledge_simulated"})
        if body["acknowledge_simulated"] is not True:
            raise ValueError("Acknowledge that this is a simulated $500 monthly account.")
        clock = _now(now)
        with self._ownership():
            with self.database() as db:
                cfg, brief = self._policy(db)
            with self._committing(brief) as db:
                self._policy(db, cfg)
                replay = self._reserve(db, "paper", body, None, clock)
                if replay:
                    return replay
                if self._account(db):
                    raise HandoffConflict("The existing options experiment must be explicitly archived; no budget reset is permitted.")
                from .qqq_data import exchange_day
                month = exchange_day(clock).strftime("%Y-%m")
                # Archiving cannot mint a new monthly risk allowance, even if
                # the old account is flat or began in a previous month.
                for old in db.execute("SELECT state,started_at,closed_at FROM opt_accounts"):
                    from .options_recovery import validate_state
                    state = _load(old["state"])
                    validate_state(state)
                    used_months = {state["month"], exchange_day(datetime.fromisoformat(old["started_at"])).strftime("%Y-%m")}
                    if old["closed_at"]:
                        used_months.add(exchange_day(datetime.fromisoformat(old["closed_at"])).strftime("%Y-%m"))
                    if state["position"] is not None or not state["cap_qualified"]:
                        raise HandoffConflict("An archived account has unresolved risk; archival cannot qualify a new monthly allowance.")
                    if month in used_months:
                        raise HandoffConflict("This calendar month already belongs to a retained options experiment; archival never resets its $500 allowance.")
                if db.execute("SELECT count(*) FROM opt_accounts").fetchone()[0] >= 32:
                    raise ValueError("Options experiment quota is full.")
                from .options_recovery import initial_state
                state = initial_state(None, started_at=clock.isoformat(), symbol="QQQ")
                identifier = "options-paper-" + uuid.uuid4().hex
                db.execute("INSERT INTO opt_accounts VALUES(?,?,?,NULL,?)", (identifier, _json(state), clock.isoformat(), _json(self._epoch(db, cfg, brief))))
                receipt = {"request_id": body["request_id"], "action": "paper", "status": "completed", "account_id": identifier, "started_at": clock.isoformat(), "simulated_initial_cash": "500.00", "monthly_loss_allowance": "500.00", "artifacts": [], "model_calls": 0, "replayed": False}
                self._finish(db, receipt)
        return receipt

    def acknowledge(self, body):
        self._arguments(body)
        with self._ownership(), self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._unpaused(db)
            self._cas(self._meta(db)[0], body["expected_revision"])
            row = db.execute("SELECT status,receipt FROM opt_runs WHERE request_id=?", (body["request_id"],)).fetchone()
            if not row or row["status"] not in {"running", "failed"}:
                raise ValueError("Choose an uncertain options receipt to acknowledge.")
            receipt = _load(row["receipt"])
            receipt.update(status="acknowledged", acknowledged_at=_now().isoformat(), scope="Inspected only; no partial quotes, fills or artifacts adopted, replayed or deleted.")
            self._finish(db, receipt)
        return receipt

    def archive(self, body, now=None):
        self._arguments(body, {"reason", "archive_data"})
        if type(body["reason"]) is not str or not 1 <= len(body["reason"].strip()) <= 1000 or any(ord(c) < 32 and c not in "\n\t" for c in body["reason"]) or type(body["archive_data"]) is not bool:
            raise ValueError("Supply a bounded reason and explicit archive_data boolean.")
        clock, receipt = _now(now), None
        with self._ownership():
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._unpaused(db)
                    self._schema(db)
                    cfg, brief = self._meta(db)[1], self.controls()
                    # Archiving remains available during a local options pause,
                    # but an explicit brief/global hold still stops publication.
                    if not brief["enabled"] or brief["paused"] or brief.get("execution_hold"):
                        raise HandoffConflict("Brief controls hold options archival.")
                    replay = self._reserve(db, "archive", body, None, clock)
                    if replay:
                        return replay
                    receipt = {"request_id": body["request_id"], "action": "archive", "status": "running", "created_at": clock.isoformat(), "reason": body["reason"].strip(), "artifacts": [], "model_calls": 0, "replayed": False, "synthetic_liquidation": False}
                    account, data = self._account(db), self._data(db)
                    forward = self._data(db, forward=True)
                    if not account and not ((data or forward) and body["archive_data"]):
                        raise ValueError("There is no requested active experiment or input to archive.")
                    payload = _json({"schema": 1, "archived_at": clock.isoformat(), "reason": receipt["reason"], "paper": account,
                                     "data": data["dataset"] if data and body["archive_data"] else None,
                                     "forward_data": forward["dataset"] if forward and body["archive_data"] else None,
                                     "synthetic_liquidation": False}).encode()
                def final(db):
                    if account:
                        db.execute("UPDATE opt_accounts SET closed_at=? WHERE id=?", (clock.isoformat(), account["id"]))
                    if body["archive_data"]:
                        db.execute("DELETE FROM opt_data")
                self._publish(cfg, brief, receipt, (("archive.json", payload),), final=final)
                return receipt
            except Exception as error:
                self._failed(receipt, error)
                raise

    def run_due(self, now=None):
        clock = _now(now)
        from .briefing import _tz
        with self.database() as db:
            revision, cfg = self._meta(db)
            brief = self.controls()
            if self.stopped.is_set() or not cfg["enabled"] or cfg["paused"] or self.store.setting("paused", False) or not brief["enabled"] or brief["paused"] or brief.get("execution_hold"):
                return None
            local = clock.astimezone(_tz(cfg["timezone"]))
            slot = local.replace(hour=6, minute=0, second=0, microsecond=0)
            if clock < slot or cfg["activation_at"] and slot < datetime.fromisoformat(cfg["activation_at"]) or brief.get("activation_at") and slot < datetime.fromisoformat(brief["activation_at"]):
                return None
            day = local.date().isoformat()
            if self._exists(db) and db.execute("SELECT 1 FROM opt_runs WHERE day=? OR status IN ('running','failed') LIMIT 1", (day,)).fetchone():
                return None
        try:
            return self.refresh({"request_id": "options-daily-" + day, "expected_revision": revision}, now=clock, day=day)
        except HandoffConflict:
            return None

    def brief_snapshot(self, now=None):
        status = self.status(now)
        return {key: status[key] for key in ("config", "quality", "forward_quality", "paper", "events", "hold", "last_run", "backtest", "rule_sha256", "model_calls", "scope", "inbox")}
