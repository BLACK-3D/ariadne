"""Bounded, lossless structural review of verified local routine JSON reports.

No source registration, network, model, process, report publication or cache is
involved. Existing routine verification can hold recurrence after an integrity
failure. This compares recorded JSON values, not physical conditions or quality.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MAX_ARTIFACT_BYTES = 4_000_000
MAX_VISITED_NODES = 20_000
MAX_DIFFERENCES = 100
MAX_PREVIEW_CHARS = 160
MAX_RESULT_BYTES = 12_000
MAX_JSON_DEPTH = 128
PRIMARY_ARTIFACTS = {
    "readiness_report": "readiness.json", "stock_rotation": "stock-rotation.json",
    "power_budget": "power-budget.json", "rebuilding_plan": "rebuilding-plan.json",
    "reference_audit": "reference-audit.json", "business_snapshot": "business-snapshot.json",
    "knowledge_inventory": "knowledge-inventory.json", "offline_checklist": "offline-checklist.json",
}
# Exact known/report-generation paths only. Schema, revision, expiry, source
# observation dates and arbitrary properties named "date" are substantive.
METADATA_POINTERS = frozenset({"/as_of", "/summary/as_of", "/generated_at_utc", "/generated_at", "/generated_on"})
_MISSING = object()


def _encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        key.encode("utf-8")  # Reject unpaired JSON Unicode escapes in property names.
        if key in value:
            raise ValueError("Duplicate JSON object keys are not comparable.")
        value[key] = item
    return value


def _integer(value):
    if len(value.lstrip("-")) > 512:
        raise ValueError("JSON integer exceeds the bounded comparison precision.")
    return int(value)


def _number(value):
    if len(value) > 512:
        raise ValueError("JSON number exceeds the bounded comparison precision.")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid JSON number.") from None
    if not result.is_finite():
        raise ValueError("Non-finite JSON numbers are not comparable.")
    return result


def _constant(value):
    raise ValueError("Non-finite JSON constants are not supported.")


def parse_json(payload):
    if type(payload) is not bytes or len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("A primary JSON artifact exceeds its 4 MB limit.")
    text = payload.decode("utf-8")
    depth, in_string, escaped = 0, False, False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON nesting exceeds the bounded comparison depth.")
        elif char in "]}":
            depth -= 1
    result = json.loads(text, object_pairs_hook=_pairs,
                        parse_int=_integer, parse_float=_number, parse_constant=_constant)
    # Validate the entire parsed input before comparison can count any changes.
    # JSON accepts escaped lone surrogates; UTF-8 evidence and result previews
    # cannot represent them. Iterator frames avoid a second wide object graph.
    frames = [iter((result,))]
    while frames:
        try:
            value = next(frames[-1])
        except StopIteration:
            frames.pop()
            continue
        if type(value) is str:
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("JSON text contains an unpaired Unicode escape.") from None
        elif type(value) is dict:
            frames.append(iter(value.values()))
        elif type(value) is list:
            frames.append(iter(value))
    return result


def _type(value):
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is Decimal:
        return "number"
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("JSON text contains an unpaired Unicode escape.") from None
        return "string"
    if type(value) is list:
        return "array"
    if type(value) is dict:
        return "object"
    raise ValueError("Unsupported comparison value.")


def _preview(value):
    kind = _type(value)
    if kind == "missing":
        return None, False
    if kind in {"object", "array"}:
        return kind+" ("+str(len(value))+(" keys)" if kind == "object" else " items)"), False
    if kind == "number":
        text = str(value)
    elif kind == "string":
        text = json.dumps(value[:MAX_PREVIEW_CHARS+1], ensure_ascii=False)
    else:
        text = json.dumps(value, ensure_ascii=False)
    cut = len(text) > MAX_PREVIEW_CHARS or kind == "string" and len(value) > MAX_PREVIEW_CHARS
    return text[:MAX_PREVIEW_CHARS], cut


def _is_date(value):
    if value is _MISSING or value is None:
        return True
    if type(value) is not str or not 10 <= len(value) <= 64:
        return False
    try:
        if len(value) == 10:
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _pointer(parent, key):
    return parent+"/"+str(key).replace("~", "~0").replace("/", "~1")


def _children(pointer, left, right):
    if type(left) is dict:
        for key in sorted(left.keys() | right.keys()):
            yield _pointer(pointer, key), left.get(key, _MISSING), right.get(key, _MISSING)
    else:
        for index in range(max(len(left), len(right))):
            yield _pointer(pointer, index), left[index] if index < len(left) else _MISSING, right[index] if index < len(right) else _MISSING


def _walk(left, right, budget):
    changes, metadata, substantive, visited, omitted = [], 0, 0, 0, 0
    frames = [iter([( "", left, right)])]
    stopped = False
    while frames:
        try:
            pointer, before, after = next(frames[-1])
        except StopIteration:
            frames.pop()
            continue
        if budget["visited_nodes"] >= MAX_VISITED_NODES or budget["observed_differences"] >= MAX_DIFFERENCES:
            stopped = True
            break
        budget["visited_nodes"] += 1
        visited += 1
        before_type, after_type = _type(before), _type(after)
        if before_type == after_type and before_type in {"object", "array"}:
            frames.append(iter(_children(pointer, before, after)))
            continue
        if before_type == after_type and before == after:
            continue
        kind = "added" if before is _MISSING else "removed" if after is _MISSING else "type_changed" if before_type != after_type else "changed"
        is_metadata = pointer in METADATA_POINTERS and _is_date(before) and _is_date(after)
        before_preview, before_cut = _preview(before)
        after_preview, after_cut = _preview(after)
        if len(pointer.encode("utf-8")) > MAX_RESULT_BYTES//2:
            # A full RFC6901 path is never silently shortened. Count this
            # observed change but omit a path that cannot fit the response.
            omitted += 1
        else:
            changes.append({"pointer": pointer, "kind": kind, "before_type": before_type, "after_type": after_type,
                            "before": before_preview, "after": after_preview, "metadata": is_metadata,
                            "preview_truncated": before_cut or after_cut})
        budget["observed_differences"] += 1
        metadata += int(is_metadata)
        substantive += int(not is_metadata)
    return {"changes": changes, "visited_nodes": visited, "observed_differences": metadata+substantive,
            "metadata_differences": metadata, "substantive_differences": substantive,
            "omitted_observed_differences": omitted,
            "counts_are_lower_bounds": stopped, "comparison_complete": not stopped,
            "metadata_only": metadata > 0 and substantive == 0 and not stopped,
            "truncated": stopped}


def _primary(run, step):
    filename = PRIMARY_ARTIFACTS.get(step["procedure_id"])
    if filename is None:
        raise ValueError("This fixed procedure has no supported primary JSON report.")
    expected = "procedures/"+step["request_id"]+"/"+filename
    artifacts = step.get("receipt", {}).get("artifacts", [])
    rows = [row for row in artifacts if row.get("path") == expected]
    if len(rows) != 1:
        raise ValueError("The primary JSON artifact is missing from the child receipt.")
    artifact = rows[0]
    return {"parent_request_id": run["id"], "child_request_id": step["request_id"],
            "path": artifact["path"], "sha256": artifact["sha256"], "bytes": artifact["bytes"]}


def _read(service, run, source):
    registry = service.procedures.registry
    path = registry._path(source["path"])
    try:
        with registry._directory(path.parent) as descriptor:
            with os.fdopen(registry._open_read(path, descriptor), "rb") as document:
                payload = document.read(MAX_ARTIFACT_BYTES+1)
        if len(payload) != source["bytes"] or hashlib.sha256(payload).hexdigest() != source["sha256"]:
            raise ValueError("Primary JSON bytes changed after routine verification.")
    except (OSError, ValueError):
        # Reuse the established verification/failure-hold path. A successful
        # second check after an owner race still never adopts the failed read.
        service.verify(run["id"])
        raise
    return parse_json(payload)


def _result(current_id):
    return {"state": "current_unavailable", "current_id": current_id, "baseline_id": None,
            "steps": [], "totals": {"visited_nodes": 0, "observed_differences": None,
                "metadata_differences": None, "substantive_differences": None, "returned_differences": 0,
                "omitted_observed_differences": 0, "counts_are_lower_bounds": False},
            "metadata_only": False, "comparison_complete": False, "warnings": [],
            "limits": {"artifact_bytes": MAX_ARTIFACT_BYTES, "visited_nodes": MAX_VISITED_NODES,
                "observed_differences": MAX_DIFFERENCES, "preview_characters": MAX_PREVIEW_CHARS, "result_bytes": MAX_RESULT_BYTES,
                "json_nesting": MAX_JSON_DEPTH, "number_characters": 512},
            "truncation": {"traversal": False, "response": False, "input_reports_partial": False,
                           "omitted_steps": 0, "omitted_observed_differences": 0},
            "metadata_paths": sorted(METADATA_POINTERS), "model_calls": 0,
            "scope": "Structural comparison of saved verified JSON reports. Values and indices are observations from those snapshots; no current physical state, freshness or safety is inferred."}


def _bounded(result):
    # Every omitted row was already observed and counted. Omitting its preview
    # never silently changes the observed exact/lower-bound totals.
    for step in result["steps"]:
        step["returned_differences"] = len(step.get("changes", []))
        step.setdefault("omitted_observed_differences", 0)
        step["response_truncated"] = bool(step["omitted_observed_differences"])
    result["truncation"]["omitted_observed_differences"] = sum(step["omitted_observed_differences"] for step in result["steps"])
    result["truncation"]["response"] = result["truncation"]["response"] or bool(result["truncation"]["omitted_observed_differences"])
    while True:
        result["totals"]["returned_differences"] = sum(len(step.get("changes", [])) for step in result["steps"])
        result["totals"]["omitted_observed_differences"] = result["truncation"]["omitted_observed_differences"]
        if len(_encode(result)) <= MAX_RESULT_BYTES:
            break
        candidates = [step for step in result["steps"] if step.get("changes")]
        if candidates:
            selected = max(candidates, key=lambda step: len(_encode(step["changes"])))
            selected["changes"].pop()
            selected["returned_differences"] -= 1
            selected["omitted_observed_differences"] += 1
            selected["response_truncated"] = True
            result["truncation"]["omitted_observed_differences"] += 1
        else:
            # Fixed IDs/paths are bounded by the routine schema, but malformed
            # historical metadata still cannot force an oversized API response.
            if result["steps"]:
                removed = result["steps"].pop()
                result["truncation"]["omitted_steps"] += 1
                result["truncation"]["omitted_observed_differences"] += removed.get("returned_differences", 0)
            else:
                result["warnings"] = ["Comparison metadata exceeded the bounded response size."]
                result["current_id"] = str(result["current_id"])[:64]
                result["baseline_id"] = str(result["baseline_id"])[:64] if result["baseline_id"] else None
        result["truncation"]["response"] = True
    if result["truncation"]["response"]:
        result["state"] = "partial"
    return result


def compare_routines(service, current_id, previous_id=None):
    current = service.detail(current_id)
    result = _result(current_id)
    if current["status"] != "completed":
        result["warnings"].append("The selected current invocation is not completed; no comparison was inferred.")
        return _bounded(result)
    try:
        current = service.verify(current_id)
    except (OSError, ValueError, KeyError) as error:
        result["warnings"].append("Current artifact verification failed; inspect the routine's integrity hold. "+str(error)[:250])
        result["integrity_failed"] = True
        return _bounded(result)
    if previous_id is None:
        previous = None
        with service.database() as db:
            rows = db.execute("SELECT * FROM routine_runs WHERE routine_id=? AND status='completed' AND (created_at<? OR (created_at=? AND id<?)) ORDER BY created_at DESC,id DESC",
                (current["routine_id"], current["created_at"], current["created_at"], current["id"])).fetchall()
        for row in rows:
            candidate = service._run(row)
            if candidate["plan"]["steps"] == current["plan"]["steps"]:
                previous = candidate
                break
        if previous is None:
            result["state"] = "first_run"
            result["warnings"].append("No older completed invocation with the same routine and ordered procedure sequence is available.")
            result["steps"] = []
            for step in current["steps"]:
                try:
                    source = _primary(current, step)
                except ValueError:
                    source = None
                    result["warnings"].append("Structural comparison does not support this step's report; its verified execution receipt remains available.")
                result["steps"].append({"procedure_id": step["procedure_id"], "state": "first_run" if source else "unsupported", "current_source": source, "baseline_source": None,
                    "current_status": step["status"], "baseline_status": None,
                    "changes": [], "observed_differences": None, "metadata_only": False})
            return _bounded(result)
    else:
        previous = service.detail(previous_id)
    result["baseline_id"] = previous["id"]
    if previous["status"] != "completed":
        result.update(state="incomparable")
        result["warnings"].append("The requested baseline invocation is not completed.")
        return _bounded(result)
    try:
        previous = service.verify(previous["id"])
    except (OSError, ValueError, KeyError) as error:
        result.update(state="partial", integrity_failed=True)
        result["warnings"].append("Baseline artifact verification failed; inspect its integrity hold. "+str(error)[:250])
        return _bounded(result)
    if (previous["id"] == current["id"] or previous["routine_id"] != current["routine_id"]
            or previous["created_at"] > current["created_at"] or previous["plan"]["steps"] != current["plan"]["steps"]):
        result["state"] = "incomparable"
        result["warnings"].append("Comparison requires a distinct older invocation of the same routine and exact ordered procedure IDs.")
        return _bounded(result)
    budget = {"visited_nodes": 0, "observed_differences": 0}
    metadata, substantive, complete = 0, 0, True
    for current_step, previous_step in zip(current["steps"], previous["steps"]):
        row = {"procedure_id": current_step["procedure_id"], "state": "partial", "changes": [],
               "current_status": current_step["status"], "baseline_status": previous_step["status"],
               "observed_differences": 0, "metadata_differences": 0, "substantive_differences": 0, "visited_nodes": 0,
               "counts_are_lower_bounds": True, "comparison_complete": False, "metadata_only": False}
        try:
            row.update(current_source=_primary(current, current_step), baseline_source=_primary(previous, previous_step))
            if budget["visited_nodes"] >= MAX_VISITED_NODES or budget["observed_differences"] >= MAX_DIFFERENCES:
                row["warning"] = "The shared comparison budget was reached before this step."
                complete = False
                result["truncation"]["traversal"] = True
                result["steps"].append(row)
                continue
            before = _read(service, previous, row["baseline_source"])
            after = _read(service, current, row["current_source"])
            source_partial = type(before) is dict and before.get("partial") is True or type(after) is dict and after.get("partial") is True
            compared = _walk(before, after, budget)
            row.update(compared)
            row["input_reports_partial"] = bool(source_partial)
            if source_partial:
                result["truncation"]["input_reports_partial"] = True
                row["metadata_only"] = False
            row["state"] = "compared" if compared["comparison_complete"] and not source_partial else "partial"
            complete = complete and row["state"] == "compared"
            metadata += row["metadata_differences"]
            substantive += row["substantive_differences"]
            result["truncation"]["traversal"] = result["truncation"]["traversal"] or compared["truncated"]
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
            row["warning"] = "Primary JSON could not be compared: "+str(error)[:250]
            complete = False
        result["steps"].append(row)
    result.update(state="compared" if complete else "partial", comparison_complete=complete,
                  metadata_only=complete and metadata > 0 and substantive == 0)
    result["totals"].update(visited_nodes=budget["visited_nodes"], observed_differences=budget["observed_differences"],
        metadata_differences=metadata, substantive_differences=substantive, counts_are_lower_bounds=not complete)
    return _bounded(result)
