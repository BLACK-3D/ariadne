"use strict";
// Usage: node scripts/check_command_snapshot.cjs [--receipt PATH]
// Independent command-center presentation checks. No browser, owner data,
// network, microphone, real timers or model execution is used by this verifier.
// The VM clock is fixed so future/expired schedule fixtures stay deterministic.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== "--receipt" || !args[1])) {
  console.error("Usage: node scripts/check_command_snapshot.cjs [--receipt PATH]");
  process.exit(2);
}
const receiptPath = args.length ? path.resolve(args[1]) : null;
const fixtureNow = Date.UTC(2026, 9, 28, 20, 0, 0);
class FixtureDate extends Date {
  constructor(...values) { super(...(values.length ? values : [fixtureNow])); }
  static now() { return fixtureNow; }
}
const source = fs.readFileSync(path.join(root, "ariadne/static/command.js"), "utf8");
const appSource = fs.readFileSync(path.join(root, "ariadne/static/app.js"), "utf8");
const checks = [];
const clone = value => JSON.parse(JSON.stringify(value));
const elements = new Map();
const documentEvents = new Map(), bodyClasses = new Set();
let focused = null;
class Element {
  constructor(id) { this.id = id; this.dataset = {}; this.attributes = {}; this.handlers = {}; this.writes = 0; this._text = ""; }
  get textContent() { return this._text; }
  set textContent(value) { this._text = String(value); this.writes += 1; }
  set innerHTML(value) { throw new Error("Untrusted state must never enter innerHTML"); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  focus() { focused = this.id; }
}
for (const id of ["view-missions", "command-presence", "command-state", "command-state-detail", "command-phase-announcement",
  "command-model", "command-model-note", "command-mic", "command-network", "command-active", "command-review",
  "command-next-brief", "command-brief-note", "command-sync", "command-log-count", "command-review-open",
  "command-refresh", "command-new-mission", "approval-button"]) elements.set(id, new Element(id));
const forbidden = () => { throw new Error("The presence module attempted an independent timer or external request"); };
const documentFixture = {
  hidden: false,
  body: {classList: {toggle(name, enabled) { if (enabled) bodyClasses.add(name); else bodyClasses.delete(name); }}},
  addEventListener: (name, handler) => documentEvents.set(name, handler),
  getElementById: id => elements.get(id) || null,
  querySelector: selector => selector === "#approval-list button" ? elements.get("approval-button") : null,
};
const context = vm.createContext({window: {}, document: documentFixture,
  Date: FixtureDate, Intl, fetch: forbidden, setTimeout: forbidden, setInterval: forbidden, requestAnimationFrame: forbidden});
vm.runInContext(source, context, {filename: "command.js", timeout: 2000});
const command = context.window.ariadneCommand;
assert.equal(typeof command?.snapshot, "function");
checks.push("module initialization has no network, timers or model work");

function base() { return {authenticated: true, view: "missions", runs: [], detail: null, selectedId: null,
  creating: false, recording: false, transcribing: false,
  status: {model: "local-fixture:9b", ollama: {available: true, models: [{name: "local-fixture:9b"}]},
    paused: false, active_runs: 0, wake: {active: false}, internet: {config: {enabled: false}}}}; }
function test(label, change, expected, inspect) {
  const state = base(); change(state);
  const before = JSON.stringify(state), result = command.snapshot(state);
  assert.equal(result.phase, expected, label);
  assert.equal(JSON.stringify(state), before, label+" mutated input state");
  if (inspect) inspect(result);
  checks.push(label);
}

function checkScrollContracts() {
  const start = appSource.indexOf("function missionScroller()"), end = appSource.indexOf("function renderActivitySummary(", start);
  assert.ok(start >= 0 && end > start);
  let bottom = 650;
  const items = new Map();
  const make = id => {
    const item = new Element(id);
    item.classList = {add() {}, remove() {}};
    item.append = () => {};
    item.getBoundingClientRect = () => ({top: 100, bottom});
    items.set(id, item);
    return item;
  };
  for (const id of ["conversation", "main-content", "execution-trail", "mission-layout", "mission-heading", "mission-title",
    "mission-status", "cancel-run", "welcome", "message-list", "activity-live"]) make(id);
  const main = items.get("main-content"), body = {}, frames = [];
  main.clientHeight = 600; main.clientTop = 0; main.scrollHeight = 4000; main.scrollTop = 200;
  items.get("conversation").parentElement = main; main.parentElement = body;
  const state = {selectedId: "scroll-fixture", view: "missions", authenticated: true, status: {}};
  const sandbox = vm.createContext({state, document: {body, scrollingElement: {}},
    $: selector => items.get(selector.replace(/^[#.]/, "")) || null,
    getComputedStyle: element => ({overflowY: element === main ? "auto" : "visible"}),
    isActive: run => ["queued", "running", "waiting_approval", "verifying"].includes(run.status),
    executionLabel: run => run.status, node: () => make("generated"),
    clear: () => {}, renderVerification() {}, renderActivitySummary() {}, renderApprovals() {}, renderEvents() {},
    requestAnimationFrame: callback => frames.push(callback), fetch: forbidden});
  vm.runInContext(appSource.slice(start, end), sandbox, {timeout: 2000});
  const detail = {run: {id: "scroll-fixture", status: "completed", title: "Fixture"}, messages: []};
  sandbox.renderRun(detail, true); assert.equal(frames.length, 1); frames.shift()();
  assert.equal(main.scrollTop, 0); assert.equal(items.get("execution-trail").open, false);
  checks.push("selecting a completed mission starts at its top rather than the page footer");
  main.scrollTop = 200; bottom = 650;
  sandbox.renderRun(detail, false); bottom = 900; frames.shift()();
  assert.equal(main.scrollTop, 424);
  checks.push("subsequent output following targets the message end rather than ledger or event footer");
  main.scrollTop = 200; bottom = 650;
  sandbox.renderRun(detail, false); main.scrollTop = 260; frames.shift()();
  assert.equal(main.scrollTop, 260);
  checks.push("a user scroll after render takes precedence over a delayed output-follow callback");
  for (const change of [() => { state.selectedId = "other"; }, () => { state.view = "agents"; }, () => { state.authenticated = false; }]) {
    Object.assign(state, {selectedId: "scroll-fixture", view: "missions", authenticated: true});
    main.scrollTop = 200; bottom = 650; sandbox.renderRun(detail, false); change(); frames.shift()();
    assert.equal(main.scrollTop, 200);
  }
  checks.push("delayed scrolling cannot affect a different mission, hidden view or locked console");
  Object.assign(state, {selectedId: "scroll-fixture", view: "missions", authenticated: true});
  main.scrollTop = 200; bottom = 1400; sandbox.renderRun(detail, false);
  assert.equal(frames.length, 0); assert.equal(main.scrollTop, 200);
  checks.push("output outside the followed viewport does not pull the reader away from prior content");
  const active = {run: {...detail.run, status: "running"}, messages: []};
  sandbox.renderRun(active, true); frames.shift()();
  assert.equal(items.get("execution-trail").open, true);
  items.get("execution-trail").open = false; bottom = 1400; sandbox.renderRun(active, false);
  assert.equal(items.get("execution-trail").open, false);
  checks.push("execution-trail first-render defaults preserve a user's later collapse during polling");
}
test("locked state conceals prior runtime/microphone counts", s => { s.authenticated = false; s.status.wake.active = true; }, "locked", r => {
  assert.equal(r.activeCount, null); assert.equal(r.mic, "Status unavailable"); assert.equal(r.model, "Not connected");
});
test("installed local model with no activity is standby", () => {}, "standby");
test("missing runtime is unavailable rather than executing", s => { s.status.ollama.available = false; }, "unavailable");
test("missing selected model is unavailable", s => { s.status.model = "missing:9b"; }, "unavailable");
test("pause comes from actual saved server status", s => { s.status.paused = true; }, "paused");
test("submitting describes an actual unconfirmed launch", s => { s.creating = true; }, "submitting");
test("waiting approval produces review and an actual mission target", s => {
  s.runs = [{id: "review-fixture", status: "waiting_approval"}]; s.status.active_runs = 1;
}, "review", r => { assert.equal(r.reviewCount, 1); assert.equal(r.firstReviewId, "review-fixture"); });
test("running mission and separate pending review remain distinguishable", s => {
  s.runs = [{id: "review-fixture", status: "waiting_approval"}, {id: "run-fixture", status: "running", title: "Actual run"}]; s.status.active_runs = 2;
}, "executing", r => { assert.equal(r.reviewCount, 1); assert.equal(r.activeCount, 2); });
test("verification is identified from actual mission status", s => {
  s.runs = [{id: "verify-fixture", status: "verifying"}]; s.status.active_runs = 1;
}, "executing", r => assert.match(r.label, /verifying/i));
test("queued mission does not claim inference is running", s => {
  s.runs = [{id: "queued-fixture", status: "queued"}]; s.status.active_runs = 1;
}, "queued", r => assert.doesNotMatch(r.label, /thinking|executing/i));
test("active count without detail does not invent a running state", s => { s.status.active_runs = 1; }, "active", r => assert.doesNotMatch(r.label, /thinking|executing/i));
test("completed selected mission does not mask another running mission", s => {
  s.selectedId = "old"; s.detail = {run: {id: "old", status: "completed"}};
  s.runs = [{id: "old", status: "running"}, {id: "current", status: "running", title: "Current mission"}]; s.status.active_runs = 1;
}, "executing", r => { assert.equal(r.activeCount, 1); assert.equal(r.detail, "Current mission"); });
test("browser recording is distinct from PC wake-word capture", s => { s.recording = {}; }, "recording", r => assert.equal(r.mic, "Browser recording"));
test("local transcription reflects actual client processing", s => { s.transcribing = true; }, "transcribing");
test("actual PC playback is identified as speaking", s => { s.status.wake = {active: true, speaking: true}; }, "speaking");
test("actual wake command window is identified", s => { s.status.wake = {active: true, state: "awaiting_command"}; }, "listening");
test("actual idle wake listener distinguishes ongoing speech processing from language-model calls", s => { s.status.wake = {active: true, state: "listening", suppressed: false}; }, "listening", r => assert.match(r.detail, /no idle language-model calls/i));
test("suppressed wake listener does not claim live listening", s => { s.status.wake = {active: true, state: "listening", suppressed: true}; }, "standby", r => assert.match(r.mic, /muted/i));
const inaccessible = command.snapshot(base(), null, true);
assert.equal(inaccessible.phase, "unreachable"); assert.match(inaccessible.detail, /last confirmed/i);
checks.push("connection failure clearly labels last-confirmed values");
const briefing = {config: {enabled: true, paused: false, timezone: "America/Chicago", region: {name: "Public city fixture"}}, next_due_at: "2026-10-29T11:00:00+00:00"};
assert.equal(command.snapshot(base(), briefing).nextBrief, "06:00");
const held = clone(briefing); held.config.paused = true; assert.match(command.snapshot(base(), held).nextBrief, /held/i);
const locked = base(); locked.authenticated = false; assert.doesNotMatch(JSON.stringify(command.snapshot(locked, briefing)), /Public city fixture/);
checks.push("briefing schedule uses actual time zone, holds and auth boundary");
const located = clone(briefing); located.config.region.name += " · PC position";
assert.match(command.snapshot(base(), located).briefNote, /America\/Chicago\nPublic city fixture$/);
assert.match(located.config.region.name, /PC position$/);
checks.push("overview compacts regional provenance label without changing saved briefing data");
const network = base();
assert.match(command.snapshot(network).network, /^Web off/);
network.status.internet = {config: {enabled: true}, connection_verified: false};
assert.equal(command.snapshot(network).network, "Public web · unverified");
network.status.internet.connection_verified = true;
assert.equal(command.snapshot(network).network, "Public web · recently verified");
checks.push("public web policy and verified reachability remain distinct");
const past = clone(briefing); past.next_due_at = "2020-01-01T12:00:00+00:00";
assert.match(command.snapshot(base(), past).nextBrief, /refresh/i);
checks.push("expired schedule snapshot requests manual refresh without inventing a new due time");
checkScrollContracts();

async function lifecycle() {
  const state = base(), requests = []; let refreshes = 0, loads = 0, mission = null;
  const helpers = {state, api: async endpoint => { requests.push(endpoint); return briefing; },
    refreshStatus: async () => { refreshes += 1; }, loadRuns: async () => { loads += 1; },
    newMission: () => {}, selectRun: async id => { mission = id; }};
  command.attach(helpers);
  assert.equal(requests.length, 0);
  checks.push("attachment/rendering performs no status fetch or public request");
  await command.connected();
  assert.deepEqual(requests, ["/briefing"]);
  checks.push("connecting adds only one local briefing-settings read");
  assert.ok(bodyClasses.has("command-motion-visible"));
  documentFixture.hidden = true; documentEvents.get("visibilitychange")();
  assert.ok(!bodyClasses.has("command-motion-visible"));
  documentFixture.hidden = false; state.view = "agents"; documentEvents.get("visibilitychange")();
  assert.ok(!bodyClasses.has("command-motion-visible"));
  state.view = "missions"; command.render(); assert.ok(bodyClasses.has("command-motion-visible"));
  assert.deepEqual(requests, ["/briefing"]);
  checks.push("motion eligibility stops when hidden or away from overview without network work");
  const announcements = elements.get("command-phase-announcement").writes;
  command.render(); command.render();
  assert.equal(elements.get("command-phase-announcement").writes, announcements);
  checks.push("unchanged phase is not repeatedly announced during status rendering");
  state.runs = [{id: "literal-fixture", status: "running", title: '<img src=x onerror="hostile()">'}]; state.status.active_runs = 1;
  command.render();
  assert.equal(elements.get("command-state-detail").textContent, '<img src=x onerror="hostile()">');
  checks.push("untrusted mission titles remain literal text in the presence UI");
  await command.refresh();
  assert.equal(refreshes, 1); assert.equal(loads, 1); assert.deepEqual(requests, ["/briefing", "/briefing"]);
  checks.push("manual refresh uses existing local readers without polling timers");
  state.runs = [{id: "review-fixture", status: "waiting_approval"}]; command.render();
  await elements.get("command-review-open").handlers.click();
  assert.equal(mission, "review-fixture"); assert.equal(focused, "approval-button");
  checks.push("review shortcut selects the waiting mission and focuses its action controls");
  let resolveRead;
  helpers.api = endpoint => { requests.push(endpoint); return new Promise(resolve => { resolveRead = resolve; }); };
  const pending = command.connected();
  state.authenticated = false; command.locked(); resolveRead(briefing); await pending;
  assert.equal(elements.get("command-state").textContent, "Console locked");
  assert.equal(elements.get("command-next-brief").textContent, "Not loaded");
  assert.equal(elements.get("command-refresh").disabled, true);
  checks.push("locking invalidates an in-flight local briefing read and disables actions");
}
lifecycle().then(() => {
  const receipt = {created_at_utc: new Date().toISOString(), checks, passed: checks.length, failures: 0,
    source: "ariadne/static/command.js", sha256: crypto.createHash("sha256").update(source).digest("hex"),
    source_sha256: {"ariadne/static/command.js": crypto.createHash("sha256").update(source).digest("hex"),
      "ariadne/static/app.js": crypto.createHash("sha256").update(appSource).digest("hex"),
      "scripts/check_command_snapshot.cjs": crypto.createHash("sha256").update(fs.readFileSync(__filename)).digest("hex")},
    fixture_now_utc: new Date(fixtureNow).toISOString(),
    scope: "Pure snapshot, minimal DOM lifecycle and actual renderRun scroll contracts; no live browser layout, server operations, microphone or model execution."};
  if (receiptPath) {
    fs.mkdirSync(path.dirname(receiptPath), {recursive:true});
    fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2)+"\n");
  }
  console.log(JSON.stringify({passed: checks.length, failures: 0, ...(receiptPath ? {receipt:receiptPath} : {})}));
}).catch(error => { console.error(error.stack); process.exitCode = 1; });
