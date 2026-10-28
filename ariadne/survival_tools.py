"""Bounded offline household planning and owner-reported readiness records.

The calculations are inventory estimates, not food/water safety certification,
medical advice, or permission to carry out a physical task. No network or model
inference is needed, and quantities never pass through binary floating math.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from decimal import Decimal, ROUND_DOWN, localcontext

from .config import _allowed_path

NAMES = {"survival_plan", "survival_records"}
CATEGORIES = ("water", "food", "power", "hygiene", "medical", "tools", "seeds", "materials", "communications", "other")
UNITS = ("liter", "kcal", "wh", "count", "kg", "meter")
TASK_STATUSES = ("planned", "in_progress", "blocked", "done")
MAX_SUPPLIES = 500
MAX_TASKS = 500
MAX_LOADS = 100
MAX_REQUESTS = 100_000
CDC_WATER_URL = "https://www.cdc.gov/water-emergency/about/how-to-create-and-store-an-emergency-water-supply.html"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,12})?\Z")
PROFILE_DEFAULTS = {
    "people": 1, "days": 14, "water_liters_per_person_per_day": "3.785411784",
    "food_kcal_per_person_per_day": "2000", "food_basis": "planning-assumption",
    "battery_wh": "0", "usable_fraction": "0.8", "inverter_efficiency": "0.9", "loads": [],
}
_PROFILE_FIELDS = set(PROFILE_DEFAULTS)
ROADMAP = (
    ("water", "Inventory water access, storage and dated safety references", (), "Record container counts, measured volumes, source identities, rotation dates and unresolved quality questions. Acceptance: a traceable inventory and a qualified local review plan for any unverified source; no treatment decision is inferred."),
    ("shelter", "Document shelter options and essential access needs", (), "Map existing shelter locations, occupancy, weather exposure, access needs and evacuation alternatives. Acceptance: an owner-reviewed shelter checklist and a list of structural, fire and utility questions for qualified assessment."),
    ("tools", "Catalog maintainable tools, measurement equipment and skills", (), "Inventory hand tools, measuring instruments, spare parts, manuals, condition and responsible users. Acceptance: a dated list distinguishing inspected equipment from unverified items and identifying training or service needs."),
    ("sanitation", "Plan sanitation continuity and lawful waste handling", ("water",), "Record hygiene supplies, handwashing access, waste categories and applicable local disposal guidance. Acceptance: named responsibilities, supply gaps and documented public-health or utility review questions; no improvised chemical or disposal recipe."),
    ("food", "Build a traceable food stock and preparation inventory", ("water",), "Record labeled quantities, calorie information when available, storage conditions, expiry dates and preparation dependencies. Acceptance: a rotation list and explicit unknowns; a calorie estimate does not establish nutritional adequacy or food safety."),
    ("health", "Organize continuity of care and first-aid resources", ("water", "sanitation"), "Document consented emergency contacts, training, first-aid stock, accessibility needs and continuity-of-care arrangements. Acceptance: a reviewed contact and resource list with qualified clinical questions kept separate; no diagnosis or dosing."),
    ("power", "Measure essential energy demand and backup capacity", ("shelter", "tools"), "Record measured load watts, operating hours, nameplate capacities and dated manufacturer manuals. Acceptance: an explicit energy budget plus surge, ventilation, installation and maintenance questions for qualified review; calculations authorize no equipment operation."),
    ("materials", "Identify reusable materials and compatibility evidence", ("tools",), "Catalog material identity, provenance, dimensions, condition and storage requirements; mark unknown substances and incompatible uses for review. Acceptance: a measured, labeled inventory with appropriate specialist review questions."),
    ("agriculture", "Record a site-specific growing and seed continuity plan", ("water", "food", "tools"), "Record site observations, available area, water access, seed provenance, storage dates, seasonal records and local extension references. Acceptance: a dated observation log, explicit uncertainties and a review plan suited to the actual site."),
    ("navigation", "Prepare dated maps and route observations", ("tools",), "Inventory offline maps, coordinate systems, compass or positioning equipment and known routes with observation dates. Acceptance: an owner-reviewed route record identifying access, terrain and current-condition gaps; mapped routes are not certified safe."),
    ("construction", "Prepare measured repair scopes for qualified review", ("shelter", "materials", "navigation"), "Document existing conditions, measurements, material provenance, site access and intended repair outcomes. Acceptance: a reviewable scope and applicable code, structural and utility questions for qualified professionals; no load-bearing design or construction authorization."),
    ("communications", "Document fallback communications and check-in roles", ("power",), "Inventory communication equipment, power dependencies, lawful operating references and consented contact/check-in arrangements. Acceptance: documented roles, equipment readiness observations and unresolved licensing or coverage questions."),
    ("logistics", "Map supply dependencies, storage and accountable movement", ("food", "communications"), "Record storage locations, transport constraints, stock ownership, replenishment assumptions and accountable handoff records. Acceptance: a traceable local supply plan with measured capacity limits and named decision owners; no purchase or transport is triggered."),
    ("community", "Establish consented roles and shared decision records", ("shelter", "communications"), "Record agreed roles, accessibility needs, consented contacts, resource ownership and a process for resolving conflicting information. Acceptance: an owner/community-reviewed coordination record with clear limits on delegated authority."),
    ("education", "Preserve practical training references and knowledge gaps", ("community",), "Inventory lawful offline references, available instructors, learning prerequisites and evidence of completed training. Acceptance: a prioritized learning plan linking each topic to dated sources and qualified review where needed; reading alone does not certify competence."),
    ("digital", "Verify local data recovery and offline system dependencies", ("power", "education"), "Inventory essential local systems, model assets, reference copies, backup locations and documented restore steps. Acceptance: recorded offline availability and a reviewed recovery exercise using non-destructive test data; no claim of a full backup without verification."),
)


class RevisionConflict(ValueError):
    def __init__(self, current_revision):
        self.current_revision = current_revision
        super().__init__(f"Readiness records changed; reload revision {current_revision} before saving.")


def _fields(value, allowed, required=()):
    if type(value) is not dict or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError("Missing or unsupported readiness fields.")


def _text(value, label, maximum=1000, *, empty=False):
    if type(value) is not str or len(value) > maximum or (not empty and not value.strip()) or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError(f"Invalid {label}.")
    return value


def _identifier(value, label="id"):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError(f"{label} must be 1–64 letters, digits, dots, underscores or hyphens.")
    return value


def _integer(value, label, low=0, high=10**12):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}.")
    return value


def _number(value, label, low="0", high="1000000000000"):
    # Strings are deliberately plain decimal notation. JSON finite numbers are
    # accepted for UI compatibility, converted through their decimal spelling.
    if type(value) not in {str, int, float} or (type(value) is float and not math.isfinite(value)):
        raise ValueError(f"{label} must be a finite decimal quantity.")
    if type(value) is str and (len(value) > 27 or not _DECIMAL.fullmatch(value)):
        raise ValueError(f"{label} needs plain nonnegative decimal notation, at most 12 decimal places.")
    number = Decimal(str(value))
    if not number.is_finite() or number.as_tuple().exponent < -12 or not Decimal(low) <= number <= Decimal(high):
        raise ValueError(f"{label} is outside its permitted range or precision.")
    return _format(number)


def _format(value):
    return "0" if not value else format(value, "f").rstrip("0").rstrip(".") if value.as_tuple().exponent < 0 else str(value)


def _ratio(numerator, denominator):
    if not denominator:
        return None
    return _format((numerator / denominator).quantize(Decimal("0.000001"), rounding=ROUND_DOWN))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _date(value):
    if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("expires must be an ISO date (YYYY-MM-DD) or empty.")
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError("Invalid calendar date.") from None


def _profile_values(values):
    _fields(values, _PROFILE_FIELDS)
    result = {}
    for key, value in values.items():
        if key == "people":
            result[key] = _integer(value, key, 1, 100_000)
        elif key == "days":
            result[key] = _integer(value, key, 1, 36_500)
        elif key == "water_liters_per_person_per_day":
            result[key] = _number(value, key, "0.000000000001", "1000")
        elif key == "food_kcal_per_person_per_day":
            result[key] = None if value is None else _number(value, key, "0.000000000001", "100000")
        elif key in {"usable_fraction", "inverter_efficiency"}:
            result[key] = _number(value, key, "0.000000000001", "1")
        elif key == "battery_wh":
            result[key] = _number(value, key)
        elif key == "food_basis":
            result[key] = _text(value, key, 200)
        elif key == "loads":
            if type(value) is not list or len(value) > MAX_LOADS:
                raise ValueError(f"loads must contain at most {MAX_LOADS} entries.")
            loads = []
            for load in value:
                _fields(load, {"name", "watts", "hours_per_day", "count"}, {"name", "watts", "hours_per_day"})
                loads.append({"name": _text(load["name"], "load name", 120),
                              "watts": _number(load["watts"], "watts", high="1000000000"),
                              "hours_per_day": _number(load["hours_per_day"], "hours_per_day", high="24"),
                              "count": _integer(load.get("count", 1), "count", 1, 1_000_000)})
            result[key] = loads
    return result


def _assumptions(profile):
    water_default = profile["water_liters_per_person_per_day"] == PROFILE_DEFAULTS["water_liters_per_person_per_day"]
    return [
        "Water is a storage-planning quantity for drinking and sanitation; individual needs, pets, climate and activity can require more.",
        "The water baseline is 1 US gallon (3.785411784 liters) per person per day from CDC." if water_default else "Water consumption is an owner-specified assumption that replaces the CDC baseline.",
        "Food calories are an owner-editable planning assumption, not individualized nutrition advice; no nutritional completeness is inferred." if profile["food_kcal_per_person_per_day"] is not None else "Food calorie planning is disabled; no food adequacy is calculated.",
        "Battery watt-hours are nominal stored capacity; usable_fraction reserves capacity and inverter_efficiency accounts for conversion losses.",
        "Load energy equals watts × hours_per_day × count. Startup surge, battery aging, standby draw, charging, generator fuel and solar production are not modeled.",
        "Records and task statuses are owner reports. Recording a task does not authorize equipment operation, construction or other physical actions.",
    ]


def plan(values):
    profile = {**PROFILE_DEFAULTS, "loads": [], **_profile_values(values)}
    with localcontext() as context:
        context.prec = 80
        people, days = Decimal(profile["people"]), Decimal(profile["days"])
        daily_water = people * Decimal(profile["water_liters_per_person_per_day"])
        daily_food = None if profile["food_kcal_per_person_per_day"] is None else people * Decimal(profile["food_kcal_per_person_per_day"])
        loads = []
        daily_wh = Decimal(0)
        for load in profile["loads"]:
            energy = Decimal(load["watts"]) * Decimal(load["hours_per_day"]) * Decimal(load["count"])
            daily_wh += energy
            loads.append({**load, "daily_wh": _format(energy)})
        battery = Decimal(profile["battery_wh"])
        usable = battery * Decimal(profile["usable_fraction"]) * Decimal(profile["inverter_efficiency"])
        warnings = []
        if profile["water_liters_per_person_per_day"] != PROFILE_DEFAULTS["water_liters_per_person_per_day"]:
            warnings.append("Custom water assumption is not a verified adequacy recommendation.")
        if not daily_wh:
            warnings.append("No daily electrical demand is entered; battery runtime and power coverage are unknown.")
        return {"ok": True, "schema": 1, "profile": profile,
                "water": {"daily_liters": _format(daily_water), "total_liters": _format(daily_water * days)},
                "food": {"daily_kcal": None if daily_food is None else _format(daily_food), "total_kcal": None if daily_food is None else _format(daily_food * days), "basis": profile["food_basis"]},
                "power": {"daily_wh": _format(daily_wh), "total_wh": _format(daily_wh * days), "battery_nominal_wh": _format(battery),
                          "battery_usable_wh": _format(usable), "battery_runtime_days": _ratio(usable, daily_wh),
                          "reserve_fraction": _format(Decimal(1) - Decimal(profile["usable_fraction"])),
                          "inverter_efficiency": profile["inverter_efficiency"], "loads": loads},
                "assumptions": _assumptions(profile), "warnings": warnings,
                "sources": [{"id": "cdc-emergency-water", "title": "CDC: How to Create an Emergency Water Supply", "url": CDC_WATER_URL,
                             "scope": "Water storage baseline; the default 14-day planning horizon is owner editable.", "verified_on": "2026-10-28"}]}


class SurvivalTools:
    def __init__(self, registry):
        self.registry = registry
        self.path = registry.settings.data_dir / "survival.sqlite3"
        self.lock = threading.RLock()

    @contextmanager
    def database(self):
        path = _allowed_path(self.path)
        with self.lock, self.registry._directory(path.parent, create=True):
            for suffix in ("", "-journal", "-wal", "-shm"):
                candidate = _allowed_path(str(path) + suffix)
                if candidate.exists():
                    info = candidate.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Readiness database files must be regular files with one link.")
            db = sqlite3.connect(path, timeout=5)
            db.row_factory = sqlite3.Row
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            try:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA trusted_schema=OFF")
                db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES('revision',0);
                CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS supplies(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,data TEXT NOT NULL);
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
    def _revision(db):
        return db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]

    @staticmethod
    def _saved_profile(db):
        row = db.execute("SELECT data FROM profile WHERE id=1").fetchone()
        return {**PROFILE_DEFAULTS, "loads": [], **(json.loads(row[0]) if row else {})}

    def status(self):
        return {"ready": True, "offline": True, "reason": "Deterministic storage/energy planning and owner-reported supplies, profiles and task dependencies; no external services.",
                "limits": {"supplies": MAX_SUPPLIES, "tasks": MAX_TASKS, "loads": MAX_LOADS, "writes": MAX_REQUESTS},
                "safety_scope": "No water treatment dosing, diagnosis, food safety certification or hardware control."}

    def schemas(self):
        from .tools import _schema
        number = {"type": ["string", "number"], "description": "Finite nonnegative decimal; plain strings have at most 12 decimal places."}
        profile = {"people": {"type": "integer", "minimum": 1, "maximum": 100000}, "days": {"type": "integer", "minimum": 1, "maximum": 36500},
                   "water_liters_per_person_per_day": number, "food_kcal_per_person_per_day": {"type": ["string", "number", "null"]},
                   "food_basis": {"type": "string", "maxLength": 200}, "battery_wh": number, "usable_fraction": number, "inverter_efficiency": number,
                   "loads": {"type": "array", "maxItems": MAX_LOADS, "items": {"type": "object", "additionalProperties": False, "required": ["name", "watts", "hours_per_day"],
                             "properties": {"name": {"type": "string", "maxLength": 120}, "watts": number, "hours_per_day": number, "count": {"type": "integer", "minimum": 1, "maximum": 1000000}}}}}
        return [_schema("survival_plan", "Calculate explicit people/days water storage, optional calorie assumptions and battery/load energy with exact decimal arithmetic. Offline estimates only: no treatment dosing, nutritional/safety certification, generator or hardware control. Omitted fields use inspectable defaults; food null disables calorie planning.", profile),
                _schema("survival_records", "Read or review writes to the local readiness profile, supplies and dependency tasks. list is automatic; every write requires approval, a request_id, and can guard expected_revision. Supplies use id,name,category,quantity,unit,expires,location,notes; expiry is YYYY-MM-DD or empty. Task statuses planned/in_progress/blocked/done. Writes are records, not permission to carry out physical actions.", {
                    "action": {"type": "string", "enum": ["list", "save_profile", "upsert_supply", "delete_supply", "upsert_task", "delete_task", "seed_roadmap"]},
                    "record": {"type": "object", "description": "Profile fields match survival_plan. Supply category: " + ",".join(CATEGORIES) + "; unit: " + ",".join(UNITS) + ". Task: id,title,domain,status,depends_on(list of existing task IDs),notes. Deletes require only id. seed_roadmap takes {} and atomically creates 16 original planning tasks with dependencies; existing deterministic IDs cause refusal, never overwrite."},
                    "request_id": {"type": "string", "maxLength": 64}, "expected_revision": {"type": "integer", "minimum": 0}}, ["action"])]

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown survival tool.")
        return "auto" if name == "survival_plan" or args.get("action") == "list" else "approval"

    def fingerprint_material(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown survival tool.")
        with self.database() as db:
            return {"revision": self._revision(db), "implementation": "survival-1"}

    @staticmethod
    def _prepare(action, record):
        if action == "seed_roadmap":
            _fields(record, set())
            return {}
        if action == "save_profile":
            if not record:
                raise ValueError("Profile update must contain at least one field.")
            return _profile_values(record)
        if action in {"delete_supply", "delete_task"}:
            _fields(record, {"id"}, {"id"})
            return {"id": _identifier(record["id"])}
        if action == "upsert_supply":
            _fields(record, {"id", "name", "category", "quantity", "unit", "expires", "location", "notes"}, {"id", "name", "category", "quantity", "unit"})
            if record["category"] not in CATEGORIES or record["unit"] not in UNITS:
                raise ValueError("Unsupported supply category or unit.")
            expires = record.get("expires", "")
            if expires != "":
                _date(expires)
            return {"id": _identifier(record["id"]), "name": _text(record["name"], "supply name", 200),
                    "category": record["category"], "quantity": _number(record["quantity"], "quantity"), "unit": record["unit"],
                    "expires": expires, "location": _text(record.get("location", ""), "location", 200, empty=True), "notes": _text(record.get("notes", ""), "notes", 1000, empty=True)}
        if action == "upsert_task":
            _fields(record, {"id", "title", "domain", "status", "depends_on", "notes"}, {"id", "title"})
            dependencies = record.get("depends_on", [])
            if type(dependencies) is not list or len(dependencies) > 100:
                raise ValueError("depends_on must contain at most 100 task IDs.")
            dependencies = [_identifier(item, "dependency") for item in dependencies]
            if len(set(dependencies)) != len(dependencies):
                raise ValueError("Task dependencies must not repeat.")
            status = record.get("status", "planned")
            if status not in TASK_STATUSES:
                raise ValueError("Unsupported task status.")
            return {"id": _identifier(record["id"]), "title": _text(record["title"], "task title", 200),
                    "domain": _identifier(record.get("domain", "general"), "domain"), "status": status,
                    "depends_on": sorted(dependencies), "notes": _text(record.get("notes", ""), "notes", 1000, empty=True)}
        raise ValueError("Unknown survival records action.")

    @staticmethod
    def _check_tasks(tasks):
        # Iterative Kahn traversal also handles a full 500-task chain without
        # consuming Python recursion depth. Missing/deleted dependencies fail.
        pending = {key: set(task["depends_on"]) for key, task in tasks.items()}
        if any(dependency not in tasks for dependencies in pending.values() for dependency in dependencies):
            raise ValueError("Every dependency must name an existing task.")
        ready = [key for key, dependencies in pending.items() if not dependencies]
        visited = set()
        while ready:
            key = ready.pop()
            if key in visited:
                continue
            visited.add(key)
            for other, dependencies in pending.items():
                if key in dependencies:
                    dependencies.remove(key)
                    if not dependencies:
                        ready.append(other)
        if len(visited) != len(tasks):
            raise ValueError("Task dependencies must not contain a cycle.")

    def execute(self, name, args, *, cancelled=None):
        if name == "survival_plan":
            if cancelled and cancelled():
                raise ValueError("Operation cancelled.")
            return plan(args)
        if name != "survival_records":
            raise ValueError("Unknown survival tool.")
        _fields(args, {"action", "record", "request_id", "expected_revision"}, {"action"})
        action = args["action"]
        if type(action) is not str or action not in {"list", "save_profile", "upsert_supply", "delete_supply", "upsert_task", "delete_task", "seed_roadmap"}:
            raise ValueError("Unknown survival records action.")
        if action == "list":
            _fields(args, {"action"}, {"action"})
            return {"ok": True, **self.overview()}
        key = _identifier(args.get("request_id"), "request_id")
        expected = None if "expected_revision" not in args else _integer(args["expected_revision"], "expected_revision")
        record = self._prepare(action, args.get("record"))
        digest = hashlib.sha256(_canonical({"action": action, "record": record, "expected_revision": expected}).encode()).hexdigest()
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = db.execute("SELECT digest,result FROM requests WHERE id=?", (key,)).fetchone()
            if replay:
                if replay["digest"] != digest:
                    raise ValueError("request_id was already used for different inputs.")
                return {**json.loads(replay["result"]), "replayed": True}
            revision = self._revision(db)
            if expected is not None and expected != revision:
                raise RevisionConflict(revision)
            if db.execute("SELECT count(*) FROM requests").fetchone()[0] >= MAX_REQUESTS:
                raise ValueError("Readiness write history is full; archive reviewed records before adding more.")
            if cancelled and cancelled():
                raise ValueError("Operation cancelled before commit.")
            if action == "save_profile":
                profile = {**self._saved_profile(db), **record}
                db.execute("INSERT INTO profile VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (_canonical(profile),))
            elif action == "seed_roadmap":
                tasks = {row["id"]: json.loads(row["data"]) for row in db.execute("SELECT * FROM tasks")}
                additions = [{"id": "roadmap-" + domain, "title": title, "domain": domain, "status": "planned",
                              "depends_on": ["roadmap-" + dependency for dependency in dependencies], "notes": notes}
                             for domain, title, dependencies, notes in ROADMAP]
                if len(tasks) + len(additions) > MAX_TASKS:
                    raise ValueError("Not enough task capacity for the complete 16-domain roadmap.")
                if any(task["id"] in tasks for task in additions):
                    raise ValueError("A roadmap task ID already exists. Existing tasks are preserved; no roadmap was added.")
                tasks.update((task["id"], task) for task in additions)
                self._check_tasks(tasks)
                db.executemany("INSERT INTO tasks VALUES(?,?)", [(task["id"], _canonical(task)) for task in additions])
            elif action in {"upsert_supply", "upsert_task"}:
                table = "supplies" if action == "upsert_supply" else "tasks"
                maximum = MAX_SUPPLIES if table == "supplies" else MAX_TASKS
                exists = db.execute("SELECT 1 FROM " + table + " WHERE id=?", (record["id"],)).fetchone()
                if not exists and db.execute("SELECT count(*) FROM " + table).fetchone()[0] >= maximum:
                    raise ValueError(f"{table} limit reached ({maximum}).")
                if table == "tasks":
                    tasks = {row["id"]: json.loads(row["data"]) for row in db.execute("SELECT * FROM tasks")}
                    tasks[record["id"]] = record
                    self._check_tasks(tasks)
                db.execute("INSERT INTO " + table + " VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (record["id"], _canonical(record)))
            else:
                table = "supplies" if action == "delete_supply" else "tasks"
                if table == "tasks":
                    tasks = {row["id"]: json.loads(row["data"]) for row in db.execute("SELECT * FROM tasks")}
                    tasks.pop(record["id"], None)
                    self._check_tasks(tasks)
                if not db.execute("DELETE FROM " + table + " WHERE id=?", (record["id"],)).rowcount:
                    raise ValueError("Record does not exist.")
            if cancelled and cancelled():
                raise ValueError("Operation cancelled before commit.")
            db.execute("UPDATE metadata SET value=value+1 WHERE key='revision'")
            result = {"ok": True, "action": action, "request_id": key, "revision": revision + 1, "replayed": False}
            db.execute("INSERT INTO requests VALUES(?,?,?)", (key, digest, _canonical(result)))
            return result

    def overview(self, *, as_of=None):
        today = dt.date.today() if as_of is None else _date(as_of)
        with self.database() as db:
            db.execute("BEGIN")
            revision = self._revision(db)
            profile = self._saved_profile(db)
            supplies = [json.loads(row[0]) for row in db.execute("SELECT data FROM supplies ORDER BY id")]
            tasks = [json.loads(row[0]) for row in db.execute("SELECT data FROM tasks ORDER BY id")]
        calculated = plan(profile)
        task_states = {task["id"]: task["status"] for task in tasks}
        for task in tasks:
            task["blocked_by"] = [item for item in task["depends_on"] if task_states[item] != "done"]
            task["ready"] = task["status"] not in {"done", "blocked"} and not task["blocked_by"]
        expired, unknown, unquantified = [], [], []
        available = {"water": Decimal(0), "food": Decimal(0), "power": Decimal(0)}
        for supply in supplies:
            expiry = supply["expires"]
            supply["expiry_status"] = "unknown" if not expiry else "expired" if _date(expiry) < today else "current"
            supply["usable"] = supply["expiry_status"] != "expired"
            supply["safety_verified"] = False
            if supply["expiry_status"] == "expired":
                expired.append(supply["id"])
            if supply["expiry_status"] == "unknown":
                unknown.append(supply["id"])
            category = supply["category"]
            if category in available:
                if supply["unit"] != {"water": "liter", "food": "kcal", "power": "wh"}[category]:
                    unquantified.append(supply["id"])
                elif supply["usable"]:
                    available[category] += Decimal(supply["quantity"])
        with localcontext() as context:
            context.prec = 80
            available["power"] = (available["power"] + Decimal(profile["battery_wh"])) * Decimal(profile["usable_fraction"]) * Decimal(profile["inverter_efficiency"])
            summary = {}
            for category, unit, daily_key, total_key in (("water", "liters", "daily_liters", "total_liters"), ("food", "kcal", "daily_kcal", "total_kcal"), ("power", "wh", "daily_wh", "total_wh")):
                daily = calculated[category][daily_key]
                required = calculated[category][total_key]
                summary[category] = {"available_" + unit: _format(available[category]), "required_" + unit: required,
                                     "shortfall_" + unit: None if required is None else _format(max(Decimal(0), Decimal(required) - available[category])),
                                     "coverage_days": None if daily is None else _ratio(available[category], Decimal(daily))}
        warnings = list(calculated["warnings"])
        warnings.append("Supply amounts and dates are unverified owner reports. A listed expiry date or calculated coverage never certifies food, water, fuel or medical supplies safe.")
        if unknown:
            warnings.append("Supplies with unknown expiry are counted as reported stock; inspect condition, suitability and rotation separately.")
        if expired:
            warnings.append("Expired supplies are excluded from available coverage; no disposal or safety decision is inferred.")
        if unquantified:
            warnings.append("Some water/food/power supplies use other units and cannot contribute to dimensional coverage without explicit conversion data.")
        if Decimal(profile["battery_wh"]) and any(item["category"] == "power" and item["unit"] == "wh" and item["usable"] for item in supplies):
            warnings.append("Power totals assume profile battery_wh and inventory wh are distinct capacities; avoid entering the same battery twice.")
        summary.update({"as_of": today.isoformat(), "plan": calculated, "expired_supply_ids": expired, "unknown_expiry_supply_ids": unknown,
                        "unquantified_supply_ids": unquantified, "open_tasks": sum(task["status"] != "done" for task in tasks),
                        "completed_tasks": sum(task["status"] == "done" for task in tasks),
                        "blocked_tasks": sum(task["status"] != "done" and (task["status"] == "blocked" or bool(task["blocked_by"])) for task in tasks),
                        "assumptions": calculated["assumptions"], "warnings": warnings})
        return {"schema": 1, "revision": revision, "profile": calculated["profile"], "supplies": supplies, "tasks": tasks, "summary": summary}
