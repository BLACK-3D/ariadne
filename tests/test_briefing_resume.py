"""Synthetic future resume controls; never opens the installed owner databases."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.briefing import BriefingService, BriefingConflict
from ariadne.config import Settings, _allowed_path
from ariadne.store import Store
from ariadne.tools import ToolRegistry

ROOT = Path(__file__).absolute().parents[1]


class BriefingResumeTests(unittest.TestCase):
    def setUp(self):
        parent = _allowed_path(ROOT / 'staging' / 'briefing-resume-fixtures')
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='resume-', dir=parent)
        self.root = Path(self.temporary.name)
        def cleanup():
            if not _allowed_path(self.root).is_relative_to(ROOT):
                raise ValueError('Refuse cleanup outside synthetic source fixture.')
            self.temporary.cleanup()
        self.addCleanup(cleanup)
        self.settings = Settings(data_dir=self.root / 'data', api_token='synthetic-resume-fixture')
        self.store = Store(self.settings.data_dir / 'ariadne.sqlite3')
        self.registry = ToolRegistry(self.settings)
        self.service = BriefingService(self.settings, self.store, self.registry)
        self.now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
        self.service.configure({'enabled': True, 'paused': True}, self.now - timedelta(days=1))

    def add_receipt(self, status='failed', *, day='2026-11-02'):
        request = 'daily-' + day
        raw = json.dumps({'ok': False, 'status': 'running' if status == 'interrupted' else status,
            'request_id': request, 'day': day, 'created_at': self.now.timestamp(), 'artifacts': [],
            'error': 'Synthetic failure receipt must remain unchanged'}, sort_keys=True)
        with self.service.database() as db:
            db.execute('INSERT INTO briefing_runs VALUES(?,?,?,?,?)', (request, day, status, self.now.timestamp(), raw))
        return request, raw

    def row(self, request):
        with self.service.database() as db:
            row = db.execute('SELECT day,status,receipt FROM briefing_runs WHERE request_id=?', (request,)).fetchone()
        return tuple(row)

    def acknowledgements(self):
        with self.service.database() as db:
            return [dict(row) for row in db.execute('SELECT * FROM briefing_failure_acknowledgements ORDER BY request_id')]

    def resume(self):
        return self.service.configure({'paused': False}, self.now, expected_revision=self.service.config()['revision'])

    def legacy_unpaused(self):
        config = self.service.config()
        config.update(paused=False, pause_reason=None)
        with self.service.database() as db:
            db.execute('UPDATE briefing_settings SET value=? WHERE id=1', (json.dumps(config),))
        return config

    def forbid_children(self, service=None):
        service = service or self.service
        stack = ExitStack()
        for owner, name in ((service.qqq, 'run_due'), (service.qqq, 'busy'), (service.options, 'run_due'),
                            (service.options, 'busy'), (service, 'run'), (service, 'collect'),
                            (service.venture, 'run_for_brief')):
            stack.enter_context(patch.object(owner, name, side_effect=AssertionError('No child work is authorized by an acknowledged day')))
        return stack

    def test_explicit_cas_resume_changes_only_brief_pause_and_binds_exact_receipt(self):
        request, raw = self.add_receipt()
        previous, old_row = self.service.config(), self.row(request)
        self.store.set_setting('paused', True)
        result = self.resume()
        self.assertEqual(self.service.config(), {**previous, 'paused': False, 'pause_reason': None, 'revision': previous['revision']+1})
        self.assertTrue(self.store.setting('paused'))
        self.assertTrue(result['autonomy_paused'])
        self.assertEqual(self.row(request), old_row)
        ack = self.acknowledgements()[0]
        self.assertEqual((ack['request_id'], ack['day'], ack['status']), (request, '2026-11-02', 'failed'))
        self.assertEqual(ack['receipt_sha256'], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(ack['config_revision'], previous['revision']+1)

    def test_acknowledged_failed_day_skips_before_any_child_and_never_repauses(self):
        request, _ = self.add_receipt(); before = self.row(request)
        self.resume(); revision = self.service.config()['revision']
        with self.forbid_children():
            self.assertIsNone(self.service.run_due(self.now))
            self.assertIsNone(self.service.run_due(self.now + timedelta(hours=2)))
        self.assertFalse(self.service.config()['paused'])
        self.assertEqual(self.service.config()['revision'], revision)
        self.assertEqual(self.row(request), before)

    def test_interrupted_day_can_be_acknowledged_without_adopting_running_receipt(self):
        request, _ = self.add_receipt('interrupted'); before = self.row(request)
        self.resume()
        with self.forbid_children():
            self.assertIsNone(self.service.run_due(self.now))
        self.assertEqual(self.row(request), before)
        self.assertEqual(self.acknowledgements()[0]['status'], 'interrupted')
        self.assertEqual(json.loads(before[2])['status'], 'running')

    def test_missing_or_stale_expected_revision_cannot_resume_or_acknowledge(self):
        request, _ = self.add_receipt(); previous = self.service.config(); before = self.row(request)
        for expected in (None, previous['revision']-1):
            with self.subTest(expected=expected), self.assertRaises(BriefingConflict):
                self.service.configure({'paused': False}, self.now, expected_revision=expected)
        self.assertEqual(self.service.config(), previous)
        self.assertEqual(self.row(request), before)
        self.assertEqual(self.acknowledgements(), [])

    def test_running_current_day_rejects_resume_without_any_adoption(self):
        request, _ = self.add_receipt('running'); previous = self.service.config(); before = self.row(request)
        with self.assertRaises(BriefingConflict):
            self.resume()
        self.assertEqual(self.service.config(), previous)
        self.assertEqual(self.row(request), before)
        self.assertEqual(self.acknowledgements(), [])

    def test_unacknowledged_failed_day_retains_fail_stop(self):
        request, _ = self.add_receipt(); before = self.row(request)
        self.legacy_unpaused()
        with self.forbid_children():
            self.assertIsNone(self.service.run_due(self.now))
        self.assertTrue(self.service.config()['paused'])
        self.assertEqual(self.row(request), before)
        self.assertEqual(self.acknowledgements(), [])

    def test_false_to_false_configuration_is_not_an_acknowledgement(self):
        self.add_receipt(); self.legacy_unpaused()
        self.service.configure({'paused': False}, self.now, expected_revision=self.service.config()['revision'])
        self.assertEqual(self.acknowledgements(), [])
        with self.forbid_children():
            self.service.run_due(self.now)
        self.assertTrue(self.service.config()['paused'])

    def test_changed_failure_bytes_repause_and_cannot_replace_old_acknowledgement(self):
        request, raw = self.add_receipt(); self.resume(); ack = self.acknowledgements()
        with self.service.database() as db:
            db.execute('UPDATE briefing_runs SET receipt=? WHERE request_id=?', (raw+' ', request))
        with self.forbid_children():
            self.assertIsNone(self.service.run_due(self.now))
        self.assertTrue(self.service.config()['paused'])
        previous = self.service.config()
        with self.assertRaises(BriefingConflict):
            self.resume()
        self.assertEqual(self.service.config(), previous)
        self.assertEqual(self.acknowledgements(), ack)
        self.assertEqual(self.row(request)[2], raw+' ')

    def test_mismatched_ack_day_status_or_future_revision_does_not_clear_failure(self):
        request, _ = self.add_receipt(); self.resume()
        original = self.acknowledgements()[0]
        for column, value in (('day', '2026-11-03'), ('status', 'interrupted'), ('config_revision', original['config_revision']+100)):
            with self.subTest(column=column):
                self.legacy_unpaused()
                with self.service.database() as db:
                    db.execute('UPDATE briefing_failure_acknowledgements SET '+column+'=? WHERE request_id=?', (value, request))
                with self.forbid_children():
                    self.assertIsNone(self.service.run_due(self.now))
                self.assertTrue(self.service.config()['paused'])
                with self.service.database() as db:
                    db.execute('UPDATE briefing_failure_acknowledgements SET '+column+'=? WHERE request_id=?', (original[column], request))

    def test_later_configuration_revision_does_not_erase_exact_acknowledgement(self):
        self.add_receipt(); self.resume(); ack = self.acknowledgements()
        self.service.configure({'hour': 7}, self.now, expected_revision=self.service.config()['revision'])
        with self.forbid_children():
            self.assertIsNone(self.service.run_due(self.now))
        self.assertFalse(self.service.config()['paused'])
        self.assertEqual(self.acknowledgements(), ack)

    def test_reopen_preserves_acknowledgement_and_failed_request_stays_unreplayable(self):
        request, _ = self.add_receipt(); self.resume(); before = self.row(request)
        reopened = BriefingService(self.settings, self.store, self.registry)
        with self.forbid_children(reopened):
            self.assertIsNone(reopened.run_due(self.now))
        with self.assertRaises(BriefingConflict):
            reopened.run(request, self.now)
        self.assertEqual(self.row(request), before)
        self.assertEqual(len(self.acknowledgements()), 1)

    def test_next_day_is_eligible_without_resetting_the_old_failure(self):
        request, _ = self.add_receipt(); self.resume(); before = self.row(request)
        with patch.object(self.service.qqq, 'run_due'), patch.object(self.service.qqq, 'busy', return_value=False), \
             patch.object(self.service.options, 'run_due'), patch.object(self.service.options, 'busy', return_value=False), \
             patch.object(self.service, 'run', return_value={'status': 'synthetic_next_day'}) as run:
            self.assertEqual(self.service.run_due(self.now+timedelta(days=1)), {'status': 'synthetic_next_day'})
            run.assert_called_once_with('daily-2026-11-03', self.now+timedelta(days=1), scheduled_day='2026-11-03', collect=True)
        self.assertEqual(self.row(request), before)

    def test_acknowledgement_does_not_clear_financial_execution_hold(self):
        self.add_receipt(); self.resume()
        cfg = self.service.config()
        with self.service.database() as db, patch('ariadne.briefing.datetime', wraps=datetime) as clock:
            clock.now.return_value = self.now
            self.assertTrue(self.service._qqq_brief_hold(db, cfg))

    def test_previous_day_failure_is_preserved_without_new_current_day_ack(self):
        request, _ = self.add_receipt(day='2026-11-01'); before = self.row(request)
        self.resume()
        self.assertFalse(self.service.config()['paused'])
        self.assertEqual(self.acknowledgements(), [])
        self.assertEqual(self.row(request), before)

    def test_two_day_old_failure_resume_creates_only_new_day_and_preserves_old_bytes(self):
        request, _ = self.add_receipt(day='2026-11-02')
        before = self.row(request)
        self.now += timedelta(days=2)
        self.resume()
        self.assertFalse(self.service.config()['paused'])
        self.assertEqual(self.acknowledgements(), [])
        with patch.object(self.service.qqq, 'run_due'), patch.object(self.service.qqq, 'busy', return_value=False), \
             patch.object(self.service.options, 'run_due'), patch.object(self.service.options, 'busy', return_value=False), \
             patch.object(self.service.venture, 'run_for_brief', side_effect=AssertionError('Old child cannot be replayed')), \
             patch.object(self.service, 'run', return_value={'status': 'new_current_day'}) as run:
            self.assertEqual(self.service.run_due(self.now), {'status': 'new_current_day'})
            run.assert_called_once_with('daily-2026-11-04', self.now, scheduled_day='2026-11-04', collect=True)
        self.assertEqual(self.row(request), before)
        self.assertEqual(self.acknowledgements(), [])

    def test_acknowledgement_uses_configured_local_day_not_utc_day(self):
        self.now = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)
        request, _ = self.add_receipt(day='2026-11-02'); self.resume()
        self.assertEqual(self.acknowledgements()[0]['day'], '2026-11-02')
        with self.forbid_children():
            self.assertIsNone(self.service.run_due(self.now))


if __name__ == '__main__':
    unittest.main()
