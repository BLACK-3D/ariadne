"""Interpreter binding and explicit CIM environment; no models or live servers."""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import host, windows_host


class InterpreterBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".tools-test-interpreter-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "runtime/python"
        self.venv = self.root / ".venv"
        self.base = self.root / "base-python"
        for directory in (self.bundle, self.venv / "Scripts", self.base):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "python.exe").write_bytes(b"synthetic native image")
        self.base_executable = self.base / "python.exe"
        self.config()

    def config(self, **updates):
        values = {"home": str(self.base), "include-system-site-packages": "false", "version": "3.13.99",
                  "executable": str(self.base_executable)}
        values.update(updates)
        (self.venv / "pyvenv.cfg").write_text("".join(k+" = "+v+"\n" for k, v in values.items()), encoding="utf8")

    def identity(self, native=None, pid=123):
        return {"pid": pid, "executable": str(native or self.base_executable), "created_filetime": 100, "owner_sid": "fixture-owner"}

    def context(self, *, virtual=None, prefix=None, base=None, native=None, base_executable=None, exec_prefix=None):
        with ExitStack() as stack:
            stack.enter_context(patch.object(host, "ROOT", self.root))
            stack.enter_context(patch.object(host.sys, "executable", str(virtual or self.venv / "Scripts/python.exe")))
            stack.enter_context(patch.object(host.sys, "prefix", str(prefix or self.venv)))
            stack.enter_context(patch.object(host.sys, "exec_prefix", str(exec_prefix or prefix or self.venv)))
            stack.enter_context(patch.object(host.sys, "base_prefix", str(base or self.base)))
            stack.enter_context(patch.object(host.sys, "_base_executable", str(base_executable or self.base_executable)))
            stack.enter_context(patch.object(host, "process_identity", return_value=self.identity(native)))
            return host.host_interpreter()

    def test_venv_maps_logical_executable_to_actual_native_image(self):
        value = self.context()
        self.assertEqual(value, {"kind": "venv", "virtual_executable": str(self.venv / "Scripts/python.exe"),
            "native_executable": str(self.base_executable), "prefix": str(self.venv), "base_prefix": str(self.base)})
        self.assertNotEqual(value["virtual_executable"], value["native_executable"])

    def test_bundle_keeps_one_native_and_logical_executable(self):
        image = self.bundle / "python.exe"
        value = self.context(virtual=image, prefix=self.bundle, base=self.bundle, native=image, base_executable=image)
        self.assertEqual(value["kind"], "bundled")
        self.assertEqual(value["virtual_executable"], value["native_executable"])

    def test_virtual_python_outside_box_profile_is_refused(self):
        with self.assertRaisesRegex(ValueError, "validated .venv"):
            self.context(virtual=self.base_executable)

    def test_native_image_must_match_actual_base_and_prefix(self):
        for kwargs in ({"base_executable": self.bundle / "python.exe"}, {"base": self.bundle}, {"exec_prefix": self.bundle}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.context(**kwargs)

    def test_config_home_and_executable_must_match_base(self):
        for updates in ({"home": str(self.bundle)}, {"executable": str(self.bundle / "python.exe")}):
            with self.subTest(updates=updates):
                self.config(**updates)
                with self.assertRaisesRegex(ValueError, "configuration differs"):
                    self.context()
                self.config()

    def test_system_site_packages_and_duplicate_config_are_refused(self):
        self.config(**{"include-system-site-packages": "true"})
        with self.assertRaisesRegex(ValueError, "isolation profile"):
            self.context()
        self.config()
        with (self.venv / "pyvenv.cfg").open("a", encoding="utf8") as output:
            output.write("home = " + str(self.base) + "\n")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.context()

    def test_missing_profile_or_native_image_is_refused(self):
        (self.venv / "pyvenv.cfg").unlink()
        with self.assertRaises(OSError): self.context()
        self.config(); self.base_executable.unlink()
        with self.assertRaises(OSError): self.context()

    def test_venv_status_requires_context_plus_exact_native_identity(self):
        context = self.context()
        identity = self.identity()
        record = {"schema": 1, "root": str(self.root), "state": "running", "identity": identity, "interpreter": context}
        with patch.object(host, "ROOT", self.root), patch.object(host, "host_interpreter", return_value=context), \
             patch.object(host, "process_identity", return_value=self.identity(pid=999)), patch.object(host, "same_process", return_value=True):
            self.assertTrue(host.verified_record(record))
            self.assertFalse(host.verified_record({k:v for k,v in record.items() if k != "interpreter"}))
            self.assertFalse(host.verified_record({**record, "interpreter": {**context, "prefix": str(self.base)}}))
            self.assertFalse(host.verified_record({**record, "identity": {**identity, "executable": context["virtual_executable"]}}))
            self.assertFalse(host.verified_record({**record, "identity": {**identity, "owner_sid": "other-owner"}}))
        with patch.object(host, "ROOT", self.root), patch.object(host, "host_interpreter", return_value=context), \
             patch.object(host, "process_identity", return_value=self.identity(pid=999)), patch.object(host, "same_process", return_value=False):
            self.assertFalse(host.verified_record(record))

    def test_old_bundled_schema1_records_remain_compatible(self):
        image = self.bundle / "python.exe"
        context = self.context(virtual=image, prefix=self.bundle, base=self.bundle, native=image, base_executable=image)
        record = {"schema": 1, "root": str(self.root), "state": "running", "identity": self.identity(native=image)}
        with patch.object(host, "ROOT", self.root), patch.object(host, "host_interpreter", return_value=context), \
             patch.object(host, "process_identity", return_value=self.identity(native=image, pid=999)), \
             patch.object(host, "same_process", return_value=True):
            self.assertTrue(host.verified_record(record))
            self.assertFalse(host.verified_record({**record, "state": "failed"}))

    def test_clean_environment_never_keeps_inherited_startup_hint_or_secrets(self):
        with patch.object(host, "ROOT", self.root), patch.dict(os.environ, {"__PYVENV_LAUNCHER__": "hostile-parent-hint", "HTTP_PROXY": "private", "PYTHONPATH": "private", "SECRET_TOKEN": "private"}):
            value = host.clean_environment()
        self.assertFalse({"__PYVENV_LAUNCHER__", "HTTP_PROXY", "PYTHONPATH", "SECRET_TOKEN"} & value.keys())
        self.assertTrue(Path(value["TEMP"]).is_relative_to(self.root))

    def test_start_passes_native_executable_and_scoped_hint_without_app_start(self):
        context = self.context(); identity = self.identity()
        request = "a" * 48
        record = {"schema": 1, "root": str(self.root), "state": "running", "identity": identity,
                  "interpreter": context, "request_id": request, "job_free_preflight": True,
                  "job_free_ready": True, "url": "http://127.0.0.1:19080"}
        guard = Mock()
        with patch.object(host, "ROOT", self.root), patch.object(host, "host_interpreter", return_value=context), \
             patch.object(host, "status", return_value=None), patch.object(host, "InstanceLock", return_value=guard), \
             patch("ariadne.box_profiles.bootstrap_profiles"), patch("ariadne.config.load_settings", return_value=SimpleNamespace(data_dir=self.root / "data")), \
             patch.object(host.secrets, "token_hex", return_value=request), patch.object(host, "write_json"), \
             patch.object(host, "read_json", return_value=record), patch.object(host, "detach", return_value=(identity["pid"], "fixture")) as detach, \
             patch.object(host, "process_identity", return_value=identity), patch.object(host, "same_process", return_value=True), \
             patch.object(host, "job_free", return_value=True), patch("sys.stdout", io.StringIO()):
            result = host.start(no_browser=True)
        self.assertEqual(result, record)
        command, directory, environment = detach.call_args.args
        self.assertEqual(command[0], context["native_executable"])
        self.assertEqual(environment["__PYVENV_LAUNCHER__"], context["virtual_executable"])
        self.assertEqual(directory, str(self.root))


class ExplicitCimEnvironmentTests(unittest.TestCase):
    def test_cim_gets_unicode_child_environment_but_broker_has_no_venv_hint(self):
        environment = {"SystemRoot": os.environ["SystemRoot"], "PATH": "trusted-local-path", "__PYVENV_LAUNCHER__": r"C:\example-box\.venv\Scripts\python.exe"}
        response = SimpleNamespace(returncode=0, stdout="1234", stderr="")
        with patch.object(windows_host.subprocess, "run", return_value=response) as invoke:
            pid, method = windows_host.detach(["native-python.exe", "-I", "-B", "fixed-script.py"], "box", environment)
        self.assertEqual((pid, method), (1234, "wmi_same_user"))
        call = invoke.call_args
        payload = json.loads(call.kwargs["input"])
        self.assertIn("__PYVENV_LAUNCHER__=" + environment["__PYVENV_LAUNCHER__"], payload["environment"])
        self.assertIn("PATH=trusted-local-path", payload["environment"])
        self.assertNotIn("__PYVENV_LAUNCHER__", call.kwargs["env"])
        self.assertEqual(call.kwargs["env"]["PATH"], "trusted-local-path")
        self.assertIn("EnvironmentVariables=[string[]]$p.environment", call.args[0][-1])
        self.assertIn("0x01000608", call.args[0][-1])
        self.assertNotIn(r"C:\example-box", call.args[0][-1])

    def test_invalid_or_oversized_environment_never_invokes_broker(self):
        for value in ({"bad=key": "x"}, {"key": "bad\x00value"}, {"key": 1}, {"key": "x" * (1024**2 + 1)}):
            with self.subTest(value_type=type(next(iter(value.values()))).__name__), patch.object(windows_host.subprocess, "run") as invoke:
                with self.assertRaises(ValueError): windows_host.detach(["python.exe"], "box", value)
                invoke.assert_not_called()

    def test_broker_failure_does_not_fall_back_to_redirector_or_inherited_spawn(self):
        with patch.object(windows_host.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="private")) as invoke:
            with self.assertRaisesRegex(OSError, "denied"):
                windows_host.detach(["native.exe"], "box", {})
        self.assertEqual(invoke.call_count, 1)


if __name__ == "__main__": unittest.main()
