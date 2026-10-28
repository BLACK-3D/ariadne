"""Durable migration, owner expectations, sources and capability regression tests."""
import base64
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.sources import SourceManager, SourceChanged
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.verification import validate_checks, verify


def call(name, arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


class Model:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def chat(self, name, messages, tools):
        self.calls.append(messages)
        return (self.responses.pop(0) if self.responses else {"role": "assistant", "content": "Finished."}), {}

    def status(self):
        return {"available": True, "models": [{"name": "qwen3:8b"}]}


class NewCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-fixture-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(data_dir=self.root / "private-data", api_token="fixture-only-key")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.sources = SourceManager(self.settings, self.store)
        self.reference = self.root / "reference"
        self.reference.mkdir()

    def engine(self, replies):
        return Engine(self.settings, self.store, Model(replies))

    def process(self, engine, run):
        self.store.next_run()
        engine.process(run["id"])
        return self.store.details(run["id"])

    def test_owner_checks_trigger_bounded_repair_and_hash_verified_bytes(self):
        checks = [{"kind": "file_contains", "path": "business/result.md", "text": "verified evidence"}]
        engine = self.engine([{"role": "assistant", "content": "Already done."}, call("file_write", {"path": "business/result.md", "content": "verified evidence"}), {"role": "assistant", "content": "Repaired."}])
        run = self.store.new_run("Make a result", checks=checks)
        detail = self.process(engine, run)
        self.assertEqual(detail["run"]["status"], "completed")
        self.assertEqual(detail["verification"]["status"], "passed")
        self.assertEqual(detail["run"]["verification_attempts"], 2)
        self.assertEqual(detail["artifacts"][0]["sha256"], hashlib.sha256(b"verified evidence").hexdigest())
        self.assertEqual(detail["run"]["checks"], checks)
        self.assertTrue(any(e["kind"] == "repair" for e in detail["events"]))

    def test_false_claims_never_pass_expectations_and_unknown_tools_recover(self):
        checks = [{"kind": "file_exists", "path": "missing.md"}]
        engine = self.engine([call("override_owner_checks", {"checks": []}), {"role": "assistant", "content": "Everything passed."}])
        run = self.store.new_run("Create output", checks=checks)
        detail = self.process(engine, run)
        self.assertEqual(detail["run"]["status"], "completed")
        self.assertEqual(detail["verification"]["status"], "failed")
        self.assertEqual(detail["run"]["verification_attempts"], 2)
        self.assertEqual(detail["run"]["checks"], checks)
        self.assertTrue(any("Unknown or unavailable" in m.get("content", "") for m in detail["messages"]))

    def test_file_checks_reject_escapes_and_ambiguous_json(self):
        for path in ("../escape.md", "E:\\file.txt", "/outside"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_checks([{"kind": "file_exists", "path": path}], self.registry)
        self.registry.execute("file_write", {"path": "bad.json", "content": '{"total":1,"total":2}'})
        report = verify([{"kind": "json_valid", "path": "bad.json"}], self.registry)
        self.assertEqual(report["status"], "failed")

    def test_workflow_launch_snapshots_owner_contract(self):
        checks = [{"kind": "file_exists", "path": "output.md"}]
        workflow = self.store.workflow_save("Original", "Original mission", checks)
        run = self.store.workflow_run(workflow["id"])
        self.store.workflow_save("Changed", "Changed mission", [], workflow["id"])
        self.assertEqual(run["prompt"], "Original mission")
        self.assertEqual(run["checks"], checks)

    def test_exact_numeric_failure_triggers_bounded_repair(self):
        checks = validate_checks([{"kind": "json_number", "path": "summary.json", "pointer": "/total_value", "value": "16.00"}], self.registry)
        self.registry.execute("file_write", {"path": "summary.json", "content": '{"total_value":6.75}'})
        before = verify(checks, self.registry)
        self.assertEqual(before["status"], "failed")
        self.assertIn("expected 16.00, observed 6.75", before["checks"][0]["message"])
        engine = self.engine([{"role": "assistant", "content": "Done."}, call("file_write", {"path": "summary.json", "content": '{"total_value":"16"}', "overwrite": True}), {"role": "assistant", "content": "Corrected."}])
        detail = self.process(engine, self.store.new_run("Calculate weighted total", checks=checks))
        self.assertEqual(detail["verification"]["status"], "passed")
        self.assertEqual(detail["run"]["verification_attempts"], 2)

    def test_numeric_pointer_escapes_arrays_and_invalid_values(self):
        self.registry.execute("file_write", {"path": "nested.json", "content": '{"a/b":{"~field":["0.10000000000000000001",true]}}'})
        check = {"kind": "json_number", "path": "nested.json", "pointer": "/a~1b/~0field/0", "value": "0.10000000000000000001"}
        self.assertEqual(verify(validate_checks([check], self.registry), self.registry)["status"], "passed")
        for pointer in ("/a~1b/~0field/1", "/a~1b/~0field/01", "/missing", "/a~1b/~0field/-"):
            self.assertEqual(verify([{**check, "pointer": pointer}], self.registry)["status"], "failed")
        for patch_value in ({"pointer": "bad"}, {"pointer": "/~2"}, {"pointer": "/" * 33}, {"value": True}, {"value": 0.1}, {"value": "NaN"}, {"value": "1e401"}, {"value": "1+1"}, {"value": "1" * 201}, {"path": "../escape"}):
            with self.subTest(patch_value=patch_value), self.assertRaises(ValueError):
                validate_checks([{**check, **patch_value}], self.registry)
        for content in ('{"n":NaN}', '{"n":true}', '{"n":"Infinity"}', '{"n":1e401}', '{"n":1,"n":1}'):
            self.registry.execute("file_write", {"path": "number.json", "content": content, "overwrite": True})
            self.assertEqual(verify([{"kind": "json_number", "path": "number.json", "pointer": "/n", "value": "1"}], self.registry)["status"], "failed")

    def test_source_identity_refresh_retains_provenance_and_warnings(self):
        file = self.reference / "Guide.md"
        file.write_text("olduniquereference", encoding="utf-8")
        source = self.sources.register("Reference", str(self.reference))
        preview = self.sources.preview(source["id"], "Guide.md")
        imported = self.sources.import_reference(source["id"], "Guide.md", preview["sha256"])
        self.assertFalse(imported["unchanged"])
        self.assertTrue(self.sources.import_reference(source["id"], "Guide.md", preview["sha256"])["unchanged"])
        file.write_text("newuniquereference", encoding="utf-8")
        with self.assertRaises(SourceChanged):
            self.sources.import_reference(source["id"], "Guide.md", preview["sha256"])
        preview = self.sources.preview(source["id"], "Guide.md")
        with patch("ariadne.documents.extract_document", return_value={"text": "newuniquereference", "sha256": preview["sha256"], "bytes": 18, "format": "text", "warnings": ["Partial extraction fixture"]}):
            result = self.sources.import_reference(source["id"], "Guide.md", preview["sha256"])
        self.assertEqual(result["document"]["version"], 2)
        self.assertEqual(len(self.store.documents()), 1)
        self.assertEqual(self.store.search("olduniquereference", True), [])
        self.assertEqual(self.store.search("newuniquereference", True)[0]["warnings"], ["Partial extraction fixture"])

    def test_failure_stop_and_explicit_reenable(self):
        schedule = self.store.schedule_add("Report", "Create report", 5)
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET next_run=0")
        self.store.due_schedules()
        run = self.store.next_run()
        self.store.update_run(run["id"], status="failed", error="Fixture failure")
        self.assertFalse(self.store.schedules()[0]["enabled"])
        self.store.schedule_update(schedule["id"], True)
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET next_run=0")
        self.store.due_schedules()
        self.assertEqual(len(self.store.runs()), 2)

    def test_runner_approval_binds_all_snapshot_bytes(self):
        registry = ToolRegistry(replace(self.settings, enable_docker=True))
        registry.execute("file_write", {"path": "script.py", "content": "print('one')"})
        registry.execute("file_write", {"path": "input.json", "content": "[]"})
        args = {"path": "script.py"}
        approved = registry.fingerprint("run_python", args)
        registry.execute("file_write", {"path": "input.json", "content": "[1]", "overwrite": True})
        self.assertNotEqual(approved, registry.fingerprint("run_python", args))
        with patch("ariadne.tools.shutil.which", return_value="fixture-docker"), patch.object(registry, "_execute_docker") as execute:
            result = registry.execute("run_python", args, approval_fingerprint=approved)
        execute.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("changed after approval", result["error"])

    def test_legacy_database_migrates_without_losing_state(self):
        legacy = self.root / "legacy.sqlite3"
        db = sqlite3.connect(legacy)
        db.executescript("CREATE TABLE documents(id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at REAL NOT NULL); INSERT INTO documents VALUES ('old','Previous note',1);")
        db.close()
        migrated = Store(legacy)
        self.assertEqual(migrated.documents()[0]["title"], "Previous note")
        self.assertEqual(migrated.documents()[0]["warnings"], [])
        migrated.document_add("New note", "Imported after migration")
        self.assertEqual(len(Store(legacy).documents()), 2)


if __name__ == "__main__":
    unittest.main()
