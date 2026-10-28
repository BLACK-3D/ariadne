"""Owner-registered, loopback-only MCP tools. Optional SDK code lives in a worker.

Remote metadata is evidence to compare, never authorization. This module does not
install packages, start servers, or discover tools on a model's behalf.
"""
from __future__ import annotations

import base64
import copy
import contextlib
import hashlib
import hmac
import importlib.metadata
import json
import math
import os
import re
import secrets
import stat
from pathlib import Path
from urllib.parse import quote, urlsplit

from .config import Settings, _allowed_path
from .tools import ToolRegistry

SDK_VERSION = "4.1.4"
MAX_CONFIG = 262_144
MAX_SCHEMA = 12_000
MAX_ARGUMENTS = 24_000
MAX_OUTPUT = 48_000
MAX_DISCOVERED = 64
MAX_SELECTED = 16
WIRE_LIMIT = 524_288
DEADLINE = 20
MEMORY_BYTES = 384 * 1024 * 1024
_ID = re.compile(r"[a-z][a-z0-9_]{0,23}\Z")
_NAME = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
_ENV = re.compile(r"ARIADNE_MCP_[A-Z0-9_]{1,80}\Z")
_SECRET = re.compile(r"authorization|password|secret|api.?key|token|cookie", re.I)
_PATHS = object.__new__(ToolRegistry)  # Only its directory-lock utility; no registry initialization.


class MCPFailure(ValueError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON keys are unsupported.")
        result[key] = value
    return result


def parse_json(data: bytes):
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON number.")))
    except (RecursionError, OverflowError, UnicodeError):
        raise ValueError("MCP JSON exceeds supported nesting or numeric limits.") from None


def _bounded(value, limit: int, *, depth=0, counter=None):
    if counter is None:
        counter = [0]
    counter[0] += 1
    if depth > 12 or counter[0] > 6000:
        raise ValueError("MCP JSON structure exceeds its limits.")
    if value is None or type(value) in (str, bool, int, float):
        if isinstance(value, str) and (len(value) > limit or "\x00" in value):
            raise ValueError("MCP text exceeds its limits.")
        if type(value) is int and value.bit_length() > 1024:
            raise ValueError("MCP integer exceeds its limit.")
        if type(value) is float and not math.isfinite(value):
            raise ValueError("MCP numbers must be finite.")
    elif isinstance(value, list):
        for child in value:
            _bounded(child, limit, depth=depth + 1, counter=counter)
    elif isinstance(value, dict) and all(isinstance(k, str) and len(k) <= 256 for k in value):
        for child in value.values():
            _bounded(child, limit, depth=depth + 1, counter=counter)
    else:
        raise ValueError("MCP values must be bounded JSON.")
    if depth == 0 and len(canonical(value)) > limit:
        raise ValueError("MCP JSON exceeds its byte limit.")


def read_json(path: Path, limit=MAX_CONFIG, *, missing=None):
    path = _allowed_path(path)
    try:
        with _PATHS._directory(path.parent) as directory:
            fd = _PATHS._open_read(path, directory)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                    raise ValueError("MCP configuration must be a bounded regular file without hard links.")
                raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("MCP configuration exceeds its limit.")
        value = parse_json(raw)
        _bounded(value, limit)
        return value, raw
    except FileNotFoundError:
        if missing is not None:
            return copy.deepcopy(missing), b""
        raise ValueError("MCP configuration or snapshot was not found.") from None


def write_json(path: Path, value) -> bytes:
    """Atomic owner-state write with held ancestor handles and no linked target."""
    raw = canonical(value) + b"\n"
    if len(raw) > MAX_CONFIG:
        raise ValueError("MCP configuration exceeds its limit.")
    path = _allowed_path(path)
    with _PATHS._directory(path.parent, create=True) as directory:
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("MCP target must be a regular file without hard links.")
        temporary = ".mcp-" + secrets.token_hex(12) + ".tmp"
        target = path.parent / temporary if directory is None else temporary
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            if directory is None:
                os.replace(target, path)
            else:
                os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(target, dir_fd=directory)
            except FileNotFoundError:
                pass
    return raw


def validate_url(value):
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise ValueError("MCP requires a fixed loopback HTTP URL.")
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        raise ValueError("MCP URL has an invalid port.") from None
    if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "::1"}
            or not port or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or not parts.path.startswith("/")
            or "%" in parts.netloc or parts.path.startswith("//")):
        raise ValueError("MCP initially supports literal 127.0.0.1/[::1] HTTP endpoints with an explicit port only.")
    return value


def validate_schema(schema, *, root=True, depth=0):
    """Small explicit JSON-Schema subset; no references, regexes or code generation."""
    if not isinstance(schema, dict) or depth > 6:
        raise ValueError("MCP schema must be a supported bounded object.")
    allowed = {"type", "title", "description", "default", "enum", "properties", "required", "additionalProperties", "items",
               "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
    if set(schema) - allowed:
        raise ValueError("MCP schema uses unsupported keywords; registration is refused.")
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if not kinds or len(kinds) > 2 or any(not isinstance(k, str) or k not in {"object", "array", "string", "integer", "number", "boolean", "null"} for k in kinds):
        raise ValueError("MCP schema requires an explicit supported type.")
    if len(set(kinds)) != len(kinds) or (len(kinds) == 2 and "null" not in kinds):
        raise ValueError("Only a single type or a nullable single type is supported.")
    if root and kind != "object":
        raise ValueError("MCP tool input must be an object schema.")
    for key in ("title", "description"):
        if key in schema and (not isinstance(schema[key], str) or len(schema[key]) > 2000):
            raise ValueError("MCP schema text exceeds its limit.")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key]) or abs(schema[key]) > 1e100):
            raise ValueError("MCP schema numeric bound is unsupported.")
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in schema and (type(schema[key]) is not int or not 0 <= schema[key] <= 24000):
            raise ValueError("MCP schema length bound is unsupported.")
    if "enum" in schema:
        if not isinstance(schema["enum"], list) or not 1 <= len(schema["enum"]) <= 64 or any(isinstance(v, (dict, list)) for v in schema["enum"]):
            raise ValueError("MCP enum is unsupported.")
    if "object" in kinds:
        properties = schema.get("properties", {})
        if not isinstance(properties, dict) or len(properties) > 64 or any(not isinstance(k, str) or len(k) > 128 for k in properties):
            raise ValueError("MCP object properties exceed limits.")
        if schema.get("additionalProperties", False) is not False:
            raise ValueError("MCP open-ended object properties are unsupported.")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(k, str) or k not in properties for k in required) or len(set(required)) != len(required):
            raise ValueError("MCP required properties are invalid.")
        for child in properties.values():
            validate_schema(child, root=False, depth=depth + 1)
    elif set(schema) & {"properties", "required", "additionalProperties"}:
        raise ValueError("MCP schema object keywords require object type.")
    if "array" in kinds:
        validate_schema(schema.get("items"), root=False, depth=depth + 1)
    elif "items" in schema:
        raise ValueError("MCP items requires array type.")
    _bounded(schema, MAX_SCHEMA)


def validate_arguments(value, schema):
    _bounded(value, MAX_ARGUMENTS)

    def check(item, node):
        kind = node["type"]
        kinds = kind if isinstance(kind, list) else [kind]
        actual = ("null" if item is None else "boolean" if type(item) is bool else "integer" if type(item) is int
                  else "number" if type(item) is float else "string" if isinstance(item, str)
                  else "array" if isinstance(item, list) else "object" if isinstance(item, dict) else "invalid")
        if actual not in kinds and not (actual == "integer" and "number" in kinds):
            raise ValueError("MCP argument type does not match the registered schema.")
        if "enum" in node and not any(type(item) is type(v) and item == v for v in node["enum"]):
            raise ValueError("MCP argument is outside its registered enum.")
        if actual == "object":
            props = node.get("properties", {})
            if set(item) - set(props) or set(node.get("required", [])) - set(item):
                raise ValueError("MCP object has unknown or missing arguments.")
            for key, child in item.items():
                check(child, props[key])
        if actual == "array":
            if not node.get("minItems", 0) <= len(item) <= min(256, node.get("maxItems", 256)):
                raise ValueError("MCP array length is outside limits.")
            for child in item:
                check(child, node["items"])
        if actual == "string" and not node.get("minLength", 0) <= len(item) <= min(12000, node.get("maxLength", 12000)):
            raise ValueError("MCP string length is outside limits.")
        if actual in {"integer", "number"}:
            for key, valid in (("minimum", lambda n: item >= n), ("maximum", lambda n: item <= n),
                               ("exclusiveMinimum", lambda n: item > n), ("exclusiveMaximum", lambda n: item < n)):
                if key in node and not valid(node[key]):
                    raise ValueError("MCP number is outside its registered bounds.")
    check(value, schema)


def validate_tool(tool):
    _bounded(tool, MAX_SCHEMA + 4000)
    if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not _NAME.fullmatch(tool["name"]):
        raise ValueError("MCP tool name is unsupported.")
    if not isinstance(tool.get("description", ""), str) or len(tool.get("description", "")) > 2000:
        raise ValueError("MCP tool description exceeds its limit.")
    validate_schema(tool.get("inputSchema"))
    # Output schemas are compared as plain bounded JSON, never compiled to Python types.
    if "outputSchema" in tool:
        validate_schema(tool["outputSchema"], root=False)


def _server(value):
    if not isinstance(value, dict) or set(value) - {"id", "url", "mode", "headers", "identity", "tools"}:
        raise ValueError("Unsupported MCP server configuration.")
    if not isinstance(value.get("id"), str) or not _ID.fullmatch(value["id"]):
        raise ValueError("MCP server id must be a short lowercase identifier.")
    validate_url(value.get("url"))
    if not isinstance(value.get("mode"), str) or value["mode"] not in {"modern", "legacy"}:
        raise ValueError("MCP protocol mode must be explicitly modern or legacy.")
    headers = value.get("headers", {})
    if not isinstance(headers, dict) or len(headers) > 4:
        raise ValueError("MCP credential headers exceed their limit.")
    seen = set()
    for name, ref in headers.items():
        if not isinstance(name, str) or name.lower() in seen or name.lower() not in {"authorization", "x-api-key"}:
            raise ValueError("Only dedicated authorization headers are supported.")
        seen.add(name.lower())
        if (not isinstance(ref, dict) or set(ref) - {"env", "prefix"} or not isinstance(ref.get("env"), str)
                or not _ENV.fullmatch(ref["env"]) or ref.get("prefix", "") not in {"", "Bearer "}):
            raise ValueError("MCP credentials must use dedicated ARIADNE_MCP_ environment references.")
    tools = value.get("tools", [])
    if not isinstance(tools, list) or len(tools) > MAX_SELECTED:
        raise ValueError("Too many selected MCP tools.")
    names = set()
    for selected in tools:
        if not isinstance(selected, dict) or set(selected) != {"metadata", "read_only"} or type(selected["read_only"]) is not bool:
            raise ValueError("Each MCP tool needs an owner policy and metadata snapshot.")
        validate_tool(selected["metadata"])
        name = selected["metadata"]["name"]
        if name in names:
            raise ValueError("Duplicate MCP tool registration.")
        names.add(name)
    if tools and not isinstance(value.get("identity"), dict):
        raise ValueError("MCP tools require an approved server identity snapshot.")
    return value


def redact(value, secret_values):
    variants = set()
    for secret in secret_values:
        if secret:
            variants.update((secret, quote(secret, safe=""), base64.b64encode(secret.encode()).decode(),
                             base64.urlsafe_b64encode(secret.encode()).decode()))
    def clean(item):
        if type(item) in (int, float) and (str(item) in variants or canonical(item).decode("ascii") in variants):
            return "[redacted]"
        if isinstance(item, str):
            for secret in sorted(variants, key=len, reverse=True):
                item = item.replace(secret, "[redacted]")
            return item
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, dict):
            return {clean(key): "[redacted]" if _SECRET.search(key) else clean(child) for key, child in item.items()}
        return item
    return clean(value)


def _registered_name(server, tool):
    bound = {"server": {key: value for key, value in server.items() if key != "tools"}, "tool": tool}
    slug = re.sub(r"[^a-z0-9_]", "_", tool["metadata"]["name"].lower())[:16]
    return "mcp_" + server["id"] + "_" + slug + "_" + digest(bound)[:10], bound


def validate_catalog(config):
    """Build and bound the exposed catalog before owner configuration commits."""
    result = []
    for server in config["servers"]:
        for tool in server.get("tools", []):
            name, _ = _registered_name(server, tool)
            description = tool["metadata"].get("description", "")[:512]
            result.append({"type": "function", "function": {"name": name,
                "description": f"Registered local MCP tool {server['id']}: {description}",
                "parameters": copy.deepcopy(tool["metadata"]["inputSchema"])}})
    if len(canonical(result)) > 64_000:
        raise ValueError("Selected MCP tool schemas exceed their total limit.")
    return result


class MCPTools:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = _allowed_path(settings.data_dir / "mcp.json")
        self._fingerprint_key = settings.api_token.encode() if settings.api_token else secrets.token_bytes(32)

    @contextlib.contextmanager
    def mutation(self):
        """Serialize owner config/discovery transactions across HTTP and CLI processes."""
        from .locking import InstanceLock
        with _PATHS._directory(self.settings.data_dir, create=True):
            path = _allowed_path(self.settings.data_dir / "mcp-config.lock")
            if path.exists():
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("MCP mutation lock must be a regular file without hard links.")
            lock = InstanceLock(path)
            lock.acquire()
            try:
                yield
            finally:
                lock.release()

    def config(self):
        value, _ = read_json(self.path, missing={"version": 1, "servers": []})
        if not isinstance(value, dict) or set(value) != {"version", "servers"} or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("MCP configuration version is unsupported.")
        if not isinstance(value["servers"], list) or len(value["servers"]) > 4:
            raise ValueError("MCP server count exceeds its limit.")
        names = set()
        for server in value["servers"]:
            _server(server)
            if server["id"] in names:
                raise ValueError("Duplicate MCP server identifier.")
            names.add(server["id"])
        if sum(len(s.get("tools", [])) for s in value["servers"]) > MAX_SELECTED:
            raise ValueError("MCP selected tool count exceeds its limit.")
        validate_catalog(value)
        return value

    def _entries(self):
        result = {}
        for server in self.config()["servers"]:
            for tool in server.get("tools", []):
                name, bound = _registered_name(server, tool)
                result[name] = (server, tool, bound)
        return result

    def schemas(self):
        return validate_catalog(self.config())

    def _entry(self, name):
        try:
            return self._entries()[name]
        except (KeyError, TypeError):
            raise ValueError("Unknown or changed MCP tool registration.") from None

    def policy(self, name, args):
        _, tool, _ = self._entry(name)
        validate_arguments(args, tool["metadata"]["inputSchema"])
        return "auto" if tool["read_only"] else "approval"

    def _headers(self, server):
        headers, values = {}, []
        for name, ref in server.get("headers", {}).items():
            secret = os.environ.get(ref["env"], "")
            if not secret or len(secret) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in secret):
                raise ValueError("A configured MCP credential is missing or invalid.")
            headers[name] = ref.get("prefix", "") + secret
            values.extend((secret, headers[name]))
        return headers, values

    def fingerprint_material(self, name):
        server, _, bound = self._entry(name)
        headers, _ = self._headers(server)
        # HMAC prevents a persisted fingerprint from exposing a guessable credential hash.
        auth = hmac.new(self._fingerprint_key, canonical(headers), hashlib.sha256).hexdigest()
        return {"kind": "mcp", "registration": copy.deepcopy(bound), "credential_binding": auth}

    def descriptions(self):
        return [{"name": name, "server": server["id"], "url": server["url"], "mode": server["mode"],
                 "remote_tool": tool["metadata"]["name"], "read_only": tool["read_only"],
                 "credential_references": [ref["env"] for ref in server.get("headers", {}).values()]}
                for name, (server, tool, _) in self._entries().items()]

    def status(self):
        try:
            version = importlib.metadata.version("fastmcp-slim")
        except importlib.metadata.PackageNotFoundError:
            version = None
        try:
            count = len(self._entries())
            return {"ready": version == SDK_VERSION, "installed_version": version, "required_version": SDK_VERSION,
                    "selected_tools": count, "transport": "loopback-http", "worker_memory_bytes": MEMORY_BYTES,
                    "deadline_seconds": DEADLINE}
        except (ValueError, OSError):
            return {"ready": False, "error": "MCP owner configuration is invalid.", "required_version": SDK_VERSION}

    def _worker(self, server, operation, *, cancelled=None, **extra):
        if not self.status().get("ready"):
            raise MCPFailure("Pinned optional MCP client is unavailable or configuration is invalid.")
        from .worker import run_worker
        try:
            headers, secrets_ = self._headers(server)
        except ValueError:
            raise MCPFailure("A configured MCP credential is missing or invalid.") from None
        request = {"operation": operation, "url": server["url"], "mode": server["mode"], "headers": headers, **extra}
        try:
            raw = run_worker(Path(__file__).with_name("_mcp_worker.py"), canonical(request), timeout=DEADLINE,
                             memory_bytes=MEMORY_BYTES, cpu_seconds=10, max_output_bytes=MAX_CONFIG,
                             cancelled=cancelled)
        except (ValueError, OSError):
            raise MCPFailure("MCP worker failed within its limits.", uncertain=operation == "call") from None
        result = parse_json(raw)
        _bounded(result, MAX_CONFIG)
        if not isinstance(result, dict):
            raise MCPFailure("MCP worker response is invalid.", uncertain=operation == "call")
        if result.get("error"):
            raise MCPFailure("MCP service failed or exceeded a safety limit.",
                             uncertain=operation == "call" and bool(result.get("submitted", True)))
        return redact(result, secrets_)

    def discover(self, server_id):
        server = next((s for s in self.config()["servers"] if s["id"] == server_id), None)
        if server is None:
            raise ValueError("Unknown configured MCP server.")
        result = self._worker(server, "discover")
        tools = result.get("tools")
        if not isinstance(tools, list) or len(tools) > MAX_DISCOVERED or not isinstance(result.get("identity"), dict):
            raise ValueError("MCP discovery exceeded its limits.")
        # Unsupported schemas remain visible to an owner, but can never be registered.
        public = {key: copy.deepcopy(value) for key, value in server.items() if key not in {"tools", "identity"}}
        return {"version": 1, "server": public, "identity": result["identity"], "tools": tools}

    def execute(self, name, args, *, cancelled=None):
        write = False
        submitted = False
        try:
            server, tool, _ = self._entry(name)
            write = not tool["read_only"]
            validate_arguments(args, tool["metadata"]["inputSchema"])
            if cancelled and cancelled():
                raise MCPFailure("MCP call cancelled before worker submission.")
            submitted = True
            result = self._worker(server, "call", cancelled=cancelled, name=tool["metadata"]["name"], arguments=args,
                                  expected_identity=server["identity"], expected_tool=tool["metadata"])
            _bounded(result, MAX_OUTPUT)
            return {"ok": True, "server": server["id"], **result}
        except (ValueError, OSError, ImportError) as error:
            uncertain = write and submitted and (error.uncertain if isinstance(error, MCPFailure) else True)
            return {"ok": False, "uncertain": uncertain,
                    "error": "MCP call rejected, unavailable, changed, or over its limits. Reconcile an uncertain write before retrying."}
