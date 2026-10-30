"""Protected venture configuration, bounded progress and fail-stop regressions."""
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.venture_lab import VentureConflict, VentureLab

UTC = timezone.utc
NOW = datetime(2026, 10, 30, 11, tzinfo=UTC)


class VentureLabTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1]/"runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="venture-fixture-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.settings = Settings(data_dir=self.base/"private", workspace=self.base/"workspace")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.lab = VentureLab(self.registry, self.store)
        self.source = self.settings.workspace/"plan.json"
        self.source.write_text(json.dumps({"summary": {"offer": "Manual quote comparison", "cash_cost_assumption": 0, "not_observed_revenue": True}}), encoding="utf-8")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.board = {"schema": 1, "enabled": True, "paused": False, "max_drafts_per_day": 2,
            "projects": [{"id": "business-one", "name": "Quote research", "domain": "business",
                          "sources": [{"role": "plan", "path": "plan.json", "sha256": self.digest, "data_as_of": "2026-10-20"}]}],
            "tasks": [{"id": "validation", "project_id": "business-one", "kind": "business_validation_pack", "title": "Validate the offer", "requires_owner_evidence": True},
                      {"id": "offer", "project_id": "business-one", "kind": "business_offer_pack", "title": "Prepare an offer", "depends_on": ["validation"]}]}

    def configure(self, board=None, resume=False):
        return self.lab.configure(board or self.board, self.lab.status()["revision"], resume=resume)

    def run_day(self, day=30, identifier=None, **kwargs):
        now = NOW+timedelta(days=day-30)
        return self.lab.run_for_brief(identifier or "daily-"+now.date().isoformat(), local_day=now.date().isoformat(), now=now, **kwargs)

    def counts(self):
        with self.store.connect(require_existing=True) as db:
            return {name: db.execute("SELECT count(*) FROM "+name).fetchone()[0] for name in ("venture_runs", "venture_task_progress", "venture_inputs", "venture_evidence")}

    def test_configuration_is_private_cas_and_accepts_sources_once(self):
        result = self.configure()
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(self.counts()["venture_inputs"], 1)
        with self.assertRaises(VentureConflict):
            self.lab.configure(self.board, 0)
        workspace = list(self.settings.workspace.rglob("*"))
        self.assertEqual(workspace, [self.source])
        (self.settings.workspace/"board.json").write_text('{"enabled":false}')
        self.assertTrue(self.lab.status()["config"]["enabled"])
        with self.store.connect(require_existing=True) as db:
            self.assertEqual(db.execute("SELECT length(content) FROM venture_inputs").fetchone()[0], len(self.source.read_bytes()))

    def test_daily_draft_waits_for_real_owner_evidence_and_never_contacts_anyone(self):
        self.configure()
        with mock.patch.object(socket, "socket", side_effect=AssertionError("No network")), mock.patch("subprocess.Popen", side_effect=AssertionError("No child/model")):
            first = self.run_day()
        self.assertEqual(first["status"], "completed")
        self.assertEqual([row["task_id"] for row in first["advanced_tasks"]], ["validation"])
        self.assertEqual(first["advanced_tasks"][0]["progress"], "draft_ready")
        self.assertTrue(first["next_actions"][0]["blockers"])
        second = self.run_day(day=31)
        self.assertEqual(second["advanced_tasks"], [])
        evidence = self.lab.record_evidence({"task_id": "validation", "kind": "interview", "observed_at": "2026-10-29T12:00:00+00:00", "note": "Owner reports one interview; hypothesis remains unvalidated."}, self.lab.status()["revision"])
        self.assertTrue(evidence["evidence"][0]["owner_reported"])
        self.assertFalse(evidence["evidence"][0]["independently_verified"])
        third = self.run_day(day=32)
        self.assertEqual([row["task_id"] for row in third["advanced_tasks"]], ["offer"])
        self.assertEqual(third["model_calls"], 0)
        self.assertEqual(third["network_calls"], 0)
        for artifact in third["artifacts"]:
            data = (self.settings.workspace/artifact["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), artifact["sha256"])

    def test_two_independent_tasks_max_and_second_request_same_day_cannot_advance(self):
        board = copy.deepcopy(self.board)
        board["tasks"][1]["depends_on"] = []
        board["tasks"].append({"id": "third", "project_id": "business-one", "kind": "business_offer_pack", "title": "Later draft"})
        self.configure(board)
        first = self.run_day()
        self.assertEqual(len(first["advanced_tasks"]), 2)
        before = self.counts()
        replay = self.run_day(identifier="another-manual-request")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["request_id"], first["request_id"])
        self.assertEqual(self.counts(), before)
        self.assertEqual([row["task_id"] for row in self.run_day(day=31)["advanced_tasks"]], ["third"])

    def test_global_board_and_briefing_pauses_never_start_a_pulse(self):
        self.configure()
        before = self.counts()
        self.store.set_setting("paused", True)
        self.assertEqual(self.run_day()["status"], "paused")
        self.store.set_setting("paused", False)
        self.assertEqual(self.run_day(permitted=lambda: False)["status"], "held")
        self.assertEqual(self.run_day(advance=False)["status"], "review_only")
        self.assertEqual(self.counts(), before)
        board = copy.deepcopy(self.board); board["paused"] = True
        self.configure(board)
        self.assertEqual(self.run_day()["status"], "paused")
        self.assertEqual(self.counts(), before)

    def test_changed_workspace_source_holds_and_failed_request_never_replays(self):
        self.configure()
        accepted = self.source.read_bytes()
        self.source.write_text('{"pretend_return":999999}')
        result = self.run_day()
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.counts()["venture_task_progress"], 0)
        status = self.lab.status()
        self.assertEqual(status["state"], "held")
        with self.assertRaises(VentureConflict):
            self.lab.configure(self.board, 1, resume=True)
        self.source.write_bytes(accepted)
        self.lab.resolve_hold(status["hold"]["request_id"], status["revision"])
        self.assertEqual(self.run_day()["status"], "held")
        self.assertEqual(self.counts()["venture_task_progress"], 0)

    def test_interrupted_receipt_is_not_adopted_even_when_all_outputs_exist(self):
        self.configure()
        original = self.lab._checkpoint
        calls = []
        def die_before_commit(revision, permitted):
            original(revision, permitted)
            calls.append(True)
            if len(calls) == 5:
                raise KeyboardInterrupt("simulated power loss after output publication")
        with mock.patch.object(self.lab, "_checkpoint", die_before_commit), self.assertRaises(KeyboardInterrupt):
            self.run_day()
        self.assertEqual(len(list((self.settings.workspace/"venture-lab/progress").rglob("progress.*"))), 2)
        restarted = VentureLab(self.registry, self.store)
        result = restarted.run_for_brief("next-request", local_day="2026-10-31", now=NOW+timedelta(days=1))
        self.assertEqual(result["status"], "held")
        status = restarted.status()
        self.assertEqual(status["latest"]["status"], "interrupted")
        self.assertEqual(status["progress"][0]["status"], "interrupted")
        restarted.resolve_hold(status["hold"]["request_id"], status["revision"])
        after = restarted.run_for_brief("new-request-after-review", local_day="2026-10-31", now=NOW+timedelta(days=1))
        self.assertEqual(after["advanced_tasks"], [])
        self.assertNotEqual(restarted.status()["progress"][0]["status"], "draft_ready")

    def test_output_tampering_holds_without_rewriting_the_completed_receipt(self):
        self.configure()
        first = self.run_day()
        path = self.settings.workspace/first["artifacts"][0]["path"]
        path.write_text("tampered output")
        replay = self.run_day(identifier="new-alias")
        self.assertEqual(replay["status"], "held")
        self.assertEqual(replay["request_id"], first["request_id"])
        self.assertEqual(self.lab.status()["latest"]["status"], "completed")
        self.assertEqual(path.read_text(), "tampered output")

    def test_pause_after_writeahead_leaves_a_durable_hold_and_no_automatic_retry(self):
        self.configure()
        calls = []
        def permitted():
            calls.append(True)
            return len(calls) < 3
        result = self.run_day(permitted=permitted)
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.lab.status()["latest"]["status"], "failed")
        self.assertEqual(self.lab.status()["progress"][0]["status"], "failed")
        self.assertEqual(self.run_day(day=31)["status"], "held")

    def test_input_quota_rolls_back_config_and_deduplicates_private_inputs(self):
        with mock.patch("ariadne.venture_lab.MAX_INPUT_TOTAL", 1), self.assertRaises(ValueError):
            self.configure()
        self.assertEqual(self.lab.status()["revision"], 0)
        self.assertEqual(self.counts()["venture_inputs"], 0)
        self.configure()
        self.configure()
        self.assertEqual(self.counts()["venture_inputs"], 1)

    def test_invalid_cycles_types_and_started_task_edits_are_rejected(self):
        for change in ({"schema": True}, {"max_drafts_per_day": 3}, {"enabled": "yes"}):
            board = {**copy.deepcopy(self.board), **change}
            with self.subTest(change=change), self.assertRaises(ValueError): self.configure(board)
        board = copy.deepcopy(self.board); board["tasks"][0]["depends_on"] = ["offer"]
        with self.assertRaises(ValueError): self.configure(board)
        self.configure(); self.run_day()
        board = copy.deepcopy(self.board); board["tasks"][0]["title"] = "Silently revised started work"
        with self.assertRaises(ValueError): self.configure(board)

    def test_etf_review_preserves_dates_cost_scope_and_research_only_labels(self):
        source = self.settings.workspace/"strategy.json"
        source.write_text(json.dumps({"summary": {"execution_model": "nav_next_close_proxy", "holdout_cagr": -0.12, "max_drawdown": -0.34, "cost_bps": 10, "qualified": False}}))
        board = {"schema": 1, "enabled": True, "projects": [{"id": "etf-one", "name": "ETF research", "domain": "etf_research", "sources": [{"role": "strategy_report", "path": "strategy.json", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "data_as_of": "2026-10-20"}]}], "tasks": [{"id": "etf-review", "project_id": "etf-one", "kind": "etf_review_pack", "title": "Review losing and winning periods"}]}
        self.configure(board)
        result = self.run_day()
        self.assertEqual(result["status"], "completed")
        observation = result["projects"][0]["sources"][0]
        self.assertEqual(observation["data_as_of"], "2026-10-20")
        self.assertEqual(observation["freshness"], "stale")
        self.assertFalse(observation["live"])
        self.assertIn('-0.12', observation["accepted_excerpt"])
        text = (self.settings.workspace/result["output_folder"]/"progress.md").read_text()
        self.assertIn("NAV-only studies use a next-close NAV proxy", text)
        self.assertIn("No message, purchase", text)
        artifact = json.loads((self.settings.workspace/result["output_folder"]/"progress.json").read_text())
        self.assertNotIn("status", artifact)
        self.assertEqual(artifact["artifact_kind"], "local_research_draft")

    def test_missing_main_database_is_not_recreated(self):
        aside = self.store.path.with_suffix(".held")
        self.store.path.rename(aside)
        try:
            with self.assertRaises(ValueError): self.lab.status()
            self.assertFalse(self.store.path.exists())
        finally:
            aside.rename(self.store.path)

    def test_history_quota_holds_without_publishing_or_replaying(self):
        self.configure()
        with mock.patch("ariadne.venture_lab.MAX_HISTORY", 0):
            result = self.run_day()
        self.assertEqual(result["status"], "held")
        self.assertFalse((self.settings.workspace/"venture-lab/progress").exists())
        self.assertEqual(self.lab.status()["state"], "held")

    def test_new_task_after_explicit_resume_cannot_adopt_a_failed_task(self):
        self.configure()
        with mock.patch("ariadne.venture_lab.publish_bytes", side_effect=OSError("fixture disk error")):
            self.assertEqual(self.run_day()["status"], "held")
        state = self.lab.status()
        self.assertEqual(state["progress"][0]["status"], "failed")
        board = copy.deepcopy(self.board)
        board["tasks"] = [{"id": "explicit-new-draft", "project_id": "business-one", "kind": "business_validation_pack", "title": "New reviewed attempt"}]
        self.lab.configure(board, state["revision"], resume=True)
        self.assertEqual([row["task_id"] for row in self.run_day(day=31)["advanced_tasks"]], ["explicit-new-draft"])
        old = next(row for row in self.lab.status()["progress"] if row["task_id"] == "validation")
        self.assertEqual(old["status"], "failed")

    def test_later_day_verifies_earlier_drafts_before_advancing_dependencies(self):
        self.configure()
        first = self.run_day()
        self.lab.record_evidence({"task_id": "validation", "kind": "review", "observed_at": "2026-10-29T12:00:00+00:00", "note": "Owner reviewed the draft."}, self.lab.status()["revision"])
        (self.settings.workspace/first["artifacts"][1]["path"]).write_text("changed prior draft")
        next_day = self.run_day(day=31)
        self.assertEqual(next_day["status"], "held")
        self.assertFalse(any(row["task_id"] == "offer" for row in self.lab.status()["progress"]))

    def test_partial_publication_is_held_and_never_adopted_after_resume(self):
        self.configure()
        from ariadne.atomic import publish_bytes as real_publish
        calls = []
        def partial(path, payload):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("simulated disk failure before second artifact")
            return real_publish(path, payload)
        with mock.patch("ariadne.venture_lab.publish_bytes", partial):
            result = self.run_day()
        self.assertEqual(result["status"], "held")
        self.assertEqual(len(result["artifacts"]), 1)
        state = self.lab.status()
        self.lab.resolve_hold(state["hold"]["request_id"], state["revision"])
        later = self.run_day(day=31)
        self.assertEqual(later["advanced_tasks"], [])
        self.assertEqual(self.lab.status()["progress"][0]["status"], "failed")

    def test_private_input_corruption_is_not_accepted_or_published(self):
        self.configure()
        with self.store.connect(require_existing=True) as db:
            db.execute("UPDATE venture_inputs SET content=? WHERE digest=?", (b"forged input", self.digest))
        result = self.run_day()
        self.assertEqual(result["status"], "held")
        self.assertFalse((self.settings.workspace/"venture-lab/progress").exists())

    def test_nonfinite_strategy_json_rejected_and_deep_plan_remains_quoted_text(self):
        source = self.settings.workspace/"invalid-strategy.json"
        source.write_text('{"return":1e99999}')
        board = copy.deepcopy(self.board)
        board["projects"][0] = {"id": "business-one", "name": "ETF", "domain": "etf_research", "sources": [{"role": "strategy_report", "path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}
        board["tasks"] = [{"id": "etf", "project_id": "business-one", "kind": "etf_review_pack", "title": "Review source"}]
        with self.assertRaises(ValueError): self.configure(board)
        self.assertEqual(self.lab.status()["revision"], 0)
        self.source.write_text("["*1500+"0"+"]"*1500)
        board = copy.deepcopy(self.board)
        board["projects"][0]["sources"][0]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.configure(board)
        self.assertEqual(self.run_day()["status"], "completed")

    def test_lock_contention_does_not_dispatch_or_hold_another_live_request(self):
        self.configure()
        from ariadne.locking import InstanceLock
        lock = InstanceLock(self.lab.lock_path)
        lock.acquire()
        try:
            result = self.run_day()
        finally:
            lock.release()
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.counts()["venture_runs"], 0)
        self.assertEqual(self.lab.status()["state"], "ready")

    def test_reentrant_owner_change_during_boundary_cannot_be_overwritten(self):
        self.configure()
        calls = []
        def permitted():
            calls.append(True)
            if len(calls) == 3:
                state = self.lab.status()
                board = state["config"]
                board["paused"] = True
                self.lab.configure(board, state["revision"])
            return True
        result = self.run_day(permitted=permitted)
        self.assertEqual(result["status"], "held")
        state = self.lab.status()
        self.assertTrue(state["config"]["paused"])
        self.assertEqual(state["progress"][0]["status"], "failed")
