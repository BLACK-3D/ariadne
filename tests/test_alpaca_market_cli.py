"""Isolated data-only command fixtures; no live market, broker or owner access."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from scripts import alpaca_market as cli
from ariadne.options_workflow import DEFAULT
from ariadne import options_feed_qualification as bridge
from test_options_feed_qualification import capture,supplement,CONTRACT,WATCH
from test_options_bankroll_eod import state as state6

AT=datetime(2026,11,3,15,0,0,100000,tzinfo=timezone.utc)


class AlpacaMarketCLITests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/'runtime/tmp';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='alpaca-cli-',dir=parent)
        self.addCleanup(self.temp.cleanup);self.addCleanup(patch.stopall)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.workspace=self.data/'workspace'
        (self.workspace/'imports').mkdir(parents=True)
        self.path=self.data/'ariadne.sqlite3';self.brief=self.data/'briefing.sqlite3'
        cfg={**DEFAULT,'enabled':True,'revision':1,'activation_at':'2026-10-30T12:00:00Z'}
        self.policy={'global_pause_rowid':1,'options_revision':1,'brief_revision':13}
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO settings VALUES(?,?)',('paused','false'))
            db.execute('INSERT INTO settings VALUES(?,?)',('internet_access','{"enabled":true,"revision":5}'))
            db.execute('CREATE TABLE opt_meta(id INTEGER PRIMARY KEY,revision INTEGER,config TEXT)')
            db.execute('INSERT INTO opt_meta VALUES(1,4,?)',(json.dumps(cfg),))
            db.execute('CREATE TABLE opt_accounts(id TEXT,state TEXT,policy TEXT,closed_at TEXT)')
            db.execute('INSERT INTO opt_accounts VALUES(?,?,?,NULL)',('fictional-shared-account',bridge.canonical(state6()).decode(),json.dumps(self.policy)))
            db.execute('CREATE TABLE opt_runs(request_id TEXT,status TEXT)')
            db.execute('CREATE TABLE qqq_runs(request_id TEXT,status TEXT)')
        with closing(sqlite3.connect(self.brief)) as db,db:
            db.execute('CREATE TABLE briefing_settings(id INTEGER PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO briefing_settings VALUES(1,?)',(json.dumps({'enabled':True,'paused':False,'revision':13,'timezone':'America/Chicago'}),))
            db.execute('CREATE TABLE briefing_runs(request_id TEXT,day TEXT,status TEXT)')
        self.put('imports/watch.json',{'schema':1,'instruments':WATCH})
        self.put('imports/metadata.json',supplement())
        self.calls=[];outer=self
        class FakeClient:
            def __init__(self,key,secret,online):
                outer.assertEqual((key,secret,online),('fictional-key','fictional-secret',True));outer.calls.append('client')
            def quotes(self,instruments):
                outer.assertEqual(instruments,WATCH);outer.calls.append('quotes');return capture()
            def close(self):outer.calls.append('closed')
        self.factory=FakeClient
        self.load=patch('ariadne.alpaca_credentials.load',return_value=('fictional-key','fictional-secret')).start()
        patch('ariadne.network_policy.network_policy_status',return_value={'mode':'online'}).start()
        patch.dict(os.environ,{'ARIADNE_OFFLINE':'0'}).start()
        patch('socket.create_connection',side_effect=AssertionError('No sockets')).start()
        binding=self.runcli('binding')
        self.request={**binding,'request_id':'fictional-qualification','requests':[
            {'request_id':'candidate-one','contract_id':CONTRACT,'side':'buy','quantity':2,'price_limit':'1.04','allow_partial':False}]}
        self.put('imports/request.json',self.request)

    def put(self,path,value):
        (self.workspace/path).write_text(json.dumps(value),encoding='utf-8')

    def runcli(self,*args,**kwargs):
        return cli.run(cli.parser().parse_args(args),root=self.root,client_factory=kwargs.get('factory',self.factory),now=kwargs.get('now',AT))

    def qualify(self,output='reports/alpaca-one',**kwargs):
        return self.runcli('qualify','--online','--instruments','imports/watch.json','--request','imports/request.json',
            '--supplement','imports/metadata.json','--output',output,**kwargs)

    def dbbytes(self):return self.path.read_bytes(),self.brief.read_bytes()

    def test_status_and_binding_are_passive_no_key_unlock_or_network(self):
        before=self.dbbytes();status=self.runcli('status');self.runcli('binding')
        self.assertEqual(before,self.dbbytes());self.load.assert_not_called();self.assertEqual(self.calls,[])
        self.assertEqual(status['current_cash'],'25000');self.assertFalse(status['paper_submission_ready'])
        self.assertFalse(status['retained_financial_history_verified'])

    def test_capture_and_qualification_publish_hash_checked_evidence_without_financial_writes(self):
        before=self.dbbytes();result=self.qualify();self.assertEqual(before,self.dbbytes())
        self.assertEqual(self.calls,['client','quotes','closed'])
        folder=self.workspace/'reports/alpaca-one';receipt=json.loads((folder/'completed.json').read_bytes())
        self.assertFalse(receipt['paper_submission_ready']);self.assertEqual(receipt['paper_fills'],0)
        self.assertFalse(receipt['ledger_write_performed']);self.assertEqual(receipt['funding_added'],'0')
        for row in receipt['artifacts']:
            raw=(folder/row['path']).read_bytes()
            self.assertEqual(len(raw),row['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),row['sha256'])
            self.assertNotIn(b'fictional-secret',raw);self.assertNotIn(b'fictional-key',raw)
        saved=json.loads((folder/'qualification.json').read_bytes())
        self.assertEqual(saved['admission_candidates'],1);self.assertFalse(saved['proposals'][0]['paper_fill'])
        self.assertEqual(result['output'],'reports/alpaca-one')

    def test_prior_complete_or_uncertain_output_refuses_before_keys(self):
        for suffix in ('complete','incomplete'):
            path=self.workspace/('reports/alpaca-'+suffix);path.mkdir(parents=True)
            if suffix=='complete':(path/'completed.json').write_text('{}')
            with self.assertRaises(ValueError):self.qualify('reports/alpaca-'+suffix)
        self.load.assert_not_called();self.assertEqual(self.calls,[])

    def test_malformed_request_metadata_and_stale_binding_fail_before_key_network(self):
        for change in ('revision','clock','metadata','unknown_contract'):
            with self.subTest(change=change):
                request=deepcopy(self.request);sup=supplement()
                if change=='revision':request['expected_revision']=0
                elif change=='clock':request['requests'][0]['decision_at']='2026-11-03T15:00:00Z'
                elif change=='metadata':sup['contracts'][0]['quote_at']='2026-11-03T15:00:00Z'
                else:request['requests'][0]['contract_id']='F261120C00010000'
                self.put('imports/request.json',request);self.put('imports/metadata.json',sup)
                with self.assertRaises(ValueError):self.qualify()
        self.load.assert_not_called();self.assertEqual(self.calls,[])

    def test_global_and_workflow_hold_reject_qualification(self):
        before=self.dbbytes()
        with closing(sqlite3.connect(self.path)) as db,db:db.execute("INSERT INTO opt_runs VALUES('fictional-failed','failed')")
        held=self.dbbytes()
        with self.assertRaises(ValueError):self.qualify()
        self.assertEqual(held,self.dbbytes());self.load.assert_not_called();self.assertEqual(self.calls,[])
        self.assertNotEqual(before,held)

    def test_collect_can_inspect_quotes_with_qqq_hold_without_altering_it(self):
        with closing(sqlite3.connect(self.path)) as db,db:db.execute("INSERT INTO qqq_runs VALUES('old-held','failed')")
        before=self.dbbytes()
        self.runcli('collect','--online','--instruments','imports/watch.json','--output','reports/alpaca-inspect')
        self.assertEqual(before,self.dbbytes())
        result=self.qualify()
        self.assertIn('qqq_workflow_hold_preserved',result['blockers']);self.assertEqual(before,self.dbbytes())

    def test_network_pause_or_inherited_offline_refuses_before_key(self):
        with patch.dict(os.environ,{'ARIADNE_OFFLINE':'1'}):
            with self.assertRaises(ValueError):self.qualify()
        with closing(sqlite3.connect(self.path)) as db,db:db.execute("UPDATE settings SET value='true' WHERE key='paused'")
        with self.assertRaises(ValueError):self.qualify()
        self.load.assert_not_called();self.assertEqual(self.calls,[])

    def test_account_or_permission_change_during_capture_prevents_publication(self):
        outer=self
        class Moving(self.factory):
            def quotes(self,instruments):
                value=super().quotes(instruments)
                with closing(sqlite3.connect(outer.path)) as db,db:db.execute('UPDATE opt_meta SET revision=revision+1')
                return value
        with self.assertRaises(ValueError):self.qualify(factory=Moving)
        self.assertFalse((self.workspace/'reports/alpaca-one').exists());self.assertIn('closed',self.calls)

    def test_wrong_entitlement_transport_marker_is_refused(self):
        class Wrong(self.factory):
            def quotes(self,instruments):
                value=super().quotes(instruments);value['transport']['options_feed']='indicative';return value
        with self.assertRaises(ValueError):self.qualify(factory=Wrong)
        self.assertFalse((self.workspace/'reports/alpaca-one').exists())

    def test_recorded_clock_cannot_precede_capture_receipt(self):
        with self.assertRaises(ValueError):self.qualify(now=datetime(2026,11,3,15,0,tzinfo=timezone.utc))
        self.assertFalse((self.workspace/'reports/alpaca-one').exists())

    def test_setup_requires_hidden_interactive_input(self):
        with patch.object(cli.sys.stdin,'isatty',return_value=False),patch.object(cli.getpass,'getpass') as prompt:
            with self.assertRaises(ValueError):self.runcli('setup')
        prompt.assert_not_called();self.assertEqual(self.calls,[])

    def test_invalid_output_path_refused_before_network(self):
        for path in ('../outside','reports/other','reports/alpaca-name/child','C:/outside'):
            with self.assertRaises(ValueError):self.qualify(path)
        self.load.assert_not_called()
