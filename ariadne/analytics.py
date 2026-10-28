"""On-demand, read-only analytics over bounded immutable workspace snapshots.

The model supplies a small operation description, never SQL. DuckDB is imported
only by the fixed child worker; the server retains no database or native engine.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
import re
import threading
from pathlib import Path, PurePosixPath

VERSION = "2.0.0"
NAMES = {"analytics_query"}
MAX_INPUT_BYTES = 10_000_000
MAX_ROWS = 100_000
MAX_COLUMNS = 100
MAX_CELLS = 1_000_000
MAX_CELL_CHARS = 10_000
MAX_OUTPUT_BYTES = 60_000
MEMORY_BYTES = 768 * 1024 * 1024
DUCKDB_MEMORY = "128MiB"
CPU_SECONDS = 20
WALL_SECONDS = 30
MAX_REQUEST_BYTES = 13_500_000
MAX_JOIN_ROWS = MAX_ROWS
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


def _object(value, allowed, required=()):
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError("Analytics arguments must be an object.")
    if set(value) - set(allowed) or set(required) - set(value):
        raise ValueError("Missing or unsupported analytics arguments; SQL and expressions are not accepted.")
    return value


def _name(value, label="name"):
    if type(value) is not str or not _NAME.fullmatch(value):
        raise ValueError(f"{label} must use 1–64 ASCII letters, digits or underscores, beginning with a letter or underscore.")
    return value


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer between {low} and {high}.")
    return value


def _text(value, label, maximum=MAX_CELL_CHARS):
    if type(value) is not str or len(value) > maximum or any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        raise ValueError(f"{label} must be text within the analytics length/control-character limits.")
    return value


def column_names(value):
    if (type(value) is not list or not 1 <= len(value) <= MAX_COLUMNS
            or any(type(c) is not str or not c or len(c) > 100 or any(ord(x) < 32 for x in c) for c in value)
            or len({c.casefold() for c in value}) != len(value)):
        raise ValueError("Each table needs 1–100 unique, nonempty column names of at most 100 characters, without controls.")
    return value


def _ref(value, table_names):
    _text(value, "column reference", 165)
    table, separator, column = value.partition(".")
    if not separator or table not in table_names or not column or len(column) > 100 or any(ord(c) < 32 for c in column):
        raise ValueError("Column references must be table_name.column_name using a declared table.")
    return value


def _refs(value, names, label, maximum=MAX_COLUMNS * 2):
    if type(value) is not list or len(value) > maximum or len(set(x for x in value if type(x) is str)) != len(value):
        raise ValueError(f"{label} must be a bounded list of unique column references.")
    return [_ref(x, names) for x in value]


def decimal_value(value):
    """Parse exact decimals; never receive a binary float, including expectations."""
    from decimal import Decimal, InvalidOperation
    if type(value) not in {str, int} or (isinstance(value, str) and len(value) > 128):
        raise ValueError("Numeric analytics values must be decimal strings or integers, never floats or booleans.")
    raw = str(value)
    if not _DECIMAL.fullmatch(raw):
        raise ValueError("Numeric analytics values must be finite decimal strings or integers.")
    try:
        result = Decimal(raw)
    except InvalidOperation:
        raise ValueError("Invalid analytics decimal.") from None
    digits = result.as_tuple().digits
    exponent = result.as_tuple().exponent
    # This is intentionally narrower than the small Python table tool: DuckDB
    # fixed-point arithmetic has a maximum precision of 38, with no float fallback.
    if (not result.is_finite() or len(digits) > 38 or abs(exponent) > 38
            or result.adjusted() > 37 or result.adjusted() < -38):
        raise ValueError("Analytics decimal exceeds the supported 38-digit precision/range.")
    return result


def decimal_text(value):
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def validate_query(args):
    """Pure request validation, repeated in the fixed worker before native calls."""
    args = _object(args, {"tables", "numeric_columns", "join", "select", "filters", "group_by", "aggregations", "order_by", "limit", "offset"}, {"tables"})
    tables = args["tables"]
    if type(tables) is not list or not 1 <= len(tables) <= 2:
        raise ValueError("Analytics accepts one table or two tables with a single equijoin.")
    names = []
    clean_tables = []
    for table in tables:
        _object(table, {"name", "path"}, {"name", "path"})
        name = _name(table["name"], "table name")
        if name.casefold() in {x.casefold() for x in names}:
            raise ValueError("Table names must be unique, including case.")
        names.append(name)
        path = _text(table["path"], "path", 1024)
        if not path or PurePosixPath(path.replace("\\", "/")).suffix.lower() not in {".csv", ".tsv", ".json"}:
            raise ValueError("Analytics supports workspace-relative CSV, TSV, or flat JSON array files only.")
        clean_tables.append({"name": name, "path": path})
    clean = {"tables": clean_tables,
             "numeric_columns": _refs(args.get("numeric_columns", []), names, "numeric_columns"),
             "group_by": _refs(args.get("group_by", []), names, "group_by", 20),
             "limit": _integer(args.get("limit", 100), 1, 200, "limit"),
             "offset": _integer(args.get("offset", 0), 0, MAX_ROWS, "offset")}
    if "select" in args:
        clean["select"] = _refs(args["select"], names, "select")
        if not clean["select"]:
            del clean["select"]  # An empty optional projection means no projection request.
    if len(tables) == 2:
        join = _object(args.get("join"), {"left", "right", "type"}, {"left", "right"})
        left, right = _ref(join["left"], names), _ref(join["right"], names)
        kind = join.get("type", "inner")
        if kind not in {"inner", "left"} or left.partition(".")[0] != names[0] or right.partition(".")[0] != names[1]:
            raise ValueError("Use an inner/left equijoin from the first table to the second; right keys must be unique.")
        clean["join"] = {"left": left, "right": right, "type": kind}
    elif "join" in args:
        raise ValueError("A join requires exactly two tables.")
    filters = args.get("filters", [])
    if type(filters) is not list or len(filters) > 20:
        raise ValueError("filters must contain at most 20 conditions combined with AND.")
    clean["filters"] = []
    for condition in filters:
        _object(condition, {"column", "op", "value"}, {"column", "op", "value"})
        reference = _ref(condition["column"], names)
        operator, value = condition["op"], condition["value"]
        if type(operator) is not str or operator not in {"eq", "ne", "contains", "gt", "gte", "lt", "lte"}:
            raise ValueError("Unsupported analytics filter operator.")
        numeric = reference in clean["numeric_columns"]
        if value is None:
            if operator not in {"eq", "ne"}:
                raise ValueError("Null filters support only eq and ne.")
        elif numeric:
            if operator == "contains":
                raise ValueError("contains is available only for text columns.")
            value = decimal_text(decimal_value(value))
        else:
            if operator not in {"eq", "ne", "contains"}:
                raise ValueError("Ordered comparisons require an explicitly declared numeric column.")
            _text(value, "filter value")
        clean["filters"].append({"column": reference, "op": operator, "value": value})
    aggregations = args.get("aggregations", [])
    if type(aggregations) is not list or len(aggregations) > 20:
        raise ValueError("aggregations must contain at most 20 fixed operations.")
    clean["aggregations"] = []
    aliases = set(clean["group_by"])
    for operation in aggregations:
        _object(operation, {"op", "column", "columns", "as"}, {"op", "as"})
        kind = operation["op"]
        if type(kind) is not str or kind not in {"count", "sum", "avg", "min", "max", "sum_product"}:
            raise ValueError("Unsupported analytics aggregation.")
        alias = _name(operation["as"], "aggregation alias")
        if alias in aliases:
            raise ValueError("Aggregation output names must be unique.")
        aliases.add(alias)
        if kind == "sum_product":
            _object(operation, {"op", "columns", "as"}, {"op", "columns", "as"})
            factors = operation["columns"]
            if type(factors) is not list or len(factors) != 2:
                raise ValueError("sum_product requires exactly two numeric column references.")
            references = [_ref(x, names) for x in factors]
            if any(x not in clean["numeric_columns"] for x in references):
                raise ValueError("Declare both sum_product factors in numeric_columns.")
            clean["aggregations"].append({"op": kind, "columns": references, "as": alias})
        else:
            _object(operation, {"op", "column", "as"}, {"op", "column", "as"})
            reference = operation["column"]
            if not (kind == "count" and reference == "*"):
                _ref(reference, names)
                if kind != "count" and reference not in clean["numeric_columns"]:
                    raise ValueError("Numeric aggregations require a column declared in numeric_columns.")
            clean["aggregations"].append({"op": kind, "column": reference, "as": alias})
    if aggregations and "select" in clean:
        raise ValueError("Use group_by and aggregation aliases instead of select with aggregations.")
    if clean["group_by"] and not aggregations:
        raise ValueError("group_by requires an aggregation.")
    order_by = args.get("order_by", [])
    if type(order_by) is not list or len(order_by) > 5:
        raise ValueError("order_by supports at most five result columns.")
    clean["order_by"] = []
    for order in order_by:
        _object(order, {"column", "direction"}, {"column"})
        column = _text(order["column"], "order column", 165)
        direction = order.get("direction", "asc")
        if type(direction) is not str or direction not in {"asc", "desc"}:
            raise ValueError("Sort direction must be asc or desc.")
        clean["order_by"].append({"column": column, "direction": direction})
    return clean


class AnalyticsTools:
    def __init__(self, registry):
        self.registry = registry
        self._gate = threading.BoundedSemaphore(1)

    def status(self):
        try:
            version = importlib.metadata.version("duckdb")
        except importlib.metadata.PackageNotFoundError:
            version = None
        ready = version == VERSION and os.name in {"nt", "posix"}
        return {"ready": ready, "version": version, "required_version": VERSION,
                "limits": {"input_bytes": MAX_INPUT_BYTES, "rows": MAX_ROWS, "columns_per_table": MAX_COLUMNS,
                           "cells": MAX_CELLS, "tables": 2, "returned_rows": 200, "output_bytes": MAX_OUTPUT_BYTES,
                           "worker_memory_bytes": MEMORY_BYTES, "duckdb_memory": DUCKDB_MEMORY,
                           "cpu_seconds": CPU_SECONDS, "wall_seconds": WALL_SECONDS},
                "details": "Read-only fixed analytics in an on-demand worker; no SQL, native file readers, extensions, or persistent database."
                           if ready else "Install the pinned optional analytics wheel in Ariadne's environment. No server/model restart loop or background database is required."}

    def schemas(self):
        reference = {"type": "string", "description": "Declared table name, a dot, then the exact column header; e.g. sales.quantity."}
        references = {"type": "array", "items": reference, "maxItems": MAX_COLUMNS * 2}
        properties = {
            "tables": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "object", "properties": {
                "name": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,63}$"},
                "path": {"type": "string", "description": "Workspace-relative CSV/TSV or JSON array of flat objects."}}, "required": ["name", "path"], "additionalProperties": False}},
            "numeric_columns": {**references, "description": "Explicitly opt numeric columns into exact DECIMAL (max38 digits); every input cell must be nonblank/non-null numeric. Other columns stay text, preserving IDs/leading zeros. Use decimal strings for exact filter values."},
            "join": {"type": "object", "properties": {"left": reference, "right": reference, "type": {"type": "string", "enum": ["inner", "left"]}}, "required": ["left", "right"], "additionalProperties": False, "description": "Optional single equijoin from first to second table. Right-hand key must be unique; no cross/many-to-many joins."},
            "select": {**references, "description": "Optional projection for non-aggregate queries. Omit select (or use []) with aggregations; use group_by for grouped columns."}, "group_by": {**references, "maxItems": 20},
            "filters": {"type": "array", "maxItems": 20, "items": {"type": "object", "properties": {
                "column": reference, "op": {"type": "string", "enum": ["eq", "ne", "contains", "gt", "gte", "lt", "lte"]},
                "value": {"description": "Text/null for text columns; exact decimal string or integer for numeric columns. Conditions combine with AND."}}, "required": ["column", "op", "value"], "additionalProperties": False}},
            "aggregations": {"type": "array", "maxItems": 20, "items": {"type": "object", "properties": {
                "op": {"type": "string", "enum": ["count", "sum", "avg", "min", "max", "sum_product"]},
                "column": {**reference, "description": "Required except sum_product; count also accepts * to count all rows."},
                "columns": {"type": "array", "minItems": 2, "maxItems": 2, "items": reference, "description": "For sum_product only: [quantity,unit_cost] qualified references."},
                "as": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,63}$"}}, "required": ["op", "as"], "additionalProperties": False}},
            "order_by": {"type": "array", "maxItems": 5, "items": {"type": "object", "properties": {
                "column": {"type": "string", "description": "A selected/grouped column reference or aggregation alias."},
                "direction": {"type": "string", "enum": ["asc", "desc"]}}, "required": ["column"], "additionalProperties": False}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "offset": {"type": "integer", "minimum": 0, "maximum": MAX_ROWS},
        }
        return [{"type": "function", "function": {"name": "analytics_query", "description":
            "Read-only analytics for larger local tables: combined10MB/100000 rows/1million cells. Fixed filters, one unique-key inner/left join, grouped exact sum/min/max/count and sum_product weighted totals. Numeric values/results use exact decimal strings; unsupported precision/overflow fails. avg uses decimal sum/count rounded to38 significant digits. No SQL, file writes, URIs, extensions or executable expressions. Results include input snapshot hashes and bounded pagination.",
            "parameters": {"type": "object", "properties": properties, "required": ["tables"], "additionalProperties": False}}}]

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown analytics tool.")
        return "auto"

    def _snapshot(self, table, remaining):
        path = self.registry._path(table["path"])
        with self.registry._lock, self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path, directory), "rb") as stream:
                before = os.fstat(stream.fileno())
                if before.st_size > remaining:
                    raise ValueError("Analytics inputs exceed the combined10MB limit.")
                data = stream.read(remaining + 1)
                after = os.fstat(stream.fileno())
                if len(data) > remaining:
                    raise ValueError("Analytics inputs exceed the combined10MB limit.")
                if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                    raise ValueError("An analytics input changed during snapshot reading; retry after it is stable.")
        return {"name": table["name"], "path": table["path"], "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data), "data": base64.b64encode(data).decode("ascii")}

    def execute(self, name, args, *, cancelled=None):
        try:
            if name not in NAMES:
                raise ValueError("Unknown analytics tool.")
            query = validate_query(args)
            if not self.status()["ready"]:
                raise ValueError("Pinned optional DuckDB analytics is unavailable; install requirements-analytics.txt in this environment.")
            if cancelled and cancelled():
                raise ValueError("Analytics operation cancelled.")
            if not self._gate.acquire(blocking=False):
                raise ValueError("An analytics worker is already running; retry when it finishes.")
            try:
                snapshots = []
                remaining = MAX_INPUT_BYTES
                for table in query["tables"]:
                    snapshot = self._snapshot(table, remaining)
                    remaining -= snapshot["bytes"]
                    snapshots.append(snapshot)
                payload = json.dumps({"query": query, "snapshots": snapshots}, ensure_ascii=False, allow_nan=False).encode("utf-8")
                if len(payload) > MAX_REQUEST_BYTES:
                    raise ValueError("Analytics worker request exceeds its input limit.")
                return self._run(payload, cancelled=cancelled)
            finally:
                self._gate.release()
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        except (OSError, TypeError, RecursionError, OverflowError):
            return {"ok": False, "error": "Analytics input could not be read safely or the bounded worker could not complete."}

    def _run(self, payload, *, cancelled=None):
        from .worker import run_worker
        output = run_worker(Path(__file__).with_name("_analytics_worker.py"), payload,
                            timeout=WALL_SECONDS, memory_bytes=MEMORY_BYTES, cpu_seconds=CPU_SECONDS,
                            max_output_bytes=MAX_OUTPUT_BYTES, max_input_bytes=MAX_REQUEST_BYTES,
                            cancelled=cancelled)
        try:
            result = json.loads(output)
        except (ValueError, UnicodeError):
            raise ValueError("Analytics worker returned an invalid result.") from None
        if type(result) is not dict or type(result.get("ok")) is not bool:
            raise ValueError("Analytics worker returned an invalid result.")
        if result["ok"] and (type(result.get("rows")) is not list or len(result["rows"]) > 200
                             or result.get("version") != VERSION):
            raise ValueError("Analytics worker returned an invalid or oversized result.")
        return result
