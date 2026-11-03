"""Opt-in, evidence-linked projects over the existing mission and routine workers.

This module owns no clock, thread, model client, tool authority or scheduler.
Its durable dispatch link and child queue insertion share the main transaction.
Reads never create tables and recovery never replays or adopts uncertain work.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import uuid

from .agents import AgentRegistry, resolve_agent
from .handoff import HandoffDesk, HandoffConflict, _bytes, _digest, _fields, _identifier, _json, _load, _text
from .verification import validate_checks, verify

MAX_PROJECTS = 32
MAX_TASKS = 24
MAX_DEPENDENCIES = 6
MAX_CHILDREN = 3
MAX_STEPS = 8
MAX_CYCLES = 1024
MAX_REQUESTS = 4096
MAX_STATE_BYTES = 16 * 1024 * 1024
MAX_STORAGE_BYTES = 64 * 1024 * 1024
MAX_PROJECT_BYTES = 256 * 1024
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_ASSIGNMENT_BYTES = 8192
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_CYCLE_STATES = {"active", "completed", "held", "owner_stopped", "budget_exhausted"}
_TASK_STATES = {"pending", "awaiting_review", "active", "completed", "held"}
_SCOPE = "Bounded local project execution; owner attestations and checked files do not prove sales, profit, customer consent or physical actions. Existing tool approvals and global pause remain authoritative."

WorkroomConflict = HandoffConflict


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        raise ValueError("A lowercase SHA-256 fingerprint is required.")
    return value


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}.")
    return value


def _manual(identifier, title, acceptance, depends=()):
    return {"id": identifier, "title": title, "kind": "manual", "depends_on": list(depends), "acceptance": acceptance}


def _procedure(identifier, title, procedure_id, acceptance, depends=()):
    return {**_manual(identifier, title, acceptance, depends), "kind": "procedure", "procedure_id": procedure_id}


def _templates():
    rows = [
        {"id": "offline-continuity", "name": "Offline continuity", "description": "Three fixed, zero-model reports followed by an owner review. No internet or hardware activation.",
         "objective": "Establish what this local installation can support during an outage, with dated artifact evidence.",
         "acceptance": "Inventory, reference audit and offline checklist exist with matching hashes; the owner records remaining gaps.",
         "tasks": [_procedure("inventory", "Inventory local knowledge", "knowledge_inventory", "A complete fixed knowledge inventory receipt and its declared report files exist."),
                   _procedure("references", "Audit local references", "reference_audit", "The fixed reference audit completed with checked output files.", ["inventory"]),
                   _procedure("continuity", "Inspect offline readiness", "offline_checklist", "A fixed offline checklist records observed readiness and unknowns.", ["references"]),
                   _manual("decision", "Review outage gaps", "Record the reviewed gaps and one practical next step; a checklist does not certify physical readiness.", ["continuity"])]},
        {"id": "service-pilot", "name": "Service desk pilot", "description": "Draft a customer handoff pilot without sending messages, creating customers or posting a ledger.",
         "objective": "Validate a narrow quote-to-invoice service using owner-supplied evidence and a reviewed local operating plan.",
         "acceptance": "The owner records a target niche, an example process, observed costs and a go/no-go decision; no revenue is inferred.",
         "tasks": [_manual("scope", "Choose one pilot problem", "Record the niche, actual problem evidence, draft offer and what remains unvalidated."),
                   _procedure("baseline", "Record the local business baseline", "business_snapshot", "A checked business snapshot distinguishes saved records from unknown customer demand.", ["scope"]),
                   _manual("handoff", "Review an example intake-to-invoice handoff", "Link a fictional or authorized local example, approved rate basis and exception handling; no external sending.", ["baseline"]),
                   _manual("decision", "Decide the next pilot experiment", "Record delivery effort, cost assumptions and an observable validation experiment, without claiming a sale.", ["handoff"])]},
        {"id": "pod-validation", "name": "Print-on-demand validation", "description": "Evaluate a niche and unit economics before paying for a store, advertisements or inventory.",
         "objective": "Determine whether a narrow print-on-demand offer merits a small, explicitly authorized validation experiment.",
         "acceptance": "Owner-reviewed niche evidence, supplier terms, all-in unit economics and a go/no-go decision are recorded.",
         "tasks": [_manual("niche", "Document a niche and original offer", "Record source evidence for the audience and originality/IP questions; popularity is not purchase intent."),
                   _manual("economics", "Calculate all-in unit economics", "Include dated garment, print, delivery, platform, payment, return and tax assumptions; unknown costs stay unknown.", ["niche"]),
                   _manual("decision", "Review a zero-spend validation plan", "Record a falsifiable demand test and acceptance threshold; do not open accounts, spend money or contact anyone.", ["economics"])]},
        {"id": "research-dossier", "name": "Evidence research dossier", "description": "Organize local research and explicit review gates; does not launch studies or promote trading candidates.",
         "objective": "Produce a source-linked dossier with reproducible methods, limitations and a clear owner decision.",
         "acceptance": "Source coverage, methodology, contrary evidence and missing data are documented and reviewed.",
         "tasks": [_procedure("inventory", "Inventory imported evidence", "knowledge_inventory", "Checked local metadata inventory outputs exist; missing sources remain missing."),
                   _manual("method", "Review the research question and method", "Record an observable question, dates, dataset hashes and out-of-sample controls where relevant.", ["inventory"]),
                   _manual("decision", "Review findings and limitations", "Link actual results and contrary evidence, distinguish hypotheses from validated outcomes, and record a next experiment.", ["method"])]},
    ]
    return [{"id": row["id"], "name": row["name"], "description": row["description"],
             "project": {key: copy.deepcopy(row[key]) for key in ("id", "name", "objective", "acceptance", "tasks")}} for row in rows]


class Workroom(HandoffDesk):
    def __init__(self, registry, store, routines):
        super().__init__(registry, store)
        self.routines = routines
        self._agent_catalog = None

    def bind_agent_catalog(self, reader):
        if not callable(reader):
            raise ValueError("Workroom agent catalog must be a trusted reader.")
        self._agent_catalog = reader

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workroom_state'").fetchone() is not None

    @staticmethod
    def _initialize(db):
        db.execute("CREATE TABLE IF NOT EXISTS workroom_state(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL,sha256 TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS workroom_requests(request_id TEXT PRIMARY KEY,action TEXT NOT NULL,fingerprint TEXT NOT NULL,value TEXT NOT NULL,sha256 TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS workroom_children(id TEXT PRIMARY KEY,kind TEXT NOT NULL,project_id TEXT NOT NULL,cycle_id TEXT NOT NULL,task_id TEXT NOT NULL,value TEXT NOT NULL,sha256 TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS workroom_versions(project_id TEXT NOT NULL,revision INTEGER NOT NULL,value TEXT NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(project_id,revision))")
        state = {"schema": 1, "revision": 0, "projects": {}}
        db.execute("INSERT OR IGNORE INTO workroom_state VALUES(1,?,?)", (_json(state), _digest(state)))

    @staticmethod
    def _read_json(row, maximum=MAX_STATE_BYTES):
        if row is None:
            raise KeyError("Workroom record not found.")
        raw = row["value"]
        if type(raw) is not str or len(raw.encode("utf-8")) > maximum:
            raise ValueError("Workroom record exceeds its storage bound.")
        value = _load(raw)
        if _digest(value) != row["sha256"]:
            raise ValueError("Workroom record failed its integrity check; owner review is required.")
        return value

    def _state(self, db):
        if not self._exists(db):
            return {"schema": 1, "revision": 0, "projects": {}}
        row = db.execute("SELECT substr(value,1,?) value,sha256 FROM workroom_state WHERE id=1", (MAX_STATE_BYTES+1,)).fetchone()
        state = self._read_json(row)
        _fields(state, {"schema", "revision", "projects"}, {"schema", "revision", "projects"})
        if state["schema"] != 1 or type(state["revision"]) is not int or state["revision"] < 0 or type(state["projects"]) is not dict or len(state["projects"]) > MAX_PROJECTS:
            raise ValueError("Workroom state is inconsistent.")
        cycles = 0
        for identifier, project in state["projects"].items():
            _identifier(identifier)
            definition = project["definition"]
            if identifier != definition["id"] or _digest(definition) != project["content_sha256"] or len(_bytes(definition)) > MAX_PROJECT_BYTES:
                raise ValueError("Workroom project definition is inconsistent.")
            self._validate_shape(definition, stored=True)
            if set(project["runtime"]) != {task["id"] for task in definition["tasks"]}:
                raise ValueError("Workroom task runtime is inconsistent.")
            for runtime in project["runtime"].values():
                if runtime["status"] not in _TASK_STATES or type(runtime["evidence"]) is not dict:
                    raise ValueError("Workroom task state is inconsistent.")
            if type(project["cycles"]) is not list or any(cycle["status"] not in _CYCLE_STATES for cycle in project["cycles"]):
                raise ValueError("Workroom cycle history is inconsistent.")
            cycles += len(project["cycles"])
        if cycles > MAX_CYCLES:
            raise ValueError("Workroom cycle history exceeds its bound.")
        return state

    @staticmethod
    def _write(db, state):
        state["revision"] += 1
        raw = _json(state)
        if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError("Workroom storage is full; inspect retained project evidence.")
        db.execute("UPDATE workroom_state SET value=?,sha256=? WHERE id=1", (raw, _digest(state)))
        Workroom._storage_limit(db)

    @staticmethod
    def _storage_limit(db):
        used = sum(db.execute("SELECT coalesce(sum(length(cast(value AS BLOB))),0) FROM "+table).fetchone()[0]
                   for table in ("workroom_state", "workroom_requests", "workroom_children", "workroom_versions"))
        if used > MAX_STORAGE_BYTES:
            raise ValueError("The 64 MiB Workroom metadata/history budget is full; inspect retained records. Nothing is deleted automatically.")

    @staticmethod
    def _paused(db):
        row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return bool(_load(row[0])) if row else False

    @classmethod
    def _unpaused(cls, db):
        if cls._paused(db):
            raise HandoffConflict("Global autonomy is paused; Workroom changes are held.")

    def _old(self, db, body, action):
        row = db.execute("SELECT * FROM workroom_requests WHERE request_id=?", (body["request_id"],)).fetchone()
        if row:
            if row["action"] != action or row["fingerprint"] != _digest(body):
                raise HandoffConflict("This Workroom request ID belongs to different arguments.")
            return {**self._read_json(row, MAX_PROJECT_BYTES), "replayed": True}
        if db.execute("SELECT count(*) FROM workroom_requests").fetchone()[0] >= MAX_REQUESTS:
            raise ValueError("Workroom request history is full; retained receipts require review.")
        return None

    @staticmethod
    def _require_project(state, identifier):
        _identifier(identifier)
        if identifier not in state["projects"]:
            raise KeyError("Workroom project not found.")
        return state["projects"][identifier]

    @staticmethod
    def _cycle(project, identifier=None):
        if identifier is None:
            latest = project["cycles"][-1] if project["cycles"] else None
            return latest if latest and latest["definition_revision"] == project["definition_revision"] and latest["project_sha256"] == project["content_sha256"] else None
        _identifier(identifier)
        for cycle in project["cycles"]:
            if cycle["id"] == identifier:
                return cycle
        raise KeyError("Workroom cycle not found.")

    def _mutate(self, action, body, change, *, paused_ok=False):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._initialize(db)
            old = self._old(db, body, action)
            if old:
                return old
            state = self._state(db)
            self._revision(state, body["expected_revision"])
            if not paused_ok:
                self._unpaused(db)
            details = change(db, state)
            self._write(db, state)
            receipt = {"schema": 1, "request_id": body["request_id"], "status": "completed", "action": action,
                       "revision": state["revision"], "completed_at": _now(), **details}
            db.execute("INSERT INTO workroom_requests VALUES(?,?,?,?,?)", (body["request_id"], action, _digest(body), _json(receipt), _digest(receipt)))
            self._storage_limit(db)
            result = {**receipt}
            if details.get("project_id"):
                result["project"] = self._view(db, state["projects"][details["project_id"]])
            return result

    @staticmethod
    def _validate_shape(value, *, stored=False):
        _fields(value, {"id", "name", "objective", "acceptance", "tasks"}, {"id", "name", "objective", "acceptance", "tasks"})
        _identifier(value["id"])
        _text(value["name"], 200); _text(value["objective"], 4000); _text(value["acceptance"], 1000)
        if type(value["tasks"]) is not list or not 1 <= len(value["tasks"]) <= MAX_TASKS:
            raise ValueError("A Workroom project requires 1–24 tasks.")
        identifiers = set()
        for task in value["tasks"]:
            extra = {"execution_preview"} if stored else set()
            _fields(task, {"id", "title", "kind", "depends_on", "acceptance", "procedure_id", "workflow_id", "required_source_refs"} | extra,
                    {"id", "title", "kind", "depends_on", "acceptance"} | extra)
            _identifier(task["id"]); _text(task["title"], 200); _text(task["acceptance"], 1000)
            if task["id"] in identifiers:
                raise ValueError("Workroom task IDs must be unique.")
            identifiers.add(task["id"])
            if type(task["kind"]) is not str or task["kind"] not in {"manual", "procedure", "workflow"}:
                raise ValueError("Workroom tasks are manual, procedure or workflow tasks.")
            if "required_source_refs" in task:
                if task["kind"] != "manual":
                    raise ValueError("Required source references belong only to manual review gates.")
                Workroom._required_refs(task)
            target = {"manual": set(), "procedure": {"procedure_id"}, "workflow": {"workflow_id"}}[task["kind"]]
            if {"procedure_id", "workflow_id"}.intersection(task) != target:
                raise ValueError("Each Workroom task must contain only its declared execution target.")
            for field in target:
                _identifier(task[field])
            if type(task["depends_on"]) is not list or len(task["depends_on"]) > MAX_DEPENDENCIES:
                raise ValueError("A task supports at most six unique direct dependencies.")
            for identifier in task["depends_on"]:
                _identifier(identifier)
            if len(set(task["depends_on"])) != len(task["depends_on"]):
                raise ValueError("Task dependencies must be unique.")
        graph = {task["id"]: task["depends_on"] for task in value["tasks"]}
        visiting, visited = set(), set()
        def walk(identifier):
            if identifier in visiting:
                raise ValueError("Workroom dependencies must be acyclic.")
            if identifier not in identifiers:
                raise ValueError("Workroom dependencies must name tasks in this project.")
            if identifier in visited:
                return
            visiting.add(identifier)
            for dependency in graph[identifier]:
                walk(dependency)
            visiting.remove(identifier); visited.add(identifier)
        for identifier in identifiers:
            walk(identifier)

    def _workflow_preview(self, db, identifier):
        row = db.execute("SELECT * FROM workflows WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Choose an existing saved workflow.")
        source = dict(row)
        checks = json.loads(source["checks"])
        if source["isolated_outputs"] != 1 or not checks:
            raise ValueError("Workroom workflow tasks require isolated outputs and nonempty acceptance checks.")
        from .store import _isolated_outputs
        _isolated_outputs(source["prompt"], checks, True)
        checks = validate_checks(checks, self.registry)
        agent_id = resolve_agent(source["prompt"], source.get("agent_id"))
        selected = self._resolved_agent(agent_id)
        workflow = {"id": source["id"], "name": source["name"], "prompt": source["prompt"], "checks": checks,
                    "agent_id": agent_id, "isolated_outputs": True}
        return {"workflow": workflow, "workflow_sha256": _digest(workflow), "resolved_agent": selected,
                "catalog_sha256": _digest(selected)}

    def _resolved_agent(self, agent_id):
        """One trusted profile projection shared by project and kit capture."""
        if self._agent_catalog is None:
            raise ValueError("The trusted agent catalog must be bound before admitting workflow tasks.")
        catalog = self._agent_catalog()
        agents = catalog.get("agents") if type(catalog) is dict else None
        if type(agents) is not list:
            raise ValueError("The trusted agent catalog is unavailable.")
        found = [item for item in agents if item.get("id") == agent_id]
        if len(found) != 1:
            raise ValueError("The saved workflow agent is unavailable.")
        entry = found[0]
        profile = AgentRegistry.profile(agent_id)
        cap = _integer(entry.get("max_steps"), 1, 100, "Agent AI-step limit")
        selected = {key: copy.deepcopy(entry[key]) for key in ("id", "name", "profile_version", "tools", "connectivity", "approval_policy")}
        selected["max_steps"] = cap
        selected["instruction"] = profile.instruction
        return selected

    @staticmethod
    def _required_refs(task):
        refs = task.get("required_source_refs", [])
        if type(refs) is not list or len(refs) > 10:
            raise ValueError("A manual gate supports at most ten required source references.")
        prepared, seen = [], set()
        for ref in refs:
            _fields(ref, {"path", "sha256", "bytes"}, {"path", "sha256", "bytes"})
            path = _text(ref["path"], 300).replace("\\", "/")
            _sha(ref["sha256"]); _integer(ref["bytes"], 0, MAX_REFERENCE_BYTES, "Required source bytes")
            if path in seen:
                raise ValueError("Required source references must be unique.")
            seen.add(path)
            prepared.append({"path": path, "sha256": ref["sha256"], "bytes": ref["bytes"]})
        return prepared

    @classmethod
    def _require_sources(cls, task, refs):
        actual = {(ref["path"].replace("\\", "/"), ref["sha256"], ref["bytes"]) for ref in refs}
        needed = {(ref["path"], ref["sha256"], ref["bytes"]) for ref in cls._required_refs(task)}
        if not needed.issubset(actual):
            raise ValueError("This manual gate requires every exact prepared source reference; they cannot be omitted or replaced.")

    def _procedures(self):
        return {row["id"]: row for row in self.routines.procedures.catalog()["procedures"]}

    def _preview(self, db, task):
        if task["kind"] == "manual":
            return None
        if task["kind"] == "workflow":
            return self._workflow_preview(db, task["workflow_id"])
        procedure = self._procedures().get(task["procedure_id"])
        if procedure is None:
            raise ValueError("Choose an existing fixed local procedure.")
        return {"procedure_id": procedure["id"], "name": procedure["name"], "outputs": procedure["outputs"], "model_calls": 0}

    def catalog(self):
        with self.database() as db:
            workflows = []
            for row in db.execute("SELECT id,name,agent_id,isolated_outputs,checks FROM workflows ORDER BY name,id LIMIT 101"):
                item = {"id": row["id"], "name": row["name"], "agent_id": row["agent_id"], "isolated_outputs": bool(row["isolated_outputs"]),
                        "check_count": len(json.loads(row["checks"])), "content_sha256": None, "eligible": False, "reason": None}
                try:
                    preview = self._workflow_preview(db, row["id"])
                    item.update(eligible=True, content_sha256=preview["workflow_sha256"])
                except (ValueError, KeyError, TypeError) as error:
                    item["reason"] = str(error)[:300]
                workflows.append(item)
        return {"schema": 1, "templates": _templates(), "procedures": list(self._procedures().values()), "workflows": workflows,
                "limits": {"projects": MAX_PROJECTS, "tasks": MAX_TASKS, "dependencies": MAX_DEPENDENCIES, "children_per_cycle": MAX_CHILDREN,
                           "steps_per_child": MAX_STEPS, "assignment_bytes": MAX_ASSIGNMENT_BYTES, "assignment_artifacts": 20,
                           "evidence_bytes": MAX_EVIDENCE_BYTES, "metadata_history_bytes": MAX_STORAGE_BYTES, "requests": MAX_REQUESTS, "cycles": MAX_CYCLES},
                "scope": _SCOPE, "recurrence": "None. Starting another cycle requires an explicit owner action.",
                "stop": "Stop after current blocks additional dispatch. An already queued child remains governed by its existing worker and approvals."}

    def save_project(self, body):
        self._arguments(body, {"project"}, {"project"})
        self._validate_shape(body["project"])
        return self._mutate("save_project", body, lambda db, state: self._save_project(db, state, body["project"]))

    def _save_project(self, db, state, definition):
        """Capture a definition inside the caller's existing main transaction."""
        self._validate_shape(definition)
        definition = copy.deepcopy(definition)
        for key in ("name", "objective", "acceptance"):
            definition[key] = definition[key].strip()
        for task in definition["tasks"]:
            task["title"] = task["title"].strip(); task["acceptance"] = task["acceptance"].strip()
            if "required_source_refs" in task:
                task["required_source_refs"] = self._check_refs(self._required_refs(task))
            task["execution_preview"] = self._preview(db, task)
        if len(_bytes(definition)) > MAX_PROJECT_BYTES:
            raise ValueError("The captured Workroom project exceeds its 256 KiB limit.")
        old = state["projects"].get(definition["id"])
        if old:
            cycle = self._cycle(old)
            if cycle and (cycle["status"] == "active" or cycle.get("current_task_id")):
                raise HandoffConflict("An active or unresolved project child locks its definition; inspect it before making a new project.")
            if _digest(definition) == old["content_sha256"]:
                raise HandoffConflict("This project definition is unchanged; its task evidence will not be reset.")
            previous_tasks = {task["id"]: task for task in old["definition"]["tasks"]}
            for task in definition["tasks"]:
                previous = previous_tasks.get(task["id"])
                if previous and old["runtime"][task["id"]]["status"] == "held":
                    identity = lambda value: {key: value.get(key) for key in ("kind", "procedure_id", "workflow_id", "execution_preview")}
                    same_execution = identity(task) == identity(previous)
                    if same_execution and (task["kind"] != "manual" or task["acceptance"] == previous["acceptance"]):
                        raise HandoffConflict("A held task cannot replay the same execution under its existing task ID; inspect it and use a new task or changed execution.")
            if db.execute("SELECT count(*) FROM workroom_versions").fetchone()[0] >= MAX_CYCLES:
                raise ValueError("Workroom definition history is full.")
            db.execute("INSERT INTO workroom_versions VALUES(?,?,?,?)", (definition["id"], old["definition_revision"], _json(old), _digest(old)))
        elif len(state["projects"]) >= MAX_PROJECTS:
            raise ValueError("Workroom supports at most 32 projects.")
        project = {"definition": definition, "definition_revision": old["definition_revision"]+1 if old else 1,
                   "content_sha256": _digest(definition), "runtime": {task["id"]: self._empty_runtime() for task in definition["tasks"]},
                   "cycles": old["cycles"] if old else [], "created_at": old["created_at"] if old else _now(), "updated_at": _now()}
        state["projects"][definition["id"]] = project
        return {"project_id": definition["id"], "content_sha256": project["content_sha256"]}

    @staticmethod
    def _empty_runtime():
        return {"status": "pending", "child": None, "evidence": {"assurance": None, "artifacts": []}, "review": None}

    def _drift(self, db, task):
        try:
            return self._preview(db, task) != task["execution_preview"]
        except (ValueError, KeyError, TypeError):
            return True

    @staticmethod
    def _ready(project, task):
        return all(project["runtime"][identifier]["status"] == "completed" for identifier in task["depends_on"])

    @staticmethod
    def _task_digest(task, runtime):
        return _digest({"task": task, "runtime": runtime})

    def _child_row(self, db, identifier):
        row = db.execute("SELECT * FROM workroom_children WHERE id=?", (identifier,)).fetchone()
        child = self._read_json(row, 64 * 1024)
        if child["id"] != identifier or child["kind"] != row["kind"] or any(child[key] != row[key] for key in ("project_id", "cycle_id", "task_id")):
            raise ValueError("Workroom child linkage is inconsistent.")
        return child

    def _child_status(self, db, child):
        if child["kind"] == "routine":
            row = db.execute("SELECT * FROM routine_runs WHERE id=?", (child["id"],)).fetchone()
            if row is None:
                return {"kind": "routine", "id": child["id"], "status": "missing"}
            return {"kind": "routine", "id": child["id"], "status": row["status"], "model_calls": 0}
        row = db.execute("SELECT status,verification_status FROM runs WHERE id=?", (child["id"],)).fetchone()
        unresolved = db.execute("SELECT count(*) FROM approvals WHERE run_id=? AND status NOT IN ('executed','rejected','cancelled')", (child["id"],)).fetchone()[0]
        return {"kind": "mission", "id": child["id"], "status": row["status"] if row else "missing",
                "verification_status": row["verification_status"] if row else "missing", "approval_count": unresolved,
                "max_steps": child["step_limit"]}

    def _view(self, db, project):
        definition = copy.deepcopy(project["definition"])
        counts = dict.fromkeys(("total", "completed", "ready", "blocked", "active", "held", "awaiting_review"), 0)
        aggregate_drift = False
        for task in definition["tasks"]:
            runtime = project["runtime"][task["id"]]
            state = runtime["status"]
            if state == "pending":
                state = "ready" if self._ready(project, task) else "blocked"
            child = self._child_status(db, self._child_row(db, runtime["child"])) if runtime["child"] else None
            drift = self._drift(db, task)
            aggregate_drift |= drift
            task.update(status=state, child=child, evidence=copy.deepcopy(runtime["evidence"]), review=copy.deepcopy(runtime["review"]),
                        content_sha256=self._task_digest(task, runtime), source_drift=drift)
            counts["total"] += 1; counts[state] += 1
        return {**definition, "definition_revision": project["definition_revision"], "content_sha256": project["content_sha256"],
                "task_counts": counts, "source_drift": aggregate_drift, "cycle": copy.deepcopy(self._cycle(project)),
                "cycles": copy.deepcopy(project["cycles"][-20:]), "created_at": project["created_at"], "updated_at": project["updated_at"]}

    def status(self):
        with self.database() as db:
            initialized = self._exists(db)
            state = self._state(db)
            projects = []
            for project in state["projects"].values():
                view = self._view(db, project)
                projects.append({key: view[key] for key in ("id", "name", "objective", "definition_revision", "content_sha256", "task_counts", "cycle", "source_drift")})
            return {"schema": 1, "revision": state["revision"], "initialized": initialized, "projects": projects,
                    "active_cycle_count": sum(bool(self._cycle(project)) and self._cycle(project)["status"] == "active" for project in state["projects"].values()),
                    "scope": _SCOPE, "idle_model_calls": 0}

    def project(self, identifier):
        with self.database() as db:
            state = self._state(db)
            return {"schema": 1, "revision": state["revision"], "project": self._view(db, self._require_project(state, identifier)), "scope": _SCOPE}

    def receipt(self, identifier):
        _identifier(identifier)
        with self.database() as db:
            if not self._exists(db):
                raise KeyError("Workroom request receipt not found.")
            return self._read_json(db.execute("SELECT value,sha256 FROM workroom_requests WHERE request_id=?", (identifier,)).fetchone(), MAX_PROJECT_BYTES)

    def start_cycle(self, body):
        self._arguments(body, {"project_id", "content_sha256", "max_children", "max_steps_per_child"}, {"project_id", "content_sha256", "max_children"})
        _sha(body["content_sha256"]); _integer(body["max_children"], 1, MAX_CHILDREN, "Child invocation budget")
        if "max_steps_per_child" in body:
            _integer(body["max_steps_per_child"], 1, MAX_STEPS, "Per-child AI-step cap")
        def change(db, state):
            project = self._require_project(state, body["project_id"])
            if project["content_sha256"] != body["content_sha256"]:
                raise HandoffConflict("The project changed; review the exact frozen definition before starting.")
            previous = self._cycle(project)
            if previous and (previous["status"] in {"active", "held"} or previous.get("current_task_id")):
                raise HandoffConflict("The prior cycle is active or held; inspect its original evidence rather than replaying it.")
            if any(runtime["status"] == "held" for runtime in project["runtime"].values()):
                raise HandoffConflict("A failed or rejected task holds this definition; revise it after inspection.")
            if all(runtime["status"] == "completed" for runtime in project["runtime"].values()):
                raise HandoffConflict("This project is already complete.")
            if sum(len(item["cycles"]) for item in state["projects"].values()) >= MAX_CYCLES:
                raise ValueError("Workroom cycle history is full.")
            for task in project["definition"]["tasks"]:
                if self._drift(db, task):
                    raise HandoffConflict("An execution source changed; explicitly save and review a revised project before starting.")
            cap = body.get("max_steps_per_child", 6)
            agent_caps = [task["execution_preview"]["resolved_agent"]["max_steps"] for task in project["definition"]["tasks"] if task["kind"] == "workflow"]
            if agent_caps and "max_steps_per_child" not in body:
                cap = min(cap, min(agent_caps))
            cycle = {"id": "cycle-"+uuid.uuid4().hex, "status": "active", "project_sha256": project["content_sha256"],
                     "definition_revision": project["definition_revision"], "max_children": body["max_children"],
                     "max_steps_per_child": cap, "worst_case_model_steps": body["max_children"]*cap,
                     "children_dispatched": 0, "current_task_id": None, "reason": None, "created_at": _now(), "updated_at": _now()}
            project["cycles"].append(cycle); project["updated_at"] = _now()
            return {"project_id": body["project_id"], "cycle_id": cycle["id"], "max_children": cycle["max_children"],
                    "max_steps_per_child": cap, "worst_case_model_steps": cycle["worst_case_model_steps"]}
        return self._mutate("start_cycle", body, change)

    def stop_cycle(self, body):
        self._arguments(body, {"project_id", "cycle_id"}, {"project_id", "cycle_id"})
        def change(db, state):
            project = self._require_project(state, body["project_id"]); cycle = self._cycle(project, body["cycle_id"])
            if cycle["status"] != "active":
                raise HandoffConflict("Only an active cycle can be stopped after its current child.")
            self._finish(cycle, "owner_stopped", "Owner stopped additional dispatch. Any linked queued child retains its existing worker and approval controls.")
            return {"project_id": body["project_id"], "cycle_id": cycle["id"]}
        return self._mutate("stop_cycle", body, change, paused_ok=True)

    @staticmethod
    def _finish(cycle, status, reason):
        cycle.update(status=status, reason=reason, updated_at=_now())

    def _check_refs(self, refs):
        if type(refs) is not list or len(refs) > 20:
            raise ValueError("At most twenty artifact references may cross an execution boundary.")
        checked, total, seen = [], 0, set()
        for ref in refs:
            _fields(ref, {"path", "sha256", "bytes"}, {"path", "sha256", "bytes"})
            path = _text(ref["path"], 300); _sha(ref["sha256"])
            _integer(ref["bytes"], 0, MAX_REFERENCE_BYTES, "Artifact size")
            if path in seen:
                raise ValueError("Artifact references must be unique.")
            seen.add(path)
            target = self.registry._path(path)
            # _path and _open_read enforce workspace containment/reparse/link rules.
            with self.registry._directory(target.parent) as directory_fd:
                with os.fdopen(self.registry._open_read(target, directory_fd), "rb") as stream:
                    before = os.fstat(stream.fileno())
                    raw = stream.read(MAX_REFERENCE_BYTES+1)
                    after = os.fstat(stream.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("An evidence file changed while it was being checked.")
            total += len(raw)
            if len(raw) != ref["bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"] or total > MAX_EVIDENCE_BYTES:
                raise ValueError("An evidence file is missing, oversized or changed; inspect its original receipt.")
            checked.append({"path": path.replace("\\", "/"), "sha256": ref["sha256"], "bytes": ref["bytes"]})
        return checked

    def review_task(self, body):
        self._arguments(body, {"project_id", "task_id", "content_sha256", "decision", "note", "source_refs"},
                        {"project_id", "task_id", "content_sha256", "decision", "note", "source_refs"})
        _sha(body["content_sha256"]); _identifier(body["task_id"]); _text(body["note"], 2000)
        if type(body["decision"]) is not str or body["decision"] not in {"accept", "reject"} or type(body["source_refs"]) is not list or len(body["source_refs"]) > 10:
            raise ValueError("Choose accept/reject and at most ten local evidence references.")
        def change(db, state):
            project = self._require_project(state, body["project_id"])
            task = next((task for task in project["definition"]["tasks"] if task["id"] == body["task_id"]), None)
            if task is None:
                raise KeyError("Workroom task not found.")
            runtime = project["runtime"][task["id"]]
            if task["kind"] != "manual" or runtime["status"] not in {"pending", "awaiting_review"} or not self._ready(project, task):
                raise HandoffConflict("Only a ready manual task can receive an owner attestation; child failures cannot be accepted away.")
            if self._task_digest(task, runtime) != body["content_sha256"]:
                raise HandoffConflict("This task or its evidence changed; review the current fingerprint.")
            self._dependency_evidence(db, project, task)
            refs = self._check_refs(body["source_refs"])
            self._require_sources(task, refs)
            total = sum(sum(ref["bytes"] for ref in value["evidence"]["artifacts"]) for key, value in project["runtime"].items() if key != task["id"])
            if total+sum(ref["bytes"] for ref in refs) > MAX_EVIDENCE_BYTES:
                raise ValueError("The project accepted-evidence byte budget is exhausted.")
            runtime["review"] = {"decision": body["decision"], "note": body["note"].strip(), "reviewed_at": _now(), "content_sha256": body["content_sha256"]}
            runtime["evidence"] = {"assurance": "owner_attested", "artifacts": refs, "checked_at": _now(), "note": "Owner attestation; file hashes establish identity, not factual truth or customer approval."}
            runtime["status"] = "completed" if body["decision"] == "accept" else "held"
            cycle = self._cycle(project)
            if cycle and cycle["status"] == "active" and body["decision"] == "reject":
                self._finish(cycle, "held", "Owner rejected a manual gate; no subsequent child will be dispatched.")
            project["updated_at"] = _now()
            return {"project_id": body["project_id"], "task_id": task["id"], "assurance": "owner_attested"}
        return self._mutate("review_task", body, change)

    def _assignment(self, project, cycle, task, cap):
        dependencies = []
        by_id = {item["id"]: item for item in project["definition"]["tasks"]}
        artifacts = 0
        for identifier in task["depends_on"]:
            definition = by_id[identifier]; evidence = project["runtime"][identifier]["evidence"]
            artifacts += len(evidence["artifacts"])
            dependencies.append({"task_id": identifier, "title": definition["title"], "acceptance": definition["acceptance"],
                                 "assurance": evidence["assurance"], "artifacts": copy.deepcopy(evidence["artifacts"])})
        definition = project["definition"]
        result = {"schema": 1, "project_id": definition["id"], "cycle_id": cycle["id"], "task_id": task["id"],
                  "project_name": definition["name"], "objective": definition["objective"], "project_acceptance": definition["acceptance"],
                  "task_title": task["title"], "task_acceptance": task["acceptance"], "project_sha256": project["content_sha256"],
                  "workflow_sha256": task["execution_preview"].get("workflow_sha256") if task["execution_preview"] else None,
                  "effective_max_steps": cap, "dependencies": dependencies, "context_is_untrusted_data": True}
        if artifacts > 20 or len(_bytes(result)) > MAX_ASSIGNMENT_BYTES:
            raise ValueError("The complete dependency assignment exceeds twenty artifacts or 8 KiB; split the task before dispatch.")
        return result

    def assignment(self, run_id):
        _identifier(run_id)
        with self.database() as db:
            if not self._exists(db):
                return None
            row = db.execute("SELECT * FROM workroom_children WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            child = self._child_row(db, run_id)
            return copy.deepcopy(child["assignment"]) if child["kind"] == "mission" else None

    def model_step_limit(self, run_id):
        assignment = self.assignment(run_id)
        return _integer(assignment["effective_max_steps"], 1, MAX_STEPS, "Linked mission step cap") if assignment else None

    def _dependency_evidence(self, db, project, task):
        for identifier in task["depends_on"]:
            runtime = project["runtime"][identifier]
            if runtime["status"] != "completed":
                raise ValueError("A direct dependency is incomplete.")
            if runtime["child"]:
                child = self._child_row(db, runtime["child"])
                evidence = self._verify_child(db, child)
                if evidence["artifacts"] != runtime["evidence"]["artifacts"]:
                    raise ValueError("Dependency evidence changed from its accepted completion basis.")
            else:
                if runtime["evidence"]["assurance"] != "owner_attested":
                    raise ValueError("Manual dependency lacks an owner attestation.")
                self._require_sources(next(item for item in project["definition"]["tasks"] if item["id"] == identifier), runtime["evidence"]["artifacts"])
                self._check_refs(runtime["evidence"]["artifacts"])

    def _verify_child(self, db, child):
        if child["kind"] == "routine":
            run = self.routines._run(db.execute("SELECT * FROM routine_runs WHERE id=?", (child["id"],)).fetchone())
            if run["status"] != "completed" or _digest(run["plan"]) != child["execution_sha256"] or len(run["steps"]) != 1:
                raise ValueError("The linked fixed routine is not a completed exact-plan invocation.")
            step = run["steps"][0]
            if step["status"] != "completed" or step["procedure_id"] != child["procedure_id"]:
                raise ValueError("The linked fixed procedure did not complete.")
            self.routines._verify_step(run, step)
            artifacts = self._check_refs(step["receipt"]["artifacts"])
        else:
            row = db.execute("SELECT * FROM runs WHERE id=?", (child["id"],)).fetchone()
            if row is None:
                raise ValueError("The linked mission is missing.")
            run = self.store.decode(row)
            expected = child["execution"]
            observed = {key: run[key] for key in expected}
            if observed != expected or run["status"] != "completed" or run["verification_status"] != "passed":
                raise ValueError("The linked mission is incomplete, unverified or differs from its frozen execution.")
            unresolved = db.execute("SELECT count(*) FROM approvals WHERE run_id=? AND status NOT IN ('executed','rejected','cancelled')", (child["id"],)).fetchone()[0]
            if unresolved:
                raise ValueError("The linked mission retains unresolved or uncertain approval work.")
            recorded = run["verification"]
            actual = verify(run["checks"], self.registry)
            if not run["checks"] or type(recorded) is not dict or recorded.get("status") != "passed" or actual["status"] != "passed" or actual["checks"] != recorded.get("checks") or actual["artifacts"] != recorded.get("artifacts"):
                raise ValueError("The linked mission no longer has exact, nonempty passing verification evidence.")
            prefix = "missions/"+child["id"]+"/"
            refs = []
            for artifact in actual["artifacts"]:
                if not artifact["path"].replace("\\", "/").startswith(prefix):
                    raise ValueError("A mission artifact escaped its isolated child scope.")
                refs.append({"path": artifact["path"], "sha256": artifact["sha256"], "bytes": artifact["size"]})
            artifacts = self._check_refs(refs)
        if not artifacts:
            raise ValueError("Checked child completion requires actual artifact evidence.")
        return {"assurance": "checked_artifacts", "artifacts": artifacts, "checked_at": _now(),
                "note": "Exact completed child checks and current file identity passed; this does not establish external outcomes."}

    def _dispatch(self, db, project, cycle, task):
        self._dependency_evidence(db, project, task)
        if self._drift(db, task):
            raise ValueError("The frozen procedure, workflow or agent/catalog changed before dispatch.")
        cap = min(cycle["max_steps_per_child"], task["execution_preview"]["resolved_agent"]["max_steps"]) if task["kind"] == "workflow" else None
        assignment = self._assignment(project, cycle, task, cap)
        common = {"project_id": project["definition"]["id"], "task_id": task["id"], "cycle_id": cycle["id"],
                  "project_sha256": project["content_sha256"], "assignment": assignment, "step_limit": cap}
        if task["kind"] == "procedure":
            identifier = "workroom-"+uuid.uuid4().hex
            if db.execute("SELECT 1 FROM routine_runs WHERE id=?", (identifier,)).fetchone() or db.execute("SELECT 1 FROM workroom_children WHERE id=?", (identifier,)).fetchone():
                raise ValueError("A child invocation identity already exists; no work was adopted.")
            definition = {"id": "workroom-"+hashlib.sha256((cycle["id"]+task["id"]).encode()).hexdigest()[:32],
                          "name": "Project task: "+task["title"][:100], "revision": 1, "steps": [task["procedure_id"]], "interval_minutes": 1440}
            run = self.routines._enqueue(db, definition, identifier, "workroom")
            child = {**common, "id": identifier, "kind": "routine", "procedure_id": task["procedure_id"], "execution_sha256": _digest(run["plan"])}
        else:
            workflow = task["execution_preview"]["workflow"]
            identifier = self.store._insert_run(db, workflow["prompt"], checks=workflow["checks"], workflow_id=workflow["id"], agent_id=workflow["agent_id"], isolated_outputs=True)
            run = self.store.decode(db.execute("SELECT * FROM runs WHERE id=?", (identifier,)).fetchone())
            execution = {key: run[key] for key in ("prompt", "checks", "workflow_id", "agent_id", "isolated_outputs", "output_prefix")}
            child = {**common, "id": identifier, "kind": "mission", "execution": execution, "execution_sha256": _digest(execution)}
        if len(_bytes(child)) > 64 * 1024:
            raise ValueError("The frozen child dispatch context exceeds its 64 KiB bound.")
        db.execute("INSERT INTO workroom_children VALUES(?,?,?,?,?,?,?)", (identifier, child["kind"], child["project_id"], cycle["id"], task["id"], _json(child), _digest(child)))
        project["runtime"][task["id"]].update(status="active", child=identifier)
        cycle["children_dispatched"] += 1; cycle["current_task_id"] = task["id"]; cycle["updated_at"] = _now()

    def _advance(self, db, project, cycle):
        if cycle["status"] not in {"active", "owner_stopped"}:
            return False
        if self._paused(db):
            if cycle["status"] == "active":
                self._finish(cycle, "held", "Global autonomy pause held this cycle. Clearing pause does not resume it; inspect the retained child and start a revised project explicitly.")
                return True
            return False
        tasks = project["definition"]["tasks"]
        if cycle.get("current_task_id"):
            task_id = cycle["current_task_id"]; runtime = project["runtime"][task_id]
            child = self._child_row(db, runtime["child"])
            observed = self._child_status(db, child)
            if observed["status"] in {"queued", "running", "waiting_approval"}:
                return False
            if observed["status"] != "completed":
                runtime["status"] = "held"
                self._finish(cycle, "held", "Linked child ended "+observed["status"]+"; inspect its original receipt. No retry or acceptance override.")
                cycle["current_task_id"] = None
                return True
            try:
                runtime["evidence"] = self._verify_child(db, child)
                total = sum(sum(ref["bytes"] for ref in value["evidence"]["artifacts"]) for value in project["runtime"].values())
                if total > MAX_EVIDENCE_BYTES:
                    raise ValueError("The project accepted-evidence byte budget is exhausted.")
            except (ValueError, OSError, KeyError, TypeError) as error:
                runtime["status"] = "held"; cycle["current_task_id"] = None
                self._finish(cycle, "held", "Child evidence could not be accepted: "+str(error)[:350])
                return True
            runtime["status"] = "completed"; cycle["current_task_id"] = None; cycle["updated_at"] = _now()
            return True
        if cycle["status"] != "active":
            return False
        if all(runtime["status"] == "completed" for runtime in project["runtime"].values()):
            self._finish(cycle, "completed", "All project tasks have recorded completion bases; external outcomes remain unproven.")
            return True
        ready = next((task for task in tasks if project["runtime"][task["id"]]["status"] in {"pending", "awaiting_review"} and self._ready(project, task)), None)
        if ready is None:
            self._finish(cycle, "held", "No ready task remains; inspect blocked or held dependencies.")
            return True
        runtime = project["runtime"][ready["id"]]
        if ready["kind"] == "manual":
            if runtime["status"] == "awaiting_review":
                return False
            try:
                self._dependency_evidence(db, project, ready)
            except (ValueError, OSError, KeyError, TypeError) as error:
                runtime["status"] = "held"; self._finish(cycle, "held", "Dependency evidence changed: "+str(error)[:350])
                return True
            runtime["status"] = "awaiting_review"; cycle["updated_at"] = _now()
            return True
        if cycle["children_dispatched"] >= cycle["max_children"]:
            self._finish(cycle, "budget_exhausted", "This cycle's child-invocation budget is exhausted. Another cycle requires explicit owner review and start.")
            return True
        try:
            # A failed dispatch rolls back the entire child insertion, not just
            # the link. The enclosing transaction can still retain a safe hold.
            db.execute("SAVEPOINT workroom_dispatch")
            self._dispatch(db, project, cycle, ready)
            db.execute("RELEASE workroom_dispatch")
        except (ValueError, OSError, KeyError, TypeError) as error:
            db.execute("ROLLBACK TO workroom_dispatch"); db.execute("RELEASE workroom_dispatch")
            runtime["status"] = "held"; self._finish(cycle, "held", "Dispatch held before a linked invocation could commit: "+str(error)[:350])
        return True

    def reconcile(self, body):
        self._arguments(body, {"project_id", "cycle_id"}, {"project_id", "cycle_id"})
        def change(db, state):
            project = self._require_project(state, body["project_id"]); cycle = self._cycle(project, body["cycle_id"])
            current = self._cycle(project)
            if current is None or current["id"] != cycle["id"]:
                raise HandoffConflict("Only the current cycle can be reconciled; historical receipts are retained.")
            changed = self._advance(db, project, cycle)
            return {"project_id": body["project_id"], "cycle_id": cycle["id"], "changed": changed, "cycle_status": cycle["status"]}
        return self._mutate("reconcile", body, change, paused_ok=True)

    def tick(self):
        with self.database() as db:
            if not self._exists(db):
                return False
            db.execute("BEGIN IMMEDIATE")
            state = self._state(db)
            for project in state["projects"].values():
                cycle = self._cycle(project)
                if cycle and self._advance(db, project, cycle):
                    project["updated_at"] = _now(); self._write(db, state)
                    return True
            return False

    def recover(self):
        with self.database() as db:
            if not self._exists(db):
                return 0
            db.execute("BEGIN IMMEDIATE")
            state = self._state(db); count = 0
            for project in state["projects"].values():
                cycle = self._cycle(project)
                if cycle and (cycle["status"] == "active" or cycle["status"] == "owner_stopped" and cycle.get("current_task_id")):
                    self._finish(cycle, "held", "Host recovery held this cycle without adopting, requeueing or cancelling its retained child. Inspect original receipts before a revised project.")
                    count += 1
            if count:
                self._write(db, state)
            return count
