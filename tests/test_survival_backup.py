"""Private multi-database snapshots include committed WAL data and reject links."""
import contextlib
import importlib.util
import io
import os
import sqlite3
import tempfile
import unittest
import zipfile
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class SurvivalBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".survival-backup-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "private-data"
        self.workspace = self.data / "workspace"
        self.workspace.mkdir(parents=True)
        self.settings = SimpleNamespace(data_dir=self.data, workspace=self.workspace)
        spec = importlib.util.spec_from_file_location("survival_backup_script", Path(__file__).resolve().parent.parent / "scripts" / "backup.py")
        self.backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.backup)

    def database(self, name, *, wal=False):
        connection = sqlite3.connect(self.data / name)
        if wal:
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE records(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.commit()
        if wal:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("INSERT INTO records VALUES (?, ?)", ("owner", name + " committed owner state"))
        connection.commit()
        if wal:
            self.addCleanup(connection.close)
            return connection
        connection.close()

    def run_backup(self, name="private-backup.zip"):
        destination = self.root / name
        with mock.patch.object(self.backup, "load_settings", return_value=self.settings), mock.patch("sys.argv", ["backup.py", str(destination)]), contextlib.redirect_stdout(io.StringIO()):
            self.backup.main()
        return destination

    def test_all_five_databases_restore_committed_wal_rows_with_integrity(self):
        live = []
        for name in self.backup.DATABASES:
            live.append(self.database(name, wal=True))
            self.assertGreater((self.data / (name + "-wal")).stat().st_size, 0)
            # The main file has no row before checkpoint; committed state is in WAL.
            main_copy = self.root / (name + ".main-only")
            main_copy.write_bytes((self.data / name).read_bytes())
            with contextlib.closing(sqlite3.connect(main_copy)) as baseline:
                self.assertEqual(baseline.execute("SELECT COUNT(*) FROM records").fetchone()[0], 0)
        destination = self.run_backup()
        restored = self.root / "restored"
        restored.mkdir()
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), set(self.backup.DATABASES) | {"BACKUP-MANIFEST.json"})
            self.assertIsNone(archive.testzip())
            for name in self.backup.DATABASES:
                target = restored / name
                target.write_bytes(archive.read(name))
                with contextlib.closing(sqlite3.connect(target)) as saved:
                    self.assertEqual(saved.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(saved.execute("SELECT key,value FROM records").fetchall(), [("owner", name + " committed owner state")])
        for name, connection in zip(self.backup.DATABASES, live):
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)
        self.assertEqual(list(self.data.glob("backup-*")), [])

    def test_private_configuration_and_workspace_preserved_without_model_duplication(self):
        for name in self.backup.DATABASES:
            self.database(name)
        private = {
            "server.token": b"fixture-private-pairing-key",
            "mcp.json": b'{"fixture":"keep byte-for-byte"}',
            "automation-config.json": b'{"fixture":"flows"}',
            "knowledge-config.json": b'{"fixture":"private references"}',
            "repository-radar-cache.json": b'{"fixture":"dated upstream baseline"}',
            "erpnext/bootstrap.env": b"FIXTURE_SECRET=owner-only\n",
        }
        for name, payload in private.items():
            path = self.data / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        (self.workspace / "owner-report.md").write_text("Keep this work product", encoding="utf-8")
        (self.data / "models").mkdir()
        (self.data / "models" / "weights.bin").write_bytes(b"MODEL_CACHE_MUST_NOT_BE_DUPLICATED")
        (self.data / "private-unselected-file.txt").write_bytes(b"Not a configured backup input")
        (self.root / "runtime").mkdir()
        (self.root / "runtime" / "engine.bin").write_bytes(b"RUNTIME_MUST_NOT_BE_DUPLICATED")
        destination = self.run_backup()
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), set(self.backup.DATABASES) | set(private) | {"workspace/owner-report.md", "BACKUP-MANIFEST.json"})
            for name, payload in private.items():
                self.assertEqual(archive.read(name), payload)
            self.assertEqual(archive.read("workspace/owner-report.md"), b"Keep this work product")
            self.assertFalse(any("-wal" in name or "-shm" in name or "-journal" in name for name in archive.namelist()))

    def test_missing_optional_databases_are_not_created(self):
        self.database("ariadne.sqlite3")
        destination = self.run_backup()
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), {"ariadne.sqlite3", "BACKUP-MANIFEST.json"})
        for name in self.backup.DATABASES[1:]:
            self.assertFalse((self.data / name).exists())
            for suffix in self.backup.SQLITE_SIDECARS:
                self.assertFalse((self.data / (name + suffix)).exists())

    def test_existing_backup_is_never_overwritten(self):
        destination = self.root / "private-backup.zip"
        destination.write_bytes(b"PREEXISTING_OWNER_BACKUP")
        with mock.patch.object(self.backup, "load_settings", side_effect=AssertionError("Must reject overwrite before loading settings")), mock.patch("sys.argv", ["backup.py", str(destination)]):
            with self.assertRaisesRegex(SystemExit, "overwrite"):
                self.backup.main()
        self.assertEqual(destination.read_bytes(), b"PREEXISTING_OWNER_BACKUP")

    def test_all_database_and_sidecar_paths_checked_before_any_sqlite_open(self):
        self.database("ariadne.sqlite3")
        checked = set()
        real_allowed, real_connect = self.backup._allowed_path, sqlite3.connect
        expected = {self.data / (name + suffix) for name in self.backup.DATABASES for suffix in ("", *self.backup.SQLITE_SIDECARS)}
        def allowed(path):
            result = real_allowed(path)
            checked.add(result)
            return result
        def connect(*args, **kwargs):
            self.assertTrue(expected <= checked, "SQLite opened before all source/sidecar guards")
            if kwargs.get("uri"):
                self.assertEqual(parse_qs(urlsplit(args[0]).query).get("mode"), ["ro"])
            return real_connect(*args, **kwargs)
        with mock.patch.object(self.backup, "_allowed_path", side_effect=allowed), mock.patch.object(self.backup.sqlite3, "connect", side_effect=connect):
            self.run_backup()

    def test_hardlinked_database_or_sidecar_rejected_before_sqlite_and_canary_unchanged(self):
        probe, probe_link = self.root / "link-probe", self.root / "link-probe-copy"
        probe.write_bytes(b"fixture")
        try:
            os.link(probe, probe_link)
        except (NotImplementedError, OSError):
            self.skipTest("Hard links unavailable under this filesystem or Windows policy")
        finally:
            if probe_link.exists():
                probe_link.unlink()
            probe.unlink()
        for name in self.backup.DATABASES:
            self.database(name)
        for name in self.backup.DATABASES:
            for suffix in ("", *self.backup.SQLITE_SIDECARS):
                with self.subTest(database=name, sidecar=suffix):
                    target = self.data / (name + suffix)
                    original = target.read_bytes() if target.exists() else None
                    if target.exists():
                        target.unlink()
                    canary = self.root / "unopened-canary"
                    canary.write_bytes(b"UNCHANGED_CANARY_NOT_A_SQLITE_DATABASE")
                    try:
                        os.link(canary, target)
                    except (NotImplementedError, OSError) as failure:
                        self.skipTest(f"Hard links unavailable: {failure}")
                    try:
                        with mock.patch.object(self.backup.sqlite3, "connect", side_effect=AssertionError("Must reject links before SQLite opens")) as opened:
                            with self.assertRaisesRegex(ValueError, "regular files without links"):
                                self.run_backup()
                            opened.assert_not_called()
                        self.assertEqual(canary.read_bytes(), b"UNCHANGED_CANARY_NOT_A_SQLITE_DATABASE")
                        self.assertFalse((self.root / "private-backup.zip").exists())
                    finally:
                        target.unlink()
                        if original is not None:
                            target.write_bytes(original)
                        canary.unlink()

    def test_directory_database_or_sidecar_is_rejected_before_sqlite(self):
        self.database("ariadne.sqlite3")
        for name in ("survival.sqlite3", "books.sqlite3-wal", "procedures.sqlite3-journal", "business.sqlite3-shm"):
            with self.subTest(path=name):
                target = self.data / name
                target.mkdir()
                try:
                    with mock.patch.object(self.backup.sqlite3, "connect", side_effect=AssertionError("Must reject directories before SQLite opens")) as opened:
                        with self.assertRaisesRegex(ValueError, "regular files without links"):
                            self.run_backup()
                        opened.assert_not_called()
                finally:
                    target.rmdir()


if __name__ == "__main__":
    unittest.main()
