# QQQ recovery research and forward paper account

Version 0.21.0 connects a fixed recovery hypothesis to public daily market data, a deterministic backtest, a separate forward paper ledger and the existing daily brief. Open **All systems → Venture Lab → QQQ recovery lab**. Nothing in this feature connects to a brokerage, creates a real order, uses leverage or spends money.

## The first result is unfavorable

The initial retained source snapshot contains 6,954 paired QQQ/SPY sessions from March 10, 1999 through October 29, 2026. October 30's unfinished bar was excluded. Evaluation after warmup begins July 7, 1999. At five basis points per transaction leg, the fixed rule's simulated $10,000 ended near **$8,694**, approximately **−0.51% annualized**, with **72.89% maximum close-to-close drawdown** and 206 transaction legs. The same-calendar QQQ buy-and-hold benchmark ended near $127,271, approximately 9.76% annualized, with 82.96% drawdown.

These are vendor-data research results, not independently reconciled issuer total returns or executable fills. Both paths retain distributions as cash instead of automatically reinvesting them. The unfavorable result remains visible as `not_supported_by_this_backtest`; paper tracking is an experiment, not a recommendation to fund the rule. Thresholds were fixed before inspecting this study's outcome and were not searched for a winning combination. Retrospective historical slices do not become prospective validation merely by being named a holdout.

## Fixed rule

- Universe: QQQ or cash, with SPY used only as broad-market confirmation. Fully funded fractional simulated shares; no shorts, options or leverage.
- Setup: QQQ suffered at least an 8% closing drawdown from its prior 63-session closing high, with the setup occurring in the current/prior 19 completed sessions.
- Entry decision: QQQ closes strictly above the highest high of its prior five sessions, and SPY closes strictly above its 20-session moving average, including that completed close.
- Exit decision: QQQ closes below the lowest low of its prior ten sessions, or the position has held 20 completed sessions.
- Fill assumption: the next eligible regular-session open. A forward decision must be durably recorded before that open, with a 60-second safety margin. It cannot borrow an earlier open after seeing the completed bar.
- Costs: five basis points per buy/sell leg by default, with fixed 5/10/25/50-basis-point stress paths in the historical report. No cash interest or taxes are modeled. Expenses already reflected in market prices are not subtracted again.

This has no intraday protective stop. A close-based exit can gap substantially before its next-open fill. The rule hash binds the complete rule and forward timing contract; changing it requires separately reviewed evidence rather than silently continuing an old paper account.

## Data and offline operation

The fixed collector requests only QQQ and SPY from Yahoo Finance's public chart endpoint. It is an unofficial, revocable source used for private personal research; commercial redistribution or exchange-grade accuracy is not asserted. It needs no API key, paid service, yfinance installation or new dependency. An unavailable feed is reported rather than bypassed through login, cookies, challenges or arbitrary fallback URLs.

Only the fixed online child gets networking enabled. The main offline socket policy stays in place. Collection requires the master Internet switch, QQQ controls and briefing controls to permit it, with the global autonomy pause clear. Permission revisions are checked during collection and held stable through final publication. Each GET uses the existing public-DNS/TLS protections, fixed source URLs, no redirects, byte bounds and a shared deadline.

Raw OHLC is split-adjusted but not dividend-adjusted. The ledger separately credits the supplied split-adjusted cash dividend to carried-in holders on the ex-date, before opening fills. A buyer at that open does not receive the dividend. Adjusted close is retained as diagnostic source data and never used to multiply ledger returns or price fills. Crediting dividends on ex-date is a modeling convention; it is not the actual cash payment date.

Structural checks cover dates, finite prices, OHLC relationships, volume, actions, paired calendars, duplicates, missing sessions and current unfinished bars. The bundled US equity calendar covers 1999–2035, known special closures and early closes; unexpected future exchange closures require review. Finalized bars need the actual session close plus a 15-minute availability allowance. Source hashes establish byte identity, not independent financial correctness.

Active normalized history is bounded to 8 MiB. Accepted economic history cannot silently change; adjusted-close-only rescaling is ignored for that comparison. Changed OHLC, volumes or corporate actions create a hold. A future split also requires a reviewed basis transition. The latest normalized snapshot, compact source hashes, account states and append-only events are retained. Raw daily transport responses are scratch, removed after consumption or failure; original HTTP bytes are not archived for every daily check. Historical studies retain their exact accepted normalized input and report.

The first source probe measured approximately 1.50 MB of uncompressed JSON across the two responses. At one refresh per day, 30 similar responses would total roughly 45 MB before protocol overhead/compression; source growth and actual transfer sizes can differ. The default cadence is two GETs once per local day, with at most four explicit refresh attempts per day. There are zero model calls in collection, backtesting, paper advancement and daily brief rendering.

Without connectivity, prior reports and paper state remain readable. No new market price or paper fill is invented. A stale/unavailable update retains its dated cache. Substantive validation, permission, history or publication failures stop advancement for inspection; uncertain work is never automatically replayed.

## Automatic daily operation and the brief

When enabled, the existing briefing host and native wake path check the QQQ workflow at **6 AM America/Chicago**. They share a cross-process lock and durable daily reservation, so the host, native task and manual request cannot run the same daily update twice. The feature adds no OS task or separate recurring model mission. Existing explicit briefing/global pauses remain effective.

At 6 AM, the latest available completed session is normally the previous trading day. A close-based decision becomes a pending simulated order for a future eligible open. Its assumed fill is recorded only after the following session's completed bar becomes available. This is delayed end-of-day paper accounting, not live broker execution at the opening bell.

Each new processed session appends an explicit daily result: session date, ending equity, cash/shares, daily and cumulative P&L/return, fees and modeled distributions. The brief's JSON/Markdown retain paper state, events, freshness, holds and the historical evidence gate. The one-page poster includes a compact paper result/status; the model-facing verified briefing summary labels it as saved simulated evidence. A previously completed brief is immutable and is not rewritten to add newly activated paper trading. New results appear in the next newly generated brief.

## Paper start, pauses and recovery

The initial account contains **$10,000 of simulated cash**, separate from real capital and systems operating budgets. It begins at the actual activation time with zero shares, zero trades and no historical target adopted. Historical bars provide indicator warmup only. A no-trade result is valid; starting the workflow does not force a purchase.

Only one unseen completed session can advance at a time. Missed sessions, late decisions, source-basis changes or a permission epoch change hold the account rather than fabricate an unattended fill. Changing global pause, Internet permission, QQQ controls or brief permissions can invalidate the old forward execution context. Inspect the saved receipt and acknowledge it explicitly; acknowledgement adopts nothing.

**Archive experiment** closes the current simulated experiment while retaining its state, ledger and evidence. It does not fabricate a sell or realized liquidation. The optional **archive accepted data basis** first saves and verifies the old accepted data, then clears the active cache so a reviewed corrected basis can be collected. To recover, acknowledge the hold, archive the affected experiment/basis as appropriate, refresh valid data, run a new historical backtest if its economic basis changed, and explicitly start a new cash experiment. Old accounts and study outcomes remain retained.

Storage caps: 10,000 compact request receipts / 32 MiB receipt bytes, 30,000 paper events, 32 paper experiments, and 64 MiB combined historical-study/archive reservations. Failed or interrupted publication reservations remain charged. Limits can be reached earlier than counts suggest. No automatic deletion of financial history is implemented.

## Owner API and authority

All endpoints retain bearer authentication, same-origin/host checks and strict request JSON. Writes use an expected workspace revision; operational actions also use a unique request ID. Stale revisions conflict. Completed requests return their original receipt; failed or uncertain requests cannot be replayed.

- `GET /api/qqq`: passive status, dated data, fixed rule, historical result, current paper state/events and holds.
- `PUT /api/qqq/config`: `enabled`, `paused`, `expected_revision`.
- `POST /api/qqq/refresh` or `/backtest`: `request_id`, `expected_revision`.
- `POST /api/qqq/paper`: those fields plus `replace` and `acknowledge_simulated:true`; replacement is explicit and retains the earlier account.
- `POST /api/qqq/acknowledge`: the held request ID and current expected revision.
- `POST /api/qqq/archive`: request ID/revision, a typed `reason`, and explicit `archive_data` boolean. Archive is available during a local QQQ pause; the global pause still blocks it.

There is no real-order route and no model-tool mutation route. Agents can read verified paper evidence from new briefs and workspace reports; they cannot turn a research signal into brokerage authority. Private market data and paper ledgers stay excluded from GitHub/source distribution. Canonical private backups include the main-database tables and published workspace evidence.
