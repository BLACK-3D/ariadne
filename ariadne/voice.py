"""Optional offline CLI adapters. Models/binaries are supplied by the operator."""
import io
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path


class Voice:
    def __init__(self, settings):
        self.settings = settings
        from .speech_runtime import NaturalSpeech
        self.natural = NaturalSpeech(settings.data_dir)
        self.windows_speech = None
        if os.name == "nt":
            executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            if executable.is_file():
                try:
                    check = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command",
                        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.GetInstalledVoices().Count; $s.Dispose()"],
                        capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if check.returncode == 0 and check.stdout.strip().isdigit() and int(check.stdout.strip()) > 0:
                        self.windows_speech = str(executable)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    def status(self):
        s = self.settings
        from .media_tools import load_profile, backend_status
        try:
            profile = load_profile(s.data_dir)
            sherpa = backend_status(profile, "sherpa")["ready"]
            kokoro = backend_status(profile, "kokoro")["ready"]
            fast_speech = backend_status(profile, "piper")["ready"]
        except (ValueError, OSError):
            sherpa = kokoro = fast_speech = False
        whisper = bool(s.whisper_bin and s.whisper_model and Path(s.whisper_bin).is_file() and Path(s.whisper_model).is_file())
        piper = bool(s.piper_bin and s.piper_model and Path(s.piper_bin).is_file() and Path(s.piper_model).is_file())
        system = bool(self.windows_speech or piper)
        from .speech_runtime import SPEECH_VOICES
        return {"transcription": bool(sherpa or whisper), "speech": bool(kokoro or fast_speech or system),
                "transcription_backend": "sherpa-streaming" if sherpa else "whisper.cpp" if whisper else None,
                "speech_backend": "piper" if piper else "windows-sapi" if self.windows_speech else "piper-neural" if fast_speech else "kokoro" if kokoro else None,
                "speech_backends": (["system"] if system else []) + (["natural"] if kokoro or fast_speech else []),
                "default_voice": "cori" if fast_speech else "af_heart",
                "voice_presets": [item for item in SPEECH_VOICES if (fast_speech if item.get("backend") == "piper" else kokoro)],
                "natural_runtime": self.natural.status(),
                "latency_note": "Natural speech reuses a local CPU model after warm-up. No cloud service or GPU is required."}

    def warm(self, *, voice=None, speed=1.12):
        voice = voice or self.status()["default_voice"]
        return self.natural.warm(voice=voice, speed=speed)

    def reset_natural(self):
        """An explicit owner preference action may recover a failed worker."""
        if self.natural.status()["state"] == "error":
            from .speech_runtime import NaturalSpeech
            self.natural.close()
            self.natural = NaturalSpeech(self.settings.data_dir)

    def close(self):
        self.natural.close()

    def play(self, text, *, backend="natural", voice=None, speed=1.12):
        """Synchronous PC playback; WakeService keeps its microphone muted."""
        if os.name != "nt":
            raise ValueError("Direct PC speech playback is available on Windows only.")
        from .media_tools import validate_generated_wav
        audio = self.speak(text, backend=backend, voice=voice, speed=speed)
        validate_generated_wav(audio)
        import winsound
        winsound.PlaySound(audio, winsound.SND_MEMORY | winsound.SND_NODEFAULT)

    def transcribe(self, audio, *, cancelled=None):
        if not self.status()["transcription"]:
            raise ValueError("Local whisper.cpp binary and model are not configured")
        from .media_tools import wav_info, load_profile, run_media
        wav_info(audio)
        if self.status().get("transcription_backend") == "sherpa-streaming":
            import base64
            return run_media({"action": "sherpa", "profile": load_profile(self.settings.data_dir),
                "data": base64.b64encode(audio).decode("ascii")}, cancelled=cancelled)["text"]
        try:
            with wave.open(io.BytesIO(audio)) as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000 or wav.getnframes() > 16000 * 90:
                    raise ValueError("Audio must be mono 16-bit 16 kHz WAV, at most 90 seconds")
        except (wave.Error, EOFError):
            raise ValueError("Invalid WAV file") from None
        with tempfile.TemporaryDirectory(prefix="voice-", dir=self.settings.data_dir) as directory:
            source, output = Path(directory) / "input.wav", Path(directory) / "transcript"
            source.write_bytes(audio)
            try:
                result = subprocess.run([self.settings.whisper_bin, "-m", self.settings.whisper_model, "-f", str(source), "-otxt", "-of", str(output)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False)
                target = output.with_suffix(".txt")
                if result.returncode or not target.is_file() or target.stat().st_size > 64000:
                    raise ValueError("Local transcription failed; check whisper.cpp configuration")
                return target.read_text(encoding="utf-8").strip()
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError("Local transcription failed or timed out") from None

    def speak(self, text, *, backend="system", voice=None, speed=1.12, cancelled=None):
        from .media_tools import _text
        _text(text, "text", 2000)
        if backend not in {"system", "natural"}:
            raise ValueError("Speech backend must be system or natural.")
        if backend == "natural":
            voice = voice or self.status()["default_voice"]
            return self.natural.synthesize(text, voice=voice, speed=speed, cancelled=cancelled)
        if not self.status()["speech"]:
            raise ValueError("Local Piper binary and voice model are not configured")
        with tempfile.TemporaryDirectory(prefix="voice-", dir=self.settings.data_dir) as directory:
            output = Path(directory) / "speech.wav"
            try:
                if self.status().get("speech_backend") == "piper":
                    command = [self.settings.piper_bin, "--model", self.settings.piper_model, "--output_file", str(output)]
                    payload = text.encode()
                elif self.windows_speech:
                    command = [self.windows_speech, "-NoProfile", "-NonInteractive", "-Command",
                        "$ErrorActionPreference = 'Stop'; Add-Type -AssemblyName System.Speech; $p = [Console]::In.ReadToEnd() | ConvertFrom-Json; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; try { $s.SetOutputToWaveFile([string]$p.path); $s.Speak([string]$p.text) } finally { $s.Dispose() }"]
                    payload = json.dumps({"path": str(output), "text": text}, ensure_ascii=True).encode()
                else:
                    raise ValueError("No local speech engine is available")
                result = subprocess.run(command, input=payload, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode or not output.is_file() or output.stat().st_size > 20_000_000:
                    raise ValueError("Local speech generation failed; check Piper configuration")
                return output.read_bytes()
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError("Local speech generation failed or timed out") from None
