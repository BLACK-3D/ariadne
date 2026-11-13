# Recording-first perception

Ariadne 0.38 adds **Awareness**, a local desk for explicitly imported observation recordings and a fictional demonstration. Open **Awareness** in the navigation, or use `?view=perception` on the running local interface. Opening the desk reads saved evidence only.

The desk processes observations already recorded in JSON. It does not collect Wi-Fi CSI/RSSI, open a camera, activate a microphone, connect to mmWave hardware, run a recognition model or control a device. The modality names in a recording describe its declared source. They do not establish installed hardware support or physical accuracy. Import, replay, the demo and saved-evidence reads require no model, network, additional package or resident service.

## Try the fictional demonstration

1. Pair with your own running installation and open **Awareness**.
2. Choose **Create synthetic demo** explicitly. This saves a bounded fixture and its receipt; simply opening the page does not create it.
3. Inspect its zones, evidence timeline and observation provenance. The fixture demonstrates supported, unknown, stale and conflicting recording evidence. It remains labeled **SYNTHETIC** and **SIMULATION**.
4. Inspect the source and canonical SHA-256 values. A verified hash establishes the identity of retained bytes, not sensor accuracy, trustworthy capture origin or current room conditions.

The demonstration is bundled at [`ariadne/resources/perception/synthetic-demo.json`](../ariadne/resources/perception/synthetic-demo.json). It contains invented observations, not recordings from the owner's environment.

## Import and replay a recording

1. Start from the public [example recording](../ariadne/resources/perception/example-recording.json) and [recording contract](../ariadne/resources/perception/recording-contract.json). Keep original source timestamps and mark invented observations `capture_origin: "synthetic"`.
2. Put the supported UTF-8 JSON file inside this installation's **Workspace**, for example `data/workspace/perception/room-session.json`. The input field takes the Workspace-relative path `perception/room-session.json`. Absolute paths, parent traversal, linked/network folders and E: are outside the permitted path boundary.
3. Enter that path under **Workspace JSON path** and choose **Import recording**. Import validates the selected file and retains its exact bytes, normalized observations, deterministic evidence and receipt. It grants no source registration or device authority.
4. Inspect the imported receipt, then choose **Replay selected recording** explicitly when wanted. Replay uses the verified retained source, rather than rereading a possibly edited Workspace file, and saves a separate receipt. Changing the Workspace file requires a new import.
5. Use **Refresh saved evidence** to inspect the current saved state. Selecting or refreshing a receipt performs no replay.

Imported and replayed observations use processing mode **REPLAY**. Their original `capture_origin` remains `live`, `synthetic` or `unknown`; a declared live capture is still unqualified owner-supplied evidence. Replaying synthetic data never converts it into real capture.

## What the evidence means

| Display | Meaning |
|---|---|
| Known | The fixed recording rules support a presence/motion value at that recording offset. It is not a live occupancy assertion. |
| Unknown | Evidence is missing, unavailable, debouncing, unqualified by the recording rules or otherwise insufficient. Missing evidence never becomes absence. |
| Stale | The source is too old under the fixed recording-time rule. Replay does not make its physical capture time newer. |
| Conflict | Available eligible sources disagree. The derived presence/motion remains unknown rather than selecting a convenient source. |
| Hash verified | The saved source, artifacts and receipt pass their integrity checks on read. Their factual accuracy and source authenticity remain unverified. |

The current rules require two consecutive matching observations from a source before accepting its state, and treat evidence older than ten seconds as stale. These are provisional deterministic rules, not calibrated physical thresholds. Quality flags, clock resets or regressions, future timestamps and upstream outages remain visible and can prevent a supported state. Sensor silence, missing frames and unavailable sources do not establish an empty room.

The timeline and zone cards refer to the **original recording timeline**. The separate API `current_zones` view computes physical source age when read and always leaves current presence and motion unknown because the source is a saved recording. Replay does not reset either source clocks or physical freshness. Repeated processing of the same source and processing mode has the same canonical evidence hash; receipt identifiers and receipt times are separate execution metadata.

## Recording contract v1

The [contract resource](../ariadne/resources/perception/recording-contract.json) and example travel with the application. Unknown fields, duplicate JSON keys, nonfinite numbers and malformed values are rejected. The principal fields are:

| Level | Required fields |
|---|---|
| Bundle | `schema_version` (1), `recording_id`, `title`, `started_at`, `duration_ms`, `sensors`, `zones`, `observations` |
| Sensor | `registered_sensor_alias`, `modality`, `producer_revision`, `zone_id` |
| Observation | `event_id`, `registered_sensor_alias`, `producer_boot_id`, `capture_origin`, `observed_at`, `sequence`, `replay_offset_ms`, `presence`, `motion`, `upstream_source_state` |

IDs use 1–64 ASCII letters, digits, underscores or hyphens, beginning with a letter or digit. Times use unambiguous UTC timestamps ending in `Z`; an unavailable observation time or sequence is explicitly `null`. Sensors select a declared zone. Allowed modalities are `wifi_csi`, `wifi_rssi`, `camera` and `mmwave`. Observations reference a declared sensor, have unique event IDs and ordered replay offsets, and increase sequence numbers within a producer boot when sequence numbers are available.

Optional bounded metadata includes producer clocks, source event IDs, quality flags, calibration revisions, artifact hashes, visible object labels and supporting observation IDs. Object labels are untrusted source text. An optional model score is explicitly uncalibrated and is never a probability. Recording v1 accepts no spatial coordinate frame or metric units, and imports cannot claim local live qualification. Calibration or model metadata does not establish independent verification.

The fixed bounds are 1,000,000 source bytes, 512 observations, 16 sensors, 16 zones and a timeline of at most 24 hours. Each derived artifact is bounded to 4,000,000 bytes; retained perception artifacts have a 32,000,000-byte budget and at most 100 accepted attempts. There is no recurring acquisition, polling or background replay. The implementation checks capacity before reserving a new attempt.

## Receipts, pause and failure handling

Every write has an explicit request ID and reviewed state revision. The global pause blocks new imports, replays and demos while saved evidence remains readable. Reused IDs cannot describe different inputs. Completed retained results are verified before reuse; failed or incomplete attempts cannot run again.

An accepted attempt reserves its ID before artifact publication. A failure or interruption holds further perception writes. Preserve the receipt, ledger and artifacts and inspect them; v1 provides no automatic retry, hold reset or deletion control. Invalid input rejected before acceptance creates no accepted attempt. A lost response is uncertain: inspect that request ID before initiating more work. Do not delete the private ledger or alter hashes to force progress.

The private state lives under `data/perception/`, including its ledger and per-request source, normalized observations, evidence and receipts. Source imports in Workspace also remain private. Clean source, core and full appliance exports include public code, UI, fictional fixtures and this guide, and exclude owner recordings, derived evidence and the perception ledger. Keep private backups separate from a public distribution.

## Local HTTP interface

These routes use the existing paired local API and its owner authority. Read the current `revision` from `GET /api/perception` before an explicit write.

| Request | Body or result |
|---|---|
| `GET /api/perception` | Saved status, revision, bounds, holds and receipt summaries. |
| `GET /api/perception/recordings/<request_id>` | Verified saved receipt, recording evidence and separately aged `current_zones`. |
| `POST /api/perception/import` | `{"path":"perception/room-session.json","request_id":"import-001","expected_revision":0}` |
| `POST /api/perception/replay` | `{"recording_id":"import-001","request_id":"replay-001","expected_revision":2}` |
| `POST /api/perception/demo` | `{"request_id":"demo-001","expected_revision":0}` |

The example revisions illustrate separate requests, not a sequence to copy unchanged. Fetch the actual revision before each action. A replay's `recording_id` identifies a saved import request; the bundle's declared `recording_id` is source metadata. Passive agent access uses the bounded `perception_read` tool and cannot import, replay, capture, identify people, configure sensors or authorize an action. Presence evidence does not replace any existing approval, safety or business control.

## RuView recording adapter

Version 0.41 adds an explicit, pinned native RuView JSONL recording adapter and a fictional RuView-shaped example. See [RuView intake, source policies, clock semantics and the physical CSI plan](RUVIEW_WIFI.md). The adapter preserves exact original bytes, source policy and producer clocks through verified replay. ESP32-only admission rejects simulator fallback; recorded-any admission preserves synthetic/unknown origin. Server timestamps do not become physical RF observation clocks, and saved recordings never establish current occupancy.

## Future live RuView acquisition boundary

The observation contract remains the live-acquisition extension boundary. The recording adapter does not connect to a RuView service or acquire CSI. A later separately qualified live adapter must preserve real versus synthetic capture origin, producer clock semantics, source age, declared quality and calibration. It must not promote an upstream live/verified label to local physical qualification or turn unavailable room evidence into absence. Explicit physical source selection must reject simulator fallback.

Keep acquisition outside the model loop and keep its endpoints/devices owner-registered. Future live acquisition needs its own explicit controls, network/transport review and field tests; importing a recording does not register or enable that source. Current recording APIs deliberately provide no capture, configuration, firmware, actuator or model-loading route.

## Package and qualification limits

The Windows starter includes this recording feature and public fixtures as source. Initial runtime/model setup still follows [Get started](GET_STARTED.md). A clean offline core includes source assets bound to its released `SOURCE-MANIFEST.json`; a fully prepared core is required before creating an offline copy. The historical full 0.16.1 appliance predates Awareness and is a separate artifact.

Offline tests of synthetic imports, deterministic replay, receipts and portable inventories establish software behavior only. They do not qualify real CSI/camera/mmWave acquisition, human detection, pose estimation, device latency, power-outage continuity or a disconnected target PC. Live sensing and downstream physical actions require a separately accepted implementation and qualification.
