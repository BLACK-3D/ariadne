"""Synthetic offline robustness-runner validation; never runs a market study."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne import options_experiments as engine, options_robustness as audit
from scripts import options_robustness as cli

NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)


def encoded(value):
    return cli._canonical(value)


def receipt(kind='synthetic_offline_research'):
    return {'schema': 1, 'status': 'completed', 'kind': kind, 'source_stable': True,
        'automatic_adoption': False, 'execution_qualified': False, 'paper_operations': 0,
        'model_calls': 0, 'artifacts': {'quotes.json': {'bytes': 2, 'sha256': cli.sha(b'{}')}}}


def intent():
    return {'schema': 1, 'kind': 'finite_options_robustness_intent',
        'configurations': engine.configs(), 'paths': 712, 'max_seconds': cli.MAX_SECONDS,
        'max_output_bytes': cli.MAX_OUTPUT, 'engine_rule_sha256': engine.RULE_SHA256,
        'clock_profiles': list(cli.prior.PROFILES),
        'scenarios': {name: 'Synthetic fixed scenario description: ' + name for name in cli.SCENARIO_MAP}}


class TemporaryBox(unittest.TestCase):
    def setUp(self):
        parent = ROOT / 'runtime/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='robustness-cli-', dir=parent)
        self.box = Path(self.temporary.name)
        self.guard = cli._root_guard(self.box)

    def tearDown(self):
        self.temporary.cleanup()


class InventoryTests(TemporaryBox):
    def check_receipt(self, value, maximum=1024, expected=None, kind='synthetic_offline_research'):
        raw = encoded(value)
        return cli.inventory(self.guard, raw, cli.sha(raw) if expected is None else expected, kind, maximum)

    def test_completed_metadata_is_accepted_without_opening_any_price_body(self):
        value = receipt()
        with patch.object(cli, 'read', side_effect=AssertionError('No source body read')):
            self.assertEqual(self.check_receipt(value), value)
        self.assertFalse((self.box/'quotes.json').exists())

    def test_exact_receipt_integer_types_not_bool_or_float(self):
        for key, bads in {'schema': (True, 1.0, '1'), 'paper_operations': (False, 0.0, '0'),
                          'model_calls': (False, 0.0, '0')}.items():
            for bad in bads:
                value = receipt(); value[key] = bad
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError): self.check_receipt(value)

    def test_completed_hash_kind_and_research_only_flags_are_mandatory(self):
        with self.assertRaises(ValueError): self.check_receipt(receipt(), expected='0'*64)
        for key, value in (('status', 'failed_or_interrupted'), ('kind', 'different_source'),
                           ('source_stable', False), ('automatic_adoption', True),
                           ('execution_qualified', True), ('paper_operations', 1), ('model_calls', 1)):
            item = receipt(); item[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.check_receipt(item)

    def test_artifact_size_hash_schema_and_aggregate_budget_are_strict(self):
        for bad in (True, 2.0, 0, -1, 1025):
            value = receipt(); value['artifacts']['quotes.json']['bytes'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError): self.check_receipt(value)
        for bad in ('A'*64, 'not-a-digest', None, 123):
            value = receipt(); value['artifacts']['quotes.json']['sha256'] = bad
            with self.subTest(hash=bad), self.assertRaises(ValueError): self.check_receipt(value)
        value = receipt(); value['artifacts']['quotes.json']['extra'] = 'not allowed'
        with self.assertRaises(ValueError): self.check_receipt(value)
        value = receipt(); value['artifacts']['more.json'] = {'bytes': 2, 'sha256': cli.sha(b'[]')}
        with self.assertRaises(ValueError): self.check_receipt(value, maximum=3)

    def test_artifact_paths_case_aliases_backslashes_and_traversal_are_refused(self):
        for name in ('../outside.json', '/outside.json', 'nested\\quotes.json', 'nested/../quotes.json', 'quotes.json:stream'):
            value = receipt(); value['artifacts'] = {name: next(iter(value['artifacts'].values()))}
            with self.subTest(name=name), self.assertRaises(ValueError): self.check_receipt(value)
        value = receipt(); value['artifacts']['QUOTES.JSON'] = deepcopy(value['artifacts']['quotes.json'])
        with self.assertRaises(ValueError): self.check_receipt(value)

    def test_duplicate_and_nonfinite_json_cannot_enter_source_or_intent(self):
        for raw in (b'{"schema":1,"schema":1}', b'{"value":NaN}', b'{"value":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): cli.strict_json(raw)


class FrozenIntentTests(unittest.TestCase):
    def check_intent(self, value, expected=None):
        raw = encoded(value)
        return cli.validate_intent(raw, cli.sha(raw) if expected is None else expected)

    def test_exact_grid_clocks_limits_scenario_map_and_hash_are_accepted(self):
        value = intent()
        self.assertEqual(self.check_intent(value), value)
        self.assertEqual(set(cli.SCENARIO_MAP.values()), set(audit.SCENARIOS))
        self.assertEqual(len(value['configurations']), 89)
        with self.assertRaises(ValueError): self.check_intent(value, expected='0'*64)

    def test_intent_numeric_metadata_rejects_bool_float_and_changed_limits(self):
        for key, wrong in (('schema', True), ('schema', 1.0), ('paths', 712.0),
                            ('max_seconds', 600.0), ('max_output_bytes', float(cli.MAX_OUTPUT)),
                            ('paths', 713), ('max_seconds', 601), ('max_output_bytes', cli.MAX_OUTPUT+1)):
            value = intent(); value[key] = wrong
            with self.subTest(key=key, wrong=wrong), self.assertRaises(ValueError): self.check_intent(value)

    def test_intent_grid_order_types_clock_set_and_engine_binding_are_frozen(self):
        for defect in ('config_type', 'config_count', 'config_order', 'clock', 'engine', 'kind'):
            value = intent()
            if defect == 'config_type': value['configurations'][0]['max_contracts'] = True
            if defect == 'config_count': value['configurations'].pop()
            if defect == 'config_order': value['configurations'].reverse()
            if defect == 'clock': value['clock_profiles'].reverse()
            if defect == 'engine': value['engine_rule_sha256'] = '0'*64
            if defect == 'kind': value['kind'] = 'other_kind'
            with self.subTest(defect=defect), self.assertRaises(ValueError): self.check_intent(value)

    def test_scenarios_require_exact_alias_dict_and_nonempty_string_descriptions(self):
        for wrong in (list(cli.SCENARIO_MAP), {**intent()['scenarios'], 'extra': 'extra'},
                      {key: '' for key in cli.SCENARIO_MAP}, {key: True for key in cli.SCENARIO_MAP}):
            value = intent(); value['scenarios'] = wrong
            with self.subTest(wrong=wrong), self.assertRaises(ValueError): self.check_intent(value)

    def test_witness_identity_accepts_only_registered_config_and_scenario(self):
        original = engine.configs()[0]
        self.assertEqual(cli.witness_identity(cli.prior.PROFILES[0], 'recorded_base', original), 'cash')
        for key, value in (('id', '../escape'), ('max_contracts', True), ('max_hold', 5.0)):
            item = deepcopy(original); item[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                cli.witness_identity(cli.prior.PROFILES[0], 'recorded_base', item)
        for profile, scenario in (('../escape', 'recorded_base'), (cli.prior.PROFILES[0], 'adverse_cent'),
                                  (cli.prior.PROFILES[0], 'modeled_after_outcomes')):
            with self.subTest(profile=profile, scenario=scenario), self.assertRaises(ValueError):
                cli.witness_identity(profile, scenario, original)
        self.assertEqual(original, engine.configs()[0])


class EarlyRunnerTests(TemporaryBox):
    def setUp(self):
        super().setUp()
        self.authorities, self.read_names = [], []
        self.owner_bytes = b'SYNTHETIC OWNER SENTINEL - NO DATABASE OPEN'
        (self.box/'data').mkdir(); (self.box/'data/ariadne.sqlite3').write_bytes(self.owner_bytes)
        for name in cli.SOURCE_FILES:
            target = self.box/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)
        self.capture, self.baseline = self.box/'captured', self.box/'baseline'
        self.capture.mkdir(); self.baseline.mkdir()
        source = receipt('fixed_KO_historical_nullable_sizes_reference_research')
        source['source_records'] = 125; source['artifacts'] = {}; source['outputs'] = {}
        for profile in cli.prior.PROFILES:
            name = profile+'/catalog.json'; raw = b'{}'
            target = self.capture/name; target.parent.mkdir(parents=True); target.write_bytes(raw)
            source['artifacts'][name] = {'bytes': len(raw), 'sha256': cli.sha(raw)}
            source['outputs'][profile] = {'catalog_path': name, 'catalog_sha256': cli.sha(raw)}
        baseline = receipt('fixed_KO_causal_experiments')
        baseline.update(configuration_count=89, clock_profiles=list(cli.prior.PROFILES))
        baseline['artifacts'] = {}
        for name in ('ariadne/options_experiments.py', 'ariadne/options_experiment_signals.py'):
            raw = (self.box/name).read_bytes(); relative = 'source_snapshot/'+name.replace('/', '_')
            target = self.baseline/relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
            baseline['artifacts'][relative] = {'bytes': len(raw), 'sha256': cli.sha(raw)}
        self.source_value, self.baseline_value = source, baseline
        self.source_raw, self.baseline_raw = encoded(source), encoded(baseline)
        (self.capture/'completed.json').write_bytes(self.source_raw)
        (self.baseline/'completed.json').write_bytes(self.baseline_raw)
        self.intent_value = intent(); raw = encoded(self.intent_value); (self.box/'intent.json').write_bytes(raw)
        self.args = cli.parser().parse_args(['--source-workspace', 'captured', '--source-completed-sha256', cli.sha(self.source_raw),
            '--prior-workspace', 'baseline', '--prior-completed-sha256', cli.sha(self.baseline_raw),
            '--intent', 'intent.json', '--intent-sha256', cli.sha(raw), '--output', 'staging/synthetic-study'])

    def tearDown(self):
        try:
            self.assertEqual((self.box/'data/ariadne.sqlite3').read_bytes(), self.owner_bytes)
            self.assertTrue(all(authority.closed for authority in self.authorities))
        finally:
            super().tearDown()

    @property
    def output(self):
        return self.box/self.args.output

    def authority(self, box, owner_data, detached):
        class Authority:
            closed = False
            def check(self): raise PermissionError('SYNTHETIC_PRIVATE_EXCEPTION_MUST_NOT_LEAK')
            def scope(self): return {'mode': 'synthetic_readonly'}
            def close(self): self.closed = True
        authority = Authority(); self.authorities.append(authority); return authority

    def invoke(self):
        from ariadne import options_archive, research_window, options_regime
        original_read = cli.read
        def tracked(guard, name, limit):
            self.read_names.append(name); return original_read(guard, name, limit)
        manifest = {'symbol': 'KO', 'selection': {'start': cli.prior.START, 'end': cli.prior.END}, 'chunks': []}
        with patch('sqlite3.connect', side_effect=AssertionError('No owner database access')), \
             patch('socket.socket', side_effect=AssertionError('No networking')), \
             patch.object(cli, 'read', side_effect=tracked), \
             patch.object(options_archive, 'load_manifest', return_value=(b'{}', manifest)), \
             patch.object(research_window, 'require_archive_manifest'), \
             patch.object(options_archive, 'iter_archive', side_effect=AssertionError('No price iteration')), \
             patch.object(options_regime, 'build_timeline', side_effect=AssertionError('No market context build')), \
             patch.object(audit, 'audit', side_effect=AssertionError('No study execution')):
            return cli.run(self.args, root=self.box, now=NOW, _authority=self.authority)

    def test_pause_leaves_redacted_durable_failure_and_no_replay(self):
        with self.assertRaisesRegex(ValueError, 'no automatic replay'): self.invoke()
        attempt = (self.output/'attempt.json').read_bytes()
        failed = (self.output/'failed.json').read_bytes(); receipt = json.loads(failed)
        self.assertFalse(receipt['replay_allowed']); self.assertFalse(receipt['automatic_retry'])
        self.assertEqual(receipt['phase'], 'registration')
        self.assertNotIn(b'SYNTHETIC_PRIVATE_EXCEPTION', failed)
        self.assertFalse((self.output/'completed.json').exists())
        self.assertFalse((self.output/'registration.json').exists())
        before = {path.name: path.read_bytes() for path in self.output.iterdir()}
        with self.assertRaises(FileExistsError): self.invoke()
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.output.iterdir()})
        self.assertEqual((self.output/'attempt.json').read_bytes(), attempt)
        self.assertEqual(len(self.authorities), 1)
        self.assertFalse(any(name.endswith('catalog.json') for name in self.read_names))

    def test_new_output_must_be_private_staging_before_source_body_reads(self):
        for output in ('data/research', 'staging', '../outside', 'staging\\unsafe', '/outside'):
            self.args.output = output
            with self.subTest(output=output), self.assertRaises(ValueError): self.invoke()
        self.assertEqual(self.read_names, []); self.assertEqual(self.authorities, [])

    def test_existing_output_is_never_adopted_or_overwritten(self):
        self.output.mkdir(parents=True); marker = self.output/'existing.txt'; marker.write_bytes(b'KEEP')
        with self.assertRaises(FileExistsError): self.invoke()
        self.assertEqual(marker.read_bytes(), b'KEEP')
        self.assertFalse((self.output/'attempt.json').exists()); self.assertEqual(self.authorities, [])

    def test_wrong_completed_hash_and_float_cohort_count_refuse_before_output(self):
        self.args.source_completed_sha256 = '0'*64
        with self.assertRaises(ValueError): self.invoke()
        self.assertFalse(self.output.exists()); self.assertEqual(self.authorities, [])
        self.source_value['source_records'] = 125.0
        raw = encoded(self.source_value); (self.capture/'completed.json').write_bytes(raw)
        self.args.source_completed_sha256 = cli.sha(raw)
        with self.assertRaises(ValueError): self.invoke()
        self.assertFalse(self.output.exists()); self.assertEqual(self.authorities, [])

    def test_deadline_stops_before_authority_or_market_iteration(self):
        with patch.object(cli.time, 'monotonic', side_effect=(0, cli.MAX_SECONDS+1)):
            with self.assertRaisesRegex(ValueError, 'no automatic replay'): self.invoke()
        failure = json.loads((self.output/'failed.json').read_bytes())
        self.assertEqual(failure['phase'], 'registration'); self.assertFalse(failure['automatic_retry'])
        self.assertFalse((self.output/'completed.json').exists())


if __name__ == '__main__': unittest.main()
