# VIX data access — 30 October 2026 follow-up

Robinhood offers an official authenticated route worth qualifying with the owner's existing account. Public free no-account VIX option quotes remain unqualified in the sources checked. No VIX paper fill or option performance result has been produced from this review.

## Existing Robinhood account

[Robinhood's official tool list](https://robinhood.com/us/en/support/articles/trading-with-your-agent/) includes `get_option_chains`, `get_option_instruments`, `get_option_quotes`, `get_option_historicals` and index-data tools for external agents. Its [index-options documentation](https://robinhood.com/us/en/support/articles/index-options/) includes VIX. This corrects treating Robinhood programmatic access as crypto-only. The published descriptions do not establish the actual VIX quote response's field coverage.

The [official connection instructions](https://robinhood.com/us/en/support/articles/onboarding-an-external-agent/) give `https://agent.robinhood.com/mcp/trading`. A bounded unauthenticated initialize request returned **HTTP401**, and the linked public protected-resource metadata advertised only `internal` scope. That probe sent no account information and made no market-data or order calls. It does not establish a public feed or a read-only OAuth grant.

During this review no Robinhood tool was exposed in the Codex tool inventory, the plugin directory returned no Robinhood entry, and Chrome showed a blank Robinhood login form. Existing permission to inspect the owner's account remains valid; an authenticated session/connection was absent from the available surfaces at that observation. These are dated observations, not ongoing account monitoring.

Sign in directly at Robinhood when inspecting an existing session. A website login does not automatically authenticate Ariadne or Codex's MCP connection. Do not copy browser credentials, cookies or session tokens into code, quote files, configuration or chats. Onboarding can require a dedicated MCP brokerage account and broader access to financial information. The owner must perform any account opening or approve a new access grant; this release performs neither. Robinhood's external MCP also exposes real-order tools, so a future Ariadne adapter must independently restrict itself to reviewed market-data tools. The server's general OAuth scope is not a read-only guarantee.

Robinhood's [billing explanation](https://robinhood.com/us/en/support/articles/token-usage-billing/) distinguishes hosted-agent token billing from external agents. This does not establish a market-data SLA, permanent free entitlement or zero cost for the external agent's own compute. This update adds no recurring API/model calls or subscription.

## Yahoo Finance and TradingView

| Route | Finding | Consequence |
|---|---|---|
| [Yahoo VIX chain](https://finance.yahoo.com/quote/%5EVIX/options/) | No usable VIX contract set with side sizes and quote clocks established. [Yahoo terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) restrict automated collection without permission. | No hidden endpoint, cookie extraction or scraper added. |
| [TradingView VIX chain](https://www.tradingview.com/symbols/CBOE-VIX/options-chain/) | Public chain viewing is available. [Official MCP](https://www.tradingview.com/mcp/docs) requires an eligible paid plan and OAuth; its published tools lack an options-chain/BBO tool. [Policies](https://www.tradingview.com/policies/) restrict unlicensed extraction/non-display use. | Not a qualifying free no-account recurring feed. |

The complete earlier candidate scan remains in [VIX research documentation](VIX_0_25.md) and packaged `source-assessment.json`. Public availability alone does not establish API access, quote quality or redistribution rights.

## Gates before VIX paper execution

1. Verify a permitted authenticated data connection using only reviewed read tools.
2. Inspect actual VIX/VIXW instruments and bid/ask responses, including true quoted side sizes and source timestamps. Option OHLC bars or last-trade prices alone are insufficient.
3. Supply actual individual VX references with exactly matching expiry and acceptable clocks. The documented Robinhood index tools do not establish a futures-data source. Spot VIX is not a substitute under the frozen rule.
4. Verify official contract calendar, last-trade time and European cash/VRO terms.
5. Qualify an atomic shared-ledger migration. The observed AAPL account cannot be pristine-retargeted, and creating another $500 VIX account would duplicate the allowance.

All gates remain **unverified**. Packaged findings cannot clear one, authenticate a connection or grant execution authority. The frozen signal rules and actual paper account are unchanged. The sole $500 monthly allowance still covers premium plus entry and reserved exit fees, without profit recycling. No new funded account, financial order path, credential store, dependency or network collector is added.

The options UI shows fixed official links and these gates without polling external services. Future daily brief JSON/Markdown and its bounded summary include the saved route status and review time. Existing completed briefs remain immutable. Saved research remains usable offline; fresh quotes require a permitted source connection. No raw account or licensed quote data is included in GitHub/source exports.
