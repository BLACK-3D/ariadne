"use strict";
// Isolated DOM/API contracts: no owner records, browser, model or network access.
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm"),crypto=require("node:crypto");
const root=path.resolve(__dirname,"../.."),file=path.join(root,"ariadne/static/client_handoff.js"),source=fs.readFileSync(file,"utf8");
const copy=value=>JSON.parse(JSON.stringify(value)),checks=[];
const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.childNodes=[];this.listeners={};this.dataset={};this.attrs={};this._text="";this.value="";this.checked=false;this.disabled=false;}
  append(...items){for(const item of items){this.childNodes.push(item);if(item instanceof Element)item.parent=this;}}
  replaceChildren(...items){this.childNodes=[];this._text="";this.append(...items);}
  set textContent(value){this.childNodes=[];this._text=String(value);}
  get textContent(){return this._text+this.childNodes.map(item=>item instanceof Element?item.textContent:String(item)).join("");}
  setAttribute(key,value){this.attrs[key]=String(value);}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  async fire(type){for(const fn of this.listeners[type]||[])await fn({preventDefault(){}});}
}
const walk=node=>[node,...node.childNodes.flatMap(item=>item instanceof Element?walk(item):[])];
const profile=(id="client-one")=>({id,revision:1,name:"QA Workshop (test fixture)",address:"1 Test Fixture Lane",email:"workshop@example.invalid",contact:"Fixture owner",notes:"Private fixture-only note",archived:false});
const job=()=>({id:"job-one",client_id:"client-one",provider:profile(),revision:1,content_sha256:"b".repeat(64),title:"Repair visit (fixture)",customer:{name:"Test Customer",email:null,address:"2 Fixture Way"},source_notes:"Supplied test notes\nNo real customer.",service_date:"2026-10-29",document_date:"2026-10-29",due_date:"2026-11-10",lines:[{description:"Repair labour",quantity:"2.5",unit:"hours",unit_price:"40.00",price_approved:true,source_ref:"FIXTURE-RATE"}],tax:{bps:0,approved:true,source_ref:"FIXTURE-TAX"},permission:{confirmed:true,source_ref:"FIXTURE-ENGAGEMENT"},can_generate_quote:true,created_at:"2026-10-29T18:00:00Z"});
const doc=(overrides={})=>({...job(),id:"Q-000001",reference:"Q-000001",kind:"quote",intake_id:"job-one",parent_quote_id:null,work:{completed:false,approved:false,source_ref:null},content_sha256:"a".repeat(64),lines:job().lines.map(row=>({...row,line_total_cents:"10000"})),status:"ready_for_review",exceptions:[],subtotal_cents:"10000",tax_cents:"0",total_cents:"10000",review:null,acceptance:null,invoice_id:null,voided:null,can_edit:true,can_accept:false,can_invoice:false,can_void:true,...overrides});
const summary=row=>({id:row.id,reference:row.reference,kind:row.kind,title:row.title,client_id:row.client_id,intake_id:row.intake_id,parent_quote_id:row.parent_quote_id,revision:row.revision,content_sha256:row.content_sha256,status:row.status,total_cents:row.total_cents,provider_name:row.provider.name,customer_name:row.customer.name,voided:row.voided,invoice_id:row.invoice_id,can_edit:row.can_edit,can_accept:row.can_accept,can_invoice:row.can_invoice,can_void:row.can_void});
const board=(populated=true)=>({schema:1,revision:7,demo_only:false,workflow_timezone:"America/Chicago",clients:populated?[profile()]:[],intakes:populated?[job()]:[],documents:populated?[summary(doc())]:[],totals:{clients:populated?1:0,intakes:populated?1:0,documents:populated?1:0},summary:{ready_for_review:populated?1:0},summary_scope:"current_document_page",pagination:{limit:50,clients:{offset:0,total:populated?1:0,has_more:false},intakes:{offset:0,total:populated?1:0,has_more:false},documents:{offset:0,total:populated?1:0,has_more:false}},audit:[],latest_export:null,export_hold:null,model_calls:0,network_calls:0});
function harness(value=board()) {
  const body=new Element("main");for(const id of ["client-handoff-content","refresh-client-handoff"]){const el=new Element(id.includes("content")?"div":"button");el.id=id;body.append(el);}
  const document={getElementById:id=>walk(body).find(el=>el.id===id)||null};
  const state={authenticated:true,token:"owner-fixture-token",status:{paused:false}},calls=[],toasts=[],artifacts=[],downloads=[],confirmations=[];
  let response=copy(value),handler=null;const details={clients:{"client-one":profile()},intakes:{"job-one":job()},documents:{"Q-000001":doc()}};
  const defaultGet=p=>{const base=p.split("?")[0],match=/^\/service-desk\/(clients|intakes|documents)\/([^/]+)$/.exec(base);if(match)return {revision:response.revision,[{clients:"client",intakes:"intake",documents:"document"}[match[1]]]:copy(details[match[1]][decodeURIComponent(match[2])])};return copy(response);};
  const defaultReply=(p,o)=>o?{status:p.endsWith("/export/acknowledge")?"acknowledged":"completed",revision:response.revision,request_id:o.body.request_id,entity_id:o.body.client?.id||o.body.intake?.id||o.body.document_id||o.body.quote_id||"Q-000001",entity_type:o.body.client?"client":o.body.intake?"intake":"document"}:defaultGet(p);
  const app={state,node:(tag,cls,text)=>{const el=new Element(tag);el.className=cls;if(text!==undefined&&text!==null)el.textContent=text;return el;},button:(label,cls,fn)=>{const el=new Element("button");el.className=cls;el.textContent=label;el.addEventListener("click",fn);return el;},
    api:async(p,o)=>{calls.push({path:p,options:o});return handler?handler(p,o):defaultReply(p,o);},toast:text=>toasts.push(text),downloadArtifact:p=>downloads.push(p),openArtifact:p=>artifacts.push(p),confirmAction:async(...args)=>{confirmations.push(args);return true;}};
  const box={window:{document},document,Date,BigInt,Map,Object,Number,crypto:{randomUUID:crypto.randomUUID},console};vm.createContext(box);vm.runInContext(source,box,{filename:file});const api=box.window.ariadneClientHandoff;api.attach(app);
  return {api,app,body,document,calls,toasts,artifacts,downloads,confirmations,details,defaultGet,defaultReply,get response(){return response;},set response(v){response=v;},set handler(v){handler=v;},all:()=>walk(body),find:label=>walk(body).find(el=>el.tagName==="BUTTON"&&el.textContent===label),writes:()=>calls.filter(call=>call.options),async openDoc(row=doc()){details.documents[row.id]=copy(row);response.documents=[summary(row)];await api.load();await walk(body).find(el=>el.attrs["aria-label"]===`Open ${row.reference||row.kind} ${row.title}`).fire("click");}};
}
const formOf=button=>{for(let node=button;node;node=node.parent)if(node.tagName==="FORM")return node;throw new Error("Missing enclosing form");};
const input=(scope,name)=>walk(scope).find(el=>["INPUT","SELECT","TEXTAREA"].includes(el.tagName)&&el.name===name);
const set=async(scope,name,value)=>{const el=input(scope,name);assert(el,`Missing field ${name}`);if(typeof value==="boolean")el.checked=value;else el.value=value;await el.fire("input");};
const fields=()=>({title:"Supplied fixture job",customer:"Fixture customer",email:"",address:"3 Fixture Street",source_notes:"Job note\nRate supplied by client.",service_date:"2026-10-29",document_date:"2026-10-29",due_date:"2026-11-10",lines:[{description:"Supplied work",quantity:"2.5",unit:"hours",unit_price:"40.00",price_approved:true,source_ref:"RATE-REF"}],tax:"",tax_approved:false,tax_source:"",permission_confirmed:false,permission_source:""});
const check=async(name,fn)=>{await fn();checks.push(name);};
async function main(){
  await check("Real workspace starts empty, read-only and separate from the fictional demonstration",async()=>{
    const h=harness(board(false));await h.api.load();assert.deepEqual(h.calls,[{path:"/service-desk",options:undefined}]);assert(h.find("Save new service client"));assert.equal(input(h.body,"name").value,"");assert.match(h.body.textContent,/fictional demonstration stays in its own workspace/);assert.doesNotMatch(h.body.textContent,/Example Co|QA Workshop|DEMO-RATE/);assert.match(h.body.textContent,/Approval does not send/);assert.equal(h.writes().length,0);
  });
  await check("Selected client and job lists are read with an explicit server filter and full client detail",async()=>{
    const h=harness();await h.api.load();assert(h.calls.some(call=>call.path==="/service-desk?client_id=client-one"));assert(h.calls.some(call=>call.path==="/service-desk/clients/client-one"));assert.equal(input(h.body,"address").value,"1 Test Fixture Lane");assert.match(h.body.textContent,/Ready on this page/);
  });
  await check("Currency rendering uses exact integer cents and preserves unknown input",()=>{
    const h=harness();assert.equal(h.api.money("1000000000000001"),"$10,000,000,000,000.01");assert.equal(h.api.money("0"),"$0.00");for(const value of [null,undefined,0,-1,"1.5","-2",true,{}])assert.equal(h.api.money(value),"Awaiting input");
  });
  await check("Decimal hours stay strings, support thousandths and reject rounding or out-of-bound quantities",()=>{
    const h=harness();for(const quantity of ["2.5","0.001","10000.000"]){const line=h.api.linePayload({...fields().lines[0],quantity});assert.equal(line.quantity,quantity);assert.equal(typeof line.quantity,"string");}for(const quantity of ["0","0.000","1.0001","10000.001","10001","01","1e3","-1","NaN",2.5])assert.throws(()=>h.api.linePayload({...fields().lines[0],quantity}));assert.throws(()=>h.api.linePayload({...fields().lines[0],unit_price:"12.345"}));
  });
  await check("Blank tax, unchecked permission and absent customer email remain unresolved",()=>{
    const h=harness(),payload=copy(h.api.intakePayload(fields(),7,"intake-request","job-new","client-one"));assert.equal(payload.intake.tax.bps,null);assert.equal(payload.intake.tax.approved,false);assert.equal(payload.intake.permission.confirmed,false);assert.equal(payload.intake.permission.source_ref,null);assert.equal(payload.intake.customer.email,null);assert.equal(payload.intake.lines[0].unit_price,"40.00");assert.equal(payload.intake.lines[0].quantity,"2.5");assert.equal(payload.expected_revision,7);
  });
  await check("Tax percentage conversion is exact and never defaults an unknown to zero",()=>{
    const h=harness();for(const [raw,expected]of [["8.25",825],["0",0],["100.00",10000],["",null]])assert.equal(h.api.taxBps(raw),expected);for(const raw of ["0.001","100.01","-1","8,25","Infinity"])assert.throws(()=>h.api.taxBps(raw));
  });
  await check("A blank profile can be completed through human fields and saved with CAS",async()=>{
    const h=harness(board(false));await h.api.load();const form=formOf(h.find("Save new service client"));for(const[name,value]of Object.entries({name:"Workshop Fixture",contact:"Fixture Owner",email:"owner@example.invalid",address:"4 Fixture Ave",notes:"Private note"}))await set(form,name,value);
    h.handler=(p,o)=>{if(!o)return h.defaultGet(p);const saved={...copy(o.body.client),revision:1};h.response.clients=[saved];h.details.clients[saved.id]=saved;return {...h.defaultReply(p,o),entity_id:saved.id};};await form.fire("submit");const write=h.writes()[0];assert.equal(write.path,"/service-desk/clients");assert.equal(write.options.method,"POST");assert.equal(write.options.body.client.name,"Workshop Fixture");assert.equal(write.options.body.client.archived,false);assert.equal(write.options.body.expected_revision,7);assert(!("decision"in write.options.body));
  });
  await check("Unsaved line additions preserve typed fields and create no server mutations",async()=>{
    const h=harness();await h.api.load();let form=formOf(h.find("Save job intake"));await set(form,"title","Unsent local fixture");await set(form,"quantity","2.5");await h.find("Add service line").fire("click");form=formOf(h.find("Save job intake"));assert.equal(input(form,"title").value,"Unsent local fixture");assert.equal(walk(form).filter(el=>el.name==="quantity").length,2);assert.equal(input(form,"quantity").value,"2.5");await h.find("Remove line 2").fire("click");assert.equal(walk(formOf(h.find("Save job intake"))).filter(el=>el.name==="quantity").length,1);assert.equal(h.writes().length,0);
  });
  await check("Saved client profile changes cannot silently overwrite an open local draft",async()=>{
    const h=harness();await h.api.load();await set(formOf(h.find("Save client profile")),"name","Unsaved name");h.details.clients["client-one"].revision=2;h.details.clients["client-one"].address="Changed in another owner session";await h.api.load();assert(h.find("Save client profile").disabled);assert.match(h.body.textContent,/saved client profile changed/);await h.find("Discard local client edits").fire("click");assert.equal(input(formOf(h.find("Save client profile")),"address").value,"Changed in another owner session");assert.equal(h.find("Save client profile").disabled,false);
  });
  await check("Adding another client clears previous job and document context without seeding",async()=>{
    const h=harness();await h.openDoc();await h.find("Add another service client").fire("click");assert(h.find("Save new service client"));assert.equal(input(h.body,"name").value,"");assert.equal(h.find("Approve exact draft"),undefined);assert.equal(h.writes().length,0);
  });
  await check("Generating a quote uses the exact immutable intake hash once",async()=>{
    const h=harness();await h.api.load();await h.all().find(el=>el.attrs["aria-label"]==="Open job Repair visit (fixture)").fire("click");await h.find("Generate quote draft").fire("click");const write=h.writes()[0];assert.equal(write.path,"/service-desk/quotes");assert.equal(write.options.body.intake_id,"job-one");assert.equal(write.options.body.content_sha256,"b".repeat(64));assert.equal(write.options.body.expected_revision,7);assert.equal(Object.keys(write.options.body).length,4);
  });
  await check("Internal review, customer acceptance and completed work are separate visible stages",async()=>{
    const h=harness();await h.openDoc();assert.match(h.body.textContent,/1 \/ INTERNAL DOCUMENT REVIEW/);assert.match(h.body.textContent,/2 \/ CUSTOMER RESPONSE/);assert.match(h.body.textContent,/3 \/ COMPLETED WORK/);assert.match(h.body.textContent,/owner-reported/);assert(h.find("Record customer response").disabled);assert(h.find("Generate invoice draft from accepted quote").disabled);assert.match(h.body.textContent,/No ledger posting or payment/);
  });
  await check("Held or invoiced quotes cannot be approved or rejected through a stale button",async()=>{
    const h=harness(),held=doc({status:"held",total_cents:null,exceptions:[{field:"lines.0.unit_price",message:"Approved rate missing"}]});await h.openDoc(held);assert(h.find("Approve exact draft").disabled);await h.api.review(held,"approve","");assert.equal(h.writes().length,0);await h.openDoc(doc({invoice_id:"I-000001",can_edit:false,can_void:false}));assert(h.find("Approve exact draft").disabled);assert(h.find("Reject draft with note").disabled);await h.api.review(h.details.documents["Q-000001"],"reject","Superseded");assert.equal(h.writes().length,0);
  });
  await check("Internal approval sends only its exact hash, document and observed revision",async()=>{
    const h=harness();await h.openDoc();await h.find("Approve exact draft").fire("click");const write=h.writes()[0];assert.equal(write.path,"/service-desk/review");assert.equal(write.options.body.document_id,"Q-000001");assert.equal(write.options.body.content_sha256,"a".repeat(64));assert.equal(write.options.body.expected_revision,7);assert.equal(write.options.body.decision,"approve");assert.equal(write.options.body.accepted,undefined);assert.match(h.toasts[0],/Nothing sent, posted or paid/);
  });
  await check("Reject requires a note and exact rejected drafts can explicitly return to review",async()=>{
    const h=harness();await h.openDoc();await h.find("Reject draft with note").fire("click");assert.equal(h.writes().length,0);assert.match(h.body.textContent,/Rejection note needs/);assert.equal(h.api.canReview(doc({status:"rejected"}),"approve"),true);assert.equal(h.api.canReview(doc({status:"held",exceptions:[{}]}),"approve"),false);
  });
  await check("Customer response requires explicit choice, contact, evidence and nonfuture observation",()=>{
    const h=harness(),row=doc({status:"approved",can_accept:true}),f={response:"accepted",contact:"Fixture Customer",source_ref:"CUSTOMER-EMAIL-REF",observed_at:"2026-10-29T17:30:00Z",note:"Supplied record"},now=Date.parse("2026-10-30T06:00:00Z");const payload=copy(h.api.acceptancePayload(row,f,7,"response-one",now));assert.equal(payload.accepted,true);assert.equal(payload.observed_at,"2026-10-29T17:30:00.000Z");assert.equal(payload.contact,"Fixture Customer");assert.equal(payload.content_sha256,"a".repeat(64));for(const changed of [{response:""},{contact:""},{source_ref:""},{observed_at:""},{observed_at:"2026-10-31T00:00:00Z"}])assert.throws(()=>h.api.acceptancePayload(row,{...f,...changed},7,"response-one",now));assert.equal(h.api.acceptancePayload(row,{...f,response:"declined"},7,"response-two",now).accepted,false);
  });
  await check("Invoice creation requires distinct checked work evidence and never overrides accepted prices",()=>{
    const h=harness(),row=doc({status:"approved",can_invoice:true}),f={service_date:"2026-10-29",document_date:"2026-10-30",due_date:"2026-11-10",completed:true,work_approved:true,work_source:"COMPLETION-REF"};const payload=copy(h.api.invoicePayload(row,f,7,"invoice-one"));assert.equal(payload.quote_id,"Q-000001");assert.equal(payload.content_sha256,"a".repeat(64));assert.deepEqual(payload.work,{completed:true,approved:true,source_ref:"COMPLETION-REF"});for(const forbidden of ["customer","provider","lines","tax","permission"])assert.equal(payload[forbidden],undefined);for(const changed of [{completed:false},{work_approved:false},{work_source:""},{document_date:""}])assert.throws(()=>h.api.invoicePayload(row,{...f,...changed},7,"invoice-one"));assert.throws(()=>h.api.invoicePayload({...row,kind:"invoice"},f,7,"invoice-one"));
  });
  await check("Invoice correction exposes only dates/work/title/notes and explains accepted-source immutability",async()=>{
    const h=harness(),row=doc({id:"I-000001",reference:"I-000001",kind:"invoice",parent_quote_id:"Q-000001",status:"ready_for_review",work:{completed:true,approved:true,source_ref:"COMPLETED"}});await h.openDoc(row);const form=formOf(h.find("Save corrections and require new review"));for(const name of ["customer","email","address","quantity","unit_price","tax","permission_confirmed"])assert.equal(input(form,name),undefined);assert.match(h.body.textContent,/void the unsent invoice and revise the quote/);await set(form,"title","Corrected fixture invoice");await form.fire("submit");const write=h.writes()[0];assert.equal(write.options.method,"PUT");assert.deepEqual(Object.keys(write.options.body.changes).sort(),["document_date","due_date","service_date","source_notes","title","work"]);assert.equal(h.find("Record customer response"),undefined);
  });
  await check("Document corrections hold when another session changes their saved content hash",async()=>{
    const h=harness();await h.openDoc();await set(formOf(h.find("Save corrections and require new review")),"title","Unsent change");h.details.documents["Q-000001"].content_sha256="c".repeat(64);await h.api.load();assert(h.find("Save corrections and require new review").disabled);assert.match(h.body.textContent,/saved document changed while these local edits/);await h.find("Discard local edits").fire("click");assert.equal(h.find("Save corrections and require new review").disabled,false);
  });
  await check("Voiding is explicit, requires a reason and binds confirmation to exact content",async()=>{
    const h=harness();await h.openDoc();await h.find("Void unsent draft").fire("click");assert.equal(h.writes().length,0);await set(h.body,"reason","Wrong unsent draft");await h.find("Void unsent draft").fire("click");const write=h.writes()[0];assert.equal(write.path,"/service-desk/void");assert.equal(write.options.body.reason,"Wrong unsent draft");assert.equal(write.options.body.content_sha256,"a".repeat(64));assert.equal(h.confirmations.length,1);assert.match(h.confirmations[0][1],/does not send a cancellation/);
  });
  await check("A void confirmation cannot cross a pairing change",async()=>{
    const h=harness();await h.openDoc();await set(h.body,"reason","Wrong draft");const pending=defer();h.app.confirmAction=()=>pending.promise;const one=h.find("Void unsent draft").fire("click");h.app.state.token="replacement-owner";h.api.locked();pending.resolve(true);await one;assert.equal(h.writes().length,0);assert.equal(h.document.getElementById("client-handoff-content").textContent,"");
  });
  await check("Only exact approved current-document export artifacts are shown",async()=>{
    const value=board();value.latest_export={status:"completed",document_id:"Q-000001",content_sha256:"a".repeat(64),artifacts:[{path:"client-handoff/export-one/draft.html"},{path:"client-handoff/export-one/evidence.json"}]};const h=harness(value);await h.openDoc(doc({status:"approved"}));await h.find("Download printable draft").fire("click");await h.find("Inspect handoff data").fire("click");assert.deepEqual(h.downloads,["client-handoff/export-one/draft.html"]);assert.deepEqual(h.artifacts,["client-handoff/export-one/evidence.json"]);assert.match(h.body.textContent,/private JSON evidence file/);assert.match(h.body.textContent,/including private client notes/);h.response.latest_export.document_id="Q-999999";await h.api.load();assert.equal(h.find("Download printable draft"),undefined);h.response.latest_export.document_id="Q-000001";h.response.latest_export.status="integrity_error";await h.api.load();assert.equal(h.find("Download printable draft"),undefined);
  });
  await check("Artifact paths reject traversal, roots and remote locations",()=>{
    const h=harness();for(const value of ["E:/secret","../escape","https://example.invalid","/absolute","a//b","a/./b","a/../b"])assert.throws(()=>h.api.relativePath(value));assert.equal(h.api.relativePath("client-handoff\\one\\draft.html"),"client-handoff/one/draft.html");
  });
  await check("An export hold blocks exports and has a separate inspection acknowledgement",async()=>{
    const value=board();value.export_hold={request_id:"failed-export",reason:"Publication interrupted",status:"failed"};const h=harness(value);await h.openDoc(doc({status:"approved"}));assert(h.find("Export approved draft").disabled);await h.find("Export approved draft").fire("click");assert.equal(h.writes().length,0);await h.find("Acknowledge inspected export hold").fire("click");const write=h.writes()[0];assert.equal(write.path,"/service-desk/export/acknowledge");assert.deepEqual(copy(write.options.body),{request_id:"failed-export",expected_revision:7});assert.match(h.confirmations[0][1],/does not adopt or replay/);assert.equal(h.toasts.length,1);assert.doesNotMatch(h.body.textContent,/did not complete/);
  });
  await check("An uncertain mutation is never repeated; inspection does not retry it",async()=>{
    const h=harness();await h.openDoc();h.handler=()=>{throw new Error("Response lost");};await h.find("Approve exact draft").fire("click");await h.api.review(doc(),"approve","");assert.equal(h.writes().length,1);assert.match(h.body.textContent,/nothing is retried automatically/);assert(h.find("I inspected the saved state").disabled);h.handler=null;await h.api.load();assert.equal(h.find("I inspected the saved state").disabled,false);await h.find("I inspected the saved state").fire("click");assert.equal(h.writes().length,1);assert.equal(h.find("Approve exact draft").disabled,false);
  });
  await check("CAS conflicts hold further changes until a new read without replay",async()=>{
    const h=harness();await h.openDoc();h.handler=()=>{throw Object.assign(new Error("Saved revision changed"),{status:409});};await h.api.review(doc(),"approve","");assert(h.find("Approve exact draft").disabled);await h.api.review(doc(),"approve","");assert.equal(h.writes().length,1);h.handler=null;await h.api.load();assert.equal(h.find("Approve exact draft").disabled,false);assert.equal(h.writes().length,1);
  });
  await check("Duplicate clicks and local navigation are held while a mutation is in flight",async()=>{
    const h=harness();await h.openDoc();const pending=defer();h.handler=()=>pending.promise;const one=h.find("Approve exact draft").fire("click");assert(h.find("Add another service client").disabled);await h.find("Add another service client").fire("click");await h.api.review(doc(),"approve","");assert.equal(h.writes().length,1);h.handler=null;pending.resolve({status:"completed",revision:7,entity_id:"Q-000001"});await one;assert(h.find("Save client profile"));
  });
  await check("Global autonomy pause disables creation, review, acceptance, invoice and export",async()=>{
    const h=harness();await h.openDoc(doc({status:"approved",can_accept:true,can_invoice:true}));h.app.state.status.paused=true;h.api.status();for(const label of ["Save client profile","Save job intake","Record customer response","Generate invoice draft from accepted quote","Export approved draft","Void unsent draft"])assert(h.find(label).disabled,label);await h.find("Export approved draft").fire("click");assert.equal(h.writes().length,0);
  });
  await check("Pairing changes discard late private reads and erase local draft fields",async()=>{
    const h=harness(),pending=defer();h.handler=()=>pending.promise;const one=h.api.load();h.app.state.authenticated=false;h.api.locked();pending.resolve(board());await one;assert.equal(h.document.getElementById("client-handoff-content").textContent,"");assert.equal(h.calls.length,1);h.app.state.authenticated=true;h.app.state.token="new-key";h.handler=null;h.response=board(false);await h.api.load();assert.equal(input(h.body,"name").value,"");
  });
  await check("Late mutation replies after pairing changes cannot toast, reload or expose artifacts",async()=>{
    const h=harness();await h.openDoc(doc({status:"approved"}));const pending=defer();h.handler=()=>pending.promise;const before=h.calls.length,one=h.find("Export approved draft").fire("click");h.app.state.token="replacement";h.api.locked();pending.resolve({status:"completed",artifacts:[{path:"client-handoff/private/draft.html"}]});await one;assert.equal(h.toasts.length,0);assert.equal(h.calls.length,before+1);assert.equal(h.document.getElementById("client-handoff-content").textContent,"");
  });
  await check("Mixed-revision details and cross-client queues disable changes",async()=>{
    const h=harness();h.handler=(p,o)=>{const answer=h.defaultReply(p,o);return p.includes("/clients/")?{...answer,revision:8}:answer;};await h.api.load();assert(h.find("Save new service client").disabled);assert.match(h.body.textContent,/changed while loading document details/);h.handler=null;h.response.intakes[0].client_id="another-client";await h.api.load();assert(h.find("Save client profile").disabled);assert.match(h.body.textContent,/selected client list could not be verified/);
  });
  await check("Hostile customer and source text remains literal rather than active markup",async()=>{
    const h=harness(),row=doc();row.customer.name="<svg onload=bad()>";row.source_notes="<script>never run</script>";await h.openDoc(row);assert.match(h.body.textContent,/<svg onload=bad\(\)>/);assert.match(h.body.textContent,/<script>never run<\/script>/);assert(!h.all().some(el=>["SCRIPT","SVG","IMG","IFRAME"].includes(el.tagName)));
  });
  await check("Archive blocks new jobs while existing documents remain readable",async()=>{
    const h=harness();h.details.clients["client-one"].archived=true;h.response.clients[0].archived=true;await h.openDoc();assert.equal(h.find("Save job intake"),undefined);assert.match(h.body.textContent,/client is archived/);assert(h.find("Approve exact draft"));
  });
  await check("Pagination uses bounded server offsets and retains the selected client filter",async()=>{
    const value=board();value.pagination.documents={offset:0,total:80,has_more:true};const h=harness(value);await h.api.load();await h.find("Next documents").fire("click");assert(h.calls.some(call=>call.path==="/service-desk?document_offset=50&client_id=client-one"));assert.match(h.body.textContent,/of 80/);
  });
  await check("Friendly source headings and latest-eight audit records avoid internal field paths",async()=>{
    const h=harness();h.response.audit=Array.from({length:12},(_,i)=>({request_id:`audit-${12-i}`,action:`saved_action_${12-i}`,created_at:"2026-10-30T05:00:00Z"}));await h.openDoc(doc({status:"held",exceptions:[{field:"lines.0.unit_price",message:"Approved rate missing"},{field:"provider.address",message:"Issuer address needed"}]}));assert.match(h.body.textContent,/Line 1 · approved rate/);assert.match(h.body.textContent,/Service client business address/);assert.match(h.body.textContent,/audit-12/);assert.match(h.body.textContent,/audit-5/);assert.doesNotMatch(h.body.textContent,/audit-4(?!\d)|lines\.0\.unit_price/);
  });
  await check("UI has no timers, external libraries, executable markup, sending, payment or ledger endpoint",()=>{
    assert.doesNotMatch(source,/\.innerHTML\s*=|\beval\s*\(|setInterval\s*\(|setTimeout\s*\(|https?:\/\//);assert.doesNotMatch(source,/\/send|\/post-invoice|\/payments|\/business_records/);assert.doesNotMatch(source,/parseFloat\s*\(/);const css=fs.readFileSync(path.join(root,"ariadne/static/client_handoff.css"),"utf8");assert.match(css,/@media\(max-width:480px\)/);assert.match(css,/focus-visible/);assert.match(css,/\[hidden\].*display:none!important/);assert.doesNotMatch(css,/@import|url\(/);
  });
  console.log(JSON.stringify({passed:checks.length,failures:0,checks,source_sha256:crypto.createHash("sha256").update(source).digest("hex")},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
