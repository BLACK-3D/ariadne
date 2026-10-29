# Offline routines and checked recurring workflows

Ariadne 0.9 adds ordered routines over the installed fixed report procedures. A routine reads saved local records and creates new report files without a language-model call, network connection or additional service. It does not change supplies, financial records, equipment or connected systems. Asking an AI agent to interpret the results still uses the selected model.

## Create a routine

Open **Workflows → Offline routines**. Review a starter or create a sequence of one to four different procedures:

- **Household continuity:** readiness, stock rotation, essential power budget and rebuilding dependencies.
- **Business handoff:** recorded inventory and trial balance.
- **Knowledge continuity:** imported-document provenance, bundled-reference integrity and the offline checklist.

Each step shows its fixed output files. The reports retain missing-data warnings and distinguish owner reports from verified source bytes. A readiness report does not prove equipment works or a physical task happened.

Save the routine with recurrence disabled for manual use, or explicitly enable its interval. The default interval is one day; supported intervals range from five minutes to one year. Intervals are elapsed time while Ariadne is available, rather than a wall-clock appointment. The existing daily briefing and Windows wake task remain separate.

Configuration updates include the revision that was reviewed. An intervening edit or failure hold rejects a stale update; refresh and review the current state before changing it. An old editor cannot silently undo a newly recorded failure stop.

**Run once** creates a unique invocation and freezes the name, definition revision, ordered procedures and expected outputs. Later definition edits apply to future invocations. A lost launch response is resolved by reading the original request ID; the interface never silently submits a replacement invocation.

## Autonomous execution and evidence

The existing single engine advances at most one fixed procedure at a time when no AI mission is ready. It checks due schedules on its existing clock. There is no added polling daemon, idle model invocation or cloud dependency. No energy or monetary saving has been measured.

Each child receives a stable request ID. The runner commits a start marker before calling it, then records its actual receipt, output paths, byte counts, hashes and warnings. Earlier completed artifacts are checked before another step begins. The history distinguishes completed, cancelled, failed and interrupted invocations. **Verify artifacts** performs an explicit current integrity check; merely viewing a saved receipt does not rehash its files.

Agents can use the read-only `routine_read` tool to inspect status or a compact summary of the latest recorded receipt. It preserves the parent request ID, every step's status, exact artifact totals and file hashes within the model's tool-output budget. Long warnings have explicit shortening and omission indicators; the full receipt remains available in the interface. This cannot create, enable, run or reschedule routines, and it does not perform fresh artifact verification.

## Pause, cancellation and recovery

Global **Pause autonomy** holds new steps and due work. A fixed procedure already executing can finish; pause and cancellation take effect at the next boundary. **Cancel** preserves completed reports and stops the remaining steps. Disabling recurrence also stops remaining scheduled work at a boundary; it does not cancel an independently requested manual run.

There is no overlap for a given routine and no flood of missed intervals. A substantive failure, cancellation, interrupted execution or failed artifact-integrity review disables recurrence and records a reason. Review the evidence and explicitly enable recurrence to continue with a new invocation. Failed or interrupted child IDs are never automatically retried. On restart, never-started queued requests can run; a started unfinished routine is interrupted even if one or more child reports completed. This avoids adopting output after an uncertain crash.

There are at most 32 configured routines, eight active invocations, 1,000 stored invocations and four steps per invocation. Report accounting is capped at 256 MiB. Before each child, its worst-case output allowance is reserved; verified successful output is charged by actual byte count. Failed or interrupted children retain the conservative reservation because partial output can exist. Reaching a quota stops new work for review. Ariadne never deletes owner reports to reclaim space automatically.

Routine metadata is stored in the main Ariadne database, so the existing consistent backup includes definitions and receipts alongside other state. Workspace report files remain part of the private backup. Source and factory appliance exports exclude owner routines and their reports.

## Specialist workflows and checked schedules

An AI workflow now saves its specialist selection. Manual runs freeze the current instructions and output checks and resolve **Automatic** routing at enqueue time. Editing a workflow without specifying a new specialist preserves the saved selection. Older workflows retain the `general` profile.

**Schedule** on an AI workflow creates a recurring mission from a snapshot of its instructions, expected checks and concrete specialist, resolved when the schedule is created. Editing or deleting the original workflow cannot alter that accepted snapshot. Create a new schedule to adopt a revised template. Freeform schedules continue to work; the API also accepts their explicit checks and specialist.

The existing queue, step budget, approval gates, pause, no-overlap and failure stops apply. A model's completed answer with failed expected-output checks disables its schedule. A rejected connected action also disables recurrence. Saving a workflow or schedule grants no new tool authority.

## Local verification

Run the full offline automated suite and maintained UI checks from the repository root:

```powershell
python -B -m unittest discover -s tests -v
python scripts/check_command_ui_contracts.py
node scripts/check_command_snapshot.cjs
node scripts/check_routine_ui.cjs
```

Node is only needed for the JavaScript development check. Tests exercise crash recovery, cross-instance scheduling, frozen plans, cancellation, pause, integrity failure, storage accounting and rejected or failed scheduled missions. Native-engine, GPU-model, microphone and complete portable-appliance qualification remain separate checks on the intended machine.
