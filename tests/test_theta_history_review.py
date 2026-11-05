"""Independent adversarial review: synthetic EOD inputs, no Terminal or owner data."""
from contextlib import ExitStack, closing
from copy import deepcopy
from datetime import datetime, timezone
import builtins
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from ariadne import theta_history as history
from ariadne import research_window
from ariadne import options_data
from test_alpaca_market_data import FakeResponse,FakeConnection

AT=datetime(2026,11,5,15,0,0,tzinfo=timezone.utc)
CONTRACT='KO261120P00080000'


def manifest():
    return {'schema':1,'start_date':'2026-11-02','end_date':'2026-11-03',
        'requests':[{'kind':'stock','symbol':'KO'},{'kind':'option','contract_id':CONTRACT}]}


def row(option=False):
    result={'created':'2026-11-03T17:15:00.000','last_trade':'2026-11-03T15:59:57.000',
        'open':1,'high':1.5,'low':0.8,'close':1.1,'volume':850,'count':100,
        'bid':1,'ask':1.04,'bid_size':8,'ask_size':9,
        'bid_exchange':1,'ask_exchange':2,'bid_condition':0,'ask_condition':0}
    if option:result.update(symbol='KO',expiration='2026-11-20',strike=80,right='put')
    else:result.update(open=80,high=81,low=79,close=80.1,bid=80,ask=80.02)
    return result


def response(request,payload,received='2026-11-05T15:00:01Z'):
    raw=json.dumps(payload,separators=(',',':')).encode()
    return {'request':deepcopy(request),'payload':raw.decode(),'received_at':received,
        'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}


def capture():
    selection=manifest()
    return {'schema':1,'source_id':'theta_eod_research_v1','status':'completed',
        'selection':selection,'selection_window':research_window.derive_window(AT).as_dict(),
        'started_at':'2026-11-05T15:00:00Z','completed_at':'2026-11-05T15:00:03Z',
        'responses':[response(selection['requests'][0],[row()], '2026-11-05T15:00:01Z'),
                     response(selection['requests'][1],[row(True)],'2026-11-05T15:00:02Z')],
        'transport':{'source_id':'theta_eod_research_v1','operation':'historical_eod',
            'provider_authenticated':False,'local_terminal_verified':False,'kind':'local_terminal_unattested'}}


def change_payload(value,index,transform):
    record=value['responses'][index];rows=json.loads(record['payload']);transform(rows)
    updated=response(record['request'],rows,record['received_at']);record.update(updated)
    return value


class IndependentThetaHistoryReview(unittest.TestCase):
    def test_report_creation_and_last_trade_are_not_quote_event_or_fills(self):
        value=capture();before=deepcopy(value);result=history.normalize_capture(value)
        option=result['rows'][1]
        self.assertEqual(option['created_raw'],'2026-11-03T17:15:00.000')
        self.assertEqual(option['last_trade_raw'],'2026-11-03T15:59:57.000')
        self.assertIsNone(option['created_at'])
        self.assertIsNone(option['last_trade_at'])
        self.assertIsNone(option['quote_at'])
        self.assertIsNone(option['open_interest'])
        self.assertFalse(option['quote_event_clock_verified'])
        self.assertFalse(option['standard_deliverable_verified'])
        self.assertFalse(result['provider_authenticated'])
        self.assertEqual(result['paper_operations'],0)
        self.assertEqual(result['created_clock_role'],'vendor_report_generation_not_quote_event')
        self.assertEqual(value,before)
        with self.assertRaises(options_data.OptionsDataError):options_data.validate_dataset(result,AT)

    def test_final_receipt_and_capture_completion_are_distinct_actual_clocks(self):
        value=capture();result=history.normalize_capture(value)
        self.assertEqual(result['actual_received_at'],'2026-11-05T15:00:02Z')
        self.assertEqual(result['capture_completed_at'],'2026-11-05T15:00:03Z')
        self.assertNotEqual(result['actual_received_at'],result['capture_completed_at'])
        self.assertEqual(result['rows'][1]['received_at_raw'],'2026-11-05T15:00:02Z')
        self.assertIsInstance(result['rows'][1]['received_at_ns'],int)

    def test_missing_report_sessions_and_empty_responses_are_not_zero_returns(self):
        value=capture();value['responses'][1]=response(value['selection']['requests'][1],[],'2026-11-05T15:00:02Z')
        result=history.normalize_capture(value)
        self.assertEqual(len(result['rows']),1)
        coverage=result['coverage'][1]
        self.assertEqual(coverage['status'],'no_reports')
        self.assertEqual(coverage['missing_report_sessions'],['2026-11-02','2026-11-03'])
        self.assertFalse(coverage['completeness_verified'])
        self.assertNotIn('pnl',result)
        self.assertNotIn('returns',result)

    def test_missing_size_is_unknown_and_crossed_price_is_preserved_with_issue(self):
        value=change_payload(capture(),1,lambda rows:rows[0].update(bid_size=None,ask=0.5))
        option=history.normalize_capture(value)['rows'][1]
        self.assertIsNone(option['bid_size'])
        self.assertEqual(option['ask'],'0.5')
        self.assertIn('crossed_eod_quote',option['issues'])
        self.assertIn('displayed_size_unknown',option['issues'])

    def test_duplicate_session_or_old_actual_row_rejects_complete_cohort_without_repair(self):
        for transform in (lambda rows:rows.append(deepcopy(rows[0])),
                          lambda rows:rows[0].update(created='2025-11-04T17:15:00.000')):
            value=change_payload(capture(),1,transform);before=deepcopy(value)
            with self.assertRaises(ValueError):history.normalize_capture(value)
            self.assertEqual(value,before)

    def test_selection_cannot_expand_scope_or_substitute_request_response(self):
        for mutate in (lambda c:c['selection'].update(start_date='2025-11-04'),
                       lambda c:c['responses'][1]['request'].update(contract_id='F261120P00010000'),
                       lambda c:c['selection_window'].update(oldest='2020-01-01')):
            value=capture();mutate(value)
            with self.assertRaises(ValueError):history.normalize_capture(value)

    def test_whole_manifest_refuses_wildcard_path_and_boolean_scope(self):
        window=research_window.derive_window(AT)
        for mutate in (lambda m:m.update(schema=True),
                       lambda m:m['requests'][0].update(symbol='*'),
                       lambda m:m['requests'][0].update(symbol='../quote'),
                       lambda m:m['requests'].append(deepcopy(m['requests'][0])),
                       lambda m:m.update(end_date='2026-11-05')):
            value=manifest();mutate(value)
            with self.assertRaises(ValueError):history.validate_manifest(value,window)

    def test_raw_hash_byte_count_and_duplicate_json_keys_are_not_repaired(self):
        value=capture();raw=json.dumps(value).encode();digest=hashlib.sha256(raw).hexdigest()
        self.assertEqual(history.import_capture(raw,expected_sha256=digest)['selection_window'],value['selection_window'])
        with self.assertRaises(ValueError):history.import_capture(raw+b' ',expected_sha256=digest)
        value['responses'][1]['bytes']+=1
        with self.assertRaises(ValueError):history.normalize_capture(value)
        duplicate=b'{"schema":1,"schema":1}'
        with self.assertRaises(ValueError):history.import_capture(duplicate,expected_sha256=hashlib.sha256(duplicate).hexdigest())

    def test_replay_uses_original_frozen_window_and_has_no_external_effects(self):
        value=capture();history.normalize_capture(value)
        with ExitStack() as stack:
            for owner,name in ((socket,'socket'),(sqlite3,'connect'),(subprocess,'Popen'),
                               (builtins,'open'),(Path,'read_bytes'),(Path,'write_bytes')):
                stack.enter_context(patch.object(owner,name,side_effect=AssertionError('Unexpected effect')))
            result=history.normalize_capture(value)
        self.assertEqual(result['selection_window'],value['selection_window'])
        self.assertEqual(result['model_calls'],0)
        self.assertEqual(result['paper_operations'],0)

    def test_report_clock_with_future_nanosecond_is_not_hidden_by_projection(self):
        value=capture();value['selection_window']=research_window.derive_window('2026-11-03T23:15:00Z').as_dict()
        value['started_at']='2026-11-03T23:15:00Z';value['completed_at']='2026-11-03T23:15:01Z'
        for record in value['responses']:record['received_at']='2026-11-03T23:15:00Z'
        change_payload(value,1,lambda rows:rows[0].update(created='2026-11-03T23:15:00.000000001Z'))
        with self.assertRaises(ValueError):history.normalize_capture(value)

    def test_last_trade_after_created_and_unknown_open_interest_field_are_refused(self):
        for fields in ({'last_trade':'2026-11-04T15:00:00.000'},{'open_interest':2000}):
            value=change_payload(capture(),1,lambda rows:rows[0].update(fields))
            with self.assertRaises(ValueError):history.normalize_capture(value)

    def test_forged_authentication_or_failed_capture_cannot_enter_completed_import(self):
        for mutate in (lambda c:c['transport'].update(provider_authenticated=True),
                       lambda c:c['transport'].update(local_terminal_verified=True),
                       lambda c:c.update(status='failed')):
            value=capture();mutate(value)
            with self.assertRaises(ValueError):history.normalize_capture(value)

    def test_declared_finite_acquisition_span_is_enforced_on_replay(self):
        value=capture();value['completed_at']='2026-11-06T15:00:03Z'
        with self.assertRaises(ValueError):history.normalize_capture(value)

    def test_offline_and_closed_clients_refuse_before_loopback_socket(self):
        for online,offline,policy in ((False,'0','online'),(True,'1','online'),(True,'0','offline')):
            with patch.dict(os.environ,{'ARIADNE_OFFLINE':offline}),patch.object(history,'install_network_policy',return_value={'mode':policy}),patch.object(history.http.client,'HTTPConnection') as connect:
                with self.assertRaises(ValueError):history.ThetaHistoryClient(online=online).capture(manifest(),window=research_window.derive_window(AT))
                connect.assert_not_called()
        client=history.ThetaHistoryClient(online=True);client.close()
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'}),patch.object(history,'install_network_policy',return_value={'mode':'online'}),patch.object(history.http.client,'HTTPConnection') as connect:
            with self.assertRaises(ValueError):client.capture(manifest(),window=research_window.derive_window(AT))
            connect.assert_not_called()

    def test_failed_second_http_preserves_valid_prefix_without_retry_or_body_echo(self):
        first=FakeResponse(json.dumps([row()]).encode());second=FakeResponse(b'PRIVATE_SENTINEL',status=429)
        connections=[FakeConnection(first),FakeConnection(second)]
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'}),patch.object(history,'install_network_policy',return_value={'mode':'online'}),patch.object(history.http.client,'HTTPConnection',side_effect=connections) as connect,patch.object(history.time,'sleep'),patch.object(history,'datetime',wraps=datetime) as clock:
            clock.now.return_value=AT
            with self.assertRaises(history.ThetaHistoryCaptureError) as caught:
                history.ThetaHistoryClient(online=True).capture(manifest(),window=research_window.derive_window(AT))
        partial=caught.exception.partial_capture
        self.assertEqual(connect.call_count,2)
        self.assertEqual(len(partial['responses']),1)
        self.assertEqual(partial['responses'][0]['sha256'],hashlib.sha256(json.dumps([row()]).encode()).hexdigest())
        self.assertEqual(partial['status'],'failed')
        self.assertEqual(second.reads,0)
        self.assertNotIn('PRIVATE_SENTINEL',str(caught.exception))
        self.assertNotIn('PRIVATE_SENTINEL',repr(partial))
        self.assertFalse(partial['transport']['provider_authenticated'])
        with self.assertRaises(ValueError):history.normalize_capture(partial)

    def test_invalid_utf8_http200_is_retained_only_as_failed_raw_evidence(self):
        raw=b'\xffmalformed'
        conn=FakeConnection(FakeResponse(raw))
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'}),patch.object(history,'install_network_policy',return_value={'mode':'online'}),patch.object(history.http.client,'HTTPConnection',return_value=conn),patch.object(history,'datetime',wraps=datetime) as clock:
            clock.now.return_value=AT
            with self.assertRaises(history.ThetaHistoryCaptureError) as caught:
                history.ThetaHistoryClient(online=True).capture(manifest(),window=research_window.derive_window(AT))
        failed=caught.exception.partial_capture.get('failed_response')
        self.assertIsInstance(failed,dict)
        self.assertEqual(failed['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(failed['bytes'],len(raw))
        import base64
        self.assertEqual(base64.b64decode(failed['payload_base64'],validate=True),raw)
        with self.assertRaises(ValueError):history.normalize_capture(caught.exception.partial_capture)


class IndependentThetaHistoryCLIReview(unittest.TestCase):
    def setUp(self):
        import tempfile
        from scripts import theta_history as command
        self.cli=command
        base=ROOT/'runtime/tmp';base.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix='theta-history-review-',dir=base)
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.data=self.root/'data';self.workspace=self.data/'workspace'
        (self.workspace/'imports').mkdir(parents=True)
        with closing(sqlite3.connect(self.data/'ariadne.sqlite3')) as db, db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.executemany('INSERT INTO settings VALUES(?,?)',(('paused','false'),
                ('internet_access',json.dumps({'enabled':True,'revision':1}))))
        self.environment=patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'});self.environment.start();self.addCleanup(self.environment.stop)
        self.pins=patch.object(self.cli,'_pins',return_value={'synthetic-module.py':'0'*64});self.pins.start();self.addCleanup(self.pins.stop)

    def args(self,name='first',selection=None):
        raw=history.canonical(manifest() if selection is None else selection)
        path=self.workspace/f'imports/{name}.json';path.write_bytes(raw)
        return self.cli.parser().parse_args(['capture','--online','--manifest',f'imports/{name}.json',
            '--manifest-sha256',hashlib.sha256(raw).hexdigest(),'--output',f'reports/theta-history-{name}'])

    def owner_rows(self):
        with closing(sqlite3.connect(self.data/'ariadne.sqlite3')) as db, db:return db.execute('SELECT rowid,key,value FROM settings ORDER BY rowid').fetchall()

    def journal_rows(self):
        with closing(sqlite3.connect(self.data/'theta-history.sqlite3')) as db, db:return db.execute('SELECT id,status,manifest_sha256,receipt_sha256 FROM theta_history_attempts ORDER BY id').fetchall()

    def fake_factory(self,after=None):
        class Client:
            def __init__(self,*,online):self.closed=False
            def capture(inner,selection,*,window,check):
                check()
                if after is not None:after()
                return capture()
            def close(inner):inner.closed=True
        return Client

    def test_completed_capture_has_hash_checked_evidence_without_owner_financial_writes(self):
        before=self.owner_rows();args=self.args()
        receipt=self.cli.run(args,root=self.root,client_factory=self.fake_factory(),now=AT)
        self.assertEqual(receipt['status'],'completed');self.assertEqual(receipt['paper_fills'],0)
        self.assertEqual(receipt['funding_added'],'0');self.assertFalse(receipt['performance_study_created'])
        folder=self.workspace/args.output
        for item in receipt['artifacts']:
            raw=(folder/item['path']).read_bytes()
            self.assertEqual(len(raw),item['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),item['sha256'])
        self.assertEqual(self.owner_rows(),before)
        self.assertEqual(self.journal_rows()[0][1],'completed')

    def test_changed_prior_artifact_blocks_new_capture_before_client_or_reservation(self):
        self.cli.run(self.args(),root=self.root,client_factory=self.fake_factory(),now=AT)
        prior=self.journal_rows();folder=self.workspace/'reports/theta-history-first'
        path=folder/'normalized.json';path.write_bytes(path.read_bytes()+b' ')
        args=self.args('second');factory=unittest.mock.Mock()
        with self.assertRaises(ValueError):self.cli.run(args,root=self.root,client_factory=factory,now=AT)
        factory.assert_not_called();self.assertEqual(self.journal_rows(),prior)
        self.assertFalse((self.workspace/args.output).exists())

    def test_revocation_after_capture_retains_quarantined_source_and_stops_new_attempt(self):
        def revoke():
            with closing(sqlite3.connect(self.data/'ariadne.sqlite3')) as db, db:db.execute("UPDATE settings SET value='true' WHERE key='paused'")
        args=self.args()
        with self.assertRaises(ValueError):self.cli.run(args,root=self.root,client_factory=self.fake_factory(revoke),now=AT)
        self.assertEqual(self.journal_rows()[0][1],'failed')
        folder=self.workspace/args.output;self.assertTrue((folder/'failed.json').is_file())
        expected=capture()['responses'][0]['sha256'].encode()
        self.assertTrue(any(expected in path.read_bytes() for path in folder.iterdir() if path.is_file()))
        with closing(sqlite3.connect(self.data/'ariadne.sqlite3')) as db, db:db.execute("UPDATE settings SET value='false' WHERE key='paused'")
        factory=unittest.mock.Mock();new=self.args('second')
        with self.assertRaises(ValueError):self.cli.run(new,root=self.root,client_factory=factory,now=AT)
        factory.assert_not_called();self.assertEqual(len(self.journal_rows()),1)

    def test_completed_artifact_without_committed_journal_cannot_be_imported_as_completed(self):
        original=self.cli._publish
        def interrupt_after_publish(*args,**kwargs):
            result=original(*args,**kwargs)
            if args[3]['status']=='completed':raise KeyboardInterrupt('Synthetic interruption before journal commit')
            return result
        args=self.args()
        with patch.object(self.cli,'_publish',side_effect=interrupt_after_publish):
            with self.assertRaises(KeyboardInterrupt):self.cli.run(args,root=self.root,client_factory=self.fake_factory(),now=AT)
        self.assertEqual(self.journal_rows()[0][1],'running')
        self.assertTrue((self.workspace/args.output/'completed.json').is_file())
        saved=self.workspace/args.output/'capture.json';raw=saved.read_bytes()
        args=self.cli.parser().parse_args(['import','--capture',saved.relative_to(self.workspace).as_posix(),
            '--capture-sha256',hashlib.sha256(raw).hexdigest(),'--output','reports/theta-history-import'])
        with self.assertRaises(ValueError):self.cli.run(args,root=self.root,now=AT)
        self.assertFalse((self.workspace/args.output).exists())


class IndependentThetaTerminalReview(unittest.TestCase):
    @staticmethod
    def jar(main='net.example.Main',major=65):
        from io import BytesIO
        import zipfile
        buffer=BytesIO()
        with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('META-INF/MANIFEST.MF','Manifest-Version: 1.0\r\nMain-Class: '+main+'\r\n\r\n')
            archive.writestr(main.replace('.','/')+'.class',b'\xca\xfe\xba\xbe\x00\x00'+major.to_bytes(2,'big'))
        return buffer.getvalue()

    def test_compressed_manifest_cannot_echo_private_key_as_metadata(self):
        from ariadne import theta_terminal as terminal
        key='private.SecretSentinel12345';raw=self.jar(key)
        self.assertNotIn(key.encode(),raw)
        self.assertEqual(terminal.inspect_jar(raw)['main_class'],key)
        connection=FakeConnection(FakeResponse(raw,headers={'Content-Type':'application/java-archive'}))
        connection.set_debuglevel=lambda value:None
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'}),patch.object(terminal,'install_network_policy',return_value={'mode':'online'}),patch.object(terminal.http.client,'HTTPSConnection',return_value=connection):
            with self.assertRaises(terminal.ThetaTerminalError) as caught:
                terminal.ThetaBootstrapClient(key,online=True).application('202611051')
        self.assertNotIn(key,str(caught.exception))

    def test_eight_digit_assigned_version_reaches_the_fixed_application_route(self):
        from ariadne import theta_terminal as terminal
        responses=[FakeResponse(b'20260615',headers={'Content-Type':'text/plain'}),
                   FakeResponse(self.jar(),headers={'Content-Type':'application/java-archive'})]
        connections=[FakeConnection(value) for value in responses]
        for connection in connections:connection.set_debuglevel=lambda value:None
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'}),patch.object(terminal,'install_network_policy',return_value={'mode':'online'}),patch.object(terminal.http.client,'HTTPSConnection',side_effect=connections) as factory:
            client=terminal.ThetaBootstrapClient('REVIEW_SECRET_KEY_123456789',online=True)
            assigned=client.version();artifact=client.application(assigned['version']);client.close()
        self.assertEqual(assigned['version'],'20260615')
        self.assertEqual(factory.call_count,2)
        self.assertEqual(connections[1].requests[0][:2],('GET','/bootstrap/jars/20260615'))
        self.assertEqual(artifact['evidence']['main_class'],'net.example.Main')
        self.assertFalse(artifact['evidence']['target_executed'])

    def test_invalid_class_version_is_not_reported_as_negative_java_requirement(self):
        from ariadne import theta_terminal as terminal
        with self.assertRaises(terminal.ThetaTerminalError):terminal.inspect_jar(self.jar(major=0))


class IndependentThetaBrowserSetupReview(unittest.TestCase):
    KEY = 'REVIEW_ONLY_THETA_KEY_123456789'

    class MemoryConnection:
        def __init__(self, raw):
            from io import BytesIO
            self.source=BytesIO(raw);self.output=bytearray();self.closed=False;self.shutdown_called=False
        def makefile(self, *args, **kwargs):return self.source
        def sendall(self, value):self.output.extend(value)
        def settimeout(self, value):pass
        def shutdown(self, value):self.shutdown_called=True
        def close(self):self.closed=True

    def state(self):
        import time
        from ariadne import theta_setup_browser as browser
        data=unittest.mock.Mock();data._path.return_value.exists.return_value=False
        state=browser._State(data,time.monotonic()+60)
        state.host='127.0.0.1:43187';state.origin='http://'+state.host
        return state

    def exchange(self,state,raw):
        from contextlib import redirect_stdout,redirect_stderr
        from io import StringIO
        from ariadne import theta_setup_browser as browser
        connection=self.MemoryConnection(raw);out=StringIO();err=StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            browser._handler(state)(connection,('127.0.0.1',12345),object())
        self.assertEqual(out.getvalue(),'');self.assertEqual(err.getvalue(),'')
        self.assertNotIn(self.KEY.encode(),connection.output)
        return bytes(connection.output)

    def post(self,state,*,headers=None,fields=None):
        from urllib.parse import urlencode
        from ariadne import theta_setup_browser as browser
        body=urlencode(fields if fields is not None else [('csrf',state.csrf),('key',self.KEY)]).encode()
        base=[('Host',state.host),('Origin',state.origin),('Content-Type','application/x-www-form-urlencoded'),
              ('Content-Length',str(len(body))),('Cookie',browser.COOKIE+'='+str(state.cookie))]
        if headers is not None:base=headers(base)
        raw=('POST /save HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in base)+'\r\n').encode()+body
        return raw

    def test_form_is_no_script_no_store_and_never_a_credential_url(self):
        state=self.state();result=self.exchange(state,('GET / HTTP/1.1\r\nHost: '+state.host+'\r\n\r\n').encode())
        self.assertIn(b'200 OK',result);self.assertIn(b'type="password"',result)
        self.assertIn(b'action="/save"',result);self.assertNotIn(b'<script',result)
        self.assertIn(b'Cache-Control: no-store',result);self.assertIn(b'Referrer-Policy: same-origin',result)
        self.assertIn(b"default-src 'none'; form-action 'self'",result)
        self.assertIn(b'HttpOnly; SameSite=Strict',result)
        self.assertNotIn(b'Access-Control-Allow-Origin',result)

    def test_origin_host_framing_and_crosssite_ambiguities_never_save(self):
        from ariadne import theta_setup_browser as browser
        changes=[lambda h:[(k,v) for k,v in h if k!='Origin'],
            lambda h:[(k,'http://evil.invalid' if k=='Origin' else v) for k,v in h],
            lambda h:[(k,'localhost:43187' if k=='Host' else v) for k,v in h],
            lambda h:[(k,'127.0.0.1:43188' if k=='Host' else v) for k,v in h],
            lambda h:h+[('Host','127.0.0.1:43187')],lambda h:h+[('Origin','http://127.0.0.1:43187')],
            lambda h:h+[('Content-Length','1')],lambda h:h+[('Content-Type','application/x-www-form-urlencoded')],
            lambda h:h+[('Transfer-Encoding','chunked')],lambda h:h+[('Expect','100-continue')],
            lambda h:h+[('Sec-Fetch-Site','cross-site')],lambda h:h+[('Sec-Fetch-Mode','no-cors')],
            lambda h:h+[('Sec-Fetch-Dest','iframe')]]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                state=self.state()
                with patch.object(browser.terminal,'save_key') as save:
                    result=self.exchange(state,self.post(state,headers=change))
                save.assert_not_called();self.assertFalse(state.used);self.assertNotIn(b'303 See Other',result)

    def test_invalid_form_cookie_and_duplicate_fields_never_save(self):
        from ariadne import theta_setup_browser as browser
        for kind in ('csrf','cookie','duplicate-key','extra-field','invalid-escape'):
            with self.subTest(kind=kind):
                state=self.state();raw=self.post(state)
                if kind=='csrf':raw=self.post(state,fields=[('csrf','wrong'),('key',self.KEY)])
                if kind=='cookie':raw=self.post(state,headers=lambda h:[(k,'wrong=wrong' if k=='Cookie' else v) for k,v in h])
                if kind=='duplicate-key':raw=self.post(state,fields=[('key',self.KEY),('key',self.KEY)])
                if kind=='extra-field':raw=self.post(state,fields=[('csrf',state.csrf),('key',self.KEY),('extra',self.KEY)])
                if kind=='invalid-escape':raw=raw.replace(self.KEY.encode(),b'%GG'+self.KEY.encode()[3:])
                with patch.object(browser.terminal,'save_key') as save:self.exchange(state,raw)
                save.assert_not_called();self.assertFalse(state.used)

    def test_uncertain_save_consumes_attempt_without_echo_or_replay(self):
        from ariadne import theta_setup_browser as browser
        state=self.state();raw=self.post(state)
        with patch.object(browser.terminal,'save_key',side_effect=RuntimeError(self.KEY)) as save:
            first=self.exchange(state,raw);second=self.exchange(state,raw)
        self.assertEqual(save.call_count,1);self.assertTrue(state.used and state.failed and state.done)
        self.assertIn(b'500 Internal Server Error',first);self.assertIn(b'410 Gone',second)

    def test_success_redirect_has_only_fixed_location_and_cannot_save_twice(self):
        from ariadne import theta_setup_browser as browser
        state=self.state();raw=self.post(state)
        with patch.object(browser.terminal,'save_key',return_value={'configured':True}) as save:
            result=self.exchange(state,raw);self.exchange(state,raw)
        self.assertEqual(save.call_count,1);self.assertTrue(state.saved)
        self.assertIn(b'303 See Other',result);self.assertIn(b'Location: /done\r\n',result)
        self.assertNotIn(b'Location: /done?',result)
        done=self.exchange(state,('GET /done HTTP/1.1\r\nHost: '+state.host+'\r\n\r\n').encode())
        self.assertIn(b'200 OK',done);self.assertIn(b'Theta key saved locally',done);self.assertTrue(state.done)

    def test_malformed_parser_errors_do_not_reflect_key_or_log_it(self):
        for raw in [b'GET /'+self.KEY.encode()+b' HTTP/garbage\r\n\r\n',
                    self.KEY.encode()+b' / HTTP/1.1\r\nHost: wrong\r\n\r\n',
                    b'GET / HTTP/1.1\r\nHost: wrong\r\n'+self.KEY.encode()+b'\r\n\r\n']:
            with self.subTest(raw_length=len(raw)):self.exchange(self.state(),raw)

    def test_absolute_expiry_closes_owned_connection_and_blocks_post(self):
        from ariadne import theta_setup_browser as browser
        state=self.state();raw=self.post(state);connection=self.MemoryConnection(b'')
        state.active=connection;server=unittest.mock.Mock();state.expire(server)
        self.assertTrue(state.expired and connection.closed and connection.shutdown_called)
        server.socket.close.assert_called_once_with()
        with patch.object(browser.terminal,'save_key') as save:self.assertEqual(self.exchange(state,raw),b'')
        save.assert_not_called()

    def test_observed_crosssite_top_level_initial_get_allows_only_display_then_strict_post(self):
        from ariadne import theta_setup_browser as browser
        state=self.state()
        raw=('GET / HTTP/1.1\r\nHost: '+state.host+'\r\nSec-Fetch-Site: cross-site\r\nSec-Fetch-Mode: navigate\r\nSec-Fetch-Dest: document\r\n\r\n').encode()
        with patch.object(browser.terminal,'save_key',return_value={'configured':True}) as save:
            result=self.exchange(state,raw)
            self.assertIn(b'200 OK',result);self.assertIn(b'type="password"',result)
            save.assert_not_called();self.assertFalse(state.used)
            result=self.exchange(state,self.post(state,headers=lambda h:h+[('Sec-Fetch-Site','same-origin'),('Sec-Fetch-Mode','navigate'),('Sec-Fetch-Dest','document')]))
            self.assertIn(b'303 See Other',result);save.assert_called_once_with(state.data,self.KEY)

    def test_crosssite_get_exception_refuses_noninitial_embedded_or_unproven_navigation(self):
        cases=[('/',None,'cors','document'),('/',None,'no-cors','document'),('/',None,'navigate','iframe'),
               ('/',None,None,'document'),('/',None,'navigate',None),('/?key=ignored',None,'navigate','document'),
               ('/done',None,'navigate','document'),('/','same','navigate','document'),('/','http://evil.invalid','navigate','document')]
        for path,origin,mode,dest in cases:
            with self.subTest(path=path,origin=origin,mode=mode,dest=dest):
                state=self.state();state.saved=path=='/done'
                headers=[('Host',state.host),('Sec-Fetch-Site','cross-site')]
                if origin is not None:headers.append(('Origin',state.origin if origin=='same' else origin))
                if mode is not None:headers.append(('Sec-Fetch-Mode',mode))
                if dest is not None:headers.append(('Sec-Fetch-Dest',dest))
                raw=('GET '+path+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in headers)+'\r\n').encode()
                result=self.exchange(state,raw)
                self.assertNotIn(b'200 OK',result);self.assertNotIn(b'type="password"',result)

    def test_null_origin_post_stays_refused_with_otherwise_valid_same_origin_metadata(self):
        from ariadne import theta_setup_browser as browser
        state=self.state()
        raw=self.post(state,headers=lambda h:[(k,'null' if k=='Origin' else v) for k,v in h]+[('Sec-Fetch-Site','same-origin'),('Sec-Fetch-Mode','navigate'),('Sec-Fetch-Dest','document')])
        with patch.object(browser.terminal,'save_key') as save:result=self.exchange(state,raw)
        save.assert_not_called();self.assertFalse(state.used);self.assertNotIn(b'303 See Other',result)

    def test_crosssite_top_level_post_remains_refused_with_valid_origin_cookie_csrf(self):
        from ariadne import theta_setup_browser as browser
        state=self.state()
        raw=self.post(state,headers=lambda h:h+[('Sec-Fetch-Site','cross-site'),('Sec-Fetch-Mode','navigate'),('Sec-Fetch-Dest','document')])
        with patch.object(browser.terminal,'save_key') as save:result=self.exchange(state,raw)
        save.assert_not_called();self.assertFalse(state.used);self.assertNotIn(b'303 See Other',result)

    def test_setup_refuses_existing_or_environment_keys_before_listener(self):
        from ariadne import theta_setup_browser as browser
        data=unittest.mock.Mock();data._path.return_value.exists.return_value=True
        with patch.object(browser,'_Server') as server:
            with self.assertRaises(browser.ThetaSetupError):browser.serve(data,ready=lambda value:None)
        server.assert_not_called()
        data._path.return_value.exists.return_value=False
        with patch.dict(os.environ,{'THETADATA_API_KEY':''}),patch.object(browser,'_Server') as server:
            with self.assertRaises(browser.ThetaSetupError):browser.serve(data,ready=lambda value:None)
        server.assert_not_called()


if __name__=='__main__':unittest.main()
