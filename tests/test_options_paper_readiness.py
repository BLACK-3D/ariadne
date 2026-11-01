"""Generated metadata fixtures; no market quotes, account operations or returns."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Inexact, ROUND_UP, localcontext
import json
import unittest
from unittest.mock import patch

from ariadne import options_paper_readiness as readiness

NOW = "2026-11-01T12:00:00+00:00"


def window(number=0, *, verified=False, path=None):
    return {"catalog_path": path or "market-data/options/archives/generated-" + str(number) + "/catalog.json",
            "catalog_sha256": format(number + 1, "064x"), "completion_sha256": format(number + 100, "064x"),
            "start": "2011-03-23", "end": "2011-12-30", "sessions": 197,
            **{name: verified for name in readiness.PROVENANCE_FIELDS}}


def root(symbol="IWM", *, windows=None, spot=None):
    return {"symbol": symbol, "windows": [window()] if windows is None else windows, "dated_spot": spot}


def index(*roots):
    return {"schema": 1, "roots": list(roots or [root()])}


def spot(price="49.10"):
    return {"price": price, "source_date": "2026-10-30", "received_at": "2026-10-31T10:00:00+00:00", "source_sha256": "a" * 64}


def paper(**changes):
    result = {"account_id": "existing-paper-generated", "schema": 4, "symbol": "F", "source_id": "onclick_f_eod_v1",
              "source_enabled": True, "policy_id": "f_stress_breakdown_put_v1", "cash": "450.50", "monthly_committed": "51.30",
              "position_present": True, "pending_present": False, "hold_present": False,
              "context": {"status": "stress_ready", "source_session": "2026-10-30", "published_at": "2026-10-31T10:00:00+00:00", "label": "stress_unwind"}}
    result.update(changes)
    return result


class PaperReadinessTests(unittest.TestCase):
    def build(self, value=None, account=None, now=NOW):
        return readiness.build_readiness(value, paper=account, checked_at=now)

    def rejects(self, value, account=None):
        with self.assertRaises(readiness.ReadinessError):
            self.build(value, account)

    def test_missing_inventory_preserves_existing_shared_view_without_authority(self):
        result = self.build(account=paper())
        self.assertEqual(result["inventory_status"], "unconfigured")
        self.assertEqual(result["configured_active_paper_roots"], ["F"])
        self.assertEqual(result["roots"], [])
        self.assertEqual(result["shared_risk"]["monthly_remaining"], "448.70")
        self.assertEqual(result["shared_risk"]["cash"], "450.50")
        self.assertEqual(result["shared_risk"]["new_funding"], "0")
        self.assertFalse(result["shared_risk"]["monthly_budget_reset"])
        self.assertFalse(result["entry_authorized"])
        self.assertFalse(result["source_activation_authorized"])

    def test_no_account_never_suggests_funding_or_a_new_paper_root(self):
        result = self.build(index(root("F"), root("IWM", windows=[])))
        self.assertEqual(result["configured_active_paper_roots"], [])
        self.assertIsNone(result["shared_risk"]["cash"])
        self.assertIsNone(result["shared_risk"]["monthly_remaining"])
        self.assertEqual(result["shared_risk"]["additional_accounts_created"], 0)
        self.assertTrue(all("unsupported_active_paper_root" in row["blockers"] for row in result["roots"]))

    def test_all_true_catalog_claims_cannot_qualify_another_feed_or_trade(self):
        value = index(root("IWM", windows=[window(verified=True)], spot=spot()))
        result = self.build(value, paper())
        row = result["roots"][0]
        self.assertTrue(all(row["declared_windows"][0][key] for key in readiness.PROVENANCE_FIELDS))
        self.assertTrue(row["provenance_flags_are_declarations"])
        self.assertFalse(row["catalogs_verified_by_this_module"])
        self.assertFalse(row["entry_authorized"])
        self.assertFalse(row["execution_qualified"])
        self.assertFalse(row["current_eligibility_established"])
        self.assertIn("no_qualified_current_options_source", row["blockers"])
        self.assertFalse(result["additional_active_paper_roots_supported"])

    def test_f_policy_is_configured_experimental_even_when_disabled_or_held(self):
        result = self.build(index(root("F")), paper(source_enabled=False, hold_present=True))
        row = result["roots"][0]
        self.assertEqual(result["configured_active_paper_roots"], ["F"])
        self.assertTrue(row["configured_active_paper_root"])
        self.assertEqual(row["current_options_source"], "configured_experimental_date_only_F")
        self.assertIn("configured_source_disabled", row["blockers"])
        self.assertIn("existing_account_hold", row["blockers"])
        self.assertFalse(row["entry_authorized"])
        self.assertFalse(result["active_paper"]["execution_qualified"])

    def test_no_inference_from_ticker_and_no_missing_price_substitution(self):
        result = self.build(index(root("VIX"), root("AAPL", windows=[])), paper())
        for row in result["roots"]:
            self.assertEqual(row["current_price_status"], "current_price_unmeasured")
            self.assertIsNone(row["below_100_at_saved_spot"])
            self.assertIsNone(row["dated_spot"])
            self.assertFalse(row["asset_type_inferred_from_symbol"])
            self.assertFalse(row["configured_active_paper_root"])

    def test_dated_under100_and_over100_remain_observations_only(self):
        result = self.build(index(root("AAA", spot=spot("99.99999999")), root("BBB", windows=[], spot=spot("100"))))
        self.assertTrue(result["roots"][0]["below_100_at_saved_spot"])
        self.assertFalse(result["roots"][1]["below_100_at_saved_spot"])
        for row in result["roots"]:
            self.assertEqual(row["current_price_status"], "dated_spot_supplied")
            self.assertFalse(row["current_eligibility_established"])

    def test_overlapping_windows_are_not_summed_or_called_continuous(self):
        result = self.build(index(root(windows=[window(1), window(2)])))
        row = result["roots"][0]
        self.assertEqual(row["declared_window_count"], 2)
        self.assertFalse(row["continuous_history_established"])
        self.assertNotIn("total_sessions", row)
        self.assertNotIn("pnl", result)
        self.assertFalse(result["selection_performed"])

    def test_inputs_and_outputs_do_not_alias(self):
        value, account = index(), paper()
        original = deepcopy((value, account))
        result = self.build(value, account)
        self.assertEqual((value, account), original)
        result["roots"][0]["declared_windows"][0]["catalog_path"] = "changed"
        result["active_paper"]["context"]["label"] = "changed"
        self.assertEqual((value, account), original)

    def test_complete_maximum_metadata_inventory_fits_output_bound(self):
        symbols = ["A" + chr(65 + n) for n in range(16)]
        value = index(*(root(symbol, windows=[window(n * 2, path="a" * (127 - len(str(n * 2))) + "/" + str(n * 2)),
                                             window(n * 2 + 1, path="b" * (127 - len(str(n * 2 + 1))) + "/" + str(n * 2 + 1))], spot=spot())
                        for n, symbol in enumerate(symbols)))
        for item in value["roots"]:
            item["dated_spot"].update(price="999999.99999999", received_at="2026-10-31T10:00:00.123456+00:00")
        result = self.build(value, paper(account_id="a" * 128, cash="1000000000.00000000"))
        self.assertEqual(result["declared_root_count"], 16)
        self.assertEqual(result["declared_window_count"], 32)
        self.assertLessEqual(len(readiness._encoded(result, readiness.MAX_OUTPUT_BYTES)), 32768)

    def test_root_and_window_limits_are_global_not_per_symbol(self):
        self.rejects(index(*(root("A" + chr(65 + n), windows=[]) for n in range(17))))
        self.rejects(index(root("AAA", windows=[window(n) for n in range(17)]),
                           root("BBB", windows=[window(n + 17) for n in range(16)])))

    def test_duplicate_and_conflicting_coverage_is_rejected(self):
        self.rejects(index(root("IWM"), root("IWM", windows=[])))
        self.rejects(index(root(windows=[window(), window()])))
        other = window(1, path=window()["catalog_path"])
        self.rejects(index(root("AAA"), root("BBB", windows=[other])))

    def test_exact_schemas_and_provenance_booleans(self):
        for mutate in (lambda x: x.update(extra=True), lambda x: x["roots"][0].update(feed_qualified=True),
                       lambda x: x["roots"][0]["windows"][0].update(side_sizes_verified="true"),
                       lambda x: x["roots"][0]["windows"][0].update(sessions=True)):
            value = index()
            mutate(value)
            self.rejects(value)
        value = index(); value["schema"] = True
        self.rejects(value)

    def test_canonical_symbols_paths_hashes_and_no_drive_paths(self):
        for symbol in ("iwm", "BRK.B", "F1", "*", "../F", "A" * 7):
            self.rejects(index(root(symbol)))
        for path in ("E:/catalog.json", "../catalog.json", "/catalog.json", "a\\catalog.json", "a//b", "a/./b", "a" * 129):
            value = index(); value["roots"][0]["windows"][0]["catalog_path"] = path
            self.rejects(value)
        value = index(); value["roots"][0]["windows"][0]["catalog_sha256"] = "A" * 64
        self.rejects(value)

    def test_invalid_dates_counts_and_future_windows(self):
        for change in ({"start": "2011-12-31"}, {"end": "2030-01-01"}, {"end": "2011-02-30"},
                       {"start": "1998-01-01"}, {"sessions": 0}, {"sessions": 500}):
            value = index(); value["roots"][0]["windows"][0].update(change)
            self.rejects(value)

    def test_spot_requires_exact_positive_finite_strings_and_causal_receipts(self):
        for price in ("0", "-1", "1e2", "NaN", "Infinity", "01", "1.000000001", 10, 10.0, True):
            self.rejects(index(root(spot=spot(price))))
        for change in ({"source_date": "2026-11-02"}, {"received_at": "2026-11-02T12:00:00+00:00"},
                       {"received_at": "2026-10-29T12:00:00+00:00"}, {"received_at": "2026-10-31T10:00:00"}):
            observed = spot(); observed.update(change)
            self.rejects(index(root(spot=observed)))

    def test_json_duplicate_nonfinite_size_and_structural_bounds(self):
        for raw in (b'{"schema":1,"schema":1,"roots":[]}', b'{"schema":1,"roots":NaN}', b'\xff', b" " * 65537,
                    b'[' * 1200 + b']' * 1200, b'{"schema":1,"roots":[],"n":1.5}'):
            with self.assertRaises(readiness.ReadinessError):
                readiness.load_index(raw, checked_at=NOW)
        compact = json.dumps({"schema": 1, "roots": []}).encode()
        padded = compact + b" " * (65536 - len(compact))
        self.assertEqual(readiness.load_index(padded, checked_at=NOW), {"schema": 1, "roots": []})
        recursive = []; recursive.append(recursive)
        with self.assertRaises(readiness.ReadinessError):
            readiness.validate_index({"schema": 1, "roots": recursive}, checked_at=NOW)

    def test_projection_rejects_unknown_accounts_and_changed_financial_shapes(self):
        for change in ({"schema": 3}, {"schema": True}, {"symbol": "IWM"}, {"source_id": "arbitrary"},
                       {"policy_id": "winner"}, {"monthly_committed": "500.01"}, {"cash": -1},
                       {"position_present": 1}, {"source_enabled": "true"}, {"account_id": "../other"}):
            self.rejects(index(), paper(**change))
        account = paper(); account["extra"] = True
        self.rejects(index(), account)

    def test_context_projection_never_authorizes_and_rejects_future_inconsistency(self):
        result = self.build(index(root("F")), paper())
        self.assertFalse(result["active_paper"]["entry_authorized"])
        self.assertTrue(result["active_paper"]["runtime_permissions_and_full_entry_gates_required"])
        for change in ({"label": "recovery_growth"}, {"published_at": "2026-11-02T00:00:00+00:00"},
                       {"source_session": None}, {"status": "feed_trusted"}):
            account = paper(); account["context"].update(change)
            self.rejects(index(), account)
        account = paper(context={"status": "missing", "source_session": None, "published_at": None, "label": None})
        self.assertFalse(self.build(index(root("F")), account)["entry_authorized"])

    def test_checked_clock_must_be_aware_and_normalizes_to_utc(self):
        for now in ("2026-11-01T12:00:00", "2026-11-01 12:00:00+00:00", "2026-11-01T12:00:00.1234567+00:00", datetime(2026, 11, 1), None, 123):
            with self.assertRaises(readiness.ReadinessError):
                self.build(now=now)
        self.assertEqual(self.build(now="2026-11-01T06:00:00-06:00")["checked_at"], NOW)
        self.assertEqual(self.build(now=datetime(2026, 11, 1, 12, tzinfo=timezone.utc))["checked_at"], NOW)

    def test_decimal_caller_context_cannot_change_shared_cap_view(self):
        with localcontext() as context:
            context.prec = 2; context.rounding = ROUND_UP; context.traps[Inexact] = True
            result = self.build(account=paper(monthly_committed="100.99999999"))
        self.assertEqual(result["shared_risk"]["monthly_remaining"], "399.00000001")

    def test_no_io_model_or_network_needed_and_no_engine_authority(self):
        with patch("builtins.open", side_effect=AssertionError("filesystem read")), \
             patch("socket.socket", side_effect=AssertionError("network")), \
             patch("subprocess.Popen", side_effect=AssertionError("process")):
            result = self.build(index(root("F")), paper())
        self.assertEqual((result["model_calls"], result["network_calls"], result["paper_operations"]), (0, 0, 0))
        self.assertFalse(result["financial_authority"])

    def test_fixed_rule_mutation_is_not_accepted(self):
        before = deepcopy(readiness.RULE)
        try:
            readiness.RULE["risk"] = "new500perstock"
            with self.assertRaises(readiness.ReadinessError):
                self.build(index())
        finally:
            readiness.RULE.clear(); readiness.RULE.update(before)


if __name__ == "__main__":
    unittest.main()
