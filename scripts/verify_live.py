"""Exercise the real local model and optional voice without recording a microphone."""
import json
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ariadne.config import load_settings
from ariadne.engine import Engine
from ariadne.store import Store
from ariadne.voice import Voice


def main():
    base = load_settings()
    directory = base.data_dir / "verification" / uuid.uuid4().hex
    settings = replace(base, data_dir=directory, workspace=directory / "workspace", connectors_path=directory / "connectors.json", max_steps=6)
    settings.workspace.mkdir(parents=True)
    store = Store(directory / "state.sqlite3")
    store.document_add("Workshop brief", "Business name: Cobalt Workshop. Initial stock ceiling: 12 units. All outputs are local drafts. No external actions are authorized.")
    engine = Engine(settings, store)
    prompt = "Search the local knowledge for the Workshop brief. Create business/launch-plan.md with a short, actionable launch checklist for that business, incorporating its exact initial stock ceiling. Use the file_write tool to save it. Do not contact anyone or perform external actions. Report the saved path."
    run = store.new_run(prompt)
    store.next_run()
    start = time.monotonic()
    engine.process(run["id"])
    detail = store.details(run["id"])
    artifact = settings.workspace / "business/launch-plan.md"
    content = artifact.read_text(encoding="utf-8") if artifact.is_file() else ""
    model_ok = detail["run"]["status"] == "completed" and "12" in content and "Cobalt" in content
    result = {"model": settings.model, "model_passed": model_ok, "status": detail["run"]["status"], "steps": detail["run"]["step_count"],
              "seconds": round(time.monotonic() - start, 3), "artifact": str(artifact), "error": detail["run"]["error"],
              "model_usage": [e["data"] for e in detail["events"] if e["kind"] == "model_usage"]}
    voice = Voice(base)
    result["voice_available"] = voice.status()
    if voice.status()["speech"] and voice.status()["transcription"]:
        try:
            import io
            import struct
            import wave
            audio = voice.speak("The local assistant is ready. Inventory is twelve units.")
            with wave.open(io.BytesIO(audio)) as source:
                channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
                frames = source.readframes(source.getnframes())
            if width != 2:
                raise ValueError("Verification expects PCM16 speech output")
            samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
            mono = [sum(samples[i:i + channels]) / channels for i in range(0, len(samples), channels)]
            converted = []
            for i in range(int(len(mono) * 16000 / rate)):
                position = i * rate / 16000
                index = int(position)
                fraction = position - index
                value = mono[min(index, len(mono) - 1)] * (1 - fraction) + mono[min(index + 1, len(mono) - 1)] * fraction
                converted.append(round(value))
            output = io.BytesIO()
            with wave.open(output, "wb") as target:
                target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
                target.writeframes(struct.pack("<" + "h" * len(converted), *converted))
            transcript = voice.transcribe(output.getvalue())
            result["voice_roundtrip"] = {"passed": "local" in transcript.lower() and "ready" in transcript.lower(), "transcript": transcript}
        except Exception as error:
            result["voice_roundtrip"] = {"passed": False, "error": str(error)}
    (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (base.data_dir / "verification-latest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not model_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
