"""Owner controls and the existing offline briefing clock compose once."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class NoModel:
    def status(self):
        return {"available": False, "models": []}

    def chat(self, *args, **kwargs):
        raise AssertionError("Daily venture work must never call inference")


class VentureAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".venture-api-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name), api_token="venture-tests")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.engine = Engine(self.settings, self.store, NoModel())
        self.app = Application(self.settings, self.store, self.engine)
        self.now = datetime(2026, 10, 29, 11, tzinfo=timezone.utc)
        raw = b'{"summary":"A fixed-scope administration pilot, no customers yet."}'
        (self.settings.workspace / "plan.json").write_bytes(raw)
        self.board = {"schema": 1, "enabled": True, "paused": False, "projects": [{
            "id": "admin", "name": "Quote administration", "domain": "business",
            "sources": [{"role": "plan", "path": "plan.json", "sha256": hashlib.sha256(raw).hexdigest()}]}],
            "tasks": [{"id": "offer", "project_id": "admin", "kind": "business_offer_pack",
                       "title": "Prepare pilot", "requires_owner_evidence": True},
                      {"id": "interviews", "project_id": "admin", "kind": "business_validation_pack",
                       "title": "Prepare validation", "depends_on": ["offer"]}]}

    def request(self, path, method="GET", data=None, token=True, origin=None):
        body = json.dumps({} if data is None else data).encode()
        env = {"HTTP_HOST": "localhost:8080", "REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        if token:
            env["HTTP_AUTHORIZATION"] = "Bearer venture-tests"
        if origin:
            env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            response.update(status=int(status.split()[0]), headers=dict(headers))
        raw = b"".join(self.app(env, start))
        response["data"] = json.loads(raw) if response["headers"]["Content-Type"].startswith("application/json") else raw
        return response

    def enable(self):
        revision = self.app.venture.status()["revision"]
        response = self.request("/api/venture/config", "PUT", {"board": self.board, "expected_revision": revision})
        self.assertEqual(response["status"], 200, response)
        self.app.briefing.configure({"enabled": True}, self.now-timedelta(days=1))
        return response["data"]

    def report(self, receipt):
        item = next(item for item in receipt["artifacts"] if item["path"].endswith("/brief.json"))
        return json.loads((self.settings.workspace/item["path"]).read_bytes())

    def test_owner_auth_origin_and_private_controls(self):
        self.assertEqual(self.request("/api/venture", token=False)["status"], 401)
        self.assertEqual(self.request("/api/venture/config", "PUT", {}, origin="https://elsewhere.example")["status"], 403)
        self.assertEqual(self.request("/api/venture")["data"]["state"], "disabled")
        self.assertEqual(self.request("/api/venture/run", "POST")["status"], 409)
        self.assertEqual(self.request("/api/venture/config", "PUT", {"board": self.board, "expected_revision": True})["status"], 400)
        first = self.enable()
        conflict = self.request("/api/venture/config", "PUT", {"board": self.board, "expected_revision": first["revision"]-1})
        self.assertEqual(conflict["status"], 409, conflict)
        self.assertEqual(conflict["data"]["revision"], first["revision"])

    def test_fixed_asset_routes_preserve_csp(self):
        for name in ("venture.js", "venture.css"):
            response = self.request("/static/"+name, token=False)
            self.assertEqual(response["status"], 200)
            self.assertEqual(response["data"], (Path(__file__).resolve().parents[1]/"ariadne/static"/name).read_bytes())
            self.assertIn("script-src 'self'", response["headers"]["Content-Security-Policy"])

    def test_native_brief_advances_once_without_network_models_or_new_scheduler(self):
        self.enable()
        with patch("socket.create_connection", side_effect=AssertionError("No network")), patch("subprocess.Popen", side_effect=AssertionError("No worker")):
            receipt = self.app.briefing.run_due(self.now)
            report = self.report(receipt)
            pulse = report["venture_checkin"]
            self.assertEqual(pulse["status"], "completed", pulse)
            self.assertEqual(len(pulse["advanced_tasks"]), 1)
            self.assertEqual(pulse["model_calls"], 0)
            self.assertEqual(len(receipt["artifacts"]), 3)
            self.assertIsNone(self.app.briefing.run_due(self.now+timedelta(hours=1)))
            second = self.app.briefing.run("same-day-manual", self.now+timedelta(hours=2))
            self.assertEqual(self.report(second)["venture_checkin"]["status"], "completed")
            self.assertTrue(self.report(second)["venture_checkin"]["replayed"])
        state = self.app.venture.status(self.now)
        self.assertEqual(len(state["progress"]), 1)
        self.assertFalse(state["next_actions"][0]["eligible"])
        self.assertIn("owner-reported evidence", state["next_actions"][0]["blockers"][0])

    def test_child_integrity_hold_does_not_retry_or_suppress_weather_brief(self):
        self.enable()
        (self.settings.workspace/"plan.json").write_text("changed", encoding="utf-8")
        receipt = self.app.briefing.run_due(self.now)
        self.assertEqual(receipt["status"], "completed")
        report = self.report(receipt)
        self.assertEqual(report["venture_checkin"]["status"], "held")
        self.assertTrue(any("Venture" in warning for warning in report["warnings"]))
        self.assertTrue(self.app.venture.status()["hold"])
        self.assertEqual(self.app.briefing.run(receipt["request_id"], self.now, scheduled_day=self.now.date().isoformat())["replayed"], True)
        self.assertEqual(self.app.venture.status()["progress"], [])

    def test_paused_or_disabled_briefing_blocks_manual_advancement(self):
        self.enable()
        cfg = self.app.briefing.config()
        self.app.briefing.configure({"paused": True}, expected_revision=cfg["revision"])
        self.assertEqual(self.request("/api/venture/run", "POST", {"request_id": "held-pulse"})["status"], 409)
        receipt = self.app.briefing.run("held-parent", self.now)
        self.assertEqual(self.report(receipt)["venture_checkin"]["status"], "held")
        self.assertEqual(self.app.venture.status()["progress"], [])
        self.store.set_setting("paused", True)
        self.assertEqual(self.request("/api/venture/run", "POST")["status"], 409)

    def test_owner_evidence_is_strict_and_explicitly_unverified(self):
        self.enable()
        self.app.venture.run_for_brief("fixture-draft", local_day=self.now.date().isoformat(), now=self.now)
        revision = self.app.venture.status()["revision"]
        response = self.request("/api/venture/evidence", "POST", {"expected_revision": revision, "evidence": {
            "task_id": "offer", "kind": "review", "observed_at": self.now.isoformat(), "note": "Owner reviewed the pilot scope; no customer contacted."}})
        self.assertEqual(response["status"], 200, response)
        evidence = response["data"]["evidence"][0]
        self.assertTrue(evidence["owner_reported"])
        self.assertFalse(evidence["independently_verified"])
        self.assertTrue(response["data"]["next_actions"][0]["eligible"])
        self.assertEqual(self.request("/api/venture/evidence", "POST", {"expected_revision": revision, "evidence": {}})["status"], 400)

    def test_display_history_limit_does_not_change_evidence_eligibility(self):
        self.board["tasks"].append({"id": "other", "project_id": "admin", "kind": "business_offer_pack", "title": "Another draft"})
        self.enable()
        self.app.venture.run_for_brief("two-drafts", local_day=self.now.date().isoformat(), now=self.now)
        revision = self.app.venture.status()["revision"]
        self.app.venture.record_evidence({"task_id": "offer", "kind": "review", "observed_at": self.now.isoformat(), "note": "Actual owner review"}, revision)
        # Valid history for another drafted task hides the older display row.
        with self.store.connect(require_existing=True) as db:
            for index in range(101):
                db.execute("INSERT INTO venture_evidence VALUES(?,?,?,?,?,?,?)", ("display-"+str(index), "other", "review", self.now.isoformat(), "Later owner note", None, 2_000_000_000+index))
        status = self.app.venture.status()
        self.assertEqual(len(status["evidence"]), 100)
        self.assertTrue(next(row for row in status["next_actions"] if row["task_id"] == "interviews")["eligible"])


if __name__ == "__main__":
    unittest.main()
