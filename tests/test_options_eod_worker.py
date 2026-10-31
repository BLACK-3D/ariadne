"""Fixed child boundary; simulated subprocesses never access a data source."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.handoff import HandoffConflict
from ariadne.options_eod_worker import run_collector
from ariadne.tools import ToolRegistry


class Process(SimpleNamespace):
    __hash__ = object.__hash__


class EodWorkerTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "runtime/tmp"
        root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="eod-worker-", dir=root)
        self.addCleanup(temporary.cleanup)
        settings = Settings(data_dir=Path(temporary.name) / "data")
        self.registry = ToolRegistry(settings)
        self.policy = tuple({"revision": n} for n in range(1, 5))
        self.epoch = {"global_pause_rowid": 31}
        self.owner = SimpleNamespace(registry=self.registry, stopped=threading.Event(),
            database=lambda: nullcontext(object()), _epoch=lambda *args: self.epoch,
            _account=lambda db: {"id": "one-shared-account", "state": {"schema": 3}, "policy": self.epoch})
        self.workflow = SimpleNamespace(owner=self.owner, lock=threading.RLock(), processes=set(),
                                       _policy=lambda db: self.policy)
        self.created = []

    def launch(self, command, **kwargs):
        kwargs["stdout"].write(b'{"unavailable":true,"reason":"software fixture"}')
        kwargs["stdout"].flush()
        process = Process(returncode=0, poll=lambda: 0)
        self.created.append((command, kwargs, process))
        return process

    def run_worker(self):
        return run_collector(self.workflow, *self.policy, "fixed-test-request", [])

    def test_fixed_command_minimal_environment_and_owned_scratch_cleanup(self):
        with patch.dict(os.environ, {"UNRELATED_API_KEY": "not-in-child", "HTTP_PROXY": "not-in-child"}), patch("subprocess.Popen", side_effect=self.launch):
            result = self.run_worker()
        self.assertTrue(result["unavailable"])
        command, kwargs, _ = self.created[0]
        self.assertEqual(command[1:3], ["-I", "-B"])
        self.assertEqual(Path(command[3]).name, "options_eod_feed.py")
        self.assertEqual(command[4], "worker")
        self.assertIn("one-shared-account", command)
        self.assertEqual(command[-2:], ["--pause-rowid", "31"])
        self.assertNotIn("UNRELATED_API_KEY", kwargs["env"])
        self.assertNotIn("HTTP_PROXY", kwargs["env"])
        self.assertEqual(kwargs["env"]["ARIADNE_OPTIONS_EOD_COLLECTOR"], "1")
        self.assertEqual(list((self.registry.settings.data_dir / "options-eod-collector").iterdir()), [])
        self.assertFalse(self.workflow.processes)

    def test_permission_change_before_launch_never_starts_worker(self):
        self.workflow._policy = lambda db: tuple({"revision": 99} for _ in range(4))
        with patch("subprocess.Popen", side_effect=AssertionError("No child")), self.assertRaises(HandoffConflict):
            self.run_worker()

    def test_request_identity_cannot_overwrite_unaccepted_output(self):
        folder = self.registry.settings.data_dir / "options-eod-collector"
        folder.mkdir(parents=True)
        target = folder / "fixed-test-request.json"
        target.write_bytes(b"uncertain prior evidence")
        with patch("subprocess.Popen", side_effect=AssertionError("No replay")), self.assertRaises(FileExistsError):
            self.run_worker()
        self.assertEqual(target.read_bytes(), b"uncertain prior evidence")

    def test_revocation_during_child_kills_without_adoption(self):
        checks = 0
        def policy(db):
            nonlocal checks
            checks += 1
            return self.policy if checks == 1 else tuple({"revision": 99} for _ in range(4))
        self.workflow._policy = policy
        killed = []
        def launch(command, **kwargs):
            process = Process(returncode=None, poll=lambda: 0 if killed else None,
                kill=lambda: killed.append(True), wait=lambda **kw: 0)
            return process
        with patch("subprocess.Popen", side_effect=launch), self.assertRaises(HandoffConflict):
            self.run_worker()
        self.assertEqual(killed, [True])
        self.assertFalse(self.workflow.processes)

    def test_output_size_and_nonzero_exit_rejected(self):
        for mode in ("size", "exit", "object"):
            def launch(command, **kwargs):
                kwargs["stdout"].write(b"x" * 20 if mode == "size" else b"[]")
                kwargs["stdout"].flush()
                return Process(returncode=1 if mode == "exit" else 0, poll=lambda: 0)
            with self.subTest(mode=mode), patch("subprocess.Popen", side_effect=launch), patch("ariadne.options_eod_worker.MAX_OUTPUT", 10 if mode == "size" else 100), self.assertRaises(ValueError):
                self.run_worker()
            self.assertFalse(self.workflow.processes)

    def test_unsafe_contract_and_excess_scratch_fail_before_launch(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No child")), self.assertRaises(ValueError):
            run_collector(self.workflow, *self.policy, "fixed-test-request", ["AAPL261113C00100000"])
        folder = self.registry.settings.data_dir / "options-eod-collector"
        folder.mkdir(parents=True)
        for number in range(9):
            (folder / str(number)).write_bytes(b"retained")
        with patch("subprocess.Popen", side_effect=AssertionError("No child")), self.assertRaises(ValueError):
            self.run_worker()
        self.assertEqual(len(list(folder.iterdir())), 9)
