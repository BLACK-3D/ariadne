"""Privacy tests use only synthetic committed Git fixtures inside Yikes staging."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import export_source as source

ROOT = Path(__file__).absolute().parents[1]
TEMP_ROOT = ROOT / "staging/publication_audit_2026_11_01/export-source-tests"


class ExportSourcePrivacyTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="fixture-", dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.write("ariadne/__init__.py", b'__version__ = "1.2.3"\n')
        self.write("pyproject.toml", b'[project]\nname="fixture"\nversion="1.2.3"\n')
        self.write("README.md", b"Committed public documentation\n")
        self.write("config/connectors.example.json", b'{"token_env":"EXAMPLE_TOKEN"}\n')
        self.write(".gitignore", b"/dist/\n")
        self.commit()

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.root), *arguments], env=source._environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout

    def write(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def commit(self):
        self.git("add", "--all")
        self.git("-c", "user.name=Public Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Synthetic public source")

    def destination(self, name="release.zip"):
        return self.root / "dist" / name

    def test_untracked_env_overrides_private_documents_and_credentials_never_enter_archive(self):
        for name in ("config/.env", "config/connectors.json", "config/credentials.json", "docs/private-notes.txt", "ariadne/server.token"):
            self.write(name, b"SYNTHETIC_OWNER_ONLY_VALUE\n")
        destination = self.destination()
        source.export(destination, root=self.root)
        with zipfile.ZipFile(destination) as archive:
            for name in archive.namelist():
                self.assertNotIn(b"SYNTHETIC_OWNER_ONLY_VALUE", archive.read(name))
            self.assertIn("config/connectors.example.json", archive.namelist())
            self.assertNotIn("docs/private-notes.txt", archive.namelist())

    def test_modified_tracked_worktree_bytes_cannot_leak(self):
        self.write("README.md", b"SYNTHETIC_PRIVATE_WORKTREE_NOTE\n")
        source.export(self.destination(), root=self.root)
        with zipfile.ZipFile(self.destination()) as archive:
            self.assertEqual(archive.read("README.md"), b"Committed public documentation\n")

    def test_perception_guide_and_fictional_resources_are_bound_to_committed_source(self):
        public = {
            "ariadne/perception.py": b"# public replay implementation\n",
            "ariadne/static/perception.js": b"// public recording interface\n",
            "ariadne/resources/perception/demo.json": b'{"fictional":true,"observations":[]}\n',
            "docs/PERCEPTION_RECORDINGS.md": b"Public recording guide.\n",
        }
        for name, raw in public.items():
            self.write(name, raw)
        self.commit()
        for name in ("data/perception/recordings/owner.json", "data/perception/reports/replay.json"):
            self.write(name, b"PRIVATE_RECORDING_SENTINEL")
        source.export(self.destination(), root=self.root)
        with zipfile.ZipFile(self.destination()) as archive:
            pins = json.loads(archive.read("SOURCE-MANIFEST.json"))["sha256"]
            for name, raw in public.items():
                self.assertEqual(archive.read(name), raw)
                self.assertEqual(pins[name], hashlib.sha256(raw).hexdigest())
            self.assertFalse(any(name.startswith("data/") for name in archive.namelist()))
            self.assertTrue(all(b"PRIVATE_RECORDING_SENTINEL" not in archive.read(name) for name in archive.namelist()))

    def test_staged_but_uncommitted_files_are_excluded(self):
        self.write("docs/staged-private.txt", b"SYNTHETIC_STAGED_PRIVATE_NOTE")
        self.git("add", "--", "docs/staged-private.txt")
        source.export(self.destination(), root=self.root)
        with zipfile.ZipFile(self.destination()) as archive:
            self.assertNotIn("docs/staged-private.txt", archive.namelist())

    def test_tracked_secret_names_block_export(self):
        for name in (".env", "config/.env.local", "config/connectors.json", "ariadne/server.token", "docs/credentials.json"):
            with self.subTest(name=name):
                self.write(name, b"SYNTHETIC_TRACKED_PRIVATE_VALUE")
                self.commit()
                with self.assertRaises(ValueError):
                    source.export(self.destination(), root=self.root)
                self.assertFalse(self.destination().exists())
                self.git("rm", "-q", "--", name)
                self.commit()

    def test_tracked_workflow_is_outside_accepted_source_refresh_contract(self):
        self.write(".github/workflows/public.yml", b"name: synthetic-public-workflow\n")
        self.commit()
        with self.assertRaises(ValueError):
            source.export(self.destination(), root=self.root)
        self.assertFalse(self.destination().exists())

    def test_example_environment_templates_remain_allowed(self):
        self.write("config/.env.example", b"EXAMPLE_TOKEN=replace-me\n")
        self.commit()
        source.export(self.destination(), root=self.root)
        with zipfile.ZipFile(self.destination()) as archive:
            self.assertIn("config/.env.example", archive.namelist())

    def test_tracked_owner_directories_and_archive_names_block_export(self):
        for name in ("data/notes.txt", "staging/log.txt", "runtime/owner.txt", "docs/private.zip", "config/customer.sqlite3"):
            with self.subTest(name=name):
                self.write(name, b"SYNTHETIC_PRIVATE_STATE")
                self.commit()
                with self.assertRaises(ValueError):
                    source.export(self.destination(), root=self.root)
                self.assertFalse(self.destination().exists())
                self.git("rm", "-q", "--", name)
                self.commit()

    def test_disguised_database_archive_executable_and_lfs_payloads_block_export(self):
        for body in (b"SQLite format 3\0FAKE", b"PK\x03\x04FAKE", b"MZFAKE", b"version https://git-lfs.github.com/spec/v1\nFAKE"):
            with self.subTest(body_type=body[:2]):
                self.write("docs/fixture.txt", body)
                self.commit()
                with self.assertRaises(ValueError):
                    source.export(self.destination(), root=self.root)
                self.assertFalse(self.destination().exists())

    def test_committed_git_symlink_is_rejected_without_following_it(self):
        oid = subprocess.run(["git", "-C", str(self.root), "hash-object", "-w", "--stdin"],
            input=b"README.md", stdout=subprocess.PIPE, check=True, env=source._environment()).stdout.decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "120000," + oid + ",docs/linked")
        self.git("-c", "user.name=Public Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Synthetic link")
        with self.assertRaises(ValueError):
            source.export(self.destination(), root=self.root)
        self.assertFalse(self.destination().exists())

    def test_missing_git_inventory_fails_closed(self):
        plain = self.root / "plain"
        plain.mkdir()
        (plain / "README.md").write_bytes(b"No trusted inventory")
        with self.assertRaises(ValueError):
            source.export(plain / "out.zip", root=plain)
        self.assertFalse((plain / "out.zip").exists())

    def test_relative_destination_is_resolved_inside_selected_checkout(self):
        result = source.export(Path("dist/relative.zip"), root=self.root)
        self.assertEqual(Path(result["path"]), self.root / "dist/relative.zip")
        self.assertTrue((self.root / "dist/relative.zip").exists())

    def test_external_destination_is_rejected_without_writing(self):
        outside = self.root.parent / (self.root.name + "-outside.zip")
        with self.assertRaises(ValueError):
            source.export(outside, root=self.root)
        self.assertFalse(outside.exists())

    def test_existing_artifact_is_never_overwritten(self):
        self.write("dist/release.zip", b"VERIFIED_OLD_ARTIFACT")
        with self.assertRaises(FileExistsError):
            source.export(self.destination(), root=self.root)
        self.assertEqual(self.destination().read_bytes(), b"VERIFIED_OLD_ARTIFACT")

    def test_size_limits_fail_before_publication(self):
        with patch.object(source, "MAX_FILE_BYTES", 1):
            with self.assertRaises(ValueError):
                source.export(self.destination(), root=self.root)
        self.assertFalse(self.destination().exists())

    def test_manifest_hashes_every_committed_member_and_preserves_version_schema(self):
        result = source.export(self.destination(), root=self.root)
        self.assertEqual(result["commit"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertEqual(result["version"], "1.2.3")
        with zipfile.ZipFile(self.destination()) as archive:
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read("SOURCE-MANIFEST.json"))
            self.assertEqual(set(manifest), {"version", "sha256"})
            self.assertEqual(set(archive.namelist()), set(manifest["sha256"]) | {"SOURCE-MANIFEST.json"})
            for name, digest in manifest["sha256"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)

    def test_changed_head_before_publication_fails_closed(self):
        real = source._git
        calls = 0
        def changing(root, *args):
            nonlocal calls
            value = real(root, *args)
            if args == ("rev-parse", "--verify", "HEAD^{commit}"):
                calls += 1
                if calls == 2:
                    return b"0" * 40 + b"\n"
            return value
        with patch.object(source, "_git", side_effect=changing):
            with self.assertRaises(ValueError):
                source.export(self.destination(), root=self.root)
        self.assertFalse(self.destination().exists())
        self.assertEqual(list(self.destination().parent.glob("*.partial")), [])

    def test_consumer_privacy_and_native_boundaries_are_reused(self):
        rejected = ("config/owner.env", "docs/run.log", "ariadne/module.pyc", "ariadne/module.pyo",
            "config/.cache/token.txt", "config/browser-profile/cookies.txt", "config/browser-profiles/session.json",
            "config/user-data/preferences.json", "config/.npmrc.private", "config/connectors.json",
            "docs/library.so", "docs/library.dylib", "docs/runtime.whl", "docs/model.bin",
            "config/secret.p12", "config/secret.pfx", "docs/state.pickle")
        for name in rejected:
            with self.subTest(path_kind=name.rsplit("/", 1)[-1]):
                with self.assertRaises(ValueError):
                    source._member(name)

    def test_admitted_paths_are_accepted_by_unchanged_refresh_consumer(self):
        admitted = ("ariadne/module.py", "scripts/export_source.py", "tests/test_export_source.py",
            "docs/public.md", "config/connectors.example.json", "config/.env.example", "README.md",
            "requirements-server.txt", "Setup Ariadne.cmd", "START_HERE.html",
            "docs/GET_STARTED.md", "scripts/setup_windows.py", "scripts/setup_windows.ps1")
        for name in admitted:
            with self.subTest(path=name):
                self.assertEqual(source._member(name), name)
                self.assertTrue(source.source_policy._source_member(name))
                self.assertFalse(source.source_policy._private(name))

    def test_case_aliases_and_nonportable_paths_are_rejected(self):
        for name in ("docs/../private.txt", "docs/CON.txt", "docs/trailing. ", "C:/private.txt", "docs\\private.txt"):
            with self.subTest(path_kind=name.split('/')[0]):
                with self.assertRaises(ValueError):
                    source._member(name)
        raw = b"100644 blob " + b"1" * 40 + b"\tdocs/Case.txt\0" + b"100644 blob " + b"2" * 40 + b"\tdocs/case.txt\0"
        with patch.object(source, "_git", return_value=raw):
            with self.assertRaises(ValueError):
                source._tree(self.root, "1" * 40)


if __name__ == "__main__":
    unittest.main()
