# Ariadne 0.2 independent adversarial review

Reviewed 2026-10-28. Scope: root-authored `sources.py`, `verification.py`, `store.py`, `engine.py`, `capabilities.py`, and `server.py`, plus the new approval-fingerprint call boundary. The reviewer did **not** independently review its own `data_tools.py`, `documents.py`, or original filesystem/runner implementation.

After this scoped review, the same agent implemented `sum_product` in `data_tools.py` and its builder tests to address a live weighted-inventory calculation failure. That implementation requires another reviewer's assessment; this report does not independently approve it. The root's subsequent numeric acceptance predicate received a separate independent review recorded in the numeric addendum below. The preserved failed live qualification remains separate evidence and was not changed by this agent.

## Final review result

**Recommendation: approve within the reviewed scope and stated limits.** The implementer resolved all three reproduced source findings and the subsequently identified numeric API rounding boundary; their regression fixtures pass. The additional runner-input approval-binding candidate was also fixed and its mutation-denial regression independently rerun. No unresolved high/medium finding remains in the reviewed paths. No unauthenticated source import or model-controlled source registration was found.

| Priority | Open | Resolved during this review |
| --- | ---: | ---: |
| P1 / high | 0 | 0 |
| P2 / medium | 0 | 4 |
| P3 / low | 0 | 0 |

This is a scoped source review, not a security certification. The workspace has no Git repository, so commit comparison, blame, historical regression attribution, and changed-line statistics are unavailable. Prior 0.1 architecture/review notes were used only as context. The differential-review skill's risk, adversarial, coverage, and report workflow was used where applicable; no historical differential coverage is claimed.

## Threat model and boundaries

The relevant adversary is a malicious file or instruction within an owner-selected reference folder, or model output attempting to exceed a mission's supplied capabilities. The bearer-token holder remains the installation owner. The review checked that imported bytes cannot install code, modify owner checks, register new folders, or grant external authority. Owner-approved writes still require capability/input binding.

All test fixtures and state were created under `Yikes`; no personal document, production token, `.env`, E: path contents, or live business/Home Assistant connection was read. E: rejection tests supplied only a forbidden path string, which was rejected before filesystem access.

## Reproduced findings, now resolved

### P2: Common credential aliases bypass the reference filename exclusion

**Location at first pass:** `sources.py:19-26`, `sources.py:42-49`, `sources.py:67-90`.

`permitted_name()` rejects hidden names and a credential-name regex, but permits `id_rsa.txt`, `id_ed25519.txt`, `token.json`, and `auth.json`. The source picker lists all four synthetic fixtures, and the same path guard permits preview/import. These are common private-key or authentication-file aliases covered by the product's advertised credential-like-name exclusion.

**Reachability/impact:** requires an authenticated owner to select a reference directory and preview/import a misleading file. This is a local privacy-filter failure, not unauthenticated exfiltration or autonomous outside-folder browsing. Imported credential material could otherwise become persistent searchable knowledge.

**Evidence:** `test_common_private_key_and_auth_names_are_excluded` fails because all four names appear in the listing. Fixtures contain only synthetic text.

**Recommendation:** add explicit credential/auth/private-key aliases to the common shared filename policy; apply it to listings, preview/import, and uploads. The policy must remain a filename heuristic, not a claim that all secret content can be detected.

**Fix inspected and verified:** `sources.py:20-24` adds shared common-credential aliases; the original failing credential-name fixture now passes.

### P2: Selecting an excluded directory as the source root bypasses its exclusion

**Location at first pass:** `sources.py:17-18`, `sources.py:30-40`, `sources.py:42-49`, `sources.py:99-107`.

Nested `runtime`, `venv`, `data`, and `node_modules` directories are excluded by `source_path()`. `register()` does not apply that same directory policy to the selected root, so selecting any of those directories directly registers it and permits supported files beneath it. The empty relative root has no components for `source_path()` to reject.

**Evidence:** all four subcases in `test_selected_excluded_root_cannot_bypass_directory_policy` fail because registration succeeds. Ordinary nested exclusions pass in the positive boundary test.

**Recommendation:** validate the selected root and its meaningful ancestors under the same folder policy. Revalidate persisted source roots when creating a reader, so existing registrations cannot bypass a later policy fix.

**Fix inspected and verified:** `ReadOnlyFiles.__init__` now rejects private/excluded root components before opening a reader, and registration applies the same policy. Both the original selected-root fixture and a legacy persisted-source fixture pass.

### P2: Windows case aliases create duplicate snapshots and independent version histories

**Location at first pass:** `sources.py:126-136`, `store.py:269-291`.

Source identity stores the caller's relative path with slash normalization only. On Windows, `Guide.md` and `guide.md` read the same file, but SQLite's source-path equality treats them as distinct keys. Importing both creates two documents, duplicate retrieval entries, and separate version histories. A refreshed alias can leave stale evidence indexed under the other alias.

**Evidence:** `test_windows_case_alias_does_not_duplicate_source_snapshot` imports a synthetic file with two casing variants and receives different document IDs.

**Recommendation:** derive a canonical filesystem-relative identity after safe resolution; preserve a separate display path if desired. Respect platform filesystem semantics rather than applying universal case folding to case-sensitive platforms. Cover refresh through an alternate casing as well as initial deduplication.

**Fix inspected and verified:** `sources.py:71` derives a safe platform-normalized relative identity, which import and upload metadata use. Windows imports and a subsequent changed-byte refresh through three casing variants retain one document ID and advance its version.

## Additional approval-binding candidate, resolved

The new `run_python` fingerprint binds tool schema, arguments, image name, and enabled state, but not the script or other snapshot bytes. A queued mission can change `script.py` while another mission waits for approval; the path arguments remain unchanged while the executed code differs from the reviewed content. This was reported separately to the root implementer. No Docker container was launched for this review, and no independent approval of the reviewer's original runner code is implied.

Prefer an immutable, safely captured runner snapshot and manifest hash bound to the approval. Hashing only the entry script is insufficient if it imports another workspace file. The existing read-only/networkless runner limits reduce impact but do not establish byte identity.

**Fix inspected and verified:** the root-authored `_snapshot_digest`/`fingerprint` additions at `tools.py:323-344` bind the full input tree. `Engine.execute_call` passes the approved fingerprint, and the runner compares it against the actual captured execution snapshot before invoking Docker (`tools.py:573-574`). The reviewer independently reran the implementer's non-entry-file mutation regression; it passed without launching Docker. Approval descriptions also expose the script preview and nonsecret destination/method/environment-reference names for owner review.

## Tested invariants and coverage

Command: `.venv\Scripts\python.exe -m unittest discover -s tests -p test_redteam_0_2.py -v`.

First pass: **10 test methods, seven passing and three failing methods**; the excluded-root method has four failing subcases, producing six failure assertions overall. No test uses a real model, production state, or external service.

| Invariant | Evidence |
| --- | --- |
| Hidden names, obvious credential names, nested excluded folders, traversal, E:, and private app state cannot be imported | Positive source-boundary fixture passes |
| Preview digest cannot authorize changed bytes | Changed-byte import raises `SourceChanged`; fresh preview is required |
| Refresh updates one document, increments version, removes stale FTS content, and carries hash/path provenance | Versioned refresh fixture passes |
| Existing approval cannot be downgraded to auto after capability change | Fresh pending approval required; no tool execution |
| Rejection remains effective after policy/fingerprint changes | No execution; rejection result retained |
| Cancellation before the execution boundary prevents invocation | No tool execution |
| Model cannot register external sources or replace owner checks | Unknown source tool cannot register; checks remain persisted and fail honestly |
| A failed scheduled run stops recurrence until explicit re-enable | No subsequent run until owner re-enables |
| Source registration and checked mission creation require owner authentication | Both unauthenticated endpoint probes return 401 and create no source/run |

### Quantified direct call boundaries

At the first pass, `SourceManager.register`, `listing`, `preview`, and `import_reference` each have one API caller. `permitted_name` is shared by path validation, directory listing, and upload validation. `Store.document_upsert` has three production call sites: manual documents, source imports, and uploads. `validate_checks` has four production call sites: mission creation, workflow create/update, and engine completion. `verify` and `Engine.execute_call` each have one engine caller; `due_schedules` has one worker caller. The relatively small caller count allows direct boundary testing; no code-coverage percentage is claimed.

## Final verification addendum

The first final independent adversarial suite ran **13 tests, all passing, with no skips**. This included actual Windows hidden-file attributes and a temporary directory junction pointing at a second synthetic folder within the same disposable Yikes fixture. The normal sandbox denied junction creation; automatic tool escalation allowed the same fixture-only test run to create it. Source listing and preview rejected that junction. The subsequent numeric review expanded this suite to 17 passing tests, as recorded below.

The reviewer also independently ran the implementer's `test_workflows_sources.py`: **8 tests passed**. That suite covers legacy database migration, immutable workflow contracts, bounded repair and hashed file checks, schedule stop/re-enable, source provenance/warnings, ambiguous-JSON rejection, and full runner-snapshot approval binding. This is additional behavioral evidence; it is not an independent review of the reviewer's own table/document implementations.

The reviewer changed only this report and `tests/test_redteam_0_2.py`; application fixes were made by the root implementer. No real Docker container, external business action, microphone recording, credential file, or personal document was used by this review.

## Independent numeric owner-predicate addendum

The follow-up review covered root-authored `verification.py` (`number`, `pointer_parts`, `select_number`, `validate_checks`, and `verify`), the immutable-check and bounded-repair engine path, and the numeric contract in `scripts/verify_0_2.py`. It did not assess the reviewer's `sum_product` implementation as independent work and did not rerun a live model qualification.

### P2: Raw expected JSON fractions could lose precision before validation — resolved

The HTTP body decoder initially used ordinary `json.loads` before the new numeric predicate validator. A numeric expected value such as `0.10000000000000000001` therefore became the Python float `0.1`; normalizing that float would silently weaken the owner's exact expected value. This boundary was identified by inspection, and the lossy JSON decode was directly reproduced in the independent fixture. The pre-fix live endpoint was not exercised after the implementer had already patched it.

**Fix inspected:** `verification.py:69-74` now permits only decimal strings or Python integers for owner-authored expected values, rejecting floats, booleans, and arbitrary objects before numeric normalization. Actual artifact JSON numbers still parse directly into bounded exact Decimals. Quoted fractional expectations therefore retain their full precision; an owner can still submit an exact large integer through the API.

**Independent endpoint regression:** the authenticated synthetic WSGI fixture confirms fractional numeric expectations, `16.0`, booleans, NaN/Infinity, and overflowing numeric literals receive HTTP 400. A quoted `"0.10000000000000000001"` receives HTTP 201 and persists unchanged. Artifact `0.1` fails that expectation; artifact `0.10000000000000000001` passes. No production access token was read or used.

### Adversarial numeric coverage

- Exact finite comparisons: 200-digit near-matches remain distinct, and values at exponent limits `1e400` and `1e-400` are checked without float rounding.
- Rejection: boolean/null/container values, NaN/Infinity, duplicate JSON keys including escaped-key aliases, oversized digits/exponents, numeric-expression strings, whitespace/leading-plus/leading-zero forms, and malformed owner expected-value types.
- RFC 6901: empty root pointer, empty object key, `/` and `~` escaping, the `~01` double-escape edge, existing canonical array indices, missing keys, negative/leading-zero/noninteger/oversized indices, invalid escapes, excessive segments, and oversized pointers.
- Immutability/repair: a synthetic model attempting `override_owner_checks` cannot alter the persisted `16.00` expectation. A wrong `6.75` artifact remains wrong and is reported failed after exactly one repair request and two verification attempts. Model completion claims never change that result.

Final independent runs: **17/17 adversarial tests passed with no skips**, including the real Windows hidden-file/junction fixtures; **10/10 root-authored workflow/source integration tests passed**. Only synthetic disposable Yikes fixtures were used. The earlier failed live weighted-inventory qualification was not overwritten, relaxed, or relabeled by this review.

## Limits and follow-up

Concurrent filesystem replacement races, live external side effects, physical power loss, and a fully disconnected restore were not exercised. Static Windows hidden-file/junction rejection was tested successfully; this does not establish resistance to every concurrent local replacement attack. Cancellation probes establish no invocation when cancellation precedes the final running-state check; they do not claim to roll back an already-started external action. Imported snapshots remaining after source removal are separate retained knowledge, whose deletion/retention behavior should be clear in the UI. Filename exclusions remain heuristics and cannot guarantee detection of secrets stored under ordinary names.
