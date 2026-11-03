# Public market data for local options paper trading

This guide describes the current repository source, not the last packaged 0.37.0 Windows starter. Use a checkout containing `scripts/public_market.py` and the matching Ariadne modules. Installing these files does not restart an existing service, activate a new paper policy or enable a recurring collector.

Public supplies authenticated market data. Ariadne keeps the paper account locally. The connection has no real-order endpoint, and its simulated money is separate from a Public brokerage account. The existing shared **$25,000 total bankroll** is preserved; there are **no automatic top-ups**, no second bankroll and no transfer of real funds as part of setup. Existing pauses, substantive failures and review holds remain effective.

## Create the account and secret

Public's [Electronic Delivery and Signature Agreement, section 6](https://public.com/disclosures/electronic-delivery-agreement) requires a valid mobile phone number to open and maintain an account, and its [signup page](https://public.com/signup) starts with that field. No official phone-free onboarding path was verified. If this requirement is unacceptable, choose another data provider before opening a Public account. Enter account-verification details directly with Public; Ariadne does not collect your phone number or verification codes.

1. Open [Public's API page](https://public.com/api) and sign up or sign in directly with Public. Complete its account approval, identity checks and applicable agreements yourself. Approval and market-data eligibility are Public's decisions. This integration does not open an account, accept agreements, request trading permissions or move money for you.
2. Read the [current fee schedule](https://public.com/disclosures/customer-fee-schedule) and [Individual API terms](https://public.com/disclosures/individual-API-program). Check your actual market-data entitlement and any account/funding requirements with Public before relying on its quotes. Ariadne does not require you to transfer the $25,000 real bankroll to obtain a data connection.
3. While signed in, open [Public API settings](https://public.com/settings/v2/api/api-keys) and generate a personal secret key. Public's [quickstart](https://public.com/api/docs/quickstart) links to these settings. Menu labels may change; use the official quickstart if the direct link changes.
4. Enter the secret only in Ariadne's local hidden-input prompt. Never paste it into a chat, browser pairing screen, source file, GitHub issue, command-line argument or screenshot. Public's long-lived secret is exchanged for a short-lived access token; the bearer token is kept in memory rather than printed or saved.
5. Select the intended account explicitly after the account lookup. Ariadne does not choose the first account automatically. Keep its account identifier private along with the secret and any raw account-lookup results.

The Public secret may authorize real trading outside this connector. Public's token documentation does not establish that a market-data-only personal secret can be created. Ariadne restricts its own allowed network methods and paths; that is a software restriction, not a broker-enforced read-only credential. Revoke a lost or exposed secret in Public's settings and generate a replacement. Do not share credentials or subscribed quotes with another Ariadne installation.

## Local commands

The exact command sequence and generated private paths are documented below with the matching CLI in this source revision. Run from the Ariadne installation folder in PowerShell using its bundled Python. No additional market-data SDK, cloud model or paid AI subscription is required.

Start with account discovery, then store your selected account and secret locally:

```powershell
runtime\python\python.exe -I -B scripts\public_market.py accounts --online
runtime\python\python.exe -I -B scripts\public_market.py setup
runtime\python\python.exe -I -B scripts\public_market.py status
```

`accounts --online` performs the token exchange and account lookup. If no credential is stored, it asks for the secret through a hidden prompt. It prints account identifiers locally so you can select one; do not post that output publicly. `setup` asks for the secret and selected account identifier without making a network request. It writes `data/public-market/credential.dpapi`, protected with Windows CurrentUser DPAPI. It does not save an unencrypted secret or a bearer token. Treat the complete `data` directory as private even though this file is encrypted. A clean portable export must exclude it; on another PC or Windows account, perform setup again with that owner's credentials.

`status` reports configuration and saved paper-account state without logging in or collecting fresh data. A configured credential is not evidence of successful authentication or a usable live feed. Use `--help` for command-specific input contracts:

```powershell
runtime\python\python.exe -I -B scripts\public_market.py --help
runtime\python\python.exe -I -B scripts\public_market.py collect --help
runtime\python\python.exe -I -B scripts\public_market.py paper --help
```

Save `imports/public-watchlist.json` under `data/workspace` using this exact structure:

```json
{
  "schema": 1,
  "instruments": [
    {"symbol": "F", "type": "EQUITY"},
    {"symbol": "F261218P00010000", "type": "OPTION"}
  ]
}
```

These identifiers illustrate the input format only. They are not a recommendation, a current-price assertion or evidence that this contract is presently listed or eligible. Replace them with the stock/ETF and exact listed option identifiers you intend to inspect. The list is bounded and explicit; collection does not discover an expanding universe automatically.
A quote-only capture is separate from paper-account mutation:

```powershell
runtime\python\python.exe -I -B scripts\public_market.py collect --online --instruments imports/public-watchlist.json --output reports/public-capture-001
```

The watchlist is a local structured input under `data/workspace`, not an arbitrary URL or program. Use a new report name for each capture. Raw quote evidence and completed receipts stay in the private workspace. `collect` does not migrate the paper account or create a simulated fill.

Inspect the account and migration binding before considering the paper bridge:

```powershell
runtime\python\python.exe -I -B scripts\public_market.py binding
```

Save the complete JSON returned by `binding` as `data/workspace/imports/public-migrate.json`. Keep its `expected_revision`, `expected_account_id`, `expected_state_sha256`, `expected_source_sha256` and `expected_owner_epoch` unchanged. Add exactly these fields after reviewing what you are adopting:

- `request_id`: a new identifier such as `public-migrate-001`.
- `acknowledge_simulated`: `true`.
- `acknowledge_no_topups`: `true`.

This is an approval for the bound local paper migration, not brokerage order authority. Inspect the complete file before running it. Do not put the Public secret or brokerage account identifier into this request: `expected_account_id` is the existing **local paper-account** ID supplied by `binding`.

Migration is an explicit separate action with that local reviewed request:

```powershell
runtime\python\python.exe -I -B scripts\public_market.py migrate --request imports/public-migrate.json
```

Before this one-time migration, stop Ariadne through its normal owner controls. The migration must acquire the same `data/server.lock` held by the server; it refuses an active server before reserving work and keeps the lock through commit. Restart Ariadne from the updated source only after a completed migration receipt. This prevents an older running process from trying to read the new account format. Account setup and quote-only collection do not require this schema migration. These instructions do not authorize clearing existing pauses or failure holds.

Only the existing healthy, flat and unheld account can qualify. The request must acknowledge the paper-only policy and bind the inspected account/owner state. An obsolete fingerprint, existing position, failure, hold or changed permission prevents migration. There is no automatic migration during setup and no funding increase. Do not fabricate a request to bypass a refusal.

After qualified migration, run `binding` again and save its complete current result as `imports/public-paper-request.json`. Add a new outer `request_id` and a `requests` array containing the exact fields below. This row is a **format example**, not a current trade or recommended limit:

```json
{
  "request_id": "candidate-001",
  "contract_id": "F261218P00010000",
  "side": "buy",
  "quantity": 1,
  "price_limit": "1.00",
  "allow_partial": false
}
```

Use an actual explicitly reviewed listed contract, integer quantity and fixed price protection. The request contains no supplied decision clock. For a sale, use `side: "sell"` and an explicit minimum acceptable bid; `allow_partial: true` only permits a partial sale of already owned inventory. The workflow never changes a limit or clips a buy to force a fill. Get a fresh binding after any financial transition; an old request cannot authorize a changed account.

The independently sourced metadata file has exactly `schema: 1` and `contracts`, a bounded list. Each contract row has exactly `contract_id`, `underlying_type`, `standard_deliverable_verified`, `deliverable_verification_source`, `underlying_activity`, `contract_activity` and `open_interest`. The last three objects use the dated shapes in [the book-gate evidence table](OPTIONS_LIQUIDITY.md#input-fields-and-a-fictional-replay). Missing objects can remain `null`; that blocks a new entry rather than asserting evidence. Any supplied volume/OI value must match the actual Public capture. A receipt binds the template and capture hashes at execution; the owner does not predict the next capture's hash. Metadata timestamps and verification claims must describe real independent evidence, never invented dates.

An explicit paper attempt then collects fresh quotes and evaluates those bounded requests at the actual decision time:

```powershell
runtime\python\python.exe -I -B scripts\public_market.py paper --online --instruments imports/public-watchlist.json --request imports/public-paper-request.json --supplement imports/public-liquidity-evidence.json
```

All input paths and the quote-only output path above are relative to `data/workspace`. The paper command publishes through the protected ledger receipt under `reports/options-recovery/<request_id>`; it does not accept `--output`. `--supplement` supplies independently sourced missing dated evidence using the command's exact schema; it cannot be used to overwrite the captured prices, sizes or clocks. A paper attempt may complete with **no fill** because a required observation is missing or stale. Inspect the saved reason codes instead of weakening the gate to make a trade occur.

`--online` is an explicit network opt-in for this command process. An inherited explicit `ARIADNE_OFFLINE=1`, a disabled owner Internet control, a global pause or applicable workflow/briefing hold still prevents access; the command does not clear them. No command enables an unattended scheduler, clears a pause or starts a background collector. No credentials or live quotes were available during the offline implementation tests, so production authentication, entitlement and five-second feed qualification remain pending owner setup.

## What must pass before a paper fill

A successful login or HTTP response is not a successful feed qualification. Public's [quote endpoint](https://public.com/api/docs/resources/market-data/get-quotes) documents bid and ask prices, displayed sizes, separate side timestamps, volume and open interest. Ariadne must also establish all the dated evidence required by the [strict options-book gate](OPTIONS_LIQUIDITY.md).

- The actual quote-event clocks and receipt clock must be causal. A new HTTP response cannot make an old quote fresh. Both side clocks are retained. Buys use the older required side clock; a sale uses its actual bid-event clock. An update to the opposite side does not replenish already consumed depth.
- New buys require a fresh stock/ETF quote below $100, twenty dated completed-session observations for stock liquidity, dated current-session contract volume and the latest prior-session open-interest observation.
- Standard 100-share deliverable verification is explicit evidence. It is not inferred merely because an option symbol has the expected shape.
- Missing, stale or unsupported evidence blocks admission. The connection does not infer an open-interest session from the collection date, manufacture timestamps or replace absent displayed size with volume.
- Buys use the actual ask; sales use the actual bid, with the supplied price protection unchanged. No midpoint, last-price, theoretical-price or target-price fills are substituted.
- Cash, owned quantity, fees and previously consumed displayed depth must pass the shared account's durable controls before a proposal becomes a simulated fill. Repeated polling does not replenish the same quote's size.

These tests establish a conservative paper-account model. They do not establish actual execution, queue priority, price availability at a real broker or a profitable strategy. No strategy is automatically promoted merely because market data becomes available.

## Offline operation and daily results

Fresh Public quotes require Internet, valid credentials and Public's current entitlement. Offline use can inspect saved captures and paper receipts, reproduce deterministic checks and read the last saved result. It cannot generate a fresh market quote or treat a saved historical observation as current. The connector must respect the existing offline switch and owner controls; setup does not silently change them.

Ariadne's daily brief can report saved paper results and blockers through the existing paper-account reporting path. A completed brief remains immutable. Installing this source does not clear a held briefing schedule, rewrite an old report or create duplicate quote polling. Daily results must distinguish a blocked/no-trade evaluation, an actual saved simulated fill and stale offline observations.

Public also documents historical option bars. Bars are not timestamped historical bid/ask quotes with displayed sizes, and this connection does not claim to supply a usable twelve-month executable options history. Forward capture and historical backtesting are different evidence sets.

## Costs and personal use

Checked November 3, 2026: Public advertises no API-access or request fee. Account charges still apply. Its fee schedule, updated October 13, 2026, lists a **$3.99 monthly inactivity fee** for accounts with **less than $70 total value and no activity in the last six months**. API requests have not been verified to count as activity. Do not place unnecessary real trades to try to waive a fee. Review Public's current terms before opening or funding an account.

The individual API is offered for the account owner's personal, noncommercial use. Public's terms restrict sharing platform/API access and third-party services. The open-source Ariadne license does not grant market-data redistribution rights or commercial Public API access. Each owner needs their own approved access and must check their permitted use; do not expose this connection as a shared data service.

The connection performs no language-model calls. Polling still consumes local compute, network capacity and energy; no savings or fixed operating cost has been measured. This setup does not buy a subscription or authorize live brokerage orders.

## Primary documentation

- [Public API overview and advertised access fees](https://public.com/api)
- [Quickstart and secret creation](https://public.com/api/docs/quickstart)
- [Personal access-token exchange and expiry](https://public.com/api/docs/resources/authorization/create-personal-access-token)
- [Account lookup](https://public.com/api/docs/resources/list-accounts/get-accounts)
- [Actual quote response fields](https://public.com/api/docs/resources/market-data/get-quotes)
- [Historical bars](https://public.com/api/docs/resources/market-data/get-bars-v2)
- [Customer fee schedule](https://public.com/disclosures/customer-fee-schedule)
- [Individual API program terms](https://public.com/disclosures/individual-API-program)