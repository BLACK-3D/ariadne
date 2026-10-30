"""Independent fixtures for frozen VIX INDEX signals and provenance (no trades)."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from ariadne import vix_signals as v
from ariadne.qqq_data import session_dates

NOW = datetime(2024, 3, 1, 12, tzinfo=timezone.utc)
DAYS = session_dates(date(2024, 1, 2), date(2024, 2, 29))


def raw(closes, *, omit=()):
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE"]
    for index, value in enumerate(closes):
        if index in omit:
            continue
        number = Decimal(str(value))
        lines.append(",".join((DAYS[index].strftime("%m/%d/%Y"), str(number), str(number+1), str(number-1), str(number))))
    return ("\n".join(lines)+"\n").encode()


def history(closes, *, symbol="VIX", omit=()):
    return v.load_history(raw(closes, omit=omit), symbol=symbol, retrieved_at=NOW, now=NOW)


class VixHistoryTests(unittest.TestCase):
    def test_strict_official_csv_identity_and_provenance(self):
        data = raw([20, 21])
        value = v.load_history(data, retrieved_at=NOW, now=NOW)
        self.assertEqual(value["source"]["raw_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(value["source"]["historical_availability"], "unknown")
        self.assertIsNone(value["source"]["source_published_at"])
        self.assertEqual(value["source"]["url"], v.OFFICIAL_VIX_URL)
        self.assertEqual(value["evidence_kind"], "retrospective_index_history")
        self.assertEqual(value["rows"][0]["date"], "2024-01-02")

    def test_no_external_url_symbol_or_permission_alias(self):
        for kwargs in ({"symbol": "VXX"}, {"symbol": "^VIX"}, {"symbol": True}, {"source_url": "https://example.com/fake.csv"},
                       {"source_url": v.SOURCE_URLS["VIX9D"]}, {"permission_url": "https://example.com/license"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(v.VixSignalsError):
                v.load_history(raw([20]), retrieved_at=NOW, now=NOW, **kwargs)

    def test_format_bounds_and_nonfinite_values_rejected(self):
        payload = raw([20])
        malformed = [b"", payload.decode(), b"\xff", b"x"*(v.MAX_PAYLOAD+1),
                     payload.replace(b"DATE,OPEN,HIGH,LOW,CLOSE", b"DATE,OPEN,HIGH,LOW,LOW"),
                     payload.replace(b"20,21,19,20", b"20,21,19,NaN"),
                     payload.replace(b"20,21,19,20", b"20,21,19,Infinity"),
                     payload.replace(b"20,21,19,20", b"20,21,19,2e1"),
                     payload.replace(b"20,21,19,20", b"20,21,19,0"),
                     payload.replace(b"20,21,19,20", b"20,21,19,-1"),
                     payload.replace(b"20,21,19,20", b"20,21,19,020"),
                     payload.replace(b"20,21,19,20", b"20,21,19,20.000000001"),
                     payload.replace(b"20,21,19,20", b"20,21,19,20\t"),
                     payload.replace(b"20,21,19,20", b"20,21,19,20,extra")]
        for data in malformed:
            with self.subTest(data=data[:80]), self.assertRaises(v.VixSignalsError):
                v.load_history(data, retrieved_at=NOW, now=NOW)

    def test_duplicate_backwards_future_and_invalid_dates_rejected(self):
        payload = raw([20, 21])
        for data in (payload+payload.splitlines()[1]+b"\n",
                     b"\n".join((payload.splitlines()[0], payload.splitlines()[2], payload.splitlines()[1])),
                     payload.replace(b"01/02/2024", b"02/30/2024"),
                     payload.replace(b"01/02/2024", b"03/02/2024"),
                     payload.replace(b"01/02/2024", b"1/02/2024")):
            with self.subTest(data=data), self.assertRaises(v.VixSignalsError):
                v.load_history(data, retrieved_at=NOW, now=NOW)

    def test_positive_prices_and_close_range_remain_strict(self):
        for data in (raw([1001]), raw([1]), raw([20]).replace(b"20,21,19,20", b"20,18,19,20"),
                     raw([20]).replace(b"20,21,19,20", b"20,21,19,22")):
            with self.assertRaises(v.VixSignalsError):
                v.load_history(data, retrieved_at=NOW, now=NOW)

    def test_actual_archive_open_anomaly_retained_and_reported_not_repaired(self):
        payload = raw([20]*6).replace(b"20,21,19,20", b"50,21,19,20", 1)
        value = v.load_history(payload, retrieved_at=NOW, now=NOW)
        report = v.study(value)
        self.assertEqual(value["rows"][0]["open"], "50")
        self.assertEqual(report["coverage"]["open_outside_high_low_count"], 1)
        self.assertFalse(report["coverage"]["open_high_low_used_in_signals"])

    def test_aware_retrieval_publication_and_study_asof_required(self):
        for kwargs in ({"retrieved_at": "2024-03-01"}, {"retrieved_at": "nonsense"},
                       {"retrieved_at": NOW+timedelta(seconds=1)},
                       {"retrieved_at": NOW, "source_published_at": NOW+timedelta(seconds=1)}):
            with self.assertRaises(v.VixSignalsError):
                v.load_history(raw([20]), now=NOW, **kwargs)
        with self.assertRaises(v.VixSignalsError):
            v.study(history([20]), as_of=NOW-timedelta(seconds=1))

    def test_tampered_normalized_input_cannot_be_adopted(self):
        mutations = [lambda x:x["rows"][0].update(close="21"), lambda x:x.update(evidence_kind="live"),
                     lambda x:x["source"].update(historical_availability="known"), lambda x:x.update(extra=True),
                     lambda x:x["source"].update(raw_sha256="bad"), lambda x:x.update(schema=True)]
        for mutate in mutations:
            value = history([20]*6)
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(v.VixSignalsError):
                v.study(value)


class VixSignalStudyTests(unittest.TestCase):
    def test_exact_frozen_breakout_boundary_and_later_index_changes(self):
        report = v.study(history([20]*20+[22, 24, 26, 28, 30, 33]))
        first = report["strategies"]["vix_shock_breakout_call_v1"]["events"][0]
        self.assertEqual(first["observed_session"], DAYS[20].isoformat())
        self.assertEqual(first["next_1_session"]["index_percent_change"], "9.090909")
        self.assertEqual(first["next_5_session"]["index_percent_change"], "50")
        self.assertIsNone(first["observed_at"])
        self.assertIsNone(first["historical_available_at"])
        for closes in ([20]*20+[21.99], [22]*20+[22]):
            self.assertEqual(v.study(history(closes))["summary"]["signal_counts"]["vix_shock_breakout_call_v1"], 0)

    def test_exact_fade_threshold_and_high_volatility_gate(self):
        key = "vix_spike_fade_put_v1"
        self.assertEqual(v.study(history([30]*5+[27]))["summary"]["signal_counts"][key], 1)
        self.assertEqual(v.study(history([30]*5+[27.01]))["summary"]["signal_counts"][key], 0)
        self.assertEqual(v.study(history([24]*5+[20]))["summary"]["signal_counts"][key], 0)

    def test_short_stress_ratio_uses_strict_current_and_inclusive_prior_thresholds(self):
        key = "vix_short_term_stress_call_v1"
        for current, count in ((21, 0), (21.01, 1)):
            report = v.study(history([20]*6), vix9d_history=history([20]*5+[current], symbol="VIX9D"))
            self.assertEqual(report["summary"]["signal_counts"][key], count)
        report = v.study(history([20]*6), vix9d_history=history([20, 20.01, 20, 20, 20, 22], symbol="VIX9D"))
        self.assertEqual(report["summary"]["signal_counts"][key], 0)

    def test_missing_vix9d_blocks_only_pair_hypothesis(self):
        report = v.study(history([20]*20+[22]))
        self.assertIsNotNone(report["summary"]["vix9d_blocked_reason"])
        self.assertEqual(report["summary"]["signal_counts"]["vix_shock_breakout_call_v1"], 1)
        self.assertEqual(report["strategies"]["vix_short_term_stress_call_v1"]["evaluated_sessions"], 0)

    def test_missing_lookback_is_never_filled_or_skipped(self):
        report = v.study(history([20]*20+[22], omit=(10,)))
        self.assertEqual(report["summary"]["signal_counts"]["vix_shock_breakout_call_v1"], 0)
        self.assertEqual(report["coverage"]["missing_vix_session_count"], 1)
        self.assertEqual(report["coverage"]["missing_vix_session_sample"], [DAYS[10].isoformat()])
        pair = v.study(history([20]*6), vix9d_history=history([20]*5+[22], symbol="VIX9D", omit=(2,)))
        self.assertEqual(pair["summary"]["signal_counts"]["vix_short_term_stress_call_v1"], 0)
        self.assertIsNotNone(pair["summary"]["vix9d_blocked_reason"])

    def test_outcome_missing_next_session_does_not_jump_to_a_later_close(self):
        report = v.study(history([20]*20+[22, 24, 26, 28, 30, 33], omit=(21,)))
        event = report["strategies"]["vix_shock_breakout_call_v1"]["events"][0]
        self.assertEqual(event["next_1_session"]["status"], "missing_index_observation")
        self.assertEqual(event["next_5_session"]["status"], "missing_index_observation")
        pending = v.study(history([20]*20+[22]))["strategies"]["vix_shock_breakout_call_v1"]["events"][0]
        self.assertEqual(pending["next_1_session"]["status"], "pending_future_sessions")

    def test_future_rows_do_not_change_past_signal_decisions(self):
        before = v.study(history([20]*20+[22]))
        after = v.study(history([20]*20+[22, 18, 17, 16, 15, 14]))
        key = "vix_shock_breakout_call_v1"
        self.assertEqual(before["strategies"][key]["events"][0]["observed_session"], after["strategies"][key]["events"][0]["observed_session"])
        self.assertEqual(before["strategies"][key]["events"][0]["index_close"], after["strategies"][key]["events"][0]["index_close"])

    def test_report_preserves_inputs_binding_bounds_and_no_trading_claims(self):
        value = history([20]*20+[22])
        prior = deepcopy(value)
        with patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")) as network:
            report = v.study(value)
        network.assert_not_called()
        self.assertEqual(value, prior)
        self.assertEqual(report["sources"]["VIX"]["raw_sha256"], value["source"]["raw_sha256"])
        self.assertEqual(report["input_history_sha256"]["VIX"], value["history_sha256"])
        self.assertEqual(report["rule_sha256"], v.RULE_SHA256)
        self.assertEqual(report["preregistration_sha256"], v.PREREGISTRATION_SHA256)
        self.assertIsNone(report["summary"]["tradable_pnl"])
        self.assertFalse(report["summary"]["forward_execution_qualified"])
        self.assertLess(len(v.canonical(report["summary"])), 4096)
        self.assertLess(len(v.canonical(report["coverage"])), 4096)
        self.assertEqual(list(report["strategies"]), v.RULE["strategy_order"])

    def test_wrong_history_pair_identity_rejected(self):
        with self.assertRaises(v.VixSignalsError):
            v.study(history([20]*6, symbol="VIX9D"))
        with self.assertRaises(v.VixSignalsError):
            v.study(history([20]*6), vix9d_history=history([20]*6))

    def test_unreviewed_and_nonregular_dates_retained_but_never_signal_sessions(self):
        prefix = b"12/31/1998,20,21,19,20\n01/01/1999,20,21,19,20\n"
        payload = raw([20]*6)
        payload = payload.split(b"\n", 1)[0]+b"\n"+prefix+payload.split(b"\n", 1)[1]
        report = v.study(v.load_history(payload, retrieved_at=NOW, now=NOW))
        self.assertEqual(report["coverage"]["calendar_unreviewed_rows"], 1)
        self.assertEqual(report["coverage"]["nonregular_rows_excluded"], 1)
        self.assertEqual(report["coverage"]["reviewed_start"], "2024-01-02")

    def test_preregistration_file_hash_and_no_option_execution_gate(self):
        registration = Path(v.__file__).parent / "resources/vix/preregistration.json"
        self.assertEqual(hashlib.sha256(registration.read_bytes()).hexdigest(), v.PREREGISTRATION_SHA256)
        report = v.study(history([20]*6))
        self.assertFalse(report["execution_qualified"])
        self.assertEqual(report["research_gate"], "retrospective_index_only_not_option_backtest")


if __name__ == "__main__":
    unittest.main()
