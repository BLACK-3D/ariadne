"use strict";

// This client has no external dependencies. All network requests are same-origin.
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const svgNS = "http://www.w3.org/2000/svg";
const iconPaths = {
  globe: ["M22 12a10 10 0 1 0-20 0 10 10 0 0 0 20 0Z", "M2 12h20M12 2c5 5 5 15 0 20-5-5-5-15 0-20Z"],
  plus: ["M12 5v14M5 12h14"],
  orbit: ["M20 12a8 8 0 1 1-8-8", "M21 3v6h-6", "M21 3l-9 9", "M8 12a4 4 0 0 0 4 4"],
  book: ["M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Z", "M12 5v15"],
  memory: ["M9 3h6v4h4v6h-4v4H9v-4H5V7h4V3Z", "M9 21h6M3 10H1M23 10h-2M12 1V0"],
  clock: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z", "M12 6v6l4 2"],
  folder: ["M3 6h6l2 2h10v12H3V6Z", "M3 6V4h6l2 2"],
  plug: ["M8 3v5M16 3v5M5 8h14M7 8v4a5 5 0 0 0 10 0V8M12 17v5"],
  settings: ["m12 3 2 3 3-.5.5 3 3 2-1.5 2.5 1.5 2.5-3 2-.5 3-3-.5-2 3-2-3-3 .5-.5-3-3-2L4 13.5 2.5 11l3-2 .5-3 3 .5 3-3.5Z", "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"],
  server: ["M4 3h16v7H4V3ZM4 14h16v7H4v-7ZM7 6.5h.01M7 17.5h.01M12 6.5h5M12 17.5h5"],
  refresh: ["M20 8a8 8 0 0 0-14-3L3 8M3 3v5h5M4 16a8 8 0 0 0 14 3l3-3M21 21v-5h-5"],
  pause: ["M8 5v14M16 5v14"],
  play: ["m8 4 12 8-12 8V4Z"],
  lock: ["M5 10h14v11H5V10ZM8 10V6a4 4 0 0 1 8 0v4M12 14v3"],
  menu: ["M4 6h16M4 12h16M4 18h16"],
  sparkles: ["m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4M18 4h4"],
  "arrow-up-right": ["M6 18 18 6M6 6h12v12"],
  "arrow-up": ["M12 20V4M5 11l7-7 7 7"],
  "arrow-down": ["M12 4v16M5 13l7 7 7-7"],
  "arrow-right": ["M4 12h16M13 5l7 7-7 7"],
  "arrow-left": ["M20 12H4M11 5l-7 7 7 7"],
  workflow: ["M3 3h6v6H3V3ZM15 15h6v6h-6v-6ZM6 9v9h9M9 6h9v9"],
  thread: ["M4 4h16v12H9l-5 5V4ZM8 8h8M8 12h5"],
  mic: ["M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"],
  shield: ["m12 2 8 3v6c0 5-4 9-8 11-4-2-8-6-8-11V5l8-3Z", "m8 12 3 3 5-6"],
  eye: ["M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z", "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"],
  file: ["M5 2h9l5 5v15H5V2ZM14 2v6h5M9 12h6M9 16h6"],
  copy: ["M9 9h12v12H9V9ZM15 5V3H3v12h2"],
  close: ["m6 6 12 12M18 6 6 18"],
  stop: ["M6 6h12v12H6V6Z"],
  power: ["M12 2v10M6 5a9 9 0 1 0 12 0"],
  trash: ["M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M10 10v7M14 10v7"],
  check: ["m5 12 4 4L19 6"],
  alert: ["m12 3 10 18H2L12 3ZM12 9v5M12 17h.01"],
  volume: ["M4 9h4l5-5v16l-5-5H4V9ZM17 8a6 6 0 0 1 0 8M20 5a10 10 0 0 1 0 14"],
  upload: ["M12 16V3M7 8l5-5 5 5M4 16v5h16v-5"],
  brand: ["M12 2v20M3 6l18 12M3 18 21 6"],
};
function icon(name) {
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const d of iconPaths[name] || iconPaths.file) {
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}
function hydrateIcons(root = document) {
  $$('[data-icon]', root).forEach(node => node.replaceChildren(icon(node.dataset.icon)));
}
function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined && text !== null) result.textContent = String(text);
  return result;
}
function iconButton(name, label, action) {
  const result = node("button", "icon-button");
  result.type = "button";
  result.title = label;
  result.setAttribute("aria-label", label);
  result.append(icon(name));
  if (action) result.addEventListener("click", action);
  return result;
}
function button(label, className, action, iconName) {
  const result = node("button", `button ${className}`, label);
  result.type = "button";
  if (iconName) result.prepend(icon(iconName));
  if (action) result.addEventListener("click", action);
  return result;
}
function clear(nodeToClear) { nodeToClear.replaceChildren(); }
function pretty(value) {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}
function humanize(value) { return String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, char => char.toUpperCase()); }
function asDate(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [year, month, day] = value.split("-").map(Number);
    const local = new Date(year, month - 1, day);
    return local.getFullYear() === year && local.getMonth() === month - 1 && local.getDate() === day ? local : null;
  }
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? null : date;
}
function dateLabel(value, includeTime = false) {
  const date = asDate(value);
  return date ? date.toLocaleString(undefined, includeTime ? {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"} : {month:"short", day:"numeric", year:"numeric"}) : "—";
}
function timeLabel(value) {
  const date = asDate(value);
  return date ? date.toLocaleTimeString(undefined, {hour:"2-digit", minute:"2-digit", second:"2-digit"}) : "";
}
function bytes(value) {
  if (typeof value !== "number" || value < 0) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}
function storageGet() { try { return sessionStorage.getItem("ariadne.access") || ""; } catch { return ""; } }
function storageSet(value) { try { value ? sessionStorage.setItem("ariadne.access", value) : sessionStorage.removeItem("ariadne.access"); } catch { /* Pairing still works without storage. */ } }
const state = {
  token: storageGet(), authenticated: false, pairingGeneration:0, status: null, runs: [], detail: null,
  commandAllMissions: false,
  selectedId: null, sessionId: null, view: "missions", creating: false,
  statusTimer: null, runTimer: null, runRequest: 0, renderKey: "", eventOpen: new Set(),
  editor: null, directory: ".", fileRequest: 0, recording: null, transcribing: false,
  speechUrl: null, speechButton: null, speechRequest: 0, speechPreferenceRequest: 0, speechPreferenceConfirmed: 0, speechPreferencesSaving: false, refreshingStatus: false,
  catalog: null, knowledgeTab: "documents", sources: [], sourceId: null,
  sourceDirectory: ".", sourceRequest: 0, sourcePreviewRequest: 0, preview: null,
  workflows: [], workflowToRun: null, workflowLaunching: false, uploading: false, uploadFile: null,
  workflowAgentFilter: "",
  workflowTab: "ai", procedures: [], procedureRuns: [], procedureRequest: 0, procedureRunsRequest: 0, procedureReview: null, procedureRunBusy: false, procedureUncertain: null,
  capabilityTab: "catalog", mcp: null, mcpBusy: false, mcpRequest: 0, mcpReview: null, previewPath: null,
  survival: null, survivalTab: "overview", survivalBusy: false, survivalRequest: 0, survivalLibraryRequest: 0, survivalEditor: null,
  survivalBooksRequest: 0, survivalBookPreview: null, survivalBookBusy: false, survivalReader: null, survivalReaderRequest: 0,
};
const activeStatuses = new Set(["queued", "running", "waiting_approval", "awaiting_approval", "pending", "waiting", "verifying"]);
const isActive = run => Boolean(run && activeStatuses.has(run.status));
const viewTitles = {missions:"Command center", operations:"Operations", workroom:"Project Workroom", "project-kits":"Project Kits", venture:"Venture Lab", handoff:"Service desk", agents:"Agents", briefing:"Daily brief", vision:"Awareness", perception:"Awareness", internet:"Internet", workflows:"Workflows", knowledge:"Knowledge", survival:"Survival", memory:"Memory", schedules:"Schedules", files:"Workspace", connectors:"Capabilities", settings:"Settings & setup"};
const executionLabel = run => run?.status === "completed" ? "Execution finished" : humanize(run?.status);

function toast(message, error = false) {
  const item = node("div", `toast${error ? " error" : ""}`);
  item.append(icon(error ? "alert" : "check"), node("span", "", message));
  $("#toast-region").append(item);
  setTimeout(() => item.remove(), error ? 8500 : 4500);
}
async function api(path, options = {}) {
  const requestToken = state.token, requestPairing = state.pairingGeneration;
  const headers = {Authorization:`Bearer ${requestToken}`, ...options.headers};
  let body = options.body;
  if (body !== undefined && !(body instanceof Blob) && !(body instanceof ArrayBuffer)) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`/api${path}`, {method:options.method || "GET", headers, body, cache:"no-store", credentials:"omit", signal:options.signal, keepalive:options.keepalive === true});
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("Cannot reach the local server. Check that Ariadne is running, then try again.");
  }
  if (!response.ok) {
    let message = `Server request failed (${response.status}).`;
    try { const data = await response.json(); message = data.error || data.detail || message; } catch { /* Use the status message. */ }
    const error = new Error(typeof message === "string" ? message : pretty(message));
    error.status = response.status;
    if ((response.status === 401 || response.status === 403) && !options.signal?.aborted && state.authenticated && state.token === requestToken && state.pairingGeneration === requestPairing) lockSession("Your access key was rejected. Pair this tab again.");
    throw error;
  }
  if (options.blob) return response.blob();
  if (response.status === 204) return {};
  return response.json();
}

function modelReady(status = state.status) {
  if (!status?.ollama?.available) return false;
  return (status.ollama.models || []).some(model => model.name === status.model || model.name === `${status.model}:latest`);
}
function updateComposer() {
  const working = state.selectedId && isActive(state.detail?.run);
  const disabled = !state.authenticated || !modelReady() || state.creating || working || state.transcribing;
  $("#send-mission").disabled = Boolean(disabled);
  $("#send-mission span").textContent = state.creating ? "Launching…" : working ? "Mission active" : "Launch mission";
  $("#send-mission").title = working ? "Wait for this mission or start a new mission to work independently" : !modelReady() ? "Configure an installed local model in Settings & setup" : "Send mission (Ctrl+Enter)";
  const voiceSupported = Boolean(window.isSecureContext && navigator.mediaDevices?.getUserMedia && window.MediaRecorder && (window.AudioContext || window.webkitAudioContext) && window.OfflineAudioContext);
  $("#voice-record").disabled = !state.authenticated || !state.status?.voice?.transcription || !voiceSupported || state.transcribing;
  $("#voice-record").title = state.transcribing ? "Transcribing locally…" : state.recording ? "Stop recording" : !voiceSupported ? "Microphone capture needs a supported browser on localhost or HTTPS" : !state.status?.voice?.transcription ? "Configure a local transcription model in server settings" : "Record voice input (up to 85 seconds)";
  $("#voice-record").setAttribute("aria-label", $("#voice-record").title);
  $("#resume-notice").hidden = !state.sessionId;
  $("#launch-workflow").disabled = !state.authenticated || !modelReady() || state.workflowLaunching;
  window.ariadneCommand?.render();
}
function renderStatus(status) {
  const ready = modelReady(status);
  const runtime = Boolean(status.ollama?.available);
  const badge = $("#connection-badge");
  badge.className = `connection-badge ${ready ? "ready" : "warning"}`;
  badge.replaceChildren(node("i"), document.createTextNode(ready ? "Local & ready" : runtime ? "Model needed" : "Runtime offline"));
  $("#local-indicator").className = `local-indicator ${ready ? "ready" : "error"}`;
  $("#runtime-label").textContent = ready ? "System online" : runtime ? "Model setup needed" : "Model runtime offline";
  $("#runtime-sublabel").textContent = ready ? status.model : "Open Settings & setup";
  $("#composer-model").textContent = status.model || "Local model";
  $("#pause-toggle").disabled = false;
  $("#pause-toggle").classList.toggle("paused", Boolean(status.paused));
  $("#pause-toggle").setAttribute("aria-pressed", String(Boolean(status.paused)));
  $("#pause-toggle [data-icon]").replaceChildren(icon(status.paused ? "play" : "pause"));
  $("#pause-label").textContent = status.paused ? "Resume autonomy" : "Pause autonomy";
  $("#pause-toggle").title = status.paused ? "Resume queued missions and autonomous steps" : "Pause before the next autonomous step";
  const banner = $("#global-banner");
  clear(banner);
  banner.hidden = ready && !status.paused;
  if (!banner.hidden) {
    banner.append(icon(status.paused ? "pause" : "alert"));
    banner.append(node("span", "", status.paused ? "Autonomy is paused. Queued missions wait, and active work stops before its next step." : runtime ? `Install or select the local model “${status.model}” to launch a mission.` : "The Ollama runtime is unavailable. Start it to connect your local intelligence."));
    const action = node("button", "", status.paused ? "Resume" : "Open setup");
    action.type = "button";
    action.addEventListener("click", () => status.paused ? togglePause() : showView("settings"));
    banner.append(action);
  }
  $("#workspace-path").textContent = `${status.workspace || "Server-managed workspace"}${state.directory === "." ? "" : ` / ${state.directory}`}`;
  $("#version-label").textContent = status.version ? `Version ${status.version} · Local operations console.` : "Local operations console.";
  renderSettings();
  renderWakeStatus(status.wake);
  if (status.internet) renderInternetStatus(status.internet);
  updateComposer();
  window.ariadneCommand?.status(status);
  window.ariadneRoutines?.status(status);
  window.ariadneOperationWorkflows?.status(status);
  window.ariadneVenture?.status(status);
  window.ariadneQQQ?.status(status);
  window.ariadneOptions?.status(status);
  window.ariadneOptionsReview?.status(status);
  window.ariadneRevenue?.status(status);
  window.ariadnePerception?.status(status);
  window.ariadneVision?.status(status);
  window.ariadneHandoff?.status(status);
  window.ariadneClientHandoff?.status(status);
}
async function refreshStatus({quiet = false} = {}) {
  if (!state.authenticated || state.refreshingStatus) return;
  const token = state.token, speechPreferenceConfirmed = state.speechPreferenceConfirmed, wakeSnapshot = wakeStatusRevision;
  state.refreshingStatus = true;
  try {
    const status = await api("/status");
    if (!state.authenticated || state.token !== token) return;
    // A status request begun before a confirmed preference write can carry the
    // earlier voice snapshot. Preserve only the newer, server-confirmed values.
    if (state.speechPreferenceConfirmed !== speechPreferenceConfirmed && state.status) {
      for (const key of ["speech_backend", "speech_voice", "speech_speed"]) if (Object.hasOwn(state.status, key)) status[key] = state.status[key];
    }
    // A confirmed mic action or lightweight wake read can finish before an
    // older full-status response. Keep its newer wake snapshot only.
    if (wakeStatusRevision !== wakeSnapshot && state.status) status.wake = state.status.wake;
    const previousActive = state.status?.active_runs;
    state.status = status;
    renderStatus(status);
    window.ariadneWorkroom?.status();
    window.ariadneProjectKits?.status();
    if (status.active_runs || previousActive !== status.active_runs) await loadRuns();
  } catch (error) {
    if (state.authenticated && state.token === token) {
      const badge = $("#connection-badge");
      badge.className = "connection-badge warning";
      badge.replaceChildren(node("i"), document.createTextNode("Server unreachable"));
      window.ariadneCommand?.connectionLost();
      if (!quiet) toast(error.message, true);
    }
  } finally { state.refreshingStatus = false; }
}
async function connect() {
  state.pairingGeneration += 1;
  resetWakeControls();
  const connectButton = $("#pairing-submit");
  connectButton.disabled = true;
  $("#pairing-error").hidden = true;
  try {
    const status = await api("/status");
    state.authenticated = true;
    state.status = status;
    storageSet(state.token);
    $("#pairing-overlay").hidden = true;
    $(".app-shell").inert = false;
    $("#access-token").value = "";
    renderStatus(status);
    loadAgents({quiet:true});
    await loadRuns();
    window.ariadneCommand?.connected();
    clearInterval(state.statusTimer);
    state.statusTimer = setInterval(() => refreshStatus({quiet:true}), 10000);
    if (state.view !== "missions") await loadView(state.view);
    $("#mission-prompt").focus();
  } catch (error) {
    state.authenticated = false;
    storageSet("");
    $("#pairing-overlay").hidden = false;
    $(".app-shell").inert = true;
    $("#pairing-error").textContent = error.status === 401 || error.status === 403 ? "That key was not accepted. Copy the access key from your server terminal." : error.message;
    $("#pairing-error").hidden = false;
    $("#access-token").focus();
  } finally { connectButton.disabled = false; }
}
function lockSession(message = "") {
  window.ariadneVision?.locking();
  state.navigationGeneration = (state.navigationGeneration || 0) + 1;
  internetGeneration += 1;
  state.pairingGeneration += 1;
  state.authenticated = false;
  state.token = "";
  resetWakeControls();
  storageSet("");
  clearInterval(state.statusTimer);
  clearTimeout(state.runTimer);
  state.runRequest += 1;
  if (state.recording) stopRecording(true);
  stopSpeech();
  clearBriefPoster(true);
  state.speechPreferenceRequest += 1;
  state.speechPreferenceConfirmed = 0;
  state.speechPreferencesSaving = false;
  if (state.artifactUrl) { URL.revokeObjectURL(state.artifactUrl); state.artifactUrl = null; }
  $("#pairing-overlay").hidden = false;
  $(".app-shell").inert = true;
  $("#pairing-error").textContent = message;
  $("#pairing-error").hidden = !message;
  if ($("#editor-dialog").open) $("#editor-dialog").close();
  if ($("#confirm-dialog").open) $("#confirm-dialog").close();
  if ($("#workflow-review-dialog").open) $("#workflow-review-dialog").close();
  state.mcpRequest += 1; state.mcpReview = null; state.preview = null; state.sourcePreviewRequest += 1;
  state.survivalRequest += 1; state.survivalLibraryRequest += 1; state.survivalBooksRequest += 1; state.survivalReaderRequest += 1; state.survivalEditor = null; state.survivalBookPreview = null; state.survivalReader = null;
  state.procedureRequest += 1; state.procedureRunsRequest += 1; state.procedureReview = null;
  for (const id of ["upload-dialog", "mcp-server-dialog", "mcp-review-dialog", "capability-setup-dialog", "local-apps-dialog", "survival-editor-dialog", "survival-reader-dialog", "procedure-review-dialog"]) if ($(`#${id}`).open) $(`#${id}`).close();
  updateComposer();
  setMenu(false);
  window.ariadneCommand?.locked();
  window.ariadneRoutines?.locked();
  window.ariadneOperationWorkflows?.locked();
  window.ariadneWorkroom?.locked();
  window.ariadneProjectKits?.locked();
  window.ariadneVenture?.locked();
  window.ariadneQQQ?.locked();
  window.ariadneOptions?.locked();
  window.ariadneOptionsReview?.locked();
  window.ariadneRevenue?.locked();
  window.ariadnePerception?.locked();
  window.ariadneVision?.locked();
  window.ariadneHandoff?.locked();
  window.ariadneClientHandoff?.locked();
  $("#access-token").focus();
}

let directoryReturn = null;
function setMenu(open) {
  const wasOpen = $("#sidebar").classList.contains("open");
  if (open && !state.authenticated) return;
  if (open && !wasOpen) directoryReturn = document.activeElement;
  $("#sidebar").classList.toggle("open", open);
  $("#sidebar").hidden = !open;
  $("#sidebar").setAttribute("aria-hidden", String(!open));
  $("#sidebar-scrim").hidden = !open;
  $("#sidebar").inert = !open;
  document.body.classList.toggle("directory-open", open);
  $("#menu-toggle").setAttribute("aria-expanded", String(open));
  $("#menu-toggle").setAttribute("aria-label", open ? "Close system directory" : "Open system directory");
  if (open) ($("#directory-close") || $("#new-mission")).focus();
  else if (wasOpen) (directoryReturn?.isConnected ? directoryReturn : $("#menu-toggle")).focus();
}
async function showView(view) {
  if (!viewTitles[view]) return;
  state.navigationGeneration = (state.navigationGeneration || 0) + 1;
  const changedView = state.view !== view;
  if (changedView && state.view === "handoff") window.ariadneClientHandoff?.leave();
  if (changedView && state.view === "workroom") window.ariadneWorkroom?.leave();
  if (changedView && state.view === "project-kits") window.ariadneProjectKits?.leave();
  if (changedView && state.view === "perception") window.ariadnePerception?.leave();
  if (changedView && state.view === "vision") window.ariadneVision?.leave();
  if (changedView && state.view === "venture") { window.ariadneRevenue?.detach(); window.ariadneOptionsReview?.detach(); }
  state.view = view;
  for (const item of $$(".view")) item.hidden = item.id !== `view-${view}`;
  for (const item of $$("[data-view]")) {
    item.classList.toggle("active", item.dataset.view === view);
    item.dataset.view === view ? item.setAttribute("aria-current", "page") : item.removeAttribute("aria-current");
  }
  $("#view-title").textContent = viewTitles[view];
  document.title = `${viewTitles[view]} · Ariadne`;
  setMenu(false);
  window.ariadneCommand?.render();
  if (changedView) $("#main-content").scrollTop = 0;
  if (state.authenticated) await loadView(view);
}
async function openLinkedView(view, action) {
  if (!state.authenticated) return;
  const token = state.token, generation = (state.navigationGeneration || 0) + 1;
  await showView(view);
  if (!state.authenticated || state.token !== token || state.view !== view || state.navigationGeneration !== generation) return;
  await action();
}
async function loadView(view) {
  if (view === "operations") { await window.ariadneOperations?.load(); return; }
  if (view === "workroom") { await window.ariadneWorkroom?.load(); return; }
  if (view === "project-kits") { await window.ariadneProjectKits?.load(); return; }
  if (view === "perception") { await window.ariadnePerception?.load(); return; }
  if (view === "vision") { await window.ariadneVision?.load(); return; }
  if (view === "venture") {
    await window.ariadneVenture?.load(); if (state.view !== view) return;
    await window.ariadneRevenue?.load(); if (state.view !== view) return;
    await window.ariadneQQQ?.load(); if (state.view !== view) return;
    await window.ariadneOptions?.load(); if (state.view !== view) return;
    await window.ariadneOptionsReview?.load(); return;
  }
  if (view === "handoff") { await loadServiceDesk(); return; }
  if (view === "settings") { renderSettings(); return; }
  const loaders = {agents:loadAgents, briefing:loadBriefing, internet:loadInternet, knowledge:loadKnowledge, survival:loadSurvival, workflows:loadWorkflows, memory:loadMemory, schedules:loadSchedules, files:loadFiles, connectors:loadConnectors};
  if (loaders[view]) await loaders[view]();
}
async function loadRuns() {
  if (!state.authenticated) return;
  try {
    const data = await api("/runs");
    state.runs = data.runs || [];
    renderRuns();
  } catch (error) { if (state.authenticated) toast(error.message, true); }
}
function renderRuns() {
  const container = $("#recent-runs");
  clear(container);
  const toggle = $("#command-log-toggle");
  if (toggle) {
    toggle.hidden = state.runs.length <= 4;
    toggle.textContent = state.commandAllMissions ? "Show recent four" : `Show all ${state.runs.length} missions`;
    toggle.setAttribute("aria-expanded", String(state.commandAllMissions));
    toggle.setAttribute("aria-controls", "recent-runs");
  }
  if (!state.runs.length) { container.append(node("p", "sidebar-empty", "No recorded missions. Give Ariadne a directive to begin.")); window.ariadneCommand?.render(); return; }
  for (const run of state.commandAllMissions ? state.runs : state.runs.slice(0, 4)) {
    const entry = node("button", `recent-run${run.id === state.selectedId ? " selected" : ""}`);
    entry.type = "button";
    entry.title = `${run.title || "Untitled mission"}\n${executionLabel(run)} · ${dateLabel(run.created_at, true)}${run.verification_status === "passed" ? " · Checks passed" : run.verification_status === "failed" ? " · Checks failed" : ""}`;
    const dot = node("span", `run-dot ${run.status || ""}`);
    const label = node("span", "run-title", run.title || "Untitled mission");
    const body = node("span", "recent-run-body");
    body.append(label, node("span", "recent-run-meta", `${executionLabel(run)} · ${dateLabel(run.created_at, true)}`));
    entry.append(dot, body, icon("arrow-up-right"));
    if (["passed", "failed"].includes(run.verification_status)) {
      const check = node("span", `recent-check-status ${run.verification_status}`);
      check.title = run.verification_status === "passed" ? "Expected output checks passed" : "Expected output checks failed";
      check.setAttribute("aria-label", check.title); check.append(icon(run.verification_status === "passed" ? "check" : "alert")); entry.append(check);
    }
    if (run.id === state.selectedId) entry.setAttribute("aria-current", "true");
    entry.addEventListener("click", () => selectRun(run.id));
    container.append(entry);
  }
  window.ariadneCommand?.render();
}
function newMission() {
  clearTimeout(state.runTimer);
  state.runRequest += 1;
  state.selectedId = null;
  state.sessionId = null;
  state.detail = null;
  state.renderKey = "";
  state.eventOpen.clear();
  $("#mission-heading").hidden = true;
  $("#welcome").hidden = false;
  $("#message-list").hidden = true;
  clear($("#message-list"));
  clear($("#approval-list"));
  clear($("#event-list"));
  $(".mission-layout").classList.remove("has-run");
  $("#activity-live").hidden = true;
  renderActivitySummary(null);
  renderRuns();
  updateComposer();
  showView("missions");
  $("#mission-prompt").focus({preventScroll:true});
  $("#main-content").scrollTop = 0;
}
async function selectRun(id) {
  clearTimeout(state.runTimer);
  state.selectedId = id;
  state.renderKey = "";
  state.detail = null;
  state.sessionId = null;
  state.eventOpen.clear();
  state.runRequest += 1;
  renderRuns();
  await showView("missions");
  $("#welcome").hidden = true;
  $("#message-list").hidden = false;
  $("#message-list").replaceChildren(node("div", "loading-state", "Opening mission…"));
  updateComposer();
  await refreshRun(id, state.runRequest);
}
async function refreshRun(id = state.selectedId, request = state.runRequest) {
  if (!id || !state.authenticated || id !== state.selectedId || request !== state.runRequest) return;
  clearTimeout(state.runTimer);
  try {
    const detail = await api(`/runs/${encodeURIComponent(id)}`);
    if (id !== state.selectedId || request !== state.runRequest || !state.authenticated) return;
    state.detail = detail;
    state.sessionId = detail.run?.session_id || null;
    const key = JSON.stringify(detail);
    if (key !== state.renderKey) {
      const wasActive = state.runs.find(run => run.id === id);
      state.runs = state.runs.map(run => run.id === id ? {...run, ...detail.run} : run);
      renderRun(detail, !state.renderKey);
      state.renderKey = key;
      renderRuns();
      if (wasActive && isActive(wasActive) && !isActive(detail.run)) refreshStatus({quiet:true});
    }
    updateComposer();
    if (isActive(detail.run)) state.runTimer = setTimeout(() => refreshRun(id, request), 1500);
  } catch (error) {
    if (request !== state.runRequest || !state.authenticated) return;
    toast(error.message, true);
    if (state.detail && isActive(state.detail.run)) state.runTimer = setTimeout(() => refreshRun(id, request), 5000);
    else if (!state.detail) $("#message-list").replaceChildren(node("p", "inline-error", error.message));
  }
}
function missionScroller() {
  // Follow the actual page scroll container. The command center uses one page
  // scroller, while an older/local stylesheet may retain a nested container.
  for (let item = $("#conversation"); item && item !== document.body; item = item.parentElement) {
    if (/^(auto|scroll)$/.test(getComputedStyle(item).overflowY)) return item;
  }
  return document.scrollingElement || document.documentElement;
}
function renderRun(detail, firstRender) {
  const run = detail.run;
  if (!run) return;
  const scroller = missionScroller();
  const scrollTopBefore = scroller.scrollTop;
  const viewportTop = scroller === document.scrollingElement ? 0 : scroller.getBoundingClientRect().top + scroller.clientTop;
  const priorOutput = $("#message-list").getBoundingClientRect();
  const followOutput = !firstRender && priorOutput.bottom >= viewportTop
    && priorOutput.bottom <= viewportTop + scroller.clientHeight + 140;
  if (firstRender && $("#execution-trail")) $("#execution-trail").open = isActive(run);
  $(".mission-layout").classList.add("has-run");
  $("#mission-heading").hidden = false;
  $("#mission-title").textContent = run.title || "Untitled mission";
  $("#mission-status").textContent = `${executionLabel(run).toUpperCase()} · ${String(run.id).slice(0, 8)}`;
  $("#cancel-run").hidden = !isActive(run);
  $("#welcome").hidden = true;
  const list = $("#message-list");
  list.hidden = false;
  clear(list);
  const messages = (detail.messages || []).filter(message => ["user", "assistant"].includes(message.role) && typeof message.content === "string" && message.content.trim());
  for (const message of messages) {
    const card = node("article", `message ${message.role}`);
    const header = node("div", "message-header");
    const avatar = node("span", "message-avatar");
    avatar.append(message.role === "assistant" ? icon("brand") : icon("arrow-up-right"));
    header.append(avatar, node("span", "", message.role === "assistant" ? "Ariadne output" : "Directive"));
    // Model text is deliberately rendered as text, never executable HTML or Markdown.
    card.append(header, node("div", "message-content", message.content));
    if (message.role === "assistant") {
      const actions = node("div", "message-actions");
      actions.append(iconButton("copy", "Copy response", () => copyText(message.content)));
      if (state.status?.voice?.speech) {
        const speakButton = iconButton("volume", message.content.length > 2000 ? "Read the first 2,000 characters aloud locally" : "Read aloud locally");
        speakButton.addEventListener("click", () => speak(message.content, speakButton));
        actions.append(speakButton);
      }
      card.append(actions);
    }
    list.append(card);
  }
  if (isActive(run)) {
    const pending = (detail.approvals || []).some(approval => approval.status === "pending");
    if (pending) {
      const notice = node("div", "run-notice", "Your approval is needed. Review the exact action in Mission activity to continue. ");
      const review = node("button", "text-button", "Review action");
      review.type = "button";
      review.addEventListener("click", () => {
        $("#approval-list").scrollIntoView({behavior:window.matchMedia("(prefers-reduced-motion:reduce)").matches ? "instant" : "smooth", block:"center"});
        $("#approval-list button")?.focus({preventScroll:true});
      });
      notice.append(review);
      list.append(notice);
    }
    else {
      const thinking = node("div", "thinking-row");
      const dots = node("span", "thinking-dots");
      dots.append(node("i"), node("i"), node("i"));
      thinking.append(dots, node("span", "", run.status === "verifying" ? "Checking the expected outputs…" : run.status === "queued" ? state.status?.paused ? "Queued · autonomy is paused" : "Queued for local execution…" : "Ariadne is working…"));
      list.append(thinking);
    }
  } else if (run.error) list.append(node("div", "run-error", run.error));
  else if (run.status === "cancelled") list.append(node("div", "run-notice", "Mission cancelled. Actions already completed remain recorded in the activity trail."));
  else if (!messages.some(message => message.role === "assistant")) list.append(node("div", "mission-empty-message", "Review the mission activity for its results."));
  renderVerification(detail, list);
  renderActivitySummary(run);
  renderApprovals(detail.approvals || []);
  renderEvents(detail.events || []);
  $("#activity-live").hidden = !isActive(run);
  if (firstRender || followOutput) requestAnimationFrame(() => {
    if (state.selectedId !== run.id || state.view !== "missions" || !state.authenticated) return;
    if (firstRender) { scroller.scrollTop = 0; return; }
    // Follow the result's end, not the execution trail or ledger further down
    // the page. A user scroll after this render began takes precedence.
    if (Math.abs(scroller.scrollTop - scrollTopBefore) > 2) return;
    const top = scroller === document.scrollingElement ? 0 : scroller.getBoundingClientRect().top + scroller.clientTop;
    const outputEnd = $("#message-list").getBoundingClientRect().bottom - top + scroller.scrollTop;
    scroller.scrollTop = Math.max(0, outputEnd - scroller.clientHeight + 24);
  });
}
function renderActivitySummary(run) {
  const summary = $("#activity-summary");
  summary.className = `activity-summary${run ? " compact" : ""}`;
  clear(summary);
  if (!run) {
    const symbol = node("span", "activity-orbit");
    symbol.append(icon("orbit"));
    summary.append(symbol, node("h2", "", "Execution, in view."), node("p", "", "Tool calls, results, and approval requests appear here as Ariadne works."));
    return;
  }
  summary.append(node("h2", "", executionLabel(run)), node("p", "", `Started ${dateLabel(run.created_at, true)}`));
  const steps = node("div", "run-stat-row");
  steps.append(node("span", "", "Model steps"), node("strong", "", `${run.step_count || 0} / ${state.status?.max_steps || "—"}`));
  summary.append(steps);
}
function renderApprovals(approvals) {
  const container = $("#approval-list");
  clear(container);
  for (const approval of approvals.filter(item => item.status === "pending")) {
    const card = node("article", "approval-card");
    const label = node("div", "approval-label");
    label.append(icon("shield"), node("span", "", "YOUR APPROVAL NEEDED"));
    card.append(label, node("h3", "", approval.tool || "Tool action"), node("p", "", "Review the target, scope, and complete arguments. Approval allows this specific action to execute once."));
    if (approval.capability && typeof approval.capability === "object" && !Array.isArray(approval.capability) && Object.keys(approval.capability).length) {
      const capability = approval.capability;
      const snapshot = node("div", "approval-capability");
      snapshot.append(node("h4", "", "Action target & scope"));
      if (capability.method || capability.destination) snapshot.append(node("p", "approval-destination", `${capability.method || ""} ${capability.destination || ""}`.trim()));
      const metadata = Object.fromEntries(Object.entries(capability).filter(([key]) => key !== "script_preview"));
      snapshot.append(node("pre", "", pretty(metadata)));
      if (typeof capability.script_preview === "string") {
        snapshot.append(node("h4", "", "Script preview"), node("pre", "approval-script", capability.script_preview));
        if (capability.preview_truncated) snapshot.append(node("p", "approval-preview-warning", "This script preview is truncated. Inspect the complete script before approving."));
        if (typeof approval.args?.path === "string") snapshot.append(button("Inspect script file", "button-secondary", () => openArtifact(approval.args.path), "file"));
      }
      if (approval.capability_fingerprint) { const fingerprint = node("details", "tool-details"); fingerprint.append(node("summary", "", "Approval fingerprint"), node("pre", "", approval.capability_fingerprint)); snapshot.append(fingerprint); }
      card.append(snapshot);
    }
    card.append(node("h4", "approval-args-label", "Arguments"), node("pre", "", pretty(approval.args || {})));
    const actions = node("div", "approval-actions");
    const decide = async decision => {
      for (const item of $$("button", actions)) item.disabled = true;
      try {
        await api(`/approvals/${encodeURIComponent(approval.id)}`, {method:"POST", body:{decision}});
        toast(decision === "approve" ? "Action approved." : "Action rejected.");
        await refreshRun();
      } catch (error) { toast(error.message, true); for (const item of $$("button", actions)) item.disabled = false; }
    };
    actions.append(button("Reject", "button-secondary", () => decide("reject")), button("Approve once", "button-primary", () => decide("approve"), "check"));
    card.append(actions);
    container.append(card);
  }
}
function eventDescription(event) {
  const data = event.data || {};
  const labels = {
    queued:"Mission queued", thinking:`Thinking · step ${data.step || ""}`, tool_started:data.tool ? `Using ${data.tool}` : "Tool started",
    tool_result:data.tool ? `${data.tool} returned` : "Tool result", approval_required:"Approval requested", completed:"Execution finished",
    verification:"Output verification", verification_started:"Checking expected outputs", verification_completed:"Output checks finished",
    assistant:"Progress update", error:"Mission stopped", budget_reached:"Step budget reached", schedule:"Scheduled mission", model_usage:"Local model usage",
  };
  return labels[event.kind] || humanize(event.kind);
}
function renderEvents(events) {
  const container = $("#event-list");
  if ($("#execution-trail-summary")) $("#execution-trail-summary").textContent = `Execution trail · ${events.length} events`;
  const scrollParent = $(".activity-panel");
  const scrollDown = scrollParent.scrollHeight - scrollParent.scrollTop - scrollParent.clientHeight < 100;
  clear(container);
  // Usage is available in the raw event details, without presenting token counts as business outcomes.
  for (const event of events) {
    const data = event.data || {};
    const item = node("article", `event${event.kind === "error" ? " error" : event.kind === "approval_required" ? " waiting" : ""}`);
    item.append(node("h3", "", eventDescription(event)));
    const time = node("time", "", timeLabel(event.created_at));
    const date = asDate(event.created_at);
    if (date) time.dateTime = date.toISOString();
    item.append(time);
    const text = typeof data.message === "string" ? data.message : event.kind === "tool_result" && data.result?.error ? data.result.error : "";
    if (text && event.kind !== "completed" && event.kind !== "assistant") item.append(node("p", "", text));
    const details = node("details");
    const eventId = String(event.id);
    details.open = state.eventOpen.has(eventId);
    details.append(node("summary", "", "Inspect event"), node("pre", "", pretty(data)));
    details.addEventListener("toggle", () => { details.open ? state.eventOpen.add(eventId) : state.eventOpen.delete(eventId); });
    item.append(details);
    container.append(item);
  }
  if (scrollDown) requestAnimationFrame(() => { scrollParent.scrollTop = scrollParent.scrollHeight; });
}
async function launchMission(event) {
  event.preventDefault();
  const prompt = $("#mission-prompt").value.trim();
  if (!prompt || $("#send-mission").disabled) return;
  let checks;
  try { checks = parseChecks($("#mission-checks").value); }
  catch (error) {
    $("#mission-expectations").open = true;
    $("#mission-checks-error").textContent = error.message;
    $("#mission-checks-error").hidden = false;
    $("#mission-checks").focus();
    return;
  }
  state.creating = true;
  updateComposer();
  try {
    const body = {prompt, checks, agent_id:$("#mission-agent").value};
    if (state.sessionId) body.session_id = state.sessionId;
    const data = await api("/runs", {method:"POST", body});
    $("#mission-prompt").value = "";
    $("#mission-checks").value = "[]";
    $("#mission-expectations").open = false;
    updateCheckCount();
    resizeComposer();
    await loadRuns();
    await selectRun(data.run.id);
    refreshStatus({quiet:true});
  } catch (error) { toast(error.message, true); }
  finally { state.creating = false; updateComposer(); }
}
async function cancelRun() {
  if (!state.selectedId) return;
  const confirmed = await confirmAction("Cancel this mission?", "Ariadne will stop before the next step. Actions already completed are preserved in the activity trail.", "Cancel mission", "Keep working");
  if (!confirmed) return;
  const cancelButton = $("#cancel-run");
  cancelButton.disabled = true;
  try {
    await api(`/runs/${encodeURIComponent(state.selectedId)}/cancel`, {method:"POST", body:{}});
    await refreshRun();
    await loadRuns();
    toast(state.detail?.run?.status === "cancelled" ? "Mission cancelled." : `Mission is now ${executionLabel(state.detail?.run).toLowerCase()}. Review its recorded result.`);
  } catch (error) { toast(error.message, true); }
  finally { cancelButton.disabled = false; }
}
async function togglePause() {
  if (!state.status) return;
  const control = $("#pause-toggle");
  control.disabled = true;
  try {
    const data = await api("/control", {method:"POST", body:{paused:!state.status.paused}});
    state.status.paused = data.paused;
    renderStatus(state.status);
    toast(data.paused ? "Autonomy paused. The current step may finish." : "Autonomy resumed.");
  } catch (error) { toast(error.message, true); }
  finally { control.disabled = !state.authenticated; }
}

function emptyState(iconName, title, description, action) {
  const container = node("div", "empty-state");
  const symbol = node("span");
  symbol.append(icon(iconName));
  // Keep icon sizing consistent with static empty states.
  symbol.dataset.icon = iconName;
  container.append(symbol, node("h2", "", title), node("p", "", description));
  if (action) container.append(button(action.label, "button-secondary", action.fn, "plus"));
  return container;
}
async function resourceLoad(container, loader, render) {
  container.replaceChildren(node("div", "loading-state", "Loading from your local server…"));
  try { const data = await loader(); render(data); }
  catch (error) { container.replaceChildren(node("p", "inline-error", error.message)); }
}
async function loadMemory() {
  const container = $("#memory-list");
  await resourceLoad(container, () => api("/memory"), data => {
    clear(container);
    const memories = data.memories || [];
    if (!memories.length) { container.append(emptyState("memory", "A little context goes a long way.", "Add a preference, a useful fact, or a principle you want Ariadne to remember.", {label:"Add your first memory", fn:() => openEditor("memory")})); return; }
    for (const memory of memories) {
      const card = node("article", "resource-card");
      const header = node("div", "resource-card-header");
      const symbol = node("div", "card-icon"); symbol.append(icon("memory"));
      header.append(symbol, iconButton("trash", "Remove memory", () => removeResource("memory", memory.id, "Remove this memory?", memory.content)));
      card.append(header, node("p", "", memory.content));
      card.append(node("div", "resource-card-footer", `Saved ${dateLabel(memory.created_at)}`));
      container.append(card);
    }
  });
}
async function loadKnowledge() {
  syncKnowledgeTab();
  const jobs = [loadKnowledgeDocuments(), fetchCatalog().catch(() => null)];
  if (state.knowledgeTab === "sources") jobs.push(loadSources());
  await Promise.allSettled(jobs);
}
async function loadKnowledgeDocuments() {
  const container = $("#knowledge-list");
  await resourceLoad(container, () => api("/knowledge"), data => {
    clear(container);
    const documents = data.documents || [];
    $("#knowledge-count").textContent = documents.length ? String(documents.length) : "";
    if (!documents.length) { container.append(emptyState("book", "Give your intelligence some context.", "Save notes, reference documents, or instructions as searchable local knowledge.", {label:"Add your first document", fn:() => openEditor("knowledge")})); return; }
    for (const doc of documents) {
      const card = node("article", "resource-card");
      const header = node("div", "resource-card-header");
      const symbol = node("div", "card-icon"); symbol.append(icon("book"));
      header.append(symbol, iconButton("trash", "Remove knowledge document", () => removeResource("knowledge", doc.id, "Remove this document?", `“${doc.title}” and its indexed content will be removed from local knowledge.`)));
      card.append(header, node("h2", "", doc.title), node("p", "", doc.processor?.name === "rapidocr" ? "Recognized with local OCR and indexed for search. Review text and numbers before relying on them." : "Indexed for local knowledge search."));
      if ((Array.isArray(doc.warnings) && doc.warnings.length) || doc.partial) {
        const warning = node("div", "document-warning"); warning.append(icon("alert"));
        const notes = node("div"); notes.append(node("strong", "", "Extraction notes"));
        for (const note of Array.isArray(doc.warnings) ? doc.warnings : []) notes.append(node("p", "", typeof note === "string" ? note : pretty(note)));
        if (doc.partial && !doc.warnings?.length) notes.append(node("p", "", "This reference contains a partial extraction."));
        warning.append(notes); card.append(warning);
      }
      if (doc.source_name || doc.source_path || doc.format || doc.sha256) {
        const origin = node("details", "tool-details document-provenance");
        origin.append(node("summary", "", "Source & provenance"));
        const data = {};
        for (const key of ["source_name", "source_path", "format", "version", "sha256", "content_sha256", "byte_size", "processor"]) if (doc[key] !== undefined && doc[key] !== null) data[key] = doc[key];
        origin.append(node("pre", "", pretty(data)));
        card.append(origin);
      }
      card.append(node("div", "resource-card-footer", doc.version ? `Version ${doc.version} · ${dateLabel(doc.updated_at || doc.created_at)}` : `Added ${dateLabel(doc.created_at)}`));
      container.append(card);
    }
  });
}
function intervalLabel(minutes) {
  if (minutes % 1440 === 0) return `Every ${minutes / 1440} ${minutes === 1440 ? "day" : "days"}`;
  if (minutes % 60 === 0) return `Every ${minutes / 60} ${minutes === 60 ? "hour" : "hours"}`;
  return `Every ${minutes} minutes`;
}
async function loadSchedules() {
  const container = $("#schedule-list");
  await resourceLoad(container, () => api("/schedules"), data => {
    clear(container);
    const schedules = data.schedules || [];
    if (!schedules.length) { container.append(emptyState("clock", "A steady rhythm, on your terms.", "Set a mission to repeat while your local server is running.", {label:"Create a schedule", fn:() => openEditor("schedule")})); return; }
    for (const schedule of schedules) {
      const card = node("article", "schedule-card");
      const main = node("div", "schedule-main");
      const symbol = node("span", "schedule-icon"); symbol.append(icon("clock"));
      const body = node("div", "schedule-body");
      body.append(node("h2", "", schedule.name), node("p", "", schedule.prompt));
      if (schedule.workflow_id) body.append(node("p", "schedule-workflow-snapshot", `Saved workflow snapshot · ${workflowAgentLabel(schedule.agent_id)} · ${(schedule.checks || []).length} output checks · ${schedule.isolated_outputs === true ? "new checked-output folder per run" : "fixed checked-output paths"}. Later workflow edits do not change these instructions.`));
      if (schedule.last_error) body.append(node("p", "schedule-last-error", `Schedule held · ${schedule.last_error}`));
      const meta = node("div", "schedule-meta");
      const interval = node("span"); interval.append(icon("clock"), document.createTextNode(intervalLabel(schedule.interval_minutes)));
      meta.append(interval, node("span", "", schedule.enabled ? `Next · ${dateLabel(schedule.next_run, true)}` : "Schedule disabled"));
      body.append(meta); main.append(symbol, body);
      const actions = node("div", "schedule-actions");
      const toggle = node("button", "toggle");
      toggle.type = "button"; toggle.setAttribute("role", "switch"); toggle.setAttribute("aria-checked", String(Boolean(schedule.enabled)));
      toggle.setAttribute("aria-label", `${schedule.enabled ? "Disable" : "Enable"} ${schedule.name}`);
      toggle.title = `${schedule.enabled ? "Disable" : "Enable"} schedule`;
      toggle.addEventListener("click", async () => {
        toggle.disabled = true;
        try { await api(`/schedules/${encodeURIComponent(schedule.id)}`, {method:"POST", body:{enabled:!schedule.enabled}}); await loadSchedules(); }
        catch (error) { toast(error.message, true); toggle.disabled = false; }
      });
      actions.append(toggle, iconButton("trash", "Remove schedule", () => removeResource("schedules", schedule.id, "Remove this schedule?", `“${schedule.name}” will stop creating recurring missions. Existing missions will remain.`)));
      if (schedule.last_run_id) actions.append(button("Last mission", "button-secondary", () => selectRun(schedule.last_run_id), "eye"));
      card.append(main, actions); container.append(card);
    }
  });
}
async function removeResource(type, id, title, description) {
  if (!await confirmAction(title, String(description).slice(0, 1000))) return;
  try {
    await api(`/${type}/${encodeURIComponent(id)}`, {method:"DELETE"});
    toast("Item removed.");
    await loadView(state.view);
  } catch (error) { toast(error.message, true); }
}
function confirmAction(title, description, actionLabel = "Remove", cancelLabel = "Keep it") {
  const dialog = $("#confirm-dialog");
  if (dialog.open) return Promise.resolve(false);
  $("#confirm-title").textContent = title;
  $("#confirm-description").textContent = description;
  $("#confirm-action").textContent = actionLabel;
  $('#confirm-dialog button[value="cancel"].button').textContent = cancelLabel;
  dialog.returnValue = "cancel";
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once:true});
    dialog.showModal();
  });
}
const editorDefinitions = {
  source:{title:"Add a read-only source folder", eyebrow:"LOCAL REFERENCE ACCESS", endpoint:"sources", fields:[{name:"name",label:"Folder name",max:120,placeholder:"For example: Business references"},{name:"path",label:"Absolute folder path on this server",max:1000,placeholder:"C:\\ReferenceLibrary",help:"This grants read-only browsing of this folder. Selected documents can be imported into Knowledge. Drive E: and linked paths are excluded."}]},
  workflow:{title:"Create a workflow",eyebrow:"REUSABLE INSTRUCTIONS, CHECKABLE OUTPUTS",endpoint:"workflows",fields:[{name:"name",label:"Workflow name",max:120,placeholder:"For example: Prepare a weekly operations brief"},{name:"prompt",label:"Mission instructions",textarea:true,max:16000,placeholder:"Describe the task, references, output paths, and boundaries…"},{name:"checks",label:"Expected output checks (JSON)",textarea:true,code:true,max:50000,value:"[]",help:"Up to 10 workspace checks. Use the templates below, then edit their paths, text, or column names. Use quoted decimal strings for exact JSON number checks; unquoted values must be safe integers. An empty array means no automatic outcome checks."}]},
  workflowSchedule:{title:"Schedule a saved workflow",eyebrow:"FROZEN WORKFLOW / RECURRING LOCAL WORK",endpoint:"schedules",fields:[{name:"name",label:"Schedule name",max:120},{name:"interval_minutes",label:"Repeat every (minutes)",type:"number",min:5,max:525600,value:1440,help:"Saving captures this workflow's current instructions, output checks and resolved agent. Later workflow edits do not change this schedule. Missed intervals are not replayed; ordinary permissions and approval gates remain active."}]},
  memory:{title:"Add a memory", eyebrow:"DURABLE CONTEXT", endpoint:"memory", fields:[{name:"content", label:"What should Ariadne remember?", textarea:true, max:4000, placeholder:"For example: Prefer simple local tools and low recurring operating costs.", help:"Keep it useful and specific. Maximum 4,000 characters."}]},
  knowledge:{title:"Add local knowledge", eyebrow:"YOUR REFERENCE LIBRARY", endpoint:"knowledge", fields:[{name:"title", label:"Document title", max:200, placeholder:"For example: Our business operating principles"}, {name:"content", label:"Reference text", textarea:true, max:500000, placeholder:"Paste the content you want to make searchable…", help:"Plain text is indexed and stored on this server. Maximum 500,000 characters."}]},
  schedule:{title:"Create a schedule", eyebrow:"RECURRING LOCAL WORK", endpoint:"schedules", fields:[{name:"name", label:"Schedule name", max:120, placeholder:"For example: Daily workspace review"}, {name:"prompt", label:"Mission instructions", textarea:true, max:16000, placeholder:"Describe the work, its expected output, and any limits…", help:"The same tool permissions and approval policies apply."}, {name:"interval_minutes", label:"Repeat every (minutes)", type:"number", min:5, max:525600, value:1440, help:"Minimum 5 minutes. Runs while the server is powered on; missed intervals are not replayed."}]},
};
editorDefinitions.workflow.fields.splice(1, 0, {name:"agent_id", label:"Assigned agent", type:"agent", value:"general", help:"Choose a specialist or automatic routing. Saved workflow missions keep the existing approval and step limits."});
editorDefinitions.workflow.fields.push({name:"isolated_outputs", label:"New checked-output folder on each run", type:"checkbox", value:false, help:"Use the literal {{run_dir}} in the instructions and at the start of every expected output path, for example {{run_dir}}/report.md. The server expands it to a new missions/<run ID> folder when queued. This isolates checked-output locations only; it is not an OS sandbox or a restriction on all file writes."});
function populateWorkflowAgent(select, chosen = "general") {
  select.replaceChildren();
  const auto = node("option", "", "Auto · route this workflow"); auto.value = "auto"; select.append(auto);
  const entries = agentCatalog.length ? agentCatalog : [{id:"general",name:"Ariadne coordinator"}];
  for (const agent of entries) { const option = node("option", "", agent.name); option.value = agent.id; select.append(option); }
  if (![...select.options].some(option => option.value === chosen)) {
    const missing = node("option", "", `${chosen} · unavailable`); missing.value = chosen; missing.disabled = true; select.append(missing);
  }
  select.value = chosen;
}
function workflowAgentLabel(identifier) {
  return identifier === "auto" ? "Auto · resolved when queued" : agentCatalog.find(agent => agent.id === (identifier || "general"))?.name || humanize(identifier || "general");
}
function scheduleWorkflow(workflow) {
  openEditor("workflowSchedule", {extra:{workflow_id:workflow.id}, values:{name:`${workflow.name} schedule`.slice(0,120)}});
}
function openEditor(type, options = {}) {
  const definition = editorDefinitions[type];
  if (!definition) return;
  state.editor = {...definition, editId:options.editId || null, extra:options.extra || {}};
  $("#editor-title").textContent = options.editId ? "Edit workflow" : definition.title;
  $("#editor-eyebrow").textContent = definition.eyebrow;
  $("#editor-error").hidden = true;
  $("#save-editor").disabled = false;
  $("#save-editor").textContent = "Save";
  $("#editor-dialog").classList.toggle("workflow-editor", type === "workflow");
  const fields = $("#editor-fields"); clear(fields);
  for (const field of definition.fields) {
    const wrapper = node("div", "editor-field");
    const label = node("label", "", field.label); label.htmlFor = `editor-input-${field.name}`;
    const input = node(field.type === "agent" ? "select" : field.textarea ? "textarea" : "input");
    input.id = `editor-input-${field.name}`; input.name = field.name; input.required = field.type !== "checkbox";
    if (field.placeholder) input.placeholder = field.placeholder;
    if (field.textarea || !field.type) input.maxLength = field.max;
    if (field.type === "agent") populateWorkflowAgent(input, options.values?.[field.name] || field.value || "general");
    else if (field.type === "checkbox") input.type = "checkbox";
    else if (field.type) { input.type = field.type; input.min = String(field.min); input.max = String(field.max); input.step = "1"; }
    if (field.type === "checkbox") input.checked = (options.values?.[field.name] ?? field.value) === true;
    else {
      if (field.value !== undefined) input.value = String(field.value);
      if (options.values?.[field.name] !== undefined) input.value = String(options.values[field.name]);
    }
    if (field.code) { input.classList.add("code-input"); input.spellcheck = false; }
    if (field.type === "checkbox") { wrapper.classList.add("editor-checkbox-field"); wrapper.append(input, label); }
    else wrapper.append(label, input);
    if (field.help) { const help = node("p", "field-help", field.help); help.id = `help-${field.name}`; input.setAttribute("aria-describedby", help.id); wrapper.append(help); }
    if (field.name === "checks") wrapper.append(checkTemplateControls(input));
    fields.append(wrapper);
  }
  $("#editor-dialog").showModal();
  $("input, textarea, select", fields)?.focus();
}
async function saveEditor(event) {
  event.preventDefault();
  if (!state.editor) return;
  const save = $("#save-editor"); save.disabled = true;
  $("#editor-error").hidden = true;
  const definition = state.editor;
  const body = {...(definition.extra || {})};
  for (const field of definition.fields) {
    if (field.type === "checkbox") { body[field.name] = $(`#editor-input-${field.name}`).checked; continue; }
    const value = $(`#editor-input-${field.name}`).value.trim();
    body[field.name] = field.type === "number" ? Number(value) : value;
    if (!value) { $("#editor-error").textContent = "Fill in all fields with nonempty content."; $("#editor-error").hidden = false; save.disabled = false; return; }
  }
  try {
    if (definition.endpoint === "workflows") body.checks = parseChecks(body.checks);
    const result = await api(`/${definition.endpoint}${definition.editId ? `/${encodeURIComponent(definition.editId)}` : ""}`, {method:definition.editId ? "PUT" : "POST", body});
    $("#editor-dialog").close();
    if (definition.endpoint === "sources") { state.sourceId = result.source?.id || result.id || null; state.sourceDirectory = "."; state.knowledgeTab = "sources"; }
    const messages = {schedules:"Schedule created.", knowledge:"Local knowledge added.", memory:"Memory saved.", sources:"Read-only source folder added.", workflows:"Workflow saved."};
    toast(messages[definition.endpoint] || "Saved.");
    await loadView(state.view);
  } catch (error) { $("#editor-error").textContent = error.message; $("#editor-error").hidden = false; }
  finally { save.disabled = false; }
}

async function loadFiles() {
  const container = $("#file-list");
  const directory = state.directory;
  const request = ++state.fileRequest;
  container.replaceChildren(node("div", "loading-state", "Reading workspace…"));
  try {
    const data = await api(`/files?directory=${encodeURIComponent(directory)}`);
    if (request !== state.fileRequest) return;
    clear(container);
    $("#workspace-path").textContent = `${state.status?.workspace || "Workspace"}${directory === "." ? "" : ` / ${directory}`}`;
    if (directory !== ".") {
      const back = node("button", "file-item"); back.type = "button";
      back.append(icon("arrow-left"), node("span", "", "Parent folder"));
      back.addEventListener("click", () => { const parts = directory.split("/").filter(Boolean); parts.pop(); state.directory = parts.join("/") || "."; loadFiles(); });
      container.append(back);
    }
    const files = [...(data.files || [])].sort((a, b) => (b.type === "directory") - (a.type === "directory") || String(a.path || a.name).localeCompare(String(b.path || b.name)));
    if (!files.length) container.append(emptyState("folder", "A fresh workspace.", "Files created by your missions will appear here."));
    for (const file of files) {
      const path = file.path || file.name;
      const item = node("button", "file-item"); item.type = "button"; item.title = path;
      const label = node("span");
      label.append(node("strong", "", file.name || path), node("small", "", file.type === "directory" ? "Folder" : bytes(file.size)));
      item.append(icon(file.type === "directory" ? "folder" : "file"), label);
      item.addEventListener("click", () => {
        if (file.type === "directory") { state.directory = path.replaceAll("\\", "/"); loadFiles(); }
        else { $$(".file-item", container).forEach(entry => entry.classList.remove("active")); item.classList.add("active"); readFile(path); }
      });
      container.append(item);
    }
    if (data.truncated) container.append(node("p", "inline-error", "This directory listing was limited by the server. Open a subfolder to see more."));
  } catch (error) { if (request === state.fileRequest) container.replaceChildren(node("p", "inline-error", error.message)); }
}
async function readFile(path) {
  const preview = $("#file-preview");
  const request = (state.previewRequest || 0) + 1; state.previewRequest = request;
  preview.replaceChildren(node("div", "loading-state", "Reading file…"));
  if (state.artifactUrl) { URL.revokeObjectURL(state.artifactUrl); state.artifactUrl = null; }
  try {
    const extension = path.split(".").pop().toLowerCase();
    if (["png", "jpg", "jpeg", "wav", "mp4", "docx", "xlsx", "pptx", "pdf", "step", "stp", "stl", "zip"].includes(extension)) {
      const header = node("div", "file-preview-header");
      header.append(node("span", "", path), button("Download", "button-secondary", () => downloadArtifact(path), "arrow-up-right"));
      preview.replaceChildren(header);
      if (["png", "jpg", "jpeg", "wav", "mp4"].includes(extension)) {
        const raw = await api(`/artifacts?path=${encodeURIComponent(path)}`, {blob:true});
        if (request !== state.previewRequest) return;
        const mime = {png:"image/png", jpg:"image/jpeg", jpeg:"image/jpeg", wav:"audio/wav", mp4:"video/mp4"}[extension];
        state.artifactUrl = URL.createObjectURL(new Blob([raw], {type:mime}));
        const media = node(["wav", "mp4"].includes(extension) ? extension === "wav" ? "audio" : "video" : "img", "artifact-preview-media");
        if (["wav", "mp4"].includes(extension)) media.controls = true;
        else media.alt = path;
        media.src = state.artifactUrl; preview.append(media);
      } else preview.append(node("p", "muted", "Download this artifact to open it in its local application."));
      return;
    }
    const file = await api(`/files?path=${encodeURIComponent(path)}`);
    if (request !== state.previewRequest) return;
    clear(preview);
    const header = node("div", "file-preview-header");
    header.append(node("span", "", file.path || path), iconButton("copy", "Copy file content", () => copyText(file.content || "")), button("Download", "button-secondary", () => downloadArtifact(path), "arrow-up-right"));
    preview.append(header, node("pre", "", file.content || ""));
    if (file.truncated) preview.append(node("p", "inline-error", "Preview truncated by the server. Inspect the complete file locally when needed."));
  } catch (error) { if (request === state.previewRequest) preview.replaceChildren(node("p", "inline-error", error.message)); }
}

async function downloadArtifact(path) {
  try {
    const file = await api(`/artifacts?path=${encodeURIComponent(path)}`, {blob:true});
    const url = URL.createObjectURL(file);
    const link = node("a", ""); link.href = url; link.download = path.replaceAll("\\", "/").split("/").pop();
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message, true); }
}
async function loadConnectors() {
  syncCapabilityTab();
  const jobs = [resourceLoad($("#connector-list"), () => fetchCatalog(), renderCapabilities)];
  if (state.capabilityTab === "mcp") jobs.push(loadMCP());
  await Promise.allSettled(jobs);
}
function facts(container, entries) {
  clear(container);
  for (const [label, value, warning] of entries) {
    const row = node("div", "setting-fact");
    row.append(node("span", "", label), node("strong", warning ? "warning" : "", value));
    container.append(row);
  }
}
function renderSettings() {
  const status = state.status;
  if (!status) return;
  const select = $("#model-select");
  const models = status.ollama?.models || [];
  const signature = JSON.stringify([models.map(model => model.name), status.model]);
  // Do not replace a selection being edited during ordinary status polling.
  if (select.dataset.signature !== signature) {
    clear(select);
    if (!models.length) { const option = node("option", "", "No installed models found"); option.value = ""; select.append(option); }
    for (const model of models) {
      const option = node("option", "", `${model.name}${model.size ? ` · ${bytes(model.size)}` : ""}`);
      option.value = model.name; option.selected = model.name === status.model || model.name === `${status.model}:latest`; select.append(option);
    }
    if (models.length && ![...select.options].some(option => option.selected && (option.value === status.model || option.value === `${status.model}:latest`))) {
      const option = node("option", "", `${status.model} · not installed`); option.value = ""; option.selected = true; select.prepend(option);
    }
    select.dataset.signature = signature;
  }
  select.disabled = !models.length;
  $("#save-model").disabled = !models.length;
  facts($("#runtime-facts"), [["Model runtime", status.ollama?.available ? "Ollama connected" : "Unavailable", !status.ollama?.available], ["Installed models", String(models.length)], ["Step limit per mission", String(status.max_steps || "—")]]);
  facts($("#voice-facts"), [["Transcription", status.voice?.transcription ? "Configured · local" : "Not configured", !status.voice?.transcription], ["Speech output", status.voice?.speech ? "Configured · local" : "Not configured", !status.voice?.speech]]);
  renderSpeechPreferences();
  facts($("#access-facts"), [["Autonomy", status.paused ? "Paused" : "Enabled", status.paused], ["Active missions", String(status.active_runs ?? "—")], ["Browser access", "Paired for this tab"]]);
}
function renderSpeechPreferences() {
  const status = state.status;
  if (!status) return;
  const backend = $("#speech-backend"), voice = $("#speech-voice"), speed = $("#speech-speed"), preview = $("#speech-preview");
  const available = Array.isArray(status.voice?.speech_backends) ? status.voice.speech_backends : [];
  const presets = (Array.isArray(status.voice?.voice_presets) ? status.voice.voice_presets : []).filter(item => typeof item?.id === "string" && typeof item?.name === "string");
  const signature = JSON.stringify([status.speech_backend, status.speech_voice, status.speech_speed, status.voice?.default_voice, presets]);
  if (!state.speechPreferencesSaving && voice.dataset.signature !== signature) {
    backend.value = status.speech_backend || (available.includes("natural") ? "natural" : "system");
    clear(voice);
    for (const preset of presets) {
      const option = node("option", "", preset.name); option.value = preset.id; voice.append(option);
    }
    if (!presets.length) { const option = node("option", "", "Natural voices unavailable"); option.value = ""; voice.append(option); }
    voice.value = status.speech_voice || status.voice?.default_voice || "cori";
    speed.value = Number.isFinite(status.speech_speed) ? String(status.speech_speed) : "1.12";
    voice.dataset.signature = signature;
  }
  for (const option of backend.options) option.disabled = !available.includes(option.value);
  const busy = state.speechPreferencesSaving || !state.authenticated;
  backend.disabled = busy || !status.voice?.speech;
  voice.disabled = busy || backend.value !== "natural" || !available.includes("natural");
  speed.disabled = voice.disabled;
  preview.disabled = busy || !status.voice?.speech || (state.speechButton === preview && !state.speechUrl);
  const runtimeLabels = {idle:"Natural voice starts locally on demand.", warming:"Preparing the local neural voice…", ready:"Local neural voice ready.", error:"Natural voice is unavailable. Review voice status or choose system speech."};
  $("#speech-runtime").textContent = backend.value === "natural" ? runtimeLabels[status.voice?.natural_runtime?.state] || "Natural speech uses local CPU inference." : "System speech uses the configured local speech adapter.";
}
async function saveSpeechPreferences({quiet = false} = {}) {
  if (!state.authenticated || state.speechPreferencesSaving) return false;
  const speed = Number($("#speech-speed").value);
  if (!Number.isFinite(speed) || speed < 0.85 || speed > 1.30) { toast("Choose a speech pace from 0.85 to 1.30.", true); return false; }
  const request = ++state.speechPreferenceRequest, token = state.token;
  const body = {backend:$("#speech-backend").value, voice:$("#speech-voice").value || state.status?.speech_voice || state.status?.voice?.default_voice || "cori", speed};
  state.speechPreferencesSaving = true;
  renderSpeechPreferences();
  try {
    const result = await api("/voice/preferences", {method:"PUT", body});
    if (!state.authenticated || state.token !== token || state.speechPreferenceRequest !== request) return false;
    Object.assign(state.status, result);
    state.speechPreferenceConfirmed = request;
    $("#speech-voice").dataset.signature = "";
    if (!quiet) toast(result.speech_warning || "Voice and pace saved for PC and browser speech.", Boolean(result.speech_warning));
    return true;
  } catch (error) {
    if (state.authenticated && state.token === token && state.speechPreferenceRequest === request) { $("#speech-voice").dataset.signature = ""; toast(error.message, true); }
    return false;
  } finally {
    if (state.speechPreferenceRequest === request) { state.speechPreferencesSaving = false; renderSpeechPreferences(); }
  }
}
async function previewSpeech() {
  const control = $("#speech-preview");
  if (state.speechButton === control) { stopSpeech(); return; }
  if (await saveSpeechPreferences({quiet:true})) await speak("I’m Ariadne. Ready when you are.", control);
}
async function saveModel(event) {
  event.preventDefault();
  const model = $("#model-select").value;
  if (!model) return;
  $("#save-model").disabled = true;
  try {
    await api("/settings", {method:"POST", body:{model}});
    state.status.model = model;
    renderStatus(state.status);
    toast(`Active model set to ${model}.`);
  } catch (error) { toast(error.message, true); }
  finally { $("#save-model").disabled = !state.authenticated || !state.status?.ollama?.models?.length; }
}
async function copyText(value) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard access requires localhost or HTTPS. Select and copy the text manually.");
    await navigator.clipboard.writeText(value);
    toast("Copied to clipboard.");
  } catch (error) { toast(error.message || "Clipboard access was unavailable. Select and copy the text manually.", true); }
}
function resizeComposer() {
  const input = $("#mission-prompt");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
}

async function toggleRecording() {
  if (state.recording) { stopRecording(); return; }
  if (!state.status?.voice?.transcription || state.transcribing) return;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1, echoCancellation:true, noiseSuppression:true}, video:false});
    const recorder = new MediaRecorder(stream);
    const record = {recorder, stream, chunks:[], discard:false, timer:null};
    state.recording = record;
    recorder.addEventListener("dataavailable", event => { if (event.data.size) record.chunks.push(event.data); });
    recorder.addEventListener("error", () => { record.discard = true; stopRecording(true); toast("Microphone recording failed. Please try again.", true); });
    recorder.addEventListener("stop", () => processRecording(record), {once:true});
    recorder.start();
    record.timer = setTimeout(() => { if (state.recording === record) stopRecording(); }, 85000);
    $("#voice-record").classList.add("recording");
    $("#voice-record").replaceChildren(icon("stop"));
    updateComposer();
    toast("Recording locally. Press the stop button when you’re finished.");
  } catch (error) {
    if (stream) stream.getTracks().forEach(track => track.stop());
    state.recording = null;
    toast(error.name === "NotAllowedError" ? "Microphone permission was not granted." : error.message || "Could not access the microphone.", true);
  }
}
function stopRecording(discard = false) {
  const record = state.recording;
  if (!record) return;
  record.discard = record.discard || discard;
  clearTimeout(record.timer);
  if (record.recorder.state !== "inactive") record.recorder.stop();
  record.stream.getTracks().forEach(track => track.stop());
  state.recording = null;
  $("#voice-record").classList.remove("recording");
  $("#voice-record").replaceChildren(icon("mic"));
  updateComposer();
}
async function processRecording(record) {
  record.stream.getTracks().forEach(track => track.stop());
  if (record.discard || !state.authenticated) return;
  state.transcribing = true;
  updateComposer();
  try {
    toast("Transcribing with your local voice adapter…");
    const blob = new Blob(record.chunks, {type:record.recorder.mimeType});
    if (!blob.size) throw new Error("No audio was captured. Please try again.");
    const wav = await toMonoWav(await blob.arrayBuffer());
    if (!state.authenticated) return;
    const data = await api("/voice/transcribe", {method:"POST", body:wav, headers:{"Content-Type":"audio/wav"}});
    const text = typeof data.text === "string" ? data.text.trim() : "";
    if (!text) throw new Error("No speech was detected. Please try again.");
    const input = $("#mission-prompt");
    input.value = `${input.value.trim()}${input.value.trim() ? "\n" : ""}${text}`.slice(0, input.maxLength);
    resizeComposer(); input.focus();
    toast("Transcription added. Review it before launching your mission.");
  } catch (error) { if (state.authenticated) toast(error.message, true); }
  finally { state.transcribing = false; updateComposer(); }
}
async function toMonoWav(arrayBuffer) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  const context = new AudioContextClass();
  let decoded;
  try { decoded = await context.decodeAudioData(arrayBuffer); }
  finally { await context.close(); }
  if (decoded.duration < 0.15) throw new Error("The recording was too short. Please try again.");
  const frames = Math.min(Math.ceil(decoded.duration * 16000), 90 * 16000);
  const offline = new OfflineAudioContext(1, frames, 16000);
  const source = offline.createBufferSource(); source.buffer = decoded; source.connect(offline.destination); source.start(0);
  const rendered = await offline.startRendering();
  const samples = rendered.getChannelData(0);
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset, text) => [...text].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); write(8, "WAVE"); write(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, "data"); view.setUint32(40, samples.length * 2, true);
  for (let index = 0; index < samples.length; index += 1) { const sample = Math.max(-1, Math.min(1, samples[index])); view.setInt16(44 + index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true); }
  return new Blob([buffer], {type:"audio/wav"});
}
function stopSpeech() {
  state.speechRequest += 1;
  clearInterval(state.speechMuteTimer);
  const player = $("#speech-player");
  player.pause(); player.removeAttribute("src");
  if (state.speechUrl) URL.revokeObjectURL(state.speechUrl);
  state.speechUrl = null;
  if (state.speechButton) {
    const control = state.speechButton;
    control.disabled = false;
    control.replaceChildren(icon("volume"));
    if (control.id === "speech-preview") control.append(node("span", "", "Preview voice"));
    control.title = control.dataset.speechIdleTitle || "Read aloud locally";
    control.setAttribute("aria-label", control.title);
  }
  state.speechButton = null;
}
async function speak(text, control) {
  if (state.speechButton === control) { stopSpeech(); return; }
  stopSpeech();
  const request = state.speechRequest, token = state.token;
  const current = () => state.authenticated && state.token === token && state.speechRequest === request && state.speechButton === control;
  if (!control.dataset.speechIdleTitle) control.dataset.speechIdleTitle = control.title || (control.id === "speech-preview" ? "Preview voice" : "Read aloud locally");
  control.disabled = true; state.speechButton = control;
  try {
    const audio = await api("/voice/speak", {method:"POST", body:{text:text.slice(0, 2000)}, blob:true});
    if (!current()) return;
    state.speechUrl = URL.createObjectURL(audio);
    const player = $("#speech-player"); player.src = state.speechUrl;
    if (state.status?.wake?.active) {
      const wakeSession = wakeGeneration;
      const wake = await api("/voice/wake/suppress", {method:"POST", body:{seconds:10}});
      if (!current() || !state.speechUrl) return;
      if (wakeGeneration !== wakeSession) { stopSpeech(); return; }
      confirmWakeSnapshot(wake);
      state.speechMuteTimer = setInterval(async () => {
        if (!current() || player.paused || wakeRequest || state.status?.wake?.active === false) return;
        const wakeSession = wakeGeneration;
        try {
          const wake = await api("/voice/wake/suppress", {method:"POST", body:{seconds:10}});
          if (current()) {
            if (wakeGeneration !== wakeSession) { stopSpeech(); return; }
            confirmWakeSnapshot(wake);
          }
        }
        catch { if (current()) stopSpeech(); }
      }, 6000);
    }
    if (!current() || !state.speechUrl) return;
    await player.play();
    if (!current()) return;
    control.disabled = false; control.replaceChildren(icon("stop"));
    if (control.id === "speech-preview") control.append(node("span", "", "Stop preview"));
    control.title = "Stop speech playback";
    control.setAttribute("aria-label", "Stop speech playback");
  } catch (error) { if (current()) { toast(error.message, true); stopSpeech(); } }
}

function syncKnowledgeTab() {
  const sources = state.knowledgeTab === "sources";
  $("#knowledge-documents-panel").hidden = sources;
  $("#knowledge-sources-panel").hidden = !sources;
  $("#knowledge-document-actions").hidden = sources;
  for (const tab of $$('[data-knowledge-tab]')) {
    const selected = tab.dataset.knowledgeTab === state.knowledgeTab;
    tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
  }
}
async function setKnowledgeTab(tab) {
  state.knowledgeTab = tab === "sources" ? "sources" : "documents";
  syncKnowledgeTab();
  if (state.authenticated) await (state.knowledgeTab === "sources" ? loadSources() : loadKnowledgeDocuments());
}
function resetSourcePreview() {
  state.preview = null; state.previewPath = null; state.sourcePreviewRequest += 1;
  $("#source-preview").replaceChildren(emptyState("book", "Review before you import.", "Select a document to inspect its extracted text, provenance, and any format warnings."));
}
async function loadSources() {
  const list = $("#source-file-list");
  try {
    const data = await api("/sources");
    state.sources = data.sources || [];
    state.sourcePolicy = data.policy || {};
    if (!state.sources.some(source => source.id === state.sourceId)) { state.sourceId = state.sources[0]?.id || null; state.sourceDirectory = "."; resetSourcePreview(); }
    const select = $("#source-select"); clear(select);
    for (const source of state.sources) { const option = node("option", "", source.name); option.value = source.id; select.append(option); }
    if (!state.sources.length) { const option = node("option", "", "No source folders yet"); option.value = ""; select.append(option); }
    select.value = state.sourceId || ""; select.disabled = !state.sources.length;
    $("#remove-source").disabled = !state.sourceId;
    const source = state.sources.find(item => item.id === state.sourceId);
    $("#source-scope").textContent = source ? `Read-only source · ${source.path}` : "Add a specific folder you want Ariadne to read. Drive E: is excluded.";
    if (source) await loadSourceFiles();
    else { list.replaceChildren(emptyState("folder", "Choose a source folder.", "Add read-only access to a folder on this server.")); $("#source-breadcrumb").textContent = "No folder selected"; }
  } catch (error) { list.replaceChildren(node("p", "inline-error", error.message)); }
}
async function removeSource() {
  const source = state.sources.find(item => item.id === state.sourceId);
  if (!source || !await confirmAction("Remove source folder access?", `Read-only access to “${source.name}” will be removed. Documents already imported into Knowledge will remain.`, "Remove access")) return;
  try {
    await api(`/sources/${encodeURIComponent(source.id)}`, {method:"DELETE"});
    state.sourceId = null; state.sourceDirectory = "."; resetSourcePreview();
    await loadSources(); toast("Source folder access removed.");
  } catch (error) { toast(error.message, true); }
}
async function loadSourceFiles() {
  const sourceId = state.sourceId;
  if (!sourceId) return;
  const directory = state.sourceDirectory;
  const request = ++state.sourceRequest;
  const container = $("#source-file-list"); container.replaceChildren(node("div", "loading-state", "Reading source folder…"));
  try {
    const data = await api(`/sources/${encodeURIComponent(sourceId)}/files?directory=${encodeURIComponent(directory)}`);
    if (request !== state.sourceRequest || sourceId !== state.sourceId) return;
    clear(container);
    $("#source-breadcrumb").textContent = directory === "." ? "Source folder" : directory;
    if (directory !== ".") {
      const back = node("button", "file-item"); back.type = "button"; back.append(icon("arrow-left"), node("span", "", "Parent folder"));
      back.addEventListener("click", () => { const parts = directory.split("/"); parts.pop(); state.sourceDirectory = parts.join("/") || "."; resetSourcePreview(); loadSourceFiles(); }); container.append(back);
    }
    const files = [...(data.files || [])].sort((a, b) => (b.type === "directory") - (a.type === "directory") || String(a.name || a.path).localeCompare(String(b.name || b.path)));
    if (!files.length) container.append(emptyState("folder", "No readable entries.", "This folder has no entries available under the server's source policy."));
    for (const file of files) {
      const path = file.path || file.name;
      const item = node("button", "file-item"); item.type = "button"; item.title = path;
      const label = node("span");
      const extension = String(file.name || path).split(".").pop().toLowerCase();
      const format = state.catalog?.formats?.find(entry => String(entry.extension).replace(/^\./, "").toLowerCase() === extension);
      const readable = format?.ready || (extension === "pdf" && state.catalog?.ocr?.ready && $("#source-ocr").checked);
      const unavailable = file.type !== "directory" && state.catalog?.formats?.length && !readable;
      label.append(node("strong", "", file.name || path), node("small", "", file.type === "directory" ? "Folder" : unavailable ? format ? "Reader needs setup" : "Unsupported format" : bytes(file.size)));
      item.append(icon(file.type === "directory" ? "folder" : "file"), label);
      item.disabled = Boolean(unavailable);
      item.addEventListener("click", () => {
        if (file.type === "directory") { state.sourceDirectory = path.replaceAll("\\", "/"); resetSourcePreview(); loadSourceFiles(); }
        else { $$(".file-item", container).forEach(entry => entry.classList.remove("active")); item.classList.add("active"); previewSource(path); }
      });
      container.append(item);
    }
    if (data.truncated) container.append(node("p", "inline-error", "The server limited this directory listing. Open a subfolder to see more."));
  } catch (error) { if (request === state.sourceRequest) container.replaceChildren(node("p", "inline-error", error.message)); }
}
async function previewSource(path) {
  const sourceId = state.sourceId;
  if (!sourceId) return;
  const request = ++state.sourcePreviewRequest;
  state.previewPath = path; state.preview = null;
  const useOCR = String(path).toLowerCase().endsWith(".pdf") && $("#source-ocr").checked;
  const container = $("#source-preview"); container.replaceChildren(node("div", "loading-state", "Extracting reference text locally…"));
  try {
    const preview = await api(`/sources/${encodeURIComponent(sourceId)}/preview`, {method:"POST", body:{path, ocr:useOCR}});
    if (request !== state.sourcePreviewRequest || sourceId !== state.sourceId) return;
    state.preview = preview; renderSourcePreview(preview, sourceId);
    if (window.matchMedia("(max-width:700px)").matches) container.scrollIntoView({block:"start", behavior:window.matchMedia("(prefers-reduced-motion:reduce)").matches ? "instant" : "smooth"});
  } catch (error) { if (request === state.sourcePreviewRequest) container.replaceChildren(node("p", "inline-error", error.message)); }
}
function renderSourcePreview(preview, sourceId) {
  const container = $("#source-preview"); clear(container);
  const header = node("div", "source-preview-heading");
  header.append(node("span", "eyebrow", "REVIEW A LOCAL REFERENCE"), node("h2", "", preview.title || preview.path), node("p", "", preview.path));
  const metadata = node("div", "source-metadata");
  metadata.append(node("span", "tag", String(preview.format || "Text")), node("span", "", bytes(preview.bytes)));
  if (preview.processor?.name === "rapidocr") metadata.append(node("span", "tag approval-required", "OCR · review recognized values"));
  header.append(metadata); container.append(header);
  const note = node("div", "source-import-note"); note.append(icon("shield"), node("span", "", "Importing adds searchable reference text. It does not install software or grant new tool permissions.")); container.append(note);
  for (const warning of preview.warnings || []) { const item = node("div", "source-warning"); item.append(icon("alert"), node("span", "", typeof warning === "string" ? warning : pretty(warning))); container.append(item); }
  const text = typeof preview.text === "string" ? preview.text : "";
  const excerpt = node("pre", "source-text", text.slice(0, 20000)); container.append(excerpt);
  if (text.length > 20000) {
    const showAll = button(`Show complete extracted text (${text.length.toLocaleString()} characters)`, "button-secondary", () => { excerpt.textContent = text; showAll.remove(); });
    showAll.classList.add("source-show-all"); container.append(showAll);
  }
  const provenance = node("details", "tool-details source-hash");
  provenance.append(node("summary", "", "Reviewed source & extraction fingerprint"), node("pre", "", pretty({source_sha256:preview.sha256 || null, review_identity:preview.review_identity || null, processor:preview.processor || null, ocr:Boolean(preview.ocr)}))); container.append(provenance);
  const error = node("p", "form-error"); error.hidden = true; error.setAttribute("role", "alert"); container.append(error);
  const actions = node("div", "source-preview-actions");
  const refresh = button("Refresh preview", "button-secondary", () => previewSource(preview.path), "refresh");
  const importButton = button("Import into Knowledge", "button-primary", async () => {
    importButton.disabled = true; error.hidden = true;
    try {
      const result = await api(`/sources/${encodeURIComponent(sourceId)}/import`, {method:"POST", body:{path:preview.path, sha256:preview.sha256, ocr:Boolean(preview.ocr), review_identity:preview.review_identity}});
      importButton.textContent = result.unchanged ? "Already imported" : "Imported";
      toast(result.unchanged ? "This exact source is already in Knowledge." : "Reviewed reference imported into Knowledge.");
      await loadKnowledgeDocuments();
    } catch (failure) {
      error.textContent = failure.status === 409 ? "The source or extraction processor changed after this preview. Refresh it and review the new text before importing." : failure.message;
      error.hidden = false;
      importButton.disabled = failure.status === 409;
    }
  }, "plus");
  importButton.disabled = !preview.sha256;
  actions.append(refresh, importButton); container.append(actions);
}
async function fetchCatalog() {
  const data = await api("/capabilities"); state.catalog = data;
  const supported = (data.formats || []).filter(format => format.ready);
  $("#supported-formats").textContent = supported.length ? `Available file formats: ${supported.map(format => format.extension).join(", ")}.` : "No document file readers are available. Paste reference text instead.";
  $("#knowledge-file-input").accept = supported.map(format => `.${String(format.extension).replace(/^\./, "")}`).join(",");
  updateOCRControls();
  return data;
}
function renderCapabilities(data = state.catalog) {
  if (!data) return;
  const container = $("#connector-list"); clear(container);
  const query = $("#capability-search").value.trim().toLowerCase();
  const all = data.capabilities || [];
  const destinations = new Map([["documents", ["Open Knowledge", "knowledge"]], ["pdf", ["Open Knowledge", "knowledge"]], ["ocr", ["Open Knowledge", "knowledge"]], ["workflows", ["Open workflows", "workflows"]], ["speech_input", ["Open mission control", "missions"]], ["speech_output", ["Open mission control", "missions"]]]);
  const capabilities = all.filter(capability => [capability.name, capability.description, capability.category, ...(capability.tools || []).map(tool => tool.name)].join(" ").toLowerCase().includes(query));
  $("#capability-summary").textContent = `${all.filter(capability => capability.status === "ready").length} ready · ${all.length} total`;
  for (const capability of capabilities) {
    const ready = capability.status === "ready";
    const card = node("article", `resource-card capability-card${ready ? "" : " needs-setup"}`);
    const header = node("div", "resource-card-header");
    const category = String(capability.category || "").toLowerCase();
    const symbol = node("div", "card-icon"); symbol.append(icon(category.includes("voice") ? "mic" : category.includes("file") ? "folder" : category.includes("knowledge") ? "book" : "plug"));
    header.append(symbol, node("span", `tag ${ready ? "ready-tag" : "approval-required"}`, ready ? "Ready" : "Needs setup"));
    const tags = node("div", "tag-row");
    tags.append(node("span", "tag", capability.offline ? "Works offline" : "Network required"), node("span", `tag${capability.approval_required ? " approval-required" : ""}`, capability.approval_required ? "Approval required" : "Within configured scope"));
    card.append(header, node("span", "capability-category", humanize(capability.category || "Capability")), node("h2", "", capability.name), tags, node("p", "", capability.description));
    if (capability.details) card.append(node("p", "capability-detail", capability.details));
    if ((capability.tools || []).length) {
      const tools = node("details", "tool-details capability-tools"); tools.append(node("summary", "", `Inspect ${capability.tools.length} ${capability.tools.length === 1 ? "tool" : "tools"}`));
      for (const tool of capability.tools) {
        const toolCard = node("div", "capability-tool");
        toolCard.append(node("h3", "", tool.name), node("p", "", tool.description), node("small", "", tool.approval_required ? "Each action requires approval" : "Automatic within this tool's scope"));
        if (tool.parameters) toolCard.append(node("pre", "", pretty(tool.parameters)));
        tools.append(toolCard);
      }
      card.append(tools);
    }
    const destination = destinations.get(capability.id);
    const action = button(capability.id === "mcp" ? "Manage local MCP" : ready ? destination?.[0] || "Use in mission" : "View setup", "button-secondary", () => {
      if (capability.id === "mcp") { setCapabilityTab("mcp"); return; }
      if (!ready) { openCapabilitySetup(capability); return; }
      if (destination) { showView(destination[1]); return; }
      newMission(); $("#mission-prompt").value = `Help me use the ${capability.name} capability. First ask me what outcome I want, then work toward it using the available tools and their existing permissions.`; resizeComposer(); $("#mission-prompt").focus();
    }, ready ? "arrow-up-right" : "settings");
    action.classList.add("capability-use"); card.append(action); container.append(card);
  }
  if (!capabilities.length) container.append(emptyState("plug", query ? "No matching capabilities." : "No capabilities reported.", query ? "Try a tool name or a broader search." : "Refresh this catalog after the server is configured."));
  const formats = $("#format-catalog"); clear(formats);
  if ((data.formats || []).length) {
    formats.append(node("h2", "", "Reference file formats"));
    const list = node("div", "format-list");
    for (const format of data.formats) { const item = node("div", `format-item${format.ready ? "" : " unavailable"}`); item.append(icon(format.ready ? "check" : "settings"), node("strong", "", format.extension), node("span", "", `${format.description || humanize(format.format || "Reference text")}${format.ready ? "" : " · needs setup"}`)); list.append(item); }
    formats.append(list);
  }
}

function openCapabilitySetup(capability) {
  $("#capability-setup-title").textContent = capability.name;
  const body = $("#capability-setup-body"); clear(body);
  body.append(node("p", "", capability.description), node("p", "muted", capability.details || "Configure this local capability, then refresh its status."));
  if (capability.setup_command) {
    const command = node("div", "command-row");
    command.append(node("code", "", capability.setup_command), iconButton("copy", "Copy setup command", () => copyText(capability.setup_command)));
    body.append(node("h3", "", "Provision on this server"), command, node("p", "muted small", "Provisioning is an operator step. Keep runtime assets and model caches with the box; normal startup never downloads them."));
  }
  if (capability.id === "local_browser") body.append(button("Manage local applications", "button-secondary", () => { $("#capability-setup-dialog").close(); openLocalApps(); }, "plug"));
  $("#capability-setup-dialog").showModal();
}

async function openLocalApps() {
  try {
    const data = await api("/automation");
    const list = $("#local-apps-list"); clear(list);
    for (const app of data.apps || []) {
      const row = node("article", "local-app-row");
      const text = node("div", ""); text.append(node("strong", "", app.id), node("p", "muted small", app.url), node("p", "", app.description || ""));
      row.append(text, iconButton("trash", `Remove ${app.id}`, async () => {
        try { await api(`/automation/apps/${encodeURIComponent(app.id)}`, {method:"DELETE"}); await openLocalApps(); await loadConnectors(); }
        catch (error) { toast(error.message, true); }
      })); list.append(row);
    }
    if (!data.apps?.length) list.append(node("p", "muted", "No local applications registered."));
    $("#local-flow-summary").textContent = `${data.flows?.length || 0} fixed local workflow routes configured. Service availability is checked when invoked.`;
    $("#local-app-error").hidden = true;
    if (!$("#local-apps-dialog").open) $("#local-apps-dialog").showModal();
  } catch (error) { toast(error.message, true); }
}

async function saveLocalApp(event) {
  event.preventDefault();
  $("#save-local-app").disabled = true;
  try {
    await api("/automation/apps", {method:"POST", body:{id:$("#local-app-id").value.trim(), url:$("#local-app-url").value.trim(), description:$("#local-app-description").value.trim()}});
    $("#local-app-form").reset(); await openLocalApps(); await loadConnectors(); toast("Local application registered. Browser actions still require approval.");
  } catch (error) { $("#local-app-error").textContent = error.message; $("#local-app-error").hidden = false; }
  finally { $("#save-local-app").disabled = false; }
}
async function uploadKnowledge(file) {
  if (!file || state.uploading) return;
  const extension = file.name.split(".").pop().toLowerCase();
  const useOCR = extension === "pdf" && $("#upload-ocr").checked;
  const recognizing = useOCR || ["png", "jpg", "jpeg"].includes(extension);
  state.uploading = true;
  const control = $("#upload-knowledge"); control.disabled = true;
  control.replaceChildren(icon("upload"), document.createTextNode("Importing…"));
  $("#import-upload").disabled = true; $("#upload-ocr").disabled = true;
  $("#cancel-upload").textContent = "Close (import continues)";
  $("#upload-progress").textContent = recognizing ? "Recognizing text with local CPU models… Processing can take up to 90 seconds." : "Extracting and indexing reference text locally…";
  $("#upload-progress").hidden = false; $("#upload-error").hidden = true;
  try {
    const limit = state.catalog?.limits?.input_bytes || state.sourcePolicy?.input_bytes || 10_000_000;
    if (file.size > limit) throw new Error(`This server accepts reference files up to ${bytes(limit)}. Choose a smaller file.`);
    const content = await new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.addEventListener("load", () => resolve(String(reader.result).split(",", 2)[1] || ""), {once:true}); reader.addEventListener("error", () => reject(new Error("The selected file could not be read.")), {once:true}); reader.readAsDataURL(file);
    });
    const result = await api("/knowledge/upload", {method:"POST", body:{name:file.name, content_base64:content, ocr:useOCR}});
    $("#upload-dialog").close();
    state.knowledgeTab = "documents"; syncKnowledgeTab(); await loadKnowledgeDocuments();
    toast(result.warnings?.length ? `Reference imported. Format notes: ${result.warnings.slice(0, 2).map(item => typeof item === "string" ? item : pretty(item)).join(" ")}` : "Reference file imported into local Knowledge.");
  } catch (error) {
    if ($("#upload-dialog").open) { $("#upload-error").textContent = error.message; $("#upload-error").hidden = false; }
    else if (state.authenticated) toast(error.message, true);
  }
  finally { state.uploading = false; control.disabled = false; control.replaceChildren(icon("upload"), document.createTextNode("Upload file")); $("#knowledge-file-input").value = ""; $("#upload-progress").hidden = true; $("#import-upload").disabled = false; $("#cancel-upload").textContent = "Cancel"; updateUploadMode(); }
}

const checkTemplates = {
  file_exists:{kind:"file_exists", path:"output/report.md"},
  file_contains:{kind:"file_contains", path:"output/report.md", text:"Summary"},
  file_size:{kind:"file_size", path:"output/plan.json", max_bytes:2500},
  json_valid:{kind:"json_valid", path:"output/plan.json"},
  json_number:{kind:"json_number", path:"output/summary.json", pointer:"/total_value", value:"16.00"},
  json_value:{kind:"json_value", path:"output/plan.json", pointer:"/draft", equals:true},
  json_shape:{kind:"json_shape", path:"output/plan.json", fields:{draft:"boolean", scope:"string", unknowns:"strings"}},
  csv_columns:{kind:"csv_columns", path:"output/budget.csv", columns:["item", "amount"]},
};
function parseChecks(raw) {
  let checks;
  try { checks = JSON.parse(raw || "[]"); }
  catch { throw new Error("Checks must be valid JSON. Use a template to get started."); }
  if (!Array.isArray(checks) || checks.length > 10) throw new Error("Provide an array containing at most 10 checks.");
  // Inspect original number tokens before JSON.stringify can silently round a
  // decimal literal (even one that JSON.parse rounds to an integer).
  const unquoted = String(raw || "[]").replace(/"(?:\\.|[^"\\])*"/g, '""');
  for (const match of unquoted.matchAll(/-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g)) {
    if (/[.eE]/.test(match[0]) || !Number.isSafeInteger(Number(match[0]))) throw new Error('Use a quoted decimal string such as "16.00" for decimal or large expected values. Unquoted numbers must be exact safe integers.');
  }
  return checks.map((check, index) => {
    const label = `Check ${index + 1}`;
    if (!check || typeof check !== "object" || Array.isArray(check) || !Object.hasOwn(checkTemplates, check.kind)) throw new Error(`${label}: choose file_exists, file_contains, file_size, json_valid, json_number, json_value, json_shape, or csv_columns.`);
    if (typeof check.path !== "string" || !check.path.trim()) throw new Error(`${label}: provide a workspace-relative file path.`);
    const path = check.path.trim().replaceAll("\\", "/");
    if (path.startsWith("/") || /^[a-z]:/i.test(path) || path.split("/").some(part => part === ".." || part === "." || !part)) throw new Error(`${label}: use a relative workspace path such as output/report.md, without parent-directory segments.`);
    const allowed = new Set(["kind", "path", ...(check.kind === "file_contains" ? ["text"] : check.kind === "csv_columns" ? ["columns"] : check.kind === "json_number" ? ["pointer", "value"] : check.kind === "json_value" ? ["pointer", "equals"] : check.kind === "file_size" ? ["max_bytes"] : check.kind === "json_shape" ? ["fields"] : [])]);
    if (Object.keys(check).some(key => !allowed.has(key))) throw new Error(`${label}: contains an unexpected field. Use only ${[...allowed].join(", ")}.`);
    if (check.kind === "file_contains" && (typeof check.text !== "string" || !check.text || check.text.length > 4000)) throw new Error(`${label}: provide the exact text the file must contain, up to 4,000 characters.`);
    if (check.kind === "csv_columns" && (!Array.isArray(check.columns) || !check.columns.length || check.columns.length > 100 || check.columns.some(column => typeof column !== "string" || !column || column.length > 200) || new Set(check.columns).size !== check.columns.length)) throw new Error(`${label}: columns must contain 1 to 100 unique column names, up to 200 characters each.`);
    if (check.kind === "file_size" && (!Number.isInteger(check.max_bytes) || check.max_bytes < 1 || check.max_bytes > 10000000)) throw new Error(`${label}: max_bytes must be a whole number from 1 to 10,000,000.`);
    if (check.kind === "json_shape") {
      const fields = check.fields, kinds = new Set(["string","boolean","integer","null","strings","nonempty_strings"]);
      if (!fields || typeof fields !== "object" || Array.isArray(fields) || !Object.keys(fields).length || Object.keys(fields).length > 16 || Object.entries(fields).some(([key,value]) => !/^[A-Za-z_][A-Za-z0-9_]{0,79}$/.test(key) || !kinds.has(value))) throw new Error(`${label}: fields must define 1–16 named root fields with bounded local types.`);
    }
    if (check.kind === "json_number" || check.kind === "json_value") {
      if (typeof check.pointer !== "string" || check.pointer.length > 500 || (check.pointer !== "" && !check.pointer.startsWith("/")) || check.pointer.split("/").length - 1 > 32 || /~(?![01])/.test(check.pointer)) throw new Error(`${label}: provide a JSON pointer such as /total_value, up to 500 characters and 32 segments. Escape ~ as ~0 and / inside a key as ~1. An empty pointer selects the root value.`);
    }
    if (check.kind === "json_value") {
      const value = check.equals;
      if (!(value === null || typeof value === "boolean" || typeof value === "string" && value.length <= 4000 || typeof value === "number" && Number.isSafeInteger(value))) throw new Error(`${label}: equals must be a string up to 4,000 characters, boolean, null or exact safe integer.`);
    }
    if (check.kind === "json_number") {
      const validNumber = typeof check.value === "number" && Number.isSafeInteger(check.value);
      const validDecimal = typeof check.value === "string" && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(check.value) && Number.isFinite(Number(check.value));
      if (!validNumber && !validDecimal) throw new Error(`${label}: value must be an exact safe integer or a quoted decimal such as "16.00". The server enforces its numeric bounds.`);
    }
    return {...check, path};
  });
}
function updateCheckCount() {
  try {
    const count = parseChecks($("#mission-checks").value).length;
    $("#checks-count").textContent = count ? `${count} ${count === 1 ? "check" : "checks"}` : "Optional";
  } catch { $("#checks-count").textContent = "Review JSON"; }
  $("#mission-checks-error").hidden = true;
}
function addCheckTemplate(input, kind) {
  try {
    const checks = parseChecks(input.value);
    if (checks.length >= 10) throw new Error("A mission can have at most 10 output checks.");
    checks.push({...checkTemplates[kind]});
    input.value = JSON.stringify(checks, null, 2);
    if (input.id === "mission-checks") updateCheckCount();
    input.focus();
  } catch (error) { toast(error.message, true); }
}
function checkTemplateControls(input) {
  const controls = node("div", "check-templates");
  for (const [kind, label] of [["file_exists", "+ File"], ["file_contains", "+ Text"], ["file_size", "+ Size"], ["json_valid", "+ JSON"], ["json_number", "+ Number"], ["json_value", "+ Value"], ["json_shape", "+ Shape"], ["csv_columns", "+ CSV"]]) {
    const item = node("button", "", label); item.type = "button";
    item.addEventListener("click", () => addCheckTemplate(input, kind)); controls.append(item);
  }
  return controls;
}
function checkDescription(check) {
  if (check.kind === "file_contains") return `Contains ${JSON.stringify(String(check.text || "").slice(0, 150))}`;
  if (check.kind === "csv_columns") return `CSV columns: ${(check.columns || []).join(", ")}`;
  if (check.kind === "json_number") return `JSON ${check.pointer || "(root value)"} equals ${String(check.value)}`;
  if (check.kind === "json_value") return `JSON ${check.pointer || "(root value)"} exactly equals ${JSON.stringify(check.equals)}`;
  if (check.kind === "file_size") return `At most ${check.max_bytes} bytes`;
  if (check.kind === "json_shape") return `Exact typed JSON fields: ${Object.keys(check.fields || {}).join(", ")}`;
  return check.kind === "json_valid" ? "Parses as valid JSON" : "File exists";
}
function checkDefinitions(checks) {
  return (checks || []).filter(check => Object.hasOwn(checkTemplates, check.kind)).map(check => ({kind:check.kind, path:check.path, ...(check.kind === "file_contains" ? {text:check.text} : check.kind === "csv_columns" ? {columns:check.columns} : check.kind === "json_number" ? {pointer:check.pointer, value:check.value} : check.kind === "json_value" ? {pointer:check.pointer, equals:check.equals} : check.kind === "file_size" ? {max_bytes:check.max_bytes} : check.kind === "json_shape" ? {fields:check.fields} : {})}));
}
function renderVerification(detail, parent) {
  const verification = detail.verification || {status:detail.run?.verification_status || "not_requested", checks:[]};
  const artifacts = detail.artifacts || verification.artifacts || [];
  const status = verification.status || "not_requested";
  if (isActive(detail.run) && status === "not_requested" && !artifacts.length) return;
  const card = node("section", `verification-card verification-${status}`);
  card.setAttribute("aria-label", "Mission output verification");
  const heading = node("div", "verification-heading");
  const labels = {passed:"Expected outputs verified", failed:"Output checks need attention", pending:"Expected outputs awaiting verification", not_requested:"Outcome checks not requested"};
  heading.append(icon(status === "passed" ? "check" : status === "failed" ? "alert" : status === "pending" ? "clock" : "eye"), node("h2", "", labels[status] || "Output verification"));
  card.append(heading);
  if (status === "not_requested") card.append(node("p", "verification-explanation", "Execution and outcome verification are separate. This mission did not request automatic file checks."));
  if (status === "pending") card.append(node("p", "verification-explanation", "The server checks the expected workspace files when execution finishes."));
  if (status === "passed") card.append(node("p", "verification-explanation", "These checks confirm the requested files and their specified content or structure."));
  if (status === "failed") card.append(node("p", "verification-explanation", "The requested outputs did not pass every check. Review the results before continuing."));
  for (const check of verification.checks || []) {
    const row = node("div", `verification-check ${check.status || "pending"}`);
    row.append(icon(check.status === "passed" ? "check" : check.status === "failed" ? "alert" : "clock"));
    const text = node("div");
    text.append(node("strong", "", check.path || humanize(check.kind)), node("span", "", `${humanize(check.kind)} · ${check.message || humanize(check.status || "pending")}`));
    row.append(text, node("span", "check-status", humanize(check.status || "pending")));
    card.append(row);
  }
  if (artifacts.length) {
    card.append(node("h3", "artifact-label", "Workspace outputs"));
    const list = node("div", "artifact-list");
    for (const artifact of artifacts) {
      if (!artifact || typeof artifact.path !== "string") continue;
      const item = node("button", "artifact-file"); item.type = "button";
      const text = node("span"); text.append(node("strong", "", artifact.path), node("small", "", `${bytes(artifact.size)}${artifact.sha256 ? ` · SHA-256 ${artifact.sha256.slice(0, 12)}…` : ""}`));
      item.append(icon("file"), text, icon("arrow-up-right"));
      item.title = `Preview ${artifact.path}${artifact.sha256 ? `\nSHA-256: ${artifact.sha256}` : ""}`;
      item.addEventListener("click", () => openArtifact(artifact.path)); list.append(item);
    }
    card.append(list);
  }
  parent.append(card);
}
async function openArtifact(path) {
  const parts = path.replaceAll("\\", "/").split("/"); parts.pop();
  state.directory = parts.join("/") || ".";
  await showView("files");
  await readFile(path);
  for (const item of $$(".file-item", $("#file-list"))) item.classList.toggle("active", item.title === path);
  if (window.matchMedia("(max-width:700px)").matches) $("#file-preview").scrollIntoView({block:"start"});
}
async function loadWorkflows() {
  syncWorkflowTab();
  if (state.workflowTab === "routines") { await window.ariadneRoutines?.load(); return; }
  await Promise.allSettled([loadAIWorkflows(), loadProcedures()]);
}
async function loadAIWorkflows() {
  const container = $("#workflow-list");
  await resourceLoad(container, () => api("/workflows"), data => {
    state.workflows = data.workflows || []; renderWorkflowFilter(); renderAIWorkflows();
  });
}
function renderWorkflowFilter() {
  const select = $("#workflow-agent-filter"); select.replaceChildren();
  const all = node("option", "", "All agents"); all.value = ""; select.append(all);
  const auto = node("option", "", "Auto routing"); auto.value = "auto"; select.append(auto);
  const available = new Map(agentCatalog.map(agent => [agent.id, agent.name]));
  for (const workflow of state.workflows) if (!available.has(workflow.agent_id || "general")) available.set(workflow.agent_id || "general", workflowAgentLabel(workflow.agent_id));
  for (const [id, name] of available) { if (id === "auto") continue; const option = node("option", "", name); option.value = id; select.append(option); }
  if (![...select.options].some(option => option.value === state.workflowAgentFilter)) state.workflowAgentFilter = "";
  select.value = state.workflowAgentFilter;
}
function renderAIWorkflows() {
    const container = $("#workflow-list"); clear(container);
    const workflows = state.workflows.filter(workflow => !state.workflowAgentFilter || (workflow.agent_id || "general") === state.workflowAgentFilter);
    $("#workflow-filter-count").textContent = `${workflows.length} ${workflows.length === 1 ? "workflow" : "workflows"}`;
    if (!workflows.length) { container.append(emptyState("workflow", state.workflowAgentFilter ? "No workflows assigned to this agent." : "Make useful work repeatable.", "Save a mission with clear instructions and expected output files.", {label:"Create a workflow", fn:() => openEditor("workflow", {values:{agent_id:state.workflowAgentFilter || "general"}})})); return; }
    for (const workflow of workflows) {
      const card = node("article", "resource-card workflow-card");
      const header = node("div", "resource-card-header");
      const symbol = node("div", "card-icon"); symbol.append(icon("workflow")); header.append(symbol);
      if (workflow.builtin) header.append(node("span", "tag", "Starter workflow"));
      header.append(node("span", "tag", workflowAgentLabel(workflow.agent_id)));
      if (agentCatalog.find(agent => agent.id === workflow.agent_id)?.connectivity === "local_only") header.append(node("span", "tag", "Local only"));
      if (workflow.isolated_outputs === true) header.append(node("span", "tag", "New checked-output folder"));
      card.append(header, node("h2", "", workflow.name), node("p", "workflow-excerpt", workflow.prompt));
      const checks = workflow.checks || [];
      const list = node("div", "workflow-check-preview");
      list.append(node("span", "workflow-check-label", checks.length ? `${checks.length} expected output ${checks.length === 1 ? "check" : "checks"}` : "No automatic outcome checks"));
      for (const check of checks.slice(0, 3)) { const line = node("span", ""); line.append(icon("file"), document.createTextNode(check.path || humanize(check.kind))); list.append(line); }
      if (checks.length > 3) list.append(node("small", "", `+ ${checks.length - 3} more`));
      card.append(list);
      const actions = node("div", "workflow-actions");
      actions.append(button("Review & run", "button-primary", () => reviewWorkflow(workflow), "play"));
      actions.append(button("Schedule", "button-secondary", () => scheduleWorkflow(workflow), "clock"));
      const tools = node("div", "workflow-edit-actions");
      tools.append(iconButton("settings", "Edit workflow", () => editWorkflow(workflow)), iconButton("copy", "Copy workflow", () => editWorkflow(workflow, true)), iconButton("trash", "Remove workflow", () => removeResource("workflows", workflow.id, "Remove this workflow?", `“${workflow.name}” will be removed from your workflow library. Existing missions keep their recorded instructions and results.`)));
      actions.append(tools); card.append(actions); container.append(card);
    }
}
function editWorkflow(workflow, copy = false) {
  openEditor("workflow", {editId:copy ? null : workflow.id, values:{name:`${workflow.name}${copy ? " copy" : ""}`.slice(0, 120), agent_id:workflow.agent_id || "general", isolated_outputs:workflow.isolated_outputs === true, prompt:workflow.prompt, checks:JSON.stringify(workflow.checks || [], null, 2)}});
}
function saveRunWorkflow() {
  const detail = state.detail;
  if (!detail) return;
  let prompt = [...(detail.messages || [])].reverse().find(message => message.role === "user" && typeof message.content === "string")?.content || "";
  let checks = checkDefinitions(detail.run?.checks || detail.verification?.checks);
  const isolated = detail.run?.isolated_outputs === true, prefix = detail.run?.output_prefix;
  if (isolated && typeof prefix === "string" && prefix) {
    const escaped = prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    prompt = prompt.replace(new RegExp(escaped+"(?=/|[^A-Za-z0-9_-]|$)", "g"), "{{run_dir}}");
    checks = checks.map(check => typeof check.path === "string" && check.path.startsWith(prefix+"/") ? {...check, path:"{{run_dir}}"+check.path.slice(prefix.length)} : check);
  }
  openEditor("workflow", {values:{name:String(detail.run?.title || "New workflow").slice(0, 120), agent_id:detail.run?.agent_id || "general", isolated_outputs:isolated, prompt, checks:JSON.stringify(checks, null, 2)}});
}
function reviewWorkflow(workflow) {
  state.workflowToRun = workflow;
  $("#workflow-review-title").textContent = workflow.name;
  $("#workflow-review-prompt").textContent = workflow.prompt;
  $("#workflow-review-agent").textContent = workflowAgentLabel(workflow.agent_id);
  $("#workflow-review-output-mode").textContent = workflow.isolated_outputs === true ? "New checked-output folder per run. {{run_dir}} expands when queued; existing tool permissions remain active." : "Fixed expected output paths. Review these paths before repeating the workflow.";
  $("#workflow-review-error").hidden = true;
  const checks = $("#workflow-review-checks"); clear(checks);
  for (const check of workflow.checks || []) {
    const row = node("div", "review-check"); row.append(icon("file"));
    const text = node("div"); text.append(node("strong", "", check.path), node("span", "", checkDescription(check))); row.append(text); checks.append(row);
  }
  if (!(workflow.checks || []).length) checks.append(node("p", "review-intro", "This workflow has no automatic outcome checks."));
  updateComposer();
  $("#workflow-review-dialog").showModal();
}
async function launchWorkflow() {
  if (!state.workflowToRun || state.workflowLaunching || !modelReady()) return;
  state.workflowLaunching = true; updateComposer();
  try {
    const data = await api(`/workflows/${encodeURIComponent(state.workflowToRun.id)}/run`, {method:"POST", body:{}});
    $("#workflow-review-dialog").close();
    await loadRuns(); await selectRun(data.run.id); refreshStatus({quiet:true});
  } catch (error) { $("#workflow-review-error").textContent = error.message; $("#workflow-review-error").hidden = false; }
  finally { state.workflowLaunching = false; updateComposer(); }
}

function syncWorkflowTab() {
  for (const tab of $$('[data-workflow-tab]')) {
    const selected = tab.dataset.workflowTab === state.workflowTab;
    tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
    $(`#workflows-${tab.dataset.workflowTab}-panel`).hidden = !selected;
  }
}
function setWorkflowTab(name) {
  if (!["ai", "procedures", "routines"].includes(name)) return;
  state.workflowTab = name; syncWorkflowTab();
  if (name === "procedures" && state.authenticated) loadProcedures();
  if (name === "routines" && state.authenticated) window.ariadneRoutines?.load();
}
function procedureRequestId(run) { return run?.request_id || run?.id || ""; }
function procedureControls() {
  for (const control of $$(".procedure-run-button")) control.disabled = state.procedureRunBusy || !state.authenticated;
  for (const id of ["refresh-procedures","refresh-procedure-runs"]) $(`#${id}`).disabled = state.procedureRunBusy;
  $("#run-procedure").disabled = state.procedureRunBusy || !state.procedureReview || !state.authenticated;
  $("#cancel-procedure-review").textContent = state.procedureRunBusy ? "Close (run continues)" : "Cancel";
}
async function loadProcedures() {
  if (!state.authenticated) return;
  const request = ++state.procedureRequest;
  $("#procedure-status").textContent = "Reading the local procedure catalog…"; $("#procedure-error").hidden = true;
  try {
    const data = await api("/procedures");
    if (request !== state.procedureRequest || !state.authenticated) return;
    state.procedures = data.procedures || []; renderProcedures();
    if (Array.isArray(data.runs)) { state.procedureRuns = data.runs; renderProcedureRuns(); }
    else await loadProcedureRuns();
  } catch (error) {
    if (request === state.procedureRequest && state.authenticated) { $("#procedure-error").textContent = error.message; $("#procedure-error").hidden = false; $("#procedure-status").textContent = "Catalog unavailable. Refresh to retry."; }
  }
}
function renderProcedures() {
  const container = $("#procedure-list"); clear(container);
  $("#procedure-status").textContent = `${state.procedures.length} fixed local ${state.procedures.length === 1 ? "procedure" : "procedures"} · run only when requested · no model inference`;
  if (!state.procedures.length) { container.append(emptyState("workflow", "No fixed procedures available.", "Refresh after this server has been updated.")); return; }
  for (const procedure of state.procedures) {
    const card = node("article", "resource-card procedure-card"), header = node("div", "resource-card-header"), symbol = node("div", "card-icon"); symbol.append(icon("workflow"));
    header.append(symbol, node("span", "tag", procedure.model_calls === 0 ? "0 model calls" : "Fixed local procedure")); card.append(header,node("h2", "", procedure.name),node("p", "", procedure.description));
    const outputs = node("div", "procedure-card-outputs"); outputs.append(node("span", "eyebrow", "EXPECTED OUTPUTS"));
    for (const path of procedure.outputs || []) { const line = node("span"); line.append(icon("file"),node("code", "", path)); outputs.append(line); } card.append(outputs);
    if (procedure.sources?.length) {
      const details = node("details", "procedure-sources"); details.append(node("summary", "", "Background & sources"));
      for (const source of procedure.sources) { const link = survivalSourceLink(source); if (link) details.append(link); } card.append(details);
    }
    card.append(button("Run local", "button-primary procedure-run-button", () => reviewProcedure(procedure), "play")); container.append(card);
  }
  procedureControls();
}
function reviewProcedure(procedure) {
  if (state.procedureRunBusy || !state.authenticated) return;
  state.procedureReview = {procedure,requestId:crypto.randomUUID().replaceAll("-", "")};
  $("#procedure-review-title").textContent = procedure.name; $("#procedure-review-description").textContent = procedure.description;
  $("#procedure-review-request").textContent = state.procedureReview.requestId; $("#procedure-review-error").hidden = true; $("#procedure-review-progress").hidden = true;
  const outputs = $("#procedure-review-outputs"); clear(outputs); for (const path of procedure.outputs || []) outputs.append(node("li", "", path));
  procedureControls(); $("#procedure-review-dialog").showModal(); $("#cancel-procedure-review").focus();
}
async function runProcedure() {
  const review = state.procedureReview;
  if (!review || state.procedureRunBusy || !state.authenticated) return;
  state.procedureRequest += 1; state.procedureRunsRequest += 1;
  state.procedureRunBusy = true; procedureControls(); $("#procedure-review-error").hidden = true;
  state.procedureUncertain = {requestId:review.requestId,name:review.procedure.name};
  let finalStatus = `Request ${review.requestId}: final state not confirmed. Check recent receipts.`;
  $("#procedure-status").textContent = `Running ${review.procedure.name} · request ${review.requestId}`;
  $("#procedure-review-progress").textContent = "Reading local inputs and creating report files…"; $("#procedure-review-progress").hidden = false;
  try {
    const data = await api(`/procedures/${encodeURIComponent(review.procedure.id)}/run`, {method:"POST",body:{request_id:review.requestId}}), receipt = data.run;
    if (!state.authenticated) return;
    if (!receipt || typeof receipt.status !== "string") throw new Error("The server did not return a valid receipt. Check recent runs before requesting another run.");
    state.procedureRequest += 1; state.procedureRunsRequest += 1;
    state.procedureRuns = [receipt,...state.procedureRuns.filter(run => procedureRequestId(run) !== procedureRequestId(receipt))];
    if (receipt.status !== "completed") state.procedureUncertain = {requestId:review.requestId,name:review.procedure.name};
    else if (state.procedureUncertain?.requestId === review.requestId) state.procedureUncertain = null;
    finalStatus = `${review.procedure.name} · ${humanize(receipt.status)} · request ${review.requestId}`;
    renderProcedureRuns(); $("#procedure-review-dialog").close();
    toast(receipt.status === "completed" ? "Local reports created. Review the receipt and output files." : "Procedure returned an unfinished state. Review its receipt before continuing.", receipt.status !== "completed");
    $("#procedure-runs").scrollIntoView({block:"start",behavior:"smooth"});
  } catch (error) {
    if (!state.authenticated) return;
    // A lost response cannot establish whether files were created. Never retry it automatically.
    state.procedureUncertain = {requestId:review.requestId,name:review.procedure.name}; renderProcedureUncertain();
    $("#procedure-review-error").textContent = `${error.message} The request may have started. Check recent receipts using request ${review.requestId} before requesting a new run. This client will not repeat the request automatically.`; $("#procedure-review-error").hidden = false;
    if (!$("#procedure-review-dialog").open) toast("The final procedure state was not confirmed. Check recent receipts before requesting another run.", true);
    state.procedureReview = null;
  } finally { state.procedureRunBusy = false; $("#procedure-review-progress").hidden = true; $("#procedure-status").textContent = finalStatus; procedureControls(); }
}
function renderProcedureUncertain() {
  const container = $("#procedure-uncertain"), uncertain = state.procedureUncertain; clear(container); container.hidden = !uncertain;
  if (!uncertain) return;
  container.append(icon("alert")); const text = node("div"); text.append(node("strong", "", "A previous request needs review"),node("p", "", `${uncertain.name} · request ${uncertain.requestId}. Review its recorded state before requesting another run. No retry was started.`));
  container.append(text,button("Check receipts", "button-secondary", loadProcedureRuns, "refresh"));
}
async function loadProcedureRuns() {
  if (!state.authenticated) return;
  const request = ++state.procedureRunsRequest, control = $("#refresh-procedure-runs"); control.disabled = true;
  try {
    const data = await api("/procedures/runs");
    if (request !== state.procedureRunsRequest || !state.authenticated) return;
    $("#procedure-error").hidden = true;
    state.procedureRuns = data.runs || []; renderProcedureRuns();
  } catch (error) { if (request === state.procedureRunsRequest && state.authenticated) { $("#procedure-error").textContent = error.message; $("#procedure-error").hidden = false; } }
  finally { if (request === state.procedureRunsRequest) control.disabled = false; }
}
function renderProcedureRuns() {
  const container = $("#procedure-runs"); clear(container);
  if (state.procedureUncertain) {
    const confirmed = state.procedureRuns.find(run => procedureRequestId(run) === state.procedureUncertain.requestId);
    if (confirmed?.status === "completed") state.procedureUncertain = null;
  }
  renderProcedureUncertain();
  if (!state.procedureRuns.length) { container.append(node("p", "procedure-empty", "No procedure receipts yet. Review a procedure above to create local reports.")); return; }
  for (const run of state.procedureRuns) {
    const card = node("article", "procedure-receipt"), heading = node("div", "procedure-receipt-heading"), text = node("div"), definition = state.procedures.find(item => item.id === run.procedure_id);
    text.append(node("h3", "", definition?.name || humanize(run.procedure_id)),node("span", "procedure-receipt-id", `Request ${procedureRequestId(run) || "not reported"}`));
    heading.append(text,node("span", `tag${run.status !== "completed" ? " approval-required" : ""}`, humanize(run.status))); card.append(heading);
    const seconds = run.seconds ?? run.wall_seconds, measured = typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 0 ? `${seconds.toFixed(seconds < 1 ? 3 : 2)} seconds recorded` : "Elapsed time not reported";
    card.append(node("p", "procedure-receipt-measurements", `${measured} · ${typeof run.model_calls === "number" ? `${run.model_calls} model calls` : "Model call count not reported"}${run.completed_at || run.created_at ? ` · ${dateLabel(run.completed_at || run.created_at,true)}` : ""}`));
    if (run.status !== "completed") card.append(node("p", "procedure-receipt-warning", "This receipt does not confirm a completed run. Inspect its recorded state; no retry is performed automatically."));
    for (const warning of run.warnings || []) card.append(node("p", "procedure-receipt-warning", typeof warning === "string" ? warning : pretty(warning)));
    if (run.error) card.append(node("p", "procedure-receipt-warning", String(run.error)));
    const artifacts = node("div", "procedure-artifacts");
    for (const artifact of run.artifacts || []) {
      if (!artifact || typeof artifact.path !== "string") continue;
      const row = node("div", "procedure-artifact"), details = node("div"); details.append(node("strong", "", artifact.path),node("small", "", `${bytes(artifact.bytes ?? artifact.size)}${artifact.sha256 ? ` · SHA-256 ${artifact.sha256}` : ""}`));
      const actions = node("div", "procedure-artifact-actions"); actions.append(button("Preview", "button-secondary", () => openArtifact(artifact.path), "eye"),button("Download", "button-secondary", () => downloadArtifact(artifact.path), "arrow-up-right")); row.append(icon("file"),details,actions); artifacts.append(row);
    }
    card.append(artifacts); container.append(card);
  }
}

function updateOCRControls() {
  const ready = Boolean(state.catalog?.ocr?.ready);
  $("#source-ocr").disabled = !ready;
  if (!ready) $("#source-ocr").checked = false;
  $("#source-ocr-help").textContent = ready
    ? "Images use local OCR automatically. PDFs use embedded text unless you select OCR. Review recognized text and numbers; OCR can make mistakes."
    : `PDF text extraction is the default. Local OCR needs setup: ${state.catalog?.ocr?.reason || "inspect the OCR capability in the catalog."}`;
}
async function openUpload(file) {
  if (!file || state.uploading || !state.authenticated) return;
  state.uploadFile = file;
  $("#knowledge-file-input").value = "";
  $("#upload-name").textContent = file.name;
  $("#upload-size").textContent = bytes(file.size);
  $("#upload-ocr").checked = false;
  $("#upload-error").hidden = true; $("#upload-progress").hidden = true;
  if (!state.catalog) { try { await fetchCatalog(); } catch { /* API import still reports its exact availability. */ } }
  if (!state.authenticated) return;
  updateUploadMode();
  $("#upload-dialog").showModal();
  $("#import-upload").focus();
}
function updateUploadMode() {
  const file = state.uploadFile;
  if (!file) return;
  const extension = file.name.split(".").pop().toLowerCase();
  const pdf = extension === "pdf";
  const image = ["png", "jpg", "jpeg"].includes(extension);
  const ready = Boolean(state.catalog?.ocr?.ready);
  $("#upload-ocr").disabled = !pdf || !ready || state.uploading;
  if (!pdf || !ready) $("#upload-ocr").checked = false;
  let description = "This file uses its local text/document reader. Importing indexes reference text without enabling tools or executing source instructions.";
  if (image) description = ready ? "This image uses local CPU OCR automatically. No image or text is sent to an online service." : "Image imports need the pinned local OCR runtime. Inspect the OCR capability for setup details.";
  if (pdf) description = $("#upload-ocr").checked
    ? "This PDF will be rendered and recognized with local CPU OCR, up to 10 pages. Processing can take up to 90 seconds."
    : `This PDF uses embedded text extraction.${ready ? " Select OCR above if its pages are scanned images." : " Scanned pages need the local OCR capability to be provisioned."}`;
  $("#upload-mode-help").textContent = description;
  $("#import-upload").disabled = state.uploading || (image && !ready);
}
function syncCapabilityTab() {
  const mcp = state.capabilityTab === "mcp";
  $("#catalog-panel").hidden = mcp;
  $("#mcp-panel").hidden = !mcp;
  for (const tab of $$('[data-capability-tab]')) {
    const selected = tab.dataset.capabilityTab === state.capabilityTab;
    tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
  }
}
async function setCapabilityTab(tab) {
  state.capabilityTab = tab === "mcp" ? "mcp" : "catalog";
  syncCapabilityTab();
  if (state.view !== "connectors") { await showView("connectors"); return; }
  if (state.authenticated && state.capabilityTab === "mcp") await loadMCP();
}
async function loadMCP() {
  const request = ++state.mcpRequest;
  const container = $("#mcp-server-list");
  container.replaceChildren(node("div", "loading-state", "Reading owner-managed MCP configuration…"));
  try {
    const data = await api("/mcp");
    if (request !== state.mcpRequest || !state.authenticated) return;
    state.mcp = data; renderMCP();
  } catch (error) {
    if (request === state.mcpRequest) { container.replaceChildren(node("p", "inline-error", error.message)); $("#mcp-status").textContent = "MCP configuration could not be loaded."; }
  }
}
function renderMCP() {
  if (!state.mcp) return;
  const {status = {}, servers = [], tools = []} = state.mcp;
  $("#mcp-status").textContent = status.ready
    ? `Local client available · ${tools.length} selected ${tools.length === 1 ? "tool" : "tools"}. Servers are contacted only during discovery or tool use.`
    : status.error || `Local MCP client needs setup${status.required_version ? ` (required version ${status.required_version})` : ""}. Registration does not test server availability.`;
  $("#add-mcp-server").disabled = state.mcpBusy || servers.length >= 4;
  const container = $("#mcp-server-list"); clear(container);
  if (!servers.length) {
    container.append(emptyState("plug", "No local tool servers registered.", "Register an already-running loopback HTTP server, then review individual tools before enabling them.", {label:"Register a local server", fn:openMCPServer}));
    return;
  }
  for (const server of servers) {
    const card = node("article", "resource-card mcp-server-card");
    const header = node("div", "resource-card-header");
    const symbol = node("div", "card-icon"); symbol.append(icon("plug"));
    const remove = iconButton("trash", `Remove ${server.id} registration`, () => removeMCPServer(server)); remove.disabled = state.mcpBusy;
    header.append(symbol, remove); card.append(header, node("h2", "", server.id), node("p", "mcp-endpoint", server.url));
    const selected = Array.isArray(server.tools) ? server.tools : [];
    const tags = node("div", "tag-row"); tags.append(node("span", "tag", server.mode === "legacy" ? "Legacy mode" : "Modern mode"), node("span", "tag", `${selected.length} selected`)); card.append(tags);
    card.append(node("p", "", selected.length ? "Configured allowlist. Availability and identity are checked when a tool is used." : "Registered only. No tools from this server are available to missions."));
    if (selected.length) {
      const list = node("div", "mcp-enabled-tools");
      for (const entry of selected) {
        const row = node("div"); row.append(node("strong", "", entry.metadata?.name || "Unnamed tool"), node("span", entry.read_only ? "" : "mcp-policy-approval", entry.read_only ? "Owner marked read-only" : "Approval for each call")); list.append(row);
      }
      card.append(list);
    }
    const details = node("details", "tool-details"); details.append(node("summary", "", "Inspect registration"));
    details.append(node("pre", "", pretty({url:server.url, mode:server.mode, credential_references:server.headers || {}, identity:server.identity || null}))); card.append(details);
    const discover = button("Discover & review tools", "button-secondary", () => discoverMCP(server), "eye");
    discover.disabled = state.mcpBusy || !status.ready;
    if (!status.ready) discover.title = "Provision the pinned local MCP client before discovery";
    discover.classList.add("mcp-discover"); card.append(discover); container.append(card);
  }
}
function openMCPServer() {
  if (state.mcpBusy || !state.authenticated) return;
  $("#mcp-server-form").reset(); $("#mcp-server-headers").value = "{}";
  $("#mcp-server-error").hidden = true; $("#save-mcp-server").disabled = false;
  $("#mcp-server-dialog").showModal(); $("#mcp-server-id").focus();
}
function validateMCPRegistration(id, rawURL, mode, rawHeaders) {
  if (!/^[a-z][a-z0-9_]{0,23}$/.test(id)) throw new Error("Choose a lowercase server ID of 1–24 letters, digits or underscores, starting with a letter.");
  let url;
  try { url = new URL(rawURL); } catch { throw new Error("Provide a full loopback HTTP URL, such as http://127.0.0.1:9000/mcp."); }
  const explicitEndpoint = /^http:\/\/(?:127\.0\.0\.1|\[::1\]):[0-9]+\//.test(rawURL);
  if (/[\x00-\x20\\]/.test(rawURL) || !explicitEndpoint || url.protocol !== "http:" || !["127.0.0.1", "[::1]"].includes(url.hostname) || url.username || url.password || url.search || url.hash || url.pathname.startsWith("//")) throw new Error("Use literal 127.0.0.1 or [::1] with HTTP, an explicit port and a path; omit credentials, query strings and fragments.");
  if (!["modern", "legacy"].includes(mode)) throw new Error("Choose an explicit protocol mode.");
  let headers;
  try { headers = JSON.parse(rawHeaders || "{}"); } catch { throw new Error("Credential references must be valid JSON. Leave {} when no credentials are required."); }
  if (!headers || typeof headers !== "object" || Array.isArray(headers) || Object.keys(headers).length > 4) throw new Error("Credential references must be a small JSON object, or {}.");
  const seen = new Set();
  for (const [name, ref] of Object.entries(headers)) {
    const key = name.toLowerCase();
    if (!["authorization", "x-api-key"].includes(key) || seen.has(key)) throw new Error("Use only one Authorization or X-API-Key header reference, without duplicate names.");
    seen.add(key);
    if (!ref || typeof ref !== "object" || Array.isArray(ref) || Object.keys(ref).some(field => !["env", "prefix"].includes(field)) || typeof ref.env !== "string" || !/^ARIADNE_MCP_[A-Z0-9_]{1,80}$/.test(ref.env) || (Object.hasOwn(ref, "prefix") && !["", "Bearer "].includes(ref.prefix))) throw new Error("Each credential must reference an existing ARIADNE_MCP_ environment variable, with an optional empty or 'Bearer ' prefix. Never paste a secret value.");
  }
  return {id, url:rawURL, mode, headers};
}
async function saveMCPServer(event) {
  event.preventDefault(); if (state.mcpBusy) return;
  const error = $("#mcp-server-error"); error.hidden = true;
  try {
    const body = validateMCPRegistration($("#mcp-server-id").value.trim(), $("#mcp-server-url").value.trim(), $("#mcp-server-mode").value, $("#mcp-server-headers").value);
    state.mcpBusy = true; $("#save-mcp-server").disabled = true;
    await api("/mcp/servers", {method:"POST", body});
    if (!state.authenticated) return;
    $("#mcp-server-dialog").close(); await loadMCP();
    toast("Local server registered with no tools enabled. Discover and review tools to continue.");
  } catch (failure) { error.textContent = failure.message; error.hidden = false; }
  finally { state.mcpBusy = false; $("#save-mcp-server").disabled = false; renderMCP(); }
}
async function removeMCPServer(server) {
  if (state.mcpBusy || !await confirmAction("Remove this local server?", `“${server.id}” and its selected tool allowlist will be removed. This does not stop or uninstall its local service.`, "Remove registration")) return;
  state.mcpBusy = true; renderMCP();
  try { await api(`/mcp/servers/${encodeURIComponent(server.id)}`, {method:"DELETE"}); await loadMCP(); await fetchCatalog(); renderCapabilities(); toast("Local tool server registration removed."); }
  catch (error) { toast(error.message, true); }
  finally { state.mcpBusy = false; renderMCP(); }
}
async function discoverMCP(server) {
  if (state.mcpBusy || !state.authenticated) return;
  const request = ++state.mcpRequest;
  state.mcpBusy = true; renderMCP();
  $("#mcp-status").textContent = `Discovering tools from ${server.id}… No tool is being selected or invoked.`;
  try {
    const snapshot = await api(`/mcp/${encodeURIComponent(server.id)}/discover`, {method:"POST", body:{}});
    if (request !== state.mcpRequest || !state.authenticated) return;
    state.mcpReview = {server, snapshot, entries:[]}; renderMCPReview();
    $("#mcp-review-dialog").showModal(); $("#mcp-review-title").scrollIntoView({block:"nearest"});
  } catch (error) { if (state.authenticated) toast(error.message, true); }
  finally { state.mcpBusy = false; renderMCP(); updateMCPSelection(); }
}
function renderMCPReview() {
  const review = state.mcpReview;
  if (!review) return;
  $("#mcp-review-title").textContent = `Review tools · ${review.server.id}`;
  $("#mcp-review-error").hidden = true;
  $("#mcp-review-identity").textContent = pretty({identity:review.snapshot.identity, snapshot_sha256:review.snapshot.snapshot_sha256});
  const container = $("#mcp-discovered-tools"); clear(container);
  review.entries = [];
  for (const [index, tool] of (review.snapshot.tools || []).entries()) {
    const metadata = tool.metadata || {};
    const supported = tool.supported === true;
    const card = node("article", `mcp-discovered-tool${supported ? "" : " unsupported"}`);
    const heading = node("div", "mcp-tool-heading");
    const checkbox = node("input"); checkbox.type = "checkbox"; checkbox.id = `mcp-select-${index}`; checkbox.checked = false; checkbox.disabled = !supported;
    const label = node("label", "", metadata.name || "Unnamed tool"); label.htmlFor = checkbox.id;
    heading.append(checkbox, label, node("span", `tag${supported ? "" : " approval-required"}`, supported ? "Supported" : "Unavailable"));
    card.append(heading);
    if (metadata.description) card.append(node("p", "mcp-tool-description", metadata.description));
    if (!supported) card.append(node("p", "mcp-schema-reason", tool.reason || "This schema is outside the supported subset."));
    const details = node("details", "tool-details"); details.append(node("summary", "", "Inspect complete metadata & input schema"), node("pre", "", pretty(metadata))); card.append(details);
    const policy = node("div", "mcp-policy-field");
    const policyLabel = node("label", "", "Owner policy"); policyLabel.htmlFor = `mcp-policy-${index}`;
    const select = node("select"); select.id = policyLabel.htmlFor; select.disabled = true;
    const approval = node("option", "", "Approval for each call"); approval.value = "approval";
    const readOnly = node("option", "", "Owner marks read-only"); readOnly.value = "read_only";
    select.append(approval, readOnly); select.value = "approval";
    policy.append(policyLabel, select); card.append(policy);
    const entry = {name:metadata.name, checkbox, select, supported}; review.entries.push(entry);
    checkbox.addEventListener("change", () => { select.disabled = !checkbox.checked || !supported; updateMCPSelection(); });
    select.addEventListener("change", updateMCPSelection);
    container.append(card);
  }
  if (!review.entries.length) container.append(emptyState("plug", "No tools discovered.", "This server reported no tool definitions. Applying an empty selection clears any previous allowlist for this server."));
  updateMCPSelection();
}
function updateMCPSelection() {
  const review = state.mcpReview;
  if (!review) return;
  const selected = review.entries.filter(entry => entry.supported && entry.checkbox.checked);
  const others = (state.mcp?.servers || []).filter(server => server.id !== review.server.id).reduce((total, server) => total + (server.tools || []).length, 0);
  const remaining = Math.max(0, 16 - others);
  $("#mcp-selection-count").textContent = `${selected.length} selected · ${remaining} available slots`;
  $("#apply-mcp-tools").textContent = `Apply ${selected.length} selected ${selected.length === 1 ? "tool" : "tools"}`;
  $("#apply-mcp-tools").disabled = state.mcpBusy || selected.length > remaining || !review.snapshot.snapshot_sha256;
}
async function applyMCPTools() {
  const review = state.mcpReview;
  if (!review || state.mcpBusy || $("#apply-mcp-tools").disabled) return;
  const selections = review.entries.filter(entry => entry.supported && entry.checkbox.checked).map(entry => ({name:entry.name, read_only:entry.select.value === "read_only"}));
  state.mcpBusy = true; updateMCPSelection(); $("#mcp-review-error").hidden = true;
  try {
    await api(`/mcp/${encodeURIComponent(review.server.id)}/select`, {method:"POST", body:{snapshot_sha256:review.snapshot.snapshot_sha256, selections}});
    if (!state.authenticated) return;
    $("#mcp-review-dialog").close(); await loadMCP(); await fetchCatalog(); renderCapabilities();
    toast(selections.length ? `${selections.length} local ${selections.length === 1 ? "tool selected" : "tools selected"} with the owner policies you reviewed.` : "This server's tool allowlist is now empty.");
  } catch (error) { $("#mcp-review-error").textContent = error.message; $("#mcp-review-error").hidden = false; }
  finally { state.mcpBusy = false; renderMCP(); updateMCPSelection(); }
}

// Survival records use revision checks so a second tab cannot silently replace a plan.
const survivalCategories = ["water", "food", "power", "hygiene", "medical", "tools", "seeds", "materials", "communications", "other"];
const survivalTaskStatuses = ["planned", "in_progress", "blocked", "done"];
function survivalId() { return crypto.randomUUID().replaceAll("-", ""); }
function survivalNumber(value) {
  const number = Number(value);
  return value === null || value === undefined || !Number.isFinite(number) ? "—" : number.toLocaleString(undefined, {maximumFractionDigits:2});
}
function survivalError(message = "") { $("#survival-error").textContent = message; $("#survival-error").hidden = !message; }
function setSurvivalTab(name) {
  if (!["overview", "supplies", "projects", "library"].includes(name)) return;
  state.survivalTab = name;
  for (const tab of $$('[data-survival-tab]')) {
    const selected = tab.dataset.survivalTab === name;
    tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
    $(`#survival-${tab.dataset.survivalTab}-panel`).hidden = !selected;
  }
  if (name === "library" && state.authenticated) { loadSurvivalLibrary(); loadSurvivalBooks(); }
}
function survivalControls() {
  for (const id of ["edit-survival-profile", "add-survival-supply", "add-survival-task", "seed-survival-roadmap"]) $(`#${id}`).disabled = state.survivalBusy || !state.survival || !state.authenticated;
  for (const control of $$("#survival-supply-list button, #survival-task-list button")) control.disabled = state.survivalBusy || !state.authenticated;
  $("#refresh-survival").disabled = state.survivalBusy;
}
async function loadSurvival() {
  const request = ++state.survivalRequest;
  $("#survival-status").textContent = "Reading your local plan…"; survivalError();
  try {
    const result = await api("/survival");
    if (request !== state.survivalRequest || !state.authenticated) return;
    state.survival = result; renderSurvival();
    if (state.survivalTab === "library") await Promise.allSettled([loadSurvivalLibrary(), loadSurvivalBooks()]);
  } catch (error) {
    if (request === state.survivalRequest && state.authenticated) { survivalError(error.message); $("#survival-status").textContent = "Plan unavailable. Refresh to retry."; }
  } finally { survivalControls(); }
}
function renderSurvival() {
  const data = state.survival;
  if (!data) return;
  const profile = data.profile || {}, summary = data.summary || {};
  $("#survival-status").textContent = `LOCAL PLAN / ${profile.people || 1} ${(profile.people || 1) === 1 ? "person" : "people"} / ${profile.days || 14} days / revision ${data.revision ?? "—"}${summary.as_of ? ` / reviewed ${dateLabel(summary.as_of)}` : ""}`;
  const metrics = $("#survival-summary"); clear(metrics);
  for (const [key, title, unit, availableKey, requiredKey, shortfallKey, symbol] of [
    ["water", "Stored water", "L", "available_liters", "required_liters", "shortfall_liters", "01"],
    ["food", "Recorded food energy", "kcal", "available_kcal", "required_kcal", "shortfall_kcal", "02"],
    ["power", "Available energy", "Wh", "available_wh", "required_wh", "shortfall_wh", "03"],
  ]) {
    const item = summary[key] || {}, card = node("article", "survival-metric");
    const heading = node("div", "survival-metric-heading"); heading.append(node("span", "eyebrow", title), node("span", "survival-sector", symbol));
    const value = node("div", "survival-metric-value"); value.append(node("strong", "", survivalNumber(item[availableKey])), node("span", "", unit));
    const required = Number(item[requiredKey]), available = Number(item[availableKey]);
    const meter = node("div", "survival-meter"), fill = node("span");
    fill.style.width = `${Number.isFinite(required) && required > 0 && Number.isFinite(available) ? Math.min(100, Math.max(0, available / required * 100)) : 0}%`; meter.append(fill); meter.setAttribute("aria-hidden", "true");
    const deficit = Number(item[shortfallKey]);
    card.append(heading, value, meter, node("p", "", `${survivalNumber(item[requiredKey])} ${unit} planned · ${item.coverage_days === null || item.coverage_days === undefined ? "Coverage unavailable" : `${survivalNumber(item.coverage_days)} days estimated`}`), node("span", `survival-metric-state${deficit > 0 ? " warning" : ""}`, item[requiredKey] === null || !required ? "Add demand to estimate coverage" : deficit > 0 ? `${survivalNumber(item[shortfallKey])} ${unit} to plan` : "Recorded quantity meets this estimate"));
    metrics.append(card);
  }
  facts($("#survival-profile-facts"), [
    ["People / planning horizon", `${profile.people || 1} / ${profile.days || 14} days`],
    ["Water assumption", `${survivalNumber(profile.water_liters_per_person_per_day)} L / person / day`],
    ["Food assumption", `${survivalNumber(profile.food_kcal_per_person_per_day)} kcal / person / day`],
    ["Food basis", profile.food_basis || "Planning estimate; individual needs vary"],
    ["Battery / usable fraction / efficiency", `${survivalNumber(profile.battery_wh)} Wh / ${survivalNumber(Number(profile.usable_fraction) * 100)}% / ${survivalNumber(Number(profile.inverter_efficiency) * 100)}%`],
    ["Recorded electrical loads", String((profile.loads || []).length)],
  ]);
  const attention = $("#survival-attention"); clear(attention);
  const alerts = [];
  if (summary.expired_supply_ids?.length) alerts.push(`${summary.expired_supply_ids.length} expired supply ${summary.expired_supply_ids.length === 1 ? "entry is" : "entries are"} excluded from coverage.`);
  if (summary.unknown_expiry_supply_ids?.length) alerts.push(`${summary.unknown_expiry_supply_ids.length} supply ${summary.unknown_expiry_supply_ids.length === 1 ? "entry has" : "entries have"} no recorded expiry. Review condition and storage.`);
  if (summary.blocked_tasks?.length || Number(summary.blocked_tasks) > 0) alerts.push(`${Array.isArray(summary.blocked_tasks) ? summary.blocked_tasks.length : summary.blocked_tasks} projects have unresolved prerequisites or are blocked.`);
  for (const warning of summary.warnings || []) alerts.push(typeof warning === "string" ? warning : pretty(warning));
  if (alerts.length) { const list = node("ul", "survival-alerts"); for (const alert of alerts) list.append(node("li", "", alert)); attention.append(list); }
  else attention.append(node("p", "survival-footnote", "No recorded expiry or dependency flags. Verify your physical reserves and the assumptions above."));
  attention.append(button("Review supplies", "button-secondary", () => setSurvivalTab("supplies"), "arrow-right"), button("Review projects", "button-secondary", () => setSurvivalTab("projects"), "workflow"));
  if (summary.assumptions?.length) { const details = node("details", "survival-assumptions"); details.append(node("summary", "", "Calculation assumptions")); const list = node("ul"); for (const assumption of summary.assumptions) list.append(node("li", "", typeof assumption === "string" ? assumption : pretty(assumption))); details.append(list); attention.append(details); }
  const book = data.library?.book || {}, bookPanel = $("#survival-book"); clear(bookPanel);
  const bookIntro = node("div"); bookIntro.append(node("span", "eyebrow", "YOUR REBUILDING REFERENCE"), node("h2", "", book.title || "The Knowledge: How to Rebuild Our World from Scratch"), node("p", "", book.notice || "The commercial book is not bundled or reproduced. Add your own legally obtained copy in the Survival library to make its reference text searchable alongside your local plans."));
  const bookActions = node("div", "survival-book-actions"); bookActions.append(node("span", "tag approval-required", book.status === "owner_copy_required" || !book.status ? "Owner copy required" : humanize(book.status)), button("Import your copy", "button-secondary", () => { setSurvivalTab("library"); $("#survival-book-import").open = true; }, "upload"));
  bookPanel.append(bookIntro, bookActions);
  renderSurvivalSupplies(); renderSurvivalTasks(); survivalControls();
  const select = $("#survival-topic"), selected = select.value; select.replaceChildren(node("option", "", "All topics")); select.firstChild.value = "";
  for (const topic of data.library?.topics || []) { const option = node("option", "", typeof topic === "string" ? humanize(topic) : topic.title || topic.label || humanize(topic.id)); option.value = typeof topic === "string" ? topic : topic.id; select.append(option); } select.value = selected;
}
function renderSurvivalSupplies() {
  const container = $("#survival-supply-list"), supplies = state.survival?.supplies || []; clear(container);
  if (!supplies.length) { container.append(emptyState("shield", "Begin with what is on hand.", "Add measured quantities. Your water, food, and energy estimates update from these records.", {label:"Add first supply", fn:() => openSurvivalEditor("supply")})); return; }
  const wrapper = node("div", "survival-table-wrap"), table = node("table", "survival-table"), head = node("thead"), headings = node("tr");
  const caption = node("caption", "sr-only", "Stored supplies and recorded expiry dates");
  for (const title of ["Supply", "Quantity", "Location", "Expiry / review", "Actions"]) { const th = node("th", "", title); th.scope = "col"; headings.append(th); } head.append(headings); table.append(caption, head);
  const body = node("tbody");
  for (const supply of supplies) {
    const row = node("tr"), name = node("td"); name.append(node("strong", "", supply.name), node("small", "", humanize(supply.category))); if (supply.notes) { const details = node("details"); details.append(node("summary", "", "Notes"), node("p", "", supply.notes)); name.append(details); }
    const expiry = node("td"); expiry.append(node("span", `tag${supply.expiry_status !== "current" ? " approval-required" : ""}`, supply.expiry_status === "expired" ? "Expired · excluded" : supply.expires || "Expiry unknown"));
    const actions = node("td", "survival-row-actions"); actions.append(button("Edit", "button-secondary", () => openSurvivalEditor("supply", supply)), iconButton("trash", `Remove ${supply.name}`, () => deleteSurvivalRecord("supply", supply)));
    row.append(name, node("td", "survival-quantity", `${survivalNumber(supply.quantity)} ${supply.unit}`), node("td", "", supply.location || "Not recorded"), expiry, actions); body.append(row);
  }
  table.append(body); wrapper.append(table); container.append(wrapper);
}
function renderSurvivalTasks() {
  const container = $("#survival-task-list"), tasks = state.survival?.tasks || []; clear(container);
  if (!tasks.length) { container.append(emptyState("workflow", "Build a practical sequence.", "Track projects and their dependencies, from household continuity to long-term recovery.", {label:"Add first project", fn:() => openSurvivalEditor("task")})); return; }
  const byId = new Map(tasks.map(task => [task.id, task]));
  for (const task of tasks) {
    const card = node("article", "survival-project survival-panel");
    const heading = node("div", "survival-project-top"); heading.append(node("span", "eyebrow", humanize(task.domain)), node("span", `tag${task.status === "blocked" || task.blocked_by?.length ? " approval-required" : ""}`, task.blocked_by?.length && task.status !== "done" ? "Prerequisites pending" : humanize(task.status))); card.append(heading, node("h2", "", task.title));
    if (task.notes) card.append(node("p", "", task.notes));
    if (task.depends_on?.length) { const dependency = node("div", "survival-dependencies"); dependency.append(node("span", "eyebrow", "PREREQUISITES")); for (const id of task.depends_on) { const prerequisite = byId.get(id); const label = node("span", prerequisite?.status === "done" ? "completed" : "warning"); label.append(icon(prerequisite?.status === "done" ? "check" : "clock"), document.createTextNode(prerequisite?.title || `Missing project: ${id}`)); dependency.append(label); } card.append(dependency); }
    const actions = node("div", "survival-project-actions"); actions.append(button("Edit project", "button-secondary", () => openSurvivalEditor("task", task)), iconButton("trash", `Remove ${task.title}`, () => deleteSurvivalRecord("task", task))); card.append(actions); container.append(card);
  }
}
function survivalField(container, definition, value) {
  const wrapper = node("div", `editor-field${definition.wide ? " survival-wide" : ""}`), label = node("label", "", definition.label), input = node(definition.options ? "select" : definition.textarea ? "textarea" : "input");
  label.htmlFor = `survival-input-${definition.name}`; input.id = label.htmlFor; input.name = definition.name; input.required = definition.required !== false;
  if (definition.options) for (const choice of definition.options) { const option = node("option", "", typeof choice === "string" ? humanize(choice) : choice.label); option.value = typeof choice === "string" ? choice : choice.value; input.append(option); }
  else if (!definition.textarea) input.type = definition.type || "text";
  if (definition.min !== undefined) input.min = String(definition.min); if (definition.max !== undefined) input.max = String(definition.max); if (definition.step) input.step = definition.step;
  if (definition.maxLength) input.maxLength = definition.maxLength; else if (!definition.options && !["number", "date"].includes(input.type)) input.maxLength = definition.textarea ? 4000 : 200;
  if (definition.placeholder) input.placeholder = definition.placeholder; input.value = value === undefined || value === null ? "" : String(value);
  wrapper.append(label, input); if (definition.help) { const help = node("p", "field-help", definition.help); help.id = `${input.id}-help`; wrapper.append(help); input.setAttribute("aria-describedby", help.id); } container.append(wrapper); return input;
}
function survivalDomainChoices(current = "general") {
  const choices = new Map([["general", "General"]]);
  for (const topic of state.survival?.library?.topics || []) {
    const id = typeof topic === "string" ? topic : topic.id;
    if (typeof id === "string" && id) choices.set(id, typeof topic === "string" ? humanize(topic) : topic.title || topic.label || humanize(id));
  }
  if (current && !choices.has(current)) choices.set(current, humanize(current));
  return [...choices].map(([value,label]) => ({value,label}));
}
function openSurvivalEditor(type, record = {}) {
  if (!state.survival || state.survivalBusy || !state.authenticated) return;
  const fields = $("#survival-editor-fields"); clear(fields); $("#survival-editor-error").hidden = true;
  state.survivalEditor = {type, record, recordId:record.id || survivalId(), revision:state.survival.revision, requestId:survivalId()};
  $("#survival-editor-title").textContent = type === "profile" ? "Household & energy assumptions" : `${record.id ? "Edit" : "Add"} ${type === "supply" ? "supply" : "project"}`;
  if (type === "profile") {
    const profile = state.survival.profile || {};
    for (const definition of [
      {name:"people",label:"People",type:"number",min:1,max:10000,step:"1"}, {name:"days",label:"Planning days",type:"number",min:1,max:36500,step:"1"},
      {name:"water_liters_per_person_per_day",label:"Water · liters / person / day",type:"number",min:"0.001",max:1000,step:"any"},
      {name:"food_kcal_per_person_per_day",label:"Food · kcal / person / day",type:"number",min:1,max:20000,step:"1"},
      {name:"food_basis",label:"Basis for food assumption",wide:true,maxLength:200,help:"A planning estimate, not a dietary recommendation. Record the basis and household needs."},
      {name:"battery_wh",label:"Battery capacity · Wh",type:"number",min:0,max:"1000000000000",step:"any"},
      {name:"usable_fraction",label:"Usable fraction · 0–1",type:"number",min:"0.000001",max:1,step:"any"},
      {name:"inverter_efficiency",label:"Conversion efficiency · 0–1",type:"number",min:"0.000001",max:1,step:"any"},
    ]) survivalField(fields, definition, profile[definition.name]);
    const loads = node("div", "survival-loads survival-wide"); loads.append(node("h3", "", "Daily electrical loads"), node("p", "field-help", "Enter rated watts, daily hours, and device count. Nameplate power does not model startup surges, battery chemistry, fuel, or weather."));
    const list = node("div", "survival-load-list"); loads.append(list, button("Add load", "button-secondary", () => addSurvivalLoad(list), "plus")); fields.append(loads);
    for (const load of profile.loads || []) addSurvivalLoad(list, load);
  } else if (type === "supply") {
    const definitions = [
      {name:"name",label:"Supply name",wide:true,maxLength:200,placeholder:"Stored drinking water"},
      {name:"category",label:"Category",options:survivalCategories},
      {name:"quantity",label:"Quantity",type:"number",min:0,max:"1000000000000",step:"any"},
      {name:"unit",label:"Unit",options:[{value:"liter",label:"Liters · water"},{value:"kcal",label:"kcal · food energy"},{value:"wh",label:"Wh · stored energy"},{value:"count",label:"Count"},{value:"kg",label:"Kilograms"},{value:"meter",label:"Meters"}]},
      {name:"expires",label:"Expiry date",type:"date",required:false,help:"Leave blank if unknown. This records the label; it cannot establish an item's safety."},
      {name:"location",label:"Storage location",wide:true,required:false,maxLength:200},
      {name:"notes",label:"Notes & review conditions",textarea:true,wide:true,required:false,maxLength:1000},
    ]; for (const definition of definitions) survivalField(fields, definition, record[definition.name] ?? ({category:"water",quantity:"0",unit:"liter"}[definition.name]));
  } else {
    for (const definition of [{name:"title",label:"Project title",wide:true,maxLength:200}, {name:"domain",label:"Domain",options:survivalDomainChoices(record.domain)}, {name:"status",label:"Status",options:survivalTaskStatuses}, {name:"notes",label:"Notes / completion criteria",textarea:true,wide:true,required:false,maxLength:1000}]) survivalField(fields, definition, record[definition.name] ?? ({domain:"general",status:"planned"}[definition.name]));
    const dependency = node("fieldset", "survival-wide survival-dependency-picker"); dependency.append(node("legend", "", "Prerequisite projects"), node("p", "field-help", "Unfinished prerequisites are flagged in the plan. Missing dependencies and dependency cycles are rejected. Completion remains your reported status."));
    for (const task of state.survival.tasks || []) if (task.id !== record.id) { const label = node("label", "checkbox-label"), checkbox = node("input"); checkbox.type = "checkbox"; checkbox.name = "depends_on"; checkbox.value = task.id; checkbox.checked = (record.depends_on || []).includes(task.id); label.append(checkbox, node("span", "", task.title)); dependency.append(label); }
    if (!(state.survival.tasks || []).some(task => task.id !== record.id)) dependency.append(node("p", "field-help", "No other projects yet.")); fields.append(dependency);
  }
  $("#save-survival-editor").disabled = false; $("#survival-editor-dialog").showModal(); $("input,select,textarea", fields)?.focus();
}
function addSurvivalLoad(container, load = {}) {
  const row = node("div", "survival-load-row"), suffix = survivalId();
  for (const [name, label, value, min, max, step] of [["name","Device",load.name || "",null,null,null],["watts","Watts",load.watts ?? "0",0,"1000000000","any"],["hours_per_day","Hours / day",load.hours_per_day ?? "0",0,24,"any"],["count","Count",load.count ?? 1,1,10000,"1"]]) {
    const wrapper = node("div"), caption = node("label", "", label), input = node("input"); input.type = name === "name" ? "text" : "number"; input.dataset.loadField = name; input.id = `survival-load-${suffix}-${name}`; caption.htmlFor = input.id; input.value = String(value); input.required = true;
    if (min !== null) { input.min = String(min); input.max = String(max); input.step = step; } else input.maxLength = 120;
    wrapper.append(caption,input); row.append(wrapper);
  }
  row.append(iconButton("trash", "Remove this electrical load", () => row.remove())); container.append(row);
}
async function survivalMutation(action, record, revision, requestId) {
  state.survivalBusy = true; survivalControls();
  try {
    const result = await api("/survival", {method:"POST",body:{action,record,request_id:requestId,expected_revision:revision}});
    if (!state.authenticated) return false;
    state.survival = result; renderSurvival(); survivalError(); return true;
  } catch (error) {
    if (error.status === 409) { await loadSurvival(); throw new Error("The plan changed in another request. Close this editor, review the refreshed plan, and reopen it before saving."); }
    throw error;
  } finally { state.survivalBusy = false; survivalControls(); }
}
async function saveSurvivalEditor(event) {
  event.preventDefault(); const editor = state.survivalEditor;
  if (!editor || state.survivalBusy || !state.authenticated) return;
  const error = $("#survival-editor-error"), form = new FormData(event.currentTarget), record = {}; error.hidden = true; $("#save-survival-editor").disabled = true;
  try {
    if (editor.type === "profile") {
      Object.assign(record, state.survival.profile);
      for (const key of ["people","days","food_kcal_per_person_per_day"]) record[key] = Number(form.get(key));
      for (const key of ["water_liters_per_person_per_day","food_basis","battery_wh","usable_fraction","inverter_efficiency"]) record[key] = String(form.get(key) || "").trim();
      record.loads = $$(".survival-load-row", $("#survival-editor-fields")).map(row => { const load = {}; for (const input of $$("[data-load-field]", row)) load[input.dataset.loadField] = input.dataset.loadField === "count" ? Number(input.value) : input.value.trim(); return load; });
    } else {
      record.id = editor.recordId;
      const names = editor.type === "supply" ? ["name","category","quantity","unit","expires","location","notes"] : ["title","domain","status","notes"];
      for (const name of names) record[name] = String(form.get(name) || "").trim();
      if (editor.type === "task") record.depends_on = form.getAll("depends_on").map(String);
    }
    if (await survivalMutation(editor.type === "profile" ? "save_profile" : `upsert_${editor.type}`, record, editor.revision, editor.requestId)) { $("#survival-editor-dialog").close(); toast("Survival plan saved locally."); }
  } catch (failure) { error.textContent = failure.message; error.hidden = false; }
  finally { $("#save-survival-editor").disabled = false; }
}
async function deleteSurvivalRecord(type, record) {
  if (state.survivalBusy || !state.authenticated) return;
  const revision = state.survival.revision;
  if (!await confirmAction(`Remove this ${type === "supply" ? "supply" : "project"}?`, `${record.name || record.title}\nThis removes the local planning record. Dependent projects must be updated before a prerequisite can be removed.`)) return;
  try { if (await survivalMutation(`delete_${type}`, {id:record.id}, revision, survivalId())) toast("Planning record removed."); }
  catch (error) { survivalError(error.message); }
}
async function seedSurvivalRoadmap() {
  if (state.survivalBusy || !state.survival || !state.authenticated) return;
  const revision = state.survival.revision;
  if (!await confirmAction("Add the rebuilding roadmap?", "Add a dependency-linked set of starter projects to this local plan. These are planning records; no equipment or physical work is started. Existing projects are preserved.", "Add roadmap", "Not now")) return;
  try { if (await survivalMutation("seed_roadmap", {}, revision, survivalId())) toast("Rebuilding roadmap added to your local plan."); }
  catch (error) { survivalError(error.message); }
}
function survivalSourceLink(source) {
  const urlText = typeof source === "string" ? source : source.url;
  try { const url = new URL(urlText); if (!["https:","http:"].includes(url.protocol) || url.username || url.password) return null; const link = node("a", "survival-source", typeof source === "string" ? url.hostname : source.title || source.publisher || url.hostname); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; return link; } catch { return null; }
}
async function loadSurvivalLibrary() {
  if (!state.authenticated) return;
  const request = ++state.survivalLibraryRequest, container = $("#survival-library-list");
  clear($("#survival-book-results"));
  container.replaceChildren(node("div", "loading-state", "Searching the local reference catalog…"));
  try {
    const data = await api(`/survival/library?q=${encodeURIComponent($("#survival-search").value.trim())}&topic=${encodeURIComponent($("#survival-topic").value)}`);
    if (request !== state.survivalLibraryRequest || !state.authenticated) return;
    clear(container); renderSurvivalBookResults(data.book_results || [], data.reference_results || [], data.resources || []);
    const cards = data.resources || data.cards || [];
    if (!cards.length) { container.append(emptyState("book", "No matching references.", "Try another topic or a shorter search.")); return; }
    for (const resource of cards) {
      const card = node("article", "survival-panel survival-reference"); card.append(node("span", "eyebrow", humanize(resource.topic)), node("h2", "", resource.title), node("p", "", resource.summary));
      if (resource.checklist?.length) { const details = node("details"); details.append(node("summary", "", "Planning checklist")); const list = node("ul"); for (const entry of resource.checklist) list.append(node("li", "", typeof entry === "string" ? entry : entry.text || pretty(entry))); details.append(list); card.append(details); }
      const sources = node("div", "survival-sources"); sources.append(node("span", "eyebrow", "SOURCE REFERENCES"));
      for (const source of resource.sources || []) { const link = survivalSourceLink(source); if (link) { const entry = node("div", "survival-source-entry"); entry.append(link); if (source.issued || source.checked) entry.append(node("small", "", `${source.issued ? `Issued ${source.issued}` : ""}${source.issued && source.checked ? " · " : ""}${source.checked ? `Checked ${source.checked}` : ""}`)); sources.append(entry); } }
      card.append(sources);
      if (resource.status === "bundled") card.append(button(String(resource.path || "").toLowerCase().endsWith(".pdf") ? "Download source PDF" : "Download planning card", "button-secondary survival-reference-download", () => downloadSurvivalReference(resource), "file"));
      const fetched = resource.fetched_at || data.checked_utc || data.fetched_at, status = resource.status !== "bundled" ? "External reference · link only" : String(resource.path || "").toLowerCase().endsWith(".md") ? "Original Ariadne planning card · bundled" : "Source document · bundled";
      card.append(node("p", "survival-reference-provenance", `${humanize(status)}${fetched ? ` · checked ${dateLabel(fetched)}` : ""}`));
      if (resource.provenance) { const details = node("details", "survival-provenance"); details.append(node("summary", "", "Reference provenance"), node("pre", "", pretty(resource.provenance))); card.append(details); }
      container.append(card);
    }
  } catch (error) { if (request === state.survivalLibraryRequest && state.authenticated) container.replaceChildren(node("p", "inline-error", error.message)); }
}

async function downloadSurvivalReference(resource) {
  try {
    const file = await api(`/survival/library/${encodeURIComponent(resource.id)}/download`, {blob:true}), url = URL.createObjectURL(file), link = node("a");
    link.href = url; link.download = typeof resource.download_name === "string" ? resource.download_name.replaceAll("\\", "/").split("/").pop() : typeof resource.path === "string" ? resource.path.replaceAll("\\", "/").split("/").pop() : String(resource.id); document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message, true); }
}
function invalidateSurvivalBookPreview() {
  state.survivalBookPreview = null; $("#survival-book-preview").hidden = true; $("#import-survival-book").disabled = true;
}
function survivalBookControls(busy) {
  state.survivalBookBusy = busy;
  for (const id of ["preview-survival-book","survival-book-source","survival-book-path","survival-book-title"]) $(`#${id}`).disabled = busy;
  $("#import-survival-book").disabled = busy || !state.survivalBookPreview;
}
async function loadSurvivalBooks() {
  const request = ++state.survivalBooksRequest, container = $("#survival-book-list");
  try {
    const results = await Promise.allSettled([api("/survival/books"),api("/sources")]);
    if (request !== state.survivalBooksRequest || !state.authenticated) return;
    clear(container);
    if (results[0].status === "rejected") throw results[0].reason;
    const books = results[0].value.books || [];
    if (!books.length) container.append(node("p", "survival-footnote", "No full books imported yet. Review and import a local copy below."));
    for (const book of books) {
      const row = node("article", "survival-imported-book"), text = node("div");
      text.append(node("h3", "", book.title), node("p", "", `${humanize(book.format)} · ${book.text_sections ?? 0} / ${book.total_sections ?? 0} sections with text · ${book.coverage_complete ? "Text found in every section" : "Some sections have no text"}`), node("p", "survival-book-coverage-note", "All sections processed; full content coverage is not certified. Diagrams/images are not interpreted."));
      const provenance = node("details", "survival-provenance"); provenance.append(node("summary", "", "Extraction & source fingerprint"), node("pre", "", pretty({sha256:book.sha256,total_sections:book.total_sections,text_sections:book.text_sections,empty_sections:book.empty_sections,coverage_metric:book.coverage_metric || "sections_with_any_extractable_text",created:book.created}))); text.append(provenance);
      if (book.warnings?.length) for (const warning of book.warnings) text.append(node("p", "survival-book-warning", typeof warning === "string" ? warning : pretty(warning)));
      row.append(text,button("Read", "button-secondary", () => openSurvivalBook(book.id, 1), "book")); container.append(row);
    }
    if (results[1].status === "fulfilled") {
      const select = $("#survival-book-source"), current = select.value; select.replaceChildren(node("option", "", "Choose an approved folder")); select.firstChild.value = "";
      for (const source of results[1].value.sources || []) { const option = node("option", "", source.name || source.id); option.value = source.id; select.append(option); } select.value = current;
      if (!(results[1].value.sources || []).length) select.firstChild.textContent = "Register a folder in Knowledge first";
    } else { $("#survival-book-error").textContent = results[1].reason.message; $("#survival-book-error").hidden = false; }
  } catch (error) { if (request === state.survivalBooksRequest && state.authenticated) container.replaceChildren(node("p", "inline-error", error.message)); }
}
async function previewSurvivalBook(event) {
  event.preventDefault(); if (state.survivalBookBusy || !state.authenticated) return;
  const sourceId = $("#survival-book-source").value, path = $("#survival-book-path").value.trim(); invalidateSurvivalBookPreview(); survivalBookControls(true); $("#survival-book-error").hidden = true; $("#survival-book-progress").textContent = "Reviewing the local file…";
  try {
    const preview = await api("/survival/books/preview", {method:"POST",body:{source_id:sourceId,path}});
    if (!state.authenticated) return;
    state.survivalBookPreview = {source_id:sourceId,path,sha256:preview.sha256};
    if (!$("#survival-book-title").value.trim()) $("#survival-book-title").value = preview.name || "Local reference book";
    $("#survival-book-preview").textContent = `${preview.name || path} · ${bytes(preview.bytes)} · ${humanize(preview.format)}\nSHA-256 ${preview.sha256}\n${preview.notice || "Import will verify this file fingerprint before extracting reference text."}\nFull content coverage is not certified. Diagrams/images are not interpreted.`; $("#survival-book-preview").hidden = false;
  } catch (error) { $("#survival-book-error").textContent = error.message; $("#survival-book-error").hidden = false; }
  finally { survivalBookControls(false); $("#survival-book-progress").textContent = ""; }
}
async function importSurvivalBook() {
  const preview = state.survivalBookPreview; if (!preview || state.survivalBookBusy || !state.authenticated) return;
  const title = $("#survival-book-title").value.trim(); if (!title) { $("#survival-book-title").focus(); return; }
  survivalBookControls(true); $("#survival-book-error").hidden = true; $("#survival-book-progress").textContent = "Extracting and indexing every section locally…";
  try {
    await api("/survival/books", {method:"POST",body:{...preview,title}});
    if (!state.authenticated) return;
    invalidateSurvivalBookPreview(); await loadSurvivalBooks(); toast("Book imported locally. Review the reported text coverage.");
  } catch (error) { $("#survival-book-error").textContent = error.message; $("#survival-book-error").hidden = false; }
  finally { survivalBookControls(false); $("#survival-book-progress").textContent = ""; }
}
function renderSurvivalBookResults(results, references = [], resources = []) {
  const container = $("#survival-book-results"); clear(container);
  const catalog = new Map([...(state.survival?.library?.resources || []),...resources].map(resource => [resource.id,resource]));
  if (references.length) container.append(node("h2", "", "Matches in bundled references"));
  for (const result of references) {
    const source = {...(catalog.get(result.resource_id) || {}),id:result.resource_id};
    if (result.path) source.path = result.path;
    if (result.download_name) source.download_name = result.download_name;
    const isPDF = String(source.path || source.download_name || "").toLowerCase().endsWith(".pdf");
    const row = node("article", "survival-book-result");
    row.append(node("h3", "", result.title), node("span", "eyebrow", result.label || `Section ${result.section}`));
    const citations = result.sources || source.sources || [];
    if (citations[0]) row.append(node("p", "muted small", `Source snapshot · ${citations[0].issued || "Issue date not recorded"} · checked ${citations[0].checked || "date not recorded"}`));
    const passage = String(result.snippet || ""); row.append(node("p", "", passage.length > 400 ? `${passage.slice(0,400)}…` : passage));
    const details = node("details"); details.append(node("summary", "", "Matched passage & source identity"), node("p", "", passage), node("code", "", `SHA-256 ${result.sha256 || "not recorded"}`));
    for (const citation of citations) { const link = survivalSourceLink(citation); if (link) details.append(link); }
    row.append(details, button(isPDF ? "Download source PDF" : "Download planning card", "button-secondary", () => downloadSurvivalReference(source), "file")); container.append(row);
  }
  if (results.length) container.append(node("h2", "", "Matches in your books"));
  for (const result of results) {
    const row = node("article", "survival-book-result"); row.append(node("h3", "", result.title), node("span", "eyebrow", result.label || `Section ${result.section}`), node("p", "", result.snippet), button("Read section", "button-secondary", () => openSurvivalBook(result.book_id, Number(result.section)), "book")); container.append(row);
  }
}
async function openSurvivalBook(id, section) {
  if (!state.authenticated) return;
  const request = ++state.survivalReaderRequest; $("#survival-reader-error").hidden = true; $("#survival-reader-text").textContent = "Loading local reference text…"; $("#survival-reader-previous").disabled = true; $("#survival-reader-next").disabled = true;
  if (!$("#survival-reader-dialog").open) $("#survival-reader-dialog").showModal();
  try {
    const data = await api(`/survival/books/${encodeURIComponent(id)}?section=${section}`);
    if (request !== state.survivalReaderRequest || !state.authenticated) return;
    const book = data.book || {}, current = data.section?.number || section, total = book.total_sections || data.total_sections || current;
    state.survivalReader = {id,section:current,total};
    $("#survival-reader-title").textContent = book.title || "Local reference book"; $("#survival-reader-position").textContent = `${data.section?.label || `Section ${current}`} · ${current} / ${total}`;
    $("#survival-reader-text").textContent = data.section?.text || "This section contains no extracted text. Inspect the original file; scanned pages may need OCR.";
    $("#survival-reader-coverage").textContent = book.coverage_complete ? "Text found in every section" : "Some sections have no text";
    $("#survival-reader-previous").disabled = current <= 1; $("#survival-reader-next").disabled = current >= total; $("#survival-reader-text").scrollTop = 0;
  } catch (error) { if (request === state.survivalReaderRequest && state.authenticated) { $("#survival-reader-text").textContent = ""; $("#survival-reader-error").textContent = error.message; $("#survival-reader-error").hidden = false; } }
}

hydrateIcons();
$(".app-shell").inert = true;
setMenu(false);
window.matchMedia("(max-width:700px)").addEventListener("change", () => setMenu(false));
$("#mission-prompt").maxLength = 16000;
$("#pairing-form").addEventListener("submit", event => { event.preventDefault(); state.token = $("#access-token").value.trim(); if (state.token) connect(); });
$("#new-mission").addEventListener("click", newMission);
$(".brand").addEventListener("click", event => { event.preventDefault(); newMission(); });
$$('[data-view]').forEach(item => item.addEventListener("click", () => item.dataset.view === "missions" ? newMission() : showView(item.dataset.view)));
$("#menu-toggle").addEventListener("click", () => setMenu(!$("#sidebar").classList.contains("open")));
$("#sidebar-scrim").addEventListener("click", () => setMenu(false));
$("#directory-close")?.addEventListener("click", () => setMenu(false));
$("#refresh-runs").addEventListener("click", loadRuns);
$("#command-log-toggle")?.addEventListener("click", () => { state.commandAllMissions = !state.commandAllMissions; renderRuns(); });
$("#lock-button").addEventListener("click", () => lockSession());
$("#settings-lock").addEventListener("click", () => lockSession());
$("#pause-toggle").addEventListener("click", togglePause);
// Specialist roles and deterministic daily reporting share the same local server.
let agentCatalog = [], wakeRequest = false, wakeRememberDirty = false, wakeGeneration = 0, wakeStatusRevision = 0, briefRequest = false, briefConfig = null;
let wakePollTimer = null, wakePollRequest = null, wakePollGeneration = 0;
let briefViewRequest = 0, briefLoadRequest = 0, briefPosterUrl = null, briefPoster = null;
async function loadAgents({quiet = false} = {}) {
  if (!state.authenticated) return;
  try {
    const data = await api("/agents");
    if (!state.authenticated) return;
    agentCatalog = data.agents || [];
    if ($("#editor-input-agent_id")) populateWorkflowAgent($("#editor-input-agent_id"), $("#editor-input-agent_id").value || "general");
    const select = $("#mission-agent"), selected = select.value;
    select.replaceChildren();
    const auto = node("option", "", "Auto · route this mission"); auto.value = "auto"; select.append(auto);
    for (const agent of agentCatalog) { const option = node("option", "", agent.name); option.value = agent.id; select.append(option); }
    select.value = [...select.options].some(option => option.value === selected) ? selected : "auto";
    $("#agent-count").textContent = `${agentCatalog.length} specialists`;
    renderWorkflowFilter(); if (state.workflows.length) renderAIWorkflows();
    facts($("#agent-autonomy-facts"), [["Worker concurrency", String(data.worker_concurrency || 1)], ["Idle model calls", String(data.idle_model_calls ?? 0)], ["Mission step limit", String(state.status?.max_steps || "—")], ["Dispatch", "Deterministic · no extra model call"]]);
    const container = $("#agent-list"); clear(container);
    for (const agent of agentCatalog) {
      const card = node("article", "agent-card");
      card.append(node("span", "eyebrow", agent.id), node("h2", "", agent.name), node("p", "", agent.description));
      if (agent.connectivity === "local_only") card.append(node("span", "tag", "Local only"));
      card.append(node("div", "agent-tools", `${agent.tool_count} exposed tools · ${agent.max_steps} steps maximum · existing approvals`));
      card.append(button("Give this agent a mission", "button-secondary", () => { newMission(); select.value = agent.id; $("#mission-prompt").focus(); }, "arrow-up-right"));
      card.append(button("Browse assigned workflows", "button-secondary", () => { state.workflowAgentFilter = agent.id; setWorkflowTab("ai"); showView("workflows"); }, "workflow"));
      container.append(card);
    }
    renderWakeStatus(state.status?.wake);
  } catch (error) { if (!quiet) toast(error.message, true); }
}
function wakePresentation(wake) {
  if (!wake || typeof wake !== "object" || typeof wake.active !== "boolean") return {known:false, active:false, label:"Voice unavailable", description:"Refresh system status to see the PC microphone state."};
  const active = wake.active === true;
  if (!active) return {known:true, active:false, label:"Mic off", description:"Microphone off. Turn it on when you want to talk."};
  if (wake.speaking === true) return {known:true, active:true, label:"Ariadne speaking", description:"Ariadne is speaking. Recognition is paused during her reply."};
  if (wake.suppressed === true || wake.acknowledging === true) return {known:true, active:true, label:"Listening paused", description:"Recognition is paused for playback. Wait before speaking."};
  if (wake.state === "starting") return {known:true, active:true, label:"Starting microphone", description:"Starting local PC microphone capture."};
  if (wake.state === "working" || wake.pending_run_id) return {known:true, active:true, label:"Working", description:"Working on your request. Wait for the reply before a follow-up."};
  if (wake.conversation_active === true) return {known:true, active:true, label:"Awake", description:"Awake. Keep talking without repeating “Ariadne”."};
  return {known:true, active:true, label:"Waiting for Ariadne", description:"Say “Ariadne” to begin. The PC microphone is listening locally."};
}
function applyWakeSnapshot(wake) {
  wakeStatusRevision += 1;
  if (state.status) state.status.wake = wake;
  renderWakeStatus(wake);
  window.ariadneCommand?.render();
}
function confirmWakeSnapshot(wake) {
  cancelWakePolling();
  applyWakeSnapshot(wake);
}
function renderWakeStatus(wake) {
  const view = wakePresentation(state.authenticated ? wake : null);
  const indicator = $("#wake-indicator");
  indicator.classList.toggle("active", view.active);
  indicator.title = `${view.label}. Open voice controls`;
  indicator.setAttribute("aria-label", indicator.title);
  $("#wake-indicator-label").textContent = view.label;
  $("#wake-conversation-status").textContent = state.authenticated ? view.description : "Pair this tab to see the PC microphone state. Locking this tab does not stop PC listening.";
  const timeout = wake?.conversation_idle_timeout_seconds;
  const idle = Number.isInteger(timeout) && timeout > 0 && timeout <= 86400 ? timeout % 60 === 0 ? `${timeout / 60} minutes` : `${timeout} seconds` : "Unavailable";
  const phrases = Array.isArray(wake?.sleep_phrases) ? wake.sleep_phrases.filter(value => typeof value === "string" && value.length <= 80).slice(0, 4) : [];
  facts($("#wake-facts"), view.known ? [["Microphone", view.active ? `${humanize(wake.mode)} · on` : "Off"], ["Conversation idle timeout", idle], ["Return to wake word", phrases.length ? phrases.map(value => `“${value}”`).join(", ") : "Go to sleep"], ["Listening session", wake.session_limit_hours === null ? "Until stopped" : Number.isInteger(wake.session_limit_hours) ? `${wake.session_limit_hours} hours` : "Unavailable"], ["Startup listening", typeof wake.auto_start === "boolean" ? wake.auto_start ? "Enabled" : "Off" : "Unavailable"], ["Ambient audio saved", wake.audio_saved === true ? "Yes" : "No"], ["Idle model calls", String(wake.idle_llm_calls ?? 0)]] : []);
  if (!view.known) $("#wake-remember").checked = false;
  else if (view.active || !wakeRememberDirty) $("#wake-remember").checked = wake.auto_start === true;
  $("#wake-remember").disabled = !view.known || view.active || wakeRequest;
  const error = view.known && typeof wake.last_error === "string" ? wake.last_error : "";
  $("#wake-error").textContent = error; $("#wake-error").hidden = !error;
  $("#wake-start").disabled = !view.known || view.active || wakeRequest || wake.pc_supported !== true;
  // An explicit stop remains available after an uncertain start or with startup listening enabled.
  $("#wake-stop").disabled = !state.authenticated || wakeRequest || (view.known && !view.active && wake.auto_start !== true);
  syncWakePolling(state.authenticated ? wake : null);
}
function cancelWakePolling() {
  wakePollGeneration += 1;
  if (wakePollTimer !== null) clearTimeout(wakePollTimer);
  wakePollTimer = null;
  // Retain the in-flight slot until the aborted read settles: never overlap reads.
  if (wakePollRequest) wakePollRequest.controller.abort();
}
function syncWakePolling(wake) {
  if (!state.authenticated || wake?.active !== true || wakeRequest) { cancelWakePolling(); return; }
  if (wakePollTimer !== null || wakePollRequest !== null) return;
  const generation = wakePollGeneration, token = state.token;
  const timer = setTimeout(() => {
    if (wakePollTimer !== timer) return;
    wakePollTimer = null;
    refreshWakeStatus(generation, token);
  }, 2000);
  wakePollTimer = timer;
}
async function refreshWakeStatus(generation, token) {
  if (!state.authenticated || state.token !== token || generation !== wakePollGeneration || wakeRequest || state.status?.wake?.active !== true || wakePollRequest !== null) return;
  const request = {controller:new AbortController(), generation, token};
  wakePollRequest = request;
  const current = () => state.authenticated && state.token === token && generation === wakePollGeneration && !wakeRequest && wakePollRequest === request;
  try {
    const wake = await api("/voice/wake", {signal:request.controller.signal});
    if (!current()) return;
    applyWakeSnapshot(wake);
  } catch (error) {
    if (current() && error.name !== "AbortError") {
      // The existing full-status refresh can recover this view. Do not claim
      // capture stopped, keep retrying a failed read, or issue any mic action.
      applyWakeSnapshot(null);
    }
  } finally {
    if (wakePollRequest === request) {
      wakePollRequest = null;
      syncWakePolling(state.status?.wake);
    }
  }
}
function resetWakeControls() {
  wakeGeneration += 1; wakeRequest = false; wakeRememberDirty = false;
  confirmWakeSnapshot(null);
}
async function setWakeListening(start) {
  if (wakeRequest || !state.authenticated || $(start ? "#wake-start" : "#wake-stop").disabled) return;
  const remember = $("#wake-remember").checked, token = state.token, generation = ++wakeGeneration;
  const current = () => state.authenticated && state.token === token && wakeGeneration === generation;
  if (start) stopSpeech();
  wakeStatusRevision += 1;
  wakeRequest = true;
  renderWakeStatus(state.status?.wake);
  try {
    const wake = await api(`/voice/wake/${start ? "start" : "stop"}`, {method:"POST", body:start ? {mode:"pc", remember} : {}});
    if (!current()) return;
    wakeRememberDirty = false;
    confirmWakeSnapshot(wake);
    toast(start ? "PC microphone enabled. Say Ariadne once, then keep talking. Say go to sleep to return to the wake word." : "PC microphone and startup listening are off.");
  } catch (error) {
    if (current()) {
      confirmWakeSnapshot(null);
      toast(`${error.message} Checking microphone status; the request is not retried.`, true);
    }
  } finally {
    if (current()) {
      wakeRequest = false;
      renderWakeStatus(state.status?.wake);
      await refreshStatus({quiet:true});
      if (current()) renderWakeStatus(state.status?.wake);
    }
  }
}
async function loadBriefing() {
  if (!state.authenticated || briefRequest) return;
  const request = ++briefLoadRequest, token = state.token;
  const current = () => state.authenticated && state.token === token && request === briefLoadRequest;
  try {
    const data = await api("/briefing");
    if (!current()) return;
    window.ariadneCommand?.briefing(data);
    briefConfig = data.config;
    if (data.location) renderLocationResult(data.location, false);
    const cfg = data.config, receipt = data.last_report;
    $("#brief-next").textContent = `${String(cfg.hour).padStart(2,"0")}:${String(cfg.minute).padStart(2,"0")} · ${cfg.timezone}`;
    facts($("#brief-facts"), [["Schedule", cfg.paused ? "Held · review before resuming" : cfg.enabled ? "Enabled" : "Disabled", cfg.paused], ["Next brief", dateLabel(data.next_due_at, true)], ["Region", cfg.region?.name || "City / ZIP still needed", !cfg.region], ["Internet channel", data.internet_enabled ? "Enabled" : "Off · saved evidence only", !data.internet_enabled], ["Public-source cache", data.cache ? dateLabel(data.cache.collected_at, true) : "Unavailable", !data.cache]]);
    // A refresh does not replace the form while the owner is editing it.
    if (!$("#brief-config-form").contains(document.activeElement)) {
      $("#brief-enabled").checked = cfg.enabled;
      $("#brief-region").value = cfg.region?.name || "";
      $("#brief-latitude").value = cfg.region?.latitude ?? "";
      $("#brief-longitude").value = cfg.region?.longitude ?? "";
      $("#brief-online").checked = cfg.online_sources;
      $("#brief-repository-updates").checked = cfg.repository_updates === true;
      $("#brief-config-form").dataset.revision = String(cfg.revision);
      $("#brief-feeds").value = (cfg.news_feeds || []).map(feed => feed.url).join("\n");
    }
    $("#brief-error").textContent = cfg.pause_reason || ""; $("#brief-error").hidden = !cfg.pause_reason;
    $("#brief-save").textContent = cfg.paused ? "Save settings and resume brief" : "Save briefing settings";
    await showBriefReceipt(receipt);
    if (!current()) return;
    const history = await api("/briefing/history");
    if (!current()) return;
    const container = $("#brief-history"); clear(container);
    for (const run of (history.runs || []).slice(0,10)) {
      const card = node("article", "brief-history-item"), body = node("div");
      body.append(node("strong", "", `${run.day || "Manual brief"} · ${humanize(run.status)}`), node("p", "", `${dateLabel(run.created_at, true)} · ${run.model_calls ?? 0} model calls · ${run.request_id}`)); card.append(body);
      if (run.artifacts?.length) card.append(button("Read report", "button-secondary", () => showBriefReceipt(run), "file"));
      container.append(card);
    }
  } catch (error) { if (current()) toast(error.message, true); }
}
function clearBriefPoster(invalidateLoad = false) {
  if (invalidateLoad) briefLoadRequest += 1;
  briefViewRequest += 1;
  if (briefPosterUrl) URL.revokeObjectURL(briefPosterUrl);
  briefPosterUrl = null; briefPoster = null;
  const image = $("#brief-poster-image"), frame = $("#brief-poster-frame");
  if (image) { image.removeAttribute("src"); image.alt = "One-page Ariadne daily brief"; }
  if (frame) frame.hidden = true;
  const note = $("#brief-poster-note");
  if (note) note.textContent = "";
  $("#brief-report-title").textContent = "Awaiting the first brief";
  $("#brief-report").textContent = "Your local daily brief will appear here.";
  $("#brief-receipt").textContent = "One page. Local evidence. No language-model calls.";
  clear($("#brief-artifacts"));
}
function downloadBriefBlob(blob, filename) {
  const url = URL.createObjectURL(blob), link = node("a");
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function downloadBriefPoster(format, control) {
  const poster = briefPoster, request = briefViewRequest, token = state.token;
  const current = () => state.authenticated && state.token === token && request === briefViewRequest && briefPoster === poster;
  if (!poster || !current() || control.disabled) return;
  if (format === "svg") { downloadBriefBlob(poster.blob, poster.filename+".svg"); return; }
  control.disabled = true;
  const url = URL.createObjectURL(poster.blob);
  try {
    const image = new Image();
    await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error("The local poster image could not be decoded. Download SVG instead.")); image.src = url; });
    if (!current()) return;
    const canvas = document.createElement("canvas"); canvas.width = 1080; canvas.height = 1350;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("PNG export is unavailable in this browser. Download SVG instead.");
    context.drawImage(image, 0, 0, 1080, 1350);
    const blob = await new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error("PNG export failed. Download SVG instead.")), "image/png"));
    if (current()) downloadBriefBlob(blob, poster.filename+".png");
  } catch (error) { if (current()) toast(error.message, true); }
  finally { URL.revokeObjectURL(url); if (current()) control.disabled = false; }
}
function printBriefPoster() {
  if (!state.authenticated || !briefPoster) return;
  document.body.classList.add("brief-printing");
  try { window.print(); } finally { document.body.classList.remove("brief-printing"); }
}
async function showBriefReceipt(receipt) {
  clearBriefPoster();
  const request = briefViewRequest, token = state.token;
  const current = () => state.authenticated && state.token === token && request === briefViewRequest;
  const actions = $("#brief-artifacts"); clear(actions);
  if (!receipt || !state.authenticated) return;
  $("#brief-report-title").textContent = `${receipt.day || "Latest brief"} · ${humanize(receipt.status)}`;
  $("#brief-receipt").textContent = `${receipt.model_calls ?? 0} model calls · ${receipt.wall_seconds ?? "—"} seconds · ${receipt.source_count ?? 0} cached sources`;
  const artifact = receipt.artifacts?.find(item => item.path.endsWith("brief.md"));
  if (artifact && receipt.status === "completed") {
    let report;
    try { report = await api(`/briefing/report?request_id=${encodeURIComponent(receipt.request_id)}`); }
    catch (error) {
      if (current()) { $("#brief-report").textContent = "Could not read this saved brief. "+error.message; toast(error.message, true); }
      return;
    }
    if (!current()) return;
    $("#brief-report").textContent = report.content || "The report is empty.";
    if (report.truncated) $("#brief-report").append(document.createTextNode("\n[Preview truncated; download the report for complete content.]"));
    const poster = report.poster;
    if (poster && poster.mime === "image/svg+xml" && poster.width === 1080 && poster.height === 1350 && typeof poster.svg === "string" && poster.svg.length <= 36000) {
      const blob = new Blob([poster.svg], {type:"image/svg+xml"});
      briefPosterUrl = URL.createObjectURL(blob);
      briefPoster = {blob, filename:"ariadne-brief-"+String(receipt.day || receipt.request_id).replace(/[^A-Za-z0-9_-]/g,"_").slice(0,64)};
      const image = $("#brief-poster-image");
      image.src = briefPosterUrl; image.alt = typeof poster.accessible_text === "string" ? poster.accessible_text : "One-page Ariadne daily brief";
      $("#brief-poster-note").textContent = poster.note || "One-page view from verified saved evidence. Full details below.";
      $("#brief-poster-frame").hidden = false;
      const png = button("Download PNG", "button-primary", () => downloadBriefPoster("png", png), "arrow-up-right");
      const svg = button("Download SVG", "button-secondary", () => downloadBriefPoster("svg", svg), "file");
      actions.append(png, svg, button("Print one page", "button-secondary", printBriefPoster, "file"));
    }
    for (const file of receipt.artifacts) {
      if (file.path.endsWith(".svg")) continue; // Freshness-aware export above; saved SVG stays in the workspace.
      actions.append(button(file.path.endsWith(".md") ? "Full report" : "Evidence JSON", "button-secondary", () => downloadArtifact(file.path), "file"));
    }
  } else $("#brief-report").textContent = receipt.error || "This briefing has no completed report. Inspect its status before retrying.";
}
async function saveBriefing(event) {
  event.preventDefault();
  const name = $("#brief-region").value.trim(), latitude = $("#brief-latitude").value, longitude = $("#brief-longitude").value;
  if ((name || latitude || longitude) && (!name || !latitude || !longitude)) { toast("Enter the region name and both coordinates, or leave all three blank.", true); return; }
  if (briefConfig?.paused && !await confirmAction("Resume the daily brief?", "Review the previous failed or interrupted report before resuming. The daily schedule will not replay that day automatically.", "Save and resume", "Keep held")) return;
  $("#brief-save").disabled = true;
  try {
    const expectedRevision = Number($("#brief-config-form").dataset.revision);
    if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 0) throw new Error("Refresh briefing settings before saving.");
    const urls = $("#brief-feeds").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    const feeds = urls.map(url => (briefConfig?.news_feeds || []).find(feed => feed.url === url) || {title:"Regional official news", url, region_terms:[]});
    await api("/briefing/config", {method:"PUT", body:{enabled:$("#brief-enabled").checked, paused:false, region:name ? {name, latitude:Number(latitude), longitude:Number(longitude), country:"US"} : null, online_sources:$("#brief-online").checked, repository_updates:$("#brief-repository-updates").checked, news_feeds:feeds, expected_revision:expectedRevision}});
    toast("Daily briefing settings saved."); await loadBriefing();
  } catch (error) { toast(error.message, true); }
  finally { $("#brief-save").disabled = false; }
}
function renderLocationResult(location, apply) {
  if (!location?.ok) { $("#location-note").textContent = location?.note || "The device did not provide a location. You can keep the configured city reference."; return; }
  const accuracy = Number.isFinite(location.accuracy_meters) ? `Reported accuracy ±${Math.round(location.accuracy_meters)} m` : "Accuracy not reported";
  $("#location-note").textContent = `${humanize(location.source)} · ${accuracy} · ${dateLabel(location.measured_at_utc || location.fetched_at_utc, true)}${location.stale ? " · Stale" : ""}. A device estimate, not an exact position.${apply ? " Review the coordinates and save briefing settings to use it." : ""}`;
  if (apply && !location.stale) {
    $("#brief-latitude").value = location.latitude; $("#brief-longitude").value = location.longitude;
    if (!$("#brief-region").value.trim()) $("#brief-region").value = "Device location";
  }
}
async function detectLocation(browserMode = false) {
  $("#location-detect").disabled = true; $("#location-browser").disabled = true;
  try {
    let location;
    if (browserMode) {
      if (!navigator.geolocation || !window.isSecureContext) throw new Error("Device location needs a supported browser on localhost or HTTPS.");
      const position = await new Promise((resolve,reject) => navigator.geolocation.getCurrentPosition(resolve,reject,{enableHighAccuracy:true,timeout:15000,maximumAge:0}));
      location = await api("/location/browser", {method:"POST",body:{latitude:position.coords.latitude,longitude:position.coords.longitude,accuracy_meters:position.coords.accuracy,timestamp_ms:position.timestamp}});
    } else location = await api("/location/detect", {method:"POST", body:{}});
    if (state.authenticated) renderLocationResult(location, true);
  } catch (error) { toast(error.message || "Location was unavailable or permission was not granted.", true); }
  finally { $("#location-detect").disabled = false; $("#location-browser").disabled = false; }
}
async function runBriefing() {
  if (briefRequest) return;
  briefRequest = true; $("#brief-run").disabled = true;
  const requestId = `manual-${crypto.randomUUID().replaceAll("-", "")}`;
  try { await api("/briefing/run", {method:"POST", body:{request_id:requestId}}); toast("Daily brief created locally."); }
  catch (error) { toast(`${error.message} Request ${requestId}; inspect briefing history before retrying.`, true); }
  finally { briefRequest = false; $("#brief-run").disabled = false; await loadBriefing(); }
}
$("#wake-indicator").addEventListener("click", () => showView("agents"));
$("#refresh-agents").addEventListener("click", () => loadAgents());
$("#wake-start").addEventListener("click", () => setWakeListening(true));
$("#wake-stop").addEventListener("click", () => setWakeListening(false));
$("#wake-remember").addEventListener("change", () => { wakeRememberDirty = true; });
$("#agents-open-brief").addEventListener("click", () => showView("briefing"));
$("#brief-config-form").addEventListener("submit", saveBriefing);
$("#brief-run").addEventListener("click", runBriefing);
$("#refresh-brief").addEventListener("click", loadBriefing);
$("#location-detect").addEventListener("click", () => detectLocation(false));
$("#location-browser").addEventListener("click", () => detectLocation(true));
$("#mission-form").addEventListener("submit", launchMission);
$("#mission-prompt").addEventListener("input", resizeComposer);
$("#mission-prompt").addEventListener("keydown", event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); $("#mission-form").requestSubmit(); } });
$$('[data-prompt]').forEach(item => item.addEventListener("click", () => { $("#mission-prompt").value = item.dataset.prompt; resizeComposer(); $("#mission-prompt").focus(); }));
$("#cancel-run").addEventListener("click", cancelRun);
$("#save-run-workflow").addEventListener("click", saveRunWorkflow);
$("#reset-session").addEventListener("click", newMission);
$("#composer-knowledge").addEventListener("click", () => showView("knowledge"));
$("#add-memory").addEventListener("click", () => openEditor("memory"));
$("#refresh-survival").addEventListener("click", loadSurvival);
$("#edit-survival-profile").addEventListener("click", () => openSurvivalEditor("profile"));
$("#add-survival-supply").addEventListener("click", () => openSurvivalEditor("supply"));
$("#add-survival-task").addEventListener("click", () => openSurvivalEditor("task"));
$("#seed-survival-roadmap").addEventListener("click", seedSurvivalRoadmap);
$("#survival-editor-form").addEventListener("submit", saveSurvivalEditor);
for (const id of ["close-survival-editor", "cancel-survival-editor"]) $(`#${id}`).addEventListener("click", () => $("#survival-editor-dialog").close());
$("#survival-library-search").addEventListener("submit", event => { event.preventDefault(); loadSurvivalLibrary(); });
$("#survival-topic").addEventListener("change", loadSurvivalLibrary);
$("#survival-manage-sources").addEventListener("click", () => { state.knowledgeTab = "sources"; showView("knowledge"); });
$("#survival-book-form").addEventListener("submit", previewSurvivalBook);
$("#import-survival-book").addEventListener("click", importSurvivalBook);
for (const id of ["survival-book-source","survival-book-path"]) $(`#${id}`).addEventListener("input", invalidateSurvivalBookPreview);
$("#close-survival-reader").addEventListener("click", () => { state.survivalReaderRequest += 1; $("#survival-reader-dialog").close(); });
$("#survival-reader-dialog").addEventListener("cancel", () => { state.survivalReaderRequest += 1; });
for (const [id,delta] of [["survival-reader-previous",-1],["survival-reader-next",1]]) $(`#${id}`).addEventListener("click", () => { const reader = state.survivalReader; if (reader) openSurvivalBook(reader.id,reader.section + delta); });
$$('[data-survival-tab]').forEach(tab => {
  tab.addEventListener("click", () => setSurvivalTab(tab.dataset.survivalTab));
  tab.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); const names = ["overview", "supplies", "projects", "library"], current = names.indexOf(state.survivalTab);
    const name = event.key === "Home" ? names[0] : event.key === "End" ? names.at(-1) : names[(current + (event.key === "ArrowLeft" ? -1 : 1) + names.length) % names.length];
    setSurvivalTab(name); $(`[data-survival-tab="${name}"]`).focus();
  });
});
$("#add-knowledge").addEventListener("click", () => openEditor("knowledge"));
$("#upload-knowledge").addEventListener("click", () => $("#knowledge-file-input").click());
$("#knowledge-file-input").addEventListener("change", event => openUpload(event.target.files?.[0]));
$("#upload-form").addEventListener("submit", event => { event.preventDefault(); uploadKnowledge(state.uploadFile); });
$("#upload-ocr").addEventListener("change", updateUploadMode);
for (const id of ["close-upload", "cancel-upload"]) $(`#${id}`).addEventListener("click", () => $("#upload-dialog").close());
$("#source-ocr").addEventListener("change", () => { const path = state.previewPath; loadSourceFiles(); if (path) previewSource(path); });
$("#add-mcp-server").addEventListener("click", openMCPServer);
$("#mcp-server-form").addEventListener("submit", saveMCPServer);
for (const id of ["close-mcp-server", "cancel-mcp-server"]) $(`#${id}`).addEventListener("click", () => $("#mcp-server-dialog").close());
for (const id of ["close-mcp-review", "cancel-mcp-review"]) $(`#${id}`).addEventListener("click", () => $("#mcp-review-dialog").close());
$("#apply-mcp-tools").addEventListener("click", applyMCPTools);
$$('[data-capability-tab]').forEach(tab => {
  tab.addEventListener("click", () => setCapabilityTab(tab.dataset.capabilityTab));
  tab.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const name = event.key === "Home" ? "catalog" : event.key === "End" ? "mcp" : state.capabilityTab === "catalog" ? "mcp" : "catalog";
    setCapabilityTab(name); $(`[data-capability-tab="${name}"]`).focus();
  });
});
$("#add-source").addEventListener("click", () => openEditor("source"));
$("#remove-source").addEventListener("click", removeSource);
$("#source-select").addEventListener("change", event => { state.sourceId = event.target.value || null; state.sourceDirectory = "."; resetSourcePreview(); const source = state.sources.find(item => item.id === state.sourceId); $("#source-scope").textContent = source ? `Read-only source · ${source.path}` : "No source selected"; loadSourceFiles(); });
$$('[data-knowledge-tab]').forEach(tab => {
  tab.addEventListener("click", () => setKnowledgeTab(tab.dataset.knowledgeTab));
  tab.addEventListener("keydown", event => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); const name = event.key === "Home" ? "documents" : event.key === "End" ? "sources" : state.knowledgeTab === "documents" ? "sources" : "documents"; setKnowledgeTab(name); $(`[data-knowledge-tab="${name}"]`).focus(); } });
});
$("#add-workflow").addEventListener("click", () => { setWorkflowTab("ai"); openEditor("workflow"); });
$("#workflow-agent-filter").addEventListener("change", event => { state.workflowAgentFilter = event.target.value; renderAIWorkflows(); });
$$('[data-workflow-tab]').forEach(tab => {
  tab.addEventListener("click", () => setWorkflowTab(tab.dataset.workflowTab));
  tab.addEventListener("keydown", event => {
    if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault(); const names = $$('[data-workflow-tab]').map(item => item.dataset.workflowTab), current = names.indexOf(state.workflowTab);
    const name = event.key === "Home" ? names[0] : event.key === "End" ? names[names.length-1] : names[(current + (event.key === "ArrowRight" ? 1 : -1) + names.length) % names.length];
    setWorkflowTab(name); $(`[data-workflow-tab="${name}"]`).focus();
  });
});
$("#refresh-procedures").addEventListener("click", loadProcedures);
$("#refresh-procedure-runs").addEventListener("click", loadProcedureRuns);
$("#run-procedure").addEventListener("click", runProcedure);
for (const id of ["close-procedure-review","cancel-procedure-review"]) $(`#${id}`).addEventListener("click", () => $("#procedure-review-dialog").close());
$("#launch-workflow").addEventListener("click", launchWorkflow);
$("#close-workflow-review").addEventListener("click", () => $("#workflow-review-dialog").close());
$("#cancel-workflow-review").addEventListener("click", () => $("#workflow-review-dialog").close());
$("#capability-search").addEventListener("input", () => renderCapabilities());
$("#manage-local-apps").addEventListener("click", openLocalApps);
$("#local-app-form").addEventListener("submit", saveLocalApp);
$("#close-local-apps").addEventListener("click", () => $("#local-apps-dialog").close());
$("#close-capability-setup").addEventListener("click", () => $("#capability-setup-dialog").close());
$("#capability-setup-done").addEventListener("click", () => $("#capability-setup-dialog").close());
$("#mission-checks").addEventListener("input", updateCheckCount);
$$('[data-check-template]', $("#mission-expectations")).forEach(item => item.addEventListener("click", () => addCheckTemplate($("#mission-checks"), item.dataset.checkTemplate)));
$("[data-clear-checks]").addEventListener("click", () => { $("#mission-checks").value = "[]"; updateCheckCount(); });
$("#add-schedule").addEventListener("click", () => openEditor("schedule"));
$("#editor-form").addEventListener("submit", saveEditor);
$("#close-editor").addEventListener("click", () => $("#editor-dialog").close());
$("#cancel-editor").addEventListener("click", () => $("#editor-dialog").close());
$("#refresh-files").addEventListener("click", loadFiles);
$("#refresh-connectors").addEventListener("click", loadConnectors);
$("#refresh-status").addEventListener("click", () => refreshStatus());
$("#model-form").addEventListener("submit", saveModel);
$("#speech-backend").addEventListener("change", () => saveSpeechPreferences());
$("#speech-voice").addEventListener("change", () => saveSpeechPreferences());
$("#speech-speed").addEventListener("change", () => saveSpeechPreferences());
$("#speech-preview").addEventListener("click", previewSpeech);
$("#copy-pull").addEventListener("click", () => copyText("ollama pull qwen3.5:9b"));
$("#voice-record").addEventListener("click", toggleRecording);
$("#speech-player").addEventListener("ended", stopSpeech);
document.addEventListener("keydown", event => {
  if (typeof event.key !== "string") return;
  if (event.key === "Escape" && $("#sidebar").classList.contains("open")) { event.preventDefault(); setMenu(false); return; }
  if (event.key === "Tab" && $("#sidebar").classList.contains("open")) {
    const items = $$('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]', $("#sidebar")).filter(item => !item.hidden && item.getClientRects().length);
    const first = items[0], last = items[items.length - 1];
    if (!$("#sidebar").contains(document.activeElement) || (event.shiftKey && document.activeElement === first)) { event.preventDefault(); (event.shiftKey ? last : first)?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
  const editing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "") || document.activeElement?.isContentEditable;
  if (event.key.toLowerCase() === "n" && !editing && !event.ctrlKey && !event.metaKey && !event.altKey && state.authenticated && !$("dialog[open]")) { event.preventDefault(); newMission(); }
});
let internetConfig = null, internetBusy = false, internetChanging = false, internetGeneration = 0;
function renderInternetStatus(data) {
  internetConfig = data.config;
  if (state.status) state.status.internet = data;
  const enabled = data.config.enabled === true;
  $("#internet-indicator-label").textContent = enabled ? "Web on" : "Web off";
  $("#internet-indicator").classList.toggle("active", enabled);
  $("#internet-mode").textContent = enabled ? "PUBLIC READING ENABLED" : "OFFLINE / SAVED EVIDENCE";
  $("#internet-channel-title").textContent = enabled ? "Channel enabled." : "Local first. Connected when needed.";
  $("#internet-channel-note").textContent = enabled ? "Agents can read public sources on demand. Your scheduled brief may refresh regional news and weather. Local inference stays on this PC." : "Enable public reading for your agents and scheduled news and weather. Local AI and saved references work offline.";
  $("#internet-toggle").textContent = internetChanging ? "Applying…" : enabled ? "Go offline" : "Enable Internet";
  $("#internet-toggle").disabled = internetChanging || !state.authenticated;
  $("#internet-toggle").setAttribute("aria-pressed", String(enabled));
  $("#internet-probe").disabled = !enabled || internetBusy;
  facts($("#internet-facts"), [["Last public success", data.connection_verified_at ? dateLabel(data.connection_verified_at, true) : "Not tested"], ["Offline cache", `${data.cache_entries} results · ${(data.cache_bytes / 1000000).toFixed(2)} MB`], ["Active requests", data.active_requests], ["Web retrieval model calls", "0"]]);
  window.ariadneCommand?.render();
}
async function loadInternet() {
  if (!state.authenticated) return;
  try {
    const data = await api("/internet");
    if (!state.authenticated) return;
    renderInternetStatus(data);
    await loadInternetHistory();
  } catch (error) { toast(error.message, true); }
}
async function loadInternetHistory() {
  if (!state.authenticated) return;
  const data = await api("/internet/history?limit=8");
  if (!state.authenticated) return;
  const container = $("#internet-history"); clear(container);
  if (!data.requests.length) container.append(node("p", "muted", "Your public requests will be recorded here."));
  for (const item of data.requests) {
    const row = node("article", "internet-history-row"), body = node("div");
    body.append(node("strong", "", item.label), node("p", "", `${dateLabel(item.created, true)} · ${humanize(item.operation)}${item.error ? ` · ${item.error}` : ""}`));
    row.append(node("span", `tag${item.ok ? "" : " warning"}`, item.cached ? "SAVED" : item.ok ? "RECEIVED" : "UNAVAILABLE"), body); container.append(row);
  }
}
async function toggleInternet() {
  if (!internetConfig || internetChanging) return;
  internetChanging = true;
  $("#internet-toggle").disabled = true;
  // Invalidate the visible result of any request that preceded this decision.
  internetGeneration += 1;
  try {
    const data = await api("/internet/config", {method:"PUT", body:{enabled:!internetConfig.enabled, expected_revision:internetConfig.revision}});
    if (!state.authenticated) return;
    renderInternetStatus(data);
    $("#internet-notice").textContent = data.config.enabled ? "Public reading is enabled for agents and the regional collector. Requests happen on demand or on your configured brief schedule." : "Internet is off. Saved results remain available. Active public reads are cancelled; the daily brief can use its existing cache.";
    toast(data.config.enabled ? "Internet enabled." : "Internet disabled.");
  } catch (error) { toast(error.message, true); }
  finally { internetChanging = false; await loadInternet(); }
}
function renderInternetResult(result) {
  const container = $("#internet-results"); clear(container);
  $("#internet-results-title").textContent = !result.ok ? "Request unavailable" : result.operation === "search" ? "Search evidence" : result.operation === "probe" ? "Connection test" : "Source captured";
  $("#internet-result-label").textContent = result.ok ? result.cached ? "SAVED RESULT" : "PUBLIC RESPONSE" : "NO RESULT";
  const notices = [...(result.warnings || [])];
  if (result.error) notices.push(result.error);
  if (result.refresh_failed) notices.push(`Fresh request failed: ${result.refresh_error}. Showing the saved evidence with its original retrieval time.`);
  if (result.offline) notices.push("This request used saved evidence while Internet was off.");
  if (result.provider === "wikipedia_only_fallback") notices.push("Wikipedia-only search fallback. These results do not cover the wider web.");
  if (result.operation === "probe" && result.ok && !result.cached) notices.push("A public HTTPS request to the National Weather Service succeeded. Other websites may still be unavailable.");
  $("#internet-notice").textContent = notices.join(" ") || "Public evidence is untrusted source material. Review the date and source before using it.";
  for (const source of result.sources || []) {
    const card = node("article", "internet-source"), heading = node("div", "internet-source-heading");
    const snippet = source.result_kind === "search_snippet";
    heading.append(node("span", "eyebrow", snippet ? "SEARCH SNIPPET / PAGE NOT READ" : "EXTRACTED PUBLIC TEXT"), node("span", "tag", source.stale ? "STALE CACHE" : source.cached ? "SAVED" : "RECEIVED"));
    card.append(heading, node("h3", "", source.title || "Public source"));
    // No remote markup, scripts, media or embeds enter the DOM.
    try {
      const url = new URL(source.url);
      if (url.protocol === "https:" && !url.username && !url.password) {
        const link = node("a", "internet-source-url", source.url); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link);
      }
    } catch { card.append(node("p", "", "Invalid source URL")); }
    card.append(node("p", "internet-source-date", `Retrieved ${dateLabel(source.fetched_at, true)}${source.cached ? ` · ${Math.floor((source.age_seconds || 0) / 60)} min old` : ""}${source.text_truncated ? " · Text clipped" : ""}`));
    card.append(node("pre", "internet-source-text", source.text));
    const metadata = node("details", "internet-source-metadata"); metadata.append(node("summary", "", "Provenance and content hash"), node("p", "", `SHA-256 (${source.hash_scope === "search_response" ? "search response; not the destination page" : "original HTTP response bytes"}): ${source.sha256}`));
    if (source.retrieval_url) metadata.append(node("p", "", `Search retrieval: ${source.retrieval_url}`));
    card.append(metadata);
    if (snippet) card.append(button("Read this source", "button-secondary", () => { $("#internet-url").value = source.url; requestInternet("fetch", {url:source.url}); }, "file"));
    container.append(card);
  }
  if (!result.sources?.length) container.append(node("p", "internet-empty", result.error || "The source returned no usable text."));
}
async function requestInternet(action, body = {}) {
  if (internetBusy || !state.authenticated) return;
  internetBusy = true;
  const generation = ++internetGeneration;
  const refresh = action === "probe" || $("#internet-refresh").checked;
  for (const id of ["internet-search-button", "internet-fetch-button", "internet-probe"]) $(`#${id}`).disabled = true;
  $("#internet-notice").textContent = "Request in progress. Public reads have a 65-second limit; Go offline cancels active requests.";
  try {
    const result = await api(`/internet/${action}`, {method:"POST", body:{...body, refresh}});
    if (state.authenticated && generation === internetGeneration) renderInternetResult(result);
  } catch (error) { if (state.authenticated && generation === internetGeneration) $("#internet-notice").textContent = error.message; }
  finally {
    internetBusy = false;
    for (const id of ["internet-search-button", "internet-fetch-button"]) $(`#${id}`).disabled = false;
    await loadInternet();
  }
}
$("#internet-indicator").addEventListener("click", () => showView("internet"));
$("#internet-toggle").addEventListener("click", toggleInternet);
$("#internet-probe").addEventListener("click", () => requestInternet("probe"));
$("#internet-history-refresh").addEventListener("click", () => loadInternet().catch(error => toast(error.message, true)));
$("#internet-search-form").addEventListener("submit", event => { event.preventDefault(); requestInternet("search", {query:$("#internet-query").value.trim(), max_results:5}); });
$("#internet-fetch-form").addEventListener("submit", event => { event.preventDefault(); requestInternet("fetch", {url:$("#internet-url").value.trim()}); });
window.addEventListener("pagehide", () => { if (state.recording) stopRecording(true); stopSpeech(); });
window.ariadneCommand?.attach({state, api, node, button, icon, selectRun, newMission, showView, dateLabel, humanize, modelReady, refreshStatus, loadRuns});
window.ariadneRoutines?.attach({state, api, node, button, icon, openArtifact, downloadArtifact, confirmAction, toast, dateLabel, bytes, humanize});
window.ariadneOperations?.attach({state, api, node, button, openArtifact, newMission, showView, resizeComposer});
window.ariadneWorkroom?.attach({state, api, node, button, openArtifact, selectRun, showView, toast});
window.ariadneProjectKits?.attach({state, api, node, button, openArtifact, showView, toast,
  openProject:id => openLinkedView("workroom", () => window.ariadneWorkroom?.openProject(id))});
window.ariadneOperationWorkflows?.attach({state, api, node, button, showView, setWorkflowTab, confirmAction, toast});
window.ariadneVenture?.attach({state, api, node, button, openArtifact, showView, confirmAction, toast});
window.ariadneQQQ?.attach({state, api, node, button, openArtifact, confirmAction, toast});
window.ariadneOptions?.attach({state, api, node, button, openArtifact, confirmAction, toast});
window.ariadneOptionsReview?.attach({state, api, node, button, openArtifact});
window.ariadneRevenue?.attach({state, api, node, button, openArtifact, confirmAction, toast});
window.ariadnePerception?.attach({state, api, node, button});
window.ariadneVision?.attach({state, api, node, button});
window.ariadneHandoff?.attach({state, api, node, button, openArtifact, downloadArtifact, showView, confirmAction, toast});
window.ariadneClientHandoff?.attach({state, api, node, button, openArtifact, downloadArtifact, confirmAction, toast,
  openProjectKit:id => openLinkedView("project-kits", () => window.ariadneProjectKits?.selectIntake(id))});
let serviceDeskMode = "real";
async function loadServiceDesk() {
  if (serviceDeskMode === "demo") await window.ariadneHandoff?.load();
  else await window.ariadneClientHandoff?.load();
}
async function selectServiceDesk(mode) {
  if (mode === "demo") window.ariadneClientHandoff?.leave();
  serviceDeskMode = mode;
  const real = mode === "real";
  $("#client-handoff-content").hidden = !real;
  $("#handoff-content").hidden = real;
  $("#refresh-client-handoff").hidden = !real;
  $("#refresh-handoff").hidden = real;
  for (const [id, selected] of [["#handoff-real-mode", real], ["#handoff-demo-mode", !real]]) {
    $(id).setAttribute("aria-pressed", String(selected));
    $(id).classList.toggle("button-primary", selected);
    $(id).classList.toggle("button-secondary", !selected);
  }
  if (state.authenticated) await loadServiceDesk();
}
$("#handoff-real-mode").addEventListener("click", () => selectServiceDesk("real").catch(error => toast(error.message, true)));
$("#handoff-demo-mode").addEventListener("click", () => selectServiceDesk("demo").catch(error => toast(error.message, true)));
if (typeof location !== "undefined" && new URLSearchParams(location.search).get("view") === "handoff") showView("handoff");
if (typeof location !== "undefined" && new URLSearchParams(location.search).get("view") === "workroom") showView("workroom");
if (typeof location !== "undefined" && new URLSearchParams(location.search).get("view") === "project-kits") showView("project-kits");
if (typeof location !== "undefined" && ["vision", "perception"].includes(new URLSearchParams(location.search).get("view"))) showView(new URLSearchParams(location.search).get("view"));
if (state.token) connect(); else $("#access-token").focus();
