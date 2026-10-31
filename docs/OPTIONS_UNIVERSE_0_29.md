# Multi-stock options and market-regime research

This offline lab evaluates fixed long-call/put hypotheses across explicit local stock archives. It requires no model, paid API, account, broker or resident service. It does not activate a strategy, change owner funds or replace the Ford paper account. A large number of comparisons does not establish a profitable edge.

## Available data and coverage

The provider's [free Level2 monthly sample](https://historicaloptiondata.com/sample-files/) covers22 August2019 sessions, with separate stock, options and IV-statistic files. The [field definitions](https://historicaloptiondata.com/data-structure/) distinguish stock close, option-linked underlying price, bids/asks and last trade. The source supplies no executable-side sizes or per-contract deliverables. All quotes, original files, rejection records and derived source substitutes remain private; no redistribution right is inferred from a free sample.

The importer verifies the original ZIP before/after processing, safe bounded central-directory inventory, all66 complete CSV byte counts/CRC/SHA, exact source dates and column structure. No bundled program is executed. Every source root receives a coverage disposition. Eligibility requires full raw-stock session coverage, at least one raw close below100, a standard alphabetic root, and usable retained quotes in every session. Known index roots are excluded. Remaining security types and standard100 American physical-delivery terms are explicit unverified assumptions, not authenticated contract or listing evidence. Missing sessions, adjusted/ambiguous terms and invalid quotes are never repaired into fills.

Version0.29.2 stores compact contiguous stock-window chunks instead of one file per stock/session. A512MiB aggregate encoded-snapshot buffer guard bounds packing; every dataset still obeys32MiB/200,000quotes/4,000snapshots and every stock archive retains the96-chunk/512MiB limits. Coverage requires all22source sessions regardless of chunk boundaries. Both final verification passes read each file using its recorded length plus an overflow sentinel, then verify exact length and full SHA256. This avoids requesting a2GiB temporary read buffer for every small artifact. The original1,800-second conversion deadline and2GiB output limit remain fixed. A timed-out or failed import remains unadopted; a separately inspected correction must rebuild from the original ZIP into a new exclusive folder.

Selection retains0–35calendarDTE and an inclusive strike interval from0.95times the minimum to1.05times the maximum raw stock close in the source window. That full-window interval conservatively retains potential entry and exit contracts; it is never a signal or stock-ranking input. The actual trading gate uses only the current decision/fill price. Higher-price history remains available for warmup and exits. Raw stock close is the reference, and every retained disagreement with the option's linked spot preserves the original fields in the issue ledger.

Source availability is not historical publication. Date-only rows become assumed session-close observations with a15-minute publication lag. Sizes remain null. The strict profile refuses unknown executable sizes; the separate price-proxy profile explicitly permits them without claiming executable liquidity. The22-session window cannot manufacture41/65-session warmups or establish a complete AI-cycle history.

## Two-sided context and entry timing

A verified retained QQQ/SPY chart bundle supplies price context. The fixed60-close rule labels stress/unwind, growth/recovery or mixed/neutral conditions. Stress means a QQQ daily decline of at least2% with negative20-session return, or both ETFs below their60-close means with QQQ at least5% below its20-close high. Otherwise growth/recovery requires both above their means with positive QQQ20-session return, or a confirmed two-close rebound, positive five-session returns and a remaining drawdown of at least3%. These price patterns do not identify or time an AI bubble.

The default `retained_capture` clock does not backdate observations before their actual capture. Historical gate studies must explicitly choose `assumed_eod_plus_15m`; publication and revised price vintages remain unverified. Features use prefixes only. A new decision uses context available at its publication clock; an older pending BUY can use only context already available at the quote's observation clock. A later-published close cannot selectively cancel an earlier modeled fill. Missing/stale context blocks directional entries, while marks, exits and mandatory disposal remain independent.

Descriptive analogs compare five fixed-scale features with prior same-label observations at least21sessions earlier and20-session spacing between matches. They never use later returns or option outcomes. Named eras, unmatched observations and missing-data periods remain explicit; an analog is not a profit forecast.

## Fixed comparisons and one shared allowance

`conventional_regime` compares cash, unfiltered recovery/breakout calls and breakdown puts, the three corresponding directional-context filters, and their context-aware rotating combination. `volatility_regime` applies the same comparison structure to compression calls, trend-pullback calls and downside-shock puts. These are the previously defined hypotheses, not a parameter search. Insufficient warmups, zero trades, held ledgers and losses remain results.

Every portfolio has one500USD monthly gross premium/fee commitment allowance across all its stocks and signals, one fully paid100-share call OR put total, and a100USD all-in entry ceiling including0.65entry fee and0.65reserved exit fee. Profits do not replenish the allowance. Both decision and fill require raw share price strictly below100. Entries use a later-session ask; exits use later eligible bids, with the existing5x/half-entry observations and mandatory disposal deadlines. Missing exact-contract quotes hold the ledger and can invalidate cap qualification; no synthetic exit, exercise, settlement or cross-month reset is supplied.

The stock priority is a deterministic lexical round robin independent of returns. Frames that begin managing a position/pending action cannot make a second same-frame entry decision. Funding/accounting stay in the existing tested kernel. Individual screens are separate500USD counterfactuals, processed sequentially. Pooled batches share one allowance within each batch/profile/preset. Never add individual stocks, batches, profiles, years or separate accounts into one return or an executable portfolio.

## Reproduce locally

Keep the original offered ZIP in private Yikes storage, record its actual retrieval time and SHA256, then qualify into a new Workspace directory:

```powershell
runtime\python\python.exe -I -B scripts\options_monthly_sample.py --zip staging/source/Sample_L2_2019_August.zip --zip-sha256 EXACT_ZIP_SHA256 --retrieved-at ACTUAL_UTC_RECEIPT_TIME --assume-standard-100 --output market-data/options/archives/market-2019-08-level2
```

Use only a completed import. Its `universe.json` admits qualified catalogs, lists fixed64-stock batches, and links complete source/coverage/issue proofs. Tentative files belonging to excluded stocks remain unadopted. Downloaded data is not included in Git or the source distribution.

Before studying outcomes, save a schema1 JSON intent describing the full source window, universe, fixed comparisons and reporting rules, then record its digest. Choose the exact stock catalogs and retained benchmark bundle:

```powershell
runtime\python\python.exe -I -B scripts\options_universe.py --manifest market-data/options/archives/market-2019-08-level2/symbols/ticker-F/catalog.json --manifest-sha256 EXACT_CATALOG_SHA256 --benchmark reviews/qqq-history/candidate-bundle.json --benchmark-sha256 EXACT_BUNDLE_SHA256 --benchmark-clock-profile assumed_eod_plus_15m --intent staging/study-intent.json --intent-sha256 EXACT_INTENT_SHA256 --preset conventional_regime --mode individual --output staging/study-001
```

Repeat the manifest/hash pair for additional stocks on the same complete calendar. Individual mode permits at most8stocks sequentially; pooled mode permits64. Each attempt retains at most16live ledgers,512MiB source and8MiB output, with a600-second deadline. A bounded process-local cache reuses at most two immutable benchmark timelines only when raw bytes, code/timezone hashes and clock interpretation match. Every run still checks owner pause authority and rehashes source/output evidence at final publication.

All outputs are exclusive. A failed or interrupted folder is never overwritten, replayed or adopted. Inspect substantive failures before any separately justified correction. Keep original failed receipts and earlier studies. The current owner's accounts, microphone, services and configuration are outside these research commands' authority.

Read contribution-excluded P/L beside fees, closed trades, exact ending dates, missing-quote/context holds, maximum commitment and full-window/flat status. Unequal held endpoints are not comparable full-period returns. Historical leaders across many tests remain exploratory; small samples and reused periods do not support universal-best or future-profit claims.
