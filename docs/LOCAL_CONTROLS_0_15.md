# Selected local Home Assistant controls · 0.15

Ariadne now has a fixed `home_control` adapter for a powered local Home Assistant installation. It can import/read dated state snapshots, refresh a selected local server, propose exact actions, and perform individually approved light/switch on/off or scene activation calls. It uses the existing connector transport and approval engine. It installs no Home Assistant server, connects no real device automatically, and adds no model, resident worker or network exception.

## What works offline

`catalog`, `status`, `propose` and `receipt` use local state only. `import_snapshot` reads an owner-provided `/api/states` JSON export inside the workspace and preserves the explicit observation time and exact input-file SHA-256. The adapter selects only the configured entity IDs and a small set of scalar attributes. Missing entities, `unknown`/`unavailable` states, old retrievals and old entity updates remain explicit. Reading a cache never renews its timestamps or initiates a connection.

A local Home Assistant server and its devices must be powered for a refresh or action to work. Ariadne's default process policy allows numeric loopback only: `127.0.0.1` or `::1`. A separately operated LAN installation requires the owner's existing network-mode configuration; this adapter does not bypass the offline policy. Its own endpoint validation permits only numeric loopback or RFC1918/ULA private addresses, exact Home Assistant paths, and one shared origin. Hostnames, public addresses, link-local metadata addresses, redirects, URL credentials and query strings are rejected.

## Configure a named installation

1. Install/manage Home Assistant separately and identify the exact entities to expose. No server, devices or credentials are configured by this release.
2. Review `config/examples/control-connectors.json` and merge the required connector entries into private `data/connectors.json`; preserve any existing entries. Adjust the numeric address and port to the chosen server. Do not put credentials in a URL or fixed header.
3. Review `config/examples/control-home-assistant.json` and save the selected scope as private `data/local-controls.json`. The example is disabled. Its entities and action IDs are examples, not observations of this owner's hardware.
4. Configure the full `Authorization` header value in the connector's environment reference (`ARIADNE_CONNECTOR_HA_AUTH`, value beginning with `Bearer `) for the actual Ariadne process. The application can load referenced, prefixed variables from private `data/connector-secrets.json`, including in the detached host: `{"schema":1,"environment":{"ARIADNE_CONNECTOR_HA_AUTH":"Bearer YOUR_TOKEN"}}`. Use a real token only in that private owner file; no real credential is supplied by the example. Existing nonempty process values take precedence. Do not persist tokens in source examples or model arguments. Missing credentials fail closed.
5. Restart the application after connector changes because connectors are loaded at startup. Offline snapshot imports can be qualified while actions remain disabled. Only then deliberately set `enabled` to true for refresh and individually approved actions.

An omitted `local-controls.json` produces a disabled, unconfigured adapter. Configuration supports at most 64 exact entities and 32 fixed actions. Only `light.turn_on`, `light.turn_off`, `switch.turn_on`, `switch.turn_off` and `scene.turn_on` are accepted, with matching entity domains. Only fixed integer `brightness_pct` (0–100) and `transition` (0–10 seconds) are supported for light-on. Locks, alarms, covers, scripts, notifications, arbitrary services, area targets and caller-supplied action parameters are outside this adapter.

## Tool calls

```json
{"action":"catalog"}
{"action":"status"}
{"action":"import_snapshot","source_path":"devices/states.json","observed_at":"2026-10-29T12:00:00+00:00"}
{"action":"refresh"}
{"action":"propose","action_id":"desk_work_light"}
{"action":"apply","action_id":"desk_work_light","request_id":"desk-light-20261029-001"}
{"action":"receipt","request_id":"desk-light-20261029-001"}
```

The import path must be relative to `data/workspace`; the example observation time is deliberately dated and must never be relabeled as current. `config/examples/control-states-demo.json` is synthetic demo data. To try it, copy it into a selected workspace folder and use its actual stated observation time. A saved scope is required before imported entity details can appear; an unconfigured adapter does not infer a device allowlist from a file.

## Action authority and receipts

`apply` uses the existing owner-approval gate and independently requires its current approval fingerprint. It binds the arguments, exact scope, fixed connectors and resolved credential scope. Changing a target, fixed body, credential or configuration requires a new approval. The application store must be bound, autonomy must be unpaused, actions must be enabled, and cancellation is checked before dispatch. The main settings write lease serializes a pause against the one bounded connector dispatch; a pause committed first stops it, while a later pause waits for that existing dispatch to finish.

A private SQLite receipt is committed as uncertain **before** any POST. A completed invocation can be read again without another POST. A network error, invalid/truncated response, redirect, interrupted process or lost response keeps the request uncertain and prevents automatic replay. A known cancellation/revocation before sending records `not_sent` and consumes that request ID. Reusing a request ID with different inputs fails. Reconciliation is manual: inspect Home Assistant and physical state, preserve the uncertain receipt, and issue a new expressly reviewed instruction only when appropriate. There is no automatic retry, fallback action or reconciliation POST.

`accepted` means Home Assistant acknowledged the service call; it is not independent confirmation that a physical device changed. The adapter returns a response digest rather than broad remote state from a service response. The local journal is capped at 10,000 requests; source/config/cache input is bounded at 64 KiB. Existing connector timeouts, secret redaction, TLS validation and redirect refusal remain in force.

The generic connector tools remain independently owner-approved capabilities. This adapter does not broaden them or make their POST bodies automatic. Use the named `home_control` action when you need its stricter entity binding and durable no-replay receipt.

## Private state and qualification

Private `data/local-controls.json` holds scope, `data/local-controls.sqlite3` holds selected observations and action receipts, and `data/connectors.json` holds endpoint/credential references. They are owner state, excluded from factory/source examples. Include them in checked private backups when present. Neither model weights nor a new database service is required.

The isolated native suite exercises actual loopback GET/POST and redirect refusal, fixed body/endpoint validation, exact source hashing, staleness, input/link boundaries, approval and credential changes, global pause serialization, reserved-gap revocation, concurrent request suppression, process interruption and uncertain-receipt replay prevention. This qualifies the adapter contract and local transport fixture. No real Home Assistant installation, sensor or physical device has been connected or qualified by these tests.

Primary protocol evidence checked 29 October 2026: [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/) documents authenticated `/api/states` reads and `/api/services/<domain>/<service>` calls. The latter returns changed-state lists; a state write only updates Home Assistant's representation and does not control a device, so this adapter never uses `/api/states/<entity_id>` writes. [Service action implementation](https://developers.home-assistant.io/docs/dev_101_services/) explains registered action semantics. These are protocol integrations with upstream Home Assistant, not vendored upstream source code or an installation of its runtime.
