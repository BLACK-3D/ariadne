"""Workroom's real local domain boundaries; no model, internet or owner state."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from ariadne.agents import AgentRegistry
from ariadne.config import Settings, _allowed_path
from ariadne.handoff import _digest, _json
from ariadne.procedures import Procedures
from ariadne.routines import Routines
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.verification import verify
from ariadne.workroom import Workroom, WorkroomConflict

ROOT = Path(__file__).resolve().parents[1]


class WorkroomTests(unittest.TestCase):
    def setUp(self):
        self.parent = _allowed_path(ROOT/"runtime/tmp")
        self.parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="workroom-unit-", dir=self.parent)
        self.base = _allowed_path(self.temp.name)
        self.addCleanup(self.cleanup)
        self.settings = Settings(data_dir=self.base/"data", workspace=self.base/"workspace")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.routines = Routines(Procedures(self.registry, self.store), self.store)
        self.workroom = Workroom(self.registry, self.store, self.routines)
        self.agents = AgentRegistry(self.registry, max_steps=12)
        self.workroom.bind_agent_catalog(self.agents.catalog)
        self.sequence = 0

    def cleanup(self):
        if self.base.parent != self.parent or not self.base.name.startswith("workroom-unit-"):
            raise ValueError("Refusing cleanup outside generated Workroom fixtures.")
        self.temp.cleanup()

    def body(self, **fields):
        self.sequence += 1
        return {"request_id": "request-"+str(self.sequence), "expected_revision": self.workroom.status()["revision"], **fields}

    def template(self, identifier="offline-continuity"):
        return copy.deepcopy(next(row["project"] for row in self.workroom.catalog()["templates"] if row["id"] == identifier))

    def save(self, definition=None):
        return self.workroom.save_project(self.body(project=definition or self.template()))["project"]

    def start(self, identifier="offline-continuity", **budget):
        project = self.workroom.project(identifier)["project"]
        return self.workroom.start_cycle(self.body(project_id=identifier, content_sha256=project["content_sha256"], max_children=budget.pop("max_children", 3), **budget))

    def run_procedure(self):
        self.assertTrue(self.routines.tick())
        self.assertTrue(self.workroom.tick())

    def task(self, identifier="offline-continuity", task="inventory"):
        return next(row for row in self.workroom.project(identifier)["project"]["tasks"] if row["id"] == task)

    def review(self, identifier, task, decision="accept", refs=None):
        row = self.task(identifier, task)
        return self.workroom.review_task(self.body(project_id=identifier, task_id=task, content_sha256=row["content_sha256"], decision=decision,
                                                  note="Owner reviewed this bounded local evidence; no external outcome claimed.", source_refs=refs or []))

    def workflow_project(self, dependencies=()):
        workflow = self.store.workflow_save("Checked report", "Write a local report to {{run_dir}}/report.json with the supplied evidence.",
                                            [{"kind": "json_valid", "path": "{{run_dir}}/report.json"}], agent_id="operations", isolated_outputs=True)
        definition = {"id": "checked-project", "name": "Checked project", "objective": "Review a frozen existing workflow with local evidence.",
                      "acceptance": "A checked local report exists.", "tasks": []}
        if dependencies:
            definition["tasks"].append({"id": "gate", "title": "Owner context", "kind": "manual", "depends_on": [], "acceptance": "Owner records the research basis."})
        definition["tasks"].append({"id": "report", "title": "Checked report", "kind": "workflow", "depends_on": list(dependencies),
                                    "workflow_id": workflow["id"], "acceptance": "The declared JSON check passes for actual child output."})
        self.save(definition)
        return workflow, definition

    def complete_mission(self, child_id, *, payload=b'{"observed":true}\n'):
        run = self.store.run(child_id)
        target = self.registry._path(run["checks"][0]["path"])
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(payload)
        evidence = verify(run["checks"], self.registry)
        self.store.update_run(child_id, status="completed", verification_status=evidence["status"], verification=evidence)
        return target

    def test_passive_reads_and_tick_never_initialize_or_call_model(self):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("No network")), mock.patch("subprocess.Popen", side_effect=AssertionError("No processes/models")):
            self.assertEqual(self.workroom.status()["revision"], 0)
            self.assertFalse(self.workroom.status()["initialized"])
            self.assertEqual(len(self.workroom.catalog()["templates"]), 4)
            self.assertFalse(self.workroom.tick()); self.assertEqual(self.workroom.recover(), 0)
            self.assertIsNone(self.workroom.assignment("unlinked")); self.assertIsNone(self.workroom.model_step_limit("unlinked"))
            with self.assertRaises(KeyError): self.workroom.receipt("missing")
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'workroom_%'").fetchone())

    def test_complete_three_procedure_chain_and_manual_gate_without_network(self):
        self.save(); self.start()
        with mock.patch.object(socket, "socket", side_effect=AssertionError("No network")), mock.patch("subprocess.Popen", side_effect=AssertionError("No process/model")):
            for task in ("inventory", "references", "continuity"):
                self.assertTrue(self.workroom.tick())
                self.assertEqual(self.task(task=task)["status"], "active")
                self.run_procedure()
                row = self.task(task=task)
                self.assertEqual(row["status"], "completed")
                self.assertEqual(row["evidence"]["assurance"], "checked_artifacts")
                self.assertTrue(row["evidence"]["artifacts"])
            self.assertTrue(self.workroom.tick())
            self.assertEqual(self.task(task="decision")["status"], "awaiting_review")
            self.assertFalse(self.workroom.tick())
            self.review("offline-continuity", "decision")
            self.assertEqual(self.task(task="decision")["evidence"]["assurance"], "owner_attested")
            self.assertTrue(self.workroom.tick())
        project = self.workroom.project("offline-continuity")["project"]
        self.assertEqual(project["cycle"]["status"], "completed")
        self.assertEqual(project["task_counts"]["completed"], 4)
        self.assertEqual(len(self.routines.runs()), 3)
        self.assertEqual(self.routines.definitions(), [])

    def test_cas_and_request_id_replay_does_not_reset_evidence(self):
        body = self.body(project=self.template())
        first = self.workroom.save_project(body)
        replay = self.workroom.save_project(body)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["revision"], replay["revision"])
        self.assertEqual(self.workroom.receipt(body["request_id"])["action"], "save_project")
        wrong = copy.deepcopy(body); wrong["project"]["name"] = "Different"
        with self.assertRaises(WorkroomConflict): self.workroom.save_project(wrong)
        with self.assertRaises(WorkroomConflict): self.workroom.save_project({**body, "request_id": "stale"})

    def test_budget_counts_invocations_and_requires_explicit_next_cycle(self):
        self.save(); self.start(max_children=1)
        self.assertTrue(self.workroom.tick()); self.run_procedure()
        self.assertTrue(self.workroom.tick())
        self.assertEqual(self.workroom.project("offline-continuity")["project"]["cycle"]["status"], "budget_exhausted")
        for _ in range(3): self.assertFalse(self.workroom.tick())
        self.assertEqual(len(self.routines.runs()), 1)
        self.start(max_children=1); self.assertTrue(self.workroom.tick())
        self.assertEqual(len(self.routines.runs()), 2)

    def test_pause_holds_before_hashing_and_never_silently_resumes(self):
        self.save(); self.start(); self.store.set_setting("paused", True)
        with mock.patch.object(self.workroom, "_dependency_evidence", side_effect=AssertionError("No hash while paused")):
            self.assertTrue(self.workroom.tick())
        self.store.set_setting("paused", False)
        self.assertFalse(self.workroom.tick())
        self.assertEqual(self.workroom.project("offline-continuity")["project"]["cycle"]["status"], "held")
        self.assertEqual(self.routines.runs(), [])
        with self.assertRaises(WorkroomConflict): self.start()

    def test_recovery_retains_queued_child_and_never_replays_or_adopts(self):
        self.save(); self.start(); self.workroom.tick()
        child = self.task()["child"]["id"]
        self.assertEqual(self.workroom.recover(), 1)
        self.assertEqual(self.routines.detail(child)["status"], "queued")
        self.routines.tick()
        self.assertFalse(self.workroom.tick())
        self.assertEqual(self.task()["status"], "active")
        self.assertEqual(len(self.routines.runs()), 1)

    def test_stop_after_current_allows_known_child_to_finish_without_dispatch(self):
        self.save(); start = self.start(); self.workroom.tick()
        self.workroom.stop_cycle(self.body(project_id="offline-continuity", cycle_id=start["cycle_id"]))
        self.run_procedure()
        self.assertEqual(self.task()["status"], "completed")
        self.assertFalse(self.workroom.tick()); self.assertEqual(len(self.routines.runs()), 1)

    def test_dependency_rehash_holds_if_accepted_report_was_tampered(self):
        self.save(); self.start(); self.workroom.tick(); self.run_procedure()
        artifact = self.task()["evidence"]["artifacts"][0]
        self.registry._path(artifact["path"]).write_bytes(b"tampered")
        self.assertTrue(self.workroom.tick())
        project = self.workroom.project("offline-continuity")["project"]
        self.assertEqual(project["cycle"]["status"], "held")
        self.assertEqual(len(self.routines.runs()), 1)
        self.assertEqual(self.task()["evidence"]["artifacts"][0], artifact)

    def test_workflow_snapshot_cap_assignment_and_actual_output_checks(self):
        self.workflow_project(("gate",))
        self.review("checked-project", "gate")
        self.start("checked-project", max_steps_per_child=2)
        self.workroom.tick()
        row = self.task("checked-project", "report"); child = row["child"]["id"]
        self.assertEqual(self.workroom.model_step_limit(child), 2)
        assignment = self.workroom.assignment(child)
        self.assertTrue(assignment["context_is_untrusted_data"])
        self.assertEqual(assignment["dependencies"][0]["assurance"], "owner_attested")
        self.assertEqual(assignment["task_id"], "report")
        self.complete_mission(child); self.workroom.tick()
        self.assertEqual(self.task("checked-project", "report")["status"], "completed")
        self.assertEqual(self.workroom.assignment(child), assignment)

    def test_workflow_drift_blocks_start_and_dispatch(self):
        workflow, _ = self.workflow_project()
        self.store.workflow_save("Changed", "Write {{run_dir}}/report.json", workflow["checks"], workflow_id=workflow["id"], agent_id="operations", isolated_outputs=True)
        self.assertTrue(self.workroom.project("checked-project")["project"]["source_drift"])
        with self.assertRaises(WorkroomConflict): self.start("checked-project")
        with self.store.connect() as db: self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 0)

    def test_uncertain_approval_prevents_completed_mission_adoption(self):
        self.workflow_project(); self.start("checked-project"); self.workroom.tick()
        child = self.task("checked-project", "report")["child"]["id"]
        self.complete_mission(child)
        approval = self.store.approval(child, "call", "file_write", {"path": "ignored"})
        self.store.approval_status(approval["id"], "uncertain")
        self.workroom.tick()
        self.assertEqual(self.task("checked-project", "report")["status"], "held")
        with self.assertRaises(WorkroomConflict): self.review("checked-project", "report")

    def test_dispatch_link_and_child_insert_roll_back_together(self):
        self.workflow_project(); self.start("checked-project")
        original = self.store._insert_run
        def insert_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ValueError("Injected post-insert failure")
        with mock.patch.object(self.store, "_insert_run", side_effect=insert_then_fail):
            self.assertTrue(self.workroom.tick())
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM workroom_children").fetchone()[0], 0)
        self.assertEqual(self.task("checked-project", "report")["status"], "held")

    def test_parallel_ticks_create_one_linked_child(self):
        self.save(); self.start()
        second = Workroom(self.registry, self.store, self.routines); second.bind_agent_catalog(self.agents.catalog)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda service: service.tick(), [self.workroom, second]))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.routines.runs()), 1)

    def test_manual_refs_require_exact_identity_and_checked_child_is_not_manual(self):
        self.save(self.template("pod-validation"))
        target = self.settings.workspace/"source.txt"; target.write_bytes(b"actual source")
        ref = {"path": "source.txt", "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "bytes": target.stat().st_size}
        bad = {**ref, "bytes": 0}
        with self.assertRaises(ValueError): self.review("pod-validation", "niche", refs=[bad])
        self.review("pod-validation", "niche", refs=[ref])
        target.write_bytes(b"changed")
        with self.assertRaises(ValueError): self.review("pod-validation", "economics")
        self.save()
        with self.assertRaises(WorkroomConflict): self.review("offline-continuity", "inventory")

    def test_dag_validation_and_active_definition_lock(self):
        definition = self.template()
        definition["tasks"][0]["depends_on"] = ["continuity"]
        with self.assertRaises(ValueError): self.save(definition)
        self.save(); self.start()
        definition = self.template(); definition["name"] = "Edited"
        with self.assertRaises(WorkroomConflict): self.save(definition)

    def test_edited_definition_resets_manual_attestations_and_archives_original(self):
        definition = self.template("pod-validation"); self.save(definition)
        self.review("pod-validation", "niche")
        definition["objective"] = "An explicitly revised validation question."
        self.save(definition)
        self.assertEqual(self.task("pod-validation", "niche")["status"], "ready")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM workroom_versions").fetchone()
            prior = json.loads(row["value"])
        self.assertEqual(prior["runtime"]["niche"]["status"], "completed")

    def test_failed_execution_needs_new_identity_then_revised_cycle_can_start(self):
        _, definition = self.workflow_project(); self.start("checked-project"); self.workroom.tick()
        child = self.task("checked-project", "report")["child"]["id"]
        self.store.update_run(child, status="failed", error="Fixture failure")
        self.workroom.tick()
        cosmetic = copy.deepcopy(definition); cosmetic["tasks"][0]["title"] = "Cosmetic retry"
        cosmetic["tasks"][0]["acceptance"] = "Changed acceptance wording does not change this execution."
        with self.assertRaises(WorkroomConflict): self.save(cosmetic)
        replacement = copy.deepcopy(definition); replacement["tasks"][0]["id"] = "reviewed-new-attempt"
        project = self.save(replacement)
        self.assertIsNone(project["cycle"])
        self.assertEqual(project["cycles"][-1]["status"], "held")
        self.start("checked-project")
        self.assertEqual(self.workroom.project("checked-project")["project"]["cycle"]["status"], "active")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM runs WHERE id=?", (child,)).fetchone()[0], "failed")

    def test_complete_assignment_cannot_silently_drop_over_budget_dependency_metadata(self):
        _, definition = self.workflow_project(("gate",))
        definition["objective"] = "o"*4000
        definition["acceptance"] = "a"*1000
        definition["tasks"][0]["acceptance"] = "g"*1000
        definition["tasks"][1]["acceptance"] = "r"*1000
        # A long but valid source locator makes the complete context exceed8KiB.
        directory = self.settings.workspace/("d"*120)
        directory.mkdir(); target = directory/("s"*120+".txt"); target.write_bytes(b"source")
        ref = {"path": target.relative_to(self.settings.workspace).as_posix(), "sha256": hashlib.sha256(b"source").hexdigest(), "bytes": 6}
        self.save(definition); self.review("checked-project", "gate", refs=[ref]); self.start("checked-project")
        with mock.patch("ariadne.workroom.MAX_ASSIGNMENT_BYTES", 7500):
            self.workroom.tick()
        self.assertEqual(self.task("checked-project", "report")["status"], "held")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM workroom_children").fetchone()[0], 0)

    def test_trusted_catalog_required_and_cap_changes_hold_before_dispatch(self):
        workflow, definition = self.workflow_project()
        unbound = Workroom(self.registry, self.store, self.routines)
        definition["id"] = "unbound"
        with self.assertRaises(ValueError): unbound.save_project(self.body(project=definition))
        self.start("checked-project", max_steps_per_child=3)
        self.agents.max_steps = 2
        self.assertTrue(self.workroom.tick())
        self.assertEqual(self.workroom.project("checked-project")["project"]["cycle"]["status"], "held")
        with self.store.connect() as db: self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 0)

    def test_metadata_budget_rollback_preserves_original_definition_and_revision(self):
        self.save()
        before = self.workroom.status()
        replacement = self.template("pod-validation")
        with mock.patch("ariadne.workroom.MAX_STORAGE_BYTES", 1):
            with self.assertRaises(ValueError): self.save(replacement)
        self.assertEqual(self.workroom.status(), before)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM workroom_requests").fetchone()[0], 1)

    def test_manual_accepted_evidence_total_is_bounded_across_tasks(self):
        self.save(self.template("pod-validation"))
        target = self.settings.workspace/"source.txt"; target.write_bytes(b"sixsix")
        ref = {"path": "source.txt", "sha256": hashlib.sha256(b"sixsix").hexdigest(), "bytes": 6}
        self.review("pod-validation", "niche", refs=[ref])
        with mock.patch("ariadne.workroom.MAX_EVIDENCE_BYTES", 10):
            with self.assertRaises(ValueError): self.review("pod-validation", "economics", refs=[ref])
        self.assertEqual(self.task("pod-validation", "economics")["status"], "ready")

    def test_unhashable_task_inputs_fail_as_validation_errors(self):
        for change in ({"kind": {}}, {"depends_on": [{}]}, {"depends_on": ["decision", "decision"]}):
            definition = self.template(); definition["tasks"][0].update(change)
            with self.assertRaises(ValueError): self.save(definition)

    def test_accepted_mission_evidence_is_rechecked_before_manual_gate(self):
        _, definition = self.workflow_project()
        definition["tasks"].append({"id": "review", "title": "Review report", "kind": "manual", "depends_on": ["report"], "acceptance": "Owner reviews the exact checked output."})
        self.save(definition); self.start("checked-project"); self.workroom.tick()
        child = self.task("checked-project", "report")["child"]["id"]
        output = self.complete_mission(child); self.workroom.tick()
        output.write_bytes(b'{"changed":true}\n')
        self.workroom.tick()
        self.assertEqual(self.task("checked-project", "review")["status"], "held")
        self.assertEqual(self.task("checked-project", "report")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
