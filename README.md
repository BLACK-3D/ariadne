# Ariadne — local AI in a box

Version 0.18.0 adds faster casual conversation and wake-once voice sessions with explicit sleep and microphone-stop controls. It keeps the installed 9B model, preserves full mission budgets and microphone-off preferences, and adds measured load/prompt/generation timing. See [conversation controls and measured performance](docs/VOICE_CONVERSATION.md).

Version 0.17.0 added a private Venture Lab: business drafts, owner evidence gates and fixed ETF research connected to the daily brief, with zero model or network calls in the daily check-in. See [Venture Lab's operation and research limits](docs/VENTURE_LAB.md). The separately qualified full offline ZIP remains version 0.16.1 until another complete package is built and verified.

Version 0.16.1 fixed a Windows SQLite status-read race: normal removal of temporary WAL files could briefly make a completed workflow appear unavailable. Routine access distinguishes transient sidecars from the main database and refuses to recreate a missing main database. That complete package uses `qwen3.5:9b` by default; the optional pinned 4B model reached its response budget before tools in one fresh-copy test. See [reliability and qualification scope](docs/SQLITE_RELIABILITY_0_16_1.md).

Version 0.16 connects business reports, market audits/studies and saved device observations to **autonomous offline routines**. Runs freeze their accepted inputs, use the existing single worker, verify artifacts and hold recurrence after failures or interruptions. Configure them in **Operations → Connect the work**, then run, verify and manage them under **Workflows → Offline routines**. Factory recurrence remains off. See [autonomous operations](docs/AUTONOMOUS_OPERATIONS_0_16.md) and [the full one-click package](docs/PORTABLE_0_16.md).

Version 0.15 adds the **Operations desk**: offline business snapshots and ERPNext-export audits, exact draft quotes/invoices, OpenBB-compatible historical-data audits and strategy replay, and a scoped Home Assistant gateway with approved actions and durable uncertainty receipts. Open **All systems → Operations** for zero-model local reports and explicit synthetic examples. Two new specialists and three checked AI workflows use these tools. No ERP, brokerage or Home Assistant installation is implied. See [operations and setup](docs/OPERATIONS_0_15.md).

Version 0.14 adds a **daily repository watch** to the local 6 AM brief: selected business, trading, control and AI upstreams, first-observation baselines, changed/unchanged metadata and dated offline results. It uses no language model and installs nothing. See [daily repository operation](docs/REPOSITORY_WATCH_0_14.md).

The [29 October capability radar](docs/CAPABILITY_RADAR_2026_10_29.md) adds a primary-source scan of **49 business, trading, device/control and AI candidates**, with offline requirements, license constraints, recurring-cost considerations and concrete integration workflows. These are researched proposals, separate from installed capabilities. The dated report is also saved as a local daily-brief addendum.

Version 0.13 adds **checked private backups**: double-click **Backup Ariadne.cmd** to create a new owner-state archive, verify every member hash and check each SQLite snapshot before publishing it. Verification runs offline without a model and leaves Ariadne running. This is a standalone utility; an already running server can keep its current version until its next deliberate restart. See [backup scope and verification limits](docs/CHECKED_BACKUPS_0_13.md).

Version 0.12 adds the **Signal Room interface**, a **1080×1350 one-page daily brief** with local PNG/SVG export and printing, and a **Windows background host that continues after the launcher or Codex closes**. Double-click Start to launch or reopen the verified host; use Stop Ariadne.cmd for explicit shutdown. See [interface research](docs/SIGNAL_ROOM_0_12.md), [daily signal](docs/DAILY_SIGNAL_0_12.md) and [independent hosting](docs/INDEPENDENT_HOST_0_12.md).

The 0.12.1 host uses the fixed Windows CIM broker and verifies that the host is outside all Windows jobs before starting services. A successful breakaway alone is insufficient when a caller belongs to nested jobs.

Version 0.11 adds **quick offline Cori speech**, a reusable CPU speech worker, voice and pace controls shared by PC wake replies and browser playback, and concise spoken answers. Existing system speech and four slower Kokoro presets remain available. See [voice operation and measurements](docs/FAST_VOICE_0_11.md).

Version 0.10 adds **three local-only specialist agents**, six checked offline workflows with a new output folder per run, and verified structural comparisons between saved routine reports. The full portable exporter pins public model assets and excludes private model metadata. See [offline autonomy and portable operation](docs/OFFLINE_AUTONOMY_0_10.md).

Version 0.9 adds **offline routines**: saved sequences of fixed reports, opt-in recurrence, durable step receipts and explicit artifact verification, with no model calls inside a routine. AI workflows can retain their selected specialist, and workflow schedules freeze instructions, output checks and specialist selection. See [offline routines and checked schedules](docs/OFFLINE_ROUTINES_0_9.md).

A local assistant with a browser console, autonomous tool loops, durable memory, searchable knowledge, recurring missions, offline voice, and operator-defined HTTP integrations. Models, conversations and files stay on your server when using the bundled local configuration. No paid API is required.

Version 0.8 introduces a **command-center interface**: a live system presence, horizontal navigation, an on-demand system directory, a directive station, an expandable mission ledger, and result dossiers with visible approval controls. Presence reflects reported activity; it does not simulate thoughts. The interface uses local assets and no new model calls or public polling. See [the command center](docs/COMMAND_CENTER_0_8.md).

Version 0.7 added **Internet controls and public web tools**: on-demand search, HTTPS text reading, a connection test, dated offline caches, and request history. The owner controls the shared Internet switch for agents and the news/weather collector. New installations start with Internet disabled. Local models remain local. See [Internet operation](docs/INTERNET.md).

Version 0.6 introduced **16 specialist agents**, deterministic dispatch with enforced tool scopes, an opt-in offline **“Ariadne” PC wake listener**, spoken responses, and a **6 AM daily brief** with durable reports and dated weather/alert/regional-source caches. Listening uses local ASR with zero idle language-model calls. Fixed briefing reports use zero language-model calls. See [agent operating details](docs/AGENTS_0_6.md), [voice setup](docs/WAKE_VOICE.md) and [daily briefing setup](docs/DAILY-BRIEFING.md). New weather and news require connectivity to refresh; the report itself still works offline.

Version 0.5 adds a **Survival** workspace with supply rotation, exact reserve/energy estimates, rebuilding project dependencies, dated offline references and private PDF/EPUB books. **Workflows** includes fixed local report procedures that make zero model calls, alongside optional AI workflows. See [survival setup](docs/SURVIVAL_0_5.md), [reference provenance](docs/SURVIVAL-SOURCES.md) and [workflow research](docs/WORKFLOWS_0_5.md). Historical release archives remain in `dist`; the current full portable export is built separately from GitHub source.

This is an implemented assistant runtime, not movie-level general intelligence. It can create business plans, code, operating procedures and other artifacts, and interact with systems you explicitly connect. It does not independently establish legal entities, acquire accounts, spend money, publish services, contact people or control arbitrary hardware. Its quality depends on the selected model, tools and instructions. Review consequential outputs.

## Use this PC

For a fresh GitHub clone, follow [Build from source](docs/BUILD_FROM_SOURCE.md). See [GitHub source contents](docs/GITHUB_SOURCE.md) for included files and private-data exclusions.

1. Double-click **Start Ariadne.cmd**, or run `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1` from this folder.
2. Open **http://127.0.0.1:8080**. Pair using the key in `data/server.token`. The background host does not print that key into its logs. The browser retains it in tab-session storage, not a password manager. **Lock** clears that browser session.
3. Start a mission, or review a reusable **Workflow**. Inspect its activity trail, file checks and approval requests. Add references under **Knowledge**, durable notes under **Memory**, and recurring instructions under **Schedules**. **Capabilities** shows the tools actually available on this server.
4. Closing the browser, launcher or Codex leaves the Windows background host running. Double-click **Stop Ariadne.cmd** when you want Ariadne and its owned model/flow services to stop. This does not install a Windows service or automatic login task.

The local token is also in `data/server.token`. Treat the project data folder and backups as private. `python -m ariadne token` prints it locally. Never share the token in a public URL.

This installation reserves **E:**. Configuration rejects E: and linked/reparse paths. Source, runtimes, model weights, speech models and application state are under this project on C:. The launcher redirects Ollama's home, identity, cache and model storage into this box.

The launcher starts the project's owned Ollama process if needed. It refuses to silently reuse another installation's local listener. An explicitly supplied `ARIADNE_OLLAMA_URL` uses that operator-managed server; the operator must then verify its storage, privacy and model policy.

## What works

| Capability | Behavior |
|---|---|
| Missions | One local inference worker, at most 12 model steps by default, at most 8 tool calls per step, durable messages and event trail |
| Files | Confined UTF-8 read, atomic write, directory listing and bounded literal search in `data/workspace` |
| Calculation | Bounded arithmetic parser; no host Python evaluation |
| Memory | Owner notes with SQLite full-text recall; no embedding service or additional model |
| Knowledge | Pasted/uploaded documents and explicitly imported read-only folder references; source/content hashes, processor identities, versions and extraction warnings |
| Business data | CSV/TSV/flat JSON tables; filters, groups, decimal totals, weighted totals and bounded summaries without code execution |
| Larger analytics | Up to100,000 rows/10MB, fixed filters/groups and one unique-key join; exact decimal totals, read-only snapshots and no model SQL |
| Local OCR | PNG/JPEG and scanned PDF text through pinned CPU models, up to 10 pages / 12 million pixels per page; processor identity and review warnings |
| Local MCP | Owner-registered loopback Streamable HTTP servers, reviewed tool allowlists and per-write approval; modern and legacy protocol modes |
| Workflows | Editable local mission templates with owner-authored file/content/JSON/CSV/numeric checks, artifact hashes and one bounded repair pass |
| Schedules | Persisted interval missions, minimum 5 minutes; no overlap/catch-up flood; substantive failure disables the schedule until explicitly reenabled |
| Voice | Local streaming Sherpa transcription, Whisper fallback, voice activity and anonymous speaker segmentation; quick Windows speech or explicitly selected natural Kokoro speech |
| Engineering and media | Fixed FreeCAD, Blender, KiCad, QGIS and FFmpeg operations; local vision and FLUX image creation |
| Office and planning | DOCX/XLSX/PPTX/PDF creation, bounded symbolic mathematics and fixed optimization templates |
| Business records | Local contacts, stock movements and balanced integer-cent journals with idempotent writes |
| Integrations | Operator-configured fixed HTTP endpoints, credential references from environment variables, argument validation and approval for writes |
| Code execution | Optional Docker-only Python runner using a read-only input snapshot, no network, resource limits and approval; disabled by default |

Autonomous workspace work runs without approving every step. Executing code and changing connected systems require a reviewable approval. Rejection is returned to the model. Every new external action has its own approval; approving one action does not grant blanket authority. The pause button takes effect before the next step, and cancellation cannot undo an action already in progress.

No cloud speech recognition, remote JavaScript, CDN, telemetry SDK or remote embeddings are used by the bundled local configuration. Public web search and reading require the owner-enabled Internet channel; queries, requested URLs and the public IP reach their sources. Configured external integrations have their own connectivity and approval rules. Offline operation means local models, files, knowledge, schedules and speech remain available; disconnected internet services do not.

## Reference folders and documents

In **Knowledge → Local folders**, add a specific allowed source folder, select a document, review the extracted text and import it. The source is read-only; imported snapshots and their metadata live in Yikes. The model cannot add source folders or browse those folders itself. Only explicitly imported text is searchable by missions. Refreshing the same source path replaces its searchable text, increments its version and retains bounded hash/version metadata. This is a current snapshot index, not a full document revision archive. A changed preview file hash requires a new review before import. OCR imports also bind the extracted text, warnings and processor identity to the reviewed preview. Changing the processor or recognized text requires another preview. File and extracted-content hashes are retained separately.

Uploads and previews accept text/code, CSV/TSV, JSON, DOCX, XLSX and optional PDF text, at most 10 MB per file. Office parsing has expanded-size, member, XML and text limits; formulas/macros are never executed. XLSX uses existing cached formula values and reports missing caches. PDF runs in a fixed worker with a 30-second deadline, 256 MiB process memory limit, bounded output and at most 100 pages. Extraction can omit layout, images, annotations, revisions or unsupported content; inspect warnings and the original. Use the local OCR option for scanned PDFs, or upload PNG/JPEG images. The optional OCR worker is provisioned on this PC; see the 0.3 setup instructions below. OCR may misread values and reading order.

Hidden files, credential-like filenames, reparse paths, hard links, private application state and excluded dependency/system folders are blocked. Select narrow reference folders rather than a whole drive or a folder containing Ariadne's private state. Filename exclusions cannot identify every secret inside ordinary documents; review selected content before importing. Removing a source stops future access; already imported snapshots are separate Knowledge entries and can be removed there.

PDF support on a fresh installation is optional:

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-documents.txt
```

This PC also has the pinned server/document wheels in `runtime/wheels`. With Python already available, an offline repair can install them using `pip install --no-index --find-links runtime/wheels --require-hashes -r requirements-server.txt -r requirements-documents.txt`. Preserve that folder with the model/runtime assets; it is excluded from the source-only archive.

The field-console update is documented in [UI verification](docs/UI_0_2_1.md). The dated [capability repository shortlist](docs/REPOSITORY_SHORTLIST.md) separates research candidates from installed tools.

## Checked workflows and exact totals

Review the starter workflows, edit their instructions/checks or create your own. Running a workflow freezes its instructions and checks for that run. **Execution finished** and **Expected outputs verified** are distinct outcomes. A model's success claim cannot satisfy a file check. On failed checks, the worker receives one repair opportunity within the original step budget. Remaining failures stay visible and stop an associated recurring mission.

Checks support `file_exists`, `file_contains` (literal text), `json_valid` (no duplicate keys/nonfinite numbers), `csv_columns`, and exact `json_number` predicates. For example:

```json
[
  {"kind":"csv_columns","path":"business/inventory.csv","columns":["item","quantity","unit_cost"]},
  {"kind":"json_number","path":"business/summary.json","pointer":"/total_value","value":"16.00"}
]
```

Numeric expectations accept quoted decimals or integers; quote decimal and large values to preserve exactness through browsers and JSON clients. RFC 6901 pointers select fields/array elements, with at most 32 segments/500 characters. Values are bounded to 200 coefficient digits and exponent magnitude 400. Checks verify the selected facts, not overall business correctness, legal compliance or deployment. Verified artifact hashes identify the bytes checked; a later file edit creates different bytes.

Ask `table_query` for `sum_product` with exactly two factor columns to calculate weighted totals deterministically:

```json
{"path":"business/inventory.csv","aggregations":[{"op":"sum_product","columns":["quantity","unit_cost"],"as":"total_value"}]}
```

Tables are bounded to 100,000 source characters, 2,000 rows and 100 columns. Numeric inputs have at most 100 digits, magnitude 1e100 and exponent magnitude 100. Weighted accumulation uses 512-digit precision; averages round to 40 significant digits. Numeric output uses decimal strings. Formula-like text is escaped in CSV/TSV exports. These tools do not execute spreadsheet formulas, SQL or arbitrary expressions.

## Portable deployment

The full **0.10 Windows x64 offline ZIP** includes current source, Python, installed engines, pinned public models, notices and offline repair wheels. Extract it into an allowed folder and double-click **Start Ariadne.cmd**. A new box creates its own pairing key and private state; it does not copy the original owner's conversations, files or credentials. See [current offline and portable operation](docs/OFFLINE_AUTONOMY_0_10.md) and [baseline portable instructions](docs/PORTABLE_0_4.md).

The source-only distribution requires Python **3.11+** for the core. This checkout runs without a package installation:

```powershell
python -m ariadne doctor
python -m ariadne serve
```

For the production WSGI server, create an isolated environment and install the checksum-pinned package:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-cache-dir --require-hashes -r requirements-bootstrap.txt
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-server.txt
```

The Windows launcher prefers the bundled `runtime/python/python.exe`, then `.venv`, then system Python. It uses isolated Python mode and opens the browser after the HTTP server has bound. The dependency-free server permits loopback use only; LAN serving requires Waitress. On Linux/macOS run `sh start.sh` with Python and a separately provisioned local Ollama service. `pip install .[server]` installs a package and the optional server dependency. The full runtime is not automatically downloaded by importing or starting the application.

Use a security-supported Python with current CA trust before online provisioning or HTTPS integrations. The bootstrap requirement pins pip 26.2.2 with the checksum in the [Python security team's October 2026 guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576). It corrects pip's vendored certificate bundle; it does not update the base Python interpreter, OS/container trust stores, or other environments. The verified pip wheel is also cached in `runtime/wheels` for `--no-index` repair with `requirements-bootstrap.txt`.

To provision a new **Windows x64** installation while online:

```powershell
python scripts/provision.py --model qwen3.5:9b
python scripts/provision_voice.py
```

The provisioners use pinned official runtime URLs and SHA-256 checksums. Model weights are fetched through Ollama's normal verified blob download; `data/provision-manifest.json` records the installed model digests. Runtime downloads and model weights are kept under this project. The model tag identifies a provisioning request; the recorded digest identifies the actual downloaded artifact. Reprovisioning a mutable tag in the future can fetch a different model, so preserve the verified offline bundle when exact recovery matters.

The checked-in source is portable, while this particular installation's `.venv` and Windows binaries are platform-specific. Do not copy a Windows virtual environment to another OS. Acquire the appropriate Python/Ollama runtime there, or use Docker.

## Docker appliance

Docker is optional and is not required or installed on this Windows PC by the application. Build and pull while online:

```sh
docker compose -f compose.yaml -f compose.provision.yaml up -d --build
docker compose -f compose.yaml -f compose.provision.yaml exec ollama ollama pull qwen3:4b
docker compose -f compose.yaml -f compose.provision.yaml down
docker compose up -d
docker compose exec ariadne python -m ariadne token
```

The final command starts the runtime on an **internal** Docker network. Ollama has no published port. Only the application's loopback port 8080 is published. Named volumes persist application state and models, and containers restart unless stopped. The app runs as an unprivileged user with a read-only root, without a Docker socket. The Python runner is therefore unavailable in this Compose deployment. Voice is optional and requires supplying its binaries/models in a compatible container build.

Container image tags can change. Before an appliance rollout, pin the selected Python and Ollama image digests in your deployment record. Export the built application image, Ollama image and volumes for a disconnected restore. Docker's own image/volume storage must also be on an allowed physical disk; the application cannot control Docker Desktop's disk placement. Do not use E: for Docker data on this installation.

The Compose files have not been exercised on this host because Docker is not installed. Native Windows operation is the verified deployment path here.

## Connect systems

Copy `config/connectors.example.json` to `data/connectors.json`, supply the real fixed URLs, put secrets in environment variables, and restart. The model cannot change the URL, method or credentials. A configured GET is autonomous only when explicitly marked `read_only: true`; other calls require approval. Connector responses are bounded, redirects are rejected, credentials are redacted, and proxies are not inherited. Only connect endpoints whose behavior and downstream authority you understand.

Business systems, home automation, inventory services and custom devices can expose small HTTP adapters. Give each operation a narrow endpoint and separate read/write tools. Local MCP clients and approved browser interaction with explicitly registered loopback applications are implemented. Browser requests are confined to the registered origin and path, with redirects and external resources blocked. Email/calendar account setup and arbitrary repository installation remain owner tasks. Add supported adapters with tests rather than automatically executing code from a model or repository.

For offline coding experiments on a **native** deployment, install Docker separately, pre-pull a trusted Python image, then start with `ARIADNE_ENABLE_DOCKER=true`. Review the script in the approval card before executing it. The runner uses a bounded snapshot of the workspace, has no network, runs as UID 65534, has a read-only root, and returns bounded stdout/stderr. Its temporary output is discarded; it cannot write back into the host workspace. Do not mount the Docker daemon socket into the web app.

## Configuration

Environment variables are read at server startup. Defaults are portable; model choice can also be changed to another installed model in the interface.

| Variable | Default |
|---|---|
| `ARIADNE_DATA_DIR` | `./data` |
| `ARIADNE_MODEL` | `qwen3:4b` |
| `ARIADNE_OLLAMA_URL` | `http://127.0.0.1:11434` |
| `ARIADNE_HOST` / `ARIADNE_PORT` | `127.0.0.1` / `8080` |
| `ARIADNE_ALLOWED_HOSTS` | Additional comma-separated host names or IPs, without ports |
| `ARIADNE_MAX_STEPS` | `12`, clamped to 1–32 |
| `ARIADNE_MODEL_TIMEOUT` | `180` seconds per model request |
| `ARIADNE_CONNECTORS_PATH` | `data/connectors.json` |
| `ARIADNE_ENABLE_DOCKER` | `false` |
| `ARIADNE_OFFLINE` | `1`; Python process permits outgoing loopback traffic only; restart required to change |
| `ARIADNE_DOCKER_IMAGE` | `python:3.13-slim`; pre-pull and pin for deployment |
| `ARIADNE_WHISPER_BIN` / `ARIADNE_WHISPER_MODEL` | Optional whisper.cpp executable/model |
| `ARIADNE_PIPER_BIN` / `ARIADNE_PIPER_MODEL` | Optional Piper executable/ONNX voice |

`data/voice-config.json` supplies fallback CLI adapter paths unless an environment override is present. The optional fast speech pack adds the pinned Cori neural voice through `data/media-config.json`; the same saved voice and pace apply to PC wake replies and browser playback. Windows system speech remains available. The opt-in PC wake listener operates with the browser closed; browser push-to-talk remains separate. Uploaded push-to-talk audio is temporary. See [voice setup](docs/WAKE_VOICE.md) and [fast speech provisioning](docs/FAST_VOICE_0_11.md).

For a trusted LAN, set `ARIADNE_HOST=0.0.0.0`, add the server IP/name to `ARIADNE_ALLOWED_HOSTS`, use Waitress, and place HTTPS in front of it. Bind port 8080 to loopback behind that proxy. The proxy must preserve the original Host header; origin checks require a same-origin host. Do not port-forward the bearer-token server to the public internet. This is a single-owner appliance: separate users need separate instances and state directories. It has no tenant isolation, account roles or enterprise identity provider.

## Power loss, recovery and backups

Once provisioned, local inference and voice work without internet. Power the PC and any LAN/router needed to reach it, then run the launcher. A generator does not eliminate the risk of interrupted disk writes; SQLite uses WAL transactions and file writes are atomic, but backups remain necessary.

Queued work and pending approvals survive restarts. A mission that was running when the process stopped is marked **interrupted** and is not automatically replayed, since an external action could have succeeded just before the outage. Inspect its trail and start a focused follow-up. An in-progress approved action can be marked **uncertain**; check the connected system before retrying it. External actions do not have exactly-once guarantees unless their adapters implement idempotency.

Double-click **Backup Ariadne.cmd** for a newly named, verified private archive under `backups/`. Ariadne can stay running. Stop it deliberately first when you need a coordinated database-and-workspace pair. The command interface is also available:

```powershell
python scripts/backup.py ${BOX}\backups\ariadne-backup.zip
```

Use a new destination outside `data/workspace`. The script snapshots the seven known SQLite databases, includes regular workspace files and allowlisted private configuration, then reads back the archive and checks its hashes and database integrity. Each database is consistent individually; a live backup is not one global transaction. It does not duplicate large models or external connector secrets. Preserve `data/models`, runtime binaries, licenses and the provisioning manifest separately for a fully disconnected reinstall. Protect and encrypt backups using your normal disk/backup tools; the application does not encrypt them.

See [checked backups](docs/CHECKED_BACKUPS_0_13.md) for offline verification and recovery constraints. There is no automatic restore. Stop the server, preserve current state, verify a trusted archive and inspect it in a fresh allowed directory before attempting recovery. Saved schedules, queued work, credentials and microphone preferences remain potentially active data; review them and uncertain actions before starting a recovered installation. Absolute capability paths and the fixed local flow data path require separate relocation handling. Never restore over a running server.

## Operating cost and evidence

The core uses no model/API subscription. It makes no inference calls while idle, uses one model worker, caps tool/context/output budgets, and retains the selected model for up to fifteen minutes after use under the 0.18 voice policy. Retaining model memory trades availability of RAM/VRAM for fewer cold loads; it is not a measurement of electricity savings. Optional hybrid retrieval uses an on-demand CPU embedding worker and a persisted vector index; lexical recall remains available without it. Heavy native and model workers run on demand. Tool schemas are selected for the current task; ordinary prompts measured 9,196 schema bytes versus 24,516 for all schemas on the qualification snapshot. These are byte counts, not measured energy savings. Electricity, hardware wear, storage and administration still have costs; no monetary or energy savings are claimed without measurement.

Model events record prompt tokens, output tokens and runtime duration. Use those alongside task success, GPU/CPU utilization and wall-meter energy readings to compare models. A smaller model that repeatedly fails or needs more turns may cost more to operate. Keep the smaller model for tasks it demonstrably handles; choose a stronger model when its measured quality warrants it.

For a business cost-and-capacity scenario, the offline [Venture Lab economics calculator](docs/VENTURE_LAB.md#monthly-cost-and-capacity-scenarios) separates cash surplus from the value of owner labor, preserves unknown costs, and shows client counts, capacity and one-client-loss stress. It uses no model or network calls. Prices and client counts in its example are hypotheses, not market validation or observed revenue.

Run verification:

```powershell
python -m unittest discover -s tests -v
node --check ariadne/static/app.js
python scripts/verify_live.py
python scripts/verify_0_2.py
```

The original live verification creates a business-plan artifact and optionally round-trips generated speech without recording a microphone. The 0.2 qualification imports a synthetic DOCX inventory brief, writes tables and operating notes, and independently checks exact units and weighted value. Results live in `data/verification-latest.json` and `data/verification-0.2-latest.json`; each 0.2 case retains its own evidence directory. See [runtime research](docs/RESEARCH.md), [discovered reusable patterns](docs/DISCOVERY.md), [integration research](docs/INTEGRATION_RESEARCH.md), [original verification](docs/VERIFICATION.md) and [0.2 verification](docs/VERIFICATION_0_2.md).

## Optional capabilities added in 0.3

This PC has the hash-locked MCP, OCR and analytics dependencies installed inside
`.venv`. They load in short-lived workers only when invoked; startup and idle
operation do not import FastMCP, DuckDB, ONNX Runtime or image libraries. Local
resource limits bound CPU time, wall time, memory and input/output bytes. They
are not a general OS filesystem/network sandbox. Native document parsers still
process untrusted formats; keep the pinned environment current after review.

In **Capabilities**, register an existing local MCP server, choose its explicit
protocol mode, discover its metadata, inspect each tool and select only those
you intend to enable. Nothing is selected automatically. Approval is the default
classification; designate a tool read-only only after reviewing its behavior.
Provider hints do not grant authority. Credentials use dedicated environment
references, never values in model prompts. Only literal `127.0.0.1`/`[::1]` HTTP
URLs with explicit ports are supported initially; no server launching, stdio,
OAuth, remote destinations or auto-installation. An uncertain write stops its
mission and must be reconciled before retrying. See [MCP setup](docs/MCP_0_3.md).

For larger data, ask for `analytics_query`, with explicit `numeric_columns`. It
accepts CSV/TSV/flat JSON and fixed structured operations, at most two tables,
one unique-right-key inner/left join, and bounded result pages. No SQL from the
model is accepted. Decimal precision is bounded to 38 digits; unsupported range
or overflow fails, and averages report their rounding. Input snapshot hashes
identify the analyzed bytes. See [analytics limits](docs/ANALYTICS_0_3.md).

The optional dependency locks are qualified for **Windows x64, Python 3.13**.
For an offline repair on this PC:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --only-binary=:all: --find-links runtime/wheels --find-links runtime/downloads/mcp --find-links runtime/ocr/wheels --require-hashes -r requirements-mcp.txt -r requirements-ocr.txt -r requirements-analytics.txt
.\.venv\Scripts\python.exe scripts/provision_ocr.py
.\.venv\Scripts\python.exe -m pip check
```

For a new box, obtain the exact wheels while online and retain all wheelhouses,
model weights, native runtime binaries, model cards/notices, Python installer
and source together. See [OCR provisioning](docs/OCR_0_3.md) for its public
wheel manifest and offline model extraction. The source ZIP excludes models,
installed dependencies and private state; it is not a complete offline disk
image. Optional locks for other OS/Python combinations need separate review
and qualification; the core remains portable.

Release qualification and delivery: [0.3 receipt](docs/DELIVERY_0_3.md).

## Capabilities and packaging added in 0.4

The qualified Windows box includes Qwen3.5:9b vision/tool use, Sherpa streaming speech, Kokoro natural speech, Silero voice detection, anonymous speaker segmentation, FLUX.2-klein image generation, Docling layout/table extraction, BGE hybrid retrieval, two compact Kiwix reference libraries and Monty bounded computation. Office, optimization, CAD, rendering, PCB, GIS and media tools use fixed structured inputs rather than arbitrary shell commands. Six starter workflows cover business documents, reference briefs and engineering artifacts alongside the existing checked workflows.

System speech was the default in 0.4: measured short-sentence latency was about 1.2 seconds versus 19.8 seconds for a cold Kokoro worker on this PC. Version 0.11 introduces the faster Cori option and a warm speech worker; see its separate measurements. One qualified 512-pixel FLUX image took 189 seconds. These are individual local measurements, not throughput guarantees. The full box was built for Windows x64 on 64 GiB RAM and an NVIDIA RTX 5060 Ti with 16 GiB VRAM; CUDA image generation requires a compatible installed driver. It is not a cross-platform disk image.

The factory Node-RED service exposes only health and event echo routes; its editor and palette installation are disabled. Owners can register additional local applications in the console. The pinned ERPNext recipe is included, but Docker/Linux prerequisites and ERP container images are not installed on this host. Local business records work without ERPNext. MCP servers, owner application connections and Docker code execution require their separate owner configuration.

Offline mode blocks external Python sockets and DNS by default. It is not an OS firewall: native processes use their own offline settings and cached assets. No provisioning download is performed on launch. Review [0.4 delivery and limits](docs/DELIVERY_0_4.md), [media](docs/MEDIA_0_4.md), [knowledge](docs/KNOWLEDGE_0_4.md) and [production tools](docs/PRODUCTION_0_4.md).
