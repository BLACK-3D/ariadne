import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ariadne.locking import InstanceLock
from ariadne.store import Store


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".recovery-test-", dir=Path.cwd())
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_one_worker_owns_state(self):
        first = InstanceLock(self.root / "server.lock")
        second = InstanceLock(self.root / "server.lock")
        first.acquire()
        try:
            with self.assertRaises(ValueError):
                second.acquire()
        finally:
            first.release()
        second.acquire()
        second.release()

    def backup_module(self):
        spec = importlib.util.spec_from_file_location("ariadne_backup", Path(__file__).resolve().parent.parent / "scripts/backup.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_pairing_key_is_published_only_after_complete_staging(self):
        from ariadne.config import _persistent_token
        directory = self.root / "pairing"
        with patch("ariadne.atomic.commit_staged", side_effect=RuntimeError("injected interruption")):
            with self.assertRaisesRegex(RuntimeError, "injected interruption"):
                _persistent_token(directory)
        self.assertFalse((directory / "server.token").exists())
        token = _persistent_token(directory)
        saved = (directory / "server.token").read_bytes()
        self.assertGreaterEqual(len(token), 40)
        with patch("ariadne.atomic.publish_bytes", side_effect=OSError("injected disk full")):
            self.assertEqual(_persistent_token(directory), token)
        self.assertEqual((directory / "server.token").read_bytes(), saved)

    def test_invalid_existing_owner_pairing_key_is_not_replaced(self):
        from ariadne.config import _persistent_token
        directory = self.root / "owner-pairing"
        directory.mkdir()
        path = directory / "server.token"
        path.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "server.token is invalid"):
            _persistent_token(directory)
        self.assertEqual(path.read_bytes(), b"")

    def test_consistent_backup_closes_handles(self):
        directory = self.root / "data"
        store = Store(directory / "ariadne.sqlite3")
        store.memory_add("Keep this note")
        (directory / "voice-config.json").write_text('{"whisper_bin":"example"}', encoding="utf-8")
        for name in ("mcp.json", "ocr-config.json"):
            (directory / name).write_text('{"fixture":"preserve-exactly"}', encoding="utf-8")
        workspace = directory / "workspace"
        workspace.mkdir()
        (workspace / "plan.md").write_text("Actual artifact", encoding="utf-8")
        destination = self.root / "backup.zip"
        with patch.dict(os.environ, {"ARIADNE_DATA_DIR": str(directory)}), patch("sys.argv", ["backup.py", str(destination)]):
            self.backup_module().main()
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(archive.read("workspace/plan.md"), b"Actual artifact")
            self.assertIn("voice-config.json", archive.namelist())
            for name in ("mcp.json", "ocr-config.json"):
                self.assertEqual(archive.read(name), b'{"fixture":"preserve-exactly"}')
            snapshot = self.root / "snapshot.sqlite3"
            snapshot.write_bytes(archive.read("ariadne.sqlite3"))
            db = sqlite3.connect(snapshot)
            try:
                self.assertEqual(db.execute("SELECT content FROM memories").fetchone()[0], "Keep this note")
            finally:
                db.close()
        self.assertEqual(list(directory.glob("backup-*")), [])

    def test_backup_cannot_include_itself(self):
        directory = self.root / "data"
        Store(directory / "ariadne.sqlite3")
        destination = directory / "workspace" / "backup.zip"
        with patch.dict(os.environ, {"ARIADNE_DATA_DIR": str(directory)}), patch("sys.argv", ["backup.py", str(destination)]):
            with self.assertRaisesRegex(SystemExit, "outside the workspace"):
                self.backup_module().main()
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
