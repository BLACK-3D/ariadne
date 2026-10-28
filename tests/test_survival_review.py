"""Independent review regressions for private-book confinement and coverage.

Fixtures are original synthetic content and remain inside Yikes. When Windows
denies link creation, only the link metadata is substituted; SQLite must still
remain unopened and the real fixture bytes must remain unchanged.
"""
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ariadne.books import BookLibrary
from ariadne.config import Settings
from ariadne.sources import SourceManager
from ariadne.store import Store
from ariadne.survival_library import SurvivalLibrary
from ariadne.tools import ToolRegistry


class SurvivalReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="survival-review-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_dir=self.root / "private-data", api_token="review-fixture-token")
        self.registry = ToolRegistry(self.settings)
        self.books = BookLibrary(self.registry, None)
        self.books.books()

    def test_primary_and_sqlite_sidecar_hardlinks_are_rejected_before_connect(self):
        original_stat = Path.stat
        for suffix in ("", "-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                candidate = Path(str(self.books.path) + suffix)
                canary = self.root / ("canary" + (suffix or "-primary"))
                if suffix:
                    canary.write_bytes(b"OWNER-REVIEW-CANARY\n")
                    before = canary.read_bytes()
                    source, link = canary, candidate
                else:
                    before = candidate.read_bytes()
                    source, link = candidate, canary
                try:
                    os.link(source, link)
                except (OSError, NotImplementedError):
                    if suffix:
                        candidate.write_bytes(before)
                    def linked_stat(path, *args, **kwargs):
                        info = original_stat(path, *args, **kwargs)
                        if path == candidate:
                            return SimpleNamespace(st_mode=info.st_mode, st_nlink=2,
                                                   st_file_attributes=getattr(info, "st_file_attributes", 0))
                        return info
                    guard = mock.patch.object(Path, "stat", linked_stat)
                else:
                    guard = mock.patch.object(Path, "stat", original_stat)
                try:
                    with guard, mock.patch("ariadne.books.sqlite3.connect") as connect:
                        with self.assertRaisesRegex(ValueError, "one link"):
                            self.books.books()
                        connect.assert_not_called()
                    self.assertEqual(source.read_bytes(), before)
                finally:
                    if suffix:
                        candidate.unlink(missing_ok=True)
                    else:
                        canary.unlink(missing_ok=True)

    def test_sqlite_sidecar_reparse_points_are_rejected_before_connect(self):
        original_lstat = Path.lstat
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                canary = self.root / ("reparse-canary" + suffix)
                canary.write_bytes(b"OWNER-REVIEW-CANARY\n")
                candidate = Path(str(self.books.path) + suffix)
                try:
                    candidate.symlink_to(canary)
                except (OSError, NotImplementedError):
                    candidate.write_bytes(canary.read_bytes())
                    def linked_lstat(path, *args, **kwargs):
                        info = original_lstat(path, *args, **kwargs)
                        if path == candidate:
                            return SimpleNamespace(st_mode=info.st_mode,
                                                   st_file_attributes=getattr(info, "st_file_attributes", 0) | 0x400)
                        return info
                    guard = mock.patch.object(Path, "lstat", linked_lstat)
                else:
                    guard = mock.patch.object(Path, "lstat", original_lstat)
                try:
                    with guard, mock.patch("ariadne.books.sqlite3.connect") as connect:
                        with self.assertRaisesRegex(ValueError, "symbolic links|reparse"):
                            self.books.books()
                        connect.assert_not_called()
                    self.assertEqual(canary.read_bytes(), b"OWNER-REVIEW-CANARY\n")
                finally:
                    candidate.unlink(missing_ok=True)

    def test_directory_sqlite_sidecar_is_rejected_before_connect(self):
        sidecar = Path(str(self.books.path) + "-wal")
        sidecar.mkdir()
        with mock.patch("ariadne.books.sqlite3.connect") as connect:
            with self.assertRaisesRegex(ValueError, "regular files"):
                self.books.books()
            connect.assert_not_called()

    def test_some_text_per_section_does_not_certify_complete_content(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="package.opf"/></rootfiles></container>')
            archive.writestr("package.opf", '<package><manifest><item id="body" href="body.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="body"/></spine></package>')
            archive.writestr("body.xhtml", '<html><body><h1>Visible heading</h1><svg xmlns="http://www.w3.org/2000/svg"><text>UNINTERPRETED-DIAGRAM-CONTENT</text></svg></body></html>')
        raw = content.getvalue()
        store = Store(self.settings.data_dir / "review-state.sqlite3")
        sources = SourceManager(self.settings, store)
        folder = self.root / "lawful-references"
        folder.mkdir()
        (folder / "coverage.epub").write_bytes(raw)
        source = sources.register("Original review fixture", str(folder))
        self.books.sources = sources
        book = self.books.import_book(source["id"], "coverage.epub", hashlib.sha256(raw).hexdigest(), "Original coverage fixture")["book"]
        self.assertTrue(book["processed_all_sections"])
        self.assertTrue(book["coverage_complete"])
        self.assertEqual(book["coverage_metric"], "sections_with_any_extractable_text")
        self.assertIn("not certified", book["coverage_notice"])
        self.assertFalse(book["image_coverage"])
        self.assertTrue(any("not interpreted" in warning for warning in book["warnings"]))
        text = self.books.read(book["id"])["section"]["text"]
        self.assertIn("Visible heading", text)
        self.assertNotIn("UNINTERPRETED-DIAGRAM-CONTENT", text)

    def reference_assets(self, *, indexed=True):
        assets = self.root / "reference-assets"
        assets.mkdir()
        raw = b"ORIGINAL-REFERENCE-FIXTURE\n"
        (assets / "fixture.md").write_bytes(raw)
        resource = {"id": "fixture", "title": "Original reference fixture", "topic": "water",
                    "summary": "Metadata summary is not the source text.", "checklist": [],
                    "path": "fixture.md", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        external = {"id": "external", "title": "External reference", "topic": "water",
                    "summary": "External catalog summary.", "checklist": []}
        catalog = json.dumps({"schema": 1, "resources": [resource, external]}).encode()
        (assets / "catalog.json").write_bytes(catalog)
        records = {"fixture": {"source_sha256": resource["sha256"],
                               "sections": [{"number": 1, "label": "Original section", "text": raw.decode()}]}} if indexed else {}
        index = json.dumps({"schema": 1, "resources": records}).encode()
        (assets / "text-index.json").write_bytes(index)
        (assets / "text-index-manifest.json").write_text(json.dumps({"schema": 1,
            "bytes": len(index), "sha256": hashlib.sha256(index).hexdigest(),
            "catalog_sha256": hashlib.sha256(catalog).hexdigest()}), encoding="utf-8")
        return assets

    def test_missing_index_refuses_summary_substitution_for_source_sections(self):
        assets = self.reference_assets(indexed=False)
        references = SurvivalLibrary(self.registry)
        with mock.patch("ariadne.survival_library.ASSETS", assets):
            with self.assertRaisesRegex(ValueError, "no verified text index"):
                references.read_reference("fixture")
            (assets / "text-index.json").unlink()
            for operation in (lambda: references.read_reference("fixture"),
                              lambda: references.search_text("ORIGINAL-REFERENCE-FIXTURE")):
                with self.assertRaisesRegex(ValueError, "index is missing"):
                    operation()
            # A missing text index does not falsify or block the intact original
            # file download; its hash still establishes the downloaded bytes.
            self.assertEqual(references.download("fixture"), (b"ORIGINAL-REFERENCE-FIXTURE\n", "fixture.md"))

    def test_reference_only_metadata_is_labeled_and_search_preserves_file_type(self):
        assets = self.reference_assets()
        references = SurvivalLibrary(self.registry)
        with mock.patch("ariadne.survival_library.ASSETS", assets):
            metadata = references.read_reference("external")
            self.assertEqual(metadata["content_kind"], "catalog_metadata_only")
            self.assertFalse(metadata["source_text_available"])
            self.assertIn("Catalog metadata only", metadata["section"]["text"])
            found = references.search_text("ORIGINAL-REFERENCE-FIXTURE")[0]
            self.assertEqual(found["path"], "fixture.md")
            self.assertEqual(found["download_name"], "fixture.md")
            original = references.read_reference("fixture")
            self.assertEqual(original["content_kind"], "source_text")
            self.assertEqual(original["section"]["text"], "ORIGINAL-REFERENCE-FIXTURE\n")


if __name__ == "__main__":
    unittest.main()
