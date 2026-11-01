"""Generated isolated owner stores; never provider data or investment evidence."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import json
import os
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from ariadne.handoff import HandoffConflict
from ariadne.internet import SETTINGS_KEY
from ariadne.options_workflow import _json, _digest, _paper_engine
from ariadne.qqq_data import session_dates
from ariadne import options_root_eod_data as data
from ariadne import options_root_workflow as workflow
import test_options_eod_workflow as eod_fixtures
import test_options_stress_workflow as stress_fixtures
import test_brief_options_eod as brief_fixtures
import test_options_paper_readiness as readiness_fixtures
import test_vix_workflow as vix_fixtures

UTC = timezone.utc
DAY = eod_fixtures.DAY
EXPIRY = eod_fixtures.EXPIRY


def bundle(day, received, *, price=12, bid=.8, ask=.9, strike=11.5, expiry=EXPIRY):
    """Actual KO-shaped source evidence, independently of the normalized model."""
    rows = [{"warning":"only 1 strikes out of 2 strikes were returned. Upgrade to level 2 to get full chain."}]
    for kind,side in (("call","C"),("put","P")):
        rows.append({"osi-key":"KO----"+expiry.strftime("%y%m%d")+side+str(int(strike*1000)).zfill(8),
            "ticker":"KO","date":day.isoformat(),"expiration":expiry.isoformat(),"strike":strike,
            "type":kind,"bid":bid,"ask":ask,"bid_size":5,"ask_size":5})
    evidence=eod_fixtures.evidence
    raw=eod_fixtures.raw
    return {"session":day.isoformat(),
        "expirations":evidence(raw({"call":[expiry.isoformat()],"put":[expiry.isoformat()]}),data.expiration_url(day),received),
        "chains":[evidence(raw(rows),data.chain_url(day,expiry),received)],
        "stock":evidence(raw(eod_fixtures.stock_rows(day,price)),data.stock_url(day),received)}


class RootWorkflowTests(TestCase):
    body=eod_fixtures.EODWorkflowTests.body
    ready=eod_fixtures.EODWorkflowTests.ready
    paper=eod_fixtures.EODWorkflowTests.paper
    put=eod_fixtures.EODWorkflowTests.put
    source=stress_fixtures.StressWorkflowTests.source

    def setUp(self):
        eod_fixtures.EODWorkflowTests.setUp(self)
        self.addCleanup(patch.stopall)
        patch("socket.create_connection",side_effect=AssertionError("No test network")).start()
        patch("subprocess.Popen",side_effect=AssertionError("No test source worker")).start()

    def prepare(self, *, old_observation=False):
        self.ready()
        eod_fixtures.EODWorkflowTests.migrate(self)
        self.clock+=timedelta(minutes=1)
        self.owner.eod.stress.activate(self.body(acknowledge_simulated=True,acknowledge_stress_only=True),self.clock)
        if old_observation:
            eod_fixtures.EODWorkflowTests.collect(self,DAY)
            self.clock+=timedelta(minutes=1)
        self.service=self.owner.root_eod
        self.original=deepcopy(self.paper())

    def migrate(self, baseline_path=None):
        self.root_body=self.body(acknowledge_simulated=True,acknowledge_partial_eod=True,baseline_path=baseline_path)
        return self.service.migrate(self.root_body,self.clock)

    def baseline(self):
        prior=DAY-timedelta(days=1)
        days=session_dates(prior-timedelta(days=50),prior)[-15:]
        raw=eod_fixtures.raw([r for day in days for r in eod_fixtures.stock_rows(day,12)])
        path=self.put("imports/generated-ko-stock.json",raw)
        source_receipt={"number":1,"url":"https://api.onclickmedia.com/stock-data/v2/?"+urlencode({"ticker":"KO",
            "from":days[0].isoformat()+"-09-30","to":days[-1].isoformat()+"-16-00","extended":"false","bar_size":"1m"}),
            "started_at":(self.clock-timedelta(seconds=1)).isoformat(),"http_status":200,"body_cap":2*1024*1024,
            "headers":{"Content-Type":"application/json"},"received_at":self.clock.isoformat(),"bytes":len(raw),"sha256":_digest(raw)}
        receipt_raw=eod_fixtures.raw(source_receipt)
        receipt_path=self.put("imports/generated-ko-stock-receipt.json",receipt_raw)
        manifest={"schema":1,"kind":"onclick_ko_stock_baseline","raw_path":path,"raw_sha256":_digest(raw),
            "received_at":self.clock.isoformat(),"through_date":days[-1].isoformat(),
            "source_receipt_path":receipt_path,"source_receipt_sha256":_digest(receipt_raw)}
        return self.put("imports/generated-ko-baseline.json",eod_fixtures.raw(manifest))

    def collect(self, day=DAY, *, value=None, collector=None, price=12, bid=.8, ask=.9):
        following=session_dates(day+timedelta(days=1),day+timedelta(days=7))[0]
        self.clock=datetime(following.year,following.month,following.day,15,5,tzinfo=UTC)
        payload=bundle(day,self.clock,price=price,bid=bid,ask=ask) if value is None else value
        self.collect_body=self.body()
        return self.service.collect(self.collect_body,self.clock,collector=collector or (lambda **_:payload))

    def ledger(self):
        with self.owner.database() as db:
            return {table:[tuple(r) for r in db.execute("SELECT * FROM "+table)] for table in
                ("opt_accounts","opt_events","opt_eod_data","opt_eod_state","opt_eod_meta")}

    def active_raw(self):
        with self.owner.database() as db:return json.loads(db.execute("SELECT state FROM opt_accounts WHERE closed_at IS NULL").fetchone()[0])

    def database_dump(self):
        with self.owner.database() as db:return tuple(db.iterdump())

    def test_passive_uninitialized_source_does_not_create_root_tables(self):
        self.ready()
        # Only the explicit source API is inspected, without starting a source.
        with self.owner.database() as db:
            value=self.owner.root_eod.config(db)
            self.assertFalse(value["initialized"])
            self.assertFalse(self.owner.root_eod._exists(db))

    def test_migration_same_account_all_financials_and_frozen_f_cutoff(self):
        self.prepare(old_observation=True);before=self.ledger();receipt=self.migrate();after=self.paper()
        self.assertEqual(after["id"],self.original["id"])
        self.assertEqual(after["started_at"],self.original["started_at"])
        for key in ("cash","contributions","month","monthly_start_equity","monthly_committed","fees","trade_count","closed_trade_count","funding_count"):
            self.assertEqual(after["state"][key],self.original["state"][key],key)
        self.assertEqual(after["state"]["schema"],5)
        self.assertEqual(after["state"]["symbol"],"KO")
        self.assertEqual(after["state"]["source_id"],"onclick_ko_eod_v1")
        self.assertEqual(after["state"]["root_processed_count"],0)
        self.assertEqual(receipt["simulated_funding_added"],"0");self.assertFalse(receipt["monthly_budget_reset"])
        self.assertEqual(set(self.service.verify(receipt)),{"previous-state.json","initial-state.json","activation.json","baseline-source.json","baseline-manifest.json","baseline-receipt.json"})
        for table in ("opt_events","opt_eod_data","opt_eod_state","opt_eod_meta"):
            self.assertEqual(self.ledger()[table],before[table],table)
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM opt_accounts").fetchone()[0],1)
            self.owner.eod.stress.verify_account(db,after["id"],self.original["state"])
            self.service.verify_account(db,after["id"],after["state"],full=True)

    def test_baseline_ko_observations_are_separate_and_not_historical_fills(self):
        self.prepare();receipt=self.migrate(self.baseline());state=self.paper()["state"]
        self.assertEqual(len(state["history"]),15)
        self.assertTrue(all(r["underlying_close"]=="12" for r in state["history"]))
        self.assertEqual(state["trade_count"],0);self.assertEqual(state["root_processed_count"],0)
        self.assertEqual(state["cash"],self.original["state"]["cash"])
        self.assertEqual(self.service.verify(receipt)["baseline-source.json"],self.registry._path("imports/generated-ko-stock.json").read_bytes())

    def test_baseline_wrong_root_response_hash_and_received_clock_are_rejected(self):
        for kind in ("F_URL","receipt_hash","received_clock"):
            with self.subTest(kind=kind):
                self.setUp();self.prepare();path=self.baseline();before=self.ledger()
                manifest=json.loads(self.registry._path(path).read_bytes())
                original=json.loads(self.registry._path(manifest["source_receipt_path"]).read_bytes())
                if kind=="F_URL":original["url"]=original["url"].replace("ticker=KO","ticker=F")
                elif kind=="received_clock":original["received_at"]=(self.clock-timedelta(seconds=1)).isoformat()
                raw=eod_fixtures.raw(original)
                self.put(manifest["source_receipt_path"],raw)
                manifest["source_receipt_sha256"]="0"*64 if kind=="receipt_hash" else _digest(raw)
                self.put(path,eod_fixtures.raw(manifest))
                self.assertRaises(ValueError,self.migrate,path)
                self.assertEqual(self.ledger(),before);self.assertEqual(self.active_raw()["schema"],4)

    def test_baseline_parse_cache_reuses_parse_but_rehashes_reads_and_returns_copies(self):
        self.prepare();path=self.baseline()
        manifest_raw=self.registry._path(path).read_bytes();manifest=json.loads(manifest_raw)
        raw=self.registry._path(manifest["raw_path"]).read_bytes()
        receipt_raw=self.registry._path(manifest["source_receipt_path"]).read_bytes()
        with patch.object(data,"load_baseline",wraps=data.load_baseline) as parse:
            cold=self.service._baseline(manifest_raw,raw,self.clock,receipt_raw)
            expected=deepcopy(cold);cold[0]["underlying_close"]="999"
            self.assertEqual(self.service._baseline(manifest_raw,raw,self.clock,receipt_raw),expected)
            receipt=self.migrate(path)
            before=self.database_dump()
            with patch.object(self.owner,"_read",wraps=self.owner._read) as reads,patch.object(workflow,"_digest",wraps=workflow._digest) as hashes:
                for _ in range(3):self.service.verify(receipt)
            self.assertEqual(parse.call_count,1)
            for suffix in ("baseline-source.json","baseline-receipt.json"):
                self.assertEqual(sum(call.args[0].endswith(suffix) for call in reads.call_args_list),3)
            self.assertGreaterEqual(sum(call.args[0]==raw for call in hashes.call_args_list),3)
            self.assertGreaterEqual(sum(call.args[0]==receipt_raw for call in hashes.call_args_list),3)
            hit=self.service._baseline(manifest_raw,raw,self.clock,receipt_raw)
            hit[-1]["source_date"]="1900-01-01"
            self.assertEqual(self.service._baseline(manifest_raw,raw,self.clock,receipt_raw),expected)
            self.assertEqual(parse.call_count,1)
            self.assertEqual(self.database_dump(),before)

    def test_warm_baseline_cache_never_adopts_same_size_restored_mtime_artifact_changes(self):
        for name,old,new in (("baseline-source.json",b'"close":12',b'"close":13'),
                            ("baseline-receipt.json",b'"http_status":200',b'"http_status":201')):
            with self.subTest(artifact=name):
                self.setUp();self.prepare();receipt=self.migrate(self.baseline())
                self.service.verify(receipt);before=self.database_dump()
                item=next(a for a in receipt["artifacts"] if a["path"].endswith(name))
                path=self.registry._path(item["path"]);info=path.stat();raw=path.read_bytes()
                self.assertIn(old,raw);changed=raw.replace(old,new,1);self.assertEqual(len(changed),len(raw))
                path.write_bytes(changed);os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
                with patch.object(data,"load_baseline",side_effect=AssertionError("Changed artifact must reject before cached parse reuse")):
                    self.assertRaises(ValueError,self.service.verify,receipt)
                self.assertEqual(self.database_dump(),before)

    def test_baseline_prefix_and_clock_cache_keys_revalidate_new_inputs_and_full_tail(self):
        self.prepare();path=self.baseline();manifest=json.loads(self.registry._path(path).read_bytes())
        raw=self.registry._path(manifest["raw_path"]).read_bytes()
        receipt=json.loads(self.registry._path(manifest["source_receipt_path"]).read_bytes())
        receipt_raw=eod_fixtures.raw(receipt);manifest_raw=eod_fixtures.raw(manifest)
        with patch.object(data,"load_baseline",wraps=data.load_baseline) as parse:
            first=self.service._baseline(manifest_raw,raw,self.clock,receipt_raw)
            self.assertEqual(len(first),15);self.assertEqual(parse.call_count,1)
            prefix=deepcopy(manifest);prefix["through_date"]=first[-2]["source_date"]
            prefix_raw=eod_fixtures.raw(prefix)
            self.assertEqual(len(self.service._baseline(prefix_raw,raw,self.clock,receipt_raw)),14)
            self.assertEqual(parse.call_count,2)
            self.assertEqual(len(self.service._baseline(prefix_raw,raw,self.clock+timedelta(seconds=1),receipt_raw)),14)
            self.assertEqual(parse.call_count,3)
            self.assertRaises(ValueError,self.service._baseline,prefix_raw,raw,self.clock-timedelta(seconds=1),receipt_raw)
            self.assertEqual(parse.call_count,3)
            # New coherently rehashed inputs must inspect the unused fifteenth
            # day, rather than reuse a fourteen-row prefix from another body.
            rows=json.loads(raw);rows[-1]["close"]=99
            changed=eod_fixtures.raw(rows)
            updated_receipt=deepcopy(receipt);updated_receipt.update(bytes=len(changed),sha256=_digest(changed))
            updated_receipt_raw=eod_fixtures.raw(updated_receipt)
            updated_manifest=deepcopy(prefix);updated_manifest.update(raw_sha256=_digest(changed),source_receipt_sha256=_digest(updated_receipt_raw))
            with patch.object(data.base,"_stock_sessions",wraps=data.base._stock_sessions) as full_parse:
                self.assertRaises(ValueError,self.service._baseline,eod_fixtures.raw(updated_manifest),changed,self.clock,updated_receipt_raw)
                full_parse.assert_called_once()

    def test_acknowledgements_fail_before_reservation_and_no_second_source_account(self):
        self.prepare();before=self.ledger()
        body=self.body(acknowledge_simulated=True,acknowledge_partial_eod=False,baseline_path=None)
        self.assertRaises(ValueError,self.service.migrate,body,self.clock)
        self.assertEqual(self.ledger(),before)
        self.migrate();raw=self.active_raw();count=raw["contributions"]
        self.assertRaises(HandoffConflict,self.migrate)
        self.assertEqual(self.active_raw(),raw);self.assertEqual(self.active_raw()["contributions"],count)

    def test_completed_migration_replay_revalidates_evidence_and_never_refunds(self):
        self.prepare();receipt=self.migrate();before=self.ledger()
        replay=self.service.migrate(self.root_body,self.clock)
        self.assertTrue(replay["replayed"]);self.assertEqual(replay["state_sha256"],receipt["state_sha256"])
        self.assertEqual(self.ledger(),before)

    def test_migration_policy_revocation_retains_schema4_and_frozen_anchor(self):
        self.prepare();before=self.ledger();publish=self.owner._publish
        def revoked(*args,**kwargs):
            self.controls["revision"]+=1
            return publish(*args,**kwargs)
        with patch.object(self.owner,"_publish",side_effect=revoked):self.assertRaises(HandoffConflict,self.migrate)
        self.assertEqual(self.ledger(),before);self.assertEqual(self.active_raw()["schema"],4)

    def test_late_migration_artifact_tamper_prevents_state_adoption(self):
        self.prepare();before=self.ledger();verify=self.service.verify
        def tampered(receipt):
            artifact=next(a for a in receipt["artifacts"] if a["path"].endswith("previous-state.json"))
            path=self.registry._path(artifact["path"]);raw=path.read_bytes();path.write_bytes(b" "+raw[1:])
            return verify(receipt)
        with patch.object(self.service,"verify",side_effect=tampered):self.assertRaises(ValueError,self.migrate)
        self.assertEqual(self.ledger(),before)

    def test_actual_ko_snapshot_is_saved_with_identity_and_original_raw_evidence(self):
        self.prepare();self.migrate();before=self.ledger();receipt=self.collect()
        state=self.paper()["state"]
        self.assertEqual(state["schema"],5);self.assertEqual(state["root_processed_count"],1)
        self.assertEqual(state["trade_count"],0);self.assertEqual(state["cash"],"500")
        saved=self.service.verify(receipt)
        snap=json.loads(saved["snapshot.json"])
        self.assertEqual(snap["underlying_symbol"],"KO")
        self.assertTrue(all(q["contract_id"].startswith("KO") for q in snap["quotes"]))
        self.assertIsNone(snap["quote_observed_at"]);self.assertTrue(snap["partial_chain"])
        self.assertEqual(self.ledger()["opt_eod_data"],before["opt_eod_data"])
        self.assertEqual(self.ledger()["opt_eod_state"],before["opt_eod_state"])
        with self.owner.database() as db:self.service.verify_account(db,self.paper()["id"],state,full=True)

    def test_wrong_f_source_fails_with_raw_evidence_no_adoption_and_no_retry(self):
        self.prepare();self.migrate();before=self.ledger()
        following=date(2024,1,3);received=datetime(2024,1,3,15,5,tzinfo=UTC)
        self.assertRaises(ValueError,self.collect,DAY,value=eod_fixtures.bundle(DAY,received))
        self.assertEqual(self.ledger(),before)
        with self.owner.database() as db:
            row=db.execute("SELECT status,receipt FROM opt_runs WHERE action='root_collect'").fetchone()
            self.assertEqual(row["status"],"failed");receipt=json.loads(row["receipt"])
            self.assertTrue(receipt["source_failed"]);self.assertFalse(receipt["dataset_adopted"])
            self.assertEqual(receipt["fills_adopted"],0)
            self.assertEqual(db.execute("SELECT count(*) FROM opt_root_data").fetchone()[0],0)
        self.assertIsNone(self.service.run_due(self.clock+timedelta(minutes=1)))

    def test_collect_pause_or_internet_revision_revocation_never_adopts(self):
        for kind in ("pause","internet"):
            with self.subTest(kind=kind):
                self.setUp();self.prepare();self.migrate();before=self.ledger()
                def revoke(**kwargs):
                    payload=bundle(kwargs["expected_session"],kwargs["now"])
                    if kind=="pause":self.store.set_setting("paused",True)
                    else:self.store.set_setting(SETTINGS_KEY,{"enabled":True,"revision":2})
                    return payload
                self.assertRaises(HandoffConflict,self.collect,collector=revoke)
                self.assertEqual(self.ledger(),before)

    def test_disable_source_preserves_capital_and_suppresses_automatic_collection(self):
        self.prepare();self.migrate();state=self.active_raw()
        receipt=self.service.configure(self.body(enabled=False,acknowledge_source=True),self.clock)
        self.assertEqual(receipt["simulated_funding_added"],"0");self.assertFalse(receipt["monthly_budget_reset"])
        self.assertEqual(self.active_raw(),state)
        self.assertIsNone(self.service.run_due(datetime(2024,1,3,15,5,tzinfo=UTC)))

    def test_only_selected_ko_source_is_dispatched_not_old_f_collector(self):
        self.prepare();self.migrate();sentinel={"generated":"selected-root"}
        with patch.object(self.owner.eod.stress,"capture_due",return_value=None),patch.object(self.service,"run_due",return_value=sentinel) as root_run,patch.object(self.owner.eod,"run_due",side_effect=AssertionError("No parallel F collector")),patch.object(self.owner.feed,"run_due",side_effect=AssertionError("No legacy feed")):
            self.assertEqual(self.owner.collect_due(self.clock),sentinel);root_run.assert_called_once_with(self.clock)
        self.assertEqual(_paper_engine(self.active_raw()).RULE["schema"],5)

    def test_frozen_f_anchor_and_accepted_ko_cache_tampering_are_integrity_failures(self):
        for table in ("opt_eod_state","opt_root_data"):
            with self.subTest(table=table):
                self.setUp();self.prepare();self.migrate();self.collect()
                with self.owner.database() as db:
                    if table=="opt_eod_state":db.execute("UPDATE opt_eod_state SET state_sha256=?",("0"*64,))
                    else:db.execute("UPDATE opt_root_data SET digest=?",("0"*64,))
                self.assertRaises(ValueError,self.paper)
                from ariadne.options_journal import review_options
                self.assertRaises(ValueError,review_options,self.owner,self.clock)

    def test_full_f_cutoff_artifact_rehash_stops_ko_mutation_and_journal(self):
        self.prepare(old_observation=True);self.migrate();before=self.ledger()
        with self.owner.database() as db:
            receipt=json.loads(db.execute("SELECT receipt FROM opt_runs WHERE action='eod_collect' AND status='completed'").fetchone()[0])
        artifact=next(a for a in receipt["artifacts"] if a["path"].endswith("bundle.json"))
        path=self.registry._path(artifact["path"]);info=path.stat();raw=path.read_bytes()
        path.write_bytes(b" "+raw[1:]);os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
        collector=Mock(side_effect=AssertionError("No new source after old evidence tamper"))
        self.assertRaises(ValueError,self.collect,date(2024,1,3),collector=collector)
        collector.assert_not_called();self.assertEqual(self.ledger(),before)
        from ariadne.options_journal import review_options
        self.assertRaises(ValueError,review_options,self.owner,self.clock)

    def test_coherent_empty_saved_context_ring_cannot_seal_a_different_allowed_gate(self):
        self.prepare();self.migrate(self.baseline());self.source(last=DAY.isoformat())
        self.assertEqual(self.owner.eod.stress.capture_due(self.clock)["status"],"completed")
        before=self.ledger();publish=self.owner._publish
        def replaced(cfg,brief,receipt,files,**kwargs):
            self.assertEqual(receipt["action"],"root_collect")
            files=tuple((name,b"[]" if name=="contexts.json" else raw) for name,raw in files)
            receipt["contexts_sha256"]=_digest(b"[]")
            # All publication metadata is coherent, but the engine's saved
            # allowed gate cannot have come from this deliberately empty ring.
            return publish(cfg,brief,receipt,files,**kwargs)
        with patch.object(self.owner,"_publish",side_effect=replaced):
            self.assertRaisesRegex(ValueError,"benchmark input/query",self.collect,DAY,price=11.8)
        self.assertEqual(self.ledger(),before)
        with self.owner.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM opt_root_data").fetchone()[0],0)

    def test_late_same_length_restored_mtime_source_tamper_cannot_commit(self):
        self.prepare();self.migrate();before=self.ledger();publish=self.owner._publish
        def tampered(*args,**kwargs):
            final=kwargs["final"]
            def mutate(db):
                receipt=args[2];artifact=next(a for a in receipt["artifacts"] if a["path"].endswith("bundle.json"))
                path=self.registry._path(artifact["path"]);info=path.stat();raw=path.read_bytes()
                path.write_bytes(b" "+raw[1:]);os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
                return final(db)
            kwargs["final"]=mutate
            return publish(*args,**kwargs)
        with patch.object(self.owner,"_publish",side_effect=tampered):self.assertRaises(ValueError,self.collect)
        self.assertEqual(self.ledger(),before)
        with self.owner.database() as db:self.assertEqual(db.execute("SELECT status FROM opt_runs WHERE action='root_collect'").fetchone()[0],"failed")

    def test_schema5_daily_brief_keeps_ko_source_caps_and_no_forward_result_fabrication(self):
        self.prepare();self.migrate();self.collect()
        from ariadne.briefing import options_eod_brief_view
        from ariadne.briefing_summary import compact_summary
        options=self.owner.status(self.clock);view=options_eod_brief_view(options)
        self.assertEqual(view["schema"],5);self.assertEqual(view["symbol"],"KO")
        self.assertEqual(view["source_id"],"onclick_ko_eod_v1")
        self.assertEqual(view["entry_all_in_cap"],"100.00");self.assertEqual(view["monthly_cap"],"500.00")
        self.assertIsNone(view["quote_observed_at"]);self.assertFalse(view["execution_qualified"])
        report=brief_fixtures.fixture();report["options_paper"]=options
        compact=compact_summary(report,self.clock)["options_paper"]
        self.assertEqual(compact["symbol"],"KO");self.assertEqual(compact["contributions"],"500")
        text=brief_fixtures.rendered(report)
        self.assertIn("Active KO source: onclick_ko_eod_v1",text)
        self.assertIn("All-in entry ceiling: $100",text)

    def test_real_generated_stress_put_flows_through_collect_and_verified_reload(self):
        self.prepare();self.migrate(self.baseline())
        for day,price in ((DAY,11.8),(date(2024,1,3),11.7),(date(2024,1,4),11.6)):
            self.source(last=day.isoformat())
            capture=self.owner.eod.stress.capture_due(self.clock)
            self.assertEqual(capture["status"],"completed")
            self.collect(day,price=price)
        account=self.paper();state=account["state"]
        self.assertEqual(state["trade_count"],1);self.assertEqual(state["monthly_committed"],"91.3")
        self.assertEqual(state["fees"],"0.65");self.assertEqual(state["position"]["contract_id"],"KO240119P00011500")
        self.assertEqual(state["position"]["quantity"],1);self.assertEqual(state["position"]["multiplier"],100)
        self.assertEqual(state["position"]["budget_debit"],"91.3")
        self.assertEqual(state["contributions"],"500")
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM opt_accounts").fetchone()[0],1)
            self.service.verify_account(db,account["id"],state,full=True)

    def test_missing_new_benchmark_never_intercepts_later_sale_or_reenters_same_frame(self):
        self.prepare();self.migrate(self.baseline())
        for day,price in ((DAY,11.8),(date(2024,1,3),11.7),(date(2024,1,4),11.6)):
            self.source(last=day.isoformat());self.owner.eod.stress.capture_due(self.clock);self.collect(day,price=price)
        self.assertIsNotNone(self.paper()["state"]["position"])
        with patch.object(self.owner.eod.stress,"contexts",return_value=[]):
            for day in (date(2024,1,5),date(2024,1,8),date(2024,1,9)):
                self.collect(day,price=11.5,bid=4.6,ask=4.7)
        account=self.paper();state=account["state"]
        self.assertIsNone(state["position"]);self.assertIsNone(state["pending"])
        self.assertEqual(state["trade_count"],1);self.assertEqual(state["closed_trade_count"],1)
        self.assertEqual(state["fees"],"1.3");self.assertEqual(state["monthly_committed"],"91.3")
        with self.owner.database() as db:self.service.verify_account(db,account["id"],state,full=True)
        # Read the sealed BUY/SELL evidence; never run the trading engine again.
        from ariadne.options_journal import review_options
        from ariadne import options_root_eod as engine
        before=self.database_dump()
        with patch.object(engine,"advance_paper",side_effect=AssertionError("Journal cannot replay financial work")):
            first=review_options(self.owner,self.clock)
            second=review_options(self.owner,self.clock)
            with localcontext() as decimal_context:
                decimal_context.prec=3;decimal_context.rounding=ROUND_UP;decimal_context.traps[Inexact]=True
                hostile=review_options(self.owner,self.clock)
                hostile_readiness=self.owner.status(self.clock)["paper_readiness"]
        self.assertEqual(first,second);self.assertEqual(first,hostile)
        self.assertEqual(first["profile"],"experimental_root_stress_put_eod_paper")
        metrics=first["performance"]
        self.assertEqual((metrics["entries"],metrics["closed_trades"],metrics["wins"],metrics["losses"]),(1,1,1,0))
        self.assertEqual(Decimal(metrics["fees"]),Decimal("1.30"))
        self.assertEqual(Decimal(metrics["trading_pnl"]),Decimal("368.70"))
        self.assertEqual(Decimal(metrics["by_strategy"][0]["realized_pnl"]),Decimal("368.70"))
        self.assertEqual(Decimal(metrics["monthly_budget_used"]),Decimal("91.30"))
        self.assertEqual(Decimal(hostile_readiness["shared_risk"]["monthly_remaining"]),Decimal("408.70"))
        self.assertEqual(metrics["sample_status"],"insufficient_closed_proxy_sample")
        self.assertFalse(first["execution_qualified"]);self.assertEqual(first["network_calls"],0);self.assertEqual(first["model_calls"],0)
        self.assertEqual(self.database_dump(),before)

    def test_readiness_describes_only_verified_ko_and_cannot_select_declared_f(self):
        self.prepare();self.migrate()
        index=readiness_fixtures.index(readiness_fixtures.root("F"),readiness_fixtures.root("KO"))
        self.put("market-data/options/paper-readiness.json",_json(index).encode())
        before=self.database_dump();value=self.owner.status(self.clock)["paper_readiness"]
        self.assertEqual(value["configured_active_paper_roots"],["KO"])
        rows={r["symbol"]:r for r in value["roots"]}
        self.assertFalse(rows["F"]["configured_active_paper_root"])
        self.assertTrue(rows["KO"]["configured_active_paper_root"])
        self.assertFalse(rows["KO"]["current_eligibility_established"])
        self.assertFalse(value["entry_authorized"]);self.assertFalse(rows["KO"]["entry_authorized"])
        risk=value["shared_risk"]
        self.assertEqual(risk["monthly_gross_commitment_cap"],"500");self.assertEqual(risk["all_in_entry_cap"],"100")
        self.assertEqual(risk["new_funding"],"0");self.assertFalse(risk["monthly_budget_reset"])
        self.assertEqual(value["active_paper"]["account_id"],self.original["id"])
        self.assertEqual(self.database_dump(),before)

    def test_local_vix_signal_research_preserves_the_ko_account_caps_and_source_anchors(self):
        self.prepare();self.migrate();self.clock=datetime(2024,2,12,21,tzinfo=UTC)
        path,_=vix_fixtures.VixWorkflowTests.history(self)
        before=self.ledger();paper=deepcopy(self.paper())
        with self.owner.database() as db:
            root_before={table:[tuple(r) for r in db.execute("SELECT * FROM "+table)] for table in
                ("opt_root_state","opt_root_meta","opt_root_data","opt_root_migrations")}
        receipt=self.owner.vix.signals(self.body(path=path),self.clock)
        self.assertEqual(receipt["status"],"completed");self.assertEqual(receipt["action"],"vix_signals")
        self.assertEqual(receipt["active_funding_added"],"0");self.assertFalse(receipt["paper_state_changed"])
        self.assertFalse(receipt["execution_qualified"]);self.assertEqual(receipt["model_calls"],0)
        self.assertEqual(self.paper(),paper);self.assertEqual(self.ledger(),before)
        with self.owner.database() as db:
            root_after={table:[tuple(r) for r in db.execute("SELECT * FROM "+table)] for table in root_before}
        self.assertEqual(root_after,root_before)
        status=self.owner.status(self.clock)
        self.assertEqual(status["vix"]["shared_account_id"],paper["id"])
        self.assertEqual(status["vix"]["shared_monthly_limit"],"500.00")
        self.assertEqual(Decimal(status["vix"]["shared_monthly_remaining"]),Decimal("500"))
        self.assertFalse(status["vix"]["paper_fills_enabled"])
        self.assertEqual(status["paper_readiness"]["configured_active_paper_roots"],["KO"])

    def test_research_reservations_remain_available_but_legacy_financial_actions_are_denied(self):
        self.prepare();self.migrate();before=self.database_dump()
        for action in ("import","vix_signals","vix_backtest"):
            with self.subTest(allowed_research=action):
                body=self.body(path="imports/generated-research-only.json")
                with self.owner.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    count=db.execute("SELECT count(*) FROM opt_runs").fetchone()[0]
                    self.assertIsNone(self.owner._reserve(db,action,body,None,self.clock))
                    row=db.execute("SELECT action,status FROM opt_runs WHERE request_id=?",(body["request_id"],)).fetchone()
                    self.assertEqual(tuple(row),(action,"running"))
                    self.assertEqual(db.execute("SELECT count(*) FROM opt_runs").fetchone()[0],count+1)
                    # These are reservation-contract probes, never published or
                    # uncertain real jobs. Discard each generated transaction.
                    db.rollback()
                self.assertEqual(self.database_dump(),before)
        for action in ("refresh","feed_config","feed_collect","paper","portfolio","eod_migrate","eod_config","eod_collect","stress_activate","archive"):
            with self.subTest(denied_financial_or_old_source=action):
                body=self.body()
                with self.owner.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    count=db.execute("SELECT count(*) FROM opt_runs").fetchone()[0]
                    revision=self.owner._meta(db)[0]
                    self.assertRaises(HandoffConflict,self.owner._reserve,db,action,body,None,self.clock)
                    self.assertFalse(db.execute("SELECT 1 FROM opt_runs WHERE request_id=?",(body["request_id"],)).fetchone())
                    self.assertEqual(db.execute("SELECT count(*) FROM opt_runs").fetchone()[0],count)
                    self.assertEqual(self.owner._meta(db)[0],revision)
                    db.rollback()
                self.assertEqual(self.database_dump(),before)

    def test_malformed_optional_inventory_is_diagnostic_and_does_not_hold_source(self):
        self.prepare();self.migrate();self.put("market-data/options/paper-readiness.json",b'{"schema":1,"schema":1,"roots":[]}')
        before=self.database_dump();value=self.owner.status(self.clock)
        self.assertEqual(value["paper_readiness"]["diagnostic_status"],"unavailable")
        self.assertEqual(value["paper_readiness"]["configured_active_paper_roots"],["KO"])
        self.assertIsNone(value["hold"]);self.assertIsNone(self.active_raw()["hold"])
        self.assertEqual(self.database_dump(),before)
        receipt=self.collect()
        self.assertTrue(receipt["accepted_snapshot"]);self.assertEqual(self.paper()["state"]["root_processed_count"],1)
        self.assertIsNone(self.active_raw()["hold"])

    def test_failed_or_interrupted_root_receipts_are_never_financial_performance(self):
        from ariadne.options_journal import review_options
        for kind in ("failed","running"):
            with self.subTest(kind=kind):
                self.setUp();self.prepare();self.migrate()
                if kind=="failed":
                    received=datetime(2024,1,3,15,5,tzinfo=UTC)
                    self.assertRaises(ValueError,self.collect,DAY,value=eod_fixtures.bundle(DAY,received))
                else:
                    receipt={"request_id":"generated-root-interrupted","action":"root_collect","status":"running",
                        "created_at":self.clock.isoformat(),"artifacts":[],"reason":"Generated interrupted reservation"}
                    raw=_json(receipt)
                    with self.owner.database() as db:db.execute("INSERT INTO opt_runs VALUES(?,?,?,?,?,?,?)",(receipt["request_id"],"root_collect",None,_digest(raw.encode()),"running",raw,len(raw.encode())))
                before=self.database_dump();result=review_options(self.owner,self.clock)
                self.assertEqual(result["status"],"held")
                self.assertEqual(result["performance"]["entries"],0);self.assertEqual(result["performance"]["closed_trades"],0)
                self.assertEqual(Decimal(result["performance"]["trading_pnl"]),0)
                self.assertTrue(result["failed_checks"])
                self.assertTrue(all(r["adopted_as_performance"] is False for r in result["failed_checks"]))
                self.assertEqual(self.database_dump(),before)
