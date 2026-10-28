"""Offline knowledge, document structure, library search and constrained code.

Optional libraries load only in bounded, short-lived workers. A failed or stale
semantic index falls back to the existing lexical search, never to a download.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.metadata
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath

from .config import _allowed_path
from .tools import _bounded_opener, _integer, _object, _schema, _text

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = {"fastembed": "0.9.1", "sqlite-vec": "0.1.9", "pydantic-monty": "1.4.0",
            "pydantic-monty-client": "1.4.0", "pydantic-monty-runtime": "1.4.0"}
NAMES = {"hybrid_search", "knowledge_reindex", "document_structure", "library_search", "library_read", "code_transform"}
MAX_CHUNKS = 1000
MAX_TEXT_BYTES = 2_000_000
MAX_FILE_BYTES = 10_000_000
MAX_OUTPUT = 3_900_000
MEMORY = 4 * 1024**3
CPU = 90
WALL = 120
MODEL = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384
INDEX_BATCH = 64
INDEX_WALL = 900


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def local_file(path, limit=500_000):
    path = _allowed_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("Knowledge assets must be bounded, unlinked regular files.")
    return path


def file_hash(path, limit=1_000_000_000):
    with local_file(path, limit).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_json(value, *, max_bytes=65_536):
    """JSON only; no host instances, callbacks, byte strings or nonfinite values."""
    nodes = 0
    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > 12 or nodes > 10_000:
            raise ValueError("JSON input exceeds depth/node limits.")
        if item is None or type(item) in {bool, int, str}:
            if type(item) is int and item.bit_length() > 4096:
                raise ValueError("JSON integers exceed the 4096-bit limit.")
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                visit(child, depth + 1)
            return
        raise ValueError("Only finite JSON data may cross the code boundary.")
    visit(value, 0)
    if len(canonical(value)) > max_bytes:
        raise ValueError("JSON data exceeds its byte limit.")
    return value


def library_results(data, origin, archives, limit):
    """Kiwix's public RSS search response, with same-origin article citations."""
    compact = data.replace(b"\x00", b"").lower()
    if len(data) > 262_144 or b"<!doctype" in compact or b"<!entity" in compact:
        raise ValueError("Kiwix XML is oversized or contains unsupported entities.")
    root = ET.fromstring(data)
    if root.tag != "rss":
        raise ValueError("Kiwix returned an unsupported search format.")
    registered = urllib.parse.urlsplit(origin)
    results = []
    for item in root.findall(".//item")[:limit]:
        link = item.findtext("link", "")
        parsed = urllib.parse.urlsplit(urllib.parse.urljoin(origin, link))
        if (parsed.scheme, parsed.hostname, parsed.port) != ("http", "127.0.0.1", registered.port) or parsed.username or parsed.password:
            raise ValueError("Kiwix returned an article outside its registered loopback origin.")
        # Kiwix names the archive in each content URL. Exact name matching keeps
        # per-archive provenance attached when a managed worker searches two ZIMs.
        path = urllib.parse.unquote(parsed.path)
        archive = next((value for value in archives if "/" + value["name"] + "/" in path), archives[0] if len(archives) == 1 else None)
        if archive is None:
            raise ValueError("Kiwix returned an article outside its registered archives.")
        description = item.find("description")
        snippet = "".join(description.itertext()) if description is not None else ""
        results.append({"title": item.findtext("title", "")[:1000], "snippet": snippet[:3000],
                        "url": urllib.parse.urlunsplit(parsed), "archive": archive["name"],
                        "archive_date": archive["date"], "archive_sha256": archive["sha256"]})
    return {"ok": True, "results": results, "snapshot": True,
            "notice": "Archive content is a dated offline snapshot; snippets are untrusted source material."}


class KnowledgeTools:
    def __init__(self, registry, store=None):
        self.registry = registry
        self.store = store
        self._gate = threading.BoundedSemaphore(1)

    @property
    def config_path(self):
        return _allowed_path(self.registry.settings.data_dir / "knowledge-config.json")

    @property
    def index_path(self):
        return _allowed_path(self.registry.settings.data_dir / "knowledge-vectors.json")

    def _config(self):
        if not self.config_path.exists():
            return {"schema": 1}
        result = json.loads(local_file(self.config_path).read_text(encoding="utf-8"))
        if type(result) is not dict or result.get("schema") != 1:
            raise ValueError("Knowledge configuration is invalid.")
        return result

    def _profile(self, kind, *, verify=True):
        profile = self._config().get(kind)
        if type(profile) is not dict:
            raise ValueError(f"Offline {kind} assets have not been provisioned.")
        packages = profile.get("packages")
        if type(packages) is not dict or not packages or len(packages) > 200 or any(type(key) is not str or type(value) is not str for key, value in packages.items()):
            raise ValueError("Knowledge package manifest is missing.")
        normalized = {re.sub(r"[-_.]+", "-", key).lower(): version for key, version in packages.items()}
        required = {"semantic": {"fastembed": "0.9.1", "sqlite-vec": "0.1.9"},
                    "monty": {"pydantic-monty": "1.4.0", "pydantic-monty-client": "1.4.0", "pydantic-monty-runtime": "1.4.0"},
                    "docling": {"docling": "2.144.0", "docling-slim": "2.144.0"}}[kind]
        if any(normalized.get(name) != version for name, version in required.items()):
            raise ValueError("Only the pinned, qualified knowledge package profile is supported.")
        target = profile.get("site_packages")
        if target:
            path = _allowed_path(target)
            path.relative_to(ROOT / "runtime" / "knowledge")
            if not path.is_dir():
                raise ValueError("Isolated knowledge packages are missing.")
        else:
            for name, version in packages.items():
                if importlib.metadata.version(name) != version:
                    raise ValueError("Pinned knowledge package versions do not match.")
        assets = profile.get("assets", [])
        if type(assets) is not list or len(assets) > 300:
            raise ValueError("Knowledge asset manifest is invalid.")
        for asset in assets:
            if type(asset) is not dict or set(asset) != {"path", "sha256", "bytes"}:
                raise ValueError("Knowledge asset manifest is invalid.")
            path = _allowed_path(ROOT / "runtime" / "knowledge" / asset["path"])
            path.relative_to(ROOT / "runtime" / "knowledge")
            info = local_file(path, 2_000_000_000).stat()
            if info.st_size != asset["bytes"] or (verify and file_hash(path, 2_000_000_000) != asset["sha256"]):
                raise ValueError("A knowledge asset differs from its recorded hash.")
        if kind == "semantic":
            if profile.get("model") != MODEL or profile.get("dimensions") != DIMENSIONS or not assets:
                raise ValueError("Only the qualified local embedding model is supported.")
            model_path = _allowed_path(profile.get("model_path", ""))
            model_path.relative_to(ROOT / "runtime" / "knowledge")
        if kind == "monty":
            binary = _allowed_path(profile.get("binary", ""))
            binary.relative_to(ROOT)
            if verify and file_hash(binary, 100_000_000) != profile.get("binary_sha256"):
                raise ValueError("Pinned Monty runtime hash differs.")
        return profile

    def status(self):
        result = {"lexical": {"ready": self.store is not None}, "limits": {"semantic_chunks": MAX_CHUNKS,
                  "document_bytes": MAX_FILE_BYTES, "worker_memory_bytes": MEMORY, "cpu_seconds": CPU,
                  "wall_seconds": WALL, "reindex_batch_chunks": INDEX_BATCH,
                  "reindex_total_wall_seconds": INDEX_WALL}, "runtime_downloads": False}
        for kind in ("semantic", "docling", "monty", "kiwix"):
            try:
                if kind == "kiwix":
                    profile = self._kiwix(verify=False)
                else:
                    profile = self._profile(kind, verify=False)
                all_packages = profile.get("packages", {})
                selected = {key: version for key, version in all_packages.items()
                            if re.sub(r"[-_.]+", "-", key).lower() in {"fastembed", "sqlite-vec", "pydantic-monty", "docling"}}
                result[kind] = {"ready": True, "packages": selected, "locked_packages": len(all_packages),
                                "details": "Local assets configured; checked again before execution."}
                if kind == "semantic":
                    result[kind].update({"model": MODEL, "dimensions": DIMENSIONS})
                elif kind == "docling":
                    result[kind]["mode"] = profile.get("mode")
                elif kind == "kiwix":
                    result[kind].update({"mode": profile.get("mode", "registered"), "version": profile.get("version"),
                                        "archives": [{key: item[key] for key in ("name", "date", "sha256")} for item in profile.get("archives", [])]})
            except (ValueError, OSError, KeyError, TypeError, importlib.metadata.PackageNotFoundError):
                result[kind] = {"ready": False, "details": f"Provision the optional offline {kind} profile."}
        result["semantic"]["indexed"] = self.index_path.exists()
        result["ready"] = any(result[kind]["ready"] for kind in ("semantic", "docling", "monty", "kiwix"))
        return result

    def schemas(self):
        query = {"type": "string", "maxLength": 4000}
        schemas = [
            _schema("hybrid_search", "Search imported knowledge snapshots with exact FTS and optional offline semantic reciprocal-rank fusion. Returns source hashes/version/citations; falls back to lexical when assets or index are unavailable.", {"query": query, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"]),
            _schema("knowledge_reindex", "Build/rebuild the bounded offline semantic index of already imported knowledge. Requires approval; never reads new source folders or downloads assets.", {}),
            _schema("document_structure", "Extract document reading order/tables as Markdown with offline Docling from one held workspace snapshot. PDF/DOCX only, 10 MB/50 pages; no remote models or image fetches.", {"path": {"type": "string"}}, ["path"]),
            _schema("library_search", "Search bundled Python/SQLite or an owner-registered loopback Kiwix reference archive. This reads a dated offline snapshot and cannot search the live Internet. Optional archive selects an exact bundled name; omit it to search all.", {"query": query, "archive": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"]),
            _schema("library_read", "Read an article_path returned by library_search from a bundled offline Kiwix archive. Returns text and archive SHA-256/date, with no image/script execution or external fetches.", {"archive": {"type": "string"}, "article_path": {"type": "string", "maxLength": 3000}}, ["archive", "article_path"]),
            _schema("code_transform", "Run a short Python-subset program in Monty with explicit JSON input named data. Requires approval. No host functions, mounts, network, shell or third-party modules. Final expression must return bounded JSON; 32 MB interpreter heap/2 seconds.", {"code": {"type": "string", "maxLength": 20_000}, "data": {}}, ["code", "data"]),
        ]
        status = self.status()
        ready = {"hybrid_search": True, "knowledge_reindex": status["semantic"]["ready"],
                 "document_structure": status["docling"]["ready"], "library_search": status["kiwix"]["ready"],
                 "library_read": status["kiwix"]["ready"],
                 "code_transform": status["monty"]["ready"]}
        return [schema for schema in schemas if ready[schema["function"]["name"]]]

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown knowledge tool.")
        return "approval" if name in {"code_transform", "knowledge_reindex"} else "auto"

    def fingerprint_material(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown knowledge tool.")
        material = {"name": name, "args": args, "config_sha256": file_hash(self.config_path) if self.config_path.exists() else None}
        if name == "code_transform":
            material["profile"] = self._profile("monty")
        elif name == "knowledge_reindex":
            material["profile"] = self._profile("semantic")
            material["corpus_sha256"] = self._corpus()[1]
        return material

    def _corpus(self):
        if self.store is None:
            raise ValueError("Knowledge store is unavailable.")
        with self.store.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT c.id AS chunk_id,c.document_id,c.title,c.content,d.sha256,d.source_name,d.source_path,d.version,d.warnings,d.processor,d.content_sha256 FROM chunks c JOIN documents d ON d.id=c.document_id ORDER BY c.id LIMIT ?", (MAX_CHUNKS + 1,))]
        if len(rows) > MAX_CHUNKS or sum(len(row["content"].encode("utf-8")) for row in rows) > MAX_TEXT_BYTES:
            raise ValueError("Semantic corpus exceeds 1000 chunks/2 MB; narrow imported knowledge before indexing.")
        return rows, hashlib.sha256(canonical(rows)).hexdigest()

    def _lexical(self, query):
        if self.store is None:
            return []
        match = self.store.query(query)
        if not match:
            return []
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT c.id AS chunk_id,c.document_id,c.title,c.content,d.sha256,d.source_name,d.source_path,d.version,d.warnings,d.processor,d.content_sha256 FROM knowledge_fts f JOIN chunks c ON c.id=f.id JOIN documents d ON d.id=c.document_id WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT 20", (match,))]

    def search(self, query, *, limit=6, cancelled=None):
        return self.execute("hybrid_search", {"query": query, "limit": limit}, cancelled=cancelled)

    def _search(self, args, cancelled):
        _object(args, {"query", "limit"}, {"query"})
        query = _text(args["query"], "query", 4000)
        limit = _integer(args.get("limit", 6), "limit", 1, 10)
        lexical = self._lexical(query)
        semantic = []
        warning = None
        try:
            profile = self._profile("semantic")
            rows, digest = self._corpus()
            index = json.loads(local_file(self.index_path, MAX_OUTPUT).read_text(encoding="utf-8"))
            if index.get("schema") != 1 or index.get("corpus_sha256") != digest or index.get("profile_sha256") != hashlib.sha256(canonical(profile)).hexdigest():
                raise ValueError("Semantic index is stale; approved reindex required.")
            vectors = index["vectors"]
            if type(vectors) is not list or len(vectors) != len(rows):
                raise ValueError("Semantic index has invalid dimensions.")
            answer = self._run({"op": "rank", "profile": profile, "query": query, "vectors": vectors}, cancelled)
            if not answer.get("ok"):
                raise ValueError(answer.get("error", "Semantic ranking unavailable."))
            for item in answer["ranked"]:
                number = _integer(item["index"], "semantic index", 0, len(rows) - 1)
                semantic.append(rows[number])
        except (ValueError, OSError, KeyError, TypeError, importlib.metadata.PackageNotFoundError):
            warning = "Semantic search unavailable or stale; returned lexical matches. Approve knowledge_reindex after local model provisioning."
        scores, candidates, channels = {}, {}, {}
        for channel, matches in (("lexical", lexical), ("semantic", semantic)):
            for rank, row in enumerate(matches, 1):
                key = row["chunk_id"]
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
                candidates[key] = row
                channels.setdefault(key, []).append(channel)
        results = []
        for key in sorted(scores, key=lambda key: (-scores[key], key))[:limit]:
            row = dict(candidates[key])
            row["citation"] = f"[doc:{row['document_id']} v{row['version']} chunk:{row['chunk_id']}]"
            row["retrieval"] = channels[key]
            row["rank_score"] = round(scores[key], 8)
            for field in ("warnings", "processor"):
                if type(row.get(field)) is str:
                    try:
                        row[field] = json.loads(row[field])
                    except ValueError:
                        pass
            results.append(row)
        return {"ok": True, "mode": "hybrid" if semantic else "lexical", "results": results, "warning": warning,
                "score_note": "Reciprocal rank fusion scores order results; they are not factual confidence probabilities."}

    def _reindex(self, args, cancelled):
        _object(args, set())
        profile = self._profile("semantic")
        rows, digest = self._corpus()
        if not rows:
            raise ValueError("Import knowledge documents before indexing.")
        vectors = []
        deadline = time.monotonic() + INDEX_WALL
        for offset in range(0, len(rows), INDEX_BATCH):
            if cancelled and cancelled():
                raise ValueError("Knowledge indexing was cancelled; previous index retained.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Knowledge indexing exceeded its total 900-second deadline; previous index retained.")
            batch = rows[offset:offset + INDEX_BATCH]
            result = self._run({"op": "embed", "profile": profile, "texts": [row["content"] for row in batch]}, cancelled,
                               timeout=min(WALL, remaining))
            if not result.get("ok"):
                return result
            if type(result.get("vectors")) is not list or len(result["vectors"]) != len(batch):
                raise ValueError("Embedding worker returned the wrong vector count.")
            vectors.extend(result["vectors"])
        if self._corpus()[1] != digest:
            raise ValueError("Knowledge changed during indexing; index was not committed.")
        index = {"schema": 1, "corpus_sha256": digest, "profile_sha256": hashlib.sha256(canonical(profile)).hexdigest(), "vectors": vectors}
        payload = canonical(index)
        if len(payload) > MAX_OUTPUT:
            raise ValueError("Semantic index exceeds its byte limit.")
        temporary = self.index_path.with_name(self.index_path.name + ".tmp")
        with self.registry._lock, self.registry._directory(self.index_path.parent):
            if temporary.exists():
                raise ValueError("A previous index temporary file needs owner cleanup.")
            try:
                with temporary.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if cancelled and cancelled():
                    raise ValueError("Knowledge indexing was cancelled; previous index retained.")
                if self.index_path.exists():
                    local_file(self.index_path, MAX_OUTPUT)
                os.replace(temporary, self.index_path)
            finally:
                if temporary.exists():
                    local_file(temporary, MAX_OUTPUT).unlink()
        return {"ok": True, "chunks": len(rows), "corpus_sha256": digest, "model": MODEL, "dimensions": DIMENSIONS}

    def _snapshot(self, path):
        value = _text(path, "path", 1000)
        target = self.registry._path(value)
        if target.suffix.lower() not in {".pdf", ".docx"}:
            raise ValueError("Document structure supports PDF or DOCX snapshots only.")
        with self.registry._lock, self.registry._directory(target.parent) as directory:
            with os.fdopen(self.registry._open_read(target, directory), "rb") as stream:
                before = os.fstat(stream.fileno())
                if before.st_size > MAX_FILE_BYTES:
                    raise ValueError("Document exceeds the 10 MB limit.")
                data = stream.read(MAX_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
                if len(data) > MAX_FILE_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("Document changed during snapshot reading or exceeds limits.")
        return {"name": target.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "data": base64.b64encode(data).decode("ascii")}

    def _kiwix(self, verify=True):
        profile = self._config().get("kiwix")
        if type(profile) is dict and profile.get("mode") == "managed":
            if profile.get("version") != "3.8.1" or type(profile.get("assets")) is not list or len(profile["assets"]) > 1000 or not 1 <= len(profile.get("archives", [])) <= 5:
                raise ValueError("Managed Kiwix manifest is invalid.")
            binary = _allowed_path(profile["binary"])
            binary.relative_to(ROOT / "runtime/knowledge/kiwix")
            expected = set()
            for item in profile["assets"]:
                path = _allowed_path(ROOT / "runtime/knowledge" / item["path"])
                path.relative_to(ROOT / "runtime/knowledge")
                if local_file(path, 2_000_000_000).stat().st_size != item["bytes"] or verify and file_hash(path, 2_000_000_000) != item["sha256"]:
                    raise ValueError("A Kiwix asset differs from its recorded SHA-256.")
                expected.add(str(path))
            if str(binary) not in expected:
                raise ValueError("Kiwix binary is absent from its asset manifest.")
            for item in profile["archives"]:
                path = _allowed_path(item["path"])
                path.relative_to(ROOT / "runtime/knowledge/library")
                if str(path) not in expected or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", item["name"]) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["date"]):
                    raise ValueError("Kiwix archive provenance is invalid.")
            return profile
        if type(profile) is not dict or set(profile) != {"url", "archive", "date", "sha256"}:
            raise ValueError("An owner-registered Kiwix archive is required.")
        parsed = urllib.parse.urlsplit(profile["url"])
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Kiwix must be registered as a fixed IPv4 loopback HTTP origin.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", profile["archive"]) or not re.fullmatch(r"[a-f0-9]{64}", profile["sha256"]) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", profile["date"]):
            raise ValueError("Kiwix archive provenance is invalid.")
        return profile

    def _library(self, args, cancelled=None):
        _object(args, {"query", "limit", "archive"}, {"query"})
        query = _text(args["query"], "query", 4000)
        limit = _integer(args.get("limit", 6), "limit", 1, 10)
        profile = self._kiwix()
        if profile.get("mode") == "managed":
            archive = args.get("archive")
            if archive is not None and archive not in {item["name"] for item in profile["archives"]}:
                raise ValueError("Select an exact registered archive name.")
            return self._run({"op": "kiwix", "profile": profile, "action": "search", "query": query, "limit": limit, "archive": archive}, cancelled)
        url = profile["url"].rstrip("/") + "/search?" + urllib.parse.urlencode({"content": profile["archive"], "pattern": query, "format": "xml", "pageLength": limit})
        with _bounded_opener(10) as (opener, expired):
            with opener.open(urllib.request.Request(url, headers={"Accept": "application/xml"}), timeout=10) as response:
                data = response.read(262_145)
                if response.status != 200 or len(data) > 262_144:
                    raise ValueError("Kiwix response exceeds limits or failed.")
        return library_results(data, profile["url"], [{"name": profile["archive"], "date": profile["date"], "sha256": profile["sha256"]}], limit)

    def execute(self, name, args, *, cancelled=None):
        try:
            if name not in NAMES:
                raise ValueError("Unknown knowledge tool.")
            if cancelled and cancelled():
                raise ValueError("Knowledge operation cancelled.")
            if not self._gate.acquire(blocking=False):
                raise ValueError("A knowledge worker is busy; retry after completion.")
            try:
                if name == "hybrid_search":
                    return self._search(args, cancelled)
                if name == "knowledge_reindex":
                    return self._reindex(args, cancelled)
                if name == "library_search":
                    return self._library(args, cancelled)
                if name == "library_read":
                    _object(args, {"archive", "article_path"}, {"archive", "article_path"})
                    profile = self._kiwix()
                    if profile.get("mode") != "managed":
                        raise ValueError("Article reading requires bundled managed archives.")
                    archive = _text(args["archive"], "archive", 200)
                    path = _text(args["article_path"], "article_path", 3000)
                    decoded = urllib.parse.unquote(urllib.parse.unquote(path))
                    if archive not in {item["name"] for item in profile["archives"]} or not decoded.startswith("/content/" + archive + "/") or any(part == ".." for part in decoded.split("/")) or any(ord(c) < 32 for c in decoded) or "\\" in decoded or any(c in path for c in "?#\\"):
                        raise ValueError("Article path must remain inside a registered archive.")
                    return self._run({"op": "kiwix", "profile": profile, "action": "read", "archive": archive, "article_path": path}, cancelled)
                if name == "document_structure":
                    _object(args, {"path"}, {"path"})
                    snapshot = self._snapshot(args["path"])
                    result = self._run({"op": "docling", "profile": self._profile("docling"), "snapshot": snapshot}, cancelled)
                    result["source"] = {key: value for key, value in snapshot.items() if key != "data"}
                    return result
                _object(args, {"code", "data"}, {"code", "data"})
                code = _text(args["code"], "code", 20_000)
                data = validate_json(args["data"])
                return self._run({"op": "monty", "profile": self._profile("monty"), "code": code, "data": data}, cancelled)
            finally:
                self._gate.release()
        except (ValueError, OSError, KeyError, TypeError, RecursionError, importlib.metadata.PackageNotFoundError) as error:
            return {"ok": False, "error": str(error)[:2000]}
        except Exception:
            return {"ok": False, "error": "The bounded offline knowledge operation could not complete."}

    def _run(self, payload, cancelled, *, timeout=WALL):
        from .worker import run_worker
        result = run_worker(Path(__file__).with_name("_knowledge_worker.py"), canonical(payload), timeout=timeout,
                            memory_bytes=MEMORY, cpu_seconds=CPU, max_output_bytes=MAX_OUTPUT,
                            max_input_bytes=16_000_000, process_limit=2 if payload["op"] in {"monty", "kiwix"} else 1,
                            cancelled=cancelled)
        answer = json.loads(result)
        if type(answer) is not dict or type(answer.get("ok")) is not bool:
            raise ValueError("Knowledge worker returned an invalid result.")
        return answer
