"use strict";
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm"),assert=require("node:assert/strict");
class Node{
 constructor(tag="div"){this.tagName=tag;this.children=[];this.listeners={};this.attributes={};this._text="";}
 set textContent(value){this._text=String(value);this.children=[];}get textContent(){return this._text+this.children.map(x=>x.textContent??String(x)).join(" ");}
 append(...children){this.children.push(...children);}replaceChildren(...children){this._text="";this.children=[...children];}
 setAttribute(key,value){this.attributes[key]=String(value);}addEventListener(key,fn){this.listeners[key]=fn;}
}
const nodes=new Map(["options-review-content","refresh-options-review"].map(id=>[id,new Node()]));
global.document={getElementById:id=>nodes.get(id)};global.window={};
vm.runInThisContext(fs.readFileSync(path.join(__dirname,"../../ariadne/static/options_review.js"),"utf8"));
const ui=window.ariadneOptionsReview,checks=[],failures=[];
const fixture=()=>({status:"waiting_for_first_capture",research_only:true,execution_qualified:false,model_calls:0,network_calls:0,source:{},holds:{},performance:{cash:"500",equity:"500",contributions:"500",trading_pnl:"0",fees:"0",entries:0,closed_trades:0,wins:0,losses:0,observed_win_rate_pct:null,max_peak_trading_pnl_drop:"0",months:[]},diagnostics:{strategies:[]},failed_checks:[],limitations:[]});
let calls=[],response=fixture();const state={authenticated:true,token:"owner",view:"venture",status:{paused:false}};
const app={state,api:async(...args)=>{calls.push(args);return response;},node:(tag,cls,value)=>{const n=new Node(tag);n.className=cls;if(value!==undefined)n.textContent=value;return n;}};
function check(name,fn){try{fn();checks.push(name);}catch(error){failures.push({name,error:error.message});}}
async function asyncCheck(name,fn){try{await fn();checks.push(name);}catch(error){failures.push({name,error:error.message});}}
(async()=>{
 ui.attach(app);check("Attaching a journal never fetches, collects or trades",()=>assert.equal(calls.length,0));
 await asyncCheck("Loading uses only the owner read endpoint",async()=>{await ui.load();assert.deepEqual(calls,[["/options/review"]]);});
 check("Waiting state distinguishes starting cash from performance",()=>{assert.match(nodes.get("options-review-content").textContent,/No F option capture/);assert.match(nodes.get("options-review-content").textContent,/starting cash and deposits are not trading results/);});
 check("No observed closed win rate is invented",()=>assert.match(nodes.get("options-review-content").textContent,/No observed closed-trade win rate/));
 check("The journal explains peak PnL drop separately from gross risk cap",()=>assert.match(nodes.get("options-review-content").textContent,/different measure/));
 check("Money display rejects nonfinite and unsupported values",()=>{for(const value of [null,NaN,"NaN","Infinity",{},true])assert.equal(ui.money(value),"Not observed");});
 check("Unqualified response cannot be presented as verified journal",()=>{for(const value of [{...fixture(),execution_qualified:true},{...fixture(),research_only:false},{...fixture(),model_calls:1}])assert.throws(()=>ui.validate(value));});
 state.status.paused=true;ui.status();check("Paused journal reads never issue a mutation",()=>assert.equal(calls.length,1));
 response={...fixture(),status:"recorded_observations",source:{last_source_date:"2026-10-20",actual_received_at:"2026-10-21T13:35:01Z"},performance:{...fixture().performance,closed_trades:2,wins:1,losses:1,observed_win_rate_pct:"50"},diagnostics:{source_date:"2026-10-20",recorded_at:"2026-10-21T13:35:02Z",strategies:[{id:"eod_recovery_call_v1",signal:true,inspected_quotes:4,eligible_count:0,rejections:{expiry_outside_decision_month:4}}]}};
 await ui.load();check("Recorded original receipt and rejection counts remain dated",()=>{const t=nodes.get("options-review-content").textContent;assert.match(t,/2026-10-21T13:35:01Z/);assert.match(t,/expiry outside decision month: 4/);assert.match(t,/does not establish a profitable strategy/);});
 await asyncCheck("Leaving the view invalidates an in-flight reply",async()=>{let resolve;app.api=(...args)=>{calls.push(args);return new Promise(done=>resolve=done);};const pending=ui.load();state.view="missions";ui.detach();resolve(fixture());await pending;assert.equal(nodes.get("options-review-content").children.length,0);});
 await asyncCheck("A hidden view does not start a new read",async()=>{const before=calls.length;await ui.load();assert.equal(calls.length,before);});
 state.view="venture";state.authenticated=true;app.api=async(...args)=>{calls.push(args);throw new Error("damaged saved source");};
 await asyncCheck("Damaged evidence produces one failed read without automatic retry",async()=>{const before=calls.length;await ui.load();assert.equal(calls.length,before+1);assert.match(nodes.get("options-review-content").textContent,/unavailable/);});
 state.authenticated=false;ui.locked();check("Authentication loss drops cash and source records",()=>assert.equal(nodes.get("options-review-content").textContent,""));
 process.stdout.write(JSON.stringify({checks,failures:failures.length,failed:failures}));process.exitCode=failures.length?1:0;
})();
