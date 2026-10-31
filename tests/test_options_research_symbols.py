"""Research-symbol parsing must not expand existing paper-account authority."""
from copy import deepcopy
import unittest

from ariadne import options_data, options_recovery
from test_options_recovery import fixture


def ko_fixture():
    data = deepcopy(fixture(21))
    for row in data["snapshots"]:
        row["underlying_symbol"], row["underlying_price"] = "KO", "50"
        for quote in row["quotes"]:
            quote["strike"] = "50"
            quote["contract_id"] = options_data.contract_id("KO", quote["expiry"], quote["type"], "50")
    return data


class ResearchSymbolTests(unittest.TestCase):
    def test_base_schema_accepts_named_ko_without_claiming_authenticity(self):
        value = options_data.validate_dataset(ko_fixture())
        self.assertEqual(value["symbols"], ["KO"])
        self.assertFalse(value["source_independently_verified"])

    def test_existing_universe_and_paper_activation_still_reject_ko(self):
        self.assertEqual(options_recovery.RULE["universe"], ["QQQ", "SPY", "AAPL"])
        with self.assertRaises(options_recovery.OptionsRecoveryError):
            options_recovery._rows(ko_fixture(), "KO")
        with self.assertRaises(options_recovery.OptionsRecoveryError):
            options_recovery.initial_state(ko_fixture(), started_at="2026-10-31T13:00:00+00:00", symbol="KO")

    def test_explicit_campaign_rows_allow_only_below_100_research_ko(self):
        rows, quality = options_recovery._rows(ko_fixture(), "KO", allow_research_ko=True)
        self.assertEqual(len(rows), 21)
        self.assertEqual(quality["symbols"], ["KO"])
        for price in ("100", "100.01"):
            data = ko_fixture()
            data["snapshots"][0]["underlying_price"] = price
            with self.assertRaisesRegex(options_recovery.OptionsRecoveryError, "strictly below100"):
                options_recovery._rows(data, "KO", allow_research_ko=True)

    def test_opt_in_is_typed_and_does_not_accept_arbitrary_symbol(self):
        for flag in (1, "true", None):
            with self.assertRaises(options_recovery.OptionsRecoveryError):
                options_recovery._rows(ko_fixture(), "KO", allow_research_ko=flag)
        with self.assertRaises(options_recovery.OptionsRecoveryError):
            options_recovery._rows(ko_fixture(), "VIX", allow_research_ko=True)


if __name__ == "__main__":
    unittest.main()
