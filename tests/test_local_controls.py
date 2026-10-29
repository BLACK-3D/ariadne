"""Native isolated Home Assistant contract and action no-replay tests."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne.local_controls import LocalControls
from ariadne.store import Store
from ariadne.tools import ToolRegistry

UTC = timezone.utc


class LocalControlTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix=".home-control-test-", dir=Path.cwd())
        self.addCleanup(folder.cleanup)
        self.settings = Settings(data_dir=Path(folder.name), api_token="owner-fixture-key")
        self.registry = ToolRegistry(self.settings)
        self.provider = getattr(self.registry, "local_controls", None) or LocalControls(self.registry)
        self.registry.local_controls = self.provider
        if "home_control" not in self.registry._extension_names:
            self.registry.extensions += (self.provider,)
            self.registry._extension_names["home_control"] = self.provider
        self.provider.store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.cfg = {"schema": 1, "enabled": True, "read_connector": "connector_ha_states",
            "entities": ["light.desk", "sensor.temperature", "switch.missing"], "stale_seconds": 300,
            "actions": [{"id": "desk_on", "entity_id": "light.desk", "service": "light.turn_on", "connector": "connector_ha_on", "data": {"brightness_pct": 50}}]}
        self.registry._connectors.update(self.connectors())
        self.write_config()

    @staticmethod
    def connectors(port=8123):
        return {"connector_ha_states": {"id": "ha_states", "description": "Fixture observations", "method": "GET", "read_only": True,
                "url": f"http://127.0.0.1:{port}/api/states", "headers": {}, "timeout_seconds": 1},
            "connector_ha_on": {"id": "ha_on", "description": "Fixture light action", "method": "POST", "read_only": False,
                "url": f"http://127.0.0.1:{port}/api/services/light/turn_on", "headers": {}, "timeout_seconds": 1}}

    def write_config(self):
        self.provider.config_path.write_text(json.dumps(self.cfg), encoding="utf-8")

    def call(self, action, **kwargs):
        return self.provider.execute("home_control", {"action": action, **kwargs})

    def rows(self):
        stamp = datetime.now(UTC).isoformat()
        return [{"entity_id": "light.desk", "state": "off", "last_updated": stamp,
                "attributes": {"friendly_name": "Desk", "brightness": 0, "sensitive_unselected": "excluded"}},
            {"entity_id": "sensor.temperature", "state": "21.5", "last_updated": stamp, "attributes": {"unit_of_measurement": "C"}},
            {"entity_id": "lock.front_door", "state": "unlocked", "last_updated": stamp, "attributes": {}}]

    def import_rows(self, rows=None, observed=None):
        path = self.settings.workspace / "states.json"
        path.write_text(json.dumps(rows if rows is not None else self.rows()), encoding="utf-8")
        return self.call("import_snapshot", source_path="states.json", observed_at=observed or datetime.now(UTC).isoformat())

    def apply_args(self, request_id="turn-desk-on-1"):
        return {"action": "apply", "action_id": "desk_on", "request_id": request_id}

    def approve(self, args):
        return self.registry.fingerprint("home_control", args)

    def test_missing_configuration_is_disabled_and_status_never_networks(self):
        self.provider.config_path.unlink()
        with mock.patch("socket.socket", side_effect=AssertionError("No sockets")), mock.patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")):
            result = self.call("catalog")
            self.assertFalse(result["enabled"])
            self.assertEqual(self.call("status")["snapshot"]["status"], "unavailable")
            self.assertTrue(self.provider.status()["offline"])

    def test_offline_import_filters_scope_attributes_and_hashes_exact_source(self):
        with mock.patch.object(self.registry, "_request", side_effect=AssertionError("No network")):
            result = self.import_rows()
            snapshot = result["snapshot"]
            self.assertEqual([r["entity_id"] for r in snapshot["entities"]], self.cfg["entities"])
            self.assertNotIn("sensitive_unselected", snapshot["entities"][0]["attributes"])
            self.assertTrue(snapshot["entities"][2]["unavailable"])
            self.assertEqual(snapshot["source_sha256"], hashlib.sha256((self.settings.workspace / "states.json").read_bytes()).hexdigest())
            self.assertEqual(self.call("status")["snapshot"]["observed_at"], snapshot["observed_at"])

    def test_missing_old_unavailable_and_changed_scope_are_explicit(self):
        rows = self.rows()
        old = (datetime.now(UTC)-timedelta(hours=2)).isoformat()
        rows[0]["last_updated"] = old
        self.import_rows(rows)
        snapshot = self.call("status")["snapshot"]
        self.assertTrue(snapshot["entities"][0]["stale"])
        self.assertFalse(snapshot["stale"])
        self.cfg["entities"] = ["sensor.temperature"]
        self.cfg["actions"] = []
        self.write_config()
        self.assertEqual(self.call("status")["snapshot"]["status"], "configuration_changed")

    def test_old_import_uses_original_observation_time(self):
        old = datetime.now(UTC)-timedelta(days=2)
        rows = self.rows()
        for row in rows:
            row["last_updated"] = old.isoformat()
        snapshot = self.import_rows(rows, observed=old.isoformat())["snapshot"]
        self.assertTrue(snapshot["stale"])
        self.assertGreater(snapshot["age_seconds"], 100_000)
        self.assertEqual(snapshot["origin"], "owner_imported_export")

    def test_snapshot_invalid_timestamps_duplicates_and_nonfinite_fail(self):
        variants = []
        rows = self.rows(); rows[0]["last_updated"] = "2026-10-29T10:00:00"; variants.append(rows)
        rows = self.rows(); rows.append(rows[0]); variants.append(rows)
        rows = self.rows(); rows[0]["attributes"]["brightness"] = float("nan"); variants.append(rows)
        rows = self.rows(); rows[0]["last_updated"] = (datetime.now(UTC)+timedelta(days=1)).isoformat(); variants.append(rows)
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.import_rows(rows)
        with self.assertRaises(ValueError):
            self.import_rows(observed=(datetime.now(UTC)+timedelta(days=1)).isoformat())

    def test_paths_e_drive_traversal_and_hardlinks_are_rejected(self):
        for name in ("../states.json", "E:/states.json", "C:/states.json", "states.json:stream"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.call("import_snapshot", source_path=name, observed_at=datetime.now(UTC).isoformat())
        path = self.settings.workspace / "states.json"
        path.write_text("[]")
        os.link(path, self.settings.workspace / "other.json")
        with self.assertRaises(ValueError):
            self.call("import_snapshot", source_path="states.json", observed_at=datetime.now(UTC).isoformat())

    def test_no_devices_no_actions_and_unknown_fields_never_dispatch(self):
        with mock.patch.object(self.registry, "_request", side_effect=AssertionError("No network")):
            self.assertFalse(self.call("propose", action_id="desk_on")["performed"])
            for args in ({"action": "propose", "action_id": "unknown"}, {"action": "apply", "action_id": "desk_on", "request_id": "x", "entity_id": "lock.front_door"},
                         {"action": []}, {"action": "status", "request_id": "x"}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    self.provider.execute("home_control", args)

    def test_configuration_rejects_ssrf_public_dns_query_origin_and_unsafe_services(self):
        bad_urls = ["http://169.254.169.254/api/states", "http://8.8.8.8/api/states", "http://localhost:8123/api/states", "http://homeassistant.local:8123/api/states",
                    "http://127.0.0.1:8123/api/states?x=1", "http://127.0.0.1:8123/api/config"]
        original = self.registry._connectors["connector_ha_states"]["url"]
        for url in bad_urls:
            self.registry._connectors["connector_ha_states"]["url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.call("catalog")
        self.registry._connectors["connector_ha_states"]["url"] = original
        self.registry._connectors["connector_ha_on"]["url"] = "http://127.0.0.1:9999/api/services/light/turn_on"
        with self.assertRaises(ValueError): self.call("catalog")
        self.registry._connectors.update(self.connectors())
        self.cfg["actions"][0]["service"] = "lock.unlock"
        self.write_config()
        with self.assertRaises(ValueError): self.call("catalog")

    def test_fixed_body_values_and_exact_allowlist_enforced(self):
        for data in ({"entity_id": "light.other"}, {"brightness_pct": True}, {"brightness_pct": 101}, {"transition": 11}, {"template": "{{evil}}"}):
            self.cfg["actions"][0]["data"] = data
            self.write_config()
            with self.subTest(data=data), self.assertRaises(ValueError): self.call("catalog")

    def test_apply_needs_approval_store_and_unpaused_enabled_configuration(self):
        args = self.apply_args()
        self.assertEqual(self.provider.policy("home_control", args), "approval")
        with mock.patch.object(self.registry, "_request", side_effect=AssertionError("No network")):
            with self.assertRaises(ValueError): self.provider.execute("home_control", args)
            self.provider.store.set_setting("paused", True)
            with self.assertRaises(ValueError): self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
            self.provider.store.set_setting("paused", False)
            self.cfg["enabled"] = False; self.write_config()
            with self.assertRaises(ValueError): self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
            self.cfg["enabled"] = True; self.write_config()
            self.provider.store = None
            with self.assertRaises(ValueError): self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))

    def test_success_reserves_before_fixed_dispatch_and_never_repeats(self):
        args = self.apply_args()
        fingerprint = self.approve(args)
        def request(connector, payload):
            self.assertEqual(self.call("receipt", request_id=args["request_id"])["receipt"]["status"], "uncertain")
            self.assertEqual(payload, {"body": {"entity_id": "light.desk", "brightness_pct": 50}})
            self.assertEqual(connector["url"], self.registry._connectors["connector_ha_on"]["url"])
            return {"ok": True, "data": [], "truncated": False}
        with mock.patch.object(self.registry, "_request", side_effect=request) as dispatch:
            first = self.provider.execute("home_control", args, approval_fingerprint=fingerprint)
            second = self.provider.execute("home_control", args, approval_fingerprint=fingerprint)
        self.assertTrue(first["ok"])
        self.assertTrue(second["replayed"])
        self.assertEqual(dispatch.call_count, 1)
        self.assertIn("not independent physical", first["warning"])

    def test_uncertain_response_exception_and_restart_never_replay(self):
        args = self.apply_args()
        with mock.patch.object(self.registry, "_request", side_effect=OSError("private diagnostic")) as dispatch:
            first = self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
            self.assertTrue(first["uncertain"])
            self.assertNotIn("private diagnostic", json.dumps(first))
            restarted = LocalControls(self.registry); restarted.store = self.provider.store
            second = restarted.execute("home_control", args, approval_fingerprint=self.approve(args))
            self.assertTrue(second["uncertain"])
            self.assertTrue(second["replayed"])
            self.assertEqual(dispatch.call_count, 1)

    def test_process_interruption_after_reservation_leaves_durable_uncertain_receipt(self):
        args = self.apply_args("crash-after-reservation")
        fingerprint = self.approve(args)
        with mock.patch.object(self.registry, "_request", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.provider.execute("home_control", args, approval_fingerprint=fingerprint)
        restarted = LocalControls(self.registry); restarted.store = self.provider.store
        with mock.patch.object(self.registry, "_request", side_effect=AssertionError("Do not resend")):
            result = restarted.execute("home_control", args, approval_fingerprint=fingerprint)
        self.assertTrue(result["uncertain"])
        self.assertTrue(result["replayed"])

    def test_acknowledged_but_invalid_response_or_receipt_failure_stays_uncertain(self):
        args = self.apply_args("nan-response")
        with mock.patch.object(self.registry, "_request", return_value={"ok": True, "data": [float("nan")], "truncated": False}):
            result = self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
        self.assertTrue(result["uncertain"])
        args = self.apply_args("receipt-write-failed")
        original, count = self.provider.database, [0]
        @contextmanager
        def fail_finalization():
            count[0] += 1
            if count[0] == 3:
                raise OSError("private disk diagnostic")
            with original() as db: yield db
        with mock.patch.object(self.provider, "database", fail_finalization), mock.patch.object(self.registry, "_request", return_value={"ok": True, "data": [], "truncated": False}):
            result = self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
        self.assertTrue(result["uncertain"])
        self.assertNotIn("private disk", json.dumps(result))
        self.assertEqual(self.call("receipt", request_id=args["request_id"])["receipt"]["status"], "uncertain")

    def test_global_pause_writer_serializes_with_one_bounded_dispatch(self):
        args = self.apply_args("pause-race")
        started, committed = threading.Event(), threading.Event()
        def pause():
            started.set()
            self.provider.store.set_setting("paused", True)
            committed.set()
        writer = threading.Thread(target=pause)
        def request(*unused):
            writer.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(committed.wait(.1), "Pause writer must not overtake an already leased dispatch")
            return {"ok": True, "data": [], "truncated": False}
        try:
            with mock.patch.object(self.registry, "_request", side_effect=request):
                result = self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
            writer.join(3)
            self.assertTrue(result["ok"])
            self.assertTrue(committed.is_set())
        finally:
            if writer.is_alive(): writer.join(5)
        with mock.patch.object(self.registry, "_request", side_effect=AssertionError("Paused dispatch")), self.assertRaises(ValueError):
            self.provider.execute("home_control", self.apply_args("after-pause"), approval_fingerprint=self.approve(self.apply_args("after-pause")))

    def test_concurrent_same_request_has_only_one_remote_dispatch(self):
        args = self.apply_args("concurrent-same-request")
        fingerprint = self.approve(args)
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def dispatch(*unused):
            entered.set()
            self.assertTrue(release.wait(3))
            return {"ok": True, "data": [], "truncated": False}
        def first():
            outcomes.append(self.provider.execute("home_control", args, approval_fingerprint=fingerprint))
        worker = threading.Thread(target=first)
        with mock.patch.object(self.registry, "_request", side_effect=dispatch) as post:
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                second = LocalControls(self.registry); second.store = self.provider.store
                duplicate = second.execute("home_control", args, approval_fingerprint=fingerprint)
                self.assertTrue(duplicate["replayed"])
                self.assertTrue(duplicate["uncertain"])
            finally:
                release.set(); worker.join(5)
            self.assertEqual(post.call_count, 1)
        self.assertTrue(outcomes[0]["ok"])

    def test_credential_rotation_or_configuration_change_revokes_old_approval(self):
        self.registry._connectors["connector_ha_on"]["headers"] = {"Authorization": {"env": "ARIADNE_HA_FIXTURE_TOKEN"}}
        args = self.apply_args()
        with mock.patch.dict(os.environ, {"ARIADNE_HA_FIXTURE_TOKEN": "Bearer first-private-token"}):
            fingerprint = self.approve(args)
            material = self.provider.fingerprint_material("home_control", args)
            self.assertNotIn("first-private-token", json.dumps(material))
            with mock.patch.dict(os.environ, {"ARIADNE_HA_FIXTURE_TOKEN": "Bearer rotated-private-token"}), mock.patch.object(self.registry, "_request", side_effect=AssertionError("No POST")):
                with self.assertRaises(ValueError): self.provider.execute("home_control", args, approval_fingerprint=fingerprint)

    def test_revocation_in_reserved_gap_records_known_not_sent(self):
        args = self.apply_args()
        fingerprint = self.approve(args)
        original = self.provider._dispatch_lease
        @contextmanager
        def revoke(cancelled):
            self.provider.store.set_setting("paused", True)
            with original(cancelled): yield
        with mock.patch.object(self.provider, "_dispatch_lease", revoke), mock.patch.object(self.registry, "_request", side_effect=AssertionError("No POST")):
            result = self.provider.execute("home_control", args, approval_fingerprint=fingerprint)
        self.assertEqual(result["status"], "not_sent")
        self.assertFalse(result["uncertain"])
        self.assertEqual(self.call("receipt", request_id=args["request_id"])["receipt"]["status"], "not_sent")

    def test_read_refresh_failure_keeps_cached_observation_time_and_revocation_discards(self):
        saved = self.import_rows()["snapshot"]
        with mock.patch.object(self.registry, "_request", return_value={"ok": False, "status": 503}):
            self.assertFalse(self.call("refresh")["ok"])
        self.assertEqual(self.call("status")["snapshot"]["observed_at"], saved["observed_at"])
        def revoke(*args):
            self.cfg["enabled"] = False; self.write_config()
            return {"ok": True, "data": self.rows(), "truncated": False}
        with mock.patch.object(self.registry, "_request", side_effect=revoke), self.assertRaises(ValueError): self.call("refresh")

    def test_request_collision_and_invalid_ids_do_not_repeat(self):
        args = self.apply_args()
        with mock.patch.object(self.registry, "_request", return_value={"ok": True, "data": [], "truncated": False}) as dispatch:
            self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
            self.cfg["actions"][0]["data"]["brightness_pct"] = 40; self.write_config()
            with self.assertRaises(ValueError): self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
            self.assertEqual(dispatch.call_count, 1)
        with self.assertRaises(ValueError): self.call("receipt", request_id="../bad")

    def test_shipped_examples_load_disabled_with_no_credentials_or_dispatch(self):
        root = Path(__file__).resolve().parents[1]
        self.settings.connectors_path.write_bytes((root / "config/examples/control-connectors.json").read_bytes())
        self.provider.config_path.write_bytes((root / "config/examples/control-home-assistant.json").read_bytes())
        registry = ToolRegistry(self.settings)
        provider = getattr(registry, "local_controls", None) or LocalControls(registry)
        with mock.patch.object(registry, "_request", side_effect=AssertionError("No network")):
            result = provider.execute("home_control", {"action": "catalog"})
            self.assertFalse(result["enabled"])
            self.assertEqual(len(result["actions"]), 2)
            self.assertEqual(len(result["entities"]), 2)

    def test_native_loopback_transport_success_and_redirect_refusal(self):
        rows, calls = self.rows(), []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(inner):
                calls.append(("GET", inner.path))
                data = json.dumps(rows).encode()
                inner.send_response(200); inner.send_header("Content-Type", "application/json"); inner.send_header("Content-Length", str(len(data))); inner.end_headers(); inner.wfile.write(data)
            def do_POST(inner):
                body = json.loads(inner.rfile.read(int(inner.headers["Content-Length"])))
                calls.append(("POST", inner.path, body))
                if body["brightness_pct"] == 49:
                    inner.send_response(302); inner.send_header("Location", "/forbidden-redirect"); inner.end_headers()
                else:
                    inner.send_response(200); inner.send_header("Content-Length", "2"); inner.end_headers(); inner.wfile.write(b"[]")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.registry._connectors.update(self.connectors(server.server_port))
        self.assertTrue(self.call("refresh")["ok"])
        args = self.apply_args("native-on")
        self.assertTrue(self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))["ok"])
        self.cfg["actions"][0]["data"]["brightness_pct"] = 49; self.write_config()
        args = self.apply_args("native-redirect")
        result = self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
        self.assertTrue(result["uncertain"])
        replay = self.provider.execute("home_control", args, approval_fingerprint=self.approve(args))
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(calls), 3)
        self.assertFalse(any("forbidden" in call[1] for call in calls))


if __name__ == "__main__":
    unittest.main()
