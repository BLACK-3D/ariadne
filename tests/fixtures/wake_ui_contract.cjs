"use strict";
// Actual app.js wake functions, dependency-free DOM/API fixtures. No microphone or browser.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), crypto = require("node:crypto");
const root = path.resolve(__dirname, "../.."), file = path.join(root, "ariadne/static/app.js");
const source = fs.readFileSync(file, "utf8"), index = fs.readFileSync(path.join(root, "ariadne/static/index.html"), "utf8");
const wakeCode = source.slice(source.indexOf("function wakePresentation("), source.indexOf("async function loadBriefing("));
const factsCode = source.slice(source.indexOf("function facts("), source.indexOf("function renderSettings("));
const statusCode = source.slice(source.indexOf("async function refreshStatus("), source.indexOf("async function connect(" )).replace("async function refreshStatus(", "async function fullStatusFixture(");
const apiCode = source.slice(source.indexOf("async function api("), source.indexOf("function modelReady(")).replace("async function api(", "async function realApiFixture(");
const speechCode = source.slice(source.indexOf("function stopSpeech("), source.indexOf("function syncKnowledgeTab("))
  .replace("function stopSpeech(", "function stopSpeechFixture(").replace("function speak(", "function speakFixture(").replaceAll("stopSpeech();", "stopSpeechFixture();");
assert.ok(wakeCode.startsWith("function wakePresentation("));
const checks = [], clone = value => JSON.parse(JSON.stringify(value));
const defer = () => { let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject}; };
class Element {
  constructor() { this.disabled = false; this.checked = false; this.hidden = false; this.children = []; this._text = ""; this.attributes = {}; this.dataset = {}; this.paused = true; this.classes = new Set(); this.classList = {toggle:(name, on) => on ? this.classes.add(name) : this.classes.delete(name)}; }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text + this.children.map(item => item.textContent).join(""); }
  replaceChildren(...items) { this.children = items; this._text = ""; }
  append(...items) { this.children.push(...items); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; delete this[name]; }
  pause() { this.paused = true; }
}
const off = () => ({active:false, mode:null, state:"stopped", conversation_active:false, conversation_idle_timeout_seconds:120, sleep_phrases:["go to sleep", "sleep"], auto_start:false, pc_supported:true, session_limit_hours:8, audio_saved:false, idle_llm_calls:0});
const on = (changes = {}) => ({...off(), active:true, mode:"pc", state:"conversing", conversation_active:true, ...changes});
function harness() {
  const ids = ["wake-indicator", "wake-indicator-label", "wake-conversation-status", "wake-facts", "wake-remember", "wake-error", "wake-start", "wake-stop", "connection-badge", "speech-player"];
  const elements = Object.fromEntries(ids.map(id => ["#" + id, new Element()]));
  const state = {authenticated:true, token:"same-owner-key", pairingGeneration:0, status:{wake:off(), active_runs:0}, refreshingStatus:false, speechPreferenceConfirmed:0, speechRequest:0, speechUrl:null, speechButton:null}, calls = [], toasts = [], fetches = [], locks = [];
  let handler = async (p) => p.endsWith("/stop") ? off() : on({state:"listening", conversation_active:false});
  let refreshes = 0, speechStops = 0, refreshHandler = async () => {}, nextTimer = 0, nextInterval = 0, playCalls = 0;
  let fetchHandler = async () => {throw new Error("Unexpected real API fixture call");};
  const timers = new Map(), timerHistory = [], intervals = new Map();
  elements["#speech-player"].play = async () => {playCalls += 1; elements["#speech-player"].paused = false;};
  const box = {state, Number, Array, String, console, AbortController, Blob, ArrayBuffer,
    setTimeout:(callback, delay) => {const id = ++nextTimer; const timer = {id, callback, delay}; timers.set(id, timer); timerHistory.push(timer); return id;},
    clearTimeout:id => timers.delete(id),
    setInterval:(callback, delay) => {const id = ++nextInterval; intervals.set(id, {callback, delay}); return id;},
    clearInterval:id => intervals.delete(id),
    URL:{createObjectURL:() => "blob:fixture-audio", revokeObjectURL:() => {}},
    $:selector => {assert.ok(elements[selector], selector); return elements[selector];},
    humanize:value => String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, char => char.toUpperCase()),
    node:(tag, cls, text) => {const item = new Element(); if (text !== undefined) item.textContent = text; return item;},
    clear:element => element.replaceChildren(),
    icon:() => new Element(), pretty:value => JSON.stringify(value),
    api:async (url, options = {}) => {const call = {url, options:clone(options)}; if (options.signal) call.signal = options.signal; calls.push(call); return handler(url, options);},
    toast:(text, error = false) => toasts.push({text, error}),
    stopSpeech:() => {speechStops += 1;},
    refreshStatus:async options => {assert.equal(options.quiet, true); refreshes += 1; await refreshHandler();},
    renderStatus:status => box.testWake.renderWakeStatus(status.wake),
    fetch:async (url, options) => {fetches.push({url, options}); return fetchHandler(url, options);},
    lockSession:message => {locks.push(message); state.pairingGeneration += 1; state.authenticated = false; state.token = ""; box.testWake.stopSpeechFixture(); box.testWake.resetWakeControls();},
    loadRuns:async () => {}, window:{}, document:{createTextNode:text => {const item = new Element(); item.textContent = text; return item;}},
  };
  vm.createContext(box);
  vm.runInContext('let wakeRequest=false, wakeRememberDirty=false, wakeGeneration=0, wakeStatusRevision=0; let wakePollTimer=null, wakePollRequest=null, wakePollGeneration=0;\n' + factsCode + wakeCode + statusCode + apiCode + speechCode + '\nthis.testWake={wakePresentation,renderWakeStatus,setWakeListening,resetWakeControls,fullStatusFixture,realApiFixture,speakFixture,stopSpeechFixture,dirty:()=>{wakeRememberDirty=true;},pending:()=>wakeRequest};', box, {filename:file});
  const api = box.testWake;
  api.renderWakeStatus(state.status.wake);
  return {api, state, elements, calls, toasts, fetches, locks, intervals, el:id => elements["#" + id], control:() => {const item = new Element(); item.id = "speech-fixture"; item.title = "Read aloud"; return item;},
    set handler(value) {handler = value;}, set refreshHandler(value) {refreshHandler = value;},
    set fetchHandler(value) {fetchHandler = value;},
    get refreshes() {return refreshes;}, get speechStops() {return speechStops;}, get playCalls() {return playCalls;},
    render:wake => {state.status.wake = wake; api.renderWakeStatus(wake);}, timers, timerHistory,
    tick:async () => {const timer = timers.values().next().value; if (!timer) return false; timers.delete(timer.id); timer.callback(); await new Promise(resolve => setImmediate(resolve)); return true;},
    settle:async () => {await new Promise(resolve => setImmediate(resolve));}};
}
const check = async (name, test) => {await test(); checks.push(name);};
async function main() {
  await check("Mic off is confirmed server state and rendering performs no capture or request", () => {
    const h = harness(); assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.equal(h.el("wake-start").disabled, false); assert.equal(h.el("wake-stop").disabled, true); assert.equal(h.el("wake-remember").checked, false); assert.equal(h.calls.length, 0); assert.equal(h.speechStops, 0);
  });
  await check("One wake is visibly Awake and follow-ups need no repeated name", () => {
    const h = harness(); h.render(on()); assert.equal(h.el("wake-indicator-label").textContent, "Awake"); assert.match(h.el("wake-conversation-status").textContent, /without repeating/); assert.equal(h.el("wake-start").disabled, true); assert.equal(h.el("wake-stop").disabled, false); assert.equal(h.el("wake-remember").disabled, true); assert.match(h.el("wake-indicator").attributes["aria-label"], /Awake/);
  });
  await check("Sleep or idle expiry returns to waiting with PC capture still on and no UI mutation", () => {
    const h = harness(); h.render(on()); h.render(on({state:"listening", conversation_active:false})); assert.equal(h.el("wake-indicator-label").textContent, "Waiting for Ariadne"); assert.match(h.el("wake-facts").textContent, /Pc · on/); assert.equal(h.el("wake-stop").disabled, false); assert.equal(h.calls.length, 0);
  });
  await check("Actual playback, acknowledgement and pending work override Awake", () => {
    const h = harness();
    for (const [changes, label] of [[{speaking:true}, "Ariadne speaking"], [{suppressed:true}, "Listening paused"], [{acknowledging:true}, "Listening paused"], [{state:"working"}, "Working"], [{pending_run_id:"run-1"}, "Working"], [{state:"starting"}, "Starting microphone"]]) {h.render(on(changes)); assert.equal(h.el("wake-indicator-label").textContent, label);}
  });
  await check("A stopped microphone cannot be shown Awake by a stale conversation flag", () => {
    const h = harness(); h.render({...off(), conversation_active:true, speaking:true}); assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.equal(h.el("wake-indicator").classes.has("active"), false);
  });
  await check("Missing or malformed status is unknown and only an explicit paired stop is recoverable", () => {
    const h = harness();
    for (const wake of [null, undefined, {}, {active:"true"}]) {h.render(wake); assert.equal(h.el("wake-indicator-label").textContent, "Voice unavailable"); assert.equal(h.el("wake-start").disabled, true); assert.equal(h.el("wake-stop").disabled, false); assert.equal(h.el("wake-facts").textContent, "");}
    h.state.authenticated = false; h.api.renderWakeStatus(on()); assert.equal(h.el("wake-stop").disabled, true); assert.match(h.el("wake-conversation-status").textContent, /Locking this tab does not stop/);
  });
  await check("Actual timeout and configured sleep phrases are literal text with no freshness assumption", () => {
    const h = harness(); h.render(on({sleep_phrases:["go to sleep", "<img src=x onerror=bad()>"], conversation_idle_timeout_seconds:120})); assert.match(h.el("wake-facts").textContent, /2 minutes/); assert.match(h.el("wake-facts").textContent, /<img src=x onerror=bad\(\)>/);
    h.render(on({conversation_idle_timeout_seconds:75})); assert.match(h.el("wake-facts").textContent, /75 seconds/);
    for (const value of [0, true, "120", Infinity, 1000000]) {h.render(on({conversation_idle_timeout_seconds:value})); assert.match(h.el("wake-facts").textContent, /Conversation idle timeoutUnavailable/);}
  });
  await check("Status refresh preserves a user-edited opt-in without silently enabling startup", () => {
    const h = harness(); h.el("wake-remember").checked = true; h.api.dirty(); h.render(off()); assert.equal(h.el("wake-remember").checked, true); assert.equal(h.calls.length, 0);
    h.render(on({auto_start:false})); assert.equal(h.el("wake-remember").checked, false); assert.equal(h.el("wake-remember").disabled, true);
  });
  await check("Manual start sends only the existing PC and explicit unchecked remember contract", async () => {
    const h = harness(); await h.api.setWakeListening(true); assert.deepEqual(h.calls, [{url:"/voice/wake/start", options:{method:"POST", body:{mode:"pc", remember:false}}}]); assert.equal(h.speechStops, 1); assert.equal(h.refreshes, 1); assert.equal(h.toasts.length, 1); assert.match(h.toasts[0].text, /Say Ariadne once/); assert.equal(h.el("wake-indicator-label").textContent, "Waiting for Ariadne");
  });
  await check("Remember is sent only after the user checks the existing option", async () => {
    const h = harness(); h.el("wake-remember").checked = true; await h.api.setWakeListening(true); assert.equal(h.calls[0].options.body.remember, true);
  });
  await check("A pending start cannot create duplicate start or stop requests", async () => {
    const h = harness(), pending = defer(); h.handler = () => pending.promise; const first = h.api.setWakeListening(true); await h.api.setWakeListening(true); await h.api.setWakeListening(false); assert.equal(h.calls.length, 1); assert.equal(h.api.pending(), true); pending.resolve(on()); await first; assert.equal(h.api.pending(), false);
  });
  await check("Explicit stop turns off capture and startup without any browser microphone call", async () => {
    const h = harness(); h.render(on({auto_start:true})); await h.api.setWakeListening(false); assert.deepEqual(h.calls, [{url:"/voice/wake/stop", options:{method:"POST", body:{}}}]); assert.equal(h.speechStops, 0); assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.match(h.toasts[0].text, /startup listening are off/);
  });
  await check("Inactive startup listening can be explicitly disabled without first starting the mic", async () => {
    const h = harness(); h.render({...off(), auto_start:true}); assert.equal(h.el("wake-stop").disabled, false); await h.api.setWakeListening(false); assert.equal(h.calls[0].url, "/voice/wake/stop"); assert.equal(h.speechStops, 0);
  });
  await check("An uncertain response does not claim mic off, retry, or prevent explicit stop", async () => {
    const h = harness(), refresh = defer(); h.handler = async () => {throw new Error("connection interrupted");}; h.refreshHandler = () => refresh.promise; const start = h.api.setWakeListening(true); await new Promise(resolve => setImmediate(resolve)); assert.equal(h.el("wake-indicator-label").textContent, "Voice unavailable"); assert.equal(h.el("wake-start").disabled, true); assert.equal(h.el("wake-stop").disabled, false); assert.equal(h.calls.length, 1); assert.match(h.toasts[0].text, /not retried/); refresh.resolve(); await start; assert.equal(h.calls.length, 1);
  });
  await check("Late success after locking and re-pairing the same token cannot change the new session UI", async () => {
    const h = harness(), pending = defer(); h.handler = () => pending.promise; const first = h.api.setWakeListening(true); h.state.authenticated = false; h.state.token = ""; h.api.resetWakeControls(); h.state.authenticated = true; h.state.token = "same-owner-key"; h.render(off()); pending.resolve(on()); await first; assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.equal(h.toasts.length, 0); assert.equal(h.refreshes, 0); assert.equal(h.api.pending(), false);
  });
  await check("Late failure after a pairing change cannot toast or refresh under the new pairing", async () => {
    const h = harness(), pending = defer(); h.handler = () => pending.promise; const first = h.api.setWakeListening(true); h.state.authenticated = false; h.api.resetWakeControls(); h.state.authenticated = true; h.state.token = "different-owner"; h.render(off()); pending.reject(new Error("old request failed")); await first; assert.equal(h.toasts.length, 0); assert.equal(h.refreshes, 0); assert.equal(h.el("wake-indicator-label").textContent, "Mic off");
  });
  await check("Lock reset never issues a stop or start and discards dirty startup preferences", () => {
    const h = harness(); h.el("wake-remember").checked = true; h.api.dirty(); h.state.authenticated = false; h.api.resetWakeControls(); assert.equal(h.el("wake-remember").checked, false); assert.equal(h.el("wake-start").disabled, true); assert.equal(h.el("wake-stop").disabled, true); assert.equal(h.calls.length, 0); assert.match(source, /state\.token = "";\s+resetWakeControls\(\);/);
  });
  await check("Static controls are manual-only, accessible, and distinguish AI sleep from PC shutdown", () => {
    for (const id of ["wake-conversation-status", "wake-conversation-help", "wake-start", "wake-stop", "wake-remember"]) assert.equal((index.match(new RegExp('id="' + id + '"', 'g')) || []).length, 1);
    assert.match(index, /id="wake-conversation-status"[^>]*role="status"[^>]*aria-live="polite"/); assert.match(index, /id="wake-start" disabled/); assert.doesNotMatch(index.match(/<input[^>]*id="wake-remember"[^>]*>/)[0], /\bchecked\b/); assert.match(index, /microphone stays on/); assert.match(index, /do not shut down the PC/); assert.doesNotMatch(wakeCode, /getUserMedia|SpeechRecognition|MediaRecorder|setInterval|\/wake\/accept/);
  });
  await check("The shutdown phrase explicitly disables microphone capture rather than PC power", () => {
    const help = index.match(/<p id="wake-conversation-help"[^>]*>([^<]+)<\/p>/)[1];
    assert.match(help, /“Stop listening” or “Ariadne, shutdown” turns off PC microphone capture and startup listening/);
    assert.match(help, /“Go to sleep” returns to wake-word listening while the microphone stays on/);
    assert.match(help, /do not shut down the PC/);
  });
  await check("Off and unpaired states schedule zero lightweight wake reads", async () => {
    const h = harness(); assert.equal(h.timers.size, 0); assert.equal(await h.tick(), false);
    h.state.authenticated = false; h.render(on()); assert.equal(h.timers.size, 0); assert.equal(h.calls.length, 0);
  });
  await check("Active capture schedules one two-second status-only read without accelerating full status", async () => {
    const h = harness(); h.render(on()); h.api.renderWakeStatus(h.state.status.wake); assert.equal(h.timers.size, 1); assert.equal(h.timerHistory[0].delay, 2000); await h.tick(); assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, "/voice/wake"); assert.equal(h.calls[0].options.method, undefined); assert.equal(h.calls[0].options.body, undefined); assert.equal(h.timers.size, 1); assert.equal(h.refreshes, 0);
  });
  await check("A pending lightweight read holds one slot with no overlapping request or timer", async () => {
    const h = harness(), pending = defer(); h.handler = () => pending.promise; h.render(on()); await h.tick(); assert.equal(h.calls.length, 1); assert.equal(h.timers.size, 0); h.api.renderWakeStatus(h.state.status.wake); h.timerHistory[0].callback(); await h.settle(); assert.equal(h.calls.length, 1); assert.equal(h.timers.size, 0); pending.resolve(on()); await h.settle(); assert.equal(h.timers.size, 1);
  });
  await check("A confirmed stopped snapshot disarms status reads", async () => {
    const h = harness(); h.handler = async () => off(); h.render(on()); await h.tick(); assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.equal(h.timers.size, 0); assert.equal(await h.tick(), false); assert.equal(h.calls.length, 1);
  });
  await check("Explicit stop aborts an in-flight read and discards its late active response", async () => {
    const h = harness(), pending = defer(); h.handler = async url => url.endsWith("/stop") ? off() : pending.promise; h.render(on()); await h.tick(); const signal = h.calls[0].signal; await h.api.setWakeListening(false); assert.equal(signal.aborted, true); assert.equal(h.timers.size, 0); pending.resolve(on()); await h.settle(); assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.equal(h.timers.size, 0); assert.equal(h.calls.length, 2); assert.equal(h.calls[1].url, "/voice/wake/stop");
  });
  await check("Lock cancels a timer and its stale callback cannot consume the new pairing timer", async () => {
    const h = harness(); h.render(on()); const old = h.timerHistory[0]; h.state.authenticated = false; h.api.resetWakeControls(); assert.equal(h.timers.size, 0); h.state.authenticated = true; h.render(on()); const fresh = h.timerHistory[1]; old.callback(); assert.equal(h.timers.size, 1); assert.equal(h.timers.has(fresh.id), true); assert.equal(h.calls.length, 0); await h.tick(); assert.equal(h.calls.length, 1);
  });
  await check("Lock and same-token re-pairing discard an old in-flight response without overlapping reads", async () => {
    const h = harness(), pending = defer(); h.handler = () => pending.promise; h.render(on()); await h.tick(); h.state.authenticated = false; h.api.resetWakeControls(); assert.equal(h.calls[0].signal.aborted, true); h.state.authenticated = true; h.render(on({conversation_active:false, state:"listening"})); assert.equal(h.timers.size, 0); pending.resolve(on()); await h.settle(); assert.equal(h.el("wake-indicator-label").textContent, "Waiting for Ariadne"); assert.equal(h.calls.length, 1); assert.equal(h.timers.size, 1);
  });
  await check("A token change discards a late status response", async () => {
    const h = harness(), pending = defer(); h.handler = () => pending.promise; h.render(on({conversation_active:false, state:"listening"})); await h.tick(); h.state.token = "new-owner-key"; pending.resolve(on()); await h.settle(); assert.equal(h.el("wake-indicator-label").textContent, "Waiting for Ariadne"); assert.equal(h.calls.length, 1);
  });
  await check("A failed lightweight read shows unknown and waits for the existing full refresh to recover", async () => {
    const h = harness(); h.handler = async () => {throw new Error("offline");}; h.render(on()); await h.tick(); assert.equal(h.el("wake-indicator-label").textContent, "Voice unavailable"); assert.equal(h.el("wake-stop").disabled, false); assert.equal(h.timers.size, 0); assert.equal(h.toasts.length, 0); h.render(on()); assert.equal(h.timers.size, 1);
  });
  await check("An older full-status response cannot re-arm the microphone view after explicit stop", async () => {
    const h = harness(), full = defer(); h.render(on()); h.handler = async url => url === "/status" ? full.promise : off(); const old = h.api.fullStatusFixture({quiet:true}); await h.api.setWakeListening(false); full.resolve({wake:on(), active_runs:0}); await old; assert.equal(h.state.status.wake.active, false); assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.equal(h.timers.size, 0);
  });
  await check("An older full-status response cannot overwrite a newer Awake snapshot", async () => {
    const h = harness(), full = defer(); h.render(on({conversation_active:false, state:"listening"})); h.handler = async url => url === "/status" ? full.promise : on(); const old = h.api.fullStatusFixture({quiet:true}); await h.tick(); assert.equal(h.el("wake-indicator-label").textContent, "Awake"); full.resolve({wake:on({conversation_active:false, state:"listening"}), active_runs:0}); await old; assert.equal(h.el("wake-indicator-label").textContent, "Awake"); assert.equal(h.timers.size, 1);
  });
  await check("A fresh full-status stop remains authoritative when no newer wake snapshot intervenes", async () => {
    const h = harness(); h.render(on()); h.handler = async () => ({wake:off(), active_runs:0}); await h.api.fullStatusFixture({quiet:true}); assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.equal(h.timers.size, 0);
  });
  await check("Initial HTML does not assert a microphone state before a confirmed status read", () => {
    assert.match(index, /id="wake-indicator-label">Voice unavailable<\/span>/);
    assert.match(index, /id="wake-conversation-status"[^>]*>Pair this tab to see the PC microphone state\.<\/p>/);
    assert.doesNotMatch(index, /id="wake-indicator-label">Mic off|id="wake-conversation-status"[^>]*>Microphone off/);
  });
  await check("Actual initial browser suppression invalidates an older lightweight Awake response", async () => {
    const h = harness(), light = defer(); h.render(on());
    h.handler = async url => url === "/voice/wake" ? light.promise : url === "/voice/speak" ? {} : on({suppressed:true});
    await h.tick(); const signal = h.calls[0].signal; await h.api.speakFixture("Fixture reply", h.control()); assert.equal(signal.aborted, true); assert.equal(h.playCalls, 1);
    light.resolve(on()); await h.settle(); assert.equal(h.el("wake-indicator-label").textContent, "Listening paused"); assert.equal(h.state.status.wake.suppressed, true); assert.equal(h.calls.filter(call => call.url === "/voice/wake/start").length, 0);
  });
  await check("Actual initial browser suppression survives an older full-status snapshot", async () => {
    const h = harness(), full = defer(); h.render(on());
    h.handler = async url => url === "/status" ? full.promise : url === "/voice/speak" ? {} : on({suppressed:true});
    const old = h.api.fullStatusFixture({quiet:true}); await h.api.speakFixture("Fixture reply", h.control()); full.resolve({wake:on(), active_runs:0}); await old; assert.equal(h.el("wake-indicator-label").textContent, "Listening paused"); assert.equal(h.state.status.wake.suppressed, true);
  });
  await check("Actual six-second suppression renewal invalidates an older lightweight response", async () => {
    const h = harness(), light = defer(); h.render(on());
    h.handler = async url => url === "/voice/speak" ? {} : on({suppressed:true}); await h.api.speakFixture("Fixture reply", h.control());
    h.handler = async url => url === "/voice/wake" ? light.promise : on({suppressed:true}); await h.tick(); const oldRead = h.calls.find(call => call.url === "/voice/wake");
    const renewal = h.intervals.values().next().value; assert.equal(renewal.delay, 6000); await renewal.callback(); assert.equal(oldRead.signal.aborted, true); light.resolve(on()); await h.settle(); assert.equal(h.el("wake-indicator-label").textContent, "Listening paused");
  });
  await check("Actual suppression renewal survives an older full-status response", async () => {
    const h = harness(), full = defer(); h.render(on()); h.handler = async url => url === "/voice/speak" ? {} : on({suppressed:true}); await h.api.speakFixture("Fixture reply", h.control());
    h.handler = async url => url === "/status" ? full.promise : on({suppressed:true}); const old = h.api.fullStatusFixture({quiet:true}); await h.intervals.values().next().value.callback(); full.resolve({wake:on(), active_runs:0}); await old; assert.equal(h.el("wake-indicator-label").textContent, "Listening paused");
  });
  await check("A late initial suppression response cannot undo a newer explicit microphone stop", async () => {
    const h = harness(), suppression = defer(); h.render(on()); h.handler = async url => url === "/voice/speak" ? {} : url.endsWith("/stop") ? off() : suppression.promise;
    const speech = h.api.speakFixture("Fixture reply", h.control()); await h.settle(); await h.api.setWakeListening(false); suppression.resolve(on({suppressed:true})); await speech; assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.equal(h.playCalls, 0); assert.equal(h.timers.size, 0);
  });
  await check("A late suppression renewal cannot undo a newer explicit microphone stop", async () => {
    const h = harness(), suppression = defer(); h.render(on()); h.handler = async url => url === "/voice/speak" ? {} : on({suppressed:true}); await h.api.speakFixture("Fixture reply", h.control());
    h.handler = async url => url.endsWith("/stop") ? off() : suppression.promise; const renewal = h.intervals.values().next().value.callback(); await h.settle(); await h.api.setWakeListening(false); suppression.resolve(on({suppressed:true})); await renewal; assert.equal(h.el("wake-indicator-label").textContent, "Mic off"); assert.equal(h.el("speech-player").paused, true); assert.equal(h.intervals.size, 0);
  });
  await check("Actual API still locks the currently rejected pairing", async () => {
    const h = harness(); h.fetchHandler = async () => ({ok:false, status:401, json:async () => ({error:"Current key rejected"})}); await assert.rejects(h.api.realApiFixture("/voice/wake"), /Current key rejected/); assert.equal(h.locks.length, 1); assert.equal(h.state.authenticated, false);
  });
  await check("A deferred real 401 body cannot lock a re-paired session using the same token", async () => {
    const h = harness(), body = defer(); h.fetchHandler = async () => ({ok:false, status:401, json:() => body.promise}); const old = h.api.realApiFixture("/voice/wake"); await h.settle();
    h.state.authenticated = false; h.state.pairingGeneration += 1; h.api.resetWakeControls(); h.state.authenticated = true; h.render(off()); body.resolve({error:"Old pairing rejected"}); await assert.rejects(old, /Old pairing rejected/); assert.equal(h.locks.length, 0); assert.equal(h.state.authenticated, true);
    assert.match(source, /async function connect\(\) \{\s+state\.pairingGeneration \+= 1;/); assert.match(source, /function lockSession\([^)]*\) \{\s+internetGeneration \+= 1;\s+state\.pairingGeneration \+= 1;/);
  });
  await check("A deferred real 403 body cannot lock a session with a different current token", async () => {
    const h = harness(), body = defer(); h.fetchHandler = async () => ({ok:false, status:403, json:() => body.promise}); const old = h.api.realApiFixture("/voice/wake"); await h.settle(); h.state.token = "different-owner-key"; body.resolve({error:"Old token rejected"}); await assert.rejects(old, /Old token rejected/); assert.equal(h.locks.length, 0); assert.equal(h.state.authenticated, true);
  });
  await check("An aborted real 401 body cannot lock the current pairing after cancellation", async () => {
    const h = harness(), body = defer(), controller = new AbortController(); h.fetchHandler = async () => ({ok:false, status:401, json:() => body.promise}); const old = h.api.realApiFixture("/voice/wake", {signal:controller.signal}); await h.settle(); controller.abort(); body.reject(Object.assign(new Error("Aborted body"), {name:"AbortError"})); await assert.rejects(old, /Server request failed \(401\)/); assert.equal(h.locks.length, 0); assert.equal(h.state.authenticated, true);
  });
  await check("An aborted real 401 body cannot lock a same-token re-pairing", async () => {
    const h = harness(), body = defer(), controller = new AbortController(); h.fetchHandler = async () => ({ok:false, status:401, json:() => body.promise}); const old = h.api.realApiFixture("/voice/wake", {signal:controller.signal}); await h.settle();
    h.state.pairingGeneration += 1; h.api.resetWakeControls(); h.render(off()); controller.abort(); body.reject(Object.assign(new Error("Aborted body"), {name:"AbortError"})); await assert.rejects(old, /Server request failed \(401\)/); assert.equal(h.locks.length, 0); assert.equal(h.state.authenticated, true);
  });
  console.log(JSON.stringify({passed:checks.length, failures:0, checks, app_js_sha256:crypto.createHash("sha256").update(source).digest("hex"), index_html_sha256:crypto.createHash("sha256").update(index).digest("hex"), microphone_used:false}, null, 2));
}
main().catch(error => {console.error(error); process.exitCode = 1;});
