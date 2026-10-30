# Aggressive options research and forward paper workflow

Version 0.22.0 adds **All systems → Venture Lab → Options lab**. It implements a fixed long-call research hypothesis, historical option bid/ask imports, deterministic backtests, a separate forward simulated account, and daily evidence in the existing 6 AM America/Chicago brief. There is no broker, real-order route, paid subscription, model call or new runtime dependency. Existing QQQ share research remains separate.

## The $500 monthly loss contract

The paper experiment starts with $500 simulated cash. Each observed later exchange calendar month contributes another $500 of simulated external funding after the previous month is verified flat. Deposits are recorded separately and never counted as trading profit. These are simulated ledger entries, not withdrawals from the owner's account.

Each entry is one fully paid standard 100-share long call. Premium, entry fee and reserved exit fee together must fit within $125 per entry and $500 for the entire month. The fee assumption is $0.65 per contract per side. Sale proceeds and profits do not replenish the month's committed allowance. The cap is net loss relative to a flat opening month's equity, excluding contributions; it is **not a $500 peak-to-trough drawdown guarantee**. A large unrealized gain can subsequently fall by more than $500. Modeled fills and fees are not actual brokerage risk controls.

Positions must be disposed of before expiration and before the month ends. The disposal date is precommitted at entry: the earliest of the preceding expiry session, the penultimate regular session of the entry month, and the tenth following regular session. There is no exercise, assignment, short leg, margin or invented expiry settlement. Missing or unexecutable disposal evidence holds the ledger, suspends cap assurance and blocks new funding/risk. Passive views derive overdue assurance from the current clock even when the inbox is absent.

Archival retains all marks, inputs and losses without fabricating a sale. It cannot reset the current month's allowance. A retained experiment that belongs to the same month blocks a replacement; unresolved archived exposure blocks a new experiment. Pausing, acknowledging or restarting does not erase committed risk. New installations begin disabled and without a paper account.

## Frozen experiment

The rule was registered before inspecting its historical results. Supported research symbols are QQQ, SPY and AAPL, with QQQ the forward default. Historical reports choose QQQ first, then SPY, then AAPL based solely on availability; symbols are not pooled or selected for winning results.

- After a minimum 14-session warmup, observe a close above the previous two daily observations, with a 3% closing decline from the preceding ten-observation high occurring in the current/prior four observations.
- Choose a call with 7–21 calendar days to expiry, expiry in the current month, and strike from spot to 5% above spot. Rank by distance from 14 DTE, then lowest strike and contract ID.
- Require positive uncrossed bid/ask and a spread no wider than 25% of midpoint. Forward fills also require actual quoted executable-side size of at least one contract. Missing historical size is disclosed as a price-only assumption.
- A decision can affect only a later regular-session snapshot observed more than 60 seconds after its durable decision time. Buy at the later ask; sell at the later bid. The contract is fixed at the decision; a changed price does not cause hindsight selection of a different contract.
- Observe an exit after the bid reaches five times the entry ask or falls to half the entry ask. Execution occurs at a later eligible quote, so gaps can defeat the target or stop. Mandatory disposal remains precommitted.
- Count realized 2×/5×/10× payouts using net exit cash divided by premium plus entry fee. A displayed bid or a 5× gross-price target is not a realized 5× net return.

Every report binds its rule SHA-256 and exact accepted input. Thresholds were not optimized against the first outcome. All results remain retrospective hypotheses, not prospective validation or a profitable execution qualification.

## Actual historical evidence and first result

The official Cboe [Option Quotes](https://datashop.cboe.com/option-quote-intervals) sample contains 499 AAPL quotes for September 21, 2023. It demonstrates the recorded-time CSV adapter; one session is insufficient strategy history. Cboe's full historical products are paid and were not purchased.

The exploratory QQQ input is the earliest chronological window in a public data mirror, **March 23–June 30, 2011**, with **70 sessions and 70,314 contract quotes**. The window was fixed before outcomes, retains all available calls, puts, strikes and expirations within it, and fits the bounded local import format. It is pinned to [mirror revision 37f6c456fe1a4775c875673fb8ef907d5cd2fd66](https://github.com/anahatsingh-ui/options-dataset-hist/tree/37f6c456fe1a4775c875673fb8ef907d5cd2fd66). Original price methodology and individual quote timestamps are undocumented. Its dates are explicitly **assumed EOD**, its authenticity/completeness is not independently reconciled, and it is ineligible for forward fills. Source notices are provenance, not a redistribution license for exchange data. Raw/normalized histories stay private and out of GitHub/source ZIPs.

The fixed rule made one hypothetical entry on May 20: QQQ110527C00059000 at an ask of $0.07, plus $0.65 entry fee. The mandatory May 26 disposal had no executable bid, so the study stopped with **`held_incomplete_evidence`**, zero completed exits and zero realized 2×/5×/10× payouts. The last dated mark through May 25 showed **−$7.65 trading P/L**, $1,500 external simulated contributions and $1,492.35 marked equity. That is **not a completed full-window return**, verified liquidation or evidence of a winning strategy. No sale or total-loss settlement was invented to finish the chart.

## Historical and forward data lanes

Historical imports and forward observations occupy separate caches. Importing or backtesting an old winning/losing dataset never advances a paper account, changes its cash or adopts historical positions. The first suitable current forward dataset establishes warmup only. Later updates advance exactly one unseen session; revised accepted history, missed sessions or uncertain permission epochs require review rather than retrospective fills.

The fixed forward inbox is `data/workspace/market-data/options/current.json`. Each permitted 6 AM brief checks it once per Chicago day. Another local tool or owner may place an approved canonical dataset there. There is **no connected options data subscription**. With no inbox, the brief reports waiting for quotes and retains the ledger. Old/assumed timestamps never become current quotes. A recorded current chain needs the latest completed exchange session, publication time and actual executable-side size. The public Cboe delayed chain was examined but not connected: generation/last-trade timestamps do not establish individual bid/ask quote times required by this contract.

Local JSON imports, Cboe CSV conversion, backtests and report reading work offline. No internet access or model request is needed for them. No offline system can observe new market conditions without a supplied feed/file. A source hash proves byte identity, not market accuracy. Delayed daily paper accounting does not execute at the exchange or monitor intraday stops.

## Import and conversion

Paths below are relative to `data/workspace`, never arbitrary absolute paths, URLs or other drives. Put a legally obtained source CSV/JSON inside that workspace. The UI imports canonical JSON; the bundled CLI validates it or converts the documented Cboe interval CSV. Conversion writes a new file and refuses overwrite, links, malformed quotes, future timestamps and synthetic owner data.

```powershell
runtime\python\python.exe -I -B scripts\options_market.py validate --input market-data/options/history.json
runtime\python\python.exe -I -B scripts\options_market.py convert-cboe --input market-data/options/source.csv --metadata market-data/options/source-metadata.json --output market-data/options/new-history.json
```

Canonical schema 1 contains `evidence_kind: historical_quotes`, `currency: USD`, an aware `as_of`, source provenance and ordered daily snapshots. Source metadata has exactly `provider`, `url`, `sha256`, `retrieved_at`, `usage`, `quote_time_basis` (`recorded` or `assumed_eod`) and `independently_verified: false`. For Cboe conversion the SHA-256 must match the actual CSV bytes. Snapshot fields are `observed_at`, `available_at`, `underlying_symbol`, `underlying_price` and `quotes`. Availability must be at least 15 minutes after observation and no later than actual retrieval; it does not prove that a historical file was accessible to the trader at that time.

Each quote has `contract_id`, `type`, `expiry`, `strike`, `multiplier`, `bid`, `ask`, `bid_size`, `ask_size`, `volume` and `open_interest`. Exact prices are decimal strings; counts are bounded integers or null. Standard multiplier 100 and matching OCC identity are required; adjusted contracts are rejected. Only one daily snapshot per symbol during the final 30 minutes of a reviewed session is supported by the strategy engine. CSV imports retain the last supported interval without combining intraday prices into a fictional simultaneous chain.

## Owner controls, retention and daily reports

Owner-only same-origin authenticated APIs expose `GET /api/options`, `PUT /api/options/config`, and POST `import`, `refresh`, `backtest`, `paper`, `acknowledge`, `archive`. Mutations require an expected revision; operational requests require a unique request ID. Completed exact requests return their original receipt; failed/interrupted work is held and never replayed automatically. Acknowledgment adopts no partial data or fills. The global pause, local workflow controls and brief permissions remain effective. Existing source/briefing holds and changed permission epochs stop advancement.

Limits: two 32 MiB active input caches, 200,000 quotes and 4,000 snapshots per dataset, 32 experiments, 30,000 events, 10,000 receipts / 32 MiB receipt bytes, and 100 report/archive reservations sharing 128 MiB. Failed publication reservations remain charged. No automatic deletion of financial history is implemented. Canonical private backups retain the prefixed SQLite tables and report evidence.

New briefs contain dated equity, cash, contributions, trading P/L, monthly P/L, committed/remaining allowance, cap assurance, positions, pending decisions, holds and the historical evidence gate. JSON and Markdown retain details; the one-page poster and compact model view label saved simulation evidence. Existing completed briefs remain immutable. This adds no OS task, Codex automation, real broker connection or general model mission.
