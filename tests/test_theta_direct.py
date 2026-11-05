"""Pure synthetic protocol/HTTP fixtures; never vendor imports/auth or data I/O."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from decimal import localcontext
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne import theta_direct as d
from ariadne import research_window as scope

ROOT = Path(__file__).resolve().parents[1]
KEY = 'FICTIONAL_DIRECT_API_KEY_0123456789_TEST_ONLY'
TOKEN = 'FICTIONAL_SESSION_TOKEN_0123456789_TEST_ONLY'
EMAIL = 'fictional-review@example.invalid'
NOW = '2026-11-05T18:00:00+00:00'
SELECTION = {'kind':'stock','symbol':'F','start_date':'2026-11-02','end_date':'2026-11-04'}
OPTION = {'kind':'option','contract_id':'F270115P00015000','start_date':'2026-11-02','end_date':'2026-11-04'}


def varint(value):
    if value < 0:value += 1 << 64
    output = bytearray()
    while value > 127:output.append((value & 127) | 128);value >>= 7
    output.append(value);return bytes(output)


def field(number, value, *, integer=False):
    if integer:return varint(number << 3) + varint(value)
    if type(value) is str:value = value.encode('utf-8')
    return varint((number << 3) | 2) + varint(len(value)) + value


def number(value):return field(2,value,integer=True)
def text(value):return field(1,value)
def price(value=1234,scale=8):return field(3,field(1,value,integer=True)+field(2,scale,integer=True))
def stamp(day='2026-11-04',hour=22,minute=15,*,nanos=0,precision=0):
    clock=datetime.fromisoformat(day).replace(hour=hour,minute=minute,tzinfo=timezone.utc)
    millis=(clock-datetime(1970,1,1,tzinfo=timezone.utc))//timedelta(milliseconds=1)
    return field(4,field(1,millis,integer=True)+field(2,0,integer=True)
        +field(3,nanos,integer=True)+field(4,precision,integer=True))


def values(day='2026-11-04',option=False):
    result={key:(price() if key in d._PRICES else number(100)) for key in d._VALUE_KEYS}
    result['created']=stamp(day);result['last_trade']=stamp(day,21,0)
    if option:result.update(symbol=text('F'),expiration=text('2027-01-15'),strike=price(15000,7),right=text('put'))
    return result


def table(rows=None,headers=None):
    rows=[values()] if rows is None else rows
    headers=sorted(rows[0]) if headers is None else headers
    return b''.join(field(1,header) for header in headers)+b''.join(
        field(2,b''.join(field(1,row[header]) for header in headers)) for row in rows)


def packet(rows=None,headers=None,*,raw_table=None,algorithm=0,payload=None):
    raw=table(rows,headers) if raw_table is None else raw_table
    return field(1,raw if payload is None else payload)+field(2,field(1,algorithm,integer=True))+field(3,len(raw),integer=True)


class FakeTransport:
    def __init__(self,messages=None,*,failure=None,auth_failure=None):
        self.messages=[packet()] if messages is None else messages
        self.failure=failure;self.auth_failure=auth_failure;self.auth_calls=self.rpc_calls=self.closed=0
    def authenticate(self,key,deadline,check):
        self.auth_calls+=1;check()
        if self.auth_failure:raise self.auth_failure
        return d._Session(TOKEN,EMAIL)
    def eod(self,session,selection,contract,deadline,check):
        self.rpc_calls+=1
        for message in self.messages:check();yield message
        if self.failure:raise self.failure
    def decompress(self,raw,maximum):raise AssertionError('Unexpected synthetic ZSTD')
    def close(self):self.closed+=1


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.window=scope.derive_window(NOW);self.operations=[]
        self.addCleanup(patch.stopall)
        patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'},clear=True).start()
        patch.object(d,'install_network_policy',return_value={'mode':'online'}).start()
        self.native=patch.object(d,'_load_native',side_effect=AssertionError('Native imports forbidden in these tests')).start()
        patch.object(d,'tls_status',return_value={'anchors':1,'verify_mode':'CERT_REQUIRED','check_hostname':True,'minimum_tls':'TLSv1.2'}).start()
        patch.object(d,'asset_status',return_value={'manifest_sha256':d.ASSET_MANIFEST_SHA256,
            'files_checked':145,'asset_bytes':15079814,'vendor_imported':False,'target':'CPython3.13-win_amd64'}).start()
    def run_capture(self,transport=None,selection=None,check=lambda:None,before=None):
        transport=FakeTransport() if transport is None else transport
        result=d.DirectEODClient(True,transport=transport).capture(KEY,selection or SELECTION,
            allowed_symbols=('F',),window=self.window,check=check,
            before_operation=self.operations.append if before is None else before)
        return result,transport
    def failure(self,transport,selection=None,**kwargs):
        with self.assertRaises(d.DirectError) as caught:self.run_capture(transport,selection,**kwargs)
        self.assertNotIn(KEY,str(caught.exception));self.assertNotIn(TOKEN,str(caught.exception))
        self.assertFalse(self.native.called)
        return caught.exception

    def test_stock_capture_exact_evidence_missing_sessions_and_no_financial_activity(self):
        result,transport=self.run_capture()
        self.assertEqual(self.operations,['authentication','eod_rpc'])
        self.assertEqual((transport.auth_calls,transport.rpc_calls,transport.closed),(1,1,1))
        self.assertEqual(result['operations'],{'authentication':1,'eod_rpc':1})
        self.assertEqual(result['missing_report_sessions'],['2026-11-02','2026-11-03'])
        self.assertFalse(result['transport']['provider_authenticated']);self.assertEqual(result['paper_fills'],0)
        self.assertEqual(result['rows'][0]['values']['close']['decimal'],'12.34')
        self.assertIsNone(result['rows'][0]['quote_event_at']);self.assertIsNone(result['rows'][0]['open_interest'])
        self.assertFalse(result['rows'][0]['standard_deliverable_verified'])
        payload=d.canonical(result)
        for secret in (KEY,TOKEN,EMAIL):self.assertNotIn(secret.encode(),payload)
        self.assertFalse(self.native.called)

    def test_single_explicit_option_identity_is_checked(self):
        result,_=self.run_capture(FakeTransport([packet([values(option=True)])]),OPTION)
        self.assertEqual(result['selection']['contract_id'],'F270115P00015000')
        malformed=values(option=True);malformed['strike']=price(16000,7)
        self.assertEqual(self.failure(FakeTransport([packet([malformed])]),OPTION).code,'invalid_response')

    def test_prices_are_context_independent_and_all_uint32_nanos_preserved(self):
        row=values();row['close']=price(123456789,1);row['created']=stamp(nanos=0xffffffff,precision=1)
        with localcontext() as context:
            context.prec=2;result,_=self.run_capture(FakeTransport([packet([row])]))
        created=result['rows'][0]['values']['created']
        self.assertEqual(result['rows'][0]['values']['close']['decimal'],'0.123456789')
        self.assertEqual(created['nanos'],0xffffffff);self.assertIsNone(created['epoch_ns'])
        self.assertEqual(created['precision'],1)

    def test_missing_price_remains_unknown_and_zero_volume_is_actual_zero(self):
        row=values();row['bid']=price(0,0);row['volume']=number(0);row['last_trade']=field(5,0,integer=True)
        result,_=self.run_capture(FakeTransport([packet([row])]))
        self.assertIsNone(result['rows'][0]['values']['bid']['decimal'])
        self.assertEqual(result['rows'][0]['values']['volume']['value'],0)
        self.assertEqual(result['rows'][0]['values']['last_trade']['kind'],'null')

    def test_offline_refuses_before_native_assets_or_credential_use(self):
        with patch.object(d,'asset_status',side_effect=AssertionError('Asset check must not run')):
            with self.assertRaises(d.DirectError) as caught:
                d.DirectEODClient(False).capture(KEY,SELECTION,allowed_symbols=('F',),window=self.window,
                    check=lambda:None,before_operation=self.operations.append)
        self.assertEqual(caught.exception.code,'offline');self.assertEqual(self.operations,[])
        self.assertFalse(self.native.called)

    def test_active_process_policy_is_required_even_with_online_flag(self):
        with patch.object(d,'install_network_policy',return_value={'mode':'offline'}):
            error=self.failure(FakeTransport())
        self.assertEqual(error.code,'offline');self.assertEqual(self.operations,[])

    def test_bad_scope_watchlist_current_day_and_window_rejected_before_auth(self):
        cases=[dict(SELECTION,symbol='QQQ'),dict(SELECTION,start_date='2025-11-04'),
            dict(SELECTION,end_date='2026-11-05'),dict(SELECTION,end_date='2026-11-01'),
            dict(SELECTION,extra=True),dict(OPTION,contract_id='F*')]
        for selection in cases:
            transport=FakeTransport()
            with self.subTest(selection=selection):self.failure(transport,selection)
            self.assertEqual((transport.auth_calls,transport.rpc_calls),(0,0))
        self.assertEqual(self.operations,[])

    def test_proxy_destination_debug_and_environment_keys_are_refused(self):
        for name in ('HTTPS_PROXY','grpc_proxy','GRPC_TRACE','SSLKEYLOGFILE','THETADATA_AUTH_URL','THETADATA_API_KEY','OTEL_EXPORTER_OTLP_ENDPOINT'):
            with self.subTest(name=name),patch.dict(os.environ,{name:'fictional'}):
                error=self.failure(FakeTransport());self.assertEqual(error.code,'invalid_input')
        self.assertEqual(self.operations,[])

    def test_auth_failure_has_no_retry_or_rpc_and_redacts_raw_error_text(self):
        transport=FakeTransport(auth_failure=d.DirectError('authentication_refused',http_status=403))
        error=self.failure(transport)
        self.assertEqual((transport.auth_calls,transport.rpc_calls,transport.closed),(1,0,1))
        self.assertEqual(error.partial_capture['responses'],[])
        self.assertEqual(d.diagnostic_from_error(error)['http_status'],403)

    def test_policy_revocation_after_auth_stops_before_rpc(self):
        transport=FakeTransport()
        def check():
            if transport.auth_calls:raise ValueError(KEY)
        error=self.failure(transport,check=check)
        self.assertEqual(error.code,'policy_stopped');self.assertEqual(transport.rpc_calls,0)
        self.assertEqual(self.operations,['authentication'])

    def test_before_operation_denial_stops_without_authentication(self):
        transport=FakeTransport()
        error=self.failure(transport,before=lambda _:(_ for _ in ()).throw(ValueError(KEY)))
        self.assertEqual(error.code,'policy_stopped');self.assertEqual(transport.auth_calls,0)
        self.assertEqual(error.partial_capture['operations'],{'authentication':0,'eod_rpc':0})

    def test_one_use_client_cannot_repeat_successful_or_failed_work(self):
        transport=FakeTransport();client=d.DirectEODClient(True,transport=transport)
        kwargs={'allowed_symbols':('F',),'window':self.window,'check':lambda:None,'before_operation':self.operations.append}
        client.capture(KEY,SELECTION,**kwargs)
        with self.assertRaises(d.DirectError):client.capture(KEY,SELECTION,**kwargs)
        self.assertEqual(transport.auth_calls,1)

    def test_invalid_packet_retains_only_prior_valid_secret_free_market_prefix(self):
        transport=FakeTransport([packet(),packet()+field(99,b'unknown')])
        error=self.failure(transport)
        self.assertEqual(len(error.partial_capture['responses']),1)
        self.assertEqual(len(error.partial_capture['rows']),1)
        self.assertEqual(error.partial_capture['status'],'failed')

    def test_rpc_permission_and_authentication_status_are_safe_not_details(self):
        for status in ('PERMISSION_DENIED','UNAUTHENTICATED','RESOURCE_EXHAUSTED'):
            error=self.failure(FakeTransport([],failure=d.DirectError('rpc_refused',rpc_status=status)))
            self.assertEqual(d.diagnostic_from_error(error)['rpc_status'],status)

    def test_raw_key_session_and_email_reflection_never_enter_partial_evidence(self):
        for secret in (KEY,TOKEN,EMAIL):
            row=values();row['symbol']=text(secret)
            with self.subTest(secret_kind=secret[:10]):
                error=self.failure(FakeTransport([packet([row])]))
            self.assertEqual(error.code,'secret_reflection');self.assertEqual(error.partial_capture['responses'],[])

    def test_compressed_secret_reflection_is_rejected_before_raw_message_retention(self):
        row=values();row['symbol']=text(KEY);decoded=table([row]);transport=FakeTransport([
            packet(raw_table=decoded,algorithm=1,payload=b'fictional-compressed')])
        transport.decompress=lambda raw,maximum:decoded
        error=self.failure(transport)
        self.assertEqual(error.code,'secret_reflection');self.assertEqual(error.partial_capture['responses'],[])

    def test_duplicate_fields_headers_oneofs_and_noncanonical_varints_rejected(self):
        bad=[packet()+field(1,b'x'),packet(raw_table=table()+field(1,'created')),
            packet()+b'\x80\x00',packet()+b'\x0b',packet(raw_table=table([dict(values(),close=price()+number(1))]))]
        for raw in bad:
            with self.subTest(shape=len(raw)):self.failure(FakeTransport([raw]))

    def test_header_schema_row_shape_and_out_of_order_or_duplicate_days_rejected(self):
        unknown=values();unknown['open_interest']=number(20)
        day2=values('2026-11-03')
        for raw in (packet([unknown]),packet([values(),values()]),packet([values(),day2]),
                field(1,field(1,'created')+field(2,b''))):
            self.failure(FakeTransport([raw]))
        first=packet([values('2026-11-03')]);last=packet([values()],headers=list(reversed(sorted(values()))))
        self.assertEqual(len(self.failure(FakeTransport([first,last])).partial_capture['responses']),1)

    def test_out_of_range_report_future_or_last_trade_after_report_rejected(self):
        for row in (values('2026-10-30'),values('2026-11-05'),dict(values(),last_trade=stamp('2026-11-05'))):
            self.failure(FakeTransport([packet([row])]))

    def test_payload_message_decoded_and_row_limits_fail_closed(self):
        self.assertEqual(self.failure(FakeTransport([b'x'*(d.MAX_MESSAGE_BYTES+1)])).code,'response_limit')
        with patch.object(d,'MAX_RPC_BYTES',1):self.assertEqual(self.failure(FakeTransport()).code,'response_limit')
        with patch.object(d,'MAX_DECODED_BYTES',1):self.assertEqual(self.failure(FakeTransport()).code,'response_limit')
        with patch.object(d,'MAX_ROWS',0):self.assertEqual(self.failure(FakeTransport()).code,'response_limit')
        with patch.object(d,'MAX_MESSAGES',0):self.assertEqual(self.failure(FakeTransport()).code,'response_limit')

    def test_empty_valid_response_is_missing_evidence_not_zero_return(self):
        result,_=self.run_capture(FakeTransport([]))
        self.assertTrue(result['empty_response']);self.assertEqual(result['rows'],[])
        self.assertEqual(result['missing_report_sessions'],['2026-11-02','2026-11-03','2026-11-04'])

    def test_error_export_refuses_subclasses_and_unhashable_enum_inputs(self):
        error=d.DirectError([],rpc_status={})
        self.assertEqual(error.code,'invalid_response');self.assertIsNone(error.rpc_status)
        class Untrusted(d.DirectError):pass
        self.assertEqual(d.diagnostic_from_error(Untrusted())['code'],'policy_stopped')
        self.assertEqual(d.diagnostic_from_error(ValueError(KEY))['code'],'policy_stopped')

    def test_complete_flow_and_failure_diagnostics_never_print_credentials(self):
        stdout,stderr=io.StringIO(),io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            self.run_capture();self.failure(FakeTransport(auth_failure=ValueError(KEY)))
        self.assertEqual(stdout.getvalue(),'');self.assertEqual(stderr.getvalue(),'')


    def test_auth_failure_partial_dict_does_not_claim_market_evidence(self):
        error=self.failure(FakeTransport(auth_failure=d.DirectError('authentication_json',http_status=200)))
        diagnostic=d.diagnostic_from_error(error)
        self.assertEqual(diagnostic['http_status'],200);self.assertFalse(diagnostic['partial_market_evidence_available'])
        self.assertEqual(error.partial_capture['rows'],[]);self.assertEqual(error.partial_capture['responses'],[])
        self.assertFalse(error.partial_capture['transport']['provider_authenticated'])


    def test_only_nonempty_validated_internal_market_prefix_is_reported(self):
        error=self.failure(FakeTransport([packet()],failure=d.DirectError('rpc_refused',rpc_status='UNAVAILABLE')))
        self.assertTrue(d.diagnostic_from_error(error)['partial_market_evidence_available'])
        empty=packet(raw_table=table([],headers=sorted(values())))
        error=self.failure(FakeTransport([empty],failure=d.DirectError('rpc_refused',rpc_status='UNAVAILABLE')))
        self.assertFalse(d.diagnostic_from_error(error)['partial_market_evidence_available'])


    def test_arbitrary_partial_dict_is_not_internal_market_evidence(self):
        fabricated={'source_id':d.SOURCE_ID,'responses':[{'rows':1}],'rows':[{'values':{}}]}
        error=d.DirectError('rpc_refused',partial_capture=fabricated)
        self.assertFalse(d.diagnostic_from_error(error)['partial_market_evidence_available'])


class WireAndAssetsTests(unittest.TestCase):
    def test_authentication_opaque_token_no_guessed_subscription_or_uuid_enum(self):
        raw=json.dumps({'sessionId':TOKEN,'user':{'email':EMAIL,'optionsSubscription':{'unknown':'provider-shape'}}}).encode()
        session=d._authentication(raw);self.assertEqual(session.token,TOKEN);self.assertNotIn(TOKEN,repr(session))
        for value in ({'sessionId':TOKEN,'user':{'email':EMAIL},'mddsHost':'attacker.invalid'},
                {'sessionId':TOKEN,'user':{'email':EMAIL},'host':'http://mdds-01.thetadata.us'},
                {'sessionId':None,'user':{'email':EMAIL}}):
            with self.assertRaises(d.DirectError):d._authentication(json.dumps(value).encode())
        with self.assertRaises(d.DirectError):d._authentication(b'{"sessionId":"a","sessionId":"b"}')

    def test_price_signed_and_unknown_scale_validation(self):
        self.assertEqual(d._value(price(-123,8))['decimal'],'-1.23')
        for raw in (price(1,20),field(2,1<<63,integer=True)+field(1,'bad')):
            with self.assertRaises(d.DirectError):d._value(raw)

    def test_exact_stock_year_probe_scope_and_explicit_future_put_preflight(self):
        # This checks selection/window behavior, independently of installation.
        # The dedicated asset tests below verify real manifests and file guards.
        synthetic_assets={'manifest_sha256':d.ASSET_MANIFEST_SHA256,'files_checked':0,
            'asset_bytes':0,'vendor_imported':False,'target':'CPython3.13-win_amd64'}
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'1'},clear=True), \
                patch.object(d,'_load_native',side_effect=AssertionError), \
                patch.object(d,'asset_status',return_value=synthetic_assets), \
                patch.object(d,'tls_status',return_value={'anchors':1,'verify_mode':'CERT_REQUIRED','check_hostname':True,'minimum_tls':'TLSv1.2'}):
            window=scope.derive_window(NOW);client=d.DirectEODClient()
            selection=dict(SELECTION,start_date='2025-11-05',end_date='2026-11-04')
            result=client.preflight(selection,allowed_symbols=('F',),window=window)
            self.assertEqual(result['assets']['manifest_sha256'],d.ASSET_MANIFEST_SHA256)
            self.assertFalse(result['vendor_imported']);self.assertFalse(result['authentication_performed'])
            client.preflight(dict(OPTION,start_date='2025-11-05'),allowed_symbols=('F',),window=window)

    def test_manifest_bounded_hashes_unknown_files_and_hardlinks_refused(self):
        parent=ROOT/'runtime/tmp';parent.mkdir(exist_ok=True,parents=True)
        with tempfile.TemporaryDirectory(prefix='direct-assets-',dir=parent) as folder:
            root=Path(folder);payload=b'fictional inert source';(root/'sample.py').write_bytes(payload)
            manifest={'profile':'ariadne-theta-direct-eod-assets-v1','target':'CPython3.13-win_amd64',
                'files':[{'path':'sample.py','bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}]}
            raw=d.canonical(manifest);(root/'assets.json').write_bytes(raw);digest=hashlib.sha256(raw).hexdigest()
            self.assertEqual(d._verify_assets(root,digest)['files_checked'],1)
            (root/'extra.txt').write_bytes(b'x')
            with self.assertRaises(d.DirectError):d._verify_assets(root,digest)
            (root/'extra.txt').unlink();(root/'sample.py').write_bytes(b'changed')
            with self.assertRaises(d.DirectError):d._verify_assets(root,digest)
            (root/'sample.py').write_bytes(payload)
            alias=root/'hardlink.py';os.link(root/'sample.py',alias)
            with self.assertRaises(d.DirectError):d._verify_assets(root,digest)

    def test_asset_inventory_does_not_follow_symbolic_directory(self):
        parent=ROOT/'runtime/tmp';parent.mkdir(exist_ok=True,parents=True)
        with tempfile.TemporaryDirectory(prefix='direct-links-',dir=parent) as folder:
            root=Path(folder);inside=root/'inside';inside.mkdir();(inside/'secret-fixture').write_bytes(b'inert')
            link=root/'link'
            try:link.symlink_to(inside,target_is_directory=True)
            except OSError:self.skipTest('Native symlink creation unavailable')
            with self.assertRaises(d.DirectError):d._inventory(root)


class AuthTransportTests(unittest.TestCase):
    def call(self,raw,*,status=200,length=None,debug_initial=1,media='application/json',encoding='identity'):
        class Context:
            minimum_version=None
            def load_verify_locations(self,**kwargs):pass
            def cert_store_stats(self):return {'x509':1}
        class Response:
            def __init__(self):self.status=status;self.raw=io.BytesIO(raw);self.closed=False
            def getheader(self,key,default=None):
                return {'Content-Type':media,'Content-Encoding':encoding,'Content-Length':str(len(raw)) if length is None else length}.get(key,default)
            def read1(self,limit):return self.raw.read(limit)
            def close(self):self.closed=True
        response=Response();calls=[]
        class Connection:
            sock=None
            def __init__(self,host,port,**kwargs):self.debuglevel=debug_initial;calls.append((host,port));self.closed=False
            def set_debuglevel(self,value):self.debuglevel=value
            def request(self,method,path,body,headers):
                self.method,self.path,self.body,self.headers=method,path,body,headers
                if self.debuglevel:print(body)
            def getresponse(self):return response
            def close(self):self.closed=True
        connections=[]
        def factory(*args,**kwargs):value=Connection(*args,**kwargs);connections.append(value);return value
        transport=object.__new__(d._NativeTransport);transport.roots=b'-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\n';transport.connection=None
        import time
        with patch.object(d.ssl,'SSLContext',return_value=Context()),patch.object(d.http.client,'HTTPSConnection',side_effect=factory):
            try:return transport.authenticate(KEY,time.monotonic()+10,lambda:None),connections,response,calls
            except d.DirectError as error:return error,connections,response,calls

    def test_auth_uses_fixed_tls_host_api_shape_no_debug_and_closes_both_objects(self):
        output=io.StringIO()
        with redirect_stdout(output):
            result,connections,response,calls=self.call(json.dumps({'sessionId':TOKEN,'user':{'email':EMAIL}}).encode())
        connection=connections[0]
        self.assertIs(type(result),d._Session);self.assertEqual(calls,[(d.AUTH_HOST,443)])
        self.assertEqual(connection.debuglevel,0);self.assertEqual(output.getvalue(),'')
        self.assertEqual(connection.path,d.AUTH_PATH);self.assertEqual(connection.method,'POST')
        self.assertEqual(json.loads(connection.body),{'apiKey':KEY,'authEnv':{'envType':'PROD'}})
        self.assertEqual(connection.headers['TD-TERMINAL-KEY'],d.PUBLIC_SDK_IDENTIFIER)
        self.assertTrue(connection.closed and response.closed)

    def test_auth_response_length_permission_and_oversize_fail_without_body_diagnostics(self):
        for status,raw,length in ((403,KEY.encode(),None),(200,b'{}','200'),(200,b'x'*(d.MAX_AUTH_BYTES+1),None)):
            error,connections,response,calls=self.call(raw,status=status,length=length)
            self.assertIs(type(error),d.DirectError);self.assertNotIn(KEY,str(error))
            self.assertTrue(connections[0].closed and response.closed);self.assertEqual(len(calls),1)


class AdditionalBoundariesTests(unittest.TestCase):
    def test_excessive_repeated_headers_are_refused_before_unbounded_allocation(self):
        raw=b''.join(field(1,'x'+str(index)) for index in range(25))
        with self.assertRaises(d.DirectError) as error:
            d._decode(packet(raw_table=raw),d.MAX_DECODED_BYTES,lambda *_:b'',[])
        self.assertEqual(error.exception.code,'response_limit')

    def test_inventory_refuses_a_reparse_entry_before_descent(self):
        import stat
        import types
        parent=ROOT/'runtime/tmp';parent.mkdir(exist_ok=True,parents=True)
        with tempfile.TemporaryDirectory(prefix='direct-no-follow-',dir=parent) as folder:
            root=Path(folder);link=root/'mocked-junction';link.mkdir()
            original=Path.lstat;walk=os.scandir;visited=[]
            def lstat(path,*args,**kwargs):
                if path==link:return types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_file_attributes=0x400,st_nlink=1)
                return original(path,*args,**kwargs)
            def scandir(path):visited.append(Path(path));return walk(path)
            with patch.object(Path,'lstat',lstat),patch.object(d.os,'scandir',scandir):
                with self.assertRaises(d.DirectError):d._inventory(root)
            self.assertNotIn(link,visited)

    def test_rpc_uses_exact_one_fixed_secure_method_deadline_and_closes_call_and_channel(self):
        import time
        from types import SimpleNamespace
        calls=[]
        class Message:
            def __init__(self,**kwargs):self.fields=kwargs
            def SerializeToString(self):return b'fictional-request-not-saved'
        class Stream:
            cancelled=False
            def __iter__(self):yield packet()
            def cancel(self):self.cancelled=True
        stream=Stream()
        class Channel:
            closed=False
            def unary_stream(self,method,**kwargs):
                calls.append(('method',method,kwargs))
                def call(request,**options):calls.append(('request',request,options));return stream
                return call
            def close(self):self.closed=True
        channel=Channel()
        def secure(target,credentials,**kwargs):calls.append(('channel',target,credentials,kwargs));return channel
        transport=object.__new__(d._NativeTransport)
        transport.grpc=SimpleNamespace(secure_channel=secure,ssl_channel_credentials=lambda roots:('verified-roots',roots))
        transport.common=SimpleNamespace(AuthToken=Message,ContractSpec=Message)
        transport.requests=SimpleNamespace(QueryInfo=Message,StockHistoryEodRequestQuery=Message,StockHistoryEodRequest=Message)
        transport.roots=b'fictional-pinned-roots';transport.channel=transport.call=transport.connection=None
        result=list(transport.eod(d._Session(TOKEN,EMAIL),SELECTION,None,time.monotonic()+30,lambda:None))
        self.assertEqual(result,[packet()]);self.assertEqual(len([x for x in calls if x[0]=='request']),1)
        channel_call=calls[0];self.assertEqual(channel_call[1],d.DATA_HOST+':443')
        options=dict(channel_call[3]['options'])
        self.assertEqual(options['grpc.enable_http_proxy'],0);self.assertEqual(options['grpc.enable_retries'],0)
        self.assertEqual(options['grpc.service_config_disable_resolution'],1)
        self.assertEqual(options['grpc.max_receive_message_length'],d.MAX_MESSAGE_BYTES)
        self.assertEqual(calls[1][1],'/BetaEndpoints.BetaThetaTerminal/GetStockHistoryEod')
        self.assertTrue(0<calls[2][2]['timeout']<=30);self.assertFalse(calls[2][2]['wait_for_ready'])
        transport.close();self.assertTrue(stream.cancelled and channel.closed)



# One authentic public trust anchor from the pinned optional gRPC bundle.
# Public test data, not an account key. Embedding it keeps source-only tests
# independent of optional native assets while exercising real stdlib OpenSSL.
_PUBLIC_TEST_CA = b'-----BEGIN CERTIFICATE-----\r\nMIIDdTCCAl2gAwIBAgILBAAAAAABFUtaw5QwDQYJKoZIhvcNAQEFBQAwVzELMAkG\r\nA1UEBhMCQkUxGTAXBgNVBAoTEEdsb2JhbFNpZ24gbnYtc2ExEDAOBgNVBAsTB1Jv\r\nb3QgQ0ExGzAZBgNVBAMTEkdsb2JhbFNpZ24gUm9vdCBDQTAeFw05ODA5MDExMjAw\r\nMDBaFw0yODAxMjgxMjAwMDBaMFcxCzAJBgNVBAYTAkJFMRkwFwYDVQQKExBHbG9i\r\nYWxTaWduIG52LXNhMRAwDgYDVQQLEwdSb290IENBMRswGQYDVQQDExJHbG9iYWxT\r\naWduIFJvb3QgQ0EwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDaDuaZ\r\njc6j40+Kfvvxi4Mla+pIH/EqsLmVEQS98GPR4mdmzxzdzxtIK+6NiY6arymAZavp\r\nxy0Sy6scTHAHoT0KMM0VjU/43dSMUBUc71DuxC73/OlS8pF94G3VNTCOXkNz8kHp\r\n1Wrjsok6Vjk4bwY8iGlbKk3Fp1S4bInMm/k8yuX9ifUSPJJ4ltbcdG6TRGHRjcdG\r\nsnUOhugZitVtbNV4FpWi6cgKOOvyJBNPc1STE4U6G7weNLWLBYy5d4ux2x8gkasJ\r\nU26Qzns3dLlwR5EiUWMWea6xrkEmCMgZK9FGqkjWZCrXgzT/LCrBbBlDSgeF59N8\r\n9iFo7+ryUp9/k5DPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNVHRMBAf8E\r\nBTADAQH/MB0GA1UdDgQWBBRge2YaRQ2XyolQL30EzTSo//z9SzANBgkqhkiG9w0B\r\nAQUFAAOCAQEA1nPnfE920I2/7LqivjTFKDK1fPxsnCwrvQmeU79rXqoRSLblCKOz\r\nyj1hTdNGCbM+w6DjY1Ub8rrvrTnhQ7k4o+YviiY776BQVvnGCv04zcQLcFGUl5gE\r\n38NflNUVyRRBnMRddWQVDf9VMOyGj/8N7yy5Y0b2qvzfvGn9LhJIZJrglfCm7ymP\r\nAbEVtQwdpf5pLGkkeB6zpxxxYu7KyJesF12KwvhHhm4qxFYxldBniYUr+WymXUad\r\nDKqC5JlR3XC321Y9YeRq4VzW9v493kHMB65jUr9TU/Qr6cf9tveCX4XSQRjbgbME\r\nHMUfpIBvFSDJ3gyICh3WZlXi/EjJKSZp4A==\r\n-----END CERTIFICATE-----\n'


class CertificateBundleTests(unittest.TestCase):
    def test_real_public_anchor_with_utf8_comments_loads_without_weakening_tls(self):
        raw='# Árvíztűrő certificate comments; not PEM payload\n'.encode('utf-8')+_PUBLIC_TEST_CA
        original=bytes(raw)
        pem,count=d._certificate_pem(raw)
        self.assertEqual(count,1);self.assertEqual(pem.encode('ascii'),_PUBLIC_TEST_CA.replace(b'\r\n',b'\n'))
        context=d._tls_context(raw)
        self.assertEqual(context.cert_store_stats()['x509'],1)
        self.assertEqual(context.verify_mode,d.ssl.CERT_REQUIRED);self.assertTrue(context.check_hostname)
        self.assertGreaterEqual(context.minimum_version,d.ssl.TLSVersion.TLSv1_2)
        self.assertEqual(raw,original)

    def test_malformed_nested_orphan_truncated_and_non_ascii_payload_rejected(self):
        invalid=[b'no anchors',b'bad UTF8 \xff\n'+_PUBLIC_TEST_CA,
            _PUBLIC_TEST_CA.replace(b'-----END CERTIFICATE-----',b''),
            b'-----END CERTIFICATE-----\n'+_PUBLIC_TEST_CA,
            b'-----BEGIN CERTIFICATE-----\n'+_PUBLIC_TEST_CA,
            _PUBLIC_TEST_CA.replace(b'\n',b'\n\xff',1),
            _PUBLIC_TEST_CA.replace(b'\n','\né'.encode('utf-8'),1),
            b'-----BEGIN CERTIFICATE-----\n$invalid$\n-----END CERTIFICATE-----\n',
            b'-----BEGIN PRIVATE KEY-----\nAA==\n-----END PRIVATE KEY-----\n'+_PUBLIC_TEST_CA,
            b'# -----BEGIN CERTIFICATE----- trailing\n'+_PUBLIC_TEST_CA,
            b'-----BEGIN CERTIFICAT\n'+_PUBLIC_TEST_CA,
            _PUBLIC_TEST_CA+b'-----END CERTIFICAT\n',
            b' '*(512*1024+1)]
        for raw in invalid:
            with self.subTest(shape=len(raw)),self.assertRaises(d.DirectError) as caught:d._certificate_pem(raw)
            self.assertEqual(caught.exception.code,'authentication_tls_setup')

    def test_invalid_der_and_duplicate_anchors_are_not_silently_dropped(self):
        for raw in (b'-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\n',
                _PUBLIC_TEST_CA+_PUBLIC_TEST_CA):
            with self.assertRaises(d.DirectError) as caught:d._tls_context(raw)
            self.assertEqual(caught.exception.code,'authentication_tls_setup')

    def test_tls_status_checks_exact_derived_public_bundle_pin_before_ssl(self):
        fixture='# Unicode comments: é\n'.encode('utf-8')+_PUBLIC_TEST_CA
        with patch.object(d,'_read_asset',return_value=fixture), \
                patch.object(d,'ROOTS_SHA256',hashlib.sha256(fixture).hexdigest()):
            self.assertEqual(d.tls_status(),{'anchors':1,'verify_mode':'CERT_REQUIRED',
                'check_hostname':True,'minimum_tls':'TLSv1.2'})
        with patch.object(d,'_read_asset',return_value=fixture), \
                patch.object(d,'ROOTS_SHA256','0'*64), \
                patch.object(d,'_tls_context',side_effect=AssertionError('Changed bundle must not be parsed')) as context:
            with self.assertRaises(d.DirectError) as caught:d.tls_status()
        self.assertEqual(caught.exception.code,'authentication_tls_setup');self.assertFalse(context.called)

    def test_pre_request_ssl_failures_get_fixed_diagnostic_and_never_construct_http(self):
        import time
        transport=object.__new__(d._NativeTransport);transport.roots=_PUBLIC_TEST_CA;transport.connection=None
        with patch.object(d.ssl,'SSLContext',side_effect=ValueError(KEY)), \
                patch.object(d.http.client,'HTTPSConnection',side_effect=AssertionError('Must not connect')) as http:
            with self.assertRaises(d.DirectError) as caught:
                transport.authenticate(KEY,time.monotonic()+10,lambda:None)
        self.assertEqual(caught.exception.code,'authentication_tls_setup')
        self.assertNotIn(KEY,str(caught.exception));self.assertFalse(http.called)



class AuthenticationDiagnosticTests(unittest.TestCase):
    def response(self,value=None,**kwargs):
        raw=json.dumps(value if value is not None else {'sessionId':TOKEN,'user':{'email':EMAIL}}).encode()
        return AuthTransportTests().call(raw,**kwargs)

    def test_each_fixed_validation_stage_retains_observed_200_without_raw_provider_data(self):
        fixtures=[
            ('authentication_media',None,{'media':'text/plain; private='+KEY}),
            ('authentication_encoding',None,{'encoding':'private-'+KEY}),
            ('authentication_length',None,{'length':'private-'+KEY}),
            ('authentication_json',[KEY],{}),
            ('authentication_session',{'sessionId':None,'private':KEY,'user':{'email':EMAIL}},{}),
            ('authentication_user',{'sessionId':TOKEN,'user':KEY},{}),
            ('authentication_email',{'sessionId':TOKEN,'user':{'email':'bad\n'+KEY}},{}),
            ('authentication_assignment',{'sessionId':TOKEN,'user':{'email':EMAIL},'mddsHost':'https://'+KEY},{}),
        ]
        for expected,value,options in fixtures:
            with self.subTest(stage=expected):
                error,connections,response,calls=self.response(value,**options)
                self.assertIs(type(error),d.DirectError)
                diagnostic=d.diagnostic_from_error(error)
                self.assertEqual(diagnostic['code'],expected);self.assertEqual(diagnostic['http_status'],200)
                self.assertFalse(diagnostic['partial_market_evidence_available'])
                for secret in (KEY,TOKEN,EMAIL):self.assertNotIn(secret,str(error));self.assertNotIn(secret,str(diagnostic))
                self.assertEqual(len(calls),1);self.assertTrue(connections[0].closed and response.closed)

    def test_json_decoder_duplicates_utf8_and_nonfinite_have_one_fixed_stage(self):
        for raw in (b'{',b'\xff',b'{"sessionId":"a","sessionId":"b"}',b'{"x":NaN}'):
            error,_,_,_=AuthTransportTests().call(raw)
            self.assertEqual(error.code,'authentication_json');self.assertEqual(error.http_status,200)

    def test_header_and_body_lengths_have_fixed_stage_and_http_200(self):
        for raw,length in ((b'{}','5'),(b'{}','-1'),(b'{}','9'*5000),
                (b'x'*(d.MAX_AUTH_BYTES+1),str(d.MAX_AUTH_BYTES+1))):
            error,_,_,_=AuthTransportTests().call(raw,length=length)
            self.assertEqual(error.code,'authentication_length');self.assertEqual(error.http_status,200)

    def test_incidental_account_names_are_ignored_not_routing_or_persisted_fields(self):
        value={'sessionId':TOKEN,'reportingEnabled':True,'user':{'email':EMAIL,
            'supportLevel':KEY,'reportingEnabled':False,'passport':KEY,'address':KEY,'imported':True}}
        session=d._authentication(d.canonical(value))
        self.assertIs(type(session),d._Session)
        self.assertEqual(set(vars(session)),{'token','email'})
        self.assertEqual((d.AUTH_HOST,d.DATA_HOST),('nexus-api.thetadata.us','mdds-01.thetadata.us'))
        self.assertNotIn(KEY,repr(session))

    def test_known_exact_host_port_echoes_and_normalized_keys_remain_supported(self):
        for key,value in (('host',d.DATA_HOST),('server',d.DATA_HOST),('mddsHost',d.DATA_HOST),
                ('mddsServer',d.DATA_HOST),('MDDS_HOST',d.DATA_HOST),('mdds-host',d.DATA_HOST),
                ('port',443),('mddsPort','443'),('MDDS_PORT',443)):
            with self.subTest(key=key):
                session=d._authentication(d.canonical({'sessionId':TOKEN,'user':{'email':EMAIL},key:value}))
                self.assertEqual(session.token,TOKEN)

    def test_explicit_mismatches_and_destination_aliases_still_refuse_without_echo(self):
        for key,value in (('host','attacker.invalid'),('server',KEY),('mddsHost','http://'+d.DATA_HOST),
                ('port',80),('mddsPort','443/'+KEY),('port',443.0),('serverAddress',KEY),
                ('destinationURL','https://'+KEY),('endpoint_url',KEY),('mdds-url',KEY),
                ('authURL','https://'+KEY),('destinationHost',KEY),('serverPort',443),
                ('server_port','443'),('dataPort',443),('hostName',d.DATA_HOST),
                ('hostname',d.DATA_HOST),('endpoint',KEY),('url','https://'+KEY)):
            with self.subTest(key=key),self.assertRaises(d.DirectError) as caught:
                d._authentication(d.canonical({'sessionId':TOKEN,'user':{'email':EMAIL},key:value}))
            self.assertEqual(caught.exception.code,'authentication_assignment');self.assertNotIn(KEY,str(caught.exception))

    def test_hostile_exception_state_dictionary_and_non_string_keys_are_never_walked(self):
        class HostileDict(dict):
            def get(self,*args,**kwargs):raise AssertionError(KEY)
        error=d.DirectError('authentication_json',http_status=200)
        error.__dict__=HostileDict(error.__dict__)
        self.assertEqual(d.diagnostic_from_error(error)['code'],'policy_stopped')
        self.assertFalse(d.diagnostic_from_error(error)['partial_market_evidence_available'])
        error=d.DirectError('authentication_json',http_status=200)
        error.__dict__[object()]=KEY
        self.assertEqual(d.diagnostic_from_error(error)['code'],'policy_stopped')

    def test_unrelated_or_subclass_exceptions_cannot_inject_diagnostic_attributes(self):
        class Untrusted(d.DirectError):
            def __getattribute__(self,name):raise AssertionError(KEY)
        error=BaseException.__new__(Untrusted)
        diagnostic=d.diagnostic_from_error(error)
        self.assertEqual(diagnostic['code'],'policy_stopped')
        self.assertFalse(diagnostic['partial_market_evidence_available']);self.assertNotIn(KEY,str(diagnostic))



if __name__ == '__main__':unittest.main()
