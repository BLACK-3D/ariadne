"use strict";

// Owner actions only. Viewing this panel never imports data or advances paper.
(() => {
  let app, attached, data=null, generation=0, busy=false, loading=false, stale=false;
  let notice="", uncertain=null, inspected=false, actions=[], fields=[], lastPause=null;
  let importDraft="market-data/options/current.json", manifestDraft="market-data/options/archives/qqq-2022/catalog.json", reasonDraft="", archiveData=false;
  let vixSignalDraft="market-data/vix/index-history.json",vixQuoteDraft="market-data/vix/option-history.json";
  let baselineDraft="";
  const strategyNames={recovery_call_v1:"Recovery / calls",breakout_call_v1:"Breakout / calls",breakdown_put_v1:"Breakdown / puts",eod_recovery_call_v1:"EOD recovery / calls",eod_breakout_call_v1:"EOD breakout / calls",eod_breakdown_put_v1:"EOD breakdown / puts",regime_recovery_call_v1:"Trend-filtered recovery / calls",trend_breakout_call_v1:"Trend breakout / calls",trend_breakdown_put_v1:"Trend breakdown / puts"};
  const $=id=>document.getElementById(id), e=(tag,cls="",value)=>app.node(tag,cls,value);
  strategyNames.f_stress_breakdown_put_v1="Stress-filtered / puts";
  strategyNames.ko_stress_breakdown_put_v1="KO stress-filtered / puts";
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
    if(["eod-config","root-config"].includes(kind)){
      if(typeof extra.enabled!=="boolean")throw new Error("Choose an explicit EOD collection setting.");
      return {...body,enabled:extra.enabled,acknowledge_source:true};
    }
    if(["eod-migrate","root-migrate"].includes(kind))return {...body,acknowledge_simulated:true,acknowledge_partial_eod:true,baseline_path:extra.baseline_path==null||extra.baseline_path===""?null:path(extra.baseline_path)};
    if(kind==="stress-activate")return {...body,acknowledge_simulated:true,acknowledge_stress_only:true};
    if(kind==="feed"){
      if(typeof extra.enabled!=="boolean")throw new Error("Choose an explicit feed setting.");
      return {...body,enabled:extra.enabled,acknowledge_feed:true};
    }
    if(["import","register-archive","vix-signals","vix-backtest"].includes(kind))return {...body,path:path(extra.path)};
    if(["paper","portfolio","upgrade-portfolio"].includes(kind))return {...body,acknowledge_simulated:true};
    if(kind==="archive"){
      const reason=txt(extra.reason).trim();
      if(!reason||reason.length>1000||typeof extra.archive_data!=="boolean")throw new Error("Supply an archive reason and explicit data choice.");
      return {...body,reason,archive_data:extra.archive_data};
    }
    if(!["refresh","backtest","compare","collect-feed","research-compare","acknowledge","eod-collect","root-collect"].includes(kind))throw new Error("Unsupported options action.");
    return body;
  }
  function writable(held=false){return Boolean(app?.state.authenticated&&data&&!busy&&!loading&&!stale&&!uncertain&&!app.state.status?.paused&&(held||!data.hold));}
  function runnable(){return writable()&&data.config.enabled&&!data.config.paused;}
  function bankrollMode(){return data?.paper?.state?.schema===6||data?.paper?.summary?.schema===6;}
  function eodActive(){return Boolean(data?.eod?.config?.initialized||[3,4,5,6].includes(data?.paper?.state?.schema)||[3,4,5,6].includes(data?.paper?.summary?.schema));}
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
    if(quality.quote_time_basis==="vendor_eod_date_only"){
      p(parent,`${label} source-date coverage: ${txt(quality.first_source_date)||"unknown start"} to ${txt(quality.last_source_date)||"unknown end"} · ${Number.isInteger(quality.snapshots)?quality.snapshots:"unknown"} sessions · ${Number.isInteger(quality.quotes)?quality.quotes.toLocaleString("en-US"):"unknown"} returned contract quotes.`);
      p(parent,`${quality.fresh_now===true?"Latest expected source session saved":"Saved or stale source date"}. ${quality.partial_chain===true?"Partial returned chain":"Chain completeness unverified"}; option quote-event time UNKNOWN. Date-only EOD price proxy, research only; execution is not qualified.`);
      const details=e("details");details.append(e("summary","","Source provenance and validation limits"),e("pre","",JSON.stringify(quality,null,2)));parent.append(details);return;
    }
    p(parent,`${label} coverage: ${txt(quality.first_observed_at).slice(0,10)||"unknown start"} to ${txt(quality.last_observed_at).slice(0,10)||"unknown end"} · ${Number.isInteger(quality.snapshots)?quality.snapshots:"unknown"} sessions · ${Number.isInteger(quality.quotes)?quality.quotes.toLocaleString("en-US"):"unknown"} contract quotes.`);
    p(parent,quality.quote_time_basis==="assumed_eod"?"Quote times assumed from daily dates. Retrospective research only; source accuracy and completeness remain unverified.":`${quality.fresh_now?"Latest completed session available":"Saved or stale observations"}. Recorded source times; price accuracy and executable liquidity remain unverified.`);
    const details=e("details");details.append(e("summary","","Source provenance and validation limits"),e("pre","",JSON.stringify(quality,null,2)));parent.append(details);
  }
  function artifactButtons(parent,items){
    const row=e("div","options-actions");
    for(const item of (Array.isArray(items)?items:[]).slice(0,4))try{
      const relative=path(item.path),label=relative.endsWith("vix-report.json")?"Inspect VIX research":relative.endsWith("vix-input.json")?"Inspect VIX input":relative.endsWith("/VIX.csv")?"Inspect private VIX history":relative.endsWith("/VIX9D.csv")?"Inspect private VIX9D history":relative.endsWith("accepted-input.json")?"Inspect historical input":relative.endsWith("research-manifest.json")?"Inspect research archive manifest":relative.endsWith("research.json")?"Inspect regime research":relative.endsWith("archive-manifest.json")?"Inspect accepted archive manifest":relative.endsWith("comparison.json")?"Inspect strategy comparison":"Inspect full research report",node=app.button(label,"button-secondary",()=>app.openArtifact(relative));node.type="button";row.append(node);
    }catch{}
    parent.append(row);
  }
  function render(){
    const root=$("options-content");if(!root||!data||!app?.state.authenticated)return;
    root.replaceChildren();actions=[];fields=[];lastPause=Boolean(app.state.status?.paused);
    const hero=e("section","options-hero"),copy=e("div"),limit=e("div","options-limit");
    copy.append(e("span","eyebrow","OPTIONS / ASYMMETRIC RESEARCH"),e("h2","","Small stake. Measured upside."));
    p(copy,bankrollMode()?"Stress-filtered puts on one total-funded paper account. Actual option quotes, auditable decisions, zero model calls. Research comparisons never select a live winner.":"Three fixed call/put hypotheses. One shared paper portfolio. Actual option quotes, auditable decisions, zero model calls.");
    limit.append(e("strong","",bankrollMode()?"$25,000":"$500"),e("span","",bankrollMode()?"TOTAL SIMULATED BANKROLL / NO TOPUPS":"MONTHLY SIMULATED LOSS BUDGET"));hero.append(copy,limit);root.append(hero);
    const warning=e("div","options-warning");warning.setAttribute("role","status");
    p(warning,bankrollMode()?"SIMULATED ONLY. One total $25,000 bankroll, funded once. The entire bankroll can be lost. Monthly commitment resets add no cash. No short options, brokerage connection or real-money orders.":"SIMULATED ONLY. The cap bounds modeled monthly net loss from a flat opening account. It does not cap a fall from unrealized profits. No short options, brokerage connection or real-money orders.");
    if(notice)p(warning,notice);
    if(stale)p(warning,"The revision changed or could not be verified. Inspect the saved state before another action.");
    if(app.state.status?.paused)p(warning,"Global autonomy is paused. Saved research remains readable; mutations are held.");
    if(uncertain){p(warning,`Interrupted ${uncertain.kind} response · ${uncertain.id||"configuration"}. No automatic retry. Inspect saved receipts first.`);
      const node=app.button("I inspected the saved options receipts","button-secondary",()=>{if(!inspected||busy||loading)return;uncertain=null;inspected=false;notice="";render();});node.type="button";node.disabled=!inspected||busy||loading;warning.append(node);}
    root.append(warning);
    const row=e("div","options-actions");row.append(button(data.config.enabled?"Disable options workflow":"Enable options workflow",()=>mutate("config",{enabled:!data.config.enabled,paused:data.config.paused}),()=>true,true));
    row.append(button(data.config.paused?"Resume options workflow":"Pause options workflow",()=>mutate("config",{enabled:data.config.enabled,paused:!data.config.paused}),()=>data.config.enabled,true));
    if(!eodActive())row.append(button("Read forward quote inbox",()=>mutate("refresh"),()=>runnable()&&!data.feed?.config?.initialized),button("Backtest imported history",()=>mutate("backtest"),()=>runnable()&&Boolean(data.quality)));root.append(row);
    if(data.eod){
      const source=e("section","options-card");source.append(e("span","eyebrow",`${txt(data.eod.config.symbol)} / EXPERIMENTAL EOD / NO ACCOUNT`),e("h3","","Under-$100 options paper source"));
      p(source,`Actual vendor bid/ask values and quoted sizes from a partial returned ${txt(data.eod.config.symbol)} chain. Option quote-event time UNKNOWN: date-only EOD price proxies, lower-evidence research only. No live feed, execution qualification or guaranteed publication.`,"options-warning");
      p(source,bankrollMode()?"Underlying below $100 at each entry. One shared long-option position; integer contracts limited by supplied quote size. Entry risk is min($1,000, 4% of current equity, available cash, unused shared monthly allowance), including both fees per contract. One $25,000 gross monthly ceiling; no within-month profit recycling.":"The underlying share price must be strictly below $100 at each new entry. One standard long contract total; each entry is at most $100 including premium and both fees. The SAME account shares ONE $500 monthly commitment across all strategies. Profits never replenish it.");
      p(source,bankrollMode()?`Source ${data.eod.config.enabled?"enabled":"paused"}; total funding is $25,000 including the prior credited balance. No automatic top-ups or second account. Existing receipts and holds remain.`:eodActive()?`Source ${data.eod.config.enabled?"enabled":"paused"}; existing account identity, cash, contributions and monthly allowance retained. Migration adds NO new funding and never resets risk.`:"Migrate only a healthy, zero-trade, unspent existing shared account. Prior observations are retained as evidence. No second account, added funding, historical fills or monthly reset.");
      p(source,"Collection checks after 9:35 AM Eastern for the previous completed source session. Provider timing is not guaranteed. The 6 AM brief reads saved results; offline, failed or uncertain work never invents a fill or retries itself.");
      if(data.eod.quality)coverage(source,data.eod.quality,"Experimental EOD");else p(source,`No ${txt(data.eod.config.symbol)||"selected"} option source session accepted yet. Starting cash and stock warmup are not trading returns.`);
      if(eodActive()){
        const history=data.paper?.state?.history,reference=Array.isArray(history)?history[history.length-1]:null;
        if(reference)p(source,`Dated underlying reference ${txt(reference.source_date)||"unknown"}: ${amount(reference.underlying_close)} per share, unadjusted regular last bar; not a current live price.`);
        p(source,`Stock indicator seeds: ${Number.isInteger(data.eod.baseline_count)?data.eod.baseline_count:"unknown"}. Seeds never adopt historical trades; 14 completed references are required before a signal.`);
      }
      if(data.eod.last_run)p(source,`Latest EOD operation: ${txt(data.eod.last_run.status)||"unknown"} · source date ${txt(data.eod.last_run.source_date)||"not accepted"} · recorded ${txt(data.eod.last_run.created_at)||"not recorded"}. Quote-event time remains unknown.`);
      if(data.eod.publication_assurance?.waiting_for_disposal_source)p(source,`Disposal source ${txt(data.eod.publication_assurance.deadline_source_date)||"unknown"} remains unverified. Awaiting publication is not a verified sale or permission for funding rollover.`,"options-warning");
      if(Number.isInteger(data.eod.retention?.remaining_evidence_operations))p(source,`Retained evidence capacity: ${data.eod.retention.remaining_evidence_operations} operations remaining. Full storage stops collection for reviewed maintenance.`);
      const controls=e("div","options-actions");
      if(!eodActive()){
        const advanced=e("details");advanced.append(e("summary","","Optional stock warmup manifest"));p(advanced,"Optional local onclick_f_stock_baseline manifest. Its verified prior stock references seed indicators only, never past option trades. Leave blank to start without warmup.");
        const label=e("label","options-field","Baseline manifest inside Ariadne Workspace"),input=e("input");input.name="options_eod_baseline_path";input.maxLength=1000;input.value=baselineDraft;input.addEventListener("input",()=>{baselineDraft=input.value;});label.append(input);fields.push(input);advanced.append(label);source.append(advanced);
        controls.append(button("Migrate shared account to F EOD",migrateEOD,()=>runnable()&&data.eod.migration_eligible===true));
        if(data.eod.migration_eligible!==true)p(source,"Migration is unavailable for this saved account. Its evidence or existing activity must be reviewed; no replacement account is created.");
      }else controls.append(button(`${data.eod.config.enabled?"Pause":"Resume"} ${txt(data.eod.config.symbol)} EOD source`,configureEOD,runnable),button(`Capture available ${txt(data.eod.config.symbol)} EOD quotes`,()=>mutate([5,6].includes(data.paper?.state?.schema)?"root-collect":"eod-collect"),()=>runnable()&&data.eod.config.enabled===true&&data.eod.collection_window_open===true));
      source.append(controls);root.append(source);
      if(data.eod.stress){
        const stress=data.eod.stress,card=e("section","options-card");
        card.append(e("span","eyebrow","FORWARD / STRESS FILTER"),e("h3","",stress.active?"Stress-filtered puts / active":"Stress-filtered puts / available"));
        p(card,bankrollMode()?`One fully paid ${txt(data.eod.config.symbol)} put position at a time. $25,000 total bankroll; $1,000 / 4%-of-equity entry maximum, supplied-size limited. No automatic top-ups. This hypothesis has no established profitable edge.`:`One fully paid ${txt(data.eod.config.symbol)} put at a time. The existing $500 shared monthly commitment and $100 all-in entry cap stay in force. This is an owner-selected hypothesis, with no established profitable edge.`);
        p(card,"A fresh QQQ/SPY stress regime is required for a new decision. A modeled BUY fill requires prior-session context published before that source session opened. Missing context blocks entries while exit checks continue.");
        const last=stress.last_context;
        p(card,last?`Saved context: ${txt(last.source_session)} · ${txt(last.label)} · actually published ${txt(last.published_at)}.`:"No published forward context yet. Cash remains available; no benchmark is invented.");
        if(stress.context_hold)p(card,`Context entry hold: ${txt(stress.context_hold.reason)||txt(stress.context_hold.status)}. Inspect saved evidence; no automatic retry.`,"options-warning");
        if(!stress.active)card.append(button("Focus this paper account on stress puts",activateStress,()=>runnable()&&stress.migration_eligible===true));
        root.append(card);
      }
    }
    if(data.feed&&!eodActive()){
      const source=e("section","options-card");source.append(e("span","eyebrow","PUBLIC QUOTES / NO ACCOUNT"),e("h3","","AAPL daily quote source"));
      p(source,"Actual vendor end-of-day bid/ask snapshots and quoted sizes. The previous session becomes available after the next market open; collection starts at 9:35 AM Eastern. This is delayed paper research, not a live feed.");
      p(source,data.feed.config.initialized?`Source ${data.feed.config.enabled?"enabled":"paused"} on the existing AAPL account. One shared $500 allowance remains in force.`:"Optional activation retargets only a pristine QQQ paper account to AAPL in place. Its account ID, $500 cash and monthly allowance are preserved. No extra account or funding.");
      p(source,"The 6 AM brief shows the latest saved, dated results. Market freshness and overdue disposal assurance remain visible while waiting for publication. Offline or failed collection cannot invent fills. Quarantined crossed quotes cannot be used; a bad open/pending contract quote stops advancement.");
      if(data.feed.last_run){const latest=data.feed.last_run,provenance=latest.provenance||{};p(source,`Latest source operation: ${txt(latest.status)} · observed ${txt(latest.source_observed_at)||"not accepted"} · received ${txt(latest.retrieved_at)||"not accepted"} · quarantined rows ${Number.isInteger(provenance.quarantined_rows)?provenance.quarantined_rows:"not measured"}.`);if(Array.isArray(provenance.quarantined)&&provenance.quarantined.length){const details=e("details");details.append(e("summary","","Inspect quarantined quotes"),e("pre","",JSON.stringify(provenance.quarantined,null,2)));source.append(details);}}
      if(data.feed.config.initialized&&Number.isInteger(data.paper?.state?.processed_count))p(source,`Accepted observations: ${data.paper.state.processed_count}. The unchanged strategies need 14 closes before a signal; the first capture warms indicators and adopts no historical trade.`);
      if(Number.isInteger(data.feed.retention?.remaining_evidence_operations))p(source,`Retained evidence capacity: ${data.feed.retention.remaining_evidence_operations} source/research/archive operations remaining. Collection stops for reviewed maintenance when full; financial history is never deleted automatically.`);
      for(const item of (data.feed.last_run?.artifacts||[]).filter(value=>txt(value.path).endsWith("provenance.json")).slice(0,1))try{const relative=path(item.path),node=app.button("Inspect full quote provenance","button-secondary",()=>app.openArtifact(relative));node.type="button";source.append(node);}catch{}
      const controls=e("div","options-actions");controls.append(button(!data.feed.config.initialized?"Enable free AAPL feed":data.feed.config.enabled?"Pause free AAPL feed":"Resume free AAPL feed",configureFeed,runnable),button("Capture available AAPL quotes",()=>mutate("collect-feed"),()=>runnable()&&data.feed.config.enabled===true&&data.feed.collection_window_open===true));source.append(controls);p(source,"Personal, private research only. The anonymous vendor service is revocable; there is no established uptime guarantee or unlimited request quota. One bounded scheduled request per source session, with no automatic retry.");root.append(source);
    }
    if(data.hold){const held=e("div","options-warning");p(held,`HELD · ${txt(data.hold.request_id)} · ${txt(data.hold.reason)}`);held.append(button("Acknowledge inspected operation",acknowledge,()=>identity.test(txt(data.hold.request_id)),true));root.append(held);}
    const grid=e("div","options-grid"),paper=e("section","options-card"),summary=data.paper?.summary;
    if(data.paper_readiness){
      const readiness=data.paper_readiness,risk=readiness.shared_risk||{},inventory=e("section","options-card options-full");
      inventory.append(e("span","eyebrow","RESEARCH COVERAGE / FORWARD AVAILABILITY"),e("h3","","One account. Explicit source coverage."));
      p(inventory,`Configured paper roots: ${(Array.isArray(readiness.configured_active_paper_roots)?readiness.configured_active_paper_roots:[]).slice(0,16).join(", ")||"none"}. One shared monthly commitment cap ${amount(risk.monthly_gross_commitment_cap)}, entry ceiling ${amount(risk.all_in_entry_cap)}; remaining ${amount(risk.monthly_remaining)}.`);
      p(inventory,"Historical coverage and saved prices describe research inputs. Each additional paper root needs a qualified current quote source and a reviewed same-account workflow. Current prices and entry eligibility are not inferred from historical tests.");
      if(readiness.diagnostic_status==="unavailable")p(inventory,`Optional inventory unavailable: ${txt(readiness.input_error)}. Existing financial controls remain in force.`,"options-warning");
      const cards=e("div","options-comparison");
      for(const row of (Array.isArray(readiness.roots)?readiness.roots:[]).slice(0,16)){
        const card=e("div","options-strategy");card.append(e("strong","",txt(row.symbol)));
        p(card,row.configured_active_paper_root===true?`Existing experimental ${txt(row.symbol)} paper source; full runtime and causal entry checks required.`:"Additional active paper root unavailable / no qualified current options source.");
        for(const window of (Array.isArray(row.declared_windows)?row.declared_windows:[]).slice(0,4))p(card,`${txt(window.start)} to ${txt(window.end)} · ${Number.isSafeInteger(window.sessions)?window.sessions:"unknown"} declared sessions.`);
        p(card,`${txt(row.current_price_status)||"current_price_unmeasured"}. Separate windows do not establish continuous history. Catalog pins record declared evidence identity.`);
        if(row.dated_spot)p(card,`Saved spot ${amount(row.dated_spot.price)} on ${txt(row.dated_spot.source_date)}; this is not current eligibility.`);
        cards.append(card);
      }
      inventory.append(cards);p(inventory,"This inventory cannot select, fund, activate or place a paper trade. Quote-event clocks, executable sizes, prices and contract terms still require evidence.","options-warning");grid.append(inventory);
    }
    paper.append(e("span","eyebrow","FORWARD / SIMULATED CASH"),e("h3","","Options paper account"));
    if(summary?.symbol)p(paper,`Instrument: ${txt(summary.symbol)}. The research comparisons below do not change this account's strategy or cash.`);
    if(summary){const metrics=e("div","options-metrics");
      for(const[label,key]of [["Equity","equity"],["Trading P/L","trading_pnl"],["External simulated funding","contributions"],["Monthly net P/L","monthly_pnl"],["Monthly risk committed","monthly_budget_used"],["Monthly risk remaining","monthly_budget_remaining"]])metric(metrics,label,amount(summary[key]));paper.append(metrics);
      p(paper,`Ledger month ${txt(summary.month)} · entries ${Number.isInteger(summary.trade_count)?summary.trade_count:"unknown"} · completed trades ${Number.isInteger(summary.closed_trade_count)?summary.closed_trade_count:"unknown"}. Deposits are excluded from trading P/L.`);
      const capKnown=summary.cap_qualified===true&&summary.cap_assurance!=="unverified_missing_disposal_source";
      p(paper,capKnown?"Monthly cap: within the modeled premium and fee budget.":"CAP ASSURANCE UNAVAILABLE. Missing disposal or ledger evidence blocks new risk.",capKnown?"options-small":"options-warning");
      if(summary.hold)p(paper,JSON.stringify(summary.hold).slice(0,1800),"options-warning");
      p(paper,summary.position?`Open contract: ${txt(summary.position.contract_id)} · last bid mark ${amount(summary.position.marked_bid)} per share.`:"No open option position.");
      p(paper,summary.pending?`Pending paper decision: ${JSON.stringify(summary.pending).slice(0,1200)}`:"No pending paper decision. Cash is a valid outcome.");
      p(paper,summary.last_daily_result?`Latest dated result: ${JSON.stringify(summary.last_daily_result).slice(0,2200)}`:"No forward daily trading result yet. Starting cash is not a return.");
      if([2,3,4,6].includes(summary.schema)){
        p(paper,`ONE shared $500 allowance for all enabled strategies. One open position total. Next selector priority: ${strategyNames[summary.next_strategy]||txt(summary.next_strategy)}.`);
        const statsRows=[4,5,6].includes(summary.schema)?{[summary.strategy_id]:summary.policy_stats}:summary.strategy_stats||{};
        for(const[id,stats]of Object.entries(statsRows).slice(0,3))if(stats)p(paper,`${strategyNames[id]||id}: ${Number.isInteger(stats.entry_count)?stats.entry_count:"unknown"} entries / ${Number.isInteger(stats.closed_count)?stats.closed_count:"unknown"} closed · realized P/L ${amount(stats.realized_pnl)} · month committed ${amount(stats.monthly_committed)}.`);
      }else if(data.paper.state?.schema===1&&data.portfolio_rule){
        p(paper,"This is the retained frozen recovery-call account. A pristine account can upgrade in place; its existing cash, month and allowance stay intact.");
        paper.append(button("Upgrade pristine account to shared portfolio",upgradePortfolio,runnable));
      }
    }else{p(paper,"Start with $500 simulated cash. Later observed calendar months add $500 of simulated external funding only after the previous month is verified flat. This does not spend your money.");paper.append(button("Start $500 monthly paper experiment",startPaper,runnable));}
    if([3,4].includes(summary?.schema)){p(paper,"EXPERIMENTAL EOD PROXY · source dates are not quote-event timestamps; no execution qualification.","options-warning");if(summary.disposal)p(paper,`Disposal source ${txt(summary.disposal.required_source_date)} · ${txt(summary.disposal.status)}. Missing evidence is not a sale.`);if(summary.cap_assurance==="unverified_missing_disposal_source")p(paper,"CAP ASSURANCE UNVERIFIED · required disposal source evidence is missing.","options-warning");}
    p(paper,eodActive()?`The 6 AM Chicago brief reports dated saved ${txt(summary?.symbol)||"selected"} EOD experimental results. No paid options subscription, brokerage or real orders.`:data.feed?.config?.initialized?"The public source is collected after the market opens. The 6 AM Chicago brief reports the latest saved results. No paid options subscription or broker is connected.":"The 6 AM Chicago brief reads the fixed local quote inbox. There is no connected options subscription. Offline reads and backtests work; absent or stale quotes never become invented fills.");grid.append(paper);
    if(data.portfolio_rule||eodActive()){
      const stressMode=[4,5,6].includes(summary?.schema);
      const tracks=e("section","options-card");tracks.append(e("span","eyebrow","REGISTERED / NO WINNER CHASING"),e("h3","",stressMode?"Stress puts. One risk ledger.":"Three hypotheses. One risk ledger."));
      const hypotheses=stressMode?{[summary.strategy_id]:{signal:data.eod?.stress?.rule?.entry||"Fixed breakdown-put signal plus fresh stress context."}}:(eodActive()?data.eod?.rule:data.portfolio_rule)?.strategies||{};
      for(const[id,value]of Object.entries(hypotheses).slice(0,3)){
        const card=e("div","options-strategy");card.append(e("strong","",strategyNames[id]||id));p(card,txt(value.signal));tracks.append(card);
      }
      p(tracks,bankrollMode()?"The fixed stress-put policy controls new entries. Backtests never choose the next trade. One $25,000 total bankroll, no top-ups; each entry is at most $1,000 / 4% of equity including both per-contract fees. All symbols share one monthly gross commitment ceiling.":`${stressMode?"The fixed stress-put policy controls new entries.":"Fixed priority rotates after an entry."} Backtest performance never chooses the next strategy. Each entry costs at most $${eodActive()?"100":"125"} including premium and both fees. Profits cannot replenish the $500 monthly commitment allowance.`);grid.append(tracks);
      if(!eodActive()){
      const study=e("section","options-card options-full");study.append(e("span","eyebrow","SAME SOURCE / SEPARATE COUNTERFACTUALS"),e("h3","","Expanded strategy comparison"));
      if(data.archive_catalog){coverage(study,data.archive_catalog.quality,"Registered for next run");p(study,`Registered manifest for the next study: ${txt(data.archive_catalog.manifest_sha256)}. Price hashes establish identity, not accuracy.`);}
      else p(study,"Register a complete immutable historical quote archive. Every chunk is verified before use; gaps, changed terms and missing exits are never repaired into profits.");
      if(data.comparison){
        const accepted=data.comparison.coverage||{};
        p(study,`Saved comparison input: ${txt(accepted.first_date)||"unknown start"} to ${txt(accepted.last_source_date)||"unknown end"} · ${Number.isInteger(accepted.source_snapshots)?accepted.source_snapshots:"unknown"} source sessions. Accepted manifest SHA-256 ${txt(data.comparison.manifest_sha256)||"not recorded"}.`);
        if(data.archive_catalog&&data.archive_catalog.manifest_sha256!==data.comparison.manifest_sha256)p(study,"The registered archive changed. This saved comparison has not been rerun on it; its results and artifact links retain the input identified above.","options-warning");
        const results=e("div","options-comparison");
        for(const[id,result]of [["shared_portfolio",data.comparison],...Object.entries(data.comparison.strategy_results||{}).slice(0,3)]){
          const card=e("div","options-strategy"),m=result.metrics||{},c=result.coverage||{};
          card.append(e("strong","",id==="shared_portfolio"?"Shared $500 portfolio":`${strategyNames[id]||id} / separate counterfactual`));
          p(card,`P/L excluding deposits ${amount(m.trading_pnl)} · ${Number.isInteger(m.closed_trade_count)?m.closed_trade_count:"unknown"} closed / ${Number.isInteger(m.trade_count)?m.trade_count:"unknown"} entries.`);
          p(card,`${c.complete===true?"Complete input processed":"INCOMPLETE / last dated mark"}: ${Number.isInteger(c.processed_snapshots)?c.processed_snapshots:"unknown"} of ${Number.isInteger(c.source_snapshots)?c.source_snapshots:"unknown"} sessions · through ${txt(c.last_processed_date)||"unknown"}. Gate: ${txt(result.research_gate)||"not qualified"}.`);
          p(card,`Realized payout milestones ${JSON.stringify(m.payout_milestones||{})}.`);results.append(card);
        }
        study.append(results);p(study,"Counterfactual accounts each have their own simulated $500 allowance for comparison. Their P/L is never added together. Missing-exit ledgers stop at their last dated observation; these are not full-window realized returns. No result qualifies real-money execution.","options-warning");artifactButtons(study,data.comparison.artifacts);
      }
      const form=e("form"),label=e("label","options-field","Archive manifest inside Ariadne Workspace"),input=e("input");input.name="options_manifest_path";input.value=manifestDraft;input.maxLength=1000;input.addEventListener("input",()=>{manifestDraft=input.value;});label.append(input);fields.push(input);form.append(label);
      const submit=e("button","button button-secondary","Register historical archive");submit.type="submit";actions.push({node:submit,allowed:runnable,held:false});form.append(submit);
      form.addEventListener("submit",async event=>{event.preventDefault();if(runnable())try{await mutate("register-archive",{path:manifestDraft});}catch(error){notice=error.message;render();}});study.append(form,button("Compare registered strategies",()=>mutate("compare"),()=>runnable()&&Boolean(data.archive_catalog)));grid.append(study);
      const research=e("section","options-card options-full");research.append(e("span","eyebrow","FROZEN HYPOTHESES / RESEARCH ONLY"),e("h3","","Regime-filtered strategy research"));
      p(research,"Three 51-close hypotheses add a 20/50-session trend regime to recovery calls, 20-session breakout calls and breakdown puts. The same $125 entry ceiling, fees and shared $500 monthly research allowance apply. These research accounts cannot trade or adopt your paper funds.");
      p(research,"Individual strategy ledgers are separate $500 counterfactuals. Their budgets and returns are never added together, and no winning result is automatically selected.");
      if(data.research){
        const cards=e("div","options-comparison");
        for(const[id,value]of [["shared",data.research],["baseline",data.research.baseline||{}],...Object.entries(data.research.strategy_results||{}).slice(0,3)]){
          const card=e("div","options-strategy"),m=value.metrics||{},c=value.coverage||{};card.append(e("strong","",id==="shared"?"New shared research portfolio":id==="baseline"?"Original frozen portfolio / same input":`${strategyNames[id]||id} / separate counterfactual`));p(card,`P/L excluding deposits ${amount(m.trading_pnl)} · closed ${Number.isInteger(m.closed_trade_count)?m.closed_trade_count:"unknown"} · ${c.complete===true?"complete":"INCOMPLETE / last dated mark"} ${Number.isInteger(c.processed_snapshots)?c.processed_snapshots:"unknown"}/${Number.isInteger(c.source_snapshots)?c.source_snapshots:"unknown"} sessions through ${txt(c.last_processed_date)||"unknown"}. Gate ${txt(value.research_gate)||"not qualified"}.`);cards.append(card);
        }
        research.append(cards);const diagnostics=e("details");diagnostics.append(e("summary","","Eligibility, affordability and skipped candidates"),e("pre","",JSON.stringify(data.research.eligibility||{},null,2)));research.append(diagnostics);artifactButtons(research,data.research.artifacts);
      }
      p(research,"Previously examined years are exploratory. New retrospective windows are not prospective proof. Comparing partial ledgers with different stop dates cannot establish superiority, and zero trades is not a winning return.","options-warning");research.append(button("Backtest regime hypotheses",()=>mutate("research-compare"),()=>runnable()&&Boolean(data.archive_catalog)));grid.append(research);
      }
    }
    if(data.vix){
      const vix=e("section","options-card options-full");vix.append(e("span","eyebrow","VIX / VOLATILITY RESEARCH"),e("h3","","Study the shock. Verify the contract."));
      p(vix,"VIX/VIXW index options settle in cash using the VRO special opening quotation. European exercise, multiplier 100. Spot VIX, VX futures and volatility ETF options are different instruments.");
      p(vix,"VIX forward execution DISABLED · waiting for qualified quotes. No VIX paper fills, forward paper P/L, funded account or extra $500 allowance.","options-warning");
      const connection=data.vix.connection_readiness;
      if(connection){
        const source=e("div","options-strategy");source.append(e("strong","","Robinhood / existing-account data route"));
        p(source,"Robinhood publishes an official agent connection with options-chain, instrument and real-time quote tools. It requires authentication. This is a candidate data route; no live VIX response has been qualified by this release.");
        p(source,`Source review ${txt(connection.checked_at)||"not recorded"} · ${txt(connection.status)||"unverified"}. Saved research metadata, not a live connection check.`);
        p(source,"Your existing account can be used for authorized read-only inspection. Sign in directly at Robinhood. Never paste passwords, session cookies or access tokens into Ariadne, a quote file or a chat. Opening an MCP account or granting broader access requires owner action.");
        const links=e("div","options-actions");
        for(const[label,url]of [["Robinhood connection instructions","https://robinhood.com/us/en/support/articles/onboarding-an-external-agent/"],["Robinhood market-data tools","https://robinhood.com/us/en/support/articles/trading-with-your-agent/"],["Yahoo VIX chain","https://finance.yahoo.com/quote/%5EVIX/options/"],["TradingView VIX chain","https://www.tradingview.com/symbols/CBOE-VIX/options-chain/"]]){
          const link=e("a","button button-secondary",label);link.href=url;link.target="_blank";link.rel="noopener noreferrer";links.append(link);
        }
        source.append(links);
        p(source,"Yahoo and TradingView chain pages are viewing tools, not a verified free automated quote feed. TradingView's official MCP requires a paid plan and its published tools do not supply an options chain. No private endpoints are scraped.");
        for(const gate of (Array.isArray(connection.gates)?connection.gates:[]).slice(0,8))p(source,`${txt(gate.id)} · ${txt(gate.status)||"unverified"}: ${txt(gate.detail)}`);
        p(source,"A connection alone cannot unlock fills. Actual VIX bid/ask, sizes and quote clocks, matching-expiry VX references, an official calendar and a qualified single shared ledger must all pass. No orders or account-management tools are added.");
        vix.append(source);
      }else p(vix,"The saved source review has not qualified a permitted free no-account option feed. Inspect current provider access before importing quotes.");
      p(vix,`The existing ${txt(data.paper?.summary?.symbol)||"options"} account holds the only shared monthly allowance. Remaining ${amount(data.vix.shared_monthly_remaining)}; ${data.vix.shared_cap_qualified===true?"cap within modeled assumptions":"cap assurance unavailable"}. Historical research cannot change its cash, strategies, positions or approvals.`);
      p(vix,"Three fixed hypotheses: shock breakout calls, elevated-volatility reversal puts, and short-horizon stress calls. Option research requires actual bid/ask, side sizes, exact-expiry VX reference and an official contract calendar. These hypotheses have no established profitable edge.");
      for(const [key,title]of [["signals","Official index / signal diagnostics"],["backtest","Actual option-quote backtest"]]){
        const saved=data.vix[key],card=e("div","options-strategy");card.append(e("strong","",title));
        if(saved){
          const s=saved.summary||{},c=saved.coverage||{};p(card,`Saved ${txt(saved.created_at)} · gate ${txt(saved.research_gate)||"research only"}.`);
          if(key==="signals"){
            p(card,`Source session ${txt(s.source_date)||"unknown"} · VIX ${txt(s.index_close)||"unavailable"} · VIX9D ${txt(s.vix9d_close)||"unavailable"}. Index levels, not option prices.`);
            const counts=s.signal_counts||{},count=id=>Number.isSafeInteger(counts[id])?counts[id]:"unmeasured";
            p(card,`Historical signals: shock ${count("vix_shock_breakout_call_v1")} · fade ${count("vix_spike_fade_put_v1")} · stress ${count("vix_short_term_stress_call_v1")}. Counts are not win rates.`);
            const conditions=Object.values(s.latest_conditions||{});p(card,conditions.length===3&&conditions.every(value=>value===false)?"Latest observed session: no registered condition triggered.":"Latest condition details and blocked inputs are retained in the report.");
            p(card,`Observed regular sessions ${Number.isSafeInteger(c.observed_regular_sessions)?c.observed_regular_sessions:"unknown"} · missing ${Number.isSafeInteger(c.missing_vix_session_count)?c.missing_vix_session_count:"unknown"}. Historical publication times are unknown.`);
          }else{
            p(card,`Hypothetical shared research P/L: realized ${amount(s.realized_pnl)}, unrealized ${amount(s.unrealized_pnl)} · closed trades ${Number.isSafeInteger(s.closed_trades)?s.closed_trades:"unknown"}. No active funds or forward fills.`);
          }
          const details=e("details");details.append(e("summary","","Coverage and research assumptions"),e("pre","",JSON.stringify({summary:s,coverage:c},null,2)));card.append(details);artifactButtons(card,saved.artifacts);
        }
        else p(card,key==="signals"?"No index study saved. Import official local CSV history to inspect fixed signal frequency and subsequent index changes.":"No qualifying VIX option history imported. There is no measured option strategy return to display.");
        vix.append(card);
      }
      p(vix,"Index changes are signal diagnostics, never tradable option returns or win probabilities. Saved source dates remain historical while offline; this release makes no automatic VIX network requests. Original source files stay private and are excluded from GitHub and portable source exports.");
      p(vix,`Saved index source date ${txt(data.vix.index_freshness?.source_date)||"unavailable"} · ${data.vix.index_freshness?.stale===false?"matches latest reviewed regular session":"STALE / missing latest reviewed regular session"}. Date-only observations have no verified option execution timestamp.`);
      for(const [kind,name,label]of [["vix-signals","vix_signal_path","Study official index history"],["vix-backtest","vix_quote_path","Backtest VIX option quotes"]]){
        const form=e("form"),field=e("label","options-field",kind==="vix-signals"?"Index-history manifest inside Ariadne Workspace":"VIX option-history JSON inside Ariadne Workspace"),input=e("input");input.name=name;input.maxLength=1000;input.value=kind==="vix-signals"?vixSignalDraft:vixQuoteDraft;
        input.addEventListener("input",()=>{if(kind==="vix-signals")vixSignalDraft=input.value;else vixQuoteDraft=input.value;});field.append(input);fields.push(input);form.append(field,button(label,()=>mutate(kind,{path:kind==="vix-signals"?vixSignalDraft:vixQuoteDraft}),runnable));
        form.addEventListener("submit",async event=>{event.preventDefault();if(!runnable())return;await mutate(kind,{path:kind==="vix-signals"?vixSignalDraft:vixQuoteDraft});});vix.append(form);
      }
      const detail=e("details");detail.append(e("summary","","Data-source assessment and execution gates"));for(const reason of (data.vix.readiness||[]).slice(0,8))p(detail,txt(reason));p(detail,`Source assessment checked ${txt(data.vix.source_assessment?.checked_at)||"unknown"}; availability can change.`);vix.append(detail);grid.append(vix);
    }
    const history=e("section","options-card");history.append(e("span","eyebrow","HISTORICAL / QUOTE-BASED RESEARCH"),e("h3","","Backtest evidence"));
    if(data.backtest){const report=data.backtest,metrics=report.metrics||{};
      p(history,`Research gate: ${txt(report.research_gate)||"not qualified"}. Historical fills are assumptions; this result does not qualify real-money execution.`,"options-warning");
      const values=e("div","options-metrics");for(const[label,key]of [["Trading P/L excluding deposits","trading_pnl"],["Ending equity","ending_equity"],["External simulated contributions","total_contributions"],["Maximum modeled monthly loss","maximum_monthly_loss"]])metric(values,label,amount(metrics[key]));history.append(values);
      p(history,`Symbol ${txt(report.symbol)} · entries ${Number.isInteger(metrics.trade_count)?metrics.trade_count:"unknown"} · completed trades ${Number.isInteger(metrics.closed_trade_count)?metrics.closed_trade_count:"unknown"}.`);
      p(history,`Realized payout milestones: ${JSON.stringify(metrics.payout_milestones||{})}. A large possible payoff is not an estimated win probability.`);artifactButtons(history,report.artifacts);
    }else p(history,"Import historical option bid/ask quotes, then run the fixed rule. Underlying stock bars and theoretical option prices are not substituted for missing contract history.");
    coverage(history,data.quality,eodActive()?"Retained legacy historical":"Historical");if(!eodActive())coverage(history,data.forward_quality,"Forward");grid.append(history);
    if(!eodActive()){
    const importer=e("section","options-card options-full");importer.append(e("h3","","Import historical quotes"));
    p(importer,"Use the bundled converter for Cboe CSV or the strict canonical JSON format. Imports stay local and do not advance the forward account. Data from undocumented sources remains explicitly unverified.");
    const form=e("form"),label=e("label","options-field","JSON file inside Ariadne Workspace"),input=e("input");input.name="options_import_path";input.value=importDraft;input.maxLength=1000;input.addEventListener("input",()=>{importDraft=input.value;});label.append(input);fields.push(input);form.append(label);
    const submit=e("button","button button-secondary","Import historical dataset");submit.type="submit";actions.push({node:submit,allowed:runnable,held:false});form.append(submit);
    form.addEventListener("submit",async event=>{event.preventDefault();if(!runnable())return;try{await mutate("import",{path:importDraft});}catch(error){notice=error.message;render();}});importer.append(form);p(importer,`Daily forward inbox: ${txt(data.inbox)}. Forward observations need recorded timestamps, quoted size and the latest completed session. Assumed-EOD research cannot place paper fills.`);grid.append(importer);
    }
    const rule=e("details","options-card options-full");rule.append(e("summary","","Frozen selection rule and monthly risk contract"));for(const[key,value]of Object.entries((eodActive()?data.eod?.rule:data.portfolio_rule||data.rule)||{}).slice(0,30))p(rule,`${key}: ${typeof value==="string"?value:JSON.stringify(value)}`);p(rule,eodActive()?`Experimental EOD rule SHA-256 ${txt(data.eod?.rule_sha256)}`:`Portfolio rule SHA-256 ${txt(data.portfolio_rule_sha256)} / original recovery rule ${txt(data.rule_sha256)}`);grid.append(rule);
    if(!eodActive()&&(data.paper||data.quality||data.forward_quality)){const archive=e("details","options-card options-full");archive.append(e("summary","","Archive an experiment or input basis"));p(archive,"Retains the ledger and last marks without inventing a sale. Archiving never resets a month's allowance. An unresolved archived position blocks a new experiment.");
      const reasonLabel=e("label","options-field","Reason"),reason=e("textarea");reason.name="options_archive_reason";reason.maxLength=1000;reason.value=reasonDraft;reason.addEventListener("input",()=>{reasonDraft=reason.value;});reasonLabel.append(reason);fields.push(reason);archive.append(reasonLabel);
      const checkLabel=e("label","options-field"),check=e("input");check.type="checkbox";check.checked=archiveData;check.name="options_archive_data";check.addEventListener("change",()=>{archiveData=check.checked;});checkLabel.append(check,e("span","","Also retain and clear both imported data caches"));fields.push(check);archive.append(checkLabel,button("Review archival",archiveExperiment));grid.append(archive);}
    if(Array.isArray(data.events)&&data.events.length){const events=e("details","options-card options-full");events.append(e("summary","","Recent paper events"));const pre=e("pre","",JSON.stringify(data.events.slice(0,20),null,2));events.append(pre);grid.append(events);}
    root.append(grid);if(data.last_run)p(root,`Last request ${txt(data.last_run.request_id)} · ${txt(data.last_run.status)} · ${txt(data.last_run.outcome)}.`);controls();
  }
  async function load(){
    if(!app?.state.authenticated||busy||loading)return;loading=true;controls();const marker=generation,token=app.state.token;
    try{const result=await app.api("/options");if(!current(marker,token))return;
      if(!Number.isSafeInteger(result?.revision)||result.revision<0||typeof result.config?.enabled!=="boolean"||typeof result.config?.paused!=="boolean")throw new Error("Saved options status is incomplete. No action is permitted.");
      if(result.feed&&(typeof result.feed.config?.enabled!=="boolean"||typeof result.feed.config?.initialized!=="boolean"||typeof result.feed.collection_window_open!=="boolean"))throw new Error("Saved quote-source controls are incomplete. Inspect before another action.");
      if(result.eod&&(typeof result.eod.config?.enabled!=="boolean"||typeof result.eod.config?.initialized!=="boolean"||typeof result.eod.collection_window_open!=="boolean"||typeof result.eod.migration_eligible!=="boolean"))throw new Error("Saved experimental EOD controls are incomplete. Inspect before another action.");
      if(([3,4,5,6].includes(result.paper?.state?.schema)||[3,4,5,6].includes(result.paper?.summary?.schema))&&result.eod?.config?.initialized!==true)throw new Error("The experimental EOD account lacks its matching source authority. No action is permitted.");
      if(result.eod?.config?.initialized===true&&(![3,4,5,6].includes(result.paper?.state?.schema)||result.paper?.summary?.schema!==result.paper?.state?.schema||!result.eod.rule||typeof result.eod.rule_sha256!=="string"))throw new Error("The initialized EOD source lacks its matching account/rule. No action is permitted.");
      if([4,5,6].includes(result.paper?.state?.schema)&&(result.eod?.stress?.active!==true||result.eod.stress.rule_sha256!==result.paper?.summary?.rule_sha256))throw new Error("Stress policy authority is incomplete. Inspect saved records before another action.");
      if([5,6].includes(result.paper?.state?.schema)&&(result.paper.state.symbol!=="KO"||result.eod.config.symbol!=="KO"||result.eod.config.provider!=="onclick_ko_eod_v1"||result.eod.stress.policy_id!==(result.paper.state.schema===6?"ko_bankroll_stress_breakdown_put_v1":"ko_stress_breakdown_put_v1")))throw new Error("The KO account/source binding is incomplete. Inspect before another action.");
      data=result;stale=false;if(uncertain)inspected=true;render();
    }catch(error){if(current(marker,token)){notice=error.message;stale=true;if(data)render();else $("options-content")?.replaceChildren(e("p","options-warning",notice));}}
    finally{if(current(marker,token)){loading=false;controls();}}
  }
  async function mutate(kind,extra={},identifier=null){
    if(!writable(kind==="config"||kind==="acknowledge"))return;
    if(["refresh","backtest","import","paper","portfolio","upgrade-portfolio","register-archive","compare","feed","collect-feed","research-compare","vix-signals","vix-backtest","eod-migrate","eod-config","eod-collect","stress-activate","root-migrate","root-config","root-collect"].includes(kind)&&!runnable())return;
    if([5,6].includes(data.paper?.state?.schema)&&["eod-migrate","eod-config","eod-collect","stress-activate","root-migrate"].includes(kind))return;
    if(["root-config","root-collect"].includes(kind)&&![5,6].includes(data.paper?.state?.schema))return;
    if(kind==="stress-activate"&&data.eod?.stress?.migration_eligible!==true)return;
    if(eodActive()&&["refresh","feed","collect-feed","archive","paper","portfolio","upgrade-portfolio"].includes(kind))return;
    if(kind==="eod-migrate"&&(!data.eod||eodActive()||data.eod.migration_eligible!==true))return;
    if(["eod-config","root-config"].includes(kind)&&(!eodActive()||data.eod?.config?.initialized!==true))return;
    if(["eod-collect","root-collect"].includes(kind)&&(!eodActive()||data.eod?.config?.enabled!==true||data.eod.collection_window_open!==true))return;
    if(kind==="refresh"&&data.feed?.config?.initialized)return;
    if(kind==="collect-feed"&&(data.feed?.config?.enabled!==true||data.feed.collection_window_open!==true))return;
    if(["paper","portfolio"].includes(kind)&&data.paper)return;
    if(kind==="upgrade-portfolio"&&data.paper?.state?.schema!==1)return;
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
  async function startPaper(){if(!runnable()||data.paper)return;await confirmed("Start the options paper experiment?","Start $500 simulated cash with at most $500 committed premium/fees per month across ALL strategies, one position total. No profits recycle the allowance. No cross-month positions or real orders. A restart cannot reset this month's budget.","Start simulated experiment",()=>mutate(data.portfolio_rule?"portfolio":"paper"));}
  async function activateStress(){if(!runnable()||data.eod?.stress?.migration_eligible!==true)return;await confirmed("Focus this paper account on stress-filtered puts?","Version the same pristine account without adding cash or resetting its $500 monthly allowance. Only new stress-filtered F long puts may enter, up to $100 including fees. Context must meet actual publication and preopen rules. This is an experimental hypothesis, not a proven winner. Existing evidence and source holds remain.","Keep budget and focus on puts",()=>mutate("stress-activate"));}
  async function upgradePortfolio(){if(!runnable()||data.paper?.state?.schema!==1)return;await confirmed("Upgrade this pristine account in place?","Enable all three fixed strategies on the SAME account. Existing $500 simulated cash, contributions, ledger month and monthly allowance remain unchanged. No historical positions, new funding or reset. The server refuses any prior observed or traded account.","Preserve budget and upgrade",()=>mutate("upgrade-portfolio"));}
  async function configureFeed(){if(!runnable()||!data.feed||eodActive())return;const enabled=!data.feed.config.enabled;await confirmed(enabled?"Enable the free AAPL daily source?":"Pause the free AAPL source?",!data.feed.config.initialized?"Retarget the pristine QQQ paper portfolio to AAPL on the SAME account. Preserve cash, contributions and the shared $500 monthly allowance. Quotes are delayed EOD; capture starts at 9:35 AM Eastern. No paid service, account, broker or added funding.":"Change only collection permission. Cash, losses, committed risk, source history and existing holds remain. No replay of missed sessions or automatic failure retry.",enabled?"Enable AAPL source":"Pause collection",()=>mutate("feed",{enabled}));}
  async function migrateEOD(){if(!runnable()||!data.eod||eodActive()||data.eod.migration_eligible!==true)return;const body=request("eod-migrate",data.revision,newId(),{baseline_path:baselineDraft});await confirmed("Migrate this same shared account to F EOD?","Use the experimental partial date-only F chain with UNKNOWN quote-event clocks. Preserve the SAME account ID, cash, contributions, existing month and shared $500 allowance. NO new funding, risk reset or historical trade adoption. Underlying shares must be below $100; each entry is at most $100 including both fees. Optional stock seeds only warm indicators. This is a lower-evidence EOD price proxy, not execution qualification.","Preserve account and migrate",()=>mutate("eod-migrate",{baseline_path:body.baseline_path},body.request_id));}
  async function configureEOD(){if(!runnable()||!eodActive()||!data.eod)return;const enabled=!data.eod.config.enabled,symbol=txt(data.eod.config.symbol),kind=[5,6].includes(data.paper?.state?.schema)?"root-config":"eod-config";await confirmed(`${enabled?"Resume":"Pause"} experimental ${symbol} EOD collection?`,bankrollMode()?"Change only collection permission. Preserve the one-time $25,000 funding, existing cash, shared commitments and all evidence. No top-up, replay or automatic failure retry.":"Change only collection permission. Preserve the same cash, contributions, shared $500 commitments and all evidence. Partial date-only quotes remain unqualified price proxies. No new funding, missed-session replay or automatic failure retry.",`${enabled?"Resume":"Pause"} ${symbol} EOD collection`,()=>mutate(kind,{enabled}));}
  async function acknowledge(){if(!writable(true)||!data.hold)return;const identifier=data.hold.request_id;await confirmed("Acknowledge inspected options work?","The failed request stays closed. Partial inputs, fills and files are not adopted, deleted or replayed.","Acknowledge inspected request",()=>mutate("acknowledge",{},identifier));}
  async function archiveExperiment(){if(!writable()||eodActive())return;const body=request("archive",data.revision,newId(),{reason:reasonDraft,archive_data:archiveData});await confirmed("Archive this options experiment?",`${body.reason}\n\nLast marks, history and losses remain retained. No synthetic sell. This cannot replenish a monthly allowance.${body.archive_data?" Both data caches are retained in evidence before clearing.":" Imported data stays cached."}`,"Retain evidence and archive",()=>mutate("archive",{reason:body.reason,archive_data:body.archive_data},body.request_id));}
  function status(){if(data&&lastPause!==Boolean(app?.state.status?.paused))render();else controls();}
  function locked(){generation++;data=null;busy=false;loading=false;stale=false;notice="";uncertain=null;inspected=false;actions=[];fields=[];lastPause=null;importDraft="market-data/options/current.json";manifestDraft="market-data/options/archives/qqq-2022/catalog.json";vixSignalDraft="market-data/vix/index-history.json";vixQuoteDraft="market-data/vix/option-history.json";baselineDraft="";reasonDraft="";archiveData=false;$("options-content")?.replaceChildren();controls();}
  function attach(value){app=value;const root=$("options-content");if(!root||attached===root)return;attached=root;$("refresh-options")?.addEventListener("click",load);controls();}
  window.ariadneOptions={attach,load,status,locked,request,path,amount,mutate,startPaper,upgradePortfolio,configureFeed,migrateEOD,configureEOD,activateStress,acknowledge,archiveExperiment};
})();
