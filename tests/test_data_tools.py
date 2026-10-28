import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ariadne.config import Settings
from ariadne.data_tools import DataTools, MAX_ROWS
from ariadne.tools import ToolRegistry


class DataToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".data-test-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.registry = ToolRegistry(Settings(data_dir=Path(self.temp.name)))
        self.tools = DataTools(self.registry)

    def raw(self, path, text):
        result = self.registry.execute("file_write", {"path": path, "content": text})
        self.assertTrue(result["ok"], result)

    def test_json_table_roundtrip_and_artifact_hash(self):
        result = self.tools.execute("table_write", {"path": "business/inventory.json", "rows": [{"id": 1, "name": "Café", "price": 0.1}, {"id": 2, "name": "Tea"}]})
        self.assertTrue(result["ok"], result)
        artifact = result["artifact"]
        content = (self.registry.root / artifact["path"]).read_bytes()
        self.assertEqual(artifact["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(artifact["bytes"], len(content))
        self.assertEqual(artifact["row_count"], 2)
        loaded = self.tools.execute("table_read", {"path": artifact["path"], "limit": 1})
        self.assertEqual(loaded["rows"][0], {"id": 1, "name": "Café", "price": "0.1"})
        self.assertEqual(loaded["next_offset"], 1)
        self.assertTrue(loaded["truncated"])
        last = self.tools.execute("table_read", {"path": artifact["path"], "offset": 1})
        self.assertIsNone(last["rows"][0]["price"])
        self.assertIsNone(last["next_offset"])
        self.assertFalse(self.tools.execute("table_write", {"path": artifact["path"], "rows": []})["ok"])

    def test_formula_like_text_is_escaped_but_numeric_negatives_are_not(self):
        result = self.tools.execute("table_write", {"path": "safe.csv", "rows": [
            {"name": "=HYPERLINK(\"bad\")", "amount": -10}, {"name": " +cmd", "amount": 0},
            {"name": "@command", "amount": 4}, {"name": "\tvalue", "amount": 2}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["escaped_cells"], 4)
        loaded = self.tools.execute("table_read", {"path": "safe.csv"})
        self.assertTrue(all(row["name"].startswith("'") for row in loaded["rows"]))
        self.assertEqual(loaded["rows"][0]["amount"], "-10")

    def test_tsv_quotes_and_unicode(self):
        result = self.tools.execute("table_write", {"path": "items.tsv", "rows": [{"item": "é\tpart", "note": 'quoted "value"\nline'}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.tools.execute("table_read", {"path": "items.tsv"})["rows"], [{"item": "é\tpart", "note": 'quoted "value"\nline'}])

    def test_decimal_groups_filters_and_select(self):
        self.raw("sales.csv", "team,amount,status\nA,0.1,paid\nA,0.2,paid\nB,2.50,due\nB,,due\n")
        result = self.tools.execute("table_query", {"path": "sales.csv", "group_by": ["team"], "aggregations": [
            {"column": "amount", "op": "sum", "as": "total"}, {"column": "amount", "op": "avg", "as": "average"},
            {"column": "amount", "op": "min", "as": "minimum"}, {"column": "amount", "op": "max", "as": "maximum"},
            {"column": "*", "op": "count", "as": "rows"}, {"column": "amount", "op": "count", "as": "numbers"}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rows"][0], {"team": "A", "total": "0.3", "average": "0.15", "minimum": "0.1", "maximum": "0.2", "rows": 2, "numbers": 2})
        self.assertEqual(result["rows"][1]["numbers"], 1)
        selected = self.tools.execute("table_query", {"path": "sales.csv", "filters": [{"column": "amount", "op": "gte", "value": "0.2"}, {"column": "status", "op": "contains", "value": "PA"}], "select": ["team", "amount"]})
        self.assertEqual(selected["rows"], [{"team": "A", "amount": "0.2"}])

    def test_high_precision_json_and_average_cancellation(self):
        self.raw("precise.json", '[{"n":0.12345678901234567890123456789},{"n":0.2}]')
        result = self.tools.execute("table_query", {"path": "precise.json", "aggregations": [{"column": "n", "op": "sum", "as": "total"}]})
        self.assertEqual(result["rows"], [{"total": "0.32345678901234567890123456789"}])
        self.raw("cancel.json", '[{"n":"1e50"},{"n":"1"},{"n":"-1e50"}]')
        result = self.tools.execute("table_query", {"path": "cancel.json", "aggregations": [{"column": "n", "op": "avg", "as": "average"}]})
        self.assertEqual(result["rows"], [{"average": "0." + "3" * 40}])

    def test_sum_product_computes_weighted_inventory_total(self):
        self.raw("inventory.csv", "item,quantity,unit_cost\nWidget,3,2.5\nCable,2,4.25\n")
        result = self.tools.execute("table_query", {"path": "inventory.csv", "aggregations": [
            {"op": "sum_product", "columns": ["quantity", "unit_cost"], "as": "total_value"},
            {"op": "sum", "column": "quantity", "as": "total_quantity"}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rows"], [{"total_value": "16", "total_quantity": "5"}])
        grouped = self.tools.execute("table_query", {"path": "inventory.csv", "group_by": ["item"],
            "aggregations": [{"op": "sum_product", "columns": ["quantity", "unit_cost"], "as": "value"}]})
        self.assertEqual(grouped["rows"], [{"item": "Widget", "value": "7.5"}, {"item": "Cable", "value": "8.5"}])

    def test_sum_product_preserves_small_product_through_large_cancellation(self):
        self.raw("product-cancellation.json", '[{"a":"1e100","b":"1e100"},{"a":"1e-100","b":"1e-100"},{"a":"-1e100","b":"1e100"}]')
        result = self.tools.execute("table_query", {"path": "product-cancellation.json", "aggregations": [
            {"op": "sum_product", "columns": ["a", "b"], "as": "total"}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rows"], [{"total": "0." + "0" * 199 + "1"}])

    def test_sum_product_validates_shape_and_finite_numeric_factors(self):
        self.raw("factors.csv", "quantity,unit_cost\n3,2.5\n")
        invalid = [
            {"op": "sum_product", "columns": [], "as": "value"},
            {"op": "sum_product", "columns": ["quantity"], "as": "value"},
            {"op": "sum_product", "columns": ["quantity", "unit_cost", "quantity"], "as": "value"},
            {"op": "sum_product", "columns": "quantity*unit_cost", "as": "value"},
            {"op": "sum_product", "columns": ["quantity", "missing"], "as": "value"},
            {"op": "sum_product", "columns": ["quantity", "unit_cost"], "column": "quantity", "as": "value"},
            {"op": "sum_product", "columns": ["quantity", "unit_cost"], "expression": "quantity*unit_cost", "as": "value"},
            {"op": "sum_product", "column": "quantity", "as": "value"},
        ]
        for aggregation in invalid:
            with self.subTest(aggregation=aggregation):
                result = self.tools.execute("table_query", {"path": "factors.csv", "aggregations": [aggregation]})
                self.assertFalse(result["ok"], result)
        for value in ["", "NaN", "Infinity", "1e101", None, True, "not numeric"]:
            with self.subTest(value=value):
                (self.registry.root / "invalid-factor.json").write_text(json.dumps([{"quantity": value, "unit_cost": 2.5}]), encoding="utf-8")
                result = self.tools.execute("table_query", {"path": "invalid-factor.json", "aggregations": [
                    {"op": "sum_product", "columns": ["quantity", "unit_cost"], "as": "value"}]})
                self.assertFalse(result["ok"], result)

    def test_oversized_row_returns_explicit_error_and_select_can_progress(self):
        self.tools.execute("table_write", {"path": "wide.json", "rows": [{"a": "a" * 9000, "b": "b" * 9000, "c": "c" * 9000}]})
        result = self.tools.execute("table_read", {"path": "wide.json"})
        self.assertFalse(result["ok"])
        self.assertIn("select", result["error"])
        self.assertEqual(self.tools.execute("table_query", {"path": "wide.json", "select": ["a"]})["returned_rows"], 1)

    def test_empty_aggregate_is_defined(self):
        self.tools.execute("table_write", {"path": "empty.json", "rows": []})
        result = self.tools.execute("table_query", {"path": "empty.json", "aggregations": [{"column": "*", "op": "count", "as": "count"}]})
        self.assertEqual(result["rows"], [{"count": 0}])

    def test_invalid_paths_and_options_never_escape_workspace(self):
        for path in ["../outside.csv", "C:/outside.csv", "E:/outside.csv", "a/../b.json", "a.csv:stream", "NUL.csv"]:
            with self.subTest(path=path):
                self.assertFalse(self.tools.execute("table_write", {"path": path, "rows": [{"x": 1}]})["ok"])
                self.assertFalse(self.tools.execute("table_read", {"path": path})["ok"])
        self.assertFalse((Path(self.temp.name) / "outside.csv").exists())
        self.assertFalse(self.tools.execute("table_write", {"path": "bad.csv", "rows": [{"x": [1]}]})["ok"])
        self.assertFalse(self.tools.execute("table_write", {"path": "bad.json", "rows": [{"x": float("nan")} ]})["ok"])
        self.assertFalse(self.tools.execute("table_write", {"path": "bad.json", "rows": [], "overwrite": "true"})["ok"])
        self.assertFalse(self.tools.execute("table_query", {"path": "bad.json", "expression": "import os"})["ok"])
        self.assertFalse(self.tools.execute("table_read", {"path": "bad.json", "limit": True})["ok"])
        with self.assertRaises(ValueError):
            self.tools.policy("unknown", {})

    def test_malformed_and_oversized_tables_are_rejected(self):
        for path, text in [("duplicate.csv", "x,x\n1,2"), ("uneven.csv", "x,y\n1"),
                           ("not-array.json", '{"x":1}'), ("nested.json", '[{"x":{"y":1}}]'),
                           ("nan.json", '[{"x":NaN}]'), ("large.csv", "x\n" + "1\n" * (MAX_ROWS + 1)),
                           ("long.csv", "x\n" + "a" * 100_000)]:
            with self.subTest(path=path):
                # Write the over-limit fixture directly inside the owned test workspace.
                (self.registry.root / path).write_text(text, encoding="utf-8")
                self.assertFalse(self.tools.execute("table_read", {"path": path})["ok"])


if __name__ == "__main__":
    unittest.main()
