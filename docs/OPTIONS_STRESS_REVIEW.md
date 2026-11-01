# Stress-put evidence focus

The offline evidence reviewer can compare fixed stress-filtered puts directly with the existing raw puts, cash and call peers. It reads a completed report export; it does not fetch prices, rerun backtests, choose a ticker, fit parameters or activate a paper strategy.

| Preset | Fixed reference | Same-cohort peers |
|---|---|---|
| `conventional_regime` | `stress_breakdown_put` | Raw breakdown put, recovery/breakout calls and their growth filters, cash, fixed rotation |
| `volatility_regime` | `stress_shock_put` | Raw shock put, compression/pullback calls and their growth filters, cash, fixed rotation |

Use `--focus stress_put` on `scripts/options_paired_review.py`. The default `--focus recovery` keeps the existing recovery comparison, rule identity and HTML behavior. There is no arbitrary reference, rule or threshold input. Both fixed presets require all eight candidates and cash, including unsuccessful, censored and no-trade cases.

```powershell
runtime\python\python.exe -I -B scripts\options_paired_review.py --focus stress_put --input data/workspace/reports/REPORT/all-results.csv --input-sha256 EXACT_CSV_SHA256 --publication data/workspace/reports/REPORT/publication.json --publication-sha256 EXACT_PUBLICATION_SHA256 --output staging/stress-put-review-NEW
```

Every attempt needs a new output directory. The CLI binds the exact CSV to its completed publisher receipt, checks the full header and row count, pins the used source files and inputs, respects the existing global pause epoch through a read-only connection, and rehashes inputs and all earlier outputs before completion. Failed or interrupted evidence remains; it is never automatically retried. Detached mode is limited to isolated boxes without an owner database. Application network/process guards apply before input parsing.

## Reading the comparison

The difference is **peer net P/L minus the stress-put reference net P/L**. A negative difference means the stress-put observation was higher. Each pair must use the exact same portfolio membership, full source window, source-row count, preset, evidence kind, quote-clock basis and liquidity profile. Individual-stock and pooled cases stay separate, as do different periods and presets. Case identity mismatches, duplicates, incomplete candidate sets, malformed fees and risk inconsistencies fail closed.

Held exits, open/pending endpoints, partial coverage, incomplete benchmark context and insufficient source-row warmup remain in the status/censoring counts. They never receive an invented zero or completed net return. Accepted-prefix dates may differ because a hold legitimately stops one leg early. A complete no-trade case remains a zero outcome.

Each peer reports:

- The four-cell eligibility matrix and overlapping exclusion reasons.
- Net-difference distributions and higher/lower/tie counts for all eligible pairs.
- A separate both-traded subset, plus counts where only the reference, only the peer, or neither traded.
- Matched fee distributions and every candidate's gain/loss/held/incomplete/no-trade counts.

A 23-session volatility window may warm the shock-put component and fixed rotation while the 41/65-session call peers remain censored. The report states partial rotation warmup separately. Cash never contributes a both-traded observation. Many ties or comparisons against cash do not establish an active strategy's win probability.

## Evidence and risk boundaries

The row schema verifies descriptive consistency, including the reported $500 maximum monthly gross commitment, fee arithmetic and counts. The CSV lacks individual fill, raw spot, funding and archive/registry identities, so it cannot independently prove the $100 entry gate, historical point-in-time availability, contract deliverables or source authenticity. The caller's exact publisher/CSV hashes establish the identity of the derived evidence, not the correctness of the original feed.

No independent account profits or capital are added. Counts are dependent counterfactual observations, not independent trials, a global strategy ranking or a prospective return forecast. The new report cannot promote a historical winner or change the current forward paper account. Its $500 shared monthly gross allowance, $100 all-in entry ceiling, fees, single fully paid long-contract limit and no-profit-recycling rule stay unchanged. The forward policy and actual publication/preopen controls are documented separately in [OPTIONS_STRESS_0_30.md](OPTIONS_STRESS_0_30.md).

Limits remain 48 MiB input, 150,000 rows, one MiB pure comparison output, three MiB aggregate CLI output and 180 seconds. The report requires no model inference, paid service, extra resident process, broker or network fetch. This describes its operating dependencies; no unmeasured energy or runtime savings are claimed.

