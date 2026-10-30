"""Exact fictional handoff math, content review, persistence and export stops."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

from ariadne.config import Settings, _allowed_path
from ariadne.handoff import HandoffDesk, HandoffConflict, assess_record, validate_record, _load, MAX_REQUESTS
from ariadne.store import Store
from ariadne.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


class HandoffTests(unittest.TestCase):
    def setUp(self):
        parent = _allowed_path(ROOT/'runtime/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='handoff-unit-', dir=parent)
        self.base = _allowed_path(temporary.name)
        def cleanup():
            path = _allowed_path(temporary.name)
            if path.parent != parent or not path.name.startswith('handoff-unit-'):
                raise ValueError('Refusing cleanup outside the generated handoff fixture.')
            temporary.cleanup()
        self.addCleanup(cleanup)
        self.settings = Settings(data_dir=self.base/'private', workspace=self.base/'workspace')
        self.store = Store(self.settings.data_dir/'ariadne.sqlite3')
        self.registry = ToolRegistry(self.settings)
        self.desk = HandoffDesk(self.registry, self.store)
        self.fixture = json.loads((ROOT/'config/examples/handoff-demo.json').read_bytes())

    def seed(self):
        return self.desk.seed_demo({'request_id':'seed','expected_revision':0})

    def select(self, identifier='demo-office-quote'):
        return next(row for row in self.desk.status()['records'] if row['id'] == identifier)

    def review(self, identifier='demo-office-quote', key='review', decision='approve'):
        state=self.desk.status();record=self.select(identifier)
        return self.desk.review({'request_id':key,'expected_revision':state['revision'],
            'record_id':identifier,'content_sha256':record['content_sha256'],'decision':decision})

    def revise(self, changes, identifier='demo-office-quote', key='revise'):
        state=self.desk.status();record=self.select(identifier)
        return self.desk.revise({'request_id':key,'expected_revision':state['revision'],
            'record_id':identifier,'content_sha256':record['content_sha256'],'changes':changes})

    def test_passive_status_does_not_initialize_demo_or_business_ledger(self):
        before=self.store.path.read_bytes()
        value=self.desk.status()
        self.assertFalse(value['initialized']);self.assertEqual(value['revision'],0)
        self.assertEqual(value['records'],[]);self.assertIsNone(value['latest_export'])
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='handoff_demo_state'").fetchone())
        self.assertFalse((self.settings.data_dir/'business.sqlite3').exists())
        self.assertFalse(list(self.settings.workspace.iterdir()))

    def test_seed_is_explicit_fictional_idempotent_and_persistent(self):
        with mock.patch.object(socket,'socket',side_effect=AssertionError('No network')), mock.patch('subprocess.Popen',side_effect=AssertionError('No child/model')):
            first=self.seed()
        self.assertEqual(first['summary'],{'records':4,'ready_for_review':2,'held':2,'approved':0,'rejected':0})
        self.assertTrue(first['demo_only']);self.assertEqual(first['model_calls'],0)
        replay=self.seed();self.assertTrue(replay['replayed']);self.assertEqual(replay['revision'],1)
        recreated=HandoffDesk(self.registry,self.store)
        self.assertEqual(recreated.status()['records'],self.desk.status()['records'])
        with self.assertRaises(HandoffConflict):
            self.desk.seed_demo({'request_id':'new-seed','expected_revision':1})
        self.assertFalse((self.settings.data_dir/'business.sqlite3').exists())

    def test_packaged_fixture_matches_documented_example(self):
        self.assertEqual((ROOT/'config/examples/handoff-demo.json').read_bytes(),
                         (ROOT/'ariadne/resources/handoff/demo.json').read_bytes())

    def test_money_exact_and_approved_tax_rounds_half_up(self):
        self.seed()
        quote=self.select();invoice=self.select('demo-completed-invoice')
        self.assertEqual((quote['subtotal_cents'],quote['tax_cents'],quote['total_cents']),('50000','0','50000'))
        self.assertEqual((invoice['subtotal_cents'],invoice['tax_cents'],invoice['total_cents']),('17990','1484','19474'))
        penny=copy.deepcopy(self.fixture['records'][0]);penny['lines'][0].update(quantity=1,unit_price='0.01')
        penny['tax']['bps']=5000
        self.assertEqual(assess_record(penny)['tax_cents'],'1')

    def test_unknown_rate_and_tax_remain_held_with_no_complete_subtotal(self):
        self.seed();held=self.select('demo-missing-rate')
        self.assertEqual(held['status'],'held')
        self.assertIsNone(held['subtotal_cents']);self.assertIsNone(held['tax_cents']);self.assertIsNone(held['total_cents'])
        self.assertTrue({'missing_rate','unknown_tax'} <= {row['code'] for row in held['exceptions']})
        with self.assertRaises(HandoffConflict):self.review('demo-missing-rate')

    def test_known_subtotal_does_not_infer_tax_or_final_total(self):
        record=copy.deepcopy(self.fixture['records'][0]);record['tax'].update(bps=None,approved=False,source_ref=None)
        result=assess_record(record)
        self.assertEqual(result['subtotal_cents'],'50000');self.assertIsNone(result['total_cents'])
        self.assertEqual(result['status'],'held')
        record['tax']['bps']=0
        self.assertIsNone(assess_record(record)['total_cents'])

    def test_invoice_requires_completed_work_and_quote_never_becomes_invoice(self):
        self.seed();record=self.select('demo-unconfirmed-work')
        self.assertEqual(record['subtotal_cents'],'24000');self.assertIsNone(record['total_cents'])
        self.assertIn('completion_required',[row['code'] for row in record['exceptions']])
        with self.assertRaises(HandoffConflict):self.review('demo-unconfirmed-work')
        with self.assertRaises(ValueError):self.revise({'kind':'invoice'})
        self.assertEqual(self.select()['kind'],'quote')

    def test_approve_hash_cas_and_any_edit_invalidates_review(self):
        self.seed();source=self.select()
        approved=self.review();self.assertEqual(approved['revision'],2)
        self.assertEqual(self.select()['status'],'approved')
        self.revise({'title':'Revised fictional scope'})
        changed=self.select();self.assertEqual(changed['status'],'ready_for_review');self.assertIsNone(changed['review'])
        self.assertNotEqual(source['content_sha256'],changed['content_sha256'])
        self.assertEqual(changed['revision'],2)
        with self.assertRaises(HandoffConflict):
            self.desk.review({'request_id':'stale-hash','expected_revision':3,'record_id':source['id'],
                'content_sha256':source['content_sha256'],'decision':'approve'})
        with self.assertRaises(HandoffConflict):
            self.desk.review({'request_id':'stale-cas','expected_revision':1,'record_id':changed['id'],
                'content_sha256':changed['content_sha256'],'decision':'approve'})

    def test_resolve_needs_explicit_price_and_tax_sources(self):
        self.seed();record=self.select('demo-missing-rate')
        lines=[{k:line[k] for k in ('description','quantity','unit_price','price_approved','source_ref')} for line in record['lines']]
        lines[0].update(unit_price='99.50',price_approved=True,source_ref='DEMO supplied approved rate')
        self.revise({'lines':lines,'tax':{'bps':0,'approved':True,'source_ref':'DEMO supplied explicit tax basis'}},identifier=record['id'])
        result=self.select(record['id'])
        self.assertEqual(result['status'],'ready_for_review');self.assertEqual(result['total_cents'],'9950')
        self.assertIsNone(result['review'])

    def test_invalid_inputs_rejected_without_state_changes(self):
        self.seed();before=self.desk.status()
        for changes in ({'lines':[]},{'tax':{'bps':True,'approved':True,'source_ref':'Demo'}},
                        {'customer':{'name':'A','email':None,'address':'B','unexpected':'value'}},
                        {'document_date':'2026-2-03'},{'sent':True}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.revise(changes)
        self.assertEqual(self.desk.status(),before)
        for literal in (b'{"schema":1,"schema":2}', b'{"rate":NaN}', b'{"rate":1.25}', b'{"n":1234567890123456789}'):
            with self.assertRaises(ValueError):_load(literal)

    def test_exact_money_and_quantity_bounds_reject_floats_bools_negative(self):
        for price in (1.25,True,'-0.01','0.001','NaN','Infinity','10000000000000000000000'):
            record=copy.deepcopy(self.fixture['records'][0]);record['lines'][0]['unit_price']=price
            with self.subTest(price=price),self.assertRaises(ValueError):validate_record(record)
        for quantity in (True,0,-1,1.5,10001):
            record=copy.deepcopy(self.fixture['records'][0]);record['lines'][0]['quantity']=quantity
            with self.subTest(quantity=quantity),self.assertRaises(ValueError):validate_record(record)

    def test_future_invoice_and_backward_due_date_hold(self):
        record=copy.deepcopy(self.fixture['records'][1]);record.update(service_date='2026-11-01',due_date='2026-10-01')
        result=assess_record(record)
        self.assertTrue({'future_service','date_order'} <= {row['code'] for row in result['exceptions']})
        self.assertIsNone(result['total_cents'])

    def test_aggregate_and_tax_inclusive_totals_are_bounded(self):
        record=copy.deepcopy(self.fixture['records'][0])
        line=record['lines'][0];line.update(quantity=1,unit_price='10000000000.00')
        record['tax']['bps']=1
        with self.assertRaises(ValueError):assess_record(record)
        record['tax']['bps']=0;record['lines'].append(copy.deepcopy(line))
        with self.assertRaises(ValueError):assess_record(record)

    def test_reject_is_demo_review_not_external_action(self):
        self.seed();self.review(decision='reject')
        record=self.select();self.assertEqual(record['status'],'rejected')
        self.assertFalse(record['sent']);self.assertFalse(record['posted']);self.assertFalse(record['payment_received'])
        self.assertTrue(record['review']['demo_only'])

    def test_paused_mutations_leave_state_and_requests_unchanged(self):
        self.seed();self.store.set_setting('paused',True)
        before=self.desk.status()
        with self.assertRaises(HandoffConflict):self.review()
        with self.assertRaises(HandoffConflict):self.revise({'title':'Held edit'})
        with self.assertRaises(HandoffConflict):self.desk.export({'request_id':'held-export','expected_revision':1})
        self.assertEqual(before,self.desk.status())

    def test_request_ids_bind_action_and_arguments_and_revision_is_not_bool(self):
        self.seed()
        with self.assertRaises(HandoffConflict):self.desk.seed_demo({'request_id':'seed','expected_revision':1})
        with self.assertRaises(ValueError):self.desk.seed_demo({'request_id':'bool','expected_revision':True})
        with self.assertRaises(ValueError):self.desk.export({'request_id':'bad/id','expected_revision':1})

    def test_export_only_approved_complete_records_exact_readback_and_replay(self):
        self.seed();self.review();self.review('demo-completed-invoice',key='invoice-review')
        body={'request_id':'packet','expected_revision':3}
        with mock.patch.object(socket,'socket',side_effect=AssertionError('No network')),mock.patch('subprocess.Popen',side_effect=AssertionError('No child/model')):
            result=self.desk.export(body)
        self.assertEqual(result['status'],'completed');self.assertEqual(len(result['record_ids']),2)
        self.assertEqual(len(result['excluded_record_ids']),2)
        for artifact in result['artifacts']:
            raw=(self.settings.workspace/artifact['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),artifact['sha256']);self.assertEqual(len(raw),artifact['bytes'])
        packet=json.loads((self.settings.workspace/'handoff-demo/packet/packet.json').read_bytes())
        self.assertTrue(packet['demo_only']);self.assertEqual(len(packet['records']),2)
        markup=(self.settings.workspace/'handoff-demo/packet/packet.html').read_text(encoding='utf-8')
        self.assertEqual(markup.count('FICTIONAL DEMONSTRATION'),2)
        self.assertNotIn('<script',markup);self.assertNotIn('http://',markup);self.assertNotIn('https://',markup)
        self.assertTrue(self.desk.export(body)['replayed'])
        self.assertEqual(self.desk.status()['latest_export']['request_id'],'packet')
        self.assertFalse((self.settings.data_dir/'business.sqlite3').exists())

    def test_empty_or_held_packet_cannot_export(self):
        self.seed()
        with self.assertRaises(HandoffConflict):self.desk.export({'request_id':'empty','expected_revision':1})
        self.assertFalse((self.settings.workspace/'handoff-demo').exists())

    def test_export_content_is_html_escaped(self):
        self.seed();self.revise({'title':'<img src=x onerror=alert(1)>','source_notes':'<script>unsafe</script>'})
        self.review();result=self.desk.export({'request_id':'escaped','expected_revision':3})
        artifact=next(row for row in result['artifacts'] if row['path'].endswith('.html'))
        markup=(self.settings.workspace/artifact['path']).read_text(encoding='utf-8')
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;',markup)
        self.assertNotIn('<img src=x',markup);self.assertNotIn('<script>unsafe',markup)

    def test_partial_export_failed_reservation_never_replays_or_adopts(self):
        self.seed();self.review()
        body={'request_id':'partial','expected_revision':2}
        from ariadne.atomic import publish_bytes as actual_publish
        calls=[]
        def fail_second(path,raw,**kwargs):
            calls.append(path)
            if len(calls)==2:raise OSError('simulated second file failure')
            return actual_publish(path,raw,**kwargs)
        with mock.patch('ariadne.handoff.publish_bytes',side_effect=fail_second),self.assertRaises(OSError):self.desk.export(body)
        first=self.settings.workspace/'handoff-demo/partial/packet.json'
        self.assertTrue(first.is_file());before=first.read_bytes()
        with self.assertRaises(HandoffConflict):self.desk.export(body)
        self.assertEqual(first.read_bytes(),before)
        self.assertEqual(self.desk.status()['latest_export']['status'],'failed')
        with self.assertRaises(HandoffConflict):self.desk.export({'request_id':'new-blocked','expected_revision':2})
        self.assertEqual(self.desk.status()['export_hold']['request_id'],'partial')

    def test_acknowledgement_lifts_hold_without_adopting_or_replaying_old_output(self):
        self.seed();self.review()
        body={'request_id':'uncertain','expected_revision':2}
        with mock.patch('ariadne.handoff.publish_bytes',side_effect=OSError('simulated failure')),self.assertRaises(OSError):self.desk.export(body)
        with self.assertRaises(HandoffConflict):self.desk.acknowledge_export({'request_id':'uncertain','expected_revision':1})
        acknowledge={'request_id':'uncertain','expected_revision':2}
        result=self.desk.acknowledge_export(acknowledge)
        self.assertEqual(result['revision'],3);self.assertIsNone(result['export_hold'])
        self.assertEqual(result['latest_export']['status'],'acknowledged')
        self.assertTrue(self.desk.acknowledge_export(acknowledge)['replayed'])
        with self.assertRaises(HandoffConflict):self.desk.export(body)
        fresh=self.desk.export({'request_id':'fresh-after-ack','expected_revision':3})
        self.assertEqual(fresh['status'],'completed')
        with self.assertRaises(HandoffConflict):self.desk.acknowledge_export({'request_id':'fresh-after-ack','expected_revision':3})

    def test_tampered_completed_export_is_not_regenerated_and_status_is_explicit(self):
        self.seed();self.review();body={'request_id':'tamper','expected_revision':2}
        result=self.desk.export(body)
        path=self.settings.workspace/result['artifacts'][0]['path'];path.write_text('changed')
        with self.assertRaises(ValueError):self.desk.export(body)
        self.assertEqual(path.read_text(),'changed')
        self.assertEqual(self.desk.status()['latest_export']['status'],'integrity_error')

    def test_state_changes_during_export_fail_stop(self):
        self.seed();self.review();body={'request_id':'changing','expected_revision':2}
        from ariadne.atomic import publish_bytes as actual_publish
        changed=False
        def mutate_during_publish(path,raw,**kwargs):
            nonlocal changed
            if not changed:
                changed=True;self.revise({'title':'Owner revised during export'},key='concurrent')
            return actual_publish(path,raw,**kwargs)
        with mock.patch('ariadne.handoff.publish_bytes',side_effect=mutate_during_publish),self.assertRaises(HandoffConflict):self.desk.export(body)
        self.assertEqual(self.select()['status'],'ready_for_review')
        with self.assertRaises(HandoffConflict):self.desk.export(body)

    def test_history_quota_is_bounded_without_overwriting_old_requests(self):
        self.seed()
        with self.store.connect() as db:
            for index in range(MAX_REQUESTS-1):
                db.execute("INSERT INTO handoff_demo_requests VALUES(?,?,?,?,?,?,?,0)",(f'reserved-{index}','review','x','failed','2026-10-30',1,'{}'))
        with self.assertRaises(ValueError):self.review()
        self.assertEqual(self.desk.status()['revision'],1)
        self.assertTrue(self.seed()['replayed'])


if __name__=='__main__':unittest.main()
