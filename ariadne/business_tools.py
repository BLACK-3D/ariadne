"""Local business records with integer units, balanced journals and no cloud calls."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import _allowed_path

NAMES = {"business_records"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class BusinessTools:
    def __init__(self, registry):
        self.registry = registry
        self.lock = threading.RLock()
        self.path = registry.settings.data_dir / "business.sqlite3"

    @contextmanager
    def database(self):
        path = _allowed_path(self.path)
        with self.lock, self.registry._directory(path.parent, create=True):
            if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
                raise ValueError("Business database must be a regular unlinked file.")
            db = sqlite3.connect(path, timeout=5)
            db.row_factory = sqlite3.Row
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            try:
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES('revision',0);
                CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL,notes TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,name TEXT NOT NULL,unit TEXT NOT NULL,unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents>=0));
                CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY,item_id TEXT NOT NULL REFERENCES items(id),quantity INTEGER NOT NULL,reference TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS journals(id TEXT PRIMARY KEY,memo TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS journal_lines(journal_id TEXT REFERENCES journals(id),account TEXT NOT NULL,debit_cents INTEGER NOT NULL,credit_cents INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,digest TEXT NOT NULL,result TEXT NOT NULL);
                """)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def identifier(value, label="id"):
        if type(value) is not str or not _ID.fullmatch(value):
            raise ValueError(f"{label} must be 1–64 letters, digits, underscores, dots or hyphens.")
        return value

    @staticmethod
    def text(value, label, maximum=1000, empty=False):
        if type(value) is not str or len(value) > maximum or (not empty and not value.strip()) or any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError(f"Invalid {label}.")
        return value

    @staticmethod
    def integer(value, label, low=0, high=10**12):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{label} must be an integer from {low} to {high}.")
        return value

    @staticmethod
    def fields(value, allowed, required):
        if type(value) is not dict or set(value) - set(allowed) or set(required) - set(value):
            raise ValueError("Missing or unsupported business fields.")

    def schemas(self):
        from .tools import _schema
        return [_schema("business_records", "Local CRM, inventory and balanced journal records. Money is integer cents; stock is integer named units. Writes require approval and an idempotency key. This is an operational ledger, not tax or legal certification.", {
            "action": {"type": "string", "enum": ["list_contacts", "list_items", "inventory", "trial_balance", "journal", "upsert_contact", "upsert_item", "stock_move", "post_journal"]},
            "record": {"type": "object", "description": "For contact: id,name,email,notes. Item: id,name,unit,unit_price_cents. Stock: item_id,quantity,reference. Journal: id,memo,lines[{account,debit_cents,credit_cents}]."},
            "request_id": {"type": "string", "maxLength": 64},
            "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ["action"])]

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown business tool.")
        return "auto" if args.get("action") in {"list_contacts", "list_items", "inventory", "trial_balance", "journal"} else "approval"

    def status(self):
        return {"ready": True, "offline": True, "reason": "Local contacts, inventory and balanced integer-cent journals; writes are reviewed. No external accounts or transactions."}

    def fingerprint_material(self, name, args):
        with self.database() as db:
            revision = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]
        return {"revision": revision, "implementation": "local-business-1"}

    def execute(self, name, args, *, cancelled=None):
        if name not in NAMES:
            raise ValueError("Unknown business tool.")
        self.fields(args, {"action", "record", "request_id", "limit", "offset"}, {"action"})
        action = args["action"]
        limit = self.integer(args.get("limit", 50), "limit", 1, 100)
        offset = self.integer(args.get("offset", 0), "offset", 0, 100_000)
        with self.database() as db:
            if self.policy(name, args) == "auto":
                if set(args) - {"action", "limit", "offset"}:
                    raise ValueError("Read operations accept only action and limit.")
                queries = {
                    "list_contacts": "SELECT * FROM contacts ORDER BY id LIMIT ?",
                    "list_items": "SELECT * FROM items ORDER BY id LIMIT ?",
                    "inventory": "SELECT i.id,i.name,i.unit,COALESCE(SUM(m.quantity),0) quantity,i.unit_price_cents FROM items i LEFT JOIN movements m ON i.id=m.item_id GROUP BY i.id ORDER BY i.id LIMIT ?",
                    "trial_balance": "SELECT account,SUM(debit_cents) debit_cents,SUM(credit_cents) credit_cents,SUM(debit_cents)-SUM(credit_cents) balance_cents FROM journal_lines GROUP BY account ORDER BY account LIMIT ?",
                    "journal": "SELECT j.id,j.memo,j.created,l.account,l.debit_cents,l.credit_cents FROM journals j JOIN journal_lines l ON j.id=l.journal_id ORDER BY j.created DESC,j.id,l.account LIMIT ?"}
                rows = [dict(r) for r in db.execute(queries[action] + " OFFSET ?", (limit + 1, offset))]
                return {"ok": True, "records": rows[:limit], "limit": limit, "offset": offset, "has_more": len(rows) > limit,
                        "currency_unit": "cents", "scope": "local operational records"}
            key = self.identifier(args.get("request_id"), "request_id")
            record = args.get("record")
            if type(record) is not dict:
                raise ValueError("Writes require a record object.")
            if {"limit", "offset"} & set(args):
                raise ValueError("Writes do not accept a result limit.")
            digest = hashlib.sha256(json.dumps({"action": action, "record": record}, sort_keys=True, allow_nan=False).encode()).hexdigest()
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM requests WHERE id=?", (key,)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise ValueError("request_id was already used for different inputs.")
                return {**json.loads(old["result"]), "replayed": True}
            if db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 100_000:
                raise ValueError("Local business ledger reached its 100,000-write limit; archive reviewed records before adding more.")
            if cancelled and cancelled():
                raise ValueError("Operation cancelled before commit.")
            if action == "upsert_contact":
                self.fields(record, {"id", "name", "email", "notes"}, {"id", "name"})
                values = (self.identifier(record["id"]), self.text(record["name"], "name", 200), self.text(record.get("email", ""), "email", 254, True), self.text(record.get("notes", ""), "notes", 2000, True))
                if not db.execute("SELECT 1 FROM contacts WHERE id=?", (values[0],)).fetchone() and db.execute("SELECT count(*) FROM contacts").fetchone()[0] >= 10_000:
                    raise ValueError("Contact limit reached (10,000).")
                db.execute("INSERT INTO contacts VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,email=excluded.email,notes=excluded.notes", values)
            elif action == "upsert_item":
                self.fields(record, {"id", "name", "unit", "unit_price_cents"}, {"id", "name", "unit", "unit_price_cents"})
                values = (self.identifier(record["id"]), self.text(record["name"], "name", 200), self.text(record["unit"], "unit", 30), self.integer(record["unit_price_cents"], "unit_price_cents"))
                existing = db.execute("SELECT unit FROM items WHERE id=?", (values[0],)).fetchone()
                if not existing and db.execute("SELECT count(*) FROM items").fetchone()[0] >= 10_000:
                    raise ValueError("Item limit reached (10,000).")
                if existing and existing[0] != values[2] and db.execute("SELECT 1 FROM movements WHERE item_id=?", (values[0],)).fetchone():
                    raise ValueError("An item with stock history cannot change its unit.")
                db.execute("INSERT INTO items VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,unit=excluded.unit,unit_price_cents=excluded.unit_price_cents", values)
            elif action == "stock_move":
                self.fields(record, {"item_id", "quantity", "reference"}, {"item_id", "quantity", "reference"})
                item = self.identifier(record["item_id"])
                quantity = self.integer(record["quantity"], "quantity", -10**9, 10**9)
                if not quantity or not db.execute("SELECT 1 FROM items WHERE id=?", (item,)).fetchone():
                    raise ValueError("Stock movement needs an existing item and nonzero quantity.")
                balance = db.execute("SELECT COALESCE(SUM(quantity),0) FROM movements WHERE item_id=?", (item,)).fetchone()[0]
                if not 0 <= balance + quantity <= 10**12:
                    raise ValueError("Stock movement would produce negative or excessive stock.")
                db.execute("INSERT INTO movements(item_id,quantity,reference,created) VALUES(?,?,?,?)", (item, quantity, self.text(record["reference"], "reference", 500), time.time()))
            elif action == "post_journal":
                self.fields(record, {"id", "memo", "lines"}, {"id", "memo", "lines"})
                journal_id = self.identifier(record["id"])
                if db.execute("SELECT count(*) FROM journals").fetchone()[0] >= 20_000:
                    raise ValueError("Journal limit reached (20,000).")
                lines = record["lines"]
                if type(lines) is not list or not 2 <= len(lines) <= 100:
                    raise ValueError("A journal needs 2–100 lines.")
                prepared = []
                for line in lines:
                    self.fields(line, {"account", "debit_cents", "credit_cents"}, {"account", "debit_cents", "credit_cents"})
                    debit = self.integer(line["debit_cents"], "debit_cents")
                    credit = self.integer(line["credit_cents"], "credit_cents")
                    if bool(debit) == bool(credit):
                        raise ValueError("Each line needs exactly one positive debit or credit.")
                    prepared.append((journal_id, self.identifier(line["account"], "account"), debit, credit))
                if sum(x[2] for x in prepared) != sum(x[3] for x in prepared):
                    raise ValueError("Journal debits and credits must balance exactly.")
                db.execute("INSERT INTO journals VALUES(?,?,?)", (journal_id, self.text(record["memo"], "memo", 1000), time.time()))
                db.executemany("INSERT INTO journal_lines VALUES(?,?,?,?)", prepared)
            else:
                raise ValueError("Unknown business action.")
            if cancelled and cancelled():
                raise ValueError("Operation cancelled before commit.")
            db.execute("UPDATE metadata SET value=value+1 WHERE key='revision'")
            result = {"ok": True, "action": action, "request_id": key, "revision": db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0], "replayed": False}
            db.execute("INSERT INTO requests VALUES(?,?,?)", (key, digest, json.dumps(result)))
            return result
