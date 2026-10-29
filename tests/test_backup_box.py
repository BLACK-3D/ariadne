"""Box-level backup controls preserve owner scope and never launch recovered work."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class BackupBoxTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".recovery-test-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        spec = importlib.util.spec_from_file_location("backup_box_control", ROOT / "scripts/backup_box.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.root_patch = patch.object(self.module, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_one_click_ignores_inherited_data_and_connector_redirects(self):
        receipt = {"schema": 1, "verified": True, "sha256": "a" * 64}
        redirected = self.root / "different-owner"
        with patch.dict(os.environ, {"ARIADNE_DATA_DIR": str(redirected), "ARIADNE_CONNECTORS_PATH": str(redirected / "secret.json")}), patch.object(self.module, "create_backup", return_value=receipt) as create:
            result = self.module.run()
        destination, settings = create.call_args.args
        self.assertEqual(settings.data_dir, self.root / "data")
        self.assertEqual(settings.connectors_path, self.root / "data/connectors.json")
        self.assertTrue(destination.is_relative_to(self.root / "backups"))
        self.assertFalse(redirected.exists())
        self.assertFalse((self.root / "data").exists(), "Settings construction must not create a pairing key")
        self.assertEqual(json.loads(Path(result["receipt"]).read_text(encoding="utf-8")), receipt)

    def test_verify_rejects_archive_outside_this_box_without_opening_it(self):
        outside = ROOT / "other-owner-backup.zip"
        with patch.object(self.module, "verify_backup") as verify, patch.object(self.module, "create_backup") as create:
            with self.assertRaisesRegex(ValueError, "inside the box"):
                self.module.run(verify=outside)
        verify.assert_not_called()
        create.assert_not_called()
        self.assertFalse(outside.exists())

    def test_verify_does_not_load_settings_or_create_pairing_state(self):
        archive = self.root / "backups/existing.zip"
        receipt = {"verified": True}
        with patch.object(self.module, "Settings", side_effect=AssertionError("No owner initialization")), patch.object(self.module, "verify_backup", return_value=receipt) as verify, patch.object(self.module, "create_backup") as create:
            self.assertEqual(self.module.run(verify=archive), receipt)
        verify.assert_called_once_with(archive, self.root / "backups")
        create.assert_not_called()
        self.assertFalse((self.root / "data").exists())

    def test_receipt_write_failure_is_not_reported_as_completed(self):
        with patch.object(self.module, "create_backup", return_value={"verified": True}), patch.object(self.module, "publish_bytes", side_effect=OSError("disk full")), contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.module.main([]), 1)
        self.assertNotIn("Archive verified", output.getvalue())
        self.assertIn("No verified result is claimed", errors.getvalue())

    def test_console_failure_does_not_expose_private_payload(self):
        with patch.object(self.module, "create_backup", side_effect=ValueError("FIXTURE_PRIVATE_KEY_PAYLOAD")), contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.module.main([]), 1)
        self.assertNotIn("FIXTURE_PRIVATE_KEY_PAYLOAD", output.getvalue() + errors.getvalue())
        self.assertIn("ValueError", errors.getvalue())

    def test_no_initialization_when_box_database_does_not_exist(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.module.main([]), 1)
        self.assertFalse((self.root / "data/server.token").exists())
        self.assertFalse((self.root / "data/ariadne.sqlite3").exists())
        self.assertEqual(list((self.root / "backups").glob("*.zip")), [])


if __name__ == "__main__":
    unittest.main()
