# Ariadne control and automation repository scan

Checked **October 29, 2026**, at **2026-10-29T17:25:53Z**. This is a targeted scan of **14 detailed candidates**, with two adjacent frameworks/libraries and discovery results considered separately. It is not an exhaustive scan of all GitHub, a vulnerability audit, or a claim that any new capability is installed. Official repository identities, archive flags, release redirects, selected source files, licenses and publisher documentation were inspected. No software was installed or executed; no device, camera, microphone, account, service, network setting or E: access was used.

The best next increment is a **small device gateway with read-only telemetry**, followed by checked system inventory. Reuse Ariadne's existing local model, durable receipts, fixed Node-RED routes, loopback Playwright and reviewed MCP permissions. A second agent platform should earn its upkeep through a specific task benchmark before becoming another permanent service. These priorities are engineering judgments, not measured performance or security rankings.

## Recommended order

1. **Home Assistant read gateway:** selected device IDs, timestamps, unavailable/stale labels and bounded history. This creates a useful local awareness layer without immediately granting physical control.
2. **Mosquitto plus selected ESPHome or Zigbee devices:** deterministic telemetry/events through authenticated, direction-specific topic permissions. Choose hardware only after identifying an actual sensor/control need.
3. **DSC Get/Test inventory:** fixed reviewed resources can report system drift and produce a reviewable configuration plan. A resource's read-like operation name is not proof its implementation has no side effects.
4. **Extend the installed Playwright adapter:** local business apps, draft forms and artifact/UI verification already fit the current box. Compare pywinauto and FlaUI only for a named native app that lacks a usable API.
5. **Optional fleet/service infrastructure:** OpenTelemetry when several services justify shared measurements; Temporal only for a demonstrated durable multi-service requirement. Keep OpenHands as an isolated coding experiment rather than replacing Ariadne's operating core.

## Current repository observations

Version labels below are what the official latest-release redirect or download page exposed during this scan. Exact release timestamps were not machine-verified; missing timestamps remain null in the JSON. A latest tag is not an approved deployment pin. Default-branch READMEs can describe work newer than the released artifact.

| Project | Observed version | License inspected | Decision |
| --- | --- | --- | --- |
| [Home Assistant Core](https://github.com/home-assistant/core) | [2026.10.4](https://github.com/home-assistant/core/releases/tag/2026.10.4) | Apache-2.0 | recommended candidate |
| [ESPHome](https://github.com/esphome/esphome) | [2026.10.1](https://github.com/esphome/esphome/releases/tag/2026.10.1) | MIT for Python/other code; GPL-3.0 for C++ runtime | recommended candidate |
| [Eclipse Mosquitto](https://github.com/eclipse-mosquitto/mosquitto) | [2.1.2](https://mosquitto.org/download/) | EPL-2.0 OR BSD-3-Clause (Eclipse Distribution License 1.0) | recommended candidate |
| [Open Home Foundation Matter.js Server](https://github.com/matter-js/matterjs-server) | [v1.5.5](https://github.com/matter-js/matterjs-server/releases/tag/v1.5.5) | Apache-2.0 | conditional candidate |
| [Legacy Python Matter Server](https://github.com/matter-js/python-matter-server) | [8.1.2](https://github.com/matter-js/python-matter-server) | Apache-2.0 | reject archived |
| [Zigbee2MQTT](https://github.com/Koenkk/zigbee2mqtt) | [2.14.2](https://github.com/Koenkk/zigbee2mqtt/releases/tag/2.14.2) | GPL-3.0 | conditional candidate |
| [pywinauto](https://github.com/pywinauto/pywinauto) | [0.6.9](https://github.com/pywinauto/pywinauto/releases/tag/0.6.9) | BSD-3-Clause | conditional candidate |
| [FlaUI](https://github.com/FlaUI/FlaUI) | [v5.0.0](https://github.com/FlaUI/FlaUI/releases/tag/v5.0.0) | MIT | conditional candidate |
| [Playwright](https://github.com/microsoft/playwright) | [v1.64.1](https://github.com/microsoft/playwright/releases/tag/v1.64.1) | Apache-2.0 | already installed extend |
| [Microsoft DSC](https://github.com/PowerShell/DSC) | [v3.3.0](https://github.com/PowerShell/DSC/releases/tag/v3.3.0) | MIT | recommended candidate |
| [OpenHands Agent Canvas](https://github.com/OpenHands/OpenHands) | [v1.34.0](https://github.com/OpenHands/OpenHands/releases/tag/v1.34.0) | MIT for inspected core repository; editions/providers/dependencies separate | conditional candidate |
| [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | [v2.3.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0) | MIT | conditional candidate |
| [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector) | [v0.164.0](https://github.com/open-telemetry/opentelemetry-collector/releases/tag/v0.164.0) | Apache-2.0 | conditional candidate |
| [Temporal](https://github.com/temporalio/temporal) | [v1.32.1](https://github.com/temporalio/temporal/releases/tag/v1.32.1) | MIT | defer scale |

## Candidate details

### Home Assistant Core

**Incremental ability:** Local device state and reviewed automation. Local integrations can work offline; cloud integrations do not. VM/container and local network/devices remain powered.

**Fit and proposed adapter:** Existing fixed Node-RED and MCP supply a transport but no physical-device inventory. Start with a localhost read gateway for selected entity IDs, timestamps and unavailable states; later exact service/parameters approval.

**Operating burden:** Separate VM/container, recorder retention, backups and integration updates; no added language model required.

**Qualification boundaries:** Every authenticated HA user is an owner-like trust boundary under current security policy. MCP defaults expose all LLM APIs; select APIs/entities instead of importing broad authority. [Release/source](https://github.com/home-assistant/core/releases/tag/2026.10.4), [License/source](https://github.com/home-assistant/core/blob/dev/LICENSE.md), [Publisher evidence 1](https://www.home-assistant.io/security/), [Publisher evidence 2](https://www.home-assistant.io/integrations/mcp_server/), [Publisher evidence 3](https://www.home-assistant.io/installation/).

### ESPHome

**Incremental ability:** Purpose-built local sensors and low-risk device endpoints. Flashed firmware communicates locally; offline rebuild also needs the cached toolchain and exact component sources.

**Fit and proposed adapter:** Ariadne has no commissioned ESP hardware adapter; reuse HA gateway rather than second device authority path. Prototype temperature, environment or power telemetry via encrypted native API; device-specific bounded commands only after simulation.

**Operating burden:** Requires suitable microcontroller/sensors, firmware/toolchain archive and firmware updates; device hardware draws power.

**Qualification boundaries:** Native API encryption must be configured, not inferred. External components/lambdas compile code; unreviewed YAML is not harmless configuration. Physical installation and actuation need their own engineering review. [Release/source](https://github.com/esphome/esphome/releases/tag/2026.10.1), [License/source](https://github.com/esphome/esphome/blob/dev/LICENSE), [Publisher evidence 1](https://new.esphome.io/components/api/).

### Eclipse Mosquitto

**Incremental ability:** Local MQTT event bus and bounded device telemetry. Native Windows packages are available; local broker/client traffic needs no WAN after provisioning.

**Fit and proposed adapter:** Existing Node-RED can consume MQTT; Ariadne currently exposes only fixed HTTP routes, so arbitrary flows are not already approved. Gateway subscribes to selected state topics; write topics have separate credentials/ACLs and expiring exact command IDs.

**Operating burden:** One small local broker service plus bounded retained/persistent storage; no inference required for routing.

**Qualification boundaries:** Local-only anonymous defaults are for tests; authenticated listeners and topic ACLs are needed for a connected system. Retained messages and redelivery can repeat unsafe commands unless command expiry/idempotency is implemented. [Release/source](https://mosquitto.org/download/), [License/source](https://github.com/eclipse-mosquitto/mosquitto/blob/master/LICENSE.txt), [Publisher evidence 1](https://github.com/eclipse-mosquitto/mosquitto/blob/master/README.md), [Publisher evidence 2](https://mosquitto.org/documentation/authentication-methods/).

### Open Home Foundation Matter.js Server

**Incremental ability:** Current Matter controller behind Home Assistant. Local commissioned-device control is useful offline; Thread/BLE/IPv6/mDNS and commissioning trust data still need qualification.

**Fit and proposed adapter:** Prefer HA integration instead of adding a second device/fabric authority store to Ariadne. Use current HA-supported Matter server and read selected attributes via the HA gateway; preserve fabric/storage backup.

**Operating burden:** Node.js server and supported radio/border-router/network setup; extra credential-bearing state and upgrade/migration burden.

**Qualification boundaries:** README supports Node 22.13+,24.x or26.x; not a claim of a native Windows hardware qualification. The new server can migrate Python-server state on first start; do not try migration against owner devices during research. matter.js library itself does not certify a product. [Release/source](https://github.com/matter-js/matterjs-server/releases/tag/v1.5.5), [License/source](https://github.com/matter-js/matterjs-server/blob/main/README.md), [Publisher evidence 1](https://github.com/matter-js/matterjs-server), [Publisher evidence 2](https://www.home-assistant.io/blog/2026/06/23/the-matter-upgrade-youve-been-waiting-for/), [Publisher evidence 3](https://github.com/matter-js/matter.js/blob/main/README.md).

### Legacy Python Matter Server

**Incremental ability:** Migration context only. Legacy local controller; not a new offline foundation.

**Fit and proposed adapter:** Do not add this archived server to Ariadne. Use its documentation only when planning a separately reviewed migration to matterjs-server.

**Operating burden:** Unsupported legacy maintenance would add avoidable burden.

**Qualification boundaries:** Repository archived June23,2026. README identifies8.1.2 as final and says updates/support have ended. [Release/source](https://github.com/matter-js/python-matter-server), [License/source](https://github.com/matter-js/python-matter-server/blob/main/README.md).

### Zigbee2MQTT

**Incremental ability:** Local Zigbee devices and sensor state. Works with local MQTT broker; Windows supported with Node and an appropriate coordinator/driver; no mandatory cloud for local devices.

**Fit and proposed adapter:** Adds radio-backed device support; reuse MQTT/HA gateway and existing approval records. Start with coordinator simulator and read telemetry; later specific allowed device commands with reconciliation.

**Operating burden:** Separate Node process, adapter firmware/device compatibility and key/coordinator backups.

**Qualification boundaries:** Needs supported coordinator and actual compatible devices; none were discovered or commissioned. Do not accept unexpected onboarding/port8080 conflicts with Ariadne. RF interference and coordinator failure are distinct from AI reliability. [Release/source](https://github.com/Koenkk/zigbee2mqtt/releases/tag/2.14.2), [License/source](https://github.com/Koenkk/zigbee2mqtt), [Publisher evidence 1](https://www.zigbee2mqtt.io/guide/getting-started/), [Publisher evidence 2](https://www.zigbee2mqtt.io/guide/adapters/), [Publisher evidence 3](https://www.zigbee2mqtt.io/guide/installation/05_windows.html).

### pywinauto

**Incremental ability:** Selected native Windows application inspection/actions. Can operate local Windows apps offline; many keyboard/mouse operations require an active desktop.

**Fit and proposed adapter:** Ariadne has loopback browser automation but no general approved native desktop controller. One app/process-identity allowlist, semantic UI selectors, short session lease, before/after evidence and explicit write approvals.

**Operating burden:** On-demand Python worker; no new model required for deterministic app tasks; UI changes create maintenance.

**Qualification boundaries:** Docs label0.6.8 while GitHub latesttag is0.6.9; exact bundled Python3.13 compatibility remains unqualified. Lock/RDP/session changes can break desktop interaction. Never route trading orders or privileged OS changes through a generic click/type tool. [Release/source](https://github.com/pywinauto/pywinauto/releases/tag/0.6.9), [License/source](https://github.com/pywinauto/pywinauto/blob/master/LICENSE), [Publisher evidence 1](https://github.com/pywinauto/pywinauto), [Publisher evidence 2](https://pywinauto.readthedocs.io/en/latest/remote_execution.html).

### FlaUI

**Incremental ability:** Alternative .NET Windows UI Automation worker. Native Win32/WinForms/WPF inspection and interaction can run locally; .NET/runtime assets must be cached.

**Fit and proposed adapter:** Alternative to pywinauto, not a reason to bundle both. Compare one problematic target app against pywinauto; fixed signed worker and selected automation IDs.

**Operating burden:** Adds .NET worker/build/runtime footprint and UI regression maintenance; choose only on measured app compatibility.

**Qualification boundaries:** UIA2/UIA3 behavior varies by target framework. Release5 removes older .NET targets and adds .NET8; this PC's target-app/session compatibility is not measured. Desktop UI access itself is broad authority; library license does not constrain it. [Release/source](https://github.com/FlaUI/FlaUI/releases/tag/v5.0.0), [License/source](https://github.com/FlaUI/FlaUI/blob/main/LICENSE.txt), [Publisher evidence 1](https://github.com/FlaUI/FlaUI/blob/main/README.md).

### Playwright

**Incremental ability:** Operate and verify local business web applications. Existing local runtime works with provisioned browser assets; live websites still require network/account access.

**Fit and proposed adapter:** Already integrated for approved loopback apps; extend fixed workflows rather than adding an unrestricted MCP browser. Read inventory screens, fill draft local forms, verify generated apps and save bounded evidence; approvals remain per write.

**Operating burden:** On-demand browser and regression tests; cache exact matching browser revision; no additional AI service by itself.

**Qualification boundaries:** Browser contexts and request routing are not an OS sandbox. Existing Ariadne local origin/path confinement must remain; do not expose generic navigation to owner sessions. Publisher's token-efficiency marketing is not a local measurement. [Release/source](https://github.com/microsoft/playwright/releases/tag/v1.64.1), [License/source](https://github.com/microsoft/playwright), [Publisher evidence 1](https://playwright.dev/docs/browsers).

### Microsoft DSC

**Incremental ability:** Declarative system inventory and approved configuration plans. Cross-platform standalone CLI; fixed resources can operate offline after provisioning.

**Fit and proposed adapter:** Ariadne has independent hosting but no general checked OS desired-state inventory. Begin with reviewed resource Get/Test only and produce a drift report; Set is a separate exact configuration approval.

**Operating burden:** On-demand command rather than a new scheduler/service; resource manifests and binaries need pinned updates.

**Qualification boundaries:** Get/Test labels do not make an arbitrary third-party resource trustworthy. Set can change OS/software/services outside Yikes; current research grants no such exception. Resource code may spawn programs or contact networks; review each fixed resource implementation. [Release/source](https://github.com/PowerShell/DSC/releases/tag/v3.3.0), [License/source](https://github.com/PowerShell/DSC), [Publisher evidence 1](https://github.com/PowerShell/DSC/blob/main/README.md), [Publisher evidence 2](https://learn.microsoft.com/en-us/powershell/dsc/overview?view=dsc-3.0).

### OpenHands Agent Canvas

**Incremental ability:** Isolated software-building experiments. Local backend is possible; offline operation also needs local model, dependency/container caches and a blocked network test.

**Fit and proposed adapter:** Substantial overlap with Ariadne agents, schedules, files, reviews and independent host; avoid second default control plane. Use only a disposable project snapshot and qualified sandbox for coding benchmark; review patches before applying.

**Operating burden:** Extra agent backend/UI/sandbox lifecycle and model inference; Docker Desktop is not currently part of the verified box.

**Qualification boundaries:** Current default-branch README warns direct no-sandbox agent server has full filesystem access. Docker recommended provider is not proof of sufficient mounts/egress/credential isolation. Do not run repository instructions, self-updates or autonomous deployment as part of this scan. [Release/source](https://github.com/OpenHands/OpenHands/releases/tag/v1.34.0), [License/source](https://github.com/OpenHands/OpenHands/blob/main/LICENSE), [Publisher evidence 1](https://github.com/OpenHands/OpenHands/blob/main/README.md), [Publisher evidence 2](https://docs.openhands.dev/openhands/usage/sandboxes/overview).

### Official MCP Python SDK

**Incremental ability:** Typed local capability gateways. SDK can serve local tools over standard transports; each actual tool controls offline behavior.

**Fit and proposed adapter:** Ariadne already uses checksum-pinned FastMCP slim4.1.4 with explicit reviewed protocol modes; do not blindly replace its client. Build narrow loopback-only business/device gateways, select tool identities and schemas, retain approved read/write policy.

**Operating burden:** Additional gateway dependency only where existing fixed HTTP is insufficient; protocol upgrades need compatibility tests.

**Qualification boundaries:** SDK examples and read-only annotations are not authorization. Currentv2 APIs differ from oldexamples; keep Ariadne's pinned protocol contract until tested. Do not enable sampling, resources, elicitation or arbitrary server registration by default. [Release/source](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0), [License/source](https://github.com/modelcontextprotocol/python-sdk), [Publisher evidence 1](https://py.sdk.modelcontextprotocol.io/).

### OpenTelemetry Collector

**Incremental ability:** Local service metrics and traces across optional engines. Collector can route telemetry locally; exporters and receivers must be explicitly configured.

**Fit and proposed adapter:** Existing receipts/logs cover single-box work; useful when multiple optional business/device services justify correlation. Local receiver/exporter only, bounded queue/retention, redact prompt/document/credential payloads; chart task success and measured latency.

**Operating burden:** One optional daemon and telemetry storage; disable unused receivers/exporters and avoid high-cardinality traces.

**Qualification boundaries:** Examples include network listeners/exporters; do not assume a private default. Observability may capture sensitive data and creates disk/CPU work. No measured savings or native resource budget established. [Release/source](https://github.com/open-telemetry/opentelemetry-collector/releases/tag/v0.164.0), [License/source](https://github.com/open-telemetry/opentelemetry-collector), [Publisher evidence 1](https://opentelemetry.io/docs/collector/configuration/).

### Temporal

**Incremental ability:** Durable multi-service business workflow orchestration. Self-hosted service is possible; single-binary development mode is not a production recovery qualification.

**Fit and proposed adapter:** Current Ariadne already has durable mission/routine receipts and fail-stop uncertainty handling. Consider only when a concrete multi-service workflow exceeds current engine; bind external action idempotency/reconciliation explicitly.

**Operating burden:** Extra production service/storage, workers, auth/TLS, schema upgrades, backups and monitoring.

**Qualification boundaries:** Workflow durability and retries do not establish exactly-once physical actions or trading orders. [Release/source](https://github.com/temporalio/temporal/releases/tag/v1.32.1), [License/source](https://github.com/temporalio/temporal), [Publisher evidence 1](https://docs.temporal.io/self-hosted-guide).

## Particularly useful discoveries

- **The old Python Matter Server is archived.** Its README identifies 8.1.2 as the final release and says updates/support have ended; the canonical repository is now `matter-js/python-matter-server`. Its successor is **`matter-js/matterjs-server`**, not an assumption that the old Python package remains supported. Home Assistant's [June 23 migration announcement](https://www.home-assistant.io/blog/2026/06/23/the-matter-upgrade-youve-been-waiting-for/) and the [archived README](https://github.com/matter-js/python-matter-server/blob/main/README.md) establish this change. No migration was attempted.
- **ESPHome has a split license:** Python/other code is MIT, while its C++ runtime is GPLv3. Treating the entire project as one permissive package would be wrong for an appliance redistributed to businesses. See the [actual license](https://github.com/esphome/esphome/blob/dev/LICENSE).
- **Home Assistant authentication is not an isolation boundary for an AI.** Its published [security policy](https://www.home-assistant.io/security/) treats authenticated users as trusted, and its [MCP defaults](https://www.home-assistant.io/integrations/mcp_server/) expose all LLM APIs unless configured otherwise. A separately enforced narrow gateway is the preferred Ariadne integration.
- **OpenHands currently exposes a direct process option with full filesystem access.** The inspected default-branch README now describes Agent Canvas and explicitly warns about that option. Merely installing a framework with a “sandbox” feature does not activate or qualify containment. See [README](https://github.com/OpenHands/OpenHands/blob/main/README.md) and [sandbox provider documentation](https://docs.openhands.dev/openhands/usage/sandboxes/overview).

## Proposed operating boundary

Keep the AI controller's offline network policy unchanged. Reach devices through separately reviewed loopback gateways with fixed destinations; do not hand the model arbitrary LAN addresses, MQTT wildcard publishes, a host shell or a whole desktop. Each gateway needs a versioned capability declaration, selected reads, secret handling outside model context, bounded time/output, and explicit owner-approved write operations.

For a write, freeze the exact device/app identity, action, parameters, configuration revision and expiry in the approval. Record the requested action, observed response and read-back state. On timeout or power failure, hold an uncertain result for reconciliation; neither MQTT redelivery nor a durable workflow engine proves exactly-once execution. Locks, alarms, valves, heat, electrical power, machinery and trading orders need separate domain qualification and must not become generic autonomous click/type commands. This is a proposed architecture, not a newly granted runtime authority.

For offline acceptance, stage all dependencies, browser revisions, native libraries, firmware sources and local device credentials first; then test cold start, useful reads, disconnected WAN behavior, stale/unavailable states, cancellation, device loss, power interruption and restore. Internet-independent control still needs electrical power and working local networking/radios. Commissioning, certificate checks, package installation and cloud integrations may require connectivity even when later local operation does not.

Costs here are qualitative architecture assessments: new daemons, UI sessions, database writes, retention, firmware upgrades and backups all have recurring costs. No new LLM is required for deterministic telemetry or fixed actions, but no watts, latency savings or monetary savings were measured. General agents still consume model inference; a local model is not free computation.

## Screened but not promoted

[LangGraph 1.2.18](https://github.com/langchain-ai/langgraph/releases/tag/1.2.18) is a maintained MIT orchestration option, but replacing Ariadne's existing durable engine has no demonstrated benefit for this increment. [matter.js 0.18.0](https://github.com/matter-js/matter.js/releases/tag/v0.18.0) is the underlying protocol library; its [README](https://github.com/matter-js/matter.js/blob/main/README.md) explicitly distinguishes using the library from product certification. Prefer the supported server/integration route for an owner appliance. The discovered [FlaUI-MCP wrapper](https://github.com/shanselman/FlaUI-MCP) was not independently qualified and is not treated as safe because it wraps a known library.

Discovery queries were `local home automation matter mqtt`, `Windows UI automation`, and `local AI agent sandbox`, followed by direct checks of established publishers. Search results also included small/new wrappers and platform-specific sandboxes; they were not installed or promoted without evidence. Popularity, archive status and recent releases are screening signals, not security attestations.

The [machine-readable inventory](repo-scan-controls-2026-10-29.json) preserves decisions, offline constraints, current tags, unknown timestamps, risks, source links and selected upstream Git blob identities. These files document research only. None of the proposed integrations was provisioned or enabled.

