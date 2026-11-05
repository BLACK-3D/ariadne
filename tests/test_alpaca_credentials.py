"""Fictional keys in guarded per-test stores below Yikes; no owner secrets."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne import alpaca_credentials as credentials
from ariadne.tools import ToolRegistry
from scripts import export_source, refresh_offline_source

KEY = 'FICTIONAL_ALPACA_KEY_ID'
SECRET = 'fictional-alpaca-secret-not-a-real-key'


class AlpacaCredentialTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / 'runtime/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='alpaca-credentials-', dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.guard = object.__new__(ToolRegistry)
        self.guard.root = self.data
        self.path = self.data / credentials.PRIVATE_PATH
        environment = patch.dict(os.environ, {}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('ARIADNE_ALPACA_KEY_ID', None)
        os.environ.pop('ARIADNE_ALPACA_SECRET_KEY', None)

    def write(self, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(payload)

    def envelope(self, blob=b'fictional-encrypted-bytes'):
        return json.dumps({'schema': 1, 'source_id': 'alpaca_opra_v1',
            'protection': 'windows-current-user-dpapi', 'blob': base64.b64encode(blob).decode()}).encode()

    def test_unconfigured_status_creates_nothing(self):
        self.assertFalse(credentials.configured(self.guard))
        with self.assertRaises(FileNotFoundError):
            credentials.load(self.guard)
        self.assertFalse(self.path.exists())

    @unittest.skipUnless(os.name == 'nt', 'Native Windows DPAPI qualification')
    def test_native_current_user_roundtrip_no_plaintext(self):
        status = credentials.save(self.guard, KEY, SECRET)
        self.assertEqual(status['network_requests'], 0)
        self.assertEqual(credentials.load(self.guard), (KEY, SECRET))
        self.assertTrue(credentials.configured(self.guard))
        payload = self.path.read_bytes()
        self.assertNotIn(KEY.encode(), payload)
        self.assertNotIn(SECRET.encode(), payload)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ['credential.dpapi'])

    def test_session_pair_has_precedence_and_no_disk_read(self):
        with patch.dict(os.environ, {'ARIADNE_ALPACA_KEY_ID': KEY, 'ARIADNE_ALPACA_SECRET_KEY': SECRET}), patch.object(credentials, '_raw', side_effect=AssertionError('No file read')):
            self.assertEqual(credentials.load(self.guard), (KEY, SECRET))
        self.assertFalse(self.path.exists())

    def test_partial_or_empty_environment_pair_does_not_fall_back(self):
        for values in ({'ARIADNE_ALPACA_KEY_ID': KEY}, {'ARIADNE_ALPACA_SECRET_KEY': SECRET},
                       {'ARIADNE_ALPACA_KEY_ID': KEY, 'ARIADNE_ALPACA_SECRET_KEY': ''}):
            with self.subTest(keys=sorted(values)), patch.dict(os.environ, values), patch.object(credentials, '_raw', side_effect=AssertionError('No fallback')):
                with self.assertRaises(ValueError) as caught:
                    credentials.load(self.guard)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertNotIn(SECRET, str(caught.exception))

    def test_invalid_credentials_fail_before_write_or_encryption(self):
        for key, secret in ((None, SECRET), ('', SECRET), ('bad key', SECRET), ('x' * 129, SECRET),
                            (KEY, None), (KEY, ''), (KEY, 'x' * 4097), (KEY, 'bad\nheader'),
                            (KEY, 'nonascii-\u00e9'), (KEY, 'with space')):
            with patch.object(credentials, '_dpapi', side_effect=AssertionError('No encryption')):
                with self.assertRaises(ValueError):
                    credentials.save(self.guard, key, secret)
                self.assertFalse(self.path.exists())

    def test_existing_and_concurrent_store_never_overwritten(self):
        original = self.envelope()
        self.write(original)
        with patch.object(credentials, '_dpapi', side_effect=AssertionError('No replacement')):
            with self.assertRaises(FileExistsError):
                credentials.save(self.guard, KEY, SECRET)
        self.assertEqual(self.path.read_bytes(), original)

    def test_concurrent_publication_never_overwrites_winner(self):
        original = self.envelope()
        publisher = credentials.publish_bytes
        def compete(path, payload):
            path.write_bytes(original)
            publisher(path, payload)
        with patch.object(credentials, '_dpapi', return_value=b'fictional-encrypted'), patch.object(credentials, 'publish_bytes', side_effect=compete):
            with self.assertRaises(FileExistsError):
                credentials.save(self.guard, KEY, SECRET)
        self.assertEqual(self.path.read_bytes(), original)

    def test_malformed_or_cross_provider_container_fails_before_unlock(self):
        payloads = [b'{}', b'[]', b'{bad', b'\xff',
            b'{"schema":1,"schema":1}', b'{"schema":NaN}']
        for changes in ({'schema': True}, {'schema': 2}, {'source_id': 'public'},
                        {'blob': ''}, {'blob': '*invalid*'}, {'protection': 'plaintext'}, {'extra': SECRET}):
            value = json.loads(self.envelope())
            value.update(changes)
            payloads.append(json.dumps(value).encode())
        for payload in payloads:
            self.write(payload)
            with patch.object(credentials, '_dpapi', side_effect=AssertionError('No unlocking')):
                with self.assertRaises(ValueError):
                    credentials.load(self.guard)
                with self.assertRaises(ValueError):
                    credentials.configured(self.guard)

    def test_unlocked_payload_must_have_correct_provider_and_exact_fields(self):
        self.write(self.envelope())
        payloads = [b'{}', b'[]', b'{"secret_key":NaN}',
            json.dumps({'source_id': 'public', 'key_id': KEY, 'secret_key': SECRET}).encode(),
            json.dumps({'source_id': 'alpaca_opra_v1', 'key_id': KEY, 'secret_key': SECRET, 'extra': 1}).encode()]
        for payload in payloads:
            with patch.object(credentials, '_dpapi', return_value=payload):
                with self.assertRaises(ValueError) as caught:
                    credentials.load(self.guard)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertNotIn(SECRET, str(caught.exception))

    def test_hardlinked_store_refused_before_unlock(self):
        self.write(self.envelope())
        os.link(self.path, self.root / 'fictional-link.dpapi')
        with patch.object(credentials, '_dpapi', side_effect=AssertionError('No link read')):
            with self.assertRaises(ValueError):
                credentials.load(self.guard)
            with self.assertRaises(ValueError):
                credentials.configured(self.guard)

    def test_oversized_store_and_dpapi_error_are_redacted(self):
        self.write(b'x' * (credentials.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            credentials.load(self.guard)
        self.write(self.envelope())
        with patch.object(credentials, '_windows_dpapi', side_effect=ValueError(KEY + SECRET)):
            with self.assertRaises(ValueError) as caught:
                credentials.load(self.guard)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn(SECRET, str(caught.exception))

    def test_private_location_is_excluded_from_exports_and_git(self):
        member = 'data/' + credentials.PRIVATE_PATH
        result = subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '--no-index', '--', member],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(refresh_offline_source._source_member(member))
        self.assertFalse(refresh_offline_source._runtime_member(member))
        with self.assertRaises(ValueError):
            export_source._member(member)


if __name__ == '__main__':
    unittest.main()
