"""Generated publication boxes only; no market data, owner or strategy runs."""
import csv
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from ariadne import options_stress_review as review
from scripts import options_paired_review as cli
import test_options_paired_review_cli as legacy_fixtures
from test_options_paired_review import cohort


class StressReviewCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture=legacy_fixtures.PairedReviewCLITests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        for name in set(cli.STRESS_SOURCE_FILES)-set(cli.SOURCE_FILES):
            target=self.fixture.root/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((cli.ROOT/name).read_bytes())

    def args(self,*extra):
        return self.fixture.args("--detached-research","--focus","stress_put",*extra)

    def run_cli(self,args=None):
        return self.fixture.run_cli(args or self.args())

    def failed(self,args=None):
        return self.fixture.assert_failed(args or self.args())

    def replace_rows(self,values):
        for row in values:row.update(evidence_kind="synthetic_fixture",research_gate="synthetic_fixture_only")
        raw=io.StringIO(newline="");writer=csv.DictWriter(raw,fieldnames=review.COLUMNS)
        writer.writeheader();writer.writerows(values)
        self.fixture.csv=raw.getvalue().encode();self.fixture.source.write_bytes(self.fixture.csv)
        self.fixture.pub["comparison_rows"]=len(values)
        self.fixture.pub["artifacts"]["all-results.csv"]={"bytes":len(self.fixture.csv),
            "sha256":hashlib.sha256(self.fixture.csv).hexdigest()}
        self.fixture.save_pub()

    def test_default_focus_is_recovery_and_unknown_focus_rejected_before_output(self):
        self.assertEqual(self.fixture.args("--detached-research").focus,"recovery")
        args=self.args();args.focus="fit-any-rule"
        with self.assertRaisesRegex(ValueError,"fixed"):self.run_cli(args)
        self.assertFalse(self.fixture.output.exists())

    def test_stress_completion_registration_and_hashes_bind_fixed_module_and_focus(self):
        result=self.run_cli()
        saved=json.loads((self.fixture.output/"review.json").read_bytes())
        registration=json.loads((self.fixture.output/"registration.json").read_bytes())
        self.assertEqual(saved["kind"],"descriptive_stress_put_options_review")
        self.assertEqual(registration["focus"],"stress_put");self.assertEqual(registration["rule_sha256"],review.RULE_SHA256)
        self.assertEqual(result["focus"],"stress_put");self.assertEqual(result["rule_sha256"],review.RULE_SHA256)
        self.assertIn("ariadne/options_stress_review.py",result["module_sha256"])
        self.assertEqual(result["backtests_run"],0);self.assertEqual(result["paper_operations"],0)
        self.assertFalse((self.fixture.root/"data/ariadne.sqlite3").exists())
        for name,item in result["artifacts"].items():
            raw=(self.fixture.output/name).read_bytes()
            self.assertEqual((len(raw),hashlib.sha256(raw).hexdigest()),(item["bytes"],item["sha256"]))

    def test_volatility_has_its_own_stress_reference_in_the_same_export(self):
        self.replace_rows(cohort()+cohort(preset="volatility_regime"))
        self.run_cli()
        report=json.loads((self.fixture.output/"review.json").read_bytes())
        self.assertEqual({x["reference_candidate"] for x in report["strata"]},{"stress_breakdown_put","stress_shock_put"})
        self.assertTrue(all("stress_pairs" in x for x in report["strata"]))

    def test_default_completion_does_not_gain_new_focus_fields_or_dependency(self):
        result=self.run_cli(self.fixture.args("--detached-research"))
        self.assertNotIn("focus",result);self.assertNotIn("ariadne/options_stress_review.py",result["module_sha256"])
        self.assertEqual(json.loads((self.fixture.output/"review.json").read_bytes())["kind"],"descriptive_paired_options_review")

    def test_bad_publication_is_rejected_before_stress_dispatch(self):
        self.fixture.pub["execution_qualified"]=True;self.fixture.save_pub()
        with patch.object(review,"compare_rows",side_effect=AssertionError("Do not dispatch")):
            self.assertIn("completed",self.failed()["reason"])

    def test_stress_source_mutation_prevents_completion(self):
        original=review.compare_rows
        def changed(rows):
            value=original(rows);path=self.fixture.root/"ariadne/options_stress_review.py"
            path.write_bytes(path.read_bytes()+b"\n# generated change\n")
            return value
        with patch.object(review,"compare_rows",side_effect=changed):self.failed()

    def test_input_mutation_after_stress_analysis_prevents_completion(self):
        original=review.compare_rows
        def changed(rows):
            value=original(rows);path=self.fixture.source;stamp=path.stat()
            path.write_bytes(self.fixture.csv.replace(b"fixture-case",b"Fixture-case"))
            os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns));return value
        with patch.object(review,"compare_rows",side_effect=changed):self.failed()

    def test_stress_focus_respects_pause_epoch_and_preserves_account_rows(self):
        self.fixture.owner();database=self.fixture.root/"data/ariadne.sqlite3"
        original=review.compare_rows
        def changed(rows):
            value=original(rows)
            with closing(sqlite3.connect(database)) as db,db:
                db.execute("INSERT OR REPLACE INTO settings VALUES('paused','false')")
            return value
        args=self.fixture.args("--focus","stress_put")
        with patch.object(review,"compare_rows",side_effect=changed):
            self.assertIn("epoch",self.failed(args)["reason"])
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute("SELECT value FROM opt_accounts").fetchone()[0],"never read or mutate")

    def test_late_same_size_output_tampering_is_detected_before_completion(self):
        from ariadne import atomic
        original=atomic.publish_bytes
        def changed(path,raw,**kwargs):
            if path.name=="completed.json":
                earlier=self.fixture.output/"review.json";value=earlier.read_bytes();stamp=earlier.stat()
                earlier.write_bytes(b" "+value[1:]);os.utime(earlier,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            return original(path,raw,**kwargs)
        with patch.object(atomic,"publish_bytes",side_effect=changed):
            self.assertIn("output",self.failed()["reason"].lower())

    def test_stress_attempt_is_exclusive_and_never_replayed(self):
        self.run_cli();raw=(self.fixture.output/"completed.json").read_bytes()
        with self.assertRaises(FileExistsError):self.run_cli()
        self.assertEqual((self.fixture.output/"completed.json").read_bytes(),raw)


if __name__=="__main__":unittest.main()

