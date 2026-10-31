"use strict";

// Fixed research and delayed daily simulation. Reads never start market work.
(() => {
  let app, attached, data = null, generation = 0, busy = false, loading = false;
  let stale = false, notice = "", uncertain = null, inspected = false, actions = [], inputs = [];
  let archiveDraft = {reason:"",archive_data:false};
  let lastGlobalPause = null;
  const $ = id => document.getElementById(id);
  const text = value => typeof value === "string" ? value : "";
  const current = (marker, token) => marker === generation && app?.state.authenticated && app.state.token === token;
  const idPattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
  const newId = () => "qqq-ui-" + crypto.randomUUID().replaceAll("-", "");
  const e = (tag, cls = "", value) => app.node(tag, cls, value);
  const p = (parent, value, cls = "") => parent.append(e("p", cls, value));
  const pretty = value => text(value).replaceAll("_", " ") || "Not recorded";
  const stamp = value => { const date = new Date(value); return value && Number.isFinite(date.valueOf()) ? date.toLocaleString() : "Not recorded"; };

  function number(value) {
    if (typeof value === "string" && !/^-?\d{1,100}(?:\.\d{1,100})?$/.test(value)) return null;
    if (!["number", "string"].includes(typeof value)) return null;
    const result = Number(value); return Number.isFinite(result) && Math.abs(result) <= 1e15 ? result : null;
  }
  function money(value) { const result=number(value); return result === null ? "Not measured" : result.toLocaleString("en-US",{style:"currency",currency:"USD",minimumFractionDigits:2,maximumFractionDigits:2}); }
  function percent(value) { const result=number(value); return result === null ? "Not measured" : `${(result*100).toFixed(2)}%`; }
  function count(value) { const result=number(value); return result === null ? "Not recorded" : result.toLocaleString("en-US",{maximumFractionDigits:6}); }
  function artifactPath(value) {
    const path=text(value).replaceAll("\\","/");
    if (!path || path.length>1000 || path.startsWith("/") || path.includes(":") || path.split("/").some(part=>!part||part==="."||part==="..")) throw new Error("Choose a report inside Ariadne Workspace.");
    return path;
  }
  function request(kind, revision, identifier, extra = {}) {
    if (!Number.isInteger(revision) || revision < 0) throw new Error("Refresh the QQQ workspace before changing it.");
    if (kind === "config") {
      if (typeof extra.enabled !== "boolean" || typeof extra.paused !== "boolean") throw new Error("Choose explicit workflow controls.");
      return {expected_revision:revision, enabled:extra.enabled, paused:extra.paused};
    }
    if (!idPattern.test(identifier)) throw new Error("Use a unique bounded request identifier.");
    if (kind === "paper") {
      if (typeof extra.replace !== "boolean") throw new Error("Choose whether the earlier simulation is retained and replaced.");
      return {request_id:identifier,expected_revision:revision,replace:extra.replace,acknowledge_simulated:true};
    }
    if (kind === "archive") {
      const reason=text(extra.reason).trim();
      if(!reason||reason.length>1000||typeof extra.archive_data!=="boolean")throw new Error("Record a reason of 1–1,000 characters and an explicit data-basis choice.");
      return {request_id:identifier,expected_revision:revision,reason,archive_data:extra.archive_data};
    }
    if (!["refresh","backtest","acknowledge"].includes(kind)) throw new Error("Unsupported QQQ workflow action.");
    return {request_id:identifier,expected_revision:revision};
  }
  function canWrite(allowHold = false) {
    return Boolean(app?.state.authenticated && data && !busy && !loading && !uncertain && !stale && !app.state.status?.paused && (allowHold || !data.hold));
  }
  function canRun() { return canWrite() && data.config?.enabled === true && data.config?.paused !== true; }
  function internetAvailable() { const latest=app?.state.status?.internet?.config?.enabled; return typeof latest==="boolean"?latest:data?.internet_enabled===true; }
  function controls() {
    const refresh=$("refresh-qqq"); if(refresh)refresh.disabled=busy||loading||!app?.state.authenticated;
    for(const action of actions)action.button.disabled=!canWrite(action.allowHold)||!action.allowed();
    for(const input of inputs)input.disabled=busy||loading||!app?.state.authenticated;
  }
  function button(label, action, allowed = () => true, {allowHold=false, primary=false}={}) {
    const result=app.button(label,primary?"button-primary":"button-secondary",async()=>{
      if(!canWrite(allowHold)||!allowed())return;
      try{await action();}catch(error){notice=error.message;render();}
    });result.type="button";actions.push({button:result,allowed,allowHold});return result;
  }
  function localButton(label, action) { const result=app.button(label,"button-secondary",action); result.type="button"; return result; }
  function notes(parent) {
    if(!notice&&!stale&&!uncertain&&!app.state.status?.paused)return;
    const box=e("section","qqq-notice");box.setAttribute("role","status");
    if(notice)p(box,notice);
    if(app.state.status?.paused)p(box,"Global autonomy is paused. Saved market and simulation records remain readable; changes are held.");
    if(stale)p(box,"The saved revision changed or could not be read completely. Refresh before another action.");
    if(uncertain){p(box,`The ${uncertain.kind} response was interrupted. Request ${uncertain.id || "configuration change"}. Inspect the saved status and receipts before continuing. No action is retried automatically.`);
      const acknowledge=localButton("I inspected the saved QQQ records",()=>{if(!inspected||busy||loading)return;uncertain=null;inspected=false;notice="";render();});acknowledge.disabled=!inspected||busy||loading;box.append(acknowledge);}
    parent.append(box);
  }
  function metric(parent,label,value) { const card=e("div","qqq-metric");card.append(e("span","",label),e("strong","",value));parent.append(card); }
  function artifacts(parent, values) {
    const row=e("div","qqq-actions");
    for(const artifact of (Array.isArray(values)?values:[]).slice(0,8)){
      try{const path=artifactPath(artifact.path);row.append(localButton(path.endsWith(".json")?"Inspect research data":"Read research report",()=>app.openArtifact(path)));}catch{}
    }
    if(row.childNodes.length)parent.append(row);
  }
  function rulePanel(parent) {
    const box=e("details","qqq-card");box.append(e("summary","","Frozen recovery rule and simulation assumptions"));
    const rule=data.rule;
    if(rule&&typeof rule==="object")for(const[key,value]of Object.entries(rule).slice(0,30)){
      const display=typeof value==="object"?JSON.stringify(value):String(value);p(box,`${pretty(key)}: ${display.slice(0,1500)}`,"qqq-small");
    }else p(box,"The fixed rule appears after the service publishes its registered contract.","qqq-muted");
    p(box,`Rule SHA-256: ${text(data.rule_sha256||data.rule_hash||data.hash)||"Not recorded"}`,"qqq-hash");
    p(box,"Long-only QQQ, no leverage or brokerage connection. A completed-close signal can only affect a later session's simulated open. Daily replay is delayed; it is not a live fill or an intraday stop. Historical tests are not forward paper results.","qqq-muted");
    p(box,"Primary transaction cost: 5 basis points per leg; historical stress cases use higher costs. Prices, cash distributions and splits retain their stated source conventions. This experiment may remain in cash and make no trades.","qqq-muted");parent.append(box);
  }
  function historyPanel(parent) {
    const box=e("section","qqq-card");box.append(e("span","eyebrow","HISTORICAL / NOT FORWARD PERFORMANCE"),e("h3","","Backtest evidence"));
    const backtest=data.backtest;
    if(!backtest){p(box,"No completed historical backtest is saved. A disabled or stale source is never presented as a fresh signal.","qqq-muted");parent.append(box);return;}
    const metrics=backtest.metrics||{},benchmark=backtest.benchmark_metrics||{};
    const gate=backtest.research_gate;
    if(gate)p(box,gate==="not_supported_by_this_backtest"?"NOT SUPPORTED BY THIS BACKTEST. The historical recovery rule failed its return comparison. Paper trading remains an experiment, not an endorsed live strategy.":`Research gate: ${pretty(gate)}. Forward evidence is still required.`,"qqq-caution");
    p(box,`Observed range: ${text(metrics.start_date||backtest.start_date)||"not supplied"} to ${text(metrics.end_date||backtest.end_date)||"not supplied"}. Initial sample and later retrospective folds are historical research, not a prospectively registered holdout.`,"qqq-muted");
    const scroll=e("div","qqq-table-scroll"),table=e("table"),head=e("tr");for(const label of ["Historical measure","Recovery rule","QQQ buy-and-hold"])head.append(e("th","",label));table.append(head);
    for(const[label,key,format]of [["CAGR","cagr",percent],["Total return","total_return",percent],["Maximum drawdown","maximum_close_drawdown",percent],["Trades","trade_count",count]]){
      const row=e("tr");row.append(e("th","",label),e("td","",format(metrics[key]??(key==="maximum_close_drawdown"?metrics.maximum_drawdown??metrics.max_drawdown:key==="trade_count"?backtest.trade_count:null))),e("td","",format(benchmark[key]??(key==="maximum_close_drawdown"?benchmark.maximum_drawdown??benchmark.max_drawdown:null))));table.append(row);
    }scroll.append(table);box.append(scroll);
    p(box,"No result proves a profitable live strategy or reliable monthly income. Simulated open prices, transaction costs and cash-dividend assumptions are not actual broker fills, taxes or independent data verification.","qqq-small qqq-caution");artifacts(box,backtest.artifacts);parent.append(box);
  }
  function paperPanel(parent) {
    const box=e("section","qqq-card");box.append(e("span","eyebrow","FORWARD / SIMULATED MONEY ONLY"),e("h3","","Daily paper account"));
    const paper=data.paper,account=paper?.state;
    if(!account)p(box,"No paper account has started. The initial $10,000 is a simulation assumption, not your budget, deposit or real invested capital.","qqq-muted");
    else{
      p(box,`Simulation ${text(paper.id)||"recorded"} · started ${stamp(account.started_at||paper.started_at)} · latest processed session ${text(account.last_processed)||"none"}.`,"qqq-muted");
      const metrics=e("div","qqq-metrics");for(const[label,value]of [["Simulated equity",money(account.equity)],["Simulated cash",money(account.cash)],["QQQ units",count(account.shares)],["Modeled fees",money(account.total_fees)],["Cash distributions",money(account.total_dividends)],["Recorded trades",count(account.trade_count)]])metric(metrics,label,value);box.append(metrics);
      const pending=account.pending;p(box,pending?`Pending simulation instruction: ${JSON.stringify(pending).slice(0,1000)}. A missing or stale next-session bar cannot become an invented fill.`:"No pending simulation instruction. Holding cash is a valid result.","qqq-small");
      const daily=(Array.isArray(data.events)?data.events:[]).find(event=>event.type==="paper_daily_result");
      if(daily){const results=e("div","qqq-daily");results.append(e("h4","",`Latest forward paper result · ${text(daily.session)||"date not recorded"}`));p(results,`Session P/L ${money(daily.day_pnl)} (${percent(daily.day_return)}). Cumulative P/L ${money(daily.cumulative_pnl)} (${percent(daily.cumulative_return)}).`,"qqq-small");box.append(results);}
      else p(box,"No forward daily return has been observed yet. Starting simulated cash is not a trading result.","qqq-small qqq-muted");
    }
    p(box,"The next permitted 6 AM check-in updates completed-session observations and records delayed paper outcomes. Offline or stale data keeps the last saved figures dated and blocks new simulated fills. Real accounts and orders are outside this workflow.","qqq-small");
    box.append(button(paper?"Archive and replace simulated account":"Start $10,000 simulated account",startPaper,()=>canRun()&&data.quality?.fresh_now===true&&Boolean(data.backtest),{primary:!paper}));
    if(paper)p(box,"Replacing starts a new simulation with new cash; earlier results remain retained and separate. It does not erase losses or turn a restart into continuous performance.","qqq-small qqq-muted");parent.append(box);
    if(paper||data.quality?.retrieved_at){
      const details=e("details","qqq-card");details.append(e("summary","","Close an experiment or archive a changed data basis"));
      p(details,"Close the current paper experiment while retaining its full ledger and last marks. This records no synthetic sell. After a source revision or split-basis change, optionally archive the accepted data snapshot before clearing the active cache for reviewed new research. A failed hold must be acknowledged first.","qqq-small qqq-muted");
      const form=e("form"),reasonLabel=e("label","qqq-field","Reason for closing or archiving"),reason=e("textarea");reason.name="archive_reason";reason.maxLength=1000;reason.value=archiveDraft.reason;reason.addEventListener("input",()=>{archiveDraft.reason=reason.value;});reasonLabel.append(reason);form.append(reasonLabel);inputs.push(reason);
      const choice=e("label","qqq-field qqq-check"),checkbox=e("input");checkbox.type="checkbox";checkbox.name="archive_data";checkbox.checked=archiveDraft.archive_data;checkbox.addEventListener("change",()=>{archiveDraft.archive_data=checkbox.checked;});choice.append(checkbox,e("span","","Also archive the accepted data basis and clear its active cache"));form.append(choice);inputs.push(checkbox);
      const submit=app.button("Review experiment closure","button-secondary",()=>{});submit.type="submit";actions.push({button:submit,allowed:()=>true,allowHold:false});form.append(submit);
      form.addEventListener("submit",async event=>{event.preventDefault();if(!canWrite())return;try{await archiveExperiment();}catch(error){notice=error.message;render();}});details.append(form);parent.append(details);
    }
  }
  function eventPanel(parent) {
    const events=Array.isArray(data.events)?data.events:[];
    if(!events.length)return;
    const box=e("details","qqq-card");box.append(e("summary","","Recent simulation events and receipts"));
    const list=e("ol");for(const event of events.slice(0,20)){const item=e("li","qqq-small");item.append(e("span","",JSON.stringify(event).slice(0,1800)));list.append(item);}box.append(list);parent.append(box);
  }
  function render() {
    const root=$("qqq-content");if(!root||!data||!app?.state.authenticated)return;
    root.replaceChildren();actions=[];inputs=[];lastGlobalPause=Boolean(app.state.status?.paused);
    const intro=e("section","qqq-intro"),copy=e("div");copy.append(e("span","eyebrow","QQQ / RECOVERY RESEARCH"),e("h2","","Observe. Test. Simulate."));
    p(copy,"A fixed recovery experiment with automatic daily data checks and a separate forward paper ledger. Zero model calls; no real-money execution.","qqq-muted");intro.append(copy,e("span","qqq-badge","PAPER ONLY"));root.append(intro);notes(root);
    const row=e("div","qqq-actions");row.append(button(data.config?.enabled?"Disable QQQ workflow":"Enable QQQ workflow",()=>configure({enabled:!data.config.enabled,paused:Boolean(data.config.paused)}),()=>true,{allowHold:true}));
    row.append(button(data.config?.paused?"Resume QQQ workflow":"Pause QQQ workflow",()=>configure({enabled:Boolean(data.config.enabled),paused:!data.config.paused}),()=>Boolean(data.config?.enabled),{allowHold:true}));
    row.append(button("Update market data",()=>run("refresh"),()=>canRun()&&internetAvailable()));
    row.append(button("Backtest cached history",()=>run("backtest"),()=>canRun()&&Boolean(data.quality?.retrieved_at||data.quality?.last_date||data.quality?.latest_session)));root.append(row);
    const quality=data.quality||{},source=e("section","qqq-source");
    p(source,`Data status: ${quality.fresh_now===true?"latest completed session available":"unavailable or stale — no new simulated fills"}. Last retrieved: ${stamp(quality.retrieved_at)}. Expected completed session now: ${text(quality.expected_last_session_now)||"not recorded"}.`,"qqq-small");
    p(source,"Automatic refresh runs with the permitted 6 AM America/Chicago brief and owner-enabled Internet. Global pause, local pause, briefing holds and source failures stop advancement. There is no page timer, hidden trading request or automatic retry here.","qqq-small qqq-muted");root.append(source);
    if(data.hold){const box=e("section","qqq-hold");box.append(e("h3","","QQQ work is held for review"));p(box,text(data.hold.reason||data.hold.error)||"Inspect the saved failure or interrupted request before continuing.");if(data.hold.resolution_error)p(box,text(data.hold.resolution_error),"qqq-caution");p(box,`Request ${text(data.hold.request_id)} · ${pretty(data.hold.status)}. Partial work remains untrusted and is never adopted automatically.`,"qqq-small");box.append(button("Review and acknowledge QQQ hold",acknowledge,()=>idPattern.test(text(data.hold?.request_id)),{allowHold:true}));root.append(box);}
    const resolution=data.latest_history_resolution;
    if(resolution&&resolution.status==="completed"){
      const box=e("section","qqq-card");box.append(e("span","eyebrow","SOURCE HISTORY / REVIEWED"),e("h3","","History review retained"));
      p(box,"A separate reviewed resolution accepted source metadata. The original failed request stays retained; this operation created no simulated fills or funding.","qqq-muted");
      p(box,`Resolution ${text(resolution.request_id)||"not supplied"} · prior request ${text(resolution.failed_request_id)||"not supplied"}.`,"qqq-small");
      for(const [label,key] of [["Review SHA-256","review_sha256"],["Candidate SHA-256","candidate_sha256"],["Accepted data SHA-256","data_sha256"]])p(box,`${label}: ${text(resolution[key])||"Not recorded"}`,"qqq-hash");
      p(box,"Volume and adjusted-close revisions are diagnostic-only for this frozen recovery rule. Changed prices, distributions, splits or dates still stop automatic acceptance.","qqq-small qqq-muted");
      artifacts(box,resolution.artifacts);root.append(box);
    }
    rulePanel(root);const grid=e("div","qqq-grid");paperPanel(grid);historyPanel(grid);root.append(grid);eventPanel(root);
    if(data.last_run)p(root,`Last workflow request: ${pretty(data.last_run.status)} · ${text(data.last_run.request_id)||"not supplied"}.`,"qqq-small qqq-muted");controls();
  }
  async function load() {
    if(!app?.state.authenticated||busy||loading)return;
    loading=true;controls();const marker=generation,token=app.state.token;
    try{const result=await app.api("/qqq");if(!current(marker,token))return;if(!Number.isInteger(result.revision)||result.revision<0||!result.config||typeof result.config.enabled!=="boolean"||typeof result.config.paused!=="boolean")throw new Error("The saved QQQ status is incomplete. No action is permitted.");data=result;stale=false;if(uncertain)inspected=true;render();}
    catch(error){if(current(marker,token)){notice=error.message;stale=true;if(data)render();else $("qqq-content")?.replaceChildren(e("p","qqq-notice",notice));}}
    finally{if(current(marker,token)){loading=false;controls();}}
  }
  async function mutate(kind,body,method="POST") {
    if(!canWrite(kind==="config"||kind==="acknowledge"))return;
    busy=true;notice="";controls();const marker=generation,token=app.state.token;
    try{const result=await app.api("/qqq/"+kind,{method,body});if(!current(marker,token))return;
      if(!result||typeof result!=="object"||(kind==="config"?(!Number.isInteger(result.revision)||!result.config||result.config.enabled!==body.enabled||result.config.paused!==body.paused):(result.request_id!==body.request_id||!["completed","acknowledged","failed","held","interrupted"].includes(result.status))))throw new Error("The QQQ action response did not contain its expected saved receipt. Inspect the saved records; nothing is retried.");
      if(result.ok===false||["failed","held","interrupted"].includes(result.status)){notice=result.reason||result.error||"The QQQ request did not complete. Inspect the saved hold.";}
      else app.toast(kind==="paper"?"Simulated account recorded. No money deposited or orders submitted.":"QQQ workflow receipt recorded; no real orders submitted.");
    }catch(error){if(!current(marker,token))return;notice=error.message;if(error.status===409)stale=true;else if(!error.status||error.status>=500)uncertain={kind,id:body.request_id||null};}
    finally{if(current(marker,token)){busy=false;render();if(!stale)await load();}}
  }
  async function configure(config) { await mutate("config",request("config",data.revision,null,config),"PUT"); }
  async function run(kind) { if(kind==="refresh"&&(!canRun()||!internetAvailable()))return;if(kind==="backtest"&&(!canRun()||!Boolean(data.quality?.retrieved_at||data.quality?.last_date||data.quality?.latest_session)))return;await mutate(kind,request(kind,data.revision,newId())); }
  async function startPaper() {
    if(!canRun()||data.quality?.fresh_now!==true||!data.backtest)return;
    const marker=generation,token=app.state.token,revision=data.revision,replacing=Boolean(data.paper);
    const confirmed=await app.confirmAction(replacing?"Archive and replace this simulation?":"Start the forward QQQ paper account?",replacing?"The old simulated account and its losses remain retained. A new separate account starts with $10,000 simulated cash. No real funds, broker connection or orders are involved.":"Start with $10,000 of simulated cash, a frozen rule and modeled transaction costs. This is not your budget or a deposit. No real funds, broker connection or orders are involved.",replacing?"Retain old and start new":"Start simulated account","Keep current state");
    if(!confirmed||!current(marker,token)||data.revision!==revision)return;
    await mutate("paper",request("paper",revision,newId(),{replace:replacing}));
  }
  async function acknowledge() {
    if(!data.hold||!canWrite(true))return;
    const marker=generation,token=app.state.token,revision=data.revision,identifier=data.hold.request_id;
    const confirmed=await app.confirmAction("Acknowledge the inspected QQQ hold?","The earlier request remains closed. Partial outputs and historical-data revisions are not adopted, deleted or replayed. Review the saved evidence before releasing this hold.","Acknowledge inspected hold","Keep held");
    if(!confirmed||!current(marker,token)||data.revision!==revision||data.hold?.request_id!==identifier)return;
    await mutate("acknowledge",request("acknowledge",revision,identifier));
  }
  async function archiveExperiment() {
    if(!canWrite())return;
    const marker=generation,token=app.state.token,revision=data.revision,body=request("archive",revision,newId(),{...archiveDraft});
    const confirmed=await app.confirmAction("Close this paper experiment?",`${body.reason}\n\nThe current simulated ledger and last marks stay retained. No synthetic sale or real order occurs.${body.archive_data?" The accepted source snapshot is also archived and its active cache cleared; new research needs a separately checked source basis.":" The accepted source cache remains available."}`,"Retain records and close","Keep current experiment");
    if(!confirmed||!current(marker,token)||data.revision!==revision)return;
    await mutate("archive",body);
    if(!uncertain&&!stale&&!data.paper){archiveDraft={reason:"",archive_data:false};render();}
  }
  function status(){if(data&&lastGlobalPause!==Boolean(app?.state.status?.paused))render();else controls();}
  function locked(){generation++;data=null;busy=false;loading=false;stale=false;notice="";uncertain=null;inspected=false;actions=[];inputs=[];lastGlobalPause=null;archiveDraft={reason:"",archive_data:false};$("qqq-content")?.replaceChildren();controls();}
  function attach(value){app=value;const root=$("qqq-content");if(!root||attached===root)return;attached=root;$("refresh-qqq")?.addEventListener("click",load);controls();}
  window.ariadneQQQ={attach,load,status,locked,request,money,percent,artifactPath,run,startPaper,acknowledge,archiveExperiment};
})();
