"""Operator-selected references: read outside the box, write only local knowledge."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path

from .config import _allowed_path
from .tools import ToolRegistry, _linked


MAX_BYTES = 10_000_000
EXTENSIONS = {".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sql", ".xml", ".pdf", ".docx", ".xlsx"}
EXCLUDED_DIRS = {"node_modules", "data", "runtime", "venv", "env", "__pycache__", "windows", "programdata", "appdata", "system volume information", "$recycle.bin"}
EXTENSIONS.update({".png", ".jpg", ".jpeg", ".epub"})
SENSITIVE = re.compile(r"(?:password|passwd|credential|secret|private[_. -]?key|access[_. -]?token|refresh[_. -]?token|server[_. -]?token|auth[_. -]?token|api[_. -]?key|cookie)", re.I)
SENSITIVE_COMMON = re.compile(r"(?:^|[_. -])(?:tokens?|auth|id_rsa|id_ed25519|id_ecdsa|id_dsa)(?:[_. -]|$)", re.I)


def permitted_name(name, directory=False):
    if not name or name.startswith(".") or SENSITIVE.search(name) or SENSITIVE_COMMON.search(name):
        return False
    if directory:
        return name.lower() not in EXCLUDED_DIRS
    return Path(name).suffix.lower() in EXTENSIONS


class ReadOnlyFiles(ToolRegistry):
    """Reuse handle-based confinement without creating a source directory."""
    def __init__(self, settings, root):
        self.settings = settings
        self.root = _allowed_path(root)
        if self.root == Path(self.root.anchor) or self.root.is_relative_to(settings.data_dir) or settings.data_dir.is_relative_to(self.root):
            raise ValueError("Choose a reference folder that neither contains nor is inside private Ariadne data")
        if any(not permitted_name(part, directory=True) for part in self.root.parts[1:]):
            raise ValueError("Excluded folders cannot be reference roots")
        self._check_ancestors(self.root)
        if not self.root.is_dir():
            raise ValueError("The source folder does not exist or cannot be read")
        if getattr(self.root.stat(), "st_file_attributes", 0) & 0x2:
            raise ValueError("Hidden folders cannot be reference roots")
        self._lock, self._connectors = threading.RLock(), {}

    def source_path(self, relative, directory=False):
        parts = self._parts(relative, root_ok=directory)
        for i, part in enumerate(parts):
            if not permitted_name(part, directory=directory or i < len(parts) - 1):
                raise ValueError("This path is excluded from reference imports")
        path = self._path(relative, root_ok=directory)
        for component in (self.root, *[self.root.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]):
            if getattr(component.lstat(), "st_file_attributes", 0) & 0x2:
                raise ValueError("Hidden files and folders cannot be imported")
        return path

    def read_bytes(self, relative):
        path = self.source_path(relative)
        with self._directory(path.parent) as directory_fd:
            with os.fdopen(self._open_read(path, directory_fd), "rb") as source:
                if getattr(os.fstat(source.fileno()), "st_file_attributes", 0) & 0x2:
                    raise ValueError("Hidden files cannot be imported")
                if os.fstat(source.fileno()).st_size > MAX_BYTES:
                    raise ValueError("Reference files are limited to 10 MB")
                data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Reference files are limited to 10 MB")
        return data

    def identity(self, relative):
        path = self.source_path(relative)
        return os.path.normcase(str(path.relative_to(self.root))).replace("\\", "/")

    def list_files(self, relative="."):
        path = self.source_path(relative, directory=True)
        entries, truncated = [], False
        prefix = "" if relative in (".", "./") else relative.replace("\\", "/").rstrip("/") + "/"
        with self._directory(path) as directory_fd:
            with os.scandir(path if directory_fd is None else directory_fd) as iterator:
                scanned = 0
                for entry in iterator:
                    scanned += 1
                    if scanned > 5000:
                        truncated = True
                        break
                    info = entry.stat(follow_symlinks=False)
                    is_dir = stat.S_ISDIR(info.st_mode)
                    if _linked(info) or getattr(info, "st_file_attributes", 0) & 0x2 or not (is_dir or stat.S_ISREG(info.st_mode)) or info.st_nlink > 1 and not is_dir:
                        continue
                    if not permitted_name(entry.name, directory=is_dir):
                        continue
                    if len(entries) >= 500:
                        truncated = True
                        break
                    entries.append({"name": entry.name, "path": prefix + entry.name, "type": "directory" if is_dir else "file", "size": info.st_size})
        return {"files": sorted(entries, key=lambda e: (e["type"] != "directory", e["name"].casefold())), "truncated": truncated}


class SourceManager:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    @staticmethod
    def policy():
        return {"read_only": True, "input_bytes": MAX_BYTES, "output_chars": 500000,
                "extensions": sorted(EXTENSIONS), "excluded": "Hidden folders/files, credential-like names, runtime/data folders, links and E: are excluded. Imports are reference data, never executable skills or authority."}

    def register(self, name, path):
        root = _allowed_path(path)
        if root == Path(root.anchor) or root.is_relative_to(self.settings.data_dir) or self.settings.data_dir.is_relative_to(root):
            raise ValueError("Choose a reference folder that neither contains nor is inside private Ariadne data")
        if any(not permitted_name(part, directory=True) for part in root.parts[1:]):
            raise ValueError("Private/system folders cannot be reference roots")
        ReadOnlyFiles(self.settings, root)
        return self.store.source_add(name, os.path.normcase(str(root)))

    def reader(self, source_id):
        source = self.store.source(source_id)
        if not source:
            raise KeyError("Source not found")
        return source, ReadOnlyFiles(self.settings, source["path"])

    def listing(self, source_id, directory="."):
        _, reader = self.reader(source_id)
        return reader.list_files(directory)

    @staticmethod
    def review_identity(extracted):
        material = {key: extracted.get(key) for key in ("text", "format", "sha256", "processor", "warnings")}
        return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def preview(self, source_id, relative, *, ocr=False):
        from .documents import extract_document
        source, reader = self.reader(source_id)
        data = reader.read_bytes(relative)
        extracted = extract_document(Path(relative).name, data, ocr_config=self.settings.data_dir / "ocr-config.json", ocr=ocr)
        return {**extracted, "review_identity": self.review_identity(extracted), "ocr": ocr,
                "source_id": source_id, "source_name": source["name"], "path": reader.identity(relative), "title": (source["name"] + " / " + relative)[:200]}

    def import_reference(self, source_id, relative, digest, *, ocr=False, review_identity=None):
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("A preview SHA-256 is required")
        from .documents import extract_document
        source, reader = self.reader(source_id)
        data = reader.read_bytes(relative)
        if hashlib.sha256(data).hexdigest() != digest:
            raise SourceChanged("The source changed after preview. Review a fresh preview before importing.")
        extracted = extract_document(Path(relative).name, data, ocr_config=self.settings.data_dir / "ocr-config.json", ocr=ocr)
        if (review_identity is not None or extracted.get("processor", {}).get("name") == "rapidocr") and review_identity != self.review_identity(extracted):
            raise SourceChanged("The extraction changed after preview. Review a fresh preview before importing.")
        return self.store.document_upsert((source["name"] + " / " + relative)[:200], extracted["text"], {"source_id": source_id, "source_name": source["name"],
            "source_path": reader.identity(relative), "sha256": digest, "format": extracted["format"], "byte_size": extracted["bytes"], "warnings": extracted.get("warnings", []), "processor": extracted.get("processor")})

    def upload(self, name, content_base64, *, ocr=False):
        from .documents import extract_document
        if not isinstance(name, str) or len(name) > 200 or "/" in name or "\\" in name or not permitted_name(name):
            raise ValueError("Use a supported non-sensitive document filename without a folder path")
        if not isinstance(content_base64, str) or len(content_base64) > 13_333_340:
            raise ValueError("Document uploads are limited to 10 MB")
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid base64 upload") from None
        extracted = extract_document(name, data, ocr_config=self.settings.data_dir / "ocr-config.json", ocr=ocr)
        result = self.store.document_upsert(name, extracted["text"], {"source_id": "upload", "source_name": "Uploaded files", "source_path": os.path.normcase(name),
            "sha256": extracted["sha256"], "format": extracted["format"], "byte_size": extracted["bytes"], "warnings": extracted.get("warnings", []), "processor": extracted.get("processor")})
        result["warnings"] = extracted.get("warnings", [])
        return result


class SourceChanged(ValueError):
    pass
