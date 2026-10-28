"""Reviewed loopback apps and fixed local flows; no arbitrary URL or shell tool."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import _allowed_path
from .worker import run_worker, WorkerError

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"browser_task", "local_flow", "erpnext_read"}
_ID = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")


def local_url(value, protected=()):
    if type(value) is not str or len(value) > 2000 or any(ord(c) < 33 for c in value) or "\\" in value or "%" in value:
        raise ValueError("Use a literal loopback HTTP URL without encoded path components.")
    url = urlsplit(value)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password or url.query or url.fragment or not url.port or url.port in {8080, 11434, *protected}:
        raise ValueError("Only a separate reviewed app at http://127.0.0.1:PORT is supported. Ariadne and model endpoints are excluded.")
    if any(part in {".", ".."} for part in url.path.split("/")):
        raise ValueError("Dot path components are unsupported.")
    return urlunsplit((url.scheme, url.netloc, url.path or "/", "", ""))


def within_app(candidate, prefix):
    """Match origin and complete path segments, allowing ordinary query strings."""
    if type(candidate) is not str or len(candidate) > 4000 or any(ord(c) < 32 for c in candidate):
        return False
    parsed, base = urlsplit(candidate), urlsplit(prefix)
    if parsed.username or parsed.password or parsed.fragment or (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
        return False
    try:
        local_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", "")))
    except ValueError:
        return False
    path = base.path.rstrip("/")
    return parsed.path == path or parsed.path.startswith(path + "/")


class AutomationTools:
    def __init__(self, registry):
        self.registry = registry
        self.config_path = registry.settings.data_dir / "automation-config.json"

    def config(self, supplied=None):
        path = _allowed_path(self.config_path)
        if supplied is None:
            if not path.exists():
                return {"apps": [], "flows": [], "erpnext": None}
            if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 32_768:
                raise ValueError("Invalid automation configuration file.")
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = supplied
        if type(data) is not dict or set(data) - {"apps", "flows", "erpnext"}:
            raise ValueError("Unsupported automation configuration.")
        data = {"apps": [], "flows": [], "erpnext": None, **data}
        for kind in ("apps", "flows"):
            if type(data[kind]) is not list or len(data[kind]) > 16:
                raise ValueError("Configure at most 16 local apps or flows.")
            seen = set()
            for entry in data[kind]:
                allowed = {"id", "url", "description"} | ({"method", "token_file"} if kind == "flows" else set())
                if type(entry) is not dict or set(entry) - allowed or not {"id", "url"} <= set(entry) or type(entry["id"]) is not str or not _ID.fullmatch(entry["id"]) or entry["id"] in seen:
                    raise ValueError("Each local integration requires a unique id and fixed URL.")
                seen.add(entry["id"])
                if type(entry.get("description", "")) is not str or len(entry.get("description", "")) > 500:
                    raise ValueError("Integration descriptions must be text of at most 500 characters.")
                local_url(entry["url"], {self.registry.settings.port})
                if kind == "flows" and entry.get("method", "POST") not in {"GET", "POST"}:
                    raise ValueError("Local flows support fixed GET or POST routes.")
                if "token_file" in entry:
                    token = _allowed_path(self.registry.settings.data_dir / entry["token_file"])
                    if token.parent != self.registry.settings.data_dir or token.name != "flows.token":
                        raise ValueError("Only the private local flow token can be referenced.")
        if data["erpnext"] is not None:
            erp = data["erpnext"]
            if type(erp) is not dict or set(erp) != {"url", "credential_env"} or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", str(erp["credential_env"])):
                raise ValueError("ERPNext needs a fixed loopback URL and credential environment reference.")
            local_url(erp["url"], {self.registry.settings.port})
        return data

    def configure(self, data):
        # Operator API only. The model has no registration tool.
        self.config(data)
        encoded = json.dumps(data, allow_nan=False, ensure_ascii=False).encode()
        if len(encoded) > 32_768:
            raise ValueError("Automation configuration exceeds its limit.")
        path = _allowed_path(self.config_path)
        with self.registry._lock, self.registry._directory(path.parent, create=True):
            temporary = _allowed_path(path.with_suffix("." + secrets.token_hex(8) + ".tmp"))
            try:
                with temporary.open("xb") as target:
                    target.write(encoded)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return self.overview()

    def overview(self):
        data = self.config()
        return {"apps": data["apps"], "flows": [{k: v for k, v in x.items() if k != "token_file"} for x in data["flows"]], "erpnext": {"configured": bool(data["erpnext"])}, "status": self.status()}

    def status(self):
        try:
            data = self.config()
            browser = _allowed_path(ROOT / "runtime" / "automation" / "browsers")
            driver = _allowed_path(ROOT / "runtime" / "automation" / "python" / "playwright")
            binaries = driver.is_dir() and browser.is_dir() and any(browser.glob("chromium-*"))
            return {"browser": {"ready": bool(binaries and data["apps"]), "runtime_cached": binaries, "reason": "Register a reviewed local app; browser runs with a disposable profile and request filtering. This is not OS-level network isolation."},
                    "flows": {"ready": bool(data["flows"]), "reason": "Fixed owner-registered loopback routes; services are checked on invocation."},
                    "erpnext": {"ready": bool(data["erpnext"] and os.environ.get(data["erpnext"]["credential_env"])), "reason": "Optional read-only ERPNext connector; requires a separately installed local ERP stack and scoped credential."}}
        except (ValueError, OSError):
            return {key: {"ready": False, "reason": "Automation configuration is invalid; inspect local settings."} for key in ("browser", "flows", "erpnext")}

    def schemas(self):
        from .tools import _schema
        ready = self.status()
        schemas = []
        if ready["browser"]["ready"]:
            schemas.append(_schema("browser_task", "Inspect and interact with one owner-registered local web app using a fresh browser profile. All invocations require approval. No arbitrary JavaScript, credentials, downloads or external sites.", {"app": {"type": "string"}, "path": {"type": "string", "default": "/"}, "actions": {"type": "array", "maxItems": 12, "items": {"type": "object", "properties": {"op": {"enum": ["click", "fill", "select"]}, "selector": {"type": "string"}, "value": {"type": "string"}}, "required": ["op", "selector"], "additionalProperties": False}}}, ["app"]))
        if ready["flows"]["ready"]:
            schemas.append(_schema("local_flow", "Invoke a fixed reviewed local workflow route. POST requires approval. Does not install nodes or deploy flows.", {"flow": {"type": "string"}, "body": {"type": "object"}}, ["flow"]))
        if ready["erpnext"]["ready"]:
            schemas.append(_schema("erpnext_read", "Read selected records from an owner-configured local ERPNext server. Results remain untrusted. No posting, spending, email or account changes.", {"doctype": {"enum": ["Item", "Customer", "Supplier", "Warehouse", "Sales Invoice", "Purchase Order"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ["doctype"]))
        return schemas

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown automation tool.")
        if name == "erpnext_read":
            return "auto"
        if name == "local_flow":
            route = next((x for x in self.config()["flows"] if x["id"] == args.get("flow")), None)
            return "auto" if route and route.get("method", "POST") == "GET" else "approval"
        return "approval"

    def fingerprint_material(self, name, args):
        data = self.config()
        material = {"config": data, "implementation": "local-automation-1"}
        for entry in data["flows"]:
            if entry.get("token_file"):
                path = _allowed_path(self.registry.settings.data_dir / entry["token_file"])
                material["token_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        if data["erpnext"]:
            material["credential"] = os.environ.get(data["erpnext"]["credential_env"], "")
        return material

    def execute(self, name, args, *, cancelled=None):
        from .tools import _object, _text, _integer, _bounded_opener
        import urllib.request
        import urllib.error
        data = self.config()
        if cancelled and cancelled():
            raise ValueError("Operation cancelled.")
        if name == "browser_task":
            _object(args, {"app", "path", "actions"}, {"app"})
            app = next((x for x in data["apps"] if x["id"] == args["app"]), None)
            if not app or not self.status()["browser"]["ready"]:
                raise ValueError("Local app or browser assets are unavailable.")
            base = local_url(app["url"], {self.registry.settings.port})
            path = _text(args.get("path", "/"), "path", 1000)
            if not path.startswith("/") or path.startswith("//"):
                raise ValueError("Use an app-relative path beginning with one slash.")
            parsed = urlsplit(base)
            url = local_url(urlunsplit((parsed.scheme, parsed.netloc, path, "", "")), {self.registry.settings.port})
            if not within_app(url, base):
                raise ValueError("Path must stay under the registered app prefix.")
            actions = args.get("actions", [])
            if type(actions) is not list or len(actions) > 12:
                raise ValueError("Use at most 12 browser actions.")
            for action in actions:
                _object(action, {"op", "selector", "value"}, {"op", "selector"})
                if action["op"] not in {"click", "fill", "select"}:
                    raise ValueError("Unsupported browser action.")
                _text(action["selector"], "selector", 300)
                if action["op"] != "click":
                    _text(action.get("value"), "value", 2000, empty=True)
                elif "value" in action:
                    raise ValueError("Click does not accept a value.")
            payload = json.dumps({"url": url, "prefix": base, "actions": actions}).encode()
            try:
                return json.loads(run_worker(Path(__file__).with_name("_automation_worker.py"), payload, timeout=60, memory_bytes=3 * 1024**3, cpu_seconds=45, max_output_bytes=64_000, process_limit=8, cancelled=cancelled))
            except WorkerError as error:
                return {"ok": False, "error": str(error), "uncertain": True}
        if name == "local_flow":
            _object(args, {"flow", "body"}, {"flow"})
            entry = next((x for x in data["flows"] if x["id"] == args["flow"]), None)
            if not entry:
                raise ValueError("Unknown local flow.")
            url, method = local_url(entry["url"], {self.registry.settings.port}), entry.get("method", "POST")
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if entry.get("token_file"):
                token_path = _allowed_path(self.registry.settings.data_dir / entry["token_file"])
                if token_path.stat().st_size > 256 or token_path.stat().st_nlink != 1:
                    raise ValueError("Invalid local flow token.")
                token = token_path.read_text(encoding="ascii").strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token):
                    raise ValueError("Invalid local flow token.")
                headers["Authorization"] = "Bearer " + token
            if method == "GET" and "body" in args:
                raise ValueError("GET flows do not accept a body.")
            if type(args.get("body", {})) is not dict:
                raise ValueError("Flow body must be an object.")
            payload = json.dumps(args.get("body", {}), allow_nan=False).encode() if method == "POST" else None
            if payload and len(payload) > 32_768:
                raise ValueError("Flow input exceeds 32 KiB.")
        elif name == "erpnext_read":
            from urllib.parse import quote, urlencode
            _object(args, {"doctype", "limit"}, {"doctype"})
            if args["doctype"] not in {"Item", "Customer", "Supplier", "Warehouse", "Sales Invoice", "Purchase Order"} or not data["erpnext"]:
                raise ValueError("Unsupported ERP record type or missing configuration.")
            entry = data["erpnext"]
            url = local_url(entry["url"], {self.registry.settings.port}).rstrip("/") + "/api/resource/" + quote(args["doctype"], safe="") + "?" + urlencode({"fields": '["name"]', "limit_page_length": _integer(args.get("limit", 20), "limit", 1, 50)})
            credential = os.environ.get(entry["credential_env"], "")
            if not credential or len(credential) > 4096 or any(ord(c) < 32 for c in credential):
                raise ValueError("ERP credential is missing or invalid.")
            method, payload = "GET", None
            headers = {"Accept": "application/json", "Authorization": "token " + credential}
        else:
            raise ValueError("Unknown automation tool.")
        try:
            with _bounded_opener(15) as (opener, expired):
                with opener.open(urllib.request.Request(url, data=payload, headers=headers, method=method), timeout=15) as response:
                    body = response.read(60_001)
                    if expired.is_set() or len(body) > 60_000:
                        raise OSError("Response exceeds deadline or output bound.")
            value = json.loads(body)
            value = self.registry._redact(value, [headers.get("Authorization", ""), headers.get("Authorization", "").split(" ", 1)[-1]])
            return {"ok": True, "result": value}
        except urllib.error.HTTPError as error:
            return {"ok": False, "error": "Local service returned an HTTP error; redirects are disabled.", "status": error.code, "uncertain": method == "POST"}
        except (OSError, ValueError, urllib.error.URLError):
            return {"ok": False, "error": "Local service did not return a bounded JSON response.", "uncertain": method == "POST"}
