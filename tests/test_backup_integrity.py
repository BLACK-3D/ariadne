"""Private backup checks reject damaged, aliased and executable recovery inputs."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from ariadne import backup
from ariadne.config import Settings


class BackupIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".backup-integrity-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.workspace = self.data / "workspace"
        self.workspace.mkdir(parents=True)
        self.settings = Settings(data_dir=self.data)
        with contextlib.closing(sqlite3.connect(self.data / "ariadne.sqlite3")) as database:
            database.execute("CREATE TABLE records(value TEXT)")
            database.execute("INSERT INTO records VALUES('fixture owner data')")
            database.commit()
        (self.workspace / "plan.md").write_text("PRIVATE_FIXTURE_DOCUMENT", encoding="utf-8")
        (self.data / "server.token").write_text("PRIVATE_FIXTURE_KEY", encoding="ascii")
        self.archive = self.root / "backup.zip"

    def create(self):
        return backup.create_backup(self.archive, self.settings)

    def members(self):
        with zipfile.ZipFile(self.archive) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def rewrite(self, members, mutate_info=None):
        # The tested reader must reject malformed inputs before SQLite sees any
        # unverified data. ZIP writes here only produce disposable test fixtures.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for index, (name, value) in enumerate(members.items() if isinstance(members, dict) else members):
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    if mutate_info:
                        mutate_info(info, index)
                    archive.writestr(info, value)

    def mutate_manifest(self, mutate):
        members = self.members()
        manifest = json.loads(members[backup.MANIFEST_NAME])
        mutate(manifest)
        members[backup.MANIFEST_NAME] = json.dumps(manifest).encode()
        self.rewrite(members)

    def verify(self):
        return backup.verify_backup(self.archive, self.root)

    def test_snapshot_readback_manifest_receipt_and_no_private_output(self):
        receipt = self.create()
        self.assertEqual(receipt, self.verify())
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["member_count"], 3)
        self.assertEqual(receipt["database_count"], 1)
        self.assertEqual(receipt["archive_sha256"], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assertEqual(receipt["bytes"], self.archive.stat().st_size)
        self.assertIn("not one global", receipt["warnings"][0])
        self.assertNotIn("PRIVATE_FIXTURE", json.dumps(receipt))
        members = self.members()
        manifest = json.loads(members.pop(backup.MANIFEST_NAME))
        for row in manifest["files"]:
            self.assertEqual(row["sha256"], hashlib.sha256(members[row["path"]]).hexdigest())
            self.assertEqual(row["bytes"], len(members[row["path"]]))
        self.assertEqual(list(self.data.glob("backup-*")), [])
        self.assertEqual(list(self.root.glob("backup-verify-*")), [])

    def test_changed_payload_fails_even_with_valid_zip_crc(self):
        self.create()
        members = self.members()
        members["workspace/plan.md"] = b"PRIVATE_FIXTURE_TAMPERED"
        self.rewrite(members)
        with self.assertRaisesRegex(ValueError, "members do not match|hash or size"):
            self.verify()

    def test_same_size_tamper_fails_hash_check(self):
        self.create()
        members = self.members()
        members["workspace/plan.md"] = b"X" * len(members["workspace/plan.md"])
        self.rewrite(members)
        with self.assertRaisesRegex(ValueError, "hash or size"):
            self.verify()

    def test_corrupt_database_resealed_hash_still_fails_sqlite(self):
        self.create()
        members = self.members()
        members["ariadne.sqlite3"] = b"NOT_A_SQLITE_DATABASE"
        manifest = json.loads(members[backup.MANIFEST_NAME])
        row = next(row for row in manifest["files"] if row["path"] == "ariadne.sqlite3")
        row.update(bytes=len(members["ariadne.sqlite3"]), sha256=hashlib.sha256(members["ariadne.sqlite3"]).hexdigest())
        members[backup.MANIFEST_NAME] = json.dumps(manifest).encode()
        self.rewrite(members)
        with self.assertRaisesRegex(ValueError, "SQLite database is corrupt"):
            self.verify()
        self.assertEqual(list(self.root.glob("backup-verify-*")), [])

    def test_manifest_record_mismatch_missing_and_extra_members_refused(self):
        self.create()
        saved = self.members()
        for mutate in (lambda m: m.pop("workspace/plan.md"), lambda m: m.update({"workspace/extra": b"x"})):
            with self.subTest(mutate=mutate):
                members = dict(saved)
                mutate(members)
                self.rewrite(members)
                with self.assertRaisesRegex(ValueError, "members do not match"):
                    self.verify()

    def test_unsupported_names_never_extracted(self):
        self.create()
        saved = self.members()
        names = ["../escape", "/absolute", "E:/forbidden", "workspace/a:b", "workspace/../a", "workspace\\a", "workspace/NUL.txt",
                 "workspace/COM1", "workspace/LPT9.txt", "workspace/a.", "workspace/a ", "workspace//a", "workspace/a\x00hidden", "other-owner.json"]
        for name in names:
            with self.subTest(name=name):
                self.rewrite([*saved.items(), (name, b"x")])
                with self.assertRaises(ValueError):
                    self.verify()
        self.assertFalse((self.root / "escape").exists())

    def test_duplicates_case_aliases_and_file_directory_collision_refused(self):
        self.create()
        saved = self.members()
        for extra in ("workspace/plan.md", "workspace/PLAN.md", "workspace/plan.md/child"):
            with self.subTest(extra=extra):
                self.rewrite([*saved.items(), (extra, b"x")])
                with self.assertRaisesRegex(ValueError, "duplicate|colliding"):
                    self.verify()

    def test_symlink_device_fifo_directory_and_unknown_metadata_refused(self):
        self.create()
        saved = self.members()
        mutations = [lambda i: setattr(i, "external_attr", (stat.S_IFLNK | 0o777) << 16),
                     lambda i: setattr(i, "external_attr", (stat.S_IFCHR | 0o600) << 16),
                     lambda i: setattr(i, "external_attr", (stat.S_IFIFO | 0o600) << 16),
                     lambda i: setattr(i, "external_attr", 0x400),
                     lambda i: setattr(i, "extra", struct.pack("<HH", 0xcafe, 0)),
                     lambda i: setattr(i, "comment", b"unsupported")]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.rewrite(saved, lambda info, index: mutate(info) if index == 0 else None)
                with self.assertRaises(ValueError):
                    self.verify()

    def test_duplicate_nonfinite_and_oversized_manifest_json_refused(self):
        self.create()
        saved = self.members()
        for payload in (b'{"schema":1,"schema":1}', b'{"schema":NaN}', b"[" * 1100, b" " * (backup.MAX_MANIFEST_BYTES + 1)):
            with self.subTest(payload_bytes=len(payload)):
                self.rewrite({**saved, backup.MANIFEST_NAME: payload})
                with self.assertRaises(ValueError):
                    self.verify()

    def test_boolean_manifest_integer_wrong_kinds_and_legacy_schema_refused(self):
        self.create()
        saved = self.members()
        for mutate in (lambda m: m.update(schema=True), lambda m: m.update(schema=2),
                       lambda m: m["files"][0].update(bytes=True), lambda m: m["files"][0].update(kind="workspace"),
                       lambda m: m.update(created_utc="2026-10-29T06:00:00"), lambda m: m.update(warnings=[]),
                       lambda m: m.update(application_version="1" * 10000 + ".0.0")):
            with self.subTest(mutate=mutate):
                self.rewrite(saved)
                self.mutate_manifest(mutate)
                with self.assertRaises(ValueError):
                    self.verify()

    def test_legacy_archive_is_never_claimed_verified(self):
        self.rewrite({"ariadne.sqlite3": b"legacy", "workspace/plan.md": b"legacy"})
        with self.assertRaisesRegex(ValueError, "Legacy backup.*unverified"):
            self.verify()

    def test_truncation_zip_crc_corruption_and_trailing_data_refused(self):
        self.create()
        saved = self.archive.read_bytes()
        for raw in (saved[:-4], saved + b"trailing", b"EXECUTABLE_PREFIX" + saved):
            with self.subTest(bytes=len(raw)):
                self.archive.write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.verify()
        self.archive.write_bytes(saved)
        with zipfile.ZipFile(self.archive) as archive:
            info = archive.getinfo("workspace/plan.md")
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        broken = bytearray(saved)
        broken[offset] ^= 0x20
        self.archive.write_bytes(broken)
        with self.assertRaises(ValueError):
            self.verify()

    def test_central_directory_count_and_size_rejected_before_zipfile(self):
        self.create()
        saved = self.archive.read_bytes()
        for field, value in ((8, backup.MAX_FILES + 2), (12, backup.MAX_CENTRAL_BYTES + 1)):
            altered = bytearray(saved)
            struct.pack_into("<H" if field == 8 else "<L", altered, len(altered)-22+field, value)
            if field == 8:
                struct.pack_into("<H", altered, len(altered)-22+10, value)
            self.archive.write_bytes(altered)
            with patch.object(backup.zipfile, "ZipFile", side_effect=AssertionError("Unsafe ZIP directory parsed")):
                with self.assertRaises(ValueError):
                    self.verify()

    def test_lying_small_central_count_rejected_before_zipfile(self):
        self.create()
        altered = bytearray(self.archive.read_bytes())
        struct.pack_into("<HH", altered, len(altered)-22+8, 1, 1)
        self.archive.write_bytes(altered)
        with patch.object(backup.zipfile, "ZipFile", side_effect=AssertionError("Unsafe ZIP directory parsed")):
            with self.assertRaisesRegex(ValueError, "count does not match"):
                self.verify()

    def test_invalid_deflate_stream_is_normalized_without_traceback(self):
        self.create()
        raw = bytearray(self.archive.read_bytes())
        with zipfile.ZipFile(self.archive) as archive:
            info = archive.getinfo("workspace/plan.md")
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        # Invalid BTYPE=3, whether or not this is a final deflate block.
        raw[offset] = (raw[offset] & ~6) | 6
        self.archive.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "ZIP is incomplete, corrupt or unsupported"):
            self.verify()

    def test_encrypted_and_unsupported_compression_refused(self):
        self.create()
        saved = self.archive.read_bytes()
        central = saved.index(b"PK\x01\x02")
        for offset, value in ((central + 8, 1), (central + 10, zipfile.ZIP_BZIP2)):
            with self.subTest(offset=offset):
                raw = bytearray(saved)
                struct.pack_into("<H", raw, offset, value)
                self.archive.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "encrypted, linked, special or unsupported"):
                    self.verify()

    def test_expanded_total_limit_includes_only_exact_supported_payload(self):
        receipt = self.create()
        with patch.object(backup, "MAX_TOTAL_BYTES", receipt["payload_bytes"] - 1):
            with self.assertRaisesRegex(ValueError, "expanded payload exceeds"):
                self.verify()

    def test_directory_count_limit_is_enforced_before_recursive_traversal(self):
        for index in range(4):
            (self.workspace / ("dir" + str(index))).mkdir()
        with patch.object(backup, "MAX_FILES", 3):
            with self.assertRaisesRegex(ValueError, "entry-count limit"):
                self.create()
        self.assertFalse(self.archive.exists())

    def test_saved_empty_database_is_not_called_verified(self):
        self.create()
        members = self.members()
        members["ariadne.sqlite3"] = b""
        manifest = json.loads(members[backup.MANIFEST_NAME])
        row = next(row for row in manifest["files"] if row["path"] == "ariadne.sqlite3")
        row.update(bytes=0, sha256=hashlib.sha256(b"").hexdigest())
        members[backup.MANIFEST_NAME] = json.dumps(manifest).encode()
        self.rewrite(members)
        with self.assertRaisesRegex(ValueError, "no SQLite header"):
            self.verify()

    def test_concurrent_destination_creation_cannot_replace_owner_bytes(self):
        commit = backup.commit_staged
        def collision(stage, destination):
            destination.write_bytes(b"NEW_OWNER_BACKUP")
            commit(stage, destination)
        with patch.object(backup, "commit_staged", side_effect=collision):
            with self.assertRaises(FileExistsError):
                self.create()
        self.assertEqual(self.archive.read_bytes(), b"NEW_OWNER_BACKUP")
        self.assertEqual(list(self.root.glob("*.partial")), [])

    def test_quota_failure_publishes_no_final_or_stage(self):
        with patch.object(backup, "MAX_FILE_BYTES", 100):
            with self.assertRaises(ValueError):
                self.create()
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob("*.partial")), [])
        self.assertEqual(list(self.data.glob("backup-*")), [])

    def test_readback_failure_and_atomic_publication_failure_preserve_no_success(self):
        for name in ("verify_backup", "commit_staged"):
            with self.subTest(phase=name), patch.object(backup, name, side_effect=ValueError("injected failure")):
                with self.assertRaisesRegex(ValueError, "injected failure"):
                    self.create()
                self.assertFalse(self.archive.exists())
                self.assertEqual(list(self.root.glob("*.partial")), [])

    def test_existing_destination_and_workspace_destination_refused(self):
        self.archive.write_bytes(b"OWNER_BACKUP")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.create()
        self.assertEqual(self.archive.read_bytes(), b"OWNER_BACKUP")
        with self.assertRaisesRegex(ValueError, "outside the workspace"):
            backup.create_backup(self.workspace / "self.zip", self.settings)

    def test_archive_hardlink_and_configuration_hardlink_refused(self):
        source = self.root / "canary"
        source.write_bytes(b"UNCHANGED_CANARY")
        alias = self.root / "alias"
        try:
            os.link(source, alias)
        except OSError:
            self.skipTest("Hard links unavailable")
        try:
            with self.assertRaisesRegex(ValueError, "regular files without links"):
                backup.verify_backup(alias, self.root)
        finally:
            alias.unlink()
        token = self.data / "server.token"
        token.unlink()
        os.link(source, token)
        with self.assertRaisesRegex(ValueError, "regular files without links"):
            self.create()
        self.assertFalse(self.archive.exists())
        self.assertEqual(source.read_bytes(), b"UNCHANGED_CANARY")

    def test_workspace_hardlink_omission_is_counted_and_not_followed(self):
        source = self.root / "canary"
        source.write_bytes(b"UNCHANGED_CANARY")
        try:
            os.link(source, self.workspace / "linked")
        except OSError:
            self.skipTest("Hard links unavailable")
        receipt = self.create()
        self.assertEqual(receipt["omitted_workspace_entries"], 1)
        self.assertNotIn("workspace/linked", self.members())

    @unittest.skipUnless(os.name == "nt", "Native Windows no-delete share semantics")
    def test_present_sqlite_leaf_pin_blocks_rename_but_allows_committed_writes(self):
        source = self.data / "ariadne.sqlite3"
        renamed = self.data / "renamed.sqlite3"
        source.rename(renamed)
        renamed.rename(source)
        with backup._pin_database_files(source):
            with self.assertRaises(OSError):
                source.rename(renamed)
            with contextlib.closing(sqlite3.connect(source)) as database:
                database.execute("INSERT INTO records VALUES('write while backup input is pinned')")
                database.commit()
        with contextlib.closing(sqlite3.connect(source)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2)
        source.rename(renamed)
        renamed.rename(source)

    @unittest.skipUnless(os.name == "nt", "Native Windows no-delete share semantics")
    def test_present_wal_and_shm_are_pinned_during_snapshot(self):
        source = self.data / "ariadne.sqlite3"
        with contextlib.closing(sqlite3.connect(source)) as live:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("INSERT INTO records VALUES('committed WAL row')")
            live.commit()
            for suffix in ("-wal", "-shm"):
                self.assertTrue(Path(str(source) + suffix).is_file())
            with backup._pin_database_files(source):
                for suffix in ("-wal", "-shm"):
                    with self.assertRaises(OSError):
                        Path(str(source) + suffix).rename(self.data / ("moved" + suffix))
                self.create()
            self.assertEqual(live.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2)

    def test_present_rollback_journal_refused_before_pin_or_sqlite_open(self):
        journal = self.data / "ariadne.sqlite3-journal"
        journal.write_bytes(b"UNCHANGED_ROLLBACK_JOURNAL_FIXTURE")
        with patch.object(backup, "_pin_sqlite_file", side_effect=AssertionError("Pinned an active rollback journal")), patch.object(backup.sqlite3, "connect", side_effect=AssertionError("Opened SQLite before journal preflight")):
            with self.assertRaisesRegex(ValueError, "rollback journal.*writes settle"):
                self.create()
        self.assertFalse(self.archive.exists())
        self.assertEqual(journal.read_bytes(), b"UNCHANGED_ROLLBACK_JOURNAL_FIXTURE")
        self.assertEqual(list(self.data.glob("backup-*")), [])

    def test_live_delete_transaction_can_commit_after_backup_is_refused(self):
        source = self.data / "ariadne.sqlite3"
        with contextlib.closing(sqlite3.connect(source)) as live:
            self.assertEqual(live.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
            live.execute("BEGIN IMMEDIATE")
            live.execute("INSERT INTO records VALUES('uncommitted live row')")
            journal = self.data / "ariadne.sqlite3-journal"
            self.assertTrue(journal.is_file())
            with self.assertRaisesRegex(ValueError, "rollback journal.*writes settle"):
                self.create()
            self.assertFalse(self.archive.exists())
            live.commit()
            self.assertFalse(journal.exists())
            self.assertEqual(live.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2)
        self.assertTrue(self.create()["verified"])

    def test_expired_deadline_fails_without_private_output(self):
        self.create()
        with self.assertRaisesRegex(ValueError, "time limit"):
            backup.verify_backup(self.archive, self.root, deadline=1)

    def test_verify_cli_does_not_bootstrap_owner_settings(self):
        self.create()
        spec = importlib.util.spec_from_file_location("backup_cli_fixture", Path(__file__).resolve().parents[1] / "scripts/backup.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        output = io.StringIO()
        with patch.object(cli, "load_settings", side_effect=AssertionError("Verify bootstrapped settings")), patch("sys.argv", ["backup.py", "--verify", str(self.archive), "--json"]), contextlib.redirect_stdout(output):
            cli.main()
        self.assertTrue(json.loads(output.getvalue())["verified"])
        self.assertNotIn("PRIVATE_FIXTURE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
