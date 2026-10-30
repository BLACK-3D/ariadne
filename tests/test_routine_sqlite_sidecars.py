"""Native SQLite sidecar lifecycle and existing-main fail-closed regressions."""
from contextlib import contextmanager
import inspect
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class NoModel:
    def status(self): return {"available": False, "models": []}
    def chat(self, *args, **kwargs): raise AssertionError("No model in SQLite lifecycle qualification")


class RoutineSQLiteSidecarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".routine-sidecar-test-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)/"box with spaces + #scope"
        self.settings = Settings(data_dir=self.data, api_token="fixture-sidecar-owner")
        self.store = Store(self.data/"main with spaces+#.sqlite3")
        self.app = Application(self.settings, self.store, Engine(self.settings, self.store, NoModel()))
        self.addCleanup(self.app.stop_services)
        self.service = self.app.routines
        definition = self.service.save("Native metadata observation", ["business_snapshot"])
        self.run = self.service.enqueue(definition["id"], "known-sidecar-request")

    def get_run(self):
        statuses = []
        env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/api/routines/runs/known-sidecar-request",
               "HTTP_HOST": "127.0.0.1:8080", "HTTP_AUTHORIZATION": "Bearer fixture-sidecar-owner"}
        content = b"".join(self.app(env, lambda status, headers: statuses.append(status)))
        return int(statuses[0].split()[0]), json.loads(content)

    def writer(self):
        ready, release, closed = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def write():
            try:
                with self.store.connect() as db:
                    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('sidecar-observation-proof','1')")
                    db.commit()
                    ready.set()
                    if not release.wait(10):
                        raise AssertionError("The bounded writer was not released")
            except BaseException as error:
                errors.append(error)
                ready.set()
            finally:
                closed.set()
        thread = threading.Thread(target=write, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(10))
        self.assertEqual(errors, [])
        def finish():
            release.set(); thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
        return release, closed, finish

    def test_native_last_connection_close_does_not_turn_known_run_into_http_404(self):
        # The old exists/stat guard is forced to observe a real WAL/SHM and
        # then close SQLite's last connection before its second stat. The
        # repaired single-lstat guard never makes that stale exists decision.
        for suffix in ("-wal", "-shm"):
            with self.subTest(suffix=suffix):
                target = Path(str(self.store.path)+suffix)
                release, closed, finish = self.writer()
                original_exists = Path.exists
                self.assertTrue(original_exists(target), "Native SQLite must create the selected sidecar")
                triggered = []
                def observed_then_close(path):
                    exists = original_exists(path)
                    if path == target and exists and not triggered:
                        triggered.append(True)
                        release.set()
                        self.assertTrue(closed.wait(10))
                        self.assertFalse(original_exists(target), "Last SQLite close must remove its actual sidecar")
                    return exists
                try:
                    with mock.patch.object(Path, "exists", observed_then_close), mock.patch("subprocess.Popen", side_effect=AssertionError("No model/process")):
                        code, result = self.get_run()
                    self.assertEqual(code, 200, result)
                    self.assertEqual(result["run"]["id"], self.run["id"])
                    self.assertEqual(result["run"]["status"], "queued")
                finally:
                    finish()

    def test_native_sidecar_disappearing_at_single_lstat_is_tolerated(self):
        for suffix in ("-wal", "-shm"):
            with self.subTest(suffix=suffix):
                target = Path(str(self.store.path)+suffix)
                release, closed, finish = self.writer()
                original_lstat = Path.lstat
                self.assertTrue(target.exists())
                triggered, native_errors = [], []
                def close_at_guard(path):
                    caller = inspect.currentframe().f_back
                    direct_guard = caller.f_code.co_name == "database" and Path(caller.f_code.co_filename).name == "routines.py"
                    if path == target and direct_guard and not triggered:
                        triggered.append(True); release.set()
                        self.assertTrue(closed.wait(10))
                    try:
                        return original_lstat(path)
                    except FileNotFoundError as error:
                        if path == target and direct_guard:
                            native_errors.append((error.errno, error.filename))
                        raise
                try:
                    with mock.patch.object(Path, "lstat", close_at_guard):
                        code, result = self.get_run()
                    self.assertEqual(code, 200, result)
                    self.assertTrue(triggered)
                    self.assertEqual(len(native_errors), 1)
                    self.assertEqual(native_errors[0][0], 2)
                    self.assertEqual(Path(native_errors[0][1]), target)
                finally:
                    finish()

    def test_missing_expected_journal_is_optional_but_main_is_required(self):
        original_lstat = Path.lstat
        journal = Path(str(self.store.path)+"-journal")
        def vanished_journal(path):
            if path == journal:
                raise FileNotFoundError(2, "Expected SQLite journal is absent", str(path))
            return original_lstat(path)
        with mock.patch.object(Path, "lstat", vanished_journal):
            self.assertEqual(self.service.detail(self.run["id"])["id"], self.run["id"])
        aside = self.store.path.with_suffix(".held")
        self.store.path.rename(aside)
        try:
            with mock.patch.object(self.store, "connect", side_effect=AssertionError("Missing main must not connect")):
                with self.assertRaisesRegex(ValueError, "database is missing"):
                    self.service.detail(self.run["id"])
            self.assertFalse(self.store.path.exists())
        finally:
            aside.rename(self.store.path)

    def test_main_disappearing_after_guard_cannot_create_empty_replacement(self):
        original_connect = self.store.connect
        aside = self.store.path.with_suffix(".held-after-guard")
        observed = []
        @contextmanager
        def disappear(*, require_existing=False):
            self.assertTrue(require_existing)
            self.store.path.rename(aside)
            try:
                try:
                    with original_connect(require_existing=require_existing) as db:
                        yield db
                finally:
                    observed.append(self.store.path.exists())
            finally:
                aside.rename(self.store.path)
        with mock.patch.object(self.store, "connect", disappear):
            with self.assertRaises(sqlite3.OperationalError):
                self.service.detail(self.run["id"])
        self.assertEqual(observed, [False])
        self.assertEqual(self.service.detail(self.run["id"])["id"], self.run["id"])

    def test_existing_only_uri_preserves_names_and_default_bootstrap_creation(self):
        path = self.data/"new bootstrap + #name.sqlite3"
        isolated = object.__new__(Store)
        isolated.path = path
        with self.assertRaises(sqlite3.OperationalError):
            with isolated.connect(require_existing=True):
                self.fail("Missing main was created")
        self.assertFalse(path.exists())
        with isolated.connect() as db:
            db.execute("CREATE TABLE bootstrap(value TEXT)")
            db.execute("INSERT INTO bootstrap VALUES('retained')")
        with isolated.connect(require_existing=True) as db:
            self.assertEqual(db.execute("SELECT value FROM bootstrap").fetchone()[0], "retained")
        for value in (None, 0, "true", []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                with isolated.connect(require_existing=value): pass

    def test_hardlinked_and_directory_sidecars_are_rejected_before_sqlite(self):
        target = Path(str(self.store.path)+"-journal")
        sentinel = self.data/"link-source.bin"
        sentinel.write_bytes(b"fixture only")
        os.link(sentinel, target)
        try:
            with mock.patch.object(self.store, "connect", side_effect=AssertionError("Unsafe sidecar must never reach SQLite")):
                with self.assertRaises(ValueError): self.service.detail(self.run["id"])
        finally:
            target.unlink()
        target.mkdir()
        try:
            with mock.patch.object(self.store, "connect", side_effect=AssertionError("Directory sidecar must never reach SQLite")):
                with self.assertRaises(ValueError): self.service.detail(self.run["id"])
        finally:
            target.rmdir()

    def test_hardlinked_main_is_rejected_before_sqlite(self):
        alias = self.data/"main-hardlink.fixture"
        os.link(self.store.path, alias)
        try:
            with mock.patch.object(self.store, "connect", side_effect=AssertionError("Linked main must not reach SQLite")):
                with self.assertRaises(ValueError): self.service.detail(self.run["id"])
        finally:
            alias.unlink()

    def test_reparse_bit_permission_and_other_errors_are_not_ignored(self):
        target = Path(str(self.store.path)+"-journal")
        target.write_bytes(b"fixture only")
        original_lstat = Path.lstat
        info = original_lstat(target)
        reparse = SimpleNamespace(st_mode=stat.S_IFREG|0o600, st_nlink=1, st_file_attributes=0x400)
        def flagged(path):
            return reparse if path == target else original_lstat(path)
        try:
            with mock.patch.object(Path, "lstat", flagged), self.assertRaises(ValueError):
                self.service.detail(self.run["id"])
            for failure in (PermissionError(13, "Fixture denied", str(target)), OSError(5, "Fixture I/O failure", str(target))):
                def fail(path, selected=failure):
                    if path == target: raise selected
                    return original_lstat(path)
                with self.subTest(failure=type(failure).__name__), mock.patch.object(Path, "lstat", fail):
                    with self.assertRaises(type(failure)):
                        self.service.detail(self.run["id"])
        finally:
            target.unlink()


if __name__ == "__main__":
    unittest.main()
