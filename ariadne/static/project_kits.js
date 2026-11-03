"use strict";

// Preparation produces local drafts. Workroom owns execution and its approvals.
(() => {
  const BASE = "/project-kits";
  const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/, SHA = /^[a-f0-9]{64}$/;
  let app, board = null, catalogue = null, sources = null, workroomRevision = null, specialistProfiles = [];
  let source = null, kit = null, selectedIntake = "", selectedKit = null, intakeDraft = "";
  let epoch = 0, busy = false, reading = false, readFailed = false, warning = "";
  let sourceConfirmed = false, adoptionConfirmed = false, uncertainty = null, receiptConfirmed = false, renderedControls = null;
  const root = () => document.getElementById("project-kits-content");
  const text = value => typeof value === "string" ? value : "";
  const clone = value => JSON.parse(JSON.stringify(value));
  const node = (tag, cls = "", value) => app.node(tag, cls, value);
  const paragraph = (parent, value, cls = "") => parent.append(node("p", cls, value));
  const requestId = prefix => prefix + crypto.randomUUID().replaceAll("-", "");
  const current = (mark, token) => mark === epoch && app?.state.authenticated && app.state.token === token;
  const sameNavigation=(generation,view)=>app?.state.navigationGeneration===generation&&app?.state.view===view&&(view==null||view==="project-kits");
  const paused = () => app?.state.status?.paused === true || board?.paused === true;
  const controls = () => ({authenticated:!!app?.state.authenticated, token:app?.state.token, paused:paused()});
  const writable = () => app?.state.authenticated && !paused() && !busy && !reading && !readFailed && !uncertainty;
  const validRevision = value => Number.isInteger(value) && value >= 0;
  const pretty = value => text(value).replaceAll("_", " ");
  const stamp = value => value && Number.isFinite(new Date(value).valueOf()) ? new Date(value).toLocaleString() : "Not recorded";

  function safePath(value) {
    const path = text(value).replaceAll("\\", "/");
    if (!path || path.length > 300 || path.startsWith("/") || path.includes(":") || path.split("/").some(part => !part || [".", ".."].includes(part))) throw new Error("Only saved relative Workspace artifacts can be opened.");
    return path;
  }
  function preparePayload(intakeId, preview, revision, id) {
    if (!ID.test(intakeId || "") || !SHA.test(preview?.source_sha256 || "") || !validRevision(revision) || !ID.test(id || "")) throw new Error("Refresh and inspect this exact saved intake before preparing a pack.");
    if (preview.schema !== 1 || !preview.source || typeof preview.source !== "object" || Array.isArray(preview.source) || !Number.isInteger(preview.source_bytes) || preview.source_bytes < 1 || preview.source_bytes > 65536) throw new Error("A complete source snapshot of at most 64 KiB is required.");
    return {request_id:id, expected_revision:revision, intake_id:intakeId, source_sha256:preview.source_sha256};
  }
  function adoptPayload(value, revision, projectRevision, id) {
    if (!value || !ID.test(value.id || "") || !SHA.test(value.content_sha256 || "") || !validRevision(revision) || !validRevision(projectRevision) || !ID.test(id || "")) throw new Error("Refresh and inspect the exact prepared pack and Workroom revision before adoption.");
    if (value.status !== "prepared" || value.hold || value.project_id) throw new Error("Only a prepared, unheld pack can create a new project.");
    if(!frozenScopesReady(value))throw new Error("Inspect the complete frozen local specialist scopes before adopting this pack.");
    return {request_id:id, expected_revision:revision, kit_id:value.id, content_sha256:value.content_sha256, expected_workroom_revision:projectRevision};
  }
  function frozenScopesReady(value){
    const workflows=value?.workflow_previews;
    if(!Array.isArray(workflows)||workflows.length!==2)return false;
    return workflows.every(workflow=>{
      const agent=workflow?.resolved_agent;
      return ["offline_operations","offline_research"].includes(workflow?.agent_id)&&workflow.isolated_outputs===true&&Array.isArray(workflow.checks)&&workflow.checks.length>0&&workflow.checks.length<=10
        &&agent?.id===workflow.agent_id&&agent.connectivity==="local_only"&&text(agent.instruction).length>0&&Array.isArray(agent.tools)&&agent.tools.every(name=>typeof name==="string")
        &&Number.isInteger(agent.max_steps)&&agent.max_steps>=1&&Number.isInteger(agent.profile_version)&&agent.profile_version>=1&&text(agent.approval_policy).length>0&&SHA.test(workflow.catalog_sha256||"");
    })&&new Set(workflows.map(workflow=>workflow.agent_id)).size===2;
  }
  function button(label, action, enabled = true, primary = false) {
    const control = app.button(label, primary ? "button-primary" : "button-secondary", action);
    control.disabled = !enabled;
    return control;
  }
  function disclosure(parent, title, value) {
    const detail = node("details", "pk-disclosure");
    detail.append(node("summary", "", title), node("pre", "", JSON.stringify(value, null, 2)));
    parent.append(detail);
  }
  function checkbox(parent, name, label, checked, change, enabled) {
    const wrapper = node("label", "pk-check"), control = node("input");
    control.type = "checkbox"; control.name = name; control.checked = checked; control.disabled = !enabled;
    control.addEventListener("change", () => {change(control.checked); render();});
    wrapper.append(control, node("span", "", label)); parent.append(wrapper);
  }
  function artifactList(parent, rows) {
    const list = node("div", "pk-artifacts");
    for (const artifact of Array.isArray(rows) ? rows : []) {
      try {
        if (!artifact || !SHA.test(artifact.sha256 || "") || !Number.isInteger(artifact.bytes) || artifact.bytes < 0) throw Error("Invalid artifact identity");
        const path = safePath(artifact.path), row = node("div", "pk-artifact");
        row.append(button(path, () => app.openArtifact?.(path), !busy));
        paragraph(row, `${artifact.bytes} bytes · SHA-256 ${artifact.sha256}`, "pk-hash"); list.append(row);
      } catch {paragraph(list, "A recorded artifact reference could not be offered safely. Inspect its receipt.", "pk-notice");}
    }
    parent.append(list);
  }
  function validStatus(value) {
    return value?.schema === 1 && validRevision(value.revision) && Array.isArray(value.kits);
  }
  async function load() {
    if (!app?.state.authenticated || busy) return;
    const mark = ++epoch, token = app.state.token;
    reading = true; sourceConfirmed = false; adoptionConfirmed = false; warning = ""; render();
    try {
      const [next, recipes, intakes, room, agents] = await Promise.all([
        app.api(BASE), app.api(BASE + "/catalog"), app.api(BASE + "/sources"), app.api("/workroom"), app.api("/agents")
      ]);
      if (!current(mark, token)) return;
      if (!validStatus(next) || recipes?.schema !== 1 || !Array.isArray(recipes.recipes) || intakes?.schema !== 1 || !Array.isArray(intakes.sources) || !validRevision(room?.revision)) throw Error("This server does not provide the Project Kits contract. Use its supported views or restart the updated installation.");
      board = next; catalogue = recipes; sources = intakes; workroomRevision = room.revision;
      specialistProfiles = Array.isArray(agents?.agents) ? agents.agents.filter(value => ["offline_operations","offline_research"].includes(value.id)) : [];
      readFailed = false;
      if (selectedKit) {
        const detail = await app.api(BASE + "/kits/" + encodeURIComponent(selectedKit));
        if (!current(mark, token)) return;
        if (!detail?.kit || detail.kit.id !== selectedKit || !validRevision(detail.revision)) throw Error("The saved pack response is inconsistent.");
        kit = detail.kit; board.revision = detail.revision;
      }
      if (selectedIntake) {
        const preview = await app.api(BASE + "/sources/" + encodeURIComponent(selectedIntake));
        if (!current(mark, token)) return;
        preparePayload(selectedIntake, preview, board.revision, "source-validation"); source = preview;
      }
    } catch (error) {if (current(mark, token)) {warning = error.message; readFailed = true;}}
    finally {if (current(mark, token)) {reading = false; render();}}
  }
  async function selectIntake(identifier) {
    if (!ID.test(identifier || "") || !app?.state.authenticated || busy) return;
    const initialToken=app.state.token,navigation=app.state.navigationGeneration,view=app.state.view;
    if(!sameNavigation(navigation,view))return;
    if (!board) {const pending=load(),loadingEpoch=epoch;await pending;if(!current(loadingEpoch,initialToken)||!sameNavigation(navigation,view))return;}
    if (!app?.state.authenticated || app.state.token!==initialToken || busy || !board||readFailed||!sameNavigation(navigation,view)) return;
    const mark = ++epoch, token = app.state.token;
    const present=()=>current(mark,token)&&sameNavigation(navigation,view);
    selectedIntake = identifier; intakeDraft = identifier; source = null; selectedKit = null; kit = null;
    sourceConfirmed = false; adoptionConfirmed = false; reading = true; warning = ""; render();
    try {
      const value = await app.api(BASE + "/sources/" + encodeURIComponent(identifier));
      if (!present()) return;
      preparePayload(identifier, value, board.revision, "source-validation"); source = value; readFailed = false;
    } catch (error) {if (present()) {selectedIntake="";source=null;warning = error.message; readFailed = true;}}
    finally {if (present()) {reading = false; render();}}
  }
  async function selectKit(identifier) {
    if (!ID.test(identifier || "") || !app?.state.authenticated || busy) return;
    const initialToken=app.state.token,navigation=app.state.navigationGeneration,view=app.state.view;
    if(!sameNavigation(navigation,view))return;
    if (!board) {const pending=load(),loadingEpoch=epoch;await pending;if(!current(loadingEpoch,initialToken)||!sameNavigation(navigation,view))return;}
    if(!app?.state.authenticated||app.state.token!==initialToken||busy||!board||readFailed||!sameNavigation(navigation,view))return;
    const mark = ++epoch, token = app.state.token;
    const present=()=>current(mark,token)&&sameNavigation(navigation,view);
    selectedKit = identifier; kit = null; source = null; selectedIntake = ""; intakeDraft = ""; sourceConfirmed = false; adoptionConfirmed = false; reading = true; warning = ""; render();
    try {
      const value = await app.api(BASE + "/kits/" + encodeURIComponent(identifier));
      if (!present()) return;
      if (!value?.kit || value.kit.id !== identifier || !validRevision(value.revision)) throw Error("The saved pack response is inconsistent.");
      kit = value.kit; board.revision = value.revision; readFailed = false;
    } catch (error) {if (present()) {selectedKit=null;kit=null;warning = error.message; readFailed = true;}}
    finally {if (present()) {reading = false; render();}}
  }
  async function mutate(action, body) {
    if (!writable()) return;
    const mark = epoch, token = app.state.token;
    let rejected = false;
    busy = true; sourceConfirmed = false; adoptionConfirmed = false; warning = "";
    uncertainty = {request_id:body.request_id, action, inspected:false}; receiptConfirmed = false; render();
    try {
      const value = await app.api(BASE + "/" + action, {method:"POST", body});
      if (!current(mark, token)) return;
      if (!value || value.status!=="completed" || !validRevision(value.revision) || !ID.test(value.kit_id || "")) throw Error("The operation returned no consistent completed pack receipt.");
      uncertainty = null; selectedKit = value.kit_id; kit = value.kit || null; source = null; selectedIntake = ""; intakeDraft = ""; board.revision = value.revision;
      app.toast?.(action === "prepare" ? "Source pack prepared locally. No workflow was started." : "Project adopted. Source review and a separate Workroom cycle are still required.");
    } catch (error) {
      if (current(mark, token)) {
        if ([400,409].includes(error.status)) {rejected = true; uncertainty = null; readFailed = true; warning = `Request rejected: ${error.message}. Refresh kits before another action. Nothing was retried.`;}
        else warning = `${error.message} Inspect this exact request receipt before another write; nothing was retried.`;
      }
    } finally {
      busy = false;
      if (current(mark, token)) {if (!uncertainty && !rejected) await load(); else render();}
      else if (app.state.authenticated) render();
    }
  }
  async function inspectReceipt() {
    if (!uncertainty || busy || !app?.state.authenticated) return;
    const mark = epoch, token = app.state.token, id = uncertainty.request_id;
    try {
      const value = await app.api(BASE + "/receipts/" + encodeURIComponent(id));
      if (!current(mark, token) || uncertainty?.request_id !== id) return;
      const receipt = value?.receipt || value;
      if (receipt.request_id !== id || !["completed","failed"].includes(receipt.status)) throw Error("A definite terminal receipt is not available; this request remains held.");
      if (ID.test(receipt.kit_id || "")) {
        const detail = await app.api(BASE + "/kits/" + encodeURIComponent(receipt.kit_id));
        if (!current(mark, token) || uncertainty?.request_id !== id) return;
        if (detail?.kit?.id !== receipt.kit_id) throw Error("The retained pack could not be inspected.");
        selectedKit = receipt.kit_id; kit = detail.kit;
      }
      uncertainty.receipt = clone(receipt); uncertainty.inspected = true; receiptConfirmed = false; render();
    } catch (error) {if (current(mark, token)) {warning = `${error.message} The uncertainty hold remains.`; render();}}
  }
  function uncertaintyPanel(parent) {
    if (!uncertainty) return;
    const region = node("section", "pk-notice pk-section"); region.append(node("h3", "", "Inspect the unconfirmed request"));
    paragraph(region, `Request ${uncertainty.request_id} may have changed local state. This tab will not replay it. Inspect its exact terminal receipt and retained pack before acknowledging this tab's uncertainty.`);
    region.append(button("Inspect request receipt", () => inspectReceipt(), !busy && !reading));
    if (uncertainty.receipt) disclosure(region, "Exact saved request receipt", uncertainty.receipt);
    checkbox(region, "kit_receipt_confirmed", "I inspected the terminal receipt and retained pack state.", receiptConfirmed, value => {receiptConfirmed = value;}, !busy && uncertainty.inspected === true);
    region.append(button("Acknowledge this tab's uncertainty", () => {
      if (!uncertainty?.inspected || !receiptConfirmed) return;
      uncertainty = null; receiptConfirmed = false; readFailed = true;
      warning = "This tab's uncertainty was acknowledged. Refresh kits before another action; retained server holds remain in force."; render();
    }, !busy && uncertainty.inspected === true && receiptConfirmed)); parent.append(region);
  }
  function sourcePanel(parent) {
    const region = node("section", "pk-section"); region.append(node("span", "pk-kicker", "01 / SELECT THE EVIDENCE"), node("h2", "", "Start with a saved intake."));
    paragraph(region, "Choose reviewed Service desk paperwork. Reading and preparing a pack use no model or network; source claims do not approve prices, permission, customer acceptance or payment.");
    const form = node("form", "pk-source-form"), label = node("label", "pk-field", "Saved intake"), select = node("select"); select.name = "kit_intake";
    const placeholder = node("option", "", "Choose an intake to inspect"); placeholder.value = ""; select.append(placeholder);
    for (const item of sources?.sources || []) {const option = node("option", "", `${item.title || item.intake_id} · ${item.intake_id}`); option.value = item.intake_id; select.append(option);}
    select.value = selectedIntake; select.disabled = busy || reading;
    select.addEventListener("change", () => {if (select.value) selectIntake(select.value);}); label.append(select); form.append(label);
    const exactLabel = node("label", "pk-field", "Or a saved intake identifier"), exact = node("input"); exact.name = "kit_intake_id"; exact.maxLength = 64; exact.value = intakeDraft; exact.disabled = busy || reading;
    exact.addEventListener("input", () => {intakeDraft = exact.value;}); exactLabel.append(exact); form.append(exactLabel);
    form.append(button("Inspect saved intake", () => {if (ID.test(intakeDraft)) return selectIntake(intakeDraft); warning = "Choose a saved intake identifier before reading it."; render();}, !busy && !reading));
    form.addEventListener("submit", event => {event.preventDefault(); if (ID.test(intakeDraft)) return selectIntake(intakeDraft);}); region.append(form);
    if (sources?.has_more) paragraph(region, "This list covers the newest 50 saved intakes. Supply an exact intake identifier to inspect an older saved record.", "pk-stamp");
    if (sources && !sources.sources.length) paragraph(region, "No saved intake is available. Create and review an intake in Service desk first; a fictional demo is separate from real-client records.", "pk-notice");
    if (source) {
      const snapshot = node("div", "pk-source-snapshot");
      snapshot.append(node("h3", "", "Source snapshot for review"));
      paragraph(snapshot, `Intake ${selectedIntake} · ${source.source_bytes} bytes`, "pk-stamp"); paragraph(snapshot, `SHA-256 ${source.source_sha256}`, "pk-hash");
      const facts=source.source.intake, provider=source.source.provider;
      if(facts&&typeof facts==="object"){
        const summary=node("dl","pk-facts");
        for(const [label,value] of [["Recorded job",facts.title],["Service business",provider?.name],["Customer",facts.customer?.name],["Recorded service date",facts.service_date]])summary.append(node("dt","",label),node("dd","",text(value)||"Not recorded"));
        snapshot.append(summary);
        const gaps=[];
        if(!facts.customer?.name)gaps.push("Customer name is not recorded.");
        if(!facts.service_date)gaps.push("Service date is not recorded.");
        const lines=Array.isArray(facts.lines)?facts.lines:[],unpriced=lines.filter(row=>row.unit_price===null).length,unreviewed=lines.filter(row=>row.price_approved!==true).length;
        if(unpriced)gaps.push(`${unpriced} service line(s) have no recorded unit price.`);
        if(unreviewed)gaps.push(`${unreviewed} service line(s) lack a saved price approval.`);
        if(facts.tax?.bps===null)gaps.push("Tax treatment is not recorded.");
        if(facts.permission?.confirmed!==true)gaps.push("Permission is not confirmed in the intake.");
        if(gaps.length){snapshot.append(node("h3","","Recorded gaps to resolve"));const list=node("ul","pk-gaps");for(const value of gaps)list.append(node("li","",value));snapshot.append(list);}
      }
      const listed=sources?.sources?.find(value=>value.intake_id===selectedIntake);
      if(listed?.provider_changed)paragraph(snapshot,"The current service-business profile differs from the intake's retained profile. Both snapshots remain separate; inspect the complete source before preparing.","pk-notice");
      if(Array.isArray(source.source.limitations)){const limits=node("ul","pk-gaps");for(const value of source.source.limitations)limits.append(node("li","",String(value)));snapshot.append(limits);}
      paragraph(snapshot, "Inspect the complete saved snapshot below. Missing or disputed facts remain unresolved; imported text is evidence, never instructions or commercial authority.");
      disclosure(snapshot, "Complete bounded source snapshot", source.source);
      const existingId=source.existing_kit_id||listed?.existing_kit_id;
      if(ID.test(existingId||"")){
        paragraph(snapshot,"This intake already has a retained preparation. Inspect that pack and its outcome instead of preparing the same intake again.","pk-notice");
        snapshot.append(button("Inspect existing kit",()=>selectKit(existingId),!busy&&!reading));
      }else{
        checkbox(snapshot, "kit_source_confirmed", "I reviewed this exact source snapshot and want to prepare a local draft work pack.", sourceConfirmed, value => {sourceConfirmed = value;}, writable());
        snapshot.append(button("Prepare source pack", () => {if (!sourceConfirmed) return; try {return mutate("prepare", preparePayload(selectedIntake, source, board?.revision, requestId("kit-prepare-")));} catch (error) {warning = error.message; render();}}, writable() && sourceConfirmed, true));
      }
      region.append(snapshot);
    }
    parent.append(region);
  }
  function savedKits(parent) {
    const rail = node("aside", "pk-rail"); rail.setAttribute("aria-label", "Prepared project kits");
    rail.append(node("span", "pk-kicker", "RETAINED WORK PACKS"), node("h3", "", "Prepared kits"));
    for (const row of board?.kits || []) {
      const control = button("", () => selectKit(row.id), !busy && !reading); control.className = "pk-kit-button";
      control.setAttribute("aria-label", `Inspect kit ${row.title || row.id}`); control.setAttribute("aria-pressed", String(row.id === selectedKit));
      control.append(node("span", "pk-kicker", pretty(row.status)), node("strong", "", row.title || row.id), node("small", "", stamp(row.created_at))); rail.append(control);
    }
    if (!board?.kits?.length) paragraph(rail, "Prepared source packs and their adoption receipts appear here."); parent.append(rail);
  }
  function kitPanel(parent) {
    if (!kit) return;
    const region = node("section", "pk-section pk-prepared");
    region.append(node("span", "pk-kicker", "02 / REVIEW THE PREPARED PROJECT"), node("h2", "", kit.title || "Saved work pack"));
    paragraph(region, `${pretty(kit.status)} · ${stamp(kit.created_at)} · Intake ${kit.intake_id || "not recorded"}`, "pk-stamp");
    paragraph(region, `Pack fingerprint: ${kit.content_sha256 || "Not available"}`, "pk-hash");
    if (kit.hold || ["held","failed","running","interrupted"].includes(kit.status)) paragraph(region, "This preparation has a retained hold or incomplete outcome. Inspect its receipt; preparing or adopting it again cannot resolve uncertainty.", "pk-notice");
    if(text(kit.error))paragraph(region,`Saved failure: ${kit.error}`,"pk-notice");
    paragraph(region, "These files preserve the reviewed input. They are drafts and retained source identities, not customer permission, an approved quote or a completed service.");
    artifactList(region, kit.artifacts);
    if (kit.snapshot) disclosure(region, "Exact retained source snapshot", kit.snapshot);
    const path = node("ol", "pk-path");
    const tasks = kit.project_preview?.tasks || [];
    for (const task of tasks) {const item = node("li"); item.append(node("strong", "", task.title || task.id)); paragraph(item, `${pretty(task.kind)} · ${task.depends_on?.length ? "After " + task.depends_on.join(", ") : "No task dependency"}`, "pk-stamp"); paragraph(item, text(task.acceptance)); path.append(item);} region.append(path);
    if(kit.project_preview)disclosure(region,"Exact prepared project definition",kit.project_preview);
    for (const workflow of kit.workflow_previews || []) {
      const preview = node("section", "pk-workflow"); preview.append(node("h3", "", workflow.name || "Prepared business workflow"));
      paragraph(preview, `${workflow.agent_id || "local"} specialist · isolated outputs · saved draft only`, "pk-stamp");
      disclosure(preview, "Exact prepared instructions and output checks", workflow); region.append(preview);
      if(workflow.resolved_agent)disclosure(preview,"Frozen specialist instructions and permitted tools",{resolved_agent:workflow.resolved_agent,catalog_sha256:workflow.catalog_sha256});
    }
    if (specialistProfiles.length) disclosure(region, "Current local specialist scopes at this read (observation only)", specialistProfiles);
    paragraph(region, "Adopting saves two workflows and the project together. Its source-review gate remains unaccepted, and no agent runs. Start a separate bounded Workroom cycle only after inspecting the adopted instructions, tools and checks.");
    const eligible = kit.status === "prepared" && !kit.hold && !kit.project_id && validRevision(workroomRevision)&&frozenScopesReady(kit);
    if(kit.status==="prepared"&&!frozenScopesReady(kit))paragraph(region,"The complete frozen local specialist scopes are missing or inconsistent. Refresh and inspect the saved pack; adoption remains unavailable.","pk-notice");
    if (eligible) {
      checkbox(region, "kit_adoption_confirmed", "I reviewed this exact pack and prepared plan. Save the workflows and project without executing them.", adoptionConfirmed, value => {adoptionConfirmed = value;}, writable());
      region.append(button("Adopt reviewed project", () => {if (!adoptionConfirmed) return; try {return mutate("adopt", adoptPayload(kit, board?.revision, workroomRevision, requestId("kit-adopt-")));} catch (error) {warning = error.message; render();}}, writable() && adoptionConfirmed, true));
    }
    if (ID.test(kit.project_id || "")) {
      paragraph(region, "03 / CONTINUE IN WORKROOM", "pk-kicker"); paragraph(region, `Saved project ${kit.project_id}. Source review and execution authorization remain separate.`, "pk-stamp");
      region.append(button("Open adopted project", () => app.openProject?.(kit.project_id), !busy && !reading));
    }
    if (kit.adoption_receipt) disclosure(region, "Saved adoption receipt", kit.adoption_receipt);
    parent.append(region);
  }
  function render() {
    const mount = root(); if (!mount || !app) return;
    renderedControls = controls(); mount.replaceChildren();
    if (!app.state.authenticated) {mount.append(node("p", "pk-notice", "Pair this tab to inspect private source-linked project kits.")); return;}
    const shell = node("div", "pk-shell"), heading = node("div", "pk-heading"), intro = node("div");
    intro.append(node("span", "pk-kicker", "PROJECT KITS / EVIDENCE INTO WORK"), node("h1", "", "Give the source a path."));
    paragraph(intro, "Reviewed paperwork → retained input → prepared agent work → a separate owner handoff."); heading.append(intro);
    if(catalogue?.recipes?.[0])paragraph(intro,`${catalogue.recipes[0].name}: ${catalogue.recipes[0].description}`,"pk-stamp");
    heading.append(button("Refresh kits", () => load(), !busy && !reading)); shell.append(heading);
    if (reading || busy) paragraph(shell, busy ? "Recording one explicit local request…" : "Reading saved source and project state…", "pk-stamp");
    if (warning) paragraph(shell, warning, "pk-notice");
    if (paused()) paragraph(shell, "Global autonomy is paused. Inspection remains available; preparation and adoption are held.", "pk-notice");
    uncertaintyPanel(shell);
    const layout = node("div", "pk-layout"); savedKits(layout); const main = node("div", "pk-main"); sourcePanel(main); kitPanel(main); layout.append(main); shell.append(layout);
    const footer = node("footer", "pk-scope"); paragraph(footer, "Preparation uses no inference, network, paid service or recurring worker. Only an explicitly started Workroom cycle uses its existing model and tool budgets. A generated plan does not establish revenue, customer consent, delivery or payment."); shell.append(footer); mount.append(shell);
  }
  function leave() {
    epoch++; board = null; catalogue = null; sources = null; specialistProfiles = []; workroomRevision = null;
    source = null; kit = null; selectedIntake = ""; selectedKit = null; intakeDraft = ""; warning = "";
    sourceConfirmed = false; adoptionConfirmed = false; reading = false; readFailed = false; receiptConfirmed = false;
    if (uncertainty) uncertainty = {request_id:uncertainty.request_id, action:uncertainty.action, inspected:false};
    renderedControls = controls(); root()?.replaceChildren();
  }
  function locked() {leave(); render();}
  function status() {
    if (!app) return;
    const next = controls();
    if (renderedControls && next.authenticated === renderedControls.authenticated && next.token === renderedControls.token && next.paused === renderedControls.paused) return;
    if (renderedControls && (next.authenticated !== renderedControls.authenticated || next.token !== renderedControls.token)) leave();
    sourceConfirmed = false; adoptionConfirmed = false; render();
  }
  function attach(value) {app = value; render();}
  window.ariadneProjectKits = {attach, load, leave, locked, status, selectIntake, selectKit, preparePayload, adoptPayload, safePath,frozenScopesReady};
})();
