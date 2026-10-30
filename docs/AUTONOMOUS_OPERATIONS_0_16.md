# Frozen offline operation workflows · 0.16

The new operation steps use Ariadne's existing **Offline routines** engine, queue, clock, global pause, revision checks, storage reservations and fail-stop receipts. There is no second scheduler, resident agent or model worker. One accepted child runs at a time through the established engine. Each step makes zero model calls and no network or external action calls.

## Build a workflow

The owner interface can save one to four different fixed procedures or typed operation steps. New definitions default to recurrence off. Review a manual invocation before deliberately enabling an interval. A routine's accepted name, operation arguments, source identities and output contracts stay frozen for that invocation; later definition edits affect future invocations only.

Supported typed operations:

| Operation ID | Arguments | Result |
|---|---|---|
| `business_snapshot` | Optional `cash_accounts` array | Accepted local contacts, stock, journal balance and explicitly selected cash accounts. |
| `erp_audit` | Workspace `path`, `as_of` date or explicit `today_utc` policy | Submitted invoice audit and ageing from the accepted ERPNext export. |
| `quote_draft` | Workspace request `path` | Exact quote and draft invoice using accepted local customer/item/stock records. |
| `market_audit` | Workspace `path` and `metadata_path` | Historical OHLCV coverage and validity audit. |
| `market_study` | Same `path` and `metadata_path` as a preceding market audit | Fixed historical strategy replay, walk-forward and reserved holdout study. |
| `device_status` | Empty object | Selected accepted saved device observations, with original observation clocks. |
| `device_snapshot` | Workspace `source_path` and actual aware `observed_at` | Report selected states from an accepted export; does not import them into the live control cache. |

Typed API steps have the form `{"operation":"business_snapshot","arguments":{}}`. They share the existing routine save, enqueue, run history, cancel and explicit verification APIs. Legacy named procedures remain compatible and may appear in the same routine. Each operation kind can appear once per routine; audit and study are distinct kinds.

`erp_audit` with `as_of:"today_utc"` explicitly resolves to the acceptance day's real UTC date before the plan is stored. A fixed date remains fixed. Device observation dates are never advanced by viewing or scheduling a report.

## Accepted inputs and evidence

At enqueue, guarded reads capture each workspace input once. A source that changes size, modification time or identity during that read is rejected. One consistent local-business read transaction and one selected device configuration/cache snapshot are reused across the invocation. These separate sources are not one global atomic point-in-time snapshot.

Accepted bytes are stored as bounded, deduplicated BLOBs in `operation_inputs` in the existing private main SQLite database. The public plan retains their original workspace paths, byte lengths and SHA-256 identities. The canonical SQLite backup therefore retains accepted inputs alongside queued definitions and receipts; they are excluded from factory exports with all other owner state. Model-writable workspace files cannot replace accepted source bytes. A changed or deleted original file after acceptance cannot silently change the workflow's calculation.

Each dispatch constructs its own provider instance. Only the fixed source-read methods consume accepted bytes; artifact replay and verification use actual guarded workspace files. Concurrent Operations desk reads keep their own inputs. The runtime never monkeypatches a shared provider, runs source code from inputs, imports a pickle, contacts a provider or chooses a tool from untrusted text.

Every accepted operation writes `procedures/<child-request-id>/operation.json` and `operation.md`, plus its exact business or market JSON artifact where applicable. The parent stores source-plan identity, stable child ID, byte counts and hashes in its durable receipt. The wrapper distinguishes acceptance time, generation time and original device/source observations. Completion is checked against the complete fixed artifact set before the next step starts.

Existing **Verify artifacts** supports these complete receipts. Structural **Compare** coverage is explicitly unsupported for typed operation steps, including their first invocation; a mixed routine can have only partial comparison coverage. The interface does not claim semantic equality or infer business/strategy changes from generation IDs, summaries or artifact hashes. Inspect the actual provider reports for their complete financial, market or device observations.

## Market qualification gate

A study requires a preceding market audit with exactly the same accepted dataset and assumptions. One shared captured copy supplies both. The actual audit implementation must report valid input and publish a verified completed receipt. A malformed, incomplete or duplicate dataset fails the audit step, disables recurrence and prevents study execution. A user-edited `valid:true` string in a workspace file does not replace that provider result or parent receipt.

All existing research assumptions remain explicit: costs, sessions, corporate-action policy, causal fills and train/test/holdout boundaries. This workflow is historical research, not a live/paper brokerage route or evidence of future profits.

## Pause, interruption and failure holds

The canonical global pause holds new work and is checked during the new bounded providers and before publication/completion. Cancelling or disabling a scheduled routine holds later steps at the established child boundary; an executing child may finish. No connected device write, refresh, live market read, installation, ledger posting, payment or message operation is accepted by this offline allowlist.

The parent commits its child-start marker before dispatch. The operation also commits its own procedure ledger marker before generating artifacts. Restarting a started unfinished routine interrupts it, disables recurrence and preserves any partial output; completed orphan child output is not adopted. A previously started operation child ID is never automatically retried. New attempts need new request IDs after reviewing the original receipt.

Missing, oversized, malformed or otherwise uncapturable input causes one durable recurrence hold rather than repeated due-time capture attempts. Partial capture inserts roll back atomically. Ordinary edits made while disabled preserve the existing failure reason. Resuming a held recurrence requires explicit `enabled:true` with the currently reviewed configuration revision; stale editors cannot remove a hold.

## Resource and offline scope

The established bounds remain four steps, eight active requests, 1,000 stored invocations and a conservative 256 MiB report-accounting budget with an 8 MB reservation per in-flight child. New private input snapshots have a separate **64 MiB lifetime budget**, at most **512 unique digests**, and at most **4 MB per blob**. Individual provider limits are smaller where appropriate: market CSV 2 MB, market assumptions 50 KB, business JSON 256 KiB and device exports/cache 64 KiB. Identical source bytes reuse a blob. Reaching a quota stops new work for owner review; Ariadne does not delete reports or accepted inputs automatically.

Factory recurrence stays off. A synthetic market example is for explicit qualification, never evidence that the owner's market dataset or strategy is available. A missing Home Assistant scope/cache yields explicit unavailable coverage, not inferred physical status. Live device actions remain in the separate individually approved gateway. The powered local server, its existing worker and the required input/model assets must be present for work to run; the new fixed workflows themselves require no model or public Internet.
