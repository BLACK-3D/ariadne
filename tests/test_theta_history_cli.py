"""Guarded historical command fixtures; no actual account, Terminal or sockets."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import theta_history as cli
from ariadne import theta_history as history
from ariadne.locking import InstanceLock
import test_theta_history as fixtures


class ThetaHistoryCLITests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/'runtime/tmp';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='theta-history-cli-',dir=parent)
        self.addCleanup(self.temp.cleanup);self.addCleanup(patch.stopall)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.workspace=self.data/'workspace'
        (self.workspace/'imports').mkdir(parents=True)
        self.dbpath=self.data/'ariadne.sqlite3'
        with closing(sqlite3.connect(self.dbpath)) as db,db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO settings VALUES(?,?)',('paused','false'))
            db.execute('INSERT INTO settings VALUES(?,?)',('internet_access','{"enabled":true,"revision":5}'))
            db.execute('CREATE TABLE fictional_financial_state(id TEXT,cash TEXT,contributions TEXT,held INTEGER)')
            db.execute('INSERT INTO fictional_financial_state VALUES(?,?,?,?)',('original-simulated-account','25000','25000',1))
        self.manifest=fixtures.manifest();self.put('imports/manifest.json',self.manifest)
        self.calls=[];self.on_capture=None;self.failure=None;outer=self
        class FakeClient:
            def __init__(self,*,online):
                outer.assertIs(online,True);outer.calls.append('client')
            def capture(self,manifest,*,window,check):
                outer.calls.append('capture');check()
                if outer.failure is not None:raise outer.failure
                captured=outer.make_capture(manifest,window)
                if outer.on_capture:outer.on_capture()
                check();return captured
            def close(self):outer.calls.append('closed')
        self.factory=FakeClient
        patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'}).start()
        patch('socket.create_connection',side_effect=AssertionError('No sockets in tests')).start()
        patch('socket.socket.connect',side_effect=AssertionError('No sockets in tests')).start()

    def put(self,name,value):
        path=self.workspace/name;path.parent.mkdir(parents=True,exist_ok=True)
        raw=history.canonical(value);path.write_bytes(raw);return hashlib.sha256(raw).hexdigest()

    def pin(self,name):return hashlib.sha256((self.workspace/name).read_bytes()).hexdigest()

    def put_capture(self):
        digest=self.put('imports/capture.json',fixtures.capture())
        body=(self.workspace/'imports/capture.json').read_bytes()
        self.put('imports/completed.json',{'schema':1,'source_id':history.SOURCE_ID,
            'status':'completed','kind':'free_eod_capture','artifacts':[
                {'path':'capture.json','sha256':digest,'bytes':len(body)}]})


    def make_capture(self,manifest,window):
        value=fixtures.capture();value['selection']=deepcopy(manifest);value['selection_window']=window.as_dict();value['responses']=[]
        day=manifest['end_date']
        for request in manifest['requests']:
            option=request['kind']=='option';row=fixtures.row(day,option)
            if option:
                contract=history.snapshot._contract(request['contract_id'])
                row.update(contract);row['strike']=int(contract['strike'].split('.')[0])
            else:row['symbol']=request['symbol']
            value['responses'].append(fixtures.record(request,[row]))
        return value

    def runcli(self,*args,**kwargs):
        return cli.run(cli.parser().parse_args(args),root=self.root,
            client_factory=kwargs.get('factory',self.factory),now=fixtures.NOW)

    def capture(self,output='reports/theta-history-one',manifest='imports/manifest.json',**kwargs):
        return self.runcli('capture','--online','--manifest',manifest,'--manifest-sha256',self.pin(manifest),'--output',output,**kwargs)

    def import_saved(self,output='reports/theta-history-import'):
        return self.runcli('import','--capture','imports/capture.json','--capture-sha256',self.pin('imports/capture.json'),'--output',output)

    def settings(self,key,value):
        with closing(sqlite3.connect(self.dbpath)) as db,db:
            db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(value),key))

    def dbhash(self):return hashlib.sha256(self.dbpath.read_bytes()).hexdigest()

    def journal(self):
        with closing(sqlite3.connect(self.data/'theta-history.sqlite3')) as db:
            db.row_factory=sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM theta_history_attempts ORDER BY id')]

    def test_status_does_not_create_journal_output_or_contact_provider(self):
        before=self.dbhash();result=self.runcli('status')
        self.assertEqual(result['attempts'],0);self.assertEqual(result['network_requests'],0)
        self.assertEqual(result['paper_fills'],0);self.assertEqual(result['funding_added'],'0')
        self.assertFalse((self.data/'theta-history.sqlite3').exists());self.assertEqual(self.calls,[])
        self.assertEqual(before,self.dbhash());self.assertFalse((self.workspace/'reports').exists())

    def test_capture_exclusive_artifacts_have_exact_hashes_and_preserve_financial_state(self):
        before=self.dbhash();receipt=self.capture()
        self.assertEqual(receipt['status'],'completed');self.assertEqual(receipt['paper_fills'],0)
        self.assertEqual(receipt['funding_added'],'0');self.assertFalse(receipt['automatic_topups'])
        self.assertEqual(self.calls,['client','capture','closed']);self.assertEqual(before,self.dbhash())
        base=self.workspace/'reports/theta-history-one'
        self.assertEqual({item['path'] for item in receipt['artifacts']},{'capture.json','normalized.json','selection.json'})
        for item in receipt['artifacts']:
            raw=(base/item['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),item['sha256']);self.assertEqual(len(raw),item['bytes'])
        rows=self.journal();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'completed')
        self.assertEqual(rows[0]['reserved_requests'],2);self.assertEqual(rows[0]['reserved_wire'],history.MAX_TOTAL_BYTES)
        self.assertEqual(hashlib.sha256((base/'completed.json').read_bytes()).hexdigest(),rows[0]['receipt_sha256'])

    def test_import_is_offline_has_zero_requests_and_creates_no_budget_journal(self):
        self.put_capture();self.settings('internet_access',{'enabled':False,'revision':6})
        before=self.dbhash()
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'1'}):receipt=self.import_saved()
        self.assertEqual(receipt['network_requests'],0);self.assertEqual(receipt['paper_fills'],0)
        self.assertEqual(receipt['funding_added'],'0');self.assertFalse(receipt['performance_study_created'])
        self.assertFalse((self.data/'theta-history.sqlite3').exists());self.assertEqual(self.calls,[])
        self.assertEqual(before,self.dbhash())

    def test_input_hash_malformed_scope_and_invalid_output_fail_before_client(self):
        bad=deepcopy(self.manifest);bad['start_date']='2024-01-01';self.put('imports/bad.json',bad)
        cases=[('capture','--online','--manifest','imports/manifest.json','--manifest-sha256','0'*64,'--output','reports/theta-history-one'),
               ('capture','--online','--manifest','imports/bad.json','--manifest-sha256',self.pin('imports/bad.json'),'--output','reports/theta-history-one'),
               ('capture','--online','--manifest','imports/manifest.json','--manifest-sha256',self.pin('imports/manifest.json'),'--output','../outside')]
        for args in cases:
            with self.subTest(args=args),self.assertRaises((ValueError,OSError)):self.runcli(*args)
        self.assertEqual(self.calls,[]);self.assertFalse((self.data/'theta-history.sqlite3').exists())

    def test_global_pause_or_disabled_internet_refuses_before_reserving(self):
        for key,value in (('paused',True),('internet_access',{'enabled':False,'revision':6})):
            with self.subTest(key=key):
                self.settings(key,value)
                with self.assertRaises(ValueError):self.capture()
                self.assertFalse((self.data/'theta-history.sqlite3').exists());self.assertEqual(self.calls,[])
                self.settings('paused',False);self.settings('internet_access',{'enabled':True,'revision':7})

    def test_inherited_offline_missing_optin_and_unsupported_overrides_do_not_connect(self):
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'1'}),self.assertRaises(ValueError):self.capture()
        with self.assertRaises(ValueError):self.runcli('capture','--manifest','imports/manifest.json','--manifest-sha256',self.pin('imports/manifest.json'),'--output','reports/theta-history-one')
        with self.assertRaises(ValueError):self.runcli('status','--online')
        self.assertEqual(self.calls,[]);self.assertFalse((self.data/'theta-history.sqlite3').exists())

    def test_complete_or_incomplete_existing_output_is_not_overwritten(self):
        directory=self.workspace/'reports/theta-history-one';directory.mkdir(parents=True)
        (directory/'unresolved.txt').write_bytes(b'private fixture marker')
        with self.assertRaises(FileExistsError):self.capture()
        self.assertEqual((directory/'unresolved.txt').read_bytes(),b'private fixture marker')
        self.assertEqual(self.calls,[]);self.assertFalse((self.data/'theta-history.sqlite3').exists())

    def test_failed_capture_keeps_partial_evidence_and_stops_new_request_ids(self):
        partial=fixtures.capture();partial.update(status='failed',failed_request_index=1,failure_code='fixture-failure')
        partial['responses']=partial['responses'][:1]
        self.failure=history.ThetaHistoryCaptureError(partial)
        before=self.dbhash()
        with self.assertRaises(ValueError):self.capture()
        base=self.workspace/'reports/theta-history-one';receipt=json.loads((base/'failed.json').read_bytes())
        self.assertEqual(receipt['status'],'failed');self.assertFalse(receipt['retry_performed'])
        self.assertTrue(receipt['inspect_before_any_new_capture']);self.assertEqual(before,self.dbhash())
        saved=json.loads((base/'partial-capture.json').read_bytes());self.assertEqual(saved,partial)
        self.assertEqual(self.journal()[0]['status'],'failed')
        self.failure=None;self.put('imports/new.json',{**self.manifest,'requests':[{'kind':'stock','symbol':'F'}]})
        self.calls.clear()
        with self.assertRaises(ValueError):self.capture(output='reports/theta-history-new-id',manifest='imports/new.json')
        self.assertEqual(self.calls,[]);self.assertEqual(len(self.journal()),1)

    def test_interrupted_running_journal_refuses_replay_even_new_manifest(self):
        self.capture()
        with closing(sqlite3.connect(self.data/'theta-history.sqlite3')) as db,db:
            db.execute("UPDATE theta_history_attempts SET status='running'")
        self.calls.clear();self.put('imports/new.json',{**self.manifest,'requests':[{'kind':'stock','symbol':'F'}]})
        with self.assertRaises(ValueError):self.capture(output='reports/theta-history-new',manifest='imports/new.json')
        self.assertEqual(self.calls,[]);self.assertEqual(len(self.journal()),1)
        self.assertTrue(self.runcli('status')['held'])

    def test_internet_revision_or_pause_revocation_during_capture_persists_failure(self):
        self.on_capture=lambda:self.settings('internet_access',{'enabled':True,'revision':6})
        with self.assertRaises(ValueError):self.capture()
        self.assertEqual(self.journal()[0]['status'],'failed')
        self.assertFalse((self.workspace/'reports/theta-history-one/completed.json').exists())
        self.assertTrue((self.workspace/'reports/theta-history-one/failed.json').is_file())
        self.assertEqual(self.calls,['client','capture','closed'])

    def test_changed_input_or_source_pins_during_capture_never_publish_completion(self):
        self.on_capture=lambda:self.put('imports/manifest.json',{**self.manifest,'end_date':'2026-11-03'})
        with self.assertRaises(ValueError):self.capture()
        self.assertEqual(self.journal()[0]['status'],'failed')
        self.assertFalse((self.workspace/'reports/theta-history-one/completed.json').exists())

    def test_changed_source_pins_preserve_failure_hold(self):
        state={'changed':False};self.on_capture=lambda:state.update(changed=True)
        with patch.object(cli,'_pins',side_effect=lambda:{'module':'new' if state['changed'] else 'original'}):
            with self.assertRaises(ValueError):self.capture()
        self.assertEqual(self.journal()[0]['status'],'failed')
        self.assertFalse((self.workspace/'reports/theta-history-one/completed.json').exists())

    def test_prior_artifact_tampering_holds_collection_without_network(self):
        self.capture();base=self.workspace/'reports/theta-history-one'
        (base/'normalized.json').write_bytes(b'{}')
        self.calls.clear();self.put('imports/new.json',{**self.manifest,'requests':[{'kind':'stock','symbol':'F'}]})
        with self.assertRaises(ValueError):self.capture(output='reports/theta-history-new',manifest='imports/new.json')
        self.assertEqual(self.calls,[]);self.assertEqual(len(self.journal()),1)

    def test_gross_attempt_and_wire_budgets_do_not_renew_or_refund(self):
        before=self.dbhash()
        for index,symbol in enumerate(('F','KO','MS','AAL')):
            name=f'imports/plan-{index}.json';self.put(name,{**self.manifest,'requests':[{'kind':'stock','symbol':symbol}]})
            self.capture(output=f'reports/theta-history-budget-{index}',manifest=name)
        status=self.runcli('status')
        self.assertEqual(status['attempts'],4);self.assertEqual(status['reserved_requests'],4)
        self.assertEqual(status['reserved_wire_bytes'],cli.MAX_RESERVED_WIRE)
        self.assertEqual(status['wire_reservation_limit'],64*1024*1024)
        self.put('imports/last.json',{**self.manifest,'requests':[{'kind':'stock','symbol':'SOFI'}]})
        calls=list(self.calls)
        with self.assertRaises(ValueError):self.capture(output='reports/theta-history-fifth',manifest='imports/last.json')
        self.assertEqual(calls,self.calls);self.assertEqual(before,self.dbhash())

    def test_repeat_completed_manifest_new_output_does_not_renew_request_budget(self):
        self.capture();before=self.runcli('status');calls=list(self.calls)
        with self.assertRaises(ValueError):self.capture(output='reports/theta-history-another-id')
        self.assertEqual(calls,self.calls);self.assertEqual(self.runcli('status'),before)

    def test_import_paused_or_source_hash_changed_does_not_create_any_output(self):
        self.put_capture();self.settings('paused',True)
        with self.assertRaises(ValueError):self.import_saved()
        self.settings('paused',False)
        (self.workspace/'imports/capture.json').write_bytes(b'{}')
        with self.assertRaises(ValueError):self.runcli('import','--capture','imports/capture.json','--capture-sha256','0'*64,'--output','reports/theta-history-import')
        self.assertFalse((self.workspace/'reports').exists());self.assertFalse((self.data/'theta-history.sqlite3').exists())

    def test_existing_process_lock_blocks_before_any_journal_reservation(self):
        lock=InstanceLock(self.data/'theta-history.lock');lock.acquire()
        try:
            with self.assertRaises(ValueError):self.capture()
        finally:lock.release()
        self.assertEqual(self.calls,[]);self.assertFalse((self.data/'theta-history.sqlite3').exists())

    def test_import_needs_original_completed_capture_receipt(self):
        self.put('imports/capture.json',fixtures.capture())
        with self.assertRaises((ValueError,OSError)):self.import_saved()
        self.assertFalse((self.workspace/'reports').exists());self.assertEqual(self.calls,[])
        self.put_capture()
        proof=json.loads((self.workspace/'imports/completed.json').read_bytes());proof['status']='failed'
        self.put('imports/completed.json',proof)
        with self.assertRaises(ValueError):self.import_saved()
        self.assertFalse((self.workspace/'reports').exists())

    def test_import_cannot_adopt_an_uncommitted_local_capture(self):
        self.capture()
        with closing(sqlite3.connect(self.data/'theta-history.sqlite3')) as db,db:
            db.execute("UPDATE theta_history_attempts SET status='running'")
        raw=self.workspace/'reports/theta-history-one/capture.json'
        with self.assertRaises(ValueError):
            self.runcli('import','--capture','reports/theta-history-one/capture.json',
                '--capture-sha256',hashlib.sha256(raw.read_bytes()).hexdigest(),
                '--output','reports/theta-history-uncommitted-import')
        self.assertFalse((self.workspace/'reports/theta-history-uncommitted-import').exists())

    def test_boolean_completed_proof_schema_is_refused(self):
        self.put_capture();proof=json.loads((self.workspace/'imports/completed.json').read_bytes())
        proof['schema']=True;self.put('imports/completed.json',proof)
        with self.assertRaises(ValueError):self.import_saved()
        self.assertFalse((self.workspace/'reports').exists())

    def test_client_constructor_failure_records_failed_attempt_without_secret_text(self):
        class BrokenClient:
            def __init__(self,*,online):raise OSError('PRIVATE_SENTINEL')
        with self.assertRaises(ValueError) as caught:self.capture(factory=BrokenClient)
        self.assertNotIn('PRIVATE_SENTINEL',str(caught.exception))
        self.assertEqual(self.journal()[0]['status'],'failed')
        proof=json.loads((self.workspace/'reports/theta-history-one/failed.json').read_bytes())
        self.assertEqual(proof['status'],'failed');self.assertNotIn('PRIVATE_SENTINEL',str(proof))

    def test_postcapture_revocation_retains_quarantined_raw_without_completed_import_proof(self):
        outer=self
        class RevokedClient:
            def __init__(self,*,online):pass
            def capture(self,manifest,*,window,check):
                check();captured=outer.make_capture(manifest,window)
                outer.settings('internet_access',{'enabled':True,'revision':6})
                return captured
            def close(self):pass
        with self.assertRaises(ValueError):self.capture(factory=RevokedClient)
        base=self.workspace/'reports/theta-history-one'
        quarantined=json.loads((base/'partial-capture.json').read_bytes())
        self.assertEqual(quarantined['kind'],'quarantined_completed_response')
        self.assertFalse(quarantined['automatic_adoption_allowed'])
        self.assertEqual(quarantined['capture']['status'],'completed')
        self.assertEqual(quarantined['status'],'failed')
        self.assertFalse((base/'completed.json').exists())
        self.assertEqual(self.journal()[0]['status'],'failed')


if __name__=='__main__':unittest.main()
