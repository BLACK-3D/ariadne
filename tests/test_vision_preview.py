"""Synthetic live-preview boundaries; no devices, optional imports or models."""
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne.vision import Vision, VisionConflict, WorkerError, _ACTIVE, _bytes, _sha, _preview_jpeg, run_vision
from tests.test_vision import frame


def jpeg(width=640, height=480):
    # Fixed synthetic JPEG marker structure. It is never sent to a decoder.
    return (b'\xff\xd8\xff\xc0\x00\x0b\x08'+height.to_bytes(2,'big')+width.to_bytes(2,'big')+
            b'\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x11\x22\xff\xd9')


def live_frame(index=0, offset=0):
    value=frame(index,offset=offset)
    value.update(acquired_at=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),
                 timestamp_basis='camera_source_pts',source_lag_ms=0,capture_elapsed_ms=offset)
    return value


def preview_message(value, image=None):
    image=jpeg() if image is None else image
    return {'type':'preview','sample_index':value['sample_index'],'frame_sha256':value['frame_sha256'],
            'jpeg_sha256':_sha(image),'width':640,'height':480,'jpeg_base64':base64.b64encode(image).decode('ascii')}


class PreviewTests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/'runtime/tmp'
        parent.mkdir(parents=True,exist_ok=True)
        tmp=tempfile.TemporaryDirectory(prefix='vision-preview-',dir=parent)
        self.addCleanup(tmp.cleanup)
        self.settings=Settings(data_dir=Path(tmp.name)/'data',workspace=Path(tmp.name)/'workspace')
        self.store=Store(self.settings.data_dir/'state.sqlite3')
        self.manager=Vision(ToolRegistry(self.settings),self.store)
        self.release=threading.Event()
        self.entered=threading.Event()
        self.worker_finished=threading.Event()
        self.payloads=[]
        self.more_frames=[]
        self.failure=False
        self.addCleanup(self.close)
        self.assets=patch('ariadne.vision.vision_assets',return_value={'ready':True,'model_id':'fixture','model_sha256':'a'*64})
        self.assets.start()
        self.addCleanup(self.assets.stop)
        self.discovery=patch.object(self.manager,'discover_cameras',return_value={'devices':[{'device_id':'usb-fixture'}]})
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.native=patch('ariadne.vision.run_vision',side_effect=self.worker)
        self.native.start()
        self.addCleanup(self.native.stop)

    def close(self):
        self.release.set()
        self.manager.close()
        for key in list(_ACTIVE):
            if key[0]==self.manager.key:
                _ACTIVE.pop(key)

    def worker(self,payload,**kwargs):
        self.payloads.append(payload)
        value=live_frame()
        values=[value]
        kwargs['progress'](value)
        if payload.get('preview'):
            message=preview_message(value)
            kwargs['preview']({k:v for k,v in message.items() if k!='jpeg_base64'},jpeg())
        else:
            self.assertIsNone(kwargs['preview'])
        for item in self.more_frames:
            kwargs['progress'](item)
            values.append(item)
        self.entered.set()
        try:
            while not self.release.wait(.01):
                if kwargs['cancelled']():raise VisionConflict('fixture revoked')
                if kwargs['stop']():break
            if self.failure:raise WorkerError('fixture native failure')
            return {'type':'result','frames':values,'duration_ms':values[-1]['timeline_offset_ms'],'decoded_frames':len(values)}
        finally:self.worker_finished.set()

    def start(self,identifier='live-one',**kwargs):
        result=self.manager.start_camera({'request_id':identifier,'device_id':'usb-fixture','expected_control_revision':0,
                                          'duration_ms':15000,**kwargs})
        self.assertTrue(self.entered.wait(2))
        return result

    def finish(self,identifier='live-one'):
        self.release.set()
        _ACTIVE[(self.manager.key,identifier)]['thread'].join(2)
        self.assertTrue(_ACTIVE[(self.manager.key,identifier)]['finished'])

    def test_preview_is_explicit_and_legacy_request_digest_is_unchanged(self):
        request=self.manager._request({'request_id':'legacy','device_id':'usb-fixture','expected_control_revision':0,'duration_ms':15000},'camera')
        old_identity={k:v for k,v in request.items() if k not in {'expected_control_revision','expected_revision','preview'}}
        result=self.start('legacy')
        self.assertNotIn('preview',self.payloads[0])
        self.assertEqual(result['digest'],_sha(_bytes({'operation':'camera',**old_identity})))
        self.assertFalse(self.manager.status()['sessions'][0]['preview']['enabled'])
        with self.assertRaises(VisionConflict):self.manager.preview('legacy')
        with self.assertRaises(ValueError):
            self.manager._request({'request_id':'bad','device_id':'usb-fixture','expected_control_revision':0,'preview':1},'camera')
        with self.assertRaises(ValueError):
            self.manager._request({'request_id':'bad','source_path':'clip.mp4','source_sha256':'a'*64,'expected_control_revision':0,'preview':False},'video')

    def test_current_preview_binds_exact_frame_and_never_renews_lease(self):
        self.start(preview=True)
        active=_ACTIVE[(self.manager.key,'live-one')]
        deadline=active['lease_deadline']
        result=self.manager.preview('live-one')
        self.assertEqual(base64.b64decode(result['jpeg_base64']),jpeg())
        self.assertEqual(result['jpeg_sha256'],_sha(jpeg()))
        self.assertEqual(result['source_frame_sha256'],active['latest']['frame_sha256'])
        self.assertEqual(len(result['frame_id']),64)
        self.assertFalse(result['retained'])
        self.assertGreater(result['freshness_remaining_ms'],0)
        self.assertLessEqual(result['freshness_remaining_ms'],3000)
        frozen=self.manager.frame_for_description('live-one',result['frame_id'])
        self.assertEqual(frozen['jpeg'],jpeg())
        self.assertNotIn('jpeg_base64',frozen)
        self.assertEqual(frozen['control_revision'],0)
        self.assertEqual(active['lease_deadline'],deadline)
        with self.assertRaises(VisionConflict):self.manager.frame_for_description('live-one','0'*64)
        with self.assertRaises(VisionConflict):self.manager.preview('another-session')
        status=self.manager.status()['sessions'][0]['preview']
        self.assertTrue(status['available'])
        self.assertNotIn('jpeg_base64',status)
        self.assertNotIn('jpeg',status)

    def test_preview_freshness_uses_its_own_sample_not_newer_detector_metadata(self):
        self.more_frames=[live_frame(1,500)]
        self.start(preview=True,sample_interval_ms=500)
        active=_ACTIVE[(self.manager.key,'live-one')]
        self.assertEqual(active['latest']['sample_index'],1)
        selected=self.manager.frame_for_description('live-one')
        self.assertEqual(selected['sample_index'],0)
        self.assertEqual(selected['source_frame_sha256'],_sha(b'0'))

    def test_stale_preview_is_scrubbed_and_cannot_be_described(self):
        self.start(preview=True)
        cached=_ACTIVE[(self.manager.key,'live-one')]['preview_cache']
        buffer=cached['jpeg']
        cached['received_monotonic']=time.monotonic()-4
        with self.assertRaises(VisionConflict):self.manager.preview('live-one')
        self.assertEqual(buffer,bytearray(len(jpeg())))
        self.assertNotIn('preview_cache',_ACTIVE[(self.manager.key,'live-one')])

    def test_source_backlog_and_clock_age_do_not_become_fresh_on_get(self):
        self.start(preview=True)
        active=_ACTIVE[(self.manager.key,'live-one')]
        original=dict(active['preview_cache'])
        source=json.loads(json.dumps(original['source_frame']))
        for field,value in (('source_lag_ms',4000),('acquired_at','2026-01-01T00:00:00.000Z')):
            with self.subTest(field=field):
                active['preview_cache']={**original,'jpeg':bytearray(jpeg()),'source_frame':dict(source),'received_monotonic':time.monotonic()}
                active['preview_cache']['source_frame'][field]=value
                with self.assertRaises(VisionConflict):self.manager.frame_for_description('live-one')
                self.assertFalse(self.manager.status()['sessions'][0]['preview']['available'])

    def test_effective_preview_lifetime_accounts_for_source_backlog_and_lease(self):
        self.start(preview=True)
        active=_ACTIVE[(self.manager.key,'live-one')]
        active['preview_cache']['source_frame']['source_lag_ms']=2358
        value=self.manager.preview('live-one')
        self.assertGreater(value['freshness_remaining_ms'],0)
        self.assertLessEqual(value['freshness_remaining_ms'],642)
        active['lease_deadline']=time.monotonic()+.1
        self.assertLessEqual(self.manager.preview('live-one')['freshness_remaining_ms'],100)

    def test_explicit_stop_scrubs_immediately_and_revokes_description_publication(self):
        self.start(preview=True)
        buffer=_ACTIVE[(self.manager.key,'live-one')]['preview_cache']['jpeg']
        self.assertTrue(self.manager.description_source_valid('live-one',0))
        self.manager.stop_camera({'session_id':'live-one'})
        self.assertEqual(buffer,bytearray(len(jpeg())))
        with self.assertRaises(VisionConflict):self.manager.preview('live-one')
        self.assertFalse(self.manager.description_source_valid('live-one',0))
        with self.assertRaises(VisionConflict):
            with self.manager.description_source_guard('live-one',0):self.fail('must not publish')

    def test_pause_resume_fence_scrubs_and_revokes_old_frames(self):
        self.start(preview=True)
        buffer=_ACTIVE[(self.manager.key,'live-one')]['preview_cache']['jpeg']
        self.store.set_setting('paused',True)
        self.store.set_setting('paused',False)
        with self.assertRaises(VisionConflict):self.manager.preview('live-one')
        self.assertEqual(buffer,bytearray(len(jpeg())))
        self.assertFalse(self.manager.description_source_valid('live-one',0))

    def test_normal_completion_drops_image_but_allows_bound_text_publication(self):
        self.start(preview=True)
        frozen=self.manager.frame_for_description('live-one')
        self.finish()
        self.assertTrue(self.manager.description_source_valid('live-one',0))
        with self.manager.description_source_guard('live-one',0):pass
        with self.assertRaises(VisionConflict):self.manager.preview('live-one')
        self.assertEqual(frozen['jpeg'],jpeg())
        detail=self.manager.detail('live-one')
        self.assertTrue(detail['receipt']['verified'])
        self.assertFalse(detail['receipt']['thumbnail_available'])
        self.assertNotIn('jpeg_base64',json.dumps(detail))
        self.assertFalse(list((self.manager.root/'live-one').glob('*.jpg')))
        for path in (self.manager.root/'live-one').iterdir():self.assertNotIn(jpeg(),path.read_bytes())
        evidence=self.manager.root/'live-one/evidence.json'
        original=evidence.read_bytes()
        evidence.write_bytes(original+b' ')
        self.assertFalse(self.manager.description_source_valid('live-one',0))
        evidence.write_bytes(original)
        self.manager.close()
        self.assertFalse(self.manager.description_source_valid('live-one',0))

    def test_mismatched_or_overfrequent_preview_is_not_retained(self):
        for mode in ('wrong-frame','too-often'):
            with self.subTest(mode=mode):
                def invalid(payload,**kwargs):
                    first=live_frame()
                    kwargs['progress'](first)
                    message=preview_message(first)
                    if mode=='wrong-frame':message['frame_sha256']='f'*64
                    kwargs['preview']({k:v for k,v in message.items() if k!='jpeg_base64'},jpeg())
                    second=live_frame(1,500)
                    kwargs['progress'](second)
                    message=preview_message(second)
                    kwargs['preview']({k:v for k,v in message.items() if k!='jpeg_base64'},jpeg())
                # Each failed case uses a distinct isolated manager/ledger;
                # failed accepted IDs are never cleared or retried.
                folder=self.settings.data_dir.parent/mode
                settings=Settings(data_dir=folder/'data',workspace=folder/'workspace')
                manager=Vision(ToolRegistry(settings),Store(settings.data_dir/'state.sqlite3'))
                with patch.object(manager,'discover_cameras',return_value={'devices':[{'device_id':'usb-fixture'}]}),patch('ariadne.vision.run_vision',side_effect=invalid):
                    manager.start_camera({'request_id':mode,'device_id':'usb-fixture','expected_control_revision':0,'preview':True})
                    _ACTIVE[(manager.key,mode)]['thread'].join(2)
                self.assertTrue(manager.status()['hold'])
                self.assertNotIn('preview_cache',_ACTIVE[(manager.key,mode)])
                self.assertFalse(list((manager.root/mode).glob('*.jpg')))
                manager.close()

    def test_native_failure_drops_image_and_leaves_the_existing_failure_hold(self):
        self.failure=True
        self.start(preview=True)
        buffer=_ACTIVE[(self.manager.key,'live-one')]['preview_cache']['jpeg']
        self.finish()
        self.assertEqual(buffer,bytearray(len(jpeg())))
        self.assertTrue(self.manager.status()['hold'])
        self.assertFalse(self.manager.description_source_valid('live-one',0))
        with self.assertRaises(VisionConflict):self.manager.frame_for_description('live-one')
        self.assertFalse(list((self.manager.root/'live-one').glob('*.jpg')))


class PreviewProtocolTests(unittest.TestCase):
    def test_jpeg_dimensions_hash_and_byte_budget_are_validated_without_imports(self):
        value=preview_message(live_frame())
        self.assertEqual(_preview_jpeg(value),jpeg())
        for changes in ({'jpeg_sha256':'0'*64},{'width':641},{'height':479},{'sample_index':True},
                        {'jpeg_base64':base64.b64encode(b'\xff\xd8'+b'x'*65536+b'\xff\xd9').decode()},
                        {'jpeg_base64':'not base64!'}):
            with self.subTest(changes=changes),self.assertRaises((ValueError,WorkerError)):_preview_jpeg({**value,**changes})

    def protocol(self,*,enabled=True,metadata_cap=None,preview_cap=None):
        value=live_frame()
        message=preview_message(value)
        frame_line=_bytes({'type':'frame','frame':value})
        image_line=_bytes(message)
        result_line=_bytes({'type':'result','decoded_frames':1,'duration_ms':0})
        class Child:
            def __init__(self):
                self.stdin=io.BytesIO()
                self.stdout=io.BytesIO(frame_line+image_line+result_line)
                self.returncode=0
            def poll(self):return 0
            def wait(self,timeout=None):return 0
            def terminate(self):pass
            def kill(self):pass
        captured=[]
        with patch('ariadne.vision.subprocess.Popen',return_value=Child()), \
             patch('ariadne.vision.MAX_OUTPUT_BYTES',metadata_cap or len(frame_line)+len(result_line)), \
             patch('ariadne.vision.MAX_PREVIEW_OUTPUT_BYTES',preview_cap or len(image_line)):
            result=run_vision({'operation':'camera','duration_ms':1000,**({'preview':True} if enabled else {})},
                              preview=lambda value,data:captured.append((value,data)),camera=True)
        return result,captured

    def test_transient_preview_has_separate_cap_and_never_enters_results(self):
        result,captured=self.protocol()
        self.assertEqual(len(captured),1)
        self.assertEqual(captured[0][1],jpeg())
        self.assertEqual(len(result['frames']),1)
        self.assertNotIn('jpeg_base64',json.dumps(result))

    def test_unrequested_preview_and_both_overflow_classes_fail_closed(self):
        for args in ({'enabled':False},{'metadata_cap':1},{'preview_cap':1}):
            with self.subTest(args=args),self.assertRaises(WorkerError):self.protocol(**args)

    def test_worker_emits_at_most_one_preview_per_source_second(self):
        from ariadne import _vision_worker as worker
        stderr=b''.join((f'[showinfo@source @ 0] n: {n} pts: {n*500} pts_time:{n*.5} fmt:yuv420p s:640x480\n'
                         f'[showinfo@selected @ 0] n: {n} pts: {n*500} pts_time:{n*.5} fmt:yuv420p s:640x480\n').encode() for n in range(3))
        class Child:
            def __init__(self):
                self.stdout,self.stderr=io.BytesIO(b'\0'*(worker.FRAME_BYTES*3)),io.BytesIO(stderr)
                self.returncode=0
            def poll(self):return 0
            def wait(self,timeout=None):return 0
            def kill(self):pass
        detector=SimpleNamespace(np=SimpleNamespace(uint8=0,frombuffer=lambda *a,**k:SimpleNamespace(reshape=lambda *a:object())),
             infer=lambda _: ([],{'state':'unknown'},'a'*64,'b'*64,1.),encode_preview=lambda _:jpeg())
        with patch.object(worker,'_popen',return_value=Child()), \
             patch.object(worker,'camera_inventory',return_value=[{'device_id':'usb-fixture','_alternative_name':'@device_pnp_usb#fixture'}]), \
             patch.object(worker,'emit') as output:
            result=worker.decode({'operation':'camera','device_id':'usb-fixture','duration_ms':1000,
                                  'sample_interval_ms':500,'thumbnail':False,'preview':True},'sealed-ffmpeg',detector,threading.Event())
        messages=[call.args[0] for call in output.call_args_list]
        self.assertEqual([row['sample_index'] for row in messages if row['type']=='preview'],[0,2])
        self.assertEqual(len([row for row in messages if row['type']=='frame']),3)
        self.assertIsNone(result['thumbnail'])


if __name__=='__main__':unittest.main()
