"""Compact replies preserve mission authority, budgets and bounded voice context."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.model import Ollama, GPU_LOCK, ModelError
from ariadne.server import Application
from ariadne.store import Store


def reply(text="I help you reason, plan and work with configured local tools."):
    return {"role": "assistant", "content": text}


class ProbeModel:
    def __init__(self, fast=None, full=None):
        self.fast = list(fast or [reply()]); self.full = list(full or [reply("No action was taken.")]); self.calls = []
    def chat_fast(self, model, messages, tools):
        self.calls.append(("fast", messages, tools))
        return self.fast.pop(0), {"profile": "conversation", "prompt_tokens": 200, "output_tokens": 20}
    def chat(self, model, messages, tools):
        self.calls.append(("full", messages, tools))
        return self.full.pop(0), {"profile": "mission", "prompt_tokens": 6000, "output_tokens": 20}


class InteractiveLatencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="interactive-", dir=Path.cwd()/"runtime/tmp")
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name), max_steps=4)
        self.store = Store(self.settings.data_dir/"state.sqlite3")

    def process(self, model, prompt="What can you do?", **options):
        engine = Engine(self.settings, self.store, model)
        run = self.store.new_run(prompt, **options);self.store.update_run(run["id"], status="running")
        engine.process(run["id"])
        return engine, self.store.details(run["id"])

    def test_casual_reply_uses_small_prompt_without_retrieval_or_operational_schemas(self):
        model = ProbeModel(); engine = Engine(self.settings, self.store, model)
        run = self.store.new_run("What can you do?");self.store.update_run(run["id"], status="running")
        with patch.object(self.store, "search", side_effect=AssertionError("No private retrieval")), patch.object(engine.tools, "schemas", side_effect=AssertionError("No tool inventory")):
            engine.process(run["id"])
        detail = self.store.details(run["id"])
        self.assertEqual(detail["run"]["status"], "completed")
        self.assertEqual(detail["run"]["step_count"], 1)
        self.assertEqual([row[0] for row in model.calls], ["fast"])
        self.assertEqual([row["function"]["name"] for row in model.calls[0][2]], ["conversation_delegate"])
        self.assertLess(sum(len(row["content"]) for row in model.calls[0][1]), 2500)

    def test_returned_compact_tools_never_execute_and_delegation_consumes_step(self):
        model = ProbeModel(fast=[{"role":"assistant","content":"","tool_calls":[{"function":{"name":"file_write","arguments":{"path":"bad.txt","content":"bad"}}}]}])
        engine = Engine(self.settings, self.store, model)
        run = self.store.new_run("What can you do?");self.store.update_run(run["id"],status="running")
        with patch.object(engine.tools,"execute",side_effect=AssertionError("Routing cannot execute")):
            engine.process(run["id"])
        detail=self.store.details(run["id"])
        self.assertEqual([row[0] for row in model.calls],["fast","full"])
        self.assertEqual(detail["run"]["status"],"completed")
        self.assertEqual(detail["run"]["step_count"],2)
        self.assertFalse((self.settings.workspace/"bad.txt").exists())
        self.assertEqual(len([row for row in detail["events"] if row["kind"]=="model_usage"]),2)
        self.assertFalse(any(row.get("tool_calls") for row in detail["run"]["context"]))

    def test_unverified_completion_claim_falls_back(self):
        model=ProbeModel(fast=[reply("I created your report and sent it.")])
        _,detail=self.process(model)
        self.assertEqual([row[0] for row in model.calls],["fast","full"])
        self.assertEqual(detail["messages"][-1]["content"],"No action was taken.")

    def test_full_work_and_output_checks_never_use_compact_route(self):
        model=ProbeModel(full=[reply("No file exists yet.")])
        _,detail=self.process(model,"What can you do?",checks=[{"kind":"file_exists","path":"required.txt"}])
        # Failed checks request repair; an exhausted fake response is a failure,
        # never a compact-path success or fabricated artifact.
        self.assertEqual(model.calls[0][0],"full")
        self.assertFalse(any(row[0]=="fast" for row in model.calls))
        self.assertNotEqual(detail["run"]["verification_status"],"passed")

    def test_compact_delegation_cannot_expand_the_step_budget(self):
        settings=Settings(data_dir=self.settings.data_dir,max_steps=1)
        model=ProbeModel(fast=[{"role":"assistant","content":"","tool_calls":[{"function":{"name":"conversation_delegate","arguments":{}}}]}])
        engine=Engine(settings,self.store,model);run=self.store.new_run("Who are you?")
        self.store.update_run(run["id"],status="running");engine.process(run["id"])
        self.assertEqual(self.store.run(run["id"])["status"],"exhausted")
        self.assertEqual(len(model.calls),1)

    def test_cancelled_compact_reply_is_not_published(self):
        model=ProbeModel();engine=Engine(self.settings,self.store,model)
        run=self.store.new_run("Who are you?");self.store.update_run(run["id"],status="running")
        def cancel(*args):
            self.store.update_run(run["id"],status="cancelled")
            return reply(),{}
        with patch.object(model,"chat_fast",side_effect=cancel):engine.process(run["id"])
        self.assertEqual(self.store.run(run["id"])["status"],"cancelled")
        self.assertFalse(any(row["kind"]=="completed" for row in self.store.details(run["id"])["events"]))

    def test_voice_turns_share_only_the_current_wake_conversation(self):
        app=object.__new__(Application);app.store=self.store;app.engine=Engine(self.settings,self.store,ProbeModel())
        app.wake_voice=SimpleNamespace(conversation_id="voice-conversation-a")
        first=app.submit_voice("Who are you?")
        self.store.update_run(first["id"],status="completed",context=[{"role":"user","content":"Who are you?"},reply("I am Ariadne.")])
        follow=app.submit_voice("Tell me more.")
        self.assertEqual(first["session_id"],follow["session_id"])
        self.assertEqual(len(first["session_id"]),32)
        self.assertTrue(all(c in "0123456789abcdef" for c in first["session_id"]))
        self.assertIn("I am Ariadne.",str(self.store.run(follow["id"])["context"]))
        self.store.update_run(follow["id"],status="completed")
        app.wake_voice.conversation_id="voice-conversation-b"
        fresh=app.submit_voice("Who are you?")
        self.assertNotEqual(fresh["session_id"],first["session_id"])
        self.assertEqual(self.store.run(fresh["id"])["context"],[{"role":"user","content":"Who are you?"}])

    def test_voice_history_is_bounded_without_cropping_current_input(self):
        for index in range(4):
            run=self.store.new_run("Past "+str(index)+"x"*4000,session_id="bounded",interaction="voice")
            self.store.update_run(run["id"],status="completed",context=[reply("y"*6000)])
        run=self.store.new_run("Exact new question.",session_id="bounded",interaction="voice")
        context=run["context"]
        self.assertLessEqual(sum(len(row["content"]) for row in context[:-1]),4000)
        self.assertEqual(context[-1]["content"],"Exact new question.")

    def test_warm_is_one_background_load_and_respects_pause(self):
        entered=threading.Event();release=threading.Event();calls=[]
        class Warmer:
            def warm(self,model):
                calls.append(model);entered.set();release.wait(3)
                return {"state":"ready","inference_tokens":0}
        engine=Engine(self.settings,self.store,Warmer())
        self.addCleanup(release.set)
        engine.warm_selected_model();self.assertTrue(entered.wait(2))
        engine.warm_selected_model();self.assertEqual(len(calls),1)
        release.set();engine.model_warm_thread.join(2)
        self.assertEqual(engine.model_warm_state["inference_tokens"],0)
        self.store.set_setting("paused",True);engine.warm_selected_model();self.assertEqual(len(calls),1)

    def test_optional_warm_failure_does_not_fail_server_startup(self):
        class Warmer:
            def warm(self,model):raise RuntimeError("Fixture failure")
        engine=Engine(self.settings,self.store,Warmer());engine.warm_selected_model();engine.model_warm_thread.join(2)
        self.assertEqual(engine.model_warm_state["state"],"unavailable")


class ModelLatencyTests(unittest.TestCase):
    def test_fast_keeps_context_allocation_and_reports_load_prefill_decode(self):
        client=Ollama("http://127.0.0.1:11434")
        result={"message":reply(),"eval_count":20,"prompt_eval_count":220,"prompt_eval_cached_count":100,
                "total_duration":500_000_000,"load_duration":10_000_000,"prompt_eval_duration":90_000_000,"eval_duration":400_000_000}
        with patch.object(client,"request",return_value=result) as request:
            _,usage=client.chat_fast("qwen3.5:9b",[],[])
        payload=request.call_args.args[1]
        self.assertEqual(payload["options"]["num_ctx"],16384)
        self.assertEqual(payload["options"]["num_predict"],384)
        self.assertEqual(payload["keep_alive"],"15m")
        self.assertIs(payload["think"],False)
        self.assertEqual([usage[k] for k in ("load_seconds","prefill_seconds","decode_seconds")],[.01,.09,.4])
        self.assertEqual(usage["cached_prompt_tokens"],100)

    def test_warm_loads_no_output_and_does_not_unload_or_generate(self):
        client=Ollama("http://127.0.0.1:11434")
        with patch.object(client,"request",side_effect=[{"models":[]},{"done_reason":"load","eval_count":0}]) as request:
            result=client.warm("qwen3.5:9b")
        self.assertEqual(result["inference_tokens"],0)
        payload=request.call_args.args[1]
        self.assertEqual(payload["messages"],[])
        self.assertEqual(payload["keep_alive"],"15m")
        self.assertNotIn("tools",payload)

    def test_resident_warm_skips_empty_chat_and_preserves_prompt_cache(self):
        client=Ollama("http://127.0.0.1:11434")
        with patch.object(client,"request",return_value={"models":[{"name":"qwen3.5:9b","context_length":16384}]}) as request:
            result=client.warm("qwen3.5:9b")
        self.assertTrue(result["already_resident"])
        request.assert_called_once_with("/api/ps",timeout=3)

    def test_busy_gpu_is_not_queued_by_warm(self):
        ready=threading.Event();release=threading.Event()
        def hold():
            with GPU_LOCK:ready.set();release.wait(3)
        thread=threading.Thread(target=hold);thread.start();self.assertTrue(ready.wait(2))
        try:
            client=Ollama("http://127.0.0.1:11434")
            with patch.object(client,"request",side_effect=AssertionError("Busy warm must not call")):
                self.assertEqual(client.warm("qwen3.5:9b")["state"],"busy")
        finally:release.set();thread.join(2)

    def test_warm_does_not_displace_another_resident_model(self):
        client=Ollama("http://127.0.0.1:11434")
        with patch.object(client,"request",return_value={"models":[{"name":"another:8b","context_length":16384}]}) as request:
            self.assertEqual(client.warm("qwen3.5:9b")["state"],"busy")
        request.assert_called_once_with("/api/ps",timeout=3)


if __name__=="__main__":unittest.main()
