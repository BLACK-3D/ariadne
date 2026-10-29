"use strict";
// Exercise the actual settings callback without network, timers or owner state.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const root = path.resolve(__dirname,"..");
const source = fs.readFileSync(path.join(root,"ariadne/static/app.js"),"utf8");
const html = fs.readFileSync(path.join(root,"ariadne/static/index.html"),"utf8");
const start = source.indexOf("async function saveBriefing("), end = source.indexOf("function renderLocationResult(",start);
assert.ok(start >= 0 && end > start);
const checks = [];
function fixture({checked=true,revision="7",paused=false,confirm=true,fail=false}={}) {
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector,{value:"",checked:false,disabled:false,dataset:{}});
    return elements.get(selector);
  };
  $("#brief-enabled").checked=true;
  $("#brief-repository-updates").checked=checked;
  $("#brief-config-form").dataset.revision=revision;
  const calls=[],toasts=[];
  const box = {$,briefConfig:{revision:99,paused,news_feeds:[]},confirmAction:async()=>confirm,
    api:async(p,options)=>{calls.push({p,options});if(fail)throw new Error("Settings changed; refresh.");},
    toast:(...args)=>toasts.push(args),loadBriefing:async()=>{}};
  vm.createContext(box);
  vm.runInContext(source.slice(start,end),box);
  return {box,calls,toasts,$};
}
(async()=>{
  assert.ok(html.includes('id="brief-repository-updates"'));
  assert.match(html,/<label class="checkbox-label"><input type="checkbox" id="brief-repository-updates"><span>Daily repository updates<\/span><\/label>/);
  assert.ok(source.includes('$("#brief-repository-updates").checked = cfg.repository_updates === true;'));
  assert.ok(source.includes('$("#brief-config-form").dataset.revision = String(cfg.revision);'));
  checks.push("repository control has a visible label and loads the saved opt-in with a form revision");
  let f=fixture();await f.box.saveBriefing({preventDefault(){}});
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].p,"/briefing/config");
  assert.equal(f.calls[0].options.body.repository_updates,true);
  assert.equal(f.calls[0].options.body.expected_revision,7);
  assert.equal(f.calls[0].options.body.online_sources,false);
  checks.push("saving carries the explicit watch choice and pinned form revision, not a newer background revision");
  f=fixture({checked:false});await f.box.saveBriefing({preventDefault(){}});
  assert.equal(f.calls[0].options.body.repository_updates,false);
  checks.push("turning the watch off is an explicit persisted false value");
  f=fixture();delete f.$("#brief-config-form").dataset.revision;
  await f.box.saveBriefing({preventDefault(){}});
  assert.equal(f.calls.length,0);assert.match(f.toasts[0][0],/Refresh briefing settings/);
  checks.push("a missing revision cannot silently submit unreviewed settings");
  f=fixture({paused:true,confirm:false});await f.box.saveBriefing({preventDefault(){}});
  assert.equal(f.calls.length,0);
  checks.push("repository opt-in cannot bypass the existing briefing-hold resume confirmation");
  f=fixture({fail:true});await f.box.saveBriefing({preventDefault(){}});
  assert.equal(f.calls.length,1);assert.equal(f.$("#brief-save").disabled,false);
  assert.equal(f.toasts.length,1);assert.match(f.toasts[0][0],/Settings changed/);
  checks.push("a rejected stale save reports failure without retrying and releases the save control");
  const receipt={passed:checks.length,failures:0,checks,source_sha256:crypto.createHash("sha256").update(source).digest("hex"),html_sha256:crypto.createHash("sha256").update(html).digest("hex")};
  if(process.argv[2]==="--receipt"){
    assert.equal(process.argv.length,4);fs.writeFileSync(process.argv[3],JSON.stringify(receipt,null,2)+"\n",{flag:"wx"});
  }
  console.log(JSON.stringify(receipt,null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
