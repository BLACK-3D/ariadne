"""Safe offline command publication and data-admission tests; fictional quotes."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import options_book as cli

FIXTURE = Path(__file__).resolve().parents[1] / 'docs/examples/options-book.synthetic.json'
NOW = datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc)

class OptionsBookCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'data/workspace'
        (self.workspace / 'imports').mkdir(parents=True)
        self.payload = json.loads(FIXTURE.read_text())
        self.input = self.workspace / 'imports/book.json'
        self.input.write_text(json.dumps(self.payload))
        self.args = cli.parser().parse_args(['review', '--input', 'imports/book.json', '--output', 'reports/one'])
        self.destination = self.workspace / 'reports/one'
    def run_review(self):return cli.run(self.args, root=self.root, now=NOW)
    def test_exact_guide_sale_uses_actual_bid_and_immutable_artifacts(self):
        before=self.input.read_bytes();receipt=self.run_review()
        review=json.loads((self.destination/'review.json').read_text());proposal=review['evaluation']['proposals'][0]
        self.assertEqual(proposal['proposed_quantity'],2)
        self.assertEqual(proposal['quoted_price'],'1.05')
        self.assertFalse(proposal['execution_confirmed'])
        self.assertFalse(review['provider_authenticated'])
        self.assertFalse(review['portfolio_controls_checked'])
        self.assertEqual(receipt['input']['sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(len(receipt['source_sha256']),8)
        for name,item in receipt['artifacts'].items():
            raw=(self.destination/name).read_bytes()
            self.assertEqual(len(raw),item['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),item['sha256'])
        self.assertEqual(self.input.read_bytes(),before)
        self.assertEqual(receipt['brokerage_orders'],0);self.assertEqual(receipt['paper_ledger_writes'],0)
        self.assertIn('Paper proposals only',(self.destination/'report.html').read_text())
    def test_unclocked_legacy_values_are_counted_without_admitting_or_filling(self):
        self.args.action='audit-legacy'
        self.input.write_text(json.dumps({'source':{'quote_time_basis':'assumed_eod'},'snapshots':[{'observed_at':'2026-10-30T20:00:00+00:00','quote_observed_at':None,'quotes':[{'bid':'1','ask':'1.05','bid_size':None,'ask_size':2,'volume':9999,'open_interest':99999}]}]}))
        self.run_review();review=json.loads((self.destination/'review.json').read_text())
        self.assertFalse(review['strict_book_compatible']);self.assertFalse(review['fill_model_ran'])
        self.assertEqual(review['counts']['quotes_with_numeric_volume'],1)
        self.assertEqual(review['counts']['quotes_with_numeric_open_interest'],1)
        self.assertEqual(review['counts']['snapshots_without_valid_declared_event_clock'],1)
        self.assertEqual(review['counts']['quotes_with_missing_bid_size'],1)
        self.assertEqual(review['paper_proposals'],0)
    def test_zero_unknown_malformed_depth_and_bad_clocks_remain_distinct(self):
        self.args.action='audit-legacy'
        rows=[]
        for clock in (True, '2026-11-02', 'not-an-event-time', ['malformed']):
            rows.append({'quote_observed_at':clock,'quotes':[{'bid_size':None,'ask_size':None},{'bid_size':0,'ask_size':0},{'bid_size':True,'ask_size':-1}]})
        self.input.write_text(json.dumps({'snapshots':rows}))
        self.run_review();review=json.loads((self.destination/'review.json').read_text());counts=review['counts']
        self.assertEqual(counts['snapshots_without_valid_declared_event_clock'],4)
        self.assertEqual(counts['snapshots_with_valid_declared_event_clock'],0)
        for side in ('bid','ask'):
            for kind in ('missing','zero','malformed'):
                self.assertEqual(counts['quotes_with_'+kind+'_'+side+'_size'],4)
        self.assertFalse(review['actual_event_time_authenticated']);self.assertFalse(review['strict_book_compatible'])
    def test_uppercase_side_is_invalid_instead_of_silently_reinterpreted(self):
        data=deepcopy(self.payload);data['requests'][0]['side']='BUY';self.input.write_text(json.dumps(data))
        with self.assertRaises(ValueError):self.run_review()
        self.assertFalse(self.destination.exists())
    def test_duplicate_json_keys_refuse_before_output(self):
        self.input.write_text('{"schema":1,"schema":1,"books":[],"requests":[]}')
        with self.assertRaises(ValueError):self.run_review()
        self.assertFalse(self.destination.exists())
    def test_future_or_naive_request_refuses_before_output(self):
        for clock in ('2026-11-04T15:00:00+00:00','2026-11-02T15:30:01'):
            with self.subTest(clock=clock):
                data=deepcopy(self.payload);data['requests'][0]['decision_at']=clock;self.input.write_text(json.dumps(data))
                with self.assertRaises(ValueError):self.run_review()
                self.assertFalse(self.destination.exists())
    def test_schema_boolean_and_unrecognized_fields_refuse(self):
        for data in ({**self.payload,'schema':True},{**self.payload,'broker_token':'fictional-unexpected-field'}):
            self.input.write_text(json.dumps(data))
            with self.assertRaises(ValueError):self.run_review()
            self.assertFalse(self.destination.exists())
    def test_batch_size_cannot_bypass_bound(self):
        data=deepcopy(self.payload);data['books']*=257;self.input.write_text(json.dumps(data))
        with self.assertRaises(ValueError):self.run_review()
        self.assertFalse(self.destination.exists())
    def test_parent_and_absolute_input_and_output_are_refused(self):
        for key,value in [('input','../ariadne.sqlite3'),('input','C:/outside/book.json'),('output','reports/../replace'),('output','imports/overwrite')]:
            original=getattr(self.args,key);setattr(self.args,key,value)
            with self.assertRaises((ValueError,RuntimeError)):self.run_review()
            setattr(self.args,key,original)
        self.assertEqual(self.input.read_text(),json.dumps(self.payload))
    def test_existing_or_partial_output_is_preserved(self):
        self.destination.mkdir(parents=True);path=self.destination/'attempt.json';path.write_bytes(b'uncertain preserved attempt')
        with self.assertRaises(FileExistsError):self.run_review()
        self.assertEqual(path.read_bytes(),b'uncertain preserved attempt')
    def test_changed_source_is_rejected_before_publication(self):
        from ariadne import options_liquidity
        original=options_liquidity.evaluate_batch
        def changed(*args):
            result=original(*args);self.input.write_bytes(self.input.read_bytes()+b' ');return result
        with patch.object(options_liquidity,'evaluate_batch',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'changed'):self.run_review()
        self.assertFalse(self.destination.exists())
    def test_changed_code_pin_is_rejected_before_publication(self):
        with patch.object(cli,'_source_hashes',side_effect=[{'code':'before'},{'code':'after'}]):
            with self.assertRaisesRegex(ValueError,'changed'):self.run_review()
        self.assertFalse(self.destination.exists())
    def test_partial_publication_retains_attempt_and_cannot_replay(self):
        from ariadne import atomic
        original=atomic.publish_bytes
        def fail_after_attempt(path,data,*args,**kwargs):
            if Path(path).name=='review.json':raise OSError('Synthetic publication failure')
            return original(path,data,*args,**kwargs)
        with patch.object(atomic,'publish_bytes',side_effect=fail_after_attempt):
            with self.assertRaises(OSError):self.run_review()
        self.assertTrue((self.destination/'attempt.json').exists());self.assertFalse((self.destination/'completed.json').exists())
        with self.assertRaises(FileExistsError):self.run_review()
    def test_invalid_registration_clock_and_unqualified_buy(self):
        with self.assertRaises(ValueError):cli.run(self.args,root=self.root,now=datetime(2026,11,3))
        data=deepcopy(self.payload);data['requests'][0].update(side='buy',price_limit='1.07');data['books'][0].update(ask='1.07',ask_size=10)
        self.input.write_text(json.dumps(data));self.run_review()
        result=json.loads((self.destination/'review.json').read_text())['evaluation']['proposals'][0]
        self.assertEqual(result['status'],'no_fill');self.assertEqual(result['proposed_quantity'],0)

if __name__=='__main__':unittest.main()
