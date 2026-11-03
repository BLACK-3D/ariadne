"use strict";
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm"),assert=require("node:assert/strict");
const code=fs.readFileSync(path.join(__dirname,"../../ariadne/static/app.js"),"utf8");
const first=code.indexOf("async function openLinkedView("),last=code.indexOf("async function loadView(",first);
assert.ok(first>0&&last>first);
let passed=0;
async function scenario(label,steer,expected){
  let release,calls=0;
  const state={authenticated:true,token:"synthetic-navigation",view:"handoff",navigationGeneration:0};
  const context=vm.createContext({state,showView:async view=>{state.navigationGeneration++;state.view=view;await new Promise(resolve=>{release=resolve;});}});
  vm.runInContext(code.slice(first,last)+"\nthis.open=openLinkedView;",context);
  const pending=context.open("workroom",()=>{calls++;});
  if(steer)steer(state);
  release();await pending;
  assert.equal(calls,expected,label);passed++;
}
(async()=>{
  await scenario("normal navigation selects once",null,1);
  await scenario("leaving while view load is pending",s=>{s.view="files";s.navigationGeneration++;},0);
  await scenario("away and back cannot revive a stale selection",s=>{s.view="workroom";s.navigationGeneration+=2;},0);
  await scenario("locking invalidates even same-token reconnect",s=>{s.navigationGeneration++;s.authenticated=true;},0);
  await scenario("token change invalidates context",s=>{s.token="different-synthetic-key";},0);
  await scenario("locked tab cannot select",s=>{s.authenticated=false;},0);
  let navigated=false;
  const context=vm.createContext({state:{authenticated:false},showView:async()=>{navigated=true;}});
  vm.runInContext(code.slice(first,last)+"\nthis.open=openLinkedView;",context);
  await context.open("project-kits",()=>{throw Error("Locked action called");});
  assert.equal(navigated,false);passed++;
  process.stdout.write(JSON.stringify({passed,failures:0,scope:"Synthetic delayed navigation; no browser, network, owner state or model."}));
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
