"""Generated ledgers and isolated workflows, never market returns or owner data."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import localcontext
import json
import unittest
from unittest.mock import patch

from ariadne.options_eod import advance_paper, STRATEGY_IDS, RULE_SHA256, _eligible
from ariadne.options_journal import build_review, review_options, rejection_reason, REJECTION_REASONS
from ariadne import options_journal as journal_module
from test_options_eod import state, snapshot, quote
import test_options_eod_workflow as workflow_fixture

UTC=timezone.utc


def capture(before, day, *, price="12.05", quotes=None):
    row=snapshot(day,price=price,quotes=quotes)
    recorded=max(row["received_at"],before["last_decided_at"])
    paper=advance_paper(row,before,decided_at=recorded)
    if paper["status"]!="completed":raise AssertionError(paper)
    receipt={"request_id":"generated-"+day.isoformat(),"account_id":"one-paper-account",
             "accepted_snapshot":True,"quote_observed_at":None,"rule_sha256":RULE_SHA256,
             "source_date":row["source_date"],"received_at":row["received_at"],"recorded_at":recorded}
    return {"before_state":deepcopy(before),"snapshot":row,"paper":paper,"receipt":receipt}


def journal(*, winning=False):
    current=state();records=[]
    values=((date(2024,1,2),"12.05","0.8","0.9"),(date(2024,1,3),"12.1","0.8","0.9"),
            (date(2024,1,4),"12.15","0.8","0.9"),(date(2024,1,5),"12.2","4.5" if winning else "0.4","4.6" if winning else "0.42"),
            (date(2024,1,8),"12.2","4.5" if winning else "0.4","4.6" if winning else "0.42"),
            (date(2024,1,9),"12.2","4.5" if winning else "0.4","4.6" if winning else "0.42"))
    for day,price,bid,ask in values:
        value=capture(current,day,price=price,quotes=[quote(bid=bid,ask=ask)])
        records.append(value);current=value["paper"]["state"]
    return {"id":"one-paper-account","state":current},records


class OptionsJournalPureTests(unittest.TestCase):
    def test_malformed_capture_and_account_json_shapes_fail_with_supported_integrity_error(self):
        account,records=journal()
        malformed=(None,[],"not-an-object",0,True,{})
        for field in ("receipt","snapshot","paper"):
            for value in malformed:
                changed=deepcopy(records);changed[0][field]=value
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                    build_review(account,changed,now=account["state"]["last_decided_at"])
        for value in malformed:
            with self.subTest(account=value),self.assertRaises(ValueError):
                build_review(value,deepcopy(records),now=account["state"]["last_decided_at"])
        for value in malformed[:-1]:
            changed=deepcopy(account);changed["state"]=value
            with self.subTest(account_state=value),self.assertRaises(ValueError):
                build_review(changed,deepcopy(records),now=account["state"]["last_decided_at"])
        for value in (None,[None],[[]],[{}],"not-a-manifest"):
            changed=deepcopy(records);changed[0]["receipt"]["artifacts"]=value
            with self.subTest(artifacts=value),self.assertRaises(ValueError):
                build_review(account,changed,now=account["state"]["last_decided_at"])

    def test_baseline_only_has_no_quote_diagnostics_win_rate_or_performance_curve(self):
        current=state();before=deepcopy(current)
        value=build_review({"id":"one","state":current},[],now=current["last_decided_at"])
        self.assertEqual(value["status"],"waiting_for_first_capture")
        self.assertEqual(value["performance"]["curve"],[])
        self.assertIsNone(value["performance"]["observed_win_rate_pct"])
        self.assertEqual(value["diagnostics"]["strategies"],[])
        self.assertEqual(value["performance"]["trading_pnl"],"0")
        self.assertEqual(current,before)

    def test_actual_fee_inclusive_losses_and_peak_pnl_decline_reconcile(self):
        account,records=journal();before=deepcopy((account,records))
        result=build_review(account,records,now=account["state"]["last_decided_at"])
        p=result["performance"]
        self.assertEqual(p["entries"],1);self.assertEqual(p["closed_trades"],1)
        self.assertEqual(p["losses"],1);self.assertEqual(p["wins"],0)
        self.assertEqual(p["fees"],"1.3");self.assertEqual(p["trading_pnl"],"-51.3")
        self.assertEqual(p["max_peak_trading_pnl_drop"],"51.3")
        self.assertEqual(p["observed_win_rate_pct"],"0")
        self.assertEqual(p["sample_status"],"insufficient_closed_proxy_sample")
        self.assertEqual([row["id"] for row in p["by_strategy"]],list(STRATEGY_IDS))
        self.assertTrue(all(row["within_500_gross_cap"] for row in p["months"]))
        self.assertEqual((account,records),before)

    def test_winning_proxy_sample_is_not_promoted_as_future_probability(self):
        account,records=journal(winning=True)
        result=build_review(account,records,now=account["state"]["last_decided_at"])
        p=result["performance"]
        self.assertEqual(p["wins"],1);self.assertEqual(p["trading_pnl"],"358.7")
        self.assertEqual(p["observed_win_rate_pct"],"100")
        self.assertEqual(p["sample_status"],"insufficient_closed_proxy_sample")
        self.assertFalse(result["execution_qualified"])
        self.assertIn("not a predicted probability",p["win_rate_scope"])

    def test_contribution_at_actual_funding_month_is_excluded_from_pnl_curve(self):
        current=state(refs=False)
        row=capture(current,date(2024,1,31),quotes=[quote(expiry="2024-02-16")])
        account={"id":"one-paper-account","state":row["paper"]["state"]}
        value=build_review(account,[row],now=account["state"]["last_decided_at"])
        p=value["performance"]
        self.assertEqual(p["contributions"],"1000")
        self.assertEqual(p["trading_pnl"],"0")
        self.assertEqual(p["curve"][0]["trading_pnl"],"0")
        self.assertEqual(p["curve"][0]["daily_pnl"],"0")
        self.assertEqual(p["months"][0]["month"],"2024-02")
        self.assertEqual(p["months"][0]["funding_added"],"500")
        self.assertEqual(p["monthly_budget_remaining"],"500")

    def test_diagnostics_use_original_clock_not_current_month_or_future_trade_advice(self):
        before=state();record=capture(before,date(2024,1,2))
        account={"id":"one-paper-account","state":record["paper"]["state"]}
        original=build_review(account,[record],now=account["state"]["last_decided_at"])
        later=build_review(account,[record],now="2024-02-02T15:00:00+00:00")
        self.assertEqual(original["diagnostics"],later["diagnostics"])
        self.assertEqual(later["diagnostics"]["actual_decision_month"],"2024-01")
        self.assertFalse(later["source"]["latest_matches_expected_session"])
        self.assertIsNone(later["source"]["quote_observed_at"])

    def test_rejection_diagnostics_match_frozen_engine_gates_across_contract_conditions(self):
        row=snapshot(date(2024,1,2),price="12.05")
        clock="2024-01-03T15:00:00+00:00"
        candidates=[quote(),quote(expiry="2024-01-05"),quote(expiry="2024-02-02"),
                    quote(strike="13"),quote(ask="0.99",bid="0.9"),quote(bid="0",bid_size=0),
                    quote(ask="0.9",bid="0.1"),quote(ask_size=0)]
        for item in candidates:
            with self.subTest(quote=item):
                reason=rejection_reason(item,row,"call",clock)
                self.assertEqual(reason is None,_eligible(item,row,"call",datetime.fromisoformat(clock)))
                self.assertTrue(reason is None or reason in REJECTION_REASONS)
        underlier=deepcopy(row);underlier["underlying_close"]="100"
        self.assertEqual(rejection_reason(quote(),underlier,"call",clock),"underlying_at_or_above_100")
        # An actualNYJanuary decision is not reclassified asFebruary merely
        # because its UTC timestamp has crossed midnight/month-end.
        january_source=snapshot(date(2024,1,19),quotes=[quote(expiry="2024-02-02")])
        self.assertEqual(rejection_reason(january_source["quotes"][0],january_source,"call","2024-02-01T00:30:00+00:00"),"expiry_outside_decision_month")

    def test_no_partial_metrics_when_identity_counts_events_or_saved_summary_disagree(self):
        account,records=journal()
        changes=(lambda a,r:r.pop(),lambda a,r:r[0]["receipt"].update(account_id="other"),
                 lambda a,r:r[0]["paper"]["summary"].update(cash="999"),
                 lambda a,r:r[-1]["paper"]["events"].append({"type":"fill","action":"SELL"}),
                 lambda a,r:r[1].update(before_state=deepcopy(r[0]["before_state"])))
        for change in changes:
            a,r=deepcopy(account),deepcopy(records);change(a,r)
            with self.assertRaises(ValueError):build_review(a,r,now=account["state"]["last_decided_at"])

    def test_hostile_decimal_context_does_not_change_exact_accounting(self):
        account,records=journal(winning=True)
        normal=build_review(account,records,now=account["state"]["last_decided_at"])
        with localcontext() as context:
            context.prec=2
            self.assertEqual(build_review(account,records,now=account["state"]["last_decided_at"]),normal)

    def test_daily_profit_claim_cannot_survive_only_by_matching_copied_summary(self):
        current=state(refs=False)
        record=capture(current,date(2024,1,31),quotes=[quote(expiry="2024-02-16")])
        record["paper"]["state"]["last_daily_result"]["daily_pnl"]="500"
        record["paper"]["summary"]["last_daily_result"]["daily_pnl"]="500"
        account={"id":"one-paper-account","state":record["paper"]["state"]}
        with self.assertRaisesRegex(ValueError,"excluding external funding"):
            build_review(account,[record],now=account["state"]["last_decided_at"])


class OptionsJournalProtectedTests(unittest.TestCase):
    def setUp(self):
        self.fixture=workflow_fixture.EODWorkflowTests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def ready(self):
        self.fixture.ready();self.fixture.migrate()

    def database_snapshot(self):
        with self.fixture.owner.database() as db:return list(db.iterdump())

    def add_failed_metadata(self,count):
        """Bounded generated receipt history, not operational owner work."""
        with self.fixture.owner.database() as db:
            for index in range(count):
                identifier="generated-failure-"+str(index)
                receipt={"request_id":identifier,"action":"research_compare","status":"failed", "artifacts":[],
                         "created_at":self.fixture.clock.isoformat(),"reason":"R"*1200}
                text=json.dumps(receipt)
                db.execute("INSERT INTO opt_runs VALUES(?,?,?,?,?,?,?)",(identifier,"research_compare",None,"a"*64,"failed",text,len(text.encode())))

    def test_passive_uninitialized_review_creates_no_table_or_account(self):
        before=self.database_snapshot()
        value=review_options(self.fixture.owner,self.fixture.clock)
        self.assertEqual(value["status"],"not_started")
        self.assertEqual(self.database_snapshot(),before)

    def test_malformed_failed_running_and_acknowledged_receipts_never_escape_as_shape_errors(self):
        self.ready();identifier="generated-malformed-failure"
        with self.fixture.owner.database() as db:
            db.execute("INSERT INTO opt_runs VALUES(?,?,?,?,?,?,?)",(identifier,"research_compare",None,"a"*64,"failed","[]",2))
        for status in ("failed","running","acknowledged"):
            for value in (None,[],"not-an-object",0,True,{}):
                raw=json.dumps(value)
                with self.fixture.owner.database() as db:
                    db.execute("UPDATE opt_runs SET action=?,status=?,receipt=?,bytes=? WHERE request_id=?",("eod_collect" if status=="acknowledged" else "research_compare",status,raw,len(raw.encode()),identifier))
                before=self.database_snapshot()
                with self.subTest(status=status,value=value),self.assertRaisesRegex(ValueError,"retained receipt"):
                    review_options(self.fixture.owner,self.fixture.clock)
                self.assertEqual(self.database_snapshot(),before)

    def test_malformed_nonprefix_report_receipts_are_rejected_before_artifact_verification(self):
        self.ready();identifier="generated-malformed-report"
        with self.fixture.owner.database() as db:
            db.execute("INSERT INTO opt_runs VALUES(?,?,?,?,?,?,?)",(identifier,"eod_collect",None,"a"*64,"failed","[]",2))
            db.execute("INSERT INTO opt_reports VALUES(?,?,?)",(identifier,2,"[]"))
        for value in (None,[],"not-an-object",0,True,{}, {"request_id":identifier,"artifacts":[None]}):
            raw=json.dumps(value)
            with self.fixture.owner.database() as db:
                db.execute("UPDATE opt_runs SET receipt=?,bytes=? WHERE request_id=?",(raw,len(raw.encode()),identifier))
                db.execute("UPDATE opt_reports SET receipt=?,bytes=? WHERE request_id=?",(raw,len(raw.encode()),identifier))
            before=self.database_snapshot()
            with self.subTest(value=value),self.assertRaises(ValueError):
                review_options(self.fixture.owner,self.fixture.clock)
            self.assertEqual(self.database_snapshot(),before)

    def test_malformed_completed_receipt_in_frozen_upstream_reader_becomes_supported_integrity_error(self):
        self.ready();receipt=self.fixture.collect();identifier=receipt["request_id"]
        for value in (None,[],"not-an-object",0,True,{}):
            raw=json.dumps(value)
            with self.fixture.owner.database() as db:
                db.execute("UPDATE opt_runs SET receipt=?,bytes=? WHERE request_id=?",(raw,len(raw.encode()),identifier))
                db.execute("UPDATE opt_reports SET receipt=?,bytes=? WHERE request_id=?",(raw,len(raw.encode()),identifier))
            before=self.database_snapshot()
            with self.subTest(value=value),self.assertRaises(ValueError):
                review_options(self.fixture.owner,self.fixture.clock)
            self.assertEqual(self.database_snapshot(),before)

    def test_malformed_snapshot_or_paper_outer_json_never_returns_partial_metrics(self):
        self.ready();self.fixture.collect();original=journal_module._artifact
        for name in ("snapshot.json","paper.json"):
            for value in (None,[],"not-an-object",0,True,{}):
                def malformed(owner,receipt,artifact,maximum):
                    return value if artifact==name else original(owner,receipt,artifact,maximum)
                before=self.database_snapshot()
                with self.subTest(name=name,value=value),patch("ariadne.options_journal._artifact",side_effect=malformed),self.assertRaises(ValueError):
                    review_options(self.fixture.owner,self.fixture.clock)
                self.assertEqual(self.database_snapshot(),before)

    def test_actual_verified_captures_return_metadata_only_without_writes_models_or_network(self):
        self.ready();self.fixture.collect();before=self.database_snapshot()
        with patch("socket.socket",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No model/worker")):
            value=review_options(self.fixture.owner,self.fixture.clock)
        self.assertEqual(value["status"],"recorded_observations")
        self.assertEqual(len(value["performance"]["curve"]),1)
        self.assertEqual(value["source"]["last_source_date"],"2024-01-02")
        self.assertTrue(value["source"]["partial_chain"])
        self.assertNotIn('"quotes":',json.dumps(value))
        self.assertEqual(self.database_snapshot(),before)

    def test_global_pause_is_preserved_and_does_not_block_read_only_review(self):
        self.ready();self.fixture.store.set_setting("paused",True)
        before=self.database_snapshot()
        value=review_options(self.fixture.owner,self.fixture.clock)
        self.assertTrue(value["holds"]["global_paused"])
        self.assertEqual(value["performance"]["entries"],0)
        self.assertEqual(self.database_snapshot(),before)

    def test_older_capture_file_and_original_cache_changes_stop_review(self):
        self.ready();first=self.fixture.collect();self.fixture.collect(date(2024,1,3))
        item=next(item for item in first["artifacts"] if item["path"].endswith("bundle.json"))
        path=self.fixture.registry._path(item["path"]);original=path.read_bytes();path.write_bytes(b'{}')
        before=self.database_snapshot()
        with self.assertRaisesRegex(ValueError,"missing or changed"):
            review_options(self.fixture.owner,self.fixture.clock)
        self.assertEqual(self.database_snapshot(),before)
        path.write_bytes(original)
        with self.fixture.owner.database() as db:
            db.execute("UPDATE opt_eod_data SET value=? WHERE source_date=?",(b'{}',"2024-01-02"))
        with self.assertRaises(ValueError):review_options(self.fixture.owner,self.fixture.clock)

    def test_source_failure_is_reported_without_adopting_a_quote_or_return(self):
        self.ready()
        with self.assertRaises(ValueError):self.fixture.collect(collector=lambda **kwargs:{"malformed":True})
        before=self.database_snapshot()
        value=review_options(self.fixture.owner,self.fixture.clock)
        self.assertEqual(value["status"],"held")
        self.assertEqual(value["performance"]["curve"],[])
        self.assertIsNone(value["performance"]["observed_win_rate_pct"])
        self.assertEqual(len(value["failed_checks"]),1)
        self.assertFalse(value["failed_checks"][0]["adopted_as_performance"])
        self.assertTrue(value["failed_checks"][0]["retained_artifacts_verified"])
        self.assertEqual(self.database_snapshot(),before)

    def test_failure_window_is_bounded_while_earliest_unresolved_hold_is_preserved(self):
        self.ready();self.add_failed_metadata(25);before=self.database_snapshot()
        value=review_options(self.fixture.owner,self.fixture.clock)
        self.assertEqual(value["status"],"held")
        self.assertEqual(len(value["failed_checks"]),20)
        self.assertEqual(value["omitted_failed_checks"],5)
        self.assertEqual(value["failed_checks"][0]["request_id"],"generated-failure-24")
        self.assertEqual(value["holds"]["workflow"]["request_id"],"generated-failure-0")
        self.assertTrue(all(len(row["reason"])==400 for row in value["failed_checks"]))
        self.assertLess(len(json.dumps(value).encode()),128*1024)
        self.assertEqual(self.database_snapshot(),before)

    def test_older_failed_capture_artifacts_are_verified_beyond_display_window(self):
        self.ready()
        with self.assertRaises(ValueError):self.fixture.collect(collector=lambda **kwargs:{"malformed":True})
        identifier=self.fixture.collect_body["request_id"]
        with self.fixture.owner.database() as db:
            receipt=json.loads(db.execute("SELECT receipt FROM opt_runs WHERE request_id=?",(identifier,)).fetchone()[0])
        self.add_failed_metadata(25)
        item=next(item for item in receipt["artifacts"] if item["path"].endswith("failure.json"))
        self.fixture.registry._path(item["path"]).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError,"missing or changed"):
            review_options(self.fixture.owner,self.fixture.clock)

    def test_multiple_active_accounts_never_become_an_aggregated_budget(self):
        self.ready()
        with self.fixture.owner.database() as db:
            db.execute("INSERT INTO opt_accounts(id,state,started_at,policy,closed_at) SELECT 'other-unqualified-account',state,started_at,policy,closed_at FROM opt_accounts WHERE closed_at IS NULL")
        with self.assertRaisesRegex(ValueError,"single shared"):
            review_options(self.fixture.owner,self.fixture.clock)
