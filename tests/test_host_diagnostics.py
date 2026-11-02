"""Startup failure evidence does not leak the failing input or external paths."""
import json
import io
from contextlib import ExitStack
import os
import tempfile
from unittest.mock import Mock, patch
from pathlib import Path
import unittest

from scripts.host_diagnostics import failure_detail
from scripts import host

ROOT = Path(__file__).absolute().parents[1]


class HostDiagnosticTests(unittest.TestCase):
    def error(self, filename, expression="raise OSError(5, 'PRIVATE_TOKEN_secret')"):
        try:
            exec(compile(expression, str(filename), "exec"), {})
        except BaseException as error:
            return error
        self.fail("Fixture did not raise")

    def test_application_frame_retained_without_source_or_exception_text(self):
        result = failure_detail(self.error(ROOT / "scripts/launch.py"), "application", root=ROOT)
        self.assertEqual(result["stage"], "application")
        self.assertEqual(result["errno"], 5)
        self.assertEqual(result["error_type"], "OSError")
        self.assertEqual(result["application_frames"], [{"file": "scripts/launch.py", "line": 1, "function": "<frame>"}])
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(result))
        self.assertNotIn(str(ROOT), json.dumps(result))

    def test_external_frames_excluded(self):
        result = failure_detail(self.error(ROOT.parent / "private-user-path.py"), "request", root=ROOT)
        self.assertEqual(result["application_frames"], [])
        self.assertNotIn("private-user", json.dumps(result))

    def test_non_source_and_non_ascii_paths_excluded(self):
        for path in (ROOT / "scripts/secret.txt", ROOT / "data/private.py", ROOT / "scripts/pr\u00efvate.py"):
            with self.subTest(path=path):
                self.assertEqual(failure_detail(self.error(path), "application", root=ROOT)["application_frames"], [])

    def test_unknown_stage_replaced_not_echoed(self):
        result = failure_detail(ValueError("SECRET"), "SECRET", root=ROOT)
        self.assertEqual(result["stage"], "unknown")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_numeric_codes_bounded_and_boolean_rejected(self):
        error = OSError()
        error.errno = True
        error.winerror = 2**40
        result = failure_detail(error, "identity", root=ROOT)
        self.assertNotIn("errno", result)
        self.assertNotIn("winerror", result)
        error.errno = 5
        error.winerror = 87
        result = failure_detail(error, "identity", root=ROOT)
        self.assertEqual((result["errno"], result["winerror"]), (5, 87))

    def test_message_is_never_formatted(self):
        class PrivateError(Exception):
            def __str__(self):
                raise AssertionError("Do not stringify")
        result = failure_detail(PrivateError("SECRET"), "log", root=ROOT)
        self.assertEqual(result["error_type"], "Exception")

    def test_custom_class_name_and_properties_are_not_inspected(self):
        def forbidden(self):
            raise AssertionError("Private attribute accessed")
        error_class = type("PRIVATE_TOKEN_identifier", (OSError,),
                           {"errno": property(forbidden), "winerror": property(forbidden)})
        result = failure_detail(error_class(), "identity", root=ROOT)
        self.assertEqual(result["error_type"], "Exception")
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(result))
        self.assertNotIn("errno", result)

    def test_non_string_stage_is_unknown(self):
        self.assertEqual(failure_detail(ValueError(), [], root=ROOT)["stage"], "unknown")

    def test_chained_exception_is_not_inspected(self):
        error = ValueError("public")
        error.__cause__ = RuntimeError("PRIVATE_TOKEN")
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(failure_detail(error, "cleanup", root=ROOT)))

    def test_trace_scan_is_bounded(self):
        expression = "def f(n):\n if n: return f(n-1)\n raise ValueError('SECRET')\nf(100)"
        result = failure_detail(self.error(ROOT / "scripts/launch.py", expression), "application", root=ROOT)
        self.assertEqual(len(result["application_frames"]), 8)
        self.assertTrue(result["trace_scan_truncated"])


class HostRunDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="host-diagnostics-", dir=ROOT / "staging")
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(host, "ROOT", self.root)
        self.root_patch.start()
        self.base = host.host_directory()
        self.request = "a" * 48
        self.status = self.base / "status.json"
        self.sentinel = b'{"state":"owner-status-sentinel"}\n'
        self.status.write_bytes(self.sentinel)

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def request_file(self, environment):
        host.write_json(self.base / ("start-" + self.request + ".json"),
            {"schema": 1, "request_id": self.request, "environment": environment})

    def failure(self):
        return json.loads((self.base / ("failure-" + self.request + ".json")).read_text())["failure"]

    def test_missing_request_preserves_shared_status_and_records_stage(self):
        with self.assertRaises(FileNotFoundError):
            host.run(self.request)
        self.assertEqual(self.status.read_bytes(), self.sentinel)
        self.assertEqual(self.failure()["stage"], "request")
        self.assertEqual(self.failure()["error_type"], "FileNotFoundError")

    def test_existing_failure_is_preserved(self):
        path = self.base / ("failure-" + self.request + ".json")
        path.write_bytes(b'{"sentinel":true}\n')
        with patch("sys.stderr", io.StringIO()), self.assertRaises(FileNotFoundError):
            host.run(self.request)
        self.assertEqual(path.read_bytes(), b'{"sentinel":true}\n')
        self.assertEqual(self.status.read_bytes(), self.sentinel)

    def test_environment_failure_has_no_secret_or_shared_status_write(self):
        self.request_file({"PRIVATE_TOKEN": "SECRET_VALUE"})
        with self.assertRaises(ValueError):
            host.run(self.request)
        self.assertEqual(self.failure()["stage"], "environment")
        self.assertNotIn("SECRET_VALUE", json.dumps(self.failure()))
        self.assertEqual(self.status.read_bytes(), self.sentinel)

    def test_lock_refusal_does_not_replace_other_host_status(self):
        self.request_file({})
        lock = Mock()
        lock.acquire.side_effect = ValueError("PRIVATE_TOKEN_lock")
        with patch.dict(os.environ), patch.object(host.os, "chdir"), patch.object(host, "InstanceLock", return_value=lock):
            with self.assertRaises(ValueError):
                host.run(self.request)
        lock.release.assert_not_called()
        self.assertEqual(self.failure()["stage"], "lock")
        self.assertEqual(self.status.read_bytes(), self.sentinel)

    def test_identity_failure_releases_owned_lock_and_never_starts_services(self):
        self.request_file({})
        lock = Mock()
        with patch.dict(os.environ), patch.object(host.os, "chdir"), patch.object(host, "InstanceLock", return_value=lock), \
                patch.object(host, "process_identity", side_effect=OSError(5, "PRIVATE_TOKEN")), \
                patch.object(host, "StopEvent") as event, patch.object(host, "ServiceJob") as services:
            host.run(self.request)
        lock.release.assert_called_once()
        event.assert_not_called()
        services.assert_not_called()
        record = json.loads(self.status.read_text())
        self.assertEqual(record["state"], "failed")
        self.assertEqual(record["failure"]["stage"], "identity")
        self.assertEqual(record["failure"]["errno"], 5)
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(record))

    def test_application_failure_retains_structural_detail_without_message(self):
        self.request_file({})
        lock, service = Mock(), Mock()
        identity = {"pid": 123, "executable": "fixture-python.exe", "created_filetime": 123, "owner_sid": "fixture"}
        with patch.dict(os.environ), patch.object(host.os, "chdir"), patch.object(host, "InstanceLock", return_value=lock), \
                patch.object(host, "process_identity", return_value=identity), patch.object(host, "host_interpreter", return_value={}), \
                patch.object(host, "job_free", return_value=True), patch.object(host, "StopEvent"), \
                patch.object(host, "ServiceJob", return_value=service), patch.object(host, "watch_for_stop"), \
                patch("scripts.launch.main", side_effect=ValueError("PRIVATE_TOKEN_application")), patch("sys.argv", ["fixture"]):
            host.run(self.request)
        record = json.loads(self.status.read_text())
        self.assertEqual(record["state"], "failed")
        self.assertEqual(record["failure"]["stage"], "application")
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(record))
        service.close.assert_called_once()
        lock.release.assert_called_once()


    def test_initialization_errors_always_release_the_acquired_lock(self):
        self.request_file({})
        for target in ("threading.Event", "secrets.token_hex"):
            with self.subTest(target=target):
                lock = Mock()
                with patch.dict(os.environ), patch.object(host.os, "chdir"), patch.object(host, "InstanceLock", return_value=lock), \
                        patch("scripts.host." + target, side_effect=RuntimeError("PRIVATE_TOKEN_init")):
                    with self.assertRaises(RuntimeError):
                        host.run(self.request)
                lock.release.assert_called_once()
                self.assertEqual(self.status.read_bytes(), self.sentinel)
                self.assertNotIn("PRIVATE_TOKEN", json.dumps(self.failure()))
                self.assertEqual(self.failure()["stage"], "initialization")
                (self.base / ("failure-" + self.request + ".json")).unlink()

    def cleanup_fixture(self, *, start_error=None, join_error=None, event_error=None, service_error=None):
        self.request_file({})
        lock, event, service, watcher = Mock(), Mock(), Mock(), Mock()
        watcher.start.side_effect = start_error
        watcher.join.side_effect = join_error
        event.close.side_effect = event_error
        service.close.side_effect = service_error
        identity = {"pid": 123, "executable": "fixture-python.exe", "created_filetime": 123, "owner_sid": "fixture"}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ))
            stack.enter_context(patch.object(host.os, "chdir"))
            stack.enter_context(patch.object(host, "InstanceLock", return_value=lock))
            stack.enter_context(patch.object(host, "process_identity", return_value=identity))
            stack.enter_context(patch.object(host, "host_interpreter", return_value={}))
            stack.enter_context(patch.object(host, "job_free", return_value=True))
            stack.enter_context(patch.object(host, "StopEvent", return_value=event))
            stack.enter_context(patch.object(host, "ServiceJob", return_value=service))
            stack.enter_context(patch.object(host.threading, "Thread", return_value=watcher))
            launch = stack.enter_context(patch("scripts.launch.main", side_effect=ValueError("PRIVATE_TOKEN_app")))
            stack.enter_context(patch("sys.argv", ["fixture"]))
            stack.enter_context(patch("sys.stderr", io.StringIO()))
            host.run(self.request)
        return json.loads(self.status.read_text()), lock, event, service, watcher, launch

    def test_watcher_start_failure_does_not_join_or_skip_resource_cleanup(self):
        record, lock, event, service, watcher, launch = self.cleanup_fixture(start_error=RuntimeError("PRIVATE_TOKEN_start"))
        watcher.join.assert_not_called()
        launch.assert_not_called()
        event.close.assert_called_once()
        service.close.assert_called_once()
        lock.release.assert_called_once()
        self.assertEqual(record["failure"]["stage"], "stop_watcher")
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(record))

    def test_watcher_join_failure_still_closes_event_services_and_lock(self):
        record, lock, event, service, watcher, _ = self.cleanup_fixture(join_error=RuntimeError("PRIVATE_TOKEN_join"))
        event.close.assert_called_once()
        service.close.assert_called_once()
        lock.release.assert_called_once()
        self.assertEqual(record["cleanup_failures"][0]["resource"], "stop_watcher")
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(record))

    def test_stop_event_close_failure_still_releases_lock(self):
        record, lock, event, service, watcher, _ = self.cleanup_fixture(event_error=OSError(5, "PRIVATE_TOKEN_event"))
        service.close.assert_called_once()
        lock.release.assert_called_once()
        self.assertEqual(record["cleanup_failures"][0]["resource"], "stop_event")
        self.assertEqual(record["cleanup_failure"]["errno"], 5)

    def test_multiple_cleanup_failures_all_get_attempted_and_recorded(self):
        record, lock, event, service, watcher, _ = self.cleanup_fixture(
            join_error=RuntimeError("PRIVATE_TOKEN_join"), service_error=OSError(5,"PRIVATE_TOKEN_service"),
            event_error=OSError(6,"PRIVATE_TOKEN_event"))
        watcher.join.assert_called_once()
        service.close.assert_called_once()
        event.close.assert_called_once()
        lock.release.assert_called_once()
        self.assertEqual([x["resource"] for x in record["cleanup_failures"]], ["stop_watcher","owned_services","stop_event"])
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
