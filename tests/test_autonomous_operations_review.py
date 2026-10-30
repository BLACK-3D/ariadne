"""Independent authority, immutable-input and recovery checks for typed routines."""
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
from pathlib import Path
import os
import tempfile
import threading
import tracemalloc
import unittest
from unittest import mock
import zipfile

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.market_research import MarketResearchTools
from ariadne.operations_workflows import OperationsWorkflows
from ariadne.routines import RoutineConflict
from ariadne.store import Store


class NoModel:
    def chat(self, *args, **kwargs):
        raise AssertionError("A fixed autonomous operation cannot invoke inference")

    def status(self):
        return {"available": False, "models": []}


class AutonomousOperationsIndependentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix=".operations-test-autonomy-review-", dir=Path.cwd())
        self.addCleanup(folder.cleanup)
        self.settings = Settings(data_dir=Path(folder.name), api_token="independent-autonomy-fixture")
        self.store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.engine = Engine(self.settings, self.store, NoModel())
        self.registry = self.engine.tools
        self.workflows = OperationsWorkflows(self.registry.procedures.service(), self.store)

    def write(self, path, content):
        return self.registry._file_write({"path": path, "content": content, "overwrite": True})

    def market(self):
        fixture = Path(__file__).resolve().parents[1] / "config/examples"
        raw = (fixture / "market-demo.csv").read_text(encoding="utf-8")
        metadata = (fixture / "market-demo.json").read_text(encoding="utf-8")
        self.write("review/bars.csv", raw)
        self.write("review/metadata.json", metadata)
        args = {"path": "review/bars.csv", "metadata_path": "review/metadata.json"}
        return raw.encode(), [{"operation": "market_audit", "arguments": args}, {"operation": "market_study", "arguments": args}]

    def finish(self, definition, key):
        run = self.workflows.enqueue(definition["id"], key)
        for _ in range(5):
            if run["status"] not in {"queued", "running"}:
                return run
            self.assertTrue(self.workflows.tick())
            run = self.workflows.detail(key)
        self.fail("The bounded operation did not terminate within its four-step plan")

    def report_text(self, run):
        return "\n".join(self.registry._path(item["path"]).read_text(encoding="utf-8") for item in run["artifacts"])

    def test_only_reviewed_operations_are_accepted_and_market_study_requires_same_data_audit(self):
        for operation in ("home_control", "refresh", "apply", "connector_ha_light_on", "run_python", "file_write"):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.workflows.save("Forbidden", [{"operation": operation, "arguments": {}}])
        for extra in ({"action": "apply"}, {"request_id": "chosen-child"}, {"endpoint": "http://example.test"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.workflows.save("No argument authority", [{"operation": "device_status", "arguments": extra}])
        _, steps = self.market()
        with self.assertRaises(ValueError):
            self.workflows.save("Missing audit", [steps[1]])
        with self.assertRaises(ValueError):
            self.workflows.save("Different audit", [steps[0], {"operation": "market_study", "arguments": {**steps[1]["arguments"], "path": "review/other.csv"}}])

    def test_business_snapshot_uses_the_accepted_transaction_after_owner_records_change(self):
        self.registry.business.execute("business_records", {"action": "upsert_item", "request_id": "frozen-item", "record": {"id": "review", "name": "Accepted item", "unit": "Nos", "unit_price_cents": 123}})
        definition = self.workflows.save("Accepted business", [{"operation": "business_snapshot", "arguments": {}}])
        self.workflows.enqueue(definition["id"], "frozen-business")
        self.registry.business.execute("business_records", {"action": "upsert_item", "request_id": "changed-item", "record": {"id": "review", "name": "Changed later item", "unit": "Nos", "unit_price_cents": 999}})
        self.assertTrue(self.workflows.tick())
        run = self.workflows.detail("frozen-business")
        self.assertEqual(run["status"], "completed")
        output = self.report_text(run)
        self.assertIn("Accepted item", output)
        self.assertNotIn("Changed later item", output)

    def test_market_audit_and_study_consume_accepted_bytes_after_workspace_replacement(self):
        original, steps = self.market()
        definition = self.workflows.save("Frozen market", steps)
        self.workflows.enqueue(definition["id"], "frozen-market")
        self.write("review/bars.csv", "untrusted replacement is not historical bars\n")
        with mock.patch("socket.socket", side_effect=AssertionError("No network")), mock.patch("subprocess.Popen", side_effect=AssertionError("No process or model")):
            self.assertTrue(self.workflows.tick())
            self.assertTrue(self.workflows.tick())
        run = self.workflows.detail("frozen-market")
        self.assertEqual(run["status"], "completed")
        self.assertIn(hashlib.sha256(original).hexdigest(), self.report_text(run))
        self.assertNotIn("untrusted replacement", self.report_text(run))
        self.assertEqual(run["model_calls"], 0)

    def test_audit_study_share_one_file_observation_even_if_writer_changes_during_acceptance(self):
        original, steps = self.market()
        definition = self.workflows.save("One accepted observation", steps)
        read = self.workflows._read_workspace
        observed = []

        def replacing_reader(path, cap):
            raw = read(path, cap)
            observed.append(path)
            if path == "review/bars.csv":
                self.write(path, "changed between the audit and study capture stages\n")
            return raw

        with mock.patch.object(self.workflows, "_read_workspace", replacing_reader):
            self.workflows.enqueue(definition["id"], "capture-once")
        self.assertEqual(observed.count("review/bars.csv"), 1)
        self.assertEqual(observed.count("review/metadata.json"), 1)
        self.assertTrue(self.workflows.tick()); self.assertTrue(self.workflows.tick())
        run = self.workflows.detail("capture-once")
        self.assertEqual(run["status"], "completed")
        self.assertIn(hashlib.sha256(original).hexdigest(), self.report_text(run))

    def test_corrupt_oversized_private_blob_is_rejected_before_large_python_allocation(self):
        from ariadne.operations_workflows import MAX_INPUT_BYTES
        digest = "f" * 64
        with self.store.connect() as db:
            db.execute("INSERT INTO operation_inputs VALUES(?,zeroblob(?),1)", (digest, MAX_INPUT_BYTES + 1))
        tracemalloc.start()
        try:
            with self.assertRaises(ValueError):
                self.workflows._get({"sha256": digest, "bytes": 1})
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 1_000_000, "An oversized private BLOB must fail its size metadata check before materializing content")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT length(content) FROM operation_inputs WHERE digest=?", (digest,)).fetchone()[0], MAX_INPUT_BYTES + 1)

    def test_dispatch_does_not_redirect_concurrent_desk_reads_to_frozen_input(self):
        original_bytes, steps = self.market()
        definition = self.workflows.save("No provider crossover", [steps[0]])
        self.workflows.enqueue(definition["id"], "isolated-provider")
        rows = list(csv.DictReader(io.StringIO(original_bytes.decode())))
        for row in rows:
            row["volume"] = str(int(row["volume"]) + 1)
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
        current_bytes = stream.getvalue().encode()
        self.write("review/bars.csv", current_bytes.decode())
        entered, release = threading.Event(), threading.Event()
        execute = MarketResearchTools.execute

        def park(instance, *args, **kwargs):
            if instance is not self.registry.market_research:
                entered.set()
                if not release.wait(8):
                    raise AssertionError("Timed out waiting for concurrent desk read")
            return execute(instance, *args, **kwargs)

        with mock.patch.object(MarketResearchTools, "execute", park), ThreadPoolExecutor(max_workers=2) as workers:
            future = workers.submit(self.workflows.tick)
            try:
                self.assertTrue(entered.wait(5), "Typed dispatch must use its own provider instance")
                owner = workers.submit(self.registry.execute, "market_research", {"action": "audit", **steps[0]["arguments"], "request_id": "concurrent-current"})
                result = owner.result(timeout=5)
                self.assertTrue(result["ok"], result)
                report = self.registry._path(result["artifacts"][0]["path"]).read_text(encoding="utf-8")
                self.assertIn(hashlib.sha256(current_bytes).hexdigest(), report)
                self.assertNotIn(hashlib.sha256(original_bytes).hexdigest(), report)
            finally:
                release.set()
            self.assertTrue(future.result(timeout=8))
        self.assertEqual(self.workflows.detail("isolated-provider")["status"], "completed")

    def test_input_quota_failure_rolls_back_blobs_queue_and_last_run(self):
        _, steps = self.market()
        definition = self.workflows.save("Quota rollback", [steps[0]])
        with mock.patch("ariadne.operations_workflows.MAX_INPUT_TOTAL_BYTES", 1), self.assertRaises(ValueError):
            self.workflows.enqueue(definition["id"], "quota-overflow")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM operation_inputs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM routine_runs WHERE id='quota-overflow'").fetchone()[0], 0)
        current = next(row for row in self.workflows.definitions() if row["id"] == definition["id"])
        self.assertIsNone(current["last_run_id"])

    def test_interrupted_child_is_not_adopted_or_replayed_and_stale_enable_is_rejected(self):
        definition = self.workflows.save("Crash hold", [{"operation": "business_snapshot", "arguments": {}}], enabled=True)
        self.workflows.enqueue(definition["id"], "interrupted-child")
        with mock.patch.object(self.workflows, "_run_child", side_effect=SystemExit("simulated process interruption")), self.assertRaises(SystemExit):
            self.workflows.tick()
        second = OperationsWorkflows(self.registry.procedures.service(), self.store)
        with mock.patch.object(second, "_run_child", side_effect=AssertionError("Never replay a started child")):
            self.assertEqual(second.recover(), 1)
            self.assertFalse(second.tick())
        self.assertEqual(second.detail("interrupted-child")["status"], "interrupted")
        current = next(row for row in second.definitions() if row["id"] == definition["id"])
        self.assertFalse(current["enabled"])
        with self.assertRaises(RoutineConflict):
            second.save("Stale resume", definition["steps"], enabled=True, routine_id=definition["id"], expected_revision=definition["revision"])

    def test_tampered_earlier_artifact_stops_following_step_and_recurrence(self):
        definition = self.workflows.save("Checked boundary", [{"operation": "business_snapshot", "arguments": {}}, {"operation": "device_status", "arguments": {}}], enabled=True)
        self.workflows.enqueue(definition["id"], "tampered-boundary")
        self.assertTrue(self.workflows.tick())
        first = self.workflows.detail("tampered-boundary")
        artifact = self.registry._path(first["artifacts"][0]["path"])
        artifact.write_bytes(artifact.read_bytes() + b" ")
        with mock.patch.object(self.workflows, "_run_child", side_effect=AssertionError("Do not advance after corruption")):
            self.assertTrue(self.workflows.tick())
        self.assertEqual(self.workflows.detail("tampered-boundary")["status"], "failed")
        self.assertFalse(next(row for row in self.workflows.definitions() if row["id"] == definition["id"])["enabled"])

    def test_global_pause_holds_due_work_and_each_pending_child(self):
        definition = self.workflows.save("Paused typed work", [{"operation": "business_snapshot", "arguments": {}}], enabled=True)
        self.workflows.enqueue(definition["id"], "paused-work")
        self.store.set_setting("paused", True)
        with mock.patch.object(self.workflows, "_run_child", side_effect=AssertionError("Paused child must not dispatch")):
            self.assertFalse(self.workflows.tick())
            self.assertEqual(self.workflows.due(), 0)
        self.assertEqual(self.workflows.detail("paused-work")["status"], "queued")

    def test_failed_market_audit_stops_before_study_and_disables_recurrence(self):
        original, steps = self.market()
        lines = original.decode().splitlines()
        self.write("review/bars.csv", "\n".join([lines[0], lines[1], lines[1], *lines[2:]]) + "\n")
        definition = self.workflows.save("Audit gate", steps, enabled=True)
        self.workflows.enqueue(definition["id"], "invalid-market-data")
        calls = []
        execute = MarketResearchTools.execute

        def observe(instance, name, args, **kwargs):
            calls.append(args["action"])
            return execute(instance, name, args, **kwargs)

        with mock.patch.object(MarketResearchTools, "execute", observe):
            self.assertTrue(self.workflows.tick())
            self.assertFalse(self.workflows.tick())
        self.assertEqual(calls, ["audit"])
        self.assertEqual(self.workflows.detail("invalid-market-data")["status"], "failed")
        self.assertFalse(next(row for row in self.workflows.definitions() if row["id"] == definition["id"])["enabled"])

    def test_comparison_reports_unsupported_operation_coverage_without_false_change_claims(self):
        definition = self.workflows.save("Mixed coverage", ["business_snapshot", {"operation": "device_status", "arguments": {}}])
        first = self.finish(definition, "mixed-first")
        comparison = self.workflows.compare(first["id"])
        self.assertEqual(comparison["state"], "first_run")
        self.assertFalse(comparison["comparison_complete"])
        self.assertEqual(len(comparison["steps"]), 2)
        self.assertTrue(any("supported" in json.dumps(row).lower() or "compared" in json.dumps(row).lower() for row in comparison["steps"] if row["procedure_id"] == "operation_device_status"))
        second = self.finish(definition, "mixed-second")
        compared = self.workflows.compare(second["id"], first["id"])
        self.assertEqual(compared["state"], "partial")
        self.assertFalse(compared["comparison_complete"])
        self.assertFalse(compared["metadata_only"])
        typed = next(row for row in compared["steps"] if row["procedure_id"] == "operation_device_status")
        self.assertFalse(typed["comparison_complete"])
        self.assertTrue(typed.get("warning"))
        self.assertEqual(typed["changes"], [])
        self.assertTrue(next(row for row in compared["steps"] if row["procedure_id"] == "business_snapshot")["comparison_complete"])


class PortableBoundaryIndependentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix=".operations-test-portable-review-", dir=Path.cwd())
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_custom_model_port_cannot_adopt_an_existing_listener_via_legacy_pid_check(self):
        from scripts import provision
        with mock.patch.dict(os.environ, {"ARIADNE_MODEL_PORT": "19634"}), mock.patch.object(provision, "api", return_value={}), mock.patch.object(provision, "owned_model_server", return_value=True), mock.patch.object(provision.subprocess, "Popen") as spawn:
            with self.assertRaises(SystemExit):
                provision.start_model_server(self.root / "ollama.exe", {}, allow_owned=True)
            spawn.assert_not_called()

    def test_one_click_flow_override_conflict_fails_before_model_spawn_without_owner_rewrite(self):
        from scripts import launch
        data = self.root / "data"; data.mkdir()
        settings = Settings(data_dir=data)
        path = data / "automation-config.json"
        original = json.dumps({"flows": [{"id": "flows_health", "token_file": "flows.token", "url": "http://127.0.0.1:1880/ariadne/health"}]}).encode()
        path.write_bytes(original)
        with mock.patch.dict(os.environ, {"ARIADNE_FLOW_PORT": "19880"}), mock.patch.object(launch, "ROOT", self.root), mock.patch.object(launch, "load_settings", return_value=settings), mock.patch("ariadne.box_profiles.bootstrap_profiles"), mock.patch.object(launch, "start_model_server") as model, mock.patch("sys.argv", ["launch.py", "--no-browser"]):
            with self.assertRaises(ValueError):
                launch.main()
        model.assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_full_factory_export_omits_private_state_and_reanchored_voice_metadata_is_rejected(self):
        from scripts import export_offline
        from tests.test_offline_package_update import VoiceExportPinsTests
        fixture = VoiceExportPinsTests("test_complete_voice_pins_include_weights_licenses_wheels_and_installed_engine")
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        secret = b"INDEPENDENT_OWNER_STATE_MUST_STAY_PRIVATE"
        for name in ("ariadne.sqlite3", "local-controls.sqlite3", "local-controls.json", "connectors.json", "connector-secrets.json"):
            (fixture.root / "data" / name).write_bytes(secret)
        reports = fixture.root / "data/workspace/private"; reports.mkdir(parents=True)
        (reports / "orders.json").write_bytes(secret)
        output = fixture.root / "dist/independent-private-exclusion.zip"
        export_offline.export(output, fixture.root)
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            self.assertFalse({"data/" + name for name in ("ariadne.sqlite3", "local-controls.sqlite3", "local-controls.json", "connectors.json", "connector-secrets.json")} & names)
            self.assertFalse(any(name.startswith("data/workspace/") for name in names))
            self.assertTrue(all(secret not in archive.read(name) for name in names))
        changed = json.loads(fixture.catalog_path.read_text(encoding="utf-8"))
        changed["unreviewed_provenance"] = "replacement source or license"
        fixture.catalog_path.write_text(json.dumps(changed), encoding="utf-8")
        installed = json.loads(fixture.install.read_text(encoding="utf-8"))
        installed["catalog_sha256"] = hashlib.sha256(fixture.catalog_path.read_bytes()).hexdigest()
        fixture.install.write_text(json.dumps(installed), encoding="utf-8")
        with self.assertRaises(ValueError):
            export_offline._speech_pins(fixture.root, fixture.selected)


if __name__ == "__main__":
    unittest.main()
