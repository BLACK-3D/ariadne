# Recent options history: acquisition and comparison readiness

Checked November 1, 2026. **No new historical strategy comparison or candidate promotion completed.** The new source acquisition timed out before receiving its first stock body; no new options chains or simulated trades were created. A source failure is not a zero-trade backtest or a zero-return result.

The owner's rolling window currently admits observations from **November 1, 2025 through October 30, 2026**, the latest finalized session. Existing older crisis-era studies remain archived reference. Current saved KO quotes still cover only three partial dates, October 28–30. The 126 availability labels are not captured history.

## Sources investigated

| Source | Published scope and actual check | Disposition |
|---|---|---|
| [OnClick individual EOD API](https://www.onclickmedia.com/pricing) | Advertises anonymous one-year history. Previously captured real KO partial quotes contain supplied bid/ask sizes. The new fixed stock-history request timed out after one GET, with no HTTP status or complete response body. | This acquisition stopped. No retry, smaller substituted cohort, extra strikes, paid level or repaired source clocks. |
| [OnClick backtester service](https://www.onclickmedia.com/backtest/api-docs) | A distinct documented KO availability GET returned HTTP 502 at 18:21:37 UTC. | Stopped. No backtest POST or fallback. |
| [MarketData.app](https://www.marketdata.app/docs/api/authentication/) | Official anonymous historical demo covers AAPL. Its [chain schema](https://www.marketdata.app/docs/api/options/chain/) includes quotes and sizes. | No new eligible below-$100 root qualified; no account or subscription created. |
| [ThetaData](https://thetadata.net/docs/Articles/Getting-Started/Getting-Started.html) | Current authentication requires an account/API key; anonymous defaults are documented only by legacy v2. Current [terms](https://www.thetadata.net/terms-and-conditions) leave automated archival use ambiguous without an applicable agreement. | Unqualified lead. No terminal installed or anonymous login attempted. |
| [volrec](https://github.com/gabrielmitton-cloud/volrec) | Documentation describes a recent ATM-call/IV panel using an indicative feed. [Alpaca](https://docs.alpaca.markets/us/docs/historical-option-data) distinguishes indicative modified quotes from OPRA. | Not a matched put/call execution-history dataset. No data downloaded. |
| [Preserved ETF archive](https://github.com/lambdaclass/options_portfolio_backtester/blob/master/data/DATA_NOTICE.md) | Surviving notice retains SPY/IWM/QQQ, with uncertain original provenance. No qualifying recent single-stock mirror was established. | Older retained studies are outside this research window; no old-period rerun or vanished-CDN retry. |

This bounded search does not prove that no qualifying public dataset exists. It establishes that none of the inspected new leads supplied an admitted, complete recent comparison.

## Implemented tools

`scripts/options_recent_capture.py` performs one finite, predeclared KO experiment. It accepts no ticker, URL, date, credentials or retry override. It pins the existing master Internet and global pause permissions through read-only checks and preserves the current account untouched.

The fixed cohort is **26 sessions, September 11–October 16, 2026**, with **October 16 expiry** on every date, both call/put types and every returned strike. Fourteen September source sessions provide causal close warmup. Two exact stock windows respect the existing parser's fifteen-session ceiling. The plan uses 54 sequential GETs, bounded by 64 requests, 16 MiB wire, 24 MiB output, 180 seconds overall and ten seconds per GET. Missing metadata, stock grids, required quotes, invalid values, source errors or permission changes stop the whole attempt. There is no resumption of an interrupted output folder.

`ariadne/options_recent_history.py` independently normalizes a completed cohort. It preserves all original raw hashes, actual receipt strings, prices, zero sizes and partial-chain warnings. It rejects missing, reordered, out-of-window or invalid records; it does not repair sources or infer liquidity from volume/open interest.

Two profiles are registered before acquiring prices: hypothetical session-close-plus-fifteen-minute availability and hypothetical next-session 09:35 New York availability. Both remain **unverified research assumptions**, with actual source clocks retained separately. The second is a timing sensitivity, not an exact production execution replica. Neither normalizer nor historical ranking can place a paper order.

For a successful capture, the completion receipt explicitly identifies its research workspace. Both catalog paths and chunk paths are relative to that workspace. The unchanged `scripts/options_bankroll_research.py` comparator then evaluates stress-filtered puts, unfiltered puts, recovery-growth calls and cash with identical source dates, fees, integer sizing and held-exit rules. Incomplete ledgers cannot rank. Separate counterfactual capital must never be added or adopted into the owner account.

The actual attempt is retained privately under `staging/recent_options_2026_11_01/ko-september-october-v1/failed.json`. It attempted only its first stock GET; it did not reach normalization or backtesting. Do not rerun the same failed work under a new output name or automatically loosen its deadline. A future acquisition requires a substantively reviewed source/availability change or explicit owner direction; preserve this failure evidence.

## Paper and validation status

The current one-time **$25,000** paper bankroll and schema6 controls remain unchanged: no top-ups; one shared $25,000 monthly gross commitment ceiling; all-in entry at most min($1,000, 4% of equity, available cash, remaining commitment), with both fees and actual side-size checks. The existing KO forward hypothesis is put-only. Recovery calls require their own additive reviewed causal policy and a same-account zero-funding transition; changing a candidate label would violate the existing contract. No candidate was promoted from these unrun comparisons.

**94 targeted tests passed in a fresh source copy**, with zero failures, errors or skips. Four positive controls demonstrated that network, process, database and original-project access were blocked; prohibited attempts during the tests were zero. The receipt is `staging/recent_options_2026_11_01/fresh-source-v1/completed.json`. This validates code and stop behavior using generated fixtures and the original bundled Python; it is not a physical outage test, a full-model ZIP rebuild, a successful provider acquisition or a profitable strategy result.

No paid service, model, dependency or resident process was added. Source acquisition requires Internet; saved receipts, normalization, comparisons and reports remain usable offline. Recurring fees added are $0; energy/compute savings are unmeasured. Provider bodies, pairing tokens, owner data and private receipts are not published to GitHub.
