"""Offline binary/kit contracts using fictional firmware; never RF qualification."""
from contextlib import ExitStack, closing
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.request

from ariadne import ruview_setup as setup
from scripts import ruview_nodes


ROOT = Path(__file__).absolute().parents[1]


def image(*, chip=9, mode=2, flash=3, payload=b"TEST", digest=True):
    header = bytearray(24)
    header[:4] = bytes((0xE9, 1, mode, flash << 4))
    struct.pack_into("<H", header, 12, chip)
    header[23] = int(digest)
    raw = bytes(header) + struct.pack("<II", 0x3FC80000, len(payload)) + payload
    check_at = (len(raw) // 16 + 1) * 16 - 1
    checksum = 0xEF
    for value in payload:
        checksum ^= value
    raw += b"\0" * (check_at - len(raw)) + bytes((checksum,))
    return raw + hashlib.sha256(raw).digest() if digest else raw


def partitions(rows=setup.PARTITIONS):
    raw = b"".join(struct.pack("<HBBII16sI", 0x50AA, kind, subtype, offset, size, name.encode(), 0)
                   for name, kind, subtype, offset, size in rows)
    raw += b"\xEB\xEB" + b"\xFF" * 14 + hashlib.md5(raw).digest()
    return raw.ljust(0xC00, b"\xFF")


class RuViewSetupTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="ruview-setup-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "firmware-source"
        self.bundle.mkdir()
        self.payloads = {"bootloader.bin": image(), "partition-table.bin": partitions(),
                         "ota_data_initial.bin": b"\xFF" * 0x2000, "esp32-csi-node.bin": image(payload=b"FAKE")}
        for name, raw in self.payloads.items():
            (self.bundle / name).write_bytes(raw)
        self.manifest = {"schema": 1, "profile": setup.PROFILE,
            "source": {"repository": "https://github.com/ruvnet/RuView", "revision": setup.UPSTREAM_REVISION,
                       "archive_sha256": "a" * 64, "patch_sha256": "b" * 64},
            "build": {"esp_idf_version": "v5.4", "esp_idf_revision": "c" * 40,
                      "sdkconfig_sha256": "d" * 64, "recipe_sha256": "e" * 64, "tools_lock_sha256": "f" * 64},
            "target": {"chip": "esp32s3", "board": "seeed-xiao-esp32s3", "flash_bytes": setup.FLASH_BYTES,
                       "psram_bytes": setup.FLASH_BYTES, "headless": True, "mock": False,
                       "embedded_credentials": False, "nvs_image": False},
            "segments": [{"name": role, "path": name, "offset": offset,
                          "bytes": len(self.payloads[name]), "sha256": setup._sha(self.payloads[name])}
                         for role, name, offset, _ in setup.SEGMENTS]}
        self.reseal()

    def reseal(self):
        raw = setup._bytes(self.manifest)
        (self.bundle / setup.MANIFEST).write_bytes(raw)
        seal = self.root / setup.SEAL_PATH
        seal.parent.mkdir(parents=True, exist_ok=True)
        seal.write_bytes(setup._bytes({"schema": 1, "profile": setup.PROFILE, "source_revision": setup.UPSTREAM_REVISION,
            "manifest_sha256": setup._sha(raw), "qualification": "compiled_not_hardware_tested"}))

    def replace_segment(self, name, raw):
        (self.bundle / name).write_bytes(raw)
        item = next(row for row in self.manifest["segments"] if row["path"] == name)
        item.update(bytes=len(raw), sha256=setup._sha(raw))
        self.reseal()

    def test_complete_prepare_verify_status_plan_under_network_process_device_denial(self):
        with ExitStack() as stack:
            for target in ("socket.socket", "socket.create_connection", "urllib.request.urlopen", "subprocess.Popen", "os.system"):
                stack.enter_context(mock.patch(target, side_effect=AssertionError("Forbidden operation")))
            verified = setup.verify_bundle("firmware-source", root=self.root)
            self.assertTrue(verified["verified"])
            self.assertFalse(verified["hardware_qualified"])
            self.assertEqual(verified["target"]["chip"], "esp32s3")
            result = setup.prepare("firmware-source", root=self.root)
            self.assertEqual(result["status"], "completed")
            status = setup.status(root=self.root)
            plan = setup.flash_plan("xiao-three-node", 2, "COM5", root=self.root)
        self.assertTrue(status["verified"])
        self.assertFalse(status["ports_assigned"])
        self.assertFalse(plan["executed"])
        self.assertTrue(plan["node_id_must_be_provisioned_separately"])
        self.assertEqual(plan["node_id"], 2)
        self.assertTrue(all(command[0] == "<ESPTOOL_PYTHON>" for command in plan["argv"]))
        self.assertTrue(all("'COM5'" in command for command in plan["powershell"]))
        self.assertFalse(any("erase_flash" in command for command in plan["argv"]))
        kit = self.root / result["kit"]
        nodes = json.loads((kit / "nodes.json").read_bytes())
        self.assertEqual([node["node_id"] for node in nodes["nodes"]], [1, 2, 3])
        self.assertTrue(all(node["placement"] is None and node["usb_port"] is None and
                            node["hardware_identity"] is None for node in nodes["nodes"]))
        self.assertEqual(set(os.listdir(kit)), {"attempt.json", "firmware", "nodes.json", "flash-plan.json", "SETUP.html", "receipt.json"})

    def test_manifest_needs_the_fixed_source_seal_not_a_self_declared_hash(self):
        self.manifest["target"]["mock"] = True
        (self.bundle / setup.MANIFEST).write_bytes(setup._bytes(self.manifest))
        with self.assertRaisesRegex(ValueError, "source-distributed seal"):
            setup.verify_bundle(self.bundle, root=self.root)
        (self.root / setup.SEAL_PATH).unlink()
        with self.assertRaises(OSError):
            setup.verify_bundle(self.bundle, root=self.root)

    def test_resealed_wrong_chip_and_wrong_memory_and_mock_and_credentials_are_rejected(self):
        original = copy.deepcopy(self.manifest)
        for key, value in (("chip", "esp32c6"), ("board", "esp32-s3-zero"), ("flash_bytes", 4 * 1024**2),
                           ("psram_bytes", 2 * 1024**2), ("mock", True), ("headless", False),
                           ("embedded_credentials", True), ("nvs_image", True), ("headless", 1)):
            self.manifest = copy.deepcopy(original)
            self.manifest["target"][key] = value
            self.reseal()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                setup.verify_bundle(self.bundle, root=self.root)

    def test_segment_hash_mismatch_truncation_wrong_chip_header_flash_mode_and_appended_hash(self):
        original = image(payload=b"FAKE")
        (self.bundle / "esp32-csi-node.bin").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "sealed bytes"):
            setup.verify_bundle(self.bundle, root=self.root)
        invalid = [original[:-1], image(chip=13), image(mode=0), image(flash=2), original + b"unexpected"]
        broken = bytearray(original)
        broken[32] ^= 1
        invalid.append(bytes(broken))
        for raw in invalid:
            self.replace_segment("esp32-csi-node.bin", raw)
            with self.subTest(raw=raw[:16]), self.assertRaises(ValueError):
                setup.verify_bundle(self.bundle, root=self.root)

    def test_esp_image_without_appended_sha_is_distinguished_not_fabricated(self):
        self.replace_segment("bootloader.bin", image(digest=False))
        result = setup.verify_bundle(self.bundle, root=self.root)
        self.assertEqual([row["sha_appended"] for row in result["images"]], [False, True])

    def test_partition_md5_exact_layout_no_overlaps_and_empty_otadata(self):
        corrupt = bytearray(partitions())
        corrupt[240] ^= 1
        variants = [bytes(corrupt), partitions()[:-1], partitions() + b"x"]
        changed = list(setup.PARTITIONS)
        changed[3] = ("ota_0", 0, 0x10, 0x10000, 0x200000)
        variants.append(partitions(changed))
        for raw in variants:
            self.replace_segment("partition-table.bin", raw)
            with self.subTest(raw=raw[:16]), self.assertRaises(ValueError):
                setup.verify_bundle(self.bundle, root=self.root)
        self.replace_segment("partition-table.bin", partitions())
        self.replace_segment("ota_data_initial.bin", b"\x00" * 0x2000)
        with self.assertRaisesRegex(ValueError, "OTA initialization"):
            setup.verify_bundle(self.bundle, root=self.root)

    def test_segment_metadata_rejects_nvs_offsets_order_duplicate_and_boolean_lengths(self):
        original = copy.deepcopy(self.manifest)
        for change in (lambda x: x[0].update(offset=0x9000), lambda x: x[0].update(path="nvs.bin"),
                       lambda x: x[0].update(bytes=True), lambda x: x.reverse(), lambda x: x.append(x[0])):
            self.manifest = copy.deepcopy(original)
            change(self.manifest["segments"])
            self.reseal()
            with self.assertRaises(ValueError):
                setup.verify_bundle(self.bundle, root=self.root)

    def test_unknown_duplicate_and_oversized_json_are_rejected_before_acceptance(self):
        raw = (self.bundle / setup.MANIFEST).read_bytes()
        seal_path = self.root / setup.SEAL_PATH
        for malformed in (raw.replace(b'"schema": 1', b'"schema": 1, "schema": 1'), b" " * (setup.MAX_JSON_BYTES + 1)):
            (self.bundle / setup.MANIFEST).write_bytes(malformed)
            seal = json.loads(seal_path.read_bytes())
            seal["manifest_sha256"] = setup._sha(malformed)
            seal_path.write_bytes(setup._bytes(seal))
            with self.assertRaises(ValueError):
                setup.prepare(self.bundle, root=self.root)
        self.assertFalse((self.root / "data/workspace/ruview-kits").exists())
        self.reseal()
        self.manifest["private_password"] = "not-a-real-secret"
        self.reseal()
        with self.assertRaises(ValueError):
            setup.prepare(self.bundle, root=self.root)

    def test_extra_binary_or_file_is_not_silently_included(self):
        (self.bundle / "nvs.bin").write_bytes(b"not admitted")
        with self.assertRaises(ValueError):
            setup.prepare(self.bundle, root=self.root)
        self.assertFalse((self.root / "data/workspace/ruview-kits").exists())

    def test_paths_stay_inside_root_and_hardlinks_are_rejected(self):
        with self.assertRaises(ValueError):
            setup.verify_bundle(self.root.parent, root=self.root)
        hardlink = self.bundle / "linked.bin"
        os.link(self.bundle / "bootloader.bin", hardlink)
        with self.assertRaisesRegex(ValueError, "Hardlinked"):
            setup.verify_bundle(self.bundle, root=self.root)

    def test_windows_device_aliases_ads_and_ambiguous_names_are_rejected_before_any_io(self):
        values = ("COM1", "NUL.txt", "dir/COM¹", "LPT0", "AUX.log", "CONIN$", "CLOCK$", "dir./bundle",
                  "dir /bundle", "bundle:stream", "dir/../bundle", "dir\nname", "bad?name", r"\\.\COM5", "E:/private",
                  "C:folder", r"\\server\share\bundle", "COM1 .txt", "NUL .log")
        with mock.patch.object(setup, "_allowed_path", side_effect=AssertionError("Must reject before filesystem validation")), \
             mock.patch.object(Path, "lstat", side_effect=AssertionError("Must reject before lstat")):
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    setup.verify_bundle(value, root=self.root)

    def test_directory_name_collection_stops_at_the_bound_before_materializing_a_large_list(self):
        observed = []
        class ManyEntries:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def __iter__(self):
                for number in range(10000):
                    observed.append(number)
                    yield type("Entry", (), {"name": "item-" + str(number)})()
        with mock.patch.object(os, "scandir", return_value=ManyEntries()), self.assertRaises(ValueError):
            setup._entries(self.root, self.bundle, 5)
        self.assertEqual(len(observed), 6)

    def test_symlink_bundle_and_ancestor_are_rejected(self):
        link = self.root / "linked-firmware"
        try:
            link.symlink_to(self.bundle, target_is_directory=True)
        except OSError:
            self.skipTest("Windows symlink creation requires an unavailable privilege.")
        with self.assertRaises(ValueError):
            setup.verify_bundle(link, root=self.root)

    def test_pause_blocks_prepare_but_does_not_rewrite_controls(self):
        path = self.root / "data/ariadne.sqlite3"
        path.parent.mkdir()
        with closing(sqlite3.connect(path)) as database:
            database.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
            database.execute("INSERT INTO settings VALUES('paused','true')")
            database.commit()
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "paused"):
            setup.prepare(self.bundle, root=self.root)
        self.assertEqual(setup.status(root=self.root)["status"], "not_prepared")
        self.assertEqual(path.read_bytes(), before)

    def test_failed_or_interrupted_work_is_retained_and_never_replayed(self):
        original = setup._write
        def fail(root, path, raw):
            if Path(path).name == "nodes.json":
                raise OSError("Injected storage failure")
            return original(root, path, raw)
        with mock.patch.object(setup, "_write", side_effect=fail), self.assertRaises(OSError):
            setup.prepare(self.bundle, root=self.root)
        output = self.root / "data/workspace/ruview-kits/xiao-three-node"
        self.assertTrue((output / "attempt.json").is_file())
        self.assertFalse((output / "receipt.json").exists())
        self.assertEqual(setup.status(root=self.root)["status"], "failed_or_uncertain")
        before = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}
        with self.assertRaisesRegex(ValueError, "already exists"):
            setup.prepare(self.bundle, root=self.root)
        self.assertEqual(before, {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()})

    def test_completed_kit_reads_are_frozen_and_source_file_changes_cannot_change_kit(self):
        result = setup.prepare(self.bundle, root=self.root)
        (self.bundle / "esp32-csi-node.bin").write_bytes(b"changed original")
        self.assertTrue(setup.status(root=self.root)["verified"])
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        setup.flash_plan("xiao-three-node", 1, "COM9", root=self.root)
        with self.assertRaises(ValueError):
            setup.prepare(self.root / result["kit"] / "firmware", root=self.root)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_tampering_and_rehashed_noncanonical_node_claims_still_fail(self):
        result = setup.prepare(self.bundle, root=self.root)
        output = self.root / result["kit"]
        nodes = json.loads((output / "nodes.json").read_bytes())
        nodes["nodes"][0]["flashed"] = True
        raw = setup._bytes(nodes)
        (output / "nodes.json").write_bytes(raw)
        receipt = json.loads((output / "receipt.json").read_bytes())
        receipt["files"]["nodes.json"] = {"bytes": len(raw), "sha256": setup._sha(raw)}
        (output / "receipt.json").write_bytes(setup._bytes(receipt))
        with self.assertRaisesRegex(ValueError, "frozen workflow"):
            setup.status(root=self.root)

    def test_float_nonfinite_and_boolean_receipt_claims_are_not_equivalent_to_integers(self):
        result = setup.prepare(self.bundle, root=self.root)
        path = self.root / result["kit"] / "receipt.json"
        original = path.read_bytes()
        for old, new in ((b'"schema": 1', b'"schema": true'),
                         (b'"schema": 1', b'"schema": 1.0'),
                         (b'"schema": 1', b'"schema": 1e999'),
                         (b'"node_ids": [\n    1,', b'"node_ids": [\n    true,')):
            changed = original.replace(old, new)
            self.assertNotEqual(changed, original)
            path.write_bytes(changed)
            with self.assertRaises(ValueError):
                setup.status(root=self.root)

    def test_bad_pause_database_holds_preparation_without_creating_a_kit(self):
        path = self.root / "data/ariadne.sqlite3"
        path.parent.mkdir()
        path.write_bytes(b"not a SQLite database")
        with self.assertRaisesRegex(ValueError, "pause controls"):
            setup.prepare(self.bundle, root=self.root)
        self.assertFalse((self.root / "data/workspace/ruview-kits").exists())

    def test_manual_port_and_node_validation_cannot_inject_shell_or_open_any_device(self):
        for node in (True, 0, 4, "1"):
            with self.assertRaises(ValueError):
                setup.flash_plan("xiao-three-node", node, "COM5", root=self.root)
        for port in ("COM0", "com5", "COM5 & calc", "COM5; exit", r"\\.\COM5", "<PORT>", "COM10000"):
            with self.assertRaises(ValueError):
                setup.flash_plan("xiao-three-node", 1, port, root=self.root)

    def test_cli_uses_fixed_root_and_reports_holds_without_creating_a_database_or_token(self):
        output = io.StringIO()
        with mock.patch.object(ruview_nodes, "ROOT", self.root), mock.patch("sys.stdout", output):
            self.assertEqual(ruview_nodes.main(["verify", "firmware-source"]), 0)
            self.assertEqual(ruview_nodes.main(["prepare", "firmware-source"]), 0)
            self.assertEqual(ruview_nodes.main(["status"]), 0)
            self.assertEqual(ruview_nodes.main(["plan", "--node", "1", "--port", "COM5"]), 0)
            self.assertEqual(ruview_nodes.main(["prepare", "firmware-source"]), 1)
        self.assertIn('"status": "held"', output.getvalue())
        self.assertFalse((self.root / "data/ariadne.sqlite3").exists())
        self.assertFalse((self.root / "data/server.token").exists())


if __name__ == "__main__":
    unittest.main()
