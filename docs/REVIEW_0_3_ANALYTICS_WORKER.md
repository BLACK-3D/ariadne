# 0.3 analytics and shared worker review

Reviewed 2026-10-28 on the Windows Yikes installation. Scope: root-controlled `analytics.py`, `_analytics_worker.py`, `worker.py`, `_worker_bootstrap.py`, `locking.py`, and `mcp_admin.py`; related file boundaries and tests were inspected where required. This is a focused review, not a security audit or certification.

**Ownership boundary:** this reviewer built the MCP adapter/worker/CLI and small table tools. MCP test results below are builder qualification, not an independent approval of those components. The separate research reviewer assesses MCP. Analytics and the shared worker/lock changes were implemented by the coordinating root agent. The reviewer found and, after explicit ownership transfer, changed one shared Windows directory-handle access line in `tools.py`; independent delivery review must include that delta.

## Result

No unresolved high/medium finding was confirmed in the reviewed analytics/worker slice after the fixes described below. Acceptance remains bounded to the tested Windows scope, fixed operations, and stated resource limits. No external account, device, business system, or production MCP server was connected; test files remained inside Yikes on C:. E: was not accessed.

## Analytics boundary and independent oracle

The analytics API accepts fixed structured operations, not SQL. The parent reads confined regular workspace files into immutable byte snapshots, preserving hashes. Only those bytes and validated operations reach the native worker. Python parses UTF-8 CSV/TSV/flat JSON; native database/file readers receive no caller path. Generated `tN`/`cN`/`rN` identifiers separate SQL syntax from arbitrary headers. Inserts/filter values are bound parameters. The worker disables and locks external access, extension loading/installing, persistent secrets, replacement scans, and spill settings. Reviewed joins require a unique right key and reject unsupported scale combinations and missing numeric lookup factors.

Numeric parsing preserves JSON number spellings and explicitly opts columns into finite fixed-point values. No binary float is used as the fallback for unsupported numeric data. Preflight reserves capacity for worst-case absolute sum/product intermediates within DECIMAL38. Exact sums precede the separately reported 38-significant-digit, half-even average division. Conservative overflow/scale rejection can reject mathematically cancelling results; the implementation documents this tradeoff.

An independently constructed 42-row fixture passed through the **actual public analytics adapter and bounded worker**. It combined `+1e12 × 1e12` and `-1e12 × 1e12` with forty small signed six-decimal factors. The returned weighted sum was **5.1786E-8**, matching an independent Python Decimal oracle at precision 512. The returned average also matched the oracle rounded separately at precision 38. The fixture used synthetic JSON bytes inside a disposable Yikes directory. This supplements the builder's larger fixtures; it is not an end-to-end model-driven business result or a performance benchmark.

No additional concrete SQL injection, native file-access, or decimal correctness issue was found in the reviewed fixed builder. A native-library vulnerability remains outside the protection supplied by these input constraints.

## Resource controls and allocations

| Worker | Process ceiling | Additional native budget | CPU / parent wall limit | Output |
| --- | --- | --- | --- | --- |
| MCP | 384 MiB | HTTP aggregate body budget 512 KiB | 10 CPU seconds / 20 seconds | 256 KiB worker envelope, 48,000-byte tool output |
| Analytics | 768 MiB | DuckDB 128 MiB, spill disabled | 20 CPU seconds / 30 seconds | 60,000 bytes |

The bootstrap applies OS limits before optional SDK/native imports. Windows uses a Job Object with memory, CPU and active-process constraints; POSIX uses address-space/CPU rlimits. The parent bounds input, captured output, elapsed time, and cancellation, and reaps children. It strips inherited owner credentials/proxies/Python hooks and redirects temporary/home/cache locations inside Yikes. Only fixed internal worker scripts are accepted.

The shared two-worker admission slots are not an aggregate RAM guarantee: different worker ceilings can add up, and the application/model/runtime also consume memory. No independent power/RSS comparison or new memory-allocation stress probe was performed in this review. The native allocation evidence described in `ANALYTICS_0_3.md` belongs to its builder. These resource controls are not a general OS filesystem/network sandbox and do not prove containment of arbitrary native code.

## Concrete findings and fixes

**Windows directory replacement despite metadata handle, resolved.** A reduced fixture proved that `with ToolRegistry._directory(p): p.rename(q)` succeeded with the old `CreateFileW` desired access of zero. Merely holding that metadata handle while omitting `FILE_SHARE_DELETE` did not establish the assumed path pin on this host. A direct access-flag matrix showed:

| Desired access | Rename while handle remained open |
| --- | --- |
| `0` | Allowed |
| `FILE_READ_ATTRIBUTES` (`0x80`) | Allowed |
| `SYNCHRONIZE` (`0x100000`) | Allowed |
| `FILE_LIST_DIRECTORY` (`1`) | Denied, WinError 32 |
| `GENERIC_READ` (`0x80000000`) | Denied, WinError 32 |

After ownership transfer, the shared helper changed the desired access to `FILE_LIST_DIRECTORY`, retaining share-read/share-write and omitting share-delete. The real-child temporary-directory replacement regression then passed. Existing workspace/source junction and hard-link cases also passed. This fixes a shared path-boundary assumption, not merely an MCP-specific detail.

**Lock first-write held-file mismatch, resolved.** Path `lstat()` alone could describe a restored ordinary path while the already-open descriptor referred elsewhere. A synthetic C:-only descriptor-swap fixture reproduced `InstanceLock.acquire()` writing its initial `0` byte into another empty fixture. Root added held-handle path equality before any write on Windows and device/inode equality on POSIX. The regression now rejects the mismatched descriptor and leaves the other fixture empty. A separate hard-link regression confirms an empty linked owner file is rejected before its first write and remains unchanged.

**Worker temporary-path lifetime, resolved for tested Windows behavior.** Initial environment validation preceded process startup without holding the temporary-directory path for the child's lifetime. Root now holds the shared directory guard while the child runs. Combined with the corrected Windows desired access, a real-child regression denies the rename required to replace that temporary directory with a junction. The test uses a disposable C: path and attempts the replacement prerequisite; it does not target production temporary files or E:. Equivalent native temporary-path replacement behavior on POSIX was not qualified here; holding a directory descriptor alone does not imply Windows-style rename denial there.

**Concurrent owner config edits, resolved for cooperating writers.** HTTP routes already had a server lock, but separate CLI processes could race their read/modify/write operations. The shared `MCPTools.mutation()` OS lock now spans cooperating API/CLI changes and discovery persistence; the root registry also holds it across approved fingerprint recheck and dispatch. A competing descriptor is rejected. Arbitrary host programs that ignore this cooperative lock are not made trustworthy by it. This reviewer supplied the adapter guard; the root agent implemented/administered its application integration, which has a separate MCP review.

## Executed evidence

- Final combined command: `.venv/Scripts/python.exe -m unittest tests.test_mcp_tools tests.test_tools tests.test_redteam_0_2 -v` — **59 tests, 55 passed, 4 symbolic-link privilege skips; 57.288 seconds**. Junction creation, hard-link checks, held-file identity, temporary-directory replacement denial, modern/legacy loopback, credential redaction and existing source/approval/schedule boundaries passed. The skipped symbolic-link cases are not represented as passes.
- Separate new successful-SSE fixture: both modern `2026-07-28` and legacy `2025-11-25` Streamable HTTP discovery/call responses passed — **one unittest, two protocol cases; 8.697 seconds**. This is builder evidence for MCP; the independent MCP report must record its own delta qualification.
- Later targeted known-pre-submission uncertainty/cancellation checks — **two passed; 2.484 seconds**. Missing runtime or cancellation before launch does not claim an uncertain external write; potentially submitted failures retain conservative uncertainty.
- Independent 42-row analytics weighted-cancellation/average oracle — passed through the real bounded worker, as described above.

The initial unprivileged loopback attempt was stopped after networking failures; it is not counted as a successful qualification. The first full MCP run exposed the Windows temporary-directory replacement failure and is retained as a failed run, not relabeled as passing. Successful final networking fixtures required explicit local-test execution permission. No raw production token, environment credential, personal document, external business service, or microphone recording was used.

## Documentation and remaining gates

`BUILD_0_3.md` and `ANALYTICS_0_3.md` clearly distinguish module fixtures, future integration/production gates, resource limits, and a full sandbox. No misleading measured energy/cost saving or general safety claim was found in those documents. `MCP_0_3.md` records its Windows-specific optional lock/dependency bundle and unsupported features.

The latest shared-handle, lock, catalog-budget and SDK-extension changes require the coordinating agent's final complete suite and the independent MCP review delta before promotion. UI QA, live local-model argument selection, idle-server upgrade, archive verification, restore, full offline cold boot, deployment budget, and other platforms are separate delivery gates. No approval to expand authority, deploy externally, spend money, or connect services is granted by this review.
