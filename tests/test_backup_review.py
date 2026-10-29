"""Independent checks of inert backup verification and concurrent file changes."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ariadne import backup
from ariadne.config import Settings


class BackupReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".recovery-test-backup-review-", dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(data_dir=self.root / "data")
        self.settings.workspace.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(self.settings.data_dir / "ariadne.sqlite3")) as connection:
            connection.execute("CREATE TABLE records(value TEXT)")
            connection.execute("INSERT INTO records VALUES('PRIVATE_REVIEW_FIXTURE')")
            connection.execute("CREATE VIEW never_evaluate AS SELECT load_extension('PRIVATE_REVIEW_FIXTURE')")
            connection.commit()
        self.archive = self.root / "checked.zip"

    def test_workspace_code_and_sqlite_view_remain_inert(self):
        canary = self.root / "executed-canary"
        source = "from pathlib import Path\nPath(" + repr(str(canary)) + ").write_text('EXECUTED')\n"
        (self.settings.workspace / "untrusted.py").write_text(source, encoding="utf-8")
        with patch("subprocess.Popen", side_effect=AssertionError("Backup started a process")), \
             patch("socket.create_connection", side_effect=AssertionError("Backup made a network request")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("Backup fetched external data")):
            created = backup.create_backup(self.archive, self.settings)
            checked = backup.verify_backup(self.archive, self.root)
        self.assertTrue(checked["verified"])
        self.assertEqual(created, checked)
        self.assertFalse(canary.exists())
        self.assertNotIn("PRIVATE_REVIEW_FIXTURE", json.dumps(checked))
        self.assertEqual(set(self.settings.data_dir.iterdir()), {
            self.settings.workspace, self.settings.data_dir / "ariadne.sqlite3"
        })

    def test_archive_changed_after_final_hash_has_no_success_receipt(self):
        backup.create_backup(self.archive, self.settings)
        stream = backup._stream
        changed = []

        def mutate_after_hash(source, target, maximum, deadline):
            result = stream(source, target, maximum, deadline)
            if isinstance(source, io.BufferedReader) and target is None:
                with self.archive.open("ab") as output:
                    output.write(b"concurrent change")
                changed.append(True)
            return result

        with patch.object(backup, "_stream", side_effect=mutate_after_hash):
            with self.assertRaisesRegex(ValueError, "changed"):
                backup.verify_backup(self.archive, self.root)
        self.assertEqual(changed, [True])
        self.assertEqual(list(self.root.glob("backup-verify-*")), [])

    def test_workspace_mutation_during_copy_refuses_publication(self):
        document = self.settings.workspace / "changing.txt"
        document.write_bytes(b"first private value")
        stream = backup._stream
        changed = []

        def mutate_during_copy(source, target, maximum, deadline):
            result = stream(source, target, maximum, deadline)
            if target is not None and isinstance(source, io.BufferedReader) and not changed:
                # Database snapshot inputs have the SQLite header. Mutate only
                # the live workspace input after its bytes have been copied.
                position = source.tell()
                source.seek(0)
                is_document = source.read(19) == b"first private value"
                source.seek(position)
                if is_document:
                    with document.open("ab") as output:
                        output.write(b" changed after capture")
                    changed.append(True)
            return result

        with patch.object(backup, "_stream", side_effect=mutate_during_copy):
            with self.assertRaisesRegex(ValueError, "changed"):
                backup.create_backup(self.archive, self.settings)
        self.assertEqual(changed, [True])
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob(".*.partial")), [])
        self.assertEqual(list(self.settings.data_dir.glob("backup-*")), [])

    def test_manifest_version_cannot_expand_the_public_receipt(self):
        backup.create_backup(self.archive, self.settings)
        with zipfile.ZipFile(self.archive) as archive:
            members = {info.filename: archive.read(info) for info in archive.infolist()}
        manifest = json.loads(members[backup.MANIFEST_NAME])
        manifest["application_version"] = "9" * 4096 + ".1.0"
        members[backup.MANIFEST_NAME] = json.dumps(manifest).encode("utf-8")
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        with self.assertRaisesRegex(ValueError, "version"):
            backup.verify_backup(self.archive, self.root)

    @unittest.skipUnless(os.name == "nt", "Native Windows directory pin")
    def test_actual_verification_pins_its_temporary_child_directory(self):
        backup.create_backup(self.archive, self.settings)
        check = backup._quick_check
        observed = []

        def check_while_directory_is_pinned(snapshot, deadline):
            with self.assertRaises(OSError):
                snapshot.parent.rename(self.root / "moved-temporary")
            observed.append(True)
            return check(snapshot, deadline)

        with patch.object(backup, "_quick_check", side_effect=check_while_directory_is_pinned):
            self.assertTrue(backup.verify_backup(self.archive, self.root)["verified"])
        self.assertEqual(observed, [True])
        self.assertFalse((self.root / "moved-temporary").exists())


if __name__ == "__main__":
    unittest.main()
