"""Protected original scenarios and own-business evidence; synthetic fixtures only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.atomic import publish_bytes
from ariadne.config import Settings
from ariadne.handoff import HandoffConflict
from ariadne.revenue_workflow import RevenueWorkbench
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from test_revenue_planner import fixture

NOW = datetime(2026, 10, 30, 20, tzinfo=timezone.utc)


class RevenueWorkflowTests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/"runtime/tmp";parent.mkdir(parents=True,exist_ok=True)
        temp=tempfile.TemporaryDirectory(prefix="revenue-workflow-",dir=parent);self.addCleanup(temp.cleanup)
        self.settings=Settings(data_dir=Path(temp.name)/"data")
        self.store=Store(self.settings.data_dir/"ariadne.sqlite3");self.store.set_setting("paused",False)
        self.registry=ToolRegistry(self.settings);self.service=RevenueWorkbench(self.registry,self.store)
        self.count=0

    def body(self,**extra):
        self.count+=1
        return {"request_id":"revenue-test-"+str(self.count),"expected_revision":self.service.status(NOW)["revision"],**extra}

    def plan(self,scenario=None):
        self.plan_body=self.body(scenario=fixture() if scenario is None else scenario)
        self.plan_receipt=self.service.plan(self.plan_body,NOW)
        self.scenario_id=self.plan_receipt["scenario_id"]
        return self.plan_receipt

    def event(self,kind,*,amount=None,minutes=None,units=None,reference_path=None,occurred_at=None,scenario_id=None):
        return {"scenario_id":self.scenario_id if scenario_id is None else scenario_id,"kind":kind,
            "occurred_at":occurred_at or (NOW-timedelta(minutes=1)).isoformat(),"amount":amount,"minutes":minutes,
            "units":units,"note":"Owner-reported regression fixture, not independently verified","reference_path":reference_path}

    def evidence(self,kind,**kwargs):
        self.evidence_body=self.body(event=self.event(kind,**kwargs))
        return self.service.record_evidence(self.evidence_body,NOW)

    def test_passive_status_does_not_seed_tables_or_scenarios(self):
        with self.store.connect() as db:before=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        with patch("socket.socket",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No model")):
            first,second=self.service.status(NOW),self.service.status(NOW)
        self.assertEqual(first,second)
        self.assertEqual(first["scenarios"],[]);self.assertEqual(first["evidence"],[])
        self.assertIsNone(first["selected_scenario_id"])
        self.assertEqual(first["cash_summary"]["status"],"no_evidence")
        with self.store.connect() as db:self.assertEqual(before,[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")])

    def test_plan_is_immutable_and_completed_replay_never_recalculates(self):
        with patch("socket.socket",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No model")):
            receipt=self.plan()
        before=self.service.status(NOW)
        self.assertEqual(receipt["status"],"completed");self.assertEqual(len(receipt["artifacts"]),2)
        self.assertTrue(before["plan"]["planning_only"])
        with patch("ariadne.revenue_planner.calculate",side_effect=AssertionError("No replay")):
            repeated=self.service.plan(self.plan_body,NOW+timedelta(days=1))
        self.assertTrue(repeated["replayed"])
        self.assertEqual(self.service.status(NOW),before)
        for item in receipt["artifacts"]:
            raw=self.registry._path(item["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),item["sha256"])

    def test_detail_of_older_plan_does_not_select_or_recalculate(self):
        self.plan();first=self.scenario_id
        scenario=fixture();scenario["label"]="Another unvalidated scenario"
        self.plan(scenario);second=self.scenario_id
        before=self.service.status(NOW)
        with patch("ariadne.revenue_planner.calculate",side_effect=AssertionError("No recomputation")):
            old=self.service.detail(first)
        self.assertEqual(old["id"],first)
        self.assertEqual(old["scenario"]["label"],fixture()["label"])
        self.assertEqual(self.service.status(NOW)["selected_scenario_id"],second)
        self.assertEqual(self.service.status(NOW),before)

    def test_cash_movement_excludes_sales_does_not_deduct_budget_or_owner_hours(self):
        self.plan()
        self.evidence("sale_reported",amount="100.00",units=2)
        self.evidence("payout_received",amount="80.00")
        self.evidence("cost_paid",amount="10.00")
        self.evidence("refund_paid",amount="5.00")
        self.evidence("hours_logged",minutes=90)
        self.evidence("interview")
        self.evidence("pilot_agreed")
        cash=self.service.status(NOW)["cash_summary"]
        self.assertEqual(cash["sale_reported"],"100.00")
        self.assertEqual(cash["payout_received"],"80.00")
        self.assertEqual(cash["net_reported_cash"],"65.00")
        self.assertEqual(cash["owner_minutes_logged"],90)
        self.assertEqual(cash["units_reported"],2)
        self.assertEqual((cash["interviews"],cash["pilot_agreements"]),(1,1))
        self.assertFalse(cash["independently_verified"]);self.assertFalse(cash["cash_costs_complete"])
        self.assertIn("Do not deduct",cash["scope"])
        self.assertEqual(self.service.status(NOW)["plan"]["system_budget_monthly"],"100.00")

    def test_occurrence_month_is_utc_not_storage_month_or_original_offset_month(self):
        self.plan()
        next_month=datetime(2026,11,1,12,tzinfo=timezone.utc)
        body=self.body(event=self.event("payout_received",amount="50.00",occurred_at="2026-11-01T00:30:00+02:00"))
        self.service.record_evidence(body,next_month)
        self.assertEqual(self.service.status(next_month)["cash_summary"]["payout_received"],"0.00")
        october=self.service.status(NOW)["cash_summary"]
        self.assertEqual(october["payout_received"],"50.00");self.assertEqual(october["timezone"],"UTC")

    def test_evidence_validates_exact_applicable_fields_and_future_dates(self):
        self.plan();before=self.service.status(NOW)
        invalid=[self.event("payout_received",amount="0.00"),self.event("payout_received",amount="10"),
            self.event("payout_received",amount="-1.00"),self.event("sale_reported",amount="10.00"),
            self.event("interview",amount="10.00"),self.event("hours_logged",minutes=True),
            self.event("hours_logged",minutes=10,units=1),self.event("interview",occurred_at=(NOW+timedelta(seconds=1)).isoformat())]
        for event in invalid:
            with self.subTest(event=event),self.assertRaises(ValueError):self.service.record_evidence(self.body(event=event),NOW)
        self.assertEqual(self.service.status(NOW),before)

    def test_reference_is_private_retained_bytes_not_a_live_file_dependency_on_replay(self):
        self.plan();path=self.settings.workspace/"evidence/payment.txt";path.parent.mkdir(parents=True);path.write_bytes(b"owner supplied receipt fixture")
        receipt=self.evidence("payout_received",amount="42.00",reference_path="evidence/payment.txt")
        path.write_bytes(b"changed workspace copy")
        with patch.object(self.service,"_read",wraps=self.service._read) as reads:
            replay=self.service.record_evidence(self.evidence_body,NOW)
        self.assertTrue(replay["replayed"])
        self.assertNotIn("evidence/payment.txt",[call.args[0] for call in reads.call_args_list])
        row=self.service.status(NOW)["evidence"][0]
        self.assertEqual(row["source"]["reference_sha256"],hashlib.sha256(b"owner supplied receipt fixture").hexdigest())
        self.assertEqual(receipt["owner_reported"],True)

    def test_absolute_or_traversal_support_is_never_accepted(self):
        self.plan();before=self.service.status(NOW)
        for path in ("E:/never-open", "../outside", str(self.settings.workspace/"inside.txt")):
            with self.subTest(path=path),self.assertRaises(ValueError):self.service.record_evidence(self.body(event=self.event("interview",reference_path=path)),NOW)
        self.assertEqual(self.service.status(NOW),before)

    def test_hardlinked_support_is_rejected_before_read_and_retained_sources_stay_private(self):
        import os
        self.plan()
        original=self.settings.workspace/"original.txt";original.write_bytes(b"private support")
        linked=self.settings.workspace/"linked.txt";os.link(original,linked)
        with self.assertRaises(ValueError):self.service.record_evidence(self.body(event=self.event("interview",reference_path="linked.txt")),NOW)
        self.assertEqual(self.service.status(NOW)["evidence"],[])

    def test_oversized_support_stops_with_durable_failure_without_any_cash_evidence(self):
        self.plan();path=self.settings.workspace/"oversized.bin";path.write_bytes(b"x"*(256*1024+1))
        body=self.body(event=self.event("payout_received",amount="10.00",reference_path="oversized.bin"))
        with self.assertRaises(ValueError):self.service.record_evidence(body,NOW)
        status=self.service.status(NOW)
        self.assertEqual(status["evidence"],[])
        self.assertEqual(status["cash_summary"]["payout_received"],"0.00")
        self.assertEqual(status["hold"]["request_id"],body["request_id"])

    def test_excessive_stored_metadata_is_rejected_before_decoding_a_plan(self):
        self.plan()
        with self.service.database() as db:db.execute("UPDATE revenue_scenarios SET plan=?",("x"*(128*1024+1),))
        status=self.service.status(NOW)
        self.assertEqual(status["hold"]["status"],"integrity_error")
        self.assertIsNone(status["plan"])

    def test_failed_input_never_replays_and_inspection_ack_only_allows_new_work(self):
        self.plan();bad=self.body(event=self.event("interview",reference_path="missing.txt"))
        with self.assertRaises(FileNotFoundError):self.service.record_evidence(bad,NOW)
        held=self.service.status(NOW)
        self.assertEqual(held["hold"]["request_id"],bad["request_id"])
        with self.assertRaises(HandoffConflict):self.service.record_evidence(bad,NOW)
        with self.assertRaises(HandoffConflict):self.service.record_evidence(self.body(event=self.event("interview")),NOW)
        ack=self.body(failed_request_id=bad["request_id"],reason="Inspected missing local input; no output exists")
        result=self.service.acknowledge(ack,NOW)
        self.assertFalse(result["adopted"])
        self.assertIsNone(self.service.status(NOW)["hold"])
        self.assertEqual(self.service.status(NOW)["evidence"],[])
        with self.assertRaises(HandoffConflict):self.service.record_evidence(bad,NOW)
        self.assertTrue(self.service.acknowledge(ack,NOW)["replayed"])
        self.evidence("interview")

    def test_interrupted_publication_remains_held_and_partial_files_never_adopted(self):
        self.plan();body=self.body(event=self.event("interview"))
        with patch("ariadne.revenue_workflow.publish_bytes",side_effect=SystemExit("interrupted")):
            with self.assertRaises(SystemExit):self.service.record_evidence(body,NOW)
        held=self.service.status(NOW)
        self.assertEqual(held["hold"]["status"],"running")
        self.assertEqual(held["evidence"],[])
        with self.assertRaises(HandoffConflict):self.service.record_evidence(body,NOW)

    def test_existing_output_folder_is_not_reused_even_when_empty(self):
        self.plan();body=self.body(scenario=fixture())
        self.registry._path("reports/revenue-workbench/"+body["request_id"]).mkdir()
        with self.assertRaises(FileExistsError):self.service.plan(body,NOW)
        self.assertEqual(self.service.status(NOW)["selected_scenario_id"],self.scenario_id)
        self.assertEqual(self.service.status(NOW)["hold"]["request_id"],body["request_id"])

    def test_pause_before_request_and_pause_on_off_during_publish_revoke_authority(self):
        self.store.set_setting("paused",True)
        with self.assertRaises(HandoffConflict):self.plan()
        self.store.set_setting("paused",False);self.plan()
        body=self.body(event=self.event("interview"))
        def publish(path,raw,**kwargs):
            self.store.set_setting("paused",True);self.store.set_setting("paused",False)
            return publish_bytes(path,raw,**kwargs)
        with patch("ariadne.revenue_workflow.publish_bytes",side_effect=publish),self.assertRaises(HandoffConflict):self.service.record_evidence(body,NOW)
        self.assertEqual(self.service.status(NOW)["evidence"],[])
        self.assertEqual(self.service.status(NOW)["hold"]["request_id"],body["request_id"])

    def test_older_artifact_corruption_stops_new_work_before_computation(self):
        first=self.plan();self.plan()
        artifact=next(item for item in first["artifacts"] if item["path"].endswith("scenario.json"))
        self.registry._path(artifact["path"]).write_bytes(b'{}')
        body=self.body(scenario=fixture())
        with patch("ariadne.revenue_planner.calculate",side_effect=AssertionError("No compute")),self.assertRaises(ValueError):self.service.plan(body,NOW)
        with self.service.database() as db:self.assertEqual(db.execute("SELECT status FROM revenue_runs WHERE request_id=?",(body["request_id"],)).fetchone()[0],"failed")

    def test_new_input_tamper_before_commit_fails_without_new_selection(self):
        self.plan();old=self.scenario_id;body=self.body(scenario=fixture())
        def publish(path,raw,**kwargs):
            result=publish_bytes(path,raw,**kwargs)
            if path.name=="plan.json":
                with self.service.database() as db:
                    digest=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
                    row=db.execute("SELECT value FROM revenue_inputs WHERE digest=?",(digest,)).fetchone()
                    data=bytes(row[0]);db.execute("UPDATE revenue_inputs SET value=? WHERE digest=?",(b"x"+data[1:],digest))
            return result
        with patch("ariadne.revenue_workflow.publish_bytes",side_effect=publish),self.assertRaises(ValueError):self.service.plan(body,NOW)
        with self.service.database() as db:
            self.assertEqual(db.execute("SELECT selected_id FROM revenue_meta").fetchone()[0],old)
            self.assertEqual(db.execute("SELECT count(*) FROM revenue_scenarios").fetchone()[0],1)

    def test_selected_full_source_and_cash_evidence_are_verified_for_brief(self):
        self.plan();self.evidence("payout_received",amount="100.00")
        value=self.service.brief_snapshot(NOW)
        self.assertEqual(value["status"],"available");self.assertEqual(value["cash_summary"]["net_reported_cash"],"100.00")
        item=self.service.status(NOW)["last_run"]["artifacts"][0]
        self.registry._path(item["path"]).write_bytes(b'{}')
        broken=self.service.brief_snapshot(NOW)
        self.assertEqual(broken["status"],"unavailable");self.assertIsNone(broken["cash_summary"]["net_reported_cash"])

    def test_status_rejects_changed_payout_artifact_without_inventing_zero_cash(self):
        self.plan();receipt=self.evidence("payout_received",amount="77.00")
        self.assertEqual(self.service.status(NOW)["cash_summary"]["net_reported_cash"],"77.00")
        item=receipt["artifacts"][0]
        self.registry._path(item["path"]).write_bytes(b'{}')
        with self.service.database() as db:before=list(db.iterdump())
        status=self.service.status(NOW)
        self.assertEqual(status["hold"]["status"],"integrity_error")
        self.assertEqual(status["cash_summary"]["status"],"unavailable")
        self.assertIsNone(status["cash_summary"]["payout_received"])
        self.assertIsNone(status["cash_summary"]["net_reported_cash"])
        self.assertEqual(status["evidence"],[])
        with self.service.database() as db:self.assertEqual(list(db.iterdump()),before)

    def test_status_rehashes_same_length_retained_reference_and_preserves_existing_hold(self):
        self.plan();path=self.settings.workspace/"payment.txt";raw=b"owner supplied payment fixture";path.write_bytes(raw)
        self.evidence("payout_received",amount="77.00",reference_path="payment.txt")
        failed=self.body(event=self.event("interview",reference_path="missing.txt"))
        with self.assertRaises(FileNotFoundError):self.service.record_evidence(failed,NOW)
        with self.service.database() as db:
            db.execute("UPDATE revenue_inputs SET value=? WHERE digest=?",(b"x"+raw[1:],hashlib.sha256(raw).hexdigest()))
        status=self.service.status(NOW)
        self.assertEqual(status["hold"]["request_id"],failed["request_id"])
        self.assertEqual(status["hold"]["status"],"failed")
        self.assertEqual(status["cash_summary"]["status"],"unavailable")
        self.assertIsNone(status["cash_summary"]["net_reported_cash"])
        self.assertIsNone(status["cash_summary"]["evidence_count"])

    def test_oversized_request_metadata_is_rejected_before_receipt_decoding(self):
        self.plan()
        with self.service.database() as db:
            db.execute("UPDATE revenue_runs SET fingerprint=?",("x"*(1024*1024),))
        with patch("ariadne.revenue_workflow._load",side_effect=AssertionError("Oversized metadata must be rejected before JSON decoding")):
            with self.assertRaisesRegex(ValueError,"metadata field exceeds"):
                self.service.status(NOW)

    def test_oversized_held_identifier_and_pause_authority_fail_closed(self):
        self.plan();body=self.body(event=self.event("interview",reference_path="missing.txt"))
        with self.assertRaises(FileNotFoundError):self.service.record_evidence(body,NOW)
        with self.service.database() as db:
            db.execute("UPDATE revenue_runs SET request_id=? WHERE request_id=?",("x"*(1024*1024),body["request_id"]))
        with self.assertRaisesRegex(ValueError,"held revenue request identity"):
            self.service.status(NOW)
        with self.service.database() as db:
            db.execute("UPDATE settings SET value=? WHERE key='paused'",("x"*(1024*1024),))
            before=db.execute("SELECT count(*) FROM revenue_runs").fetchone()[0]
        request={"request_id":"bounded-pause-test","expected_revision":4,"scenario":fixture()}
        with self.assertRaises(HandoffConflict):self.service.plan(request,NOW)
        with self.service.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM revenue_runs").fetchone()[0],before)

    def test_nonobject_saved_receipts_and_evidence_raise_supported_integrity_errors(self):
        self.plan();self.evidence("payout_received",amount="77.00")
        with self.service.database() as db:
            db.execute("UPDATE revenue_evidence SET source='[]'")
        status=self.service.status(NOW)
        self.assertEqual(status["cash_summary"]["status"],"unavailable")
        self.assertIsNone(status["cash_summary"]["net_reported_cash"])
        with self.service.database() as db:
            db.execute("UPDATE revenue_runs SET receipt='[]',bytes=2 WHERE request_id=?",(self.evidence_body["request_id"],))
        with self.assertRaises(ValueError):self.service.brief_snapshot(NOW)

    def test_receipt_or_manifest_tampering_never_becomes_valid_evidence(self):
        self.plan();self.evidence("interview")
        with self.service.database() as db:
            row=db.execute("SELECT id,source FROM revenue_evidence").fetchone();source=json.loads(row["source"])
            source["reference_scope"]="Invented independent verification"
            db.execute("UPDATE revenue_evidence SET source=? WHERE id=?",(json.dumps(source),row["id"]))
        status=self.service.status(NOW)
        self.assertEqual(status["hold"]["status"],"integrity_error")
        self.assertEqual(status["cash_summary"]["status"],"unavailable")

    def test_report_and_request_quotas_hold_without_deleting_history(self):
        self.plan();before=self.service.status(NOW)
        with patch("ariadne.revenue_workflow.MAX_REPORT_TOTAL",1),self.assertRaises(ValueError):self.evidence("interview")
        with self.service.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM revenue_scenarios").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM revenue_evidence").fetchone()[0],0)
        self.assertEqual(before["scenarios"][0]["id"],self.scenario_id)

    def test_global_lock_and_scenario_identity_reject_concurrent_or_unknown_work(self):
        self.plan();other=RevenueWorkbench(self.registry,self.store)
        with self.service._ownership(),self.assertRaises(ValueError):other.record_evidence(self.body(event=self.event("interview")),NOW)
        with self.assertRaises(ValueError):self.service.record_evidence(self.body(event=self.event("interview",scenario_id="missing")),NOW)
