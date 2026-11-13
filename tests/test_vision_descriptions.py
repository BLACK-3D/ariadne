"""Offline description provenance, revocation and local provider boundaries."""
from contextlib import contextmanager
import copy
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.model import Ollama,ModelError,GPU_LOCK
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.vision import Vision,VisionConflict
from ariadne.vision_descriptions import VisionDescriptions,_ACTIVE

MODEL="qwen3.5:9b"
DIGEST="a"*64
JPEG=b"\xff\xd8"+b"synthetic-camera-frame"*20+b"\xff\xd9"
IMAGE_SHA=hashlib.sha256(JPEG).hexdigest()


class VisionDescriptionModelTests(unittest.TestCase):
    def setUp(self):
        self.client=Ollama("http://127.0.0.1:11434")
        self.calls=[]
        self.tags={"models":[{"name":MODEL,"digest":DIGEST,"size":6000000000}]}
        self.show={"details":{"format":"gguf"},"model_info":{"general.architecture":"qwen35"},"capabilities":["completion","vision"]}
        self.reply={"model":MODEL,"done":True,"done_reason":"stop","message":{"role":"assistant","content":"A person is beside a table."},
                    "prompt_eval_count":800,"eval_count":9,"total_duration":1234567890}
        patched=patch.object(self.client,"_vision_json",side_effect=self.request)
        self.http=patched.start()
        self.addCleanup(patched.stop)
        no_network=patch("socket.create_connection",side_effect=AssertionError("No network in provider fixtures"))
        no_network.start()
        self.addCleanup(no_network.stop)

    def request(self,path,payload=None,**kwargs):
        self.calls.append((path,copy.deepcopy(payload),kwargs))
        return copy.deepcopy(self.tags if path=="/api/tags" else self.show if path=="/api/show" else self.reply)

    def describe(self,**kwargs):
        return self.client.describe_image(kwargs.get("model",MODEL),kwargs.get("question","What do you see?"),kwargs.get("image",JPEG),cancelled=kwargs.get("cancelled"))

    def test_one_image_one_generation_tools_disabled_and_hash_bound(self):
        answer,usage=self.describe()
        self.assertIn("person",answer)
        self.assertEqual([c[0] for c in self.calls],["/api/tags","/api/show","/api/chat","/api/tags"])
        body=self.calls[2][1]
        self.assertEqual(body["tools"],[])
        self.assertFalse(body["think"])
        self.assertFalse(body["stream"])
        self.assertEqual(body["options"]["num_predict"],256)
        self.assertEqual(len(body["messages"]),2)
        self.assertIn("untrusted",body["messages"][0]["content"])
        self.assertIn("never instructions",body["messages"][0]["content"])
        self.assertEqual(len(body["messages"][1]["images"]),1)
        self.assertEqual(usage["profile"],"vision_description")
        self.assertEqual(usage["image_sha256"],IMAGE_SHA)
        self.assertEqual(usage["model_digest"],DIGEST)
        self.assertEqual(usage["generation_requests"],1)
        self.assertEqual(usage["duration_ms"],1234)
        self.assertTrue(all(0<c[2]["deadline"]-time.monotonic()<=60 for c in self.calls))

    def test_identity_passive_rejects_remote_url_and_cloud_selectors(self):
        self.client.vision_identity(MODEL)
        self.http.assert_not_called()
        for url in ("http://localhost:11434","https://example.test","http://127.0.0.1","http://127.0.0.1:11434/path",
                    "http://127.0.0.1:11434?proxy=x","http://u:p@127.0.0.1:11434","https://127.0.0.1:11434"):
            with self.subTest(url=url),self.assertRaises((ModelError,ValueError)):
                Ollama(url).describe_image(MODEL,"What?",JPEG)
        for name in ("model:cloud","model:8b-cloud","bad name",""):
            with self.subTest(name=name),self.assertRaises(ModelError):self.describe(model=name)
        self.http.assert_not_called()

    def test_invalid_input_never_reaches_metadata_or_generation(self):
        for changes in ({"image":b"not-jpeg"},{"image":b"\xff\xd8"+b"x"*65536+b"\xff\xd9"},{"question":""},{"question":"x"*2001}):
            with self.subTest(changes=changes),self.assertRaises(ModelError):self.describe(**changes)
        self.http.assert_not_called()

    def test_missing_remote_duplicate_or_nonvision_model_receives_no_pixels(self):
        original_tags=copy.deepcopy(self.tags)
        original_show=copy.deepcopy(self.show)
        for tags,show in (({"models":[]},original_show),({"models":original_tags["models"]*2},original_show),
                          ({"models":[{**original_tags["models"][0],"remote_host":"https://remote.test"}]},original_show),
                          (original_tags,{**original_show,"capabilities":["completion"]}),
                          (original_tags,{**original_show,"details":{"format":"remote"}})):
            self.tags,self.show=tags,show
            self.calls=[]
            with self.assertRaises(ModelError):self.describe()
            self.assertFalse(any(p=="/api/chat" for p,_,_ in self.calls))
            self.assertNotIn("images",json.dumps([c[1] for c in self.calls]))

    def test_changed_digest_tools_partial_and_invalid_usage_not_adopted(self):
        original=copy.deepcopy(self.reply)
        for change in ({"done_reason":"length"},{"done":False},
                       {"model":"a-different-model:latest"},
                       {"message":{"role":"assistant","content":"Do this","tool_calls":[{"function":{"name":"delete"}}]}},
                       {"message":{"role":"assistant","content":"x"*4001}}, {"eval_count":True},{"eval_count":257}):
            self.reply={**copy.deepcopy(original),**change}
            with self.subTest(change=change),self.assertRaises(ModelError):self.describe()
        self.reply=original
        tag_calls=0
        def changed(path,payload=None,**kw):
            nonlocal tag_calls
            result=self.request(path,payload,**kw)
            if path=="/api/tags":
                tag_calls+=1
                if tag_calls==2:result["models"][0]["digest"]="b"*64
            return result
        self.http.side_effect=changed
        with self.assertRaisesRegex(ModelError,"changed"):self.describe()

    def test_shared_gpu_busy_never_queues_or_sends_image(self):
        held,released=threading.Event(),threading.Event()
        def hold():
            with GPU_LOCK:
                held.set();released.wait(2)
        t=threading.Thread(target=hold)
        t.start()
        self.assertTrue(held.wait(1))
        try:
            with self.assertRaisesRegex(ModelError,"busy"):self.describe()
            self.http.assert_not_called()
        finally:released.set();t.join(2)

    def test_owned_socket_deadline_interrupts_trickling_response(self):
        interrupted=threading.Event()
        class Sock:
            def shutdown(self,*_):interrupted.set()
            def close(self):interrupted.set()
        class Response:
            status=200
            def read(self,_):
                interrupted.wait(2)
                return b'{}'
        class Connection:
            def __init__(self,*_,**__):self.sock=Sock()
            def connect(self):pass
            def request(self,*_,**__):pass
            def getresponse(self):return Response()
            def close(self):self.sock.close()
        client=Ollama("http://127.0.0.1:11434")
        started=time.monotonic()
        with patch("http.client.HTTPConnection",Connection),self.assertRaisesRegex(ModelError,"deadline"):
            client._vision_json("/api/chat",{},identity=client.vision_identity(MODEL),deadline=started+.02)
        self.assertTrue(interrupted.is_set())
        self.assertLess(time.monotonic()-started,1.5)

    def test_transport_rejects_redirect_and_oversized_response_without_following(self):
        connections=[]
        class Sock:
            def shutdown(self,*_):pass
            def close(self):pass
        class Response:
            status=302
            def read(self,_):return b"x"*2_000_001
        class Connection:
            def __init__(self,host,port,**_):
                connections.append((host,port));self.sock=Sock()
            def connect(self):pass
            def request(self,*_,**__):pass
            def getresponse(self):return Response()
            def close(self):pass
        client=Ollama("http://127.0.0.1:11434")
        with patch("http.client.HTTPConnection",Connection):
            with self.assertRaisesRegex(ModelError,"redirect"):
                client._vision_json("/api/show",{},identity=client.vision_identity(MODEL),deadline=time.monotonic()+5)
            Response.status=200
            with self.assertRaisesRegex(ModelError,"2 MB"):
                client._vision_json("/api/chat",{},identity=client.vision_identity(MODEL),deadline=time.monotonic()+5)
        self.assertEqual(connections,[("127.0.0.1",11434)]*2)


class FakeVision:
    def __init__(self,registry,store):
        self.registry,self.store=registry,store
        self.guard=Vision(registry,store)
        self.revoked=False
        self.frame_calls=0
    def frame_for_description(self,session,expected=None):
        self.frame_calls+=1
        if self.revoked:raise VisionConflict("Camera off")
        frame={"session_id":session,"frame_id":"f"*64,"jpeg_sha256":IMAGE_SHA,"source_frame_sha256":"b"*64,
            "jpeg":JPEG,"sample_index":1,"acquired_at":datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z"),
            "source_pts_ms":500,"age_ms":20,"control_revision":self.store.setting("perception_revision",0),"width":640,"height":480,
            "source_clock":"software_arrival","driver_latency":"unknown","retained":False}
        if expected and expected!=frame["frame_id"]:raise VisionConflict("Frame changed")
        return frame
    def description_source_valid(self,session,revision):
        return not self.revoked and not self.guard._revoked(revision)
    @contextmanager
    def description_source_guard(self,session,revision):
        with self.guard._owner_guard(revision):
            if self.revoked:raise VisionConflict("Camera stopped")
            yield
    def _owner_guard(self,revision):return self.guard._owner_guard(revision)


class FakeModel:
    def __init__(self):
        self.calls=[]
        self.entered=threading.Event()
        self.release=None
        self.error=None
        self.before_return=None
    def vision_identity(self,model):return {"provider":"http://127.0.0.1:11434","model":model}
    def describe_image(self,model,question,image,*,cancelled=None):
        self.calls.append((model,question,hashlib.sha256(image).hexdigest()))
        self.entered.set()
        if self.release:self.release.wait(2)
        if self.error:raise self.error
        if self.before_return:self.before_return()
        return "A person is beside a table.",{"profile":"vision_description","model":model,"model_digest":DIGEST,
            "image_sha256":hashlib.sha256(image).hexdigest(),"generation_requests":1,"metadata_requests":3}


class VisionDescriptionServiceTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/"runtime/tmp"
        root.mkdir(parents=True,exist_ok=True)
        tmp=tempfile.TemporaryDirectory(prefix="description-",dir=root)
        self.addCleanup(tmp.cleanup)
        self.settings=Settings(data_dir=Path(tmp.name)/"data",workspace=Path(tmp.name)/"workspace")
        self.store=Store(self.settings.data_dir/"state.sqlite3")
        self.registry=ToolRegistry(self.settings)
        self.vision=FakeVision(self.registry,self.store)
        self.model=FakeModel()
        self.selected=MODEL
        self.service=VisionDescriptions(self.vision,self.model,self.store,lambda:self.selected)
        self.addCleanup(self.service.close)
        p=patch("socket.create_connection",side_effect=AssertionError("No network in service fixtures"))
        p.start();self.addCleanup(p.stop)
    def request(self,id="describe-one",**changes):
        return {"request_id":id,"session_id":"camera-one","expected_control_revision":self.store.setting("perception_revision",0),"question":"What do you see?",**changes}
    def finish(self,id="describe-one"):
        _ACTIVE[(self.service.key,id)]["thread"].join(3)
        detail=self.service.detail(id)
        self.assertNotEqual(detail["receipt"]["status"],"running",detail)
        return detail
    def review(self,id="describe-one",**changes):
        summary=self.service.detail(id)["receipt"]
        return {"request_id":id,"expected_revision":self.service.status()["revision"],"expected_control_revision":self.store.setting("perception_revision",0),"failure_sha256":summary["failure_sha256"],**changes}

    def test_unused_status_is_passive_and_never_creates_state_or_freezes_frame(self):
        state=self.service.status()
        self.assertFalse(state["automatic_generation"])
        self.assertFalse(state["image_retention"])
        self.assertFalse(self.service.root.exists())
        self.assertEqual(self.vision.frame_calls,0)
        self.assertEqual(self.model.calls,[])

    def test_caption_creation_rejects_equal_or_ancestor_agent_workspace(self):
        for workspace in (self.settings.data_dir,self.settings.data_dir/'vision-descriptions'):
            with self.subTest(workspace=workspace):
                settings=Settings(data_dir=self.settings.data_dir,workspace=workspace)
                registry=ToolRegistry(settings)
                vision=FakeVision(registry,self.store)
                service=VisionDescriptions(vision,self.model,self.store,lambda:self.selected)
                status=service.status()
                self.assertFalse(status['configured'])
                self.assertFalse(status['private_storage_outside_workspace'])
                self.assertIn('outside the agent Workspace',status['reason'])
                with self.assertRaisesRegex(VisionConflict,'overlaps'):
                    service.start(self.request())
                self.assertFalse(service.path.exists())
                self.assertEqual(vision.frame_calls,0)
        self.assertEqual(self.model.calls,[])

    def test_explicit_frozen_frame_completed_receipt_and_no_image_artifact(self):
        self.service.start(self.request())
        detail=self.finish()
        receipt=detail["receipt"]
        self.assertEqual(receipt["status"],"completed",detail)
        self.assertTrue(receipt["verified"])
        self.assertTrue(receipt["historical"])
        self.assertFalse(receipt["live_available"])
        self.assertEqual(receipt["source"]["jpeg_sha256"],IMAGE_SHA)
        self.assertEqual(receipt["usage"]["profile"],"vision_description")
        self.assertEqual(len(self.model.calls),1)
        files=list((self.service.root/"describe-one").iterdir())
        self.assertEqual({f.name for f in files},{"attempt.json","receipt.json"})
        self.assertTrue(all(JPEG not in f.read_bytes() for f in files))
        self.assertFalse(self.store.setting("paused",False))

    def test_completed_replay_is_verified_historical_even_after_camera_stop_and_pause(self):
        original=self.request()
        self.service.start(original)
        before=self.finish()["receipt"]
        calls=self.vision.frame_calls
        self.vision.revoked=True
        self.store.set_setting("paused",True)
        replay=self.service.start(original)
        self.assertEqual(replay["answer"],before["answer"])
        self.assertEqual(len(self.model.calls),1)
        self.assertEqual(self.vision.frame_calls,calls)
        self.assertTrue(replay["historical"])
        self.assertFalse(replay["live_available"])
        with self.assertRaises(VisionConflict):self.service.start({**original,"question":"different"})

    def test_invalid_request_pause_or_stale_frame_rejected_before_reservation(self):
        for request in (self.request("../escape"),self.request(question=""),self.request(question="x"*2001),
                        self.request(expected_control_revision=True),self.request(unexpected=True),self.request(expected_frame_id="bad")):
            with self.subTest(request=request),self.assertRaises(ValueError):self.service.start(request)
        self.store.set_setting("paused",True)
        with self.assertRaises(VisionConflict):self.service.start(self.request())
        self.store.set_setting("paused",False)
        self.vision.revoked=True
        with self.assertRaises(VisionConflict):self.service.start(self.request())
        self.assertFalse(self.service.root.exists())
        self.assertEqual(self.model.calls,[])

    def test_one_active_job_no_second_queue_and_explicit_stop_revokes_publication(self):
        self.model.release=threading.Event()
        self.service.start(self.request())
        self.assertTrue(self.model.entered.wait(1))
        with self.assertRaises(VisionConflict):self.service.start(self.request("second"))
        self.vision.revoked=True
        self.model.release.set()
        result=self.finish()
        self.assertEqual(result["receipt"]["status"],"failed")
        self.assertIsNone(result["description"])
        self.assertTrue(self.service.status()["hold"])
        self.assertFalse((self.service.root/"describe-one/receipt.json").exists())

    def test_pause_resume_fence_and_model_selection_changes_revoke_output(self):
        def revoke():
            self.store.set_setting("paused",True);self.store.set_setting("paused",False)
        self.model.before_return=revoke
        self.service.start(self.request())
        self.assertEqual(self.finish()["receipt"]["status"],"failed")
        self.service.acknowledge_failure(self.review())
        self.model.before_return=lambda:setattr(self,"selected","qwen3:4b")
        self.service.start(self.request("model-changed"))
        self.assertEqual(self.finish("model-changed")["receipt"]["status"],"failed")

    def test_failure_requires_exact_review_and_failed_id_never_reexecutes(self):
        self.model.error=ModelError("Local model failed")
        self.service.start(self.request())
        self.finish()
        with self.assertRaises(VisionConflict):self.service.start(self.request("second"))
        with self.assertRaises(VisionConflict):self.service.acknowledge_failure(self.review(failure_sha256="0"*64))
        self.service.acknowledge_failure(self.review())
        self.assertFalse(self.service.status()["hold"])
        with self.assertRaises(VisionConflict):self.service.start(self.request())
        self.model.error=None
        self.service.start(self.request("separate-new"))
        self.assertEqual(self.finish("separate-new")["receipt"]["status"],"completed")
        self.assertTrue((self.service.root/"describe-one/failure.json").exists())

    def test_completed_or_reviewed_failure_tampering_holds_new_work(self):
        self.service.start(self.request())
        self.finish()
        (self.service.root/"describe-one/attempt.json").write_text("{}")
        self.assertTrue(self.service.status()["hold"])
        with self.assertRaises(VisionConflict):self.service.start(self.request("after-tamper"))

    def test_reviewed_failure_files_still_consume_physical_quota(self):
        self.model.error=ModelError("Local model failed")
        self.service.start(self.request());self.finish()
        self.service.acknowledge_failure(self.review())
        usage=self.service.status()["retained_bytes"]
        self.assertGreater(usage,0)
        with patch("ariadne.vision_descriptions.MAX_RETAINED_BYTES",usage+127):
            with self.assertRaises(VisionConflict):self.service.start(self.request("quota-next"))

    def test_interrupted_attempt_never_adopted_or_replayed(self):
        self.model.release=threading.Event()
        self.service.start(self.request())
        self.assertTrue(self.model.entered.wait(1))
        active=_ACTIVE.pop((self.service.key,"describe-one"))
        try:
            fresh=VisionDescriptions(self.vision,self.model,self.store,lambda:self.selected)
            summary=fresh.detail("describe-one")["receipt"]
            self.assertEqual(summary["status"],"interrupted")
            with self.assertRaises(VisionConflict):fresh.start(self.request("new-id"))
        finally:
            _ACTIVE[(self.service.key,"describe-one")]=active
            self.vision.revoked=True
            self.model.release.set();active["thread"].join(2)

    def test_close_revokes_inflight_output_and_does_not_stop_camera(self):
        self.model.release=threading.Event()
        self.service.start(self.request())
        self.assertTrue(self.model.entered.wait(1))
        self.service.close();self.model.release.set()
        self.assertEqual(self.finish()["receipt"]["status"],"failed")
        self.assertFalse(self.vision.revoked)

    def test_real_preview_metadata_and_guard_allow_only_normal_completed_source(self):
        from ariadne.vision import _ACTIVE as camera_active
        from tests.test_vision_preview import jpeg,live_frame,preview_message
        manager=Vision(self.registry,self.store)
        entered,release=threading.Event(),threading.Event()
        self.service=VisionDescriptions(manager,self.model,self.store,lambda:self.selected)
        self.model.release=threading.Event()
        def worker(payload,**kwargs):
            value=live_frame()
            kwargs['progress'](value)
            msg=preview_message(value)
            kwargs['preview']({k:v for k,v in msg.items() if k!='jpeg_base64'},jpeg())
            entered.set()
            release.wait(2)
            return {'type':'result','frames':[value],'duration_ms':0,'decoded_frames':1}
        try:
            with patch('ariadne.vision.vision_assets',return_value={'ready':True,'model_id':'fixture','model_sha256':'a'*64}),\
                 patch.object(manager,'discover_cameras',return_value={'devices':[{'device_id':'usb-fixture'}]}),\
                 patch('ariadne.vision.run_vision',side_effect=worker):
                manager.start_camera({'request_id':'camera-one','device_id':'usb-fixture','expected_control_revision':0,'preview':True})
                self.assertTrue(entered.wait(1))
                self.service.start(self.request())
                self.assertTrue(self.model.entered.wait(1))
                release.set()
                camera_active[(manager.key,'camera-one')]['thread'].join(2)
                self.assertEqual(manager.detail('camera-one')['receipt']['status'],'completed')
                with self.assertRaises(VisionConflict):manager.frame_for_description('camera-one')
                self.model.release.set()
                result=self.finish()
                self.assertEqual(result['receipt']['status'],'completed',result)
                self.assertEqual(result['receipt']['source']['jpeg_sha256'],hashlib.sha256(jpeg()).hexdigest())
                self.assertTrue(result['receipt']['historical'])
        finally:
            release.set();self.model.release.set()
            manager.close()
            for key in list(camera_active):
                if key[0]==manager.key:camera_active.pop(key)


if __name__=="__main__":unittest.main()
