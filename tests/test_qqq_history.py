"""Generated-only history review, metadata adoption and failure preservation."""
import base64
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.handoff import HandoffConflict
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.qqq_workflow import QQQWorkflow,compare_history,_json,_hash
from test_qqq_recovery import fixture,through

REASON="Accepted economic history changed (including possible split/revision); explicit review is required."

class HistoryTests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/"runtime/tmp"
        parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="qqq-history-",dir=parent)
        self.addCleanup(temporary.cleanup)
        self.settings=Settings(data_dir=Path(temporary.name)/"data")
        self.store=Store(self.settings.data_dir/"ariadne.sqlite3")
        self.store.set_setting("paused",False)
        self.store.set_setting("internet_access",{"enabled":True,"revision":1})
        self.registry=ToolRegistry(self.settings)
        self.controls={"enabled":True,"paused":False,"revision":1}
        self.service=QQQWorkflow(self.registry,self.store,controls=lambda:dict(self.controls))
        self.data=through(fixture(),90)
        self.clock=datetime.fromisoformat(self.data["as_of"])
        self.counter=0
        self.service.configure({"enabled":True,"paused":False,"expected_revision":0},self.clock-timedelta(hours=1))
        from ariadne.qqq_data import parse_chart
        self.real_parse_chart=parse_chart
        # Transaction fixtures use90 rows. Retain every raw normalizer check
        # while isolating only the collector's1999/>5000-row coverage premise.
        def short_generated(raw,symbol,clock,*,full_history=False):
            self.assertTrue(full_history)
            return parse_chart(raw,symbol,clock,full_history=False)
        self.short_chart=patch("ariadne.qqq_data.parse_chart",side_effect=short_generated)
        self.short_chart.start();self.addCleanup(self.short_chart.stop)

    def body(self,**extra):
        self.counter+=1
        return {"request_id":"generated-"+str(self.counter),"expected_revision":self.service.status(self.clock)["revision"],**extra}

    def bundle(self,data):
        from ariadne.qqq_data import _bundle_quality,source_url
        data=deepcopy(data);payloads={};notes=[]
        analysis=datetime.fromisoformat(data["as_of"])
        started=analysis-timedelta(seconds=2)
        for symbol,entry in data["symbols"].items():
            bars=entry["bars"]
            stamps=[int(datetime.fromisoformat(row["date"]).replace(hour=16,tzinfo=timezone.utc).timestamp()) for row in bars]
            arrays={key:[int(row[key]) if key=="volume" else float(row[key]) for row in bars] for key in ("open","high","low","close","volume")}
            chart={"chart":{"error":None,"result":[{"meta":{"symbol":symbol,"currency":"USD","instrumentType":"ETF",
                "exchangeTimezoneName":"America/New_York","dataGranularity":"1d"},"timestamp":stamps,
                "indicators":{"quote":[arrays],"adjclose":[{"adjclose":[float(row["adj_close"]) for row in bars]}]},"events":{}}]}}
            raw=_json(chart).encode()
            series,note=self.real_parse_chart(raw,symbol,analysis,full_history=False)
            series["source"].update(url=source_url(symbol,started),retrieved_at=(analysis-timedelta(seconds=1) if symbol=="QQQ" else analysis).isoformat())
            data["symbols"][symbol]=series;notes.append(note)
            payloads[symbol]=base64.b64encode(raw).decode()
        return {"dataset":data,"quality":_bundle_quality(notes,started,analysis),"raw_payloads":payloads}

    def refresh(self,data):
        bundle=self.bundle(data)
        self.clock=datetime.fromisoformat(data["as_of"])
        return self.service.refresh(self.body(),self.clock,collector=lambda *args:bundle)

    def failed_legacy(self,*,paper=True):
        self.refresh(self.data)
        if paper:
            self.service.backtest(self.body(),self.clock)
            self.service.start_paper(self.body(replace=False,acknowledge_simulated=True),self.clock+timedelta(seconds=1))
        candidate=through(fixture(),91)
        candidate["symbols"]["QQQ"]["bars"][-2]["volume"]+=100
        candidate["symbols"]["SPY"]["bars"][-2]["volume"]+=200
        bundle=self.bundle(candidate)
        self.clock=datetime.fromisoformat(candidate["as_of"])
        failed=self.body()
        with patch.object(self.service,"_merge",side_effect=ValueError(REASON)),self.assertRaisesRegex(ValueError,"history changed"):
            self.service.refresh(failed,self.clock,collector=lambda *args:bundle)
        raw=_json(bundle).encode()
        relative="market-data/qqq-review/generated.json"
        path=self.registry._path(relative)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
        return {"candidate_path":relative,"candidate_sha256":hashlib.sha256(raw).hexdigest(),"failed_request_id":failed["request_id"]}

    def rows(self,failed_id):
        with self.service.database() as db:
            return {"failure":tuple(db.execute("SELECT * FROM qqq_runs WHERE request_id=?",(failed_id,)).fetchone()),
                "accounts":[tuple(row) for row in db.execute("SELECT * FROM qqq_accounts ORDER BY rowid")],
                "events":[tuple(row) for row in db.execute("SELECT * FROM qqq_events ORDER BY id")]}

    def resolve_body(self,review):
        plan=self.service.review_history(review,self.clock)
        self.counter+=1
        return plan,{**plan["resolve_body_without_request_id"],"request_id":"resolution-"+str(self.counter)}

    def test_volume_and_adjclose_are_exact_diagnostics_under_frozen_source(self):
        changed=deepcopy(self.data)
        changed["symbols"]["QQQ"]["bars"][2]["volume"]+=11
        changed["symbols"]["SPY"]["bars"][3]["adj_close"]="99.001"
        review=compare_history(self.data,changed)
        self.assertTrue(review["eligible"]);self.assertEqual(review["volume_change_count"],1)
        self.assertEqual(review["volume_changes"][0]["before"]+11,review["volume_changes"][0]["after"])
        self.assertEqual(review["volume_changes_sha256"],_hash(review["volume_changes"]))
        self.assertEqual(review["adj_close_change_count"],1);self.assertFalse(review["tolerance_used"])
        with patch("ariadne.qqq_workflow.DIAGNOSTIC_ENGINE_SHA256","0"*64),self.assertRaisesRegex(ValueError,"fingerprint"):
            compare_history(self.data,changed)
        changed=deepcopy(self.data)
        changed["symbols"]["QQQ"]["bars"][2]["high"]=str(float(changed["symbols"]["QQQ"]["bars"][2]["high"])+0.000000001)
        review=compare_history(self.data,changed)
        self.assertFalse(review["eligible"]);self.assertEqual(review["protected_change_count"],1)

    def test_readonly_plan_and_resolution_preserve_failure_account_events_and_do_not_advance(self):
        review=self.failed_legacy();before=self.rows(review["failed_request_id"])
        before_status=self.service.status(self.clock)
        with patch("socket.create_connection",side_effect=AssertionError("network")),patch("subprocess.Popen",side_effect=AssertionError("worker")):
            plan,body=self.resolve_body(review)
            self.assertEqual(self.rows(review["failed_request_id"]),before)
            self.assertTrue(plan["eligible"]);self.assertEqual(plan["comparison"]["volume_change_count"],2)
            receipt=self.service.resolve_history(body,self.clock)
        self.assertEqual(receipt["status"],"completed")
        self.assertEqual(self.rows(review["failed_request_id"]),before)
        after=self.service.status(self.clock)
        self.assertIsNone(after["hold"])
        self.assertEqual(after["latest_history_resolution"],receipt)
        self.assertEqual(after["paper"],before_status["paper"])
        self.assertEqual(after["events"],before_status["events"])
        self.assertNotEqual(after["data_sha256"],before_status["data_sha256"])
        self.assertEqual(receipt["funding_added"],"0")
        self.assertFalse(receipt["paper_state_changed"])
        with self.assertRaises(HandoffConflict):self.service.resolve_history(body,self.clock)

    def test_original_failed_id_is_never_replayed_and_future_due_can_proceed(self):
        review=self.failed_legacy();_,body=self.resolve_body(review)
        self.service.resolve_history(body,self.clock)
        failed_body={"request_id":review["failed_request_id"],"expected_revision":self.service.status(self.clock)["revision"]}
        with patch.object(self.service,"_collect",side_effect=AssertionError("replayed network")),self.assertRaises(HandoffConflict):
            self.service.refresh(failed_body,self.clock)
        # A different future scheduler day may perform an ordinary new operation.
        with patch.object(self.service,"refresh",return_value={"status":"completed"}) as refresh:
            self.service.run_due(self.clock+timedelta(days=1))
            self.assertEqual(refresh.call_count,1)

    def test_stale_hashes_revision_and_pauses_cannot_mutate_owner_state(self):
        review=self.failed_legacy();_,body=self.resolve_body(review)
        before=self.rows(review["failed_request_id"])
        for key in ("failed_receipt_sha256","expected_data_sha256","expected_account_state_sha256","expected_account_policy_sha256","expected_events_sha256","expected_review_sha256","expected_authority_sha256"):
            changed={**body,key:"0"*64}
            with self.subTest(key=key),self.assertRaises(HandoffConflict):self.service.resolve_history(changed,self.clock)
        with self.assertRaises(HandoffConflict):self.service.resolve_history({**body,"expected_revision":0},self.clock)
        self.store.set_setting("paused",True)
        with self.assertRaises(HandoffConflict):self.service.resolve_history(body,self.clock)
        self.assertEqual(self.rows(review["failed_request_id"]),before)

    def test_another_uncertain_hold_is_not_excused(self):
        review=self.failed_legacy()
        with self.service.database() as db:
            row=list(db.execute("SELECT * FROM qqq_runs WHERE request_id=?",(review["failed_request_id"],)).fetchone())
            row[0]="other-uncertain";row[2]=None;row[4]="running"
            db.execute("INSERT INTO qqq_runs VALUES(?,?,?,?,?,?,?)",row)
        plan,body=self.resolve_body(review)
        self.assertFalse(plan["eligible"])
        with self.assertRaises(HandoffConflict):self.service.resolve_history(body,self.clock)

    def test_changed_protected_history_cannot_be_resolved(self):
        review=self.failed_legacy()
        path=self.registry._path(review["candidate_path"])
        bundle=json.loads(path.read_bytes())
        row=bundle["dataset"]["symbols"]["QQQ"]["bars"][3]
        row["high"]=str(float(row["high"])+1)
        bundle=self.bundle(bundle["dataset"]);raw=_json(bundle).encode();path.write_bytes(raw)
        review["candidate_sha256"]=hashlib.sha256(raw).hexdigest()
        plan,body=self.resolve_body(review)
        self.assertFalse(plan["eligible"])
        with self.assertRaises(HandoffConflict):self.service.resolve_history(body,self.clock)

    def test_candidate_change_during_publication_stops_and_keeps_failed_receipts(self):
        from ariadne import atomic
        review=self.failed_legacy();_,body=self.resolve_body(review)
        before=self.rows(review["failed_request_id"])
        original=atomic.publish_bytes
        def publish(path,raw,*,checkpoint=None):
            self.registry._path(review["candidate_path"]).write_bytes(b"changed generated file")
            return original(path,raw,checkpoint=checkpoint)
        with patch("ariadne.qqq_workflow.publish_bytes",side_effect=publish),self.assertRaises(HandoffConflict):
            self.service.resolve_history(body,self.clock)
        self.assertEqual(self.rows(review["failed_request_id"]),before)
        with self.service.database() as db:
            self.assertEqual(db.execute("SELECT status FROM qqq_runs WHERE request_id=?",(body["request_id"],)).fetchone()[0],"failed")
            self.assertGreater(db.execute("SELECT bytes FROM qqq_archives WHERE request_id=?",(body["request_id"],)).fetchone()[0],0)
        with self.assertRaises((HandoffConflict,ValueError)):self.service.resolve_history(body,self.clock)

    def test_raw_payload_fixed_url_and_normalized_prices_are_bound_before_resolution(self):
        review=self.failed_legacy()
        path=self.registry._path(review["candidate_path"])
        original=path.read_bytes()
        for kind in ("unrelated_raw","wrong_url","forged_appended_price"):
            bundle=json.loads(original)
            if kind=="unrelated_raw":
                raw=b'{"unrelated":"generated"}'
                bundle["raw_payloads"]["QQQ"]=base64.b64encode(raw).decode()
                bundle["dataset"]["symbols"]["QQQ"]["source"]["sha256"]=hashlib.sha256(raw).hexdigest()
            elif kind=="wrong_url":bundle["dataset"]["symbols"]["QQQ"]["source"]["url"]="https://example.com/QQQ"
            else:
                row=bundle["dataset"]["symbols"]["QQQ"]["bars"][-1]
                row["close"]+=0.1
            raw=_json(bundle).encode();path.write_bytes(raw)
            changed={**review,"candidate_sha256":hashlib.sha256(raw).hexdigest()}
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.service.review_history(changed,self.clock)
        path.write_bytes(original)
        self.short_chart.stop()
        with self.assertRaisesRegex(ValueError,"full history"):
            self.service.review_history(review,self.clock)

    def test_artifact_changed_after_its_readback_cannot_be_adopted(self):
        from ariadne import atomic
        review=self.failed_legacy();_,body=self.resolve_body(review)
        old_digest=self.service.status(self.clock)["data_sha256"]
        before=self.rows(review["failed_request_id"])
        original=atomic.publish_bytes
        def publish(path,raw,*,checkpoint=None):
            result=original(path,raw,checkpoint=checkpoint)
            if path.name=="binding.json":
                target=path.parent/"accepted-before.json"
                info=target.stat();previous=target.read_bytes()
                changed=previous.replace(b'"close":100.0',b'"close":100.1',1)
                self.assertNotEqual(changed,previous);self.assertEqual(len(changed),len(previous))
                target.write_bytes(changed);os.utime(target,ns=(info.st_atime_ns,info.st_mtime_ns))
            return result
        with patch("ariadne.qqq_workflow.publish_bytes",side_effect=publish),self.assertRaisesRegex(ValueError,"before adoption"):
            self.service.resolve_history(body,self.clock)
        self.assertEqual(self.rows(review["failed_request_id"]),before)
        self.assertEqual(self.service.status(self.clock)["data_sha256"],old_digest)
        with self.service.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM qqq_history_resolutions").fetchone()[0],0)

    def test_missing_resolution_evidence_or_tampered_link_reinstates_original_hold(self):
        review=self.failed_legacy();_,body=self.resolve_body(review)
        receipt=self.service.resolve_history(body,self.clock)
        artifact=self.registry._path(receipt["artifacts"][0]["path"])
        artifact.write_bytes(b"changed retained evidence")
        status=self.service.status(self.clock)
        self.assertEqual(status["hold"]["request_id"],review["failed_request_id"])
        self.assertIn("resolution_error",status["hold"])
        self.assertIsNone(status["latest_history_resolution"])
        with patch.object(self.service,"_collect",side_effect=AssertionError("network")),self.assertRaises(HandoffConflict):
            self.service.refresh(self.body(),self.clock)

    def test_changed_failed_record_metadata_cannot_hide_behind_unchanged_receipt(self):
        review=self.failed_legacy();_,body=self.resolve_body(review)
        self.service.resolve_history(body,self.clock)
        with self.service.database() as db:
            db.execute("UPDATE qqq_runs SET digest=? WHERE request_id=?",("0"*64,review["failed_request_id"]))
        status=self.service.status(self.clock)
        self.assertEqual(status["hold"]["request_id"],review["failed_request_id"])
        self.assertIn("resolution_error",status["hold"])
        self.assertIsNone(status["latest_history_resolution"])

    def test_future_large_volume_revision_gets_full_charged_artifact(self):
        self.refresh(self.data)
        changed=deepcopy(self.data)
        for entry in changed["symbols"].values():
            for row in entry["bars"]:row["volume"]+=7
        receipt=self.refresh(changed)
        revision=receipt["history_revision"]
        self.assertEqual(revision["volume_change_count"],180)
        self.assertNotIn("volume_changes",revision)
        raw=self.registry._path(revision["volume_changes_artifact"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),revision["volume_changes_sha256"])
        self.assertEqual(len(json.loads(raw)),180)
        with self.service.database() as db:self.assertEqual(db.execute("SELECT bytes FROM qqq_archives WHERE request_id=?",(receipt["request_id"],)).fetchone()[0],len(raw))

if __name__=="__main__":unittest.main()
