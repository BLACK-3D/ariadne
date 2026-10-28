import csv
import hashlib
import importlib.metadata
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from unittest import mock

from ariadne.analytics import (AnalyticsTools, MAX_INPUT_BYTES, MAX_ROWS,
                               MEMORY_BYTES, VERSION, validate_query)
from ariadne.config import Settings
from ariadne.tools import ToolRegistry


def installed():
    try:
        return importlib.metadata.version("duckdb") == VERSION
    except importlib.metadata.PackageNotFoundError:
        return False


class AnalyticsValidationTests(unittest.TestCase):
    def test_no_sql_expressions_or_open_ended_options(self):
        base = {"tables": [{"name": "s", "path": "sales.csv"}]}
        for extra in [{"sql": "SELECT read_csv('secret')"}, {"query": "DROP TABLE x"}, {"attach": "other.db"}, {"extensions": ["httpfs"]}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_query({**base, **extra})
        for name in ['x";COPY', "http://host", "x.y", "", "a" * 65]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_query({"tables": [{"name": name, "path": "sales.csv"}]})

    def test_raw_float_filter_expectations_rejected(self):
        base = {"tables": [{"name": "s", "path": "sales.csv"}], "numeric_columns": ["s.n"]}
        for value in [0.1, 16.0, True, float("nan"), "NaN", "Infinity", "1e100", " 1", "+1", "1;INSTALL httpfs"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_query({**base, "filters": [{"column": "s.n", "op": "eq", "value": value}]})
        self.assertEqual(validate_query({**base, "filters": [{"column": "s.n", "op": "eq", "value": "0.10000000000000000001"}]})["filters"][0]["value"], "0.10000000000000000001")

    def test_strict_shapes_bounds_and_aggregation_aliases(self):
        base = {"tables": [{"name": "s", "path": "sales.csv"}], "numeric_columns": ["s.n"]}
        invalid = [{"limit": True}, {"limit": 201}, {"offset": -1}, {"numeric_columns": ["s.n", "s.n"]},
                   {"group_by": ["s.n"]}, {"join": {"left": "s.n", "right": "s.n"}},
                   {"aggregations": [{"op": "sum", "column": "s.n", "as": "x);COPY"}]},
                   {"aggregations": [{"op": "sum_product", "columns": ["s.n"], "as": "x"}]},
                   {"aggregations": [{"op": "sum_product", "columns": ["s.n", "s.n"], "expression": "1+1", "as": "x"}]},
                   {"filters": [{"column": "s.n", "op": "contains", "value": "1"}]},
                   {"order_by": [{"column": "s.n", "direction": "asc; COPY"}]},
                   {"tables": [{"name": "s", "path": "sales.parquet"}]}]
        for extra in invalid:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_query({**base, **extra})


@unittest.skipUnless(installed(), "Optional pinned DuckDB wheel is not installed")
class AnalyticsNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".data-test-analytics-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.registry = ToolRegistry(Settings(data_dir=Path(self.temp.name)))
        self.tools = AnalyticsTools(self.registry)

    def write(self, path, text):
        file = self.registry.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8", newline="")
        return file

    def query(self, path="data.csv", **kwargs):
        result = self.tools.execute("analytics_query", {"tables": [{"name": "s", "path": path}], **kwargs})
        self.assertTrue(result["ok"], result)
        return result

    def test_real_weighted_total_and_snapshot_provenance(self):
        file = self.write("data.csv", "item,quantity,unit_cost\nWidget,3,2.5\nCable,2,4.25\n")
        result = self.query(numeric_columns=["s.quantity", "s.unit_cost"], aggregations=[
            {"op": "sum_product", "columns": ["s.quantity", "s.unit_cost"], "as": "total_value"},
            {"op": "sum", "column": "s.quantity", "as": "quantity"}])
        self.assertEqual(result["rows"], [{"total_value": "16", "quantity": "5"}])
        self.assertEqual(result["sources"][0]["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
        self.assertEqual(result["sources"][0]["bytes"], file.stat().st_size)
        self.assertEqual(result["version"], "2.0.0")
        self.assertEqual(self.tools.policy("analytics_query", {}), "auto")
        self.assertTrue(self.tools.status()["ready"])

    def test_join_grouped_6000_rows_matches_independent_fraction_oracle(self):
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["sku", "quantity", "price"])
        oracle = {"parts": Fraction(0), "service": Fraction(0)}
        counts = {key: 0 for key in oracle}
        for index in range(6000):
            sku = "001" if index % 2 == 0 else "002"
            quantity = str(index % 7 + 1)
            price = f"{index % 19}.{index % 100:02d}"
            writer.writerow([sku, quantity, price])
            group = "parts" if sku == "001" else "service"
            oracle[group] += Fraction(quantity) * Fraction(price)
            counts[group] += 1
        self.write("orders.csv", output.getvalue())
        self.write("lookup.json", '[{"sku":"001","department":"parts"},{"sku":"002","department":"service"}]')
        result = self.tools.execute("analytics_query", {
            "tables": [{"name": "o", "path": "orders.csv"}, {"name": "d", "path": "lookup.json"}],
            "numeric_columns": ["o.quantity", "o.price"],
            "join": {"left": "o.sku", "right": "d.sku", "type": "inner"},
            "group_by": ["d.department"], "aggregations": [
                {"op": "sum_product", "columns": ["o.quantity", "o.price"], "as": "total"},
                {"op": "count", "column": "*", "as": "orders"}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["matched_rows"], 6000)
        for row in result["rows"]:
            expected = oracle[row["d.department"]]
            self.assertEqual(Fraction(row["total"]), expected)
            self.assertEqual(row["orders"], counts[row["d.department"]])

    def test_full_100000_rows_exceeds_old_tool_limit_and_remains_exact(self):
        self.write("data.csv", "amount\n" + "0.01\n" * MAX_ROWS)
        result = self.query(numeric_columns=["s.amount"], aggregations=[{"op": "sum", "column": "s.amount", "as": "total"}])
        self.assertEqual(result["rows"], [{"total": "1000"}])
        self.assertEqual(result["input_rows"], MAX_ROWS)

    def test_text_ids_filters_select_sort_and_pagination(self):
        self.write("data.csv", "id,label,n\n001,Alpha,0.1\n010,Beta,0.11\n002,Alpine,0.2\n")
        first = self.query(select=["s.id", "s.label"], filters=[{"column": "s.label", "op": "contains", "value": "AL"}], order_by=[{"column": "s.id", "direction": "asc"}], limit=1)
        self.assertEqual(first["rows"], [{"s.id": "001", "s.label": "Alpha"}])
        self.assertEqual(first["total_rows"], 2)
        self.assertEqual(first["next_offset"], 1)
        last = self.query(select=["s.id"], offset=2)
        self.assertEqual(last["rows"], [{"s.id": "002"}])
        self.assertFalse(last["truncated"])
        exhausted = self.query(offset=100)
        self.assertEqual(exhausted["total_rows"], 3)
        self.assertEqual(exhausted["rows"], [])
        exact = self.query(numeric_columns=["s.n"], select=["s.id"], filters=[{"column": "s.n", "op": "eq", "value": "0.1001"}])
        self.assertEqual(exact["rows"], [])  # A finer filter must never round to0.1.

    def test_decimal_cancellation_and_raw_json_precision(self):
        self.write("precise.json", '[{"n":0.12345678901234567890123456789},{"n":0.2}]')
        result = self.query("precise.json", numeric_columns=["s.n"], aggregations=[{"op": "sum", "column": "s.n", "as": "total"}])
        self.assertEqual(result["rows"], [{"total": "0.32345678901234567890123456789"}])
        self.write("cancel.json", '[{"a":"1e15","b":"1e15"},{"a":"0.1","b":"0.1"},{"a":"-1e15","b":"1e15"}]')
        result = self.query("cancel.json", numeric_columns=["s.a", "s.b"], aggregations=[{"op": "sum_product", "columns": ["s.a", "s.b"], "as": "total"}])
        self.assertEqual(result["rows"], [{"total": "0.01"}])
        self.write("cancel-sum.json", '[{"n":"1e30"},{"n":"0.01"},{"n":"-1e30"}]')
        result = self.query("cancel-sum.json", numeric_columns=["s.n"], aggregations=[{"op": "sum", "column": "s.n", "as": "total"}])
        self.assertEqual(result["rows"], [{"total": "0.01"}])

    def test_average_decimal_rounding_is_explicit_and_min_max_exact(self):
        self.write("data.csv", "n\n1\n0\n0\n")
        result = self.query(numeric_columns=["s.n"], aggregations=[
            {"op": "avg", "column": "s.n", "as": "average"}, {"op": "min", "column": "s.n", "as": "minimum"}, {"op": "max", "column": "s.n", "as": "maximum"}])
        self.assertEqual(result["rows"], [{"average": "0." + "3" * 38, "minimum": "0", "maximum": "1"}])
        self.assertEqual(result["average_precision"], 38)
        self.assertEqual(result["average_rounding"], "half_even_significant_digits")

    def test_empty_table_aggregates_are_defined(self):
        self.write("data.csv", "n\n")
        result = self.query(numeric_columns=["s.n"], aggregations=[
            {"op": "sum", "column": "s.n", "as": "total"}, {"op": "avg", "column": "s.n", "as": "average"}, {"op": "count", "column": "*", "as": "count"}])
        self.assertEqual(result["rows"], [{"total": "0", "average": None, "count": 0}])

    def test_left_join_does_not_hide_missing_numeric_lookup(self):
        self.write("left.csv", "id,qty\n001,2\n002,3\n")
        self.write("right.csv", "id,price\n001,4.5\n")
        base = {"tables": [{"name": "l", "path": "left.csv"}, {"name": "r", "path": "right.csv"}],
                "numeric_columns": ["l.qty", "r.price"], "join": {"left": "l.id", "right": "r.id", "type": "left"}}
        selected = self.tools.execute("analytics_query", {**base, "select": ["l.id", "r.price"]})
        self.assertTrue(selected["ok"], selected)
        self.assertIsNone(selected["rows"][1]["r.price"])
        failed = self.tools.execute("analytics_query", {**base, "aggregations": [{"op": "sum_product", "columns": ["l.qty", "r.price"], "as": "total"}]})
        self.assertFalse(failed["ok"], failed)
        self.assertIn("unmatched", failed["error"])

    def test_duplicate_join_keys_and_mixed_numeric_keys_rejected(self):
        self.write("left.csv", "id\n1\n")
        self.write("right.csv", "id\n1\n1\n")
        base = {"tables": [{"name": "l", "path": "left.csv"}, {"name": "r", "path": "right.csv"}], "join": {"left": "l.id", "right": "r.id"}}
        duplicate = self.tools.execute("analytics_query", base)
        self.assertFalse(duplicate["ok"], duplicate)
        self.assertIn("unique", duplicate["error"])
        mixed = self.tools.execute("analytics_query", {**base, "numeric_columns": ["l.id"]})
        self.assertFalse(mixed["ok"], mixed)

    def test_overflow_missing_values_and_excessive_precision_fail_closed(self):
        cases = ['[{"n":"9e37"},{"n":"-9e37"}]', '[{"n":"1e38"}]', '[{"n":"0.' + '0' * 38 + '1"}]', '[{"n":null}]', '[{"n":""}]', '[{"n":"NaN"}]', '[{"n":true}]']
        for text in cases:
            with self.subTest(text=text):
                self.write("bad.json", text)
                result = self.tools.execute("analytics_query", {"tables": [{"name": "s", "path": "bad.json"}], "numeric_columns": ["s.n"], "aggregations": [{"op": "sum", "column": "s.n", "as": "total"}]})
                self.assertFalse(result["ok"], result)

    def test_injection_shaped_headers_and_values_are_data(self):
        header = 'x"); COPY secret TO bad; --'
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([header, "value"])
        writer.writerow(["INSTALL httpfs; LOAD httpfs;", "'=HYPERLINK(\"http://example.invalid\")"])
        self.write("data.csv", output.getvalue())
        result = self.query(select=["s." + header, "s.value"])
        self.assertEqual(result["rows"][0]["s." + header], "INSTALL httpfs; LOAD httpfs;")
        self.assertFalse((self.registry.root / "bad").exists())

    def test_invalid_paths_never_escape_and_hardlinks_are_rejected(self):
        for path in ["../outside.csv", "C:/outside.csv", "E:/outside.csv", "http://127.0.0.1/data.csv", "data.csv:stream", "NUL.csv"]:
            with self.subTest(path=path):
                result = self.tools.execute("analytics_query", {"tables": [{"name": "s", "path": path}]})
                self.assertFalse(result["ok"], result)
        original = self.write("data.csv", "n\n1\n")
        linked = self.registry.root / "hardlink.csv"
        try:
            os.link(original, linked)
        except OSError:
            self.skipTest("Hardlink creation unavailable")
        result = self.tools.execute("analytics_query", {"tables": [{"name": "s", "path": "hardlink.csv"}]})
        self.assertFalse(result["ok"], result)

    def test_binary_nested_duplicate_and_oversized_inputs_rejected(self):
        cases = {"duplicate.csv": "n,n\n1,2", "case.csv": "N,n\n1,2", "ragged.csv": "n,x\n1", "nested.json": '[{"n":{"x":1}}]', "duplicate.json": '[{"n":1,"n":2}]', "nan.json": '[{"n":NaN}]', "binary.csv": "SQLite format3\x00\x00", "deep.json": "[" * 100 + "0" + "]" * 100, "rows.csv": "n\n" + "1\n" * (MAX_ROWS + 1)}
        for path, text in cases.items():
            with self.subTest(path=path):
                self.write(path, text)
                result = self.tools.execute("analytics_query", {"tables": [{"name": "s", "path": path}]})
                self.assertFalse(result["ok"], result)
        (self.registry.root / "oversized.csv").write_bytes(b"x" * (MAX_INPUT_BYTES + 1))
        result = self.tools.execute("analytics_query", {"tables": [{"name": "s", "path": "oversized.csv"}]})
        self.assertFalse(result["ok"], result)

    def test_output_bound_reports_truncation_and_rejects_single_huge_row(self):
        self.write("data.csv", "n,payload\n" + "1," + "x" * 9000 + "\n" * 0 + "\n" + "2," + "x" * 9000 + "\n" + "3," + "x" * 9000 + "\n" + "4," + "x" * 9000 + "\n" + "5," + "x" * 9000 + "\n" + "6," + "x" * 9000 + "\n" + "7," + "x" * 9000 + "\n")
        result = self.query()
        self.assertTrue(result["truncated"])
        self.assertLess(len(json.dumps(result).encode()), 60000)
        self.assertEqual(result["next_offset"], result["returned_rows"])
        header = ",".join("c" + str(i) for i in range(7))
        self.write("wide.csv", header + "\n" + ",".join("x" * 9000 for _ in range(7)))
        failed = self.tools.execute("analytics_query", {"tables": [{"name": "s", "path": "wide.csv"}]})
        self.assertFalse(failed["ok"], failed)
        narrow = self.query("wide.csv", select=["s.c0"])
        self.assertEqual(narrow["returned_rows"], 1)

    def test_native_engine_external_access_and_configuration_are_locked(self):
        # Trusted test inspection, never a public SQL tool.
        from ariadne._analytics_worker import _connection
        connection = _connection()
        self.addCleanup(connection.close)
        expected = {"enable_external_access": "false", "allow_unsigned_extensions": "false", "allow_community_extensions": "false", "autoload_known_extensions": "false", "autoinstall_known_extensions": "false", "lock_configuration": "true", "python_enable_replacements": "false", "threads": "1"}
        for setting, value in expected.items():
            self.assertEqual(str(connection.execute("SELECT current_setting(?)", [setting]).fetchone()[0]).lower(), value)
        destination = self.registry.root / "must-not-exist.csv"
        for statement in ["SET enable_external_access=true", "INSTALL httpfs", "LOAD httpfs", "ATTACH 'untrusted.duckdb'", "SELECT * FROM read_csv('missing.csv')", "COPY (SELECT 1) TO '" + str(destination).replace("'", "''") + "'"]:
            with self.subTest(statement=statement), self.assertRaises(Exception):
                connection.execute(statement)
        self.assertFalse(destination.exists())

    def test_cancellation_terminates_actual_worker(self):
        self.write("data.csv", "n\n" + "0.01\n" * MAX_ROWS)
        processes = []
        real_popen = subprocess.Popen
        calls = 0

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 5

        started = time.monotonic()
        with mock.patch("ariadne.worker.subprocess.Popen", side_effect=capture):
            result = self.tools.execute("analytics_query", {"tables": [{"name": "s", "path": "data.csv"}], "numeric_columns": ["s.n"], "aggregations": [{"op": "sum", "column": "s.n", "as": "total"}]}, cancelled=cancelled)
        self.assertFalse(result["ok"], result)
        self.assertIn("cancel", result["error"].lower())
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertLess(time.monotonic() - started, 5)

    def test_os_memory_ceiling_after_real_duckdb_import(self):
        # Operator test code, separate from the model-facing worker protocol.
        code = '''import sys,json
sys.path.insert(0,sys.argv[1])
from ariadne.worker import apply_limits
apply_limits(768*1024*1024,10)
import duckdb
chunks=[]
try:
 for i in range(64): chunks.append(bytearray(32*1024*1024))
 print(json.dumps({"limited":False}))
except MemoryError:
 print(json.dumps({"limited":True,"allocated_bytes":len(chunks)*32*1024*1024,"version":duckdb.__version__}))
'''
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(Path.cwd())], capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr[:1000])
        proof = json.loads(result.stdout)
        self.assertTrue(proof["limited"], proof)
        self.assertEqual(proof["version"], VERSION)
        self.assertLess(proof["allocated_bytes"], MEMORY_BYTES)
        self.assertGreater(proof["allocated_bytes"], 128 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
