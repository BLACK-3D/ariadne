# Free Theta history before paid market data

Use free historical observations to develop daily swing/position hypotheses first. A paid live feed is not a prerequisite for that research. This source addition captures and imports EOD evidence; it does not create a performance study, select a winning strategy, migrate a paper account or place an order.

The current research window remains the last twelve calendar months through the latest completed session. The existing $25,000 paper bankroll, no automatic top-ups, financial rules and failure holds remain unchanged.

## What is verified and what is pending

Theta documents Free stock/options EOD access. Its subscription page conflicts about the exact free lookback and rate: prose says one year/20 requests per minute, while tables list an older first-access date/30 requests per minute. Ariadne uses the conservative twenty-per-minute pace and verifies actual returned coverage. A successful signup or a historical date label does not prove a complete year of data.

The supplied bootstrap JAR and portable Java have been staged locally. The downloaded application must still be prepared, pinned and inspected before any supervised launch. A local Terminal response remains unattested provider evidence. Credentials, actual account entitlement, complete history and profitable strategy performance are not established by synthetic tests.

## Protect the API key locally

In PowerShell from the Ariadne project folder:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_terminal.py status
& '.\scripts\Set up Theta Terminal.cmd'
```

Use the [account API Key page](https://www.thetadata.net/portal/api_key), linked directly from Theta's Getting Started guide. REST, Python, MCP and Excel are documentation/interface choices; they are not values to paste into the key prompt. The setup console asks for a Theta **API key**, not the account password. Enter it only in that hidden local prompt, never in chat, a command argument, GitHub, a screenshot, a CSV or an import manifest. Windows CurrentUser DPAPI protects the private store under `data/theta-terminal`. Setup itself performs no network request, subscription purchase or Terminal launch.

An optional existing `THETADATA_API_KEY` process environment variable is accepted privately. Do not type its value into shell history. Exporting the project to another Windows user does not transfer DPAPI credentials; each owner performs their own setup.

After the key is saved, the preparation command can download the assigned application for static review:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_terminal.py prepare --online
```

Preparation uses only fixed vendor HTTPS routes, no proxies/redirects/retries, and generic errors. The vendor metadata protocol puts the key in the TLS-protected URL path; Ariadne never persists or prints that private URL or provider error text. The supplied bootstrap can log such URLs, so these commands do not run it with real credentials. The application artifact receives an observed SHA-256 and ZIP/class inspection, not an invented independent publisher checksum or safety certificate. Preparation never executes Java. Existing or partial preparation is held for inspection rather than overwritten.

On this installation, portable Temurin 21.0.13+9 was checksum-verified from its official Windows x64 JRE ZIP and the owner-supplied bootstrap was hash-pinned. A plain Git checkout does not include either binary. For another installation, [obtain the reviewed Java release](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.13%2B9) and Theta's official bootstrap separately; preparation refuses bytes that differ from the reviewed pins. Java ZIP SHA-256: `2332c0b4cff6bed722da3567c91d5e47568bcba863fa4945d0a8d6fc76158628`. Bootstrap SHA-256: `08773e104a6df321f35fdd0d59d455065cac1ed36aa82b9962557dd740fd20b5`. A later vendor revision needs a new review rather than a hash bypass.

Portable Java lives under `runtime/java21`; the JAR/application/log/temp paths belong inside the project. No global Java, PATH, registry, service, microphone or startup setting is changed by preparation. Actual vendor application confinement remains a separate review before launch.

## Define the historical request before collection

Create a workspace-relative `data/workspace/imports/theta-history-request.json`. The schema has exactly `schema`, `start_date`, `end_date` and `requests`. Each request is exactly `{kind:"stock",symbol:...}` or `{kind:"option",contract_id:...}`. Include the matching stock for each explicit option. At most four stock roots and sixty-four requests are supported; no universe/expiry/strike wildcard or outcome-based discovery is performed.

This is a format example only; it does not prove listing, data availability or a suitable trade:

```json
{"schema":1,"start_date":"2026-11-03","end_date":"2026-11-04","requests":[{"kind":"stock","symbol":"KO"},{"kind":"option","contract_id":"KO261120P00080000"}]}
```

Choose the cohort before looking at outcomes. For the first account test, a small predeclared stock/contract sample establishes availability only. It is not a twelve-month backtest or an independently validated edge.

Hash the exact request and pass that lowercase SHA-256:

```powershell
(Get-FileHash -Algorithm SHA256 data\workspace\imports\theta-history-request.json).Hash.ToLowerInvariant()
.\runtime\python\python.exe -I -B scripts\theta_history.py capture --online --manifest imports/theta-history-request.json --manifest-sha256 <SHA256> --output reports/theta-history-first
```

A running, entitled Terminal v3 at `127.0.0.1:25503` is required. Explicit online mode is required even for loopback because the Terminal relays Internet access. Inherited offline policy, global pause, disabled/revoked owner Internet permission or changed source/input bytes stop acquisition. The workflow reads owner controls without initializing or updating financial tables.

Each attempt is finite: at most 300 collection seconds, 512 KiB per response, 16 MiB aggregate wire, 20,000 rows and a paced twenty requests per minute. The research-only journal reserves at most four attempts, 256 total requests and 64 MiB wire across attempts. Reservations are not automatically refunded or renewed. This journal is separate from the paper account.

## Read and import the saved evidence offline

A completed capture saves `selection.json`, `capture.json`, `normalized.json` and `completed.json`, with artifact lengths, hashes, frozen dates and implementation hashes. Normalized coverage distinguishes returned and missing regular sessions for each requested instrument. Missing reports are not zero-volume days, zero losses or successful trades.

For another offline normalization, preserve the source capture and its original completed receipt:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_history.py import --capture reports/theta-history-first/capture.json --capture-sha256 <CAPTURE_SHA256> --output reports/theta-history-offline-review
```

The importer requires the exact capture bytes and sibling `completed.json`. A known local attempt must also have a committed completed journal row; a completion file written immediately before a crash does not bypass a running/failed journal. No journal is created by an offline import. Saved hashes establish byte identity, not provider authenticity.

Inspect budget bookkeeping without collection:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_history.py status
```

Status reports journal holds and counters, and explicitly says artifact integrity was not checked by that lightweight view. A new acquisition rechecks all prior completed artifacts. Failed/interrupted attempts and existing output directories are never replayed through a new identifier. Complete received responses are quarantined privately after validation or permission failure; no failed prefix becomes a completed import. Storage failures may leave only an uncertain journal reservation, which remains held.

## What a later strategy study must preserve

EOD `created` is report generation, `last_trade` is a separate trade clock, and neither becomes an actual NBBO-event timestamp. Theta describes the report as generated at 17:15 Eastern with the last NBBO available then. Raw clocks and actual receipt times stay distinct; unknown event timezone/age remains unknown. EOD access supplies no separate historical OI endpoint, independently verified standard deliverable or corporate-action basis here.

A later versioned research study can explicitly model conservative ask entries, bid exits, fees, limited depth, later-session decisions and retrospective availability assumptions. It must not claim same-close fills using a report generated after that close, infer intraday stop/target ordering, repair missing exits, fill missing OI with zero, relabel Theta as another provider, or bypass the existing live five-second gate. All performance rows must separately pass the study's actual registration window.

Compare stress-filtered puts, recovery calls, alternatives and cash on matched data and capital assumptions. Existing KO outcomes have already been examined; re-splitting them does not manufacture an untouched holdout. New unseen cohorts or subsequent forward observations are needed for meaningful validation. No winner, actual profit or subscription requirement follows from this data-acquisition milestone.

Primary references: [Free subscriptions](https://thetadata.net/docs/Articles/Getting-Started/Subscriptions.html), [options EOD](https://thetadata.net/docs/operations/option_history_eod.html), [stock EOD](https://thetadata.net/docs/operations/stock_history_eod.html), [EOD methodology](https://thetadata.net/docs/Articles/Data-And-Requests/OHLC-EOD.html), [private API-key setup](https://thetadata.net/docs/Articles/Getting-Started/Getting-Started.html).
