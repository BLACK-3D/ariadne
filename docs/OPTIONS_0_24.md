# Public AAPL EOD source and regime-filtered research

Version 0.24.0 connects a documented no-account source to the existing Options lab. It adds source evidence/quality controls and three separate research hypotheses. The frozen 0.23 paper engine, its rule SHA, $125 per-entry limit, one-position rule, shared $500 monthly non-recycling allowance, fees, future-observation requirement and disposal rules are unchanged. There are no real orders, broker connections, paid services, new model calls or new runtime dependencies.

## What the free source supplies

[MarketData's authentication documentation](https://www.marketdata.app/docs/api/authentication/) permits historical AAPL stock/options endpoints without a token. The documented public API, rather than an undocumented website endpoint, is used. Its [data-freshness contract](https://www.marketdata.app/docs/account/data-freshness/) says options EOD becomes historical after the **next exchange session opens**, not immediately after the previous close. Friday's data therefore rolls on Monday after the open. Its documentation explicitly treats HTTP203 cached JSON as successful data. Personal, nonprofessional documented-API automation and local storage are covered by the [provider terms](https://www.marketdata.app/terms/); data files remain private and outside Git/source packages.

This is a vendor EOD quote source, **not live NBBO or independently reconciled prices**. Anonymous access is revocable; no uptime SLA, unlimited quota or permanent free entitlement is established. One bounded request per completed source session is scheduled. Other tickers are not silently substituted or accessed with a key. The scan did not establish an equally qualifying no-account QQQ quote API. Cboe's delayed quote-table page expressly prohibits automated extraction; no scraper or screenshot workaround is connected. Alpaca's free indicative quotes are modified prices, not accepted as actual OPRA quotes.

Actual qualification used one documented public AAPL request. The source returned **990 rows**, **154,109 bytes**, observed **October29,2026 at20:00UTC**, actually received **October30 at19:18:53.736348UTC**, HTTP203. The accepted scope contains **176 quotes** and retains its one zero bid. Eight crossed quotes are recorded in quarantine. Source hash:
`b768e6772467fd62057cad23b06d4856fe7fc58e5126ed69894ff790e03090fe`

The original strict whole-chain qualification failed on these crosses. Its raw input and failure are retained separately. A new explicit normalization policy was reviewed before owner use; no quote was repaired and no failed owner work was replayed.

## Quote quality and timing

The adapter checks exact symbol/OCC terms, standard100 multiplier, exchange-local expiry, a common vendor EOD `updated` timestamp, contemporaneous underlying price, bounded exact decimals, counts and sizes. Request generation and last-trade times are not promoted to quote timestamps. Availability is actual receipt time. Unknown identities, mixed times, malformed counts, impossible economic bounds or inconsistent underlying observations reject the capture.

Only crossed **non-required** quotes are quarantined, with exact contract ID, bid, ask and reason. Raw source rows remain unchanged. They are unavailable for entries and never become canonical executable quotes. A crossed or missing **open/pending contract** makes the whole capture unavailable; no replacement contract, mark, sale or settlement is invented. All supplied zero quotes/sizes inside the fixed scope are retained. Scope is0–35 calendar DTE and±5%of contemporaneous spot, plus required open/pending contracts outside that strike band.

Raw source JSON, normalized daily snapshot and full provenance are separate hash-bound artifacts. Compact receipts display up to16 quarantined rows, the true count and the full provenance SHA; the complete list remains in the artifact. Failed normalization can retain bounded raw failure evidence without adopting quotes or trades. Network/transport failures have their own durable hold and are not retried automatically.

Normalization policy SHA:
`c02d7b4420991d2b89664fd6cd5188bd8b56468b23be430430b6a36b77071cd5`

## Same account, no new allowance

Explicit activation can retarget only a pristine, unobserved schema2 QQQ portfolio to AAPL **on the same ID**. It requires exactly$500 inherited cash/contributions/opening equity, zero fees/commitments/trades/cursor/history/milestones/strategy activity, empty accepted prefix, original strategy order, no pending/open position, no hold and no forward source basis. Only the retained original portfolio-upgrade event may precede activation.

The immutable pre-feed state and hash are retained. The deterministic transformation changes only the symbol and actual decision clock. Original start time, portfolio activation, legacy migration witness, month and funding stay unchanged. A zero-funding event and separate source activation record are committed atomically. Status verifies this witness chain. A pause, archive, source toggle or restart cannot replenish the allowance or manufacture a replacement feed account. Existing QQQ share research remains separate and unchanged.

## Automatic collection and daily brief

The existing local scheduler checks the source independently of whether today's6AM brief has already completed. On exchange sessions it collects at **9:35AM Eastern**, five minutes after documented source publication. A restarted host can collect later that session until five minutes before the reviewed close, leaving a buffer beyond the original>60-second future-observation requirement and the bounded child runtime. Early closes, weekends, holidays and US DST use the existing reviewed exchange calendar/clock.

The6AM America/Chicago brief displays the last saved, dated source and paper results. It does not pretend that yesterday's close is already available before the provider releases it. Market freshness remains false when appropriate; passive overdue disposal assurance remains suspended until qualifying evidence arrives. These labels are not suppressed to make the feed look current.

After explicit source activation, the old6AM local-inbox refresh is skipped, including while the source is paused. Importing its old prefix before publication would persist a stale-source hold. A new valid contiguous deadline-day snapshot supplied at the next open can satisfy the original precommitted disposal under the **unchanged engine**. An already persisted hold, missing contract, bad exit bid, further finalized-session gap or changed authority epoch remains a hard stop. No failure is cleared by a new source.

The first capture establishes indicators only, adopting no historical trades. The unchanged strategies require14 closes before a signal; zero starting P/L is not a trading result. Decisions are durable at actual receipt/decision time. A hypothetical fill must use a later source observation more than60seconds after that decision. Buy ask, sell bid, actual executable-side size and all existing fee/risk gates remain.

The offline main process never enables its network policy. A fixed child can access only the documented public API through reviewed DNS/public-IP/TLS guards. It uses no redirects, proxy, cookie, credential or arbitrary URL; accepts only200/203JSON; and makes one request without retry. Bounds are8MiB raw,12MiB child output,15seconds transport budget and35seconds parent runtime. Global/local/source/Internet/brief permissions and the original account pause epoch are checked during collection and every publication boundary. Disabling/resuming between polls cannot reuse an old permission epoch. A source failure holds its workflow without pausing unrelated regional reporting.

Retention remains bounded by the **existing**100 shared source/research/archive reservations and128MiB evidence limit, plus the32MiB/200,000-quote forward cache and existing receipt/account/event quotas. Failed reservations count. UI/brief show remaining evidence operations/bytes. Each successful source capture consumes a reservation, so fewer than100 additional sessions remain once studies are included. Collection stops for reviewed maintenance when full; no financial history is deleted automatically. The measured raw sample is154KB; storage for normalized evidence and accumulated history is additional. No unmeasured compute/energy saving is claimed.

## New hypotheses and actual results

The research-only rules were preregistered before examining new outcomes. They require51 closes and use reference windows excluding the current close:

| Hypothesis | Fixed signal |
| --- | --- |
| Regime recovery calls | Original recovery trigger plus current close above prior50-close SMA and prior20-close SMA above prior50-close SMA |
| Trend breakout calls | Current close above prior20-close high, above prior50-close SMA, and prior20-SMA above prior50-SMA |
| Trend breakdown puts | Current close below prior20-close low, below prior50-close SMA, and prior20-SMA below prior50-SMA |

The same execution/risk constraints isolate the signal change. [Time-series momentum research](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf) motivates an underlying trend hypothesis; diversified futures results do not prove profitable short-DTE option buying. [OIC price-behavior guidance](https://www.optionseducation.org/referencelibrary/faq/option-price-behavior) explains why volatility and time decay can defeat a correct direction. A distinct research schema/hash has no forward/account-migration API. The original paper rules and cash are never adopted from these counterfactuals.

Research rule SHA:
`91d1af60793d3df7149ad763d5243e18b8aa8255093e127cbdbc885333752661`

The previously unexamined full2024 window contains **252 sessions and702,096 supplied quotes**, in11 chunks totaling133,847,327bytes. Before option outcomes, its static strike interval was fixed at0.95×the minimum underlying close through1.05×the maximum underlying close for that year ($376.4660–$565.0785). This includes every possible eligible near-spot entry strike and retains subsequent in-window exit quotes when spot moves. All in-scope rows remain, including77,715 zero bids and2,046 zero asks. No missing sessions or in-scope term contradictions were repaired. This remains assumed-EOD/unverified mirror data; the source does not become prospective or independently authenticated.

| Window / ledger | Trading P/L excluding deposits | Closed / entries | Processed sessions | Outcome |
| --- | ---: | ---: | ---: | --- |
|2024 new shared research |−$104.60|1/2|121/252, throughJune25|Held, last dated mark|
|2024 original shared portfolio, same input |−$311.85|4/5|78/252, throughApril23|Held, last dated mark|
|2024 regime recovery calls |−$86.30|0/1|144/252, throughJuly29|Held, last dated mark|
|2024 trend breakout calls |−$104.60|1/2|121/252|Held, last dated mark|
|2024 trend breakdown puts |$0.00|0/0|252/252|No trades; not a winning return|
|2022 new shared research |−$120.60|1/2|236/251, throughDecember8|Known-year exploratory result; held|
|2022 original shared portfolio |−$162.60|1/2|37/251|Known-year exploratory result; held|

Different stop dates prevent a claim that the smaller marked loss is superior full-year performance. No completed profitable shared strategy, realized5×/10× result or prospective edge was established. The put hypothesis's zero-trade result is not success: affordability, DTE/expiry/strike gates and later eligibility cancellations can leave no fill. Detailed diagnostics report warmup, signals, rejected/unaffordable quotes, budget blocks and rotation skips. No parameter sweep, increased stake, paper-account restart or automatic winning-strategy selection follows these outcomes.

New studies and source results appear separately in the UI and future brief JSON/Markdown/compact views. Full reports retain rule/input hashes, all events, curves, source/processed coverage, gate, underlying evidence limitations and baseline. Existing completed reports remain immutable.

## VIX and supported instruments

The anonymous source qualifies AAPL. VIX is not relabeled as an equity or represented by a made-up quote feed. [Cboe's VIX specifications](https://www.cboe.com/tradable-products/vix/vix-options/specifications) describe different index-option trading and expiration hours; VIX contracts require a separately validated pricing/settlement and source model. The current importer rejects unsupported VIX contracts. No VIX trade or backtest is claimed in this release.

## Owner controls and qualification

New authenticated same-origin POST routes are `/api/options/feed` (`enabled`, `acknowledge_feed:true`), `/api/options/collect-feed` and `/api/options/research-compare`. Each requires an exact revision and unique request ID. Completed exact requests return saved evidence; failed/interrupted work is never replayed. No API accepts arbitrary provider/ticker, capital, broker credentials or strategy parameters. Offline mode keeps saved reports and backtests available and blocks online collection.

Tests cover quote normalization/hostile inputs, no-account fixed-host transport, crossed/required quote handling, raw failure preservation, witness/account preservation, shared caps, publication/clock boundaries, all authority revocations including pause/resume races, immutable artifacts, due-source independence from completed briefs, UI confirmation/auth races and research isolation. Fresh-copy qualification blocks networking/models while reproducing actual vendor normalization and the new historical study. It does not claim a physical network outage, provider uptime guarantee or real-market fills.
