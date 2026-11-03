# Source-linked Project Kits

Project Kits turns a saved Service desk intake into a retained source pack and a prepared Workroom project. It supplies the workflow instructions, output checks and dependency path so you can inspect them instead of copying paperwork and authoring every workflow yourself.

Open **Project Kits** in the system directory. From a selected Service desk intake, **Plan delivery project** opens the same view with that intake selected. The shortcut reads saved evidence; it does not prepare, adopt or execute a project.

## The complete path

1. In **Service desk**, create and review a real-client intake from supplied facts or an imported source. Fictional demo records remain separate. Project Kits starts from the ordinary saved intake; it does not approve source suggestions for you.
2. Choose the saved intake in **Project Kits**, or enter an exact saved intake identifier and choose **Inspect saved intake**. The list covers the newest 50 intakes and identifies when older records exist. Inspection shows recorded gaps, the source fingerprint, any change to the service-business profile, and the complete bounded source snapshot.
3. Inspect the saved intake, current provider profile and any retained packet/proposal evidence. Check the source confirmation, then choose **Prepare source pack**. Preparation writes immutable local JSON, Markdown and a manifest with exact artifact identities. It creates no agent mission, workflow, Workroom project or commercial approval.
4. Inspect the prepared kit's retained artifacts, dependency path, complete workflow prompts, output checks and frozen specialist instructions/tools. The separate current-catalogue disclosure is only an observation at that read; it does not replace the frozen scope. Check the separate adoption confirmation, then choose **Adopt reviewed project**. Adoption saves the two fixed local workflows and their project together. It does not accept a manual gate or start a cycle.
5. Choose **Open adopted project**. Workroom first requires an explicit owner review of the retained source files. Its source-reference field is prefilled with the required path, SHA-256 and byte size. Inspect the files, write a review note and record your decision. Required references must remain in either decision; extra checked references are allowed within the existing ten-reference limit.
6. In Workroom, inspect the frozen instructions, specialist scope, acceptance checks and budgets. Explicitly start a bounded cycle. The existing **Offline operations agent** prepares a draft delivery plan; the **Offline evidence agent** reviews that draft. Both use existing local specialist scopes and the shared worker. The final owner handoff review remains a separate decision.

Project Kits never creates a quote, invoice, customer message, business ledger entry, brokerage order or payment. Continue those actions through their existing Service desk or trading controls when separately authorized.

## What is retained and what the model receives

The retained source snapshot keeps the complete admitted intake, current provider profile, retained source packet/proposal and their checked links. The immutable intake's own provider snapshot remains part of the intake, so a later provider profile is not silently substituted for historical evidence. Saved or preparation times do not establish the source's publication date or freshness.

The first recipe accepts a source snapshot of at most **64 KiB**. It also checks that the complete reviewed intake facts and fixed instructions fit the workflow prompt bound. Oversized cases are refused; they are not silently trimmed into an apparently complete brief. The owner can inspect the complete admitted snapshot.

The generated workflows receive the complete admitted, reviewed intake facts and source identities. Retained packet/proposal text supplies owner-review provenance; its presence is not a claim that the model analyzed every attachment or every passage. A retained text packet is not a binary archive of the original PDF, email or other attachment. Keep the original separately when that is required.

Imported titles, source notes, packet text and model proposals remain untrusted data. They cannot add tools, authorize spending, change controls or override the workflow's scope. Unknown prices, dates, tax treatment, permission and customer acceptance remain unresolved. An approval flag recorded in an old intake is historical source data, not a new approval granted by kit preparation or an AI draft.

## Checks and decisions

Each generated workflow writes isolated JSON and Markdown outputs under its own mission folder. File-size checks bound the JSON and Markdown, JSON validity is checked, and exact scalar checks bind the source fingerprint and draft/no-commercial-authority markers. JSON pointers identify the expected values; booleans remain distinct from integer values. These are deterministic local checks, not model judgments.

Inspect their coverage. A valid document with the right source identity can still contain a poor proposal or an unsupported claim. The second agent's review is another draft, not independent proof that the first agent is correct. The owner must inspect the actual files, unresolved questions and limitations before accepting the final handoff.

Workroom's required source references are checked when an owner records either decision and rehashed before downstream dispatch. Source or output drift, failed checks, approval uncertainty, an interruption or exhausted budgets retains the existing hold. Neither preparing nor adopting a kit clears a hold, copies an owner attestation to new work or renews a cycle automatically.

## Failure and recovery

Preparation records a durable intent before file publication. A failed or interrupted publication remains inspectable; partial files are not adopted as a completed pack. Each intake has one retained preparation, so **Inspect existing kit** is the route for an intake already associated with a preparation. A failed pack cannot be repaired by repeatedly pressing Prepare with a different request identifier.

If the browser loses a write response, it keeps the exact request identifier and blocks more writes. Choose **Inspect request receipt**, inspect the terminal receipt and retained kit, then explicitly acknowledge this tab's uncertainty. A running, missing or nonterminal receipt cannot release that tab hold. Acknowledgement does not clear a server hold or replay the request. Refresh before another action.

A definite validation or revision-conflict refusal requires **Refresh kits**. No automatic retry follows it. Preparation binds the exact source snapshot and local specialist scopes; adoption also binds the current Workroom revision. A changed captured source or specialist scope blocks adoption of that historical kit. Inspect it and create a newly reviewed intake for new work; refreshing does not silently regenerate instructions or adopt a different scope. If another tab changes the Workroom revision, inspect fresh state rather than assuming that the old confirmation applies.

Locking or leaving the view clears its private source display and confirmations. Late replies cannot restore that display or silently start work. Ordinary status refreshes preserve the exact editor field, focus and selection when controls have not changed. An actual pause or authentication change still disables or clears the relevant controls.

## Offline operation and costs

Source inspection, source-pack preparation, adoption and artifact checks use local files and SQLite with no model, network, paid service or new recurring worker. Only an explicitly started Workroom cycle uses the installed model and its existing step/tool budgets. Those budgets are upper bounds, not measurements of electricity, elapsed time or money saved.

The prepared local installation can use this path without internet. Missing local models or optional source parsers remain setup requirements; Project Kits does not download them. Fresh market, weather or other external facts do not become current while disconnected. No new schedule, microphone listener, cloud account or autonomous continuation is created.

See [Workroom](WORKROOM.md) for cycle and evidence rules, [Document inbox](CLIENT_INBOX.md) for source admission, and [Project direction](PROJECT_DIRECTION.md) for the maintained scope.

## Development evidence

`tests/fixtures/project_kits_ui_contract.cjs` uses a synthetic DOM and API. It checks source inspection, separate unchecked confirmations, exact CAS payloads, immutable artifact navigation, draft/authority labels, failed preparation holds, lost responses, lock/leave behavior, source privacy and typing focus. It makes no model, network or owner-service request. `tests/test_project_kits_ui.py` runs it with the bundled Node runtime when available.

Separate Python domain and WSGI tests establish transaction, source-identity, file-publication and authorization behavior. UI checks alone do not establish native deployment, model quality, a completed service, a sale or profitable trading.
