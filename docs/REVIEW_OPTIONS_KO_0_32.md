# Differential review — KO paper source, 0.32

Baseline: `90eccce0268869e31ca7aa134008f766f86c1528`. Scope: the shared EOD financial extraction, explicit KO schema5, source/child boundary, same-account host migration, immutable evidence, context publication, journal/readiness, API/UI/brief dispatch, package version and generated regressions. This is a software/evidence review, not investment validation.

## Executive summary

Overall change risk: **high**, because it touches simulated value accounting, retained evidence and outbound requests. Independent financial, source/transport, host and cache reviews found no remaining scoped blocker after the fixes below. Activation remains conditional on the final stable complete suite, fresh-copy guards and unchanged owner permissions.

| Finding | Resolution |
|---|---|
| Later KO mutations did not fully rehash the inherited Ford cutoff | Full mutations and journal reads now audit the original F artifact/source prefix without replaying trading |
| Stock rows have no ticker, so a label alone could misattribute baseline prices | Retain and bind the original KO response receipt, exact whole-window URL, HTTP status, bytes/hash and receipt clock |
| Saved gates were not proved against their exact saved input ring | Recompute only the bounded stateless benchmark query for newly added gates; bind all ring witnesses to published immutable records |
| Receipt metadata could diverge from exposure IDs or actual decision clocks | Verify required IDs, schema, status, source/receipt clocks, adoption count and exact after-state binding |
| Valid no-candidate metadata incorrectly stopped a flat account | New KO source can record a stock-only observation; requested empty chains and missing required contracts still fail |
| Baseline parsing could consume the fixed collection deadline | A one-item exact-byte parse cache retains per-call reads, SHA checks, path/size/activation checks and isolated copies |

All findings were resolved before owner activation. No risk limit, runtime deadline, storage quota, substantive-failure stop or existing pause was widened to make the integration pass.

## History and blast radius

Git blame traces the original schema3 validation and fixed-host transport to `b760d905`. The source baseline and its complete state/event/summary output corpora were independently loaded from Git, rather than trusting a new golden generated from modified code.

The shared monetary step has **two direct production callers**: the unchanged legacy wrapper and the explicit KO wrapper. The extracted financial header, tail and summary likewise have two callers each. Shared HTTPS transport has two fixed-validator callers, F and KO. There is no JSON-selected validator, general URL, alternate broker or second accounting implementation.

The broader path is owner API/scheduler → selected source workflow → permission-bound child → fixed historical API → offline normalization → pure shared ledger → atomic evidence/state commit. Readiness and journals have no source or trade authority. The three new owner endpoints use the existing authentication and ownership/CAS controls.

## Trust boundaries and adversarial checks

The feed is treated as untrusted, partial educational input. Checks cover wrong or adjusted roots, OSI/strike/type/expiry mismatches, malformed and duplicate JSON, changed bytes, crossed prices, zero supplied sizes, bad minute grids, future/stale receipt clocks, extra/omitted/requested-empty chains and missing held/disposal contracts. No chain text or repository content is executed. The worker retains the original fixed public-DNS/TLS policy, 15-second / 8-MiB aggregate and five-request ceiling, with no credentials, redirects, proxy or retry path.

The owner API accepts an explicit same-account upgrade, not supplied cash, strategy parameters, fees or risk limits. Tests cover no activation funding/reset, preservation of nonzero prior funding count and global source quotas, delayed decisions/fills, current-month/DTE/underlying checks, no profit recycling, missing mandatory disposal and exit continuation without fresh context. Superseded F mutations cannot reserve new work against a KO account.

Coherent-tampering cases update report hashes while substituting an empty context ring, replace baseline response roots/clocks, modify old F evidence and restore file length/mtime, or alter current KO cache bytes. These must fail without adopting account/events/source changes. Cache checks include wrong-root receipts, changed activation/prefix/grid policy, modified unused raw tails, return mutation and concurrent cache replacement; immutable bytes are rehashed on every verification.

UI tests verify explicit KO source/policy binding, correct versioned controls, no action on ordinary status ticks, rejection under pauses/uncertainty, no extra funding fields and text-only rendering. Brief tests retain dated results, unknown quote-event clocks, source freshness, fees, shared limits and zero-trade states. Journal tests reconcile sealed entries/exits, isolate Decimal context and prove repeated reads leave SQL unchanged without financial replay.

## Independent evidence

Private qualification receipts are retained locally under `staging/paper_multiroot_2026_11_01`; vendor payloads and owner data are excluded from the repository.

- Financial review: 99 generated checks, five full legacy output corpora byte-identical to the original Git blob; receipt SHA-256 `ab38954cf7b204011cf0ce8442e2a0c63712abbaaa963474c143149dabc2df60`.
- Source/transport/child review: original transport AST/body preserved apart from the fixed code-owned validator; source59 and worker13 generated checks passed. Review SHA-256 `d757d732abe62309f81746c0c079d1931f7a816ef94adad5883c01d5c7775fd5`.
- Host review: four accepted evidence-binding findings resolved; receipt SHA-256 `0c5c1b1761b90fc05d2aca6f6ddde4455495020cd9639d3ce0da1341f0223950`, supplemented by the cache review after its narrow change.
- Final cache review: 21 independent checks at final workflow SHA-256 `4498cab6549734f1f314dd1f3badeb25cee42e08b088e91479288f91a35db535`; receipt SHA-256 `5734b7866db20499a6d415f78248c7f8b5403732e844165387d5e538fec2f1a4`.
- Host regression file contains 28 generated cases; the three cache regressions and two preserved-research regressions also passed with global source stability. Explicit historical imports remain confined to the research cache, and VIX studies cannot alter the shared account. Final complete-suite and fresh-copy receipts bind all final source bytes separately.

Measured baseline parsing used the actual retained 615,037-byte source body: 0.478 seconds cold and 0.0062–0.0064 seconds warm in the independent run. These are parse-method measurements, including its path/hash checks but excluding caller artifact reads, account/HTTP work and provider delay. They establish no end-to-end latency guarantee or unmeasured energy/API-cost saving. The product adds no model calls, paid API or new resident service.

Earlier functional test runs whose source changed during review are explicitly marked unstable and are not release qualification. Generated monetary examples are software tests, not historical strategy returns.

## Limits and recommendation

Approval is limited to an experimental paper-price workflow. Real provider availability at the next configured collection, quote authenticity, source publication guarantees, OPRA entitlements, standard deliverables, intraday paths and profitable execution are not established. Actual acquisition evidence covers three option dates and 15 stock sessions; availability labels do not establish a longer captured backtest cohort. Physical Internet disconnection and a rebuilt full-model portable ZIP are outside this source release's validation.

Release only after the final frozen suite/fresh-copy checks pass, preserving current owner settings and all existing holds. A failed or uncertain activation/collection stays closed for inspection; it must not be automatically replayed.

Final frozen qualification completed November 1, 2026 UTC: **2,260 repository tests, zero failures/errors, seven skips**, with source stability; full receipt SHA-256 `efd09b05c7a60d2d4baba76f8f10366171fb1feda6a751e7f7a515443a524eee`. The final fresh source copy passed **118 tests**, zero skips and zero network/process/original-source/outside-database attempts, with four guard self-tests and unchanged source; receipt SHA-256 `cf1227c541ba822702721c45c840e5354a0510904de8ae72531d52902f545e15`.

The subsequent native 0.32 restart passed 25 preservation checks. The single explicit KO activation then passed 61 read-back preservation checks, including the original account/financial fields, immutable F tables, prior receipts, briefing settings and microphone-off state. Activation added no funding or trades. These operational witnesses establish the installed configuration, not future source availability or profitable fills.
