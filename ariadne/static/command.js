"use strict";

// A deterministic view over the app's existing local state. No animation loop,
// model request or background public retrieval belongs in this presentation.
(() => {
  const activeStates = new Set(["queued", "running", "waiting_approval", "awaiting_approval", "pending", "waiting", "verifying"]);
  const reviewStates = new Set(["waiting_approval", "awaiting_approval"]);
  let app = null, briefingData = null, briefingError = "", lastSync = null;
  let connectionFailed = false, briefingPending = null, generation = 0, refreshing = false;

  function snapshot(state, briefing = null, failed = false) {
    const status = state.status || {}, wake = status.wake || {};
    const runs = (state.runs || []).map(run => run.id === state.detail?.run?.id ? {...run, ...state.detail.run} : run);
    if (state.detail?.run && !runs.some(run => run.id === state.detail.run.id)) runs.push(state.detail.run);
    const active = runs.filter(run => activeStates.has(run.status));
    const review = runs.filter(run => reviewStates.has(run.status));
    const running = runs.filter(run => ["running", "verifying"].includes(run.status));
    const queued = runs.filter(run => run.status === "queued");
    const installed = Boolean(status.ollama?.available && (status.ollama.models || []).some(model => model.name === status.model || model.name === `${status.model}:latest`));
    const reported = Number.isSafeInteger(status.active_runs) && status.active_runs >= 0 ? status.active_runs : active.length;
    const result = {
      phase: "standby", label: "Standing by", detail: "No missions in progress. Give Ariadne a directive.",
      activeCount: Math.max(reported, active.length), reviewCount: review.length,
      model: status.model || "Not selected", modelNote: installed ? "Installed · local inference" : status.ollama?.available ? "Selected model is unavailable" : "Local runtime unavailable",
      mic: state.recording ? "Browser recording" : state.transcribing ? "Transcribing locally" : wake.active ? wake.speaking ? "Speaking locally" : wake.suppressed ? "Mic · playback muted" : `${wake.mode === "browser" ? "Browser" : "PC"} mic · ${String(wake.state || "active").replaceAll("_", " ")}` : "Microphone off",
      network: status.internet?.config?.enabled === true ? status.internet.connection_verified === true ? "Public web · recently verified" : "Public web · unverified" : "Web off · saved evidence",
      nextBrief: "Not loaded", briefNote: "Open Daily brief or refresh this overview.", firstReviewId: review[0]?.id || null,
    };
    if (!state.authenticated) {
      result.phase = "locked"; result.label = "Console locked"; result.detail = "Pair this browser tab with your local server.";
      result.activeCount = null; result.reviewCount = null;
      result.model = "Not connected"; result.modelNote = "Pair this browser tab to inspect the local model.";
      result.mic = "Status unavailable"; result.network = "Status unavailable";
    } else if (failed) {
      result.phase = "unreachable"; result.label = "Server unreachable"; result.detail = "Last confirmed values are shown. Check the local server and refresh.";
    } else if (!installed) {
      result.phase = "unavailable"; result.label = "Model setup needed"; result.detail = "Open Settings to select an installed local model. Local records remain available.";
    } else if (state.creating) {
      result.phase = "submitting"; result.label = "Submitting directive"; result.detail = "Waiting for the local server to confirm the mission.";
    } else if (status.paused) {
      result.phase = "paused"; result.label = "Autonomy paused"; result.detail = "Queued missions are held. A current step may finish before execution pauses.";
    } else if (review.length && !running.length) {
      result.phase = "review"; result.label = "Your review is needed"; result.detail = `${review.length} ${review.length === 1 ? "mission is" : "missions are"} waiting for an action decision.`;
    } else if (running.length) {
      result.phase = "executing"; result.label = running.some(run => run.status === "verifying") ? "Verifying outputs" : "Mission executing";
      result.detail = running[0].title || "The local worker reports a running mission.";
    } else if (queued.length) {
      result.phase = "queued"; result.label = "Mission queued"; result.detail = `${queued.length} ${queued.length === 1 ? "directive is" : "directives are"} waiting for the local worker.`;
    } else if (result.activeCount) {
      result.phase = "active"; result.label = "Missions active"; result.detail = "The server reports active missions. Open the mission log for their recorded state.";
    } else if (state.recording) {
      result.phase = "recording"; result.label = "Recording your directive"; result.detail = "Browser microphone input is active. Stop recording to transcribe locally.";
    } else if (state.transcribing) {
      result.phase = "transcribing"; result.label = "Transcribing locally"; result.detail = "Processing the recorded audio on your server.";
    } else if (wake.active && wake.speaking) {
      result.phase = "speaking"; result.label = "Speaking locally"; result.detail = "PC voice playback is active. Wake detection is suppressed during playback.";
    } else if (wake.active && wake.state === "awaiting_command") {
      result.phase = "listening"; result.label = "Ready for your spoken directive"; result.detail = "The PC microphone is in its wake-word command window.";
    } else if (wake.active && wake.state === "listening" && !wake.suppressed) {
      result.phase = "listening"; result.label = "Listening for Ariadne"; result.detail = "PC wake-word listening is active. No idle language-model calls.";
    }
    if (state.authenticated && briefing?.config) {
      const cfg = briefing.config;
      if (cfg.paused) { result.nextBrief = "Held for review"; result.briefNote = cfg.pause_reason || "Review the previous briefing before resuming its schedule."; }
      else if (!cfg.enabled) { result.nextBrief = "Not scheduled"; result.briefNote = "Enable the schedule in Daily brief."; }
      else if (status.paused) { result.nextBrief = "Autonomy paused"; result.briefNote = "Scheduled work is held until autonomy resumes."; }
      else {
        const due = new Date(briefing.next_due_at || "invalid");
        if (!Number.isNaN(due.getTime()) && due.getTime() <= Date.now()) {
          result.nextBrief = "Refresh schedule";
          result.briefNote = "The last observed due time has passed. Refresh this overview to read the current schedule.";
        } else if (!Number.isNaN(due.getTime())) {
          try {
            result.nextBrief = new Intl.DateTimeFormat(undefined, {hour:"2-digit", minute:"2-digit", hour12:false, timeZone:cfg.timezone}).format(due);
            const region = (cfg.region?.name || "Region needed").replace(/ · PC position$/, "");
            result.briefNote = `${new Intl.DateTimeFormat(undefined, {month:"short", day:"numeric", timeZone:cfg.timezone}).format(due)} · ${cfg.timezone}\n${region}`;
          } catch { result.nextBrief = "Time unavailable"; result.briefNote = "Open Daily brief to inspect its saved schedule."; }
        } else { result.nextBrief = "Time unavailable"; result.briefNote = "Open Daily brief to inspect its saved schedule."; }
      }
    }
    return result;
  }

  function put(id, value) {
    const element = document.getElementById(id);
    const text = value === null || value === undefined ? "—" : String(value);
    if (element && element.textContent !== text) element.textContent = text;
  }
  function render() {
    if (!app) return;
    document.body?.classList.toggle("command-motion-visible", Boolean(app.state.authenticated && app.state.view === "missions" && !document.hidden));
    const data = snapshot(app.state, briefingData, connectionFailed);
    const view = document.getElementById("view-missions");
    if (view) view.dataset.selected = app.state.selectedId ? "true" : "false";
    const presence = document.getElementById("command-presence");
    if (presence) { presence.dataset.state = data.phase; presence.setAttribute("aria-label", data.label); }
    put("command-state", data.label); put("command-state-detail", data.detail);
    put("command-phase-announcement", data.label);
    put("command-model", data.model); put("command-model-note", data.modelNote);
    put("command-mic", data.mic); put("command-network", data.network);
    put("command-active", data.activeCount); put("command-review", data.reviewCount);
    put("command-next-brief", data.nextBrief); put("command-brief-note", briefingError && !briefingData ? briefingError : data.briefNote);
    put("command-sync", !app.state.authenticated ? "Not connected" : lastSync ? `${connectionFailed ? "Last confirmed" : "Checked"} ${lastSync.toLocaleTimeString(undefined, {hour:"2-digit", minute:"2-digit", second:"2-digit"})}` : "Waiting for local status");
    put("command-log-count", app.state.authenticated ? `${app.state.commandAllMissions ? app.state.runs.length : Math.min(4, app.state.runs.length)} of ${app.state.runs.length} recorded ${app.state.runs.length === 1 ? "mission" : "missions"}` : "Local mission history");
    const review = document.getElementById("command-review-open");
    if (review) { review.disabled = !app.state.authenticated || !data.firstReviewId; review.setAttribute("aria-label", data.firstReviewId ? `Review ${data.reviewCount} waiting ${data.reviewCount === 1 ? "mission" : "missions"}` : "No missions awaiting review"); }
    const refresh = document.getElementById("command-refresh");
    if (refresh) { refresh.disabled = !app.state.authenticated || refreshing; refresh.setAttribute("aria-busy", String(refreshing)); }
    const fresh = document.getElementById("command-new-mission");
    if (fresh) fresh.disabled = !app.state.authenticated;
  }

  function briefing(data) { briefingData = data; briefingError = ""; render(); }
  async function readBriefing() {
    if (!app?.state.authenticated) return;
    if (briefingPending) return briefingPending;
    const current = generation;
    // This endpoint reads local settings/cache only. It does not refresh public
    // sources, generate a briefing, or perform a model call.
    const request = (async () => {
      try {
        const data = await app.api("/briefing");
        if (current === generation && app.state.authenticated) briefing(data);
      } catch {
        if (current === generation && app.state.authenticated) { briefingError = "Briefing status unavailable. Open Daily brief to retry."; render(); }
      }
    })();
    briefingPending = request;
    try { await request; } finally { if (briefingPending === request) briefingPending = null; }
  }
  function status() { lastSync = new Date(); connectionFailed = false; render(); }
  function connectionLost() { connectionFailed = true; render(); }
  function locked() { generation += 1; briefingData = null; briefingError = ""; briefingPending = null; lastSync = null; connectionFailed = false; render(); }
  async function connected() { generation += 1; briefingData = null; briefingError = ""; briefingPending = null; status(); await readBriefing(); }
  async function refresh() {
    if (!app?.state.authenticated || refreshing) return;
    refreshing = true; render();
    try { await Promise.allSettled([app.refreshStatus(), app.loadRuns(), readBriefing()]); }
    finally { refreshing = false; render(); }
  }
  function attach(helpers) {
    if (app) return;
    app = helpers;
    document.addEventListener("visibilitychange", render);
    document.getElementById("command-refresh")?.addEventListener("click", refresh);
    document.getElementById("command-new-mission")?.addEventListener("click", app.newMission);
    document.getElementById("command-review-open")?.addEventListener("click", async () => {
      const current = snapshot(app.state, briefingData, connectionFailed).firstReviewId;
      if (!current || !app.state.authenticated) return;
      await app.selectRun(current);
      document.querySelector("#approval-list button")?.focus();
    });
    render();
  }
  window.ariadneCommand = Object.freeze({attach, render, snapshot, status, connectionLost, connected, locked, briefing, refresh});
})();
