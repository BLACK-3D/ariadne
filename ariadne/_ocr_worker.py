"""Fixed internal OCR executable. Only stdin bytes are document input."""
from __future__ import annotations

import contextlib
import io
import json
import math
import os
import sys
import warnings
from pathlib import Path


def limit_process(memory_bytes, seconds):
    from ariadne.worker import apply_limits
    apply_limits(memory_bytes, seconds)


def deny_network_and_execution(event, args):
    if event.startswith("socket.") or event in {"urllib.Request", "http.client.connect", "subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.spawn"}:
        raise PermissionError("Network and child-process execution are disabled in the OCR worker")


def checked_dimensions(width, height, policy):
    if not all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0 for value in (width, height)):
        raise ValueError("Document has invalid image dimensions")
    width, height = math.ceil(width), math.ceil(height)
    if max(width, height) > policy.MAX_DIMENSION or width * height > policy.MAX_PIXELS:
        raise ValueError("OCR image/page exceeds its 6,000-pixel side or 12-million-pixel limit")
    return width, height


def images(extension, payload, policy):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = policy.MAX_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    if extension != ".pdf":
        with Image.open(io.BytesIO(payload), formats=["PNG", "JPEG"]) as image:
            expected = "PNG" if extension == ".png" else "JPEG"
            if image.format != expected or getattr(image, "n_frames", 1) != 1:
                raise ValueError("OCR requires a single PNG/JPEG image matching its filename")
            checked_dimensions(*image.size, policy)
            image.load()
            converted = ImageOps.exif_transpose(image).convert("RGB")
            try:
                yield 1, converted
            finally:
                converted.close()
        return
    import pypdfium2 as pdfium
    from pypdfium2 import raw
    with pdfium.PdfDocument(payload) as document:
        if raw.FPDF_GetSecurityHandlerRevision(document) >= 0:
            raise ValueError("Encrypted PDFs are unsupported; provide an unencrypted copy")
        if not 1 <= len(document) <= policy.MAX_PAGES:
            raise ValueError("OCR PDFs must contain 1 to 10 pages")
        for index in range(len(document)):
            page = document[index]
            try:
                checked_dimensions(*(value * 2 for value in page.get_size()), policy)
                bitmap = page.render(scale=2)
                try:
                    image = bitmap.to_pil().convert("RGB")
                    try:
                        yield index + 1, image
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()


def main():
    # Source location is fixed by the application, never supplied by a document.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ariadne import ocr as policy
    try:
        limit_process(policy.MEMORY_BYTES, policy.TIMEOUT_SECONDS)
        sys.addaudithook(deny_network_and_execution)
        header = sys.stdin.buffer.readline(4097)
        if len(header) > 4096 or not header.endswith(b"\n"):
            raise ValueError("Invalid OCR request header")
        request = json.loads(header)
        if not isinstance(request, dict) or set(request) != {"extension", "config"} or request["extension"] not in {".png", ".jpg", ".jpeg", ".pdf"}:
            raise ValueError("Invalid OCR request")
        _, config = policy._config(request["config"])
        payload = sys.stdin.buffer.read(policy.MAX_INPUT_BYTES + 1)
        if not payload or len(payload) > policy.MAX_INPUT_BYTES:
            raise ValueError("OCR input exceeds its byte limit")
        parts, notes, size = [], ["Machine OCR may misread text or ordering. Verify important values against the original document."], 0
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from rapidocr import RapidOCR
            import numpy as np
            params = {"Global.log_level": "error", "Global.max_side_len": 2400,
                      "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                      "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                      "EngineConfig.onnxruntime.use_cuda": False, "EngineConfig.onnxruntime.use_dml": False,
                      "Det.limit_type": "max", "Det.limit_side_len": 1600,
                      "Cls.cls_batch_num": 4, "Rec.rec_batch_num": 4}
            for role, section in (("det", "Det"), ("rec", "Rec"), ("cls", "Cls")):
                params[section + ".model_path"] = config["models"][role]
            engine = RapidOCR(params=params)
            for page, image in images(request["extension"], payload, policy):
                # RapidOCR's array input expects BGR rather than PIL's RGB.
                result = engine(np.asarray(image)[:, :, ::-1].copy())
                text = "\n".join(result.txts or ())
                if not text.strip():
                    notes.append("No readable text was recognized on page " + str(page) + ".")
                text = "".join(char for char in text if ord(char) >= 32 or char in "\n\t\r")
                section = ("\n" if parts else "") + "--- Page " + str(page) + " ---\n" + text
                if size + len(section) > policy.MAX_TEXT:
                    raise ValueError("OCR text exceeds its 250,000-character limit; split the document")
                parts.append(section)
                size += len(section)
        if not any(part.split("---\n", 1)[-1].strip() for part in parts):
            raise ValueError("OCR found no readable text; no result was imported")
        result = {"text": "".join(parts), "warnings": notes}
    except ValueError as error:
        result = {"error": str(error)[:500]}
    except (Exception, MemoryError):
        result = {"error": "OCR could not process this malformed, unsupported, encrypted, or resource-limited document"}
    encoded = json.dumps(result, ensure_ascii=False).encode()
    if len(encoded) > policy.MAX_RESPONSE:
        encoded = b'{"error":"OCR result exceeded its output limit"}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    if sys.argv[1:]:
        raise SystemExit("This is an internal OCR worker, not a general command runner")
    raise SystemExit(main())
