"""Generated date-admission evidence; no market performance or owner changes."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import research_window as scope


NOW = "2026-11-01T14:00:00+00:00"


def dataset():
    return {"as_of": "2026-11-01T13:30:00+00:00",
            "source": {"retrieved_at": "2026-11-01T13:00:00+00:00"},
            "snapshots": [{"observed_at": "2026-10-29T20:00:00+00:00", "available_at": "2026-10-29T20:15:00+00:00"},
                          {"observed_at": "2026-10-30T20:00:00+00:00", "available_at": "2026-10-30T20:15:00+00:00"}]}


def manifest():
    return {"as_of": "2026-11-01T13:30:00+00:00",
            "source": {"retrieved_at": "2026-11-01T13:00:00+00:00"},
            "selection": {"start": "2025-11-01", "end": "2026-10-30"},
            "chunks": [{"first_observed_at": "2025-11-03T21:00:00+00:00",
                        "last_observed_at": "2026-10-30T20:00:00+00:00"}]}


class ResearchWindowTests(unittest.TestCase):
    def setUp(self):
        self.window = scope.derive_window(NOW)

    def test_calendar_year_bounds_and_finalized_weekend_end(self):
        self.assertEqual((self.window.oldest, self.window.newest), ("2025-11-01", "2026-10-30"))
        self.assertEqual(self.window.anchor_day, "2026-11-01")
        self.assertEqual(self.window.rule_sha256, scope.RULE_SHA256)

    def test_anchor_is_new_york_date_not_utc_date(self):
        before_midnight = scope.derive_window("2026-11-02T04:30:00+00:00")
        after_midnight = scope.derive_window("2026-11-02T05:00:00+00:00")
        self.assertEqual(before_midnight.oldest, "2025-11-01")
        self.assertEqual(after_midnight.oldest, "2025-11-02")

    def test_leap_day_clamps_calendar_anniversary_not_365_days(self):
        leap = scope.derive_window("2024-02-29T18:00:00+00:00")
        ordinary = scope.derive_window("2025-02-28T18:00:00+00:00")
        self.assertEqual(leap.oldest, "2023-02-28")
        self.assertEqual(ordinary.oldest, "2024-02-28")

    def test_regular_close_requires_full_finalization_lag(self):
        before = scope.derive_window("2026-10-30T20:14:59.999999+00:00")
        at = scope.derive_window("2026-10-30T20:15:00+00:00")
        self.assertEqual(before.newest, "2026-10-29")
        self.assertEqual(at.newest, "2026-10-30")

    def test_early_close_and_holiday_boundary(self):
        before = scope.derive_window("2026-11-27T18:14:59+00:00")
        at = scope.derive_window("2026-11-27T18:15:00+00:00")
        self.assertEqual(before.newest, "2026-11-25")
        self.assertEqual(at.newest, "2026-11-27")

    def test_equivalent_aware_offset_clock_normalizes_to_same_window(self):
        self.assertEqual(self.window, scope.derive_window("2026-11-01T09:00:00-05:00"))
        self.assertEqual(self.window, scope.derive_window(datetime(2026,11,1,14,tzinfo=timezone.utc)))

    def test_invalid_or_naive_registration_clocks_rejected(self):
        for value in (True, False, 123, 1.5, {}, [], "bad", "2026-11-01", "2026-11-01T14:00:00", datetime(2026,11,1), date(2026,11,1)):
            with self.subTest(value=value), self.assertRaises(scope.ResearchWindowError):
                scope.derive_window(value)

    def test_metadata_is_detached_bounded_and_financially_inert(self):
        first = self.window.as_dict()
        first["oldest"] = "2008-01-01"
        self.assertEqual(self.window.as_dict()["oldest"], "2025-11-01")
        self.assertFalse(first["pre_window_option_rows_allowed"])
        self.assertEqual(first["older_benchmark_role"], "causal_context_warmup_only")
        self.assertFalse(first["source_clocks_rewritten"])

    def test_frozen_window_and_forged_bounds_policy_or_clock_rejected(self):
        with self.assertRaises(FrozenInstanceError):
            self.window.oldest = "2008-01-01"
        for value in (replace(self.window, oldest="2008-01-01"), replace(self.window, newest="2026-11-02"),
                      replace(self.window, rule_sha256="0"*64), replace(self.window, registered_at="bad")):
            with self.subTest(value=value), self.assertRaises(scope.ResearchWindowError):
                scope.require_range("2026-10-29", "2026-10-30", value)
        with self.assertRaises(scope.ResearchWindowError):
            scope.require_range("2026-10-29", "2026-10-30", self.window.as_dict())

    def test_both_inclusive_boundaries_accepted_without_clipping(self):
        value = scope.require_range("2025-11-01", "2026-10-30", self.window)
        self.assertEqual((value["start"], value["end"]), ("2025-11-01", "2026-10-30"))
        self.assertTrue(value["scope_only"])

    def test_wholly_old_mixed_or_future_ranges_rejected(self):
        for start, end in (("2008-01-02","2008-12-31"), ("2025-10-31","2026-10-30"),
                           ("2026-10-29","2026-11-01"), ("2026-11-02","2026-11-03")):
            with self.subTest(start=start,end=end), self.assertRaises(scope.ResearchWindowError):
                scope.require_range(start,end,self.window)

    def test_reversed_or_nonstrict_dates_rejected(self):
        with self.assertRaises(scope.ResearchWindowError):
            scope.require_range("2026-10-30", "2026-10-29", self.window)
        for value in (True, None, 20261030, "2026-1-01", "2026-10-30T00:00:00Z", "2026-02-29", " 2026-10-30", date(2026,10,30)):
            with self.subTest(value=value), self.assertRaises(scope.ResearchWindowError):
                scope.require_range(value,"2026-10-30",self.window)

    def test_dataset_checks_every_row_without_mutation(self):
        value = dataset()
        original = deepcopy(value)
        result = scope.require_option_dataset(value,self.window)
        self.assertEqual(value,original)
        self.assertEqual(result["snapshots_checked"],2)
        self.assertEqual(result["first_observed_date"],"2026-10-29")
        hidden_old = deepcopy(value["snapshots"][0])
        hidden_old["observed_at"]="2008-01-02T21:00:00+00:00"
        hidden_old["available_at"]="2008-01-02T21:15:00+00:00"
        value["snapshots"].insert(1,hidden_old)
        with self.assertRaises(scope.ResearchWindowError):
            scope.require_option_dataset(value,self.window)

    def test_observation_scope_uses_ny_date_not_utc_date(self):
        value = dataset()
        value["snapshots"]=[{"observed_at":"2025-11-01T00:30:00Z","available_at":"2025-11-01T00:45:00Z"}]
        with self.assertRaises(scope.ResearchWindowError):
            scope.require_option_dataset(value,self.window)
        value["snapshots"]=[{"observed_at":"2026-10-31T00:30:00Z","available_at":"2026-10-31T00:45:00Z"}]
        # Date admission only: the separate canonical validator owns session hours.
        self.assertEqual(scope.require_option_dataset(value,self.window)["last_observed_date"],"2026-10-30")

    def test_actual_receipt_after_last_session_allowed_when_already_captured(self):
        value = dataset()
        value["snapshots"][0]["available_at"]="2026-11-01T12:00:00Z"
        value["snapshots"][1]["available_at"]="2026-11-01T12:00:01Z"
        before = deepcopy(value)
        scope.require_option_dataset(value,self.window)
        self.assertEqual(value,before)

    def test_future_asof_retrieval_or_publication_rejected(self):
        for target in ("as_of", "retrieved_at", "available_at"):
            value=dataset()
            if target=="as_of":value["as_of"]="2026-11-01T14:00:01Z"
            elif target=="retrieved_at":value["source"]["retrieved_at"]="2026-11-01T14:00:01Z"
            else:value["snapshots"][0]["available_at"]="2026-11-01T14:00:01Z"
            with self.subTest(target=target),self.assertRaises(scope.ResearchWindowError):
                scope.require_option_dataset(value,self.window)

    def test_bad_capture_order_rejected(self):
        value=dataset();value["as_of"]="2026-11-01T12:00:00Z"
        with self.assertRaises(scope.ResearchWindowError):scope.require_option_dataset(value,self.window)
        value=dataset();value["snapshots"][0]["available_at"]="2026-10-29T19:59:00Z"
        with self.assertRaises(scope.ResearchWindowError):scope.require_option_dataset(value,self.window)
        value=dataset();value["snapshots"][0]["available_at"]="2026-11-01T13:01:00Z"
        with self.assertRaises(scope.ResearchWindowError):scope.require_option_dataset(value,self.window)

    def test_invalid_snapshot_clocks_and_bounded_row_count(self):
        for bad in (True,None,0,"2026-10-30T20:00:00", "x"*65):
            value=dataset();value["snapshots"][0]["observed_at"]=bad
            with self.subTest(bad=bad),self.assertRaises(scope.ResearchWindowError):
                scope.require_option_dataset(value,self.window)
        for rows in ([], {}, [dataset()["snapshots"][0]]*4001, [True]):
            value=dataset();value["snapshots"]=rows
            with self.subTest(rows_type=type(rows)),self.assertRaises(scope.ResearchWindowError):
                scope.require_option_dataset(value,self.window)

    def test_scope_does_not_filter_contract_expiry_or_assert_quote_authenticity(self):
        value=dataset()
        value["snapshots"][0]["quotes"]=[{"expiry":"2026-11-13"}]
        result=scope.require_option_dataset(value,self.window)
        self.assertTrue(result["scope_only"])
        self.assertEqual(value["snapshots"][0]["quotes"][0]["expiry"],"2026-11-13")

    def test_manifest_selection_and_all_chunk_bounds_without_mutation(self):
        value=manifest();before=deepcopy(value)
        result=scope.require_archive_manifest(value,self.window)
        self.assertEqual(result["chunks_checked"],1)
        self.assertEqual(value,before)
        value["chunks"][0]["first_observed_at"]="2025-10-31T20:00:00Z"
        with self.assertRaises(scope.ResearchWindowError):scope.require_archive_manifest(value,self.window)

    def test_mixed_manifest_rejected_without_slicing_or_relabeling(self):
        value=manifest();value["selection"]["start"]="2008-01-02"
        before=deepcopy(value)
        with self.assertRaises(scope.ResearchWindowError):scope.require_archive_manifest(value,self.window)
        self.assertEqual(value,before)

    def test_chunk_must_also_fit_own_declared_selection(self):
        value=manifest();value["selection"]={"start":"2026-10-29","end":"2026-10-30"}
        value["chunks"][0]["first_observed_at"]="2026-10-28T20:00:00Z"
        with self.assertRaises(scope.ResearchWindowError):scope.require_archive_manifest(value,self.window)

    def test_chunk_order_capture_and_count_rejected(self):
        for kind in ("reversed","overlap","future","after_capture","empty","many"):
            value=manifest()
            if kind=="reversed":value["chunks"][0].update(first_observed_at="2026-10-30T20:00:00Z",last_observed_at="2025-11-03T21:00:00Z")
            elif kind=="overlap":value["chunks"].append(deepcopy(value["chunks"][0]))
            elif kind=="future":value["chunks"][0]["last_observed_at"]="2026-11-02T21:00:00Z"
            elif kind=="after_capture":value["source"]["retrieved_at"]="2026-10-29T21:00:00Z"
            elif kind=="empty":value["chunks"]=[]
            else:value["chunks"]=[deepcopy(value["chunks"][0])]*97
            with self.subTest(kind=kind),self.assertRaises(scope.ResearchWindowError):
                scope.require_archive_manifest(value,self.window)

    def test_current_range_does_not_prove_missing_sessions_or_warmup(self):
        value=manifest()
        result=scope.require_archive_manifest(value,self.window)
        self.assertTrue(result["scope_only"])
        self.assertNotIn("complete_history",result)
        self.assertNotIn("trades",result)

    def test_non_dataset_inputs_rejected(self):
        for value in (None,True,[],{}, {"as_of":NOW,"source":None}):
            with self.subTest(value=value),self.assertRaises(scope.ResearchWindowError):
                scope.require_option_dataset(value,self.window)
            with self.subTest(value=value),self.assertRaises(scope.ResearchWindowError):
                scope.require_archive_manifest(value,self.window)


if __name__=="__main__":
    unittest.main()
