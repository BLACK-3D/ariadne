# Theta Terminal: use the existing account without changing the paper bankroll

This connection is a **data-only, diagnostic bridge**, not an installed Terminal or an activated paper strategy. This guide supports existing Theta FREE accounts. Ariadne does not sign in, upgrade an account, install software, launch a Terminal/JVM or request a phone number.

The existing shared **$25,000 total paper bankroll, no automatic top-ups**, remains unchanged. This connector has no ledger writer, broker order, account funding or policy-rebind operation. Older schema6/7 rules and receipts are retained exactly.

## Historical research first

The separate [free-history workflow](THETA_HISTORY.md) adds bounded EOD capture and hash-checked offline imports. It does not apply the live five-second admission rule to historical research, create a performance result or change the paper ledger. Start there before considering a paid live feed.

## What the FREE account can establish

Theta's FREE access does not establish the fresh live quotes required by Ariadne's unchanged five-second liquidity gate. The separate historical workflow supports EOD download/import; this snapshot connector remains dedicated to quote qualification. Retrieval now does not make a historical event clock current. Do not upgrade merely to make a test pass.

Current advertised options Value access is $40/month. The stock add-on advertised at $80/month would make a combined options-plus-stock route $120/month, subject to current eligibility, entitlements and checkout terms. It is not the cheapest combined candidate compared with the $99/month Alpaca alternative. No purchase is authorized or performed by this guide. The existing FREE signup also does not establish whether any future paid checkout would require a phone number.

References: [Theta pricing](https://www.thetadata.net/pricing), [Terminal/API documentation](https://thetadata.net/docs/), [option quote snapshot](https://thetadata.net/docs/operations/option_snapshot_quote.html), [stock quote snapshot](https://thetadata.net/docs/operations/stock_snapshot_quote.html).

## Terminal prerequisite and portability

The API bridge supports only Terminal v3 at fixed `127.0.0.1:25503`. It does not accept another hostname, port, proxy, redirect, executable or installer. The Terminal manages its own upstream login and subscriptions.

If you independently choose to prepare the Terminal, obtain its installer/JAR and runtime from the official provider after reviewing their terms. Keep any project-controlled download, runtime, configuration and log inside Yikes. Do not install a global Java runtime, Windows service or machine-wide configuration as part of these steps. This task has not established that the provider's launcher confines every generated credential/log/cache to the project; check that behavior before launch. Do not put account credentials in chat or GitHub, and do not copy them into Ariadne's JSON imports.

An accessible loopback port does **not** prove that the local responder is Theta or that upstream quotes have paid live entitlements. The adapter always records `provider_authenticated:false` and `local_terminal_verified:false`; imported files cannot change those flags to true.

## Local commands

From PowerShell in the existing Ariadne folder:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_market.py status
.\runtime\python\python.exe -I -B scripts\theta_market.py binding
```

Neither command contacts or launches the Terminal, reads its credentials, creates a bankroll or changes settings. The account binding uses the same initialized owner databases and the same five expected fields described in [Alpaca qualification](ALPACA_PAPER_DATA.md).

For a quote-only inspection, prepare `data/workspace/imports/theta-watch.json` with exactly `schema:1` and `instruments`, containing one to four explicit canonical option instruments and exactly their required stocks. Use the same symbol/type format as the Alpaca guide.

```powershell
.\runtime\python\python.exe -I -B scripts\theta_market.py collect --online --instruments imports/theta-watch.json --output reports/theta-first-capture
```

Explicit `--online` is required even though the connection is loopback, because the Terminal is an upstream Internet proxy. Inherited `ARIADNE_OFFLINE=1`, installed offline policy, owner global pause or disabled Internet access blocks collection before attempting the port. A missing Terminal, missing entitlement, malformed response or unsupported quote condition fails closed without a retry.

For a read-only account-bound liquidity diagnostic, use the same complete request structure, unchanged current bindings and optional independently dated missing-metadata supplement documented in [Alpaca qualification](ALPACA_PAPER_DATA.md):

```powershell
.\runtime\python\python.exe -I -B scripts\theta_market.py qualify --online --instruments imports/theta-watch.json --request imports/theta-request.json --output reports/theta-first-check
```

Every capture/check publishes exclusively into a new `reports/theta-<name>` folder with actual source bytes, normalization and hash/byte receipts. No existing or uncertain output is overwritten. Account and permission bindings are checked before collection and again afterward. Options/briefing holds block qualification; the separate QQQ hold remains a reported downstream blocker.

## Evidence limits that remain deliberate blockers

- Theta option snapshots are labeled NBBO messages. Their single timestamp is not an independent last-change clock for each bid and ask. Stocks are requested explicitly as Nasdaq Basic BBO, not relabeled as consolidated SIP/NBBO.
- The snapshot documentation reviewed supplies a naive timestamp format without a sufficient verified response-timezone basis. Such raw clocks are preserved, but UTC event time remains unknown. Ariadne does not infer UTC from the PC timezone, the receipt clock or an unrelated request-time parameter. Even an apparently recent naive timestamp cannot pass the strict gate.
- When a source provides an explicit aware offset, exact nanosecond event/receipt comparisons still apply before the conservative microsecond projection. A future event or receipt cannot be hidden by rounding.
- Current-session contract volume, dated prior-session open interest, exact ADV20 observations and verified deliverable/reference evidence are not invented from a snapshot. An optional independent supplement may fill only missing nonquote metadata; it is not authenticated provider evidence.
- The existing strict five-second, displayed-depth, bid/ask, under-$100 underlying and fee-inclusive capital constraints are unchanged. There is no midpoint fill, fake depth, automatic quantity reduction, month reset or additional capital.

The output reports `evaluated_evidence_only`, with explicit blocked proposals or diagnostic admission candidates. It always reports **zero paper fills**, `paper_submission_ready:false`, and preserved terminal-provenance/entitlement/versioned-ledger blockers. No current quote, executed trade or profitable outcome follows from a FREE account, an open port, a JSON file or passing synthetic tests.

Offline Ariadne can inspect saved reports and daily connection-readiness notes. Fresh market quotes still require the necessary running, qualified and entitled source. No extra scheduler or automatic polling is created by these commands.
