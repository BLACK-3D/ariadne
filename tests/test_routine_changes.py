"""Verified offline structural changes: exact values, bounds and honest limits."""
import hashlib
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ariadne.config import Settings
from ariadne.procedures import Procedures
from ariadne.routines import Routines
from ariadne.routine_changes import MAX_RESULT_BYTES, parse_json
from ariadne.store import Store
from ariadne.tools import ToolRegistry


class RoutineChangeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".tools-test-routine-changes-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(data_dir=Path(self.temporary.name)/"data")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.procedures = Procedures(self.registry, self.store)
        self.routines = Routines(self.procedures, self.store)
        self.definition = self.routines.save("Changes", ["business_snapshot"])

    def complete(self, request_id, fields=None, definition=None):
        definition = definition or self.definition
        self.routines.enqueue(definition["id"], request_id)
        payload = {"schema": 1, "inventory": [], "trial_balance": [], "partial": False, **(fields or {})}
        with mock.patch.object(self.procedures, "_snapshot", return_value=(payload, [])):
            self.assertTrue(self.routines.tick())
        result = self.routines.detail(request_id)
        self.assertEqual(result["status"], "completed")
        return result

    def replace_primary(self, request_id, payload):
        run = self.routines.detail(request_id)
        artifact = next(row for row in run["steps"][0]["receipt"]["artifacts"] if row["path"].endswith("business-snapshot.json"))
        self.registry._path(artifact["path"]).write_bytes(payload)
        artifact.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        for row in run["artifacts"]:
            if row["path"] == artifact["path"]:
                row.update(artifact)
        with self.routines.database() as db:
            db.execute("UPDATE routine_runs SET steps=?,artifacts=? WHERE id=?", (json.dumps(run["steps"]), json.dumps(run["artifacts"]), request_id))

    def test_first_run_and_noncompleted_state_do_not_claim_zero_changes(self):
        self.routines.enqueue(self.definition["id"], "queued")
        unavailable = self.routines.compare("queued")
        self.assertEqual(unavailable["state"], "current_unavailable")
        self.assertIsNone(unavailable["totals"]["observed_differences"])
        self.routines.tick()
        first = self.routines.compare("queued")
        self.assertEqual(first["state"], "first_run")
        self.assertIsNone(first["baseline_id"])
        self.assertIsNone(first["totals"]["observed_differences"])
        self.assertTrue(first["steps"][0]["current_source"]["sha256"])
        with self.assertRaises(KeyError):
            self.routines.compare("unknown")

    def test_identical_reports_are_verified_without_hidden_effects(self):
        self.complete("one", {"extra": {"value": 4}})
        self.complete("two", {"extra": {"value": 4}})
        before_runs = self.routines.runs()
        before_defs = self.routines.definitions()
        before_status = self.routines.status()
        before_files = {str(path): path.read_bytes() for path in self.registry.root.rglob("*") if path.is_file()}
        with mock.patch("subprocess.Popen", side_effect=AssertionError("No process")), mock.patch("socket.create_connection", side_effect=AssertionError("No network")), mock.patch("urllib.request.urlopen", side_effect=AssertionError("No request")), mock.patch.object(self.routines, "_bump", side_effect=AssertionError("No writes")):
            result = self.routines.compare("two")
        self.assertEqual(result["state"], "compared")
        self.assertEqual(result["baseline_id"], "one")
        self.assertEqual(result["totals"]["observed_differences"], 0)
        self.assertTrue(result["comparison_complete"])
        self.assertEqual(self.routines.runs(), before_runs)
        self.assertEqual(self.routines.definitions(), before_defs)
        self.assertEqual(self.routines.status(), before_status)
        self.assertEqual({str(path): path.read_bytes() for path in self.registry.root.rglob("*") if path.is_file()}, before_files)

    def test_changes_preserve_types_sorted_keys_array_indices_and_rfc6901_paths(self):
        self.complete("before", {"extra": {"a/b~c": False, "gone": 4, "list": ["a", 2]}})
        self.complete("after", {"extra": {"a/b~c": 0, "added": None, "list": ["a", 3, 4]}})
        result = self.routines.compare("after", "before")
        changes = result["steps"][0]["changes"]
        self.assertEqual([row["pointer"] for row in changes], ["/extra/a~1b~0c", "/extra/added", "/extra/gone", "/extra/list/1", "/extra/list/2"])
        self.assertEqual(changes[0]["kind"], "type_changed")
        self.assertEqual((changes[0]["before_type"], changes[0]["after_type"]), ("boolean", "integer"))
        self.assertEqual(changes[1]["kind"], "added")
        self.assertEqual(changes[2]["kind"], "removed")
        self.assertEqual(result["totals"]["substantive_differences"], 5)

    def test_only_allowlisted_valid_generation_dates_are_metadata(self):
        self.complete("date-one", {"as_of": "2026-01-01", "revision": 1, "expires": "2027-01-01"})
        self.complete("date-two", {"as_of": "2026-01-02", "revision": 1, "expires": "2027-01-01"})
        metadata = self.routines.compare("date-two")
        self.assertTrue(metadata["metadata_only"])
        self.assertEqual(metadata["totals"]["metadata_differences"], 1)
        self.complete("date-three", {"as_of": "2026-01-03", "revision": 2, "expires": "2028-01-01"})
        mixed = self.routines.compare("date-three")
        self.assertFalse(mixed["metadata_only"])
        self.assertEqual(mixed["totals"]["metadata_differences"], 1)
        self.assertEqual(mixed["totals"]["substantive_differences"], 2)
        self.complete("invalid-date", {"as_of": "invalid", "revision": 2, "expires": "2028-01-01"})
        self.assertEqual(self.routines.compare("invalid-date")["totals"]["substantive_differences"], 1)

    def test_baseline_ignores_name_revision_interval_edits_but_requires_same_step_order(self):
        self.complete("matching-old", {"extra": 1})
        changed = self.routines.save("Different name", ["power_budget"], interval_minutes=60, routine_id=self.definition["id"])
        self.routines.enqueue(changed["id"], "other-plan")
        self.routines.tick()
        self.definition = self.routines.save("Newest name", ["business_snapshot"], interval_minutes=30, routine_id=changed["id"])
        self.complete("matching-new", {"extra": 2})
        result = self.routines.compare("matching-new")
        self.assertEqual(result["baseline_id"], "matching-old")
        self.assertEqual(result["state"], "compared")
        mismatch = self.routines.compare("matching-new", "other-plan")
        self.assertEqual(mismatch["state"], "incomparable")
        self.assertIsNone(mismatch["totals"]["observed_differences"])
        future = self.routines.compare("matching-old", "matching-new")
        self.assertEqual(future["state"], "incomparable")

    def test_explicit_other_routine_is_incomparable_even_with_same_procedures(self):
        another = self.routines.save("Another", ["business_snapshot"])
        self.complete("other-routine", definition=another)
        self.complete("current-routine")
        self.assertEqual(self.routines.compare("current-routine", "other-routine")["state"], "incomparable")

    def test_source_hashes_and_exact_primary_paths_are_retained(self):
        older = self.complete("provenance-old", {"extra": 1})
        newer = self.complete("provenance-new", {"extra": 2})
        result = self.routines.compare("provenance-new")
        for side, run in (("baseline_source", older), ("current_source", newer)):
            source = result["steps"][0][side]
            self.assertEqual(source["parent_request_id"], run["id"])
            self.assertEqual(source["child_request_id"], run["steps"][0]["request_id"])
            self.assertTrue(source["path"].endswith("/business-snapshot.json"))
            self.assertEqual(hashlib.sha256(self.registry._path(source["path"]).read_bytes()).hexdigest(), source["sha256"])

    def test_changed_or_missing_artifacts_use_existing_recurrence_integrity_hold(self):
        self.definition = self.routines.save("Enabled", ["business_snapshot"], enabled=True, routine_id=self.definition["id"])
        old = self.complete("integrity-old")
        current = self.complete("integrity-current")
        self.registry._path(old["artifacts"][0]["path"]).write_bytes(b"Owner changed report")
        result = self.routines.compare("integrity-current")
        self.assertEqual(result["state"], "partial")
        self.assertTrue(result["integrity_failed"])
        self.assertFalse(self.routines.definitions()[0]["enabled"])
        self.assertEqual(self.routines.detail("integrity-current"), current)

    def test_invalid_duplicate_nonfinite_or_deep_json_is_explicitly_partial(self):
        self.complete("valid-old")
        self.complete("invalid-new")
        for payload in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{broken', b'{"x":"\\ud800"}', b'{"\\ud800":1}', b'['*1500+b'0'+b']'*1500):
            with self.subTest(prefix=payload[:20]):
                self.replace_primary("invalid-new", payload)
                result = self.routines.compare("invalid-new", "valid-old")
                self.assertEqual(result["state"], "partial")
                self.assertFalse(result["comparison_complete"])
                self.assertTrue(result["totals"]["counts_are_lower_bounds"])

    def test_numbers_are_lossless_and_bool_is_not_an_integer(self):
        parsed = parse_json(b'{"v":9007199254740993.000000000001}')
        self.assertEqual(parsed["v"], Decimal("9007199254740993.000000000001"))
        self.complete("number-old")
        self.complete("number-new")
        self.replace_primary("number-old", b'{"v":9007199254740993.000000000001}')
        self.replace_primary("number-new", b'{"v":9007199254740993.000000000002}')
        result = self.routines.compare("number-new")
        self.assertEqual(result["totals"]["observed_differences"], 1)
        self.assertEqual(result["steps"][0]["changes"][0]["before"], "9007199254740993.000000000001")
        with self.assertRaises(ValueError):
            parse_json(b'0'*4_000_001)

    def test_invalid_late_or_nested_string_is_rejected_before_counting_changes(self):
        self.complete("strings-old")
        self.complete("strings-new")
        self.replace_primary("strings-old", b'{"a":0,"z":"valid"}')
        for payload in (b'{"a":1,"z":"\\ud800"}', b'{"a":1,"added":{"hidden":"\\ud800"}}'):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_json(payload)
                self.replace_primary("strings-new", payload)
                result = self.routines.compare("strings-new", "strings-old")
                self.assertEqual(result["state"], "partial")
                self.assertEqual(result["totals"]["observed_differences"], 0)
                self.assertEqual(result["totals"]["metadata_differences"]+result["totals"]["substantive_differences"], 0)
                self.assertEqual(result["steps"][0]["observed_differences"], 0)
                self.assertEqual(result["steps"][0]["changes"], [])
                self.assertTrue(result["totals"]["counts_are_lower_bounds"])
        self.assertEqual(parse_json(b'"\\ud83d\\ude00"'), "\U0001f600")

    def test_shared_node_and_difference_limits_never_claim_an_unchanged_complete_report(self):
        self.complete("large-old", {"extra": [0]*21_000})
        self.complete("large-new", {"extra": [0]*20_999+[1]})
        limited = self.routines.compare("large-new")
        self.assertEqual(limited["state"], "partial")
        self.assertTrue(limited["truncation"]["traversal"])
        self.assertLessEqual(limited["totals"]["visited_nodes"], 20_000)
        self.assertTrue(limited["totals"]["counts_are_lower_bounds"])
        self.complete("different-old", {"extra": {str(i): 0 for i in range(150)}})
        self.complete("different-new", {"extra": {str(i): 1 for i in range(150)}})
        limited = self.routines.compare("different-new")
        self.assertEqual(limited["totals"]["observed_differences"], 100)
        self.assertEqual(limited["state"], "partial")

    def test_utf8_response_and_preview_limits_preserve_omission_accounting(self):
        self.complete("unicode-old", {"extra": {str(i): "旧"*200 for i in range(80)}})
        self.complete("unicode-new", {"extra": {str(i): "新"*200 for i in range(80)}})
        result = self.routines.compare("unicode-new")
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.assertLessEqual(len(encoded), MAX_RESULT_BYTES)
        self.assertEqual(result["totals"]["observed_differences"], 80)
        self.assertFalse(result["totals"]["counts_are_lower_bounds"])
        self.assertTrue(result["truncation"]["response"])
        self.assertEqual(result["totals"]["returned_differences"]+result["totals"]["omitted_observed_differences"], 80)
        for step in result["steps"]:
            for change in step["changes"]:
                self.assertLessEqual(len(change["before"]), 160)
                self.assertLessEqual(len(change["after"]), 160)

    def test_existing_partial_snapshot_and_oversized_pointer_are_explicit(self):
        self.complete("partial-old", {"partial": True, "extra": {"long"*2000: 1}})
        self.complete("partial-new", {"partial": True, "extra": {"long"*2000: 2}})
        result = self.routines.compare("partial-new")
        self.assertEqual(result["state"], "partial")
        self.assertTrue(result["truncation"]["input_reports_partial"])
        self.assertEqual(result["totals"]["observed_differences"], 1)
        self.assertEqual(result["totals"]["omitted_observed_differences"], 1)
        self.assertEqual(result["totals"]["returned_differences"], 0)
        self.assertFalse(result["metadata_only"])


if __name__ == "__main__":
    unittest.main()
