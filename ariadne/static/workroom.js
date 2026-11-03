"use strict";

// Project intent, actual child receipts and owner decisions stay separate.
(() => {
  const BASE = "/workroom";
  const ROUTES = {status:BASE, catalog:BASE + "/catalog", save:BASE + "/projects", start:BASE + "/cycles/start", stop:BASE + "/cycles/stop", reconcile:BASE + "/reconcile", review:BASE + "/tasks/review"};
  const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/, SHA = /^[a-f0-9]{64}$/;
  let app, board = null, catalog = null, project = null, selected = null, selectedTask = null;
  let epoch = 0, busy = false, reading = false, readFailed = false, warning = "", uncertainty = null, inspection = false;
  let editor = null, cycleReview = false, cycleConfirmed = false, childBudget = 1, stepBudget = 6, reviewNote = "", reviewSources = "[]";
  let renderedControls = null;
  const root = () => document.getElementById("workroom-content");
  const text = value => typeof value === "string" ? value : "";
  const clean = value => text(value).trim();
  const clone = value => JSON.parse(JSON.stringify(value));
  const id = prefix => prefix + crypto.randomUUID().replaceAll("-", "");
  const current = (mark, token) => mark === epoch && app?.state.authenticated && app.state.token === token;
  const node = (tag, cls = "", value) => app.node(tag, cls, value);
  const paragraph = (parent, value, cls = "") => parent.append(node("p", cls, value));
  const pretty = value => text(value).replaceAll("_", " ");
  const stamp = value => value && Number.isFinite(new Date(value).valueOf()) ? new Date(value).toLocaleString() : "Not recorded";
  const paused = () => board?.paused === true || app?.state.status?.paused === true;
  const controlState = () => ({authenticated:!!app?.state.authenticated,token:app?.state.token,paused:paused()});
  const writable = () => app?.state.authenticated && !busy && !reading && !readFailed && !uncertainty && !paused();
  const revision = () => Number.isInteger(board?.revision) && board.revision >= 0 ? board.revision : null;
  const meta = prefix => {
    if (revision() === null) throw new Error("Refresh the saved Workroom before changing it.");
    return {request_id:id(prefix), expected_revision:revision()};
  };
  const activeCycle = () => project?.cycle || null;
  const cycleActive = cycle => !!cycle && ["running", "active", "queued", "waiting_approval", "needs_review"].includes(cycle.status);
  function artifactPath(value) {
    const path = text(value).replaceAll("\\", "/");
    if (!path || path.length > 1000 || path.startsWith("/") || path.includes(":") || path.split("/").some(part => !part || [".", ".."].includes(part))) throw new Error("Only a saved Workspace artifact can be opened here.");
    return path;
  }
  function bounded(value, label, maximum) {
    const result = clean(value);
    if (!result || result.length > maximum) throw new Error(`${label} needs 1–${maximum} characters.`);
    return result;
  }
  function sourceReferences(value) {
    if (!Array.isArray(value) || value.length > 10) throw new Error("Select at most ten checked Workspace artifacts.");
    const refs=value.map(ref=>{
      if(!ref||Object.keys(ref).sort().join(",")!=="bytes,path,sha256"||!SHA.test(ref.sha256||"")||!Number.isInteger(ref.bytes)||ref.bytes<0||ref.bytes>10*1024*1024||typeof ref.path!=="string"||ref.path.length>300)throw new Error("Artifact references require exact path, SHA-256 and a byte size up to 10 MiB.");
      return {path:artifactPath(ref.path),sha256:ref.sha256,bytes:ref.bytes};
    });
    if(new Set(refs.map(ref=>ref.path)).size!==refs.length)throw new Error("Artifact references must use different Workspace paths.");
    return refs;
  }
  const includesRequiredSources=(sources,required)=>sourceReferences(required).every(ref=>sources.some(value=>value.path===ref.path&&value.sha256===ref.sha256&&value.bytes===ref.bytes));
  const initialReviewSources=task=>JSON.stringify(sourceReferences(task?.required_source_refs||[]),null,2);
  function authoringProject(value) {
    if (!value || !ID.test(value.id || "") || !Array.isArray(value.tasks) || !value.tasks.length || value.tasks.length > 24) throw new Error("Choose a project identifier and 1–24 task definitions.");
    const acceptance = bounded(value.acceptance,"Project acceptance",1000);
    const seen = new Set();
    const tasks = value.tasks.map(task => {
      if (!task || !ID.test(task.id || "") || seen.has(task.id) || !["manual", "procedure", "workflow"].includes(task.kind)) throw new Error("Tasks need unique identifiers and a supported task kind.");
      seen.add(task.id);
      if (!Array.isArray(task.depends_on) || task.depends_on.length > 6 || task.depends_on.some(x => !ID.test(x) || x === task.id) || new Set(task.depends_on).size !== task.depends_on.length) throw new Error("Use at most six different task identifiers as direct dependencies.");
      const result = {id:task.id, title:bounded(task.title,"Task title",200), kind:task.kind, depends_on:[...task.depends_on], acceptance:bounded(task.acceptance,"Task acceptance",1000)};
      if (task.kind === "procedure") result.procedure_id = bounded(task.procedure_id,"Procedure identifier",64);
      if (task.kind === "workflow") result.workflow_id = bounded(task.workflow_id,"Workflow identifier",64);
      if (task.required_source_refs !== undefined) {
        if(task.kind!=="manual")throw new Error("Required source references belong to an owner-decision step.");
        result.required_source_refs=sourceReferences(task.required_source_refs);
      }
      return result;
    });
    if (tasks.some(task => task.depends_on.some(dependency => !seen.has(dependency)))) throw new Error("Every dependency must refer to a task in this project.");
    const visiting = new Set(), visited = new Set(), byId = new Map(tasks.map(task => [task.id, task]));
    function visit(key) {
      if (visiting.has(key)) throw new Error("Task dependencies must not contain a cycle.");
      if (visited.has(key)) return;
      visiting.add(key); for (const dependency of byId.get(key).depends_on) visit(dependency); visiting.delete(key); visited.add(key);
    }
    for (const task of tasks) visit(task.id);
    return {id:value.id, name:bounded(value.name,"Project name",200), objective:bounded(value.objective,"Objective",4000), acceptance, tasks};
  }
  function cyclePayload(row, maxChildren, revisionValue, requestId, maxSteps = 6) {
    if (!row || !ID.test(row.id || "") || !SHA.test(row.content_sha256 || "") || !Number.isInteger(maxChildren) || maxChildren < 1 || maxChildren > 3 || !Number.isInteger(maxSteps) || maxSteps < 1 || maxSteps > 8 || !Number.isInteger(revisionValue) || revisionValue < 0 || !ID.test(requestId || "")) throw new Error("Refresh and review the exact project with 1–3 children and 1–8 AI steps per child.");
    return {request_id:requestId, expected_revision:revisionValue, project_id:row.id, content_sha256:row.content_sha256, max_children:maxChildren, max_steps_per_child:maxSteps};
  }
  function reviewPayload(row, task, decision, note, revisionValue, requestId, sourceRefs = []) {
    if (!row || !ID.test(row.id || "") || !task || task.kind !== "manual" || !["ready","awaiting_review"].includes(task.status) || !ID.test(task.id || "") || !SHA.test(task.content_sha256 || "") || !["accept", "reject"].includes(decision) || !Number.isInteger(revisionValue) || revisionValue < 0 || !ID.test(requestId || "")) throw new Error("Refresh the exact ready owner-decision evidence before recording a review.");
    const sources=sourceReferences(sourceRefs);
    if(task.required_source_refs?.length&&!includesRequiredSources(sources,task.required_source_refs))throw new Error("This source gate requires its saved source references for either decision. Inspect them before reviewing; the server also verifies their current bytes.");
    return {request_id:requestId, expected_revision:revisionValue, project_id:row.id, task_id:task.id, content_sha256:task.content_sha256, decision, note:bounded(note,"Review note",2000),source_refs:sources};
  }
  function statusSummary(row) {
    const tasks = Array.isArray(row?.tasks) ? row.tasks : [];
    return {total:tasks.length, completed:tasks.filter(x => x.status === "completed").length, review:tasks.filter(x => ["needs_review", "awaiting_review", "waiting_review"].includes(x.status)).length, blocked:tasks.filter(x => x.status === "blocked").length};
  }
  function button(label, action, enabled = true, primary = false) {
    const control = app.button(label, primary ? "button-primary" : "button-secondary", action);
    control.disabled = !enabled;
    return control;
  }
  function badge(value) {
    const status = node("span", "wr-status", pretty(value) || "Not recorded"); status.setAttribute("data-state", text(value)); return status;
  }
  function list(parent, values) {
    const container = node("ul", "wr-list");
    for (const value of typeof values === "string" ? [values] : values || []) container.append(node("li", "", typeof value === "string" ? value : JSON.stringify(value)));
    parent.append(container);
  }
  function disclosure(parent, title, value) {
    const detail = node("details", "wr-receipt"); detail.append(node("summary", "", title), node("pre", "", JSON.stringify(value, null, 2))); parent.append(detail);
  }
  function field(form, label, name, value, {area = false, type = "text", wide = false, maximum = 4000} = {}) {
    const wrapper = node("label", `wr-field${wide ? " wr-field-wide" : ""}`, label), input = node(area ? "textarea" : "input");
    input.name = name; if (!area) input.type = type; input.value = text(value); input.maxLength = maximum; input.disabled = !writable();
    wrapper.append(input); form.append(wrapper); return input;
  }
  function receiptPanel(parent) {
    if (!uncertainty) return;
    const hold = node("section", "wr-section"); hold.append(node("h3", "", "Inspect the unconfirmed request"));
    paragraph(hold, `Request ${uncertainty.request_id} may have been accepted. Inspect its saved receipt and project state before starting another action. This tab will not repeat it.`, "wr-notice");
    hold.append(button("Inspect request receipt", () => inspectReceipt(), !busy && app.state.authenticated));
    if (uncertainty.receipt) disclosure(hold, "Saved receipt for this exact request", uncertainty.receipt);
    const label = node("label", "wr-check"), checkbox = node("input"); checkbox.type = "checkbox"; checkbox.name="receipt_inspected";checkbox.checked = inspection; checkbox.disabled = !uncertainty.inspected || busy;
    checkbox.addEventListener("change", () => {inspection = checkbox.checked; render();});
    label.append(checkbox, node("span", "", "I inspected the request receipt and the current project / child state.")); hold.append(label);
    hold.append(button("Acknowledge this tab's uncertainty", () => { if (!inspection || !uncertainty?.inspected) return; uncertainty = null; inspection = false; warning = "Only this tab's uncertainty was acknowledged. Saved server holds still apply."; render(); }, inspection && !busy));
    parent.append(hold);
  }
  async function inspectReceipt() {
    if (!uncertainty || busy || !app.state.authenticated) return;
    const mark = epoch, token = app.state.token, requestId = uncertainty.request_id;
    try {
      const value = await app.api(BASE + "/receipts/" + encodeURIComponent(requestId));
      if (!current(mark, token) || uncertainty?.request_id !== requestId) return;
      uncertainty.receipt = value; uncertainty.inspected = true; render();
    } catch (error) { if (current(mark, token)) { warning = `Receipt inspection did not complete: ${error.message}. The hold remains.`; render(); } }
  }
  async function mutate(path, body, successId = selected) {
    if (!writable()) return;
    const mark = epoch, token = app.state.token;
    let rejected=false;
    busy = true; cycleConfirmed = false; warning = ""; uncertainty = {request_id:body.request_id, inspected:false}; render();
    try {
      const result = await app.api(path, {method:"POST", body});
      if (!current(mark, token)) return;
      uncertainty = null; inspection = false;
      if (Number.isInteger(result.revision)) board.revision = result.revision;
      selected = result.project_id || result.project?.id || successId;
      editor = null; cycleReview = false; reviewNote = ""; reviewSources = "[]";
      app.toast?.("Saved locally. Existing execution and approval controls still apply.");
    } catch (error) {
      if (current(mark, token)) {
        if([400,409].includes(error.status)){rejected=true;uncertainty=null;inspection=false;readFailed=true;warning=`Request rejected: ${error.message}. Refresh projects before preparing another action. No retry was made; saved server holds remain in force.`;}
        else warning = `${error.message} Inspect request ${body.request_id}; no retry was made.`;
      }
    } finally {
      busy = false;
      if (current(mark, token)) { if (!uncertainty&&!rejected) await load(); else render(); }
      else if (app.state.authenticated) render();
    }
  }
  async function selectProject(key) {
    if (!ID.test(key || "") || !app?.state.authenticated || busy) return;
    selected = key; selectedTask = null; editor = null; cycleReview = false; cycleConfirmed = false; reviewNote = ""; reviewSources = "[]";
    const mark = ++epoch, token = app.state.token; reading = true; project = null; warning = ""; render();
    try {
      const value = await app.api(BASE + "/projects/" + encodeURIComponent(key));
      if (!current(mark, token)) return;
      if (!value?.project || value.project.id !== key || !Array.isArray(value.project.tasks)) throw new Error("The saved project response is inconsistent.");
      project = value.project; board.revision = value.revision;
      readFailed = false;
      selectedTask = project.tasks[0]?.id || null;
      reviewSources=initialReviewSources(project.tasks[0]);
    } catch (error) { if (current(mark, token)) {warning = error.message;readFailed=true;} }
    finally { if (current(mark, token)) {reading = false; render();} }
  }
  function openEditor(definition = null) {
    if (!writable()) return;
    const value = definition?.project || definition?.definition || definition;
    editor = value ? {id:value.id, name:value.name, objective:value.objective, acceptance:text(value.acceptance), tasks:clone(value.tasks || [])} : {id:id("project-"), name:"", objective:"", acceptance:"", tasks:[]};
    if (definition && definition !== project) editor.id = id("project-");
    editor.tasks_text = JSON.stringify(editor.tasks,null,2); editor.base_sha = definition === project ? project?.content_sha256 : null; editor.stale = false;
    cycleReview = false; cycleConfirmed = false; render();
  }
  function editorPanel(parent) {
    if (!editor) return;
    const form = node("form", "wr-form"); form.append(node("h3", "", "Review the project plan"));
    paragraph(form, "Saving a plan queues no work. Review its tasks, dependencies and acceptance conditions before starting a separate bounded cycle.");
    if(editor.stale)paragraph(form,"This saved project changed after you began editing. Inspect the current plan, then discard these edits before preparing a new revision.","wr-notice");
    const grid = node("div", "wr-form-grid");
    const projectId = field(grid,"Project identifier","project_id",editor.id,{maximum:64}), name = field(grid,"Project name","name",editor.name,{maximum:200});
    const objective = field(grid,"Objective","objective",editor.objective,{area:true,wide:true,maximum:4000});
    const acceptance = field(grid,"Project acceptance condition","acceptance",editor.acceptance,{area:true,wide:true,maximum:1000}); form.append(grid);
    const detail = node("details", "wr-receipt"); detail.append(node("summary", "", "Task definitions and dependencies"));
    const tasks = field(detail,"Task definitions (JSON)","tasks",editor.tasks_text,{area:true,wide:true,maximum:50000});
    paragraph(detail, "Each task has id, title, kind, depends_on and an acceptance string. Use 1–24 tasks and at most six direct dependencies per task. Procedure tasks select procedure_id; workflow tasks select an existing workflow_id. Saved workflow permissions and checks still govern their children.");
    const keep = () => {editor = {...editor,id:projectId.value,name:name.value,objective:objective.value,acceptance:acceptance.value,tasks_text:tasks.value}; cycleConfirmed = false;};
    for (const input of [projectId,name,objective,acceptance,tasks]) input.addEventListener("input", keep);
    tasks.addEventListener("change",()=>{keep();render();});
    taskEditor(form,tasks);form.append(detail);
    form.addEventListener("submit", event => {
      event.preventDefault(); if (!writable() || editor.stale) return;
      try { const value = authoringProject({id:projectId.value,name:name.value,objective:objective.value,acceptance:acceptance.value,tasks:JSON.parse(tasks.value)}); if(!editor.base_sha && board.projects.some(row=>row.id===value.id))throw new Error("That project identifier already exists. Open it and choose Edit project plan.");return mutate(ROUTES.save,{...meta("project-save-"),project:value},value.id); }
      catch (error) { warning = error.message; render(); }
    });
    const actions = node("div", "wr-actions"); const save = button("Save project plan",()=>{},writable()&&!editor.stale,true); save.type = "submit";
    actions.append(save,button("Discard plan edits",()=>{editor=null;warning="";render();},!busy)); form.append(actions); parent.append(form);
  }
  function taskEditor(form, raw) {
    const region=node("section","wr-task-editor");region.append(node("h4","","Build the dependency path"));
    paragraph(region,"Define observable work and the evidence that would finish it. Manual steps wait for your decision; automated steps use an existing fixed procedure or saved workflow.");
    let definitions;try{definitions=JSON.parse(editor.tasks_text);if(!Array.isArray(definitions))throw Error();}catch{paragraph(region,"The advanced task definitions contain invalid JSON. Correct that text to restore the visual step editor.","wr-notice");form.append(region);return;}
    const sync=()=>{editor.tasks_text=JSON.stringify(definitions,null,2);raw.value=editor.tasks_text;cycleConfirmed=false;};
    const choose=(parent,label,name,choices,currentValue,onChange)=>{
      const wrapper=node("label","wr-field",label),select=node("select");select.name=name;select.disabled=!writable();
      const values=[...choices];if(currentValue&&!values.some(row=>row.id===currentValue))values.push({id:currentValue,name:`Unavailable saved selection · ${currentValue}`});
      for(const row of values){const option=node("option","",row.name||row.id);option.value=row.id;option.selected=row.id===currentValue;select.append(option);}select.value=currentValue||values[0]?.id||"";
      select.addEventListener("change",()=>onChange(select.value));wrapper.append(select);parent.append(wrapper);return select;
    };
    definitions.forEach((task,index)=>{
      if(!task||typeof task!=="object"){paragraph(region,`Step ${index+1} has an invalid definition; correct it in the advanced editor.`,"wr-notice");return;}
      const card=node("section","wr-task-card"),title=node("div","wr-title-row");title.append(node("h4","",`Step ${index+1}`),button(`Remove step ${index+1}`,()=>{definitions.splice(index,1);sync();render();},writable()));card.append(title);
      const grid=node("div","wr-form-grid");
      for(const [label,key,maximum]of [["Step identifier","id",64],["Step title","title",200]]){const control=field(grid,label,`task_${key}_${index}`,task[key],{maximum});control.addEventListener("input",()=>{task[key]=control.value;sync();});}
      choose(grid,"How this step runs",`task_kind_${index}`,[{id:"manual",name:"Owner decision"},{id:"procedure",name:"Fixed local procedure"},{id:"workflow",name:"Saved workflow"}],task.kind,value=>{task.kind=value;delete task.procedure_id;delete task.workflow_id;if(value==="workflow")task.workflow_id=(catalog?.workflows||[]).find(row=>row.eligible)?.id||"";if(value==="procedure")task.procedure_id=(catalog?.procedures||[])[0]?.id||"";sync();render();});
      const dependencies=field(grid,"After steps · comma-separated identifiers",`task_dependencies_${index}`,Array.isArray(task.depends_on)?task.depends_on.join(", "):"",{maximum:400});dependencies.addEventListener("input",()=>{task.depends_on=dependencies.value.split(",").map(clean).filter(Boolean);sync();});
      const acceptance=field(grid,"Evidence / acceptance condition",`task_acceptance_${index}`,task.acceptance,{area:true,wide:true,maximum:1000});acceptance.addEventListener("input",()=>{task.acceptance=acceptance.value;sync();});
      if(task.kind==="workflow"){const rows=(catalog?.workflows||[]).filter(row=>row.eligible);choose(grid,"Existing reviewed workflow",`task_workflow_${index}`,rows,task.workflow_id,value=>{task.workflow_id=value;sync();});if(!rows.length)paragraph(card,"No eligible isolated-output workflow is available. Create and review one in Workflows before selecting it here.","wr-notice");}
      if(task.kind==="procedure")choose(grid,"Fixed offline procedure",`task_procedure_${index}`,catalog?.procedures||[],task.procedure_id,value=>{task.procedure_id=value;sync();});
      card.append(grid);region.append(card);
    });
    region.append(button("Add owner decision step",()=>{let index=definitions.length+1;while(definitions.some(row=>row?.id===`step-${index}`))index++;definitions.push({id:`step-${index}`,title:"",kind:"manual",depends_on:[],acceptance:""});sync();render();},writable()&&definitions.length<24));form.append(region);
  }
  function templatesPanel(parent) {
    const templates = catalog?.templates || [];
    if (!templates.length) return;
    const region = node("div", "wr-templates");
    for (const template of templates) {
      const control = button("",()=>openEditor(template),writable()); control.className = "wr-template";
      control.append(node("span","wr-kicker",template.category || "REVIEWED STARTING POINT"),node("strong","",template.name || template.project?.name || template.id),node("small","",template.description || "Review and adapt this local project before starting work.")); region.append(control);
    }
    parent.append(region);
  }
  function executionReady(row) {
    if(!row || row.source_drift) return false;
    return (row.tasks || []).every(task => task.kind !== "workflow" || (
      task.execution_preview?.workflow && SHA.test(task.execution_preview.workflow_sha256 || "")
      && Number.isInteger(task.execution_preview.resolved_agent?.max_steps)
      && task.execution_preview.resolved_agent.max_steps >= 1
    ));
  }
  function executionPreview(parent, task) {
    const preview=task.execution_preview;
    if(task.kind === "workflow") {
      const region=node("section","wr-section");region.append(node("h4","",task.title || task.id));
      if(!preview?.workflow){paragraph(region,"The captured workflow preview is unavailable. Refresh and inspect the saved plan before starting.","wr-notice");parent.append(region);return;}
      const workflow=preview.workflow,agent=preview.resolved_agent || {};
      paragraph(region,`${workflow.name || workflow.id} · ${agent.name || workflow.agent_id || "Agent not recorded"} · at most ${Number.isInteger(agent.max_steps)?agent.max_steps:"unknown"} mission steps`,"wr-stamp");
      paragraph(region,`Workflow fingerprint: ${preview.workflow_sha256 || "Unavailable"}`,"wr-hash");
      disclosure(region,"Exact frozen instructions and output checks",{prompt:workflow.prompt,checks:workflow.checks,agent_id:workflow.agent_id,isolated_outputs:workflow.isolated_outputs});
      disclosure(region,"Resolved specialist and permitted tool scope",agent);parent.append(region);
    } else if(task.kind === "procedure" && preview) {
      const region=node("section","wr-section");region.append(node("h4","",task.title || task.id));
      paragraph(region,`${preview.name || task.procedure_id} · fixed local procedure · no model calls`,"wr-stamp");list(region,preview.outputs || []);parent.append(region);
    }
  }
  function cyclePanel(parent) {
    if (!cycleReview || !project) return;
    const region = node("section", "wr-section"); region.append(node("h3","","Review the bounded cycle"));
    paragraph(region,"The saved project and captured workflow definitions are bound to this review. Existing tool approvals remain separate. A failure, interruption, unverified outcome or human decision stops further advancement.");
    paragraph(region,"The child budget counts runner invocations, not model calls. Each AI child uses the lower of this cycle's AI-step cap and its resolved specialist limit; fixed local procedures use no model steps.");
    paragraph(region,`Project fingerprint: ${project.content_sha256 || "Unavailable"}`,"wr-hash");
    const label = node("label","wr-field","Maximum children in this cycle"), budget = node("select"); budget.name="max_children";
    for (let value=1;value<=3;value++){const option=node("option","",String(value));option.value=String(value);option.selected=value===childBudget;budget.append(option);} budget.value=String(childBudget); budget.disabled=!writable();
    budget.addEventListener("change",()=>{childBudget=Number(budget.value);cycleConfirmed=false;render();});label.append(budget);region.append(label);
    const stepLabel=node("label","wr-field","Maximum AI steps per child"),steps=node("input");steps.name="max_steps_per_child";steps.type="number";steps.min="1";steps.max="8";steps.step="1";steps.value=String(stepBudget);steps.disabled=!writable();
    steps.addEventListener("input",()=>{stepBudget=Number(steps.value);cycleConfirmed=false;render();});stepLabel.append(steps);region.append(stepLabel);
    const validSteps=Number.isInteger(stepBudget)&&stepBudget>=1&&stepBudget<=8;
    paragraph(region,validSteps?`Upper bound: ${childBudget} runner invocations × ${stepBudget} AI steps = ${childBudget*stepBudget} model steps. Actual use may be lower; this is not calls spent or a dollar-cost estimate.`:"Choose an AI-step cap from 1 to 8.","wr-stamp");
    disclosure(region,"Inspect the exact saved task definitions",project.tasks.map(({id,title,kind,depends_on,acceptance,procedure_id,workflow_id})=>({id,title,kind,depends_on,acceptance,...(procedure_id?{procedure_id}:{}),...(workflow_id?{workflow_id}:{})})));
    for(const task of project.tasks)executionPreview(region,task);
    if(!executionReady(project))paragraph(region,"The captured workflow or specialist changed or is unavailable. Inspect and explicitly revise the project; this cycle cannot start.","wr-notice");
    const confirm = node("label","wr-check"), check = node("input");check.type="checkbox";check.name="cycle_confirmed";check.checked=cycleConfirmed;check.disabled=!writable();check.addEventListener("change",()=>{cycleConfirmed=check.checked;render();});
    confirm.append(check,node("span","",`I reviewed this exact project and authorize at most ${childBudget} runner invocations with ${stepBudget} AI steps per child through the existing runner and approval controls.`));region.append(confirm);
    region.append(button("Start reviewed cycle",()=>{if(!cycleConfirmed||!writable()||!executionReady(project)||!validSteps)return;try{return mutate(ROUTES.start,cyclePayload(project,childBudget,revision(),id("cycle-start-"),stepBudget));}catch(error){warning=error.message;render();}},writable()&&cycleConfirmed&&executionReady(project)&&validSteps,true));parent.append(region);
  }
  function evidencePanel(parent, task) {
    const detail=node("section","wr-decision");detail.append(node("span","wr-kicker","SELECTED STEP / EVIDENCE & DECISION"),node("h3","",task.title || task.id),badge(task.status));
    paragraph(detail,task.kind === "manual" ? "This step records an owner decision. Accepting it is an owner attestation, not independent verification of the real-world result." : "Inspect the actual child receipt and its output checks. A completed model response alone does not establish acceptance.");
    list(detail,task.acceptance || []);
    if(task.depends_on?.length)paragraph(detail,`Depends on: ${task.depends_on.join(", ")}`);
    if(task.procedure_id)paragraph(detail,`Fixed procedure: ${task.procedure_id}`,"wr-stamp");
    if(task.workflow_id)paragraph(detail,`Saved workflow: ${task.workflow_id}`,"wr-stamp");
    if(task.required_source_refs?.length){
      const required=node("section","wr-section");required.append(node("h4","","Required source files"));
      for(const ref of sourceReferences(task.required_source_refs)){required.append(button(ref.path,()=>app.openArtifact?.(ref.path),!busy));paragraph(required,`${ref.bytes} bytes · SHA-256 ${ref.sha256}`,"wr-hash");}
      paragraph(required,"These are retained source identities. Opening them is not a new integrity check; the server checks their current bytes when a decision is recorded.");detail.append(required);
    }
    executionPreview(detail,task);
    const child=task.child;
    if(child){const section=node("div","wr-section");section.append(node("h4","","Actual child activity"));paragraph(section,`${child.kind || "Child"} ${child.id || child.run_id || ""} · ${pretty(child.status) || "State unavailable"}`,"wr-stamp");const childId=child.run_id || child.id;if(child.kind!=="routine"&&ID.test(childId||""))section.append(button("Inspect mission and approvals",()=>app.selectRun?.(childId),!busy));disclosure(section,"Child receipt",child);detail.append(section);}
    const evidence=task.evidence || {}, artifacts=Array.isArray(evidence)?evidence:evidence.artifacts || [];
    if(artifacts.length){const section=node("div","wr-section");section.append(node("h4","","Recorded output artifacts"));for(const artifact of artifacts){if(!artifact || !SHA.test(artifact.sha256||""))continue;try{const path=artifactPath(artifact.path),row=node("div","wr-artifact");row.append(button(path,()=>app.openArtifact?.(path),!busy));paragraph(row,`SHA-256 ${artifact.sha256}`,"wr-hash");section.append(row);}catch{paragraph(section,"An artifact path was refused; inspect the server receipt.","wr-notice");}}detail.append(section);}
    if(evidence.assurance)paragraph(detail,evidence.assurance === "owner_attested" ? "Completion basis: owner attestation. This does not independently verify the real-world outcome." : evidence.assurance === "checked_artifacts" ? "Completion basis: checked child artifacts. Inspect their coverage and receipt." : `Completion basis: ${pretty(evidence.assurance)}`,"wr-stamp");
    if(task.review)disclosure(detail,"Recorded owner review",task.review);
    if(task.evidence)disclosure(detail,"Evidence snapshot and coverage",task.evidence);
    if(SHA.test(task.content_sha256||""))paragraph(detail,`Review fingerprint: ${task.content_sha256}`,"wr-hash");
    if(task.kind === "manual" && ["ready","awaiting_review"].includes(task.status)){
      const section=node("div","wr-section");section.append(node("h4","","Record an explicit decision"));
      const note=field(section,"Review note","review_note",reviewNote,{area:true,maximum:2000});note.addEventListener("input",()=>{reviewNote=note.value;});
      const sources=field(section,"Checked Workspace source references (JSON array)","source_refs",reviewSources,{area:true,maximum:20000});sources.addEventListener("input",()=>{reviewSources=sources.value;});
      paragraph(section,task.required_source_refs?.length?"This gate requires the saved source references shown above. Inspect their files before accepting. Hashes establish file identity, not factual truth, customer permission or commercial approval. An accepted step remains owner-attested.":"Use [] for a decision without artifact references. Each supplied artifact needs path, sha256 and bytes; the server checks its current bytes. An accepted manual step remains owner-attested.");
      const actions=node("div","wr-actions");for(const decision of ["accept","reject"])actions.append(button(decision==="accept"?"Accept reviewed step":"Reject with note",()=>{try{return mutate(ROUTES.review,reviewPayload(project,task,decision,reviewNote,revision(),id("task-review-"),JSON.parse(reviewSources)));}catch(error){warning=error.message;render();}},writable()&&SHA.test(task.content_sha256||""),decision==="accept"));section.append(actions);detail.append(section);
    }
    parent.append(detail);
  }
  function projectPanel(parent) {
    if (!project) {const empty=node("section","wr-empty");empty.append(node("span","wr-kicker","INTENT → EVIDENCE → REVIEW"),node("h2","",reading?"Reading the saved project…":"Give the work a durable home."));paragraph(empty,"Turn a goal into named steps, dependencies and acceptance conditions. Review a plan, then authorize a small cycle. Ariadne uses its existing runners, receipts and approvals to advance it.");templatesPanel(empty);parent.append(empty);return;}
    const objective=node("section","wr-objective"),cycle=activeCycle(),summary=statusSummary(project);
    objective.append(node("span","wr-kicker",`PROJECT ${project.id} · DEFINITION ${project.definition_revision || "—"}`),node("h2","",project.name),node("p","",project.objective));
    const title=node("div","wr-title-row");title.append(badge(cycle?.status || "plan"),node("span","wr-stamp",cycle?`Cycle ${cycle.id || cycle.cycle_id || ""} · ${stamp(cycle.created_at)}`:"No cycle has been authorized."));objective.append(title);
    const facts=node("div","wr-facts");for(const [value,label]of [[`${summary.completed} / ${summary.total}`,"Recorded complete"],[summary.review,"Awaiting review"],[summary.blocked,"Blocked dependencies"],[cycle?.max_children ?? "—","Cycle child budget"]]){const fact=node("div","wr-fact");fact.append(node("strong","",String(value)),node("small","",label));facts.append(fact);}objective.append(facts);
    const progress=node("progress","wr-progress");progress.max=Math.max(1,summary.total);progress.value=summary.completed;progress.setAttribute("aria-label",`${summary.completed} of ${summary.total} steps recorded complete`);objective.append(progress);
    paragraph(objective,"Manual completion is an owner attestation. Checked artifacts and child acceptance are recorded separately; these counts do not prove revenue, deployment or a real-world result.");
    if(cycle?.reason)paragraph(objective,cycle.reason,"wr-notice");
    if(cycle){paragraph(objective,`Recorded cycle: ${cycle.children_dispatched ?? 0} child invocations dispatched of ${cycle.max_children}. AI-step cap ${cycle.max_steps_per_child ?? "not recorded"}; upper bound ${cycle.worst_case_model_steps ?? "not recorded"} model steps.`,"wr-stamp");paragraph(objective,"Stopping prevents further dispatch. An already linked queued or running child may still finish through the normal runner; Stop does not cancel or reclassify it.");}
    if(project.source_drift)paragraph(objective,"A captured workflow or specialist differs from its current definition. Historical evidence remains available; starting new work requires an explicit reviewed revision.","wr-notice");
    const actions=node("div","wr-actions");actions.append(button("Review cycle",()=>{cycleReview=!cycleReview;cycleConfirmed=false;render();},writable()&&!cycleActive(cycle)&&cycle?.status!=="held",true),button("Inspect / advance cycle",()=>{if(!project?.cycle)return;try{mutate(ROUTES.reconcile,{...meta("cycle-check-"),project_id:project.id,cycle_id:project.cycle.id || project.cycle.cycle_id});}catch(error){warning=error.message;render();}},writable()&&!!cycle),button("Stop after this step",()=>{if(!project?.cycle)return;try{mutate(ROUTES.stop,{...meta("cycle-stop-"),project_id:project.id,cycle_id:project.cycle.id || project.cycle.cycle_id});}catch(error){warning=error.message;render();}},writable()&&cycleActive(cycle)),button("Edit project plan",()=>openEditor(project),writable()&&!cycleActive(cycle)));objective.append(actions);
    const accept=node("details","wr-receipt");accept.append(node("summary","","Project acceptance conditions"));list(accept,project.acceptance || []);objective.append(accept);parent.append(objective);cyclePanel(parent);
    const columns=node("div","wr-columns"),path=node("section");path.append(node("span","wr-kicker","DEPENDENCY PATH"),node("h3","","The next useful step"));const steps=node("ol","wr-path");
    for(const task of project.tasks){const item=node("li","wr-step");item.setAttribute("data-state",task.status || "unknown");const control=button("",()=>{selectedTask=task.id;reviewNote="";reviewSources=initialReviewSources(task);render();},!busy);control.className="wr-step-button";control.setAttribute("aria-pressed",String(selectedTask===task.id));control.setAttribute("aria-label",`Inspect step ${task.title}`);const title=node("div","wr-title-row");title.append(node("strong","",task.title),badge(task.status));control.append(title);paragraph(control,task.depends_on?.length?`After ${task.depends_on.join(" · ")}`:"No task dependency");item.append(control);steps.append(item);}path.append(steps);columns.append(path);
    const task=project.tasks.find(row=>row.id===selectedTask)||project.tasks[0];if(task)evidencePanel(columns,task);parent.append(columns);
    if(project.cycles?.length)disclosure(parent,"Cycle history",project.cycles);
  }
  function render() {
    const mount=root();if(!mount||!app)return;renderedControls=controlState();mount.replaceChildren();
    if(!app.state.authenticated){mount.append(node("p","wr-notice","Pair this tab to inspect your private project work."));return;}
    const shell=node("div","wr-shell"),toolbar=node("div","wr-toolbar"),intro=node("div");intro.append(node("span","wr-kicker","PROJECT OPERATIONS / LOCAL FIRST"),node("p","",busy?"Recording one explicit request…":reading?"Reading saved project state…":"Intent is a plan. Progress needs evidence."));toolbar.append(intro);const actions=node("div","wr-actions");actions.append(button("Refresh projects",()=>load(),!busy&&!reading),button("Create project",()=>openEditor(),writable(),true));toolbar.append(actions);shell.append(toolbar);
    if(warning)paragraph(shell,warning,"wr-notice");if(paused())paragraph(shell,"Global autonomy is paused. Project reads remain available; project changes are held.","wr-notice");receiptPanel(shell);
    const layout=node("div","wr-layout"),rail=node("aside","wr-rail");rail.setAttribute("aria-label","Saved projects");const heading=node("div","wr-rail-heading");heading.append(node("h3","","Workstreams"),node("span","wr-count",String(board?.projects?.length || 0)));rail.append(heading);const list=node("div","wr-project-list");
    for(const row of board?.projects || []){const control=button("",()=>selectProject(row.id),!busy);control.className="wr-project-button";control.setAttribute("aria-pressed",String(selected===row.id));control.setAttribute("aria-label",`Open project ${row.name}`);control.append(node("span","wr-kicker",row.cycle?.status?pretty(row.cycle.status):"SAVED PLAN"),node("strong","",row.name),node("small","",row.id));list.append(control);}if(!(board?.projects||[]).length)paragraph(list,"No project has been saved. Choose a starting point and review its plan.");rail.append(list);layout.append(rail);const main=node("div","wr-main");projectPanel(main);editorPanel(main);layout.append(main);shell.append(layout);mount.append(shell);
  }
  async function load() {
    if(!app?.state.authenticated||busy)return;
    const mark=++epoch,token=app.state.token;reading=true;cycleConfirmed=false;render();
    try{
      const [next,catalogue]=await Promise.all([app.api(ROUTES.status),app.api(ROUTES.catalog)]);
      if(!current(mark,token))return;
      if(next?.schema!==1||!Array.isArray(next.projects)||!Number.isInteger(next.revision))throw new Error("This server does not provide the Workroom contract. Use its current supported views.");
      board=next;catalog=catalogue;readFailed=false;
      if(selected&&board.projects.some(row=>row.id===selected)){
        const detail=await app.api(BASE+"/projects/"+encodeURIComponent(selected));if(!current(mark,token))return;project=detail.project;board.revision=detail.revision;selectedTask=project?.tasks?.some(row=>row.id===selectedTask)?selectedTask:project?.tasks?.[0]?.id||null;if(editor?.base_sha&&project.content_sha256!==editor.base_sha)editor.stale=true;
      }else{selected=null;selectedTask=null;project=null;}
    }catch(error){if(current(mark,token)){warning=error.message;readFailed=true;}}
    finally{if(current(mark,token)){reading=false;render();}}
  }
  function leave(){epoch++;board=null;catalog=null;project=null;selected=null;selectedTask=null;editor=null;cycleReview=false;cycleConfirmed=false;reviewNote="";reviewSources="[]";warning="";reading=false;readFailed=false;inspection=false;if(uncertainty)uncertainty={request_id:uncertainty.request_id,inspected:false};renderedControls=controlState();if(root())root().replaceChildren();}
  function locked(){leave();if(uncertainty){uncertainty={request_id:uncertainty.request_id,inspected:false};inspection=false;}render();}
  function attach(value){app=value;render();}
  async function openProject(identifier){
    if(!ID.test(identifier||"")||!app?.state.authenticated||busy)return;
    const token=app.state.token,navigation=app.state.navigationGeneration,view=app.state.view;
    const sameNavigation=()=>app?.state.navigationGeneration===navigation&&app?.state.view===view&&(view==null||view==="workroom");
    if(!sameNavigation())return;
    if(!board){const pending=load(),mark=epoch;await pending;if(!current(mark,token)||!sameNavigation())return;}
    if(board&&!readFailed&&app?.state.authenticated&&app.state.token===token&&!busy&&sameNavigation())await selectProject(identifier);
  }
  function status(){
    if(!app)return;
    const next=controlState();
    if(renderedControls&&next.authenticated===renderedControls.authenticated&&next.token===renderedControls.token&&next.paused===renderedControls.paused)return;
    if(renderedControls&&(next.authenticated!==renderedControls.authenticated||next.token!==renderedControls.token))leave();
    cycleConfirmed=false;render();
  }
  window.ariadneWorkroom={attach,load,leave,locked,status,openProject,authoringProject,cyclePayload,reviewPayload,statusSummary,artifactPath,executionReady,sourceReferences};
})();
