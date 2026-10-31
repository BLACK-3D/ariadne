# Business comparison and options evidence — 0.27.0

Open **All systems → Venture Lab**. The Revenue workbench compares a POD product with the existing quote/invoice administration service, using an illustrative scenario with a $100 monthly systems planning budget, 10–20 weekly hours and a $2,000 monthly cash-surplus target before operator compensation and personal taxes. Those are planning inputs, not measured costs, sales or promised income. No investment return is budgeted to cover operating bills.

## Compare before choosing

Use **Load illustrative comparison** to populate editable example assumptions. This only fills the form. **Calculate and save comparison** is the explicit action that accepts and hashes a scenario, produces an immutable report and selects it for inspection. No product, account, customer, listing or payment is created. Example prices and costs are hypothetical, not current supplier quotes or validated demand. Assumed volume begins at zero.

The planner calculates contribution after production, shipping, other variable costs, payment percentage/fixed fees, refund reserves and acquisition costs. Percentage costs round up to cents per unit. The payment-fee base is retained gross customer revenue before refunds; pass-through sales tax belongs outside unit revenue. Unknown inputs stay unknown. Explicit zero is different from an empty field. An unknown actual system cost uses the planning budget once, visibly; a supplied actual replaces that budget instead of being added to it. Unknown other fixed costs prevent a complete monthly target calculation.

Cash surplus excludes owner compensation and personal taxes. Economic surplus also subtracts the declared value of delivery and fixed sales/admin hours. Capacity uses exactly 52/12 weeks per month for each hours profile. Missing time assumptions do not imply free labor or unlimited capacity. A zero per-unit time input is only a declared lack of a unit-time constraint. These are scenario calculations, not accounting, demand validation or tax conclusions.

Funding mode distinguishes merchant-fronted fulfillment from production/shipping deducted from customer payments. Advertising and other separately paid variable costs can still need cash. Working-capital estimates assume uniform sales over a 30-day month and the supplied payout lag. A nonzero volume has at least a one-unit sequencing buffer even when lag is zero. Actual payout thresholds, holds, uneven sales, refunds, fixed expenses and account eligibility are not established by that estimate.

Primary sources checked October 30, 2026 identify useful customer-funded routes:

- [Printful Quick Stores](https://help.printful.com/hc/en-us/articles/50265763155217-How-much-does-using-Quick-Stores-cost) describes no setup/monthly fee or starting billing method, customer checkout, fulfillment deductions and monthly earnings payouts above $25.
- [Printify Pop-Up](https://help.printify.com/hc/en-us/articles/12051908094993-How-will-my-Pop-Up-Store-earnings-be-paid-out) describes production/shipping deductions, prior-month payouts on the 15th, Stripe verification and possible additional first-payout delay.
- [Ordinary Printify channel integrations](https://help.printify.com/hc/en-us/articles/4483601124113-How-does-the-payment-process-work) require the merchant to pay production/shipping separately while the sales channel controls payout timing.

These are dated provider descriptions, not permanent eligibility, price or margin guarantees. No account is created or connected. Obtain the actual SKU, print-location, shipping, checkout, return and acquisition assumptions before relying on a plan. The standalone generic service calculator remains available at `scripts/venture_economics.py`; this workbench adds multi-offer POD/funding comparison and a web evidence trail rather than replacing that tool or the existing Service desk.

## Record outcomes separately

The evidence form records **your own business**, not a client's invoice face value. Supported owner-reported events are interview, pilot agreement, sale booked, payout received, separately paid cost, refund paid and actual owner time. Monetary values are explicit USD decimal text. A booked sale is not received cash. A net payout must not have provider-withheld fees deducted again as a separate payment. References are optional bounded Workspace files, copied and hashed privately; their existence does not authenticate consent, payment or a bank balance.

The cash panel groups occurrence timestamps by **UTC month** and reports payouts minus separately paid costs and refunds. It keeps booked sales separate and never labels that number verified profit or a complete cost ledger. It does not automatically subtract the systems budget, because an already-recorded cost might include it. Costs and bank records still need reconciliation. Missing or damaged evidence yields unavailable values instead of zero profit.

Plans and events use strict authenticated same-origin APIs, revision checks, one owner lock, global pause epochs, retained raw inputs, exclusive report paths and completed receipts. Old and newly accepted evidence is checked before a mutation commits. Failed/interrupted work stops further changes. Inspection acknowledgement never adopts, repeats, replaces or deletes uncertain work. Limits are 100 shared workbench requests, 16 MiB retained input and 64 MiB reports; failed reservations remain charged. No automatic deletion or quota increase is introduced.

## Review paper evidence

**Paper evidence journal** reads the existing single Ford account and its verified source prefix. It neither collects quotes nor advances the book. Saved trading P/L excludes contributions. Fees, entries, closed proxy outcomes, a dated P/L curve, monthly commitments and fixed strategy attribution remain separate. Peak-to-trough trading-P/L decline measures drops from saved marks and is different from the $500 gross monthly commitment cap.

Latest-session diagnostics evaluate the three existing hypotheses at the original recorded decision clock. They show signal state, the returned contracts inspected, eligible counts and bounded rejection reasons. Those counts do not describe the full market, create a recommendation, choose a winning strategy or grant risk authority. The journal retains source date, actual receipt and actual decision separately; option quote-event clocks remain unknown. Before the first accepted option capture, there are no quote diagnostics or observed win rate. Thirty or fewer closed proxy positions are explicitly an insufficient sample; larger samples still do not establish executable returns or a profitable edge.

The collector's versioned `onclick_actual_receipt_month_candidate_v2` policy now prefers the nearest-14-DTE candidate within the **actual metadata receipt's America/New_York month**, using only the returned common call/put expiration list. It always includes exact held/pending expiries. If no in-month candidate exists, the previous global-nearest candidate is retained for observations; the engine can still reject every entry. A historical October source collected in November uses the November receipt month. No backdated decision or outcome-based parameter search is performed.

The source ID, snapshot keys and frozen paper-engine rule hash remain unchanged. The five-request, 15-second worker and 8 MiB wire bounds, one account, $100 all-in entry limit, below-$100 underlying gate and shared $500 monthly non-recycling commitment cap remain unchanged. Delayed-fill DTE, month, liquidity and mandatory-disposal gates still apply. Partial EOD quotes and unverified source reliability/execution timing remain limitations. Old captures retain their original provenance and are not renormalized under the new source policy.

The coordinated owner reload must occur before the new child collector is used: an old parent correctly rejects a bundle whose selected expiry differs from its cached policy. Updating the files alone is not a qualified live upgrade.

## Briefs and offline operation

New daily briefs include the saved business comparison, owner-reported cash context, next evidence step and compact paper-journal status. Reading or generating a brief never creates a business event, sale, payment, quote capture or trade through these views. Completed earlier briefs remain unchanged. Reports, calculations and review work offline; new market observations still require the already-enabled source and its existing permissions.

No new dependency, language-model call, hardware control, subscription, broker or background scheduler is required. Tests use generated fixtures rather than investment-performance evidence. Fresh-copy qualification blocks networking, models, owner data and all native launches except the exact copied UI fixtures. That scope is separate from a physical Wi-Fi outage or a newly verified model-inclusive portable bundle.

The interrupted AERIS recording lab is preserved under private staging and removed from the active source tree. It is not part of this release.
