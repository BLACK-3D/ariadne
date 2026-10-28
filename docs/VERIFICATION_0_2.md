# Ariadne local-box 0.2 verification

Verified on this Windows PC on 2026-10-28. This is the standalone Yikes release, not acceptance or promotion of the separate Moonshot/Threadline program.

## Release evidence

- Final full suite: **90 tests, 87 passed, 3 skipped**, 13.467 seconds. Command: `.venv\Scripts\python.exe -m unittest discover -s tests -q`, with local socket/temporary in-Yikes file fixtures permitted. Three older symbolic-link fixtures require Windows privileges not available here. Actual Windows junction, hidden-attribute and hard-link boundaries passed. An earlier sandbox-only run could not connect fixture sockets; those failures were resolved by rerunning with local socket access, without weakening the tests.
- JavaScript syntax: `node --check ariadne/static/app.js` passed after promotion.
- Independent review: [REVIEW_0_2.md](REVIEW_0_2.md), including exact rational-oracle comparisons for weighted sums and legacy-state migration. [RED_TEAM_0_2.md](RED_TEAM_0_2.md) records 17 adversarial fixtures and reviewer/builder scope. No unresolved serious issue was identified within those reviewed surfaces. These are engineering reviews by separate agents, not a third-party certification.
- Fresh local model qualification: `data/verification-0.2/4ac7ebea2f7347b59ef3348205fd2e78/result.json`. Qwen3:8b completed in **6 model steps / 14.298 seconds**, passed all five owner predicates, and passed the external semantic fixture. It imported a synthetic DOCX, saved the exact two inventory rows, computed total_units=5 and weighted total_value=16, and wrote a source-citing operations checklist. Three artifact hashes are retained in the result. Timing describes one local run, not a general performance guarantee.
- Browser QA used a disposable paused instance on port8089 with synthetic data/key, then the real deployment on port8080. Checked capabilities, workflow create/save/reopen, quoted numeric preservation, rejection of rounding-prone decimals, source preview/import, versions/hashes/warnings, escaped file text, separate passed/failed results, and script approval preview. Synthetic approval actions were never executed. The QA server was stopped after testing.

## Preserved failure and correction

`data/verification-0.2/3cf791edc6bb46f1b7f8a4967fba4d78/` remains failed evidence. Its model produced 6.75 by adding unit prices instead of the correct quantity-weighted value of 16. Structural file checks passed; the separate semantic fixture failed. That run received no qualification credit. The product gained a deterministic two-column `sum_product` operator, exact owner-authored `json_number` predicates and actionable numeric repair feedback before the successful replacement qualification.

A separate review found that raw JSON float expectations could round before validation. Expected values now require quoted decimals or integers; the UI requires safe integers for unquoted values and rejects fractional/exponent literals before serialization. Actual artifact JSON parses with Decimal. API and UI regression evidence covers this correction. Numeric checks are bounded to 200 coefficient digits/exponent magnitude 400; they do not cover every possible long fixed-string aggregation output or certify overall business quality.

## Upgrade and offline assets

No mission was active before the verified Yikes 0.1 listener was stopped. A consistent private backup was created at `backups/ariadne-before-0.2-20261028.zip` before additive schema migration. The prior source archive is retained. One historical completed mission, its messages and existing pairing/model settings survived; the new browser tab reused the existing pairing. Three starter workflows and two read-only source registrations are present, with four imported engineering references.

The upgraded public `/health` endpoint reported **0.2.0** on 127.0.0.1:8080. The actual UI reported **qwen3:8b**, eight ready capability groups out of nine, with both offline voice adapters ready; the Docker runner remains disabled. The standalone `doctor` command reports the portable environment default 4b rather than the UI's persisted 8b selection. Native process metadata identified the exact Yikes launcher before stopping/restarting it; no unknown listener received credentials.

Waitress 3.0.2 and pypdf 6.21.3 wheels were cached under `runtime/wheels` using hash-locked requirements and no package cache, for offline dependency repair with Python already installed. Existing model weights, Ollama and whisper assets remain in Yikes. No new recurring service or paid API was added; no energy/cost saving is claimed from unmeasured runs.

All implementation, fixtures, dependency downloads, state and backups for this iteration stay under Yikes. Other source folders were read-only. E: was excluded from discovery, sources and runtime paths.

## Limits

Native Windows is the exercised deployment. Docker/Compose and the optional Docker execution path have mocked boundary tests but no live daemon test on this host. Generic MCP, OCR, always-listening wake words, live browser agents, external account integrations and physical device control were not activated. Source filenames cannot detect every embedded secret; import requires owner review. Output checks verify specific owner criteria, not unrestricted autonomy or movie-level intelligence.
