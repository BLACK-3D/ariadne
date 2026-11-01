# Stress-put evidence and paper-source readiness

Version 0.31 adds a stress-put focus to the offline evidence review and a passive source inventory in the Options panel and daily brief. It uses the existing local scheduler, runtime and paper account. No additional model, subscription, service, broker or funding is introduced.

## Compare stress puts on matching evidence

The protected paired-review CLI now accepts `--focus stress_put`. The default `recovery` focus retains its previous rule and output. The fixed stress references are `stress_breakdown_put` for the conventional preset and `stress_shock_put` for the volatility preset. Each is compared with its seven registered peers only within the same source window, portfolio, preset and liquidity profile.

Complete, flat, context-complete and sufficiently warmed cases supply paired differences. Held, open, incomplete and short-warmup cases remain censored. Comparisons distinguish both-traded cases from complete no-trade zeros. No profits or capital are added across counterfactuals, and no result activates a strategy or additional paper root. The review verifies the exact CSV and its completed publication, pins its code and inputs, respects owner pause authority, and refuses an existing output folder.

```powershell
runtime\python\python.exe -I -B scripts\options_paired_review.py --focus stress_put --input staging/COMPLETED-PUBLICATION/all-results.csv --input-sha256 EXACT_SHA256 --publication staging/COMPLETED-PUBLICATION/completed.json --publication-sha256 EXACT_SHA256 --output staging/stress-review-NEW
```

The existing broad sample study covers 3,309 historical roots, not 3,309 currently eligible stocks. A new eight-stock pooled comparison uses AMD, BAC, CSCO, F, GE, GM, INTC and KO in the existing August 2019 sample. Its 22 sessions are reused development data, not new history or an independent holdout. Side sizes remain null: strict liquidity cannot qualify those quotes. The 23/41/65-session hypotheses cannot manufacture their missing warmup.

## Keep source coverage separate from paper authority

The optional Workspace file `market-data/options/paper-readiness.json` declares at most 16 research roots and 32 source windows. Reads are bounded to 64 KiB; the pure output is bounded to 32 KiB. Catalog/completion digests and provenance flags are declared metadata, not independent source verification. Coverage envelopes can contain gaps or overlap. Saved historical spots never establish current below-$100 eligibility.

The inventory displays the same account's single $500 monthly gross-commitment allowance and $100 all-in entry ceiling. **The active paper source remains F only**, using the experimental date-only partial-chain workflow. Additional roots explicitly lack a qualified current options source and reviewed active workflow. Metadata cannot authorize entry, funding, account creation or source activation. Missing or malformed optional metadata produces a diagnostic and does not suppress the existing quote collector or exits.

The daily brief records a bounded coverage count and configured paper roots alongside its saved paper results. Earlier completed briefs are not rewritten. Changing actual active roots requires a separately versioned, reviewed same-account engine and qualified input source; one $500 account per ticker is not supported.

## November 1 source review

The surviving preserved-history repositories contain SPY/IWM/QQQ archives, not the advertised vanished single-stock history. Their existing data remains provisional. The public [Hugging Face stock-options dataset](https://huggingface.co/datasets/siddharthmb/stocks-options) has a 9.13 GB CSV and related OHLCV/action tables, but no documented upstream provider or license, no executable-side sizes, and unresolved event/date semantics. Its automatic viewer is explicitly partial. The first bounded documented stock/date query returned HTTP 500; the failed attempt was retained without retries or bulk download. No complete new cohort qualified.

[HistoricalData.net](https://historicaldata.net/options.html) offers its existing six-month sample and public schema/coverage/quality reports. The quality evidence does not grant free access to its licensed raw daily chains. Its aggregate comparison percentages do not authenticate bid/ask sizes or executable quotes. No purchases, accounts, access-control workarounds or silent feed substitutions were made.

New data must qualify before historical analysis or paper use. Free availability, a repository license assertion, a large file, or a profitable short sample does not establish a reliable feed, source rights, standard deliverables or a profitable edge.
