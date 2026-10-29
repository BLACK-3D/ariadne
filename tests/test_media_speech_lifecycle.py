"""One adapter owns reusable speech across media-tool entry points and cleanup."""
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import wave

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.store import Store
from ariadne.tools import ToolRegistry


def wav(rate=24000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b'\0\0' * 240)
    return output.getvalue()


class FakeVoice:
    windows_speech = None

    def __init__(self):
        self.speak = Mock(return_value=wav())
        self.transcribe = Mock(return_value='Synthetic transcript.')
        self.close = Mock()

    def status(self):
        return {'speech': True, 'transcription': True, 'speech_backend': 'kokoro',
                'speech_backends': ['natural'], 'transcription_backend': 'whisper.cpp'}


class MediaSpeechLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix='.media-speech-lifecycle-')
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name), model='qwen3.5:9b')
        self.registry = ToolRegistry(self.settings)
        self.media = self.registry.media
        self.addCleanup(self.media.close)

    def test_schemas_approval_synthesis_and_transcription_share_exactly_one_voice(self):
        voice = FakeVoice()
        (self.settings.workspace / 'input.wav').write_bytes(wav(rate=16000))
        with patch('ariadne.voice.Voice', return_value=voice) as create:
            self.media.schemas()
            self.media.fingerprint_material('media_speak', {'text': 'First.', 'output': 'first.wav'})
            for name in ('first.wav', 'second.wav'):
                result = self.media.execute('media_speak', {'text': 'Synthetic phrase.', 'output': name, 'backend': 'natural'})
                self.assertTrue(result['ok'], result)
                self.assertEqual((self.settings.workspace / name).read_bytes(), wav())
            self.media.schemas()
            self.media.fingerprint_material('media_speak', {'text': 'Third.', 'output': 'third.wav'})
            transcript = self.media.execute('media_transcribe', {'path': 'input.wav'})
        create.assert_called_once_with(self.settings)
        self.assertEqual(voice.speak.call_count, 2)
        voice.transcribe.assert_called_once_with(wav(rate=16000), cancelled=None)
        self.assertEqual(transcript['text'], 'Synthetic transcript.')
        self.media.close()
        self.media.close()
        voice.close.assert_called_once_with()

    def test_simultaneous_first_lookups_cannot_construct_duplicate_voice_workers(self):
        voice, entered, release = FakeVoice(), threading.Event(), threading.Event()
        results, errors = [], []
        def factory(_settings):
            entered.set()
            if not release.wait(2):
                raise AssertionError('Synthetic constructor was not released.')
            return voice
        def obtain():
            try:
                results.append(self.media._voice_adapter())
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=obtain) for _ in range(8)]
        with patch('ariadne.voice.Voice', side_effect=factory) as create:
            try:
                threads[0].start()
                self.assertTrue(entered.wait(2))
                for thread in threads[1:]:
                    thread.start()
            finally:
                release.set()
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(timeout=3)
            self.assertEqual(errors, [])
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            create.assert_called_once_with(self.settings)
        self.assertEqual(len(results), 8)
        self.assertTrue(all(result is voice for result in results))

    def test_failed_close_keeps_owned_adapter_and_never_creates_a_replacement(self):
        voice = FakeVoice()
        voice.close.side_effect = [ValueError('Synthetic termination failure.'), None]
        with patch('ariadne.voice.Voice', return_value=voice) as create:
            self.assertIs(self.media._voice_adapter(), voice)
            with self.assertRaisesRegex(ValueError, 'termination failure'):
                self.media.close()
            self.assertIs(self.media._voice, voice)
            with self.assertRaisesRegex(ValueError, 'closed'):
                self.media._voice_adapter()
            self.media.close()
            self.media.close()
            create.assert_called_once_with(self.settings)
        self.assertEqual(voice.close.call_count, 2)
        self.assertIsNone(self.media._voice)

    def test_closing_before_first_use_prevents_late_native_start(self):
        self.media.close()
        with patch('ariadne.voice.Voice') as create:
            with self.assertRaisesRegex(ValueError, 'closed'):
                self.media._voice_adapter()
        create.assert_not_called()

    def test_pending_speech_approval_binds_both_new_persistent_implementation_files(self):
        voice = FakeVoice()
        revisions = {'speech_runtime.py': 'runtime-original', '_speech_worker.py': 'worker-original'}
        def identity(path, **_kwargs):
            return {'path': str(path), 'bytes': 1, 'sha256': revisions.get(path.name, 'unchanged')}
        with patch('ariadne.voice.Voice', return_value=voice), patch.object(self.media, '_identity', side_effect=identity):
            args = {'text': 'Synthetic phrase.', 'output': 'approval.wav', 'backend': 'natural'}
            first = self.media.fingerprint_material('media_speak', args)['implementation']
            revisions['_speech_worker.py'] = 'worker-updated'
            second = self.media.fingerprint_material('media_speak', args)['implementation']
            revisions['speech_runtime.py'] = 'runtime-updated'
            third = self.media.fingerprint_material('media_speak', args)['implementation']
        self.assertNotEqual(first['_speech_worker.py'], second['_speech_worker.py'])
        self.assertEqual(first['speech_runtime.py'], second['speech_runtime.py'])
        self.assertNotEqual(second['speech_runtime.py'], third['speech_runtime.py'])

    def test_standalone_engine_stop_closes_its_reusable_media_adapter(self):
        engine = Engine(self.settings, Store(self.settings.data_dir / 'state.sqlite3'), model=Mock())
        voice = FakeVoice()
        engine.tools.media._voice = voice
        engine.stop()
        engine.stop()
        voice.close.assert_called_once_with()
        self.assertTrue(engine.stopping.is_set())
        self.assertTrue(engine.wake.is_set())

    def test_media_close_failure_does_not_skip_engine_join_or_instance_lock_release(self):
        engine = Engine(self.settings, Store(self.settings.data_dir / 'state.sqlite3'), model=Mock())
        voice = FakeVoice()
        voice.close.side_effect = [ValueError('Synthetic termination failure.'), None]
        engine.tools.media._voice = voice
        engine.thread, engine.lock = Mock(), Mock()
        engine.thread.is_alive.return_value = False
        with self.assertRaisesRegex(ValueError, 'termination failure'):
            engine.stop()
        engine.thread.join.assert_called_once_with(timeout=2)
        engine.lock.release.assert_called_once_with()
        self.assertIs(engine.tools.media._voice, voice)
        engine.stop()
        self.assertEqual(voice.close.call_count, 2)
        self.assertIsNone(engine.tools.media._voice)


if __name__ == '__main__':
    unittest.main()
