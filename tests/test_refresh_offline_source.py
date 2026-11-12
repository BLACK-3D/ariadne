"""Complete source refresh preserves immutable runtime bytes and fails closed."""
import copy
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ariadne import __version__
from scripts.export_offline import export
from scripts import refresh_offline_source as refresh
from tests.test_box_profiles import public_export_fixture


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class SourceRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".refresh-review-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.box = self.root / "box"
        for directory in ("data", "docs", "ariadne", "scripts", "runtime/python"):
            (self.box / directory).mkdir(parents=True, exist_ok=True)
        public_export_fixture(self.box)
        for name, raw in {
            "runtime/python/python.exe": b"synthetic qualified interpreter",
            "runtime/ollama/ollama.exe": b"synthetic native inference engine",
            "runtime/whisper/b5454/Release/whisper-cli.exe": b"synthetic native speech engine",
            "runtime/knowledge/python/bin/monty.exe": b"synthetic fixed native Monty",
            "runtime/production/qgis/SourceDir/QGIS/share/proj/proj.db": b"public immutable CRS database fixture",
            "data/voice/ggml-base.en.bin": b"synthetic voice weights",
            "ariadne/old.py": b"# removed in new audited source\n",
            "ariadne/agents.py": b"OLD AUDITED PROFILE\n",
            "scripts/provision.py": b"# immutable runtime recipe\n",
            "scripts/export_offline.py": b"# immutable packaging recipe\n",
            "requirements-core.txt": b"synthetic==1.0 --hash=sha256:fixture\n",
            "Start Ariadne.cmd": b"@echo off\r\nrem immutable launcher\r\n",
            "README.md": b"Old public documentation\n",
        }.items():
            path = self.box / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (self.box / "ariadne/__init__.py").write_text('__version__ = "' + __version__ + '"\n', encoding="utf-8")
        self.project = '[project]\nname = "fixture"\nversion = "' + __version__ + '"\nrequires-python = ">=3.13"\ndependencies = ["fixture==1"]\n'
        (self.box / "pyproject.toml").write_text(self.project, encoding="utf-8")
        (self.box / "runtime/python/PORTABLE-MANIFEST.json").write_text(json.dumps({"sha256": {
            "python.exe": digest((self.box / "runtime/python/python.exe").read_bytes())}}), encoding="utf-8")
        self.base = self.box / "dist/base.zip"
        export(self.base, self.box)
        with zipfile.ZipFile(self.base) as archive:
            self.base_members = {name: archive.read(name) for name in archive.namelist() if name != "BOX-MANIFEST.json"}
        self.source_members = {name: raw for name, raw in self.base_members.items() if refresh._source_member(name)}
        self.source_members.pop("ariadne/old.py")
        self.source_members.update({"ariadne/agents.py": b"NEW AUDITED PROFILE\n",
            "docs/new.md": b"New public notes\n", "README.md": b"New public documentation\n",
            "scripts/refresh_offline_source.py": b"# audited refresh utility addition\n"})
        self.source = self.root / "source.zip"
        self.output = self.root / "complete refreshed.zip"
        self.catalog_hash = digest(self.base_members["config/public-models.json"])
        self.addCleanup(patch.stopall)
        patch.object(refresh, "PUBLIC_CATALOG_SHA256", self.catalog_hash).start()
        self.write_source()

    def write_source(self, members=None, *, manifest_mutator=None, version=__version__):
        members = self.source_members if members is None else members
        manifest = {"version": version, "sha256": {name: digest(raw) for name, raw in members.items()}}
        if manifest_mutator:
            manifest_mutator(manifest)
        with zipfile.ZipFile(self.source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
            archive.writestr("SOURCE-MANIFEST.json", json.dumps(manifest))
        self.source.with_suffix(".zip.sha256").write_text(digest(self.source.read_bytes()) + "  " + self.source.name + "\n", encoding="ascii")

    def rewrite_base(self, *, member_mutator=None, manifest_mutator=None, info_mutator=None):
        with zipfile.ZipFile(self.base) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members.pop("BOX-MANIFEST.json"))
        if member_mutator:
            member_mutator(members)
        if manifest_mutator:
            manifest_mutator(manifest)
        members["BOX-MANIFEST.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(self.base, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, raw in members.items():
                info = refresh._entry(name)
                if info_mutator:
                    info_mutator(info)
                archive.writestr(info, raw)
        sha = digest(self.base.read_bytes())
        self.base.with_suffix(".zip.sha256").write_text(sha + "  " + self.base.name + "\n", encoding="ascii")
        self.base.with_suffix(".zip.receipt.json").write_text(json.dumps({"path": self.base.name,
            "application_version": manifest["application_version"], "sha256": sha, "bytes": self.base.stat().st_size,
            "files": len(members), "payload_bytes": manifest["payload_bytes"], "manifest": "BOX-MANIFEST.json"}), encoding="utf-8")

    def run_refresh(self):
        return refresh.refresh(self.base, self.source, self.output, root=self.root)

    def reseal_base_bytes(self, raw):
        receipt_path = self.base.with_suffix(".zip.receipt.json")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.base.write_bytes(raw)
        receipt.update(sha256=digest(raw), bytes=len(raw))
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.base.with_suffix(".zip.sha256").write_text(digest(raw) + "  " + self.base.name, encoding="ascii")

    def assert_unpublished(self):
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".zip.sha256").exists())
        self.assertFalse(self.output.with_suffix(".zip.receipt.json").exists())

    def test_complete_refresh_replaces_source_snapshot_preserves_all_native_bytes_and_verifies_all_hashes(self):
        before = {path: digest(path.read_bytes()) for path in (self.base, self.source)}
        with patch.object(refresh, "_copy", wraps=refresh._copy) as copied:
            receipt = self.run_refresh()
        self.assertEqual(copied.call_count, len(self.base_members) + len(self.source_members))
        with zipfile.ZipFile(self.output) as archive:
            self.assertNotIn("ariadne/old.py", archive.namelist())
            self.assertEqual(archive.read("ariadne/agents.py"), b"NEW AUDITED PROFILE\n")
            for name, raw in self.base_members.items():
                if not refresh._source_member(name):
                    self.assertEqual(archive.read(name), raw)
            manifest = json.loads(archive.read("BOX-MANIFEST.json"))
            self.assertTrue(manifest["source_refresh"]["all_old_members_verified"])
            self.assertEqual(manifest["source_refresh"]["source_archive_sha256"], before[self.source])
            self.assertEqual(receipt["application_version"], __version__)
            for row in manifest["files"]:
                self.assertEqual(row["sha256"], digest(archive.read(row["path"])))
            self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED and not info.extra for info in archive.infolist()))
        self.assertEqual(receipt["sha256"], digest(self.output.read_bytes()))
        self.assertEqual(before, {path: digest(path.read_bytes()) for path in before})

    def test_corrupt_replaced_or_deleted_old_source_is_still_rejected(self):
        self.rewrite_base(member_mutator=lambda members: members.update({"ariadne/old.py": b"X" * len(members["ariadne/old.py"])}))
        with self.assertRaisesRegex(ValueError, "SHA256/size"):
            self.run_refresh()
        self.assert_unpublished()
        self.assertTrue(self.output.with_suffix(".zip.partial").exists())

    def test_perception_source_refresh_includes_public_fixture_ui_and_guide_without_owner_data(self):
        public = {
            "ariadne/perception.py": b"# public deterministic replay\n",
            "ariadne/static/perception.js": b"// public recording review\n",
            "ariadne/resources/perception/demo.json": b'{"fictional":true,"observations":[]}\n',
            "docs/PERCEPTION_RECORDINGS.md": b"Public recording guide.\n",
        }
        self.write_source({**self.source_members, **public})
        self.run_refresh()
        with zipfile.ZipFile(self.output) as archive:
            rows = {row["path"]: row for row in json.loads(archive.read("BOX-MANIFEST.json"))["files"]}
            for name, raw in public.items():
                self.assertEqual(archive.read(name), raw)
                self.assertEqual(rows[name]["sha256"], digest(raw))
            for name, raw in self.base_members.items():
                if refresh._runtime_member(name):
                    self.assertEqual(archive.read(name), raw)
            self.assertFalse(any(name.startswith("data/perception/") for name in archive.namelist()))

    def test_perception_owner_recordings_cannot_be_admitted_as_source_or_runtime(self):
        for name in ("data/perception/recordings/owner.json", "data/perception/reports/replay.json",
                     "data/workspace/imports/recording.json"):
            self.assertFalse(refresh._source_member(name))
            self.assertFalse(refresh._runtime_member(name))
            self.write_source({**self.source_members, name: b"PRIVATE_RECORDING_SENTINEL"})
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "non-source replacement"):
                self.run_refresh()
            self.assert_unpublished()

    def test_corrupt_same_size_retained_native_is_rejected_during_stream(self):
        name = "runtime/ollama/ollama.exe"
        self.rewrite_base(member_mutator=lambda members: members.update({name: b"X" * len(members[name])}))
        with self.assertRaisesRegex(ValueError, "SHA256/size"):
            self.run_refresh()
        self.assert_unpublished()
        self.assertTrue(self.output.with_suffix(".zip.partial").exists())

    def test_changed_config_requirements_launch_or_provision_recipe_requires_reprovision(self):
        for name in ("config/box-profiles.json", "requirements-core.txt", "Start Ariadne.cmd", "scripts/provision.py", "scripts/export_offline.py"):
            with self.subTest(name=name):
                members = {**self.source_members, name: self.source_members[name] + b"\nCHANGED"}
                self.write_source(members)
                with self.assertRaisesRegex(ValueError, "recipes changed"):
                    self.run_refresh()
                self.assert_unpublished()

    def test_pyproject_dependency_requires_python_and_build_changes_are_not_source_only(self):
        for text in (self.project.replace("fixture==1", "fixture==2"), self.project.replace(">=3.13", ">=3.14"),
                     self.project + '\n[build-system]\nrequires = ["new-build"]\n'):
            with self.subTest(project=text):
                self.write_source({**self.source_members, "pyproject.toml": text.encode()})
                with self.assertRaisesRegex(ValueError, "runtime/build requirements changed"):
                    self.run_refresh()
                self.assert_unpublished()

    def test_missing_recipe_and_added_unknown_recipe_are_refused(self):
        for mode in ("missing", "added"):
            members = dict(self.source_members)
            if mode == "missing":
                members.pop("requirements-core.txt")
            else:
                members["scripts/provision_unreviewed.py"] = b"# new runtime recipe"
            self.write_source(members)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "recipes changed"):
                self.run_refresh()
            self.assert_unpublished()

    def test_private_source_runtime_weights_and_unknown_root_members_are_refused(self):
        for name in ("data/server.token", "runtime/ollama/ollama.exe", "config/private.env", "ariadne/private.sqlite3",
                     "ariadne/resources/model.gguf", "unreviewed-root.txt"):
            self.write_source({**self.source_members, name: b"PRIVATE_SENTINEL"})
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "non-source replacement"):
                self.run_refresh()
            self.assert_unpublished()

    def test_unknown_public_catalog_and_missing_portable_pin_are_refused(self):
        with patch.object(refresh, "PUBLIC_CATALOG_SHA256", "f" * 64):
            with self.assertRaisesRegex(ValueError, "Unknown public-model catalog"):
                self.run_refresh()
        self.rewrite_base(manifest_mutator=lambda manifest: next(row for row in manifest["files"] if row["path"] == "runtime/python/python.exe").update(sha256="a" * 64))
        with self.assertRaisesRegex(ValueError, "Portable interpreter pin"):
            self.run_refresh()
        self.assert_unpublished()

    def test_sidecar_and_receipt_mismatches_are_refused(self):
        self.source.with_suffix(".zip.sha256").write_text("a" * 64 + "  " + self.source.name, encoding="ascii")
        with self.assertRaisesRegex(ValueError, "SHA256 differs"):
            self.run_refresh()
        self.write_source()
        receipt = self.base.with_suffix(".zip.receipt.json")
        value = json.loads(receipt.read_text())
        value["bytes"] += 1
        receipt.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "receipt SHA256/size"):
            self.run_refresh()
        self.assert_unpublished()

    def test_boolean_manifest_payload_size_is_refused(self):
        self.rewrite_base(manifest_mutator=lambda manifest: manifest.update(payload_bytes=True))
        with self.assertRaisesRegex(ValueError, "payload size"):
            self.run_refresh()
        self.assert_unpublished()

    def test_duplicate_json_keys_nonfinite_values_and_boolean_schema_fail_closed(self):
        for raw in (b'{"schema":1,"schema":1}', b'{"v":NaN}', b'{"v":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                refresh._json(raw)
        self.rewrite_base(manifest_mutator=lambda manifest: manifest.update(schema=True))
        with self.assertRaisesRegex(ValueError, "schema"):
            self.run_refresh()
        self.assert_unpublished()

    def test_version_update_is_consistent_and_other_runtime_project_fields_stay_fixed(self):
        members = {**self.source_members, "ariadne/__init__.py": b'__version__ = "0.10.1"\n',
            "pyproject.toml": self.project.replace(__version__, "0.10.1").encode()}
        self.write_source(members, version="0.10.1")
        receipt = self.run_refresh()
        self.assertEqual(receipt["application_version"], "0.10.1")
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(json.loads(archive.read("BOX-MANIFEST.json"))["application_version"], "0.10.1")

    def test_source_version_disagreement_is_rejected(self):
        self.write_source(manifest_mutator=lambda manifest: manifest.update(version="9.9.9"))
        with self.assertRaisesRegex(ValueError, "version differs"):
            self.run_refresh()
        self.assert_unpublished()

    def test_no_runtime_filesystem_traversal_is_used_during_complete_refresh(self):
        with patch("os.walk", side_effect=AssertionError("No original runtime traversal")), \
             patch("os.scandir", side_effect=AssertionError("No original runtime traversal")):
            receipt = self.run_refresh()
        self.assertTrue(receipt["source_refresh"]["all_old_members_verified"])

    def test_unknown_owner_fields_in_box_manifest_are_rejected(self):
        self.rewrite_base(manifest_mutator=lambda manifest: manifest.update(owner_notes="PRIVATE_SENTINEL"))
        with self.assertRaisesRegex(ValueError, "schema"):
            self.run_refresh()
        self.assert_unpublished()

    def test_unknown_model_blob_cannot_be_smuggled_through_a_self_consistent_box_manifest(self):
        name, raw = "data/models/blobs/sha256-" + "f" * 64, b"PRIVATE_MODEL_SENTINEL"
        def pin(manifest):
            manifest["files"].append({"path": name, "bytes": len(raw), "sha256": digest(raw)})
            manifest["payload_bytes"] += len(raw)
        self.rewrite_base(member_mutator=lambda members: members.update({name: raw}), manifest_mutator=pin)
        with self.assertRaisesRegex(ValueError, "Unreviewed model member"):
            self.run_refresh()
        self.assert_unpublished()

    def test_unicode_output_name_is_refused_before_writing_a_complete_archive(self):
        path = self.root / "refreshed-é.zip"
        with self.assertRaisesRegex(ValueError, "ASCII"):
            refresh.refresh(self.base, self.source, path, root=self.root)
        self.assertFalse(path.exists())

    def test_actual_zip_crc_corruption_is_rejected_even_with_resealed_archive_checksum(self):
        raw = self.base.read_bytes()
        payload = b"synthetic native inference engine"
        self.assertEqual(raw.count(payload), 1)
        self.reseal_base_bytes(raw.replace(payload, b"X" * len(payload)))
        with self.assertRaises(zipfile.BadZipFile):
            self.run_refresh()
        self.assert_unpublished()

    def test_actual_nul_truncated_zip_name_is_refused_before_manifest_processing(self):
        self.write_source({**self.source_members, "ariadne/aXhidden.py": b"safe text"})
        raw = self.source.read_bytes()
        self.assertEqual(raw.count(b"ariadne/aXhidden.py"), 2)
        raw = raw.replace(b"ariadne/aXhidden.py", b"ariadne/a\x00hidden.py")
        self.source.write_bytes(raw)
        self.source.with_suffix(".zip.sha256").write_text(digest(raw) + "  " + self.source.name, encoding="ascii")
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.run_refresh()
        self.assert_unpublished()

    def test_missing_upstream_notice_cannot_be_adopted_as_a_complete_runtime(self):
        name = "runtime/whisper/LICENSE.SDL2"
        def remove_pin(manifest):
            manifest["files"] = [row for row in manifest["files"] if row["path"] != name]
            manifest["payload_bytes"] -= len(self.base_members[name])
        self.rewrite_base(member_mutator=lambda members: members.pop(name), manifest_mutator=remove_pin)
        with self.assertRaisesRegex(ValueError, "notice pin is absent"):
            self.run_refresh()
        self.assert_unpublished()

    def test_owner_node_red_flow_cannot_be_smuggled_in_a_runtime_tree(self):
        name, raw = "runtime/automation/node-red/flows.json", b"PRIVATE_OWNER_FLOW"
        def pin(manifest):
            manifest["files"].append({"path": name, "bytes": len(raw), "sha256": digest(raw)})
            manifest["payload_bytes"] += len(raw)
        self.rewrite_base(member_mutator=lambda members: members.update({name: raw}), manifest_mutator=pin)
        with self.assertRaisesRegex(ValueError, "private appliance member"):
            self.run_refresh()
        self.assert_unpublished()

    def test_source_manifest_missing_member_or_wrong_pin_is_refused(self):
        for mutator in (lambda value: value["sha256"].pop("README.md"),
                        lambda value: value["sha256"].update({"README.md": "0" * 64})):
            self.write_source(manifest_mutator=mutator)
            with self.subTest(mutator=mutator), self.assertRaises(ValueError):
                self.run_refresh()
            self.assert_unpublished()
            partial = self.output.with_suffix(".zip.partial")
            if partial.exists():
                partial.unlink()  # Exact disposable fixture artifact, never a release/input.

    def test_unsafe_windows_and_unicode_collision_names_are_refused(self):
        for name in ("../escape.py", "/abs.py", "C:/abs.py", "ariadne/a\\b.py", "ariadne/NUL.txt", "ariadne/CONIN$",
                     "ariadne/trailing. ", "ariadne/file:stream", "ariadne//a.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                refresh.member_name(name)
        for names in (("ariadne/A.py", "ariadne/a.py"), ("ariadne/é.py", "ariadne/e\u0301.py"), ("ariadne/a", "ariadne/a/b.py")):
            with self.subTest(names=names), self.assertRaises(ValueError):
                refresh._names([refresh._entry(name) for name in names], source=True)

    def test_nul_truncated_filename_encryption_and_symlink_metadata_are_refused(self):
        info = refresh._entry("ariadne/safe.py")
        info.orig_filename = "ariadne/safe.py\x00hidden"
        with self.assertRaisesRegex(ValueError, "truncated"):
            refresh._names([info, refresh._entry("README.md")], source=True)
        info = refresh._entry("ariadne/safe.py")
        info.flag_bits = 1
        with self.assertRaisesRegex(ValueError, "Encrypted"):
            refresh._names([info, refresh._entry("README.md")], source=True)
        info.flag_bits = 0
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(ValueError, "regular"):
            refresh._names([info, refresh._entry("README.md")], source=True)

    def test_input_alias_existing_output_sidecar_and_partial_never_overwrite(self):
        original = self.base.read_bytes()
        with self.assertRaisesRegex(ValueError, "aliases"):
            refresh.refresh(self.base, self.source, self.base, root=self.root)
        self.assertEqual(self.base.read_bytes(), original)
        for path in (self.output, self.output.with_suffix(".zip.sha256"), self.output.with_suffix(".zip.partial")):
            path.write_bytes(b"preserved fixture")
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "never overwrites"):
                self.run_refresh()
            self.assertEqual(path.read_bytes(), b"preserved fixture")
            path.unlink()

    def test_source_size_limits_and_workspace_escape_fail_before_publication(self):
        with patch.object(refresh, "MAX_SOURCE_PAYLOAD", 1):
            with self.assertRaisesRegex(ValueError, "Source payload limit"):
                self.run_refresh()
        with self.assertRaisesRegex(ValueError, "workspace"):
            refresh.refresh(self.base, self.source, self.root.parent / "escape.zip", root=self.root)
        self.assert_unpublished()

    def test_input_identity_change_after_verification_is_not_published(self):
        original = refresh._copy
        changed = False
        def alter_source(*args, **kwargs):
            nonlocal changed
            result = original(*args, **kwargs)
            if not changed:
                changed = True
                info = self.source.stat()
                import os
                os.utime(self.source, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
            return result
        with patch.object(refresh, "_copy", side_effect=alter_source):
            with self.assertRaisesRegex(ValueError, "changed during refresh"):
                self.run_refresh()
        self.assert_unpublished()


if __name__ == "__main__":
    unittest.main()
