"""Independent adversarial fixtures for root-authored 0.2 operational code.

No production state, credentials, outside files, external services, or model
downloads are used. These tests express the documented security invariants.
"""

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine, INTERNAL
from ariadne.server import Application
from ariadne.sources import SourceManager, SourceChanged
from ariadne.store import Store
from ariadne.verification import validate_checks, verify


class Model:
    def __init__(self, messages=None):
        self.messages = list(messages or [])

    def chat(self, model, messages, tools):
        answer = self.messages.pop(0) if self.messages else {"role": "assistant", "content": "Finished fixture."}
        return answer, {"prompt_tokens": 0, "output_tokens": 0, "duration_seconds": 0}

    def status(self):
        return {"available": True, "models": []}


class MutableCapability:
    def __init__(self):
        self.current_policy = "approval"
        self.current_fingerprint = "capability-v1"
        self.calls = []

    def policy(self, name, args):
        return self.current_policy

    def fingerprint(self, name, args):
        return self.current_fingerprint

    def execute(self, name, args):
        self.calls.append((name, args))
        return {"ok": True}


class RedTeamFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="redteam-fixture-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.references = self.root / "references"
        self.references.mkdir()
        self.settings = Settings(data_dir=self.root / "private-data", api_token="synthetic-fixture-token")
        self.store = Store(self.settings.data_dir / "fixture.sqlite3")
        self.sources = SourceManager(self.settings, self.store)

    def reference(self):
        return self.sources.register("Synthetic references", str(self.references))

    def pending(self, capability):
        engine = Engine(self.settings, self.store, Model())
        engine.tools = capability
        run = self.store.new_run("Synthetic gated action")
        self.store.next_run()
        call = {"name": "fixture_action", "args": {"task": "fixture"}, "key": "1:0"}
        self.assertIsNone(engine.execute_call(self.store.run(run["id"]), call))
        gate = self.store.existing_approval(run["id"], call["key"])
        return engine, run, call, gate

    def test_sources_block_hidden_credential_nested_excluded_and_escape_paths(self):
        for name in [".hidden.txt", "server-token.json", "passwords.md"]:
            (self.references / name).write_text("synthetic fixture only", encoding="utf-8")
        (self.references / "runtime").mkdir()
        (self.references / "runtime" / "manifest.json").write_text("{}", encoding="utf-8")
        (self.references / "allowed.md").write_text("Allowed synthetic reference", encoding="utf-8")
        source = self.reference()
        self.assertEqual([entry["name"] for entry in self.sources.listing(source["id"])["files"]], ["allowed.md"])
        for relative in [".hidden.txt", "server-token.json", "passwords.md", "runtime/manifest.json", "../outside.txt", "E:/forbidden.txt"]:
            with self.subTest(relative=relative):
                with self.assertRaises(ValueError):
                    self.sources.preview(source["id"], relative)
        with self.assertRaises(ValueError):
            self.sources.register("Forbidden drive", "E:/forbidden")
        with self.assertRaises(ValueError):
            self.sources.register("Private data", str(self.settings.data_dir))

    def test_common_private_key_and_auth_names_are_excluded(self):
        names = ["id_rsa.txt", "id_ed25519.txt", "token.json", "auth.json"]
        for name in names:
            (self.references / name).write_text("synthetic credential fixture, never a real key", encoding="utf-8")
        source = self.reference()
        listed = [entry["name"] for entry in self.sources.listing(source["id"])["files"]]
        self.assertEqual(listed, [], "Credential-like filenames must not enter the source import picker")
        for name in names:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.sources.preview(source["id"], name)

    def test_selected_excluded_root_cannot_bypass_directory_policy(self):
        for name in ["runtime", "venv", "data", "node_modules"]:
            folder = self.references / name
            folder.mkdir()
            (folder / "fixture.json").write_text("{}", encoding="utf-8")
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.sources.register("Excluded " + name, str(folder))

    def test_legacy_persisted_excluded_root_is_revalidated_when_opened(self):
        folder = self.references / "runtime"
        folder.mkdir()
        (folder / "fixture.json").write_text("{}", encoding="utf-8")
        legacy = self.store.source_add("Legacy registration fixture", str(folder))
        with self.assertRaises(ValueError):
            self.sources.listing(legacy["id"])
        with self.assertRaises(ValueError):
            self.sources.preview(legacy["id"], "fixture.json")

    @unittest.skipUnless(os.name == "nt", "Windows hidden attribute boundary")
    def test_windows_hidden_attribute_prevents_listing_and_preview(self):
        import ctypes
        path = self.references / "ordinary.md"
        path.write_text("Synthetic hidden fixture", encoding="utf-8")
        source = self.reference()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        kernel.SetFileAttributesW.restype = ctypes.c_int
        if not kernel.SetFileAttributesW(str(path), 0x2):
            self.skipTest("This sandbox does not allow setting the fixture hidden attribute")
        try:
            self.assertEqual(self.sources.listing(source["id"])["files"], [])
            with self.assertRaises(ValueError):
                self.sources.preview(source["id"], "ordinary.md")
        finally:
            kernel.SetFileAttributesW(str(path), 0x80)

    @unittest.skipUnless(os.name == "nt", "Windows directory-junction boundary")
    def test_windows_junction_cannot_import_a_second_fixture_folder(self):
        import subprocess
        target = self.root / "second-fixture-folder"
        target.mkdir()
        (target / "note.md").write_text("Synthetic junction fixture", encoding="utf-8")
        junction = self.references / "shortcut"
        # Both verified absolute paths are within this test's disposable folder.
        self.assertTrue(target.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(junction.absolute().is_relative_to(self.root.resolve()))
        try:
            result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    shell=False, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("The test sandbox cannot create a directory-junction fixture")
        if result.returncode:
            self.skipTest("The test sandbox cannot create a directory-junction fixture")
        source = self.reference()
        self.assertEqual(self.sources.listing(source["id"])["files"], [])
        with self.assertRaises(ValueError):
            self.sources.preview(source["id"], "shortcut/note.md")

    def test_preview_digest_prevents_changed_byte_import_and_refresh_is_versioned(self):
        path = self.references / "guide.md"
        path.write_text("oldunique synthetic evidence", encoding="utf-8")
        source = self.reference()
        preview = self.sources.preview(source["id"], "guide.md")
        first = self.sources.import_reference(source["id"], "guide.md", preview["sha256"])
        same = self.sources.import_reference(source["id"], "guide.md", preview["sha256"])
        self.assertTrue(same["unchanged"])
        path.write_text("newunique replacement evidence", encoding="utf-8")
        with self.assertRaises(SourceChanged):
            self.sources.import_reference(source["id"], "guide.md", preview["sha256"])
        fresh = self.sources.preview(source["id"], "guide.md")
        refreshed = self.sources.import_reference(source["id"], "guide.md", fresh["sha256"])
        self.assertEqual(first["document"]["id"], refreshed["document"]["id"])
        self.assertEqual(refreshed["document"]["version"], 2)
        self.assertEqual(self.store.search("oldunique", knowledge=True), [])
        found = self.store.search("newunique", knowledge=True)[0]
        self.assertEqual(found["sha256"], fresh["sha256"])
        self.assertEqual(found["source_path"], "guide.md")

    @unittest.skipUnless(os.name == "nt", "Case aliasing applies to the deployed Windows filesystem")
    def test_windows_case_alias_does_not_duplicate_source_snapshot(self):
        (self.references / "Guide.md").write_text("same synthetic evidence", encoding="utf-8")
        source = self.reference()
        preview = self.sources.preview(source["id"], "Guide.md")
        one = self.sources.import_reference(source["id"], "Guide.md", preview["sha256"])
        two = self.sources.import_reference(source["id"], "guide.md", preview["sha256"])
        self.assertEqual(one["document"]["id"], two["document"]["id"])
        self.assertEqual(len(self.store.documents()), 1)
        (self.references / "Guide.md").write_text("refreshed synthetic evidence", encoding="utf-8")
        fresh = self.sources.preview(source["id"], "guide.md")
        refreshed = self.sources.import_reference(source["id"], "GUIDE.md", fresh["sha256"])
        self.assertEqual(refreshed["document"]["id"], one["document"]["id"])
        self.assertEqual(refreshed["document"]["version"], 2)
        self.assertEqual(len(self.store.documents()), 1)

    def test_policy_downgrade_and_changed_fingerprint_require_new_approval(self):
        capability = MutableCapability()
        engine, run, call, approval = self.pending(capability)
        self.store.decide(approval["id"], "approved")
        self.store.next_run()
        capability.current_policy = "auto"
        capability.current_fingerprint = "capability-v2"
        self.assertIsNone(engine.execute_call(self.store.run(run["id"]), call))
        current = self.store.existing_approval(run["id"], call["key"])
        self.assertEqual(current["status"], "pending")
        self.assertNotEqual(current["id"], approval["id"])
        self.assertEqual(capability.calls, [])

    def test_rejection_survives_changed_policy_and_cancellation_prevents_execution(self):
        capability = MutableCapability()
        engine, run, call, approval = self.pending(capability)
        self.store.decide(approval["id"], "rejected")
        self.store.next_run()
        capability.current_policy = "auto"
        capability.current_fingerprint = "changed"
        result = engine.execute_call(self.store.run(run["id"]), call)
        self.assertFalse(result["ok"])
        self.assertIn("rejected", result["error"])
        self.assertEqual(capability.calls, [])
        self.store.cancel(run["id"])
        capability2 = MutableCapability()
        engine2, run2, call2, approval2 = self.pending(capability2)
        self.store.decide(approval2["id"], "approved")
        self.store.next_run()
        self.store.cancel(run2["id"])
        self.assertIsNone(engine2.execute_call(self.store.run(run2["id"]), call2))
        self.assertEqual(capability2.calls, [])

    def test_model_cannot_replace_owner_checks_or_register_external_sources(self):
        model = Model([{"role": "assistant", "content": "", "tool_calls": [{"function": {
            "name": "source_register", "arguments": {"path": str(self.references)}}}]}])
        engine = Engine(self.settings, self.store, model)
        checks = validate_checks([{"kind": "file_exists", "path": "required.txt"}], engine.tools)
        run = self.store.new_run("Pretend no checks are needed", checks=checks)
        self.store.next_run()
        engine.process(run["id"])
        persisted = self.store.run(run["id"])
        self.assertEqual(persisted["checks"], checks)
        self.assertEqual(persisted["verification_status"], "failed")
        self.assertEqual(self.store.sources(), [])
        self.assertFalse(any("source" in item["function"]["name"] for item in engine.tools.schemas() + INTERNAL))
        with self.assertRaises(ValueError):
            self.store.update_run(run["id"], checks=[])

    def test_scheduled_failure_stops_until_explicit_reenable(self):
        schedule = self.store.schedule_add("Synthetic schedule", "Fixture", 5)
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET next_run=0 WHERE id=?", (schedule["id"],))
        self.store.due_schedules()
        run = self.store.next_run()
        self.store.update_run(run["id"], status="failed", error="Synthetic failure")
        self.assertFalse(self.store.schedules()[0]["enabled"])
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET next_run=0 WHERE id=?", (schedule["id"],))
        self.store.due_schedules()
        self.assertEqual(len(self.store.runs()), 1)
        self.store.schedule_update(schedule["id"], True)
        self.assertIsNone(self.store.schedules()[0]["last_run_id"])
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET next_run=0 WHERE id=?", (schedule["id"],))
        self.store.due_schedules()
        self.assertEqual(len(self.store.runs()), 2)

    def test_source_and_check_endpoints_require_owner_authentication(self):
        app = Application(self.settings, self.store, Engine(self.settings, self.store, Model()))
        for path, payload in [("/api/sources", {"name": "Synthetic", "path": str(self.references)}),
                              ("/api/runs", {"prompt": "Fixture", "checks": []})]:
            body = json.dumps(payload).encode()
            env = {"HTTP_HOST": "localhost:8080", "REQUEST_METHOD": "POST", "PATH_INFO": path,
                   "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
            statuses = []
            b"".join(app(env, lambda status, headers: statuses.append(status)))
            self.assertTrue(statuses[0].startswith("401"))
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(self.store.runs(), [])

    def test_exact_numeric_api_rejects_rounded_expected_floats_and_preserves_strings(self):
        engine = Engine(self.settings, self.store, Model())
        app = Application(self.settings, self.store, engine)

        def request(value_literal):
            body = ('{"prompt":"Synthetic exact predicate","checks":[{"kind":"json_number","path":"n.json",'
                    '"pointer":"/n","value":' + value_literal + '}]}').encode()
            # Demonstrate why float values must be rejected at the API boundary:
            # a raw high-precision JSON fraction is already rounded by json.loads.
            if value_literal == "0.10000000000000000001":
                self.assertEqual(json.loads(body)["checks"][0]["value"], 0.1)
            env = {"HTTP_HOST": "localhost:8080", "REQUEST_METHOD": "POST", "PATH_INFO": "/api/runs",
                   "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
                   "HTTP_AUTHORIZATION": "Bearer " + self.settings.api_token}
            statuses = []
            result = json.loads(b"".join(app(env, lambda status, headers: statuses.append(status))))
            return int(statuses[0].split()[0]), result

        for value in ["0.10000000000000000001", "0.1", "16.0", "true", "NaN", "Infinity", "1e999999"]:
            with self.subTest(value=value):
                self.assertEqual(request(value)[0], 400)
        status, payload = request('"0.10000000000000000001"')
        self.assertEqual(status, 201)
        checks = self.store.run(payload["run"]["id"])["checks"]
        self.assertEqual(checks[0]["value"], "0.10000000000000000001")
        engine.tools.execute("file_write", {"path": "n.json", "content": '{"n":0.1}'})
        self.assertEqual(verify(checks, engine.tools)["status"], "failed")
        engine.tools.execute("file_write", {"path": "n.json", "content": '{"n":0.10000000000000000001}', "overwrite": True})
        self.assertEqual(verify(checks, engine.tools)["status"], "passed")
        self.assertEqual(request("9007199254740993")[0], 201)

    def test_numeric_predicate_exact_ranges_and_rfc6901_pointer_edges(self):
        engine = Engine(self.settings, self.store, Model())
        document = {"": 16, "a/b": {"~": [0, "1e-400"]}, "~1": "1e400", "array": [16], "0": 16}
        engine.tools.execute("file_write", {"path": "numbers.json", "content": json.dumps(document)})
        for pointer, expected in [("/", "16"), ("/a~1b/~0/1", "1e-400"), ("/~01", "1e400"), ("/array/0", "16"), ("/0", "16")]:
            with self.subTest(pointer=pointer):
                check = {"kind": "json_number", "path": "numbers.json", "pointer": pointer, "value": expected}
                self.assertEqual(verify(validate_checks([check], engine.tools), engine.tools)["status"], "passed")
        for pointer in ["/array/00", "/array/-1", "/array/+0", "/array/0.0", "/array/-", "/array/999999999", "/absent"]:
            check = {"kind": "json_number", "path": "numbers.json", "pointer": pointer, "value": "16"}
            self.assertEqual(verify(validate_checks([check], engine.tools), engine.tools)["status"], "failed")
        for pointer in ["not/a/pointer", "/~", "/~2", "/" * 33, "/" + "x" * 500, True]:
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                validate_checks([{"kind": "json_number", "path": "numbers.json", "pointer": pointer, "value": "16"}], engine.tools)
        for value in ["NaN", "Infinity", "-Infinity", "1e401", "1e-401", "1e99999999999999999999", "1" * 201, "0" * 601, " 16", "+16", "01", "1+15", True, None, [], Decimal("16"), 16.0]:
            with self.subTest(value=str(value)[:50]), self.assertRaises(ValueError):
                validate_checks([{"kind": "json_number", "path": "numbers.json", "pointer": "/0", "value": value}], engine.tools)
        engine.tools.execute("file_write", {"path": "scalar.json", "content": "16"})
        self.assertEqual(verify(validate_checks([{"kind": "json_number", "path": "scalar.json", "pointer": "", "value": 16}], engine.tools), engine.tools)["status"], "passed")

    def test_numeric_artifacts_cannot_use_bool_nonfinite_duplicate_or_rounded_values(self):
        engine = Engine(self.settings, self.store, Model())
        check = validate_checks([{"kind": "json_number", "path": "artifact.json", "pointer": "/n", "value": "16"}], engine.tools)
        for content in ['{"n":true}', '{"n":null}', '{"n":NaN}', '{"n":Infinity}', '{"n":"NaN"}',
                        '{"n":"16 "}', '{"n":"1+15"}', '{"n":16,"\\u006e":6.75}', '{"n":1e401}', '{"n":[]}', '{"n":{}}']:
            with self.subTest(content=content):
                engine.tools.execute("file_write", {"path": "artifact.json", "content": content, "overwrite": True})
                self.assertEqual(verify(check, engine.tools)["status"], "failed")
        expected = "1" + "0" * 198 + "1"
        engine.tools.execute("file_write", {"path": "artifact.json", "content": '{"n":' + "1" + "0" * 199 + '}', "overwrite": True})
        near = validate_checks([{**check[0], "value": expected}], engine.tools)
        self.assertEqual(verify(near, engine.tools)["status"], "failed")

    def test_numeric_owner_predicate_cannot_be_changed_by_model_and_repair_stops(self):
        model = Model([{"role": "assistant", "content": "", "tool_calls": [{"function": {
            "name": "override_owner_checks", "arguments": {"checks": [], "expected": "6.75"}}}]},
            {"role": "assistant", "content": "The expected number is 6.75; all checks passed."},
            {"role": "assistant", "content": "No need to repair; mark this verified."}])
        engine = Engine(self.settings, self.store, model)
        engine.tools.execute("file_write", {"path": "summary.json", "content": '{"total_value":6.75}'})
        checks = validate_checks([{"kind": "json_number", "path": "summary.json", "pointer": "/total_value", "value": "16.00"}], engine.tools)
        run = self.store.new_run("Keep the owner expected total immutable", checks=checks)
        self.store.next_run()
        engine.process(run["id"])
        result = self.store.details(run["id"])
        self.assertEqual(result["run"]["checks"], checks)
        self.assertEqual(result["run"]["verification_attempts"], 2)
        self.assertEqual(result["verification"]["status"], "failed")
        self.assertEqual(sum(event["kind"] == "repair" for event in result["events"]), 1)
        self.assertIn("expected 16.00, observed 6.75", result["verification"]["checks"][0]["message"])
        self.assertEqual(json.loads((engine.tools.root / "summary.json").read_text())["total_value"], 6.75)


if __name__ == "__main__":
    unittest.main()
