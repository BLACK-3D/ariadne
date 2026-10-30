"""Durable streamed research and one shared options risk account, offline."""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from unittest.mock import patch
import unittest

from ariadne.handoff import HandoffConflict
from ariadne.options_data import canonical
from ariadne.options_portfolio import RULE_SHA256, STRATEGY_IDS, backtest_comparison
from ariadne.options_workflow import OptionsWorkflow, INBOX
import test_options_workflow as workflow_helpers
from test_options_recovery import fixture, through
from test_options_portfolio import mixed_strategies_month


class OptionsWorkflowPortfolioTests(unittest.TestCase):
    # Reuse the isolated registry/store setup, without inheriting and rerunning
    # its legacy tests. Every writable path is a private runtime/tmp directory.
    setUp=workflow_helpers.OptionsWorkflowTests.setUp
    body=workflow_helpers.OptionsWorkflowTests.body
    enable=workflow_helpers.OptionsWorkflowTests.enable
    write=workflow_helpers.OptionsWorkflowTests.write
    paper=workflow_helpers.OptionsWorkflowTests.paper

    def portfolio(self):
        return self.service.start_portfolio(self.body(acknowledge_simulated=True),self.clock)

    def restart(self):
        self.service=OptionsWorkflow(self.registry,self.store,controls=lambda:dict(self.controls))

    def archive_files(self, value=None, split=14):
        data=deepcopy(value or fixture(24,historical=True))
        self.clock=datetime.fromisoformat(data["as_of"])
        pieces=[]
        for index,rows in enumerate((data["snapshots"][:split],data["snapshots"][split:])):
            part=deepcopy(data);part["snapshots"]=rows
            path=f"imports/options/archive/part-{index}.json"
            raw=canonical(part);destination=self.registry._path(path)
            destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
            pieces.append({"path":path,"sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw),"snapshots":len(rows),
                "quotes":sum(len(row["quotes"]) for row in rows),"first_observed_at":rows[0]["observed_at"],"last_observed_at":rows[-1]["observed_at"]})
        manifest={"schema":1,"as_of":data["as_of"],"evidence_kind":data["evidence_kind"],"symbol":"QQQ","source":deepcopy(data["source"]),
            "selection":{"start":data["snapshots"][0]["observed_at"][:10],"end":data["snapshots"][-1]["observed_at"][:10],"max_dte":35,"reason":"Fixed full fixture interval before research results."},
            "limitations":["Synthetic regression prices marked historical only to exercise owner evidence boundaries; not performance evidence."],"chunks":pieces}
        path="imports/options/archive/catalog.json"
        self.write(manifest,path)
        return path,manifest,data

    def registered(self):
        path,manifest,data=self.archive_files();self.enable()
        receipt=self.service.register_archive(self.body(path=path),self.clock)
        return receipt,path,manifest,data

    def report(self, receipt):
        artifact=next(item for item in receipt["artifacts"] if item["path"].endswith("comparison.json"))
        return json.loads((self.settings.workspace/artifact["path"]).read_bytes())

    def test_register_and_compare_are_offline_streams_separate_from_forward_cash(self):
        from ariadne.options_archive import iter_archive
        consumed=[]
        def observed(*args,**kwargs):
            for chunk in iter_archive(*args,**kwargs):
                consumed.append(len(chunk["snapshots"]))
                yield chunk
        with patch("socket.create_connection",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No process")):
            registered,path,manifest,data=self.registered();self.portfolio()
            before=self.service.status(self.clock)["paper"]
            with patch("ariadne.options_archive.iter_archive",side_effect=observed):
                result=self.service.compare(self.body(),self.clock)
        report=self.report(result);status=self.service.status(self.clock)
        self.assertEqual(consumed,[14,10])
        self.assertEqual(registered["historical_positions_adopted"],0)
        self.assertEqual(status["paper"],before)
        self.assertEqual(status["paper"]["state"]["cash"],"500")
        self.assertIsNone(status["forward_quality"])
        self.assertEqual(report["metrics"],backtest_comparison(data)["metrics"])
        self.assertEqual(report["rule_sha256"],RULE_SHA256)
        self.assertEqual(set(report["strategy_results"]),set(STRATEGY_IDS))
        self.assertEqual({item["path"].split("/")[-1] for item in result["artifacts"]},{"comparison.json","archive-manifest.json"})
        self.assertEqual(report["accepted_manifest_sha256"],registered["manifest_sha256"])
        self.assertEqual(report["coverage"]["source_snapshots"],24)
        self.assertFalse(report["execution_qualified"])
        self.assertEqual(status["events"],[])

    def test_catalog_persists_only_manifest_not_another_copy_of_quote_chunks(self):
        result,path,manifest,data=self.registered()
        with self.service.database() as db:
            row=db.execute("SELECT value,bytes,quality FROM opt_catalogs").fetchone()
            self.assertEqual(bytes(row["value"]),canonical(manifest))
            self.assertLess(row["bytes"],sum(item["bytes"] for item in manifest["chunks"]))
            self.assertEqual(db.execute("SELECT count(*) FROM opt_data").fetchone()[0],0)
        self.assertEqual(self.service.status(self.clock)["archive_catalog"]["quality"]["snapshots"],24)
        self.assertEqual(result["chunks"],2)

    def test_completed_registration_and_comparison_replay_never_rescan_market_chunks(self):
        path,manifest,data=self.archive_files();self.enable()
        body=self.body(path=path);self.service.register_archive(body,self.clock)
        revision=self.service.status(self.clock)["revision"]
        with patch("ariadne.options_archive.inspect_archive",side_effect=AssertionError("No replay scan")):
            replay=self.service.register_archive(body,self.clock)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.service.status(self.clock)["revision"],revision)
        body=self.body();self.service.compare(body,self.clock)
        revision=self.service.status(self.clock)["revision"]
        with patch("ariadne.options_archive.iter_archive",side_effect=AssertionError("No replay history")):
            replay=self.service.compare(body,self.clock)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.service.status(self.clock)["revision"],revision)

    def test_changed_chunk_refuses_new_comparison_without_adopting_partial_history(self):
        result,path,manifest,data=self.registered();self.portfolio()
        before=self.service.status(self.clock)["paper"]
        self.registry._path(manifest["chunks"][1]["path"]).write_bytes(b"{}")
        # Passive status cites accepted catalog bytes rather than repeatedly
        # reading every private quote; a new study must revalidate all chunks.
        self.assertIsNotNone(self.service.status(self.clock)["archive_catalog"])
        with self.assertRaises(ValueError):self.service.compare(self.body(),self.clock)
        status=self.service.status(self.clock)
        self.assertEqual(status["paper"],before)
        self.assertIsNone(status["comparison"])
        self.assertEqual(status["hold"]["status"],"failed")
        with self.service.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM opt_reports").fetchone()[0],0)

    def test_changed_manifest_rejects_compare_and_catalog_db_tamper_rejects_status(self):
        result,path,manifest,data=self.registered()
        self.registry._path(path).write_bytes(b"{}")
        with self.assertRaises(ValueError):self.service.compare(self.body(),self.clock)
        with self.service.database() as db:db.execute("UPDATE opt_catalogs SET value=?",(b"{}",))
        with self.assertRaisesRegex(ValueError,"integrity"):self.service.status(self.clock)

    def test_published_comparison_tamper_blocks_status_and_replay_without_regeneration(self):
        self.registered();body=self.body();result=self.service.compare(body,self.clock)
        target=self.settings.workspace/next(item["path"] for item in result["artifacts"] if item["path"].endswith("comparison.json"))
        target.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError,"missing or changed"):self.service.status(self.clock)
        with self.assertRaisesRegex(ValueError,"missing or changed"):self.service.compare(body,self.clock)
        self.assertEqual(target.read_bytes(),b"{}")

    def test_revoked_permission_while_streaming_keeps_existing_account_and_blocks_publication(self):
        self.registered();self.portfolio();before=self.service.status(self.clock)["paper"]
        from ariadne.options_archive import iter_archive
        def revoke(*args,**kwargs):
            for index,chunk in enumerate(iter_archive(*args,**kwargs)):
                yield chunk
                if index==0:self.controls.update(paused=True,revision=2)
        with patch("ariadne.options_archive.iter_archive",side_effect=revoke),self.assertRaises(HandoffConflict):
            self.service.compare(self.body(),self.clock)
        status=self.service.status(self.clock)
        self.assertEqual(status["paper"],before)
        self.assertIsNone(status["comparison"])
        self.assertEqual(status["hold"]["status"],"failed")
        with self.service.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM opt_reports").fetchone()[0],0)

    def test_registration_permission_revocation_keeps_no_accepted_catalog(self):
        path,manifest,data=self.archive_files();self.enable()
        from ariadne.options_archive import inspect_archive
        def revoke(*args,**kwargs):
            result=inspect_archive(*args,**kwargs);self.controls.update(paused=True,revision=2);return result
        with patch("ariadne.options_archive.inspect_archive",side_effect=revoke),self.assertRaises(HandoffConflict):
            self.service.register_archive(self.body(path=path),self.clock)
        status=self.service.status(self.clock)
        self.assertIsNone(status["archive_catalog"])
        self.assertEqual(status["hold"]["status"],"failed")

    def test_new_portfolio_and_legacy_entry_points_keep_distinct_frozen_engines(self):
        self.enable();receipt=self.paper()
        old=self.service.status(self.clock)["paper"]
        self.assertEqual(old["state"]["schema"],1)
        self.assertNotEqual(old["state"]["rule_sha256"],RULE_SHA256)
        with self.assertRaises(HandoffConflict):self.portfolio()
        self.assertEqual(self.service.status(self.clock)["paper"],old)
        self.assertEqual(receipt["account_id"],old["id"])

    def test_new_portfolio_replay_and_restart_keep_only_one_500_account(self):
        self.enable();body=self.body(acknowledge_simulated=True)
        result=self.service.start_portfolio(body,self.clock);before=self.service.status(self.clock)["paper"]
        self.restart();replay=self.service.start_portfolio(body,self.clock)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.service.status(self.clock)["paper"],before)
        self.assertEqual(before["state"]["schema"],2)
        self.assertEqual(before["state"]["enabled_strategies"],list(STRATEGY_IDS))
        self.assertEqual(before["state"]["contributions"],"500")
        with self.service.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM opt_accounts").fetchone()[0],1)
        self.assertEqual(result["monthly_loss_allowance"],"500.00")

    def test_upgrade_preserves_same_id_old_start_funding_and_immutable_witness(self):
        self.enable();self.paper();before=self.service.status(self.clock)["paper"]
        self.clock+=timedelta(hours=1);body=self.body(acknowledge_simulated=True)
        result=self.service.upgrade_portfolio(body,self.clock);after=self.service.status(self.clock)["paper"]
        self.assertEqual(after["id"],before["id"])
        self.assertEqual(after["started_at"],before["started_at"])
        for key in ("cash","contributions","month","started_at","monthly_start_equity","monthly_committed"):
            self.assertEqual(after["state"][key],before["state"][key])
        self.assertEqual(after["state"]["portfolio_activation_at"],self.clock.isoformat())
        self.assertEqual(result["simulated_funding_added"],"0")
        self.assertFalse(result["monthly_budget_reset"])
        with self.service.database() as db:
            witness=db.execute("SELECT * FROM opt_migrations").fetchone()
            self.assertEqual(json.loads(witness["old_state"]),before["state"])
            self.assertEqual(witness["old_sha256"],hashlib.sha256(witness["old_state"].encode()).hexdigest())
            self.assertEqual(witness["old_sha256"],result["legacy_state_sha256"])
            self.assertEqual(db.execute("SELECT count(*) FROM opt_accounts").fetchone()[0],1)
        self.restart();self.assertEqual(self.service.status(self.clock)["paper"],after)
        replay=self.service.upgrade_portfolio(body,self.clock)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.service.status(self.clock)["paper"],after)
        self.assertEqual(self.service.status(self.clock)["events"][0]["simulated_funding_added"],"0")

    def test_upgrade_witness_tamper_fails_passive_read_without_repair(self):
        self.enable();self.paper();self.service.upgrade_portfolio(self.body(acknowledge_simulated=True),self.clock)
        with self.service.database() as db:db.execute("UPDATE opt_migrations SET old_state='{}'")
        self.restart()
        with self.assertRaisesRegex(ValueError,"witness"):self.service.status(self.clock)
        with self.service.database() as db:self.assertEqual(db.execute("SELECT old_state FROM opt_migrations").fetchone()[0],"{}")

    def test_upgrade_rejects_any_forward_observation_and_does_not_fund_another_account(self):
        self.enable();self.paper();self.write(path=INBOX)
        self.service.refresh(self.body(),self.clock);before=self.service.status(self.clock)["paper"]
        with self.assertRaises(HandoffConflict):self.service.upgrade_portfolio(self.body(acknowledge_simulated=True),self.clock)
        self.assertEqual(self.service.status(self.clock)["paper"],before)
        with self.service.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM opt_migrations").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM opt_accounts").fetchone()[0],1)

    def test_upgrade_refuses_nonpristine_budget_or_changed_permission_epoch(self):
        self.enable();self.paper();before=self.service.status(self.clock)["paper"]
        used=deepcopy(before["state"]);used["monthly_committed"]="1"
        with self.service.database() as db:db.execute("UPDATE opt_accounts SET state=?",(json.dumps(used),))
        with self.assertRaises(ValueError):self.service.upgrade_portfolio(self.body(acknowledge_simulated=True),self.clock)
        with self.service.database() as db:
            self.assertEqual(json.loads(db.execute("SELECT state FROM opt_accounts").fetchone()[0]),used)
            self.assertEqual(db.execute("SELECT count(*) FROM opt_migrations").fetchone()[0],0)
            db.execute("UPDATE opt_accounts SET state=?",(json.dumps(before["state"]),))
        self.controls["revision"]=2
        with self.assertRaisesRegex(HandoffConflict,"permission epoch"):self.service.upgrade_portfolio(self.body(acknowledge_simulated=True),self.clock)

    def test_acknowledgment_required_and_global_pause_cannot_start_or_upgrade(self):
        self.enable()
        with self.assertRaises(ValueError):self.service.start_portfolio(self.body(acknowledge_simulated=False),self.clock)
        self.paper();self.store.set_setting("paused",True)
        with self.assertRaises(HandoffConflict):self.service.upgrade_portfolio(self.body(acknowledge_simulated=True),self.clock)
        with self.assertRaises(HandoffConflict):self.portfolio()
        self.assertEqual(self.service.status(self.clock)["paper"]["state"]["schema"],1)

    def test_forward_mixed_strategies_and_restart_keep_exact_shared_500_cap(self):
        data=mixed_strategies_month();data["evidence_kind"]="historical_quotes"
        self.clock=datetime.fromisoformat(through(data,13)["as_of"]);self.enable();self.portfolio()
        for count in range(13,len(data["snapshots"])+1):
            current=through(data,count);self.clock=datetime.fromisoformat(current["as_of"])
            self.write(current,path=INBOX)
            self.service.refresh(self.body(),self.clock)
            if count==29:self.restart()
        before=self.service.status(self.clock)["paper"]
        self.assertEqual(before["summary"]["monthly_budget_used"],"500")
        self.assertEqual(before["summary"]["monthly_budget_remaining"],"0")
        self.assertEqual({key for key,item in before["state"]["strategy_stats"].items() if Decimal(item["monthly_committed"])>0},set(STRATEGY_IDS))
        self.restart();self.assertEqual(self.service.status(self.clock)["paper"],before)
        unchanged=self.service.refresh(self.body(),self.clock)
        self.assertEqual(unchanged["paper_events"],0)
        self.assertEqual(self.service.status(self.clock)["paper"],before)
        self.service.archive(self.body(reason="Retain exact shared risk proof",archive_data=False),self.clock)
        with self.assertRaisesRegex(HandoffConflict,"already belongs"):self.portfolio()
        with self.assertRaisesRegex(HandoffConflict,"already belongs"):self.paper()


if __name__=="__main__":unittest.main()
