"use strict";

// Owner-entered scenarios and evidence. Loading and prefilling never write.
(() => {
  let app, attached, data=null, detail=null, selected=null, generation=0, sequence=0;
  let visible=false, busy=false, loading=false, stale=false, uncertain=null, inspected=false, notice="", dirty=false;
  let actions=[], inputs=[], locals=[], draft=blankScenario(), evidence=blankEvidence(), reason="";
  const $=id=>document.getElementById(id), text=value=>typeof value==="string"?value:"", trim=value=>text(value).trim();
  const idPattern=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
  const moneyPattern=/^(?:0|[1-9]\d{0,6})(?:\.\d{1,2})?$/;
  const hourPattern=/^(?:0|[1-9]\d{0,2})(?:\.\d{1,6})?$/;
  const moneyKinds=new Set(["sale_reported","payout_received","cost_paid","refund_paid"]);
  const kinds={interview:"Interview you conducted",pilot_agreed:"Pilot agreement you report",sale_reported:"Sale booked — not cash received",payout_received:"Own payout actually received",cost_paid:"Own business cost actually paid",refund_paid:"Own refund actually paid",hours_logged:"Owner time actually spent"};
  const clone=value=>JSON.parse(JSON.stringify(value));
  const e=(tag,cls="",value)=>app.node(tag,cls,value), p=(parent,value,cls="revenue-small")=>parent.append(e("p",cls,value));
  const current=(marker,token)=>marker===generation&&visible&&app?.state.authenticated&&app.state.token===token&&(!app.state.view||app.state.view==="venture");
  const paused=()=>app?.state.status?.paused!==false;
  const newId=()=>"revenue-ui-"+crypto.randomUUID().replaceAll("-","");

  function blankOffer(kind){return {id:kind==="pod"?"apparel":"service",name:kind==="pod"?"Print-on-demand apparel hypothesis":"Recurring quote and invoice service hypothesis",kind,
    unit_price:"",units_assumed:"0",owner_hours_per_unit:"",costs:{production_per_unit:"",shipping_per_unit:"",other_variable_per_unit:"",payment_fee_bps:"",payment_fee_fixed_per_unit:"",refund_reserve_bps:"",acquisition_per_unit:""},funding:{mode:"merchant_fronts",payout_lag_days:""},sources:[]};}
  function blankScenario(){return {schema:1,label:"POD and service comparison — unvalidated assumptions",system_budget_monthly:"100.00",actual_system_cost_monthly:"",other_fixed_cost_monthly:"",monthly_profit_target:"2000.00",owner_hourly_value:"20.00",weekly_hours_profiles:[10,20],fixed_nonbillable_hours_monthly:"20",offers:[blankOffer("pod"),blankOffer("recurring_service")]};}
  function blankEvidence(){return {scenario_id:"",kind:"interview",occurred_at:"",amount:"",minutes:"",units:"",note:"",reference_path:""};}
  function moneyInput(value,label,nullable=false){if(value!==null&&value!==undefined&&typeof value!=="string")throw new Error(`${label}: money must be entered as exact decimal text.`);const raw=trim(value);if(!raw&&nullable)return null;if(!moneyPattern.test(raw))throw new Error(`${label}: enter a dollar amount with at most two decimal places${nullable?", or leave it unknown":""}.`);const [whole,part=""]=raw.split("."),cents=BigInt(whole)*100n+BigInt(part.padEnd(2,"0"));if(cents>100000000n)throw new Error(`${label}: amount exceeds the planning limit.`);return `${cents/100n}.${String(cents%100n).padStart(2,"0")}`;}
  function money(value){if(typeof value!=="string"||! /^-?(?:0|[1-9]\d{0,16})\.\d{2}$/.test(value))return "Unknown";const sign=value.startsWith("-")?"-":"",raw=sign?value.slice(1):value,[whole,part]=raw.split(".");return `${sign}$${whole.replace(/\B(?=(\d{3})+(?!\d))/g,",")}.${part}`;}
  function hoursInput(value,label,nullable=false){if(value!==null&&value!==undefined&&typeof value!=="string")throw new Error(`${label}: hours must be entered as exact decimal text.`);const raw=trim(value);if(!raw&&nullable)return null;if(!hourPattern.test(raw)||Number(raw)>744)throw new Error(`${label}: use hours from 0 to 744 with at most six decimal places.`);return raw;}
  function whole(value,label,low,high,nullable=false){const raw=typeof value==="number"&&Number.isInteger(value)?String(value):trim(value);if(!raw&&nullable)return null;if(!/^\d{1,7}$/.test(raw))throw new Error(`${label}: use a whole number.`);const number=Number(raw);if(!Number.isSafeInteger(number)||number<low||number>high)throw new Error(`${label}: choose ${low}–${high}.`);return number;}
  function percentBps(value){if(value!==null&&value!==undefined&&typeof value!=="string")throw new Error("Enter percentages as decimal text.");const raw=trim(value);if(!raw)return null;const found=/^(\d{1,3})(?:\.(\d{1,2}))?$/.exec(raw);if(!found)throw new Error("Fee and refund percentages need at most two decimal places, or a blank for unknown.");const result=Number(found[1])*100+Number((found[2]||"").padEnd(2,"0"));if(result>10000)throw new Error("Fee and refund percentages must be between 0 and 100.");return result;}
  function percentText(value){return Number.isSafeInteger(value)&&value>=0&&value<=10000?`${Math.floor(value/100)}.${String(value%100).padStart(2,"0")}`:"";}
  function required(value,label,max=200){const result=trim(value);if(!result||result.length>max||/[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/.test(result))throw new Error(`${label} needs bounded nonempty ordinary text.`);return result;}
  function path(value){const result=text(value).replaceAll("\\","/");if(text(value)!==trim(value)||!result||result.length>1000||result.startsWith("/")||/[\u0000-\u001f\u007f:<>"|?*]/.test(result)||result.split("/").some(part=>!part||part==="."||part===".."||/[. ]$/.test(part)||/^(?:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\.|$)/i.test(part)))throw new Error("Choose an ordinary file inside Ariadne Workspace.");return result;}
  function localTime(value,now=Date.now()){
    const raw=trim(value);if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d{1,3})?)?$/.test(raw))throw new Error("Enter the actual observation time in this PC's local time zone.");
    const date=new Date(raw);if(!Number.isFinite(date.valueOf())||date.valueOf()>now)throw new Error("Choose a valid actual observation time; future evidence is not accepted.");
    const parts=/^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)/.exec(raw);
    if([date.getFullYear(),date.getMonth()+1,date.getDate(),date.getHours(),date.getMinutes()].some((item,index)=>item!==Number(parts[index+1])))throw new Error("That local date/time is invalid or skipped by a clock change. Verify the observation time.");
    return date.toISOString();
  }
  function scenarioPayload(value){
    if(!Array.isArray(value.offers)||value.offers.length<1||value.offers.length>12)throw new Error("Choose one to twelve offers.");
    const seen=new Set(),offers=value.offers.map(item=>{
      if(!idPattern.test(text(item.id))||seen.has(item.id)||!["pod","recurring_service"].includes(item.kind))throw new Error("Offer identities are incomplete; reload a saved comparison.");seen.add(item.id);
      const costs={};for(const key of ["production_per_unit","shipping_per_unit","other_variable_per_unit","payment_fee_fixed_per_unit","acquisition_per_unit"])costs[key]=moneyInput(item.costs[key],key.replaceAll("_"," "),true);
      costs.payment_fee_bps=typeof item.costs.payment_fee_bps==="number"?whole(item.costs.payment_fee_bps,"Payment fee basis points",0,10000):percentBps(item.costs.payment_fee_bps);
      costs.refund_reserve_bps=typeof item.costs.refund_reserve_bps==="number"?whole(item.costs.refund_reserve_bps,"Refund reserve basis points",0,10000):percentBps(item.costs.refund_reserve_bps);
      if(!["merchant_fronts","deducted_from_customer_payment"].includes(item.funding.mode))throw new Error("Choose how fulfillment is funded.");
      return {id:item.id,name:required(item.name,"Offer name"),kind:item.kind,unit_price:moneyInput(item.unit_price,"Gross unit price"),units_assumed:whole(item.units_assumed,"Assumed units",0,10000),owner_hours_per_unit:hoursInput(item.owner_hours_per_unit,"Owner hours per unit",true),costs,
        funding:{mode:item.funding.mode,payout_lag_days:whole(item.funding.payout_lag_days,"Payout lag days",0,90,true)},sources:Array.isArray(item.sources)?clone(item.sources):[]};
    });
    return {schema:1,label:required(value.label,"Comparison label"),system_budget_monthly:moneyInput(value.system_budget_monthly,"System budget"),actual_system_cost_monthly:moneyInput(value.actual_system_cost_monthly,"Actual system cost",true),other_fixed_cost_monthly:moneyInput(value.other_fixed_cost_monthly,"Other fixed costs",true),monthly_profit_target:moneyInput(value.monthly_profit_target,"Monthly profit target"),owner_hourly_value:moneyInput(value.owner_hourly_value,"Owner hourly value"),weekly_hours_profiles:[10,20],fixed_nonbillable_hours_monthly:hoursInput(value.fixed_nonbillable_hours_monthly,"Fixed nonbillable hours"),offers};
  }
  function evidencePayload(fields,now=Date.now()){
    if(!idPattern.test(text(fields.scenario_id))||!Object.hasOwn(kinds,fields.kind))throw new Error("Choose a saved comparison and an evidence type.");
    const amount=moneyKinds.has(fields.kind)?moneyInput(fields.amount,"Evidence amount"):null;
    if(amount!==null&&BigInt(amount.replace(".",""))===0n)throw new Error("Money evidence needs a positive actual amount.");
    return {scenario_id:fields.scenario_id,kind:fields.kind,occurred_at:localTime(fields.occurred_at,now),amount,
      minutes:fields.kind==="hours_logged"?whole(fields.minutes,"Owner minutes",1,600000):null,
      units:fields.kind==="sale_reported"?whole(fields.units,"Sold units",1,1000000):null,
      note:required(fields.note,"What happened",1000),reference_path:trim(fields.reference_path)?path(fields.reference_path):null};
  }
  function request(kind,revision,identifier,extra={}){
    if(!Number.isSafeInteger(revision)||revision<0||!idPattern.test(identifier))throw new Error("Inspect the latest saved revision before an action.");
    const base={request_id:identifier,expected_revision:revision};
    if(kind==="plan")return {...base,scenario:scenarioPayload(extra.scenario)};
    if(kind==="evidence")return {...base,event:evidencePayload(extra.event)};
    if(kind==="acknowledge"){if(!idPattern.test(text(extra.failed_request_id)))throw new Error("Choose the failed or interrupted receipt you inspected.");return {...base,failed_request_id:extra.failed_request_id,reason:required(extra.reason,"Inspection reason",1000)};}
    throw new Error("Unsupported revenue action.");
  }
  function writable(held=false){const inspectedHeld=held&&uncertain&&inspected&&data?.hold?.request_id===uncertain.id&&["failed","running"].includes(data.hold.status);return Boolean(app?.state.authenticated&&visible&&data&&!busy&&!loading&&!paused()&&!stale&&(!uncertain||inspectedHeld)&&(held||!data.hold));}
  function controls(){
    if($("refresh-revenue"))$("refresh-revenue").disabled=busy||loading||!app?.state.authenticated;
    for(const item of actions)item.node.disabled=!writable(item.held)||!item.allowed();
    for(const item of inputs)item.disabled=busy||loading||!app?.state.authenticated;
    for(const item of locals)item.disabled=busy||loading||!app?.state.authenticated||!visible;
  }
  function button(label,callback,allowed=()=>true,held=false){const node=app.button(label,"button-secondary",async()=>{if(!writable(held)||!allowed())return;try{await callback();}catch(error){notice=error.message;render();}});actions.push({node,allowed,held});return node;}
  function localButton(label,callback){const node=app.button(label,"button-secondary",()=>{if(busy||loading||!app?.state.authenticated||!visible)return;try{callback();}catch(error){notice=error.message;render();}});locals.push(node);return node;}
  function field(parent,label,name,value,onInput,{type="text",options=null,max=100}={}){
    const wrap=e("label","revenue-field",label),node=e(options?"select":type==="textarea"?"textarea":"input");node.name=name;node.setAttribute("aria-label",label);
    if(options)for(const [key,title]of options){const option=e("option","",title);option.value=key;node.append(option);}else if(type!=="textarea")node.type=type;
    if(!options)node.maxLength=max;if(type==="datetime-local")node.step="60";node.value=value===null||value===undefined?"":String(value);
    node.addEventListener(options?"change":"input",()=>onInput(node.value));wrap.append(node);parent.append(wrap);inputs.push(node);return node;
  }
  function draftField(parent,label,name,object,key,options={}){return field(parent,label,name,object[key],value=>{object[key]=value;dirty=true;controls();},options);}
  function count(value){return Number.isSafeInteger(value)&&value>=0?value.toLocaleString("en-US"):"Unknown / not attainable";}
  function fits(value){return value===true?"Fits the assumed hours":value===false?"Exceeds the assumed hours":"Unknown";}
  function metric(parent,label,value){const row=e("div","revenue-metric");row.append(e("span","",label),e("strong","",value));parent.append(row);}
  function artifactButtons(parent,artifacts){for(const item of(Array.isArray(artifacts)?artifacts:[]).slice(0,4)){try{const relative=path(item.path);parent.append(app.button(relative.endsWith(".json")?"Inspect saved report data":"Open saved artifact","button-secondary",()=>app.openArtifact?.(relative)));}catch{}}}

  function renderComparison(root,plan){
    if(!plan||!Array.isArray(plan.offers))return;
    const section=e("section","revenue-section");section.append(e("span","eyebrow","SAVED ASSUMPTIONS / NO PROVEN DEMAND"),e("h3","",text(plan.label)||"Saved comparison"));
    p(section,dirty?"The form has unsaved changes. These results belong to the saved scenario until you calculate and save a new comparison.":"Scenario arithmetic only. No price acceptance, customer demand, collected revenue or dependable trading income is established.","revenue-warning");
    p(section,`System cost used: ${money(plan.system_cost_used)} · ${plan.system_cost_basis==="budget_assumption_not_measured"?"actual bill unknown; budget used once":"owner-supplied actual cost, unverified"}. Other fixed costs: ${money(plan.other_fixed_cost_monthly)}. Required monthly cash contribution: ${money(plan.required_monthly_cash_contribution)}.`);
    const cards=e("div","revenue-comparison");
    for(const row of plan.offers.slice(0,12)){
      const card=e("article","revenue-offer-result"),unit=row.kind==="pod"?"orders":"clients";card.append(e("span","eyebrow",row.kind==="pod"?"PRINT ON DEMAND":"RECURRING SERVICE"),e("h4","",text(row.name)||"Unnamed offer"));
      metric(card,"Cash contribution per "+(row.kind==="pod"?"order":"client billing cycle"),money(row.per_unit?.cash_contribution));
      metric(card,"After assumed owner-time value per unit",money(row.per_unit?.economic_contribution));
      metric(card,`${unit} needed for the cash target`,count(row.target?.minimum_units_cash));
      metric(card,`${unit} needed after owner-time value`,count(row.target?.minimum_units_economic));
      metric(card,"Modeled monthly surplus before owner pay / personal tax",money(row.monthly?.cash_surplus_before_owner_compensation_and_personal_taxes));
      metric(card,"Modeled monthly surplus after imputed owner time",money(row.monthly?.economic_surplus_before_personal_taxes));
      metric(card,"Estimated fulfillment/marketing cash buffer",money(row.funding?.working_capital_estimate));
      p(card,`Assumed volume: ${count(row.units_assumed)} ${unit}. Gross sales assumed: ${money(row.monthly?.gross_sales_assumed)}. These are not booked sales or cash receipts.`);
      if(row.unit_costs_complete!==true)p(card,`Unknown unit costs: ${(row.unknown_unit_cost_fields||[]).map(value=>text(value).replaceAll("_"," ")).join(", ")||"not supplied"}. Unknown is not zero.`,"revenue-warning");
      for(const capacity of(Array.isArray(row.capacity)?row.capacity:[]).slice(0,4))p(card,`${text(capacity.weekly_hours)} h/week: cash target ${fits(capacity.cash_target_units_fit).toLowerCase()}; after-time target ${fits(capacity.economic_target_units_fit).toLowerCase()}.`);
      p(card,row.funding?.mode==="deducted_from_customer_payment"?"Customer-funded fulfillment is an explicit assumption. Other variable and acquisition costs can still require your cash. Eligibility and payout timing remain unverified.":"Merchant-fronted fulfillment may require production and shipping cash before a channel payout.");
      p(card,"Cash-buffer estimates assume uniform 30-day order flow and a first-unit floor; they exclude actual payout holds, refund shocks, taxes and unmodeled costs. They do not establish peak cash needs.");cards.append(card);
    }
    section.append(cards);artifactButtons(section,detail?.artifacts);root.append(section);
  }
  function renderForm(root){
    const section=e("section","revenue-section"),form=e("form","revenue-form");section.append(e("h3","","Build a comparison"));
    p(section,"Blank optional costs stay unknown. Enter 0 only when it is an explicit assumption you accept. Prices are retained gross proceeds excluding pass-through sales tax; include retained shipping revenue only when you mean to.");
    section.append(localButton("Load illustrative comparison",loadIllustrative));
    p(section,"The illustrative prices and costs are hypothetical, not provider quotes. Assumed sales start at zero. Loading them only fills this local form.");
    const common=e("div","revenue-fields");
    for(const [label,key]of[["Comparison label","label"],["Monthly system budget · USD","system_budget_monthly"],["Actual monthly system cost · blank = unknown","actual_system_cost_monthly"],["Other monthly fixed costs · blank = unknown","other_fixed_cost_monthly"],["Monthly cash profit target before personal tax · USD","monthly_profit_target"],["Value of owner time · USD/hour","owner_hourly_value"],["Fixed sales/admin hours per month","fixed_nonbillable_hours_monthly"]])draftField(common,label,key,draft,key,{max:key==="label"?200:30});
    form.append(common);p(form,"Capacity is compared at 10 and 20 owner hours/week using 52/12 weeks/month. Unit delivery/support hours are additional to fixed sales/admin hours.");
    for(let index=0;index<draft.offers.length;index++){
      const offer=draft.offers[index],card=e("fieldset","revenue-offer-input");card.append(e("legend","",offer.kind==="pod"?"POD apparel / per order":"Quote and invoice service / per client billing cycle"));
      const fields=e("div","revenue-fields");
      for(const [label,key]of[["Offer name","name"],["Gross price per unit · USD","unit_price"],["Assumed orders / active paying clients per month","units_assumed"],["Owner delivery/support hours per unit · blank = unknown","owner_hours_per_unit"]])draftField(fields,label,`offer${index}_${key}`,offer,key,{max:key==="name"?200:30});
      for(const [label,key]of[["Production cost per unit · USD","production_per_unit"],["Shipping cost per unit · USD","shipping_per_unit"],["Other variable cost per unit · USD","other_variable_per_unit"],["Payment fee · percent of full gross sale","payment_fee_bps"],["Fixed payment fee per unit · USD","payment_fee_fixed_per_unit"],["Refund reserve · percent of full gross sale","refund_reserve_bps"],["Acquisition/advertising cost per unit · USD","acquisition_per_unit"]])draftField(fields,label,`offer${index}_${key}`,offer.costs,key,{max:30});
      draftField(fields,"Fulfillment funding",`offer${index}_funding`,offer.funding,"mode",{options:[["merchant_fronts","I front production and shipping"],["deducted_from_customer_payment","Deduct production/shipping from customer payment"]]});
      draftField(fields,"Assumed payout lag · whole days, blank = unknown",`offer${index}_payout_lag_days`,offer.funding,"payout_lag_days",{max:2});
      card.append(fields);p(card,"Fee and reserve percentages are planning inputs. Fulfillment mode and payout lag do not prove provider eligibility or actual payout availability.");form.append(card);
    }
    form.append(button("Calculate and save comparison",()=>mutate("plan",{scenario:draft})));form.addEventListener("submit",event=>{event.preventDefault();if(writable())mutate("plan",{scenario:draft}).catch(error=>{notice=error.message;render();});});section.append(form);root.append(section);
  }
  function renderCash(root){
    const cash=data.cash_summary||{},section=e("section","revenue-section");section.append(e("span","eyebrow","OWNER-REPORTED / UNVERIFIED"),e("h3","","Cash movements you reported"));
    p(section,`Reporting month: ${text(cash.month)||"unknown"} · UTC. Booked sales, customer invoice face values and draft scenarios are not cash received by your business.`);
    const metrics=e("div","revenue-cash-grid");for(const [label,key]of[["Booked sales reported — not collected cash","sale_reported"],["Own payouts received","payout_received"],["Own costs paid","cost_paid"],["Own refunds paid","refund_paid"],["Net reported cash movements","net_reported_cash"]])metric(metrics,label,money(cash[key]));section.append(metrics);
    p(section,"Net reported cash = payouts received − costs paid − refunds paid. Provider-net payouts must not have the same fees deducted again. System budget and owner-time assumptions are not automatically deducted here. Incomplete or missing expenses prevent a profit claim.","revenue-warning");
    p(section,`Reported interviews: ${count(cash.interviews)} · pilot agreements: ${count(cash.pilot_agreements)} · owner minutes: ${count(cash.owner_minutes_logged)}. These records are your reports, not independently verified outcomes.`);root.append(section);
  }
  function renderEvidence(root){
    const section=e("section","revenue-section"),form=e("form","revenue-form"),rows=Array.isArray(data.scenarios)?data.scenarios:[];section.append(e("h3","","Record a real business observation"));
    p(section,"Use this for your own service fee or store payout. Do not enter the face value of an invoice prepared for someone else's business as your income. A reference file stays local and is not independently authenticated.");
    const fields=e("div","revenue-fields");
    field(fields,"Saved comparison","evidence_scenario",evidence.scenario_id,value=>{evidence.scenario_id=value;},{options:[["","Choose a saved comparison"],...rows.slice(0,100).filter(row=>idPattern.test(text(row.id))).map(row=>[row.id,text(row.label)||row.id])]});
    field(fields,"Evidence type","evidence_kind",evidence.kind,value=>{evidence.kind=value;evidence.amount=evidence.minutes=evidence.units="";render();},{options:Object.entries(kinds)});
    field(fields,"Actual observation time · this PC's local time zone","evidence_time",evidence.occurred_at,value=>{evidence.occurred_at=value;},{type:"datetime-local"});
    if(moneyKinds.has(evidence.kind))field(fields,"Actual amount · USD","evidence_amount",evidence.amount,value=>{evidence.amount=value;},{max:30});
    if(evidence.kind==="sale_reported")field(fields,"Sold units / client billing cycles","evidence_units",evidence.units,value=>{evidence.units=value;},{max:7});
    if(evidence.kind==="hours_logged")field(fields,"Owner minutes actually spent","evidence_minutes",evidence.minutes,value=>{evidence.minutes=value;},{max:6});
    field(fields,"What happened? · owner report","evidence_note",evidence.note,value=>{evidence.note=value;},{type:"textarea",max:1000});
    field(fields,"Optional reference file inside Workspace","evidence_reference",evidence.reference_path,value=>{evidence.reference_path=value;},{max:1000});
    form.append(fields,button("Save owner-reported evidence",()=>mutate("evidence",{event:evidence}),()=>rows.length>0));
    form.addEventListener("submit",event=>{event.preventDefault();if(writable())mutate("evidence",{event:evidence}).catch(error=>{notice=error.message;render();});});section.append(form);
    const history=e("details");history.append(e("summary","","Recent owner-reported observations"));for(const item of(Array.isArray(data.evidence)?data.evidence:[]).slice(-20).reverse()){const row=e("article","revenue-evidence-row");row.append(e("strong","",kinds[item.kind]||"Owner observation"));p(row,`${text(item.occurred_at)} · ${text(item.scenario_id)}${item.amount?" · "+money(item.amount):""}`);p(row,text(item.note));history.append(row);}section.append(history);root.append(section);
  }
  function render(){
    const root=$("revenue-content");if(!root||!app)return;root.replaceChildren();actions=[];inputs=[];locals=[];
    if(!app.state.authenticated||!visible){controls();return;}
    const intro=e("section","revenue-intro");intro.append(e("span","eyebrow","BUSINESS / COMPARE BEFORE COMMITTING"),e("h3","","Contribution first. Collected cash next."));p(intro,"Compare apparel fulfillment with a recurring paperwork service. This workbench creates local scenarios and records your observations; it does not contact customers, open stores, place orders or supply dependable trading income.");root.append(intro);
    if(notice)p(root,notice,"revenue-notice");
    if(uncertain){const card=e("section","revenue-warning");p(card,`Interrupted ${uncertain.kind} response · ${uncertain.id}. No automatic retry. Refresh saved work and inspect the original receipt before another action.`);card.append(localButton("Inspect interrupted request",()=>{inspected=true;load();}));if(inspected&&data?.last_run?.request_id===uncertain.id&&data.last_run.status==="completed"&&data.last_run.action===uncertain.kind)card.append(localButton("I reviewed the completed receipt",()=>{uncertain=null;stale=false;notice="Original completed receipt inspected. No action was replayed.";render();}));root.append(card);}
    if(!data){p(root,loading?"Reading saved revenue work…":"Saved work is unavailable. Inspect again when the local server is ready.");controls();return;}
    if(paused())p(root,"Autonomy is paused. Reading and local form preparation remain available; saving scenarios, evidence and acknowledgments is held.","revenue-warning");
    if(stale)p(root,"The saved revision needs inspection. Refresh before another change.","revenue-warning");
    if(data.hold){const held=e("section","revenue-warning");p(held,`HELD · ${text(data.hold.request_id)} · ${text(data.hold.reason)}`);field(held,"Why is this failed/interrupted receipt safe to acknowledge?","revenue_ack_reason",reason,value=>{reason=value;},{type:"textarea",max:1000});held.append(button("Acknowledge inspected receipt",acknowledge,()=>idPattern.test(text(data.hold.request_id)),true));root.append(held);}
    const manifests=Array.isArray(data.scenarios)?data.scenarios:[];
    if(manifests.length){const history=e("section","revenue-history");field(history,"Saved comparisons","revenue_history",selected||"",value=>selectScenario(value),{options:manifests.slice(-100).reverse().filter(row=>idPattern.test(text(row.id))).map(row=>[row.id,text(row.label)||row.id])});root.append(history);}
    renderComparison(root,detail?.plan);renderCash(root);renderForm(root);renderEvidence(root);
    p(root,"Only USD scenarios are supported. Fees, refund reserves and imputed owner time round upward to cents per unit. Leaving Venture Lab clears unsaved forms and cached records. No model or external service is used.");controls();
  }
  function loadIllustrative(){
    draft=blankScenario();draft.label="Illustrative POD vs service comparison — unvalidated assumptions";draft.other_fixed_cost_monthly="0.00";
    const pod=draft.offers[0],service=draft.offers[1];pod.unit_price="29.99";pod.owner_hours_per_unit="0.1";pod.costs={production_per_unit:"14.00",shipping_per_unit:"5.00",other_variable_per_unit:"0.00",payment_fee_bps:"3.00",payment_fee_fixed_per_unit:"0.30",refund_reserve_bps:"5.00",acquisition_per_unit:"0.00"};pod.funding.payout_lag_days="14";
    service.unit_price="500.00";service.owner_hours_per_unit="6";service.costs={production_per_unit:"0.00",shipping_per_unit:"0.00",other_variable_per_unit:"15.00",payment_fee_bps:"3.00",payment_fee_fixed_per_unit:"0.30",refund_reserve_bps:"5.00",acquisition_per_unit:"0.00"};service.funding.payout_lag_days="14";
    dirty=true;notice="Illustrative assumptions loaded locally. Zero sales assumed; no provider quote, customer or revenue is established. Calculate only after reviewing every input.";render();
  }
  function editDraft(scenario){draft=clone(scenario);for(const offer of draft.offers){offer.costs.payment_fee_bps=percentText(offer.costs.payment_fee_bps);offer.costs.refund_reserve_bps=percentText(offer.costs.refund_reserve_bps);}dirty=false;}
  async function selectScenario(identifier,{replaceDraft=true}={}){
    if(!idPattern.test(text(identifier))||!app?.state.authenticated||!visible)return;
    const marker=generation,token=app.state.token,read=++sequence;loading=true;controls();
    try{const value=await app.api("/revenue/plans/"+encodeURIComponent(identifier));if(!current(marker,token)||read!==sequence)return;
      if(value?.id!==identifier||value.receipt?.request_id!==identifier||value.receipt?.status!=="completed"||value.receipt?.action!=="plan"||!value.scenario||!Array.isArray(value.scenario.offers)||!value.plan)throw new Error("The saved comparison lacks its matching completed receipt.");
      detail=value;selected=identifier;if(replaceDraft)editDraft(value.scenario);if(!evidence.scenario_id)evidence.scenario_id=identifier;
    }catch(error){if(current(marker,token)&&read===sequence){detail=null;stale=true;notice=error.message;}}
    finally{if(current(marker,token)&&read===sequence){loading=false;render();}}
  }
  async function load({preferredId=null,replaceDraft=false}={}){
    if(!app?.state.authenticated||(app.state.view&&app.state.view!=="venture")||busy)return;
    visible=true;const marker=generation,token=app.state.token,read=++sequence;loading=true;controls();
    try{const value=await app.api("/revenue");if(!current(marker,token)||read!==sequence)return;
      if(!value||!Number.isSafeInteger(value.revision)||value.revision<0||!Array.isArray(value.scenarios)||value.scenarios.length>500)throw new Error("Revenue status is incomplete; no change is permitted.");
      data=value;stale=false;loading=false;
      const identifier=preferredId||selected||value.selected_scenario_id;
      if(identifier&&value.scenarios.some(row=>row.id===identifier)){await selectScenario(identifier,{replaceDraft:replaceDraft||!dirty});return;}
      detail=null;selected=null;render();
    }catch(error){if(current(marker,token)&&read===sequence){data=null;detail=null;selected=null;stale=true;notice=error.message;}}
    finally{if(current(marker,token)&&read===sequence){loading=false;render();}}
  }
  async function mutate(kind,extra={}){
    if(!writable(kind==="acknowledge"))return;
    const body=request(kind,data.revision,newId(),extra),marker=generation,token=app.state.token;busy=true;notice="";controls();let completed=false;
    try{const result=await app.api("/revenue/"+kind,{method:"POST",body});if(!current(marker,token))return;
      if(result?.request_id!==body.request_id||result.status!=="completed"||result.action!==kind
        ||kind==="plan"&&result.scenario_id!==body.request_id||kind==="evidence"&&result.scenario_id!==body.event.scenario_id
        ||kind==="acknowledge"&&result.failed_request_id!==body.failed_request_id)throw new Error("The action returned no matching completed receipt.");
      completed=true;uncertain=null;notice=kind==="plan"?"Comparison saved. All results remain hypothetical assumptions.":kind==="evidence"?"Owner-reported observation saved; it is not independently verified.":"Inspected receipt acknowledged. No prior work was adopted or replayed.";
      if(kind==="evidence")evidence={...blankEvidence(),scenario_id:extra.event.scenario_id};if(kind==="acknowledge")reason="";
    }catch(error){if(current(marker,token)){if([400,409].includes(error.status)){stale=true;notice=`${error.message} Refresh and inspect saved work before another change.`;}else{uncertain={kind,id:body.request_id};inspected=false;notice="The response is uncertain. Preserve the request and inspect saved receipts; do not retry it.";}}}
    finally{if(current(marker,token)){busy=false;render();if(completed)await load({preferredId:kind==="plan"?body.request_id:selected,replaceDraft:kind==="plan"});}}
  }
  async function acknowledge(){
    if(!writable(true)||!data.hold)return;
    const marker=generation,token=app.state.token,revision=data.revision,extra={failed_request_id:data.hold.request_id,reason};
    request("acknowledge",revision,"inspection-check",extra);
    if(app.confirmAction&&!await app.confirmAction("Acknowledge inspected revenue work?","Retain the failed/interrupted receipt and any partial files. Nothing will be adopted, replayed, deleted or counted as revenue.","Acknowledge inspected receipt","Keep held"))return;
    if(!current(marker,token)||data.revision!==revision||!writable(true))return;await mutate("acknowledge",extra);
  }
  function status(){if(!app?.state.authenticated){locked();return;}if(visible)controls();}
  function detach(){generation++;sequence++;visible=false;data=null;detail=null;selected=null;busy=loading=false;draft=blankScenario();evidence=blankEvidence();dirty=false;actions=[];inputs=[];locals=[];$("revenue-content")?.replaceChildren();}
  function locked(){detach();uncertain=null;inspected=false;notice="";stale=false;reason="";}
  function attach(value){app=value;const root=$("revenue-content");if(!root||attached===root)return;attached=root;$("refresh-revenue")?.addEventListener("click",()=>load());}
  window.ariadneRevenue={attach,load,status,locked,detach,request,path,scenarioPayload,evidencePayload,percentBps,moneyInput,localTime,loadIllustrative,selectScenario,mutate,acknowledge};
})();
