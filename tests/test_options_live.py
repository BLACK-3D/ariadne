"""Isolated feed authority, exact-account migration and delayed-paper integration."""
import base64
from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.handoff import HandoffConflict
from ariadne.internet import SETTINGS_KEY
from ariadne.options_data import canonical,contract_id,validate_dataset
from ariadne.options_feed import normalize_chain,source_url
from ariadne.options_live import retarget_pristine
from ariadne.options_portfolio import initial_state,advance_paper,RULE_SHA256
from ariadne.options_workflow import OptionsWorkflow,INBOX
from ariadne.qqq_data import session_close_utc,session_dates
from ariadne.store import Store
from ariadne.tools import ToolRegistry

UTC=timezone.utc
OPEN=datetime(2024,1,2,14,35,tzinfo=UTC)


def source_payload(session,*,expiry=date(2024,1,31),missing_call=False,crossed_call=False,crossed_put=False):
    kinds=["put"] if missing_call else ["call","put"]
    return canonical({"s":"ok","optionSymbol":[contract_id("AAPL",expiry.isoformat(),kind,"103" if kind=="call" else "100") for kind in kinds],
        "underlying":["AAPL"]*len(kinds),"expiration":[int(session_close_utc(expiry).timestamp())]*len(kinds),"side":kinds,
        "strike":[103 if kind=="call" else 100 for kind in kinds],"updated":[int(session_close_utc(session).timestamp())]*len(kinds),
        "bid":[2 if kind=="call" and crossed_call or kind=="put" and crossed_put else 1 for kind in kinds],
        "ask":[1.1]*len(kinds),"bidSize":[10]*len(kinds),"askSize":[10]*len(kinds),"underlyingPrice":[101]*len(kinds),"volume":[10]*len(kinds),"openInterest":[50]*len(kinds)})


def bundle(session,received,*,raw=None,required=()):
    raw=source_payload(session) if raw is None else raw
    normalized=normalize_chain(raw,retrieved_at=received,expected_session=session,required_contracts=required)
    return {**normalized,"schema":1,"network_requests":1,"raw":{"url":source_url(session),"content_base64":base64.b64encode(raw).decode(),
        "sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw),"retrieved_at":received.isoformat(),"http_status":203}}


class OptionsLiveTests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/"runtime/tmp";parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="options-live-",dir=parent);self.addCleanup(temporary.cleanup)
        self.settings=Settings(data_dir=Path(temporary.name)/"data")
        self.store=Store(self.settings.data_dir/"ariadne.sqlite3");self.store.set_setting("paused",False)
        self.store.set_setting(SETTINGS_KEY,{"enabled":True,"revision":1})
        self.registry=ToolRegistry(self.settings)
        self.controls={"enabled":True,"paused":False,"revision":1}
        self.service=OptionsWorkflow(self.registry,self.store,controls=lambda:dict(self.controls))
        self.clock=OPEN;self.counter=0

    def body(self,**extra):
        self.counter+=1
        with self.service.database() as db:revision=self.service._meta(db)[0]
        return {"request_id":"feed-test-"+str(self.counter),"expected_revision":revision,**extra}

    def ready(self,*,legacy=False):
        self.service.configure({"enabled":True,"paused":False,"expected_revision":0},self.clock-timedelta(minutes=2))
        if legacy:
            self.service.start_paper(self.body(acknowledge_simulated=True),self.clock-timedelta(minutes=1))
            self.service.upgrade_portfolio(self.body(acknowledge_simulated=True),self.clock)
        else:self.service.start_portfolio(self.body(acknowledge_simulated=True),self.clock)
        self.before=self.service.status(self.clock)["paper"]
        self.clock+=timedelta(seconds=1)
        return self.service.feed.configure(self.body(enabled=True,acknowledge_feed=True),self.clock)

    def collect(self,session=date(2024,1,2),*,collector=None):
        next_day=session_dates(session+timedelta(days=1),session+timedelta(days=7))[0]
        self.clock=datetime(next_day.year,next_day.month,next_day.day,14,35,tzinfo=UTC)
        def fixed(**kwargs):
            kwargs["check"]()
            return bundle(session,self.clock,required=kwargs["required_contracts"])
        self.last_collect_body=self.body()
        return self.service.feed.collect(self.last_collect_body,self.clock,collector=collector or fixed)

    def paper(self):return self.service.status(self.clock)["paper"]

    def seed_funded_state(self):
        """A validated synthetic funded state; only this private test database."""
        from test_options_recovery import fixture
        data=fixture(21,historical=True)
        for row in data["snapshots"]:
            row["underlying_symbol"]="AAPL"
            for quote in row["quotes"]:quote["contract_id"]="AAPL"+quote["contract_id"][3:]
            day=datetime.fromisoformat(row["observed_at"]).date()
            next_day=session_dates(day+timedelta(days=1),day+timedelta(days=7))[0]
            row["available_at"]=datetime(next_day.year,next_day.month,next_day.day,14,35,tzinfo=UTC).isoformat()
        state=self.paper()["state"]
        for count in (13,14,15):
            current=deepcopy(data);current["snapshots"]=current["snapshots"][:count]
            current["as_of"]=current["source"]["retrieved_at"]=current["snapshots"][-1]["available_at"]
            if count==15:
                actual=bundle(date(2024,1,23),datetime.fromisoformat(current["as_of"]))
                current["source"]=deepcopy(actual["dataset"]["source"])
            result=advance_paper(current,state,decided_at=current["as_of"])
            self.assertEqual(result["status"],"completed");state=result["state"]
        self.assertIsNotNone(state["position"])
        self.clock=datetime.fromisoformat(current["as_of"])
        raw=canonical(current);quality=validate_dataset(current,self.clock)
        quality.update(as_of=current["as_of"],provider=current["source"]["provider"],source_url=current["source"]["url"],retrieved_at=current["source"]["retrieved_at"])
        with self.service.database() as db:
            db.execute("UPDATE opt_accounts SET state=? WHERE closed_at IS NULL",(canonical(state).decode(),))
            db.execute("INSERT OR REPLACE INTO opt_data VALUES(?,?,?,?,?)",(2,hashlib.sha256(raw).hexdigest(),raw,len(raw),canonical(quality).decode()))
        return state

    def test_passive_feed_status_is_disabled_without_seeding_or_workers(self):
        with patch("socket.create_connection",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No worker")):
            value=self.service.status(self.clock)
        self.assertFalse(value["feed"]["config"]["initialized"])
        with self.service.database() as db:self.assertFalse(self.service._exists(db))

    def test_exact_account_and_funding_survive_feed_retarget_and_restart(self):
        receipt=self.ready(legacy=True);before=self.before
        value=self.paper();state=value["state"]
        self.assertEqual(value["id"],before["id"])
        self.assertEqual(state["symbol"],"AAPL")
        for key in ("started_at","portfolio_activation_at","migration","month","cash","contributions","monthly_start_equity","monthly_committed","fees","rule_sha256"):
            self.assertEqual(state[key],before["state"][key],key)
        self.assertEqual(receipt["simulated_funding_added"],"0");self.assertIs(receipt["monthly_budget_reset"],False)
        with self.service.database() as db:
            witness=dict(db.execute("SELECT * FROM opt_feed_activation").fetchone())
            self.assertEqual(witness["old_sha256"],hashlib.sha256(witness["old_state"].encode()).hexdigest())
            self.assertEqual(json.loads(witness["old_state"]),before["state"])
            self.assertEqual(db.execute("SELECT count(*) FROM opt_migrations").fetchone()[0],1)
        self.service=OptionsWorkflow(self.registry,self.store,controls=lambda:dict(self.controls))
        self.assertEqual(self.paper(),value)

    def test_configuration_replay_cannot_add_funding_or_a_second_witness(self):
        self.ready();body=self.body(enabled=False,acknowledge_feed=True)
        first=self.service.feed.configure(body,self.clock)
        replay=self.service.feed.configure(body,self.clock)
        self.assertTrue(replay["replayed"]);self.assertEqual(first["account_id"],replay["account_id"])
        with self.service.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM opt_feed_activation").fetchone()[0],1)
        self.assertEqual(self.paper()["state"]["contributions"],"500")

    def test_retarget_rejects_nonpristine_or_changed_rule_and_backward_clock(self):
        state=initial_state(started_at=OPEN.isoformat())
        for mutate in (lambda s:s.update(symbol="AAPL"),lambda s:s.update(next_strategy=True),lambda s:s.update(cap_qualified=False),lambda s:s.update(cash="1000"),lambda s:s.update(contributions="1000")):
            changed=deepcopy(state);mutate(changed)
            with self.assertRaises(ValueError):retarget_pristine(changed,activated_at=OPEN.isoformat())
        with self.assertRaises(ValueError):retarget_pristine(state,activated_at=(OPEN-timedelta(seconds=1)).isoformat())
        self.assertEqual(state["symbol"],"QQQ")

    def test_arbitrary_prior_events_prevent_source_activation(self):
        self.service.configure({"enabled":True,"paused":False,"expected_revision":0},self.clock)
        self.service.start_portfolio(self.body(acknowledge_simulated=True),self.clock)
        before=self.paper()
        with self.service.database() as db:db.execute("INSERT INTO opt_events(account_id,run_id,value) VALUES(?,?,?)",(before["id"],"synthetic",'{"type":"baseline"}'))
        with self.assertRaises(HandoffConflict):self.service.feed.configure(self.body(enabled=True,acknowledge_feed=True),self.clock)
        self.assertEqual(self.paper(),before)

    def test_existing_forward_basis_prevents_silent_provider_change(self):
        self.service.configure({"enabled":True,"paused":False,"expected_revision":0},self.clock)
        self.service.start_portfolio(self.body(acknowledge_simulated=True),self.clock)
        from test_options_recovery import fixture
        data=fixture(1,historical=True);raw=canonical(data)
        with self.service.database() as db:db.execute("INSERT INTO opt_data VALUES(?,?,?,?,?)",(2,hashlib.sha256(raw).hexdigest(),raw,len(raw),"{}"))
        with self.assertRaises(HandoffConflict):self.service.feed.configure(self.body(enabled=True,acknowledge_feed=True),self.clock)
        with self.service.database() as db:self.assertFalse(self.service.feed.config(db)["initialized"])

    def test_missing_or_changed_activation_witness_blocks_reads(self):
        self.ready()
        with self.service.database() as db:db.execute("UPDATE opt_feed_activation SET old_sha256=?",("0"*64,))
        with self.assertRaises(ValueError):self.paper()

    def test_changed_target_or_actual_activation_cannot_borrow_the_witness(self):
        self.ready();value=self.paper()["state"]
        for changed in ({**value,"symbol":"QQQ"},{**value,"last_decided_at":value["started_at"]}):
            with self.service.database() as db:db.execute("UPDATE opt_accounts SET state=?",(canonical(changed).decode(),))
            with self.assertRaises(ValueError):self.paper()
        with self.service.database() as db:db.execute("UPDATE opt_accounts SET state=?",(canonical(value).decode(),))

    def test_first_capture_is_recorded_warmup_not_an_adopted_historical_trade(self):
        self.ready();result=self.collect();value=self.paper()
        self.assertEqual(result["status"],"completed");self.assertEqual(result["paper_status"],"completed")
        self.assertEqual(value["state"]["processed_count"],1)
        self.assertEqual(value["state"]["trade_count"],0);self.assertEqual(value["state"]["cash"],"500")
        self.assertIsNone(value["state"]["last_daily_result"])
        self.assertEqual(self.service.status(self.clock)["forward_quality"]["quote_time_basis"],"recorded")
        self.assertFalse(self.service.status(self.clock)["brokerage_connected"])

    def test_one_source_observation_per_session_and_exact_replay_without_worker(self):
        self.ready();self.clock=datetime(2024,1,3,14,35,tzinfo=UTC);body=self.body()
        calls=[]
        def fixed(**kwargs):calls.append(kwargs["required_contracts"]);return bundle(date(2024,1,2),self.clock)
        first=self.service.feed.collect(body,self.clock,collector=fixed)
        with patch.object(self.service.feed,"_collect",side_effect=AssertionError("No replay request")):
            replay=self.service.feed.collect(body,self.clock)
        self.assertTrue(replay["replayed"]);self.assertEqual(len(calls),1)
        with self.assertRaises(HandoffConflict):self.service.feed.collect(self.body(),self.clock,collector=fixed)
        self.assertEqual(len(calls),1);self.assertEqual(first["data_sha256"],replay["data_sha256"])

    def test_capture_artifacts_are_bound_and_tampered_replay_does_not_regenerate(self):
        self.ready();result=self.collect();artifact=next(item for item in result["artifacts"] if item["path"].endswith("source.json"))
        path=self.settings.workspace/artifact["path"];self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),result["source_sha256"])
        path.write_bytes(b"changed")
        with patch.object(self.service.feed,"_collect",side_effect=AssertionError("No replay request")):
            with self.assertRaises(ValueError):
                self.service.feed.collect(self.last_collect_body,self.clock)
        self.assertEqual(path.read_bytes(),b"changed")

    def test_six_am_legacy_inbox_is_skipped_even_while_feed_is_disabled(self):
        self.ready();self.service.feed.configure(self.body(enabled=False,acknowledge_feed=True),self.clock)
        before=self.paper()
        with patch.object(self.service,"refresh",side_effect=AssertionError("No premature source refresh")):
            self.assertIsNone(self.service.run_due(datetime(2024,1,3,12,tzinfo=UTC)))
        self.assertEqual(self.paper(),before)

    def test_source_due_window_rejects_preopen_weekend_and_near_close_without_request(self):
        self.ready()
        times=(datetime(2024,1,3,12,tzinfo=UTC),datetime(2024,1,6,15,tzinfo=UTC),datetime(2024,1,3,20,55,tzinfo=UTC))
        with patch.object(self.service.feed,"_collect",side_effect=AssertionError("No outside-window request")):
            for clock in times:
                self.assertIsNone(self.service.feed.run_due(clock))
                with self.assertRaises(HandoffConflict):self.service.feed.collect(self.body(),clock)

    def test_source_window_edges_use_bundled_dst_and_leave_five_minutes_before_close(self):
        window=self.service.feed._window
        for before,ready in (("2024-01-03T14:34:59+00:00","2024-01-03T14:35:00+00:00"),("2024-03-11T13:34:59+00:00","2024-03-11T13:35:00+00:00")):
            self.assertFalse(window(datetime.fromisoformat(before))[0])
            self.assertTrue(window(datetime.fromisoformat(ready))[0])
        for day in (date(2024,1,3),date(2024,7,3),date(2024,11,29)):
            close=session_close_utc(day)
            self.assertTrue(window(close-timedelta(minutes=5,seconds=1))[0],str(day))
            self.assertFalse(window(close-timedelta(minutes=5))[0],str(day))
            self.assertFalse(window(close-timedelta(seconds=61))[0],str(day))
            self.assertGreater((close-(close-timedelta(minutes=5,seconds=1)+timedelta(seconds=35))).total_seconds(),60)
        self.assertFalse(window(datetime(2024,7,4,14,tzinfo=UTC))[0])
        self.assertEqual(window(datetime(2024,7,5,13,35,tzinfo=UTC))[1],date(2024,7,3))

    def test_global_local_internet_brief_and_stored_holds_block_due_collection(self):
        self.ready();clock=datetime(2024,1,3,14,35,tzinfo=UTC)
        with patch.object(self.service.feed,"collect",side_effect=AssertionError("No unauthorized request")):
            self.store.set_setting("paused",True);self.assertIsNone(self.service.feed.run_due(clock));self.store.set_setting("paused",False)
            self.store.set_setting(SETTINGS_KEY,{"enabled":False,"revision":2});self.assertIsNone(self.service.feed.run_due(clock));self.store.set_setting(SETTINGS_KEY,{"enabled":True,"revision":3})
            for field in ("paused","execution_hold"):
                self.controls[field]=True;self.assertIsNone(self.service.feed.run_due(clock))
                if field=="paused":self.controls[field]=False
                else:self.controls.pop(field)
            with self.service.database() as db:
                config=json.loads(db.execute("SELECT config FROM opt_meta").fetchone()[0]);config["paused"]=True
                db.execute("UPDATE opt_meta SET config=?",(canonical(config).decode(),))
            self.assertIsNone(self.service.feed.run_due(clock))
            with self.service.database() as db:
                config["paused"]=False;db.execute("UPDATE opt_meta SET config=?",(canonical(config).decode(),))
                state=json.loads(db.execute("SELECT state FROM opt_accounts WHERE closed_at IS NULL").fetchone()[0])
                state.update(hold={"code":"inspected_fixture_hold","message":"Stored paper hold must remain binding","observed_at":clock.isoformat()},cap_qualified=False)
                db.execute("UPDATE opt_accounts SET state=?",(canonical(state).decode(),))
            self.assertIsNone(self.service.feed.run_due(clock))

    def test_mid_collection_internet_revocation_preserves_cash_and_failed_hold(self):
        self.ready();before=self.paper()
        def revoked(**kwargs):
            value=bundle(date(2024,1,2),self.clock)
            self.store.set_setting(SETTINGS_KEY,{"enabled":False,"revision":2})
            kwargs["check"]();return value
        with self.assertRaises(HandoffConflict):self.collect(collector=revoked)
        self.assertEqual(self.paper(),before)
        self.assertEqual(self.service.status(self.clock)["hold"]["status"],"failed")
        self.store.set_setting(SETTINGS_KEY,{"enabled":True,"revision":3})
        with patch.object(self.service.feed,"collect",side_effect=AssertionError("No retry")):self.assertIsNone(self.service.feed.run_due(self.clock))

    def test_parent_revalidates_worker_dataset_and_immutable_raw_identity(self):
        self.ready();before=self.paper()
        def changed(**kwargs):
            value=bundle(date(2024,1,2),self.clock);value["dataset"]["snapshots"][0]["quotes"][0]["ask"]="0.5";return value
        with self.assertRaises(ValueError):self.collect(collector=changed)
        self.assertEqual(self.paper(),before);self.assertIsNotNone(self.service.status(self.clock)["hold"])

    def test_nonrequired_crossed_quotes_have_retained_exact_quarantine_evidence(self):
        self.ready();raw=source_payload(date(2024,1,2),crossed_put=True)
        def fixed(**kwargs):return bundle(date(2024,1,2),self.clock,raw=raw,required=kwargs["required_contracts"])
        result=self.collect(collector=fixed)
        self.assertEqual(result["status"],"completed")
        provenance_artifact=next(item for item in result["artifacts"] if item["path"].endswith("provenance.json"))
        provenance=json.loads((self.settings.workspace/provenance_artifact["path"]).read_bytes())
        self.assertEqual(provenance["quarantined_rows"],1)
        self.assertEqual(provenance["quarantined"],[{"contract_id":"AAPL240131P00100000","bid":"2","ask":"1.1","reason":"crossed_bid_greater_than_ask"}])
        self.assertEqual(len(provenance["normalization_policy_sha256"]),64)
        source_artifact=next(item for item in result["artifacts"] if item["path"].endswith("source.json"))
        self.assertEqual((self.settings.workspace/source_artifact["path"]).read_bytes(),raw)
        with self.service.database() as db:dataset=self.service._data(db,forward=True)["dataset"]
        self.assertEqual([item["type"] for item in dataset["snapshots"][0]["quotes"]],["call"])
        self.assertIn(provenance["normalization_policy_sha256"],dataset["source"]["usage"])

    def test_child_cannot_hide_or_forge_quarantine_provenance(self):
        self.ready();before=self.paper()
        def forged(**kwargs):
            value=bundle(date(2024,1,2),self.clock,raw=source_payload(date(2024,1,2),crossed_put=True))
            value["provenance"]["quarantined"]=[];value["provenance"]["quarantined_rows"]=0
            return value
        with self.assertRaises(ValueError):self.collect(collector=forged)
        self.assertEqual(self.paper(),before)
        self.assertEqual(self.service.status(self.clock)["hold"]["status"],"failed")

    def test_mid_publication_permission_revocation_keeps_partial_evidence_without_cash_mutation(self):
        from ariadne.options_workflow import publish_bytes
        self.ready();before=self.paper();published=[]
        def revoked(path,raw,**kwargs):
            result=publish_bytes(path,raw,**kwargs);published.append(path)
            if len(published)==1:self.store.set_setting(SETTINGS_KEY,{"enabled":False,"revision":2})
            return result
        with patch("ariadne.options_workflow.publish_bytes",side_effect=revoked):
            with self.assertRaises(HandoffConflict):self.collect()
        self.assertTrue(published);self.assertTrue(published[0].exists())
        self.assertEqual(self.paper(),before)
        with self.service.database() as db:
            self.assertIsNone(self.service._data(db,forward=True))
            self.assertGreater(db.execute("SELECT sum(bytes) FROM opt_reports").fetchone()[0],0)
        self.assertEqual(self.service.status(self.clock)["hold"]["status"],"failed")

    def test_brief_revision_change_cannot_commit_a_collected_source(self):
        self.ready();before=self.paper()
        def revoked(**kwargs):
            value=bundle(date(2024,1,2),self.clock)
            self.controls["revision"]+=1;kwargs["check"]();return value
        with self.assertRaises(HandoffConflict):self.collect(collector=revoked)
        self.assertEqual(self.paper(),before)
        with self.service.database() as db:self.assertIsNone(self.service._data(db,forward=True))

    def test_global_pause_and_resume_between_checkpoints_cannot_reuse_the_old_paper_epoch(self):
        self.ready();before=self.paper()
        def resumed(**kwargs):
            value=bundle(date(2024,1,2),self.clock)
            self.store.set_setting("paused",True)
            self.store.set_setting("paused",False)
            kwargs["check"]();return value
        with self.assertRaises(HandoffConflict):self.collect(collector=resumed)
        self.assertEqual(self.paper(),before)
        with self.service.database() as db:self.assertIsNone(self.service._data(db,forward=True))

    def test_rejected_source_body_is_retained_as_failed_evidence_without_any_quote_adoption(self):
        self.ready();before=self.paper();raw=source_payload(date(2024,1,2),crossed_call=True)
        def rejected(**kwargs):
            return {"schema":1,"unavailable":True,"reason":"Synthetic required-contract validation rejection","network_requests":1,
                "raw":{"url":source_url(date(2024,1,2)),"content_base64":base64.b64encode(raw).decode(),"sha256":hashlib.sha256(raw).hexdigest(),
                    "bytes":len(raw),"retrieved_at":self.clock.isoformat(),"http_status":203}}
        with self.assertRaises(ValueError):self.collect(collector=rejected)
        self.assertEqual(self.paper(),before)
        value=self.service.status(self.clock);self.assertEqual(value["hold"]["status"],"failed")
        receipt=value["last_run"]
        self.assertIs(receipt["dataset_adopted"],False)
        self.assertEqual({Path(item["path"]).name for item in receipt["artifacts"]},{"source.json","failure.json"})
        source=next(item for item in receipt["artifacts"] if item["path"].endswith("source.json"))
        failure=next(item for item in receipt["artifacts"] if item["path"].endswith("failure.json"))
        self.assertEqual((self.settings.workspace/source["path"]).read_bytes(),raw)
        details=json.loads((self.settings.workspace/failure["path"]).read_bytes())
        self.assertEqual(details["trades_adopted"],0);self.assertIs(details["dataset_adopted"],False)
        with self.service.database() as db:self.assertIsNone(self.service._data(db,forward=True))
        with patch.object(self.service.feed,"collect",side_effect=AssertionError("No automatic failure replay")):self.assertIsNone(self.service.feed.run_due(self.clock))

    def test_missing_funded_contract_cannot_be_substituted_or_erase_the_position(self):
        self.ready();state=self.seed_funded_state();before=deepcopy(state);seen=[]
        def absent(**kwargs):
            seen.extend(kwargs["required_contracts"])
            return bundle(date(2024,1,24),self.clock,raw=source_payload(date(2024,1,24),missing_call=True))
        with self.assertRaises(ValueError):self.collect(date(2024,1,24),collector=absent)
        self.assertEqual(seen,[state["position"]["contract_id"]]);self.assertEqual(self.paper()["state"],before)
        self.assertEqual(self.service.status(self.clock)["hold"]["status"],"failed")

    def test_crossed_required_contract_is_never_quarantined_into_a_fake_sale(self):
        self.ready();state=self.seed_funded_state()
        def crossed(**kwargs):
            return bundle(date(2024,1,24),self.clock,raw=source_payload(date(2024,1,24),crossed_call=True),required=kwargs["required_contracts"])
        with self.assertRaises(ValueError):self.collect(date(2024,1,24),collector=crossed)
        self.assertEqual(self.paper()["state"],state);self.assertEqual(self.paper()["state"]["closed_trade_count"],0)

    def test_feed_archive_cannot_replace_account_or_reset_the_monthly_allowance(self):
        self.ready();identifier=self.paper()["id"]
        result=self.service.archive(self.body(reason="Retain source experiment",archive_data=False),self.clock)
        self.assertEqual(result["status"],"completed");self.assertIsNone(self.paper())
        for start in (self.service.start_paper,self.service.start_portfolio):
            with self.assertRaises(HandoffConflict):start(self.body(acknowledge_simulated=True),datetime(2024,2,2,15,tzinfo=UTC))
        with self.service.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM opt_accounts").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT id FROM opt_accounts").fetchone()[0],identifier)

    def test_existing_brief_does_not_prevent_separate_late_source_due_check(self):
        from ariadne.briefing import BriefingService
        calls=[]
        class Event:
            def __init__(self):self.stopped=False
            def is_set(self):return self.stopped
            def wait(self,_):self.stopped=True
        service=object.__new__(BriefingService);service.stop_event=Event()
        class Feed:
            def run_due(self):calls.append("source")
        class Options:
            feed=Feed()
            def collect_due(self):return self.feed.run_due()
        service.options=Options();service.run_due=lambda:calls.append("existing brief read-only return")
        service._pause=lambda reason:(_ for _ in ()).throw(AssertionError(reason))
        service._loop()
        self.assertEqual(calls,["source","existing brief read-only return"])

    def test_feed_source_failure_cannot_pause_unrelated_daily_brief(self):
        from ariadne.briefing import BriefingService
        calls=[]
        class Event:
            def __init__(self):self.stopped=False
            def is_set(self):return self.stopped
            def wait(self,_):self.stopped=True
        service=object.__new__(BriefingService);service.stop_event=Event()
        class Feed:
            def run_due(self):raise ValueError("Source failed; feed owns its hold")
        class Options:
            feed=Feed()
            def collect_due(self):return self.feed.run_due()
        service.options=Options();service.run_due=lambda:calls.append("weather brief remains permitted")
        service._pause=lambda reason:calls.append("WRONG whole brief pause")
        service._loop()
        self.assertEqual(calls,["weather brief remains permitted"])


class OptionsFeedAPITests(unittest.TestCase):
    def setUp(self):
        from ariadne.engine import Engine
        from ariadne.server import Application
        from test_options_api import NoModel
        parent=Path(__file__).resolve().parents[1]/"runtime/tmp";parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="options-feed-api-",dir=parent);self.addCleanup(temporary.cleanup)
        settings=Settings(data_dir=Path(temporary.name)/"data",api_token="feed-fixture-key")
        self.store=Store(settings.data_dir/"ariadne.sqlite3")
        with patch("ariadne.server.Voice",return_value=object()):self.app=Application(settings,self.store,Engine(settings,self.store,NoModel()))

    def request(self,tail,body=None,*,raw=None,auth=True,origin=None,host="localhost:8080"):
        raw=json.dumps(body or {}).encode() if raw is None else raw
        env={"HTTP_HOST":host,"REQUEST_METHOD":"POST","PATH_INFO":"/api/options/"+tail,"CONTENT_TYPE":"application/json","CONTENT_LENGTH":str(len(raw)),"wsgi.input":io.BytesIO(raw),"QUERY_STRING":""}
        if auth:env["HTTP_AUTHORIZATION"]="Bearer feed-fixture-key"
        if origin:env["HTTP_ORIGIN"]=origin
        result={}
        def start(status,headers):result["status"]=int(status.split()[0])
        result["body"]=json.loads(b"".join(self.app(env,start)))
        return result

    def test_both_new_routes_keep_auth_origin_host_and_strict_json(self):
        for tail in ("feed","collect-feed"):
            self.assertEqual(self.request(tail,auth=False)["status"],401)
            self.assertEqual(self.request(tail,origin="https://outside.example")["status"],403)
            self.assertEqual(self.request(tail,host="outside.example")["status"],403)
            self.assertEqual(self.request(tail,raw=b'{"request_id":"a","request_id":"b","expected_revision":0}')["status"],400)
            self.assertEqual(self.request(tail,{"request_id":"one","expected_revision":True})["status"],400)

    def test_positive_route_dispatch_does_not_accept_arbitrary_url_or_capital(self):
        for tail,method in (("feed","configure"),("collect-feed","collect")):
            target=getattr(self.app.briefing.options.feed,method)
            with patch.object(self.app.briefing.options.feed,method,return_value={"status":"completed","request_id":"one"}) as routed:
                result=self.request(tail,{"request_id":"one","expected_revision":0})
                self.assertEqual(result["status"],200);routed.assert_called_once_with({"request_id":"one","expected_revision":0})
        for extra in ({"url":"https://outside.example"},{"capital":"5000"},{"symbol":"QQQ"}):
            result=self.request("feed",{"request_id":"one","expected_revision":0,"enabled":True,"acknowledge_feed":True,**extra})
            self.assertEqual(result["status"],400)
