"""Generated high-precision independent-reference fixtures, no real sources."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_reference_history as reference
from ariadne import research_window

NOW = datetime(2026, 11, 1, 14, tzinfo=timezone.utc)
WINDOW = research_window.derive_window(NOW)


def raw(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def chain(day):
    expiry = reference.expiry_for(day).isoformat()
    prefix = expiry[2:].replace("-", "")
    return [{"warning": "only 1 strikes out of 2 strikes were returned. Upgrade to level 2 to get full chain."}]+[
        {"osi-key": "KO----"+prefix+side+"00020000", "ticker": "KO", "date": day.isoformat(),
         "expiration": expiry, "strike": 20, "type": "call" if side == "C" else "put",
         "bid": 0, "ask": .5, "bid_size": 0, "ask_size": 10, "volume": 0, "open_interest": 0}
        for side in ("C", "P")]


def records():
    return [{"source_date": day.isoformat(),
        "received_at": (NOW-timedelta(hours=1)+timedelta(seconds=index)).isoformat(),
        "chain_raw": raw(chain(day)), "chain_url": reference.source.chain_url(day, reference.expiry_for(day))}
        for index, day in enumerate(reference.DAYS)]


def stock_fixture():
    """Fresh metadata plus generated whole CSV with exact17-place close strings."""
    days = reference.session_dates(date(2026, 4, 13), date(2026, 10, 30))
    body = ("date,close,ignored_original_field\r\n"+"".join(day.strftime("%Y%m%d")+",20.12345678500000000,kept\r\n" for day in days)).encode()
    return body, {"provider": "Generated unknown-basis research fixture",
        "url": "https://example.com/KO.csv", "sha256": hashlib.sha256(body).hexdigest(),
        "received_at": "2026-11-01T12:00:00Z", "usage": "Generated unit fixture; never market evidence.",
        "basis": "unknown_reported_daily_close", "reconciliation_sha256": "f"*64}


def replace_stock(body, metadata):
    return body, {**metadata, "sha256": hashlib.sha256(body).hexdigest()}


def normalize(supplied=None, *, profile=reference.CLOCK_PROFILES[0], stock=None, window=WINDOW):
    body, metadata = stock_fixture() if stock is None else stock
    return reference.normalize(records() if supplied is None else supplied, stock_raw=body,
        stock_metadata=metadata, window=window, clock_profile=profile)


class ReferenceHistoryTests(unittest.TestCase):
    def test_fixed125date_predeclared_paired_cohort_preserves_prices_sizes_receipts_and_inputs(self):
        supplied = records(); stock = stock_fixture(); before = deepcopy((supplied, stock))
        dataset, audit = normalize(supplied, stock=stock)
        self.assertEqual((supplied, stock), before)
        self.assertEqual((audit["sessions"], audit["quotes"]), (125, 250))
        self.assertEqual(reference.DAYS[-1], date(2026, 10, 29))
        self.assertEqual(dataset["snapshots"][0]["underlying_price"], "20.12345678")
        self.assertFalse(audit["cross_provider_reconciliation_passed"])
        self.assertFalse(audit["owner_source_replaced"])
        self.assertFalse(audit["deployable"])
        self.assertFalse(audit["execution_qualified"])
        self.assertIsNone(audit["actual_quote_event_clock"])
        self.assertEqual(dataset["source"]["quote_time_basis"], "assumed_eod")
        for record, row in zip(supplied, audit["source_manifest"]["chain_records"]):
            self.assertEqual(row["chain_received_at"], record["received_at"])
            self.assertEqual(row["chain_sha256"], hashlib.sha256(record["chain_raw"]).hexdigest())
        for row in dataset["snapshots"]:
            self.assertEqual({q["type"] for q in row["quotes"]}, {"call", "put"})
            for q in row["quotes"]:
                self.assertEqual((q["bid"], q["ask"], q["bid_size"], q["ask_size"], q["volume"], q["open_interest"]), ("0", "0.5", 0, 10, 0, 0))

    def test_exact_half_even_precision_conversion_is_per_row_audited(self):
        dataset, audit = normalize()
        for row in audit["stock_precision_rows"]:
            self.assertEqual(row["raw_close"], "20.12345678500000000")
            self.assertEqual(row["canonical_close"], "20.12345678")
            self.assertEqual(Decimal(row["signed_delta"]), Decimal("-0.000000005"))
        body, metadata = stock_fixture(); body, metadata = replace_stock(body.replace(b"20.12345678500000000", b"20.12345679500000000"), metadata)
        _, changed = normalize(stock=(body, metadata))
        self.assertTrue(all(row["canonical_close"] == "20.1234568" and Decimal(row["signed_delta"]) == Decimal("0.000000005") for row in changed["stock_precision_rows"]))

    def test_hostile_ambient_decimal_context_cannot_change_declared_conversion(self):
        expected = normalize()
        with localcontext() as context:
            context.prec = 2; context.rounding = ROUND_UP; context.traps[Inexact] = True
            self.assertEqual(normalize(), expected)

    def test_preflight_validates_entire_csv_and_preserves_original_hash(self):
        body, metadata = stock_fixture(); before = deepcopy(metadata)
        result = reference.validate_reference(body, metadata, WINDOW)
        self.assertEqual(metadata, before)
        self.assertEqual(result["option_sessions"], 125)
        self.assertEqual(result["stock_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["stock_last_date"], "2026-10-30")
        self.assertGreater(result["stock_rows"], 125)
        self.assertEqual(len(result["canonical_closes"]), 125)
        self.assertTrue(all(value == "20.12345678" for value in result["canonical_closes"].values()))
        _, audit = normalize()
        self.assertEqual(result["stock_precision_sha256"], audit["stock_precision_sha256"])
        self.assertEqual(audit["stock_precision_rows"][0]["role"], "causal_context_only_not_option_metrics")
        self.assertEqual(audit["stock_precision_rows"][-1]["role"], "outside_option_cohort_not_used")

    def test_paired_plan_preflight_rejects_future_sensitivity_before_any_csv_parsing(self):
        for now in (datetime(2026, 10, 29, 20, 15, tzinfo=timezone.utc),
                    datetime(2026, 10, 30, 13, 34, 59, tzinfo=timezone.utc)):
            window = research_window.derive_window(now)
            with self.subTest(now=now), self.assertRaises(ValueError): reference.validate_plan(window)
            with patch.object(reference, "_stock", side_effect=AssertionError("CSV must not be parsed")) as parser:
                with self.assertRaises(ValueError): reference.validate_reference(b"", {}, window)
                parser.assert_not_called()
        ready = research_window.derive_window(datetime(2026, 10, 30, 13, 35, tzinfo=timezone.utc))
        result = reference.validate_plan(ready)
        self.assertEqual(result["sessions"], 125)
        self.assertEqual(result["latest_assumed_availability"][reference.CLOCK_PROFILES[1]], "2026-10-30T13:35:00+00:00")

    def test_both_profiles_use_same_actual_sources_and_matching_dates_without_future_oct30(self):
        eod, a = normalize(); delayed, b = normalize(profile=reference.CLOCK_PROFILES[1])
        self.assertEqual(eod["source"]["sha256"], delayed["source"]["sha256"])
        self.assertEqual(a["source_manifest"], b["source_manifest"])
        self.assertNotEqual(a["dataset_sha256"], b["dataset_sha256"])
        self.assertEqual(delayed["snapshots"][-1]["available_at"], "2026-10-30T13:35:00+00:00")
        for x, y in zip(eod["snapshots"], delayed["snapshots"]):
            self.assertEqual({k: v for k, v in x.items() if k != "available_at"}, {k: v for k, v in y.items() if k != "available_at"})

    def test_monthly_expiry_is_strictly_later_with_holiday_adjustment_and_roll(self):
        cases = {"2026-05-04": "2026-05-15", "2026-05-14": "2026-05-15", "2026-05-15": "2026-06-18",
            "2026-06-17": "2026-06-18", "2026-06-18": "2026-07-17", "2026-10-15": "2026-10-16",
            "2026-10-16": "2026-11-20", "2026-12-18": "2027-01-15"}
        for day, expected in cases.items():
            with self.subTest(day=day): self.assertEqual(reference.expiry_for(day), date.fromisoformat(expected))
        for bad in (True, datetime(2026, 5, 4), "2026-5-4", "2026-05-16", "2026-06-19"):
            with self.subTest(bad=bad), self.assertRaises(ValueError): reference.expiry_for(bad)

    def test_stock_rounding_cannot_create_below100_eligibility_or_reduce_to_cents(self):
        for price, expected, raw_under in ((b"100.000000001", "100", False), (b"100", "100", False),
                                          (b"99.999999999", "100", True), (b"99.876543219", "99.87654322", True)):
            body, metadata = stock_fixture(); body, metadata = replace_stock(body.replace(b"20.12345678500000000", price), metadata)
            dataset, audit = normalize(stock=(body, metadata))
            self.assertEqual(dataset["snapshots"][0]["underlying_price"], expected)
            self.assertEqual(audit["stock_precision_rows"][0]["raw_under100"], raw_under)
            if not raw_under: self.assertFalse(audit["stock_precision_rows"][0]["canonical_under100"])

    def test_optional_volume_and_interest_remain_null_if_not_supplied(self):
        supplied = records(); rows = chain(reference.DAYS[0]); rows[1].pop("volume"); rows[1]["open_interest"] = None; supplied[0]["chain_raw"] = raw(rows)
        dataset, _ = normalize(supplied)
        self.assertIsNone(dataset["snapshots"][0]["quotes"][0]["volume"])
        self.assertIsNone(dataset["snapshots"][0]["quotes"][0]["open_interest"])

    def test_quote_price_precision_is_not_changed_by_stock_quantization(self):
        supplied = records(); rows = chain(reference.DAYS[0]); rows[1].update(bid=.12345678, ask=.23456789, bid_size=2); supplied[0]["chain_raw"] = raw(rows)
        dataset, _ = normalize(supplied)
        self.assertEqual((dataset["snapshots"][0]["quotes"][0]["bid"], dataset["snapshots"][0]["quotes"][0]["ask"]), ("0.12345678", "0.23456789"))

    def test_reference_requires_matching_whole_file_and_failed_reconciliation_metadata(self):
        body, metadata = stock_fixture()
        mutations = (lambda m: m.update(sha256="0"*64), lambda m: m.update(basis="unadjusted"),
            lambda m: m.update(reconciliation_sha256=""), lambda m: m.update(underlying_price="20"),
            lambda m: m.update(url="https://user:secret@example.com/KO.csv"), lambda m: m.update(url="http://example.com/KO.csv"))
        for mutate in mutations:
            value = deepcopy(metadata); mutate(value)
            with self.assertRaises(ValueError): reference.validate_reference(body, value, WINDOW)

    def test_entire_reference_csv_rejects_duplicate_missing_future_unsorted_and_non_session_rows(self):
        body, metadata = stock_fixture(); lines = body.decode().splitlines(True)
        missing = [line for line in lines if not line.startswith("20260504,")]
        cases = ["".join(missing).encode(), "".join(lines[:2]+lines[1:]).encode(),
            "".join(lines[:1]+lines[1:][::-1]).encode(), body+b"20261102,20,unused\r\n",
            body.replace(b"20260413", b"20260412", 1)]
        for index, bad in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError): reference.validate_reference(*replace_stock(bad, metadata), WINDOW)

    def test_unknown_extra_fields_are_whole_hash_bound_and_not_used_as_prices(self):
        first, audit = normalize(); body, metadata = stock_fixture()
        body, metadata = replace_stock(body.replace(b",kept", b",1000000"), metadata)
        second, changed = normalize(stock=(body, metadata))
        self.assertEqual(first["snapshots"], second["snapshots"])
        self.assertNotEqual(first["source"]["sha256"], second["source"]["sha256"])

    def test_stock_decimal_forms_zero_nan_exponents_negative_and_more_than40places_rejected(self):
        for price in (b"0", b"-1", b"NaN", b"Infinity", b"2e1", b" 20", b"20.", b"01.2", b"1."+b"1"*41):
            body, metadata = stock_fixture(); bad = body.replace(b"20.12345678500000000", price, 1)
            with self.subTest(price=price), self.assertRaises(ValueError): reference.validate_reference(*replace_stock(bad, metadata), WINDOW)

    def test_strict_csv_headers_column_count_and_utf8_contract(self):
        body, metadata = stock_fixture()
        for bad in (body.replace(b"date,close,ignored_original_field", b"date,close,close", 1),
                    body.replace(b"date,close,ignored_original_field", b"date,other,ignored_original_field", 1),
                    body.replace(b",kept", b",kept,extra", 1), body+b"\x00", b"\xff"):
            with self.assertRaises(ValueError): reference.validate_reference(*replace_stock(bad, metadata), WINDOW)

    def test_reference_bounds_reject_oversized_bytes_more_than2000_rows_and_nonbytes(self):
        body, metadata = stock_fixture()
        for bad in (b"", bytearray(body), b" "*(reference.MAX_RAW+1)):
            value = {**metadata, "sha256": hashlib.sha256(bad).hexdigest()}
            with self.assertRaises(ValueError): reference.validate_reference(bad, value, WINDOW)
        days = reference.session_dates(date(2018, 1, 2), date(2026, 10, 30))[:2001]
        bad = ("date,close\n"+"".join(day.strftime("%Y%m%d")+",20\n" for day in days)).encode()
        with self.assertRaises(ValueError): reference.validate_reference(*replace_stock(bad, metadata), WINDOW)

    def test_both_sides_root_source_day_and_fixed_expiry_are_mandatory(self):
        mutations = (lambda rows: rows.pop(), lambda rows: rows[1].update(ticker="F"),
            lambda rows: rows[1].update(expiration="2026-06-18"), lambda rows: rows[1].update(date="2026-05-05"))
        for mutate in mutations:
            supplied = records(); rows = chain(reference.DAYS[0]); mutate(rows); supplied[0]["chain_raw"] = raw(rows)
            with self.assertRaises(ValueError): normalize(supplied)

    def test_duplicate_crossed_bad_size_and_economically_invalid_quotes_stop_without_repair(self):
        mutations = (lambda rows: rows.append(deepcopy(rows[1])), lambda rows: rows[1].update(bid=1, ask=.5),
            lambda rows: rows[1].update(ask_size=True), lambda rows: rows[1].update(volume=True),
            lambda rows: rows[1].update(bid=21, ask=22))
        for mutate in mutations:
            supplied = records(); rows = chain(reference.DAYS[0]); mutate(rows); supplied[0]["chain_raw"] = raw(rows)
            with self.assertRaises(ValueError): normalize(supplied)

    def test_full_fixed_records_cannot_be_sorted_clipped_repeated_or_extended(self):
        for supplied in (records()[:-1], records()+[records()[-1]], records()[::-1], tuple(records())):
            with self.assertRaises(ValueError): normalize(supplied)
        supplied = records(); supplied[1] = deepcopy(supplied[0])
        with self.assertRaises(ValueError): normalize(supplied)

    def test_wrong_url_cannot_select_another_root_day_expiry_or_endpoint(self):
        supplied = records(); supplied[0]["chain_url"] = supplied[0]["chain_url"].replace("ticker=KO", "ticker=F")
        with self.assertRaises(ValueError): normalize(supplied)

    def test_future_receipts_naive_bool_and_forged_registration_are_rejected(self):
        for value in (True, NOW, "2026-11-01T13:00:00", "2026-11-01T14:00:01Z"):
            supplied = records(); supplied[0]["received_at"] = value
            with self.assertRaises(ValueError): normalize(supplied)
        body, metadata = stock_fixture(); metadata["received_at"] = "2026-11-01T14:00:01Z"
        with self.assertRaises(ValueError): reference.validate_reference(body, metadata, WINDOW)

    def test_explicit_profile_and_calendar_scope_cannot_be_overridden(self):
        body, metadata = stock_fixture()
        with self.assertRaises(TypeError): reference.normalize(records(), stock_raw=body, stock_metadata=metadata, window=WINDOW)
        for bad in (True, None, "recorded"):
            with self.assertRaises(ValueError): normalize(profile=bad)
        with self.assertRaises(ValueError): normalize(window=research_window.derive_window(datetime(2028, 1, 1, tzinfo=timezone.utc)))

    def test_future_assumed_availability_rejects_instead_of_clipping_or_backdating(self):
        supplied = records(); stock_raw, metadata = stock_fixture()
        # Source Oct29 is only available Oct30 at09:35 under the delayed profile.
        early = datetime(2026, 10, 30, 13, 34, tzinfo=timezone.utc)
        for row in supplied: row["received_at"] = early.isoformat()
        metadata["received_at"] = early.isoformat()
        # Remove the unneeded future Oct30 CSV row; all required125 rows retained.
        stock_raw = b"\r\n".join(line for line in stock_raw.split(b"\r\n") if not line.startswith(b"20261030,"))
        stock_raw, metadata = replace_stock(stock_raw, metadata)
        with self.assertRaises(ValueError): normalize(supplied, profile=reference.CLOCK_PROFILES[1], stock=(stock_raw, metadata), window=research_window.derive_window(early))

    def test_policy_mutation_is_detected(self):
        with patch.dict(reference.RULE, {"sessions": 124}):
            with self.assertRaises(ValueError): normalize()


if __name__ == "__main__":
    unittest.main()
