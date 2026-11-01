# KO stress-put paper source — 0.32

The new KO source uses the same paper account and the same $500 monthly gross commitment and $100 all-in entry ceilings. It adds no brokerage, subscription, model, resident service or real-order interface. Ford remains a sealed historical epoch; it does not continue collecting into another funded book.

## What was actually verified

[OnClick's pricing](https://www.onclickmedia.com/pricing) advertises anonymous historical EOD options access for one year, with shared capacity. Its [About page](https://www.onclickmedia.com/About) describes educational/research use and upstream aggregation. The successful requests used completed historical dates on the documented API host, without credentials, redirects or retries. Public marketing is not a provider reliability guarantee or proof of upstream entitlements.

The finite qualification captured:

| Evidence | Actual coverage |
|---|---|
| KO availability metadata | 126 ordered exchange dates, May 4–October 30, 2026; metadata alone is not 126 captured chains |
| Unadjusted-path stock references | 15 complete minute grids, October 12–30; all 5,865 rows retained, including zero volume |
| Actual option snapshots | October 28, 29 and 30, November 13 expiry; 32 returned call/put quotes on each day |
| Strike coverage | Partial: 16 of 38 strikes; union 38 contract IDs, intersection 26 across these three snapshots |
| October 30 underlying reference | $94.08; a dated source observation, not a live price |
| Source-price capacity example | KO November 13 $92 put, bid $0.55 / ask $0.65, supplied sizes 96 / 230, $66.30 including entry and reserved exit fees |

The original metadata receipt was October 31 in New York. November expiry was therefore an **observation-only fallback at that recorded clock**. The capacity example is not an approved entry, current recommendation, backtest return or paper fill. Future decisions require newly acquired evidence and all current gates. No historical decision or fill is adopted from qualification.

Intel also returned genuine-to-retained-payload option rows, but no sampled put met the unchanged strike, spread and $100 entry rules. Cheap farther-out puts were not substituted. Account-requiring sources were not used; automated Cboe dashboard extraction was excluded under its published restriction. Existing denied, failed and uncertain source attempts remain stopped.

Three quote sessions qualify a source path and expose coverage gaps. They do not establish long-history strategy performance, reliable held-contract exits or a profitable edge. Partial chains, vendor provenance, quote-event clocks, publication cutoffs and standard deliverables remain unverified. Actual anonymous access may fail or change.

## Shared-account behavior

The explicit schema5 upgrade accepts only a healthy, flat, unspent, zero-trade schema4 account. It preserves the account ID/start, authority epoch, financial balances, funding count, month, commitments, counters, original migrations and complete Ford cutoff. It adds **zero** funds and resets no allowance. A separate KO cursor extends the existing source prefix, and storage/operation quotas remain shared.

KO indicator seeds contain only actual KO stock observations, never Ford prices. An explicit prefix through October 29 uses 14 observations from the unchanged 15-session raw body; October 30 is not seeded as a financial frame. The original whole-body hash and actual receipt clock stay on each seed. The first forward source must still be current and consecutive. Missing financial sessions cannot be repaired using warmup.

The new root-bound policy uses the existing breakdown-put signal. Original F-tagged QQQ/SPY witnesses remain immutable **benchmark data**, while new KO gates bind their causal calculation to the KO policy and actual query clock. Missing or stale context denies new BUY exposure, while existing marks, exits and mandatory disposal continue.

Entry requires the underlying below $100 at both decision and fill; 7–21 calendar DTE; expiry in the actual decision month; strike within 0–5% below the underlying; positive actual executable-side size; positive uncrossed quotes; spread at most 25% of midpoint; and at most $100 including $0.65 entry and $0.65 reserved exit fees. One fully paid standard-100 long put may exist across the whole account. Profits do not replenish gross commitments.

A durable decision cannot fill on its own historical source date. BUY and conditional SELL fills require a source session strictly after the actual decision's New York calendar day and a later receipt. A BUY fill also requires stress context published before the fill source session opened. Missing required disposal quotes hold the account; they never create a synthetic sale or settlement. Ordinary verified flat month rollover retains the existing single $500 simulated monthly contribution rule; activation itself adds nothing.

## Collection, offline use and controls

The selected source checks the previous completed session after **09:35 America/New_York on a regular market session**. Normal acquisition uses three GETs; required expiry coverage may use up to five, sharing the unchanged 15-second / 8-MiB wire budget. Valid no-candidate metadata with no required contract uses two GETs and records only the stock observation, with zero option quotes and no entry. An empty requested chain or missing held contract remains a failure. The offline owner independently normalizes child-returned raw evidence before publication. The worker receives only the fixed KO source profile and existing permission revisions; it cannot accept a general URL or broker command.

Internet permission, global pause, options/briefing controls, original account authority, exact CAS revision and retained failure receipts remain authoritative. Failed or uncertain operations are not automatically retried or healed by a different ticker. Offline mode keeps saved evidence, the journal, results and brief available; it cannot invent new quotes. The journal performs no financial replay or collection.

The Options panel identifies KO explicitly and sends its controls to the versioned `root-config` / `root-collect` endpoints. Superseded Ford mutations cannot reserve new work against the KO account. The 6 AM brief reports saved KO results with the actual source date, receipt, fees, commitments, holdings and stress-context status; zero trades and stale observations remain explicit.

An owner-authorized local upgrade uses `POST /api/options/root-migrate` with only `request_id`, `expected_revision`, `acknowledge_simulated: true`, `acknowledge_partial_eod: true`, and an optional Workspace-relative `baseline_path`. The exact KO manifest has `schema: 1`, `kind: "onclick_ko_stock_baseline"`, `raw_path`, `raw_sha256`, the original `received_at`, `through_date`, `source_receipt_path`, and `source_receipt_sha256`. The retained original response receipt must bind the exact KO URL for the entire source window, HTTP 200, body size/hash and actual receipt clock. A Ford stock file cannot become a KO seed merely by changing a manifest label. The parser validates the entire raw window before taking the explicit prefix. No source payloads, credentials, owner databases or private captures are distributed through GitHub.

## Validation scope

Generated offline tests exercise same-account migration and funding continuity, real KO identities, source byte/clock validation, root-bound stress decisions and delayed fills, the shared cap, no profit recycling, exits without fresh context, mandatory-disposal failure, pauses, revoked permissions, uncertainty stops, tampered evidence, passive journal/readiness and UI control routing. Frozen schema3/4 output corpora must remain unchanged after extracting the shared monetary transition.

Release verification includes the complete repository suite and a fresh source copy with process-local network/process/original-source/database guards. This is source/runtime validation, not a physical network-disconnection test, full-model portable-package rebuild, provider SLA or proof of investment profitability.

The [differential review](REVIEW_OPTIONS_KO_0_32.md) records the security boundaries, resolved evidence gaps, independent checks and measured parsing cost.
