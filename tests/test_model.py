import unittest
from unittest.mock import patch

from ariadne.model import ModelError, Ollama


class ModelTests(unittest.TestCase):
    def test_output_limit_is_a_failure_not_completion(self):
        client = Ollama("http://127.0.0.1:11434")
        with patch.object(client, "request", return_value={"done_reason": "length", "message": {"role": "assistant", "content": "I will keep planning"}}):
            with self.assertRaisesRegex(ModelError, "output budget"):
                client.chat("qwen3:4b", [], [])

    def test_native_tool_contract_and_efficiency_options(self):
        client = Ollama("http://127.0.0.1:11434")
        reply = {"done_reason": "stop", "message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "calculate", "arguments": {"expression": "21*2"}}}]}, "eval_count": 15, "total_duration": 1000000000}
        with patch.object(client, "request", return_value=reply) as request:
            message, usage = client.chat("qwen3:4b", [{"role": "user", "content": "Calculate"}], [])
        payload = request.call_args.args[1]
        self.assertIs(payload["think"], False)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["options"]["num_predict"], 2048)
        self.assertEqual(message["tool_calls"][0]["function"]["arguments"]["expression"], "21*2")
        self.assertEqual(usage["duration_seconds"], 1)

    def test_invalid_tool_arguments_rejected(self):
        client = Ollama("http://127.0.0.1:11434")
        with patch.object(client, "request", return_value={"message": {"role": "assistant", "tool_calls": [{"function": {"name": "file_write", "arguments": "not an object"}}]}}):
            with self.assertRaises(ModelError):
                client.chat("qwen3:4b", [], [])

    def test_adapter_does_not_leak_remote_connection_errors(self):
        client = Ollama("http://127.0.0.1:11434")
        with patch.object(client.client, "open", side_effect=OSError("sensitive request details")):
            status = client.status()
        self.assertFalse(status["available"])
        self.assertNotIn("sensitive", status["error"])


if __name__ == "__main__":
    unittest.main()
