"""Authority and provenance integration across the new optional capabilities."""
import base64
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.mcp_admin import MCPAdmin
from ariadne.mcp_tools import write_json
from ariadne.sources import SourceChanged, SourceManager
from ariadne.store import Store
from test_core import FakeModel, tool
from test_mcp_tools import LocalServer, TOOL


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="integration-03-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(data_dir=self.root / "private", api_token="fixture-only-token")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.engine = Engine(self.settings, self.store, FakeModel())
        self.admin = MCPAdmin(self.engine.tools.mcp)

    def register(self, server, read_only=False):
        self.admin.add({"id": "fixture", "url": server.url, "mode": server.state["mode"]})
        snapshot = self.admin.discover("fixture")
        self.assertEqual(self.engine.tools.mcp.schemas(), [])
        self.admin.select("fixture", {"snapshot_sha256": snapshot["snapshot_sha256"],
            "selections": [{"name": TOOL["name"], "read_only": read_only}]})
        return self.engine.tools.mcp.schemas()[0]["function"]["name"]

    def process(self, run):
        self.store.next_run()
        self.engine.process(run["id"])
        return self.store.details(run["id"])

    def test_real_mcp_write_waits_for_owner_and_calls_once(self):
        with LocalServer() as server:
            name = self.register(server)
            self.engine.model.responses = [tool(name, {"sku": "WIDGET"})]
            run = self.store.new_run("Synthetic local MCP action")
            detail = self.process(run)
            self.assertEqual(detail["run"]["status"], "waiting_approval")
            self.assertEqual(server.state["calls"], [])
            self.store.decide(detail["approvals"][0]["id"], "approved")
            final = self.process(run)
            self.assertEqual(final["run"]["status"], "completed")
            self.assertEqual(len(server.state["calls"]), 1)
            self.assertEqual(final["approvals"][0]["status"], "executed")

    def test_real_uncertain_write_stops_model_and_schedule_without_retry(self):
        with LocalServer() as server:
            name = self.register(server)
            server.state["behavior"] = "tool_error"
            self.engine.model.responses = [tool(name, {"sku": "WIDGET"})]
            schedule = self.store.schedule_add("Fixture", "Synthetic local action", 5)
            with self.store.connect() as db:
                db.execute("UPDATE schedules SET next_run=0 WHERE id=?", (schedule["id"],))
            self.store.due_schedules()
            run = self.store.runs()[0]
            detail = self.process(run)
            self.store.decide(detail["approvals"][0]["id"], "approved")
            detail = self.process(run)
            self.assertEqual(detail["run"]["status"], "failed")
            self.assertEqual(detail["approvals"][0]["status"], "uncertain")
            self.assertEqual(len(server.state["calls"]), 1)
            self.assertEqual(len(self.engine.model.calls), 1)
            self.store.due_schedules()
            self.assertFalse(self.store.schedules()[0]["enabled"])

    def test_approved_registration_change_never_calls_old_tool(self):
        with LocalServer() as server:
            name = self.register(server)
            self.engine.model.responses = [tool(name, {"sku": "WIDGET"})]
            run = self.store.new_run("Synthetic action")
            detail = self.process(run)
            self.store.decide(detail["approvals"][0]["id"], "approved")
            config = self.engine.tools.mcp.config()
            config["servers"][0]["tools"][0]["read_only"] = True
            write_json(self.engine.tools.mcp.path, config)
            self.process(run)
            self.assertEqual(server.state["calls"], [])
            self.assertEqual(self.store.details(run["id"])["approvals"][0]["status"], "unavailable")

    def test_discovery_fingerprint_and_selection_are_owner_bound(self):
        with LocalServer() as server:
            self.admin.add({"id": "fixture", "url": server.url, "mode": "modern"})
            snapshot = self.admin.discover("fixture")
            self.assertTrue(snapshot["tools"][0]["supported"])
            with self.assertRaises(ValueError):
                self.admin.select("fixture", {"snapshot_sha256": "0" * 64, "selections": [{"name": TOOL["name"], "read_only": True}]})
            with self.assertRaises(ValueError):
                self.admin.select("fixture", {"snapshot_sha256": snapshot["snapshot_sha256"], "selections": [{"name": "unreviewed", "read_only": True}]})
            self.assertEqual(self.engine.tools.mcp.schemas(), [])

    def test_ocr_preview_identity_and_processor_version_are_preserved(self):
        manager = SourceManager(self.settings, self.store)
        reference = self.root / "reference"
        reference.mkdir()
        (reference / "scan.png").write_bytes(b"fixture-image")
        source = manager.register("Fixture", str(reference))
        result = {"text": "Stock 16.00", "format": "ocr-png", "warnings": ["Review OCR"],
            "sha256": __import__("hashlib").sha256(b"fixture-image").hexdigest(), "bytes": 13,
            "processor": {"name": "rapidocr", "identity": "first"}}
        with patch("ariadne.documents.extract_document", return_value=result):
            preview = manager.preview(source["id"], "scan.png")
            with self.assertRaises(SourceChanged):
                manager.import_reference(source["id"], "scan.png", preview["sha256"])
            imported = manager.import_reference(source["id"], "scan.png", preview["sha256"], review_identity=preview["review_identity"])
            self.assertEqual(imported["document"]["processor"], result["processor"])
            again = manager.import_reference(source["id"], "scan.png", preview["sha256"], review_identity=preview["review_identity"])
            self.assertTrue(again["unchanged"])
        changed = copy.deepcopy(result)
        changed["processor"]["identity"] = "second"
        with patch("ariadne.documents.extract_document", return_value=changed):
            with self.assertRaises(SourceChanged):
                manager.import_reference(source["id"], "scan.png", preview["sha256"], review_identity=preview["review_identity"])
            fresh = manager.preview(source["id"], "scan.png")
            refreshed = manager.import_reference(source["id"], "scan.png", fresh["sha256"], review_identity=fresh["review_identity"])
            self.assertEqual(refreshed["document"]["version"], 2)
        self.assertEqual(self.store.search("Stock", knowledge=True)[0]["processor"], changed["processor"])

    def test_worker_environment_does_not_inherit_owner_secrets(self):
        from ariadne.worker import _environment, run_worker, WorkerError
        with patch.dict("os.environ", {"ARIADNE_MCP_TEST_SECRET": "fixture-secret", "HTTPS_PROXY": "fixture", "PYTHONPATH": "fixture"}):
            env = _environment(None)
        self.assertNotIn("ARIADNE_MCP_TEST_SECRET", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertTrue(Path(env["TEMP"]).is_relative_to(Path.cwd()))
        with self.assertRaises(WorkerError):
            run_worker(Path(__file__), b"", timeout=1, memory_bytes=100_000_000, cpu_seconds=1, max_output_bytes=10)

    def test_empty_optional_projection_allows_real_aggregate(self):
        (self.settings.workspace / "values.csv").write_text("quantity,price\n2,3.5\n3,3\n")
        result = self.engine.tools.execute("analytics_query", {"tables": [{"name": "s", "path": "values.csv"}],
            "numeric_columns": ["s.quantity", "s.price"], "select": [],
            "aggregations": [{"op": "sum_product", "columns": ["s.quantity", "s.price"], "as": "total"}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rows"][0]["total"], "16")
