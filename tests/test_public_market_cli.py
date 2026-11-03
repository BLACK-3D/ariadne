"""Isolated Public command checks. All prices/credentials are fictional, no sockets."""
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from scripts import public_market as cli
from ariadne.config import _allowed_path


class PublicMarketCLITests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / 'runtime/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='public-cli-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root/'data'
        self.workspace = self.data/'workspace'
        self.workspace.mkdir(parents=True)
        (self.workspace/'imports').mkdir()
        self.path = self.data/'ariadne.sqlite3'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO settings VALUES(?,?)', ('paused', 'false'))
            db.execute('INSERT INTO settings VALUES(?,?)', ('internet_access', '{"enabled":true,"revision":1}'))
            db.execute('CREATE TABLE opt_accounts(id TEXT,state TEXT,closed_at TEXT)')
            db.execute('INSERT INTO opt_accounts VALUES(?,?,NULL)', ('one-account', '{"schema":6,"cash":"25000","contributions":"25000"}'))
        self.watch = {'schema':1, 'instruments':[{'symbol':'F', 'type':'EQUITY'}, {'symbol':'F261218P00010000','type':'OPTION'}]}
        self.put('imports/watch.json', self.watch)
        self.addCleanup(patch.stopall)
        patch('socket.create_connection', side_effect=AssertionError('No test network')).start()
        self.calls = []
        outer = self
        class FakeClient:
            def __init__(self, secret, *, online):
                outer.assertEqual(secret, 'fictional-local-secret')
                outer.assertTrue(online)
                outer.calls.append('client')
            def quotes(self, account, instruments):
                outer.assertEqual(account, 'fictional-account')
                outer.calls.append('quotes')
                rows = [{'instrument':i, 'outcome':'SUCCESS', 'bid':'1.00', 'ask':'1.02',
                         'bidSize':20, 'askSize':20, 'bidTimestamp':'2026-11-03T15:00:00Z',
                         'askTimestamp':'2026-11-03T15:00:00Z', 'last':'1.01',
                         'lastTimestamp':'2026-11-03T15:00:00Z', 'volume':1000, 'openInterest':2000} for i in instruments]
                return {'payload':json.dumps({'quotes':rows}).encode(), 'received_at':'2026-11-03T15:00:01Z',
                        'transport':{'source_id':'public','operation':'quotes','provider_authenticated':True}}
            def accounts(self):
                outer.calls.append('accounts')
                return [{'account_id':'fictional-account','account_type':'BROKERAGE'}]
            def close(self):
                outer.calls.append('closed')
        self.factory = FakeClient
        patch.dict(os.environ, {'ARIADNE_PUBLIC_SECRET':'fictional-local-secret', 'ARIADNE_PUBLIC_ACCOUNT_ID':'fictional-account'}).start()

    def put(self, name, value):
        (self.workspace/name).write_text(json.dumps(value), encoding='utf-8')

    def args(self, *values):
        return cli.parser().parse_args(values)

    def runcli(self, *values, **kwargs):
        return cli.run(self.args(*values), root=self.root, client_factory=kwargs.get('factory', self.factory))

    def test_status_is_passive_and_never_unlocks_or_connects(self):
        before = self.path.read_bytes()
        result = self.runcli('status')
        self.assertFalse(result['credential_configured'])
        self.assertFalse(result['live_feed_qualified'])
        self.assertEqual(result['paper_account_schema'], 6)
        self.assertFalse(result['state_projection_verified'])
        self.assertEqual(result['funding_added'],'0')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_collect_exclusive_saved_output_preserves_account_and_hashes(self):
        before = self.path.read_bytes()
        result = self.runcli('collect','--online','--instruments','imports/watch.json','--output','reports/public-one')
        folder = self.workspace/'reports/public-one'
        receipt = json.loads((folder/'completed.json').read_bytes())
        self.assertEqual(receipt['paper_fills'], 0)
        self.assertFalse(receipt['live_feed_qualified'])
        for item in receipt['artifacts']:
            raw = (folder/item['path']).read_bytes()
            self.assertEqual(len(raw), item['bytes'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item['sha256'])
            self.assertNotIn(b'fictional-local-secret', raw)
            self.assertNotIn(b'fictional-account', raw)
        self.assertEqual(self.path.read_bytes(), before)
        calls = list(self.calls)
        with self.assertRaises(ValueError):
            self.runcli('collect','--online','--instruments','imports/watch.json','--output','reports/public-one')
        self.assertEqual(self.calls, calls)

    def test_global_pause_and_internet_disabled_block_before_auth(self):
        for key, value in [('paused','true'), ('internet_access','{"enabled":false,"revision":2}')]:
            with self.subTest(key=key):
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute('UPDATE settings SET value=? WHERE key=?',(value,key))
                with self.assertRaises(ValueError):
                    self.runcli('collect','--online','--instruments','imports/watch.json','--output','reports/public-blocked')
                self.assertEqual(self.calls, [])
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute('UPDATE settings SET value=? WHERE key=?', ('false' if key=='paused' else '{"enabled":true,"revision":3}',key))

    def test_revocation_during_collection_never_publishes(self):
        factory = self.factory
        outer = self
        class Revoke(factory):
            def quotes(self,*args):
                result=super().quotes(*args)
                with closing(sqlite3.connect(outer.path)) as db, db:
                    db.execute("UPDATE settings SET value='true' WHERE key='paused'")
                return result
        with self.assertRaises(ValueError):
            self.runcli('collect','--online','--instruments','imports/watch.json','--output','reports/public-revoked',factory=Revoke)
        self.assertFalse((self.workspace/'reports/public-revoked').exists())
        self.assertEqual(self.calls, ['client','quotes','closed'])

    def test_bad_watchlist_and_paths_refused_before_network(self):
        for watch in [{'schema':True,'instruments':[]}, {'schema':1,'instruments':[{'symbol':'F','type':'EQUITY'},{'symbol':'F','type':'EQUITY'}]}]:
            self.put('imports/bad.json',watch)
            with self.assertRaises(ValueError):
                self.runcli('collect','--online','--instruments','imports/bad.json','--output','reports/public-bad')
        for name in ('../escape','imports/overwrite','reports/../../outside'):
            with self.assertRaises(ValueError):
                self.runcli('collect','--online','--instruments','imports/watch.json','--output',name)
        self.assertEqual(self.calls, [])

    def test_missing_explicit_online_never_connects(self):
        for command in [('accounts',),('collect','--instruments','imports/watch.json','--output','reports/x')]:
            with self.assertRaises(ValueError):self.runcli(*command)
        self.assertEqual(self.calls, [])

    def test_accounts_print_minimal_local_selection_without_saving(self):
        result = self.runcli('accounts','--online')
        self.assertEqual(result['accounts'],[{'account_id':'fictional-account','account_type':'BROKERAGE'}])
        self.assertFalse(result['saved'])
        self.assertFalse((self.data/'public-market').exists())
        self.assertEqual(self.calls,['client','accounts','closed'])

    def test_inherited_offline_refusal_and_hidden_prompt_fail_closed(self):
        with patch.dict(os.environ, {'ARIADNE_OFFLINE':'1'}), patch('builtins.print'):
            self.assertEqual(cli.main(['accounts','--online']),2)
        with patch('sys.stdin.isatty',return_value=False), patch('getpass.getpass') as prompt:
            with self.assertRaises(ValueError):cli._hidden('secret')
            prompt.assert_not_called()

    def test_unsupported_arguments_and_missing_owner_never_create_bankroll(self):
        for command in [('status','--online'),('setup','--online'),('status','--request','imports/watch.json'),('migrate','--output','reports/x')]:
            with self.assertRaises(ValueError):self.runcli(*command)
        self.path.unlink()
        with self.assertRaises(ValueError):self.runcli('status')
        self.assertFalse(self.path.exists())
        self.assertEqual(self.calls,[])


if __name__ == '__main__':unittest.main()
