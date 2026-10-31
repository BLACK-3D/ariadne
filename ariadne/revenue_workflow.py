"""Protected planning and owner-reported business evidence, without external actions.

The workbench never sends an offer, creates an account, collects money, posts a
ledger, or treats a service client's invoice as the owner's income. Cash views
are dated arithmetic over immutable owner reports, not bank reconciliation.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import hashlib
import json
import os

from .atomic import publish_bytes
from .config import _allowed_path
from .handoff import HandoffDesk, HandoffConflict, _identifier, _text
from .locking import InstanceLock

MAX_INPUT = 64 * 1024
MAX_REFERENCE = 256 * 1024
MAX_INPUT_TOTAL = 16 * 1024 * 1024
MAX_REPORT_TOTAL = 64 * 1024 * 1024
MAX_REPORT = 128 * 1024
MAX_RECEIPT = 16 * 1024
MAX_REQUESTS = 100
IMPLEMENTATION = "revenue-workbench-v1"
_KINDS = {"interview", "pilot_agreed", "sale_reported", "payout_received", "cost_paid", "refund_paid", "hours_logged"}
_MONEY_KINDS = {"sale_reported", "payout_received", "cost_paid", "refund_paid"}
_SCOPE = "Owner-reported business observations only; no independently verified sale, bank balance, complete costs, profit or tax conclusion. A service client's invoice is not the owner's service fee."


def _raw(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _hash(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _load(raw, maximum=MAX_REPORT):
    if type(raw) not in (str, bytes) or len(raw) > maximum:
        raise ValueError("Saved revenue metadata exceeds its bound.")
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Duplicate saved revenue fields.")
            result[key] = value
        return result
    def integer(value):
        if len(value) > 16:
            raise ValueError("Saved revenue integer exceeds its bound.")
        return int(value)
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_int=integer,
            parse_float=lambda _: (_ for _ in ()).throw(ValueError("Revenue numbers use exact decimal text.")),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite revenue numbers are prohibited.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Saved revenue metadata is invalid.") from None


def _now(value=None):
    clock = datetime.now(timezone.utc) if value is None else value
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Use an aware revenue observation/storage clock.")
    return clock.astimezone(timezone.utc)


def _stamp(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError("Use a bounded aware ISO observation time.")
    try:
        return _now(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError):
        raise ValueError("Use an aware ISO observation time.") from None


def _event(value, clock):
    keys = {"scenario_id", "kind", "occurred_at", "amount", "minutes", "units", "note", "reference_path"}
    if type(value) is not dict or set(value) != keys:
        raise ValueError("Use exactly the documented business-evidence fields.")
    identifier, kind = _identifier(value["scenario_id"]), value["kind"]
    if type(kind) is not str or kind not in _KINDS:
        raise ValueError("Choose a supported owner-reported business observation.")
    observed = _stamp(value["occurred_at"])
    if observed > clock:
        raise ValueError("Owner business observations cannot be future dated.")
    amount = value["amount"]
    if kind in _MONEY_KINDS:
        from .revenue_planner import money_cents, format_money
        cents = money_cents(amount, "evidence amount")
        if cents is None or cents <= 0 or format_money(cents) != amount:
            raise ValueError("Money evidence requires a positive canonical USD amount such as 10.00.")
    elif amount is not None:
        raise ValueError("This evidence kind has no money amount; use null.")
    minutes, units = value["minutes"], value["units"]
    if kind == "hours_logged":
        if type(minutes) is not int or not 1 <= minutes <= 600000:
            raise ValueError("Log 1–600000 whole owner minutes.")
    elif minutes is not None:
        raise ValueError("Only hours_logged accepts minutes; use null.")
    if kind == "sale_reported":
        if type(units) is not int or not 1 <= units <= 1000000:
            raise ValueError("A reported sale needs 1–1000000 whole units.")
    elif units is not None:
        raise ValueError("Only sale_reported accepts units; use null.")
    reference = value["reference_path"]
    if reference is not None:
        reference = _text(reference, 1000)
    return {"scenario_id": identifier, "kind": kind, "occurred_at": observed.isoformat(),
            "amount": amount, "minutes": minutes, "units": units, "note": _text(value["note"], 1000),
            "reference_path": reference}


class RevenueWorkbench(HandoffDesk):
    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='revenue_meta'").fetchone() is not None

    @staticmethod
    def _schema(db):
        db.execute("CREATE TABLE IF NOT EXISTS revenue_meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,selected_id TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS revenue_runs(request_id TEXT PRIMARY KEY,action TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,receipt TEXT NOT NULL,bytes INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS revenue_inputs(digest TEXT PRIMARY KEY,value BLOB NOT NULL,bytes INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS revenue_reports(request_id TEXT PRIMARY KEY,bytes INTEGER NOT NULL,receipt TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS revenue_scenarios(id TEXT PRIMARY KEY,input_digest TEXT NOT NULL,scenario_digest TEXT NOT NULL,scenario_bytes INTEGER NOT NULL,plan_digest TEXT NOT NULL,plan_bytes INTEGER NOT NULL,plan TEXT NOT NULL,manifest TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS revenue_evidence(id TEXT PRIMARY KEY,scenario_id TEXT NOT NULL,input_digest TEXT NOT NULL,event_digest TEXT NOT NULL,event_bytes INTEGER NOT NULL,event TEXT NOT NULL,source TEXT NOT NULL,manifest TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS revenue_acknowledgements(failed_request_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL)")
        db.execute("INSERT OR IGNORE INTO revenue_meta VALUES(1,0,NULL)")

    def _meta(self, db):
        if not self._exists(db):
            return 0, None
        row = db.execute("SELECT CASE WHEN typeof(revision)='integer' THEN revision ELSE NULL END revision,substr(selected_id,1,65) selected_id,length(CAST(selected_id AS BLOB)) selected_bytes FROM revenue_meta WHERE id=1").fetchone()
        if not row or type(row["revision"]) is not int or not 0 <= row["revision"] <= MAX_REQUESTS * 2:
            raise ValueError("Revenue workbench metadata is missing or changed; no reset is permitted.")
        if row["selected_id"] is not None:
            if not 1 <= row["selected_bytes"] <= 64:
                raise ValueError("The selected revenue scenario identity changed.")
            _identifier(row["selected_id"])
        return row["revision"], row["selected_id"]

    @staticmethod
    def _permission(db):
        row = db.execute("SELECT rowid,typeof(value) kind,length(CAST(value AS BLOB)) bytes,substr(CAST(value AS BLOB),1,17) value FROM settings WHERE key='paused'").fetchone()
        if row:
            if row["kind"] != "text" or not 1 <= row["bytes"] <= 16:
                raise HandoffConflict("Owner pause authority is invalid; revenue changes are held.")
            value = _load(bytes(row["value"]), 16)
            if type(value) is not bool or value:
                raise HandoffConflict("Autonomy is paused; revenue changes are held.")
        return row["rowid"] if row else None

    def _checkpoint(self, epoch):
        with self.database() as db:
            if self._permission(db) != epoch:
                raise HandoffConflict("Owner pause authority changed before revenue publication.")

    @contextmanager
    def _ownership(self):
        lock = InstanceLock(_allowed_path(self.registry.settings.data_dir / "revenue-workbench.lock"))
        with self.lock:
            lock.acquire()
            try:
                yield
            finally:
                lock.release()

    @staticmethod
    def _arguments(body, extra):
        if type(body) is not dict or set(body) != {"request_id", "expected_revision", *extra}:
            raise ValueError("Use exactly the documented revenue request fields.")
        _identifier(body["request_id"])
        if type(body["expected_revision"]) is not int or body["expected_revision"] < 0:
            raise ValueError("Use a nonnegative expected_revision.")

    @staticmethod
    def _hold(db):
        row = db.execute("SELECT substr(r.request_id,1,65) request_id,typeof(r.request_id) kind,length(CAST(r.request_id AS BLOB)) bytes FROM revenue_runs r LEFT JOIN revenue_acknowledgements a ON a.failed_request_id=r.request_id WHERE r.status IN ('running','failed') AND a.request_id IS NULL ORDER BY r.rowid LIMIT 1").fetchone()
        if not row:
            return None
        if row["kind"] != "text" or not 1 <= row["bytes"] <= 64:
            raise ValueError("A held revenue request identity exceeds its bound.")
        metadata = RevenueWorkbench._run_metadata(db, row["request_id"])
        return {"request_id": metadata["request_id"], "status": metadata["status"],
                "reason": "Inspect the failed or interrupted work and partial outputs. It is never replayed or adopted automatically."} if row else None

    @staticmethod
    def _run_metadata(db, identifier):
        """Bound SQL values before Python allocation, including corrupt rows."""
        _identifier(identifier)
        fields = (("request_id", 64), ("action", 16), ("fingerprint", 64), ("status", 16), ("created_at", 40))
        columns = ",".join(f"typeof({name}),length(CAST({name} AS BLOB)),substr(CAST({name} AS BLOB),1,{limit+1})" for name, limit in fields)
        row = db.execute("SELECT " + columns + " FROM revenue_runs WHERE request_id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("A revenue request lost its original metadata.")
        result = {}
        for index, (name, limit) in enumerate(fields):
            kind, size, raw = row[index*3:index*3+3]
            if kind != "text" or type(size) is not int or not 1 <= size <= limit:
                raise ValueError("A revenue request metadata field exceeds its bound.")
            try:
                result[name] = bytes(raw).decode("utf-8")
            except UnicodeError:
                raise ValueError("A revenue request metadata field is invalid.") from None
        if (result["request_id"] != identifier or not _hash(result["fingerprint"])
                or result["action"] not in {"plan", "evidence", "acknowledge"}
                or result["status"] not in {"running", "failed", "completed"}
                or _stamp(result["created_at"]).isoformat() != result["created_at"]):
            raise ValueError("A revenue request metadata identity changed.")
        return result

    def _receipt(self, db, identifier):
        metadata = self._run_metadata(db, identifier)
        row = db.execute("SELECT substr(status,1,32) status,substr(CAST(receipt AS BLOB),1,?) raw,CASE WHEN typeof(bytes)='integer' THEN bytes ELSE NULL END bytes,length(CAST(receipt AS BLOB)) actual FROM revenue_runs WHERE request_id=?", (MAX_RECEIPT + 1, identifier)).fetchone()
        if not row or type(row["bytes"]) is not int or not 1 <= row["bytes"] <= MAX_RECEIPT or row["actual"] != row["bytes"]:
            raise ValueError("A revenue request lost its bounded receipt.")
        receipt = _load(bytes(row["raw"]), MAX_RECEIPT)
        if (type(receipt) is not dict or receipt.get("request_id") != identifier or receipt.get("status") != metadata["status"]
                or receipt.get("action") != metadata["action"] or receipt.get("created_at") != metadata["created_at"]
                or receipt.get("implementation") != IMPLEMENTATION or receipt.get("model_calls") != 0
                or receipt.get("network_calls") != 0):
            raise ValueError("A saved revenue receipt changed.")
        return receipt

    def _reserve(self, db, action, body, clock, *, allow_hold=False):
        self._permission(db)
        self._schema(db)
        fingerprint = _sha(_raw({"action": action, "body": body}))
        old = db.execute("SELECT 1 FROM revenue_runs WHERE request_id=?", (body["request_id"],)).fetchone()
        if old:
            metadata = self._run_metadata(db, body["request_id"])
            if metadata["fingerprint"] != fingerprint or metadata["status"] != "completed":
                raise HandoffConflict("The earlier revenue request differs or is uncertain; it cannot replay.")
            receipt = self._receipt(db, body["request_id"])
            if action == "plan":
                self._scenario(db, body["request_id"], full=True)
            elif action == "evidence":
                self._evidence(db, body["request_id"], full=True)
            return {**receipt, "replayed": True}
        held = self._hold(db)
        if held and not allow_hold:
            raise HandoffConflict("Revenue work is held; inspect and acknowledge its uncertain receipt first.")
        actual, _ = self._meta(db)
        if actual != body["expected_revision"]:
            raise HandoffConflict("Revenue workbench changed; refresh before saving.", revision=actual)
        if db.execute("SELECT count(*) FROM revenue_runs").fetchone()[0] >= MAX_REQUESTS:
            raise ValueError("Revenue request history is full; retained evidence was not deleted.")
        receipt = {"schema": 1, "request_id": body["request_id"], "action": action, "status": "running",
                   "created_at": clock.isoformat(), "implementation": IMPLEMENTATION,
                   "artifacts": [], "model_calls": 0, "network_calls": 0, "replayed": False,
                   "scope": _SCOPE}
        raw = _raw(receipt)
        db.execute("INSERT INTO revenue_runs VALUES(?,?,?,?,?,?,?)", (body["request_id"], action, fingerprint, "running", clock.isoformat(), raw.decode(), len(raw)))
        db.execute("UPDATE revenue_meta SET revision=revision+1 WHERE id=1")
        return receipt

    def _finish(self, db, receipt):
        raw = _raw(receipt)
        if len(raw) > MAX_RECEIPT:
            raise ValueError("Revenue receipt exceeds its compact bound.")
        db.execute("UPDATE revenue_runs SET status=?,receipt=?,bytes=? WHERE request_id=?", (receipt["status"], raw.decode(), len(raw), receipt["request_id"]))
        db.execute("UPDATE revenue_meta SET revision=revision+1 WHERE id=1")

    def _failed(self, receipt, error):
        if receipt and not receipt.get("replayed"):
            receipt.update(status="failed", reason=str(error).encode("utf-8", "replace").decode("utf-8")[:1000])
            with self.database() as db:
                self._finish(db, receipt)

    def _read(self, relative, maximum):
        path = self.registry._path(relative)
        with self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path, directory), "rb") as source:
                before = os.fstat(source.fileno())
                raw = source.read(maximum + 1)
                after = os.fstat(source.fileno())
        if len(raw) > maximum or len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
            raise ValueError("Revenue source changed during reading or exceeds its bound.")
        return raw

    def _put(self, db, raw):
        if not 1 <= len(raw) <= MAX_REFERENCE:
            raise ValueError("Revenue accepted input exceeds its bound.")
        digest = _sha(raw)
        old = db.execute("SELECT bytes,length(value) actual FROM revenue_inputs WHERE digest=?", (digest,)).fetchone()
        if old:
            if old["bytes"] != len(raw) or old["actual"] != len(raw) or self._input(db, digest, full=True) != raw:
                raise ValueError("A retained revenue input changed.")
            return digest
        total = db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM revenue_inputs").fetchone()
        if total[0] >= MAX_REQUESTS * 2 or total[1] + len(raw) > MAX_INPUT_TOTAL:
            raise ValueError("Revenue accepted-input storage is full; nothing was deleted.")
        db.execute("INSERT INTO revenue_inputs VALUES(?,?,?)", (digest, raw, len(raw)))
        return digest

    def _input(self, db, digest, *, full=False):
        if not _hash(digest):
            raise ValueError("Revenue input hash is invalid.")
        row = db.execute("SELECT bytes,length(value) actual,typeof(value) kind FROM revenue_inputs WHERE digest=?", (digest,)).fetchone()
        if not row or type(row["bytes"]) is not int or not 1 <= row["bytes"] <= MAX_REFERENCE or row["actual"] != row["bytes"] or row["kind"] != "blob":
            raise ValueError("A retained revenue input is missing or changed.")
        if full:
            raw = bytes(db.execute("SELECT substr(value,1,?) FROM revenue_inputs WHERE digest=?", (row["bytes"] + 1, digest)).fetchone()[0])
            if len(raw) != row["bytes"] or _sha(raw) != digest:
                raise ValueError("A retained revenue input failed its byte hash.")
            return raw
        return row["bytes"]

    def _verify_files(self, db, identifier, expected, *, full=False):
        receipt = self._receipt(db, identifier)
        report = db.execute("SELECT bytes,substr(CAST(receipt AS BLOB),1,?) raw,length(CAST(receipt AS BLOB)) actual FROM revenue_reports WHERE request_id=?", (MAX_RECEIPT + 1, identifier)).fetchone()
        if not report or report["actual"] > MAX_RECEIPT or _load(bytes(report["raw"]), MAX_RECEIPT) != receipt or receipt["status"] != "completed":
            raise ValueError("Revenue evidence lacks matching completed receipts.")
        artifacts = receipt.get("artifacts")
        base = "reports/revenue-workbench/" + identifier + "/"
        if (type(artifacts) is not list or len(artifacts) != len(expected)
                or any(type(item) is not dict or set(item) != {"path", "bytes", "sha256"} for item in artifacts)
                or {item["path"] for item in artifacts} != {base + name for name in expected}
                or sum(item["bytes"] for item in artifacts) != report["bytes"]):
            raise ValueError("Revenue evidence lost its complete artifact set.")
        result = {}
        for item in artifacts:
            if type(item["bytes"]) is not int or not 1 <= item["bytes"] <= MAX_REPORT or not _hash(item["sha256"]):
                raise ValueError("Revenue artifact metadata changed.")
            if full:
                raw = self._read(item["path"], MAX_REPORT)
                if len(raw) != item["bytes"] or _sha(raw) != item["sha256"]:
                    raise ValueError("Revenue evidence is missing or changed; it is never regenerated.")
                result[item["path"][len(base):]] = raw
        return receipt, result

    def _scenario(self, db, identifier, *, full=False):
        _identifier(identifier)
        size = db.execute("SELECT typeof(scenario_bytes),typeof(plan_bytes),scenario_bytes BETWEEN 1 AND ?,plan_bytes BETWEEN 1 AND ?,length(CAST(plan AS BLOB)),length(CAST(manifest AS BLOB)),length(input_digest),length(scenario_digest),length(plan_digest) FROM revenue_scenarios WHERE id=?", (MAX_INPUT, MAX_REPORT, identifier)).fetchone()
        if not size or size[0] != "integer" or size[1] != "integer" or size[2] != 1 or size[3] != 1 or not 1 <= size[4] <= MAX_REPORT or not 1 <= size[5] <= MAX_RECEIPT or any(value != 64 for value in size[6:]):
            raise ValueError("Revenue scenario metadata is missing or exceeds its bound.")
        row = db.execute("SELECT *,length(CAST(plan AS BLOB)) actual FROM revenue_scenarios WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ValueError("The selected revenue scenario is missing.")
        if (type(row["scenario_bytes"]) is not int or not 1 <= row["scenario_bytes"] <= MAX_INPUT
                or type(row["plan_bytes"]) is not int or not 1 <= row["plan_bytes"] <= MAX_REPORT
                or row["actual"] != row["plan_bytes"] or not _hash(row["scenario_digest"]) or not _hash(row["plan_digest"])):
            raise ValueError("Revenue scenario metadata changed.")
        plan_raw = row["plan"].encode("utf-8")
        if _sha(plan_raw) != row["plan_digest"]:
            raise ValueError("The saved revenue plan changed.")
        plan, manifest = _load(plan_raw), _load(row["manifest"], MAX_RECEIPT)
        if (type(plan) is not dict or plan.get("schema") != 1 or plan.get("planning_only") is not True
                or plan.get("model_calls") != 0 or plan.get("network_calls") != 0
                or type(manifest) is not dict or manifest.get("id") != identifier
                or _raw(plan) != plan_raw
                or manifest.get("input_sha256") != row["input_digest"] or manifest.get("plan_sha256") != row["plan_digest"]):
            raise ValueError("The saved revenue scenario lost its planning/source identity.")
        receipt, saved = self._verify_files(db, identifier, {"scenario.json", "plan.json"}, full=full)
        expected = {"scenario.json": (row["scenario_digest"], row["scenario_bytes"]), "plan.json": (row["plan_digest"], row["plan_bytes"])}
        base = "reports/revenue-workbench/" + identifier + "/"
        expected_manifest = {"id": identifier, "label": plan["label"], "created_at": receipt["created_at"],
                             "offer_count": len(plan["offers"]), "input_sha256": row["input_digest"], "plan_sha256": row["plan_digest"]}
        if (receipt.get("action") != "plan" or receipt.get("scenario_id") != identifier
                or receipt.get("input_sha256") != row["input_digest"] or receipt.get("plan_sha256") != row["plan_digest"]
                or receipt.get("planning_only") is not True or manifest != expected_manifest):
            raise ValueError("Revenue scenario receipt/source identity changed.")
        for item in receipt["artifacts"]:
            if (item["sha256"], item["bytes"]) != expected[item["path"][len(base):]]:
                raise ValueError("Revenue plan/cache artifact metadata disagree.")
        original = self._input(db, row["input_digest"], full=full)
        scenario = None
        if full:
            body = _load(original, MAX_INPUT)
            self._arguments(body, {"scenario"})
            if body["request_id"] != identifier or _sha(_raw({"action": "plan", "body": body})) != self._run_metadata(db, identifier)["fingerprint"]:
                raise ValueError("The original revenue scenario request changed.")
            from .revenue_planner import validate, canonical
            scenario = validate(body["scenario"])
            if saved["scenario.json"] != canonical(scenario) or saved["plan.json"] != plan_raw:
                raise ValueError("Revenue plan artifacts lost their accepted source bytes.")
        return {"id": identifier, "scenario": scenario, "plan": plan, "manifest": manifest, "receipt": receipt, "input_digest": row["input_digest"]}

    def _evidence(self, db, identifier, *, full=False):
        _identifier(identifier)
        size = db.execute("SELECT typeof(event_bytes),event_bytes BETWEEN 1 AND ?,length(CAST(event AS BLOB)),length(CAST(source AS BLOB)),length(CAST(manifest AS BLOB)),length(scenario_id),length(input_digest),length(event_digest) FROM revenue_evidence WHERE id=?", (MAX_INPUT, identifier)).fetchone()
        if not size or size[0] != "integer" or size[1] != 1 or not 1 <= size[2] <= MAX_INPUT or not 1 <= size[3] <= MAX_RECEIPT or not 1 <= size[4] <= MAX_RECEIPT or not 1 <= size[5] <= 64 or any(value != 64 for value in size[6:]):
            raise ValueError("Owner evidence metadata is missing or exceeds its bound.")
        row = db.execute("SELECT *,length(CAST(event AS BLOB)) actual FROM revenue_evidence WHERE id=?", (identifier,)).fetchone()
        if not row or type(row["event_bytes"]) is not int or not 1 <= row["event_bytes"] <= MAX_INPUT or row["actual"] != row["event_bytes"]:
            raise ValueError("Saved owner evidence is missing or exceeds its bound.")
        event_raw = row["event"].encode("utf-8")
        if _sha(event_raw) != row["event_digest"]:
            raise ValueError("Saved owner evidence changed.")
        event, source, manifest = _load(event_raw, MAX_INPUT), _load(row["source"], MAX_RECEIPT), _load(row["manifest"], MAX_RECEIPT)
        if any(type(value) is not dict for value in (event, source, manifest)):
            raise ValueError("Owner evidence objects lost their documented shape.")
        receipt, saved = self._verify_files(db, identifier, {"event.json"}, full=full)
        if (receipt.get("action") != "evidence" or receipt.get("scenario_id") != row["scenario_id"]
                or receipt.get("input_sha256") != row["input_digest"] or source.get("input_sha256") != row["input_digest"]
                or manifest.get("id") != identifier or manifest.get("scenario_id") != row["scenario_id"]
                or receipt.get("event_sha256") != row["event_digest"] or event.get("source") != source
                or _raw(event) != event_raw
                or receipt["artifacts"][0]["sha256"] != row["event_digest"] or receipt["artifacts"][0]["bytes"] != row["event_bytes"]):
            raise ValueError("Owner evidence lost its receipt/source identity.")
        clock = _stamp(receipt["created_at"])
        if _event(event["event"], clock) != event["event"] or event.get("owner_reported") is not True or event.get("independently_verified") is not False or event["event"]["scenario_id"] != row["scenario_id"]:
            raise ValueError("Owner evidence is not canonical or changed its verification scope.")
        if manifest != {"id": identifier, "scenario_id": row["scenario_id"], "kind": event["event"]["kind"],
                        "occurred_at": event["event"]["occurred_at"], "created_at": receipt["created_at"], "event_sha256": row["event_digest"]}:
            raise ValueError("The owner evidence manifest changed.")
        if source.get("reference_path") != event["event"]["reference_path"]:
            raise ValueError("The owner evidence reference lost its original path identity.")
        if not db.execute("SELECT 1 FROM revenue_scenarios WHERE id=?", (row["scenario_id"],)).fetchone():
            raise ValueError("Owner evidence lost its accepted scenario.")
        original = self._input(db, row["input_digest"], full=full)
        reference_digest = source.get("reference_sha256")
        if reference_digest is not None:
            size = self._input(db, reference_digest, full=False)
            if size != source.get("reference_bytes"):
                raise ValueError("The accepted evidence reference changed size.")
            if full:
                self._input(db, reference_digest, full=True)
        elif source.get("reference_bytes") is not None or event["event"]["reference_path"] is not None:
            raise ValueError("The accepted evidence reference is incomplete.")
        if full:
            body = _load(original, MAX_INPUT)
            self._arguments(body, {"event"})
            if body["request_id"] != identifier or _event(body["event"], clock) != event["event"] or saved["event.json"] != event_raw:
                raise ValueError("Owner evidence lost its original request or immutable output.")
            if _sha(_raw({"action": "evidence", "body": body})) != self._run_metadata(db, identifier)["fingerprint"]:
                raise ValueError("The original owner evidence request changed.")
        return {"id": identifier, "event": event["event"], "source": source, "manifest": manifest, "receipt": receipt}

    def _audit(self, db, *, full=False):
        if not self._exists(db):
            return [], []
        for table in ("revenue_runs", "revenue_reports", "revenue_scenarios", "revenue_evidence", "revenue_acknowledgements"):
            if db.execute("SELECT count(*) FROM " + table).fetchone()[0] > MAX_REQUESTS:
                raise ValueError("Revenue retained history exceeds its fixed request bound.")
        for table in ("revenue_scenarios", "revenue_evidence"):
            if db.execute("SELECT 1 FROM " + table + " WHERE typeof(id)!='text' OR length(CAST(id AS BLOB)) NOT BETWEEN 1 AND 64 LIMIT 1").fetchone():
                raise ValueError("Revenue retained entity identity changed.")
        bounds = (("revenue_runs", (("request_id",64),("action",16),("fingerprint",64),("status",16),("created_at",40))),
                  ("revenue_acknowledgements", (("failed_request_id",64),("request_id",64))))
        for table, columns in bounds:
            for column, maximum in columns:
                minimum = 64 if column == "fingerprint" else 1
                if db.execute(f"SELECT 1 FROM {table} WHERE typeof({column})!='text' OR length(CAST({column} AS BLOB)) NOT BETWEEN {minimum} AND {maximum} LIMIT 1").fetchone():
                    raise ValueError("Revenue retained request metadata exceeds its bound.")
        if db.execute("SELECT 1 FROM revenue_runs WHERE action NOT IN ('plan','evidence','acknowledge') OR status NOT IN ('running','failed','completed') OR fingerprint GLOB '*[^0-9a-f]*' LIMIT 1").fetchone():
            raise ValueError("Revenue retained request metadata identity changed.")
        input_stats = db.execute("SELECT count(*),coalesce(sum(bytes),0),coalesce(sum(CASE WHEN typeof(value)!='blob' OR typeof(bytes)!='integer' OR bytes<1 OR bytes>? OR length(value)!=bytes THEN 1 ELSE 0 END),0) FROM revenue_inputs", (MAX_REFERENCE,)).fetchone()
        if input_stats[0] > MAX_REQUESTS * 2 or input_stats[2]:
            raise ValueError("Revenue accepted-input metadata changed.")
        sizes = input_stats[1]
        if sizes > MAX_INPUT_TOTAL or db.execute("SELECT coalesce(sum(bytes),0) FROM revenue_reports").fetchone()[0] > MAX_REPORT_TOTAL:
            raise ValueError("Revenue retained byte quota changed.")
        scenarios = [self._scenario(db, row[0], full=full) for row in db.execute("SELECT id FROM revenue_scenarios ORDER BY rowid LIMIT ?", (MAX_REQUESTS + 1,)).fetchall()]
        events = [self._evidence(db, row[0], full=full) for row in db.execute("SELECT id FROM revenue_evidence ORDER BY rowid LIMIT ?", (MAX_REQUESTS + 1,)).fetchall()]
        expected = {value["id"] for value in scenarios + events}
        actual = {row[0] for row in db.execute("SELECT substr(request_id,1,65) FROM revenue_runs WHERE status='completed' AND action IN ('plan','evidence') LIMIT ?", (MAX_REQUESTS + 1,))}
        if actual != expected or len(expected) != len(scenarios) + len(events):
            raise ValueError("Revenue accepted entities disagree with completed history.")
        acknowledged = set()
        for row in db.execute("SELECT substr(a.failed_request_id,1,65) failed_request_id,substr(a.request_id,1,65) request_id,substr(r.status,1,17) status,substr(r.action,1,17) action FROM revenue_acknowledgements a LEFT JOIN revenue_runs r ON r.request_id=a.request_id LIMIT ?", (MAX_REQUESTS + 1,)):
            receipt = self._receipt(db, row["request_id"])
            target = self._run_metadata(db, row["failed_request_id"])
            if row["status"] != "completed" or row["action"] != "acknowledge" or receipt.get("failed_request_id") != row["failed_request_id"] or target["status"] not in {"failed", "running"}:
                raise ValueError("Revenue inspection acknowledgement lost its original target.")
            acknowledged.add(row["request_id"])
        if acknowledged != {row[0] for row in db.execute("SELECT substr(request_id,1,65) FROM revenue_runs WHERE status='completed' AND action='acknowledge'")}:
            raise ValueError("Revenue inspection history lost its acknowledgement mapping.")
        return scenarios, events

    @staticmethod
    def _cash(events, clock):
        from .revenue_planner import money_cents, format_money
        month = clock.strftime("%Y-%m")
        totals = {kind: 0 for kind in _MONEY_KINDS}
        minutes = units = interviews = pilots = count = 0
        for item in events:
            event = item["event"]
            if _stamp(event["occurred_at"]).strftime("%Y-%m") != month:
                continue
            count += 1
            kind = event["kind"]
            if kind in totals:
                totals[kind] += money_cents(event["amount"])
            if kind == "hours_logged":minutes += event["minutes"]
            if kind == "sale_reported":units += event["units"]
            if kind == "interview":interviews += 1
            if kind == "pilot_agreed":pilots += 1
        return {"status": "reported_movements" if count else "no_evidence", "month": month, "timezone": "UTC", **{kind: format_money(total) for kind, total in totals.items()},
                "net_reported_cash": format_money(totals["payout_received"] - totals["cost_paid"] - totals["refund_paid"]),
                "units_reported": units, "owner_minutes_logged": minutes, "interviews": interviews,
                "pilot_agreements": pilots, "evidence_count": count, "owner_reported": True,
                "independently_verified": False, "cash_costs_complete": False,
                "scope": "Payouts minus separately paid costs and refunds, across all scenarios in this UTC occurrence month. Reported sales and pending payouts are not cash. This is not verified profit, bank balance or complete cost coverage. Do not deduct provider-withheld fees twice."}

    def status(self, now=None):
        clock = _now(now)
        with self.database() as db:
            revision, selected = self._meta(db)
            scenarios, events, held, latest, plan = [], [], None, None, None
            integrity_error = False
            if self._exists(db):
                held = self._hold(db)
                row = db.execute("SELECT substr(request_id,1,65) request_id,typeof(request_id) kind,length(CAST(request_id AS BLOB)) bytes FROM revenue_runs ORDER BY rowid DESC LIMIT 1").fetchone()
                if row and (row["kind"] != "text" or not 1 <= row["bytes"] <= 64):
                    raise ValueError("The latest revenue request identity exceeds its bound.")
                latest = self._receipt(db, row[0]) if row else None
                try:
                    # Owner-visible money totals require the retained original
                    # evidence, including reference bytes and every artifact.
                    scenarios, events = self._audit(db, full=True)
                    if selected:
                        chosen = next((value for value in scenarios if value["id"] == selected), None)
                        if chosen is None:
                            raise ValueError("The selected revenue plan is missing.")
                        plan = chosen["plan"]
                        item = next(item for item in chosen["receipt"]["artifacts"] if item["path"].endswith("/plan.json"))
                        if self._read(item["path"], MAX_REPORT) != _raw(plan):
                            raise ValueError("The selected revenue plan artifact changed.")
                except (ValueError, OSError, KeyError, TypeError) as error:
                    integrity_error = True
                    held = held or {"request_id": selected, "status": "integrity_error", "reason": str(error)[:1000]}
                    scenarios, events, plan = [], [], None
            cash = self._cash(events, clock)
            if integrity_error:
                cash.update(status="unavailable", **{key: None for key in (*_MONEY_KINDS, "net_reported_cash", "units_reported", "owner_minutes_logged", "interviews", "pilot_agreements", "evidence_count")})
            return {"schema": 1, "revision": revision, "selected_scenario_id": selected, "plan": plan,
                    "scenarios": [value["manifest"] for value in scenarios],
                    "evidence": [{"id": value["id"], **value["event"], "source": value["source"], "owner_reported": True, "independently_verified": False} for value in events],
                    "cash_summary": cash, "last_run": latest, "hold": held,
                    "model_calls": 0, "network_calls": 0, "offline": True, "scope": _SCOPE,
                    "limits": {"requests": MAX_REQUESTS, "input_bytes": MAX_INPUT, "reference_bytes": MAX_REFERENCE,
                               "retained_input_bytes": MAX_INPUT_TOTAL, "report_bytes": MAX_REPORT_TOTAL}}

    def plan(self, body, now=None):
        self._arguments(body, {"scenario"})
        from .revenue_planner import validate
        validate(body["scenario"])
        return self._accept("plan", body, _now(now))

    def detail(self, scenario_id):
        """Inspect an older immutable plan without selecting or recomputing it."""
        _identifier(scenario_id)
        with self.database() as db:
            if not self._exists(db):
                raise ValueError("No revenue scenario has been accepted.")
            value = self._scenario(db, scenario_id, full=True)
            return {key: copy.deepcopy(value[key]) for key in ("id", "scenario", "plan", "receipt")} | {"artifacts": copy.deepcopy(value["receipt"]["artifacts"])}

    def record_evidence(self, body, now=None):
        self._arguments(body, {"event"})
        clock = _now(now)
        event = _event(body["event"], clock)
        if event["reference_path"] is not None:
            self.registry._path(event["reference_path"])
        return self._accept("evidence", body, clock)

    def _accept(self, action, body, clock):
        original = _raw(body)
        if len(original) > MAX_INPUT:
            raise ValueError("Revenue request input exceeds 64 KiB.")
        receipt = None
        with self._ownership():
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    epoch = self._permission(db)
                    receipt = self._reserve(db, action, body, clock)
                    if receipt.get("replayed"):
                        return receipt
                with self.database() as db:
                    self._audit(db, full=True)
                    if action == "evidence":
                        self._scenario(db, body["event"]["scenario_id"], full=True)
                self._checkpoint(epoch)
                reference_raw = None
                if action == "plan":
                    from .revenue_planner import validate, canonical, calculate
                    scenario_raw = canonical(validate(body["scenario"]))
                    plan = calculate(body["scenario"])
                    plan_raw = _raw(plan)
                    manifest = {"id": body["request_id"], "label": plan["label"], "created_at": clock.isoformat(),
                                "offer_count": len(plan["offers"]), "input_sha256": _sha(original), "plan_sha256": _sha(plan_raw)}
                    files = (("scenario.json", scenario_raw), ("plan.json", plan_raw))
                    receipt.update(scenario_id=body["request_id"], input_sha256=_sha(original), plan_sha256=_sha(plan_raw), planning_only=True)
                else:
                    event = _event(body["event"], clock)
                    if event["reference_path"] is not None:
                        reference_raw = self._read(event["reference_path"], MAX_REFERENCE)
                    source = {"input_sha256": _sha(original), "reference_path": event["reference_path"],
                              "reference_sha256": _sha(reference_raw) if reference_raw is not None else None,
                              "reference_bytes": len(reference_raw) if reference_raw is not None else None,
                              "reference_scope": "A private byte copy of owner-supplied support; existence and hashing do not authenticate payment or consent."}
                    payload = {"schema": 1, "event": event, "source": source, "owner_reported": True,
                               "independently_verified": False, "scope": _SCOPE}
                    event_raw = _raw(payload)
                    manifest = {"id": body["request_id"], "scenario_id": event["scenario_id"], "kind": event["kind"],
                                "occurred_at": event["occurred_at"], "created_at": clock.isoformat(), "event_sha256": _sha(event_raw)}
                    files = (("event.json", event_raw),)
                    receipt.update(scenario_id=event["scenario_id"], input_sha256=_sha(original), event_sha256=_sha(event_raw),
                                   owner_reported=True, independently_verified=False)
                if any(len(raw) > MAX_REPORT for _, raw in files):
                    raise ValueError("Revenue output exceeds its per-report bound.")
                size = sum(len(raw) for _, raw in files)
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if self._permission(db) != epoch:
                        raise HandoffConflict("Owner pause authority changed before accepting revenue sources.")
                    self._put(db, original)
                    if reference_raw is not None:
                        self._put(db, reference_raw)
                    total = db.execute("SELECT coalesce(sum(bytes),0) FROM revenue_reports").fetchone()[0]
                    if total + size > MAX_REPORT_TOTAL:
                        raise ValueError("Revenue report storage is full; failed reservations remain charged.")
                    db.execute("INSERT INTO revenue_reports VALUES(?,?,?)", (body["request_id"], size, _raw(receipt).decode()))
                self._checkpoint(epoch)
                relative = "reports/revenue-workbench/" + body["request_id"]
                folder = self.registry._path(relative)
                with self.registry._directory(folder.parent, create=True):
                    folder.mkdir()
                    with self.registry._directory(folder):
                        for name, raw in files:
                            publish_bytes(folder / name, raw, checkpoint=lambda: self._checkpoint(epoch))
                            item = {"path": relative + "/" + name, "bytes": len(raw), "sha256": _sha(raw)}
                            if self._read(item["path"], MAX_REPORT) != raw:
                                raise ValueError("Revenue publication readback failed; original state retained.")
                            receipt["artifacts"].append(item)
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if self._permission(db) != epoch:
                        raise HandoffConflict("Owner pause authority changed before revenue commit.")
                    self._audit(db, full=True)
                    if self._input(db, _sha(original), full=True) != original:
                        raise ValueError("The new revenue input changed before publication commit.")
                    if reference_raw is not None and self._input(db, _sha(reference_raw), full=True) != reference_raw:
                        raise ValueError("The new evidence reference changed before publication commit.")
                    for item, (_, raw) in zip(receipt["artifacts"], files):
                        if self._read(item["path"], MAX_REPORT) != raw:
                            raise ValueError("The new revenue artifact changed before commit.")
                    if action == "plan":
                        db.execute("INSERT INTO revenue_scenarios VALUES(?,?,?,?,?,?,?,?)", (body["request_id"], _sha(original), _sha(scenario_raw), len(scenario_raw), _sha(plan_raw), len(plan_raw), plan_raw.decode(), _raw(manifest).decode()))
                        db.execute("UPDATE revenue_meta SET selected_id=? WHERE id=1", (body["request_id"],))
                    else:
                        db.execute("INSERT INTO revenue_evidence VALUES(?,?,?,?,?,?,?,?)", (body["request_id"], event["scenario_id"], _sha(original), _sha(event_raw), len(event_raw), event_raw.decode(), _raw(source).decode(), _raw(manifest).decode()))
                    receipt["status"] = "completed"
                    db.execute("UPDATE revenue_reports SET receipt=? WHERE request_id=?", (_raw(receipt).decode(), body["request_id"]))
                    self._finish(db, receipt)
                return receipt
            except Exception as error:
                self._failed(receipt, error)
                raise

    def acknowledge(self, body, now=None):
        self._arguments(body, {"failed_request_id", "reason"})
        _identifier(body["failed_request_id"])
        reason = _text(body["reason"], 1000)
        clock, receipt = _now(now), None
        with self._ownership():
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    epoch = self._permission(db)
                    self._schema(db)
                    old = db.execute("SELECT 1 FROM revenue_runs WHERE request_id=?", (body["request_id"],)).fetchone()
                    if old:
                        return self._reserve(db, "acknowledge", body, clock, allow_hold=True)
                    target = self._run_metadata(db, body["failed_request_id"])
                    if target["status"] not in {"failed", "running"} or db.execute("SELECT 1 FROM revenue_acknowledgements WHERE failed_request_id=?", (body["failed_request_id"],)).fetchone():
                        raise HandoffConflict("Choose one unacknowledged failed/interrupted revenue request after inspection.")
                    receipt = self._reserve(db, "acknowledge", body, clock, allow_hold=True)
                with self.database() as db:
                    self._audit(db, full=True)
                self._checkpoint(epoch)
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if self._permission(db) != epoch:
                        raise HandoffConflict("Owner pause authority changed before inspection acknowledgement.")
                    self._audit(db, full=True)
                    db.execute("INSERT INTO revenue_acknowledgements VALUES(?,?)", (body["failed_request_id"], body["request_id"]))
                    receipt.update(status="completed", failed_request_id=body["failed_request_id"], reason=reason,
                                   adopted=False, scope="Inspection acknowledged only. No earlier data or output was replayed, adopted, overwritten or deleted.")
                    self._finish(db, receipt)
                return receipt
            except Exception as error:
                self._failed(receipt, error)
                raise

    def brief_snapshot(self, now=None):
        clock = _now(now)
        value = self.status(clock)
        result = {"schema": 1, "status": "held" if value["hold"] else "not_configured" if value["selected_scenario_id"] is None else "available",
                  "scenario_id": value["selected_scenario_id"], "plan_summary": None,
                  "cash_summary": value["cash_summary"], "hold": copy.deepcopy(value["hold"]),
                  "last_run": {key: value["last_run"].get(key) for key in ("request_id", "action", "status", "created_at")} if value["last_run"] else None,
                  "model_calls": 0, "network_calls": 0, "scope": _SCOPE}
        if value["selected_scenario_id"] is not None:
            try:
                with self.database() as db:
                    selected = self._scenario(db, value["selected_scenario_id"], full=True)
                    _, evidence = self._audit(db, full=True)
                plan = selected["plan"]
                result["cash_summary"] = self._cash(evidence, clock)
                result["plan_summary"] = {key: copy.deepcopy(plan[key]) for key in
                    ("label", "planning_only", "system_cost_basis", "system_cost_used", "unknown_fixed_cost_fields", "monthly_profit_target", "trading_income_budgeted")}
                result["plan_summary"].update(offer_count=len(plan["offers"]), offers=[{
                    "id": item["id"], "name": item["name"], "kind": item["kind"],
                    "cash_contribution_per_unit": item["per_unit"]["cash_contribution"],
                    "minimum_units_cash_target": item["target"]["minimum_units_cash"],
                    "unit_costs_complete": item["unit_costs_complete"]} for item in plan["offers"][:3]],
                    omitted_offers=max(0, len(plan["offers"]) - 3))
            except (ValueError, OSError, KeyError, TypeError) as error:
                result.update(status="unavailable", plan_summary=None,
                              hold={"request_id": value["selected_scenario_id"], "status": "integrity_error", "reason": str(error)[:400]})
                result["cash_summary"].update(status="unavailable", **{key: None for key in (*_MONEY_KINDS, "net_reported_cash", "units_reported", "owner_minutes_logged", "interviews", "pilot_agreements", "evidence_count")})
        if len(_raw(result)) > 8192:
            raise ValueError("Revenue brief snapshot exceeds its compact bound.")
        return result
