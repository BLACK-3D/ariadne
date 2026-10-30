"""Owned service port isolation and full fast-voice package pinning."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile

from ariadne.config import Settings, load_settings, local_service_port
from ariadne import box_profiles
from scripts import export_offline, host, launch, local_services, provision
from tests.test_box_profiles import public_export_fixture


class OwnedPortTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".portable-update-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"ARIADNE_DATA_DIR": str(self.root / "data"), "ARIADNE_OFFLINE": "1"}).start()
        for key in ("ARIADNE_MODEL_PORT", "ARIADNE_FLOW_PORT", "ARIADNE_OLLAMA_URL", "ARIADNE_API_TOKEN"):
            os.environ.pop(key, None)

    def test_owned_ports_are_decimal_numbers_only_and_defaults_stay_fixed(self):
        self.assertEqual(local_service_port("ARIADNE_MODEL_PORT", 11434), 11434)
        self.assertEqual(local_service_port("ARIADNE_FLOW_PORT", 1880), 1880)
        for value in ("", "0", "65536", "-1", "1.5", " 1881", "01881", "localhost:1881", "1881;code", True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_service_port("ARIADNE_FLOW_PORT", 1880, environ={"ARIADNE_FLOW_PORT": value})
        self.assertEqual(local_service_port("ARIADNE_FLOW_PORT", 1880, environ={"ARIADNE_FLOW_PORT": "65535"}), 65535)

    def test_settings_owned_port_and_explicit_operator_server_have_separate_scope(self):
        os.environ["ARIADNE_MODEL_PORT"] = "11448"
        self.assertEqual(load_settings().ollama_url, "http://127.0.0.1:11448")
        os.environ.update(ARIADNE_MODEL_PORT="not-used", ARIADNE_OLLAMA_URL="http://127.0.0.1:11449")
        self.assertEqual(load_settings().ollama_url, "http://127.0.0.1:11449")

    def test_model_environment_confines_storage_and_probe_uses_same_owned_port(self):
        os.environ["ARIADNE_MODEL_PORT"] = "11448"
        with patch.object(provision, "ROOT", self.root):
            env = provision.local_env()
            self.assertEqual(env["OLLAMA_HOST"], "127.0.0.1:11448")
            self.assertEqual(env["OLLAMA_NO_CLOUD"], "1")
            self.assertEqual(env["OLLAMA_MODELS"], str(self.root / "data/models"))
            opener = Mock()
            opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(b'{"version":"fixture"}'))
            opener.open.return_value.__exit__ = Mock(return_value=False)
            with patch.object(provision.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(provision.api("/api/version"), {"version": "fixture"})
            self.assertEqual(opener.open.call_args.args[0], "http://127.0.0.1:11448/api/version")

    def test_existing_custom_port_listener_is_never_adopted(self):
        os.environ["ARIADNE_MODEL_PORT"] = "11448"
        with patch.object(provision, "api", return_value={}), patch.object(provision.subprocess, "Popen") as factory:
            with self.assertRaisesRegex(SystemExit, "11448"):
                provision.start_model_server(self.root / "ollama.exe", {})
            factory.assert_not_called()
        with patch.object(provision, "api", return_value={}), patch.object(provision, "owned_model_server", return_value=True) as old_pid_record:
            with self.assertRaisesRegex(SystemExit, "11448"):
                provision.start_model_server(self.root / "ollama.exe", {}, allow_owned=True)
            old_pid_record.assert_not_called()

    def test_model_child_and_factory_routes_share_selected_ports(self):
        os.environ.update(ARIADNE_MODEL_PORT="11448", ARIADNE_FLOW_PORT="1888")
        (self.root / "data").mkdir()
        child = Mock(pid=456)
        child.poll.return_value = None
        factory = Mock(return_value=child)
        with patch.object(provision, "ROOT", self.root), patch.object(provision, "api", side_effect=[urllib.error.URLError("fixture"), {}]), patch("sys.stdout", io.StringIO()):
            provision.start_model_server(self.root / "ollama.exe", {"OLLAMA_HOST": "untrusted:9"}, create_process=factory)
        self.assertEqual(factory.call_args.kwargs["env"]["OLLAMA_HOST"], "127.0.0.1:11448")
        self.assertIn(b"uiPort:1888", local_services.factory_payloads()["settings.js"])
        routes = box_profiles._flow_config()["flows"]
        self.assertTrue(all(row["url"].startswith("http://127.0.0.1:1888/") for row in routes))

    def test_flow_port_override_preserves_conflicting_existing_owner_routes(self):
        data = self.root / "data"; data.mkdir()
        cfg = {"flows": [{"id": "flows_health", "url": "http://127.0.0.1:1880/ariadne/health", "token_file": "flows.token"}]}
        path = data / "automation-config.json"; raw = json.dumps(cfg).encode(); path.write_bytes(raw)
        settings = Settings(data_dir=data)
        self.assertTrue(launch.factory_flow_requested(settings))
        os.environ["ARIADNE_FLOW_PORT"] = "1888"
        with self.assertRaisesRegex(ValueError, "preserved"):
            launch.factory_flow_requested(settings)
        self.assertEqual(path.read_bytes(), raw)
        cfg["flows"][0]["url"] = "http://127.0.0.1:1888/ariadne/health"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertTrue(launch.factory_flow_requested(settings))

    def test_detached_host_forwards_only_validated_port_settings(self):
        with patch.object(host, "ROOT", self.root), patch.dict(os.environ, {"SystemRoot": "C:\\Windows", "PYTHONPATH": "unsafe", "SECRET_TOKEN": "private"}):
            env = host.clean_environment({"ARIADNE_MODEL_PORT": "11448", "ARIADNE_FLOW_PORT": "1888"})
            self.assertEqual((env["ARIADNE_MODEL_PORT"], env["ARIADNE_FLOW_PORT"]), ("11448", "1888"))
            self.assertNotIn("PYTHONPATH", env); self.assertNotIn("SECRET_TOKEN", env)
            with self.assertRaises(ValueError): host.clean_environment({"ARIADNE_MODEL_PORT": "127.0.0.1:11448"})


class HeadlessOneClickTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".portable-update-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        source = Path(__file__).resolve().parents[1]
        (self.root / "start.ps1").write_bytes((source / "start.ps1").read_bytes())
        (self.root / "Start Ariadne.cmd").write_bytes((source / "Start Ariadne.cmd").read_bytes())
        (self.root / "scripts/host_process.ps1").write_text("param([string]$Action,[switch]$NoBrowser)\n[Console]::Write((@{action=$Action;no_browser=[bool]$NoBrowser}|ConvertTo-Json -Compress))\n", encoding="utf-8")

    def run_script(self, value, *, cmd=False):
        env = dict(os.environ)
        if value is None: env.pop("ARIADNE_NO_BROWSER", None)
        else: env["ARIADNE_NO_BROWSER"] = value
        command = ([str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"), "/d", "/c", str(self.root / "Start Ariadne.cmd")]
                   if cmd else [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(self.root / "start.ps1")])
        return subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True, timeout=20,
                              stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_actual_cmd_delegates_headless_without_argument_interpolation(self):
        result = self.run_script("1", cmd=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"action": "start", "no_browser": True})
        self.assertNotIn("%*", (self.root / "Start Ariadne.cmd").read_text())

    def test_normal_default_and_invalid_headless_values_preserve_fixed_scope(self):
        for value in (None, "0"):
            result = self.run_script(value)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["no_browser"])
        for value in ("true", "1; Write-Output injected", "1\" & echo injected"):
            result = self.run_script(value)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('"action"', result.stdout)


class VoiceExportPinsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".portable-update-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ("data", "docs", "ariadne", "scripts", "runtime/python"):
            (self.root / directory).mkdir(parents=True)
        public_export_fixture(self.root)
        payload = b"synthetic qualified interpreter"
        (self.root / "runtime/python/python.exe").write_bytes(payload)
        (self.root / "runtime/python/PORTABLE-MANIFEST.json").write_text(json.dumps({"sha256": {"python.exe": hashlib.sha256(payload).hexdigest()}}), encoding="utf-8")
        (self.root / "runtime/ollama/ollama.exe").write_bytes(b"synthetic inference runtime")
        paths = ["wheels/piper_tts-1.8.0-cp39-abi3-win_amd64.whl", "wheels/pathvalidate-3.3.1-py3-none-any.whl",
                 "en_GB-cori-medium.onnx", "en_GB-cori-medium.onnx.json", "notices/Cori-MODEL_CARD", "notices/Piper-COPYING"]
        assets = []
        for name in paths:
            path = "runtime/media/piper/" + name; payload = ("public fixture " + name).encode()
            target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(payload)
            assets.append({"path": path, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "url": "https://files.pythonhosted.org/fixture/" + name})
        self.catalog = {"schema": 1, "assets": assets}
        self.catalog_path = self.root / "config/speech-assets.json"
        self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf-8")
        catalog_pin = patch.object(export_offline, "SPEECH_CATALOG_SHA256", hashlib.sha256(self.catalog_path.read_bytes()).hexdigest())
        catalog_pin.start(); self.addCleanup(catalog_pin.stop)
        self.package = self.root / "runtime/media/piper-python/piper/voice.py"
        self.package.parent.mkdir(parents=True); self.package.write_bytes(b"# public installed fixture")
        self.install = self.root / "runtime/media/piper/INSTALL-MANIFEST.json"
        self.install.write_text(json.dumps({"schema": 1, "catalog_sha256": hashlib.sha256(self.catalog_path.read_bytes()).hexdigest(),
            "packages": {"piper-tts": "1.8.0", "pathvalidate": "3.3.1"}, "files": {"piper/voice.py": {"bytes": self.package.stat().st_size, "sha256": hashlib.sha256(self.package.read_bytes()).hexdigest()}}}), encoding="utf-8")
        self.selected = {"profiles": {"media-config.json": {"version": 1, "backends": {"piper": {"assets": {
            "model": {key: self.catalog["assets"][2][key] for key in ("path", "sha256")},
            "config": {key: self.catalog["assets"][3][key] for key in ("path", "sha256")}}}}}}}
        (self.root / "data/media-config.json").write_text(json.dumps(self.selected["profiles"]["media-config.json"]), encoding="utf-8")

    def test_complete_voice_pins_include_weights_licenses_wheels_and_installed_engine(self):
        pins = export_offline._speech_pins(self.root, self.selected)
        self.assertEqual(len(pins), 9)
        self.assertIn("runtime/media/piper-python/piper/voice.py", pins)
        receipt = export_offline.export(self.root / "dist/full.zip", self.root)
        with zipfile.ZipFile(self.root / "dist/full.zip") as archive:
            manifest = json.loads(archive.read("BOX-MANIFEST.json"))
            rows = {row["path"]: row for row in manifest["files"]}
            self.assertTrue(set(pins) <= set(rows))
            self.assertTrue(all(rows[name]["sha256"] == digest for name, digest in pins.items()))
        self.assertEqual(receipt["manifest"], "BOX-MANIFEST.json")

    def test_partial_selected_voice_or_extra_unknown_installed_package_fails_before_export(self):
        missing = self.root / self.catalog["assets"][4]["path"]
        missing.unlink()
        with self.assertRaises(FileNotFoundError): export_offline.plan(self.root)
        missing.write_bytes(b"restored but wrong size")
        with self.assertRaises(ValueError): export_offline.plan(self.root)
        asset = self.catalog["assets"][4]; missing.write_bytes(("public fixture notices/Cori-MODEL_CARD").encode())
        self.assertEqual(missing.stat().st_size, asset["bytes"])
        (self.package.parent / "unknown.py").write_text("# unknown runtime material", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory"):
            export_offline.plan(self.root)

    def test_same_size_package_tamper_is_rejected_during_stream_without_published_archive(self):
        old = self.package.read_bytes(); self.package.write_bytes(b"x" * len(old))
        output = self.root / "dist/tampered.zip"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            export_offline.export(output, self.root)
        self.assertFalse(output.exists())
        self.assertTrue(output.with_suffix(".zip.partial").exists())
        self.assertFalse(output.with_suffix(".zip.sha256").exists())

    def test_catalog_paths_and_selected_voice_identity_cannot_be_substituted(self):
        value = copy.deepcopy(self.selected)
        value["profiles"]["media-config.json"]["backends"]["piper"]["assets"]["model"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Factory fast voice"):
            export_offline._speech_pins(self.root, value)
        self.catalog["assets"][0]["path"] = "data/owner-secret"
        self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf-8")
        with self.assertRaises(ValueError): export_offline._speech_pins(self.root, self.selected)

    def test_catalog_provenance_changes_require_review_even_when_asset_bytes_match(self):
        self.catalog["unreviewed_provenance"] = "changed license or upstream source"
        self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reviewed public Cori"):
            export_offline._speech_pins(self.root, self.selected)


if __name__ == "__main__":
    unittest.main()
