# Options book review and liquidity checks

**Source-only addition:** this guide describes the repository source tools. The last packaged 0.37.0 starter does not include this command yet, and an existing running service does not acquire a new paper policy from these files. Use a checkout that contains `scripts/options_book.py`.

Review imported US stock/ETF option quotes before treating a hypothetical entry or exit as priceable. `ariadne/options_liquidity.py` is a separate deterministic research gate; `scripts/options_book.py` reads local JSON and saves a review. It adds no broker connection, live data feed, financial ledger, background worker or automatic strategy promotion.

A passing review is a bounded top-of-book proposal, not an executed order or evidence of a profitable strategy. Every proposal keeps `execution_confirmed`, `provider_authenticated` and `portfolio_controls_checked` false. The gate cannot authenticate a provider, establish that an order would receive the displayed price, or replace the existing account's capital and approval controls.

## Use the correct side of the book

- A proposed **buy** uses the supplied **ask** and ask size. A proposed **sale** uses the supplied **bid** and bid size.
- A caller supplies an explicit limit price. A buy cannot pay above it; a sale cannot accept less. The gate never widens that protection or changes a trading limit to make a proposal pass.
- A midpoint, last trade, chart candle, theoretical value or strategy stop/target is not a substitute fill price. A trigger may explain a request; only the admitted quote side can price the proposal.
- Displayed size is finite. The same supplied observation cannot be reused through another snapshot identifier or source alias to multiply its depth within a review. A fresh review is not proof that the same liquidity remains available.
- A partial sale is considered only when the caller explicitly permits it. An insufficient-size buy is not silently converted into a smaller purchase.

This is top-of-book accounting, not a complete order-book simulator. It has no hidden-depth model, queue position, routing, auction, guaranteed execution, price improvement, exercise or assignment model. Prices and sizes can change before an actual order reaches a market.

## Fixed engineering admission rules

These initial values are fixed engineering defaults, not thresholds fitted to the saved profits or a claim that they guarantee enough liquidity.

| Check for a new buy | Required evidence |
|---|---|
| Underlying liquidity | Twenty prior completed stock sessions with average daily dollar volume of at least $25 million. |
| Option volume | At least 500 contracts of same-day volume, with its observation time no later than the decision. |
| Open interest | At least 1,000 contracts in the latest prior-session observation, available by the decision. |
| Spread | Positive uncrossed bid/ask; spread no more than both 5% of midpoint and $0.10 per option share. |
| Freshness | The option quote is no more than five seconds old at the supplied decision time. A buy also needs a fresh underlying quote below $100. Future observations do not qualify. |
| Trading clock | A supported regular US exchange session and its actual calendar, including holidays and early closes. |
| Contract identity | An explicit US stock/ETF option identity and caller-supplied verification/attestation of a standard 100-share deliverable. |
| Price and quantity | The explicit limit admits the actual ask, and both supplied ask and bid sizes cover the entire requested integer quantity. The gate never clips a buy to fit smaller depth. |

Dated volume and open interest answer different questions; neither substitutes for the supplied current bid/ask depth. A large open-interest figure does not mean an equally large executable quote. A narrow spread does not establish a fill probability.

Sales use the actual bid, size, freshness and caller's unchanged minimum price. A missing ask is not automatically a reason to block a valid protected sale; a supplied ask still must be valid and uncrossed. They are not blocked merely because a new-entry volume, open-interest or spread threshold fails. If a valid protected sale cannot be modeled, the review records the refusal; it does not invent a midpoint sale, discard the remaining quantity or assume zero settlement.

The caller's standard-contract attestation is input evidence. This module cannot independently authenticate the exchange, quote publisher, corporate-action history or deliverable. Unverified or missing required evidence remains a reason to refuse, rather than a fact inferred from a familiar ticker.

## Run it offline

Use the bundled runtime from the Ariadne folder. Both commands read workspace-relative JSON and reserve a **new** report folder:

```powershell
runtime\python\python.exe -I -B scripts\options_book.py review --workspace data/workspace --input imports/options-book.json --output reports/options-book-review-001
runtime\python\python.exe -I -B scripts\options_book.py audit-legacy --workspace data/workspace --input imports/legacy-options.json --output reports/legacy-options-review-001
```

`review` accepts a strict schema-1 object containing `books` and `requests`. Each request has its own recorded `decision_at`; the five-second age test is relative to that decision, not proof that an old file is fresh now. The command records its actual registration clock and refuses a future decision time. There is no `--now` or backdating option. A historical review remains a recorded-book replay proposal, never an assurance that its quotes are currently available. Use exact contract identities, observed/available timestamps, dated liquidity measurements, supplied prices/sizes and explicit order protection. Do not pass account passwords, broker tokens or executable instructions as quote data.

### Input fields and a fictional replay

A review has exactly `schema`, `books` and `requests`, with one to 256 items in each list. Each book and request below shows the complete required field set; nullable missing observations remain `null`. Prices and dollar amounts are decimal strings, not floating-point JSON numbers or exponents. Sizes, quantity, volume and open interest are nonnegative JSON integers; booleans cannot stand in for numbers. Times are aware ISO timestamps with seconds.

Requests need unique identifiers, nondecreasing `decision_at` clocks and an exact supplied `source_id` / `snapshot_id` / `contract_id` match. Reusing one contract and actual event time through source or snapshot aliases shares its displayed depth. Conflicting quote bodies for that event refuse the complete batch. Opposite sides consume their own displayed size. Buy proposals also reserve their supporting bid depth across other buys in the same batch; that entry-coverage reservation does not represent a sale or block a separately valid sell request. This module has no durable cross-batch consumption ledger.

For buy admission, the nullable evidence objects use these exact shapes:

| Object | Fields |
|---|---|
| `underlying_quote` | `price`, `quote_at`, `received_at`. |
| `underlying_activity` | `sessions`: exactly twenty consecutive prior completed-session objects, each with `session` (`YYYY-MM-DD`), `dollar_volume` and `published_at`. The publishing clock cannot precede that session's close or follow the decision. |
| `contract_activity` | `volume`, `as_of`, `published_at`; the as-of clock must be in the decision session and available by the decision. |
| `open_interest` | `value`, `session`, `published_at`; the session must be the latest completed prior session and its value must have been available by the decision. |

Save the following as `imports/options-book.json` inside the chosen workspace. **Every value is fictional.** It demonstrates a recorded protected sale with missing entry-only metrics, not an owned position, an authenticated quote or a current opportunity. Its supplied verification text is only a caller attestation. The command must run after the example's recorded decision time.

```json
{
  "schema": 1,
  "books": [
    {
      "schema": 1,
      "source_id": "fictional-example",
      "snapshot_id": "recorded-sale-001",
      "quote_kind": "top_of_book",
      "underlying_symbol": "KO",
      "underlying_type": "stock",
      "contract_id": "KO261120P00060000",
      "expiry": "2026-11-20",
      "type": "put",
      "strike": "60",
      "multiplier": 100,
      "standard_deliverable_verified": true,
      "deliverable_verification_source": "Fictional caller attestation for this example only",
      "bid": "1.05",
      "ask": null,
      "bid_size": 3,
      "ask_size": null,
      "quote_at": "2026-11-02T15:30:00Z",
      "received_at": "2026-11-02T15:30:00.100Z",
      "underlying_quote": null,
      "underlying_activity": null,
      "contract_activity": null,
      "open_interest": null
    }
  ],
  "requests": [
    {
      "request_id": "fictional-sale-request-001",
      "source_id": "fictional-example",
      "snapshot_id": "recorded-sale-001",
      "contract_id": "KO261120P00060000",
      "decision_at": "2026-11-02T15:30:01Z",
      "side": "sell",
      "quantity": 2,
      "price_limit": "1.00",
      "allow_partial": false
    }
  ]
}
```

Under these supplied facts, the expected proposal is two contracts at the recorded bid of `1.05`, leaving one displayed bid contract within this batch. There is no fee calculation, cash credit, inventory debit or order. Raising `price_limit` to `1.06` refuses that proposal without changing the price. Changing the request to `buy` leaves the ask and dated entry evidence missing, so it cannot produce a buy proposal. Use separately reviewed new inputs and output folders for deliberate variants.

### Results and publication

Open the new folder's `report.html` for a self-contained offline report. `review.json` retains all proposal/refusal rows and depth consumption; `completed.json` binds the input and output fingerprints. `attempt.json` identifies the reserved publication. A completed review can contain only `no_fill` rows: completed publication is not approval, execution or a successful trade.

The input limit is 4 MiB; review output is bounded to 8 MiB. The legacy audit accepts at most 10,000 snapshots and 20,000 quotes. Invalid structure, duplicate request identifiers, contradictory aliases or malformed prices stop the review rather than produce a partial successful batch. Missing admissible observations and failed gate conditions remain visible in the returned refusal reasons.

`audit-legacy` inspects an older supplied data representation and reports the gaps that prevent strict book admission. It does not upgrade date-only history into a timestamped feed or manufacture missing sizes and liquidity measurements.

Existing output, incomplete publication or an uncertain receipt must be inspected. The command does not overwrite the folder or replay the request to make a failed review appear successful. A new output name is for a separately reviewed new attempt, not a way to repair or reinterpret retained evidence silently.

The tools use local imported files with no model, network call, new dependency, paid subscription or resident worker. Obtaining fresh market data remains separate and may have provider terms or costs. No new reliable free, no-account live feed is established by this feature. Compute and electricity costs have not been measured.

## Relationship to Ariadne's trading studies

The existing KO source contains partial, date-only end-of-day observations and does not establish these dated live-book and liquidity measurements. It therefore cannot be passed off as a five-second executable option book. Its original captures, financial rule hashes, strategy results and holds remain unchanged. Historical fill assumptions remain visible in their own reports.

This addition does not migrate or enable the active paper strategy. The experimental mean-reclaim call is still a research candidate; the frozen earlier selection remains cash. Passing the book gate alone neither promotes that candidate nor establishes a positive expected return.

The gate does not read an account or verify its available cash, risk allowance, inventory, pending orders or fees. Existing funding, entry, monthly commitment, drawdown and approval controls remain separate and unchanged. A sell request must also be checked against actual held quantity before any real action; a supplied request is not proof of ownership. No liquidity result authorizes a budget or inventory override.

Use [the options experiment evidence](OPTIONS_EXPERIMENTS.md), [the current twelve-month study policy](TRADING_STUDY_WINDOW.md) and [the existing paper bankroll contract](OPTIONS_BANKROLL_0_33.md) together. Saved local research can work offline; current broker decisions require fresh qualified observations and the account's separate controls.

## Primary reference context

The engineering thresholds above are Ariadne's initial policy choices, not requirements attributed to an exchange or broker. Provider definitions and execution documentation explain the source fields and their limits; they do not certify this imported JSON or an eventual fill.

- [OIC: trade entry and execution](https://www.optionseducation.org/referencelibrary/faq/trade-entry-execution).
- [OIC: general options information](https://www.optionseducation.org/referencelibrary/faq/general-information).
- [Robinhood: options-chain metrics](https://robinhood.com/us/en/support/articles/options-chain-metrics/).
- [Robinhood: options limit orders](https://robinhood.com/us/en/support/articles/limit-order-options/).
