# Stress-filtered put paper policy

Version 0.30 adds an explicitly activated, experimental Ford (F) stress-filtered breakdown-put policy to the existing options paper account. It changes no brokerage account and places no real orders. Historical results do not automatically activate this policy or identify a proven winner.

## One account and unchanged limits

Activation requires the owner route, current revision and both simulated-money and stress-only acknowledgements. Only a healthy, unspent, flat schema-3 EOD account can migrate. The account ID, cash, contributions, month, gross commitments, source prefix and previous receipts stay intact. Activation adds no funding and resets no budget. Existing accounts retain their original rules until activation.

The sole shared gross monthly commitment allowance remains **$500**, with **$100 maximum per entry including premium, entry fee and reserved exit fee**. This policy permits one fully paid standard 100-share long put, with no short options, borrowing, exercise or recycling of proceeds into the monthly allowance. The underlying must be below $100 at both decision and fill. Existing exits and missing-quote holds remain binding.

## Offline context and causal entry checks

The local scheduler derives a small, hash-bound context witness from the already accepted QQQ/SPY benchmark data. This step uses no model, network call or new service. Unchanged accepted data takes a cheap no-op path. The source receipt, prepared witness and sealed witness are retained separately, with a new local publication time. These are normalized-data evidence; the normal benchmark refresh does not retain an original raw response bundle.

New put decisions require fresh `stress_unwind` context from the fixed classifier. A subsequent BUY also requires the appropriate previous-session context to have been published before that source session opened. A context collected after the fact cannot authorize an earlier source session. Missing, stale, non-stress, held or uncertain context denies new exposure. Ordinary context unavailability does not disable position management or exits. Corrupted retained financial evidence remains a substantive integrity failure.

The account retains a bounded gate hash chain. Full mutation and journal audits bind every retained schema-4 gate to the host's immutable published context files, verify exact policy extensions across accepted source transitions, and reconcile the final policy with the current account. A frame that begins with pending work or a position only manages that exposure; a new decision waits for a later source frame.

Context capture has fixed five-second and storage bounds. An interrupted or failed capture is retained for review and is not automatically replayed. An unresolved benchmark history receipt blocks new context and entries. Existing pauses, authority epochs, source configuration and approval controls apply throughout.

## Quotes, scheduling and results

The existing free, no-account F EOD collector checks after 9:35 AM Eastern on a market session. Its partial option chains lack a verified quote-event timestamp. Simulated fills remain **experimental price proxies, not execution-qualified prices**. No qualifying quote or gate means no trade. Offline operation supports saved context, paper accounting, research and brief rendering; acquisition of new market data requires connectivity.

The Options panel and the next daily brief identify the active stress-only policy, saved gate state, fees, committed budget and actual paper results. A completed earlier brief is immutable and is not rewritten by activation.

## Longer free historical research

The October 31 research acquired pinned public repository archives reporting IWM options for 2008 and IWM/QQQ options for 2011. Conversion retained 253 IWM sessions in 2008 and 197 common sessions in 2011. Reported historical underlying closes are below $100; that does not establish current eligibility or an authenticated raw, as-traded price basis.

The repository asserts an MIT license, but original quote providers, upstream data rights, publication vintages, deliverables and price adjustments remain unverified. Acquisition in 2026 and repository creation in 2025 cannot establish historical availability. These archives are **provisional research only**. Raw data and private reports are excluded from Git and portable-source exports.

The preregistered comparison keeps strict-size and unknown-size assumptions separate, retains missing-exit holds, and compares fixed recovery, breakout, breakdown, stress and cash candidates without tuning against these outcomes. Individual accounts and the pooled shared ledger are separate experiments; their P&L must not be summed. Held marked values are excluded from completed-result rankings. More observations, authenticated data and forward paper evidence are needed before drawing conclusions about profitability.

Source repositories: [original preserved history](https://github.com/waterscep/sks-options-dataset-hist), [public mirror](https://github.com/anahatsingh-ui/options-dataset-hist). Repository availability is not verification of its market data.
