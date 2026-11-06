# Options robustness research

This offline workflow measures how the existing options experiments respond to fixed price and liquidity stresses. Every source period has already been inspected. Results are development evidence, with no untouched holdout, automatic strategy adoption, paper-account migration or order placement.

This guide describes the method and commands. It contains no study outcomes or private market-data files. A source checkout or portable package does not supply the private captured cohort and its completed receipts.

## Fixed scope

The study uses the source-pinned KO cohort from May 4 through October 29, 2026: 125 regular sessions, approximately six months of partially observed chains. Those observations must all fit inside the actual registration's rolling twelve-calendar-month window. Being inside that window does not turn six months of captured data into a year of options history.

The source contains one selected expiry and sixteen strikes per side per session. Unknown side sizes, underlying-reference/adjustment-basis limitations, failed cross-provider reconciliation and source-calendar caveats remain attached to the original evidence. No missing source row is repaired or silently dropped.

The experiment grid is frozen before this audit reads outcomes:

- Eleven existing signal definitions, each with a 2x or 5x target, a five- or ten-session maximum hold, and one-contract or budget-limited sizing: 88 configurations.
- One cash control: 89 configurations in total.
- Both existing availability assumptions: `assumed_eod_plus_15m` and `assumed_next_session_0935`.
- Four fixed quote/depth views: 89 x 2 x 4 = **712 separate counterfactual paths**.

The signals cover stress-filtered puts, raw and three-day breakdown puts, recovery calls, moving-average reclaim, momentum, uptrend pullback, failed bounce, tight-range breakout, oversold rebound and volatile downside variants. Their exact definitions, grid and engine rules are hash-bound by the registration. The audit does not retune thresholds after seeing which case earns more.

### Scenario names

The intent uses semantic aliases; saved engine witnesses use the module identifiers below. The completed result records this mapping.

| Intent alias | Module scenario | Hypothetical change |
| --- | --- | --- |
| `recorded_base` | `recorded_base` | Detached exact copy of recorded inputs |
| `adverse_cent` | `modeled_adverse_cent` | Subtract exactly $0.01 from bid and add exactly $0.01 to ask |
| `half_depth` | `modeled_half_depth` | Floor-divide each known integer bid/ask size by two |
| `combined` | `modeled_combined` | Apply both fixed changes |

There is no clamp or rescue for a nonpositive modeled bid; it cannot supply an executable sale or zero-price liquidation. Unknown sizes remain null, zero remains zero, and volume/open interest never supplies missing depth. Original prices, sizes, identities and clocks stay unchanged outside these explicitly hypothetical views. Fields named `actual_*` in an unchanged engine report refer to that case's supplied scenario values; they do not authenticate a real fill.

## Unchanged capital and fill rules

Each path starts with a separate $25,000 contribution and no top-ups. Profits from different configurations must never be added together. The unchanged engine reserves both $0.65 per-contract side fees and caps each entry at the minimum of $1,000, 4% of flat cash, available cash and unused $25,000 monthly gross commitment. Selling does not refund gross commitment. A permitted monthly reset adds no money.

Entries require an underlying below $100, a standard 100-share multiplier assumption, 7-21 days to expiry, expiry in the decision month, the existing moneyness/spread gates and known positive size on both sides. The exact contract is selected causally from the supplied prefix. A subsequent fill uses a later eligible source session and reruns the unchanged entry gates and sizing. It cannot search later prices for a more favorable execution after the first eligible fill fails.

Buys use ask and sells use positive bid, with whole contracts and the supplied size limits. No midpoint, theoretical price or last-trade substitution is made. Target/stop observations create delayed exit intent. The engine also applies its precommitted disposal deadline, partial-sale depth accounting and holds for unresolved exposure. Historical daily data cannot establish intraday target/stop ordering or guarantee a buyer at the displayed bid.

The two clocks remain hypothetical historical conventions. Signal context must be available by its decision; fill context is checked against information available at that source session's regular open. Source publication and quote-event times are not independently established by this cohort.

## Reading the results

The audit exposes every configuration and all 712 cases, including cash, no-trade paths, losses and holds. A configuration receives a worst terminal P&L across all eight clock/scenario cases only when all eight finish flat and complete. Recorded-base profit leaders, all-scenario profit leaders and evidence-screen qualifiers are separate lists.

Each case reports fees, closed episodes, known-NAV fraction, realized P&L by SELL month, concentration and largest-winner sensitivity. Source months with no SELL show zero realized P&L. Partial SELL realization is included even while a position remains open; these amounts are not monthly NAV returns or fully closed wins. Unknown liquidation-sized NAV remains unknown. Any unknown NAV makes peak drawdown unavailable rather than zero.

Largest-winner shares explicitly identify their denominator: positive closed-episode P&L or net closed-episode P&L. The latter can exceed 100%. Removing the best one or two positive closed episodes is arithmetic sensitivity using net episode P&L. It does not replay later decisions, sizing or compounding with different cash. Held books retain no terminal-profit ranking.

### Evidence and loss-preference screens

The module's descriptive adequacy screen requires at least 30 fully closed episodes, exits in at least three distinct months, complete books, all interim NAV known and a passed loss-preference check in every clock/scenario case. This count/coverage screen does not itself require profitability. The runner's `intent_screen_ids` additionally requires positive terminal P&L in every case. Neither screen proves an edge, future reliability or independent validation.

The accepted $20,000 total-loss preference is an analysis screen against the initial $25,000, with a $5,000 equity threshold. For each recorded frame the conservative equity floor is cash minus remaining quantity times the $0.65 reserved exit fee. A known NAV below $5,000 is a breach. A floor below $5,000 combined with unknown NAV is `stop_unverifiable`. Such cases cannot qualify for the descriptive evidence screen.

This analysis does not activate a $20,000 stop in the existing engine or guarantee a future loss limit. Peak drawdown is measured separately and remains unavailable when NAV is incomplete. Original account controls, funding, financial policies and failure holds are unchanged.

## Run a reviewed offline study

Use the bundled Python from the Ariadne project folder. The runner installs process-local offline guards and uses `-I -B`; it requires no model, provider transport, cloud service or native Theta runtime. Owner pause authority is checked throughout. The finite limits are **600 seconds** and **32 MiB aggregate output**, with space reserved for a failure receipt. Full witnesses cannot be truncated to make a run pass.

Before running, review the existing capture receipt, previous experiment receipt and a predeclared intent. The intent must contain the exact 89 configurations, both clocks, the four named scenario descriptions, engine rule hash, 712 paths and the fixed limits accepted by `validate_intent`. Record its hash before any new outcome inspection. The runner checks the prior engine/signal source snapshots and binds its current sources, inputs and rules in a registration.

Replace every uppercase placeholder below with the reviewed local relative path or lowercase SHA-256. The output must be a new `staging/...` folder. These commands do not collect missing source data or grant permission to resume a held attempt.

```powershell
.\runtime\python\python.exe -I -B scripts\options_robustness.py `
  --source-workspace "staging/REVIEWED_CAPTURE" `
  --source-completed-sha256 "SOURCE_COMPLETED_SHA256" `
  --prior-workspace "staging/REVIEWED_PRIOR_EXPERIMENT" `
  --prior-completed-sha256 "PRIOR_COMPLETED_SHA256" `
  --intent "staging/REVIEWED_INTENT/study-intent.json" `
  --intent-sha256 "PREDECLARED_INTENT_SHA256" `
  --output "staging/NEW_ROBUSTNESS_STUDY"
```

The source includes an owner-data location; `--owner-data` defaults to `data`. The runner reads pause authority without opening a trading write interface. A source digest establishes byte identity, not provider authentication or a historically executable quote.

A completed study retains `attempt.json`, `registration.json`, `study-intent.json`, `contexts.json`, `results.json`, `completed.json` and **712 full engine reports** under `cases/<clock>/<scenario>/<configuration>.json`. All **178 recorded-base reports** must exactly match the previous completed experiment. Each summary binds its complete engine report by SHA-256, so an independent audit can inspect fills, curve, exposure and episode accounting without rerunning the financial engine.

A failure or interruption is held, records safe diagnostics where publication remains possible, and permits no automatic replay. Preserve that directory and its receipts. Existing outputs are never overwritten or adopted; changing the folder name does not resolve a substantive failure or grant new authority.

### Render a completed local report

Use the exact completed-study receipt hash. Rendering rechecks the full saved inventory, makes no new strategy run and creates a new report folder:

```powershell
.\runtime\python\python.exe -I -B scripts\options_robustness_report.py `
  --workspace "staging/COMPLETED_ROBUSTNESS_STUDY" `
  --completed-sha256 "COMPLETED_STUDY_RECEIPT_SHA256" `
  --output "data/workspace/reports/NEW_ROBUSTNESS_REPORT"
```

Open `report.html` from that new report folder. It is a standalone offline presentation of saved results. Source hashes, complete/held status and caveats take precedence over a leaderboard. No report or completed commit promotes a strategy into the paper account.

## Theta EOD readiness is a separate step

[`theta_eod_research.py`](../ariadne/theta_eod_research.py) provides the pure `assess_capture(...)` readiness API for completed direct-Theta EOD evidence. Callers supply capture and completion bytes with their expected hashes, the admitted research window and the existing public calendar bytes through `calendar_tzif_raw`. The reviewed calendar file is `ariadne/resources/briefing/America_Chicago.tzif`; the module checks its fixed hash and uses the bytes in memory instead of discovering host timezone files.

The readiness check validates completion/artifact bindings, raw frame hashes, exact typed prices, contract identity, preserved report/receipt clocks, requested sessions and optional independently completed matching stock evidence. It performs no file access, native decompression/import, provider request, model call, timestamp projection or account operation. It does not independently re-decode the saved compressed wire projection or authenticate market data merely because local hashes match.

The reviewed Ford capture contains **111 reports for `F270115P00015000`**, May 29-November 4, 2026, against a November 5, 2025-November 4, 2026 request. The earlier 140 requested sessions are missing. The contract has **72-231 DTE** across the returned period, so zero rows meet the current 7-21-DTE/same-month rule. Matching daily underlying evidence and recovery calls are absent. This is a verified capture/readiness milestone, not a usable matched stress-put/recovery-call comparison or a paper-trading authorization.

Theta's `created` clock describes report generation, documented at 17:15 Eastern; `last_trade` is separate and quote-event time remains unknown. Raw integer price/scale and timestamp fields, unknown OI and unverified standard deliverables remain explicit. Do not relabel this source as the KO provider, manufacture an observed close timestamp, silently widen DTE or adopt failed prefixes to force a strategy result. See [Theta history and protected setup](THETA_HISTORY.md) for the separate data-access contract. This guide redistributes no captured quotes and authorizes no new provider operation.

## Operating cost and validation

The saved-input audit has zero model calls, provider requests and recurring subscription use. Machine runtime is recorded in its receipt; energy use and monetary compute savings are unmeasured. Keeping the financial engine unchanged, streaming bounded full reports and reusing saved source evidence limits maintenance duplication without claiming measured savings.

Previously inspected dates remain development data even after a new split, stress test or higher historical profit. A subsequent experiment must freeze its hypotheses and assumptions before evaluating genuinely new observations. All current outputs retain `independent_holdout: false`, `sample_sufficient_for_edge: false`, `execution_qualified: false` and `automatic_adoption: false`.
