"""Explicit future USB onboarding for the pinned three-XIAO pilot.

Importing this module does not import a serial driver or open a port. Only an
explicitly armed identify/configure call may do that. There is no flash, erase,
OTA, eFuse, network listener, subprocess, credential file or retry operation.
Device responses are declarations, never firmware or physical attestation.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
import re
import secrets
import time

from . import ruview_setup as setup

DEVICE_VERSION = "ariadne-xiao-v1"
MAX_LINE = 384
MAX_READ_BYTES = 8192
MAX_READ_LINES = 32
RESPONSE_SECONDS = 8
NONCE_TTL_SECONDS = 60
MAX_ATTEMPTS = 32
_REQUEST = re.compile(r"usb-[a-z0-9][a-z0-9-]{0,47}\Z")
_DIGEST = re.compile(r"[0-9a-f]{16}\Z")
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_HELLO_FIELDS = {"nonce", "chip", "version", "profile", "node_id", "target_ip", "target_port", "configured", "device_digest"}


class OnboardingError(ValueError):
    """An intentionally redacted operational failure; receipt retains phase."""


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _number(value, minimum, maximum):
    setup._require(type(value) is str and re.fullmatch(r"0|[1-9][0-9]{0,4}", value), "Invalid onboarding integer.")
    number = int(value)
    setup._require(minimum <= number <= maximum, "Onboarding integer is outside its bound.")
    return number


def _device(value):
    setup._require(type(value) is str and _DIGEST.fullmatch(value) and value != "0" * 16,
                   "Declared device digest is invalid or the firmware's unavailable-identity sentinel.")
    return value


def _ipv4(value, *, private=False):
    setup._require(type(value) is str and 7 <= len(value) <= 15, "Invalid local IPv4 address.")
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise ValueError("Invalid local IPv4 address.") from None
    setup._require(str(address) == value, "Use a canonical dotted-decimal IPv4 address.")
    if private:
        setup._require(any(address in network for network in (
            ipaddress.IPv4Network("10.0.0.0/8"), ipaddress.IPv4Network("172.16.0.0/12"),
            ipaddress.IPv4Network("192.168.0.0/16"))), "Receiver address must be explicitly selected inside RFC1918 private IPv4 space.")
    return value


def _port_node(port, node):
    setup._require(type(port) is str and setup._PORT.fullmatch(port), "Choose an exact uppercase COM port, such as COM5.")
    setup._require(type(node) is int and node in {1, 2, 3}, "Choose node 1, 2 or 3.")


def _root(root, name):
    setup._kit(root, name)  # validates the same fixed kit namespace/name
    return setup._scope(root, Path("data/ruview-onboarding") / name)


def _path(root, name, request_id):
    setup._require(type(request_id) is str and _REQUEST.fullmatch(request_id), "Use an exclusive usb- request identifier.")
    return setup._scope(root, _root(root, name) / request_id)


def _receipt(root, path):
    record = setup._json(setup._read(root, path / "receipt.json", setup.MAX_JSON_BYTES))
    setup._fields(record, {"schema", "profile", "request_id", "action", "status", "kit_name", "manifest_sha256",
        "node_id", "port", "created_at", "completed_at", "attempt_sha256", "device_digest", "declarations",
        "configuration_acknowledged", "persistent_state_verified", "hardware_qualified", "rf_verified",
        "local_credential_retention", "source_identity", "target_ip", "target_port", "identification_request_id", "control_revision"}, "USB receipt")
    setup._require(type(record["schema"]) is int and record["schema"] == 1 and record["profile"] == setup.PROFILE and
        record["status"] == "completed" and record["action"] in {"identify", "configure"} and
        record["request_id"] == path.name and record["kit_name"] == path.parent.name and
        record["persistent_state_verified"] is False and record["hardware_qualified"] is False and
        record["rf_verified"] is False and record["local_credential_retention"] == "none" and
        record["source_identity"] == "USB device declarations, not attestation" and
        type(record["configuration_acknowledged"]) is bool and
        record["configuration_acknowledged"] == (record["action"] == "configure"), "USB receipt contains unsupported claims.")
    _port_node(record["port"], record["node_id"])
    setup._digest(record["manifest_sha256"], "USB manifest")
    _device(record["device_digest"])
    attempt_raw = setup._read(root, path / "attempt.json", setup.MAX_JSON_BYTES)
    setup._require(record["attempt_sha256"] == setup._sha(attempt_raw), "USB attempt receipt integrity differs.")
    attempt = setup._json(attempt_raw)
    setup._fields(attempt, {"schema", "profile", "request_id", "action", "status", "kit_name", "manifest_sha256",
        "node_id", "port", "created_at", "target_ip", "target_port", "identification_request_id", "expected_device_digest",
        "existing_configuration_reviewed", "control_revision"}, "USB attempt")
    for key in ("schema", "profile", "request_id", "action", "kit_name", "manifest_sha256", "node_id", "port", "created_at",
                "target_ip", "target_port", "identification_request_id", "control_revision"):
        setup._require(attempt[key] == record[key] and type(attempt[key]) is type(record[key]), "USB attempt binding differs.")
    setup._require(attempt["status"] == "accepted" and type(attempt["existing_configuration_reviewed"]) is bool,
                   "USB attempt status differs.")
    setup._require(type(record["control_revision"]) is int and 0 <= record["control_revision"] <= 2**53 - 1,
                   "USB control revision is invalid.")
    declarations = _declarations(record["declarations"])
    setup._require(declarations["device_digest"] == record["device_digest"], "USB device receipt binding differs.")
    if record["action"] == "configure":
        _ipv4(record["target_ip"], private=True)
        setup._require(type(record["target_port"]) is int and 1 <= record["target_port"] <= 65535 and
                       type(record["identification_request_id"]) is str and _REQUEST.fullmatch(record["identification_request_id"]) and
                       attempt["expected_device_digest"] == record["device_digest"], "USB configuration intent differs.")
        setup._require(not declarations["configured"] or attempt["existing_configuration_reviewed"] is True,
                       "USB receipt lacks review of its declared existing configuration.")
    else:
        setup._require(record["target_ip"] is record["target_port"] is record["identification_request_id"] is None and
                       attempt["expected_device_digest"] is None, "Identification receipt cannot claim configuration intent.")
    setup._require(set(setup._entries(root, path, 2)) == {"attempt.json", "receipt.json"}, "USB attempt has uncertain or extra artifacts.")
    return record


def _ledger(root, name, *, active=None):
    folder = _root(root, name)
    if not folder.exists():
        return []
    rows = []
    names = setup._entries(root, folder, MAX_ATTEMPTS + 1)
    if "active-action.json" in names:
        if active is None:
            raise OnboardingError("An active or interrupted USB reservation holds new actions; it is never automatically cleared.")
        setup._require(setup._read(root, folder / "active-action.json", setup.MAX_JSON_BYTES) == active[0],
                       "An active or interrupted USB reservation holds new actions; it is never automatically cleared.")
        names.remove("active-action.json")
    else:
        setup._require(active is None, "The owned USB reservation disappeared.")
    setup._require(len(names) <= MAX_ATTEMPTS, "USB onboarding attempt retention bound reached.")
    for request_id in sorted(names):
        path = _path(root, name, request_id)
        setup._require(path.is_dir(), "USB journal contains an unsupported artifact.")
        if active is not None and request_id == active[1]:
            continue  # Only our exclusive reservation's just-created attempt.
        if not setup._scope(root, path / "receipt.json").is_file():
            raise OnboardingError("An interrupted or failed USB attempt holds new actions. Inspect its retained receipt; no automatic retry is available.")
        rows.append(_receipt(root, path))
    by_id = {row["request_id"]: row for row in rows}
    used_nodes, used_devices = set(), set()
    for row in rows:
        if row["action"] != "configure":
            continue
        identity = by_id.get(row["identification_request_id"])
        setup._require(identity is not None and identity["action"] == "identify" and all(
            identity[key] == row[key] for key in ("kit_name", "manifest_sha256", "node_id", "port", "device_digest")),
            "USB configuration receipt lost its exact identification binding.")
        setup._require(identity["declarations"] == row["declarations"], "USB configuration used declarations changed after identification.")
        setup._require(row["node_id"] not in used_nodes and row["device_digest"] not in used_devices,
                       "USB journal contains conflicting node/device assignments.")
        used_nodes.add(row["node_id"])
        used_devices.add(row["device_digest"])
    return rows


def _control_fence(root, expected):
    paused, revision = setup._controls(root)
    setup._require(type(expected) is int and 0 <= expected <= 2**53 - 1 and not paused and revision == expected,
                   "Owner pause/perception revision changed; the old USB intent is revoked.")


def preflight(name, node, port, *, root, _active=None, expected_control_revision=None):
    """Read-only readiness; safe before prompting for any credential."""
    _port_node(port, node)
    paused, revision = setup._controls(root)
    setup._require(not paused, "Autonomy is paused; physical onboarding is held.")
    if expected_control_revision is not None:
        _control_fence(root, expected_control_revision)
        setup._require(revision == expected_control_revision, "Owner controls changed during preflight.")
    kit = setup._verified_kit(root, name)
    rows = _ledger(root, name, active=_active)
    setup._require(all(row["manifest_sha256"] == kit["manifest_sha256"] for row in rows),
                   "USB journal belongs to a different firmware seal; review it before new actions.")
    setup._require(len(rows) < MAX_ATTEMPTS, "USB onboarding attempt retention bound reached.")
    kit = {**kit, "control_revision": revision}
    return kit, rows


def configuration_preflight(name, node, port, request_id, identification, expected_device, target_ip, target_port, *,
                            root, review_existing=False, _active=None, expected_control_revision=None):
    """Validate all non-secret intent before hidden prompts or a physical open."""
    kit, rows = preflight(name, node, port, root=root, _active=_active, expected_control_revision=expected_control_revision)
    output = _path(root, name, request_id)
    setup._require(_active is not None or not output.exists(), "This USB request ID already exists; no replay is allowed.")
    _device(expected_device)
    _ipv4(target_ip, private=True)
    setup._require(type(target_port) is int and 1 <= target_port <= 65535 and type(review_existing) is bool,
                   "Invalid receiver port or existing-configuration review flag.")
    identity = _receipt(root, _path(root, name, identification))
    setup._require(identity["action"] == "identify" and identity["node_id"] == node and identity["port"] == port and
                   identity["manifest_sha256"] == kit["manifest_sha256"] and identity["device_digest"] == expected_device,
                   "Configuration must bind the exact reviewed kit/node/port identification receipt.")
    setup._require(not identity["declarations"]["configured"] or review_existing is True,
                   "The identification declares existing configuration; explicit owner review is required.")
    for row in rows:
        if row["action"] == "configure":
            setup._require(row["node_id"] != node and row["device_digest"] != expected_device,
                           "This node ID or declared device is already assigned; review its existing configuration separately.")
    return kit, rows, identity


def _reserve(root, name, request_id, action, revision):
    path = setup._scope(root, _root(root, name) / "active-action.json")
    raw = setup._bytes({"schema": 1, "profile": setup.PROFILE, "request_id": request_id,
                      "action": action, "control_revision": revision,
                      "reservation": secrets.token_hex(16), "created_at": _utc()})
    try:
        setup._write(root, path, raw)
    except FileExistsError:
        raise OnboardingError("Another active or interrupted USB reservation holds new actions.") from None
    return raw, request_id


def _release(root, name, active):
    path = setup._scope(root, _root(root, name) / "active-action.json")
    with object.__new__(setup.ToolRegistry)._directory(path.parent) as directory:
        setup._require(setup._read(root, path, setup.MAX_JSON_BYTES) == active[0], "Owned USB reservation changed; it was not cleared.")
        if directory is None:
            path.unlink()
        else:
            os.unlink(path.name, dir_fd=directory)


def _declarations(value):
    setup._fields(value, {"chip", "version", "profile", "node_id", "target_ip", "target_port", "configured", "device_digest"}, "HELLO declarations")
    setup._require(value["chip"] == "esp32s3" and value["version"] == DEVICE_VERSION and value["profile"] == setup.PROFILE,
                   "Device does not declare the reviewed S3 firmware/profile.")
    setup._require(type(value["node_id"]) is int and 0 <= value["node_id"] <= 255 and
                   type(value["target_port"]) is int and 1 <= value["target_port"] <= 65535 and
                   type(value["configured"]) is bool,
                   "Invalid bounded HELLO declarations.")
    _device(value["device_digest"])
    _ipv4(value["target_ip"])
    return value


def _fields_line(raw, prefix):
    setup._require(type(raw) is bytes and len(raw) <= MAX_LINE and raw.endswith(b"\n"), "USB response exceeds its line bound or is incomplete.")
    try:
        text = raw[:-1].removesuffix(b"\r").decode("ascii")
    except UnicodeError:
        raise ValueError("USB protocol response is not bounded ASCII.") from None
    setup._require(text.startswith(prefix + " ") and all(32 <= ord(char) < 127 for char in text), "Unexpected USB protocol response.")
    result = {}
    for token in text[len(prefix) + 1:].split(" "):
        setup._require(token.count("=") == 1, "Invalid USB protocol field.")
        key, value = token.split("=", 1)
        setup._require(key and value and key not in result, "Duplicate or empty USB protocol field.")
        result[key] = value
    return result


def _response(transport, prefix, *, clock, check):
    deadline, total = clock() + RESPONSE_SECONDS, 0
    for _ in range(MAX_READ_LINES):
        check()
        setup._require(clock() <= deadline, "USB response deadline expired.")
        raw = transport.readline(MAX_LINE + 1, deadline)
        check()
        setup._require(type(raw) is bytes, "USB driver returned an unsupported response.")
        total += len(raw)
        setup._require(len(raw) <= MAX_LINE and total <= MAX_READ_BYTES, "USB response exceeds its retained/read bound.")
        if not raw:
            continue
        setup._require(raw.endswith(b"\n"), "USB response line was truncated.")
        if raw.startswith(b"RUVIEW_CONFIG_ERR_V1 "):
            # Never log an untrusted line, reason string or echoed credential.
            raise OnboardingError("Device rejected the configuration; no retry was attempted.")
        if raw.startswith(b"RUVIEW_"):
            setup._require(clock() <= deadline, "USB response deadline expired.")
            return _fields_line(raw, prefix)
        # Bounded boot diagnostics may precede a reply; they are discarded and
        # never persisted or displayed, because they may contain private data.
    raise OnboardingError("No matching USB response arrived within the line/deadline bounds.")


def _send(transport, raw):
    setup._require(type(raw) is bytes and len(raw) < MAX_LINE and raw.endswith(b"\n"), "USB request exceeds its protocol bound.")
    setup._require(transport.write(raw) == len(raw), "USB write was incomplete; no retry was attempted.")


def _hello(transport, *, clock, check):
    nonce, issued = secrets.token_hex(16), clock()
    check()
    _send(transport, ("RUVIEW_HELLO_V1 " + nonce + "\n").encode("ascii"))
    fields = _response(transport, "RUVIEW_HELLO_OK_V1", clock=clock, check=check)
    setup._require(set(fields) == _HELLO_FIELDS and fields["nonce"] == nonce, "USB HELLO nonce or field binding differs.")
    value = {key: fields[key] for key in ("chip", "version", "profile", "target_ip", "device_digest")}
    value.update(node_id=_number(fields["node_id"], 0, 255), target_port=_number(fields["target_port"], 1, 65535),
                 configured=bool(_number(fields["configured"], 0, 1)))
    return _declarations(value), nonce, issued


def _secret(value, minimum, maximum, label):
    setup._require(type(value) is str, "Invalid hidden " + label + ".")
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid hidden " + label + ".") from None
    setup._require(minimum <= len(raw) <= maximum and all(byte >= 32 and byte != 127 for byte in raw),
                   "Hidden " + label + " is outside the firmware's UTF-8 byte/printable bounds.")
    return raw


class _SerialLineTransport:
    """Optional pyserial 3.5 driver, created only after explicit physical arming.

    DTR/RTS are not deliberately pulsed; opening a USB serial port can still
    reset some hardware. That physical behavior remains unqualified until tested.
    """
    def __init__(self, port):
        _port_node(port, 1)
        import serial
        setup._require(getattr(serial, "VERSION", None) == "3.5", "The reviewed pyserial 3.5 environment is required.")
        driver = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=2)
        try:
            driver.dtr = False
            driver.rts = False
            driver.port = port
            driver.open()
        except BaseException:
            driver.close()
            raise
        self.driver = driver

    def write(self, raw):
        return self.driver.write(raw)

    def readline(self, maximum, deadline):
        value = bytearray()
        while len(value) < maximum and time.monotonic() <= deadline:
            byte = self.driver.read(1)
            if byte:
                value.extend(byte)
                if byte == b"\n":
                    break
        return bytes(value)

    def close(self):
        self.driver.close()


def _perform(action, name, node, port, request_id, *, root, armed, identification=None,
             expected_device=None, target_ip=None, target_port=None, ssid=None, password=None,
             review_existing=False, transport_factory=_SerialLineTransport, clock=time.monotonic,
             expected_control_revision=None):
    setup._require(armed is True, "Physical USB access requires its explicit command-line arm flag.")
    kit, rows = preflight(name, node, port, root=root, expected_control_revision=expected_control_revision)
    revision = kit["control_revision"]
    output = _path(root, name, request_id)
    setup._require(not output.exists(), "This USB request ID already exists; no replay is allowed.")
    if action == "configure":
        kit, rows, identity = configuration_preflight(name, node, port, request_id, identification, expected_device,
            target_ip, target_port, root=root, review_existing=review_existing, expected_control_revision=revision)
        ssid_bytes = _secret(ssid, 1, 32, "SSID")
        password_bytes = _secret(password, 8, 63, "password")
    else:
        setup._require(action == "identify", "Unsupported USB onboarding action.")
    attempt = {"schema": 1, "profile": setup.PROFILE, "request_id": request_id, "action": action, "status": "accepted",
        "kit_name": name, "manifest_sha256": kit["manifest_sha256"], "node_id": node, "port": port, "created_at": _utc(),
        "target_ip": target_ip, "target_port": target_port, "identification_request_id": identification,
        "expected_device_digest": expected_device, "existing_configuration_reviewed": review_existing,
        "control_revision": revision}
    active = _reserve(root, name, request_id, action, revision)
    try:
        with object.__new__(setup.ToolRegistry)._directory(output.parent):
            output.mkdir(mode=0o700)
    except BaseException:
        # The accepted reservation remains held if its attempt directory cannot
        # be created. Do not clear or adopt uncertain filesystem state.
        raise OnboardingError("USB reservation could not create its attempt; retained reservation requires review.") from None
    phase, transport = "reserve", None
    try:
        attempt_raw = setup._bytes(attempt)
        setup._write(root, output / "attempt.json", attempt_raw)
        phase = "reservation_recheck"
        if action == "configure":
            locked_kit, rows, identity = configuration_preflight(name, node, port, request_id, identification, expected_device,
                target_ip, target_port, root=root, review_existing=review_existing, _active=active,
                expected_control_revision=revision)
        else:
            locked_kit, rows = preflight(name, node, port, root=root, _active=active, expected_control_revision=revision)
        setup._require(locked_kit["manifest_sha256"] == kit["manifest_sha256"], "The sealed kit changed during USB reservation.")
        phase = "open_usb"
        _control_fence(root, revision)
        transport = transport_factory(port)
        phase = "hello"
        check = lambda: _control_fence(root, revision)
        hello, nonce, issued = _hello(transport, clock=clock, check=check)
        if action == "configure":
            setup._require(hello == identity["declarations"], "USB declarations changed after the reviewed identification; no configuration was written.")
            setup._require(not hello["configured"] or review_existing is True,
                           "Device already declares existing configuration; explicit owner review is required.")
            phase = "configuration_fence"
            setup._require(setup._verified_kit(root, name)["manifest_sha256"] == kit["manifest_sha256"],
                           "Controls or sealed kit changed before device configuration.")
            setup._require(0 <= clock() - issued < NONCE_TTL_SECONDS, "USB challenge expired before configuration; no retry was attempted.")
            frame = ("RUVIEW_CONFIG_V1 " + nonce + " " + str(node) + " " + target_ip + " " + str(target_port) + " " +
                base64.b64encode(ssid_bytes).decode("ascii") + " " + base64.b64encode(password_bytes).decode("ascii") + "\n").encode("ascii")
            phase = "configuration_write"
            check()
            _send(transport, frame)
            del frame, ssid_bytes, password_bytes
            phase = "configuration_ack"
            ack = _response(transport, "RUVIEW_CONFIG_OK_V1", clock=clock, check=check)
            setup._require(set(ack) == {"nonce", "node_id", "rebooting"} and ack["nonce"] == nonce and
                           _number(ack["node_id"], 1, 3) == node and ack["rebooting"] == "1",
                           "Configuration acknowledgement differs; outcome remains uncertain.")
        phase = "close_usb"
        transport.close()
        transport = None
        receipt = {key: attempt[key] for key in ("schema", "profile", "request_id", "action", "kit_name", "manifest_sha256",
            "node_id", "port", "created_at", "target_ip", "target_port", "identification_request_id", "control_revision")}
        receipt.update(status="completed", completed_at=_utc(), attempt_sha256=setup._sha(attempt_raw),
            device_digest=hello["device_digest"], declarations=hello, configuration_acknowledged=action == "configure",
            persistent_state_verified=False, hardware_qualified=False, rf_verified=False, local_credential_retention="none",
            source_identity="USB device declarations, not attestation")
        phase = "receipt"
        check()
        setup._write(root, output / "receipt.json", setup._bytes(receipt))
        phase = "release_reservation"
        _release(root, name, active)
        return {"status": "completed", "action": action, "request_id": request_id, "node_id": node,
            "device_digest": hello["device_digest"], "device_declarations_only": True,
            "existing_configuration_declared": hello["configured"], "configuration_acknowledged": action == "configure",
            "persistent_state_verified": False, "hardware_qualified": False, "rf_verified": False,
            "local_credential_retention": "none", "receipt": str((output / "receipt.json").relative_to(Path(root).absolute()))}
    except BaseException as error:
        # No raw protocol body, frame, credential, credential hash, diagnostic or
        # exception text is saved. A failed/uncertain operation is never retried.
        try:
            setup._write(root, output / "failure.json", setup._bytes({"schema": 1, "profile": setup.PROFILE,
                "status": "failed_or_uncertain", "request_id": request_id, "action": action, "phase": phase,
                "error_type": type(error).__name__, "automatic_retry": False, "local_credential_retention": "none"}))
        finally:
            if transport is not None:
                try:
                    transport.close()
                except BaseException:
                    pass
        raise OnboardingError("USB onboarding failed or its result is uncertain; inspect the retained failure phase. No retry occurred.") from None


def identify(name, node, port, request_id, *, root, open_usb=False, transport_factory=_SerialLineTransport, clock=time.monotonic,
             expected_control_revision=None):
    return _perform("identify", name, node, port, request_id, root=root, armed=open_usb,
                    transport_factory=transport_factory, clock=clock, expected_control_revision=expected_control_revision)


def configure(name, node, port, request_id, identification, expected_device, target_ip, target_port, ssid, password, *,
              root, apply_to_device=False, review_existing=False, transport_factory=_SerialLineTransport, clock=time.monotonic,
              expected_control_revision=None):
    return _perform("configure", name, node, port, request_id, root=root, armed=apply_to_device,
        identification=identification, expected_device=expected_device, target_ip=target_ip, target_port=target_port,
        ssid=ssid, password=password, review_existing=review_existing, transport_factory=transport_factory, clock=clock,
        expected_control_revision=expected_control_revision)


def status(name="xiao-three-node", *, root):
    """Passive bounded journal inspection, including holds; never opens USB."""
    try:
        paused, revision = setup._controls(root)
        kit = setup._verified_kit(root, name)
        rows = _ledger(root, name)
        setup._require(all(row["manifest_sha256"] == kit["manifest_sha256"] for row in rows), "USB journal firmware seal changed.")
    except (ValueError, OSError):
        return {"status": "held", "automatic_retry": False, "hardware_qualified": False, "rf_verified": False}
    return {"status": "paused" if paused else "ready_for_explicit_owner_action", "paused": paused,
        "control_revision": revision, "completed_attempts": len(rows),
        "identified_node_ids": sorted({row["node_id"] for row in rows if row["action"] == "identify"}),
        "acknowledged_node_ids": sorted({row["node_id"] for row in rows if row["action"] == "configure"}),
        "all_three_configuration_acknowledgements": {row["node_id"] for row in rows if row["action"] == "configure"} == {1, 2, 3},
        "persistent_state_verified": False, "hardware_qualified": False, "rf_verified": False,
        "device_declarations_only": True, "local_credential_retention": "none"}
