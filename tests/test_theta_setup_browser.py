"""Real loopback HTTP with fictional keys and isolated stores; no owner setup."""
from contextlib import redirect_stdout,redirect_stderr
import http.client
import io
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode,urlsplit

from ariadne import theta_setup_browser as b
from ariadne.tools import ToolRegistry
from ariadne.locking import InstanceLock

ROOT=Path(__file__).resolve().parents[1]
KEY='FICTIONAL_THETA_API_KEY_0123456789_ONLY_TEST'


class BrowserSetupTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/'runtime/tmp';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='theta-browser-',dir=parent)
        self.addCleanup(self.temp.cleanup);self.addCleanup(patch.stopall);self.addCleanup(self.stop)
        self.root=Path(self.temp.name);self.root.mkdir(exist_ok=True)
        self.data=object.__new__(ToolRegistry);self.data.root=self.root
        self.saved=[];self.urls=[];self.result=None;self.error=None;self.state=None;self.server=None;self.thread=None
        self.ready=threading.Event();self.port=None
        patch.dict(os.environ,{},clear=False).start();os.environ.pop('THETADATA_API_KEY',None)
        patch.object(b.terminal,'save_key',side_effect=lambda data,key:self.saved.append(key)).start()
        original_state=b._State;original_server=b._Server
        def state(*args,**kwargs):self.state=original_state(*args,**kwargs);return self.state
        def server(*args,**kwargs):self.server=original_server(*args,**kwargs);return self.server
        patch.object(b,'_State',side_effect=state).start();patch.object(b,'_Server',side_effect=server).start()

    def stop(self):
        if self.thread and self.thread.is_alive():
            if self.state and self.server:self.state.expire(self.server)
            self.thread.join(3)
            if self.thread.is_alive():raise AssertionError('Fixture server did not stop')

    def start(self,lifetime=5):
        def ready(url):
            self.urls.append(url);parts=urlsplit(url)
            self.assertEqual(parts.hostname,'127.0.0.1');self.assertEqual(parts.path,'/')
            self.assertFalse(parts.query);self.port=parts.port;self.ready.set()
        def run():
            try:self.result=b.serve(self.data,lifetime=lifetime,ready=ready)
            except Exception as error:self.error=error;self.ready.set()
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
        self.assertTrue(self.ready.wait(3));self.assertIsNone(self.error)

    @property
    def host(self):return '127.0.0.1:'+str(self.port)

    @property
    def origin(self):return 'http://'+self.host

    def request(self,method='GET',path='/',body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2)
        try:
            connection.request(method,path,body=body,headers=headers or {})
            response=connection.getresponse();raw=response.read()
            return response.status,dict(response.getheaders()),raw
        except (ConnectionAbortedError,ConnectionResetError,http.client.RemoteDisconnected):
            # Closing an unread rejected POST can produce a Windows TCP reset.
            # Treat the closed connection as refusal, never as a successful save.
            return 0,{},b''
        finally:connection.close()

    def form(self):
        status,headers,raw=self.request(headers={'Sec-Fetch-Site':'none','Sec-Fetch-Mode':'navigate'})
        self.assertEqual(status,200)
        csrf=re.search(rb'name="csrf" value="([A-Za-z0-9_-]+)"',raw).group(1).decode()
        return csrf,headers['Set-Cookie'].split(';')[0],headers,raw

    def post(self,csrf,cookie,*,key=KEY,headers=None,body=None,path='/save'):
        body=urlencode({'csrf':csrf,'key':key}) if body is None else body
        values={'Origin':self.origin,'Cookie':cookie,'Content-Type':'application/x-www-form-urlencoded',
            'Sec-Fetch-Site':'same-origin','Sec-Fetch-Mode':'navigate'}
        values.update(headers or {})
        return self.request('POST',path,body,values)

    def complete(self):
        csrf,cookie,_,_=self.form();status,headers,body=self.post(csrf,cookie)
        self.assertEqual(status,303);self.assertEqual(headers['Location'],'/done');self.assertNotIn(KEY.encode(),body)
        self.assertEqual(self.request(path='/done')[0],200)
        self.thread.join(3);self.assertFalse(self.thread.is_alive());self.assertIsNone(self.error)

    def raw_request(self,raw):
        with socket.create_connection(('127.0.0.1',self.port),timeout=2) as connection:
            connection.sendall(raw);connection.shutdown(socket.SHUT_WR);output=[]
            while True:
                try:part=connection.recv(4096)
                except (ConnectionResetError,socket.timeout):break
                if not part:break
                output.append(part)
            return b''.join(output)

    def test_actual_http_single_use_password_form_and_safe_success(self):
        self.start();csrf,cookie,headers,raw=self.form()
        self.assertIn(b'type="password"',raw);self.assertIn(b'autocomplete="off"',raw)
        self.assertNotIn(b'<script',raw);self.assertNotIn(KEY.encode(),raw)
        self.assertIn("default-src 'none'",headers['Content-Security-Policy'])
        self.assertIn("form-action 'self'",headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertIn('no-store',headers['Cache-Control']);self.assertEqual(headers['Referrer-Policy'],'same-origin')
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.assertEqual(self.post(csrf,cookie)[0],303)
        # Refresh/repost cannot call key storage again, even before PRG GET.
        # Windows may reset an unread rejected POST before delivering its 410.
        self.assertIn(self.post(csrf,cookie)[0],(0,410));self.assertEqual(self.saved,[KEY])
        self.assertEqual(self.request(path='/done')[0],200);self.thread.join(3)
        self.assertIsNone(self.error);self.assertEqual(self.result['status'],'saved')
        self.assertTrue(self.result['configured']);self.assertEqual(self.result['outbound_network_requests'],0)
        self.assertFalse(self.result['application_launched']);self.assertEqual(self.result['paper_fills'],0)
        self.assertNotIn(KEY,str(self.result));self.assertNotIn(KEY,str(self.urls))

    def test_cross_origin_wrong_host_and_fetch_metadata_are_refused(self):
        self.start();csrf,cookie,_,_=self.form()
        for headers in ({'Origin':'https://attacker.invalid'},{'Origin':'null'},
                        {'Host':'localhost:'+str(self.port)},{'Sec-Fetch-Site':'cross-site'},
                        {'Sec-Fetch-Dest':'iframe'}):
            with self.subTest(headers=headers):self.assertIn(self.post(csrf,cookie,headers=headers)[0],(0,400))
        self.assertEqual(self.saved,[]);self.complete()

    def test_null_origin_is_refused_with_valid_cookie_csrf_and_native_form_metadata(self):
        self.start();csrf,cookie,headers,_=self.form()
        self.assertEqual(headers['Referrer-Policy'],'same-origin')
        status,_,raw=self.post(csrf,cookie,headers={'Origin':'null','Sec-Fetch-Dest':'document'})
        self.assertIn(status,(0,400));self.assertNotIn(KEY.encode(),raw)
        self.assertEqual(self.saved,[]);self.assertFalse(self.state.used)
        self.assertEqual(self.post(csrf,cookie,headers={'Sec-Fetch-Dest':'document'})[0],303)
        self.assertEqual(self.request(path='/done')[0],200);self.thread.join(3)
        self.assertIsNone(self.error);self.assertEqual(self.saved,[KEY])

    def test_wrong_or_missing_csrf_cookie_and_duplicates_do_not_save(self):
        self.start();csrf,cookie,_,_=self.form()
        for token,cookies,body in (('wrong',cookie,None),(csrf,'wrong=1',None),
                (csrf,cookie,'csrf='+csrf+'&csrf='+csrf),(csrf,cookie,'csrf='+csrf+'&key=x&extra=y'),
                (csrf,cookie,'csrf=%GG&key='+KEY),(csrf,cookie,'csrf='+csrf+'&key=%FF')):
            with self.subTest(body=body):self.assertIn(self.post(token,cookies,body=body)[0],(0,400))
        self.assertEqual(self.saved,[]);self.complete()

    def test_key_with_urlencoded_punctuation_is_preserved(self):
        self.start();csrf,cookie,_,_=self.form();key=KEY+'+%&=<>'
        self.assertEqual(self.post(csrf,cookie,key=key)[0],303)
        self.assertEqual(self.request(path='/done')[0],200);self.thread.join(3)
        self.assertEqual(self.saved,[key]);self.assertIsNone(self.error)

    def test_bad_paths_methods_and_malformed_versions_never_echo_or_log(self):
        output=io.StringIO();errors=io.StringIO()
        with redirect_stdout(output),redirect_stderr(errors):
            self.start()
            for method,path in (('GET','/?key='+KEY),('GET','/bad/'+KEY),('DELETE','/'+KEY)):
                _,_,raw=self.request(method,path)
                self.assertNotIn(KEY.encode(),raw)
            raw=self.raw_request(('GET /'+KEY+' BAD_'+KEY+'\r\nHost: '+self.host+'\r\n\r\n').encode())
            self.assertNotIn(KEY.encode(),raw)
            self.complete()
        self.assertNotIn(KEY,output.getvalue());self.assertNotIn(KEY,errors.getvalue())

    def test_header_size_count_transfer_encoding_and_duplicate_origin_refused(self):
        self.start();csrf,cookie,_,_=self.form()
        body=urlencode({'csrf':csrf,'key':KEY}).encode()
        base='POST /save HTTP/1.1\r\nHost: '+self.host+'\r\nOrigin: '+self.origin+'\r\nCookie: '+cookie+'\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: '+str(len(body))+'\r\n'
        extras=('Origin: '+self.origin+'\r\n','Host: '+self.host+'\r\n',
                'Content-Length: '+str(len(body))+'\r\n','Transfer-Encoding: chunked\r\n',
                'X-Long: '+'x'*3000+'\r\n',''.join('X-'+str(i)+': x\r\n' for i in range(40)))
        for extra in extras:
            raw=self.raw_request((base+extra+'\r\n').encode()+body)
            self.assertNotIn(KEY.encode(),raw)
        self.assertEqual(self.saved,[]);self.complete()

    def test_invalid_authorized_key_consumes_session_without_storage_or_retry(self):
        self.start();csrf,cookie,_,_=self.form()
        self.assertEqual(self.post(csrf,cookie,key='too short')[0],400)
        self.thread.join(3);self.assertFalse(self.thread.is_alive())
        self.assertIsInstance(self.error,b.ThetaSetupError);self.assertEqual(self.saved,[])
        self.assertNotIn('too short',str(self.error))

    def test_storage_failure_is_single_use_and_exception_text_is_redacted(self):
        with patch.object(b.terminal,'save_key',side_effect=OSError(KEY)):
            self.start();csrf,cookie,_,_=self.form();status,_,raw=self.post(csrf,cookie)
            self.assertEqual(status,400);self.assertNotIn(KEY.encode(),raw)
            self.thread.join(3)
        self.assertIsInstance(self.error,b.ThetaSetupError);self.assertNotIn(KEY,str(self.error))

    def test_store_or_environment_present_before_bind_never_overwritten(self):
        callback=[]
        with patch.dict(os.environ,{'THETADATA_API_KEY':''}):
            with self.assertRaises(b.ThetaSetupError):b.serve(self.data,ready=callback.append)
        path=self.data._path(b.terminal.PRIVATE_PATH);path.parent.mkdir();path.write_bytes(b'existing private fixture')
        with self.assertRaises(b.ThetaSetupError):b.serve(self.data,ready=callback.append)
        self.assertEqual(callback,[]);self.assertEqual(path.read_bytes(),b'existing private fixture')

    def test_credential_appearing_after_form_load_is_not_overwritten(self):
        self.start();csrf,cookie,_,_=self.form()
        path=self.data._path(b.terminal.PRIVATE_PATH);path.parent.mkdir();path.write_bytes(b'existing private fixture')
        self.assertEqual(self.post(csrf,cookie)[0],400);self.thread.join(3)
        self.assertEqual(self.saved,[]);self.assertEqual(path.read_bytes(),b'existing private fixture')

    def test_existing_storage_lock_refuses_another_setup_session(self):
        lock=InstanceLock(self.data.root/'theta-setup.lock');lock.acquire()
        try:
            with self.assertRaises(ValueError):b.serve(self.data,ready=lambda _:None)
        finally:lock.release()
        self.assertEqual(self.saved,[])

    def test_hard_lifetime_closes_slow_header_sender_without_save(self):
        started=time.monotonic();self.start(lifetime=1)
        connection=socket.create_connection(('127.0.0.1',self.port),timeout=2)
        try:
            connection.sendall(b'GET / HTTP/1.1\r\nX-Slow: ')
            while time.monotonic()-started<1.5:
                try:connection.sendall(b'x')
                except OSError:break
                time.sleep(.03)
        finally:connection.close()
        self.thread.join(2);self.assertFalse(self.thread.is_alive())
        self.assertLess(time.monotonic()-started,2.5);self.assertEqual(self.saved,[])
        self.assertEqual(self.result['status'],'expired')

    def test_hard_lifetime_closes_slow_post_body_without_save(self):
        self.start(lifetime=1);csrf,cookie,_,_=self.form();started=time.monotonic()
        connection=socket.create_connection(('127.0.0.1',self.port),timeout=2)
        headers=('POST /save HTTP/1.1\r\nHost: '+self.host+'\r\nOrigin: '+self.origin+'\r\nCookie: '+cookie+'\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 1000\r\n\r\n')
        try:
            connection.sendall(headers.encode())
            while time.monotonic()-started<1.5:
                try:connection.sendall(b'x')
                except OSError:break
                time.sleep(.03)
        finally:connection.close()
        self.thread.join(2);self.assertFalse(self.thread.is_alive())
        self.assertLess(time.monotonic()-started,2.5);self.assertEqual(self.saved,[])
        self.assertEqual(self.result['status'],'expired')

    def test_lifetime_and_callback_are_explicitly_bounded(self):
        for lifetime in (True,0,301,1.5):
            with self.assertRaises(ValueError):b.serve(self.data,lifetime=lifetime,ready=lambda _:None)
        with self.assertRaises(ValueError):b.serve(self.data,ready=None)
        self.assertEqual(self.urls,[])

    def test_observed_cross_site_top_level_initial_get_then_strict_post_flow(self):
        self.start()
        headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        status,response,body=self.request(headers=headers)
        self.assertEqual(status,200);self.assertIn(b'type="password"',body)
        csrf=re.search(rb'name="csrf" value="([A-Za-z0-9_-]+)"',body).group(1).decode()
        cookie=response['Set-Cookie'].split(';')[0]
        self.assertIn(self.post(csrf,cookie,headers=headers)[0],(0,400))
        self.assertEqual(self.saved,[])
        self.assertEqual(self.post(csrf,cookie)[0],303)
        self.assertEqual(self.request(path='/done',headers=headers)[0],403)
        self.assertEqual(self.request(path='/done')[0],200)
        self.thread.join(3);self.assertIsNone(self.error);self.assertEqual(self.saved,[KEY])

    def test_cross_site_get_allowance_rejects_embeds_cors_origin_and_other_paths(self):
        self.start()
        base={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        cases=[('/',{**base,'Sec-Fetch-Dest':'iframe'}),('/',{**base,'Sec-Fetch-Mode':'cors'}),
            ('/',{'Sec-Fetch-Site':'cross-site','Sec-Fetch-Dest':'document'}),
            ('/',{**base,'Origin':'https://attacker.invalid'}),('/',{**base,'Origin':self.origin}),
            ('/',{**base,'Host':'localhost:'+str(self.port)}),('/done',base),('/?csrf=untrusted',base)]
        for path,headers in cases:
            with self.subTest(path=path,headers=headers):self.assertEqual(self.request(path=path,headers=headers)[0],403)
        self.assertEqual(self.saved,[]);self.complete()


if __name__=='__main__':unittest.main()
