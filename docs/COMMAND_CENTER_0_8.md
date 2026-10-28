# Ariadne command center

The 0.8 interface presents Ariadne as a local operating environment. Overview combines the live system presence, current work, decisions, next briefing, model status, a directive station and the mission ledger. It replaces the persistent conversation sidebar with horizontal navigation and an **All systems** directory.

## Direct the work

Enter a directive, choose a specialist or automatic routing, and select **Launch mission**. Expected output checks remain available in their disclosure. The local microphone and reference-library controls remain beside the launch action. Ctrl+Enter launches the form on supported keyboards. Voice recognition and the “Ariadne” PC wake word keep their existing settings.

The presence displays real reported states: locked or unavailable, autonomy paused, a directive being submitted, a queued or executing mission, review required, local speech activity, or standby. Listening means the saved PC microphone state is active; it does not claim the language model is generating while idle. Motion is limited to actual work, recording, transcription or speech. It stops in hidden tabs, outside Overview, and for reduced-motion preferences. Decorative geometry is not a thought trace or an audio-level measurement.

The cards distinguish active work from missions awaiting an action decision. The review shortcut opens a waiting mission and focuses its approval controls. The next briefing uses its configured time zone and saved schedule, with a dated region label. An expired saved schedule says to refresh; it does not keep presenting a past time as upcoming. The overview performs one local briefing-status read on connection or manual refresh. It never refreshes public sources or generates a report merely to draw the interface.

## Inspect a mission

Choose an entry in the mission ledger. A dossier presents the directive and Ariadne's output alongside execution state, the step limit, verification and any precise approval request. Completed missions start with their execution trail collapsed; active missions start with it open. You can toggle it, and later polls preserve your choice. Approval arguments, scope, fingerprint, **Approve once**, **Reject**, **Cancel** and global **Pause autonomy** keep their existing behavior.

Opening a dossier starts at the top. Later results follow only when the result was in view and you have not scrolled away; the interface does not jump to the page footer or history ledger. **Overview**, the Ariadne logo and **New direction** return to the directive station without cancelling background work. Recent history initially shows four missions; **Show all** exposes all loaded history.

## Navigate the environment

The primary navigation offers Overview, Agents, Daily brief, Workflows and Workspace. All systems also includes Internet, Knowledge, Survival, Memory, Schedules, Capabilities and Settings. The directory is hidden and inert when closed; it traps keyboard focus while open and returns focus on Escape or dismissal. The main application starts inert while unpaired. Locking a browser tab keeps the existing server and microphone preferences; it does not stop the appliance.

Phone layouts stack the command deck, metrics, directive and ledger. Resource views and approvals remain reachable. There is one main page scroller; opening an execution disclosure or an explicit dialog can add its normal bounded content scrolling.

## Local operation and limits

Fonts, CSS, JavaScript and SVG geometry are served locally. There are no new frontend packages, remote fonts, CDN requests, idle language-model calls or public polling. Existing local status and mission polling are reused. No measured energy saving is claimed. The UI changes do not expand agent authority or change Internet, approval, scheduler, model, source-folder or OS settings.

Validation artifacts and the source-package scope are recorded in `dist/DELIVERY-0.8.md` and `dist/release-evidence-0.8.json`. A source package uses the existing bundled interpreter; it is not a complete new runtime/model appliance image.
