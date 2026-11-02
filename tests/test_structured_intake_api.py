"""Independent offline WSGI contracts for optional local structured intake.

All records and model replies are synthetic. The actual Application/provider
binding is exercised; socket/DNS/process use is denied, with no owner service.
"""
import base64
import copy
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.model import Ollama
from ariadne.server import Application
from ariadne.store import Store

PREFIX = "/api/service-desk"
TOKEN = "fictional-structured-api-token"
SOURCE = ("Please arrange a repair for Jill Example at 12 Example Road.\n"
          "Her email is jill@example.test.\n"
          "The agreed scope is replace the broken lamp.\n"
          "We expect 2 hours at 40.00 USD per hour.\n"
          "The requested service date is 2026-11-10.\n")
GOOD = {"suggestions": [
    {"field": "customer.name", "value": "Jill Example", "quote": "Please arrange a repair for Jill Example at 12 Example Road."},
    {"field": "customer.email", "value": "jill@example.test", "quote": "Her email is jill@example.test."},
    {"field": "lines.0.description", "value": "replace the broken lamp", "quote": "The agreed scope is replace the broken lamp."},
    {"field": "lines.0.unit_price", "value": "40.00", "quote": "We expect 2 hours at 40.00 USD per hour."},
]}


class UnreadableBody:
    def read(self, *args, **kwargs):
        raise AssertionError("Rejected input must not be read")


class StructuredIntakeAPITests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="structured-api-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(data_dir=Path(temporary.name)/"data", api_token=TOKEN,
                                 model="qwen3.5:9b", ollama_url="http://127.0.0.1:11459")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.model = Ollama(self.settings.ollama_url)
        self.calls = []
        self.request_targets = []
        self.reply = copy.deepcopy(GOOD)
        self.provider_hook = None
        self.envelope = {}
        self.failure = None
        self.tags_digest = "a"*64
        mocked = patch.object(self.model, "request", side_effect=self.fake_request)
        mocked.start(); self.addCleanup(mocked.stop)
        self.engine = Engine(self.settings, self.store, self.model)
        with patch("ariadne.server.Voice", return_value=object()):
            self.app = Application(self.settings, self.store, self.engine)
        self.counter = 0
        with self.store.connect() as db:
            self.initial_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.denied_attempts = []
        def forbidden(target):
            def reject(*args, **kwargs):
                self.denied_attempts.append(target)
                raise AssertionError("Fixture must not use network, DNS or processes")
            return reject
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            guard = patch(target, side_effect=forbidden(target))
            guard.start(); self.addCleanup(guard.stop)
        self.addCleanup(self.assertEqual, self.denied_attempts, [])

    def fake_request(self, path, payload=None, timeout=None, **kwargs):
        self.request_targets.append(kwargs.get("_target"))
        self.calls.append((path, copy.deepcopy(payload)))
        if path == "/api/show":
            return {"details": {"format": "gguf", "family": "qwen3.5"},
                    "model_info": {"general.architecture": "qwen3.5"},
                    "capabilities": ["completion"]}
        if path == "/api/tags":
            return {"models": [{"name": "qwen3.5:9b", "digest": self.tags_digest, "size": 6000000000}]}
        self.assertEqual(path, "/api/chat", "Only bounded extraction may invoke inference")
        if self.provider_hook:
            self.provider_hook()
        if self.failure:
            raise self.failure
        content = self.reply if type(self.reply) is str else json.dumps(self.reply, ensure_ascii=False)
        return {"done": True, "done_reason": "stop", "model": "qwen3.5:9b",
                "message": {"role": "assistant", "content": content},
                "eval_count": 30, "prompt_eval_count": 80, "total_duration": 1000000,
                **copy.deepcopy(self.envelope)}

    @property
    def generations(self):
        return [payload for path, payload in self.calls if path == "/api/chat"]

    def request(self, path=PREFIX, method="GET", body=None, *, raw=None,
                auth=True, origin=None, host="localhost:8080", query="",
                content_type="application/json", stream=None, length=None, disconnect=False):
        raw = json.dumps({} if body is None else body, ensure_ascii=False).encode() if raw is None else raw
        env = {"HTTP_HOST": host, "REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_TYPE": content_type, "CONTENT_LENGTH": str(len(raw) if length is None else length),
               "wsgi.input": io.BytesIO(raw) if stream is None else stream, "QUERY_STRING": query}
        if auth: env["HTTP_AUTHORIZATION"] = "Bearer "+TOKEN
        if origin: env["HTTP_ORIGIN"] = origin
        response = {}
        def start(status, headers):
            if disconnect: raise ConnectionResetError("Fictional browser disconnected")
            response.update(status=int(status.split()[0]), headers=dict(headers))
        payload = b"".join(self.app(env, start))
        response["body"] = json.loads(payload) if response["headers"]["Content-Type"].startswith("application/json") else payload
        return response

    def state(self):
        response = self.request(); self.assertEqual(response["status"], 200, response)
        return response["body"]

    def meta(self, prefix="request"):
        self.counter += 1
        return {"request_id": prefix+"-"+str(self.counter), "expected_revision": self.state()["revision"]}

    def prepare(self, text=SOURCE):
        result = self.request(PREFIX+"/clients", "POST", {**self.meta("client"), "client": {
            "id": "fictional-issuer", "name": "Fictional Service", "address": "10 Example Road"}})
        self.assertEqual(result["status"], 200, result)
        result = self.request(PREFIX+"/inbox", "POST", {**self.meta("upload"), "client_id": "fictional-issuer",
            "filename": "fictional-request.txt", "content_base64": base64.b64encode(text.encode()).decode()})
        self.assertEqual(result["status"], 200, result)
        result = self.request(PREFIX+"/inbox/"+result["body"]["entity_id"])
        self.assertEqual(result["status"], 200, result)
        return result["body"]["packet"]

    def generation_body(self, packet):
        return {**self.meta("proposal"), "packet_id": packet["id"], "content_sha256": packet["content_sha256"]}

    def generate(self, packet, body=None):
        body = self.generation_body(packet) if body is None else body
        result = self.request(PREFIX+"/inbox/proposals", "POST", body)
        self.assertEqual(result["status"], 200, result)
        self.assertEqual(result["body"]["status"], "completed", result)
        self.assertEqual(result["body"]["entity_type"], "intake_proposal")
        return self.proposal(result["body"]["entity_id"]), body, result["body"]

    def proposal(self, identifier):
        result = self.request(PREFIX+"/inbox/proposals/"+identifier)
        self.assertEqual(result["status"], 200, result)
        return result["body"]["proposal"]

    def hold(self):
        return self.state()["structured_extraction"]["hold"]

    def ack_body(self):
        hold = self.hold(); self.assertIsNotNone(hold)
        return {"request_id": hold["request_id"], "expected_revision": self.state()["revision"],
                "receipt_sha256": hold["receipt_sha256"], "inspection_confirmed": True}

    def acceptance(self, packet, proposal=None):
        body = {**self.meta("accept"), "packet_id": packet["id"], "content_sha256": packet["content_sha256"],
                "review_confirmed": True, "intake": copy.deepcopy((proposal or packet)["proposed_intake"])}
        if proposal: body.update(proposal_id=proposal["id"], proposal_sha256=proposal["content_sha256"])
        return body

    def test_routes_enforce_auth_origin_host_before_reading_body(self):
        routes = (("/inbox/proposals", "POST"), ("/inbox/proposals/missing", "GET"),
                  ("/inbox/proposals/acknowledge", "POST"))
        for suffix, method in routes:
            for overrides, expected in (({"auth": False},401), ({"origin":"https://outsider.example"},403), ({"host":"outsider.example"},403)):
                with self.subTest(suffix=suffix, overrides=overrides):
                    result = self.request(PREFIX+suffix, method, stream=UnreadableBody(), length=10, **overrides)
                    self.assertEqual(result["status"], expected, result)
        self.assertEqual(self.calls, [])

    def test_passive_status_and_missing_proposal_never_generate_or_initialize(self):
        state = self.state(); self.assertIn("structured_extraction", state)
        self.assertIsNone(state["structured_extraction"]["hold"])
        self.assertIn(self.request(PREFIX+"/inbox/proposals/missing")["status"], (400,404))
        with self.store.connect() as db:
            self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},self.initial_tables)
        self.assertEqual(self.calls, [])

    def test_strict_json_mime_body_bound_and_query_admission(self):
        for suffix in ("/inbox/proposals", "/inbox/proposals/acknowledge"):
            for raw in (b'{"request_id":"a","request_id":"b"}',b'{"expected_revision":1.1}',b'{"x":NaN}',b'[]',b'null',b'\xff'):
                self.assertEqual(self.request(PREFIX+suffix,"POST",raw=raw)["status"],400)
            self.assertEqual(self.request(PREFIX+suffix,"POST",content_type="text/plain")["status"],415)
            self.assertEqual(self.request(PREFIX+suffix,"POST",stream=UnreadableBody(),length=128*1024+1)["status"],413)
            self.assertEqual(self.request(PREFIX+suffix,"POST",query="unsafe=1",stream=UnreadableBody(),length=5)["status"],400)
        self.assertEqual(self.request(PREFIX+"/inbox/proposals/missing",query="x=1")["status"],400)
        self.assertEqual(self.calls,[])

    def test_pause_stale_revision_bad_source_identity_precede_generation(self):
        packet=self.prepare(); body=self.generation_body(packet)
        self.store.set_setting("paused",True)
        self.assertEqual(self.request(PREFIX+"/inbox/proposals","POST",body)["status"],409)
        self.store.set_setting("paused",False)
        self.assertEqual(self.request(PREFIX+"/inbox/proposals","POST",{**body,"expected_revision":0})["status"],409)
        self.assertIn(self.request(PREFIX+"/inbox/proposals","POST",{**body,"content_sha256":"0"*64})["status"],(400,409))
        self.assertEqual(self.generations,[])
        self.assertIsNone(self.hold())

    def test_changed_client_cannot_start_generation(self):
        packet=self.prepare()
        update=self.request(PREFIX+"/clients","POST",{**self.meta("client-edit"),"client":{
            "id":"fictional-issuer","name":"Fictional renamed company","address":"10 Example Road"}})
        self.assertEqual(update["status"],200,update)
        self.assertEqual(self.request(PREFIX+"/inbox/proposals","POST",self.generation_body(packet))["status"],409)
        self.assertEqual(self.generations,[])

    def test_completed_proposal_binds_source_exact_citations_and_model_identity(self):
        packet=self.prepare(); proposal,body,receipt=self.generate(packet)
        self.assertEqual(len(self.generations),1)
        self.assertTrue(self.request_targets and all(target is self.request_targets[0] for target in self.request_targets))
        self.assertEqual(self.request_targets[0][0],self.settings.ollama_url)
        self.assertEqual(proposal["packet_id"],packet["id"])
        self.assertEqual(proposal["packet_content_sha256"],packet["content_sha256"])
        self.assertEqual(proposal["text_sha256"],packet["text_sha256"])
        self.assertEqual(proposal["model"],"qwen3.5:9b")
        self.assertEqual(proposal["provider"],self.settings.ollama_url)
        self.assertEqual(len(proposal["candidates"]),4)
        for candidate in proposal["candidates"]:
            self.assertEqual(packet["text"][candidate["start"]:candidate["end"]],candidate["quote"])
            self.assertEqual(hashlib.sha256(candidate["quote"].encode()).hexdigest(),candidate["quote_sha256"])
        request=self.generations[0]
        self.assertIs(request["stream"],False);self.assertIs(request["think"],False)
        self.assertFalse(request.get("tools"));self.assertIsInstance(request["format"],dict)
        self.assertEqual(self.store.runs(),[])
        self.assertIsNone(self.hold())
        reread=self.request(PREFIX+"/inbox/"+packet["id"])["body"]["packet"]
        for key,value in packet.items():
            if key not in {"structured_extraction"}:self.assertEqual(reread[key],value,key)

    def test_exact_completed_replay_does_not_call_model_again(self):
        packet=self.prepare();proposal,body,receipt=self.generate(packet)
        replay=self.request(PREFIX+"/inbox/proposals","POST",body)
        self.assertEqual(replay["status"],200,replay)
        self.assertEqual(replay["body"]["entity_id"],proposal["id"])
        self.assertIs(replay["body"]["replayed"],True)
        self.assertEqual(len(self.generations),1)
        mismatch=self.request(PREFIX+"/inbox/proposals","POST",{**body,"content_sha256":"0"*64})
        self.assertEqual(mismatch["status"],409,mismatch)
        self.assertEqual(len(self.generations),1)

    def test_generator_has_no_open_write_transaction_and_active_ack_is_refused(self):
        packet=self.prepare(); observed=[]
        def during():
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE");db.rollback()
            hold=self.hold();self.assertIsNotNone(hold)
            ack=self.request(PREFIX+"/inbox/proposals/acknowledge","POST",self.ack_body())
            observed.append(ack["status"])
        self.provider_hook=during
        self.generate(packet)
        self.assertEqual(observed,[409]);self.assertEqual(len(self.generations),1)

    def test_concurrent_same_request_claim_produces_one_generation(self):
        packet=self.prepare();body=self.generation_body(packet);entered=threading.Event();release=threading.Event()
        def during():
            entered.set()
            if not release.wait(10):raise AssertionError("Fixture release timed out")
        self.provider_hook=during
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.request,PREFIX+"/inbox/proposals","POST",body)
            try:
                self.assertTrue(entered.wait(5))
                second=self.request(PREFIX+"/inbox/proposals","POST",body)
                self.assertEqual(second["status"],409,second)
            finally:release.set()
            self.assertEqual(first.result(timeout=10)["status"],200)
        self.assertEqual(len(self.generations),1)

    def test_source_review_creates_no_approvals_even_if_operator_supplies_true(self):
        packet=self.prepare();proposal,_,_=self.generate(packet);body=self.acceptance(packet,proposal)
        for row in body["intake"]["lines"]:row["price_approved"]=True
        body["intake"]["tax"].update(bps=0,approved=True,source_ref="Fictional source")
        body["intake"]["permission"].update(confirmed=True,source_ref="Fictional source")
        result=self.request(PREFIX+"/inbox/accept","POST",body)
        self.assertEqual(result["status"],200,result)
        intake=self.request(PREFIX+"/intakes/"+result["body"]["entity_id"])["body"]["intake"]
        self.assertTrue(all(line["price_approved"] is False for line in intake["lines"]))
        self.assertIs(intake["tax"]["approved"],False);self.assertIs(intake["permission"]["confirmed"],False)
        self.assertIn("structured_proposal",intake)
        self.assertEqual(len(self.generations),1)

    def test_acceptance_needs_exact_proposal_pair_and_explicit_review(self):
        packet=self.prepare();proposal,_,_=self.generate(packet);body=self.acceptance(packet,proposal)
        for mutation in ({"proposal_sha256":"0"*64},{"review_confirmed":False}):
            response=self.request(PREFIX+"/inbox/accept","POST",{**body,**mutation})
            self.assertIn(response["status"],(400,409),response)
        missing=copy.deepcopy(body);del missing["proposal_sha256"]
        self.assertEqual(self.request(PREFIX+"/inbox/accept","POST",missing)["status"],400)
        self.assertEqual(len(self.generations),1)

    def test_unknown_arguments_cannot_choose_schema_model_url_or_approval(self):
        packet=self.prepare();body=self.generation_body(packet)
        for key,value in (("model","remote-cloud"),("provider","https://outside.example"),("schema",{}),("price_approved",True),("tools",[])):
            result=self.request(PREFIX+"/inbox/proposals","POST",{**body,key:value})
            self.assertEqual(result["status"],400,result)
        self.assertEqual(self.generations,[])

    def test_timeout_is_durable_failure_without_replay_or_exception_leak(self):
        packet=self.prepare();body=self.generation_body(packet);self.failure=TimeoutError("private synthetic document details")
        failed=self.request(PREFIX+"/inbox/proposals","POST",body)
        self.assertIn(failed["status"],(200,400,409,500,503),failed)
        self.assertNotIn("private synthetic document details",json.dumps(failed["body"]))
        hold=self.hold();self.assertIsNotNone(hold)
        self.failure=None
        self.assertEqual(self.request(PREFIX+"/inbox/proposals","POST",body)["status"],409)
        self.assertEqual(len(self.generations),1)

    def test_malformed_json_results_in_inspectable_hold_not_automatic_repair(self):
        packet=self.prepare();body=self.generation_body(packet);self.reply='{"suggestions":['
        self.request(PREFIX+"/inbox/proposals","POST",body)
        self.assertIsNotNone(self.hold());self.assertEqual(len(self.generations),1)
        self.reply=copy.deepcopy(GOOD)
        self.assertEqual(self.request(PREFIX+"/inbox/proposals","POST",body)["status"],409)
        self.assertEqual(len(self.generations),1)

    def test_forged_quotes_and_values_do_not_enter_proposed_facts(self):
        packet=self.prepare();self.reply={"suggestions":[{"field":"customer.name","value":"Invented Person","quote":"Invented Person"}]}
        result=self.request(PREFIX+"/inbox/proposals","POST",self.generation_body(packet))
        if result["status"]==200 and result["body"].get("status")=="completed":
            proposal=self.proposal(result["body"]["entity_id"])
            self.assertEqual(proposal["candidates"],[])
            self.assertIsNone(proposal["proposed_intake"]["customer"]["name"])
            self.assertTrue(proposal["warnings"])
        else:self.assertIsNotNone(self.hold())
        self.assertEqual(len(self.generations),1)

    def test_schema_permission_or_tool_rows_cannot_grant_authority(self):
        packet=self.prepare();self.reply={"suggestions":[{"field":"permission.confirmed","value":"true","quote":"Jill Example"}]}
        result=self.request(PREFIX+"/inbox/proposals","POST",self.generation_body(packet))
        if result["status"]==200 and result["body"].get("status")=="completed":
            proposal=self.proposal(result["body"]["entity_id"])
            self.assertIs(proposal["proposed_intake"]["permission"]["confirmed"],False)
            self.assertFalse(any(c["field"]=="permission.confirmed" for c in proposal["candidates"]))
        else:self.assertIsNotNone(self.hold())
        self.assertEqual(len(self.generations),1)

    def test_pause_during_generation_holds_output_and_prevents_retry(self):
        packet=self.prepare();body=self.generation_body(packet)
        self.provider_hook=lambda:self.store.set_setting("paused",True)
        result=self.request(PREFIX+"/inbox/proposals","POST",body)
        self.assertFalse(result["status"]==200 and result["body"].get("status")=="completed",result)
        self.assertIsNotNone(self.hold());self.store.set_setting("paused",False);self.provider_hook=None
        self.assertEqual(self.request(PREFIX+"/inbox/proposals","POST",body)["status"],409)
        self.assertEqual(len(self.generations),1)

    def test_changed_selected_model_during_generation_holds_output(self):
        packet=self.prepare();body=self.generation_body(packet)
        self.provider_hook=lambda:self.store.set_setting("model","different-local-model:1")
        result=self.request(PREFIX+"/inbox/proposals","POST",body)
        self.assertFalse(result["status"]==200 and result["body"].get("status")=="completed",result)
        self.assertIsNotNone(self.hold());self.assertEqual(len(self.generations),1)

    def test_acknowledgement_requires_exact_hash_and_inspection_without_adopting_output(self):
        packet=self.prepare();self.reply='invalid JSON'
        self.request(PREFIX+"/inbox/proposals","POST",self.generation_body(packet))
        body=self.ack_body()
        for mutation in ({"receipt_sha256":"0"*64},{"inspection_confirmed":False}):
            result=self.request(PREFIX+"/inbox/proposals/acknowledge","POST",{**body,**mutation})
            self.assertIn(result["status"],(400,409),result)
        self.assertIsNotNone(self.hold())
        result=self.request(PREFIX+"/inbox/proposals/acknowledge","POST",body)
        self.assertEqual(result["status"],200,result)
        self.assertIsNone(self.hold());self.assertEqual(len(self.generations),1)
        self.assertEqual(self.state()["intakes"],[])

    def test_refusal_thinking_tool_calls_and_truncation_never_become_proposals(self):
        packet=self.prepare()
        for envelope in ({"done_reason":"length"},{"message":{"role":"assistant","content":json.dumps(GOOD),"tool_calls":[{"function":{"name":"file_write","arguments":{}}}]}},
                         {"message":{"role":"assistant","content":json.dumps(GOOD),"thinking":"unrequested internal text"}},
                         {"message":{"role":"assistant","content":json.dumps(GOOD),"refusal":"Cannot process"}}):
            with self.subTest(envelope=envelope):
                self.envelope=envelope;body=self.generation_body(packet)
                result=self.request(PREFIX+"/inbox/proposals","POST",body)
                self.assertFalse(result["status"]==200 and result["body"].get("status")=="completed",result)
                self.assertIsNotNone(self.hold())
                ack=self.request(PREFIX+"/inbox/proposals/acknowledge","POST",self.ack_body())
                self.assertEqual(ack["status"],200,ack)
        self.assertEqual(len(self.generations),4)

    def test_remote_provider_is_rejected_before_inference(self):
        packet=self.prepare();self.model.url="https://outside.example"
        result=self.request(PREFIX+"/inbox/proposals","POST",self.generation_body(packet))
        self.assertIn(result["status"],(400,409,503),result)
        self.assertEqual(self.generations,[])

    def test_cloud_model_is_rejected_before_inference(self):
        packet=self.prepare();self.store.set_setting("model","qwen3.5:9b-cloud")
        result=self.request(PREFIX+"/inbox/proposals","POST",self.generation_body(packet))
        self.assertIn(result["status"],(400,409,503),result)
        self.assertEqual(self.generations,[])

    def test_response_disconnect_after_completion_is_inspected_without_generation_replay(self):
        packet=self.prepare();body=self.generation_body(packet)
        with self.assertRaises(ConnectionResetError):
            self.request(PREFIX+"/inbox/proposals","POST",body,disconnect=True)
        self.assertEqual(len(self.generations),1)
        replay=self.request(PREFIX+"/inbox/proposals","POST",body)
        self.assertEqual(replay["status"],200,replay)
        self.assertTrue(replay["body"]["replayed"])
        self.assertEqual(len(self.generations),1)


    def test_consumed_packet_cannot_start_generation(self):
        packet=self.prepare()
        accepted=self.request(PREFIX+"/inbox/accept","POST",self.acceptance(packet))
        self.assertEqual(accepted["status"],200,accepted)
        result=self.request(PREFIX+"/inbox/proposals","POST",self.generation_body(packet))
        self.assertEqual(result["status"],409,result)
        self.assertEqual(self.generations,[])

    def test_provider_model_digest_change_during_generation_holds_result(self):
        packet=self.prepare()
        self.provider_hook=lambda:setattr(self,"tags_digest","b"*64)
        result=self.request(PREFIX+"/inbox/proposals","POST",self.generation_body(packet))
        self.assertFalse(result["status"]==200 and result["body"].get("status")=="completed",result)
        self.assertIsNotNone(self.hold());self.assertEqual(len(self.generations),1)

    def test_client_changed_through_real_api_during_generation_holds_result(self):
        packet=self.prepare();changed=[]
        def during():
            changed.append(self.request(PREFIX+"/clients","POST",{**self.meta("client-edit"),"client":{
                "id":"fictional-issuer","name":"Fictional changed company","address":"10 Example Road"}}))
        self.provider_hook=during
        result=self.request(PREFIX+"/inbox/proposals","POST",self.generation_body(packet))
        self.assertEqual(changed[0]["status"],200,changed)
        self.assertFalse(result["status"]==200 and result["body"].get("status")=="completed",result)
        self.assertIsNotNone(self.hold());self.assertEqual(len(self.generations),1)

    def test_model_cannot_silently_replace_conflicting_deterministic_fact(self):
        packet=self.prepare("Customer: Alex Example\nJill Example will perform the repair.\n")
        self.reply={"suggestions":[{"field":"customer.name","value":"Jill Example","quote":"Jill Example will perform the repair."}]}
        proposal,_,_=self.generate(packet)
        self.assertEqual(proposal["proposed_intake"]["customer"]["name"],"Alex Example")
        self.assertFalse(any(c["field"]=="customer.name" and c["value"]=="Jill Example" for c in proposal["candidates"]))
        self.assertTrue(proposal["warnings"])

    def test_ambiguous_repeated_quote_is_left_unresolved(self):
        packet=self.prepare("Please contact Jill Example.\nPlease contact Jill Example.\n")
        self.reply={"suggestions":[{"field":"customer.name","value":"Jill Example","quote":"Please contact Jill Example."}]}
        proposal,_,_=self.generate(packet)
        self.assertEqual(proposal["candidates"],[])
        self.assertIsNone(proposal["proposed_intake"]["customer"]["name"])
        self.assertTrue(proposal["warnings"])

    def test_cited_quote_without_proposed_value_does_not_establish_a_fact(self):
        packet=self.prepare()
        self.reply={"suggestions":[{"field":"customer.name","value":"Invented Name","quote":"Please arrange a repair for Jill Example at 12 Example Road."}]}
        proposal,_,_=self.generate(packet)
        self.assertEqual(proposal["candidates"],[])
        self.assertIsNone(proposal["proposed_intake"]["customer"]["name"])
        self.assertTrue(proposal["warnings"])

    def test_accept_cannot_attach_another_packets_proposal(self):
        first=self.prepare();proposal,_,_=self.generate(first)
        upload=self.request(PREFIX+"/inbox","POST",{**self.meta("second-source"),"client_id":"fictional-issuer",
            "filename":"second-source.txt","content_base64":base64.b64encode(b"Second fictional source request.").decode()})
        self.assertEqual(upload["status"],200,upload)
        second=self.request(PREFIX+"/inbox/"+upload["body"]["entity_id"])["body"]["packet"]
        result=self.request(PREFIX+"/inbox/accept","POST",self.acceptance(second,proposal))
        self.assertEqual(result["status"],409,result)
        self.assertEqual(self.state()["intakes"],[])



    def test_decimal_prefixes_cannot_become_different_quantities_or_prices(self):
        packet=self.prepare("The job needs 2.5 hours at 40.50 USD per hour.\n")
        self.reply={"suggestions":[
            {"field":"lines.0.quantity","value":"2","quote":"The job needs 2.5 hours at 40.50 USD per hour."},
            {"field":"lines.0.unit_price","value":"40","quote":"The job needs 2.5 hours at 40.50 USD per hour."}]}
        proposal,_,_=self.generate(packet)
        self.assertEqual(proposal["candidates"],[])
        self.assertEqual(proposal["proposed_intake"]["lines"][0]["quantity"],"1")
        self.assertIsNone(proposal["proposed_intake"]["lines"][0]["unit_price"])
        self.assertTrue(proposal["warnings"])

    def test_negative_currency_amount_cannot_become_a_positive_price(self):
        packet=self.prepare("A refund of -$40.00 USD was discussed.\n")
        self.reply={"suggestions":[{"field":"lines.0.unit_price","value":"40.00","quote":"A refund of -$40.00 USD was discussed."}]}
        proposal,_,_=self.generate(packet)
        self.assertEqual(proposal["candidates"],[])
        self.assertIsNone(proposal["proposed_intake"]["lines"][0]["unit_price"])
        self.assertTrue(proposal["warnings"])



    def test_cropped_quotes_cannot_hide_decimal_token_continuations(self):
        packet=self.prepare("The job needs 2.5 hours at 40.50 USD per hour.\n")
        self.reply={"suggestions":[
            {"field":"lines.0.quantity","value":"2","quote":"2"},
            {"field":"lines.0.unit_price","value":"40","quote":"40"}]}
        proposal,_,_=self.generate(packet)
        self.assertEqual(proposal["candidates"],[])
        self.assertIsNone(proposal["proposed_intake"]["lines"][0]["unit_price"])

    def test_cropped_quote_cannot_hide_a_negative_currency_sign(self):
        packet=self.prepare("A refund of -$40.00 USD was discussed.\n")
        self.reply={"suggestions":[{"field":"lines.0.unit_price","value":"40.00","quote":"40.00"}]}
        proposal,_,_=self.generate(packet)
        self.assertEqual(proposal["candidates"],[])
        self.assertIsNone(proposal["proposed_intake"]["lines"][0]["unit_price"])

    def test_cropped_quote_cannot_turn_part_of_a_word_into_a_name(self):
        packet=self.prepare("The customer contact is Jillian Example.\n")
        self.reply={"suggestions":[{"field":"customer.name","value":"Jill","quote":"Jill"}]}
        proposal,_,_=self.generate(packet)
        self.assertEqual(proposal["candidates"],[])
        self.assertIsNone(proposal["proposed_intake"]["customer"]["name"])

    def test_cropped_quote_cannot_hide_a_foreign_currency(self):
        packet=self.prepare("The proposed rate was 40.00 GBP per hour.\n")
        self.reply={"suggestions":[{"field":"lines.0.unit_price","value":"40.00","quote":"40.00"}]}
        proposal,_,_=self.generate(packet)
        self.assertEqual(proposal["candidates"],[])
        self.assertIsNone(proposal["proposed_intake"]["lines"][0]["unit_price"])



    def test_cropped_quote_cannot_discard_space_grouped_thousands(self):
        for separator in (" ", "\u00a0", "\u202f"):
            with self.subTest(separator=repr(separator)):
                packet=self.prepare("The proposed rate was 1"+separator+"400.00 USD per hour.\n")
                self.reply={"suggestions":[{"field":"lines.0.unit_price","value":"400.00","quote":"400.00"}]}
                proposal,_,_=self.generate(packet)
                self.assertEqual(proposal["candidates"],[])
                self.assertIsNone(proposal["proposed_intake"]["lines"][0]["unit_price"])

    def test_cropped_quote_cannot_discard_typographic_negative_dashes(self):
        for sign in ("\u2010", "\u2012", "\u2013"):
            with self.subTest(sign=repr(sign)):
                packet=self.prepare("A refund of "+sign+"40.00 USD was discussed.\n")
                self.reply={"suggestions":[{"field":"lines.0.unit_price","value":"40.00","quote":"40.00"}]}
                proposal,_,_=self.generate(packet)
                self.assertEqual(proposal["candidates"],[])
                self.assertIsNone(proposal["proposed_intake"]["lines"][0]["unit_price"])


if __name__ == "__main__":
    unittest.main()
