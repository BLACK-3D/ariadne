"""Synthetic offline-core provenance, privacy and fail-stop publication tests.

All archives and model blobs are tiny invented fixtures under private staging.
No installed owner runtime, model, service, network or actual account is used.
"""
from __future__ import annotations
from contextlib import ExitStack
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import core_runtime as core
from scripts import export_core as exporter
from scripts import provision
from scripts import setup_windows as setup

ROOT = Path(__file__).absolute().parents[1]
FIXTURES = ROOT / "staging/core_copy_2026_11_02/fixtures"
MODEL_NAME = "data/models/manifests/registry.ollama.ai/library/qwen3.5/9b"
OWNER = b"SYNTHETIC_OWNER_SECRET_MUST_NEVER_BE_EXPORTED"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


class CoreFixture(unittest.TestCase):
    def setUp(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="core-", dir=FIXTURES)
        self.area = Path(self.temp.name)
        self.root = self.area / "Synthetic core box"
        self.root.mkdir()
        self.addCleanup(self.cleanup)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.upstream = {"python.exe": b"PUBLIC_PYTHON", "Lib/os.py": b"PUBLIC_STDLIB",
                         "Scripts/unused-launcher.exe": OWNER,
                         "Lib/site-packages/preinstalled.py": b"UPSTREAM_PREINSTALLED"}
        python_archive = self.zip("runtime/downloads/python.3.13.17.nupkg", {"tools/"+name:raw for name,raw in self.upstream.items()})
        self.python_pin = digest(python_archive.read_bytes())
        self.stack.enter_context(patch.object(core, "PYTHON_SHA256", self.python_pin))
        for name,raw in self.upstream.items(): self.put("runtime/python/"+name, raw)
        self.bootstrap = {"schema": 1, "version": core.PYTHON_VERSION, "archive_sha256": self.python_pin,
                          "files": {name:digest(raw) for name,raw in self.upstream.items()}}
        self.put("runtime/python/BOOTSTRAP-MANIFEST.json", encoded(self.bootstrap))
        self.wheel_members = {}
        for requirement,package in zip(core.REQUIREMENTS, ("pip", "waitress", "pypdf")):
            members = {package+"/__init__.py": ("PUBLIC_"+package).encode(), package+"-1.0.dist-info/METADATA": b"PUBLIC_METADATA",
                       package+"-1.0.dist-info/RECORD": OWNER}
            wheel = self.zip("runtime/wheels/"+package+"-1.0-py3-none-any.whl", members)
            self.put(requirement, (package+"==1.0 --hash=sha256:"+digest(wheel.read_bytes())+"\n").encode())
            for name,raw in members.items():
                self.put("runtime/python/Lib/site-packages/"+name, raw)
                if not name.endswith(".dist-info/RECORD"): self.wheel_members["Lib/site-packages/"+name] = digest(raw)
        runtime_members = {"ollama.exe": b"PUBLIC_ENGINE", "LICENSE": b"PUBLIC_ENGINE_LICENSE"}
        package = self.zip("runtime/downloads/ollama-"+provision.VERSION+".zip", runtime_members)
        self.stack.enter_context(patch.object(provision, "SHA256", digest(package.read_bytes())))
        for name,raw in runtime_members.items(): self.put("runtime/ollama/"+name, raw)
        self.config_blob = b"PUBLIC_MODEL_CONFIGURATION"
        self.weights_blob = b"PUBLIC_MODEL_WEIGHTS"
        self.model = {"schemaVersion": 2, "config": {"digest": "sha256:"+digest(self.config_blob), "size": len(self.config_blob)},
                      "layers": [{"digest": "sha256:"+digest(self.weights_blob), "size": len(self.weights_blob)}]}
        self.put(MODEL_NAME, encoded(self.model))
        for raw in (self.config_blob,self.weights_blob): self.put("data/models/blobs/sha256-"+digest(raw), raw)
        self.catalog = {"schema": 1, "default_model": "qwen3.5:9b", "models": [{"tag": "qwen3.5:9b",
            "source_url": "https://ollama.com/library/qwen3.5:9b", "manifest": {"path": MODEL_NAME, "bytes": len(encoded(self.model)), "sha256": digest(encoded(self.model))},
            "metadata": [], "blobs": [{"sha256": digest(raw), "bytes": len(raw)} for raw in (self.config_blob,self.weights_blob)]}]}
        self.put("config/public-models.json", encoded(self.catalog))
        self.stack.enter_context(patch.object(exporter, "PUBLIC_CATALOG_SHA256", digest(encoded(self.catalog))))
        self.stack.enter_context(patch.dict(provision.MODEL_PINS, {"qwen3.5:9b": digest(encoded(self.model))}))
        self.put("README.md", b"Public synthetic source.\n")
        self.original_profile = encoded({"schema":1,"default_model":"qwen3.5:9b","factory_local_flows":True,
                                         "profiles":{"media-config.json":{"fixture":OWNER.decode()}}})
        self.put("config/box-profiles.json", self.original_profile)
        self.source_names = ("README.md", "config/box-profiles.json", "config/public-models.json", *core.REQUIREMENTS)
        self.manifest = {"version":"0.33.0","sha256":{name:digest((self.root/name).read_bytes()) for name in self.source_names}}
        self.put("SOURCE-MANIFEST.json", encoded(self.manifest))

    def cleanup(self):
        if self.area.exists() and not self.area.resolve().is_relative_to(FIXTURES.resolve()):
            raise AssertionError("Fixture cleanup escaped private staging")
        self.temp.cleanup()

    def put(self,name,raw):
        path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw); return path

    def zip(self,name,members):
        path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_STORED) as archive:
            for member,raw in members.items(): archive.writestr(member,raw)
        return path

    def seal(self):
        runtime=self.root/"runtime/python"
        with patch.object(core.sys,"prefix",str(runtime)),patch.object(core.sys,"base_prefix",str(runtime)),patch.object(core.sys,"executable",str(runtime/"python.exe")):
            return core.seal(self.root)

    def target(self,name="copy"):
        return self.root/"dist"/(name+".zip")

    def approved_changed_model(self,model,catalog=None):
        self.put(MODEL_NAME,encoded(model)); value=copy.deepcopy(catalog or self.catalog)
        value["models"][0]["manifest"].update(sha256=digest(encoded(model)),bytes=len(encoded(model)))
        self.put("config/public-models.json",encoded(value)); return digest(encoded(value))


class SourcePrivacyTests(CoreFixture):
    def test_source_tampering_is_refused_before_an_archive_is_created(self):
        self.put("README.md",b"CHANGED")
        with self.assertRaises(ValueError): exporter.export(self.target(),root=self.root)
        self.assertFalse(self.target().exists()); self.assertFalse(self.target().with_suffix(".zip.partial").exists())

    def test_private_and_escaping_source_names_are_rejected_even_with_valid_hashes(self):
        for name in ("data/server.token","config/owner.env","docs/run.log","docs/private.pem","../outside.md"):
            value=copy.deepcopy(self.manifest);value["sha256"][name]=digest(OWNER)
            self.put("SOURCE-MANIFEST.json",encoded(value))
            with self.subTest(name=name),self.assertRaises(ValueError): exporter.source_files(self.root)

    def test_every_core_recipe_must_be_source_bound_before_runtime_collection(self):
        for recipe in core.REQUIREMENTS:
            value=copy.deepcopy(self.manifest);value["sha256"].pop(recipe)
            self.put("SOURCE-MANIFEST.json",encoded(value))
            with self.subTest(recipe=recipe),patch.object(core,"sealed_files",side_effect=AssertionError("Never collect runtime from an unbound recipe")),self.assertRaises(ValueError):
                exporter.inventory(self.root)

    def test_factory_profiles_are_empty_and_exported_source_hash_is_updated_without_owner_write(self):
        before=(self.root/"SOURCE-MANIFEST.json").read_bytes()
        files=exporter.source_files(self.root); raw,expected=files["config/box-profiles.json"]
        self.assertIs(type(raw),bytes);self.assertEqual(digest(raw),expected)
        factory=json.loads(raw);self.assertEqual(factory["profiles"],{});self.assertFalse(factory["factory_local_flows"])
        self.assertNotIn(OWNER,raw)
        updated=json.loads(files["SOURCE-MANIFEST.json"][0]);self.assertEqual(updated["sha256"]["config/box-profiles.json"],expected)
        self.assertEqual((self.root/"SOURCE-MANIFEST.json").read_bytes(),before)
        self.assertEqual((self.root/"config/box-profiles.json").read_bytes(),self.original_profile)

    def test_only_exact_known_factory_transformation_is_accepted(self):
        self.put("config/box-profiles.json",core.factory_profile())
        self.assertEqual(exporter.source_files(self.root)["config/box-profiles.json"][0],core.factory_profile())
        bad=json.loads(core.factory_profile());bad["profiles"]={"owner":"value"}
        self.put("config/box-profiles.json",encoded(bad))
        with self.assertRaises(ValueError):exporter.source_files(self.root)

    def test_catalog_must_be_source_bound_and_match_independent_reviewed_pin(self):
        value=copy.deepcopy(self.manifest);value["sha256"].pop("config/public-models.json")
        self.put("SOURCE-MANIFEST.json",encoded(value))
        with self.assertRaises(ValueError):exporter.source_files(self.root)
        altered=copy.deepcopy(self.catalog);altered["models"][0]["source_url"]="https://example.invalid/owner"
        self.put("config/public-models.json",encoded(altered))
        with self.assertRaises(ValueError):exporter.model_files(self.root)


class FactoryConversionTests(CoreFixture):
    def test_fresh_factory_conversion_is_exact_idempotent_and_source_manifest_remains_bound(self):
        source_before=(self.root/"SOURCE-MANIFEST.json").read_bytes()
        setup.prepare_factory(root=self.root)
        profile=self.root/"config/box-profiles.json"
        self.assertEqual(profile.read_bytes(),core.factory_profile())
        self.assertEqual((self.root/"SOURCE-MANIFEST.json").read_bytes(),source_before)
        self.assertEqual(exporter.source_files(self.root)["config/box-profiles.json"][0],core.factory_profile())
        with patch.object(setup.os,"replace",side_effect=AssertionError("No second replacement")):
            setup.prepare_factory(root=self.root)
        self.assertFalse((self.root/"data/server.token").exists())

    def test_every_owner_marker_refuses_factory_conversion_without_changes(self):
        for name in setup.OWNER_MARKERS:
            marker=self.put(name,OWNER);before=(self.root/"config/box-profiles.json").read_bytes()
            with self.subTest(marker=name),self.assertRaises(ValueError):setup.prepare_factory(root=self.root)
            self.assertEqual((self.root/"config/box-profiles.json").read_bytes(),before)
            self.assertEqual(marker.read_bytes(),OWNER)
            marker.unlink()

    def test_owner_appearing_during_staging_preserves_original_profile(self):
        original_fsync=os.fsync
        before=set(p.name for p in (self.root/"config").iterdir())
        def owner_appears(descriptor):
            original_fsync(descriptor)
            self.put("data/server.token",OWNER)
        with patch.object(setup.os,"fsync",side_effect=owner_appears),self.assertRaises(ValueError):
            setup.prepare_factory(root=self.root)
        self.assertEqual((self.root/"config/box-profiles.json").read_bytes(),self.original_profile)
        self.assertEqual((self.root/"data/server.token").read_bytes(),OWNER)
        self.assertEqual(set(p.name for p in (self.root/"config").iterdir()),before)

    def test_concurrent_profile_edit_is_preserved_instead_of_replaced(self):
        original_fsync=os.fsync;profile=self.root/"config/box-profiles.json";owner_edit=b"SYNTHETIC_NEW_OWNER_PROFILE"
        def edit_appears(descriptor):
            original_fsync(descriptor)
            profile.write_bytes(owner_edit)
        with patch.object(setup.os,"fsync",side_effect=edit_appears),self.assertRaises(ValueError):
            setup.prepare_factory(root=self.root)
        self.assertEqual(profile.read_bytes(),owner_edit)


class RuntimeProvenanceTests(CoreFixture):
    def test_trusted_runtime_comes_only_from_upstream_and_pinned_wheels(self):
        self.put("runtime/python/Lib/site-packages/private.env",OWNER)
        self.put("runtime/python/Lib/site-packages/pip-1.0.dist-info/direct_url.json",OWNER)
        files,requirements=core.trusted_members(self.root)
        self.assertIn("python.exe",files);self.assertIn("Lib/os.py",files)
        self.assertTrue(set(self.wheel_members)<=set(files))
        self.assertFalse(any(name.startswith("Scripts/") or name.endswith(("private.env","direct_url.json","/RECORD")) for name in files))
        self.assertEqual(set(requirements),set(core.REQUIREMENTS))
        self.seal();sealed=core.sealed_files(self.root)
        self.assertNotIn("runtime/python/Lib/site-packages/private.env",sealed)

    def test_upstream_archive_change_cannot_be_authorized_by_editing_bootstrap_inventory(self):
        changed={"tools/"+name:raw for name,raw in self.upstream.items()};changed["tools/python.exe"]=OWNER
        self.zip("runtime/downloads/python.3.13.17.nupkg",changed)
        edited=copy.deepcopy(self.bootstrap);edited["files"]["python.exe"]=digest(OWNER)
        self.put("runtime/python/BOOTSTRAP-MANIFEST.json",encoded(edited));self.put("runtime/python/python.exe",OWNER)
        with self.assertRaises(ValueError):core.trusted_members(self.root)

    def test_arbitrary_runtime_file_cannot_be_added_to_bootstrap_or_portable_seal(self):
        self.seal();path=self.root/"runtime/python/PORTABLE-MANIFEST.json";value=json.loads(path.read_bytes())
        value["sha256"]["Lib/private.env"]=digest(OWNER);self.put("runtime/python/Lib/private.env",OWNER);path.write_bytes(encoded(value))
        with self.assertRaises(ValueError):core.sealed_files(self.root)
        edited=copy.deepcopy(self.bootstrap);edited["files"]["Lib/private.env"]=digest(OWNER)
        self.put("runtime/python/BOOTSTRAP-MANIFEST.json",encoded(edited))
        with self.assertRaises(ValueError):core.trusted_members(self.root)

    def test_installed_upstream_or_wheel_changes_fail_the_seal(self):
        for name in ("python.exe","Lib/site-packages/waitress/__init__.py"):
            path=self.root/"runtime/python"/name;before=path.read_bytes();path.write_bytes(b"CHANGED")
            with self.subTest(name=name),self.assertRaises(ValueError):core.trusted_members(self.root)
            path.write_bytes(before)

    def test_seal_is_idempotent_but_existing_owner_state_is_never_resealed(self):
        first=self.seal();path=self.root/"runtime/python/PORTABLE-MANIFEST.json";before=path.read_bytes()
        self.assertEqual(self.seal(),first);self.assertEqual(path.read_bytes(),before)
        self.put("data/server.token",OWNER)
        with self.assertRaises(ValueError):self.seal()
        self.assertEqual(path.read_bytes(),before)

    def test_sealing_requires_the_private_full_interpreter(self):
        with self.assertRaises(ValueError):core.seal(self.root)
        self.assertFalse((self.root/"runtime/python/PORTABLE-MANIFEST.json").exists())


class ModelAndEngineTests(CoreFixture):
    def test_only_default_manifest_and_referenced_public_blobs_are_selected(self):
        private=self.put("data/models/blobs/sha256-"+digest(OWNER),OWNER)
        files=exporter.model_files(self.root)
        expected={MODEL_NAME,*["data/models/blobs/sha256-"+digest(raw) for raw in (self.config_blob,self.weights_blob)]}
        self.assertEqual(set(files),expected);self.assertNotIn(private.relative_to(self.root).as_posix(),files)

    def test_manifest_digest_must_match_reviewed_recipe(self):
        value=copy.deepcopy(self.model);value["unreviewed"]=True;self.put(MODEL_NAME,encoded(value))
        with self.assertRaises(ValueError):exporter.model_files(self.root)

    def test_bad_model_reference_is_rejected_before_blob_path_stats(self):
        for raw in ("../outside",digest(self.config_blob),"sha256:"+"g"*64):
            model=copy.deepcopy(self.model);model["config"]["digest"]=raw;pin=self.approved_changed_model(model)
            with self.subTest(raw=raw),patch.object(exporter,"PUBLIC_CATALOG_SHA256",pin),patch.object(exporter,"regular",side_effect=AssertionError("Never stat an unqualified blob")):
                with self.assertRaises(ValueError):exporter.model_files(self.root)

    def test_duplicate_or_invalid_catalog_blob_pins_are_rejected(self):
        for kind in ("duplicate","path","bool_size"):
            catalog=copy.deepcopy(self.catalog)
            if kind=="duplicate":catalog["models"][0]["blobs"].append(copy.deepcopy(catalog["models"][0]["blobs"][0]))
            elif kind=="path":catalog["models"][0]["blobs"][0]["sha256"]="../outside"
            else:catalog["models"][0]["blobs"][0]["bytes"]=True
            pin=self.approved_changed_model(self.model,catalog)
            with self.subTest(kind=kind),patch.object(exporter,"PUBLIC_CATALOG_SHA256",pin),self.assertRaises(ValueError):exporter.model_files(self.root)

    def test_ollama_archive_pin_and_member_paths_are_enforced(self):
        self.put("runtime/downloads/ollama-"+provision.VERSION+".zip",b"CHANGED")
        with self.assertRaises(ValueError):exporter.ollama_files(self.root)
        package=self.zip("runtime/downloads/ollama-"+provision.VERSION+".zip",{"../outside.txt":b"unsafe","ollama.exe":b"PUBLIC_ENGINE"})
        with patch.object(provision,"SHA256",digest(package.read_bytes())),self.assertRaises(ValueError):exporter.ollama_files(self.root)


class PublicationTests(CoreFixture):
    def test_complete_export_excludes_owner_data_and_binds_every_artifact(self):
        self.seal()
        private=("data/server.token","data/ariadne.sqlite3","data/connectors.json","data/workspace/private.txt","runtime/python/Lib/site-packages/private.env")
        for name in private:self.put(name,OWNER)
        self.put("data/models/blobs/sha256-"+digest(OWNER),OWNER)
        original_sha=core.sha;seen=[]
        def public_only(path):
            path=Path(path);self.assertNotIn(path,{self.root/name for name in private});seen.append(path);return original_sha(path)
        target=self.target()
        with patch.object(core,"sha",side_effect=public_only):receipt=exporter.export(target,root=self.root)
        self.assertEqual(receipt["status"],"completed");self.assertFalse(receipt["private_state_included"])
        self.assertFalse(receipt["setup_required"]);self.assertFalse(receipt["internet_required_for_local_use"])
        self.assertEqual(digest(target.read_bytes()),receipt["sha256"])
        self.assertEqual(json.loads(target.with_suffix(".zip.receipt.json").read_bytes()),receipt)
        self.assertTrue(target.with_suffix(".zip.sha256").read_text().startswith(receipt["sha256"]+"  "))
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip());manifest=json.loads(archive.read("CORE-MANIFEST.json"))
            self.assertFalse(manifest["private_state_included"])
            for row in manifest["files"]:
                payload=archive.read(row["path"]);self.assertEqual(len(payload),row["bytes"]);self.assertEqual(digest(payload),row["sha256"]);self.assertNotIn(OWNER,payload)
            self.assertFalse(set(private)&set(archive.namelist()))
            factory=archive.read("config/box-profiles.json");source=json.loads(archive.read("SOURCE-MANIFEST.json"))
            self.assertEqual(source["sha256"]["config/box-profiles.json"],digest(factory))
            self.assertEqual(json.loads(factory)["profiles"],{})
        self.assertTrue(all((self.root/name).read_bytes()==OWNER for name in private))

    def test_existing_outputs_sidecars_and_partial_block_before_inventory(self):
        for suffix in (".zip",".zip.sha256",".zip.receipt.json",".zip.partial"):
            target=self.target("existing-"+suffix.replace('.','-'));existing=target.with_suffix(suffix);existing.parent.mkdir(parents=True,exist_ok=True);existing.write_bytes(OWNER)
            with self.subTest(suffix=suffix),patch.object(exporter,"inventory",side_effect=AssertionError("Never replay uncertain export")),self.assertRaises(FileExistsError):exporter.export(target,root=self.root)
            self.assertEqual(existing.read_bytes(),OWNER)

    def test_destination_outside_box_is_refused_before_any_source_read(self):
        target=self.area/"outside.zip"
        with patch.object(exporter,"inventory",side_effect=AssertionError("No inventory")),self.assertRaises(ValueError):exporter.export(target,root=self.root)
        self.assertFalse(target.exists());self.assertFalse(target.with_suffix(".zip.partial").exists())

    def test_stream_hash_failure_leaves_only_partial_and_never_publishes_a_zip(self):
        self.seal();weights=self.root/("data/models/blobs/sha256-"+digest(self.weights_blob));weights.write_bytes(b"X"*len(self.weights_blob))
        target=self.target()
        with self.assertRaises(ValueError):exporter.export(target,root=self.root)
        self.assertFalse(target.exists());self.assertTrue(target.with_suffix(".zip.partial").exists())
        self.assertFalse(target.with_suffix(".zip.sha256").exists());self.assertFalse(target.with_suffix(".zip.receipt.json").exists())
        with patch.object(exporter,"inventory",side_effect=AssertionError("Never replay")),self.assertRaises(FileExistsError):exporter.export(target,root=self.root)

    def test_changed_same_size_engine_is_caught_during_streaming(self):
        self.seal();engine=self.root/"runtime/ollama/ollama.exe";engine.write_bytes(b"X"*engine.stat().st_size)
        with self.assertRaises(ValueError):exporter.export(self.target(),root=self.root)
        self.assertFalse(self.target().exists())

    def test_failure_before_atomic_zip_commit_preserves_partial(self):
        target=self.target();raw=b"PUBLIC"
        with patch.object(exporter,"inventory",return_value={"README.md":(raw,digest(raw))}),patch.object(exporter,"commit_staged",side_effect=OSError("Synthetic commit failure")),self.assertRaises(OSError):exporter.export(target,root=self.root)
        self.assertTrue(target.with_suffix(".zip.partial").exists());self.assertFalse(target.exists())

    def test_postcommit_sidecar_failure_is_uncertain_and_never_replayed(self):
        target=self.target();raw=b"PUBLIC"
        with patch.object(exporter,"inventory",return_value={"README.md":(raw,digest(raw))}),patch.object(exporter,"publish_bytes",side_effect=OSError("Synthetic sidecar failure")),self.assertRaises(OSError):exporter.export(target,root=self.root)
        self.assertTrue(target.exists());self.assertFalse(target.with_suffix(".zip.receipt.json").exists())
        with patch.object(exporter,"inventory",side_effect=AssertionError("Never adopt unreceipted ZIP")),self.assertRaises(FileExistsError):exporter.export(target,root=self.root)


if __name__ == "__main__":unittest.main()