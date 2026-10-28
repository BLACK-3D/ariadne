"""Finite offline checks using generated images/speech and fixed local model prompts."""
from __future__ import annotations

import base64
import io
import json
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import Settings
from ariadne.media_tools import MediaTools, load_profile, run_media
from ariadne.model import Ollama
from ariadne.tools import ToolRegistry


def main():
    directory = ROOT / "data" / "media-qualification"
    directory.mkdir(parents=True, exist_ok=True)
    result = {"scope": "Finite smoke checks, not a general accuracy or throughput benchmark.", "checks": {}}
    settings = Settings(data_dir=ROOT / "data", model="qwen3.5:9b")
    media = MediaTools(ToolRegistry(settings))
    profile = load_profile(settings.data_dir)
    started = time.monotonic()
    speech = run_media({"action": "kokoro", "profile": profile, "text": "Ariadne is ready. The offline system has forty two supplies."})
    speech_bytes = base64.b64decode(speech["data"])
    (directory / "speech.wav").write_bytes(speech_bytes)
    # The recognizer and VAD accept a single explicit PCM format.
    import numpy as np
    with wave.open(io.BytesIO(speech_bytes), "rb") as source:
        raw = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
        source_rate = source.getframerate()
    samples = np.interp(np.arange(0, len(raw), source_rate / 16000), np.arange(len(raw)), raw).astype("<i2")
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(samples.tobytes())
    encoded = base64.b64encode(target.getvalue()).decode("ascii")
    transcribed = run_media({"action": "sherpa", "profile": profile, "data": encoded})
    activity = run_media({"action": "vad", "profile": profile, "data": encoded})
    result["checks"]["speech"] = {"ok": bool(speech_bytes and transcribed["text"] and activity["segments"]),
        "generated_wav_bytes": len(speech_bytes), "transcript": transcribed["text"], "partial_count": len(transcribed["partials"]),
        "speech_segments": activity["segments"], "combined_wall_seconds": round(time.monotonic() - started, 3)}
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 100, 220, 280), fill="red")
    draw.ellipse((400, 100, 580, 280), fill="blue")
    draw.text((180, 30), "ARIADNE 42", fill="black", font_size=36)
    path = settings.workspace / "media-qualification" / "vision.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    inspected = media.execute("media_inspect_image", {"paths": ["media-qualification/vision.png"],
        "question": "Read the exact label. What color and shape are on the left, and on the right?"})
    result["checks"]["vision"] = inspected
    tools = [{"type": "function", "function": {"name": "calculate", "description": "Calculate an arithmetic expression.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"], "additionalProperties": False}}}]
    client = Ollama(settings.ollama_url)
    for model in ("qwen3:8b", "qwen3.5:9b"):
        start = time.monotonic()
        message, usage = client.chat(model, [{"role": "user", "content": "Use the calculate tool to evaluate 17*23. Do not answer without calling the tool."}], tools)
        calls = message.get("tool_calls", [])
        passed = len(calls) == 1 and calls[0]["function"]["name"] == "calculate" and calls[0]["function"]["arguments"].get("expression", "").replace(" ", "") == "17*23"
        result["checks"][model] = {"ok": passed, "message": message, "usage": usage, "wall_seconds": round(time.monotonic() - start, 3)}
    (directory / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
