"""Offline, evidence-bound intake suggestions and a passive next-action projection.

Imported documents are untrusted data. Suggestions never approve prices, taxes,
permission, completed work, customer acceptance, sending or payment.
"""
from __future__ import annotations

import base64
import binascii
import copy
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import PurePosixPath
import hashlib
import re

from . import documents
from .handoff import HandoffConflict, _digest, _fields, _identifier, _text

MAX_UPLOAD = 1_000_000
MAX_TEXT_CHARS = 10_000
MAX_TEXT_BYTES = 24_000
MAX_PACKETS = 2000
MAX_QUOTES = 32
MAX_CANDIDATES = 16
FORMATS = {"txt", "md", "csv", "tsv", "docx", "xlsx", "pdf", "eml"}
TABLES = ("client_handoff_inbox", "client_handoff_intake_sources")
SOURCE_SCOPE = "Owner reviewed source and entered intake facts. Imported claims do not approve rates, tax, permission, completion, delivery or payment."


def _check_link(value, intake, packet):
    from .client_handoff import _core
    fields = {"packet_id", "packet_content_sha256", "raw_sha256", "text_sha256", "facts_sha256", "intake_content_sha256", "accepted_at", "review_confirmed", "scope"}
    _fields(value, fields, fields)
    expected = {"packet_id": packet["id"], "packet_content_sha256": packet["content_sha256"],
                "raw_sha256": packet["raw_sha256"], "text_sha256": packet["text_sha256"],
                "facts_sha256": _digest(_core(intake)), "intake_content_sha256": _digest(intake),
                "accepted_at": intake["created_at"], "review_confirmed": True, "scope": SOURCE_SCOPE}
    if value != expected or value["review_confirmed"] is not True:
        raise ValueError("The intake source link does not match retained evidence and reviewed facts.")


def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS client_handoff_inbox(id TEXT PRIMARY KEY,client_id TEXT NOT NULL,client_sha256 TEXT NOT NULL,raw_sha256 TEXT NOT NULL,filename TEXT NOT NULL,created_at TEXT NOT NULL,sha256 TEXT NOT NULL,value TEXT NOT NULL,bytes INTEGER NOT NULL,consumed_intake_id TEXT,UNIQUE(client_id,client_sha256,raw_sha256,filename))")
    db.execute("CREATE INDEX IF NOT EXISTS client_handoff_inbox_client ON client_handoff_inbox(client_id,created_at,id)")
    db.execute("CREATE TABLE IF NOT EXISTS client_handoff_intake_sources(intake_id TEXT PRIMARY KEY,packet_id TEXT NOT NULL UNIQUE,value TEXT NOT NULL,bytes INTEGER NOT NULL)")


def exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='client_handoff_inbox'").fetchone() is not None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _eml(raw):
    # A request-local factory bounds object construction itself, before the
    # parser can build a large MIME tree. CPython may create a policy probe
    # object as well as actual parts; that probe deliberately counts too.
    created = 0
    class BoundedMessage(EmailMessage):
        def __init__(self, *args, **kwargs):
            nonlocal created
            created += 1
            if created > 40:
                raise ValueError("Email exceeds forty MIME object constructions.")
            super().__init__(*args, **kwargs)
    try:
        message = BytesParser(_class=BoundedMessage, policy=policy.default).parsebytes(raw)
    except (RecursionError, ValueError):
        raise ValueError("Malformed or overly nested MIME requires plain-text intake.") from None
    parts, pending = [], [(message, 0)]
    while pending:
        part, depth = pending.pop()
        if depth > 8 or len(parts) >= 40:
            raise ValueError("Email exceeds forty MIME parts or eight nesting levels.")
        parts.append(part)
        if part.is_multipart():
            payload = part.get_payload()
            if type(payload) is not list:
                raise ValueError("Malformed multipart email payload.")
            pending.extend((child, depth + 1) for child in reversed(payload))
    headers = []
    for name in ("Subject", "From", "To", "Date"):
        values = message.get_all(name, [])
        if len(values) > 1:
            raise ValueError("Duplicate email identity headers require manual plain-text intake.")
        if values:
            value = str(values[0])
            if len(value) > 1000 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("Email headers must be bounded single-line text.")
            headers.append(name + ": " + value)
    bodies, warnings = [], ["Email headers are source claims; sender identity and delivery are not verified."]
    for part in parts:
        if part.defects:
            raise ValueError("Malformed MIME requires a reviewed plain-text copy.")
        if part.get_content_disposition() == "attachment" or part.get_filename():
            raise ValueError("Email attachments are not imported; select an attachment separately.")
        if part.is_multipart():
            continue
        kind = part.get_content_type()
        if kind == "text/plain":
            try:
                text = part.get_content()
            except (LookupError, UnicodeError, ValueError):
                raise ValueError("Email plain text has an unsupported encoding.") from None
            if type(text) is not str or len(text) > MAX_UPLOAD:
                raise ValueError("Email plain text exceeds its extraction bound.")
            bodies.append(text)
        elif kind == "text/html":
            warnings.append("HTML email content was ignored; only plain-text MIME content is extracted.")
        else:
            raise ValueError("Email supports plain-text bodies only; other MIME payloads require separate import.")
    if not bodies:
        raise ValueError("Email needs a readable plain-text body; HTML-only messages are not imported.")
    return {"text": "\n".join(headers + ["", *bodies]), "format": "eml", "warnings": warnings,
            "processor": {"name": "stdlib-email-plain-text", "identity": "eml-v1"}}


def _snapshot(result):
    original = result["text"]
    text = original.replace("\r\n", "\n").replace("\r", "\n")
    clean = "".join(c for c in text if (ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF) or c in "\n\t")
    warnings = list(result["warnings"])
    if clean != text:
        warnings.append("Unsupported control characters were removed from the extracted snapshot.")
    text = clean[:MAX_TEXT_CHARS].encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
    truncated = len(text) < len(clean) or any("truncat" in warning.lower() for warning in warnings)
    if len(text) < len(clean):
        warnings.append("Inbox snapshot was truncated at 10000 characters or 24000 UTF-8 bytes; review the original separately.")
    if len(warnings) > 12 or any(type(w) is not str or len(w) > 1000 for w in warnings):
        raise ValueError("Extraction warning metadata exceeds its bound.")
    return text, list(dict.fromkeys(warnings)), truncated


def _spans(text):
    start = 0
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        quote = line.rstrip("\r\n")
        if quote.strip():
            yield {"line_number": number, "start": start, "end": start + len(quote), "quote": quote,
                   "quote_sha256": _sha(quote.encode("utf-8"))}
        start += len(line)


LABELS = {"title": "title", "subject": "title", "customer": "customer.name", "customer name": "customer.name",
          "customer email": "customer.email", "email": "customer.email", "customer address": "customer.address",
          "service date": "service_date", "document date": "document_date", "due date": "due_date",
          "scope": "lines.0.description", "description": "lines.0.description", "quantity": "lines.0.quantity",
          "unit": "lines.0.unit", "unit price": "lines.0.unit_price", "rate": "lines.0.unit_price"}


def _suggest(text, packet_id, format_name):
    from .client_handoff import _core, _day, _quantity
    from .business_ops import _cents, _money
    found = {}
    quotes = []
    for span in _spans(text):
        if len(quotes) < MAX_QUOTES:
            quote = span["quote"][:160]
            quotes.append({**span, "end": span["start"] + len(quote), "quote": quote,
                           "quote_sha256": _sha(quote.encode("utf-8"))})
        match = re.fullmatch(r"\s*([^:]{1,40}):\s*(\S.*?)\s*", span["quote"])
        if not match:
            continue
        label, value = match[1].casefold(), match[2]
        field = LABELS.get(label)
        method = "explicit_label"
        if format_name == "eml" and label == "from":
            # Header display names and addresses are claims, not authenticated identities.
            email_match = re.fullmatch(r"(?:[^<>\r\n]{0,300}\s*<)?([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63})>?", value)
            field = "customer.email" if email_match else None
            value = email_match[1] if email_match else value
            method = "email_header_claim"
        if not field or len(value) > 500:
            continue
        try:
            if field.endswith("_date"):
                value = _day(value)
            elif field == "lines.0.quantity":
                value = _quantity(value)
            elif field == "lines.0.unit_price":
                if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,2})?", value):
                    continue
                value = _money(_cents(value))
            else:
                value = _text(value, 300 if field == "title" else 40 if field == "lines.0.unit" else 500)
        except ValueError:
            continue
        if len(span["quote"]) > 600:
            continue
        found.setdefault(field, []).append({"field": field, "value": value, "method": method, **span})
    # Multiple assertions are deliberately left unresolved, including repeated labels.
    candidates = [rows[0] for rows in found.values() if len(rows) == 1][:MAX_CANDIDATES]
    proposal = {"title": "Review imported request", "customer": {"name": None, "email": None, "address": None},
                "source_notes": "Imported evidence packet " + packet_id + "; source claims require owner review.",
                "service_date": None, "document_date": None, "due_date": None,
                "lines": [{"description": "Review imported scope", "quantity": "1", "unit": "unit", "unit_price": None,
                           "price_approved": False, "source_ref": None}],
                "tax": {"bps": None, "approved": False, "source_ref": None},
                "permission": {"confirmed": False, "source_ref": None}}
    for candidate in candidates:
        field, value = candidate["field"], candidate["value"]
        if field.startswith("customer."):
            proposal["customer"][field.split(".")[1]] = value
        elif field.startswith("lines.0."):
            proposal["lines"][0][field.split(".")[2]] = value
            proposal["lines"][0]["source_ref"] = "Packet " + packet_id + ", extracted line " + str(candidate["line_number"])
        else:
            proposal[field] = value
    return quotes, candidates, _core(proposal)


class ClientInboxMixin:
    def prepare_intake_packet(self, body):
        self._arguments(body, {"client_id", "filename", "content_base64"}, {"client_id", "filename", "content_base64"})
        client_id = _identifier(body["client_id"])
        filename = _text(body["filename"], 180)
        if any(c in filename for c in "/\\:") or filename in {".", ".."}:
            raise ValueError("Upload a filename, never a filesystem path.")
        extension = PurePosixPath(filename).suffix.lower().lstrip(".")
        if extension not in FORMATS:
            raise ValueError("Inbox supports TXT, MD, CSV, TSV, DOCX, XLSX, PDF and plain-text EML.")
        encoded = body["content_base64"]
        if type(encoded) is not str or not encoded or len(encoded) > ((MAX_UPLOAD + 2) // 3) * 4:
            raise ValueError("Inbox uploads must contain at most 1 MB of raw bytes.")
        # Cheap read-only admission before any parser worker or MIME extraction.
        with self.database() as db:
            self._unpaused(db)
            if not self._exists(db):
                raise ValueError("Save a service-business client profile before importing evidence.")
            old = self._old(db, body, "prepare_intake_packet")
            if old:
                return old
            self._revision(self._meta(db), body["expected_revision"])
            provider = self._client(db, client_id)
            if provider["archived"]:
                raise HandoffConflict("This client is archived; no evidence packet can be prepared.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Upload content requires strict standard base64.") from None
        if not raw or len(raw) > MAX_UPLOAD or base64.b64encode(raw).decode("ascii") != encoded:
            raise ValueError("Inbox uploads require canonical base64 and 1 to 1000000 raw bytes.")
        result = _eml(raw) if extension == "eml" else documents.extract_document(filename, raw)
        text, warnings, truncated = _snapshot(result)
        if not text.strip():
            raise ValueError("No readable evidence text was extracted; use a reviewed text copy or provision OCR separately.")
        identity = {"client_id": client_id, "client_sha256": _digest(provider), "raw_sha256": _sha(raw), "filename": filename}
        identifier = "P-" + _digest(identity)[:24]
        quotes, candidates, proposal = _suggest(text, identifier, result["format"])
        from .client_handoff import _now
        packet = {"schema": 1, "id": identifier, "client_id": client_id, "client_revision": provider["revision"],
                  "client_content_sha256": _digest(provider), "created_at": _now().isoformat(), "filename": filename,
                  "format": result["format"], "raw_sha256": _sha(raw), "raw_bytes": len(raw), "text": text,
                  "text_sha256": _sha(text.encode("utf-8")), "processor": result["processor"], "warnings": warnings,
                  "truncated": truncated, "source_lines": quotes, "candidates": candidates, "proposed_intake": proposal}
        saved = self._raw(packet)
        def change(db, meta):
            if self._client(db, client_id) != provider:
                raise HandoffConflict("The client profile changed during extraction; prepare a new packet.", revision=meta["revision"])
            old = db.execute("SELECT id FROM client_handoff_inbox WHERE client_id=? AND client_sha256=? AND raw_sha256=? AND filename=?", (client_id, identity["client_sha256"], identity["raw_sha256"], filename)).fetchone()
            if old:
                existing = self._packet(db, old["id"])
                return {"entity_type": "intake_packet", "entity_id": existing["id"], "content_sha256": existing["content_sha256"], "duplicate_source": True, "model_calls": 0, "network_calls": 0}
            if db.execute("SELECT count(*) FROM client_handoff_inbox").fetchone()[0] >= MAX_PACKETS:
                raise ValueError("The two-thousand evidence packet limit is full; review retained history.")
            self._entity_quota(db, len(saved.encode("utf-8")))
            db.execute("INSERT INTO client_handoff_inbox VALUES(?,?,?,?,?,?,?,?,?,NULL)", (identifier, client_id, identity["client_sha256"], identity["raw_sha256"], filename, packet["created_at"], _digest(packet), saved, len(saved.encode("utf-8"))))
            return {"entity_type": "intake_packet", "entity_id": identifier, "content_sha256": _digest(packet), "duplicate_source": False, "model_calls": 0, "network_calls": 0}
        return self._mutate("prepare_intake_packet", body, change)

    def _packet(self, db, identifier):
        if not exists(db):
            raise ValueError("No evidence packet has been saved.")
        from .client_handoff import MAX_ENTITY, _SHA
        row = db.execute("SELECT id,client_id,client_sha256,raw_sha256,filename,created_at,sha256,substr(value,1,?) value,bytes,consumed_intake_id FROM client_handoff_inbox WHERE id=?", (MAX_ENTITY + 1, _identifier(identifier))).fetchone()
        value = self._value(row, sha=True)
        fields = {"schema", "id", "client_id", "client_revision", "client_content_sha256", "created_at", "filename", "format", "raw_sha256", "raw_bytes", "text", "text_sha256", "processor", "warnings", "truncated", "source_lines", "candidates", "proposed_intake"}
        _fields(value, fields, fields)
        if value["schema"] != 1 or value["id"] != row["id"] or value["client_id"] != row["client_id"] or value["client_content_sha256"] != row["client_sha256"] or value["raw_sha256"] != row["raw_sha256"] or value["filename"] != row["filename"] or value["created_at"] != row["created_at"]:
            raise ValueError("Stored evidence packet metadata is inconsistent.")
        if type(value["text"]) is not str or len(value["text"]) > MAX_TEXT_CHARS or len(value["text"].encode("utf-8")) > MAX_TEXT_BYTES or _sha(value["text"].encode("utf-8")) != value["text_sha256"]:
            raise ValueError("Stored evidence snapshot text is inconsistent.")
        for key in ("raw_sha256", "text_sha256", "client_content_sha256"):
            if type(value[key]) is not str or not _SHA.fullmatch(value[key]):
                raise ValueError("Stored evidence identities are invalid.")
        if type(value["raw_bytes"]) is not int or not 1 <= value["raw_bytes"] <= MAX_UPLOAD or type(value["client_revision"]) is not int or value["client_revision"] < 1 or type(value["truncated"]) is not bool:
            raise ValueError("Stored evidence limits are inconsistent.")
        extension = PurePosixPath(_text(value["filename"], 180)).suffix.lower().lstrip(".")
        if extension not in FORMATS or any(c in value["filename"] for c in "/\\:") or value["format"] != ("text" if extension in {"txt", "md"} else extension):
            raise ValueError("Stored evidence format metadata is invalid.")
        expected_processor = {"name": "stdlib-email-plain-text", "identity": "eml-v1"} if extension == "eml" else {"name": "document-text", "identity": "text-v1"}
        if value["processor"] != expected_processor or type(value["warnings"]) is not list or len(value["warnings"]) > 12 or any(type(w) is not str or len(w) > 1000 for w in value["warnings"]):
            raise ValueError("Stored extraction metadata is invalid.")
        stamp = datetime.fromisoformat(_text(value["created_at"], 100))
        identity = {"client_id": value["client_id"], "client_sha256": value["client_content_sha256"], "raw_sha256": value["raw_sha256"], "filename": value["filename"]}
        if value["id"] != "P-" + _digest(identity)[:24] or stamp.tzinfo is None or stamp.utcoffset() is None or not value["text"].strip():
            raise ValueError("Stored evidence snapshot identity is invalid.")
        link = db.execute("SELECT intake_id,substr(value,1,?) value,bytes FROM client_handoff_intake_sources WHERE packet_id=?", (MAX_ENTITY + 1, value["id"])).fetchone()
        if row["consumed_intake_id"] is not None:
            intake_id = _identifier(row["consumed_intake_id"])
            if link is None or link["intake_id"] != intake_id:
                raise ValueError("The consumed evidence packet has no matching intake source link.")
            _check_link(self._value(link), self._intake(db, intake_id), {**value, "content_sha256": row["sha256"]})
        elif link is not None:
            raise ValueError("An unconsumed evidence packet has a conflicting intake source link.")
        if _suggest(value["text"], value["id"], value["format"]) != (value["source_lines"], value["candidates"], value["proposed_intake"]):
            raise ValueError("Stored evidence suggestions do not match exact source text.")
        provider = self._client(db, value["client_id"])
        return {**value, "content_sha256": row["sha256"], "consumed_intake_id": row["consumed_intake_id"],
                "client_changed": provider["archived"] or provider["revision"] != value["client_revision"] or _digest(provider) != value["client_content_sha256"]}

    def get_intake_packet(self, packet_id):
        with self.database() as db:
            if not self._exists(db):
                raise ValueError("No evidence packet has been saved.")
            return {"schema": 1, "revision": self._meta(db)["revision"], "packet": self._packet(db, packet_id)}

    def accept_intake_packet(self, body):
        self._arguments(body, {"packet_id", "content_sha256", "review_confirmed", "intake"}, {"packet_id", "content_sha256", "review_confirmed", "intake"})
        if body["review_confirmed"] is not True:
            raise ValueError("Explicit source review is required before creating an intake.")
        from .client_handoff import _core, _QUOTE_EDITS, _now, MAX_INTAKES, _SHA
        _identifier(body["packet_id"])
        if type(body["content_sha256"]) is not str or not _SHA.fullmatch(body["content_sha256"]):
            raise ValueError("Supply the exact evidence packet content hash.")
        _fields(body["intake"], _QUOTE_EDITS, _QUOTE_EDITS)
        core = _core(body["intake"])
        # Import review is never business authorization. Ordinary document edits and
        # approval gates remain the only route to confirm these facts later.
        for line in core["lines"]:
            line["price_approved"] = False
        core["tax"]["approved"] = False
        core["permission"]["confirmed"] = False
        def change(db, meta):
            packet = self._packet(db, body["packet_id"])
            if packet["content_sha256"] != body["content_sha256"]:
                raise HandoffConflict("The evidence packet identity changed; inspect it again.", revision=meta["revision"])
            if packet["client_changed"]:
                raise HandoffConflict("The client profile changed or is archived; prepare a new packet.", revision=meta["revision"])
            if packet["consumed_intake_id"]:
                raise HandoffConflict("This packet already created an intake; inspect the saved intake instead of replaying work.", revision=meta["revision"])
            identifier = "J-" + packet["id"][2:]
            if db.execute("SELECT 1 FROM client_handoff_intakes WHERE id=?", (identifier,)).fetchone():
                raise HandoffConflict("The intake identity is already occupied.")
            if db.execute("SELECT count(*) FROM client_handoff_intakes").fetchone()[0] >= MAX_INTAKES:
                raise ValueError("The intake count limit is full.")
            intake = {"id": identifier, "client_id": packet["client_id"], "provider": self._client(db, packet["client_id"]),
                      "revision": 1, "created_at": _now().isoformat(), **copy.deepcopy(core)}
            raw = self._raw(intake)
            provenance = {"packet_id": packet["id"], "packet_content_sha256": packet["content_sha256"], "raw_sha256": packet["raw_sha256"],
                          "text_sha256": packet["text_sha256"], "facts_sha256": _digest(core), "intake_content_sha256": _digest(intake),
                          "accepted_at": intake["created_at"], "review_confirmed": True,
                          "scope": SOURCE_SCOPE}
            link = self._raw(provenance)
            self._entity_quota(db, len(raw.encode("utf-8")) + len(link.encode("utf-8")))
            db.execute("INSERT INTO client_handoff_intakes VALUES(?,?,?,?,?,?)", (identifier, intake["client_id"], intake["created_at"], _digest(intake), raw, len(raw.encode("utf-8"))))
            db.execute("INSERT INTO client_handoff_intake_sources VALUES(?,?,?,?)", (identifier, packet["id"], link, len(link.encode("utf-8"))))
            db.execute("UPDATE client_handoff_inbox SET consumed_intake_id=? WHERE id=? AND consumed_intake_id IS NULL", (identifier, packet["id"]))
            return {"entity_type": "intake", "entity_id": identifier, "content_sha256": _digest(intake),
                    "packet_id": packet["id"], "packet_content_sha256": packet["content_sha256"], "facts_sha256": _digest(core),
                    "model_calls": 0, "network_calls": 0}
        return self._mutate("accept_intake_packet", body, change)

    def _intake_packet_reference(self, db, intake_id):
        if not exists(db):
            return None
        from .client_handoff import MAX_ENTITY, _core
        row = db.execute("SELECT packet_id,substr(value,1,?) value,bytes FROM client_handoff_intake_sources WHERE intake_id=?", (MAX_ENTITY + 1, intake_id)).fetchone()
        if not row:
            return None
        value = self._value(row)
        packet = self._packet(db, row["packet_id"])
        intake = self._intake(db, intake_id)
        if packet["consumed_intake_id"] != intake_id:
            raise ValueError("The source packet was not consumed by this intake.")
        _check_link(value, intake, packet)
        return value

    def _inbox_status(self, db, limit, client_id=None):
        result = {"packets": [], "total": 0, "offset": 0, "limit": limit, "has_more": False}
        if not exists(db):
            return result
        where = " WHERE client_id=?" if client_id is not None else ""
        params = (client_id,) if client_id is not None else ()
        result["total"] = db.execute("SELECT count(*) FROM client_handoff_inbox" + where, params).fetchone()[0]
        ids = [r[0] for r in db.execute("SELECT id FROM client_handoff_inbox" + where + " ORDER BY created_at DESC,id DESC LIMIT ?", (*params, limit))]
        for identifier in ids:
            packet = self._packet(db, identifier)
            result["packets"].append({k: packet[k] for k in ("id", "client_id", "filename", "created_at", "content_sha256", "consumed_intake_id", "client_changed")})
        result["has_more"] = len(ids) < result["total"]
        return result

    def next_actions(self, limit=50, offset=0, now=None):
        from .client_handoff import _now, _local_day
        if type(offset) is not int or not 0 <= offset <= 32000 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Use a record offset from zero to 32000 and a limit from one to fifty.")
        now = _now() if now is None else now
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Next-action time must be timezone-aware.")
        result = {"schema": 1, "revision": 0, "initialized": False, "as_of": now.isoformat(), "items": [],
                  "coverage": "bounded_record_window", "truncated": False, "model_calls": 0, "network_calls": 0,
                  "scope": "Passive local next steps for the scanned record window. No messages, ledger or payment status is established."}
        with self.database() as db:
            if not self._exists(db):
                result["pagination"] = {"offset": offset, "limit": limit, "total_records": 0, "scanned": 0, "has_more": False, "next_offset": None}
                return result
            result.update(initialized=True, revision=self._meta(db)["revision"])
            parts = ["SELECT 'document' entity_type,id,created_at FROM client_handoff_documents", "SELECT 'intake' entity_type,id,created_at FROM client_handoff_intakes"]
            if exists(db):
                parts.append("SELECT 'intake_packet' entity_type,id,created_at FROM client_handoff_inbox")
            query = " UNION ALL ".join(parts)
            total = db.execute("SELECT count(*) FROM (" + query + ")").fetchone()[0]
            rows = db.execute("SELECT * FROM (" + query + ") ORDER BY created_at DESC,entity_type,id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            def action(kind, entity_type, value, message):
                result["items"].append({"id": kind + ":" + value["id"], "kind": kind, "entity_type": entity_type,
                    "entity_id": value["id"], "client_id": value["client_id"], "title": value.get("title", value.get("filename", "Review source")),
                    "message": message, "content_sha256": value["content_sha256"]})
            for row in rows:
                kind = row["entity_type"]
                if kind == "intake_packet":
                    packet = self._packet(db, row["id"])
                    if not packet["consumed_intake_id"]:
                        action("source_review", kind, packet, "Client profile changed; prepare a new packet." if packet["client_changed"] else "Review exact extracted source and candidate fields before creating an intake.")
                elif kind == "intake":
                    intake = self._intake(db, row["id"])
                    summary = self._intake_summary(db, intake)
                    if summary["can_generate_quote"]:
                        action("generate_quote", kind, {**intake, "content_sha256": _digest(intake)}, "Create a held quote draft, then resolve its facts and approval gates.")
                else:
                    _, document = self._document(db, row["id"])
                    if document["voided"] or document["invoice_id"]:
                        continue
                    if document["exceptions"]:
                        action("missing_facts", kind, document, "Resolve source and fact gates: " + "; ".join(dict.fromkeys(e["message"] for e in document["exceptions"]))[:2000])
                    elif document["status"] in {"ready_for_review", "rejected"}:
                        action("owner_review", kind, document, "Owner review is required for this exact current document hash.")
                    elif document["kind"] == "quote" and not (document["acceptance"] and document["acceptance"]["accepted"]):
                        if document["due_date"] and document["due_date"] < _local_day(now).isoformat():
                            action("expired_quote", kind, document, "The quote valid-through date passed. Review validity; only a genuine earlier within-window acceptance observation can be recorded against this version.")
                        else:
                            action("customer_acceptance", kind, document, "Record owner-reported customer acceptance; owner approval alone does not establish it.")
                    elif document["kind"] == "quote":
                        action("completion_evidence", kind, document, "Review completed-work evidence and invoice dates before creating an invoice draft.")
                    elif document["kind"] == "invoice":
                        action("payment_status_unknown", kind, document, "Payment status is unknown; this workspace records no cash receipt or unpaid balance.")
                        if document["due_date"] and document["due_date"] < _local_day(now).isoformat():
                            action("due_date_review", kind, document, "The stated invoice due date has passed; review payment status externally. No unpaid debt is inferred.")
            hold = db.execute("SELECT request_id,status,entity_id FROM client_handoff_requests WHERE action='export' AND status IN ('running','failed') ORDER BY rowid LIMIT 1").fetchone()
            if hold:
                result["items"].append({"id": "export_hold:" + hold["request_id"], "kind": "export_hold", "entity_type": "request", "entity_id": hold["request_id"], "client_id": None, "title": "Inspect held export", "message": "Inspect and explicitly acknowledge the interrupted or failed export; never replay or adopt partial output.", "content_sha256": None})
            has_more = offset + len(rows) < total
            result["pagination"] = {"offset": offset, "limit": limit, "total_records": total, "scanned": len(rows), "has_more": has_more, "next_offset": offset + len(rows) if has_more else None}
            result["truncated"] = offset > 0 or has_more
        return result
