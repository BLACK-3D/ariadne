"""Offline pack admission fails before writes for tampered or unexpected input."""
from pathlib import Path
import hashlib
import json
import stat
import tempfile
import unittest
import warnings
from unittest.mock import patch
import zipfile

from scripts import install_vision_pack as installer
from scripts.provision_vision import wheel_members
from scripts.provision_vision import assets, catalog, exact_runtime_inventory


class VisionPackAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".vision-pack-test-", dir=Path.cwd() / "staging")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payloads = {"runtime/vision/python/module.py": b"# reviewed public runtime\n",
                         "runtime/vision/manifest.json": b'{"reviewed":"synthetic fixture"}\n'}
        self.expected = {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in self.payloads.items()}
        self.patcher = patch.object(installer, "expected_files", return_value=self.expected)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def archive(self, changes=None, *, extras=None, link=False, duplicate=False):
        path = self.root / "pack.zip"
        values = {**self.payloads, **(changes or {}), **(extras or {})}
        manifest = {"schema": 1, "profile": "ariadne-offline-vision-v1", "application_version": "0.39.0",
                    "private_state_included": False,
                    "files": [{"path": name, **row} for name, row in self.expected.items()]}
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in values.items():
                info = zipfile.ZipInfo(name)
                info.external_attr = ((stat.S_IFLNK if link else stat.S_IFREG) | 0o644) << 16
                archive.writestr(info, raw)
            if duplicate:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr("runtime/vision/manifest.json", self.payloads["runtime/vision/manifest.json"])
            archive.writestr("VISION-PACK-MANIFEST.json", json.dumps(manifest))
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def assert_no_runtime_writes(self):
        self.assertFalse((self.root / "runtime").exists())

    def test_valid_offline_install_publishes_readiness_last_and_never_overwrites(self):
        path, sha = self.archive()
        calls = []
        original = installer.commit_staged
        def record(stage, target):
            calls.append(target.relative_to(self.root).as_posix())
            original(stage, target)
        with patch.object(installer, "commit_staged", side_effect=record):
            result = installer.install(path, sha, root=self.root)
        self.assertTrue(result["installed"])
        self.assertEqual(calls[-1], "runtime/vision/manifest.json")
        for name, raw in self.payloads.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
        self.assertEqual(installer.install(path, sha, root=self.root)["files_written"], 0)

    def test_archive_checksum_mismatch_has_no_runtime_writes(self):
        path, _ = self.archive()
        with self.assertRaisesRegex(ValueError, "supplied SHA"):
            installer.install(path, "0" * 64, root=self.root)
        self.assert_no_runtime_writes()

    def test_member_hash_mismatch_has_no_runtime_writes(self):
        original = self.payloads["runtime/vision/python/module.py"]
        path, sha = self.archive({"runtime/vision/python/module.py": b"X" * len(original)})
        with self.assertRaisesRegex(ValueError, "shipped hash"):
            installer.install(path, sha, root=self.root)
        self.assert_no_runtime_writes()

    def test_private_extra_linked_and_duplicate_paths_have_no_runtime_writes(self):
        for options in ({"extras": {"data/vision-private.json": b"PRIVATE_OWNER_SENTINEL"}}, {"link": True}, {"duplicate": True}):
            with self.subTest(options=options):
                path, sha = self.archive(**options)
                with self.assertRaises(ValueError):
                    installer.install(path, sha, root=self.root)
                self.assert_no_runtime_writes()

    def test_existing_uncertain_asset_blocks_all_new_publication(self):
        path, sha = self.archive()
        target = self.root / "runtime/vision/python/module.py"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"uncertain existing bytes")
        with self.assertRaisesRegex(FileExistsError, "uncertain"):
            installer.install(path, sha, root=self.root)
        self.assertEqual(target.read_bytes(), b"uncertain existing bytes")
        self.assertFalse((self.root / "runtime/vision/manifest.json").exists())

    def test_unsealed_existing_bootstrap_blocks_install_before_publication(self):
        path, sha = self.archive()
        target = self.root / "runtime/vision/python/cv2/config-3.13.py"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"# unreviewed executable config\n")
        with self.assertRaisesRegex(ValueError, "Unsealed"):
            installer.install(path, sha, root=self.root)
        self.assertFalse((self.root / "runtime/vision/python/module.py").exists())
        self.assertFalse((self.root / "runtime/vision/manifest.json").exists())

    def test_wheel_traversal_is_rejected_before_extraction(self):
        path = self.root / "hostile.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../escape.py", b"untrusted")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            list(wheel_members(path))
        self.assertFalse((self.root.parent / "escape.py").exists())

    def test_unsealed_python_bootstrap_is_rejected(self):
        target = self.root / "runtime/vision/python/cv2/config-3.13.py"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"raise RuntimeError('unsealed code must never run')")
        with self.assertRaisesRegex(ValueError, "Unsealed"):
            exact_runtime_inventory(self.root, set())

    def test_corresponding_source_is_required_but_compiler_is_not_exported(self):
        value = catalog()
        names = {item["path"] for item in assets(value)}
        source_inputs = [item for item in value["decoder_build"]["inputs"] if item["role"] == "source"]
        self.assertTrue(source_inputs)
        for item in source_inputs:
            self.assertIn(item["path"], names)
        self.assertIn("runtime/vision/sources/ffmpeg-9.0.2.tar.xz", names)
        self.assertTrue(any(name.endswith("sources/build/ffmpeg/ffmpeg-src-n7.1.tar.xz") for name in names))
        self.assertFalse(any("build-cache/" in name or "llvm-mingw" in Path(name).name for name in names))
        # A source-role build input cannot silently disappear if catalog layout changes.
        value["assets"] = [item for item in value["assets"] if item["path"] not in {source["path"] for source in source_inputs}]
        self.assertTrue({item["path"] for item in source_inputs} <= {item["path"] for item in assets(value)})


if __name__ == "__main__":
    unittest.main()
