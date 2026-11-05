"""Lifecycle and owner-control tests; no native imports, credentials or feeds."""
from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts import theta_direct as cli

ROOT = Path(__file__).resolve().parents[1]


class Child:
    def __init__(self):
        self.polls = 0
        self.returncode = 0
    def poll(self):
        self.polls += 1
        return None if self.polls == 1 else self.returncode


class Job:
    latest = None
    fail = False
    def __init__(self):
        Job.latest = self
        self.closed = False
        self.handle = 1
        self.closed_handles = []
        def close_handle(handle):
            self.closed_handles.append(handle)
            return True
        self.kernel = type('Kernel', (), {'CloseHandle':staticmethod(close_handle)})()
        self.command = self.kwargs = None
    def popen(self, command, **kwargs):
        self.command, self.kwargs = command, kwargs
        if Job.fail:
            raise OSError('fictional launch failure')
        return Child()
    def close(self):
        self.closed = True


class TriggeredTimer:
    """Fire on another thread while a tested operation is definitely blocked."""
    latest = None
    def __init__(self, interval, callback):
        TriggeredTimer.latest = self
        self.callback = callback
        self.gate = threading.Event()
        self.fired = threading.Event()
        self.cancelled = threading.Event()
        self.thread = threading.Thread(target=self._run,daemon=True)
    def _run(self):
        if self.gate.wait(2) and not self.cancelled.is_set():
            self.callback()
            self.fired.set()
    def start(self):self.thread.start()
    def cancel(self):self.cancelled.set();self.gate.set()
    def join(self):self.thread.join(2)
    @property
    def ident(self):return self.thread.ident


class DirectCLITests(unittest.TestCase):
    def setUp(self):
        parent = ROOT/'runtime/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='theta-supervisor-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'data/workspace').mkdir(parents=True)
        db = sqlite3.connect(self.root/'data/ariadne.sqlite3')
        db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        db.executemany('INSERT INTO settings VALUES(?,?)', (
            ('paused', 'false'), ('internet_access', '{"enabled":true,"revision":1}')))
        db.commit(); db.close()
        sources = ('scripts/theta_direct.py','scripts/windows_host.py','staging/test/worker.py')
        pins = {}
        for name in sources:
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# fictional inert test source\n')
            pins[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        for name, raw in (('runtime/python/python.exe',b'not an executable'),
                          ('staging/test/amendment.json',b'{"fictional":true}')):
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.profile = {'worker':'staging/test/worker.py','binding':'staging/test/binding.json',
            'home':'staging/test/home','ordinal':2,'source_sha256':pins,
            'asset_review_sha256':'a'*64,'protocol_amendment':'staging/test/amendment.json',
            'protocol_amendment_sha256':hashlib.sha256(b'{"fictional":true}').hexdigest(),
            'python_sha256':hashlib.sha256(b'not an executable').hexdigest(),
            'selection':{'kind':'stock','symbol':'F','start_date':'2025-11-05','end_date':'2026-11-04'},
            'allowed_symbols':['F'],'research_window':{'fictional':True},
            'registered_at':'2026-11-05T20:00:00+00:00',
            'owner_policy_sha256':cli.owner_policy(cli._guard(self.root))}
        for target, value in (
            ('scripts.windows_host.ServiceJob', Job),
            ('scripts.windows_host.process_identity', lambda pid: {'pid':pid,'fictional':True}),
            ('ariadne.network_policy.network_policy_status', lambda: {'mode':'online'}),
            ('scripts.theta_direct.time.sleep', lambda seconds: None)):
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'ARIADNE_OFFLINE':'0', 'HTTPS_PROXY':'fictional-private-proxy',
            'GRPC_TRACE':'all','SSLKEYLOGFILE':'fictional-keylog','THETADATA_API_KEY':'FICTIONAL_NO_REAL_KEY'}, clear=False)
        p.start(); self.addCleanup(p.stop)
        Job.latest = None; Job.fail = False

    def invoke(self):
        return cli.supervise(self.profile, root=self.root)

    def test_owned_worker_hidden_stdio_and_environment(self):
        result = self.invoke()
        job = Job.latest
        self.assertTrue(job.closed)
        self.assertEqual(job.command[1:3], ['-I','-B'])
        self.assertEqual(job.command[-2:], ['2','staging/test/binding.json'])
        self.assertEqual(job.kwargs['creationflags'], 0x08000000)
        for name in ('stdin','stdout','stderr'):
            self.assertEqual(job.kwargs[name], subprocess.DEVNULL)
        for name in ('HTTPS_PROXY','GRPC_TRACE','SSLKEYLOGFILE','THETADATA_API_KEY','PYTHONPATH'):
            self.assertNotIn(name, job.kwargs['env'])
        for name in ('HOME','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA'):
            self.assertTrue(Path(job.kwargs['env'][name]).is_relative_to(self.root))
        binding = json.loads((self.root/self.profile['binding']).read_bytes())
        self.assertEqual(binding['maximum_seconds'],300)
        self.assertEqual(binding['deadline_monotonic']-binding['started_monotonic'],300)
        self.assertTrue(result['owned_worker_exit_verified'])
        self.assertFalse(result['worker_evidence_verified'])
        self.assertFalse(result['automatic_retry'])

    def test_source_mutation_during_request_closes_job(self):
        def mutate(seconds):
            (self.root/'staging/test/worker.py').write_bytes(b'# changed')
        with patch.object(cli.time,'sleep',mutate), self.assertRaises(cli.SupervisionError):
            self.invoke()
        self.assertTrue(Job.latest.closed)

    def test_permission_revocation_closes_job(self):
        original = cli.owner_policy
        calls = []
        def permission(box):
            calls.append(None)
            return original(box) if len(calls)<3 else 'revoked'
        with patch.object(cli,'owner_policy',permission), self.assertRaises(cli.SupervisionError):
            self.invoke()
        self.assertTrue(Job.latest.closed)

    def test_binding_tamper_closes_job(self):
        def mutate(seconds):
            (self.root/self.profile['binding']).write_bytes(b'{}')
        with patch.object(cli.time,'sleep',mutate), self.assertRaises(cli.SupervisionError):
            self.invoke()
        self.assertTrue(Job.latest.closed)

    def test_deadline_closes_job(self):
        calls = 0
        def clock():
            nonlocal calls
            calls += 1
            return 0 if calls<9 else 301
        with patch.object(cli.time,'monotonic',clock), self.assertRaises(cli.SupervisionError):
            self.invoke()
        self.assertTrue(Job.latest.closed)

    def test_launch_failure_closes_job(self):
        Job.fail = True
        with self.assertRaises(OSError):
            self.invoke()
        self.assertTrue(Job.latest.closed)

    def test_independent_watchdog_expires_during_blocked_source_check(self):
        original = cli._pins
        calls = 0
        def blocked(box, values):
            nonlocal calls
            calls += 1
            if calls == 2:
                TriggeredTimer.latest.gate.set()
                self.assertTrue(TriggeredTimer.latest.fired.wait(2))
                self.assertEqual(Job.latest.closed_handles,[1])
            original(box,values)
        with patch.object(cli.threading,'Timer',TriggeredTimer),patch.object(cli,'_pins',blocked), \
                self.assertRaises(cli.SupervisionError):
            self.invoke()
        self.assertTrue(Job.latest.closed)
        self.assertIsNone(Job.latest.command)
        self.assertEqual(Job.latest.closed_handles,[1])

    def test_independent_watchdog_expires_during_blocked_launch(self):
        original = Job.popen
        def blocked(job, command, **kwargs):
            TriggeredTimer.latest.gate.set()
            self.assertTrue(TriggeredTimer.latest.fired.wait(2))
            self.assertEqual(job.closed_handles,[1])
            return original(job,command,**kwargs)
        with patch.object(cli.threading,'Timer',TriggeredTimer),patch.object(Job,'popen',blocked), \
                self.assertRaises(cli.SupervisionError):
            self.invoke()
        self.assertTrue(Job.latest.closed)
        self.assertEqual(Job.latest.closed_handles,[1])

    def test_forbidden_system_root_rejected_without_access(self):
        with patch.dict(os.environ,{'SystemRoot':'E:\\Windows'}), \
                patch('ariadne.config._allowed_path',side_effect=AssertionError('must not resolve E:')), \
                self.assertRaises(cli.SupervisionError):
            cli._environment(self.root,self.root/'staging/test/home')

    def test_existing_binding_is_not_replaced_or_replayed(self):
        (self.root/self.profile['binding']).write_bytes(b'prior uncertain registration')
        with self.assertRaises(FileExistsError):
            self.invoke()
        self.assertIsNone(Job.latest)
        self.assertEqual((self.root/self.profile['binding']).read_bytes(),b'prior uncertain registration')

    def test_changed_pinned_source_prevents_launch(self):
        (self.root/'scripts/windows_host.py').write_bytes(b'changed')
        with self.assertRaises(cli.SupervisionError):
            self.invoke()
        self.assertIsNone(Job.latest)

    def test_unknown_profile_fields_refused(self):
        self.profile['api_key'] = 'fictional'
        with self.assertRaises(cli.SupervisionError):
            self.invoke()
        self.assertIsNone(Job.latest)

    def test_additional_reviewed_ordinal_preserves_its_identity(self):
        self.profile['ordinal'] = 5
        result = self.invoke()
        self.assertEqual(Job.latest.command[-2:], ['5','staging/test/binding.json'])
        binding = json.loads((self.root/self.profile['binding']).read_bytes())
        self.assertEqual(binding['ordinal'],5)
        self.assertEqual(result['ordinal'],5)
        self.assertFalse(result['automatic_retry'])
        self.assertFalse(result['worker_evidence_verified'])

    def test_unreviewed_ordinals_never_launch_a_worker(self):
        for ordinal in (1,6,0,-1,True,'5'):
            with self.subTest(ordinal=ordinal):
                self.profile['ordinal'] = ordinal
                with self.assertRaises(cli.SupervisionError):
                    self.invoke()
                self.assertIsNone(Job.latest)

    def test_paused_owner_prevents_launch(self):
        with closing(sqlite3.connect(self.root/'data/ariadne.sqlite3')) as db:
            db.execute("UPDATE settings SET value='true' WHERE key='paused'")
            db.commit()
        with self.assertRaises(cli.SupervisionError):
            self.invoke()
        self.assertIsNone(Job.latest)

    def test_missing_owner_database_is_not_created(self):
        path = self.root/'data/ariadne.sqlite3'
        path.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            cli.owner_policy(cli._guard(self.root))
        self.assertFalse(path.exists())

    def test_inspection_cli_has_no_authentication_action_or_key_option(self):
        for args in (['capture'],['status','--api-key','fictional-private-value']):
            with self.assertRaises(cli.SupervisionError) as caught:
                cli.parser().parse_args(args)
            self.assertNotIn('fictional-private-value',str(caught.exception))

    def test_status_does_not_decrypt_or_import_native_dependencies(self):
        with patch('ariadne.theta_direct.asset_status',return_value={'fictional':True}), \
                patch('ariadne.theta_terminal.load_key',side_effect=AssertionError('must not decrypt')):
            result = cli.run(cli.parser().parse_args(['status']))
        self.assertFalse(result['credential_store_read'])
        self.assertFalse(result['authentication_performed'])
        self.assertEqual(result['paper_fills'],0)

    def test_main_rejects_private_arguments_without_echo(self):
        out, err = StringIO(),StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            self.assertEqual(cli.main(['status','--api-key','fictional-private-value']),2)
        self.assertNotIn('fictional-private-value',out.getvalue()+err.getvalue())


if __name__ == '__main__':
    unittest.main()
