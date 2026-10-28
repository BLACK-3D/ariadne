# Ariadne 0.4 delivery

The Windows offline appliance contains the application, its qualified CPython 3.13.15 runtime, three cached Qwen models, local speech/vision/image/retrieval models, native engineering and office engines, fixed local Node-RED routes, wheel caches and publisher notices. No account, API subscription or model download is needed to launch the installed local capabilities. Extract the full offline ZIP and double-click `Start Ariadne.cmd`.

The source ZIP is a separate source distribution. It does not include runtime engines or model weights. A new extracted box generates a fresh pairing key and empty owner database. Private missions, files, credentials, application registrations, source folders and backups stay on the original installation. Preserve private state separately using the backup utility.

## Deployment requirements

The complete appliance is qualified on this Windows x64 host with 64 GiB RAM, an i9-9900K and an RTX 5060 Ti with 16 GiB VRAM. Python and native engines are bundled; Windows system libraries and a compatible NVIDIA driver remain host prerequisites. The full payload is approximately 51 GiB. Allow additional room for the ZIP, extraction, generated artifacts, private state and future backups. CPU tools remain usable without the image model's CUDA capability; no performance guarantee is implied for weaker hardware.

Use `Start Ariadne.cmd` for normal launch. `start.ps1 -NoBrowser` starts the same owned services without opening the browser. The launcher creates missing factory profiles, starts the bundled local model runtime and two fixed local flow routes, then opens the console after HTTP bind. It never downloads missing assets. Ports 8080, 11434 and 1880 must be available or already owned by this box. Two copies cannot share those fixed service ports simultaneously.

The console retains its clean dark field style. It now exposes setup commands for unavailable capabilities, owner local-app registration, quick/natural speech selection and authenticated artifact previews/downloads. Binary previews use temporary authenticated object URLs; the public file route does not bypass pairing.

## Implemented scope

- Local autonomous mission loops, checked workflows, durable notes, schedules, approval-bound tool calls and interrupted/uncertain outcome handling.
- Cached Qwen3.5:9b vision and tool use, quick system speech, optional natural Kokoro speech, streaming Sherpa transcription, Silero activity detection and anonymous speaker segmentation.
- FLUX.2-klein fixed local image generation, FFmpeg transforms, FreeCAD parts, Blender rendering, KiCad manufacturing exports and QGIS buffers.
- DOCX/XLSX/PPTX/PDF generation, typed spreadsheet totals, bounded SymPy mathematics and fixed OR-Tools planning templates.
- Docling layout/table extraction, persisted BGE/SQLite hybrid retrieval, offline Kiwix reference archives and Monty computation without host callbacks.
- Local contacts, inventory and balanced journals; idempotency keys and revision-bound approvals for writes.
- Owner-registered loopback applications, fixed HTTP flow adapters, and existing reviewed local MCP clients.

Docker code execution remains disabled unless separately provisioned. No MCP server or owner application is silently connected. The ERPNext deployment recipe pins image digests and uses local bind storage and generated credentials, but Docker/Linux and ERP images are absent on this host. It is not presented as a running offline ERP installation. No independent company formation, spending, external messaging or arbitrary hardware control has been authorized or implemented by this release.

## Evidence and review

Detailed subsystem evidence and measured limits are in `MEDIA_0_4.md`, `KNOWLEDGE_0_4.md` and `PRODUCTION_0_4.md`. The earlier broad suite ran 201 tests in 520.896 seconds, with 195 passing and six platform/opt-in skips. Later targeted suites qualified the native browser redirect boundary, all native production tools, media/model route approval binding, knowledge workers, portable Python and offline network policy. Final full-suite output is retained privately at `data/qualification-0.4-final-tests.txt`; a separate fresh-folder acceptance receipt accompanies the completed archive. Earlier suite counts are not represented as covering later changes.

The independent portable Python test copied 7,160 files into a folder containing spaces and proved both Python prefixes and core module origins belonged to that copy with isolated mode and deliberately poisoned Python environment variables. It does not alone qualify the entire appliance; full ZIP extraction and actual one-click launch are separate acceptance steps.

Independent reviews used the saved 0.3 source ZIP because this workspace has no Git history. See `DIFFERENTIAL_REVIEW_0_4_SHARED.md`, `REVIEW_0_4_EXTENSIONS.md` and `REVIEW_PORTABLE_PYTHON_0_4.md`. Review covered application integration boundaries, not every line of upstream dependencies. Browser redirect/path scope, uncertain outcomes, worker cleanup and system-speech approval binding were corrected and received targeted regressions. POSIX descendant cleanup remains unqualified on this Windows host.

Offline mode is enabled by default and denies external Python sockets/DNS. The policy explicitly reports its Python-process scope. It is not an OS firewall or a filesystem sandbox; native engines use their fixed offline profiles and cached assets. Native worker CPU/memory/time limits reduce resource exposure but do not make arbitrary native parsers safe against all malicious files.

No energy or monetary savings have been measured. Idle inference is avoided, heavy workers run on demand, retrieval indexes persist, and task-specific tool schemas reduce request bytes. Natural speech and image generation are opt-in because their measured cold latency is materially higher than simple local tools. These choices reduce avoidable work without claiming measured electricity savings.
