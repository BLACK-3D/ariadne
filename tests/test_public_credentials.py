"""Private Public credential storage tests; every credential is fictional.

Temporary stores and link targets stay below this checkout's runtime/tmp.
No production credential, network, model, service or device is accessed.
"""
from contextlib import contextmanager
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
from ariadne import public_credentials as credentials
from ariadne.tools import ToolRegistry
from scripts import export_source, refresh_offline_source

SECRET = 'fictional-public-credential-only-THIS_IS_NOT_A_KEY'
ACCOUNT = 'fictional-public-account'


class PublicCredentialTests(unittest.TestCase):
    def setUp(self):
        temporary = ROOT / 'runtime' / 'tmp'
        temporary.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='public-credentials-test-', dir=temporary)
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
        os.environ.pop('ARIADNE_PUBLIC_SECRET', None)
        os.environ.pop('ARIADNE_PUBLIC_ACCOUNT_ID', None)

    def write(self, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(payload)

    def envelope(self, blob=b'fictional-encrypted-container'):
        return json.dumps({'schema': 1, 'protection': 'windows-current-user-dpapi',
                           'blob': base64.b64encode(blob).decode('ascii')}).encode()

    def test_unconfigured_status_does_not_create_a_file(self):
        self.assertFalse(credentials.configured(self.guard))
        self.assertFalse(self.path.exists())
        with self.assertRaises(FileNotFoundError):
            credentials.load(self.guard)

    @unittest.skipUnless(os.name == 'nt', 'Windows CurrentUser DPAPI native qualification')
    def test_current_user_dpapi_roundtrip_stores_no_plaintext_or_bearer(self):
        result = credentials.save(self.guard, SECRET, ACCOUNT)
        payload = self.path.read_bytes()
        self.assertEqual(result, {'configured': True, 'protection': 'windows-current-user-dpapi',
                                  'network_requests': 0})
        self.assertNotIn(SECRET.encode(), payload)
        self.assertNotIn(ACCOUNT.encode(), payload)
        self.assertNotIn(b'Bearer ', payload)
        self.assertNotIn(SECRET, repr(result))
        self.assertTrue(credentials.configured(self.guard))
        self.assertEqual(credentials.load(self.guard), (SECRET, ACCOUNT))
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ['credential.dpapi'])
        self.assertNotIn('ARIADNE_PUBLIC_SECRET', os.environ)
        self.assertNotIn('ARIADNE_PUBLIC_ACCOUNT_ID', os.environ)

    def test_environment_pair_works_without_saved_storage(self):
        with patch.dict(os.environ, {'ARIADNE_PUBLIC_SECRET': SECRET,
                                     'ARIADNE_PUBLIC_ACCOUNT_ID': ACCOUNT}), \
             patch.object(credentials, '_raw', side_effect=AssertionError('Do not read private file')):
            self.assertEqual(credentials.load(self.guard), (SECRET, ACCOUNT))
        self.assertFalse(self.path.exists())

    def test_environment_pair_takes_precedence_over_even_an_invalid_saved_store(self):
        self.write(b'not a valid container')
        with patch.dict(os.environ, {'ARIADNE_PUBLIC_SECRET': SECRET,
                                     'ARIADNE_PUBLIC_ACCOUNT_ID': ACCOUNT}), \
             patch.object(credentials, '_dpapi', side_effect=AssertionError('Do not unlock saved file')):
            self.assertEqual(credentials.load(self.guard), (SECRET, ACCOUNT))
        self.assertEqual(self.path.read_bytes(), b'not a valid container')

    def test_partial_environment_pair_never_falls_back_to_disk(self):
        for environment in ({'ARIADNE_PUBLIC_SECRET': SECRET}, {'ARIADNE_PUBLIC_ACCOUNT_ID': ACCOUNT},
                            {'ARIADNE_PUBLIC_SECRET': '', 'ARIADNE_PUBLIC_ACCOUNT_ID': ACCOUNT}):
            with self.subTest(keys=sorted(environment)), patch.dict(os.environ, environment), \
                 patch.object(credentials, '_raw', side_effect=AssertionError('No fallback')):
                with self.assertRaises(ValueError) as caught:
                    credentials.load(self.guard)
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertNotIn(ACCOUNT, str(caught.exception))

    def test_invalid_keys_and_account_ids_are_not_written_or_echoed(self):
        for secret, account in ((None, ACCOUNT), ('', ACCOUNT), ('x' * 4097, ACCOUNT),
                                ('secret\r\nheader', ACCOUNT), ('secret key', ACCOUNT),
                                ('nonascii-\u00e9', ACCOUNT), (SECRET, '../account'),
                                (SECRET, 'account/positions'), (SECRET, 'id?x=1'),
                                (SECRET, None), (SECRET, 'x' * 129)):
            with self.subTest(secret_type=type(secret).__name__, account_type=type(account).__name__), \
                 patch.object(credentials, '_dpapi', side_effect=AssertionError('No encryption before validation')):
                with self.assertRaises(ValueError) as caught:
                    credentials.save(self.guard, secret, account)
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertFalse(self.path.exists())

    def test_existing_store_is_not_replaced_or_unlocked(self):
        original = self.envelope()
        self.write(original)
        with patch.object(credentials, '_dpapi', side_effect=AssertionError('No encrypt/unlock')):
            with self.assertRaises(FileExistsError):
                credentials.save(self.guard, SECRET, ACCOUNT)
        self.assertEqual(self.path.read_bytes(), original)

    def test_concurrent_publication_cannot_overwrite_another_complete_store(self):
        original = self.envelope(b'concurrent-fictional-ciphertext')
        publisher = credentials.publish_bytes
        def competing_publish(path, payload):
            path.write_bytes(original)
            publisher(path, payload)
        with patch.object(credentials, '_dpapi', return_value=b'fictional-ciphertext'), \
             patch.object(credentials, 'publish_bytes', side_effect=competing_publish):
            with self.assertRaises(FileExistsError):
                credentials.save(self.guard, SECRET, ACCOUNT)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('.*.box-*.tmp')), [])

    def test_duplicate_nonfinite_and_malformed_envelopes_fail_before_decryption(self):
        values = [b'{"schema":1,"schema":1,"protection":"windows-current-user-dpapi","blob":"eA=="}',
                  b'{"schema":NaN,"protection":"windows-current-user-dpapi","blob":"eA=="}',
                  b'{"schema":1,"protection":"windows-current-user-dpapi","blob":Infinity}',
                  b'{bad json', b'[]', b'null', b'\xff', b'{}']
        for changes in ({'schema': True}, {'schema': 2}, {'protection': 'plaintext'},
                        {'blob': '*invalid-base64*'}, {'blob': ''}, {'blob': 42}, {'extra': SECRET}):
            value = json.loads(self.envelope())
            value.update(changes)
            values.append(json.dumps(value).encode())
        for payload in values:
            with self.subTest(payload_length=len(payload)), \
                 patch.object(credentials, '_dpapi', side_effect=AssertionError('No decryption')):
                self.write(payload)
                with self.assertRaises(ValueError) as caught:
                    credentials.load(self.guard)
                self.assertNotIn(SECRET, str(caught.exception))
                with self.assertRaises(ValueError):
                    credentials.configured(self.guard)

    def test_bounded_file_and_invalid_dpapi_bytes_fail_without_disclosing_data(self):
        self.write(b'x' * (credentials.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            credentials.load(self.guard)
        self.write(self.envelope(b'not-a-dpapi-blob'))
        with self.assertRaises(ValueError) as caught:
            credentials.load(self.guard)
        self.assertNotIn('not-a-dpapi-blob', str(caught.exception))

    def test_unlocked_duplicate_nonfinite_invalid_or_unknown_fields_are_refused(self):
        self.write(self.envelope())
        for value in (b'{"secret":"x","secret":"y","account_id":"id"}',
                      b'{"secret":NaN,"account_id":"id"}', b'[]', b'{}',
                      json.dumps({'secret': SECRET, 'account_id': ACCOUNT, 'bearer': 'fictional'}).encode(),
                      json.dumps({'secret': 'bad\nsecret', 'account_id': ACCOUNT}).encode()):
            with self.subTest(size=len(value)), patch.object(credentials, '_dpapi', return_value=value):
                with self.assertRaises(ValueError) as caught:
                    credentials.load(self.guard)
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertNotIn(ACCOUNT, str(caught.exception))

    def test_hardlinked_store_is_refused_before_dpapi(self):
        self.write(self.envelope())
        alias = self.root / 'fictional-hardlink.dpapi'
        os.link(self.path, alias)
        with patch.object(credentials, '_dpapi', side_effect=AssertionError('No DPAPI read for linked files')):
            with self.assertRaises(ValueError):
                credentials.load(self.guard)
            with self.assertRaises(ValueError):
                credentials.configured(self.guard)
        self.assertEqual(self.path.stat().st_nlink, 2)

    def test_file_reparse_point_guard_rejects_before_decrypt(self):
        self.write(self.envelope())
        original_lstat = Path.lstat
        target = self.path
        class ReparseInfo:
            st_file_attributes = 0x400
            st_mode = 0o100600
        def lstat(path, *args, **kwargs):
            if path == target:
                return ReparseInfo()
            return original_lstat(path, *args, **kwargs)
        with patch.object(Path, 'lstat', lstat), \
             patch.object(credentials, '_dpapi', side_effect=AssertionError('No DPAPI read for reparse files')):
            with self.assertRaises(ValueError):
                credentials.load(self.guard)

    @unittest.skipUnless(os.name == 'nt', 'Native Windows junction guard')
    def test_native_junction_parent_cannot_redirect_credential_write(self):
        destination = self.root / 'junction-target'
        destination.mkdir()
        link = self.data / 'public-market'
        self.assertTrue(link.absolute().is_relative_to(self.root.absolute()))
        self.assertTrue(destination.absolute().is_relative_to(self.root.absolute()))
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(destination)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            self.skipTest('Windows did not permit creating the isolated test junction.')
        try:
            with patch.object(credentials, '_dpapi', side_effect=AssertionError('No encryption across junction')):
                with self.assertRaises(ValueError):
                    credentials.save(self.guard, SECRET, ACCOUNT)
                with self.assertRaises(ValueError):
                    credentials.configured(self.guard)
            self.assertEqual(list(destination.iterdir()), [])
        finally:
            # Remove only this verified test junction itself, never its target.
            link.rmdir()

    def test_native_protector_rejects_empty_and_oversized_inputs(self):
        for value in (b'', b'x' * (credentials.MAX_BYTES + 1), 'not-bytes', None):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(ValueError):
                    credentials._dpapi(value)

    def test_credential_location_is_gitignored_and_not_a_public_source_or_runtime_member(self):
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
