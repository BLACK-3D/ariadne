"""Offline planning math, state integrity and approval-bound readiness records."""
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from ariadne.config import Settings
from ariadne.survival_tools import (CDC_WATER_URL, MAX_LOADS, MAX_SUPPLIES,
                                    RevisionConflict, SurvivalTools, plan)
from ariadne.tools import ToolRegistry


class SurvivalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".survival-test-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.registry = ToolRegistry(Settings(data_dir=self.directory, api_token="survival-test-token"))
        self.tools = SurvivalTools(self.registry)
        self.counter = 0

    def write(self, action, record, **extra):
        self.counter += 1
        return self.tools.execute("survival_records", {"action": action, "record": record,
                                  "request_id": f"request-{self.counter}", **extra})

    def supply(self, identifier, **changes):
        return {"id": identifier, "name": "Stored water", "category": "water", "quantity": "10", "unit": "liter", **changes}

    def task(self, identifier, **changes):
        return {"id": identifier, "title": "Inspect equipment", **changes}

    def test_exact_water_food_and_energy_with_reserves_and_efficiency(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("offline means no fetch")):
            result = plan({"people": 4, "days": 14, "battery_wh": "1000", "loads": [
                {"name": "Radio", "watts": "10", "hours_per_day": "6"},
                {"name": "Lighting", "watts": "2.5", "hours_per_day": "4", "count": 2}]})
        self.assertEqual(result["water"]["total_liters"], "211.983059904")
        self.assertEqual(result["food"]["total_kcal"], "112000")
        self.assertEqual(result["power"]["daily_wh"], "80")
        self.assertEqual(result["power"]["total_wh"], "1120")
        self.assertEqual(result["power"]["battery_usable_wh"], "720")
        self.assertEqual(result["power"]["battery_runtime_days"], "9")
        self.assertEqual(result["power"]["reserve_fraction"], "0.2")
        self.assertEqual(result["sources"][0]["url"], CDC_WATER_URL)
        self.assertTrue(any("not modeled" in item for item in result["assumptions"]))

    def test_disabled_calories_zero_demand_and_fractional_decimal_math(self):
        result = plan({"food_kcal_per_person_per_day": None, "water_liters_per_person_per_day": "0.1", "people": 3, "days": 3})
        self.assertEqual(result["water"]["total_liters"], "0.9")
        self.assertIsNone(result["food"]["total_kcal"])
        self.assertIsNone(result["power"]["battery_runtime_days"])
        self.assertTrue(any("Custom water" in item for item in result["warnings"]))
        self.assertEqual(plan({"usable_fraction": 0.8})["profile"]["usable_fraction"], "0.8")
        self.assertEqual(plan({"battery_wh": "10.000000000001", "usable_fraction": "1", "inverter_efficiency": "1"})["power"]["battery_usable_wh"], "10.000000000001")

    def test_strict_shapes_numeric_bounds_and_no_treatment_parameters(self):
        invalid = [{"people": True}, {"people": 0}, {"days": 36501}, {"battery_wh": "NaN"},
                   {"battery_wh": float("inf")}, {"battery_wh": "1e6"}, {"battery_wh": "0.0000000000001"},
                   {"battery_wh": -1}, {"usable_fraction": "0"}, {"inverter_efficiency": "1.01"},
                   {"food_kcal_per_person_per_day": 0}, {"water_liters_per_person_per_day": "0"},
                   {"loads": [{}]}, {"loads": [{"name": "Test", "watts": 1, "hours_per_day": 25}]},
                   {"loads": [{"name": "Test", "watts": 1, "hours_per_day": 1, "count": True}]},
                   {"loads": [None] * (MAX_LOADS + 1)}, {"chlorine_dose": 1}, {"output": "E:/plan.json"}]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                plan(values)
        for action in ({}, [], None, "nonexistent"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.tools.execute("survival_records", {"action": action, "record": {}, "request_id": "invalid"})

    def test_idempotency_and_expected_revision_are_durable(self):
        args = {"action": "save_profile", "record": {"people": 3}, "request_id": "original", "expected_revision": 0}
        first = self.tools.execute("survival_records", args)
        self.assertEqual(first["revision"], 1)
        self.write("save_profile", {"days": 7}, expected_revision=1)
        reopened = SurvivalTools(self.registry)
        replay = reopened.execute("survival_records", args)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["revision"], 1)
        overview = reopened.overview()
        self.assertEqual((overview["revision"], overview["profile"]["people"], overview["profile"]["days"]), (2, 3, 7))
        with self.assertRaises(ValueError):
            reopened.execute("survival_records", {**args, "record": {"people": 4}})
        with self.assertRaises(RevisionConflict) as error:
            self.write("delete_supply", {"id": "missing"}, expected_revision=1)
        self.assertEqual(error.exception.current_revision, 2)
        self.assertEqual(reopened.overview()["revision"], 2)

    def test_expiry_unknown_units_and_coverage_are_explicit(self):
        self.write("save_profile", {"people": 2, "days": 2, "water_liters_per_person_per_day": "2", "battery_wh": "100", "loads": [{"name": "Radio", "watts": "10", "hours_per_day": "2"}]})
        self.write("upsert_supply", self.supply("old", quantity="100", expires="2026-10-27"))
        self.write("upsert_supply", self.supply("today", quantity="2", expires="2026-10-28"))
        self.write("upsert_supply", self.supply("unknown", quantity="4"))
        self.write("upsert_supply", self.supply("bottles", quantity="99", unit="count"))
        self.write("upsert_supply", self.supply("spare", category="power", unit="wh", quantity="100"))
        self.write("upsert_supply", self.supply("food", category="food", unit="kcal", quantity="10000"))
        overview = self.tools.overview(as_of="2026-10-28")
        summary = overview["summary"]
        self.assertEqual(summary["water"], {"available_liters": "6", "required_liters": "8", "shortfall_liters": "2", "coverage_days": "1.5"})
        self.assertEqual(summary["food"]["coverage_days"], "2.5")
        self.assertEqual(summary["power"]["available_wh"], "144")
        self.assertEqual(summary["power"]["coverage_days"], "7.2")
        self.assertEqual(summary["expired_supply_ids"], ["old"])
        self.assertEqual(summary["unquantified_supply_ids"], ["bottles"])
        supplies = {item["id"]: item for item in overview["supplies"]}
        self.assertFalse(supplies["old"]["usable"])
        self.assertTrue(supplies["today"]["usable"])
        self.assertEqual(supplies["unknown"]["expiry_status"], "unknown")
        self.assertTrue(all(item["safety_verified"] is False for item in supplies.values()))
        self.assertTrue(any("same battery twice" in warning for warning in summary["warnings"]))
        self.assertTrue(any("cannot contribute" in warning for warning in summary["warnings"]))

    def test_task_dependency_cycles_missing_references_and_delete_rollback(self):
        self.write("upsert_task", self.task("inspect"))
        self.write("upsert_task", self.task("repair", depends_on=["inspect"]))
        overview = self.tools.overview()
        repair = next(task for task in overview["tasks"] if task["id"] == "repair")
        self.assertEqual(repair["blocked_by"], ["inspect"])
        self.assertFalse(repair["ready"])
        self.assertEqual(overview["summary"]["blocked_tasks"], 1)
        for action, record in [("upsert_task", self.task("inspect", depends_on=["repair"])),
                               ("upsert_task", self.task("missing", depends_on=["absent"])),
                               ("upsert_task", self.task("duplicate", depends_on=["inspect", "inspect"])),
                               ("delete_task", {"id": "inspect"})]:
            with self.subTest(action=action, record=record), self.assertRaises(ValueError):
                self.write(action, record)
        self.assertEqual(self.tools.overview()["revision"], 2)
        self.write("upsert_task", self.task("inspect", status="done"))
        repair = next(task for task in self.tools.overview()["tasks"] if task["id"] == "repair")
        self.assertTrue(repair["ready"])
        self.write("delete_task", {"id": "repair"})
        self.write("delete_task", {"id": "inspect"})
        self.assertEqual(self.tools.overview()["tasks"], [])

    def test_original_roadmap_is_complete_atomic_dependency_ordered_and_idempotent(self):
        args = {"action": "seed_roadmap", "record": {}, "request_id": "seed-roadmap", "expected_revision": 0}
        first = self.tools.execute("survival_records", args)
        self.assertEqual(first["revision"], 1)
        self.assertTrue(self.tools.execute("survival_records", args)["replayed"])
        overview = self.tools.overview()
        tasks = {task["id"]: task for task in overview["tasks"]}
        self.assertEqual(len(tasks), 16)
        self.assertTrue(all(task["status"] == "planned" and "Acceptance:" in task["notes"] for task in tasks.values()))
        self.assertEqual({task["domain"] for task in tasks.values() if task["ready"]}, {"water", "shelter", "tools"})
        self.assertEqual(set(tasks["roadmap-construction"]["depends_on"]), {"roadmap-shelter", "roadmap-materials", "roadmap-navigation"})
        self.assertEqual(set(tasks["roadmap-digital"]["depends_on"]), {"roadmap-power", "roadmap-education"})
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.write("seed_roadmap", {})
        self.assertEqual(self.tools.overview()["revision"], 1)

    def test_roadmap_id_collision_preserves_custom_task_without_partial_seed(self):
        self.write("upsert_task", self.task("roadmap-education", title="Owner custom learning plan", notes="Keep this exact note"))
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.write("seed_roadmap", {})
        overview = self.tools.overview()
        self.assertEqual(len(overview["tasks"]), 1)
        self.assertEqual(overview["tasks"][0]["title"], "Owner custom learning plan")
        self.assertEqual(overview["tasks"][0]["notes"], "Keep this exact note")
        self.assertEqual(overview["revision"], 1)

    def test_cancelled_mutation_and_invalid_expiry_leave_no_partial_write(self):
        calls = iter([False, True])
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.tools.execute("survival_records", {"action": "upsert_supply", "record": self.supply("cancelled"), "request_id": "cancel"}, cancelled=lambda: next(calls))
        overview = self.tools.overview()
        self.assertEqual((overview["revision"], overview["supplies"]), (0, []))
        for expiry in ("2026-02-30", "28/10/2026", None, "2026-1-01"):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                self.write("upsert_supply", self.supply("invalid", expires=expiry))
        self.assertTrue(self.tools.execute("survival_records", {"action": "upsert_supply", "record": self.supply("cancelled"), "request_id": "cancel"})["ok"])

    def test_independent_connections_cannot_overwrite_same_expected_revision(self):
        self.tools.overview()
        barrier = threading.Barrier(2)
        def change(index):
            provider = SurvivalTools(self.registry)
            barrier.wait(timeout=5)
            try:
                return provider.execute("survival_records", {"action": "save_profile", "record": {"people": index}, "request_id": f"parallel-{index}", "expected_revision": 0})
            except RevisionConflict as error:
                return error
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, (2, 3)))
        self.assertEqual(sum(isinstance(result, RevisionConflict) for result in results), 1)
        self.assertEqual(self.tools.overview()["revision"], 1)

    def test_policy_fingerprint_and_registry_reject_stale_write(self):
        # Register only this provider in the real registry for an independent
        # test of its extension contract, even before the app wiring lands.
        original = tuple(provider for provider in self.registry.extensions if not isinstance(provider, SurvivalTools))
        self.registry.extensions = (*original, self.tools)
        self.registry._extension_names.update({name: self.tools for name in ("survival_plan", "survival_records")})
        self.assertEqual(self.tools.policy("survival_plan", {}), "auto")
        self.assertEqual(self.tools.policy("survival_records", {"action": "list"}), "auto")
        self.assertEqual(self.tools.policy("survival_records", {"action": "delete_task"}), "approval")
        args = {"action": "upsert_supply", "record": self.supply("reviewed"), "request_id": "reviewed"}
        fingerprint = self.registry.fingerprint("survival_records", args)
        self.write("save_profile", {"days": 5})
        self.assertNotEqual(fingerprint, self.registry.fingerprint("survival_records", args))
        result = self.registry.execute("survival_records", args, approval_fingerprint=fingerprint)
        self.assertFalse(result["ok"])
        self.assertEqual(self.tools.overview()["supplies"], [])

    def test_bounded_storage_and_database_link_guard(self):
        with mock.patch("ariadne.survival_tools.MAX_SUPPLIES", 1):
            self.write("upsert_supply", self.supply("first"))
            with self.assertRaisesRegex(ValueError, "limit"):
                self.write("upsert_supply", self.supply("second"))
            self.write("upsert_supply", self.supply("first", quantity="12"))
        self.assertEqual(self.tools.overview()["supplies"][0]["quantity"], "12")
        link = self.directory / "database-alias"
        try:
            os.link(self.tools.path, link)
        except (OSError, NotImplementedError):
            # Windows policy may deny creating actual hard links. Keep the
            # production lstat/path guard and sqlite untouched, only supply
            # its final stat's link count to prove rejection precedes connect.
            original_stat = Path.stat
            database_path = self.tools.path
            def linked_stat(path, *args, **kwargs):
                info = original_stat(path, *args, **kwargs)
                if path == database_path:
                    return SimpleNamespace(st_mode=info.st_mode, st_nlink=2,
                                           st_file_attributes=getattr(info, "st_file_attributes", 0))
                return info
            with mock.patch.object(Path, "stat", linked_stat), mock.patch("ariadne.survival_tools.sqlite3.connect", side_effect=AssertionError("linked database must never open")):
                with self.assertRaisesRegex(ValueError, "one link"):
                    self.tools.overview()
        else:
            with self.assertRaisesRegex(ValueError, "one link"):
                self.tools.overview()
            link.unlink()

    def test_excluded_drive_is_rejected_before_open_and_defaults_are_not_shared(self):
        self.tools.path = Path("E:/never-open-survival.sqlite3")
        with self.assertRaisesRegex(ValueError, "E:"):
            self.tools.overview()
        first = plan({})
        first["profile"]["loads"].append({"untrusted": "mutated caller"})
        self.assertEqual(plan({})["profile"]["loads"], [])

    def test_overview_is_bounded_json_and_list_does_not_mutate_revision(self):
        result = self.tools.execute("survival_records", {"action": "list"})
        self.assertEqual(result["schema"], 1)
        self.assertEqual(result["revision"], 0)
        self.assertEqual(result["profile"]["food_basis"], "planning-assumption")
        self.assertEqual(result["summary"]["water"]["shortfall_liters"], "52.995764976")
        self.assertEqual(result["summary"]["open_tasks"], 0)
        self.assertTrue(json.dumps(result, allow_nan=False))
        with self.assertRaises(ValueError):
            self.tools.execute("survival_records", {"action": "list", "record": {}})
        with self.assertRaises(ValueError):
            self.write("upsert_supply", self.supply("bad", quantity=True))
        self.assertEqual(self.tools.overview()["revision"], 0)


if __name__ == "__main__":
    unittest.main()
