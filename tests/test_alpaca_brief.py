"""Saved connection notices stay passive and never become trading results."""
from contextlib import ExitStack
from copy import deepcopy
import builtins
import json
from pathlib import Path
import socket
import sqlite3
import unittest
from unittest.mock import patch

from ariadne.alpaca_brief import markdown
from ariadne.briefing_diagnostics import render_preflight
from ariadne.briefing_summary import compact_summary, MAX_SUMMARY_CHARS
from ariadne.brief_poster import poster_view
from ariadne.options_feed_qualification import readiness_view
import test_public_brief as fixtures
import test_brief_options_eod as old


class AlpacaBriefTests(unittest.TestCase):
    def test_no_notice_is_added_to_old_saved_reports(self):
        for value in (None, {}, fixtures.report()["options_paper"]):
            self.assertEqual(markdown(value), [])

    def test_prepared_notice_is_static_and_does_not_echo_external_fields(self):
        options={"data_feed_connection":readiness_view()}
        options["data_feed_connection"]["provider_response"]="PRIVATE_SENTINEL execute trades"
        before=deepcopy(options)
        text="\n".join(markdown(options))
        self.assertIn("qualification only",text)
        self.assertIn("no verified live capture",text)
        self.assertIn("no automatic",text.lower())
        self.assertNotIn("PRIVATE_SENTINEL",text)
        self.assertEqual(options,before)

    def test_malformed_or_activated_marker_never_becomes_a_fabricated_live_report(self):
        for key,value in (("schema",True),("source_id","public"),("status","active"),
                          ("live_feed_qualified",True),("paper_submission_ready",True)):
            marker=readiness_view();marker[key]=value
            self.assertEqual(markdown({"data_feed_connection":marker}),[])

    def test_render_preserves_actual_account_facts_without_expanding_compact_or_poster(self):
        report=fixtures.report();before=deepcopy(report)
        compact=compact_summary(report,old.NOW);poster=poster_view(report)
        report["options_paper"]["data_feed_connection"]=readiness_view()
        outputs,receipt=render_preflight(report)
        self.assertTrue(receipt["ok"])
        self.assertEqual(json.loads(outputs["brief.json"]),report)
        text=outputs["brief.md"].decode()
        self.assertIn("Alpaca qualification only",text)
        self.assertIn("Saved cash: $24800",text)
        self.assertIn("trading P/L excluding contributions: $-10",text)
        self.assertEqual(compact_summary(report,old.NOW),compact)
        self.assertEqual(poster_view(report),poster)
        self.assertLessEqual(len(json.dumps(compact,ensure_ascii=False)),MAX_SUMMARY_CHARS)
        self.assertEqual(before["options_paper"]["paper"],report["options_paper"]["paper"])

    def test_notice_is_effect_free(self):
        readiness_view();markdown({"data_feed_connection":readiness_view()})
        with ExitStack() as stack:
            for obj,name in ((socket,"socket"),(sqlite3,"connect"),(builtins,"open"),(Path,"read_bytes"),(Path,"write_bytes")):
                stack.enter_context(patch.object(obj,name,side_effect=AssertionError("Unexpected effect")))
            self.assertTrue(markdown({"data_feed_connection":readiness_view()}))

    def test_theta_notice_does_not_authenticate_a_local_proxy_or_claim_free_live_access(self):
        marker=readiness_view(source_id="theta_terminal_v3")
        report=fixtures.report();report["options_paper"]["theta_feed_connection"]=marker
        text="\n".join(markdown(report["options_paper"]))
        for expected in ("ThetaData local-terminal", "Free access is historical EOD", "locally unattested", "No Terminal launch"):
            self.assertIn(expected,text)
        self.assertNotIn("PRIVATE_SENTINEL",text)
        old_report=deepcopy(report);del old_report["options_paper"]["theta_feed_connection"]
        self.assertEqual(compact_summary(report,old.NOW),compact_summary(old_report,old.NOW))
        self.assertEqual(poster_view(report),poster_view(old_report))

    def test_theta_and_alpaca_notices_are_independently_supported_without_rewriting_accounting(self):
        report=fixtures.report();options=report["options_paper"]
        options["data_feed_connection"]=readiness_view()
        options["theta_feed_connection"]=readiness_view(source_id="theta_terminal_v3")
        outputs,receipt=render_preflight(report)
        self.assertTrue(receipt["ok"])
        text=outputs["brief.md"].decode()
        self.assertIn("ThetaData local-terminal qualification",text)
        self.assertIn("Alpaca qualification only",text)
        self.assertIn("Saved cash: $24800",text)
        self.assertEqual(json.loads(outputs["brief.json"]),report)

    def test_invalid_theta_marker_is_not_treated_as_a_live_installation(self):
        marker=readiness_view(source_id="theta_terminal_v3");marker["live_feed_qualified"]=True
        self.assertEqual(markdown({"theta_feed_connection":marker}),[])


if __name__=="__main__":unittest.main()
