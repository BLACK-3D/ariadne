"""Private credentials survive a clean host without admitting environment hooks."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne.connector_credentials import load_connector_credentials
from ariadne.tools import ToolRegistry


class ConnectorCredentialsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".operations-test-credentials-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.settings = Settings(data_dir=self.data)
        self.path = self.data / "connector-secrets.json"
        self.settings.connectors_path.write_text(json.dumps({"connectors": [{"id": "ha_states", "description": "Fixture states", "method": "GET", "read_only": True,
            "url": "http://127.0.0.1:8123/api/states", "headers": {"Authorization": {"env": "ARIADNE_CONNECTOR_HA_AUTH"}}}]}), encoding="utf-8")
        self.registry = ToolRegistry(self.settings)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start(); self.addCleanup(patcher.stop)
        os.environ.pop("ARIADNE_CONNECTOR_HA_AUTH", None)

    def write(self, environment):
        self.path.write_text(json.dumps({"schema": 1, "environment": environment}), encoding="utf-8")

    def test_missing_file_is_optional_and_referenced_secret_loads_after_clean_environment(self):
        self.assertEqual(load_connector_credentials(self.registry), {"configured": False, "loaded": 0})
        self.write({"ARIADNE_CONNECTOR_HA_AUTH": "Bearer fixture-only"})
        with mock.patch("socket.socket", side_effect=AssertionError("No network")):
            result = load_connector_credentials(self.registry)
        self.assertEqual(result, {"configured": True, "loaded": 1})
        self.assertEqual(os.environ["ARIADNE_CONNECTOR_HA_AUTH"], "Bearer fixture-only")
        self.assertNotIn("fixture-only", json.dumps(result))

    def test_explicit_process_secret_wins_over_file_and_no_unknown_hooks_load(self):
        os.environ["ARIADNE_CONNECTOR_HA_AUTH"] = "Bearer explicit-fixture"
        self.write({"ARIADNE_CONNECTOR_HA_AUTH": "Bearer file-fixture"})
        self.assertEqual(load_connector_credentials(self.registry)["loaded"], 0)
        self.assertEqual(os.environ["ARIADNE_CONNECTOR_HA_AUTH"], "Bearer explicit-fixture")
        for name in ("SystemRoot", "PYTHONPATH", "HTTP_PROXY", "ARIADNE_OFFLINE", "ARIADNE_CONNECTOR_UNUSED"):
            with self.subTest(name=name):
                self.write({"ARIADNE_CONNECTOR_HA_AUTH": "Bearer not-loaded", name: "unsafe"})
                with self.assertRaises(ValueError):
                    load_connector_credentials(self.registry)
                self.assertEqual(os.environ["ARIADNE_CONNECTOR_HA_AUTH"], "Bearer explicit-fixture")

    def test_whole_file_validated_before_any_environment_mutation(self):
        for secret in (None, 7, "", "line\r\ninjection", "\x00", "x" * 4097, "nonascii-\u00e9"):
            with self.subTest(secret=str(secret)[:20]):
                self.write({"ARIADNE_CONNECTOR_HA_AUTH": secret})
                with self.assertRaises(ValueError):
                    load_connector_credentials(self.registry)
                self.assertNotIn("ARIADNE_CONNECTOR_HA_AUTH", os.environ)

    def test_duplicate_linked_and_oversized_files_rejected(self):
        self.path.write_text('{"schema":1,"environment":{},"environment":{}}', encoding="utf-8")
        with self.assertRaises(ValueError): load_connector_credentials(self.registry)
        self.path.write_bytes(b" " * 65_537)
        with self.assertRaises(ValueError): load_connector_credentials(self.registry)
        self.write({"ARIADNE_CONNECTOR_HA_AUTH": "Bearer fixture"})
        linked = self.data / "second-link.json"
        os.link(self.path, linked)
        with self.assertRaises(ValueError): load_connector_credentials(self.registry)


if __name__ == "__main__":
    unittest.main()
