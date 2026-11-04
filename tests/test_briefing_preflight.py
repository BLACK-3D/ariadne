"""Offline briefing preflight, safe diagnostics and Windows console regressions.

All fixture files live in private Yikes staging. No owner request is replayed.
"""
from copy import deepcopy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from ariadne.briefing import BriefingService, BriefingConflict, _json
from ariadne.briefing_diagnostics import failure_detail, render_preflight
from ariadne.config import Settings
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from scripts import briefing as live_cli
from scripts import briefing_preflight as cli

ROOT = Path(__file__).absolute().parents[1]
TEMP_ROOT = ROOT / "staging/briefing_regressions_2026_11_02/fixtures"
NOW = datetime(2026, 10, 29, 11, 0, tzinfo=timezone.utc)
SECRET = "SYNTHETIC_CREDENTIAL_SENTINEL_DO_NOT_INCLUDE"


def stamp(seconds=0):
    return (NOW+timedelta(seconds=seconds)).isoformat()


def fixture():
    sources = []
    for kind, items in (
        ("weather", [{"title": "FIXTURE OBSERVATION", "summary": "Mostly cloudy; temperature: 58 °F; wind: 5 mph"}]),
        ("forecast", [{"title": "FIXTURE FORECAST", "summary": "Generated forecast detail", "ends": stamp(21600)}]),
        ("alerts", [{"title": "FIXTURE ALERT", "summary": "Generated official alert", "expires": stamp(3600), "expired": False}]),
        ("news", [{"title": "FIXTURE REGIONAL NOTICE", "summary": "Generated regional detail", "published_at": stamp(-90)}]),
    ):
        sources.append({"kind": kind, "title": "Generated "+kind, "url": "https://example.gov/"+kind,
                        "fetched_at": stamp(-60), "issued_at": stamp(-90), "status": "available",
                        "stale": False, "items": items})
    return {"schema": 1, "generated_at_utc": stamp(), "local_date": "2026-10-29",
        "config": {"region": {"name": "Fixture City ☃"}, "repository_updates": False},
        "activity": {"window_start_utc": stamp(-86400), "window_end_utc": stamp(),
                     "runs_count": 13, "events_count": 17, "new_memories": 2, "new_documents": 3,
                     "unresolved_approvals": [{"tool": "file_write"}], "procedures": [], "runs": [],
                     "coverage": "Synthetic recorded fixture activity only.", "details_truncated": False},
        "public_sources": {"collected_at": stamp(-60), "sources": sources, "warnings": []},
        "repository_updates": {"enabled": False}, "warnings": [], "model_calls": 0}


def svg_text(raw):
    return [node.text or "" for node in ET.fromstring(raw).iter() if node.tag.endswith("text")]


class BriefingPreflightPureTests(unittest.TestCase):
    def test_three_formats_share_frozen_source_facts_and_exact_input_hash(self):
        value = fixture();before = deepcopy(value)
        outputs, receipt = render_preflight(value)
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(set(outputs), {"brief.json", "brief.md", "brief.svg"})
        self.assertEqual(json.loads(outputs["brief.json"]), before)
        self.assertEqual(value, before)
        self.assertEqual(receipt["input_sha256"], hashlib.sha256(_json(before)).hexdigest())
        md = outputs["brief.md"].decode("utf-8")
        texts = svg_text(outputs["brief.svg"]);visible = " ".join(texts)
        for fact in ("Fixture City ☃", "2026-10-29", "FIXTURE FORECAST", "FIXTURE ALERT", "FIXTURE REGIONAL NOTICE"):
            self.assertIn(fact, md)
            self.assertIn(fact, visible)
        self.assertIn("Missions touched: 13. Events: 17.", md)
        self.assertEqual(texts[texts.index("MISSIONS TOUCHED")-1], "13")
        self.assertEqual(texts[texts.index("RECORDED EVENTS")-1], "17")
        self.assertEqual(texts[texts.index("NEED YOUR DECISION")-1], "1")
        self.assertIn("SAVED / RECENT", visible)
        self.assertIn(stamp(-60), md)
        self.assertIn("29 Oct 10:59 UTC", visible)

    def test_success_manifest_binds_every_nonempty_utf8_artifact(self):
        outputs, receipt = render_preflight(fixture())
        self.assertEqual({row["path"] for row in receipt["artifacts"]}, set(outputs))
        for row in receipt["artifacts"]:
            data = outputs[row["path"]]
            self.assertEqual(row["bytes"], len(data))
            self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
            data.decode("utf-8", errors="strict")
        self.assertEqual(receipt["model_calls"], 0)
        self.assertEqual(receipt["network_calls"], 0)
        self.assertEqual(receipt["workflow_calls"], 0)

    def test_preflight_is_deterministic_and_cannot_construct_or_advance_services(self):
        value = fixture();expected = render_preflight(value)
        self.assertTrue(expected[1]["ok"])
        with ExitStack() as stack:
            for target in ("builtins.open", "sqlite3.connect", "socket.socket", "subprocess.Popen"):
                stack.enter_context(patch(target, side_effect=AssertionError("Pure preflight forbids "+target)))
            stack.enter_context(patch.object(Path, "open", side_effect=AssertionError("No file access")))
            for cls, method in ((BriefingService, "__init__"), (BriefingService, "run"),
                                (BriefingService, "run_due"), (BriefingService, "collect"),
                                (Store, "__init__"), (ToolRegistry, "__init__")):
                stack.enter_context(patch.object(cls, method, side_effect=AssertionError("No service effects")))
            actual = render_preflight(value)
        self.assertEqual(actual, expected)

    def test_stale_and_expired_saved_facts_remain_explicit_in_all_formats(self):
        value = fixture()
        for source in value["public_sources"]["sources"]:
            source.update(fetched_at=stamp(-172800), issued_at=stamp(-172900), stale=True)
        alert = value["public_sources"]["sources"][2]["items"][0]
        alert.update(expires=stamp(-1), expired=True)
        outputs, receipt = render_preflight(value)
        self.assertTrue(receipt["ok"])
        saved = json.loads(outputs["brief.json"])
        self.assertTrue(saved["public_sources"]["sources"][2]["items"][0]["expired"])
        self.assertIn("STALE", outputs["brief.md"].decode())
        self.assertIn("EXPIRED", outputs["brief.md"].decode())
        self.assertIn("STALE", " ".join(svg_text(outputs["brief.svg"])))
        self.assertIn("EXPIRED", " ".join(svg_text(outputs["brief.svg"])))

    def test_render_failure_is_message_free_and_returns_no_partial_payloads(self):
        cause = OSError(SECRET)
        try:
            raise NameError(SECRET) from cause
        except NameError as error:
            with patch.object(BriefingService, "_render", side_effect=error):
                outputs, receipt = render_preflight(fixture())
        self.assertEqual(outputs, {})
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["artifacts"], [])
        self.assertEqual(receipt["failure_detail"]["phase"], "render")
        self.assertEqual(receipt["failure_detail"]["exception_type"], "NameError")
        self.assertNotIn(SECRET, json.dumps(receipt))
        self.assertNotIn("cause", receipt["failure_detail"])

    def test_mutating_renderer_cannot_mutate_its_callers_saved_report(self):
        value = fixture();before = deepcopy(value)
        original = BriefingService._render
        def mutate(copy):
            copy["activity"]["runs_count"] = 999
            return original(copy)
        with patch.object(BriefingService, "_render", side_effect=mutate):
            outputs, receipt = render_preflight(value)
        self.assertEqual(value, before)
        self.assertEqual(outputs, {})
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["failure_detail"]["phase"], "validate")

    def test_missing_malformed_or_changed_artifacts_fail_as_one_unit(self):
        valid, _ = render_preflight(fixture())
        cases = []
        altered = dict(valid);altered.pop("brief.svg");cases.append(altered)
        for name, body in (("brief.md", b"\xff"), ("brief.svg", b"<svg/>"),
                           ("brief.json", b'{"synthetic_changed_fact":true}'), ("brief.md", b"")):
            altered = dict(valid);altered[name] = body;cases.append(altered)
        altered = dict(valid);altered["brief.svg"] = "not bytes";cases.append(altered)
        for altered in cases:
            with self.subTest(names=list(altered)), patch.object(BriefingService, "_render", return_value=altered):
                outputs, receipt = render_preflight(fixture())
                self.assertEqual(outputs, {})
                self.assertFalse(receipt["ok"])
                self.assertEqual(receipt["artifacts"], [])
                self.assertEqual(receipt["failure_detail"]["phase"], "validate")

    def test_nonfinite_or_nonobject_input_fails_without_rendering(self):
        for value in (None, [], "saved input", {"invalid": float("nan")}):
            with self.subTest(kind=type(value).__name__), patch.object(BriefingService, "_render") as renderer:
                outputs, receipt = render_preflight(value)
                self.assertEqual(outputs, {})
                self.assertFalse(receipt["ok"])
                self.assertEqual(receipt["failure_detail"]["phase"], "input")
                renderer.assert_not_called()


class BriefingFailureDetailTests(unittest.TestCase):
    def test_diagnostics_exclude_message_arguments_locals_and_chained_cause(self):
        private_local = SECRET
        try:
            try:
                raise OSError(private_local)
            except OSError as cause:
                raise ValueError({"credential": private_local}) from cause
        except ValueError as error:
            result = failure_detail(error, phase="render")
        self.assertEqual(set(result), {"schema", "phase", "exception_type", "frames", "scope"})
        self.assertEqual(result["exception_type"], "ValueError")
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn("credential", json.dumps(result))

    def test_custom_exception_and_untrusted_phase_cannot_leak_identifiers(self):
        custom = type(SECRET, (Exception,), {})
        detail = failure_detail(custom(SECRET), phase=SECRET)
        self.assertEqual(detail["exception_type"], "Error")
        self.assertEqual(detail["phase"], "unknown")
        self.assertNotIn(SECRET, json.dumps(detail))

    def test_diagnostics_never_evaluate_exception_text(self):
        class TextTrap(Exception):
            def __str__(self):
                raise AssertionError("Exception text must never be evaluated")
        detail = failure_detail(TextTrap(), phase="render")
        self.assertEqual(detail["exception_type"], "Error")

    def test_actual_render_failure_has_only_bounded_relative_application_frames(self):
        malformed = fixture();malformed.pop("activity")
        outputs, receipt = render_preflight(malformed)
        self.assertEqual(outputs, {})
        detail = receipt["failure_detail"]
        self.assertTrue(detail["frames"])
        self.assertLessEqual(len(detail["frames"]), 8)
        for frame in detail["frames"]:
            self.assertEqual(set(frame), {"file", "function", "line"})
            self.assertTrue(frame["file"].startswith(("ariadne/", "scripts/")))
            self.assertNotIn("..", Path(frame["file"]).parts)
            self.assertNotIn(":", frame["file"])
            self.assertLessEqual(len(frame["function"]), 80)
            self.assertIs(type(frame["line"]), int)
            self.assertGreater(frame["line"], 0)


class BriefingPreflightCLITests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="preflight-", dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "saved-report.json"
        # Source-byte identity is deliberately distinct from canonical _json identity.
        self.payload = json.dumps(fixture(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.input.write_bytes(self.payload)
        self.digest = hashlib.sha256(self.payload).hexdigest()

    def args(self, name="preview"):
        return ["--input", self.input.relative_to(ROOT).as_posix(), "--input-sha256", self.digest,
                "--output", (self.root/name).relative_to(ROOT).as_posix()]

    def invoke(self, args):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=False), patch.object(cli, "install_network_policy"), patch.object(cli.sys, "stdout", output):
            code = cli.main(args)
        return code, json.loads(output.getvalue())

    def test_cli_publishes_only_three_frozen_formats_and_a_matching_receipt(self):
        before = self.input.read_bytes()
        with patch("sqlite3.connect", side_effect=AssertionError("No owner database")), patch("socket.socket", side_effect=AssertionError("No network")), patch("subprocess.Popen", side_effect=AssertionError("No workflows")):
            code, result = self.invoke(self.args())
        self.assertEqual(code, 0)
        destination = self.root/"preview"
        self.assertEqual({p.name for p in destination.iterdir()}, {"brief.json", "brief.md", "brief.svg", "receipt.json"})
        receipt = json.loads((destination/"receipt.json").read_bytes())
        self.assertEqual(result, receipt)
        self.assertEqual(receipt["source_sha256"], self.digest)
        self.assertEqual(receipt["input_sha256"], hashlib.sha256(_json(fixture())).hexdigest())
        self.assertEqual(json.loads((destination/"brief.json").read_bytes()), fixture())
        for row in receipt["artifacts"]:
            data = (destination/row["path"]).read_bytes()
            self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.input.read_bytes(), before)

    def test_hash_mismatch_and_invalid_hash_fail_before_render_or_output_claim(self):
        for digest in ("0"*64, "NOT_A_SHA256"):
            args = self.args();args[3] = digest
            with self.subTest(kind=len(digest)), patch.object(cli, "render_preflight") as renderer:
                code, result = self.invoke(args)
                self.assertEqual(code, 2)
                self.assertFalse(result["ok"])
                self.assertFalse((self.root/"preview").exists())
                renderer.assert_not_called()

    def test_deep_json_fails_safely_before_render_or_output_claim(self):
        # Byte bounds alone do not prevent the native JSON decoder exhausting its
        # safe nesting depth. This remains much smaller than the 2 MB input cap.
        self.payload = ('{"'+SECRET+'":').encode("ascii") + b"["*10000 + b"0" + b"]"*10000 + b"}"
        self.input.write_bytes(self.payload)
        self.digest = hashlib.sha256(self.payload).hexdigest()
        original_open = ToolRegistry._open_read
        reads = []
        def only_fixture_read(registry, path, descriptor):
            self.assertEqual(path, self.input)
            reads.append(path)
            return original_open(registry, path, descriptor)
        with patch.object(cli, "render_preflight") as renderer, \
             patch.object(ToolRegistry, "_open_read", only_fixture_read), \
             patch("sqlite3.connect", side_effect=AssertionError("No owner database")), \
             patch("socket.socket", side_effect=AssertionError("No network")), \
             patch("subprocess.Popen", side_effect=AssertionError("No workflows")):
            code, result = self.invoke(self.args())
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_detail"]["phase"], "input")
        self.assertEqual(result["failure_detail"]["exception_type"], "RecursionError")
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(reads, [self.input])
        renderer.assert_not_called()
        self.assertFalse((self.root/"preview").exists())
        self.assertEqual(self.input.read_bytes(), self.payload)
        diagnostic = json.dumps(result, ensure_ascii=True)
        self.assertNotIn(SECRET, diagnostic)
        self.assertNotIn(str(ROOT), diagnostic)
        self.assertNotIn(ROOT.as_posix(), diagnostic)
        self.assertTrue(all(not Path(row["file"]).is_absolute() for row in result["failure_detail"]["frames"]))

    def test_input_and_output_parent_traversal_are_rejected_before_render(self):
        for index in (1, 5):
            args = self.args();args[index] = "../outside-fixture.json"
            with self.subTest(argument=index), patch.object(cli, "render_preflight") as renderer:
                code, result = self.invoke(args)
                self.assertEqual(code, 2)
                self.assertFalse(result["ok"])
                renderer.assert_not_called()
                self.assertFalse((self.root/"preview").exists())

    def test_output_must_be_staging_even_for_project_relative_paths(self):
        args = self.args();args[5] = "runtime/tmp/preflight-output-must-not-be-created"
        with patch.object(cli, "render_preflight") as renderer:
            code, result = self.invoke(args)
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        renderer.assert_not_called()
        self.assertFalse((ROOT/args[5]).exists())

    def test_existing_output_is_never_replayed_or_overwritten(self):
        destination = self.root/"preview";destination.mkdir()
        sentinel = destination/"owner-sentinel.txt";sentinel.write_bytes(b"SYNTHETIC_OWNER_EDIT")
        with patch.object(cli, "render_preflight") as renderer:
            code, result = self.invoke(self.args())
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        renderer.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"SYNTHETIC_OWNER_EDIT")
        self.assertEqual([p.name for p in destination.iterdir()], ["owner-sentinel.txt"])

    def test_render_failure_publishes_safe_receipt_and_cannot_be_retried_in_place(self):
        with patch.object(BriefingService, "_render", side_effect=NameError(SECRET)):
            code, result = self.invoke(self.args("failed-preview"))
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        destination = self.root/"failed-preview"
        self.assertEqual([p.name for p in destination.iterdir()], ["receipt.json"])
        saved = (destination/"receipt.json").read_bytes()
        self.assertNotIn(SECRET.encode(), saved)
        self.assertEqual(json.loads(saved)["failure_detail"]["exception_type"], "NameError")
        with patch.object(cli, "render_preflight") as renderer:
            second_code, _ = self.invoke(self.args("failed-preview"))
        self.assertEqual(second_code, 2)
        renderer.assert_not_called()
        self.assertEqual((destination/"receipt.json").read_bytes(), saved)

    def test_status_cli_roundtrips_unicode_through_strict_cp1252_without_owner_access(self):
        service = Mock();expected = {"ok": True, "region": {"name": "Fixture ☃ Δ city"}}
        service.status.return_value = expected
        raw = io.BytesIO();stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with patch.dict(os.environ, {}, clear=False), patch.object(live_cli, "install_network_policy"), \
             patch.object(live_cli, "Settings", return_value=SimpleNamespace(data_dir=self.root/"fake-data")), \
             patch.object(live_cli, "Store", return_value=object()) as store, \
             patch.object(live_cli, "ToolRegistry", return_value=object()), \
             patch.object(live_cli, "BriefingService", return_value=service), \
             patch.object(live_cli.sys, "argv", ["briefing.py", "status", "--data-dir", str(self.root/"fake-data")]), \
             patch.object(live_cli.sys, "stdout", stream):
            live_cli.main()
        stream.flush();payload = raw.getvalue();stream.detach()
        self.assertTrue(payload.isascii())
        self.assertEqual(json.loads(payload.decode("cp1252")), expected)
        service.status.assert_called_once_with()
        store.assert_called_once()
        self.assertFalse((self.root/"fake-data").exists())


class BriefingFailedReceiptTests(unittest.TestCase):
    def test_generated_scheduled_failure_retains_detail_and_prevents_replay(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="failure-ledger-", dir=TEMP_ROOT) as temporary:
            settings = Settings(data_dir=Path(temporary)/"private")
            store = Store(settings.data_dir/"ariadne.sqlite3")
            registry = ToolRegistry(settings)
            service = BriefingService(settings, store, registry)
            service.configure({"enabled": True, "online_sources": False,
                "region": {"name": "Generated fixture city", "latitude": 41.0, "longitude": -87.0, "country": "US"}}, NOW-timedelta(days=1))
            with patch.object(service.venture, "run_for_brief", return_value={"status": "disabled"}), \
                 patch.object(service, "_render", side_effect=NameError(SECRET)), \
                 patch.object(service.qqq, "run_due", side_effect=AssertionError("No child advancement")), \
                 patch.object(service.options, "run_due", side_effect=AssertionError("No child advancement")), \
                 self.assertRaises(ValueError):
                service.run("diagnostic-failure-fixture", NOW, scheduled_day="2026-10-29", collect=False)
            first = service.history()["runs"][0]
            self.assertEqual(first["status"], "failed")
            self.assertEqual(first["failure_detail"]["phase"], "render")
            self.assertEqual(first["failure_detail"]["exception_type"], "NameError")
            self.assertNotIn(SECRET, json.dumps(first))
            self.assertEqual(first["artifacts"], [])
            self.assertTrue(service.config()["paused"])
            with patch.object(service, "_render", side_effect=AssertionError("No failed work replay")), self.assertRaises(BriefingConflict):
                service.run("diagnostic-failure-fixture", NOW)
            self.assertEqual(service.history()["runs"][0], first)
            self.assertIsNone(service.run_due(NOW+timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
