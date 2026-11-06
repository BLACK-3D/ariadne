"""Fictional full-grid report and isolated publication checks; no market data."""
from collections import Counter
from copy import deepcopy
from decimal import localcontext
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import options_robustness_report as report

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ('assumed_eod_plus_15m','assumed_next_session_0935')
SCENARIOS = ('recorded_base','one_cent_adverse','half_known_depth','combined')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sha(raw):return hashlib.sha256(raw).hexdigest()


def fixture():
    ids=['cash']+['candidate_'+str(i).zfill(3) for i in range(1,89)]
    aggregates=[];cases=[]
    for index,identifier in enumerate(ids):
        held=index==2
        aggregates.append({'config':{'id':identifier},'recorded_base_worst_terminal_pnl':'0' if index==0 else '125.25',
            'worst_terminal_pnl':None if held else '0' if index==0 else '100.25',
            'minimum_closed_episodes':0 if index==0 else 4,
            'minimum_closed_exit_months':0 if index==0 else 2,'all_cases_complete':not held,
            'all_nav_known':index not in (2,3), 'minimum_conservative_equity_floor':'24000',
            'loss_budget_screen_passed':True,'worst_terminal_pnl_without_largest_2_positive':'-12.25'})
        for profile in PROFILES:
            for scenario in SCENARIOS:
                gap=index in (2,3)
                cases.append({'config_id':identifier,'clock_profile':profile,'scenario':scenario,
                    'status':'held' if held else 'complete_flat',
                    'terminal_pnl':None if held else '0' if index==0 else '100.25',
                    'entries':0 if index==0 else 4,'closed_episodes':0 if index==0 else 3 if held else 4,
                    'fees':'0' if index==0 else '5.20','known_nav_frames':120 if gap else 125,'source_frames':125,
                    'max_drawdown_dollars':None if gap else '0' if index==0 else '25.00',
                    'terminal_pnl_without_largest_1_positive':None if held else '25.00',
                    'terminal_pnl_without_largest_2_positive':None if held else '-12.25',
                    'hold_code':'deadline_missing_bid' if held else None,'engine_report_sha256':sha(identifier.encode())})
    result={'kind':'fixed_options_development_robustness_audit','case_count':712,'configuration_count':89,
        'cases':cases,'aggregates':aggregates,'automatic_adoption':False,'independent_holdout':False,
        'baseline_reports_exact_matches':178,'all_scenario_profit_leaders':['candidate_001'],
        'intent_screen_ids':[]}
    receipt={'schema':1,'kind':'fixed_KO_robustness_audit','status':'completed','source_stable':True,
        'registered_at':'2026-11-05T23:00:00+00:00','seconds':12.345,
        'network_calls':0,'model_calls':0,'paper_operations':0}
    return result,receipt


class Document(HTMLParser):
    def __init__(self,html):
        super().__init__();self.tables={};self.current=None;self.row=None;self.cell=None
        self.tags=Counter();self.references=[];self.feed(html)
    def handle_starttag(self,tag,attrs):
        self.tags[tag]+=1;attrs=dict(attrs)
        for key in ('href','src','action','poster'):
            if key in attrs:self.references.append((tag,key,attrs[key]))
        if tag=='table':self.current=attrs['id'];self.tables[self.current]=[]
        elif tag=='tr' and self.current is not None:self.row=[]
        elif tag=='td' and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag=='td' and self.cell is not None:self.row.append(''.join(self.cell));self.cell=None
        elif tag=='tr' and self.current is not None and self.row is not None:
            if self.row:self.tables[self.current].append(self.row)
            self.row=None
        elif tag=='table':self.current=None


class RobustnessHTMLTests(unittest.TestCase):
    def test_every_89_configuration_and_712_case_identity_is_visible_once(self):
        result,receipt=fixture();doc=Document(report.render(result,receipt))
        self.assertEqual(len(doc.tables['all-configurations']),89)
        self.assertEqual(len(doc.tables['all-cases']),712)
        expected=Counter((report.label(c['config_id']),c['clock_profile'],c['scenario'])for c in result['cases'])
        actual=Counter(tuple(row[:3])for row in doc.tables['all-cases'])
        self.assertEqual(actual,expected)
        self.assertTrue(all(count==1 for count in actual.values()))

    def test_held_and_null_nav_cases_keep_state_and_unknown_drawdown(self):
        result,receipt=fixture();doc=Document(report.render(result,receipt))
        held=[r for r in doc.tables['all-cases']if r[0]=='candidate 002']
        self.assertEqual(len(held),8)
        for row in held:
            self.assertEqual(row[3],'held');self.assertEqual(row[4],'Unavailable')
            self.assertEqual(row[8],'120/125');self.assertEqual(row[9],'Unavailable')
            self.assertEqual(row[12],'deadline_missing_bid')
        aggregate=next(r for r in doc.tables['all-configurations']if r[0]=='candidate 003')
        self.assertEqual(aggregate[6],'No')

    def test_escaped_configuration_hold_and_receipt_text_cannot_create_active_tags(self):
        result,receipt=fixture();malicious='<script src="https://invalid.example/x">&bad</script>'
        old='candidate_001';result['all_scenario_profit_leaders']=[malicious]
        for aggregate in result['aggregates']:
            if aggregate['config']['id']==old:aggregate['config']['id']=malicious
        for case in result['cases']:
            if case['config_id']==old:case['config_id']=malicious
            if case['config_id']=='candidate_002':case['hold_code']='<img src=x onerror=bad>'
        receipt['registered_at']='<svg onload=bad>'
        html=report.render(result,receipt);doc=Document(html)
        self.assertEqual(doc.tags['script'],0);self.assertEqual(doc.tags['img'],0)
        self.assertIn('&lt;script',html);self.assertIn('&lt;img',html)
        self.assertIn('&lt;svg onload=bad&gt;',html)
        self.assertNotIn('https://invalid.example/x',[x[2]for x in doc.references])

    def test_report_has_only_copied_local_links_and_inline_assets(self):
        result,receipt=fixture();html=report.render(result,receipt);doc=Document(html)
        self.assertEqual(doc.references,[('a','href','results.json'),('a','href','study-completed.json')])
        for tag in ('script','link','iframe','img','object','embed','form'):
            self.assertEqual(doc.tags[tag],0)
        self.assertNotIn('url(',html.lower())
        self.assertNotIn('\u00e2\u02c6',html)

    def test_development_and_cash_selection_limits_remain_prominent(self):
        result,receipt=fixture();html=report.render(result,receipt)
        for text in ('already been inspected','earlier frozen chronological selection remains cash',
                     'no untouched holdout','Held books are unranked','not proof of an edge',
                     'not a guaranteed fill','Drawdown is unavailable','Energy cost is unmeasured'):
            self.assertIn(text,html)
        self.assertIn('No strategy meets the registered evidence screen.',html)

    def test_incomplete_or_wrong_sized_results_are_rejected(self):
        for change in (lambda r:r['cases'].pop(),lambda r:r['aggregates'].pop(),
                       lambda r:r.__setitem__('automatic_adoption',True),
                       lambda r:r.__setitem__('independent_holdout',True),
                       lambda r:r.__setitem__('baseline_reports_exact_matches',177)):
            result,receipt=fixture();change(result)
            with self.assertRaises(ValueError):report.render(result,receipt)

    def test_nonzero_or_unknown_operation_receipts_cannot_print_zero_claim(self):
        for field in ('network_calls','model_calls','paper_operations'):
            for value in (1,None,False):
                result,receipt=fixture();receipt[field]=value
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):report.render(result,receipt)

    def test_money_is_decimal_and_independent_of_caller_precision(self):
        with localcontext()as context:
            context.prec=2
            self.assertEqual(report.money('1000.125'),'+$1,000.12')
            self.assertEqual(report.money('-183.90'),'\u2212$183.90')
            self.assertEqual(report.money(None),'Unavailable')


class RobustnessPublicationTests(unittest.TestCase):
    def setUp(self):
        staging=ROOT/'staging';staging.mkdir(exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(prefix='robustness-report-test-',dir=staging)
        self.root=Path(self.temporary.name)
        self.assertEqual(self.root.resolve().parent,staging.resolve())
        self.addCleanup(self.cleanup)
        self.source=self.root/'study';self.source.mkdir()
        self.result,self.receipt=fixture()
        self.write_source()
    def cleanup(self):
        # Every recursive cleanup target is verified inside this fixture's box.
        if self.root.exists():
            if self.root.resolve().parent!=(ROOT/'staging').resolve():raise AssertionError('Cleanup escaped staging')
        self.temporary.cleanup()
    def write_source(self,extra=None):
        payload=canonical(self.result);witness=b'fictional-full-ledger-witness'
        (self.source/'results.json').write_bytes(payload);(self.source/'witness.json').write_bytes(witness)
        artifacts={'results.json':{'bytes':len(payload),'sha256':sha(payload)},
            'witness.json':{'bytes':len(witness),'sha256':sha(witness)}}
        if extra:artifacts.update(extra)
        self.receipt['artifacts']=artifacts;self.seal_receipt()
    def seal_receipt(self):
        self.receipt_raw=canonical(self.receipt);self.receipt_hash=sha(self.receipt_raw)
        (self.source/'completed.json').write_bytes(self.receipt_raw)
    def run_report(self,output='data/workspace/reports/demo',digest=None):
        return report.run('study',self.receipt_hash if digest is None else digest,output,root=self.root)
    def test_valid_full_inventory_is_copied_and_output_hashes_verify(self):
        result=self.run_report();base=self.root/'data/workspace/reports/demo'
        self.assertEqual(result['source_completed_sha256'],self.receipt_hash)
        self.assertEqual((base/'study-completed.json').read_bytes(),self.receipt_raw)
        self.assertEqual((base/'results.json').read_bytes(),(self.source/'results.json').read_bytes())
        for name,item in result['artifacts'].items():
            raw=(base/name).read_bytes();self.assertEqual(len(raw),item['bytes']);self.assertEqual(sha(raw),item['sha256'])
        self.assertEqual(len(Document((base/'report.html').read_text(encoding='utf-8')).tables['all-cases']),712)
        self.assertTrue((base/'completed.json').is_file())
    def test_wrong_completed_hash_creates_no_output(self):
        with self.assertRaises(ValueError):self.run_report(digest='0'*64)
        self.assertFalse((self.root/'data').exists())
    def test_unused_source_artifact_tampering_also_stops_before_output(self):
        (self.source/'witness.json').write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.run_report()
        self.assertFalse((self.root/'data').exists())
    def test_missing_results_and_ambiguous_inventory_are_rejected(self):
        del self.receipt['artifacts']['results.json'];self.seal_receipt()
        with self.assertRaises(ValueError):self.run_report()
        self.write_source()
        raw=b'alias';(self.source/'duplicate.json').write_bytes(raw);(self.source/'DUPLICATE.json').write_bytes(raw)
        item={'bytes':len(raw),'sha256':sha(raw)}
        self.receipt['artifacts'].update({'duplicate.json':item,'DUPLICATE.json':item});self.seal_receipt()
        with self.assertRaises(ValueError):self.run_report()
    def test_declared_artifact_length_and_type_bounds_are_enforced(self):
        for value in (True,0,32*1024*1024+1):
            self.receipt['artifacts']['witness.json']['bytes']=value;self.seal_receipt()
            with self.subTest(value=value),self.assertRaises(ValueError):self.run_report()
    def test_duplicate_results_json_keys_cannot_be_adopted(self):
        raw=b'{"kind":"first","kind":"second"}';(self.source/'results.json').write_bytes(raw)
        self.receipt['artifacts']['results.json']={'bytes':len(raw),'sha256':sha(raw)};self.seal_receipt()
        with self.assertRaises(ValueError):self.run_report()
        self.assertFalse((self.root/'data').exists())
    def test_only_one_named_report_folder_is_permitted(self):
        for output in ('staging/demo','data/workspace/reports/demo/nested','data/workspace/reports/../demo'):
            with self.subTest(output=output),self.assertRaises(ValueError):self.run_report(output)
    def test_existing_output_is_not_overwritten_or_reused(self):
        base=self.root/'data/workspace/reports/demo';base.mkdir(parents=True)
        marker=base/'sentinel.txt';marker.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):self.run_report()
        self.assertEqual(marker.read_bytes(),b'keep');self.assertFalse((base/'completed.json').exists())
    def test_publication_readback_failure_leaves_incomplete_nonreplayed_output(self):
        real=report.read
        def changed(guard,relative,limit):
            raw=real(guard,relative,limit)
            return b'changed' if relative=='data/workspace/reports/demo/report.html' else raw
        with patch.object(report,'read',side_effect=changed),self.assertRaises(ValueError):self.run_report()
        base=self.root/'data/workspace/reports/demo'
        self.assertTrue(base.is_dir());self.assertFalse((base/'completed.json').exists())
        with self.assertRaises(FileExistsError):self.run_report()


if __name__=='__main__':unittest.main()
