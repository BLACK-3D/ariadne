"""Host publication and retained-policy lineage, using generated stores only."""
from copy import deepcopy
from datetime import datetime
import json
import os
import unittest
from unittest.mock import patch

from ariadne import options_stress_context as context, options_stress_eod as engine
from ariadne.options_eod_workflow import _stress_extension
from ariadne.options_workflow import _json, _digest
from ariadne.options_journal import review_options
from ariadne import options_workflow
import test_options_stress_workflow as fixtures


class StressPrefixTests(unittest.TestCase):
    def test_late_capture_tamper_rejects_both_schemas_before_any_account_adoption(self):
        for schema in (3,4):
            with self.subTest(schema=schema):
                case=fixtures.StressWorkflowTests("test_eligible_put_fills_same_account_and_reloads_published_context_and_codec")
                case.setUp();self.addCleanup(case.doCleanups);case.prepare()
                if schema==4:case.activate()
                before=case.paper()["state"];original=options_workflow.publish_bytes
                def tamper(path,raw,**kwargs):
                    reply=original(path,raw,**kwargs)
                    if path.name=="paper.json":
                        earlier=path.parent/"bundle.json";stat=earlier.stat();data=earlier.read_bytes()
                        earlier.write_bytes(b" "+data[1:]);os.utime(earlier,ns=(stat.st_atime_ns,stat.st_mtime_ns))
                    return reply
                with patch.object(options_workflow,"publish_bytes",side_effect=tamper):
                    with self.assertRaises(ValueError):case.collect(fixtures.DAY)
                with case.owner.database() as db:
                    saved=json.loads(db.execute("SELECT state FROM opt_accounts WHERE id=?",(case.original["id"],)).fetchone()[0])
                    self.assertEqual(saved,before)
                    self.assertEqual(db.execute("SELECT count(*) FROM opt_eod_data").fetchone()[0],0)
                    self.assertEqual(db.execute("SELECT count(*) FROM opt_events").fetchone()[0],0)
                    self.assertEqual(db.execute("SELECT count(*) FROM opt_runs WHERE action='eod_collect' AND status='failed'").fetchone()[0],1)

    def generated(self):
        case = fixtures.StressWorkflowTests("test_eligible_put_fills_same_account_and_reloads_published_context_and_codec")
        case.setUp(); self.addCleanup(case.doCleanups)
        case.test_eligible_put_fills_same_account_and_reloads_published_context_and_codec()
        return case

    def test_older_self_consistent_unpublished_gate_is_rejected_even_with_unchanged_current_account(self):
        case = self.generated(); before = case.paper()
        with case.owner.database() as db:
            receipt = json.loads(db.execute("SELECT receipt FROM opt_runs WHERE action='eod_collect' AND status='completed' ORDER BY rowid LIMIT 1").fetchone()[0])
        item = next(a for a in receipt["artifacts"] if a["path"].endswith("/paper.json"))
        path = case.registry._path(item["path"]); paper = json.loads(path.read_bytes())
        policy = paper["state"]["stress_policy"]; record = policy["context_history"][0]
        witness = deepcopy(record["gate"]["witness"])
        witness["source_receipt_original_sha256"] = "c"*64
        witness["witness_sha256"] = context._digest({k:v for k,v in witness.items() if k != "witness_sha256"})
        record["gate"] = context.select_gate([witness], decision_at=record["recorded_at"], source_date=None)
        record["prefix_sha256"] = context._digest(engine._gate_payload(record))
        policy["context_prefix_sha256"] = record["prefix_sha256"]
        engine.validate_state(paper["state"])
        paper["summary"] = engine.summarize(paper["state"], now=datetime.fromisoformat(receipt["recorded_at"]))
        raw = _json(paper).encode(); path.write_bytes(raw)
        item.update(bytes=len(raw), sha256=_digest(raw)); receipt["state_sha256"] = _digest(_json(paper["state"]).encode())
        with case.owner.database() as db:
            for table in ("opt_runs", "opt_reports"):
                db.execute("UPDATE "+table+" SET receipt=? WHERE request_id=?", (_json(receipt), receipt["request_id"]))
        self.assertEqual(case.paper(), before)
        with case.owner.database() as db:
            with self.assertRaisesRegex(ValueError, "retained published context"):
                case.owner.eod._audit_prefix(db, before["id"], before["state"], full=True)
        with self.assertRaisesRegex(ValueError, "retained published context"):
            review_options(case.owner, case.clock)

    def test_gate_cursor_extension_rejects_rewrites_deletions_and_detachment(self):
        case = self.generated(); states = []
        with case.owner.database() as db:
            for row in db.execute("SELECT receipt FROM opt_runs WHERE action='eod_collect' AND status='completed' ORDER BY rowid"):
                receipt = json.loads(row[0]); saved = case.owner.eod.verify(receipt)
                states.append(json.loads(saved["paper.json"])["state"]["stress_policy"])
        _stress_extension(states[0], states[1]); _stress_extension(states[1], states[2])
        for change in ("rewrite", "delete", "detach"):
            changed = deepcopy(states[1])
            if change == "rewrite": changed["context_history"][0]["context_input_rejected"] = True
            elif change == "delete": changed["context_count"] = 0
            else: changed["context_history"][-1]["previous_sha256"] = "d"*64
            with self.subTest(change=change), self.assertRaises(ValueError):
                _stress_extension(states[0], changed)
        _stress_extension(states[2], deepcopy(states[2]))


if __name__ == "__main__": unittest.main()
