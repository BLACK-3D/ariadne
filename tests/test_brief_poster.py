"""One-page brief facts, bounded graphics, provenance and stored artifact checks."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from ariadne.brief_poster import HEIGHT, MAX_SVG_BYTES, WIDTH, poster_view
from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 11, 0, tzinfo=UTC)


def stamp(seconds=0):
    return (NOW+timedelta(seconds=seconds)).isoformat()


def source(kind, items, **values):
    return {"kind": kind, "url": "https://example.gov/"+kind, "status": "available",
            "fetched_at": stamp(-60), "issued_at": stamp(-90), "items": items, **values}


def report():
    return {"generated_at_utc": stamp(), "local_date": "2026-10-29",
            "config": {"region": {"name": "St. Louis, MO"}},
            "activity": {"runs_count": 9, "events_count": 71, "unresolved_approvals": [{"tool": "file_write"}]},
            "public_sources": {"collected_at": stamp(-60), "sources": [
                source("weather", [{"title": "Station", "summary": "Mostly Cloudy; temperature: 58 °F (14.4 °C); wind: 5 mph"}]),
                source("forecast", [{"title": "Today", "summary": "A chance of rain after noon.", "ends": stamp(8*3600)}]),
                source("alerts", [{"title": "Flood advisory", "expires": stamp(3600)}]),
                source("news", [{"title": "Road closure", "summary": "A saved regional notice."}]),
                source("earthquakes", [{"title": "M2.5 source event", "distance_km": 70}])]}, "warnings": []}


def visible(result):
    root = ET.fromstring(result["svg"])
    return " ".join(element.text or "" for element in root.iter())


class BriefPosterTests(unittest.TestCase):
    def test_self_contained_exactly_one_portrait_svg_with_source_labels(self):
        result = poster_view(report(), NOW)
        root = ET.fromstring(result["svg"])
        self.assertEqual((result["width"], result["height"]), (WIDTH, HEIGHT))
        self.assertEqual(root.attrib["viewBox"], "0 0 1080 1350")
        self.assertEqual((root.attrib["width"], root.attrib["height"]), ("1080", "1350"))
        self.assertLess(len(result["svg"].encode()), MAX_SVG_BYTES)
        for name in ("St. Louis, MO", "Flood advisory", "Road closure", "58", "S1", "S5", "SAVED / RECENT"):
            self.assertIn(name, visible(result))
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["generated_at_utc"], stamp())

    def test_real_weather_temperature_is_not_taken_from_wind_or_fabricated(self):
        value = report()
        value["public_sources"]["sources"][0]["items"][0]["summary"] = "Wind 58 mph; humidity: 70 percent"
        svg = poster_view(value, NOW)["svg"]
        self.assertIn('font-size="110" font-weight="700" letter-spacing="-5">—</text>', svg)
        self.assertIn("NO DATA", svg)

    def test_missing_sources_are_explicit_and_never_become_good_weather_or_all_clear(self):
        value = report(); value["public_sources"] = {}; value["activity"]["unresolved_approvals"] = []
        content = visible(poster_view(value, NOW))
        self.assertIn("No saved station observation", content)
        self.assertIn("Forecast unavailable", content)
        self.assertIn("UNAVAILABLE / NOT INFERRED", content)
        self.assertIn("not an all-clear", content)
        self.assertIn("Check the source gaps", content)
        self.assertNotIn("SAVED / RECENT", content)

    def test_freshness_is_recomputed_and_failed_refresh_preserves_explicit_status(self):
        value = report()
        value["public_sources"]["sources"][0].update(status="cached_refresh_failed", stale=False)
        content = visible(poster_view(value, NOW+timedelta(days=2)))
        self.assertIn("STALE / REFRESH FAILED", content)
        self.assertNotIn("SAVED / RECENT", content)
        self.assertIn("AGE CHECKED 31 Oct 11:00 UTC", content)

    def test_unknown_source_and_issue_times_are_not_recent(self):
        value = report()
        for row in value["public_sources"]["sources"]:
            row["fetched_at"] = "bad time"; row["issued_at"] = None
        content = visible(poster_view(value, NOW))
        self.assertIn("STALE", content)
        self.assertIn("time unavailable", content)
        self.assertNotIn("SAVED / RECENT", content)

    def test_expired_alerts_are_labeled_not_presented_as_active(self):
        value = report(); value["public_sources"]["sources"][2]["items"][0]["expires"] = stamp(-1)
        self.assertIn("EXPIRED", visible(poster_view(value, NOW)))

    def test_external_markup_and_controls_remain_literal_xml_text(self):
        value = report()
        attack = '<script>alert(1)</script><image href="https://evil.example/a" onload="x"/> & " < > \x01\ud800'
        value["config"]["region"]["name"] = attack
        value["public_sources"]["sources"][2]["items"][0]["title"] = attack
        result = poster_view(value, NOW)
        root = ET.fromstring(result["svg"])
        allowed = {"svg", "title", "desc", "rect", "circle", "path", "text"}
        self.assertTrue(all(element.tag.split("}")[-1] in allowed for element in root.iter()))
        self.assertFalse(any("href" in key or key.startswith("on") for element in root.iter() for key in element.attrib))
        self.assertIn("&lt;script&gt;", result["svg"])
        self.assertNotIn("\x01", result["svg"])

    def test_pathological_inputs_stay_within_geometry_and_byte_budget_and_admit_omissions(self):
        value = report()
        value["config"]["region"]["name"] = "W"*5000
        kinds = ["weather", "forecast", "alerts", "earthquakes"]+["news"]*5
        value["public_sources"]["sources"] = [source(kind, [{"title": "W"*10000, "summary": "w"*10000, "ends": stamp(100), "expires": stamp(100)}]*100) for kind in kinds]
        result = poster_view(value, NOW)
        self.assertLessEqual(len(result["svg"].encode()), MAX_SVG_BYTES)
        self.assertTrue(result["omissions"])
        self.assertIn("omitted", visible(result))
        for element in ET.fromstring(result["svg"]).iter():
            if element.tag.endswith("text"):
                self.assertTrue(0 <= float(element.attrib["x"]) < WIDTH)
                self.assertTrue(0 <= float(element.attrib["y"]) < HEIGHT)

    def test_renderer_is_pure_deterministic_and_cannot_access_network_models_or_files(self):
        value = report(); original = copy.deepcopy(value)
        with mock.patch("builtins.open", side_effect=AssertionError("No files")), mock.patch("socket.socket", side_effect=AssertionError("No sockets")), mock.patch("subprocess.Popen", side_effect=AssertionError("No processes")):
            first = poster_view(value, NOW)
            self.assertEqual(first, poster_view(value, NOW))
        self.assertEqual(value, original)

    def test_priority_uses_recorded_approvals_and_full_report_remains_explicit(self):
        result = poster_view(report(), NOW)
        self.assertIn("Review 1 unresolved approval.", visible(result))
        self.assertIn("full details + URLs in report", visible(result))
        self.assertIn("Flood advisory", result["accessible_text"])

    def test_missing_activity_does_not_invent_zero_approvals(self):
        value = report(); value.pop("activity")
        content = visible(poster_view(value, NOW))
        self.assertIn("Approval status was not supplied", content)
        self.assertNotIn("No unresolved approvals were recorded", content)

    def test_renderer_only_region_and_forecast_title_clipping_are_reported(self):
        value = report(); value["activity"]["unresolved_approvals"] = []
        # No raw report details, excessive source items or selector text clips.
        value["public_sources"]["sources"][0]["items"][0]["summary"] = "58 °F, cloudy"
        value["public_sources"]["sources"][3]["items"][0]["summary"] = ""
        self.assertFalse(poster_view(value, NOW)["omissions"])
        value["config"]["region"]["name"] = "A"*70
        value["public_sources"]["sources"][1]["items"][0]["title"] = "B"*60
        from ariadne.briefing_summary import compact_summary
        self.assertFalse(compact_summary(value, NOW)["truncated"])
        result = poster_view(value, NOW)
        self.assertTrue(result["omissions"])
        self.assertIn("items/text omitted", visible(result))


class NoModel:
    def status(self):
        return {"available": False, "models": []}

    def chat(self, *_args):
        raise AssertionError("Brief presentation must not execute model calls")


class BriefPosterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".poster-review-")
        self.settings = Settings(data_dir=Path(self.temp.name), api_token="fixture-poster-key")
        self.store = Store(self.settings.data_dir/"state.sqlite3")
        self.engine = Engine(self.settings, self.store, NoModel())
        self.app = Application(self.settings, self.store, self.engine)

    def tearDown(self):
        self.app.stop_services(); self.temp.cleanup()

    def request(self, path, token=True):
        parsed = urlsplit(path)
        env = {"HTTP_HOST": "localhost:8080", "PATH_INFO": parsed.path, "QUERY_STRING": parsed.query,
               "REQUEST_METHOD": "GET", "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO()}
        if token: env["HTTP_AUTHORIZATION"] = "Bearer fixture-poster-key"
        response = {}
        def start(status, _headers): response["status"] = int(status.split()[0])
        payload = b"".join(self.app(env, start))
        return response["status"], json.loads(payload)

    def test_generated_report_saves_hashed_svg_and_replays_exact_original_artifacts(self):
        receipt = self.app.briefing.run("poster-proof", NOW)
        row = next(row for row in receipt["artifacts"] if row["path"].endswith(".svg"))
        content = (self.settings.workspace/row["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(content).hexdigest(), row["sha256"])
        self.assertEqual(ET.fromstring(content).attrib["viewBox"], "0 0 1080 1350")
        replay = self.app.briefing.run("poster-proof", NOW+timedelta(hours=1))
        self.assertEqual(receipt["artifacts"], replay["artifacts"])

    def test_authenticated_api_derives_preview_from_verified_json_without_models(self):
        receipt = self.app.briefing.run("poster-api", NOW)
        self.assertEqual(self.request("/api/briefing/report?request_id=poster-api", False)[0], 401)
        status, result = self.request("/api/briefing/report?request_id=poster-api")
        self.assertEqual(status, 200)
        self.assertEqual(result["poster"]["mime"], "image/svg+xml")
        row = next(row for row in receipt["artifacts"] if row["path"].endswith("brief.json"))
        self.assertEqual(result["poster"]["evidence_json"]["sha256"], row["sha256"])
        self.assertIn("content", result)

    def test_changed_json_is_rejected_even_when_markdown_is_unchanged(self):
        receipt = self.app.briefing.run("poster-tamper", NOW)
        row = next(row for row in receipt["artifacts"] if row["path"].endswith("brief.json"))
        (self.settings.workspace/row["path"]).write_text('{"altered":true}')
        self.assertEqual(self.request("/api/briefing/report?request_id=poster-tamper")[0], 409)

    def test_older_two_artifact_receipt_still_has_freshness_aware_api_preview(self):
        receipt = self.app.briefing.run("poster-legacy", NOW)
        receipt["artifacts"] = [row for row in receipt["artifacts"] if not row["path"].endswith("brief.svg")]
        with self.app.briefing.database() as db:
            db.execute("UPDATE briefing_runs SET receipt=? WHERE request_id=?", (json.dumps(receipt), "poster-legacy"))
        self.assertEqual(self.app.briefing.run("poster-legacy", NOW)["artifacts"], receipt["artifacts"])
        self.assertEqual(self.request("/api/briefing/report?request_id=poster-legacy")[0], 200)

    def test_saved_poster_tampering_is_held_without_overwrite(self):
        receipt = self.app.briefing.run("poster-integrity", NOW)
        row = next(row for row in receipt["artifacts"] if row["path"].endswith("brief.svg"))
        path = self.settings.workspace/row["path"]; path.write_text("Owner changed poster")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.app.briefing.run("poster-integrity", NOW)
        self.assertEqual(path.read_text(), "Owner changed poster")


if __name__ == "__main__":
    unittest.main()
