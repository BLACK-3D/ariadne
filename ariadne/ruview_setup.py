"""Offline preparation of a sealed, headless three-node RuView firmware kit.

This module never opens a device, starts a process, acquires RF, configures a
network, provisions credentials or flashes firmware. Commands are inert plans.
The fixed source-distributed seal admits reviewed bytes; hashes and image-format
checks are not hardware, runtime configuration or sensing qualification.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import stat
import struct

from .atomic import publish_bytes
from .config import _allowed_path
from .tools import ToolRegistry

PROFILE = "ariadne-ruview-xiao-s3-headless-v1"
UPSTREAM_REVISION = "094e1400719ded1283c67d23b9c737f6738a3244"
SEAL_PATH = "config/ruview-xiao-firmware.json"
MANIFEST = "firmware-manifest.json"
ESPTOOL_CONFIG = b"[esptool]\n"
FLASH_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
SEGMENTS = (
    ("bootloader", "bootloader.bin", 0, 0x8000),
    ("partition_table", "partition-table.bin", 0x8000, 0x1000),
    ("otadata", "ota_data_initial.bin", 0xF000, 0x2000),
    ("application", "esp32-csi-node.bin", 0x20000, 0x200000),
)
# Exact reviewed 8 MB layout from the pinned partitions_display.csv. A headless
# build still uses this layout; its name does not enable a display.
PARTITIONS = (
    ("nvs", 1, 2, 0x9000, 0x6000),
    ("otadata", 1, 0, 0xF000, 0x2000),
    ("phy_init", 1, 1, 0x11000, 0x1000),
    ("ota_0", 0, 0x10, 0x20000, 0x200000),
    ("ota_1", 0, 0x11, 0x220000, 0x200000),
    ("spiffs", 1, 0x82, 0x420000, 0x1E0000),
    ("storage", 1, 0x81, 0x600000, 0x200000),
)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REV = re.compile(r"[0-9a-f]{40}\Z")
_NAME = re.compile(r"xiao-[a-z0-9][a-z0-9-]{0,42}\Z")
_PORT = re.compile(r"COM[1-9][0-9]{0,3}\Z")


def _require(value, message):
    if not value:
        raise ValueError(message)


def _fields(value, names, label):
    _require(type(value) is dict and set(value) == set(names), "Invalid " + label + " fields.")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _bytes(value):
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n").encode()


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, "Duplicate JSON field.")
        result[key] = value
    return result


def _json(raw):
    _require(type(raw) is bytes and len(raw) <= MAX_JSON_BYTES, "JSON input exceeds its bound.")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_float=lambda _: (_ for _ in ()).throw(ValueError("Setup JSON uses exact integers, not floats.")),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid JSON number.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Invalid bounded UTF-8 JSON.") from None


def _scope(root, path):
    # Reject Win32 device aliases/ADS/ambiguous names before lstat, resolve,
    # is_dir or any other operation can invoke filesystem name resolution.
    for value in (root, path):
        text = os.fspath(value)
        _require(type(text) is str and len(text) <= 4096, "Invalid setup path.")
        windows = PureWindowsPath(text)
        _require(windows.drive.lower() != "e:", "The E: drive is excluded.")
        _require(not windows.drive.startswith("\\") and (not windows.drive or windows.is_absolute()),
                 "UNC, device and drive-relative setup paths are forbidden.")
        for part in windows.parts:
            if part == windows.anchor:
                continue
            _require(part not in {".", ".."} and not part.endswith((" ", ".")) and
                     not any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF or
                             char in '<>:"|?*' for char in part), "Setup path contains an ambiguous or unsupported component.")
            base = part.split(".", 1)[0].rstrip(" ").upper()
            _require(base not in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"} and
                     not re.fullmatch(r"(?:COM|LPT)[0-9¹²³]", base), "Windows device aliases are forbidden in setup paths.")
    root = Path(os.path.abspath(root))
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    path = Path(os.path.abspath(path))
    _require(PureWindowsPath(str(root)).drive.lower() != "e:" and
             PureWindowsPath(str(path)).drive.lower() != "e:", "The E: drive is excluded.")
    _require(path.is_relative_to(root), "Setup paths must remain inside this Ariadne folder.")
    path = _allowed_path(path)
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        _require(not stat.S_ISREG(info.st_mode) or info.st_nlink == 1, "Hardlinked setup files are forbidden.")
    return path


def _read(root, path, maximum):
    path = _scope(root, path)
    guard = object.__new__(ToolRegistry)
    with guard._directory(path.parent) as directory:
        with os.fdopen(guard._open_read(path, directory), "rb") as source:
            before = os.fstat(source.fileno())
            _require(before.st_size <= maximum, "Setup input exceeds its bound.")
            raw = source.read(maximum + 1)
            after = os.fstat(source.fileno())
            _require(len(raw) <= maximum and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                     (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "Setup input changed while reading.")
    return raw


def _write(root, path, raw):
    path = _scope(root, path)
    guard = object.__new__(ToolRegistry)
    with guard._directory(path.parent, create=True):
        publish_bytes(path, raw)


def _entries(root, path, maximum):
    """Bound name collection before a selected directory can consume memory."""
    path = _scope(root, path)
    result = []
    with object.__new__(ToolRegistry)._directory(path) as directory:
        with os.scandir(path if directory is None else directory) as entries:
            for entry in entries:
                result.append(entry.name)
                _require(len(result) <= maximum, "Setup directory exceeds its entry bound.")
    return result


def _digest(value, label):
    _require(type(value) is str and _SHA.fullmatch(value), "Invalid " + label + " SHA-256.")


def _seal(root):
    seal = _json(_read(root, SEAL_PATH, MAX_JSON_BYTES))
    _fields(seal, {"schema", "profile", "source_revision", "manifest_sha256", "qualification"}, "firmware seal")
    _require(type(seal["schema"]) is int and seal["schema"] == 1 and seal["profile"] == PROFILE and
             seal["source_revision"] == UPSTREAM_REVISION and seal["qualification"] == "compiled_not_hardware_tested",
             "The fixed firmware seal is unsupported.")
    _digest(seal["manifest_sha256"], "sealed manifest")
    return seal


def _image(raw, role):
    """Parse unsigned ESP image framing, S3 chip ID, XOR checksum and optional SHA.

    Format reference: Espressif ESP-IDF v5.4 esp_app_format.h; checksum is XOR of
    segment payloads starting at 0xEF, stored at the end of a 16-byte block.
    This is a format check, not emulation or proof of embedded-code behavior.
    """
    _require(len(raw) >= 32 and raw[0] == 0xE9 and 1 <= raw[1] <= 16, "Invalid ESP image header.")
    _require(raw[2] == 2 and raw[3] >> 4 == 3 and raw[3] & 15 in {0, 1, 2, 15},
             "Firmware must use DIO and the reviewed 8 MB flash header.")
    _require(struct.unpack_from("<H", raw, 12)[0] == 9 and raw[23] in {0, 1}, "Firmware chip must be ESP32-S3.")
    position, checksum = 24, 0xEF
    for _ in range(raw[1]):
        _require(position + 8 <= len(raw), "Truncated ESP image segment header.")
        _, length = struct.unpack_from("<II", raw, position)
        position += 8
        _require(length % 4 == 0 and position + length <= len(raw), "Truncated or unaligned ESP image segment.")
        for byte in raw[position:position + length]:
            checksum ^= byte
        position += length
    check_at = (position // 16 + 1) * 16 - 1
    _require(check_at < len(raw) and raw[position:check_at] == b"\0" * (check_at - position) and
             raw[check_at] == checksum, "ESP image checksum or padding differs.")
    end = check_at + 1
    if raw[23]:
        _require(raw[end:end + 32] == hashlib.sha256(raw[:end]).digest(), "ESP image SHA-256 differs.")
        end += 32
    _require(end == len(raw), "ESP image has trailing, signed or unsupported bytes.")
    return {"role": role, "chip_id": 9, "segments": raw[1], "sha_appended": bool(raw[23])}


def _partitions(raw):
    _require(len(raw) in {0xC00, 0x1000}, "Unsupported ESP partition-table length.")
    rows, position = [], 0
    while position + 32 <= len(raw) and raw[position:position + 2] == b"\xAA\x50":
        magic, kind, subtype, offset, size, label, flags = struct.unpack_from("<HBBII16sI", raw, position)
        try:
            name = label.split(b"\0", 1)[0].decode("ascii")
        except UnicodeError:
            raise ValueError("Invalid partition name.") from None
        _require(label == name.encode().ljust(16, b"\0") and flags == 0, "Unsupported partition name or flags.")
        rows.append((name, kind, subtype, offset, size))
        position += 32
    _require(tuple(rows) == PARTITIONS, "Firmware partition layout differs from the reviewed 8 MB layout.")
    _require(raw[position:position + 16] == b"\xEB\xEB" + b"\xFF" * 14 and
             raw[position + 16:position + 32] == hashlib.md5(raw[:position]).digest(),
             "Partition-table MD5 is absent or differs.")
    _require(raw[position + 32:] == b"\xFF" * (len(raw) - position - 32), "Unexpected partition-table tail.")
    return [dict(zip(("name", "type", "subtype", "offset", "bytes"), row)) for row in rows]


def _bundle(root, bundle):
    bundle = _scope(root, bundle)
    _require(bundle.is_dir(), "Select the extracted firmware directory.")
    seal = _seal(root)
    raw_manifest = _read(root, bundle / MANIFEST, MAX_JSON_BYTES)
    _require(_sha(raw_manifest) == seal["manifest_sha256"], "Firmware manifest differs from the source-distributed seal.")
    manifest = _json(raw_manifest)
    _fields(manifest, {"schema", "profile", "source", "build", "target", "segments"}, "firmware manifest")
    _require(type(manifest["schema"]) is int and manifest["schema"] == 1 and manifest["profile"] == PROFILE,
             "Unsupported firmware manifest profile.")
    source, build, target = (manifest[key] for key in ("source", "build", "target"))
    _fields(source, {"repository", "revision", "archive_sha256", "patch_sha256"}, "source provenance")
    _require(source["repository"] == "https://github.com/ruvnet/RuView" and source["revision"] == UPSTREAM_REVISION,
             "Unsupported firmware source revision.")
    for key in ("archive_sha256", "patch_sha256"):
        _digest(source[key], key)
    _fields(build, {"esp_idf_version", "esp_idf_revision", "sdkconfig_sha256", "recipe_sha256", "tools_lock_sha256"}, "build provenance")
    _require(build["esp_idf_version"] == "v5.4" and type(build["esp_idf_revision"]) is str and
             _REV.fullmatch(build["esp_idf_revision"]), "Unsupported ESP-IDF build provenance.")
    for key in ("sdkconfig_sha256", "recipe_sha256", "tools_lock_sha256"):
        _digest(build[key], key)
    _require(target == {"chip": "esp32s3", "board": "seeed-xiao-esp32s3", "flash_bytes": FLASH_BYTES,
                       "psram_bytes": FLASH_BYTES, "headless": True, "mock": False,
                       "embedded_credentials": False, "nvs_image": False} and
             all(type(target[key]) is bool for key in ("headless", "mock", "embedded_credentials", "nvs_image")) and
             all(type(target[key]) is int for key in ("flash_bytes", "psram_bytes")),
             "Firmware target must be headless, non-mock, unprovisioned XIAO ESP32-S3 8/8 MB.")
    _require(type(manifest["segments"]) is list and len(manifest["segments"]) == 4, "Firmware needs exactly four flash segments.")
    files, images = {MANIFEST: raw_manifest}, []
    for item, (role, name, offset, maximum) in zip(manifest["segments"], SEGMENTS):
        _fields(item, {"name", "path", "offset", "bytes", "sha256"}, "firmware segment")
        _require(item["name"] == role and item["path"] == name and type(item["offset"]) is int and
                 item["offset"] == offset and type(item["bytes"]) is int and 0 < item["bytes"] <= maximum,
                 "Firmware segment path, offset or bound differs.")
        _digest(item["sha256"], "firmware segment")
        raw = _read(root, bundle / name, maximum)
        _require(len(raw) == item["bytes"] and _sha(raw) == item["sha256"], "Firmware segment differs from its sealed bytes.")
        files[name] = raw
        if role in {"bootloader", "application"}:
            images.append(_image(raw, role))
        elif role == "partition_table":
            partitions = _partitions(raw)
        else:
            _require(len(raw) == 0x2000 and raw == b"\xFF" * 0x2000, "OTA initialization must be the reviewed empty 8 KB image.")
    expected = set(files)
    _require(set(_entries(root, bundle, 5)) == expected, "Firmware directory contains missing or extra members.")
    result = {"profile": PROFILE, "verified": True, "manifest_sha256": seal["manifest_sha256"],
              "source_revision": UPSTREAM_REVISION, "target": target, "partitions": partitions,
              "images": images, "hardware_qualified": False, "device_opened": False,
              "flashed": False, "credentials_provisioned": False}
    return result, files


def verify_bundle(bundle, *, root):
    """Read and validate only; do not prepare a kit or invoke any device tooling."""
    return _bundle(root, bundle)[0]


def _nodes():
    return {"schema": 1, "nodes": [{"node_id": number, "label": "node-" + str(number),
            "placement": None, "usb_port": None, "hardware_identity": None,
            "flashed": False, "unique_node_id_provisioned": False, "csi_verified": False}
            for number in (1, 2, 3)], "wifi_credentials": "not_stored",
            "physical_qualification": "unknown", "all_three_unique_ids_required": True}


def _plan():
    return {"schema": 1, "profile": PROFILE, "execution": "manual_only_not_executed",
            "python_placeholder": "<ESPTOOL_PYTHON>", "port_placeholder": "<CONFIRMED_COM_PORT>",
            "working_directory": "kit directory", "node_ids": [1, 2, 3],
            "required_environment": {"ESPTOOL_CFGFILE": "esptool.cfg"},
            "esptool_config": {"path": "esptool.cfg", "bytes": len(ESPTOOL_CONFIG), "sha256": _sha(ESPTOOL_CONFIG)},
            "required_before_flash": ["Confirm the exact XIAO ESP32-S3 board and USB port.",
                "Read chip identity/revision and verify 8 MB flash with the reviewed esptool environment.",
                "Confirm first-install intent; preserve any existing firmware/private NVS separately."],
            "required_after_flash": ["Provision the selected unique node ID (1, 2 or 3) and Wi-Fi locally, without putting credentials in commands/logs.",
                "Verify real CSI packet yield before interpreting presence or motion.",
                "Qualify one node first, then three fixed placements with labeled held-out trials."],
            "segments": [{"path": "firmware/" + name, "offset": offset} for _, name, offset, _ in SEGMENTS],
            "hardware_qualified": False, "nvs_written": False, "erase_flash": False}


def _page(manifest_sha, name):
    text = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ariadne · Three XIAO RuView nodes</title><style>body{max-width:950px;margin:3rem auto;padding:0 1rem;background:#101919;color:#e9eee8;font:17px/1.6 system-ui}section{padding:1rem 1.5rem;margin:1rem 0;border:1px solid #52615a;border-radius:12px}code{overflow-wrap:anywhere;color:#cedba5}a{color:#b8d8ee}li{margin:.5rem 0}</style>
<h1>Three XIAO ESP32-S3 nodes</h1><p>Offline preparation complete · no device connected, flashed, provisioned or physically qualified by this workflow.</p>
<section><h2>1 · Check each board</h2><p>Use the plain Seeed XIAO ESP32-S3, 8 MB flash / 8 MB PSRAM, USB-C data cable and supplied antenna. Attach the antenna with power off. An existing 2.4 GHz access point and controlled local traffic are required. Headers, camera, microphone and a display are not needed for this pilot.</p></section>
<section><h2>2 · Start with one node</h2><p>Keep node IDs <strong>1, 2 and 3</strong> unique. All USB ports, hardware identities and placements are deliberately unset. The saved plan contains no Wi-Fi credentials. Confirm the board/port and first-install intent before a manual flash. A reused board may contain private state that must be reviewed separately.</p><p>Generate an inert, port-specific plan with <code>scripts/ruview_nodes.py plan --name KIT_NAME --node 1 --port COM5</code> from Ariadne. This prints commands; it does not execute them. Use the complete PowerShell plan, including its ESPTOOL_CFGFILE try/finally fence; bare argv omits that required isolation. The kit's hash-checked <code>esptool.cfg</code> contains only an empty [esptool] section, preventing inherited or home-directory tool configuration from redirecting work. The plan restores the previous process environment afterward without reading its referenced file. Replace the Python placeholder only with the separately reviewed esptool environment. Do not use an arbitrary online display-enabled firmware image.</p></section>
<section><h2>3 · Provision locally, then verify CSI</h2><p>After manually flashing the verified bytes, use the separately reviewed Python environment with pyserial 3.5. Replace the placeholders below with the physically confirmed port, owner-reviewed declared device digest, and intended private-LAN receiver. These are inert examples, not actions taken by this page.</p><p><code>scripts/ruview_onboard.py identify --name KIT_NAME --node 1 --port &lt;COM_PORT&gt; --request-id usb-node-1-id --open-usb</code></p><p>Inspect the exact identification receipt. Device digest, profile and version are declarations, not authentication or firmware attestation. Then explicitly configure the same identified board:</p><p><code>scripts/ruview_onboard.py configure --name KIT_NAME --node 1 --port &lt;COM_PORT&gt; --request-id usb-node-1-config --identification usb-node-1-id --expected-device &lt;DEVICE_DIGEST&gt; --target-ip &lt;RECEIVER_IPV4&gt; --target-port &lt;UDP_PORT&gt; --apply-to-device</code></p><p>The SSID and password are requested through hidden local prompts, never command arguments or local credential files. Configuration intentionally stores them in board NVS and requests a reboot. Ordinary USB transport and this pilot's NVS are not encrypted secret storage; protect the physical board and any later private NVS backup. No encryption/eFuse operation is part of this workflow.</p><p>Keep Wi-Fi passwords out of chat, source, command lines and public reports. A configuration acknowledgement is not persistent-state or RF verification. Verify packet yield, source IDs, source age and loss/outage behavior after reboot. A successful ping is not proof of CSI reception.</p></section>
<section><h2>4 · Add the other two nodes</h2><p>Repeat the identity, flash and unique-ID checks one board at a time. Choose fixed placements and record the actual arrangement. Compare occupied/empty, moving/still, interference and disconnected-source trials against labeled ground truth. More nodes do not establish pose, distance, accurate people counts or current occupancy by themselves.</p></section>
<section><h2>Evidence and limits</h2><p>Manifest SHA-256: <code>MANIFEST_SHA</code></p><p><a href="receipt.json">Preparation receipt</a> · <a href="nodes.json">Unset node worksheet</a> · <a href="flash-plan.json">Manual plan and gates</a></p><p>The source-distributed seal and format checks bind reviewed firmware bytes. They do not attest physical hardware, runtime provisioning, radio clocks, RF accuracy or truth. This kit includes no NVS image and writes no devices. Its setup commands are inert text. Failed or interrupted preparation is preserved and must not be replayed automatically.</p></section>"""
    return text.replace("MANIFEST_SHA", html.escape(manifest_sha)).replace("KIT_NAME", html.escape(name)).encode()


def _kit(root, name):
    _require(type(name) is str and _NAME.fullmatch(name), "Kit name must start xiao- and contain lowercase letters, digits and hyphens.")
    return _scope(root, Path("data/workspace/ruview-kits") / name)


def _controls(root):
    path = _scope(root, "data/ariadne.sqlite3")
    values = {}
    if path.exists():
        try:
            with object.__new__(ToolRegistry)._directory(path.parent):
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
                    # One statement sees a consistent pause/epoch pair. Opening
                    # Store/load_settings here would create/migrate owner state.
                    values = dict(database.execute("SELECT key,value FROM settings WHERE key IN ('paused','perception_revision')"))
        except sqlite3.Error:
            raise ValueError("Global pause controls could not be read; preparation is held.") from None
    paused = _json(values["paused"].encode()) if "paused" in values else False
    revision = _json(values["perception_revision"].encode()) if "perception_revision" in values else 0
    _require(type(paused) is bool and type(revision) is int and 0 <= revision <= 2**53 - 1,
             "Global pause or perception revision is invalid.")
    return paused, revision


def _paused(root):
    return _controls(root)[0]


def prepare(bundle, name="xiao-three-node", *, root):
    """Prepare one exclusive kit; uncertain/failed/completed names are never reused."""
    root = _scope(root, root)
    paused, revision = _controls(root)
    _require(not paused, "Autonomy is paused; new firmware-kit preparation is held.")
    verified, files = _bundle(root, bundle)
    _require(_controls(root) == (False, revision), "Owner controls changed before firmware-kit reservation.")
    output = _kit(root, name)
    _require(not output.exists(), "Kit already exists; inspect status without replaying preparation.")
    guard = object.__new__(ToolRegistry)
    with guard._directory(output.parent, create=True):
        output.mkdir(mode=0o700)
    try:
        _write(root, output / "attempt.json", _bytes({"schema": 1, "profile": PROFILE, "status": "accepted",
            "manifest_sha256": verified["manifest_sha256"], "control_revision": revision,
            "created_at": datetime.now(timezone.utc).isoformat()}))
        for filename, raw in files.items():
            _write(root, output / "firmware" / filename, raw)
        _write(root, output / "nodes.json", _bytes(_nodes()))
        _write(root, output / "flash-plan.json", _bytes(_plan()))
        _write(root, output / "esptool.cfg", ESPTOOL_CONFIG)
        _write(root, output / "SETUP.html", _page(verified["manifest_sha256"], name))
        retained = {"attempt.json", "nodes.json", "flash-plan.json", "esptool.cfg", "SETUP.html"} | {"firmware/" + name for name in files}
        receipt = {"schema": 1, "profile": PROFILE, "status": "completed", "manifest_sha256": verified["manifest_sha256"],
                   "node_ids": [1, 2, 3], "hardware_qualified": False, "flashed": False,
                   "credentials_provisioned": False, "network_used": False, "process_started": False,
                   "control_revision": revision,
                   "files": {name: {"bytes": len(raw := _read(root, output / name, FLASH_BYTES)), "sha256": _sha(raw)}
                             for name in sorted(retained)}}
        _require(_controls(root) == (False, revision), "Owner controls changed before firmware-kit completion.")
        _write(root, output / "receipt.json", _bytes(receipt))
        return {"status": "completed", "kit": str(output.relative_to(root)), "node_ids": [1, 2, 3],
                "manifest_sha256": verified["manifest_sha256"], "hardware_qualified": False,
                "flashed": False, "setup_page": str((output / "SETUP.html").relative_to(root))}
    except BaseException as error:
        _write(root, output / "failure.json", _bytes({"schema": 1, "status": "failed_or_uncertain",
            "error_type": type(error).__name__, "automatic_retry": False}))
        raise


def _verified_kit(root, name):
    output = _kit(root, name)
    receipt = _json(_read(root, output / "receipt.json", MAX_JSON_BYTES))
    _fields(receipt, {"schema", "profile", "status", "manifest_sha256", "node_ids", "hardware_qualified", "flashed",
                     "credentials_provisioned", "network_used", "process_started", "files", "control_revision"}, "kit receipt")
    _require(type(receipt["schema"]) is int and receipt["schema"] == 1 and receipt["profile"] == PROFILE and
             receipt["status"] == "completed" and receipt["node_ids"] == [1, 2, 3] and
             type(receipt["node_ids"]) is list and all(type(node) is int for node in receipt["node_ids"]) and
             all(receipt[key] is False for key in ("hardware_qualified", "flashed", "credentials_provisioned", "network_used", "process_started")),
             "Kit receipt contains unsupported completion claims.")
    _require(type(receipt["control_revision"]) is int and 0 <= receipt["control_revision"] <= 2**53 - 1,
             "Kit control revision is invalid.")
    verified, files = _bundle(root, output / "firmware")
    _require(receipt["manifest_sha256"] == verified["manifest_sha256"], "Kit manifest receipt differs.")
    expected = {"attempt.json", "nodes.json", "flash-plan.json", "esptool.cfg", "SETUP.html"} | {"firmware/" + name for name in files}
    _require(type(receipt["files"]) is dict and set(receipt["files"]) == expected, "Kit receipt inventory differs.")
    for filename, record in receipt["files"].items():
        _fields(record, {"bytes", "sha256"}, "kit artifact")
        raw = _read(root, output / filename, FLASH_BYTES)
        _require(type(record["bytes"]) is int and record["bytes"] == len(raw) and record["sha256"] == _sha(raw), "Kit artifact integrity differs.")
    _require(_read(root, output / "nodes.json", MAX_JSON_BYTES) == _bytes(_nodes()) and
             _read(root, output / "flash-plan.json", MAX_JSON_BYTES) == _bytes(_plan()) and
             _read(root, output / "esptool.cfg", MAX_JSON_BYTES) == ESPTOOL_CONFIG and
             _read(root, output / "SETUP.html", MAX_JSON_BYTES) == _page(verified["manifest_sha256"], name), "Kit worksheet or plan differs from the frozen workflow.")
    attempt = _json(_read(root, output / "attempt.json", MAX_JSON_BYTES))
    _fields(attempt, {"schema", "profile", "status", "manifest_sha256", "created_at", "control_revision"}, "kit attempt")
    _require(type(attempt["schema"]) is int and attempt["schema"] == 1 and attempt["profile"] == PROFILE and
             attempt["status"] == "accepted" and attempt["manifest_sha256"] == verified["manifest_sha256"] and
             type(attempt["control_revision"]) is int and attempt["control_revision"] == receipt["control_revision"] and
             type(attempt["created_at"]) is str and len(attempt["created_at"]) <= 40,
             "Kit attempt metadata differs.")
    _require(set(_entries(root, output, 7)) == {"attempt.json", "firmware", "nodes.json", "flash-plan.json", "esptool.cfg", "SETUP.html", "receipt.json"},
             "Kit has extra or uncertain artifacts.")
    return receipt


def status(name="xiao-three-node", *, root):
    output = _kit(root, name)
    if not output.exists():
        return {"status": "not_prepared", "hardware_qualified": False, "flashed": False}
    if not _scope(root, output / "receipt.json").exists():
        return {"status": "failed_or_uncertain" if _scope(root, output / "failure.json").exists() else "pending_or_uncertain",
                "automatic_retry": False, "hardware_qualified": False, "flashed": False}
    receipt = _verified_kit(root, name)
    return {"status": "completed", "verified": True, "manifest_sha256": receipt["manifest_sha256"],
            "node_ids": [1, 2, 3], "ports_assigned": False, "hardware_qualified": False, "flashed": False}


def flash_plan(name, node, port, *, root):
    """Return PowerShell-safe, deliberately inert manual commands, never run them."""
    _require(type(node) is int and node in {1, 2, 3} and type(port) is str and _PORT.fullmatch(port),
             "Choose node 1, 2 or 3 and an exact uppercase COM port, such as COM5.")
    receipt = _verified_kit(root, name)
    prefix = ["<ESPTOOL_PYTHON>", "-I", "-B", "-m", "esptool", "--chip", "esp32s3", "--port", port]
    addresses = [value for _, filename, offset, _ in SEGMENTS for value in (hex(offset), "firmware/" + filename)]
    commands = [prefix + ["chip_id"], prefix + ["flash_id"],
                prefix + ["--baud", "460800", "write_flash", "--flash_mode", "dio", "--flash_size", "8MB"] + addresses,
                prefix + ["verify_flash"] + addresses]
    config = _kit(root, name) / "esptool.cfg"
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    lines = ["$ariadneXiaoHadEspConfig = Test-Path Env:ESPTOOL_CFGFILE",
             "$ariadneXiaoSavedEspConfig = $env:ESPTOOL_CFGFILE", "try {",
             "    $env:ESPTOOL_CFGFILE = " + quote(str(config))]
    for index, command in enumerate(commands):
        if index == 2:
            confirmation = "FLASH-NODE-" + str(node)
            lines += ["    $ariadneXiaoFlashConfirm = Read-Host " + quote("Review the chip/flash results and this board. Type " + confirmation + " to write the reviewed first-install images"),
                      "    if ($ariadneXiaoFlashConfirm -cne " + quote(confirmation) + ") { throw 'First-install intent was not confirmed; no image write was requested.' }"]
        lines += ["    & " + " ".join(quote(part) for part in command),
                  "    if (-not $? -or $LASTEXITCODE -ne 0) { throw 'esptool stopped; inspect this attempt before any fresh action.' }"]
    lines += ["} finally {", "    if ($ariadneXiaoHadEspConfig) {",
              "        $env:ESPTOOL_CFGFILE = $ariadneXiaoSavedEspConfig", "    } else {",
              "        Remove-Item Env:ESPTOOL_CFGFILE -ErrorAction SilentlyContinue", "    }",
              "    Remove-Variable ariadneXiaoHadEspConfig,ariadneXiaoSavedEspConfig,ariadneXiaoFlashConfirm -ErrorAction SilentlyContinue", "}"]
    return {"status": "manual_plan_only", "kit": str(_kit(root, name).relative_to(Path(root).absolute())),
            "manifest_sha256": receipt["manifest_sha256"], "node_id": node, "port": port,
            "port_is_owner_supplied_not_detected": True, "device_opened": False, "executed": False,
            "node_id_must_be_provisioned_separately": True, "python_placeholder_must_be_resolved": True,
            "required_environment": {"ESPTOOL_CFGFILE": str(config)},
            "esptool_config_sha256": _sha(ESPTOOL_CONFIG),
            "argv": commands,
            "powershell": ["\n".join(lines)],
            "required_gates": _plan()["required_before_flash"], "required_after_flash": _plan()["required_after_flash"]}
