"""Fictional keys and JARs only; preparation never executes vendor code."""
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ariadne import theta_terminal as t
from ariadne.tools import ToolRegistry
from scripts import theta_terminal as cli
from scripts import alpaca_market as controls

ROOT = Path(__file__).resolve().parents[1]
KEY = 'FICTIONAL_THETA_KEY_NOT_A_REAL_CREDENTIAL_0123456789'
AT = '2026-11-05T15:00:00+00:00'
VERSION = '202611050'


def jar(*, main='net.thetadata.Fictional', extras=(), manifest=None):
    output=BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('META-INF/MANIFEST.MF',manifest or 'Manifest-Version: 1.0\r\nMain-Class: '+main+'\r\n\r\n')
        archive.writestr(main.replace('.','/')+'.class',b'\xca\xfe\xba\xbe\x00\x00\x00\x41')
        for name,raw in extras:
            info=zipfile.ZipInfo(name);info.filename=name;archive.writestr(info,raw)
    return output.getvalue()


class Response:
    def __init__(self,payload=b'',status=200,media='text/plain',headers=None):
        self.payload=payload;self.status=status;self.position=0;self.closed=False;self.reads=0
        self.headers={'Content-Type':media,'Content-Length':str(len(payload)),**(headers or {})}
    def getheader(self,name,default=None):return self.headers.get(name,default)
    def read1(self,n):
        self.reads+=1;value=self.payload[self.position:self.position+n];self.position+=len(value);return value
    def close(self):self.closed=True


class Connection:
    def __init__(self,response):self.response=response;self.sock=None;self.calls=[];self.closed=False;self.debug=None
    def set_debuglevel(self,value):self.debug=value
    def request(self,*args,**kwargs):self.calls.append((args,kwargs))
    def getresponse(self):return self.response
    def close(self):self.closed=True


class FakeClient:
    calls=[]
    def __init__(self,key,*,online=False):
        assert key==KEY and online is True
        self.closed=False;self.calls=FakeClient.calls
    def version(self):self.calls.append('version');return {'version':VERSION,'received_at':AT}
    def application(self,version):
        assert version==VERSION;self.calls.append('application');raw=jar()
        return {'payload':raw,'received_at':AT,'sha256':hashlib.sha256(raw).hexdigest(),'evidence':t.inspect_jar(raw)}
    def close(self):self.closed=True


class ThetaTerminalTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/'runtime/tmp';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='theta-terminal-',dir=parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'data/workspace').mkdir(parents=True)
        self.guard=object.__new__(ToolRegistry);self.guard.root=self.root
        self.data=object.__new__(ToolRegistry);self.data.root=self.root/'data'
        self.patch=patch.dict(os.environ,{},clear=False);self.patch.start();self.addCleanup(self.patch.stop)
        os.environ.pop('THETADATA_API_KEY',None)
        FakeClient.calls=[]
        self.java=b'fictional-java-executable-not-runnable';self.bootstrap=jar()
        for name,raw in ((t.JAVA_PATH,self.java),(t.BOOTSTRAP_PATH,self.bootstrap)):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        for name,value in (('JAVA_SHA256',hashlib.sha256(self.java).hexdigest()),('BOOTSTRAP_SHA256',hashlib.sha256(self.bootstrap).hexdigest())):
            p=patch.object(t,name,value);p.start();self.addCleanup(p.stop)

    def prepare(self,check=lambda:'unchanged'):
        with patch.dict(os.environ,{'THETADATA_API_KEY':KEY}):
            return t.prepare(self.guard,self.data,check=check,client_factory=FakeClient)

    def test_status_is_passive_without_unlock_network_or_process(self):
        with patch.object(t,'_protect',side_effect=AssertionError('No decrypt')),patch.object(t.http.client,'HTTPSConnection',side_effect=AssertionError('No socket')):
            value=t.status(self.guard,self.data)
        self.assertTrue(value['java_pinned']);self.assertTrue(value['bootstrap_pinned'])
        self.assertFalse(value['key_configured']);self.assertFalse(value['application_prepared'])
        self.assertEqual(value['network_requests'],0);self.assertFalse(value['execution_allowed'])
        self.assertFalse((self.root/t.LIBRARY_PATH).exists())

    @unittest.skipUnless(os.name=='nt','Native Windows CurrentUser DPAPI')
    def test_dpapi_roundtrip_no_plaintext_or_password_file(self):
        value=t.save_key(self.data,KEY)
        self.assertTrue(t.configured(self.data));self.assertEqual(t.load_key(self.data),KEY)
        raw=(self.data.root/t.PRIVATE_PATH).read_bytes()
        self.assertNotIn(KEY.encode(),raw);self.assertEqual(value['network_requests'],0)
        self.assertEqual([p.name for p in (self.data.root/t.PRIVATE_PATH).parent.iterdir()],['credential.dpapi'])
        with self.assertRaises(FileExistsError):t.save_key(self.data,KEY)

    def test_environment_key_precedes_disk_and_empty_key_never_falls_back(self):
        with patch.dict(os.environ,{'THETADATA_API_KEY':KEY}),patch.object(t,'_read',side_effect=AssertionError('No disk read')):
            self.assertTrue(t.configured(self.data));self.assertEqual(t.load_key(self.data),KEY)
        with patch.dict(os.environ,{'THETADATA_API_KEY':''}),patch.object(t,'_read',side_effect=AssertionError('No fallback')):
            with self.assertRaises(t.ThetaTerminalError):t.load_key(self.data)

    def test_invalid_key_never_encrypted_or_written(self):
        for key in (None,True,'','short','x'*4097,'control\n'+KEY,'nonascii-ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©'+KEY):
            with patch.object(t,'_protect',side_effect=AssertionError('No encryption')):
                with self.assertRaises(t.ThetaTerminalError) as caught:t.save_key(self.data,key)
                self.assertNotIn(KEY,str(caught.exception))
        self.assertFalse((self.data.root/t.PRIVATE_PATH).exists())

    def test_invalid_or_cross_provider_envelope_refuses_before_decrypt(self):
        for changes in ({'schema':True},{'source_id':'public'},{'blob':'bad*'},{'protection':'plaintext'},{'extra':KEY}):
            value={'schema':1,'source_id':t.SOURCE_ID,'protection':'windows-current-user-dpapi','blob':base64.b64encode(b'fictional').decode(),**changes}
            path=self.data.root/t.PRIVATE_PATH;path.parent.mkdir(exist_ok=True);path.write_bytes(json.dumps(value).encode())
            with patch.object(t,'_protect',side_effect=AssertionError('No decrypt')):
                with self.assertRaises(t.ThetaTerminalError):t.configured(self.data)
                with self.assertRaises(t.ThetaTerminalError):t.load_key(self.data)

    def test_bad_unlocked_inner_payload_is_not_adopted(self):
        path=self.data.root/t.PRIVATE_PATH;path.parent.mkdir();path.write_bytes(json.dumps({'schema':1,'source_id':t.SOURCE_ID,'protection':'windows-current-user-dpapi','blob':base64.b64encode(b'fictional').decode()}).encode())
        for raw in (b'{}',b'{"schema":NaN}',json.dumps({'source_id':'public','api_key':KEY}).encode()):
            with patch.object(t,'_protect',return_value=raw):
                with self.assertRaises(t.ThetaTerminalError):t.load_key(self.data)

    def test_concurrent_key_store_is_not_overwritten(self):
        original=b'fictional other completed store'
        def compete(path,payload):path.write_bytes(original);t.publish_bytes_original(path,payload)
        with patch.object(t,'_protect',return_value=b'fictional encrypted'),patch.object(t,'publish_bytes_original',t.publish_bytes,create=True),patch.object(t,'publish_bytes',side_effect=compete):
            with self.assertRaises(FileExistsError):t.save_key(self.data,KEY)
        self.assertEqual((self.data.root/t.PRIVATE_PATH).read_bytes(),original)

    def test_preparation_publishes_exact_jar_and_manifest_but_never_runs_it(self):
        with patch('subprocess.Popen',side_effect=AssertionError('Never execute target')):
            value=self.prepare()
        self.assertEqual(FakeClient.calls,['version','application']);self.assertFalse(value['application_launched'])
        self.assertEqual(value['paper_fills'],0);self.assertEqual(value['funding_added'],'0')
        self.assertFalse(value['execution_allowed']);self.assertFalse(value['publisher_checksum_independently_verified'])
        manifest=json.loads((self.root/t.APPLICATION_PATH).read_text())
        raw=(self.root/manifest['artifact']['path']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),manifest['artifact']['sha256'])
        self.assertNotIn(KEY,(self.root/t.APPLICATION_PATH).read_text())
        self.assertTrue(t.status(self.guard,self.data)['application_prepared'])
        self.assertFalse((self.root/'data/ariadne.sqlite3').exists())

    def test_existing_complete_or_uncertain_preparation_never_unlocks_or_requests(self):
        for location in (t.LIBRARY_PATH,t.APPLICATION_PATH):
            path=self.root/location;path.parent.mkdir(exist_ok=True)
            if location==t.LIBRARY_PATH:path.mkdir()
            else:path.write_text('{}')
            with patch.object(t,'load_key',side_effect=AssertionError('No unlock')):
                with self.assertRaises(FileExistsError):self.prepare()
            if path.is_dir():path.rmdir()
            else:path.unlink()
        self.prepare()
        with self.assertRaises(FileExistsError):self.prepare()
        self.assertEqual(FakeClient.calls,['version','application'])

    def test_bad_pins_or_owner_hold_refuse_before_key(self):
        (self.root/t.JAVA_PATH).write_bytes(b'changed')
        with patch.object(t,'load_key',side_effect=AssertionError('No unlock')):
            with self.assertRaises(t.ThetaTerminalError):self.prepare()
        with self.assertRaisesRegex(ValueError,'held'):self.prepare(check=lambda:(_ for _ in ()).throw(ValueError('held')))
        self.assertFalse((self.root/t.LIBRARY_PATH).exists())

    def test_revocation_at_each_publication_seam_stops_and_retains_failure(self):
        # The setup for each subcase uses a separate otherwise identical fresh root.
        for change_on in (2,3,4,5):
            child=self.root/str(change_on);child.mkdir();guard=object.__new__(ToolRegistry);guard.root=child
            data=object.__new__(ToolRegistry);data.root=self.data.root
            for name,raw in ((t.JAVA_PATH,self.java),(t.BOOTSTRAP_PATH,self.bootstrap)):
                p=child/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            calls=[]
            def check():calls.append(None);return 'old' if len(calls)<change_on else 'new'
            with patch.dict(os.environ,{'THETADATA_API_KEY':KEY}):
                with self.assertRaises(t.ThetaTerminalUnavailable):t.prepare(guard,data,check=check,client_factory=FakeClient)
            self.assertFalse((child/t.APPLICATION_PATH).exists())
            self.assertTrue((child/t.LIBRARY_PATH/'preparation-failed.json').is_file())
            self.assertNotIn(KEY,(child/t.LIBRARY_PATH/'preparation-failed.json').read_text())

    def test_download_failure_is_held_without_raw_error_or_secret_receipt(self):
        class Failure(FakeClient):
            def version(self):raise t.ThetaTerminalUnavailable('Safe provider failure')
        with patch.dict(os.environ,{'THETADATA_API_KEY':KEY}):
            with self.assertRaises(t.ThetaTerminalUnavailable):t.prepare(self.guard,self.data,check=lambda:'same',client_factory=Failure)
        value=t.status(self.guard,self.data);self.assertEqual(value['application_state'],'incomplete_preparation_held')
        with self.assertRaises(FileExistsError):self.prepare()
        for file in (self.root/t.LIBRARY_PATH).iterdir():self.assertNotIn(KEY,file.read_text())

    def test_manifest_or_artifact_tampering_refuses_status(self):
        self.prepare();p=self.root/t.APPLICATION_PATH;manifest=json.loads(p.read_text());path=self.root/manifest['artifact']['path'];path.write_bytes(b'changed')
        with self.assertRaises(t.ThetaTerminalError):t.status(self.guard,self.data)
        path.write_bytes(jar());manifest['execution_allowed']=True;p.write_text(json.dumps(manifest))
        with self.assertRaises(t.ThetaTerminalError):t.status(self.guard,self.data)

    def test_signed_manifest_sections_are_not_misparsed_as_main_attributes(self):
        raw=jar(manifest='Manifest-Version: 1.0\r\nMain-Class: net.thetadata.Fictional\r\n\r\nName: A.class\r\nSHA-256-Digest: fictional\r\n\r\nName: B.class\r\nSHA-256-Digest: fictional\r\n\r\n')
        self.assertEqual(t.inspect_jar(raw)['minimum_java_from_main_class'],21)

    def test_traversal_backslash_absolute_and_link_members_are_rejected(self):
        for name in ('../outside','/absolute','folder/../../outside','bad\\name','C:bad'):
            with self.assertRaises(t.ThetaTerminalError):t.inspect_jar(jar(extras=((name,b'fictional'),)))
        b=BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            link=zipfile.ZipInfo('link');link.external_attr=(0o120777<<16);z.writestr(link,'../outside')
        with self.assertRaises(t.ThetaTerminalError):t.inspect_jar(b.getvalue())

    def test_invalid_main_manifest_nonjar_and_duplicate_fields_refused(self):
        for raw in (b'not a jar',jar(main='../../Bad'),jar(manifest='Manifest-Version: 1.0\nMain-Class: net.thetadata.Fictional\nMain-Class: net.thetadata.Fictional\n\n')):
            with self.assertRaises(t.ThetaTerminalError):t.inspect_jar(raw)

    def test_fixed_tls_private_route_and_no_key_in_output(self):
        response=Response(VERSION.encode());connection=Connection(response)
        os.environ['ARIADNE_OFFLINE']='0'
        with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=connection) as create,patch.dict(os.environ,{'HTTPS_PROXY':'https://invalid.example','HTTP_PROXY':'http://invalid.example'}):
            value=t.ThetaBootstrapClient(KEY,online=True).version()
        self.assertEqual(create.call_args.args,(t.HOST,));self.assertEqual(create.call_args.kwargs['port'],443)
        self.assertEqual(connection.calls[0][0],('GET','/bootstrap/current/'+KEY))
        self.assertEqual(connection.debug,0);self.assertTrue(connection.closed);self.assertTrue(response.closed)
        self.assertNotIn(KEY,json.dumps(value));self.assertEqual(value['version'],VERSION)

    def test_special_characters_in_private_key_cannot_change_route(self):
        key=KEY+'/../?secret#fragment';response=Response(VERSION.encode());connection=Connection(response)
        os.environ['ARIADNE_OFFLINE']='0'
        with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=connection):
            t.ThetaBootstrapClient(key,online=True).version()
        route=connection.calls[0][0][1]
        self.assertIn('%2F..%2F%3Fsecret%23fragment',route);self.assertNotIn('?secret',route)

    def test_unsupported_operation_version_and_offline_stop_before_socket(self):
        for op,kwargs in (('orders',{}),('application',{'version':'../bad'}),('version',{'version':VERSION})):
            with patch.object(t.http.client,'HTTPSConnection',side_effect=AssertionError('No socket')):
                with self.assertRaises(t.ThetaTerminalError):t.ThetaBootstrapClient(KEY,online=True)._http(op,**kwargs)
        for online,env in ((False,'0'),(True,'1')):
            with patch.dict(os.environ,{'ARIADNE_OFFLINE':env}),patch.object(t.http.client,'HTTPSConnection',side_effect=AssertionError('No socket')):
                with self.assertRaises(t.ThetaTerminalUnavailable):t.ThetaBootstrapClient(KEY,online=online).version()

    def test_redirect_auth_rate_limit_and_provider_errors_never_read_bodies(self):
        os.environ['ARIADNE_OFFLINE']='0'
        for code in (301,302,401,403,429,500):
            response=Response(KEY.encode(),status=code);connection=Connection(response)
            with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=connection):
                with self.assertRaises(t.ThetaTerminalUnavailable) as caught:t.ThetaBootstrapClient(KEY,online=True).version()
            self.assertNotIn(KEY,str(caught.exception));self.assertEqual(response.reads,0);self.assertEqual(len(connection.calls),1)

    def test_transport_exception_and_reflected_key_never_escape(self):
        os.environ['ARIADNE_OFFLINE']='0'
        with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',side_effect=OSError('private '+KEY)):
            with self.assertRaises(t.ThetaTerminalUnavailable) as caught:t.ThetaBootstrapClient(KEY,online=True).version()
        self.assertNotIn(KEY,str(caught.exception))
        with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=Connection(Response(KEY.encode()))):
            with self.assertRaises(t.ThetaTerminalUnavailable) as caught:t.ThetaBootstrapClient(KEY,online=True).version()
        self.assertNotIn(KEY,str(caught.exception))

    def test_content_bounds_encoding_metadata_and_application_are_validated(self):
        os.environ['ARIADNE_OFFLINE']='0'
        for response in (Response(b'wrong-version'),Response(b'{}',media='application/json'),Response(VERSION.encode(),headers={'Content-Encoding':'gzip'}),Response(VERSION.encode(),headers={'Content-Length':'129'})):
            with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=Connection(response)):
                with self.assertRaises(t.ThetaTerminalUnavailable):t.ThetaBootstrapClient(KEY,online=True).version()
        raw=jar();connection=Connection(Response(raw,media='application/java-archive'))
        with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=connection):
            result=t.ThetaBootstrapClient(KEY,online=True).application(VERSION)
        self.assertEqual(result['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(connection.calls[0][0][1],'/bootstrap/jars/'+VERSION)

    def test_deadline_and_closed_client_stop_before_socket(self):
        os.environ['ARIADNE_OFFLINE']='0';client=t.ThetaBootstrapClient(KEY,online=True);client._deadline=0
        with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',side_effect=AssertionError('No socket')):
            with self.assertRaises(t.ThetaTerminalUnavailable):client.version()
            client.close()
            with self.assertRaises(t.ThetaTerminalUnavailable):client.version()

    def test_cli_setup_hidden_only_and_unknown_key_arguments_never_echo(self):
        args=cli.parser().parse_args(['setup'])
        with patch.object(cli,'_hidden',return_value=KEY),patch.object(t,'save_key',return_value={'configured':True}) as save:
            self.assertEqual(cli.run(args,root=self.root),{'configured':True})
        save.assert_called_once()
        err=StringIO()
        with redirect_stderr(err):self.assertEqual(cli.main(['setup','--api-key',KEY]),2)
        self.assertNotIn(KEY,err.getvalue())
        with patch.object(cli.sys.stdin,'isatty',return_value=False),patch.object(cli.getpass,'getpass',side_effect=AssertionError('No piped key')):
            with self.assertRaises(t.ThetaTerminalError):cli._hidden()

    def test_cli_hold_and_inherited_offline_precede_unlock(self):
        args=cli.parser().parse_args(['prepare','--online'])
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'1'}),patch.object(t,'load_key',side_effect=AssertionError('No unlock')):
            with self.assertRaises(t.ThetaTerminalError):cli.run(args,root=self.root,client_factory=FakeClient)
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'}),patch('ariadne.network_policy.network_policy_status',return_value={'mode':'online'}),patch.object(cli,'_hidden',side_effect=AssertionError('No prompt')),patch.object(controls,'_network_allowed',side_effect=ValueError('held')) as owner_check:
            with self.assertRaises(ValueError):cli.run(args,root=self.root,client_factory=FakeClient)
            owner_check.assert_called_once()
        self.assertFalse((self.root/t.LIBRARY_PATH).exists())

    def test_cli_local_actions_never_accept_online_or_prepare_without_online(self):
        for argv in (['status','--online'],['setup','--online'],['prepare']):
            with self.assertRaises(t.ThetaTerminalError):cli.run(cli.parser().parse_args(argv),root=self.root)

    def test_one_click_setup_has_no_key_argument_download_or_java_launch(self):
        text=(ROOT/'scripts/Set up Theta Terminal.cmd').read_text()
        self.assertIn('scripts\\theta_terminal.py" setup',text)
        self.assertNotIn('--online',text);self.assertNotIn('--api-key',text);self.assertNotIn('java.exe',text)
        self.assertNotIn('%*',text)

    def test_lying_metadata_length_cannot_bypass_actual_body_bound(self):
        os.environ['ARIADNE_OFFLINE']='0'
        response=Response(b'x'*129,headers={'Content-Length':'1'});connection=Connection(response)
        with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=connection):
            with self.assertRaises(t.ThetaTerminalUnavailable):t.ThetaBootstrapClient(KEY,online=True).version()
        self.assertTrue(response.closed);self.assertEqual(response.position,129)

    def test_cli_prepare_end_to_end_uses_owner_check_and_no_financial_store(self):
        args=cli.parser().parse_args(['prepare','--online'])
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'0','THETADATA_API_KEY':KEY}),patch('ariadne.network_policy.network_policy_status',return_value={'mode':'online'}),patch.object(controls,'_network_allowed',return_value='unchanged') as check:
            value=cli.run(args,root=self.root,client_factory=FakeClient)
        self.assertEqual(value['status'],'prepared_for_static_review');self.assertEqual(check.call_count,5)
        self.assertFalse((self.root/'data/ariadne.sqlite3').exists());self.assertFalse(value['execution_allowed'])

    def test_cli_generic_failure_never_echoes_private_exception_text(self):
        err=StringIO()
        with patch.object(cli,'run',side_effect=OSError('private '+KEY)),redirect_stderr(err):
            self.assertEqual(cli.main(['status']),2)
        self.assertNotIn(KEY,err.getvalue());self.assertNotIn('Traceback',err.getvalue())

    def test_assigned_versions_accept_bounded_numeric_dates_without_override_assumption(self):
        os.environ['ARIADNE_OFFLINE']='0'
        for version in ('20261105','202611050','202611050123'):
            self.assertEqual(t._version(version),version)
            connection=Connection(Response(version.encode('ascii')))
            with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=connection):
                value=t.ThetaBootstrapClient(KEY,online=True).version()
            self.assertEqual(value['version'],version)
        for version in ('2026110','2026110501234','2026/1105','20261105.jar','../../20261105','２０２６１１０５','20261105?key'):
            with self.assertRaises(t.ThetaTerminalError):t._version(version)

    def test_http_diagnostics_keep_only_status_and_no_error_body_or_reason(self):
        os.environ['ARIADNE_OFFLINE']='0'
        class HostileResponse(Response):
            @property
            def reason(self):raise AssertionError('Never access private reason '+KEY)
        for code in (401,403,404,302):
            response=HostileResponse(KEY.encode(),status=code,media=KEY,
                headers={'Location':'https://invalid.example/'+KEY})
            connection=Connection(response)
            with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=connection):
                with self.assertRaises(t.ThetaTerminalUnavailable) as caught:t.ThetaBootstrapClient(KEY,online=True).version()
            diagnostic=caught.exception.diagnostic
            self.assertEqual(diagnostic['http_status'],code)
            self.assertEqual(diagnostic['stage'],'http_status')
            self.assertEqual(diagnostic['operation'],'version')
            self.assertTrue(diagnostic['request_attempted']);self.assertTrue(diagnostic['request_dispatched'])
            self.assertTrue(diagnostic['response_observed']);self.assertEqual(diagnostic['bytes_observed'],0)
            self.assertIsNone(diagnostic['media_valid']);self.assertEqual(response.reads,0)
            self.assertNotIn(KEY,json.dumps(diagnostic));self.assertNotIn(KEY,str(caught.exception))

    def test_bad_media_encoding_and_version_are_classified_without_provider_text(self):
        os.environ['ARIADNE_OFFLINE']='0'
        for response,stage,flag in ((Response(KEY.encode(),media=KEY),'content_policy','media_valid'),
                (Response(KEY.encode(),headers={'Content-Encoding':KEY}),'content_policy','encoding_valid'),
                (Response(b'wrong-version'),'version_decode','version_valid')):
            with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=Connection(response)):
                with self.assertRaises(t.ThetaTerminalUnavailable) as caught:t.ThetaBootstrapClient(KEY,online=True).version()
            diagnostic=caught.exception.diagnostic
            self.assertEqual(diagnostic['stage'],stage);self.assertIs(diagnostic[flag],False)
            self.assertEqual(diagnostic['http_status'],200);self.assertTrue(diagnostic['response_observed'])
            self.assertEqual(diagnostic['bytes_observed'],13 if flag=='version_valid' else 0)
            self.assertNotIn(KEY,json.dumps(diagnostic));self.assertNotIn(KEY,str(caught.exception))

    def test_transport_tls_and_uncertain_dispatch_are_not_reported_as_http(self):
        os.environ['ARIADNE_OFFLINE']='0'
        class BrokenDispatch(Connection):
            def request(self,*args,**kwargs):raise t.ssl.SSLError('private '+KEY)
        cases=((OSError('private '+KEY),None,'connect',False,False),
            (t.ssl.SSLError('private '+KEY),None,'tls',False,False),
            (None,BrokenDispatch(Response()),'tls',True,None))
        for error,connection,stage,attempted,dispatched in cases:
            seam=patch.object(t.http.client,'HTTPSConnection',side_effect=error) if error else patch.object(t.http.client,'HTTPSConnection',return_value=connection)
            with patch.object(t,'install_network_policy',return_value={'mode':'online'}),seam:
                with self.assertRaises(t.ThetaTerminalUnavailable) as caught:t.ThetaBootstrapClient(KEY,online=True).version()
            diagnostic=caught.exception.diagnostic
            self.assertEqual(diagnostic['stage'],stage);self.assertIs(diagnostic['request_attempted'],attempted)
            self.assertIs(diagnostic['request_dispatched'],dispatched)
            self.assertFalse(diagnostic['response_observed']);self.assertIsNone(diagnostic['http_status'])
            self.assertNotIn(KEY,json.dumps(diagnostic));self.assertNotIn(KEY,str(caught.exception))

    def test_diagnostic_sanitizer_never_formats_unknown_objects_or_accepts_bool_numbers(self):
        class Hostile:
            def __str__(self):raise AssertionError('No string conversion')
            def __repr__(self):raise AssertionError('No repr')
            def __eq__(self,other):raise AssertionError('No equality')
        hostile=Hostile()
        value=t.safe_diagnostic({'operation':hostile,'stage':hostile,'http_status':True,
            'bytes_observed':True,'request_attempted':1,'response_observed':'true',
            'request_dispatched':1,'media_valid':1,'encoding_valid':KEY,'size_valid':hostile,
            'version_valid':[],'url':KEY,'secret':KEY})
        self.assertEqual(value,t.safe_diagnostic());self.assertNotIn(KEY,json.dumps(value))
        class HostileKey(Hostile):
            def __hash__(self):return hash('operation')
        self.assertEqual(t.safe_diagnostic({HostileKey():KEY}),t.safe_diagnostic())
        for status in (-1,99,600,200.0,KEY):
            value=t.safe_diagnostic({'http_status':status,'request_attempted':True,
                'request_dispatched':True,'response_observed':True})
            self.assertIsNone(value['http_status'])
        for count in (-1,t.MAX_JAR_BYTES+2,1.0,KEY):self.assertEqual(t.safe_diagnostic({'bytes_observed':count})['bytes_observed'],0)
        contradictory=t.safe_diagnostic({'http_status':200,'bytes_observed':12,'response_observed':True,'version_valid':True})
        self.assertEqual(contradictory,t.safe_diagnostic())

    def test_exception_diagnostic_copy_and_unknown_exception_properties_are_safe(self):
        error=t.ThetaTerminalUnavailable('Safe failure',diagnostic={'operation':'version','stage':'http_status',
            'http_status':403,'request_attempted':True,'request_dispatched':True,'response_observed':True,'secret':KEY})
        copied=error.diagnostic;copied['http_status']=401;copied['url']=KEY
        self.assertEqual(error.diagnostic['http_status'],403);self.assertNotIn(KEY,json.dumps(error.diagnostic))
        class HostileError(OSError):
            @property
            def diagnostic(self):raise AssertionError('Never access untrusted properties')
            def __str__(self):raise AssertionError('Never format unknown exception')
        self.assertEqual(t.diagnostic_from_error(HostileError(KEY)),t.safe_diagnostic())
        error._diagnostic={'operation':KEY,'stage':KEY,'http_status':True,'secret':KEY}
        self.assertEqual(t.diagnostic_from_error(error),t.safe_diagnostic())
        class HostileDict(dict):
            def get(self,*args):raise AssertionError('Never invoke unknown dictionary overrides')
        error.__dict__=HostileDict(error.__dict__)
        self.assertEqual(t.diagnostic_from_error(error),t.safe_diagnostic())

    def test_failure_receipt_persists_safe_http_diagnostic_and_never_replays(self):
        os.environ['ARIADNE_OFFLINE']='0'
        response=Response(KEY.encode(),status=404);connection=Connection(response)
        with patch.dict(os.environ,{'THETADATA_API_KEY':KEY}),patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=connection):
            with self.assertRaises(t.ThetaTerminalUnavailable):t.prepare(self.guard,self.data,check=lambda:'unchanged')
        path=self.root/t.LIBRARY_PATH/'preparation-failed.json';before=path.read_bytes();receipt=json.loads(before)
        self.assertEqual(receipt['diagnostic']['http_status'],404);self.assertEqual(receipt['diagnostic']['operation'],'version')
        self.assertNotIn(KEY,before.decode());self.assertEqual(len(connection.calls),1)
        with patch.object(t,'load_key',side_effect=AssertionError('No unlock/replay')):
            with self.assertRaises(FileExistsError):self.prepare()
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(t.status(self.guard,self.data)['application_state'],'incomplete_preparation_held')

    def test_local_failure_receipt_never_invokes_hostile_exception_property(self):
        class HostileError(OSError):
            @property
            def diagnostic(self):raise AssertionError('Do not inspect unknown exception')
        class BrokenClient(FakeClient):
            def version(self):raise HostileError('private '+KEY)
        with patch.dict(os.environ,{'THETADATA_API_KEY':KEY}):
            with self.assertRaises(HostileError):t.prepare(self.guard,self.data,check=lambda:'same',client_factory=BrokenClient)
        receipt=json.loads((self.root/t.LIBRARY_PATH/'preparation-failed.json').read_bytes())
        self.assertEqual(receipt['diagnostic']['stage'],'local');self.assertIsNone(receipt['diagnostic']['http_status'])
        self.assertFalse(receipt['diagnostic']['request_attempted']);self.assertNotIn(KEY,json.dumps(receipt))

    def test_second_request_transport_failure_cannot_inherit_first_http_status(self):
        os.environ['ARIADNE_OFFLINE']='0'
        client=t.ThetaBootstrapClient(KEY,online=True)
        with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=Connection(Response(VERSION.encode()))):
            self.assertEqual(client.version()['diagnostic']['http_status'],200)
        with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',side_effect=OSError('private '+KEY)):
            with self.assertRaises(t.ThetaTerminalUnavailable) as caught:client.application(VERSION)
        diagnostic=caught.exception.diagnostic
        self.assertEqual(diagnostic['operation'],'application');self.assertEqual(diagnostic['stage'],'connect')
        self.assertIsNone(diagnostic['http_status']);self.assertFalse(diagnostic['response_observed'])
        self.assertNotIn(KEY,json.dumps(diagnostic))

    def test_declared_content_length_must_match_completed_observed_body(self):
        os.environ['ARIADNE_OFFLINE']='0'
        for declared in ('8','10'):
            response=Response(VERSION.encode(),headers={'Content-Length':declared})
            with patch.object(t,'install_network_policy',return_value={'mode':'online'}),patch.object(t.http.client,'HTTPSConnection',return_value=Connection(response)):
                with self.assertRaises(t.ThetaTerminalUnavailable) as caught:t.ThetaBootstrapClient(KEY,online=True).version()
            diagnostic=caught.exception.diagnostic
            self.assertEqual(diagnostic['stage'],'size');self.assertIs(diagnostic['size_valid'],False)
            self.assertEqual(diagnostic['http_status'],200);self.assertEqual(diagnostic['bytes_observed'],len(VERSION))
            self.assertIsNone(diagnostic['version_valid']);self.assertNotIn(KEY,json.dumps(diagnostic))


if __name__=='__main__':unittest.main()
