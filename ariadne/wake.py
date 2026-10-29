"""Explicitly enabled, local wake-word sessions. No LLM runs while listening.

The PC adapter keeps the pinned Sherpa recognizer in one owned process; the
browser adapter accepts final utterances from the paired, same-origin UI.
Recognized speech is input, not authentication, and grants no new tool powers.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import _allowed_path

ROOT = Path(__file__).resolve().parents[1]
MAX_SESSION_SECONDS = 8 * 60 * 60
ACK_TAIL_SECONDS = 0.4
_WAKE = re.compile(r"^\s*(?:hey[\s,]+)?ariadne(?=$|[\s,.:;!?—–-])[\s,.:;!?—–-]*", re.I)


class WakeGate:
    """A final-utterance gate with a bounded command window and replay guard."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.until = 0.0
        self.suppressed_until = 0.0
        self.last_command = ""
        self.last_command_at = -math.inf

    def reset(self):
        self.until = 0.0

    def suppress(self, seconds):
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or not 0 <= seconds <= 180:
            raise ValueError("Wake suppression must be between zero and 180 seconds.")
        self.reset()
        self.suppressed_until = max(self.suppressed_until, self.clock() + seconds)

    def accept(self, text):
        if type(text) is not str or len(text) > 3000 or any(ord(c) < 32 and c not in "\n\t\r" for c in text):
            raise ValueError("A final utterance must be text within 3000 characters without controls.")
        now = self.clock()
        if now < self.suppressed_until:
            return {"event": "suppressed"}
        text = " ".join(text.split())
        if not text:
            return {"event": "ignored"}
        wake = _WAKE.match(text)
        if wake:
            text = text[wake.end():].strip()
            if not text:
                self.until = now + 12
                return {"event": "awake", "command_window_seconds": 12}
        elif now >= self.until:
            return {"event": "ignored"}
        self.reset()
        if text.casefold().strip(" .!?,") in {"stop listening", "stop microphone", "disable wake word", "stop wake word"}:
            return {"event": "stop"}
        if text.casefold().strip(" .!?,") in {"cancel", "cancel that", "never mind", "nevermind", "go to sleep"}:
            return {"event": "cancelled"}
        canonical = text.casefold().strip(" .!?,")
        if canonical == self.last_command and now - self.last_command_at < 10:
            return {"event": "duplicate"}
        self.last_command, self.last_command_at = canonical, now
        return {"event": "command", "command": text}


def windows_speak_response(text):
    """Speak locally and return only after playback, keeping capture suppressed."""
    if os.name != "nt":
        raise ValueError("Direct PC speech playback is available on Windows only.")
    if type(text) is not str or not text.strip() or len(text) > 1000 or "\x00" in text:
        raise ValueError("A spoken response must contain at most 1000 characters.")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Speech; $p=[Console]::In.ReadToEnd() | ConvertFrom-Json; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; try { $s.Speak([string]$p.text) } finally { $s.Dispose() }"
    try:
        result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command", command],
                                input=json.dumps({"text": text}, ensure_ascii=True).encode("ascii"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ValueError("Windows speech playback failed. Review the mission in the UI.")
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Windows speech playback failed or timed out. Review the mission in the UI.") from None


class WakeService:
    """Server-owned microphone session; callbacks retain normal mission policy.

    submit(command) returns the newly queued run dictionary. blocked() returns
    true when the server is paused or should reject another mission. get_run(id)
    returns the existing run, and speak(text) blocks through local playback.
    No callback is invoked for background speech without a leading wake word.
    """

    def __init__(self, settings, submit, *, blocked=None, get_run=None, speak=None, on_spoken_stop=None, clock=time.monotonic):
        self.settings, self.submit = settings, submit
        self.blocked, self.get_run, self.speak = blocked, get_run, speak
        self.on_spoken_stop = on_spoken_stop
        self.clock = clock
        self.lock = threading.RLock()
        self.gate = WakeGate(clock)
        self.active = False
        self.mode = None
        self.state = "stopped"
        self.lease = ""
        self.started = 0.0
        self.sequence = 0
        self.process = None
        self.last_event = None
        self.last_error = None
        self.pending_run = None
        self.speaking = False
        self.expiry_timer = None
        self.session_hours = 8
        self.ack_lease = None
        self.ack_timer = None

    def status(self):
        with self.lock:
            if self.active and self.session_hours is not None and self.clock() - self.started >= self.session_hours * 3600:
                self._stop("expired")
            suppressed = self.speaking or self.clock() < self.gate.suppressed_until
            state = self.state
            if self.active and self.state == "listening" and not suppressed:
                state = "working" if self.pending_run else "awaiting_command" if self.clock() < self.gate.until else "listening"
            return {"word": "Ariadne", "active": self.active, "mode": self.mode, "state": state,
                    "lease": self.lease if self.active else None, "last_event": self.last_event,
                    "last_error": self.last_error, "pending_run_id": self.pending_run,
                    "speaking": self.speaking, "suppressed": suppressed, "session_limit_hours": self.session_hours,
                    "acknowledging": bool(self.active and self.ack_lease == self.lease),
                    "command_window_seconds": 12, "idle_llm_calls": 0,
                    "pc_supported": os.name == "nt", "audio_saved": False,
                    "note": "Speech is not identity verification. Existing mission limits and approvals still apply."}

    def start(self, mode="pc", session_hours=8):
        if type(mode) is not str or mode not in {"pc", "browser"}:
            raise ValueError("Wake listening mode must be pc or browser.")
        if session_hours is not None and (type(session_hours) is not int or session_hours != 8):
            raise ValueError("Wake listening duration must be 8 hours or null for until stopped.")
        with self.lock:
            if self.active:
                if self.mode != mode or self.session_hours != session_hours:
                    raise ValueError("Stop the current microphone session before switching devices or duration.")
                return self.status()
            if mode == "pc" and os.name != "nt":
                raise ValueError("PC microphone capture currently supports Windows. Use the paired browser on other hosts.")
            self.gate = WakeGate(self.clock)
            self.lease = secrets.token_urlsafe(24)
            self.started, self.sequence = self.clock(), 0
            self.session_hours = session_hours
            self.active, self.mode, self.state = True, mode, "starting" if mode == "pc" else "listening"
            self.last_event = "started"
            self.last_error, self.pending_run = None, None
            if mode == "pc":
                try:
                    from .media_tools import backend_status, load_profile
                    if not backend_status(load_profile(self.settings.data_dir), "sherpa")["ready"]:
                        raise ValueError("The pinned local Sherpa speech assets are not ready.")
                    script = _allowed_path(ROOT / "scripts" / "wake_listener.py")
                    info = script.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("The fixed wake listener must be a regular unlinked file.")
                    from .worker import _environment
                    self.process = subprocess.Popen([sys.executable, "-I", "-B", str(script), "--data-dir", str(self.settings.data_dir),
                        "--session-seconds", str(0 if session_hours is None else session_hours * 3600)],
                        cwd=ROOT, env=_environment(None), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    process, lease = self.process, self.lease
                    threading.Thread(target=self._read_worker, args=(process, lease), name="ariadne-wake", daemon=True).start()
                except Exception:
                    self._stop("error")
                    self.last_error = "PC microphone startup failed. Check local speech assets and microphone access."
                    raise
            if session_hours is not None:
                lease = self.lease
                def expire():
                    with self.lock:
                        if self.active and self.lease == lease:
                            self._stop("expired")
                self.expiry_timer = threading.Timer(session_hours * 3600, expire)
                self.expiry_timer.daemon = True
                self.expiry_timer.start()
            return self.status()

    def _stop(self, state):
        if self.ack_timer:
            self.ack_timer.cancel()
            self.ack_timer = None
        self.ack_lease = None
        if self.expiry_timer:
            self.expiry_timer.cancel()
            self.expiry_timer = None
        process, self.process = self.process, None
        self.active, self.state, self.lease = False, state, ""
        self.gate.reset()
        self.speaking = False
        if process:
            try:
                if process.stdin:
                    process.stdin.write(b'{"action":"stop"}\n')
                    process.stdin.flush()
                process.wait(timeout=1)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            for stream in (process.stdin, process.stdout):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def stop(self):
        with self.lock:
            self._stop("stopped")
            self.last_event = "stopped"
            return self.status()

    close = stop

    def suppress(self, seconds=5):
        with self.lock:
            self.gate.suppress(seconds)
            self._worker_command("mute")
            lease = self.lease
            def unmute():
                with self.lock:
                    if self.active and self.lease == lease and not self.speaking and not self.ack_lease and self.clock() >= self.gate.suppressed_until:
                        self._worker_command("unmute")
            timer = threading.Timer(seconds, unmute)
            timer.daemon = True
            timer.start()

    def _worker_command(self, action):
        process = self.process
        if process and process.poll() is None:
            try:
                process.stdin.write((json.dumps({"action": action}) + "\n").encode("ascii"))
                process.stdin.flush()
            except (OSError, ValueError):
                pass

    def accept(self, text, *, source="browser", lease, sequence):
        with self.lock:
            self.status()
            if not self.active or not secrets.compare_digest(str(lease), self.lease) or source != self.mode:
                raise ValueError("This wake session is inactive or belongs to a different microphone.")
            if type(sequence) is not int or not self.sequence < sequence <= 1_000_000_000:
                raise ValueError("Wake utterance sequence must increase within its current session.")
            self.sequence = sequence
            if self.speaking:
                return {"event": "suppressed"}
            event = self.gate.accept(text)
            if event["event"] == "stop":
                self._stop("stopped")
                self.last_event = "stopped"
                if self.on_spoken_stop:
                    try:
                        self.on_spoken_stop()
                    except Exception:
                        self.last_error = "Listening stopped, but the startup preference could not be saved. Disable resume in the console before restarting Ariadne."
                        return {"event": "stopped", "warning": self.last_error}
                return {"event": "stopped"}
            if event["event"] == "awake" and self.mode == "pc" and self.speak:
                if self.ack_lease:
                    self.gate.reset()
                    return {"event": "acknowledging"}
                try:
                    blocked = self.pending_run or self.blocked and self.blocked()
                except Exception:
                    self._stop("error")
                    self.last_error = "Mission state could not be checked. Review the console before restarting listening."
                    raise
                if blocked:
                    self.gate.reset()
                    self.last_event = "busy"
                    return {"event": "busy", "message": "Ariadne is paused or already working. Review the current mission."}
                self.ack_lease = self.lease
                self.speaking = True
                self.gate.reset()
                self._worker_command("mute")
                self.last_event = "acknowledging"
                try:
                    threading.Thread(target=self._acknowledge, args=(self.lease,), name="ariadne-wake-ack", daemon=True).start()
                except Exception:
                    self._stop("error")
                    self.last_error = "The local wake acknowledgement could not start. Restart listening from the console."
                    raise
                return {**event, "acknowledging": True}
            if event["event"] == "command":
                try:
                    blocked = self.pending_run or self.blocked and self.blocked()
                except Exception:
                    self._stop("error")
                    self.last_error = "Mission state could not be checked. Review the console before restarting listening."
                    raise
                if blocked:
                    self.gate.reset()
                    self.last_event = "busy"
                    return {"event": "busy", "message": "Ariadne is paused or already working. Review the current mission."}
                try:
                    run = self.submit(event["command"])
                except Exception:
                    self._stop("error")
                    self.last_event = "rejected"
                    self.last_error = "Spoken mission submission returned an uncertain result. Review the UI before restarting listening."
                    raise
                if type(run) is not dict or not isinstance(run.get("id"), str) or not 1 <= len(run["id"]) <= 128:
                    self.last_error = "Spoken mission submission returned an uncertain result. Review the UI."
                    self.state = "error"
                    self._stop("error")
                    raise ValueError(self.last_error)
                self.last_event = "submitted"
                self.pending_run = run["id"] if self.get_run else None
                if self.get_run:
                    threading.Thread(target=self._watch_run, args=(run["id"], self.lease), name="ariadne-spoken-result", daemon=True).start()
                return {"event": "submitted", "run": run}
            if event["event"] not in {"ignored", "suppressed"}:
                self.last_event = event["event"]
            return event

    def _acknowledge(self, lease):
        """One short PC acknowledgement; no mission or LLM call is made."""
        with self.lock:
            if not self.active or self.lease != lease or self.ack_lease != lease:
                return
        try:
            self.speak("Listening.")
        except Exception:
            with self.lock:
                if self.active and self.lease == lease and self.ack_lease == lease:
                    self._stop("error")
                    self.last_error = "The wake acknowledgement could not be played. Listening stopped; check PC audio output and restart listening."
            return
        with self.lock:
            if not self.active or self.lease != lease or self.ack_lease != lease:
                return
            self.speaking = False
            self.gate.suppress(ACK_TAIL_SECONDS)
            self._arm_after_ack(lease)

    def _arm_after_ack(self, lease):
        with self.lock:
            if not self.active or self.lease != lease or self.ack_lease != lease:
                return
            remaining = self.gate.suppressed_until - self.clock()
            if remaining > 0:
                self.ack_timer = threading.Timer(remaining, self._arm_after_ack, args=(lease,))
                self.ack_timer.daemon = True
                self.ack_timer.start()
                return
            self.ack_timer = None
            try:
                blocked = self.pending_run or self.blocked and self.blocked()
            except Exception:
                self._stop("error")
                self.last_error = "Mission state could not be checked. Review the console before restarting listening."
                return
            self.ack_lease = None
            if blocked:
                self.gate.reset()
                self.last_event = "busy"
            else:
                self.gate.until = self.clock() + 12
                self.last_event = "awake"
            self._worker_command("unmute")

    def _read_worker(self, process, lease):
        sequence = 0
        try:
            while True:
                line = process.stdout.readline(16_385)
                if not line:
                    break
                if len(line) > 16_384 or not line.endswith(b"\n"):
                    raise ValueError("Invalid listener message.")
                message = json.loads(line)
                if type(message) is not dict or message.get("event") not in {"ready", "utterance", "error"}:
                    raise ValueError("Invalid listener message.")
                with self.lock:
                    if not self.active or self.lease != lease:
                        return
                    if message.get("event") == "ready":
                        self.state = "listening"
                    elif message.get("event") == "utterance":
                        sequence += 1
                        self.accept(message.get("text"), source="pc", lease=lease, sequence=sequence)
                    elif message.get("event") == "error":
                        raise ValueError("Microphone listener failed.")
        except (OSError, ValueError, TypeError):
            pass
        finally:
            with self.lock:
                if self.active and self.lease == lease:
                    if self.session_hours is not None and self.clock() - self.started >= self.session_hours * 3600:
                        self._stop("expired")
                    else:
                        self.last_error = "The PC microphone listener stopped. Check the input device and restart listening."
                        self._stop("error")

    @staticmethod
    def response_text(run):
        status = run.get("status")
        if status == "waiting_approval":
            return "Ariadne needs your approval. Review the pending action in the console."
        if status != "completed":
            return "I couldn't finish that mission. Review its status in the console."
        if run.get("verification_status") == "failed":
            return "Output checks failed. Review the result in the console."
        context = run.get("context", [])
        for message in reversed(context if isinstance(context, list) else []):
            if isinstance(message, dict) and message.get("role") == "assistant" and not message.get("tool_calls"):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", content)
                    text = re.sub(r"```[\s\S]*?(?:```|$)", " Code is in the console. ", text)
                    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
                    text = re.sub(r"https?://\S+", "link in the console", text)
                    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*+][ \t]+(?=[^\s\d.])|\d+\.\s+)", "", text)
                    text = re.sub(r"(?<!\w)(\*\*|__)(.+?)\1(?!\w)", r"\2", text)
                    text = " ".join(text.replace("`", "").split())
                    words = text.split()
                    if len(text) > 420 or len(words) > 65:
                        brief = " ".join(words[:65])[:420]
                        if len(brief) < len(text) and len(brief) > 1 and not text[len(brief)].isspace():
                            brief = brief.rsplit(" ", 1)[0] or brief
                        sentences = list(re.finditer(r"[.!?](?=\s|$)", brief))
                        if sentences and sentences[-1].end() >= 80:
                            brief = brief[:sentences[-1].end()]
                        return brief.rstrip(" .,;:!?") + ". Full result in the console."
                    return text or "The result is in the console."
        return "The mission is complete. Its result is in the console."

    def _watch_run(self, run_id, lease):
        try:
            approval_announced = False
            for _ in range(1800):  # 30 minutes; never replay a mission on timeout.
                time.sleep(1)
                with self.lock:
                    if not self.active or self.lease != lease:
                        return
                run = self.get_run(run_id)
                if not run:
                    continue
                if run.get("status") == "waiting_approval":
                    if not approval_announced:
                        if not self._play_response(run, lease):
                            return
                        approval_announced = True
                    continue
                approval_announced = False
                if run.get("status") not in {"completed", "failed", "interrupted", "cancelled", "exhausted"}:
                    continue
                self._play_response(run, lease)
                return
            with self.lock:
                if self.lease == lease:
                    self.last_error = "The spoken mission is still active after 30 minutes. Review it in the console."
        except Exception:
            with self.lock:
                if self.lease == lease:
                    self.last_error = "The spoken result could not be played. The mission remains available in the console."
        finally:
            with self.lock:
                if self.lease == lease and self.pending_run == run_id:
                    self.pending_run = None

    def _play_response(self, run, lease):
        with self.lock:
            if not self.active or self.lease != lease:
                return False
            if not self.speak or self.mode != "pc":
                return True
            self.speaking = True
            self.gate.reset()
            self._worker_command("mute")
        try:
            self.speak(self.response_text(run))
        finally:
            with self.lock:
                if self.active and self.lease == lease:
                    self.speaking = False
                    self.suppress(2)
        return True
