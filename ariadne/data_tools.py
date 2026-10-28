"""Deterministic, confined business tables without an interpreter or database DSL."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import PurePosixPath
from typing import Any

MAX_CHARS = 100_000
MAX_ROWS = 2_000
MAX_COLUMNS = 100
MAX_CELL_CHARS = 10_000
MAX_OUTPUT_CHARS = 24_000
NAMES = {"table_read", "table_write", "table_query"}


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required,
                           "additionalProperties": False}}}


def _args(args: Any, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(args, dict) or any(not isinstance(key, str) for key in args):
        raise ValueError("Arguments must be a JSON object.")
    if set(args) - allowed or required - set(args):
        raise ValueError("Missing or unsupported table arguments.")
    return args


def _integer(value: Any, low: int, high: int, name: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}.")
    return value


def _columns(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_COLUMNS:
        raise ValueError(f"columns must be a list of at most {MAX_COLUMNS} unique names.")
    if (any(not isinstance(column, str) or not column or len(column) > 100
            or any(ord(char) < 32 for char in column) for column in value)
            or len(set(value)) != len(value)):
        raise ValueError("Column names must be unique, nonempty text without control characters.")
    return value


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Numeric operations require finite numbers or decimal strings.")
    text = str(value).strip()
    if len(text) > 128:
        raise ValueError("Numeric value exceeds the precision limit.")
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise ValueError("Numeric operations require finite numbers or decimal strings.") from None
    if (not result.is_finite() or abs(result) > Decimal("1e100")
            or len(result.as_tuple().digits) > 100 or abs(result.as_tuple().exponent) > 100):
        raise ValueError("Numeric value exceeds the finite precision/range limit.")
    return result


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > MAX_CELL_CHARS or "\x00" in value:
            raise ValueError("Table cell exceeds the text limit or contains a null character.")
        return value
    if isinstance(value, (int, float, Decimal)):
        _decimal(value)
        return value
    raise ValueError("Table cells must be text, finite numbers, booleans, or null; nested values are unsupported.")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _validate_rows(rows: Any, columns: list[str] | None = None) -> tuple[list[dict], list[str]]:
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError(f"A table must contain at most {MAX_ROWS} object rows.")
    inferred = [] if columns is None else list(columns)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Every table row must be an object.")
        _columns(list(row))
        for key, value in row.items():
            _cell(value)
            if columns is not None and key not in columns:
                raise ValueError("A row contains a column absent from the supplied columns.")
            if key not in inferred:
                inferred.append(key)
    _columns(inferred)
    return [{column: row.get(column) for column in inferred} for row in rows], inferred


class DataTools:
    def __init__(self, registry):
        self.registry = registry

    def schemas(self) -> list[dict]:
        path = {"type": "string", "description": "Workspace-relative .csv, .tsv, or .json file."}
        column_list = {"type": "array", "items": {"type": "string"}, "maxItems": MAX_COLUMNS}
        return [
            _schema("table_read", "Read a bounded CSV/TSV or JSON array of flat objects. JSON decimal values are returned as exact strings.",
                    {"path": path, "offset": {"type": "integer", "minimum": 0, "maximum": MAX_ROWS},
                     "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["path"]),
            _schema("table_write", "Atomically create/replace a workspace table. CSV/TSV formula-like text is prefixed with an apostrophe; escaped_cells reports the count. Numbers remain numeric.",
                    {"path": path, "rows": {"type": "array", "items": {"type": "object"}, "maxItems": MAX_ROWS},
                     "columns": column_list, "overwrite": {"type": "boolean", "default": False}}, ["path", "rows"]),
            _schema("table_query", "Filter/select local table rows or group and aggregate numeric columns. For weighted totals use sum_product with columns:[quantity,unit_cost]: multiply both numeric factors for each row, then sum. Blank/null factors are rejected. Aggregates use finite Decimal arithmetic and return exact decimal strings (average rounded to 40 significant digits). Filters combine with AND.",
                    {"path": path, "select": column_list, "group_by": column_list,
                     "filters": {"type": "array", "maxItems": 20, "items": {"type": "object", "properties": {
                         "column": {"type": "string"}, "op": {"type": "string", "enum": ["eq", "ne", "contains", "gt", "gte", "lt", "lte"]},
                         "value": {}}, "required": ["column", "op", "value"], "additionalProperties": False}},
                     "aggregations": {"type": "array", "maxItems": 20, "items": {"type": "object", "properties": {
                         "column": {"type": "string", "description": "Required for count/sum/avg/min/max; use * with count to count all rows. Omit for sum_product."},
                         "columns": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2,
                                     "description": "Exactly two existing numeric-factor columns; required only for sum_product. Example: [quantity,unit_cost]."},
                         "op": {"type": "string", "enum": ["count", "sum", "avg", "min", "max", "sum_product"]},
                         "as": {"type": "string"}}, "required": ["op", "as"], "additionalProperties": False}},
                     "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["path"]),
        ]

    def policy(self, name: str, args: dict) -> str:
        if name not in NAMES:
            raise ValueError("Unknown data tool.")
        return "auto"

    def execute(self, name: str, args: dict) -> dict:
        try:
            handler = {"table_read": self._read, "table_write": self._write, "table_query": self._query}.get(name)
            if handler is None:
                raise ValueError("Unknown data tool.")
            return handler(args)
        except (ValueError, TypeError, csv.Error, InvalidOperation, OverflowError, RecursionError):
            # Return only our validation messages, never arbitrary filesystem
            # exceptions or a row's potentially sensitive content.
            import sys
            error = sys.exc_info()[1]
            message = str(error) if type(error) is ValueError else "Invalid or unsupported table data."
            return {"ok": False, "error": message}

    @staticmethod
    def _format(path: Any) -> str:
        if not isinstance(path, str) or len(path) > 1024 or not path:
            raise ValueError("path must be a workspace-relative table filename.")
        extension = PurePosixPath(path.replace("\\", "/")).suffix.lower()
        if extension not in {".csv", ".tsv", ".json"}:
            raise ValueError("Tables support only .csv, .tsv, and .json files.")
        return extension[1:]

    def _load(self, path: str) -> tuple[list[dict], list[str], str]:
        format_name = self._format(path)
        result = self.registry.execute("file_read", {"path": path, "max_chars": MAX_CHARS})
        if not result.get("ok"):
            raise ValueError(result.get("error", "Cannot read the workspace table."))
        if result.get("truncated"):
            raise ValueError(f"Table exceeds the {MAX_CHARS}-character limit.")
        text = result["content"].removeprefix("\ufeff")
        if format_name == "json":
            try:
                rows = json.loads(text, parse_float=Decimal,
                                  parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON numbers are unsupported.")))
            except (json.JSONDecodeError, RecursionError):
                raise ValueError("Table must contain a valid JSON array of flat objects.") from None
            rows, columns = _validate_rows(rows)
        else:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t" if format_name == "tsv" else ",", strict=True)
            try:
                columns = _columns(next(reader))
            except StopIteration:
                raise ValueError("Delimited tables must include a header row.") from None
            rows = []
            for values in reader:
                if len(rows) >= MAX_ROWS:
                    raise ValueError(f"Table exceeds the {MAX_ROWS}-row limit.")
                if len(values) != len(columns):
                    raise ValueError("Delimited rows must have the same number of cells as the header.")
                rows.append(dict(zip(columns, map(_cell, values))))
        return rows, columns, format_name

    @staticmethod
    def _page(rows: list[dict], columns: list[str], offset: int, limit: int) -> dict:
        output = []
        characters = 0
        for row in rows[offset:offset + limit]:
            safe = _json_safe(row)
            size = len(json.dumps(safe, ensure_ascii=False))
            if not output and size > MAX_OUTPUT_CHARS:
                raise ValueError("A table row exceeds the output limit; use table_query with select to request fewer columns.")
            if characters + size > MAX_OUTPUT_CHARS:
                break
            output.append(safe)
            characters += size
        return {"columns": columns, "rows": output, "total_rows": len(rows), "offset": offset,
                "returned_rows": len(output), "next_offset": offset + len(output) if offset + len(output) < len(rows) else None,
                "truncated": offset + len(output) < len(rows)}

    def _read(self, args: dict) -> dict:
        args = _args(args, {"path", "offset", "limit"}, {"path"})
        offset = _integer(args.get("offset", 0), 0, MAX_ROWS, "offset")
        limit = _integer(args.get("limit", 100), 1, 200, "limit")
        rows, columns, format_name = self._load(args["path"])
        return {"ok": True, "path": args["path"], "format": format_name, **self._page(rows, columns, offset, limit)}

    def _write(self, args: dict) -> dict:
        args = _args(args, {"path", "rows", "columns", "overwrite"}, {"path", "rows"})
        format_name = self._format(args["path"])
        columns = _columns(args["columns"]) if "columns" in args else None
        rows, columns = _validate_rows(args["rows"], columns)
        overwrite = args.get("overwrite", False)
        if type(overwrite) is not bool:
            raise ValueError("overwrite must be a boolean.")
        escaped_cells = 0
        if format_name == "json":
            content = json.dumps(_json_safe(rows), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        else:
            if not columns:
                raise ValueError("CSV/TSV tables require at least one column.")

            def escape(value: Any) -> Any:
                nonlocal escaped_cells
                if isinstance(value, str) and (value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
                    escaped_cells += 1
                    return "'" + value
                if value is None:
                    return ""
                return value

            output = io.StringIO(newline="")
            writer = csv.writer(output, delimiter="\t" if format_name == "tsv" else ",", lineterminator="\n")
            writer.writerow([escape(column) for column in columns])
            for row in rows:
                writer.writerow([escape(row[column]) for column in columns])
            content = output.getvalue()
        if len(content) > MAX_CHARS:
            raise ValueError(f"Serialized table exceeds the {MAX_CHARS}-character limit.")
        result = self.registry.execute("file_write", {"path": args["path"], "content": content, "overwrite": overwrite})
        if not result.get("ok"):
            return result
        encoded = content.encode("utf-8")
        artifact = {"path": result["path"], "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(),
                    "format": format_name, "row_count": len(rows), "columns": columns}
        return {"ok": True, "artifact": artifact, "escaped_cells": escaped_cells}

    def _query(self, args: dict) -> dict:
        args = _args(args, {"path", "filters", "select", "aggregations", "group_by", "limit"}, {"path"})
        rows, columns, format_name = self._load(args["path"])
        limit = _integer(args.get("limit", 100), 1, 200, "limit")
        selected = _columns(args.get("select", columns))
        grouped = _columns(args.get("group_by", []))
        if any(column not in columns for column in selected + grouped):
            raise ValueError("Selected/grouped column does not exist in the table.")
        filters = args.get("filters", [])
        if not isinstance(filters, list) or len(filters) > 20:
            raise ValueError("filters must contain at most 20 conditions.")
        for condition in filters:
            _args(condition, {"column", "op", "value"}, {"column", "op", "value"})
            if condition["column"] not in columns or condition["op"] not in {"eq", "ne", "contains", "gt", "gte", "lt", "lte"}:
                raise ValueError("Filter column/operator is unsupported.")
            _cell(condition["value"])

        def matches(row: dict) -> bool:
            for condition in filters:
                left, right, operator = row[condition["column"]], condition["value"], condition["op"]
                if operator in {"eq", "ne"}:
                    if type(right) in {int, float, Decimal} and left not in {None, ""}:
                        same = _decimal(left) == _decimal(right)
                    else:
                        same = type(left) is type(right) and left == right
                    if same != (operator == "eq"):
                        return False
                elif operator == "contains":
                    if not isinstance(left, str) or not isinstance(right, str) or right.casefold() not in left.casefold():
                        return False
                else:
                    if left is None or left == "":
                        return False
                    one, two = _decimal(left), _decimal(right)
                    if not {"gt": one > two, "gte": one >= two, "lt": one < two, "lte": one <= two}[operator]:
                        return False
            return True

        filtered = [row for row in rows if matches(row)]
        aggregations = args.get("aggregations", [])
        if not isinstance(aggregations, list) or len(aggregations) > 20:
            raise ValueError("aggregations must contain at most 20 operations.")
        aliases = list(grouped)
        for operation in aggregations:
            _args(operation, {"column", "columns", "op", "as"}, {"op", "as"})
            if operation["op"] == "sum_product":
                _args(operation, {"columns", "op", "as"}, {"columns", "op", "as"})
                factors = operation["columns"]
                if (not isinstance(factors, list) or len(factors) != 2
                        or any(not isinstance(column, str) or column not in columns for column in factors)):
                    raise ValueError("sum_product requires exactly two existing numeric-factor columns.")
            else:
                _args(operation, {"column", "op", "as"}, {"column", "op", "as"})
                if operation["op"] not in {"count", "sum", "avg", "min", "max"}:
                    raise ValueError("Unsupported aggregation operator.")
                if operation["column"] not in columns and not (operation["op"] == "count" and operation["column"] == "*"):
                    raise ValueError("Aggregation column does not exist.")
            aliases.append(operation["as"])
        _columns(aliases)
        if grouped and not aggregations:
            raise ValueError("group_by requires aggregations.")
        if aggregations:
            if "select" in args:
                raise ValueError("select cannot be combined with aggregations; use group_by and aliases.")
            groups: dict[tuple, list[dict]] = {}
            for row in filtered:
                # Include type in the key: JSON true and integer 1 are distinct.
                key = tuple((type(row[column]).__name__, row[column]) for column in grouped)
                groups.setdefault(key, []).append(row)
            if not grouped and not groups:
                groups[()] = []
            result_rows = []
            with localcontext() as context:
                # Two bounded factors span 1e200 through 1e-200. Keep all
                # significant places plus row-count carry through cancellation.
                context.prec = 512
                for members in groups.values():
                    result = {column: members[0][column] for column in grouped}
                    for operation in aggregations:
                        operator = operation["op"]
                        if operator == "sum_product":
                            first, second = operation["columns"]
                            value = sum((_decimal(row[first]) * _decimal(row[second]) for row in members), Decimal(0))
                            result[operation["as"]] = _decimal_text(value)
                            continue
                        column = operation["column"]
                        values = [row[column] for row in members if column != "*" and row[column] is not None and row[column] != ""]
                        if operator == "count":
                            value = len(members) if column == "*" else len(values)
                        else:
                            numbers = [_decimal(value) for value in values]
                            if operator == "sum": value = sum(numbers, Decimal(0))
                            elif not numbers: value = None
                            elif operator == "avg":
                                total = sum(numbers, Decimal(0))
                                with localcontext() as average_context:
                                    average_context.prec = 40
                                    value = total / len(numbers)
                            elif operator == "min": value = min(numbers)
                            else: value = max(numbers)
                            if isinstance(value, Decimal):
                                value = _decimal_text(value)
                        result[operation["as"]] = value
                    result_rows.append(result)
            output_rows, output_columns = result_rows, aliases
        else:
            output_rows, output_columns = [{column: row[column] for column in selected} for row in filtered], selected
        return {"ok": True, "path": args["path"], "format": format_name, "matched_rows": len(filtered),
                **self._page(output_rows, output_columns, 0, limit)}
