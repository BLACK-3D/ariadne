"""Fixed offline specialist worker. No caller filenames or host callbacks."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
import socket
import subprocess
import struct
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path
from ariadne.knowledge_tools import (DIMENSIONS, MAX_CHUNKS, MAX_FILE_BYTES, MAX_OUTPUT,
                                     MODEL, canonical, file_hash, validate_json, library_results)


def deny_network(*args, **kwargs):
    raise OSError("Networking is disabled in this offline worker.")


def setup(profile, *, loopback=False):
    site = profile.get("site_packages")
    if site:
        path = _allowed_path(site)
        path.relative_to(ROOT / "runtime" / "knowledge")
        sys.path.insert(0, str(path))
    for name, version in profile["packages"].items():
        if importlib.metadata.version(name) != version:
            raise ValueError("Optional knowledge package version mismatch.")
    for item in profile.get("assets", []):
        path = _allowed_path(ROOT / "runtime" / "knowledge" / item["path"])
        path.relative_to(ROOT / "runtime" / "knowledge")
        if file_hash(path, 2_000_000_000) != item["sha256"]:
            raise ValueError("Knowledge asset hash mismatch.")
    # Fail closed even if a dependency ignores HF offline environment switches.
    # Native code still needs its own policy: CPU providers and local artifacts.
    if not loopback:
        socket.socket.connect = deny_network
        socket.socket.connect_ex = deny_network
        socket.create_connection = deny_network
        socket.getaddrinfo = deny_network


def kiwix(payload):
    from ariadne.tools import _bounded_opener
    profile = payload["profile"]
    binary = _allowed_path(profile["binary"])
    binary.relative_to(ROOT / "runtime/knowledge/kiwix")
    archives = profile["archives"]
    if not 1 <= len(archives) <= 5 or payload.get("action") not in {"search", "read"}:
        raise ValueError("Invalid managed library search request.")
    if payload["action"] == "search" and (type(payload["query"]) is not str or not 1 <= len(payload["query"]) <= 4000 or type(payload["limit"]) is not int or not 1 <= payload["limit"] <= 10):
        raise ValueError("Invalid managed library search request.")
    paths = []
    for archive in archives:
        path = _allowed_path(archive["path"])
        path.relative_to(ROOT / "runtime/knowledge/library")
        if file_hash(path, 20_000_000) != archive["sha256"]:
            raise ValueError("Kiwix archive hash differs.")
        paths.append(str(path))
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = "http://127.0.0.1:" + str(port)
    process = subprocess.Popen([str(binary), "--address=127.0.0.1", "--port=" + str(port), "--threads=1", "--blockexternal", *paths],
                               cwd=str(binary.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=dict(os.environ),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                raise ValueError("The owned native Kiwix server failed to start.")
            try:
                with _bounded_opener(1) as (opener, expired):
                    with opener.open(origin + "/", timeout=1) as response:
                        response.read(1024)
                time.sleep(0.05)
                if process.poll() is not None:
                    raise ValueError("The owned native Kiwix server exited before search.")
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError("Kiwix local startup deadline exceeded.")
                time.sleep(0.1)
        # Search each archive by its fixed name to preserve exact provenance.
        if payload["action"] == "read":
            archive = next(item for item in archives if item["name"] == payload["archive"])
            path = payload["article_path"]
            decoded = urllib.parse.unquote(urllib.parse.unquote(path))
            if not decoded.startswith("/content/" + archive["name"] + "/") or any(part == ".." for part in decoded.split("/")) or any(ord(c) < 32 for c in decoded) or "\\" in decoded or any(c in path for c in "?#\\"):
                raise ValueError("Article path leaves the fixed archive.")
            with _bounded_opener(10) as (opener, expired):
                with opener.open(origin + path, timeout=10) as response:
                    raw = response.read(524_289)
                    content_type = response.headers.get("Content-Type", "")
            if len(raw) > 524_288 or "text/html" not in content_type:
                raise ValueError("Library article is oversized or not HTML text.")
            from html.parser import HTMLParser
            class TextOnly(HTMLParser):
                def __init__(self):
                    super().__init__(convert_charrefs=True)
                    self.ignored = 0
                    self.parts = []
                def handle_starttag(self, tag, attrs):
                    if tag in {"script", "style"}:
                        self.ignored += 1
                    if tag in {"p", "div", "pre", "h1", "h2", "h3", "li", "br"}:
                        self.parts.append("\n")
                def handle_endtag(self, tag):
                    if tag in {"script", "style"} and self.ignored:
                        self.ignored -= 1
                def handle_data(self, text):
                    if not self.ignored:
                        self.parts.append(text)
            parser = TextOnly()
            parser.feed(raw.decode("utf-8", errors="replace"))
            text = "".join(parser.parts)
            return {"ok": True, "text": text[:50_000], "truncated": len(text) > 50_000,
                    "archive": archive["name"], "archive_date": archive["date"], "archive_sha256": archive["sha256"],
                    "article_path": path, "article_sha256": hashlib.sha256(raw).hexdigest(),
                    "citation": "zim:" + archive["name"] + ":" + path,
                    "notice": "Text from a dated offline reference snapshot. Images/scripts were not executed or fetched."}
        per_archive = []
        for archive in archives:
            if payload.get("archive") and payload["archive"] != archive["name"]:
                continue
            url = origin + "/search?" + urllib.parse.urlencode({"books.name": archive["name"], "pattern": payload["query"], "format": "xml", "pageLength": payload["limit"]})
            with _bounded_opener(10) as (opener, expired):
                with opener.open(urllib.request.Request(url, headers={"Accept": "application/xml"}), timeout=10) as response:
                    data = response.read(262_145)
            result = library_results(data, origin, [archive], payload["limit"])
            for row in result["results"]:
                # On-demand server exits after search. Keep a durable archive-
                # relative citation rather than publishing a dead ephemeral URL.
                row["article_path"] = urllib.parse.urlsplit(row.pop("url")).path
                row["citation"] = "zim:" + archive["name"] + ":" + row["article_path"]
            per_archive.append(result["results"])
        # Interleave ranks, avoiding an arbitrary first-archive monopoly.
        combined = [rows[rank] for rank in range(payload["limit"]) for rows in per_archive if rank < len(rows)]
        return {"ok": True, "results": combined[:payload["limit"]], "snapshot": True, "server_version": "3.8.1",
                "service": "On demand; no background process retained.", "notice": "Offline archive snapshots; Python August 2026 and SQLite October 2026. Source material is untrusted data."}
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def embedding(profile):
    if profile.get("model") != MODEL or profile.get("dimensions") != DIMENSIONS:
        raise ValueError("Unsupported embedding model profile.")
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL, specific_model_path=profile["model_path"],
                         local_files_only=True, threads=2, providers=["CPUExecutionProvider"])


def pack(vector):
    import numpy as np
    vector = np.asarray(vector, dtype="<f4")
    if vector.shape != (DIMENSIONS,) or not np.isfinite(vector).all():
        raise ValueError("Embedding has invalid dimensions or nonfinite elements.")
    return base64.b64encode(vector.tobytes()).decode("ascii")


def embed(payload):
    texts = payload["texts"]
    if type(texts) is not list or not 1 <= len(texts) <= MAX_CHUNKS or any(type(text) is not str or len(text) > 1600 for text in texts):
        raise ValueError("Invalid embedding corpus.")
    model = embedding(payload["profile"])
    vectors = [pack(vector) for vector in model.passage_embed(texts, batch_size=32, parallel=None)]
    if len(vectors) != len(texts):
        raise ValueError("Embedding model returned the wrong number of vectors.")
    return {"ok": True, "vectors": vectors}


def rank(payload):
    import sqlite3
    import sqlite_vec
    import numpy as np
    vectors = payload["vectors"]
    if type(vectors) is not list or len(vectors) > MAX_CHUNKS:
        raise ValueError("Invalid semantic vector count.")
    if not vectors:
        return {"ok": True, "ranked": []}
    if type(payload["query"]) is not str or not 1 <= len(payload["query"]) <= 4000:
        raise ValueError("Invalid semantic query.")
    blobs = []
    for text in vectors:
        blob = base64.b64decode(text, validate=True)
        if len(blob) != DIMENSIONS * 4 or not np.isfinite(np.frombuffer(blob, dtype="<f4")).all():
            raise ValueError("Semantic vector has invalid dimensions or elements.")
        blobs.append(blob)
    model = embedding(payload["profile"])
    query = base64.b64decode(pack(next(model.query_embed(payload["query"]))), validate=True)
    with sqlite3.connect(":memory:") as db:
        db.enable_load_extension(True)
        try:
            sqlite_vec.load(db)
        finally:
            db.enable_load_extension(False)
        db.execute(f"CREATE VIRTUAL TABLE vectors USING vec0(id INTEGER PRIMARY KEY, embedding float[{DIMENSIONS}] distance_metric=cosine)")
        db.executemany("INSERT INTO vectors(id,embedding) VALUES (?,?)", enumerate(blobs))
        rows = db.execute("SELECT id,distance FROM vectors WHERE embedding MATCH ? AND k=? ORDER BY distance", (query, min(20, len(blobs)))).fetchall()
    return {"ok": True, "ranked": [{"index": row[0], "distance": row[1]} for row in rows]}


def monty(payload):
    from pydantic_monty import CollectString, Monty
    profile = payload["profile"]
    binary = _allowed_path(profile["binary"])
    binary.relative_to(ROOT)
    if file_hash(binary, 100_000_000) != profile["binary_sha256"]:
        raise ValueError("Monty executable hash mismatch.")
    code = payload["code"]
    if type(code) is not str or not 1 <= len(code) <= 20_000:
        raise ValueError("Invalid code input.")
    data = validate_json(payload["data"])
    printed = CollectString(max_bytes=32_768)
    # Fresh process and session per invocation. No os callback, mount, host
    # external functions, external modules, or untrusted state restoration.
    with Monty(binary_path=str(binary), min_processes=1, max_processes=1,
               checkout_timeout=1.0, request_timeout=4.0, max_checkouts_per_worker=1) as pool:
        with pool.checkout(limits={"max_memory": 32_000_000, "max_feed_duration_secs": 2.0,
                                  "max_turn_duration_secs": 2.0, "max_recursion_depth": 100,
                                  "max_suspensions": 100, "max_total_sleep_secs": 0.0},
                           os_policy={"datetime": datetime(2026, 1, 1), "sleep": "zero",
                                      "random_start": {"seed": 0}}) as session:
            answer = session.feed_run(code, inputs={"data": data}, print_callback=printed)
            validate_json(answer, max_bytes=65_536)
    return {"ok": True, "value": answer, "stdout": printed.output, "interpreter": "Monty 1.4.0",
            "limits": {"heap_bytes": 32_000_000, "execution_seconds": 2, "output_bytes": 65_536},
            "authority": "No filesystem mounts, network, host functions or external modules."}


def docling(payload):
    snapshot = payload["snapshot"]
    data = base64.b64decode(snapshot["data"], validate=True)
    if len(data) > MAX_FILE_BYTES or len(data) != snapshot["bytes"] or hashlib.sha256(data).hexdigest() != snapshot["sha256"]:
        raise ValueError("Document snapshot exceeds limits or fails its hash.")
    suffix = Path(snapshot["name"]).suffix.lower()
    if suffix not in {".pdf", ".docx"} or Path(snapshot["name"]).name != snapshot["name"]:
        raise ValueError("Unsupported document snapshot.")
    if suffix == ".docx":
        from ariadne.documents import _archive
        with _archive(data):
            pass  # Bound and validate decompression/member paths before Docling.
    from docling.datamodel.base_models import InputFormat, DocumentStream
    from docling.datamodel.backend_options import MsWordBackendOptions, PdfBackendOptions
    from docling.datamodel.pipeline_options import PdfPipelineOptions, NativePdfPipelineOptions
    from docling.datamodel.pipeline_options import TableFormerMode
    from docling.datamodel.accelerator_options import AcceleratorDevice
    from docling.document_converter import DocumentConverter, PdfFormatOption, NativePdfFormatOption, WordFormatOption
    profile = payload["profile"]
    if profile.get("mode") == "layout":
        artifacts = _allowed_path(profile["artifacts_path"])
        artifacts.relative_to(ROOT / "runtime" / "knowledge")
        options = PdfPipelineOptions(artifacts_path=artifacts, enable_remote_services=False,
                                     allow_external_plugins=False, do_ocr=False, do_table_structure=True,
                                     do_picture_classification=False, do_picture_description=False)
        options.accelerator_options.num_threads = 2
        options.accelerator_options.device = AcceleratorDevice.CPU
        options.table_structure_options.mode = TableFormerMode.FAST
        pdf = PdfFormatOption(pipeline_options=options, backend_options=PdfBackendOptions(enable_remote_fetch=False, enable_local_fetch=False))
    elif profile.get("mode") == "native":
        pdf = NativePdfFormatOption(pipeline_options=NativePdfPipelineOptions(enable_remote_services=False, allow_external_plugins=False),
                                    backend_options=PdfBackendOptions(enable_remote_fetch=False, enable_local_fetch=False))
    else:
        raise ValueError("Unknown offline Docling pipeline mode.")
    converter = DocumentConverter(allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
                                  format_options={InputFormat.PDF: pdf, InputFormat.DOCX: WordFormatOption(
                                      backend_options=MsWordBackendOptions(enable_remote_fetch=False, enable_local_fetch=False))})
    result = converter.convert(DocumentStream(name=snapshot["name"], stream=io.BytesIO(data)),
                               max_num_pages=50, max_file_size=MAX_FILE_BYTES, raises_on_error=True)
    markdown = result.document.export_to_markdown()
    if len(markdown) > 100_000:
        raise ValueError("Structured document exceeds the 100000-character output limit.")
    items = []
    for item, level in result.document.iterate_items():
        if len(items) >= 500:
            break
        pages = sorted({prov.page_no for prov in getattr(item, "prov", [])})
        text = getattr(item, "text", None)
        if text is not None:
            items.append({"label": str(item.label), "text": text[:3000], "pages": pages, "level": level})
    return {"ok": True, "markdown": markdown, "items": items, "tables": len(result.document.tables),
            "pages": len(result.document.pages), "processor": {"name": "docling", "version": "2.144.0", "mode": profile["mode"]},
            "warnings": ["Native mode preserves text cells and DOCX structure; PDF layout/table models are not used."] if profile["mode"] == "native" else []}


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read(16_000_001))
        if type(payload) is not dict or payload.get("op") not in {"embed", "rank", "monty", "docling", "kiwix"}:
            raise ValueError("Invalid fixed knowledge worker operation.")
        setup(payload["profile"], loopback=payload["op"] == "kiwix")
        # Package diagnostics must not corrupt the bounded JSON protocol.
        with contextlib.redirect_stdout(sys.stderr):
            result = globals()[payload["op"]](payload)
        encoded = canonical(result)
        if len(encoded) > MAX_OUTPUT:
            raise ValueError("Knowledge result exceeds output limits.")
    except Exception as error:
        # Error text can contain a program traceback but no inherited credentials.
        encoded = canonical({"ok": False, "error": f"{type(error).__name__}: {str(error)[:1500]}"})
    sys.stdout.buffer.write(encoded)


if __name__ == "__main__":
    main()
