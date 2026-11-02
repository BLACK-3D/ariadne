# Project Workroom

Workroom gives ongoing local work a project objective, a dependency path and inspectable evidence. Open **Workroom** in Ariadne, or use `http://127.0.0.1:8080/?view=workroom` with your configured local port. Pair the browser to your own server first.

A saved project is a plan. Starting a cycle separately authorizes a small amount of work through Ariadne's existing runners. Merely opening Workroom, choosing a template, saving a plan or refreshing the page starts no model request.

## A first project

1. Choose a starting template or **Create project**. Give it a name, an objective and an observable acceptance condition. Templates are draft plans; their presence does not establish customer demand, revenue or a validated result.
2. Use the visual step editor to define each step's identifier, title, acceptance condition and dependencies. **After steps** takes comma-separated identifiers from this project. A project can contain 1–24 steps, each with at most six direct dependencies. Cycles, missing dependencies and duplicate identifiers are rejected.
3. Select how each step runs:
   - **Owner decision** waits for your explicit decision and note.
   - **Fixed local procedure** uses an existing bounded offline procedure.
   - **Saved workflow** selects an eligible existing workflow with isolated outputs and acceptance checks. Create and review the workflow in **Workflows** first if none is available.
4. **Save project plan**. Inspect the saved dependency path. Saving captures executable definitions on the server; browser-supplied runtime fields cannot grant execution or approval authority. Advanced task JSON is available in a disclosure for inspection and bulk editing.
5. Resolve a ready owner-decision step if its stated condition has actually been met. A review requires a note; optional source references must name current Workspace files with exact SHA-256 and byte size. Use `[]` when there is no artifact reference. Accepting a manual step records **owner-attested** completion even when file references are supplied.
6. Choose **Review cycle**. Inspect the exact frozen workflow prompt, output checks, specialist instructions, permitted tools and resolved specialist limit. Select **1–3 child invocations** and **1–8 AI steps per child**, then confirm the review and choose **Start reviewed cycle**.
7. Inspect the actual child receipt and saved outputs. **Inspect mission and approvals** opens the existing mission view. **Refresh projects** reads saved state. **Inspect / advance cycle** is an explicit write: it reconciles a child and may advance to another eligible step within the already authorized cycle.

The child budget counts runner invocations, not model calls. Each AI child is limited to the lower of the selected cycle cap and its resolved specialist/global limit. The displayed invocation count multiplied by the cycle cap is an upper bound on model steps, not a measurement of inference calls, elapsed time, electricity or money. A fixed local procedure uses zero model steps. No new recurring service or paid dependency is required by Workroom itself; model compute and energy remain dependent on the selected local model and hardware and have not been measured by this UI.

## What progress means

The dependency path shows recorded ready, blocked, active, held and completed steps. Completion counts and the progress bar use stored task state; they do not advance with a timer or a simulated agent animation.

**Checked child artifacts** means the runner recorded outputs that passed its configured checks and whose saved bytes can be checked. Inspect the checks' coverage: an existing file or a valid JSON structure alone does not prove the business claims inside it. **Owner-attested** means a person recorded a decision; it is not independent verification of a real-world outcome. An AI response alone does not complete a workflow step.

Artifact links display their SHA-256. Only relative Workspace artifact paths are offered. Project receipts retain linked child activity and bounded cycle history. Read access through an eligible operations/business specialist's `workroom_read` tool does not grant project mutation, cycle execution or approval authority.

## Stops, changes and recovery

- Existing global autonomy pauses, tool approvals, workflow restrictions and source checks remain in force. A campaign does not clear them or grant extra permissions.
- A failed, interrupted, unverified or uncertain child holds advancement. A cycle does not automatically renew its budget. Inspect its actual receipt and resolve the cause through the existing controls before preparing new reviewed work.
- **Stop after this step** prevents additional dispatch. An already linked queued or running child can still finish through its normal runner; this control does not cancel or relabel that child.
- A changed workflow, specialist profile or captured source/catalog fingerprint blocks new work under the old definition. Its historical preview remains readable. Explicitly review and save a revised project rather than assuming that a refresh adopts new instructions.
- Refreshing revokes the cycle confirmation. Changing either budget also revokes it. A project changed during editing invalidates the old draft.
- Editing after a terminal cycle creates a new definition version and resets that version's task state and attestations. Historical versions and cycle receipts remain evidence. Saving an unchanged failed execution is not a retry mechanism.
- If a write response is lost, the browser holds further writes and does not replay the request. Use **Inspect request receipt**, inspect the corresponding project/child, then explicitly acknowledge this tab's uncertainty. This acknowledgement does not clear a server hold or repeat work. If no completed receipt can be established, leave the hold in place and investigate.
- A definite validation or revision-conflict rejection (HTTP 400/409) requires **Refresh projects** before another action. The rejected request is not automatically submitted again, and server holds remain in force. Transport failures and server failures retain the receipt-inspection hold.

Locking or leaving Workroom clears its private display and unsaved draft. Late responses cannot restore that display or silently dispatch another cycle. Reopening Workroom performs saved-state reads; there is no page timer polling or background model loop.

## Useful offline projects

For a **Service desk pilot**, start with a manual check of supplied customer/job facts and source permission, generate a draft through an existing isolated workflow, then review the actual output. Workroom does not approve prices, invoices, customer messages or publication on your behalf. Existing Service desk controls still govern those actions.

For **print-on-demand validation**, document a proposed audience, original design concept, known unit costs and unknown demand. Use local workflows to assemble a dossier and an owner review to decide the next experiment. A draft concept is not a sale or permission to use another party's artwork.

For **offline continuity**, use fixed local checks and supplied evidence to prepare a continuity review. The project must not infer that a backup, restore or outage exercise succeeded from a commit or a plan alone.

For a **research dossier**, choose saved local sources, track unknowns, generate a draft with source coverage checks and require owner review before treating its conclusions as established. Workroom neither creates brokerage orders nor changes trading budgets.

All project planning, stored-state review, local procedures and installed local workflows can operate without internet. A workflow still needs its locally installed runtime/model and authorized inputs. Current weather, prices, headlines and other external facts cannot become fresh while disconnected. Workroom does not download missing models, create a network connection or claim live external coverage.

## Development checks

`tests/fixtures/workroom_ui_contract.cjs` exercises the UI with a synthetic DOM and API. It covers plan graphs, visual editing, exact source/review fingerprints, bounded start controls, manual attestation, safe artifact navigation, global pause, lost replies, concurrency, and lock/leave behavior without models or internet. `tests/test_workroom_ui.py` runs that fixture with the bundled Node runtime when installed.

The Python domain and server tests cover persisted execution and authorization separately. Passing a synthetic UI contract does not establish deployment, model quality, a profitable business or live trading performance. See [Project direction](PROJECT_DIRECTION.md) for the maintained long-term scope.
