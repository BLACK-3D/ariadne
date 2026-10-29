"use strict";

// Offline routines share the server's bounded worker. This module reads local
// state only, and adds no timer, public request, model call or automatic retry.
(() => {
  const active = run => ["queued", "running", "waiting", "cancelling"].includes(run?.status);
  const pendingKey = "ariadne.routine.pending";
  let app, generation = 0, loading = false, busy = false, loaded = false, lastMarker = null;
  let data = {templates:[], procedures:[], routines:[], runs:[]}, editor = null, review = null, pending = null;
  const verification = new Map(), comparisons = new Map();
  const $ = id => document.getElementById(id);
  const visible = () => app?.state.authenticated && app.state.view === "workflows" && app.state.workflowTab === "routines" && !document.hidden;
  const current = token => token === generation && app.state.authenticated;
  const idOf = run => run.id || run.request_id;
  const procedure = id => data.procedures.find(item => item.id === id);
  const nameOf = id => procedure(id)?.name || app.humanize(id);
  function error(message = "") { $("routine-error").textContent = message; $("routine-error").hidden = !message; }
  function savePending(value) {
    pending = value;
    try { value ? sessionStorage.setItem(pendingKey, JSON.stringify(value)) : sessionStorage.removeItem(pendingKey); } catch { /* In-memory reconciliation still works. */ }
  }
  function restorePending() {
    try {
      const value = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
      if (value && typeof value.requestId === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(value.requestId)
          && typeof value.routineId === "string" && typeof value.name === "string") pending = value;
    } catch { /* Invalid or unavailable tab storage grants no authority. */ }
  }
  function controls() {
    for (const id of ["add-routine", "refresh-routines", "save-routine", "add-routine-step"]) if ($(id)) $(id).disabled = !app.state.authenticated || busy || (id === "refresh-routines" && loading);
    if (editor) $("add-routine-step").disabled = busy || !app.state.authenticated || editor.steps.length >= 4 || editor.steps.length >= data.procedures.length;
    $("run-routine").disabled = !app.state.authenticated || busy || !review || Boolean(pending);
    $("cancel-routine-review").textContent = busy ? "Close (request continues)" : "Cancel";
    for (const button of document.querySelectorAll(".routine-mutation")) button.disabled = busy || !app.state.authenticated || button.dataset.unavailable === "true";
  }
  function replaceRun(run) {
    if (!run || typeof idOf(run) !== "string" || typeof run.status !== "string") throw new Error("A valid routine receipt was not returned. Read its request ID before trying another run.");
    data.runs = [run, ...data.runs.filter(item => idOf(item) !== idOf(run))];
  }
  function renderPending() {
    const box = $("routine-uncertain"); box.replaceChildren(); box.hidden = !pending || !app.state.authenticated;
    if (!pending || !app.state.authenticated) return;
    const text = app.node("div");
    text.append(app.node("strong", "", "A request needs reconciliation"), app.node("p", "", `${pending.name} · request ${pending.requestId}. A lost response does not show whether execution began. No retry is sent automatically.`));
    box.append(app.icon("alert"), text, app.button("Read this request", "button-secondary", checkPending, "refresh"));
    box.append(app.button("Clear reviewed notice", "button-secondary", async () => {
      const token = generation;
      if (!app.state.authenticated || busy || !await app.confirmAction("Clear this local notice?", "First inspect this request's server receipt. Clearing the notice does not cancel a run or repeat a request. A new run will use a new ID.", "Clear reviewed notice", "Keep notice")) return;
      if (current(token)) { savePending(null); await load(); renderPending(); controls(); }
    }));
  }
  async function checkPending() {
    if (!pending || !app.state.authenticated || busy) return;
    const token = generation, identifier = pending.requestId;
    busy = true; controls(); error();
    try {
      const result = await app.api(`/routines/runs/${encodeURIComponent(identifier)}`);
      if (!current(token)) return;
      if (idOf(result.run || {}) !== identifier) throw new Error("The returned receipt did not match this request.");
      replaceRun(result.run); savePending(null); render();
      app.toast("The recorded request was found. Review its actual state below.");
    } catch (failure) {
      if (current(token)) error(failure.status === 404 ? `No receipt is currently recorded for ${identifier}. The request was not repeated. Refresh and review the server before deciding to create another run.` : failure.message);
    } finally { if (token === generation) { busy = false; controls(); } }
  }
  function planList(ids, withOutputs = false) {
    const list = app.node("ol", "routine-plan");
    for (const id of ids || []) {
      const row = app.node("li"); row.append(app.node("strong", "", nameOf(id)));
      if (withOutputs) row.append(app.node("small", "", (procedure(id)?.outputs || []).join(" · ") || "Outputs are recorded in the step receipt."));
      list.append(row);
    }
    return list;
  }
  function payload(routine, enabled = routine.enabled) {
    return {name:routine.name, steps:[...routine.steps], interval_minutes:routine.interval_minutes, enabled:Boolean(enabled), expected_revision:routine.revision};
  }
  async function toggle(routine) {
    if (busy || !app.state.authenticated) return;
    const token = generation;
    if (!routine.enabled && routine.last_error && !await app.confirmAction("Resume this routine's recurrence?", `${routine.last_error} Review the last run and its files before resuming. A held request will not be replayed.`, "Enable recurrence", "Keep held")) return;
    if (!current(token)) return;
    busy = true; controls(); error();
    try {
      await app.api(`/routines/${encodeURIComponent(routine.id)}`, {method:"PUT", body:payload(routine, !routine.enabled)});
      if (current(token)) await load();
    } catch (failure) { if (current(token)) error(`${failure.message} Refresh the saved definition before changing it again.`); }
    finally { if (token === generation) { busy = false; controls(); } }
  }
  async function remove(routine) {
    if (busy || !app.state.authenticated) return;
    const token = generation;
    if (!await app.confirmAction("Remove this routine?", `“${routine.name}” will stop creating recurring runs. Existing receipts and report files remain.`, "Remove routine", "Keep routine") || !current(token)) return;
    busy = true; controls();
    try {
      await app.api(`/routines/${encodeURIComponent(routine.id)}`, {method:"DELETE"});
      if (current(token)) await load();
    } catch (failure) { if (current(token)) error(failure.message); }
    finally { if (token === generation) { busy = false; controls(); } }
  }
  function renderDefinitions() {
    const list = $("routine-list"); list.replaceChildren();
    if (!data.routines.length) list.append(app.node("p", "procedure-empty", "No routines saved. Create an ordered set of local reports, or start from a template below."));
    for (const routine of data.routines) {
      const card = app.node("article", "resource-card routine-card"), heading = app.node("div", "resource-card-header");
      heading.append(app.node("span", "eyebrow", routine.last_error ? "RECURRENCE HELD" : routine.enabled ? "RECURRING" : "ON DEMAND"), app.node("span", "tag", "0 model calls"));
      card.append(heading, app.node("h2", "", routine.name), planList(routine.steps));
      card.append(app.node("p", "routine-schedule-note", routine.enabled ? `Every ${routine.interval_minutes} minutes · next ${app.dateLabel(routine.next_run, true)}` : "Recurrence off · Run once is available"));
      if (routine.last_error) card.append(app.node("p", "procedure-receipt-warning", routine.last_error));
      const actions = app.node("div", "operation-actions");
      const run = app.button("Run once", "button-primary routine-mutation", () => reviewRun(routine), "play");
      run.dataset.unavailable = String(Boolean(pending) || data.runs.some(item => item.routine_id === routine.id && active(item)));
      if (run.dataset.unavailable === "true") run.title = "Read the pending or active run before requesting another.";
      actions.append(run, app.button("Edit", "button-secondary routine-mutation", () => openEditor(routine), "settings"),
        app.button(routine.enabled ? "Disable recurrence" : routine.last_error ? "Review & resume" : "Enable recurrence", "button-secondary routine-mutation", () => toggle(routine), "clock"),
        app.button("Remove", "button-secondary routine-mutation", () => remove(routine), "trash"));
      card.append(actions); list.append(card);
    }
    const templates = $("routine-templates"); templates.replaceChildren();
    for (const template of data.templates) {
      const card = app.node("article", "resource-card routine-template");
      card.append(app.node("h3", "", template.name), app.node("p", "", template.description), planList(template.steps), app.button("Use this template", "button-secondary routine-mutation", () => openEditor({...template, id:null, enabled:false, interval_minutes:1440}), "plus"));
      templates.append(card);
    }
  }
  async function runAction(run, action) {
    if (busy || !app.state.authenticated) return;
    const token = generation;
    if (action === "cancel" && !await app.confirmAction("Cancel the remaining routine steps?", "An executing fixed step may finish. Completed files and receipts remain; cancellation does not roll back earlier steps.", "Cancel remaining steps", "Keep running")) return;
    if (!current(token)) return;
    busy = true; controls(); error();
    try {
      const result = await app.api(`/routines/runs/${encodeURIComponent(idOf(run))}/${action}`, {method:"POST", body:{}});
      if (!current(token)) return;
      replaceRun(result.run);
      if (action === "verify") verification.set(idOf(run), result.verification || result.run.verification || result);
      renderRuns();
      app.toast(action === "verify" ? "File verification returned. Inspect the recorded result." : "Cancellation recorded. Inspect the final run state.");
    } catch (failure) { if (current(token)) error(`${failure.message} Read this run's receipt before repeating the action.`); }
    finally { if (token === generation) { busy = false; controls(); } }
  }
  async function compareRun(run) {
    const identifier = idOf(run);
    if (!app.state.authenticated || comparisons.get(identifier)?.loading) return;
    const token = generation, entry = {loading:true, result:null, error:null};
    comparisons.set(identifier, entry); renderRuns(); controls();
    try {
      const result = await app.api(`/routines/runs/${encodeURIComponent(identifier)}/changes`);
      if (!current(token) || comparisons.get(identifier) !== entry) return;
      if (!result || typeof result.state !== "string" || result.current_id !== identifier) throw new Error("The comparison did not identify this exact current request.");
      entry.result = result;
    } catch (failure) {
      if (current(token) && comparisons.get(identifier) === entry) entry.error = failure.message;
    } finally {
      if (current(token) && comparisons.get(identifier) === entry) { entry.loading = false; renderRuns(); controls(); }
    }
  }
  function renderComparison(run, card) {
    const entry = comparisons.get(idOf(run));
    if (!entry) return;
    const box = app.node("section", "routine-comparison");
    if (entry.loading) box.append(app.node("p", "operation-status", "Comparing verified local report snapshots…"));
    if (entry.error) box.append(app.node("p", "inline-error", `${entry.error} No comparison is asserted. You may explicitly request it again.`));
    const result = entry.result;
    if (result) {
      const labels = {first_run:"No prior baseline identified", current_unavailable:"Current run unavailable for comparison", incomparable:"Runs are not comparable", partial:"Partial comparison", compared:result.metadata_only ? "Only metadata changes found" : "Reports compared"};
      box.append(app.node("h4", "", labels[result.state] || app.humanize(result.state)),
        app.node("p", "routine-comparison-identity", `Current request: ${result.current_id}`),
        app.node("p", "routine-comparison-identity", `Baseline request: ${result.baseline_id || "None returned"}`));
      if (["first_run", "current_unavailable", "incomparable"].includes(result.state)) box.append(app.node("p", "routine-comparison-note", "The returned state does not establish that the underlying records are unchanged."));
      if (result.state === "partial") box.append(app.node("p", "procedure-receipt-warning", "This comparison is incomplete. Returned differences and counts may be lower bounds; inspect the service limits and truncation details."));
      let shown = 0, returned = 0;
      for (const step of Array.isArray(result.steps) ? result.steps : []) {
        const changes = Array.isArray(step.changes) ? step.changes : [];
        returned += changes.length;
        const section = app.node("div", "routine-comparison-step");
        section.append(app.node("strong", "", `${nameOf(step.procedure_id)} · ${app.humanize(step.state || result.state)}`));
        if (typeof step.observed_differences === "number") section.append(app.node("p", "routine-comparison-note", `${step.observed_differences} observed differences${step.truncated ? " · bounded result" : ""}`));
        for (const change of changes) {
          if (shown >= 12) break;
          shown += 1;
          const row = app.node("div", "routine-change"), heading = app.node("div", "routine-change-heading");
          heading.append(app.node("code", "", change.pointer || change.path || "/"), app.node("span", "tag", `${app.humanize(change.kind)} · ${change.metadata === true ? "Metadata" : "Substantive"}`)); row.append(heading);
          for (const key of ["before", "after"]) {
            const value = Object.hasOwn(change, key) ? typeof change[key] === "string" ? change[key] : JSON.stringify(change[key]) : "Not present";
            const preview = String(value);
            row.append(app.node("pre", "", `${key === "before" ? "Before" : "After"}: ${preview.slice(0,180)}${preview.length > 180 ? "… [display clipped]" : ""}`));
          }
          section.append(row);
        }
        box.append(section);
      }
      if (returned > shown) box.append(app.node("p", "routine-comparison-note", `Showing ${shown} of ${returned} returned differences. The complete bounded response is below.`));
      const detail = app.node("details", "tool-details"); detail.append(app.node("summary", "", "Comparison sources, hashes, limits and complete response"), app.node("pre", "", JSON.stringify(result,null,2))); box.append(detail);
    }
    card.append(box);
  }
  function renderRuns() {
    const list = $("routine-runs"); list.replaceChildren();
    if (!data.runs.length) list.append(app.node("p", "procedure-empty", "No routine runs recorded. New requests receive their own immutable request ID."));
    for (const run of data.runs) {
      const card = app.node("article", "procedure-receipt routine-receipt"), heading = app.node("div", "procedure-receipt-heading"), text = app.node("div");
      text.append(app.node("h3", "", run.name || "Local routine"), app.node("span", "procedure-receipt-id", `Request ${idOf(run)}`));
      heading.append(text, app.node("span", `tag${run.status === "completed" ? "" : " approval-required"}`, app.humanize(run.status)));
      card.append(heading, app.node("p", "procedure-receipt-measurements", `${app.dateLabel(run.created_at, true)} · ${run.model_calls === 0 ? "0 model calls" : "Model call count not recorded"}${typeof run.wall_seconds === "number" ? ` · ${run.wall_seconds.toFixed(3)} seconds` : ""}`));
      const steps = app.node("ol", "routine-run-steps");
      for (const step of run.steps || []) {
        const row = app.node("li"); row.append(app.node("strong", "", nameOf(step.procedure_id)), app.node("span", `tag${step.status === "completed" ? "" : " approval-required"}`, app.humanize(step.status)));
        if (step.request_id) row.append(app.node("small", "", `Child request ${step.request_id}`));
        steps.append(row);
      }
      card.append(steps);
      for (const warning of run.warnings || []) card.append(app.node("p", "procedure-receipt-warning", String(warning)));
      if (run.error) card.append(app.node("p", "procedure-receipt-warning", run.error));
      if (run.cancel_requested) card.append(app.node("p", "procedure-receipt-warning", "Cancellation requested; an executing step may still finish."));
      const artifacts = app.node("div", "procedure-artifacts");
      for (const artifact of run.artifacts || []) {
        if (typeof artifact?.path !== "string") continue;
        const row = app.node("div", "procedure-artifact"), info = app.node("div"), actions = app.node("div", "procedure-artifact-actions");
        info.append(app.node("strong", "", artifact.path), app.node("small", "", `${app.bytes(artifact.bytes)} · SHA-256 ${artifact.sha256 || "not recorded"}`));
        actions.append(app.button("Preview", "button-secondary", () => app.openArtifact(artifact.path), "eye"), app.button("Download", "button-secondary", () => app.downloadArtifact(artifact.path), "arrow-up-right"));
        row.append(app.icon("file"), info, actions); artifacts.append(row);
      }
      card.append(artifacts);
      const actions = app.node("div", "operation-actions");
      if (active(run)) actions.append(app.button("Cancel remaining steps", "button-secondary routine-mutation", () => runAction(run, "cancel"), "stop"));
      if (run.status === "completed" && run.artifacts?.length) actions.append(app.button("Verify files", "button-secondary routine-mutation", () => runAction(run, "verify"), "check"));
      if (run.status === "completed") {
        const compare = app.button(comparisons.get(idOf(run))?.loading ? "Comparing…" : "Compare prior run", "button-secondary", () => compareRun(run), "workflow");
        compare.disabled = !app.state.authenticated || comparisons.get(idOf(run))?.loading === true;
        actions.append(compare);
      }
      card.append(actions);
      renderComparison(run, card);
      const savedVerification = verification.get(idOf(run)) || run.verification;
      if (savedVerification) { const details = app.node("details", "tool-details"); details.append(app.node("summary", "", "Recorded file-verification result"), app.node("pre", "", JSON.stringify(savedVerification, null, 2))); card.append(details); }
      const details = app.node("details", "tool-details"); details.append(app.node("summary", "", "Complete receipt and frozen plan"), app.node("pre", "", JSON.stringify(run, null, 2))); card.append(details);
      list.append(card);
    }
  }
  function render() {
    renderPending(); renderDefinitions(); renderRuns();
    $("routine-status").textContent = `${data.routines.length} saved routines · ${data.runs.filter(active).length} active runs${app.state.status?.paused ? " · autonomy paused; queued steps wait" : ""} · no model calls inside routines`;
    controls();
  }
  async function load({quiet = false} = {}) {
    if (!app?.state.authenticated || loading) return;
    const token = generation, marker = app.state.status?.routines?.last_updated_at ?? null;
    loading = true; controls();
    try {
      const result = await app.api("/routines");
      if (!current(token)) return;
      if (!["templates", "procedures", "routines", "runs"].every(key => Array.isArray(result[key]))) throw new Error("The routine catalog did not return valid local records.");
      data = result; loaded = true; lastMarker = marker; error(); render();
    } catch (failure) { if (current(token)) { error(failure.message); if (!quiet) $("routine-status").textContent = "Routine records unavailable. Refresh to retry."; } }
    finally { if (token === generation) { loading = false; controls(); } }
  }
  function status(value) {
    if (!visible()) return;
    const hint = value.routines || {}, changed = loaded && hint.last_updated_at != null && hint.last_updated_at !== lastMarker;
    if (changed || hint.active_runs > 0 || data.runs.some(active)) load({quiet:true});
  }
  function renderEditorSteps() {
    const list = $("routine-step-list"); list.replaceChildren();
    for (const [index, identifier] of editor.steps.entries()) {
      const row = app.node("div", "routine-step-row"), select = app.node("select"); select.setAttribute("aria-label", `Procedure for step ${index+1}`);
      for (const item of data.procedures) { const option = app.node("option", "", item.name); option.value = item.id; select.append(option); }
      select.value = identifier;
      select.addEventListener("change", () => { editor.steps[index] = select.value; });
      const move = direction => { const target = index+direction; [editor.steps[index],editor.steps[target]] = [editor.steps[target],editor.steps[index]]; renderEditorSteps(); };
      const up = app.button("", "icon-button", () => move(-1), "arrow-up"); up.setAttribute("aria-label", `Move step ${index+1} up`); up.disabled = index === 0;
      const down = app.button("", "icon-button", () => move(1), "arrow-down"); down.setAttribute("aria-label", `Move step ${index+1} down`); down.disabled = index === editor.steps.length-1;
      const remove = app.button("", "icon-button", () => { editor.steps.splice(index,1); renderEditorSteps(); }, "trash"); remove.setAttribute("aria-label", `Remove step ${index+1}`);
      row.append(app.node("span", "routine-step-number", index+1), select, up, down, remove); list.append(row);
    }
    $("add-routine-step").disabled = editor.steps.length >= 4 || editor.steps.length >= data.procedures.length;
  }
  function openEditor(definition = null) {
    if (busy || !app.state.authenticated) return;
    editor = {id:definition?.id || null, revision:definition?.revision ?? null, steps:[...(definition?.steps || (data.procedures[0] ? [data.procedures[0].id] : []))]};
    $("routine-editor-title").textContent = editor.id ? "Edit offline routine" : "Create offline routine";
    $("routine-name").value = definition?.name || ""; $("routine-interval").value = definition?.interval_minutes || 1440;
    $("routine-enabled").checked = definition?.enabled === true; $("routine-editor-error").hidden = true;
    renderEditorSteps(); $("routine-editor-dialog").showModal(); $("routine-name").focus();
  }
  async function saveEditor(event) {
    event.preventDefault(); if (!editor || busy || !app.state.authenticated) return;
    const body = {name:$("routine-name").value.trim(), steps:[...editor.steps], interval_minutes:Number($("routine-interval").value), enabled:$("routine-enabled").checked};
    if (editor.id) body.expected_revision = editor.revision;
    const invalid = !body.name || !body.steps.length || body.steps.length > 4 || new Set(body.steps).size !== body.steps.length || body.steps.some(id => !procedure(id)) || !Number.isInteger(body.interval_minutes) || body.interval_minutes < 5 || body.interval_minutes > 525600;
    if (invalid) { $("routine-editor-error").textContent = "Choose a name, one to four unique installed procedures, and an integer interval from 5 to 525600 minutes."; $("routine-editor-error").hidden = false; return; }
    const token = generation, identifier = editor.id; busy = true; controls();
    try {
      await app.api(`/routines${identifier ? `/${encodeURIComponent(identifier)}` : ""}`, {method:identifier ? "PUT" : "POST", body});
      if (!current(token)) return;
      $("routine-editor-dialog").close(); editor = null; await load(); app.toast("Offline routine saved.");
    } catch (failure) { if (current(token)) { $("routine-editor-error").textContent = failure.status === 409 ? `${failure.message} Close this editor, refresh the current routine definition, and reopen it before saving. This request was not retried.` : `${failure.message} Settings may have been saved. Refresh the routine list before creating another definition.`; $("routine-editor-error").hidden = false; } }
    finally { if (token === generation) { busy = false; controls(); } }
  }
  function reviewRun(routine) {
    if (busy || pending || !app.state.authenticated) return;
    review = {routine, requestId:crypto.randomUUID().replaceAll("-", "")};
    $("routine-review-title").textContent = routine.name; $("routine-review-request").textContent = `Immutable request ID ${review.requestId}`;
    $("routine-review-steps").replaceChildren(planList(routine.steps,true)); $("routine-review-error").hidden = true;
    controls(); $("routine-review-dialog").showModal();
  }
  async function run() {
    if (!review || pending || busy || !app.state.authenticated) return;
    const request = review, token = generation;
    savePending({requestId:request.requestId, routineId:request.routine.id, name:request.routine.name});
    busy = true; renderPending(); controls();
    try {
      const result = await app.api(`/routines/${encodeURIComponent(request.routine.id)}/run`, {method:"POST", body:{request_id:request.requestId}});
      if (!current(token)) return;
      if (idOf(result.run || {}) !== request.requestId) throw new Error("The returned receipt did not match this request ID.");
      replaceRun(result.run); savePending(null); review = null; $("routine-review-dialog").close(); render();
      app.toast("Routine request recorded. The worker will advance its fixed steps when available.");
    } catch (failure) {
      if (current(token)) { review = null; $("routine-review-error").textContent = `${failure.message} Read request ${request.requestId}; this client will not resubmit it.`; $("routine-review-error").hidden = false; renderPending(); error("The final submission state is not confirmed. Use Read this request before starting another run."); }
    } finally { if (token === generation) { busy = false; controls(); } }
  }
  function locked() {
    generation += 1; loading = false; busy = false; loaded = false; lastMarker = null; editor = null; review = null; verification.clear(); comparisons.clear();
    data = {templates:[], procedures:[], routines:[], runs:[]};
    for (const id of ["routine-editor-dialog", "routine-review-dialog"]) if ($(id)?.open) $(id).close();
    for (const id of ["routine-name", "routine-interval"]) $(id).value = "";
    $("routine-enabled").checked = false;
    for (const id of ["routine-step-list", "routine-review-steps", "routine-review-request", "routine-review-error", "routine-editor-error"]) $(id).replaceChildren();
    $("routine-editor-title").textContent = "Create offline routine"; $("routine-review-title").textContent = "Run routine";
    error();
    render();
  }
  function attach(helpers) {
    if (app) return; app = helpers; restorePending();
    $("refresh-routines").addEventListener("click", () => load()); $("add-routine").addEventListener("click", () => openEditor());
    $("routine-editor-form").addEventListener("submit", saveEditor); $("run-routine").addEventListener("click", run);
    for (const id of ["close-routine-editor", "cancel-routine-editor"]) $(id).addEventListener("click", () => $("routine-editor-dialog").close());
    for (const id of ["close-routine-review", "cancel-routine-review"]) $(id).addEventListener("click", () => $("routine-review-dialog").close());
    $("add-routine-step").addEventListener("click", () => { const next = data.procedures.find(item => !editor.steps.includes(item.id)); if (next && editor.steps.length < 4) { editor.steps.push(next.id); renderEditorSteps(); } });
    controls();
  }
  window.ariadneRoutines = Object.freeze({attach, load, status, locked});
})();
