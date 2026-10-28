"""Fixed public-HTTPS text worker. Read one bounded JSON request and exit."""
import json
import os
import sys
from pathlib import Path


def main():
    if os.environ.get("ARIADNE_INTERNET_WORKER") != "1":
        raise SystemExit("This worker is launched only by Ariadne's enabled Internet service.")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ariadne.internet import MAX_OUTPUT, worker_request
    request = sys.stdin.buffer.readline(8193)
    if len(request) > 8192 or not request.endswith(b"\n"):
        raise SystemExit("Request exceeds its limit.")
    try:
        value = json.loads(request)
        result = worker_request(value)
    except Exception as error:
        result = {"ok": False, "operation": "request", "sources": [], "error": str(error)[:500]}
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_OUTPUT:
        payload = b'{"ok":false,"sources":[],"error":"Internet output exceeds its limit."}'
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
