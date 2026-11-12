"""Constrained local tools. Model-supplied text never becomes a host command."""

from __future__ import annotations

import ast
import contextlib
import http.client
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PureWindowsPath
from typing import Any

from .config import Settings


MAX_TEXT = 100_000
MAX_RESPONSE = 65_536
_REPARSE_POINT = 0x400
_SECRET_KEY = re.compile(r"(?:authorization|password|passwd|secret|api[_-]?key|token|cookie)", re.I)


class ToolError(ValueError):
    """A safe, user-facing tool validation error."""


def _object(value: Any, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ToolError("Arguments must be a JSON object.")
    if set(value) - allowed:
        raise ToolError("Unexpected tool arguments.")
    if required - set(value):
        raise ToolError("Missing required tool arguments.")
    return value


def _text(value: Any, label: str, limit: int = MAX_TEXT, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not value and not empty) or "\x00" in value:
        raise ToolError(f"{label} must be text of at most {limit} characters.")
    return value


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ToolError(f"{label} must be an integer between {low} and {high}.")
    return value


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required or [], "additionalProperties": False}}}


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any) -> None:
        return None


@contextlib.contextmanager
def _bounded_opener(timeout: int):
    """Bound slow response bodies as well as individual socket operations."""
    sockets: list[socket.socket] = []
    lock = threading.Lock()
    expired = threading.Event()

    def close_sockets() -> None:
        with lock:
            expired.set()
            for stream in sockets:
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                stream.close()

    def register(stream: socket.socket) -> None:
        with lock:
            if expired.is_set():
                stream.close()
                raise TimeoutError("Connector request deadline exceeded.")
            sockets.append(stream)

    class HTTPConnection(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            register(self.sock)

    class HTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            super().connect()
            register(self.sock)

    class HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(HTTPConnection, request)

    class HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            # HTTPSConnection's default context verifies certificates and names.
            return self.do_open(HTTPSConnection, request)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects(), HTTPHandler(), HTTPSHandler())
    timer = threading.Timer(timeout, close_sockets)
    timer.daemon = True
    timer.start()
    try:
        yield opener, expired
    finally:
        timer.cancel()
        close_sockets()


class ToolRegistry:
    """Workspace and explicitly configured HTTP capabilities.

    ``policy`` is advisory to the agent engine: the engine must obtain approval
    before invoking tools marked ``approval``. ``execute`` still validates every
    argument, including calls made directly outside the model loop.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.workspace.absolute()
        self._lock = threading.RLock()
        self._check_ancestors(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._check_ancestors(self.root)
        self._connectors = self._load_connectors()
        from .data_tools import DataTools
        self.data_tools = DataTools(self)
        from .analytics import AnalyticsTools
        from .mcp_tools import MCPTools
        self.analytics = AnalyticsTools(self)
        self.mcp = MCPTools(settings)
        from .production_tools import ProductionTools, NAMES as production_names
        from .knowledge_tools import KnowledgeTools, NAMES as knowledge_names
        from .media_tools import MediaTools, NAMES as media_names
        from .automation_tools import AutomationTools, NAMES as automation_names
        from .business_tools import BusinessTools, NAMES as business_names
        from .survival_tools import SurvivalTools, NAMES as survival_names
        from .survival_library import SurvivalLibrary, NAMES as reference_names
        from .procedure_tools import ProcedureTools, NAMES as procedure_names
        from .business_ops import BusinessOps, NAMES as business_ops_names
        from .market_research import MarketResearchTools, NAMES as market_names
        from .etf_tools import ETFResearchTools, NAMES as etf_names
        from .local_controls import LocalControls, NAMES as control_names
        from .service_desk_tools import ServiceDeskReadTools, NAMES as service_desk_names
        from .workroom_tools import WorkroomReadTools, NAMES as workroom_names
        from .perception_tools import PerceptionReadTools, NAMES as perception_names
        from .vision_tools import VisionReadTools, NAMES as vision_names
        self.production = ProductionTools(self)
        self.knowledge = KnowledgeTools(self)
        self.media = MediaTools(self)
        self.automation = AutomationTools(self)
        self.business = BusinessTools(self)
        self.survival = SurvivalTools(self)
        self.references = SurvivalLibrary(self)
        self.procedures = ProcedureTools(self)
        self.business_ops = BusinessOps(self)
        self.market_research = MarketResearchTools(self)
        self.etf_research = ETFResearchTools(self)
        self.local_controls = LocalControls(self)
        self.service_desk = ServiceDeskReadTools(self)
        self.workroom = WorkroomReadTools(self)
        self.perception = PerceptionReadTools(self)
        self.vision = VisionReadTools(self)
        self.extensions = (self.production, self.knowledge, self.media, self.automation, self.business, self.survival, self.references, self.procedures, self.business_ops, self.market_research, self.etf_research, self.local_controls, self.service_desk, self.workroom, self.perception, self.vision)
        self._extension_names = {}
        for provider, names in zip(self.extensions, (production_names, knowledge_names, media_names, automation_names, business_names, survival_names, reference_names, procedure_names, business_ops_names, market_names, etf_names, control_names, service_desk_names, workroom_names, perception_names, vision_names)):
            for name in names:
                if name in self._extension_names:
                    raise ToolError("Duplicate extension tool name.")
                self._extension_names[name] = provider

    @staticmethod
    def _check_ancestors(path: Path) -> None:
        for component in reversed((path, *path.parents)):
            try:
                info = component.lstat()
            except FileNotFoundError:
                continue
            if _linked(info):
                raise ToolError("Symbolic links and reparse points are not permitted in workspace paths.")

    def _parts(self, value: Any, *, root_ok: bool = False) -> tuple[str, ...]:
        raw = _text(value, "path", 1024)
        win_path = PureWindowsPath(raw)
        if raw.startswith(("/", "\\")) or win_path.drive or ":" in raw:
            raise ToolError("Use a relative path inside the workspace.")
        parts = tuple(raw.replace("\\", "/").split("/"))
        if root_ok and raw in {".", "./"}:
            return ()
        if any(not part or part in {".", ".."} or part.endswith((" ", ".")) for part in parts):
            raise ToolError("Empty, dot, parent, or ambiguous path components are not permitted.")
        for part in parts:
            if any(ord(char) < 32 for char in part) or any(char in '<>"|?*' for char in part):
                raise ToolError("Path contains unsupported characters.")
            base = part.split(".", 1)[0].upper()
            if base in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[0-9¹²³]", base):
                raise ToolError("Windows device names are not permitted.")
        return parts

    def _path(self, value: Any, *, root_ok: bool = False) -> Path:
        path = self.root.joinpath(*self._parts(value, root_ok=root_ok))
        self._check_ancestors(path)
        try:
            path.resolve(strict=False).relative_to(self.root.resolve(strict=True))
        except (ValueError, OSError):
            raise ToolError("Path must remain inside the workspace.") from None
        return path

    @contextlib.contextmanager
    def _directory(self, path: Path, *, create: bool = False):
        """Hold directory handles while accessing files, preventing link swaps.

        POSIX operations use directory-relative descriptors with O_NOFOLLOW.
        Windows holds each directory without FILE_SHARE_DELETE, which prevents
        replacement while a child is opened or atomically committed.
        """
        self._check_ancestors(path)
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel.CreateFileW
            create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            create_file.restype = wintypes.HANDLE
            close_handle = kernel.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            class FileInfo(ctypes.Structure):
                _fields_ = [("attributes", wintypes.DWORD), ("creation", wintypes.FILETIME),
                            ("access", wintypes.FILETIME), ("write", wintypes.FILETIME),
                            ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                            ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                            ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]
            get_info = kernel.GetFileInformationByHandle
            get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
            get_info.restype = wintypes.BOOL
            handles = []
            try:
                for component in reversed((path, *path.parents)):
                    if not component.exists() and create:
                        component.mkdir()
                    # Metadata-only access (0 / FILE_READ_ATTRIBUTES) does not
                    # block directory rename on Windows. FILE_LIST_DIRECTORY
                    # makes the omitted FILE_SHARE_DELETE an effective pin.
                    handle = create_file(str(component), 1, 1 | 2, None, 3, 0x02000000 | 0x00200000, None)
                    if handle == ctypes.c_void_p(-1).value:
                        raise OSError(ctypes.get_last_error(), "Cannot open workspace directory.")
                    handles.append(handle)
                    info = FileInfo()
                    if not get_info(handle, ctypes.byref(info)) or info.attributes & _REPARSE_POINT or not info.attributes & 0x10:
                        raise ToolError("Workspace directories must not be links or reparse points.")
                yield None
            finally:
                for handle in reversed(handles):
                    close_handle(handle)
        else:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path.anchor, flags)
            try:
                for part in path.parts[1:]:
                    if create:
                        try:
                            os.mkdir(part, mode=0o700, dir_fd=fd)
                        except FileExistsError:
                            pass
                    child = os.open(part, flags, dir_fd=fd)
                    os.close(fd)
                    fd = child
                yield fd
            finally:
                os.close(fd)

    def _open_read(self, path: Path, directory_fd: int | None) -> int:
        if os.name == "nt":
            # Parent handles are held by _directory; O_NOINHERIT prevents any
            # descriptor from accidentally reaching a launched process.
            self._check_ancestors(path)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
            fd = os.open(path, flags)
        else:
            fd = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=directory_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            os.close(fd)
            raise ToolError("Only regular files can be read.")
        if os.name == "nt":
            # A final-component reparse point could have been swapped between
            # the path check and open. Check the opened handle before reading.
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            get_final = kernel.GetFinalPathNameByHandleW
            get_final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            get_final.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            count = get_final(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
            expected = os.path.normcase(str(path.absolute()))
            actual = buffer.value.removeprefix("\\\\?\\")
            if not count or count >= len(buffer) or os.path.normcase(actual) != expected:
                os.close(fd)
                raise ToolError("File path changed while it was being opened.")
            try:
                self._check_ancestors(path)
            except Exception:
                os.close(fd)
                raise
        return fd

    def schemas(self) -> list[dict]:
        result = [
            _schema("capability_find", "Discover installed optional tools by desired ability, such as CAD, speech, optimization, documents, code, browser or business records. Matching tools become available on the next step. This grants no permissions.",
                    {"query": {"type": "string", "maxLength": 500}}, ["query"]),
            _schema("file_list", "List regular files and folders inside the workspace. Links are omitted.",
                    {"path": {"type": "string", "default": "."}, "max_entries": {"type": "integer", "minimum": 1, "maximum": 500}}),
            _schema("file_read", "Read a bounded UTF-8 text file inside the workspace.",
                    {"path": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 1, "maximum": MAX_TEXT}}, ["path"]),
            _schema("file_write", "Atomically save UTF-8 text inside the workspace. Existing files require overwrite=true.",
                    {"path": {"type": "string"}, "content": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}}, ["path", "content"]),
            _schema("workspace_search", "Search UTF-8 workspace text for a literal string; bounded file and byte scan.",
                    {"query": {"type": "string"}, "path": {"type": "string", "default": "."},
                     "max_results": {"type": "integer", "minimum": 1, "maximum": 100}}, ["query"]),
            _schema("calculate", "Evaluate arithmetic using numbers and + - * / // % ** only.",
                    {"expression": {"type": "string"}}, ["expression"]),
        ]
        if self.settings.enable_docker:
            result.append(_schema("run_python", "Run a workspace Python script in an isolated, resource-limited Docker container; approval required. Docker image must already be installed.",
                          {"path": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
                           "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60}}, ["path"]))
        for tool_name, connector in self._connectors.items():
            properties = {} if connector["method"] == "GET" else {"body": {"type": "object", "description": "JSON request body. Never include credentials."}}
            result.append(_schema(tool_name, connector["description"], properties))
        return (result + self.data_tools.schemas() + self.analytics.schemas() + self.mcp.schemas()
                + [schema for provider in self.extensions for schema in provider.schemas()])

    def schemas_for(self, prompt, context):
        """Deterministic discovery avoids sending every optional schema per call."""
        text = (prompt + " " + json.dumps(context[-8:], ensure_ascii=False)).casefold()
        keywords = {
            "production_optimize": ("optimiz", "assign", "schedule", "route", "routing", "packing"),
            "production_math": ("algebra", "symbolic", "differentiat", "integrat", "equation", "calculus"),
            "office_create": ("office", "report", "document", "docx", "xlsx", "pptx", "presentation", "spreadsheet", "pdf"),
            "geo_analyze": ("geojson", "geograph", "map", "spatial"),
            "cad_part": ("cad", "mechanical", "enclosure", "step file", "stl", "part", "geometry"),
            "render_product": ("blender", "render", "product image", "3d"),
            "pcb_manufacture": ("pcb", "circuit", "electronics", "kicad", "gerber"),
            "gis_buffer": ("qgis", "geograph", "spatial", "buffer"),
            "media_transform": ("ffmpeg", "convert", "resample", "thumbnail", "video", "audio", "clip"),
            "hybrid_search": ("semantic", "knowledge", "reference", "search", "memory"),
            "knowledge_reindex": ("reindex", "semantic index", "index knowledge"),
            "document_structure": ("docling", "extract", "document", "pdf", "reading order"),
            "library_search": ("library", "kiwix", "encyclopedia", "reference", "wikipedia"),
            "library_read": ("library", "kiwix", "reference", "wikipedia", "article"),
            "code_transform": ("python", "code", "transform", "monty", "program"),
            "media_inspect_image": ("image", "photo", "picture", "vision", "screenshot", "diagram"),
            "media_transcribe": ("transcrib", "recording", "speech", "audio", "voice"),
            "media_vad": ("vad", "speech", "audio", "voice"),
            "media_diarize": ("diariz", "speaker", "meeting"),
            "media_speak": ("speak", "tts", "voice", "speech", "narrat"),
            "media_generate_image": ("generate image", "create image", "flux", "illustrat", "image generation"),
            "business_records": ("business", "inventory", "stock", "contact", "crm", "ledger", "journal", "trial balance"),
            "business_ops": ("business", "invoice", "quote", "erp", "cash", "stock", "reorder"),
            "service_desk_read": ("service desk", "intake", "quote", "review", "needs attention", "customer"),
            "workroom_read": ("project", "workroom", "dependency", "handoff", "next task"),
            "market_research": ("trading", "backtest", "market data", "ohlcv", "walk-forward", "strategy", "openbb", "holdout"),
            "etf_research": ("etf", "swing", "position", "momentum", "monthly", "nav", "holdout", "backtest"),
            "home_control": ("home assistant", "device", "light", "switch", "scene", "local control", "telemetry"),
            "survival_plan": ("survival", "prepper", "preparedness", "water", "rations", "battery", "power budget", "off-grid", "rebuild", "dartnell"),
            "survival_records": ("survival", "prepper", "preparedness", "supply", "supplies", "expiry", "stockpile", "rebuild", "dartnell"),
            "survival_reference": ("survival", "prepper", "preparedness", "reference", "book", "civilization", "rebuild", "dartnell", "water safety"),
            "local_procedure": ("workflow", "procedure", "report", "readiness", "rotation", "power budget", "snapshot", "audit", "inventory", "checklist"),
            "browser_task": ("browser", "web app", "webpage", "website", "playwright", "form", "ui test"),
            "local_flow": ("node-red", "workflow", "automation", "mqtt", "device", "local flow"),
            "erpnext_read": ("erpnext", "procurement", "invoice", "customer", "supplier"),
        }
        return [schema for schema in self.schemas()
                if schema["function"]["name"] not in self._extension_names
                or schema["function"]["name"].casefold() in text
                or any(word in text for word in keywords.get(schema["function"]["name"], ()))]

    def policy(self, name: str, args: dict) -> str:
        if name in self._extension_names:
            return self._extension_names[name].policy(name, args)
        if name.startswith("mcp_"):
            return self.mcp.policy(name, args)
        if name == "analytics_query":
            return "auto"
        if name in {"table_read", "table_write", "table_query"}:
            return "auto"
        if name == "run_python":
            return "approval"
        if name in self._connectors:
            return "auto" if self._connectors[name]["method"] == "GET" and self._connectors[name]["read_only"] else "approval"
        if name in {"file_list", "file_read", "file_write", "workspace_search", "calculate", "capability_find"}:
            return "auto"
        raise ToolError("Unknown tool.")

    @staticmethod
    def _snapshot_digest(snapshot: Path) -> str:
        records = []
        for path in sorted(snapshot.rglob("*")):
            relative = path.relative_to(snapshot).as_posix()
            records.append(["directory", relative] if path.is_dir() else ["file", relative, hashlib.sha256(path.read_bytes()).hexdigest()])
        return hashlib.sha256(json.dumps(records, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

    def fingerprint(self, name: str, args: dict, snapshot_digest=None) -> str:
        """Bind approval to current capability, route, credential scope and input."""
        from . import __version__
        schema = next((s for s in self.schemas() if s["function"]["name"] == name), None)
        material = {"version": __version__, "name": name, "schema": schema, "args": args, "policy": self.policy(name, args)}
        provider = self._extension_names.get(name)
        if provider is not None and hasattr(provider, "fingerprint_material"):
            material["extension"] = provider.fingerprint_material(name, args)
        if name.startswith("mcp_"):
            material["mcp"] = self.mcp.fingerprint_material(name)
        if name in self._connectors:
            connector = self._connectors[name]
            headers = {key: os.environ.get(value["env"], "") if isinstance(value, dict) else value for key, value in connector["headers"].items()}
            material["connector"] = {**connector, "resolved_headers": headers}
        if name == "run_python":
            if snapshot_digest is None:
                with self._lock, tempfile.TemporaryDirectory(prefix=".approval-inputs-", dir=self.settings.data_dir) as temporary:
                    self._snapshot_workspace(Path(temporary))
                    snapshot_digest = self._snapshot_digest(Path(temporary))
            material["runner"] = {"image": self.settings.docker_image, "enabled": self.settings.enable_docker, "snapshot_sha256": snapshot_digest}
        payload = json.dumps(material, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        # Keyed digest does not expose reusable hashes of short secret values.
        return hmac.new(self.settings.api_token.encode() or b"ariadne-unpaired-test", payload, hashlib.sha256).hexdigest()

    def approval_description(self, name, args):
        if name in self._extension_names:
            provider = self._extension_names[name]
            material = provider.fingerprint_material(name, args) if hasattr(provider, "fingerprint_material") else {}
            return {"tool": name, "scope": "fixed local capability", "inputs": args,
                    "configuration_sha256": hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest(),
                    "binding": "Approval binds the arguments, current capability configuration and input identities. It grants no unrelated actions."}
        if name.startswith("mcp_"):
            description = next(d for d in self.mcp.descriptions() if d["name"] == name)
            return {"tool": name, **description,
                    "binding": "Approval binds this server, selected tool metadata, credential scope and arguments. Remote outcomes can require reconciliation."}
        if name in self._connectors:
            entry = self._connectors[name]
            parsed = urllib.parse.urlsplit(entry["url"])
            return {"tool": name, "method": entry["method"], "destination": urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")),
                    "query_values_hidden": bool(parsed.query), "credential_sources": sorted(v["env"] for v in entry["headers"].values() if isinstance(v, dict))}
        if name == "run_python":
            preview = self.execute("file_read", {"path": args.get("path"), "max_chars": 20000})
            return {"tool": name, "image": self.settings.docker_image, "network": "none", "host_workspace": "read-only input snapshot",
                    "script_preview": preview.get("content", "Unable to preview script"), "preview_truncated": preview.get("truncated", False),
                    "binding": "The fingerprint binds all snapshot file bytes; changed inputs require review again."}
        return {"tool": name}

    def execute(self, name: str, args: dict, *, approval_fingerprint=None, cancelled=None) -> dict:
        try:
            if name == "capability_find":
                _object(args, {"query"}, {"query"})
                query = _text(args["query"], "query", 500).casefold()
                terms = re.findall(r"[a-z0-9_]+", query)[:24]
                scored = []
                for schema in self.schemas():
                    function = schema["function"]
                    if function["name"] in self._extension_names:
                        haystack = (function["name"] + " " + function["description"]).casefold()
                        score = sum(term in haystack for term in terms)
                        if score:
                            scored.append((score, function["name"], function["description"]))
                return {"ok": True, "tools": [{"name": name, "description": description} for _, name, description in sorted(scored, key=lambda x: (-x[0], x[1]))[:8]],
                        "notice": "Installed matching tools are supplied on the next model step. Existing approval requirements apply."}
            if name in self._extension_names:
                with self._lock:
                    if approval_fingerprint is not None and not hmac.compare_digest(approval_fingerprint, self.fingerprint(name, args)):
                        return {"ok": False, "error": "Local capability or approved inputs changed. Review a new action."}
                    if name == "home_control":
                        return self._extension_names[name].execute(name, args, cancelled=cancelled,
                                                                  approval_fingerprint=approval_fingerprint)
                    return self._extension_names[name].execute(name, args, cancelled=cancelled)
            if name == "analytics_query":
                return self.analytics.execute(name, args, cancelled=cancelled)
            if name.startswith("mcp_"):
                with self.mcp.mutation():
                    if approval_fingerprint is not None and not hmac.compare_digest(approval_fingerprint, self.fingerprint(name, args)):
                        return {"ok": False, "error": "MCP approval inputs changed before execution. Review a new action."}
                    return self.mcp.execute(name, args, cancelled=cancelled)
            if name in {"table_read", "table_write", "table_query"}:
                return self.data_tools.execute(name, args)
            if name in self._connectors:
                return self._request(self._connectors[name], args)
            if name == "run_python":
                with self._lock:
                    return self._run_python(args, approval_fingerprint)
            handler = {"file_list": self._file_list, "file_read": self._file_read, "file_write": self._file_write,
                       "workspace_search": self._workspace_search, "calculate": self._calculate,
                       }.get(name)
            if handler is None:
                raise ToolError("Unknown tool.")
            with self._lock:
                return handler(args)
        except ToolError as error:
            return {"ok": False, "error": str(error)}
        except UnicodeError:
            return {"ok": False, "error": "The file is not valid UTF-8 text."}
        except ValueError:
            return {"ok": False, "error": "Invalid capability arguments or configuration."}
        except FileNotFoundError:
            return {"ok": False, "error": "File or directory not found."}
        except FileExistsError:
            return {"ok": False, "error": "The file already exists; set overwrite=true to replace it."}
        except PermissionError:
            return {"ok": False, "error": "Access denied by the operating system."}
        except (OSError, OverflowError, ArithmeticError, RecursionError):
            return {"ok": False, "error": "The tool could not complete this operation."}

    def _file_list(self, args: dict) -> dict:
        args = _object(args, {"path", "max_entries"})
        path = self._path(args.get("path", "."), root_ok=True)
        limit = _integer(args.get("max_entries", 200), "max_entries", 1, 500)
        entries = []
        truncated = False
        with self._directory(path) as directory_fd:
            with os.scandir(path if directory_fd is None else directory_fd) as iterator:
                for entry in iterator:
                    info = entry.stat(follow_symlinks=False)
                    if _linked(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                        continue
                    if len(entries) >= limit:
                        truncated = True
                        break
                    entries.append({"name": entry.name, "type": "directory" if stat.S_ISDIR(info.st_mode) else "file", "size": info.st_size})
        return {"ok": True, "path": path.relative_to(self.root).as_posix(), "entries": sorted(entries, key=lambda item: item["name"].casefold()), "truncated": truncated}

    def _file_read(self, args: dict) -> dict:
        args = _object(args, {"path", "max_chars"}, {"path"})
        path = self._path(args["path"])
        limit = _integer(args.get("max_chars", 20_000), "max_chars", 1, MAX_TEXT)
        with self._directory(path.parent) as directory_fd:
            with os.fdopen(self._open_read(path, directory_fd), "r", encoding="utf-8") as source:
                content = source.read(limit + 1)
        return {"ok": True, "path": path.relative_to(self.root).as_posix(), "content": content[:limit], "truncated": len(content) > limit}

    def _file_write(self, args: dict) -> dict:
        args = _object(args, {"path", "content", "overwrite"}, {"path", "content"})
        path = self._path(args["path"])
        content = _text(args["content"], "content", empty=True)
        overwrite = args.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise ToolError("overwrite must be a boolean.")
        temporary = ".ariadne-" + secrets.token_hex(12) + ".tmp"
        encoded = content.encode("utf-8")
        with self._directory(path.parent, create=True) as directory_fd:
            self._check_ancestors(path)
            existing = path.lstat() if path.exists() else None
            if existing and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink > 1):
                raise ToolError("Only regular files with a single link can be replaced.")
            if existing and not overwrite:
                raise FileExistsError()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            temp_path = path.parent / temporary
            fd = os.open(temp_path if directory_fd is None else temporary, flags, 0o600,
                         **({} if directory_fd is None else {"dir_fd": directory_fd}))
            try:
                with os.fdopen(fd, "wb") as target:
                    target.write(encoded)
                    target.flush()
                    os.fsync(target.fileno())
                self._check_ancestors(path)
                if overwrite:
                    if directory_fd is None:
                        os.replace(temp_path, path)
                    else:
                        os.replace(temporary, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                else:
                    # Windows rename fails atomically when the destination
                    # exists. POSIX uses link for the same exclusive commit.
                    if directory_fd is None:
                        os.rename(temp_path, path)
                    else:
                        os.link(temporary, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
            finally:
                try:
                    if directory_fd is None:
                        temp_path.unlink()
                    else:
                        os.unlink(temporary, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
        return {"ok": True, "path": path.relative_to(self.root).as_posix(), "bytes": len(encoded)}

    def _workspace_search(self, args: dict) -> dict:
        args = _object(args, {"path", "query", "max_results"}, {"query"})
        query = _text(args["query"], "query", 200)
        start = self._path(args.get("path", "."), root_ok=True)
        limit = _integer(args.get("max_results", 30), "max_results", 1, 100)
        matches: list[dict] = []
        scanned_files = scanned_bytes = scanned_entries = 0
        truncated = False
        pending = [start]
        deadline = time.monotonic() + 3
        while pending:
            path = pending.pop()
            if scanned_files >= 500 or scanned_entries >= 2000 or scanned_bytes >= 4_000_000 or time.monotonic() >= deadline:
                truncated = True
                break
            self._check_ancestors(path)
            if path.is_dir():
                with self._directory(path) as directory_fd:
                    with os.scandir(path if directory_fd is None else directory_fd) as iterator:
                        for entry in iterator:
                            scanned_entries += 1
                            if scanned_entries > 2000:
                                truncated = True
                                break
                            info = entry.stat(follow_symlinks=False)
                            if not _linked(info) and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                                pending.append(path / entry.name)
                continue
            with self._directory(path.parent) as directory_fd:
                with os.fdopen(self._open_read(path, directory_fd), "rb") as source:
                    data = source.read(min(100_000, 4_000_000 - scanned_bytes) + 1)
            scanned_files += 1
            scanned_bytes += len(data)
            if len(data) > 100_000:
                truncated = True
                data = data[:100_000]
            if b"\x00" in data:
                continue
            try:
                content = data.decode("utf-8")
            except UnicodeError:
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": path.relative_to(self.root).as_posix(), "line": number, "text": line[:400]})
                    if len(matches) >= limit:
                        return {"ok": True, "matches": matches, "scanned_files": scanned_files, "truncated": True}
        return {"ok": True, "matches": matches, "scanned_files": scanned_files, "truncated": truncated}

    def _calculate(self, args: dict) -> dict:
        args = _object(args, {"expression"}, {"expression"})
        expression = _text(args["expression"], "expression", 300)
        try:
            tree = ast.parse(expression, mode="eval")
        except (SyntaxError, RecursionError):
            raise ToolError("Invalid arithmetic expression.") from None
        if sum(1 for _ in ast.walk(tree)) > 100:
            raise ToolError("Arithmetic expression is too complex.")

        def evaluate(node: ast.AST) -> int | float:
            if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
                value = node.value
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                operand = evaluate(node.operand)
                value = operand if isinstance(node.op, ast.UAdd) else -operand
            elif isinstance(node, ast.BinOp):
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Pow):
                    if abs(right) > 100 or (abs(left) > 1 and right > 0 and right * math.log10(abs(left)) > 100):
                        raise ToolError("Power exceeds the arithmetic size limit.")
                    value = left ** right
                elif isinstance(node.op, ast.Add): value = left + right
                elif isinstance(node.op, ast.Sub): value = left - right
                elif isinstance(node.op, ast.Mult): value = left * right
                elif isinstance(node.op, ast.Div): value = left / right
                elif isinstance(node.op, ast.FloorDiv): value = left // right
                elif isinstance(node.op, ast.Mod): value = left % right
                else: raise ToolError("Only basic arithmetic operators are supported.")
            else:
                raise ToolError("Only numbers and arithmetic operators are supported.")
            if type(value) not in {int, float} or not math.isfinite(value) or abs(value) > 10 ** 100:
                raise ToolError("Result exceeds the arithmetic size limit.")
            return value

        return {"ok": True, "result": evaluate(tree.body)}

    def _run_python(self, args: dict, approved_fingerprint=None) -> dict:
        if not self.settings.enable_docker:
            raise ToolError("Python execution is disabled. Enable Docker execution explicitly to use it.")
        args = _object(args, {"path", "args", "timeout_seconds"}, {"path"})
        path = self._path(args["path"])
        if path.suffix.lower() != ".py" or not path.is_file():
            raise ToolError("path must identify an existing workspace .py file.")
        arguments = args.get("args", [])
        if not isinstance(arguments, list) or len(arguments) > 16:
            raise ToolError("args must be an array of at most 16 strings.")
        arguments = [_text(arg, "argument", 500, empty=True) for arg in arguments]
        timeout = _integer(args.get("timeout_seconds", 20), "timeout_seconds", 1, 60)
        image = self.settings.docker_image
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}", image):
            raise ToolError("Configured Docker image name is invalid.")
        docker = shutil.which("docker")
        if not docker:
            raise ToolError("Docker is not installed or is not on PATH.")
        with tempfile.TemporaryDirectory(prefix=".runner-", dir=self.settings.data_dir) as temporary:
            snapshot = Path(temporary)
            self._snapshot_workspace(snapshot)
            if approved_fingerprint and not hmac.compare_digest(approved_fingerprint, self.fingerprint("run_python", args, self._snapshot_digest(snapshot))):
                raise ToolError("Runner inputs changed after approval. Nothing was executed; review the new inputs before retrying.")
            return self._execute_docker(docker, snapshot, path, arguments, timeout)

    def _snapshot_workspace(self, destination: Path) -> None:
        """Copy regular files into a small read-only runner input snapshot.

        The container sees this copy, never a writable host workspace. Uniform
        read permissions let its unprivileged user read files created as 0600.
        """
        pending = [self.root]
        scanned_entries = total_bytes = 0
        deadline = time.monotonic() + 5
        destination.chmod(0o755)
        while pending:
            directory = pending.pop()
            with self._directory(directory) as directory_fd:
                with os.scandir(directory if directory_fd is None else directory_fd) as entries:
                    for entry in entries:
                        scanned_entries += 1
                        if scanned_entries > 500 or time.monotonic() > deadline:
                            raise ToolError("Runner workspace snapshot exceeds 500 entries or the copy time limit.")
                        info = entry.stat(follow_symlinks=False)
                        source = directory / entry.name
                        target = destination / source.relative_to(self.root)
                        if _linked(info) or info.st_nlink > 1 and stat.S_ISREG(info.st_mode):
                            raise ToolError("Runner input must not contain linked files or directories.")
                        if stat.S_ISDIR(info.st_mode):
                            target.mkdir(mode=0o755)
                            pending.append(source)
                        elif stat.S_ISREG(info.st_mode):
                            remaining = 10_000_000 - total_bytes
                            if info.st_size > remaining:
                                raise ToolError("Runner workspace snapshot exceeds 10 MB.")
                            with os.fdopen(self._open_read(source, directory_fd), "rb") as content:
                                data = content.read(remaining + 1)
                            if len(data) > remaining:
                                raise ToolError("Runner workspace snapshot exceeds 10 MB.")
                            total_bytes += len(data)
                            with open(target, "xb") as output:
                                output.write(data)
                            target.chmod(0o444)
                        else:
                            raise ToolError("Runner input may contain only regular files and directories.")

    def _execute_docker(self, docker: str, snapshot: Path, path: Path, arguments: list[str], timeout: int) -> dict:
        if any(char in str(snapshot) for char in ",\r\n"):
            raise ToolError("Docker runner data paths must not contain commas or newlines.")
        image = self.settings.docker_image
        name = "ariadne-" + secrets.token_hex(12)
        command = [docker, "run", "--rm", "--pull=never", "--name", name,
                   "--network", "none", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--pids-limit", "64",
                   "--memory", "256m", "--memory-swap", "256m", "--cpus", "1",
                   "--ulimit", "nofile=128:128", "--user", "65534:65534",
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
                   "--mount", f"type=bind,source={snapshot},target=/workspace,readonly",
                   "--workdir", "/workspace", "--entrypoint", "python", image,
                   "-I", "-B", "/workspace/" + path.relative_to(self.root).as_posix(), *arguments]
        output = bytearray()
        output_limit = threading.Event()
        process = None
        timed_out = False
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, shell=False,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

            def collect() -> None:
                assert process is not None and process.stdout is not None
                while True:
                    block = process.stdout.read1(4096)
                    if not block:
                        return
                    remaining = MAX_RESPONSE - len(output)
                    output.extend(block[:max(0, remaining)])
                    if len(block) > remaining:
                        output_limit.set()
                        return

            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if output_limit.wait(0.05) or time.monotonic() >= deadline:
                    timed_out = not output_limit.is_set()
                    process.kill()
                    break
            process.wait(timeout=5)
            reader.join(timeout=2)
            return {"ok": process.returncode == 0 and not output_limit.is_set() and not timed_out,
                    "exit_code": process.returncode, "output": output.decode("utf-8", errors="replace"),
                    "truncated": output_limit.is_set(), "timed_out": timed_out}
        except subprocess.TimeoutExpired:
            raise ToolError("Docker did not terminate within the cleanup timeout.") from None
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                try:
                    subprocess.run([docker, "rm", "-f", name], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
                                   timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if process.stdout is not None:
                    process.stdout.close()

    def _load_connectors(self) -> dict[str, dict]:
        path = self.settings.connectors_path
        if not path.exists():
            return {}
        if path.stat().st_size > MAX_RESPONSE:
            raise ToolError("Connector configuration exceeds 64 KiB.")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            raise ToolError("Connector configuration must be UTF-8 JSON.") from None
        _object(raw, {"connectors"}, {"connectors"})
        entries = raw["connectors"]
        if not isinstance(entries, list) or len(entries) > 32:
            raise ToolError("Configure at most 32 connectors.")
        result = {}
        for entry in entries:
            _object(entry, {"id", "description", "url", "method", "read_only", "headers", "timeout_seconds"}, {"id", "description", "url", "method"})
            identifier = _text(entry["id"], "connector id", 48)
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", identifier):
                raise ToolError("Connector IDs must start with a lowercase letter and use letters, digits, or underscores.")
            name = "connector_" + identifier
            if name in result:
                raise ToolError("Connector IDs must be unique.")
            url = _text(entry["url"], "connector URL", 2000)
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                    or parsed.fragment or any(char in url for char in "\r\n{}")
                    or any(_SECRET_KEY.search(key) for key, _ in urllib.parse.parse_qsl(parsed.query))):
                raise ToolError("Connector URLs must be fixed HTTP(S) URLs without credentials, secret query parameters, or templates.")
            try:
                parsed.port
            except ValueError:
                raise ToolError("Connector URL has an invalid port.") from None
            method = entry["method"]
            if method not in {"GET", "POST"}:
                raise ToolError("Connector method must be GET or POST.")
            read_only = entry.get("read_only", False)
            if not isinstance(read_only, bool) or (read_only and method != "GET"):
                raise ToolError("Only explicitly read-only GET connectors may run automatically.")
            headers = entry.get("headers", {})
            if not isinstance(headers, dict) or len(headers) > 16:
                raise ToolError("Connector headers must be an object of at most 16 entries.")
            for key, value in headers.items():
                if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", key) or key.lower() in {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization"}:
                    raise ToolError("Connector contains an unsupported header.")
                if isinstance(value, dict):
                    _object(value, {"env"}, {"env"})
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", _text(value["env"], "environment variable name", 128)):
                        raise ToolError("Connector secret environment variable name is invalid.")
                else:
                    _text(value, "header value", 4096, empty=True)
                    if "\r" in value or "\n" in value or _SECRET_KEY.search(key):
                        raise ToolError("Secret headers must reference an environment variable.")
            result[name] = {**entry, "description": _text(entry["description"], "connector description", 500),
                            "read_only": read_only, "headers": headers,
                            "timeout_seconds": _integer(entry.get("timeout_seconds", 15), "connector timeout", 1, 30)}
        return result

    def list_connectors(self) -> list[dict]:
        return [{"name": name, "id": entry["id"], "description": entry["description"],
                 "method": entry["method"], "read_only": entry["read_only"],
                 "approval_required": self.policy(name, {}) == "approval"}
                for name, entry in self._connectors.items()]

    @staticmethod
    def _redact(value: Any, secrets_to_hide: list[str]) -> Any:
        if isinstance(value, dict):
            return {key: "[redacted]" if _SECRET_KEY.search(str(key)) else ToolRegistry._redact(item, secrets_to_hide) for key, item in value.items()}
        if isinstance(value, list):
            return [ToolRegistry._redact(item, secrets_to_hide) for item in value]
        if isinstance(value, str):
            for secret in sorted(secrets_to_hide, key=len, reverse=True):
                if secret:
                    value = value.replace(secret, "[redacted]")
            return value
        return value

    def _request(self, connector: dict, args: dict) -> dict:
        args = _object(args, set() if connector["method"] == "GET" else {"body"})
        headers = {"Accept": "application/json, text/plain", "User-Agent": "Ariadne-local/1"}
        secrets_to_hide = [self.settings.api_token] if self.settings.api_token else []
        for key, value in connector["headers"].items():
            if isinstance(value, dict):
                value = os.environ.get(value["env"])
                if not value:
                    raise ToolError("A required connector credential is not configured.")
                if len(value) > 8192 or "\r" in value or "\n" in value:
                    raise ToolError("A connector credential has an invalid format.")
                secrets_to_hide.append(value)
                # Authentication values often contain a fixed prefix such as
                # Bearer; also remove the actual credential if echoed alone.
                if " " in value:
                    secrets_to_hide.append(value.rsplit(" ", 1)[-1])
            headers[key] = value
        body = None
        if connector["method"] == "POST":
            payload = args.get("body", {})
            if not isinstance(payload, dict):
                raise ToolError("Connector body must be a JSON object.")
            try:
                serialized = json.dumps(payload, allow_nan=False, ensure_ascii=False)
            except (TypeError, ValueError, RecursionError):
                raise ToolError("Connector body must contain valid JSON values.") from None
            if len(serialized.encode("utf-8")) > MAX_RESPONSE:
                raise ToolError("Connector request body exceeds 64 KiB.")
            if self._redact(payload, secrets_to_hide) != payload:
                raise ToolError("Do not place credentials or secret fields in connector input. Configure credentials as environment headers.")
            body = serialized.encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(connector["url"], data=body, headers=headers, method=connector["method"])
        try:
            with _bounded_opener(connector["timeout_seconds"]) as (opener, expired):
                with opener.open(request, timeout=connector["timeout_seconds"]) as response:
                    data = response.read(MAX_RESPONSE + 1)
                    status = response.status
                    if expired.is_set():
                        raise TimeoutError("Connector response deadline exceeded.")
        except urllib.error.HTTPError as error:
            return {"ok": False, "error": "Connector returned an HTTP error; redirects are disabled.", "status": error.code}
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
            return {"ok": False, "error": "Connector connection failed. Check the configured service and credentials."}
        truncated = len(data) > MAX_RESPONSE
        text = data[:MAX_RESPONSE].decode("utf-8", errors="replace")
        try:
            result = json.loads(text)
        except (ValueError, RecursionError):
            result = text
        return {"ok": True, "status": status, "data": self._redact(result, secrets_to_hide), "truncated": truncated}
