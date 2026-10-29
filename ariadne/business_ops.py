"""Offline business review and ERPNext-compatible invoice drafts.

No ERP client, network request, ledger posting or inferred tax policy lives here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import os
import re

NAMES = {"business_ops"}
MAX_BYTES = 262_144
MAX_ROWS = 500
MAX_MONEY = 10**15
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_PRIMARY = ["https://docs.frappe.io/framework/user/en/api/rest",
            "https://docs.frappe.io/framework/doctypes/docstatus",
            "https://docs.frappe.io/erpnext/sales-invoice"]


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _fields(value, allowed, required=()):
    if type(value) is not dict or set(value)-set(allowed) or set(required)-set(value):
        raise ValueError("Missing or unsupported business-operation fields.")


def _text(value, maximum=200):
    if type(value) is not str or not value.strip() or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("Business text must be bounded and contain no control characters.")
    return value


def _int(value, low=0, high=MAX_MONEY):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Business quantity or rate is outside its integer bounds.")
    return value


def _day(value):
    if type(value) is not str:
        raise ValueError("Business dates require YYYY-MM-DD.")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("Business dates require YYYY-MM-DD.")
    return result


def _cents(value):
    if type(value) not in (str, int, Decimal) or len(str(value)) > 40:
        raise ValueError("Money requires finite exact decimal text or an integer.")
    try:
        with localcontext() as context:
            context.prec = 50
            number = Decimal(value)
            scaled = number * 100
            if not number.is_finite() or number < 0 or scaled != scaled.to_integral_value() or scaled > MAX_MONEY:
                raise ValueError("Only nonnegative money with at most two decimal places is supported.")
            return int(scaled)
    except (InvalidOperation, OverflowError):
        raise ValueError("Money requires finite bounded exact decimals.") from None


def _money(cents):
    return f"{cents//100}.{cents%100:02d}"


def _round_ratio(numerator, denominator=10_000):
    # All inputs are nonnegative integers. Integer half-up avoids binary floats.
    return (numerator * 2 + denominator) // (denominator * 2)


class BusinessOps:
    def __init__(self, registry):
        self.registry = registry

    def schemas(self):
        from .tools import _schema
        return [_schema("business_ops", "Offline local business snapshot, ERPNext exported-invoice audit or quote-to-draft invoice. Exact integer-cent math; no ledger changes, ERP connection, submission or sending. Writes one immutable JSON report under business-ops/<request_id>.json. Use a new request_id for changed input. Existing completed results replay only if their verified input identities still match.", {
            "action": {"type": "string", "enum": ["snapshot", "audit_erp", "draft_quote"]},
            "request_id": {"type": "string", "maxLength": 64},
            "path": {"type": "string", "description": "Workspace JSON input; quote or documented ERPNext export envelope."},
            "as_of": {"type": "string", "description": "YYYY-MM-DD. Required for ERP ageing; optional snapshot label."},
            "cash_accounts": {"type": "array", "maxItems": 20, "items": {"type": "string"}, "description": "Snapshot only: explicit local account IDs. No account-name inference."}}, ["action", "request_id"])]

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown business-operation tool.")
        return "auto"

    def status(self):
        return {"ready": True, "offline": True, "reason": "Local stock/cash review, exported ERPNext invoice ageing and checked quote drafts; no resident ERP or network dependency."}

    def _read(self, relative):
        path = self.registry._path(relative)
        if path.suffix.lower() != ".json":
            raise ValueError("Business inputs must be workspace JSON files.")
        with self.registry._directory(path.parent) as directory_fd:
            with os.fdopen(self.registry._open_read(path, directory_fd), "rb") as source:
                payload = source.read(MAX_BYTES+1)
        if len(payload) > MAX_BYTES:
            raise ValueError("Business input exceeds 256 KiB.")
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("Duplicate JSON keys are unsupported.")
                result[key] = value
            return result
        try:
            value = json.loads(payload.decode("utf-8-sig"), parse_float=Decimal,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON is unsupported.")), object_pairs_hook=pairs)
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise ValueError("Business input must be bounded UTF-8 JSON.") from None
        return value, {"path": path.relative_to(self.registry.root).as_posix(), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

    def _local(self):
        business = self.registry.business
        with business.database() as db:
            db.execute("BEGIN")
            revision = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]
            contacts = [dict(row) for row in db.execute("SELECT id,name FROM contacts ORDER BY id LIMIT 10001")]
            items = [dict(row) for row in db.execute("SELECT i.id,i.name,i.unit,i.unit_price_cents,COALESCE(SUM(m.quantity),0) quantity FROM items i LEFT JOIN movements m ON m.item_id=i.id GROUP BY i.id ORDER BY i.id LIMIT 10001")]
            accounts = [dict(row) for row in db.execute("SELECT account,SUM(debit_cents) debit_cents,SUM(credit_cents) credit_cents FROM journal_lines GROUP BY account ORDER BY account LIMIT 10001")]
        if max(len(contacts), len(items), len(accounts)) > 10_000:
            raise ValueError("Local business snapshot exceeds the 10,000-record category limit.")
        value = {"revision": revision, "contacts": contacts, "items": items, "accounts": accounts}
        return value, {"kind": "local-business-records", "revision": revision, "sha256": _digest(value), "scope": "One read transaction of contacts, items, recorded stock and journal balances; no bank reconciliation."}

    def _snapshot(self, args, local):
        selected = args.get("cash_accounts", [])
        if type(selected) is not list or len(selected) > 20 or any(type(account) is not str for account in selected) or len(set(selected)) != len(selected):
            raise ValueError("Select at most twenty unique cash-account IDs.")
        for account in selected:
            self.registry.business.identifier(account, "cash account")
        accounts = {row["account"]: row for row in local["accounts"]}
        if any(account not in accounts for account in selected):
            raise ValueError("A selected cash account has no recorded journal lines.")
        debits = sum(row["debit_cents"] for row in local["accounts"])
        credits = sum(row["credit_cents"] for row in local["accounts"])
        inventory = []
        for row in local["items"]:
            inventory.append({"item_id": row["id"], "name": row["name"], "unit": row["unit"], "quantity": row["quantity"],
                "unit_price_cents": str(row["unit_price_cents"]), "indicative_sales_value_cents": str(row["quantity"]*row["unit_price_cents"])})
        return {"as_of": args.get("as_of", datetime.now(timezone.utc).date().isoformat()), "currency": None, "currency_assumption": "Existing local ledger stores cents without currency metadata; currency is unspecified. No conversion or currency inference is performed.",
            "contact_count": len(local["contacts"]), "item_count": len(inventory),
            "inventory": inventory, "zero_stock_item_ids": [row["item_id"] for row in inventory if row["quantity"] == 0],
            "indicative_inventory_sales_value_cents": str(sum(int(row["indicative_sales_value_cents"]) for row in inventory)),
            "journal": {"debit_cents": str(debits), "credit_cents": str(credits), "balanced": debits == credits,
                "accounts": [{"account": row["account"], "debit_cents": str(row["debit_cents"]), "credit_cents": str(row["credit_cents"]), "balance_cents": str(row["debit_cents"]-row["credit_cents"])} for row in local["accounts"]]},
            "selected_cash": {"accounts": selected, "balance_cents": str(sum(accounts[key]["debit_cents"]-accounts[key]["credit_cents"] for key in selected)) if selected else None,
                "coverage": "Explicit selected journal accounts only; no bank/feed verification, forecast or inferred available balance."},
            "valuation_basis": "Current configured selling prices, not inventory cost, realizable value or profit."}

    def _quote(self, value, local):
        _fields(value, {"customer_id", "company", "posting_date", "due_date", "currency", "tax_bps", "tax_account", "lines"},
            {"customer_id", "company", "posting_date", "due_date", "currency", "tax_bps", "lines"})
        customer = self.registry.business.identifier(value["customer_id"], "customer_id")
        contacts = {row["id"]: row for row in local["contacts"]}
        if customer not in contacts:
            raise ValueError("Quote customer must exist in local contacts.")
        company = _text(value["company"])
        posting, due = _day(value["posting_date"]), _day(value["due_date"])
        if due < posting or value["currency"] != "USD":
            raise ValueError("Quote requires USD and a due date on or after its posting date.")
        tax_bps = _int(value["tax_bps"], 0, 10_000)
        tax_account = _text(value.get("tax_account")) if tax_bps else None
        lines = value["lines"]
        if type(lines) is not list or not 1 <= len(lines) <= 100:
            raise ValueError("A quote needs 1–100 item lines.")
        items = {row["id"]: row for row in local["items"]}
        seen, prepared, payload_items, shortages = set(), [], [], []
        gross_total = discount_total = subtotal = 0
        for line in lines:
            _fields(line, {"item_id", "quantity", "discount_bps"}, {"item_id", "quantity"})
            item_id = self.registry.business.identifier(line["item_id"], "item_id")
            if item_id in seen or item_id not in items:
                raise ValueError("Quote item IDs must be unique existing local items.")
            seen.add(item_id)
            item = items[item_id]
            qty = _int(line["quantity"], 1, 10**9)
            discount_bps = _int(line.get("discount_bps", 0), 0, 10_000)
            gross = item["unit_price_cents"]*qty
            if gross > MAX_MONEY:
                raise ValueError("A quote line exceeds the bounded money limit.")
            unit_discount = _round_ratio(item["unit_price_cents"]*discount_bps)
            net_unit_price = item["unit_price_cents"]-unit_discount
            discount = unit_discount*qty
            net = gross-discount
            gross_total += gross; discount_total += discount; subtotal += net
            prepared.append({"item_id": item_id, "name": item["name"], "unit": item["unit"], "quantity": qty,
                "unit_price_cents": str(item["unit_price_cents"]), "discount_bps": discount_bps, "unit_discount_cents": str(unit_discount),
                "net_unit_price_cents": str(net_unit_price), "gross_cents": str(gross), "discount_cents": str(discount), "net_cents": str(net), "recorded_stock": item["quantity"]})
            # Carry the already discounted, exact two-decimal rate into the
            # draft. Passing a percentage as well could apply it twice.
            payload_items.append({"item_code": item_id, "item_name": item["name"], "qty": qty, "uom": item["unit"],
                "rate": _money(net_unit_price), "amount": _money(net)})
            if qty > item["quantity"]:
                shortages.append({"item_id": item_id, "requested": qty, "recorded_available": item["quantity"], "shortfall": qty-item["quantity"]})
        tax = _round_ratio(subtotal*tax_bps)
        if subtotal+tax > MAX_MONEY:
            raise ValueError("Quote total exceeds the bounded money limit.")
        return {"status": "DRAFT_ONLY", "customer_id": customer, "customer_name": contacts[customer]["name"],
            "currency": "USD", "currency_assumption": "Owner-supplied quote declares that local cent prices are USD; no conversion is performed.", "lines": prepared, "stock_exceptions": shortages,
            "gross_cents": str(gross_total), "discount_cents": str(discount_total), "subtotal_cents": str(subtotal),
            "tax_bps": tax_bps, "tax_cents": str(tax), "total_cents": str(subtotal+tax), "total": _money(subtotal+tax),
            "rounding": "Discount rounded half up to cents per unit before quantity multiplication; tax rounded half up once on the discounted subtotal. ERP draft rates carry the net price, with no second discount percentage. No jurisdiction or tax applicability is inferred.",
            "erpnext_draft": {"doctype": "Sales Invoice", "docstatus": 0, "company": company, "customer": customer,
                "posting_date": posting.isoformat(), "due_date": due.isoformat(), "currency": "USD", "update_stock": 0,
                "items": payload_items, "taxes": [{"charge_type": "On Net Total", "account_head": tax_account,
                    "description": "Owner-specified quote tax", "rate": _money(tax_bps)}] if tax_bps else []},
            "compatibility": "ERPNext-shaped review draft, not an installed ERP service or import certification. Match company/customer/item/UOM/account IDs and reconcile site precision, tax rules and recalculated totals before a separately approved import. Stock is not reserved or deducted."}

    def _audit(self, value, as_of, *, now=None):
        _fields(value, {"schema", "provider", "captured_at", "currency", "invoices"}, {"schema", "provider", "captured_at", "currency", "invoices"})
        if type(value["schema"]) is not int or value["schema"] != 1 or value["provider"] != "erpnext" or value["currency"] != "USD":
            raise ValueError("ERP audit requires the documented schema-1 ERPNext USD envelope.")
        stamp = value["captured_at"]
        if type(stamp) is not str or len(stamp) > 40:
            raise ValueError("ERP captured_at requires an aware ISO timestamp.")
        captured = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        now = now or datetime.now(timezone.utc)
        if captured.tzinfo is None or captured.timestamp() > now.timestamp()+300:
            raise ValueError("ERP captured_at requires a nonfuture aware ISO timestamp.")
        _fields(value["invoices"], {"data"}, {"data"})
        rows = value["invoices"]["data"]
        if type(rows) is not list or len(rows) > MAX_ROWS:
            raise ValueError("ERP audit accepts at most 500 invoice records.")
        seen, details, buckets = set(), [], {"not_due": 0, "1_30_days": 0, "31_60_days": 0, "61_90_days": 0, "over_90_days": 0}
        submitted = draft = cancelled = total = overdue = 0
        required = {"name", "customer", "posting_date", "due_date", "currency", "docstatus", "grand_total", "outstanding_amount"}
        for row in rows:
            # Native ERP records may contain extra fields. They remain inert and are not copied into the report.
            if type(row) is not dict or required-set(row):
                raise ValueError("An ERP invoice lacks required audit fields.")
            name = _text(row["name"])
            customer = _text(row["customer"])
            if name in seen:
                raise ValueError("ERP invoice names must be unique; combine each export page only once.")
            seen.add(name)
            posting, due = _day(row["posting_date"]), _day(row["due_date"])
            status = _int(row["docstatus"], 0, 2)
            amount, outstanding = _cents(row["grand_total"]), _cents(row["outstanding_amount"])
            if row["currency"] != "USD" or outstanding > amount or due < posting or posting > as_of:
                raise ValueError("ERP audit cannot mix currencies, future invoices, negative terms or inconsistent outstanding balances.")
            days = max(0, (as_of-due).days)
            bucket = "not_due" if not days else "1_30_days" if days <= 30 else "31_60_days" if days <= 60 else "61_90_days" if days <= 90 else "over_90_days"
            if status == 1:
                submitted += 1; total += outstanding; buckets[bucket] += outstanding
                if days:
                    overdue += outstanding
            elif status == 0:
                draft += 1
            else:
                cancelled += 1
            details.append({"invoice_id": name, "customer_id": customer, "docstatus": status,
                "due_date": due.isoformat(), "outstanding_cents": str(outstanding), "days_overdue": days,
                "included_in_receivables": status == 1, "ageing_bucket": bucket if status == 1 else None})
        return {"provider": "erpnext", "currency": "USD", "as_of": as_of.isoformat(), "captured_at": captured.astimezone(timezone.utc).isoformat(),
            "snapshot_age_seconds": max(0, round((now-captured.astimezone(timezone.utc)).total_seconds())),
            "invoice_count": len(rows), "submitted_count": submitted, "excluded_draft_count": draft, "excluded_cancelled_count": cancelled,
            "receivables_cents": str(total), "overdue_cents": str(overdue), "ageing_cents": {key: str(amount) for key, amount in buckets.items()},
            "invoices": details, "coverage": "Only supplied submitted sales invoices; completeness, authentication and source capture are owner assertions. Not current bank cash, a full general ledger, foreign-exchange conversion or a tax filing. Credit notes/negative invoices require a separate supported workflow."}

    def execute(self, name, args, *, cancelled=None):
        if name not in NAMES:
            raise ValueError("Unknown business-operation tool.")
        _fields(args, {"action", "request_id", "path", "as_of", "cash_accounts"}, {"action", "request_id"})
        request = args["request_id"]
        if type(request) is not str or not _KEY.fullmatch(request):
            raise ValueError("request_id requires 1–64 letters, digits, underscores or hyphens.")
        action = args["action"]
        if type(action) is not str or action not in {"snapshot", "audit_erp", "draft_quote"}:
            raise ValueError("Unknown business-operation action.")
        if cancelled and cancelled():
            raise ValueError("Business operation cancelled before input reads.")
        if action == "snapshot":
            if "path" in args:
                raise ValueError("snapshot does not accept an input path.")
            if "as_of" in args:
                if _day(args["as_of"]) != datetime.now(timezone.utc).date():
                    raise ValueError("snapshot reports current recorded state; historical or future snapshots are unsupported.")
            local, identity = self._local()
            sources = [identity]
            result = self._snapshot(args, local)
        else:
            if "cash_accounts" in args or "path" not in args:
                raise ValueError("This operation requires an input path and accepts no cash accounts.")
            value, identity = self._read(args["path"])
            sources = [identity]
            if action == "draft_quote":
                if "as_of" in args:
                    raise ValueError("draft_quote takes its dates from the input file.")
                local, identity = self._local()
                sources.append(identity)
                result = self._quote(value, local)
            else:
                if "as_of" not in args:
                    raise ValueError("audit_erp requires an explicit as_of date.")
                result = self._audit(value, _day(args["as_of"]))
        input_digest = _digest({"implementation": "business-ops-1", "args": args, "sources": sources})
        relative = "business-ops/" + request + ".json"
        destination = self.registry._path(relative)
        replayed = False
        if destination.exists():
            prior, artifact = self._read(relative)
            if type(prior) is not dict or prior.get("schema") != 1 or prior.get("input_sha256") != input_digest or prior.get("request_id") != request or prior.get("action") != action or prior.get("result_sha256") != _digest(prior.get("result")):
                raise ValueError("Completed report or input identities changed; choose a new request_id and review the old report.")
            stamp = prior.get("created_at")
            if type(stamp) is not str or len(stamp) > 40:
                raise ValueError("Completed report creation time is invalid.")
            created = datetime.fromisoformat(stamp)
            if created.tzinfo is None or created.timestamp() > datetime.now(timezone.utc).timestamp()+300:
                raise ValueError("Completed report creation time is invalid.")
            # Recompute from current hash-bound inputs at the saved observation
            # clock. A caller-editable result and its self-declared hash cannot
            # authenticate a total, source identity or authority statement.
            if action == "audit_erp":
                result = self._audit(value, _day(args["as_of"]), now=created)
            elif action == "snapshot" and "as_of" not in args:
                result = self._snapshot({**args, "as_of": created.astimezone(timezone.utc).date().isoformat()}, local)
            expected = {"schema": 1, "implementation": "business-ops-1", "request_id": request, "action": action,
                "created_at": stamp, "input_sha256": input_digest, "sources": sources,
                "result": result, "result_sha256": _digest(result), "primary_contracts": _PRIMARY,
                "authority": "Local report/draft only; no record mutation, reservation, posting, sending, payment or network request."}
            if _json(prior) != _json(expected):
                raise ValueError("Completed report differs from recomputed inputs; the existing file was preserved for review.")
            replayed = True
        else:
            report = {"schema": 1, "implementation": "business-ops-1", "request_id": request, "action": action,
                "created_at": datetime.now(timezone.utc).isoformat(), "input_sha256": input_digest, "sources": sources,
                "result": result, "result_sha256": _digest(result), "primary_contracts": _PRIMARY,
                "authority": "Local report/draft only; no record mutation, reservation, posting, sending, payment or network request."}
            payload = _json(report)
            if len(payload) > min(MAX_BYTES, 100_000):
                raise ValueError("Business report exceeds the bounded artifact size; narrow source records.")
            if cancelled and cancelled():
                raise ValueError("Business operation cancelled before report publication.")
            written = self.registry._file_write({"path": relative, "content": payload.decode("utf-8")})
            artifact = {"path": written["path"], "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        summary = ({"item_count": result["item_count"], "contact_count": result["contact_count"], "journal_balanced": result["journal"]["balanced"], "selected_cash_balance_cents": result["selected_cash"]["balance_cents"]} if action == "snapshot" else
                   {"status": result["status"], "total": result["total"], "currency": "USD", "stock_exceptions": result["stock_exceptions"]} if action == "draft_quote" else
                   {"invoice_count": result["invoice_count"], "receivables_cents": result["receivables_cents"], "overdue_cents": result["overdue_cents"], "captured_at": result["captured_at"]})
        return {"ok": True, "action": action, "request_id": request, "replayed": replayed,
            "input_sha256": input_digest, "artifacts": [artifact], "summary": summary, "model_calls": 0, "offline": True}
