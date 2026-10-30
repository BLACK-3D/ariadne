"use strict";
// Dependency-free DOM and deferred API fixtures exercise writes, not browser layout.
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path"),crypto=require("node:crypto");
const root=path.resolve(__dirname,"../.."),file=path.join(root,"ariadne/static/venture.js"),source=fs.readFileSync(file,"utf8");
const copy=value=>JSON.parse(JSON.stringify(value)),checks=[];
const defer=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.childNodes=[];this.dataset={};this.listeners={};this.value="";this.disabled=false;this.hidden=false;this._text="";}
  append(...items){for(const item of items){this.childNodes.push(item);if(item && typeof item==="object")item.parent=this;}}
  replaceChildren(...items){this.childNodes=[];this._text="";this.append(...items);}
  set textContent(value){this.childNodes=[];this._text=String(value);}
  get textContent(){return this._text+this.childNodes.map(item=>typeof item==="string"?item:item.textContent).join("");}
  setAttribute(key,value){this[key]=String(value);}
  addEventListener(type,callback){(this.listeners[type]??=[]).push(callback);}
  async fire(type){for(const callback of this.listeners[type]||[])await callback({preventDefault(){}});}
}
const walk=node=>[node,...node.childNodes.flatMap(item=>item instanceof Element?walk(item):[])];
const now=new Date().toISOString();
const board=()=>({schema:1,revision:7,state:"ready",local_day:"2026-10-29",config:{enabled:true,paused:false,max_drafts_per_day:2,tasks:[{id:"offer",project_id:"business",title:"Draft offer",kind:"business_offer_pack",requires_owner_evidence:true}]},briefing:{enabled:true,paused:false,permitted:true,hour:6,minute:0,timezone:"America/Chicago"},hold:null,latest:null,progress:[{task_id:"offer",status:"draft_ready",run_status:"completed"}],evidence:[],next_actions:[],projects:[{id:"business",name:"<img src=x onerror=bad()>",domain:"business",sources:[{role:"plan",path:"plans/offer.json",sha256:"a".repeat(64),data_as_of:"2026-10-29",freshness:"dated",age_calendar_days:0,accepted_excerpt:JSON.stringify({one_liner:"<script>never execute</script>"})}]}]});
function harness(){
  const body=new Element("main");for(const id of ["venture-content","refresh-venture","run-venture"]){const item=new Element(id.includes("content")?"div":"button");item.id=id;body.append(item);}
  const document={getElementById:id=>walk(body).find(item=>item.id===id)||null,querySelectorAll:selector=>selector==="#venture-content [data-venture-mutation]"?walk(document.getElementById("venture-content")).filter(item=>item.dataset.ventureMutation):[]};
  const calls=[],toasts=[],artifacts=[],state={authenticated:true,token:"owner-key",status:{paused:false}};
  let response=board(),handler=null;
  const app={state,node:(tag,cls,text)=>{const item=new Element(tag);item.className=cls;if(text!==undefined&&text!==null)item.textContent=text;return item;},button:(label,cls,callback)=>{const item=new Element("button");item.className=cls;item.textContent=label;item.addEventListener("click",callback);return item;},api:async(p,o)=>{calls.push({path:p,options:o});return handler?handler(p,o):copy(response);},toast:value=>toasts.push(value),openArtifact:value=>artifacts.push(value),confirmAction:async()=>true};
  const box={window:{},document,crypto:{randomUUID:crypto.randomUUID},Date,Intl,console};vm.createContext(box);vm.runInContext(source,box,{filename:file});
  const api=box.window.ariadneVenture;api.attach(app);
  return {api,app,body,document,calls,toasts,artifacts,get response(){return response;},set response(value){response=value;},set handler(value){handler=value;},all:()=>walk(body),find:label=>walk(body).find(item=>item.tagName==="BUTTON"&&item.textContent===label)};
}
const check=(description,callback)=>Promise.resolve().then(callback).then(()=>checks.push(description));
async function main(){
  await check("Central local day and server day control the once-daily action",()=>{
    const h=harness();assert.equal(h.api.dayKey("America/Chicago",new Date("2026-10-30T02:00:00Z")),"2026-10-29");assert.equal(h.api.dayKey("invalid/timezone"),null);
    const value=board();assert.equal(h.api.canAdvance(value),true);value.latest={local_day:"2026-10-29",status:"completed"};assert.equal(h.api.canAdvance(value),false);assert.equal(h.api.canAdvance(value,false,"2026-10-30"),true);
  });
  await check("Global, board, briefing, receipt and persistent holds prevent advancement",()=>{
    const h=harness();assert.equal(h.api.canAdvance(board(),true),false);
    for(const change of [v=>v.config.paused=true,v=>v.config.enabled=false,v=>v.state="held",v=>v.briefing.permitted=false,v=>v.latest={status:"running"}]){const value=board();change(value);assert.equal(h.api.canAdvance(value),false);}
  });
  await check("Owner evidence carries CAS, observation and an explicit pinned optional file",()=>{
    const h=harness(),value=copy(h.api.evidencePayload({task:"offer",kind:"interview",note:"Owner spoke with a prospect",observed:"2026-10-29T10:00:00Z",path:"evidence\\notes.md",sha:"a".repeat(64)},7,Date.parse("2026-10-29T11:00:00Z")));
    assert.equal(value.expected_revision,7);assert.equal(value.evidence.path,"evidence/notes.md");assert.equal(value.evidence.observed_at,"2026-10-29T10:00:00.000Z");assert.equal(value.evidence.independently_verified,undefined);
    for(const override of [{observed:"2099-01-01"},{note:""},{kind:"trade_filled"},{path:"E:/secret",sha:"a".repeat(64)},{path:"../escape",sha:"a".repeat(64)},{path:"notes.md",sha:"A".repeat(64)},{path:"",sha:"a".repeat(64)}])assert.throws(()=>h.api.evidencePayload({task:"offer",kind:"review",note:"Observed",observed:"2026-10-29T10:00:00Z",...override},7,Date.parse("2026-10-29T11:00:00Z")));
  });
  await check("ETF request selects only fixed local study with finite explicit costs",()=>{
    const h=harness(),body=copy(h.api.studyPayload({path:"research\\nav.json",cost:"5"},"etf-fixture"));
    assert.deepEqual(body,{tool:"etf_research",arguments:{action:"study",path:"research/nav.json",request_id:"etf-fixture",cost_bps:5}});
    for(const cost of [""," ","NaN","Infinity","-1","100.01"])assert.throws(()=>h.api.studyPayload({path:"nav.json",cost},"new-id"));
    for(const badPath of ["E:/secret","C:/secret","../escape","/root","https://example.test","\\\\host\\share"])assert.throws(()=>h.api.studyPayload({path:badPath,cost:"5"},"new-id"));
    assert.equal(h.api.studyPayload({path:"nav.json",cost:"0"},"id").arguments.cost_bps,0);assert.equal(h.api.studyPayload({path:"nav.json",cost:"100"},"id").arguments.cost_bps,100);
  });
  await check("Plan text stays literal and owner evidence remains visibly unverified",async()=>{
    const h=harness();h.response.evidence=[{task_id:"offer",kind:"customer",note:"<svg onload=bad()>",observed_at:now}];await h.api.load();
    assert.match(h.body.textContent,/<img src=x onerror=bad\(\)>/);assert.match(h.body.textContent,/<script>never execute<\/script>/);assert.match(h.body.textContent,/not independently verified/);assert.equal(h.all().some(item=>["IMG","SCRIPT","SVG"].includes(item.tagName)),false);assert.equal(h.calls.length,1);
  });
  await check("A bounded display history does not assert that older owner evidence is missing",async()=>{
    const h=harness();await h.api.load();assert.match(h.body.textContent,/owner-evidence gate/);assert.doesNotMatch(h.body.textContent,/awaiting owner evidence/);
  });
  await check("A pending write is one request and an uncertain response is never retried",async()=>{
    const h=harness();await h.api.load();const pending=defer();h.handler=()=>pending.promise;
    const first=h.api.advance();await h.api.advance();assert.equal(h.calls.filter(call=>call.options?.method==="POST").length,1);
    pending.reject(new Error("connection interrupted"));await first;await h.api.advance();assert.equal(h.calls.length,2);assert.match(h.body.textContent,/Nothing is retried automatically/);
    assert.equal(h.find("I reviewed the saved records").disabled,true);h.handler=()=>copy(h.response);await h.api.load();assert.equal(h.find("I reviewed the saved records").disabled,false);
  });
  await check("A CAS conflict blocks further writes until a fresh owner read",async()=>{
    const h=harness();await h.api.load();h.handler=()=>{throw Object.assign(new Error("revision changed"),{status:409});};await h.api.advance();await h.api.advance();assert.equal(h.calls.length,2);assert.equal(h.document.getElementById("run-venture").disabled,true);
    h.handler=()=>copy(h.response);await h.api.load();assert.equal(h.document.getElementById("run-venture").disabled,false);
  });
  await check("Locking a tab discards an in-flight read and all private view data",async()=>{
    const h=harness(),pending=defer();h.handler=()=>pending.promise;const load=h.api.load();h.app.state.authenticated=false;h.api.locked();pending.resolve(board());await load;assert.equal(h.document.getElementById("venture-content").textContent,"");assert.equal(h.document.getElementById("run-venture").disabled,true);
  });
  await check("Late write responses after re-pairing cause no toast, refresh or new action",async()=>{
    const h=harness();await h.api.load();const pending=defer();h.handler=()=>pending.promise;const write=h.api.advance();h.app.state.token="different-key";h.api.locked();pending.resolve({status:"completed"});await write;assert.equal(h.calls.length,2);assert.equal(h.toasts.length,0);assert.equal(h.document.getElementById("venture-content").textContent,"");
  });
  await check("A global pause disables study submission and the daily advance",async()=>{
    const h=harness();await h.api.load();h.app.state.status.paused=true;h.api.status();assert.equal(h.document.getElementById("run-venture").disabled,true);
    const study=h.all().find(item=>item.dataset.ventureMutation==="study");assert.equal(study.disabled,true);await study.parent.fire("submit");await h.api.advance();assert.equal(h.calls.length,1);
  });
  await check("ETF study reports distinguish cutoff, actual data dates and qualification",async()=>{
    const h=harness();await h.api.load();const study=h.all().find(item=>item.dataset.ventureMutation==="study"),form=study.parent;const fields=walk(form).filter(item=>item.tagName==="INPUT");fields.find(item=>item.name==="path").value="research/nav.json";
    h.handler=(p,o)=>o?{ok:true,status:"completed",accepted_input:{sha256:"b".repeat(64)},source_as_of:"2026-10-29T00:00:00Z",summary:{data_last_dates:{VTI:"2026-10-28"},dividend_status:"excluded",month_end_coverage_status:"unverified_exchange_calendar",strategies:[{id:"sma10",status:"completed",metrics:{cagr:-.02,maximum_close_drawdown:-.3}}]},artifacts:[{path:"reports/etf-research/demo/study.md"}]}:copy(h.response);
    await form.fire("submit");await new Promise(resolve=>setImmediate(resolve));assert.equal(h.calls[1].path,"/operations/run");assert.equal(h.calls[1].options.body.tool,"etf_research");assert.match(h.body.textContent,/Analysis cutoff: 2026-10-29/);assert.match(h.body.textContent,/Last observations: VTI 2026-10-28/);assert.match(h.body.textContent,/Dividends: excluded/);assert.match(h.body.textContent,/unverified exchange calendar/);assert.match(h.body.textContent,/-2\.0%/);assert.match(h.body.textContent,/no future profit proved/);await h.find("Read report").fire("click");assert.equal(h.artifacts[0],"reports/etf-research/demo/study.md");
  });
  await check("Evidence form binds the read revision and never claims customer execution",async()=>{
    const h=harness();await h.api.load();const submit=h.find("Save owner-reported evidence"),form=submit.parent;walk(form).find(item=>item.name==="task").value="offer";walk(form).find(item=>item.name==="note").value="Owner reviewed scope";walk(form).find(item=>item.name==="observed").value="2026-10-28T10:00";
    h.handler=(p,o)=>o?{revision:8}:copy(h.response);await form.fire("submit");await new Promise(resolve=>setImmediate(resolve));const write=h.calls.find(call=>call.path==="/venture/evidence");assert.equal(write.options.body.expected_revision,7);assert.equal(write.options.body.evidence.kind,"review");assert.equal(write.options.body.evidence.task_id,"offer");assert.equal(write.options.body.evidence.note,"Owner reviewed scope");
  });
  await check("A hold confirmation cannot cross a pairing change",async()=>{
    const h=harness();h.response.state="held";h.response.hold={request_id:"held-receipt",reason:"Interrupted"};await h.api.load();const pending=defer();h.app.confirmAction=()=>pending.promise;const confirmation=h.find("Review and acknowledge hold").fire("click");h.app.state.token="new-owner";h.api.locked();pending.resolve(true);await confirmation;assert.equal(h.calls.length,1);
  });
  await check("No dynamic markup execution, runtime dependency, timer or hidden configuration editor",()=>{
    assert.doesNotMatch(source,/\.innerHTML\s*=|\beval\s*\(|setInterval\s*\(|setTimeout\s*\(|https?:\/\//);assert.doesNotMatch(source,/\/venture\/config/);
  });
  console.log(JSON.stringify({passed:checks.length,failures:0,checks,source_sha256:crypto.createHash("sha256").update(source).digest("hex")},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
