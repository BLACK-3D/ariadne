"""Real private-book readers, guarded owner APIs and reference provenance.

Fixtures and worker scratch data stay in Yikes. Tests invoke the fixed actual
PDF/EPUB subprocess reader; no persistent services, downloads or DRM bypass.
"""
import hashlib
import io
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from ariadne.books import BookLibrary
from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.sources import SourceChanged, SourceManager
from ariadne.store import Store
from ariadne.survival_library import SurvivalLibrary
from ariadne.tools import ToolRegistry


def pdf_bytes(pages=1, *, blank_pages=(), encrypted=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    for number in range(1, pages + 1):
        page = writer.add_blank_page(width=612, height=792)
        if number in blank_pages:
            continue
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 40 740 Td (Readiness page {number} EVIDENCEPAGE{number}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("fixture-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def epub_bytes(*, href="first.xhtml", encryption=False, extra_members=(), large_text=False):
    # Manifest order is deliberately different from the spine. Endnotes are
    # non-spine text and must be retained after the ordered chapters.
    parts = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "OPS/package.opf": ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>'
                            '<item id="second" href="second.xhtml" media-type="application/xhtml+xml"/>'
                            f'<item id="first" href="{href}" media-type="application/xhtml+xml"/>'
                            '<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>'
                            '</manifest><spine><itemref idref="first"/><itemref idref="second"/></spine></package>').encode(),
        "OPS/first.xhtml": ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ignored head</title></head><body><h1>FIRSTCHAPTER</h1><p>' + ("x" * 100001 if large_text else "Local water reference evidence.") + '</p><script>DO_NOT_EXECUTE_OR_INDEX_THIS_SCRIPT</script></body></html>').encode(),
        "OPS/second.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>SECONDCHAPTER</h1><p>Power reference evidence.</p></body></html>',
        "OPS/notes.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>ENDNOTES</h1><p>Endnotes retained without a spine entry.</p></body></html>',
    }
    if encryption:
        parts["META-INF/encryption.xml"] = b"<encryption/>"
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in parts.items():
                archive.writestr(name, content)
            for name, content in extra_members:
                archive.writestr(name, content)
    return output.getvalue()


class NoNetworkModel:
    def status(self):
        return {"available": False, "models": []}


class BookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="book-review-test-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root / "private-data", api_token="private-book-test-token")
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.sources = SourceManager(self.settings, self.store)
        self.reference = self.root / "references"
        self.reference.mkdir()
        self.source = self.sources.register("Owner lawful references", str(self.reference))
        self.books = BookLibrary(self.registry, self.sources)

    def save(self, name, data):
        (self.reference / name).write_bytes(data)
        return self.books.preview(self.source["id"], name)

    def import_file(self, name, data):
        preview = self.save(name, data)
        return self.books.import_book(self.source["id"], name, preview["sha256"], "Owner fixture book")

    def test_real_151_page_pdf_has_last_page_search_and_durable_exact_citation(self):
        data = pdf_bytes(151)
        imported = self.import_file("whole-book.pdf", data)
        book = imported["book"]
        self.assertEqual(book["total_sections"], 151)
        self.assertEqual(book["text_sections"], 151)
        self.assertTrue(book["processed_all_sections"])
        self.assertTrue(book["coverage_complete"])
        self.assertFalse(book["image_coverage"])
        self.assertEqual(book["sha256"], hashlib.sha256(data).hexdigest())
        found = self.books.search("EVIDENCEPAGE151")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["section"], 151)
        self.assertEqual(found[0]["book_id"], book["id"])
        self.assertEqual(found[0]["sha256"], book["sha256"])
        self.assertTrue(found[0]["untrusted_reference"])
        reopened = BookLibrary(self.registry, self.sources)
        last = reopened.read(book["id"], 151)
        self.assertEqual(last["section"]["label"], "Page 151")
        self.assertIn("EVIDENCEPAGE151", last["section"]["text"])
        preview = self.books.preview(self.source["id"], "whole-book.pdf")
        replay = reopened.import_book(self.source["id"], "whole-book.pdf", preview["sha256"], "Other title")
        self.assertTrue(replay["unchanged"])
        self.assertEqual(len(reopened.books()), 1)

    def test_real_epub_source_listing_spine_order_and_nonspine_endnotes(self):
        data = epub_bytes()
        result = self.import_file("whole-book.epub", data)
        self.assertIn("whole-book.epub", [entry["name"] for entry in self.sources.listing(self.source["id"])["files"]])
        book = result["book"]
        self.assertEqual(book["total_sections"], 3)
        texts = [self.books.read(book["id"], number)["section"]["text"] for number in (1, 2, 3)]
        self.assertIn("FIRSTCHAPTER", texts[0])
        self.assertIn("SECONDCHAPTER", texts[1])
        self.assertIn("ENDNOTES", texts[2])
        self.assertNotIn("DO_NOT_EXECUTE", "".join(texts))
        self.assertNotIn("Ignored head", "".join(texts))
        self.assertEqual(self.books.search("ENDNOTES")[0]["section"], 3)

    def test_real_blank_pdf_section_is_reported_incomplete(self):
        imported = self.import_file("mixed.pdf", pdf_bytes(3, blank_pages={2}))
        book = imported["book"]
        self.assertEqual(book["total_sections"], 3)
        self.assertEqual(book["text_sections"], 2)
        self.assertEqual(book["empty_sections"], [2])
        self.assertFalse(book["coverage_complete"])
        self.assertTrue(book["processed_all_sections"])
        self.assertTrue(any("no extractable text" in warning for warning in book["warnings"]))

    def test_actual_encrypted_and_malformed_inputs_leave_no_partial_books(self):
        fixtures = [
            ("encrypted.pdf", pdf_bytes(encrypted=True), "Encrypted"),
            ("encrypted.epub", epub_bytes(encryption=True), "Encrypted"),
            ("escape.epub", epub_bytes(extra_members=[("../escape.xhtml", b"<html/>")]), "unsafe"),
            ("duplicate.epub", epub_bytes(extra_members=[("OPS/first.xhtml", b"<html/>")]), "duplicate"),
            ("external.epub", epub_bytes(href="https://example.invalid/chapter.xhtml"), "local"),
            ("oversized.epub", epub_bytes(large_text=True), "bounded"),
            ("malformed.pdf", b"%PDF-1.7\nthis is not a valid book", "malformed"),
        ]
        for name, data, message in fixtures:
            with self.subTest(name=name):
                preview = self.save(name, data)
                with self.assertRaisesRegex(ValueError, message):
                    self.books.import_book(self.source["id"], name, preview["sha256"], "Rejected fixture")
                self.assertEqual(self.books.books(), [])

    def test_source_hash_change_rejected_and_reference_folder_stays_read_only(self):
        data = pdf_bytes()
        preview = self.save("reviewed.pdf", data)
        path = self.reference / "reviewed.pdf"
        changed = data + b"\n% fixture changed after preview\n"
        path.write_bytes(changed)
        with self.assertRaises(SourceChanged):
            self.books.import_book(self.source["id"], "reviewed.pdf", preview["sha256"], "Stale preview")
        self.assertEqual(self.books.books(), [])
        latest = self.books.preview(self.source["id"], "reviewed.pdf")
        imported = self.books.import_book(self.source["id"], "reviewed.pdf", latest["sha256"], "Fresh preview")
        self.assertTrue(imported["ok"])
        self.assertEqual(path.read_bytes(), changed)
        self.assertEqual(list(self.reference.iterdir()), [path])
        for relative in ("../outside.pdf", "E:/never.pdf", "https://example.invalid/book.pdf", ".hidden.pdf"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.books.preview(self.source["id"], relative)

    def test_search_limits_and_section_types_are_strict(self):
        for limit in (-1, 0, 21, True, "6", 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.books.search("readiness", limit=limit)
        for number in (True, "1", -1, 2001):
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.books.read("a" * 32, number)

    def request(self, path, method="GET", data=None, *, token="private-book-test-token", origin=None, query=""):
        if not hasattr(self, "app"):
            self.app = Application(self.settings, self.store, Engine(self.settings, self.store, NoNetworkModel()))
        payload = json.dumps(data or {}).encode()
        env = {"HTTP_HOST": "localhost:8080", "REQUEST_METHOD": method, "PATH_INFO": path,
               "QUERY_STRING": query, "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)), "wsgi.input": io.BytesIO(payload)}
        if token is not None:
            env["HTTP_AUTHORIZATION"] = "Bearer " + token
        if origin:
            env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            response["status"] = int(status.split()[0])
            response["headers"] = dict(headers)
        body = b"".join(self.app(env, start))
        response["data"] = json.loads(body) if response["headers"]["Content-Type"].startswith("application/json") else body
        return response

    def test_owner_api_auth_origin_revision_and_real_epub_import_read(self):
        for path in ("/api/survival", "/api/survival/library", "/api/survival/books"):
            self.assertEqual(self.request(path, token=None)["status"], 401)
            self.assertEqual(self.request(path, token="bad")["status"], 401)
            self.assertEqual(self.request(path, origin="https://example.invalid")["status"], 403)
        current = self.request("/api/survival")
        self.assertEqual(current["status"], 200)
        self.assertEqual(current["data"]["revision"], 0)
        update = {"action": "save_profile", "record": {"people": 2}, "request_id": "owner-update", "expected_revision": 0}
        self.assertEqual(self.request("/api/survival", "POST", update)["status"], 200)
        stale = self.request("/api/survival", "POST", {**update, "record": {"people": 3}, "request_id": "stale-update"})
        self.assertEqual(stale["status"], 409)
        self.assertEqual(self.request("/api/survival")["data"]["profile"]["people"], 2)
        self.save("api-book.epub", epub_bytes())
        preview_args = {"source_id": self.source["id"], "path": "api-book.epub"}
        preview = self.request("/api/survival/books/preview", "POST", preview_args)
        self.assertEqual(preview["status"], 200)
        wrong = self.request("/api/survival/books", "POST", {**preview_args, "title": "API book", "sha256": "0" * 64})
        self.assertEqual(wrong["status"], 409)
        imported = self.request("/api/survival/books", "POST", {**preview_args, "title": "API book", "sha256": preview["data"]["sha256"]})
        self.assertEqual(imported["status"], 201)
        identifier = imported["data"]["book"]["id"]
        read = self.request("/api/survival/books/" + identifier, query="section=3")
        self.assertEqual(read["status"], 200)
        self.assertIn("ENDNOTES", read["data"]["section"]["text"])
        self.assertEqual(self.request("/api/survival/books/" + identifier, query="section=999")["status"], 404)
        self.assertEqual(self.request("/api/survival/books/" + identifier, query="section=0")["status"], 400)

    def fixture_assets(self):
        assets = self.root / "bundled-survival"
        assets.mkdir()
        source = b"# Fixture source\nStable original text evidence.\n"
        (assets / "source.md").write_bytes(source)
        digest = hashlib.sha256(source).hexdigest()
        catalog = {"schema": 1, "book": {"title": "Owner import"}, "topics": [{"id": "water", "title": "Water"}],
                   "resources": [{"id": "fixture", "title": "Fixture source", "topic": "water", "summary": "Stable evidence", "checklist": [],
                                  "path": "source.md", "bytes": len(source), "sha256": digest, "sources": [{"url": "https://example.invalid/source", "title": "Original fixture"}]}]}
        index = {"schema": 1, "resources": {"fixture": {"source_sha256": digest, "sections": [{"number": 1, "label": "Section one", "text": source.decode()}]}}}
        catalog_data = json.dumps(catalog).encode()
        index_data = json.dumps(index).encode()
        (assets / "catalog.json").write_bytes(catalog_data)
        (assets / "text-index.json").write_bytes(index_data)
        manifest = {"schema": 1, "bytes": len(index_data), "sha256": hashlib.sha256(index_data).hexdigest(),
                    "catalog_sha256": hashlib.sha256(catalog_data).hexdigest()}
        (assets / "text-index-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return assets, source

    def test_reference_source_hash_is_verified_for_read_search_and_download(self):
        assets, original = self.fixture_assets()
        with mock.patch("ariadne.survival_library.ASSETS", assets):
            references = SurvivalLibrary(self.registry)
            self.assertEqual(references.download("fixture")[0], original)
            self.assertIn("Stable", references.read_reference("fixture")["section"]["text"])
            self.assertEqual(references.search_text("Stable")[0]["section"], 1)
            (assets / "source.md").write_bytes(original.replace(b"Stable", b"CHANGED"))
            for operation in (lambda: references.download("fixture"), lambda: references.read_reference("fixture"), lambda: references.search_text("Stable")):
                with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "integrity|match|hash|changed"):
                    operation()

    def test_reference_paths_cannot_escape_asset_root_and_download_api_is_private(self):
        assets, original = self.fixture_assets()
        with mock.patch("ariadne.survival_library.ASSETS", assets):
            for relative in ("../outside.md", "E:/never.md", "/outside.md", "folder\\source.md"):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    SurvivalLibrary._file(relative)
            self.assertEqual(self.request("/api/survival/library/fixture/download", token=None)["status"], 401)
            downloaded = self.request("/api/survival/library/fixture/download")
            self.assertEqual(downloaded["status"], 200)
            self.assertEqual(downloaded["data"], original)
            self.assertIn("attachment", downloaded["headers"]["Content-Disposition"])

    def test_text_index_and_catalog_integrity_prevent_wrong_source_citations(self):
        assets, _ = self.fixture_assets()
        with mock.patch("ariadne.survival_library.ASSETS", assets):
            references = SurvivalLibrary(self.registry)
            index = assets / "text-index.json"
            original = index.read_bytes()
            index.write_bytes(original.replace(b"Stable original text", b"Unverified rewritten text"))
            with self.assertRaisesRegex(ValueError, "integrity"):
                references.read_reference("fixture")
            with self.assertRaisesRegex(ValueError, "integrity"):
                references.search_text("Unverified")
            index.write_bytes(original)
            catalog = assets / "catalog.json"
            catalog.write_bytes(catalog.read_bytes().replace(b"Fixture source", b"Replaced source"))
            with self.assertRaisesRegex(ValueError, "integrity"):
                references.search_text("Stable")

    def test_actual_bundled_sources_hashes_sections_and_model_offset_page(self):
        references = SurvivalLibrary(self.registry)
        catalog = references.catalog("", "")
        resources = [resource for resource in catalog["resources"] if resource.get("path")]
        self.assertGreaterEqual(len(resources), 20)
        for resource in resources:
            with self.subTest(resource=resource["id"]):
                raw, _ = references.download(resource["id"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), resource["sha256"])
                read = references.read_reference(resource["id"])
                self.assertTrue(read["untrusted_reference"])
                self.assertTrue(read["section"]["text"].strip())
        # The model receives bounded chunks even for a long actual imported
        # chapter; operator UI reads retain the complete section.
        long_epub = epub_bytes()
        # Rebuild only the first XHTML with a long but permitted text section.
        incoming, outgoing = io.BytesIO(long_epub), io.BytesIO()
        with zipfile.ZipFile(incoming) as source, zipfile.ZipFile(outgoing, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == "OPS/first.xhtml":
                    data = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>' + b"early " * 2000 + b"OFFSETCHAPTERTAIL" + b'</p></body></html>'
                target.writestr(item.filename, data)
        book = self.import_file("long.epub", outgoing.getvalue())["book"]
        references.books = self.books
        first = references.execute("survival_reference", {"action": "read_book", "book_id": book["id"], "section": 1})
        self.assertLessEqual(len(first["section"]["text"]), 8000)
        self.assertNotIn("OFFSETCHAPTERTAIL", first["section"]["text"])
        tail = references.execute("survival_reference", {"action": "read_book", "book_id": book["id"], "section": 1, "offset": 8000})
        self.assertIn("OFFSETCHAPTERTAIL", tail["section"]["text"])
        self.assertIn("OFFSETCHAPTERTAIL", self.books.read(book["id"], 1)["section"]["text"])


if __name__ == "__main__":
    unittest.main()
