# Offline market research · 0.15

The `market_research` tool audits a local OHLCV export and runs a fixed intraday SMA study. It requires no Internet, API key, provider package, language model or always-running research service. OpenBB's historical-price schema is an input-format reference; OpenBB, Qlib and LEAN are **not installed or invoked** by this feature. Their provider entitlements and runtime/license obligations remain separate.

## Run it

Place a single-instrument UTF-8 CSV and a JSON assumptions file inside Ariadne's workspace, then use:

```json
{"action":"study","path":"research/bars.csv","metadata_path":"research/assumptions.json","request_id":"study-001"}
```

Choose `audit` to inspect data quality without simulation. `config/examples/market-demo.csv` and `market-demo.json` are deliberately short, **synthetic demonstration inputs**, not quotes, forecasts or a recommended strategy. Copy them to the workspace explicitly to try the workflow. The eight-minute sessions demonstrate the contract; real studies must declare the actual session and calendar of the licensed input.

The tool writes a verified JSON artifact at `reports/market-research/<request_id>.json`. It includes exact input hashes, all declared assumptions, timezone-record identity, audit results, selected parameters, folds, simulated fills, cash/equity series and reserved-holdout comparisons. The return value supplies a small summary and the artifact's SHA-256. Reusing an ID returns only a byte-identical deterministic result; changed input, changed implementation output or modified prior results cause a conflict. Existing artifacts are never overwritten.

## Input contract

CSV columns are `timestamp` **or** `date`, plus `open,high,low,close,volume`. Optional columns are `symbol,adj_close,vwap,split_ratio,dividend`; optional adjustment columns are disclosed as ignored and never substitute for owner-declared provenance. Export one symbol. Intraday timestamps require an explicit UTC offset or `Z` and represent the **bar open**. Date-only rows are accepted for daily audits; daily data is not passed to this intraday strategy. Numbers use bounded nonnegative decimal notation, without exponent notation or NaN; OHLC prices must be positive.

JSON must declare `symbol`, three-letter `currency`, `source`, `adjustment` (`raw`, `split_adjusted` or `total_return_adjusted`), `timeframe_minutes`, `timezone`, `session_open`, `session_close`, and `weekdays` (Monday=0). Supported offline clocks are **America/New_York, America/Chicago and UTC**. Sessions must start and end on the same local day and divide into whole bars. Overnight markets are not silently approximated.

An optional `expected_sessions` list explicitly declares ISO dates. Without it the audit checks complete bar grids only for observed sessions and states that entirely missing sessions and exchange holidays are unknown. A listed session with no bars is reported missing. Duplicates, out-of-order rows, missing bars, symbol mismatches, malformed OHLCV, bars outside the declared grid and ambiguous repeated DST slots are reported; the tool does not sort, fill gaps, infer corporate actions or invent calendar coverage.

A `study` object supplies every cost and split: decimal-string `initial_cash`, integer `shares`, decimal-string `commission_per_order`, `fee_bps`, full `spread_bps`, `slippage_bps`, integer `train_sessions`, `test_sessions`, `holdout_sessions`, and one to six `parameter_grid` objects with `fast` and `slow` SMA lengths. There are no invisible zero-fee defaults. All study input must pass its audit. Total-return-adjusted prices are rejected for simulated execution; split-adjusted data remains a declared synthetic-price assumption requiring owner review. Each bar must report at least ten times the fixed share quantity; this simple gate does not establish real liquidity.

## Evaluation and accounting

The strategy is long or flat, with fixed whole shares, no shorting or leverage. A signal uses completed closes only; an entry/exit can fill at the next bar's open within the same session. No pending order crosses a session or evaluation boundary. Positions flatten at the preplanned final session bar's close. The report distinguishes the fill clock and records signal time, reference price, adverse fill price, fee, notional and cash. Historical close fills are a declared simplifying assumption, not an exchange auction model.

Half the declared spread plus all declared slippage moves each fill adversely. Fixed commission must be an exact two-decimal currency amount; commission plus notional fee is rounded half-up to two currency decimals. Prices, notionals and cash retain Decimal precision. An entry reserves the eventual fixed exit commission; unaffordable entries are skipped. Close-marked drawdown, exposure, costs, fill counts and ending-flat reconciliation are reported. Intrabar drawdown, partial fills, queue position, market impact, latency, halts and exchange-specific tick/lot rules are outside this model.

Walk-forward folds expand training only into earlier complete sessions. Each fold selects the SMA pair with the highest training ending cash under the same costs and initial capital; ties choose sorted `(fast,slow)`. Test outcomes never select that fold's pair. Test capital chains between folds. The final whole holdout sessions are excluded from every selection. One pair is fixed from pre-holdout training, then assessed against scheduled session-open-to-close and cash baselines on the holdout. Prior training closes may warm an indicator; holdout positions start flat. Repeated owner experiments can contaminate a holdout and are not prevented by this tool.

This is historical research, not a forecast, personalized investment recommendation or evidence of future profit. There is no broker interface, live/paper account, order route or automatic trading permission.

## Bounds, offline assets and sources

Inputs are limited to 2 MB / 5,000 bars, metadata to 50 KB, grids to six pairs, walk-forward evaluation to twenty folds and reports to 4 MB. The implementation uses the existing runtime and guarded atomic workspace files; it adds no resident engine. Cancellation is checked during bounded parsing/simulation and before publication. No measured energy or monetary saving is claimed.

New York uses a pinned 1,744-byte TZif extracted from the official Python `tzdata` **2026.5 / IANA 2026e** wheel, with its exact licenses and distribution/member hashes in `ariadne/resources/market/provenance.json`. Chicago reuses Ariadne's pinned existing IANA 2026f record. POSIX future rules in these records apply until the records are deliberately updated; future legislative timezone changes cannot be learned offline. Unsupported zones fail explicitly. No Python package is installed from the wheel.

Primary references checked 29 October 2026: [OpenBB historical OHLCV reference](https://docs.openbb.co/odp/python/reference/equity/price/historical/), [OpenBB EquityHistorical schema](https://docs.openbb.co/odp/v4/python/data_models/EquityHistorical), [LEAN fill-model documentation](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/trade-fills/supported-models/equity-model), [Python tzdata 2026.5 distribution metadata](https://pypi.org/pypi/tzdata/2026.5/json), [IANA release archive](https://data.iana.org/time-zones/releases/). Ariadne's explicitly described fill template is its own bounded implementation, not LEAN's fill model or a claim of upstream engine equivalence.
