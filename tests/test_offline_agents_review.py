"""Independent local-only specialist authority checks, with no real networking.

The global public Internet switch deliberately remains enabled in these tests.
Disposable state and a scripted model expose attempted authority violations.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ariadne.agents import AgentRegistry, resolve_agent
from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.store import Store


OFFLINE_AGENTS = ("offline_operations", "offline_research", "offline_developer")
EXTERNAL_TOOLS = ("web_search", "web_fetch", "browser_task", "local_flow",
                  "erpnext_read", "mcp_registered", "mcp_unregistered", "run_python")


def tool_call(name, arguments):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}


class ScriptedModel:
    def __init__(self):
        self.replies = []
        self.calls = []

    def chat(self, model, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        return (self.replies.pop(0) if self.replies else
                {"role": "assistant", "content": "Local fixture finished."}), {}


class OfflineAgentReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".offline-agent-review-", dir=Path.cwd())
        self.settings = Settings(data_dir=Path(self.temporary.name), api_token="synthetic-offline-review", max_steps=3)
        self.store = Store(self.settings.data_dir / "state.sqlite3")
        self.model = ScriptedModel()
        self.engine = Engine(self.settings, self.store, self.model)
        self.provider = Mock(return_value={"ok": True, "text": "Public fixture."})
        self.engine.internet_provider = self.provider
        self.engine.internet_enabled = lambda: True

    def tearDown(self):
        self.temporary.cleanup()

    def process(self, identifier):
        self.assertEqual(self.store.next_run()["id"], identifier)
        self.engine.process(identifier)
        return self.store.details(identifier)

    def test_explicit_profiles_are_fixed_and_do_not_inherit_public_or_registered_tools(self):
        dynamic = {"function": {"name": "mcp_registered"}}
        with patch.object(self.engine.tools.mcp, "schemas", return_value=[dynamic]):
            for identifier in OFFLINE_AGENTS:
                with self.subTest(agent=identifier):
                    self.assertEqual(resolve_agent("Browse the internet", identifier), identifier)
                    profile = AgentRegistry.profile(identifier)
                    self.assertIsInstance(profile.tools, frozenset)
                    self.assertTrue({"file_read", "file_write", "routine_read"} <= profile.tools)
                    for name in EXTERNAL_TOOLS:
                        self.assertFalse(self.engine.agents.allows(identifier, name), name)
        self.assertEqual(self.model.calls, [])
        self.provider.assert_not_called()

    def test_catalog_and_actual_model_schemas_exclude_external_tools_while_master_is_on(self):
        self.assertTrue(self.engine.internet_available())
        entries = {entry["id"]: entry for entry in self.engine.agents.catalog()["agents"]}
        self.assertEqual(self.engine.agents.catalog()["worker_concurrency"], 1)
        for identifier in OFFLINE_AGENTS:
            with self.subTest(agent=identifier):
                run = self.store.new_run("Search online documentation and then save a local report.", agent_id=identifier)
                detail = self.process(run["id"])
                self.assertEqual(detail["run"]["status"], "completed")
                offered = {schema["function"]["name"] for schema in self.model.calls[-1]["tools"]}
                self.assertFalse(set(EXTERNAL_TOOLS) & offered)
                self.assertFalse(set(EXTERNAL_TOOLS) & set(entries[identifier]["tools"]))
                system = self.model.calls[-1]["messages"][0]["content"]
                self.assertNotIn("Public Internet reading is enabled through web_search", system)
        self.provider.assert_not_called()

    def test_forged_model_web_and_service_calls_never_reach_either_executor(self):
        arguments = {"web_search": {"query": "public weather"}, "web_fetch": {"url": "https://example.org/"},
                     "browser_task": {"app": "owner-app", "path": "/", "actions": []},
                     "local_flow": {"id": "owner-flow"}, "erpnext_read": {"resource": "Contact"},
                     "mcp_registered": {}, "mcp_unregistered": {}, "run_python": {"code": "print(391)"}}
        for identifier in OFFLINE_AGENTS:
            for name in EXTERNAL_TOOLS:
                with self.subTest(agent=identifier, tool=name):
                    self.model.replies = [tool_call(name, arguments[name])]
                    run = self.store.new_run("Use the requested tool.", agent_id=identifier)
                    with patch.object(self.engine.tools, "execute") as execute:
                        detail = self.process(run["id"])
                    execute.assert_not_called()
                    self.provider.assert_not_called()
                    self.assertEqual(detail["approvals"], [])
                    self.assertTrue(any("outside the assigned" in message.get("content", "") for message in detail["messages"]))

    def test_old_approved_pending_web_call_is_invalidated_before_resume(self):
        for identifier in OFFLINE_AGENTS:
            with self.subTest(agent=identifier):
                run = self.store.new_run("Continue local evidence review.", agent_id=identifier)
                pending = {"name": "web_fetch", "args": {"url": "https://example.org/"}, "key": "1:0"}
                approval = self.store.approval(run["id"], pending["key"], pending["name"], pending["args"])
                self.store.approval_status(approval["id"], "approved")
                self.store.update_run(run["id"], pending=[pending])
                detail = self.process(run["id"])
                self.assertEqual(detail["approvals"][0]["status"], "unavailable")
        self.provider.assert_not_called()

    def test_offline_developer_preserves_code_approval_and_fingerprint_binding(self):
        arguments = {"code": "{'total': sum(data)}", "data": [17, 23]}
        self.model.replies = [tool_call("code_transform", arguments)]
        run = self.store.new_run("Compute the local fixture with approved constrained Monty.", agent_id="offline_developer")
        readiness = {name: {"ready": name == "monty"} for name in ("semantic", "docling", "monty", "kiwix")}
        with patch.object(self.engine.tools.knowledge, "status", return_value=readiness), \
             patch.object(self.engine.tools, "fingerprint", return_value="synthetic-bound-code-fixture"), \
             patch.object(self.engine.tools, "approval_description", return_value={"tool": "code_transform"}), \
             patch.object(self.engine.tools, "execute", return_value={"ok": True, "stdout": "391"}) as execute:
            detail = self.process(run["id"])
            self.assertEqual(detail["run"]["status"], "waiting_approval")
            self.assertIn("code_transform", {schema["function"]["name"] for schema in self.model.calls[0]["tools"]})
            execute.assert_not_called()
            self.store.decide(detail["approvals"][0]["id"], "approved")
            resumed = self.process(run["id"])
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args, ("code_transform", arguments))
        self.assertEqual(execute.call_args.kwargs["approval_fingerprint"], "synthetic-bound-code-fixture")
        self.assertTrue(callable(execute.call_args.kwargs["cancelled"]))
        self.assertEqual(resumed["approvals"][0]["status"], "executed")
        self.provider.assert_not_called()

    def test_pause_and_step_limit_apply_without_starting_additional_workers(self):
        run = self.store.new_run("Repeat local arithmetic.", agent_id="offline_operations")
        self.store.set_setting("paused", True)
        self.assertEqual(self.process(run["id"])["run"]["status"], "queued")
        self.assertEqual(self.model.calls, [])
        self.store.set_setting("paused", False)
        self.model.replies = [tool_call("calculate", {"expression": "1+1"})] * 8
        result = self.process(run["id"])
        self.assertEqual(result["run"]["status"], "exhausted")
        self.assertEqual(len(self.model.calls), 3)
        self.provider.assert_not_called()

    def test_legacy_general_and_research_keep_their_existing_enabled_web_scope(self):
        for identifier in ("general", "research"):
            with self.subTest(agent=identifier):
                self.model.replies = [tool_call("web_search", {"query": "public fixture"})]
                run = self.store.new_run("Research public fixture.", agent_id=identifier)
                detail = self.process(run["id"])
                self.assertEqual(detail["run"]["status"], "completed")
                self.assertIn("web_search", {schema["function"]["name"] for schema in self.model.calls[-1]["tools"]})
        self.assertEqual(self.provider.call_count, 2)
        self.assertEqual(resolve_agent("Transcribe audio"), "general")


if __name__ == "__main__":
    unittest.main()
