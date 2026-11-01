"""Generated parent-source anomalies; no source request or account activity."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_reference_projection as projection
from ariadne import options_reference_history as v1
from test_options_reference_history import records, stock_fixture, NOW, WINDOW


def replace_stock(body, metadata):
    return body, {**metadata, "sha256": hashlib.sha256(body).hexdigest()}


def insert_row(body, row):
    lines = body.decode().splitlines()
    return (lines[0]+"\n"+"\n".join(sorted(lines[1:]+[row], key=lambda line: line.split(",")[0]))+"\n").encode()


def parent_fixture():
    body, metadata = stock_fixture()
    body = insert_row(body, "20251127,19.98765432100000000,unusable_holiday_source_row")
    return replace_stock(body, metadata)


def normalize(*, stock=None, profile=projection.CLOCK_PROFILES[0], supplied=None):
    body, metadata = parent_fixture() if stock is None else stock
    return projection.normalize(records() if supplied is None else supplied, stock_raw=body,
        stock_metadata=metadata, window=WINDOW, clock_profile=profile)


class ReferenceProjectionTests(unittest.TestCase):
    def test_outside_holiday_remains_failed_parent_anomaly_with_no_context_or_option_price_role(self):
        body, metadata = parent_fixture(); before = deepcopy((body, metadata))
        facts = projection.validate_reference(body, metadata, WINDOW)
        self.assertEqual((body, metadata), before)
        self.assertEqual(facts["global_source_calendar_quality"], "FAILED")
        self.assertEqual(facts["global_calendar_anomaly_count"], 1)
        anomaly = facts["global_calendar_anomalies"][0]
        self.assertEqual(anomaly["source_date"], "2025-11-27")
        self.assertEqual(anomaly["raw_close"], "19.98765432100000000")
        self.assertEqual(anomaly["role"], "unusable_out_of_scope_source_anomaly")
        self.assertFalse(anomaly["used_as_option_price"])
        self.assertFalse(anomaly["used_as_context_price"])
        self.assertEqual(facts["stock_sha256"], metadata["sha256"])
        self.assertNotEqual(facts["stock_sha256"], facts["derived_stock_sha256"])
        self.assertEqual(len(facts["canonical_closes"]), 125)
        self.assertNotIn("2025-11-27", facts["canonical_closes"])
        with self.assertRaises(ValueError): v1.validate_reference(body, metadata, WINDOW)

    def test_projection_preserves_original_decimal_text_until_unchanged_v1_quantization(self):
        dataset, audit = normalize()
        derived = ("date,close\n"+"".join(day.strftime("%Y%m%d")+",20.12345678500000000\n" for day in projection.DAYS)).encode()
        self.assertEqual(audit["derived_stock_sha256"], hashlib.sha256(derived).hexdigest())
        self.assertEqual(audit["projection"]["projected_rows"], 125)
        self.assertFalse(audit["projection"]["derived_csv_is_http_response"])
        self.assertFalse(audit["projection"]["original_parent_bytes_changed"])
        self.assertTrue(audit["projection"]["original_close_text_preserved_in_projection"])
        self.assertEqual(len(audit["stock_precision_rows"]), 125)
        self.assertTrue(all(row["raw_close"] == "20.12345678500000000" and row["canonical_close"] == "20.12345678"
            and row["role"] == "option_reference" for row in audit["stock_precision_rows"]))
        self.assertEqual(dataset["snapshots"][0]["underlying_price"], "20.12345678")

    def test_changing_unusable_outside_price_changes_source_evidence_but_never125snapshots(self):
        first, a = normalize(); body, metadata = parent_fixture()
        body, metadata = replace_stock(body.replace(b"19.98765432100000000", b"999.12345678900000000"), metadata)
        second, b = normalize(stock=(body, metadata))
        self.assertEqual(first["snapshots"], second["snapshots"])
        self.assertEqual(a["derived_stock_sha256"], b["derived_stock_sha256"])
        self.assertNotEqual(a["parent_raw_sha256"], b["parent_raw_sha256"])
        self.assertNotEqual(first["source"]["sha256"], second["source"]["sha256"])
        self.assertNotEqual(a["source_manifest_sha256"], b["source_manifest_sha256"])

    def test_changing_unused_regular_row_or_extra_field_is_bound_but_not_context_data(self):
        first, a = normalize(); body, metadata = parent_fixture()
        changed = body.replace(b"20260413,20.12345678500000000,kept", b"20260413,21.11111111111111111,changed")
        second, b = normalize(stock=replace_stock(changed, metadata))
        self.assertEqual(first["snapshots"], second["snapshots"])
        self.assertNotEqual(a["parent_raw_sha256"], b["parent_raw_sha256"])
        self.assertEqual(a["derived_stock_sha256"], b["derived_stock_sha256"])
        self.assertFalse(b["projection"]["excluded_rows_used_as_context"])

    def test_inclusive_range_holiday_is_rejected_even_though_not_in_days_set(self):
        body, metadata = parent_fixture()
        for row in ("20260525,20.1,in_range_Memorial_Day", "20260704,20.2,in_range_Saturday"):
            bad = insert_row(body, row)
            with self.subTest(row=row), self.assertRaises(ValueError): projection.validate_reference(*replace_stock(bad, metadata), WINDOW)

    def test_duplicate_future_and_malformed_parent_dates_anywhere_cannot_be_quarantined(self):
        body, metadata = parent_fixture()
        for row in ("20251127,20.1,duplicate_outside", "20260504,20.2,duplicate_inside", "20261102,20.2,future_outside",
                    "20250230,20.2,invalid_calendar", "bad_date,20.2,invalid_text"):
            bad = insert_row(body, row)
            with self.subTest(row=row), self.assertRaises(ValueError): projection.validate_reference(*replace_stock(bad, metadata), WINDOW)

    def test_invalid_positive_price_contract_applies_to_unused_anomaly_and_regular_rows(self):
        body, metadata = parent_fixture()
        for bad_price in (b"0", b"-1", b"NaN", b"Infinity", b"1e1", b"20.\"", b" 20", b"1000001"):
            bad = body.replace(b"19.98765432100000000", bad_price)
            with self.subTest(price=bad_price), self.assertRaises(ValueError): projection.validate_reference(*replace_stock(bad, metadata), WINDOW)
        bad = body.replace(b"20260413,20.12345678500000000", b"20260413,0")
        with self.assertRaises(ValueError): projection.validate_reference(*replace_stock(bad, metadata), WINDOW)

    def test_missing_required_day_whole_hash_bad_metadata_and_malformed_columns_reject(self):
        body, metadata = parent_fixture()
        missing = b"\n".join(line for line in body.split(b"\n") if not line.startswith(b"20260504,"))
        with self.assertRaises(ValueError): projection.validate_reference(*replace_stock(missing, metadata), WINDOW)
        with self.assertRaises(ValueError): projection.validate_reference(body, {**metadata, "sha256": "0"*64}, WINDOW)
        with self.assertRaises(ValueError): projection.validate_reference(body, {**metadata, "basis": "unadjusted"}, WINDOW)
        for bad in (body.replace(b"date,close,ignored_original_field", b"date,close,close", 1),
                    body.replace(b",kept", b",kept,too_many", 1), body+b"\x00", b"\xff"):
            with self.assertRaises(ValueError): projection.validate_reference(*replace_stock(bad, metadata), WINDOW)

    def test_original_parent_provenance_and_derived_provenance_remain_distinct(self):
        body, metadata = parent_fixture(); dataset, audit = normalize(stock=(body, metadata))
        self.assertEqual(audit["parent_stock_metadata"], metadata)
        self.assertEqual(audit["projection"]["parent_stock_metadata"], metadata)
        derived_meta = audit["source_manifest"]["v1_actual_source_manifest"]["stock_metadata"]
        self.assertEqual(derived_meta["received_at"], metadata["received_at"])
        self.assertEqual(derived_meta["url"], metadata["url"])
        self.assertEqual(derived_meta["sha256"], audit["derived_stock_sha256"])
        self.assertIn("DERIVED", derived_meta["provider"])
        self.assertIn("not an HTTP response", derived_meta["usage"])
        self.assertIn("SCOPE PROJECTION", dataset["source"]["usage"])
        self.assertIn("FAILED", dataset["source"]["usage"])
        self.assertIn("2025-11-27", dataset["source"]["usage"])
        self.assertFalse(audit["cross_provider_reconciliation_passed"])
        self.assertFalse(audit["owner_source_replaced"])
        self.assertFalse(audit["automatic_promotion"])
        self.assertFalse(audit["deployable"])

    def test_both_clock_profiles_bind_identical_parent_projection_and_actual_quote_evidence(self):
        first, a = normalize(); second, b = normalize(profile=projection.CLOCK_PROFILES[1])
        self.assertEqual(first["source"]["sha256"], second["source"]["sha256"])
        self.assertEqual(a["source_manifest"], b["source_manifest"])
        self.assertNotEqual(a["dataset_sha256"], b["dataset_sha256"])
        self.assertEqual(len(first["snapshots"]), 125)
        self.assertEqual(first["snapshots"][-1]["observed_at"], second["snapshots"][-1]["observed_at"])
        self.assertEqual(second["snapshots"][-1]["available_at"], "2026-10-30T13:35:00+00:00")

    def test_valid_parent_calendar_is_not_falsely_labeled_failed_or_authenticated(self):
        body, metadata = stock_fixture()
        facts = projection.validate_reference(body, metadata, WINDOW)
        self.assertEqual(facts["global_source_calendar_quality"], "NO_NONSESSION_ROWS_OBSERVED_NOT_AUTHENTICATED")
        self.assertEqual(facts["global_calendar_anomaly_count"], 0)
        self.assertFalse(facts["deployable"])

    def test_frozen_v1_quote_validation_remains_effective_through_wrapper(self):
        supplied = records(); supplied[0]["chain_raw"] = supplied[0]["chain_raw"].replace(b'"ask_size":10', b'"ask_size":true')
        with self.assertRaises(ValueError): normalize(supplied=supplied)


if __name__ == "__main__":
    unittest.main()
