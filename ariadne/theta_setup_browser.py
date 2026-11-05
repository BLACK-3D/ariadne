"""Single-use numeric-loopback key entry; no outbound calls or target launch.

The key travels only in a bounded same-origin POST body and existing CurrentUser
DPAPI storage. Browsers may retain form data in memory; no secure-erasure claim.
"""
from __future__ import annotations
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import re
import secrets
import socket
from socketserver import TCPServer
import threading
import time
from urllib.parse import parse_qsl

from .locking import InstanceLock
from . import theta_terminal as terminal

MAX_LIFETIME = 300
MAX_REQUESTS = 32
MAX_REQUEST_LINE = 2048
MAX_HEADER_BYTES = 8192
MAX_HEADER_LINES = 32
MAX_BODY = 14 * 1024
COOKIE = 'ariadne_theta_setup'
_CSP = "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
_SAVED = b'<!doctype html><html><title>Saved locally</title><body><h1>Theta key saved locally</h1><p>You can close this page.</p></body></html>'
_STOPPED = b'<!doctype html><html><title>Setup stopped</title><body><h1>Setup stopped</h1><p>Inspect the local credential status before starting another setup.</p></body></html>'


class ThetaSetupError(ValueError):
    """Fixed diagnostic only; never parser, key, body or native exception text."""


def _available(data):
    # Presence, even an empty/invalid environment override, must not fall back
    # to saving another key. A malformed existing store is not overwritten.
    if 'THETADATA_API_KEY' in os.environ or data._path(terminal.PRIVATE_PATH).exists():
        raise ThetaSetupError('A Theta key or environment override already exists. Inspect it privately; setup cannot replace it.')


class _State:
    def __init__(self, data, deadline):
        self.data, self.deadline = data, deadline
        self.csrf, self.cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.host = self.origin = None
        self.requests = 0
        self.used = self.saved = self.failed = self.done = self.expired = False
        self.grace_deadline = None
        self.active = None
        self.active_lock = threading.Lock()

    def valid(self):
        return not self.expired and time.monotonic() < self.deadline

    def expire(self, server):
        self.expired = True
        # shutdown wakes a blocked rfile read even when makefile retains the
        # socket descriptor. This is an absolute session deadline, not merely
        # an inactivity timeout that a slow sender could indefinitely refresh.
        with self.active_lock:
            active = self.active
        if active is not None:
            try:active.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            try:active.close()
            except OSError:pass
        try:server.socket.close()
        except OSError:pass


class _HeaderReader:
    def __init__(self, stream):
        self.stream, self.bytes, self.lines, self.ended = stream, 0, 0, False
    def readline(self, size=-1):
        capacity = min(MAX_REQUEST_LINE + 1, MAX_HEADER_BYTES + 1 - self.bytes)
        if capacity <= 0 or self.lines >= MAX_HEADER_LINES:
            raise http.client.HTTPException('Bounded headers exceeded')
        line = self.stream.readline(capacity)
        self.bytes += len(line);self.lines += 1
        if (self.bytes > MAX_HEADER_BYTES or len(line) > MAX_REQUEST_LINE
                or line and not line.endswith(b'\r\n')):
            raise http.client.HTTPException('Bounded headers exceeded')
        if line == b'\r\n':self.ended = True
        return line


def _handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'AriadneLocalSetup'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(max(.05, min(2.0, state.deadline - time.monotonic())))
            with state.active_lock:state.active = self.connection

        def finish(self):
            try:super().finish()
            except OSError:pass
            finally:
                with state.active_lock:
                    if state.active is self.connection:state.active = None

        def log_message(self, *args):pass
        def log_error(self, *args):pass
        def log_request(self, *args):pass

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler may pass the raw requestline/version as an
            # error message before route methods run. Deliberately discard it.
            self._respond(code if type(code) is int and 400 <= code <= 599 else 400, _STOPPED)

        def handle_expect_100(self):
            self.send_error(400)
            return False

        def parse_request(self):
            original = self.rfile;bounded = _HeaderReader(original);self.rfile = bounded
            try:
                result = super().parse_request()
                if result and not bounded.ended:
                    self.send_error(400);return False
                return result
            finally:self.rfile = original

        def handle_one_request(self):
            self.close_connection = True
            state.requests += 1
            if state.requests > MAX_REQUESTS or not state.valid():
                state.done = True
                return
            try:
                self.raw_requestline = self.rfile.readline(MAX_REQUEST_LINE + 1)
                if not self.raw_requestline:return
                if len(self.raw_requestline) > MAX_REQUEST_LINE or not self.raw_requestline.endswith(b'\r\n'):
                    self.request_version = 'HTTP/1.1';self.requestline = '';self.command = None
                    self.send_error(400);return
                if not self.parse_request():return
                self.close_connection = True
                if self.request_version != 'HTTP/1.1':self.send_error(400);return
                if self.command == 'GET':self.do_GET()
                elif self.command == 'POST':self.do_POST()
                else:self.send_error(405)
            except (OSError, ValueError, UnicodeError, OverflowError, http.client.HTTPException):
                # No tracebacks, body, path, headers or native exception text.
                if state.used and not state.saved:state.failed = state.done = True
            except Exception:
                state.failed = state.done = True

        def _respond(self, code, body, *, cookie=None, location=None):
            self.close_connection = True
            if not state.valid():return
            try:
                self.connection.settimeout(max(.05, min(2.0, state.deadline - time.monotonic())))
                self.send_response(code)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.send_header('Cache-Control', 'no-store, max-age=0')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Content-Security-Policy', _CSP)
                self.send_header('X-Content-Type-Options', 'nosniff')
                # Native same-origin POSTs need their canonical Origin;
                # no-referrer makes it null under Fetch's Origin-header rules.
                self.send_header('Referrer-Policy', 'same-origin')
                self.send_header('X-Frame-Options', 'DENY')
                if cookie is not None:self.send_header('Set-Cookie', cookie)
                if location is not None:self.send_header('Location', location)
                self.end_headers()
                self.wfile.write(body)
            except (OSError, ValueError):pass

        def _one(self, key, *, required=True):
            values = self.headers.get_all(key, [])
            if len(values) != 1 if required else len(values) > 1:
                raise ValueError
            return values[0] if values else None

        def _same_origin(self, *, post=False):
            if not state.valid() or self._one('Host') != state.host:raise ValueError
            if self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Expect'):raise ValueError
            origin = self._one('Origin', required=post)
            if origin is not None and origin != state.origin:raise ValueError
            site = self._one('Sec-Fetch-Site', required=False)
            mode = self._one('Sec-Fetch-Mode', required=False)
            dest = self._one('Sec-Fetch-Dest', required=False)
            # A user opening this local page from another application produces
            # Chrome's cross-site top-level navigation metadata. It may receive
            # the fixed initial form, but never POST, embedded/cors content,
            # another path or an Origin-bearing cross-site request.
            initial_navigation = (not post and self.command == 'GET' and self.path == '/'
                and not state.used and origin is None and site == 'cross-site'
                and mode == 'navigate' and dest == 'document')
            if site is not None and site not in ('none', 'same-origin') and not initial_navigation:raise ValueError
            if mode is not None and mode not in ('navigate', 'same-origin'):raise ValueError
            if dest is not None and dest not in ('document', 'empty'):raise ValueError

        def do_GET(self):
            try:
                self._same_origin()
                if self.headers.get_all('Content-Length') or self.headers.get_all('Content-Type'):raise ValueError
                if self.path == '/done' and state.saved:
                    self._respond(200, _SAVED);state.done = True;return
                if self.path != '/':self.send_error(404);return
                if state.used:self.send_error(410);return
                # Only random CSRF material is reflected, never supplied text.
                body = ('<!doctype html><html><title>Theta local setup</title><body>'
                    '<h1>Save your Theta API key locally</h1>'
                    '<p>This saves the key with Windows user protection. It does not sign in, start Terminal, buy data or trade.</p>'
                    '<form method="post" action="/save" autocomplete="off" accept-charset="UTF-8">'
                    '<input type="hidden" name="csrf" value="' + state.csrf + '">'
                    '<label>Theta API key <input type="password" name="key" maxlength="4096" autocomplete="off" spellcheck="false" required></label>'
                    '<button type="submit">Save locally</button></form></body></html>').encode('ascii')
                cookie = COOKIE + '=' + state.cookie + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=' + str(MAX_LIFETIME)
                self._respond(200, body, cookie=cookie)
            except (ValueError, TypeError):self.send_error(403)

        def do_POST(self):
            key = None
            try:
                self._same_origin(post=True)
                if self.path != '/save':self.send_error(404);return
                if state.used:self.send_error(410);return
                length = self._one('Content-Length')
                if not re.fullmatch(r'(?:0|[1-9][0-9]{0,4})', length or ''):raise ValueError
                size = int(length)
                if not 1 <= size <= MAX_BODY:raise ValueError
                content_type = self._one('Content-Type')
                if content_type.lower() not in ('application/x-www-form-urlencoded', 'application/x-www-form-urlencoded; charset=utf-8'):raise ValueError
                cookies = self._one('Cookie')
                matching = [part.strip().partition('=')[2] for part in cookies.split(';')
                    if part.strip().partition('=')[0] == COOKIE]
                if len(matching) != 1 or not hmac.compare_digest(matching[0], state.cookie):raise ValueError
                raw = self.rfile.read(size)
                if len(raw) != size or not state.valid():raise ValueError
                form = raw.decode('ascii')
                if re.search(r'%(?![A-Fa-f0-9]{2})', form):raise ValueError
                fields = parse_qsl(form, keep_blank_values=True, strict_parsing=True,
                    encoding='utf-8', errors='strict', max_num_fields=2, separator='&')
                if len(fields) != 2 or {name for name, _ in fields} != {'csrf', 'key'}:raise ValueError
                values = dict(fields)
                if not hmac.compare_digest(values['csrf'], state.csrf):raise ValueError
                # An authorized form submission is consumed before validation or
                # storage. A native failure cannot trigger an automatic retry.
                state.used = True
                state.csrf = state.cookie = None
                key = terminal.validate_key(values['key'])
                _available(state.data)
                if not state.valid():raise ValueError
                terminal.save_key(state.data, key)
                state.saved = True
                state.grace_deadline = min(state.deadline, time.monotonic() + 2)
                self._respond(303, b'', cookie=COOKIE+'=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0', location='/done')
            except (ValueError, TypeError, UnicodeError, OSError):
                if state.used:state.failed = state.done = True
                self.send_error(400)
            except Exception:
                state.failed = state.done = True
                self.send_error(500)
            finally:key = None
    return Handler


class _Server(HTTPServer):
    allow_reuse_address = False
    request_queue_size = 1
    address_family = socket.AF_INET
    def server_bind(self):
        # HTTPServer normally performs getfqdn() here, including reverse DNS.
        # Binding a numeric loopback endpoint needs no resolver or outbound call.
        TCPServer.server_bind(self)
        self.server_name = '127.0.0.1'
        self.server_port = self.server_address[1]
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address
    def verify_request(self, request, client_address):
        return client_address[0] == '127.0.0.1'
    def handle_error(self, request, client_address):pass


def serve(data, *, lifetime=MAX_LIFETIME, ready=None):
    """Serve one bounded owner-managed setup; ``ready`` receives a URL only.

    The callback must be trusted/nonblocking (e.g. print the local URL). This
    helper neither opens a browser nor changes the installed network policy.
    """
    if type(lifetime) is not int or not 1 <= lifetime <= MAX_LIFETIME or not callable(ready):
        raise ThetaSetupError('Use a one-to300-second local setup and a URL callback.')
    _available(data)
    lock = InstanceLock(data.root/'theta-setup.lock')
    lock.acquire()
    server = timer = None
    state = _State(data, time.monotonic()+lifetime)
    try:
        _available(data)
        server = _Server(('127.0.0.1', 0), _handler(state))
        state.host = '127.0.0.1:' + str(server.server_address[1])
        state.origin = 'http://' + state.host
        server.timeout = .2
        timer = threading.Timer(lifetime, state.expire, args=(server,));timer.daemon = True;timer.start()
        ready(state.origin + '/')
        while state.valid() and not state.done:
            if state.saved and time.monotonic() >= state.grace_deadline:break
            try:server.handle_request()
            except (OSError, ValueError):
                if state.expired:break
                raise ThetaSetupError('Local setup stopped; inspect private credential status before another attempt.') from None
        if state.failed:
            raise ThetaSetupError('Local setup stopped; inspect private credential status before another attempt.')
        return {'schema': 1, 'status': 'saved' if state.saved else 'expired',
            'configured': state.saved, 'protection': 'windows-current-user-dpapi' if state.saved else None,
            'setup_channel': 'single_use_loopback_browser', 'network_requests': 0,
            'outbound_network_requests': 0, 'model_calls': 0, 'application_launched': False,
            'paper_fills': 0, 'funding_added': '0'}
    except ThetaSetupError:raise
    except Exception:
        raise ThetaSetupError('Local setup stopped; inspect private credential status before another attempt.') from None
    finally:
        state.csrf = state.cookie = None
        if timer is not None:timer.cancel()
        if server is not None:server.server_close()
        lock.release()
