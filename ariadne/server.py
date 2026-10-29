"""Authenticated same-origin WSGI API and bundled local web interface."""
from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, quote

from . import __version__
from .engine import Engine
from .store import Store
from .voice import Voice
from .sources import SourceManager, SourceChanged
from .verification import validate_checks
from .capabilities import catalog, seed_workflows
from .routines import RoutineConflict


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def text_field(data, key, limit, default=None):
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{key} must be nonempty text, at most {limit} characters")
    return value.strip()


class Application:
    def __init__(self, settings, store=None, engine=None):
        self.settings = settings
        self.store = store or Store(settings.data_dir / "ariadne.sqlite3")
        self.engine = engine or Engine(settings, self.store)
        self.voice = Voice(settings)
        self.voice_lock = threading.Lock()
        self.import_lock = threading.Lock()
        self.mcp_lock = threading.Lock()
        from .mcp_admin import MCPAdmin
        self.mcp_admin = MCPAdmin(self.engine.tools.mcp)
        self.sources = SourceManager(settings, self.store)
        self.engine.tools.references.books.sources = self.sources
        self.procedures = self.engine.tools.procedures.service()
        self.routines = self.engine.routines
        from .internet import InternetService
        self.internet = InternetService(settings, self.store, self.engine.tools)
        self.engine.internet_provider = self.internet_call
        self.engine.internet_enabled = lambda: self.internet.config()["enabled"]
        from .briefing import BriefingService
        from .wake import WakeService, windows_speak_response
        self.briefing = BriefingService(settings, self.store, self.engine.tools)
        self.engine.briefing_provider = self.briefing_read
        self.wake_voice = WakeService(settings, self.submit_voice, blocked=self.voice_blocked,
                                      get_run=self.store.run, speak=windows_speak_response,
                                      on_spoken_stop=self.disable_wake_startup)
        seed_workflows(self.store)
        self.static = Path(__file__).parent / "static"
        self.status_cache, self.status_at = None, 0
        self.status_lock = threading.Lock()
        self.allowed_hosts = {"localhost", "127.0.0.1", "[::1]", socket.gethostname().lower()}
        if settings.host not in {"0.0.0.0", "::"}:
            self.allowed_hosts.add(settings.host.lower())
        self.allowed_hosts.update(x.strip().lower() for x in os.environ.get("ARIADNE_ALLOWED_HOSTS", "").split(",") if x.strip())

    def start_services(self):
        self.briefing.start()
        preferences = self.store.setting("wake_preferences", {})
        if isinstance(preferences, dict) and preferences.get("enabled") is True:
            try:
                self.wake_voice.start(mode="pc", session_hours=None)
            except (ValueError, OSError):
                logging.getLogger("ariadne").warning("Saved PC microphone preference could not start; inspect voice status")

    def disable_wake_startup(self):
        self.store.set_setting("wake_preferences", {"enabled": False, "mode": "pc", "session_hours": None})

    def wake_status(self):
        preferences = self.store.setting("wake_preferences", {})
        return {**self.wake_voice.status(), "auto_start": isinstance(preferences, dict) and preferences.get("enabled") is True}

    def location_status(self):
        value = self.store.setting("device_location", None)
        if not isinstance(value, dict):
            return None
        value = dict(value)
        try:
            age = max(0, (datetime.now(timezone.utc)-datetime.fromisoformat(value["measured_at_utc"])).total_seconds())
            value.update(age_seconds=round(age, 3), stale=age > 300)
        except (KeyError, ValueError, TypeError):
            value["stale"] = True
        return value

    def stop_services(self):
        try:
            self.wake_voice.stop()
        finally:
            try:
                self.briefing.stop()
            finally:
                self.internet.stop()

    def internet_call(self, action, arguments):
        """Supply compact, dated public evidence to the local model."""
        if action == "search":
            result = self.internet.search(arguments["query"], limit=arguments["max_results"], refresh=arguments["refresh"])
            limit = 700
        elif action == "fetch":
            result = self.internet.fetch(arguments["url"], refresh=arguments["refresh"])
            # Leave space for citation, dates and provenance in the engine's tool budget.
            limit = min(arguments["max_chars"], 5000)
        else:
            raise ValueError("Public web reads support search or fetch")
        result = dict(result)
        # Keep source retrieval time distinct from service bookkeeping. The full
        # owner API retains completion time; the model cites each source's fetch.
        result.pop("completed_at", None)
        result["sources"] = [dict(source) for source in result.get("sources", [])]
        for source in result["sources"]:
            text = source.get("text", "")
            if len(text) > limit:
                source.update(text=text[:limit], text_truncated=True)
        # Budget the whole serialized tool response so the engine does not replace
        # structured citations with its generic 7,000-character JSON preview.
        budget = 7600
        def size(value):
            return len(json.dumps(value, ensure_ascii=False))
        def metadata_size():
            return size({**result, "sources": [{**source, "text": ""} for source in result["sources"]]})
        omitted = 0
        while len(result["sources"]) > 1 and metadata_size() > budget - 500:
            result["sources"].pop()
            omitted += 1
        if omitted:
            result["omitted_sources"] = omitted
            result["sources_truncated"] = True
        while size(result) > budget:
            source = max(result["sources"], key=lambda item: len(item.get("text", "")), default=None)
            if source is None or not source.get("text"):
                # Extreme URL/metadata sizes can consume even a single citation.
                result = {key: result[key] for key in ("ok", "operation", "cached", "offline", "refresh_failed", "error", "omitted_sources") if key in result}
                result["sources"] = []
                result["context_notice"] = "Evidence metadata exceeds the model context allowance; inspect the full source in Internet controls."
                break
            source["text"] = source["text"][:max(0, len(source["text"]) - max(100, size(result)-budget+50))]
            source["text_truncated"] = True
        return result

    def voice_blocked(self):
        return self.engine.is_paused() or any(r["status"] in {"queued", "running", "waiting_approval", "verifying"} for r in self.store.runs())

    def submit_voice(self, command):
        if self.voice_blocked():
            raise ValueError("Ariadne is paused or a mission is active; voice requests are held")
        profile = self.engine.agents.select(command, "auto")
        run = self.store.new_run(command, agent_id=profile["id"])
        self.store.event(run["id"], "voice_command", {"wake_word": "Ariadne", "agent_id": profile["id"]})
        self.engine.wake.set()
        return self.public_run(run)

    def briefing_read(self, action):
        """Read dated existing evidence without network access or generation."""
        status = self.briefing.status()
        if action == "status":
            return {"ok": True, **status}
        if action != "latest":
            raise ValueError("Briefing reads support status or latest")
        receipt = status.get("last_report")
        if not receipt or receipt.get("status") != "completed":
            return {"ok": False, "error": "No completed daily brief is available", "region_required": status["region_required"]}
        report = self.briefing_content(receipt, limit=300)
        json_artifact, payload = self.briefing_payload(receipt, "brief.json")
        from .briefing_summary import compact_summary
        return {"ok": True, **report, "summary": compact_summary(json.loads(payload)),
                "evidence_json": {"path": json_artifact["path"], "sha256": json_artifact["sha256"]},
                "note": "Summary derived from verified saved JSON; artifact hashes identify the original files, not the derived summary. No refresh or generation was performed."}

    def briefing_content(self, receipt, limit=20000):
        """Bind exactly the returned report bytes to the saved receipt."""
        artifact, payload = self.briefing_payload(receipt, "brief.md")
        content = payload.decode("utf-8")
        return {"path": artifact["path"], "sha256": artifact["sha256"], "bytes": len(payload),
                "content": content[:limit], "truncated": len(content) > limit}

    def briefing_payload(self, receipt, filename):
        artifact = next((item for item in receipt["artifacts"] if item["path"].endswith("/"+filename)), None)
        if artifact is None:
            raise ValueError("The completed brief has no readable report")
        registry = self.engine.tools
        path = registry._path(artifact["path"])
        with registry._lock, registry._directory(path.parent) as directory_fd:
            with os.fdopen(registry._open_read(path, directory_fd), "rb") as source:
                payload = source.read(4_000_001)
        if len(payload) > 4_000_000 or len(payload) != artifact["bytes"] or hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
            raise HTTPError(409, "The saved briefing report has changed; inspect its artifact and receipt")
        return artifact, payload

    def __call__(self, env, start_response):
        headers = [("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                   ("X-Frame-Options", "DENY"), ("Cache-Control", "no-store"),
                   ("Permissions-Policy", "microphone=(self), camera=(), geolocation=(self)"),
                   ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]
        try:
            host = env.get("HTTP_HOST", "")
            hostname = urlsplit("//" + host).hostname
            if not hostname or (hostname.lower() not in self.allowed_hosts and "[" + hostname.lower() + "]" not in self.allowed_hosts):
                raise HTTPError(403, "Host is not allowed. Configure ARIADNE_ALLOWED_HOSTS for LAN access.")
            origin = env.get("HTTP_ORIGIN")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != host.lower():
                    raise HTTPError(403, "Cross-origin requests are not allowed")
            path, method = env.get("PATH_INFO", "/"), env.get("REQUEST_METHOD", "GET")
            if path == "/health" and method == "GET":
                status, payload, mime = 200, {"name": "Ariadne", "status": "ok", "version": __version__}, "application/json"
            elif path.startswith("/api/"):
                expected = "Bearer " + self.settings.api_token
                supplied = env.get("HTTP_AUTHORIZATION", "")
                if not self.settings.api_token or not hmac.compare_digest(supplied.encode(), expected.encode()):
                    raise HTTPError(401, "Pair this browser with the server access token")
                status, payload, mime = self.api(path, method, env)
            else:
                if method != "GET":
                    raise HTTPError(405, "Method not allowed")
                assets = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
                          "/app.js": ("app.js", "text/javascript"), "/styles.css": ("styles.css", "text/css"),
                          "/static/app.js": ("app.js", "text/javascript"), "/static/styles.css": ("styles.css", "text/css"),
                          "/static/command.js": ("command.js", "text/javascript"), "/static/command.css": ("command.css", "text/css"),
                          "/static/routines.js": ("routines.js", "text/javascript")}
                if path not in assets:
                    raise HTTPError(404, "Not found")
                filename, mime = assets[path]
                status, payload = 200, (self.static / filename).read_bytes()
        except HTTPError as error:
            status, payload, mime = error.status, {"error": error.message}, "application/json"
        except SourceChanged as error:
            status, payload, mime = 409, {"error": str(error)}, "application/json"
        except RoutineConflict as error:
            status, payload, mime = 409, {"error": str(error)}, "application/json"
        except KeyError:
            status, payload, mime = 404, {"error": "Not found"}, "application/json"
        except FileNotFoundError:
            status, payload, mime = 404, {"error": "File or reference folder no longer exists"}, "application/json"
        except PermissionError:
            status, payload, mime = 403, {"error": "Access denied by the operating system"}, "application/json"
        except (ValueError, TypeError) as error:
            status, payload, mime = 400, {"error": str(error)}, "application/json"
        except Exception:
            logging.getLogger("ariadne").exception("Request failed")
            status, payload, mime = 500, {"error": "Server operation failed; check the local server log"}, "application/json"
        if mime == "application/json":
            payload = json.dumps(payload, ensure_ascii=False).encode()
        if status == 200 and mime == "application/octet-stream" and env.get("ariadne.download_name"):
            headers.append(("Content-Disposition", "attachment; filename=artifact; filename*=UTF-8''" + quote(env["ariadne.download_name"], safe="")))
        headers.extend([("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime == "application/json" else "")), ("Content-Length", str(len(payload)))])
        labels = {200: "OK", 201: "Created", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 413: "Payload Too Large", 415: "Unsupported Media Type", 429: "Too Many Requests", 500: "Internal Server Error"}
        start_response(f"{status} {labels.get(status, 'Error')}", headers)
        return [payload]

    def body(self, env, raw=False, limit=None):
        try:
            length = int(env.get("CONTENT_LENGTH") or "0")
        except ValueError:
            raise HTTPError(400, "Invalid Content-Length") from None
        if length < 0 or length > (limit if limit is not None else 4_000_000 if raw else 1_000_000):
            raise HTTPError(413, "Request body exceeds the limit")
        if not raw and env.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
            raise HTTPError(415, "Use application/json")
        payload = env["wsgi.input"].read(length)
        if len(payload) != length:
            raise HTTPError(400, "Incomplete request body")
        if raw:
            return payload
        try:
            data = json.loads(payload or b"{}")
        except (ValueError, UnicodeError):
            raise HTTPError(400, "Invalid JSON") from None
        if not isinstance(data, dict):
            raise HTTPError(400, "JSON body must be an object")
        return data

    @staticmethod
    def public_run(run):
        return {k: v for k, v in run.items() if k not in {"context", "pending", "prompt", "verification"}}

    def api(self, path, method, env):
        parts = path.strip("/").split("/")[1:]
        query = parse_qs(env.get("QUERY_STRING", ""))
        result, status = None, 200
        if parts == ["status"] and method == "GET":
            with self.status_lock:
                if time.monotonic() - self.status_at > 5 or self.status_cache is None:
                    self.status_cache, self.status_at = self.engine.model.status(), time.monotonic()
            result = {"name": "Ariadne", "version": __version__, "model": self.store.setting("model", self.settings.model),
                      "ollama": self.status_cache, "paused": self.engine.is_paused(),
                      "active_runs": sum(r["status"] in {"running", "queued", "waiting_approval"} for r in self.store.runs()),
                      "max_steps": self.settings.max_steps, "workspace": str(self.settings.workspace), "voice": self.voice.status(),
                      "speech_backend": self.store.setting("speech_backend", "system"),
                      "wake": self.wake_status()}
            result["routines"] = self.routines.status()
            from .network_policy import network_policy_status
            result["network_policy"] = network_policy_status()
            result["internet"] = self.internet.status()
        elif parts == ["runs"] and method == "GET":
            result = {"runs": self.store.runs()}
        elif parts == ["runs"] and method == "POST":
            data = self.body(env)
            prompt = text_field(data, "prompt", 16000)
            session_id = data.get("session_id")
            if session_id is not None and (not isinstance(session_id, str) or len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id)):
                raise ValueError("Invalid session ID")
            checks = validate_checks(data.get("checks", []), self.engine.tools)
            profile = self.engine.agents.select(prompt, data.get("agent_id", "auto"))
            result, status = {"run": self.public_run(self.store.new_run(prompt, session_id, checks, agent_id=profile["id"]))}, 201
            self.engine.wake.set()
        elif parts == ["agents"] and method == "GET":
            result = self.engine.agents.catalog()
        elif parts == ["internet"] and method == "GET":
            result = self.internet.status()
        elif parts == ["internet", "history"] and method == "GET":
            result = self.internet.history(limit=min(100, max(1, int(query.get("limit", ["30"])[0]))))
        elif parts == ["internet", "config"] and method == "PUT":
            data = self.body(env)
            if set(data) - {"enabled", "expected_revision"} or "enabled" not in data:
                raise ValueError("Expected enabled and optional expected_revision")
            result = self.internet.configure({"enabled": data["enabled"]}, expected_revision=data.get("expected_revision"))
        elif parts in (["internet", "probe"], ["internet", "search"], ["internet", "fetch"]) and method == "POST":
            data = self.body(env)
            action = parts[1]
            allowed = {"refresh"} | ({"query", "max_results"} if action == "search" else {"url"} if action == "fetch" else set())
            if set(data) - allowed or type(data.get("refresh", action == "probe")) is not bool:
                raise ValueError("Invalid public web request fields or refresh flag")
            if action == "probe":
                result = self.internet.probe(refresh=data.get("refresh", True))
            elif action == "search":
                if type(data.get("max_results", 5)) is not int or not 1 <= data.get("max_results", 5) <= 5:
                    raise ValueError("max_results must be an integer from 1 to 5")
                result = self.internet.search(data.get("query"), limit=data.get("max_results", 5), refresh=data.get("refresh", False))
            else:
                result = self.internet.fetch(data.get("url"), refresh=data.get("refresh", False))
        elif parts == ["briefing"] and method == "GET":
            result = self.briefing.status()
            result["location"] = self.location_status()
        elif parts == ["location"] and method == "GET":
            from .location import st_louis_reference
            result = {"last_device_location": self.location_status(), "city_reference": st_louis_reference()}
        elif parts == ["location", "detect"] and method == "POST":
            if self.body(env):
                raise ValueError("Location detection accepts an empty object")
            from .location import detect_windows_location
            result = detect_windows_location()
            if result.get("ok") and not result.get("stale"):
                self.store.set_setting("device_location", result)
        elif parts == ["location", "browser"] and method == "POST":
            from .location import validate_browser_location
            result = validate_browser_location(self.body(env))
            if result.get("stale"):
                raise ValueError("The browser location is stale; request a fresh position")
            self.store.set_setting("device_location", result)
        elif parts == ["briefing", "config"] and method == "PUT":
            result = self.briefing.configure(self.body(env))
        elif parts == ["briefing", "run"] and method == "POST":
            data = self.body(env)
            if set(data) - {"request_id"}:
                raise ValueError("Only request_id is accepted")
            if self.engine.is_paused():
                raise HTTPError(409, "Autonomy is paused; resume before creating a brief")
            from .briefing import BriefingConflict
            try:
                result = self.briefing.run(request_id=data.get("request_id"), collect=True)
            except BriefingConflict as error:
                raise HTTPError(409, str(error)) from None
        elif parts == ["briefing", "history"] and method == "GET":
            result = self.briefing.history()
        elif parts == ["briefing", "report"] and method == "GET":
            request_id = query.get("request_id", [""])[0]
            receipt = next((item for item in self.briefing.history()["runs"] if item["request_id"] == request_id), None)
            if not receipt or receipt.get("status") != "completed":
                raise KeyError()
            result = self.briefing_content(receipt)
        elif parts == ["voice", "wake"] and method == "GET":
            result = self.wake_status()
        elif parts == ["voice", "wake", "start"] and method == "POST":
            data = self.body(env)
            if set(data) - {"mode", "remember", "session_hours"} or type(data.get("remember", False)) is not bool:
                raise ValueError("Expected mode, session_hours and boolean remember")
            if data.get("remember") and data.get("mode", "pc") != "pc":
                raise ValueError("Startup listening currently supports only the PC microphone")
            if data.get("remember") and data.get("session_hours") is not None:
                raise ValueError("Remembered PC listening runs until stopped; omit session_hours or use null")
            hours = None if data.get("remember") else data.get("session_hours", 8)
            self.wake_voice.start(mode=data.get("mode", "pc"), session_hours=hours)
            try:
                self.store.set_setting("wake_preferences", {"enabled": data.get("remember", False), "mode": "pc", "session_hours": hours})
            except Exception:
                self.wake_voice.stop()
                raise
            result = self.wake_status()
        elif parts == ["voice", "wake", "stop"] and method == "POST":
            self.body(env)
            try:
                self.wake_voice.stop()
            finally:
                self.disable_wake_startup()
            result = self.wake_status()
        elif parts == ["voice", "wake", "accept"] and method == "POST":
            data = self.body(env)
            if set(data) != {"text", "lease", "sequence"}:
                raise ValueError("Expected text, lease and sequence")
            result = self.wake_voice.accept(data["text"], source="browser", lease=data["lease"], sequence=data["sequence"])
        elif parts == ["voice", "wake", "suppress"] and method == "POST":
            data = self.body(env)
            if set(data) != {"seconds"}:
                raise ValueError("Expected seconds")
            self.wake_voice.suppress(data["seconds"])
            result = self.wake_status()
        elif len(parts) == 2 and parts[0] == "runs" and method == "GET":
            result = self.store.details(parts[1])
            if not result:
                raise KeyError()
            result["run"] = self.public_run(result["run"])
        elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel" and method == "POST":
            self.body(env)
            if not self.store.run(parts[1]):
                raise KeyError()
            self.store.cancel(parts[1])
            result = {"ok": True}
        elif len(parts) == 2 and parts[0] == "approvals" and method == "POST":
            data = self.body(env)
            if data.get("decision") not in {"approve", "reject"}:
                raise ValueError("decision must be approve or reject")
            self.store.decide(parts[1], {"approve": "approved", "reject": "rejected"}[data["decision"]])
            self.engine.wake.set()
            result = {"ok": True}
        elif parts == ["control"] and method == "POST":
            data = self.body(env)
            if type(data.get("paused")) is not bool:
                raise ValueError("paused must be a boolean")
            self.store.set_setting("paused", data["paused"])
            self.engine.wake.set()
            result = {"paused": data["paused"]}
        elif parts == ["memory"] and method == "GET":
            result = {"memories": self.store.memories()}
        elif parts == ["memory"] and method == "POST":
            result, status = self.store.memory_add(text_field(self.body(env), "content", 4000)), 201
        elif len(parts) == 2 and parts[0] == "memory" and method == "DELETE":
            self.store.memory_delete(parts[1])
            result = {"ok": True}
        elif parts == ["knowledge"] and method == "GET":
            result = {"documents": self.store.documents()}
        elif parts == ["survival"] and method in {"GET", "POST"}:
            from .survival_tools import RevisionConflict
            with self.engine.tools._lock:
                if method == "POST":
                    try:
                        self.engine.tools.survival.execute("survival_records", self.body(env))
                    except RevisionConflict as error:
                        raise HTTPError(409, str(error)) from None
                result = {**self.engine.tools.survival.overview(), "library": self.engine.tools.references.catalog()}
        elif parts == ["survival", "library"] and method == "GET":
            result = self.engine.tools.references.catalog(query.get("q", [""])[0], query.get("topic", [""])[0])
        elif len(parts) == 4 and parts[:2] == ["survival", "library"] and parts[3] == "download" and method == "GET":
            payload, filename = self.engine.tools.references.download(parts[2])
            env["ariadne.download_name"] = filename
            return 200, payload, "application/octet-stream"
        elif parts == ["survival", "books"] and method == "GET":
            result = {"books": self.engine.tools.references.books.books()}
        elif parts in (["survival", "books"], ["survival", "books", "preview"]) and method == "POST":
            data = self.body(env)
            from .tools import _object
            _object(data, {"source_id", "path", "sha256", "title"}, {"source_id", "path"})
            if not self.import_lock.acquire(blocking=False):
                raise HTTPError(429, "Another document operation is in progress")
            try:
                source_id, relative = text_field(data, "source_id", 64), text_field(data, "path", 1024)
                if parts[-1] == "preview":
                    result = self.engine.tools.references.books.preview(source_id, relative)
                else:
                    result, status = self.engine.tools.references.books.import_book(source_id, relative,
                        data.get("sha256"), text_field(data, "title", 200)), 201
            finally:
                self.import_lock.release()
        elif len(parts) == 3 and parts[:2] == ["survival", "books"] and method == "GET":
            result = self.engine.tools.references.books.read(parts[2], int(query.get("section", ["1"])[0]))
        elif parts == ["knowledge", "upload"] and method == "POST":
            data = self.body(env, limit=14_000_000)
            if not self.import_lock.acquire(blocking=False):
                raise HTTPError(429, "Another document operation is in progress")
            try:
                result, status = self.sources.upload(text_field(data, "name", 200), data.get("content_base64"), ocr=data.get("ocr", False)), 201
            finally:
                self.import_lock.release()
        elif parts == ["knowledge"] and method == "POST":
            data = self.body(env)
            result, status = self.store.document_add(text_field(data, "title", 200), text_field(data, "content", 500000)), 201
        elif len(parts) == 2 and parts[0] == "knowledge" and method == "DELETE":
            self.store.document_delete(parts[1])
            result = {"ok": True}
        elif parts == ["sources"] and method == "GET":
            result = {"sources": self.store.sources(), "policy": self.sources.policy()}
        elif parts == ["sources"] and method == "POST":
            data = self.body(env)
            result, status = {"source": self.sources.register(text_field(data, "name", 120), text_field(data, "path", 1024))}, 201
        elif len(parts) == 2 and parts[0] == "sources" and method == "DELETE":
            self.store.source_delete(parts[1])
            result = {"ok": True}
        elif len(parts) == 3 and parts[0] == "sources" and parts[2] == "files" and method == "GET":
            result = self.sources.listing(parts[1], query.get("directory", ["."])[0])
        elif len(parts) == 3 and parts[0] == "sources" and parts[2] in {"preview", "import"} and method == "POST":
            data = self.body(env)
            path = text_field(data, "path", 1024)
            if not self.import_lock.acquire(blocking=False):
                raise HTTPError(429, "Another document operation is in progress")
            try:
                if parts[2] == "preview":
                    result = self.sources.preview(parts[1], path, ocr=data.get("ocr", False))
                else:
                    result = self.sources.import_reference(parts[1], path, data.get("sha256"), ocr=data.get("ocr", False), review_identity=data.get("review_identity"))
            finally:
                self.import_lock.release()
        elif parts == ["capabilities"] and method == "GET":
            result = catalog(self)
        elif parts == ["automation"] and method == "GET":
            result = self.engine.tools.automation.overview()
        elif parts == ["automation"] and method == "PUT":
            result = self.engine.tools.automation.configure(self.body(env, limit=32_768))
        elif parts == ["automation", "apps"] and method == "POST":
            data = self.body(env, limit=5000)
            if set(data) != {"id", "url", "description"}:
                raise ValueError("Provide an app id, URL and description.")
            with self.engine.tools._lock:
                config = self.engine.tools.automation.config()
                if any(app["id"] == data["id"] for app in config["apps"]):
                    raise ValueError("That local app id is already registered.")
                config["apps"].append(data)
                result = self.engine.tools.automation.configure(config)
        elif len(parts) == 3 and parts[:2] == ["automation", "apps"] and method == "DELETE":
            with self.engine.tools._lock:
                config = self.engine.tools.automation.config()
                config["apps"] = [app for app in config["apps"] if app["id"] != parts[2]]
                result = self.engine.tools.automation.configure(config)
        elif parts == ["mcp"] and method == "GET":
            result = self.mcp_admin.overview()
        elif parts == ["mcp", "servers"] and method == "POST":
            data = self.body(env, limit=10_000)
            with self.mcp_lock:
                result, status = self.mcp_admin.add(data), 201
        elif len(parts) == 3 and parts[:2] == ["mcp", "servers"] and method == "DELETE":
            with self.mcp_lock:
                result = self.mcp_admin.remove(parts[2])
        elif len(parts) == 3 and parts[0] == "mcp" and parts[2] in {"discover", "select"} and method == "POST":
            data = self.body(env, limit=10_000)
            if not self.mcp_lock.acquire(blocking=False):
                raise HTTPError(429, "Another local MCP configuration operation is in progress")
            try:
                result = self.mcp_admin.discover(parts[1]) if parts[2] == "discover" else self.mcp_admin.select(parts[1], data)
            finally:
                self.mcp_lock.release()
        elif parts == ["procedures"] and method == "GET":
            result = {**self.procedures.catalog(), "runs": self.procedures.history()["runs"]}
        elif parts == ["procedures", "runs"] and method == "GET":
            result = self.procedures.history()
        elif len(parts) == 3 and parts[0] == "procedures" and parts[2] == "run" and method == "POST":
            from .tools import _object
            from .procedures import ProcedureConflict
            data = self.body(env)
            _object(data, {"request_id"}, {"request_id"})
            try:
                result, status = {"run": self.procedures.run(parts[1], data["request_id"])}, 201
            except ProcedureConflict as error:
                raise HTTPError(409, str(error)) from None
        elif parts == ["routines"] and method == "GET":
            result = {**self.routines.catalog(), "routines": self.routines.definitions(), "runs": self.routines.runs()}
        elif parts == ["routines"] and method == "POST":
            data = self.body(env)
            from .tools import _object
            _object(data, {"name", "steps", "interval_minutes", "enabled"}, {"name", "steps"})
            result, status = {"routine": self.routines.save(text_field(data, "name", 120), data["steps"], data.get("interval_minutes", 1440), data.get("enabled", False))}, 201
            self.engine.wake.set()
        elif len(parts) == 2 and parts[0] == "routines" and method in {"PUT", "DELETE"}:
            if method == "DELETE":
                self.routines.delete(parts[1])
                result = {"ok": True}
            else:
                data = self.body(env)
                from .tools import _object
                _object(data, {"name", "steps", "interval_minutes", "enabled", "expected_revision"}, {"name", "steps", "expected_revision"})
                result = {"routine": self.routines.save(text_field(data, "name", 120), data["steps"], data.get("interval_minutes", 1440), data.get("enabled", False), routine_id=parts[1], expected_revision=data["expected_revision"])}
            self.engine.wake.set()
        elif len(parts) == 3 and parts[0] == "routines" and parts[2] == "run" and method == "POST":
            data = self.body(env)
            from .tools import _object
            from .routines import RoutineConflict
            _object(data, {"request_id"}, {"request_id"})
            try:
                result, status = {"run": self.routines.enqueue(parts[1], data["request_id"])}, 201
            except RoutineConflict as error:
                raise HTTPError(409, str(error)) from None
            self.engine.wake.set()
        elif len(parts) == 3 and parts[:2] == ["routines", "runs"] and method == "GET":
            result = {"run": self.routines.detail(parts[2])}
        elif len(parts) == 4 and parts[:2] == ["routines", "runs"] and parts[3] in {"cancel", "verify"} and method == "POST":
            data = self.body(env)
            from .tools import _object
            _object(data, set(), set())
            from .routines import RoutineConflict
            try:
                result = {"run": (self.routines.cancel(parts[2]) if parts[3] == "cancel" else self.routines.verify(parts[2]))}
            except RoutineConflict as error:
                raise HTTPError(409, str(error)) from None
            self.engine.wake.set()
        elif parts == ["workflows"] and method == "GET":
            result = {"workflows": self.store.workflows()}
        elif parts == ["workflows"] and method == "POST":
            data = self.body(env)
            result, status = {"workflow": self.store.workflow_save(text_field(data, "name", 120), text_field(data, "prompt", 16000), validate_checks(data.get("checks", []), self.engine.tools), agent_id=data.get("agent_id"))}, 201
        elif len(parts) == 2 and parts[0] == "workflows" and method == "PUT":
            data = self.body(env)
            result = {"workflow": self.store.workflow_save(text_field(data, "name", 120), text_field(data, "prompt", 16000), validate_checks(data.get("checks", []), self.engine.tools), parts[1], agent_id=data.get("agent_id"))}
        elif len(parts) == 2 and parts[0] == "workflows" and method == "DELETE":
            self.store.workflow_delete(parts[1])
            result = {"ok": True}
        elif len(parts) == 3 and parts[0] == "workflows" and parts[2] == "run" and method == "POST":
            self.body(env)
            result, status = {"run": self.public_run(self.store.workflow_run(parts[1]))}, 201
            self.engine.wake.set()
        elif parts == ["schedules"] and method == "GET":
            result = {"schedules": self.store.schedules()}
        elif parts == ["schedules"] and method == "POST":
            data = self.body(env)
            interval = data.get("interval_minutes")
            if type(interval) is not int or not 5 <= interval <= 525600:
                raise ValueError("interval_minutes must be an integer between 5 and 525600")
            if "workflow_id" in data:
                from .tools import _object
                _object(data, {"name", "workflow_id", "interval_minutes"}, {"name", "workflow_id", "interval_minutes"})
                result, status = self.store.schedule_add(text_field(data, "name", 120), None, interval, workflow_id=data["workflow_id"]), 201
            else:
                result, status = self.store.schedule_add(text_field(data, "name", 120), text_field(data, "prompt", 16000), interval, agent_id=data.get("agent_id"), checks=validate_checks(data.get("checks", []), self.engine.tools)), 201
        elif len(parts) == 2 and parts[0] == "schedules" and method in {"POST", "DELETE"}:
            if method == "DELETE":
                self.store.schedule_delete(parts[1])
            else:
                enabled = self.body(env).get("enabled")
                if type(enabled) is not bool:
                    raise ValueError("enabled must be a boolean")
                self.store.schedule_update(parts[1], enabled)
            result = {"ok": True}
        elif parts == ["artifacts"] and method == "GET":
            registry = self.engine.tools
            path = registry._path(query.get("path", [""])[0])
            with registry._lock, registry._directory(path.parent) as directory:
                with os.fdopen(registry._open_read(path, directory), "rb") as source:
                    before = os.fstat(source.fileno())
                    if before.st_size > 20_000_000:
                        raise HTTPError(413, "Artifact download exceeds 20 MB.")
                    payload = source.read(20_000_001)
                    after = os.fstat(source.fileno())
                    if len(payload) > 20_000_000 or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise HTTPError(409, "Artifact changed while it was being read.")
            env["ariadne.download_name"] = path.name
            return 200, payload, "application/octet-stream"
        elif parts == ["files"] and method == "GET":
            if "path" in query:
                result = self.engine.tools.execute("file_read", {"path": query["path"][0]})
                if not result.get("ok"):
                    raise ValueError(result.get("error", "Cannot read file"))
            else:
                directory = query.get("directory", ["."])[0]
                listing = self.engine.tools.execute("file_list", {"path": directory})
                if not listing.get("ok"):
                    raise ValueError(listing.get("error", "Cannot list directory"))
                prefix = "" if directory == "." else directory.rstrip("/") + "/"
                result = {"files": [{**entry, "path": prefix + entry["name"]} for entry in listing.get("entries", [])], "truncated": listing.get("truncated", False)}
        elif parts == ["connectors"] and method == "GET":
            result = {"connectors": self.engine.tools.list_connectors(), "tools": [
                {"name": s["function"]["name"], "description": s["function"]["description"],
                 "approval_required": self.engine.tools.policy(s["function"]["name"], {}) == "approval"}
                for s in self.engine.tools.schemas()]}
        elif parts == ["settings"] and method == "GET":
            result = {"model": self.store.setting("model", self.settings.model), "max_steps": self.settings.max_steps}
        elif parts == ["settings"] and method == "POST":
            model = text_field(self.body(env), "model", 150)
            available = self.engine.model.status()
            if model not in {m["name"] for m in available.get("models", [])}:
                raise ValueError("Choose an installed model. Provisioning models is a separate operator step.")
            self.store.set_setting("model", model)
            result = {"model": model}
        elif parts == ["voice", "transcribe"] and method == "POST":
            if not self.voice_lock.acquire(blocking=False):
                raise HTTPError(429, "Another voice operation is in progress")
            try:
                result = {"text": self.voice.transcribe(self.body(env, raw=True))}
            finally:
                self.voice_lock.release()
        elif parts == ["voice", "speak"] and method == "POST":
            text = text_field(self.body(env), "text", 4000)
            if not self.voice_lock.acquire(blocking=False):
                raise HTTPError(429, "Another voice operation is in progress")
            try:
                backend = self.store.setting("speech_backend", "system")
                audio = self.voice.speak(text) if backend == "system" else self.voice.speak(text, backend=backend)
                return 200, audio, "audio/wav"
            finally:
                self.voice_lock.release()
        elif parts == ["voice", "preferences"] and method == "PUT":
            data = self.body(env)
            if set(data) != {"backend"} or data["backend"] not in {"system", "natural"}:
                raise ValueError("Choose system or natural speech.")
            if data["backend"] == "natural" and not self.engine.tools.media.status().get("backends", {}).get("kokoro", {}).get("ready"):
                raise ValueError("Natural speech assets are not ready.")
            self.store.set_setting("speech_backend", data["backend"])
            result = {"speech_backend": data["backend"]}
        else:
            raise HTTPError(404, "API route not found")
        return status, result, "application/json"
