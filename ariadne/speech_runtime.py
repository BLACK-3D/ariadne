"""One owned, bounded CPU speech worker; no arbitrary text is cached or logged.

This is a resource-limited preset synthesizer, not an OS security sandbox.
Failures are held until the owner explicitly creates a new runtime instance.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import queue
import secrets
import stat
import struct
import subprocess
import sys
import threading
import time
import wave

from .config import _allowed_path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VOICE = "cori"
DEFAULT_SPEED = 1.12
SPEECH_VOICES = (
    {"id": "cori", "name": "Cori · British (quick)", "language": "en-gb", "backend": "piper"},
    {"id": "af_heart", "name": "Heart · American (slower)", "language": "en-us", "backend": "kokoro"},
    {"id": "af_bella", "name": "Bella · American (slower)", "language": "en-us", "backend": "kokoro"},
    {"id": "bf_emma", "name": "Emma · British (slower)", "language": "en-gb", "backend": "kokoro"},
    {"id": "bm_george", "name": "George · British (slower)", "language": "en-gb", "backend": "kokoro"},
)
VOICE_LANGUAGES = {item["id"]: item["language"] for item in SPEECH_VOICES}
VOICE_BACKENDS = {item["id"]: item["backend"] for item in SPEECH_VOICES}
VOICE_SAMPLE_RATES = {voice: 22050 if backend == "piper" else 24000 for voice, backend in VOICE_BACKENDS.items()}
ACKNOWLEDGEMENT = "Listening."
MAX_TEXT = 2000
MAX_INPUT_LINE = 16_384
MAX_WAV_BYTES = 3_000_000
MAX_OUTPUT_LINE = 4_000_512
REQUEST_TIMEOUT = 60.0
_PINS = {
    "model": ("runtime/media/kokoro/kokoro-v1.0.int8.onnx", "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c"),
    "voices": ("runtime/media/kokoro/voices-v1.0.bin", "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
ASSET_PINS = {"kokoro": _PINS, "piper": {
    "model": ("runtime/media/piper/en_GB-cori-medium.onnx", "1899f98e5fb8310154f3c2973f4b8a929ba7245e722b3d3a85680b833d95f10d"),
    "config": ("runtime/media/piper/en_GB-cori-medium.onnx.json", "e262c16d7f192f69d4edd6b4ef8a5915379e67495fcc402f1ab15eeb33da3d36"),
}}
_READY = {"event": "ready", "protocol": 1, "backend": "offline-neural"}


class SpeechError(ValueError):
    """Safe, bounded speech failure without private request/engine diagnostics."""


def validate_speech_options(voice, speed):
    if type(voice) is not str or voice not in VOICE_LANGUAGES:
        raise ValueError("Choose one of the five installed speech presets.")
    if type(speed) not in {int, float} or not math.isfinite(speed) or not .85 <= speed <= 1.30:
        raise ValueError("Speech speed must be a finite number between 0.85 and 1.30.")
    return voice, float(speed)


def validate_speech_text(text):
    if (type(text) is not str or not text.strip() or len(text) > MAX_TEXT
            or any((ord(c) < 32 and c not in "\n\r\t") or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in text)):
        raise ValueError("Speech must contain at most 2000 characters without control or invalid Unicode characters.")
    return " ".join(text.split())


def validate_speech_wav(audio):
    if type(audio) is not bytes or not 46 <= len(audio) <= MAX_WAV_BYTES:
        raise ValueError("Natural speech must be a complete WAV within 3 MB.")
    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE" or struct.unpack("<I", audio[4:8])[0] + 8 != len(audio):
        raise ValueError("Natural speech WAV has an invalid RIFF size.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            rate = source.getframerate()
            if (source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (1, 2, "NONE") or rate not in {22050, 24000}:
                raise ValueError("Natural speech must be mono PCM16 at 22.05 or 24 kHz.")
            frames = source.getnframes()
            if not 1 <= frames <= rate * 90 or len(source.readframes(frames + 1)) != frames * 2:
                raise ValueError("Natural speech PCM is incomplete or exceeds 90 seconds.")
            return {"sample_rate": rate, "frames": frames, "duration_seconds": round(frames / rate, 4)}
    except (wave.Error, EOFError, struct.error):
        raise ValueError("Natural speech WAV is invalid.") from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate protocol key.")
        result[key] = value
    return result


def strict_json(raw):
    def reject(_value):
        raise ValueError("Nonfinite protocol number.")
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=reject)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _regular(path, maximum):
    path = _allowed_path(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400 or not 0 < info.st_size <= maximum):
        raise ValueError("A local speech file is missing, linked or outside its size limit.")
    return path, _identity(info)


def speech_assets(data_dir, *, verify=False):
    """Pin the small profile and asset identities; hash native assets at startup."""
    data_dir = _allowed_path(data_dir)
    if not data_dir.is_relative_to(ROOT) or not data_dir.is_dir():
        raise ValueError("Speech state must remain in the local installation.")
    profile_path, before = _regular(data_dir / "media-config.json", 32768)
    raw = profile_path.read_bytes()
    if _regular(profile_path, 32768)[1] != before:
        raise ValueError("Speech profile changed during inspection.")
    profile = strict_json(raw)
    if (type(profile) is not dict or set(profile) != {"version", "backends"}
            or type(profile["version"]) is not int or profile["version"] != 1
            or type(profile["backends"]) is not dict):
        raise ValueError("Speech profile schema is invalid.")
    configured = set(profile["backends"]) & set(ASSET_PINS)
    if not configured:
        raise ValueError("Pinned natural speech assets are not configured.")
    paths, identities = {}, {"profile": (before, hashlib.sha256(raw).hexdigest())}
    for backend in sorted(configured):
        config, pins = profile["backends"][backend], ASSET_PINS[backend]
        if type(config) is not dict or set(config) != {"assets"} or type(config["assets"]) is not dict or set(config["assets"]) != set(pins):
            raise ValueError("A natural speech asset set differs from its fixed schema.")
        for role, (name, digest) in pins.items():
            record = config["assets"][role]
            if type(record) is not dict or record != {"path": name, "sha256": digest}:
                raise ValueError("Natural speech requires the accepted model and preset pins.")
            path, identity = _regular(ROOT / name, 200_000_000)
            if not path.is_relative_to(ROOT / "runtime/media"):
                raise ValueError("Speech assets escaped the installation.")
            if verify:
                with path.open("rb") as stream:
                    opened = _identity(os.fstat(stream.fileno()))
                    # Compare ctime only within each API: some Windows file
                    # providers report path and handle ctimes differently.
                    if opened[:4] != identity[:4] or hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise ValueError("A natural speech asset failed its SHA-256 pin.")
                    if _identity(os.fstat(stream.fileno())) != opened:
                        raise ValueError("A natural speech asset changed during verification.")
                if _regular(path, 200_000_000)[1] != identity:
                    raise ValueError("A natural speech asset was replaced during verification.")
            key = backend + "." + role
            paths[key], identities[key] = path, identity
    return paths, identities


class NaturalSpeech:
    """Serial reusable child with a single bounded protocol queue and 60s calls."""

    def __init__(self, data_dir):
        self.data_dir = _allowed_path(data_dir)
        if not self.data_dir.is_relative_to(ROOT):
            raise ValueError("Speech state must remain in the local installation.")
        self._operation = threading.Lock()
        self._lock = threading.RLock()
        self._closed = False
        self._state, self._error = "idle", None
        self._process, self._reader = None, None
        self._queue = queue.Queue(maxsize=2)
        self._overflow = threading.Event()
        self._done = threading.Event()
        self._assets = None
        self._cache = {}
        self._warm_thread, self._warm_next = None, None
        self._voice, self._speed = DEFAULT_VOICE, DEFAULT_SPEED
        self._busy = False

    def status(self):
        # Never acquire the long operation lock or invoke an engine/status probe.
        with self._lock:
            return {"state": self._state, "backend": VOICE_BACKENDS[self._voice], "closed": self._closed,
                    "voice": self._voice, "speed": self._speed, "busy": self._busy,
                    "cached_acknowledgements": len(self._cache), "error": self._error,
                    "sample_rate": VOICE_SAMPLE_RATES[self._voice], "cpu_threads": 2, "gpu_used": False}

    def _check(self, deadline, cancelled, *, worker=True):
        with self._lock:
            if self._closed:
                raise SpeechError("Natural speech is closed.")
            if self._state == "error":
                raise SpeechError(self._error)
            if worker and self._process is not None and (self._overflow.is_set() or self._done.is_set() or self._process.poll() is not None):
                raise SpeechError("Natural speech stopped or returned an invalid response. Reapply voice settings to restart it.")
        if cancelled and cancelled():
            raise SpeechError("Natural speech was cancelled. Reapply voice settings to restart it.")
        if time.monotonic() >= deadline:
            raise SpeechError("Natural speech exceeded its 60-second limit. Reapply voice settings to restart it.")

    def _receive(self, process, deadline, cancelled):
        while True:
            self._check(deadline, cancelled)
            if self._overflow.is_set():
                raise SpeechError("Natural speech returned an invalid or oversized response.")
            try:
                return self._queue.get(timeout=min(.05, max(.001, deadline - time.monotonic())))
            except queue.Empty:
                if self._done.is_set() or process.poll() is not None:
                    raise SpeechError("Natural speech stopped unexpectedly. Reapply voice settings to restart it.")

    def _read_stdout(self, process):
        try:
            while raw := process.stdout.readline(MAX_OUTPUT_LINE + 1):
                if len(raw) > MAX_OUTPUT_LINE or not raw.endswith(b"\n"):
                    raise ValueError("Invalid speech frame.")
                value = strict_json(raw)
                if type(value) is not dict:
                    raise ValueError("Invalid speech response.")
                self._queue.put_nowait(value)
        except (OSError, ValueError, TypeError, RecursionError, queue.Full):
            self._overflow.set()
        finally:
            self._done.set()

    def _start(self, deadline, cancelled):
        if self._process is not None:
            return self._process
        from .worker import _environment
        script, script_identity = _regular(Path(__file__).with_name("_speech_worker.py"), 100_000)
        _, self._assets = speech_assets(self.data_dir)
        self._check(deadline, cancelled)
        # No waiting for native initialization while holding the metadata lock.
        with self._lock:
            self._check(deadline, cancelled)
            self._process = subprocess.Popen([sys.executable, "-I", "-B", str(script), "--data-dir", str(self.data_dir)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=ROOT, env=_environment(None), bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=os.name == "posix")
            process = self._process
        if _regular(script, 100_000)[1] != script_identity:
            raise SpeechError("The fixed speech worker changed during startup.")
        self._reader = threading.Thread(target=self._read_stdout, args=(process,), name="natural-speech-output", daemon=True)
        self._reader.start()
        ready = self._receive(process, deadline, cancelled)
        if ready != _READY or type(ready.get("protocol")) is not int:
            raise SpeechError("Natural speech could not initialize its fixed CPU engine.")
        self._verify_assets()
        return process

    def _verify_assets(self):
        if speech_assets(self.data_dir)[1] != self._assets:
            raise SpeechError("Local speech assets or settings changed. Reapply voice settings to restart it.")

    def _send(self, process, payload, deadline, cancelled):
        failed = threading.Event()
        finished = threading.Event()
        def write():
            try:
                # FileIO is unbuffered: explicitly handle a short pipe write.
                remaining = memoryview(payload)
                while remaining:
                    count = process.stdin.write(remaining)
                    if not count:
                        raise OSError("Speech pipe closed.")
                    remaining = remaining[count:]
            except (OSError, ValueError):
                failed.set()
            finally:
                finished.set()
        writer = threading.Thread(target=write, name="natural-speech-input", daemon=True)
        writer.start()
        while not finished.wait(.02):
            self._check(deadline, cancelled)
        if failed.is_set():
            raise SpeechError("Natural speech could not accept its request.")

    def synthesize(self, text, *, voice=DEFAULT_VOICE, speed=DEFAULT_SPEED, cancelled=None):
        text = validate_speech_text(text)
        voice, speed = validate_speech_options(voice, speed)
        identifier = secrets.token_hex(12)
        payload = (json.dumps({"id": identifier, "action": "say", "text": text, "voice": voice, "speed": speed},
                             ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        if len(payload) > MAX_INPUT_LINE:
            raise ValueError("Speech exceeds its encoded request byte limit.")
        deadline = time.monotonic() + REQUEST_TIMEOUT
        acquired = False
        try:
            while not acquired:
                self._check(deadline, cancelled, worker=False)
                acquired = self._operation.acquire(timeout=.05)
            with self._lock:
                self._busy, self._voice, self._speed = True, voice, speed
                if self._state == "idle":
                    self._state = "warming"
            self._check(deadline, cancelled)
            process = self._start(deadline, cancelled)
            self._verify_assets()
            cached = self._cache.get(voice)
            if text == ACKNOWLEDGEMENT and cached and cached[0] == speed:
                result = cached[1]
                with self._lock:
                    self._check(deadline, cancelled)
                    self._cache.pop(voice)
                    self._cache[voice] = cached
            else:
                self._send(process, payload, deadline, cancelled)
                response = self._receive(process, deadline, cancelled)
                if (set(response) != {"id", "ok", "data", "sample_rate"} or response.get("id") != identifier
                        or response.get("ok") is not True or response.get("sample_rate") != VOICE_SAMPLE_RATES[voice]
                        or type(response.get("sample_rate")) is not int
                        or type(response.get("data")) is not str):
                    raise SpeechError("Natural speech returned a failed or mismatched request receipt.")
                result = base64.b64decode(response["data"], validate=True)
                if validate_speech_wav(result)["sample_rate"] != response["sample_rate"]:
                    raise SpeechError("Natural speech WAV and response sample rates differ.")
                self._verify_assets()
                if text == ACKNOWLEDGEMENT:
                    with self._lock:
                        self._check(deadline, cancelled)
                        self._cache.pop(voice, None)
                        if len(self._cache) >= 4:
                            self._cache.pop(next(iter(self._cache)))
                        self._cache[voice] = (speed, result)
            self._check(deadline, cancelled)
            with self._lock:
                self._state, self._error = "ready", None
            return result
        except (Exception, MemoryError) as error:
            message = str(error) if isinstance(error, SpeechError) else "Natural speech failed. Reapply voice settings to restart it."
            if not acquired:
                # A queued caller owns no native work. Its cancellation or
                # deadline cannot revoke another in-flight call or warm-up.
                raise SpeechError(message) from None
            with self._lock:
                if not self._closed:
                    self._state, self._error = "error", message
                self._cache.clear()
            self._stop_process()
            raise SpeechError(message) from None
        finally:
            if acquired:
                with self._lock:
                    self._busy = False
                self._operation.release()

    def warm(self, *, voice=DEFAULT_VOICE, speed=DEFAULT_SPEED):
        voice, speed = validate_speech_options(voice, speed)
        with self._lock:
            if self._closed:
                raise SpeechError("Natural speech is closed.")
            if self._state == "error":
                return self.status()
            self._warm_next = (voice, speed)
            if self._warm_thread is not None and self._warm_thread.is_alive():
                return self.status()
            self._state = "warming"
            def prepare():
                while True:
                    with self._lock:
                        options, self._warm_next = self._warm_next, None
                        if options is None or self._closed or self._state == "error":
                            self._warm_thread = None
                            return
                    try:
                        self.synthesize(ACKNOWLEDGEMENT, voice=options[0], speed=options[1])
                    except SpeechError:
                        with self._lock:
                            self._warm_next, self._warm_thread = None, None
                        return
            self._warm_thread = threading.Thread(target=prepare, name="natural-speech-warm", daemon=True)
            self._warm_thread.start()
            return self.status()

    def _stop_process(self):
        with self._lock:
            process = self._process
        if process is None:
            return True
        stopped = False
        try:
            if process.poll() is None:
                from .worker import _terminate
                _terminate(process)
            process.wait(timeout=2)
            stopped = process.poll() is not None
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            # Keep the owned handle tracked if termination could not be verified.
            if stopped:
                with self._lock:
                    if self._process is process:
                        self._process = None
                # Killing first releases blocked unbuffered pipe writes/reads.
                for stream in (process.stdin, process.stdout):
                    if stream:
                        try:
                            stream.close()
                        except OSError:
                            pass
                if self._reader is not None and self._reader is not threading.current_thread():
                    self._reader.join(timeout=1)
        return stopped

    def close(self):
        with self._lock:
            self._closed, self._state = True, "idle"
            self._warm_next = None
            self._cache.clear()
        stopped = self._stop_process()
        if self._warm_thread is not None and self._warm_thread is not threading.current_thread():
            self._warm_thread.join(timeout=1)
        if not stopped:
            with self._lock:
                self._state, self._error = "error", "The owned speech worker could not be stopped. Restart Ariadne before re-enabling speech."
            raise SpeechError(self._error)
