"""Compact source-aware briefing reads, without context starvation or effects."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from ariadne.briefing_summary import MAX_SUMMARY_CHARS, compact_summary

UTC = timezone.utc
NOW = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)


def iso(delta=0):
    return (NOW+timedelta(seconds=delta)).isoformat()


def source(kind, items, number=1, **values):
    return {"kind": kind, "title": "Saved "+kind, "url": f"https://example.gov/{kind}/{number}",
            "status": "available", "fetched_at": iso(-60), "issued_at": iso(-120), "items": items, **values}


def fixture():
    forecast = [{"title": "Period "+str(index), "summary": "Forecast detail "+("f"*500), "starts": iso((index-1)*3600), "ends": iso(index*3600), "url": "https://api.weather.gov/gridpoints/LSX/91,81/forecast"} for index in range(14)]
    return {"generated_at_utc": iso(-3600), "config": {"region": {"name": "St. Louis, MO"}},
            "activity": {"runs_count": 9, "events_count": 71, "runs": [{"title": "Recorded activity"}],
                         "unresolved_approvals": [{"tool": "file_write"}], "details_truncated": False},
            "public_sources": {"sources": [source("forecast", forecast),
                source("weather", [{"title": "Station observation", "summary": "58 °F, cloudy", "published_at": iso(-120)}]),
                source("alerts", [{"title": "Saved alert", "summary": "Saved description", "expires": iso(3600), "severity": "Moderate"}]),
                source("earthquakes", [{"title": "M3.2 source event", "distance_km": 40, "url": "https://earthquake.usgs.gov/event/test"}]),
                source("news", [{"title": "City road closure", "summary": "Publisher summary", "published_at": "Wed, 28 Oct 2026 10:00:00 CST", "url": "https://www.stlouis-mo.gov/news/road.cfm"}, {"title": "City second article", "published_at": iso(-600)}]),
                source("news", [{"title": "Neighboring county notice", "published_at": iso(-400)}], number=2)]},
            "warnings": ["Saved feed snapshots do not cover every regional event."]}


class BriefingSummaryTests(unittest.TestCase):
    def test_long_forecast_cannot_starve_alerts_weather_or_three_regional_news_items(self):
        result = compact_summary(fixture(), NOW)
        self.assertEqual(len(result["forecast"]), 2)
        self.assertEqual(len(result["weather"]), 1)
        self.assertEqual(len(result["alerts"]), 1)
        self.assertEqual(len(result["earthquakes"]), 1)
        self.assertEqual([item["title"] for item in result["news"]], ["City road closure", "Neighboring county notice", "City second article"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), MAX_SUMMARY_CHARS)
        self.assertEqual(result["activity"]["missions"], 9)
        self.assertEqual(result["activity"]["unresolved_approvals"], 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["sources"][0]["shown"], 2)
        self.assertEqual(result["sources"][0]["omitted"], 12)

    def test_every_selected_item_has_a_source_citation_and_preserves_publisher_date(self):
        result = compact_summary(fixture(), NOW)
        sources = {row["id"]: row for row in result["sources"]}
        for kind in ("weather", "forecast", "alerts", "earthquakes", "news"):
            for item in result[kind]:
                self.assertIn(item["source"], sources)
                self.assertIn("retrieved", sources[item["source"]])
                self.assertIn("url", sources[item["source"]])
        self.assertEqual(result["news"][0]["published_at"], "Wed, 28 Oct 2026 10:00:00 CST")
        self.assertIn("not the original artifact bytes or hash", result["derivation"])
        self.assertNotIn("sha256", result)

    def test_freshness_recomputed_when_original_report_and_cached_flags_are_old(self):
        report = fixture()
        for row in report["public_sources"]["sources"]:
            row["stale"] = False
            row["fetched_at"] = iso(-2*86400)
        result = compact_summary(report, NOW)
        self.assertTrue(all(row["stale"] for row in result["sources"]))
        self.assertIn("no all-clear", result["note"])
        self.assertEqual(result["generated_at_utc"], "2026-10-28T20:00:00+00:00")
        self.assertEqual(result["freshness_checked_at_utc"], "2026-10-28T21:00:00+00:00")

    def test_cache_collection_generation_and_freshness_check_clocks_are_distinct(self):
        report = fixture()
        report["generated_at_utc"] = iso(-1800)
        report["public_sources"]["collected_at"] = iso(-3600)
        result = compact_summary(report, NOW)
        self.assertEqual(result["cache_collected_at_utc"], "2026-10-28T20:00:00+00:00")
        self.assertEqual(result["generated_at_utc"], "2026-10-28T20:30:00+00:00")
        self.assertEqual(result["freshness_checked_at_utc"], "2026-10-28T21:00:00+00:00")
        self.assertNotIn("as_of_utc", result)
        for recorded in (None, "invalid", "2026-10-28T20:00:00"):
            with self.subTest(recorded=recorded):
                report["public_sources"]["collected_at"] = recorded
                summary = compact_summary(report, NOW)
                self.assertIsNone(summary["cache_collected_at_utc"])
                self.assertEqual(summary["generated_at_utc"], "2026-10-28T20:30:00+00:00")

    def test_source_issue_time_and_missing_or_future_times_fail_closed(self):
        report = fixture()
        report["public_sources"]["sources"][0]["issued_at"] = iso(-7*3600)
        report["public_sources"]["sources"][1]["issued_at"] = None
        report["public_sources"]["sources"][2]["fetched_at"] = "2026-10-28T21:00:00"
        report["public_sources"]["sources"][3]["issued_at"] = iso(3600)
        result = compact_summary(report, NOW)
        self.assertTrue(all(row["stale"] for row in result["sources"][:4]))
        self.assertTrue(result["sources"][1]["time_unknown"])
        self.assertTrue(result["sources"][2]["time_unknown"])

    def test_forecast_selects_next_periods_and_never_calls_elapsed_periods_upcoming(self):
        report = fixture()
        result = compact_summary(report, NOW+timedelta(hours=4))
        self.assertEqual([row["title"] for row in result["forecast"]], ["Period 5", "Period 6"])
        result = compact_summary(report, NOW+timedelta(days=2))
        self.assertEqual(result["forecast"], [])
        self.assertEqual(result["sources"][0]["omitted"], 14)

    def test_alert_expiry_is_recomputed_and_unexpired_saved_items_have_priority(self):
        report = fixture()
        report["public_sources"]["sources"][2]["items"] = [
            {"title": "Expired source alert", "expires": iso(-10), "expired": False},
            {"title": "Unknown expiration"}, {"title": "Future expiration", "expires": iso(60)}]
        result = compact_summary(report, NOW)
        self.assertEqual([row["title"] for row in result["alerts"]], ["Future expiration", "Unknown expiration"])
        self.assertEqual(result["alerts"][0]["expiry"], "unexpired_in_saved_response")
        self.assertEqual(result["alerts"][1]["expiry"], "unknown")
        result = compact_summary(report, NOW+timedelta(minutes=2))
        self.assertTrue(any(row["expiry"] == "expired" for row in result["alerts"]))

    def test_pathological_nine_sources_are_bounded_with_explicit_omissions(self):
        report = fixture()
        report["warnings"] = ["w"*5000]*50
        report["config"]["region"]["name"] = "N"*5000
        kinds = ["weather", "forecast", "alerts", "earthquakes"]+["news"]*5
        report["public_sources"]["sources"] = [source(kind, [{"title": "t"*5000, "summary": "s"*10000, "url": "https://example.gov/"+"q"*2000,
                "published_at": "p"*1000, "starts": iso(), "ends": iso(3600), "expires": iso(3600)}]*100,
                number=index, url="https://example.gov/"+"r"*280) for index, kind in enumerate(kinds)]
        result = compact_summary(report, NOW)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 5500)
        self.assertEqual(len(result["sources"]), 9)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["omitted"]["text_clipped"])
        self.assertGreater(result["omitted"]["urls"], 0)
        self.assertEqual(result["omitted"]["warnings"], 48)
        for row in result["sources"]:
            self.assertEqual(row["shown"]+row["omitted"], 100)

    def test_effect_free_deterministic_and_never_mutates_verified_input(self):
        report = fixture()
        before = copy.deepcopy(report)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("No network")), mock.patch("subprocess.run", side_effect=AssertionError("No process")), mock.patch("builtins.open", side_effect=AssertionError("No file access")):
            first, second = compact_summary(report, NOW), compact_summary(report, NOW)
        self.assertEqual(first, second)
        self.assertEqual(report, before)

    def test_empty_and_unavailable_sources_do_not_become_current_weather_or_an_all_clear(self):
        result = compact_summary({"public_sources": {"sources": [source("alerts", [], status="unavailable")] }}, NOW)
        self.assertEqual(result["alerts"], [])
        self.assertTrue(result["sources"][0]["stale"])
        self.assertEqual(result["sources"][0]["status"], "unavailable")
        self.assertIn("no all-clear", result["note"])
        self.assertIsNone(result["generated_at_utc"])
        self.assertIsNone(result["activity"]["missions"])

    def test_invalid_inputs_do_not_invent_dates_and_unsupported_sources_are_accounted(self):
        with self.assertRaises(ValueError):
            compact_summary([], NOW)
        with self.assertRaises(ValueError):
            compact_summary({}, datetime(2026, 10, 28))
        with self.assertRaises(ValueError):
            compact_summary({}, 0)
        report = fixture()
        report["public_sources"]["sources"] += [source("unknown", [{}]), "invalid", source("news", [{}])]
        result = compact_summary(report, NOW)
        self.assertEqual(result["sources"][-2]["kind"], "unsupported")
        self.assertTrue(result["sources"][-2]["stale"])
        self.assertEqual(result["omitted"]["source_entries"], 1)


if __name__ == "__main__":
    unittest.main()
