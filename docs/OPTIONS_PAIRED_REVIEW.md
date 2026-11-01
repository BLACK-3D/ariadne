# Recovery-call evidence and forward paper operation

The research objective is supported net profitability after costs, without a fixed monthly income goal. The current paper controls remain one shared $500 gross monthly premium-and-fee commitment, $100 all-in per entry, and one fully paid long call or put at a time. A stock must have an actual unadjusted price below $100 at both the entry decision and modeled fill. Profits do not replenish commitments. Reference capital is not authority to add funding or raise those limits.

## Compare existing evidence fairly

`scripts/options_paired_review.py` reviews the completed multi-stock report's exact `all-results.csv`, bound to its completed publication receipt. It does not rerun backtests. Each same-portfolio comparison uses a common full source window and liquidity profile. Individual-stock cases, pooled portfolios, periods, presets and profiles stay separate.

Every fixed candidate and the cash control must be present in each cohort. Duplicate, truncated, malformed or inconsistent cohorts fail instead of silently dropping rows. Held exits, open positions and incomplete directional context stay visible as censored cases; their last marks never become completed returns. Net-P/L differences and medians use only paired complete, flat observations. Complete no-trade observations are included and separately counted; comparisons in which both legs traded are reported separately. These are descriptive observations with shared sources and dependent trades, not independent win probabilities or a global strategy ranking.

The CSV alone cannot independently verify individual fills, raw stock prices, contract deliverables, funding, source availability or an execution edge. Its exact digest and publisher receipt bind the derivative to previously reviewed evidence; they do not authenticate the publisher's original market data. Both unknown-size price proxies and assumed historical quote clocks remain explicit. No comparison can promote a historical winner into the owner account.

Run from the box root with the bundled Python. Supply the actual two digests from your completed report, and reserve a new output name for every attempt:

```powershell
runtime\python\python.exe -I -B scripts\options_paired_review.py --input data/workspace/reports/REPORT/all-results.csv --input-sha256 EXACT_CSV_SHA256 --publication data/workspace/reports/REPORT/publication.json --publication-sha256 EXACT_PUBLICATION_SHA256 --output staging/paired-review-NEW
```

The command publishes registration, JSON evidence, standalone escaped HTML and a completed receipt. It pins and rehashes the input files and implementation, rechecks the global pause epoch through an existing read-only database connection, and verifies every output before completion. An existing, failed or interrupted output cannot be reused. The bounds are 48 MiB of CSV, 150,000 rows, three MiB of aggregate output and 180 seconds. The process rejects network and native-process actions before parsing inputs. These are application guards, not an OS isolation claim. Detached research is only available in an isolated box without an owner database.

## Longer-history source findings

The provider's [FirstRateData options sample](https://firstratedata.com/b/49/historical-options-data) is publicly offered without an account. A bounded inspection of the advertised ZIP's directory, README and Ford member found 662 F quotes on just **July 3, 2023**. Bid/ask prices are present; side sizes and matching raw stock prices are absent from its 16-column schema. The [dataset card](https://firstratedata.com/ai/datasets/us-options-eod) describes the commercial archive, not a promise that the free sample spans the archive. The one-date sample does not extend a historical backtest. Evaluation and derivative work must follow the [provider license](https://firstratedata.com/about/license); raw downloads are excluded from Git and source packages.

The [OptionsDX FAQ](https://www.optionsdx.com/faq/) requires checkout and an account even for free products. Optionistics historical downloads redirect to sign-in. A bounded request for Cboe's offered sample timed out and remains a failed observation, without a retry or inferred free-history coverage. No new source in this finite review qualified for at least 65 actual option-quote sessions, an eligible below-$100 underlying and the required provenance. This is a bounded finding, not proof that no such source exists anywhere.

The accepted six-month KO source contains 127 sessions but is already examined development data. All twelve fixed conventional, RSI and volatility candidates have completed results across the retained baseline and follow-up campaigns. Repeating those same cases would not create an untouched test. Their completed receipts and any failed or uncertain attempts remain preserved.

## Forward paper account and daily results

The original schema3 F date-only EOD profile rotates the registered recovery-call, breakout-call and breakdown-put hypotheses on one shared ledger. Version0.30 also supports an explicit in-place schema4 stress-put policy; see [the forward policy](OPTIONS_STRESS_0_30.md) for its actual publication controls and [the stress evidence focus](OPTIONS_STRESS_REVIEW.md) for paired comparisons. Both remain experiments rather than proven winners. Their rule, activation lineage, fees and original funding cannot be replaced by editing a strategy list or adopting historical fills.

The existing collector checks the previous completed source session after 9:35 AM Eastern on an eligible market day. It makes no collection on weekends; publication is not guaranteed. A later, actually received source session is required to fill a durable decision. Missing mandatory quotes or failed/uncertain collection receipts stop progress without invented exits or automatic retries. The daily brief reports saved results, freshness, fees, commitments and holds. Current chains are partial and date-only, so fills remain explicitly experimental price proxies with no brokerage or execution qualification.

Research tools need no paid data service, subscription, model inference, extra resident process or broker. Actual forward observations must accumulate before drawing conclusions about profitability. No fixed dollar income target, capital increase or historical isolated winner overrides these limits.
