# Free Theta history before paid market data

Use free historical observations to develop daily swing/position hypotheses first. A paid live feed is not a prerequisite for that research. This source addition captures and imports EOD evidence; it does not create a performance study, select a winning strategy, migrate a paper account or place an order.

The current research window remains the last twelve calendar months. Free EOD requests end on a completed market session before the New York registration date, respecting the advertised one-day delay. The existing $25,000 paper bankroll, no automatic top-ups, financial rules and failure holds remain unchanged.

## What is verified and what is pending

Theta documents Free stock/options EOD access. Its subscription page conflicts about the exact free lookback and rate: prose says one year/20 requests per minute, while tables list an older first-access date/30 requests per minute. Ariadne uses the conservative twenty-per-minute pace and verifies actual returned coverage. A successful signup or a historical date label does not prove a complete year of data.

A direct Python/protocol path is implemented alongside the Terminal adapter. Its offline tests and optional native-runtime compatibility checks establish implementation behavior, not provider authentication or returned history. **Qualify authentication, account entitlement and returned coverage on each installation using its completed local receipt.** Source availability alone establishes none of these. No performance study, paper fill or profitable outcome follows from this milestone. A failed or interrupted setup remains held across both paths until explicitly reviewed.

## Direct history access without Terminal

Theta's official [Python Library Getting Started](https://thetadata.net/docs/Python-Library/Getting-Started.html) documents HTTPS authentication and direct gRPC data access without a running Terminal or Java. API-key support requires their Python library version 1.0.9 or later. Ariadne reuses the reviewed protocol definitions from that official library through a small data-only client; it does not instantiate the SDK's credential-discovery or dataframe client.

The optional Windows x64 runtime pack lives under `runtime/theta-direct`, separate from the main server's dependencies. It pins official protocol definitions, gRPC, protobuf, Zstandard, typing extensions and the certificate bundle with a complete file-hash manifest. The current target is normal CPython 3.13 x64. A plain source checkout may not contain these optional assets. Asset inspection verifies the installed pack; it does not download missing files or install packages globally. Use the reviewed matching pack when assets are absent or differ.

From PowerShell in the Ariadne project folder, inspect the pack without reading a key, importing the native transport, authenticating or requesting market data:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_direct.py status
```

Preview one explicit stock or option selection locally. Replace these example dates with prior completed sessions inside the current rolling window:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_direct.py preview --symbol F --start 2026-11-03 --end 2026-11-04
.\runtime\python\python.exe -I -B scripts\theta_direct.py preview --contract F270115P00015000 --start 2026-11-03 --end 2026-11-04
```

`preview` freezes the actual registration clock, checks the date window and explicit identity, and returns limits plus asset readiness. It makes no connection and creates no study or trade. A contract example is not proof of listing, liquidity, availability or a recommendation. This inspection CLI offers `status` and `preview`; authenticated collection uses a separately reviewed worker profile.

The direct worker reuses the existing Windows CurrentUser DPAPI-protected Theta key described below. Another API key is not required. The key is loaded privately only after the caller has validated its owner authorization, source/asset pins and global attempt reservation. The direct client performs no `.env`, credentials-file or environment-key discovery. Keys, sessions and account email stay out of command arguments, exported reports and logs. Moving the pack to another Windows user requires that user's own protected-key setup.

Each admitted direct attempt permits one bounded HTTPS authentication and one fixed historical EOD RPC for one explicit stock or option contract. There is no symbol/expiry/strike wildcard, discovery sweep, automatic retry, subscription change or paper-account operation. Selecting this path after a Terminal failure does not create a fresh setup allowance: all setup paths preserve the existing global attempt reservations and substantive-failure stops.

If authentication-response validation stops, preserve its receipt and attempt reservation. New diagnostics distinguish response framing, JSON, required session/user fields and destination-policy checks using fixed categories and an observed HTTP status where available. They retain no response text, account field names, email or session value. HTTP 200 alone does not establish valid credentials or an entitled session. Older generic failures cannot retrospectively establish which check failed. Exhausting an approved attempt allowance requires fresh owner approval before another request; changing adapters or fixing local code does not renew it.

The reviewed supervisor places only its new worker in a Windows lifetime job, uses project-local home/temp directories and a minimal environment, suppresses worker console output, and applies a 300-second lifetime. Owner pause or Internet-permission changes stop the owned worker. The client uses fixed vendor TLS destinations, explicit deadlines, closed channels and bounded source decoding. It permits at most 128 response messages, 512 KiB per serialized message, 4 MiB total RPC response payload, 8 MiB decoded data and 4,096 rows. Payload counts exclude TCP/TLS/HTTP2 overhead. Authentication has its own 32 KiB response bound. Native gRPC operates outside Python's socket audit hook; this process design is not an OS network sandbox. Offline use inspects saved evidence without starting that transport.

A completed direct capture retains secret-free validated source-message bytes and hashes, exact integer price/scaling fields, raw timestamp milliseconds/nanoseconds/zone/precision, actual receipt clocks and explicit missing sessions. Unknown submillisecond interpretation remains unknown. It is a separate evidence schema from Terminal JSON captures; the Terminal importer below does not convert or authenticate it automatically. Failed prefixes remain failed research evidence and cannot be adopted as completed work or fills.

Collection diagnostics distinguish a failed owner-check callback from an unexpected local processing failure. Fixed `capture_stage` and `exception_category` fields identify the operation without retaining exception text, arbitrary class names, tracebacks, credentials or query values. An internal processing failure is not evidence of a provider permission rejection. These diagnostics apply to future captures; they do not recover the origin of older generic receipts.

A nonempty validated-prefix flag means some rows passed validation in memory before failure. It does not establish a completed file, full history, usable import or paper fill. When the caller discards failed prefixes, neither their payload nor coverage can be recovered from that flag. Preserve the failed reservation and require explicit new authority before another provider operation.

## Protect the API key locally

Browser setup avoids a separate console input window. From the Ariadne project folder:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_terminal.py setup-browser
```

Open the printed numeric loopback address in your browser. The temporary page accepts the key in a password field and saves it using the same Windows CurrentUser DPAPI store as console setup. It is local-only, bounded to five minutes and one submission, and refuses an existing key. It does not start or restart Ariadne, launch Java, enable Internet connectors or purchase a subscription. The address contains no key. An unrelated website cannot submit a key through its own origin. Browser extensions and other programs running as you remain outside these controls; this does not promise to erase clipboard or browser memory.

On the signed-in [account API Key page](https://www.thetadata.net/portal/api_key), **No API key exists** means select **Create Key** first. Newly created key text may be shown only once. Copy it into the local password field and save. Do not regenerate an existing key merely to inspect it; that can invalidate a credential already in use.

Console setup remains available:

In PowerShell from the Ariadne project folder:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_terminal.py status
& '.\scripts\Set up Theta Terminal.cmd'
```

Use the [account API Key page](https://www.thetadata.net/portal/api_key), linked directly from Theta's Getting Started guide. REST, Python, MCP and Excel are documentation/interface choices; they are not values to paste into the key prompt. Setup asks for a Theta **API key**, not the account password. Enter it only in the local password form or hidden local prompt, never in chat, a command argument, GitHub, a screenshot, a CSV or an import manifest. Windows CurrentUser DPAPI protects the private store under `data/theta-terminal`. Setup itself performs no outbound network request, subscription purchase or Terminal launch.

An optional existing `THETADATA_API_KEY` process environment variable is accepted privately. Do not type its value into shell history. Exporting the project to another Windows user does not transfer DPAPI credentials; each owner performs their own setup.

### Optional Terminal path

The direct history path above does not require this Java preparation. After the key is saved, a separately admitted Terminal preparation can download the assigned application for static review:

```powershell
.\runtime\python\python.exe -I -B scripts\theta_terminal.py prepare --online
```

Preparation uses only fixed vendor HTTPS routes, no proxies/redirects/retries, and generic errors. The vendor metadata protocol puts the key in the TLS-protected URL path; Ariadne never persists or prints that private URL or provider error text. The supplied bootstrap can log such URLs, so these commands do not run it with real credentials. The application artifact receives an observed SHA-256 and ZIP/class inspection, not an invented independent publisher checksum or safety certificate. Preparation never executes Java. Existing or partial preparation is held for inspection rather than overwritten.

The optional Terminal runtime uses checksum-verified portable Temurin 21.0.13+9 and a hash-pinned official bootstrap. A plain Git checkout does not include either binary. For another installation, [obtain the reviewed Java release](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.13%2B9) and Theta's official bootstrap separately; preparation refuses bytes that differ from the reviewed pins. Java ZIP SHA-256: `2332c0b4cff6bed722da3567c91d5e47568bcba863fa4945d0a8d6fc76158628`. Bootstrap SHA-256: `08773e104a6df321f35fdd0d59d455065cac1ed36aa82b9962557dd740fd20b5`. A later vendor revision needs a new review rather than a hash bypass.

Portable Java lives under `runtime/java21`; the JAR/application/log/temp paths belong inside the project. No global Java, PATH, registry, service, microphone or startup setting is changed by preparation. Actual vendor application confinement remains a separate review before launch.

### If preparation stops

A saved DPAPI container proves local storage, not a valid provider login or data entitlement. A failed preparation remains held; do not delete its directory, repeat the command, rotate the key, or switch authentication paths automatically. Inspect the retained receipts and obtain an explicit new continuation before another authenticated attempt.

New failure receipts include bounded numeric HTTP status and fixed diagnostic categories when those observations are available. They distinguish an attempted request, a returned response, content validation and local preparation failures without retaining a private URL, response body, header values or exception text. A reserved request is not proof that dispatch completed. Older generic receipts remain unchanged and cannot retrospectively establish a rejection or its cause.

## Terminal history capture: define the request before collection

This section applies to the separate loopback Terminal adapter, after its setup and any existing holds have been explicitly resolved. It is not the direct inspection CLI. Create a workspace-relative `data/workspace/imports/theta-history-request.json`. The schema has exactly `schema`, `start_date`, `end_date` and `requests`. Each request is exactly `{kind:"stock",symbol:...}` or `{kind:"option",contract_id:...}`. Include the matching stock for each explicit option. At most four stock roots and sixty-four requests are supported; no universe/expiry/strike wildcard or outcome-based discovery is performed.

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

Primary references: [direct Python access](https://thetadata.net/docs/Python-Library/Getting-Started.html), [Python option EOD](https://thetadata.net/docs/operations_python/option_history_eod.html), [Python stock EOD](https://thetadata.net/docs/operations_python/stock_history_eod.html), [Free subscriptions](https://thetadata.net/docs/Articles/Getting-Started/Subscriptions.html), [options EOD](https://thetadata.net/docs/operations/option_history_eod.html), [stock EOD](https://thetadata.net/docs/operations/stock_history_eod.html), [EOD methodology](https://thetadata.net/docs/Articles/Data-And-Requests/OHLC-EOD.html), [private API-key setup](https://thetadata.net/docs/Articles/Getting-Started/Getting-Started.html).
