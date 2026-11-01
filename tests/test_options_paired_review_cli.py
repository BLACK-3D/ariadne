"""Isolated source/publication and pause-boundary tests, not market evidence."""
import csv
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import options_paired_review as cli


def digest(raw): return hashlib.sha256(raw).hexdigest()


class PairedReviewCLITests(unittest.TestCase):
    def setUp(self):
        from ariadne.options_paired_review import COLUMNS
        parent=cli.ROOT/"runtime/tmp";parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="paired-review-",dir=parent)
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        for name in cli.SOURCE_FILES:
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((cli.ROOT/name).read_bytes())
        names=("cash","raw_recovery_call","raw_breakout_call","raw_breakdown_put",
               "growth_recovery_call","growth_breakout_call","stress_breakdown_put","regime_rotating")
        self.rows=[]
        for name in names:
            self.rows.append(dict(zip(COLUMNS,("fixture","individual","KO","conventional_regime","strict_sizes",name,
                "no_trade","0","0","0","0","0","2022-07-01","2022-12-30","2022-12-30","127","127",
                "True","True","True" if name.startswith(("growth_","stress_")) or name=="regime_rotating" else "False",
                "True","","synthetic_fixture_only","synthetic_fixture","assumed_eod","fixture-case"))))
        data=io.StringIO(newline="");writer=csv.DictWriter(data,fieldnames=COLUMNS);writer.writeheader();writer.writerows(self.rows)
        self.csv=data.getvalue().encode()
        directory=self.root/"reports/fixture";directory.mkdir(parents=True)
        self.source=directory/"all-results.csv";self.source.write_bytes(self.csv)
        self.pub={"schema":1,"kind":"derived_options_universe_report_publication","status":"completed",
            "research_only":True,"execution_qualified":False,"automatic_adoption":False,
            "all_source_and_output_artifacts_reverified":True,"comparison_rows":8,
            "artifacts":{"all-results.csv":{"bytes":len(self.csv),"sha256":digest(self.csv)}}}
        self.receipt=directory/"publication.json";self.save_pub()
        self.output=self.root/"staging/review"

    def save_pub(self): self.receipt.write_bytes(json.dumps(self.pub,sort_keys=True).encode())

    def args(self,*extra):
        return cli.parser().parse_args(["--input","reports/fixture/all-results.csv","--input-sha256",digest(self.csv),
            "--publication","reports/fixture/publication.json","--publication-sha256",digest(self.receipt.read_bytes()),
            "--output","staging/review",*extra])

    def run_cli(self,args=None):
        return cli.run(args or self.args("--detached-research"),root=self.root,
            now=datetime(2026,10,31,23,tzinfo=timezone.utc))

    def assert_failed(self,args=None):
        with self.assertRaises((ValueError,OSError)): self.run_cli(args)
        self.assertFalse((self.output/"completed.json").exists())
        failed=json.loads((self.output/"failed.json").read_bytes())
        self.assertFalse(failed["replay_allowed"]);self.assertFalse(failed["adopted"])
        return failed

    def test_completed_readonly_review_binds_full_inputs_outputs(self):
        result=self.run_cli()
        self.assertEqual(result["status"],"completed");self.assertEqual(result["input_rows"],8)
        self.assertEqual(result["backtests_run"],0);self.assertEqual(result["paper_operations"],0)
        for name,identity in result["artifacts"].items():
            raw=(self.output/name).read_bytes()
            self.assertEqual((len(raw),digest(raw)),(identity["bytes"],identity["sha256"]))
        self.assertFalse((self.root/"data/ariadne.sqlite3").exists())
        self.assertFalse(result["automatic_adoption"])

    def test_completed_and_uncertain_attempts_cannot_be_replayed(self):
        self.run_cli();raw=(self.output/"completed.json").read_bytes()
        with self.assertRaises(FileExistsError): self.run_cli()
        self.assertEqual((self.output/"completed.json").read_bytes(),raw)

    def test_preexisting_partial_attempt_is_preserved_without_adoption(self):
        self.output.mkdir(parents=True)
        marker=self.output/"attempt.json";marker.write_bytes(b'{"status":"uncertain"}')
        with self.assertRaises(FileExistsError): self.run_cli()
        self.assertEqual(marker.read_bytes(),b'{"status":"uncertain"}')
        self.assertFalse((self.output/"completed.json").exists())

    def test_wrong_input_and_receipt_hash_preserve_failures(self):
        args=self.args("--detached-research");args.input_sha256="0"*64
        self.assert_failed(args)
        with self.assertRaises(FileExistsError): self.run_cli(args)

    def test_malformed_publication_artifacts_rejected_without_parser_dispatch(self):
        self.pub["artifacts"]=[];self.save_pub()
        with patch("ariadne.options_paired_review.compare_rows",side_effect=AssertionError("Do not compare malformed source")):
            failure=self.assert_failed()
        self.assertIn("artifacts",failure["reason"])

    def test_row_count_and_header_are_bound(self):
        self.pub["comparison_rows"]=9;self.save_pub()
        self.assertIn("row count",self.assert_failed()["reason"])

    def test_same_size_restored_mtime_input_change_before_publication_is_rejected(self):
        from ariadne.options_paired_review import compare_rows
        def change(rows):
            value=compare_rows(rows)
            info=self.source.stat();self.source.write_bytes(self.csv.replace(b"fixture-case",b"Fixture-case"))
            os.utime(self.source,ns=(info.st_atime_ns,info.st_mtime_ns))
            return value
        with patch("ariadne.options_paired_review.compare_rows",side_effect=change):
            failure=self.assert_failed()
        self.assertIn("source",failure["reason"].lower())

    def test_source_code_change_before_completion_is_rejected(self):
        from ariadne.options_paired_review import compare_rows
        def change(rows):
            value=compare_rows(rows)
            target=self.root/"ariadne/options_paired_review.py"
            target.write_bytes(target.read_bytes()+b"\n# changed\n")
            return value
        with patch("ariadne.options_paired_review.compare_rows",side_effect=change): self.assert_failed()

    def owner(self,paused=False):
        parent=self.root/"data";parent.mkdir()
        with closing(sqlite3.connect(parent/"ariadne.sqlite3")) as db, db:
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO settings VALUES('paused',?)",(json.dumps(paused),))
            db.execute("CREATE TABLE opt_accounts(value TEXT)")
            db.execute("INSERT INTO opt_accounts VALUES('never read or mutate')")

    def test_detached_cannot_bypass_owner_and_paused_owner_refuses(self):
        self.owner(paused=True)
        with self.assertRaisesRegex(ValueError,"bypass"): self.run_cli()
        self.assertFalse(self.output.exists())
        failure=self.assert_failed(self.args())
        self.assertIn("paused",failure["reason"])

    def test_pause_epoch_change_during_comparison_cancels_publication(self):
        from ariadne.options_paired_review import compare_rows
        self.owner()
        def change(rows):
            value=compare_rows(rows)
            with closing(sqlite3.connect(self.root/"data/ariadne.sqlite3")) as db, db:
                db.execute("INSERT OR REPLACE INTO settings VALUES('paused','false')")
            return value
        with patch("ariadne.options_paired_review.compare_rows",side_effect=change):
            failure=self.assert_failed(self.args())
        self.assertIn("epoch",failure["reason"])

    def test_path_and_digest_validation_precede_attempt_creation(self):
        args=self.args("--detached-research");args.output="data/overwrite"
        with self.assertRaises(ValueError): self.run_cli(args)
        self.assertFalse(self.output.exists())
        args=self.args("--detached-research");args.input="../outside.csv"
        with self.assertRaises(ValueError): self.run_cli(args)

    def test_report_escapes_untrusted_values_without_embedded_scripts(self):
        raw=cli.render({"value":"</pre><script>alert(1)</script>"}).decode()
        self.assertNotIn("<script>",raw);self.assertIn("&lt;script&gt;",raw)


if __name__=="__main__": unittest.main()
