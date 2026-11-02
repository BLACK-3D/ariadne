"""Private, offline client intake and versioned quote/invoice draft workflow.

Owner-recorded approval and customer acceptance are separate observations. This
module never sends documents, posts a ledger, collects payment or calls a model.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import copy
import hashlib
import html
import os
import re

from .atomic import publish_bytes
from .client_inbox import ClientInboxMixin, initialize as initialize_inbox, TABLES as INBOX_TABLES
from .business_ops import _cents, _money, _round_ratio
from .handoff import HandoffConflict, HandoffDesk, _bytes, _digest, _fields, _identifier, _json, _load, _text

ClientHandoffConflict = HandoffConflict
UTC = timezone.utc
WORKFLOW_TIMEZONE = "America/Chicago"
MAX_CLIENTS = 200
MAX_INTAKES = 10000
MAX_DOCUMENTS = 20000
MAX_REQUESTS = 100000
MAX_ENTITY = 64 * 1024
MAX_ENTITY_BYTES = 128 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024 * 1024
MAX_EXPORT_BYTES = 128 * 1024 * 1024
MAX_OUTPUT = 512 * 1024
MAX_CENTS = 10**12
MAX_LINES = 50
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_QTY = re.compile(r"(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,3})?\Z")
_CLIENT_FIELDS = {"id", "name", "address", "email", "contact", "notes", "archived"}
_INTAKE_FIELDS = {"id", "client_id", "title", "customer", "source_notes", "service_date", "document_date", "due_date", "lines", "tax", "permission"}
_QUOTE_EDITS = _INTAKE_FIELDS - {"id", "client_id"}
_INVOICE_EDITS = {"title", "source_notes", "service_date", "document_date", "due_date", "work"}


def _now():
    return datetime.now(UTC)


def _local_day(value=None):
    from .briefing import _tz
    return (value or _now()).astimezone(_tz(WORKFLOW_TIMEZONE)).date()


def _notes(value, maximum=12000, *, optional=False):
    if value is None and optional:
        return None
    if type(value) is not str or not value.strip() or len(value) > maximum or any((ord(c) < 32 and c not in "\r\n\t") or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("Use bounded text; notes may contain ordinary line breaks and tabs.")
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _day(value):
    if value is None:
        return None
    if type(value) is not str or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Use a YYYY-MM-DD date or null.")
    return value


def _quantity(value):
    if type(value) is not str or not _QTY.fullmatch(value):
        raise ValueError("Quantities require positive decimal text with at most three decimal places.")
    if not 0 < _quantity_milliunits(value) <= 10000000:
        raise ValueError("Quantities must be greater than zero and at most 10000.")
    return value.rstrip("0").rstrip(".") if "." in value else value


def _quantity_milliunits(value):
    whole,_,fraction=value.partition(".")
    return int(whole)*1000+int(fraction.ljust(3,"0") or "0")


def _permission(value):
    _fields(value, {"confirmed", "source_ref"}, {"confirmed", "source_ref"})
    if type(value["confirmed"]) is not bool:
        raise ValueError("Record an explicit permission confirmation boolean.")
    return {"confirmed": value["confirmed"], "source_ref": _text(value["source_ref"], 1000, optional=True)}


def _core(value):
    """Validate supplied document facts without inventing missing values."""
    result = {"title": _text(value["title"], 300), "source_notes": _notes(value["source_notes"]),
              **{key: _day(value[key]) for key in ("service_date", "document_date", "due_date")}}
    _fields(value["customer"], {"name", "email", "address"}, {"name", "email", "address"})
    result["customer"] = {key: _text(value["customer"][key], 500, optional=True) for key in ("name", "email", "address")}
    lines = value["lines"]
    if type(lines) is not list or not 1 <= len(lines) <= MAX_LINES:
        raise ValueError("Supply one to fifty structured service lines.")
    result["lines"] = []
    for row in lines:
        _fields(row, {"description", "quantity", "unit", "unit_price", "price_approved", "source_ref"},
                {"description", "quantity", "unit_price", "price_approved", "source_ref"})
        if type(row["price_approved"]) is not bool:
            raise ValueError("Price approval must be an explicit boolean.")
        price = row["unit_price"]
        if price is not None:
            if type(price) is not str or _cents(price) > MAX_CENTS:
                raise ValueError("Unit prices require bounded exact decimal text or null.")
            price = _money(_cents(price))
        result["lines"].append({"description": _text(row["description"], 500), "quantity": _quantity(row["quantity"]),
            "unit": _text(row.get("unit", "unit"), 40), "unit_price": price,
            "price_approved": row["price_approved"], "source_ref": _text(row["source_ref"], 1000, optional=True)})
    tax = value["tax"]
    _fields(tax, {"bps", "approved", "source_ref"}, {"bps", "approved", "source_ref"})
    if tax["bps"] is not None and (type(tax["bps"]) is not int or not 0 <= tax["bps"] <= 10000) or type(tax["approved"]) is not bool:
        raise ValueError("Tax requires integer basis points from zero to 10000 or null, and a boolean approval.")
    result["tax"] = {"bps": tax["bps"], "approved": tax["approved"], "source_ref": _text(tax["source_ref"], 1000, optional=True)}
    result["permission"] = _permission(value["permission"])
    return result


def _work(value):
    _fields(value, {"completed", "approved", "source_ref"}, {"completed", "approved", "source_ref"})
    if type(value["completed"]) is not bool or type(value["approved"]) is not bool:
        raise ValueError("Completed work and its review require explicit booleans.")
    return {"completed": value["completed"], "approved": value["approved"], "source_ref": _text(value["source_ref"], 1000, optional=True)}


def _client(value, *, revision=1):
    _fields(value, _CLIENT_FIELDS, {"id", "name"})
    if type(value.get("archived", False)) is not bool:
        raise ValueError("Client archive status must be a boolean.")
    return {"id": _identifier(value["id"]), "name": _text(value["name"], 300),
            **{key: _text(value.get(key), 500, optional=True) for key in ("address", "email", "contact")},
            "notes": _notes(value.get("notes"), 2000, optional=True), "archived": value.get("archived", False), "revision": revision}


def _assess(document, review=None, acceptance=None, *, invoice_id=None):
    """Pure fixed arithmetic and source gates for a saved document snapshot."""
    result = copy.deepcopy(document)
    facts = _core(document)
    if facts != {key: document[key] for key in facts}:
        raise ValueError("Stored document facts are not canonical.")
    issues = []
    def issue(field, code, message):
        issues.append({"field": field, "code": code, "message": message})
    for key in ("name", "address"):
        if not document["provider"].get(key): issue("provider."+key, "missing_provider", "Supply the service business's "+key+".")
        if not facts["customer"][key]: issue("customer."+key, "missing_customer", "Supply the customer's "+key+".")
    for key in ("document_date", "service_date", "due_date"):
        if not facts[key]: issue(key, "missing_date", "Supply the "+key.replace("_", " ")+".")
    today = _local_day().isoformat()
    if facts["document_date"] and facts["document_date"] > today:
        issue("document_date", "future_document", "A document dated after today's workflow date must remain a draft.")
    if facts["document_date"] and facts["due_date"] and facts["due_date"] < facts["document_date"]:
        issue("due_date", "date_order", "The due or valid-through date cannot precede the document date.")
    if not facts["permission"]["confirmed"] or not facts["permission"]["source_ref"]:
        issue("permission", "permission_required", "Record the client's permission and scope reference before approving or exporting.")
    known, subtotal, calculated = True, 0, []
    for index, line in enumerate(facts["lines"]):
        amount = None
        if line["unit_price"] is None:
            known = False
            issue(f"lines.{index}.unit_price", "missing_rate", "Supply the client's approved rate.")
        else:
            scaled = _quantity_milliunits(line["quantity"])
            amount = _round_ratio(_cents(line["unit_price"])*scaled, 1000)
            subtotal += amount
            if amount > MAX_CENTS or subtotal > MAX_CENTS:
                raise ValueError("Line or subtotal exceeds the supported bound.")
        if not line["price_approved"] or not line["source_ref"]:
            issue(f"lines.{index}.source_ref", "rate_approval_required", "Record the approved price source.")
        calculated.append({**line, "line_total_cents": None if amount is None else str(amount)})
    tax = facts["tax"]
    tax_known = tax["bps"] is not None and tax["approved"] and bool(tax["source_ref"])
    if not tax_known:
        issue("tax", "tax_required", "Tax treatment is unknown until an approved instruction is supplied.")
    tax_cents = _round_ratio(subtotal*tax["bps"]) if known and tax_known else None
    if tax_cents is not None and subtotal+tax_cents > MAX_CENTS:
        raise ValueError("Document total exceeds the supported bound.")
    if document["kind"] == "invoice":
        work = _work(document["work"])
        if not work["completed"] or not work["approved"] or not work["source_ref"]:
            issue("work", "completion_required", "Supply reviewed evidence that this work was completed.")
        if facts["service_date"] and (facts["service_date"] > today or facts["document_date"] and facts["service_date"] > facts["document_date"]):
            issue("service_date", "future_completion", "Completed work cannot occur after today or the invoice date.")
        parent=document["parent"]
        accepted_day=_local_day(datetime.fromisoformat(parent["acceptance"]["observed_at"])).isoformat()
        if facts["document_date"] and (facts["document_date"]<parent["document_date"] or facts["document_date"]<accepted_day):
            issue("document_date","invoice_before_acceptance","An invoice cannot predate its source quote or the recorded customer's acceptance day.")
    content_sha = _digest(document)
    active_review = review if review and review.get("content_sha256") == content_sha else None
    active_acceptance = acceptance if acceptance and acceptance.get("content_sha256") == content_sha else None
    voided = document.get("voided") is not None
    status = "voided" if voided else "held" if issues else "approved" if active_review and active_review["decision"] == "approve" else "rejected" if active_review and active_review["decision"] == "reject" else "ready_for_review"
    result.update(content_sha256=content_sha, lines=calculated, currency="USD", exceptions=issues, status=status,
        subtotal_cents=str(subtotal) if known else None, tax_cents=None if tax_cents is None else str(tax_cents),
        total_cents=str(subtotal+tax_cents) if known and tax_cents is not None and not issues else None,
        review=active_review, acceptance=active_acceptance, invoice_id=invoice_id, demo_only=False,
        sent=False, posted=False, payment_received=False,
        can_edit=not voided and not invoice_id, can_accept=document["kind"]=="quote" and status=="approved" and not invoice_id,
        can_invoice=document["kind"]=="quote" and status=="approved" and bool(active_acceptance and active_acceptance["accepted"]) and not invoice_id,
        can_void=not voided and not invoice_id)
    return result


class ClientHandoffDesk(ClientInboxMixin, HandoffDesk):
    """Owner-only shared workspace. Clients are records, not authenticated tenants."""
    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='client_handoff_meta'").fetchone() is not None

    @staticmethod
    def _initialize(db):
        for statement in (
            "CREATE TABLE IF NOT EXISTS client_handoff_meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,next_reference INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS client_handoff_clients(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,value TEXT NOT NULL,bytes INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS client_handoff_intakes(id TEXT PRIMARY KEY,client_id TEXT NOT NULL,created_at TEXT NOT NULL,sha256 TEXT NOT NULL,value TEXT NOT NULL,bytes INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS client_handoff_documents(id TEXT PRIMARY KEY,kind TEXT NOT NULL,client_id TEXT NOT NULL,intake_id TEXT NOT NULL,parent_quote_id TEXT,revision INTEGER NOT NULL,sha256 TEXT NOT NULL,voided INTEGER NOT NULL,created_at TEXT NOT NULL,review TEXT,acceptance TEXT)",
            "CREATE UNIQUE INDEX IF NOT EXISTS client_handoff_active_invoice ON client_handoff_documents(parent_quote_id) WHERE kind='invoice' AND voided=0",
            "CREATE UNIQUE INDEX IF NOT EXISTS client_handoff_active_quote ON client_handoff_documents(intake_id) WHERE kind='quote' AND voided=0",
            "CREATE INDEX IF NOT EXISTS client_handoff_docs_client ON client_handoff_documents(client_id,created_at)",
            "CREATE INDEX IF NOT EXISTS client_handoff_intakes_client ON client_handoff_intakes(client_id,created_at)",
            "CREATE TABLE IF NOT EXISTS client_handoff_versions(document_id TEXT NOT NULL,revision INTEGER NOT NULL,sha256 TEXT NOT NULL,value TEXT NOT NULL,bytes INTEGER NOT NULL,PRIMARY KEY(document_id,revision))",
            "CREATE TABLE IF NOT EXISTS client_handoff_requests(request_id TEXT PRIMARY KEY,action TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,revision INTEGER NOT NULL,receipt TEXT NOT NULL,receipt_bytes INTEGER NOT NULL,reserved_bytes INTEGER NOT NULL DEFAULT 0,entity_id TEXT)",
            "CREATE INDEX IF NOT EXISTS client_handoff_request_entity ON client_handoff_requests(entity_id,revision)",
        ): db.execute(statement)
        db.execute("INSERT OR IGNORE INTO client_handoff_meta VALUES(1,0,1)")
        initialize_inbox(db)

    @staticmethod
    def _meta(db):
        row = db.execute("SELECT revision,next_reference FROM client_handoff_meta WHERE id=1").fetchone()
        if not row or type(row["revision"]) is not int or row["revision"] < 0 or type(row["next_reference"]) is not int or row["next_reference"] < 1:
            raise ValueError("The client workflow metadata is inconsistent.")
        return dict(row)

    @staticmethod
    def _unpaused(db):
        row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        if row and _load(row[0]): raise HandoffConflict("Autonomy is paused; client-workflow changes are held.")

    @staticmethod
    def _value(row, *, sha=False):
        if row is None: raise ValueError("The requested client-workflow record does not exist.")
        raw = row["value"]
        if type(raw) is not str or len(raw.encode()) > MAX_ENTITY or row["bytes"] != len(raw.encode()):
            raise ValueError("A stored client-workflow record is oversized or inconsistent.")
        value = _load(raw)
        if type(value) is not dict or _json(value) != raw or sha and _digest(value) != row["sha256"]:
            raise ValueError("Stored client-workflow content does not match its identity.")
        return value

    def _client(self, db, identifier):
        row = db.execute("SELECT id,revision,substr(value,1,?) value,bytes FROM client_handoff_clients WHERE id=?", (MAX_ENTITY+1,_identifier(identifier))).fetchone()
        value = self._value(row)
        expected = _client({k:v for k,v in value.items() if k!="revision"}, revision=row["revision"])
        if expected != value or value["id"]!=row["id"] or type(row["revision"]) is not int or row["revision"]<1:
            raise ValueError("The stored client profile is inconsistent.")
        return value

    def _intake(self, db, identifier):
        row = db.execute("SELECT id,client_id,sha256,substr(value,1,?) value,bytes FROM client_handoff_intakes WHERE id=?", (MAX_ENTITY+1,_identifier(identifier))).fetchone()
        value = self._value(row,sha=True)
        if value["id"] != row["id"] or value["client_id"] != row["client_id"] or value["revision"] != 1:
            raise ValueError("The stored intake identity is inconsistent.")
        facts=_core(value)
        if facts!={key:value[key] for key in facts}:raise ValueError("Stored intake facts are not canonical.")
        provider=value["provider"]
        if type(provider) is not dict or type(provider.get("revision")) is not int or provider["revision"]<1 or provider.get("id")!=value["client_id"] or _client({k:v for k,v in provider.items() if k!="revision"},revision=provider["revision"])!=provider:
            raise ValueError("The immutable intake provider snapshot is inconsistent.")
        return value

    def _document(self, db, identifier):
        row = db.execute("SELECT d.id,d.kind,d.client_id,d.intake_id,d.parent_quote_id,d.revision,d.sha256,d.voided,d.created_at,substr(d.review,1,?) review,substr(d.acceptance,1,?) acceptance,substr(v.value,1,?) value,v.bytes,v.sha256 version_sha256 FROM client_handoff_documents d JOIN client_handoff_versions v ON v.document_id=d.id AND v.revision=d.revision WHERE d.id=?", (MAX_ENTITY+1,MAX_ENTITY+1,MAX_ENTITY+1,_identifier(identifier))).fetchone()
        value = self._value(row,sha=True)
        if any(value[key] != row[key] for key in ("id","kind","client_id","intake_id","parent_quote_id","revision")) or bool(row["voided"]) != (value["voided"] is not None) or row["version_sha256"]!=row["sha256"]:
            raise ValueError("The saved document metadata does not match its snapshot.")
        provider = value["provider"]
        if type(provider) is not dict or type(provider.get("revision")) is not int or provider["revision"] < 1 or _client({k:v for k,v in provider.items() if k!="revision"},revision=provider["revision"]) != provider or provider["id"] != value["client_id"]:
            raise ValueError("The immutable provider snapshot is inconsistent.")
        review = self._observation(row["review"],"review",value)
        acceptance = self._observation(row["acceptance"],"acceptance",value)
        active = db.execute("SELECT id FROM client_handoff_documents WHERE parent_quote_id=? AND kind='invoice' AND voided=0", (identifier,)).fetchone()
        return value, _assess(value,review,acceptance,invoice_id=active["id"] if active else None)

    @staticmethod
    def _observation(raw, kind, document):
        if raw is None: return None
        if type(raw) is not str or len(raw.encode()) > 8192:
            raise ValueError("A stored approval observation is oversized.")
        value = _load(raw)
        if type(value) is not dict or _json(value) != raw or value.get("content_sha256") != _digest(document) or type(value.get("document_revision")) is not int or value["document_revision"] != document["revision"]:
            raise ValueError("A saved approval observation does not match its document.")
        common={"content_sha256","document_revision","note"}
        if kind=="review":
            _fields(value,common|{"decision","reviewed_at","scope"},common|{"decision","reviewed_at","scope"})
            if value["decision"] not in ("approve","reject"): raise ValueError("Invalid saved review decision.")
            _text(value["scope"],500)
            dates=("reviewed_at",)
        else:
            fields=common|{"accepted","observed_at","recorded_at","source_ref","contact","owner_reported","independently_verified","party"}
            _fields(value,fields,fields)
            if type(value["accepted"]) is not bool or value["owner_reported"] is not True or value["independently_verified"] is not False:
                raise ValueError("Invalid saved customer observation.")
            for key,maximum in (("source_ref",1000),("contact",300),("party",500)): _text(value[key],maximum)
            dates=("observed_at","recorded_at")
        _notes(value["note"],2000,optional=True)
        for key in dates:
            stamp=datetime.fromisoformat(_text(value[key],100))
            if stamp.tzinfo is None or stamp.utcoffset() is None: raise ValueError("Stored observations require timezone-aware dates.")
        return value

    @staticmethod
    def _entity_quota(db, additional):
        total = sum(db.execute("SELECT coalesce(sum(bytes),0) FROM "+name).fetchone()[0] for name in ("client_handoff_clients","client_handoff_intakes","client_handoff_versions"))
        for name in INBOX_TABLES:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
                total += db.execute("SELECT coalesce(sum(bytes),0) FROM " + name).fetchone()[0]
        if total+additional > MAX_ENTITY_BYTES: raise ValueError("The private client-workflow storage quota is full; review retained history.")

    @staticmethod
    def _raw(value):
        raw = _json(value)
        if len(raw.encode()) > MAX_ENTITY: raise ValueError("The client-workflow record exceeds 64 KiB.")
        return raw

    @staticmethod
    def _receipt(row):
        if type(row["receipt_bytes"]) is not int or not 0 < row["receipt_bytes"] <= MAX_ENTITY or type(row["receipt"]) is not str or len(row["receipt"].encode()) != row["receipt_bytes"]:
            raise ValueError("The saved operation receipt is inconsistent.")
        value=_load(row["receipt"])
        if type(value) is not dict or _json(value)!=row["receipt"] or value.get("request_id")!=row["request_id"] or value.get("status")!=row["status"]:
            raise ValueError("The saved operation receipt identity is inconsistent.")
        return value

    def _old(self, db, body, action):
        row=db.execute("SELECT request_id,action,fingerprint,status,revision,substr(receipt,1,?) receipt,receipt_bytes FROM client_handoff_requests WHERE request_id=?",(MAX_ENTITY+1,body["request_id"])).fetchone()
        if row:
            if row["action"]!=action or row["fingerprint"]!=_digest(body): raise HandoffConflict("This request ID belongs to different arguments.")
            if row["status"]!="completed": raise HandoffConflict("Earlier work is incomplete or failed; it cannot replay or adopt output.")
            receipt=self._receipt(row)
            if action=="export": self._verify_export(receipt)
            return {**receipt,"replayed":True}
        if db.execute("SELECT count(*) FROM client_handoff_requests").fetchone()[0]>=MAX_REQUESTS:
            raise ValueError("The client-workflow request history is full; archive through a reviewed maintenance process.")
        return None

    def _save_receipt(self,db,body,action,status,revision,receipt,reserved=0):
        raw=self._raw(receipt);size=len(raw.encode())
        used=db.execute("SELECT coalesce(sum(receipt_bytes),0) FROM client_handoff_requests").fetchone()[0]
        if used+size>MAX_RECEIPT_BYTES: raise ValueError("The compact request receipt quota is full.")
        db.execute("INSERT INTO client_handoff_requests VALUES(?,?,?,?,?,?,?,?,?,?)",(body["request_id"],action,_digest(body),status,_now().isoformat(),revision,raw,size,reserved,receipt.get("entity_id")))

    def _mutate(self,action,body,change):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE");self._unpaused(db);self._initialize(db)
            old=self._old(db,body,action)
            if old:return old
            meta=self._meta(db);self._revision(meta,body["expected_revision"])
            ref=change(db,meta)
            revision=meta["revision"]+1
            db.execute("UPDATE client_handoff_meta SET revision=? WHERE id=1",(revision,))
            receipt={"schema":1,"request_id":body["request_id"],"action":action,"status":"completed","revision":revision,"demo_only":False,"replayed":False,**ref}
            self._save_receipt(db,body,action,"completed",revision,receipt)
            return receipt

    def _put_document(self,db,document,*,new=False):
        raw=self._raw(document);self._entity_quota(db,len(raw.encode()))
        digest=_digest(document)
        db.execute("INSERT INTO client_handoff_versions VALUES(?,?,?,?,?)",(document["id"],document["revision"],digest,raw,len(raw.encode())))
        if new:
            if db.execute("SELECT count(*) FROM client_handoff_documents").fetchone()[0]>=MAX_DOCUMENTS: raise ValueError("The document count limit is full.")
            db.execute("INSERT INTO client_handoff_documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",(document["id"],document["kind"],document["client_id"],document["intake_id"],document["parent_quote_id"],document["revision"],digest,int(document["voided"] is not None),_now().isoformat(),None,None))
        else:
            db.execute("UPDATE client_handoff_documents SET revision=?,sha256=?,voided=?,review=NULL,acceptance=NULL WHERE id=?",(document["revision"],digest,int(document["voided"] is not None),document["id"]))
        return {"entity_type":"document","entity_id":document["id"],"content_sha256":digest}

    @staticmethod
    def _reference(db,meta,kind):
        reference=("Q-" if kind=="quote" else "I-")+str(meta["next_reference"]).zfill(6)
        db.execute("UPDATE client_handoff_meta SET next_reference=next_reference+1 WHERE id=1")
        return reference

    def _checked_document(self,db,body,key="document_id"):
        document,assessed=self._document(db,body[key])
        if type(body["content_sha256"]) is not str or not _SHA.fullmatch(body["content_sha256"]): raise ValueError("Supply the exact document content hash.")
        if body["content_sha256"]!=assessed["content_sha256"]: raise HandoffConflict("The document changed; refresh its current version.",revision=self._meta(db)["revision"])
        return document,assessed

    def save_client(self,body):
        self._arguments(body,{"client"},{"client"})
        supplied=_client(body["client"])
        def change(db,meta):
            old=db.execute("SELECT revision,bytes FROM client_handoff_clients WHERE id=?",(supplied["id"],)).fetchone()
            if not old and db.execute("SELECT count(*) FROM client_handoff_clients").fetchone()[0]>=MAX_CLIENTS: raise ValueError("The client profile count limit is full.")
            value=_client(body["client"],revision=old["revision"]+1 if old else 1)
            raw=self._raw(value);self._entity_quota(db,max(0,len(raw.encode())-(old["bytes"] if old else 0)))
            db.execute("INSERT INTO client_handoff_clients VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,value=excluded.value,bytes=excluded.bytes",(value["id"],value["revision"],raw,len(raw.encode())))
            return {"entity_type":"client","entity_id":value["id"],"client_revision":value["revision"]}
        return self._mutate("save_client",body,change)

    def create_intake(self,body):
        self._arguments(body,{"intake"},{"intake"});value=body["intake"]
        _fields(value,_INTAKE_FIELDS,_INTAKE_FIELDS)
        core=_core(value);identifier=_identifier(value["id"]);client_id=_identifier(value["client_id"])
        def change(db,meta):
            provider=self._client(db,client_id)
            if provider["archived"]: raise HandoffConflict("This client is archived; create no new intake until explicitly restored.")
            if db.execute("SELECT 1 FROM client_handoff_intakes WHERE id=?",(identifier,)).fetchone(): raise HandoffConflict("This intake ID already exists.")
            if db.execute("SELECT count(*) FROM client_handoff_intakes").fetchone()[0]>=MAX_INTAKES: raise ValueError("The intake count limit is full.")
            intake={"id":identifier,"client_id":client_id,"provider":provider,"revision":1,"created_at":_now().isoformat(),**core}
            raw=self._raw(intake);self._entity_quota(db,len(raw.encode()));digest=_digest(intake)
            db.execute("INSERT INTO client_handoff_intakes VALUES(?,?,?,?,?,?)",(identifier,client_id,intake["created_at"],digest,raw,len(raw.encode())))
            return {"entity_type":"intake","entity_id":identifier,"content_sha256":digest}
        return self._mutate("create_intake",body,change)

    def generate_quote(self,body):
        self._arguments(body,{"intake_id","content_sha256"},{"intake_id","content_sha256"})
        def change(db,meta):
            intake=self._intake(db,body["intake_id"])
            if body["content_sha256"]!=_digest(intake): raise HandoffConflict("The intake identity changed; refresh.",revision=meta["revision"])
            if self._client(db,intake["client_id"])["archived"]: raise HandoffConflict("The client is archived.")
            if db.execute("SELECT 1 FROM client_handoff_documents WHERE intake_id=? AND kind='quote' AND voided=0",(intake["id"],)).fetchone(): raise HandoffConflict("An active quote already exists for this intake.")
            identifier=self._reference(db,meta,"quote")
            document={"id":identifier,"reference":identifier,"kind":"quote","client_id":intake["client_id"],"intake_id":intake["id"],
              "provider":intake["provider"],"parent_quote_id":None,"parent":None,"revision":1,"voided":None,
              "work":{"completed":False,"approved":False,"source_ref":None},**{key:intake[key] for key in _core(intake)}}
            _assess(document)
            return self._put_document(db,document,new=True)
        return self._mutate("generate_quote",body,change)

    def revise_document(self,body):
        self._arguments(body,{"document_id","content_sha256","changes","refresh_client"},{"document_id","content_sha256","changes"})
        if type(body.get("refresh_client",False)) is not bool: raise ValueError("refresh_client must be a boolean.")
        def change(db,meta):
            document,assessed=self._checked_document(db,body)
            if not assessed["can_edit"]: raise HandoffConflict("This document is voided or has an active invoice; its source snapshot is locked.",revision=meta["revision"])
            edits=body["changes"];_fields(edits,_QUOTE_EDITS if document["kind"]=="quote" else _INVOICE_EDITS)
            if not edits and not body.get("refresh_client",False): raise ValueError("Supply at least one explicit correction.")
            if document["kind"]=="invoice" and body.get("refresh_client",False): raise ValueError("An invoice retains the accepted quote's issuer, customer and pricing snapshot.")
            updated=copy.deepcopy(document);updated.update(copy.deepcopy(edits))
            if body.get("refresh_client",False):updated["provider"]=self._client(db,document["client_id"])
            updated.update(_core(updated));updated["work"]=_work(updated["work"])
            if updated==document: raise ValueError("The correction does not change the document.")
            updated["revision"]+=1;_assess(updated)
            return self._put_document(db,updated)
        return self._mutate("revise_document",body,change)

    def review(self,body):
        self._arguments(body,{"document_id","content_sha256","decision","note"},{"document_id","content_sha256","decision"})
        if body["decision"] not in ("approve","reject"): raise ValueError("Choose approve or reject.")
        note=_notes(body.get("note"),2000,optional=True)
        def change(db,meta):
            document,assessed=self._checked_document(db,body)
            if document["voided"] or assessed["invoice_id"]: raise HandoffConflict("Voided or invoiced source quotes cannot be re-reviewed.",revision=meta["revision"])
            if body["decision"]=="approve" and assessed["exceptions"]: raise HandoffConflict("Resolve all missing inputs and source gates before approval.",revision=meta["revision"])
            review={"decision":body["decision"],"content_sha256":body["content_sha256"],"document_revision":document["revision"],
                "note":note,"reviewed_at":_now().isoformat(),"scope":"Local owner approval of this exact draft; no sending, ledger posting, payment or independent customer verification."}
            db.execute("UPDATE client_handoff_documents SET review=?,acceptance=NULL WHERE id=?",(_json(review),document["id"]))
            return {"entity_type":"document","entity_id":document["id"],"content_sha256":body["content_sha256"],"details":{"review":review}}
        return self._mutate("review",body,change)

    def accept_quote(self,body):
        self._arguments(body,{"document_id","content_sha256","accepted","observed_at","source_ref","contact","note"},{"document_id","content_sha256","accepted","observed_at","source_ref","contact"})
        if type(body["accepted"]) is not bool or type(body["observed_at"]) is not str or len(body["observed_at"])>100: raise ValueError("Supply explicit acceptance and a bounded aware observation time.")
        observed=datetime.fromisoformat(body["observed_at"])
        if observed.tzinfo is None or observed.utcoffset() is None or observed>_now(): raise ValueError("Customer observations need a timezone and cannot be in the future.")
        reference=_text(body["source_ref"],1000);contact=_text(body["contact"],300);note=_notes(body.get("note"),2000,optional=True)
        def change(db,meta):
            document,assessed=self._checked_document(db,body)
            if not assessed["can_accept"]: raise HandoffConflict("Customer acceptance requires the current approved quote without an active invoice.",revision=meta["revision"])
            day=_local_day(observed).isoformat()
            if body["accepted"] and (day<document["document_date"] or day>document["due_date"]): raise ValueError("Acceptance must be observed within this quote's document and valid-through dates.")
            acceptance={"accepted":body["accepted"],"content_sha256":body["content_sha256"],"document_revision":document["revision"],
                "observed_at":observed.astimezone(UTC).isoformat(),"recorded_at":_now().isoformat(),"source_ref":reference,"contact":contact,"note":note,
                "owner_reported":True,"independently_verified":False,"party":"The service business's customer; not an authenticated client account."}
            db.execute("UPDATE client_handoff_documents SET acceptance=? WHERE id=?",(_json(acceptance),document["id"]))
            return {"entity_type":"document","entity_id":document["id"],"content_sha256":body["content_sha256"],"details":{"acceptance":acceptance}}
        return self._mutate("accept_quote",body,change)

    def create_invoice(self,body):
        self._arguments(body,{"quote_id","content_sha256","service_date","document_date","due_date","work"},{"quote_id","content_sha256","service_date","document_date","due_date","work"})
        work=_work(body["work"]);dates={key:_day(body[key]) for key in ("service_date","document_date","due_date")}
        def change(db,meta):
            quote,assessed=self._checked_document(db,body,"quote_id")
            if not assessed["can_invoice"]: raise HandoffConflict("An invoice requires the current approved, customer-accepted quote without an active invoice.",revision=meta["revision"])
            identifier=self._reference(db,meta,"invoice")
            document={**copy.deepcopy(quote),"id":identifier,"reference":identifier,"kind":"invoice","revision":1,"work":work,**dates,
                "parent_quote_id":quote["id"],"parent":{"quote_id":quote["id"],"content_sha256":assessed["content_sha256"],"document_revision":quote["revision"],
                    "document_date":quote["document_date"],"acceptance":assessed["acceptance"],"review":assessed["review"]}}
            result=_assess(document)
            if result["exceptions"]: raise HandoffConflict("Complete reviewed work evidence and valid invoice dates are required before invoice generation.",revision=meta["revision"])
            return self._put_document(db,document,new=True)
        return self._mutate("create_invoice",body,change)

    def void_document(self,body):
        self._arguments(body,{"document_id","content_sha256","reason"},{"document_id","content_sha256","reason"})
        reason=_notes(body["reason"],2000)
        def change(db,meta):
            document,assessed=self._checked_document(db,body)
            if not assessed["can_void"]: raise HandoffConflict("This draft is already voided or has an active invoice.",revision=meta["revision"])
            updated=copy.deepcopy(document);updated["revision"]+=1
            updated["voided"]={"reason":reason,"voided_at":_now().isoformat(),"previous_content_sha256":body["content_sha256"],
                "scope":"Unsent local draft voided. This is not a credit note, refund or recall of any externally shared copy."}
            return {**self._put_document(db,updated),"details":{"voided":updated["voided"]}}
        return self._mutate("void_document",body,change)

    @staticmethod
    def _page(offset, limit):
        if type(offset) is not int or offset<0 or offset>MAX_DOCUMENTS or type(limit) is not int or not 1<=limit<=50:
            raise ValueError("Use a nonnegative offset up to 20000 and a page limit from one to fifty.")

    @staticmethod
    def _document_summary(document):
        keys=("id","reference","kind","title","client_id","intake_id","parent_quote_id","revision","content_sha256",
              "status","total_cents","voided","invoice_id","can_edit","can_accept","can_invoice","can_void")
        return {**{key:document[key] for key in keys},"provider_name":document["provider"]["name"],"customer_name":document["customer"]["name"]}

    def _intake_summary(self, db, intake):
        active=db.execute("SELECT 1 FROM client_handoff_documents WHERE intake_id=? AND kind='quote' AND voided=0",(intake["id"],)).fetchone()
        return {**{key:intake[key] for key in ("id","client_id","title","revision","created_at")},
            "content_sha256":_digest(intake),"status":"captured",
            "can_generate_quote":not active and not self._client(db,intake["client_id"])["archived"]}

    def status(self, *, client_offset=0, intake_offset=0, document_offset=0, limit=50, client_id=None):
        for offset in (client_offset,intake_offset,document_offset):self._page(offset,limit)
        if client_id is not None:_identifier(client_id)
        result={"schema":1,"revision":0,"demo_only":False,"initialized":False,"clients":[],"intakes":[],"documents":[],
            "totals":{"clients":0,"intakes":0,"documents":0},"audit":[],"latest_export":None,"export_hold":None,
            "workflow_timezone":WORKFLOW_TIMEZONE,"client_id":client_id,"model_calls":0,"network_calls":0,
            "summary_scope":"current_document_page",
            "inbox":{"packets":[],"total":0,"offset":0,"limit":limit,"has_more":False},
            "scope":"Private owner workspace. Client profiles are not authenticated tenants. Documents remain unsent drafts; no ledger posting or payment is recorded."}
        with self.database() as db:
            if self._exists(db):
                result.update(initialized=True,revision=self._meta(db)["revision"])
                result["inbox"]=self._inbox_status(db,limit,client_id)
                where=" WHERE client_id=?" if client_id is not None else ""
                params=(client_id,) if client_id is not None else ()
                for name in ("clients","intakes","documents"):
                    result["totals"][name]=db.execute("SELECT count(*) FROM client_handoff_"+name+(where if name!="clients" else ""),params if name!="clients" else ()).fetchone()[0]
                client_ids=[row[0] for row in db.execute("SELECT id FROM client_handoff_clients ORDER BY id LIMIT ? OFFSET ?",(limit,client_offset))]
                result["clients"]=[self._client(db,identifier) for identifier in client_ids]
                intake_ids=[row[0] for row in db.execute("SELECT id FROM client_handoff_intakes"+where+" ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",(*params,limit,intake_offset))]
                result["intakes"]=[self._intake_summary(db,self._intake(db,identifier)) for identifier in intake_ids]
                document_ids=[row[0] for row in db.execute("SELECT id FROM client_handoff_documents"+where+" ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",(*params,limit,document_offset))]
                result["documents"]=[self._document_summary(self._document(db,identifier)[1]) for identifier in document_ids]
                result["audit"]=[dict(row) for row in db.execute("SELECT request_id,action,status,created_at,revision,entity_id FROM client_handoff_requests ORDER BY rowid DESC LIMIT 20")]
                latest=db.execute("SELECT request_id,status,substr(receipt,1,?) receipt,receipt_bytes FROM client_handoff_requests WHERE action='export' ORDER BY rowid DESC LIMIT 1",(MAX_ENTITY+1,)).fetchone()
                if latest:
                    saved=self._receipt(latest)
                    if latest["status"]=="completed":
                        try:self._verify_export(saved)
                        except (ValueError,OSError):
                            saved={"status":"integrity_error","request_id":saved["request_id"],"entity_id":saved.get("entity_id"),
                                "document_id":saved.get("document_id"),"content_sha256":saved.get("content_sha256"),
                                "error":"The saved export is missing or changed; do not adopt or regenerate it."}
                    result["latest_export"]=saved
                held=db.execute("SELECT request_id,status,entity_id FROM client_handoff_requests WHERE action='export' AND status IN ('running','failed') ORDER BY rowid LIMIT 1").fetchone()
                if held:result["export_hold"]={**dict(held),"reason":"Inspect and explicitly acknowledge the incomplete export before making another. Original partial output is never adopted or replayed."}
        result["pagination"]={"limit":limit,**{key:{"offset":offset,"total":result["totals"][key],"has_more":offset+len(result[key])<result["totals"][key]}
            for key,offset in (("clients",client_offset),("intakes",intake_offset),("documents",document_offset))}}
        result["summary"]={"records":len(result["documents"]),**{key:sum(row["status"]==key for row in result["documents"]) for key in ("held","ready_for_review","approved","rejected","voided")}}
        return result

    def get_client(self, identifier):
        with self.database() as db:
            if not self._exists(db):raise ValueError("No client profiles have been saved.")
            return {"revision":self._meta(db)["revision"],"client":self._client(db,identifier)}

    def get_intake(self, identifier):
        with self.database() as db:
            if not self._exists(db):raise ValueError("No job intakes have been saved.")
            value=self._intake(db,identifier)
            return {"revision":self._meta(db)["revision"],"intake":{**value,**self._intake_summary(db,value),"intake_packet":self._intake_packet_reference(db,identifier)}}

    def get_document(self, identifier):
        with self.database() as db:
            if not self._exists(db):raise ValueError("No documents have been created.")
            _,value=self._document(db,identifier)
            history=[{"revision":row["revision"],"content_sha256":row["sha256"],"bytes":row["bytes"],"current":row["revision"]==value["revision"]}
                for row in db.execute("SELECT revision,sha256,bytes FROM client_handoff_versions WHERE document_id=? ORDER BY revision DESC LIMIT 20",(identifier,))]
            events=[self._receipt(row) for row in db.execute("SELECT request_id,status,substr(receipt,1,?) receipt,receipt_bytes FROM client_handoff_requests WHERE entity_id=? ORDER BY rowid DESC LIMIT 20",(MAX_ENTITY+1,identifier))]
            value.update(intake_packet=self._intake_packet_reference(db,value["intake_id"]),history=history,history_retained=db.execute("SELECT count(*) FROM client_handoff_versions WHERE document_id=?",(identifier,)).fetchone()[0],events=events,
                evidence_scope="Customer acceptance and completed-work evidence are reported by the owner. No customer account, signature verification, external delivery or payment is established.")
            return {"revision":self._meta(db)["revision"],"document":value}

    def _verify_export(self, receipt):
        identifier=_identifier(receipt.get("request_id"))
        expected={"service-desk/"+identifier+"/document.json","service-desk/"+identifier+"/document.html"}
        artifacts=receipt.get("artifacts")
        if type(artifacts) is not list or len(artifacts)!=2 or any(type(row) is not dict for row in artifacts) or {row.get("path") for row in artifacts}!=expected:
            raise ValueError("The complete client export artifact contract is missing.")
        for item in artifacts:
            _fields(item,{"path","bytes","sha256"},{"path","bytes","sha256"})
            if type(item["bytes"]) is not int or not 0<item["bytes"]<=MAX_OUTPUT or type(item["sha256"]) is not str or not _SHA.fullmatch(item["sha256"]):
                raise ValueError("Saved export artifact metadata is inconsistent.")
            path=self.registry._path(item["path"])
            with self.registry._directory(path.parent) as directory:
                with os.fdopen(self.registry._open_read(path,directory),"rb") as source:raw=source.read(MAX_OUTPUT+1)
            if len(raw)!=item["bytes"] or hashlib.sha256(raw).hexdigest()!=item["sha256"]:
                raise ValueError("A saved client export changed; it cannot be adopted or regenerated.")

    def _replace_receipt(self,db,identifier,status,receipt,revision):
        raw=self._raw(receipt);size=len(raw.encode())
        old=db.execute("SELECT receipt_bytes FROM client_handoff_requests WHERE request_id=?",(identifier,)).fetchone()
        if not old:raise HandoffConflict("The export reservation disappeared.")
        used=db.execute("SELECT coalesce(sum(receipt_bytes),0) FROM client_handoff_requests").fetchone()[0]
        if used-old[0]+size>MAX_RECEIPT_BYTES:raise ValueError("The compact receipt quota is full.")
        db.execute("UPDATE client_handoff_requests SET status=?,revision=?,receipt=?,receipt_bytes=? WHERE request_id=?",(status,revision,raw,size,identifier))

    def export(self,body):
        self._arguments(body,{"document_id","content_sha256"},{"document_id","content_sha256"})
        identifier=body["request_id"];reserved=False;receipt=None
        try:
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE");self._unpaused(db);self._initialize(db)
                old=self._old(db,body,"export")
                if old:return old
                meta=self._meta(db);self._revision(meta,body["expected_revision"])
                hold=db.execute("SELECT request_id FROM client_handoff_requests WHERE action='export' AND status IN ('running','failed') LIMIT 1").fetchone()
                if hold:raise HandoffConflict("An earlier export requires explicit acknowledgement before another is created.",revision=meta["revision"])
                document,assessed=self._checked_document(db,body)
                if assessed["status"]!="approved" or assessed["voided"]:raise HandoffConflict("Only the current approved, unvoided draft may be exported.",revision=meta["revision"])
                packet={"schema":1,"request_id":identifier,"demo_only":False,"document":assessed,
                    "workflow_timezone":WORKFLOW_TIMEZONE,"created_at":_now().isoformat(),
                    "scope":"APPROVED DRAFT / NOT SENT. Local approval binds the included content hash. Customer acceptance and completion are owner-reported; no external delivery, ledger posting or payment is established.",
                    "model_calls":0,"network_calls":0}
                payloads={"document.json":_bytes(packet),"document.html":render_document(packet).encode("utf-8")}
                if any(len(raw)>MAX_OUTPUT for raw in payloads.values()):raise ValueError("An export file exceeds 512 KiB.")
                size=sum(map(len,payloads.values()))
                if db.execute("SELECT coalesce(sum(reserved_bytes),0) FROM client_handoff_requests WHERE action='export'").fetchone()[0]+size>MAX_EXPORT_BYTES:
                    raise ValueError("The client-workflow export quota is full; retained files require reviewed archival.")
                revision=meta["revision"]+1
                receipt={"schema":1,"request_id":identifier,"action":"export","status":"running","revision":revision,"demo_only":False,
                    "entity_type":"document","entity_id":document["id"],"document_id":document["id"],"content_sha256":assessed["content_sha256"],
                    "document_revision":document["revision"],"artifacts":[],"replayed":False,"sent":False,"posted":False,"payment_received":False,"model_calls":0,"network_calls":0}
                self._save_receipt(db,body,"export","running",revision,receipt,reserved=size)
                db.execute("UPDATE client_handoff_meta SET revision=? WHERE id=1",(revision,))
            reserved=True
            def checkpoint():
                with self.database() as db:
                    self._unpaused(db);self._revision(self._meta(db),revision)
                    current,current_assessed=self._document(db,document["id"])
                    if current!=document or current_assessed["review"]!=assessed["review"] or current_assessed["status"]!="approved":
                        raise HandoffConflict("The approved document changed during export.")
                    row=db.execute("SELECT status FROM client_handoff_requests WHERE request_id=?",(identifier,)).fetchone()
                    if not row or row[0]!="running":raise HandoffConflict("The export reservation changed.")
            folder=self.registry._path("service-desk/"+identifier)
            with self.registry._directory(folder.parent,create=True):
                folder.mkdir()
                with self.registry._directory(folder):
                    for name,raw in payloads.items():
                        checkpoint();publish_bytes(folder/name,raw,checkpoint=checkpoint)
                        receipt["artifacts"].append({"path":(folder/name).relative_to(self.registry.root).as_posix(),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
            self._verify_export(receipt)
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE");checkpoint()
                receipt["status"]="completed"
                self._replace_receipt(db,identifier,"completed",receipt,revision)
            return receipt
        except Exception as error:
            if reserved:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    row=db.execute("SELECT status FROM client_handoff_requests WHERE request_id=?",(identifier,)).fetchone()
                    if row and row[0]=="running":
                        failed={**receipt,"status":"failed","error":str(error)[:2000],"scope":"Partial output remains untrusted. Do not replay or adopt it; explicit owner acknowledgement is required."}
                        self._replace_receipt(db,identifier,"failed",failed,revision)
            raise

    def acknowledge_export(self,body):
        """Release a failure hold without adopting, deleting or replaying files."""
        self._arguments(body,set(),set())
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE");self._unpaused(db)
            if not self._exists(db):raise ValueError("No export reservation exists.")
            meta=self._meta(db)
            row=db.execute("SELECT request_id,action,status,substr(receipt,1,?) receipt,receipt_bytes FROM client_handoff_requests WHERE request_id=?",(MAX_ENTITY+1,body["request_id"])).fetchone()
            if not row or row["action"]!="export":raise ValueError("Choose an existing export reservation.")
            receipt=self._receipt(row)
            if row["status"]=="acknowledged":
                if receipt.get("acknowledgement_fingerprint")!=_digest(body):raise HandoffConflict("This export was acknowledged with different arguments.",revision=meta["revision"])
                return {**receipt,"replayed":True}
            self._revision(meta,body["expected_revision"])
            if row["status"] not in ("running","failed"):raise HandoffConflict("Only an incomplete or failed export can be acknowledged.",revision=meta["revision"])
            revision=meta["revision"]+1
            receipt.update(status="acknowledged",revision=revision,acknowledged_at=_now().isoformat(),acknowledgement_fingerprint=_digest(body),
                acknowledgement_scope="Owner acknowledged the failure. Original partial files remain untrusted; nothing was adopted, sent, posted, paid or replayed.")
            self._replace_receipt(db,body["request_id"],"acknowledged",receipt,revision)
            db.execute("UPDATE client_handoff_meta SET revision=? WHERE id=1",(revision,))
            return receipt


def render_document(packet):
    """Self-contained, script-free printable draft; all owner text is escaped."""
    escape=lambda value:html.escape(str(value if value is not None else "Not supplied"),quote=True)
    money=lambda value:"Unconfirmed" if value is None else "$"+_money(int(value))
    document=packet["document"];provider=document["provider"];customer=document["customer"]
    lines="".join("<tr><td>"+escape(row["description"])+"</td><td>"+escape(row["quantity"])+" "+escape(row["unit"])+"</td><td>"+escape("$"+row["unit_price"])+"</td><td>"+money(row["line_total_cents"])+"</td></tr>" for row in document["lines"])
    sources="".join("<li>Rate: "+escape(row["source_ref"])+"</li>" for row in document["lines"])
    acceptance=document["acceptance"] if document["kind"]=="quote" else document["parent"]["acceptance"]
    customer_evidence=("<p>Customer acceptance (owner-reported, independently unverified): "+escape(acceptance["contact"])+" · "+escape(acceptance["observed_at"])+" · "+escape(acceptance["source_ref"])+" · "+("accepted" if acceptance["accepted"] else "not accepted")+"</p>") if acceptance else "<p>No customer acceptance has been recorded.</p>"
    completion=("<li>Completed-work evidence (owner-reported): "+escape(document["work"]["source_ref"])+"</li>") if document["kind"]=="invoice" else ""
    parent=("<p>Accepted quote: "+escape(document["parent_quote_id"])+" · exact source SHA-256: "+escape(document["parent"]["content_sha256"])+"</p>") if document["kind"]=="invoice" else ""
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>'''+escape(document["reference"])+''' · Approved draft</title><style>*{box-sizing:border-box}body{margin:0;color:#17211f;background:#e8edeb;font:15px/1.55 system-ui,sans-serif}main{max-width:880px;background:white;margin:24px auto;padding:44px;border:1px solid #cbd6d0}.flag{font-size:12px;letter-spacing:.13em;font-weight:700;color:#305a49}h1{margin:22px 0 4px}h2{font-size:19px;font-weight:500}table{width:100%;border-collapse:collapse;margin-top:28px}th,td{text-align:left;padding:12px 8px;border-bottom:1px solid #d7dfdb}td:last-child,th:last-child{text-align:right}dl{display:grid;grid-template-columns:1fr auto;max-width:360px;margin:24px 0 24px auto}dd{text-align:right}dt:last-of-type,dd:last-of-type{font-weight:700}.sources{white-space:pre-wrap;overflow-wrap:anywhere}.provenance{font-size:11px;overflow-wrap:anywhere;border-top:1px solid #d7dfdb;padding-top:16px}.notice{border-left:3px solid #52795a;padding:12px;background:#f0f5f0;font-size:12px}@media(max-width:600px){main{margin:8px;padding:20px}table{font-size:12px}td,th{padding:8px 4px}}@media print{body{background:white}main{margin:0;border:0;padding:12mm;max-width:none}}</style><main>
<div class="flag">APPROVED DRAFT / NOT SENT</div><h1>'''+escape(document["kind"].title())+''' · '''+escape(document["reference"])+'''</h1><h2>'''+escape(document["title"])+'''</h2>
<p><b>Service business:</b> '''+escape(provider["name"])+'''<br>'''+escape(provider["address"])+'''<br>'''+escape(provider["email"])+'''</p>
<p><b>Customer:</b> '''+escape(customer["name"])+'''<br>'''+escape(customer["address"])+'''<br>'''+escape(customer["email"])+'''</p>
<p><b>Document date:</b> '''+escape(document["document_date"])+''' · <b>Service date:</b> '''+escape(document["service_date"])+'''<br><b>'''+("Due date" if document["kind"]=="invoice" else "Valid through")+''' ('''+escape(WORKFLOW_TIMEZONE)+'''):</b> '''+escape(document["due_date"])+'''</p>
<table><thead><tr><th>Approved scope</th><th>Quantity</th><th>Unit rate</th><th>Amount</th></tr></thead><tbody>'''+lines+'''</tbody></table><dl><dt>Subtotal</dt><dd>'''+money(document["subtotal_cents"])+'''</dd><dt>Supplied tax ('''+escape(_money(document["tax"]["bps"]))+'''%)</dt><dd>'''+money(document["tax_cents"])+'''</dd><dt>Draft total (USD)</dt><dd>'''+money(document["total_cents"])+'''</dd></dl>
<h3>Source and review record</h3><p class="sources">'''+escape(document["source_notes"])+'''</p><ul>'''+sources+'''<li>Permission / scope: '''+escape(document["permission"]["source_ref"])+'''</li><li>Supplied tax instruction: '''+escape(document["tax"]["source_ref"])+'''</li>'''+completion+'''</ul>'''+customer_evidence+parent+'''
<p class="notice">This is a local approved draft. It has not been sent, posted to a ledger or paid by this workspace. Owner review does not independently verify customer consent, tax treatment or completed work. Document face value belongs to the service business and is not Ariadne revenue or a handoff service fee.</p>
<div class="provenance">Owner review: '''+escape(document["review"]["reviewed_at"])+''' · Revision '''+str(document["revision"])+'''<br>Content SHA-256: '''+escape(document["content_sha256"])+'''<br>Export request: '''+escape(packet["request_id"])+'''</div></main></html>'''
