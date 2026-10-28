# Independent shared-runtime review for 0.4

Reviewed 2026-10-28 using the differential-review skill. Scope: `tools.py`, `engine.py`, `worker.py`, `_worker_bootstrap.py`, `automation_tools.py`, `_automation_worker.py`, `business_tools.py` and `server.py`. The baseline is the saved 0.3.0 source ZIP, SHA-256 `ace8a4797cd365f2f15cc240fa72e1b84b374eb12d50601d96dcc1c649217a21`. This folder is not a Git checkout: `git status` reports “not a git repository,” so commit history and blame are unavailable. The archived source provides the concrete before/after comparison; no commit provenance is invented.

## Executive assessment

Conditional approval of the shared integration, subject to native browser qualification. The review covered all eight named files and their immediately relevant call paths. It did not review every line of the independent media/knowledge implementations, native package source code, browser internals, or third-party binary dependencies.

| Severity | Initially identified | Root fixes inspected |
| --- | ---: | ---: |
| High | 1 | 1 |
| Medium | 2 | 2 |
| Medium, semantic outcome handling | 1 | Root reports corrected; native browser regression still required |

## Changes and baseline invariants

The initial snapshot added 86 lines / removed 2 in `tools.py`, 11 / 3 in `engine.py`, 32 / 11 in `worker.py`, 7 / 4 in bootstrap and 32 / 2 in `server.py`. Automation and business implementations were new files (227, 76 and 174 lines in their first reviewed revisions). Later root fixes extended these counts; these are snapshot counts, not a final diff statistic.

Important preserved invariants:

- The model can select only fixed registered tool names. Discovery selects schemas and grants no authority.
- Existing approvals cannot be bypassed by changing a tool's policy from approval to automatic. Registry execution rechecks the current capability fingerprint while holding the registry lock.
- New integration registration is exposed through the authenticated same-origin operator API; the model receives no registration tool.
- Loopback HTTP routes reject credentials in URLs, encoded path components, dot segments, the Ariadne API port and Ollama's port. HTTP connectors disable redirects and bound body size/time.
- Existing workers retain a process limit of one. Only enumerated new workers may request up to eight. Windows limits aggregate native descendants into a Job. POSIX address-space/CPU limits remain per process and are described accordingly.
- Business writes use parameterized SQLite, `BEGIN IMMEDIATE`, idempotency keys, integer cents/units, balanced journals, bounds against negative stock and a final cancellation check before commit. Business revision changes invalidate a stale approval.

## Findings and resolution

### High: browser redirects could leave the registered origin

The first browser worker allowed approved requests with `route.continue_()`. The [primary Playwright Page.route documentation](https://playwright.dev/python/docs/api/class-page#page-route) explicitly states that routing intercepts only the first URL when the response redirects. Therefore a malicious or compromised registered local application could redirect a navigation to another local service or an external origin, contradicting the registered-app scope.

The root replaced this with an offline browser context and a bounded Python loopback broker that validates each candidate URL, disables redirects and fulfills only the returned bounded response. A 3xx response is converted to a 409 response. I inspected this correction in `_automation_worker.py`; the vulnerable `continue_()` path is removed.

An initial attempt at a real browser redirect probe hit the worker's 60 second wall deadline before either local HTTP fixture was contacted. This is not claimed as a successful exploit reproduction or a passing regression fixture. Real browser qualification must verify normal interaction, external-resource blocking and redirect refusal through the final worker path.

### Medium: path-prefix matching admitted sibling routes

The first implementation checked `candidate.startswith(prefix)`, so a registered `/app` could admit `/application` or `/app-admin`. This was reachable both in model-supplied initial paths and page requests. It weakened the owner-selected path scope.

The root added `within_app`, matching an identical origin and either the exact registered path or a subsequent complete path segment. Ordinary query strings remain supported. I inspected that both parent and worker use this helper.

### Medium: POSIX children could survive a normally exiting worker

The first multi-process cleanup killed the process group only if the leader was still running. A native wrapper could exit normally while leaving a descendant alive, after which the group was never terminated. This violated the intended on-demand lifetime and could consume resources after a completed call. Windows' kill-on-close Job handling was already present.

The root now invokes process-group termination in the POSIX `finally` path even after the leader exits. I inspected the unconditional POSIX branch. This change needs a POSIX-specific regression fixture; this Windows host cannot independently validate POSIX process behavior.

### Medium: a browser failure can be uncertain even without explicit actions

The parent currently derives `uncertain` from `bool(actions)` on `WorkerError`, and the child sets its mutation flag only immediately before explicit click/fill/select operations. Loading a reviewed app can itself run JavaScript and submit an HTTP mutation before those actions. A browser timeout after that submission can therefore be reported as a certain failed read when `actions=[]`, allowing the agent to attempt it again without the existing uncertain-outcome stop.

The root reports that all failed browser invocations now conservatively receive uncertain-outcome handling, including empty action lists. This does not require treating an ordinary local read as an unapproved action; every browser invocation already requires approval. It prevents an automatic retry from repeating an outcome that may have completed. The final native regression remains a release check.

## Test evidence and gaps

- The production tools' 16-test suite passed in 70.975 seconds, including actual OR-Tools, SymPy, all three OOXML formats, LibreOffice PDF, FreeCAD STEP, FFmpeg WAV/thumbnail/MP4, path/cancellation limits and immutable approval input behavior. These qualify production integration paths, not browser network isolation.
- The subsequent actual Blender `bpy` CPU render test passed in 12.489 seconds and asserted a 128×128 PNG with visible pixel variation.
- Existing expansion tests cover business revision invalidation, replay idempotency, negative-stock rollback, exact journals, policy, invalid automation configuration, reserved endpoints, schema discovery and prevention of relaxing legacy workers' process limits.
- Missing targeted coverage at the reviewed snapshot: browser redirect denial through the final native worker, sibling-prefix rejection through both call paths, JavaScript-triggered mutation with an empty explicit action list, and POSIX descendant cleanup after a normal leader exit. Root is implementing corresponding regression checks.

## Blast radius

Static text/call-site counts across Ariadne Python modules at review time:

| Interface | Occurrences | Relevant reach |
| --- | ---: | --- |
| `run_worker(` | 8 | Analytics, OCR, MCP, production, knowledge, media and automation providers plus definition |
| `fingerprint_material(` | 9 | Approval binding across six provider families and registry consumers |
| `schemas_for(` | 2 | Registry definition and model-loop call |
| `approval_description(` | 2 | Registry definition and approval gate |
| `local_url(` | 10 | Automation parent and browser worker checks |
| `browser_task` | 5 | Registry routing, capability catalog, automation schema/dispatch |

These are lexical counts, not a claim of dynamic branch coverage. The common worker and approval changes have the broadest transitive reach; legacy OCR/MCP/analytics regression tests remain necessary before release.
