"""Evidence inbox: offline parsing, exact provenance, atomic review and bounded queue."""
import base64
import copy
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

from ariadne import client_inbox as inbox
from ariadne.client_handoff import ClientHandoffDesk
from ariadne.config import Settings, _allowed_path
from ariadne.handoff import HandoffConflict, _digest, _json
from ariadne.store import Store
from ariadne.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 2, 16, tzinfo=timezone.utc)
TEXT = b'Title: Window repair\nCustomer: Example Customer\nCustomer address: 20 Example Lane\nService date: 2026-11-01\nDocument date: 2026-11-01\nDue date: 2026-11-10\nScope: Repair window latch\nQuantity: 2.5\nUnit: hours\nRate: 35.00\n'


def profile():
    return {"id": "client-a", "name": "Example Service LLC", "address": "10 Example Lane"}


def facts():
    return {"title": "Example job", "source_notes": "Owner supplied reviewed facts.",
            "customer": {"name": "Example Customer", "email": None, "address": "20 Example Lane"},
            "service_date": "2026-11-01", "document_date": "2026-11-01", "due_date": "2026-11-10",
            "lines": [{"description": "Repair", "quantity": "1", "unit": "job", "unit_price": "35.00", "price_approved": True, "source_ref": "Owner rate sheet"}],
            "tax": {"bps": 0, "approved": True, "source_ref": "Owner tax instruction"},
            "permission": {"confirmed": True, "source_ref": "Owner permission observation"}}


class ClientInboxTests(unittest.TestCase):
    def setUp(self):
        parent = _allowed_path(ROOT / 'runtime/tmp')
        parent.mkdir(parents=True, exist_ok=True)
        fixture = tempfile.TemporaryDirectory(prefix='client-inbox-unit-', dir=parent)
        self.base = _allowed_path(fixture.name)
        self.addCleanup(fixture.cleanup)
        patch = mock.patch('ariadne.client_handoff._now', return_value=NOW)
        patch.start(); self.addCleanup(patch.stop)
        settings = Settings(data_dir=self.base / 'private', workspace=self.base / 'workspace')
        self.store = Store(settings.data_dir / 'ariadne.sqlite3')
        self.desk = ClientHandoffDesk(ToolRegistry(settings), self.store)
        self.sequence = 0

    def body(self, **fields):
        self.sequence += 1
        return {"request_id": "req-" + str(self.sequence), "expected_revision": self.desk.status()["revision"], **fields}

    def call(self, method, **fields):
        return getattr(self.desk, method)(self.body(**fields))

    def provider(self):
        self.call('save_client', client=profile())

    def prepare(self, raw=TEXT, filename='request.txt'):
        result = self.call('prepare_intake_packet', client_id='client-a', filename=filename, content_base64=base64.b64encode(raw).decode('ascii'))
        return self.desk.get_intake_packet(result['entity_id'])['packet']

    def consume(self, packet, value=None):
        return self.call('accept_intake_packet', packet_id=packet['id'], content_sha256=packet['content_sha256'], review_confirmed=True, intake=value or packet['proposed_intake'])

    def test_passive_reads_do_not_create_workflow_tables(self):
        with self.store.connect() as db:
            before = list(db.execute('SELECT name,sql FROM sqlite_master ORDER BY name'))
        value = self.desk.next_actions(now=NOW)
        self.assertFalse(value['initialized']); self.assertEqual(value['items'], [])
        self.assertEqual(self.desk.status()['inbox']['packets'], [])
        with self.assertRaises(ValueError): self.desk.get_intake_packet('missing')
        with self.store.connect() as db:
            self.assertEqual([tuple(r) for r in before], [tuple(r) for r in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')])

    def test_exact_source_snapshot_and_conservative_candidates_no_network(self):
        self.provider()
        with mock.patch.object(socket, 'socket', side_effect=AssertionError('No network')), mock.patch('subprocess.Popen', side_effect=AssertionError('No model/process')):
            packet = self.prepare()
        self.assertEqual(packet['raw_sha256'], hashlib.sha256(TEXT).hexdigest())
        self.assertEqual(packet['text_sha256'], hashlib.sha256(packet['text'].encode()).hexdigest())
        for anchor in packet['source_lines'] + packet['candidates']:
            self.assertEqual(packet['text'][anchor['start']:anchor['end']], anchor['quote'])
            self.assertEqual(hashlib.sha256(anchor['quote'].encode()).hexdigest(), anchor['quote_sha256'])
        self.assertEqual(packet['proposed_intake']['lines'][0]['unit_price'], '35.00')
        self.assertFalse(packet['proposed_intake']['lines'][0]['price_approved'])
        self.assertFalse(packet['proposed_intake']['tax']['approved'])
        self.assertFalse(packet['proposed_intake']['permission']['confirmed'])
        self.assertNotIn('page', packet['source_lines'][0])

    def test_conflicting_labels_stay_unresolved(self):
        self.provider(); packet = self.prepare(TEXT + b'Customer: Different Customer\n')
        self.assertIsNone(packet['proposed_intake']['customer']['name'])
        self.assertNotIn('customer.name', [c['field'] for c in packet['candidates']])

    def test_review_creates_exactly_one_held_intake_and_separate_source_link(self):
        self.provider(); packet = self.prepare()
        body = self.body(packet_id=packet['id'], content_sha256=packet['content_sha256'], review_confirmed=True, intake=facts())
        result = self.desk.accept_intake_packet(body)
        replay = self.desk.accept_intake_packet(body)
        self.assertTrue(replay['replayed']); self.assertEqual(replay['entity_id'], result['entity_id'])
        intake = self.desk.get_intake(result['entity_id'])['intake']
        self.assertFalse(intake['lines'][0]['price_approved']); self.assertFalse(intake['tax']['approved']); self.assertFalse(intake['permission']['confirmed'])
        self.assertEqual(intake['intake_packet']['facts_sha256'], result['facts_sha256'])
        self.assertEqual(intake['intake_packet']['packet_content_sha256'], packet['content_sha256'])
        self.assertEqual(self.desk.get_intake_packet(packet['id'])['packet']['content_sha256'], packet['content_sha256'])
        with self.assertRaises(HandoffConflict): self.consume(packet)
        self.assertEqual(self.desk.status()['totals']['intakes'], 1)
        quote = self.call('generate_quote', intake_id=result['entity_id'], content_sha256=result['content_sha256'])
        document = self.desk.get_document(quote['entity_id'])['document']
        self.assertEqual(document['status'], 'held')
        self.assertEqual(document['intake_packet']['packet_id'], packet['id'])
        with self.assertRaises(HandoffConflict): self.call('review', document_id=document['id'], content_sha256=document['content_sha256'], decision='approve')

    def test_replay_preparation_does_not_reparse_and_new_request_deduplicates(self):
        self.provider()
        body = self.body(client_id='client-a', filename='request.txt', content_base64=base64.b64encode(TEXT).decode())
        first = self.desk.prepare_intake_packet(body)
        with mock.patch.object(inbox.documents, 'extract_document', side_effect=AssertionError('No replay parsing')):
            self.assertTrue(self.desk.prepare_intake_packet(body)['replayed'])
        second = self.call('prepare_intake_packet', client_id='client-a', filename='request.txt', content_base64=base64.b64encode(TEXT).decode())
        self.assertTrue(second['duplicate_source']); self.assertEqual(first['entity_id'], second['entity_id'])
        self.assertEqual(self.desk.status()['inbox']['total'], 1)
        changed = {**body, 'filename': 'different.txt'}
        with self.assertRaises(HandoffConflict): self.desk.prepare_intake_packet(changed)

    def test_admission_pause_and_cas_before_extraction(self):
        self.provider(); body = self.body(client_id='client-a', filename='request.txt', content_base64=base64.b64encode(TEXT).decode())
        with mock.patch.object(inbox.documents, 'extract_document', side_effect=AssertionError('No parsing')):
            self.store.set_setting('paused', True)
            with self.assertRaises(HandoffConflict): self.desk.prepare_intake_packet(body)
            self.store.set_setting('paused', False)
            with self.assertRaises(HandoffConflict): self.desk.prepare_intake_packet({**body, 'expected_revision': 0})
        self.assertEqual(self.desk.status()['inbox']['total'], 0)

    def test_profile_changes_during_parse_reject_without_packet(self):
        self.provider(); original = inbox.documents.extract_document
        def extract(*args):
            self.call('save_client', client={**profile(), 'name': 'Changed profile'})
            return original(*args)
        with mock.patch.object(inbox.documents, 'extract_document', side_effect=extract), self.assertRaises(HandoffConflict): self.prepare()
        self.assertEqual(self.desk.status()['inbox']['total'], 0)

    def test_stale_or_archived_profile_blocks_consume(self):
        self.provider(); packet = self.prepare()
        self.call('save_client', client={**profile(), 'archived': True})
        self.assertTrue(self.desk.get_intake_packet(packet['id'])['packet']['client_changed'])
        with self.assertRaises(HandoffConflict): self.consume(packet)
        self.assertEqual(self.desk.status()['totals']['intakes'], 0)

    def test_wrong_hash_review_or_extra_intake_fields_rejected(self):
        self.provider(); packet = self.prepare()
        for changes in ({'content_sha256': '0' * 64}, {'review_confirmed': False}, {'intake': {**facts(), 'id': 'unauthorized-id'}}):
            body = self.body(packet_id=packet['id'], content_sha256=packet['content_sha256'], review_confirmed=True, intake=facts())
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.desk.accept_intake_packet({**body, **changes})
        self.assertEqual(self.desk.status()['totals']['intakes'], 0)

    def test_atomic_accept_receipt_failure_rolls_back_everything(self):
        self.provider(); packet = self.prepare(); revision = self.desk.status()['revision']
        with mock.patch.object(self.desk, '_save_receipt', side_effect=OSError('Synthetic write fault')), self.assertRaises(OSError): self.consume(packet)
        self.assertEqual(self.desk.status()['revision'], revision)
        self.assertEqual(self.desk.status()['totals']['intakes'], 0)
        self.assertIsNone(self.desk.get_intake_packet(packet['id'])['packet']['consumed_intake_id'])
        with self.desk.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM client_handoff_intake_sources').fetchone()[0], 0)

    def test_upload_validation_and_unreadable_text_do_not_mutate(self):
        self.provider(); revision = self.desk.status()['revision']
        for filename, content in [('path/name.txt', 'YQ=='), ('x.html', 'YQ=='), ('x.txt', 'YQ=\n='), ('x.txt', 'YR=='), ('x.txt', base64.b64encode(b' \n\t').decode()), ('x.txt', base64.b64encode(b'a' * (inbox.MAX_UPLOAD + 1)).decode())]:
            with self.subTest(filename=filename, size=len(content)), self.assertRaises(ValueError):
                self.call('prepare_intake_packet', client_id='client-a', filename=filename, content_base64=content)
        self.assertEqual(self.desk.status()['revision'], revision)
        self.assertEqual(self.desk.status()['inbox']['total'], 0)

    def test_truncation_is_explicit_and_utf8_bounded(self):
        self.provider(); packet = self.prepare(('Title: Example\n' + '界' * 20000).encode())
        self.assertTrue(packet['truncated']); self.assertLessEqual(len(packet['text']), 10000)
        self.assertLessEqual(len(packet['text'].encode()), 24000)
        self.assertTrue(any('truncated' in w for w in packet['warnings']))

    def test_eml_plaintext_claims_and_html_ignored(self):
        self.provider()
        message = EmailMessage(); message['From'] = 'Example <sender@example.invalid>'; message['Subject'] = 'Repair request'
        message.set_content('Scope: Repair the latch\nCustomer: Example Customer\n')
        message.add_alternative('<script>fetch("https://example.invalid/")</script>', subtype='html')
        packet = self.prepare(message.as_bytes(), 'request.eml')
        self.assertNotIn('<script>', packet['text']); self.assertNotIn('fetch(', packet['text'])
        self.assertEqual(packet['proposed_intake']['customer']['email'], 'sender@example.invalid')
        self.assertEqual(packet['processor']['identity'], 'eml-v1')
        self.assertTrue(any('not verified' in w for w in packet['warnings']))

    def test_eml_attachments_and_html_only_rejected(self):
        self.provider()
        attachment = EmailMessage(); attachment.set_content('Example body'); attachment.add_attachment(b'payload', maintype='application', subtype='octet-stream', filename='x.bin')
        html = EmailMessage(); html.set_content('<p>Example</p>', subtype='html')
        for message in (attachment, html):
            with self.subTest(message=message.get_content_type()), self.assertRaises(ValueError): self.prepare(message.as_bytes(), 'request.eml')
        self.assertEqual(self.desk.status()['inbox']['total'], 0)

    def test_mime_factory_stops_excess_parts_before_construction_or_candidates(self):
        self.provider()
        boundary = b'bound-mime-test'
        raw = b'Content-Type: multipart/mixed; boundary="' + boundary + b'"\r\n\r\n'
        raw += b''.join(b'--' + boundary + b'\r\nContent-Type: text/plain\r\n\r\nPart text\r\n' for _ in range(120))
        raw += b'--' + boundary + b'--\r\n'
        original = inbox.EmailMessage.__init__
        calls = []
        def initialize(message, *args, **kwargs):
            calls.append(1)
            original(message, *args, **kwargs)
        with mock.patch.object(inbox.EmailMessage, '__init__', initialize), mock.patch.object(inbox, '_suggest', side_effect=AssertionError('No candidates before bounded parse')), self.assertRaises(ValueError):
            self.prepare(raw, 'many-parts.eml')
        self.assertEqual(len(calls), 40)
        self.assertEqual(self.desk.status()['inbox']['total'], 0)

    def test_mime_factory_bounds_deep_recursion_and_postparse_depth(self):
        self.provider()
        for depth in (12, 100):
            raw = b'Content-Type: message/rfc822\r\n\r\n' * depth + b'Content-Type: text/plain\r\n\r\nExample body\r\n'
            original = inbox.EmailMessage.__init__
            calls = []
            def initialize(message, *args, **kwargs):
                calls.append(1)
                original(message, *args, **kwargs)
            with self.subTest(depth=depth), mock.patch.object(inbox.EmailMessage, '__init__', initialize), mock.patch.object(inbox, '_suggest', side_effect=AssertionError('No candidates')), self.assertRaises(ValueError):
                self.prepare(raw, 'deep-message.eml')
            self.assertLessEqual(len(calls), 40)
        self.assertEqual(self.desk.status()['inbox']['total'], 0)

    def test_mime_object_counter_is_request_local_after_failure(self):
        raw = b'Content-Type: message/rfc822\r\n\r\n' * 100 + b'Content-Type: text/plain\r\n\r\nExample body\r\n'
        with self.assertRaises(ValueError): inbox._eml(raw)
        normal = b'Subject: Example\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nScope: Ordinary request\r\n'
        for _ in range(30):
            result = inbox._eml(normal)
            self.assertIn('Scope: Ordinary request', result['text'])

    def test_packet_hash_corruption_fails_closed(self):
        self.provider(); packet = self.prepare()
        with self.desk.database() as db: db.execute("UPDATE client_handoff_inbox SET sha256=?", ('0' * 64,))
        with self.assertRaises(ValueError): self.desk.get_intake_packet(packet['id'])

    def test_consumption_metadata_and_provenance_are_consistent(self):
        self.provider(); packet = self.prepare(); saved = self.consume(packet)
        with self.desk.database() as db:
            row = db.execute('SELECT value FROM client_handoff_intake_sources').fetchone()
            from ariadne.handoff import _load
            value = _load(row[0]); value['accepted_at'] = '2025-01-01T00:00:00+00:00'; raw = _json(value)
            db.execute('UPDATE client_handoff_intake_sources SET value=?,bytes=?', (raw, len(raw.encode())))
        with self.assertRaises(ValueError): self.desk.get_intake(saved['entity_id'])
        with self.assertRaises(ValueError): self.desk.get_intake_packet(packet['id'])

    def test_unmatched_consumed_packet_is_not_hidden_as_resolved(self):
        self.provider(); packet = self.prepare()
        with self.desk.database() as db: db.execute('UPDATE client_handoff_inbox SET consumed_intake_id=?', ('missing-intake',))
        with self.assertRaises(ValueError): self.desk.next_actions(now=NOW)

    def test_packet_quota_rolls_back_and_counts_with_other_entities(self):
        self.provider(); revision = self.desk.status()['revision']
        with mock.patch.object(inbox, 'MAX_PACKETS', 0), self.assertRaises(ValueError): self.prepare()
        self.assertEqual(self.desk.status()['revision'], revision)
        with mock.patch('ariadne.client_handoff.MAX_ENTITY_BYTES', 1000), self.assertRaises(ValueError): self.prepare()
        self.assertEqual(self.desk.status()['inbox']['total'], 0)

    def test_next_actions_bounded_windows_and_no_false_payment_claims(self):
        self.provider(); packet = self.prepare(); self.consume(packet)
        self.prepare(TEXT + b'\nAdditional evidence\n', 'second.txt')
        first = self.desk.next_actions(limit=1, now=NOW)
        self.assertEqual(first['pagination']['scanned'], 1); self.assertTrue(first['truncated'])
        self.assertEqual(first['pagination']['next_offset'], 1)
        second = self.desk.next_actions(limit=1, offset=1, now=NOW)
        self.assertEqual(second['pagination']['scanned'], 1)
        self.assertFalse(any(row['kind'] == 'payment_overdue' for row in first['items'] + second['items']))
        for fields in ({'limit': True}, {'offset': -1}, {'now': datetime(2026, 1, 1)}):
            with self.assertRaises(ValueError): self.desk.next_actions(**fields)

    def test_queue_independent_upper_offset_boundary_and_strict_integer_types(self):
        for initialized in (False, True):
            if initialized:
                self.provider()
            for offset in (20001, 32000):
                with self.subTest(initialized=initialized, offset=offset):
                    result = self.desk.next_actions(offset=offset, now=NOW)
                    self.assertEqual(result['pagination']['offset'], offset)
                    self.assertEqual(result['pagination']['scanned'], 0)
                    self.assertEqual(result['items'], [])
            for offset in (32001, True, False, 0.0, '0', None):
                with self.subTest(initialized=initialized, bad_offset=offset), self.assertRaises(ValueError):
                    self.desk.next_actions(offset=offset, now=NOW)
            for limit in (True, False, 0, 51, 1.0, '1', None):
                with self.subTest(initialized=initialized, bad_limit=limit), self.assertRaises(ValueError):
                    self.desk.next_actions(limit=limit, now=NOW)
        with self.assertRaises(ValueError):
            self.desk.status(document_offset=20001)

    def test_expired_quote_uses_workflow_timezone_but_accepted_quote_can_complete(self):
        self.provider(); source = facts(); source['due_date'] = '2026-11-01'
        saved = self.call('create_intake', intake={'id': 'manual-job', 'client_id': 'client-a', **source})
        quote = self.call('generate_quote', intake_id='manual-job', content_sha256=saved['content_sha256'])
        self.call('review', document_id=quote['entity_id'], content_sha256=quote['content_sha256'], decision='approve')
        early = datetime(2026, 11, 2, 5, tzinfo=timezone.utc)  # Nov 1 in Chicago after DST switch.
        self.assertIn('customer_acceptance', [a['kind'] for a in self.desk.next_actions(now=early)['items']])
        self.assertIn('expired_quote', [a['kind'] for a in self.desk.next_actions(now=NOW)['items']])
        self.call('accept_quote', document_id=quote['entity_id'], content_sha256=quote['content_sha256'], accepted=True,
                  observed_at='2026-11-01T18:00:00+00:00', source_ref='Genuine earlier customer reply', contact='Example Customer')
        self.assertIn('completion_evidence', [a['kind'] for a in self.desk.next_actions(now=NOW)['items']])
        self.assertNotIn('expired_quote', [a['kind'] for a in self.desk.next_actions(now=NOW)['items']])


if __name__ == '__main__':
    unittest.main()
