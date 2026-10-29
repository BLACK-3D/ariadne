"use strict";
// Actual preference and playback callbacks in an isolated DOM/async fixture.
// No browser, audio device, model, network or real timer work.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "ariadne/static/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "ariadne/static/index.html"), "utf8");
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== "--receipt" || !args[1])) throw new Error("Usage: node scripts/check_voice_ui.cjs [--receipt PATH]");
const checks = [];
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a);
  return source.slice(a, b);
}
const callbacks = section("function renderSpeechPreferences()", "async function saveModel(") + "\n" + section("function stopSpeech()", "function syncKnowledgeTab(") + "\n" + section("async function refreshStatus(", "async function connect(");
function deferred() { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise, resolve, reject}; }
function fixture(handler) {
  const elements = new Map(), requests = [], toasts = [], urls = [], revoked = [], timers = new Map(), rendered = [];
  class Element {
    constructor(id, text = "") { this.id=id; this.children=[]; this.dataset={}; this.disabled=false; this.value=""; this.title=""; this.attributes={}; this._text=text; }
    get options() { return this.children; }
    get textContent() { return this._text+this.children.map(child => child.textContent || "").join(""); }
    set textContent(value) { this._text=String(value); this.children=[]; }
    set innerHTML(value) { throw new Error("Voice status must remain literal text"); }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this._text=""; this.children=[...items]; }
    setAttribute(name,value) { this.attributes[name]=String(value); }
    removeAttribute(name) { delete this.attributes[name]; if(name==="src") this.src=""; }
  }
  for (const id of ["speech-backend","speech-voice","speech-speed","speech-preview","speech-runtime","speech-player"]) elements.set(id,new Element(id));
  const backend=elements.get("speech-backend");
  for (const value of ["natural","system"]) { const option=new Element("option",value); option.value=value; backend.append(option); }
  const player=elements.get("speech-player"); player.paused=true; player.plays=0;
  player.pause=() => {player.paused=true;}; player.play=async() => {player.paused=false;player.plays+=1;};
  const state={authenticated:true,token:"fixture-session",speechRequest:0,speechPreferenceRequest:0,speechPreferenceConfirmed:0,speechPreferencesSaving:false,speechButton:null,speechUrl:null,refreshingStatus:false,
    status:{active_runs:0,speech_backend:"natural",speech_voice:"af_heart",speech_speed:1.12,wake:{active:false},voice:{speech:true,speech_backends:["natural","system"],default_voice:"af_heart",natural_runtime:{state:"ready"},voice_presets:[
      {id:"af_heart",name:"Heart · warm American",language:"en-us"},{id:"bf_emma",name:"<literal British label>",language:"en-gb"}]}}};
  const sandbox=vm.createContext({state,
    $: selector => elements.get(selector.replace(/^#/,"")),
    clear: element => element.replaceChildren(), node: (_tag,_class,text) => new Element("created",text || ""), icon: name => ({name}),
    api: async(route,options) => {requests.push({route,options}); if(handler) return handler(route,options); return route==="/voice/preferences" ? {speech_backend:options.body.backend,speech_voice:options.body.voice,speech_speed:options.body.speed} : {audio:true};},
    toast: (...values) => toasts.push(values), renderWakeStatus: () => {},
    renderStatus: value => rendered.push(value), loadRuns: async() => {},
    URL:{createObjectURL: value => {urls.push(value);return "fixture-blob-"+urls.length;},revokeObjectURL:value=>revoked.push(value)},
    clearInterval:id=>timers.delete(id),setInterval:callback=>{const id=timers.size+1;timers.set(id,callback);return id;},
  });
  vm.runInContext(callbacks,sandbox,{timeout:2000});
  sandbox.renderSpeechPreferences();
  return {state,elements,requests,toasts,urls,revoked,timers,player,sandbox,rendered,control:elements.get("speech-preview"),
    render:()=>sandbox.renderSpeechPreferences(), save:()=>sandbox.saveSpeechPreferences(), preview:()=>sandbox.previewSpeech(),
    speak:()=>sandbox.speak("Fixture speech.",elements.get("speech-preview")),stop:()=>sandbox.stopSpeech()};
}
async function main() {
  const initial=fixture();
  assert.equal(initial.elements.get("speech-voice").value,"af_heart"); assert.equal(initial.elements.get("speech-speed").value,"1.12");
  assert.equal(initial.elements.get("speech-voice").children[1].textContent,"<literal British label>");
  assert.equal(initial.elements.get("speech-runtime").textContent,"Local neural voice ready."); assert.equal(initial.requests.length,0);
  checks.push("actual preference render preserves literal preset labels and confirmed defaults without I/O");
  const cori=fixture(); delete cori.state.status.speech_voice; cori.state.status.voice.default_voice="cori";
  cori.state.status.voice.voice_presets.unshift({id:"cori",name:"Cori · British (quick)",language:"en-gb"}); cori.render();
  assert.equal(cori.elements.get("speech-voice").value,"cori"); assert.equal(await cori.save(),true); assert.equal(cori.requests[0].options.body.voice,"cori");
  const legacy=fixture(); delete legacy.state.status.speech_voice; legacy.render(); assert.equal(legacy.elements.get("speech-voice").value,"af_heart");
  checks.push("available Cori defaults and legacy Kokoro defaults follow the server's voice metadata rather than a hardcoded voice");
  initial.elements.get("speech-speed").value="1.19"; initial.elements.get("speech-voice").value="bf_emma"; initial.render();
  assert.equal(initial.elements.get("speech-speed").value,"1.19"); assert.equal(initial.elements.get("speech-voice").value,"bf_emma");
  checks.push("unchanged status polling preserves pace and voice edits in progress");
  const saved=await initial.save(); assert.equal(saved,true); assert.equal(initial.requests.length,1);
  assert.equal(initial.requests[0].route,"/voice/preferences");
  assert.equal(JSON.stringify(initial.requests[0].options.body),JSON.stringify({backend:"natural",voice:"bf_emma",speed:1.19}));
  assert.equal(initial.state.status.speech_voice,"bf_emma"); assert.equal(initial.state.status.speech_speed,1.19);
  checks.push("actual save sends one numeric bounded preference object and retains the confirmed voice/pace");
  for (const value of ["", "abc", "0.84", "1.31", "Infinity"]) {
    const invalid=fixture(); invalid.elements.get("speech-speed").value=value;
    assert.equal(await invalid.save(),false); assert.equal(invalid.requests.length,0);
  }
  checks.push("invalid or out-of-range pace never submits a request");
  const rejected=fixture(async()=>{throw new Error("Preference rejected");});
  rejected.elements.get("speech-speed").value="1.20";
  assert.equal(await rejected.save(),false); assert.equal(rejected.elements.get("speech-speed").value,"1.12");
  assert.equal(rejected.state.speechPreferencesSaving,false); assert.equal(rejected.toasts[0][0],"Preference rejected");
  checks.push("a rejected preference save restores the last confirmed controls and releases their busy state");
  const warned=fixture(async()=>({speech_backend:"natural",speech_voice:"bf_emma",speech_speed:1.20,speech_warning:"Voice settings saved, but the natural voice could not warm. Review voice status."}));
  warned.elements.get("speech-voice").value="bf_emma"; warned.elements.get("speech-speed").value="1.20";
  assert.equal(await warned.save(),true); assert.equal(warned.state.status.speech_voice,"bf_emma"); assert.equal(warned.elements.get("speech-speed").value,"1.2");
  assert.deepEqual(warned.toasts[0],["Voice settings saved, but the natural voice could not warm. Review voice status.",true]);
  checks.push("committed preferences remain selected when the server reports a separate warm-up warning");
  const waiting=deferred(), concurrent=fixture(()=>waiting.promise); const save=concurrent.save();
  assert.equal(concurrent.elements.get("speech-voice").disabled,true); assert.equal(await concurrent.save(),false); assert.equal(concurrent.requests.length,1);
  waiting.resolve({speech_backend:"natural",speech_voice:"af_heart",speech_speed:1.12}); assert.equal(await save,true);
  const locked=fixture(); locked.state.authenticated=false; assert.equal(await locked.save(),false); assert.equal(locked.requests.length,0);
  checks.push("busy and locked controls cannot start overlapping preference writes");
  const oldSave=deferred(), staleSave=fixture(()=>oldSave.promise), pendingSave=staleSave.save();
  staleSave.state.speechPreferenceRequest+=1; staleSave.state.speechPreferencesSaving=true; staleSave.state.token="new-fixture-session";
  oldSave.resolve({speech_backend:"system",speech_voice:"bf_emma",speech_speed:1.30});
  assert.equal(await pendingSave,false); assert.equal(staleSave.state.status.speech_backend,"natural"); assert.equal(staleSave.state.speechPreferencesSaving,true); assert.equal(staleSave.toasts.length,0);
  checks.push("late preference completion cannot alter or release a newly paired session's pending controls");
  const oldStatus=deferred(), statusRace=fixture((route,options)=>route==="/status" ? oldStatus.promise : {speech_backend:options.body.backend,speech_voice:options.body.voice,speech_speed:options.body.speed});
  const earlierStatus=JSON.parse(JSON.stringify(statusRace.state.status)); earlierStatus.active_runs=7; earlierStatus.model="fresh-server-model";
  const pendingStatus=statusRace.sandbox.refreshStatus(); statusRace.elements.get("speech-voice").value="bf_emma"; statusRace.elements.get("speech-speed").value="1.20";
  assert.equal(await statusRace.save(),true); oldStatus.resolve(earlierStatus); await pendingStatus;
  assert.equal(statusRace.state.status.speech_voice,"bf_emma"); assert.equal(statusRace.state.status.speech_speed,1.20);
  assert.equal(statusRace.state.status.active_runs,7); assert.equal(statusRace.state.status.model,"fresh-server-model"); assert.equal(statusRace.rendered.length,1);
  checks.push("late status GET preserves newer confirmed voice preferences while accepting all other fresh server fields");
  const pendingGet=deferred(), failedPut=deferred(), pendingRace=fixture(route=>route==="/status" ? pendingGet.promise : failedPut.promise);
  const currentServer=JSON.parse(JSON.stringify(pendingRace.state.status)); currentServer.speech_backend="system"; currentServer.speech_speed=1.10;
  const getting=pendingRace.sandbox.refreshStatus(); pendingRace.elements.get("speech-voice").value="bf_emma"; const saving=pendingRace.save();
  pendingGet.resolve(currentServer); await getting; assert.equal(pendingRace.state.status.speech_backend,"system");
  failedPut.reject(new Error("Rejected pending preference")); assert.equal(await saving,false);
  assert.equal(pendingRace.state.status.speech_voice,"af_heart"); assert.equal(pendingRace.elements.get("speech-speed").value,"1.1"); assert.equal(pendingRace.state.speechPreferenceConfirmed,0);
  checks.push("unconfirmed or failed preference writes never replace an honest status response with unsaved form values");
  const staleGet=deferred(), statusSession=fixture(()=>staleGet.promise), gettingOld=statusSession.sandbox.refreshStatus();
  const oldSessionStatus=JSON.parse(JSON.stringify(statusSession.state.status)); statusSession.state.token="another-session"; statusSession.state.status={marker:"new session"};
  staleGet.resolve(oldSessionStatus); await gettingOld;
  assert.equal(statusSession.state.status.marker,"new session"); assert.equal(statusSession.rendered.length,0);
  checks.push("status completion for an old pairing cannot repopulate or render a different authenticated session");
  const preview=fixture(); await preview.preview();
  assert.deepEqual(preview.requests.map(item=>item.route),["/voice/preferences","/voice/speak"]);
  assert.equal(preview.requests[1].options.body.text,"I’m Ariadne. Ready when you are."); assert.equal(preview.player.plays,1);
  assert.match(preview.control.textContent,/Stop preview/); await preview.preview();
  assert.equal(preview.requests.length,2); assert.equal(preview.player.paused,true); assert.match(preview.control.textContent,/Preview voice/); assert.equal(preview.revoked.length,1);
  checks.push("preview saves first, uses the shared local WAV player, and toggles to stop without a second synthesis");
  const staleAudio=deferred(), newer=fixture((_route,options)=>options.body.text==="Fixture speech." && newer.requests.length===1 ? staleAudio.promise : {audio:"new"});
  const oldAudio=newer.speak(); newer.stop(); await newer.speak(); assert.equal(newer.player.plays,1); staleAudio.resolve({audio:"stale"}); await oldAudio;
  assert.equal(newer.player.plays,1); assert.equal(newer.urls.length,1); assert.equal(newer.state.speechButton,newer.control);
  checks.push("late audio cannot replace a later request even when the same preview control is reused");
  const staleFailure=deferred(), afterFailure=fixture(()=>afterFailure.requests.length===1 ? staleFailure.promise : {audio:"new"});
  const oldFailure=afterFailure.speak(); afterFailure.stop(); await afterFailure.speak(); staleFailure.reject(new Error("Stale synthesis failed")); await oldFailure;
  assert.equal(afterFailure.player.paused,false); assert.equal(afterFailure.toasts.length,0); assert.equal(afterFailure.state.speechButton,afterFailure.control);
  checks.push("a stale synthesis failure cannot stop or report an error over newer playback");
  const suppression=deferred(), wake=fixture((route)=>route==="/voice/wake/suppress" ? suppression.promise : {audio:true}); wake.state.status.wake={active:true,marker:"current"};
  const pendingWake=wake.speak(); await Promise.resolve(); await Promise.resolve();
  wake.stop(); wake.state.token="repaired-session"; wake.state.status.wake={active:false,marker:"new-session"}; suppression.resolve({active:true,marker:"stale"}); await pendingWake;
  assert.equal(wake.player.plays,0); assert.equal(wake.state.status.wake.marker,"new-session"); assert.equal(wake.timers.size,0);
  checks.push("late microphone-suppression response cannot repopulate a new session or start stale playback");
  const system=fixture(); system.state.status.speech_backend="system"; system.state.status.voice.speech_backends=["system"]; system.render();
  assert.equal(system.elements.get("speech-voice").disabled,true); assert.equal(system.elements.get("speech-speed").disabled,true); assert.equal(system.control.disabled,false);
  system.state.status.voice.speech=false; system.render(); assert.equal(system.control.disabled,true);
  checks.push("system-only and unavailable speech states disclose usable controls without enabling missing neural voices");
  const receipt={created_at_utc:new Date().toISOString(),passed:checks.length,failures:0,checks,
    source_sha256:{"ariadne/static/app.js":crypto.createHash("sha256").update(source).digest("hex"),"ariadne/static/index.html":crypto.createHash("sha256").update(html).digest("hex"),"scripts/check_voice_ui.cjs":crypto.createHash("sha256").update(fs.readFileSync(__filename)).digest("hex")},
    scope:"Actual preference/playback helpers in a minimal DOM and controlled async fixture; no live browser layout, audio device, microphone, model, network, or real timers."};
  if(args.length)fs.writeFileSync(path.resolve(args[1]),JSON.stringify(receipt,null,2)+"\n");
  console.log(JSON.stringify({passed:checks.length,failures:0}));
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
