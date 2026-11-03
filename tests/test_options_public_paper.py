"""Isolated same-account Public paper transitions; fictional quote bytes only."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from ariadne.handoff import HandoffConflict
from ariadne.options_workflow import _json
from test_options_bankroll_workflow import BankrollWorkflowTests
from test_options_public_engine import AT, book


def capture(value=None):
    b=book() if value is None else value
    payload={'quotes':[{'instrument':{'symbol':b['contract_id'],'type':'OPTION'},'outcome':'SUCCESS',
        'bid':b['bid'],'ask':b['ask'],'bidSize':b['bid_size'],'askSize':b['ask_size'],
        'bidTimestamp':b['quote_at'],'askTimestamp':b['quote_at'],'volume':1000,'openInterest':2000},
        {'instrument':{'symbol':'KO','type':'EQUITY'},'outcome':'SUCCESS','last':'79.90',
        'lastTimestamp':b['quote_at'],'bid':'79.89','ask':'79.91','bidTimestamp':b['quote_at'],'askTimestamp':b['quote_at']}]}
    return {'payload':_json(payload).encode(),'received_at':b['received_at'],
        'transport':{'provider_authenticated':True,'source_id':'public','operation':'quotes'}}


def supplement(value=None):
    b=book() if value is None else value
    fields=('contract_id','underlying_type','standard_deliverable_verified','deliverable_verification_source','underlying_activity','contract_activity','open_interest')
    return {'schema':1,'contracts':[{k:deepcopy(b[k]) for k in fields}]}


class PublicPaperWorkflowTests(unittest.TestCase):
    setUp=BankrollWorkflowTests.setUp
    body=BankrollWorkflowTests.body
    ready=BankrollWorkflowTests.ready
    paper=BankrollWorkflowTests.paper
    put=BankrollWorkflowTests.put
    source=BankrollWorkflowTests.source
    baseline=BankrollWorkflowTests.baseline
    collect=BankrollWorkflowTests.collect
    ledger=BankrollWorkflowTests.ledger
    database_dump=BankrollWorkflowTests.database_dump
    active_raw=BankrollWorkflowTests.active_raw
    prepare=BankrollWorkflowTests.prepare
    activate=BankrollWorkflowTests.activate

    def prepare_public(self):
        self.prepare();self.activate();self.service=self.owner.public_paper
        self.clock=datetime(2026,11,3,14,59,tzinfo=timezone.utc)
        self.old_account=deepcopy(self.paper())
        with self.owner.database() as db:binding=self.service.binding(db,self.paper())
        self.migration_body=self.body(acknowledge_simulated=True,acknowledge_no_topups=True,**binding)
        return self.service.migrate(self.migration_body,self.clock)

    def request_body(self, **changes):
        with self.owner.database() as db:binding=self.service.binding(db,self.paper())
        request={'request_id':'candidate-one','contract_id':book()['contract_id'],'side':'buy','quantity':2,'price_limit':'1.04','allow_partial':False}
        request.update(changes)
        return self.body(requests=[request],**binding)

    def execute(self,body=None,**kwargs):
        self.clock=datetime.fromisoformat(AT)
        return self.service.execute(body or self.request_body(),capture=capture(),now=self.clock,**kwargs)

    def test_migration_is_same_account_zero_added_funding_and_replay_idempotent(self):
        receipt=self.prepare_public();after=self.paper()
        self.assertEqual(after['id'],self.old_account['id']);self.assertEqual(after['state']['schema'],7)
        self.assertEqual(after['state']['cash'],self.old_account['state']['cash'])
        self.assertEqual(after['state']['contributions'],'25000');self.assertEqual(receipt['simulated_funding_added'],'0')
        before=self.database_dump();replay=self.service.migrate(self.migration_body,self.clock)
        self.assertTrue(replay['replayed']);self.assertEqual(before,self.database_dump())
        with self.owner.database() as db:self.assertEqual(db.execute('SELECT count(*) FROM opt_accounts').fetchone()[0],1)

    def test_active_server_blocks_migration_before_any_reservation(self):
        from ariadne.locking import InstanceLock
        self.prepare();self.activate();self.service=self.owner.public_paper
        self.clock=datetime(2026,11,3,14,59,tzinfo=timezone.utc)
        with self.owner.database() as db:binding=self.service.binding(db,self.paper())
        body=self.body(acknowledge_simulated=True,acknowledge_no_topups=True,**binding)
        before=self.database_dump();guard=InstanceLock(self.settings.data_dir/'server.lock')
        guard.acquire()
        try:
            with self.assertRaisesRegex(HandoffConflict,'Stop Ariadne'):
                self.service.migrate(body,self.clock)
            self.assertEqual(before,self.database_dump())
            self.assertEqual(self.paper()['state']['schema'],6)
        finally:guard.release()

    def test_server_guard_is_held_through_publication_and_released_after(self):
        from ariadne.locking import InstanceLock
        publish=self.owner._publish;seen=[]
        def check_guard(*args,**kwargs):
            if args[2].get('action')=='public_migrate':
                probe=InstanceLock(self.settings.data_dir/'server.lock')
                with self.assertRaises(ValueError):probe.acquire()
                final=kwargs['final']
                def checked_final(db):
                    with self.assertRaises(ValueError):probe.acquire()
                    seen.append('commit');return final(db)
                kwargs['final']=checked_final
            return publish(*args,**kwargs)
        with patch.object(self.owner,'_publish',side_effect=check_guard):self.prepare_public()
        self.assertEqual(seen,['commit'])
        probe=InstanceLock(self.settings.data_dir/'server.lock');probe.acquire();probe.release()

    def test_legacy_forward_quality_is_never_current_in_public_policy(self):
        self.prepare_public();quality={'provider':'old-date-only-fixture'}
        with patch.object(self.owner,'_info',side_effect=lambda db,clock,forward=False:{'quality':quality,'sha256':'1'*64} if forward else None):
            value=self.owner.status(self.clock)
        self.assertIsNone(value['forward_quality']);self.assertEqual(value['legacy_forward_quality'],quality)
        self.assertEqual(value['paper_readiness']['paper_schema'],7)
        self.assertFalse(value['paper_readiness']['entry_qualified'])
        self.assertEqual(value['paper_readiness']['diagnostic_status'],'requires_current_qualified_capture')

    def test_missing_supplement_records_no_fill_and_preserves_money(self):
        self.prepare_public();before=self.paper()['state'];receipt=self.execute();after=self.paper()['state']
        self.assertEqual(receipt['paper_fills'],0);self.assertEqual(after['cash'],before['cash'])
        self.assertEqual(after['contributions'],before['contributions']);self.assertEqual(after['fees'],before['fees'])
        self.assertEqual(after['processed_count'],1)
        saved=self.service.verify(receipt);self.assertIn('standard_deliverable_unverified',json.loads(saved['paper.json'])['proposals'][0]['reason_codes'])

    def test_actual_quote_based_fill_close_and_same_quote_not_reusable(self):
        self.prepare_public();receipt=self.execute(supplement=supplement())
        self.assertEqual(receipt['paper_fills'],1);self.assertEqual(self.paper()['state']['cash'],'24790.70')
        receipt=self.execute(self.request_body(side='sell',price_limit='1'),supplement=supplement())
        self.assertEqual(receipt['paper_fills'],1);self.assertEqual(self.paper()['state']['cash'],'24989.40')
        self.execute(supplement=supplement());self.assertEqual(self.paper()['state']['cash'],'24780.10')
        # Book has100 displayed so another2 contracts may legitimately consume
        # residual depth. Exhaustion and side-identity behavior are kernel tests.
        with self.owner.database() as db:self.service.verify_account(db,self.paper()['id'],self.paper()['state'],full=True)

    def test_completed_frame_replay_never_moves_money_twice(self):
        self.prepare_public();body=self.request_body();receipt=self.execute(body,supplement=supplement());before=self.database_dump()
        replay=self.execute(body,supplement=supplement())
        self.assertTrue(replay['replayed']);self.assertEqual(replay['state_sha256'],receipt['state_sha256'])
        self.assertEqual(before,self.database_dump())

    def test_permissions_revision_and_global_pause_preflight_before_capture(self):
        self.prepare_public();body=self.request_body();before=self.paper()['state']
        self.controls['paused']=True
        with self.assertRaises(HandoffConflict):self.service.preflight(body)
        self.controls['paused']=False;self.controls['revision']+=1
        with self.assertRaises(HandoffConflict):self.service.preflight(body)
        self.assertEqual(before,self.paper()['state'])

    def test_boolean_epoch_not_accepted_as_matching_integer(self):
        self.prepare_public();body=self.request_body();body['expected_owner_epoch']['brief_revision']=True
        with self.assertRaises(ValueError):self.service.preflight(body)

    def test_state_source_and_receipt_tampering_are_detected(self):
        self.prepare_public();receipt=self.execute(supplement=supplement())
        path=self.registry._path(next(a['path'] for a in receipt['artifacts'] if a['path'].endswith('/paper.json')))
        before=path.read_bytes();path.write_bytes(before.replace(b'24790.70',b'24790.71'))
        with self.assertRaises(ValueError):self.paper()
        path.write_bytes(before)
        with self.owner.database() as db:db.execute("UPDATE opt_bankroll_state SET state_sha256=?",('0'*64,))
        with self.assertRaises(ValueError):self.paper()

    def test_late_publish_revocation_rolls_back_money_but_retains_failed_receipt(self):
        self.prepare_public();before=self.paper()['state'];publish=self.owner._publish
        def revoke(*args,**kwargs):
            self.controls['revision']+=1
            return publish(*args,**kwargs)
        with patch.object(self.owner,'_publish',side_effect=revoke):
            with self.assertRaises(HandoffConflict):self.execute(supplement=supplement())
        self.assertEqual(before,self.paper()['state'])
        with self.owner.database() as db:self.assertEqual(db.execute("SELECT count(*) FROM opt_runs WHERE action='public_paper' AND status='failed'").fetchone()[0],1)

    def test_imported_unverified_capture_cannot_assert_transport(self):
        self.prepare_public();value=capture();value['transport']['provider_authenticated']=False
        with self.assertRaises(ValueError):self.service.execute(self.request_body(),capture=value,now=datetime.fromisoformat(AT))
        self.assertEqual(self.paper()['state']['processed_count'],0)

    def test_supplement_cannot_change_actual_volume_or_oi(self):
        self.prepare_public();extra=supplement();extra['contracts'][0]['open_interest']['value']=5000
        with self.assertRaises(ValueError):self.execute(supplement=extra)
        self.assertEqual(self.paper()['state']['processed_count'],0)

    def test_daily_journal_is_passive_saved_same_account_results(self):
        from ariadne.options_journal import review_options
        self.prepare_public();self.execute(supplement=supplement());before=self.database_dump()
        value=review_options(self.owner,datetime.fromisoformat(AT))
        self.assertEqual(value['account_schema'],7);self.assertEqual(value['performance']['cash'],'24790.70')
        self.assertEqual(value['performance']['entries'],1);self.assertEqual(value['network_calls'],0)
        self.assertFalse(value['automatic_topups']);self.assertEqual(before,self.database_dump())

    def test_old_due_collector_stays_inactive_after_new_policy(self):
        self.prepare_public();before=self.database_dump()
        with patch.object(self.owner.eod.stress,'capture_due',side_effect=AssertionError('Sealed old collector')):
            self.assertIsNone(self.owner.collect_due(self.clock))
        self.assertEqual(before,self.database_dump())

    def test_status_retains_total_bankroll_and_public_policy(self):
        self.prepare_public();value=self.owner.status(self.clock)
        self.assertTrue(value['public_paper']['active']);self.assertEqual(value['paper']['summary']['bankroll_total'],'25000')
        self.assertIn('$25000',value['scope']);self.assertNotIn('$500 monthly',value['scope'])


if __name__=='__main__':unittest.main()
