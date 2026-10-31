# Finite offline options research

The campaign tool compares a small, explicit set of hypotheses on the same historical input. It never advances, funds, migrates or selects a strategy for the owner paper account. The current Ford account, its below-$100 underlying rule, $100 all-in entry limit and one shared $500 monthly commitment allowance remain unchanged.

The initial research universe is **QQQ only**. This is an explicit, nondeployable historical research exception to the current Ford universe. The supplied archive's price and publication clocks can be assumed EOD; neither hashes nor matching field names authenticate an exchange quote, executable liquidity, corporate actions or a live fill. The new research profile uses the same $100 entry and $500 monthly gross commitment ceilings. It does not silently reuse the older $125-entry research profile.

## Fixed comparisons

The candidates are cash control, recovery calls, breakout calls, breakdown puts, a rotating combination of those three signals, two Ariadne RSI variants, and a diversified combination of all five signals. Their precise rules and hashes are in `ariadne/options_campaign.py`. The numeric technical triggers are Ariadne operationalizations of familiar ideas; OIC educational payoff pages and momentum research do not establish profitable short-dated option entry rules.

The two RSI variants were specified after the conventional campaign and creator review, before their own outcomes were evaluated. Both require 15 closes and use simple arithmetic RSI14 from their 14 changes, without Wilder smoothing. Flat prices produce RSI50; all gains produce100; all losses produce0. `rsi_rebound_call` requires RSI≤30 and a close strictly above both preceding closes. `rsi_exhaustion_put` requires RSI≥70 and a close strictly below both preceding closes. These are fixed Ariadne hypotheses, not a reproduction of a creator's bot or a claim of novel alpha. No threshold sweep is performed.

`rotating_ensemble` retains only the original recovery/breakout/breakdown signals. `diversified_ensemble` adds both RSI signals. Each combination rotates its fixed priority after entry, holds at most one contract total, and uses its own separate counterfactual shared $500 monthly ledger. The original five candidates can be rerun alongside the additions to check that their financial events, curves and metrics remain unchanged.

Two explicitly separate liquidity assumptions can consume one verified source stream:

- `strict_sizes` requires the executable side's supplied size to be at least one. Missing size rejects an entry or holds an unresolved position. Supplied size still lacks independent authentication.
- `unknown_size_price_proxy` permits a missing size as **unknown** for retrospective price diagnostics. Zero size is still rejected. This is not a substitute for observed tradable liquidity and never grants deployment permission.

The common model buys at a later observation's ask, sells at a supported later bid, charges $0.65 each side and reserves both fees inside the entry/commitment limits. It holds one fully paid standard long contract at most. Profits do not recycle the monthly allowance. A mandatory disposal deadline is precommitted. Missing held-contract quotes, unresolved boundaries and gaps stop that ledger; missing prices, clocks, sizes or settlements are never supplied to complete a profitable result.

Every standalone candidate is a separate **counterfactual comparison**, not another funded paper account. Each combination has one shared $500 monthly ledger across its own signals. Never add candidate budgets or returns together.

## Running a study

Use the bundled Python and existing canonical archive manifests. Each manifest must have an explicit expected SHA-256. For example, replace the placeholders before running:

```powershell
runtime\python\python.exe -I -B scripts\options_campaign.py --stage conventional --manifest market-data/options/archives/qqq-YYYY/catalog.json --manifest-sha256 EXACT_MANIFEST_SHA256 --output staging/options-campaign-run-001
```

The default reads only the existing owner's global pause authority. It does not create or initialize an owner database, change a setting, inspect a broker, fetch a quote, call a model or launch a native tool. A retained read-only connection performs a fresh short transaction at every checkpoint. Database identity, pause value and pause epoch must remain valid. SQLite busy retries share one five-second monotonic check budget; native cleanup and scheduling can add wall time. A pause, changed epoch, replaced database or exhausted read budget stops the run. `--detached-research` is an explicit alternative for an isolated source checkout; its receipts make no owner-readiness claim and it must not be used to bypass a pause or failure in owner-bound work.

Outputs are restricted to a new private `staging/...` folder. Existing, failed or partial folders cannot be replayed or adopted. A registration binds source manifests, candidate/profile choices, exact code and dependency hashes before market-data iteration. The new engine and command source are retained in the run's source snapshot. Completed results require full input-stream exhaustion and final source/authority verification. A later corrupt chunk invalidates the study even if every candidate already entered a hold.

Limits remain finite: at most four manifests, 512 MiB declared archive input, 16 total candidate/profile ledgers and 8 MiB aggregate command output. The command installs its process-local offline network policy. No new model, service, account or recurring dependency is required.

## Reading results

Use trading P/L **excluding contributions**, fees, closed positions, losing outcomes, marked drawdown, monthly commitments, coverage and holds together. A zero-trade rule is not a profitable strategy. A held ledger's endpoint is not a full-year return. Results stopped at different dates are not comparable full-period winners. Cross-profile values reflect different assumptions and must not be pooled into a ranking.

Any displayed ordering is descriptive same-window net P/L only. It does not establish the best strategy across markets, an out-of-sample edge or future win probability. No candidate is automatically promoted. Repeatedly searching until a positive result appears increases selection bias; retain every tried rule and failed outcome.

Conventional rules are evaluated before the new RSI variants and diversified combination are defined. The initial three-signal ensemble is predeclared with the conventional candidates. Creator claims require attributable public sources and complete rules; missing settings or incompatible intraday/futures data are reported as blocked, not filled in and attributed to the creator. Newly authored settings must be labeled Ariadne adaptations. Data whose outcomes have already been examined is development data, even when revisited under another strategy. A final validation window must be frozen before its outcomes are evaluated; a source-access failure is not a successful validation.

The bounded creator review included [SMB Capital's opening-range example](https://www.youtube.com/watch?v=dw9m0jV6PuY), [Option Alpha's RSI bot description](https://optionalpha.com/videos/new-rsi-mean-reversion-options-trading-bot), and [@astreka_'s Instagram VWAP/range-band comparison](https://www.instagram.com/reel/DeX7eb1KCSp/). Full numerical bot rules, verified transcripts or compatible intraday options evidence were missing. None is admitted as an exact creator backtest. Advertised paper returns and band-fade frequencies are not verified net option profits.

The October 31, 2026 source review found that an anonymous Ford availability list was accessible, but the registered monthly historical request was rejected for requiring a higher API-access level. That acquisition stopped. Availability metadata is not a historical options dataset. No paid account, monthly-export workaround or raw market-data redistribution is added by this tool.

Primary references: [OIC long calls](https://www.optionseducation.org/strategies/all-strategies/long-call), [OIC long puts](https://www.optionseducation.org/strategies/all-strategies/long-put), [OIC option-price behavior](https://www.optionseducation.org/referencelibrary/faq/option-price-behavior), and [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
