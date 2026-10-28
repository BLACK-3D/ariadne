"""Bounded local document text extraction. Content is data, never executable."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
import threading
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

MAX_INPUT_BYTES = 10_000_000
MAX_OUTPUT_CHARS = 500_000
MAX_ZIP_BYTES = 40_000_000
MAX_XML_BYTES = 8_000_000
MAX_ZIP_MEMBERS = 1_000
MAX_XML_NODES = 100_000
MAX_PDF_PAGES = 100
PDF_TIMEOUT_SECONDS = 30
PDF_MEMORY_BYTES = 256 * 1024 * 1024
MAX_PDF_RESPONSE = 2_100_000
TEXT_EXTENSIONS = {"txt", "md", "rst", "markdown", "log", "py", "js", "ts", "tsx", "jsx", "html", "css",
                   "yaml", "yml", "toml", "ini", "cfg", "sql", "sh", "ps1", "xml", "json", "csv", "tsv"}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def document_capabilities() -> dict:
    pdf_ready = importlib.util.find_spec("pypdf") is not None and os.name in {"nt", "posix"}
    formats = [{"extension": extension, "format": "text" if extension not in {"json", "csv", "tsv"} else extension,
                "ready": True} for extension in sorted(TEXT_EXTENSIONS)]
    formats += [{"extension": "docx", "format": "docx", "ready": True},
                {"extension": "xlsx", "format": "xlsx", "ready": True},
                {"extension": "pdf", "format": "pdf", "ready": pdf_ready}]
    return {"formats": formats, "limits": {"input_bytes": MAX_INPUT_BYTES, "output_chars": MAX_OUTPUT_CHARS},
            "pdf": {"ready": pdf_ready, "reason": "Ready; text extraction only, no OCR." if pdf_ready
                    else "Optional pypdf package and a supported worker platform are required."}}


class _Text:
    def __init__(self):
        self.parts: list[str] = []
        self.size = 0
        self.truncated = False

    def add(self, value: str) -> bool:
        value = "".join(char for char in value if ord(char) >= 32 or char in "\t\n\r")
        prefix = "\n" if self.parts else ""
        remaining = MAX_OUTPUT_CHARS - self.size
        text = prefix + value
        if len(text) > remaining:
            self.parts.append(text[:remaining])
            self.size = MAX_OUTPUT_CHARS
            self.truncated = True
            return False
        self.parts.append(text)
        self.size += len(text)
        return True

    def finish(self) -> tuple[str, list[str]]:
        return "".join(self.parts), ["Text was truncated at the 500,000-character limit."] if self.truncated else []


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("Text documents must use UTF-8 or BOM-marked UTF-16 encoding.") from None
    if "\x00" in text or any(ord(char) < 32 and char not in "\t\n\r\f" for char in text):
        raise ValueError("The document contains binary control characters instead of supported text.")
    return text


def _xml(data: bytes) -> ET.Element:
    if len(data) > MAX_XML_BYTES:
        raise ValueError("Office XML part exceeds the 8 MB limit.")
    compact = data.replace(b"\x00", b"").lower()
    if b"<!doctype" in compact or b"<!entity" in compact:
        raise ValueError("XML entity declarations and document types are unsupported.")
    root = None
    depth = count = 0
    try:
        for event, element in ET.iterparse(io.BytesIO(data), events=("start", "end")):
            if event == "start":
                count += 1
                depth += 1
                if count > MAX_XML_NODES or depth > 64:
                    raise ValueError("Office XML exceeds the node/depth limit.")
                if root is None:
                    root = element
            else:
                depth -= 1
    except ET.ParseError:
        raise ValueError("The Office document contains malformed XML.") from None
    if root is None:
        raise ValueError("The Office document contains an empty XML part.")
    return root


def _archive(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("The Office document is malformed or uses unsupported encryption.") from None
    try:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_MEMBERS or sum(entry.file_size for entry in entries) > MAX_ZIP_BYTES:
            raise ValueError("Office archive exceeds the member/decompression limits.")
        names = set()
        for entry in entries:
            name = entry.filename
            parts = name.rstrip("/").split("/")
            if (not name or name.startswith("/") or "\\" in name or ":" in name or "\x00" in name
                    or PureWindowsPath(name).drive or any(part in {"", ".", ".."} for part in parts)):
                raise ValueError("Office archive contains an unsafe member path.")
            if name.casefold() in names:
                raise ValueError("Office archive contains duplicate member paths.")
            names.add(name.casefold())
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.flag_bits & 1:
                raise ValueError("Office archives must not contain linked or encrypted members.")
            if entry.file_size > MAX_XML_BYTES:
                raise ValueError("Office archive member exceeds the 8 MB limit.")
        return archive
    except Exception:
        archive.close()
        raise


def _read_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return _xml(archive.read(name))
    except KeyError:
        raise ValueError("The Office document is missing a required XML part.") from None
    except (zipfile.BadZipFile, RuntimeError, OSError):
        raise ValueError("The Office archive could not be decompressed safely.") from None


def _docx(data: bytes) -> tuple[str, list[str]]:
    output = _Text()
    warnings = []
    with _archive(data) as archive:
        names = archive.namelist()
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise ValueError("The archive is not a DOCX document.")
        parts = ["word/document.xml"] + sorted(name for name in names if re.fullmatch(r"word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml", name))
        for name in parts:
            root = _read_xml(archive, name)
            if name == "word/document.xml" and root.tag != W + "document":
                raise ValueError("The DOCX main part has an unsupported document type.")
            for paragraph in root.iter(W + "p"):
                values = []
                for element in paragraph.iter():
                    if element.tag == W + "t": values.append(element.text or "")
                    elif element.tag == W + "tab": values.append("\t")
                    elif element.tag in {W + "br", W + "cr"}: values.append("\n")
                if values and not output.add("".join(values)):
                    break
            if output.truncated:
                break
        if any(name.startswith("word/media/") for name in names):
            warnings.append("Embedded images were not read; OCR is unavailable.")
        if any(name.endswith("vbaProject.bin") for name in names):
            warnings.append("Embedded macros were ignored and were not executed.")
    text, truncated = output.finish()
    return text, warnings + truncated


def _xlsx(data: bytes) -> tuple[str, list[str]]:
    output = _Text()
    warnings = ["Spreadsheet formatting is omitted; numbers/dates use stored values. Formulas are not evaluated."]
    with _archive(data) as archive:
        workbook = _read_xml(archive, "xl/workbook.xml")
        if workbook.tag != S + "workbook":
            raise ValueError("The XLSX main part has an unsupported workbook type.")
        relations = _read_xml(archive, "xl/_rels/workbook.xml.rels")
        relationships = {entry.get("Id"): entry for entry in relations}
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = _read_xml(archive, "xl/sharedStrings.xml")
            if shared_root.tag != S + "sst":
                raise ValueError("The spreadsheet shared-string part is malformed.")
            total = 0
            for item in shared_root.findall(S + "si"):
                value = "".join(element.text or "" for element in item.iter(S + "t"))
                total += len(value)
                if total > 2_000_000 or len(shared) >= 100_000:
                    raise ValueError("Spreadsheet shared strings exceed the extraction limit.")
                shared.append(value)
        sheets = list(workbook.iter(S + "sheet"))
        if len(sheets) > 50:
            raise ValueError("Spreadsheet exceeds the 50-sheet limit.")
        total_rows = total_cells = 0
        formula_missing = False
        for sheet in sheets:
            relation = relationships.get(sheet.get(R + "id"))
            if relation is None:
                raise ValueError("Spreadsheet worksheet relationship is missing.")
            if relation.get("TargetMode") == "External":
                warnings.append("An external worksheet was skipped; no external resources were fetched.")
                continue
            target = urllib.parse.unquote(relation.get("Target", ""))
            if not target or "\\" in target or urllib.parse.urlsplit(target).scheme:
                raise ValueError("Spreadsheet contains an unsafe worksheet relationship.")
            target = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
            if not target.startswith("xl/") or target.startswith("xl/../"):
                raise ValueError("Spreadsheet worksheet escapes the package directory.")
            root = _read_xml(archive, target)
            if root.tag != S + "worksheet":
                raise ValueError("The spreadsheet relationship does not identify a worksheet.")
            if not output.add("--- Sheet: " + (sheet.get("name") or "Untitled")[:200] + " ---"):
                break
            for row in root.iter(S + "row"):
                total_rows += 1
                if total_rows > 5_000:
                    warnings.append("Spreadsheet was truncated at the 5,000-row limit.")
                    text, truncated = output.finish()
                    return text, warnings + truncated
                values = []
                for cell in row.findall(S + "c"):
                    total_cells += 1
                    if total_cells > 100_000:
                        raise ValueError("Spreadsheet exceeds the 100,000-cell limit.")
                    reference = cell.get("r", "")
                    match = re.fullmatch(r"([A-Z]{1,3})[1-9][0-9]{0,6}", reference)
                    column = len(values)
                    if match:
                        column = 0
                        for char in match.group(1):
                            column = column * 26 + ord(char) - ord("A") + 1
                        column -= 1
                    if column >= 200:
                        if "Only the first 200 spreadsheet columns are included." not in warnings:
                            warnings.append("Only the first 200 spreadsheet columns are included.")
                        continue
                    while len(values) <= column:
                        values.append("")
                    kind = cell.get("t")
                    stored = cell.findtext(S + "v")
                    if kind == "inlineStr":
                        value = "".join(element.text or "" for element in cell.iter(S + "t"))
                    elif kind == "s":
                        try:
                            index = int(stored or "")
                            if index < 0: raise ValueError()
                            value = shared[index]
                        except (ValueError, IndexError):
                            raise ValueError("Spreadsheet shared-string reference is invalid.") from None
                    elif kind == "b":
                        value = "TRUE" if stored == "1" else "FALSE"
                    elif cell.find(S + "f") is not None and (stored is None or stored == "" and kind != "str"):
                        value = "[formula result unavailable]"
                        formula_missing = True
                    else:
                        value = stored or ""
                    values[column] = value[:10_000]
                    if len(value) > 10_000 and "Long spreadsheet cells were truncated at 10,000 characters." not in warnings:
                        warnings.append("Long spreadsheet cells were truncated at 10,000 characters.")
                if not output.add(" | ".join(values)):
                    break
            if output.truncated:
                break
        if formula_missing:
            warnings.append("Some formulas have no cached result; their results are marked unavailable.")
        if any(name.startswith("xl/media/") for name in archive.namelist()):
            warnings.append("Embedded images were not read; OCR is unavailable.")
    text, truncated = output.finish()
    return text, warnings + truncated


def _pdf(data: bytes) -> tuple[str, list[str]]:
    if not document_capabilities()["pdf"]["ready"]:
        raise ValueError("PDF extraction is unavailable; install the pinned optional pypdf package in Ariadne's environment.")
    command = [sys.executable, "-I", "-B", str(Path(__file__).absolute()), "--pdf-worker"]
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        raise ValueError("The bounded PDF extraction worker could not be started.") from None
    output = bytearray()
    overflow = threading.Event()

    def feed():
        try:
            process.stdin.write(data)
        except (OSError, BrokenPipeError):
            pass
        finally:
            process.stdin.close()

    def collect():
        while True:
            chunk = process.stdout.read1(65_536)
            if not chunk:
                return
            remaining = MAX_PDF_RESPONSE - len(output)
            output.extend(chunk[:max(0, remaining)])
            if len(chunk) > remaining:
                overflow.set()
                process.kill()
                return

    writer = threading.Thread(target=feed, daemon=True)
    reader = threading.Thread(target=collect, daemon=True)
    writer.start()
    reader.start()
    try:
        try:
            process.wait(timeout=PDF_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise ValueError("PDF extraction exceeded its 30-second time limit.") from None
        writer.join(timeout=2)
        reader.join(timeout=2)
        if overflow.is_set() or reader.is_alive() or process.returncode:
            raise ValueError("PDF extraction failed within its memory/output safety limits.")
        try:
            result = json.loads(output)
        except (ValueError, UnicodeError):
            raise ValueError("PDF extraction worker returned an invalid result.") from None
        if result.get("error"):
            raise ValueError(result["error"])
        if not isinstance(result.get("text"), str) or len(result["text"]) > MAX_OUTPUT_CHARS:
            raise ValueError("PDF extraction worker exceeded the text limit.")
        return result["text"], result.get("warnings", [])
    finally:
        if process.poll() is None:
            process.kill()
        process.stdout.close()


def extract_document(filename: str, data: bytes, *, ocr_config=None, ocr=False) -> dict:
    if not isinstance(filename, str) or not filename or len(filename) > 1024 or "\x00" in filename:
        raise ValueError("A valid document filename is required.")
    if not isinstance(data, bytes) or len(data) > MAX_INPUT_BYTES:
        raise ValueError("Documents must contain bytes and must not exceed 10 MB.")
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower().lstrip(".")
    if type(ocr) is not bool:
        raise ValueError("ocr must be a boolean.")
    if extension in {"png", "jpg", "jpeg"} or ocr:
        if ocr_config is None:
            raise ValueError("Local OCR needs explicit provisioning before use.")
        from .ocr import extract_ocr
        return extract_ocr(PurePosixPath(filename.replace("\\", "/")).name, data, ocr_config)
    warnings = []
    try:
        if extension in TEXT_EXTENSIONS:
            text = _decode(data)
            if extension == "json":
                try:
                    json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
                except (ValueError, RecursionError):
                    raise ValueError("The JSON document is malformed or exceeds the nesting limit.") from None
            if extension in {"csv", "tsv"}:
                output = _Text()
                for number, row in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter="\t" if extension == "tsv" else ",", strict=True)):
                    if number >= 5_000:
                        warnings.append("Delimited text was truncated at the 5,000-row limit.")
                        break
                    if len(row) > 200:
                        raise ValueError("Delimited document exceeds the 200-column limit.")
                    if not output.add(" | ".join(row)):
                        break
                text, truncated = output.finish()
                warnings += truncated
            elif len(text) > MAX_OUTPUT_CHARS:
                text = text[:MAX_OUTPUT_CHARS]
                warnings.append("Text was truncated at the 500,000-character limit.")
            format_name = extension if extension in {"json", "csv", "tsv"} else "text"
        elif extension == "docx":
            text, warnings = _docx(data)
            format_name = "docx"
        elif extension == "xlsx":
            text, warnings = _xlsx(data)
            format_name = "xlsx"
        elif extension == "pdf":
            text, warnings = _pdf(data)
            format_name = "pdf"
        else:
            raise ValueError("Unsupported document type. Use text, JSON, CSV, TSV, DOCX, XLSX, or optional PDF.")
    except csv.Error:
        raise ValueError("The delimited document is malformed or contains an oversized field.") from None
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError, RecursionError):
        raise ValueError("The document is malformed or exceeds its extraction limits.") from None
    if not text.strip():
        warnings.append("No readable text was found. For scanned images/pages, select local OCR after provisioning it.")
    return {"text": text, "format": format_name, "warnings": warnings,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "processor": {"name": "document-text", "identity": "text-v1"}}


def _limit_pdf_worker() -> None:
    """Apply an OS memory ceiling before importing the optional PDF parser."""
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (PDF_MEMORY_BYTES, PDF_MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (PDF_TIMEOUT_SECONDS, PDF_TIMEOUT_SECONDS))
        return
    if os.name != "nt":
        raise ValueError("This platform cannot provide the required PDF worker memory limit.")
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
                    ("minimum_working_set", ctypes.c_size_t), ("maximum_working_set", ctypes.c_size_t),
                    ("active_processes", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("counters", Counters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x100 | 0x2000 | 0x8 | 0x2  # memory, kill on close, one process, user CPU time
    limits.basic.process_time = int(PDF_TIMEOUT_SECONDS * 10_000_000)
    limits.basic.active_processes = 1
    limits.process_memory = PDF_MEMORY_BYTES
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        if handle:
            kernel.CloseHandle(handle)
        raise ValueError("The PDF worker could not establish its operating-system memory limit.")
    # Keep the handle open for the worker's entire life. The OS closes it on exit.
    globals()["_PDF_JOB_HANDLE"] = handle


def _pdf_worker() -> int:
    try:
        _limit_pdf_worker()
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError("PDF exceeds the 10 MB limit.")
        output = _Text()
        warnings = []
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDFs are unsupported; provide an unencrypted copy.")
            if len(reader.pages) > MAX_PDF_PAGES:
                warnings.append("PDF was truncated at the 100-page limit.")
            for number, page in enumerate(reader.pages):
                if number >= MAX_PDF_PAGES or not output.add("--- Page " + str(number + 1) + " ---\n" + (page.extract_text() or "")):
                    break
        text, truncated = output.finish()
        warnings += truncated
        if not any(char.isalnum() for part in output.parts for char in part.split("---")[-1]):
            warnings.append("No extractable PDF text was found; OCR is unavailable.")
        result = {"text": text, "warnings": warnings}
    except ValueError as error:
        result = {"error": str(error)}
    except (Exception, MemoryError):
        result = {"error": "PDF is malformed, unsupported, or exceeds the worker memory limit."}
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_PDF_RESPONSE:
        encoded = b'{"error":"PDF output exceeded its safety limit."}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--pdf-worker"]:
        raise SystemExit(_pdf_worker())
    raise SystemExit("This module is an internal document extractor.")
