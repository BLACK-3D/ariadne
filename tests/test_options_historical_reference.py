"""Generated historical null-size observations; never market performance."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_historical_reference as historical
from ariadne import options_bankroll_research as research
from test_options_reference_history import records as reference_records, raw, NOW, WINDOW
from test_options_reference_projection import parent_fixture


def records(*, unknown=True):
    supplied = reference_records()
    for record in supplied:
        rows = json.loads(record["chain_raw"])
        for row in rows[1:]:
            row.update(bid=.6, ask=.65, bid_size=None if unknown else 230, ask_size=None if unknown else 230,
                volume=100000, open_interest=500000)
        record["chain_raw"] = raw(rows)
    return supplied


def normalize(supplied=None, *, profile=historical.CLOCK_PROFILES[0]):
    body, metadata = parent_fixture()
    return historical.normalize(records() if supplied is None else supplied,
        stock_raw=body, stock_metadata=metadata, window=WINDOW, clock_profile=profile)


class HistoricalReferenceTests(unittest.TestCase):
    def test_unknown_side_sizes_are_retained_as_null_without_volume_or_oi_substitution(self):
        supplied = records(); before = deepcopy(supplied)
        dataset, audit = normalize(supplied)
        self.assertEqual(supplied, before)
        self.assertEqual((audit["sessions"], audit["quotes"]), (125, 250))
        self.assertEqual((audit["unknown_bid_size_quotes"], audit["unknown_ask_size_quotes"]), (250, 250))
        for row in dataset["snapshots"]:
            for quote in row["quotes"]:
                self.assertIsNone(quote["bid_size"]); self.assertIsNone(quote["ask_size"])
                self.assertEqual((quote["volume"], quote["open_interest"]), (100000, 500000))
                self.assertEqual((quote["bid"], quote["ask"]), ("0.6", "0.65"))
        self.assertTrue(audit["unknown_side_sizes_preserved_as_null"])
        self.assertFalse(audit["side_size_values_coerced_or_inferred"])
        self.assertFalse(audit["deployable"])
        self.assertFalse(audit["automatic_promotion"])

    def test_parser_uniformly_preserves_known_zero_and_null_sizes(self):
        record = records()[0]; rows = json.loads(record["chain_raw"])
        rows[1].update(bid_size=0, ask_size=None); rows[2].update(bid_size=17, ask_size=1)
        quotes, warning, counts = historical.parse_chain(raw(rows), historical.DAYS[0], historical.expiry_for(historical.DAYS[0]))
        self.assertEqual([(q["bid_size"], q["ask_size"]) for q in quotes], [(0, None), (17, 1)])
        self.assertTrue(warning); self.assertEqual(counts, {"returned_strikes": 1, "total_strikes": 2})
        self.assertTrue(all(q["multiplier"] == 100 for q in quotes))

    def test_live_frozen_parser_still_rejects_null_execution_sizes(self):
        record = records()[0]
        with self.assertRaises(ValueError): historical.source._chain(record["chain_raw"], historical.DAYS[0], historical.expiry_for(historical.DAYS[0]))
        quotes, _, _ = historical.parse_chain(record["chain_raw"], historical.DAYS[0], historical.expiry_for(historical.DAYS[0]))
        self.assertTrue(all(q["bid_size"] is None and q["ask_size"] is None for q in quotes))

    def test_nullable_missing_volume_and_interest_remain_unknown_and_actual_zero_stays_zero(self):
        supplied = records(); rows = json.loads(supplied[0]["chain_raw"])
        rows[1].pop("volume"); rows[1]["open_interest"] = None; rows[2].update(volume=0, open_interest=0)
        supplied[0]["chain_raw"] = raw(rows)
        dataset, _ = normalize(supplied)
        self.assertEqual([(q["volume"], q["open_interest"]) for q in dataset["snapshots"][0]["quotes"]], [(None, None), (0, 0)])

    def test_bool_float_string_negative_and_overbound_sizes_are_rejected_not_null_coerced(self):
        record = records()[0]
        for key in ("bid_size", "ask_size", "volume", "open_interest"):
            for value in (True, 1.0, "1", -1, 1000000001):
                rows = json.loads(record["chain_raw"]); rows[1][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    historical.parse_chain(raw(rows), historical.DAYS[0], historical.expiry_for(historical.DAYS[0]))

    def test_missing_required_size_field_is_not_treated_as_present_null(self):
        record = records()[0]
        for key in ("bid_size", "ask_size"):
            rows = json.loads(record["chain_raw"]); rows[1].pop(key)
            with self.assertRaises(ValueError): historical.parse_chain(raw(rows), historical.DAYS[0], historical.expiry_for(historical.DAYS[0]))

    def test_duplicate_crossed_wrong_root_date_expiry_osi_and_unknown_fields_are_rejected(self):
        record = records()[0]
        mutations = (lambda rows: rows.append(deepcopy(rows[1])), lambda rows: rows[1].update(bid=.8),
            lambda rows: rows[1].update(ticker="F"), lambda rows: rows[1].update(date="2026-05-05"),
            lambda rows: rows[1].update(expiration="2026-06-18"), lambda rows: rows[1].update(strike=21),
            lambda rows: rows[1].update(**{"osi-key": "KO1---260515C00020000"}),
            lambda rows: rows[1].update(quote_observed_at="2026-05-04T20:00:00Z"), lambda rows: rows.pop())
        for index, mutate in enumerate(mutations):
            rows = json.loads(record["chain_raw"]); mutate(rows)
            with self.subTest(index=index), self.assertRaises(ValueError): historical.parse_chain(raw(rows), historical.DAYS[0], historical.expiry_for(historical.DAYS[0]))

    def test_unknown_sizes_do_not_bypass_reference_economic_upper_bound(self):
        supplied = records(); rows = json.loads(supplied[0]["chain_raw"]); rows[1].update(bid=21, ask=22)
        supplied[0]["chain_raw"] = raw(rows)
        with self.assertRaises(ValueError): normalize(supplied)

    def test_known_sizes_and_precision_are_not_changed_by_historical_schema(self):
        supplied = records(unknown=False); dataset, audit = normalize(supplied)
        self.assertEqual((audit["unknown_bid_size_quotes"], audit["unknown_ask_size_quotes"]), (0, 0))
        self.assertTrue(all(q["bid_size"] == 230 and q["ask_size"] == 230 for row in dataset["snapshots"] for q in row["quotes"]))
        self.assertEqual(dataset["snapshots"][0]["underlying_price"], "20.12345678")
        self.assertEqual(audit["stock_precision_rows"][0]["raw_close"], "20.12345678500000000")

    def test_both_profiles_bind_identical_original_raw_receipts_and_parent_projection(self):
        supplied = records(); first, a = normalize(supplied); second, b = normalize(supplied, profile=historical.CLOCK_PROFILES[1])
        self.assertEqual(first["source"]["sha256"], second["source"]["sha256"])
        self.assertEqual(a["source_manifest"], b["source_manifest"])
        self.assertNotEqual(a["dataset_sha256"], b["dataset_sha256"])
        self.assertEqual(a["global_source_calendar_quality"], "FAILED")
        self.assertEqual(a["global_calendar_anomalies"][0]["source_date"], "2025-11-27")
        for source_record, evidence in zip(supplied, a["source_manifest"]["chain_records"]):
            self.assertEqual(evidence["chain_sha256"], hashlib.sha256(source_record["chain_raw"]).hexdigest())
            self.assertEqual(evidence["chain_received_at"], source_record["received_at"])

    def test_missing_or_future_source_record_is_rejected_without_replay_or_clipping(self):
        with self.assertRaises(ValueError): normalize(records()[:-1])
        supplied = records(); supplied[0]["received_at"] = "2026-11-01T14:00:01Z"
        with self.assertRaises(ValueError): normalize(supplied)
        supplied = records(); supplied[0]["chain_url"] += "&api_key=unexpected"
        with self.assertRaises(ValueError): normalize(supplied)

    def test_null_sizes_still_generate_no_entries_under_unchanged_research_money_rules(self):
        dataset, _ = normalize()
        dataset["evidence_kind"] = "synthetic_fixture"
        with patch.object(research.signals, "signal", return_value=True):
            result = research.backtest({"KO": dataset}, symbols=("KO",), benchmark=None)
        for value in result["candidate_results"].values():
            self.assertEqual(value["metrics"]["entries"], 0)
            self.assertEqual(value["metrics"]["contributions"], "25000")
            self.assertEqual(value["state"]["cash"], "25000")
            self.assertIsNone(value["state"]["position"])
            self.assertFalse(any(event["type"] == "fill" for event in value["events"]))

    def test_unknown_bid_on_funded_position_holds_full_quantity_without_synthetic_exit(self):
        from test_options_bankroll_research import stocks, study, report, fills
        data = stocks(symbols=("AAA",), sizes=230, quote_values=lambda i, d: ("0.60", "0.65"))
        for row in data["AAA"]["snapshots"][15:]:
            for quote in row["quotes"]: quote["bid_size"] = None
        value = report(study(data))
        self.assertEqual(value["hold"]["code"], "missing_or_insufficient_full_quantity_bid")
        self.assertEqual(value["state"]["position"]["quantity"], 15)
        self.assertEqual(fills(value, "SELL"), [])
        self.assertFalse(value["coverage"]["complete"])


if __name__ == "__main__":
    unittest.main()
