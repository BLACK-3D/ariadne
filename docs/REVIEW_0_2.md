# Ariadne 0.2 independent verification

Reviewed 2026-10-28 by a reviewer who did not implement the application changes assessed here. This report concerns the additive standalone Yikes work package in `BUILD_0_2.md`. It grants no deployment, campaign, phase-acceptance, or other authority in the separate Jarvis/Threadline repositories.

## Result and release boundary

The reviewed core changes pass the targeted deterministic verification below. No unresolved serious issue was identified within these checks after the reported fixes were inspected and exercised. This is a scoped verification result, not a security certification or a claim that arbitrary model-generated business calculations are correct.

A live qualification initially produced the wrong weighted inventory total, **6.75 instead of 16.00**, while its structural file checks passed. The implementation agent preserved that failed evidence under `data/verification-0.2/3cf791edc6bb46f1b7f8a4967fba4d78`. This reviewer did not read production state or alter that evidence. The new deterministic `sum_product` operation and exact `json_number` predicate have now passed independent checks. The delivery agent subsequently reported a successful replacement live qualification, separately attributed below. This report does not turn the original failed run into a pass or itself authorize deployment.

## Scope and method

Read the current implementations of `sources.py`, `store.py`, `engine.py`, `tools.py`, `server.py`, `verification.py`, `data_tools.py`, `documents.py`, and `capabilities.py`, with configuration and existing filesystem confinement as dependencies. Focused on source privacy, exact-byte import binding, persistence/migration, output verification, bounded repair, approval invalidation, and recurring-mission failure stops. The separate adversarial assessment is recorded in `RED_TEAM_0_2.md`; this review additionally assesses the subsequently added weighted aggregate and numeric predicate.

There is no Git baseline in this workspace. No historical diff or blame coverage is claimed. Tests used synthetic fixtures in disposable directories inside Yikes. No E: contents, private authentication files, outside project source execution, live business connector, real Docker process, microphone capture, or existing running server was used. The forbidden-drive tests pass only an E: path string to the validator.

## Independently executed tests

The final targeted module runs were:

| Command | Result |
| --- | --- |
| `python -B -m unittest discover -s tests -p test_workflows_sources.py -v` | 10 passed |
| `python -B -m unittest discover -s tests -p test_redteam_0_2.py -v` | 16 passed, one skipped, including the final numeric API tests |
| `python -B -m unittest discover -s tests -p test_data_tools.py -v` | 12 passed |

Total: **39 tests, 38 passed, one skipped**. The Windows directory-junction fixture was skipped because the test sandbox could not create its junction. Windows hidden-attribute and case-alias fixtures did execute and pass. These are targeted results, not a new full-suite count.

Additional independent inline fixtures exercised the real classes with fake inference or mocked external dispatch:

- A false final answer triggered one bounded repair attempt. Creating the expected file then passed; its recorded SHA-256 matched the actual file bytes. Repeated false completion without the artifact left verification failed, with owner expectations unchanged.
- A persisted approved POST action was reconstructed with a changed route and an automatic read-only GET policy. It required a fresh approval and performed no connector call. A hand-built legacy 0.1 schema preserved its rows while an old approval without a capability binding was superseded instead of executed.
- A changed reference rejected the old preview digest; a fresh preview refreshed the same document and incremented its version. Reimporting unchanged bytes deduplicated the snapshot. Parent paths, credential-like names, hidden files, and excluded directories were rejected.
- With private storage named `state`, references equal to, inside, or containing private storage were rejected. A deliberately inserted legacy parent registration was revalidated and rejected before listing or preview. No actual private file was read.
- Extraction warnings survived closing/reopening the store and were included in later knowledge retrieval. A warning attached only to the upload response would not have met this check.
- Changing approved runner script bytes changed its fingerprint. Dispatch with the earlier fingerprint rejected the changed snapshot before the mocked Docker boundary. The regression module also covers changing a separate input file.
- Failure disabled its associated recurring mission. Explicit re-enabling allowed one subsequent due mission; an existing active binding was not cleared to create overlap.
- Source, workflow, and capability routes retained bearer authentication; a cross-origin source request was rejected.

No model, network connector, or Docker invocation occurred in these additional fixtures.

## Arithmetic qualification

`sum_product` accepts exactly two existing factor columns, validates finite bounded decimal inputs, multiplies corresponding row factors, and sums the products. Its implementation uses a 512-digit local Decimal context. This is sufficient for the supported two-factor magnitude span and 2,000-row carry, including cancellation. Average retains its separate documented 40-significant-digit rounding after the higher-precision sum.

An independent rational oracle used `fractions.Fraction` over exact decimal strings, rather than copying the implementation's summation. With random seed `20261028`, 60 signed mixed-scale rows were divided among three groups, then three cancellation rows were added with products `1e200`, `1e-200`, and `-1e200`. Both forward and reversed input orders matched the oracle exactly in every group. Separate invalid-factor cases rejected blank, null, boolean, NaN, Infinity, and out-of-range values. The ordinary inventory fixture returned `3 * 2.50 + 2 * 4.25 = 16`.

The exact numeric predicate passed `16`, `"16"`, and equivalent decimal/exponent notation for an expectation of `16.00`; it rejected `6.75`, booleans, nonfinite values, duplicate JSON keys, missing locations, unsupported expressions, and out-of-bounds expectations. Escaped RFC 6901 pointer segments and array indexes were exercised. A numeric mismatch triggered the same bounded repair mechanism as other failed owner checks.

The final adversarial pass found another precision boundary: parsing an unquoted owner expectation through the API's JSON float parser could round it before validation. The final validator accepts only a quoted decimal string or an integer for the owner expectation, rejecting Python floats including `1.0`. Artifact JSON continues to use the exact Decimal parser. This reviewer independently confirmed that an expected `"0.10000000000000000001"` matches that exact artifact number and fails against the different expected `"0.1"`; the Python integer `9007199254740993` is preserved. The final red-team API regression also passed. The staged UI at `staging/ui-0.2/app.js` was inspected: it checks original unquoted numeric tokens before serialization and requires quoted strings for decimal/exponent literals or integers outside JavaScript's safe range. This was a code inspection of the staged asset, not independent browser QA or a claim that it had already replaced production assets.

The predicate deliberately limits values to 200 coefficient digits and exponent/adjusted exponent magnitude 400. That supported-check range is narrower than every possible fixed-string result across the aggregate's entire input range. An extreme legitimate aggregate can therefore be rejected by a numeric check rather than certified. The qualification target `16.00` is well inside both ranges. A supplied expected number verifies that literal expectation; it does not independently derive the correctness of an arbitrary business formula or establish business suitability.

## Findings resolved during verification

| Finding | Fix inspected and independently checked |
| --- | --- |
| Averaging could lose a small term before cancellation by summing at the lower division precision. | Sum at the higher aggregate precision before the separate 40-digit division. The `1e50 + 1 - 1e50` average regression passes. |
| A row larger than the output cap could return an empty page without advancing. | Return an explicit error recommending selected columns; a narrower selection makes progress. |
| Re-enabling a failed schedule retained the failed run pointer and immediately disabled it again. | Explicit re-enable clears a terminal failure binding, preserves active bindings, and establishes the next interval. |
| Registering an ancestor of custom-named private storage could expose private JSON through reference import. | Reject overlapping source roots at registration and every reader reconstruction, including legacy registrations. |
| Partial extraction warnings could disappear after ingestion. | Persist warnings in document metadata and return them with retrieved evidence after store reopen. |
| Approval bound runner arguments and image name but could execute changed script/input bytes. | Fingerprint the complete bounded input snapshot and compare the actual execution snapshot before Docker dispatch. |
| Persisted approval could lose its gate after a capability changed to automatic policy. | Honor an existing gate, compare its current capability binding, and require a new approval when it changes. |
| An API float could round the owner's exact numeric expectation before validation. | Require quoted decimal strings or integer expectations; reject floats while preserving exact Decimal parsing of artifact JSON. The staged browser editor also rejects unsafe original numeric tokens before serialization. |

The live weighted-total failure also established that structural checks alone were insufficient for this numerical workflow. The deterministic aggregate plus owner-authored exact numeric expectation address that specific acceptance gap. The separately reported replacement live qualification follows.

## Attributed delivery evidence

The delivery agent reports that `data/verification-0.2/4ac7ebea2f7347b59ef3348205fd2e78/result.json` records a replacement local `qwen3:8b` mission with execution status completed, all five output checks passed, and its separate semantic fixture true. Reported execution was six steps and 14.298 seconds. This reviewer did not rerun the model or independently measure that duration; one successful fixture does not establish general task reliability, speed, energy use, or savings.

After the final numeric input fix, the delivery agent also reports a full suite of **86 tests: 83 passed and three older Windows symbolic-link privilege skips**. It reports successful Windows junction, hard-link, and hidden-file cases when run with the required permission. Those are attributed delivery results; the independent sandboxed junction attempt above remains recorded as skipped. Browser QA and promotion of staged UI assets are managed by the delivery agent separately.

## Limits and follow-up evidence

- Structural or literal file checks attest the measured bytes and stated predicates, not overall semantics, legal creation of a business, publication, or deployment. Hashes describe the file at verification time; subsequent owner changes can alter it.
- Filename exclusions are privacy heuristics. An ordinary-looking selected file can contain sensitive material; this is not content-based secret detection. Imports remain explicit owner-selected reference snapshots.
- Windows junction creation was unavailable in this test sandbox. The reviewed handle-based guards and static path checks do not replace a successful privileged junction/race test on a permitted disk.
- The runner binds snapshot bytes and the configured image reference. This pass did not attest a Docker daemon, the bytes behind a mutable image tag, or a running sandbox. Docker remains an optional deployment trust boundary.
- The PDF subprocess resource controls and Office bounds were inspected as dependencies. This reviewer did not conduct a malicious-document corpus audit or certify all parser/dependency behavior.
- Crash recovery avoids automatic replay of interrupted/uncertain external actions. It does not create a distributed exactly-once guarantee for arbitrary remote services or simulate physical power loss.
- Cold disconnected startup, backup restoration into a fresh directory, physical microphone capture, energy measurements, and a second LAN client remain appliance-qualification work. No performance or operating-cost savings are inferred from these deterministic tests.

## Reviewed core snapshot

SHA-256 values recorded after the numeric changes and targeted tests. Later changes to these files require an appropriate review delta.

| File | SHA-256 |
| --- | --- |
| `ariadne/sources.py` | `073a434e8fb3bfa39144c355136381b00a91a92514e1536f22fb6acc962d450c` |
| `ariadne/store.py` | `891164c147d100cad51579e9fd78b8db0f61da1beda229cac4a7718bd7afe1e8` |
| `ariadne/engine.py` | `8403c386e05db2d20931218e8c84b24279c0437eab58725bb02623dfe307ee2d` |
| `ariadne/tools.py` | `6d834c0b18642e54c18e3feb8d82b5aeb7115e0f585ad9298e4857f537988bdc` |
| `ariadne/server.py` | `95bbdb959b15c60f03ccd3ea45b401076acfffb08469ed97a969cca0de55b52b` |
| `ariadne/verification.py` | `d35f8f00443d8177bb222e7b043e96401be0420c45e7cb4cd794b944d244db0f` |
| `ariadne/data_tools.py` | `3fc6ea9eaa99d6d71567b3ca217237c6288a3ecb91d82fc0b7c0426de7e97819` |
