"""Check offline economics arithmetic, capacity, rendering and file boundaries.

Usage: python -I -B scripts/check_venture_economics.py [--receipt RELATIVE_PATH]

Uses fresh disposable fixtures under runtime/tmp; no owner files, services,
models, microphone or network. Windows requires native directory-handle access
for the existing pinned-directory safety helpers. Optional receipts are created
exclusively inside this installation, never overwritten or silently adopted.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import venture_economics as economics


class EconomicsTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / "config/examples/venture-economics.json").read_bytes()
        self.cfg = economics.load_document(self.raw)
        fixture_parent = economics._allowed_path(ROOT / "runtime/tmp")
        fixture_parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="economics-", dir=fixture_parent)
        def cleanup_fixture():
            candidate = economics._allowed_path(temporary.name)
            if candidate.parent != fixture_parent or not candidate.name.startswith("economics-"):
                raise ValueError("Refusing cleanup outside the exact generated economics fixture.")
            temporary.cleanup()
        self.addCleanup(cleanup_fixture)
        self.workspace = Path(temporary.name)
        (self.workspace / "input.json").write_bytes(self.raw)

    def test_revised_eight_client_cash_and_labor_math(self):
        row = economics.calculate(self.cfg)["offers"][1]
        self.assertEqual(row["revenue"], "4000.00")
        self.assertEqual(row["payment_fees"], "120.00")
        self.assertEqual(row["variable_cash_cost"], "80.00")
        self.assertEqual(row["cash_surplus_before_owner_compensation_and_personal_taxes"], "3700.00")
        self.assertEqual(row["total_owner_hours"], "44.00")
        self.assertEqual(row["owner_time_value"], "1540.00")
        self.assertEqual(row["economic_surplus_before_personal_taxes"], "2160.00")
        self.assertEqual(row["minimum_clients_economic_target"], 8)
        self.assertEqual(row["additional_fixed_cash_cost_headroom_for_economic_target"], "160.00")

    def test_baseline_cash_target_is_not_economic_profit_target(self):
        row = economics.calculate(self.cfg)["offers"][0]
        self.assertEqual(row["minimum_clients_cash_target"], 7)
        self.assertEqual(row["minimum_clients_economic_target"], 20)
        self.assertTrue(row["cash_target_met_under_assumptions"])
        self.assertFalse(row["economic_target_met_under_assumptions"])
        self.assertFalse(row["capacity"][1]["economic_target_clients_fit"])

    def test_capacity_uses_full_hours_not_rounded_display(self):
        row = economics.calculate(self.cfg)["offers"][1]
        self.assertFalse(row["capacity"][0]["assumed_clients_fit"])
        self.assertTrue(row["capacity"][1]["assumed_clients_fit"])
        self.assertEqual(row["capacity"][0]["maximum_clients_under_assumptions"], 7)

    def test_nine_client_cushion_survives_one_loss(self):
        rows = economics.calculate(self.cfg)["offers"]
        self.assertEqual(rows[1]["one_client_churn"]["economic_surplus_before_personal_taxes"], "1825.00")
        self.assertFalse(rows[1]["one_client_churn"]["economic_target_met_under_assumptions"])
        self.assertEqual(rows[2]["economic_surplus_before_personal_taxes"], "2495.00")
        self.assertTrue(rows[2]["one_client_churn"]["economic_target_met_under_assumptions"])

    def test_unknown_is_not_zero_or_actual_measurement(self):
        report = economics.calculate(self.cfg)
        self.assertFalse(report["actual_costs_complete"])
        self.assertIsNone(report["actual_system_cost_monthly"])
        self.assertIsNone(report["other_fixed_cost_monthly"])
        self.assertEqual(report["system_cost_basis"], "budget_assumption_not_measured")

    def test_explicit_actual_cost_replaces_budget(self):
        self.cfg["actual_system_cost_monthly"] = 80
        self.cfg["other_fixed_cost_monthly"] = 20
        report = economics.calculate(self.cfg)
        self.assertTrue(report["actual_costs_complete"])
        self.assertEqual(report["fixed_cash_cost_used"], "100.00")
        self.assertEqual(report["system_cost_basis"], "owner_supplied_actual")

    def test_zero_clients_still_incur_fixed_cash_and_time(self):
        self.cfg["offers"][0]["clients"] = 0
        row = economics.calculate(self.cfg)["offers"][0]
        self.assertEqual(row["revenue"], "0.00")
        self.assertEqual(row["cash_surplus_before_owner_compensation_and_personal_taxes"], "-100.00")
        self.assertEqual(row["economic_surplus_before_personal_taxes"], "-520.00")

    def test_nonpositive_contribution_does_not_divide_or_invent_target(self):
        self.cfg["offers"][0]["price"] = 0
        row = economics.calculate(self.cfg)["offers"][0]
        self.assertIsNone(row["minimum_clients_cash_target"])
        self.assertIsNone(row["minimum_clients_economic_target"])
        self.assertFalse(row["capacity"][0]["cash_target_clients_fit"])

    def test_zero_need_requires_no_clients_even_with_nonpositive_contribution(self):
        self.cfg["system_budget_monthly"] = 0
        self.cfg["actual_system_cost_monthly"] = 0
        self.cfg["other_fixed_cost_monthly"] = 0
        self.cfg["monthly_profit_target"] = 0
        self.cfg["fixed_nonbillable_hours_monthly"] = 0
        self.cfg["offers"][0]["price"] = 0
        self.cfg["offers"][0]["clients"] = 0
        row = economics.calculate(self.cfg)["offers"][0]
        self.assertEqual(row["minimum_clients_cash_target"], 0)
        self.assertEqual(row["minimum_clients_economic_target"], 0)
        self.assertTrue(row["cash_target_met_under_assumptions"])
        self.assertTrue(row["economic_target_met_under_assumptions"])
        self.assertTrue(row["capacity"][0]["cash_target_clients_fit"])
        self.assertTrue(row["capacity"][0]["economic_target_clients_fit"])

    def test_fixed_hours_alone_may_exceed_capacity(self):
        self.cfg["fixed_nonbillable_hours_monthly"] = 100
        row = economics.calculate(self.cfg)["offers"][1]
        self.assertTrue(row["capacity"][0]["fixed_hours_alone_exceed_capacity"])
        self.assertEqual(row["capacity"][0]["maximum_clients_under_assumptions"], 0)

    def test_invalid_json_fields_types_counts_and_literals(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999999}', b'{"a":1e-999999}', b'[' * 1100):
            with self.subTest(raw=raw[:50]), self.assertRaises((ValueError, json.JSONDecodeError)):
                economics.load_document(raw)
        for mutate in (lambda cfg: cfg.update(extra=1), lambda cfg: cfg.update(schema=True),
                       lambda cfg: cfg.update(system_budget_monthly=True), lambda cfg: cfg.update(hourly_value="35"),
                       lambda cfg: cfg.update(weekly_hours_profiles=[10, 10]),
                       lambda cfg: cfg["offers"][0].update(clients=economics.D("1.5")),
                       lambda cfg: cfg["offers"][0].update(hours_per_client=0),
                       lambda cfg: cfg["offers"][0].update(payment_fee_percent=101)):
            cfg = copy.deepcopy(self.cfg)
            mutate(cfg)
            with self.assertRaises(ValueError):
                economics.calculate(cfg)

    def test_html_escapes_all_untrusted_display_text_and_denies_network(self):
        self.cfg["label"] = '<img src="https://example.com" onerror="alert(1)">'
        self.cfg["offers"][0]["name"] = '</h2><script>alert(1)</script>'
        rendered = economics.render_html(economics.calculate(self.cfg))
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<script", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("default-src 'none'", rendered)
        self.assertNotIn("<form", rendered)

    def test_exclusive_verified_publication_without_network_or_subprocess(self):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("Offline")), mock.patch("subprocess.Popen", side_effect=AssertionError("No process")):
            receipt = economics.run("input.json", "report", self.workspace)
        self.assertTrue(receipt["ok"])
        for artifact in receipt["artifacts"]:
            raw = (self.workspace / artifact["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
        with self.assertRaises(FileExistsError):
            economics.run("input.json", "report", self.workspace)

    def test_partial_output_is_not_adopted_or_replayed(self):
        (self.workspace / "partial").mkdir()
        (self.workspace / "partial/economics.json").write_text("uncertain")
        with self.assertRaises(FileExistsError):
            economics.run("input.json", "partial", self.workspace)
        self.assertEqual((self.workspace / "partial/economics.json").read_text(), "uncertain")

    def test_relative_path_validation_prevents_escape_and_reserved_drive(self):
        for name in ("../escape", "C:/escape", "//server/escape", "foo:bar", "NUL", "a/../b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                economics.run("input.json", name, self.workspace)

    def test_hardlinked_input_is_rejected(self):
        linked = self.workspace / "linked.json"
        os.link(self.workspace / "input.json", linked)
        with self.assertRaises(ValueError):
            economics.run("linked.json", "rejected", self.workspace)

    def test_overlarge_input_never_creates_output(self):
        (self.workspace / "large.json").write_bytes(b" " * (economics.MAX_INPUT + 1))
        with self.assertRaises(ValueError):
            economics.run("large.json", "rejected", self.workspace)
        self.assertFalse((self.workspace / "rejected").exists())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", help="New installation-relative JSON receipt; existing files are not replaced")
    args = parser.parse_args()
    sources = ("scripts/venture_economics.py", "scripts/check_venture_economics.py", "config/examples/venture-economics.json")
    source_hashes = lambda: {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    before = source_hashes()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EconomicsTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    stable = before == source_hashes()
    receipt = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "passed": result.wasSuccessful() and stable,
               "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "skipped": len(result.skipped), "source_stable": stable, "sha256": before,
               "model_calls": 0, "network_calls": 0, "owner_data_modified": False}
    if args.receipt:
        guard = object.__new__(economics.ToolRegistry)
        guard.root = ROOT
        destination = guard._path(args.receipt)
        with guard._directory(destination.parent, create=True):
            economics.publish_bytes(destination, (json.dumps(receipt, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
