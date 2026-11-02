"""Local structured proposals: exact evidence, bounded schema and durable holds."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.client_handoff import ClientHandoffDesk
from ariadne.config import Settings, _allowed_path
from ariadne.handoff import HandoffConflict, _digest, _json
from ariadne.locking import InstanceLock
from ariadne.store import Store
from ariadne.tools import ToolRegistry
from ariadne import structured_intake as structured

ROOT = Path(__file__).resolve().parents[1]
TEXT = "Please arrange a window latch repair for Jill Example at 20 Example Lane. It should take 2.5 hours, charged at $35.00 USD per hour."
IDENTITY = {"model": "qwen3.5:9b", "provider": "http://127.0.0.1:11434", "implementation": "ollama-structured-intake-v1"}
ROWS = [
    {"field": "customer.name", "value": "Jill Example", "quote": "Please arrange a window latch repair for Jill Example at 20 Example Lane."},
    {"field": "lines.0.description", "value": "window latch repair", "quote": "Please arrange a window latch repair for Jill Example at 20 Example Lane."},
    {"field": "lines.0.quantity", "value": "2.5", "quote": "It should take 2.5 hours, charged at $35.00 USD per hour."},
    {"field": "lines.0.unit", "value": "hours", "quote": "It should take 2.5 hours, charged at $35.00 USD per hour."},
    {"field": "lines.0.unit_price", "value": "35.00", "quote": "It should take 2.5 hours, charged at $35.00 USD per hour."}]


class StructuredIntakeTests(unittest.TestCase):
    def setUp(self):
        parent = _allowed_path(ROOT / 'runtime/tmp'); parent.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='structured-intake-', dir=parent); self.addCleanup(temp.cleanup)
        settings = Settings(data_dir=Path(temp.name)/'data', workspace=Path(temp.name)/'workspace')
        self.store = Store(settings.data_dir/'ariadne.sqlite3'); self.desk = ClientHandoffDesk(ToolRegistry(settings), self.store)
        self.counter = 0; self.calls = []; self.rows = copy.deepcopy(ROWS); self.descriptor = dict(IDENTITY)
        self.desk.bind_structured_intake(self.generate, lambda: dict(self.descriptor))
        self.desk.save_client(self.body(client={"id": "service-a", "name": "Example Service", "address": "1 Example Lane"}))

    def body(self, **values):
        self.counter += 1
        return {"request_id": 'structured-'+str(self.counter), "expected_revision": self.desk.status()['revision'], **values}

    def packet(self, text=TEXT):
        saved = self.desk.prepare_intake_packet(self.body(client_id='service-a', filename='example.txt', content_base64=base64.b64encode(text.encode()).decode()))
        return self.desk.get_intake_packet(saved['entity_id'])['packet']

    def generate(self, snapshot):
        self.calls.append(copy.deepcopy(snapshot))
        return {'response': json.dumps({'suggestions': self.rows}), 'usage': {
            'profile': 'structured_intake', 'model': snapshot['model_identity']['model'], 'model_digest': 'a'*64,
            'source_sha256': snapshot['source_text_sha256'], 'schema_sha256': structured.SCHEMA_SHA256, 'request_sha256': 'b'*64,
            'prompt_tokens': 10, 'output_tokens': 20, 'duration_ms': 30, 'load_ms': None, 'prefill_ms': 10, 'decode_ms': 20,
            'metadata_requests': 3, 'generation_requests': 1}}

    def proposal(self, packet=None):
        packet = packet or self.packet()
        receipt = self.desk.prepare_intake_proposal(self.body(packet_id=packet['id'], content_sha256=packet['content_sha256']))
        return self.desk.get_intake_proposal(receipt['entity_id'])['proposal']

    def accept(self, packet, proposal, facts=None):
        return self.desk.accept_intake_packet(self.body(packet_id=packet['id'], content_sha256=packet['content_sha256'], proposal_id=proposal['id'], proposal_sha256=proposal['content_sha256'], review_confirmed=True, intake=facts or proposal['proposed_intake']))

    def test_passive_status_is_configured_only_and_no_callback(self):
        status = self.desk.status()['structured_extraction']
        self.assertTrue(status['available']); self.assertIsNone(status['hold']); self.assertEqual(self.calls, [])
        self.assertFalse((self.store.path.parent/'structured-intake.lock').exists())
        before = self.store.path.stat().st_size
        self.desk.structured_intake_status()
        self.assertEqual(self.store.path.stat().st_size, before)

    def test_literal_claims_and_hashes_source_immutable(self):
        packet = self.packet(); original = copy.deepcopy(packet); value = self.proposal(packet)
        self.assertEqual(len(value['candidates']), 5); self.assertEqual(value['proposed_intake']['customer']['name'], 'Jill Example')
        self.assertEqual(value['proposed_intake']['lines'][0]['quantity'], '2.5')
        self.assertEqual(value['usage']['generation_requests'], 1); self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['schema'], structured.SCHEMA); self.assertEqual(self.calls[0]['model_identity'], IDENTITY)
        for row in value['candidates']:
            self.assertEqual(packet['text'][row['start']:row['end']], row['quote'])
            self.assertEqual(hashlib.sha256(row['quote'].encode()).hexdigest(), row['quote_sha256'])
        self.assertEqual(self.desk.get_intake_packet(packet['id'])['packet'], original)
        self.assertFalse(value['proposed_intake']['lines'][0]['price_approved']); self.assertFalse(value['proposed_intake']['tax']['approved']); self.assertFalse(value['proposed_intake']['permission']['confirmed'])

    def test_source_read_lists_saved_proposal_without_generation(self):
        packet = self.packet(); proposal = self.proposal(packet)
        result = self.desk.get_intake_packet(packet['id'])
        self.assertEqual(result['structured_extraction']['proposals'][0]['id'], proposal['id']); self.assertEqual(len(self.calls), 1)

    def test_exact_replay_and_new_request_deduplicate_without_callback(self):
        packet = self.packet(); body = self.body(packet_id=packet['id'], content_sha256=packet['content_sha256'])
        first = self.desk.prepare_intake_proposal(body)
        self.assertTrue(self.desk.prepare_intake_proposal(body)['replayed'])
        second = self.desk.prepare_intake_proposal(self.body(packet_id=packet['id'], content_sha256=packet['content_sha256']))
        self.assertEqual(first['entity_id'], second['entity_id']); self.assertTrue(second['duplicate_source']); self.assertEqual(second['model_calls'], 0); self.assertEqual(len(self.calls), 1)
        with self.assertRaises(HandoffConflict): self.desk.prepare_intake_proposal({**body, 'content_sha256': 'f'*64})

    def test_reviewed_link_is_separate_and_all_approvals_reset(self):
        packet = self.packet(); proposal = self.proposal(packet); facts = copy.deepcopy(proposal['proposed_intake'])
        facts['lines'][0]['price_approved'] = True; facts['tax'].update(bps=0, approved=True); facts['permission']['confirmed'] = True
        receipt = self.accept(packet, proposal, facts); intake = self.desk.get_intake(receipt['entity_id'])['intake']
        self.assertEqual(intake['structured_proposal']['proposal_content_sha256'], proposal['content_sha256'])
        self.assertEqual(intake['structured_proposal']['facts_sha256'], intake['intake_packet']['facts_sha256'])
        self.assertFalse(intake['lines'][0]['price_approved']); self.assertFalse(intake['tax']['approved']); self.assertFalse(intake['permission']['confirmed'])
        self.assertNotIn('structured_proposal', intake['intake_packet'])
        quote = self.desk.generate_quote(self.body(intake_id=intake['id'], content_sha256=receipt['content_sha256']))
        document = self.desk.get_document(quote['entity_id'])['document']; self.assertEqual(document['status'], 'held'); self.assertEqual(document['structured_proposal'], intake['structured_proposal'])

    def test_mutation_receipt_failure_rolls_back_structured_link_and_consumption(self):
        packet = self.packet(); proposal = self.proposal(packet)
        with patch.object(self.desk, '_save_receipt', side_effect=ValueError('Receipt full')):
            with self.assertRaises(ValueError): self.accept(packet, proposal)
        self.assertIsNone(self.desk.get_intake_packet(packet['id'])['packet']['consumed_intake_id'])
        with self.desk.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM client_handoff_intakes').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM client_handoff_structured_intakes').fetchone()[0], 0)
        self.accept(packet, proposal)

    def test_schema_rejects_unsupported_authority_and_duplicate_json(self):
        packet = self.packet()
        bad = [json.dumps({'suggestions': [{'field':'permission.confirmed','value':'true','quote':'true'}]}),
            '{"suggestions":[],"suggestions":[]}', json.dumps({'suggestions': [], 'approved': True}), json.dumps({'suggestions':[dict(ROWS[0], approval=True)]}),
            json.dumps({'suggestions':[dict(ROWS[0], value=12.3)]}), json.dumps({'suggestions': [ROWS[0]]*17})]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError): structured.validate_response(raw, packet)

    def test_unicode_literal_offsets_and_normalized_decimal(self):
        packet = self.packet('Résumé for Zoë Example: it will take 2.50 hours.')
        rows = [{'field':'customer.name','value':'Zoë Example','quote':'Résumé for Zoë Example: it will take 2.50 hours.'}, {'field':'lines.0.quantity','value':'2.50','quote':'it will take 2.50 hours.'}]
        candidates, warnings, proposed = structured.validate_response(json.dumps({'suggestions':rows}), packet)
        self.assertEqual(proposed['customer']['name'], 'Zoë Example'); self.assertEqual(proposed['lines'][0]['quantity'], '2.5')
        self.assertEqual(candidates[1]['supplied_value'], '2.50')
        for item in candidates: self.assertEqual(packet['text'][item['start']:item['end']], item['quote'])

    def test_hallucinated_value_and_nonunique_quote_stay_unresolved(self):
        packet = self.packet('Jill Example needs a repair. Jill Example needs a repair.')
        rows = [{'field':'customer.name','value':'Jill Example','quote':'Jill Example needs a repair.'}, {'field':'title','value':'Roof repair','quote':packet['text']}]
        candidates, warnings, proposed = structured.validate_response(json.dumps({'suggestions':rows}), packet)
        self.assertEqual(candidates, []); self.assertIsNone(proposed['customer']['name']); self.assertTrue(any('more than once' in item for item in warnings)); self.assertTrue(any('Unresolved title:' in item for item in warnings))

    def test_duplicate_fields_and_repeated_labels_are_unresolved(self):
        packet = self.packet('Customer: Jill Example\nCustomer: Jane Example\nPlease call Jill Example.')
        rows = [{'field':'customer.name','value':'Jill Example','quote':'Please call Jill Example.'}]
        self.assertEqual(structured.validate_response(json.dumps({'suggestions':rows}), packet)[0], [])
        packet = self.packet('Jill Example needs a repair.')
        self.assertEqual(structured.validate_response(json.dumps({'suggestions':[rows[0],rows[0]]}), packet)[0], [])

    def test_conflicting_deterministic_value_is_preserved(self):
        packet = self.packet('Customer: Jill Example\nA note refers to Jane Example.')
        rows = [{'field':'customer.name','value':'Jane Example','quote':'A note refers to Jane Example.'}]
        candidates, warnings, proposed = structured.validate_response(json.dumps({'suggestions':rows}), packet)
        self.assertEqual(candidates, []); self.assertEqual(proposed['customer']['name'], 'Jill Example'); self.assertTrue(any('conflicts' in item for item in warnings))

    def test_foreign_currency_numeric_substring_and_relative_date_are_unresolved(self):
        packet = self.packet('The price is £35.00 for 25 hours. Please start tomorrow.')
        rows = [{'field':'lines.0.unit_price','value':'35.00','quote':'The price is £35.00 for 25 hours.'}, {'field':'lines.0.quantity','value':'5','quote':'The price is £35.00 for 25 hours.'}, {'field':'service_date','value':'tomorrow','quote':'Please start tomorrow.'}]
        candidates, warnings, proposed = structured.validate_response(json.dumps({'suggestions':rows}), packet)
        self.assertEqual(candidates, []); self.assertIsNone(proposed['service_date'])

    def test_numeric_prefix_suffix_sign_and_accounting_forms_are_not_positive_evidence(self):
        cases = [('40', '40.50 USD'), ('2', '2.5 hours'), ('40.00', '-$40.00'), ('40.00', '($40.00)'), ('40.00', '-40.00'), ('40.00', '- 40.00'), ('40.00', '(40.00 USD)'), ('40', '1,040 USD'), ('40', '40,000 USD'), ('0.50', '40.50 USD')]
        for value, quote in cases:
            with self.subTest(value=value, quote=quote):
                packet = self.packet(quote)
                result = structured.validate_response(json.dumps({'suggestions':[{'field':'lines.0.unit_price','value':value,'quote':quote}]}), packet)
                self.assertEqual(result[0], [])

    def test_sentence_terminal_price_preserves_complete_decimal(self):
        packet = self.packet('The quoted unit rate is USD 40.00.')
        result = structured.validate_response(json.dumps({'suggestions':[{'field':'lines.0.unit_price','value':'40.00','quote':packet['text']}]}), packet)
        self.assertEqual(result[2]['lines'][0]['unit_price'], '40.00')
        self.assertEqual(len(result[0]), 1)

    def test_cropped_quotes_cannot_hide_full_source_numeric_name_or_currency_context(self):
        cases = [
            ('The price was 40.50 USD.', 'lines.0.unit_price', '40', '40'),
            ('It will take 2.5 hours.', 'lines.0.quantity', '2', '2'),
            ('The rate was -$40.00.', 'lines.0.unit_price', '40.00', '40.00'),
            ('The rate was - USD 40.00.', 'lines.0.unit_price', '40.00', '40.00'),
            ('The rate was -' + ' '*100 + '$40.00.', 'lines.0.unit_price', '40.00', '40.00'),
            ('The balance was (40.00 USD).', 'lines.0.unit_price', '40.00', '40.00'),
            ('Call Jillian Example.', 'customer.name', 'Jill', 'Jill'),
            ('The rate was 40.00 GBP.', 'lines.0.unit_price', '40.00', '40.00')]
        for source, field, value, quote in cases:
            with self.subTest(source=source):
                packet = self.packet(source)
                result = structured.validate_response(json.dumps({'suggestions':[{'field':field,'value':value,'quote':quote}]}), packet)
                self.assertEqual(result[0], [])

    def test_grouped_unicode_numeric_sign_fraction_and_number_words_are_unresolved(self):
        sources = ['The price was 1 400.00 USD.', 'The price was 1\u00a0400.00 USD.', 'The price was 1\u202f400.00 USD.',
            'The price was 400.00 000 USD.', 'The price was ١ 400.00 USD.', 'The price was 1/400.00 USD.',
            'The price was 1٬400.00 USD.', 'The price was ½ 400.00 USD.', 'The price was forty 400.00 USD.',
            'The price was 400.00 thousand USD.', 'The refund was ‐400.00 USD.', 'The refund was ‒400.00 USD.',
            'The refund was –400.00 USD.', 'The refund was — $400.00 USD.', 'The refund was − USD 400.00.',
            'The rate was ₩400.00.', 'The charge was 400.00 cents.', 'The price was 1\u2009400.00 USD.',
            'The price was 1\t400.00 USD.', "The price was 1'400.00 USD.", 'The price was 1’400.00 USD.',
            'The refund was 400.00-', 'The refund was 400.00 −', 'The refund was 400.00 USD-',
            'The refund was -US$400.00.', 'The refund was (US$400.00).']
        for source in sources:
            with self.subTest(source=source):
                packet = self.packet(source)
                result = structured.validate_response(json.dumps({'suggestions':[{'field':'lines.0.unit_price','value':'400.00','quote':'400.00'}]}), packet)
                self.assertEqual(result[0], [])

    def test_model_price_requires_explicit_adjacent_usd_and_refuses_foreign_dollars(self):
        for source in ('The rate is $40.00.', 'The rate is CA$40.00.', 'The rate is NZ$40.00.', 'The rate is A$40.00.', 'The rate is CA$40.00 USD.'):
            with self.subTest(source=source):
                packet = self.packet(source)
                result = structured.validate_response(json.dumps({'suggestions':[{'field':'lines.0.unit_price','value':'40.00','quote':'40.00'}]}), packet)
                self.assertEqual(result[0], [])
        for source in ('The rate is 40.00 USD.', 'The rate is USD 40.00.', 'The rate is US$40.00.', 'The rate is $40.00 USD.'):
            with self.subTest(source=source):
                packet = self.packet(source)
                result = structured.validate_response(json.dumps({'suggestions':[{'field':'lines.0.unit_price','value':'40.00','quote':'40.00'}]}), packet)
                self.assertEqual(len(result[0]), 1)

    def test_mathematical_sign_prefixes_and_suffixes_are_unsupported_scalars(self):
        for sign in '±∓⁺⁻₊₋':
            for source in ('The rate is '+sign+'40.00 USD.', 'The rate is 40.00'+sign+' USD.'):
                with self.subTest(source=source):
                    packet = self.packet(source)
                    result = structured.validate_response(json.dumps({'suggestions':[{'field':'lines.0.unit_price','value':'40.00','quote':'40.00'}]}), packet)
                    self.assertEqual(result[0], [])

    def test_empty_output_is_an_explicit_unapproved_manual_fallback(self):
        self.rows = []; proposal = self.proposal()
        self.assertEqual(proposal['candidates'], []); self.assertTrue(any('No supported' in text for text in proposal['warnings'])); self.assertFalse(proposal['proposed_intake']['permission']['confirmed'])

    def test_failed_model_holds_and_exact_ack_releases_without_replay(self):
        packet = self.packet()
        def fail(snapshot): self.calls.append(snapshot); raise ValueError('Model refused')
        self.desk.bind_structured_intake(fail, lambda: IDENTITY)
        body = self.body(packet_id=packet['id'], content_sha256=packet['content_sha256'])
        with self.assertRaises(ValueError): self.desk.prepare_intake_proposal(body)
        hold = self.desk.status()['structured_extraction']['hold']; self.assertEqual(hold['status'], 'failed')
        with self.assertRaises(HandoffConflict): self.desk.prepare_intake_proposal(body)
        with self.assertRaises(HandoffConflict): self.desk.acknowledge_intake_extraction(self.body(receipt_sha256='f'*64, inspection_confirmed=True) | {'request_id':hold['request_id']})
        acknowledgement = self.body(receipt_sha256=hold['receipt_sha256'], inspection_confirmed=True) | {'request_id':hold['request_id']}
        receipt = self.desk.acknowledge_intake_extraction(acknowledgement); self.assertEqual(receipt['status'], 'acknowledged'); self.assertTrue(self.desk.acknowledge_intake_extraction(acknowledgement)['replayed'])
        self.assertIsNone(self.desk.status()['structured_extraction']['hold'])
        with self.assertRaises(HandoffConflict): self.desk.prepare_intake_proposal(body)
        self.assertEqual(len(self.calls), 1)

    def test_live_generation_cannot_be_acknowledged_from_another_desk(self):
        packet = self.packet(); other = ClientHandoffDesk(self.desk.registry, self.store); outcomes = []
        def generate(snapshot):
            hold = other.status()['structured_extraction']['hold']
            with self.assertRaises(HandoffConflict): other.acknowledge_intake_extraction({'request_id':hold['request_id'], 'expected_revision':other.status()['revision'], 'receipt_sha256':hold['receipt_sha256'], 'inspection_confirmed':True})
            outcomes.append(True); return self.generate(snapshot)
        self.desk.bind_structured_intake(generate, lambda: IDENTITY); self.proposal(packet); self.assertEqual(outcomes, [True])

    def test_crashed_running_reservation_can_only_be_acknowledged_not_adopted(self):
        packet = self.packet()
        def crash(snapshot): raise SystemExit('Synthetic crash before response')
        self.desk.bind_structured_intake(crash, lambda: IDENTITY)
        with self.assertRaises(SystemExit): self.desk.prepare_intake_proposal(self.body(packet_id=packet['id'], content_sha256=packet['content_sha256']))
        hold = self.desk.status()['structured_extraction']['hold']; self.assertEqual(hold['status'], 'running')
        with self.desk.database() as db: self.assertEqual(db.execute('SELECT count(*) FROM client_handoff_structured_proposals').fetchone()[0], 0)
        self.desk.acknowledge_intake_extraction({'request_id':hold['request_id'], 'expected_revision':self.desk.status()['revision'], 'receipt_sha256':hold['receipt_sha256'], 'inspection_confirmed':True})
        self.assertIsNone(self.desk.status()['structured_extraction']['hold'])

    def test_os_lock_independently_refuses_acknowledgement(self):
        packet = self.packet()
        self.desk.bind_structured_intake(lambda snapshot: (_ for _ in ()).throw(ValueError('Synthetic failure')), lambda: IDENTITY)
        with self.assertRaises(ValueError): self.proposal(packet)
        hold = self.desk.status()['structured_extraction']['hold']; lock = InstanceLock(self.store.path.parent/'structured-intake.lock'); lock.acquire()
        try:
            with self.assertRaises(ValueError): self.desk.acknowledge_intake_extraction({'request_id':hold['request_id'], 'expected_revision':self.desk.status()['revision'], 'receipt_sha256':hold['receipt_sha256'], 'inspection_confirmed':True})
        finally: lock.release()
        self.assertEqual(self.desk.status()['structured_extraction']['hold']['status'], 'failed')

    def test_pause_and_stale_client_during_callback_publish_no_proposal(self):
        for change in ('pause', 'client'):
            with self.subTest(change=change):
                packet = self.packet(TEXT+' '+change)
                def generate(snapshot):
                    answer = self.generate(snapshot)
                    if change == 'pause': self.store.set_setting('paused', True)
                    else: self.desk.save_client(self.body(client={'id':'service-a','name':'Changed Service','address':'1 Example Lane'}))
                    return answer
                self.desk.bind_structured_intake(generate, lambda: IDENTITY)
                with self.assertRaises(HandoffConflict): self.proposal(packet)
                self.store.set_setting('paused', False)
                hold = self.desk.status()['structured_extraction']['hold']; self.assertEqual(hold['status'], 'failed')
                self.desk.acknowledge_intake_extraction({'request_id':hold['request_id'], 'expected_revision':self.desk.status()['revision'], 'receipt_sha256':hold['receipt_sha256'], 'inspection_confirmed':True})
                with self.desk.database() as db: self.assertEqual(db.execute('SELECT count(*) FROM client_handoff_structured_proposals').fetchone()[0], 0)

    def test_model_change_during_generation_fails_closed(self):
        packet = self.packet()
        def generate(snapshot):
            answer = self.generate(snapshot); self.descriptor['model'] = 'different-model:local'; return answer
        self.desk.bind_structured_intake(generate, lambda: dict(self.descriptor))
        with self.assertRaises(HandoffConflict): self.proposal(packet)
        self.assertEqual(self.desk.status()['structured_extraction']['hold']['status'], 'failed')

    def test_reserve_is_enforced_for_other_entity_and_receipt_writes(self):
        packet = self.packet(); observed = []
        def generate(snapshot):
            from ariadne import client_handoff as module
            with self.desk.database() as db:
                total = sum(db.execute('SELECT coalesce(sum(bytes),0) FROM '+name).fetchone()[0] for name in ('client_handoff_clients','client_handoff_intakes','client_handoff_versions',*structured.TABLES,'client_handoff_inbox','client_handoff_intake_sources'))
                used = db.execute('SELECT coalesce(sum(receipt_bytes),0) FROM client_handoff_requests').fetchone()[0]
            with patch.object(module, 'MAX_ENTITY_BYTES', total+module.MAX_ENTITY):
                with self.assertRaises(ValueError): self.desk.save_client(self.body(client={'id':'extra','name':'Extra Service','address':'2 Example Lane'}))
            with patch.object(module, 'MAX_RECEIPT_BYTES', used+module.MAX_ENTITY):
                with self.assertRaises(ValueError): self.desk.save_client(self.body(client={'id':'extra','name':'Extra Service','address':'2 Example Lane'}))
            observed.append(True); return self.generate(snapshot)
        self.desk.bind_structured_intake(generate, lambda: IDENTITY); self.proposal(packet); self.assertEqual(observed,[True])

    def test_usage_float_or_wrong_source_fails_without_saved_proposal(self):
        packet = self.packet()
        def generate(snapshot):
            answer = self.generate(snapshot); answer['usage']['duration_ms'] = 3.5; return answer
        self.desk.bind_structured_intake(generate, lambda: IDENTITY)
        with self.assertRaises(ValueError): self.proposal(packet)
        self.assertEqual(self.desk.status()['structured_extraction']['hold']['status'], 'failed')

    def test_corrupted_model_claims_and_review_link_fail_closed(self):
        packet = self.packet(); proposal = self.proposal(packet)
        receipt = self.accept(packet, proposal)
        with self.desk.database() as db:
            row = db.execute('SELECT value FROM client_handoff_structured_intakes WHERE intake_id=?',(receipt['entity_id'],)).fetchone(); value=json.loads(row[0]); value['model']='fabricated'; raw=_json(value)
            db.execute('UPDATE client_handoff_structured_intakes SET value=?,bytes=? WHERE intake_id=?',(raw,len(raw.encode()),receipt['entity_id']))
        with self.assertRaises(ValueError): self.desk.get_intake(receipt['entity_id'])
        with self.desk.database() as db:
            row=db.execute('SELECT value FROM client_handoff_structured_proposals WHERE id=?',(proposal['id'],)).fetchone(); value=json.loads(row[0]); value['candidates'][0]['value']='Invented'; raw=_json(value)
            db.execute('UPDATE client_handoff_structured_proposals SET value=?,sha256=?,bytes=? WHERE id=?',(raw,_digest(value),len(raw.encode()),proposal['id']))
        with self.assertRaises(ValueError): self.desk.get_intake_proposal(proposal['id'])

    def test_remote_or_unbound_provider_and_preclaim_quota_do_not_generate(self):
        packet = self.packet(); self.descriptor['provider']='https://example.test:443'
        self.assertFalse(self.desk.status()['structured_extraction']['available'])
        with self.assertRaises(ValueError): self.proposal(packet)
        self.assertEqual(self.calls, []); self.descriptor=dict(IDENTITY)
        with patch('ariadne.client_handoff.MAX_RECEIPT_BYTES', 1):
            with self.assertRaises(ValueError): self.proposal(packet)
        self.assertEqual(self.calls, []); self.assertIsNone(self.desk.status()['structured_extraction']['hold'])


if __name__ == '__main__': unittest.main()
