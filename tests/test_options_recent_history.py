"""Generated source-shaped fixtures only: no acquired prices or owner actions."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_recent_history as recent
from ariadne import research_window

NOW = datetime(2026, 11, 1, 14, tzinfo=timezone.utc)
WINDOW = research_window.derive_window(NOW)


def raw(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def stock(day):
    start = datetime(day.year, day.month, day.day, 9, 30)
    return [{"timestamp": (start+timedelta(minutes=n)).strftime("%Y-%m-%d %I:%M %p"),
             "open": 20, "high": 21, "low": 19, "close": 20, "volume": 5}
            for n in range(391)]


@lru_cache(maxsize=28)
def stock_body(start, end):
    days = recent.session_dates(date.fromisoformat(start), date.fromisoformat(end))
    return raw([row for day in days for row in stock(day)])


def chain(day):
    return [{"warning": "only 1 strikes out of 2 strikes were returned. Upgrade to level 2 to get full chain."}]+[
        {"osi-key": "KO----261016"+side+"00020000", "ticker": "KO", "date": day.isoformat(),
         "expiration": recent.EXPIRY, "strike": 20, "type": "call" if side == "C" else "put",
         "bid": 0, "ask": .5, "bid_size": 0, "ask_size": 10, "volume": 0, "open_interest": 0}
        for side in ("C", "P")]


def records():
    """Fresh records; two cached complete14/12-session original stock bodies."""
    result = []
    for index, day in enumerate(recent.DAYS):
        start, end = recent.STOCK_WINDOWS[0 if index < 14 else 1]
        result.append({"source_date": day.isoformat(),
            "received_at": (NOW-timedelta(hours=1)+timedelta(seconds=index)).isoformat(),
            "chain_raw": raw(chain(day)), "chain_url": recent.source.chain_url(day, recent.EXPIRY),
            "stock_raw": stock_body(start, end), "stock_received_at": "2026-11-01T12:00:00Z",
            "stock_url": recent.stock_window_url(start, end)})
    return result


def normalize(supplied=None, profile=recent.CLOCK_PROFILES[0], *, window=WINDOW):
    return recent.normalize(records() if supplied is None else supplied, window=window, clock_profile=profile)


class RecentHistoryTests(unittest.TestCase):
    def test_complete_fixed_cohort_and_every_zero_and_partial_warning_survive(self):
        supplied = records(); before = deepcopy(supplied)
        dataset, audit = normalize(supplied)
        self.assertEqual(supplied, before)
        self.assertEqual((audit["sessions"], audit["quotes"]), (26, 52))
        self.assertEqual(audit["source_date_start"], "2026-09-11")
        self.assertEqual(audit["source_date_end"], "2026-10-16")
        self.assertEqual(dataset["source"]["quote_time_basis"], "assumed_eod")
        self.assertFalse(dataset["source"]["independently_verified"])
        self.assertFalse(audit["deployable"])
        self.assertFalse(audit["execution_qualified"])
        self.assertIsNone(audit["actual_quote_event_clock"])
        for row in dataset["snapshots"]:
            self.assertEqual(row["underlying_symbol"], "KO")
            self.assertEqual(row["underlying_price"], "20")
            self.assertEqual({q["type"] for q in row["quotes"]}, {"call", "put"})
            for quote in row["quotes"]:
                self.assertEqual((quote["bid"], quote["bid_size"], quote["volume"], quote["open_interest"]), ("0", 0, 0, 0))
        self.assertTrue(all(item["partial_warning"] and item["partial_counts"] == {"returned_strikes": 1, "total_strikes": 2}
                            for item in audit["record_sources"]))

    def test_two_whole_stock_bodies_are_validated_only_once_each(self):
        with patch.object(recent.source.base, "_stock_sessions", wraps=recent.source.base._stock_sessions) as parser:
            dataset, audit = normalize()
        self.assertEqual(parser.call_count, 2)
        self.assertEqual(audit["unique_stock_bodies_parsed"], 2)
        self.assertEqual(audit["record_sources"][0]["stock_session_details"]["observed_rows"], 391)

    def test_both_profiles_share_unchanged_actual_source_hashes_and_differ_only_hypothetical_availability(self):
        supplied = records(); first, a = normalize(supplied); delayed, b = normalize(supplied, recent.CLOCK_PROFILES[1])
        self.assertEqual(first["source"]["sha256"], delayed["source"]["sha256"])
        self.assertNotEqual(a["profile_evidence_sha256"], b["profile_evidence_sha256"])
        self.assertNotEqual(a["dataset_sha256"], b["dataset_sha256"])
        for index, (x, y) in enumerate(zip(first["snapshots"], delayed["snapshots"])):
            self.assertEqual({k: v for k, v in x.items() if k != "available_at"},
                             {k: v for k, v in y.items() if k != "available_at"})
            self.assertEqual(datetime.fromisoformat(x["available_at"])-datetime.fromisoformat(x["observed_at"]), timedelta(minutes=15))
            self.assertEqual(a["record_sources"][index]["chain_received_at"], supplied[index]["received_at"])
            self.assertEqual(a["record_sources"][index]["chain_sha256"], hashlib.sha256(supplied[index]["chain_raw"]).hexdigest())
            self.assertEqual(b["record_sources"][index]["stock_received_at"], "2026-11-01T12:00:00Z")
        self.assertEqual(delayed["snapshots"][0]["available_at"], "2026-09-14T13:35:00+00:00")
        self.assertEqual(delayed["snapshots"][-1]["available_at"], "2026-10-19T13:35:00+00:00")

    def test_profile_is_explicit_not_silently_defaulted(self):
        with self.assertRaises(TypeError): recent.normalize(records(), window=WINDOW)
        for value in (None, True, "recorded", "assumed_next_session_0930"):
            with self.subTest(value=value), self.assertRaises(ValueError): normalize(profile=value)

    def test_missing_null_source_volume_and_open_interest_stay_null(self):
        supplied = records(); rows = chain(recent.DAYS[0]); rows[1].pop("volume"); rows[1]["open_interest"] = None
        supplied[0]["chain_raw"] = raw(rows)
        dataset, _ = normalize(supplied)
        call = dataset["snapshots"][0]["quotes"][0]
        self.assertIsNone(call["volume"]); self.assertIsNone(call["open_interest"])

    def test_precise_actual_prices_are_not_rounded(self):
        supplied = records(); rows = chain(recent.DAYS[0]); rows[1].update(bid=.12345678, ask=.23456789, bid_size=3)
        supplied[0]["chain_raw"] = raw(rows)
        dataset, _ = normalize(supplied)
        self.assertEqual((dataset["snapshots"][0]["quotes"][0]["bid"], dataset["snapshots"][0]["quotes"][0]["ask"]), ("0.12345678", "0.23456789"))

    def test_per_day_whole_stock_body_is_supported_without_expanding_windows(self):
        supplied = records(); day = recent.DAYS[0]
        supplied[0].update(stock_url=recent.source.stock_url(day), stock_raw=stock_body(day.isoformat(), day.isoformat()))
        _, audit = normalize(supplied)
        self.assertEqual(audit["unique_stock_bodies_parsed"], 3)

    def test_quote_representation_sort_preserves_all_quotes_and_raw_hash(self):
        supplied = records(); rows = chain(recent.DAYS[0]); rows = rows[:1]+rows[1:][::-1]
        supplied[0]["chain_raw"] = raw(rows)
        dataset, audit = normalize(supplied)
        self.assertEqual([q["type"] for q in dataset["snapshots"][0]["quotes"]], ["call", "put"])
        self.assertEqual(audit["record_sources"][0]["chain_sha256"], hashlib.sha256(supplied[0]["chain_raw"]).hexdigest())

    def test_missing_repeated_reordered_extra_and_wrong_date_cohorts_are_rejected(self):
        cases = [records()[:-1], records()+[records()[-1]], records()[::-1], tuple(records())]
        duplicate = records(); duplicate[1] = deepcopy(duplicate[0]); cases.append(duplicate)
        wrong = records(); wrong[0]["source_date"] = "2026-09-10"; cases.append(wrong)
        for supplied in cases:
            with self.subTest(kind=type(supplied), size=len(supplied)), self.assertRaises(ValueError): normalize(supplied)

    def test_fixed_expiry_root_and_osi_terms_cannot_be_substituted(self):
        for key, value in (("ticker", "F"), ("osi-key", "F-----261016C00020000"), ("strike", 21),
                           ("expiration", "2026-10-23"), ("date", "2026-09-10")):
            supplied = records(); rows = chain(recent.DAYS[0]); rows[1][key] = value; supplied[0]["chain_raw"] = raw(rows)
            with self.subTest(key=key), self.assertRaises(ValueError): normalize(supplied)

    def test_both_preregistered_option_types_are_required_without_narrowing(self):
        for side_index in (1, 2):
            supplied = records(); rows = chain(recent.DAYS[0]); supplied[0]["chain_raw"] = raw(rows[:1]+[rows[side_index]])
            with self.subTest(side_index=side_index), self.assertRaises(ValueError): normalize(supplied)

    def test_wrong_source_urls_and_unbounded_stock_windows_are_rejected(self):
        for key, value in (("chain_url", records()[0]["chain_url"].replace("ticker=KO", "ticker=F")),
                           ("stock_url", recent.source.stock_url(recent.DAYS[1])),
                           ("stock_url", recent.source.ORIGIN+"/stock-data/v2/?ticker=KO")):
            supplied = records(); supplied[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): normalize(supplied)
        with self.assertRaises(ValueError): recent.stock_window_url(recent.START, recent.END)

    def test_actual_future_receipts_and_acquisition_time_registration_are_rejected(self):
        for key in ("received_at", "stock_received_at"):
            supplied = records(); supplied[0][key] = (NOW+timedelta(seconds=1)).isoformat()
            with self.subTest(key=key), self.assertRaises(ValueError): normalize(supplied)
        with self.assertRaises(ValueError): normalize(window=research_window.derive_window(NOW-timedelta(hours=2)))

    def test_malformed_naive_bool_and_nonstring_receipt_clocks_are_rejected(self):
        for value in (True, None, NOW, "2026-11-01T13:00:00", "nonsense", "2026-13-01T13:00:00Z"):
            supplied = records(); supplied[0]["received_at"] = value
            with self.subTest(value=value), self.assertRaises(ValueError): normalize(supplied)

    def test_actual_receipts_cannot_precede_any_full_stock_body_session(self):
        supplied = records(); supplied[0]["stock_received_at"] = "2026-09-12T13:00:00Z"
        with self.assertRaises(ValueError): normalize(supplied)

    def test_same_original_stock_url_cannot_change_its_actual_body_or_receipt(self):
        supplied = records(); supplied[1]["stock_received_at"] = "2026-11-01T12:00:01Z"
        with self.assertRaises(ValueError): normalize(supplied)
        supplied = records(); body = json.loads(supplied[1]["stock_raw"]); body[0]["volume"] = 4; supplied[1]["stock_raw"] = raw(body)
        with self.assertRaises(ValueError): normalize(supplied)

    def test_entire_full_minute_grid_is_checked_without_dropping_invalid_rows(self):
        for kind in ("missing", "duplicate", "bad_price", "extra_session"):
            supplied = records(); rows = json.loads(supplied[0]["stock_raw"])
            if kind == "missing": rows.pop(77)
            elif kind == "duplicate": rows.insert(77, rows[77])
            elif kind == "bad_price": rows[-2]["close"] = 40
            else: rows.extend(stock(date(2026, 10, 1)))
            supplied[0]["stock_raw"] = raw(rows)
            with self.subTest(kind=kind), self.assertRaises(ValueError): normalize(supplied)

    def test_full_stock_body_must_match_declared_window_not_just_contain_requested_day(self):
        supplied = records(); supplied[0]["stock_raw"] = stock_body(recent.START, recent.START)
        with self.assertRaises(ValueError): normalize(supplied)

    def test_bad_quote_rows_are_rejected_not_quarantined_or_repaired(self):
        mutations = (lambda rows: rows.append(deepcopy(rows[1])), lambda rows: rows[1].update(bid=1, ask=.5),
            lambda rows: rows[1].update(bid=21, ask=22), lambda rows: rows[1].update(ask_size=True),
            lambda rows: rows[1].update(bid_size=None), lambda rows: rows[1].update(volume=True),
            lambda rows: rows[1].update(open_interest=-1), lambda rows: rows[1].update(multiplier=100))
        for index, mutate in enumerate(mutations):
            supplied = records(); rows = chain(recent.DAYS[0]); mutate(rows); supplied[0]["chain_raw"] = raw(rows)
            with self.subTest(index=index), self.assertRaises(ValueError): normalize(supplied)

    def test_raw_body_types_and_per_response_bounds_are_strict(self):
        for body in (b"", bytearray(b"[]"), "[]", b" "*(recent.source.MAX_RAW+1)):
            supplied = records(); supplied[0]["chain_raw"] = body
            with self.subTest(kind=type(body)), self.assertRaises(ValueError): normalize(supplied)

    def test_unknown_record_fields_are_not_accepted_as_clock_or_permission_extensions(self):
        supplied = records(); supplied[0]["observed_at"] = "2026-09-11T20:00:00Z"
        with self.assertRaises(ValueError): normalize(supplied)

    def test_old_or_unfinalized_windows_reject_entire_fixed_cohort(self):
        for now in (datetime(2026, 10, 16, 20, 14, tzinfo=timezone.utc), datetime(2028, 1, 1, tzinfo=timezone.utc)):
            with self.subTest(now=now), self.assertRaises(ValueError): normalize(window=research_window.derive_window(now))

    def test_policy_mutation_is_detected(self):
        with patch.dict(recent.RULE, {"sessions": 25}):
            with self.assertRaises(ValueError): normalize()


if __name__ == "__main__":
    unittest.main()
