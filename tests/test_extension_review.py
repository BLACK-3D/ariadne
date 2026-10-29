"""Independent regression checks found during the 0.4 extension review."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ariadne.config import Settings
from ariadne.tools import ToolRegistry


class SpeechApprovalBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".review-extension-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("first.exe", "second.exe", "first.onnx", "second.onnx"):
            (self.root / name).write_bytes(name.encode("ascii"))
        self.args = {"text": "Reviewed speech", "output": "speech.wav", "backend": "system"}
        # Neutralize hardware/worker initialization while preserving the real
        # provider, status path and approval HMAC/asset identity code.
        def voice_init(instance, settings):
            instance.settings = settings
            instance.windows_speech = None
            instance.natural = mock.Mock(spec=["status", "close"])
            instance.natural.status.return_value = {"state": "idle", "backend": "piper", "voice": "cori",
                "speed": 1.12, "closed": False, "busy": False, "cached_acknowledgements": 0,
                "sample_rate": 22050, "cpu_threads": 2, "gpu_used": False, "error": None}
        self.probe = mock.patch("ariadne.voice.Voice.__init__", voice_init)
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def registry(self, stem):
        registry = ToolRegistry(Settings(data_dir=self.root / "data", piper_bin=str(self.root / (stem + ".exe")),
                                         piper_model=str(self.root / (stem + ".onnx")), api_token="independent-test-token"))
        self.addCleanup(registry.media.close)
        return registry

    def test_changing_system_speech_executable_or_model_invalidates_approval(self):
        first, second = self.registry("first"), self.registry("second")
        self.assertNotEqual(first.fingerprint("media_speak", self.args), second.fingerprint("media_speak", self.args))

    def test_changed_piper_bytes_at_same_path_invalidate_approval(self):
        registry = self.registry("first")
        previous = registry.fingerprint("media_speak", self.args)
        (self.root / "first.exe").write_bytes(b"Changed native executable")
        changed_binary = registry.fingerprint("media_speak", self.args)
        self.assertNotEqual(previous, changed_binary)
        (self.root / "first.onnx").write_bytes(b"Changed voice model")
        self.assertNotEqual(changed_binary, registry.fingerprint("media_speak", self.args))


if __name__ == "__main__":
    unittest.main()
