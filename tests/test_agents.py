"""Specialist scope is enforced at dispatch, schema and execution boundaries."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ariadne.agents import AgentRegistry, PROFILES, resolve_agent
from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.store import Store


def tool_call(name, args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": args}}]}


class Model:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []

    def chat(self, model, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        return (self.replies.pop(0) if self.replies else {"role": "assistant", "content": "Finished."}), {}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".agent-test-")
        self.settings = Settings(data_dir=Path(self.temp.name), api_token="qualification", max_steps=3)
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.model = Model()
        self.engine = Engine(self.settings, self.store, self.model)
        self.registry = self.engine.agents

    def tearDown(self):
        self.temp.cleanup()

    def process(self, run):
        self.assertEqual(self.store.next_run()["id"], run["id"])
        self.engine.process(run["id"])
        return self.store.details(run["id"])

    def test_concrete_profiles_cover_every_installed_tool_without_extra_model_calls(self):
        catalog = self.registry.catalog()
        self.assertEqual(len(catalog["agents"]), 16)
        self.assertEqual(len({p.id for p in PROFILES}), 16)
        installed = {s["function"]["name"] for s in self.engine.tools.schemas()}
        general = next(item for item in catalog["agents"] if item["id"] == "general")
        self.assertTrue(installed <= set(general["tools"]))
        self.assertEqual(catalog["worker_concurrency"], 1)
        self.assertEqual(catalog["idle_model_calls"], 0)
        self.assertEqual(self.model.calls, [])
        for agent in catalog["agents"]:
            self.assertEqual(agent["max_steps"], 3)
            self.assertEqual(agent["approval_policy"], "existing_tool_policy")

    def test_deterministic_dispatch_resolves_specific_capabilities(self):
        missions = {
            "Prepare the morning brief with weather and regional news": "briefing",
            "Create a parametric CAD mounting plate": "mechanical",
            "Analyze data from this dataset and csv": "data",
            "Review preparedness water reserve and battery power budget": "preparedness",
            "Transcribe this audio recording": "media",
            "Create PCB Gerber exports in KiCad": "electronics",
            "Inspect local GeoJSON and make a spatial buffer": "geospatial",
            "Build an automation with a Node-RED local flow": "automation",
            "An unusual task without a matching specialist": "general",
        }
        for prompt, expected in missions.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(resolve_agent(prompt, "auto"), expected)
        self.assertEqual(self.model.calls, [])

    def test_keyword_matching_has_word_boundaries_and_normalizes_unicode(self):
        self.assertEqual(resolve_agent("A voiced opinion about doing nothing", "auto"), "general")
        self.assertEqual(resolve_agent("Inspect ＧＥＯＪＳＯＮ", "auto"), "geospatial")

    def test_explicit_selection_wins_and_invalid_selection_fails_closed(self):
        self.assertEqual(resolve_agent("Transcribe audio", "business"), "business")
        self.assertEqual(resolve_agent("Transcribe audio"), "general")
        for value in ("administrator", "", True, [], "general\x00", "x" * 41):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.new_run("Anything", agent_id=value)
        self.assertEqual(self.store.runs(), [])

    def test_assignment_survives_reload_and_cannot_be_updated_as_run_state(self):
        run = self.store.new_run("Create a PCB in KiCad", agent_id="auto")
        self.assertEqual(run["agent_id"], "electronics")
        restored = Store(self.store.path)
        self.assertEqual(restored.run(run["id"])["agent_id"], "electronics")
        self.assertEqual(restored.runs()[0]["agent_id"], "electronics")
        self.assertEqual(restored.details(run["id"])["events"][0]["data"]["agent_id"], "electronics")
        with self.assertRaises(ValueError):
            restored.update_run(run["id"], agent_id="general")

    def test_old_database_migrates_existing_runs_to_general(self):
        run = self.store.new_run("Old owner mission")
        with self.store.connect() as db:
            db.execute("ALTER TABLE runs DROP COLUMN agent_id")
        restored = Store(self.store.path)
        self.assertEqual(restored.run(run["id"])["agent_id"], "general")

    def test_catalog_does_not_claim_schema_presence_proves_backend_readiness(self):
        before = next(a for a in self.registry.catalog()["agents"] if a["id"] == "mechanical")
        self.assertEqual(before["status"], "available")
        states = {"capabilities": [{"id": "cad", "status": "ready"}, {"id": "rendering", "status": "needs_setup"}]}
        after = next(a for a in self.registry.catalog(states)["agents"] if a["id"] == "mechanical")
        self.assertEqual(after["status"], "partial")
        self.assertIn({"id": "rendering", "status": "needs_setup"}, after["capabilities"])

    def test_specialist_schema_and_system_prompt_narrow_scope(self):
        run = self.store.new_run("Research local reference citations", agent_id="research")
        detail = self.process(run)
        names = {s["function"]["name"] for s in self.model.calls[0]["tools"]}
        self.assertNotIn("business_records", names)
        self.assertNotIn("run_python", names)
        self.assertNotIn("memory_save", names)
        self.assertIn("knowledge_search", names)
        system = self.model.calls[0]["messages"][0]["content"]
        self.assertIn("Research agent (research)", system)
        self.assertIn("does not grant authority", system)
        self.assertEqual(detail["run"]["status"], "completed")

    def test_model_cannot_invoke_a_tool_outside_assigned_scope(self):
        self.model.replies = [tool_call("business_records", {"action": "list"})]
        run = self.store.new_run("Research references", agent_id="research")
        with patch.object(self.engine.tools, "execute") as execute:
            detail = self.process(run)
        execute.assert_not_called()
        self.assertEqual(detail["approvals"], [])
        self.assertTrue(any("outside the assigned" in m.get("content", "") for m in detail["messages"]))

    def test_explicit_specialist_receives_its_tools_without_discovery_keywords(self):
        run = self.store.new_run("Create the requested artifact", agent_id="mechanical")
        self.process(run)
        names = {s["function"]["name"] for s in self.model.calls[0]["tools"]}
        self.assertIn("cad_part", names)
        self.assertIn("render_product", names)
        self.assertNotIn("business_records", names)

    def test_scope_is_rechecked_before_resuming_an_approved_pending_call(self):
        run = self.store.new_run("Research references", agent_id="research")
        pending = {"name": "business_records", "args": {"action": "post"}, "key": "1:0"}
        approval = self.store.approval(run["id"], pending["key"], pending["name"], pending["args"])
        self.store.approval_status(approval["id"], "approved")
        self.store.update_run(run["id"], pending=[pending])
        with patch.object(self.engine.tools, "execute") as execute:
            detail = self.process(run)
        execute.assert_not_called()
        self.assertEqual(detail["approvals"][0]["status"], "unavailable")

    def test_allowed_specialist_tool_retains_real_approval_and_resume(self):
        self.model.replies = [tool_call("office_create", {"path": "report.docx"})]
        run = self.store.new_run("Create a DOCX report", agent_id="documents")
        with patch.object(self.engine.tools, "fingerprint", return_value="test-bound-fingerprint"), \
             patch.object(self.engine.tools, "approval_description", return_value={"tool": "office_create"}), \
             patch.object(self.engine.tools, "execute", return_value={"ok": True}) as execute:
            detail = self.process(run)
            self.assertEqual(detail["run"]["status"], "waiting_approval")
            execute.assert_not_called()
            self.store.decide(detail["approvals"][0]["id"], "approved")
            detail = self.process(run)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(detail["run"]["status"], "completed")
        self.assertEqual(detail["approvals"][0]["status"], "executed")

    def test_specialist_has_same_pause_and_step_budget(self):
        run = self.store.new_run("Calculate", agent_id="mathematics")
        self.store.set_setting("paused", True)
        detail = self.process(run)
        self.assertEqual(detail["run"]["status"], "queued")
        self.assertEqual(self.model.calls, [])
        self.store.set_setting("paused", False)
        self.model.replies = [tool_call("calculate", {"expression": "1+1"})] * 8
        detail = self.process(run)
        self.assertEqual(detail["run"]["status"], "exhausted")
        self.assertEqual(len(self.model.calls), 3)

    def test_unknown_persisted_profile_fails_before_model_or_tool_execution(self):
        run = self.store.new_run("Old mission")
        with self.store.connect() as db:
            db.execute("UPDATE runs SET agent_id='removed-profile' WHERE id=?", (run["id"],))
        detail = self.process(run)
        self.assertEqual(detail["run"]["status"], "failed")
        self.assertEqual(self.model.calls, [])

    def test_dynamic_mcp_access_requires_registered_tool_and_integration_profile(self):
        schema = {"function": {"name": "mcp_registered"}}
        with patch.object(self.engine.tools.mcp, "schemas", return_value=[schema]):
            self.assertTrue(self.registry.allows("automation", "mcp_registered"))
            self.assertFalse(self.registry.allows("automation", "mcp_unregistered"))
            self.assertFalse(self.registry.allows("research", "mcp_registered"))

    def test_briefing_tool_is_absent_until_a_provider_is_configured(self):
        run = self.store.new_run("What is the weather?", agent_id="briefing")
        self.process(run)
        self.assertNotIn("briefing_read", {s["function"]["name"] for s in self.model.calls[0]["tools"]})
        card = next(a for a in self.registry.catalog()["agents"] if a["id"] == "briefing")
        self.assertNotIn("briefing_read", card["tools"])
        result = self.engine.execute_internal("briefing_read", {"action": "latest"})
        self.assertFalse(result["ok"])
        self.assertIn("unavailable", result["error"])

    def test_cached_report_reaches_briefing_model_without_refresh_or_extra_generation(self):
        evidence = {"report": {"content": "Cached forecast: rain. Source: https://weather.gov/\nObserved 2026-10-27; stale.",
                               "sha256": "fixture-hash", "generated_at": "2026-10-27T06:00:00-05:00"}}
        provider = Mock(return_value=evidence)
        self.engine.briefing_provider = provider
        self.model.replies = [tool_call("briefing_read", {"action": "latest"}),
                              {"role": "assistant", "content": "The cached report is dated October 27 and may be stale."}]
        run = self.store.new_run("What is the forecast?", agent_id="briefing")
        with patch.object(self.engine.tools, "execute") as execute:
            detail = self.process(run)
        execute.assert_not_called()
        provider.assert_called_once_with("latest")
        self.assertEqual(len(self.model.calls), 2)  # The requested mission itself; no nested generation.
        names = {s["function"]["name"] for s in self.model.calls[0]["tools"]}
        self.assertIn("briefing_read", names)
        self.assertIn("dated cached evidence only", self.model.calls[0]["messages"][0]["content"])
        supplied = next(m for m in self.model.calls[1]["messages"] if m.get("tool_name") == "briefing_read")
        self.assertIn("https://weather.gov/", supplied["content"])
        self.assertIn("stale", supplied["content"])
        self.assertEqual(detail["run"]["status"], "completed")

    def test_briefing_actions_are_strict_local_reads_and_status_has_no_model_call(self):
        provider = Mock(return_value={"enabled": True, "online_sources": False})
        self.engine.briefing_provider = provider
        for args in ({}, {"action": "refresh"}, {"action": "generate"}, {"action": "latest", "url": "https://example.com"},
                     {"action": True}, {"action": []}, []):
            with self.subTest(args=args):
                self.assertFalse(self.engine.execute_internal("briefing_read", args)["ok"])
        provider.assert_not_called()
        result = self.engine.execute_internal("briefing_read", {"action": "status"})
        provider.assert_called_once_with("status")
        self.assertTrue(result["ok"])
        self.assertFalse(result["online_sources"])
        self.assertEqual(self.model.calls, [])

    def test_briefing_reader_remains_scoped_to_general_briefing_operations(self):
        provider = Mock(return_value={"cached": True})
        self.engine.briefing_provider = provider
        for profile in PROFILES:
            self.assertEqual(self.registry.allows(profile.id, "briefing_read"),
                             profile.id in {"general", "briefing", "operations"})
        self.model.replies = [tool_call("briefing_read", {"action": "latest"})]
        run = self.store.new_run("Research a source", agent_id="research")
        self.process(run)
        provider.assert_not_called()
        self.assertNotIn("briefing_read", {s["function"]["name"] for s in self.model.calls[0]["tools"]})

    def test_removed_briefing_provider_fails_closed_for_a_persisted_pending_read(self):
        run = self.store.new_run("What is the forecast?", agent_id="briefing")
        self.store.update_run(run["id"], pending=[{"name": "briefing_read", "args": {"action": "latest"}, "key": "1:0"}])
        detail = self.process(run)
        result = next(event["data"]["result"] for event in detail["events"] if event["kind"] == "tool_result")
        self.assertFalse(result["ok"])
        self.assertIn("unavailable", result["error"])
        self.assertEqual(detail["approvals"], [])

    def test_briefing_provider_failures_do_not_become_success_claims(self):
        self.engine.briefing_provider = Mock(return_value={"ok": False, "error": "No report exists"})
        self.assertFalse(self.engine.execute_internal("briefing_read", {"action": "latest"})["ok"])
        self.engine.briefing_provider = Mock(return_value="not an evidence object")
        self.assertFalse(self.engine.execute_internal("briefing_read", {"action": "latest"})["ok"])


if __name__ == "__main__":
    unittest.main()
