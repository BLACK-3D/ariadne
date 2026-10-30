"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), crypto = require("node:crypto");
const root = path.resolve(__dirname, "../.."), file = path.join(root, "ariadne/static/handoff.js"), source = fs.readFileSync(file, "utf8");
const copy = value => JSON.parse(JSON.stringify(value)), defer = () => { let resolve,reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const checks = [];
class Element {
  constructor(tag) {this.tagName=tag.toUpperCase();this.childNodes=[];this.listeners={};this.dataset={};this._text="";this.disabled=false;this.value="";this.checked=false;this.attrs={};}
  append(...items) {for(const item of items){this.childNodes.push(item);if(item instanceof Element)item.parent=this;}}
  replaceChildren(...items) {this.childNodes=[];this._text="";this.append(...items);}
  set textContent(value) {this.childNodes=[];this._text=String(value);}
  get textContent() {return this._text+this.childNodes.map(item=>item instanceof Element?item.textContent:String(item)).join("");}
  setAttribute(name,value) {this.attrs[name]=String(value);}
  addEventListener(type,callback) {(this.listeners[type]??=[]).push(callback);}
  async fire(type) {for(const callback of this.listeners[type]||[])await callback({preventDefault(){}});}
}
const walk = node => [node,...node.childNodes.flatMap(item=>item instanceof Element?walk(item):[])];
const row = (id="quote-one",held=false) => ({id,revision:1,kind:"quote",title:held?"Missing approved rate":"Four weekly office visits",company:"Example Co. (fictional)",
  customer:{name:"Example Office (fictional)",email:held?null:"office@example.invalid",address:"Fictional demonstration address"},
  document_date:"2026-10-30",service_date:"2026-11-01",due_date:"2026-11-15",source_notes:"Supplied fictional job notes — not a real customer.",
  lines:[{description:"Approved visit",quantity:4,unit_price:held?null:"125.00",price_approved:!held,source_ref:held?null:"DEMO-RATE",line_total_cents:held?null:"50000"}],
  tax:{bps:held?null:0,approved:!held,source_ref:held?null:"DEMO-TAX"},work:{completed:false,approved:false,source_ref:null},
  content_sha256:(held?"b":"a").repeat(64),subtotal_cents:held?null:"50000",tax_cents:held?null:"0",total_cents:held?null:"50000",
  exceptions:held?[{field:"unit_price",code:"missing_rate",message:"An approved unit price is missing."},{field:"tax",code:"missing_tax",message:"Tax policy is unknown."}]:[],status:held?"held":"ready_for_review",review:null});
const board = (initialized=true) => ({schema:1,revision:7,demo_only:true,initialized,summary:{records:initialized?2:0,ready_for_review:initialized?1:0,held:initialized?1:0,approved:0},records:initialized?[row(),row("held-one",true)]:[],audit:[]});
function harness(value=board()) {
  const body = new Element("main");for(const id of ["handoff-content","refresh-handoff"]){const node=new Element(id.includes("content")?"div":"button");node.id=id;body.append(node);}
  const document={getElementById:id=>walk(body).find(item=>item.id===id)||null};
  const state={authenticated:true,token:"owner-key",status:{paused:false}},calls=[],toasts=[],artifacts=[],downloads=[];
  let response=copy(value),handler=null;
  const app={state,node:(tag,cls,text)=>{const node=new Element(tag);node.className=cls;if(text!==undefined&&text!==null)node.textContent=text;return node;},button:(label,cls,callback)=>{const node=new Element("button");node.className=cls;node.textContent=label;node.addEventListener("click",callback);return node;},
    api:async(p,o)=>{calls.push({path:p,options:o});return handler?handler(p,o):copy(response);},toast:v=>toasts.push(v),openArtifact:v=>artifacts.push(v),downloadArtifact:v=>downloads.push(v),confirmAction:async()=>true};
  const box={window:{},document,Date,BigInt,Map,Object,Number,crypto:{randomUUID:crypto.randomUUID},console};vm.createContext(box);vm.runInContext(source,box,{filename:file});
  const api=box.window.ariadneHandoff;api.attach(app);
  return {api,app,body,document,calls,toasts,artifacts,downloads,get response(){return response;},set response(v){response=v;},set handler(v){handler=v;},all:()=>walk(body),find:label=>walk(body).find(item=>item.tagName==="BUTTON"&&item.textContent===label)};
}
const fieldMap = record => ({title:record.title,customer:record.customer.name,email:record.customer.email||"",address:record.customer.address,
  "service-date":record.service_date,"document-date":record.document_date,"due-date":record.due_date,
  "description-0":record.lines[0].description,"quantity-0":"4","rate-0":"125.00","source-0":"DEMO-RATE","approved-0":true,
  tax:"0.00","tax-approved":true,"tax-source":"DEMO-TAX",completed:false,"work-approved":false,"work-source":""});
const check = (name,fn) => Promise.resolve().then(fn).then(()=>checks.push(name));
async function main() {
  await check("Opening an empty desk is read-only and never seeds fictional data",async()=>{
    const h=harness(board(false));await h.api.load();assert.deepEqual(h.calls,[{path:"/handoff",options:undefined}]);assert(h.find("Load fictional examples"));assert.match(h.body.textContent,/Load the sample only when you choose/);
  });
  await check("The visible service separates supplied inputs, owner review and unsent drafts",async()=>{
    const h=harness();await h.api.load();assert.match(h.body.textContent,/client supplies the job details and approved prices/);assert.match(h.body.textContent,/No client contacted/);assert.match(h.body.textContent,/FICTIONAL DEMONSTRATION/);assert.match(h.body.textContent,/not sales, receivables or earned revenue/);assert.match(h.body.textContent,/\$500\.00/);assert.match(h.body.textContent,/YOUR CLIENT/);assert.match(h.body.textContent,/THEIR CUSTOMER/);assert.match(h.body.textContent,/These prices are untested/);assert.match(h.body.textContent,/they are not your service revenue/);
    const model=h.all().find(node=>node.tagName==="DETAILS"&&node.childNodes[0]?.textContent==="How this service works");assert(model);assert.notEqual(model.open,true);assert.match(h.body.textContent,/Incomplete or unreviewed records are excluded/);assert.doesNotMatch(h.body.textContent,/source checks and held exceptions/);
  });
  await check("Integer-cent rendering preserves large values and does not turn null into zero",()=>{
    const h=harness();assert.equal(h.api.money("1000000000000001"),"$10,000,000,000,000.01");assert.equal(h.api.money(0),"$0.00");for(const value of [null,undefined,-1,"-1","12.5",NaN,{},true])assert.equal(h.api.money(value),"Awaiting input");
  });
  await check("A held record shows unknown tax and rate, and cannot be approved",async()=>{
    const h=harness();await h.api.load();const queue=h.all().find(item=>item.attrs["aria-label"]?.startsWith("Missing approved rate"));await queue.fire("click");assert.match(h.body.textContent,/Tax — input required/);assert.match(h.body.textContent,/An approved unit price is missing/);assert.match(h.body.textContent,/Draft totalHELD/);assert(h.find("Approve this demo draft").disabled);await h.api.approve(h.response.records[1]);assert.equal(h.calls.length,1);
  });
  await check("Exception headings explain required inputs without exposing JSON field paths",async()=>{
    const value=board();value.records[1].exceptions=[{field:"lines.0.unit_price",message:"An approved rate is missing."},{field:"lines.0.source_ref",message:"Supply the approved rate source."},{field:"tax",message:"Tax policy is unknown."},{field:"work",message:"Completion evidence is missing."}];const h=harness(value);await h.api.load();await h.all().find(item=>item.attrs["aria-label"]?.startsWith("Missing approved rate")).fire("click");assert.match(h.body.textContent,/Line 1 · approved price/);assert.match(h.body.textContent,/Line 1 · price source/);assert.match(h.body.textContent,/Tax instruction/);assert.match(h.body.textContent,/Completed-work evidence/);assert.doesNotMatch(h.body.textContent,/lines\.0|unit_price|source_ref/);assert.equal(h.api.exceptionLabel("customer.name"),"Customer name");assert.equal(h.api.exceptionLabel("document_date"),"Document date");assert.equal(h.api.exceptionLabel("unexpected.internal.path"),"Owner input required");
  });
  await check("Approval sends only the exact record hash and the observed CAS revision",async()=>{
    const h=harness();await h.api.load();await h.find("Approve this demo draft").fire("click");const write=h.calls.find(call=>call.options?.method==="POST");assert.equal(write.path,"/handoff/review");assert.equal(write.options.body.expected_revision,7);assert.equal(write.options.body.record_id,"quote-one");assert.equal(write.options.body.content_sha256,"a".repeat(64));assert.equal(write.options.body.decision,"approve");assert.equal(Object.keys(write.options.body).length,5);assert.match(h.toasts[0],/Nothing sent or posted/);
  });
  await check("Review eligibility rejects incomplete, non-demo and paused snapshots",()=>{
    const h=harness(),r=row(),b=board();assert(h.api.canReview(r,b,false));for(const changed of [{...r,status:"held"},{...r,exceptions:[{}]},{...r,content_sha256:"broken"},{...r,status:"approved"}])assert.equal(h.api.canReview(changed,b,false),false);assert.equal(h.api.canReview(r,{...b,demo_only:false},false),false);assert.equal(h.api.canReview(r,b,true),false);
  });
  await check("Missing optional customer fields remain null in an editable held-record payload",()=>{
    const h=harness(),r=row("held-one",true),fields=fieldMap(r);const body=copy(h.api.editPayload(r,fields,7,"edit-id"));assert.equal(body.changes.customer.email,null);assert.equal(body.changes.lines[0].unit_price,"125.00");assert.equal(body.changes.tax.bps,0);assert.equal(body.changes.lines[0].line_total_cents,undefined);assert.equal(body.decision,undefined);assert.equal(body.content_sha256,"b".repeat(64));
  });
  await check("Tax percentage conversion is exact, blank stays unknown, and invalid quantities fail",()=>{
    const h=harness();for(const [input,expected] of [["8.25",825],["0",0],["100.00",10000],["",null]])assert.equal(h.api.taxBps(input),expected);for(const input of ["-1","0.001","100.01","Infinity","8,25"])assert.throws(()=>h.api.taxBps(input));const r=row(),fields=fieldMap(r);for(const quantity of ["0","1.5","-2","NaN"])assert.throws(()=>h.api.editPayload(r,{...fields,"quantity-0":quantity},7,"edit-id"));
  });
  await check("A form correction uses PUT and preserves absent email for backend compatibility",async()=>{
    const h=harness();await h.api.load();await h.all().find(item=>item.attrs["aria-label"]?.startsWith("Missing approved rate")).fire("click");const form=h.find("Save fictional inputs and recheck").parent;const fields=walk(form).filter(node=>node.tagName==="INPUT");fields.find(item=>item.name==="rate-0").value="125.00";fields.find(item=>item.name==="approved-0").checked=true;fields.find(item=>item.name==="source-0").value="DEMO-NEW-RATE";await form.fire("submit");const write=h.calls.find(call=>call.options?.method==="PUT");assert.equal(write.path,"/handoff/record");assert.equal(write.options.body.changes.customer.email,null);assert.equal(write.options.body.changes.tax.bps,null);assert.equal(write.options.body.changes.lines[0].source_ref,"DEMO-NEW-RATE");assert.equal(write.options.body.expected_revision,7);
  });
  await check("More than eight audit entries retain the latest eight instead of the oldest",async()=>{
    const value=board();value.audit=Array.from({length:12},(_,i)=>({request_id:`request-${12-i}`,action:`action-${12-i}`,created_at:"2026-10-30T05:00:00Z"}));const h=harness(value);await h.api.load();assert.match(h.body.textContent,/request-12/);assert.match(h.body.textContent,/request-5/);assert.doesNotMatch(h.body.textContent,/request-4(?!\d)/);
  });
  await check("Untrusted customer/source text stays literal and cannot create active markup",async()=>{
    const value=board();value.records[0].customer.name="<svg onload=bad()>";value.records[0].source_notes="<script>never run</script>";const h=harness(value);await h.api.load();assert.match(h.body.textContent,/<svg onload=bad\(\)>/);assert.match(h.body.textContent,/<script>never run<\/script>/);assert(!h.all().some(item=>["SCRIPT","SVG","IMG"].includes(item.tagName)));
  });
  await check("A duplicate seed click while a request is pending submits once",async()=>{
    const h=harness(board(false));await h.api.load();const pending=defer();h.handler=()=>pending.promise;const one=h.api.seed();await h.api.seed();assert.equal(h.calls.length,2);h.handler=()=>copy(board());pending.resolve(board());await one;assert.equal(h.calls.filter(item=>item.options?.method==="POST").length,1);
  });
  await check("An uncertain write is held, inspected and never automatically retried",async()=>{
    const h=harness();await h.api.load();h.handler=()=>{throw new Error("connection interrupted");};await h.api.approve(h.response.records[0]);await h.api.approve(h.response.records[0]);assert.equal(h.calls.length,2);assert.match(h.body.textContent,/Nothing is retried automatically/);assert(h.find("I inspected the saved state").disabled);h.handler=()=>copy(h.response);await h.api.load();assert.equal(h.find("I inspected the saved state").disabled,false);assert.equal(h.calls.filter(item=>item.options).length,1);
  });
  await check("CAS conflict disables writes until a fresh read without replaying the request",async()=>{
    const h=harness();await h.api.load();h.handler=()=>{throw Object.assign(new Error("revision changed"),{status:409});};await h.api.approve(h.response.records[0]);assert(h.find("Approve this demo draft").disabled);await h.api.approve(h.response.records[0]);assert.equal(h.calls.length,2);h.handler=()=>copy(h.response);await h.api.load();assert.equal(h.find("Approve this demo draft").disabled,false);
  });
  await check("Global pause disables seed, review, edit and export without changing source inputs",async()=>{
    const value=board();value.summary.approved=1;const h=harness(value);await h.api.load();h.app.state.status.paused=true;h.api.status();assert(h.find("Approve this demo draft").disabled);assert(h.find("Save fictional inputs and recheck").disabled);assert(h.find("Create reviewed handoff packet").disabled);await h.api.approve(value.records[0]);await h.api.exportPacket();assert.equal(h.calls.length,1);
  });
  await check("Pairing changes discard late private reads",async()=>{
    const h=harness(),pending=defer();h.handler=()=>pending.promise;const one=h.api.load();h.app.state.authenticated=false;h.api.locked();pending.resolve(board());await one;assert.equal(h.document.getElementById("handoff-content").textContent,"");assert.equal(h.calls.length,1);
  });
  await check("Pairing changes discard late mutation replies, toasts and artifact URLs",async()=>{
    const value=board();value.summary.approved=1;const h=harness(value);await h.api.load();const pending=defer();h.handler=()=>pending.promise;const one=h.api.exportPacket();h.app.state.token="new-owner-key";h.api.locked();pending.resolve({status:"completed",artifacts:[{path:"handoff-demo/example/packet.html"}]});await one;assert.equal(h.toasts.length,0);assert.equal(h.calls.length,2);assert.equal(h.document.getElementById("handoff-content").textContent,"");
  });
  await check("Verified saved packet paths use the existing local download and artifact helpers",async()=>{
    const value=board();value.latest_export={status:"completed",artifacts:[{path:"handoff-demo/export-one/packet.html",sha256:"c".repeat(64)},{path:"handoff-demo/export-one/packet.json",sha256:"d".repeat(64)}]};const h=harness(value);await h.api.load();await h.find("Download printable handoff").fire("click");await h.find("Inspect handoff data").fire("click");assert.deepEqual(h.downloads,["handoff-demo/export-one/packet.html"]);assert.deepEqual(h.artifacts,["handoff-demo/export-one/packet.json"]);for(const value of ["E:/secret","../escape","https://example.invalid","/absolute","a//b"])assert.throws(()=>h.api.relativePath(value));
  });
  await check("An unresolved export hold blocks new exports while leaving draft review available",async()=>{
    const value=board();value.summary.approved=1;value.export_hold={request_id:"failed-export",status:"failed",reason:"Publication interrupted"};const h=harness(value);await h.api.load();assert(h.find("Create reviewed handoff packet").disabled);assert.equal(h.find("Approve this demo draft").disabled,false);await h.api.exportPacket();assert.equal(h.calls.length,1);assert.match(h.body.textContent,/No partial output will be adopted or retried/);await h.find("Acknowledge inspected export hold").fire("click");const write=h.calls.find(item=>item.options);assert.equal(write.path,"/handoff/export/acknowledge");assert.equal(write.options.body.request_id,"failed-export");assert.equal(write.options.body.expected_revision,7);
  });
  await check("An export-hold confirmation cannot cross a pairing change",async()=>{
    const value=board();value.export_hold={request_id:"failed-export",status:"failed",reason:"Interrupted"};const h=harness(value);await h.api.load();const pending=defer();h.app.confirmAction=()=>pending.promise;const one=h.api.acknowledgeHold();h.app.state.token="replacement-owner";h.api.locked();pending.resolve(true);await one;assert.equal(h.calls.length,1);
  });
  await check("Failed or integrity-invalid exports never expose packet artifact links",async()=>{
    for(const status of ["running","failed","acknowledged","integrity_error"]){const value=board();value.latest_export={status,artifacts:[{path:"handoff-demo/partial/packet.html",sha256:"a".repeat(64)}]};const h=harness(value);await h.api.load();assert.equal(h.find("Download printable handoff"),undefined);}
  });
  await check("A fresh server inspection supersedes a cached export reply after an integrity failure",async()=>{
    const value=board();value.summary.approved=1;const h=harness(value);await h.api.load();h.handler=(p,o)=>o?{status:"completed",artifacts:[{path:"handoff-demo/export-one/packet.html",sha256:"a".repeat(64)}]}:{...value,latest_export:{status:"integrity_error",artifacts:[]}};await h.api.exportPacket();assert.equal(h.find("Download printable handoff"),undefined);
  });
  await check("The module has no auto-poll, markup execution, external dependencies or sending endpoint",()=>{
    assert.doesNotMatch(source,/\.innerHTML\s*=|\beval\s*\(|setInterval\s*\(|setTimeout\s*\(|https?:\/\//);assert.doesNotMatch(source,/\/send|\/post-invoice|\/payments|\/business_records/);
    const css=fs.readFileSync(path.join(root,"ariadne/static/handoff.css"),"utf8");assert.match(css,/@media\(max-width:480px\)/);assert.match(css,/focus-visible/);assert.doesNotMatch(css,/@import|url\(/);
  });
  console.log(JSON.stringify({passed:checks.length,failures:0,checks,source_sha256:crypto.createHash("sha256").update(source).digest("hex")},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
