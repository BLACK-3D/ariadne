"use strict";
// Read-only analysis of accepted paper evidence. No collection or trade action.
(() => {
  let app, attached, data=null, loading=false, generation=0, visible=false, notice="";
  const $=id=>document.getElementById(id), text=value=>typeof value==="string"?value:"";
  const element=(tag,cls="",value)=>app.node(tag,cls,value);
  const active=()=>app?.state.authenticated&&(app.state.view===undefined||app.state.view==="venture");
  const current=(marker,token)=>marker===generation&&app?.state.authenticated&&app.state.token===token&&active();
  const money=value=>typeof value==="string"&&/^-?\d{1,12}(?:\.\d{1,12})?$/.test(value)?Number(value).toLocaleString("en-US",{style:"currency",currency:"USD"}):"Not observed";
  const count=value=>Number.isSafeInteger(value)&&value>=0?String(value):"Not observed";
  function paragraph(parent,value,cls="options-small"){parent.append(element("p",cls,value));}
  function validate(value){
    if(!value||typeof value!=="object"||value.research_only!==true||value.execution_qualified!==false||value.model_calls!==0||value.network_calls!==0||typeof value.status!=="string")throw new Error("The journal lacks its verified paper-research scope.");
    return value;
  }
  function render(){
    const root=$("options-review-content");if(!root||!app)return;root.replaceChildren();
    if(!app.state.authenticated)return;
    if(notice)paragraph(root,notice,"options-warning");
    if(!data){paragraph(root,loading?"Checking saved paper evidence…":"Inspect the journal to review saved source sessions and rule diagnostics.");return;}
    const card=element("article","options-card");
    card.append(element("span","eyebrow","PROSPECTIVE / PRICE PROXIES / NO WINNER RANKING"),element("h3","","What the paper evidence supports"));
    paragraph(card,`Status: ${text(data.status).replaceAll("_"," ")}. Date-only EOD prices and partial chains remain execution-unqualified. This read fetches no market data, adds no funding and changes no decisions.`);
    const source=data.source||{}, performance=data.performance||{}, holds=data.holds||{};
    if(source.source_date||source.last_source_date)paragraph(card,`Latest accepted source session: ${text(source.source_date||source.last_source_date)}. Actual receipt: ${text(source.actual_received_at||source.received_at||source.last_received_at)||"not available"}. Quote-event time remains unknown.`);
    if(data.status==="waiting_for_first_capture")paragraph(card,"No F option capture has been accepted yet. Stock warmup, starting cash and deposits are not trading results. The enabled source must complete a permitted session check before option diagnostics are available.","options-warning");
    if(holds.workflow||holds.paper||holds.global_paused)paragraph(card,"New paper work is held or paused. Inspect the saved receipt and controls; this review does not resume anything.","options-warning");
    const table=element("table","options-table"),header=element("tr");
    for(const label of ["Observed paper measure","Saved value"])header.append(element("th","",label));table.append(header);
    for(const [label,value] of [["Trading P/L excluding contributions",money(performance.trading_pnl)],["Recorded fees",money(performance.fees)],["Entries",count(performance.entries)],["Closed positions",count(performance.closed_trades)],["Closed proxy wins / losses",`${count(performance.wins)} / ${count(performance.losses)}`],["Peak-to-trough trading P/L drop",money(performance.max_peak_trading_pnl_drop)]]){const row=element("tr");row.append(element("td","",label),element("td","",value));table.append(row);}card.append(table);
    paragraph(card,performance.observed_win_rate_pct===null||performance.observed_win_rate_pct===undefined?"No observed closed-trade win rate yet. No profitable edge has been established.":`Observed closed proxy win rate: ${text(performance.observed_win_rate_pct)}%. This small, partial, unverified sample does not establish a profitable strategy or future win probability.`);
    paragraph(card,"The $500 cap covers gross modeled monthly commitments, including fees. A decline from unrealized profits can exceed that amount; the journal's P/L drop is a different measure.");
    const strategies=Array.isArray(data.diagnostics?.strategies)?data.diagnostics.strategies.slice(0,3):[];
    if(strategies.length){
      card.append(element("h3","","Latest recorded-session diagnostics"));
      paragraph(card,`Source ${text(data.diagnostics.source_date)||"unknown"} · recorded decision ${text(data.diagnostics.recorded_at)||"unknown"}. These explain that saved rule evaluation, rather than recommend a current trade.`);
      for(const item of strategies){const section=element("section","options-strategy");section.append(element("h4","",text(item.id).replaceAll("_"," ")));paragraph(section,`Recorded signal: ${item.signal===true?"triggered":item.signal===false?"not triggered":"not evaluated"}. Eligible returned contracts: ${count(item.eligible_count)} of ${count(item.inspected_quotes)} inspected.`);if(item.rejections&&typeof item.rejections==="object")for(const [reason,total] of Object.entries(item.rejections).slice(0,12))if(Number.isSafeInteger(total)&&total>0)paragraph(section,`${reason.replaceAll("_"," ")}: ${total}`);card.append(section);}
    }else paragraph(card,"No option-snapshot diagnostics to display. No missing quote, signal or fill is invented.");
    if(Array.isArray(performance.months)&&performance.months.length){const detail=element("details");detail.append(element("summary","","Monthly commitment evidence"));for(const month of performance.months.slice(-12))paragraph(detail,JSON.stringify(month));card.append(detail);}
    if(Array.isArray(data.failed_checks)&&data.failed_checks.length){const detail=element("details");detail.append(element("summary","","Saved failed checks"));for(const item of data.failed_checks.slice(-5))paragraph(detail,`${text(item.request_id)} · ${text(item.status)} · ${text(item.reason)}`);card.append(detail);}
    for(const limitation of (Array.isArray(data.limitations)?data.limitations:[]).slice(0,8))if(typeof limitation==="string")paragraph(card,limitation);
    root.append(card);
  }
  async function load(){
    if(!active()||loading)return;visible=true;loading=true;notice="";render();
    const marker=generation,token=app.state.token;
    try{const result=validate(await app.api("/options/review"));if(current(marker,token)){data=result;render();}}
    catch(error){if(current(marker,token)){data=null;notice=`Saved paper review unavailable: ${error.message}. No action was retried.`;render();}}
    finally{if(current(marker,token)){loading=false;render();}}
  }
  function detach(){generation++;visible=false;loading=false;data=null;notice="";$("options-review-content")?.replaceChildren();}
  function locked(){detach();}
  function status(){if(!app?.state.authenticated)locked();else if(visible)render();}
  function attach(value){app=value;const root=$("options-review-content");if(!root||attached===root)return;attached=root;$("refresh-options-review")?.addEventListener("click",load);}
  window.ariadneOptionsReview={attach,load,status,locked,detach,validate,money};
})();
