"""On-demand, CPU-only OCR through a bounded isolated Python worker.

This module deliberately imports no imaging or inference library in the server.
Provisioning is explicit; runtime never downloads packages, fonts, or models.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import stat
import sys
import threading
from pathlib import Path, PurePosixPath

from .config import _allowed_path
from .worker import run_worker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_INPUT_BYTES = 10_000_000
MAX_PIXELS = 12_000_000
MAX_DIMENSION = 6_000
MAX_PAGES = 10
MAX_TEXT = 250_000
MAX_RESPONSE = 1_100_000
TIMEOUT_SECONDS = 90
MEMORY_BYTES = 2 * 1024 ** 3
MODEL_HASHES = {
    "det": ("PP-OCRv6_det_small.onnx", "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
    "rec": ("PP-OCRv6_rec_small.onnx", "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
    "cls": ("ch_ppocr_mobile_v2.0_cls_mobile.onnx", "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
}
CORE_VERSIONS = {"rapidocr": "3.10.0", "onnxruntime": "1.31.1", "pillow": "13.0.0", "pypdfium2": "5.14.0"}
_HASH_CACHE = {}
_HASH_LOCK = threading.Lock()
_WORKER_LOCK = threading.Lock()


def _local_file(value, max_bytes=None):
    path = _allowed_path(value)
    if not path.is_relative_to(PROJECT_ROOT):
        raise ValueError("OCR paths must remain inside this Ariadne installation")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or max_bytes is not None and info.st_size > max_bytes:
        raise ValueError("OCR files must be regular, unlinked files within their size limit")
    return path


def _digest(path):
    path = _local_file(path, 100_000_000)
    info = path.stat()
    key = (str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)
    with _HASH_LOCK:
        if key not in _HASH_CACHE:
            with path.open("rb") as stream:
                _HASH_CACHE[key] = hashlib.file_digest(stream, "sha256").hexdigest()
            if len(_HASH_CACHE) > 64:
                value = _HASH_CACHE[key]
                _HASH_CACHE.clear()
                _HASH_CACHE[key] = value
        return _HASH_CACHE[key]


def processor_identity(packages):
    material = {"profile": "cpu-text-v1", "models": MODEL_HASHES, "packages": packages,
                "worker": _digest(Path(__file__).with_name("_ocr_worker.py")), "adapter": _digest(Path(__file__)),
                "limits_worker": _digest(Path(__file__).with_name("worker.py")),
                "bootstrap": _digest(Path(__file__).with_name("_worker_bootstrap.py")),
                "path_guards": _digest(Path(__file__).with_name("tools.py")),
                "path_policy": _digest(Path(__file__).with_name("config.py")),
                "lock": _digest(PROJECT_ROOT / "requirements-ocr.txt"),
                "limits": [MAX_PIXELS, MAX_DIMENSION, MAX_PAGES, MAX_TEXT, TIMEOUT_SECONDS, MEMORY_BYTES]}
    identity = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"name": "rapidocr", "version": CORE_VERSIONS["rapidocr"], "engine": "onnxruntime-cpu",
            "engine_version": CORE_VERSIONS["onnxruntime"], "profile": "cpu-text-v1", "identity": identity}


def _config_path(settings_or_path):
    return Path(settings_or_path.data_dir) / "ocr-config.json" if hasattr(settings_or_path, "data_dir") else Path(settings_or_path)


def _config(settings_or_path):
    path = _local_file(_config_path(settings_or_path), 32_768)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("OCR configuration is invalid; run the explicit OCR provisioner") from None
    if not isinstance(data, dict) or set(data) != {"schema", "python", "models", "processor", "packages", "lock_sha256"} or data["schema"] != 1:
        raise ValueError("Unsupported OCR configuration schema")
    expected_pythons = { _allowed_path(PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")) }
    if os.name == "nt":
        expected_pythons.add(_allowed_path(PROJECT_ROOT / "runtime/python/python.exe"))
    python = _local_file(data["python"], 100_000_000)
    if python not in expected_pythons:
        raise ValueError("OCR must use the installation's private or bundled interpreter")
    if python != _allowed_path(sys.executable):
        raise ValueError("Start Ariadne with the same private or bundled interpreter used to provision OCR")
    packages = data["packages"]
    if not isinstance(packages, dict) or any(packages.get(key) != value for key, value in CORE_VERSIONS.items()):
        raise ValueError("OCR package versions do not match the pinned profile")
    lock = _local_file(PROJECT_ROOT / "requirements-ocr.txt", 32_768)
    if data["lock_sha256"] != _digest(lock):
        raise ValueError("OCR dependency lock changed; re-provision before using it")
    locked = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+) --hash=sha256:[a-f0-9]{64}", line)
        if not match or match[1] in locked:
            raise ValueError("OCR dependency lock is invalid")
        locked[match[1]] = match[2]
    if packages != locked or any(importlib.metadata.version(name) != expected for name, expected in locked.items()):
        raise ValueError("OCR installed packages differ from the full provisioned lock")
    if not isinstance(data["models"], dict) or set(data["models"]) != set(MODEL_HASHES):
        raise ValueError("OCR requires the three pinned local models")
    for role, (filename, digest) in MODEL_HASHES.items():
        model = _local_file(data["models"][role], 50_000_000)
        if model.name != filename or _digest(model) != digest:
            raise ValueError("OCR model digest does not match the pinned release")
        data["models"][role] = str(model)
    if data["processor"] != processor_identity(packages):
        raise ValueError("OCR processor changed; re-provision before using it")
    data["python"] = str(python)
    return path, data


def ocr_status(settings_or_configpath):
    limits = {"input_bytes": MAX_INPUT_BYTES, "pixels_per_page": MAX_PIXELS, "dimension": MAX_DIMENSION,
              "pages": MAX_PAGES, "output_chars": MAX_TEXT, "seconds": TIMEOUT_SECONDS, "memory_bytes": MEMORY_BYTES, "cpu_threads": 2}
    try:
        _, config = _config(settings_or_configpath)
        return {"ready": True, "configured": True, "formats": ["png", "jpg", "jpeg", "pdf"], "processor": config["processor"],
                "limits": limits, "reason": "Pinned local CPU OCR; loaded only for an extraction request."}
    except (ValueError, OSError, TypeError, KeyError, importlib.metadata.PackageNotFoundError):
        return {"ready": False, "configured": False, "formats": [], "limits": limits,
                "reason": "OCR is not provisioned or its pinned files changed. Run scripts/provision_ocr.py explicitly."}


def extract_ocr(name, payload, configpath, *, cancelled=None):
    if not isinstance(name, str) or not name or len(name) > 200 or any(char in name for char in "/\\:\x00"):
        raise ValueError("OCR accepts a local document filename, never a URL or path")
    extension = PurePosixPath(name).suffix.lower()
    if extension not in {".png", ".jpg", ".jpeg", ".pdf"}:
        raise ValueError("OCR supports PNG, JPEG, and PDF files")
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_INPUT_BYTES:
        raise ValueError("OCR documents must contain 1 byte to 10 MB")
    path, config = _config(configpath)
    if not _WORKER_LOCK.acquire(blocking=False):
        raise ValueError("Another OCR extraction is in progress")
    try:
        return _extract(extension, payload, path, config, cancelled)
    finally:
        _WORKER_LOCK.release()


def _extract(extension, payload, configpath, config, cancelled):
    header = json.dumps({"extension": extension, "config": str(configpath)}, separators=(",", ":")).encode() + b"\n"
    output = run_worker(Path(__file__).with_name("_ocr_worker.py"), header + payload,
                        timeout=TIMEOUT_SECONDS, memory_bytes=MEMORY_BYTES, cpu_seconds=TIMEOUT_SECONDS,
                        max_output_bytes=MAX_RESPONSE, max_input_bytes=MAX_INPUT_BYTES + 4096, cancelled=cancelled)
    try:
        result = json.loads(output)
    except (ValueError, UnicodeError):
        raise ValueError("OCR worker returned an invalid result") from None
    if not isinstance(result, dict):
        raise ValueError("OCR worker returned an invalid result")
    if result.get("error"):
        raise ValueError(str(result["error"])[:500])
    text, warnings = result.get("text"), result.get("warnings")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError("OCR found no usable text or exceeded its text limit")
    if not isinstance(warnings, list) or len(warnings) > 64 or any(not isinstance(item, str) or len(item) > 1000 for item in warnings):
        raise ValueError("OCR worker returned invalid extraction notes")
    return {"text": text, "warnings": warnings, "format": "ocr-" + extension[1:], "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "processor": config["processor"]}
