# Verification record

Completed on 2026-10-27, America/Chicago, on this Windows PC. This records observed behavior rather than a claim of universal task success.

## Installed and running

- Python 3.13.15; an isolated `.venv` contains checksum-pinned Waitress 3.0.2.
- Official portable Ollama 0.40.12. Archive SHA-256 matched its published value; Windows reported a valid Authenticode signature from Ollama Inc.
- Ollama startup reported an NVIDIA GeForce RTX 5060 Ti with about 15.9 GiB VRAM, listening only on 127.0.0.1:11434, cloud disabled, and models stored under this project's C: data directory.
- `qwen3:8b` selected for this installation. Installed artifact: 5,225,388,164 bytes; digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
- `qwen3:4b` retained as a selectable smaller option. Installed artifact: 2,497,293,931 bytes; digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`.
- whisper.cpp CPU build b5454 and the revision-pinned English base model installed, with both SHA-256 hashes verified. Windows SAPI speech output available.
- Native application started using Waitress at http://127.0.0.1:8080. No application storage was placed on E:.

## Automated verification

`python -m unittest discover -s tests -v` completed **41 tests: 38 passed, 3 skipped**. Skips require Windows symbolic-link creation privileges unavailable to the test process. Tests cover real Windows file tools, traversal/device-name/hard-link rejection, persistent tokens, reserved-drive validation, arithmetic bounds, loopback connector requests, secret redaction, proxy/redirect rejection, slow-response deadlines, Docker invocation constraints, bounded model loops, approval/rejection, cancellation, restart recovery, queue saturation, schedule atomicity, authentication/origin/Host checks, knowledge retrieval and backup recovery.

The schedule atomicity regression injects a SQLite failure during schedule advancement and verifies that no orphan queued run survives. Retrying after rollback creates one mission.

`node --check ariadne/static/app.js` and Python compile checks passed. The Docker runner contract was tested with a mocked process; a real Docker daemon was not available. Docker Compose was not built or exercised here.

## Real local inference

The live verifier seeded an isolated knowledge brief for a fictional Cobalt Workshop with a stock ceiling of 12 units, asked the local model to search that brief and save a launch checklist, and inspected the resulting file.

| Model | Result | Model steps | Observed mission wall time | Generated tokens |
|---|---|---:|---:|---:|
| Qwen3 4B | Knowledge lookup and real file creation passed | 3 | 112.088 s | 3,255 |
| Qwen3 8B | Knowledge lookup and real file creation passed | 3 | 21.271 s | 180 |

These were individual qualification runs, with different model/loading/cache conditions and small runtime changes between runs. They are **not** controlled performance or energy benchmarks, and do not establish general savings. The resulting checklists are short drafts, not validated business plans.

Two additional 4B interface missions produced repetitive planning without tool calls and reached their response limit. This exposed an incorrect completion classification. The model adapter now rejects `done_reason: length`, reports a failed mission, and retains available token/duration usage. A regression test covers that condition.

A final **8B browser mission** successfully called `file_write`, called `file_read`, and returned the exact requested file contents: 3 steps, **2.618 seconds** wall time with the model already resident. Its screenshot is in `docs/screenshots/ui8b-success.jpg`. This establishes that the browser, API, worker, model, tools and persisted activity trail worked together for that task.

Live verifier details are preserved under local `data/verification/` and summarized in `data/verification-latest.json`. Disposable browser QA used a separate `.frontend-qa` state directory and no production token.

## Voice and interface

The live voice check synthesized “The local assistant is ready. Inventory is twelve units.” using Windows' local voice, converted it to PCM16 mono 16 kHz WAV, and passed it through local whisper.cpp. Recognized text was “The local assistant is ready. Inventory is 12 units.” No microphone recording was made for this test.

Browser checks covered pairing, lock/re-pair/reload, memory and knowledge editing, schedules, file browsing, literal rendering of HTML-like input, draft preservation, pause/resume, cancellation and approve/reject fixtures. Fixture actions were cancelled before execution. The browser console had no warnings/errors. Desktop 1280×720 and mobile 390×844 layouts were visually inspected; mobile had no horizontal overflow. A duplicate HTML ID found during QA was fixed.

The optional microphone capture flow itself was not tested with live personal audio or permission prompts. LAN HTTPS, a fresh disconnected installation, a full offline restore, hardware power interruption, and adversarial privileged junction races were not tested. No electricity or energy consumption measurements were taken.

## Independent review

A separate agent reviewed the core and provisioning/recovery paths. Findings led to fixes for approval SQL, queue-full scheduling, cancellation state protection, scheduled-run atomicity, linked storage/E: exclusion, backup self-inclusion, SQLite handle closure, voice-config backup and missing-runtime launcher behavior. See `docs/REVIEW.md` for review scope and limits. This is not a professional security certification.

An automatic approval check rejected a proposed launcher discovery request that would have sent a bearer token to an unverified local listener. That proposal was not applied. The shipped launcher performs no such credential-based discovery.
