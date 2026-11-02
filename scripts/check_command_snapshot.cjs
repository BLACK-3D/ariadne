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
const commandFrames = [];
let focused = null;
class Element {
  constructor(id) { this.id = id; this.dataset = {}; this.attributes = {}; this.handlers = {}; this.writes = 0; this._text = ""; }
  get textContent() { return this._text; }
  set textContent(value) { this._text = String(value); this.writes += 1; }
  set innerHTML(value) { throw new Error("Untrusted state must never enter innerHTML"); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  focus(options) { focused = this.id; this.focusOptions = options; }
}
for (const id of ["view-missions", "command-presence", "command-state", "command-state-detail", "command-phase-announcement",
  "command-model", "command-model-note", "command-mic", "command-network", "command-active", "command-review",
  "command-next-brief", "command-brief-note", "command-sync", "command-log-count", "command-review-open",
  "command-refresh", "command-new-mission", "command-focus-title", "command-focus-detail", "command-focus-open", "approval-list", "approval-button"]) elements.set(id, new Element(id));
const forbidden = () => { throw new Error("The presence module attempted an independent timer or external request"); };
const documentFixture = {
  hidden: false,
  body: {classList: {toggle(name, enabled) { if (enabled) bodyClasses.add(name); else bodyClasses.delete(name); }}},
  addEventListener: (name, handler) => documentEvents.set(name, handler),
  getElementById: id => elements.get(id) || null,
  querySelector: selector => selector === "#approval-list button" ? elements.get("approval-button") : null,
};
const context = vm.createContext({window: {}, document: documentFixture,
  Date: FixtureDate, Intl, fetch: forbidden, setTimeout: forbidden, setInterval: forbidden,
  requestAnimationFrame: callback => { commandFrames.push(callback); return commandFrames.length; }});
vm.runInContext(source, context, {filename: "command.js", timeout: 2000});
const command = context.window.ariadneCommand;
assert.equal(typeof command?.snapshot, "function");
assert.equal(commandFrames.length, 0);
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

function checkKeyboardContracts() {
  // Evaluate the shipped listener and menu function, rather than recreating
  // their conditions in this verifier. The other app listeners are not run.
  const listenerStart = appSource.indexOf('document.addEventListener("keydown", event => {');
  const listenerEnd = appSource.indexOf("\nlet internetConfig", listenerStart);
  const menuStart = appSource.indexOf("let directoryReturn = null;");
  const menuEnd = appSource.indexOf("async function showView(", menuStart);
  assert.ok(listenerStart >= 0 && listenerEnd > listenerStart && menuStart >= 0 && menuEnd > menuStart);
  const listenerSource = appSource.slice(listenerStart, listenerEnd);
  const menuSource = appSource.slice(menuStart, menuEnd);

  function fixture() {
    const state = {authenticated: true}, items = new Map(), body = new Set();
    const effects = {missions: 0, prevented: 0, focuses: []};
    let listener = null, dialog = null;
    const document = {activeElement: null, body: {classList: {
      toggle(name, enabled) { if (enabled) body.add(name); else body.delete(name); },
    }}, addEventListener(name, callback) { assert.equal(name, "keydown"); assert.equal(listener, null); listener = callback; }};
    function element(id, tagName = "BUTTON") {
      const classes = new Set();
      const item = {id, tagName, isContentEditable: false, isConnected: true, hidden: false, inert: false,
        parentElement: null, attributes: {}, rects: 1,
        classList: {contains: name => classes.has(name), toggle(name, enabled) {
          if (enabled) classes.add(name); else classes.delete(name);
        }},
        setAttribute(name, value) { this.attributes[name] = String(value); },
        getClientRects() { return Array(this.rects).fill({}); },
        contains(target) { for (let item = target; item; item = item.parentElement) if (item === this) return true; return false; },
        focus() { document.activeElement = this; effects.focuses.push(this.id); },
      };
      items.set(id, item);
      return item;
    }
    for (const id of ["sidebar", "sidebar-scrim", "menu-toggle", "directory-close", "new-mission", "middle", "outside", "hidden", "no-rects"]) element(id);
    const sidebar = items.get("sidebar"), first = items.get("directory-close"), middle = items.get("middle"), last = items.get("new-mission");
    for (const item of [first, middle, last, items.get("hidden"), items.get("no-rects")]) item.parentElement = sidebar;
    items.get("hidden").hidden = true; items.get("no-rects").rects = 0;
    sidebar.hidden = true; sidebar.inert = true;
    let controls = [items.get("hidden"), first, middle, last, items.get("no-rects")];
    document.activeElement = items.get("outside");
    const sandbox = vm.createContext({state, document,
      $: selector => selector === "dialog[open]" ? dialog : items.get(selector.replace(/^#/, "")) || null,
      $$: (_selector, scope) => { assert.equal(scope, sidebar); return controls; },
      newMission: () => { effects.missions += 1; },
      fetch: forbidden, setTimeout: forbidden, setInterval: forbidden, requestAnimationFrame: forbidden});
    vm.runInContext(menuSource + "\n" + listenerSource, sandbox, {filename: "app.js keyboard/menu fixture", timeout: 2000});
    assert.equal(typeof listener, "function");
    return {state, document, effects, items, first, middle, last,
      menu: open => sandbox.setMenu(open), dialog: open => { dialog = open ? {} : null; },
      controls: value => { controls = value; },
      fire: values => listener({ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
        preventDefault() { effects.prevented += 1; }, ...values}),
      snapshot: () => JSON.stringify({effects, active: document.activeElement?.id, body: [...body],
        sidebar: {open: sidebar.classList.contains("open"), hidden: sidebar.hidden, inert: sidebar.inert, attributes: sidebar.attributes},
        menu: items.get("menu-toggle").attributes, scrimHidden: items.get("sidebar-scrim").hidden}),
    };
  }

  const malformed = fixture(); malformed.menu(true);
  let coerced = 0;
  const unusualKeys = [undefined, null, 7, true, {}, [], new String("n"), Symbol("n"), 1n,
    {toLowerCase() { coerced += 1; return "n"; }}];
  const before = malformed.snapshot();
  assert.doesNotThrow(() => malformed.fire({}));
  assert.equal(malformed.snapshot(), before);
  for (const key of unusualKeys) {
    assert.doesNotThrow(() => malformed.fire({key}));
    assert.equal(malformed.snapshot(), before);
  }
  assert.equal(coerced, 0);
  checks.push("actual keyboard listener ignores missing, undefined, null and non-string keys without coercion or side effects");

  for (const values of [{key: "n"}, {key: "N"}, {key: "N", shiftKey: true}]) {
    const keyboard = fixture(); keyboard.fire(values);
    assert.equal(keyboard.effects.missions, 1); assert.equal(keyboard.effects.prevented, 1);
  }
  checks.push("actual n and N keyboard shortcuts request exactly one new mission and prevent native handling");
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
    const keyboard = fixture(); keyboard.document.activeElement = {tagName}; keyboard.fire({key: "n"});
    assert.equal(keyboard.effects.missions, 0); assert.equal(keyboard.effects.prevented, 0);
  }
  const editable = fixture(); editable.document.activeElement = {tagName: "DIV", isContentEditable: true}; editable.fire({key: "N"});
  assert.equal(editable.effects.missions, 0); assert.equal(editable.effects.prevented, 0);
  checks.push("typing in inputs, textareas, selects or contenteditable does not trigger a new mission");
  const locked = fixture(); locked.state.authenticated = false; locked.fire({key: "n"}); locked.menu(true);
  assert.equal(locked.effects.missions, 0); assert.equal(locked.effects.prevented, 0); assert.equal(locked.items.get("sidebar").inert, true);
  checks.push("locked console suppresses mission shortcut and directory opening");
  for (const modifier of ["ctrlKey", "metaKey", "altKey"]) {
    const keyboard = fixture(); keyboard.fire({key: "n", [modifier]: true});
    assert.equal(keyboard.effects.missions, 0); assert.equal(keyboard.effects.prevented, 0);
  }
  checks.push("control, command and alt combinations preserve native shortcuts instead of creating missions");
  const dialog = fixture(); dialog.dialog(true); dialog.fire({key: "N"});
  assert.equal(dialog.effects.missions, 0); assert.equal(dialog.effects.prevented, 0);
  checks.push("an open dialog suppresses the new-mission keyboard shortcut");
  const ordinary = fixture();
  for (const key of ["", "x", "Enter", "Escape", "Tab", "ArrowDown"]) ordinary.fire({key});
  assert.equal(ordinary.effects.missions, 0); assert.equal(ordinary.effects.prevented, 0); assert.deepEqual(ordinary.effects.focuses, []);
  checks.push("unrelated keys and closed-directory Escape or Tab remain native with no mission or focus changes");

  const escape = fixture(); escape.menu(true); escape.fire({key: "Escape"});
  assert.equal(escape.effects.prevented, 1); assert.equal(escape.document.activeElement.id, "outside");
  assert.equal(escape.items.get("sidebar").hidden, true); assert.equal(escape.items.get("sidebar").inert, true);
  assert.equal(escape.items.get("sidebar-scrim").hidden, true); assert.equal(escape.items.get("menu-toggle").attributes["aria-expanded"], "false");
  assert.equal(escape.effects.missions, 0);
  checks.push("Escape closes the actual directory, restores inert/ARIA state and returns focus to its opener");
  const detached = fixture(); detached.menu(true); detached.items.get("outside").isConnected = false; detached.fire({key: "Escape"});
  assert.equal(detached.document.activeElement.id, "menu-toggle");
  checks.push("Escape returns focus to the Resources toggle if the original opener was removed");
  const forward = fixture(); forward.menu(true); forward.document.activeElement = forward.last; forward.fire({key: "Tab"});
  assert.equal(forward.document.activeElement, forward.first); assert.equal(forward.effects.prevented, 1);
  checks.push("directory Tab wraps from the last visible control to the first and ignores hidden controls");
  const reverse = fixture(); reverse.menu(true); reverse.document.activeElement = reverse.first; reverse.fire({key: "Tab", shiftKey: true});
  assert.equal(reverse.document.activeElement, reverse.last); assert.equal(reverse.effects.prevented, 1);
  checks.push("directory Shift-Tab wraps from the first control to the last and ignores controls without layout boxes");
  for (const shiftKey of [false, true]) {
    const keyboard = fixture(); keyboard.menu(true); keyboard.document.activeElement = keyboard.items.get("outside"); keyboard.fire({key: "Tab", shiftKey});
    assert.equal(keyboard.document.activeElement, shiftKey ? keyboard.last : keyboard.first); assert.equal(keyboard.effects.prevented, 1);
  }
  checks.push("directory Tab recaptures outside focus at the correct forward or reverse boundary");
  const middle = fixture(); middle.menu(true); middle.document.activeElement = middle.middle;
  const middleFocuses = middle.effects.focuses.length; middle.fire({key: "Tab"}); middle.fire({key: "Tab", shiftKey: true});
  assert.equal(middle.effects.prevented, 0); assert.equal(middle.effects.focuses.length, middleFocuses);
  checks.push("directory interior Tab traversal remains native rather than forcibly moving focus");
  const empty = fixture(); empty.menu(true); empty.controls([]); empty.document.activeElement = empty.items.get("outside");
  assert.doesNotThrow(() => empty.fire({key: "Tab"})); assert.equal(empty.effects.prevented, 1);
  assert.equal(empty.effects.missions, 0);
  checks.push("directory Tab tolerates a temporarily empty focusable set without throwing or creating a mission");
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
test("wake-once conversation is awake without asking for another wake word", s => {
  s.status.wake = {active:true, state:"conversing", conversation_active:true};
}, "conversing", r => { assert.match(r.label, /awake/i); assert.match(r.detail, /without repeating Ariadne/); assert.match(r.mic, /conversation awake/); });
test("active conversation flag works during a listening snapshot", s => {
  s.status.wake = {active:true, state:"listening", conversation_active:true};
}, "conversing");
test("voice work never claims that a busy listener is waiting for speech", s => {
  s.status.wake = {active:true, state:"working", pending_run_id:"voice-work", conversation_active:true};
}, "active", r => { assert.match(r.label, /in progress/); assert.doesNotMatch(r.detail, /keep talking|listening|ready for/i); });
test("muted conversation does not advertise awake recognition", s => {
  s.status.wake = {active:true, state:"conversing", conversation_active:true, acknowledging:true};
}, "standby", r => { assert.match(r.mic, /muted/); assert.match(r.label, /paused/); });
test("starting capture is distinct from listening", s => {
  s.status.wake = {active:true, state:"starting"};
}, "standby", r => { assert.match(r.label, /starting/i); assert.doesNotMatch(r.detail, /is listening|keep talking/i); });
test("inactive microphone cannot imply an awake conversation", s => {
  s.status.wake = {active:false, state:"conversing", conversation_active:true};
}, "standby", r => assert.equal(r.mic, "Microphone off"));
test("absent microphone state is unavailable rather than off", s => { delete s.status.wake; }, "standby", r => assert.match(r.mic, /unavailable/i));
for (const [label, change, expected] of [
  ["pause", s => { s.status.paused = true; }, "paused"],
  ["submission", s => { s.creating = true; }, "submitting"],
  ["missing model", s => { s.status.ollama.available = false; }, "unavailable"],
]) test(`${label} retains precedence over an open voice conversation`, s => {
  s.status.wake = {active:true, state:"conversing", conversation_active:true}; change(s);
}, expected);
test("selected inspected mission stays in focus while another mission executes", s => {
  s.selectedId = "inspected";s.detail = {run:{id:"inspected", title:"Inspected result", status:"completed", verification_status:"passed"}};
  s.runs = [{id:"running", title:"Current work", status:"running"}];s.status.active_runs = 1;
}, "executing", r => { assert.equal(r.focusRunId, "inspected"); assert.equal(r.focusTitle, "Inspected result"); assert.match(r.focusDetail, /Selected mission/); assert.match(r.focusDetail, /checks passed/); assert.equal(r.detail, "Current work"); });
test("running work takes focus before queued and recent records", s => {
  s.runs = [{id:"recent", title:"Recent record", status:"completed"}, {id:"queued", title:"Queued item", status:"queued"}, {id:"running", title:"Running item", status:"running"}];
}, "executing", r => assert.equal(r.focusRunId, "running"));
test("queued work takes focus before the latest completed record", s => {
  s.runs = [{id:"recent", title:"Recent record", status:"completed"}, {id:"queued", title:"Queued item", status:"queued"}];
}, "queued", r => assert.equal(r.focusRunId, "queued"));
test("an uninspected selection does not replace observed running work", s => {
  s.selectedId = "not-opened";s.runs = [{id:"not-opened", title:"Uninspected", status:"completed"}, {id:"running", title:"Running item", status:"running"}];
}, "executing", r => assert.equal(r.focusRunId, "running"));
test("latest already loaded record is available without inventing active work", s => {
  s.runs = [{id:"latest", title:"Saved result", status:"completed"}];
}, "standby", r => { assert.equal(r.focusRunId, "latest"); assert.match(r.focusDetail, /Latest recorded mission/); assert.equal(r.activeCount, 0); });
test("empty history has no fabricated focus target", () => {}, "standby", r => { assert.equal(r.focusRunId, null); assert.match(r.focusTitle, /no recorded/i); });
const privateState = base();privateState.authenticated = false;privateState.selectedId = "PRIVATE_ROUTING_ID";
privateState.runs = [{id:"PRIVATE_ROUTING_ID", title:"PRIVATE_MISSION_TITLE", status:"waiting_approval"}];
privateState.detail = {run:privateState.runs[0]};
const privateSnapshot = command.snapshot(privateState);
assert.equal(privateSnapshot.focusRunId, null);assert.equal(privateSnapshot.firstReviewId, null);
assert.doesNotMatch(JSON.stringify(privateSnapshot), /PRIVATE_ROUTING_ID|PRIVATE_MISSION_TITLE/);
checks.push("locked focus and review routing reveal no previously inspected mission identity or title");
const staleState = base();staleState.runs = [{id:"stale", title:"Saved mission", status:"completed"}];
const staleFocus = command.snapshot(staleState, null, true);
assert.equal(staleFocus.focusRunId, "stale");assert.match(staleFocus.focusDetail, /^Last confirmed/);
checks.push("unreachable focus labels saved work as last confirmed instead of live activity");
function checkWakeCommandNotification() {
  const start = appSource.indexOf("function applyWakeSnapshot(wake) {"), end = appSource.indexOf("function confirmWakeSnapshot(wake) {", start);
  assert.ok(start >= 0 && end > start);
  let renders = 0, wakeRender = null;
  const fixture = {state:{status:{}},wakeStatusRevision:0,renderWakeStatus(value){wakeRender=value;},
    window:{ariadneCommand:{render(){renders+=1;}}},fetch:forbidden,setTimeout:forbidden,setInterval:forbidden};
  vm.createContext(fixture);vm.runInContext(appSource.slice(start,end),fixture);
  const wake = {active:true, conversation_active:true, state:"conversing"};fixture.applyWakeSnapshot(wake);
  assert.equal(fixture.state.status.wake,wake);assert.equal(wakeRender,wake);assert.equal(fixture.wakeStatusRevision,1);assert.equal(renders,1);
  checks.push("confirmed lightweight wake snapshots notify the overview immediately with no additional reader or timer");
}
checkWakeCommandNotification();
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
checkKeyboardContracts();

async function lifecycle() {
  const state = base(), requests = []; let refreshes = 0, loads = 0, mission = null, afterSelect = null;
  const helpers = {state, api: async endpoint => { requests.push(endpoint); return briefing; },
    refreshStatus: async () => { refreshes += 1; }, loadRuns: async () => { loads += 1; },
    newMission: () => {}, selectRun: async id => { mission = id; state.selectedId = id; state.view = "missions"; afterSelect?.(id); }};
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
  assert.equal(commandFrames.length, 0);
  checks.push("unchanged phase is not repeatedly announced during status rendering");
  checks.push("ordinary overview rendering schedules no animation frames or background work");
  state.runs = [{id: "literal-fixture", status: "running", title: '<img src=x onerror="hostile()">'}]; state.status.active_runs = 1;
  command.render();
  assert.equal(elements.get("command-state-detail").textContent, '<img src=x onerror="hostile()">');
  assert.equal(elements.get("command-focus-title").textContent, '<img src=x onerror="hostile()">');
  checks.push("untrusted mission titles remain literal text in the presence UI");
  await elements.get("command-focus-open").handlers.click();
  assert.equal(mission, "literal-fixture");
  checks.push("focus shortcut opens only the already observed mission through the existing selection handler");
  command.connectionLost();
  assert.match(elements.get("command-focus-detail").textContent, /^Last confirmed/);
  command.status();
  const removed = new Map();
  for (const id of ["command-focus-title", "command-focus-detail", "command-focus-open"]) { removed.set(id,elements.get(id));elements.delete(id); }
  assert.doesNotThrow(() => command.render());for (const [id,element] of removed) elements.set(id,element);
  checks.push("focus rendering tolerates markup without the optional focus controls");
  await command.refresh();
  assert.equal(refreshes, 1); assert.equal(loads, 1); assert.deepEqual(requests, ["/briefing", "/briefing"]);
  checks.push("manual refresh uses existing local readers without polling timers");
  state.runs = [{id: "review-fixture", status: "waiting_approval"}]; command.render();
  let scrollTop = 320; const approvalTop = 1165, viewportHeight = 900, revealCalls = [], frameOrder = [];
  const approvalList = elements.get("approval-list"), approvalButton = elements.get("approval-button");
  approvalList.scrollIntoView = options => {
    revealCalls.push(options); frameOrder.push("reveal");
    scrollTop = approvalTop - viewportHeight / 2;
  };
  // The real selectRun -> renderRun path queues a first-frame scrollTop=0.
  afterSelect = () => { commandFrames.push(() => { scrollTop = 0; frameOrder.push("dossier"); }); };
  focused = null;
  await elements.get("command-review-open").handlers.click();
  assert.equal(mission, "review-fixture"); assert.equal(focused, null); assert.equal(commandFrames.length, 2);
  commandFrames.shift()(); assert.equal(scrollTop, 0); assert.equal(focused, null);
  commandFrames.shift()();
  assert.deepEqual(frameOrder, ["dossier", "reveal"]);
  assert.equal(revealCalls.length, 1); assert.equal(revealCalls[0].behavior, "instant"); assert.equal(revealCalls[0].block, "center");
  assert.ok(approvalTop-scrollTop >= 0 && approvalTop-scrollTop <= viewportHeight);
  assert.equal(focused, "approval-button"); assert.equal(approvalButton.focusOptions.preventScroll, true); assert.equal(commandFrames.length, 0);
  checks.push("review shortcut reveals the offscreen approval after the dossier first-frame reset and focuses without a second scroll");
  afterSelect = null;
  for (const [label, change] of [
    ["locking", () => { state.authenticated = false; }],
    ["navigation", () => { state.view = "agents"; }],
    ["another selection", () => { state.selectedId = "other-mission"; }],
  ]) {
    state.authenticated = true; state.view = "missions"; state.selectedId = null;
    focused = null; const beforeReveals = revealCalls.length;
    await elements.get("command-review-open").handlers.click(); assert.equal(commandFrames.length, 1);
    change(); commandFrames.shift()();
    assert.equal(focused, null); assert.equal(revealCalls.length, beforeReveals); assert.equal(commandFrames.length, 0);
    checks.push(`deferred review focus is discarded after ${label}`);
  }
  state.authenticated = true; state.view = "missions"; state.selectedId = "review-fixture";
  let resolveRead;
  helpers.api = endpoint => { requests.push(endpoint); return new Promise(resolve => { resolveRead = resolve; }); };
  const pending = command.connected();
  state.authenticated = false; command.locked(); resolveRead(briefing); await pending;
  assert.equal(elements.get("command-state").textContent, "Console locked");
  assert.equal(elements.get("command-next-brief").textContent, "Not loaded");
  assert.equal(elements.get("command-refresh").disabled, true);
  assert.equal(elements.get("command-focus-open").disabled, true);
  assert.doesNotMatch(elements.get("command-focus-title").textContent, /literal-fixture|hostile|review-fixture/);
  mission = null;await elements.get("command-focus-open").handlers.click();assert.equal(mission,null);
  checks.push("locking invalidates an in-flight local briefing read and disables actions");
}
lifecycle().then(() => {
  const receipt = {created_at_utc: new Date().toISOString(), checks, passed: checks.length, failures: 0,
    source: "ariadne/static/command.js", sha256: crypto.createHash("sha256").update(source).digest("hex"),
    source_sha256: {"ariadne/static/command.js": crypto.createHash("sha256").update(source).digest("hex"),
      "ariadne/static/app.js": crypto.createHash("sha256").update(appSource).digest("hex"),
      "scripts/check_command_snapshot.cjs": crypto.createHash("sha256").update(fs.readFileSync(__filename)).digest("hex")},
    fixture_now_utc: new Date(fixtureNow).toISOString(),
    scope: "Pure snapshot, minimal DOM lifecycle, actual renderRun scrolling and actual app keyboard/menu listeners; no live browser layout, server operations, microphone or model execution."};
  if (receiptPath) {
    fs.mkdirSync(path.dirname(receiptPath), {recursive:true});
    fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2)+"\n");
  }
  console.log(JSON.stringify({passed: checks.length, failures: 0, ...(receiptPath ? {receipt:receiptPath} : {})}));
}).catch(error => { console.error(error.stack); process.exitCode = 1; });
