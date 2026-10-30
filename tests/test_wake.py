"""Wake utterances preserve opt-in, session, pause and mission boundaries."""
import io
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ariadne.config import Settings
from ariadne.wake import ACK_TAIL_SECONDS, CONVERSATION_IDLE_SECONDS, MAX_SESSION_SECONDS, WakeGate, WakeService, windows_speak_response


class Clock:
    def __init__(self):
        self.value = 100.0
    def __call__(self):
        return self.value


class WakeGateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gate = WakeGate(self.clock)

    def test_requires_leading_whole_word_and_strips_only_wake_prefix(self):
        for text in ("Discuss Ariadne tomorrow", "Ariadneology is a word", "Ariadne's status is ready", "Ariadne42", "report status"):
            self.assertEqual(self.gate.accept(text)["event"], "ignored")
        self.assertEqual(self.gate.accept("Hey, Ariadne, report status"), {"event": "command", "command": "report status"})

    def test_bare_wake_opens_initial_window_then_bounded_followups(self):
        self.assertEqual(self.gate.accept("Ariadne!")["event"], "awake")
        self.clock.value += 11.9
        self.assertEqual(self.gate.accept("report status")["event"], "command")
        self.assertEqual(self.gate.accept("another command")["event"], "command")
        self.clock.value += CONVERSATION_IDLE_SECONDS
        self.assertEqual(self.gate.accept("too late")["event"], "ignored")
        self.assertFalse(self.gate.conversation_active)

    def test_cancel_does_not_submit_and_closes_window(self):
        self.gate.accept("Ariadne")
        self.assertEqual(self.gate.accept("never mind")["event"], "cancelled")
        self.assertEqual(self.gate.accept("do it now")["event"], "ignored")

    def test_repeated_command_is_deduplicated_but_can_be_intentional_after_ten_seconds(self):
        self.assertEqual(self.gate.accept("Ariadne report status")["event"], "command")
        self.assertEqual(self.gate.accept("ARIADNE REPORT STATUS!")["event"], "duplicate")
        self.clock.value += 10
        self.assertEqual(self.gate.accept("Ariadne report status")["event"], "command")

    def test_speech_suppression_drops_echoes_and_preserves_conversation(self):
        self.gate.accept("Ariadne")
        conversation = self.gate.conversation_id
        self.gate.suppress(5)
        self.assertEqual(self.gate.accept("Ariadne report status")["event"], "suppressed")
        self.assertEqual(self.gate.accept("go to sleep")["event"], "suppressed")
        self.assertEqual(self.gate.accept("stop listening")["event"], "suppressed")
        self.assertEqual(self.gate.conversation_id, conversation)
        self.clock.value += 5
        self.assertEqual(self.gate.accept("report status")["event"], "command")
        self.assertEqual(self.gate.conversation_id, conversation)
        for value in (-1, 181, float("nan"), float("inf"), True, "5"):
            with self.assertRaises(ValueError):
                self.gate.suppress(value)

    def test_invalid_unbounded_or_control_transcripts_are_rejected(self):
        for text in (None, [], "x" * 3001, "Ariadne\x00report"):
            with self.assertRaises(ValueError):
                self.gate.accept(text)

    def test_idle_timeout_is_bounded_and_only_new_wake_creates_a_new_conversation(self):
        for value in (True, None, 0, 14, 601, 120.0, "120"):
            with self.assertRaises(ValueError):
                WakeGate(self.clock, conversation_idle_timeout=value)
        gate = WakeGate(self.clock, conversation_idle_timeout=15)
        gate.accept("Ariadne explain reserves")
        first = gate.conversation_id
        self.clock.value += 14
        self.assertEqual(gate.accept("tell me more")["event"], "command")
        self.assertEqual(gate.conversation_id, first)
        self.clock.value += 15
        self.assertEqual(gate.accept("and now?")["event"], "ignored")
        self.assertIsNone(gate.conversation_id)
        gate.accept("Ariadne explain reserves")
        self.assertNotEqual(gate.conversation_id, first)

    def test_sleep_controls_are_exact_and_work_without_a_wake_prefix(self):
        for phrase in ("go to sleep", "Ariadne sleep!", "sleep Ariadne", "Hey Ariadne, go to sleep."):
            with self.subTest(phrase=phrase):
                self.gate.accept("Ariadne")
                self.assertEqual(self.gate.accept(phrase)["event"], "asleep")
                self.assertFalse(self.gate.conversation_active)
                self.assertIsNone(self.gate.conversation_id)
                self.assertEqual(self.gate.accept("unarmed followup")["event"], "ignored")
        self.gate.accept("Ariadne")
        self.assertEqual(self.gate.accept("explain how batteries go to sleep")["event"], "command")
        self.assertEqual(self.gate.accept("stop listening")["event"], "stop")

    def test_pending_gate_accepts_controls_only_without_poisoning_replay_history(self):
        self.gate.accept("Ariadne explain reserves")
        deadline, last = self.gate.conversation_until, self.gate.last_command
        self.clock.value += 2
        for phrase in ("Ariadne", "tell me more", "Ariadne tell me more"):
            self.assertEqual(self.gate.accept(phrase, controls_only=True)["event"], "ignored")
        self.assertEqual(self.gate.conversation_until, deadline)
        self.assertEqual(self.gate.last_command, last)
        self.assertEqual(self.gate.accept("go to sleep", controls_only=True)["event"], "asleep")

    def test_processing_hold_and_playback_tail_do_not_consume_idle_time(self):
        self.gate.accept("Ariadne explain reserves")
        self.gate.hold_conversation()
        self.clock.value += 1000
        self.assertTrue(self.gate.conversation_active)
        self.gate.suppress(2)
        self.gate.resume_conversation()
        self.assertEqual(self.gate.conversation_until, self.clock.value + 2 + CONVERSATION_IDLE_SECONDS)
        self.gate.reset()
        self.gate.resume_conversation()
        self.assertFalse(self.gate.conversation_active)


class WakeServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".wake-test-")
        self.settings = Settings(data_dir=Path(self.directory.name), api_token="wake-test")
        self.clock, self.submitted = Clock(), []
        def submit(command):
            self.submitted.append(command)
            return {"id": "mission-1", "status": "queued"}
        self.service = WakeService(self.settings, submit, clock=self.clock)

    def tearDown(self):
        self.service.stop()
        self.directory.cleanup()

    def accept(self, text, sequence=1, **kwargs):
        return self.service.accept(text, lease=self.service.lease, sequence=sequence, **kwargs)

    def test_no_background_start_or_unarmed_mission(self):
        self.assertFalse(self.service.status()["active"])
        with self.assertRaises(ValueError):
            self.accept("Ariadne report status")
        self.service.start("browser")
        self.assertEqual(self.accept("report status")["event"], "ignored")
        self.assertEqual(self.submitted, [])
        self.assertEqual(self.accept("Ariadne report status", 2)["event"], "submitted")
        self.assertEqual(self.submitted, ["report status"])
        self.assertNotIn("command", self.service.status())

    def test_lease_source_and_sequence_prevent_stale_browser_replay(self):
        self.service.start("browser")
        lease = self.service.lease
        for changes in ({"lease": "wrong", "sequence": 1}, {"lease": lease, "sequence": True}, {"lease": lease, "sequence": 1, "source": "pc"}):
            with self.assertRaises(ValueError):
                self.service.accept("Ariadne report", **changes)
        self.accept("Ariadne", 1)
        with self.assertRaises(ValueError):
            self.accept("report", 1)
        self.service.stop()
        self.service.start("browser")
        self.assertNotEqual(self.service.lease, lease)
        with self.assertRaises(ValueError):
            self.service.accept("Ariadne report", lease=lease, sequence=2)

    def test_paused_or_busy_mission_does_not_queue_or_remain_armed(self):
        self.service.blocked = lambda: True
        self.service.start("browser")
        self.assertEqual(self.accept("Ariadne report")["event"], "busy")
        self.service.blocked = lambda: False
        self.assertEqual(self.accept("report", 2)["event"], "ignored")
        self.assertEqual(self.submitted, [])

    def test_paused_browser_bare_wake_does_not_latch_after_a_later_resume(self):
        self.service.blocked = lambda: True
        self.service.start("browser", session_hours=None)
        self.assertEqual(self.accept("Ariadne")["event"], "busy")
        self.assertFalse(self.service.status()["conversation_active"])
        self.service.blocked = lambda: False
        self.assertEqual(self.accept("continue", sequence=2)["event"], "ignored")
        self.assertEqual(self.submitted, [])

    def test_optional_warming_hook_runs_once_per_new_accepted_conversation(self):
        warm = mock.Mock()
        self.service.on_awake = warm
        self.service.start("browser", session_hours=None)
        self.accept("background conversation")
        warm.assert_not_called()
        self.accept("Ariadne", sequence=2)
        first = self.service.conversation_id
        warm.assert_called_once_with()
        self.accept("explain reserves", sequence=3)
        self.accept("Ariadne explain reserves", sequence=4)  # Duplicate.
        self.accept("Ariadne", sequence=5)  # Already awake.
        self.assertEqual(warm.call_count, 1)
        self.assertEqual(self.service.conversation_id, first)
        self.accept("sleep", sequence=6)
        self.accept("Ariadne explain a new topic", sequence=7)
        self.assertEqual(warm.call_count, 2)
        self.assertNotEqual(self.service.conversation_id, first)
        self.clock.value += CONVERSATION_IDLE_SECONDS
        self.accept("Ariadne", sequence=8)
        self.assertEqual(warm.call_count, 3)

    def test_warming_hook_never_runs_for_paused_pending_or_suppressed_speech(self):
        warm = mock.Mock()
        self.service.on_awake = warm
        self.service.blocked = lambda: True
        self.service.start("browser", session_hours=None)
        self.accept("Ariadne")
        self.accept("Ariadne explain reserves", sequence=2)
        warm.assert_not_called()
        self.service.blocked = lambda: False
        self.service.pending_run = "already-running"
        self.accept("Ariadne", sequence=3)
        self.service.pending_run = None
        with mock.patch("ariadne.wake.threading.Timer"):
            self.service.suppress(5)
        self.accept("Ariadne", sequence=4)
        self.service.speaking = True
        self.accept("Ariadne", sequence=5)
        self.service.speaking = False
        warm.assert_not_called()
        self.assertEqual(self.submitted, [])

    def test_warming_hook_failure_does_not_lose_a_valid_spoken_command(self):
        self.service.on_awake = mock.Mock(side_effect=OSError("No model warming available"))
        self.service.start("browser", session_hours=None)
        self.assertEqual(self.accept("Ariadne explain reserves")["event"], "submitted")
        self.assertTrue(self.service.active)
        self.assertIn("warming was unavailable", self.service.last_error)
        self.assertEqual(self.submitted, ["explain reserves"])

    def test_status_exposes_command_window_and_playback_suppression_without_text(self):
        self.service.start("browser")
        self.accept("Ariadne")
        self.assertEqual(self.service.status()["state"], "awaiting_command")
        self.service.suppress(10)
        self.assertTrue(self.service.status()["suppressed"])
        self.clock.value += 10
        self.assertFalse(self.service.status()["suppressed"])
        self.assertEqual(self.service.status()["state"], "conversing")
        self.assertTrue(self.service.status()["conversation_active"])
        self.assertEqual(self.service.status()["conversation_idle_timeout_seconds"], 120)
        self.assertEqual(self.service.status()["sleep_phrases"], ["go to sleep", "Ariadne sleep", "sleep Ariadne", "sleep"])
        self.assertIn("Ariadne shut down", self.service.status()["stop_phrases"])
        self.assertEqual(self.service.status()["stop_action"], "microphone_only")

    def test_shutdown_aliases_stop_only_listening_and_plain_shutdown_requires_an_awake_conversation(self):
        observed = []
        self.service.on_spoken_stop = lambda: observed.append(self.service.active)
        for phrase in ("Ariadne shut down", "shut down Ariadne", "Ariadne shutdown", "Ariadneshutdown"):
            with self.subTest(phrase=phrase):
                self.service.start("browser", session_hours=None)
                self.assertEqual(self.accept(phrase)["event"], "stopped")
                self.assertFalse(self.service.active)
        self.service.start("browser", session_hours=None)
        self.assertEqual(self.accept("shut down")["event"], "ignored")
        self.assertEqual(self.accept("sleep", sequence=2)["event"], "ignored")
        self.accept("Ariadne explain reserves", sequence=3)
        self.assertEqual(self.accept("sleep", sequence=4)["event"], "asleep")
        self.assertTrue(self.service.active)
        self.accept("Ariadne explain another topic", sequence=5)
        self.assertEqual(self.accept("shut down", sequence=6)["event"], "stopped")
        self.assertEqual(observed, [False] * 5)
        self.assertEqual(self.submitted, ["explain reserves", "explain another topic"])

    def test_rapid_followups_share_context_until_sleep_then_require_a_fresh_wake(self):
        self.service.start("browser", session_hours=None)
        self.assertIsNone(self.service.conversation_id)
        self.assertEqual(self.accept("Ariadne explain reserves")["event"], "submitted")
        first = self.service.conversation_id
        self.assertEqual(self.accept("tell me more", sequence=2)["event"], "submitted")
        self.assertEqual(self.service.conversation_id, first)
        self.assertEqual(self.accept("go to sleep", sequence=3)["event"], "asleep")
        self.assertTrue(self.service.active)
        self.assertIsNone(self.service.conversation_id)
        self.assertEqual(self.accept("continue", sequence=4)["event"], "ignored")
        self.assertEqual(self.accept("Ariadne another topic", sequence=5)["event"], "submitted")
        self.assertNotEqual(self.service.conversation_id, first)
        self.assertEqual(self.submitted, ["explain reserves", "tell me more", "another topic"])

    def test_pending_followups_are_consumed_and_never_replayed_or_deferred(self):
        self.service.get_run = lambda run_id: {"status": "completed"}
        self.service.start("browser", session_hours=None)
        with mock.patch("ariadne.wake.threading.Thread"):
            self.assertEqual(self.accept("Ariadne explain reserves")["event"], "submitted")
            first = self.service.conversation_id
            for sequence, phrase in enumerate(("tell me more", "Ariadne tell me more", "Ariadne"), 2):
                self.assertEqual(self.accept(phrase, sequence=sequence)["event"], "busy")
            self.clock.value += 500
            self.assertTrue(self.service.status()["conversation_active"])
            with mock.patch("ariadne.wake.time.sleep"):
                self.service._watch_run("mission-1", self.service.lease)
            self.assertEqual(self.submitted, ["explain reserves"])
            with self.assertRaises(ValueError):
                self.accept("tell me more", sequence=4)
            self.assertEqual(self.accept("tell me more", sequence=5)["event"], "submitted")
            self.assertEqual(self.service.conversation_id, first)

    def test_sleep_during_pending_work_silences_its_response_without_stopping_the_pc_worker(self):
        spoken, stops = mock.Mock(), mock.Mock()
        self.pc_ack_session(spoken)
        self.service.on_spoken_stop = stops
        self.service.get_run = lambda run_id: {"status": "completed", "context": [{"role": "assistant", "content": "A late answer."}]}
        with mock.patch("ariadne.wake.threading.Thread"), mock.patch("ariadne.wake.time.sleep"):
            self.accept("Ariadne explain reserves", source="pc")
            self.assertEqual(self.accept("sleep Ariadne", sequence=2, source="pc")["event"], "asleep")
            self.assertEqual(self.service.pending_run, "mission-1")
            self.assertTrue(self.service.active)
            self.service._watch_run("mission-1", self.service.lease)
        spoken.assert_not_called()
        stops.assert_not_called()
        self.assertIsNone(self.service.pending_run)
        self.assertIsNone(self.service.conversation_id)
        self.assertEqual(self.accept("continue", sequence=3, source="pc")["event"], "ignored")

    def test_unprefixed_stop_works_during_pending_work_and_disables_startup_after_stop(self):
        observed = []
        self.service.on_spoken_stop = lambda: observed.append(self.service.active)
        self.service.get_run = lambda run_id: {"status": "running"}
        self.service.start("browser", session_hours=None)
        with mock.patch("ariadne.wake.threading.Thread"):
            self.accept("Ariadne explain reserves")
        self.assertEqual(self.accept("stop listening", sequence=2)["event"], "stopped")
        self.assertEqual(observed, [False])
        self.assertIsNone(self.service.conversation_id)
        self.assertEqual(self.submitted, ["explain reserves"])

    def test_pending_suppression_drops_echoed_sleep_stop_and_commands(self):
        self.service.get_run = lambda run_id: {"status": "running"}
        self.service.start("browser", session_hours=None)
        with mock.patch("ariadne.wake.threading.Thread"), mock.patch("ariadne.wake.threading.Timer"):
            self.accept("Ariadne explain reserves")
            first = self.service.conversation_id
            self.service.suppress(5)
            for sequence, phrase in enumerate(("go to sleep", "stop listening", "Ariadne delete that"), 2):
                self.assertEqual(self.accept(phrase, sequence=sequence)["event"], "suppressed")
            self.assertTrue(self.service.active)
            self.assertEqual(self.service.conversation_id, first)
            self.clock.value += 5
            self.assertEqual(self.accept("go to sleep", sequence=5)["event"], "asleep")

    def test_followup_after_paused_rejection_requires_a_new_wake_but_sleep_always_wins(self):
        self.service.start("browser", session_hours=None)
        self.accept("Ariadne explain reserves")
        self.service.blocked = lambda: True
        self.assertEqual(self.accept("tell me more", sequence=2)["event"], "busy")
        self.assertIsNone(self.service.conversation_id)
        self.assertEqual(self.accept("go to sleep", sequence=3)["event"], "asleep")
        self.service.blocked = lambda: False
        self.assertEqual(self.accept("continue", sequence=4)["event"], "ignored")

    def test_slow_inference_and_playback_leave_a_full_idle_window_after_audio_tail(self):
        self.pc_ack_session(None)
        def completed(run_id):
            self.clock.value += 90
            return {"status": "completed", "context": [{"role": "assistant", "content": "Ready."}]}
        self.service.get_run = completed
        with mock.patch("ariadne.wake.threading.Thread"), mock.patch("ariadne.wake.threading.Timer"), mock.patch("ariadne.wake.time.sleep"):
            self.accept("Ariadne explain reserves", source="pc")
            first = self.service.conversation_id
            def speak(text):
                self.clock.value += 30
                self.assertTrue(self.service.status()["conversation_active"])
                self.assertEqual(self.accept("go to sleep", sequence=2, source="pc")["event"], "suppressed")
            self.service.speak = speak
            self.service._watch_run("mission-1", self.service.lease)
        self.assertEqual(self.service.gate.conversation_until, self.clock.value + 2 + CONVERSATION_IDLE_SECONDS)
        self.clock.value += 2 + CONVERSATION_IDLE_SECONDS - .01
        self.assertEqual(self.service.conversation_id, first)
        with mock.patch("ariadne.wake.threading.Thread"):
            self.assertEqual(self.accept("tell me more", sequence=3, source="pc")["event"], "submitted")

    def test_timed_out_result_watcher_clears_context_without_replaying_the_mission(self):
        self.service.get_run = lambda run_id: {"status": "running"}
        self.service.start("browser", session_hours=None)
        with mock.patch("ariadne.wake.threading.Thread"), mock.patch("ariadne.wake.time.sleep"):
            self.accept("Ariadne explain reserves")
            self.service._watch_run("mission-1", self.service.lease)
        self.assertIsNone(self.service.pending_run)
        self.assertIsNone(self.service.conversation_id)
        self.assertIn("30 minutes", self.service.last_error)
        self.assertEqual(self.submitted, ["explain reserves"])

    def test_pc_worker_control_lifecycle_never_replays_sleep_or_stale_utterances(self):
        self.pc_ack_session(None)
        lease = self.service.lease
        messages = [{"event": "ready"}] + [{"event": "utterance", "text": phrase} for phrase in
            ("Ariadne explain reserves", "tell me more", "go to sleep", "continue", "stop listening")]
        process = mock.Mock()
        process.stdout = io.BytesIO(b"".join(json.dumps(message).encode("ascii") + b"\n" for message in messages))
        process.poll.return_value = None
        self.service.process = process
        self.service._read_worker(process, lease)
        self.assertEqual(self.submitted, ["explain reserves", "tell me more"])
        self.assertFalse(self.service.active)
        self.assertIsNone(self.service.conversation_id)
        self.assertEqual([json.loads(call.args[0]) for call in process.stdin.write.call_args_list],
                         [{"action": "unmute"}, {"action": "stop"}])
        process.wait.assert_called_once_with(timeout=1)
        process.stdin.close.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_pc_start_uses_one_owned_fixed_worker_and_stop_releases_it_without_hardware(self):
        for duration, seconds in ((8, "28800"), (None, "0")):
            with self.subTest(duration=duration), \
                    mock.patch("ariadne.media_tools.load_profile", return_value={}), \
                    mock.patch("ariadne.media_tools.backend_status", return_value={"ready": True}), \
                    mock.patch("ariadne.wake.subprocess.Popen") as launch, \
                    mock.patch("ariadne.wake.threading.Thread") as thread, \
                    mock.patch("ariadne.wake.threading.Timer"):
                status = self.service.start("pc", session_hours=duration)
                self.assertTrue(status["active"])
                self.assertFalse(status["conversation_active"])
                self.assertIsNone(status["conversation_id"])
                argv = launch.call_args.args[0]
                self.assertEqual(argv[1:3], ["-I", "-B"])
                self.assertEqual(Path(argv[3]), Path.cwd() / "scripts/wake_listener.py")
                self.assertEqual(argv[-2:], ["--session-seconds", seconds])
                self.assertEqual(thread.call_count, 1)
                launch.return_value.wait.return_value = 0
                self.service.stop()
                launch.assert_called_once()
                launch.return_value.stdin.write.assert_called_once_with(b'{"action":"stop"}\n')
                launch.return_value.wait.assert_called_once_with(timeout=1)
                launch.return_value.stdin.close.assert_called_once()
                launch.return_value.stdout.close.assert_called_once()
                self.assertFalse(self.service.active)

    def test_old_worker_or_result_cannot_rearm_a_restarted_session(self):
        self.service.start("browser", session_hours=None)
        self.accept("Ariadne explain reserves")
        old = self.service.lease
        self.service.stop()
        self.service.start("browser", session_hours=None)
        process = mock.Mock()
        process.stdout = io.BytesIO(b'{"event":"utterance","text":"Ariadne stale request"}\n')
        self.service._read_worker(process, old)
        self.service.get_run = mock.Mock()
        self.service.speak = mock.Mock()
        with mock.patch("ariadne.wake.time.sleep"):
            self.service._watch_run("old-run", old)
        self.service.get_run.assert_not_called()
        self.service.speak.assert_not_called()
        self.assertTrue(self.service.active)
        self.assertIsNone(self.service.conversation_id)
        self.assertEqual(self.submitted, ["explain reserves"])

    def test_result_watcher_start_failure_stops_capture_and_never_retries_submission(self):
        self.service.get_run = lambda run_id: {"status": "completed"}
        self.service.start("browser", session_hours=None)
        with mock.patch("ariadne.wake.threading.Thread", side_effect=RuntimeError("No thread available")):
            with self.assertRaises(RuntimeError):
                self.accept("Ariadne explain reserves")
        self.assertEqual(self.submitted, ["explain reserves"])
        self.assertFalse(self.service.active)
        self.assertIsNone(self.service.pending_run)
        self.assertIsNone(self.service.conversation_id)
        self.assertIn("saved mission", self.service.last_error)

    def test_expiry_and_stop_revoke_listening(self):
        self.service.start("browser")
        self.clock.value += MAX_SESSION_SECONDS
        self.assertEqual(self.service.status()["state"], "expired")
        self.assertFalse(self.service.active)
        self.assertIsNone(self.service.status()["lease"])
        with self.assertRaises(ValueError):
            self.accept("Ariadne report")

    def test_until_stopped_has_no_expiry_timer_and_remains_active_after_eight_hours(self):
        with mock.patch("ariadne.wake.threading.Timer") as timer:
            status = self.service.start("browser", session_hours=None)
        timer.assert_not_called()
        self.assertIsNone(status["session_limit_hours"])
        self.clock.value += MAX_SESSION_SECONDS * 10
        self.assertTrue(self.service.status()["active"])
        self.assertEqual(self.accept("Ariadne report")["event"], "submitted")
        self.service.stop()
        self.assertFalse(self.service.active)

    def test_duration_is_strict_and_cannot_silently_change_an_active_session(self):
        for mode in ([], {}, True, 1, "unknown"):
            with self.assertRaises(ValueError):
                self.service.start(mode)
        for duration in (True, False, -1, 0, 1, 8.0, "8", "until_stopped", [], {}):
            with self.assertRaises(ValueError):
                self.service.start("browser", session_hours=duration)
            self.assertFalse(self.service.active)
        self.service.start("browser", session_hours=8)
        with self.assertRaisesRegex(ValueError, "duration"):
            self.service.start("browser", session_hours=None)

    def test_spoken_stop_listening_revokes_session_without_a_model_call(self):
        self.service.start("browser")
        self.assertEqual(self.accept("Ariadne stop listening")["event"], "stopped")
        self.assertFalse(self.service.active)
        self.assertEqual(self.submitted, [])

    def test_spoken_stop_disables_saved_startup_only_after_capture_stops(self):
        observed = []
        self.service.on_spoken_stop = lambda: observed.append(self.service.active)
        self.service.start("browser", session_hours=None)
        self.accept("Ariadne stop listening")
        self.assertEqual(observed, [False])
        self.service.start("browser", session_hours=None)
        self.service.stop()  # Normal server shutdown must preserve the preference.
        self.assertEqual(observed, [False])

    def test_spoken_stop_preference_failure_leaves_capture_stopped_and_reports_warning(self):
        def fail_preference():
            raise OSError("Cannot persist preference")
        self.service.on_spoken_stop = fail_preference
        self.service.start("browser", session_hours=None)
        result = self.accept("Ariadne stop listening")
        self.assertEqual(result["event"], "stopped")
        self.assertIn("startup preference could not be saved", result["warning"])
        self.assertFalse(self.service.active)
        self.assertEqual(self.submitted, [])

    def pc_ack_session(self, speak):
        self.service.start("browser", session_hours=None)
        self.service.mode = "pc"  # Exercise PC acknowledgement without opening a microphone.
        self.service.speak = speak
        return self.service.lease

    def test_bare_pc_wake_acknowledges_once_then_opens_fresh_command_window(self):
        spoken = []
        def speak(text):
            spoken.append((text, self.service.status()["speaking"], self.service.status()["acknowledging"]))
        lease = self.pc_ack_session(speak)
        with mock.patch("ariadne.wake.threading.Thread") as thread, mock.patch("ariadne.wake.threading.Timer"):
            event = self.accept("Ariadne", source="pc")
            self.assertTrue(event["acknowledging"])
            self.assertTrue(self.service.speaking)
            self.assertEqual(self.service.gate.until, 0)
            self.assertEqual(self.accept("Ariadne", sequence=2, source="pc")["event"], "suppressed")
            self.service._acknowledge(lease)
            self.assertEqual(spoken, [("Listening.", True, True)])
            self.assertEqual(self.submitted, [])
            self.assertFalse(self.service.speaking)
            self.assertTrue(self.service.status()["suppressed"])
            self.clock.value += ACK_TAIL_SECONDS
            self.assertEqual(self.accept("Ariadne", sequence=3, source="pc")["event"], "acknowledging")
            self.assertEqual(thread.call_count, 1)
            self.service._arm_after_ack(lease)
            self.assertEqual(self.service.status()["state"], "awaiting_command")
            self.assertEqual(self.service.gate.until, self.clock.value + 12)
            self.assertFalse(self.service.status()["acknowledging"])
            self.assertEqual(self.accept("report readiness", sequence=4, source="pc")["event"], "submitted")
        self.assertEqual(self.submitted, ["report readiness"])

    def test_paused_or_pending_mission_never_starts_a_bare_wake_ack(self):
        spoken = mock.Mock()
        self.pc_ack_session(spoken)
        with mock.patch("ariadne.wake.threading.Thread") as thread:
            self.service.blocked = lambda: True
            self.assertEqual(self.accept("Ariadne", source="pc")["event"], "busy")
            self.service.blocked = lambda: False
            self.service.pending_run = "mission-1"
            self.assertEqual(self.accept("Ariadne", sequence=2, source="pc")["event"], "busy")
            thread.assert_not_called()
        spoken.assert_not_called()
        self.assertEqual(self.service.gate.until, 0)
        self.assertEqual(self.submitted, [])

    def test_ack_playback_failure_stops_capture_without_changing_startup_preference(self):
        def fail_speech(text):
            raise OSError("Audio output is unavailable")
        lease = self.pc_ack_session(fail_speech)
        self.service.on_spoken_stop = mock.Mock()
        with mock.patch("ariadne.wake.threading.Thread"):
            self.accept("Ariadne", source="pc")
        self.service._acknowledge(lease)
        self.assertFalse(self.service.active)
        self.assertEqual(self.service.state, "error")
        self.assertIn("acknowledgement could not be played", self.service.last_error)
        self.service.on_spoken_stop.assert_not_called()
        self.assertEqual(self.submitted, [])

    def test_stop_and_new_lease_during_ack_cannot_rearm_the_new_session(self):
        def stop_and_restart(text):
            self.service.stop()
            self.service.start("browser", session_hours=None)
            self.service.mode = "pc"
        lease = self.pc_ack_session(stop_and_restart)
        with mock.patch("ariadne.wake.threading.Thread"), mock.patch("ariadne.wake.threading.Timer") as timer:
            self.accept("Ariadne", source="pc")
            self.service._acknowledge(lease)
            self.service._arm_after_ack(lease)
            timer.assert_not_called()
        self.assertNotEqual(self.service.lease, lease)
        self.assertFalse(self.service.speaking)
        self.assertFalse(self.service.status()["acknowledging"])
        self.assertEqual(self.service.gate.until, 0)
        self.assertTrue(self.service.active)

    def test_other_playback_extends_ack_tail_before_the_command_window_opens(self):
        lease = self.pc_ack_session(mock.Mock())
        with mock.patch("ariadne.wake.threading.Thread"), mock.patch("ariadne.wake.threading.Timer"):
            self.accept("Ariadne", source="pc")
            self.service._acknowledge(lease)
            self.service.suppress(10)
            self.clock.value += ACK_TAIL_SECONDS
            self.service._arm_after_ack(lease)
            self.assertEqual(self.service.gate.until, 0)
            self.assertTrue(self.service.status()["acknowledging"])
            self.clock.value += 10
            self.service._arm_after_ack(lease)
        self.assertEqual(self.service.gate.until, self.clock.value + 12)

    def test_sleep_after_ack_tail_before_rearm_keeps_wake_only_worker_unmuted(self):
        lease = self.pc_ack_session(mock.Mock())
        with mock.patch("ariadne.wake.threading.Thread"), mock.patch("ariadne.wake.threading.Timer"), \
                mock.patch.object(self.service, "_worker_command") as command:
            self.accept("Ariadne", source="pc")
            first = self.service.conversation_id
            self.service._acknowledge(lease)
            self.clock.value += ACK_TAIL_SECONDS
            self.assertEqual(self.accept("go to sleep", sequence=2, source="pc")["event"], "asleep")
            command.assert_called_with("unmute")
            self.service._arm_after_ack(lease)
            self.assertTrue(self.service.active)
            self.assertFalse(self.service.status()["acknowledging"])
            self.assertIsNone(self.service.conversation_id)
            self.assertEqual(self.service.gate.until, 0)
            self.assertEqual(self.accept("Ariadne", sequence=3, source="pc")["event"], "awake")
            self.assertNotEqual(self.service.conversation_id, first)

    def test_stale_same_lease_ack_callbacks_cannot_clear_or_unmute_a_new_ack(self):
        spoken = mock.Mock()
        lease = self.pc_ack_session(spoken)
        with mock.patch("ariadne.wake.threading.Thread") as thread, \
                mock.patch("ariadne.wake.threading.Timer") as timer, \
                mock.patch.object(self.service, "_worker_command") as command:
            self.accept("Ariadne", source="pc")
            old = self.service.ack_token
            self.assertEqual(thread.call_args.kwargs["args"], (lease, old))
            self.service._acknowledge(lease, old)
            self.assertEqual(timer.call_args.kwargs["args"], (lease, old))
            self.clock.value += ACK_TAIL_SECONDS
            self.accept("go to sleep", sequence=2, source="pc")
            self.accept("Ariadne", sequence=3, source="pc")
            new = self.service.ack_token
            self.assertNotEqual(new, old)
            command.reset_mock()
            self.service._arm_after_ack(lease, old)
            self.service._acknowledge(lease, old)
            command.assert_not_called()
            self.assertTrue(self.service.speaking)
            self.assertEqual(self.service.ack_token, new)
            self.assertTrue(self.service.status()["acknowledging"])
            spoken.assert_called_once_with("Listening.")
            self.service._acknowledge(lease, new)
            self.clock.value += ACK_TAIL_SECONDS
            self.service._arm_after_ack(lease, new)
            self.assertFalse(self.service.speaking)
            self.assertFalse(self.service.status()["acknowledging"])
            command.assert_called_once_with("unmute")
            self.assertEqual(self.accept("tell me more", sequence=4, source="pc")["event"], "submitted")

    def test_browser_bare_wake_uses_no_pc_speaker_and_combined_command_needs_no_ack(self):
        spoken = mock.Mock()
        self.service.speak = spoken
        self.service.start("browser", session_hours=None)
        with mock.patch("ariadne.wake.threading.Thread") as thread:
            self.assertEqual(self.accept("Ariadne")["event"], "awake")
            self.service.mode = "pc"
            self.assertEqual(self.accept("Ariadne report readiness", sequence=2, source="pc")["event"], "submitted")
            thread.assert_not_called()
        spoken.assert_not_called()
        self.assertEqual(self.submitted, ["report readiness"])

    def test_uncertain_submission_stops_without_automatic_retry(self):
        self.service.submit = lambda command: {"unexpected": "result"}
        self.service.start("browser")
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.accept("Ariadne report")
        self.assertEqual(self.service.state, "error")
        self.assertFalse(self.service.active)

    def test_exception_after_run_is_saved_stops_without_replaying(self):
        def uncertain(command):
            self.submitted.append(command)
            raise OSError("Audit write failed after the mission was queued")
        self.service.submit = uncertain
        self.service.start("browser")
        with self.assertRaises(OSError):
            self.accept("Ariadne report")
        self.assertEqual(self.submitted, ["report"])
        self.assertFalse(self.service.active)
        self.assertIn("uncertain", self.service.status()["last_error"])

    def test_expiry_timer_stops_without_a_status_poll(self):
        with mock.patch("ariadne.wake.threading.Timer") as timer:
            self.service.start("browser")
        seconds, expire = timer.call_args.args
        self.assertEqual(seconds, MAX_SESSION_SECONDS)
        expire()
        self.assertFalse(self.service.active)
        self.assertEqual(self.service.state, "expired")
        timer.return_value.cancel.assert_called_once()

    def test_finished_run_uses_last_final_assistant_and_preserves_approval(self):
        run = {"status": "completed", "context": [
            {"role": "user", "content": "not the answer"},
            {"role": "assistant", "content": "not final", "tool_calls": [{"id": "x"}]},
            {"role": "tool", "content": "not the answer"},
            {"role": "assistant", "content": "The readiness report is available."}]}
        self.assertEqual(WakeService.response_text(run), "The readiness report is available.")
        self.assertIn("needs your approval", WakeService.response_text({"status": "waiting_approval"}))
        self.assertLessEqual(len(WakeService.response_text({"status": "completed", "context": [{"role": "assistant", "content": "x" * 5000}]})), 1000)

    def test_watching_result_mutes_during_blocking_speech_then_clears_pending(self):
        self.service.start("browser")
        self.service.mode = "pc"  # Exercise playback state without opening hardware.
        lease = self.service.lease
        self.service.pending_run = "mission-1"
        self.service.get_run = lambda run_id: {"status": "completed", "context": [{"role": "assistant", "content": "Done."}]}
        spoken = []
        self.service.speak = lambda text: spoken.append((text, self.service.speaking, self.accept("Ariadne unwanted", source="pc")["event"]))
        with mock.patch("ariadne.wake.time.sleep"):
            self.service._watch_run("mission-1", lease)
        self.assertEqual(spoken, [("Done.", True, "suppressed")])
        self.assertFalse(self.service.speaking)
        self.assertIsNone(self.service.pending_run)
        self.assertEqual(self.accept("Ariadne another", sequence=2, source="pc")["event"], "suppressed")

    def test_approval_notice_is_spoken_once_and_final_result_follows_owner_approval(self):
        self.service.start("browser")
        self.service.mode = "pc"
        self.service.pending_run = "mission-1"
        states = iter([{"status": "waiting_approval"}, {"status": "waiting_approval"}, {"status": "running"},
                       {"status": "completed", "context": [{"role": "assistant", "content": "The approved mission is complete."}]}])
        self.service.get_run = lambda run_id: next(states)
        spoken = []
        self.service.speak = spoken.append
        with mock.patch("ariadne.wake.time.sleep"):
            self.service._watch_run("mission-1", self.service.lease)
        self.assertEqual(len(spoken), 2)
        self.assertIn("needs your approval", spoken[0])
        self.assertEqual(spoken[1], "The approved mission is complete.")
        self.assertIsNone(self.service.pending_run)

    def test_speech_process_keeps_recognized_content_out_of_command_text(self):
        text = 'Read this literal $(do not execute) `text` " safely.'
        with mock.patch("ariadne.wake.os.name", "nt"), mock.patch("ariadne.wake.subprocess.run") as execute:
            execute.return_value.returncode = 0
            windows_speak_response(text)
        args, kwargs = execute.call_args
        self.assertNotIn(text, " ".join(args[0]))
        self.assertEqual(json.loads(kwargs["input"])["text"], text)
        self.assertEqual(kwargs["timeout"], 120)


class NativeCaptureBufferTests(unittest.TestCase):
    """Exercise the actual ctypes adapter against a fake WinMM, never a device."""

    @classmethod
    def setUpClass(cls):
        import sys
        old_path = sys.path[:]
        try:
            cls.worker = runpy.run_path(str(Path.cwd() / "scripts" / "wake_listener.py"), run_name="wake_capture_test")
        finally:
            sys.path[:] = old_path

    def test_native_buffers_copy_before_requeue_and_release_on_close(self):
        import ctypes
        import threading
        from types import SimpleNamespace
        calls = []
        class Function:
            def __init__(self, fn):
                self.fn = fn
            def __call__(self, *args):
                return self.fn(*args)
        def open_input(handle, device, fmt, *unused):
            handle._obj.value = 123
            observed = fmt._obj
            self.assertEqual((device, observed.format, observed.channels, observed.rate, observed.bytes_per_second, observed.alignment, observed.bits),
                             (0xFFFFFFFF, 1, 1, 16000, 32000, 2, 16))
            calls.append("open")
            return 0
        def prepare(handle, header, size):
            calls.append("prepare")
            header._obj.flags |= 2
            return 0
        def add(handle, header, size):
            calls.append("add")
            if header._obj.flags & 1:
                ctypes.memmove(header._obj.data, b"xxxx", 4)
            header._obj.flags &= ~1
            return 0
        fake = SimpleNamespace(waveInOpen=Function(open_input), waveInPrepareHeader=Function(prepare), waveInAddBuffer=Function(add))
        for name in ("waveInStart", "waveInStop", "waveInReset", "waveInClose", "waveInUnprepareHeader"):
            setattr(fake, name, Function(lambda *args, event=name: calls.append(event) or 0))
        with mock.patch.object(ctypes, "WinDLL", return_value=fake):
            source = self.worker["WindowsInput"]()
            self.assertEqual(len(source.buffers), 4)
            header = source.headers[0]
            ctypes.memmove(header.data, b"\x01\x00\x02\x00", 4)
            header.recorded, header.flags = 4, header.flags | 1
            self.assertEqual(next(source.chunks(threading.Event())), b"\x01\x00\x02\x00")
            self.assertEqual(calls.count("add"), 5)
            source.close()
            source.close()
        self.assertEqual(calls.count("waveInUnprepareHeader"), 4)
        self.assertEqual(calls.count("waveInClose"), 1)

    def exercise_worker_deadline(self, seconds):
        import argparse
        import sys
        import threading
        from types import SimpleNamespace
        counters = {"decoder_constructions": 0, "decoded_chunks": 0, "closed": 0}
        clock = Clock()
        class Decoder:
            def __init__(self, data_dir):
                counters["decoder_constructions"] += 1
            def feed(self, pcm):
                counters["decoded_chunks"] += 1
                return None
        class Source:
            def chunks(self, stopping):
                for _ in range(3):
                    clock.value += MAX_SESSION_SECONDS * 10
                    yield b"known PCM fixture"
            def close(self):
                counters["closed"] += 1
        args = SimpleNamespace(data_dir=Path.cwd() / "data", probe=False, test_wav=None, session_seconds=seconds)
        namespace = self.worker["main"].__globals__
        with mock.patch.dict(namespace, {"Decoder": Decoder, "WindowsInput": Source, "limit_continuous_worker": lambda: None, "emit": lambda event: None}), \
                mock.patch.object(argparse.ArgumentParser, "parse_args", return_value=args), \
                mock.patch.object(threading, "Thread"), mock.patch.object(sys, "addaudithook"), \
                mock.patch("time.monotonic", clock):
            namespace["main"]()
        return counters

    def test_until_stopped_worker_keeps_one_decoder_past_eight_hours(self):
        self.assertEqual(self.exercise_worker_deadline(0), {"decoder_constructions": 1, "decoded_chunks": 3, "closed": 1})

    def test_default_worker_deadline_closes_capture_after_eight_hours(self):
        self.assertEqual(self.exercise_worker_deadline(MAX_SESSION_SECONDS), {"decoder_constructions": 1, "decoded_chunks": 0, "closed": 1})


if __name__ == "__main__":
    unittest.main()
