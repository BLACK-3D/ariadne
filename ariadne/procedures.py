"""Named, deterministic local report procedures with durable execution receipts.

Procedures read typed local state and publish a fixed set of new report files.
They do not invoke a model, launch programs, call services, change inventories,
or treat reference text as executable instructions. Crash recovery is fail-stop.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager

from .atomic import publish_bytes
from .config import _allowed_path

MAX_ENTRIES = 1000
MAX_SNAPSHOT_BYTES = 4_000_000
MAX_ARTIFACT_BYTES = 4_000_000
MAX_TOTAL_BYTES = 8_000_000
MAX_RUNS = 100_000
_REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_FEMA = {"title": "FEMA: Are You Ready? (2020), official federal preservation copy", "url": "https://www.govinfo.gov/content/pkg/GOVPUB-HS5_100-PURL-gpo159555/pdf/GOVPUB-HS5_100-PURL-gpo159555.pdf"}
_PATTERNS = {"title": "Anthropic: Building effective agents — predictable workflow patterns", "url": "https://www.anthropic.com/research/building-effective-agents"}
_CONTEXT = {"title": "Node-RED: Working with context — persistence considerations", "url": "https://nodered.org/docs/user-guide/context"}
_DEFINITIONS = (
    ("readiness_report", "Household readiness report", "Snapshot reported supplies, explicit household assumptions and calculated water, calorie and energy gaps.", ("readiness.md", "readiness.json", "supplies.csv")),
    ("stock_rotation", "Stock inspection and rotation queue", "List expired and unknown-expiry supplies for owner inspection without inferring safety or disposal decisions.", ("stock-rotation.csv", "stock-rotation.json")),
    ("power_budget", "Essential power budget", "Calculate actual saved load assumptions, nominal capacity, reserves, conversion losses and estimated runtime.", ("power-budget.md", "power-budget.json", "loads.csv")),
    ("rebuilding_plan", "Rebuilding dependency plan", "Produce a reviewable task dependency graph, current blockers and available next tasks from the saved roadmap.", ("rebuilding-plan.md", "rebuilding-plan.json")),
    ("reference_audit", "Offline reference integrity audit", "Verify bundled source bytes and index integrity, and report private owner-book text coverage and unreadable sections.", ("reference-audit.json", "reference-audit.md")),
    ("business_snapshot", "Local business records snapshot", "Export current inventory and trial balance as bounded operational records, with no ledger changes.", ("inventory.csv", "trial-balance.csv", "business-snapshot.json")),
    ("knowledge_inventory", "Knowledge provenance inventory", "Export imported document metadata, versions, hashes and extraction warnings without copying document text.", ("knowledge-inventory.json", "knowledge-inventory.csv")),
    ("offline_checklist", "Offline continuity checklist", "Inspect local profile files, passive network policy state and bundled reference integrity without network probes.", ("offline-checklist.md", "offline-checklist.json")),
)
_PROCEDURES = {row[0]: row for row in _DEFINITIONS}
_OWNER_WARNING = "Supplies, capacities and task statuses are owner reports. These reports do not certify safety, nutritional adequacy, equipment readiness or permission for physical actions."


class ProcedureConflict(ValueError):
    def __init__(self, request_id, status, message):
        self.request_id, self.status = request_id, status
        super().__init__(message)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"


def _csv(rows, columns):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column, "")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if type(value) is str and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            values.append("" if value is None else value)
        writer.writerow(values)
    return output.getvalue().encode("utf-8")


def _md(value):
    return html.escape(str(value), quote=False).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _markdown(title, lines, warnings=()):
    text = "# " + title + "\n\nGenerated from local saved state. Model calls: 0.\n\n"
    text += "\n".join(lines) + "\n"
    if warnings:
        text += "\n## Assumptions and limits\n\n" + "\n".join("- " + _md(warning) for warning in warnings) + "\n"
    return text.encode("utf-8")


class Procedures:
    def __init__(self, registry, store):
        self.registry, self.store = registry, store
        self.path = registry.settings.data_dir / "procedures.sqlite3"
        self.lock = threading.RLock()

    def catalog(self):
        return {"procedures": [{"id": identifier, "name": name, "description": description,
                                "outputs": list(outputs), "model_calls": 0,
                                "sources": [_PATTERNS, _FEMA] if identifier in {"readiness_report", "stock_rotation", "rebuilding_plan"} else [_PATTERNS, _CONTEXT]}
                               for identifier, name, description, outputs in _DEFINITIONS],
                "limits": {"entries": MAX_ENTRIES, "snapshot_bytes": MAX_SNAPSHOT_BYTES,
                           "artifact_bytes": MAX_ARTIFACT_BYTES, "total_output_bytes": MAX_TOTAL_BYTES},
                "scope": "Fixed local reports only; no model, services, installations, account actions or physical controls.",
                "recovery": "Completed requests replay verified receipts. Interrupted or failed request IDs never run again automatically."}

    @contextmanager
    def database(self):
        path = _allowed_path(self.path)
        with self.lock, self.registry._directory(path.parent, create=True):
            for suffix in ("", "-journal", "-wal", "-shm"):
                file = _allowed_path(str(path) + suffix)
                if file.exists():
                    info = file.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Procedure ledger files must be regular files with one link.")
            db = sqlite3.connect(path, timeout=5)
            db.row_factory = sqlite3.Row
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            try:
                db.execute("PRAGMA trusted_schema=OFF")
                db.executescript("""
                CREATE TABLE IF NOT EXISTS procedure_runs(
                    request_id TEXT PRIMARY KEY,procedure_id TEXT NOT NULL,digest TEXT NOT NULL,
                    status TEXT NOT NULL,created REAL NOT NULL,completed REAL,receipt TEXT NOT NULL);
                """)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def history(self):
        with self.database() as db:
            rows = list(db.execute("SELECT * FROM procedure_runs ORDER BY created DESC,request_id LIMIT 100"))
        return {"runs": [{**json.loads(row["receipt"]), "status": row["status"]} for row in rows],
                "note": "History records saved execution outcomes; artifact integrity is rechecked when a completed request is replayed."}

    def _reference_audit(self):
        references = self.registry.references
        catalog = references._catalog()
        resources = catalog["resources"]
        if len(resources) > 200:
            raise ValueError("Reference catalog exceeds its bounded resource count.")
        rows, warnings = [], []
        try:
            index = references._index()
            index_ok = True
        except (ValueError, OSError, KeyError, TypeError):
            index, index_ok = {"resources": {}}, False
            warnings.append("The bundled text index failed its integrity check; restore or rebuild it from verified source files.")
        for resource in resources:
            row = {key: resource.get(key) for key in ("id", "title", "topic", "path", "bytes", "sha256", "sources")}
            row.update(verified=False, indexed=False)
            if resource.get("path"):
                try:
                    data = references._verified_resource(resource)
                    row["actual_bytes"] = len(data)
                    row["actual_sha256"] = hashlib.sha256(data).hexdigest()
                    row["verified"] = True
                    record = index["resources"].get(resource["id"])
                    row["indexed"] = bool(index_ok and record and record.get("source_sha256") == resource.get("sha256"))
                except (ValueError, OSError, KeyError, TypeError):
                    row["error"] = "Missing, altered or unsupported bundled source; the claimed source hash was not verified."
            else:
                row["error"] = "This entry is a recorded external link; no offline source bytes are included."
            rows.append(row)
        books = references.books.books()
        if len(books) > 100:
            raise ValueError("Owner book metadata exceeds its bounded count.")
        if not books:
            warnings.append("No private owner book is imported. A recorded title or link is not searchable full book text.")
        if any(not book.get("coverage_complete") for book in books):
            warnings.append("Some private books have sections without extractable text; inspect their coverage records.")
        warnings.append("Owner-book hashes identify the originally imported snapshot. Original source folders are not reread by this audit, and images are not interpreted.")
        if any(not row["verified"] for row in rows):
            warnings.append("Some catalog entries lack verified offline bytes; consult each audit row before claiming offline availability.")
        return {"schema": 1, "index_verified": index_ok, "resources": rows, "owner_books": books,
                "verified_sources": sum(row["verified"] for row in rows), "total_sources": len(rows)}, warnings

    def _business_rows(self, action, limit):
        rows, offset = [], 0
        while len(rows) < limit:
            response = self.registry.business.execute("business_records", {"action": action, "limit": min(100, limit-len(rows)), "offset": offset})
            if not response.get("ok"):
                raise ValueError("Local business snapshot could not be read.")
            rows.extend(response["records"])
            if not response["has_more"]:
                return rows, False
            offset += len(response["records"])
        return rows, True

    def _snapshot(self, identifier):
        warnings = []
        if identifier in {"readiness_report", "stock_rotation", "power_budget", "rebuilding_plan"}:
            overview = self.registry.survival.overview()
            warnings.append(_OWNER_WARNING)
            if identifier == "readiness_report":
                warnings.extend(overview["summary"]["warnings"])
                return overview, warnings
            if identifier == "stock_rotation":
                supplies = [row for row in overview["supplies"] if row["expiry_status"] in {"expired", "unknown"}]
                supplies.sort(key=lambda row: (row["expiry_status"] != "expired", row["expires"], row["id"]))
                warnings.append("Unknown expiry and expired stock are inspection queues, not recommendations to consume or discard supplies.")
                return {"schema": 1, "revision": overview["revision"], "as_of": overview["summary"]["as_of"], "supplies": supplies}, warnings
            if identifier == "power_budget":
                calculated = overview["summary"]["plan"]
                warnings.extend(calculated["assumptions"] + overview["summary"]["warnings"])
                return {"schema": 1, "revision": overview["revision"], "profile": overview["profile"], "power": calculated["power"], "coverage": overview["summary"]["power"]}, warnings
            tasks = overview["tasks"]
            if not tasks:
                warnings.append("No rebuilding tasks are saved. Seed or create a reviewed roadmap before treating this as a dependency plan.")
            return {"schema": 1, "revision": overview["revision"], "tasks": tasks,
                    "open_tasks": overview["summary"]["open_tasks"], "blocked_tasks": overview["summary"]["blocked_tasks"]}, warnings
        if identifier == "reference_audit":
            return self._reference_audit()
        if identifier == "business_snapshot":
            inventory, inventory_more = self._business_rows("inventory", MAX_ENTRIES // 2)
            balances, balances_more = self._business_rows("trial_balance", MAX_ENTRIES // 2)
            warnings.append("This is a bounded operational snapshot in named integer stock units and integer cents, not tax, audit or legal certification.")
            if inventory_more or balances_more:
                warnings.append("Business rows exceed this report's 500-row limit per dataset; the output is explicitly partial.")
            return {"schema": 1, "inventory": inventory, "trial_balance": balances,
                    "partial": inventory_more or balances_more, "currency_unit": "cents"}, warnings
        if identifier == "knowledge_inventory":
            with self.store.connect() as db:
                rows = list(db.execute("SELECT id,title,created_at,source_name,source_path,sha256,format,byte_size,version,updated_at,warnings,processor,content_sha256 FROM documents ORDER BY id LIMIT ?", (MAX_ENTRIES+1,)))
            documents = [self.store.decode(row) for row in rows[:MAX_ENTRIES]]
            if len(rows) > MAX_ENTRIES:
                warnings.append("Document metadata exceeded the report limit; this inventory is partial.")
            if not documents:
                warnings.append("No imported local knowledge documents are stored.")
            warnings.append("Metadata describes imported snapshots, not a fresh scan of source folders. Document body text and private memory contents are excluded.")
            return {"schema": 1, "documents": documents, "partial": len(rows) > MAX_ENTRIES}, warnings
        if identifier == "offline_checklist":
            from .network_policy import network_policy_status
            audit, warnings = self._reference_audit()
            profiles = []
            for name in ("voice-config.json", "ocr-config.json", "media-config.json", "knowledge-config.json"):
                file = _allowed_path(self.registry.settings.data_dir / name)
                row = {"name": name, "present": False, "valid_json": False}
                if file.exists():
                    try:
                        with self.registry._directory(file.parent) as descriptor:
                            with os.fdopen(self.registry._open_read(file, descriptor), "rb") as source:
                                encoded = source.read(1_000_001)
                        if len(encoded) > 1_000_000:
                            raise ValueError("Profile exceeds bounded size.")
                        profile = json.loads(encoded)
                        row.update(present=True, valid_json=type(profile) is dict, bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest())
                    except (ValueError, OSError):
                        row["error"] = "Profile is invalid, linked or oversized."
                profiles.append(row)
            warnings.extend(["No network, model, native program, microphone or hardware probe was performed. Profile presence does not prove referenced assets are installed or functional.",
                             "A power-loss recovery plan requires separately verified backups, restore exercises and hardware/power arrangements; this checklist does not create a backup."])
            return {"schema": 1, "profiles": profiles, "network_policy": network_policy_status(), "references": audit,
                    "survival_ready": self.registry.survival.status()["ready"], "network_probes": 0, "model_calls": 0}, warnings
        raise ValueError("Unknown fixed procedure.")

    @staticmethod
    def _render(identifier, snapshot, warnings):
        if identifier == "readiness_report":
            summary = snapshot["summary"]
            lines = [f"Snapshot revision: {snapshot['revision']}. As of: {summary['as_of']}.",
                     f"Household: {snapshot['profile']['people']} people; {snapshot['profile']['days']} planning days.",
                     "", "| Resource | Available | Required | Shortfall | Coverage days |", "|---|---:|---:|---:|---:|"]
            for group, unit in (("water", "liters"), ("food", "kcal"), ("power", "wh")):
                row = summary[group]
                lines.append("| " + " | ".join(_md(value) for value in (group + " (" + unit + ")", row["available_"+unit], row["required_"+unit], row["shortfall_"+unit], row["coverage_days"])) + " |")
            return {"readiness.md": _markdown("Household readiness report", lines, warnings), "readiness.json": _json(snapshot),
                    "supplies.csv": _csv(snapshot["supplies"], ("id", "name", "category", "quantity", "unit", "expires", "expiry_status", "location", "safety_verified", "notes"))}
        if identifier == "stock_rotation":
            return {"stock-rotation.csv": _csv(snapshot["supplies"], ("id", "name", "category", "quantity", "unit", "expires", "expiry_status", "location", "notes")), "stock-rotation.json": _json(snapshot)}
        if identifier == "power_budget":
            power = snapshot["power"]
            lines = ["Saved assumption revision: " + str(snapshot["revision"]),
                     f"Daily load energy: {power['daily_wh']} Wh. Horizon demand: {power['total_wh']} Wh.",
                     f"Profile nominal battery: {power['battery_nominal_wh']} Wh. Estimated usable delivered energy: {power['battery_usable_wh']} Wh.",
                     f"Reserved capacity fraction: {power['reserve_fraction']}. Inverter efficiency: {power['inverter_efficiency']}.",
                     "Profile battery runtime (days): " + str(power["battery_runtime_days"]),
                     "Inventory-inclusive coverage: " + json.dumps(snapshot["coverage"], sort_keys=True)]
            return {"power-budget.md": _markdown("Essential power budget", lines, warnings), "power-budget.json": _json(snapshot),
                    "loads.csv": _csv(power["loads"], ("name", "watts", "hours_per_day", "count", "daily_wh"))}
        if identifier == "rebuilding_plan":
            tasks = snapshot["tasks"]
            lookup = {task["id"]: "task" + str(index) for index, task in enumerate(tasks)}
            lines = [f"Saved revision: {snapshot['revision']}. Open: {snapshot['open_tasks']}. Blocked: {snapshot['blocked_tasks']}.", "", "## Dependency graph", "", "```mermaid", "flowchart TD"]
            for task in tasks:
                # Generated graph node IDs are fixed-safe. Labels are encoded
                # entities so user task names cannot inject Mermaid syntax.
                label = "".join(character if character.isalnum() or character in " -_" else "&#" + str(ord(character)) + ";" for character in task["title"])
                lines.append(f'  {lookup[task["id"]]}["{label}"]')
            for task in tasks:
                for dependency in task["depends_on"]:
                    lines.append(f"  {lookup[dependency]} --> {lookup[task['id']]}")
            lines.extend(["```", "", "## Recorded tasks", ""])
            for task in tasks:
                lines.extend(["### " + _md(task["title"]), "", f"ID: {_md(task['id'])}; domain: {_md(task['domain'])}; status: {_md(task['status'])}; ready: {task['ready']}.",
                              "Dependencies: " + _md(", ".join(task["depends_on"]) or "none"), "Blocked by: " + _md(", ".join(task["blocked_by"]) or "none"), _md(task["notes"]), ""])
            return {"rebuilding-plan.md": _markdown("Rebuilding dependency plan", lines, warnings), "rebuilding-plan.json": _json(snapshot)}
        if identifier == "reference_audit":
            lines = [f"Verified bundled source files: {snapshot['verified_sources']} / {snapshot['total_sources']}.",
                     f"Text index integrity verified: {snapshot['index_verified']}.", f"Private books: {len(snapshot['owner_books'])}."]
            lines.extend("- " + _md(row["title"]) + ": verified=" + str(row["verified"]) + ", indexed=" + str(row["indexed"]) for row in snapshot["resources"])
            return {"reference-audit.json": _json(snapshot), "reference-audit.md": _markdown("Offline reference audit", lines, warnings)}
        if identifier == "business_snapshot":
            return {"inventory.csv": _csv(snapshot["inventory"], ("id", "name", "unit", "quantity", "unit_price_cents")),
                    "trial-balance.csv": _csv(snapshot["trial_balance"], ("account", "debit_cents", "credit_cents", "balance_cents")), "business-snapshot.json": _json(snapshot)}
        if identifier == "knowledge_inventory":
            return {"knowledge-inventory.json": _json(snapshot), "knowledge-inventory.csv": _csv(snapshot["documents"], ("id", "title", "source_name", "source_path", "format", "version", "sha256", "content_sha256", "warnings"))}
        if identifier == "offline_checklist":
            lines = ["- [ ] Review profile files and independently qualify their referenced assets.", "- [ ] Review each bundled reference audit row and owner-book coverage record.",
                     "- [ ] Confirm a separate, tested backup and restore plan.", "- [ ] Confirm power, ventilation and installation arrangements with qualified reviewers.",
                     "", "Passive observations:", "", "| Profile | Present | JSON object |", "|---|---|---|"]
            lines.extend(f"| {_md(row['name'])} | {row['present']} | {row['valid_json']} |" for row in snapshot["profiles"])
            lines.append("\nNetwork policy: " + json.dumps(snapshot["network_policy"], sort_keys=True))
            return {"offline-checklist.md": _markdown("Offline continuity checklist", lines, warnings), "offline-checklist.json": _json(snapshot)}
        raise ValueError("Unknown fixed procedure.")

    def _publish(self, destination, payload, directory_fd):
        if os.name == "nt":
            publish_bytes(destination, payload)
            return
        # Keep publication relative to the same held directory descriptor on
        # POSIX as well; no path-based open after the confinement check.
        stage = ".procedure-" + secrets.token_hex(12) + ".tmp"
        descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            os.link(stage, destination.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
            os.unlink(stage, dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(stage, dir_fd=directory_fd)
            except FileNotFoundError:
                pass

    def _verify_receipt(self, receipt):
        root = "procedures/" + receipt["request_id"] + "/"
        for artifact in receipt["artifacts"]:
            relative = artifact["path"]
            if not relative.startswith(root) or "/" in relative.removeprefix(root):
                raise ValueError("Stored procedure artifact path is invalid.")
            path = self.registry._path(relative)
            with self.registry._directory(path.parent) as descriptor:
                with os.fdopen(self.registry._open_read(path, descriptor), "rb") as source:
                    data = source.read(MAX_ARTIFACT_BYTES + 1)
            if len(data) != artifact["bytes"] or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
                raise ValueError("Completed procedure artifacts changed; inspect the original receipt and use a new request ID.")

    def run(self, procedure_id, request_id):
        if type(procedure_id) is not str or procedure_id not in _PROCEDURES:
            raise ValueError("Choose an installed named procedure.")
        if type(request_id) is not str or not _REQUEST.fullmatch(request_id):
            raise ValueError("request_id must contain 1–64 letters, digits, underscores or hyphens; no paths or dots.")
        relative = "procedures/" + request_id
        destination = self.registry._path(relative)
        digest = hashlib.sha256(_json({"schema": 1, "procedure_id": procedure_id, "outputs": _PROCEDURES[procedure_id][3]})).hexdigest()
        with self.registry._lock, self.lock:
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute("SELECT * FROM procedure_runs WHERE request_id=?", (request_id,)).fetchone()
                if previous:
                    if previous["digest"] != digest or previous["procedure_id"] != procedure_id:
                        raise ProcedureConflict(request_id, previous["status"], "request_id already belongs to different procedure inputs.")
                    if previous["status"] != "completed":
                        raise ProcedureConflict(request_id, previous["status"], "This request previously started or failed. Inspect its files/history and choose a new request ID; it will not be replayed.")
                    receipt = json.loads(previous["receipt"])
                    self._verify_receipt(receipt)
                    return {**receipt, "replayed": True}
                if db.execute("SELECT count(*) FROM procedure_runs").fetchone()[0] >= MAX_RUNS:
                    raise ValueError("Procedure run history is full; archive reviewed history before adding more runs.")
                created = time.time()
                receipt = {"ok": False, "request_id": request_id, "procedure_id": procedure_id, "status": "running", "created_at": created,
                           "completed_at": None, "wall_seconds": None, "model_calls": 0, "artifacts": [], "warnings": [], "replayed": False}
                db.execute("INSERT INTO procedure_runs VALUES(?,?,?,?,?,?,?)", (request_id, procedure_id, digest, "running", created, None, _json(receipt).decode()))
            started = time.monotonic()
            try:
                # A pre-existing directory is not adopted, even if empty. This
                # prevents overwriting files left by a crash or an unrelated run.
                with self.registry._directory(destination.parent, create=True) as parent_fd:
                    if os.name == "nt":
                        destination.mkdir()
                    else:
                        os.mkdir(destination.name, mode=0o700, dir_fd=parent_fd)
                    with self.registry._directory(destination) as directory_fd:
                        snapshot, warnings = self._snapshot(procedure_id)
                        snapshot_data = _json(snapshot)
                        if len(snapshot_data) > MAX_SNAPSHOT_BYTES:
                            raise ValueError("Procedure input snapshot exceeds its bounded size; narrow the saved records.")
                        receipt["input_snapshot_sha256"] = hashlib.sha256(snapshot_data).hexdigest()
                        receipt["warnings"] = warnings
                        outputs = self._render(procedure_id, snapshot, warnings)
                        if set(outputs) != set(_PROCEDURES[procedure_id][3]):
                            raise ValueError("Fixed procedure output set did not match its definition.")
                        if any(type(data) is not bytes or len(data) > MAX_ARTIFACT_BYTES for data in outputs.values()) or sum(len(data) for data in outputs.values()) > MAX_TOTAL_BYTES:
                            raise ValueError("Procedure output exceeds its bounded artifact size.")
                        for name, data in outputs.items():
                            if name not in _PROCEDURES[procedure_id][3] or "/" in name or "\\" in name:
                                raise ValueError("Procedure attempted an unapproved filename.")
                            self._publish(destination / name, data, directory_fd)
                            receipt["artifacts"].append({"path": relative + "/" + name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                receipt.update(ok=True, status="completed", completed_at=time.time(), wall_seconds=round(time.monotonic()-started, 6))
                with self.database() as db:
                    db.execute("UPDATE procedure_runs SET status='completed',completed=?,receipt=? WHERE request_id=? AND status='running'", (receipt["completed_at"], _json(receipt).decode(), request_id))
                return receipt
            except BaseException as error:
                # Persist a fail-stop marker even after a partial publication.
                # A process kill can leave 'running', which has the same policy.
                receipt.update(ok=False, status="failed", completed_at=time.time(), wall_seconds=round(time.monotonic()-started, 6),
                               error="Procedure stopped before a complete report receipt was saved. Inspect any partial files; use a new request ID.")
                try:
                    with self.database() as db:
                        db.execute("UPDATE procedure_runs SET status='failed',completed=?,receipt=? WHERE request_id=? AND status='running'", (receipt["completed_at"], _json(receipt).decode(), request_id))
                except (OSError, sqlite3.Error):
                    pass  # The prior durable 'running' marker still fails closed.
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise ValueError("Fixed procedure failed: " + str(error)[:300] + ". Inspect history and any partial files before using a new request ID.") from error
