# Alpaca data-only qualification for Ariadne

Ariadne can capture paid OPRA options quotes and SIP stock quotes, validate their actual timestamps and displayed sizes, and run a **read-only admission check against the existing shared $25,000 paper account**. It cannot submit a paper fill, change a policy, create a second bankroll, top up funds or place a brokerage order through this connection.

This is a prepared integration, not a verified live feed or a profitable strategy. No account, subscription, API key or live entitlement was available during implementation. All automated examples and tests use fictional quotes. The older schema6/7 engines, their historical receipts and existing holds remain unchanged.

## Candidate cost and phone-number limitation

The candidate is Alpaca **Algo Trader Plus**, currently advertised at **$99/month** for the individual real-time data plan including OPRA options and consolidated SIP stocks. Pricing, eligibility and exchange agreements must be checked before purchasing. The free options feed is **indicative**, and the free stock venue is IEX; neither substitutes for this integration's explicit OPRA/SIP requests.

The initial data/paper account registration form has no phone field. This does **not** prove that subscription checkout, exchange subscriber classification, identity review or recovery will stay phone-free. That complete paid entitlement path is not verified. If any mandatory phone prompt appears, stop and choose another provider; do not use shared disposable SMS, false information or create a brokerage account to bypass it. No purchase or signup is performed by Ariadne.

Primary references: [market-data plans and coverage](https://docs.alpaca.markets/docs/about-market-data-api), [latest options quotes](https://docs.alpaca.markets/reference/optionlatestquotes), [latest stock quotes](https://docs.alpaca.markets/reference/stocklatestquotes), [account signup](https://app.alpaca.markets/signup).

There is no new model/API inference requirement for collection or qualification. Local compute, energy and storage costs are unmeasured. The data subscription is separate from Ariadne's runtime costs and does not imply approved spending.

## 1. Create a data/paper account privately

Use Alpaca's official site and your own email. Prefer authenticator-based MFA where offered. Review the market-data agreement and nonprofessional/professional classification accurately. Do not fund a brokerage account for this integration. Confirm the exact OPRA and SIP entitlements and the complete phone-free path before paying.

Generate the API key in Alpaca's dashboard. Although this adapter has only a fixed market-data host and read-only quote routes, a key may have broader provider-side permissions. The code allowlist is not a provider-enforced read-only key. Do not paste a key into chat, screenshots, GitHub, a JSON import or a command-line argument.

## 2. Save the key locally

Open PowerShell in the extracted Ariadne folder that contains `runtime`, `scripts` and `data`. These commands use the bundled Python; an equivalent already configured project Python can also run the script.

```powershell
.\runtime\python\python.exe -I -B scripts\alpaca_market.py setup
.\runtime\python\python.exe -I -B scripts\alpaca_market.py status
```

`setup` uses hidden prompts in an interactive terminal. It stores a CurrentUser Windows DPAPI envelope at `data/alpaca-market/credential.dpapi`; it refuses to overwrite an existing key. It does not contact Alpaca or change Ariadne settings. The envelope is specific to the Windows user and must not be included in a portable/public export.

On other platforms, private session variables `ARIADNE_ALPACA_KEY_ID` and `ARIADNE_ALPACA_SECRET_KEY` are supported as a complete pair. Never put their values in a shared script, shell history or repository. DPAPI export is not cross-user credential portability.

`status` reads existing initialized owner databases without creating tables or a bankroll. It reports the current cash projection and holds, not a newly verified account balance or current quote. Full retained financial-history verification and a future versioned paper migration remain separate requirements.

## 3. Inspect a fresh quote capture

Create `data/workspace/imports/alpaca-watch.json` with exactly `schema` and `instruments`. Include one to sixteen explicitly selected options and exactly their underlying stocks. For example, this is an identifier-format example, not a trade recommendation or guarantee that the contract exists:

```json
{"schema":1,"instruments":[{"symbol":"KO261120P00080000","type":"OPTION"},{"symbol":"KO","type":"EQUITY"}]}
```

During a regular US market session:

```powershell
.\runtime\python\python.exe -I -B scripts\alpaca_market.py collect --online --instruments imports/alpaca-watch.json --output reports/alpaca-first-capture
```

The command requests only fixed authenticated HTTPS quote endpoints at `data.alpaca.markets`. It explicitly requests `feed=opra` and `feed=sip`; an unauthorized entitlement, delayed/indicative substitution, response mismatch, rate limit or invalid clock fails closed. There are no retries, broker endpoints or automatic polling.

The new output folder contains raw capture evidence, normalized books and a hash/byte receipt. Existing or incomplete output folders are never overwritten or replayed. Inspect those receipts privately. Saved transport labels do not authenticate an imported JSON file.

## 4. Bind a diagnostic check to the existing paper account

```powershell
.\runtime\python\python.exe -I -B scripts\alpaca_market.py binding
```

Save the returned object as `data/workspace/imports/alpaca-request.json`. Preserve all five returned fields: `expected_revision`, `expected_account_id`, `expected_state_sha256`, `expected_policy_sha256`, `expected_owner_epoch`. Add `request_id` with a new local identifier and `requests` containing one to sixteen explicit objects with exactly:

```json
{"request_id":"candidate-one","contract_id":"KO261120P00080000","side":"buy","quantity":1,"price_limit":"1.04","allow_partial":false}
```

The object above is one row inside `requests`, not the complete request file. Prices and quantities require your own review; do not choose limits just to force admission. Caller decision clocks, duplicate request IDs, stale revisions or changed account/control hashes are refused before unlocking credentials or requesting quotes.

```powershell
.\runtime\python\python.exe -I -B scripts\alpaca_market.py qualify --online --instruments imports/alpaca-watch.json --request imports/alpaca-request.json --output reports/alpaca-first-check
```

Qualification collects fresh evidence in the same invocation. It never adopts a saved capture as live. It rechecks owner/control/account bindings after collection. Global pauses, disabled Internet, options holds and relevant briefing holds are preserved and block qualification. The separate QQQ hold is reported as a downstream blocker and is never cleared; quote-only collection may still inspect the data under enabled global/Internet controls.

The result says `evaluated_evidence_only`, with `admission_candidates` and `blocked_proposals`. An admission candidate is **not a paper fill**, a strategy promotion or a broker order. `paper_submission_ready` remains false, `paper_fills` is zero, and financial writes/funding are zero. Existing cash, contributions, fees, positions, gross commitment and receipt history are not changed.

## What the gate checks—and what remains missing

- The actual NBBO **message event** and separate actual receipt time must precede the command decision. Raw nanosecond clocks are retained; future nanosecond events cannot hide in a microsecond projection. The unchanged strict gate requires an event no older than five seconds within the same regular session. A freshly downloaded stale quote is still stale.
- Buys use the actual positive ask and known displayed bid/ask sizes for the full requested quantity; sells require a real positive bid and owned quantity. No midpoint, last-price fill, invented depth or automatic quantity reduction is used.
- Entry checks preserve the current ceiling: min($1,000, 4% of flat cash/equity, available cash, unused shared $25,000 monthly gross commitment), including modeled $0.65 fees on both sides. This bridge performs no allowance reset, proceeds recycling or top-up.
- The underlying must be a verified US stock/ETF below $100. The contract must have a verified standard 100-share deliverable. Exact twenty-prior-session dollar-volume observations, dated current-session contract volume and dated prior-session open interest must meet the existing floors.

The two latest-quote endpoints do not supply all of that activity and contract-reference evidence. Optional `--supplement imports/alpaca-metadata.json` can supply **only missing** independent metadata. The exact schema is `{"schema":1,"contracts":[...]}`; each contract row has `contract_id` and one or more of these fields:

| Field | Evidence required |
|---|---|
| `underlying_type` | `stock` or `etf`, supported by independent reference evidence |
| `standard_deliverable_verified` / `deliverable_verification_source` | Explicit boolean plus bounded primary-reference description; OCC-looking text alone is insufficient |
| `underlying_activity` | `sessions`: exactly twenty ordered prior regular sessions, each with `session`, `dollar_volume`, `published_at` |
| `contract_activity` | `volume`, actual `as_of`, and actual `published_at` |
| `open_interest` | `value`, prior `session`, and actual `published_at` |

Use aware event/publication clocks and decimal-string dollar volume. Unknown evidence remains missing; do not invent publication times from retrieval or a source date. Owner supplements are attestations, **not authenticated provider metadata**. They cannot replace quote prices, sizes, source/receipt clocks, underlying quotes or provider-supplied nonmissing data.

Alpaca's single NBBO message clock is not an independent last-change clock for each side. The Public schema7 durable ledger cannot accept it by relabeling the source. A separately reviewed versioned ledger with conservative depth consumption, exact ancestry and explicit current-epoch adoption is required before actual forward paper transitions. It has deliberately not been activated or substituted by this diagnostic bridge.

## Offline behavior and daily reporting

Fresh quotes require Internet, valid keys and current paid entitlements. Explicit `--online` cannot weaken inherited `ARIADNE_OFFLINE=1`, an already installed offline process policy or owner controls. Offline Ariadne can read saved evidence and daily reports, but cannot manufacture fresh quotes or financial results.

The daily brief reports connection readiness and blockers separately from the existing paper ledger. A prepared connector is not a connected feed. No completed historical brief is rewritten, no additional collector/scheduler is created, and no profit or successful paper trading is inferred from source code or test fixtures.
