"""Fixed, bounded PDF/EPUB text reader. No network, active content or DRM bypass."""
from __future__ import annotations

import io
import json
import posixpath
import sys
import urllib.parse
from pathlib import Path

MAX_INPUT = 128 * 1024 * 1024
MAX_SECTIONS = 2000
MAX_SECTION = 100_000
MAX_TEXT = 12_000_000
MAX_RESPONSE = 50_000_000


def extract(data, kind):
    from ariadne.documents import _archive, _read_xml
    sections = []
    total = 0

    def add(label, text):
        nonlocal total
        text = "".join(c for c in text if ord(c) >= 32 or c in "\n\r\t")
        total += len(text)
        if len(sections) >= MAX_SECTIONS or len(text) > MAX_SECTION or total > MAX_TEXT:
            raise ValueError("Book exceeds bounded extraction limits; split it into smaller volumes. No partial import was saved.")
        sections.append({"number": len(sections) + 1, "label": label[:500], "text": text})

    if kind == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are unsupported; use your lawful unencrypted copy. DRM is not bypassed.")
        if not 1 <= len(reader.pages) <= MAX_SECTIONS:
            raise ValueError("PDF must have 1–2000 pages; split larger books into volumes.")
        for index, page in enumerate(reader.pages, 1):
            add(f"Page {index}", page.extract_text() or "")
    elif kind == "epub":
        with _archive(data) as archive:
            names = set(archive.namelist())
            if "META-INF/encryption.xml" in names:
                raise ValueError("Encrypted or obfuscated EPUBs are unsupported; DRM is not bypassed.")
            if archive.read("mimetype").strip() != b"application/epub+zip":
                raise ValueError("The file is not an EPUB book.")
            container = _read_xml(archive, "META-INF/container.xml")
            roots = [e.attrib.get("full-path", "") for e in container.iter() if e.tag.rsplit("}", 1)[-1] == "rootfile"]
            if len(roots) != 1 or roots[0] not in names:
                raise ValueError("EPUB must have one local package document.")
            package_name = roots[0]
            package = _read_xml(archive, package_name)
            manifest, order = {}, []
            for element in package.iter():
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "item" and element.get("media-type") in {"application/xhtml+xml", "text/html"}:
                    href = urllib.parse.urlsplit(element.get("href", ""))
                    decoded = urllib.parse.unquote(href.path)
                    if href.scheme or href.netloc or href.query or decoded.startswith("/") or "\\" in decoded or ":" in decoded:
                        raise ValueError("EPUB text references must be local archive members.")
                    name = posixpath.normpath(posixpath.join(posixpath.dirname(package_name), decoded))
                    if name.startswith("../") or name not in names:
                        raise ValueError("EPUB text reference escaped its archive.")
                    identifier = element.get("id", "")
                    if not identifier or identifier in manifest:
                        raise ValueError("EPUB text manifest has duplicate or missing IDs.")
                    manifest[identifier] = name
                if tag == "itemref":
                    order.append(element.get("idref", ""))
            if not manifest or any(identifier not in manifest for identifier in order):
                raise ValueError("EPUB has an unsupported or missing text spine.")
            # Include non-spine text too (for example endnotes); never silently omit it.
            order = list(dict.fromkeys(order + list(manifest)))
            for identifier in order:
                name = manifest[identifier]
                tree = _read_xml(archive, name)
                parts = []
                def walk(element):
                    tag = element.tag.rsplit("}", 1)[-1].lower()
                    if tag in {"script", "style", "head", "svg", "math"}:
                        return
                    if element.text:
                        parts.append(element.text)
                    for child in element:
                        walk(child)
                        if child.tail:
                            parts.append(child.tail)
                    if tag in {"p", "div", "h1", "h2", "h3", "li", "section", "br"}:
                        parts.append("\n")
                walk(tree)
                add(name, "".join(parts))
    else:
        raise ValueError("Only PDF and EPUB books are supported.")
    empty = [s["number"] for s in sections if not any(c.isalnum() for c in s["text"])]
    return {"sections": sections, "total_sections": len(sections), "empty_sections": empty,
            "processed_all_sections": True, "coverage_complete": not empty,
            "coverage_metric": "sections_with_any_extractable_text",
            "image_coverage": False,
            "warnings": ["Text extraction only: diagrams, photographs, mathematical layout and embedded media are not interpreted."] +
                        (["Some sections contain no extractable text; inspect them or import an OCR text edition."] if empty else [])}


def main():
    # Invoke only this fixed module; the parent passes raw file bytes, never a host path.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from ariadne import documents
        documents.PDF_MEMORY_BYTES = 1024 * 1024 * 1024
        documents.PDF_TIMEOUT_SECONDS = 90
        documents._limit_pdf_worker()
        from ariadne.network_policy import install_network_policy
        # The fixed reader has no reason to contact even local services.
        install_network_policy()
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise ValueError("Books are limited to 128 MiB.")
        result = extract(data, sys.argv[1])
    except ValueError as error:
        result = {"error": str(error)}
    except (Exception, MemoryError):
        result = {"error": "Book is malformed, unsupported, or exceeded the reader's resource bounds."}
    output = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(output) > MAX_RESPONSE:
        output = b'{"error":"Book output exceeded the bounded response size; no partial import was saved."}'
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
