"""Private Theta key storage and fixed-route application preparation; never launch Java.

The vendor bootstrap uses the key in a URL path. This module deliberately never
logs URLs, request headers, response error bodies or underlying exception text.
An observed TLS download plus a local digest is not a publisher signature.
"""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hashlib
import http.client
from io import BytesIO
import json
import os
from pathlib import PurePosixPath
import re
import ssl
import stat
import time
import unicodedata
from urllib.parse import quote
import zipfile

from .atomic import publish_bytes
from .network_policy import install_network_policy
from .public_credentials import _dpapi as _windows_dpapi

SOURCE_ID = 'theta_terminal_bootstrap_v3'
PRIVATE_PATH = 'theta-terminal/credential.dpapi'
BOOTSTRAP_PATH = 'runtime/theta/ThetaTerminalv3.jar'
BOOTSTRAP_SHA256 = '08773e104a6df321f35fdd0d59d455065cac1ed36aa82b9962557dd740fd20b5'
JAVA_PATH = 'runtime/java21/bin/java.exe'
JAVA_SHA256 = 'f8dc082a7dedbfd2924c3562405a5a1fd12efd2641808c92e2de819c76a58b7e'
LIBRARY_PATH = 'runtime/theta/lib'
APPLICATION_PATH = 'runtime/theta/application.json'
HOST = 'nexus-api.thetadata.us'
MAX_PRIVATE_BYTES = 32768
MAX_JAR_BYTES = 64 * 1024 * 1024
MAX_COLLECTION_SECONDS = 120
# Assigned response length is unverified; published release bases use eight
# digits while the bootstrap's manual override expects nine. Keep the filename
# numeric and bounded without claiming the manual override defines the server.
_VERSION = re.compile(r'[0-9]{8,12}\Z')
_MAIN = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\Z')


_DIAGNOSTIC_STAGES = frozenset({'local', 'policy', 'deadline', 'connect', 'tls',
    'dispatch', 'response', 'http_status', 'content_policy', 'size', 'body',
    'version_decode', 'artifact_inspection', 'credential_reflection', 'owner_policy'})
_DIAGNOSTIC_FLAGS = ('media_valid', 'encoding_valid', 'size_valid', 'version_valid')


def safe_diagnostic(value=None):
    """Copy only fixed enums, strict numbers and booleans; never vendor text.

    A request reservation is not proof of dispatch. ``request_attempted`` means
    request() was entered; ``request_dispatched`` is None if it raised before
    returning, and True only if it returned. Neither proves server processing.
    """
    # Filter key objects before lookups, so hostile unknown keys cannot run
    # equality/formatting hooks while matching an allowlisted field name.
    allowed = {'operation', 'stage', 'http_status', 'bytes_observed',
        'request_attempted', 'request_dispatched', 'response_observed', *_DIAGNOSTIC_FLAGS}
    value = {key: item for key, item in value.items() if type(key) is str and key in allowed} if type(value) is dict else {}
    result = {'schema': 1,
        'operation': value.get('operation') if type(value.get('operation')) is str and value.get('operation') in ('version', 'application') else None,
        'stage': value.get('stage') if type(value.get('stage')) is str and value.get('stage') in _DIAGNOSTIC_STAGES else 'local',
        'http_status': None, 'bytes_observed': 0, 'request_attempted': False,
        'request_dispatched': False, 'response_observed': False,
        **{name: None for name in _DIAGNOSTIC_FLAGS}}
    status = value.get('http_status')
    if type(status) is int and 100 <= status <= 599:
        result['http_status'] = status
    count = value.get('bytes_observed')
    if type(count) is int and 0 <= count <= MAX_JAR_BYTES + 1:
        result['bytes_observed'] = count
    for name in ('request_attempted', 'response_observed'):
        if type(value.get(name)) is bool:
            result[name] = value[name]
    dispatched = value.get('request_dispatched', False)
    if dispatched is None or type(dispatched) is bool:
        result['request_dispatched'] = dispatched
    for name in _DIAGNOSTIC_FLAGS:
        if type(value.get(name)) is bool:
            result[name] = value[name]
    # Reject inconsistent claims rather than deriving information from text.
    if not result['request_attempted']:
        result['request_dispatched'] = False
        result['response_observed'] = False
    if result['request_dispatched'] is not True:
        result['response_observed'] = False
    if not result['response_observed']:
        result['http_status'] = None
        result['bytes_observed'] = 0
        for name in ('media_valid', 'encoding_valid', 'size_valid'):
            result[name] = None
        if result['version_valid'] is True:
            result['version_valid'] = None
    return result


class ThetaTerminalError(ValueError):
    """A fixed public message with optional allowlisted machine diagnostics."""
    def __init__(self, message, *, diagnostic=None):
        super().__init__(message)
        self._diagnostic = safe_diagnostic(diagnostic)

    @property
    def diagnostic(self):
        return safe_diagnostic(self._diagnostic)


class ThetaTerminalUnavailable(ThetaTerminalError):
    """Preparation is unavailable; no retry or target execution is performed."""


def diagnostic_from_error(error):
    """Do not invoke properties or formatting on unknown exception objects."""
    if type(error) in (ThetaTerminalError, ThetaTerminalUnavailable):
        attributes = error.__dict__
        return safe_diagnostic(attributes.get('_diagnostic') if type(attributes) is dict else None)
    return safe_diagnostic()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def validate_key(value):
    if (type(value) is not str or not 16 <= len(value) <= 4096
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise ThetaTerminalError('Use a bounded printable Theta API key at the hidden local prompt, not an account password.')
    return value


def _read(guard, name, maximum):
    from scripts.options_market import read
    return read(guard, name, maximum)


def _json(raw):
    from scripts.options_market import metadata
    return metadata(raw)


def _protect(raw, *, decrypt=False):
    try:
        return _windows_dpapi(raw, decrypt=decrypt)
    except (ValueError, OSError):
        raise ThetaTerminalError('Theta saved keys require Windows CurrentUser DPAPI. No key was disclosed.') from None


def _envelope(raw):
    try:
        value = _json(raw)
        if (type(value) is not dict or set(value) != {'schema', 'source_id', 'protection', 'blob'}
                or type(value['schema']) is not int or value['schema'] != 1
                or value['source_id'] != SOURCE_ID
                or value['protection'] != 'windows-current-user-dpapi'
                or type(value['blob']) is not str):
            raise ValueError
        protected = base64.b64decode(value['blob'], validate=True)
        if not 1 <= len(protected) <= MAX_PRIVATE_BYTES:
            raise ValueError
        return protected
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ThetaTerminalError('Saved Theta credential container is invalid; inspect it privately.') from None


def configured(data):
    """Validate envelope presence without decrypting or making a request."""
    environment = os.environ.get('THETADATA_API_KEY')
    if environment is not None:
        validate_key(environment)
        return True
    try:
        raw = _read(data, PRIVATE_PATH, MAX_PRIVATE_BYTES)
    except FileNotFoundError:
        return False
    _envelope(raw)
    return True


def save_key(data, key):
    key = validate_key(key)
    path = data._path(PRIVATE_PATH)
    if path.exists():
        raise FileExistsError('A Theta key store already exists. Deliberate key rotation requires separate review.')
    plaintext = canonical({'source_id': SOURCE_ID, 'api_key': key})
    protected = _protect(plaintext)
    payload = canonical({'schema': 1, 'source_id': SOURCE_ID,
                         'protection': 'windows-current-user-dpapi',
                         'blob': base64.b64encode(protected).decode('ascii')})
    if len(payload) > MAX_PRIVATE_BYTES:
        raise ThetaTerminalError('Protected Theta key exceeds the storage bound.')
    with data._directory(path.parent, create=True):
        publish_bytes(path, payload)
        if _read(data, PRIVATE_PATH, MAX_PRIVATE_BYTES) != payload:
            raise ThetaTerminalError('Protected Theta key readback failed.')
    return {'schema': 1, 'configured': True, 'protection': 'windows-current-user-dpapi',
            'network_requests': 0, 'application_launched': False, 'paper_fills': 0,
            'funding_added': '0'}


def load_key(data):
    environment = os.environ.get('THETADATA_API_KEY')
    if environment is not None:
        return validate_key(environment)
    protected = _envelope(_read(data, PRIVATE_PATH, MAX_PRIVATE_BYTES))
    try:
        value = _json(_protect(protected, decrypt=True))
        if (type(value) is not dict or set(value) != {'source_id', 'api_key'}
                or value['source_id'] != SOURCE_ID):
            raise ValueError
        return validate_key(value['api_key'])
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise ThetaTerminalError('The saved Theta key could not be unlocked for this Windows user.') from None


def _version(value):
    if type(value) is not str or not _VERSION.fullmatch(value):
        raise ThetaTerminalError('Vendor version must use eight to twelve ASCII digits; other path characters are refused.')
    return value


def inspect_jar(payload):
    """Read bounded ZIP metadata/manifest only; no extraction or target execution."""
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_JAR_BYTES:
        raise ThetaTerminalError('Application JAR bytes exceed their preparation bound.')
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            members = archive.infolist()
            if not 1 <= len(members) <= 100000 or sum(item.file_size for item in members) > 512 * 1024 * 1024:
                raise ValueError
            seen = set()
            for item in members:
                name = item.orig_filename
                parts = PurePosixPath(name).parts
                mode = item.external_attr >> 16
                alias = unicodedata.normalize('NFC', name).casefold()
                if (not name or len(name) > 1024 or chr(92) in name or ':' in name or name.startswith('/')
                        or any(ord(c) < 32 or ord(c) == 127 for c in name)
                        or any(part in ('.', '..') for part in parts)
                        or alias in seen or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise ValueError
                seen.add(alias)
            info = archive.getinfo('META-INF/MANIFEST.MF')
            if info.file_size > 65536:
                raise ValueError
            manifest = archive.read(info).decode('utf-8')
            # Manifest continuation lines join onto their preceding field.
            logical = []
            header = manifest.replace('\r\n', '\n').split('\n\n', 1)[0]
            for line in header.split('\n'):
                if line.startswith(' '):
                    if not logical: raise ValueError
                    logical[-1] += line[1:]
                else:
                    logical.append(line)
            fields = {}
            for line in logical:
                if not line: continue
                key, separator, value = line.partition(': ')
                if not separator or key in fields:
                    raise ValueError
                fields[key] = value
            main = fields.get('Main-Class')
            if type(main) is not str or len(main) > 256 or not _MAIN.fullmatch(main):
                raise ValueError
            main_path = main.replace('.', '/') + '.class'
            class_info = archive.getinfo(main_path)
            if class_info.file_size < 8:
                raise ValueError
            with archive.open(class_info) as stream:
                header = stream.read(8)
            if header[:4] != b'\xca\xfe\xba\xbe':
                raise ValueError
            major = int.from_bytes(header[6:8], 'big')
            if not 45 <= major <= 100:
                raise ValueError
            return {'archive_entries': len(members), 'main_class': main,
                    'main_classfile_major': major, 'minimum_java_from_main_class': major - 44,
                    'archive_extracted': False, 'target_executed': False,
                    'main_class_provider_verified': False}
    except (ValueError, KeyError, UnicodeError, zipfile.BadZipFile, RuntimeError, OverflowError):
        raise ThetaTerminalError('Downloaded JAR is malformed, unsafe to inspect or lacks a bounded executable manifest.') from None


class ThetaBootstrapClient:
    """Two fixed vendor HTTPS GET routes; no target launch or credential logs."""
    def __init__(self, key, *, online=False):
        if type(online) is not bool:
            raise ThetaTerminalError('Online preparation opt-in must be explicit.')
        self._key = validate_key(key)
        self._online = online
        self._deadline = None

    def close(self):
        self._key = None

    def _http(self, operation, *, version=None):
        diagnostic = safe_diagnostic({'operation': operation, 'stage': 'policy'})
        if operation == 'version' and version is None:
            route = '/bootstrap/current/' + quote(self._key or '', safe='')
            maximum = 128
            accepted = {'text/plain', 'application/octet-stream', 'application/json'}
        elif operation == 'application':
            try:
                version = _version(version)
            except ThetaTerminalError:
                diagnostic.update(stage='version_decode', version_valid=False)
                raise ThetaTerminalError('Vendor version must use eight to twelve ASCII digits; other path characters are refused.', diagnostic=diagnostic) from None
            route = '/bootstrap/jars/' + version
            maximum = MAX_JAR_BYTES
            accepted = {'application/java-archive', 'application/octet-stream', 'application/zip', 'application/x-java-archive'}
        else:
            raise ThetaTerminalError('Only fixed Theta version and application-download operations exist.', diagnostic=diagnostic)
        if (not self._online or os.environ.get('ARIADNE_OFFLINE') != '0'
                or install_network_policy()['mode'] != 'online'):
            raise ThetaTerminalUnavailable('Theta preparation requires explicit online mode and enabled process policy.', diagnostic=diagnostic)
        if self._key is None:
            raise ThetaTerminalUnavailable('The private Theta preparation client is closed.', diagnostic=diagnostic)
        if self._deadline is None:
            self._deadline = time.monotonic() + MAX_COLLECTION_SECONDS
        if time.monotonic() >= self._deadline:
            diagnostic['stage'] = 'deadline'
            raise ThetaTerminalUnavailable('Theta preparation deadline expired without retry.', diagnostic=diagnostic)
        connection = response = None
        try:
            diagnostic['stage'] = 'connect'
            connection = http.client.HTTPSConnection(HOST, port=443, timeout=min(10, self._deadline-time.monotonic()),
                                                      context=ssl.create_default_context())
            connection.set_debuglevel(0)
            diagnostic.update(stage='dispatch', request_attempted=True, request_dispatched=None)
            connection.request('GET', route, headers={'Accept': ', '.join(sorted(accepted)),
                'Accept-Encoding': 'identity', 'Connection': 'close', 'User-Agent': 'Ariadne-Theta-Preparation/1'})
            diagnostic['request_dispatched'] = True
            if time.monotonic() >= self._deadline:
                diagnostic['stage'] = 'deadline'
                raise ThetaTerminalUnavailable('Theta preparation deadline expired without retry.', diagnostic=diagnostic)
            if connection.sock is not None:
                connection.sock.settimeout(min(10, self._deadline-time.monotonic()))
            diagnostic['stage'] = 'response'
            response = connection.getresponse()
            diagnostic.update(stage='http_status', response_observed=True)
            status = response.status
            if type(status) is int and 100 <= status <= 599:
                diagnostic['http_status'] = status
            if type(status) is not int or status != 200:
                raise ThetaTerminalUnavailable('Theta preparation request failed; verify access privately. No retry was attempted.', diagnostic=diagnostic)
            diagnostic['stage'] = 'content_policy'
            raw_media = response.getheader('Content-Type', '')
            raw_encoding = response.getheader('Content-Encoding', 'identity')
            length = response.getheader('Content-Length')
            media = raw_media.split(';', 1)[0].strip().lower() if type(raw_media) is str and len(raw_media) <= 512 else None
            encoding = raw_encoding.strip().lower() if type(raw_encoding) is str and len(raw_encoding) <= 64 else None
            diagnostic['media_valid'] = media in accepted
            diagnostic['encoding_valid'] = encoding in ('', 'identity')
            if not diagnostic['media_valid'] or not diagnostic['encoding_valid']:
                raise ThetaTerminalUnavailable('Theta preparation returned unsupported content.', diagnostic=diagnostic)
            diagnostic['stage'] = 'size'
            declared_size_valid = length is None or (type(length) is str
                and re.fullmatch(r'[0-9]{1,20}', length) is not None and int(length) <= maximum)
            if not declared_size_valid:
                diagnostic['size_valid'] = False
                raise ThetaTerminalUnavailable('Theta preparation content exceeds its bound.', diagnostic=diagnostic)
            raw = bytearray()
            diagnostic['stage'] = 'body'
            while True:
                remaining = self._deadline-time.monotonic()
                if remaining <= 0:
                    diagnostic['stage'] = 'deadline'
                    raise ThetaTerminalUnavailable('Theta preparation deadline expired without retry.', diagnostic=diagnostic)
                if connection.sock is not None:
                    connection.sock.settimeout(min(10, remaining))
                block = response.read1(min(65536, maximum+1-len(raw)))
                if not block: break
                raw.extend(block)
                diagnostic['bytes_observed'] = min(len(raw), MAX_JAR_BYTES+1)
                if len(raw) > maximum:
                    diagnostic.update(stage='size', size_valid=False)
                    raise ThetaTerminalUnavailable('Theta preparation content exceeds its bound.', diagnostic=diagnostic)
            if length is not None and len(raw) != int(length):
                diagnostic.update(stage='size', size_valid=False)
                raise ThetaTerminalUnavailable('Theta preparation content exceeds its bound.', diagnostic=diagnostic)
            diagnostic['size_valid'] = True
            received = datetime.now(timezone.utc).isoformat()
            if self._key.encode('ascii') in raw:
                diagnostic['stage'] = 'credential_reflection'
                raise ThetaTerminalUnavailable('Vendor response unexpectedly contains private credential material; it was not saved.', diagnostic=diagnostic)
            return {'payload': bytes(raw), 'received_at': received, 'diagnostic': safe_diagnostic(diagnostic)}
        except ThetaTerminalError:
            raise
        except ssl.SSLError:
            diagnostic['stage'] = 'tls'
            raise ThetaTerminalUnavailable('Theta preparation transport failed without retry. No private diagnostic was emitted.', diagnostic=diagnostic) from None
        except (OSError, ValueError, http.client.HTTPException, OverflowError):
            raise ThetaTerminalUnavailable('Theta preparation transport failed without retry. No private diagnostic was emitted.', diagnostic=diagnostic) from None
        finally:
            # Cleanup must not replace the fixed failure with vendor/socket text.
            for item in (response, connection):
                if item is not None:
                    try: item.close()
                    except Exception: pass

    def version(self):
        result = self._http('version')
        diagnostic = safe_diagnostic(result.get('diagnostic'))
        diagnostic['stage'] = 'version_decode'
        try:
            text = result['payload'].decode('ascii').strip()
            # Some metadata handlers negotiate application/json. Accept only
            # a literal quoted digit string: no general JSON coercion/escapes.
            if re.fullmatch(r'"[0-9]{8,12}"', text):
                text = text[1:-1]
            version = _version(text)
        except (ValueError, UnicodeError):
            diagnostic['version_valid'] = False
            raise ThetaTerminalUnavailable('Vendor version response does not match its documented bounded form.', diagnostic=diagnostic) from None
        diagnostic['version_valid'] = True
        return {'version': version, 'received_at': result['received_at'], 'diagnostic': diagnostic}

    def application(self, version):
        result = self._http('application', version=version)
        diagnostic = safe_diagnostic(result.get('diagnostic'))
        diagnostic['stage'] = 'artifact_inspection'
        try:
            evidence = inspect_jar(result['payload'])
        except ThetaTerminalError:
            raise ThetaTerminalError('Downloaded JAR is malformed, unsafe to inspect or lacks a bounded executable manifest.', diagnostic=diagnostic) from None
        if self._key.encode('ascii') in canonical(evidence):
            diagnostic['stage'] = 'credential_reflection'
            raise ThetaTerminalUnavailable('Decoded application metadata unexpectedly contains private credential material; it was not saved.', diagnostic=diagnostic)
        return {**result, 'diagnostic': diagnostic, 'evidence': evidence, 'sha256': hashlib.sha256(result['payload']).hexdigest()}


def _pinned(root, path, expected, maximum):
    try:
        raw = _read(root, path, maximum)
    except FileNotFoundError:
        return False
    return hashlib.sha256(raw).hexdigest() == expected


def status(root, data):
    """Passive local artifact/key-envelope checks, never decrypt or execute."""
    result = {'schema': 1, 'source_id': SOURCE_ID,
        'bootstrap_pinned': _pinned(root, BOOTSTRAP_PATH, BOOTSTRAP_SHA256, MAX_JAR_BYTES),
        'java_pinned': _pinned(root, JAVA_PATH, JAVA_SHA256, 8*1024*1024),
        'key_configured': configured(data), 'application_prepared': False,
        'application_state': 'absent', 'application_launched': False, 'network_requests': 0,
        'paper_fills': 0, 'funding_added': '0', 'bankroll_created': False,
        'automatic_topups': False, 'execution_allowed': False}
    try:
        manifest = _json(_read(root, APPLICATION_PATH, 32768))
    except FileNotFoundError:
        if root._path(LIBRARY_PATH).exists(): result['application_state'] = 'incomplete_preparation_held'
        return result
    required = {'schema','kind','status','version','artifact','metadata_received_at','downloaded_at',
                'bootstrap_sha256','java_sha256','provenance','execution_allowed'}
    if (type(manifest) is not dict or set(manifest) != required
            or type(manifest['schema']) is not int or manifest['schema'] != 1
            or manifest['kind'] != SOURCE_ID or manifest['status'] != 'prepared_for_static_review'
            or manifest['execution_allowed'] is not False
            or manifest['bootstrap_sha256'] != BOOTSTRAP_SHA256 or manifest['java_sha256'] != JAVA_SHA256):
        raise ThetaTerminalError('Prepared application manifest is invalid; no automatic repair or launch.')
    version = _version(manifest['version']);artifact = manifest['artifact']
    if (type(artifact) is not dict or set(artifact) != {'path','bytes','sha256','inspection'}
            or artifact['path'] != LIBRARY_PATH + '/' + version + '.jar'
            or type(artifact['bytes']) is not int or not 1 <= artifact['bytes'] <= MAX_JAR_BYTES
            or type(artifact['sha256']) is not str or not re.fullmatch(r'[a-f0-9]{64}',artifact['sha256'])):
        raise ThetaTerminalError('Prepared application artifact binding is invalid.')
    payload = _read(root, artifact['path'], MAX_JAR_BYTES)
    if (len(payload) != artifact['bytes'] or hashlib.sha256(payload).hexdigest() != artifact['sha256']
            or inspect_jar(payload) != artifact['inspection']):
        raise ThetaTerminalError('Prepared application bytes changed; no automatic repair or launch.')
    result.update(application_prepared=True, application_state='prepared_for_static_review',
                  application_version=version, application_sha256=artifact['sha256'],
                  application_main_class=artifact['inspection']['main_class'])
    return result


def prepare(root, data, *, check, client_factory=None):
    """Exclusive artifact acquisition only; old/uncertain attempts are never replayed."""
    if not callable(check):
        raise ThetaTerminalError('Owner control checks are required for application preparation.')
    policy = check()
    if not _pinned(root, BOOTSTRAP_PATH, BOOTSTRAP_SHA256, MAX_JAR_BYTES) or not _pinned(root, JAVA_PATH, JAVA_SHA256, 8*1024*1024):
        raise ThetaTerminalError('Use the reviewed pinned bootstrap and project-local Java runtime before preparation.')
    library = root._path(LIBRARY_PATH);manifest_path = root._path(APPLICATION_PATH)
    if library.exists() or manifest_path.exists():
        raise FileExistsError('A previous application preparation exists. Inspect its receipts; no overwrite or replay.')
    key = load_key(data)
    factory = ThetaBootstrapClient if client_factory is None else client_factory
    client = None
    started = datetime.now(timezone.utc).isoformat()
    with root._directory(library.parent, create=True):
        library.mkdir()
        with root._directory(library):
            publish_bytes(library/'preparation-started.json',canonical({'schema':1,'source_id':SOURCE_ID,
                'status':'started','started_at':started,'application_launched':False,'paper_fills':0,'funding_added':'0'}))
    phase = 'owner_policy'
    operation = None
    try:
        if check() != policy:
            raise ThetaTerminalUnavailable('Owner Internet or pause permission changed before preparation.', diagnostic={'stage': phase})
        phase = 'local'
        client = factory(key,online=True)
        operation = 'version'
        metadata = client.version()
        phase = 'version_decode'
        version = _version(metadata['version'])
        phase = 'owner_policy'
        if check() != policy:
            raise ThetaTerminalUnavailable('Owner Internet or pause permission changed after metadata.', diagnostic={'stage': phase})
        phase = 'local'
        operation = 'application'
        result = client.application(version)
        phase = 'owner_policy'
        if check() != policy:
            raise ThetaTerminalUnavailable('Owner Internet or pause permission changed during preparation.', diagnostic={'stage': phase})
        phase = 'artifact_inspection'
        payload = result['payload'];inspection = inspect_jar(payload)
        if key.encode('ascii') in canonical(inspection) or key.encode('ascii') in payload:
            raise ThetaTerminalError('Application evidence unexpectedly contains private credential material; it was not saved.')
        digest = hashlib.sha256(payload).hexdigest()
        if result.get('sha256') != digest or result.get('evidence') != inspection:
            raise ThetaTerminalError('Downloaded artifact evidence failed its actual-byte binding.')
        phase = 'local'
        operation = None
        artifact_path = LIBRARY_PATH+'/'+version+'.jar'
        manifest = {'schema':1,'kind':SOURCE_ID,'status':'prepared_for_static_review','version':version,
            'artifact':{'path':artifact_path,'bytes':len(payload),'sha256':digest,'inspection':inspection},
            'metadata_received_at':metadata['received_at'],'downloaded_at':result['received_at'],
            'bootstrap_sha256':BOOTSTRAP_SHA256,'java_sha256':JAVA_SHA256,'execution_allowed':False,
            'provenance':{'host':HOST,'transport':'vendor_https_tls_certificate_validation',
                'publisher_checksum_independently_verified':False,'key_in_metadata_url_path':True,
                'private_url_retained':False,'automatic_updates_enabled':False,'target_executed':False}}
        with root._directory(library):
            publish_bytes(root._path(artifact_path),payload)
            if _read(root,artifact_path,MAX_JAR_BYTES) != payload:
                raise ThetaTerminalError('Application artifact readback failed.')
        phase = 'owner_policy'
        if check() != policy:
            raise ThetaTerminalUnavailable('Owner permission changed before manifest publication.', diagnostic={'stage': phase})
        phase = 'local'
        with root._directory(manifest_path.parent):
            publish_bytes(manifest_path,canonical(manifest))
            if _read(root,APPLICATION_PATH,32768) != canonical(manifest):
                raise ThetaTerminalError('Application manifest readback failed.')
        return {'schema':1,'status':'prepared_for_static_review','version':version,
            'application_sha256':digest,'application_bytes':len(payload),'main_class':inspection['main_class'],
            'network_requests':2,'application_launched':False,'execution_allowed':False,
            'paper_fills':0,'funding_added':'0','automatic_topups':False,
            'publisher_checksum_independently_verified':False}
    except Exception as error:
        diagnostic = diagnostic_from_error(error)
        if diagnostic['stage'] == 'local' and diagnostic['operation'] is None:
            diagnostic = safe_diagnostic({'stage': phase, 'operation': operation})
        try:
            with root._directory(library):
                publish_bytes(library/'preparation-failed.json',canonical({'schema':1,'status':'failed',
                    'note':'Preparation did not publish a qualified application manifest. Inspect local evidence before any new attempt.',
                    'diagnostic': diagnostic,
                    'application_launched':False,'paper_fills':0,'funding_added':'0'}))
        except (OSError, ValueError):
            pass
        raise
    finally:
        if client is not None: client.close()
        key = None
