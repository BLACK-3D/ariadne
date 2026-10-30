"use strict";

// Composition only. The existing routine API owns execution, approvals and holds.
(() => {
  let app, editing = null, busy = false, loading = false, generation = 0, revision = null;
  const $ = id => document.getElementById(id), text = value => String(value || "").trim();
  const fields = ["name","interval","business","devices","cash","quote","erp","date","market","metadata","study","snapshot","observed","enabled"];
  function workflowPayload(value, previous = null) {
    const selected = [], add = (operation, args = {}) => selected.push({operation, arguments:args});
    const cash = text(value.cash).split(",").map(x => x.trim()).filter(Boolean);
    if (value.business) add("business_snapshot", cash.length ? {cash_accounts:cash} : {});
    if (text(value.erp)) {
      const date = text(value.date);
      if (date !== "today_utc" && !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error("Choose today_utc or an explicit ERP audit date.");
      add("erp_audit", {path:text(value.erp),as_of:date});
    }
    if (text(value.quote)) add("quote_draft", {path:text(value.quote)});
    if (text(value.market) || text(value.metadata)) {
      if (!text(value.market) || !text(value.metadata)) throw new Error("Market research needs both data and assumptions files.");
      add("market_audit", {path:text(value.market),metadata_path:text(value.metadata)});
      if (value.study !== false) add("market_study", {path:text(value.market),metadata_path:text(value.metadata)});
    }
    if (text(value.snapshot) || text(value.observed)) {
      if (!text(value.snapshot) || !text(value.observed)) throw new Error("A saved device export needs its actual observation timestamp.");
      add("device_snapshot", {source_path:text(value.snapshot),observed_at:text(value.observed)});
    }
    if (value.devices) add("device_status");
    const byKind = new Map(selected.map(step => [step.operation,step])), seen = new Set();
    const steps = previous ? previous.steps.flatMap(step => {
      if (typeof step === "string") return [step];
      if (!byKind.has(step.operation)) return [];
      seen.add(step.operation); return [byKind.get(step.operation)];
    }) : [];
    for (const step of selected) if (!seen.has(step.operation)) steps.push(step);
    const interval = Number(value.interval), name = text(value.name);
    if (!name || name.length > 120 || !Number.isInteger(interval) || interval < 5 || interval > 525600 || steps.length < 1 || steps.length > 4) throw new Error("Choose a name, an interval of at least five minutes and one to four steps. Market audit and study use two steps.");
    const result = {name,steps,interval_minutes:interval,enabled:value.enabled === true};
    if (previous) result.expected_revision = previous.revision;
    return result;
  }
  function fail(message = "") { $("ops-workflow-error").textContent = message; $("ops-workflow-error").hidden = !message; }
  function reset() {
    editing = null; $("ops-workflow-form").reset(); fail();
    $("ops-workflow-save").textContent = "Save workflow";
    $("ops-workflow-edit-note").textContent = "New workflows start on demand unless you enable recurrence.";
  }
  function values() {
    return Object.fromEntries(fields.map(key => { const field = $("ops-workflow-" + key); return [key,field.type === "checkbox" ? field.checked : field.value]; }));
  }
  async function manage() { app.showView("workflows"); app.setWorkflowTab("routines"); }
  async function edit(routine) {
    const known = new Set(["business_snapshot","erp_audit","quote_draft","market_audit","market_study","device_status","device_snapshot"]);
    if (!app?.state.authenticated) return;
    if (routine.steps.some(step => typeof step !== "string" && !known.has(step.operation))) throw new Error("This saved workflow contains an unsupported operation; its definition has been preserved.");
    await app.showView("operations");
    reset(); editing = JSON.parse(JSON.stringify(routine));
    $("ops-workflow-name").value = routine.name; $("ops-workflow-interval").value = routine.interval_minutes;
    $("ops-workflow-enabled").checked = routine.enabled;
    $("ops-workflow-business").checked = false; $("ops-workflow-devices").checked = false;
    $("ops-workflow-study").checked = routine.steps.some(step => typeof step !== "string" && step.operation === "market_study");
    for (const step of routine.steps) {
      if (typeof step === "string") continue;
      const args = step.arguments || {};
      if (step.operation === "business_snapshot") { $("ops-workflow-business").checked=true; $("ops-workflow-cash").value=(args.cash_accounts||[]).join(", "); }
      if (step.operation === "device_status") $("ops-workflow-devices").checked=true;
      if (step.operation === "erp_audit") { $("ops-workflow-erp").value=args.path; $("ops-workflow-date").value=args.as_of; }
      if (step.operation === "quote_draft") $("ops-workflow-quote").value=args.path;
      if (step.operation === "market_audit" || step.operation === "market_study") { $("ops-workflow-market").value=args.path; $("ops-workflow-metadata").value=args.metadata_path; }
      if (step.operation === "device_snapshot") { $("ops-workflow-snapshot").value=args.source_path; $("ops-workflow-observed").value=args.observed_at; }
    }
    $("ops-workflow-save").textContent = "Save reviewed changes";
    const fixed = routine.steps.filter(step => typeof step === "string").length;
    $("ops-workflow-edit-note").textContent = routine.last_error ? `Recurrence held: ${routine.last_error}` : `Editing revision ${routine.revision}.${fixed ? ` ${fixed} existing fixed steps and their order will be retained.` : ""}`;
    $("ops-workflow-name").focus();
  }
  function render(data) {
    const list = $("ops-workflow-list"); list.replaceChildren();
    const plans = (data.routines || []).filter(row => row.steps.some(step => typeof step !== "string"));
    for (const routine of plans) {
      const card = app.node("article","resource-card");
      card.append(app.node("span","eyebrow",routine.last_error ? "RECURRENCE HELD" : routine.enabled ? "RECURRING" : "ON DEMAND"),app.node("h3","",routine.name));
      card.append(app.node("p","",`${routine.steps.length} steps · every ${routine.interval_minutes} minutes · no model calls`));
      if (routine.last_error) card.append(app.node("p","form-error",routine.last_error));
      card.append(app.button("Edit inputs","button-secondary",() => edit(routine).catch(error => fail(error.message)),"settings"),app.button("Run and inspect receipts","button-secondary",manage,"arrow-up-right"));
      list.append(card);
    }
    $("ops-workflow-status").textContent = `${plans.length} saved operation workflows. Recurrence pauses with Ariadne; a failed or interrupted invocation is held for review.`;
  }
  async function load() {
    if (!app?.state.authenticated || loading || busy) return;
    loading = true; const token = generation;
    try { const data = await app.api("/routines"); if (token === generation && app.state.authenticated) render(data); }
    catch (error) { if (token === generation && app.state.authenticated) fail(error.message); }
    finally { if (token === generation) loading = false; }
  }
  async function save(event) {
    event.preventDefault(); if (busy || !app.state.authenticated) return;
    const token = generation; let body;
    try { body = workflowPayload(values(),editing); } catch (error) { fail(error.message); return; }
    if (editing?.last_error && body.enabled && !await app.confirmAction("Resume this workflow's recurrence?",`${editing.last_error} Earlier failed or interrupted requests will not be replayed.`,"Enable recurrence","Keep held")) return;
    if (token !== generation || !app.state.authenticated) return;
    busy=true; fail(); $("ops-workflow-save").disabled=true;
    try {
      await app.api(editing ? `/routines/${encodeURIComponent(editing.id)}` : "/routines",{method:editing ? "PUT" : "POST",body});
      if (token !== generation || !app.state.authenticated) return;
      reset(); app.toast("Workflow saved. Use Run, verify and manage to inspect its execution.");
    } catch (error) { if (token === generation && app.state.authenticated) fail(`${error.message} Refresh the saved workflow before repeating a change.`); }
    finally { if (token === generation) { busy=false; $("ops-workflow-save").disabled=false; await load(); } }
  }
  function status(value) {
    const next = value.routines?.revision;
    if (revision !== next) { revision=next; if (app?.state.view === "operations") load(); }
  }
  function locked() { generation++; busy=false; loading=false; revision=null; reset(); $("ops-workflow-list").replaceChildren(); }
  function attach(value) {
    app=value; $("ops-workflow-form").addEventListener("submit",save);
    $("ops-workflow-new").addEventListener("click",reset); $("ops-workflow-manage").addEventListener("click",manage);
  }
  window.ariadneOperationWorkflows = {attach,load,status,locked,edit,workflowPayload};
})();
