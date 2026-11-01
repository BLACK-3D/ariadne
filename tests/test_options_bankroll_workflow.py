"""Generated isolated same-account upgrades; no feed, owner account or returns."""
from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal,Inexact,ROUND_UP,localcontext
import json
import os
from unittest import TestCase
from unittest.mock import Mock,patch

from ariadne.handoff import HandoffConflict
from ariadne.options_workflow import _json,_digest,_paper_engine
from ariadne import options_bankroll_eod as engine
import test_options_root_workflow as roots
import test_options_eod_workflow as eod

DAY=roots.DAY


class BankrollWorkflowTests(TestCase):
    setUp=roots.RootWorkflowTests.setUp
    body=roots.RootWorkflowTests.body
    ready=roots.RootWorkflowTests.ready
    paper=roots.RootWorkflowTests.paper
    put=roots.RootWorkflowTests.put
    source=roots.RootWorkflowTests.source
    baseline=roots.RootWorkflowTests.baseline
    collect=roots.RootWorkflowTests.collect
    ledger=roots.RootWorkflowTests.ledger
    database_dump=roots.RootWorkflowTests.database_dump
    active_raw=roots.RootWorkflowTests.active_raw

    def prepare(self,*,baseline=False,old_observation=False):
        roots.RootWorkflowTests.prepare(self,old_observation=old_observation)
        roots.RootWorkflowTests.migrate(self,self.baseline() if baseline else None)
        self.prior=deepcopy(self.paper());self.bank=self.owner.bankroll

    def activate(self,**overrides):
        with self.owner.database() as db:binding=self.bank.binding(db,self.paper())
        self.bank_body=self.body(acknowledge_simulated=True,acknowledge_total_bankroll=True,**binding)
        self.bank_body.update(overrides)
        return self.bank.migrate(self.bank_body,self.clock)

    def frozen(self):
        with self.owner.database() as db:
            return {t:[tuple(r) for r in db.execute("SELECT * FROM "+t)] for t in
                ("opt_root_migrations","opt_root_data","opt_root_state","opt_root_meta","opt_eod_migrations","opt_eod_data","opt_eod_state","opt_eod_meta")}

    def test_upgrade_credits_total_once_same_account_preserves_every_old_cutoff(self):
        self.prepare(old_observation=True);frozen=self.frozen();before=self.prior
        receipt=self.activate();after=self.paper();state=after["state"]
        self.assertEqual(after["id"],before["id"]);self.assertEqual(after["started_at"],before["started_at"])
        self.assertEqual(after["policy"],before["policy"]);self.assertEqual(state["schema"],6)
        self.assertEqual(state["cash"],"25000");self.assertEqual(state["contributions"],"25000")
        self.assertEqual(receipt["simulated_funding_added"],"24500");self.assertTrue(receipt["no_automatic_topups"])
        for k in ("funding_count","month","monthly_committed","fees","trade_count","closed_trade_count","history","cursor_history","prefix_sha256","root_policy"):
            self.assertEqual(state[k],before["state"][k],k)
        self.assertIsNone(state["last_daily_result"]);self.assertEqual(self.frozen(),frozen)
        self.assertEqual(set(self.bank.verify(receipt)),{"previous-state.json","initial-state.json","activation.json","source-cutoff.json"})
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM opt_accounts").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM opt_bankroll_migrations").fetchone()[0],1)
            self.bank.verify_account(db,after["id"],state,full=True)
            events=[json.loads(r[0]) for r in db.execute("SELECT value FROM opt_events WHERE run_id=?",(receipt["request_id"],))]
        self.assertEqual([e["type"] for e in events],["capital_adjustment"])
        self.assertEqual(events[0]["amount"],"24500")

    def test_completed_replay_never_credits_or_changes_sources_again(self):
        self.prepare();receipt=self.activate();before=self.database_dump()
        replay=self.bank.migrate(self.bank_body,self.clock)
        self.assertTrue(replay["replayed"]);self.assertEqual(replay["state_sha256"],receipt["state_sha256"])
        self.assertEqual(self.database_dump(),before)
        self.assertRaises(HandoffConflict,self.activate)
        self.assertEqual(self.database_dump(),before)

    def test_exact_recorded_artifact_read_bounds_and_passive_stale_binding(self):
        self.prepare();receipt=self.activate();read=self.owner._read
        with patch.object(self.owner,"_read",wraps=read) as reads:self.bank.verify(receipt)
        bounds={a["path"]:a["bytes"] for a in receipt["artifacts"]}
        self.assertEqual({c.args[0]:c.args[1] for c in reads.call_args_list},bounds)
        with self.owner.database() as db:
            self.assertRaises(HandoffConflict,self.bank.binding,db,self.prior)

    def test_exact_hash_epoch_and_acknowledgements_fail_closed(self):
        for field,value in (("acknowledge_total_bankroll",False),("expected_state_sha256","0"*64),
                            ("expected_source_sha256","0"*64),("expected_account_id","another-account")):
            with self.subTest(field=field):
                self.setUp();self.prepare();before=self.ledger()
                self.assertRaises((ValueError,HandoffConflict),self.activate,**{field:value})
                self.assertEqual(self.active_raw()["schema"],5);self.assertEqual(self.ledger(),before)
        self.setUp();self.prepare()
        with self.owner.database() as db:binding=self.bank.binding(db,self.paper())
        binding["expected_owner_epoch"]["brief_revision"]+=1
        self.assertRaises(HandoffConflict,self.activate,**binding)
        self.assertEqual(self.active_raw()["schema"],5)

    def test_permissions_revoked_during_publication_cannot_change_old_account(self):
        self.prepare();before=self.ledger();frozen=self.frozen();publish=self.owner._publish
        def revoked(*a,**kw):
            self.controls["revision"]+=1
            return publish(*a,**kw)
        with patch.object(self.owner,"_publish",side_effect=revoked):self.assertRaises(HandoffConflict,self.activate)
        self.assertEqual(self.active_raw()["schema"],5);self.assertEqual(self.ledger(),before);self.assertEqual(self.frozen(),frozen)

    def test_late_activation_artifact_tamper_cannot_commit_or_be_replayed(self):
        self.prepare();before=self.ledger();publish=self.owner._publish
        def tampered(*a,**kw):
            final=kw["final"]
            def bad(db):
                p=self.registry._path(a[2]["artifacts"][0]["path"]);s=p.stat();raw=p.read_bytes()
                p.write_bytes(b" "+raw[1:]);os.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns))
                return final(db)
            kw["final"]=bad
            return publish(*a,**kw)
        with patch.object(self.owner,"_publish",side_effect=tampered):self.assertRaises(ValueError,self.activate)
        self.assertEqual(self.ledger(),before);self.assertEqual(self.active_raw()["schema"],5)
        self.assertRaises(HandoffConflict,self.bank.migrate,self.bank_body,self.clock)

    def test_normal_collector_dispatch_seals_new_source_without_rewriting_old5(self):
        self.prepare();self.activate();frozen=self.frozen();receipt=self.collect()
        state=self.paper()["state"]
        self.assertEqual(receipt["paper_schema"],6);self.assertEqual(receipt["rule_sha256"],engine.RULE_SHA256)
        self.assertTrue(receipt["accepted_snapshot"]);self.assertEqual(state["bankroll_processed_count"],1)
        self.assertEqual(state["cash"],"25000");self.assertEqual(state["funding_count"],self.prior["state"]["funding_count"])
        self.assertEqual(state["last_daily_result"]["trading_pnl"],"0");self.assertEqual(self.frozen(),frozen)
        self.owner.root_eod.verify(receipt)
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM opt_bankroll_data").fetchone()[0],1)
            self.bank.verify_account(db,self.paper()["id"],state,full=True)
        self.assertIs(_paper_engine(state),engine)

    def test_full_old5_source_and_old_f_evidence_remain_required(self):
        for table in ("opt_root_state","opt_root_data","opt_eod_state"):
            with self.subTest(table=table):
                self.setUp();self.prepare(old_observation=True);self.collect(date(2024,1,3));self.activate();before=self.ledger()
                with self.owner.database() as db:
                    key="digest" if table=="opt_root_data" else "state_sha256"
                    db.execute("UPDATE "+table+" SET "+key+"=?",("0"*64,))
                collector=Mock(side_effect=AssertionError("No source request after old cutoff corruption"))
                self.assertRaises(ValueError,self.collect,date(2024,1,4),collector=collector)
                collector.assert_not_called();self.assertEqual(self.ledger()["opt_accounts"],before["opt_accounts"])

    def test_failed_new_source_retains_raw_and_stops_future_collection(self):
        self.prepare();self.activate();before=self.ledger()
        received=self.clock+timedelta(days=1)
        self.assertRaises(ValueError,self.collect,DAY,value=eod.bundle(DAY,received))
        self.assertEqual(self.ledger(),before);collector=Mock()
        self.assertIsNone(self.owner.root_eod.run_due(self.clock));collector.assert_not_called()
        with self.owner.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM opt_bankroll_data").fetchone()[0],0)
            row=db.execute("SELECT status,receipt FROM opt_runs WHERE action='root_collect'").fetchone()
        self.assertEqual(row[0],"failed");self.assertTrue(json.loads(row[1])["source_failed"])

    def stress_sequence(self,*,sell=False):
        self.prepare(baseline=True);self.activate()
        for day,price in ((DAY,11.8),(date(2024,1,3),11.7),(date(2024,1,4),11.6)):
            self.source(last=day.isoformat());self.owner.eod.stress.capture_due(self.clock);self.collect(day,price=price)
        if sell:
            for day in (date(2024,1,5),date(2024,1,8),date(2024,1,9)):
                self.collect(day,price=11.6,bid=5,ask=5.1)

    def test_actual_generated_multicontract_entry_and_exit_share_one_cash_ledger(self):
        self.stress_sequence();state=self.paper()["state"]
        self.assertEqual(state["trade_count"],1);self.assertEqual(state["position"]["quantity"],5)
        self.assertEqual(state["monthly_committed"],"456.5");self.assertEqual(state["fees"],"3.25")
        self.assertEqual(state["cash"],"24546.75");self.assertEqual(state["contract_entry_count"],5)
        for day in (date(2024,1,5),date(2024,1,8),date(2024,1,9)):self.collect(day,price=11.6,bid=5,ask=5.1)
        state=self.paper()["state"]
        self.assertIsNone(state["position"]);self.assertEqual(state["closed_trade_count"],1)
        self.assertEqual(state["contract_exit_count"],5);self.assertEqual(state["cash"],"27043.5")
        self.assertEqual(state["fees"],"6.5");self.assertEqual(state["contributions"],"25000")
        self.assertEqual(state["monthly_committed"],"456.5")
        with self.owner.database() as db:self.bank.verify_account(db,self.paper()["id"],state,full=True)

    def test_readonly_journal_readiness_brief_disclose_total_and_exclude_adjustment_profit(self):
        self.prepare();self.activate();self.collect()
        from ariadne.options_journal import review_options
        from ariadne.briefing import options_eod_brief_view
        before=self.database_dump();journal=review_options(self.owner,self.clock)
        status=self.owner.status(self.clock);readiness=status["paper_readiness"]
        for v in (journal,readiness):
            for k,want in (("account_schema",6),("bankroll_total","25000"),("monthly_gross_commitment_cap","25000"),("all_in_entry_cap","1000"),("automatic_topups",False)):
                self.assertEqual(v[k],want,k)
        self.assertEqual(journal["performance"]["trading_pnl"],"0");self.assertEqual(journal["performance"]["closed_trades"],0)
        self.assertEqual(readiness["configured_active_paper_roots"],["KO"]);self.assertFalse(readiness["entry_authorized"])
        self.assertEqual(options_eod_brief_view(status)["schema"],6)
        with localcontext() as ctx:
            ctx.prec=4;ctx.rounding=ROUND_UP;ctx.traps[Inexact]=True
            repeated=review_options(self.owner,self.clock);ready=self.owner.status(self.clock)["paper_readiness"]
        self.assertEqual(repeated,journal);self.assertEqual(ready,readiness)
        self.assertEqual(self.database_dump(),before)

    def test_readonly_journal_reconciles_saved_contracts_and_actual_closed_proxy_pnl(self):
        self.stress_sequence(sell=True)
        from ariadne.options_journal import review_options
        before=self.database_dump();journal=review_options(self.owner,self.clock);p=journal["performance"]
        self.assertEqual((p["entries"],p["closed_trades"],p["wins"],p["losses"]),(1,1,1,0))
        self.assertEqual((p["contract_entries"],p["contract_exits"]),(5,5));self.assertEqual(p["trading_pnl"],"2043.5")
        self.assertFalse(journal["execution_qualified"]);self.assertEqual(self.database_dump(),before)

    def test_legacy_financial_reservations_stay_blocked_research_stays_available(self):
        self.prepare();self.activate();before=self.database_dump()
        for action in ("import","vix_signals","vix_backtest"):
            with self.owner.database() as db:
                db.execute("BEGIN IMMEDIATE");self.assertIsNone(self.owner._reserve(db,action,self.body(),None,self.clock));db.rollback()
        for action in ("bankroll_migrate","root_migrate","paper","eod_collect","stress_activate","refresh"):
            with self.owner.database() as db:
                db.execute("BEGIN IMMEDIATE");count=db.execute("SELECT count(*) FROM opt_runs").fetchone()[0]
                self.assertRaises(HandoffConflict,self.owner._reserve,db,action,self.body(),None,self.clock)
                self.assertEqual(db.execute("SELECT count(*) FROM opt_runs").fetchone()[0],count);db.rollback()
        self.assertEqual(self.database_dump(),before)

    def test_capital_only_upgrade_preserves_source_pause_and_entry_context_failure(self):
        self.prepare()
        self.owner.root_eod.configure(self.body(enabled=False,acknowledge_source=True),self.clock)
        with self.owner.database() as db:
            db.execute("INSERT INTO opt_stress_context_failures VALUES(?,?,?,?)",("generated-entry-stop",self.prior["id"],self.clock.isoformat(),"Generated entry-only capture failure; no retry"))
            before=tuple(db.execute("SELECT * FROM opt_stress_context_failures").fetchone())
        receipt=self.activate();report=json.loads(self.bank.verify(receipt)["activation.json"])
        self.assertFalse(report["entry_controls"]["source"]["enabled"])
        self.assertEqual(report["entry_controls"]["failures"][0]["request_id"],"generated-entry-stop")
        status=self.owner.status(self.clock)
        self.assertTrue(status["bankroll"]["entry_only_stop_present"]);self.assertFalse(status["bankroll"]["source_enabled"])
        self.assertFalse(status["eod"]["config"]["enabled"]);self.assertEqual(self.paper()["state"]["cash"],"25000")
        with self.owner.database() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM opt_stress_context_failures").fetchone()),before)
            self.assertEqual(self.owner.eod.stress.contexts(db,self.prior["id"]),[])
        with patch.object(self.owner.root_eod,"_collect",side_effect=AssertionError("Paused source must remain stopped")):
            self.assertIsNone(self.owner.root_eod.run_due(self.clock))

    def test_changed_entry_only_controls_during_publication_cannot_be_accepted(self):
        self.prepare();before=self.ledger();publish=self.owner._publish
        def changed(*a,**kw):
            with self.owner.database() as db:db.execute("INSERT INTO opt_stress_context_failures VALUES(?,?,?,?)",("generated-new-stop",self.prior["id"],self.clock.isoformat(),"Entry-only stop arrived during publication"))
            return publish(*a,**kw)
        with patch.object(self.owner,"_publish",side_effect=changed):self.assertRaises(HandoffConflict,self.activate)
        self.assertEqual(self.ledger(),before);self.assertEqual(self.active_raw()["schema"],5)

    def test_coherent_missing_saved_ring_cannot_seal_a_different_new6_gate(self):
        self.prepare(baseline=True);self.activate();self.source(last=DAY.isoformat())
        self.owner.eod.stress.capture_due(self.clock);before=self.ledger();publish=self.owner._publish
        def changed(cfg,brief,receipt,files,**kw):
            files=tuple((name,b"[]" if name=="contexts.json" else raw) for name,raw in files)
            receipt["contexts_sha256"]=_digest(b"[]")
            return publish(cfg,brief,receipt,files,**kw)
        with patch.object(self.owner,"_publish",side_effect=changed):
            self.assertRaisesRegex(ValueError,"benchmark input/query",self.collect,DAY,price=11.8)
        self.assertEqual(self.ledger(),before)
        with self.owner.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM opt_bankroll_data").fetchone()[0],0)

    def test_upgrade_after_closed_legacy_trade_preserves_profit_and_epoch_labels(self):
        self.prepare(baseline=True)
        for day,price in ((DAY,11.8),(date(2024,1,3),11.7),(date(2024,1,4),11.6)):
            self.source(last=day.isoformat());self.owner.eod.stress.capture_due(self.clock);self.collect(day,price=price)
        for day in (date(2024,1,5),date(2024,1,8),date(2024,1,9)):self.collect(day,price=11.6,bid=5,ask=5.1)
        prior=self.paper();self.assertEqual(prior["state"]["closed_trade_count"],1)
        receipt=self.activate();state=self.paper()["state"]
        self.assertEqual(receipt["simulated_funding_added"],"24500")
        self.assertEqual(Decimal(state["cash"])-Decimal(prior["state"]["cash"]),Decimal("24500"))
        from ariadne.options_journal import review_options
        journal=review_options(self.owner,self.clock);p=journal["performance"]
        self.assertEqual((p["closed_trades"],p["legacy_closed_trade_count"],p["new6_closed_trade_count"]),(1,1,0))
        self.assertEqual(p["trading_pnl"],prior["summary"]["trading_pnl"])
        self.assertTrue(all(r["account_schema"]==5 for r in p["curve"]))
        self.collect(date(2024,1,10),price=11.6,bid=5,ask=5.1)
        daily=self.paper()["state"]["last_daily_result"]
        self.assertEqual(daily["daily_pnl"],"0");self.assertEqual(daily["trading_pnl"],prior["summary"]["trading_pnl"])
        self.assertEqual(daily["contributions"],"25000")


class BankrollJournalArithmeticTests(TestCase):
    def test_nonterminating_win_fraction_ignores_ambient_decimal_traps(self):
        """View arithmetic only; source/account integrity has separate host tests."""
        from ariadne import options_bankroll_journal as journal
        from decimal import Context
        from types import SimpleNamespace
        clock=roots.datetime(2024,1,10,15,5,tzinfo=roots.UTC)
        state={"root_processed_count":3,"bankroll_processed_count":3,"trade_count":3,"closed_trade_count":3,
            "contract_entry_count":3,"contract_exit_count":3,"fees":"3.9","monthly_committed":"100","hold":None,
            "bankroll_policy":{"activation":{"initial_financials":{"closed_trade_count":0}}}}
        current={"cash":"24999","equity":"24999","contributions":"25000","trading_pnl":"-1","fees":"3.9", "month":"2024-01",
            "monthly_budget_used":"100","monthly_budget_remaining":"24900","cap_assurance":"modeled_as_recorded", "last_entry_gate":None,"policy_stats":{"realized_pnl":"-1"}}
        class DB:
            def execute(self,sql,*args):
                if sql.startswith("SELECT request_id FROM opt_root_data"):return SimpleNamespace(fetchall=lambda:[("p0",),("p1",),("p2",)])
                if sql.startswith("SELECT count(*) FROM opt_runs"):return SimpleNamespace(fetchone=lambda:(0,))
                return iter(())
        def saved(receipt):
            n=int(receipt["request_id"][1:]);day="2024-01-0"+str(n+2);pnl=("1","0","-1")[n]
            daily={"date":day,"recorded_at":clock.isoformat(),"trading_pnl":pnl,"equity":str(25000+int(pnl)),"contributions":"25000", "month":"2024-01","monthly_pnl":pnl,"monthly_budget_used":"100","monthly_budget_remaining":"24900"}
            return {"paper.json":_json({"state":{"schema":6,"last_daily_result":daily},"events":[{"type":"fill","action":"SELL","trade_pnl":("1","-1","-1")[n]}]}).encode(),
                "snapshot.json":_json({"source_date":day,"received_at":clock.isoformat(),"partial_chain":True}).encode()}
        owner=Mock();owner.eod._recorded.side_effect=lambda db,request_id:{"request_id":request_id,"recorded_at":clock.isoformat()}
        owner.root_eod.verify.side_effect=saved;owner.root_eod.config.return_value={"enabled":True}
        account={"id":"generated-view","state":state}
        with patch.object(engine,"validate_state",return_value=state),patch.object(engine,"summarize",return_value=current):
            clean=journal.review(owner,DB(),account,now=clock,paused=False,revision=1)
            with localcontext() as ctx:
                ctx.prec=3;ctx.rounding=ROUND_UP;ctx.traps[Inexact]=True
                hostile=journal.review(owner,DB(),account,now=clock,paused=False,revision=1)
        self.assertEqual(clean,hostile);self.assertEqual(clean["performance"]["wins"],1)
        with localcontext(Context(prec=40)):expected=format(Decimal(100)/3,"f")
        self.assertEqual(clean["performance"]["observed_win_rate_pct"],expected)
