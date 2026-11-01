# Working recent options observations and matched research

November 1, 2026. A bounded, free, no-account **personal-research** acquisition completed: **125 KO sessions, May 4–October 29, 2026, and 4,000 actual returned call/put quotes**. It made 124 new anonymous historical GETs and reused the complete May 4 HTTP200 response offline, preserving its original receipt. Both pre-registered comparisons then completed without network, model or paper-account operations.

This is roughly six months **inside** the owner's last-twelve-month window, not twelve months of captured options history. The separate November 2025 availability request timed out; first-half coverage remains unverified. No older crisis-era study was rerun. October 30 was excluded from both paired studies before acquisition because its next-session availability assumption would fall on November 2, after registration.

## Results and paper disposition

Both hypothetical timing profiles produced the following same modeled results. Each candidate/profile is a separate $25,000 one-time counterfactual; do not sum their capital or returns.

| Candidate | Closed trades / entries | Realized modeled P&L | Last dated net trading mark | Completion |
|---|---:|---:|---:|---|
| Stress-filtered puts | 0 / 0 | $0 | $0, October 29 | Complete; no trades, not evidence of an edge |
| Recovery-growth calls | 1 / 2 | **+$1,214.10** | +$744.60, **September 10** | **Held September 11; incomplete** |
| Unfiltered breakdown puts | 1 / 2 | -$758.10 | -$874.90, **August 12** | **Held August 13; incomplete** |
| Cash control | 0 / 0 | $0 | $0, October 29 | Complete |

Recovery's September 11 contract was present, but its actual recorded bid size was **4 against a 15-contract position**. The model could not mark/dispose of the whole position under its unchanged full-size rule. The raw-put contract was also present and had sufficient size, **161 against 16 contracts**, but its bid/ask spread was **$0.07**, exceeding the unchanged 25%-of-midpoint limit of **$0.06875**. The common stored hold code covers several conditions; these are the independently verified exact causes.

The held marks are not October ending returns or completed gains/losses. All 125 source days were still exhausted for evidence validation, while those ledgers stopped financial transitions at their holds. Neither timing profile has a complete active-strategy ranking or an adequate independent trade sample. **No strongest deployable strategy was established, and no recovery-call promotion or historical position adoption occurred.**

The existing schema6 KO forward paper experiment stays separate, with one $25,000 bankroll, no top-ups, a shared $25,000 monthly gross-commitment ceiling, and all-in entry at most min($1,000, 4% of equity, available cash, unused commitment), including both fees and actual side sizes. Its put-only policy is unchanged. Fresh eligible observations and current permissions govern future simulated activity; this research cannot turn an archived gain into an order or bypass a hold. New recovery-call execution would need an additive reviewed causal policy, same-account zero-funding transition and suitable prospective evidence.

## What the working source supplies

[OnClick's published tier](https://www.onclickmedia.com/pricing) advertises anonymous historical EOD access. The successful individual historical requests returned actual dated option rows with OSI identities, bid/ask, sizes, volume and open interest. All 125 snapshots were partial: 16 returned strikes with both option types, not full chains. No additional strikes, paid level, credential, account or month-batching workaround was used. Event/publication clocks, upstream provenance, standard 100-share deliverables and actual execution quality remain unverified.

**96 quotes lacked each side size**, across three source days. Those values remain `null`; they are never replaced with zero, volume or open interest. The historical canonical schema permits unknown observations, while unchanged strict trading rules make missing-size entries ineligible and require sufficient actual whole-position exit evidence. Presence of non-null sizes in other rows does not establish a reliable executable feed.

A separately supplied local close reference came from the explicitly published personal-research CSV described by [Half on a Double](https://halfonadouble.com/data). Its [terms](https://halfonadouble.com/terms) permit polite automated personal research, while product/service integration and data redistribution require separate permission. **No ongoing Half on a Double feed was added to Ariadne.** Its raw data remains private and is not in GitHub or a distributed package.

The full retained CSV contains 250 observations on the requested year's exchange sessions, but its **global calendar qualification failed** because it also contains a Thanksgiving-dated row, November 27, 2025. Fourteen of fifteen overlapping close values also differ from the saved minute-close reference, including substantive differences beyond floating-point representation. No unadjusted-price guarantee was established. These failures remain explicit; this is not a replacement for the owner's source.

A separately versioned projection admits exactly the predeclared 125 study sessions from that one whole generation. Every parent date/value is validated; an invalid calendar label inside the inclusive study range rejects the source. The outside-range holiday row is retained as an unusable anomaly and cannot enter a price map, context, warmup, trade or metric. Original and derived hashes are distinct. The original decimal close text is retained with its declared eight-decimal HALF_EVEN representation and an audited maximum conversion error of `5e-9`; there is no cent rounding, source-equivalence repair or splicing.

Two retrospective availability assumptions were fixed before comparison: close + 15 minutes and next-session 09:35 New York. Actual receipt clocks remain unchanged and distinct. Neither profile replicates the complete forward execution contract; their agreement is sensitivity evidence from shared inputs, not an independent validation of profitability.

## Implementation and verification

The current research-only entrypoint is `scripts/options_historical_capture.py`, backed by `ariadne/options_historical_reference.py`. It joins **local, hash-pinned reference inputs** to one predeclared monthly option expiry per source day. It does not fetch the reference provider, access a brokerage, choose a winner, modify a paper account or accept arbitrary dates, tickers or URLs. Limits remain 128 new GETs, 16 MiB wire, 24 MiB output, 600 seconds overall and 15 seconds per GET, with read-only master-Internet/pause authority, immutable source leases, per-body validation and final atomic integrity checks.

The original strict reference normalizer and projection remain unchanged for their earlier receipts. The new historical importer accepts explicit null sizes as unknown; the existing live/forward parser still rejects them. Failed strict imports and raw receipts remain preserved; the complete May 4 response was reprocessed offline rather than requested again. Substantive financial failures are never replayed or healed through a new request ID.

The unchanged bankroll comparison engine uses identical sources/dates/fees and the current risk calculation for stress puts, recovery calls, unfiltered puts and cash. Source gaps, missing contracts, inadequate size, wide spreads and failed disposal stay visible. No quote prices or sizes are rounded, fabricated or inferred.

**168 targeted tests passed in a fresh source copy**, with zero failures/errors/skips, four guard positive controls and zero prohibited network/process/database/original-project accesses during tests. This is code qualification with the original bundled Python, not a physical Wi-Fi outage test or rebuilt full-model portable ZIP. An independent, offline recorded-evidence audit passed **63,665 checks**, including all raw-to-canonical quotes, source/receipt/artifact hashes, twelve recorded fills across both profiles, 820 curve rows, fees, funding and risk bounds. The largest modeled all-in entry was $964.50.

Actual acquisition took 71.940 seconds and 1,552,107 wire bytes. The two offline comparisons took 15.442 and 15.200 seconds. No model/API subscription, dependency or resident service was added. Recurring fees added are $0; energy and maintenance savings are unmeasured. Provider bodies, user records, pairing tokens and private evidence remain outside GitHub.

Private local evidence is retained under `staging/options_working_source_2026_11_01/`: `paired-quotes-v3/completed.json`, both `studies-v3/*/completed.json` and `results.json`, `independent-results-audit-v1/receipt.json`, and `fresh-source-v1/completed.json`. These establish observations and modeled outcomes, not actual investment income.
