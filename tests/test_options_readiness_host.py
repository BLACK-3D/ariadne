"""Optional inventory integration uses generated stores, never owner data."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

import test_options_stress_workflow as stores
import test_options_paper_readiness as fixtures
from test_brief_options_eod import fixture, rendered
from ariadne.options_paper_readiness import build_readiness
from ariadne.briefing_summary import compact_summary


class ReadinessHostTests(unittest.TestCase):
    def owner(self):
        case=stores.StressWorkflowTests("test_activation_keeps_one_account_all_financials_and_eod_configuration")
        case.setUp();self.addCleanup(case.doCleanups);case.prepare();case.activate()
        return case

    def test_absent_inventory_describes_same_account_without_writes(self):
        case=self.owner();before=case.financial()
        result=case.owner.status(case.clock)["paper_readiness"]
        self.assertEqual(result["inventory_status"],"unconfigured")
        self.assertEqual(result["configured_active_paper_roots"],["F"])
        self.assertEqual(result["shared_risk"]["cash"],"500")
        self.assertEqual(result["shared_risk"]["monthly_remaining"],"500")
        self.assertFalse(result["entry_authorized"])
        self.assertEqual(case.financial(),before)

    def test_local_metadata_adds_research_roots_without_source_or_financial_authority(self):
        case=self.owner();value=fixtures.index(fixtures.root("F"),fixtures.root("KO"))
        case.put("market-data/options/paper-readiness.json",json.dumps(value).encode());before=case.financial()
        result=case.owner.status(case.clock)["paper_readiness"]
        self.assertEqual(result["declared_root_count"],2)
        self.assertEqual(result["configured_active_paper_roots"],["F"])
        other=next(r for r in result["roots"] if r["symbol"]=="KO")
        self.assertIn("no_qualified_current_options_source",other["blockers"])
        self.assertFalse(other["current_eligibility_established"])
        self.assertFalse(other["entry_authorized"])
        self.assertEqual(case.financial(),before)

    def test_invalid_optional_metadata_is_diagnostic_and_does_not_suppress_collector(self):
        case=self.owner()
        for raw in (b'{"schema":1,"schema":1,"roots":[]}',b'x'*65537):
            with self.subTest(bytes=len(raw)):
                case.put("market-data/options/paper-readiness.json",raw);before=case.financial()
                result=case.owner.status(case.clock)
                self.assertEqual(result["paper_readiness"]["diagnostic_status"],"unavailable")
                self.assertIsNone(result["hold"])
                with patch.object(case.service,"capture_due",return_value=None),patch.object(case.owner.eod,"run_due",return_value={"exits":"still_checked"}) as collect:
                    self.assertEqual(case.owner.collect_due(case.clock),{"exits":"still_checked"})
                    collect.assert_called_once_with(case.clock)
                self.assertEqual(case.financial(),before)


class ReadinessBriefTests(unittest.TestCase):
    def test_saved_inventory_is_bounded_in_daily_and_compact_briefs_without_source_activation(self):
        value=fixture();now=datetime(2026,11,1,12,tzinfo=timezone.utc)
        value["options_paper"]["paper_readiness"]=build_readiness(fixtures.index(fixtures.root("F"),fixtures.root("KO")),paper=fixtures.paper(),checked_at=now)
        original=deepcopy(value)
        text=rendered(value)
        self.assertIn("2 declared roots; configured paper roots F",text)
        self.assertIn("additional paper-feed support",text)
        summary=compact_summary(value,now)["options_paper"]["source_inventory"]
        self.assertEqual(summary,{"declared_roots":2,"configured_paper_roots":["F"],"additional_current_sources_qualified":False})
        self.assertEqual(value,original)


if __name__=="__main__":unittest.main()
