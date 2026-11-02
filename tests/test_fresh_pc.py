"""Fresh-PC inventory and explicit setup authority; synthetic private files only.

Native no-external-Python bootstrap/relocation evidence is recorded separately
under staging/fresh_pc_2026_11_02. These tests never install or download assets,
probe live ports, initialize owner databases, or execute a model.
"""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ariadne.config import _allowed_path
from scripts import provision, setup_windows as setup

ROOT = Path(__file__).absolute().parents[1]
BUNDLED = 'runtime/python/python.exe'
LEGACY = '.venv/Scripts/python.exe'
RICH_PROFILE = (json.dumps({'schema':1,'default_model':'qwen3.5:9b','factory_local_flows':True,
    'profiles':{'voice-config.json':{'whisper_bin':'${BOX}/runtime/whisper/whisper-cli.exe',
                                    'whisper_model':'${BOX}/data/voice/ggml-base.en.bin'}}},
    sort_keys=True)+'\n').encode('utf8')


class FreshPCInventoryTests(unittest.TestCase):
    def setUp(self):
        parent = _allowed_path(ROOT/'staging/fresh_pc_2026_11_02/unit-fixtures')
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='fresh-', dir=parent)
        self.root = Path(self.temporary.name)
        def cleanup():
            if not _allowed_path(self.root).is_relative_to(ROOT):
                raise ValueError('Refuse cleanup outside the generated fixture.')
            self.temporary.cleanup()
        self.addCleanup(cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(setup, 'ROOT', self.root))
        self.stack.enter_context(patch.object(setup, '_platform_ok', return_value=True))
        self.stack.enter_context(patch.object(setup, '_python_ok', return_value=True))
        self.stack.enter_context(patch.object(setup, '_module_available', return_value=True))
        env = {name:value for name,value in os.environ.items()
               if name not in ('ARIADNE_MODEL_PORT','ARIADNE_OLLAMA_URL','ARIADNE_DATA_DIR')}
        self.stack.enter_context(patch.dict(os.environ, env, clear=True))

    def asset(self, name, payload=b'synthetic-not-an-executable'):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def tree(self):
        return {path.relative_to(self.root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob('*') if path.is_file()}

    def invoke(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = setup.main(list(arguments))
        return result, output.getvalue(), errors.getvalue()

    def test_bundled_interpreter_needs_no_legacy_virtual_environment(self):
        self.asset(BUNDLED)
        state = setup.inventory()
        self.assertEqual(state['python_mode'], 'bundled')
        self.assertEqual(state['status'], 'ready_for_setup')
        self.assertEqual(state['prerequisites_missing'], [])
        self.assertIn(BUNDLED, state['existing'])
        self.assertFalse((self.root/'.venv').exists())
        self.assertFalse(state['downloads'])
        self.assertFalse(state['services_started'])

    def test_complete_core_presence_says_ready_to_start_without_claiming_execution(self):
        self.asset(BUNDLED)
        self.asset('runtime/ollama/ollama.exe')
        self.asset('data/models/manifests/registry.ollama.ai/library/qwen3.5/9b')
        with patch.object(setup, '_listener_present', side_effect=AssertionError('No live qualification')):
            state = setup.inventory()
        self.assertEqual(state['python_mode'],'bundled')
        self.assertEqual(state['status'],'ready_to_start')
        self.assertEqual(state['prerequisites_missing'],[])
        self.assertIn('presence only',state['qualification'])
        self.assertFalse(state['downloads'])
        self.assertFalse(state['services_started'])
        self.assertFalse((self.root/'.venv').exists())

    def test_bundled_runtime_wins_over_an_existing_legacy_interpreter(self):
        self.asset(BUNDLED)
        self.asset(LEGACY)
        state = setup.inventory()
        self.assertEqual(state['python_mode'], 'bundled')
        self.assertEqual(state['prerequisites_missing'], [])

    def test_legacy_virtual_environment_remains_an_explicit_compatibility_mode(self):
        self.asset(LEGACY)
        state = setup.inventory()
        self.assertEqual(state['python_mode'], 'venv')
        self.assertEqual(state['status'], 'ready_for_setup')
        self.assertIn(LEGACY, state['existing'])
        self.assertEqual(state['prerequisites_missing'], [])

    def test_missing_private_python_cannot_be_satisfied_by_callers_path(self):
        before = self.tree()
        with patch.dict(os.environ, {'PATH':str(self.root/'pretend-global-python')}, clear=False), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('Do not discover global executables')):
            state = setup.inventory()
        self.assertEqual(state['python_mode'], 'missing')
        self.assertEqual(state['status'], 'prerequisites_missing')
        self.assertTrue(any('python.exe' in name or name=='missing_python' for name in state['prerequisites_missing']))
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.root/'data').exists())

    def test_module_readiness_is_separate_from_private_interpreter_presence(self):
        self.asset(BUNDLED)
        with patch.object(setup, '_module_available', return_value=False):
            state = setup.inventory()
        self.assertEqual(state['python_mode'], 'bundled')
        self.assertEqual(state['status'], 'prerequisites_missing')
        self.assertEqual(state['prerequisites_missing'], ['waitress','pypdf'])

    def test_bundled_check_has_no_write_port_process_or_database_effect(self):
        self.asset(BUNDLED)
        before = self.tree()
        with ExitStack() as forbidden:
            for target in ((setup,'_listener_present'),(provision,'main'),(setup.socket,'socket'),
                           (subprocess,'Popen'),(sqlite3,'connect'),(Path,'mkdir'),
                           (Path,'write_bytes'),(Path,'write_text')):
                forbidden.enter_context(patch.object(*target, side_effect=AssertionError('Read-only inventory effect')))
            code, output, errors = self.invoke('--check')
        state = json.loads(output)
        self.assertEqual((code, errors), (0,''))
        self.assertEqual(state['python_mode'], 'bundled')
        self.assertFalse(state['downloads'])
        self.assertFalse(state['services_started'])
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.root/'runtime/python/PORTABLE-MANIFEST.json').exists())
        self.assertFalse((self.root/'runtime/SETUP-READY.json').exists())
        self.assertFalse((self.root/'data').exists())

    def test_existing_owner_state_remains_visible_in_read_only_bundled_check(self):
        self.asset(BUNDLED)
        marker = self.asset('data/server.token', b'SYNTHETIC_OWNER_MARKER')
        before = self.tree()
        code, output, errors = self.invoke('--check')
        state = json.loads(output)
        self.assertEqual((code, errors),(0,''))
        self.assertEqual(state['status'],'existing_installation')
        self.assertTrue(state['private_installation_present'])
        self.assertEqual(state['python_mode'],'bundled')
        self.assertEqual(state['owner_markers'],['data/server.token'])
        self.assertEqual(marker.read_bytes(),b'SYNTHETIC_OWNER_MARKER')
        self.assertEqual(self.tree(),before)

    def test_existing_owner_state_refuses_normal_setup_before_provision_or_port_probe(self):
        self.asset(BUNDLED)
        self.asset('data/server.token', b'SYNTHETIC_OWNER_MARKER')
        before = self.tree()
        with patch.object(setup, '_listener_present', side_effect=AssertionError('No port probe')), \
             patch.object(provision, 'main', side_effect=AssertionError('No provisioning')):
            code, _, errors = self.invoke()
        self.assertEqual(code,1)
        self.assertIn('Existing private installation',errors)
        self.assertEqual(self.tree(),before)

    def test_existing_owner_state_refuses_explicit_resealing_without_side_effect(self):
        self.asset(BUNDLED)
        self.asset('data/server.token', b'SYNTHETIC_OWNER_MARKER')
        before = self.tree()
        with patch.object(setup, '_listener_present', side_effect=AssertionError('No port probe')), \
             patch.object(provision, 'main', side_effect=AssertionError('No provisioning')):
            code, _, errors = self.invoke('--seal-runtime')
        self.assertEqual(code,1)
        self.assertIn('Existing private installation',errors)
        self.assertEqual(self.tree(),before)


    def child(self):
        child = MagicMock(spec=subprocess.Popen)
        child.poll.return_value = None
        child.wait.side_effect = lambda timeout: setattr(child.poll, 'return_value', 0) or 0
        return child

    def test_explicit_sealing_dispatches_without_model_port_or_ready_publication(self):
        self.asset(BUNDLED)
        with patch.object(setup, 'seal_runtime', return_value={'schema':1}) as seal, \
             patch.object(setup, 'publish_ready', side_effect=AssertionError('Sealing is not model readiness')), \
             patch.object(setup, '_listener_present', side_effect=AssertionError('No port probe')), \
             patch.object(provision, 'main', side_effect=AssertionError('No model provision')):
            code, _, errors = self.invoke('--seal-runtime')
        self.assertEqual((code,errors),(0,''))
        seal.assert_called_once()
        self.assertFalse((self.root/'data').exists())

    def test_seal_failure_never_provisions_a_model_or_publishes_ready(self):
        self.asset(BUNDLED)
        before = self.tree()
        with patch.object(setup, 'seal_runtime', side_effect=ValueError('Synthetic qualification failure')), \
             patch.object(setup, 'publish_ready') as ready, \
             patch.object(setup, '_listener_present', side_effect=AssertionError('No port probe')), \
             patch.object(provision, 'main', side_effect=AssertionError('No model provision')):
            code, _, errors = self.invoke('--seal-runtime')
        self.assertEqual(code,1)
        self.assertIn('qualification failure',errors)
        ready.assert_not_called()
        self.assertEqual(self.tree(),before)

    def test_success_publishes_ready_only_after_captured_process_has_exited(self):
        self.asset(BUNDLED)
        child = self.child()
        def provision_model(argv, *, on_process, allow_owned):
            self.assertFalse(allow_owned)
            on_process(child)
        def ready_after_cleanup(state):
            self.assertEqual(child.poll(),0)
            self.assertEqual(state['python_mode'],'bundled')
            return {'schema':1}
        with patch.object(setup, '_listener_present', return_value=False), \
             patch.object(provision, 'main', side_effect=provision_model), \
             patch.object(setup, 'publish_ready', side_effect=ready_after_cleanup) as ready:
            code, _, errors = self.invoke()
        self.assertEqual((code,errors),(0,''))
        ready.assert_called_once()
        child.terminate.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=15)
        child.kill.assert_not_called()

    def test_unconfirmed_process_cleanup_cannot_publish_ready(self):
        self.asset(BUNDLED)
        child = self.child()
        child.wait.side_effect = OSError('Synthetic cleanup failure')
        def provision_model(argv, *, on_process, allow_owned):on_process(child)
        with patch.object(setup, '_listener_present', return_value=False), \
             patch.object(provision, 'main', side_effect=provision_model), \
             patch.object(setup, 'publish_ready') as ready:
            code, _, errors = self.invoke()
        self.assertEqual(code,1)
        self.assertIn('cleanup could not be confirmed',errors)
        ready.assert_not_called()
        self.assertFalse((self.root/'runtime/SETUP-READY.json').exists())

    def test_model_provision_failure_cannot_publish_ready_after_cleanup(self):
        self.asset(BUNDLED)
        child = self.child()
        def fail_model(argv, *, on_process, allow_owned):
            on_process(child)
            raise RuntimeError('Synthetic model provision failure')
        with patch.object(setup, '_listener_present', return_value=False), \
             patch.object(provision, 'main', side_effect=fail_model), \
             patch.object(setup, 'publish_ready') as ready:
            code, _, errors = self.invoke()
        self.assertEqual(code,1)
        self.assertIn('provisioning failed',errors)
        self.assertEqual(child.poll(),0)
        ready.assert_not_called()

    def test_readiness_write_failure_returns_failure_after_provisioner_exit(self):
        self.asset(BUNDLED)
        child = self.child()
        def provision_model(argv, *, on_process, allow_owned):on_process(child)
        with patch.object(setup, '_listener_present', return_value=False), \
             patch.object(provision, 'main', side_effect=provision_model), \
             patch.object(setup, 'publish_ready', side_effect=OSError('Synthetic readiness publication failure')):
            code, _, _ = self.invoke()
        self.assertEqual(code,1)
        self.assertEqual(child.poll(),0)
        self.assertFalse((self.root/'data/server.token').exists())


    def test_fresh_existing_public_factory_profile_is_converted_atomically(self):
        from scripts.core_runtime import factory_profile
        original = RICH_PROFILE
        expected = factory_profile()
        self.assertNotEqual(original,expected)
        profile = self.asset('config/box-profiles.json',original)
        keep = self.asset('runtime/untouched-fixture.txt',b'KEEP_SYNTHETIC_RUNTIME_BYTES')
        setup.prepare_factory(root=self.root)
        self.assertEqual(profile.read_bytes(),expected)
        self.assertEqual(keep.read_bytes(),b'KEEP_SYNTHETIC_RUNTIME_BYTES')
        self.assertEqual(list(profile.parent.glob('.core-factory-*.tmp')),[])
        self.assertFalse((self.root/'data').exists())

    def test_factory_conversion_never_replaces_an_existing_owner_profile(self):
        original = RICH_PROFILE
        profile = self.asset('config/box-profiles.json',original)
        self.asset('data/server.token',b'SYNTHETIC_OWNER_MARKER')
        before = self.tree()
        with patch.object(setup.os,'replace',side_effect=AssertionError('No owner replacement')):
            with self.assertRaisesRegex(ValueError,'Existing private installation'):
                setup.prepare_factory(root=self.root)
        self.assertEqual(profile.read_bytes(),original)
        self.assertEqual(self.tree(),before)


if __name__=='__main__':
    unittest.main()
