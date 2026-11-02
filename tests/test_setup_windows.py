"""First-run setup lifecycle tests: no live models, downloads, ports or services."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import provision, setup_windows as setup


class SetupWindowsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".tools-test-setup-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".venv/Scripts").mkdir(parents=True)
        (self.root / ".venv/Scripts/python.exe").write_bytes(b"synthetic-interpreter")
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(setup, "ROOT", self.root))
        self.stack.enter_context(patch.object(setup, "_platform_ok", return_value=True))
        self.stack.enter_context(patch.object(setup, "_python_ok", return_value=True))
        self.stack.enter_context(patch.object(setup, "_module_available", return_value=True))
        env = dict(os.environ)
        for name in ("ARIADNE_MODEL_PORT", "ARIADNE_OLLAMA_URL", "ARIADNE_DATA_DIR"):
            env.pop(name, None)
        self.stack.enter_context(patch.dict(os.environ, env, clear=True))

    def run_setup(self, args=()):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = setup.main(list(args))
        return code, output.getvalue(), errors.getvalue()

    def child(self, *, running=True):
        child = MagicMock(spec=subprocess.Popen)
        child.poll.return_value = None if running else 0
        child.wait.side_effect = lambda timeout: setattr(child.poll, "return_value", 0) or 0
        return child

    def test_check_has_no_network_process_database_or_write_effect(self):
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        with patch.object(setup, "_listener_present", side_effect=AssertionError("port probe")), \
             patch.object(provision, "main", side_effect=AssertionError("provision")), \
             patch.object(setup.socket, "socket", side_effect=AssertionError("socket")), \
             patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("database")), \
             patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
             patch.object(Path, "write_bytes", side_effect=AssertionError("file write")), \
             patch.object(Path, "write_text", side_effect=AssertionError("file write")):
            code, output, errors = self.run_setup(("--check",))
        state = json.loads(output)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(state["status"], "ready_for_setup")
        self.assertIn("runtime/ollama/ollama.exe", state["missing"])
        self.assertIn(".venv/Scripts/python.exe", state["existing"])
        self.assertFalse(state["private_installation_present"])
        self.assertFalse(state["downloads"])
        self.assertFalse(state["services_started"])
        self.assertEqual(before, sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")))
        self.assertFalse((self.root / "data").exists())

    def test_check_reports_missing_dependencies_without_installing(self):
        with patch.object(setup, "_module_available", return_value=False):
            code, output, _ = self.run_setup(("--check",))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["prerequisites_missing"], ["waitress", "pypdf"])
        self.assertEqual(json.loads(output)["status"], "prerequisites_missing")

    def test_check_reports_owner_markers_without_modifying_them(self):
        marker = self.root / "data/server.token"; marker.parent.mkdir()
        marker.write_bytes(b"private-test-sentinel")
        code, output, _ = self.run_setup(("--check",))
        state = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(state["private_installation_present"])
        self.assertEqual(state["owner_markers"], ["data/server.token"])
        self.assertEqual(state["status"], "existing_installation")
        self.assertEqual(marker.read_bytes(), b"private-test-sentinel")

    def test_success_uses_only_pinned_model_and_stops_new_child(self):
        child = self.child()
        def fake(argv, *, on_process, allow_owned):
            self.assertEqual(argv, ["--model", "qwen3.5:9b"])
            self.assertFalse(allow_owned)
            on_process(child)
        with patch.object(setup, "_listener_present", return_value=False), patch.object(provision, "main", side_effect=fake):
            code, output, errors = self.run_setup()
        self.assertEqual((code, errors), (0, ""))
        child.terminate.assert_called_once_with(); child.wait.assert_called_once_with(timeout=15)
        child.kill.assert_not_called()
        self.assertIn("Start Ariadne.cmd", output)
        self.assertNotIn("data/server.token", [p.relative_to(self.root).as_posix() for p in self.root.rglob("*")])

    def test_failure_stops_only_callback_child_and_preserves_error(self):
        child = self.child(); unrelated = self.child()
        def fake(argv, *, on_process, allow_owned):
            on_process(child)
            raise RuntimeError("synthetic failed download")
        with patch.object(setup, "_listener_present", return_value=False), patch.object(provision, "main", side_effect=fake):
            code, output, errors = self.run_setup()
        self.assertEqual(code, 1); self.assertEqual(output, "")
        self.assertIn("RuntimeError", errors)
        child.terminate.assert_called_once_with()
        unrelated.terminate.assert_not_called(); unrelated.kill.assert_not_called(); unrelated.wait.assert_not_called()

    def test_system_exit_and_keyboard_interrupt_also_clean_up(self):
        for error in (SystemExit("pin mismatch"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                child = self.child()
                def fake(argv, *, on_process, allow_owned):
                    on_process(child); raise error
                with patch.object(setup, "_listener_present", return_value=False), patch.object(provision, "main", side_effect=fake):
                    code, _, errors = self.run_setup()
                self.assertEqual(code, 1); self.assertIn("Ariadne setup:", errors)
                if isinstance(error, SystemExit): self.assertIn("pin mismatch", errors)
                child.terminate.assert_called_once_with()

    def test_already_exited_new_child_is_reaped_without_termination(self):
        child = self.child(running=False)
        with patch.object(setup, "_listener_present", return_value=False), \
             patch.object(provision, "main", side_effect=lambda argv, **kw: kw["on_process"](child)):
            code, _, _ = self.run_setup()
        self.assertEqual(code, 0); child.terminate.assert_not_called()
        child.wait.assert_called_once_with(timeout=15)

    def test_cleanup_timeout_kills_only_the_captured_child(self):
        child = self.child()
        def wait(timeout):
            if timeout == 15: raise subprocess.TimeoutExpired("synthetic", timeout)
            child.poll.return_value = 0
            return 0
        child.wait.side_effect = wait
        with patch.object(setup, "_listener_present", return_value=False), \
             patch.object(provision, "main", side_effect=lambda argv, **kw: kw["on_process"](child)):
            code, _, _ = self.run_setup()
        self.assertEqual(code, 0); child.kill.assert_called_once_with()
        self.assertEqual([c.kwargs["timeout"] for c in child.wait.call_args_list], [15, 5])

    def test_cleanup_failure_cannot_claim_ready(self):
        child = self.child(); child.terminate.side_effect = OSError("synthetic")
        with patch.object(setup, "_listener_present", return_value=False), \
             patch.object(provision, "main", side_effect=lambda argv, **kw: kw["on_process"](child)):
            code, output, errors = self.run_setup()
        self.assertEqual((code, output), (1, ""))
        self.assertIn("cleanup could not be confirmed", errors)

    def test_no_captured_child_cannot_claim_ready(self):
        with patch.object(setup, "_listener_present", return_value=False), patch.object(provision, "main", return_value=None):
            code, _, errors = self.run_setup()
        self.assertEqual(code, 1); self.assertIn("ValueError", errors)

    def test_existing_owner_files_refuse_before_network_or_provision(self):
        for name in setup.OWNER_MARKERS:
            with self.subTest(marker=name):
                marker = self.root / name; marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_bytes(b"owner-sentinel")
                with patch.object(setup, "_listener_present", side_effect=AssertionError("probe")), \
                     patch.object(provision, "main", side_effect=AssertionError("provision")):
                    code, output, errors = self.run_setup()
                self.assertEqual((code, output), (1, "")); self.assertIn("Existing private installation", errors)
                self.assertEqual(marker.read_bytes(), b"owner-sentinel")
                marker.unlink()

    def test_busy_port_refuses_without_download_or_process_change(self):
        with patch.object(setup, "_listener_present", return_value=True), patch.object(provision, "main") as run:
            code, _, errors = self.run_setup()
        self.assertEqual(code, 1); self.assertIn("already in use", errors); run.assert_not_called()

    def test_unsupported_python_or_platform_never_provisions(self):
        for name in ("_platform_ok", "_python_ok"):
            with self.subTest(check=name), patch.object(setup, name, return_value=False), \
                 patch.object(provision, "main") as run:
                code, output, _ = self.run_setup(("--check",))
                self.assertEqual(code, 1); self.assertEqual(json.loads(output)["status"], "unsupported_platform_or_python")
                code, _, _ = self.run_setup()
                self.assertEqual(code, 1); run.assert_not_called()

    def test_unsafe_path_refuses_without_effect(self):
        with patch.object(setup, "_allowed_path", side_effect=ValueError("synthetic excluded/reparse path")), \
             patch.object(provision, "main") as run:
            code, _, errors = self.run_setup(("--check",))
        self.assertEqual(code, 1); self.assertIn("excluded/reparse", errors); run.assert_not_called()

    def test_alternate_model_or_data_environment_refuses_before_port_probe(self):
        for name, value in (("ARIADNE_MODEL_PORT", "11448"), ("ARIADNE_OLLAMA_URL", "http://127.0.0.1:11434"),
                            ("ARIADNE_DATA_DIR", str(self.root / "other-data"))):
            with self.subTest(name=name), patch.dict(os.environ, {name: value}), \
                 patch.object(setup, "_listener_present", side_effect=AssertionError("probe")), \
                 patch.object(provision, "main") as run:
                code, _, _ = self.run_setup()
            self.assertEqual(code, 1); run.assert_not_called()

    def test_missing_core_dependencies_refuse_without_download(self):
        with patch.object(setup, "_module_available", return_value=False), patch.object(provision, "main") as run:
            code, _, errors = self.run_setup()
        self.assertEqual(code, 1); self.assertIn("pinned server/document", errors); run.assert_not_called()


class SetupArchitectureTests(unittest.TestCase):
    def test_native_arm64_python_is_not_the_qualified_x64_profile(self):
        with patch.object(setup.sys, "version_info", (3, 13, 99)), \
             patch.object(setup.struct, "calcsize", return_value=8), \
             patch.object(setup.sysconfig, "get_platform", return_value="win-arm64"):
            self.assertFalse(setup._python_ok())

    def test_windows_amd64_python_is_the_qualified_profile(self):
        with patch.object(setup.sys, "version_info", (3, 13, 99)), \
             patch.object(setup.struct, "calcsize", return_value=8), \
             patch.object(setup.sysconfig, "get_platform", return_value="win-amd64"):
            self.assertTrue(setup._python_ok())


class ProvisionCallbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".tools-test-setup-api-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); (self.root / "data").mkdir()
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(provision, "ROOT", self.root))
        self.stack.enter_context(patch.object(provision, "download_runtime", return_value=self.root / "synthetic-ollama.exe"))
        self.stack.enter_context(patch.object(provision, "local_env", return_value={"OLLAMA_MODELS": str(self.root / "data/models")}))
        self.stack.enter_context(patch.object(provision.subprocess, "run"))
        self.stack.enter_context(patch.object(provision, "api", return_value={"models": [{"name": setup.MODEL, "digest": provision.MODEL_PINS[setup.MODEL]}]}))
        self.output = self.stack.enter_context(redirect_stdout(io.StringIO()))

    def test_existing_cli_keeps_default_adoption_and_no_callback(self):
        with patch.object(provision, "start_model_server") as start:
            provision.main(["--model", setup.MODEL])
        self.assertTrue(start.call_args.kwargs["allow_owned"])
        self.assertIsNone(start.call_args.kwargs["on_process"])

    def test_explicit_lifecycle_api_forwards_callback_and_no_adoption(self):
        callback = lambda child: None
        with patch.object(provision, "start_model_server") as start:
            provision.main(["--model", setup.MODEL], on_process=callback, allow_owned=False)
        self.assertFalse(start.call_args.kwargs["allow_owned"])
        self.assertIs(start.call_args.kwargs["on_process"], callback)


if __name__ == "__main__":
    unittest.main()
