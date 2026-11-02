"""Provider boundary fixtures; no socket, model process or owner data is used."""
import copy
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from ariadne.model import CONTEXT_TOKENS, GPU_LOCK, ModelError, Ollama


MODEL = "qwen3.5:9b"
DIGEST = "a" * 64
SCHEMA = {"type": "object", "properties": {"suggestions": {"type": "array"}}, "required": ["suggestions"]}
MESSAGES = [{"role": "system", "content": "Extract literal claims only."}, {"role": "user", "content": "Fictional source."}]


class StructuredModelTests(unittest.TestCase):
    def setUp(self):
        self.client = Ollama("http://127.0.0.1:11434", timeout=180)
        self.calls = []
        self.tag_count = 0
        self.tags = {"models": [{"name": MODEL, "digest": DIGEST, "size": 6000000000}]}
        self.show = {"details": {"format": "gguf"}, "model_info": {"general.architecture": "qwen"}, "capabilities": ["completion"]}
        self.reply = {"model": MODEL, "done": True, "done_reason": "stop",
                      "message": {"role": "assistant", "content": '{"suggestions":[]}'},
                      "prompt_eval_count": 200, "eval_count": 8, "total_duration": 1234567890}
        self.patch = patch.object(self.client, "request", side_effect=self.request)
        self.mock = self.patch.start()
        self.addCleanup(self.patch.stop)

    def request(self, path, payload=None, **kwargs):
        self.calls.append((path, copy.deepcopy(payload), kwargs))
        if path == "/api/tags":
            self.tag_count += 1
            return copy.deepcopy(self.tags)
        if path == "/api/show":
            return copy.deepcopy(self.show)
        if path == "/api/chat":
            return copy.deepcopy(self.reply)
        self.fail("Unexpected provider request")

    def run_extract(self, **kwargs):
        return self.client.structured_extract(kwargs.get("model", MODEL), kwargs.get("messages", MESSAGES), kwargs.get("schema", SCHEMA))

    def test_exactly_one_generation_with_schema_and_integer_usage(self):
        content, usage = self.run_extract()
        self.assertEqual(content, '{"suggestions":[]}')
        self.assertEqual([row[0] for row in self.calls], ["/api/tags", "/api/show", "/api/chat", "/api/tags"])
        payload = self.calls[2][1]
        self.assertEqual(payload["format"], SCHEMA)
        self.assertEqual(payload["tools"], [])
        for field in ("think", "stream", "truncate", "shift"):
            self.assertIs(payload[field], False)
        self.assertEqual(payload["options"], {"temperature": 0, "num_ctx": CONTEXT_TOKENS, "num_predict": 1024})
        self.assertEqual(usage["generation_requests"], 1)
        self.assertEqual(usage["metadata_requests"], 3)
        self.assertEqual(usage["model_digest"], DIGEST)
        self.assertEqual(usage["duration_ms"], 1234)
        self.assertIsNone(usage["load_ms"])
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        self.assertEqual(usage["request_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(all(0 < row[2]["timeout"] <= 180 for row in self.calls))
        self.assertTrue(all(row[2]["timeout"] <= 10 for row in self.calls if row[0] != "/api/chat"))

    def test_identity_is_passive_and_rejects_nonlocal_or_ambiguous_urls(self):
        self.assertEqual(self.client.structured_identity(MODEL)["model"], MODEL)
        self.mock.assert_not_called()
        for url in ("https://example.test", "http://localhost:11434", "http://127.0.0.1", "http://127.0.0.1:11434/a",
                    "http://127.0.0.1:11434?proxy=1", "http://127.0.0.1:11434#source", "http://u:p@127.0.0.1:11434"):
            with self.subTest(url=url), self.assertRaises((ModelError, ValueError)):
                Ollama(url).structured_extract(MODEL, MESSAGES, SCHEMA)
        self.mock.assert_not_called()

    def test_cloud_selectors_rejected_before_any_request(self):
        for model in ("gpt-oss:120b-cloud", "gemma4:cloud", "model:cloud-preview", "bad name", ""):
            with self.subTest(model=model), self.assertRaises(ModelError):
                self.run_extract(model=model)
        self.mock.assert_not_called()

    def test_missing_or_duplicate_installation_never_receives_source(self):
        for rows in ([], [self.tags["models"][0]] * 2):
            self.tags["models"] = rows
            with self.assertRaises(ModelError):
                self.run_extract()
        self.assertTrue(all(path == "/api/tags" for path, _, _ in self.calls))

    def test_remote_alias_rejected_during_metadata(self):
        self.show["remote_host"] = "https://ollama.com"
        with self.assertRaises(ModelError):
            self.run_extract()
        self.assertEqual([row[0] for row in self.calls], ["/api/tags", "/api/show"])
        self.assertNotIn("Fictional source.", json.dumps([row[1] for row in self.calls]))

    def test_invalid_download_identity_and_metadata_fail_before_generation(self):
        original = copy.deepcopy(self.tags)
        for change in ({"digest": "bad"}, {"size": True}, {"size": 0}, {"remote_model": "remote"}):
            self.tags = copy.deepcopy(original)
            self.tags["models"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ModelError):
                self.run_extract()
        self.assertFalse(any(row[0] == "/api/chat" for row in self.calls))

    def test_non_gguf_and_missing_completion_metadata_fail_closed(self):
        original = copy.deepcopy(self.show)
        for change in ({"details": {}}, {"model_info": {}}, {"capabilities": []}, {"remote_model": "remote"}):
            self.show = {**copy.deepcopy(original), **change}
            with self.subTest(change=change), self.assertRaises(ModelError):
                self.run_extract()
        self.assertFalse(any(row[0] == "/api/chat" for row in self.calls))

    def test_tool_refusal_thinking_wrong_role_and_truncation_rejected(self):
        original = copy.deepcopy(self.reply)
        for change in ({"tool_calls": [{"function": {"name": "write"}}]}, {"refusal": "refused"}, {"thinking": "hidden"}, {"role": "user"}, {"content": ""}):
            self.reply = copy.deepcopy(original)
            self.reply["message"].update(change)
            with self.subTest(change=change), self.assertRaises(ModelError):
                self.run_extract()
        for change in ({"done": False}, {"done_reason": "length"}, {"remote_host": "https://remote.test"}):
            self.reply = {**copy.deepcopy(original), **change}
            with self.subTest(change=change), self.assertRaises(ModelError):
                self.run_extract()

    def test_huge_output_and_noninteger_usage_rejected(self):
        self.reply["message"]["content"] = "x" * 24001
        with self.assertRaises(ModelError):
            self.run_extract()
        self.reply["message"]["content"] = '{"suggestions":[]}'
        self.reply["eval_count"] = True
        with self.assertRaises(ModelError):
            self.run_extract()

    def test_installed_digest_drift_does_not_retry_generation(self):
        def changed(path, payload=None, **kwargs):
            result = self.request(path, payload, **kwargs)
            if path == "/api/tags" and self.tag_count == 2:
                result["models"][0]["digest"] = "b" * 64
            return result
        self.mock.side_effect = changed
        with self.assertRaises(ModelError):
            self.run_extract()
        self.assertEqual(sum(row[0] == "/api/chat" for row in self.calls), 1)

    def test_busy_model_is_not_queued(self):
        held, release = threading.Event(), threading.Event()
        def own():
            with GPU_LOCK:
                held.set()
                release.wait(5)
        thread = threading.Thread(target=own)
        thread.start()
        try:
            self.assertTrue(held.wait(2))
            with self.assertRaisesRegex(ModelError, "busy"):
                self.run_extract()
            self.mock.assert_not_called()
        finally:
            release.set()
            thread.join(2)

    def test_frozen_endpoint_and_client_survive_mutation(self):
        original_opener = self.client.client
        def mutate(path, payload=None, **kwargs):
            self.client.url = "https://remote.example.test"
            self.client.client = object()
            self.assertEqual(kwargs["_target"], ("http://127.0.0.1:11434", original_opener))
            return self.request(path, payload, **kwargs)
        self.mock.side_effect = mutate
        self.run_extract()
        self.assertEqual(sum(row[0] == "/api/chat" for row in self.calls), 1)

    def test_input_envelopes_fail_without_metadata(self):
        for messages in ([], MESSAGES + MESSAGES, [{"role": "user", "content": "x"}] * 2,
                         [{"role": "system", "content": "x", "images": []}, MESSAGES[1]],
                         [MESSAGES[0], {"role": "user", "content": "x" * 48001}]):
            with self.subTest(messages=str(messages)[:60]), self.assertRaises(ModelError):
                self.run_extract(messages=messages)
        for schema in ([], {"type": "string"}, {"type": "object", "description": "x" * 8193}):
            with self.subTest(schema=str(schema)[:60]), self.assertRaises(ModelError):
                self.run_extract(schema=schema)
        self.mock.assert_not_called()

    def test_transport_uses_frozen_url_and_opener_instead_of_live_fields(self):
        client = Ollama("http://127.0.0.1:11434")
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        client.url = "https://remote.example.test"
        client.client = object()
        result = client.request("/api/tags", _target=("http://127.0.0.1:11434", opener))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(opener.open.call_args.args[0].full_url, "http://127.0.0.1:11434/api/tags")


if __name__ == "__main__":
    unittest.main()
