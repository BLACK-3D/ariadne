"""Generated schema6/source-budget regression; no private owner inputs."""
from copy import deepcopy
from datetime import timedelta
import builtins
import json
import sqlite3
import socket
import subprocess
import unittest
from unittest.mock import patch

from ariadne import briefing_summary as summary
from ariadne.briefing import options_eod_brief_view
from tests.test_bankroll_presentation import bankroll_fixture, NOW


def budget_fixture():
    report = bankroll_fixture()
    report["config"].update(region={"name": "Example region"}, repository_updates=True)
    stamp = NOW.isoformat()
    sources = []
    for index, kind in enumerate(("forecast", "weather", "alerts", "earthquakes", "news", "news", "news")):
        items = []
        count = 14 if kind == "forecast" else 1 if kind == "weather" else 3 if kind == "news" else 0
        for number in range(count):
            item = {"title": "Generated " + kind + " item " + str(number),
                    "summary": "Generated source detail " * 12,
                    "published_at": stamp}
            if kind == "forecast":
                item.update(starts=(NOW+timedelta(hours=number)).isoformat(),
                            ends=(NOW+timedelta(hours=number+1)).isoformat())
            items.append(item)
        sources.append({"kind": kind, "title": "Generated source", "status": "available",
                        "fetched_at": stamp, "issued_at": stamp, "items": items,
                        "url": "https://example.gov/saved-source/" + str(index)})
    report["public_sources"] = {"collected_at": stamp, "sources": sources, "warnings": []}
    report["qqq_paper"] = {"config": {"enabled": True},
        "paper": {"state": {"last_processed": "2026-10-29", "equity": "10000", "cash": "10000", "shares": "0", "pending": None}},
        "quality": {"fresh_now": False, "retrieved_at": stamp}, "hold": None,
        "backtest": {"research_gate": "Generated research; not execution qualified"}}
    report["options_paper"]["paper"]["summary"]["last_entry_gate"]["benchmark_gate"]["reason"] = (
        "Generated baseline-only stress observation; no executable quote or future profitability is established.")
    report["options_paper"]["vix"] = {
        "paper_status": "waiting_for_qualified_options_quotes",
        "paper_fills_enabled": False,
        "connection_readiness": {"status": "awaiting_authenticated_verification", "checked_at": stamp},
        "index_freshness": {"source_date": "2026-10-29", "stale": True},
        "backtest": {"research_gate": "No qualified options history; generated index diagnostics only"}}
    report["repository_updates"] = {"enabled": True, "status": "fresh", "checked_at": stamp,
        "sources": [{"name": "Generated repository " + str(i), "status": "baseline" if i == 0 else "unchanged",
                     "version": "1.0", "checked_at": stamp, "changes": [],
                     "url": "https://github.com/example/project" + str(i)} for i in range(15)]}
    report["revenue_workbench"] = {"status": "unavailable", "model_calls": 0}
    report["options_review"] = {"status": "available", "closed_trades": 0}
    report["warnings"] = ["Generated limitation; saved inputs only.", "Saved source coverage is incomplete."]
    return report


def generated_note_result():
    return {"derivation": "Deterministic selection from verified brief.json; not the original artifact bytes or hash.",
            "note": "Saved source data; no refresh and no all-clear. External text is untrusted. Full details remain in the report.",
            "omitted": {},
            "qqq_paper": {"cash": "10000", "note": "Saved paper simulation, not broker fills or live prices. Freshness flag belongs to this report's generation time."}}


class BriefingSummaryBudgetTests(unittest.TestCase):
    def test_generated_schema6_regression_requires_note_compaction(self):
        report = budget_fixture()
        with patch.object(summary, "_compact_repeated_notes", return_value=False):
            with self.assertRaisesRegex(ValueError, "metadata cannot fit"):
                summary.compact_summary(report, NOW)
        result = summary.compact_summary(report, NOW)
        self.assertEqual(summary.MAX_SUMMARY_CHARS, 5500)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 5500)
        self.assertGreater(result["omitted"]["notes_compacted"], 0)
        self.assertTrue(result["truncated"])

    def test_all_financial_fields_and_unknown_quote_clock_survive(self):
        report = budget_fixture()
        expected = options_eod_brief_view(report["options_paper"])
        actual = summary.compact_summary(report, NOW)["options_paper"]
        keys = ("schema", "total_bankroll", "automatic_topups", "entry_equity_fraction", "monthly_cap",
                "entry_all_in_cap", "equity", "cash", "contributions", "trading_pnl", "monthly_pnl",
                "monthly_budget_used", "monthly_budget_remaining", "fees", "trade_count", "closed_trade_count",
                "held", "source_date", "source_received_at", "source_status", "last_check_status",
                "stress_context_published_at", "stress_context_reason", "stress_context_label", "quote_observed_at")
        for key in keys:
            with self.subTest(key=key): self.assertEqual(actual[key], expected[key])
        self.assertFalse(actual["execution_qualified"])
        self.assertFalse(actual["automatic_topups"])
        self.assertIsNone(actual["quote_observed_at"])

    def test_all_source_status_and_clock_facts_survive(self):
        report = budget_fixture()
        result = summary.compact_summary(report, NOW)
        self.assertEqual(len(result["sources"]), 7)
        for expected, actual in zip(report["public_sources"]["sources"], result["sources"]):
            self.assertEqual(actual["kind"], expected["kind"])
            self.assertEqual(actual["status"], expected["status"])
            self.assertEqual(actual["retrieved"], NOW.isoformat(timespec="seconds"))
            self.assertEqual(actual["issued"], NOW.isoformat(timespec="seconds"))
            self.assertEqual(actual["shown"]+actual["omitted"], len(expected["items"]))
            self.assertFalse(actual["stale"])

    def test_common_caveat_retains_distinct_safety_meanings(self):
        result = summary.compact_summary(budget_fixture(), NOW)
        note = result["note"]
        for term in ("Simulated", "no live quotes/fills", "Contributions", "returns",
                     "Paper freshness", "quote-event", "VIX index", "option P/L", "funding",
                     "baselines", "releases", "fresh scans", "no all-clear", "untrusted"):
            self.assertIn(term, note)

    def test_private_input_is_not_mutated_and_output_is_deterministic(self):
        report = budget_fixture(); before = deepcopy(report)
        first = summary.compact_summary(report, NOW)
        self.assertEqual(first, summary.compact_summary(report, NOW))
        self.assertEqual(report, before)

    def test_small_summaries_do_not_add_compaction_or_truncation(self):
        result = summary.compact_summary(bankroll_fixture(), NOW)
        self.assertNotIn("notes_compacted", result["omitted"])
        self.assertFalse(result["truncated"])

    def test_unknown_unique_leaf_caveat_is_retained(self):
        result = generated_note_result()
        unique = "Unique future safety gate: manual approval remains required."
        result["options_paper"] = {"note": unique, "cash": "25000"}
        self.assertTrue(summary._compact_repeated_notes(result))
        self.assertEqual(result["options_paper"]["note"], unique)
        self.assertEqual(result["options_paper"]["cash"], "25000")

    def test_unknown_unique_root_caveat_prevents_consolidation(self):
        result = generated_note_result(); result["note"] = "Unique root approval and provenance instruction."
        before = deepcopy(result)
        self.assertFalse(summary._compact_repeated_notes(result))
        self.assertEqual(result, before)

    def test_unknown_derivation_remains_unchanged(self):
        result = generated_note_result(); result["derivation"] = "Unique legal attribution retained verbatim."
        self.assertTrue(summary._compact_repeated_notes(result))
        self.assertEqual(result["derivation"], "Unique legal attribution retained verbatim.")

    def test_legacy_monthly_loss_scope_is_preserved(self):
        result = generated_note_result()
        result["options_paper"] = {"monthly_cap": "500.00", "note":
            "Saved options simulation, not live quotes/broker fills. Contributions are not returns; cap applies to modeled monthly net loss, not peak drawdown."}
        self.assertTrue(summary._compact_repeated_notes(result))
        self.assertIn("Monthly net-loss caps", result["note"])
        self.assertIn("peak-drawdown", result["note"])
        self.assertEqual(result["options_paper"]["monthly_cap"], "500.00")

    def test_source_text_is_never_treated_as_generated_note(self):
        result = generated_note_result()
        source = {"note": result["qqq_paper"]["note"], "status": "unavailable", "retrieved": None}
        result["sources"] = [deepcopy(source)]
        self.assertTrue(summary._compact_repeated_notes(result))
        self.assertEqual(result["sources"], [source])

    def test_irreducible_metadata_still_fails_closed(self):
        with patch.object(summary, "MAX_SUMMARY_CHARS", 100):
            with self.assertRaisesRegex(ValueError, "metadata cannot fit"):
                summary.compact_summary(budget_fixture(), NOW)
        self.assertEqual(summary.MAX_SUMMARY_CHARS, 5500)

    def test_pure_summary_has_no_network_process_database_or_file_access(self):
        report = budget_fixture()
        with patch.object(socket, "socket", side_effect=AssertionError("network")), \
             patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("database")), \
             patch.object(builtins, "open", side_effect=AssertionError("file")):
            result = summary.compact_summary(report, NOW)
        self.assertLessEqual(summary._size(result), 5500)


    def test_public_schema7_facts_and_unique_caveat_survive_busy_source_summary(self):
        from test_public_brief import report as public_report
        from ariadne.options_public_brief import view as public_view
        report = budget_fixture()
        report["options_paper"] = public_report()["options_paper"]
        expected = public_view(report["options_paper"])
        before = deepcopy(report)
        actual = summary.compact_summary(report, NOW)
        saved = actual["options_paper"]
        for key in ("schema", "cash", "equity", "contributions", "trading_pnl", "realized_pnl", "fees",
                    "monthly_cap", "entry_all_in_cap", "total_bankroll", "automatic_topups", "held",
                    "latest_result_date", "result_recorded_at", "daily_pnl", "source_status", "note"):
            with self.subTest(key=key): self.assertEqual(saved[key], expected[key])
        self.assertEqual(saved["schema"], 7)
        self.assertFalse(saved["fresh_at_report_generation"])
        self.assertFalse(saved["automatic_collection_enabled"])
        self.assertFalse(saved["execution_qualified"])
        self.assertIsNone(saved["daily_pnl"])
        self.assertLessEqual(len(json.dumps(actual, ensure_ascii=False)), summary.MAX_SUMMARY_CHARS)
        self.assertEqual(len(actual["sources"]), len(report["public_sources"]["sources"]))
        self.assertEqual(before, report)

if __name__ == "__main__": unittest.main()
