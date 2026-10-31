"""Initializer-free CLI preview and exact offline review binding."""
from contextlib import closing
from datetime import timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_qqq_history as fixtures
from ariadne.briefing import _DEFAULT
from ariadne.qqq_workflow import _json

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("_qqq_history_cli_under_test",ROOT/"scripts/qqq_history.py")
cli=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(cli)

class CLITests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.HistoryTests("runTest")
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.review=self.fixture.failed_legacy()
        self.root=self.fixture.settings.data_dir.parent
        cfg={**_DEFAULT,"enabled":True,"paused":False,"revision":1,"activation_at":(self.fixture.clock-timedelta(days=1)).isoformat()}
        with closing(sqlite3.connect(self.fixture.settings.data_dir/"briefing.sqlite3")) as db,db:
            db.execute("CREATE TABLE briefing_settings(id INTEGER PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO briefing_settings VALUES(1,?)",(_json(cfg),))
            db.execute("CREATE TABLE briefing_runs(request_id TEXT,day TEXT,status TEXT,created REAL,receipt TEXT)")

    def args(self,*extra):
        return cli.parser().parse_args(["review","--candidate",self.review["candidate_path"],"--candidate-sha256",self.review["candidate_sha256"],
            "--failed-request-id",self.review["failed_request_id"],"--output","staging/review.json",*extra])

    def test_review_uses_readonly_existing_connections_without_initializers_or_owner_writes(self):
        with self.fixture.service.database() as db:before=list(db.iterdump())
        connect=sqlite3.connect;opened=[]
        def observed(*args,**kwargs):
            opened.append(args[0]);self.assertIn("mode=ro",args[0])
            return connect(*args,**kwargs)
        with patch("ariadne.store.Store.__init__",side_effect=AssertionError("initializer")), \
                patch("sqlite3.connect",side_effect=observed),patch("socket.create_connection",side_effect=AssertionError("network")), \
                patch("subprocess.Popen",side_effect=AssertionError("worker")):
            result=cli.run(self.args(),root=self.root,now=self.fixture.clock)
        self.assertTrue(result["review"]["eligible"])
        self.assertEqual(result["owner_operations"],0)
        self.assertTrue(opened)
        with self.fixture.service.database() as db:self.assertEqual(list(db.iterdump()),before)
        self.assertTrue((self.root/"staging/review.json").is_file())

    def test_resolution_requires_exact_review_hash_and_new_request(self):
        cli.run(self.args(),root=self.root,now=self.fixture.clock)
        raw=(self.root/"staging/review.json").read_bytes()
        args=cli.parser().parse_args(["resolve","--review","staging/review.json","--review-sha256",hashlib.sha256(raw).hexdigest(),
            "--request-id","cli-resolution","--output","staging/resolution.json"])
        before=self.fixture.rows(self.review["failed_request_id"])
        result=cli.run(args,root=self.root,now=self.fixture.clock)
        self.assertEqual(result["receipt"]["status"],"completed")
        self.assertEqual(self.fixture.rows(self.review["failed_request_id"]),before)
        with self.assertRaises((ValueError,RuntimeError)):cli.run(args,root=self.root,now=self.fixture.clock)

    def test_changed_review_or_source_stops_before_service_resolution(self):
        cli.run(self.args(),root=self.root,now=self.fixture.clock)
        raw=(self.root/"staging/review.json").read_bytes()
        args=cli.parser().parse_args(["resolve","--review","staging/review.json","--review-sha256","0"*64,
            "--request-id","cli-resolution","--output","staging/resolution.json"])
        with patch("ariadne.qqq_workflow.QQQWorkflow.resolve_history",side_effect=AssertionError("owner call")),self.assertRaisesRegex(ValueError,"file changed"):
            cli.run(args,root=self.root,now=self.fixture.clock)
        args.review_sha256=hashlib.sha256(raw).hexdigest()
        changed=cli.source_hashes();changed["ariadne/qqq_data.py"]="0"*64
        with patch.object(cli,"source_hashes",return_value=changed),patch("ariadne.qqq_workflow.QQQWorkflow.resolve_history",side_effect=AssertionError("owner call")),self.assertRaisesRegex(ValueError,"source changed"):
            cli.run(args,root=self.root,now=self.fixture.clock)

    def test_missing_owner_is_not_created_and_output_cannot_escape_private_staging(self):
        args=self.args("--data-dir","missing-data")
        with self.assertRaises((OSError,ValueError)):cli.run(args,root=self.root,now=self.fixture.clock)
        self.assertFalse((self.root/"missing-data/ariadne.sqlite3").exists())
        args=self.args();args.output="reports/owner.json"
        with self.assertRaisesRegex(ValueError,"private staging"):cli.run(args,root=self.root,now=self.fixture.clock)

    def test_same_output_is_claimed_before_reentrant_review_can_start(self):
        from ariadne.qqq_workflow import QQQWorkflow
        original=QQQWorkflow.review_history;calls=[]
        def inspect(service,*args,**kwargs):
            calls.append(1)
            with self.assertRaises(FileExistsError):cli.run(self.args(),root=self.root,now=self.fixture.clock)
            return original(service,*args,**kwargs)
        with patch.object(QQQWorkflow,"review_history",new=inspect):
            cli.run(self.args(),root=self.root,now=self.fixture.clock)
        self.assertEqual(calls,[1])
        self.assertTrue((self.root/"staging/review.json.reserved").is_file())

    def test_foreign_output_during_resolution_is_not_overwritten_or_adopted(self):
        from ariadne.qqq_workflow import QQQWorkflow
        cli.run(self.args(),root=self.root,now=self.fixture.clock)
        raw=(self.root/"staging/review.json").read_bytes()
        args=cli.parser().parse_args(["resolve","--review","staging/review.json","--review-sha256",hashlib.sha256(raw).hexdigest(),
            "--request-id","cli-resolution","--output","staging/resolution.json"])
        original=QQQWorkflow._history_guard
        def guard(service,*values,**kwargs):
            (self.root/"staging/resolution.json").write_bytes(b"foreign evidence")
            return original(service,*values,**kwargs)
        before=self.fixture.rows(self.review["failed_request_id"])
        with patch.object(QQQWorkflow,"_history_guard",new=guard),self.assertRaisesRegex(ValueError,"claimed"):
            cli.run(args,root=self.root,now=self.fixture.clock)
        self.assertEqual((self.root/"staging/resolution.json").read_bytes(),b"foreign evidence")
        self.assertEqual(self.fixture.rows(self.review["failed_request_id"]),before)
        self.assertTrue((self.root/"staging/resolution.json.reserved").is_file())

if __name__=="__main__":unittest.main()
