"""Unused financial metadata cannot remove weather from the one-page poster."""
from copy import deepcopy
import unittest
from test_brief_poster import NOW, report, visible
from ariadne.brief_poster import poster_view


class PosterBudgetTests(unittest.TestCase):
    def test_growing_account_metadata_keeps_saved_weather_and_held_capital_notice(self):
        value = report()
        value["options_paper"] = {"config": {"enabled": True}, "hold": "Needs review",
            "paper": {"summary": {"schema": 6, "equity": "25000.00", "contributions": "25000.00"}},
            "backtest": {"research_gate": "Unqualified "*500},
            "vix": {"paper_status": "No verified fills "*500}}
        value["qqq_paper"] = {"config": {"enabled": True}, "hold": "Separate hold",
            "paper": {"state": {"equity": "25000.00"}}, "quality": {"fresh_now": False}}
        before = deepcopy(value)
        output = poster_view(value, NOW)
        text = visible(output)
        for fact in ("58", "Mostly Cloudy", "Flood advisory", "Road closure", "OPTIONS PAPER HELD", "$25,000 total / no top-ups"):
            self.assertIn(fact, text)
        self.assertEqual(value, before)
        self.assertEqual(output["model_calls"], 0)


    def test_public_schema7_high_metadata_keeps_weather_and_dated_bankroll_notice(self):
        from test_public_brief import report as public_report
        value = report()
        value["options_paper"] = public_report()["options_paper"]
        value["options_paper"]["backtest"] = {"research_gate": "Not qualified "*500}
        value["options_paper"]["vix"] = {"paper_status": "No verified fills "*500}
        value["qqq_paper"] = {"config": {"enabled": True}, "hold": "Separate hold",
            "paper": {"state": {"equity": "25000.00"}}, "quality": {"fresh_now": False}}
        before = deepcopy(value)
        output = poster_view(value, NOW)
        text = visible(output)
        for fact in ("58", "Mostly Cloudy", "Flood advisory", "Road closure",
                     "PUBLIC OPTIONS PAPER", "$25,000 total / no top-ups", "dated results"):
            self.assertIn(fact, text)
        self.assertNotIn("$500 monthly budget", text)
        self.assertEqual(value, before)
        self.assertEqual(output["model_calls"], 0)

    def test_poster_compact_input_omits_account_sections_but_keeps_public_sources(self):
        from unittest.mock import patch
        from ariadne import brief_poster
        value = report()
        value["options_paper"] = {"config": {"enabled": True},
            "paper": {"summary": {"schema": 7, "trading_pnl": "0"}},
            "unused_financial_metadata": "Generated "*10000}
        original = brief_poster.compact_summary
        inputs = []
        def checked(source_view, clock):
            inputs.append(source_view)
            for key in ("options_paper", "qqq_paper", "venture_checkin", "options_review", "revenue_workbench"):
                self.assertNotIn(key, source_view)
            self.assertEqual(source_view["public_sources"], value["public_sources"])
            return original(source_view, clock)
        with patch.object(brief_poster, "compact_summary", side_effect=checked):
            output = poster_view(value, NOW)
        self.assertEqual(len(inputs), 1)
        self.assertIn("Mostly Cloudy", visible(output))
