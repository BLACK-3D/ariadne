"""Owned, opt-in local Sherpa microphone worker. Never writes captured audio.

--probe and --test-wav never open a microphone. No model downloads are made.
The server uses this fixed entry point; stdin accepts only mute/unmute/stop.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "runtime" / "media" / "python"))

from ariadne.config import _allowed_path
from ariadne.media_tools import backend_status, load_profile


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def microphone_count():
    if os.name != "nt":
        return None
    winmm = ctypes.WinDLL("winmm", use_last_error=True)
    winmm.waveInGetNumDevs.restype = ctypes.c_uint
    return int(winmm.waveInGetNumDevs())


def limit_continuous_worker():
    """Bound memory/children without a lifetime CPU quota for opt-in listening."""
    if os.name != "nt":
        return
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                    ("processes", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", Counters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes, kernel.CreateJobObjectW.restype = [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes, kernel.AssignProcessToJobObject.restype = [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    limits = Extended()
    limits.basic.flags, limits.basic.processes = 0x100 | 0x200 | 0x2000 | 0x8, 1
    limits.process_memory = limits.job_memory = 2 * 1024**3
    handle = kernel.CreateJobObjectW(None, None)
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise ValueError("Could not establish microphone worker resource limits.")
    globals()["_JOB_HANDLE"] = handle


class WindowsInput:
    """Default Windows input, four bounded 200 ms PCM16 mono 16 kHz buffers."""

    def __init__(self):
        if os.name != "nt":
            raise ValueError("Windows microphone capture is required for PC mode.")
        from ctypes import wintypes
        class Format(ctypes.Structure):
            _fields_ = [("format", wintypes.WORD), ("channels", wintypes.WORD), ("rate", wintypes.DWORD),
                        ("bytes_per_second", wintypes.DWORD), ("alignment", wintypes.WORD),
                        ("bits", wintypes.WORD), ("extra", wintypes.WORD)]
        class Header(ctypes.Structure):
            _fields_ = [("data", ctypes.c_void_p), ("length", wintypes.DWORD), ("recorded", wintypes.DWORD),
                        ("user", ctypes.c_size_t), ("flags", wintypes.DWORD), ("loops", wintypes.DWORD),
                        ("next", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]
        self.winmm = ctypes.WinDLL("winmm", use_last_error=True)
        self.handle = ctypes.c_void_p()
        self.size = ctypes.sizeof(Header)
        self.winmm.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.UINT, ctypes.POINTER(Format), ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
        self.winmm.waveInOpen.restype = wintypes.UINT
        for name in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
            method = getattr(self.winmm, name)
            method.argtypes = [ctypes.c_void_p, ctypes.POINTER(Header), wintypes.UINT]
            method.restype = wintypes.UINT
        for name in ("waveInStart", "waveInStop", "waveInReset", "waveInClose"):
            method = getattr(self.winmm, name)
            method.argtypes, method.restype = [ctypes.c_void_p], wintypes.UINT
        self.buffers, self.headers = [], []
        self.prepared = []
        fmt = Format(1, 1, 16000, 32000, 2, 16, 0)
        self._check(self.winmm.waveInOpen(ctypes.byref(self.handle), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0))
        try:
            for _ in range(4):
                data = ctypes.create_string_buffer(6400)
                header = Header(ctypes.addressof(data), 6400, 0, 0, 0, 0, None, 0)
                self.buffers.append(data)
                self.headers.append(header)
                self._check(self.winmm.waveInPrepareHeader(self.handle, ctypes.byref(header), self.size))
                self.prepared.append(header)
                self._check(self.winmm.waveInAddBuffer(self.handle, ctypes.byref(header), self.size))
            self._check(self.winmm.waveInStart(self.handle))
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(result):
        if result:
            raise ValueError("Windows could not open or read the default microphone. Check device and privacy settings.")

    def chunks(self, stopping):
        cursor, last_audio = 0, time.monotonic()
        while not stopping.is_set():
            header = self.headers[cursor]
            if header.flags & 1:  # WHDR_DONE
                size = int(header.recorded)
                if not 0 < size <= 6400 or size % 2:
                    raise ValueError("The microphone returned an invalid PCM buffer.")
                data = ctypes.string_at(header.data, size)
                header.recorded = 0
                self._check(self.winmm.waveInAddBuffer(self.handle, ctypes.byref(header), self.size))
                cursor = (cursor + 1) % len(self.headers)
                last_audio = time.monotonic()
                yield data
            elif time.monotonic() - last_audio > 5:
                raise ValueError("The microphone stopped supplying audio.")
            else:
                stopping.wait(0.025)

    def close(self):
        if self.handle:
            self.winmm.waveInStop(self.handle)
            self.winmm.waveInReset(self.handle)
            for header in self.prepared:
                self.winmm.waveInUnprepareHeader(self.handle, ctypes.byref(header), self.size)
            self.winmm.waveInClose(self.handle)
            self.handle = None


class Decoder:
    def __init__(self, data_dir):
        import numpy as np
        import sherpa_onnx
        from ariadne._media_worker import assets
        profile = load_profile(data_dir)
        paths = assets(profile, "sherpa")
        self.np = np
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(tokens=paths["tokens"], encoder=paths["encoder"],
            decoder=paths["decoder"], joiner=paths["joiner"], num_threads=2, sample_rate=16000,
            feature_dim=80, provider="cpu", decoding_method="greedy_search", enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=0.8, rule3_min_utterance_length=20)
        self.stream = self.recognizer.create_stream()

    def reset(self):
        self.recognizer.reset(self.stream)

    def feed(self, pcm):
        self.stream.accept_waveform(16000, self.np.frombuffer(pcm, dtype="<i2").astype(self.np.float32) / 32768)
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        if self.recognizer.is_endpoint(self.stream):
            text = self.recognizer.get_result(self.stream).strip()
            self.reset()
            if text:
                return text[:3000]
        return None

    def finish(self):
        self.stream.accept_waveform(16000, self.np.zeros(16000, dtype=self.np.float32))
        self.stream.input_finished()
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        return self.recognizer.get_result(self.stream).strip()[:3000]


def fixture(decoder, path):
    path = _allowed_path(path)
    if not path.is_relative_to(ROOT):
        raise ValueError("Wake qualification WAV must remain within Yikes.")
    texts = []
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000) or source.getnframes() > 90 * 16000:
            raise ValueError("Qualification requires mono PCM16 16 kHz WAV within 90 seconds.")
        while pcm := source.readframes(3200):
            if text := decoder.feed(pcm):
                texts.append(text)
    if text := decoder.finish():
        texts.append(text)
    return texts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--test-wav", type=Path)
    parser.add_argument("--session-seconds", type=int, default=8 * 60 * 60,
                        help="28800 for an eight-hour session, or 0 to listen until explicitly stopped")
    args = parser.parse_args()
    if args.session_seconds not in {0, 8 * 60 * 60}:
        raise ValueError("Session duration must be 28800 seconds or zero for until stopped.")
    data_dir = _allowed_path(args.data_dir)
    if args.probe:
        state = backend_status(load_profile(data_dir), "sherpa")
        emit({"event": "probe", "pc_supported": os.name == "nt", "input_device_count": microphone_count(),
              "sherpa_ready": state["ready"], "microphone_opened": False, "audio_saved": False})
        return
    limit_continuous_worker()
    decoder = Decoder(data_dir)
    from ariadne._media_worker import deny_network_and_execution
    sys.addaudithook(deny_network_and_execution)
    if args.test_wav:
        started = time.monotonic()
        emit({"event": "fixture", "texts": fixture(decoder, args.test_wav),
              "wall_seconds": round(time.monotonic() - started, 3), "microphone_opened": False})
        return
    stopping, muted = threading.Event(), threading.Event()
    def commands():
        try:
            while line := sys.stdin.buffer.readline(257):
                if len(line) > 256:
                    break
                message = json.loads(line)
                if type(message) is not dict or set(message) != {"action"}:
                    break
                if message["action"] == "stop":
                    break
                if message["action"] == "mute":
                    muted.set()
                elif message["action"] == "unmute":
                    muted.clear()
                else:
                    break
        except (OSError, ValueError, TypeError):
            pass
        finally:
            stopping.set()
    threading.Thread(target=commands, name="wake-control", daemon=True).start()
    if stopping.is_set():
        return
    source = WindowsInput()
    try:
        emit({"event": "ready", "backend": "sherpa-streaming", "audio_saved": False,
              "session_limit_seconds": args.session_seconds or None})
        started, was_muted = time.monotonic(), False
        for pcm in source.chunks(stopping):
            if args.session_seconds and time.monotonic() - started >= args.session_seconds:
                break
            if muted.is_set():
                if not was_muted:
                    decoder.reset()
                was_muted = True
                continue
            if was_muted:
                decoder.reset()
                was_muted = False
            if text := decoder.feed(pcm):
                emit({"event": "utterance", "text": text})
    finally:
        source.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError):
        emit({"event": "error", "message": "Local microphone or speech recognition failed. Check the default input device and local assets."})
        raise SystemExit(1)
