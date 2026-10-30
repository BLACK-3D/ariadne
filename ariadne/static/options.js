"use strict";

// Owner actions only. Viewing this panel never imports data or advances paper.
(() => {
  let app, attached, data=null, generation=0, busy=false, loading=false, stale=false;
  let notice="", uncertain=null, inspected=false, actions=[], fields=[], lastPause=null;
  let importDraft="market-data/options/current.json", manifestDraft="market-data/options/archives/qqq-2022/catalog.json", reasonDraft="", archiveData=false;
  let vixSignalDraft="market-data/vix/index-history.json",vixQuoteDraft="market-data/vix/option-history.json";
  const strategyNames={recovery_call_v1:"Recovery / calls",breakout_call_v1:"Breakout / calls",breakdown_put_v1:"Breakdown / puts",regime_recovery_call_v1:"Trend-filtered recovery / calls",trend_breakout_call_v1:"Trend breakout / calls",trend_breakdown_put_v1:"Trend breakdown / puts"};
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
    if(!["refresh","backtest","compare","collect-feed","research-compare","acknowledge"].includes(kind))throw new Error("Unsupported options action.");
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
      const relative=path(item.path),label=relative.endsWith("vix-report.json")?"Inspect VIX research":relative.endsWith("vix-input.json")?"Inspect VIX input":relative.endsWith("/VIX.csv")?"Inspect private VIX history":relative.endsWith("/VIX9D.csv")?"Inspect private VIX9D history":relative.endsWith("accepted-input.json")?"Inspect historical input":relative.endsWith("research-manifest.json")?"Inspect research archive manifest":relative.endsWith("research.json")?"Inspect regime research":relative.endsWith("archive-manifest.json")?"Inspect accepted archive manifest":relative.endsWith("comparison.json")?"Inspect strategy comparison":"Inspect full research report",node=app.button(label,"button-secondary",()=>app.openArtifact(relative));node.type="button";row.append(node);
    }catch{}
    parent.append(row);
  }
  function render(){
    const root=$("options-content");if(!root||!data||!app?.state.authenticated)return;
    root.replaceChildren();actions=[];fields=[];lastPause=Boolean(app.state.status?.paused);
    const hero=e("section","options-hero"),copy=e("div"),limit=e("div","options-limit");
    copy.append(e("span","eyebrow","OPTIONS / ASYMMETRIC RESEARCH"),e("h2","","Small stake. Measured upside."));
    p(copy,"Three fixed call/put hypotheses. One shared paper portfolio. Actual option quotes, auditable decisions, zero model calls.");
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
    row.append(button("Read forward quote inbox",()=>mutate("refresh"),()=>runnable()&&!data.feed?.config?.initialized),button("Backtest imported history",()=>mutate("backtest"),()=>runnable()&&Boolean(data.quality)));root.append(row);
    if(data.feed){
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
    paper.append(e("span","eyebrow","FORWARD / SIMULATED CASH"),e("h3","","Options paper account"));
    if(summary?.symbol)p(paper,`Instrument: ${txt(summary.symbol)}. The research comparisons below do not change this account's strategy or cash.`);
    if(summary){const metrics=e("div","options-metrics");
      for(const[label,key]of [["Equity","equity"],["Trading P/L","trading_pnl"],["External simulated funding","contributions"],["Monthly net P/L","monthly_pnl"],["Monthly risk committed","monthly_budget_used"],["Monthly risk remaining","monthly_budget_remaining"]])metric(metrics,label,amount(summary[key]));paper.append(metrics);
      p(paper,`Ledger month ${txt(summary.month)} · entries ${Number.isInteger(summary.trade_count)?summary.trade_count:"unknown"} · completed trades ${Number.isInteger(summary.closed_trade_count)?summary.closed_trade_count:"unknown"}. Deposits are excluded from trading P/L.`);
      p(paper,summary.cap_qualified?"Monthly cap: within the modeled premium and fee budget.":"CAP ASSURANCE UNAVAILABLE. Missing disposal or ledger evidence blocks new risk.",summary.cap_qualified?"options-small":"options-warning");
      if(summary.hold)p(paper,JSON.stringify(summary.hold).slice(0,1800),"options-warning");
      p(paper,summary.position?`Open contract: ${txt(summary.position.contract_id)} · last bid mark ${amount(summary.position.marked_bid)} per share.`:"No open option position.");
      p(paper,summary.pending?`Pending paper decision: ${JSON.stringify(summary.pending).slice(0,1200)}`:"No pending paper decision. Cash is a valid outcome.");
      p(paper,summary.last_daily_result?`Latest dated result: ${JSON.stringify(summary.last_daily_result).slice(0,2200)}`:"No forward daily trading result yet. Starting cash is not a return.");
      if(summary.schema===2){
        p(paper,`ONE shared $500 allowance for all enabled strategies. One open position total. Next selector priority: ${strategyNames[summary.next_strategy]||txt(summary.next_strategy)}.`);
        for(const[id,stats]of Object.entries(summary.strategy_stats||{}).slice(0,3))p(paper,`${strategyNames[id]||id}: ${Number.isInteger(stats.entry_count)?stats.entry_count:"unknown"} entries / ${Number.isInteger(stats.closed_count)?stats.closed_count:"unknown"} closed · realized P/L ${amount(stats.realized_pnl)} · month committed ${amount(stats.monthly_committed)}.`);
      }else if(data.paper.state?.schema===1&&data.portfolio_rule){
        p(paper,"This is the retained frozen recovery-call account. A pristine account can upgrade in place; its existing cash, month and allowance stay intact.");
        paper.append(button("Upgrade pristine account to shared portfolio",upgradePortfolio,runnable));
      }
    }else{p(paper,"Start with $500 simulated cash. Later observed calendar months add $500 of simulated external funding only after the previous month is verified flat. This does not spend your money.");paper.append(button("Start $500 monthly paper experiment",startPaper,runnable));}
    p(paper,data.feed?.config?.initialized?"The public source is collected after the market opens. The 6 AM Chicago brief reports the latest saved results. No paid options subscription or broker is connected.":"The 6 AM Chicago brief reads the fixed local quote inbox. There is no connected options subscription. Offline reads and backtests work; absent or stale quotes never become invented fills.");grid.append(paper);
    if(data.portfolio_rule){
      const tracks=e("section","options-card");tracks.append(e("span","eyebrow","REGISTERED / NO WINNER CHASING"),e("h3","","Three hypotheses. One risk ledger."));
      for(const[id,value]of Object.entries(data.portfolio_rule.strategies||{}).slice(0,3)){
        const card=e("div","options-strategy");card.append(e("strong","",strategyNames[id]||id));p(card,txt(value.signal));tracks.append(card);
      }
      p(tracks,"Fixed priority rotates after an entry. Backtest performance never chooses the next strategy. Each entry costs at most $125 including premium and both fees. Profits cannot replenish the $500 monthly commitment allowance.");grid.append(tracks);
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
    if(data.vix){
      const vix=e("section","options-card options-full");vix.append(e("span","eyebrow","VIX / VOLATILITY RESEARCH"),e("h3","","Study the shock. Verify the contract."));
      p(vix,"VIX/VIXW index options settle in cash using the VRO special opening quotation. European exercise, multiplier 100. Spot VIX, VX futures and volatility ETF options are different instruments.");
      p(vix,"VIX forward execution DISABLED · waiting for qualified quotes. No permitted free no-account option feed has been established. No VIX paper fills, forward paper P/L, funded account or extra $500 allowance.","options-warning");
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
    coverage(history,data.quality,"Historical");coverage(history,data.forward_quality,"Forward");grid.append(history);
    const importer=e("section","options-card options-full");importer.append(e("h3","","Import historical quotes"));
    p(importer,"Use the bundled converter for Cboe CSV or the strict canonical JSON format. Imports stay local and do not advance the forward account. Data from undocumented sources remains explicitly unverified.");
    const form=e("form"),label=e("label","options-field","JSON file inside Ariadne Workspace"),input=e("input");input.name="options_import_path";input.value=importDraft;input.maxLength=1000;input.addEventListener("input",()=>{importDraft=input.value;});label.append(input);fields.push(input);form.append(label);
    const submit=e("button","button button-secondary","Import historical dataset");submit.type="submit";actions.push({node:submit,allowed:runnable,held:false});form.append(submit);
    form.addEventListener("submit",async event=>{event.preventDefault();if(!runnable())return;try{await mutate("import",{path:importDraft});}catch(error){notice=error.message;render();}});importer.append(form);p(importer,`Daily forward inbox: ${txt(data.inbox)}. Forward observations need recorded timestamps, quoted size and the latest completed session. Assumed-EOD research cannot place paper fills.`);grid.append(importer);
    const rule=e("details","options-card options-full");rule.append(e("summary","","Frozen selection rule and monthly risk contract"));for(const[key,value]of Object.entries(data.portfolio_rule||data.rule||{}).slice(0,30))p(rule,`${key}: ${typeof value==="string"?value:JSON.stringify(value)}`);p(rule,`Portfolio rule SHA-256 ${txt(data.portfolio_rule_sha256)} / original recovery rule ${txt(data.rule_sha256)}`);grid.append(rule);
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
      if(result.feed&&(typeof result.feed.config?.enabled!=="boolean"||typeof result.feed.config?.initialized!=="boolean"||typeof result.feed.collection_window_open!=="boolean"))throw new Error("Saved quote-source controls are incomplete. Inspect before another action.");
      data=result;stale=false;if(uncertain)inspected=true;render();
    }catch(error){if(current(marker,token)){notice=error.message;stale=true;if(data)render();else $("options-content")?.replaceChildren(e("p","options-warning",notice));}}
    finally{if(current(marker,token)){loading=false;controls();}}
  }
  async function mutate(kind,extra={},identifier=null){
    if(!writable(kind==="config"||kind==="acknowledge"))return;
    if(["refresh","backtest","import","paper","portfolio","upgrade-portfolio","register-archive","compare","feed","collect-feed","research-compare","vix-signals","vix-backtest"].includes(kind)&&!runnable())return;
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
  async function upgradePortfolio(){if(!runnable()||data.paper?.state?.schema!==1)return;await confirmed("Upgrade this pristine account in place?","Enable all three fixed strategies on the SAME account. Existing $500 simulated cash, contributions, ledger month and monthly allowance remain unchanged. No historical positions, new funding or reset. The server refuses any prior observed or traded account.","Preserve budget and upgrade",()=>mutate("upgrade-portfolio"));}
  async function configureFeed(){if(!runnable()||!data.feed)return;const enabled=!data.feed.config.enabled;await confirmed(enabled?"Enable the free AAPL daily source?":"Pause the free AAPL source?",!data.feed.config.initialized?"Retarget the pristine QQQ paper portfolio to AAPL on the SAME account. Preserve cash, contributions and the shared $500 monthly allowance. Quotes are delayed EOD; capture starts at 9:35 AM Eastern. No paid service, account, broker or added funding.":"Change only collection permission. Cash, losses, committed risk, source history and existing holds remain. No replay of missed sessions or automatic failure retry.",enabled?"Enable AAPL source":"Pause collection",()=>mutate("feed",{enabled}));}
  async function acknowledge(){if(!writable(true)||!data.hold)return;const identifier=data.hold.request_id;await confirmed("Acknowledge inspected options work?","The failed request stays closed. Partial inputs, fills and files are not adopted, deleted or replayed.","Acknowledge inspected request",()=>mutate("acknowledge",{},identifier));}
  async function archiveExperiment(){if(!writable())return;const body=request("archive",data.revision,newId(),{reason:reasonDraft,archive_data:archiveData});await confirmed("Archive this options experiment?",`${body.reason}\n\nLast marks, history and losses remain retained. No synthetic sell. This cannot replenish a monthly allowance.${body.archive_data?" Both data caches are retained in evidence before clearing.":" Imported data stays cached."}`,"Retain evidence and archive",()=>mutate("archive",{reason:body.reason,archive_data:body.archive_data},body.request_id));}
  function status(){if(data&&lastPause!==Boolean(app?.state.status?.paused))render();else controls();}
  function locked(){generation++;data=null;busy=false;loading=false;stale=false;notice="";uncertain=null;inspected=false;actions=[];fields=[];lastPause=null;importDraft="market-data/options/current.json";manifestDraft="market-data/options/archives/qqq-2022/catalog.json";vixSignalDraft="market-data/vix/index-history.json";vixQuoteDraft="market-data/vix/option-history.json";reasonDraft="";archiveData=false;$("options-content")?.replaceChildren();controls();}
  function attach(value){app=value;const root=$("options-content");if(!root||attached===root)return;attached=root;$("refresh-options")?.addEventListener("click",load);controls();}
  window.ariadneOptions={attach,load,status,locked,request,path,amount,mutate,startPaper,upgradePortfolio,configureFeed,acknowledge,archiveExperiment};
})();
