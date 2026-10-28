"""Fixed offline media worker. stdin JSON selects a finite implementation."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import sys
import wave
import warnings


def deny_network_and_execution(event, args):
    if event.startswith("socket.") or event in {"urllib.Request", "http.client.connect", "subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.spawn"}:
        raise PermissionError("Network and child-process execution are disabled in the media worker")


def normalize_image(data):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 12_000_000
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as source:
        if getattr(source, "n_frames", 1) != 1 or max(source.size) > 6000 or source.width * source.height > 12_000_000:
            raise ValueError("Images must be a single PNG/JPEG within 6000 pixels per side and 12 million pixels.")
        source.load()
        image = ImageOps.exif_transpose(source).convert("RGB")
        try:
            image.thumbnail((1536, 1536))
            target = io.BytesIO()
            image.save(target, format="PNG", optimize=False)
            result = target.getvalue()
            if len(result) > 3_000_000:
                raise ValueError("Normalized image exceeds 3 MB. Resize or simplify the image first.")
            return {"ok": True, "data": base64.b64encode(result).decode("ascii"), "width": image.width, "height": image.height}
        finally:
            image.close()


def assets(profile, backend):
    from ariadne.media_tools import ROOT, backend_status
    from ariadne.config import _allowed_path
    state = backend_status(profile, backend, verify=True)
    if not state["ready"]:
        raise ValueError(backend + ": " + state.get("reason", "not ready"))
    result = {}
    for role, asset in profile["backends"][backend]["assets"].items():
        target = _allowed_path(ROOT / asset["path"])
        if not target.is_relative_to(ROOT / "runtime" / "media"):
            raise ValueError("Media assets must remain inside runtime/media.")
        result[role] = str(target)
    return result


def audio_samples(data):
    import numpy as np
    from ariadne.media_tools import wav_info
    wav_info(data)
    with wave.open(io.BytesIO(data), "rb") as source:
        return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768


def ort_session(path):
    import onnxruntime as ort
    ort.disable_telemetry_events()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    return ort.InferenceSession(path, sess_options=options, providers=["CPUExecutionProvider"])


def kokoro(request):
    from kokoro_onnx import Kokoro
    import numpy as np
    from ariadne.media_tools import _text
    text = _text(request["text"], "text", 2000)
    paths = assets(request["profile"], "kokoro")
    engine = Kokoro.from_session(ort_session(paths["model"]), paths["voices"])
    samples, sample_rate = engine.create(text, voice="af_heart", speed=1.0, lang="en-us")
    if sample_rate != 24000 or samples.ndim != 1 or len(samples) > 24000 * 90 or not np.isfinite(samples).all():
        raise ValueError("Speech output exceeded the duration or format limit.")
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    data = target.getvalue()
    if len(data) > 3_000_000:
        raise ValueError("Speech output exceeds 3 MB.")
    return {"ok": True, "data": base64.b64encode(data).decode("ascii"), "sample_rate": sample_rate, "backend": "kokoro"}


def sherpa(request):
    import numpy as np
    import sherpa_onnx
    paths = assets(request["profile"], "sherpa")
    samples = audio_samples(base64.b64decode(request["data"], validate=True))
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(tokens=paths["tokens"], encoder=paths["encoder"],
        decoder=paths["decoder"], joiner=paths["joiner"], num_threads=2, sample_rate=16000, feature_dim=80,
        decoding_method="greedy_search", provider="cpu", enable_endpoint_detection=False)
    stream = recognizer.create_stream()
    partials, last = [], ""
    for start in range(0, len(samples), 3200):
        stream.accept_waveform(16000, samples[start:start + 3200])
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = recognizer.get_result(stream)
        if text != last and len(partials) < 450:
            partials.append({"end_seconds": round(min(start + 3200, len(samples)) / 16000, 3), "text": text[:16000]})
            last = text
    stream.accept_waveform(16000, np.zeros(4800, dtype=np.float32))
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    text = recognizer.get_result(stream)
    if len(text) > 64000:
        raise ValueError("Transcription exceeds its text limit.")
    return {"ok": True, "text": text, "partials": partials, "backend": "sherpa"}


def vad(request):
    import numpy as np
    paths = assets(request["profile"], "silero")
    samples = audio_samples(base64.b64decode(request["data"], validate=True))
    session = ort_session(paths["model"])
    state, context = np.zeros((2, 1, 128), np.float32), np.zeros((1, 64), np.float32)
    active, start, silence_start = False, 0, None
    segments = []
    for offset in range(0, len(samples), 512):
        chunk = np.zeros((1, 512), np.float32)
        available = samples[offset:offset + 512]
        chunk[0, :len(available)] = available
        x = np.concatenate((context, chunk), axis=1)
        probability, state = session.run(None, {"input": x, "state": state, "sr": np.array(16000, np.int64)})
        context = x[:, -64:]
        score = float(probability.reshape(-1)[0])
        if score >= .5:
            if not active:
                active, start = True, offset
            silence_start = None
        elif active and score < .35:
            if silence_start is None:
                silence_start = offset
            if offset - silence_start >= 3200:
                if silence_start - start >= 4000:
                    segments.append({"start_seconds": round(max(0, start - 480) / 16000, 3),
                                     "end_seconds": round(min(len(samples), silence_start + 480) / 16000, 3)})
                active, silence_start = False, None
    if active and len(samples) - start >= 4000:
        segments.append({"start_seconds": round(max(0, start - 480) / 16000, 3), "end_seconds": round(len(samples) / 16000, 3)})
    return {"ok": True, "segments": segments, "backend": "silero", "note": "Speech activity timestamps; no speaker identity inference."}


def diarization(request):
    import sherpa_onnx
    paths = assets(request["profile"], "diarization")
    samples = audio_samples(base64.b64decode(request["data"], validate=True))
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=paths["segmentation"], window_shift_ratio=0.1)),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=paths["embedding"]),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5)
    if not config.validate():
        raise ValueError("Diarization assets do not match the fixed model profile.")
    engine = sherpa_onnx.OfflineSpeakerDiarization(config)
    if engine.sample_rate != 16000:
        raise ValueError("Diarization model must use 16 kHz audio.")
    segments = [{"start_seconds": round(float(item.start), 3), "end_seconds": round(float(item.end), 3),
                 "speaker": "SPEAKER_" + str(int(item.speaker)).zfill(2)}
                for item in engine.process(samples).sort_by_start_time()]
    if len(segments) > 1000:
        raise ValueError("Diarization exceeded its segment limit.")
    return {"ok": True, "segments": segments, "backend": "sherpa", "note": "Anonymous approximate labels; not speaker identification."}


def flux(request):
    """A local directory manifest pins every file before diffusers opens it."""
    import hashlib
    from pathlib import Path
    from ariadne.config import _allowed_path
    paths = assets(request["profile"], "flux")
    manifest_path = Path(paths["manifest"])
    if manifest_path.stat().st_size > 500_000:
        raise ValueError("FLUX manifest exceeds its limit.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if type(manifest) is not dict or set(manifest) != {"model", "files"} or manifest["model"] != "black-forest-labs/FLUX.2-klein-4B":
        raise ValueError("Only the qualified FLUX.2-klein-4B family is accepted.")
    if type(manifest["files"]) is not dict or not 1 <= len(manifest["files"]) <= 200:
        raise ValueError("Invalid FLUX file manifest.")
    directory = manifest_path.parent
    for name, digest in manifest["files"].items():
        target = _allowed_path(directory / name)
        if not target.is_relative_to(directory) or target.suffix.lower() not in {".json", ".txt", ".model", ".safetensors", ".jinja", ".tiktoken"}:
            raise ValueError("Unsupported or escaping FLUX model artifact.")
        with target.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != digest:
                raise ValueError("FLUX file integrity failed.")
    # Refuse extra Python/pickle/alternate model files instead of trusting remote code.
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    if actual != set(manifest["files"]) | {manifest_path.name}:
        raise ValueError("FLUX directory contains unlisted artifacts.")
    import torch
    from diffusers import Flux2KleinPipeline
    if not torch.cuda.is_available():
        raise ValueError("This finite image-generation profile requires a qualified CUDA GPU.")
    pipeline = Flux2KleinPipeline.from_pretrained(str(directory), torch_dtype=torch.bfloat16,
        local_files_only=True, use_safetensors=True)
    pipeline.enable_model_cpu_offload()
    image = pipeline(prompt=request["prompt"], width=request["width"], height=request["height"],
        num_inference_steps=4, guidance_scale=1.0, generator=torch.Generator("cpu").manual_seed(request["seed"])).images[0]
    target = io.BytesIO()
    image.save(target, format="PNG")
    if len(target.getvalue()) > 3_000_000:
        raise ValueError("Generated image exceeds its output byte limit.")
    return {"ok": True, "data": base64.b64encode(target.getvalue()).decode("ascii"), "backend": "flux"}


def main():
    from ariadne.media_tools import MEDIA_PYTHON
    from ariadne.config import _allowed_path
    site = _allowed_path(MEDIA_PYTHON)
    if site.exists():
        sys.path.insert(0, str(site))
    sys.addaudithook(deny_network_and_execution)
    try:
        raw = sys.stdin.buffer.read(12_000_001)
        if len(raw) > 12_000_000:
            raise ValueError("Media request exceeds 12 MB.")
        request = json.loads(raw)
        if type(request) is not dict or request.get("action") not in {"normalize_image", "kokoro", "sherpa", "vad", "diarization", "flux"}:
            raise ValueError("Unsupported media operation.")
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
            action = request["action"]
            if action == "normalize_image":
                result = normalize_image(base64.b64decode(request["data"], validate=True))
            else:
                if action == "flux":
                    from ariadne.media_tools import FLUX_PYTHON
                    sys.path.insert(0, str(_allowed_path(FLUX_PYTHON)))
                result = {"kokoro": kokoro, "sherpa": sherpa, "vad": vad, "diarization": diarization, "flux": flux}[action](request)
    except (ValueError, KeyError, TypeError) as error:
        result = {"ok": False, "error": str(error)[:400]}
    except (Exception, MemoryError):
        result = {"ok": False, "error": "Local media engine failed, lacks qualified assets/dependencies, or exceeded its resource limits."}
    encoded = json.dumps(result, ensure_ascii=True).encode("ascii")
    if len(encoded) > 4_000_000:
        encoded = b'{"ok":false,"error":"Media result exceeds 4 MB."}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    if sys.argv[1:]:
        raise SystemExit("This is a fixed internal worker.")
    main()
