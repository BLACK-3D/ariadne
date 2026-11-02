"""Native Windows launcher boundaries, using synthetic hosts only.

No actual Ariadne host, model, microphone, service or network request is started.
Every generated path and shortened-deadline fault injection stays under staging.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).absolute().parents[1]
FIXTURES = ROOT / "staging/startup_fix_2026_11_02/unit-fixtures"
WRAPPERS = ("Start Ariadne.cmd", "Stop Ariadne.cmd", "start.ps1", "scripts/stop_host.ps1", "scripts/host_process.ps1")


@unittest.skipUnless(os.name == "nt", "Native Windows PowerShell entrypoints")
class StartupEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        launcher = shutil.which("py.exe")
        if not launcher:
            raise unittest.SkipTest("Full native Python 3.13 is required for isolated no-pip venv fixtures")
        result = subprocess.run([launcher, "-3.13", "-I", "-B", "-X", "utf8", "-c", "import sys;print(sys.executable)"], capture_output=True, timeout=15)
        if result.returncode:
            raise unittest.SkipTest("Native Python 3.13 is unavailable")
        cls.base_python = result.stdout.decode("utf-8").strip()
        cls.powershell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        FIXTURES.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="entrypoint-", dir=FIXTURES)
        self.area = Path(self.temp.name)
        self.box = self.area / "Synthetic friend box \u03a9"
        (self.box / "scripts").mkdir(parents=True)
        (self.area / "tmp").mkdir()
        for name in WRAPPERS:
            (self.box / name).write_bytes((ROOT / name).read_bytes())
        self.env = dict(os.environ)
        for name in tuple(self.env):
            if name.startswith(("ARIADNE_", "OLLAMA_", "PYTHON")):
                self.env.pop(name, None)
        self.env.update(TEMP=str(self.area / "tmp"), TMP=str(self.area / "tmp"), PYTHONDONTWRITEBYTECODE="1")
        self.junctions = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for junction in self.junctions:
            if junction.is_junction():
                os.rmdir(junction)  # Remove the junction itself, never its target.
        if self.area.exists():
            if not self.area.resolve().is_relative_to(FIXTURES.resolve()):
                raise AssertionError("Fixture cleanup escaped its private staging root")
        self.temp.cleanup()

    def runtime(self, code="raise SystemExit(0)\n"):
        result = subprocess.run([self.base_python, "-I", "-B", "-m", "venv", "--copies", "--without-pip", str(self.box / ".venv")], cwd=self.box, env=self.env, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        (self.box / "scripts/host.py").write_text(code, encoding="utf-8")

    def invoke(self, *, stop=False, cmd=False, env=None):
        target = self.box / ("Stop Ariadne.cmd" if stop and cmd else "Start Ariadne.cmd" if cmd else "scripts/stop_host.ps1" if stop else "start.ps1")
        command = 'cmd.exe /d /s /c ""' + str(target) + '""' if cmd else [self.powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(target)]
        return subprocess.run(command, cwd=self.area, env=env or self.env, input=b"\n", capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)

    def summaries(self):
        return [json.loads(path.read_bytes()) for path in sorted((self.box / "data/host").glob("launcher-*.json"))]

    def summary(self):
        values = self.summaries()
        self.assertEqual(len(values), 1)
        return values[0]

    def assert_safe_summary(self, summary):
        text = json.dumps(summary)
        for forbidden in (str(ROOT), str(self.area), "SYNTHETIC_PRIVATE_DETAIL", "SYNTHETIC_CREDENTIAL"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(summary["schema"], 1)
        self.assertEqual(summary["retries"], 0)
        self.assertFalse(summary["process_terminated"])
        self.assertEqual(summary["timeout_seconds"], 145)
        self.assertEqual(len(summary["attempt_id"]), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in summary["attempt_id"]))

    def test_nonzero_start_is_preserved_and_early_stderr_has_private_log(self):
        self.runtime('import sys\nprint("SYNTHETIC_PRIVATE_DETAIL: before any host imports",file=sys.stderr)\nraise SystemExit(23)\n')
        result = self.invoke()
        self.assertEqual(result.returncode, 23)
        self.assertIn(b"SYNTHETIC_PRIVATE_DETAIL", result.stderr)
        summary = self.summary(); self.assert_safe_summary(summary)
        self.assertEqual((summary["stage"], summary["failure_type"], summary["exit_code"]), ("python_exit", "ProcessExit", 23))
        log = next((self.box / "data/host").glob("launcher-*.log")).read_bytes()
        self.assertIn(b"PRIVATE", log); self.assertIn(b"SYNTHETIC_PRIVATE_DETAIL", log)

    def test_cmd_failure_pauses_and_returns_original_code_from_foreign_cwd(self):
        self.runtime('import sys\nprint("SYNTHETIC FAILURE",file=sys.stderr)\nraise SystemExit(23)\n')
        result = self.invoke(cmd=True)
        self.assertEqual(result.returncode, 23)
        self.assertIn(b"Press any key", result.stdout)
        self.assertIn(b"Ariadne could not start", result.stdout)
        self.assertEqual(self.summary()["exit_code"], 23)

    def test_success_has_no_pause_and_preserves_explicit_headless_flag(self):
        self.runtime('import json,sys\nprint(json.dumps({"args":sys.argv[1:]}))\n')
        env = {**self.env, "ARIADNE_NO_BROWSER": "1"}
        result = self.invoke(cmd=True, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(b"Press any key", result.stdout)
        self.assertEqual(json.loads(result.stdout)["args"], ["start", "--no-browser"])
        summary = self.summary(); self.assert_safe_summary(summary)
        self.assertEqual(summary["status"], "completed"); self.assertTrue(summary["no_browser"])

    def test_stop_failure_keeps_action_code_and_cmd_pause(self):
        self.runtime('import json,sys\nprint(json.dumps({"args":sys.argv[1:]}))\nraise SystemExit(31)\n')
        result = self.invoke(stop=True, cmd=True)
        self.assertEqual(result.returncode, 31)
        self.assertIn(b'"stop"', result.stdout)
        self.assertIn(b"Press any key", result.stdout)
        summary = self.summary(); self.assert_safe_summary(summary)
        self.assertEqual((summary["action"], summary["exit_code"]), ("stop", 31))

    def test_missing_runtime_is_visible_and_has_static_failure_summary(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"Setup Ariadne.cmd", result.stderr)
        summary = self.summary(); self.assert_safe_summary(summary)
        self.assertEqual((summary["stage"], summary["failure_type"], summary["interpreter"]), ("runtime", "RuntimeMissing", "missing"))
        self.assertFalse((self.box / ".venv").exists())

    def test_actual_import_exception_is_captured_before_application_logging(self):
        self.runtime('raise ModuleNotFoundError("SYNTHETIC_PRIVATE_DETAIL")\n')
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"ModuleNotFoundError", result.stderr)
        self.assert_safe_summary(self.summary())
        self.assertFalse((self.box / "data/host/host.log").exists())

    def test_both_oversized_pipes_are_drained_with_bounded_retention_and_exact_counts(self):
        self.runtime('import sys\nfor _ in range(40):\n sys.stdout.write("O"*4096);sys.stdout.flush();sys.stderr.write("E"*4096);sys.stderr.flush()\nraise SystemExit(7)\n')
        result = self.invoke()
        self.assertEqual(result.returncode, 7)
        summary = self.summary(); self.assert_safe_summary(summary)
        for stream in ("stdout", "stderr"):
            self.assertEqual(summary[stream + "_total_characters"], 40 * 4096)
            self.assertEqual(summary[stream + "_characters"], 16384)
            self.assertTrue(summary[stream + "_truncated"])
            self.assertTrue(summary[stream + "_complete"])
        self.assertLess(next((self.box / "data/host").glob("launcher-*.log")).stat().st_size, 100000)

    def test_private_log_failure_never_erases_original_python_exit(self):
        self.runtime('import sys\nprint("SYNTHETIC_PRIVATE_DETAIL",file=sys.stderr)\nraise SystemExit(23)\n')
        bridge = self.box / "scripts/host_process.ps1"
        text = bridge.read_text(encoding="utf-8")
        line = next(line for line in text.splitlines() if "$taskLogStream = [IO.FileStream]::new" in line)
        bridge.write_text(text.replace(line, "            throw [IO.IOException]::new('Synthetic diagnostic-storage failure')", 1), encoding="utf-8")
        result = self.invoke()
        self.assertEqual(result.returncode, 23)
        self.assertIn(b"storage is unavailable", result.stderr)
        self.assertIn(b"SYNTHETIC_PRIVATE_DETAIL", result.stderr)
        self.assertEqual(self.summaries(), [])

    def junction(self, path, target):
        command = 'cmd.exe /d /s /c mklink /J "' + str(path) + '" "' + str(target) + '"'
        result = subprocess.run(command, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.junctions.append(path)

    def test_linked_data_directory_is_rejected_before_child_or_outside_write(self):
        self.runtime('from pathlib import Path\nPath("executed.txt").write_text("unsafe")\n')
        target = self.area / "guard target"; target.mkdir(); sentinel = target / "sentinel.txt"; sentinel.write_bytes(b"UNCHANGED")
        self.junction(self.box / "data", target)
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"Unsafe launcher path", result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"UNCHANGED")
        self.assertFalse((target / "host").exists()); self.assertFalse((self.box / "executed.txt").exists())

    def test_hardlinked_interpreter_is_rejected_before_execution(self):
        self.runtime('from pathlib import Path\nPath("executed.txt").write_text("unsafe")\n')
        interpreter = self.box / ".venv/Scripts/python.exe"; alias = self.area / "interpreter-alias.exe"; os.link(interpreter, alias)
        before = hashlib.sha256(alias.read_bytes()).hexdigest()
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.summary()["failure_type"], "UnsafePath")
        self.assertFalse((self.box / "executed.txt").exists())
        self.assertEqual(hashlib.sha256(alias.read_bytes()).hexdigest(), before)

    def test_receipt_collision_cannot_replace_foreign_hardlinked_payload(self):
        self.runtime('raise SystemExit(23)\n')
        attempt = "4" * 32
        bridge = self.box / "scripts/host_process.ps1"; text = bridge.read_text(encoding="utf-8")
        bridge.write_text(text.replace("[Guid]::NewGuid().ToString('N')", "'" + attempt + "'", 1), encoding="utf-8")
        host = self.box / "data/host"; host.mkdir(parents=True)
        protected = self.area / "protected.json"; protected.write_bytes(b"ORIGINAL_SYNTHETIC_PAYLOAD")
        os.link(protected, host / ("launcher-" + attempt + ".json"))
        result = self.invoke()
        self.assertEqual(result.returncode, 23)
        self.assertIn(b"diagnostic publication failed", result.stderr)
        self.assertEqual(protected.read_bytes(), b"ORIGINAL_SYNTHETIC_PAYLOAD")

    def test_shortened_fixture_deadline_is_pending_and_never_kills_child(self):
        self.runtime('import time\nfrom pathlib import Path\ntime.sleep(.7)\nPath("natural-exit.txt").write_text("finished without termination")\n')
        bridge = self.box / "scripts/host_process.ps1"; text = bridge.read_text(encoding="utf-8")
        self.assertIn("145000", text)
        bridge.write_text(text.replace("145000", "100"), encoding="utf-8")
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        summary = self.summary()
        self.assertEqual((summary["status"], summary["failure_type"]), ("pending", "Timeout"))
        self.assertFalse(summary["process_terminated"])
        deadline = time.monotonic() + 5
        while not (self.box / "natural-exit.txt").exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue((self.box / "natural-exit.txt").exists(), "Synthetic child did not finish naturally")

    def test_common_credential_labels_are_redacted_from_private_retention(self):
        self.runtime('import sys\nprint("api_key=SYNTHETIC_CREDENTIAL password=SYNTHETIC_CREDENTIAL Authorization: Bearer SYNTHETIC_CREDENTIAL",file=sys.stderr)\nraise SystemExit(9)\n')
        result = self.invoke()
        self.assertEqual(result.returncode, 9)
        self.assertNotIn(b"SYNTHETIC_CREDENTIAL", result.stderr)
        self.assertIn(b"[redacted]", result.stderr)
        for log in (self.box / "data/host").glob("launcher-*.log"):
            self.assertNotIn(b"SYNTHETIC_CREDENTIAL", log.read_bytes())
        self.assert_safe_summary(self.summary())


if __name__ == "__main__":
    unittest.main()