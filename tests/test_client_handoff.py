"""Real client drafts: exact money, evidence lifecycle, isolation and crash stops."""
import copy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

from ariadne import client_handoff as workflow
from ariadne.client_handoff import ClientHandoffDesk, ClientHandoffConflict
from ariadne.config import Settings, _allowed_path
from ariadne.handoff import HandoffDesk
from ariadne.store import Store
from ariadne.tools import ToolRegistry

ROOT=Path(__file__).resolve().parents[1]
NOW=datetime(2026,10,30,16,tzinfo=timezone.utc)


def client(identifier="provider-1"):
    return {"id":identifier,"name":"Example Service LLC","address":"10 Main Street","email":None,
            "contact":None,"notes":"Owner-supplied service business details.","archived":False}


def intake(identifier="job-1",client_id="provider-1"):
    return {"id":identifier,"client_id":client_id,"title":"Repair service",
        "customer":{"name":"A Customer","address":"20 Main Street","email":None},
        "source_notes":"Owner supplied request.\nScope and rate evidence retained.",
        "service_date":"2026-10-29","document_date":"2026-10-29","due_date":"2026-11-10",
        "lines":[{"description":"Repair work","quantity":"2.5","unit":"hours","unit_price":"35.00",
                  "price_approved":True,"source_ref":"Owner supplied rate sheet"}],
        "tax":{"bps":0,"approved":True,"source_ref":"Owner supplied tax instruction"},
        "permission":{"confirmed":True,"source_ref":"Owner reported client permission and service scope"}}


class ClientHandoffTests(unittest.TestCase):
    def setUp(self):
        parent=_allowed_path(ROOT/"runtime/tmp");parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="client-handoff-unit-",dir=parent)
        self.base=_allowed_path(temporary.name)
        def cleanup():
            path=_allowed_path(temporary.name)
            if path.parent!=parent or not path.name.startswith("client-handoff-unit-"):
                raise ValueError("Refusing cleanup outside this generated fixture.")
            temporary.cleanup()
        self.addCleanup(cleanup)
        clock=mock.patch("ariadne.client_handoff._now",return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.settings=Settings(data_dir=self.base/"private",workspace=self.base/"workspace")
        self.store=Store(self.settings.data_dir/"ariadne.sqlite3")
        self.registry=ToolRegistry(self.settings)
        self.desk=ClientHandoffDesk(self.registry,self.store)
        self.sequence=0

    def call(self,method,**arguments):
        self.sequence+=1
        return getattr(self.desk,method)({"request_id":"request-"+str(self.sequence),
            "expected_revision":self.desk.status()["revision"],**arguments})

    def quote(self,source=None):
        value=source or intake()
        if not self.desk.status()["totals"]["clients"]:self.call("save_client",client=client(value["client_id"]))
        saved=self.call("create_intake",intake=value)
        quote=self.call("generate_quote",intake_id=value["id"],content_sha256=saved["content_sha256"])
        return self.document(quote["entity_id"])

    def document(self,identifier):return self.desk.get_document(identifier)["document"]

    def raw_document(self,identifier):
        with self.desk.database() as db:return self.desk._document(db,identifier)[0]

    def review(self,document,decision="approve"):
        return self.call("review",document_id=document["id"],content_sha256=document["content_sha256"],decision=decision)

    def accept(self,document,**changes):
        return self.call("accept_quote",document_id=document["id"],content_sha256=document["content_sha256"],
            **{"accepted":True,"observed_at":"2026-10-29T18:00:00+00:00","source_ref":"Owner supplied customer reply",
               "contact":"Customer named in reply","note":"Owner-reported; no identity verification.",**changes})

    def invoice(self,document,**changes):
        return self.call("create_invoice",quote_id=document["id"],content_sha256=document["content_sha256"],
            **{"service_date":"2026-10-29","document_date":"2026-10-30","due_date":"2026-11-15",
               "work":{"completed":True,"approved":True,"source_ref":"Owner supplied completion record"},**changes})

    def accepted_quote(self):
        document=self.quote();self.review(document);self.accept(document)
        return self.document(document["id"])

    def test_passive_status_is_blank_does_not_initialize_or_seed_ledger(self):
        value=self.desk.status()
        self.assertEqual(value["revision"],0);self.assertFalse(value["initialized"]);self.assertFalse(value["demo_only"])
        self.assertEqual(value["totals"],{"clients":0,"intakes":0,"documents":0})
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='client_handoff_meta'").fetchone())
            self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0],0)
        self.assertFalse((self.settings.data_dir/"business.sqlite3").exists())
        self.assertFalse(list(self.settings.workspace.iterdir()))

    def test_owner_workflow_is_offline_with_no_model_process_or_external_effects(self):
        with mock.patch.object(socket,"socket",side_effect=AssertionError("No network")),mock.patch("subprocess.Popen",side_effect=AssertionError("No process/model")):
            quote=self.accepted_quote();saved=self.invoice(quote);invoice=self.document(saved["entity_id"])
            self.review(invoice);result=self.call("export",document_id=invoice["id"],content_sha256=invoice["content_sha256"])
        self.assertEqual(result["status"],"completed")
        self.assertEqual((result["model_calls"],result["network_calls"]),(0,0))
        self.assertFalse(result["sent"]);self.assertFalse(result["posted"]);self.assertFalse(result["payment_received"])
        self.assertFalse((self.settings.data_dir/"business.sqlite3").exists())

    def test_decimal_quantity_and_tax_round_per_line_then_total(self):
        source=intake();source["lines"]=[{**source["lines"][0],"quantity":"0.5","unit_price":"0.01"} for _ in range(3)]
        source["tax"]["bps"]=5000
        document=self.quote(source)
        self.assertEqual([row["line_total_cents"] for row in document["lines"]],["1","1","1"])
        self.assertEqual((document["subtotal_cents"],document["tax_cents"],document["total_cents"]),("3","2","5"))
        ordinary=self.raw_document(document["id"])
        for quantity in ("0.001","0.049","0.5","1.005","2.5","10000"):
            for rate in ("0.01","1.15","35.33"):
                ordinary["lines"][0].update(quantity=quantity,unit_price=rate)
                expected=int((Decimal(quantity)*Decimal(rate)*100).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
                self.assertEqual(workflow._assess(ordinary)["lines"][0]["line_total_cents"],str(expected))

    def test_quantity_requires_bounded_exact_decimal_text(self):
        for value in (2.5,2,True,"0","-1","0.0001","1e3","01","1.","10000.001","NaN"):
            with self.subTest(value=value),self.assertRaises(ValueError):workflow._quantity(value)
        self.assertEqual(workflow._quantity("2.500"),"2.5")
        self.assertEqual(workflow._quantity("10000.000"),"10000")

    def test_money_stays_exact_if_another_component_changes_decimal_precision(self):
        quote=self.quote();raw=self.raw_document(quote["id"])
        raw["lines"][0].update(quantity="1.005",unit_price="35.33")
        raw["tax"]["bps"]=825
        with localcontext() as context:
            context.prec=1
            assessed=workflow._assess(raw)
        self.assertEqual(assessed["subtotal_cents"],"3551")
        self.assertEqual(assessed["tax_cents"],"293")
        self.assertEqual(assessed["total_cents"],"3844")

    def test_unknown_values_do_not_infer_price_tax_or_permission(self):
        source=intake();source["lines"][0].update(unit_price=None,price_approved=False,source_ref=None)
        source["tax"]={"bps":None,"approved":False,"source_ref":None};source["permission"]={"confirmed":False,"source_ref":None}
        document=self.quote(source)
        self.assertEqual(document["status"],"held")
        self.assertIsNone(document["subtotal_cents"]);self.assertIsNone(document["tax_cents"]);self.assertIsNone(document["total_cents"])
        with self.assertRaises(ClientHandoffConflict):self.review(document)
        with self.assertRaises(ClientHandoffConflict):self.call("export",document_id=document["id"],content_sha256=document["content_sha256"])

    def test_unknown_tax_preserves_known_subtotal_but_withholds_final_total(self):
        source=intake();source["tax"]["bps"]=None
        document=self.quote(source)
        self.assertEqual(document["subtotal_cents"],"8750");self.assertIsNone(document["total_cents"])

    def test_reject_then_correct_then_approve_clears_old_review(self):
        document=self.quote();self.review(document,"reject")
        rejected=self.document(document["id"]);self.assertEqual(rejected["status"],"rejected")
        self.call("revise_document",document_id=document["id"],content_sha256=document["content_sha256"],changes={"title":"Corrected repair scope"})
        corrected=self.document(document["id"])
        self.assertIsNone(corrected["review"]);self.assertIsNone(corrected["acceptance"])
        self.assertEqual(corrected["revision"],2);self.assertEqual(corrected["history_retained"],2)
        self.assertTrue(any(row.get("details",{}).get("review",{}).get("decision")=="reject" for row in corrected["events"]))
        with self.assertRaises(ClientHandoffConflict):self.review(document)
        self.review(corrected);self.assertEqual(self.document(document["id"])["status"],"approved")

    def test_provider_profile_edit_never_changes_historic_intake_or_approved_draft(self):
        document=self.quote();self.review(document)
        before=copy.deepcopy(self.raw_document(document["id"]));self.call("save_client",client={**client(),"name":"Updated Provider"})
        self.assertEqual(self.raw_document(document["id"]),before)
        self.assertEqual(self.desk.get_intake("job-1")["intake"]["provider"]["name"],"Example Service LLC")
        self.assertEqual(self.document(document["id"])["status"],"approved")
        self.call("revise_document",document_id=document["id"],content_sha256=document["content_sha256"],changes={},refresh_client=True)
        current=self.document(document["id"]);self.assertEqual(current["provider"]["name"],"Updated Provider")
        self.assertIsNone(current["review"]);self.assertNotEqual(current["content_sha256"],document["content_sha256"])

    def test_archive_preserves_history_and_blocks_new_intake_and_generation(self):
        self.call("save_client",client=client());saved=self.call("create_intake",intake=intake())
        self.call("save_client",client={**client(),"archived":True})
        self.assertFalse(self.desk.get_intake("job-1")["intake"]["can_generate_quote"])
        with self.assertRaises(ClientHandoffConflict):self.call("create_intake",intake=intake("job-2"))
        with self.assertRaises(ClientHandoffConflict):self.call("generate_quote",intake_id="job-1",content_sha256=saved["content_sha256"])
        self.assertEqual(self.desk.status()["totals"]["intakes"],1)

    def test_acceptance_is_separate_owner_reported_evidence_and_not_inferred(self):
        document=self.quote()
        with self.assertRaises(ClientHandoffConflict):self.accept(document)
        self.review(document)
        with self.assertRaises(ClientHandoffConflict):self.invoice(document)
        self.accept(document,accepted=False)
        self.assertFalse(self.document(document["id"])["can_invoice"])
        self.accept(document)
        accepted=self.document(document["id"])
        self.assertTrue(accepted["can_invoice"]);self.assertTrue(accepted["acceptance"]["owner_reported"])
        self.assertFalse(accepted["acceptance"]["independently_verified"])
        self.call("revise_document",document_id=document["id"],content_sha256=document["content_sha256"],changes={"source_notes":"Corrected original scope source"})
        current=self.document(document["id"])
        self.assertIsNone(current["acceptance"]);self.assertIsNone(current["review"])
        with self.assertRaises(ClientHandoffConflict):self.invoice(current)

    def test_acceptance_times_require_timezone_nonfuture_and_quote_validity(self):
        document=self.quote();self.review(document)
        for observed in ("2026-10-29T18:00:00","2026-10-31T00:00:00+00:00","2026-10-28T18:00:00+00:00"):
            with self.subTest(observed=observed),self.assertRaises(ValueError):self.accept(document,observed_at=observed)
        # Chicago is still the previous day at 01:00 UTC.
        with self.assertRaises(ValueError):self.accept(document,observed_at="2026-10-29T01:00:00+00:00")
        self.accept(document,observed_at="2026-10-30T01:00:00+00:00")
        self.assertEqual(self.document(document["id"])["acceptance"]["observed_at"],"2026-10-30T01:00:00+00:00")

    def test_completion_evidence_and_actual_invoice_dates_are_required(self):
        document=self.accepted_quote()
        for changes in ({"work":{"completed":False,"approved":True,"source_ref":"Not completed"}},
                        {"work":{"completed":True,"approved":False,"source_ref":"Unreviewed"}},
                        {"work":{"completed":True,"approved":True,"source_ref":None}},
                        {"service_date":"2026-10-31"},{"document_date":"2026-10-28"},{"document_date":"2026-10-31"},
                        {"due_date":"2026-10-01"},{"service_date":None}):
            with self.subTest(changes=changes),self.assertRaises(ClientHandoffConflict):self.invoice(document,**changes)
        self.assertEqual(self.desk.status()["totals"]["documents"],1)

    def test_invoice_copies_exact_accepted_pricing_and_locks_source_until_void(self):
        quote=self.accepted_quote();saved=self.invoice(quote);invoice=self.document(saved["entity_id"])
        for key in ("provider","customer","lines","tax","permission"):
            self.assertEqual(invoice[key],quote[key])
        self.assertEqual(invoice["parent"]["content_sha256"],quote["content_sha256"])
        self.assertEqual(invoice["parent"]["acceptance"],quote["acceptance"])
        self.assertEqual(invoice["status"],"ready_for_review");self.assertIsNone(invoice["review"])
        with self.assertRaises(ClientHandoffConflict):self.invoice(quote)
        with self.assertRaises(ClientHandoffConflict):self.call("revise_document",document_id=quote["id"],content_sha256=quote["content_sha256"],changes={"title":"Changed"})
        with self.assertRaises(ClientHandoffConflict):self.call("void_document",document_id=quote["id"],content_sha256=quote["content_sha256"],reason="Mistake")
        with self.assertRaises(ValueError):self.call("revise_document",document_id=invoice["id"],content_sha256=invoice["content_sha256"],changes={"tax":{"bps":200,"approved":True,"source_ref":"Changed"}})
        with self.assertRaises(ValueError):self.call("revise_document",document_id=invoice["id"],content_sha256=invoice["content_sha256"],changes={},refresh_client=True)

    def test_voided_invoice_recovery_keeps_history_and_uses_new_reference(self):
        quote=self.accepted_quote();first=self.invoice(quote);invoice=self.document(first["entity_id"])
        self.call("void_document",document_id=invoice["id"],content_sha256=invoice["content_sha256"],reason="Owner found wrong completion dates")
        voided=self.document(invoice["id"]);self.assertEqual(voided["status"],"voided");self.assertEqual(voided["history_retained"],2)
        self.assertIsNone(self.document(quote["id"])["invoice_id"])
        self.call("revise_document",document_id=quote["id"],content_sha256=quote["content_sha256"],changes={"title":"Corrected job"})
        corrected=self.document(quote["id"]);self.review(corrected);self.accept(corrected)
        second=self.invoice(corrected)
        self.assertNotEqual(second["entity_id"],first["entity_id"])
        self.assertEqual(self.desk.status()["totals"]["documents"],3)
        with self.assertRaises(ClientHandoffConflict):self.review(voided)

    def test_invoice_corrections_invalidate_approval_without_altering_parent(self):
        quote=self.accepted_quote();saved=self.invoice(quote);invoice=self.document(saved["entity_id"])
        self.review(invoice)
        self.call("revise_document",document_id=invoice["id"],content_sha256=invoice["content_sha256"],changes={"due_date":"2026-11-20"})
        current=self.document(invoice["id"])
        self.assertEqual(current["parent"],invoice["parent"]);self.assertIsNone(current["review"])
        self.assertEqual(current["status"],"ready_for_review")

    def test_invoice_document_cannot_predate_accepted_quote_or_customer_acceptance(self):
        source=intake();source.update(document_date="2026-10-28",service_date="2026-10-28")
        quote=self.quote(source);self.review(quote);self.accept(quote,observed_at="2026-10-30T12:00:00+00:00")
        with self.assertRaises(ClientHandoffConflict):self.invoice(quote,service_date="2026-10-28",document_date="2026-10-29")
        saved=self.invoice(quote,service_date="2026-10-28",document_date="2026-10-30")
        invoice=self.document(saved["entity_id"]);self.review(invoice)
        self.call("revise_document",document_id=invoice["id"],content_sha256=invoice["content_sha256"],changes={"document_date":"2026-10-29"})
        corrected=self.document(invoice["id"])
        self.assertEqual(corrected["status"],"held");self.assertIsNone(corrected["review"])
        self.assertIn("invoice_before_acceptance",{row["code"] for row in corrected["exceptions"]})
        with self.assertRaises(ClientHandoffConflict):self.review(corrected)

    def test_request_id_binds_exact_action_arguments_and_original_receipt(self):
        body={"request_id":"same","expected_revision":0,"client":client()}
        first=self.desk.save_client(body)
        self.call("save_client",client=client("provider-2"))
        replay=self.desk.save_client(body)
        self.assertEqual(replay,{**first,"replayed":True})
        with self.assertRaises(ClientHandoffConflict):self.desk.save_client({**body,"client":client("different")})
        with self.assertRaises(ClientHandoffConflict):self.desk.create_intake({"request_id":"same","expected_revision":2,"intake":intake()})
        for revision in (True,-1,"2"):
            with self.subTest(revision=revision),self.assertRaises(ValueError):self.desk.save_client({**body,"request_id":"bad","expected_revision":revision})

    def test_stale_cas_hash_and_ids_cannot_cross_clients(self):
        quote=self.quote();self.call("save_client",client=client("provider-2"))
        saved=self.call("create_intake",intake=intake("other-job","provider-2"))
        with self.assertRaises(ClientHandoffConflict):self.call("generate_quote",intake_id="other-job",content_sha256=quote["content_sha256"])
        second=self.call("generate_quote",intake_id="other-job",content_sha256=saved["content_sha256"])
        with self.assertRaises(ClientHandoffConflict):self.call("review",document_id=second["entity_id"],content_sha256=quote["content_sha256"],decision="approve")
        with self.assertRaises(ClientHandoffConflict):self.desk.save_client({"request_id":"stale","expected_revision":0,"client":client()})
        self.assertEqual(self.desk.status(client_id="provider-2")["totals"],{"clients":2,"intakes":1,"documents":1})

    def test_pagination_and_filter_make_all_records_reachable(self):
        self.call("save_client",client=client());self.call("save_client",client=client("provider-2"))
        for index in range(7):self.call("create_intake",intake=intake("job-"+str(index),"provider-2" if index%2 else "provider-1"))
        first=self.desk.status(client_id="provider-1",limit=2)
        second=self.desk.status(client_id="provider-1",limit=2,intake_offset=2,client_offset=1)
        self.assertEqual(first["totals"]["intakes"],4);self.assertTrue(first["pagination"]["intakes"]["has_more"])
        self.assertFalse(second["pagination"]["intakes"]["has_more"])
        self.assertEqual(len({row["id"] for row in first["intakes"]+second["intakes"]}),4)
        self.assertEqual(second["clients"][0]["id"],"provider-2")
        for kwargs in ({"limit":51},{"limit":True},{"intake_offset":-1},{"client_id":"bad/id"}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.desk.status(**kwargs)

    def test_global_pause_blocks_every_mutation_without_new_receipts(self):
        quote=self.accepted_quote();self.store.set_setting("paused",True)
        before=self.desk.status()
        for method,arguments in (("save_client",{"client":client()}),("create_intake",{"intake":intake("held")}),
             ("review",{"document_id":quote["id"],"content_sha256":quote["content_sha256"],"decision":"reject"}),
             ("void_document",{"document_id":quote["id"],"content_sha256":quote["content_sha256"],"reason":"Held"}),
             ("export",{"document_id":quote["id"],"content_sha256":quote["content_sha256"]})):
            with self.subTest(method=method),self.assertRaises(ClientHandoffConflict):self.call(method,**arguments)
        with self.assertRaises(ClientHandoffConflict):self.invoice(quote)
        with self.assertRaises(ClientHandoffConflict):self.accept(quote)
        self.assertEqual(self.desk.status(),before)

    def test_export_retains_exact_approved_snapshot_after_profile_change_and_void(self):
        quote=self.quote();self.review(quote)
        result=self.call("export",document_id=quote["id"],content_sha256=quote["content_sha256"])
        body={"request_id":result["request_id"],"expected_revision":result["revision"]-1,
              "document_id":quote["id"],"content_sha256":quote["content_sha256"]}
        for artifact in result["artifacts"]:
            raw=(self.settings.workspace/artifact["path"]).read_bytes()
            self.assertEqual(len(raw),artifact["bytes"]);self.assertEqual(hashlib.sha256(raw).hexdigest(),artifact["sha256"])
        json_path=self.settings.workspace/next(item["path"] for item in result["artifacts"] if item["path"].endswith(".json"))
        original=json_path.read_bytes();packet=json.loads(original)
        self.assertFalse(packet["demo_only"]);self.assertEqual(packet["document"]["content_sha256"],quote["content_sha256"])
        self.call("save_client",client={**client(),"name":"Changed provider"})
        self.call("void_document",document_id=quote["id"],content_sha256=quote["content_sha256"],reason="Owner withdrew unsent draft")
        self.assertEqual(json_path.read_bytes(),original)
        self.assertTrue(self.desk.export(body)["replayed"])
        self.assertEqual(self.document(quote["id"])["status"],"voided")

    def test_export_is_escaped_script_free_and_not_fictionally_watermarked(self):
        source=intake();source["title"]='<img src=x onerror="alert(1)">';source["source_notes"]="<script>unsafe</script>\nSecond line"
        quote=self.quote(source);self.review(quote)
        result=self.call("export",document_id=quote["id"],content_sha256=quote["content_sha256"])
        path=self.settings.workspace/next(item["path"] for item in result["artifacts"] if item["path"].endswith(".html"))
        markup=path.read_text(encoding="utf-8")
        self.assertIn("APPROVED DRAFT / NOT SENT",markup);self.assertIn("&lt;script&gt;unsafe&lt;/script&gt;",markup)
        self.assertNotIn("<script",markup);self.assertNotIn("<img src=x",markup);self.assertNotIn("FICTIONAL",markup)
        self.assertNotIn("http://",markup);self.assertNotIn("https://",markup)

    def failed_export(self,quote,key="failed-export"):
        body={"request_id":key,"expected_revision":self.desk.status()["revision"],"document_id":quote["id"],"content_sha256":quote["content_sha256"]}
        from ariadne.atomic import publish_bytes as actual
        calls=[]
        def fail_second(path,raw,**kwargs):
            calls.append(path)
            if len(calls)==2:raise OSError("simulated publication failure")
            return actual(path,raw,**kwargs)
        with mock.patch("ariadne.client_handoff.publish_bytes",side_effect=fail_second),self.assertRaises(OSError):self.desk.export(body)
        return body

    def test_partial_publication_is_held_never_adopted_or_replayed(self):
        quote=self.quote();self.review(quote);body=self.failed_export(quote)
        path=self.settings.workspace/"service-desk/failed-export/document.json";original=path.read_bytes()
        with self.assertRaises(ClientHandoffConflict):self.desk.export(body)
        with self.assertRaises(ClientHandoffConflict):self.call("export",document_id=quote["id"],content_sha256=quote["content_sha256"])
        self.assertEqual(path.read_bytes(),original);self.assertEqual(self.desk.status()["export_hold"]["request_id"],"failed-export")

    def test_explicit_ack_releases_only_failure_hold_and_keeps_partial_files(self):
        quote=self.quote();self.review(quote);body=self.failed_export(quote)
        original=(self.settings.workspace/"service-desk/failed-export/document.json").read_bytes()
        acknowledge={"request_id":"failed-export","expected_revision":self.desk.status()["revision"]}
        result=self.desk.acknowledge_export(acknowledge)
        self.assertEqual(result["status"],"acknowledged");self.assertTrue(self.desk.acknowledge_export(acknowledge)["replayed"])
        self.assertIsNone(self.desk.status()["export_hold"])
        self.assertEqual((self.settings.workspace/"service-desk/failed-export/document.json").read_bytes(),original)
        with self.assertRaises(ClientHandoffConflict):self.desk.export(body)
        fresh=self.call("export",document_id=quote["id"],content_sha256=quote["content_sha256"])
        self.assertEqual(fresh["status"],"completed")
        with self.assertRaises(ClientHandoffConflict):self.desk.acknowledge_export({"request_id":fresh["request_id"],"expected_revision":fresh["revision"]})

    def test_tampered_completed_export_stops_replay_and_reports_integrity_error(self):
        quote=self.quote();self.review(quote);result=self.call("export",document_id=quote["id"],content_sha256=quote["content_sha256"])
        path=self.settings.workspace/result["artifacts"][0]["path"];path.write_text("changed",encoding="utf-8")
        body={"request_id":result["request_id"],"expected_revision":result["revision"]-1,"document_id":quote["id"],"content_sha256":quote["content_sha256"]}
        with self.assertRaises(ValueError):self.desk.export(body)
        self.assertEqual(path.read_text(),"changed");self.assertEqual(self.desk.status()["latest_export"]["status"],"integrity_error")

    def test_concurrent_edit_during_export_fails_reservation_without_publication(self):
        quote=self.quote();self.review(quote)
        from ariadne.atomic import publish_bytes as actual
        changed=False
        def concurrent(path,raw,**kwargs):
            nonlocal changed
            if not changed:
                changed=True;self.call("revise_document",document_id=quote["id"],content_sha256=quote["content_sha256"],changes={"title":"Concurrent corrected scope"})
            return actual(path,raw,**kwargs)
        with mock.patch("ariadne.client_handoff.publish_bytes",side_effect=concurrent),self.assertRaises(ClientHandoffConflict):
            self.call("export",document_id=quote["id"],content_sha256=quote["content_sha256"])
        self.assertEqual(self.document(quote["id"])["status"],"ready_for_review")
        self.assertEqual(self.desk.status()["latest_export"]["status"],"failed")

    def test_compact_receipts_and_indexed_history_do_not_copy_other_clients(self):
        quote=self.quote();self.review(quote)
        for index in range(12):self.call("save_client",client={**client("other-"+str(index)),"notes":"Private other-client material "+"x"*1500})
        with self.store.connect() as db:
            rows=db.execute("SELECT receipt,receipt_bytes FROM client_handoff_requests").fetchall()
            self.assertTrue(all(row["receipt_bytes"]<1800 for row in rows))
            self.assertTrue(all("Private other-client material" not in row["receipt"] for row in rows))
            self.assertTrue(db.execute("SELECT name FROM sqlite_master WHERE name='client_handoff_request_entity'").fetchone())
        self.assertEqual(self.document(quote["id"])["provider"]["id"],"provider-1")

    def test_real_history_does_not_inherit_demo_500_request_limit(self):
        body={"request_id":"original","expected_revision":0,"client":client()};self.desk.save_client(body)
        with self.store.connect() as db:
            for index in range(550):
                receipt={"request_id":"retained-"+str(index),"status":"completed"}
                raw=workflow._json(receipt)
                db.execute("INSERT INTO client_handoff_requests VALUES(?,?,?,?,?,?,?,?,?,?)",(receipt["request_id"],"save_client","x","completed",NOW.isoformat(),1,raw,len(raw.encode()),0,"provider-1"))
        self.call("save_client",client=client("second"))
        self.assertEqual(self.desk.status()["totals"]["clients"],2);self.assertTrue(self.desk.save_client(body)["replayed"])

    def test_real_tables_and_demo_tables_never_adopt_or_mutate_each_other(self):
        demo=HandoffDesk(self.registry,self.store);demo.seed_demo({"request_id":"demo","expected_revision":0})
        before=demo.status();self.quote();self.assertEqual(before,demo.status())
        self.assertEqual(self.desk.status()["totals"]["documents"],1)
        self.assertTrue(all(row["id"].startswith("demo-") for row in demo.status()["records"]))

    def test_content_quota_rolls_back_partial_mutation_and_never_consumes_reference(self):
        self.call("save_client",client=client());saved=self.call("create_intake",intake=intake())
        before=self.desk.status()
        with mock.patch("ariadne.client_handoff.MAX_ENTITY_BYTES",1),self.assertRaises(ValueError):
            self.call("generate_quote",intake_id="job-1",content_sha256=saved["content_sha256"])
        self.assertEqual(self.desk.status(),before)
        quote=self.call("generate_quote",intake_id="job-1",content_sha256=saved["content_sha256"])
        self.assertEqual(quote["entity_id"],"Q-000001")

    def test_corrupt_stored_hash_profile_metadata_or_review_fails_closed(self):
        quote=self.quote();self.review(quote)
        with self.store.connect() as db:db.execute("UPDATE client_handoff_documents SET sha256=? WHERE id=?",("a"*64,quote["id"]))
        with self.assertRaises(ValueError):self.document(quote["id"])
        with self.store.connect() as db:db.execute("UPDATE client_handoff_documents SET sha256=?,review=? WHERE id=?",(quote["content_sha256"],'{"decision":"approve"}',quote["id"]))
        with self.assertRaises(ValueError):self.document(quote["id"])

    def test_bounded_source_schema_rejects_controls_unknown_fields_and_float_money(self):
        self.call("save_client",client=client())
        for source in (intake(),intake(),intake(),intake()):
            index=self.sequence
            if index==1:source["lines"][0]["unit_price"]=35.0
            elif index==2:source["source_notes"]="bad\x00notes"
            elif index==3:source["extra"]="unsupported"
            else:source["source_notes"]="x"*12001
            with self.assertRaises(ValueError):self.call("create_intake",intake=source)
        self.assertEqual(self.desk.status()["totals"]["intakes"],0)


if __name__=="__main__":unittest.main()
