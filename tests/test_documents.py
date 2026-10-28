import hashlib
import importlib.util
import io
import json
import stat
import unittest
import warnings
import zipfile
from unittest.mock import patch

from ariadne.documents import (MAX_INPUT_BYTES, MAX_OUTPUT_CHARS, document_capabilities,
                               extract_document, _xml)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def archive(parts):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in parts.items():
            bundle.writestr(name, content)
    return output.getvalue()


def docx(body):
    return archive({"[Content_Types].xml": "<Types/>", "word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'})


def xlsx(sheet, shared=None, target="worksheets/sheet1.xml", external=False):
    parts = {
        "xl/workbook.xml": f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="Sales" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="rId1" Target="' + target + '"' + (' TargetMode="External"' if external else '') + '/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{S}"><sheetData>{sheet}</sheetData></worksheet>',
    }
    if shared is not None:
        parts["xl/sharedStrings.xml"] = f'<sst xmlns="{S}">{shared}</sst>'
    return archive(parts)


class DocumentTests(unittest.TestCase):
    def test_text_encodings_hash_and_output_limit(self):
        data = "Café local\n".encode("utf-16")
        result = extract_document("note.md", data)
        self.assertEqual(result["text"], "Café local\n")
        self.assertEqual(result["format"], "text")
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result["bytes"], len(data))
        large = extract_document("large.txt", b"a" * (MAX_OUTPUT_CHARS + 20))
        self.assertEqual(len(large["text"]), MAX_OUTPUT_CHARS)
        self.assertIn("truncated", large["warnings"][0])
        self.assertTrue(extract_document("empty.txt", b"")["warnings"])

    def test_json_csv_tsv_and_code_are_data_only(self):
        self.assertEqual(extract_document("records.json", b'[{"id":1}]')["format"], "json")
        self.assertEqual(extract_document("run.py", b"import os\nprint('data only')")["format"], "text")
        self.assertEqual(extract_document("records.csv", b'name,note\n"A,B","quoted"\n')["text"], "name | note\nA,B | quoted")
        self.assertEqual(extract_document("records.tsv", b"id\tamount\n1\t2")["text"], "id | amount\n1 | 2")
        for name, data in [("bad.json", b"{bad"), ("nan.json", b'{"x":NaN}'), ("bad.csv", b'x\n"unfinished')]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    extract_document(name, data)

    def test_unsupported_binary_invalid_encoding_and_input_caps(self):
        for name, data in [("file.exe", b"MZ"), ("file.txt", b"a\x00b"), ("file.md", b"\xffbad"),
                           ("large.txt", b"a" * (MAX_INPUT_BYTES + 1)), ("bad.docx", b"not a ZIP")]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    extract_document(name, data)

    def test_docx_paragraphs_tabs_breaks_and_tables(self):
        data = docx('<w:p><w:r><w:t>Hello</w:t><w:tab/><w:t>world</w:t><w:br/><w:t>Next</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell value</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        result = extract_document("notes.docx", data)
        self.assertEqual(result["format"], "docx")
        self.assertIn("Hello\tworld\nNext", result["text"])
        self.assertIn("Cell value", result["text"])

    def test_xlsx_shared_inline_sparse_and_cached_formula_values(self):
        data = xlsx('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="inlineStr"><is><t>Amount</t></is></c></row><row r="2"><c r="A2" t="b"><v>1</v></c><c r="C2"><f>SUM(1,2)</f><v>3</v></c></row><row r="3"><c r="A3"><f>NOW()</f></c></row><row r="4"><c r="A4"><f>SUM(A1:A2)</f><v></v></c></row>', '<si><t>Invoice</t></si>')
        result = extract_document("sales.xlsx", data)
        self.assertIn("Invoice |  | Amount", result["text"])
        self.assertIn("TRUE |  | 3", result["text"])
        self.assertIn("[formula result unavailable]", result["text"])
        self.assertEqual(result["text"].count("[formula result unavailable]"), 2)
        self.assertTrue(any("cached" in warning for warning in result["warnings"]))

    def test_xlsx_invalid_indices_relationships_and_external_links(self):
        with self.assertRaises(ValueError):
            extract_document("bad.xlsx", xlsx('<row><c r="A1" t="s"><v>-1</v></c></row>', '<si><t>Only value</t></si>'))
        with self.assertRaises(ValueError):
            extract_document("bad.xlsx", xlsx("", target="../../outside.xml"))
        result = extract_document("external.xlsx", xlsx("", target="https://example.invalid/sheet.xml", external=True))
        self.assertTrue(any("external" in warning for warning in result["warnings"]))

    def test_archive_traversal_duplicate_links_and_zip_bombs_are_rejected(self):
        for member in ["../outside.xml", "/absolute.xml", "C:/outside.xml", "a\\outside.xml", "a/../outside.xml"]:
            with self.subTest(member=member):
                with self.assertRaises(ValueError):
                    extract_document("bad.docx", archive({member: "unsafe"}))
        for members in [{"huge.xml": b"x" * 8_000_001}, {str(index): "" for index in range(1001)}]:
            with self.assertRaises(ValueError):
                extract_document("bomb.docx", archive(members))
        output = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(output, "w") as bundle:
                bundle.writestr("duplicate.xml", "one")
                bundle.writestr("duplicate.xml", "two")
        with self.assertRaises(ValueError):
            extract_document("duplicate.docx", output.getvalue())
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as bundle:
            link = zipfile.ZipInfo("linked.xml")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(link, "outside.xml")
        with self.assertRaises(ValueError):
            extract_document("linked.docx", output.getvalue())

    def test_xml_entity_utf16_and_depth_bombs_are_rejected(self):
        for data in [b'<!DOCTYPE x [<!ENTITY e "expanded">]><x>&e;</x>',
                     '<!DOCTYPE x [<!ENTITY e "expanded">]><x>&e;</x>'.encode("utf-16"),
                     ("<x>" * 65 + "text" + "</x>" * 65).encode()]:
            with self.subTest(data=data[:40]):
                with self.assertRaises(ValueError):
                    _xml(data)

    def test_capabilities_report_optional_pdf_readiness(self):
        result = document_capabilities()
        formats = {entry["extension"]: entry for entry in result["formats"]}
        self.assertTrue(formats["docx"]["ready"])
        self.assertTrue(formats["xlsx"]["ready"])
        self.assertEqual(formats["pdf"]["ready"], result["pdf"]["ready"])
        self.assertEqual(result["limits"]["input_bytes"], MAX_INPUT_BYTES)

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "Optional pypdf is not installed in this interpreter.")
    def test_real_pdf_worker_and_encryption_rejection(self):
        from pypdf import PdfWriter
        from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 50 740 Td (Hello offline PDF) Tj ET")
        page[NameObject("/Contents")] = stream
        output = io.BytesIO()
        writer.write(output)
        result = extract_document("hello.pdf", output.getvalue())
        self.assertIn("Hello offline PDF", result["text"])
        with patch("ariadne.documents.PDF_TIMEOUT_SECONDS", 0.001):
            with self.assertRaisesRegex(ValueError, "time limit"):
                extract_document("timeout.pdf", output.getvalue())
        writer.encrypt("secret")
        encrypted = io.BytesIO()
        writer.write(encrypted)
        with self.assertRaisesRegex(ValueError, "Encrypted"):
            extract_document("encrypted.pdf", encrypted.getvalue())
        with self.assertRaises(ValueError):
            extract_document("malformed.pdf", b"%PDF-1.7\nbad")


if __name__ == "__main__":
    unittest.main()
