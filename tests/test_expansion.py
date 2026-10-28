import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ariadne.automation_tools import local_url, within_app
from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.worker import WorkerError, run_worker


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".expansion-", dir=Path.cwd())
        self.settings = Settings(data_dir=Path(self.temporary.name), api_token="expansion-test-token")
        self.registry = ToolRegistry(self.settings)

    def tearDown(self):
        self.temporary.cleanup()

    def record(self, action, record=None, key=None):
        args = {"action": action}
        if record is not None:
            args.update(record=record, request_id=key)
        return self.registry.execute("business_records", args)

    def test_inventory_replay_and_negative_stock_rollback(self):
        self.assertTrue(self.record("upsert_item", {"id": "bolt", "name": "Bolt", "unit": "each", "unit_price_cents": 125}, "new-item")["ok"])
        movement = {"item_id": "bolt", "quantity": 10, "reference": "opening count"}
        self.assertTrue(self.record("stock_move", movement, "opening")["ok"])
        self.assertTrue(self.record("stock_move", movement, "opening")["replayed"])
        self.assertFalse(self.record("stock_move", {**movement, "quantity": 11}, "opening")["ok"])
        self.assertFalse(self.record("stock_move", {**movement, "quantity": -11}, "negative")["ok"])
        self.assertEqual(self.record("inventory")["records"][0]["quantity"], 10)
        self.assertFalse(self.record("upsert_item", {"id": "bolt", "name": "Bolt", "unit": "box", "unit_price_cents": 125}, "unit-change")["ok"])

    def test_balanced_journal_and_exact_integer_money(self):
        lines = [{"account": "cash", "debit_cents": 12345, "credit_cents": 0}, {"account": "equity", "debit_cents": 0, "credit_cents": 12345}]
        self.assertTrue(self.record("post_journal", {"id": "opening", "memo": "Owner contribution", "lines": lines}, "journal-1")["ok"])
        balances = self.record("trial_balance")["records"]
        self.assertEqual(sum(row["balance_cents"] for row in balances), 0)
        self.assertEqual(balances[0]["debit_cents"], 12345)
        unbalanced = [lines[0], {**lines[1], "credit_cents": 12344}]
        self.assertFalse(self.record("post_journal", {"id": "bad", "memo": "Bad journal", "lines": unbalanced}, "journal-2")["ok"])
        self.assertEqual(len(self.record("journal")["records"]), 2)
        self.assertFalse(self.record("upsert_item", {"id": "x", "name": "x", "unit": "each", "unit_price_cents": True}, "badbool")["ok"])

    def test_business_state_change_invalidates_approval(self):
        args = {"action": "upsert_contact", "record": {"id": "customer", "name": "Customer"}, "request_id": "contact-1"}
        fingerprint = self.registry.fingerprint("business_records", args)
        self.record("upsert_item", {"id": "widget", "name": "Widget", "unit": "each", "unit_price_cents": 10}, "item-1")
        result = self.registry.execute("business_records", args, approval_fingerprint=fingerprint)
        self.assertFalse(result["ok"])
        self.assertEqual(self.record("list_contacts")["records"], [])

    def test_approval_and_read_policy(self):
        self.assertEqual(self.registry.policy("business_records", {"action": "inventory"}), "auto")
        self.assertEqual(self.registry.policy("business_records", {"action": "stock_move"}), "approval")
        self.assertEqual(self.registry.policy("browser_task", {}), "approval")

    def test_no_arbitrary_urls_and_reserved_endpoints(self):
        for url in ["https://example.com/", "http://localhost:9000/", "http://127.0.0.1:8080/", "http://127.0.0.1:11434/", "http://127.0.0.1:9000/../api", "http://user:secret@127.0.0.1:9000/", "file:///E:/model", "http://127.0.0.1:9000/%2e%2e/", "http://127.0.0.1:9000/?token=x"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                local_url(url)
        self.assertEqual(local_url("http://127.0.0.1:8090/app/"), "http://127.0.0.1:8090/app/")
        self.assertTrue(within_app("http://127.0.0.1:8090/app/resource?q=1", "http://127.0.0.1:8090/app"))
        self.assertFalse(within_app("http://127.0.0.1:8090/application", "http://127.0.0.1:8090/app"))
        self.assertFalse(within_app("http://127.0.0.1:8091/app/", "http://127.0.0.1:8090/app"))

    def test_failed_configuration_preserves_valid_state(self):
        valid = {"apps": [{"id": "test", "url": "http://127.0.0.1:8090/", "description": "Reviewed app"}], "flows": [], "erpnext": None}
        self.registry.automation.configure(valid)
        original = self.registry.automation.config_path.read_bytes()
        with self.assertRaises(ValueError):
            self.registry.automation.configure({"apps": [{"id": "test", "url": "http://127.0.0.1:8080/"}]})
        self.assertEqual(self.registry.automation.config_path.read_bytes(), original)
        with self.assertRaises(ValueError):
            self.registry.automation.configure({"flows": [{"id": "bad", "url": "http://127.0.0.1:9000/", "token_file": "E:/token"}]})

    def test_discovery_routes_schemas_without_granting_authority(self):
        general = self.registry.schemas_for("Hello", [])
        names = {s["function"]["name"] for s in general}
        self.assertIn("capability_find", names)
        self.assertNotIn("business_records", names)
        discovered = self.registry.execute("capability_find", {"query": "business inventory"})
        self.assertTrue(any(t["name"] == "business_records" for t in discovered["tools"]))
        routed = self.registry.schemas_for("Hello", [{"role": "tool", "content": json.dumps(discovered)}])
        self.assertIn("business_records", {s["function"]["name"] for s in routed})
        self.assertEqual(self.registry.policy("business_records", {"action": "stock_move"}), "approval")
        self.assertLess(len(json.dumps(general)), len(json.dumps(self.registry.schemas())))

    def test_worker_child_limit_cannot_relax_existing_workers(self):
        with self.assertRaises(WorkerError):
            run_worker(Path(__file__).parents[1] / "ariadne/_analytics_worker.py", b"{}", timeout=5, memory_bytes=512 * 1024**2, cpu_seconds=5, max_output_bytes=1000, process_limit=2)

    def test_owner_app_api_keeps_existing_flow_credentials(self):
        store = Store(self.settings.data_dir / "api.sqlite3")
        class FakeModel:
            def status(self):
                return {"available": False, "models": []}
        app = Application(self.settings, store=store, engine=Engine(self.settings, store, FakeModel()))
        flows = [{"id": "health", "url": "http://127.0.0.1:1880/ariadne/health", "method": "GET", "token_file": "flows.token"}]
        app.engine.tools.automation.configure({"apps": [], "flows": flows, "erpnext": None})
        payload = json.dumps({"id": "test", "url": "http://127.0.0.1:8090/", "description": "Test"}).encode()
        env = {"wsgi.input": io.BytesIO(payload), "CONTENT_LENGTH": str(len(payload)), "CONTENT_TYPE": "application/json"}
        status, _, _ = app.api("/api/automation/apps", "POST", env)
        self.assertEqual(status, 200)
        self.assertEqual(app.engine.tools.automation.config()["flows"], flows)

    def test_binary_artifact_download_is_authenticated_and_confined(self):
        store = Store(self.settings.data_dir / "download.sqlite3")
        class FakeModel:
            def status(self):
                return {"available": False, "models": []}
        app = Application(self.settings, store=store, engine=Engine(self.settings, store, FakeModel()))
        data = b"\x89PNG\r\n\x1a\n\x00\xff"
        (self.settings.workspace / "output.png").write_bytes(data)
        for authenticated, expected in [(False, 401), (True, 200)]:
            statuses, headers = [], []
            env = {"HTTP_HOST": "127.0.0.1:8080", "PATH_INFO": "/api/artifacts", "REQUEST_METHOD": "GET", "QUERY_STRING": "path=output.png"}
            if authenticated:
                env["HTTP_AUTHORIZATION"] = "Bearer " + self.settings.api_token
            def start(status, values):
                statuses.append(status); headers.extend(values)
            result = b"".join(app(env, start))
            self.assertTrue(statuses[0].startswith(str(expected)))
            if authenticated:
                self.assertEqual(result, data)
                self.assertIn("attachment", dict(headers)["Content-Disposition"])
                self.assertEqual(dict(headers)["Content-Type"], "application/octet-stream")
        with self.assertRaises(ValueError):
            app.api("/api/artifacts", "GET", {"QUERY_STRING": "path=E%3A%2Fprivate"})

    @unittest.skipUnless(os.environ.get("ARIADNE_QUALIFY_NATIVE") == "1", "Explicit local-browser qualification")
    def test_real_browser_actions_and_external_request_filtering(self):
        escaped = []
        class Outside(BaseHTTPRequestHandler):
            def do_GET(self):
                escaped.append(self.path)
                self.send_response(200); self.end_headers(); self.wfile.write(b"escaped")
            def log_message(self, *args):
                pass
        outside = ThreadingHTTPServer(("127.0.0.1", 0), Outside)
        outside_thread = threading.Thread(target=outside.serve_forever, daemon=True)
        outside_thread.start()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{outside.server_port}/must-not-reach")
                    self.end_headers()
                    return
                body = b'''<!doctype html><title>Local test</title><label>Quantity<input id="qty" value="1"></label><button id="calc" onclick="document.getElementById('answer').textContent=Number(document.getElementById('qty').value)*7">Calculate</button><p id="answer">0</p><img src="https://example.com/forbidden.png">'''
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.registry.automation.configure({"apps": [{"id": "fixture", "url": f"http://127.0.0.1:{server.server_port}/", "description": "Test fixture"}]})
            result = self.registry.execute("browser_task", {"app": "fixture", "actions": [{"op": "fill", "selector": "#qty", "value": "6"}, {"op": "click", "selector": "#calc"}]})
            self.assertTrue(result.get("ok"), result)
            self.assertIn("42", result["text"])
            self.assertGreaterEqual(result["blocked_requests"], 1)
            redirected = self.registry.execute("browser_task", {"app": "fixture", "path": "/redirect", "actions": []})
            self.assertFalse(redirected["ok"], redirected)
            self.assertTrue(redirected["uncertain"], redirected)
            self.assertEqual(escaped, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            outside.shutdown(); outside.server_close(); outside_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
