"use strict";
// Usage: node scripts/check_routine_ui.cjs [--receipt PATH]
// Independent minimal-DOM async boundary checks. No browser/model/network use.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== "--receipt" || !args[1])) {
  console.error("Usage: node scripts/check_routine_ui.cjs [--receipt PATH]");
  process.exit(2);
}
const receiptPath = args.length ? path.resolve(args[1]) : null;
const source = fs.readFileSync(path.join(root, "ariadne/static/routines.js"), "utf8");
const appSource = fs.readFileSync(path.join(root, "ariadne/static/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "ariadne/static/index.html"), "utf8");
const checks = [];
function deferred() { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; }
const clone = value => JSON.parse(JSON.stringify(value));
const catalog = () => ({templates:[], procedures:[
  {id:"readiness_report",name:"Readiness",outputs:["readiness.md"]},
  {id:"power_budget",name:"Power",outputs:["power-budget.md"]}],
  routines:[{id:"definition-1",name:"PRIVATE OWNER ROUTINE",steps:["readiness_report","power_budget"],interval_minutes:5,enabled:false,last_error:null,revision:7}],runs:[]});
function fixture({authenticated=true,pending=null,handler=null,confirm=null}={}) {
  const all=[], elements=new Map(), requests=[], storage=new Map(), toasts=[];
  class Element {
    constructor(tag="div",cls="",text="") {this.tagName=tag.toUpperCase();this.className=cls;this.children=[];this.dataset={};this.listeners=new Map();this.hidden=false;this.disabled=false;this.open=false;this.value="";this.checked=false;this._text=String(text);all.push(this);}
    set textContent(value){this._text=String(value);this.children=[];}
    get textContent(){return this._text+this.children.map(node=>node.textContent).join("");}
    set innerHTML(value){throw new Error("Unsafe HTML sink");}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this._text="";this.children=[...nodes];}
    setAttribute(name,value){this[name]=String(value);}
    addEventListener(name,listener){if(!this.listeners.has(name))this.listeners.set(name,[]);this.listeners.get(name).push(listener);}
    async fire(name){for(const listener of this.listeners.get(name)||[])await listener({preventDefault(){}});}
    showModal(){this.open=true;}
    close(){this.open=false;}
    focus(){this.focused=true;}
  }
  for(const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1],new Element());
  const element = id => {assert(elements.has(id),`Missing real HTML hook ${id}`);return elements.get(id);};
  const document={hidden:false,getElementById:element,querySelectorAll(selector){const cls=selector.replace(/^\./,"");return all.filter(node=>node.className.split(/\s+/).includes(cls));}};
  const sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)};
  if(pending)sessionStorage.setItem("ariadne.routine.pending",JSON.stringify(pending));
  const state={authenticated,view:"workflows",workflowTab:"routines",status:{paused:false,routines:{active_runs:0,last_updated_at:1}}};
  const helpers={state,node:(...args)=>new Element(...args),icon:()=>new Element("svg"),
    button(text,cls,listener){const node=new Element("button",cls,text);node.addEventListener("click",listener);return node;},
    api:async(route,options={})=>{requests.push({route,options:clone(options)});return handler?handler(route,options,{storage}):clone(catalog());},
    confirmAction:confirm||(()=>Promise.resolve(true)),toast:value=>toasts.push(value),dateLabel:value=>String(value),bytes:value=>String(value),humanize:value=>String(value),openArtifact(){},downloadArtifact(){}};
  const sandbox={window:{},document,sessionStorage,crypto:{randomUUID:()=>"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},console,
    setTimeout(){throw Error("Unexpected timer");},setInterval(){throw Error("Unexpected interval");},fetch(){throw Error("Unexpected direct fetch");},requestAnimationFrame(){throw Error("Unexpected animation loop");}};
  vm.runInNewContext(source,sandbox,{filename:"routines.js"});
  const module=sandbox.window.ariadneRoutines;
  assert.equal(requests.length,0);
  module.attach(helpers);
  function find(id,text){const walk=node=>[node,...node.children.flatMap(walk)];const match=walk(element(id)).find(node=>node.tagName==="BUTTON"&&node.textContent===text);assert(match,`Missing ${text}`);return match;}
  return {module,state,document,element,requests,storage,toasts,find};
}
async function runChecks(){
  {
    const f=fixture();assert.equal(f.requests.length,0);checks.push("module and attach perform no I/O or independent timer work");
    await f.module.load();assert.equal(f.requests.length,1);
    f.module.status(f.state.status);await Promise.resolve();assert.equal(f.requests.length,1);checks.push("unchanged idle status does not fetch routine records");
    f.document.hidden=true;f.module.status({routines:{active_runs:1,last_updated_at:2}});await Promise.resolve();assert.equal(f.requests.length,1);
    f.document.hidden=false;f.state.workflowTab="missions";f.module.status({routines:{active_runs:1,last_updated_at:2}});await Promise.resolve();assert.equal(f.requests.length,1);checks.push("hidden document and other workflow tab do not poll routines");
    f.state.workflowTab="routines";f.module.status({routines:{active_runs:1,last_updated_at:2}});await new Promise(resolve=>setImmediate(resolve));assert.equal(f.requests.length,2);checks.push("existing status tick refreshes visible changed/active routines only");
  }
  {
    const wait=deferred();const f=fixture({handler:()=>wait.promise});const loading=f.module.load();
    f.state.authenticated=false;f.module.locked();wait.resolve(catalog());await loading;
    assert(!f.element("routine-list").textContent.includes("PRIVATE OWNER ROUTINE"));checks.push("late catalog response cannot repopulate private records after lock");
  }
  {
    const pending={requestId:"saved-request",routineId:"definition-1",name:"PRIVATE PENDING NAME"};const f=fixture({pending});await f.module.load();
    assert(f.element("routine-uncertain").textContent.includes(pending.name));
    f.state.authenticated=false;f.module.locked();assert.equal(f.element("routine-uncertain").textContent,"");assert(f.element("routine-uncertain").hidden);
    assert(f.storage.has("ariadne.routine.pending"));checks.push("lock clears pending private display while retaining reconciliation identity");
    f.state.authenticated=true;await f.module.load();assert(f.element("routine-uncertain").textContent.includes(pending.name));checks.push("pairing restores saved pending reconciliation without submitting a request");
  }
  {
    let posts=0;const f=fixture({handler:(route,options,{storage})=>{
      if(options.method==="POST"){posts++;const saved=JSON.parse(storage.get("ariadne.routine.pending"));assert.equal(saved.requestId,options.body.request_id);throw Error("Injected lost response");}
      if(route.startsWith("/routines/runs/"))return {run:{id:"aaaaaaaabbbbccccddddeeeeeeeeeeee",routine_id:"definition-1",name:"PRIVATE OWNER ROUTINE",status:"queued",steps:[],artifacts:[],model_calls:0}};
      return clone(catalog());}});
    await f.module.load();await f.find("routine-list","Run once").fire("click");await f.element("run-routine").fire("click");
    assert.equal(posts,1);assert(f.storage.has("ariadne.routine.pending"));await f.element("run-routine").fire("click");assert.equal(posts,1);checks.push("immutable ID is saved before POST and lost response never resubmits it");
    await f.find("routine-uncertain","Read this request").fire("click");assert.equal(posts,1);assert(!f.storage.has("ariadne.routine.pending"));assert(f.requests.at(-1).route.startsWith("/routines/runs/"));checks.push("lost POST reconciles through matching read-only receipt GET");
  }
  for(const mutation of ["remove","enable","cancel"]){
    const wait=deferred();const data=catalog();data.routines[0].last_error="Prior failure";
    data.runs=[{id:"running-1",routine_id:"definition-1",name:"Active fixture",status:"running",steps:[],artifacts:[],model_calls:0}];
    const f=fixture({handler:()=>clone(data),confirm:()=>wait.promise});await f.module.load();
    const action=mutation==="remove"?f.find("routine-list","Remove"):mutation==="enable"?f.find("routine-list","Review & resume"):f.find("routine-runs","Cancel remaining steps");
    const confirming=action.fire("click");await Promise.resolve();f.state.authenticated=false;f.module.locked();f.state.authenticated=true;wait.resolve(true);await confirming;
    assert.equal(f.requests.filter(request=>request.options.method).length,0);checks.push(`stale ${mutation} confirmation cannot mutate after lock and re-pair`);
  }
  {
    const data=catalog();data.routines[0].last_error="Server held recurrence";let puts=0;
    const f=fixture({handler:(route,options)=>{
      if(options.method==="PUT"){puts++;assert.equal(options.body.expected_revision,7);const error=new Error("Stale definition; refresh required");error.status=409;throw error;}
      return clone(data);}});
    await f.module.load();await f.find("routine-list","Review & resume").fire("click");
    assert.equal(puts,1);assert.equal(f.requests.length,2);assert.match(f.element("routine-error").textContent,/refresh/i);checks.push("recurrence toggle submits reviewed revision and never retries a 409");
  }
  {
    const old=catalog();old.routines[0].enabled=true;const held=clone(old);held.routines[0].revision=8;held.routines[0].enabled=false;held.routines[0].last_error="Substantive failure";
    let gets=0,puts=0;const f=fixture({handler:(route,options)=>{
      if(options.method==="PUT"){puts++;assert.equal(options.body.expected_revision,7);assert.equal(options.body.enabled,true);const error=new Error("Revision conflict");error.status=409;throw error;}
      return clone(++gets===1?old:held);}});
    await f.module.load();await f.find("routine-list","Edit").fire("click");await f.module.load();
    await f.element("routine-editor-form").fire("submit");assert.equal(puts,1);assert.equal(f.requests.length,3);
    assert.match(f.element("routine-editor-error").textContent,/refresh/i);assert(!f.element("routine-editor-error").hidden);checks.push("editor retains its opening revision across background failure refresh and never retries conflict");
  }
  {
    const first=deferred(),second=deferred();let gets=0;const f=fixture({handler:()=>++gets===1?first.promise:second.promise});
    const old=f.module.load();f.state.authenticated=false;f.module.locked();f.state.authenticated=true;
    const fresh=f.module.load();assert.equal(f.requests.length,2);first.resolve(catalog());await old;await f.module.load();assert.equal(f.requests.length,2);
    second.resolve(catalog());await fresh;checks.push("old async finally cannot clear the new session loading flag");
  }
  {
    const data=catalog();data.routines[0].name="<img src=x onerror=steal()>";const f=fixture({handler:()=>clone(data)});await f.module.load();
    assert(f.element("routine-list").textContent.includes(data.routines[0].name));checks.push("owner/server names remain literal text and no innerHTML sink is used");
  }
  for(const state of ["first_run","incomparable","current_unavailable","partial","compared"]){
    const data=catalog();data.runs=[{id:"current-fixture",routine_id:"definition-1",name:"Completed fixture",status:"completed",steps:[],artifacts:[],model_calls:0}];
    const result={state,current_id:"current-fixture",baseline_id:state==="first_run"?null:"baseline-fixture",steps:[],metadata_only:state==="compared",limits:{changes:100},truncation:{lower_bound:state==="partial"}};
    let comparisons=0;const f=fixture({handler:route=>route.endsWith("/changes")?(comparisons++,clone(result)):clone(data)});
    await f.module.load();assert.equal(comparisons,0);f.module.status(f.state.status);await Promise.resolve();assert.equal(comparisons,0);
    await f.find("routine-runs","Compare prior run").fire("click");assert.equal(comparisons,1);
    assert.equal(f.requests.at(-1).route,"/routines/runs/current-fixture/changes");assert.equal(f.requests.at(-1).options.method,undefined);
    const text=f.element("routine-runs").textContent;assert(text.includes("Current request: current-fixture"));
    assert(text.includes(`Baseline request: ${result.baseline_id||"None returned"}`));
    if(state==="partial")assert.match(text,/incomplete.*lower bounds/s);
    if(state==="compared")assert.match(text,/Only metadata changes found/);
    if(["first_run","incomparable","current_unavailable"].includes(state))assert.match(text,/does not establish.*unchanged/);
    checks.push(`manual comparison reports ${state} and exact baseline without background retrieval`);
  }
  {
    const data=catalog();data.runs=[{id:"current-fixture",routine_id:"definition-1",status:"completed",steps:[],artifacts:[],model_calls:0}];
    const changes=Array.from({length:20},(_,index)=>({pointer:`/rows/${index}`,kind:"changed",before:"<script>untrusted()</script>",after:"new",metadata:index%2===0}));
    const result={state:"partial",current_id:"current-fixture",baseline_id:"baseline-fixture",steps:[{procedure_id:"readiness_report",state:"partial",observed_differences:20,truncated:true,changes}],metadata_only:false,limits:{changes:100},truncation:{lower_bound:true}};
    const f=fixture({handler:route=>route.endsWith("/changes")?clone(result):clone(data)});await f.module.load();await f.find("routine-runs","Compare prior run").fire("click");
    const text=f.element("routine-runs").textContent;assert.equal((text.match(/Before:/g)||[]).length,12);assert.match(text,/Showing 12 of 20 returned differences/);
    assert(text.includes("<script>untrusted()</script>"));assert(text.includes('"lower_bound": true'));
    checks.push("comparison display caps twelve literal previews and preserves bounded metadata response");
  }
  {
    const data=catalog();data.runs=[{id:"current-fixture",routine_id:"definition-1",status:"completed",steps:[],artifacts:[],model_calls:0}];
    const wait=deferred();const f=fixture({handler:route=>route.endsWith("/changes")?wait.promise:clone(data)});await f.module.load();
    const request=f.find("routine-runs","Compare prior run").fire("click");f.state.authenticated=false;f.module.locked();
    wait.resolve({state:"compared",current_id:"current-fixture",baseline_id:"PRIVATE-BASELINE",steps:[],metadata_only:true});await request;
    assert(!f.element("routine-runs").textContent.includes("PRIVATE-BASELINE"));
    checks.push("late comparison cannot repopulate private results after locking");
  }
  {
    const data=catalog();data.runs=[{id:"current-fixture",routine_id:"definition-1",status:"completed",steps:[],artifacts:[],model_calls:0}];
    const f=fixture({handler:route=>route.endsWith("/changes")?{state:"compared",current_id:"wrong-id",baseline_id:"WRONG-BASELINE",steps:[]}:clone(data)});
    await f.module.load();await f.find("routine-runs","Compare prior run").fire("click");
    const text=f.element("routine-runs").textContent;assert.match(text,/No comparison is asserted/);assert(!text.includes("WRONG-BASELINE"));
    checks.push("comparison for a different request ID is rejected rather than displayed");
  }
  await checkWorkflowOutputContracts();
}
function section(name){const start=appSource.indexOf(`function ${name}(`);assert(start>=0,name);const next=appSource.slice(start+9).search(/\n(?:async )?function /);return next<0?appSource.slice(start):appSource.slice(start,start+9+next);}
async function checkWorkflowOutputContracts(){
  let captured;const prefix="missions/abc123",state={detail:{run:{title:"Snapshot fixture",agent_id:"offline_operations",isolated_outputs:true,output_prefix:prefix,
    checks:[{kind:"file_contains",path:`${prefix}/report.md`,text:`${prefix}/literal-evidence`},{kind:"file_exists",path:"other/report.md"}]},
    messages:[{role:"user",content:`Write ${prefix}/report.md. Preserve sibling ${prefix}-other/file.md.`}]}};
  const sandbox={state,checkDefinitions:items=>clone(items),openEditor:(kind,options)=>{captured={kind,...options};}};
  vm.runInNewContext(section("saveRunWorkflow"),sandbox);sandbox.saveRunWorkflow();
  assert.equal(captured.values.isolated_outputs,true);assert.equal(captured.values.agent_id,"offline_operations");
  assert.match(captured.values.prompt,/\{\{run_dir\}\}\/report.md/);assert(captured.values.prompt.includes(`${prefix}-other/file.md`));
  const saved=JSON.parse(captured.values.checks);assert.equal(saved[0].path,"{{run_dir}}/report.md");assert.equal(saved[0].text,`${prefix}/literal-evidence`);assert.equal(saved[1].path,"other/report.md");
  checks.push("save from isolated run reverses only its known output prefix in prompt/check paths");
  vm.runInNewContext(section("editWorkflow"),sandbox);sandbox.editWorkflow({id:"workflow-1",name:"Draft",agent_id:"offline_research",isolated_outputs:true,prompt:"Local",checks:[]},true);
  assert.equal(captured.values.isolated_outputs,true);assert.equal(captured.values.agent_id,"offline_research");assert.equal(captured.editId,null);
  checks.push("copy workflow preserves local specialist and checked-output isolation flag");
  for(const checked of [false,true]){
    const elements=new Map([["save-editor",{}],["editor-error",{}],["editor-dialog",{close(){}}],["editor-input-name",{value:"Fixture"}],["editor-input-checks",{value:"[]"}],["editor-input-isolated_outputs",{checked,value:"on"}]]);
    let body;const editor={state:{editor:{endpoint:"workflows",fields:[{name:"name"},{name:"checks"},{name:"isolated_outputs",type:"checkbox"}],extra:{}}},
      $:selector=>elements.get(selector.slice(1)),parseChecks:JSON.parse,api:async(route,options)=>{body=options.body;return {};},toast(){},loadView:async()=>{}};
    vm.runInNewContext("async "+section("saveEditor"),editor);await editor.saveEditor({preventDefault(){}});assert.equal(body.isolated_outputs,checked);
  }
  checks.push("workflow editor sends true and false checkbox values as booleans without requiring checked");
}
runChecks().then(()=>{
  const receipt={created_at_utc:new Date().toISOString(),source:"ariadne/static/routines.js",source_sha256:crypto.createHash("sha256").update(source).digest("hex"),app_sha256:crypto.createHash("sha256").update(appSource).digest("hex"),checks,passed:checks.length,failures:0,scope:"Minimal DOM VM only; no live keyboard/layout/screen-reader/browser certification."};
  if (receiptPath) { fs.mkdirSync(path.dirname(receiptPath),{recursive:true}); fs.writeFileSync(receiptPath,JSON.stringify(receipt,null,2)+"\n"); }
  console.log(JSON.stringify(receipt,null,2));
}).catch(error=>{console.error(error.stack);process.exitCode=1;});
