"""Finite generated-speech qualification. Does not record or open a microphone."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "runtime" / "media" / "python"))

from ariadne.config import Settings
from ariadne.network_policy import install_network_policy
from ariadne.voice import Voice
from ariadne.wake import WakeGate


def main():
    install_network_policy()
    import numpy as np
    directory = ROOT / "data" / "wake-qualification"
    directory.mkdir(parents=True, exist_ok=True)
    expected = "Ariadne, report the readiness status."
    started = time.monotonic()
    settings = Settings(data_dir=ROOT / "data")
    audio = Voice(settings).speak(expected)
    with wave.open(io.BytesIO(audio), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("Generated speech must be mono PCM16.")
        source_rate = source.getframerate()
        raw = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    samples = np.interp(np.arange(0, len(raw), source_rate / 16000), np.arange(len(raw)), raw).astype("<i2")
    path = directory / "generated-command.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(samples.tobytes())
    listener = ROOT / "scripts" / "wake_listener.py"
    probe = subprocess.run([sys.executable, "-I", "-B", str(listener), "--probe"], capture_output=True, timeout=30, check=True)
    decoded = subprocess.run([sys.executable, "-I", "-B", str(listener), "--test-wav", str(path)], capture_output=True, timeout=60, check=True)
    observed = json.loads(decoded.stdout)
    gate = WakeGate()
    events = [gate.accept(text) for text in observed["texts"]]
    passed = any(event.get("event") == "command" and "readiness" in event.get("command", "").lower() for event in events)
    receipt = {"created_at": datetime.now(timezone.utc).isoformat(), "passed": passed,
               "scope": "Known synthetic speech through the actual pinned streaming recognizer and wake gate. Not a live microphone, human pronunciation, false-wake-rate or acoustic echo qualification.",
               "expected": expected, "device_probe": json.loads(probe.stdout), "recognition": observed,
               "gate_events": events, "microphone_opened": False, "llm_calls": 0,
               "wall_seconds": round(time.monotonic() - started, 3)}
    (directory / "result.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
