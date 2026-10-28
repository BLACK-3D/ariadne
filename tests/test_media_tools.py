import base64
import io
import json
import tempfile
import unittest
import wave
import urllib.error
import hashlib
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

from ariadne.config import Settings
from ariadne.media_tools import MediaTools, backend_status, load_profile, run_media, wav_info
from ariadne.model import ModelError, Ollama
from ariadne.tools import ToolRegistry


def wav(seconds=.1, rate=16000):
    target = io.BytesIO()
    with wave.open(target, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b"\0\0" * int(rate * seconds))
    return target.getvalue()


class MediaTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / "runtime" / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="media-test-", dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name), model="qwen3.5:9b")
        self.registry = ToolRegistry(self.settings)
        self.media = MediaTools(self.registry)

    def test_wav_contract_rejects_wrong_rate_and_truncated_samples(self):
        self.assertEqual(wav_info(wav())["frames"], 1600)
        for data in (wav(rate=8000), wav()[:-4], b"not audio", wav(seconds=90.1)):
            with self.subTest(length=len(data)):
                with self.assertRaises(ValueError):
                    wav_info(data)

    def test_profile_cannot_reference_reserved_drive_or_non_runtime_files(self):
        for path in ("E:/model.onnx", "../outside.onnx", "data/workspace/model.onnx"):
            (self.settings.data_dir / "media-config.json").write_text(json.dumps({"version": 1, "backends": {
                "silero": {"assets": {"model": {"path": path, "sha256": "a" * 64}}}}}), encoding="utf-8")
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    load_profile(self.settings.data_dir)

    def test_optional_backends_are_not_reported_ready_without_assets(self):
        self.assertFalse(backend_status({"backends": {}}, "kokoro")["ready"])
        names = [s["function"]["name"] for s in self.media.schemas()]
        self.assertNotIn("media_vad", names)
        self.assertNotIn("media_generate_image", names)
        self.assertNotIn("media_diarize", names)

    def test_binary_outputs_never_overwrite_existing_file(self):
        self.media._write("example.wav", b"first", ".wav")
        with self.assertRaises(FileExistsError):
            self.media._write("example.wav", b"second", ".wav")
        self.assertEqual((self.settings.workspace / "example.wav").read_bytes(), b"first")
        self.assertEqual(list(self.settings.workspace.glob(".ariadne-*")), [])

    def test_binary_output_cannot_escape_workspace(self):
        for path in ("../outside.wav", "E:/voice.wav", "voice.txt"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.media._write(path, b"data", ".wav")

    def test_cancelled_operation_never_starts_worker(self):
        with patch("ariadne.media_tools.run_media") as worker:
            result = self.media.execute("media_generate_image", {"prompt": "a square", "output": "square.png"}, cancelled=lambda: True)
        self.assertFalse(result["ok"])
        worker.assert_not_called()

    def test_generate_rejects_unbounded_dimensions_and_unknown_arguments(self):
        for extra in ({"width": 10000}, {"height": 300}, {"seed": True}, {"url": "https://example.com"}):
            with self.subTest(extra=extra), patch("ariadne.media_tools.run_media") as worker:
                result = self.media.execute("media_generate_image", {"prompt": "a square", "output": "square.png", **extra})
                self.assertFalse(result["ok"])
                worker.assert_not_called()

    def test_image_tool_does_not_accept_urls_or_parent_paths(self):
        for path in ("https://example.com/picture.png", "../picture.png"):
            with self.subTest(path=path), patch("ariadne.media_tools.run_media") as worker:
                result = self.media.execute("media_inspect_image", {"paths": [path], "question": "Describe."})
                self.assertFalse(result["ok"])
                worker.assert_not_called()

    def test_snapshot_identity_changes_with_contents(self):
        path = self.settings.workspace / "audio.wav"
        path.write_bytes(wav())
        first = self.media.fingerprint_material("media_transcribe", {"path": "audio.wav"})
        path.write_bytes(wav(seconds=.2))
        second = self.media.fingerprint_material("media_transcribe", {"path": "audio.wav"})
        self.assertNotEqual(first["input"]["sha256"], second["input"]["sha256"])

    def test_system_speech_fingerprint_binds_piper_model_binary_and_sidecar(self):
        from dataclasses import replace
        binary, model = self.settings.data_dir / "piper.exe", self.settings.data_dir / "voice.onnx"
        binary.write_bytes(b"reviewed binary")
        model.write_bytes(b"reviewed model")
        sidecar = Path(str(model) + ".json")
        sidecar.write_text('{"speaker_id":0}', encoding="utf-8")
        self.registry.settings = replace(self.settings, piper_bin=str(binary), piper_model=str(model))
        args = {"text": "Hello", "backend": "system", "output": "hello.wav"}
        first = self.media.fingerprint_material("media_speak", args)
        model.write_bytes(b"changed model")
        second = self.media.fingerprint_material("media_speak", args)
        self.assertNotEqual(first["speech_execution"]["model"], second["speech_execution"]["model"])
        binary.write_bytes(b"changed binary")
        third = self.media.fingerprint_material("media_speak", args)
        self.assertNotEqual(second["speech_execution"]["binary"], third["speech_execution"]["binary"])
        sidecar.write_text('{"speaker_id":1}', encoding="utf-8")
        fourth = self.media.fingerprint_material("media_speak", args)
        self.assertNotEqual(third["speech_execution"]["configuration"], fourth["speech_execution"]["configuration"])

    def test_image_chat_queries_manifest_and_sends_encoded_bytes(self):
        client = Ollama("http://127.0.0.1:11434")
        with patch.object(client, "request", side_effect=[{"capabilities": ["vision", "tools"]},
                {"message": {"role": "assistant", "content": "red square"}}]) as request:
            message, _ = client.image_chat("qwen3.5:9b", "Describe.", [b"png bytes"])
        self.assertEqual(message["content"], "red square")
        payload = request.call_args.args[1]
        self.assertEqual(base64.b64decode(payload["messages"][0]["images"][0]), b"png bytes")
        self.assertEqual(payload["tools"], [])
        self.assertFalse(payload["think"])

    def test_text_only_manifest_blocks_image_inference(self):
        client = Ollama("http://127.0.0.1:11434")
        with patch.object(client, "request", return_value={"capabilities": ["completion", "tools"]}) as request:
            with self.assertRaisesRegex(ModelError, "does not advertise"):
                client.image_chat("qwen3:8b", "Describe.", [b"image"])
        self.assertEqual(request.call_count, 1)

    def test_actual_normalizer_strips_metadata_and_limits_dimensions(self):
        try:
            from PIL import Image, PngImagePlugin
        except ImportError:
            self.skipTest("Pinned Pillow is unavailable")
        source = io.BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text("comment", "untrusted metadata")
        Image.new("RGB", (2000, 1000), "red").save(source, format="PNG", pnginfo=info)
        result = run_media({"action": "normalize_image", "data": base64.b64encode(source.getvalue()).decode("ascii")})
        self.assertEqual((result["width"], result["height"]), (1536, 768))
        with Image.open(io.BytesIO(base64.b64decode(result["data"]))) as normalized:
            self.assertNotIn("comment", normalized.info)

    def test_actual_normalizer_rejects_other_formats_and_malformed_data(self):
        for data in (b"not an image", b"GIF89a" + b"\0" * 20):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    run_media({"action": "normalize_image", "data": base64.b64encode(data).decode("ascii")})

    def test_failed_model_start_preserves_previous_pid_record(self):
        from scripts import provision
        root = self.settings.data_dir
        (root / "data").mkdir()
        pid = root / "data" / "ollama.pid"
        pid.write_text("12345", encoding="ascii")
        process = Mock(pid=99999)
        process.poll.return_value = 1
        with patch.object(provision, "ROOT", root), patch.object(provision, "api", side_effect=urllib.error.URLError("unavailable")), \
                patch.object(provision.subprocess, "Popen", return_value=process), patch.object(provision.time, "sleep"):
            with self.assertRaises(SystemExit):
                provision.start_model_server(root / "ollama.exe", {})
        self.assertEqual(pid.read_text(encoding="ascii"), "12345")
        process.terminate.assert_not_called()

    def test_timeout_terminates_only_new_model_process_and_preserves_pid(self):
        from scripts import provision
        root = self.settings.data_dir
        (root / "data").mkdir()
        pid = root / "data" / "ollama.pid"
        pid.write_text("12345", encoding="ascii")
        process = Mock(pid=99999)
        process.poll.return_value = None
        with patch.object(provision, "ROOT", root), patch.object(provision, "api", side_effect=urllib.error.URLError("unavailable")), \
                patch.object(provision.subprocess, "Popen", return_value=process), patch.object(provision.time, "sleep"):
            with self.assertRaises(SystemExit):
                provision.start_model_server(root / "ollama.exe", {})
        self.assertEqual(pid.read_text(encoding="ascii"), "12345")
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)

    def test_offline_asset_and_notice_verification_never_uses_network(self):
        from scripts import provision_media
        root = self.settings.data_dir / "offline-appliance"
        target = root / "runtime/media/silero/model.onnx"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"model fixture")
        notice = root / "runtime/media/licenses/silero/LICENSE"
        notice.parent.mkdir(parents=True)
        notice.write_bytes(b"license fixture")
        (root / "scripts").mkdir()
        spec = {"captured": "2026-10-28", "files": [{"path": "silero/LICENSE", "url": "https://example.invalid/license",
            "sha256": hashlib.sha256(notice.read_bytes()).hexdigest(), "bytes": len(notice.read_bytes()), "license": "fixture"}]}
        (root / "scripts/media-license-assets.json").write_text(json.dumps(spec), encoding="utf-8")
        assets = {"silero": {"model": ("https://example.invalid/model.onnx", hashlib.sha256(target.read_bytes()).hexdigest(), 100)}}
        with patch.object(provision_media, "ROOT", root), patch.object(provision_media, "ASSETS", assets), \
                patch.object(provision_media.urllib.request, "urlopen", side_effect=AssertionError("Offline mode attempted network")), \
                patch.object(provision_media, "download", side_effect=AssertionError("Offline mode attempted download")):
            provision_media.provision_assets(offline=True)
            provision_media.provision_licenses(offline=True)
            target.unlink()
            with self.assertRaisesRegex(ValueError, "Offline media asset"):
                provision_media.provision_assets(offline=True)


if __name__ == "__main__":
    unittest.main()
