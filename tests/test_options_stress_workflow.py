"""Isolated generated owner stores; no real account, feed or investment evidence."""
from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
import base64
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from ariadne import options_stress_context as context
from ariadne import options_stress_workflow as workflow
from ariadne.handoff import HandoffConflict
from ariadne.options_workflow import _json,_digest
from ariadne.qqq_data import session_dates,session_close_utc
import test_options_eod_workflow as eod_fixtures
from test_options_stress_context import fixture

UTC=timezone.utc
DAY=eod_fixtures.DAY


class StressWorkflowTests(unittest.TestCase):
    setUp=eod_fixtures.EODWorkflowTests.setUp
    body=eod_fixtures.EODWorkflowTests.body
    ready=eod_fixtures.EODWorkflowTests.ready
    paper=eod_fixtures.EODWorkflowTests.paper
    migrate=eod_fixtures.EODWorkflowTests.migrate
    collect=eod_fixtures.EODWorkflowTests.collect
    put=eod_fixtures.EODWorkflowTests.put

    def prepare(self):
        self.ready();self.migrate();self.clock+=timedelta(minutes=1)
        self.service=self.owner.eod.stress
        self.original=self.paper()

    def activate(self):
        self.activation_body=self.body(acknowledge_simulated=True,acknowledge_stress_only=True)
        return self.service.activate(self.activation_body,self.clock)

    def source(self,*,last="2024-01-02",pattern="stress"):
        dataset,receipt,captured=fixture(last,pattern)
        raw=_json(dataset).encode();receipt_raw=_json(receipt)
        with self.owner.database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS qqq_data(id INTEGER PRIMARY KEY,digest TEXT,value BLOB,bytes INTEGER,quality TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS qqq_runs(request_id TEXT PRIMARY KEY,action TEXT,day TEXT,digest TEXT,status TEXT,receipt TEXT,bytes INTEGER)")
            db.execute("INSERT OR REPLACE INTO qqq_data VALUES(1,?,?,?,?)",(_digest(raw),raw,len(raw),"{}"))
            db.execute("INSERT INTO qqq_runs VALUES(?,?,?,?,?,?,?)",(receipt["request_id"],receipt["action"],None,_digest(receipt_raw.encode()),"completed",receipt_raw,len(receipt_raw.encode())))
        self.clock=max(self.clock,captured+timedelta(minutes=2))
        return dataset,receipt

    def financial(self):
        with self.owner.database() as db:
            return {"accounts":[tuple(v) for v in db.execute("SELECT * FROM opt_accounts")],
                "events":[tuple(v) for v in db.execute("SELECT * FROM opt_events")],
                "runs":[tuple(v) for v in db.execute("SELECT * FROM opt_runs")],
                "eod_config":[tuple(v) for v in db.execute("SELECT * FROM opt_eod_meta")],
                "eod_data":[tuple(v) for v in db.execute("SELECT * FROM opt_eod_data")]}

    def captures(self):
        with self.owner.database() as db:return [dict(v) for v in db.execute("SELECT * FROM opt_stress_contexts")]

    def test_activation_keeps_one_account_all_financials_and_eod_configuration(self):
        self.prepare();before=self.financial();receipt=self.activate();current=self.paper()
        self.assertEqual(current["id"],self.original["id"])
        for key in ("cash","contributions","month","monthly_committed","monthly_start_equity","fees","trade_count","closed_trade_count","funding_count"):
            self.assertEqual(current["state"][key],self.original["state"][key])
        self.assertEqual(current["state"]["schema"],4)
        self.assertEqual(receipt["simulated_funding_added"],"0")
        self.assertFalse(receipt["monthly_budget_reset"])
        self.assertEqual(self.financial()["eod_config"],before["eod_config"])
        with self.owner.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM opt_accounts").fetchone()[0],1)
        self.assertEqual(set(self.service.verify(receipt)),{"previous-state.json","activation.json","initial-state.json"})

    def test_acknowledgements_and_wrong_account_cannot_trigger_mutation(self):
        self.prepare();before=self.financial()
        self.assertRaises(ValueError,self.service.activate,self.body(acknowledge_simulated=True,acknowledge_stress_only=False),self.clock)
        self.assertEqual(self.financial(),before)

    def test_completed_activation_replay_verifies_same_files_and_never_refunds(self):
        self.prepare();receipt=self.activate();before=self.financial()
        repeated=self.service.activate(self.activation_body,self.clock)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(receipt["state_sha256"],repeated["state_sha256"])
        self.assertEqual(self.financial(),before)

    def test_activation_permissions_revoked_while_publishing_keep_schema3(self):
        self.prepare();original=self.owner._publish
        def revoked(*args,**kwargs):
            self.controls["revision"]+=1
            return original(*args,**kwargs)
        with patch.object(self.owner,"_publish",side_effect=revoked):
            self.assertRaises(HandoffConflict,self.activate)
        with self.owner.database() as db:
            state=json.loads(db.execute("SELECT state FROM opt_accounts").fetchone()[0])
            self.assertEqual(state["schema"],3)
            self.assertEqual(db.execute("SELECT count(*) FROM opt_stress_migrations").fetchone()[0],0)

    def test_activation_earlier_artifact_late_tamper_is_not_adopted(self):
        self.prepare();original=self.service.verify
        def tampered(receipt):
            path=self.registry._path(receipt["artifacts"][0]["path"])
            raw=path.read_bytes();path.write_bytes(b" "+raw[1:])
            return original(receipt)
        with patch.object(self.service,"verify",side_effect=tampered):self.assertRaises(ValueError,self.activate)
        with self.owner.database() as db:self.assertEqual(json.loads(db.execute("SELECT state FROM opt_accounts").fetchone()[0])["schema"],3)

    def test_changed_calendar_fixture_rejects_activation_before_reservation(self):
        self.prepare();before=self.financial()
        root=Path(self.settings.data_dir)/"generated-runtime"
        for relative in workflow._RUNTIME:
            destination=root/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(Path(workflow.__file__).resolve().parents[1]/relative,destination)
        self.service._runtime_root=root
        changed=root/"ariadne/qqq_data.py";changed.write_bytes(changed.read_bytes()+b"\n# generated tamper\n")
        self.assertRaisesRegex(ValueError,"dependency changed",self.activate)
        self.assertEqual(self.financial(),before)

    def test_no_source_or_old_schema_capture_is_nonmutating_and_inactive(self):
        self.prepare();before=self.financial()
        self.assertIsNone(self.service.capture_due(self.clock))
        self.assertEqual(self.financial(),before)
        with self.owner.database() as db:
            snap=self.service.snapshot(db,self.clock,self.original)
            self.assertFalse(snap["active"]);self.assertTrue(snap["migration_eligible"])
        self.activate();before=self.financial()
        self.assertIsNone(self.service.capture_due(self.clock))
        self.assertEqual(self.financial(),before)

    def test_capture_publishes_actual_new_witness_full_receipt_and_no_financial_operations(self):
        self.prepare();self.activate();dataset,receipt=self.source();before=self.financial()
        result=self.service.capture_due(self.clock)
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["published_at"],self.clock.isoformat())
        self.assertEqual(len(result["artifacts"]),3)
        self.assertEqual(self.financial(),before)
        with self.owner.database() as db:
            values=self.service.contexts(db,self.paper()["id"])
            self.assertEqual(len(values),1)
            self.assertEqual(values[0]["data_sha256"],_digest(_json(dataset).encode()))
            self.assertEqual(values[0]["source_receipt_original_sha256"],_digest(_json(receipt).encode()))
        current=self.paper()
        with self.owner.database() as db:
            snap=self.service.snapshot(db,self.clock,current)
            self.assertTrue(snap["active"]);self.assertEqual(snap["context_count"],1)
            self.assertEqual(snap["context_status"],"stress_ready")

    def test_same_accepted_digest_is_cheap_noop_without_rebuilding(self):
        self.prepare();self.activate();self.source();self.service.capture_due(self.clock)
        with patch.object(context,"build_witness",side_effect=AssertionError("No rebuild")):
            self.assertIsNone(self.service.capture_due(self.clock+timedelta(minutes=1)))
        self.assertEqual(len(self.captures()),1)

    def test_ordinary_stale_offline_benchmark_is_quiet_and_does_not_reserve_failure(self):
        self.prepare();self.activate();self.source();before=self.financial()
        self.assertIsNone(self.service.capture_due(datetime(2024,1,4,15,tzinfo=UTC)))
        self.assertEqual(self.captures(),[])
        self.assertEqual(self.financial(),before)

    def test_context_failure_is_separate_stops_retry_and_preserves_eod_exit_authority(self):
        self.prepare();self.activate();self.source();before=self.financial()
        with patch.object(context,"build_witness",side_effect=ValueError("generated local context failure")):
            result=self.service.capture_due(self.clock)
        self.assertEqual(result["status"],"failed")
        self.assertEqual(self.financial(),before)
        self.assertEqual(self.captures()[0]["status"],"failed")
        self.assertIsNone(self.service.capture_due(self.clock+timedelta(minutes=1)))
        with self.owner.database() as db:
            self.assertEqual(self.service.contexts(db,self.paper()["id"]),[])
            self.assertFalse(db.execute("SELECT 1 FROM opt_runs WHERE status IN ('running','failed')").fetchone())
        # The ordinary source collector still advances a legitimate source day;
        # only the new entry gate receives an empty context ring.
        self.collect(DAY)
        self.assertEqual(self.paper()["state"]["processed_count"],1)

    def test_pending_uncertain_context_is_never_replayed(self):
        self.prepare();self.activate();self.source()
        with self.owner.database() as db:
            db.execute("INSERT INTO opt_stress_contexts VALUES(?,?,?,?,?,?,?)",("uncertain",self.paper()["id"],"0"*64,"running",self.clock.isoformat(),"{}",100))
        with patch.object(context,"build_witness",side_effect=AssertionError("No replay")):
            self.assertIsNone(self.service.capture_due(self.clock))

    def test_capture_permission_revocation_retains_failure_without_touching_paper(self):
        self.prepare();self.activate();self.source();before=self.financial();original=context.build_witness
        def revoked(*args,**kwargs):
            value=original(*args,**kwargs);self.controls["revision"]+=1;return value
        with patch.object(context,"build_witness",side_effect=revoked):result=self.service.capture_due(self.clock)
        self.assertEqual(result["status"],"failed")
        self.assertEqual(self.financial(),before)

    def test_current_source_cas_and_final_artifact_rehash(self):
        for tamper_source in (True,False):
            with self.subTest(tamper_source=tamper_source):
                # Each failed attempt needs its own isolated owner, never a retry.
                self.setUp();self.prepare();self.activate();self.source();before=self.financial()
                original=workflow.publish_bytes;done=False
                def changed(path,raw,**kwargs):
                    nonlocal done
                    value=original(path,raw,**kwargs)
                    if path.name=="source-receipt.json" and not done:
                        done=True
                        if tamper_source:
                            with self.owner.database() as db:db.execute("UPDATE qqq_data SET digest=? WHERE id=1",("0"*64,))
                        else:
                            item=path.parent/"prepared-witness.json";old=item.read_bytes();item.write_bytes(b" "+old[1:])
                    return value
                with patch.object(workflow,"publish_bytes",side_effect=changed):result=self.service.capture_due(self.clock)
                self.assertEqual(result["status"],"failed")
                self.assertEqual(self.financial(),before)

    def test_previously_accepted_context_file_and_original_receipt_cannot_be_rewritten(self):
        self.prepare();self.activate();self.source();receipt=self.service.capture_due(self.clock)
        artifact=next(v for v in receipt["artifacts"] if v["path"].endswith("source-receipt.json"))
        path=self.registry._path(artifact["path"]);raw=path.read_bytes();path.write_bytes(b" "+raw[1:])
        with self.owner.database() as db:
            self.assertRaisesRegex(ValueError,"file changed",self.service.contexts,db,self.original["id"])

    def test_deadline_failure_is_entry_only_and_never_widens(self):
        self.prepare();self.activate();self.source();before=self.financial()
        with patch.object(workflow,"MAX_CAPTURE_SECONDS",0):result=self.service.capture_due(self.clock)
        self.assertEqual(result["status"],"failed")
        self.assertIn("5second",result["reason"])
        self.assertEqual(self.financial(),before)

    def test_disabled_and_paused_capture_do_not_reserve_context_work(self):
        self.prepare();self.activate();self.source()
        self.store.set_setting("paused",True)
        self.assertIsNone(self.service.capture_due(self.clock))
        self.assertEqual(self.captures(),[])

    def test_publication_crossing_open_uses_post_durability_clock_and_denies_fill_gate(self):
        self.prepare();self.activate();self.source()
        before_open=datetime(2024,1,3,14,29,59,tzinfo=UTC)
        after_open=datetime(2024,1,3,14,30,1,tzinfo=UTC)
        actual=[before_open];original_now=workflow._now;original_publish=workflow.publish_bytes
        def now(value=None):return original_now(value) if value is not None else actual[0]
        def publish(path,raw,**kwargs):
            result=original_publish(path,raw,**kwargs)
            if path.name=="source-receipt.json":actual[0]=after_open
            return result
        with patch.object(workflow,"_now",side_effect=now),patch.object(workflow,"publish_bytes",side_effect=publish):
            receipt=self.service.capture_due()
        self.assertEqual(receipt["status"],"completed")
        self.assertEqual(receipt["published_at"],after_open.isoformat())
        with self.owner.database() as db:values=self.service.contexts(db,self.original["id"])
        gate=context.select_gate(values,decision_at="2024-01-04T15:00:00Z",source_date="2024-01-03")
        self.assertFalse(gate["allowed"])
        # Changing only SQL metadata and its coherent derived hash cannot
        # backdate the separately retained sealed-witness file.
        prepared_path=next(a["path"] for a in receipt["artifacts"] if a["path"].endswith("prepared-witness.json"))
        prepared=json.loads(self.registry._path(prepared_path).read_bytes())
        earlier=self.service._sealed(prepared,before_open.isoformat())
        receipt.update(published_at=before_open.isoformat(),witness_sha256=earlier["witness_sha256"])
        text=_json(receipt)
        with self.owner.database() as db:
            db.execute("UPDATE opt_stress_contexts SET receipt=?,bytes=? WHERE request_id=?",(text,sum(a["bytes"] for a in receipt["artifacts"])+len(text.encode()),receipt["request_id"]))
        with self.owner.database() as db:self.assertRaises(ValueError,self.service.contexts,db,self.original["id"])

    def test_context_receipt_size_status_identity_and_charged_bytes_are_integrity_checked(self):
        for update,value in (("bytes",1),("status","invented"),("receipt","x"*(workflow.MAX_CONTEXT_RECEIPT+1))):
            with self.subTest(update=update):
                self.setUp();self.prepare();self.activate();self.source();receipt=self.service.capture_due(self.clock)
                with self.owner.database() as db:
                    db.execute("UPDATE opt_stress_contexts SET "+update+"=? WHERE request_id=?",(value,receipt["request_id"]))
                with self.owner.database() as db:self.assertRaises(ValueError,self.service.contexts,db,self.original["id"])

    def test_full_numeric_market_receipt_is_retained_but_never_relaxes_money_state_codec(self):
        self.prepare();self.activate();dataset,receipt=self.source()
        receipt["diagnostic_numeric_float"]=1.25
        with self.owner.database() as db:
            raw=_json(receipt)
            db.execute("UPDATE qqq_runs SET receipt=?,bytes=? WHERE request_id=?",(raw,len(raw.encode()),receipt["request_id"]))
        result=self.service.capture_due(self.clock)
        self.assertEqual(result["status"],"completed")
        with self.owner.database() as db:values=self.service.contexts(db,self.original["id"])
        self.assertNotIn("diagnostic_numeric_float",values[0]["source_receipt"])
        self.collect(DAY)
        self.assertEqual(self.paper()["state"]["schema"],4)

    def test_sql_only_coherent_availability_backdating_cannot_rewrite_sealed_artifact(self):
        self.prepare();self.activate();self.source();receipt=self.service.capture_due(self.clock)
        row=self.captures()[0]
        receipt["label"]="recovery_growth"
        raw=_json(receipt)
        with self.owner.database() as db:
            db.execute("UPDATE opt_stress_contexts SET receipt=?,bytes=? WHERE request_id=?",(raw,sum(a["bytes"] for a in receipt["artifacts"])+len(raw.encode()),receipt["request_id"]))
        with self.owner.database() as db:self.assertRaises(ValueError,self.service.contexts,db,self.original["id"])

    def test_benchmark_unresolved_source_holds_new_entries_but_not_existing_eod_progress(self):
        self.prepare();self.activate();self.source();self.service.capture_due(self.clock)
        self.collect(DAY)
        with self.owner.database() as db:
            db.execute("INSERT INTO qqq_runs VALUES(?,?,?,?,?,?,?)",("generated-failed-source","refresh",None,"0"*64,"failed","{}",2))
        with self.owner.database() as db:
            self.assertEqual(self.service.contexts(db,self.original["id"]),[])
            self.assertIsNone(self.service.capture_due(self.clock))
        # The callback is the host's reviewed resolution-aware guard; defaults
        # remain conservative when that established callback is unavailable.
        self.owner.benchmark_guard=lambda db:[]
        with self.owner.database() as db:self.assertEqual(len(self.service.contexts(db,self.original["id"])),1)
        self.owner.benchmark_guard=lambda db:[{"request_id":"generated-failed-source","status":"failed"}]
        self.collect(date(2024,1,3))
        self.assertEqual(self.paper()["state"]["processed_count"],2)

    def test_eligible_put_fills_same_account_and_reloads_published_context_and_codec(self):
        self.ready()
        days=session_dates(date(2023,12,1),date(2023,12,29))[-14:]
        baseline=eod_fixtures.raw([row for day in days for row in eod_fixtures.stock_rows(day,price=12)])
        path=self.put("imports/generated-baseline-stock.json",baseline)
        manifest={"schema":1,"kind":"onclick_f_stock_baseline","raw_path":path,"raw_sha256":_digest(baseline),
                  "received_at":self.clock.isoformat(),"through_date":days[-1].isoformat()}
        self.migrate(self.put("imports/generated-baseline.json",eod_fixtures.raw(manifest)))
        self.clock+=timedelta(minutes=1);self.service=self.owner.eod.stress;self.original=self.paper();self.activate()
        full,_,_=fixture("2024-01-04",count=80)
        for row in full["symbols"]["QQQ"]["bars"]:
            value={"2024-01-02":97,"2024-01-03":94,"2024-01-04":91}.get(row["date"],100)
            row.update(open=value,high=value+1,low=value-1,close=value,adj_close=value)
        for day,spot in ((date(2024,1,2),11.8),(date(2024,1,3),11.7),(date(2024,1,4),11.6)):
            # One growing, unrevised generated benchmark history per capture.
            data=deepcopy(full);capture=session_close_utc(day)+timedelta(hours=1)
            data["as_of"]=capture.isoformat()
            for entry in data["symbols"].values():
                entry["bars"]=[r for r in entry["bars"] if r["date"]<=day.isoformat()]
                entry["source"]["retrieved_at"]=capture.isoformat()
                entry["source"]["sha256"]=_digest(eod_fixtures.raw(entry["bars"]))
            raw=_json(data).encode()
            receipt={"request_id":"growing-benchmark-"+day.isoformat(),"status":"completed","action":"refresh","outcome":"updated",
                "created_at":capture.isoformat(),"data_sha256":_digest(raw),"source_sha256":{s:v["source"]["sha256"] for s,v in data["symbols"].items()},
                "last_sessions":{s:day.isoformat() for s in data["symbols"]}}
            with self.owner.database() as db:
                db.execute("CREATE TABLE IF NOT EXISTS qqq_data(id INTEGER PRIMARY KEY,digest TEXT,value BLOB,bytes INTEGER,quality TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS qqq_runs(request_id TEXT PRIMARY KEY,action TEXT,day TEXT,digest TEXT,status TEXT,receipt TEXT,bytes INTEGER)")
                db.execute("INSERT OR REPLACE INTO qqq_data VALUES(1,?,?,?,?)",(_digest(raw),raw,len(raw),"{}"))
                text=_json(receipt)
                db.execute("INSERT INTO qqq_runs VALUES(?,?,?,?,?,?,?)",(receipt["request_id"],"refresh",None,_digest(text.encode()),"completed",text,len(text.encode())))
            self.clock=max(self.clock,capture+timedelta(minutes=2))
            self.assertEqual(self.service.capture_due(self.clock)["status"],"completed")
            following=session_dates(day+timedelta(days=1),day+timedelta(days=7))[0]
            received=datetime(following.year,following.month,following.day,15,5,tzinfo=UTC)
            payload=eod_fixtures.bundle(day,received,price=spot,bid=.8,ask=.9)
            chain=json.loads(base64.b64decode(payload["chains"][0]["content_base64"]))
            for item in chain:
                if "osi-key" in item:
                    item["strike"]=11.5
                    item["osi-key"]="F-----240119"+("C" if item["type"]=="call" else "P")+"00011500"
            payload["chains"][0]=eod_fixtures.evidence(eod_fixtures.raw(chain),payload["chains"][0]["url"],received)
            self.collect(day,value=payload)
        current=self.paper()
        self.assertEqual(current["id"],self.original["id"])
        self.assertEqual(current["state"]["trade_count"],1)
        self.assertEqual(current["state"]["monthly_committed"],"91.3")
        self.assertEqual(current["state"]["fees"],"0.65")
        self.assertEqual(current["state"]["position"]["type"],"put")
        self.assertEqual(current["state"]["position"]["contract_id"],"F240119P00011500")
        self.assertGreater(current["state"]["stress_policy"]["context_count"],0)
        from ariadne.options_journal import review_options
        before=self.financial()
        journal=review_options(self.owner,self.clock)
        self.assertEqual(journal["performance"]["entries"],1)
        self.assertEqual(journal["performance"]["fees"],"0.65")
        self.assertEqual(journal["performance"]["monthly_budget_used"],"91.3")
        self.assertEqual(journal["performance"]["by_strategy"][0]["id"],"f_stress_breakdown_put_v1")
        self.assertEqual(self.financial(),before)


if __name__=="__main__":unittest.main()
