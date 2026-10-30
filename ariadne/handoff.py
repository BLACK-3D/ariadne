"""A protected, fictional quote/invoice handoff desk; no contacts or ledger writes.

All decisions here concern demonstration records. A demo approval never proves
customer consent, legal tax treatment, completed real work, payment or delivery.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

from .atomic import publish_bytes
from .business_ops import _cents, _money, _round_ratio
from .config import _allowed_path

MAX_RECORDS = 20
MAX_LINES = 20
MAX_SOURCE = 256 * 1024
MAX_OUTPUT = 256 * 1024
MAX_REQUESTS = 500
MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_CENTS = 10**12
DEMO_PROVIDER = {"name": "DEMO Example Cleaning Company", "address": "900 Fictional Service Lane, Fictional City",
                 "email": "owner@example.invalid", "demo_only": True}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_EDITABLE = {"customer", "title", "service_date", "document_date", "due_date", "source_notes", "lines", "tax", "work"}
_RECORD_FIELDS = _EDITABLE | {"id", "kind"}


class HandoffConflict(ValueError):
    def __init__(self, message, *, revision=None):
        self.revision = revision
        super().__init__(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _bytes(value):
    return (_json(value) + "\n").encode("utf-8")


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _load(raw):
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise ValueError("Duplicate handoff JSON keys are not accepted.")
            value[key] = item
        return value
    def integer(value):
        if len(value) > 16:
            raise ValueError("Handoff integer literal is oversized.")
        return int(value)
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_int=integer,
                          parse_float=lambda _: (_ for _ in ()).throw(ValueError("Use exact decimal text for handoff money.")),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite handoff JSON is unsupported.")))
    except RecursionError:
        raise ValueError("Handoff JSON is too deeply nested.") from None


def _fields(value, allowed, required=()):
    if type(value) is not dict or set(value) - allowed or set(required) - set(value):
        raise ValueError("Missing or unsupported handoff fields.")


def _text(value, maximum=200, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not str or not value.strip() or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("Handoff text must be bounded, nonempty and contain no control characters.")
    return value.strip()


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("Handoff IDs require 1–64 letters, digits, underscores or hyphens.")
    return value


def _day(value):
    if value is None:
        return None
    if type(value) is not str or len(value) != 10:
        raise ValueError("Handoff dates require YYYY-MM-DD or null.")
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("Handoff dates require YYYY-MM-DD.")
    return value


def validate_record(value):
    _fields(value, _RECORD_FIELDS, _RECORD_FIELDS)
    identifier = _identifier(value["id"])
    if value["kind"] not in ("quote", "invoice"):
        raise ValueError("Handoff records are quotes or invoices.")
    customer = value["customer"]
    _fields(customer, {"name", "email", "address"}, {"name", "email", "address"})
    result = {"id": identifier, "kind": value["kind"], "title": _text(value["title"]),
              "customer": {key: _text(customer[key], 300, optional=True) for key in ("name", "email", "address")},
              "source_notes": _text(value["source_notes"], 2000),
              **{key: _day(value[key]) for key in ("service_date", "document_date", "due_date")}}
    if type(value["lines"]) is not list or not 1 <= len(value["lines"]) <= MAX_LINES:
        raise ValueError("A handoff record requires 1–20 lines.")
    result["lines"] = []
    for line in value["lines"]:
        _fields(line, {"description", "quantity", "unit_price", "price_approved", "source_ref"},
                {"description", "quantity", "unit_price", "price_approved", "source_ref"})
        if type(line["quantity"]) is not int or not 1 <= line["quantity"] <= 10000 or type(line["price_approved"]) is not bool:
            raise ValueError("Use positive whole-number quantities and an explicit price-approval boolean.")
        price = line["unit_price"]
        if price is not None:
            if type(price) is not str or _cents(price) > MAX_CENTS:
                raise ValueError("Handoff unit prices require bounded exact decimal text or null.")
            price = _money(_cents(price))
            if _cents(price) * line["quantity"] > MAX_CENTS:
                raise ValueError("Handoff line amount exceeds the supported bound.")
        result["lines"].append({"description": _text(line["description"], 300), "quantity": line["quantity"],
                                "unit_price": price, "price_approved": line["price_approved"],
                                "source_ref": _text(line["source_ref"], 300, optional=True)})
    tax = value["tax"]
    _fields(tax, {"bps", "approved", "source_ref"}, {"bps", "approved", "source_ref"})
    if tax["bps"] is not None and (type(tax["bps"]) is not int or not 0 <= tax["bps"] <= 10000) or type(tax["approved"]) is not bool:
        raise ValueError("Handoff tax requires 0–10000 basis points or null and an explicit approval boolean.")
    result["tax"] = {"bps": tax["bps"], "approved": tax["approved"], "source_ref": _text(tax["source_ref"], 300, optional=True)}
    work = value["work"]
    _fields(work, {"completed", "approved", "source_ref"}, {"completed", "approved", "source_ref"})
    if type(work["completed"]) is not bool or type(work["approved"]) is not bool:
        raise ValueError("Completion evidence requires explicit booleans.")
    result["work"] = {"completed": work["completed"], "approved": work["approved"], "source_ref": _text(work["source_ref"], 300, optional=True)}
    return result


def assess_record(value, *, revision=1, review=None):
    """Calculate only supplied values; missing or unapproved evidence holds work."""
    record = validate_record(value)
    exceptions = []
    def issue(field, code, message): exceptions.append({"field": field, "code": code, "message": message})
    for key in ("name", "address"):
        if record["customer"][key] is None:
            issue("customer." + key, "missing_customer", "Supply the fictional customer's " + key + ".")
    for key in ("service_date", "document_date", "due_date"):
        if record[key] is None:
            issue(key, "missing_date", "Supply an approved " + key.replace("_", " ") + ".")
    if record["document_date"] and record["due_date"] and record["due_date"] < record["document_date"]:
        issue("due_date", "date_order", "The due/valid-through date cannot precede the document date.")
    calculated, known, subtotal = [], True, 0
    for index, line in enumerate(record["lines"]):
        amount = None if line["unit_price"] is None else _cents(line["unit_price"]) * line["quantity"]
        if amount is None:
            known = False
            issue(f"lines.{index}.unit_price", "missing_rate", "Supply the client's approved rate; no market price is inferred.")
        else:
            subtotal += amount
        if not line["price_approved"] or not line["source_ref"]:
            issue(f"lines.{index}.source_ref", "unapproved_rate", "Record the approved rate source before handoff.")
        calculated.append({**line, "line_total_cents": None if amount is None else str(amount)})
    if subtotal > MAX_CENTS:
        raise ValueError("Handoff subtotal exceeds the supported bound.")
    tax = record["tax"]
    tax_known = tax["bps"] is not None and tax["approved"] and bool(tax["source_ref"])
    if not tax_known:
        issue("tax", "unknown_tax", "Tax treatment is unconfirmed. Final total stays unavailable until the owner supplies the approved basis.")
    tax_amount = _round_ratio(subtotal * tax["bps"]) if known and tax_known else None
    if tax_amount is not None and subtotal + tax_amount > MAX_CENTS:
        raise ValueError("Handoff final total exceeds the supported bound.")
    work = record["work"]
    if record["kind"] == "invoice":
        if not work["completed"] or not work["approved"] or not work["source_ref"]:
            issue("work", "completion_required", "An invoice draft needs explicit approved completed-work evidence; a quote does not establish completion.")
        if record["service_date"] and record["document_date"] and record["service_date"] > record["document_date"]:
            issue("service_date", "future_service", "Completed-work invoices cannot precede their service date.")
    content_sha = _digest(record)
    accepted_review = review if review and review.get("content_sha256") == content_sha else None
    status = "held" if exceptions else "approved" if accepted_review and accepted_review["decision"] == "approve" else "rejected" if accepted_review and accepted_review["decision"] == "reject" else "ready_for_review"
    return {**record, "lines": calculated, "revision": revision, "content_sha256": content_sha,
            "currency": "USD", "subtotal_cents": str(subtotal) if known else None,
            "tax_cents": None if tax_amount is None else str(tax_amount),
            "total_cents": str(subtotal + tax_amount) if known and tax_amount is not None and not exceptions else None,
            "exceptions": exceptions, "status": status, "review": accepted_review,
            "demo_only": True, "sent": False, "posted": False, "payment_received": False}


def _default():
    return {"schema": 1, "revision": 0, "initialized": False, "records": [], "record_revisions": {}, "reviews": {}}


class HandoffDesk:
    def __init__(self, registry, store):
        self.registry, self.store = registry, store
        self.lock = threading.RLock()

    @contextmanager
    def database(self):
        with self.lock, self.registry._directory(self.store.path.parent):
            for suffix in ("", "-wal", "-shm", "-journal"):
                path = _allowed_path(str(self.store.path) + suffix)
                try: info = path.lstat()
                except FileNotFoundError:
                    if suffix: continue
                    raise ValueError("The handoff owner database is missing.") from None
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Handoff database files must be regular, single-link and non-reparse.")
            with self.store.connect(require_existing=True) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                deadline = time.monotonic() + 5
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                yield db

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='handoff_demo_state'").fetchone() is not None

    @staticmethod
    def _initialize(db):
        db.execute("CREATE TABLE IF NOT EXISTS handoff_demo_state(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS handoff_demo_requests(request_id TEXT PRIMARY KEY,action TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,revision INTEGER NOT NULL,receipt TEXT NOT NULL,reserved_bytes INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT OR IGNORE INTO handoff_demo_state VALUES(1,0,?)", (_json(_default()),))

    @staticmethod
    def _state(db):
        row = db.execute("SELECT revision,length(value) bytes,substr(value,1,?) value FROM handoff_demo_state WHERE id=1", (MAX_SOURCE + 1,)).fetchone()
        if not row or row["bytes"] > MAX_SOURCE:
            raise ValueError("The handoff demonstration state is missing or oversized.")
        state = _load(row["value"])
        if type(state) is not dict or set(state) != set(_default()) or type(state.get("schema")) is not int or state["schema"] != 1 or type(state.get("revision")) is not int or state["revision"] != row["revision"] or type(state.get("initialized")) is not bool or type(state.get("records")) is not list or len(state["records"]) > MAX_RECORDS:
            raise ValueError("The handoff demonstration state is inconsistent.")
        if type(state["record_revisions"]) is not dict or type(state["reviews"]) is not dict:
            raise ValueError("The handoff record revisions or reviews are inconsistent.")
        identifiers = set()
        for record in state["records"]:
            checked = validate_record(record)
            if checked != record or record["id"] in identifiers:
                raise ValueError("The stored handoff records are not canonical and unique.")
            identifiers.add(record["id"])
        if set(state["record_revisions"]) != identifiers or set(state["reviews"]) - identifiers or any(type(value) is not int or not 1 <= value <= MAX_REQUESTS for value in state["record_revisions"].values()):
            raise ValueError("The stored handoff revisions or review identities are inconsistent.")
        for review in state["reviews"].values():
            _fields(review, {"decision", "content_sha256", "note", "reviewed_at", "demo_only", "scope"}, {"decision", "content_sha256", "note", "reviewed_at", "demo_only", "scope"})
            if review["decision"] not in ("approve", "reject") or type(review["content_sha256"]) is not str or not _SHA.fullmatch(review["content_sha256"]) or review["demo_only"] is not True:
                raise ValueError("The stored demo review is inconsistent.")
            _text(review["reviewed_at"], 100)
            _text(review["scope"], 300)
            _text(review["note"], 1000, optional=True)
        if state["initialized"] != bool(state["records"]):
            raise ValueError("The handoff initialization marker is inconsistent.")
        return state

    @staticmethod
    def _unpaused(db):
        row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        if row and _load(row[0]):
            raise HandoffConflict("Autonomy is paused; handoff demonstration changes are held.")

    @staticmethod
    def _revision(state, expected):
        if type(expected) is not int or expected < 0 or state["revision"] != expected:
            raise HandoffConflict("Handoff data changed; refresh before saving.", revision=state["revision"])

    def _snapshot(self, state, db):
        records = [assess_record(record, revision=state["record_revisions"][record["id"]], review=state["reviews"].get(record["id"])) for record in state["records"]]
        audit = [dict(row) for row in db.execute("SELECT request_id,action,status,created_at,revision FROM handoff_demo_requests ORDER BY rowid DESC LIMIT 50")] if self._exists(db) else []
        latest = db.execute("SELECT status,receipt FROM handoff_demo_requests WHERE action='export' ORDER BY rowid DESC LIMIT 1").fetchone() if self._exists(db) else None
        latest_export = _load(latest["receipt"]) if latest else None
        if latest_export and latest["status"] == "completed":
            try:
                self._verify_export(latest_export)
            except (ValueError, OSError):
                latest_export = {"status": "integrity_error", "request_id": latest_export.get("request_id"), "demo_only": True,
                                 "error": "The saved export is missing or changed; do not adopt or regenerate it."}
        held = db.execute("SELECT request_id,status FROM handoff_demo_requests WHERE action='export' AND status IN ('running','failed') ORDER BY rowid LIMIT 1").fetchone() if self._exists(db) else None
        export_hold = {"request_id": held["request_id"], "status": held["status"],
                       "reason": "Review and explicitly acknowledge the incomplete export before making a new packet. Its original output will never be adopted or replayed."} if held else None
        return {"schema": 1, "revision": state["revision"], "initialized": state["initialized"], "demo_only": True,
                "provider": dict(DEMO_PROVIDER),
                "records": records, "summary": {"records": len(records), **{key: sum(record["status"] == key for record in records) for key in ("ready_for_review", "held", "approved", "rejected")}},
                "audit": audit, "latest_export": latest_export, "export_hold": export_hold, "model_calls": 0, "network_calls": 0,
                "scope": "Fictional local demonstration. Approval concerns this exact demo draft, never customer consent, sending, ledger posting or payment."}

    def status(self):
        with self.database() as db:
            state = self._state(db) if self._exists(db) else _default()
            return self._snapshot(state, db)

    @staticmethod
    def _arguments(body, allowed, required):
        _fields(body, allowed | {"request_id", "expected_revision"}, required | {"request_id", "expected_revision"})
        _identifier(body["request_id"])
        if type(body["expected_revision"]) is not int or body["expected_revision"] < 0:
            raise ValueError("expected_revision must be a nonnegative integer.")

    @staticmethod
    def _old(db, body, action):
        row = db.execute("SELECT * FROM handoff_demo_requests WHERE request_id=?", (body["request_id"],)).fetchone()
        if row:
            if row["action"] != action or row["fingerprint"] != _digest(body):
                raise HandoffConflict("This handoff request ID already belongs to different arguments.")
            if row["status"] != "completed":
                raise HandoffConflict("Earlier handoff work is incomplete or failed; it will not be replayed or adopted.")
            return {**_load(row["receipt"]), "replayed": True}
        if db.execute("SELECT count(*) FROM handoff_demo_requests").fetchone()[0] >= MAX_REQUESTS:
            raise ValueError("The handoff demonstration history is full; review retained receipts.")
        return None

    def _mutate(self, action, body, change):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._unpaused(db)
            self._initialize(db)
            previous = self._old(db, body, action)
            if previous: return previous
            state = self._state(db)
            self._revision(state, body["expected_revision"])
            change(state)
            state["revision"] += 1
            raw = _json(state)
            if len(raw.encode()) > MAX_SOURCE:
                raise ValueError("The handoff demonstration state quota is full.")
            db.execute("UPDATE handoff_demo_state SET revision=?,value=? WHERE id=1", (state["revision"], raw))
            stamp = datetime.now(timezone.utc).isoformat()
            receipt = {**self._snapshot(state, db), "request_id": body["request_id"], "replayed": False}
            db.execute("INSERT INTO handoff_demo_requests VALUES(?,?,?,?,?,?,?,0)", (body["request_id"], action, _digest(body), "completed", stamp, state["revision"], _json(receipt)))
            # Include this request in the live return without mutating the durable
            # response on subsequent read. A replay returns the original snapshot.
            receipt["audit"] = [{"request_id": body["request_id"], "action": action, "status": "completed", "created_at": stamp, "revision": state["revision"]}, *receipt["audit"]][:50]
            db.execute("UPDATE handoff_demo_requests SET receipt=? WHERE request_id=?", (_json(receipt), body["request_id"]))
            return receipt

    def seed_demo(self, body):
        self._arguments(body, set(), set())
        fixture_path = _allowed_path(Path(__file__).resolve().parent / "resources/handoff/demo.json")
        with self.registry._directory(fixture_path.parent) as directory:
            with os.fdopen(self.registry._open_read(fixture_path, directory), "rb") as source:
                before = os.fstat(source.fileno())
                raw = source.read(MAX_SOURCE + 1)
                after = os.fstat(source.fileno())
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if len(raw) > MAX_SOURCE or identity(before) != identity(after) or len(raw) != before.st_size:
            raise ValueError("The handoff demonstration fixture is oversized.")
        fixture = _load(raw.decode("utf-8-sig"))
        _fields(fixture, {"schema", "demo_only", "records"}, {"schema", "demo_only", "records"})
        if type(fixture["schema"]) is not int or fixture["schema"] != 1 or fixture["demo_only"] is not True or type(fixture["records"]) is not list or not 1 <= len(fixture["records"]) <= MAX_RECORDS:
            raise ValueError("Use the fictional handoff schema 1 fixture.")
        records = [validate_record(record) for record in fixture["records"]]
        if len({record["id"] for record in records}) != len(records):
            raise ValueError("Handoff demonstration IDs must be unique.")
        def change(state):
            if state["initialized"]:
                raise HandoffConflict("This demonstration is already initialized; existing work will not be reset.", revision=state["revision"])
            state.update(initialized=True, records=records, record_revisions={record["id"]: 1 for record in records}, reviews={})
        return self._mutate("seed_demo", body, change)

    @staticmethod
    def _record(state, body):
        identifier = _identifier(body["record_id"])
        digest = body["content_sha256"]
        if type(digest) is not str or not _SHA.fullmatch(digest):
            raise ValueError("Record review requires the exact lowercase content SHA-256.")
        record = next((row for row in state["records"] if row["id"] == identifier), None)
        if record is None:
            raise ValueError("Choose an existing handoff demonstration record.")
        if _digest(record) != digest:
            raise HandoffConflict("The selected draft changed; refresh before reviewing.", revision=state["revision"])
        return record

    def revise(self, body):
        self._arguments(body, {"record_id", "content_sha256", "changes"}, {"record_id", "content_sha256", "changes"})
        _fields(body["changes"], _EDITABLE)
        if not body["changes"]:
            raise ValueError("Supply at least one explicit handoff correction.")
        def change(state):
            record = self._record(state, body)
            updated = validate_record({**record, **body["changes"]})
            if updated == record:
                raise ValueError("This correction does not change the draft.")
            record.clear(); record.update(updated)
            state["record_revisions"][record["id"]] += 1
            state["reviews"].pop(record["id"], None)
        return self._mutate("revise", body, change)

    def review(self, body):
        self._arguments(body, {"record_id", "content_sha256", "decision", "note"}, {"record_id", "content_sha256", "decision"})
        if body["decision"] not in ("approve", "reject"):
            raise ValueError("Choose approve or reject for this demonstration draft.")
        note = _text(body["note"], 1000) if "note" in body else None
        def change(state):
            record = self._record(state, body)
            if body["decision"] == "approve" and assess_record(record)["exceptions"]:
                raise HandoffConflict("Resolve all source, tax and work-completion exceptions before approval.", revision=state["revision"])
            state["reviews"][record["id"]] = {"decision": body["decision"], "content_sha256": body["content_sha256"],
                "note": note, "reviewed_at": datetime.now(timezone.utc).isoformat(), "demo_only": True,
                "scope": "Owner review of fictional local draft only; not customer approval or external action."}
        return self._mutate("review", body, change)

    def _verify_export(self, receipt):
        expected = {"handoff-demo/" + receipt["request_id"] + "/packet.json", "handoff-demo/" + receipt["request_id"] + "/packet.html"}
        if type(receipt.get("artifacts")) is not list or len(receipt["artifacts"]) != 2 or {row.get("path") for row in receipt["artifacts"]} != expected:
            raise ValueError("The complete handoff export artifact contract is missing.")
        for item in receipt["artifacts"]:
            path = self.registry._path(item["path"])
            with self.registry._directory(path.parent) as directory:
                with os.fdopen(self.registry._open_read(path, directory), "rb") as source:
                    raw = source.read(MAX_OUTPUT + 1)
            if len(raw) > MAX_OUTPUT or len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("A saved handoff export changed; it will not be adopted or regenerated.")

    def acknowledge_export(self, body):
        """Acknowledge an uncertain receipt, without adopting or replaying output."""
        self._arguments(body, set(), set())
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._unpaused(db)
            if not self._exists(db):
                raise HandoffConflict("There is no initialized handoff export to acknowledge.")
            state = self._state(db)
            row = db.execute("SELECT action,status,receipt FROM handoff_demo_requests WHERE request_id=?", (body["request_id"],)).fetchone()
            if not row or row["action"] != "export":
                raise ValueError("Choose an existing export reservation to acknowledge.")
            receipt = _load(row["receipt"])
            if row["status"] == "acknowledged":
                if receipt.get("acknowledgement_fingerprint") != _digest(body):
                    raise HandoffConflict("This export was acknowledged with different arguments; refresh.", revision=state["revision"])
                return {**self._snapshot(state, db), "request_id": body["request_id"], "replayed": True}
            self._revision(state, body["expected_revision"])
            if row["status"] not in ("running", "failed"):
                raise HandoffConflict("Only an incomplete or failed export can be acknowledged.", revision=state["revision"])
            receipt.update(status="acknowledged", acknowledged_at=datetime.now(timezone.utc).isoformat(),
                           acknowledgement_fingerprint=_digest(body),
                           acknowledgement_scope="Owner acknowledged the failed/incomplete demo export. Partial files remain untrusted; nothing was adopted, sent, posted or replayed.")
            state["revision"] += 1
            db.execute("UPDATE handoff_demo_state SET revision=?,value=? WHERE id=1", (state["revision"], _json(state)))
            db.execute("UPDATE handoff_demo_requests SET status='acknowledged',revision=?,receipt=? WHERE request_id=?", (state["revision"], _json(receipt), body["request_id"]))
            return {**self._snapshot(state, db), "request_id": body["request_id"], "replayed": False}

    def export(self, body):
        self._arguments(body, set(), set())
        identifier = body["request_id"]
        reserved = False
        try:
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                self._unpaused(db)
                self._initialize(db)
                old = self._old(db, body, "export")
                if old:
                    self._verify_export(old)
                    return old
                state = self._state(db)
                self._revision(state, body["expected_revision"])
                hold = db.execute("SELECT request_id FROM handoff_demo_requests WHERE action='export' AND status IN ('running','failed') ORDER BY rowid LIMIT 1").fetchone()
                if hold:
                    raise HandoffConflict("An earlier export is incomplete or failed; explicitly acknowledge its receipt before creating a new packet.", revision=state["revision"])
                snapshot = self._snapshot(state, db)
                selected = [record for record in snapshot["records"] if record["status"] == "approved"]
                if not selected:
                    raise HandoffConflict("Approve at least one complete demonstration draft before exporting.", revision=state["revision"])
                packet = {"schema": 1, "demo_only": True, "provider": dict(DEMO_PROVIDER), "title": "Fictional quote/invoice handoff packet",
                    "revision": state["revision"], "request_id": identifier, "records": selected,
                    "excluded_record_ids": [record["id"] for record in snapshot["records"] if record["status"] != "approved"],
                    "scope": "Draft demonstration only. No customer consent, external sending, ledger posting or payment was established.",
                    "model_calls": 0, "network_calls": 0}
                payloads = {"packet.json": _bytes(packet), "packet.html": render_packet(packet).encode("utf-8")}
                if any(len(raw) > MAX_OUTPUT for raw in payloads.values()):
                    raise ValueError("Handoff export exceeds its per-file quota.")
                size = sum(map(len, payloads.values()))
                if db.execute("SELECT coalesce(sum(reserved_bytes),0) FROM handoff_demo_requests").fetchone()[0] + size > MAX_EXPORT_BYTES:
                    raise ValueError("The handoff demonstration export quota is full.")
                receipt = {"status": "running", "request_id": identifier, "revision": state["revision"], "demo_only": True,
                    "record_ids": [record["id"] for record in selected], "excluded_record_ids": packet["excluded_record_ids"],
                    "artifacts": [], "replayed": False, "model_calls": 0, "network_calls": 0, "sent": False, "posted": False}
                db.execute("INSERT INTO handoff_demo_requests VALUES(?,?,?,?,?,?,?,?)", (identifier, "export", _digest(body), "running", datetime.now(timezone.utc).isoformat(), state["revision"], _json(receipt), size))
            reserved = True
            def checkpoint():
                with self.database() as db:
                    self._unpaused(db)
                    current = self._state(db)
                    self._revision(current, state["revision"])
                    if _digest(current) != _digest(state):
                        raise HandoffConflict("The handoff state changed during export.", revision=current["revision"])
            folder = self.registry._path("handoff-demo/" + identifier)
            with self.registry._directory(folder.parent, create=True):
                folder.mkdir()
                with self.registry._directory(folder):
                    for name, raw in payloads.items():
                        checkpoint()
                        publish_bytes(folder/name, raw, checkpoint=checkpoint)
                        receipt["artifacts"].append({"path": (folder/name).relative_to(self.registry.root).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            self._verify_export(receipt)
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                self._unpaused(db)
                current = self._state(db)
                self._revision(current, state["revision"])
                if _digest(current) != _digest(state):
                    raise HandoffConflict("The handoff state changed before export completion.", revision=current["revision"])
                receipt["status"] = "completed"
                changed = db.execute("UPDATE handoff_demo_requests SET status='completed',receipt=? WHERE request_id=? AND status='running'", (_json(receipt), identifier)).rowcount
                if changed != 1:
                    raise HandoffConflict("The handoff export reservation changed.")
            return receipt
        except Exception as error:
            if reserved:
                with self.database() as db:
                    failed = {"status": "failed", "request_id": identifier, "error": str(error), "demo_only": True,
                              "scope": "Partial output is retained for review; no replay or adoption is permitted."}
                    db.execute("UPDATE handoff_demo_requests SET status='failed',receipt=? WHERE request_id=? AND status='running'", (_json(failed), identifier))
            raise


def render_packet(packet):
    """Escaped, script-free, printable demonstration documents."""
    escape = lambda value: html.escape(str(value), quote=True)
    money = lambda value: "Unconfirmed" if value is None else "$" + _money(int(value))
    provider = packet["provider"]
    pages = []
    for record in packet["records"]:
        customer = record["customer"]
        lines = "".join("<tr><td>" + escape(line["description"]) + "</td><td>" + str(line["quantity"]) + "</td><td>" + escape("$" + line["unit_price"]) + "</td><td>" + money(line["line_total_cents"]) + "</td></tr>" for line in record["lines"])
        sources = "".join("<li>" + escape(line["source_ref"]) + "</li>" for line in record["lines"])
        completion = "<li>Completed-work source: " + escape(record["work"]["source_ref"]) + "</li>" if record["kind"] == "invoice" else "<li>Quote only. No completed work or customer acceptance is established.</li>"
        pages.append(f'''<article>
<div class="flag">FICTIONAL DEMONSTRATION · DRAFT ONLY</div>
<h1>{escape(record["kind"].title())} handoff</h1><h2>{escape(record["title"])}</h2>
<p><b>Prepared for the service provider:</b> {escape(provider["name"])}<br>{escape(provider["address"])}<br>
<b>Reference:</b> DEMO-{escape(record["id"])}<br>
<b>Provider's customer:</b> {escape(customer["name"])}<br>{escape(customer["address"])}<br>
<b>Document date:</b> {escape(record["document_date"])} · <b>Service date:</b> {escape(record["service_date"])}<br>
<b>{"Due date" if record["kind"] == "invoice" else "Valid through"}:</b> {escape(record["due_date"])}</p>
<table><thead><tr><th>Approved scope</th><th>Qty</th><th>Rate</th><th>Amount</th></tr></thead><tbody>{lines}</tbody></table>
<dl><dt>Subtotal</dt><dd>{money(record["subtotal_cents"])}</dd>
<dt>Supplied demonstration tax ({record["tax"]["bps"] / 100:g}%)</dt><dd>{money(record["tax_cents"])}</dd>
<dt>Draft total</dt><dd>{money(record["total_cents"])}</dd></dl>
<h3>Source and approval handoff</h3><p>{escape(record["source_notes"])}</p>
<ul>{sources}<li>{escape(record["tax"]["source_ref"])}</li>{completion}</ul>
<p class="small">Demo review: {escape(record["review"]["reviewed_at"])} · Content SHA-256: {escape(record["content_sha256"])}</p>
<p class="notice">Every party, rate, job and approval in this packet is fictional. The face value belongs to the fictional service provider's draft; it is not Ariadne revenue or the handoff service fee. This draft was not sent to a customer, posted to a ledger, or paid. Demo review is not customer consent or tax verification.</p>
</article>''')
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>Ariadne · Fictional handoff packet</title><style>*{box-sizing:border-box}body{margin:0;background:#e9ede9;color:#18241d;font:15px/1.55 system-ui,sans-serif}main{max-width:900px;margin:auto;padding:30px}article{background:white;padding:42px;margin:0 0 30px;border:1px solid #ccd4cc}.flag{color:#386341;font-size:12px;letter-spacing:.12em;font-weight:700}h1{font-size:32px;margin:22px 0 4px}h2{font-size:19px;font-weight:500}table{width:100%;border-collapse:collapse;margin-top:30px}th,td{padding:12px 8px;text-align:left;border-bottom:1px solid #d7ddd7}dl{display:grid;grid-template-columns:1fr auto;max-width:360px;margin:24px 0 24px auto}dd{text-align:right}dt:last-of-type,dd:last-of-type{font-weight:700}.small{font-size:11px;overflow-wrap:anywhere}.notice{border-left:3px solid #52795a;padding:12px;background:#f0f5f0;font-size:12px}@media(max-width:600px){main{padding:10px}article{padding:20px}table{font-size:12px}th,td{padding:8px 4px}}@media print{body{background:white}main{padding:0;max-width:none}article{border:0;margin:0;break-after:page;padding:12mm}article:last-child{break-after:auto}}</style><main>''' + "".join(pages) + "</main></html>"
