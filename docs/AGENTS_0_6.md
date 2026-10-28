# Ariadne 0.6: agents, local voice and the morning brief

The Agents console exposes 16 specialist profiles: coordinator, operations, briefing, research, knowledge, business, data, documents, developer, mechanical, electronics, geospatial, media, automation, preparedness and mathematics. Select a profile in the mission composer or leave Auto selected. Auto uses deterministic keyword routing, with no routing model call. A mixed task can use the coordinator.

These are actual enforced tool scopes and instructions around one bounded local worker. They do not allocate 16 language models or poll models while idle. The installed capability configuration still determines backend readiness. Existing approvals, pause, cancellation, step/context limits and interrupted/uncertain-result stops remain active. Profile assignment is stored on each mission; old missions use the coordinator. A specialist cannot expand its own allowlist or delegate around an approval.

The briefing agent has a read-only `briefing_read` tool for existing status and the latest completed brief. Returned report bytes are checked against the saved SHA-256 before use. This tool never refreshes public sources or creates a report. A model's summary of that evidence still requires ordinary model inference.

## Voice

Open Agents and select **Start PC microphone**. Say “Ariadne” followed by a command, or say the word alone and then speak within 12 seconds. The native Windows listener loads the pinned local Sherpa recognizer once. It does not save ambient audio or call a language model while waiting; speech recognition itself still consumes CPU and memory. Commands launch the same scoped missions as the composer. Responses and approval requests can be spoken locally through Windows SAPI.

Start/stop and coarse listening state remain visible in the console. “Ariadne stop listening” stops the session and disables saved startup listening. Without the startup option, a session expires after eight hours. The explicit **Keep listening until stopped, and resume when Ariadne starts** option saves PC listening across server restarts and removes that expiry. A new box starts with its microphone off. Spoken requests cannot authenticate a speaker, and a nearby person or recording may issue an otherwise permitted command. Existing sensitive-action approvals still apply. Saying only “Ariadne” receives a local “Listening.” acknowledgement before its 12-second command window; a combined wake word and request launches directly.

See [wake voice details](WAKE_VOICE.md) for tests and remaining acoustic qualification limits. Generated speech was recognized through the real installed ASR, and actual PC capture reached listening state. A human pronunciation/room-noise/echo trial remains necessary on the intended input device.

## Daily briefing

The Daily brief console configures a 06:00 America/Chicago daily clock, including DST, and provides an immediate report button. Reports aggregate recorded Ariadne activity, pending/uncertain approvals, schedules, fixed procedures and imported metadata. They cannot see unlogged activity in other applications.

Weather, forecast, official alerts, nearby earthquake impacts and configured official regional news feeds use a bounded public-source collector only when enabled. This separate worker does not weaken the main local server's offline network policy. Without internet, the same report runs against its dated cache, flagging unavailable/stale evidence. New weather/news cannot be obtained offline. These sources do not cover every regional event.

Reports are fixed aggregation with zero language-model calls, unique Markdown/JSON artifacts, SHA-256 receipts and durable idempotency. A failed/interrupted scheduled day is held for review. Global autonomy pause also holds the brief. The server timer and optional Windows task coordinate through the same guarded cross-process execution lock and daily receipt.

The Windows task preview can be generated with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\setup_morning_task.ps1
```

Preview writes only inside this box. **`-Install` registers a Windows Task Scheduler entry outside the box**; it requires the owner's exception to a Yikes-only change scope. The task uses current-user InteractiveToken, least privilege and no stored password; it requests WakeToRun at local 6 AM, invokes only the fixed bundled-Python briefing CLI and does not start the web server or language model. It requires the owner to remain logged in and Windows/hardware to permit wake timers. It cannot turn on a shut-down PC. No power-plan or BIOS settings are changed. A moved box needs its own new task path.

The companion 6 AM review in this chat is separate from the offline appliance. It requires the desktop app and local computer to be running; its account/model requirements do not apply to the local fixed report. It checks the due receipt before generation and respects failure holds.

## Region and position

The Daily brief page can explicitly read this PC's Windows location service or request a browser position using its normal location permission. Neither performs an IP lookup, enables OS privacy settings, or runs a background tracker. Windows device detection on the current PC reported an estimate with a 204-meter accuracy radius; that does not establish exact physical position. Private position metadata stays in the owner database and is excluded from source exports. Weather refreshes send the configured point to the NWS service. The St. Louis fallback is the U.S. Census city internal reference point, clearly separate from a device measurement. Review the coordinates and save the briefing settings when changing the region through the UI.

See [daily briefing details](DAILY-BRIEFING.md) for source coverage, clock behavior and CLI commands.
