"""Independent model-context and authenticated API review of routine changes."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from ariadne.config import Settings
from ariadne.engine import Engine, _comparison_for_model
from ariadne.routine_changes import PRIMARY_ARTIFACTS
from ariadne.server import Application
from ariadne.store import Store


class NoModel:
    def status(self):
        return {"available": False, "models": []}
    def chat(self, *args, **kwargs):
        raise AssertionError("Comparison review must use no model")


def comparison_fixture(procedure_ids=None, controls=False, change_count=100):
    procedures = list(procedure_ids or list(PRIMARY_ARTIFACTS)[:4])
    result = {"state": "compared", "current_id": "c"*64, "baseline_id": "b"*64,
        "metadata_only": False, "comparison_complete": True,
        "totals": {"observed_differences": change_count, "metadata_differences": 0,
            "substantive_differences": change_count, "returned_differences": change_count,
            "omitted_observed_differences": 0, "counts_are_lower_bounds": False},
        "truncation": {"traversal": False, "response": False, "omitted_observed_differences": 0},
        "steps": [], "warnings": ["\x00"*500 if controls else "Source scope remains limited."]}
    for index, procedure in enumerate(procedures):
        row = {"procedure_id": procedure, "state": "compared", "observed_differences": 0,
            "metadata_differences": 0, "substantive_differences": 0, "counts_are_lower_bounds": False,
            "comparison_complete": True, "metadata_only": False, "input_reports_partial": False,
            "omitted_observed_differences": 0, "changes": [], "warning": "\x01"*500 if controls else "A saved report is not current physical state."}
        for label, prefix in (("current_source", "c"), ("baseline_source", "b")):
            child = "routine-"+prefix*47+str(index)
            row[label] = {"parent_request_id": result["current_id" if prefix == "c" else "baseline_id"],
                "child_request_id": child, "path": "procedures/"+child+"/"+PRIMARY_ARTIFACTS[procedure],
                "sha256": prefix*64, "bytes": 4_000_000}
        result["steps"].append(row)
    for index in range(change_count):
        row = result["steps"][index % len(procedures)]
        row["changes"].append({"pointer": "/records/"+str(index), "kind": "changed", "before_type": "string", "after_type": "string",
            "before": ("\x00" if controls else "旧")*160, "after": ("\x1f" if controls else "新")*160,
            "metadata": False, "preview_truncated": False})
        row["observed_differences"] += 1
        row["substantive_differences"] += 1
    return result


class ModelComparisonReviewTests(unittest.TestCase):
    def assert_provenance(self, original, adapted):
        self.assertEqual(adapted["current_id"], original["current_id"])
        self.assertEqual(adapted["baseline_id"], original["baseline_id"])
        self.assertEqual(len(adapted["steps"]), len(original["steps"]))
        for before, after in zip(original["steps"], adapted["steps"]):
            self.assertEqual(after["procedure_id"], before["procedure_id"])
            for side in ("current_source", "baseline_source"):
                for key in ("child_request_id", "path", "sha256", "bytes"):
                    self.assertEqual(after[side][key], before[side][key])

    def test_all_eight_primary_formats_preserve_valid_four_step_source_identities(self):
        procedures = list(PRIMARY_ARTIFACTS)
        for group in (procedures[:4], procedures[4:]):
            with self.subTest(group=group):
                result = comparison_fixture(group)
                adapted = _comparison_for_model(result)
                self.assertLessEqual(len(json.dumps(adapted, ensure_ascii=False)), 7600)
                self.assert_provenance(result, adapted)
                self.assertEqual(adapted["model_view"]["omitted_steps"], 0)

    def test_control_escape_and_maximum_preview_budget_never_mutates_source_counts(self):
        result = comparison_fixture(controls=True)
        original = copy.deepcopy(result)
        adapted = _comparison_for_model(result)
        self.assertLessEqual(len(json.dumps(adapted, ensure_ascii=False)), 7600)
        self.assert_provenance(result, adapted)
        self.assertEqual(adapted["totals"], original["totals"])
        self.assertEqual(result, original)
        shown = sum(len(row["changes"]) for row in adapted["steps"])
        self.assertEqual(shown+adapted["model_view"]["omitted_change_previews"], 100)
        self.assertTrue(adapted["model_view"]["warning_details_shortened"])

    def test_eight_step_stress_keeps_all_sources_when_metadata_fits(self):
        result = comparison_fixture(list(PRIMARY_ARTIFACTS), change_count=0)
        # Eight steps exceed the actual routine limit of four, but exercise the
        # adapter with a compact future-shaped fixture whose source data fits.
        for row in result["steps"]:
            for key in ("metadata_differences", "substantive_differences", "counts_are_lower_bounds", "comparison_complete", "metadata_only", "input_reports_partial", "omitted_observed_differences", "warning"):
                row.pop(key, None)
        adapted = _comparison_for_model(result)
        self.assertLessEqual(len(json.dumps(adapted, ensure_ascii=False)), 7600)
        self.assert_provenance(result, adapted)


class ComparisonToolAPIReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".tools-test-comparison-api-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(data_dir=Path(self.temporary.name)/"data", api_token="comparison-api-fixture-only")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.engine = Engine(self.settings, self.store, NoModel())
        self.app = Application(self.settings, self.store, self.engine)

    def request(self, url, *, auth=True, origin=None, method="GET"):
        parsed = urlsplit(url)
        env = {"HTTP_HOST": "localhost:8080", "REQUEST_METHOD": method, "PATH_INFO": parsed.path,
            "QUERY_STRING": parsed.query, "wsgi.input": io.BytesIO(b""), "CONTENT_LENGTH": "0"}
        if auth:
            env["HTTP_AUTHORIZATION"] = "Bearer "+self.settings.api_token
        if origin:
            env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            response.update(status=int(status.split()[0]), headers=dict(headers))
        response["value"] = json.loads(b"".join(self.app(env, start)))
        return response

    def test_tool_forwards_explicit_ids_and_defaults_to_latest_completed(self):
        fixture = comparison_fixture(change_count=4)
        with mock.patch.object(self.engine.routines, "compare", return_value=fixture) as compare:
            explicit = self.engine.execute_internal("routine_read", {"action": "changes", "current_id": "current", "baseline_id": "baseline"})
            self.assertTrue(explicit["ok"])
            compare.assert_called_once_with("current", "baseline")
        with mock.patch.object(self.engine.routines, "latest_completed_id", return_value="older-completed"), mock.patch.object(self.engine.routines, "compare", return_value=fixture) as compare:
            selected = self.engine.execute_internal("routine_read", {"action": "changes"})
            self.assertTrue(selected["ok"])
            compare.assert_called_once_with("older-completed", None)

    def test_invalid_tool_arguments_are_rejected_without_comparing_or_scheduling(self):
        invalid = [None, [], {}, {"action": "run"}, {"action": []}, {"action": "changes", "execute": True},
            {"action": "changes", "current_id": None}, {"action": "changes", "current_id": ""},
            {"action": "changes", "current_id": "x"*97}, {"action": "changes", "baseline_id": 4},
            {"action": "status", "current_id": "x"}, {"action": "latest", "baseline_id": "x"}]
        with mock.patch.object(self.engine.routines, "compare", side_effect=AssertionError("Invalid request cannot compare")), mock.patch.object(self.engine.routines, "enqueue", side_effect=AssertionError("Read cannot enqueue")), mock.patch.object(self.engine.routines, "tick", side_effect=AssertionError("Read cannot execute")):
            for args in invalid:
                with self.subTest(args=args):
                    self.assertFalse(self.engine.execute_internal("routine_read", args)["ok"])

    def test_tool_absence_and_unknown_ids_are_honest(self):
        unavailable = self.engine.execute_internal("routine_read", {"action": "changes"})
        self.assertEqual(unavailable["state"], "current_unavailable")
        self.assertIsNone(unavailable["current_id"])
        self.assertIsNone(unavailable["baseline_id"])
        self.assertIsNone(unavailable["totals"])
        self.assertEqual(unavailable["steps"], [])
        self.assertEqual(unavailable["model_view"]["omitted_change_previews"], 0)
        self.assertEqual(unavailable["model_view"]["omitted_steps"], 0)
        self.assertIn("No completed routine", unavailable["warnings"][0])
        self.assertLess(len(json.dumps(unavailable, ensure_ascii=False)), 7600)
        self.assertFalse(self.engine.execute_internal("routine_read", {"action": "changes", "current_id": "unknown"})["ok"])

    def test_latest_completed_lookup_is_not_limited_by_display_history(self):
        definition = self.engine.routines.save("Old completed", ["business_snapshot"])
        self.engine.routines.enqueue(definition["id"], "completed-old")
        self.engine.routines.tick()
        with self.engine.routines.database() as db:
            original = dict(db.execute("SELECT * FROM routine_runs WHERE id='completed-old'").fetchone())
            for index in range(101):
                row = {**original, "id": "newer-failed-"+str(index), "status": "failed", "created_at": original["created_at"]+index+1}
                db.execute("INSERT INTO routine_runs VALUES("+",".join("?" for _ in row)+")", tuple(row.values()))
        self.assertNotIn("completed-old", {row["id"] for row in self.engine.routines.runs()})
        self.assertEqual(self.engine.routines.latest_completed_id(), "completed-old")
        result = self.engine.execute_internal("routine_read", {"action": "changes"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["current_id"], "completed-old")
        self.assertEqual(result["state"], "first_run")

    def test_changes_api_preserves_authentication_same_origin_and_read_only_method(self):
        url = "/api/routines/runs/current/changes?baseline=older"
        with mock.patch.object(self.app.routines, "compare", return_value=comparison_fixture(change_count=1)) as compare:
            self.assertEqual(self.request(url, auth=False)["status"], 401)
            self.assertEqual(self.request(url, origin="https://other.example")["status"], 403)
            self.assertEqual(self.request(url, method="POST")["status"], 404)
            compare.assert_not_called()
            response = self.request(url)
            self.assertEqual(response["status"], 200)
            compare.assert_called_once_with("current", "older")

    def test_changes_api_rejects_blank_unknown_and_duplicate_query_parameters(self):
        invalid = ["baseline=", "baseline=older&baseline=", "baseline=&baseline=older", "baseline=a&baseline=b", "unknown=", "unknown=a", "baseline"]
        with mock.patch.object(self.app.routines, "compare", side_effect=AssertionError("Bad query cannot compare")):
            for query in invalid:
                with self.subTest(query=query):
                    self.assertEqual(self.request("/api/routines/runs/current/changes?"+query)["status"], 400)

    def test_changes_api_unknown_receipts_are_not_fabricated(self):
        self.assertEqual(self.request("/api/routines/runs/unknown/changes")["status"], 404)


if __name__ == "__main__":
    unittest.main()
