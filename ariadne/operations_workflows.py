"""Frozen offline operation steps on Ariadne's canonical routine runner.

Input bytes live in the existing private main database, never in model-writable
workspace copies. This adds no scheduler, model worker or external action path.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import time

from .business_ops import BusinessOps
from .local_controls import LocalControls, _date as _control_date, _parse as _control_parse
from .market_research import MarketResearchTools
from .procedures import MAX_ARTIFACT_BYTES, MAX_TOTAL_BYTES, MAX_RUNS as MAX_PROCEDURE_RUNS, ProcedureConflict
from .routines import Routines, RoutineConflict, MAX_STEPS, _json

UTC = timezone.utc
MAX_INPUT_TOTAL_BYTES = 64 * 1024 * 1024
MAX_INPUT_ROWS = 512
MAX_INPUT_BYTES = 4_000_000
IMPLEMENTATION = "frozen-operations-1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {
    "business_snapshot": ("Business cash and stock snapshot", set(), {"cash_accounts"}),
    "erp_audit": ("ERP invoice export audit", {"path", "as_of"}, {"path", "as_of"}),
    "quote_draft": ("Quote and draft invoice", {"path"}, {"path"}),
    "market_audit": ("Historical market-data audit", {"path", "metadata_path"}, {"path", "metadata_path"}),
    "market_study": ("Gated historical strategy study", {"path", "metadata_path"}, {"path", "metadata_path"}),
    "device_status": ("Saved local device status", set(), set()),
    "device_snapshot": ("Selected device export report", {"source_path", "observed_at"}, {"source_path", "observed_at"}),
}
_BUSINESS_ACTIONS = {"business_snapshot": "snapshot", "erp_audit": "audit_erp", "quote_draft": "draft_quote"}
_WARNINGS = [
    "Inputs and source scope were captured when this routine invocation was accepted; this is not a fresh scan or an external action.",
    "Business outputs are operational drafts, market results are historical simulations, and saved device observations can be stale or unavailable.",
]


class OperationInputError(ValueError):
    """A substantive input-capture failure, distinct from a full routine queue."""


def _bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))+"\n").encode("utf-8")


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _json_data(raw, *, business=False):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Duplicate frozen input JSON keys.")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs,
            parse_float=Decimal if business else float,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite frozen input JSON.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Frozen operation JSON is invalid.") from None


class _FrozenBusiness(BusinessOps):
    def __init__(self, registry, inputs, local, captured, request_id):
        super().__init__(registry)
        self.inputs, self.local, self.captured = inputs, local, captured
        self.output_path = "business-ops/"+request_id+".json"

    def _read(self, relative):
        if relative not in self.inputs:
            if relative == self.output_path:
                return super()._read(relative)
            raise ValueError("The business provider requested a source outside the accepted snapshot.")
        raw = self.inputs[relative]
        return _json_data(raw, business=True), {"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    def _local(self):
        if self.local is None:
            raise ValueError("The operation has no accepted business snapshot.")
        return self.local["value"], self.local["identity"]

    def _snapshot(self, args, local):
        result = super()._snapshot(args, local)
        result["as_of"] = self.captured.date().isoformat()
        result["source_captured_at_utc"] = self.captured.isoformat()
        return result


class _FrozenMarket(MarketResearchTools):
    def __init__(self, registry, inputs, request_id):
        super().__init__(registry)
        self.inputs = inputs
        self.output_path = "reports/market-research/"+request_id+".json"

    def _read(self, value, cap=2_000_000):
        if value not in self.inputs:
            if value == self.output_path:
                return super()._read(value, cap)
            raise ValueError("The market provider requested a source outside the accepted snapshot.")
        raw = self.inputs[value]
        if len(raw) > cap:
            raise ValueError("Accepted market input exceeds its declared bound.")
        return raw


class OperationsWorkflows(Routines):
    def __init__(self, procedures, store):
        super().__init__(procedures, store)
        with self.database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS operation_inputs(digest TEXT PRIMARY KEY,content BLOB NOT NULL,bytes INTEGER NOT NULL)")

    def catalog(self):
        result = super().catalog()
        result["operations"] = [{"id": kind, "name": info[0], "required_fields": sorted(info[1]),
            "fields": sorted(info[2]), "model_calls": 0} for kind, info in _KINDS.items()]
        result["limits"].update(input_budget_bytes=MAX_INPUT_TOTAL_BYTES, input_snapshots=MAX_INPUT_ROWS)
        result["scope"] = "Fixed local procedures and frozen offline business, historical-market and saved-device reports; no refresh, device action, broker, model or installation."
        return result

    def _validate_steps(self, steps):
        if type(steps) is not list or not 1 <= len(steps) <= MAX_STEPS:
            raise ValueError("Choose 1–4 different fixed procedures or offline operations.")
        allowed = {row["id"] for row in self.procedures.catalog()["procedures"]}
        prepared, seen, audits = [], set(), set()
        for step in steps:
            if type(step) is str:
                if step not in allowed or step in seen:
                    raise ValueError("Choose different installed fixed procedures.")
                seen.add(step); prepared.append(step); continue
            if type(step) is not dict or set(step) != {"operation", "arguments"}:
                raise ValueError("An operation step needs operation and arguments only.")
            kind, args = step["operation"], step["arguments"]
            if type(kind) is not str or kind not in _KINDS or kind in seen or type(args) is not dict:
                raise ValueError("Choose different supported offline operations.")
            required, fields = _KINDS[kind][1:]
            if set(args)-fields or required-set(args):
                raise ValueError("Operation arguments do not match the selected fixed operation.")
            args = dict(args)
            for field in ("path", "metadata_path", "source_path"):
                if field in args:
                    args[field] = self.procedures.registry._path(args[field]).relative_to(self.procedures.registry.root).as_posix()
            if kind in {"erp_audit", "quote_draft"} and PurePosixPath(args["path"]).suffix.lower() != ".json":
                raise ValueError("Business operation inputs must be workspace JSON files.")
            if "as_of" in args and args["as_of"] != "today_utc":
                if type(args["as_of"]) is not str or date.fromisoformat(args["as_of"]).isoformat() != args["as_of"]:
                    raise ValueError("ERP as_of must be YYYY-MM-DD or the explicit today_utc policy.")
            if "observed_at" in args:
                args["observed_at"] = _control_date(args["observed_at"]).isoformat()
            if "cash_accounts" in args:
                accounts = args["cash_accounts"]
                if type(accounts) is not list or len(accounts) > 20 or any(type(item) is not str for item in accounts) or len(set(accounts)) != len(accounts):
                    raise ValueError("Select at most twenty different cash-account IDs.")
                args["cash_accounts"] = [self.procedures.registry.business.identifier(item, "cash account") for item in accounts]
            if kind == "market_audit":
                audits.add((args["path"], args["metadata_path"]))
            elif kind == "market_study" and (args["path"], args["metadata_path"]) not in audits:
                raise ValueError("A market study requires a preceding audit of the same dataset and assumptions.")
            seen.add(kind); prepared.append({"operation": kind, "arguments": args})
        return prepared

    @staticmethod
    def _definition_digest(definition):
        return _digest({key: definition[key] for key in ("id", "name", "steps", "interval_minutes")})

    def _read_workspace(self, value, cap):
        registry = self.procedures.registry
        path = registry._path(value)
        with registry._directory(path.parent) as directory:
            with os.fdopen(registry._open_read(path, directory), "rb") as source:
                before = os.fstat(source.fileno())
                raw = source.read(cap+1)
                after = os.fstat(source.fileno())
        if len(raw) > cap:
            raise OperationInputError("An accepted source exceeds its fixed input limit.")
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or len(raw) != before.st_size:
            raise OperationInputError("A source changed during acceptance; no mixed snapshot was accepted.")
        return raw

    @staticmethod
    def _put(db, raw):
        if type(raw) is not bytes or len(raw) > MAX_INPUT_BYTES:
            raise OperationInputError("A frozen input exceeds its bounded size.")
        digest = hashlib.sha256(raw).hexdigest()
        existing = db.execute("SELECT bytes,length(content) size,typeof(content) kind FROM operation_inputs WHERE digest=?", (digest,)).fetchone()
        if existing:
            if existing["kind"] != "blob" or existing["bytes"] != len(raw) or existing["size"] != len(raw):
                raise OperationInputError("An existing private input snapshot failed integrity review.")
            content = db.execute("SELECT substr(content,1,?) FROM operation_inputs WHERE digest=?", (MAX_INPUT_BYTES+1, digest)).fetchone()[0]
            if bytes(content) != raw:
                raise OperationInputError("An existing private input snapshot failed integrity review.")
        else:
            usage = db.execute("SELECT count(*),coalesce(sum(length(content)),0),count(CASE WHEN typeof(content)!='blob' OR typeof(bytes)!='integer' OR bytes!=length(content) OR bytes<0 OR bytes>? THEN 1 END) FROM operation_inputs", (MAX_INPUT_BYTES,)).fetchone()
            if usage[2]:
                raise OperationInputError("Private frozen-input storage has invalid size accounting; owner review is required.")
            if usage[0] >= MAX_INPUT_ROWS or usage[1]+len(raw) > MAX_INPUT_TOTAL_BYTES:
                raise OperationInputError("Private frozen-input storage is full; inspect retained workflow history before continuing. No inputs were deleted.")
            db.execute("INSERT INTO operation_inputs VALUES(?,?,?)", (digest, raw, len(raw)))
        return {"sha256": digest, "bytes": len(raw)}

    def _freeze(self, db, step, captured, snapshots):
        kind, args = step["operation"], dict(step["arguments"])
        if args.get("as_of") == "today_utc":
            args["as_of"] = captured.date().isoformat()
        frozen = {"schema": 1, "implementation": IMPLEMENTATION, "operation": kind, "arguments": args,
                  "captured_at_utc": captured.isoformat(), "inputs": {}}
        for field, cap in (("path", 2_000_000 if kind.startswith("market_") else 262_144),
                           ("metadata_path", 50_000), ("source_path", 65_536)):
            if field in args:
                if args[field] not in snapshots["files"]:
                    snapshots["files"][args[field]] = self._read_workspace(args[field], cap)
                raw = snapshots["files"][args[field]]
                if len(raw) > cap:
                    raise OperationInputError("An accepted source exceeds the selected field's size limit.")
                frozen["inputs"][field] = {"path": args[field], **self._put(db, raw)}
        if kind in {"business_snapshot", "quote_draft"}:
            if snapshots["business"] is None:
                value, identity = self.procedures.registry.business_ops._local()
                snapshots["business"] = _bytes({"value": value, "identity": identity})
            frozen["business"] = self._put(db, snapshots["business"])
        if kind in {"device_status", "device_snapshot"}:
            if snapshots["devices"] is None:
                controls = self.procedures.registry.local_controls
                cfg = controls.configuration()
                with controls.database() as database:
                    size = database.execute("SELECT length(CAST(value AS BLOB)) FROM control_cache WHERE id=1").fetchone()
                    if size and size[0] > 65_536:
                        raise OperationInputError("The saved device cache exceeds its fixed size bound.")
                    row = database.execute("SELECT value FROM control_cache WHERE id=1").fetchone()
                cache = _control_parse(row[0]) if row else None
                snapshots["devices"] = _bytes({"configuration": cfg, "cache": cache})
            frozen["devices"] = self._put(db, snapshots["devices"])
        return frozen

    def _plan(self, definition, db=None):
        if not any(type(step) is dict for step in definition["steps"]):
            return super()._plan(definition, db)
        if db is None:
            raise OperationInputError("Operation capture requires the canonical enqueue transaction.")
        captured = datetime.now(UTC)
        outputs = {row["id"]: row["outputs"] for row in self.procedures.catalog()["procedures"]}
        plan = {"routine_id": definition["id"], "name": definition["name"], "revision": definition["revision"],
            "steps": [], "outputs": {}, "interval_minutes": definition["interval_minutes"], "model_calls": 0,
            "operation_definition_sha256": self._definition_digest(definition), "operation_steps": {}}
        db.execute("SAVEPOINT operation_capture")
        try:
            snapshots = {"files": {}, "business": None, "devices": None}
            for step in definition["steps"]:
                if type(step) is str:
                    identifier = step
                    plan["outputs"][identifier] = outputs[identifier]
                else:
                    identifier = "operation_"+step["operation"]
                    plan["operation_steps"][identifier] = self._freeze(db, step, captured, snapshots)
                    plan["outputs"][identifier] = ["operation.json", "operation.md"]
                plan["steps"].append(identifier)
            db.execute("RELEASE operation_capture")
            return plan
        except Exception as error:
            db.execute("ROLLBACK TO operation_capture")
            db.execute("RELEASE operation_capture")
            if isinstance(error, OperationInputError):
                raise
            raise OperationInputError("Offline operation inputs could not be captured; inspect the selected paths, records and source scope.") from error

    def _enqueue(self, db, definition, request_id, trigger):
        previous = db.execute("SELECT * FROM routine_runs WHERE id=?", (request_id,)).fetchone()
        if previous and any(type(step) is dict for step in definition["steps"]):
            run = self._run(previous)
            if run["plan"].get("operation_definition_sha256") != self._definition_digest(definition):
                raise RoutineConflict(request_id, run["status"], "This request ID belongs to another accepted operation plan.")
            return run
        try:
            return super()._enqueue(db, definition, request_id, trigger)
        except OperationInputError:
            self._disable(db, definition["id"], "Offline operation inputs failed capture; recurrence is held. Inspect sources and explicitly review before enabling again.")
            self._bump(db)
            raise

    def enqueue(self, routine_id, request_id):
        try:
            return super().enqueue(routine_id, request_id)
        except OperationInputError:
            # Manual enqueue's surrounding transaction rolls back; persist its
            # substantive hold in a new transaction without any partial BLOBs.
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                self._disable(db, routine_id, "Offline operation inputs failed capture; inspect sources and explicitly review before enabling recurrence.")
                self._bump(db)
            raise

    def _get(self, reference):
        if type(reference) is not dict or set(reference) != {"sha256", "bytes"} or type(reference["sha256"]) is not str or not _HEX.fullmatch(reference["sha256"]) or type(reference["bytes"]) is not int or not 0 <= reference["bytes"] <= MAX_INPUT_BYTES:
            raise ValueError("A private accepted-input reference is invalid.")
        with self.database() as db:
            row = db.execute("SELECT bytes,length(content) size,typeof(content) kind FROM operation_inputs WHERE digest=?", (reference["sha256"],)).fetchone()
            if not row or row["bytes"] != reference["bytes"] or row["size"] != reference["bytes"] or row["kind"] != "blob":
                raise ValueError("An accepted private input is missing or changed.")
            raw = bytes(db.execute("SELECT substr(content,1,?) FROM operation_inputs WHERE digest=?", (MAX_INPUT_BYTES+1, reference["sha256"])).fetchone()[0])
        if len(raw) != reference["bytes"] or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ValueError("An accepted private input failed its SHA-256 check.")
        return raw

    @staticmethod
    def _expected_paths(kind, request_id):
        paths = {"procedures/"+request_id+"/operation.json", "procedures/"+request_id+"/operation.md"}
        if kind in _BUSINESS_ACTIONS:
            paths.add("business-ops/"+request_id+".json")
        elif kind.startswith("market_"):
            paths.add("reports/market-research/"+request_id+".json")
        return paths

    def _artifact_bytes(self, artifact):
        if (type(artifact) is not dict or set(artifact) != {"path", "bytes", "sha256"} or type(artifact["path"]) is not str
                or type(artifact["bytes"]) is not int or not 0 <= artifact["bytes"] <= MAX_ARTIFACT_BYTES
                or type(artifact["sha256"]) is not str or not _HEX.fullmatch(artifact["sha256"])):
            raise ValueError("Operation artifact evidence is invalid.")
        registry = self.procedures.registry
        path = registry._path(artifact["path"])
        with registry._directory(path.parent) as directory:
            with os.fdopen(registry._open_read(path, directory), "rb") as source:
                raw = source.read(MAX_ARTIFACT_BYTES+1)
        if len(raw) != artifact["bytes"] or hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            raise ValueError("Completed operation artifact changed; preserve its original receipt for review.")
        return raw

    def _verify_step(self, run, step):
        frozen = run["plan"].get("operation_steps", {}).get(step["procedure_id"])
        if frozen is None:
            return super()._verify_step(run, step)
        receipt = step.get("receipt")
        if (type(receipt) is not dict or receipt.get("ok") is not True or receipt.get("status") != "completed"
                or type(receipt.get("model_calls")) is not int or receipt["model_calls"] != 0
                or receipt.get("request_id") != step["request_id"] or receipt.get("procedure_id") != step["procedure_id"]
                or receipt.get("frozen_plan_sha256") != _digest(frozen)):
            raise ValueError("Completed offline operation receipt does not bind its accepted step.")
        artifacts = receipt.get("artifacts")
        if type(artifacts) is not list or any(type(row) is not dict for row in artifacts):
            raise ValueError("Completed operation lacks a complete artifact set.")
        if any(set(row) != {"path", "bytes", "sha256"} or type(row["path"]) is not str
               or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= MAX_ARTIFACT_BYTES
               or type(row["sha256"]) is not str or not _HEX.fullmatch(row["sha256"]) for row in artifacts):
            raise ValueError("Completed operation artifact evidence has unsupported fields or types.")
        expected = self._expected_paths(frozen["operation"], step["request_id"])
        if len(artifacts) != len(expected) or {row.get("path") for row in artifacts} != expected or sum(row.get("bytes", MAX_TOTAL_BYTES+1) for row in artifacts) > MAX_TOTAL_BYTES:
            raise ValueError("Completed operation artifact set differs from its fixed contract.")
        for artifact in artifacts:
            self._artifact_bytes(artifact)

    def _cancelled(self):
        return bool(self.store.setting("paused", False))

    def _run_child(self, run, step):
        frozen = run["plan"].get("operation_steps", {}).get(step["procedure_id"])
        if frozen is None:
            return super()._run_child(run, step)
        if frozen.get("operation") == "market_study":
            audited = False
            for previous in run["steps"]:
                if previous["request_id"] == step["request_id"]:
                    break
                candidate = run["plan"].get("operation_steps", {}).get(previous["procedure_id"], {})
                if candidate.get("operation") == "market_audit" and candidate.get("inputs") == frozen.get("inputs"):
                    self._verify_step(run, previous)
                    audited = previous["status"] == "completed" and previous["receipt"].get("audit_valid") is True
            if not audited:
                raise ValueError("A strategy study requires verified successful audit evidence for the exact accepted input bytes.")
        return self._run_operation(step, frozen)

    def _run_operation(self, step, frozen):
        if (type(frozen) is not dict or type(frozen.get("schema")) is not int or frozen["schema"] != 1 or frozen.get("implementation") != IMPLEMENTATION
                or type(frozen.get("operation")) is not str or frozen["operation"] not in _KINDS or step["procedure_id"] != "operation_"+frozen["operation"]):
            raise ValueError("Accepted offline operation schema is invalid.")
        kind, request_id = frozen["operation"], step["request_id"]
        args = frozen["arguments"]
        # Revalidate the fixed operation shape; no string can select a tool.
        check = {"operation": kind, "arguments": args}
        if kind == "market_study":
            self._validate_steps([{"operation": "market_audit", "arguments": args}, check])
        else:
            self._validate_steps([check])
        keys = {"schema", "implementation", "operation", "arguments", "captured_at_utc", "inputs"}
        if kind in {"business_snapshot", "quote_draft"}:
            keys.add("business")
        if kind in {"device_status", "device_snapshot"}:
            keys.add("devices")
        if set(frozen) != keys or type(frozen["inputs"]) is not dict or set(frozen["inputs"]) != {key for key in args if key in {"path", "metadata_path", "source_path"}}:
            raise ValueError("The accepted operation lacks its complete fixed source set.")
        captured = _control_date(frozen["captured_at_utc"])
        inputs = {}
        for field, reference in frozen["inputs"].items():
            if field not in {"path", "metadata_path", "source_path"} or reference.get("path") != args.get(field):
                raise ValueError("Accepted source reference differs from the fixed arguments.")
            inputs[reference["path"]] = self._get({key: reference[key] for key in ("sha256", "bytes")})
        local = _json_data(self._get(frozen["business"])) if "business" in frozen else None
        devices = _json_data(self._get(frozen["devices"])) if "devices" in frozen else None
        registry = self.procedures.registry
        receipt = {"ok": False, "status": "running", "request_id": request_id, "procedure_id": step["procedure_id"],
            "created_at": time.time(), "completed_at": None, "wall_seconds": None, "model_calls": 0,
            "frozen_plan_sha256": _digest(frozen), "artifacts": [], "warnings": list(_WARNINGS), "replayed": False}
        with self.procedures.database() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT status FROM procedure_runs WHERE request_id=?", (request_id,)).fetchone()
            if old:
                raise ProcedureConflict(request_id, old["status"], "An operation child already has an execution marker; it will not be adopted or retried.")
            if db.execute("SELECT count(*) FROM procedure_runs").fetchone()[0] >= MAX_PROCEDURE_RUNS:
                raise ValueError("Procedure history is full; inspect retained receipts before adding an operation child.")
            db.execute("INSERT INTO procedure_runs VALUES(?,?,?,?,?,?,?)", (request_id, step["procedure_id"], _digest(frozen), "running", receipt["created_at"], None, _json(receipt)))
        started = time.monotonic()
        relative = "procedures/"+request_id
        destination = registry._path(relative)
        try:
            if self._cancelled():
                raise ValueError("Operation held by the global autonomy pause.")
            # A pre-existing directory or child artifact is never adopted.
            if any(registry._path(path).exists() for path in self._expected_paths(kind, request_id)):
                raise ValueError("An operation output already exists; preserve it for review.")
            with registry._directory(destination.parent, create=True) as directory:
                if os.name == "nt":
                    destination.mkdir()
                else:
                    os.mkdir(destination.name, mode=0o700, dir_fd=directory)
            if kind in _BUSINESS_ACTIONS:
                provider = _FrozenBusiness(registry, inputs, local, captured, request_id)
                result = provider.execute("business_ops", {"action": _BUSINESS_ACTIONS[kind], "request_id": request_id, **args}, cancelled=self._cancelled)
            elif kind.startswith("market_"):
                provider = _FrozenMarket(registry, inputs, request_id)
                result = provider.execute("market_research", {"action": "audit" if kind == "market_audit" else "study", "request_id": request_id, **args}, cancelled=self._cancelled)
            else:
                cfg, cache = devices["configuration"], devices["cache"]
                now = datetime.now(UTC)
                controls = registry.local_controls
                if kind == "device_snapshot":
                    raw = inputs[args["source_path"]]
                    cache = controls._snapshot(cfg, _control_parse(raw.decode("utf-8")), _control_date(args["observed_at"]).timestamp(),
                        "workflow_frozen_export", hashlib.sha256(raw).hexdigest(), now)
                view = controls._view(cfg, cache, now) if cache is not None else {"status": "unavailable", "entities": [], "stale": True, "warning": "No accepted saved device observations."}
                result = {"ok": True, "snapshot": view, "enabled": cfg["enabled"], "model_calls": 0, "artifacts": [],
                          "scope": "Selected accepted observations only; the live control cache was not changed and no refresh/action occurred."}
            if result.get("ok") is not True:
                raise ValueError("The accepted operation did not return a successful fixed result.")
            receipt["artifacts"] = [dict(row) for row in result.get("artifacts", [])]
            for artifact in receipt["artifacts"]:
                raw = self._artifact_bytes(artifact)
                actual = _json_data(raw)
                if kind.startswith("market_"):
                    for field, key in (("path", "csv"), ("metadata_path", "metadata")):
                        if actual["inputs"][key]["sha256"] != frozen["inputs"][field]["sha256"]:
                            raise ValueError("Actual market output did not use its accepted input bytes.")
                    receipt["audit_valid"] = actual["audit"]["valid"] is True
                    if not receipt["audit_valid"]:
                        raise ValueError("Market-data audit reported invalid input; the study and remaining steps are held.")
                elif kind in _BUSINESS_ACTIONS:
                    expected_hashes = [row["sha256"] for row in frozen["inputs"].values()]
                    if local is not None:
                        expected_hashes.append(local["identity"]["sha256"])
                    if [row["sha256"] for row in actual["sources"]] != expected_hashes:
                        raise ValueError("Actual business output did not bind the accepted source snapshots.")
            report = {"schema": 1, "operation": kind, "request_id": request_id,
                "generated_at_utc": datetime.now(UTC).isoformat(), "captured_at_utc": captured.isoformat(),
                "frozen_plan_sha256": _digest(frozen), "accepted_arguments": args, "source_identities": frozen["inputs"],
                "summary": result.get("summary"), "observation": result.get("snapshot"), "provider_artifacts": receipt["artifacts"],
                "model_calls": 0, "network_calls": 0, "warnings": list(_WARNINGS)}
            outputs = {"operation.json": _bytes(report), "operation.md": ("# "+_KINDS[kind][0]+"\n\n"
                +"Captured accepted inputs: "+captured.isoformat()+".\n\n"
                +"Model calls: 0. Network calls: 0. External actions: none.\n\n"
                +"The full source identities, assumptions and provider outputs are recorded in operation.json.\n\n"
                +"\n".join("- "+warning for warning in _WARNINGS)+"\n").encode()}
            if self._cancelled():
                raise ValueError("Operation held before report publication.")
            with registry._directory(destination) as directory:
                for name, raw in outputs.items():
                    if len(raw) > MAX_ARTIFACT_BYTES:
                        raise ValueError("Operation wrapper report exceeds its output bound.")
                    self.procedures._publish(destination/name, raw, directory)
                    receipt["artifacts"].append({"path": relative+"/"+name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            receipt.update(ok=True, status="completed", completed_at=time.time(), wall_seconds=round(time.monotonic()-started, 6))
            if self._cancelled():
                raise ValueError("Operation held before its completion receipt.")
            self._verify_step({"plan": {"operation_steps": {step["procedure_id"]: frozen}}}, {**step, "receipt": receipt})
            with self.procedures.database() as db:
                db.execute("UPDATE procedure_runs SET status='completed',completed=?,receipt=? WHERE request_id=? AND status='running'",
                    (receipt["completed_at"], _json(receipt), request_id))
            return receipt
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            receipt.update(ok=False, status="failed", completed_at=time.time(), wall_seconds=round(time.monotonic()-started, 6),
                error="Frozen offline operation stopped; inspect its partial output and accepted inputs before using a new request ID.")
            try:
                with self.procedures.database() as db:
                    db.execute("UPDATE procedure_runs SET status='failed',completed=?,receipt=? WHERE request_id=? AND status='running'", (receipt["completed_at"], _json(receipt), request_id))
            except Exception:
                pass
            raise ValueError(receipt["error"]) from error
