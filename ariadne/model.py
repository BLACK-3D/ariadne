"""A bounded, proxy-free adapter to a local Ollama server."""
import json
import base64
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse

# Serialize local GPU consumers across mission threads and optional generators.
GPU_LOCK = threading.RLock()
KEEP_ALIVE = "15m"
CONTEXT_TOKENS = 16384


class ModelError(RuntimeError):
    def __init__(self, message, usage=None):
        super().__init__(message)
        self.usage = usage


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Ollama:
    def __init__(self, url, timeout=180):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("OLLAMA URL must be an HTTP(S) address without credentials")
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())

    def request(self, path, payload=None, timeout=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.client.open(request, timeout=timeout or self.timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ModelError("Model response exceeded the 2 MB limit")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ModelError("Model returned an invalid response")
                return result
        except (urllib.error.URLError, OSError, ValueError) as error:
            if isinstance(error, ModelError):
                raise
            raise ModelError("Local model service unavailable or returned invalid data. Check Ollama and the selected model.") from None

    def status(self):
        try:
            result = self.request("/api/tags", timeout=2)
            return {"available": True, "models": [{"name": m.get("name"), "size": m.get("size"), "digest": m.get("digest")} for m in result.get("models", [])]}
        except ModelError as error:
            return {"available": False, "models": [], "error": str(error)}

    def capabilities(self, model):
        """Ask the installed model manifest; names alone do not establish vision support."""
        result = self.request("/api/show", {"model": model}, timeout=10)
        capabilities = result.get("capabilities", [])
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise ModelError("Model returned invalid capability metadata")
        return capabilities

    def image_chat(self, model, prompt, images):
        """Images are normalized PNG bytes from the bounded media worker, never paths/URLs."""
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 8000:
            raise ModelError("Image question must contain 1 to 8000 characters")
        if (not isinstance(images, list) or not 1 <= len(images) <= 3
                or any(type(item) is not bytes or not item or len(item) > 3_000_000 for item in images)
                or sum(map(len, images)) > 6_000_000):
            raise ModelError("Provide one to three normalized images within the image byte limits")
        if "vision" not in self.capabilities(model):
            raise ModelError("The selected installed model does not advertise image understanding")
        return self.chat(model, [{"role": "user", "content": prompt,
            "images": [base64.b64encode(item).decode("ascii") for item in images]}], [])

    def chat(self, model, messages, tools):
        with GPU_LOCK:
            return self._chat(model, messages, tools)

    def chat_fast(self, model, messages, tools):
        """A small conversational payload; workspace missions retain their budget."""
        with GPU_LOCK:
            return self._chat(model, messages, tools, output_tokens=384, profile="conversation")

    def warm(self, model):
        """Load the selected installed model once, without generating a token.

        Never queue behind a real GPU consumer, poll, download or unload another
        model. Keeping the same context allocation avoids a profile reload.
        """
        if not GPU_LOCK.acquire(blocking=False):
            return {"state": "busy", "model": model, "inference_tokens": 0}
        try:
            resident = self.request("/api/ps", timeout=3).get("models", [])
            if any(type(row) is dict and row.get("name", row.get("model")) == model
                   and row.get("context_length") == CONTEXT_TOKENS for row in resident):
                return {"state": "ready", "model": model, "inference_tokens": 0, "already_resident": True}
            if any(type(row) is dict and row.get("name", row.get("model")) != model for row in resident):
                return {"state": "busy", "model": model, "inference_tokens": 0,
                        "reason": "Another model is resident; automatic loading was skipped."}
            result = self.request("/api/chat", {"model": model, "messages": [], "stream": False,
                "keep_alive": KEEP_ALIVE, "options": {"num_ctx": CONTEXT_TOKENS}}, timeout=90)
            if result.get("eval_count", 0) not in (0, None):
                raise ModelError("Model warm-up unexpectedly generated output.")
            return {"state": "ready", "model": model, "inference_tokens": 0,
                    "load_seconds": round(result.get("load_duration", 0)/1e9, 6), "keep_alive": KEEP_ALIVE}
        finally:
            GPU_LOCK.release()

    def _chat(self, model, messages, tools, *, output_tokens=2048, profile="mission"):
        payload = {"model": model, "messages": messages, "tools": tools, "stream": False,
                   "options": {"temperature": 0.2, "num_ctx": CONTEXT_TOKENS, "num_predict": output_tokens}, "keep_alive": KEEP_ALIVE}
        if model.split(":")[0].lower() in {"qwen3", "qwen3.5"}:
            payload["think"] = False
        result = self.request("/api/chat", payload)
        usage = {"model": model, "prompt_tokens": result.get("prompt_eval_count", 0), "output_tokens": result.get("eval_count", 0),
                 "duration_seconds": round(result.get("total_duration", 0) / 1e9, 3), "profile": profile,
                 "cached_prompt_tokens": result.get("prompt_eval_cached_count"),
                 "load_seconds": round(result["load_duration"]/1e9, 6) if "load_duration" in result else None,
                 "prefill_seconds": round(result["prompt_eval_duration"]/1e9, 6) if "prompt_eval_duration" in result else None,
                 "decode_seconds": round(result["eval_duration"]/1e9, 6) if "eval_duration" in result else None}
        if result.get("done_reason") == "length":
            raise ModelError("Model reached its output budget before finishing. Narrow the mission or select a stronger installed model.", usage=usage)
        message = result.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ModelError("Model did not return an assistant message")
        content, calls = message.get("content", ""), message.get("tool_calls", [])
        if not isinstance(content, str) or len(content) > 64000 or not isinstance(calls, list) or len(calls) > 8:
            raise ModelError("Model response exceeded message/tool limits")
        clean = []
        for call in calls:
            function = call.get("function", {}) if isinstance(call, dict) else {}
            if not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), dict):
                raise ModelError("Model returned invalid tool arguments")
            clean.append({"function": {"name": function["name"], "arguments": function["arguments"]}})
        return {"role": "assistant", "content": content, **({"tool_calls": clean} if clean else {})}, usage
