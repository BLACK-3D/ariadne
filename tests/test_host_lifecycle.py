"""Host ownership, launch/stop protocol and graceful HTTP shutdown contracts."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from scripts import host
from ariadne import __main__ as server_main


class HostTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / "staging"
        self.temp = tempfile.TemporaryDirectory(prefix="host-fixture-", dir=base)
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(host, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def identity(self, pid=123):
        return {"pid": pid, "executable": str(Path(host.sys.executable).resolve()), "created_filetime": 100, "owner_sid": "owner"}

    def record(self, **values):
        return {"schema": 1, "root": str(self.root), "state": "running", "identity": self.identity(),
                "request_id": "a" * 48, "stop_event": "Local\\Ariadne-" + "b" * 48,
                "url": "http://127.0.0.1:8080", **values}

    def test_environment_drops_parent_secrets_hooks_and_proxies(self):
        with patch.dict(os.environ, {"SystemRoot": "C:\\Windows", "SECRET_API_KEY": "private", "HTTP_PROXY": "private", "PYTHONPATH": "private"}, clear=True):
            result = host.clean_environment({"ARIADNE_OFFLINE": "1"})
        self.assertFalse({"SECRET_API_KEY", "HTTP_PROXY", "PYTHONPATH"} & set(result))
        self.assertEqual(result["ARIADNE_OFFLINE"], "1")
        self.assertTrue(Path(result["TEMP"]).is_relative_to(self.root))

    def test_environment_rejects_unknown_credentials_and_external_storage(self):
        for value in ({"ARIADNE_API_TOKEN": "private"}, {"ARIADNE_MODEL": False}, {"ARIADNE_MODEL": "x\x00"}, {"ARIADNE_DATA_DIR": str(self.root.parent)}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.clean_environment(value)

    def test_atomic_record_round_trip_preserves_schema(self):
        path = host.host_directory() / "status.json"
        host.write_json(path, {"state": "starting"})
        host.write_json(path, self.record())
        self.assertEqual(host.read_json(path), self.record())

    def test_oversized_record_rejected_before_parse(self):
        path = host.host_directory() / "status.json"
        path.write_text(" " * 32769)
        with self.assertRaises(ValueError):
            host.read_json(path)

    def test_record_requires_current_exact_identity_owner_and_root(self):
        with patch.object(host, "process_identity", return_value=self.identity(999)), patch.object(host, "same_process", return_value=True):
            self.assertTrue(host.verified_record(self.record()))
            for value in (self.record(root=str(self.root.parent)), self.record(state="failed"), self.record(identity={**self.identity(), "owner_sid": "other"}), self.record(identity={**self.identity(), "executable": "other.exe"})):
                self.assertFalse(host.verified_record(value))
        with patch.object(host, "process_identity", return_value=self.identity()), patch.object(host, "same_process", return_value=False):
            self.assertFalse(host.verified_record(self.record()))

    def test_reopen_verified_host_does_not_detach_or_touch_services(self):
        current = self.record()
        with patch.object(host, "status", return_value=current), patch.object(host, "detach") as detach, patch.object(host.webbrowser, "open") as browser, patch("sys.stdout", io.StringIO()):
            self.assertEqual(host.start(), current)
        detach.assert_not_called()
        browser.assert_called_once_with(current["url"])

    def test_starting_host_does_not_create_duplicate(self):
        with patch.object(host, "status", return_value=self.record(state="starting")), patch.object(host, "detach") as detach:
            with self.assertRaisesRegex(ValueError, "starting/stopping"):
                host.start(no_browser=True)
        detach.assert_not_called()

    def test_stop_absent_or_stale_record_changes_no_process(self):
        with patch.object(host, "status", return_value=None), patch.object(host, "StopEvent") as event, patch("sys.stdout", io.StringIO()):
            self.assertFalse(host.stop())
        event.assert_not_called()

    def test_stop_rechecks_identity_before_control_signal(self):
        event = Mock()
        with patch.object(host, "status", return_value=self.record()), patch.object(host, "StopEvent", return_value=event), patch.object(host, "same_process", return_value=False):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                host.stop()
        event.signal.assert_not_called()
        event.close.assert_called_once()

    def test_successful_stop_requires_matching_complete_receipt(self):
        current = self.record()
        event = Mock()
        with patch.object(host, "status", return_value=current), patch.object(host, "StopEvent", return_value=event), patch.object(host, "same_process", side_effect=[True, False, False]), patch.object(host, "read_json", return_value={**current, "state": "stopped"}), patch("sys.stdout", io.StringIO()):
            self.assertTrue(host.stop())
        event.signal.assert_called_once()
        event.close.assert_called_once()

    def test_failed_stop_receipt_is_not_reported_as_success(self):
        current = self.record()
        with patch.object(host, "status", return_value=current), patch.object(host, "StopEvent"), patch.object(host, "same_process", side_effect=[True, False, False]), patch.object(host, "read_json", return_value={**current, "state": "failed"}):
            with self.assertRaisesRegex(ValueError, "without a complete"):
                host.stop()

    def test_stop_signal_precedes_metadata_and_survives_publication_failure(self):
        event, finished, requested, record = Mock(), threading.Event(), threading.Event(), {"state": "running"}
        event.wait.return_value = True
        def fail_publish():
            self.assertTrue(requested.is_set())
            raise OSError("injected full disk")
        with patch.object(host, "_safe_diagnostic") as diagnostic:
            host.watch_for_stop(event, finished, requested, record, fail_publish)
        self.assertTrue(requested.is_set())
        self.assertEqual(record, {"state": "stopping", "stop_metadata_error": "OSError"})
        diagnostic.assert_called_once_with("Ariadne stop accepted; status publication failed. Shutdown continues.")
        event.wait.assert_called_once_with(250)

    def test_broken_error_log_cannot_strand_accepted_stop(self):
        event, finished, requested = Mock(), threading.Event(), threading.Event()
        event.wait.return_value = True
        broken_log = Mock()
        broken_log.write.side_effect = OSError("injected broken log")
        with patch("sys.stderr", broken_log):
            host.watch_for_stop(event, finished, requested, {}, Mock(side_effect=OSError("disk full")))
        self.assertTrue(requested.is_set())

    def test_model_launcher_uses_atomic_factory_before_observation(self):
        from scripts import provision
        import urllib.error
        data = self.root / "data"
        data.mkdir()
        process, order = Mock(pid=123), []
        process.poll.return_value = None
        factory = Mock(side_effect=lambda *args, **kwargs: (order.append("created_in_job"), process)[1])
        observer = Mock(side_effect=lambda value: order.append("observed"))
        with patch.object(provision, "ROOT", self.root), patch.object(provision, "api", side_effect=[urllib.error.URLError("missing"), {}]), patch.object(provision.subprocess, "Popen") as ordinary, patch("sys.stdout", io.StringIO()):
            provision.start_model_server(self.root / "ollama.exe", {}, create_process=factory, on_process=observer)
        ordinary.assert_not_called()
        factory.assert_called_once()
        observer.assert_called_once_with(process)
        self.assertEqual(order, ["created_in_job", "observed"])

    def test_flow_launcher_uses_atomic_factory_before_observation(self):
        from scripts import local_services
        process, order = Mock(pid=123), []
        process.poll.return_value = None
        factory = Mock(side_effect=lambda *args, **kwargs: (order.append("created_in_job"), process)[1])
        observer = Mock(side_effect=lambda value: order.append("observed"))
        socket = Mock()
        socket.connect_ex.side_effect = [1, 0]
        socket.__enter__ = Mock(return_value=socket)
        socket.__exit__ = Mock(return_value=False)
        with patch.object(local_services, "prepare", return_value=(self.root / "node.exe", self.root / "red.js", self.root, "x" * 43)), patch.object(local_services.socket, "socket", return_value=socket), patch.object(local_services.subprocess, "Popen") as ordinary, patch("sys.stdout", io.StringIO()):
            local_services.start(create_process=factory, on_process=observer, require_new=True)
        ordinary.assert_not_called()
        factory.assert_called_once()
        observer.assert_called_once_with(process)
        self.assertEqual(order, ["created_in_job", "observed"])


class ServerShutdownTests(unittest.TestCase):
    def test_wsgi_stop_invokes_graceful_shutdown_from_owner_thread(self):
        stop, running = threading.Event(), threading.Event()
        server = Mock()
        server.serve_forever.side_effect = lambda: (running.set(), stop.wait(3))
        server.shutdown.side_effect = lambda: None
        timer = threading.Thread(target=lambda: (running.wait(1), stop.set()))
        timer.start()
        server_main._run_server(server, stop)
        timer.join(1)
        server.shutdown.assert_called_once()

    def test_waitress_stop_closes_dedicated_map_and_dispatcher(self):
        stop = threading.Event()
        server = Mock()
        server.run.side_effect = lambda: stop.wait(3)
        timer = threading.Timer(.02, stop.set)
        timer.start()
        server_main._run_server(server, stop, waitress=True)
        timer.join(1)
        server.task_dispatcher.shutdown.assert_called_once_with(timeout=5)
        server.close.assert_called_once()
        server.asyncore.close_all.assert_called_once_with(server._map)

    def test_http_failure_propagates_without_automatic_restart(self):
        server = Mock()
        server.serve_forever.side_effect = ValueError("fixture failure")
        with self.assertRaisesRegex(ValueError, "fixture failure"):
            server_main._run_server(server, threading.Event())
        server.serve_forever.assert_called_once()

    def test_foreground_server_behavior_unchanged_without_stop_event(self):
        server = Mock()
        server_main._run_server(server, None)
        server.serve_forever.assert_called_once()
        server.shutdown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
