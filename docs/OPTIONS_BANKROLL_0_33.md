# One-time $25,000 options paper bankroll

The sample research profile uses **$25,000 total paper capital with no automatic top-ups**. Version 0.33 implements a separate schema6 paper ledger on the existing KO account. Source delivery, native activation and profitability are different facts: consult the local delivery receipt and current status for actual deployment. No brokerage connection or real-money order is introduced.

## Exact capital contract

- Total simulated lifetime contributions become $25,000, including previously credited capital. The current pristine $500 account therefore receives a **single $24,500 adjustment**, not another $25,000. An adjustment is not trading profit or a daily market result.
- No subsequent contribution, monthly deposit or automatic top-up exists. A verified-flat, unheld later month resets only gross commitment and monthly-start equity; it creates no cash. Existing pending work or positions prevent rollover.
- One shared **$25,000 monthly gross commitment ceiling** spans all strategies and symbols. Sales and profits cannot replenish that month's gross allowance. Available cash remains a separate bound.
- An entry risks at most **min($1,000, 4% of current equity, available unreserved cash, unused shared monthly commitment)**, including premium and both $0.65 fees per contract.
- Only one fully paid standard long-put position exists. Integer quantity is also limited by **both actual supplied ask size and bid size** at entry, so its whole-position liquidation mark has recorded depth. Later insufficient full-quantity bids cause a hold; partial exits or depth guesses are not manufactured.
- The entire bankroll can be lost. A 50% signal is not a guaranteed 50% fill, and the gross commitment ceiling is not a peak drawdown stop.

## Strategy and source

The primary paper hypothesis remains the fixed stress-filtered breakdown put: fresh causal QQQ/SPY stress context, at least fourteen stock observations, and a close below each of the preceding ten closes and their mean. Keep the existing below-$100 underlying gate at decision and fill; 7–21 calendar days to expiry; decision-month expiry; 0–5% out-of-the-money strike; positive uncrossed bid/ask; spread at most 25% of midpoint; later-session fills and mandatory disposal deadlines. Preserve the fixed 5x/0.5x delayed-exit hypothesis rather than fitting exit thresholds to these new results.

**KO remains the sole configured forward paper root.** Historical IWM/QQQ files do not enable new forward feeds. The no-account OnClick collector still supplies partial date-only EOD prices; quote-event clocks and real executable fills are unverified. No continuous live market feed is claimed. New quotes require Internet; saved accounting, research, journals and briefs remain usable offline.

Schema3/4/5 contracts are unchanged. Once activated, schema6 and its bound sizing-policy dictionary are also frozen contracts; even description fields inside those hashed dictionaries require a separately reviewed version rather than an in-place edit. Migration binds the exact previous account, state, source cutoff and owner epoch; original Ford/KO source rows, receipts, history, counters and holds remain sealed. Schema6 uses separate new context gates and source/state anchors. An uncertain, failed or interrupted financial request cannot be automatically replayed. A disabled source or entry-only context failure remains disabled/failed after a capital adjustment and cannot authorize new exposure.

## Historical evaluation at the larger size

These completed development cases predate the owner's recent-only study direction. They remain archived evidence; new studies are limited by the [rolling twelve-month policy](TRADING_STUDY_WINDOW.md).

The offline runner compares four separate same-capital counterfactuals: stress-filtered puts, raw breakdown puts, recovery-growth calls and cash. Each receives $25,000 once, with no later funding. Every candidate uses the same window, registered sources, clock assumptions, fees, quantity constraints and delayed fills. Returns or capital from different candidates/cases must never be added. Cash participates in eligible rankings so a losing strategy cannot silently beat the zero-return control. Held/open results cannot rank.

Three fixed development cases were registered before their new outcomes: IWM 2008 (253 sessions), newly acquired IWM 2009 (252 sessions), and pooled IWM/QQQ 2011 (197 shared sessions). None is a qualified independent holdout.

| Case | Stress puts | Raw puts | Recovery calls |
|---|---|---|---|
| IWM 2008 | No trades | No trades | No trades |
| IWM 2009 | **-$2,334; 3 closed; complete** | -$2,986.60; 5 closed; complete | Held on July 7; -$1,411.70 last dated mark, -$1,141.50 realized |
| Pooled IWM/QQQ 2011 | Held on August 8; +$1,717.70 last dated mark, **-$193 realized** | -$1,000.30; 5 closed; complete | Held on May 13; +$242.50 last dated mark, no closed trade |

The held cases lack sufficient full-position quote liquidity; their dated marks are not completed gains. Cash stayed at $25,000 in each separate case. The complete 2009 put strategies lost money. **These results do not establish a profitable strategy, justify real-money deployment or demonstrate that larger capital improves expectancy.** The forward paper workflow exists to collect additional evidence with explicit limits, not to declare a winner.

The new 2009 Parquet file was checked against its preserved original LFS SHA-256 and mirror Git object. A second independent verifier rebuilt all twelve chunks and the catalog from pinned source bytes and compared them exactly: 291,063 original rows, 43,659 retained quotes, 252 sessions, zero quarantined rows. Zero supplied side sizes remain zero. Original feed rights, historical event/publication clocks, raw-price basis and standard deliverables remain unverified. Raw vendor data stays private and is not included in GitHub.

## Offline tools and operating cost

`scripts/options_bankroll_plan.py` prints the fixed plan or hypothetical sizing. `scripts/options_bankroll_research.py` runs finite manifest/hash/intent-bound historical comparisons with offline guards, source leases, bounded output, pause checks and exclusive non-replayed receipts. Neither tool activates an account or adopts a historical position.

No new model, API subscription, resident service or paid dependency is added. Conversion reused the bundled DuckDB reader. The existing bounded scheduled KO collection owns future paper observations; daily briefs and the evidence journal read saved accounting. Machine energy and long-term maintenance savings have not been measured. Full-model portable ZIP rebuilding and physical Wi-Fi-disconnection testing are separate from fresh source-copy offline validation.

Useful commands and primary source context: [strategy rules](TRADING_25K_STRATEGY.md), [KO source scope](OPTIONS_KO_0_32.md), [OIC long put payoff](https://www.optionseducation.org/strategies/all-strategies/long-put), [published multi-year archive lead](https://github.com/lambdaclass/options_portfolio_backtester/releases/tag/data-v1), [provenance notice](https://github.com/lambdaclass/options_portfolio_backtester/blob/d42ee039dff62e37139243141735438615e86111/data/DATA_NOTICE.md).
