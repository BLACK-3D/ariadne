# Offline options research follow-up

This follow-up separates better source coverage from a better strategy. It adds a local importer for HistoricalData.net's offered July–December 2022 KO stock/options samples, and three fixed daily entry hypotheses. No data account, purchase, model or resident service is required. Downloaded data and private results stay outside Git and distributable source packages.

## Source and interpretation

The [official samples](https://historicaldata.net/samples.html) include 127 daily option files and matching KO stock history. The [license](https://historicaldata.net/terms.html#data-license) permits evaluation of free samples and restricts redistribution. The importer reads data and noncode manifests; it does not execute the publisher's bundled verification programs.

The older sample lacks option quote-event timestamps and contract deliverable/multiplier fields. A normalized 100-share contract and a session-close observation followed by a 15-minute publication lag are explicit research assumptions. They are not authenticated historical availability or executable fills. The provider also documents preprocessing, including reported zero sizes represented as blanks. Unknown sizes remain unknown. Results remain execution-unqualified even when supplied side sizes pass the strict profile.

Only the fixed KO window is selected, with raw stock closes strictly below $100 and option expiry 0–35 calendar days after each source date. All 127 source sessions and original-file checksums must qualify. Malformed, crossed, above-bound or missing-price records are never repaired. Rejected records and source selection counts remain visible; a source session without usable canonical quotes fails qualification. Corporate-action and standard-contract assumptions must remain explicit. Missing held-contract quotes still stop a study ledger.

## New fixed hypotheses

These exact rules were registered before examining the new KO sample outcomes. They are Ariadne adaptations of technical concepts, not reproductions of published winning option strategies.

- **Compression breakout call:** 41-close warmup; 20-close population Bollinger-style bands at two deviations. The bandwidth two sessions ago must be a 20-bandwidth minimum, that close must not exceed its upper band, the next two closes must exceed their own upper bands, and bandwidth must rise on the final session.
- **Trend pullback call:** 65-close warmup; close above a rising 60-close average, at least a 2% five-return decline before confirmation, and a close strictly above both preceding closes.
- **Downside shock put:** 23-close warmup; a negative prior return at least two deviations below its preceding 20-return mean, excluding the shock and confirmation sessions, followed by another negative return and a close below the 20-close average.

Indicators use bounded exact rational arithmetic from validated decimal closes. Existing signals retain their prior 15-close inputs. Monetary debits and credits keep their existing conservative cent rounding and 40-digit Decimal context.

The new `followup_ensemble` rotates among only those three signals. It holds at most one fully paid long call or put and shares one $500 monthly gross commitment allowance. Each standalone remains a separate counterfactual comparison. Profits do not replenish the allowance; the $100 entry ceiling includes premium, entry fee and reserved exit fee. No funding, owner account, fill timing, liquidity gate, disposal rule or profit target changes.

## Reproducible campaign

Download the two ZIPs linked on the provider's sample page into private storage in Yikes, retain their acquisition times and SHA-256 digests, and import them without unpacking or running their bundled programs. The importer requires a new output directory and explicit acceptance of the standard-contract assumption:

```powershell
runtime\python\python.exe -I -B scripts\options_sample.py --options staging/source/options_sample_2022H2.zip --stocks staging/source/stocks_sample_2022H2.zip --options-sha256 EXACT_OPTIONS_ZIP_SHA256 --stocks-sha256 EXACT_STOCKS_ZIP_SHA256 --retrieved-at ACTUAL_UTC_RECEIPT_TIME --assume-standard-100 --output market-data/options/archives/ko-2022h2-historicaldata
```

The output is relative to Ariadne Workspace. A completed import retains the catalog, chunks, qualification receipt and rejected original rows. Inspect those records before using its catalog digest below. An existing or interrupted output is never overwritten.

The command's default remains the original eight-candidate comparison. A separate fixed preset chooses cash, the three conventional controls, the three new hypotheses and their combination, preserving the existing maximum of 16 candidate/profile ledgers:

```powershell
runtime\python\python.exe -I -B scripts\options_campaign.py --stage combined --preset volatility_followup --manifest market-data/options/archives/ko-2022h2-historicaldata/catalog.json --manifest-sha256 EXACT_MANIFEST_SHA256 --output staging/ko-followup-001
```

Use the exact manifest digest produced by a completed, verified local import. The command infers the actual research symbol from each manifest; it never relabels KO as QQQ. Existing paper engines retain their frozen universes and reject KO activation. All failed folders and held candidates are retained. The fixed rules do not run a parameter grid, search until a profit appears, carry unverified marks across a disposal deadline or adopt a historical winner automatically.

Read P/L excluding deposits alongside fees, completed trades, coverage, dated holds and cash control. Different symbols, periods, assumptions and counterfactual accounts must not be pooled into a single investment return. Prior QQQ years are previously examined development data. A new source/asset alone does not establish an execution-qualified holdout or future edge.

Primary concept sources: [Bollinger rules](https://www.bollingerbands.com/bollinger-band-rules), [AQR's original time-series momentum dataset](https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data), [OIC option-price behavior](https://www.optionseducation.org/referencelibrary/faq/option-price-behavior), and [backtest overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
