"""Safety boundaries for local tools; no model, Docker daemon, or internet needed."""

from __future__ import annotations

import json
import io
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ariadne.config import Settings, load_settings
from ariadne.tools import ToolError, ToolRegistry


class ToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".tools-test-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.settings = Settings(data_dir=self.data)
        self.tools = ToolRegistry(self.settings)

    def test_write_read_search_and_overwrite(self):
        result = self.tools.execute("file_write", {"path": "plans/hello.txt", "content": "Hello café\nTarget line"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["bytes"], len("Hello café\nTarget line".encode()))
        self.assertFalse(self.tools.execute("file_write", {"path": "plans/hello.txt", "content": "wrong"})["ok"])
        self.assertEqual(self.tools.execute("file_read", {"path": "plans/hello.txt"})["content"], "Hello café\nTarget line")
        self.assertEqual(self.tools.execute("workspace_search", {"query": "TARGET"})["matches"][0]["line"], 2)
        self.assertTrue(self.tools.execute("file_write", {"path": "plans/hello.txt", "content": "New", "overwrite": True})["ok"])
        self.assertEqual(self.tools.execute("file_read", {"path": "plans/hello.txt", "max_chars": 2}),
                         {"ok": True, "path": "plans/hello.txt", "content": "Ne", "truncated": True})
        self.assertEqual(list((self.settings.workspace / "plans").glob(".ariadne-*.tmp")), [])

    def test_path_escape_and_windows_ambiguities_are_rejected(self):
        invalid = ["../outside", "a/../../outside", "/outside", "C:\\outside", "C:outside", "\\\\server\\share",
                   "a:stream", "a/../b", "a\\..\\b", "a//b", "a/./b", "NUL", "con.txt", "COM1.py",
                   "LPT9", "name.", "name ", "a\x00b", "\\?\\C:\\outside", "a\nb", "a?b", "a|b"]
        for path in invalid:
            with self.subTest(path=repr(path)):
                self.assertFalse(self.tools.execute("file_write", {"path": path, "content": "blocked"})["ok"])
                self.assertFalse(self.tools.execute("file_read", {"path": path})["ok"])
        self.assertFalse((self.data / "outside").exists())

    def test_links_are_rejected_for_reads_writes_and_search(self):
        outside = self.data / "outside.txt"
        outside.write_text("outside secret", encoding="utf-8")
        link = self.settings.workspace / "linked.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("Creating symbolic links requires operating-system privileges.")
        self.assertFalse(self.tools.execute("file_read", {"path": "linked.txt"})["ok"])
        self.assertFalse(self.tools.execute("file_write", {"path": "linked.txt", "content": "damage", "overwrite": True})["ok"])
        self.assertEqual(self.tools.execute("workspace_search", {"query": "secret"})["matches"], [])
        self.assertEqual(self.tools.execute("file_list", {})["entries"], [])
        self.assertEqual(outside.read_text(), "outside secret")

    def test_linked_workspace_root_is_rejected(self):
        target = self.data / "target"
        target.mkdir()
        link = self.data / "workspace-link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symbolic links requires operating-system privileges.")
        with self.assertRaises(ToolError):
            ToolRegistry(Settings(data_dir=self.data, workspace=link))

    def test_hard_links_cannot_replace_outside_data(self):
        outside = self.data / "outside.txt"
        outside.write_text("untouched", encoding="utf-8")
        try:
            os.link(outside, self.settings.workspace / "hardlink.txt")
        except OSError:
            self.skipTest("Hard links are unavailable in this operating-system sandbox.")
        self.assertFalse(self.tools.execute("file_write", {"path": "hardlink.txt", "content": "wrong", "overwrite": True})["ok"])
        self.assertEqual(outside.read_text(), "untouched")

    def test_execute_validates_arguments_independently_of_schema(self):
        cases = [("file_read", {"path": "hello.txt", "max_chars": True}),
                 ("file_read", {"path": "hello.txt", "max_chars": 1_000_000}),
                 ("file_write", {"path": "hello.txt", "content": "x", "overwrite": "yes"}),
                 ("file_list", {"path": ".", "recursive": True}),
                 ("workspace_search", {"query": ""}),
                 ("calculate", {"expression": 4}), ("unknown", {}), ("file_list", [])]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                self.assertFalse(self.tools.execute(name, args)["ok"])

    def test_calculator_rejects_code_and_unbounded_work(self):
        self.assertEqual(self.tools.execute("calculate", {"expression": "(12 + 4) / 2 + 2**3"}), {"ok": True, "result": 16.0})
        invalid = ["__import__('os').getcwd()", "sum([1,2])", "(1).__class__", "True+1", "2**100000000",
                   "10**(10**100)", "'a'*100", "[x for x in range(100)]", "1/0", "1e999", "(-1)**0.5"]
        for expression in invalid:
            with self.subTest(expression=expression):
                self.assertFalse(self.tools.execute("calculate", {"expression": expression})["ok"])

    def test_docker_disabled_by_default_and_requires_approval(self):
        self.assertNotIn("run_python", [schema["function"]["name"] for schema in self.tools.schemas()])
        self.assertEqual(self.tools.policy("run_python", {}), "approval")
        self.assertFalse(self.tools.execute("run_python", {"path": "x.py"})["ok"])

    def test_docker_runs_only_a_readonly_snapshot_with_resource_limits(self):
        tools = ToolRegistry(Settings(data_dir=self.data, enable_docker=True))
        self.assertTrue(tools.execute("file_write", {"path": "main.py", "content": "print('ok')"})["ok"])
        commands = []

        class Process:
            returncode = 0

            def __init__(self, command, **kwargs):
                commands.append(command)
                mount = command[command.index("--mount") + 1]
                copied = Path(mount.split("source=", 1)[1].split(",target=", 1)[0])
                self.snapshot = copied
                self.stdout = io.BufferedReader(io.BytesIO(b"ok\n"))

            def poll(self):
                return 0

            def wait(self, timeout):
                return 0

        with patch("ariadne.tools.shutil.which", return_value="docker"), patch("ariadne.tools.subprocess.Popen", Process), patch("ariadne.tools.subprocess.run"):
            result = tools.execute("run_python", {"path": "main.py"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["output"], "ok\n")
        command = commands[0]
        for option in ["--read-only", "--pull=never", "--rm"]:
            self.assertIn(option, command)
        for option, value in [("--network", "none"), ("--memory", "256m"), ("--cpus", "1"),
                              ("--pids-limit", "64"), ("--user", "65534:65534"), ("--cap-drop", "ALL")]:
            self.assertEqual(command[command.index(option) + 1], value)
        mount = command[command.index("--mount") + 1]
        self.assertNotIn("source=" + str(self.settings.workspace) + ",", mount)
        self.assertTrue(mount.endswith(",readonly"))
        self.assertNotIn("docker.sock", " ".join(command))
        self.assertEqual(list(self.data.glob(".runner-*")), [])

    def test_settings_token_persists_and_step_cap_applies(self):
        with patch.dict(os.environ, {"ARIADNE_DATA_DIR": str(self.data), "ARIADNE_MAX_STEPS": "999"}, clear=True):
            first = load_settings()
            second = load_settings()
        self.assertEqual(first.api_token, second.api_token)
        self.assertGreaterEqual(len(first.api_token), 40)
        self.assertEqual(first.max_steps, 32)
        self.assertTrue(first.workspace.is_dir())
        self.assertFalse(first.enable_docker)

    def test_reserved_drive_is_rejected(self):
        with self.assertRaises(ValueError):
            Settings(data_dir=Path("E:/ariadne"))
        with self.assertRaises(ValueError):
            Settings(workspace=Path("e:/workspace"))
        with self.assertRaises(ValueError):
            Settings(whisper_model="E:/model.bin")
        with self.assertRaises(ValueError):
            Settings(piper_bin="e:/piper.exe")

    def test_config_rejects_linked_ancestors_before_creating_state(self):
        target = self.data / "linked-target"
        target.mkdir()
        link = self.data / "config-link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symbolic links requires operating-system privileges.")
        with patch.dict(os.environ, {"ARIADNE_DATA_DIR": str(link / "new-data")}, clear=True):
            with self.assertRaises(ValueError):
                load_settings()
        self.assertFalse((target / "new-data").exists())

    def test_token_rejects_hardlinks(self):
        token = self.data / "original-token"
        token.write_text("a" * 43, encoding="ascii")
        try:
            os.link(token, self.data / "server.token")
        except OSError:
            self.skipTest("Hard links are unavailable in this operating-system sandbox.")
        with patch.dict(os.environ, {"ARIADNE_DATA_DIR": str(self.data)}, clear=True):
            with self.assertRaises(ValueError):
                load_settings()


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".tools-test-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append({"method": "GET", "path": self.path})
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/destination")
                    self.end_headers()
                    return
                if self.path == "/slow":
                    self.send_response(200)
                    self.end_headers()
                    try:
                        for _ in range(30):
                            self.wfile.write(b" ")
                            self.wfile.flush()
                            time.sleep(0.1)
                    except OSError:
                        pass
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                value = {"stock": 42, "Authorization": self.headers.get("Authorization", ""),
                         "echo": "credential-value", "password": "should-not-leak"}
                self.wfile.write(json.dumps(value).encode())

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                requests.append({"method": "POST", "path": self.path, "body": json.loads(body)})
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"created":true}')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def registry(self, entries):
        config = self.data / "connectors.json"
        config.write_text(json.dumps({"connectors": entries}), encoding="utf-8")
        return ToolRegistry(Settings(data_dir=self.data, api_token="server-token"))

    def entry(self, **overrides):
        return {"id": "inventory", "description": "Read inventory", "url": self.base + "/inventory",
                "method": "GET", "read_only": True, **overrides}

    def test_readonly_fixed_get_and_proxy_bypass(self):
        tools = self.registry([self.entry()])
        self.assertEqual(tools.policy("connector_inventory", {}), "auto")
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            result = tools.execute("connector_inventory", {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["stock"], 42)
        self.assertFalse(tools.execute("connector_inventory", {"url": self.base + "/attack"})["ok"])
        self.assertEqual(self.requests, [{"method": "GET", "path": "/inventory"}])

    def test_redirect_is_never_followed(self):
        tools = self.registry([self.entry(url=self.base + "/redirect")])
        result = tools.execute("connector_inventory", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 302)
        self.assertEqual([item["path"] for item in self.requests], ["/redirect"])

    def test_slow_trickle_response_obeys_total_deadline(self):
        tools = self.registry([self.entry(url=self.base + "/slow", timeout_seconds=1)])
        start = time.monotonic()
        result = tools.execute("connector_inventory", {})
        elapsed = time.monotonic() - start
        self.assertFalse(result["ok"], result)
        self.assertLess(elapsed, 2.5)

    def test_write_requires_approval_and_validates_secret_input(self):
        tools = self.registry([self.entry(method="POST", read_only=False)])
        self.assertEqual(tools.policy("connector_inventory", {}), "approval")
        result = tools.execute("connector_inventory", {"body": {"task": "repair pump"}})
        self.assertTrue(result["ok"])
        self.assertEqual(self.requests[-1]["body"], {"task": "repair pump"})
        for body in [{"password": "bad"}, {"token": "server-token"}, [1], {"x": float("nan")}]:
            self.assertFalse(tools.execute("connector_inventory", {"body": body})["ok"])
        self.assertEqual(len(self.requests), 1)

    def test_environment_credentials_and_response_redaction(self):
        tools = self.registry([self.entry(headers={"Authorization": {"env": "TEST_CONNECTOR_AUTH"}})])
        with patch.dict(os.environ, {"TEST_CONNECTOR_AUTH": "Bearer credential-value"}):
            result = tools.execute("connector_inventory", {})
        serialized = json.dumps(result)
        self.assertNotIn("credential-value", serialized)
        self.assertNotIn("should-not-leak", serialized)
        self.assertIn("[redacted]", serialized)
        self.assertNotIn("TEST_CONNECTOR_AUTH", json.dumps(tools.list_connectors()))
        self.assertNotIn("127.0.0.1", json.dumps(tools.schemas()))

    def test_invalid_configuration_is_rejected(self):
        cases = [self.entry(url="file:///etc/passwd"), self.entry(url="http://user:secret@localhost/"),
                 self.entry(url=self.base + "/{query}"), self.entry(url=self.base + "/?api_key=secret"),
                 self.entry(method="DELETE"), self.entry(method="POST", read_only=True),
                 self.entry(headers={"Authorization": "Bearer secret"}),
                 self.entry(headers={"Host": "other.local"}), self.entry(headers={"X-Test": "bad\r\nheader"})]
        for entry in cases:
            with self.subTest(entry=entry):
                with self.assertRaises(ToolError):
                    self.registry([entry])

    def test_unconfigured_get_requires_approval(self):
        entry = self.entry()
        del entry["read_only"]
        tools = self.registry([entry])
        self.assertEqual(tools.policy("connector_inventory", {}), "approval")


if __name__ == "__main__":
    unittest.main()
