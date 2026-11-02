"""Optional local-model intake proposals with independently checked source spans.

A schema constrains syntax, never truth or commercial authority. Model output is
an immutable, unapproved proposal beside the unchanged evidence packet.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import re
import threading
import unicodedata
from urllib.parse import urlsplit

from .handoff import HandoffConflict, _digest, _fields, _identifier, _json, _load, _text
from .locking import InstanceLock

FIELDS = ("title", "customer.name", "customer.email", "customer.address", "service_date", "document_date", "due_date", "lines.0.description", "lines.0.quantity", "lines.0.unit", "lines.0.unit_price")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["suggestions"], "properties": {
    "suggestions": {"type": "array", "maxItems": 16, "items": {"type": "object", "additionalProperties": False,
        "required": ["field", "value", "quote"], "properties": {
            "field": {"type": "string", "enum": list(FIELDS)},
            "value": {"type": "string", "minLength": 1, "maxLength": 500},
            "quote": {"type": "string", "minLength": 1, "maxLength": 600}}}}}}
SCHEMA_SHA256 = hashlib.sha256(_json(SCHEMA).encode("utf-8")).hexdigest()
TABLES = ("client_handoff_structured_proposals", "client_handoff_structured_intakes")
ACTION = "extract_intake_proposal"
MAX_PROPOSALS = 2000
MAX_RESPONSE = 16 * 1024
SCOPE = "Owner reviewed a model proposal against retained source evidence. The model does not establish truth, currency, price approval, tax treatment, permission, acceptance, completion, delivery or payment."
_ACTIVE = set()
_ACTIVE_LOCK = threading.Lock()
_USAGE = {"profile", "model", "model_digest", "source_sha256", "schema_sha256", "request_sha256", "prompt_tokens", "output_tokens", "duration_ms", "load_ms", "prefill_ms", "decode_ms", "metadata_requests", "generation_requests"}
_SHA = re.compile(r"[a-f0-9]{64}\Z")


def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS client_handoff_structured_proposals(id TEXT PRIMARY KEY,packet_id TEXT NOT NULL,client_id TEXT NOT NULL,identity_sha256 TEXT NOT NULL,schema_sha256 TEXT NOT NULL,created_at TEXT NOT NULL,sha256 TEXT NOT NULL,value TEXT NOT NULL,bytes INTEGER NOT NULL,UNIQUE(packet_id,identity_sha256,schema_sha256))")
    db.execute("CREATE INDEX IF NOT EXISTS client_handoff_structured_packet ON client_handoff_structured_proposals(packet_id,created_at,id)")
    db.execute("CREATE TABLE IF NOT EXISTS client_handoff_structured_intakes(intake_id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL UNIQUE,value TEXT NOT NULL,bytes INTEGER NOT NULL)")


def exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='client_handoff_structured_proposals'").fetchone() is not None


def receipt_reserve(db, exclude=None):
    from .client_handoff import MAX_ENTITY
    sql = "SELECT count(*) FROM client_handoff_requests WHERE action=? AND status='running'"
    args = [ACTION]
    if exclude is not None:
        sql += " AND request_id<>?"; args.append(exclude)
    return db.execute(sql, args).fetchone()[0] * MAX_ENTITY


def identity(value):
    _fields(value, {"model", "provider", "implementation"}, {"model", "provider", "implementation"})
    model = _text(value["model"], 200)
    if model != value["model"] or any(c.isspace() for c in model):
        raise ValueError("The configured local model identity is invalid.")
    url = urlsplit(value["provider"] if type(value["provider"]) is str else "")
    try: port = url.port
    except ValueError: raise ValueError("Use a numeric-loopback model provider.") from None
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"} or port is None or not 1 <= port <= 65535 or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("Structured extraction requires a numeric-loopback HTTP provider with an explicit port.")
    if value["implementation"] != "ollama-structured-intake-v1":
        raise ValueError("The structured extraction implementation is not supported.")
    return dict(value)


def _usage(value, descriptor, packet):
    _fields(value, _USAGE, _USAGE)
    if value["profile"] != "structured_intake" or value["model"] != descriptor["model"] or value["source_sha256"] != packet["text_sha256"] or value["schema_sha256"] != SCHEMA_SHA256:
        raise ValueError("Structured extraction usage does not match its source and model identities.")
    for key in ("model_digest", "source_sha256", "schema_sha256", "request_sha256"):
        if type(value[key]) is not str or not _SHA.fullmatch(value[key]):
            raise ValueError("Structured extraction fingerprints are invalid.")
    for key in ("prompt_tokens", "output_tokens", "metadata_requests", "generation_requests"):
        if type(value[key]) is not int or not 0 <= value[key] <= 10**12:
            raise ValueError("Structured extraction counts require bounded integers.")
    if value["generation_requests"] != 1 or value["metadata_requests"] != 3:
        raise ValueError("Structured extraction requires one generation and three local metadata checks.")
    for key in ("duration_ms", "load_ms", "prefill_ms", "decode_ms"):
        if value[key] is not None and (type(value[key]) is not int or not 0 <= value[key] <= 10**12):
            raise ValueError("Structured extraction durations require integer milliseconds or null.")
    return dict(value)


def _normalized(field, raw):
    from .client_handoff import _day, _quantity
    from .business_ops import _cents, _money
    value = _text(raw, 300 if field == "title" else 40 if field == "lines.0.unit" else 500)
    if value != raw:
        raise ValueError("A model value must retain its exact supplied spelling.")
    if field.endswith("_date"):
        return _day(value)
    if field == "lines.0.quantity":
        return _quantity(value)
    if field == "lines.0.unit_price":
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,2})?", value):
            raise ValueError("Use bounded exact decimal price text.")
        return _money(_cents(value))
    return value


_NUMBER_WORDS = frozenset("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion half quarter minus negative plus".split())
_GROUP_SEPARATORS = frozenset(".,'’`_/⁄٫٬")


def _complete_numeric_context(text, start, end):
    """ASCII decimals cannot be a piece of grouped, signed or scaled numbers."""
    left, right = text[:start], text[end:]
    # Spaces include NBSP/narrow NBSP. Unicode numeric characters and fractions
    # beside a grouping separator indicate an unsupported complete scalar.
    for context in (reversed(left), iter(right)):
        significant = next((c for c in context if not c.isspace() and c not in _GROUP_SEPARATORS), None)
        if significant is not None and significant.isnumeric(): return False
    word_before = re.search(r"([^\W\d_]+)\s+$", left, re.UNICODE)
    word_after = re.match(r"^\s+([^\W\d_]+)", right, re.UNICODE)
    if any(match and match[1].casefold() in _NUMBER_WORDS for match in (word_before, word_after)): return False
    prefix = left.rstrip()
    if re.search(r"\bUS\s*\$$", prefix, re.I): prefix = re.sub(r"\bUS\s*\$$", "", prefix, flags=re.I).rstrip()
    elif prefix and unicodedata.category(prefix[-1]) == "Sc": prefix = prefix[:-1].rstrip()
    elif re.search(r"\bUSD$", prefix, re.I): prefix = prefix[:-3].rstrip()
    suffix = right.lstrip()
    if re.match(r"^(?:USD\b|US\s*\$)", suffix, re.I): suffix = re.sub(r"^(?:USD\b|US\s*\$)", "", suffix, count=1, flags=re.I).lstrip()
    elif suffix and unicodedata.category(suffix[0]) == "Sc": suffix = suffix[1:].lstrip()
    if any(c and (unicodedata.category(c) in {"Pd", "Sm"} or c in "+−﹢﹣＋－") for c in (prefix[-1:] or "", suffix[:1])):
        return False
    if prefix.endswith("(") and re.match(r"(?:\s*(?:USD|dollars?|cents?))?\s*\)", right, re.I): return False
    # A currency-separated written negative is ambiguous too.
    trailing_word = re.search(r"([^\W\d_]+)$", prefix, re.UNICODE)
    sign_word_after = re.match(r"([^\W\d_]+)", suffix, re.UNICODE)
    if any(match and match[1].casefold() in {"minus", "negative", "plus"} for match in (trailing_word, sign_word_after)): return False
    return True


def _usd_context(text, start, end):
    """Model prices need an adjacent explicit USD assertion, never just '$'."""
    left, right = text[:start], text[end:]
    tagged_dollar = re.search(r"(?<!\w)([A-Za-z]{1,5})\$\s*$", left)
    spaced_tag = re.search(r"(?<!\w)([A-Z]{1,5})\s+\$\s*$", left)
    if any(match and match[1].upper() not in {"US", "USD"} for match in (tagged_dollar, spaced_tag)): return False
    return bool(re.search(r"(?:\bUSD\s+|\bUS\s*\$\s*)$", left, re.I) or re.match(r"^\s+USD\b", right, re.I))


def validate_response(raw, packet):
    """Validate syntax independently, then retain only uniquely anchored claims."""
    if type(raw) is not str or not raw or len(raw.encode("utf-8")) > MAX_RESPONSE:
        raise ValueError("The structured model response exceeds its 16 KiB limit.")
    value = _load(raw)
    _fields(value, {"suggestions"}, {"suggestions"})
    rows = value["suggestions"]
    if type(rows) is not list or len(rows) > 16:
        raise ValueError("A structured response allows at most sixteen suggestions.")
    from .client_inbox import LABELS, _spans
    labels = {}
    for span in _spans(packet["text"]):
        match = re.fullmatch(r"\s*([^:]{1,40}):\s*(\S.*?)\s*", span["quote"])
        field = LABELS.get(match[1].casefold()) if match else None
        if match and packet["format"] == "eml" and match[1].casefold() == "from": field = "customer.email"
        if field: labels[field] = labels.get(field, 0) + 1
    deterministic = {row["field"]: row["value"] for row in packet["candidates"]}
    counts = {}
    for row in rows:
        _fields(row, {"field", "value", "quote"}, {"field", "value", "quote"})
        if type(row["field"]) is not str or row["field"] not in FIELDS:
            raise ValueError("The model supplied an unsupported intake field.")
        if type(row["value"]) is not str or not 1 <= len(row["value"]) <= 500 or type(row["quote"]) is not str or not row["quote"].strip() or len(row["quote"]) > 600:
            raise ValueError("Model values and source quotes must be bounded strings.")
        counts[row["field"]] = counts.get(row["field"], 0) + 1
    candidates, warnings = [], ["Model field assignments are unverified claims. Literal citations do not establish their meaning, currency, truth or authorization."]
    for row in rows:
        field, quote = row["field"], row["quote"]
        reason = None
        try: normalized = _normalized(field, row["value"])
        except ValueError: normalized = None; reason = "the supplied value is not a supported exact field value"
        start = packet["text"].find(quote)
        if start < 0 or packet["text"].find(quote, start + 1) >= 0:
            reason = "the quoted source is missing or occurs more than once"
        literal = re.escape(row["value"])
        numeric = field in {"lines.0.quantity", "lines.0.unit_price"}
        if numeric:
            literal = r"(?<![\w.,+\-−﹣－])" + literal + r"(?![\w,]|\.\d)"
        else:
            if row["value"][0].isalnum(): literal = r"(?<!\w)" + literal
            if row["value"][-1].isalnum(): literal += r"(?!\w)"
        # Model quotes can crop surrounding letters, decimals or signs. Check
        # boundaries in the whole source, then require the full literal inside
        # this exact quote. A regex endpos at quote.end would hide that context.
        occurrences = [match for match in re.finditer(literal, packet["text"])
                       if start >= 0 and match.start() >= start and match.end() <= start + len(quote)]
        supported = []
        for match in occurrences:
            if not numeric or _complete_numeric_context(packet["text"], match.start(), match.end()): supported.append(match)
        if field == "lines.0.unit_price":
            supported = [match for match in supported if _usd_context(packet["text"], match.start(), match.end())]
        if not supported:
            reason = "the supplied value lacks a complete unsigned source token or an explicit supported USD price context"
        if counts[field] != 1 or labels.get(field, 0) > 1:
            reason = "multiple assertions leave this field unresolved"
        if field in deterministic and normalized != deterministic[field]:
            reason = "the suggestion conflicts with an existing labeled source value"
        if field == "lines.0.unit_price" and (any(unicodedata.category(c) == "Sc" and c != "$" for c in packet["text"]) or re.search(r"\b(?:EUR|GBP|JPY|CNY|CAD|AUD|CHF|NZD|MXN|BRL|INR|KRW|RUB|ZAR|cents?)\b", packet["text"], re.I)):
            reason = "the quote uses a currency this USD-only intake does not establish"
        if reason:
            warnings.append("Unresolved " + field + ": " + reason + ".")
            continue
        candidates.append({"field": field, "value": normalized, "supplied_value": row["value"], "method": "local_model_claim",
            "line_number": packet["text"][:start].count("\n") + 1, "start": start, "end": start + len(quote), "quote": quote,
            "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest()})
    if not candidates: warnings.append("No supported uniquely source-anchored model suggestions were retained; manual review is still available.")
    proposed = copy.deepcopy(packet["proposed_intake"])
    for row in candidates:
        field = row["field"]
        if field in deterministic: continue
        if field.startswith("customer."): proposed["customer"][field.split(".")[1]] = row["value"]
        elif field.startswith("lines.0."):
            proposed["lines"][0][field.split(".")[2]] = row["value"]
            proposed["lines"][0]["source_ref"] = "Packet " + packet["id"] + ", extracted line " + str(row["line_number"]) + "; unverified model assignment"
        else: proposed[field] = row["value"]
    from .client_handoff import _core
    return candidates, list(dict.fromkeys(warnings)), _core(proposed)


@contextmanager
def _active(desk):
    key = str(desk.store.path)
    with _ACTIVE_LOCK:
        if key in _ACTIVE: raise HandoffConflict("A structured extraction is active; it cannot be acknowledged or overlapped.")
        _ACTIVE.add(key)
    lock = InstanceLock(desk.store.path.parent / "structured-intake.lock")
    try:
        lock.acquire()
        yield
    finally:
        lock.release()
        with _ACTIVE_LOCK: _ACTIVE.discard(key)


class StructuredIntakeMixin:
    def bind_structured_intake(self, generator, identity_reader):
        if not callable(generator) or not callable(identity_reader): raise ValueError("Bind trusted local structured extraction callbacks.")
        self._structured_generator, self._structured_identity_reader = generator, identity_reader

    def _structured_identity(self):
        if not callable(getattr(self, "_structured_generator", None)) or not callable(getattr(self, "_structured_identity_reader", None)):
            raise ValueError("Local structured extraction is not configured in this application.")
        return identity(self._structured_identity_reader())

    def _structured_status(self, db):
        try:
            self._structured_identity(); available = True
        except (ValueError, TypeError): available = False
        result = {"schema": 1, "available": available, "reason": "Configured local provider only; model availability is checked on explicit extraction." if available else "A trusted numeric-loopback structured extraction provider is not configured.", "hold": None}
        if self._exists(db):
            from .client_handoff import MAX_ENTITY
            row = db.execute("SELECT request_id,status,entity_id,substr(receipt,1,?) receipt,receipt_bytes FROM client_handoff_requests WHERE action=? AND status IN ('running','failed') ORDER BY rowid LIMIT 1", (MAX_ENTITY + 1, ACTION)).fetchone()
            if row:
                receipt = self._receipt(row)
                result["hold"] = {"request_id": row["request_id"], "status": row["status"], "entity_id": row["entity_id"], "receipt_sha256": _digest(receipt), "reason": "Inspect and explicitly acknowledge this incomplete or failed extraction. It is never replayed or adopted; active work cannot be acknowledged."}
        return result

    def structured_intake_status(self, packet_id=None):
        with self.database() as db:
            result = {**self._structured_status(db), "revision": self._meta(db)["revision"] if self._exists(db) else 0}
            if packet_id is not None:
                result.update(self._structured_packet_status(db, self._packet(db, packet_id)))
            return result

    def _structured_packet_status(self, db, packet):
        result = {**self._structured_status(db), "packet_id": packet["id"], "proposals": []}
        if exists(db):
            for row in db.execute("SELECT id FROM client_handoff_structured_proposals WHERE packet_id=? ORDER BY created_at DESC,id DESC LIMIT 20", (packet["id"],)):
                proposal = self._proposal(db, row["id"])
                result["proposals"].append({key: proposal[key] for key in ("id", "content_sha256", "model", "created_at")})
        return result

    def _checked_proposal_packet(self, db, body):
        packet = self._packet(db, body["packet_id"])
        if packet["content_sha256"] != body["content_sha256"]: raise HandoffConflict("The source packet identity changed; inspect it again.")
        if packet["client_changed"]: raise HandoffConflict("The source client changed or is archived; prepare a current source packet.")
        if packet["consumed_intake_id"]: raise HandoffConflict("This packet already created an intake; no new model extraction is allowed.")
        return packet

    def prepare_intake_proposal(self, body):
        self._arguments(body, {"packet_id", "content_sha256"}, {"packet_id", "content_sha256"})
        _identifier(body["packet_id"])
        if type(body["content_sha256"]) is not str or not _SHA.fullmatch(body["content_sha256"]): raise ValueError("Supply the exact source packet hash.")
        # Read-only admission does not create a lock, table or model request.
        with self.database() as db:
            self._unpaused(db)
            if not self._exists(db): raise ValueError("Import a source packet before requesting model suggestions.")
            old = self._old(db, body, ACTION)
            if old: return old
            self._revision(self._meta(db), body["expected_revision"])
            self._checked_proposal_packet(db, body)
        descriptor = self._structured_identity()
        from .client_handoff import MAX_ENTITY, MAX_RECEIPT_BYTES, _now
        receipt, reserved = None, False
        with _active(self):
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE"); self._unpaused(db); self._initialize(db)
                    old = self._old(db, body, ACTION)
                    if old: return old
                    meta = self._meta(db); self._revision(meta, body["expected_revision"])
                    if self._structured_status(db)["hold"]: raise HandoffConflict("A prior extraction requires explicit inspection and acknowledgement.", revision=meta["revision"])
                    packet = self._checked_proposal_packet(db, body)
                    if self._structured_identity() != descriptor: raise HandoffConflict("The configured model changed before extraction.")
                    previous = db.execute("SELECT id FROM client_handoff_structured_proposals WHERE packet_id=? AND identity_sha256=? AND schema_sha256=?", (packet["id"], _digest(descriptor), SCHEMA_SHA256)).fetchone()
                    revision = meta["revision"] + 1
                    identifier = "X-" + _digest({"packet": packet["content_sha256"], "model": descriptor, "schema": SCHEMA_SHA256})[:24]
                    receipt = {"schema": 1, "request_id": body["request_id"], "action": ACTION, "status": "running", "revision": revision, "demo_only": False, "replayed": False, "entity_type": "intake_proposal", "entity_id": identifier, "packet_id": packet["id"], "packet_content_sha256": packet["content_sha256"], "model_identity": descriptor, "schema_sha256": SCHEMA_SHA256, "model_calls": None, "model_call_budget": 1, "external_network_calls": 0}
                    if previous:
                        proposal = self._proposal(db, previous["id"])
                        receipt.update(status="completed", entity_id=proposal["id"], content_sha256=proposal["content_sha256"], duplicate_source=True, model_calls=0)
                        self._save_receipt(db, body, ACTION, "completed", revision, receipt)
                        db.execute("UPDATE client_handoff_meta SET revision=? WHERE id=1", (revision,))
                        return receipt
                    if db.execute("SELECT count(*) FROM client_handoff_structured_proposals").fetchone()[0] >= MAX_PROPOSALS: raise ValueError("The two-thousand structured proposal limit is full.")
                    self._entity_quota(db, MAX_ENTITY)
                    used = db.execute("SELECT coalesce(sum(receipt_bytes),0) FROM client_handoff_requests").fetchone()[0]
                    if used + MAX_ENTITY > MAX_RECEIPT_BYTES: raise ValueError("The receipt store has no reserved completion capacity.")
                    self._save_receipt(db, body, ACTION, "running", revision, receipt, reserved=MAX_ENTITY)
                    db.execute("UPDATE client_handoff_meta SET revision=? WHERE id=1", (revision,))
                reserved = True
                snapshot = {"schema": copy.deepcopy(SCHEMA), "source_text": packet["text"], "source_text_sha256": packet["text_sha256"], "packet_id": packet["id"], "packet_content_sha256": packet["content_sha256"], "model_identity": copy.deepcopy(descriptor)}
                answer = self._structured_generator(snapshot)
                _fields(answer, {"response", "usage"}, {"response", "usage"})
                usage = _usage(answer["usage"], descriptor, packet)
                candidates, warnings, proposed = validate_response(answer["response"], packet)
                if self._structured_identity() != descriptor: raise HandoffConflict("The configured model changed during extraction.")
                value = {"schema": 1, "id": identifier, "packet_id": packet["id"], "packet_content_sha256": packet["content_sha256"], "text_sha256": packet["text_sha256"], "client_id": packet["client_id"], "client_content_sha256": packet["client_content_sha256"], "created_at": _now().isoformat(), "model": descriptor["model"], "provider": descriptor["provider"], "model_identity": descriptor, "schema_sha256": SCHEMA_SHA256, "response": answer["response"], "response_sha256": hashlib.sha256(answer["response"].encode("utf-8")).hexdigest(), "usage": usage, "candidates": candidates, "warnings": warnings, "proposed_intake": proposed, "scope": SCOPE}
                raw = self._raw(value)
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE"); self._unpaused(db); self._revision(self._meta(db), revision)
                    current = self._checked_proposal_packet(db, body)
                    if current != packet or self._structured_identity() != descriptor: raise HandoffConflict("The retained source or model changed during extraction.")
                    row = db.execute("SELECT status FROM client_handoff_requests WHERE request_id=?", (body["request_id"],)).fetchone()
                    if not row or row[0] != "running": raise HandoffConflict("The extraction reservation changed.")
                    db.execute("UPDATE client_handoff_requests SET reserved_bytes=0 WHERE request_id=?", (body["request_id"],))
                    self._entity_quota(db, len(raw.encode("utf-8")))
                    db.execute("INSERT INTO client_handoff_structured_proposals VALUES(?,?,?,?,?,?,?,?,?)", (identifier, packet["id"], packet["client_id"], _digest(descriptor), SCHEMA_SHA256, value["created_at"], _digest(value), raw, len(raw.encode("utf-8"))))
                    receipt.update(status="completed", content_sha256=_digest(value), duplicate_source=False, model_calls=1)
                    self._replace_receipt(db, body["request_id"], "completed", receipt, revision)
                return receipt
            except Exception as error:
                if reserved:
                    with self.database() as db:
                        db.execute("BEGIN IMMEDIATE")
                        row = db.execute("SELECT status FROM client_handoff_requests WHERE request_id=?", (body["request_id"],)).fetchone()
                        if row and row[0] == "running":
                            failed = {**receipt, "status": "failed", "error_type": type(error).__name__[:100], "scope": "No model proposal was adopted. Inspect this receipt and explicitly acknowledge the failure before a new request; the original request is never replayed."}
                            self._replace_receipt(db, body["request_id"], "failed", failed, revision)
                            db.execute("UPDATE client_handoff_requests SET reserved_bytes=0 WHERE request_id=?", (body["request_id"],))
                raise

    def _proposal(self, db, identifier):
        from .client_handoff import MAX_ENTITY
        if not exists(db): raise ValueError("No model proposal has been saved.")
        row = db.execute("SELECT id,packet_id,client_id,identity_sha256,schema_sha256,created_at,sha256,substr(value,1,?) value,bytes FROM client_handoff_structured_proposals WHERE id=?", (MAX_ENTITY + 1, _identifier(identifier))).fetchone()
        value = self._value(row, sha=True)
        keys = {"schema", "id", "packet_id", "packet_content_sha256", "text_sha256", "client_id", "client_content_sha256", "created_at", "model", "provider", "model_identity", "schema_sha256", "response", "response_sha256", "usage", "candidates", "warnings", "proposed_intake", "scope"}
        _fields(value, keys, keys)
        descriptor = identity(value["model_identity"])
        if type(value["schema"]) is not int or value["schema"] != 1 or any(value[key] != row[key] for key in ("id", "packet_id", "client_id", "schema_sha256", "created_at")) or row["identity_sha256"] != _digest(descriptor) or value["schema_sha256"] != SCHEMA_SHA256 or value["scope"] != SCOPE or value["model"] != descriptor["model"] or value["provider"] != descriptor["provider"]:
            raise ValueError("The retained structured proposal metadata is inconsistent.")
        from datetime import datetime
        stamp = datetime.fromisoformat(_text(value["created_at"], 100))
        if stamp.tzinfo is None or stamp.utcoffset() is None: raise ValueError("Structured proposal time requires a UTC offset.")
        packet = self._packet(db, value["packet_id"])
        if any(value[key] != packet[key] for key in ("client_id", "client_content_sha256", "text_sha256")) or value["packet_content_sha256"] != packet["content_sha256"]:
            raise ValueError("The retained structured proposal source identity is inconsistent.")
        if type(value["response"]) is not str or value["response_sha256"] != hashlib.sha256(value["response"].encode("utf-8")).hexdigest() or _usage(value["usage"], descriptor, packet) != value["usage"]:
            raise ValueError("The retained model output or usage identity is inconsistent.")
        if validate_response(value["response"], packet) != (value["candidates"], value["warnings"], value["proposed_intake"]):
            raise ValueError("The retained model claims no longer match their literal evidence.")
        expected_id = "X-" + _digest({"packet": packet["content_sha256"], "model": descriptor, "schema": SCHEMA_SHA256})[:24]
        if value["id"] != expected_id: raise ValueError("The structured proposal identity is inconsistent.")
        return {**value, "content_sha256": row["sha256"], "client_changed": packet["client_changed"], "consumed_intake_id": packet["consumed_intake_id"]}

    def get_intake_proposal(self, identifier):
        with self.database() as db:
            if not self._exists(db): raise ValueError("No source has been saved.")
            return {"schema": 1, "revision": self._meta(db)["revision"], "proposal": self._proposal(db, identifier)}

    def acknowledge_intake_extraction(self, body):
        self._arguments(body, {"receipt_sha256", "inspection_confirmed"}, {"receipt_sha256", "inspection_confirmed"})
        if body["inspection_confirmed"] is not True or type(body["receipt_sha256"]) is not str or not _SHA.fullmatch(body["receipt_sha256"]): raise ValueError("Confirm inspection of this exact failed or incomplete receipt.")
        from .client_handoff import MAX_ENTITY, _now
        with _active(self), self.database() as db:
            db.execute("BEGIN IMMEDIATE"); self._unpaused(db)
            if not self._exists(db): raise ValueError("No extraction reservation exists.")
            meta = self._meta(db)
            row = db.execute("SELECT request_id,action,status,substr(receipt,1,?) receipt,receipt_bytes FROM client_handoff_requests WHERE request_id=?", (MAX_ENTITY + 1, body["request_id"])).fetchone()
            if not row or row["action"] != ACTION: raise ValueError("Choose an existing structured extraction reservation.")
            receipt = self._receipt(row)
            if row["status"] == "acknowledged":
                if receipt.get("acknowledgement_fingerprint") != _digest(body): raise HandoffConflict("This extraction was acknowledged with different arguments.")
                return {**receipt, "replayed": True}
            self._revision(meta, body["expected_revision"])
            if row["status"] not in {"running", "failed"} or _digest(receipt) != body["receipt_sha256"]: raise HandoffConflict("Only this exact failed or incomplete extraction may be acknowledged.")
            revision = meta["revision"] + 1
            receipt.update(status="acknowledged", revision=revision, acknowledged_at=_now().isoformat(), acknowledgement_fingerprint=_digest(body), acknowledgement_scope="The owner inspected the failed or incomplete extraction. No original output was adopted, retried or approved; any later generation requires a new explicit request.")
            self._replace_receipt(db, body["request_id"], "acknowledged", receipt, revision)
            db.execute("UPDATE client_handoff_requests SET reserved_bytes=0 WHERE request_id=?", (body["request_id"],))
            db.execute("UPDATE client_handoff_meta SET revision=? WHERE id=1", (revision,))
            return receipt

    def _structured_acceptance(self, db, body, packet, intake):
        pair = {"proposal_id", "proposal_sha256"} & set(body)
        if not pair: return None
        if pair != {"proposal_id", "proposal_sha256"}: raise ValueError("Supply both structured proposal identifiers.")
        proposal = self._proposal(db, body["proposal_id"])
        if type(body["proposal_sha256"]) is not str or not _SHA.fullmatch(body["proposal_sha256"]) or proposal["content_sha256"] != body["proposal_sha256"] or proposal["packet_id"] != packet["id"] or proposal["packet_content_sha256"] != packet["content_sha256"]:
            raise HandoffConflict("The reviewed model proposal does not match this exact source packet.")
        from .client_handoff import _core
        return {"proposal_id": proposal["id"], "proposal_content_sha256": proposal["content_sha256"], "model": proposal["model"], "schema_sha256": proposal["schema_sha256"], "response_sha256": proposal["response_sha256"], "packet_id": packet["id"], "packet_content_sha256": packet["content_sha256"], "facts_sha256": _digest(_core(intake)), "intake_content_sha256": _digest(intake), "review_confirmed": True, "accepted_at": intake["created_at"], "scope": SCOPE}

    def _intake_structured_reference(self, db, intake_id):
        from .client_handoff import MAX_ENTITY
        if not exists(db): return None
        row = db.execute("SELECT proposal_id,substr(value,1,?) value,bytes FROM client_handoff_structured_intakes WHERE intake_id=?", (MAX_ENTITY + 1, intake_id)).fetchone()
        if not row: return None
        value = self._value(row)
        proposal = self._proposal(db, row["proposal_id"])
        packet = self._packet(db, proposal["packet_id"])
        intake = self._intake(db, intake_id)
        if packet["consumed_intake_id"] != intake_id: raise ValueError("The model proposal is not linked to this consumed source packet.")
        expected = self._structured_acceptance(db, {"proposal_id": proposal["id"], "proposal_sha256": proposal["content_sha256"]}, packet, intake)
        if value != expected: raise ValueError("The retained structured review link does not match its proposal and intake facts.")
        return value
