"""Typed provenance and bounded outputs must be real file predicates."""
from pathlib import Path
import json
import tempfile
import unittest

from ariadne.config import Settings
from ariadne.tools import ToolRegistry
from ariadne.verification import validate_checks, verify


class ProjectKitVerificationTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1]/'runtime/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='kit-predicates-', dir=parent)
        self.addCleanup(temp.cleanup)
        self.registry = ToolRegistry(Settings(data_dir=Path(temp.name)/'data'))

    def check(self, raw, expected, pointer=''):
        target = self.registry._path('value.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw.encode('utf8'))
        checks = validate_checks([{'kind':'json_value', 'path':'value.json', 'pointer':pointer, 'equals':expected}], self.registry)
        return verify(checks, self.registry)

    def test_source_hash_is_selected_by_exact_pointer_not_elsewhere_in_file(self):
        fingerprint = 'a'*64
        self.assertEqual(self.check(json.dumps({'source_sha256':fingerprint}), fingerprint, '/source_sha256')['status'], 'passed')
        self.assertEqual(self.check(json.dumps({'source_sha256':'b'*64, 'irrelevant':fingerprint}), fingerprint, '/source_sha256')['status'], 'failed')

    def test_booleans_do_not_coerce_numbers_strings_or_null(self):
        for raw, expected in [('true',True), ('false',False), ('null',None), ('1',1), ('"1"','1')]:
            with self.subTest(raw=raw): self.assertEqual(self.check(raw,expected)['status'],'passed')
        for raw, expected in [('1',True),('true',1),('"false"',False),('null',False),('1.0',1),('1e0',1),('"1"',1),('[]',None),('{}',None)]:
            with self.subTest(raw=raw,expected=expected): self.assertEqual(self.check(raw,expected)['status'],'failed')

    def test_pointer_escaping_empty_key_and_arrays(self):
        self.assertEqual(self.check('{"a/b":{"~key":[false]}}',False,'/a~1b/~0key/0')['status'],'passed')
        self.assertEqual(self.check('{"":null}',None,'/')['status'],'passed')
        self.assertEqual(self.check('{"~1":"literal"}','literal','/~01')['status'],'passed')
        for pointer in ('/01','/-','/999999999','/1','/missing'):
            with self.subTest(pointer=pointer): self.assertEqual(self.check('[true]',True,pointer)['status'],'failed')

    def test_invalid_expected_values_and_pointer_bounds_rejected(self):
        for value in ([],{},1.0,float('nan'),2**53,-2**53,'x'*4001):
            with self.subTest(value=repr(value)[:40]), self.assertRaises(ValueError):
                validate_checks([{'kind':'json_value','path':'value.json','pointer':'','equals':value}],self.registry)
        for pointer in (None,False,'missing','/~2','/~','/x'*33,'/'+'x'*500):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                validate_checks([{'kind':'json_value','path':'value.json','pointer':pointer,'equals':True}],self.registry)

    def test_safe_integer_extremes_and_unicode_are_exact(self):
        for value in (2**53-1,-(2**53-1),'é','e\u0301',''):
            with self.subTest(value=value): self.assertEqual(self.check(json.dumps(value),value)['status'],'passed')
        self.assertEqual(self.check('"é"','e\u0301')['status'],'failed')

    def test_duplicate_keys_nonfinite_and_invalid_json_fail(self):
        for raw in ('{"x":false,"x":true}','{"x":NaN}','{"x":Infinity}','{"x":true,}', '{"x":1e9999}'):
            with self.subTest(raw=raw): self.assertEqual(self.check(raw,True,'/x')['status'],'failed')

    def test_size_limit_counts_utf8_bytes_and_exact_boundary(self):
        path=self.registry._path('text.md');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes('éé'.encode())
        for limit,status in ((4,'passed'),(3,'failed')):
            checks=validate_checks([{'kind':'file_size','path':'text.md','max_bytes':limit}],self.registry)
            result=verify(checks,self.registry)
            self.assertEqual(result['status'],status);self.assertEqual(result['artifacts'][0]['size'],4)
        for limit in (False,True,0,-1,1.0,'10',10_000_001,None):
            with self.subTest(limit=limit),self.assertRaises(ValueError):
                validate_checks([{'kind':'file_size','path':'text.md','max_bytes':limit}],self.registry)

    def test_provenance_and_size_both_must_pass_same_artifact(self):
        path=self.registry._path('draft.json');path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{"draft":true,"commercial_authority":false,"source_sha256":"abc"}')
        checks=validate_checks([
            {'kind':'file_size','path':'draft.json','max_bytes':2500},
            {'kind':'json_value','path':'draft.json','pointer':'/draft','equals':True},
            {'kind':'json_value','path':'draft.json','pointer':'/commercial_authority','equals':False},
            {'kind':'json_value','path':'draft.json','pointer':'/source_sha256','equals':'abc'}], self.registry)
        result=verify(checks,self.registry)
        self.assertEqual(result['status'],'passed');self.assertEqual(len(result['artifacts']),1)
        path.write_text('{"draft":true,"commercial_authority":true,"source_sha256":"abc"}')
        self.assertEqual(verify(checks,self.registry)['status'],'failed')

    def test_new_check_shapes_are_strict_and_paths_remain_guarded(self):
        for check in ({'kind':'file_size','path':'value.json'}, {'kind':'json_value','path':'value.json','pointer':''},
                      {'kind':'json_value','path':'value.json','pointer':'','equals':None,'extra':True},
                      {'kind':'file_size','path':'../escape','max_bytes':1}):
            with self.subTest(check=check),self.assertRaises(ValueError): validate_checks([check],self.registry)

    def shape(self, value, fields):
        target=self.registry._path('shape.json');target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(value),encoding='utf8')
        checks=validate_checks([{'kind':'json_shape','path':'shape.json','fields':fields}],self.registry)
        return verify(checks,self.registry)['status']

    def test_shape_rejects_metadata_only_drafts_missing_the_actual_work(self):
        fields={'source_sha256':'string','draft':'boolean','commercial_authority':'boolean','scope':'string','source_facts':'nonempty_strings','unknowns':'strings','proposed_checklist':'nonempty_strings'}
        value={'source_sha256':'a'*64,'draft':True,'commercial_authority':False}
        self.assertEqual(self.shape(value,fields),'failed')
        value.update(scope='Fictional draft, not an approved job.',source_facts=['Owner supplied a fictional request.'],unknowns=[],proposed_checklist=['Ask the owner to review missing facts.'])
        self.assertEqual(self.shape(value,fields),'passed')
        self.assertEqual(self.shape({**value,'unreviewed_extra':'authority'},fields),'failed')
        for field,patches in (('scope',['',' ',1,'x'*4001]),('source_facts',[[],[None],[' '],['x']*33]),('draft',[1,'true',None])):
            for patch in patches:
                with self.subTest(field=field,patch=repr(patch)[:30]):self.assertEqual(self.shape({**value,field:patch},fields),'failed')

    def test_shape_enforces_scalar_and_array_types_without_coercion(self):
        fields={'s':'string','b':'boolean','n':'integer','z':'null','a':'strings','r':'nonempty_strings'}
        value={'s':'é','b':False,'n':2**53-1,'z':None,'a':[],'r':['evidence']}
        self.assertEqual(self.shape(value,fields),'passed')
        for field,wrong in [('n',True),('n',1.0),('n',2**53),('z',False),('a','not a list'),('r',[]),('r',[['nested']])]:
            with self.subTest(field=field,wrong=wrong):self.assertEqual(self.shape({**value,field:wrong},fields),'failed')
        for value in ([],False,None,'text'):
            with self.subTest(value=value):self.assertEqual(self.shape(value,fields),'failed')

    def test_shape_spec_is_bounded_local_data_without_schema_execution(self):
        for fields in ({}, {'x':{'$ref':'https://example.invalid'}}, {'$ref':'string'}, {'x':'object'}, {'x'*81:'string'}, {str(i):'string' for i in range(17)}, []):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                validate_checks([{'kind':'json_shape','path':'shape.json','fields':fields}],self.registry)
