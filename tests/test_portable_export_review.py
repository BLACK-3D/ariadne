"""Factory archives must contain pinned public assets, never owner model state."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from ariadne import __version__
from scripts.export_offline import export, inventory, plan, _public_model_files
from tests.test_box_profiles import public_export_fixture
from scripts.stage_runtime_notices import stage


class PortableExportReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".portable-export-review-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for relative in ("docs", "ariadne", "scripts", "data", "runtime/python", "runtime/ollama"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        (self.root / "runtime/python/python.exe").write_bytes(b"synthetic interpreter")
        (self.root / "runtime/python/PORTABLE-MANIFEST.json").write_text(json.dumps({"sha256": {
            "python.exe": hashlib.sha256(b"synthetic interpreter").hexdigest()}}), encoding="utf-8")
        (self.root / "runtime/ollama/ollama.exe").write_bytes(b"synthetic inference engine")
        public_export_fixture(self.root)
        self.output = self.root / "dist/box.zip"

    def catalog(self):
        return json.loads((self.root / "config/public-models.json").read_text(encoding="utf-8"))

    def write_catalog(self, value):
        (self.root / "config/public-models.json").write_text(json.dumps(value), encoding="utf-8")

    def test_unknown_model_metadata_names_and_contents_do_not_enter_actual_archive(self):
        for relative in ("data/models/manifests/registry.ollama.ai/owner/private-company/model",
                         "data/models/manifests-v2/owner.example/private-owner/model",
                         "data/models/metadata/private-project.json", "data/models/blobs/sha256-unreferenced-private"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"PRIVATE_FACTORY_EXPORT_SENTINEL")
        receipt = export(self.output, self.root)
        self.assertEqual(receipt["application_version"], __version__)
        with zipfile.ZipFile(self.output) as archive:
            self.assertFalse(any("private" in path for path in archive.namelist()))
            self.assertFalse(any(b"PRIVATE_FACTORY_EXPORT_SENTINEL" in archive.read(path) for path in archive.namelist()))
            manifest = json.loads(archive.read("BOX-MANIFEST.json"))
            self.assertEqual(manifest["application_version"], __version__)
            self.assertEqual(set(manifest["public_model_tags"]), {"qwen3.5:9b", "qwen3:8b", "qwen3:4b"})
            self.assertEqual(sum(path.startswith("data/models/manifests/") for path in archive.namelist()), 3)
            self.assertEqual(sum(path.startswith("data/models/blobs/") for path in archive.namelist()), 3)

    def test_fast_voice_pack_keeps_repair_assets_but_excludes_local_pip_metadata(self):
        public = {
            "config/speech-assets.json": b'{"schema":1,"assets":[]}',
            "docs/FAST_VOICE_0_11.md": b"Offline speech repair instructions.",
            "runtime/media/piper/en_GB-cori-medium.onnx": b"synthetic public speech model",
            "runtime/media/piper/wheels/piper-test.whl": b"synthetic verified wheel cache",
            "runtime/media/piper/notices/Piper-COPYING": b"retained upstream notice",
            "runtime/media/piper-python/piper/voice.py": b"# synthetic installed runtime\n",
        }
        excluded = {
            "runtime/media/piper-python/piper_tts-1.8.0.dist-info/direct_url.json",
            "runtime/media/piper-python/bin/piper.exe",
        }
        for name, payload in {**public, **{name: b"PRIVATE_LOCAL_BUILD_PATH_SENTINEL" for name in excluded}}.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        export(self.output, self.root)
        with zipfile.ZipFile(self.output) as archive:
            self.assertTrue(set(public) <= set(archive.namelist()))
            self.assertFalse(excluded & set(archive.namelist()))
            for name, payload in public.items():
                self.assertEqual(archive.read(name), payload)
            self.assertFalse(any(b"PRIVATE_LOCAL_BUILD_PATH_SENTINEL" in archive.read(name) for name in archive.namelist()))

    def test_independent_host_controls_and_design_docs_survive_factory_export_without_host_state(self):
        public = {
            "Start Ariadne.cmd": b"@echo off\r\nrem synthetic start\r\n",
            "Stop Ariadne.cmd": b"@echo off\r\nrem synthetic stop\r\n",
            "Backup Ariadne.cmd": b"@echo off\r\nrem synthetic checked backup\r\n",
            "scripts/host.py": b"# fixed background host\n",
            "scripts/windows_host.py": b"# local process identity and detach\n",
            "scripts/stop_host.ps1": b"# explicit local shutdown\n",
            "scripts/backup_box.py": b"# checked private owner snapshot\n",
            "scripts/backup_box.ps1": b"# synchronous private backup launcher\n",
            "docs/INDEPENDENT_HOST_0_12.md": b"Background start and stop instructions.",
            "docs/SIGNAL_ROOM_0_12.md": b"Offline interface design decisions.",
            "docs/DAILY_SIGNAL_0_12.md": b"One-page local briefing instructions.",
            "docs/CHECKED_BACKUPS_0_13.md": b"Private backup verification and recovery limits.",
            "config/repository-watchlist.json": b'{"schema":1,"repositories":[]}',
            "docs/REPOSITORY_WATCH_0_14.md": b"Bounded daily upstream metadata watch.",
        }
        for name, payload in public.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        private = self.root / "data/host/status.json"
        private.parent.mkdir(parents=True)
        private.write_bytes(b"PRIVATE_HOST_CONTROL_EVENT_AND_PROCESS_STATE")
        (self.root / "data/repository-radar-cache.json").write_bytes(b"PRIVATE_REPOSITORY_WATCH_BASELINE")
        export(self.output, self.root)
        with zipfile.ZipFile(self.output) as archive:
            for name, payload in public.items():
                self.assertEqual(archive.read(name), payload)
            self.assertFalse(any(name.startswith("data/host/") for name in archive.namelist()))
            self.assertFalse(any(b"PRIVATE_HOST_CONTROL_EVENT_AND_PROCESS_STATE" in archive.read(name) for name in archive.namelist()))
            self.assertNotIn("data/repository-radar-cache.json", archive.namelist())
            self.assertFalse(any(b"PRIVATE_REPOSITORY_WATCH_BASELINE" in archive.read(name) for name in archive.namelist()))

    def test_changed_reviewed_public_manifest_refuses_before_archive_publication(self):
        path = self.root / self.catalog()["models"][0]["manifest"]["path"]
        original = path.read_bytes()
        path.write_bytes(original.replace(b"layers", b"LayeRs"))
        with self.assertRaisesRegex(ValueError, "manifest or metadata changed"):
            export(self.output, self.root)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".zip.sha256").exists())

    def test_catalog_change_after_plan_is_rejected_during_stream_before_publication(self):
        def change_after_plan(root):
            report = plan(root)
            catalog = root / "config/public-models.json"
            catalog.write_bytes(catalog.read_bytes().replace(b"https://ollama.com", b"https://OLlama.com"))
            return report
        with patch("scripts.export_offline.plan", side_effect=change_after_plan):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                export(self.output, self.root)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".zip.sha256").exists())

    def test_missing_referenced_blob_refuses_without_a_finished_archive(self):
        digest = self.catalog()["models"][0]["blobs"][0]["sha256"]
        (self.root / ("data/models/blobs/sha256-" + digest)).unlink()
        with self.assertRaises((OSError, ValueError)):
            export(self.output, self.root)
        self.assertFalse(self.output.exists())

    def test_same_size_changed_blob_fails_stream_hash_and_keeps_only_named_partial(self):
        digest = self.catalog()["models"][0]["blobs"][0]["sha256"]
        path = self.root / ("data/models/blobs/sha256-" + digest)
        original = path.read_bytes()
        path.write_bytes(b"X" * len(original))
        self.assertEqual(plan(self.root)["missing_required"], [])  # Planning does not double-read multi-GB blobs.
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            export(self.output, self.root)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".zip.sha256").exists())
        self.assertTrue(self.output.with_suffix(".zip.partial").is_file())

    def test_catalog_cannot_add_an_unreferenced_private_blob_or_omit_default_model(self):
        original = self.catalog()
        altered = json.loads(json.dumps(original))
        altered["models"][0]["blobs"].append({"sha256": "a" * 64, "bytes": 17})
        self.write_catalog(altered)
        with self.assertRaisesRegex(ValueError, "do not match"):
            _public_model_files(self.root)
        altered = json.loads(json.dumps(original))
        altered["models"] = altered["models"][1:]
        self.write_catalog(altered)
        with self.assertRaises(ValueError):
            _public_model_files(self.root)

    def test_missing_catalog_fails_closed_instead_of_exporting_owner_model_tree(self):
        (self.root / "config/public-models.json").unlink()
        with self.assertRaises((OSError, ValueError)):
            inventory(self.root)

    def test_unreviewed_metadata_path_cannot_be_added_even_with_a_valid_local_hash(self):
        catalog = self.catalog()
        private = self.root / "data/owner.txt"
        private.write_bytes(b"private fixture")
        catalog["models"][0]["metadata"].append({"path": "data/owner.txt", "bytes": 15,
            "sha256": hashlib.sha256(private.read_bytes()).hexdigest()})
        self.write_catalog(catalog)
        with self.assertRaisesRegex(ValueError, "metadata path"):
            _public_model_files(self.root)

    def test_exact_notices_and_provenance_are_retained_generated_extraction_log_is_excluded(self):
        for engine in ("7zip", "qgis", "kicad"):
            log = self.root / ("runtime/production/" + engine + "/extract.log")
            log.parent.mkdir(parents=True)
            log.write_bytes(b"PRIVATE_OWNER_INSTALLATION_PATH")
        export(self.output, self.root)
        catalog = json.loads((self.root / "config/runtime-notices/catalog.json").read_text(encoding="utf-8"))
        with zipfile.ZipFile(self.output) as archive:
            for engine in ("7zip", "qgis", "kicad"):
                self.assertNotIn("runtime/production/" + engine + "/extract.log", archive.namelist())
            self.assertIn("config/public-models.json", archive.namelist())
            for asset in catalog["assets"]:
                for prefix in ("config/runtime-notices/", "runtime/"):
                    payload = archive.read(prefix + asset["path"])
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), asset["sha256"])
            self.assertIn("runtime/ollama/NOTICE-PROVENANCE.json", archive.namelist())
            self.assertIn("runtime/whisper/NOTICE-PROVENANCE.json", archive.namelist())
            self.assertNotIn(b"PRIVATE_OWNER_INSTALLATION_PATH", b"".join(archive.read(path) for path in archive.namelist()))

    def test_changed_or_missing_upstream_notice_refuses_publication(self):
        path = self.root / "runtime/ollama/LICENSE"
        original = path.read_bytes()
        path.write_bytes(original.replace(b"MIT", b"BAD", 1))
        with self.assertRaisesRegex(ValueError, "notice is missing or changed"):
            export(self.output, self.root)
        self.assertFalse(self.output.exists())

    def test_local_optional_pip_metadata_is_excluded_while_other_installed_metadata_is_preserved(self):
        for prefix in ("runtime/automation/python", "runtime/production/python", "runtime/production/blender-python"):
            path = self.root / prefix / "fixture.dist-info/direct_url.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"PRIVATE_OWNER_INSTALLATION_PATH")
            (path.parent / "METADATA").write_bytes(b"Public distribution metadata")
        export(self.output, self.root)
        with zipfile.ZipFile(self.output) as archive:
            self.assertFalse(any(name.endswith("/direct_url.json") for name in archive.namelist()))
            self.assertEqual(sum(name.endswith("fixture.dist-info/METADATA") for name in archive.namelist()), 3)
            self.assertNotIn(b"PRIVATE_OWNER_INSTALLATION_PATH", b"".join(archive.read(path) for path in archive.namelist()))

    def test_unused_optional_console_wrappers_are_excluded_but_fixed_monty_and_core_scripts_remain(self):
        for prefix in ("knowledge/python", "media/python", "media/flux-python", "production/python",
                       "production/blender-python", "automation/python"):
            path = self.root / "runtime" / prefix / "bin/unused-wrapper.exe"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"PRIVATE_UNUSED_BUILD_INTERPRETER_PATH")
        monty = self.root / "runtime/knowledge/python/bin/monty.exe"
        monty.write_bytes(b"fixed native runner")
        core = self.root / "runtime/python/Scripts/pip.exe"
        core.parent.mkdir()
        core.write_bytes(b"qualified core launcher retained unchanged")
        export(self.output, self.root)
        with zipfile.ZipFile(self.output) as archive:
            self.assertFalse(any(name.endswith("unused-wrapper.exe") for name in archive.namelist()))
            self.assertEqual(archive.read("runtime/knowledge/python/bin/monty.exe"), b"fixed native runner")
            self.assertEqual(archive.read("runtime/python/Scripts/pip.exe"), b"qualified core launcher retained unchanged")
            self.assertNotIn(b"PRIVATE_UNUSED_BUILD_INTERPRETER_PATH", b"".join(archive.read(path) for path in archive.namelist()))

    def test_local_notice_staging_restores_only_missing_accepted_files_and_is_idempotent(self):
        target = self.root / "runtime/whisper/LICENSE.SDL2"
        expected = target.read_bytes()
        target.unlink()
        result = stage(self.root)
        self.assertEqual(result["created"], ["runtime/whisper/LICENSE.SDL2"])
        self.assertFalse(result["network_used"])
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual(stage(self.root)["created"], [])

    def test_notice_staging_refuses_owner_conflict_before_copying_any_missing_file(self):
        missing = self.root / "runtime/ollama/LICENSE"
        missing.unlink()
        conflicting = self.root / "runtime/whisper/LICENSE.SDL2"
        conflicting.write_bytes(b"PRIVATE_OWNER_NOTICE_CONFLICT")
        with self.assertRaisesRegex(ValueError, "no files were replaced"):
            stage(self.root)
        self.assertFalse(missing.exists())
        self.assertEqual(conflicting.read_bytes(), b"PRIVATE_OWNER_NOTICE_CONFLICT")


if __name__ == "__main__":
    unittest.main()
