import hashlib
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from ariadne.box_profiles import bootstrap_profiles, make_template
from ariadne.store import Store
from scripts.export_offline import export, inventory, plan, write_template


def public_export_fixture(root):
    """Three tiny public-tag fixtures, plus exact source/runtime notices."""
    models = []
    for tag in ("qwen3.5:9b", "qwen3:8b", "qwen3:4b"):
        raw = ("public fixture " + tag).encode()
        digest = hashlib.sha256(raw).hexdigest()
        blob = root / ("data/models/blobs/sha256-" + digest)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(raw)
        descriptor = {"digest": "sha256:" + digest, "size": len(raw)}
        manifest = json.dumps({"schemaVersion": 2, "config": descriptor, "layers": [descriptor]}).encode()
        path = "data/models/manifests/registry.ollama.ai/library/" + tag.replace(":", "/")
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(manifest)
        models.append({"tag": tag, "source_url": "https://ollama.com/library/" + tag,
            "manifest": {"path": path, "sha256": hashlib.sha256(manifest).hexdigest(), "bytes": len(manifest)},
            "metadata": [], "blobs": [{"sha256": digest, "bytes": len(raw)}]})
    (root / "config").mkdir(exist_ok=True)
    (root / "config/public-models.json").write_text(json.dumps({"schema": 1, "default_model": "qwen3.5:9b", "models": models}), encoding="utf-8")
    source = Path(__file__).resolve().parents[1] / "config/runtime-notices"
    shutil.copytree(source, root / "config/runtime-notices")
    catalog = json.loads((source / "catalog.json").read_text(encoding="utf-8"))
    for asset in catalog["assets"]:
        target = root / "runtime" / asset["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / asset["path"], target)
    for scope in ("ollama", "whisper"):
        (root / "runtime" / scope / "NOTICE-PROVENANCE.json").write_text(json.dumps({"schema": 1,
            "assets": [asset for asset in catalog["assets"] if asset["path"].startswith(scope + "/")]}), encoding="utf-8")


class BoxProfileTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / "runtime/tmp"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="portable-test-", dir=base)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "original box"
        (self.root / "data").mkdir(parents=True)
        (self.root / "docs").mkdir()
        (self.root / "ariadne").mkdir()
        (self.root / "scripts").mkdir()
        for name in ("runtime/python/python.exe", "runtime/python/PORTABLE-MANIFEST.json", "runtime/ollama/ollama.exe",
                     "runtime/whisper/whisper.exe", "data/voice/ggml-base.en.bin"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture " + name.encode())
        (self.root / "runtime/python/PORTABLE-MANIFEST.json").write_text(json.dumps({"sha256": {
            "python.exe": hashlib.sha256((self.root / "runtime/python/python.exe").read_bytes()).hexdigest()}}), encoding="utf-8")
        voice = {"whisper_bin": str(self.root / "runtime/whisper/whisper.exe"), "whisper_model": str(self.root / "data/voice/ggml-base.en.bin")}
        (self.root / "data/voice-config.json").write_text(json.dumps(voice), encoding="utf-8")
        (self.root / "README.md").write_text("Portable appliance fixture", encoding="utf-8")
        public_export_fixture(self.root)

    def test_relocated_profiles_use_new_root_and_new_database_only(self):
        template = write_template(self.root)
        self.assertNotIn(str(self.root), json.dumps(template))
        relocated = Path(self.temporary.name) / "relocated box"
        shutil.copytree(self.root, relocated)
        (relocated / "data/voice-config.json").unlink()
        result = bootstrap_profiles(relocated)
        self.assertTrue(result["seeded_database"])
        copied = json.loads((relocated / "data/voice-config.json").read_text(encoding="utf-8"))
        self.assertEqual(copied["whisper_bin"], str(relocated / "runtime/whisper/whisper.exe"))
        self.assertEqual(Store(relocated / "data/ariadne.sqlite3").setting("model"), "qwen3.5:9b")
        self.assertFalse((relocated / "data/server.token").exists())
        self.assertFalse(result["factory_flows"])

    def test_existing_owner_profiles_and_database_are_preserved(self):
        write_template(self.root)
        existing = self.root / "data/voice-config.json"
        existing.write_text('{"whisper_bin":"owner setting"}', encoding="utf-8")
        store = Store(self.root / "data/ariadne.sqlite3")
        store.set_setting("model", "owner-selected-model")
        result = bootstrap_profiles(self.root)
        self.assertFalse(result["seeded_database"])
        self.assertEqual(existing.read_text(encoding="utf-8"), '{"whisper_bin":"owner setting"}')
        self.assertEqual(store.setting("model"), "owner-selected-model")

    def test_credentials_external_paths_and_unsupported_profiles_are_rejected(self):
        path = self.root / "data/voice-config.json"
        for value in ({"token": "private"}, {"whisper_model": "E:/reserved/model.bin"}, {"whisper_model": "C:/outside/model.bin"}):
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_template(self.root)

    def test_nested_media_credentials_and_provenance_query_secrets_are_rejected(self):
        for key in ("access_token", "hf_token", "api_key"):
            media = {"version": 1, "backends": {"silero": {"assets": {"model": {"path": "runtime/media/model.onnx", "sha256": "a" * 64}}, key: "PRIVATE_SENTINEL"}}}
            target = self.root / "data/media-config.json"
            target.write_text(json.dumps(media), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(ValueError):
                make_template(self.root)
            target.unlink()
        knowledge = {"schema": 1, "kiwix": {"mode": "managed", "download": {"url": "https://example.com/archive?token=PRIVATE_SENTINEL"}}}
        (self.root / "data/knowledge-config.json").write_text(json.dumps(knowledge), encoding="utf-8")
        with self.assertRaises(ValueError):
            make_template(self.root)

    def test_template_cannot_interpolate_parent_escape(self):
        template = write_template(self.root)
        template["profiles"]["voice-config.json"]["whisper_bin"] = "${BOX}/../outside.exe"
        (self.root / "config/box-profiles.json").write_text(json.dumps(template), encoding="utf-8")
        with self.assertRaises(ValueError):
            bootstrap_profiles(self.root)
        self.assertFalse((self.root / "data/ariadne.sqlite3").exists())

    def test_export_includes_runtimes_models_and_pins_but_excludes_private_state(self):
        for name in ("data/server.token", "data/ariadne.sqlite3", "data/automation-config.json", "data/workspace/private.txt",
                     "backups/private.zip", "runtime/tmp/private.txt", "runtime/automation/browser-profile/Cookies",
                     "runtime/automation/owner-manifest.json", "config/private-manifest.json", "data/voice/private-recording.wav"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"PRIVATE_SENTINEL")
        (self.root / "scripts/media-license-assets.json").write_text('{"files":[]}', encoding="utf-8")
        output = self.root / "dist/box.zip"
        receipt = export(output, self.root)
        self.assertEqual(receipt["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        with zipfile.ZipFile(output) as archive:
            self.assertIn("runtime/python/python.exe", archive.namelist())
            self.assertEqual(sum(name.startswith("data/models/blobs/") for name in archive.namelist()), 3)
            self.assertIn("scripts/media-license-assets.json", archive.namelist())
            self.assertFalse(any("PRIVATE_SENTINEL" in archive.read(name).decode("utf-8", errors="ignore") for name in archive.namelist()))
            manifest = json.loads(archive.read("BOX-MANIFEST.json"))
            for entry in manifest["files"]:
                self.assertEqual(entry["sha256"], hashlib.sha256(archive.read(entry["path"])).hexdigest())
            fresh = self.root / "dist/extracted"
            archive.extractall(fresh)
        result = bootstrap_profiles(fresh)
        self.assertTrue(result["seeded_database"])
        self.assertFalse((fresh / "data/server.token").exists())
        with self.assertRaises(FileExistsError):
            export(output, self.root)

    def test_inventory_refuses_hardlinks(self):
        source = self.root / "ariadne/source.py"
        source.write_text("pass", encoding="utf-8")
        try:
            os.link(source, self.root / "ariadne/linked.py")
        except OSError:
            self.skipTest("Hard-link creation unavailable")
        with self.assertRaisesRegex(ValueError, "hard-linked"):
            inventory(self.root)

    def test_perception_source_resources_and_guide_are_exported_without_owner_recordings(self):
        public = {
            ".gitattributes": b"* -text\n",
            "ariadne/perception.py": b"# public recording replay implementation\n",
            "ariadne/static/perception.js": b"// public recording review interface\n",
            "ariadne/resources/perception/demo.json": b'{"fictional":true,"observations":[]}\n',
            "docs/PERCEPTION_RECORDINGS.md": b"Public recording format and evidence limits.\n",
        }
        private = ("data/perception/recordings/owner.json", "data/perception/events.jsonl",
                   "data/perception/reports/replay.json", "data/workspace/imports/recording.json")
        for name, raw in {**public, **{name: b"PRIVATE_PERCEPTION_SENTINEL" for name in private}}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        output = self.root / "dist/perception.zip"
        export(output, self.root)
        with zipfile.ZipFile(output) as archive:
            rows = {row["path"]: row for row in json.loads(archive.read("BOX-MANIFEST.json"))["files"]}
            for name, raw in public.items():
                self.assertEqual(archive.read(name), raw)
                self.assertEqual(rows[name]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertFalse(set(private) & set(archive.namelist()))
            self.assertTrue(all(b"PRIVATE_PERCEPTION_SENTINEL" not in archive.read(name) for name in archive.namelist()))

    def test_export_never_overwrites_existing_sidecar(self):
        output = self.root / "dist/box.zip"
        output.parent.mkdir()
        sidecar = output.with_suffix(".zip.sha256")
        sidecar.write_text("PRIVATE_SENTINEL", encoding="ascii")
        with self.assertRaises(FileExistsError):
            export(output, self.root)
        self.assertEqual(sidecar.read_text(encoding="ascii"), "PRIVATE_SENTINEL")
        self.assertFalse(output.exists())

    def test_export_preserves_complete_python_qualification_file_set(self):
        script = self.root / "runtime/python/Scripts/pip.exe"
        script.parent.mkdir()
        script.write_bytes(b"qualified launcher")
        manifest_path = self.root / "runtime/python/PORTABLE-MANIFEST.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sha256"]["Scripts/pip.exe"] = hashlib.sha256(script.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report = plan(self.root)
        self.assertEqual(report["missing_required"], [])
        output = self.root / "dist/box.zip"
        export(output, self.root)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read("runtime/python/Scripts/pip.exe"), b"qualified launcher")

    def test_public_erp_recipe_and_current_review_documents_are_included(self):
        for relative in ("config/erpnext/compose.yaml", "config/erpnext/images.json", "config/erpnext/private.env",
                         "docs/DELIVERY_0_4.md", "docs/REVIEW_PORTABLE_PYTHON_0_4.md"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public fixture" if not relative.endswith(".env") else "PRIVATE_SENTINEL", encoding="utf-8")
        paths = {name for name, _, _ in inventory(self.root)}
        self.assertIn("config/erpnext/compose.yaml", paths)
        self.assertIn("config/erpnext/images.json", paths)
        self.assertIn("docs/DELIVERY_0_4.md", paths)
        self.assertIn("docs/REVIEW_PORTABLE_PYTHON_0_4.md", paths)
        self.assertNotIn("config/erpnext/private.env", paths)

    def test_ocr_uses_portable_interpreter_in_factory_profile(self):
        (self.root / "data/ocr-config.json").write_text(json.dumps({"schema": 1, "python": str(self.root / ".venv/Scripts/python.exe"),
            "models": {}, "packages": {}, "processor": {}, "lock_sha256": "a" * 64}), encoding="utf-8")
        template = make_template(self.root)
        self.assertEqual(template["profiles"]["ocr-config.json"]["python"], "${BOX}/runtime/python/python.exe")

    def test_fresh_box_prepares_only_fixed_flows_with_new_private_token(self):
        for name in ("runtime/automation/node-v24.22.1-win-x64/node.exe", "runtime/automation/node-red/node_modules/node-red/red.js"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("factory asset", encoding="utf-8")
        shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/local_services.py", self.root / "scripts/local_services.py")
        write_template(self.root)
        first = bootstrap_profiles(self.root)
        self.assertTrue(first["factory_flows"])
        token = (self.root / "data/flows.token").read_text(encoding="ascii")
        self.assertGreaterEqual(len(token), 40)
        config = json.loads((self.root / "data/automation-config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["apps"], [])
        self.assertIsNone(config["erpnext"])
        self.assertEqual({x["id"] for x in config["flows"]}, {"flows_health", "local_event"})
        self.assertFalse((self.root / "data/flows/service.pid").exists())
        second = bootstrap_profiles(self.root)
        self.assertFalse(second["factory_flows"])
        self.assertEqual((self.root / "data/flows.token").read_text(encoding="ascii"), token)

    def _fresh_factory_fixture(self):
        for name in ("runtime/automation/node-v24.22.1-win-x64/node.exe", "runtime/automation/node-red/node_modules/node-red/red.js"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"factory asset")
        shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/local_services.py", self.root / "scripts/local_services.py")
        write_template(self.root)
        (self.root / "data/voice-config.json").unlink()

    def test_every_first_launch_checkpoint_recovers_without_partial_final_files(self):
        self._fresh_factory_fixture()
        discovery = Path(self.temporary.name) / "event discovery"
        shutil.copytree(self.root, discovery)
        events = []
        with mock.patch("ariadne.box_profiles._checkpoint", side_effect=events.append):
            bootstrap_profiles(discovery)
        self.assertIn("database:committed", events)
        self.assertIn("automation-config.json:committed", events)
        for index, stop in enumerate(events):
            with self.subTest(stop=stop):
                target = Path(self.temporary.name) / ("interrupted " + str(index))
                shutil.copytree(self.root, target)

                def interrupt(stage):
                    if stage == stop:
                        raise InterruptedError("simulated interruption at " + stop)

                with mock.patch("ariadne.box_profiles._checkpoint", side_effect=interrupt), self.assertRaises(InterruptedError):
                    bootstrap_profiles(target)
                token_path = target / "data/flows.token"
                token = token_path.read_bytes() if token_path.exists() else None
                for name in ("voice-config.json", "automation-config.json"):
                    path = target / "data" / name
                    if path.exists():
                        self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)
                bootstrap_profiles(target)
                self.assertEqual(Store(target / "data/ariadne.sqlite3").setting("model"), "qwen3.5:9b")
                self.assertFalse((target / "data/.box-bootstrap-pending.json").exists())
                self.assertTrue((target / "data/automation-config.json").is_file())
                if token is not None:
                    self.assertEqual(token_path.read_bytes(), token)
                self.assertFalse(bootstrap_profiles(target)["factory_flows"])

    def test_pending_factory_recovery_preserves_owner_model_and_refuses_conflicting_files(self):
        self._fresh_factory_fixture()

        def interrupt(stage):
            if stage == "flows.token:committed":
                raise InterruptedError("simulated power loss")

        with mock.patch("ariadne.box_profiles._checkpoint", side_effect=interrupt), self.assertRaises(InterruptedError):
            bootstrap_profiles(self.root)
        token = (self.root / "data/flows.token").read_bytes()
        store = Store(self.root / "data/ariadne.sqlite3")
        store.set_setting("model", "owner-selected-model")
        target = self.root / "data/flows/settings.js"
        target.write_bytes(b"OWNER SETTINGS")
        with self.assertRaisesRegex(ValueError, "conflict"):
            bootstrap_profiles(self.root)
        self.assertEqual(target.read_bytes(), b"OWNER SETTINGS")
        self.assertEqual((self.root / "data/flows.token").read_bytes(), token)
        self.assertEqual(store.setting("model"), "owner-selected-model")
        self.assertFalse((self.root / "data/automation-config.json").exists())
        target.unlink()  # The owner explicitly resolves the fixture conflict.
        self.assertTrue(bootstrap_profiles(self.root)["factory_flows"])
        self.assertEqual(store.setting("model"), "owner-selected-model")

    def test_pending_journal_never_claims_a_database_created_by_the_owner(self):
        self._fresh_factory_fixture()

        def interrupt(stage):
            if stage == "journal:committed":
                raise InterruptedError("simulated power loss")

        with mock.patch("ariadne.box_profiles._checkpoint", side_effect=interrupt), self.assertRaises(InterruptedError):
            bootstrap_profiles(self.root)
        store = Store(self.root / "data/ariadne.sqlite3")
        store.set_setting("model", "owner database")
        with self.assertRaisesRegex(ValueError, "does not own"):
            bootstrap_profiles(self.root)
        self.assertEqual(store.setting("model"), "owner database")
        self.assertFalse((self.root / "data/flows.token").exists())


if __name__ == "__main__":
    unittest.main()
