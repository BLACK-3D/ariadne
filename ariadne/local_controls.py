"""Fixed Home Assistant observations and individually approved local actions.

Transport, credentials and process network policy belong to the existing connector
system. This adapter never discovers devices or changes that policy.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
from urllib.parse import urlsplit

from .config import _allowed_path

NAMES = {"home_control"}
UTC = timezone.utc
MAX_BYTES = 65_536
_ID = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_ENTITY = re.compile(r"[a-z][a-z0-9_]{0,31}\.[a-z0-9_]{1,96}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ATTRS = {"friendly_name", "unit_of_measurement", "device_class", "battery_level", "brightness", "temperature", "current_temperature"}
_DEFAULT = {"schema": 1, "enabled": False, "read_connector": None, "entities": [], "actions": [], "stale_seconds": 300}


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _parse(payload):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Duplicate local control JSON keys.")
            result[key] = value
        return result
    return json.loads(payload, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite local control data.")))


def _text(value, limit):
    if type(value) is not str or len(value) > limit or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("Local control data contains invalid bounded text.")
    return value


def _date(value):
    _text(value, 40)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Control observation timestamps need an explicit timezone.")
    return result.astimezone(UTC)


class LocalControls:
    def __init__(self, registry):
        self.registry, self.store = registry, None
        self.path = registry.settings.data_dir / "local-controls.sqlite3"
        self.config_path = registry.settings.data_dir / "local-controls.json"
        self.lock = threading.RLock()

    def _read(self, path):
        path = _allowed_path(path)
        with self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path, directory), "rb") as source:
                raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Local control input exceeds 64 KiB.")
        return raw

    @contextmanager
    def database(self):
        path = _allowed_path(self.path)
        with self.lock, self.registry._directory(path.parent, create=True):
            for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm"), path.with_name(path.name + "-journal")):
                _allowed_path(candidate)
                if candidate.exists() and (not stat.S_ISREG(candidate.lstat().st_mode) or candidate.lstat().st_nlink != 1):
                    raise ValueError("Control state must use regular unlinked files.")
            db = sqlite3.connect(path, timeout=5)
            db.row_factory = sqlite3.Row
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            try:
                db.execute("PRAGMA trusted_schema=OFF")
                db.executescript("""
                CREATE TABLE IF NOT EXISTS control_cache(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS control_actions(request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL);
                """)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def _endpoint(connector, expected, origin=None):
        parsed = urlsplit(connector["url"])
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            raise ValueError("Home control endpoints require an explicit numeric local address.") from None
        allowed = address.is_loopback or any(address in network for network in (
            ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7")) if address.version == network.version)
        if (not allowed or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != expected):
            raise ValueError("Home control requires a fixed local Home Assistant endpoint.")
        identity = (parsed.scheme, address.compressed, parsed.port or (443 if parsed.scheme == "https" else 80))
        if origin is not None and identity != origin:
            raise ValueError("Home control action and observation endpoints must share one origin.")
        return identity

    def configuration(self):
        if not self.config_path.exists():
            return dict(_DEFAULT)
        cfg = _parse(self._read(self.config_path).decode("utf-8"))
        if type(cfg) is not dict or set(cfg) != set(_DEFAULT) or type(cfg["schema"]) is not int or cfg["schema"] != 1 or type(cfg["enabled"]) is not bool:
            raise ValueError("Unsupported local control configuration.")
        if type(cfg["stale_seconds"]) is not int or not 30 <= cfg["stale_seconds"] <= 3600:
            raise ValueError("Control freshness must be 30 to 3600 seconds.")
        if type(cfg["entities"]) is not list or len(cfg["entities"]) > 64 or any(type(e) is not str or not _ENTITY.fullmatch(e) for e in cfg["entities"]) or len(set(cfg["entities"])) != len(cfg["entities"]):
            raise ValueError("Configure up to 64 unique exact entity IDs.")
        if type(cfg["actions"]) is not list or len(cfg["actions"]) > 32:
            raise ValueError("Configure at most 32 fixed control actions.")
        if cfg["read_connector"] is None:
            if cfg["actions"] or cfg["enabled"]:
                raise ValueError("Enabled controls require a fixed read connector.")
            return cfg
        if type(cfg["read_connector"]) is not str or not re.fullmatch(r"connector_[a-z][a-z0-9_]{0,47}", cfg["read_connector"]):
            raise ValueError("Invalid fixed Home Assistant read connector name.")
        connector = self.registry._connectors.get(cfg["read_connector"])
        if not connector or connector["method"] != "GET" or not connector["read_only"]:
            raise ValueError("The configured Home Assistant read connector is unavailable.")
        origin = self._endpoint(connector, "/api/states")
        seen = set()
        for action in cfg["actions"]:
            if type(action) is not dict or set(action) != {"id", "entity_id", "service", "connector", "data"}:
                raise ValueError("Control actions require exact fixed fields.")
            identifier = action["id"]
            if type(identifier) is not str or not _ID.fullmatch(identifier) or identifier in seen:
                raise ValueError("Control action IDs must be unique bounded identifiers.")
            seen.add(identifier)
            entity = action["entity_id"]
            if entity not in cfg["entities"]:
                raise ValueError("Action entity must be explicitly observed and allowed.")
            domain = entity.split(".", 1)[0]
            allowed = {"light": {"turn_on", "turn_off"}, "switch": {"turn_on", "turn_off"}, "scene": {"turn_on"}}
            if type(action["service"]) is not str or domain not in allowed or action["service"] not in {domain + "." + value for value in allowed[domain]}:
                raise ValueError("Only selected light/switch on/off and scene activation actions are supported.")
            data = action["data"]
            if type(data) is not dict or set(data) - ({"brightness_pct", "transition"} if action["service"] == "light.turn_on" else set()):
                raise ValueError("Unsupported fixed Home Assistant action data.")
            for key, upper in (("brightness_pct", 100), ("transition", 10)):
                if key in data and (type(data[key]) is not int or not 0 <= data[key] <= upper):
                    raise ValueError("Invalid fixed light action value.")
            if type(action["connector"]) is not str:
                raise ValueError("Invalid fixed Home Assistant action connector name.")
            target = self.registry._connectors.get(action["connector"])
            if not target or target["method"] != "POST" or target["read_only"]:
                raise ValueError("The configured Home Assistant action connector is unavailable.")
            self._endpoint(target, "/api/services/" + action["service"].replace(".", "/"), origin)
        return cfg

    def schemas(self):
        from .tools import _schema
        return [_schema("home_control", "Selected Home Assistant local controls. catalog/status/propose/receipt read local state; import_snapshot imports a dated local /api/states JSON export; refresh calls only the fixed configured local read endpoint. apply needs owner approval, an exact configured action_id and unique request_id. It never retries an uncertain action, discovers devices or controls unlisted entities.", {
            "action": {"type": "string", "enum": ["catalog", "status", "import_snapshot", "refresh", "propose", "apply", "receipt"]},
            "source_path": {"type": "string", "maxLength": 1024}, "observed_at": {"type": "string", "maxLength": 40},
            "action_id": {"type": "string", "maxLength": 48}, "request_id": {"type": "string", "maxLength": 64}}, ["action"])]

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown local control tool.")
        return "approval" if args.get("action") == "apply" else "auto"

    def status(self):
        try:
            cfg = self.configuration()
            return {"ready": True, "configured": bool(cfg["read_connector"]), "enabled": cfg["enabled"], "offline": True,
                "reason": "Dated local Home Assistant observations and individually approved fixed actions. No devices are discovered or connected automatically."}
        except (ValueError, OSError):
            return {"ready": False, "configured": False, "offline": True, "reason": "Local control configuration requires owner review."}

    def fingerprint_material(self, name, args):
        cfg = self.configuration()
        connectors = {}
        for identifier in ([cfg["read_connector"]] if cfg["read_connector"] else []) + [a["connector"] for a in cfg["actions"]]:
            entry = self.registry._connectors[identifier]
            # Only a digest leaves this method; raw resolved credentials never enter receipts or descriptions.
            resolved = {key: os.environ.get(value["env"], "") if type(value) is dict else value for key, value in entry["headers"].items()}
            connectors[identifier] = {"configuration": entry, "credential_scope_sha256": hashlib.sha256(_json(resolved).encode()).hexdigest()}
        return {"implementation": "fixed-home-controls-1", "configuration": cfg, "connectors": connectors}

    def _check(self, cancelled=None, *, action=False):
        if cancelled and cancelled():
            raise ValueError("Local control operation cancelled before dispatch.")
        if action and self.store is None:
            raise ValueError("Control actions require the application's durable approval store.")
        if self.store is not None and self.store.setting("paused", False):
            raise ValueError("Autonomy is paused; local control dispatch is stopped.")

    @contextmanager
    def _dispatch_lease(self, cancelled):
        # The existing main settings writer uses this same DB lease. A pause
        # committed before dispatch stops it; a later pause waits for this one
        # bounded connector request to finish. No second authority store exists.
        path = _allowed_path(self.store.path)
        with self.registry._directory(path.parent), self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
            if row is not None and _parse(row[0]) is not False:
                raise ValueError("Autonomy is paused; control dispatch was revoked.")
            if cancelled and cancelled():
                raise ValueError("Local control operation cancelled before dispatch.")
            yield

    @staticmethod
    def _digest(cfg):
        return hashlib.sha256(_json(cfg).encode()).hexdigest()

    def _snapshot(self, cfg, rows, observed, origin, digest, now):
        if type(rows) is not list or len(rows) > 1000:
            raise ValueError("A Home Assistant snapshot must be a bounded state list.")
        if observed > now.timestamp() + 5:
            raise ValueError("Control observation time cannot be in the future.")
        selected, seen = {}, set()
        for row in rows:
            if type(row) is not dict or type(row.get("entity_id")) is not str:
                raise ValueError("Invalid Home Assistant state object.")
            entity = row["entity_id"]
            if entity in seen:
                raise ValueError("Home Assistant snapshot has duplicate entity IDs.")
            seen.add(entity)
            if entity not in cfg["entities"]:
                continue
            state = _text(row.get("state"), 240)
            updated = _date(row.get("last_updated"))
            if updated.timestamp() > observed + 300:
                raise ValueError("Entity timestamp is later than the observation.")
            attrs = row.get("attributes", {})
            if type(attrs) is not dict:
                raise ValueError("Invalid Home Assistant attributes.")
            kept = {}
            for key in sorted(_ATTRS & set(attrs)):
                value = attrs[key]
                if isinstance(value, str):
                    kept[key] = _text(value, 240)
                elif value is None or type(value) in {int, float, bool}:
                    if type(value) in {int, float} and (not math.isfinite(value) or abs(value) > 10**12):
                        raise ValueError("Invalid Home Assistant numeric attribute.")
                    kept[key] = value
                else:
                    raise ValueError("Unsupported Home Assistant scalar attribute.")
            selected[entity] = {"entity_id": entity, "state": state, "attributes": kept, "last_updated": updated.isoformat()}
        return {"schema": 1, "configuration_sha256": self._digest(cfg), "observed_at": datetime.fromtimestamp(observed, UTC).isoformat(),
            "origin": origin, "source_sha256": digest, "entities": [selected.get(entity, {"entity_id": entity, "state": "unavailable", "attributes": {}, "last_updated": None}) for entity in cfg["entities"]]}

    def _save_snapshot(self, cfg, rows, observed, origin, digest, now):
        value = self._snapshot(cfg, rows, observed.timestamp(), origin, digest, now)
        encoded = _json(value)
        if len(encoded.encode()) > MAX_BYTES:
            raise ValueError("Selected Home Assistant cache exceeds 64 KiB.")
        with self.database() as db:
            db.execute("INSERT OR REPLACE INTO control_cache VALUES(1,?)", (encoded,))
        return {"ok": True, "snapshot": self._view(cfg, value, now)}

    def _view(self, cfg, value, now):
        if value["configuration_sha256"] != self._digest(cfg):
            return {"status": "configuration_changed", "entities": [], "stale": True, "warning": "Saved observations belong to a different configured scope; refresh or import a new snapshot."}
        observed = _date(value["observed_at"])
        age = max(0, (now-observed).total_seconds())
        rows = []
        for item in value["entities"]:
            updated = _date(item["last_updated"]) if item["last_updated"] else None
            update_age = max(0, (now-updated).total_seconds()) if updated else None
            rows.append({**item, "state_age_seconds": None if update_age is None else round(update_age),
                "unavailable": item["state"] in {"unknown", "unavailable"}, "stale": age > cfg["stale_seconds"] or updated is None or update_age > cfg["stale_seconds"]})
        return {**value, "status": "cached", "age_seconds": round(age), "stale": age > cfg["stale_seconds"], "entities": rows,
            "freshness_basis": "Saved retrieval/import time and per-entity last_updated; no status view performs a network request."}

    def _action(self, cfg, identifier):
        if type(identifier) is not str or not _ID.fullmatch(identifier):
            raise ValueError("Invalid configured control action ID.")
        found = next((a for a in cfg["actions"] if a["id"] == identifier), None)
        if found is None:
            raise ValueError("Control action is not configured.")
        return found

    def _receipt(self, request_id):
        if type(request_id) is not str or not _REQUEST.fullmatch(request_id):
            raise ValueError("A control request ID needs 1–64 letters, digits, underscores or hyphens.")
        with self.database() as db:
            size = db.execute("SELECT length(CAST(result AS BLOB)) FROM control_actions WHERE request_id=?", (request_id,)).fetchone()
            if size and size[0] > MAX_BYTES:
                raise ValueError("Saved control receipt exceeds its bounded size.")
            row = db.execute("SELECT * FROM control_actions WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def _apply(self, cfg, args, approval_fingerprint, cancelled):
        self._check(cancelled, action=True)
        if not cfg["enabled"]:
            raise ValueError("Local control actions are disabled.")
        action = self._action(cfg, args.get("action_id"))
        request_id = args.get("request_id")
        prior = self._receipt(request_id)
        if type(approval_fingerprint) is not str or not re.fullmatch(r"[0-9a-f]{64}", approval_fingerprint) or not hmac.compare_digest(approval_fingerprint, self.registry.fingerprint("home_control", args)):
            raise ValueError("This fixed device action requires a current owner approval.")
        digest = hashlib.sha256(_json({"args": args, "binding": self.fingerprint_material("home_control", args)}).encode()).hexdigest()
        if prior:
            if prior["digest"] != digest:
                raise ValueError("Control request ID belongs to different approved inputs.")
            result = _parse(prior["result"])
            return {**result, "replayed": True}
        started = datetime.now(UTC).isoformat()
        pending = {"ok": False, "uncertain": True, "request_id": request_id, "action_id": action["id"], "status": "uncertain", "started_at": started,
            "error": "Dispatch may have started; inspect Home Assistant and reconcile manually. This request will not be sent again."}
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            raced = db.execute("SELECT * FROM control_actions WHERE request_id=?", (request_id,)).fetchone()
            if raced:
                if raced["digest"] != digest:
                    raise ValueError("Control request ID belongs to different approved inputs.")
                return {**_parse(raced["result"]), "replayed": True}
            if db.execute("SELECT count(*) FROM control_actions").fetchone()[0] >= 10_000:
                raise ValueError("Control receipt history is full; owner review is required.")
            db.execute("INSERT INTO control_actions VALUES(?,?,?,?)", (request_id, digest, "uncertain", _json(pending)))
        dispatch_started = False
        try:
            with self._dispatch_lease(cancelled):
                if not hmac.compare_digest(approval_fingerprint, self.registry.fingerprint("home_control", args)):
                    raise ValueError("Approved control configuration changed before dispatch.")
                # There is deliberately no retry, redirect, fallback or automatic read/POST reconciliation.
                dispatch_started = True
                response = self.registry._request(self.registry._connectors[action["connector"]], {"body": {"entity_id": action["entity_id"], **action["data"]}})
        except Exception:
            response = None
        if not dispatch_started:
            result = {**pending, "uncertain": False, "status": "not_sent", "error": "Control dispatch was cancelled or revoked before sending; the request ID remains consumed."}
        else:
            try:
                if type(response) is dict and response.get("ok") and not response.get("truncated") and type(response.get("data")) is list:
                    result = {"ok": True, "uncertain": False, "request_id": request_id, "action_id": action["id"], "entity_id": action["entity_id"],
                        "service": action["service"], "status": "accepted", "started_at": started, "completed_at": datetime.now(UTC).isoformat(),
                        "response_sha256": hashlib.sha256(_json(response["data"]).encode()).hexdigest(),
                        "warning": "Home Assistant acknowledged this service call. This is not independent physical-device state verification."}
                else:
                    result = pending
            except Exception:
                result = pending
        try:
            with self.database() as db:
                db.execute("UPDATE control_actions SET status=?,result=? WHERE request_id=?", (result["status"], _json(result), request_id))
        except Exception:
            # The already committed uncertain reservation remains authoritative.
            return pending
        return result

    def execute(self, name, args, *, cancelled=None, approval_fingerprint=None):
        from .tools import _object
        if name not in NAMES:
            raise ValueError("Unknown local control tool.")
        _object(args, {"action", "source_path", "observed_at", "action_id", "request_id"}, {"action"})
        action = args["action"]
        fields = {"catalog": set(), "status": set(), "refresh": set(), "import_snapshot": {"source_path", "observed_at"},
            "propose": {"action_id"}, "apply": {"action_id", "request_id"}, "receipt": {"request_id"}}
        if type(action) is not str or action not in fields or set(args) != {"action"} | fields[action]:
            raise ValueError("Missing or unsupported control arguments.")
        cfg, now = self.configuration(), datetime.now(UTC)
        if action == "catalog":
            return {"ok": True, "enabled": cfg["enabled"], "configured": bool(cfg["read_connector"]), "entities": cfg["entities"],
                "actions": [{key: item[key] for key in ("id", "entity_id", "service", "data")} for item in cfg["actions"]],
                "scope": "Owner-configured fixed Home Assistant endpoints; no discovery, broad device access, public Internet, models or shell execution."}
        if action == "status":
            with self.database() as db:
                size = db.execute("SELECT length(CAST(value AS BLOB)) FROM control_cache WHERE id=1").fetchone()
                if size and size[0] > MAX_BYTES:
                    raise ValueError("Saved control cache exceeds its bounded size.")
                row = db.execute("SELECT value FROM control_cache WHERE id=1").fetchone()
            return {"ok": True, "enabled": cfg["enabled"], "snapshot": self._view(cfg, _parse(row[0]), now) if row else {"status": "unavailable", "entities": [], "stale": True, "warning": "No saved local device observations."}}
        if action == "propose":
            selected = self._action(cfg, args["action_id"])
            return {"ok": True, "proposal": {key: selected[key] for key in ("id", "entity_id", "service", "data")}, "enabled": cfg["enabled"],
                "approval_required": True, "performed": False, "configuration_sha256": self._digest(cfg)}
        if action == "receipt":
            row = self._receipt(args["request_id"])
            return {"ok": True, "found": row is not None, "receipt": _parse(row["result"]) if row else None}
        if action == "apply":
            return self._apply(cfg, args, approval_fingerprint, cancelled)
        self._check(cancelled)
        if action == "import_snapshot":
            raw = self._read(self.registry._path(args["source_path"]))
            observed = _date(args["observed_at"])
            self._check(cancelled)
            return self._save_snapshot(cfg, _parse(raw.decode("utf-8")), observed, "owner_imported_export", hashlib.sha256(raw).hexdigest(), now)
        if not cfg["enabled"] or not cfg["read_connector"]:
            raise ValueError("Local control refresh is not enabled/configured.")
        response = self.registry._request(self.registry._connectors[cfg["read_connector"]], {})
        self._check(cancelled)
        if not response.get("ok") or response.get("truncated"):
            return {"ok": False, "error": "Local Home Assistant refresh failed; saved observations retain their original times."}
        if cfg != self.configuration():
            raise ValueError("Local control scope changed; refreshed observations were discarded.")
        return self._save_snapshot(cfg, response.get("data"), datetime.now(UTC), "configured_local_connector", hashlib.sha256(_json(response.get("data")).encode()).hexdigest(), datetime.now(UTC))
