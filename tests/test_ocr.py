"""OCR boundary tests; synthetic text is generated locally, never downloaded."""
import hashlib
import io
import json
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from ariadne import ocr
from ariadne._ocr_worker import checked_dimensions, deny_network_and_execution


class OCRBoundaryTests(unittest.TestCase):
    def test_idle_import_does_not_import_inference_or_imaging(self):
        # A separate clean interpreter is exercised in qualification. This unit
        # checks the adapter's public module dependencies rather than test order.
        self.assertNotIn("rapidocr", ocr.__dict__)
        self.assertNotIn("onnxruntime", ocr.__dict__)
        self.assertNotIn("PIL", ocr.__dict__)

    def test_no_url_path_unsupported_format_empty_or_large_input(self):
        for name in ("https://example.invalid/file.png", "../file.png", "E:\\file.png", "folder/file.png", "file.gif", "file.png\x00"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                ocr.extract_ocr(name, b"not processed", "unused")
        for data in (b"", b"x" * (ocr.MAX_INPUT_BYTES + 1), "not bytes"):
            with self.subTest(kind=type(data).__name__), self.assertRaises(ValueError):
                ocr.extract_ocr("fixture.png", data, "unused")

    def test_dimensions_reject_pixel_bombs_and_invalid_numbers_before_decode(self):
        self.assertEqual(checked_dimensions(1400, 420, ocr), (1400, 420))
        for dimensions in ((6001, 1), (4000, 4000), (0, 1), (-1, 2), (float("inf"), 2), (float("nan"), 2)):
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                checked_dimensions(*dimensions, ocr)

    def test_runtime_guard_denies_python_network_and_child_process_paths(self):
        for event in ("socket.__new__", "socket.getaddrinfo", "socket.connect", "urllib.Request", "http.client.connect", "subprocess.Popen", "os.system", "os.posix_spawn"):
            with self.subTest(event=event), self.assertRaises(PermissionError):
                deny_network_and_execution(event, ())

    def test_worker_protocol_identity_and_cancel_callback_preserved(self):
        config = {"processor": {"identity": "fixture-identity"}}
        callback = lambda: False
        returned = json.dumps({"text": "Synthetic OCR", "warnings": ["Fixture note"]}).encode()
        with patch.object(ocr, "run_worker", return_value=returned) as execute:
            result = ocr._extract(".png", b"image fixture", Path("fixture-config.json"), config, callback)
        self.assertEqual(result["sha256"], hashlib.sha256(b"image fixture").hexdigest())
        self.assertEqual(result["processor"], config["processor"])
        self.assertEqual(result["format"], "ocr-png")
        args, kwargs = execute.call_args
        self.assertEqual(args[0].name, "_ocr_worker.py")
        self.assertEqual(json.loads(args[1].split(b"\n", 1)[0])["extension"], ".png")
        self.assertEqual(args[1].split(b"\n", 1)[1], b"image fixture")
        self.assertIs(kwargs["cancelled"], callback)
        self.assertEqual(kwargs["memory_bytes"], ocr.MEMORY_BYTES)
        self.assertEqual(kwargs["max_output_bytes"], ocr.MAX_RESPONSE)

    def test_invalid_worker_results_never_become_imported_text(self):
        invalid = [b"not JSON", b"[]", b'{"error":"rejected fixture"}', b'{"text":"","warnings":[]}',
                   b'{"text":"text","warnings":"wrong type"}', json.dumps({"text": "x" * (ocr.MAX_TEXT + 1), "warnings": []}).encode()]
        for output in invalid:
            with self.subTest(length=len(output)), patch.object(ocr, "run_worker", return_value=output), self.assertRaises(ValueError):
                ocr._extract(".png", b"fixture", Path("fixture-config.json"), {"processor": {}}, None)

    def test_unprovisioned_and_forbidden_config_are_unavailable(self):
        with tempfile.TemporaryDirectory(prefix="ocr-unit-", dir=Path.cwd()) as folder:
            self.assertFalse(ocr.ocr_status(Path(folder) / "missing.json")["ready"])
        self.assertFalse(ocr.ocr_status("E:/forbidden/ocr-config.json")["ready"])


class OCRRealFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Path.cwd() / "data/ocr-config.json"
        if not ocr.ocr_status(cls.config)["ready"]:
            raise unittest.SkipTest("Optional pinned OCR profile has not been explicitly provisioned")

    def image_bytes(self, kind="PNG"):
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (1400, 420), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=52)
        draw.text((60, 70), "ARIADNE LOCAL OCR 314159", font=font, fill="black")
        draw.text((60, 170), "INVOICE TOTAL 16.00", font=font, fill="black")
        output = io.BytesIO()
        image.save(output, format=kind)
        image.close()
        return output.getvalue()

    def test_real_png_jpeg_and_raster_pdf_recognize_expected_text(self):
        from PIL import Image
        cases = [("fixture.png", self.image_bytes()), ("fixture.jpg", self.image_bytes("JPEG"))]
        with Image.open(io.BytesIO(cases[0][1])) as image:
            output = io.BytesIO()
            image.save(output, format="PDF", resolution=144)
            cases.append(("fixture.pdf", output.getvalue()))
        for name, payload in cases:
            with self.subTest(name=name):
                result = ocr.extract_ocr(name, payload, self.config)
                for token in ("ARIADNE", "314159", "16.00"):
                    self.assertIn(token, result["text"])
                self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertTrue(result["warnings"])

    def test_real_malformed_blank_and_encrypted_documents_fail(self):
        for name, payload in (("bad.png", b"not an image"), ("bad.pdf", b"%PDF-malformed")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                ocr.extract_ocr(name, payload, self.config)
        from PIL import Image
        image = Image.new("RGB", (300, 100), "white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        with self.assertRaises(ValueError):
            ocr.extract_ocr("blank.png", output.getvalue(), self.config)
        from pypdf import PdfWriter
        for password in ("fixture-password", ""):
            writer = PdfWriter()
            writer.add_blank_page(400, 300)
            writer.encrypt(password)
            output = io.BytesIO()
            writer.write(output)
            with self.subTest(password_empty=not password), self.assertRaises(ValueError):
                ocr.extract_ocr("encrypted.pdf", output.getvalue(), self.config)

    def test_real_page_limit_and_cancellation(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        for _ in range(ocr.MAX_PAGES + 1):
            writer.add_blank_page(400, 300)
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaises(ValueError):
            ocr.extract_ocr("too-many-pages.pdf", output.getvalue(), self.config)
        with self.assertRaises(ValueError):
            ocr.extract_ocr("fixture.png", self.image_bytes(), self.config, cancelled=lambda: True)
        started = time.monotonic()
        with self.assertRaises(ValueError):
            ocr.extract_ocr("fixture.png", self.image_bytes(), self.config, cancelled=lambda: time.monotonic() - started > 0.1)

    def test_real_pixel_bomb_and_oversized_pdf_page_rejected(self):
        def chunk(kind, payload):
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
        # A tiny input claiming a 16-million-pixel raster must be rejected
        # before decoding allocates its claimed image, regardless of IDAT data.
        payload = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4000, 4000, 8, 2, 0, 0, 0))
        payload += chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")
        with self.assertRaises(ValueError):
            ocr.extract_ocr("pixel-bomb.png", payload, self.config)
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(100_000, 100_000)
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaises(ValueError):
            ocr.extract_ocr("oversized-page.pdf", output.getvalue(), self.config)

    def test_changed_transitive_missing_package_and_model_are_unavailable(self):
        original = ocr.importlib.metadata.version
        with patch.object(ocr.importlib.metadata, "version", side_effect=lambda name: "wrong" if name == "numpy" else original(name)):
            self.assertFalse(ocr.ocr_status(self.config)["ready"])
        with patch.object(ocr.importlib.metadata, "version", side_effect=ocr.importlib.metadata.PackageNotFoundError("fixture")):
            self.assertFalse(ocr.ocr_status(self.config)["ready"])
        with tempfile.TemporaryDirectory(prefix="ocr-model-mutation-", dir=Path.cwd()) as folder:
            folder = Path(folder)
            config = json.loads(self.config.read_text(encoding="utf-8"))
            fake_model = folder / ocr.MODEL_HASHES["det"][0]
            fake_model.write_bytes(b"synthetic invalid model bytes")
            config["models"]["det"] = str(fake_model)
            fixture_config = folder / "ocr-config.json"
            fixture_config.write_text(json.dumps(config), encoding="utf-8")
            self.assertFalse(ocr.ocr_status(fixture_config)["ready"])


if __name__ == "__main__":
    unittest.main()
