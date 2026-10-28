"""Disposable browser for one reviewed local app. No JavaScript evaluation API."""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path
from ariadne.automation_tools import local_url, within_app
from ariadne.tools import _bounded_opener


def main():
    payload = json.loads(sys.stdin.buffer.read(65_537))
    prefix = local_url(payload["prefix"])
    url = local_url(payload["url"])
    if not within_app(url, prefix):
        raise ValueError("Unregistered browser path.")
    dependencies = _allowed_path(ROOT / "runtime" / "automation" / "python")
    sys.path.insert(0, str(dependencies))
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_allowed_path(ROOT / "runtime" / "automation" / "browsers"))
    from playwright.sync_api import sync_playwright
    mutated = False
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True, args=["--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-extensions", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
            context = browser.new_context(accept_downloads=False, service_workers="block", offline=True, viewport={"width": 1280, "height": 800}, permissions=[])
            context.set_default_timeout(5000)
            blocked = []

            def route(request):
                # Fulfill through a bounded loopback-only broker. Continuing a
                # browser request can follow redirects outside route filtering.
                import urllib.request
                import urllib.error
                candidate = request.request.url
                if not within_app(candidate, prefix) or request.request.method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                    blocked.append(request.request.resource_type)
                    request.abort()
                    return
                payload = request.request.post_data_buffer
                if payload and len(payload) > 65_536:
                    blocked.append("oversized_request")
                    request.abort()
                    return
                headers = {k: v for k, v in request.request.headers.items() if k.lower() not in {"host", "content-length", "connection", "proxy-authorization", "accept-encoding"}}
                headers["Accept-Encoding"] = "identity"
                try:
                    with _bounded_opener(8) as (opener, expired):
                        with opener.open(urllib.request.Request(candidate, data=payload, headers=headers, method=request.request.method), timeout=8) as response:
                            body = response.read(2_000_001)
                            if expired.is_set() or len(body) > 2_000_000:
                                raise ValueError("Browser response exceeds its byte or time bound.")
                            response_headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
                            request.fulfill(status=response.status, headers=response_headers, body=body)
                except urllib.error.HTTPError as error:
                    if 300 <= error.code < 400:
                        blocked.append("redirect")
                        request.fulfill(status=409, content_type="text/plain", body="Local app redirects are disabled in this adapter.")
                    else:
                        request.fulfill(status=error.code, content_type="text/plain", body="Local app returned an HTTP error.")
                except (OSError, ValueError, urllib.error.URLError):
                    blocked.append("failed_request")
                    request.abort()

            context.route("**/*", route)
            context.route_web_socket("**/*", lambda socket: socket.close())
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.dismiss())
            context.on("page", lambda new_page: new_page.close() if new_page != page else None)
            # A GET can execute page scripts or have server-side effects too.
            # Failure after navigation begins requires reconciliation.
            mutated = True
            response = page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            if response is None or response.status >= 400:
                raise ValueError("Local app did not return a successful page.")
            actions = payload["actions"]
            if type(actions) is not list or len(actions) > 12:
                raise ValueError("Invalid browser action list.")
            for action in actions:
                target = page.locator(action["selector"])
                if target.count() != 1:
                    raise ValueError("Each selector must identify exactly one element.")
                mutated = True
                if action["op"] == "click":
                    target.click()
                elif action["op"] == "fill":
                    target.fill(action["value"])
                elif action["op"] == "select":
                    target.select_option(action["value"])
                else:
                    raise ValueError("Unsupported browser action.")
            result = {"ok": True, "url": page.url, "title": page.title()[:500], "text": page.locator("body").inner_text()[:24_000], "actions_completed": len(actions), "blocked_requests": len(blocked), "scope": "reviewed local app; disposable profile; request filtering is not an OS sandbox"}
            context.close()
            browser.close()
        return result
    except Exception:
        return {"ok": False, "error": "The local browser task failed; inspect the app before retrying actions.", "uncertain": mutated}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
