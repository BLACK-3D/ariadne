# Independent review of the 0.3 MCP boundary

Reviewed 2026-10-28. This reviewer did not implement the MCP adapter or its root application integration. The reviewer implemented OCR separately; this report does **not** independently approve that OCR implementation. Its builder evidence is in `OCR_0_3.md`, and the root delivery agent provides the independent OCR assessment.

## Result

No unresolved high- or medium-severity finding was identified in the reviewed MCP surfaces after the numeric credential-redaction fix and configuration-lock changes. The scoped recommendation is to accept the tested loopback-only MCP slice subject to the limits below. This is neither a security certification nor authority to deploy or connect an external account/service.

Read `mcp_tools.py`, `_mcp_worker.py`, `mcp_admin.py`, and the MCP paths in `tools.py`, `engine.py`, and `server.py`. Inspected `locking.py`, `worker.py`, `_worker_bootstrap.py`, the associated tests, and relevant installed client code as dependencies. There is no Git baseline, so no commit-diff, historical blame, or general dependency audit is claimed.

## Authority and input boundaries inspected

- Registration, discovery, and tool selection are owner API/CLI operations. They are not model tools. Registration alone grants no tool and makes no discovery request. Discovery is a bounded observation, and the owner's selection must reference its exact stored snapshot digest.
- Owner policy supplies `read_only`; remote `readOnlyHint` annotations grant no authority. Tool names bind the selected metadata, policy, fixed endpoint, and identity snapshot. Changed registration invalidates the old name. Persisted approval additionally binds arguments and a keyed digest of resolved dedicated credentials.
- Before dispatch, the tool registry holds the cross-process configuration guard while rechecking an approved fingerprint and performing the call. API and CLI configuration mutations use the same guard. A separate registration snapshot is rechecked against fresh server identity/tool metadata before `tools/call`.
- Only explicit literal `127.0.0.1` or `[::1]` HTTP URLs with a port and fixed path are supported. No hostname resolution, remote HTTPS endpoint, query string, URL credential, stdio command, or subprocess-server launch is exposed. The HTTP transport refuses changed destinations, redirects, proxies, compressed responses, and over-budget wire bodies.
- Dedicated `ARIADNE_MCP_...` environment references supply only supported credential headers. No inherited owner environment reaches the worker. Server text/errors cannot replace raw local diagnostics or leak exception details through its generic failure boundary.
- Schemas use a bounded explicit subset. References, regexes, open-ended objects, and unsupported types are refused for registration. Output schemas remain plain data. Raw `call_tool_mcp` results avoid schema-to-Python conversion. Text/JSON results are bounded and remain untrusted reference data.
- Sampling, elicitation, and input-required continuation rounds are refused; background extensions are disabled. Network transport retries are disabled. A potentially submitted write with an unconfirmed/error outcome is recorded uncertain and fails the mission. The engine does not ask the model to retry it, and the associated schedule stops.

## Findings and resolutions

**Numeric credential echo bypass, resolved.** A synthetic valid credential `24681357` could be returned as a JSON number under an ordinary key. `redact({"value": 24681357}, ["24681357"])` originally left that known credential in output because only string values and sensitive field names were scrubbed. The implementation now redacts integer/float scalars whose string or canonical representation exactly matches a known credential. The new live fixture covers both text and structured JSON output and passed independently. This addresses the reproduced ordinary JSON type-conversion case; redaction is not universal detection of every secret encoding or transformation.

**Configuration recheck/call gap, resolved before final assessment.** The first in-progress snapshot reread configuration between fingerprint comparison and dispatch without a shared mutation guard; `MCPAdmin` also referenced a not-yet-present guard method. The final implementation has `MCPTools.mutation()`, uses it across cooperating owner API/CLI changes, and holds it around the registry's approved fingerprint recheck plus actual dispatch. An independent mocked-dispatch fixture attempted owner removal at that exact point and was refused while the approved action proceeded once. External editing by an arbitrary process that ignores the guard remains outside this cooperative transaction boundary.

**Windows directory pinning, resolved.** The MCP builder's adversarial experiment found that opening a directory with metadata-only access (`0`) and omitting delete sharing did not actually prevent Windows renames. The final shared `_directory` uses `FILE_LIST_DIRECTORY` (`1`) with read/write sharing and no delete sharing. This reviewer inspected that exact fix and independently passed the real-child fixture: replacement of the child's temporary directory is denied while the fixed worker is alive. The shared supervisor holds that guard from before `Popen` through child exit. The lock implementation also validates the opened handle's final path before its first write; a simulated path/handle mismatch leaves the alternative empty file untouched. Hard-link and opened-handle tests passed; symbolic-link creation remained unavailable in this reviewer's earlier full run.

**Implicit SDK extensions, resolved.** An explicit `extensions=[]` argument alone does not disable FastMCP's internal extension factory. The final fixed worker constructs a `BoundClient` subclass with `_auto_internal_extensions=False` as well as that empty explicit extension list. A targeted test patches the internal factory to raise and confirms it is never called. This keeps future optional task packages from silently adding background result resolution. The current bounded client-only lock did not require those packages; this was a future-environment hardening gap, not an observed remote side effect.

**Combined catalog bound, resolved.** Each schema can be within its individual bound while their exposed combined catalog exceeds the model-context budget. `validate_catalog` now bounds the complete exposed MCP schema list to 64,000 bytes, and both owner registration/selection commit paths invoke it before writing. An independent owner-selection fixture presented 16 individually valid schemas whose combined size exceeded that limit; selection failed, and the previous configuration remained byte-for-byte unchanged.

**Pre-submission failure classification, resolved.** `execute()` records whether it reached worker submission and distinguishes `MCPFailure.uncertain` from errors before that point. A missing pinned runtime, invalid arguments, or prestart cancellation therefore fails without falsely telling the owner that a write may have occurred. Failures after possible submission remain conservatively uncertain for writes.

## Independently executed evidence

| Test command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_mcp_tools.py -v` | 19 tests: 18 passed, one symbolic-link creation skip; 47.909 seconds |
| `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_integration_0_3.py -v` | Seven passed; 15.217 seconds |
| Final delta: `test_mcp_tools.py` with `-k internal_sdk -k instance_lock_checks -k worker_temp -k combined_schema` | Three passed; the real-child test exceeded its 10-second worker deadline under the restricted runner; four tests, 12.005 seconds |
| Exact real-child delta rerun: `test_mcp_tools.py -k worker_temp` | One passed outside the restricted runner; 1.837 seconds |
| Final protocol/classification delta: `test_mcp_tools.py -k missing_runtime -k successful_sse` | Two passed, including successful SSE discovery/call in both protocol modes; 9.725 seconds |

These were test-owned synthetic loopback servers with fabricated credentials and disposable Yikes state. No production listener, actual owner credential, real model, business account, or outside service was used. Initial unprivileged attempts errored during local fixture discovery and were interrupted after repeated failures. The same bounded fixtures passed after allowing the local test networking; those aborted attempts are not reported as passing runs.

The successful cases include modern `2026-07-28` and legacy `2025-11-25` JSON and complete SSE exchanges, one approved call, changed metadata/identity denial before invocation, rotated credentials, credential echo redaction, malformed/unsupported schemas, pagination/tool limits, redirects, oversized output, slow SSE deadline handling, cancellation, missing-runtime classification, and uncertain-write behavior. Integration verifies that an uncertain write calls the fixture once, makes no further model request, retains uncertain approval status, fails the run, and disables its schedule.

Additional independently constructed inline fixtures passed:

1. A competing `MCPAdmin.remove()` is rejected during the approved fingerprint-recheck/dispatch guard, with one mocked dispatch and unchanged registration.
2. Changing the dedicated credential after approval prevents dispatch entirely.
3. New MCP API routes reject absent bearer authentication and cross-origin mutation requests.
4. An authenticated server registration performs no network discovery and leaves the model tool list empty.
5. Oversized combined schemas are refused through `MCPAdmin.select()` before committing, with the prior configuration and one-tool catalog retained.

Those additional fixtures made zero network or model calls. They supplement, rather than replace, the real synthetic HTTP cases.

The MCP builder separately reported its final combined MCP/tools/red-team run: 59 tests completed with four existing symbolic-link privilege skips. That is attributed builder evidence, not an additional independently executed suite. The restricted-runner deadline failure above remains in this record; the successful exact rerun does not establish why the first child was slow.

## Limits

- A loopback address and matching server metadata are not cryptographic process attestation. The operator must trust and secure the local service occupying that endpoint. Identity snapshots can detect changes but cannot prove that a service reporting identical metadata is the original program.
- An owner's `read_only` declaration is an authority decision; the adapter cannot enforce the remote service's semantics. Register only reviewed operations. Discovery and model content cannot make this decision for the owner.
- Successful JSON/SSE exchanges in both protocol modes and SSE rejection/deadline behavior were tested. Every server-initiated request variant, SSE reconnection/session-resumption case, and MCP extension was not independently exercised. Unsupported features intentionally fail rather than silently acquiring authority.
- Local file validators reject existing links/hard links. The mutation lock coordinates cooperating application/CLI writers. This pass does not establish immunity to every malicious concurrent local filesystem or process-memory modification by another host program.
- The worker's OS resource limits and restricted environment bound cost and exposure. They are not a full native-code filesystem/network sandbox. Native libraries and the pinned client dependency stack remain deployment trust boundaries.
- An uncertain action requires owner reconciliation. There is no distributed exactly-once guarantee. A stopped process can fail to observe an already-completed remote effect, and recovery deliberately does not replay it.
- This is a focused review of the MCP addition. Full-suite delivery results, final local-model qualification, UI QA, offline cold boot, backup restoration, and production promotion are separate gates managed by the root delivery agent.

## Reviewed snapshot

SHA-256 values recorded after the inspected fixes and test runs. Subsequent code changes require an appropriate review delta.

| File | SHA-256 |
| --- | --- |
| `mcp_tools.py` | `a686238e886acca67b4639121b892ed6cc2f84b0a2dc49cca9be60ae7595f66f` |
| `_mcp_worker.py` | `0f8854539bf109659d08312b96029d7d4ae338cc289503bc5421fc02cd0bedd5` |
| `mcp_admin.py` | `d0beb51dd7d6a95d71b0ba9385d9e7e845927ceee63a478982ac70f9c86c0afe` |
| `tools.py` | `f5c0d3a605dd5a3ca0602bb6886a0dd52618550cc7ea25c0681e5d8121f7e5d3` |
| `engine.py` | `5aceaba0732db384cc3f79b3215616c8aca043bff9ccae8cbcc0807794757040` |
| `server.py` | `35dc63b06b897c87c55260e2ffeed9eb3683834f9b487e75ff13884d6b62240f` |
| `locking.py` | `c13c72e6a663712cbb0d6c041f48aec16145a0e72949cc7cf7bb538a00d30b07` |
| `worker.py` | `c3c9866dfc5fcd3b3a12e3083d8fe67138c8eb2692d41a1d001ca311227e2274` |
| `_worker_bootstrap.py` | `44690f4658fe30b480f1ce8136a818959480fa5ec6e89820a2c67d91c1d302fa` |
