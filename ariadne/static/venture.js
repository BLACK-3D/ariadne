"use strict";

// Protected planning state and fixed local reports. No timer or action retry.
(() => {
  let app, data = null, busy = false, loading = false, generation = 0, attached = null;
  let notice = "", uncertain = null, inspected = false, stale = false, studyResult = null;
  let evidenceDraft = {}, studyDraft = {path:"", cost:"5"};
  const $ = id => document.getElementById(id), trim = value => String(value ?? "").trim();
  const idPattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
  const title = value => String(value || "unknown").replaceAll("_", " ");
  const pct = value => typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "Not measured";
  const stamp = value => { const date = new Date(value); return value && Number.isFinite(date.valueOf()) ? date.toLocaleString() : "Not recorded"; };
  const current = (marker, token) => marker === generation && app?.state.authenticated && app.state.token === token;

  function dayKey(timezone = "America/Chicago", now = new Date()) {
    try {
      const parts = new Intl.DateTimeFormat("en-CA", {timeZone:timezone,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(now);
      const get = key => parts.find(part => part.type === key)?.value;
      return `${get("year")}-${get("month")}-${get("day")}`;
    } catch { return null; }
  }
  function canAdvance(value, globalPaused = false, localDay = null) {
    if (!value || value.state !== "ready" || !value.config?.enabled || value.config.paused || globalPaused || value.briefing?.permitted !== true || value.latest?.status === "running") return false;
    const day = localDay || value.local_day || dayKey(value.briefing?.timezone);
    return !(day && value.latest?.local_day === day && value.latest?.status === "completed");
  }
  function relativePath(value) {
    const path = trim(value).replaceAll("\\", "/");
    if (!path || path.length > 1000 || path.startsWith("/") || /^[A-Za-z]:/.test(path) || path.split("/").some(part => !part || part === "." || part === "..")) throw new Error("Choose a relative file in Ariadne Workspace.");
    return path;
  }
  function evidencePayload(fields, revision, now = Date.now()) {
    if (!Number.isInteger(revision) || revision < 0 || !idPattern.test(trim(fields.task))) throw new Error("Refresh and choose a completed local draft.");
    const kind = trim(fields.kind), note = trim(fields.note), observed = new Date(fields.observed);
    if (!["review","interview","customer"].includes(kind) || !note || note.length > 2000) throw new Error("Choose an owner-evidence type and a note of 1–2,000 characters.");
    if (!fields.observed || !Number.isFinite(observed.valueOf()) || observed.valueOf() > now) throw new Error("Choose the actual observation time; future observations are not accepted.");
    const evidence = {task_id:trim(fields.task),kind,note,observed_at:observed.toISOString()};
    if (trim(fields.path) || trim(fields.sha)) {
      if (!/^[a-f0-9]{64}$/.test(trim(fields.sha))) throw new Error("A referenced evidence file needs its exact lowercase SHA-256.");
      evidence.path = relativePath(fields.path); evidence.sha256 = trim(fields.sha);
    }
    return {evidence,expected_revision:revision};
  }
  function studyPayload(fields, requestId) {
    if (!idPattern.test(requestId)) throw new Error("A new study needs an ordinary unique request ID.");
    if (!trim(fields.cost)) throw new Error("State the one-way cost assumption explicitly.");
    const cost = Number(fields.cost);
    if (!Number.isFinite(cost) || cost < 0 || cost > 100) throw new Error("Choose a finite one-way cost from 0 to 100 basis points.");
    return {tool:"etf_research",arguments:{action:"study",path:relativePath(fields.path),request_id:requestId,cost_bps:cost}};
  }
  const newId = prefix => prefix + crypto.randomUUID().replaceAll("-", "");
  function element(tag, className = "", text) { return app.node(tag,className,text); }
  function paragraph(parent, text, className = "") { parent.append(element("p",className,text)); }
  function action(label, callback, className = "button-secondary") {
    const result = app.button(label,className,callback); result.dataset.ventureMutation = "true"; return result;
  }
  function field(form, label, name, kind = "text", value = "") {
    const wrapper = element("label","venture-field",label), input = element(kind === "textarea" ? "textarea" : "input");
    if (kind !== "textarea") input.type = kind;
    input.name = name; input.value = value; wrapper.append(input); form.append(wrapper); return input;
  }
  function controls() {
    const disabled = busy || loading || !app?.state.authenticated || Boolean(uncertain) || stale;
    const run = $("run-venture"), refresh = $("refresh-venture");
    if (run) {
      run.disabled = disabled || !canAdvance(data, Boolean(app?.state.status?.paused));
      const today = data?.local_day || dayKey(data?.briefing?.timezone);
      run.textContent = busy ? "Working locally…" : today && data?.latest?.local_day === today && data.latest.status === "completed" ? "Today's check-in is ready" : "Create today's check-in";
    }
    if (refresh) refresh.disabled = busy || loading || !app?.state.authenticated;
    for (const control of document.querySelectorAll("#venture-content [data-venture-mutation]")) control.disabled = disabled || control.dataset.ventureMutation === "study" && Boolean(app?.state.status?.paused);
  }
  function cards(rows, container) {
    for (const row of rows || []) {
      const item = element("div","venture-metric"); item.append(element("span","",row.label),element("strong","",row.value)); container.append(item);
    }
  }
  function artifactButtons(parent, artifacts) {
    const actions = element("div","venture-artifacts");
    for (const item of artifacts || []) if (item && typeof item.path === "string") {
      const name = item.path.endsWith(".md") ? "Read report" : item.path.endsWith(".json") ? "Inspect report data" : "Open artifact";
      actions.append(app.button(name,"button-secondary",() => app.openArtifact(item.path),"file"));
    }
    if (actions.childNodes.length) parent.append(actions);
  }
  function metricTable(parent, rows) {
    if (!Array.isArray(rows) || !rows.length) return;
    const table = element("table","venture-results"), head = element("thead"), header = element("tr");
    for (const name of ["Hypothesis","Historical CAGR","Close drawdown","Coverage"]) header.append(element("th","",name));
    head.append(header); table.append(head); const body = element("tbody");
    for (const row of rows.slice(0,8)) {
      const line = element("tr"), metrics = row.metrics || {};
      for (const value of [title(row.id || row.name),pct(metrics.cagr),pct(metrics.maximum_close_drawdown),row.status === "completed" ? "Historical proxy" : row.reason || title(row.status)]) line.append(element("td","",value));
      body.append(line);
    }
    table.append(body); const scroll = element("div","venture-table-scroll"); scroll.append(table); parent.append(scroll);
  }
  function compactSource(source) {
    try { const value = JSON.parse(source.accepted_excerpt || "null"); return value && typeof value === "object" ? value : {}; }
    catch { return {}; }
  }
  function projectCard(project) {
    const card = element("article","venture-project"), business = project.domain === "business";
    card.append(element("span","eyebrow",business ? "BUSINESS / VALIDATE THE VALUE" : "ETF / HISTORICAL RESEARCH"),element("h2","",project.name));
    const first = (project.sources || [])[0], source = first ? compactSource(first) : {};
    const description = [source.one_liner,source.value_proposition,source.offer,source.description].find(value => typeof value === "string");
    if (description) paragraph(card,description.slice(0,500),"venture-description");
    if (!business) paragraph(card,"NAV results remain an execution proxy. No strategy is a proven future winner.","venture-caution");
    const tasks = (data.config.tasks || []).filter(task => task.project_id === project.id), progress = new Map((data.progress || []).map(row => [row.task_id,row]));
    const evidence = new Set((data.evidence || []).map(row => row.task_id));
    const list = element("ol","venture-task-list");
    for (const task of tasks) {
      const saved = progress.get(task.id), next = (data.next_actions || []).find(row => row.task_id === task.id), row = element("li");
      // The displayed evidence list is bounded; only server next_actions decides eligibility.
      const label = saved?.status === "draft_ready" ? task.requires_owner_evidence ? evidence.has(task.id) ? "Local draft ready · owner evidence recorded" : "Local draft ready · owner-evidence gate" : "Local draft ready" : saved ? `${title(saved.status)} · review receipt` : next?.eligible ? "Ready for a local draft" : "Waiting on earlier evidence";
      row.append(element("strong","",task.title),element("span","venture-task-state",label));
      if (next?.blockers?.length) row.append(element("small","",next.blockers.join("; ")));
      list.append(row);
    }
    if (list.childNodes.length) card.append(list);
    for (const record of project.sources || []) {
      const line = element("div","venture-source");
      line.append(element("span","venture-source-age",`Source as of ${record.data_as_of || "not supplied"} · ${title(record.freshness)}${Number.isFinite(record.age_calendar_days) ? ` · ${record.age_calendar_days} calendar days` : ""}`));
      line.append(app.button("Open registered source file","text-button",() => app.openArtifact(record.path),"file"));
      const detail = element("details"); detail.append(element("summary","","Source identity"),element("p","venture-hash",`${record.path} · SHA-256 ${record.sha256}`)); line.append(detail); card.append(line);
    }
    if (!business) metricTable(card,source.strategies || source.candidates);
    return card;
  }
  function evidenceForm(parent) {
    const ready = (data.progress || []).filter(row => row.status === "draft_ready" && row.run_status === "completed");
    const tasks = new Map((data.config.tasks || []).map(task => [task.id,task]));
    const eligible = ready.filter(row => tasks.has(row.task_id));
    parent.append(element("h2","","Add owner evidence"));
    paragraph(parent,"Record what you observed. This is an owner report, not independent verification or an action performed by Ariadne.","venture-muted");
    if (!eligible.length) { paragraph(parent,"Evidence can be attached after a local draft is ready.","venture-empty"); return; }
    const form = element("form","venture-form"), label = element("label","venture-field","Completed local draft"), select = element("select");
    select.name = "task"; for (const row of eligible) { const option = element("option","",tasks.get(row.task_id).title); option.value=row.task_id; select.append(option); }
    if (eligible.some(row => row.task_id === evidenceDraft.task)) select.value = evidenceDraft.task;
    label.append(select); form.append(label);
    const kindLabel = element("label","venture-field","Evidence type"), kind = element("select"); kind.name="kind";
    for (const [value,text] of [["review","Owner review"],["interview","Interview you conducted"],["customer","Customer evidence you report"]]) { const option=element("option","",text); option.value=value; kind.append(option); }
    kind.value=evidenceDraft.kind || "review"; kindLabel.append(kind); form.append(kindLabel);
    const local = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
    const observed = field(form,"Observed at · this PC's local time","observed","datetime-local",evidenceDraft.observed || local); observed.required=true;
    const note=field(form,"What happened?","note","textarea",evidenceDraft.note || ""); note.maxLength=2000; note.required=true; note.rows=4;
    const extra=element("details","venture-extra"); extra.append(element("summary","","Reference an existing workspace file (optional)"));
    const path=field(extra,"Evidence file","path","text",evidenceDraft.path || ""); path.maxLength=1000; path.placeholder="evidence/owner-notes.md";
    const sha=field(extra,"File SHA-256","sha","text",evidenceDraft.sha || ""); sha.maxLength=64; form.append(extra);
    const values=() => ({task:select.value,kind:kind.value,observed:observed.value,note:note.value,path:path.value,sha:sha.value});
    form.addEventListener("input",() => { evidenceDraft=values(); });
    const submit=element("button","button button-primary","Save owner-reported evidence"); submit.type="submit"; submit.dataset.ventureMutation="true"; form.append(submit);
    form.addEventListener("submit",event => {
      event.preventDefault(); evidenceDraft=values(); let body;
      try { body=evidencePayload(evidenceDraft,data.revision); } catch(error) { notice=error.message; renderNotice(); return; }
      mutate("evidence","/venture/evidence",{method:"POST",body},null,() => { evidenceDraft={}; });
    });
    parent.append(form);
  }
  function studyForm(parent) {
    parent.append(element("span","eyebrow","DETERMINISTIC / OFFLINE"),element("h2","","Test a local ETF dataset"));
    paragraph(parent,"Use a canonical NAV JSON dataset already in Workspace. The fixed monthly rules retain costs, benchmark, losing periods and qualification limits. This runs no model and places no orders.","venture-muted");
    const form=element("form","venture-form"), path=field(form,"Canonical dataset file","path","text",studyDraft.path), cost=field(form,"One-way cost assumption · basis points","cost","number",studyDraft.cost);
    path.placeholder="research/etf-nav.json"; path.required=true; path.maxLength=1000; cost.required=true; cost.min="0"; cost.max="100"; cost.step="any";
    form.addEventListener("input",() => { studyDraft={path:path.value,cost:cost.value}; });
    const submit=element("button","button button-secondary","Run fixed local study"); submit.type="submit"; submit.dataset.ventureMutation="study"; form.append(submit);
    form.addEventListener("submit",event => {
      event.preventDefault(); if (app.state.status?.paused) return;
      studyDraft={path:path.value,cost:cost.value}; const id=newId("etf-ui-"); let body;
      try { body=studyPayload(studyDraft,id); } catch(error) { notice=error.message; renderNotice(); return; }
      mutate("study","/operations/run",{method:"POST",body},id,result => { studyResult=result; });
    });
    parent.append(form);
    if (studyResult) {
      const result=element("section","venture-study-result"); result.append(element("h3","",studyResult.ok === false ? "Study stopped" : "Research result saved"));
      paragraph(result,`Accepted input SHA-256: ${studyResult.accepted_input?.sha256 || "not supplied"}`,"venture-hash");
      paragraph(result,`Analysis cutoff: ${studyResult.source_as_of || "not supplied"}. This is not a live-price timestamp.`,"venture-muted");
      if (studyResult.summary?.data_last_dates) paragraph(result,`Last observations: ${Object.entries(studyResult.summary.data_last_dates).map(([symbol,day]) => `${symbol} ${day || "unavailable"}`).join(" · ")}`,"venture-muted");
      paragraph(result,`Dividends: ${title(studyResult.summary?.dividend_status)}. Month-end coverage: ${title(studyResult.summary?.month_end_coverage_status)}.`,"venture-muted");
      paragraph(result,"Historical NAV proxy · execution unqualified · no future profit proved.","venture-caution");
      metricTable(result,studyResult.summary?.strategies); artifactButtons(result,studyResult.artifacts); parent.append(result);
    }
  }
  function renderNotice() {
    const root=$("venture-notice"); if (!root) return; root.replaceChildren(); root.hidden=!notice && !uncertain && !stale;
    if (notice) root.append(element("p","",notice));
    if (uncertain) {
      paragraph(root,`The ${uncertain.kind} response was interrupted${uncertain.id ? ` · request ${uncertain.id}` : ""}. Refresh and inspect saved receipts or owner evidence before another action. Nothing is retried automatically.`);
      if (uncertain.kind === "study" && uncertain.id) root.append(app.button("Inspect possible study output","button-secondary",() => app.openArtifact(`reports/etf-research/${uncertain.id}/study.md`),"file"));
      const acknowledge=app.button("I reviewed the saved records","button-secondary",() => { uncertain=null; inspected=false; notice=""; render(); }); acknowledge.disabled=!inspected; root.append(acknowledge);
    }
    controls();
  }
  function render() {
    const root=$("venture-content"); if (!root || !data || !app.state.authenticated) return;
    root.replaceChildren(); const note=element("section","venture-notice"); note.id="venture-notice"; note.setAttribute("role","status"); root.append(note);
    const overview=element("section","venture-overview"), heading=element("div"); heading.append(element("span","eyebrow","LOCAL PROGRESS / EVIDENCE FIRST"),element("h2","",data.state === "ready" ? "Make the next useful move." : data.state === "held" ? "Review before advancing." : "Progress is held."));
    const briefing=data.briefing, schedule=briefing ? `${String(briefing.hour).padStart(2,"0")}:${String(briefing.minute).padStart(2,"0")} ${briefing.timezone}` : "the local daily brief";
    paragraph(heading,`Up to ${data.config.max_drafts_per_day || 2} local drafts per day, attached to ${schedule}. No model calls or external actions.`,"venture-muted"); overview.append(heading);
    const counts=element("div","venture-metrics"); cards([{label:"State",value:title(data.state)},{label:"Local drafts ready",value:(data.progress || []).filter(row => row.status === "draft_ready").length},{label:"Next eligible tasks",value:(data.next_actions || []).filter(row => row.eligible).length}],counts); overview.append(counts); root.append(overview);
    if (data.hold) {
      const hold=element("section","venture-hold"); hold.append(element("h3","","Venture progress is held")); paragraph(hold,data.hold.reason); paragraph(hold,`Receipt: ${data.hold.request_id}. Failed or interrupted work is retained and will not replay.`,"venture-muted");
      hold.append(action("Review and acknowledge hold",acknowledgeHold)); root.append(hold);
    } else if (briefing?.permitted !== true || app.state.status?.paused) paragraph(root,"Autonomy or briefing controls hold advancement. Review those controls before creating a new check-in.","venture-caution");
    const layout=element("div","venture-layout"), main=element("div","venture-projects");
    for (const project of data.projects || []) main.append(projectCard(project));
    if (!(data.projects || []).length) paragraph(main,"Projects will appear after reviewed plans and source snapshots are registered.","venture-empty");
    const side=element("aside","venture-sidebar"), evidence=element("section","venture-panel"), study=element("section","venture-panel"); evidenceForm(evidence); studyForm(study); side.append(evidence,study); layout.append(main,side); root.append(layout);
    const latest=element("section","venture-latest"); latest.append(element("span","eyebrow","SAVED CHECK-IN"),element("h2","",data.latest ? `${data.latest.local_day || "Recorded"} · ${title(data.latest.status)}` : "Awaiting the first local check-in"));
    if (data.latest) { paragraph(latest,`Created ${stamp(data.latest.created_at)}. A local draft being ready does not establish validated demand, a customer or a successful trade.`,"venture-muted"); artifactButtons(latest,data.latest.artifacts); }
    const history=element("div","venture-evidence-history");
    for (const row of (data.evidence || []).slice(0,8)) { const item=element("article"); item.append(element("span","eyebrow",`OWNER REPORTED / ${title(row.kind)}`),element("p","",row.note),element("small","",`${row.task_id} · observed ${stamp(row.observed_at)} · not independently verified`)); history.append(item); }
    if (history.childNodes.length) latest.append(history); root.append(latest);
    paragraph(root,"Source snapshots retain their dates. Business assumptions still need validation; historical NAV results do not establish executable fills or future profits.","venture-footer");
    renderNotice(); controls();
  }
  async function load() {
    if (!app?.state.authenticated || busy || loading) return;
    loading=true; controls(); const marker=generation, token=app.state.token;
    try { const result=await app.api("/venture"); if (!current(marker,token)) return; data=result; stale=false; if (uncertain) inspected=true; render(); }
    catch(error) { if (current(marker,token)) { notice=error.message; renderNotice(); } }
    finally { if (current(marker,token)) { loading=false; controls(); } }
  }
  async function mutate(kind,path,options,id,onSuccess) {
    if (busy || loading || uncertain || stale || !app?.state.authenticated) return;
    busy=true; notice=""; const marker=generation, token=app.state.token; controls();
    try {
      const result=await app.api(path,options); if (!current(marker,token)) return;
      if (result.ok === false) throw Object.assign(new Error(result.error || "The local operation did not complete."),{status:400});
      if (onSuccess) onSuccess(result);
      if (result.status === "held" || result.status === "failed") notice=result.reason || result.error || "The request is held. Inspect its saved receipt before continuing.";
      else app.toast(kind === "study" ? "Local study saved; its limitations remain visible." : kind === "evidence" ? "Owner-reported evidence recorded." : "Venture state recorded.");
    } catch(error) {
      if (!current(marker,token)) return;
      notice=error.message;
      if (error.status === 409) stale=true;
      else if (!error.status || error.status >= 500) uncertain={kind,id};
    } finally {
      if (current(marker,token)) { busy=false; render(); if (!uncertain && !stale) await load(); }
    }
  }
  async function advance() {
    if (!canAdvance(data,Boolean(app?.state.status?.paused))) return;
    const id=newId("venture-ui-"); await mutate("check-in","/venture/run",{method:"POST",body:{request_id:id}},id);
  }
  async function acknowledgeHold() {
    if (busy || !data?.hold || !app.confirmAction) return;
    const marker=generation, token=app.state.token, hold={...data.hold}, revision=data.revision;
    const confirmed=await app.confirmAction("Acknowledge this Venture Lab hold?",`${hold.reason} Earlier requests and failed tasks will not replay. Revised work needs explicit new task IDs.` ,"Acknowledge reviewed hold","Keep held");
    if (!confirmed || !current(marker,token)) return;
    await mutate("hold","/venture/hold",{method:"POST",body:{request_id:hold.request_id,expected_revision:revision}});
  }
  function status() { controls(); }
  function locked() { generation++; data=null; busy=false; loading=false; notice=""; uncertain=null; inspected=false; stale=false; studyResult=null; evidenceDraft={}; studyDraft={path:"",cost:"5"}; $("venture-content")?.replaceChildren(); controls(); }
  function attach(value) {
    app=value; const root=$("venture-content"); if (!root || attached === root) return; attached=root;
    $("refresh-venture")?.addEventListener("click",load); $("run-venture")?.addEventListener("click",advance); controls();
  }
  window.ariadneVenture={attach,load,status,locked,canAdvance,evidencePayload,studyPayload,dayKey,advance};
})();
