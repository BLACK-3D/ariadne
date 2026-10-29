"""Pinned speech provisioning with synthetic files only: no pip or network."""
import copy
import hashlib
import io
import json
from pathlib import Path
import ssl
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import urllib.request

from ariadne.speech_runtime import ASSET_PINS
from scripts import provision_speech as provision


class SpeechProvisionTests(unittest.TestCase):
    def setUp(self):
        accepted = json.loads((provision.ROOT / 'config/speech-assets.json').read_bytes())
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix='.speech-provision-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        (self.root / 'config').mkdir()
        self.value = copy.deepcopy(accepted)
        for index, item in enumerate(self.value['assets']):
            payload = f'pinned synthetic asset {index}'.encode()
            item['bytes'], item['sha256'] = len(payload), hashlib.sha256(payload).hexdigest()
            target = self.root / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        self.catalog_path = self.root / 'config/speech-assets.json'
        self.catalog_path.write_text(json.dumps(self.value), encoding='utf-8')
        self.profile = {'version': 1, 'backends': {'kokoro': {'assets': {
            role: {'path': name, 'sha256': digest} for role, (name, digest) in ASSET_PINS['kokoro'].items()}}}}
        self.target = self.data / 'media-config.json'
        self.target.write_text(json.dumps(self.profile), encoding='utf-8')
        self.root_patch = patch.object(provision, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_default_command_only_verifies_cached_files_without_install_configure_or_network(self):
        previous = self.target.read_bytes()
        with patch.object(sys, 'argv', ['provision_speech.py']), patch.object(provision, 'fetch') as fetch, \
             patch.object(provision, 'install') as install, patch.object(provision, 'configure') as configure, \
             patch.object(provision.urllib.request, 'build_opener') as opener, patch('sys.stdout', io.StringIO()):
            provision.main()
        for action in (fetch, install, configure, opener):
            action.assert_not_called()
        self.assertEqual(self.target.read_bytes(), previous)

    @patch.object(provision, 'verify_installation')
    def test_normal_merge_preserves_other_backends_and_owner_state_and_is_idempotent(self, _installed):
        self.profile['backends']['silero'] = {'assets': {'model': {
            'path': 'runtime/media/silero/model.onnx', 'sha256': '0' * 64}}}
        previous = json.dumps(self.profile, indent=3).encode()
        self.target.write_bytes(previous)
        owner_database = self.data / 'ariadne.sqlite3'
        owner_database.write_bytes(b'Private owner state sentinel; not a database opened by this test.')
        provision.configure(self.value)
        after = json.loads(self.target.read_bytes())
        self.assertEqual({key: value for key, value in after['backends'].items() if key != 'piper'}, self.profile['backends'])
        self.assertEqual(after['backends']['piper'], {'assets': self.value['profile']})
        self.assertEqual((self.data / 'media-config.pre-fast-speech.json').read_bytes(), previous)
        self.assertEqual(owner_database.read_bytes(), b'Private owner state sentinel; not a database opened by this test.')
        self.assertFalse((self.data / 'media-config.fast-speech.new').exists())
        committed = self.target.read_bytes()
        provision.configure(self.value)
        self.assertEqual(self.target.read_bytes(), committed)

    @patch.object(provision, 'verify_installation')
    def test_concurrent_owner_edit_after_profile_read_is_preserved_before_any_update_preparation(self, _installed):
        later = copy.deepcopy(self.profile)
        later['backends']['silero'] = {'assets': {'model': {
            'path': 'runtime/media/silero/model.onnx', 'sha256': '1' * 64}}}
        later_bytes = json.dumps(later).encode()
        def read_then_owner_edit(_data):
            self.target.write_bytes(later_bytes)
            return copy.deepcopy(self.profile)
        with patch('ariadne.media_tools.load_profile', side_effect=read_then_owner_edit):
            with self.assertRaisesRegex(ValueError, 'changed while it was read'):
                provision.configure(self.value)
        self.assertEqual(self.target.read_bytes(), later_bytes)
        self.assertFalse((self.data / 'media-config.pre-fast-speech.json').exists())
        self.assertFalse((self.data / 'media-config.fast-speech.new').exists())

    @patch.object(provision, 'verify_installation')
    def test_later_owner_edit_before_commit_preserves_both_owner_file_and_staged_proposal(self, _installed):
        previous = self.target.read_bytes()
        later = copy.deepcopy(self.profile)
        later['backends']['silero'] = {'assets': {'model': {
            'path': 'runtime/media/silero/model.onnx', 'sha256': '2' * 64}}}
        later_bytes = json.dumps(later).encode()
        publish = provision.publish_bytes
        def edit_after_stage(path, payload):
            publish(path, payload)
            if path.name == 'media-config.fast-speech.new':
                self.target.write_bytes(later_bytes)
        with patch.object(provision, 'publish_bytes', side_effect=edit_after_stage):
            with self.assertRaisesRegex(ValueError, 'changed while preparing'):
                provision.configure(self.value)
        self.assertEqual(self.target.read_bytes(), later_bytes)
        self.assertEqual((self.data / 'media-config.pre-fast-speech.json').read_bytes(), previous)
        self.assertIn('piper', json.loads((self.data / 'media-config.fast-speech.new').read_bytes())['backends'])

    @patch.object(provision, 'verify_installation')
    def test_existing_different_piper_profile_is_never_replaced(self, _installed):
        different = copy.deepcopy(self.value['profile'])
        different['model']['sha256'] = '3' * 64
        self.profile['backends']['piper'] = {'assets': different}
        previous = json.dumps(self.profile).encode()
        self.target.write_bytes(previous)
        with self.assertRaisesRegex(ValueError, 'preserved'):
            provision.configure(self.value)
        self.assertEqual(self.target.read_bytes(), previous)
        self.assertFalse((self.data / 'media-config.pre-fast-speech.json').exists())

    def test_configure_without_an_installed_runtime_fails_before_touching_the_owner_profile(self):
        previous = self.target.read_bytes()
        with patch('ariadne.media_tools.load_profile') as load, \
             patch.object(provision.subprocess, 'run') as pip, \
             patch.object(provision.urllib.request, 'build_opener') as network:
            with self.assertRaisesRegex(ValueError, '(?i)install'):
                provision.configure(self.value)
        load.assert_not_called()
        pip.assert_not_called()
        network.assert_not_called()
        self.assertEqual(self.target.read_bytes(), previous)
        self.assertFalse((self.data / 'media-config.pre-fast-speech.json').exists())
        self.assertFalse((self.data / 'media-config.fast-speech.new').exists())

    def test_each_redirect_is_rejected_before_following_an_insecure_or_unapproved_hop(self):
        request = urllib.request.Request('https://huggingface.co/fixed-public-asset')
        handler = provision.SpeechRedirects()
        invalid = ['http://huggingface.co/asset', 'https://localhost/asset', 'https://127.0.0.1/asset',
                   'https://unrelated.example/asset', 'https://huggingface.co.unrelated.example/asset',
                   'https://user:secret@huggingface.co/asset', 'https://hf.co:444/asset',
                   'https://hf.co/asset#fragment', 'file:///C:/unrelated']
        for url in invalid:
            with self.subTest(url=url), patch.object(urllib.request.HTTPRedirectHandler, 'redirect_request') as follow:
                with self.assertRaises(ValueError):
                    handler.redirect_request(request, None, 302, 'Found', {}, url)
                follow.assert_not_called()
        for url in ('https://files.pythonhosted.org/package.whl', 'https://raw.githubusercontent.com/notice',
                    'https://huggingface.co/asset', 'https://cas-bridge.xethub.hf.co/asset?signature=public',
                    'https://cdn-lfs.huggingface.co/asset', 'https://hf.co:443/asset'):
            with self.subTest(url=url), patch.object(urllib.request.HTTPRedirectHandler, 'redirect_request', return_value='allowed') as follow:
                self.assertEqual(handler.redirect_request(request, None, 302, 'Found', {}, url), 'allowed')
                follow.assert_called_once()

    def test_changed_cached_bytes_are_rejected_without_download_or_overwrite(self):
        item = self.value['assets'][0]
        target = self.root / item['path']
        changed = b'X' * item['bytes']
        target.write_bytes(changed)
        with patch.object(provision.urllib.request, 'build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'accepted pin'):
                provision.fetch(item)
        opener.assert_not_called()
        self.assertEqual(target.read_bytes(), changed)

    def test_prior_partial_is_preserved_without_a_new_request(self):
        item = self.value['assets'][0]
        target = self.root / item['path']
        target.unlink()
        partial = Path(str(target) + '.partial')
        partial.write_bytes(b'Preserved failed download.')
        with patch.object(provision.urllib.request, 'build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'partial is preserved'):
                provision.fetch(item)
        opener.assert_not_called()
        self.assertEqual(partial.read_bytes(), b'Preserved failed download.')
        self.assertFalse(target.exists())

    def test_successful_mock_fetch_uses_verified_tls_no_proxy_fixed_redirects_and_pinned_bytes(self):
        item = self.value['assets'][0]
        target = self.root / item['path']
        payload = target.read_bytes()
        target.unlink()
        class Response(io.BytesIO):
            def geturl(self):
                return 'https://files.pythonhosted.org/fixed-package.whl'
        opener = Mock()
        opener.open.return_value = Response(payload)
        with patch.object(provision.urllib.request, 'build_opener', return_value=opener) as builder:
            provision.fetch(item)
        opener.open.assert_called_once_with(item['url'], timeout=45)
        handlers = builder.call_args.args
        self.assertTrue(any(isinstance(handler, provision.SpeechRedirects) for handler in handlers))
        self.assertTrue(any(isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {} for handler in handlers))
        contexts = [handler._context for handler in handlers if isinstance(handler, urllib.request.HTTPSHandler)]
        self.assertEqual(len(contexts), 1)
        self.assertTrue(contexts[0].check_hostname)
        self.assertEqual(contexts[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(target.read_bytes(), payload)
        self.assertFalse(Path(str(target) + '.partial').exists())

    def test_existing_catalog_or_cache_with_multiple_links_is_rejected_before_open(self):
        linked = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_nlink=2, st_file_attributes=0)
        for target in (self.catalog_path, self.root / self.value['assets'][0]['path']):
            with self.subTest(target=target.name), patch.object(provision, '_allowed_path', return_value=target), \
                 patch.object(Path, 'lstat', return_value=linked):
                with self.assertRaisesRegex(ValueError, 'unlinked'):
                    provision.regular(target)

    def test_catalog_rejects_duplicate_destination_invalid_pin_and_escaping_paths(self):
        invalid = []
        duplicate = copy.deepcopy(self.value)
        duplicate['assets'][1]['path'] = duplicate['assets'][0]['path']
        invalid.append(duplicate)
        escaped = copy.deepcopy(self.value)
        escaped['assets'][0]['path'] = 'data/not-a-speech-asset.whl'
        invalid.append(escaped)
        malformed = copy.deepcopy(self.value)
        malformed['assets'][0]['sha256'] = 'not-a-pin'
        invalid.append(malformed)
        for value in invalid:
            self.catalog_path.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError):
                provision.catalog()

    def existing_install(self):
        target = self.root / 'runtime/media/piper-python'
        (target / 'piper').mkdir(parents=True)
        (target / 'piper/__init__.py').write_text('# synthetic trusted package', encoding='utf-8')
        manifest = self.root / 'runtime/media/piper/INSTALL-MANIFEST.json'
        manifest.write_text(json.dumps({'schema': 1,
            'catalog_sha256': hashlib.sha256(self.catalog_path.read_bytes()).hexdigest(),
            'packages': {'piper-tts': '1.8.0', 'pathvalidate': '3.3.1'},
            'files': provision.package_files(target)}), encoding='utf-8')
        return target

    def test_existing_matching_install_is_verified_without_pip_and_changed_install_is_preserved(self):
        target = self.existing_install()
        with patch.object(provision.sys, 'platform', 'win32'), patch.object(provision.subprocess, 'run') as pip:
            provision.install(self.value)
            foreign = target / 'piper/foreign.dll'
            foreign.write_bytes(b'Unreviewed synthetic change.')
            with self.assertRaisesRegex(ValueError, 'no overwrite'):
                provision.install(self.value)
        pip.assert_not_called()
        self.assertEqual(foreign.read_bytes(), b'Unreviewed synthetic change.')

    def test_new_install_is_hash_locked_offline_isolated_and_uses_only_explicit_sanitized_environment(self):
        target = self.root / 'runtime/media/piper-python'
        environment = {'PATH': 'fixed-runtime-path', 'TEMP': str(self.root / 'temporary')}
        def fake_pip(*_args, **_kwargs):
            target.mkdir(parents=True)
            (target / 'piper.py').write_text('# synthetic installed package', encoding='utf-8')
        with patch.object(provision.sys, 'platform', 'win32'), \
             patch('ariadne.worker._environment', return_value=environment) as sanitized, \
             patch.object(provision.subprocess, 'run', side_effect=fake_pip) as pip:
            provision.install(self.value)
        sanitized.assert_called_once_with(None)
        command = pip.call_args.args[0]
        for flag in ('-I', '-B', '--isolated', '--no-index', '--no-deps', '--no-compile', '--require-hashes', '--no-input'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--target') + 1], str(target))
        self.assertEqual(pip.call_args.kwargs['env'], environment)
        self.assertEqual(pip.call_args.kwargs['timeout'], 120)
        self.assertIs(pip.call_args.kwargs['check'], True)
        saved = json.loads((self.root / 'runtime/media/piper/INSTALL-MANIFEST.json').read_bytes())
        self.assertEqual(saved['files'], provision.package_files(target))

    def test_failed_install_preserves_partial_files_without_publishing_success_manifest(self):
        target = self.root / 'runtime/media/piper-python'
        def fail(*_args, **_kwargs):
            target.mkdir(parents=True)
            (target / 'partial.py').write_text('# preserved failed install', encoding='utf-8')
            raise subprocess.CalledProcessError(1, ['fixed', 'synthetic', 'pip'])
        with patch.object(provision.sys, 'platform', 'win32'), \
             patch('ariadne.worker._environment', return_value={'PATH': 'fixed'}), \
             patch.object(provision.subprocess, 'run', side_effect=fail) as pip:
            with self.assertRaises(subprocess.CalledProcessError):
                provision.install(self.value)
        pip.assert_called_once()
        self.assertTrue((target / 'partial.py').is_file())
        self.assertFalse((self.root / 'runtime/media/piper/INSTALL-MANIFEST.json').exists())


if __name__ == '__main__':
    unittest.main()
