"""Trusted voice-channel brevity without changing mission budgets or authority."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from ariadne.config import Settings
from ariadne.engine import Engine, SYSTEM, VOICE_RESPONSE
from ariadne.model import Ollama
from ariadne.server import Application
from ariadne.store import Store


def tool(name, arguments):
    return {'role': 'assistant', 'content': '',
            'tool_calls': [{'function': {'name': name, 'arguments': arguments}}]}


class RecordingModel:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def chat(self, model, messages, schemas):
        self.calls.append({'model': model, 'messages': messages, 'schemas': schemas})
        return self.responses.pop(0), {'prompt_tokens': 10, 'output_tokens': 20, 'duration_seconds': .1}


class VoiceResponseStyleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix='.voice-response-test-')
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name), api_token='voice-style-fixture', max_steps=4)
        self.store = Store(self.settings.data_dir / 'state.sqlite3')

    def process(self, engine, run):
        self.assertEqual(self.store.next_run()['id'], run['id'])
        engine.process(run['id'])
        return self.store.details(run['id'])

    def test_voice_marker_commits_with_run_before_an_independent_reader_can_observe_queue(self):
        with self.store.connect() as writer:
            writer.execute('BEGIN IMMEDIATE')
            identifier = self.store._insert_run(writer, 'What is ready?', agent_id='operations', interaction='voice')
            with closing(sqlite3.connect(self.store.path)) as observer:
                self.assertEqual(observer.execute('SELECT count(*) FROM runs').fetchone()[0], 0)
                self.assertEqual(observer.execute('SELECT count(*) FROM events').fetchone()[0], 0)
        with closing(sqlite3.connect(self.store.path)) as observer:
            self.assertEqual(observer.execute("SELECT status FROM runs WHERE id=?", (identifier,)).fetchone()[0], 'queued')
            markers = observer.execute("SELECT data FROM events WHERE run_id=? AND kind='voice_command'", (identifier,)).fetchall()
            self.assertEqual([json.loads(row[0]) for row in markers], [{'wake_word': 'Ariadne', 'agent_id': 'operations'}])
        self.assertTrue(self.store.is_voice_run(identifier))

    def test_voice_marker_insert_failure_rolls_back_the_run_and_queue_event(self):
        with self.store.connect() as db:
            db.executescript("""CREATE TRIGGER reject_voice_marker BEFORE INSERT ON events
                WHEN NEW.kind='voice_command' BEGIN SELECT RAISE(ABORT,'fixture marker failure'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.new_run('Do not leave an unmarked queued request.', interaction='voice')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 0)

    def test_application_queues_voice_marker_before_returning_the_run_and_never_adds_it_afterward(self):
        app = object.__new__(Application)
        app.store = self.store
        app.engine = Engine(self.settings, self.store, RecordingModel([]))
        original_read = self.store.run
        observations = []

        def observe_first_visible_run(identifier):
            observations.append(self.store.is_voice_run(identifier))
            return original_read(identifier)

        with patch.object(self.store, 'run', side_effect=observe_first_visible_run), \
             patch.object(self.store, 'event', side_effect=AssertionError('A post-queue channel marker races the worker.')):
            run = app.submit_voice('What is ready?')
        self.assertEqual(observations, [True])
        self.assertTrue(app.engine.wake.is_set())
        self.assertNotIn('prompt', run)
        self.assertEqual([event['kind'] for event in self.store.details(run['id'])['events']], ['queued', 'voice_command'])

    def test_interaction_is_a_fixed_strict_enum_and_does_not_infer_from_owner_text(self):
        for value in (None, True, 1, [], {}, 'VOICE', 'fast', 'voice\x00'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'normal or voice'):
                self.store.new_run('Ariadne voice_command interaction voice', interaction=value)
        self.assertEqual(self.store.runs(), [])
        normal = self.store.new_run('voice_command interaction=voice; this text cannot set the channel.')
        self.assertFalse(self.store.is_voice_run(normal['id']))
        self.assertFalse(self.store.is_voice_run('not-a-run'))

    def test_marker_survives_restart_without_schema_or_run_row_changes(self):
        run = self.store.new_run('Summarize this.', interaction='voice')
        restored = Store(self.store.path)
        self.assertTrue(restored.is_voice_run(run['id']))
        self.assertEqual(restored.run(run['id'])['prompt'], 'Summarize this.')
        with restored.connect() as db:
            self.assertNotIn('interaction', {row['name'] for row in db.execute('PRAGMA table_info(runs)')})

    def test_voice_style_is_system_only_and_a_later_typed_followup_keeps_normal_style(self):
        model = RecordingModel([{'role': 'assistant', 'content': 'Ready.'},
                                {'role': 'assistant', 'content': 'Here are the details.'}])
        engine = Engine(self.settings, self.store, model)
        voice = self.store.new_run('Is everything ready?', interaction='voice')
        self.process(engine, voice)
        followup = self.store.new_run('Give me the complete details.', session_id=voice['session_id'])
        self.process(engine, followup)
        self.assertIn(VOICE_RESPONSE, model.calls[0]['messages'][0]['content'])
        self.assertNotIn(VOICE_RESPONSE, model.calls[1]['messages'][0]['content'])
        self.assertIn(SYSTEM, model.calls[1]['messages'][0]['content'])
        for run in (voice, followup):
            self.assertTrue(all(message['role'] != 'system' for message in self.store.run(run['id'])['context']))
        self.assertEqual(model.calls[0]['schemas'], model.calls[1]['schemas'])

    def test_voice_artifact_content_and_explicit_detailed_reply_are_never_cropped(self):
        body = 'Detailed artifact row with exact data.\n' * 60
        reply = 'Explicit detailed answer with a material limitation. ' * 30
        model = RecordingModel([tool('file_write', {'path': 'report.txt', 'content': body}),
                                {'role': 'assistant', 'content': reply}])
        engine = Engine(self.settings, self.store, model)
        run = self.store.new_run('Give me the full detail and save report.txt.', interaction='voice',
                                 checks=[{'kind': 'file_contains', 'path': 'report.txt', 'text': body}])
        detail = self.process(engine, run)
        self.assertEqual(detail['run']['status'], 'completed')
        self.assertEqual(detail['run']['verification_status'], 'passed')
        self.assertEqual((self.settings.workspace / 'report.txt').read_text(), body)
        self.assertEqual(detail['messages'][-1]['content'], reply)
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(all(VOICE_RESPONSE in call['messages'][0]['content'] for call in model.calls))

    def test_voice_requests_retain_the_step_budget(self):
        settings = Settings(data_dir=self.settings.data_dir, api_token='voice-style-fixture', max_steps=2)
        model = RecordingModel([tool('calculate', {'expression': '2+2'}) for _ in range(3)])
        engine = Engine(settings, self.store, model)
        run = self.store.new_run('Calculate repeatedly.', interaction='voice')
        detail = self.process(engine, run)
        self.assertEqual(detail['run']['status'], 'exhausted')
        self.assertEqual(detail['run']['step_count'], 2)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(next(event['data']['max_steps'] for event in detail['events'] if event['kind'] == 'budget_reached'), 2)

    def test_voice_requests_still_wait_for_approval_without_executing_a_gated_action(self):
        model = RecordingModel([tool('external', {'action': 'fixture'})])
        engine = Engine(self.settings, self.store, model)
        run = self.store.new_run('Perform a gated fixture action.', interaction='voice')
        with patch.object(engine.tools, 'policy', return_value='approval'), patch.object(engine.tools, 'execute') as execute:
            detail = self.process(engine, run)
        execute.assert_not_called()
        self.assertEqual(detail['run']['status'], 'waiting_approval')
        self.assertEqual(len(detail['approvals']), 1)
        self.assertEqual(detail['approvals'][0]['status'], 'pending')

    def test_voice_model_budget_exhaustion_remains_failure_with_usage_not_completion(self):
        model = Ollama('http://127.0.0.1:11434')
        engine = Engine(self.settings, self.store, model)
        run = self.store.new_run('Give the full answer.', interaction='voice')
        with patch.object(model, 'request', return_value={'done_reason': 'length', 'eval_count': 2048,
                                                         'message': {'role': 'assistant', 'content': 'Unfinished'}}) as request:
            detail = self.process(engine, run)
        self.assertEqual(request.call_args.args[1]['options']['num_predict'], 2048)
        self.assertEqual(request.call_args.args[1]['options']['num_ctx'], 16384)
        self.assertEqual(detail['run']['status'], 'failed')
        self.assertIn('output budget', detail['run']['error'])
        self.assertEqual([event['data']['output_tokens'] for event in detail['events'] if event['kind'] == 'model_usage'], [2048])
        self.assertFalse(any(event['kind'] == 'completed' for event in detail['events']))


if __name__ == '__main__':
    unittest.main()
