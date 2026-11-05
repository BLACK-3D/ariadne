"""Fictional local-Terminal bridge checks; no Terminal, credentials or sockets."""
from copy import deepcopy
from contextlib import closing
import json
import sqlite3
import unittest
from scripts import theta_market as cli
from ariadne import theta_market_data as feed
from ariadne import options_feed_qualification as bridge
from test_alpaca_market_cli import AlpacaMarketCLITests,AT
from test_options_feed_qualification import WATCH,CONTRACT,supplement


def capture(naive=False):
    stamp='2026-11-03T09:00:00.000' if naive else '2026-11-03T15:00:00.000000123Z'
    def part(stock=False):
        instrument={'type':'EQUITY','symbol':'KO'} if stock else {'type':'OPTION','symbol':CONTRACT}
        row={'symbol':'KO','timestamp':stamp,'bid':79.85 if stock else 1,'ask':79.90 if stock else 1.04,
            'bid_size':20,'ask_size':20,'bid_exchange':1,'ask_exchange':2,'bid_condition':0,'ask_condition':0}
        if not stock:row.update(expiration='2026-11-20',strike=80,right='call')
        return {'instrument':instrument,'payload':json.dumps([row]),'received_at':'2026-11-03T15:00:00.050000Z'}
    return {'schema':1,'source_id':feed.SOURCE_ID,'received_at':'2026-11-03T15:00:00.050000Z',
        'options':[part()],'stocks':[part(True)],'transport':{'source_id':feed.SOURCE_ID,
            'operation':'latest_quotes','provider_authenticated':False,'local_terminal_verified':False,
            'kind':'local_terminal_unattested','options_feed':'opra_unattested','stocks_feed':'nasdaq_basic_unattested'}}


class ThetaMarketCLITests(unittest.TestCase):
    def setUp(self):
        AlpacaMarketCLITests.setUp(self)
        outer=self
        class FakeClient:
            def __init__(self,online):outer.assertTrue(online);outer.calls.append('client')
            def quotes(self,requested):outer.assertEqual(requested,WATCH);outer.calls.append('quotes');return capture()
            def close(self):outer.calls.append('closed')
        self.factory=FakeClient
    put=AlpacaMarketCLITests.put
    dbbytes=AlpacaMarketCLITests.dbbytes

    def runcli(self,*args,**kwargs):
        return cli.run(cli.parser().parse_args(args),root=self.root,client_factory=kwargs.get('factory',self.factory),now=kwargs.get('now',AT))

    def qualify(self,output='reports/theta-one',**kwargs):
        return self.runcli('qualify','--online','--instruments','imports/watch.json','--request','imports/request.json',
            '--supplement','imports/metadata.json','--output',output,**kwargs)

    def test_status_is_passive_unattested_without_key_unlock_or_terminal(self):
        before=self.dbbytes();value=self.runcli('status')
        self.assertEqual(value['source_id'],'theta_terminal_v3');self.assertEqual(value['guide'],'docs/THETA_PAPER_DATA.md')
        self.assertIsNone(value['credential_configured']);self.assertFalse(value['paper_submission_ready'])
        self.assertEqual(self.calls,[]);self.load.assert_not_called();self.assertEqual(before,self.dbbytes())

    def test_collect_retains_unattested_loopback_and_never_reads_credentials(self):
        before=self.dbbytes()
        self.runcli('collect','--online','--instruments','imports/watch.json','--output','reports/theta-capture')
        receipt=json.loads((self.workspace/'reports/theta-capture/completed.json').read_bytes())
        self.assertFalse(receipt['transport_observed_by_command']['provider_authenticated'])
        self.assertFalse(receipt['transport_observed_by_command']['local_terminal_verified'])
        self.assertFalse(receipt['paper_submission_ready']);self.assertEqual(receipt['paper_fills'],0)
        self.load.assert_not_called();self.assertEqual(before,self.dbbytes())

    def test_qualified_fixture_never_converts_unattested_terminal_to_fill(self):
        before=self.dbbytes();result=self.qualify()
        self.assertIn('local_terminal_provenance_unattested',result['blockers'])
        saved=json.loads((self.workspace/'reports/theta-one/qualification.json').read_bytes())
        self.assertEqual(saved['admission_candidates'],1);self.assertEqual(saved['paper_fills'],0)
        self.assertFalse(saved['paper_submission_ready']);self.assertFalse(saved['provider_authenticated'])
        self.assertEqual(before,self.dbbytes());self.load.assert_not_called()

    def test_naive_source_timezone_never_inferred_from_pc_or_receipt(self):
        class Naive(self.factory):
            def quotes(self,requested):super().quotes(requested);return capture(naive=True)
        self.qualify(factory=Naive)
        saved=json.loads((self.workspace/'reports/theta-one/qualification.json').read_bytes())
        self.assertEqual(saved['admission_candidates'],0)
        self.assertIn('actual_quote_clock_missing',saved['proposals'][0]['reason_codes'])
        self.assertIn('source_timestamp_timezone_unverified',saved['provider_blockers'][0]['reasons'])

    def test_forged_terminal_authentication_flag_refused(self):
        class Forged(self.factory):
            def quotes(self,requested):
                value=super().quotes(requested);value['transport']['provider_authenticated']=True;return value
        with self.assertRaises(ValueError):self.qualify(factory=Forged)
        self.assertFalse((self.workspace/'reports/theta-one').exists())

    def test_qqq_hold_preserved_in_diagnostic(self):
        with closing(sqlite3.connect(self.path)) as db,db:db.execute("INSERT INTO qqq_runs VALUES('old-held','failed')")
        before=self.dbbytes();result=self.qualify()
        self.assertIn('qqq_workflow_hold_preserved',result['blockers']);self.assertEqual(before,self.dbbytes())

    test_malformed_request_metadata_and_stale_binding_fail_before_key_network=AlpacaMarketCLITests.test_malformed_request_metadata_and_stale_binding_fail_before_key_network
    test_global_and_workflow_hold_reject_qualification=AlpacaMarketCLITests.test_global_and_workflow_hold_reject_qualification
    test_network_pause_or_inherited_offline_refuses_before_key=AlpacaMarketCLITests.test_network_pause_or_inherited_offline_refuses_before_key
    def test_account_or_permission_change_during_capture_prevents_publication(self):
        outer=self
        class Moving(self.factory):
            def quotes(self,requested):
                value=super().quotes(requested)
                with closing(sqlite3.connect(outer.path)) as db,db:db.execute('UPDATE opt_meta SET revision=revision+1')
                return value
        with self.assertRaises(ValueError):self.qualify(factory=Moving)
        self.assertFalse((self.workspace/'reports/theta-one').exists());self.assertIn('closed',self.calls)

    def test_recorded_clock_cannot_precede_capture_receipt(self):
        from datetime import datetime,timezone
        with self.assertRaises(ValueError):self.qualify(now=datetime(2026,11,3,15,0,tzinfo=timezone.utc))
        self.assertFalse((self.workspace/'reports/theta-one').exists())
    test_invalid_output_path_refused_before_network=AlpacaMarketCLITests.test_invalid_output_path_refused_before_network
