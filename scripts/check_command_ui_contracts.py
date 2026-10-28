"""Check command-center HTML, safety handlers and exact static WSGI responses.

Usage: python scripts/check_command_ui_contracts.py [--receipt PATH]

Uses disposable state inside the repository and calls WSGI directly: no model, network
listener, microphone, production database, browser session or external request.
The reviewed baseline fixture is checked in; release ZIPs and model/runtime
downloads are not needed. An optional receipt records the checked source hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []
        self.aria_hidden = {}

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        self.elements.append((tag, attributes))
        if "id" in attributes:
            self.aria_hidden[attributes["id"]] = attributes.get("aria-hidden") == "true" or any(a.get("aria-hidden") == "true" for _, a in self.stack)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class NoModel:
    def chat(self, *args):
        raise AssertionError("UI contracts must not execute a model")

    def status(self):
        return {"available": False, "models": []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, help="Optional JSON result path; no receipt is written by default.")
    args = parser.parse_args(argv)
    from ariadne.config import Settings
    from ariadne.engine import Engine
    from ariadne.server import Application
    from ariadne.store import Store

    document = Document()
    html = (ROOT / "ariadne/static/index.html").read_text(encoding="utf-8")
    document.feed(html)
    checks = []
    ids = Counter(attributes["id"] for _, attributes in document.elements if "id" in attributes)
    assert all(count == 1 for count in ids.values()), [key for key, count in ids.items() if count != 1]
    fixture_path = ROOT / "tests/fixtures/command_ui_baseline.json"
    baseline = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == 1
    previous_ids = set(baseline["required_legacy_ids"])
    assert previous_ids <= ids.keys(), sorted(previous_ids - ids.keys())
    checks.append("HTML IDs remain unique and every reviewed legacy element ID is preserved")
    required = {
        "main-content", "pairing-overlay", "pairing-form", "pairing-submit", "pairing-error", "access-token",
        "mission-form", "mission-prompt", "mission-agent", "send-mission", "new-mission", "cancel-run",
        "mission-title", "mission-status", "mission-checks", "message-list", "conversation", "approval-list",
        "event-list", "pause-toggle", "pause-label", "lock-button", "voice-record", "wake-start", "wake-stop",
        "internet-toggle", "internet-probe", "sidebar", "sidebar-scrim", "menu-toggle", "recent-runs",
        "command-state", "command-state-detail", "command-phase-announcement", "command-presence",
        "command-refresh", "command-review-open", "command-new-mission", "command-model", "command-mic",
        "command-network", "command-active", "command-review", "command-next-brief",
        "command-home", "command-log-toggle", "directory-close", "execution-trail", "execution-trail-summary",
    }
    assert required <= ids.keys(), sorted(required - ids.keys())
    checks.append("auth, mission, approval, pause, microphone, Internet and command hooks are preserved")
    for tag, attributes in document.elements:
        if tag == "label" and "for" in attributes:
            assert attributes["for"] in ids, attributes
        for relation in ("aria-labelledby", "aria-describedby", "aria-controls"):
            for identifier in attributes.get(relation, "").split():
                assert identifier in ids, (relation, identifier)
    checks.append("static labels and ARIA references resolve to existing controls")
    shell = next(attributes for _, attributes in document.elements if "app-shell" in attributes.get("class", "").split())
    assert "inert" in shell
    main = next(attributes for tag, attributes in document.elements if tag == "main" and attributes.get("id") == "main-content")
    assert main.get("tabindex") == "-1"
    assert any(tag == "a" and attributes.get("href") == "#main-content" for tag, attributes in document.elements)
    checks.append("unpaired shell starts inert and keyboard skip target remains focusable")
    directory = next(attributes for _, attributes in document.elements if attributes.get("id") == "sidebar")
    assert "inert" in directory and "hidden" in directory
    toggle = next(attributes for _, attributes in document.elements if attributes.get("id") == "menu-toggle")
    assert toggle.get("aria-expanded") == "false" and "aria-controls" in toggle
    checks.append("closed resource directory begins hidden, inert and accurately disclosed")
    scripts = [(attributes["src"], attributes) for tag, attributes in document.elements if tag == "script" and "src" in attributes]
    styles = [attributes["href"] for tag, attributes in document.elements if tag == "link" and attributes.get("rel") == "stylesheet"]
    assert all(not urlsplit(url).netloc and url.startswith("/") for url, _ in scripts)
    assert all(not urlsplit(url).netloc and url.startswith("/") for url in styles)
    names = [Path(urlsplit(url).path).name for url, _ in scripts]
    assert names.index("command.js") < names.index("app.js")
    assert all("defer" in attributes for _, attributes in scripts)
    checks.append("local deferred command module loads before app attachment without remote dependencies")
    app_source = (ROOT / "ariadne/static/app.js").read_text(encoding="utf-8")
    command_source = (ROOT / "ariadne/static/command.js").read_text(encoding="utf-8")
    command_ids = set(re.findall(r'(?:put|document\.getElementById)\("([A-Za-z][A-Za-z0-9_-]*)"', command_source))
    assert command_ids <= ids.keys(), sorted(command_ids - ids.keys())
    for identifier in ("command-mic", "command-network"):
        assert not document.aria_hidden[identifier], identifier+" is meaningful state hidden from the accessibility tree"
    checks.append("every command output exists and meaningful microphone/network state remains accessible")
    bound_ids = set(re.findall(r'\$\([\'\"]#([A-Za-z][A-Za-z0-9_-]*)[\'\"]\)\.addEventListener', app_source))
    assert bound_ids <= ids.keys(), sorted(bound_ids - ids.keys())
    checks.append("every statically bound app control exists in the redesigned document")
    preserved = baseline["critical_handler_sha256"]
    def function_section(source, name):
        start = re.search(r"^(?:async )?function "+re.escape(name)+r"\(", source, re.M)
        assert start, name
        following = re.search(r"^(?:async )?function ", source[start.end():], re.M)
        raw = source[start.start():start.end()+following.start()] if following else source[start.start():]
        return raw.replace("\r\n", "\n").strip()+"\n"
    changed = [name for name, expected in preserved.items()
               if hashlib.sha256(function_section(app_source, name).encode("utf-8")).hexdigest() != expected]
    assert not changed, "Critical handlers differ from the reviewed baseline; inspect changes and deliberately update the fixture: "+", ".join(changed)
    checks.append("reviewed auth/approval/launch/cancel/pause/model-selection/recording handler hashes match")
    views = {attributes["id"].removeprefix("view-") for tag, attributes in document.elements
             if tag == "section" and "view" in attributes.get("class", "").split() and attributes.get("id", "").startswith("view-")}
    nav_views = {attributes["data-view"] for _, attributes in document.elements if "data-view" in attributes}
    assert {"missions", "agents", "briefing", "internet", "workflows", "knowledge", "survival", "memory", "schedules", "files", "connectors", "settings"} <= views
    assert views <= nav_views
    checks.append("all existing product views remain reachable through navigation")
    css_paths = [ROOT / "ariadne/static" / Path(urlsplit(url).path).name for url in styles]
    css = "\n".join(path.read_text(encoding="utf-8") for path in css_paths)
    assert "prefers-reduced-motion" in css and "animation:none!important" in re.sub(r"\s+", "", css)
    assert "focus-visible" in css
    checks.append("motion reduction and visible keyboard focus rules remain present")

    static_responses = []
    with tempfile.TemporaryDirectory(prefix=".command-ui-review-", dir=ROOT) as temporary:
        settings = Settings(data_dir=Path(temporary), api_token="isolated-ui-contract-fixture")
        store = Store(settings.data_dir / "state.sqlite3")
        app = Application(settings, store, Engine(settings, store, NoModel()))
        try:
            for url, _ in scripts:
                for requested in (url,):
                    result = {}
                    def start(status, headers):
                        result.update(status=int(status.split()[0]), headers=dict(headers))
                    payload = b"".join(app({"HTTP_HOST": "localhost:8080", "PATH_INFO": requested,
                        "REQUEST_METHOD": "GET", "wsgi.input": io.BytesIO()}, start))
                    assert result["status"] == 200, (requested, result)
                    assert payload == (ROOT / "ariadne/static" / Path(urlsplit(url).path).name).read_bytes()
                    assert "default-src 'self'" in result["headers"]["Content-Security-Policy"]
                    static_responses.append(requested)
            for url in styles:
                result = {}
                payload = b"".join(app({"HTTP_HOST": "localhost:8080", "PATH_INFO": url,
                    "REQUEST_METHOD": "GET", "wsgi.input": io.BytesIO()}, start))
                assert result["status"] == 200, (url, result)
                assert payload == (ROOT / "ariadne/static" / Path(urlsplit(url).path).name).read_bytes()
                assert result["headers"]["Content-Type"].startswith("text/css")
                static_responses.append(url)
        finally:
            app.stop_services()
    checks.append("actual WSGI routes serve all scripts/styles with exact bytes and existing CSP")
    paths = ["ariadne/static/index.html", "ariadne/static/app.js", "ariadne/static/command.js", "ariadne/server.py"]
    paths.extend(["scripts/check_command_ui_contracts.py", "tests/fixtures/command_ui_baseline.json"])
    paths.extend(str(path.relative_to(ROOT)).replace("\\", "/") for path in css_paths)
    receipt = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "passed": len(checks), "checks": checks,
        "static_responses": static_responses, "html_ids": len(ids), "bound_control_ids": len(bound_ids),
        "prior_html_ids_preserved": len(previous_ids),
        "accepted_baseline_version": baseline["accepted_product_version"],
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in dict.fromkeys(paths)},
        "scope": "Static HTML/ARIA/event-hook contracts and direct WSGI asset responses. No live keyboard/layout or screen-reader certification."}
    result = {"passed": len(checks), "failures": 0}
    if args.receipt:
        destination = args.receipt.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(receipt, indent=2)+"\n", encoding="utf-8")
        result["receipt"] = str(destination)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
