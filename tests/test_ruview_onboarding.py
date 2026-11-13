"""Future USB protocol qualification with fake transports only, never a COM device."""
import base64
from contextlib import ExitStack
import io
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock

from ariadne import ruview_onboarding as usb
from ariadne import ruview_setup as setup
from scripts import ruview_onboard
import test_ruview_setup as setup_tests


class FakeTransport:
    def __init__(self, *, device="1234567890abcdef", configured=False, node=0,
                 hello_change=None, ack_change=None, noise=None, short_write=False):
        self.device, self.configured, self.node = device, configured, node
        self.hello_change, self.ack_change = hello_change, ack_change
        self.noise = list(noise or ())
        self.writes, self.pending, self.closed, self.short_write = [], [], False, short_write

    def write(self, raw):
        self.writes.append(raw)
        if self.short_write:
            return len(raw) - 1
        fields = raw.decode("ascii").strip().split(" ")
        if fields[0] == "RUVIEW_HELLO_V1":
            values = {"nonce": fields[1], "chip": "esp32s3", "version": usb.DEVICE_VERSION,
                "profile": setup.PROFILE, "node_id": str(self.node), "target_ip": "192.168.1.20",
                "target_port": "5500", "configured": str(int(self.configured)), "device_digest": self.device}
            if self.hello_change:
                self.hello_change(values)
            self.pending = self.noise + [("RUVIEW_HELLO_OK_V1 " + " ".join(key + "=" + value for key, value in values.items()) + "\n").encode()]
        elif fields[0] == "RUVIEW_CONFIG_V1":
            values = {"nonce": fields[1], "node_id": fields[2], "rebooting": "1"}
            if self.ack_change:
                self.ack_change(values)
            self.pending = [("RUVIEW_CONFIG_OK_V1 " + " ".join(key + "=" + value for key, value in values.items()) + "\n").encode()]
        return len(raw)

    def readline(self, maximum, deadline):
        return self.pending.pop(0) if self.pending else b""

    def close(self):
        self.closed = True


class RuViewOnboardingTests(unittest.TestCase):
    reseal = setup_tests.RuViewSetupTests.reseal

    def setUp(self):
        setup_tests.RuViewSetupTests.setUp(self)
        setup.prepare("firmware-source", root=self.root)
        self.name = "xiao-three-node"

    def identify(self, *, name=None, node=1, port="COM5", request="usb-identify-1", transport=None):
        transport = transport or FakeTransport()
        result = usb.identify(name or self.name, node, port, request, root=self.root, open_usb=True,
                              transport_factory=lambda actual: transport)
        return result, transport

    def configure(self, *, name=None, node=1, port="COM5", request="usb-configure-1", identity="usb-identify-1",
                  device="1234567890abcdef", transport=None, review_existing=False, **kwargs):
        transport = transport or FakeTransport(device=device)
        result = usb.configure(name or self.name, node, port, request, identity, device, "192.168.1.20", 5500,
            kwargs.get("ssid", "PRIVATE-WIFI-NAME"), kwargs.get("password", "SECRET-DO-NOT-SAVE"), root=self.root,
            apply_to_device=True, review_existing=review_existing, transport_factory=lambda actual: transport,
            **({"clock": kwargs["clock"]} if "clock" in kwargs else {}))
        return result, transport

    def test_full_three_node_flow_with_no_network_subprocess_or_serial_module(self):
        with ExitStack() as stack:
            for target in ("socket.socket", "socket.create_connection", "subprocess.Popen", "urllib.request.urlopen", "os.system"):
                stack.enter_context(mock.patch(target, side_effect=AssertionError("Forbidden operation")))
            stack.enter_context(mock.patch.dict(sys.modules, {"serial": None}))
            for node in (1, 2, 3):
                device, port = f"{node:016x}", "COM" + str(node + 4)
                identity, reader = self.identify(node=node, port=port, request="usb-id-" + str(node), transport=FakeTransport(device=device))
                configured, writer = self.configure(node=node, port=port, request="usb-cfg-" + str(node), identity="usb-id-" + str(node),
                                                    device=device, transport=FakeTransport(device=device))
                self.assertTrue(reader.closed and writer.closed)
                self.assertTrue(configured["configuration_acknowledged"])
                self.assertFalse(configured["persistent_state_verified"])
                self.assertFalse(configured["hardware_qualified"] or configured["rf_verified"])
            report = usb.status(root=self.root)
        self.assertTrue(report["all_three_configuration_acknowledgements"])
        self.assertEqual(report["acknowledged_node_ids"], [1, 2, 3])
        self.assertFalse(report["persistent_state_verified"])

    def test_explicit_arming_required_before_driver_factory_or_new_journal(self):
        factory = mock.Mock(side_effect=AssertionError("Must not open driver"))
        with self.assertRaises(ValueError):
            usb.identify(self.name, 1, "COM5", "usb-no-arm", root=self.root, transport_factory=factory)
        with self.assertRaises(ValueError):
            usb.configure(self.name, 1, "COM5", "usb-no-arm", "usb-id", "1" * 16, "192.168.1.20", 5500,
                          "ssid", "password", root=self.root, transport_factory=factory)
        factory.assert_not_called()
        self.assertFalse((self.root / "data/ruview-onboarding").exists())

    def test_identity_port_node_and_fixed_kit_are_bound_before_configuration(self):
        self.identify()
        factory = mock.Mock(side_effect=AssertionError("Must not open driver"))
        for node, port, digest in ((2, "COM5", "1234567890abcdef"), (1, "COM6", "1234567890abcdef"), (1, "COM5", "0" * 16)):
            with self.assertRaises(ValueError):
                usb.configure(self.name, node, port, "usb-bad-binding", "usb-identify-1", digest,
                    "192.168.1.20", 5500, "ssid", "password", root=self.root, apply_to_device=True, transport_factory=factory)
        factory.assert_not_called()

    def test_wrong_chip_version_profile_nonce_or_digest_holds_without_config_write(self):
        changes = (lambda value: value.update(chip="esp32c6"), lambda value: value.update(version="unreviewed"),
                   lambda value: value.update(profile="different"), lambda value: value.update(nonce="0" * 32),
                   lambda value: value.update(device_digest="invalid"), lambda value: value.update(node_id="01"),
                   lambda value: value.update(device_digest="0" * 16))
        for index, change in enumerate(changes):
            name = "xiao-wrong-" + str(index)
            setup.prepare("firmware-source", name, root=self.root)
            transport = FakeTransport(hello_change=change)
            with self.assertRaises(usb.OnboardingError):
                self.identify(name=name, transport=transport)
            self.assertTrue(transport.closed)
            self.assertEqual(len(transport.writes), 1)
            self.assertEqual(usb.status(name, root=self.root)["status"], "held")

    def test_raw_diagnostics_and_credentials_never_enter_receipts_errors_or_stdout(self):
        self.identify(transport=FakeTransport(noise=[b"PRIVATE-BOOT-DIAGNOSTIC\n"]))
        result, transport = self.configure()
        frame = transport.writes[1]
        self.assertIn(base64.b64encode(b"PRIVATE-WIFI-NAME"), frame)
        self.assertIn(base64.b64encode(b"SECRET-DO-NOT-SAVE"), frame)
        saved = b"\n".join(path.read_bytes() for path in (self.root / "data/ruview-onboarding").rglob("*") if path.is_file())
        for secret in (b"PRIVATE-WIFI-NAME", b"SECRET-DO-NOT-SAVE", b"PRIVATE-BOOT-DIAGNOSTIC",
                       base64.b64encode(b"PRIVATE-WIFI-NAME"), base64.b64encode(b"SECRET-DO-NOT-SAVE"), frame.strip()):
            self.assertNotIn(secret, saved)
        self.assertNotIn("SSID", str(result))

    def test_fresh_device_identity_change_and_existing_configuration_are_not_silently_overwritten(self):
        for index, transport in enumerate((FakeTransport(device="0" * 16), FakeTransport(configured=True))):
            name = "xiao-existing-" + str(index)
            setup.prepare("firmware-source", name, root=self.root)
            self.identify(name=name)
            with self.assertRaises(usb.OnboardingError):
                self.configure(name=name, transport=transport)
            self.assertEqual(len(transport.writes), 1)
            self.assertTrue(transport.closed)
        name = "xiao-reviewed-existing"
        setup.prepare("firmware-source", name, root=self.root)
        self.identify(name=name, transport=FakeTransport(configured=True))
        result, transport = self.configure(name=name, transport=FakeTransport(configured=True), review_existing=True)
        self.assertTrue(result["configuration_acknowledged"])

    def test_duplicate_physical_digest_or_node_assignment_is_blocked_before_open(self):
        self.identify()
        self.configure()
        self.identify(node=2, port="COM6", request="usb-identify-2")
        transport = FakeTransport()
        with self.assertRaises(ValueError):
            self.configure(node=2, port="COM6", request="usb-configure-2", identity="usb-identify-2", transport=transport)
        self.assertEqual(transport.writes, [])

    def test_bad_or_uncertain_config_ack_is_never_retried_or_promoted_to_success(self):
        self.identify()
        transport = FakeTransport(ack_change=lambda value: value.update(nonce="0" * 32))
        with self.assertRaises(usb.OnboardingError):
            self.configure(transport=transport)
        self.assertEqual(len(transport.writes), 2)
        folder = self.root / "data/ruview-onboarding" / self.name / "usb-configure-1"
        self.assertFalse((folder / "receipt.json").exists())
        self.assertEqual(json.loads((folder / "failure.json").read_bytes())["phase"], "configuration_ack")
        self.assertEqual(usb.status(root=self.root)["status"], "held")
        factory = mock.Mock(side_effect=AssertionError("Never retry"))
        with self.assertRaises(usb.OnboardingError):
            usb.identify(self.name, 1, "COM5", "usb-fresh-id", root=self.root, open_usb=True, transport_factory=factory)
        factory.assert_not_called()

    def test_short_write_and_bounded_noise_or_truncation_stop_once(self):
        cases = [FakeTransport(short_write=True), FakeTransport(noise=[b"x" * 385 + b"\n"]),
                 FakeTransport(noise=[b"truncated"]), FakeTransport(noise=[b"noise\n"] * 40)]
        for index, transport in enumerate(cases):
            name = "xiao-bounds-" + str(index)
            setup.prepare("firmware-source", name, root=self.root)
            with self.assertRaises(usb.OnboardingError):
                self.identify(name=name, transport=transport)
            self.assertEqual(len(transport.writes), 1)
            self.assertTrue(transport.closed)

    def test_nonce_age_is_checked_before_any_persistent_configuration_write(self):
        self.identify()
        calls = iter([0, 0, 0, 0, 61])
        def clock():
            return next(calls, 61)
        transport = FakeTransport()
        with self.assertRaises(usb.OnboardingError):
            self.configure(transport=transport, clock=clock)
        self.assertEqual(len(transport.writes), 1)

    def test_non_rfc1918_target_invalid_ports_or_credentials_do_not_open_usb(self):
        self.identify()
        factory = mock.Mock(side_effect=AssertionError("Must not open"))
        base = [self.name, 1, "COM5", "usb-bad-input", "usb-identify-1", "1234567890abcdef", "192.168.1.20", 5500, "ssid", "password"]
        for position, value in ((6, "127.0.0.1"), (6, "8.8.8.8"), (6, "169.254.1.2"), (6, "192.168.01.2"),
                                (7, True), (7, 0), (8, ""), (8, "x" * 33), (9, "short"), (9, "secret\ntext")):
            args = list(base)
            args[position] = value
            with self.assertRaises(ValueError):
                usb.configure(*args, root=self.root, apply_to_device=True, transport_factory=factory)
        factory.assert_not_called()

    def test_pause_before_open_and_before_configuration_preserves_hold(self):
        factory = mock.Mock(side_effect=AssertionError("Must not open"))
        with mock.patch.object(setup, "_controls", return_value=(True, 0)), self.assertRaises(ValueError):
            usb.identify(self.name, 1, "COM5", "usb-paused", root=self.root, open_usb=True, transport_factory=factory)
        factory.assert_not_called()
        self.identify()
        transport = FakeTransport()
        state, original = {"paused": False}, transport.write
        def pause_after_hello(raw):
            result = original(raw)
            if raw.startswith(b"RUVIEW_HELLO_V1 "):
                state["paused"] = True
            return result
        transport.write = pause_after_hello
        with mock.patch.object(setup, "_controls", side_effect=lambda root: (state["paused"], 0)), self.assertRaises(usb.OnboardingError):
            self.configure(transport=transport)
        self.assertEqual(len(transport.writes), 1)

    def test_cli_missing_arm_or_credential_arguments_cannot_prompt_or_echo_secrets(self):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ruview_onboard, "ROOT", self.root), mock.patch("sys.stdout", output), \
             mock.patch("sys.stderr", errors), mock.patch.object(ruview_onboard, "_hidden_credentials", side_effect=AssertionError("No prompt")):
            self.assertEqual(ruview_onboard.main(["identify", "--node", "1", "--port", "COM5", "--request-id", "usb-no-arm"]), 1)
            self.assertEqual(ruview_onboard.main(["configure", "--node", "1", "--port", "COM5", "--request-id", "usb-no-arm",
                "--identification", "usb-id", "--expected-device", "1" * 16, "--target-ip", "192.168.1.20", "--target-port", "5500"]), 1)
            with self.assertRaises(SystemExit):
                ruview_onboard.main(["status", "--password", "DO-NOT-ECHO-THIS"])
        self.assertNotIn("DO-NOT-ECHO-THIS", output.getvalue() + errors.getvalue())
        self.assertFalse((self.root / "data/ruview-onboarding").exists())

    def test_driver_is_optional_lazy_and_no_deliberate_rts_dtr_reset_is_requested(self):
        observed = []
        class Driver:
            def __init__(self, **kwargs):
                self.dtr = self.rts = True
                self.port = None
                observed.append(kwargs)
            def open(self):
                observed.append((self.port, self.dtr, self.rts))
            def close(self):
                observed.append("closed")
        with mock.patch.dict(sys.modules, {"serial": types.SimpleNamespace(VERSION="3.5", Serial=Driver)}):
            driver = usb._SerialLineTransport("COM5")
            driver.close()
        self.assertEqual(observed[1], ("COM5", False, False))
        self.assertEqual(observed[0]["port"], None)

    def test_changed_reviewed_declarations_are_not_approved_by_a_generic_existing_config_flag(self):
        self.identify(transport=FakeTransport(configured=True))
        transport = FakeTransport(configured=True, hello_change=lambda fields: fields.update(target_ip="192.168.1.21"))
        with self.assertRaises(usb.OnboardingError):
            self.configure(transport=transport, review_existing=True)
        self.assertEqual(len(transport.writes), 1)

    def test_cross_process_style_exclusive_reservation_prevents_two_physical_opens(self):
        self.identify()
        opened, release = threading.Event(), threading.Event()
        transport = FakeTransport()
        results, errors = [], []
        def factory(port):
            opened.set()
            if not release.wait(3):
                raise AssertionError("Test failed to release fake transport")
            return transport
        def first():
            try:
                results.append(usb.configure(self.name, 1, "COM5", "usb-concurrent-first", "usb-identify-1", "1234567890abcdef",
                    "192.168.1.20", 5500, "ssid", "password", root=self.root, apply_to_device=True, transport_factory=factory))
            except BaseException as error:
                errors.append(type(error).__name__)
        thread = threading.Thread(target=first)
        thread.start()
        try:
            self.assertTrue(opened.wait(3))
            second = mock.Mock(side_effect=AssertionError("Second physical open forbidden"))
            with self.assertRaises(ValueError):
                usb.configure(self.name, 1, "COM5", "usb-concurrent-second", "usb-identify-1", "1234567890abcdef",
                    "192.168.1.20", 5500, "ssid", "password", root=self.root, apply_to_device=True, transport_factory=second)
            second.assert_not_called()
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(len(transport.writes), 2)
        self.assertFalse((self.root / "data/ruview-onboarding" / self.name / "active-action.json").exists())

    def test_pause_resume_epoch_change_during_hello_revokes_old_intent_without_config_write(self):
        self.identify()
        transport, state = FakeTransport(), {"revision": 0}
        original = transport.write
        def pause_then_resume(raw):
            result = original(raw)
            if raw.startswith(b"RUVIEW_HELLO_V1 "):
                state["revision"] = 2  # pause then resume, both increment the epoch
            return result
        transport.write = pause_then_resume
        with mock.patch.object(setup, "_controls", side_effect=lambda root: (False, state["revision"])), self.assertRaises(usb.OnboardingError):
            self.configure(transport=transport)
        self.assertEqual(len(transport.writes), 1)
        self.assertTrue(transport.closed)
        self.assertEqual(usb.status(root=self.root)["status"], "held")
        path = self.root / "data/ruview-onboarding" / self.name / "usb-configure-1" / "attempt.json"
        self.assertEqual(json.loads(path.read_bytes())["control_revision"], 0)

    def test_pause_resume_while_hidden_prompt_runs_cannot_refresh_the_old_cli_intent(self):
        self.identify()
        state, output = {"revision": 0}, io.StringIO()
        factory = mock.Mock(side_effect=AssertionError("No physical open after revoked prompt intent"))
        original_configure = usb.configure
        def credentials():
            state["revision"] = 2
            return "ssid", "password"
        def configure(*args, **kwargs):
            return original_configure(*args, **kwargs, transport_factory=factory)
        with mock.patch.object(ruview_onboard, "ROOT", self.root), mock.patch("sys.stdout", output), \
             mock.patch.object(setup, "_controls", side_effect=lambda root: (False, state["revision"])), \
             mock.patch.object(ruview_onboard, "_hidden_credentials", side_effect=credentials), \
             mock.patch.object(usb, "configure", side_effect=configure):
            code = ruview_onboard.main(["configure", "--node", "1", "--port", "COM5", "--request-id", "usb-cli-revoked",
                "--identification", "usb-identify-1", "--expected-device", "1234567890abcdef", "--target-ip", "192.168.1.20",
                "--target-port", "5500", "--apply-to-device"])
        self.assertEqual(code, 1)
        factory.assert_not_called()
        self.assertFalse((self.root / "data/ruview-onboarding" / self.name / "usb-cli-revoked").exists())


if __name__ == "__main__":
    unittest.main()
