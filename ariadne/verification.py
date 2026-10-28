"""Owner-authored bounded file predicates, independent of model claims."""
import csv
import hashlib
import io
import json
import os
import re
from decimal import Decimal, InvalidOperation


KINDS = {"file_exists", "file_contains", "json_valid", "json_number", "csv_columns"}


def number(value):
    """Compare exact finite decimals; never evaluate an expression or coerce a bool."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Expected a finite decimal number")
    raw = str(value)
    if len(raw) > 600 or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", raw):
        raise ValueError("Expected a decimal number in JSON numeric notation")
    try:
        result = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Expected a finite decimal number") from exc
    if not result.is_finite() or len(result.as_tuple().digits) > 200 or abs(result.as_tuple().exponent) > 400 or abs(result.adjusted()) > 400:
        raise ValueError("Numeric check exceeds the 200 digit / exponent 400 limit")
    return result


def pointer_parts(pointer):
    if not isinstance(pointer, str) or len(pointer) > 500 or (pointer and not pointer.startswith("/")):
        raise ValueError("pointer must be an RFC 6901 JSON pointer of at most 500 characters")
    parts = pointer.split("/")[1:] if pointer else []
    if len(parts) > 32 or any(re.search(r"~(?![01])", part) for part in parts):
        raise ValueError("pointer must use valid escapes and at most 32 segments")
    return [part.replace("~1", "/").replace("~0", "~") for part in parts]


def select_number(document, pointer):
    value = document
    for part in pointer_parts(pointer):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part) and len(part) <= 8 and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError("JSON pointer does not identify an existing numeric value")
    return number(value)


def validate_checks(checks, registry):
    if not isinstance(checks, list) or len(checks) > 10:
        raise ValueError("checks must be a list of at most 10 file expectations")
    clean = []
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") not in KINDS:
            raise ValueError("Unsupported file check")
        kind = check["kind"]
        allowed = {"kind", "path"} | ({"text"} if kind == "file_contains" else {"columns"} if kind == "csv_columns" else {"pointer", "value"} if kind == "json_number" else set())
        if set(check) != allowed:
            raise ValueError("Invalid file check fields")
        registry._path(check.get("path"))
        if kind == "file_contains" and (not isinstance(check["text"], str) or not check["text"] or len(check["text"]) > 4000):
            raise ValueError("file_contains needs nonempty text of at most 4000 characters")
        if kind == "csv_columns":
            columns = check["columns"]
            if not isinstance(columns, list) or not 1 <= len(columns) <= 100 or any(not isinstance(c, str) or not c or len(c) > 200 for c in columns) or len(set(columns)) != len(columns):
                raise ValueError("csv_columns needs 1 to 100 unique column names")
        if kind == "json_number":
            pointer_parts(check["pointer"])
            if type(check["value"]) not in {str, int}:
                raise ValueError("Use a quoted decimal string or integer for an exact numeric expectation")
            number(check["value"])
        clean.append({**check, "path": check["path"].replace("\\", "/")})
        if kind == "json_number":
            clean[-1]["value"] = str(number(check["value"]))
    return clean


def reject_constant(value):
    raise ValueError("Nonfinite JSON values are not supported")


def unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate JSON keys are ambiguous")
        out[key] = value
    return out


def verify(checks, registry):
    if not checks:
        return {"status": "not_requested", "checks": [], "artifacts": []}
    results, artifacts, cache = [], {}, {}
    for check in checks:
        result = {"kind": check["kind"], "path": check["path"], "status": "failed"}
        try:
            relative = check["path"]
            if relative not in cache:
                path = registry._path(relative)
                with registry._directory(path.parent) as directory_fd:
                    with os.fdopen(registry._open_read(path, directory_fd), "rb") as source:
                        data = source.read(10_000_001)
                if len(data) > 10_000_000:
                    raise ValueError("Artifact exceeds the 10 MB verification limit")
                cache[relative] = data
                artifacts[relative] = {"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            data = cache[relative]
            kind = check["kind"]
            if kind == "file_contains" and check["text"] not in data.decode("utf-8-sig"):
                raise ValueError("Expected literal text was not found")
            if kind in {"json_valid", "json_number"}:
                document = json.loads(data.decode("utf-8-sig"), parse_float=number, parse_int=number, parse_constant=reject_constant, object_pairs_hook=unique_pairs)
                if kind == "json_number":
                    expected = number(check["value"])
                    observed = select_number(document, check["pointer"])
                    if observed != expected:
                        result["message"] = f"Numeric expectation failed: expected {expected}, observed {observed}"
                        results.append(result)
                        continue
            if kind == "csv_columns":
                header = next(csv.reader(io.StringIO(data.decode("utf-8-sig")), strict=True), [])
                if len(set(header)) != len(header) or not set(check["columns"]) <= set(header):
                    raise ValueError("Expected CSV columns were not found or the header is ambiguous")
            result.update(status="passed", message="File expectation passed")
        except (ValueError, OSError, UnicodeError, csv.Error, RecursionError):
            result["message"] = "Expected file is missing, unreadable, unsafe, or does not match the required content/structure"
        results.append(result)
    return {"status": "passed" if all(r["status"] == "passed" for r in results) else "failed", "checks": results, "artifacts": list(artifacts.values())}
