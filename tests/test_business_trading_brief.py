"""Actual workbench inputs and passive paper reviews stay qualified in briefs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.briefing import BriefingService
from ariadne.briefing_summary import compact_summary,MAX_SUMMARY_CHARS
from ariadne.business_trading_brief import revenue_view,options_review_view
from ariadne.config import Settings
from ariadne.options_journal import build_review
from ariadne.revenue_workflow import RevenueWorkbench
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from test_brief_options_eod import fixture as brief_fixture,NOW
from test_revenue_planner import fixture as revenue_fixture
from test_options_eod import state


class BusinessTradingBriefTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/"runtime/tmp";root.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="business-trading-brief-",dir=root);self.addCleanup(temporary.cleanup)
        settings=Settings(data_dir=Path(temporary.name)/"data")
        self.store=Store(settings.data_dir/"ariadne.sqlite3");self.store.set_setting("paused",False)
        self.workbench=RevenueWorkbench(ToolRegistry(settings),self.store)

    def report(self):
        report=brief_fixture();report["revenue_workbench"]=self.workbench.brief_snapshot(NOW)
        current=state();review=build_review({"id":"private-fixture","state":current},[],now=current["last_decided_at"])
        report["options_review"]=options_review_view(review)
        return report

    def plan(self):
        return self.workbench.plan({"request_id":"comparison","expected_revision":0,"scenario":revenue_fixture()},NOW)

    def test_initial_brief_does_not_invent_business_or_paper_success(self):
        report=self.report()
        self.assertEqual(revenue_view(report["revenue_workbench"])["status"],"not_configured")
        self.assertEqual(report["options_review"]["status"],"waiting_for_first_capture")
        self.assertIsNone(report["options_review"]["observed_win_rate_pct"])
        self.assertEqual(self.workbench.status(NOW)["evidence"],[])

    def test_actual_saved_hypothesis_flows_to_full_and_compact_without_new_actions(self):
        self.plan();report=self.report();before=deepcopy(report)
        with patch("socket.socket",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No model")),patch("ariadne.brief_poster.poster_view",return_value={"svg":"<svg/>"}):
            markdown=BriefingService.__new__(BriefingService)._render(report)["brief.md"].decode()
            compact=compact_summary(report,NOW)
        self.assertIn("## Business revenue comparison and recorded cash",markdown)
        self.assertIn("Hypothetical planning",markdown)
        self.assertIn("actual profit is unverified",markdown)
        self.assertIn("## Options paper evidence journal",markdown)
        self.assertIn("waiting_for_first_capture",markdown)
        self.assertFalse(compact["business_revenue"]["profit_verified"])
        self.assertFalse(compact["options_journal"]["execution_qualified"])
        self.assertEqual(report,before)

    def test_recorded_sale_and_net_cash_remain_distinct_from_profit(self):
        self.plan()
        for index,(kind,amount) in enumerate((("sale_reported","100.00"),("payout_received","60.00"),("cost_paid","10.00"))):
            event={"scenario_id":"comparison","kind":kind,"occurred_at":NOW.isoformat(),"amount":amount,"minutes":None,
                   "units":1 if kind=="sale_reported" else None,"note":"Generated independent cash-flow test fixture","reference_path":None}
            self.workbench.record_evidence({"request_id":"cash-"+str(index),"expected_revision":self.workbench.status(NOW)["revision"],"event":event},NOW)
        view=revenue_view(self.workbench.brief_snapshot(NOW))
        self.assertEqual(view["cash"]["sales_reported"],"100.00")
        self.assertEqual(view["cash"]["net_reported_cash"],"50.00")
        self.assertFalse(view["actual_profit_verified"])

    def test_malformed_metadata_fails_closed_without_promoting_profit_or_winrate(self):
        for value in (None,[],{"status":[]},{"status":"available","plan_summary":{}},{"status":"available","plan_summary":{"planning_only":False}}):
            self.assertEqual(revenue_view(value)["status"],"unavailable")
        for value in (None,[],{"status":[]},{"status":"recorded_observations","execution_qualified":True,"research_only":True}):
            self.assertEqual(options_review_view(value)["status"],"unavailable")
        report=self.report();report["options_review"]=None
        self.assertFalse(compact_summary(report,NOW)["options_journal"]["execution_qualified"])

    def test_unavailable_cash_is_not_silently_zero_and_missing_costs_need_evidence(self):
        value=revenue_fixture();value["offers"][0]["costs"]["acquisition_per_unit"]=None
        self.workbench.plan({"request_id":"unknown-cost","expected_revision":0,"scenario":value},NOW)
        view=revenue_view(self.workbench.brief_snapshot(NOW))
        self.assertTrue(view["unknown_costs"])
        self.assertIn("missing supplier",view["next_action"])
        source=self.workbench.brief_snapshot(NOW);source["cash_summary"]["net_reported_cash"]=None
        self.assertIsNone(revenue_view(source)["cash"]["net_reported_cash"])

    def test_options_business_and_nine_long_sources_fit_existing_summary_budget(self):
        self.plan();report=self.report()
        for index in range(9):
            report["public_sources"]["sources"].append({"kind":"news","title":"T"*120,"url":"https://example.gov/"+"x"*250,
                "fetched_at":NOW.isoformat(),"issued_at":NOW.isoformat(),"status":"fresh","items":[{"title":"H"*500,"summary":"S"*4000,"url":"https://example.gov/"+"y"*250}]})
        compact=compact_summary(report,NOW)
        self.assertLessEqual(len(json.dumps(compact,ensure_ascii=False,allow_nan=False)),MAX_SUMMARY_CHARS)
        self.assertIn("business_revenue",compact);self.assertIn("options_journal",compact)
