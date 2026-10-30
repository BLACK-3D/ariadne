# Shared options portfolio and expanded historical evidence

Version 0.23.0 extends Venture Lab → Options lab. It adds three fixed hypotheses, a single combined paper-risk ledger, streamed immutable historical archives, comparison reports, strategy attribution and daily brief coverage. It has no broker, purchase, paid subscription, general model request or new runtime dependency. The original 0.22 engine, inputs, historical reports and account identity are retained.

## Three hypotheses, one allowance

The hypotheses were registered before examining the expanded historical outcomes:

| Strategy | Completed-session trigger | Instrument |
| --- | --- | --- |
| `recovery_call_v1` | Original recovery trigger: above the preceding two observations after a 3% closing decline from the preceding ten-observation high in the current/prior four observations | Long call |
| `breakout_call_v1` | Above the preceding ten-observation closing high and ten-observation arithmetic mean | Long call |
| `breakdown_put_v1` | Below the preceding ten-observation closing low and ten-observation arithmetic mean | Long put |

All require a 14-session warmup. The shared selector begins in the listed order and rotates after an entry. It never ranks strategies using backtest profits. There is **one open position total**, one cash ledger and **one $500 monthly commitment allowance across all strategies**. Three separate counterfactual studies are research comparisons; their $500 accounts and returns must never be added together or adopted into the forward account.

Each entry is one fully paid standard 100-share option. Premium plus entry fee plus reserved exit fee must fit within $125 per entry and the remaining shared allowance. Fees are $0.65 per contract per side. Profits and sale proceeds cannot replenish monthly commitments. The modeled cap applies to net loss from a flat month-opening account, excluding contributions; it does not constrain drawdown from an unrealized peak or guarantee an actual broker outcome.

Eligible contracts have 7–21 calendar DTE, expire in the current month, and are between spot and 5% out of the money in the corresponding direction. Ranking uses distance from 14 DTE, distance from spot, then contract ID. Positive uncrossed bid/ask and spread no wider than 25% of midpoint are required. Forward executable-side size must be at least one contract. The selected contract is fixed at decision time; the next eligible snapshot must be more than 60 seconds later. Buys use ask; sales use bid. No same-snapshot execution or hindsight contract substitution.

The original five-times-entry-ask target, half-entry-ask observation and precommitted mandatory disposal remain. A target observation is not a guaranteed fill or net payout. Missing executable disposal evidence holds the entire affected ledger, retains the last dated mark and blocks new risk/funding; no zero-price sale, invented settlement or new-month reset completes the chart. There are no short legs, margin, exercise or real orders. Deposits remain separate from P/L.

Portfolio rule SHA-256:
`e75bca82ac7c6a2a572e351c2bb7f4b4188c632153b3ef6b5358cc4a8357df62`

## Better coverage, explicit source failures

The data scan examined official products and public research samples. [Cboe Option Quotes](https://datashop.cboe.com/option-quote-intervals) documents historical NBBO quotes/sizes from January 2012; continuous history is paid and was not purchased. Its one-date public sample is useful for adapter qualification, not a multi-year return study. Publication is documented overnight; a schema minimum of 15 minutes is not proof of historical availability. A [researcher's minute sample](https://lewenguo.com/data) covers one date; the full dataset requires contact, which was not initiated. [HistoricalData.net](https://historicaldata.net/options.html) shows an EOD format with blank historical quote-time values; column names alone do not prove recorded times.

The larger private input uses only data files from [pinned mirror revision 37f6c456fe1a4775c875673fb8ef907d5cd2fd66](https://github.com/anahatsingh-ui/options-dataset-hist/tree/37f6c456fe1a4775c875673fb8ef907d5cd2fd66). No repository code was installed or executed. The [author's methodology paper](https://static.philippdubach.com/pdf/2026_Dubach_OptionOrderFlow.pdf) does not authenticate a specific feed or individual bid/ask timestamps. Feed origin, price accuracy, chain completeness, corporate actions and underlying adjustment conventions remain unverified. Dates are **assumed EOD**. This is broader retrospective evidence, not a verified executable feed. Mirror license labels do not establish exchange-data redistribution rights; raw and normalized price files stay private and outside GitHub and source ZIPs.

Selection was fixed before strategy outcomes: initially full 2020–2022, with a 0–35 calendar-DTE scope if full chains exceeded the bounded archive size. The completeness audit found missing June 23, 2020 and November 4, 2021. A predeclared 2022–2023 extension failed strict conversion because 78 late-2023 rows contradicted their OCC contract strike. No rows were repaired, filled or silently removed. Incomplete conversions remain quarantined research evidence. The predeclared **full-2022 fallback** passed: **251 expected exchange sessions, 857,552 supplied quotes, 11 chunks, 163,738,667 bytes**. All 100,781 zero bids and corresponding zero sizes are retained. This proves coverage of the supplied scoped rows, not full exchange-chain completeness.

The 0–35 DTE scope retains calls, puts, all supplied strikes, zero quotes and sizes inside that scope. It supports the fixed 7–21 DTE entries and held-contract exits. It is not an outcome-based liquidity or profitability filter. The accepted workspace manifest SHA-256 is:
`cedcdb3b74e134697b0ad880035a2428dea82fed928135c183dbccf3c3eaf15b`

## Actual 2022 results

| Ledger | Trading P/L excluding deposits | Closed / entries | Processed source sessions | Evidence outcome |
| --- | ---: | ---: | ---: | --- |
| Shared $500 portfolio | −$162.60 | 1 / 2 | 37 / 251, through February 24 | Held at missing executable exit; last dated mark |
| Recovery calls, separate counterfactual | −$162.60 | 1 / 2 | 37 / 251, through February 24 | Held at missing executable exit; last dated mark |
| Breakout calls, separate counterfactual | −$56.20 | 3 / 4 | 236 / 251, through December 8 | Held at missing executable exit; last dated mark |
| Breakdown puts, separate counterfactual | −$9.90 | 3 / 3 | 251 / 251 | Completed exploratory study; losing |

The breakout counterfactual recorded one realized net 2× payout. No ledger recorded a realized net 5× or 10× payout. One large payout did not make the overall study profitable. Held rows are not completed full-year realized returns; their source coverage and stop state remain visible. There is no annualized return claim, estimated win probability, automated winner selection or prospective profitability qualification. No profitable strategy was established. The original unsuccessful 2011 result remains accessible separately.

Reports bind the rule, accepted manifest and chunk identities. Independent reconciliation checks premium ceilings, credit floors, both fees, monthly commitments, one-position ownership, cash, external contributions and strategy attribution using exact rational arithmetic. Hash identity cannot authenticate market prices or demonstrate prospective edge.

## Local archive and account lifecycle

Each archive has an exact, bounded JSON manifest identifying provenance, scope, limitations and immutable workspace-relative chunks. Every actual study reopens and verifies manifest/chunk sizes, hashes, schemas, ordered terms and complete trading-session continuity. An error in a late chunk prevents publication of a completed result. Permission checkpoints stop revoked work. Passive status verifies the small accepted manifest and saved reports; it does not parse the full 164 MB archive on each UI poll. Reports retain the accepted manifest and refer to private chunks rather than duplicating them on every run.

Limits: 96 chunks, 32 MiB/200,000 quotes per chunk, 512 MiB per archive, 16 registered catalogs, 10,000 source observations per study. Existing two active 32 MiB input caches, 32 accounts, 30,000 events, 10,000/32 MiB receipts and 100/128 MiB shared report reservations remain. Failed reservations stay charged; no automatic financial-history deletion.

A pristine legacy account may upgrade **in place**, preserving its ID, original start time, cash, contributions, month, monthly opening equity and allowance. Only an unobserved zero-trade flat $500 account with zero fees/commitments and no pending decision, hold, forward basis or events qualifies. A separate actual portfolio activation clock prevents retrospective decisions. An immutable old-state witness, hash and zero-funding migration record are saved in the same transaction. Migration cannot archive/restart an account, add $500, erase losses or adopt historical positions. All retained legacy and portfolio accounts participate in the same archive/restart risk guard.

## Paper trading and daily results

The existing local 6 AM America/Chicago workflow reads `data/workspace/market-data/options/current.json`. No paid subscription or qualifying current feed was connected. Current recorded bid/ask timestamps, real quoted size, actual availability and the latest completed session are required. The first qualifying dataset establishes warmup without historical trades; later updates advance one unseen session with unchanged accepted history. Missed sessions, revised history, stale data and changed authority epochs hold advancement. No inbox means waiting, unchanged cash and **no forward trading result**, rather than fictitious paper fills.

All three strategies use that single forward account. UI and daily JSON/Markdown briefs show dated equity/P&L, external funding, shared commitments, remaining risk, cap assurance, open/pending positions, holds and per-strategy attribution. Historical comparisons show each ledger's individual processed coverage and qualification gate. The compact summary/poster continue to identify stale, held or absent forward outcomes. Existing completed briefs are immutable; the next due brief uses the expanded snapshot. No new OS task, Codex automation, microphone change or model request is added.

## Owner API and offline verification

New owner-only POST routes: `/api/options/register-archive` (`path`), `/api/options/compare`, `/api/options/portfolio` and `/api/options/upgrade-portfolio` (`acknowledge_simulated: true`). All require unique bounded `request_id` and exact `expected_revision`; no caller-selected strategy parameters or funding fields. Existing 0.22 routes remain compatible. Same-origin/Host/auth checks, global/local/briefing permissions, serialized ownership, immutable completed receipts and substantive-failure holds apply. Interrupted operations are never automatically replayed.

The engine/archive/workflow/UI tests cover exact shared caps across all three strategies, no profit recycling, delayed fills, puts/calls, expiry/month disposal, source gaps, chunk changes, same-ID no-funding migration, witness tampering, authorization and revocation, evidence preservation, old-account compatibility and daily brief attribution. Fresh-folder qualification uses copied source and the actual accepted archive with network/model/worker calls blocked. It is a process-level offline test; it does not claim a physical adapter outage or current-market fills. Private backups include catalog/migration SQLite rows and source/report files.

Recurring operation uses no new API/service/license and no model inference. Archive storage is measured above; the first isolated registration plus comparison took about 61.4 seconds on this PC. That is a one-off research operation, not a response-latency promise. The daily check reads only the local forward inbox and saved evidence; no historical study or repository download runs automatically.
