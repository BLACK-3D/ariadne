# Experimental options strategies and causal validation

This research develops new long-call and long-put rules from the captured KO history, including patterns suggested by profitable historical moves. Future-selected opportunities are kept separate from causal trading simulations. Each configuration has its own counterfactual $25,000 bankroll; their capital and profits cannot be added together. Historical results do not authorize a paper-account migration or establish future profitability.

## Fixed hypotheses

The eight experimental predicates were registered before their pattern audit inspected outcomes. They require fourteen completed closes, use only the supplied prefix and retain their original thresholds. These new hypotheses are ungated; the existing stress-filtered put and growth-recovery call comparators retain their respective benchmark regime gates. Raw breakdown puts and cash remain controls.

| Hypothesis | Causal price condition |
| --- | --- |
| Three-day breakdown put | Current close below the previous three closes and the latest ten-close mean |
| Five-day mean reclaim call | Previous close at or below its five-close mean; current close above its five-close mean and previous close |
| Five-day momentum call | At least 3% appreciation over five sessions |
| Uptrend pullback call | Current close above a rising ten-close mean, with a three-session decline between 1% and 3% |
| Failed bounce put | Below the ten-close mean; preceding two-session bounce at least 1%, followed by a down close |
| Tight-range breakout call | Previous five closes span at most 2% of the previous close; current close breaks above that range |
| Oversold rebound call | Previous five-session decline at least 3%; current one-session rebound at least 0.5% |
| Volatile downside put | Previous ten closes span at least 6% of the previous close; current decline at least 1% and close below the five-close mean |

`ariadne/options_experiment_signals.py` defines every exact lookback and comparison. The experiment registry combines eleven noncash signals with 2x/5x take-profit triggers, five/ten-session maximum holds and one-contract/budget-sized entries: **88 noncash configurations plus cash**. The half-entry-ask stop remains fixed. This is one finite registered grid, not an indefinite search until a winner appears.

## New research execution model

`ariadne/options_experiments.py` is a separate pure simulator. It does not import an owner account, change existing financial rule descriptors or adopt historical positions. The production schema6 contracts and previous held studies remain unchanged.

Each counterfactual receives $25,000 once with no automatic top-ups. Monthly gross commitment is at most $25,000; an entry is at most min($1,000, 4% of flat cash/equity, available cash, remaining gross allowance). Entry premium rounds upward per standard 100-share contract, and the calculation reserves $0.65 entry plus $0.65 exit fees per contract. Integer quantity is bounded by the decision target and the actual supplied ask and bid sizes at both decision and fill. Sale proceeds do not replenish the current month's gross allowance.

A decision uses the saved availability clock. A fill requires a strictly later New York source date than the New York decision date and an observation more than sixty seconds later. The delayed-availability profile waits through a source session that falls on the decision's own calendar day. An invalid first eligible buy cancels; the simulator cannot scan future prices for a better replacement. Regime evidence at a fill must have been available at the source session's regular open and must describe its latest prior completed session.

The experimental liquidation policy can sell part of a position at an actual positive supplied bid, at most the known bid size once per source frame. It does not apply the entry spread test to a sale. A price threshold latches a decision even if its displayed size is unknown; unknown depth still cannot produce a fill or full-position mark. Conditional exits wait for a later eligible session. A separate entry-authorized deadline is the earliest of the fifth/tenth following session, the session before expiration and the penultimate entry-month session. Residual quantity at that deadline permanently holds the counterfactual and excludes it from terminal-profit ranking.

A current liquidation NAV requires sufficient remaining displayed depth after any same-frame sale. Missing quotes or insufficient/unknown depth produce `null` NAV, not a stale carried mark or an invented zero. Later actual quotes may recover observability; full actual liquidation can establish terminal cash even if an earlier NAV was unknown. In that case maximum drawdown remains unestimated.

These are new research assumptions, not verified real executions. [OIC explains why displayed size and the bid/ask matter for execution](https://www.optionseducation.org/referencelibrary/faq/trade-entry-execution); a daily historical quote does not guarantee a later order can obtain its price.

## Chronological selection

`scripts/options_experiments.py` registers source/code hashes, all configurations, both timing assumptions, limits and the prior intent before parsing benchmark prices or option chunks. It observes the existing read-only global pause authority and stops on source, resource or receipt failures. Attempts reserve exclusive folders and are not replayed.

DESIGN includes only frames available by July 31, 2026, 23:59:59 New York. The two complete DESIGN results and their selection receipt are published before any full-path evaluation. Selection requires completed flat books with positive terminal profit and at least one closed position in **both** registered timing profiles. It maximizes the worse profile's terminal profit, with a lexical tie-break, against cash at zero. Duplicate configurations, mismatched profiles, contradictory exposure/cash and later-period inputs are rejected.

The full run starts from the same first source frame with the same single contribution, carrying cash and commitments continuously across the phase boundary. A later cash increment is shown only when the earlier and final endpoints are completed and flat. Full-history leaders are separately labeled post hoc diagnostics; they cannot replace the frozen DESIGN selection.

Both periods were already inspected in earlier work. This is a later **development check**, not an untouched holdout. There are 178 counterfactual paths per phase, not 178 independent trades. Multiple-testing significance, Sharpe and annualized performance are not estimated from this small dependent sample. [Bailey and coauthors describe the risk of selecting backtest winners](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf). A future prospective paper period would require a separately accepted immutable strategy contract and actual new observations.

## Source and interpretation limits

The available source contains **125 KO sessions, May 4 through October 29, 2026, and 4,000 option quotes**, all within the owner's rolling twelve-month study window. It is not twelve months of usable options history or evidence across many stocks. Each day has one predeclared monthly expiry and sixteen strikes per side. The delayed profile's final availability is October 30 at 09:35 New York.

Actual option quote prices and supplied sizes are retained. Observation/publication times, revision vintages, standard deliverables and price basis remain unverified assumptions. A separate underlying reference failed a global calendar check and cross-source equivalence checks. Its additive fixed-session projection excludes outside-cohort anomalies without repairing, splicing or replacing the original raw file. These limitations remain material even when an experimental ledger shows a profit. See [working recent history](OPTIONS_WORKING_HISTORY.md).

The separate backwards-looking audit evaluated 142 overlapping contract/window opportunities, including 41 without an eligible later exit. Its largest future-selected window was +$5,239.50 on $990.50 commitment. Choosing both the contract and exit with future knowledge makes this an oracle illustration, not a strategy return, independent win rate or evidence that the bankroll can scale that result. None of the eight pattern diagnostics established robust later evidence.

## Offline operation and results

The runner consumes saved, hash-pinned local files with no model calls, data downloads or recurring API/service subscription. Its registration fixes a 600-second execution deadline, 32 MiB output limit and at most 100 configurations. Development effort is separate from product operating cost; elapsed time and artifact sizes must come from the completed receipt rather than a savings estimate.

`scripts/options_experiment_report.py` renders a completed source-stable study into a new local, self-contained HTML report. It verifies the completed artifact hashes, retains every attempted configuration, separates the frozen earlier selection from post hoc leaders and breaks charts where NAV is unknown. It uses no network assets. Local source captures, owner files and detailed private market results are not published to GitHub.


## Completed November 1 experiment

The registered run completed in **107.606 seconds**, consumed all 125 source frames in each clock profile and kept source hashes stable. It produced 178 DESIGN and 178 continuous full-path counterfactuals with zero network calls, model calls or paper operations. Its bound artifacts total 8,778,568 bytes, plus the 2,412-byte completion receipt. This measures this local research run, not recurring power cost or a universal performance guarantee.

The frozen earlier selection was **cash**. No noncash configuration had positive completed DESIGN profit with closed trades in both clocks. For the full-history leader below, the earlier modeled profits were +$2,890.40 under close-plus-fifteen-minutes availability and -$38.30 under next-session availability. That timing sensitivity prevents calling it a prospectively selected winner.

| Post hoc full-history comparison | Close plus 15 minutes | Next session 09:35 New York | Closed positions |
| --- | ---: | ---: | --- |
| Five-day mean reclaim call, 2x target, ten-session ceiling, budget quantity | +$4,136.00 | +$3,872.40 | 4 / 4 |
| Same mean reclaim, five-session ceiling | +$2,534.00 | +$2,657.40 | 4 / 4 |
| Growth recovery call, 5x target, five-session ceiling | +$1,220.40 | +$4,975.10 | 2 / 1 |
| Raw breakdown put, 2x target, ten-session ceiling | -$2,354.50 | -$1,013.90 | 4 / 3 |
| Stress-filtered breakdown put, 2x target, ten-session ceiling | $0.00 | $0.00 | 0 / 0 |

Each row is a separate same-window $25,000 counterfactual. Both profiles of the full-history mean-reclaim leader ended flat with known terminal cash, but their interim NAV was incomplete, so maximum drawdown is unestimated. Its modeled fees were $39.00 and $41.60 respectively. The full case table retains two held close-plus-fifteen-minute books, losing configurations and no-trade cases; no held book contributes a terminal-profit rank.

The mean-reclaim rule is a useful **future research candidate** because it ties an observable recovery pattern to a fixed executable model. Four historical positions on one partly observed name do not establish a repeatable edge. Its full-history rank does not replace cash as the frozen earlier selection, does not change the live paper contract and does not justify scaling historical P&L. A future experiment should freeze this exact rule first, collect actual new quotes, measure liquidation depth and test adverse costs before any proposed paper-policy promotion.

Private local evidence:

- Study: `staging/experimental_options_2026_11_01/causal-v1`.
- Completed receipt SHA-256: `3362ed8679adadadcf4f74fba1c69d3d3bff54fba472fdcdef5f78b4cab81186`.
- Self-contained report: `data/workspace/reports/experimental-options-2026-11-01-v1/report.html`.
- Report SHA-256: `009e33cb4057d8b2209b05cc09d03583c5c30affaf64a7ee71fdaa386e2b3b91`.

These detailed local market artifacts stay out of GitHub. The repository contains the reusable rules, runner, report renderer and generated tests.


## Validation and concentration

A separately implemented ledger auditor checked all **356 saved cases** in 12.264 seconds without importing the new engine/signals or replaying trade choices. It matched quote prices and sizes, buy/sell timing, integer quantities, fees, cash, gross commitments, partial exits, known/null marks, deadlines, terminal results and the earlier cash selection. Source hashes were stable. It did not independently authenticate source prices, recompute signal predicates or reclassify the benchmark regime. Its final unfunded-study-end cancellation check also relies partly on the separately tested execution ordering. The completed audit receipt is `staging/experimental_options_2026_11_01/audit-v1/completed.json`, SHA-256 `1aeefa77d345df82d43d210be5a4464af57d192a4dfd38d3d6fde82ceb84ef10`.

A separate report review passed **2,743 checks** across every result-table row, the local links and all six SVGs. It verified that unknown NAV creates gaps, the earlier cash selection stays visible and all attempted cases remain present. Receipt: `staging/experimental_options_2026_11_01/report-review-v1/receipt.json`, SHA-256 `cc7c9691484df34f585430ff8c6cc0d04fc3b818a1f222fc5219fb81b677fe6b`.

The leading mean-reclaim model's four closed-position profits were +$2,911.70, -$21.30, +$1,770.30 and -$524.70 under the earlier-availability assumption; the delayed profile produced -$38.30, +$1,878.30, -$225.70 and +$2,258.10. Its largest position supplied 70.40% and 58.31% of net profit respectively. These are different calendar trades under the two clocks, not four stable repeated events. The delayed path includes a four-contract sale on September 11 followed by the remaining five on September 14; it depends on the new partial-liquidation assumption. Concentration, clock sensitivity and incomplete interim risk evidence are central reasons to collect new prospective observations rather than promote the full-history rank automatically.

Generated tests cover the exact signal definitions, causal prefixes, later-price selection independence, hostile Decimal contexts, fees and risk ceilings, partial/depth-limited exits, missing NAV, immutable deadlines, context freshness, source/output tampering, pause authority, exclusive attempts, phase publication order and truthful offline reporting. Fresh-copy qualification is separate from native hosting, full-model packaging or physical Wi-Fi disconnection; none is inferred from these research tests.


## Cost and depth robustness

The [fixed offline robustness audit](OPTIONS_ROBUSTNESS.md) extends this same 89-configuration grid across both existing clocks and four recorded or explicitly modeled quote/depth views. It recomputes all 712 paths, binds every full ledger and requires 178 original baseline hashes to match. It adds concentration and observed-loss-floor analysis while preserving the earlier cash selection, existing engine and all source limitations. This is development evidence on already inspected data; no new independent holdout or paper-policy adoption is inferred.
