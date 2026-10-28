"""MCP authority/transport fixtures: all files and synthetic servers are local."""
from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import importlib.metadata
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.mcp_tools import (MCPTools, canonical, parse_json, read_json, redact, validate_arguments,
                               validate_catalog, validate_schema, validate_url, write_json)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = {"type": "object", "properties": {"sku": {"type": "string", "maxLength": 20}}, "required": ["sku"], "additionalProperties": False}
TOOL = {"name": "stock_count", "description": "Return a synthetic stock count.", "inputSchema": SCHEMA,
        "annotations": {"readOnlyHint": True}}


def _installed():
    try:
        return importlib.metadata.version("fastmcp-slim") == "4.1.4"
    except importlib.metadata.PackageNotFoundError:
        return False


live = unittest.skipUnless(_installed(), "Optional pinned FastMCP client is not installed")


class LocalServer:
    def __init__(self, mode="modern"):
        self.state = {"tool": copy.deepcopy(TOOL), "calls": [], "behavior": "normal", "mode": mode, "identity": "one"}
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_DELETE(self):
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                try:
                    request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                    method = request["method"]
                    if "id" not in request:
                        self.send_response(202)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if state["behavior"] == "redirect":
                        self.send_response(307)
                        self.send_header("Location", "/different")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if method == "server/discover":
                        result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}, "instructions": state["identity"]}
                    elif method == "initialize":
                        result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "synthetic", "version": state["identity"]}}
                    elif method == "tools/list":
                        if state["behavior"] == "drip":
                            self.send_response(200)
                            self.send_header("Content-Type", "text/event-stream")
                            self.end_headers()
                            for _ in range(200):
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                                time.sleep(.2)
                            return
                        tools = [state["tool"]]
                        if state["behavior"] == "many":
                            tools = [{**state["tool"], "name": f"tool{i}"} for i in range(65)]
                        result = {"tools": tools}
                        if state["behavior"] == "pagination":
                            result["nextCursor"] = "next"
                    elif method == "tools/call":
                        state["calls"].append(request["params"])
                        text = self.headers.get("Authorization", "") if state["behavior"] == "secret" else "3"
                        if state["behavior"] == "huge":
                            text = "x" * 100_000
                        result = {"content": [{"type": "text", "text": text}]}
                        if state["behavior"] == "numeric_secret":
                            value = self.headers.get("Authorization", "").removeprefix("Bearer ")
                            result = {"content": [{"type": "text", "text": value}],
                                      "structuredContent": {"value": int(value), "boolean": True}}
                        if state["behavior"] == "tool_error":
                            result["isError"] = True
                    else:
                        self.send_error(400)
                        return
                    if state["mode"] == "modern":
                        result.update(resultType="complete", cacheScope="private", ttlMs=0)
                    output = canonical({"jsonrpc": "2.0", "id": request["id"], "result": result})
                    stream_result = state["behavior"] == "sse"
                    if stream_result:
                        output = b"event: message\ndata: " + output + b"\n\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream" if stream_result else "application/json")
                    self.send_header("Content-Length", str(len(output)))
                    self.end_headers()
                    self.wfile.write(output)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.http.server_port}/mcp"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(2)


class MCPTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "runtime" / "tmp" / "mcp-tests"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.settings = Settings(data_dir=Path(self.temp.name), api_token="test-only-owner-token")
        self.registry = MCPTools(self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, server=None, selected=True, read_only=False):
        config = {"version": 1, "servers": [{"id": "fixture", "url": server.url if server else "http://127.0.0.1:8765/mcp",
                   "mode": server.state["mode"] if server else "modern", "headers": {},
                   "identity": {}, "tools": [{"metadata": copy.deepcopy(TOOL), "read_only": read_only}] if selected else []}]}
        write_json(self.registry.path, config)
        return config

    def register(self, server, read_only=False, credentials=False):
        config = self.configure(server, selected=False)
        if credentials:
            config["servers"][0]["headers"] = {"Authorization": {"env": "ARIADNE_MCP_TEST_SECRET", "prefix": "Bearer "}}
            write_json(self.registry.path, config)
        snapshot = self.registry.discover("fixture")
        config["servers"][0].update(identity=snapshot["identity"], tools=[{"metadata": snapshot["tools"][0], "read_only": read_only}])
        write_json(self.registry.path, config)
        return self.registry.schemas()[0]["function"]["name"]

    def test_optional_empty_and_no_network_at_startup(self):
        self.assertEqual(self.registry.schemas(), [])
        self.assertEqual(self.registry.descriptions(), [])
        self.assertEqual(self.registry.status()["selected_tools"], 0)

    def test_owner_mutation_guard_prevents_concurrent_writer(self):
        with self.registry.mutation():
            other = MCPTools(self.settings)
            with self.assertRaises(ValueError):
                with other.mutation():
                    self.fail("Concurrent owner mutation acquired the same lock.")
        with self.registry.mutation():
            self.assertTrue((Path(self.temp.name) / "mcp-config.lock").is_file())

    def test_instance_lock_refuses_hardlink_before_first_write(self):
        from ariadne.locking import InstanceLock
        original = Path(self.temp.name) / "empty-owner-file"
        original.write_bytes(b"")
        lock_path = Path(self.temp.name) / "linked.lock"
        try:
            os.link(original, lock_path)
        except OSError:
            self.skipTest("Hard links are unavailable in this fixture.")
        lock = InstanceLock(lock_path)
        try:
            with self.assertRaises(ValueError):
                lock.acquire()
            self.assertEqual(original.read_bytes(), b"")
        finally:
            lock.release()

    @unittest.skipUnless(os.name == "nt", "Windows held-handle path regression")
    def test_instance_lock_checks_opened_handle_before_first_write(self):
        from ariadne.locking import InstanceLock
        expected = Path(self.temp.name) / "expected.lock"
        expected.write_bytes(b"existing")
        other = Path(self.temp.name) / "other-empty-fixture"
        other.write_bytes(b"")
        real_open = os.open

        def swapped(path, *args, **kwargs):
            # Simulate a final symlink swap, then restoration before path.lstat().
            return real_open(other if os.fspath(path) == str(expected) else path, *args, **kwargs)

        lock = InstanceLock(expected)
        try:
            with patch("ariadne.locking.os.open", side_effect=swapped), self.assertRaises(ValueError):
                lock.acquire()
            self.assertEqual(other.read_bytes(), b"")
        finally:
            lock.release()

    @unittest.skipUnless(os.name == "nt", "Windows directory replacement regression")
    def test_worker_temp_directory_cannot_be_replaced_during_child(self):
        from ariadne import worker
        temporary = Path(self.temp.name) / "child-temp"
        temporary.mkdir()
        replacement = Path(self.temp.name) / "would-be-junction-location"
        environment = worker._environment(None)
        for key in ("TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "XDG_CACHE_HOME", "LOCALAPPDATA", "APPDATA"):
            environment[key] = str(temporary)
        real_popen = subprocess.Popen
        denied = []

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            try:
                temporary.rename(replacement)
            except PermissionError:
                denied.append(True)
            return process

        with patch("ariadne.worker._environment", return_value=environment), patch("ariadne.worker.subprocess.Popen", side_effect=capture):
            worker.run_worker(ROOT / "ariadne" / "_mcp_worker.py", b"{}", timeout=10,
                              memory_bytes=384 * 1024 * 1024, cpu_seconds=10, max_output_bytes=1000)
        self.assertEqual(denied, [True])
        self.assertTrue(temporary.is_dir())
        self.assertFalse(replacement.exists())

    def test_policy_is_owner_only_and_names_bind_changes(self):
        config = self.configure()
        name = self.registry.schemas()[0]["function"]["name"]
        self.assertEqual(self.registry.policy(name, {"sku": "WIDGET"}), "approval")
        config["servers"][0]["tools"][0]["read_only"] = True
        write_json(self.registry.path, config)
        with self.assertRaises(ValueError):
            self.registry.policy(name, {"sku": "WIDGET"})
        new = self.registry.schemas()[0]["function"]["name"]
        self.assertEqual(self.registry.policy(new, {"sku": "WIDGET"}), "auto")
        self.assertNotEqual(name, new)

    def test_missing_runtime_is_not_an_uncertain_write(self):
        self.configure(read_only=False)
        name = self.registry.schemas()[0]["function"]["name"]
        with patch.object(self.registry, "status", return_value={"ready": False}):
            failed = self.registry.execute(name, {"sku": "WIDGET"})
        self.assertFalse(failed["ok"])
        self.assertFalse(failed["uncertain"])

    def test_urls_fail_closed(self):
        for url in ("http://localhost:8765/mcp", "https://127.0.0.1:8765/mcp", "http://127.0.0.2:8765/mcp",
                    "http://127.0.0.1/mcp", "http://127.0.0.1:8765/mcp?q=1", "http://user@127.0.0.1:8765/mcp",
                    "http://127.0.0.1:8765/mcp#x", "http://127.0.0.1:8765//evil", "E:\\server.py"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        self.assertEqual(validate_url("http://[::1]:8765/mcp"), "http://[::1]:8765/mcp")

    def test_invalid_schemas_and_arguments(self):
        for schema in ({"$ref": "http://host/schema"}, {"type": "object", "additionalProperties": True},
                       {"type": "string", "pattern": "(.+)+$"}, {"type": ["string", "number"]},
                       {"type": ["object", {}]}, {**SCHEMA, "required": ["unknown"]}):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                validate_schema(schema)
        for args in ({"sku": "x", "extra": 1}, {}, {"sku": True}, {"sku": "x" * 21}, {"sku": float("nan")}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_arguments(args, SCHEMA)
        numeric = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1}}, "required": ["n"]}
        for args in ({"n": True}, {"n": .5}, {"n": 0}):
            with self.assertRaises(ValueError):
                validate_arguments(args, numeric)

    def test_combined_schema_budget_rejected_before_owner_commit(self):
        config = self.configure()
        metadata = copy.deepcopy(TOOL)
        metadata["inputSchema"]["description"] = "x" * 2000
        metadata["inputSchema"]["properties"]["sku"]["description"] = "x" * 2000
        config["servers"][0]["tools"] = [{"metadata": {**metadata, "name": f"fixture{i}"}, "read_only": True} for i in range(16)]
        with self.assertRaises(ValueError):
            validate_catalog(config)
        # The previously committed one-tool configuration remains usable.
        self.assertEqual(len(self.registry.schemas()), 1)

    @live
    def test_internal_sdk_extensions_are_disabled(self):
        from ariadne._mcp_worker import _client_class
        from fastmcp.client.transports import StreamableHttpTransport
        with patch("fastmcp.client.client.build_internal_client_extensions", side_effect=AssertionError("Internal extensions must not be inspected")):
            client = _client_class()(StreamableHttpTransport("http://127.0.0.1:8765/mcp"),
                                     mode="2026-07-28", extensions=[])
            folded = client._build_extension_kwargs()
        self.assertNotIn("extensions", folded)
        self.assertNotIn("result_claims", folded)

    def test_configuration_rejects_unknown_stdio_and_hardlinks(self):
        config = self.configure()
        config["servers"][0]["command"] = "python"
        write_json(self.registry.path, config)
        with self.assertRaises(ValueError):
            self.registry.config()
        original = Path(self.temp.name) / "original.json"
        original.write_text('{"version":1,"servers":[]}', encoding="utf-8")
        self.registry.path.unlink()
        try:
            os.link(original, self.registry.path)
        except OSError:
            self.skipTest("Hard links are unavailable in this fixture.")
        with self.assertRaises(ValueError):
            self.registry.config()
        with self.assertRaises(ValueError):
            write_json(self.registry.path, {"version": 1, "servers": []})

    def test_duplicate_json_and_finite_numbers(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.assertRaises(ValueError):
                parse_json(raw)

    def test_credentials_binding_and_redaction(self):
        config = self.configure()
        config["servers"][0]["headers"] = {"Authorization": {"env": "ARIADNE_MCP_TEST_SECRET", "prefix": "Bearer "}}
        write_json(self.registry.path, config)
        name = self.registry.schemas()[0]["function"]["name"]
        with patch.dict(os.environ, {"ARIADNE_MCP_TEST_SECRET": "fixture-long-secret"}):
            first = self.registry.fingerprint_material(name)
            self.assertNotIn("fixture-long-secret", canonical(first).decode())
        with patch.dict(os.environ, {"ARIADNE_MCP_TEST_SECRET": "rotated-long-secret"}):
            self.assertNotEqual(first, self.registry.fingerprint_material(name))
        value = redact({"fixture-long-secret": "fixture-long-secret", "token": "other"}, ["fixture-long-secret"])
        self.assertNotIn("fixture-long-secret", canonical(value).decode())
        value = redact({"structured_content": {"value": 24681357, "decimal": 123.5, "boolean": True},
                        "text": "24681357"}, ["24681357", "123.5", "1"])
        self.assertEqual(value["structured_content"]["value"], "[redacted]")
        self.assertEqual(value["structured_content"]["decimal"], "[redacted]")
        self.assertIs(value["structured_content"]["boolean"], True)
        self.assertEqual(value["text"], "[redacted]")

    def test_config_symlink_and_e_drive_rejected(self):
        with self.assertRaises(ValueError):
            MCPTools(Settings(data_dir=Path("E:/forbidden")))
        original = Path(self.temp.name) / "original.json"
        original.write_text('{"version":1,"servers":[]}', encoding="utf-8")
        try:
            self.registry.path.symlink_to(original)
        except OSError:
            self.skipTest("Symlink creation is unavailable in this fixture.")
        with self.assertRaises(ValueError):
            self.registry.config()

    @live
    def test_live_modern_and_legacy(self):
        for mode in ("modern", "legacy"):
            with self.subTest(mode=mode), LocalServer(mode) as server:
                name = self.register(server, read_only=True)
                result = self.registry.execute(name, {"sku": "WIDGET"})
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["content"][0]["text"], "3")
                self.assertEqual(len(server.state["calls"]), 1)

    @live
    def test_live_successful_sse_results_in_both_protocol_modes(self):
        for mode in ("modern", "legacy"):
            with self.subTest(mode=mode), LocalServer(mode) as server:
                server.state["behavior"] = "sse"
                name = self.register(server, read_only=True)
                result = self.registry.execute(name, {"sku": "WIDGET"})
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["content"], [{"type": "text", "text": "3"}])
                self.assertEqual(len(server.state["calls"]), 1)

    @live
    def test_live_changed_metadata_and_identity_never_calls(self):
        with LocalServer() as server:
            name = self.register(server)
            server.state["tool"]["description"] = "Changed instructions"
            self.assertFalse(self.registry.execute(name, {"sku": "WIDGET"})["ok"])
            self.assertEqual(server.state["calls"], [])
            server.state["tool"] = copy.deepcopy(TOOL)
            server.state["identity"] = "two"
            self.assertFalse(self.registry.execute(name, {"sku": "WIDGET"})["ok"])
            self.assertEqual(server.state["calls"], [])

    @live
    def test_live_credential_redaction_and_error_no_retry(self):
        with LocalServer() as server, patch.dict(os.environ, {"ARIADNE_MCP_TEST_SECRET": "fixture-long-secret"}):
            name = self.register(server, credentials=True)
            server.state["behavior"] = "secret"
            result = self.registry.execute(name, {"sku": "WIDGET"})
            self.assertTrue(result["ok"], result)
            self.assertNotIn("fixture-long-secret", canonical(result).decode())
            server.state["behavior"] = "tool_error"
            failed = self.registry.execute(name, {"sku": "WIDGET"})
            self.assertFalse(failed["ok"])
            self.assertTrue(failed["uncertain"])
            self.assertEqual(len(server.state["calls"]), 2)

    @live
    def test_live_numeric_credential_redacted_from_text_and_structured_output(self):
        with LocalServer() as server, patch.dict(os.environ, {"ARIADNE_MCP_TEST_SECRET": "24681357"}):
            name = self.register(server, credentials=True)
            server.state["behavior"] = "numeric_secret"
            result = self.registry.execute(name, {"sku": "WIDGET"})
            self.assertTrue(result["ok"], result)
            self.assertNotIn("24681357", canonical(result).decode())
            self.assertIs(result["structured_content"]["boolean"], True)

    @live
    def test_live_redirect_pagination_and_tool_limit_rejected(self):
        with LocalServer() as server:
            self.configure(server, selected=False)
            for behavior in ("redirect", "pagination", "many"):
                server.state["behavior"] = behavior
                with self.subTest(behavior=behavior), self.assertRaises(ValueError):
                    self.registry.discover("fixture")
            self.assertEqual(server.state["calls"], [])

    @live
    def test_live_output_and_dripping_sse_bound(self):
        with LocalServer() as server:
            name = self.register(server)
            server.state["behavior"] = "huge"
            self.assertFalse(self.registry.execute(name, {"sku": "WIDGET"})["ok"])
            server.state["behavior"] = "drip"
            start = time.monotonic()
            with patch("ariadne.mcp_tools.DEADLINE", 5), self.assertRaises(ValueError):
                self.registry.discover("fixture")
            self.assertLess(time.monotonic() - start, 9)

    @live
    def test_cancel_callback_and_invalid_args_do_not_call(self):
        with LocalServer() as server:
            name = self.register(server)
            bad = self.registry.execute(name, {"sku": True})
            self.assertFalse(bad["ok"])
            self.assertFalse(bad["uncertain"])
            failed = self.registry.execute(name, {"sku": "WIDGET"}, cancelled=lambda: True)
            self.assertFalse(failed["ok"])
            self.assertEqual(server.state["calls"], [])

    @live
    def test_owner_discovery_then_hash_bound_registration(self):
        from scripts.provision_mcp import main
        with LocalServer() as server:
            self.configure(server, selected=False)
            base = ["--data-dir", self.temp.name]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(base + ["discover", "--server", "fixture"]), 0)
            self.assertEqual(self.registry.schemas(), [])
            path = Path(self.temp.name) / "mcp-discovery" / "fixture.json"
            reviewed = hashlib.sha256(path.read_bytes()).hexdigest()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(base + ["register", "--server", "fixture", "--tool", "stock_count",
                                             "--snapshot-sha256", "0" * 64, "--read-only"]), 1)
            self.assertEqual(self.registry.schemas(), [])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(base + ["register", "--server", "fixture", "--tool", "stock_count",
                                             "--snapshot-sha256", reviewed, "--read-only"]), 0)
            name = self.registry.schemas()[0]["function"]["name"]
            self.assertEqual(self.registry.policy(name, {"sku": "WIDGET"}), "auto")


if __name__ == "__main__":
    unittest.main()
