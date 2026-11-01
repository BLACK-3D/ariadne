# $25,000 stress-put strategy proposal

November 1, 2026. The sample research profile uses a **single $25,000 total paper bankroll with no automatic top-ups**. The offline planner does not inspect or change an account. A separate schema6 engine/host implements the versioned paper transition; consult [implementation and measured historical evidence](OPTIONS_BANKROLL_0_33.md) and the current local delivery receipt for actual activation status. No real trading is authorized.

## Capital and sizing

| Control | Proposed research rule |
|---|---|
| Initial total capital | $25,000, once; include existing credited capital in any future migration |
| Additional deposits | $0; no automatic topups |
| Shared monthly gross commitment ceiling | $25,000 across all symbols and strategies, subject to available cash |
| Per-entry all-in maximum | Smaller of $1,000, 4% of current net equity, available unreserved cash and unused monthly gross allowance |
| Position count | One long option position total; integer contracts limited by actual supplied ask size |
| Costs | Premium plus $0.65 entry fee and $0.65 reserved exit fee **per contract** |
| New month | Only when verified flat and unheld; reset commitment accounting, add no cash |

The $25,000 monthly ceiling can put nearly the initial bankroll at risk. It is neither a protected drawdown limit nor permission to deposit another $25,000. Profits cannot refill the gross allowance within the same month. The entry ceiling stays at $1,000 after gains and shrinks after losses. Ten idealized losses at exactly 4% of remaining equity leave approximately $16,620.82; this is stress arithmetic, not a probability or backtest. The entire bankroll can be lost over time.

## Primary strategy: confirmed stress breakdown puts

1. Require the existing causal QQQ/SPY benchmark context to be **ready / stress_unwind**. Its frozen definition uses a severe QQQ daily decline with a negative 20-session return, or both benchmarks below their 60-session means with QQQ at least 5% below its 20-session high. It does not forecast an AI bubble.
2. Require at least 14 completed stock closes. The latest close must be below each of the previous ten closes and their arithmetic mean.
3. Use only supported US stock/ETF contracts with saved raw underlying below $100 at decision and fill. Keep the current 7–21 calendar-day expiry window, decision-month expiry, 0–5% out-of-the-money put strike, positive uncrossed bid/ask and spread at most 25% of midpoint. Rank nearest 14 days, then nearest spot, then contract ID; do not choose by retrospective return.
4. Record the decision first. Buy only at a later eligible session's actual ask, rerunning all gates and quantity sizing. A hypothetical sizing result does not qualify a quote or authorize a fill.
5. Preserve the existing exit hypothesis: a bid at least five times the entry ask or at most half the entry ask creates a delayed sell decision. A 50% trigger cannot guarantee a 50% realized loss. Force disposal by the earliest of the tenth following regular session, session before expiry or penultimate entry-month session.
6. Require the actual later bid and supplied bid size covering the entire quantity. Missing or insufficient mandatory disposal evidence holds the study/account. Do not invent settlement, split fills, replacement contracts or exercise.
7. Stay in cash when the required data, regime, liquidity or risk budget is unavailable.

The original signal/exit settings are retained to keep the next comparison interpretable. They are hypotheses, not empirically optimal parameters. Spreads would need a separately specified two-leg fill, assignment and fee model; this proposal does not silently enable them.

## Matched research, before promotion

New performance studies use only the [last twelve calendar months](TRADING_STUDY_WINDOW.md). The older case results below remain historical reference, outside the current research window.

Compare stress-filtered puts with unfiltered breakdown puts, recovery-growth calls and cash. Each counterfactual starts with the same one-time $25,000 and uses identical dates, sources, clocks, size rules, fees and sizing. Do not add their returns together or multiply earlier one-contract P&L by the larger budget. Higher entry limits change the eligible contracts, quantity and liquidity requirements.

Existing windows have already been examined and are **development evidence**, not new holdouts:

| Existing study under its original $500 monthly / $100 entry contract | Completed modeled result | Limitation |
|---|---:|---|
| IWM/QQQ pooled 2011, strict sizes, stress puts | +$281.10; 3 closed trades | $5,000 contributions over ten months; old/provisional source assumptions |
| Same pooled study, unfiltered puts | +$52.80; 4 closed trades | Same small development cohort |
| Same study, recovery calls | Incomplete / held | Missing mandatory exit quote; not a completed ranking |
| KO July–December 2022, stress puts | +$24.80; 4 closed trades | Too few trades, provisional terms/clocks |

These are not $25,000 backtest results or evidence of repeatable profitability. The planner itself never backtests or activates an account. Newly completed $25,000 development comparisons are documented in [version 0.33 evidence](OPTIONS_BANKROLL_0_33.md); they do not establish profitability. Review the entire path, drawdown, costs, coverage, open/held cases and number of independent trades. Use newly registered unseen chronological evidence and prospective paper observations before proposing adoption. No fixed profit target or guaranteed winning strategy is inferred.

## Longer history lead

The [publisher's data-v1 release](https://github.com/lambdaclass/options_portfolio_backtester/releases/tag/data-v1) offers Parquet files advertised as IWM/SPY 2008–2025 and QQQ 2011–2025 with published hashes. The [commit-pinned data notice](https://github.com/lambdaclass/options_portfolio_backtester/blob/d42ee039dff62e37139243141735438615e86111/data/DATA_NOTICE.md) identifies uncertain original provenance; the software license does not establish market-data redistribution rights. Smaller annual files are published in the [preservation mirror](https://github.com/anahatsingh-ui/options-dataset-hist).

This was metadata research only: no new archive downloaded or strategy run. Any future acquisition needs a finite pinned intent, fail-closed digest validation, bounded resources, review of usage rights, actual quote sizes/event clocks, raw underlying basis and deliverables. Do not run upstream download helpers or retry previously denied routes. A large archive alone cannot qualify paper executions.

## Offline tool

From the Ariadne project folder:

```powershell
.\runtime\python\python.exe -I -B scripts\options_bankroll_plan.py plan
.\runtime\python\python.exe -I -B scripts\options_bankroll_plan.py size --ask 2.85 --ask-size 5
```

The second command uses **hypothetical inputs** and returns three contracts with $858.90 all-in commitment. It is not a current INTC quote, approved entry or broker order. The tool prints JSON and performs no file writes, API/model calls, downloads, account mutations or background work. It uses Python's standard library. Recurring compute and energy have not been measured; no paid service is added.

Legacy schemas 3/4/5 are unchanged. Their funding and one-contract invariants must not be repurposed by changing constants. Activation requires the confirmed total-capital contract and a separately reviewed versioned transition preserving account identity, existing balances/exposures, source receipts and holds. Never credit $25,000 on top of an existing balance or replay a historical trade.

Primary payoff reference: [OIC long put](https://www.optionseducation.org/strategies/all-strategies/long-put). A long put can lose its entire premium; correct direction alone does not establish a profitable option trade because price, time and volatility matter.
