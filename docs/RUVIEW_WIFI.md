# RuView Wi-Fi recordings (0.41)

Ariadne can import and replay recordings from a pinned RuView sensing protocol. This is the first implemented RuView adapter: it translates a selected local JSONL file into Ariadne's bounded observation journal, retains its exact original bytes, and exposes RF source, classification, quality and clock evidence in Awareness.

This adapter uses the Python standard library. It needs no RuView installation, optional camera runtime, downloaded weights, model call, cloud service or recurring polling. Physical Wi-Fi acquisition is a separate stage: no ESP32, router, network listener, sidecar, firmware or model is started by these controls.

## Try the adapter without RF hardware

1. Open **Awareness → JSON recordings** (`?view=perception`) in your paired local installation.
2. In the RuView section, explicitly choose its synthetic example.
3. Inspect the **SYNTHETIC** origin, adapter profile, raw source SHA-256, producer-clock notes and recorded RF evidence. The example uses invented RuView-shaped messages, including unavailable and conflicting observations.
4. Choose **Replay selected recording** when wanted. Replay creates a separate receipt from the retained source; simply reading or selecting it does not replay anything.

The example is a software contract test. Its animations, labels and classifications are not observations of your room. Opening the desk never discovers, connects to or activates sensing hardware.

You can ask Ariadne to summarize saved RuView evidence and what remains unknown. Its existing bounded `perception_read` tool can inspect the recording summary; it cannot import, replay, connect to a producer or start capture. Private full adapter frames remain available in the owner desk.

## Import a native RuView recording

The supported profile is `ruview-sensing-094e140-1`, pinned to RuView revision [`094e1400719ded1283c67d23b9c737f6738a3244`](https://github.com/ruvnet/RuView/commit/094e1400719ded1283c67d23b9c737f6738a3244). The adapter is versioned against the inspected wire fields rather than assuming every future RuView release has the same contract.

That pin identifies the wire contract reviewed by Ariadne. Native messages do not attest the running producer's Git revision, firmware or authenticity; a matching recording does not prove which binary produced it.

1. Obtain a recording from your own reviewed RuView setup. Its native recording writer saves one broadcast JSON object per line. The file can contain sensing updates and recognized non-sensing messages; recognition of a message is not permission to use medical/vitals outputs.
2. Place the selected `.jsonl` file inside Ariadne Workspace, for example `data/workspace/perception/ruview-room.jsonl`.
3. Enter the Workspace-relative path `perception/ruview-room.jsonl` in the RuView intake.
4. Select the source policy deliberately:

   | Policy | Admission |
   |---|---|
   | **ESP32 only** (`esp32_only`, default) | Requires declared ESP32 sensing records. A simulator, RSSI source, unknown source or fallback is rejected before an attempt is reserved. A declared ESP32 source remains unqualified owner-supplied evidence. |
   | **Recorded sources** (`recorded_any`) | Allows the pinned supported recorded source modes, including explicit simulation. Each observation keeps its original source/origin. This choice never connects a radio or promotes simulation to physical capture. |

5. Select **Import RuView recording**. Review the completed receipt before replaying or interpreting the results.

The input is bounded to 1,000,000 bytes, 1,024 total messages, 256,000 bytes per line, 512 sensing observations, 16 distinct node IDs and a 24-hour producer timeline. This profile places its source families in one generic `ruview-room` zone; it does not infer room boundaries from RF. Native ancillary messages are counted separately and do not become presence evidence. Counters outside the exactly representable JSON integer range are rejected. Oversized or unsupported inputs are rejected rather than silently shortened. Split a longer source into deliberately reviewed windows outside the importer and retain the original recording separately.

The original source is retained privately together with the converted bundle, adapter provenance, normalized observations, derived evidence and receipt. Changing the Workspace file does not change a completed import. A new source requires a new explicit import. Replaying a retained recording recomputes and verifies the same adapter/profile/policy and artifact hashes. The existing 32,000,000-byte retained-artifact budget and 100 accepted-attempt limit still apply.

## Read the Wi-Fi evidence correctly

| Field or state | Interpretation |
|---|---|
| RuView `source` | A producer declaration. ESP32, simulation and RSSI sources stay distinct. A source label does not authenticate a sender or establish local hardware qualification. |
| Capture origin | Recognized simulation stays synthetic. Other source labels remain unknown origin because native messages do not attest physical capture. |
| RuView `timestamp` | Server-created Unix seconds, preserved as producer time. It is not silently copied into the physical `observed_at` field. |
| RuView `tick` | Server counter, retained in adapter provenance. It is not a radio sequence number or authenticated boot identifier. |
| Physical observation time | Unknown when the source provides no original radio observation clock. Replay or import time never fills that gap. |
| Node inference and age | Historical producer classifications and source-age declarations. Missing age, stale input or unavailable evidence prevents a supported absence/presence conclusion. |
| Room inference | Inspected with contributing nodes. Unavailable/zero-contributor results and conflicting nodes remain visible; top-level `presence: false` alone never means an empty room. |
| Confidence | Uncalibrated model/heuristic score, never a probability or local accuracy measurement. |
| Current occupancy | **Unknown** for saved recordings, even when a historical producer claimed someone was present. |
| Hash verification | Identity and integrity of retained bytes. It does not certify that the producer's physical claims are true. |

The adapter excludes pose skeletons, medical/vitals interpretation, person identity, reliable people counts and metric coordinates. Recording contract v1 has no range, angle, depth or calibrated coordinate frame. It cannot turn Wi-Fi signal-strength values into a radar map.

Clock regressions, unsupported schema changes, duplicate or invalid counters, missing inference and quality problems require review. The adapter does not invent a timing repair or choose a convenient source to make a result appear successful.

## Privacy and controls

Use recordings from a setup you own and are authorized to observe. A native RuView source can contain a private SSID or other local identifiers. Exact source and adapter provenance stay in private perception storage; public source/portable exports exclude owner recordings and ledgers. The owner desk can inspect source declarations, while the bounded general `perception_read` tool does not forward the full private adapter frames or SSID fields.

Exact source retention also preserves any raw CSI, ignored vitals or other accepted telemetry already present in your selected file. Those fields are excluded from normalized evidence and the RF inspector. Choose the recording with that private retention in mind; the importer does not redact and relabel its original bytes.

Imported pixels, audio and model inference are not part of this adapter. The existing USB-camera preview and its explicit local image questions remain separate. This work changes neither camera duration nor microphone settings.

The existing global pause and perception revision fence block new imports, examples and replays. Completed evidence stays readable. An accepted failure or interrupted attempt holds new writes; it is not automatically retried, acknowledged or cleared. Inspect uncertain request IDs before initiating more work. No sensor observation grants workflow, device or financial authority.

## Physical CSI pilot: what remains

The existing USB cameras provide real visual images, but they do not produce Wi-Fi Channel State Information. The next physical experiment needs a correctly matched **ESP32-S3 CSI receiver**, compatible controlled Wi-Fi packet traffic/AP, USB power and fixed placement. Confirm the exact board, flash capacity and upstream firmware variant before buying or flashing anything. The upstream hardware guide treats C6 as research and C5 as experimental; start with a single S3 link before considering a mesh.

The initial aim is measured single-room presence/motion evidence, including failure cases. It is not reliable radar “anywhere.” A later live connector must use explicit real-source selection, reject automatic simulation fallback, preserve source age/outages and keep physical acquisition separate from the model loop.

RuView's default HTTP port 8080 conflicts with Ariadne's default port. Future setup must deliberately choose a nonconflicting loopback port; this implementation does not change either service or open any listening port. The planned read-only interfaces are fixed `/api/v1/status` and `/api/v1/sensing/latest` endpoints, with explicit owner controls. There is no arbitrary-URL, firmware, router, OTA, WebSocket, UDP or upstream-tool execution route in this release.

Before calling live sensing qualified, record labeled occupied/empty, moving/still, fan/pet/door, adjacent-room interference, dropped-frame and disconnected-source trials. Keep room/session holdouts. Report precision/recall, false transitions, source-age/event delay and resource use separately from successful parsing. Moving a sensor or changing a room invalidates any assumption that old placement evidence still applies. No measured range, accuracy, latency, power or energy claim is established by the recording tests.

## Local API

Both explicit writes use the existing paired API, strict JSON parser, pause controls and current `expected_revision` from `GET /api/perception`:

```json
POST /api/perception/import-ruview
{"path":"perception/ruview-room.jsonl","request_id":"rf-import-001","expected_revision":0,"source_policy":"esp32_only"}
```

```json
POST /api/perception/ruview-demo
{"request_id":"rf-demo-001","expected_revision":0}
```

The revisions are illustrative; read the actual revision before each action. Saved detail and replay use the existing `/api/perception/recordings/<request_id>` and `/api/perception/replay` routes. No capture/connect/configure route is added.

## Packaging, licensing and operating cost

The standard-library adapter, public fictional fixture and guide ship in the source/starter and source-bound offline exports. Prepared Ariadne can use them without Internet or Codex. Initial fresh-PC core preparation still follows [Get started](GET_STARTED.md). The optional camera detector pack is not needed for RuView recording imports.

This release includes no RuView binary, firmware, upstream model weights or third-party dataset. The upstream source license is MIT, while individual dependencies and weights can have separate terms. In particular, the MM-Fi pose model has noncommercial weight terms and different input geometry; it is not included in Ariadne's portable/commercial core. The encoder's published temporal-embedding metric is not presence-detection accuracy.

Routine recording reads/replay incur local CPU, memory and bounded storage costs, with zero model/API calls or recurring collector. Future continuously running RF hardware and a sidecar would add energy and maintenance costs; those remain unmeasured. Development/testing effort is separate from the product's recurring operating cost.

## Primary references

The maintained November 12 source review was refreshed against the same pinned revision before implementation:

- [Active wire structures and server](https://github.com/ruvnet/RuView/blob/094e1400719ded1283c67d23b9c737f6738a3244/v2/crates/wifi-densepose-sensing-server/src/main.rs#L497).
- [Native recording writer](https://github.com/ruvnet/RuView/blob/094e1400719ded1283c67d23b9c737f6738a3244/v2/crates/wifi-densepose-sensing-server/src/main.rs#L8461).
- [Room/node inference](https://github.com/ruvnet/RuView/blob/094e1400719ded1283c67d23b9c737f6738a3244/v2/crates/wifi-densepose-sensing-server/src/inference.rs).
- [Firmware and hardware](https://github.com/ruvnet/RuView/blob/094e1400719ded1283c67d23b9c737f6738a3244/firmware/esp32-csi-node/README.md).
- [Sensing-server security notes](https://github.com/ruvnet/RuView/blob/094e1400719ded1283c67d23b9c737f6738a3244/v2/crates/wifi-densepose-sensing-server/SECURITY.md).
- [ESP32-S3 CSI manufacturer documentation](https://docs.espressif.com/projects/esp-idf/en/v5.5.3/esp32s3/api-guides/wifi.html#wi-fi-channel-state-information).
- [MM-Fi pose model card](https://huggingface.co/ruvnet/wifi-densepose-mmfi-pose/blob/main/README.md) and [encoder model card](https://huggingface.co/ruvnet/wifi-densepose-pretrained/blob/main/README.md).

See [the underlying recording contract, limits and recovery rules](PERCEPTION_RECORDINGS.md).
