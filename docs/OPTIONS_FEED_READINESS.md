# Timestamped options feed readiness

Checked November 3, 2026 against primary vendor/exchange documentation. This is completed source research, not a connected feed, quote capture, provider reliability test or paper-account migration.

**No checked source qualifies for all current requirements:** free, no account, permitted automated access, actual US stock/ETF option bid/ask and displayed sizes, and usable causal timestamps no more than five seconds old. This bounded finding is not a claim that a qualifying source can never exist. The matching machine-readable assessment is [options-feed-readiness-2026-11-03.json](../ariadne/resources/market/options-feed-readiness-2026-11-03.json); `qualified_source_ids` is empty.

## What the connection needs

The [strict book gate](OPTIONS_LIQUIDITY.md) requires actual quote-event and receipt clocks, a matching contract identity and standard deliverable evidence, actual sides and sizes, and the dated stock/contract activity used for entry admission. Volume or open interest alone cannot establish available size. A website timestamp can describe page refresh, retrieval, last trade or a batch snapshot; it cannot silently become the BBO event clock.

Delayed or historical observations remain valuable research evidence under their own registered assumptions. They are not current five-second books. Backdating a decision or replacing an old timestamp with retrieval time would conceal the availability gap. Modified indicative quotes likewise cannot replace the actual book requested here.

## Checked sources

| Source | Primary finding | Why it is not qualified |
|---|---|---|
| [MarketData unauthenticated demo](https://www.marketdata.app/docs/api/authentication/) | Historical AAPL option access is unlocked without a token. [Quote documentation](https://www.marketdata.app/docs/api/options/quotes/) lists bid/ask, sizes, volume, OI and an updated snapshot time. | The unlocked entitlement is historical, not a fresh feed. The snapshot timestamp alone does not establish each actual quote-event/publication clock. [Free/trial accounts](https://www.marketdata.app/docs/account/free-accounts/) are also at least 24 hours delayed. |
| [Tradier](https://docs.tradier.com/docs/market-data) | Realtime US equities/options is available to brokerage account holders; sandbox data is delayed 15 minutes. | [API access](https://docs.tradier.com/overview/endpoints) requires an account/token. The sandbox fails freshness. No response was qualified here. |
| [Alpaca option quotes](https://docs.alpaca.markets/us/reference/optionlatestquotes) | Its API distinguishes official OPRA from a free indicative feed whose quotes are modified. | Authentication is required; modified indicative quotes are not the requested actual option book. [Plan documentation](https://docs.alpaca.markets/us/docs/about-market-data-api) does not establish anonymous OPRA access. |
| [Cboe delayed dashboard](https://www.cboe.com/delayed_quotes/api/quote_table) | Public dashboard viewing exists, with a notice prohibiting automated quote-table extraction. | A public page does not grant a recurring feed entitlement; the data is delayed and automated extraction is prohibited. No dashboard or hidden-endpoint collector was attempted. |
| [ThetaData](https://thetadata.net/docs/Articles/Getting-Started/Subscriptions.html) | The documented free entitlement is historical EOD data, not realtime options quotes. | [Getting started](https://thetadata.net/docs/Articles/Getting-Started/Getting-Started.html) describes user-portal API keys or account credentials. Its documentation is inconsistent about terminal necessity and some free rate limits; neither is adopted as an integration guarantee. |
| [Nasdaq Cloud Data Service chain specification](https://github.com/Nasdaq/NasdaqCloudDataService-REST-API/blob/main/restapi/chain.md) | Documents bid/ask prices and sizes, volume and OI, with realtime/delayed route choices. | The request requires a Bearer token. Listed chain fields alone do not establish all event/publication clocks. |
| [TradingView official MCP](https://www.tradingview.com/mcp/docs) | Official access uses OAuth and an eligible paid plan. | Fails both account/cost conditions; its published tools do not establish the required option BBO response. |
| [OnClickMedia historical API](https://www.onclickmedia.com/About) | The API advertises free no-account historical access; [pricing](https://www.onclickmedia.com/pricing) places historical EOD options in the free tier. | Historical source dates are not actual fresh event clocks. Live options appear under custom offerings. Upstream aggregation, shared capacity and the educational-use disclaimer do not establish a realtime SLA. |
| [Robinhood official agent tools](https://robinhood.com/us/en/support/articles/trading-with-your-agent/) | The official tool list includes realtime option/equity quotes, chains, instruments and option historical bars. | [Authenticated onboarding](https://robinhood.com/us/en/support/articles/onboarding-an-external-agent/) requires an eligible account and exposes account/real-order capabilities. This is not a no-account source. Actual response coverage and a narrowly scoped read adapter remain unqualified. |

No quote API was called, account opened, credential supplied, subscription purchased, collector installed or existing source cap changed for this review. Documentation can establish advertised access and semantics; it does not prove sustained availability, data completeness, latency or a service-level guarantee.

## Shared paper-ledger connection is separate work

The existing gate creates batch-scoped research proposals. It does not authenticate a provider, track funded inventory, maintain a durable cross-batch depth ledger or migrate an account. A passing imported book is not a confirmed broker fill.

A future qualified connection must:

1. Reuse the existing paper account and its shared cash. Add zero capital, preserve the no-top-up rule and retain the existing funding history; do not create a second funded account.
2. Bind the source identity, retained raw evidence, append-only observation prefix and actual availability clocks. Repeated source events must not replenish displayed depth across calls.
3. Preserve pending decisions, holdings, approvals, source caps, explicit pauses and failed, interrupted or uncertain receipts. Do not replay or repair uncertain financial work by changing request identifiers, tickers or clocks.
4. Independently qualify inventory, fees, entry/monthly limits, disposal behavior, liquidity consumption and any migration. Keep source admission distinct from strategy promotion and funding authorization.
5. Keep entry admission inactive when no source qualifies. Do not relax the gate, invent depth or adopt historical positions merely to produce a paper trade.

This document does not implement that connection or authorize activation. Existing [paper capital contracts](OPTIONS_BANKROLL_0_33.md), [research windows](TRADING_STUDY_WINDOW.md), rule hashes, ledgers and holds remain unchanged. A separately accepted account-bearing or paid data route would need its own access review and response qualification; none is inferred from this research.

## Offline operation and cost

This saved assessment can be read offline. It introduces no model calls, new dependency, resident worker, paid service or recurring network polling. Fresh market observations require a permitted source. Compute/energy costs and future maintenance are unmeasured; no operating-cost saving is claimed.
