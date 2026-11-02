# Document inbox and next actions

The Service desk connects a local customer document to a reviewable job intake. Source import extracts bounded text, proposes fields from explicit labels and retains the exact source snapshot and fingerprints. For less structured paperwork, you can separately ask the configured local model for source-checked suggestions. You review either path before saving an ordinary intake. Quotes and invoices continue through their existing separate approval controls.

Open **Service desk → Real clients**. This is the owner-operated workspace, separate from **Fictional demo**. Only use records you are permitted to handle. No customer login, email connection, signature verification, delivery, accounting posting or payment collection is added.

## From a document to a draft

1. Save or select the service client's business profile. This is the business delivering the work, whose issuer details belong on its quote.
2. Open **Document inbox · turn a source into a reviewed intake**. Choose one local document and select **Read selected document**. Choosing the file alone does not upload it.
3. Review the retained text, extraction warnings and **Label-based suggestions**. Each candidate displays the exact quoted source line, character span and quote SHA-256. Line numbers and character offsets belong to the extracted snapshot, not the original PDF's physical layout. Separate fingerprints identify the original bytes, extracted text and immutable packet.
4. Select **Review suggested intake**. Check or edit each field against the source. Missing or ambiguous facts stay unknown; the placeholder title/scope and quantity of one also need review. These initial suggestions use deterministic label matching. Optional model-assisted review is a separate action described below.
5. Confirm **I checked this intake against the retained source text and its extraction warnings**, then select **Save reviewed intake draft**. Editing a fact or adding/removing a service line clears that confirmation. Saving creates one ordinary intake linked to the source packet; it does not create or approve a quote automatically.
6. Select **Generate quote draft**, resolve the quote's sourced price, tax, permission and date gates, and review that exact quote. Record a genuine customer response separately. Completed-work evidence and invoice dates remain necessary before generating an invoice draft. Invoice approval and export are separate steps.

Imported text never supplies commercial approval. All price-approval, tax-approval and permission-confirmation flags are reset to false when a reviewed intake is saved, even if the document says “approved.” Source review means you checked the transcription and facts; it does not authenticate the sender or establish agreement, consent, completion or payment.

### Try a fictional text file

For a private validation, create a clearly fictional service-client profile and upload the [ready-to-use fictional sample](examples/client-inbox-demo.txt), or save the following as `fictional-request.txt`. Use the inbox in **Real clients** for this test; the separate fictional quote demonstration does not share its state.

```text
Title: Fictional repair visit
Customer: Example Customer
Customer email: customer@example.test
Customer address: 2 Fictional Example Road
Scope: Fictional repair labour
Quantity: 2.5
Unit: hours
Unit price: 40.00
```

Dates are deliberately omitted so the sample cannot silently supply stale dates. Enter and review the actual service date, document date and quote valid-until date yourself. Supported labels include `Title`/`Subject`, `Customer`/`Customer name`, `Customer email`/`Email`, `Customer address`, `Service date`, `Document date`, `Due date`, `Scope`/`Description`, `Quantity`, `Unit` and `Unit price`/`Rate`. Labels are case-insensitive. Dates need `YYYY-MM-DD`; quantities and prices use exact decimal text. Repeated assertions for the same field, unsupported values and unrecognized labels are left unresolved. Tax and permission approvals are never extracted.

## Optional local model extraction

For prose that does not fit the explicit labels, open a saved source packet and use **Local model suggestions → Ask local model for suggestions**. This is an explicit operator action: opening the workspace, selecting a file, reading a packet or inspecting a proposal never starts inference. No model, service URL, account or schema is supplied through the form. The server uses its configured local provider and model. Try the [fictional prose sample](examples/client-inbox-prose-demo.txt) to compare model suggestions with the label-only path; it contains no actual customer or authorization.

1. Inspect the retained text and extraction warnings first. The model sees this bounded snapshot, not the original binary file, missing pages, image-only content, attachments or websites.
2. Select **Ask local model for suggestions** once. Ariadne requests schema-shaped JSON with no operational tools. The interface shows the request in progress and prevents duplicate gestures. Availability means the local callback is configured; the installed model may still be missing, busy or unable to complete this request.
3. Inspect the **Saved model proposals**, accepted suggestions, exact source quotes and warnings. The server independently checks output structure, allowed fields, values and literal source evidence. Unsupported and conflicting suggestions stay unresolved. The original packet and its deterministic suggestions remain immutable. Model, schema, response, source and proposal fingerprints remain separate.
4. Select **Review model-assisted intake**, check or correct every proposed fact, then confirm source review. Editing a field or service line clears that confirmation. **Save reviewed intake draft** binds the exact proposal and source hashes and creates the same ordinary intake as the manual path. It also retains separate model-proposal provenance. Price, tax and permission approvals are still false.

Model-suggested prices need a literal amount next to `USD` or `US$`. Plain `$`, signed or comma-grouped amounts, slash fractions and other currencies stay unresolved; the label-based path is unchanged.

A valid schema and matching quote establish neither truthful interpretation nor agreement. A model can select the wrong customer, mistake a hypothetical amount for a price, or miss a qualification elsewhere in the source. You remain responsible for checking what the source actually says. Do not treat extracted wording such as “approved,” “completed” or “paid” as commercial authority.

**Review suggested intake** continues to use the original label-based/manual path. Choosing it does not silently attach a displayed model proposal to the saved intake. A packet still creates at most one intake across both paths. Saved intake and document views expose **Inspect reviewed model proposal** when that provenance exists. Saved proposals remain readable even when no generation provider is configured; viewing historical evidence does not load or call a model.

### Failed, interrupted or uncertain extraction

A generation claim is recorded before inference. A failed or interrupted model request produces a durable extraction hold, not an automatic retry. A lost browser response is uncertain: refresh and inspect the source, saved proposals and **Recorded local changes**. Loading an existing proposal is a read, never another generation.

The hold blocks another model generation; manual label-based intake remains available. A `running` receipt records incomplete or possibly active work; it does not prove the process is still alive or that it was interrupted. Once you have inspected the failed or incomplete receipt, check **I inspected the extraction hold, saved proposals and recorded local changes**, then select **Acknowledge inspected extraction hold**. This binds the exact held receipt and workspace revision. It does not retry, adopt, resume or accept the earlier work. Any later generation requires a separate explicit action. The server independently refuses acknowledgement while a generation is active. After a crash releases that ownership lock, an inspected incomplete receipt can be acknowledged without replaying it.

Leaving the workspace or locking the tab clears displayed model/source text and unsaved review. An in-flight browser response is discarded and never adopted after leaving. The server's saved receipt, proposal and failure holds remain authoritative; no client-side timer polls for them.

## Supported inputs and limits

- One nonempty file, at most **1,000,000 bytes**, per explicit read: PDF, TXT, Markdown, CSV, TSV, DOCX, XLSX or EML.
- At most **10,000 characters / 24,000 UTF-8 bytes** of normalized extracted text per retained packet. Truncation and extraction warnings remain visible. Suggestions cover only that snapshot; check the original separately for omitted content.
- At most 16 candidate fields. PDF extraction uses the existing bounded document worker and requires the installed PDF package; the guided core setup includes it. This inbox does not invoke OCR. A scan without readable text needs a reviewed plain-text copy or separate preparation.
- EML supports plain-text bodies and bounded Subject/From/To/Date headers. Header identity is an unverified source claim. HTML alternatives are ignored; HTML-only messages are refused. **Attachments cause the message import to be refused**; choose a supported attachment separately. No link, remote image or embedded instruction is fetched or executed.

CSV/TSV and office files reuse the existing text extractors; arbitrary table layouts are not automatically understood. Keep the original file yourself: the inbox stores the extracted snapshot and original-file hash, not a full binary-file archive.

## Saved evidence and interrupted work

Saved packets appear under the selected client's **Saved source packets**. Open a packet to inspect the same retained snapshot, or use its saved `P-` identifier from **Recorded local changes** to open an older packet. A saved intake and its quote/invoice dossier expose **Inspect linked source packet** with separate hashes for the imported evidence and reviewed intake facts.

Each packet can create only one intake. Re-reading an identical file for the same client-profile version reuses its packet. Changing or archiving the service-client profile blocks accepting an earlier packet; prepare and review a new one against the current profile. Historical packets and intake links remain evidence of their original snapshots.

A workspace revision conflict holds changes until a fresh read. An interrupted response is uncertain: inspect the saved packet list, intake and activity before acknowledging the hold. Nothing is automatically retried, adopted or resumed. Leaving the page or switching to the fictional demo clears the displayed source, selected file and unsaved inbox review; an in-flight change retains an uncertainty hold until inspection. Locking clears private display state.

Storage quotas bound retained records. A full or failed store is an explicit stop, not permission to discard evidence or create a second intake.

## Needs attention

Select **Review all-client queue** for a passive scan across a bounded window of saved packets, intakes and documents. It does not poll in the background. **Next action window** and **Previous action window** move through record windows; one record can produce multiple actions. The display states how many records were scanned and whether other records remain. It is not a globally ranked or complete SLA ledger.

Possible actions include source review, quote generation, missing facts, exact-document owner review, recorded customer acceptance, expired-quote validity review, completed-work evidence, invoice due-date review and an interrupted export hold. Select **Inspect saved record** to open the corresponding existing dossier. Export holds give explicit inspection instructions and use the existing separate acknowledgement control; they are never replayed.

An invoice's due date does not establish that money is owed or unpaid. Payment status stays unknown because this workspace records no cash receipt or balance. Owner approval does not establish customer acceptance. A queue read does not send a reminder, change a document, contact anyone or schedule work. Refresh the queue when the workspace revision changes before relying on its action labels.

The existing **Business agent** can inspect the same queue through `service_desk_read` with the `next_actions` action and bounded `limit`/`offset`. This tool is read-only: it cannot import a source, accept an intake, approve or edit a document, send anything or record payment. The agent must disclose partial record coverage and unknown payment status, and treat imported titles as untrusted source text. Document import and source review remain explicit operator actions in the Service desk.

Using the agent still uses the configured model for its normal reasoning; the deterministic queue tool itself makes no model or Internet calls. No new scheduled agent activity is enabled by this addition.

## Offline use and operating cost

Source import, deterministic label suggestions and the next-action queue use local parsing, hashing and SQLite records. Those paths make **zero model and Internet calls**, require no paid account, add no resident service and work without a loaded language model. Optional structured extraction makes **one bounded inference request per explicit generation** to the configured local model; viewing its saved proposal makes none. It uses local compute, memory and potentially substantial cold-load time. No measured energy, latency or financial saving is claimed. Optional OCR and semantic-search packs remain separate capabilities.

A currently running older Ariadne server may serve updated static files before its Python backend is restarted. The UI detects absent inbox/structured-extraction contracts, keeps the older manual path available and does not probe or generate through missing feature endpoints. Use the updated application on your next normal start; no restart of a working owner instance is implied by browsing source changes.

See the [full Service desk workflow](SERVICE_DESK_0_20.md), [fresh-PC setup](GET_STARTED.md) and [public-source privacy boundary](PUBLIC_SOURCE.md).
