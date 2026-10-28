"""Fixed report outputs, measured zero-model execution and fail-stop receipts."""
import csv
import datetime as dt
import hashlib
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.procedures import ProcedureConflict, Procedures
from ariadne.server import Application
from ariadne.store import Store
from ariadne.tools import ToolRegistry


class NoModelCalls:
    def status(self):
        return {"available": False, "models": []}

    def chat(self, *args, **kwargs):
        raise AssertionError("Fixed procedures must not invoke a model")


class ProcedureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="procedure-test-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root / "private-data", api_token="procedure-test-key")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.procedures = Procedures(self.registry, self.store)
        self.counter = 0

    def write_survival(self, action, record):
        self.counter += 1
        return self.registry.survival.execute("survival_records", {"action": action, "record": record, "request_id": f"setup-{self.counter}"})

    def artifact(self, receipt, name):
        artifact = next(row for row in receipt["artifacts"] if row["path"].endswith("/" + name))
        return self.registry._path(artifact["path"]).read_bytes()

    def seed(self):
        self.write_survival("save_profile", {"people": 2, "days": 7, "battery_wh": "1000", "loads": [{"name": "Radio", "watts": "25", "hours_per_day": "4"}]})
        self.write_survival("upsert_supply", {"id": "water", "name": "=UNTRUSTED_FORMULA()", "category": "water", "quantity": "10.5", "unit": "liter", "expires": (dt.date.today()-dt.timedelta(days=1)).isoformat()})
        self.write_survival("upsert_supply", {"id": "food", "name": "Food labels", "category": "food", "quantity": "20000", "unit": "kcal"})
        self.write_survival("seed_roadmap", {})
        self.registry.business.execute("business_records", {"action": "upsert_item", "record": {"id": "bolt", "name": "Bolt", "unit": "each", "unit_price_cents": 25}, "request_id": "business-item"})
        self.registry.business.execute("business_records", {"action": "stock_move", "record": {"item_id": "bolt", "quantity": 12, "reference": "verified fixture count"}, "request_id": "business-stock"})
        self.registry.business.execute("business_records", {"action": "post_journal", "record": {"id": "opening", "memo": "Owner opening", "lines": [{"account": "cash", "debit_cents": 300, "credit_cents": 0}, {"account": "equity", "debit_cents": 0, "credit_cents": 300}]}, "request_id": "business-journal"})
        self.store.document_add("@REFERENCE_TITLE", "BODY_CONTENT_EXCLUDED_FROM_METADATA_EXPORT_93721")

    def test_all_eight_actual_procedures_publish_fixed_hashed_outputs_without_services(self):
        self.seed()
        definitions = self.procedures.catalog()["procedures"]
        self.assertEqual(len(definitions), 8)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("No service request allowed")), mock.patch("subprocess.Popen", side_effect=AssertionError("No model or child process allowed")), mock.patch("socket.create_connection", side_effect=AssertionError("No network probe allowed")):
            for definition in definitions:
                with self.subTest(procedure=definition["id"]):
                    receipt = self.procedures.run(definition["id"], "report-" + definition["id"])
                    self.assertTrue(receipt["ok"])
                    self.assertEqual(receipt["status"], "completed")
                    self.assertEqual(receipt["model_calls"], 0)
                    self.assertGreaterEqual(receipt["wall_seconds"], 0)
                    self.assertEqual({Path(row["path"]).name for row in receipt["artifacts"]}, set(definition["outputs"]))
                    self.assertRegex(receipt["input_snapshot_sha256"], r"^[a-f0-9]{64}$")
                    for artifact in receipt["artifacts"]:
                        self.assertTrue(artifact["path"].startswith("procedures/report-" + definition["id"] + "/"))
                        data = self.registry._path(artifact["path"]).read_bytes()
                        self.assertEqual(len(data), artifact["bytes"])
                        self.assertEqual(hashlib.sha256(data).hexdigest(), artifact["sha256"])
        history = self.procedures.history()["runs"]
        self.assertEqual(len(history), 8)
        self.assertTrue(all(row["status"] == "completed" and row["model_calls"] == 0 for row in history))

    def test_reports_use_current_typed_state_and_preserve_business_units_and_money(self):
        self.seed()
        readiness = self.procedures.run("readiness_report", "readiness-values")
        report = json.loads(self.artifact(readiness, "readiness.json"))
        self.assertEqual(report["summary"]["water"]["available_liters"], "0")
        self.assertEqual(report["summary"]["water"]["required_liters"], "52.995764976")
        self.assertEqual(report["summary"]["food"]["available_kcal"], "20000")
        power = self.procedures.run("power_budget", "energy-values")
        energy = json.loads(self.artifact(power, "power-budget.json"))
        self.assertEqual(energy["power"]["daily_wh"], "100")
        self.assertEqual(energy["power"]["battery_usable_wh"], "720")
        self.assertEqual(energy["power"]["battery_runtime_days"], "7.2")
        business = self.procedures.run("business_snapshot", "business-values")
        inventory = list(csv.DictReader(io.StringIO(self.artifact(business, "inventory.csv").decode())))
        self.assertEqual((inventory[0]["quantity"], inventory[0]["unit"], inventory[0]["unit_price_cents"]), ("12", "each", "25"))
        balances = list(csv.DictReader(io.StringIO(self.artifact(business, "trial-balance.csv").decode())))
        self.assertEqual(sum(int(row["balance_cents"]) for row in balances), 0)
        self.assertEqual(self.registry.business.execute("business_records", {"action": "inventory"})["records"][0]["quantity"], 12)

    def test_rotation_dag_and_metadata_outputs_are_meaningful_and_escape_csv_formulas(self):
        self.seed()
        rotation = self.procedures.run("stock_rotation", "rotation-values")
        rows = list(csv.DictReader(io.StringIO(self.artifact(rotation, "stock-rotation.csv").decode())))
        self.assertEqual([row["id"] for row in rows], ["water", "food"])
        self.assertEqual(rows[0]["expiry_status"], "expired")
        self.assertTrue(rows[0]["name"].startswith("'="))
        self.assertEqual(rows[1]["expiry_status"], "unknown")
        rebuilding = self.procedures.run("rebuilding_plan", "roadmap-values")
        graph = self.artifact(rebuilding, "rebuilding-plan.md").decode()
        data = json.loads(self.artifact(rebuilding, "rebuilding-plan.json"))
        self.assertEqual(len(data["tasks"]), 16)
        self.assertIn("flowchart TD", graph)
        self.assertIn(" --> ", graph)
        self.assertIn("roadmap-digital", graph)
        knowledge = self.procedures.run("knowledge_inventory", "knowledge-values")
        metadata = self.artifact(knowledge, "knowledge-inventory.json").decode()
        self.assertNotIn("BODY_CONTENT_EXCLUDED", metadata)
        self.assertIn("content_sha256", metadata)
        self.assertIn("'@REFERENCE_TITLE", self.artifact(knowledge, "knowledge-inventory.csv").decode())

    def test_completed_receipt_replay_uses_same_snapshot_even_after_state_changes(self):
        first = self.procedures.run("readiness_report", "stable-request")
        original = self.artifact(first, "readiness.json")
        self.write_survival("save_profile", {"people": 3})
        reopened = Procedures(self.registry, self.store)
        replay = reopened.run("readiness_report", "stable-request")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["artifacts"], first["artifacts"])
        self.assertEqual(self.artifact(replay, "readiness.json"), original)
        self.assertEqual(len(reopened.history()["runs"]), 1)
        with self.assertRaises(ProcedureConflict):
            reopened.run("power_budget", "stable-request")

    def test_modified_completed_artifact_is_detected_without_overwriting(self):
        receipt = self.procedures.run("power_budget", "changed-artifact")
        path = self.registry._path(receipt["artifacts"][0]["path"])
        path.write_bytes(b"owner modified file")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.procedures.run("power_budget", "changed-artifact")
        self.assertEqual(path.read_bytes(), b"owner modified file")

    def test_preexisting_output_directory_is_not_adopted_and_failure_is_fail_stop(self):
        directory = self.registry.root / "procedures" / "occupied"
        directory.mkdir(parents=True)
        existing = directory / "readiness.json"
        existing.write_bytes(b"preserve existing file")
        with self.assertRaises(ValueError):
            self.procedures.run("readiness_report", "occupied")
        self.assertEqual(existing.read_bytes(), b"preserve existing file")
        history = self.procedures.history()["runs"]
        self.assertEqual((history[0]["status"], history[0]["ok"]), ("failed", False))
        with self.assertRaises(ProcedureConflict):
            self.procedures.run("readiness_report", "occupied")

    def test_partial_publication_failure_preserves_known_artifacts_and_never_retries(self):
        original_publish = self.procedures._publish
        count = 0
        def interrupted(destination, payload, descriptor):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("Injected disk full")
            return original_publish(destination, payload, descriptor)
        with mock.patch.object(self.procedures, "_publish", interrupted):
            with self.assertRaisesRegex(ValueError, "disk full"):
                self.procedures.run("readiness_report", "partial-output")
        history = self.procedures.history()["runs"][0]
        self.assertEqual(history["status"], "failed")
        self.assertEqual(len(history["artifacts"]), 1)
        saved = self.registry._path(history["artifacts"][0]["path"]).read_bytes()
        with self.assertRaises(ProcedureConflict):
            Procedures(self.registry, self.store).run("readiness_report", "partial-output")
        self.assertEqual(self.registry._path(history["artifacts"][0]["path"]).read_bytes(), saved)

    def test_interrupted_running_marker_and_output_bounds_fail_closed(self):
        with mock.patch("ariadne.procedures.MAX_SNAPSHOT_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "bounded"):
                self.procedures.run("readiness_report", "oversized-snapshot")
        self.assertEqual(self.procedures.history()["runs"][0]["status"], "failed")
        first = self.procedures.run("power_budget", "crashed-run")
        with self.procedures.database() as db:
            marker = {**first, "ok": False, "status": "running", "completed_at": None}
            db.execute("UPDATE procedure_runs SET status='running',completed=NULL,receipt=? WHERE request_id=?", (json.dumps(marker), "crashed-run"))
        with self.assertRaises(ProcedureConflict) as error:
            Procedures(self.registry, self.store).run("power_budget", "crashed-run")
        self.assertEqual(error.exception.status, "running")

    def test_two_instances_same_request_create_one_completed_report(self):
        barrier = threading.Barrier(2)
        def run(index):
            procedures = Procedures(self.registry, self.store)
            barrier.wait(timeout=5)
            return procedures.run("power_budget", "parallel-request")
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(run, (0, 1)))
        self.assertEqual(sum(receipt["replayed"] for receipt in receipts), 1)
        self.assertEqual(receipts[0]["artifacts"], receipts[1]["artifacts"])
        self.assertEqual(len(self.procedures.history()["runs"]), 1)

    def test_arbitrary_names_paths_device_ids_and_invalid_input_types_are_rejected(self):
        for identifier in ("../../run", "file_write", "run_python", "https://example.invalid", {}, None):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.procedures.run(identifier, "valid-id")
        for request in ("../escape", "E:/never", "nested/path", "with.dot", "", ".", "CON", "a"*65, True):
            with self.subTest(request=request), self.assertRaises((ValueError, RuntimeError)):
                self.procedures.run("power_budget", request)
        self.assertEqual(self.procedures.history()["runs"], [])

    def request(self, path, method="GET", data=None, *, token="procedure-test-key", origin=None):
        if not hasattr(self, "app"):
            self.app = Application(self.settings, self.store, Engine(self.settings, self.store, NoModelCalls()))
        payload = json.dumps(data or {}).encode()
        env = {"HTTP_HOST": "localhost:8080", "REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)), "wsgi.input": io.BytesIO(payload)}
        if token is not None:
            env["HTTP_AUTHORIZATION"] = "Bearer " + token
        if origin:
            env["HTTP_ORIGIN"] = origin
        result = {}
        def start(status, headers):
            result["status"] = int(status.split()[0])
            result["headers"] = dict(headers)
        raw = b"".join(self.app(env, start))
        result["data"] = json.loads(raw)
        return result

    def test_authenticated_owner_api_run_replay_history_and_conflict(self):
        self.assertEqual(self.request("/api/procedures", token=None)["status"], 401)
        self.assertEqual(self.request("/api/procedures", token="wrong")["status"], 401)
        self.assertEqual(self.request("/api/procedures", origin="https://example.invalid")["status"], 403)
        catalog = self.request("/api/procedures")
        self.assertEqual(catalog["status"], 200)
        self.assertEqual(len(catalog["data"]["procedures"]), 8)
        self.assertEqual(catalog["data"]["runs"], [])
        args = {"request_id": "owner-api-run"}
        first = self.request("/api/procedures/power_budget/run", "POST", args)
        self.assertEqual(first["status"], 201)
        self.assertEqual(first["data"]["run"]["model_calls"], 0)
        replay = self.request("/api/procedures/power_budget/run", "POST", args)
        self.assertEqual(replay["status"], 201)
        self.assertTrue(replay["data"]["run"]["replayed"])
        self.assertEqual(replay["data"]["run"]["artifacts"], first["data"]["run"]["artifacts"])
        conflict = self.request("/api/procedures/stock_rotation/run", "POST", args)
        self.assertEqual(conflict["status"], 409)
        history = self.request("/api/procedures/runs")
        self.assertEqual(len(history["data"]["runs"]), 1)
        self.assertEqual(self.request("/api/procedures/unknown/run", "POST", {"request_id": "other"})["status"], 400)
        self.assertEqual(self.request("/api/procedures/power_budget/run", "POST", {"request_id": "../outside"})["status"], 400)
        self.assertEqual(self.request("/api/procedures/power_budget/run", "POST", {"request_id": "new", "code": "unapproved"})["status"], 400)

    def test_model_wrapper_discovery_run_history_and_cancel_keep_fixed_authority(self):
        engine = Engine(self.settings, self.store, NoModelCalls())
        registry = engine.tools
        self.assertEqual(registry.policy("local_procedure", {"action": "run"}), "auto")
        catalog = registry.execute("local_procedure", {"action": "catalog"})
        self.assertTrue(catalog["ok"])
        self.assertEqual({row["id"] for row in catalog["procedures"]}, {row["id"] for row in self.procedures.catalog()["procedures"]})
        run = registry.execute("local_procedure", {"action": "run", "procedure_id": "power_budget", "request_id": "model-wrapper-run"})
        self.assertTrue(run["ok"])
        self.assertEqual(run["run"]["model_calls"], 0)
        self.assertTrue(registry.execute("local_procedure", {"action": "run", "procedure_id": "power_budget", "request_id": "model-wrapper-run"})["run"]["replayed"])
        self.assertEqual(len(registry.execute("local_procedure", {"action": "history"})["runs"]), 1)
        self.assertFalse(registry.execute("local_procedure", {"action": "run", "procedure_id": "run_python", "request_id": "not-code"})["ok"])
        self.assertFalse(registry.execute("local_procedure", {"action": "run", "procedure_id": "power_budget", "request_id": "cancelled"}, cancelled=lambda: True)["ok"])
        self.assertFalse((registry.root / "procedures/cancelled").exists())
        self.assertFalse(registry.execute("local_procedure", {"action": "run", "procedure_id": "power_budget", "request_id": "invalid-extra", "output": "E:/never"})["ok"])


if __name__ == "__main__":
    unittest.main()
