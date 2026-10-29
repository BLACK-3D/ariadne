"use strict";

// Fixed local report actions only. Device changes stay in the mission flow.
(() => {
  let app, busy = false;
  const $ = id => document.getElementById(id);
  const trim = value => String(value || "").trim();
  function requestFor(kind, fields, requestId) {
    if (kind === "business") {
      const args = {action: fields.action, request_id: requestId};
      if (!["snapshot", "audit_erp", "draft_quote"].includes(args.action)) throw new Error("Choose a business operation.");
      if (args.action !== "snapshot") {
        args.path = trim(fields.path);
        if (!args.path) throw new Error("Choose the business input file in Workspace.");
      }
      if (args.action === "audit_erp") {
        args.as_of = trim(fields.date);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(args.as_of)) throw new Error("Choose the audit date.");
      }
      return {tool:"business_ops", arguments:args};
    }
    if (kind === "market") {
      if (!["audit", "study"].includes(fields.action)) throw new Error("Choose a market-data operation.");
      if (!trim(fields.path) || !trim(fields.metadata)) throw new Error("Choose both the historical data and assumptions files.");
      return {tool:"market_research", arguments:{action:fields.action, path:trim(fields.path), metadata_path:trim(fields.metadata), request_id:requestId}};
    }
    if (kind === "controls") {
      if (!["status", "catalog", "import_snapshot"].includes(fields.action)) throw new Error("Device changes use the mission approval flow.");
      const args = {action:fields.action};
      if (args.action === "import_snapshot") {
        if (!trim(fields.path) || !trim(fields.time)) throw new Error("Choose a saved state file and its actual observation time.");
        args.source_path = trim(fields.path); args.observed_at = trim(fields.time);
      }
      return {tool:"home_control", arguments:args};
    }
    throw new Error("Unknown local operation.");
  }
  function setError(message = "") { $("ops-error").textContent = message; $("ops-error").hidden = !message; }
  function render(result) {
    const root = $("ops-result"); root.replaceChildren(); root.hidden = false;
    root.append(app.node("span", "eyebrow", result.example ? "ILLUSTRATIVE EXAMPLE / SYNTHETIC INPUT" : "LOCAL OPERATION RECEIPT"));
    root.append(app.node("h2", "", result.replayed ? "Saved result retrieved" : "Result ready"));
    if (result.example_notice) root.append(app.node("p", "", result.example_notice));
    if (result.example_input_folder) root.append(app.node("p", "", `Example input files: ${result.example_input_folder}`));
    const artifacts = result.artifacts || result.report?.artifacts || [];
    for (const artifact of artifacts) {
      if (!artifact || typeof artifact.path !== "string") continue;
      const row = app.node("div", "ops-artifact");
      row.append(app.button(artifact.path, "button-secondary", () => app.openArtifact(artifact.path), "file"));
      if (typeof artifact.sha256 === "string") row.append(app.node("small", "", `SHA-256 ${artifact.sha256}`));
      root.append(row);
    }
    // This is bounded tool data, inserted as text; never HTML or instructions.
    const detail = {...result}; delete detail.artifacts;
    const disclosure = app.node("details", "ops-receipt");
    disclosure.append(app.node("summary", "", artifacts.length ? "Inspect the receipt and assumptions" : "Inspect saved observations and coverage"));
    disclosure.append(app.node("pre", "", JSON.stringify(detail, null, 2).slice(0, 40000)));
    root.append(disclosure);
  }
  async function submit(body) {
    if (busy || !app.state.authenticated) return;
    busy = true; setError();
    const token = app.state.token;
    const controls = [...document.querySelectorAll("#view-operations button")];
    for (const control of controls) control.disabled = true;
    const requestId = body.request_id || body.arguments?.request_id || "read-only";
    $("ops-status").textContent = `Running locally · ${requestId}`;
    try {
      const result = await app.api("/operations/run", {method:"POST", body});
      if (!app.state.authenticated || app.state.token !== token) return;
      if (result.ok === false) throw new Error(result.error || "The operation did not complete.");
      render(result);
      $("ops-status").textContent = `Local result available · ${requestId} · no model calls`;
      if (body.example === "market" && result.example_input_folder) {
        $("ops-market-path").value = result.example_input_folder + "/market-demo.csv";
        $("ops-market-metadata").value = result.example_input_folder + "/market-demo.json";
      }
    } catch (error) {
      if (app.state.authenticated && app.state.token === token) {
        setError(error.message);
        $("ops-status").textContent = `Request ${requestId} stopped. Inspect Workspace if the connection was interrupted; no automatic retry was made.`;
      }
    } finally { busy = false; for (const control of controls) control.disabled = false; }
  }
  const newId = () => "ops-" + crypto.randomUUID().replaceAll("-", "");
  function attach(value) {
    app = value;
    $("ops-business").addEventListener("submit", event => {
      event.preventDefault();
      try { submit(requestFor("business", {action:$("ops-business-action").value, path:$("ops-business-path").value, date:$("ops-business-date").value}, newId())); }
      catch (error) { setError(error.message); }
    });
    $("ops-market").addEventListener("submit", event => {
      event.preventDefault();
      try { submit(requestFor("market", {action:$("ops-market-action").value, path:$("ops-market-path").value, metadata:$("ops-market-metadata").value}, newId())); }
      catch (error) { setError(error.message); }
    });
    $("ops-controls").addEventListener("submit", event => {
      event.preventDefault();
      try { submit(requestFor("controls", {action:$("ops-controls-action").value, path:$("ops-controls-path").value, time:$("ops-controls-time").value}, newId())); }
      catch (error) { setError(error.message); }
    });
    for (const control of document.querySelectorAll("[data-operation-example]")) control.addEventListener("click", () => submit({example:control.dataset.operationExample, request_id:newId()}));
    $("ops-control-mission").addEventListener("click", () => {
      app.newMission();
      $("mission-prompt").value = "Inspect home_control catalog and saved status. Show me the exact configured devices, observation age and permitted actions. Ask which action I want before proposing it; device changes must use the existing approval flow. Never retry an uncertain action.";
      $("mission-agent").value = "local_controls";
      app.resizeComposer(); $("mission-prompt").focus();
    });
  }
  async function load() {
    if (!app?.state.authenticated || busy) return;
    try {
      await app.api("/operations");
      $("ops-status").textContent = "Ready for a local run. Saved device observations may be stale; imported files retain their source identities.";
    } catch (error) { setError(error.message); }
  }
  window.ariadneOperations = {attach, load, requestFor};
})();
