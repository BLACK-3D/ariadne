"use strict";

// Owner actions only. Viewing this panel never imports data or advances paper.
(() => {
  let app, attached, data=null, generation=0, busy=false, loading=false, stale=false;
  let notice="", uncertain=null, inspected=false, actions=[], fields=[], lastPause=null;
  let importDraft="market-data/options/current.json", reasonDraft="", archiveData=false;
  const $=id=>document.getElementById(id), e=(tag,cls="",value)=>app.node(tag,cls,value);
  const txt=value=>typeof value==="string"?value:"";
  const identity=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
  const current=(marker,token)=>marker===generation&&app?.state.authenticated&&app.state.token===token;
  const newId=()=>"options-ui-"+crypto.randomUUID().replaceAll("-","");
  const p=(parent,value,cls="options-small")=>parent.append(e("p",cls,value));
  function amount(value){
    if(typeof value!=="string"||! /^-?\d{1,16}(?:\.\d{1,12})?$/.test(value))return "Not measured";
    const result=Number(value);return Number.isFinite(result)?result.toLocaleString("en-US",{style:"currency",currency:"USD"}):"Not measured";
  }
  function path(value){
    const result=txt(value).replaceAll("\\","/");
    if(!result||result.length>1000||result.startsWith("/")||/[\u0000-\u001f\u007f:<>"|?*]/.test(result)||result.split("/").some(part=>!part||part==="."||part===".."||/[. ]$/.test(part)||/^(?:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\.|$)/i.test(part)))throw new Error("Choose a JSON file inside Ariadne Workspace.");
    return result;
  }
  function request(kind,revision,identifier,extra={}){
    if(!Number.isSafeInteger(revision)||revision<0)throw new Error("Inspect the latest options revision first.");
    if(kind==="config"){
      if(typeof extra.enabled!=="boolean"||typeof extra.paused!=="boolean")throw new Error("Choose explicit workflow controls.");
      return {expected_revision:revision,enabled:extra.enabled,paused:extra.paused};
    }
    if(typeof identifier!=="string"||!identity.test(identifier))throw new Error("Use a unique bounded request ID.");
    const body={request_id:identifier,expected_revision:revision};
    if(kind==="import")return {...body,path:path(extra.path)};
    if(kind==="paper")return {...body,acknowledge_simulated:true};
    if(kind==="archive"){
      const reason=txt(extra.reason).trim();
      if(!reason||reason.length>1000||typeof extra.archive_data!=="boolean")throw new Error("Supply an archive reason and explicit data choice.");
      return {...body,reason,archive_data:extra.archive_data};
    }
    if(!["refresh","backtest","acknowledge"].includes(kind))throw new Error("Unsupported options action.");
    return body;
  }
  function writable(held=false){return Boolean(app?.state.authenticated&&data&&!busy&&!loading&&!stale&&!uncertain&&!app.state.status?.paused&&(held||!data.hold));}
  function runnable(){return writable()&&data.config.enabled&&!data.config.paused;}
  function controls(){
    if($("refresh-options"))$("refresh-options").disabled=busy||loading||!app?.state.authenticated;
    for(const item of actions)item.node.disabled=!writable(item.held)||!item.allowed();
    for(const field of fields)field.disabled=busy||loading||!app?.state.authenticated;
  }
  function button(label,fn,allowed=()=>true,held=false){
    const node=app.button(label,"button-secondary",async()=>{if(!writable(held)||!allowed())return;try{await fn();}catch(error){notice=error.message;render();}});
    node.type="button";actions.push({node,allowed,held});return node;
  }
  function metric(parent,label,value){const box=e("div","options-metric");box.append(e("span","",label),e("strong","",value));parent.append(box);}
  function coverage(parent,quality,label){
    if(!quality){p(parent,label==="Historical"?"No historical options dataset imported.":"Forward quotes: not connected; waiting for a recorded, current dataset in the local inbox.");return;}
    p(parent,`${label} coverage: ${txt(quality.first_observed_at).slice(0,10)||"unknown start"} to ${txt(quality.last_observed_at).slice(0,10)||"unknown end"} · ${Number.isInteger(quality.snapshots)?quality.snapshots:"unknown"} sessions · ${Number.isInteger(quality.quotes)?quality.quotes.toLocaleString("en-US"):"unknown"} contract quotes.`);
    p(parent,quality.quote_time_basis==="assumed_eod"?"Quote times assumed from daily dates. Retrospective research only; source accuracy and completeness remain unverified.":`${quality.fresh_now?"Latest completed session available":"Saved or stale observations"}. Recorded source times; price accuracy and executable liquidity remain unverified.`);
    const details=e("details");details.append(e("summary","","Source provenance and validation limits"),e("pre","",JSON.stringify(quality,null,2)));parent.append(details);
  }
  function artifactButtons(parent,items){
    const row=e("div","options-actions");
    for(const item of (Array.isArray(items)?items:[]).slice(0,4))try{
      const relative=path(item.path),node=app.button(relative.endsWith("accepted-input.json")?"Inspect historical input":"Inspect full research report","button-secondary",()=>app.openArtifact(relative));node.type="button";row.append(node);
    }catch{}
    parent.append(row);
  }
  function render(){
    const root=$("options-content");if(!root||!data||!app?.state.authenticated)return;
    root.replaceChildren();actions=[];fields=[];lastPause=Boolean(app.state.status?.paused);
    const hero=e("section","options-hero"),copy=e("div"),limit=e("div","options-limit");
    copy.append(e("span","eyebrow","OPTIONS / ASYMMETRIC RESEARCH"),e("h2","","Small stake. Measured upside."));
    p(copy,"Fully paid long-call experiments with actual option quotes, a frozen rule and a separate forward paper ledger. Zero model calls.");
    limit.append(e("strong","","$500"),e("span","","MONTHLY SIMULATED LOSS BUDGET"));hero.append(copy,limit);root.append(hero);
    const warning=e("div","options-warning");warning.setAttribute("role","status");
    p(warning,"SIMULATED ONLY. The cap bounds modeled monthly net loss from a flat opening account. It does not cap a fall from unrealized profits. No short options, brokerage connection or real-money orders.");
    if(notice)p(warning,notice);
    if(stale)p(warning,"The revision changed or could not be verified. Inspect the saved state before another action.");
    if(app.state.status?.paused)p(warning,"Global autonomy is paused. Saved research remains readable; mutations are held.");
    if(uncertain){p(warning,`Interrupted ${uncertain.kind} response · ${uncertain.id||"configuration"}. No automatic retry. Inspect saved receipts first.`);
      const node=app.button("I inspected the saved options receipts","button-secondary",()=>{if(!inspected||busy||loading)return;uncertain=null;inspected=false;notice="";render();});node.type="button";node.disabled=!inspected||busy||loading;warning.append(node);}
    root.append(warning);
    const row=e("div","options-actions");row.append(button(data.config.enabled?"Disable options workflow":"Enable options workflow",()=>mutate("config",{enabled:!data.config.enabled,paused:data.config.paused}),()=>true,true));
    row.append(button(data.config.paused?"Resume options workflow":"Pause options workflow",()=>mutate("config",{enabled:data.config.enabled,paused:!data.config.paused}),()=>data.config.enabled,true));
    row.append(button("Read forward quote inbox",()=>mutate("refresh"),runnable),button("Backtest imported history",()=>mutate("backtest"),()=>runnable()&&Boolean(data.quality)));root.append(row);
    if(data.hold){const held=e("div","options-warning");p(held,`HELD · ${txt(data.hold.request_id)} · ${txt(data.hold.reason)}`);held.append(button("Acknowledge inspected operation",acknowledge,()=>identity.test(txt(data.hold.request_id)),true));root.append(held);}
    const grid=e("div","options-grid"),paper=e("section","options-card"),summary=data.paper?.summary;
    paper.append(e("span","eyebrow","FORWARD / SIMULATED CASH"),e("h3","","Options paper account"));
    if(summary){const metrics=e("div","options-metrics");
      for(const[label,key]of [["Equity","equity"],["Trading P/L","trading_pnl"],["External simulated funding","contributions"],["Monthly net P/L","monthly_pnl"],["Monthly risk committed","monthly_budget_used"],["Monthly risk remaining","monthly_budget_remaining"]])metric(metrics,label,amount(summary[key]));paper.append(metrics);
      p(paper,`Ledger month ${txt(summary.month)} · entries ${Number.isInteger(summary.trade_count)?summary.trade_count:"unknown"} · completed trades ${Number.isInteger(summary.closed_trade_count)?summary.closed_trade_count:"unknown"}. Deposits are excluded from trading P/L.`);
      p(paper,summary.cap_qualified?"Monthly cap: within the modeled premium and fee budget.":"CAP ASSURANCE UNAVAILABLE. Missing disposal or ledger evidence blocks new risk.",summary.cap_qualified?"options-small":"options-warning");
      if(summary.hold)p(paper,JSON.stringify(summary.hold).slice(0,1800),"options-warning");
      p(paper,summary.position?`Open contract: ${txt(summary.position.contract_id)} · last bid mark ${amount(summary.position.marked_bid)} per share.`:"No open option position.");
      p(paper,summary.pending?`Pending paper decision: ${JSON.stringify(summary.pending).slice(0,1200)}`:"No pending paper decision. Cash is a valid outcome.");
      p(paper,summary.last_daily_result?`Latest dated result: ${JSON.stringify(summary.last_daily_result).slice(0,2200)}`:"No forward daily trading result yet. Starting cash is not a return.");
    }else{p(paper,"Start with $500 simulated cash. Later observed calendar months add $500 of simulated external funding only after the previous month is verified flat. This does not spend your money.");paper.append(button("Start $500 monthly paper experiment",startPaper,runnable));}
    p(paper,"The 6 AM Chicago brief reads the fixed local quote inbox. There is no connected options subscription. Offline reads and backtests work; absent or stale quotes never become invented fills.");grid.append(paper);
    const history=e("section","options-card");history.append(e("span","eyebrow","HISTORICAL / QUOTE-BASED RESEARCH"),e("h3","","Backtest evidence"));
    if(data.backtest){const report=data.backtest,metrics=report.metrics||{};
      p(history,`Research gate: ${txt(report.research_gate)||"not qualified"}. Historical fills are assumptions; this result does not qualify real-money execution.`,"options-warning");
      const values=e("div","options-metrics");for(const[label,key]of [["Trading P/L excluding deposits","trading_pnl"],["Ending equity","ending_equity"],["External simulated contributions","total_contributions"],["Maximum modeled monthly loss","maximum_monthly_loss"]])metric(values,label,amount(metrics[key]));history.append(values);
      p(history,`Symbol ${txt(report.symbol)} · entries ${Number.isInteger(metrics.trade_count)?metrics.trade_count:"unknown"} · completed trades ${Number.isInteger(metrics.closed_trade_count)?metrics.closed_trade_count:"unknown"}.`);
      p(history,`Realized payout milestones: ${JSON.stringify(metrics.payout_milestones||{})}. A large possible payoff is not an estimated win probability.`);artifactButtons(history,report.artifacts);
    }else p(history,"Import historical option bid/ask quotes, then run the fixed rule. Underlying stock bars and theoretical option prices are not substituted for missing contract history.");
    coverage(history,data.quality,"Historical");coverage(history,data.forward_quality,"Forward");grid.append(history);
    const importer=e("section","options-card options-full");importer.append(e("h3","","Import historical quotes"));
    p(importer,"Use the bundled converter for Cboe CSV or the strict canonical JSON format. Imports stay local and do not advance the forward account. Data from undocumented sources remains explicitly unverified.");
    const form=e("form"),label=e("label","options-field","JSON file inside Ariadne Workspace"),input=e("input");input.name="options_import_path";input.value=importDraft;input.maxLength=1000;input.addEventListener("input",()=>{importDraft=input.value;});label.append(input);fields.push(input);form.append(label);
    const submit=e("button","button button-secondary","Import historical dataset");submit.type="submit";actions.push({node:submit,allowed:runnable,held:false});form.append(submit);
    form.addEventListener("submit",async event=>{event.preventDefault();if(!runnable())return;try{await mutate("import",{path:importDraft});}catch(error){notice=error.message;render();}});importer.append(form);p(importer,`Daily forward inbox: ${txt(data.inbox)}. Forward observations need recorded timestamps, quoted size and the latest completed session. Assumed-EOD research cannot place paper fills.`);grid.append(importer);
    const rule=e("details","options-card options-full");rule.append(e("summary","","Frozen selection rule and monthly risk contract"));for(const[key,value]of Object.entries(data.rule||{}).slice(0,30))p(rule,`${key}: ${typeof value==="string"?value:JSON.stringify(value)}`);p(rule,`Rule SHA-256 ${txt(data.rule_sha256)}`);grid.append(rule);
    if(data.paper||data.quality||data.forward_quality){const archive=e("details","options-card options-full");archive.append(e("summary","","Archive an experiment or input basis"));p(archive,"Retains the ledger and last marks without inventing a sale. Archiving never resets a month's allowance. An unresolved archived position blocks a new experiment.");
      const reasonLabel=e("label","options-field","Reason"),reason=e("textarea");reason.name="options_archive_reason";reason.maxLength=1000;reason.value=reasonDraft;reason.addEventListener("input",()=>{reasonDraft=reason.value;});reasonLabel.append(reason);fields.push(reason);archive.append(reasonLabel);
      const checkLabel=e("label","options-field"),check=e("input");check.type="checkbox";check.checked=archiveData;check.name="options_archive_data";check.addEventListener("change",()=>{archiveData=check.checked;});checkLabel.append(check,e("span","","Also retain and clear both imported data caches"));fields.push(check);archive.append(checkLabel,button("Review archival",archiveExperiment));grid.append(archive);}
    if(Array.isArray(data.events)&&data.events.length){const events=e("details","options-card options-full");events.append(e("summary","","Recent paper events"));const pre=e("pre","",JSON.stringify(data.events.slice(0,20),null,2));events.append(pre);grid.append(events);}
    root.append(grid);if(data.last_run)p(root,`Last request ${txt(data.last_run.request_id)} · ${txt(data.last_run.status)} · ${txt(data.last_run.outcome)}.`);controls();
  }
  async function load(){
    if(!app?.state.authenticated||busy||loading)return;loading=true;controls();const marker=generation,token=app.state.token;
    try{const result=await app.api("/options");if(!current(marker,token))return;
      if(!Number.isSafeInteger(result?.revision)||result.revision<0||typeof result.config?.enabled!=="boolean"||typeof result.config?.paused!=="boolean")throw new Error("Saved options status is incomplete. No action is permitted.");
      data=result;stale=false;if(uncertain)inspected=true;render();
    }catch(error){if(current(marker,token)){notice=error.message;stale=true;if(data)render();else $("options-content")?.replaceChildren(e("p","options-warning",notice));}}
    finally{if(current(marker,token)){loading=false;controls();}}
  }
  async function mutate(kind,extra={},identifier=null){
    if(!writable(kind==="config"||kind==="acknowledge"))return;
    if(["refresh","backtest","import","paper"].includes(kind)&&!runnable())return;
    if(kind==="paper"&&data.paper)return;
    const body=request(kind,data.revision,identifier||newId(),extra),marker=generation,token=app.state.token;
    busy=true;notice="";controls();
    try{const result=await app.api("/options/"+kind,{method:kind==="config"?"PUT":"POST",body});if(!current(marker,token))return;
      if(!result||typeof result!=="object"||(kind==="config"?(!Number.isSafeInteger(result.revision)||result.config?.enabled!==body.enabled||result.config?.paused!==body.paused):(result.request_id!==body.request_id||!["completed","acknowledged"].includes(result.status))))throw new Error("The action lacks its expected completed receipt. Inspect saved records; nothing is retried.");
      app.toast("Options simulation receipt saved. No real order or payment.");
    }catch(error){if(!current(marker,token))return;notice=error.message;if(error.status===409)stale=true;else if(!error.status||error.status>=500)uncertain={kind,id:body.request_id||null};}
    finally{if(current(marker,token)){busy=false;render();if(!stale)await load();}}
  }
  async function confirmed(title,description,label,fn){
    const marker=generation,token=app.state.token,revision=data.revision;
    if(await app.confirmAction(title,description,label,"Keep current state")&&current(marker,token)&&revision===data.revision)await fn();
  }
  async function startPaper(){if(!runnable()||data.paper)return;await confirmed("Start the options paper experiment?","Start $500 simulated cash with at most $500 committed premium/fees per month. No profits recycle the allowance. No cross-month positions or real orders. A restart cannot reset this month's budget.","Start simulated experiment",()=>mutate("paper"));}
  async function acknowledge(){if(!writable(true)||!data.hold)return;const identifier=data.hold.request_id;await confirmed("Acknowledge inspected options work?","The failed request stays closed. Partial inputs, fills and files are not adopted, deleted or replayed.","Acknowledge inspected request",()=>mutate("acknowledge",{},identifier));}
  async function archiveExperiment(){if(!writable())return;const body=request("archive",data.revision,newId(),{reason:reasonDraft,archive_data:archiveData});await confirmed("Archive this options experiment?",`${body.reason}\n\nLast marks, history and losses remain retained. No synthetic sell. This cannot replenish a monthly allowance.${body.archive_data?" Both data caches are retained in evidence before clearing.":" Imported data stays cached."}`,"Retain evidence and archive",()=>mutate("archive",{reason:body.reason,archive_data:body.archive_data},body.request_id));}
  function status(){if(data&&lastPause!==Boolean(app?.state.status?.paused))render();else controls();}
  function locked(){generation++;data=null;busy=false;loading=false;stale=false;notice="";uncertain=null;inspected=false;actions=[];fields=[];lastPause=null;importDraft="market-data/options/current.json";reasonDraft="";archiveData=false;$("options-content")?.replaceChildren();controls();}
  function attach(value){app=value;const root=$("options-content");if(!root||attached===root)return;attached=root;$("refresh-options")?.addEventListener("click",load);controls();}
  window.ariadneOptions={attach,load,status,locked,request,path,amount,mutate,startPaper,acknowledge,archiveExperiment};
})();
