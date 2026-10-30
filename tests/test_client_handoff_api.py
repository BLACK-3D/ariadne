"""Real-client WSGI lifecycle and financial/auth boundary regression tests."""
import copy
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from ariadne.client_handoff import _local_day
from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class NoModel:
    def status(self):
        return {"available": False, "models": []}

    def chat(self, *args, **kwargs):
        raise AssertionError("Client paperwork must not use model inference.")


class ClientHandoffAPITests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="client-api-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name) / "data", api_token="client-workflow-test-key")
        self.store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.engine = Engine(self.settings, self.store, NoModel())
        # Construction may otherwise inventory installed Windows SAPI voices.
        with patch("ariadne.server.Voice", return_value=object()):
            self.app = Application(self.settings, self.store, self.engine)
        self.counter = 0
        self.today = _local_day().isoformat()

    def request(self, path="/api/service-desk", method="GET", body=None, *, raw=None,
                auth=True, origin=None, host="localhost:8080", query="", content_type="application/json"):
        raw = json.dumps({} if body is None else body).encode() if raw is None else raw
        env = {"HTTP_HOST": host, "REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_TYPE": content_type, "CONTENT_LENGTH": str(len(raw)),
               "wsgi.input": io.BytesIO(raw), "QUERY_STRING": query}
        if auth:
            env["HTTP_AUTHORIZATION"] = "Bearer client-workflow-test-key"
        if origin:
            env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            response.update(status=int(status.split()[0]), headers=dict(headers))
        payload = b"".join(self.app(env, start))
        response["body"] = json.loads(payload) if response["headers"]["Content-Type"].startswith("application/json") else payload
        return response

    def state(self):
        result = self.request()
        self.assertEqual(result["status"], 200, result)
        return result["body"]

    def mutate(self, path, fields, method="POST", expected=200):
        self.counter += 1
        body = {"request_id": "api-" + str(self.counter), "expected_revision": self.state()["revision"], **fields}
        result = self.request("/api/service-desk/" + path, method, body)
        self.assertEqual(result["status"], expected, result)
        return result["body"], body

    def prepare_quote(self):
        self.mutate("clients", {"client": {"id": "service-1", "name": "QA Service LLC", "address": "10 Example Street"}})
        intake = {"id": "job-1", "client_id": "service-1", "title": "QA maintenance",
                  "customer": {"name": "QA Customer", "address": "20 Example Street", "email": None},
                  "source_notes": "Fictional QA only.\nOwner supplied scope.",
                  "service_date": self.today, "document_date": self.today,
                  "due_date": (_local_day() + timedelta(days=14)).isoformat(),
                  "lines": [{"description": "Billable work", "quantity": "2.5", "unit": "hours",
                             "unit_price": "35.00", "price_approved": True, "source_ref": "QA rate sheet"}],
                  "tax": {"bps": 0, "approved": True, "source_ref": "QA explicit tax instruction"},
                  "permission": {"confirmed": True, "source_ref": "QA permission reference"}}
        self.mutate("intakes", {"intake": intake})
        saved = self.request("/api/service-desk/intakes/job-1")["body"]["intake"]
        result, _ = self.mutate("quotes", {"intake_id": "job-1", "content_sha256": saved["content_sha256"]})
        return self.request("/api/service-desk/documents/" + result["entity_id"])["body"]["document"]

    @staticmethod
    def identity(document):
        return {"document_id": document["id"], "content_sha256": document["content_sha256"]}

    def test_every_route_is_owner_only_same_origin_and_host(self):
        paths = [("", "GET"), ("/status", "GET"), ("/clients/id", "GET"),
                 ("/intakes/id", "GET"), ("/documents/id", "GET"), ("/clients", "POST"),
                 ("/clients", "PUT"), ("/intakes", "POST"), ("/quotes", "POST"),
                 ("/documents", "PUT"), ("/review", "POST"), ("/acceptance", "POST"),
                 ("/invoices", "POST"), ("/void", "POST"), ("/export", "POST"),
                 ("/export/acknowledge", "POST")]
        for path, method in paths:
            with self.subTest(path=path, method=method):
                endpoint = "/api/service-desk" + path
                self.assertEqual(self.request(endpoint, method, auth=False)["status"], 401)
                self.assertEqual(self.request(endpoint, method, origin="https://outsider.example")["status"], 403)
                self.assertEqual(self.request(endpoint, method, host="outsider.example")["status"], 403)
        self.assertEqual(self.state()["revision"], 0)

    def test_passive_reads_never_seed_clients_and_json_is_strict_and_bounded(self):
        with patch("socket.create_connection", side_effect=AssertionError("No network")), patch("subprocess.Popen", side_effect=AssertionError("No worker")):
            first = self.state()
            self.assertEqual(first["clients"], [])
            self.assertEqual(first["documents"], [])
            self.assertFalse(first["demo_only"])
            self.assertEqual(self.state()["revision"], 0)
            bad = [b'{"request_id":"a","request_id":"b","expected_revision":0}',
                   b'{"request_id":"a","expected_revision":0,"client":{"id":"x","name":NaN}}',
                   b'{"request_id":"a","expected_revision":0,"client":{"id":"x","name":1.2}}',
                   b'[]', b'null', b'\xff', b'{"x":' + b'9'*100 + b'}', b' '*131073]
            for raw in bad:
                with self.subTest(raw=raw[:80]):
                    self.assertIn(self.request("/api/service-desk/clients", "POST", raw=raw)["status"], (400, 413))
            self.assertEqual(self.request("/api/service-desk/clients", "POST", {}, content_type="text/plain")["status"], 415)
        self.assertEqual(self.state()["revision"], 0)

    def test_pause_and_query_contract_fail_closed_without_mutation(self):
        self.store.set_setting("paused", True)
        for path, method in (("clients", "POST"), ("clients", "PUT"), ("intakes", "POST"),
                             ("quotes", "POST"), ("documents", "PUT"), ("review", "POST"),
                             ("acceptance", "POST"), ("invoices", "POST"), ("void", "POST"),
                             ("export", "POST"), ("export/acknowledge", "POST")):
            self.assertEqual(self.request("/api/service-desk/" + path, method)["status"], 409)
        for query in ("limit=", "limit=2&limit=3", "limit=-1", "limit=true", "limit=100000000", "unknown=1", "client_id="):
            self.assertEqual(self.request(query=query)["status"], 400, query)
        self.assertEqual(self.state()["revision"], 0)
        self.assertTrue(self.store.setting("paused"))

    def test_complete_lifecycle_export_replay_and_void_preserve_demo_and_ledger(self):
        demo_before = copy.deepcopy(self.app.handoff.status())
        with self.engine.tools.business.database() as db:
            ledger_before = {table: db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("contacts", "items", "journals", "journal_lines", "requests")}
        with patch("socket.create_connection", side_effect=AssertionError("No network")), patch("subprocess.Popen", side_effect=AssertionError("No worker")):
            quote = self.prepare_quote()
            self.assertEqual(quote["total_cents"], "8750")
            self.mutate("invoices", {"quote_id": quote["id"], "content_sha256": quote["content_sha256"],
                        "service_date": self.today, "document_date": self.today, "due_date": self.today,
                        "work": {"completed": True, "approved": True, "source_ref": "QA completion"}}, expected=409)
            self.mutate("review", {**self.identity(quote), "decision": "approve"})
            self.mutate("acceptance", {**self.identity(quote), "accepted": True,
                        "observed_at": (datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),
                        "contact": "QA Customer", "source_ref": "QA written acceptance"})
            result, _ = self.mutate("invoices", {"quote_id": quote["id"], "content_sha256": quote["content_sha256"],
                          "service_date": self.today, "document_date": self.today, "due_date": self.today,
                          "work": {"completed": True, "approved": True, "source_ref": "QA completed work"}})
            invoice = self.request("/api/service-desk/documents/" + result["entity_id"])["body"]["document"]
            self.assertEqual(invoice["total_cents"], "8750")
            self.mutate("export", self.identity(invoice), expected=409)
            self.mutate("review", {**self.identity(invoice), "decision": "approve"})
            receipt, request = self.mutate("export", self.identity(invoice))
            self.assertEqual(receipt["status"], "completed")
            for artifact in receipt["artifacts"]:
                download = self.request("/api/artifacts", query=urlencode({"path": artifact["path"]}))
                self.assertEqual(download["status"], 200)
                self.assertEqual(hashlib.sha256(download["body"]).hexdigest(), artifact["sha256"])
                self.assertIn("attachment", download["headers"]["Content-Disposition"])
            replay = self.request("/api/service-desk/export", "POST", request)
            self.assertEqual(replay["status"], 200)
            self.assertTrue(replay["body"]["replayed"])
            self.assertEqual(replay["body"]["artifacts"], receipt["artifacts"])
            self.mutate("void", {**self.identity(invoice), "reason": "QA incorrect completion date; unsent local draft"})
            voided = self.request("/api/service-desk/documents/" + invoice["id"])["body"]["document"]
            self.assertEqual(voided["status"], "voided")
            self.mutate("export", self.identity(voided), expected=409)
            self.assertGreaterEqual(len(voided["history"]), 2)
        self.assertEqual(self.app.handoff.status(), demo_before)
        with self.engine.tools.business.database() as db:
            self.assertEqual({table: db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ledger_before}, ledger_before)
        self.assertEqual(self.store.runs(), [])
        self.assertFalse(self.store.setting("wake_preferences", {}).get("enabled", False))

    def test_static_assets_are_local_and_real_demo_mounts_are_separate(self):
        for name, mime in (("client_handoff.js", "text/javascript"), ("client_handoff.css", "text/css")):
            response = self.request("/static/" + name, auth=False)
            self.assertEqual(response["status"], 200)
            self.assertTrue(response["headers"]["Content-Type"].startswith(mime))
            self.assertEqual(response["body"], (self.app.static / name).read_bytes())
            self.assertIn("script-src 'self'", response["headers"]["Content-Security-Policy"])
        page = self.request("/", auth=False)["body"]
        for marker in (b'id="client-handoff-content"', b'id="handoff-content"', b'id="handoff-real-mode"', b'id="handoff-demo-mode"'):
            self.assertIn(marker, page)


if __name__ == "__main__":
    unittest.main()
