"""Independent safe-diagnostic review; fictional in-memory HTTP and private fixtures."""
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ariadne import theta_terminal as t
from ariadne.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
KEY = 'review.DiagnosticSecret0123456789'
VERSION = '20261105'
FIELDS = {'schema','operation','stage','http_status','bytes_observed',
          'request_attempted','request_dispatched','response_observed',
          'media_valid','encoding_valid','size_valid','version_valid'}


def jar(main='example.Fictional'):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\r\nMain-Class: '+main+'\r\n\r\n')
        archive.writestr(main.replace('.', '/')+'.class', b'\xca\xfe\xba\xbe\x00\x00\x00\x41')
    return output.getvalue()


class Response:
    def __init__(self, raw=b'', *, status=200, headers=None):
        self.raw, self.status, self.offset, self.reads = raw, status, 0, 0
        self.headers = {'Content-Type':'text/plain','Content-Length':str(len(raw)), **(headers or {})}
    def getheader(self, name, default=None):return self.headers.get(name, default)
    @property
    def reason(self):raise AssertionError('Never inspect a reason phrase')
    def read1(self, size):
        self.reads += 1
        value = self.raw[self.offset:self.offset+size];self.offset += len(value)
        return value
    def close(self):pass


class Connection:
    def __init__(self, response, *, failure=None):
        self.response, self.failure, self.sock, self.calls = response, failure, None, 0
    def set_debuglevel(self, value):
        if value != 0:raise AssertionError('No debug logging')
    def request(self, *args, **kwargs):
        self.calls += 1
        if self.failure == 'request':raise OSError(KEY)
    def getresponse(self):
        if self.failure == 'response':raise http.client.HTTPException(KEY)
        return self.response
    def close(self):pass


class ThetaTerminalDiagnosticReview(unittest.TestCase):
    def setUp(self):
        parent = ROOT/'runtime/tmp';parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='theta-diagnostic-review-', dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name);(self.root/'data').mkdir()
        self.box = object.__new__(ToolRegistry);self.box.root = self.root
        self.data = object.__new__(ToolRegistry);self.data.root = self.root/'data'
        for relative, raw, attribute in ((t.JAVA_PATH,b'fictional java never executed','JAVA_SHA256'),
                                         (t.BOOTSTRAP_PATH,jar(),'BOOTSTRAP_SHA256')):
            path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            p=patch.object(t,attribute,hashlib.sha256(raw).hexdigest());p.start();self.addCleanup(p.stop)
        for p in (patch.dict(os.environ,{'THETADATA_API_KEY':KEY,'ARIADNE_OFFLINE':'0'}),
                  patch.object(t,'install_network_policy',return_value={'mode':'online'}),
                  patch('socket.socket.connect',side_effect=AssertionError('No actual socket')),
                  patch('subprocess.Popen',side_effect=AssertionError('No target execution'))):
            p.start();self.addCleanup(p.stop)

    def safe(self, value):
        self.assertEqual(set(value),FIELDS)
        encoded=json.dumps(value,sort_keys=True)
        self.assertNotIn(KEY,encoded);self.assertNotIn(t.HOST,encoded)
        self.assertEqual(value['schema'],1);self.assertIs(type(value['schema']),int)
        self.assertIn(value['operation'],(None,'version','application'))
        self.assertIn(value['stage'],t._DIAGNOSTIC_STAGES)
        if value['http_status'] is not None:
            self.assertIs(type(value['http_status']),int);self.assertGreaterEqual(value['http_status'],100);self.assertLessEqual(value['http_status'],599)
        self.assertIs(type(value['bytes_observed']),int)
        self.assertGreaterEqual(value['bytes_observed'],0);self.assertLessEqual(value['bytes_observed'],t.MAX_JAR_BYTES+1)
        for name in ('request_attempted','response_observed'):
            self.assertIs(type(value[name]),bool)
        for name in ('request_dispatched','media_valid','encoding_valid','size_valid','version_valid'):
            self.assertTrue(value[name] is None or type(value[name]) is bool)
        return value

    def error(self, connection, *, operation='version'):
        out=StringIO();err=StringIO()
        with patch.object(t.http.client,'HTTPSConnection',return_value=connection),redirect_stdout(out),redirect_stderr(err):
            client=t.ThetaBootstrapClient(KEY,online=True)
            with self.assertRaises(t.ThetaTerminalError) as caught:
                client.version() if operation=='version' else client.application(VERSION)
        self.assertNotIn(KEY,str(caught.exception));self.assertEqual(out.getvalue(),'');self.assertEqual(err.getvalue(),'')
        return self.safe(caught.exception.diagnostic)

    def prepare_failure(self, error):
        class FailingClient:
            def __init__(self, key, *, online=False):pass
            def version(self):raise error
            def close(self):pass
        with self.assertRaises(Exception):t.prepare(self.box,self.data,check=lambda:'stable',client_factory=FailingClient)
        path=self.root/t.LIBRARY_PATH/'preparation-failed.json'
        self.assertTrue(path.is_file(),'Failure must retain a diagnostic receipt')
        raw=path.read_bytes();self.assertNotIn(KEY.encode(),raw)
        value=json.loads(raw);self.assertEqual(value['status'],'failed')
        self.assertFalse((self.root/t.APPLICATION_PATH).exists())
        return self.safe(value['diagnostic'])

    def test_non200_status_is_numeric_observation_without_body_reason_or_retry(self):
        for code in (301,302,401,403,429,500):
            with self.subTest(code=code):
                response=Response(KEY.encode(),status=code);connection=Connection(response)
                value=self.error(connection)
                self.assertEqual(value['http_status'],code);self.assertTrue(value['response_observed'])
                self.assertTrue(value['request_attempted']);self.assertIs(value['request_dispatched'],True)
                self.assertEqual(value['stage'],'http_status');self.assertEqual(value['bytes_observed'],0)
                self.assertEqual(response.reads,0);self.assertEqual(connection.calls,1)

    def test_invalid_status_types_cannot_become_verified_http_codes(self):
        for code in (True,403.0,KEY,99,600):
            with self.subTest(code_type=type(code).__name__):
                response=Response(KEY.encode(),status=code);value=self.error(Connection(response))
                self.assertIsNone(value['http_status']);self.assertEqual(response.reads,0)

    def test_unaccepted_media_and_encoding_record_only_flags_not_header_text(self):
        for headers,field in (({'Content-Type':KEY},'media_valid'),({'Content-Encoding':KEY},'encoding_valid')):
            with self.subTest(field=field):
                response=Response(KEY.encode(),headers=headers);value=self.error(Connection(response))
                self.assertEqual(value['http_status'],200);self.assertIs(value[field],False)
                self.assertEqual(value['stage'],'content_policy');self.assertEqual(response.reads,0)

    def test_version_decode_failure_is_distinct_from_http_rejection(self):
        for raw in (b'"20261105x"',b'not-an-assigned-version',b'\xff'):
            with self.subTest(byte_count=len(raw)):
                value=self.error(Connection(Response(raw)))
                self.assertEqual(value['http_status'],200);self.assertEqual(value['stage'],'version_decode')
                self.assertIs(value['version_valid'],False);self.assertEqual(value['bytes_observed'],len(raw))
                self.assertIs(value['media_valid'],True);self.assertIs(value['encoding_valid'],True)

    def test_actual_body_overflow_and_header_bound_are_distinguished_safely(self):
        response=Response(b'x'*129,headers={'Content-Length':'1'})
        value=self.error(Connection(response));self.assertEqual(value['bytes_observed'],129)
        self.assertIs(value['size_valid'],False);self.assertEqual(value['http_status'],200)
        response=Response(KEY.encode(),headers={'Content-Length':KEY})
        value=self.error(Connection(response));self.assertEqual(value['bytes_observed'],0)
        self.assertIs(value['size_valid'],False);self.assertEqual(response.reads,0)

    def test_transport_failure_records_dispatch_uncertainty_without_private_exception(self):
        with patch.object(t.http.client,'HTTPSConnection',side_effect=OSError(KEY)):
            with self.assertRaises(t.ThetaTerminalError) as caught:t.ThetaBootstrapClient(KEY,online=True).version()
        value=self.safe(caught.exception.diagnostic)
        self.assertFalse(value['request_attempted']);self.assertIs(value['request_dispatched'],False)
        self.assertIsNone(value['http_status']);self.assertNotIn(KEY,str(caught.exception))
        for where,expected in (('request',None),('response',True)):
            value=self.error(Connection(Response(),failure=where))
            self.assertTrue(value['request_attempted']);self.assertIs(value['request_dispatched'],expected)
            self.assertFalse(value['response_observed']);self.assertIsNone(value['http_status'])

    def test_second_operation_transport_error_cannot_inherit_prior200_or_body_count(self):
        connections=[Connection(Response(VERSION.encode())),Connection(Response(),failure='request')]
        with patch.object(t.http.client,'HTTPSConnection',side_effect=connections):
            client=t.ThetaBootstrapClient(KEY,online=True);first=client.version()
            self.assertEqual(first['version'],VERSION);self.safe(first['diagnostic'])
            with self.assertRaises(t.ThetaTerminalError) as caught:client.application(VERSION)
        value=self.safe(caught.exception.diagnostic)
        self.assertEqual(value['operation'],'application');self.assertIsNone(value['http_status'])
        self.assertEqual(value['bytes_observed'],0);self.assertFalse(value['response_observed'])
        self.assertIsNone(value['media_valid']);self.assertIsNone(value['version_valid'])

    def test_raw_and_compressed_manifest_key_reflection_never_enters_diagnostics(self):
        value=self.error(Connection(Response(KEY.encode())))
        self.assertEqual(value['stage'],'credential_reflection');self.assertEqual(value['bytes_observed'],len(KEY))
        raw=jar(KEY);self.assertNotIn(KEY.encode(),raw)
        value=self.error(Connection(Response(raw,headers={'Content-Type':'application/java-archive'})),operation='application')
        self.assertEqual(value['stage'],'credential_reflection');self.assertEqual(value['http_status'],200)

    def test_sanitizer_is_strict_and_never_evaluates_unknown_objects(self):
        class Explosive:
            def __eq__(self, other):raise AssertionError(KEY)
            def __str__(self):raise AssertionError(KEY)
        class HostileDict(dict):
            def get(self,*args):raise AssertionError(KEY)
        value=self.safe(t.safe_diagnostic({'operation':Explosive(),'stage':Explosive(),
            'http_status':True,'bytes_observed':True,'request_attempted':Explosive(),
            'request_dispatched':Explosive(),'response_observed':Explosive(),'media_valid':Explosive(),KEY:KEY}))
        self.assertIsNone(value['operation']);self.assertEqual(value['stage'],'local')
        self.assertEqual(value['bytes_observed'],0);self.assertIsNone(value['http_status'])
        self.safe(t.safe_diagnostic(HostileDict(secret=KEY)))
        for count in (-1,t.MAX_JAR_BYTES+2,KEY):
            self.assertEqual(t.safe_diagnostic({'bytes_observed':count})['bytes_observed'],0)

    def test_inconsistent_response_claims_are_cleared_and_exception_returns_copy(self):
        value=self.safe(t.safe_diagnostic({'operation':'version','http_status':403,'bytes_observed':12,
            'request_attempted':True,'request_dispatched':False,'response_observed':True,'media_valid':True}))
        self.assertFalse(value['response_observed']);self.assertIsNone(value['http_status'])
        self.assertEqual(value['bytes_observed'],0);self.assertIsNone(value['media_valid'])
        error=t.ThetaTerminalUnavailable('Fixed generic message')
        mutable=error.diagnostic;mutable['stage']=KEY;mutable['extra']=KEY
        self.assertEqual(str(error),'Fixed generic message');self.safe(error.diagnostic)

    def test_failure_writer_resanitizes_mutated_private_diagnostic(self):
        error=t.ThetaTerminalUnavailable('Fixed generic message')
        error.__dict__['_diagnostic']={'operation':KEY,'stage':KEY,'http_status':KEY,
            'bytes_observed':KEY,'request_attempted':True,'request_dispatched':False,
            'response_observed':True,'private_url':KEY}
        value=self.prepare_failure(error)
        self.assertIsNone(value['http_status']);self.assertFalse(value['response_observed'])

    def test_unknown_exception_diagnostics_and_subclass_properties_are_not_trusted(self):
        class ExplosiveProperty(t.ThetaTerminalUnavailable):
            @property
            def diagnostic(self):raise RuntimeError(KEY)
        error=ExplosiveProperty('Fixed generic message')
        value=self.prepare_failure(error);self.assertIsNone(value['http_status'])
        # Existing output holds the failed attempt, and cannot be replayed.
        before={p.name:p.read_bytes() for p in (self.root/t.LIBRARY_PATH).iterdir()}
        factory=unittest.mock.Mock()
        with patch.object(t,'load_key',side_effect=AssertionError('No unlock during hold')):
            with self.assertRaises(FileExistsError):t.prepare(self.box,self.data,check=lambda:'stable',client_factory=factory)
        factory.assert_not_called()
        self.assertEqual(before,{p.name:p.read_bytes() for p in (self.root/t.LIBRARY_PATH).iterdir()})

    def test_mutable_exception_dict_subclass_cannot_execute_lookup_or_lose_receipt(self):
        class HostileDict(dict):
            def get(self,*args):raise RuntimeError(KEY)
        error=t.ThetaTerminalUnavailable('Fixed generic message')
        error.__dict__=HostileDict(error.__dict__)
        value=self.prepare_failure(error)
        self.assertIsNone(value['http_status']);self.assertFalse(value['response_observed'])

    def test_declared_length_mismatch_cannot_become_complete_version_or_valid_size(self):
        for raw,declared in ((VERSION.encode(),len(VERSION)+1),((VERSION+'0').encode(),len(VERSION))):
            with self.subTest(observed=len(raw),declared=declared):
                value=self.error(Connection(Response(raw,headers={'Content-Length':str(declared)})))
                self.assertEqual(value['http_status'],200);self.assertEqual(value['stage'],'size')
                self.assertIs(value['size_valid'],False);self.assertIsNone(value['version_valid'])
                self.assertEqual(value['bytes_observed'],len(raw))

    def test_unknown_native_exception_cannot_forge_http_observation(self):
        error=OSError(KEY)
        error.diagnostic={'operation':'version','stage':'http_status','http_status':403,
            'request_attempted':True,'request_dispatched':True,'response_observed':True}
        value=self.prepare_failure(error)
        self.assertIsNone(value['http_status']);self.assertFalse(value['response_observed'])

    def test_owner_policy_failure_after_metadata_does_not_reuse_http200(self):
        class MetadataClient:
            def __init__(self,key,*,online=False):pass
            def version(self):return {'version':VERSION,'received_at':'2026-11-05T15:00:00Z',
                'diagnostic':{'stage':'version_decode','operation':'version','http_status':200,
                              'request_attempted':True,'request_dispatched':True,'response_observed':True}}
            def application(self,version):raise AssertionError('No JAR request after revocation')
            def close(self):pass
        with self.assertRaises(t.ThetaTerminalUnavailable):
            t.prepare(self.box,self.data,check=unittest.mock.Mock(side_effect=['stable','stable','revoked']),client_factory=MetadataClient)
        value=self.safe(json.loads((self.root/t.LIBRARY_PATH/'preparation-failed.json').read_bytes())['diagnostic'])
        self.assertEqual(value['stage'],'owner_policy');self.assertIsNone(value['http_status'])
        self.assertFalse(value['response_observed']);self.assertFalse(value['request_attempted'])


if __name__=='__main__':unittest.main()