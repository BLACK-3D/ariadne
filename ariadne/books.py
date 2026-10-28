"""Private owner-book library, complete bounded text coverage and section citations."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .book_worker import MAX_INPUT, MAX_RESPONSE, MAX_SECTIONS, MAX_SECTION, MAX_TEXT
from .config import _allowed_path
from .sources import SourceChanged


class BookLibrary:
    def __init__(self, registry, sources):
        self.registry, self.sources = registry, sources
        self.path = registry.settings.data_dir / "books.sqlite3"
        self.lock = threading.RLock()
        self.import_gate = threading.BoundedSemaphore(1)

    @contextmanager
    def database(self):
        path = _allowed_path(self.path)
        with self.lock, self.registry._directory(path.parent, create=True):
            for suffix in ("", "-journal", "-wal", "-shm"):
                candidate = _allowed_path(str(path) + suffix)
                if candidate.exists():
                    info = candidate.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Book database and sidecars must be regular files with one link.")
            db = sqlite3.connect(path, timeout=10)
            db.row_factory = sqlite3.Row
            deadline = time.monotonic() + 10
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            try:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA trusted_schema=OFF")
                db.executescript("""
                CREATE TABLE IF NOT EXISTS books(id TEXT PRIMARY KEY,title TEXT NOT NULL,sha256 TEXT NOT NULL UNIQUE,
                  format TEXT NOT NULL,byte_size INTEGER NOT NULL,text_chars INTEGER NOT NULL,total_sections INTEGER NOT NULL,text_sections INTEGER NOT NULL,
                  empty_sections TEXT NOT NULL,coverage_complete INTEGER NOT NULL,created REAL NOT NULL,warnings TEXT NOT NULL,
                  source_name TEXT NOT NULL,source_path TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sections(book_id TEXT REFERENCES books(id) ON DELETE CASCADE,
                  number INTEGER NOT NULL,label TEXT NOT NULL,text TEXT NOT NULL,PRIMARY KEY(book_id,number));
                CREATE VIRTUAL TABLE IF NOT EXISTS book_fts USING fts5(book_id UNINDEXED,number UNINDEXED,label,text);
                """)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def metadata(row):
        result = dict(row)
        result["empty_sections"] = json.loads(result["empty_sections"])
        result["warnings"] = json.loads(result["warnings"])
        result["coverage_complete"] = bool(result["coverage_complete"])
        result.update(processed_all_sections=True, image_coverage=False, scope="private owner reference text",
                      coverage_metric="sections_with_any_extractable_text",
                      coverage_notice="Text presence per section is measured; complete content or illustration coverage is not certified.")
        return result

    def _read(self, source_id, relative):
        source, reader = self.sources.reader(source_id)
        path = reader.source_path(relative)
        kind = path.suffix.lower().lstrip(".")
        if kind not in {"pdf", "epub"}:
            raise ValueError("Choose a PDF or EPUB from a registered reference folder.")
        with reader._directory(path.parent) as directory_fd:
            with os.fdopen(reader._open_read(path, directory_fd), "rb") as stream:
                info = os.fstat(stream.fileno())
                if info.st_size > MAX_INPUT or getattr(info, "st_file_attributes", 0) & 0x2:
                    raise ValueError("Books must be visible regular files of at most 128 MiB.")
                data = stream.read(MAX_INPUT + 1)
                after = os.fstat(stream.fileno())
        if len(data) > MAX_INPUT or len(data) != after.st_size or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SourceChanged("The book changed while being read; review it again.")
        return source, reader.identity(relative), kind, data

    def preview(self, source_id, relative):
        _, identity, kind, data = self._read(source_id, relative)
        return {"name": Path(identity).name, "path": identity, "format": kind, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "notice": "Imports your reviewed local copy as private reference data. No DRM bypass. Text coverage and unreadable sections are reported; images are not interpreted."}

    @staticmethod
    def extract(data, kind):
        process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).with_name("book_worker.py")), kind],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output, overflow = bytearray(), threading.Event()
        def feed():
            try:
                process.stdin.write(data)
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()
        def collect():
            while True:
                chunk = process.stdout.read1(65536)
                if not chunk:
                    return
                left = MAX_RESPONSE - len(output)
                output.extend(chunk[:max(0, left)])
                if len(chunk) > left:
                    overflow.set()
                    process.kill()
                    return
        writer = threading.Thread(target=feed, daemon=True)
        reader = threading.Thread(target=collect, daemon=True)
        writer.start(); reader.start()
        try:
            try:
                process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
                raise ValueError("Book extraction exceeded 120 seconds; no partial import was saved.") from None
            writer.join(timeout=2); reader.join(timeout=2)
            if overflow.is_set() or reader.is_alive() or writer.is_alive() or process.returncode:
                raise ValueError("Book extraction failed within its resource bounds.")
            result = json.loads(output)
            if result.get("error"):
                raise ValueError(result["error"])
            sections = result.get("sections")
            if (type(sections) is not list or not 1 <= len(sections) <= MAX_SECTIONS or
                    sum(len(s.get("text", "")) for s in sections) > MAX_TEXT):
                raise ValueError("Invalid book reader response.")
            for index, section in enumerate(sections, 1):
                if (section.get("number") != index or type(section.get("text")) is not str or len(section["text"]) > MAX_SECTION or
                        type(section.get("label")) is not str or len(section["label"]) > 500):
                    raise ValueError("Invalid book section response.")
            return result
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
            process.stdout.close()

    def import_book(self, source_id, relative, digest, title):
        if not self.import_gate.acquire(blocking=False):
            raise ValueError("Another book import is in progress; retry after it finishes.")
        try:
            return self._import_book(source_id, relative, digest, title)
        finally:
            self.import_gate.release()

    def _import_book(self, source_id, relative, digest, title):
        if type(digest) is not str or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Review the file before importing; a SHA-256 is required.")
        if type(title) is not str or not title.strip() or len(title) > 200 or any(ord(c) < 32 for c in title):
            raise ValueError("Book title must be 1–200 printable characters.")
        source, identity, kind, data = self._read(source_id, relative)
        if hashlib.sha256(data).hexdigest() != digest:
            raise SourceChanged("The book changed after review; review it again.")
        with self.database() as db:
            existing = db.execute("SELECT * FROM books WHERE sha256=?", (digest,)).fetchone()
            if existing:
                return {"ok": True, "unchanged": True, "book": self.metadata(existing)}
            if db.execute("SELECT count(*) FROM books").fetchone()[0] >= 100:
                raise ValueError("Book library is limited to 100 books.")
        extracted = self.extract(data, kind)
        # Commit all sections together. A failed reader or interruption cannot publish partial coverage.
        sections = extracted["sections"]
        text_chars = sum(len(s["text"]) for s in sections)
        empty = [s["number"] for s in sections if not any(c.isalnum() for c in s["text"])]
        book_id = digest[:32]
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM books WHERE sha256=?", (digest,)).fetchone()
            if existing:
                return {"ok": True, "unchanged": True, "book": self.metadata(existing)}
            if db.execute("SELECT count(*) FROM books").fetchone()[0] >= 100:
                raise ValueError("Book library is limited to 100 books.")
            if db.execute("SELECT COALESCE(sum(text_chars),0) FROM books").fetchone()[0] + text_chars > 100_000_000:
                raise ValueError("Private book library exceeds its 100-million-character text budget.")
            db.execute("INSERT INTO books VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (book_id, title.strip(), digest, kind,
                len(data), text_chars, len(sections), len(sections)-len(empty), json.dumps(empty), int(not empty), time.time(),
                json.dumps(extracted["warnings"]), source["name"], identity))
            db.executemany("INSERT INTO sections VALUES(?,?,?,?)", [(book_id,s["number"],s["label"],s["text"]) for s in sections])
            db.executemany("INSERT INTO book_fts VALUES(?,?,?,?)", [(book_id,s["number"],s["label"],s["text"]) for s in sections])
            book = self.metadata(db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
        return {"ok": True, "unchanged": False, "book": book}

    def books(self):
        with self.database() as db:
            return [self.metadata(row) for row in db.execute("SELECT * FROM books ORDER BY created DESC LIMIT 100")]

    def read(self, book_id, number=1):
        if type(book_id) is not str or not re.fullmatch(r"[a-f0-9]{32}", book_id) or type(number) is not int or not 1 <= number <= MAX_SECTIONS:
            raise ValueError("Invalid book ID or section number.")
        with self.database() as db:
            book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
            section = db.execute("SELECT number,label,text FROM sections WHERE book_id=? AND number=?", (book_id,number)).fetchone()
            if not book or not section:
                raise KeyError("Book section not found")
            return {"book": self.metadata(book), "section": dict(section)}

    def search(self, query, limit=6):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Book search limit must be an integer from 1 to 20.")
        if type(query) is not str or len(query) > 500:
            raise ValueError("Book search must be at most 500 characters.")
        words = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:24]
        if not words:
            return []
        match = " OR ".join('"'+w+'"' for w in words)
        with self.database() as db:
            rows = db.execute("SELECT b.id book_id,b.title,b.sha256,f.number section,f.label,snippet(book_fts,3,'[',']',' … ',40) snippet "
                "FROM book_fts f JOIN books b ON b.id=f.book_id WHERE book_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit)).fetchall()
            return [{**dict(row), "untrusted_reference": True} for row in rows]
