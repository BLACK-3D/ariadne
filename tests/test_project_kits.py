"""Source fidelity and kit adoption boundaries using only synthetic local state."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from ariadne.agents import AgentRegistry
from ariadne.client_handoff import ClientHandoffDesk, _core
from ariadne.config import Settings, _allowed_path
from ariadne.handoff import HandoffConflict, _digest
from ariadne.operations_workflows import OperationsWorkflows
from ariadne.project_kits import ProjectKits, RECIPE, guard_model_request
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.verification import verify
from ariadne.workroom import Workroom
from tests.test_client_handoff import client, intake

ROOT = Path(__file__).resolve().parents[1]


class ProjectKitTests(unittest.TestCase):
    def setUp(self):
        self.parent = _allowed_path(ROOT/"runtime/tmp")
        self.parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="project-kit-unit-", dir=self.parent)
        self.base = _allowed_path(self.temporary.name)
        self.addCleanup(self.cleanup)
        for name in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            guard = patch(name, side_effect=AssertionError("Kit fixtures forbid network and native processes"))
            guard.start(); self.addCleanup(guard.stop)
        self.settings = Settings(data_dir=self.base/"data", api_token="synthetic-project-kit-test")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry = ToolRegistry(self.settings)
        self.registry.media._voice = Mock(status=Mock(return_value={"transcription": False, "speech": False}))
        self.registry.procedures.store = self.store
        self.routines = OperationsWorkflows(self.registry.procedures.service(), self.store)
        self.room = Workroom(self.registry, self.store, self.routines)
        self.room.bind_agent_catalog(AgentRegistry(self.registry, 12).catalog)
        self.desk = ClientHandoffDesk(self.registry, self.store)
        self.kits = ProjectKits(self.registry, self.store, self.room, self.desk)
        self.sequence = 0

    def cleanup(self):
        if self.base.parent != self.parent or not self.base.name.startswith("project-kit-unit-"):
            raise ValueError("Refusing cleanup outside generated kit state")
        self.temporary.cleanup()

    def desk_call(self, method, **fields):
        self.sequence += 1
        return getattr(self.desk, method)({"request_id": "desk-"+str(self.sequence), "expected_revision": self.desk.status()["revision"], **fields})

    def body(self, **fields):
        self.sequence += 1
        return {"request_id": "kit-"+str(self.sequence), "expected_revision": self.kits.status()["revision"], **fields}

    def seed(self, value=None):
        self.desk_call("save_client", client=client())
        self.desk_call("create_intake", intake=value or intake())
        return self.kits.source("job-1")

    def prepare(self, value=None):
        source = self.seed(value)
        body = self.body(intake_id="job-1", source_sha256=source["source_sha256"])
        return self.kits.prepare(body), body

    def adopt(self, kit):
        return self.kits.adopt(self.body(kit_id=kit["id"], content_sha256=kit["content_sha256"], expected_workroom_revision=self.room.status()["revision"]))

    def counts(self):
        with self.store.connect() as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {name: db.execute('SELECT count(*) FROM "'+name+'"').fetchone()[0] for name in ("runs", "workflows", "schedules", "approvals", "client_handoff_documents") if name in names}

    def room_body(self, **fields):
        self.sequence += 1
        return {"request_id": "room-"+str(self.sequence), "expected_revision": self.room.status()["revision"], **fields}

    def project(self, identifier):
        return self.room.project(identifier)["project"]

    def review(self, project, task, refs, decision="accept"):
        value = next(row for row in self.project(project)["tasks"] if row["id"] == task)
        return self.room.review_task(self.room_body(project_id=project, task_id=task, content_sha256=value["content_sha256"], decision=decision,
                                                  note="Synthetic owner inspection only; no actual customer consent or commercial outcome.", source_refs=refs))

    def test_passive_reads_do_not_initialize_kit_or_workroom_or_files(self):
        with self.store.connect() as db:
            before = list(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        self.assertFalse(self.kits.status()["initialized"])
        self.assertEqual(self.kits.status()["kits"], [])
        self.assertEqual(self.kits.sources()["sources"], [])
        self.assertEqual(self.kits.catalog()["recipes"][0]["id"], RECIPE)
        self.assertFalse(list(self.settings.workspace.iterdir()))
        with self.store.connect() as db:
            self.assertEqual(list(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name")), before)

    def test_preparation_publishes_exact_complete_source_without_operational_work(self):
        result, body = self.prepare()
        kit = result["kit"]
        self.assertEqual(kit["status"], "prepared")
        self.assertEqual(len(kit["artifacts"]), 3)
        self.assertEqual(kit["source_review_refs"], kit["artifacts"])
        source = self.kits.source("job-1")
        self.assertEqual(kit["snapshot"], source["source"])
        self.assertIsNone(kit["snapshot"]["packet"])
        self.assertTrue(kit["snapshot"]["intake"]["permission"]["confirmed"])
        for ref in kit["artifacts"]:
            raw = (self.settings.workspace/ref["path"]).read_bytes()
            self.assertEqual(len(raw), ref["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), ref["sha256"])
            if ref["path"].endswith("/source.json"):
                self.assertEqual(json.loads(raw), kit["snapshot"])
                self.assertEqual(ref["sha256"], kit["source_sha256"])
            if ref["path"].endswith("/manifest.json"):
                self.assertFalse(json.loads(raw)["commercial_authority"])
        self.assertTrue(all(count == 0 for count in self.counts().values()))
        self.assertFalse(self.room.status()["initialized"])
        repeated = self.kits.prepare(body)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(repeated["kit"]["artifacts"], kit["artifacts"])

    def test_source_packet_is_captured_completely_and_binary_scope_stays_honest(self):
        self.desk_call("save_client", client=client())
        text = b'Title: Synthetic job\nCustomer: Example customer\nScope: Draft an internal plan\n'
        result = self.desk_call("prepare_intake_packet", client_id="provider-1", filename="request.txt", content_base64=base64.b64encode(text).decode())
        packet = self.desk.get_intake_packet(result["entity_id"])["packet"]
        accepted = self.desk_call("accept_intake_packet", packet_id=packet["id"], content_sha256=packet["content_sha256"], review_confirmed=True, intake=packet["proposed_intake"])
        source = self.kits.source(accepted["entity_id"])
        prepared = self.kits.prepare(self.body(intake_id=accepted["entity_id"], source_sha256=source["source_sha256"]))["kit"]
        self.assertEqual(prepared["snapshot"]["packet"]["text"], text.decode())
        self.assertEqual(prepared["snapshot"]["packet"]["raw_sha256"], hashlib.sha256(text).hexdigest())
        self.assertTrue(any("binary" in row for row in prepared["snapshot"]["limitations"]))
        self.assertIsNone(prepared["snapshot"]["proposal"])

    def test_linked_structured_proposal_is_captured_without_new_generation(self):
        from tests.test_structured_intake import IDENTITY, ROWS, TEXT, StructuredIntakeTests
        scripted = SimpleNamespace(calls=[], rows=copy.deepcopy(ROWS))
        self.desk.bind_structured_intake(lambda snapshot: StructuredIntakeTests.generate(scripted, snapshot), lambda: dict(IDENTITY))
        self.desk_call("save_client", client=client())
        result = self.desk_call("prepare_intake_packet", client_id="provider-1", filename="prose.txt", content_base64=base64.b64encode(TEXT.encode()).decode())
        packet = self.desk.get_intake_packet(result["entity_id"])["packet"]
        generated = self.desk_call("prepare_intake_proposal", packet_id=packet["id"], content_sha256=packet["content_sha256"])
        proposal = self.desk.get_intake_proposal(generated["entity_id"])["proposal"]
        accepted = self.desk_call("accept_intake_packet", packet_id=packet["id"], content_sha256=packet["content_sha256"], proposal_id=proposal["id"],
                                  proposal_sha256=proposal["content_sha256"], review_confirmed=True, intake=proposal["proposed_intake"])
        source = self.kits.source(accepted["entity_id"])
        prepared = self.kits.prepare(self.body(intake_id=accepted["entity_id"], source_sha256=source["source_sha256"]))["kit"]
        current_proposal = self.desk.get_intake_proposal(proposal["id"])["proposal"]
        self.assertEqual(prepared["snapshot"]["proposal"], current_proposal)
        self.assertEqual(prepared["snapshot"]["links"]["proposal"]["proposal_id"], proposal["id"])
        self.assertFalse(prepared["snapshot"]["intake"]["permission"]["confirmed"])
        self.assertEqual(len(scripted.calls), 1, "Preparing a kit never invokes the prior extraction callback")

    def test_complete_facts_preserve_reserved_output_token_through_store_expansion(self):
        value = intake(); value["source_notes"] = "Literal {{run_dir}} is supplied data.\nDo not change its meaning."
        kit = self.prepare(value)[0]["kit"]
        adopted = self.adopt(kit)
        workflow = self.store.workflow(kit["workflow_previews"][0]["id"])
        child = self.store.workflow_run(workflow["id"])
        block = child["prompt"].split("UNTRUSTED INTAKE JSON:\n", 1)[1].split("\nEND UNTRUSTED INTAKE JSON.", 1)[0]
        self.assertEqual(json.loads(block)["reviewed_intake_facts"], _core(value))
        self.assertIn("Literal {{run_dir}}", json.loads(block)["reviewed_intake_facts"]["source_notes"])
        self.assertNotIn("{{run_dir}}/draft", child["prompt"])
        self.assertIn("missions/"+child["id"]+"/draft.json", child["prompt"])
        self.assertFalse(adopted["cycle_started"])

    def test_source_markdown_cannot_escape_the_json_fence(self):
        value = intake(); value["source_notes"] = "Untrusted\n```\n<script>bad()</script>\n```\n[remote](https://example.invalid)"
        kit = self.prepare(value)[0]["kit"]
        ref = next(ref for ref in kit["artifacts"] if ref["path"].endswith("/source.md"))
        text = (self.settings.workspace/ref["path"]).read_text()
        self.assertEqual(text.count("\n```"), 2)
        block = text.split("```json\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(json.loads(block), _core(value))

    def test_oversized_complete_facts_refuse_before_any_publication_claim(self):
        value = intake(); value["source_notes"] = "x"*12000
        value["lines"] = [{**value["lines"][0], "description": "y"*500} for _ in range(5)]
        source = self.seed(value)
        with self.assertRaisesRegex(ValueError, "prompt bound|source pack"):
            self.kits.prepare(self.body(intake_id="job-1", source_sha256=source["source_sha256"]))
        self.assertEqual(self.kits.status()["kits"], [])
        self.assertFalse(list(self.settings.workspace.iterdir()))

    def test_failed_partial_publication_is_durable_and_never_replayed(self):
        source = self.seed()
        body = self.body(intake_id="job-1", source_sha256=source["source_sha256"])
        original = self.kits._publish_file; calls = []
        def fail_second(relative, raw):
            calls.append(relative)
            if len(calls) == 2:
                raise OSError("Synthetic second-file failure")
            return original(relative, raw)
        with patch.object(self.kits, "_publish_file", side_effect=fail_second), self.assertRaises(OSError):
            self.kits.prepare(body)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.kits.receipt(body["request_id"])["status"], "failed")
        self.assertEqual(self.kits.status()["kits"][0]["status"], "failed")
        with patch.object(self.kits, "_publish_file", side_effect=AssertionError("Must not replay")), self.assertRaises(HandoffConflict):
            self.kits.prepare(body)
        with self.assertRaises(HandoffConflict):
            self.kits.prepare(self.body(intake_id="job-1", source_sha256=source["source_sha256"]))
        self.assertFalse(self.room.status()["initialized"])

    def test_adoption_is_atomic_draft_creation_and_replay_does_not_duplicate(self):
        prepared = self.prepare()[0]["kit"]
        body = self.body(kit_id=prepared["id"], content_sha256=prepared["content_sha256"], expected_workroom_revision=0)
        result = self.kits.adopt(body)
        self.assertEqual(result["kit"]["status"], "adopted")
        self.assertEqual(self.counts()["workflows"], 2)
        self.assertEqual(self.counts()["runs"], 0)
        self.assertEqual(self.counts()["schedules"], 0)
        project = self.project(result["project_id"])
        self.assertIsNone(project["cycle"])
        self.assertEqual(project["task_counts"]["completed"], 0)
        self.assertEqual(project["tasks"][0]["required_source_refs"], prepared["artifacts"])
        self.assertEqual([workflow["agent_id"] for workflow in prepared["workflow_previews"]], ["offline_operations", "offline_research"])
        self.assertTrue(self.kits.adopt(body)["replayed"])
        self.assertEqual(self.counts()["workflows"], 2)

    def test_atomic_adoption_failure_rolls_back_both_workflows_and_project(self):
        kit = self.prepare()[0]["kit"]
        with patch.object(self.room, "_save_project", side_effect=ValueError("Synthetic atomic failure")), self.assertRaises(ValueError):
            self.adopt(kit)
        self.assertEqual(self.counts()["workflows"], 0)
        self.assertEqual(self.kits.kit(kit["id"])["kit"]["status"], "prepared")
        self.assertFalse(self.room.status()["initialized"])

    def test_adoption_checks_source_and_files_and_does_not_adopt_orphan_bytes(self):
        kit = self.prepare()[0]["kit"]
        path = self.settings.workspace/kit["artifacts"][0]["path"]
        original = path.read_bytes(); path.write_bytes(original+b" ")
        with self.assertRaises(ValueError):
            self.adopt(kit)
        self.assertEqual(self.counts()["workflows"], 0)
        self.assertFalse(self.room.status()["initialized"])

    def test_profile_drift_after_preparation_cannot_be_silently_adopted(self):
        kit = self.prepare()[0]["kit"]
        changed = client(); changed["name"] = "Changed issuer"
        self.desk_call("save_client", client=changed)
        with self.assertRaises(HandoffConflict):
            self.adopt(kit)
        self.assertEqual(self.counts()["workflows"], 0)

    def test_manual_accept_and_reject_require_all_source_identities_but_allow_extra_checked_refs(self):
        kit = self.prepare()[0]["kit"]; project = self.adopt(kit)["project_id"]
        for decision in ("accept", "reject"):
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                self.review(project, "source-review", kit["artifacts"][:-1], decision)
        extra = self.registry._path("owner-note.txt"); extra.write_bytes(b"Synthetic note")
        ref = {"path": "owner-note.txt", "bytes": extra.stat().st_size, "sha256": hashlib.sha256(extra.read_bytes()).hexdigest()}
        self.review(project, "source-review", [*kit["artifacts"], ref])
        gate = self.project(project)["tasks"][0]
        self.assertEqual(gate["evidence"]["assurance"], "owner_attested")
        self.assertEqual(len(gate["evidence"]["artifacts"]), 4)

    def test_source_gate_never_attests_automatically_and_tamper_blocks_dispatch(self):
        kit = self.prepare()[0]["kit"]; identifier = self.adopt(kit)["project_id"]
        project = self.project(identifier)
        self.room.start_cycle(self.room_body(project_id=identifier, content_sha256=project["content_sha256"], max_children=2, max_steps_per_child=4))
        self.assertTrue(self.room.tick())
        self.assertEqual(self.project(identifier)["tasks"][0]["status"], "awaiting_review")
        self.assertEqual(self.counts()["runs"], 0)
        self.review(identifier, "source-review", kit["artifacts"])
        source = self.settings.workspace/kit["artifacts"][0]["path"]
        source.write_bytes(source.read_bytes()+b"\n")
        self.assertTrue(self.room.tick())
        self.assertEqual(self.project(identifier)["cycle"]["status"], "held")
        self.assertEqual(self.counts()["runs"], 0)

    def test_output_checks_reject_missing_shape_false_authority_and_unbound_source(self):
        kit = self.prepare()[0]["kit"]
        first = kit["workflow_previews"][0]
        self.assertEqual(len(first["checks"]), 9)
        self.adopt(kit)
        run = self.store.workflow_run(first["id"])
        folder = self.registry._path(run["output_prefix"]); folder.mkdir(parents=True)
        good = {"source_sha256": kit["source_sha256"], "draft": True, "commercial_authority": False,
                "source_facts": ["Recorded fictional customer request"], "scope": "Draft scope only", "unknowns": [], "proposed_checklist": ["Owner reviews scope"]}
        (folder/"draft.md").write_text("DRAFT\n"+kit["source_sha256"])
        for change in ({"commercial_authority": True}, {"source_sha256": "0"*64}, {"source_facts": []}, {"scope": ""}):
            (folder/"draft.json").write_text(json.dumps({**good, **change}))
            self.assertEqual(verify(run["checks"], self.registry)["status"], "failed")
        (folder/"draft.json").write_text(json.dumps(good))
        self.assertEqual(verify(run["checks"], self.registry)["status"], "passed")

    def test_new_reserved_workflow_id_never_overwrites_an_existing_workflow(self):
        workflow = self.store.workflow_save("Existing", "Read local facts", [])
        with self.store.connect() as db, self.assertRaises(ValueError):
            self.store._save_workflow(db, "Replacement", "Different instructions", [], create_id=workflow["id"])
        self.assertEqual(self.store.workflow(workflow["id"])["name"], "Existing")

    def test_prepared_scopes_are_complete_frozen_offline_profiles(self):
        kit = self.prepare()[0]["kit"]
        expected_keys = {"id", "name", "profile_version", "tools", "connectivity", "approval_policy", "max_steps", "instruction"}
        for workflow in kit["workflow_previews"]:
            profile = workflow["resolved_agent"]
            self.assertEqual(set(profile), expected_keys)
            self.assertEqual(profile["connectivity"], "local_only")
            self.assertNotIn("web_fetch", profile["tools"])
            self.assertNotIn("web_search", profile["tools"])
            self.assertEqual(workflow["catalog_sha256"], _digest(profile))
        adopted = self.adopt(kit)
        actual_tasks = [task for task in self.project(adopted["project_id"])["tasks"] if task["kind"] == "workflow"]
        for task, prepared in zip(actual_tasks, kit["workflow_previews"]):
            self.assertEqual(task["execution_preview"]["resolved_agent"], prepared["resolved_agent"])
            self.assertEqual(task["execution_preview"]["catalog_sha256"], prepared["catalog_sha256"])

    def test_prepared_profile_drift_refuses_atomic_adoption(self):
        kit = self.prepare()[0]["kit"]
        catalog = self.room._agent_catalog()
        for profile in catalog["agents"]:
            if profile["id"] == "offline_research":
                profile["profile_version"] += 1
        self.room.bind_agent_catalog(lambda: copy.deepcopy(catalog))
        with self.assertRaisesRegex(HandoffConflict, "specialist"):
            self.adopt(kit)
        self.assertEqual(self.counts()["workflows"], 0)
        self.assertFalse(self.room.status()["initialized"])
        self.assertEqual(self.kits.kit(kit["id"])["kit"]["status"], "prepared")

    def test_nonlocal_profile_metadata_refuses_preparation_without_a_claim(self):
        source = self.seed()
        catalog = self.room._agent_catalog()
        for profile in catalog["agents"]:
            if profile["id"] == "offline_operations":
                profile["connectivity"] = "owner_policy"
        self.room.bind_agent_catalog(lambda: copy.deepcopy(catalog))
        with self.assertRaisesRegex(ValueError, "offline"):
            self.kits.prepare(self.body(intake_id="job-1", source_sha256=source["source_sha256"]))
        self.assertFalse(self.kits.status()["initialized"])
        self.assertEqual(self.kits.status()["kits"], [])
        self.assertFalse(list(self.settings.workspace.iterdir()))

    def managed_run(self):
        kit = self.prepare()[0]["kit"]
        self.adopt(kit)
        return self.store.workflow_run(kit["workflow_previews"][0]["id"])

    def test_managed_guard_binds_persistent_workflow_agent_and_exact_selected_model(self):
        run = self.managed_run()
        self.store.set_setting("model", "local-model:reviewed")
        self.assertEqual(guard_model_request(self.store, self.settings, run), {"model": "local-model:reviewed"})
        with self.assertRaisesRegex(ValueError, "specialist"):
            guard_model_request(self.store, self.settings, {**run, "agent_id": "general"})
        rebuilt = ProjectKits(self.registry, self.store, self.room, self.desk)
        self.assertEqual(rebuilt.guard_model_request(run), {"model": "local-model:reviewed"})

    def test_managed_guard_refuses_remote_or_ambiguous_transport_without_network(self):
        run = self.managed_run()
        for url in ("https://127.0.0.1:11434", "http://example.invalid:11434", "http://localhost:11434", "http://192.168.1.5:11434",
                    "http://127.0.0.1:11434/v1", "http://user:password@127.0.0.1:11434", "http://127.0.0.1:11434/?", "http://127.0.0.1:11434/#",
                    "http://[::1%25adapter]:11434", "\nhttp://127.0.0.1:11434"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "loopback"):
                guard_model_request(self.store, SimpleNamespace(ollama_url=url, model=self.settings.model), run)
        for url in ("http://127.0.0.1:11434", "http://127.0.0.2:11434/", "http://[::1]:11434", "http://[::ffff:127.0.0.1]:11434/"):
            with self.subTest(url=url):
                self.assertEqual(guard_model_request(self.store, SimpleNamespace(ollama_url=url, model=self.settings.model), run), {"model": self.settings.model})

    def test_managed_guard_refuses_cloud_marked_names_without_metadata_requests(self):
        run = self.managed_run()
        for model in ("qwen:cloud", "some-model:120b-cloud", "registry.example/tag:cloud", " qwen:Cloud "):
            self.store.set_setting("model", model)
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, "cloud"):
                guard_model_request(self.store, self.settings, run)

    def test_unmanaged_guard_keeps_ordinary_transport_behavior_and_does_not_create_tables(self):
        before = self.counts()
        remote = SimpleNamespace(ollama_url="https://example.invalid/provider", model="ordinary:cloud")
        self.assertIsNone(guard_model_request(self.store, remote, {}))
        self.assertIsNone(guard_model_request(self.store, remote, {"workflow_id": "ordinary-workflow"}))
        self.assertEqual(self.counts(), before)
        self.assertFalse(self.kits.status()["initialized"])
        self.prepare()
        self.assertIsNone(guard_model_request(self.store, remote, {"workflow_id": "ordinary-workflow"}))


if __name__ == "__main__":
    unittest.main()
