# Service desk: real client workflow

Version 0.20.0 adds an owner-operated client workspace under **All systems → Service desk → Real clients**. It prepares paperwork for the businesses that perform the actual service. For example, your client supplies a maintenance job and its approved rates; you prepare its quote and invoice. The document's face value belongs to that business. Your own paperwork fee is separate.

New installations start empty. Opening the desk does not seed customers or create a document. **Fictional demo** opens the separate existing demonstration; its examples never enter the real client tables or operational business ledger.

## Complete a job

1. **Save the service client.** Enter the business issuing the paperwork, its contact and billing address. Each intake copies an issuer snapshot. Later profile edits do not rewrite old records. Archive a client to stop new intake; restore it explicitly when appropriate.
2. **Capture a customer job.** Enter that business's customer, original notes, document/service/valid-through dates, service lines, supplied rates, rate references, explicit tax instruction, and permission or engagement reference. The saved original intake is immutable. Blank rates or tax remain unknown; zero tax requires an explicit sourced instruction.
3. **Generate the quote.** Review the printable preview and any missing-input holds. Correct the quote's inputs when necessary; those corrections create a new immutable document revision. Refreshing issuer details from the current profile is an explicit quote correction.
4. **Approve the exact draft.** The owner's review binds the document content hash. Rejection can record a correction note. An edit clears the active review and customer response, requiring a new review. Approval does not establish a customer's agreement.
5. **Record the customer's actual response.** Supply accepted or declined, contact, observed time and evidence reference. This is an owner-reported observation, not an authenticated customer signature. Acceptance must refer to the current approved quote and fall within its document/valid-through dates.
6. **Record completed work and generate the invoice.** Supply the actual completed-service date, invoice/due dates, completion reference and explicit completion checks. The invoice copies the accepted quote's issuer, customer, prices and tax snapshot. Only dates, notes/title and work evidence can subsequently be corrected on the invoice. Invoice date must not precede its quote or the recorded acceptance day; completed service cannot follow the invoice date or today. Response timestamps are normalized to UTC; date checks use America/Chicago.
7. **Review the invoice separately.** The quote's approval does not approve its invoice. Missing completion facts or invalid dates hold the invoice. A current approved invoice can be exported as a script-free printable HTML draft and a private JSON evidence file. Export readback verifies both file hashes.

The full demonstration uses fictional **2.5 hours × $35.00 = $87.50** with an explicitly supplied zero-tax instruction. It is workflow evidence, not a real customer, completed job or earned revenue.

## Corrections and approval controls

Only one active quote per intake and one active invoice per quote are permitted. An active invoice locks its source quote against editing, re-review or voiding. If an unsent invoice is wrong, explicitly **Void unsent draft** with a reason. Its reference and history remain; the quote can then be corrected, reviewed and accepted again before generating a replacement with a new reference. Voiding a local draft is not a credit note, refund or recall of a copy shared outside Ariadne.

Every write supplies a unique request ID and the workspace revision. Stale revisions and stale document hashes fail instead of overwriting someone else's newer work. Identical completed requests return their original compact receipt. A running or failed export creates a hold: inspect it, explicitly acknowledge it, and use a new request for any later export. Ariadne never adopts or retries partial output automatically. Browser transport uncertainty similarly requires inspection before another write.

The global autonomy pause blocks client mutations and exports, while passive reads remain available. Bearer authentication, same-origin and allowed-host checks apply to all client APIs. These are logical client records in a single owner workspace; there are no customer logins, separate approver identities, tenant-isolation guarantees or electronic-signature verification.

## Exact math and operating cost

Quantities use positive decimal strings with up to three fractional places, at most 10,000 units per line. USD rates use exact cents. Each line rounds half up to cents, then the supplied integer basis-point tax applies to the summed rounded lines and rounds half up. A source instruction is required even for zero tax. Documents allow 1–50 service lines and a bounded total. Missing approvals or required source facts prevent a confirmed total and approval.

The workflow runs fixed local Python/SQLite operations. It makes **zero model and network calls**, adds no runtime dependency, and does not require a recurring license or API subscription. This does not measure the whole PC's electricity, internet or existing AI subscription costs. Compact receipts and per-document versions avoid duplicating every client's entire history on each action.

Current hard caps are 200 client profiles, 10,000 intakes, 20,000 documents, 100,000 request receipts, 128 MiB of entity/version data, 64 MiB of receipt data and 128 MiB of lifetime reserved export data. The byte caps may be reached before the count caps. Lists are paged at up to 50 entries; client filters operate on the server. Selected documents show the latest 20 version summaries and latest 20 events, plus total retained versions. Older immutable versions remain in the database. Automatic archival/deletion is not implemented; a reached cap stops further work for reviewed archival rather than silently deleting history.

## Private storage and handoff

Client tables live in the protected main Ariadne SQLite database. Approved files live under `data/workspace/service-desk/<request-id>/`. Canonical Ariadne backups include the tables, version history, receipts and workspace files. Model weights, runtimes and OS tasks retain the backup utility's existing exclusions.

**The JSON handoff includes private source notes and the copied issuer profile, including its private notes.** Review it before sharing. The HTML document includes source/evidence references; inspect those as well. Neither file is automatically sent, posted to accounting, marked paid, or treated as Ariadne revenue. Actual customer evidence, tax treatment and legal sufficiency remain owner-supplied facts.

## Qualification scope

Backend/API/UI regressions cover fractional arithmetic, complete lifecycle, invalid chronology, stale approval, profile snapshots, rejection, void/replacement, pagination, auth/origin/host guards, pause, request replay, interrupted exports and hostile text. Independent probes also compare 350 money cases against a rational-arithmetic reference, exercise 160 jobs over multiple pages, and verify canonical backup preservation.

Fresh-folder qualification copies source and static assets, constructs an isolated WSGI application, and exercises intake through approved invoice/export with network and native child-process attempts blocked. Model calls and Voice construction are stubbed. This validates the deterministic client workflow, not microphone/model behavior or a physical Wi-Fi outage. The full portable runtime ZIP remains separately qualified at 0.16.1; this source update does not relabel it.

Workflow design keeps quotations and draft invoices separate from ledger posting, consistent with the distinctions described in the primary [ERPNext quotation](https://docs.frappe.io/erpnext/quotation) and [sales invoice](https://docs.frappe.io/erpnext/sales-invoice) documentation. Ariadne does not install ERPNext or claim an accounting integration here.
