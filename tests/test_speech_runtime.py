"""Warm preset speech boundaries without models, sound, network or processes."""
import base64
import io
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import struct
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
import wave

from ariadne import speech_runtime as speech
from ariadne import _speech_worker as worker


def wav(*, rate=22050, channels=1, width=2, frames=80):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        stream.writeframes(b"\0" * frames * channels * width)
    return output.getvalue()


class Samples:
    """Tiny numerical fixture; no native NumPy or model execution in tests."""
    ndim = 1
    def __init__(self, values):
        self.values = tuple(values)
    def __len__(self):
        return len(self.values)
    def __mul__(self, value):
        return Samples(item*value for item in self.values)
    def astype(self, dtype):
        assert dtype == "<i2"
        return types.SimpleNamespace(tobytes=lambda: struct.pack("<"+"h"*len(self), *(int(item) for item in self.values)))


FAKE_NUMPY = types.SimpleNamespace(
    concatenate=lambda values: Samples(item for array in values for item in array.values),
    isfinite=lambda array: types.SimpleNamespace(all=lambda: all(math.isfinite(item) for item in array.values)),
    clip=lambda array, lower, upper: Samples(min(upper,max(lower,item)) for item in array.values),
)


class Pipe:
    def __init__(self):
        self.items = queue.Queue()
        self.closed = False
    def feed(self, message):
        self.items.put(message if type(message) is bytes else (json.dumps(message)+"\n").encode())
    def readline(self, limit):
        return self.items.get()[:limit]
    def close(self):
        self.closed = True
        self.items.put(b"")


class Input:
    def __init__(self, process):
        self.process, self.buffer, self.closed = process, b"", False
    def write(self, value):
        if self.closed:
            raise OSError("Closed test pipe.")
        if self.process.block_write:
            self.process.killed.wait(2)
            raise OSError("Test blocked write released after kill.")
        value = bytes(value)
        count = min(len(value), self.process.short_write or len(value))
        self.buffer += value[:count]
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            request = json.loads(line)
            self.process.requests.append(request)
            self.process.callback(self.process, request)
        return count
    def close(self):
        self.closed = True


class Process:
    def __init__(self, callback=None, *, ready=True):
        self.returncode, self.pid = None, 12345
        self.stdout, self.stdin = Pipe(), Input(self)
        self.requests, self.callback = [], callback or self.respond
        self.killed = threading.Event()
        self.block_write, self.short_write = False, None
        if ready:
            self.stdout.feed(speech._READY)
    @staticmethod
    def respond(process, request):
        rate = speech.VOICE_SAMPLE_RATES[request["voice"]]
        process.stdout.feed({"id": request["id"], "ok": True, "sample_rate": rate,
                             "data": base64.b64encode(wav(rate=rate)).decode()})
    def poll(self):
        return self.returncode
    def kill(self):
        self.returncode = -9
        self.killed.set()
        self.stdout.feed(b"")
    def wait(self, timeout):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fixed speech fixture", timeout)
        return self.returncode


class SpeechOptionsTests(unittest.TestCase):
    def test_five_fixed_presets_languages_backends_and_quick_default(self):
        self.assertEqual({item["id"] for item in speech.SPEECH_VOICES}, {"cori", "af_heart", "af_bella", "bf_emma", "bm_george"})
        self.assertEqual(speech.VOICE_LANGUAGES["bf_emma"], "en-gb")
        self.assertEqual(speech.VOICE_LANGUAGES["af_heart"], "en-us")
        self.assertEqual(speech.validate_speech_options(speech.DEFAULT_VOICE, speech.DEFAULT_SPEED), ("cori", 1.12))
        self.assertEqual(speech.VOICE_BACKENDS["cori"], "piper")
        self.assertEqual(speech.VOICE_SAMPLE_RATES["cori"], 22050)

    def test_voice_and_speed_reject_coercion_nonfinite_or_unknown_presets(self):
        for voice in (None, True, 1, [], "unknown", "../af_heart"):
            with self.subTest(voice=voice), self.assertRaises(ValueError):
                speech.validate_speech_options(voice, 1.0)
        for speed in (None, True, False, "1.1", [], .84, 1.31, float("nan"), float("inf"), -float("inf")):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                speech.validate_speech_options("af_heart", speed)
        for speed in (.85, 1, 1.30):
            self.assertEqual(speech.validate_speech_options("af_heart", speed)[1], float(speed))

    def test_text_rejects_unbounded_controls_surrogates_and_normalizes_whitespace(self):
        for text in (None, True, {}, "", " ", "x"*2001, "hi\x00", "hi\x7f", "hi\ud800"):
            with self.subTest(text=repr(text)), self.assertRaises(ValueError):
                speech.validate_speech_text(text)
        self.assertEqual(speech.validate_speech_text(" One\n two\tthree. "), "One two three.")

    def test_wav_validation_checks_pcm_rate_channels_complete_data_and_riff(self):
        self.assertEqual(speech.validate_speech_wav(wav())["frames"], 80)
        self.assertEqual(speech.validate_speech_wav(wav(rate=24000))["sample_rate"], 24000)
        for audio in (None, b"", wav(rate=16000), wav(channels=2), wav(width=1), wav(frames=0), wav()[:-2], wav()+b"junk", b"x"*3_000_001):
            with self.subTest(size=len(audio) if audio else None), self.assertRaises(ValueError):
                speech.validate_speech_wav(audio)

    def test_json_frames_reject_duplicate_keys_nonfinite_and_invalid_json(self):
        for raw in (b'{"id":1,"id":2}', b'{"speed":NaN}', b'{"speed":Infinity}', b'{'):
            with self.assertRaises(ValueError):
                speech.strict_json(raw)


class NaturalSpeechTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".speech-test-")
        self.runtime = speech.NaturalSpeech(Path(self.directory.name))
        self.snapshot = {"profile": "profile-one", "model": "model-one", "voices": "voices-one"}
        self.assets = mock.patch.object(speech, "speech_assets", side_effect=lambda *_args, **_kwargs: ({}, dict(self.snapshot)))
        self.assets.start()
        self.environment = mock.patch("ariadne.worker._environment", return_value={"PATH": "fixed"})
        self.env = self.environment.start()
        self.process = Process()
        self.popen = mock.patch.object(speech.subprocess, "Popen", side_effect=lambda *_args, **_kwargs: self.process)
        self.spawn = self.popen.start()
        self.terminate = mock.patch("ariadne.worker._terminate", side_effect=lambda process: process.kill())
        self.kill = self.terminate.start()
        self.timeout = mock.patch.object(speech, "REQUEST_TIMEOUT", 1.5)
        self.timeout.start()
    def tearDown(self):
        self.runtime.close()
        self.timeout.stop()
        self.terminate.stop()
        self.popen.stop()
        self.environment.stop()
        self.assets.stop()
        self.directory.cleanup()

    def test_construction_and_status_have_no_process_model_or_file_probe(self):
        self.assertEqual(self.runtime.status()["state"], "idle")
        self.spawn.assert_not_called()
        self.assertFalse(self.runtime.status()["gpu_used"])

    def test_repeated_requests_reuse_one_hidden_isolated_sanitized_child(self):
        self.assertEqual(self.runtime.synthesize("One concise answer."), wav())
        self.assertEqual(self.runtime.synthesize("Another concise answer."), wav())
        self.assertEqual(self.spawn.call_count, 1)
        args, kwargs = self.spawn.call_args
        self.assertEqual(args[0][1:3], ["-I", "-B"])
        self.assertEqual(Path(args[0][3]), Path(speech.__file__).with_name("_speech_worker.py"))
        self.assertEqual(args[0][4:], ["--data-dir", self.directory.name])
        self.assertEqual(kwargs["env"], {"PATH": "fixed"})
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(kwargs["bufsize"], 0)
        self.env.assert_called_once_with(None)
        self.assertEqual(self.runtime.status()["state"], "ready")
        self.assertEqual(self.runtime.status()["cached_acknowledgements"], 0)

    def test_invalid_caller_input_does_not_start_or_poison_worker(self):
        for values in ({"text": ""}, {"text": "Hello", "voice": "unknown"}, {"text": "Hello", "speed": True}, {"text": "\U0001f600"*1400}):
            with self.assertRaises(ValueError):
                self.runtime.synthesize(**values)
        self.spawn.assert_not_called()
        self.assertEqual(self.runtime.status()["state"], "idle")

    def test_encoded_byte_preflight_cannot_poison_an_existing_healthy_worker(self):
        self.runtime.synthesize("A valid request.")
        with self.assertRaises(ValueError):
            self.runtime.synthesize("\U0001f600"*1400)
        self.assertEqual(self.runtime.status()["state"], "ready")
        self.assertFalse(self.process.killed.is_set())
        self.assertEqual(len(self.process.requests), 1)

    def test_only_fixed_ack_is_cached_cap_four_and_new_rate_evicts_its_voice(self):
        for voice in speech.VOICE_LANGUAGES:
            self.runtime.synthesize("Listening.", voice=voice)
            self.runtime.synthesize("Listening.", voice=voice)
        self.assertEqual(len(self.process.requests), 5)
        self.assertEqual(self.runtime.status()["cached_acknowledgements"], 4)
        self.assertNotIn("cori", self.runtime._cache)
        self.runtime.synthesize("Listening.", speed=1.2)
        self.runtime.synthesize("Listening.")
        self.assertEqual(len(self.process.requests), 7)
        self.assertEqual(self.runtime.status()["cached_acknowledgements"], 4)
        for _ in range(2):
            self.runtime.synthesize("Private arbitrary text is never cached.")
        self.assertEqual(len(self.process.requests), 9)
        self.assertNotIn("Private arbitrary", json.dumps(self.runtime.status()))

    def test_ack_cache_still_checks_profile_and_asset_identity(self):
        self.runtime.synthesize("Listening.")
        self.snapshot["voices"] = "changed"
        with self.assertRaises(speech.SpeechError):
            self.runtime.synthesize("Listening.")
        self.assertEqual(len(self.process.requests), 1)
        self.assertEqual(self.runtime.status()["state"], "error")
        self.assertTrue(self.process.killed.is_set())
        self.assertEqual(self.runtime.status()["cached_acknowledgements"], 0)

    def test_ack_cache_eviction_preserves_the_most_recent_preset(self):
        for voice in list(speech.VOICE_LANGUAGES)[:4]:
            self.runtime.synthesize("Listening.", voice=voice)
        self.runtime.synthesize("Listening.", voice="cori")
        self.runtime.synthesize("Listening.", voice="bm_george")
        self.assertEqual(self.runtime.status()["cached_acknowledgements"], 4)
        self.assertIn("cori", self.runtime._cache)
        self.assertNotIn("af_heart", self.runtime._cache)
        self.assertEqual(len(self.process.requests), 5)

    def test_warm_returns_immediately_and_status_does_not_wait_on_native_initialization(self):
        self.process = Process(ready=False)
        started = time.monotonic()
        state = self.runtime.warm()
        self.assertEqual(state["state"], "warming")
        self.assertLess(time.monotonic()-started, .2)
        self.assertEqual(self.runtime.status()["state"], "warming")
        warmer = self.runtime._warm_thread
        self.process.stdout.feed(speech._READY)
        warmer.join(timeout=1)
        self.assertEqual(self.runtime.status()["state"], "ready")
        self.assertEqual(self.runtime.status()["cached_acknowledgements"], 1)
        self.runtime.synthesize("Listening.")
        self.assertEqual(len(self.process.requests), 1)

    def test_serialized_requests_cannot_overlap_and_require_unique_matching_ids(self):
        entered, release = threading.Event(), threading.Event()
        def respond(process, request):
            if not process.requests[:-1]:
                entered.set()
                release.wait(1)
            Process.respond(process, request)
        self.process.callback = respond
        results = []
        first = threading.Thread(target=lambda: results.append(self.runtime.synthesize("First.")))
        second = threading.Thread(target=lambda: results.append(self.runtime.synthesize("Second.")))
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        self.assertEqual(len(self.process.requests), 1)
        release.set()
        first.join(1)
        second.join(1)
        self.assertEqual(results, [wav(), wav()])
        self.assertEqual(len({request["id"] for request in self.process.requests}), 2)
        self.assertEqual(self.spawn.call_count, 1)

    def test_pipe_short_writes_are_completed_without_truncating_text(self):
        self.process.short_write = 7
        self.runtime.synthesize("A small request that spans several short pipe writes.")
        self.assertEqual(len(self.process.requests), 1)
        self.assertEqual(self.process.requests[0]["text"], "A small request that spans several short pipe writes.")

    def test_queued_cancellation_and_timeout_do_not_revoke_another_owned_request(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                runtime = speech.NaturalSpeech(Path(self.directory.name))
                entered, release = threading.Event(), threading.Event()
                self.process = Process()
                def respond(process, request):
                    entered.set()
                    release.wait(1)
                    Process.respond(process, request)
                self.process.callback = respond
                results = []
                active = threading.Thread(target=lambda: results.append(runtime.synthesize("Original caller.")))
                active.start()
                self.assertTrue(entered.wait(1))
                with mock.patch.object(speech, "REQUEST_TIMEOUT", .12):
                    with self.assertRaises(speech.SpeechError):
                        runtime.synthesize("Queued caller.", cancelled=lambda: cancelled)
                self.assertFalse(self.process.killed.is_set())
                self.assertNotEqual(runtime.status()["state"], "error")
                self.assertEqual(len(self.process.requests), 1)
                release.set()
                active.join(1)
                self.assertEqual(results, [wav()])
                self.assertEqual(runtime.status()["state"], "ready")
                runtime.close()

    def test_mismatched_receipt_latches_error_and_never_automatically_restarts(self):
        self.process.callback = lambda p, _r: p.stdout.feed({"id": "0"*24, "ok": True, "sample_rate": 24000, "data": base64.b64encode(wav()).decode()})
        with self.assertRaises(speech.SpeechError):
            self.runtime.synthesize("Sensitive text must not enter status.")
        self.assertEqual(self.runtime.status()["state"], "error")
        self.assertNotIn("Sensitive text", json.dumps(self.runtime.status()))
        with self.assertRaises(speech.SpeechError):
            self.runtime.synthesize("A new call is held.")
        self.assertEqual(self.runtime.warm()["state"], "error")
        self.assertEqual(self.spawn.call_count, 1)

    def test_malformed_audio_is_rejected_and_child_diagnostics_never_surface(self):
        self.process.callback = lambda p, r: p.stdout.feed({"id": r["id"], "ok": False, "error": "PRIVATE engine input and file path"})
        with self.assertRaises(speech.SpeechError) as raised:
            self.runtime.synthesize("A request.")
        self.assertNotIn("PRIVATE", str(raised.exception))
        self.assertTrue(self.process.killed.is_set())

    def test_protocol_numeric_metadata_is_not_coerced(self):
        self.process.callback = lambda p, r: p.stdout.feed({"id": r["id"], "ok": True, "sample_rate": 22050.0,
                                                           "data": base64.b64encode(wav()).decode()})
        with self.assertRaises(speech.SpeechError):
            self.runtime.synthesize("A request.")
        self.assertEqual(self.runtime.status()["state"], "error")

    def test_actual_wave_rate_must_match_receipt_and_selected_preset(self):
        self.process.callback = lambda p, r: p.stdout.feed({"id": r["id"], "ok": True, "sample_rate": 22050,
                                                           "data": base64.b64encode(wav(rate=24000)).decode()})
        with self.assertRaises(speech.SpeechError):
            self.runtime.synthesize("A request.")
        self.assertTrue(self.process.killed.is_set())

    def test_protocol_oversize_duplicate_json_or_bad_base64_is_fatal(self):
        for mode in ("oversize", "duplicate", "base64"):
            with self.subTest(mode=mode):
                # Each failure needs a deliberate new runtime, never an auto retry.
                runtime = speech.NaturalSpeech(Path(self.directory.name))
                self.process = Process()
                def respond(process, request):
                    if mode == "oversize":
                        process.stdout.feed(b"x"*(speech.MAX_OUTPUT_LINE+1))
                    elif mode == "duplicate":
                        process.stdout.feed(b'{"id":"x","id":"y"}\n')
                    else:
                        process.stdout.feed({"id": request["id"], "ok": True, "sample_rate": 24000, "data": "!!!!"})
                self.process.callback = respond
                with self.assertRaises(speech.SpeechError):
                    runtime.synthesize("A request.")
                self.assertEqual(runtime.status()["state"], "error")
                runtime.close()

    def test_cancel_kills_owned_child_and_holds_without_retry(self):
        cancelled = threading.Event()
        self.process.callback = lambda _p, _r: cancelled.set()
        with self.assertRaises(speech.SpeechError):
            self.runtime.synthesize("A request.", cancelled=cancelled.is_set)
        self.assertTrue(self.process.killed.is_set())
        self.assertEqual(self.runtime.status()["state"], "error")
        self.assertEqual(self.spawn.call_count, 1)

    def test_blocked_write_and_missing_reply_have_finite_timeout(self):
        for block_write in (False, True):
            with self.subTest(block_write=block_write):
                runtime = speech.NaturalSpeech(Path(self.directory.name))
                self.process = Process(callback=lambda *_args: None)
                self.process.block_write = block_write
                with mock.patch.object(speech, "REQUEST_TIMEOUT", .12):
                    started = time.monotonic()
                    with self.assertRaises(speech.SpeechError):
                        runtime.synthesize("A request.")
                self.assertLess(time.monotonic()-started, 1)
                self.assertTrue(self.process.killed.is_set())
                runtime.close()

    def test_dead_worker_cannot_serve_a_cached_ack(self):
        self.runtime.synthesize("Listening.")
        self.process.kill()
        with self.assertRaises(speech.SpeechError):
            self.runtime.synthesize("Listening.")
        self.assertEqual(self.runtime.status()["state"], "error")

    def test_close_is_terminal_revokes_cache_and_drains_owned_process(self):
        self.runtime.synthesize("Listening.")
        self.runtime.close()
        self.assertTrue(self.process.killed.is_set())
        self.assertIsNone(self.runtime._process)
        self.assertEqual(self.runtime.status()["cached_acknowledgements"], 0)
        with self.assertRaises(speech.SpeechError):
            self.runtime.synthesize("After close.")
        with self.assertRaises(speech.SpeechError):
            self.runtime.warm()
        self.assertEqual(self.spawn.call_count, 1)

    def test_unverified_termination_keeps_owned_handle_and_rejects_reset(self):
        self.runtime.synthesize("Listening.")
        self.kill.side_effect = OSError("Synthetic denied termination")
        try:
            with self.assertRaises(speech.SpeechError):
                self.runtime.close()
            self.assertIs(self.runtime._process, self.process)
            self.assertEqual(self.runtime.status()["state"], "error")
            self.assertFalse(self.process.killed.is_set())
        finally:
            self.kill.side_effect = lambda process: process.kill()


class WorkerProtocolTests(unittest.TestCase):
    def test_piper_startup_keeps_kokoro_unloaded_and_lazy_quality_selection_reuses_both(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".piper-engine-test-") as directory:
            config_path = Path(directory) / "model.json"
            configuration = {"audio": {"sample_rate": 22050}, "language": {"code": "en_GB"}, "num_speakers": 1, "phoneme_type": "espeak"}
            config_path.write_text(json.dumps(configuration), encoding="utf8")
            paths = {backend+"."+role: speech.ROOT/name for backend, pins in speech.ASSET_PINS.items() for role,(name,_) in pins.items()}
            paths["piper.config"] = config_path
            piper_instance, kokoro_instance = mock.Mock(), mock.Mock()
            kokoro_instance.get_voices.return_value = list(speech.VOICE_LANGUAGES)
            piper_class = mock.Mock(return_value=piper_instance)
            config_class = types.SimpleNamespace(from_dict=mock.Mock(return_value="fixed-config"))
            kokoro_class = types.SimpleNamespace(from_session=mock.Mock(return_value=kokoro_instance))
            versions = {"piper-tts": "1.8.0", "pathvalidate": "3.3.1", "kokoro-onnx": "0.6.1"}
            options, session = [], object()
            def make_options():
                item = types.SimpleNamespace()
                options.append(item)
                return item
            ort = types.SimpleNamespace(disable_telemetry_events=mock.Mock(), SessionOptions=make_options,
                                        InferenceSession=mock.Mock(return_value=session))
            with mock.patch.dict("sys.modules", {"numpy": FAKE_NUMPY, "onnxruntime": ort,
                    "piper": types.SimpleNamespace(PiperVoice=piper_class, PiperConfig=config_class),
                    "kokoro_onnx": types.SimpleNamespace(Kokoro=kokoro_class)}), \
                    mock.patch.object(worker,"speech_assets",return_value=(paths,{"identity":"fixed"})), \
                    mock.patch.object(worker.sys,"path",list(worker.sys.path)), mock.patch.object(worker.sys,"addaudithook"), \
                    mock.patch.object(worker.importlib.metadata,"version",side_effect=lambda package:versions[package]):
                engine = worker.PresetEngine(Path(directory))
                self.assertEqual(set(engine.engines), {"piper"})
                kokoro_class.from_session.assert_not_called()
                self.assertIs(engine._load_kokoro(), kokoro_instance)
                self.assertIs(engine._load_piper(), piper_instance)
                self.assertIs(engine._load_kokoro(), kokoro_instance)
            piper_class.assert_called_once_with(session=session, config="fixed-config", download_dir=Path(directory), use_tashkeel=False)
            config_class.from_dict.assert_called_once_with(configuration)
            self.assertEqual(kokoro_class.from_session.call_count, 1)
            self.assertEqual(len(options), 2)
            self.assertTrue(all(item.intra_op_num_threads == 2 and item.inter_op_num_threads == 1 for item in options))
            self.assertTrue(all(call.kwargs["providers"] == ["CPUExecutionProvider"] for call in ort.InferenceSession.call_args_list))

    def test_worker_encodes_piper_at22050_and_kokoro_at24000_without_resampling(self):
        engine = object.__new__(worker.PresetEngine)
        engine.np = FAKE_NUMPY
        engine.check_assets = mock.Mock()
        piper_instance, kokoro_instance = mock.Mock(), mock.Mock()
        piper_instance.synthesize.return_value = [types.SimpleNamespace(sample_rate=22050, sample_width=2,
            sample_channels=1, audio_float_array=Samples([.25,-.25]*40))]
        kokoro_instance.create.return_value = (Samples([.25,-.25]*40),24000)
        engine.engines = {"piper":piper_instance,"kokoro":kokoro_instance}
        config = mock.Mock(side_effect=lambda **kwargs: types.SimpleNamespace(**kwargs))
        with mock.patch.dict("sys.modules",{"piper":types.SimpleNamespace(SynthesisConfig=config)}):
            quick = engine.say("Concise answer.","cori",1.12)
            slower = engine.say("Concise answer.","af_heart",1.12)
        self.assertEqual(speech.validate_speech_wav(quick), {"sample_rate":22050,"frames":80,"duration_seconds":round(80/22050,4)})
        self.assertEqual(speech.validate_speech_wav(slower)["sample_rate"],24000)
        config.assert_called_once_with(length_scale=1/1.12)
        self.assertEqual(engine.check_assets.call_count,4)

    def test_engine_uses_verified_assets_audit_hook_and_cpu_session_with_fixed_threads(self):
        paths = {"kokoro."+name: speech.ROOT / value[0] for name, value in speech.ASSET_PINS["kokoro"].items()}
        instance = mock.Mock()
        instance.get_voices.return_value = list(speech.VOICE_LANGUAGES)
        kokoro = types.SimpleNamespace(from_session=mock.Mock(return_value=instance))
        options = types.SimpleNamespace()
        session = object()
        ort = types.SimpleNamespace(disable_telemetry_events=mock.Mock(), SessionOptions=lambda: options,
                                    InferenceSession=mock.Mock(return_value=session))
        with mock.patch.dict("sys.modules", {"numpy": types.SimpleNamespace(), "onnxruntime": ort,
                                             "kokoro_onnx": types.SimpleNamespace(Kokoro=kokoro)}), \
                mock.patch.object(worker, "speech_assets", return_value=(paths, {"identity": "fixed"})) as assets, \
                mock.patch.object(worker.sys, "path", list(worker.sys.path)), \
                mock.patch.object(worker.sys, "addaudithook") as hook, \
                mock.patch.object(worker.importlib.metadata, "version", return_value="0.6.1"):
            engine = worker.PresetEngine(speech.ROOT / "data")
        self.assertIs(engine.engine, instance)
        self.assertEqual(assets.call_args_list[0].kwargs, {"verify": True})
        self.assertEqual(assets.call_count, 2)
        hook.assert_called_once()
        self.assertEqual(options.intra_op_num_threads, 2)
        self.assertEqual(options.inter_op_num_threads, 1)
        self.assertEqual(ort.InferenceSession.call_args.kwargs["providers"], ["CPUExecutionProvider"])
        ort.disable_telemetry_events.assert_called_once_with()
        kokoro.from_session.assert_called_once_with(session, str(paths["kokoro.voices"]))
        self.assertNotIn("piper", engine.engines)

    def test_real_serve_loop_supports_multiple_fixed_requests_stop_and_eof(self):
        engine = mock.Mock()
        engine.say.return_value = wav(rate=24000)
        requests = [{"id": format(n, "024x"), "action": "say", "text": "Concise answer.", "voice": "bf_emma", "speed": 1.12} for n in (1, 2)]
        requests += [{"id": "3"*24, "action": "stop"}]
        incoming = io.BytesIO(b"".join((json.dumps(row)+"\n").encode() for row in requests))
        outgoing = io.BytesIO()
        worker.serve(engine, incoming, outgoing)
        frames = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(frames[0], speech._READY)
        self.assertEqual([row["id"] for row in frames[1:]], [request["id"] for request in requests[:2]])
        self.assertEqual(engine.say.call_count, 2)
        self.assertEqual(engine.say.call_args.args, ("Concise answer.", "bf_emma", 1.12))
        empty = io.BytesIO()
        worker.serve(engine, io.BytesIO(b""), empty)
        self.assertEqual(len(empty.getvalue().splitlines()), 1)

    def test_worker_rejects_invalid_or_unbounded_frames_without_engine_work(self):
        base = {"id": "1"*24, "action": "say", "text": "Hello.", "voice": "af_heart", "speed": 1.12}
        invalid = [{**base, "speed": True}, {**base, "voice": "unknown"}, {**base, "path": "private.wav"}, {**base, "action": "download"}, {**base, "id": 1}, {**base, "text": "x"*2001}]
        raw_frames = [(json.dumps(value)+"\n").encode() for value in invalid]
        raw_frames += [b'{"id":"x","id":"y"}\n', b'x'*(speech.MAX_INPUT_LINE+1), json.dumps(base).encode()]
        for raw in raw_frames:
            with self.subTest(raw_prefix=raw[:40]):
                engine, outgoing = mock.Mock(), io.BytesIO()
                worker.serve(engine, io.BytesIO(raw+(json.dumps(base)+"\n").encode()), outgoing)
                engine.say.assert_not_called()
                results = outgoing.getvalue().splitlines()
                self.assertEqual(len(results), 2)
                self.assertIs(json.loads(results[1])["ok"], False)

    def test_worker_error_stops_without_private_text_or_automatic_next_request(self):
        engine = mock.Mock()
        engine.say.side_effect = ValueError("SECRET text and private path")
        request = {"id": "1"*24, "action": "say", "text": "Private request.", "voice": "af_heart", "speed": 1.12}
        raw = (json.dumps(request)+"\n").encode()
        outgoing = io.BytesIO()
        worker.serve(engine, io.BytesIO(raw+raw), outgoing)
        self.assertEqual(engine.say.call_count, 1)
        self.assertNotIn(b"SECRET", outgoing.getvalue())
        self.assertNotIn(b"Private request", outgoing.getvalue())

    def test_resource_limits_reuse_windows_job_or_posix_address_space_without_cpu_quota(self):
        with mock.patch.object(worker.sys, "path", list(worker.sys.path)), mock.patch.object(worker.os, "name", "nt"), mock.patch("scripts.wake_listener.limit_continuous_worker") as limit:
            worker.limit_resources()
            limit.assert_called_once_with()
        resource = types.SimpleNamespace(RLIMIT_AS=9, setrlimit=mock.Mock())
        with mock.patch.object(worker.os, "name", "posix"), mock.patch.dict("sys.modules", {"resource": resource}):
            worker.limit_resources()
        resource.setrlimit.assert_called_once_with(9, (2*1024**3, 2*1024**3))


class SpeechAssetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".speech-assets-")
        self.root = Path(self.directory.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.pins = {}
        import hashlib
        self.all_pins = {}
        for backend, pins in speech.ASSET_PINS.items():
            self.all_pins[backend] = {}
            for role, (name, _digest) in pins.items():
                target = self.root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((backend+role+" tiny fixture").encode())
                self.all_pins[backend][role] = (name, hashlib.sha256(target.read_bytes()).hexdigest())
        self.pins = self.all_pins["kokoro"]
        self.profile = {"version": 1, "backends": {"kokoro": {"assets": {role: {"path": name, "sha256": digest} for role, (name, digest) in self.pins.items()}}}}
        self.path = self.data / "media-config.json"
        self.path.write_text(json.dumps(self.profile), encoding="utf8")
        self.root_patch = mock.patch.object(speech, "ROOT", self.root)
        self.pin_patch = mock.patch.object(speech, "ASSET_PINS", self.all_pins)
        self.root_patch.start()
        self.pin_patch.start()
    def tearDown(self):
        self.pin_patch.stop()
        self.root_patch.stop()
        self.directory.cleanup()

    def test_full_initial_hash_and_later_profile_or_asset_identity_changes(self):
        paths, before = speech.speech_assets(self.data, verify=True)
        self.assertEqual(set(paths), {"kokoro.model", "kokoro.voices"})
        self.assertEqual(speech.speech_assets(self.data)[1], before)
        paths["kokoro.model"].write_bytes(b"changed")
        self.assertNotEqual(speech.speech_assets(self.data)[1], before)
        with self.assertRaises(ValueError):
            speech.speech_assets(self.data, verify=True)

    def test_piper_only_kokoro_only_and_both_profiles_have_exact_supported_asset_sets(self):
        for selected in (("piper",),("kokoro",),("piper","kokoro")):
            profile={"version":1,"backends":{backend:{"assets":{role:{"path":name,"sha256":digest}
                for role,(name,digest) in self.all_pins[backend].items()}} for backend in selected}}
            self.path.write_text(json.dumps(profile),encoding="utf8")
            paths, identities=speech.speech_assets(self.data,verify=True)
            expected={backend+"."+role for backend in selected for role in self.all_pins[backend]}
            self.assertEqual(set(paths),expected)
            self.assertEqual(set(identities),expected|{"profile"})
        self.path.write_text(json.dumps({"version":1,"backends":{}}),encoding="utf8")
        with self.assertRaises(ValueError):
            speech.speech_assets(self.data)

    def test_profile_content_hash_detects_change_even_when_mtime_is_restored(self):
        before = speech.speech_assets(self.data)[1]
        original = self.path.stat()
        self.path.write_text(json.dumps(self.profile, indent=2), encoding="utf8")
        os.utime(self.path, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertNotEqual(speech.speech_assets(self.data)[1], before)

    def test_path_and_handle_ctime_may_differ_but_each_must_remain_stable(self):
        original = os.fstat
        def handle_info(descriptor):
            info = original(descriptor)
            return types.SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns+100)
        with mock.patch.object(speech.os, "fstat", side_effect=handle_info):
            self.assertEqual(set(speech.speech_assets(self.data, verify=True)[0]), {"kokoro.model", "kokoro.voices"})

    def test_profile_rejects_other_model_pins_duplicate_keys_bool_schema_and_extra_roles(self):
        broken = json.loads(json.dumps(self.profile))
        broken["backends"]["kokoro"]["assets"]["model"]["sha256"] = "0"*64
        variants = [json.dumps(broken), '{"version":1,"version":1,"backends":{}}', json.dumps({**self.profile, "version": True})]
        broken["backends"]["kokoro"]["assets"]["extra"] = {}
        variants.append(json.dumps(broken))
        for text in variants:
            self.path.write_text(text, encoding="utf8")
            with self.assertRaises(ValueError):
                speech.speech_assets(self.data)

    def test_hardlinked_model_is_rejected_before_native_load(self):
        model = self.root / self.pins["model"][0]
        try:
            os.link(model, model.with_name("second-link.onnx"))
        except OSError as error:
            self.skipTest("Host cannot create the synthetic hardlink: "+type(error).__name__)
        with self.assertRaises(ValueError):
            speech.speech_assets(self.data, verify=True)


if __name__ == "__main__":
    unittest.main()
