import base64
import hashlib
import io
import json
import os
import socket
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from ariadne.config import Settings
from ariadne.knowledge_tools import KnowledgeTools, MAX_CHUNKS, ROOT, canonical, validate_json
from ariadne.store import Store
from ariadne.tools import ToolRegistry


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".data-test-knowledge-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.registry = ToolRegistry(Settings(data_dir=self.data))
        self.store = Store(self.data / "state.sqlite")
        self.tools = KnowledgeTools(self.registry, self.store)

    def production_config(self):
        source = ROOT / "data/knowledge-config.json"
        if not source.exists():
            self.skipTest("Optional knowledge assets not provisioned")
        self.tools.config_path.write_bytes(source.read_bytes())

    def test_lexical_provenance_and_unavailable_model_never_downloads(self):
        document = self.store.document_add("Generator manual", "Keep the generator oil level within the marked range.")
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            result = self.tools.search("generator")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "lexical")
        row = result["results"][0]
        self.assertEqual(row["document_id"], document["id"])
        self.assertEqual(row["content_sha256"], hashlib.sha256(row["content"].encode()).hexdigest())
        self.assertIn("chunk:", row["citation"])
        self.assertTrue(result["warning"])
        self.assertEqual({item["function"]["name"] for item in self.tools.schemas()}, {"hybrid_search"})

    def test_validation_rejects_paths_shapes_and_host_values(self):
        for path in ("E:/anything.pdf", "../escape.pdf", "C:/Windows/file.pdf", "https://example.test/file.pdf", "doc.txt"):
            with self.subTest(path=path):
                self.assertFalse(self.tools.execute("document_structure", {"path": path})["ok"])
        for value in (float("nan"), {"a": object()}, b"binary", {1: 2}, [0] * 10_001):
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                validate_json(value)
        self.assertFalse(self.tools.execute("hybrid_search", {"query": "x", "url": "http://evil"})["ok"])
        self.assertFalse(self.tools.execute("code_transform", {"code": "1", "data": None, "mount": "/"})["ok"])
        self.assertEqual(self.tools.policy("code_transform", {}), "approval")
        self.assertEqual(self.tools.policy("knowledge_reindex", {}), "approval")

    def test_owner_config_rejects_remote_kiwix_and_credentials(self):
        for url in ("http://localhost:8081", "http://192.168.1.4:8081", "http://127.0.0.1:8081/path", "http://user@127.0.0.1:8081", "http://127.0.0.1:8081/?x=y", "https://127.0.0.1:8081"):
            self.tools.config_path.write_bytes(canonical({"schema": 1, "kiwix": {"url": url, "archive": "manual", "date": "2026-10-28", "sha256": "a" * 64}}))
            with self.subTest(url=url):
                self.assertFalse(self.tools.execute("library_search", {"query": "oil"})["ok"])

    def test_kiwix_provisioning_rebases_verified_assets_for_another_pc(self):
        from scripts.provision_knowledge import rebase_kiwix, BASE
        manifest = {"version": "3.8.1", "binary": "C:/obsolete-host/kiwix-serve.exe", "assets": [], "download": {},
                    "archives": [{"name": "python", "path": "C:/obsolete-host/python.zim", "date": "2026-08-02", "sha256": "a" * 64}]}
        result = rebase_kiwix(manifest, {"kiwix/server/kiwix-serve.exe", "library/python.zim"})
        self.assertEqual(result["binary"], str(BASE / "kiwix/server/kiwix-serve.exe"))
        self.assertEqual(result["archives"][0]["path"], str(BASE / "library/python.zim"))
        with self.assertRaises(ValueError):
            rebase_kiwix(manifest, {"kiwix/server/kiwix-serve.exe"})

    def test_model_provisioning_uses_frozen_commits_without_branch_lookups(self):
        from scripts import provision_knowledge as provision
        lock = provision.asset_lock()
        commits = {lock["embedding"]["revision"], *(source["revision"] for source in lock["layout"]["sources"])}
        with mock.patch.object(provision, "BASE", self.data / "cached-models"), mock.patch.object(provision, "fetch_asset") as fetch, mock.patch.object(provision, "get", side_effect=AssertionError("No metadata or branch lookup allowed")):
            provision.fetch_model(True)
            provision.fetch_layout(True)
        urls = [call.args[1] for call in fetch.call_args_list]
        self.assertEqual(len(urls), len(lock["embedding"]["assets"]) + len(lock["layout"]["assets"]))
        self.assertEqual({url.split("/resolve/")[1].split("/")[0] for url in urls}, commits)
        self.assertFalse(any("/main/" in url or "/v2.3.0/" in url for url in urls))

    def test_snapshot_hash_and_changed_input_rejection(self):
        file = self.registry.root / "manual.pdf"
        file.write_bytes(b"%PDF-1.4 snapshot")
        snapshot = self.tools._snapshot("manual.pdf")
        self.assertEqual(snapshot["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
        self.assertEqual(base64.b64decode(snapshot["data"]), file.read_bytes())
        file.write_bytes(b"x" * 10_000_001)
        self.assertFalse(self.tools.execute("document_structure", {"path": "manual.pdf"})["ok"])

    def test_reindex_retains_previous_cache_on_mid_build_changes(self):
        self.store.document_add("Initial", "First imported snapshot.")
        old = b'{"previous":"cache"}'
        self.tools.index_path.write_bytes(old)
        def changing_worker(payload, cancelled, **kwargs):
            self.store.document_add("Changed", "A second import arrived during indexing.")
            return {"ok": True, "vectors": ["bounded-result" for text in payload["texts"]]}
        with mock.patch.object(self.tools, "_profile", return_value={"qualified": True}), mock.patch.object(self.tools, "_run", side_effect=changing_worker):
            result = self.tools.execute("knowledge_reindex", {})
        self.assertFalse(result["ok"], result)
        self.assertIn("changed", result["error"])
        self.assertEqual(self.tools.index_path.read_bytes(), old)

    def test_reindex_retains_previous_cache_when_later_batch_fails(self):
        self.store.document_add("Large snapshot", "x" * (1200 * 130))
        old = b'{"previous":"cache"}'
        self.tools.index_path.write_bytes(old)
        calls = []
        def failing_worker(payload, cancelled, **kwargs):
            calls.append(len(payload["texts"]))
            if len(calls) > 1:
                return {"ok": False, "error": "Bounded worker failed."}
            return {"ok": True, "vectors": ["bounded-result" for text in payload["texts"]]}
        with mock.patch.object(self.tools, "_profile", return_value={"qualified": True}), mock.patch.object(self.tools, "_run", side_effect=failing_worker):
            result = self.tools.execute("knowledge_reindex", {})
        self.assertFalse(result["ok"], result)
        self.assertGreater(len(calls), 1)
        self.assertEqual(self.tools.index_path.read_bytes(), old)

    def test_corpus_bound_is_explicit(self):
        self.store.document_add("Limits", "x" * (1200 * (MAX_CHUNKS + 1)))
        with self.assertRaisesRegex(ValueError, "1000 chunks"):
            self.tools._corpus()

    def test_real_monty_json_print_filesystem_import_and_limits(self):
        self.production_config()
        if not self.tools.status()["monty"]["ready"]:
            self.skipTest("Pinned Monty not installed")
        result = self.tools.execute("code_transform", {"code": "print('working')\n{'total': sum(x['quantity'] * x['price'] for x in data)}", "data": [{"quantity": 3, "price": 5}, {"quantity": 2, "price": 7}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["value"], {"total": 29})
        self.assertEqual(result["stdout"], "working\n")
        for code in ("open('/secret')", "import subprocess\nsubprocess.run('whoami')", "'x' * 1000000000", "while True: pass"):
            with self.subTest(code=code):
                answer = self.tools.execute("code_transform", {"code": code, "data": None})
                self.assertFalse(answer["ok"], answer)
        healthy = self.tools.execute("code_transform", {"code": "data + 1", "data": 41})
        self.assertEqual(healthy.get("value"), 42, healthy)

    def test_real_semantic_paraphrase_and_stale_version_fallback(self):
        self.production_config()
        if not self.tools.status()["semantic"]["ready"]:
            self.skipTest("Pinned semantic assets not installed")
        metadata = {"source_id": "approved", "source_path": "vehicles.md", "source_name": "Maintenance", "sha256": "b" * 64}
        self.store.document_upsert("Vehicle service", "The automobile requires new tires before its next highway trip.", metadata)
        self.store.document_add("Baking", "A sourdough starter contains wild yeast and bacteria.")
        indexed = self.tools.execute("knowledge_reindex", {})
        self.assertTrue(indexed["ok"], indexed)
        result = self.tools.search("car maintenance")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "hybrid", result)
        self.assertEqual(result["results"][0]["title"], "Vehicle service")
        self.assertIn("semantic", result["results"][0]["retrieval"])
        self.assertEqual(result["results"][0]["sha256"], "b" * 64)
        self.store.document_upsert("Vehicle service", "The automobile now has new tires.", {**metadata, "sha256": "c" * 64})
        result = self.tools.search("automobile")
        self.assertEqual(result["mode"], "lexical")
        self.assertEqual(result["results"][0]["version"], 2)
        self.assertEqual(result["results"][0]["sha256"], "c" * 64)

    def test_real_docling_docx_structure_no_model_downloads(self):
        self.production_config()
        if not self.tools.status()["docling"]["ready"]:
            self.skipTest("Pinned Docling not installed")
        # Genuine minimal OOXML; never import optional packages into the server.
        with zipfile.ZipFile(self.registry.root / "supplies.docx", "w") as archive:
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Supply list</w:t></w:r></w:p><w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="1000"/><w:gridCol w:w="1000"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Quantity</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>Battery</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>4</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        result = self.tools.execute("document_structure", {"path": "supplies.docx"})
        self.assertTrue(result["ok"], result)
        self.assertIn("Supply list", result["markdown"])
        self.assertIn("Battery", result["markdown"])
        self.assertEqual(result["tables"], 1)
        self.assertEqual(result["source"]["sha256"], hashlib.sha256((self.registry.root / "supplies.docx").read_bytes()).hexdigest())

    def test_real_kiwix_search_read_and_path_authority(self):
        self.production_config()
        if not self.tools.status()["kiwix"]["ready"]:
            self.skipTest("Pinned Kiwix archives not installed")
        # Restricted test shells can deny all loopback connections. Treat that
        # as an environment skip; the owner qualification run uses loopback.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            try:
                probe = socket.create_connection(listener.getsockname(), timeout=1)
                probe.close()
            except OSError:
                self.skipTest("Test shell denies loopback connections")
        result = self.tools.execute("library_search", {"query": "transaction", "limit": 3})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["results"], result)
        article = result["results"][0]
        self.assertEqual(len(article["archive_sha256"]), 64)
        read = self.tools.execute("library_read", {"archive": article["archive"], "article_path": article["article_path"]})
        self.assertTrue(read["ok"], read)
        self.assertIn("SQLite", read["text"])
        for path in ("http://example.test", "/content/other/a", "/content/" + article["archive"] + "/%2e%2e/secret", "E:/anything"):
            self.assertFalse(self.tools.execute("library_read", {"archive": article["archive"], "article_path": path})["ok"])

    def test_real_docling_pdf_layout_from_local_hashed_models(self):
        self.production_config()
        if not self.tools.status()["docling"]["ready"] or self.tools._profile("docling", verify=False).get("mode") != "layout":
            self.skipTest("Pinned Docling layout assets not installed")
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(612, 792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 20 Tf 50 740 Td (Supply manifest) Tj ET\nBT /F1 12 Tf 50 710 Td (Equipment reserved for the local workshop.) Tj ET\n0.5 w 50 600 400 80 re S 50 640 m 450 640 l S 300 600 m 300 680 l S\nBT /F1 12 Tf 60 655 Td (Item) Tj ET\nBT /F1 12 Tf 310 655 Td (Quantity) Tj ET\nBT /F1 12 Tf 60 615 Td (Battery) Tj ET\nBT /F1 12 Tf 310 615 Td (4) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        path = self.registry.root / "supply.pdf"
        with path.open("wb") as output:
            writer.write(output)
        result = self.tools.execute("document_structure", {"path": "supply.pdf"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["processor"]["mode"], "layout")
        self.assertEqual(result["pages"], 1)
        self.assertIn("Supply manifest", result["markdown"])
        self.assertIn("Battery", result["markdown"])
        self.assertGreaterEqual(result["tables"], 1, result)


if __name__ == "__main__":
    unittest.main()
