# Targeted independent review

Reviewed on 2026-10-27. This is a focused code review of the local assistant's persistence, execution, API, startup, locking, and backup paths. It is not a security certification or a claim of universal model reliability.

## Result

The serious issues found in this review have fixes present in the final code snapshot inspected. No additional unresolved high-severity issue was identified within the reviewed paths. The recovery and launcher fixes below were inspected after the implementation agent applied them; the remaining verification limits still apply.

## Scope and method

Read `ariadne/store.py`, `engine.py`, `model.py`, `server.py`, `locking.py`, and `scripts/provision.py`, `launch.py`, and `backup.py`. Read `config.py` and the relevant entry point as dependencies, plus the core, model, recovery, and boundary tests. Focused on authority checks, model output handling, duplicate execution after interruption, storage restrictions, and recovery artifacts.

There is no Git baseline available in this workspace. Historical blame, comparison against earlier committed behavior, and third-party dependency auditing were therefore outside this review. This pass did not modify application code, download components, invoke external business actions, or independently repeat the full test suite or model qualification.

## Findings resolved during review

| Finding and consequence | Final implementation inspected | Relevant regression evidence |
| --- | --- | --- |
| Approval insertion used eight placeholders for seven columns, preventing gated actions from reaching the approval workflow. | `Store.approval` names all seven columns and uses seven placeholders. | `test_approval_persists_then_executes_exactly_once`; rejected approvals are also tested. |
| An overdue schedule encountering a full queue could prevent queued missions from ever draining. | The engine advances its scheduler check time and isolates queue-full handling; the store stops scheduling when capacity is reached. | `test_full_queue_does_not_deadlock_worker`. |
| A completed model request could overwrite a cancellation made while it was running. | `Store.update_run` excludes terminal states; the engine also checks run state after inference. | `test_cancel_during_model_request_never_executes_result`. |
| Scheduled enqueue and schedule advancement were separate commits. A power loss between them could leave a queued mission and an overdue schedule, producing a duplicate after restart. | `Store.due_schedules` now inserts the mission, its events, and the schedule pointer in one `BEGIN IMMEDIATE` transaction through `_insert_run`. Normal queue capacity checking and insertion are transactional as well. | `test_schedule_enqueue_and_advancement_rollback_together` injects a database failure and checks rollback; normal no-overlap behavior is also tested. |
| An incomplete model answer at its output limit could be labeled completed. | `Ollama.chat` raises `ModelError` for `done_reason == "length"` before returning the response to the engine. | `test_output_limit_is_a_failure_not_completion`. |
| Backup destination inside the workspace could cause the ZIP to archive itself and grow without bound. Snapshot connections also remained open during Windows temporary-directory cleanup. | Backup rejects destinations inside the workspace and closes both SQLite connections before cleanup. | `test_backup_cannot_include_itself`; `test_consistent_backup_closes_handles`. |
| Persisted local speech configuration was omitted from backups, so a fresh restore could lose transcription capability despite preserved model assets. | The private-configuration allowlist now includes validated `voice-config.json`. | The backup test now checks its presence. |
| Missing bundled runtime allowed the launcher to silently fall through to an unrelated default Ollama listener. | Launcher now stops with an explicit provisioning/configuration message unless `ARIADNE_OLLAMA_URL` was supplied. Existing bundled-service reuse checks the recorded process's executable. | This branch was verified by code inspection; a dedicated launcher regression was not present when inspected. |
| Configured storage ancestors could route token creation or other operations through a junction, including onto the excluded E: drive. | Shared path validation rejects existing symbolic links/reparse points and checks the resolved drive. Provisioning, launcher setup, backup destinations, and backup files use that validation. Workspace backup traversal also omits junctions and linked files. | Configuration/link and hard-link boundary tests exist. Windows symbolic-link tests require privileges and may be skipped. |

## Evidence and remaining limits

The implementation agent's final reported suite result is **41 tests: 38 passed and three skipped for Windows symbolic-link privileges**. This includes the added scheduled-enqueue rollback regression. This reviewer inspected test intent and assertions rather than independently rerunning the suite.

The implementation agent also reported a real local business-file mission and a Windows speech-to-Whisper round trip. Those are useful integration examples. They do not establish performance across arbitrary tasks, capture from the physical microphone, a disconnected cold boot, or a second LAN client. The reported 8B and 4B timings had uncontrolled warm/cold conditions and do not support a general speed or energy claim.

The following targeted gaps remain appropriate for appliance qualification:

- Add a launcher test covering absent runtime, unknown existing listener, and explicitly configured model service. The current process check establishes that a recorded PID has the expected executable path; this review did not independently establish socket ownership or inspect that process's environment.
- Run the Windows link/junction cases with sufficient privileges on an allowed test disk. Static checks cannot establish resistance to every concurrent local filesystem replacement race.
- Exercise process termination and restart across pending approval, executing action, and scheduled enqueue boundaries. The database-trigger test establishes transaction rollback, but is not a physical power-loss or storage-durability test.
- Complete a restore into a fresh allowed directory with the WAN disconnected, including token, knowledge, artifacts, voice configuration, and separately preserved runtimes/models. The backup gives a consistent SQLite snapshot; workspace files copied while missions run are not one global point-in-time filesystem snapshot.

The reviewed design records interrupted active work and uncertain approved actions instead of automatically replaying them. It does not provide a distributed exactly-once guarantee for an arbitrary external service. Owner-configured connectors, model endpoints, and host runtime binaries remain part of the deployment's trust boundary.

## Final launcher addendum

Re-read the final `scripts/launch.py` after existing-worker detection was added. It probes the local `server.lock` through `InstanceLock`; an already-held lock causes it to print the application URL and the pairing-key file's path, optionally open the browser, and return. That discovery branch contains no HTTP request, bearer header, token-value output, or credential transmission to a listener. `load_settings()` still performs the normal local token loading and storage validation used by application startup. Model-service readiness checks remain unauthenticated version requests through the provisioning helper.

The implementation agent reports that the final launcher with `--no-browser` returned exit status 0 when the existing worker held the lock. This reviewer confirmed the branch by code inspection and did not run further tests or send any authenticated discovery request.
