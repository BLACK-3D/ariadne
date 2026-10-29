"""Fixed reusable CPU Kokoro synthesizer. No microphone, playback or downloads."""
from __future__ import annotations

import argparse
import base64
import contextlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path
from ariadne.speech_runtime import (MAX_INPUT_LINE, MAX_OUTPUT_LINE, MAX_WAV_BYTES, VOICE_LANGUAGES,
    VOICE_BACKENDS, VOICE_SAMPLE_RATES,
    _READY, speech_assets, strict_json, validate_speech_options, validate_speech_text, validate_speech_wav)


def limit_resources():
    if os.name == "nt":
        from scripts.wake_listener import limit_continuous_worker
        limit_continuous_worker()
    elif os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    else:
        raise ValueError("Speech worker resource limits are unsupported.")


class PresetEngine:
    def __init__(self, data_dir):
        self.data_dir = _allowed_path(data_dir)
        self.paths, self.identities = speech_assets(self.data_dir, verify=True)
        site = _allowed_path(ROOT / "runtime/media/python")
        piper_site = _allowed_path(ROOT / "runtime/media/piper-python")
        sys.path[:0] = [str(piper_site), str(site)]
        from ariadne._media_worker import deny_network_and_execution
        sys.addaudithook(deny_network_and_execution)
        import numpy as np
        self.np = np
        self.engines = {}
        # Quick speech pays only Piper's startup work. The slower quality
        # engine remains unloaded until its preset is explicitly selected.
        if "piper.model" in self.paths:
            self._load_piper()
        else:
            self._load_kokoro()
        self.check_assets()

    def _load_piper(self):
        if "piper" not in self.engines:
            if "piper.model" not in self.paths:
                raise ValueError("The quick preset has not been provisioned.")
            if importlib.metadata.version("piper-tts") != "1.8.0" or importlib.metadata.version("pathvalidate") != "3.3.1":
                raise ValueError("The pinned Piper runtime versions are required.")
            from piper import PiperVoice, PiperConfig
            from ariadne._media_worker import ort_session
            config = strict_json(self.paths["piper.config"].read_bytes())
            if (config.get("audio", {}).get("sample_rate") != 22050 or config.get("language", {}).get("code") != "en_GB"
                    or config.get("num_speakers") != 1 or config.get("phoneme_type") != "espeak"):
                raise ValueError("The fixed British preset configuration differs.")
            self.engines["piper"] = PiperVoice(session=ort_session(str(self.paths["piper.model"])),
                config=PiperConfig.from_dict(config), download_dir=self.data_dir, use_tashkeel=False)
        return self.engines["piper"]

    def _load_kokoro(self):
        if "kokoro" not in self.engines:
            if "kokoro.model" not in self.paths:
                raise ValueError("The selected slower preset has not been provisioned.")
            if importlib.metadata.version("kokoro-onnx") != "0.6.1":
                raise ValueError("The pinned Kokoro runtime version is required.")
            from kokoro_onnx import Kokoro
            from ariadne._media_worker import ort_session
            engine = Kokoro.from_session(ort_session(str(self.paths["kokoro.model"])), str(self.paths["kokoro.voices"]))
            required = {voice for voice, backend in VOICE_BACKENDS.items() if backend == "kokoro"}
            if not required.issubset(set(engine.get_voices())):
                raise ValueError("The curated slower speech presets are missing.")
            self.engines["kokoro"] = engine
            self.engine = engine  # Legacy internal qualification compatibility.
        return self.engines["kokoro"]

    def check_assets(self):
        if speech_assets(self.data_dir)[1] != self.identities:
            raise ValueError("Speech assets or profile changed while the worker was active.")

    def say(self, text, voice, speed):
        text = validate_speech_text(text)
        voice, speed = validate_speech_options(voice, speed)
        self.check_assets()
        if VOICE_BACKENDS[voice] == "piper":
            from piper import SynthesisConfig
            engine = self._load_piper()
            parts, count = [], 0
            for chunk in engine.synthesize(text, syn_config=SynthesisConfig(length_scale=1/speed)):
                if (chunk.sample_rate, chunk.sample_width, chunk.sample_channels) != (22050, 2, 1):
                    raise ValueError("Quick speech returned an unexpected audio format.")
                samples = chunk.audio_float_array
                if samples.ndim != 1 or not self.np.isfinite(samples).all():
                    raise ValueError("Quick speech returned invalid PCM samples.")
                count += len(samples)
                if count > 22050 * 90 or count * 2 + 44 > MAX_WAV_BYTES:
                    raise ValueError("Quick speech exceeded its audio bound.")
                parts.append(samples)
            if not parts:
                raise ValueError("Quick speech returned no audio.")
            samples, sample_rate = self.np.concatenate(parts), 22050
        else:
            samples, sample_rate = self._load_kokoro().create(text, voice=voice, speed=speed, lang=VOICE_LANGUAGES[voice])
        if (sample_rate != VOICE_SAMPLE_RATES[voice] or samples.ndim != 1 or not 1 <= len(samples) <= sample_rate * 90
                or not self.np.isfinite(samples).all()):
            raise ValueError("Speech output exceeded its PCM/duration bounds.")
        if len(samples) * 2 + 44 > MAX_WAV_BYTES:
            raise ValueError("Speech output exceeds 3 MB.")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes((self.np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        result = output.getvalue()
        validate_speech_wav(result)
        self.check_assets()
        return result


def emit(stream, message):
    encoded = (json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    if len(encoded) > MAX_OUTPUT_LINE:
        raise ValueError("Speech protocol output is oversized.")
    stream.write(encoded)
    stream.flush()


def serve(engine, incoming, outgoing):
    emit(outgoing, _READY)
    while raw := incoming.readline(MAX_INPUT_LINE + 1):
        identifier = None
        try:
            if len(raw) > MAX_INPUT_LINE or not raw.endswith(b"\n"):
                raise ValueError("Speech protocol input is oversized or incomplete.")
            request = strict_json(raw)
            if type(request) is not dict or type(request.get("id")) is not str or not re.fullmatch(r"[a-f0-9]{24}", request["id"]):
                raise ValueError("Speech request identity is invalid.")
            identifier = request["id"]
            if request == {"id": identifier, "action": "stop"}:
                return
            if set(request) != {"id", "action", "text", "voice", "speed"} or request["action"] != "say":
                raise ValueError("Unsupported speech operation.")
            voice, speed = validate_speech_options(request["voice"], request["speed"])
            text = validate_speech_text(request["text"])
            result = engine.say(text, voice, speed)
            actual_rate = validate_speech_wav(result)["sample_rate"]
            if actual_rate != VOICE_SAMPLE_RATES[voice]:
                raise ValueError("Speech PCM and requested preset sample rates differ.")
            emit(outgoing, {"id": identifier, "ok": True, "data": base64.b64encode(result).decode("ascii"), "sample_rate": actual_rate})
        except (Exception, MemoryError):
            emit(outgoing, {"id": identifier, "ok": False, "error": "Natural speech worker failed; explicitly reapply voice settings to restart it."})
            return  # Never silently continue after an invalid request/engine error.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    limit_resources()
    # Engine output/warnings stay private; stdout carries only bounded frames.
    outgoing = sys.stdout.buffer
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        engine = PresetEngine(args.data_dir)
        serve(engine, sys.stdin.buffer, outgoing)


if __name__ == "__main__":
    try:
        main()
    except (Exception, MemoryError):
        emit(sys.stdout.buffer, {"event": "error", "error": "Natural speech could not initialize its local CPU engine."})
        raise SystemExit(1)
