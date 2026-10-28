"""Fixed internal analytics worker. No caller SQL, filenames, plugins or code."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path

# -I excludes both the working directory and PYTHONPATH. Only this application's
# trusted code directory is added, never a path supplied in the request.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics import (CPU_SECONDS, DUCKDB_MEMORY, MAX_CELLS, MAX_CELL_CHARS,
                       MAX_COLUMNS, MAX_INPUT_BYTES, MAX_OUTPUT_BYTES,
                       MAX_REQUEST_BYTES, MAX_ROWS, MEMORY_BYTES, VERSION,
                       column_names, decimal_text, decimal_value, validate_query)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON tables cannot contain duplicate object keys.")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite JSON values are unsupported.")


def _bounded_json_depth(text):
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 4:
                raise ValueError("JSON analytics tables must contain flat object rows, without nesting.")
        elif char in "]}":
            depth -= 1


def _cell(value):
    if value is None:
        return None
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is not str or len(value) > MAX_CELL_CHARS or any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        raise ValueError("Analytics cells must be bounded scalar text/numbers/booleans/null; nested or binary data is unsupported.")
    return value


def parse_table(data, path):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("Analytics tables must use UTF-8 text.") from None
    if any(ord(c) < 32 and c not in "\t\n\r" for c in text):
        raise ValueError("Binary/control-character inputs are unsupported by analytics.")
    extension = Path(path.replace("\\", "/")).suffix.lower()
    if extension == ".json":
        _bounded_json_depth(text)
        try:
            rows = json.loads(text, parse_float=str, parse_int=str,
                              parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, RecursionError):
            raise ValueError("Analytics JSON must be a valid array of flat objects.") from None
        if type(rows) is not list or len(rows) > MAX_ROWS:
            raise ValueError("Analytics JSON must be an array of at most100000 flat object rows.")
        columns = []
        for row in rows:
            if type(row) is not dict:
                raise ValueError("Every analytics JSON row must be an object.")
            for key, value in row.items():
                _cell(value)
                if key not in columns:
                    columns.append(key)
                    if len(columns) > MAX_COLUMNS:
                        raise ValueError("Analytics table exceeds the100-column limit.")
        column_names(columns)
        if len(rows) * len(columns) > MAX_CELLS:
            raise ValueError("Analytics table exceeds the1million-cell limit.")
        values = [[_cell(row.get(column)) for column in columns] for row in rows]
    else:
        csv.field_size_limit(MAX_CELL_CHARS)
        reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t" if extension == ".tsv" else ",", strict=True)
        try:
            columns = column_names(next(reader))
        except StopIteration:
            raise ValueError("CSV/TSV analytics tables need a header row.") from None
        values = []
        try:
            for row in reader:
                if len(values) >= MAX_ROWS or (len(values) + 1) * len(columns) > MAX_CELLS:
                    raise ValueError("Analytics table exceeds its100000-row/1million-cell limit.")
                if len(row) != len(columns):
                    raise ValueError("Delimited analytics rows must match the header width.")
                values.append([_cell(value) for value in row])
        except csv.Error:
            raise ValueError("Malformed CSV/TSV or a cell exceeds the10000-character limit.") from None
    return columns, values


def _quote(generated):
    if not re.fullmatch(r"[tcr][0-9]+|_row|_total", generated):
        raise ValueError("Internal analytics identifier is invalid.")
    return '"' + generated + '"'


def _shape_decimal(values):
    decimals = []
    integer_digits = 1
    scale = 0
    for value in values:
        if value in {None, ""}:
            raise ValueError("Declared numeric columns must have nonblank, non-null cells; clean missing values explicitly first.")
        number = decimal_value(value)
        integer_digits = max(integer_digits, number.adjusted() + 1)
        scale = max(scale, max(0, -number.as_tuple().exponent))
        decimals.append(number)
    precision = integer_digits + scale
    if precision > 38:
        raise ValueError("Mixed numeric magnitudes/scales exceed exact DECIMAL38; use the smaller Python table tool or normalize the source.")
    maximum = 0
    normalized = []
    for number in decimals:
        # scaleb obeys the ambient Decimal precision; using the coefficient and
        # exponent directly avoids rounding while constructing the bound.
        sign, digits, exponent = number.as_tuple()
        coefficient = int("".join(map(str, digits)))
        unscaled = coefficient * 10 ** (exponent + scale)
        maximum = max(maximum, unscaled)
        normalized.append(decimal_text(number))
    return normalized, {"precision": precision, "scale": scale, "max_unscaled": maximum}


def load_inputs(payload):
    if type(payload) is not dict or set(payload) != {"query", "snapshots"}:
        raise ValueError("Invalid analytics worker request.")
    query = validate_query(payload["query"])
    snapshots = payload["snapshots"]
    if type(snapshots) is not list or len(snapshots) != len(query["tables"]):
        raise ValueError("Analytics snapshot count does not match the query.")
    tables = []
    refs = {}
    total_bytes = total_rows = total_cells = 0
    for index, (request, snapshot) in enumerate(zip(query["tables"], snapshots)):
        if type(snapshot) is not dict or set(snapshot) != {"name", "path", "sha256", "bytes", "data"}:
            raise ValueError("Invalid analytics input snapshot.")
        if snapshot["name"] != request["name"] or snapshot["path"] != request["path"] or type(snapshot["data"]) is not str:
            raise ValueError("Analytics snapshot identity mismatch.")
        try:
            data = base64.b64decode(snapshot["data"], validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid analytics snapshot encoding.") from None
        total_bytes += len(data)
        if total_bytes > MAX_INPUT_BYTES or len(data) != snapshot["bytes"] or hashlib.sha256(data).hexdigest() != snapshot["sha256"]:
            raise ValueError("Analytics snapshot exceeds limits or fails its content hash.")
        columns, rows = parse_table(data, request["path"])
        total_rows += len(rows)
        total_cells += len(rows) * len(columns)
        if total_rows > MAX_ROWS or total_cells > MAX_CELLS:
            raise ValueError("Combined analytics inputs exceed100000 rows/1million cells.")
        table = {"name": request["name"], "path": request["path"], "sha256": snapshot["sha256"],
                 "bytes": len(data), "columns": columns, "rows": rows, "sql": _quote("t" + str(index)), "types": {}}
        for ci, column in enumerate(columns):
            reference = request["name"] + "." + column
            numeric = reference in query["numeric_columns"]
            info = {"table": table, "index": ci, "sql": table["sql"] + "." + _quote("c" + str(ci)), "numeric": numeric}
            if numeric:
                normalized, shape = _shape_decimal([row[ci] for row in rows])
                for row, value in zip(rows, normalized):
                    row[ci] = value
                info.update(shape)
                table["types"][column] = f"DECIMAL({shape['precision']},{shape['scale']})"
            else:
                table["types"][column] = "VARCHAR"
            refs[reference] = info
        tables.append(table)
    requested = query["numeric_columns"] + query["group_by"] + query.get("select", [])
    requested += [condition["column"] for condition in query["filters"]]
    if "join" in query:
        requested += [query["join"]["left"], query["join"]["right"]]
    for operation in query["aggregations"]:
        requested += operation["columns"] if operation["op"] == "sum_product" else ([] if operation["column"] == "*" else [operation["column"]])
    if any(reference not in refs for reference in requested):
        raise ValueError("An analytics column reference does not exist in its input table.")
    return query, tables, refs


def _connection():
    import duckdb
    if duckdb.__version__ != VERSION:
        raise ValueError("The analytics worker requires the pinned DuckDB version.")
    config = {"enable_external_access": "false", "allow_unsigned_extensions": "false",
              "allow_community_extensions": "false", "allow_extensions_metadata_mismatch": "false",
              "allow_extension_repositories": "forbidden", "allow_persistent_secrets": "false",
              "autoload_known_extensions": "false", "autoinstall_known_extensions": "false",
              "threads": "1", "async_threads": "1",
              "memory_limit": DUCKDB_MEMORY, "temp_directory": "", "max_temp_directory_size": "0B",
              "max_expression_depth": "128",
              "max_execution_time": "15000", "python_enable_replacements": "false"}
    connection = duckdb.connect(":memory:", config=config)
    connection.execute("SET enable_progress_bar = false")
    connection.execute("SET enable_logging = false")
    connection.execute("SET lock_configuration = true")
    return connection


def _insert(connection, table):
    definitions = [f'{_quote("c" + str(index))} {table["types"][column]}' for index, column in enumerate(table["columns"])]
    connection.execute(f'CREATE TABLE {table["sql"]} ({", ".join(definitions)}, "_row" UBIGINT)')
    columns = len(table["columns"]) + 1
    row_template = "(" + ",".join("?" for _ in range(columns)) + ")"
    # Bounded parameter batches avoid optional pandas/Arrow dependencies and the
    # native CSV/JSON/file parsers; only validated scalar values cross this API.
    batch_size = max(1, min(512, 8192 // columns))
    connection.execute("BEGIN TRANSACTION")
    try:
        for offset in range(0, len(table["rows"]), batch_size):
            batch = table["rows"][offset:offset + batch_size]
            params = []
            for row_index, row in enumerate(batch, offset):
                params.extend(row)
                params.append(row_index)
            connection.execute(f'INSERT INTO {table["sql"]} VALUES ' + ",".join(row_template for _ in batch), params)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def _join(query, tables, refs):
    if len(tables) == 1:
        return tables[0]["sql"]
    join = query["join"]
    left, right = refs[join["left"]], refs[join["right"]]
    if left["numeric"] != right["numeric"]:
        raise ValueError("Join keys must both be text or both be explicitly declared numeric.")
    if left["numeric"]:
        if max(left["precision"] - left["scale"], right["precision"] - right["scale"]) + max(left["scale"], right["scale"]) > 38:
            raise ValueError("Numeric join keys cannot be compared exactly within DECIMAL38.")
    seen = set()
    for row in right["table"]["rows"]:
        key = row[right["index"]]
        if key is None:
            continue  # SQL NULL does not match NULL.
        if right["numeric"]:
            key = decimal_value(key)
        if key in seen:
            raise ValueError("Right-hand join keys must be unique; many-to-many joins are deliberately unavailable.")
        seen.add(key)
    return f'{tables[0]["sql"]} {"INNER" if join["type"] == "inner" else "LEFT"} JOIN {tables[1]["sql"]} ON {left["sql"]} = {right["sql"]}'


def _where(query, refs):
    expressions, params = [], []
    symbols = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    for condition in query["filters"]:
        info = refs[condition["column"]]
        sql = info["sql"]
        value, op = condition["value"], condition["op"]
        if value is None:
            expressions.append(sql + (" IS NULL" if op == "eq" else " IS NOT NULL"))
        elif op == "contains":
            expressions.append(f"contains(lower({sql}), lower(?))")
            params.append(value)
        elif info["numeric"]:
            number = decimal_value(value)
            scale = max(0, -number.as_tuple().exponent)
            if max(info["precision"] - info["scale"], number.adjusted() + 1) + max(info["scale"], scale) > 38:
                raise ValueError("Numeric filter cannot be compared exactly within DECIMAL38.")
            expressions.append(f'{sql} {symbols[op]} CAST(? AS DECIMAL(38,{scale}))')
            params.append(value)
        else:
            expressions.append(f'{sql} {symbols[op]} ?')
            params.append(value)
    return (" WHERE " + " AND ".join(expressions) if expressions else ""), params


def _bounded_aggregate(operation, refs, matched):
    if operation["op"] == "count":
        return
    references = operation["columns"] if operation["op"] == "sum_product" else [operation["column"]]
    scale = sum(refs[reference]["scale"] for reference in references)
    bound = 1
    for reference in references:
        bound *= refs[reference]["max_unscaled"]
    if operation["op"] in {"sum", "sum_product", "avg"}:
        bound *= max(1, matched)
    if scale > 38 or len(str(bound)) > 38:
        raise ValueError("Aggregate may overflow exact DECIMAL38 intermediates; no approximate fallback was used.")


def run_query(payload):
    query, tables, refs = load_inputs(payload)
    connection = _connection()
    try:
        for table in tables:
            _insert(connection, table)
        from_sql = _join(query, tables, refs)
        where_sql, params = _where(query, refs)
        source_sql = " FROM " + from_sql + where_sql
        matched = connection.execute("SELECT COUNT(*)" + source_sql, params).fetchone()[0]
        if matched > MAX_ROWS:
            raise ValueError("Join exceeds the analytics row bound.")
        aggregations = query["aggregations"]
        all_refs = list(refs)
        selected = query["group_by"] if aggregations else query.get("select", all_refs)
        expressions, outputs = [], []

        def add(sql, key, kind="value"):
            index = len(expressions)
            expressions.append(sql + " AS " + _quote("r" + str(index)))
            outputs.append({"key": key, "kind": kind, "index": index, "sql": _quote("r" + str(index))})

        for reference in selected:
            add(refs[reference]["sql"], reference)
        for operation in aggregations:
            kind, alias = operation["op"], operation["as"]
            _bounded_aggregate(operation, refs, matched)
            if kind == "count":
                column = "*" if operation["column"] == "*" else refs[operation["column"]]["sql"]
                add(f"COUNT({column})", alias)
            else:
                references = operation["columns"] if kind == "sum_product" else [operation["column"]]
                # A left lookup may introduce NULLs despite valid input columns.
                # Reject missing numeric factors rather than silently ignoring them.
                if matched:
                    condition = " OR ".join(refs[x]["sql"] + " IS NULL" for x in references)
                    missing = connection.execute("SELECT COUNT(*)" + source_sql + (" AND " if where_sql else " WHERE ") + "(" + condition + ")", params).fetchone()[0]
                    if missing:
                        raise ValueError("A numeric aggregation encountered unmatched/null join values; use an explicit inner join or repair the lookup data.")
                if kind == "sum_product":
                    # DuckDB may retain a narrow physical integer for decimal
                    # multiplication. Widen both factors before multiplying so
                    # validated38-digit intermediates do not overflow INT64.
                    expression = " * ".join(f"CAST({refs[x]['sql']} AS DECIMAL(38,{refs[x]['scale']}))" for x in references)
                else:
                    expression = refs[references[0]]["sql"]
                aggregate = "SUM" if kind in {"sum", "sum_product", "avg"} else kind.upper()
                add(f"{aggregate}({expression})", alias, "average" if kind == "avg" else ("sum" if kind in {"sum", "sum_product"} else "value"))
                if kind == "avg":
                    index = len(expressions)
                    expressions.append(f"COUNT({expression}) AS {_quote('r' + str(index))}")
                    outputs[-1]["count_index"] = index
        base_sql = "SELECT " + ", ".join(expressions) + source_sql
        if aggregations and selected:
            base_sql += " GROUP BY " + ", ".join(refs[x]["sql"] for x in selected)
        output_by_key = {output["key"]: output for output in outputs}
        order_sql = []
        for order in query["order_by"]:
            output = output_by_key.get(order["column"])
            if not output or output["kind"] == "average":
                raise ValueError("Sort by a selected/grouped column or a non-average aggregation alias; exact average ordering is unavailable.")
            order_sql.append(output["sql"] + (" ASC" if order["direction"] == "asc" else " DESC") + " NULLS LAST")
        if aggregations and selected:
            order_sql += [output_by_key[x]["sql"] + " ASC NULLS LAST" for x in selected]
        elif not aggregations:
            # Stable paging even when the caller's requested sort has tied values.
            row_ref = tables[0]["sql"] + '."_row"'
            expressions.append(row_ref + ' AS "_row"')
            base_sql = "SELECT " + ", ".join(expressions) + source_sql
            order_sql.append('"_row" ASC')
        sql = "SELECT *, COUNT(*) OVER() AS \"_total\" FROM (" + base_sql + ") AS result"
        if order_sql:
            sql += " ORDER BY " + ", ".join(order_sql)
        sql += " LIMIT ? OFFSET ?"
        rows = connection.execute(sql, params + [query["limit"], query["offset"]]).fetchall()
        total = rows[0][-1] if rows else (connection.execute("SELECT COUNT(*) FROM (" + base_sql + ") AS result", params).fetchone()[0] if query["offset"] else 0)
        source_metadata = [{"name": t["name"], "path": t["path"], "bytes": t["bytes"], "sha256": t["sha256"],
                            "row_count": len(t["rows"]), "columns": t["types"]} for t in tables]
        result = {"ok": True, "engine": "duckdb", "version": VERSION, "sources": source_metadata,
                  "columns": list(output_by_key), "rows": [], "input_rows": sum(len(t["rows"]) for t in tables),
                  "matched_rows": matched, "total_rows": total, "offset": query["offset"],
                  "returned_rows": 0, "next_offset": None, "truncated": False}
        if any(output["kind"] == "average" for output in outputs):
            result["average_precision"] = 38
            result["average_rounding"] = "half_even_significant_digits"
        for raw in rows:
            row = {}
            for output in outputs:
                value = raw[output["index"]]
                if output["kind"] == "average":
                    count = raw[output["count_index"]]
                    with localcontext() as context:
                        context.prec = 38
                        context.rounding = ROUND_HALF_EVEN
                        value = None if not count else value / Decimal(count)
                elif value is None and output["kind"] == "sum":
                    value = Decimal(0)
                row[output["key"]] = decimal_text(value) if isinstance(value, Decimal) else value
            result["rows"].append(row)
            if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES - 1000:
                result["rows"].pop()
                if not result["rows"]:
                    raise ValueError("One analytics result row/metadata exceeds the output limit; select fewer columns.")
                break
        result["returned_rows"] = len(result["rows"])
        next_offset = query["offset"] + result["returned_rows"]
        result["truncated"] = next_offset < total
        result["next_offset"] = next_offset if result["truncated"] else None
        return result
    finally:
        connection.close()


def main():
    from ariadne.worker import apply_limits
    started = time.monotonic()
    try:
        apply_limits(memory_bytes=MEMORY_BYTES, cpu_seconds=CPU_SECONDS)
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("Analytics worker request exceeds its input bound.")
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        result = run_query(payload)
    except ValueError as error:
        result = {"ok": False, "error": str(error)}
    except (Exception, MemoryError):
        # Native errors can include source values. Never forward their message.
        result = {"ok": False, "error": "Analytics worker failed within its fixed type, memory, CPU or runtime limits; no approximate result was returned."}
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        encoded = b'{"ok":false,"error":"Analytics result exceeded its output bound."}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    if sys.argv[1:]:
        raise SystemExit("This is a fixed internal analytics worker.")
    raise SystemExit(main())
