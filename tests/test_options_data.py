from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ariadne import options_data as data

NOW = datetime(2024, 1, 5, 12, tzinfo=timezone.utc)


def source(raw=b"fixture", *, basis="recorded"):
    return {"provider":"Synthetic test fixture", "url":"https://example.com/quotes.csv",
            "sha256":hashlib.sha256(raw).hexdigest(), "retrieved_at":NOW.isoformat(),
            "usage":"Synthetic unit-test fixture, no market evidence.",
            "quote_time_basis":basis, "independently_verified":False}


def dataset():
    return {"schema":1, "evidence_kind":"synthetic_fixture", "as_of":NOW.isoformat(),
            "currency":"USD", "source":source(), "snapshots":[{
                "observed_at":"2024-01-03T21:00:00+00:00", "available_at":"2024-01-03T21:15:00+00:00",
                "underlying_symbol":"QQQ", "underlying_price":"400", "quotes":[{
                    "contract_id":"QQQ240216C00410000", "type":"call", "expiry":"2024-02-16", "strike":"410",
                    "multiplier":100, "bid":"1.9", "ask":"2", "bid_size":10, "ask_size":20,
                    "volume":100, "open_interest":1000}]}]}


HEADER = "underlying_symbol,quote_datetime,root,expiration,strike,option_type,trade_volume,bid_size,bid,ask_size,ask,underlying_bid,underlying_ask,open_interest\n"
ROW = "AAPL,1/3/2024 16:00,AAPL,2/16/2024,190,C,42,12,1.9,20,2.0,180.0,180.2,1000\n"


class OptionsDataTests(unittest.TestCase):
    def reject(self, mutate):
        value = dataset()
        mutate(value)
        with self.assertRaises(data.OptionsDataError):
            data.validate_dataset(value, NOW)

    def test_valid_round_trip_preserves_kind_times_and_does_not_mutate(self):
        value = dataset()
        before = deepcopy(value)
        result = data.validate_dataset(value, NOW)
        self.assertEqual(before, value)
        self.assertEqual(result["quotes"], 1)
        self.assertEqual(result["evidence_kind"], "synthetic_fixture")
        self.assertFalse(result["source_independently_verified"])
        self.assertEqual(data.load_dataset(data.canonical(value), NOW), value)

    def test_duplicate_nested_json_keys_rejected(self):
        raw = data.canonical(dataset()).replace(b'"bid":"1.9"', b'"bid":"1.9","bid":"2"')
        with self.assertRaises(data.OptionsDataError):
            data.load_dataset(raw, NOW)

    def test_nonfinite_recursive_and_nonbytes_json_rejected(self):
        for raw in (b'{"bad":NaN}', b'[' * 2000 + b']' * 2000, "{}", b'\xff', b'{} trailing'):
            with self.subTest(raw_type=type(raw)), self.assertRaises(data.OptionsDataError):
                data.load_dataset(raw, NOW)

    def test_exact_keys_no_unknown_or_missing_contract_fields(self):
        for location in (lambda v:v, lambda v:v["source"], lambda v:v["snapshots"][0], lambda v:v["snapshots"][0]["quotes"][0]):
            self.reject(lambda v, loc=location:loc(v).update(unreviewed=True))

    def test_snapshot_offset_alias_cannot_evade_duplicates(self):
        value = dataset()
        duplicate = deepcopy(value["snapshots"][0])
        duplicate["observed_at"] = "2024-01-03T16:00:00-05:00"
        duplicate["available_at"] = "2024-01-03T16:15:00-05:00"
        value["snapshots"].append(duplicate)
        with self.assertRaises(data.OptionsDataError):
            data.validate_dataset(value, NOW)

    def test_snapshot_sort_uses_instant_not_string(self):
        value = dataset()
        following = deepcopy(value["snapshots"][0])
        following.update(observed_at="2024-01-04T16:00:00-05:00", available_at="2024-01-04T21:15:00Z")
        value["snapshots"].append(following)
        self.assertEqual(data.validate_dataset(value, NOW)["snapshots"], 2)
        value["snapshots"].reverse()
        with self.assertRaises(data.OptionsDataError):
            data.validate_dataset(value, NOW)

    def test_one_daily_snapshot_prevents_intraday_precision_claim(self):
        value = dataset()
        extra = deepcopy(value["snapshots"][0])
        extra.update(observed_at="2024-01-03T20:00:00Z", available_at="2024-01-03T20:15:00Z")
        value["snapshots"].insert(0, extra)
        with self.assertRaises(data.OptionsDataError):
            data.validate_dataset(value, NOW)

    def test_delay_and_publication_cannot_follow_retrieval(self):
        for stamp in ("2024-01-03T21:14:59Z", "2024-01-05T12:00:01Z"):
            self.reject(lambda v,s=stamp:v["snapshots"][0].update(available_at=s))

    def test_future_naive_and_impossible_timestamp_rejected(self):
        for path, stamp in (("as_of", "2024-01-05T12:00:01Z"), ("as_of", "2024-01-05"), ("as_of", "yesterday")):
            self.reject(lambda v,p=path,s=stamp:v.update({p:s}))
        self.reject(lambda v:v["source"].update(retrieved_at="2024-01-05T12:00:01Z"))
        self.reject(lambda v:v["snapshots"][0].update(observed_at="2024-01-03T21:00:00"))

    def test_weekends_holidays_premarket_and_afterhours_rejected(self):
        for observed, available in (("2024-01-01T21:00:00Z", "2024-01-01T21:15:00Z"),
                                    ("2023-12-30T21:00:00Z", "2023-12-30T21:15:00Z"),
                                    ("2024-01-03T14:29:59Z", "2024-01-03T15:00:00Z"),
                                    ("2024-01-03T21:00:01Z", "2024-01-03T21:15:01Z")):
            self.reject(lambda v,o=observed,a=available:v["snapshots"][0].update(observed_at=o, available_at=a))

    def test_dst_and_early_close_calendar(self):
        value = dataset()
        value["as_of"] = value["source"]["retrieved_at"] = "2024-11-30T12:00:00Z"
        snapshot = value["snapshots"][0]
        snapshot.update(observed_at="2024-11-29T18:00:00Z", available_at="2024-11-29T18:15:00Z")
        snapshot["quotes"][0].update(contract_id="QQQ241220C00410000", expiry="2024-12-20")
        self.assertEqual(data.validate_dataset(value, "2024-11-30T12:00:00Z")["quotes"], 1)
        snapshot.update(observed_at="2024-11-29T18:01:00Z", available_at="2024-11-29T18:16:00Z")
        with self.assertRaises(data.OptionsDataError):
            data.validate_dataset(value, "2024-11-30T12:00:00Z")

    def test_standard_occ_economic_identity_and_no_adjusted_roots(self):
        for field, value in (("contract_id", "QQQ1240216C00410000"), ("contract_id", "SPY240216C00410000"),
                             ("contract_id", "QQQ240216P00410000"), ("contract_id", "QQQ240216C00410001"),
                             ("expiry", "2024-02-17"), ("multiplier", 10), ("multiplier", True),
                             ("type", ["call"]), ("strike", "410.0001")):
            self.reject(lambda v,f=field,x=value:v["snapshots"][0]["quotes"][0].update({f:x}))

    def test_duplicate_and_unsorted_economic_contracts_rejected(self):
        self.reject(lambda v:v["snapshots"][0]["quotes"].append(deepcopy(v["snapshots"][0]["quotes"][0])))

    def test_decimal_types_and_crossed_quotes_rejected(self):
        for value in (2.0, 2, True, "-1", "NaN", "Infinity", "2e0", "01", "1.000000001", "1000001", " 2"):
            self.reject(lambda v,x=value:v["snapshots"][0]["quotes"][0].update(ask=x))
        self.reject(lambda v:v["snapshots"][0]["quotes"][0].update(bid="2.01"))

    def test_zero_and_null_sizes_are_retained_never_invented(self):
        value = dataset()
        quote = value["snapshots"][0]["quotes"][0]
        quote.update(bid="0", ask="0", bid_size=0, ask_size=None, volume=None, open_interest=0)
        self.assertEqual(data.load_dataset(data.canonical(value), NOW)["snapshots"][0]["quotes"][0], quote)
        for size in (-1, True, 1.5, "1", 1000000001):
            self.reject(lambda v,x=size:v["snapshots"][0]["quotes"][0].update(ask_size=x))

    def test_impossible_bid_cannot_manufacture_a_giant_exit(self):
        self.reject(lambda v:v["snapshots"][0]["quotes"][0].update(bid="401", ask="402"))
        value = dataset()
        quote = value["snapshots"][0]["quotes"][0]
        quote.update(type="put", contract_id="QQQ240216P00410000", bid="411", ask="412")
        with self.assertRaises(data.OptionsDataError):
            data.validate_dataset(value, NOW)

    def test_expired_or_remote_expiry_rejected(self):
        for expiry in ("2024-01-02", "2027-02-16"):
            value = dataset()
            quote = value["snapshots"][0]["quotes"][0]
            quote["expiry"] = expiry
            quote["contract_id"] = data.contract_id("QQQ", expiry, "call", "410")
            with self.assertRaises(data.OptionsDataError):
                data.validate_dataset(value, NOW)

    def test_provenance_is_bounded_printable_https_and_never_claims_verified(self):
        for field, value in (("url", "http://example.com"), ("url", "https://user:pass@example.com/"),
                             ("url", "https://example.com/#fragment"), ("provider", "bad\nprovider"),
                             ("usage", "x"*2001), ("sha256", "a"*63), ("quote_time_basis", []),
                             ("independently_verified", True)):
            self.reject(lambda v,f=field,x=value:v["source"].update({f:x}))

    def test_assumed_eod_is_explicit_research_metadata(self):
        value = dataset()
        value["source"]["quote_time_basis"] = "assumed_eod"
        self.assertEqual(data.validate_dataset(value, NOW)["quote_time_basis"], "assumed_eod")

    def test_bounded_counts_and_serialized_import(self):
        with patch.object(data, "MAX_DATA", 10), self.assertRaises(data.OptionsDataError):
            data.load_dataset(b" " * 11, NOW)
        with patch.object(data, "MAX_QUOTES", 0), self.assertRaises(data.OptionsDataError):
            data.validate_dataset(dataset(), NOW)
        with patch.object(data, "MAX_SNAPSHOTS", 0), self.assertRaises(data.OptionsDataError):
            data.validate_dataset(dataset(), NOW)

    def test_canonical_rejects_nonordinary_values(self):
        for value in (float("nan"), Decimal("1"), {"bad":object()}):
            with self.assertRaises(data.OptionsDataError):
                data.canonical(value)
        self.assertEqual(data.decimal_string(Decimal("100.0000")), "100")

    def test_cboe_actual_recorded_sample_conversion_and_decimal_midpoint(self):
        raw = (HEADER + ROW).encode()
        value = data.parse_cboe_csv(raw, source=source(raw), as_of=NOW, symbols=("AAPL",))
        snapshot = value["snapshots"][0]
        self.assertEqual(snapshot["observed_at"], "2024-01-03T21:00:00+00:00")
        self.assertEqual(snapshot["available_at"], "2024-01-03T21:15:00+00:00")
        self.assertEqual(snapshot["underlying_price"], "180.1")
        self.assertEqual(snapshot["quotes"][0]["contract_id"], "AAPL240216C00190000")
        self.assertEqual(value["evidence_kind"], "historical_quotes")
        self.assertEqual(value["source"]["sha256"], hashlib.sha256(raw).hexdigest())

    def test_cboe_selects_last_regular_interval_without_merging_intraday_prices(self):
        raw = (HEADER + ROW.replace("16:00", "15:00").replace("1.9", "1.8") + ROW + ROW.replace("16:00", "16:15").replace("1.9", "1.7")).encode()
        value = data.parse_cboe_csv(raw, source=source(raw), as_of=NOW)
        self.assertEqual(len(value["snapshots"]), 1)
        self.assertEqual(value["snapshots"][0]["quotes"][0]["bid"], "1.9")

    def test_cboe_hash_shape_headers_and_selected_invalid_rows_fail(self):
        raws = [(HEADER+ROW).encode(), (HEADER.replace("bid_size", "wrong")+ROW).encode(),
                (HEADER+ROW.replace(",AAPL,2/16", ",AAPL1,2/16")).encode(),
                (HEADER+ROW.replace(",1.9,", ",NaN,")).encode(),
                (HEADER+ROW.rstrip(",1000\n")+"\n").encode()]
        for index, raw in enumerate(raws):
            provenance = source(raw) if index else source(b"wrong")
            with self.subTest(index=index), self.assertRaises(data.OptionsDataError):
                data.parse_cboe_csv(raw, source=provenance, as_of=NOW)

    def test_cboe_inconsistent_underlier_and_duplicate_contract_rows_fail(self):
        for second in (ROW, ROW.replace(",190,C,", ",195,C,").replace("180.2", "180.4")):
            raw = (HEADER+ROW+second).encode()
            with self.assertRaises(data.OptionsDataError):
                data.parse_cboe_csv(raw, source=source(raw), as_of=NOW)


class OfflineOptionsCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("offline_options_cli_test", root / "scripts/options_market.py")
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)
        cls.staging = root / "staging"
        cls.staging.mkdir(exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="options-cli-test-", dir=self.staging)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.csv = (HEADER + ROW).encode()
        (self.workspace / "source.csv").write_bytes(self.csv)
        (self.workspace / "metadata.json").write_bytes(data.canonical(source(self.csv)))

    def args(self, **changes):
        fields = {"action":"convert-cboe", "workspace":"workspace", "input":"source.csv",
                  "metadata":"metadata.json", "output":"market-data/options/current.json"}
        fields.update(changes)
        return SimpleNamespace(**fields)

    def test_conversion_and_validation_offline_with_new_output_only(self):
        result = self.cli.run(self.args(), root=self.root, now=NOW)
        target = self.workspace / "market-data/options/current.json"
        original = target.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), result["output_sha256"])
        self.assertEqual(result["network_requests"], 0)
        self.assertFalse(result["orders"])
        with self.assertRaises(FileExistsError):
            self.cli.run(self.args(), root=self.root, now=NOW)
        self.assertEqual(target.read_bytes(), original)
        checked = self.cli.run(self.args(action="validate", input="market-data/options/current.json", metadata=None, output=None), root=self.root, now=NOW)
        self.assertEqual(checked["quality"]["quotes"], 1)

    def test_path_escape_absolute_drive_and_url_rejected_before_access(self):
        for location, value in (("workspace", "../outside"), ("workspace", "E:/reserved"),
                                ("input", "../source.csv"), ("input", "E:/reserved.json"),
                                ("input", "https://example.com/data"), ("output", "../bad.json")):
            with self.subTest(location=location), self.assertRaises(ValueError):
                self.cli.run(self.args(**{location:value}), root=self.root, now=NOW)

    def test_synthetic_owner_import_and_bad_metadata_do_not_publish(self):
        (self.workspace / "synthetic.json").write_bytes(data.canonical(dataset()))
        with self.assertRaisesRegex(ValueError, "Synthetic"):
            self.cli.run(self.args(action="validate", input="synthetic.json", metadata=None, output=None), root=self.root, now=NOW)
        (self.workspace / "metadata.json").write_bytes(data.canonical(source(b"wrong")))
        with self.assertRaisesRegex(ValueError, "hash"):
            self.cli.run(self.args(), root=self.root, now=NOW)
        self.assertFalse((self.workspace / "market-data").exists())
        with self.assertRaises(ValueError):
            self.cli.metadata(b'{"provider":"one","provider":"two"}')

    def test_hard_link_input_rejected(self):
        os.link(self.workspace / "source.csv", self.workspace / "linked.csv")
        with self.assertRaisesRegex(ValueError, "regular"):
            self.cli.run(self.args(input="linked.csv"), root=self.root, now=NOW)

    def test_validation_is_read_only_and_rejects_write_arguments(self):
        with self.assertRaisesRegex(ValueError, "does not write"):
            self.cli.run(self.args(action="validate"), root=self.root, now=NOW)
        self.assertEqual(sorted(p.name for p in self.workspace.iterdir()), ["metadata.json", "source.csv"])


if __name__ == "__main__":
    unittest.main()
