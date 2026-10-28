# Offline capability survey for Ariadne

Checked **October 28, 2026**. The recommendation is to build a small, local
general assistant with specialist tools loaded on demand: perception, speech,
retrieval, constrained execution and deterministic production tools. This is a
ranked engineering assessment for this PC, not a universal leaderboard.

The host inventory reports an **i9-9900K, 8 cores / 16 threads, about 64 GiB RAM,
and RTX 5060 Ti with 16,311 MiB VRAM**, driver 617.14. That permits useful local
multimodal models and creative workers, but does not establish that several
large GPU workloads can coexist. No candidate was installed, benchmarked,
activated or granted new access during this survey. All research files stay in
Yikes; E: was not accessed.

## Best next additions

| Priority | Ability | First candidates to qualify | Why it belongs in this box |
| --- | --- | --- | --- |
| 1 | A stronger general assistant with image understanding | [Qwen3.5-9B/4B](https://huggingface.co/Qwen/Qwen3.5-9B), compare [Gemma 4 12B Unified](https://huggingface.co/google/gemma-4-12B-it) | Reasoning, coding, tool use and photo/document understanding. Existing Ollama packages give a plausible Windows route; actual image/audio support must also be added and checked in Ariadne's API. These are comparison candidates against installed Qwen3:8b. |
| 2 | Streaming conversation and better spoken responses | [NeMo-Speech.cpp 0.2.0](https://github.com/NVIDIA/NeMo-Speech.cpp/releases/tag/v0.2.0), [Moonshine Voice](https://github.com/moonshine-ai/moonshine), [sherpa-onnx 2.0.1](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v2.0.1); [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) for synthesis | Compare a small CPU/native speech stack, then select one default. Add endpointing, interruption handling and optional anonymous meeting speaker labels. This develops the Jarvis interaction without requiring a giant all-in-one audio model. |
| 3 | Better grounded memory and document search | [FastEmbed 0.9.1](https://github.com/qdrant/fastembed/releases/tag/v0.9.1) + [SQLite-vec 0.1.9](https://github.com/asg017/sqlite-vec/releases/tag/v0.1.9) | Combine existing exact full-text matching with small local semantic embeddings. Keep source passages, hashes and versions; a separate vector database daemon is unnecessary until scale proves otherwise. |
| 4 | Constrained local code and transformations | [Pydantic Monty 1.4.0](https://github.com/pydantic/monty/releases/tag/1.4.0) | A promising local interpreter without requiring Docker. Its limited Python subset needs explicit execution, wall-time, output and session limits. It does not supply arbitrary Python packages or a host shell. Windows wheel/worker qualification remains necessary. |
| 5 | Finished business documents | [LibreOffice 26.8.1](https://www.libreoffice.org/download/) | Local document generation/conversion, spreadsheet recalculation, presentations and PDFs. No extra AI model is needed. Use fixed adapters, isolated profiles and verified rendered outputs. |
| 6 | Checked business planning and optimization | [OR-Tools 9.15](https://github.com/google/or-tools/releases/tag/v9.15) | Scheduling, resource allocation, assignment, packing and route problems on CPU. Return constraints and solver status; a feasible draft is different from a proven optimum. |
| 7 | Rich document structure | [Docling 2.144.0](https://github.com/docling-project/docling/releases/tag/v2.144.0) | Tables, layout and reading order beyond current OCR/text extraction. Keep RapidOCR as the lean baseline; invoke a richer parser only for documents that justify it. |
| 8 | An offline reference library | [Kiwix/ZIM](https://get.kiwix.org/en/solutions/applications/kiwix-server/) | Search preloaded encyclopedias, technical manuals and development references with citations to dated snapshots. Corpus/content licenses and storage selection matter separately from software licenses. |
| 9 | Image generation and precise editing | [FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) | Apache-2.0 model for local image generation/editing. Publisher reports roughly 13 GB VRAM; qualify full-pipeline memory and unload the conversational model for the job. |
| 10 | Deterministic local automation | [Node-RED 5.0.9](https://github.com/node-red/node-red/releases/tag/5.0.9) | Local HTTP/MQTT/event workflows can run without repeated LLM calls. Expose reviewed operations through existing Ariadne connections; secure the administrative editor and cache selected nodes. |
| 11 | Operate and test generated local web systems | [Playwright 1.64.1](https://github.com/microsoft/playwright/releases/tag/v1.64.1) | Useful for local business apps, forms and UI verification. Requires a dedicated profile and a real file/network boundary. A browser/MCP allowlist alone is not an OS sandbox. |
| 12 | Engineering and product-design artifacts | [FreeCAD 1.1.4](https://github.com/FreeCAD/FreeCAD/releases/tag/1.1.4), [Blender 5.2 LTS](https://www.blender.org/download/lts/), [KiCad 10.0.7](https://www.kicad.org/blog/2026/10/KiCad-10.0.7-Release/) | Parametric parts, product renders, electronic designs, drawings and checked exports. Favor fixed artifact templates before general scripting or desktop control. |
| 13 | Offline spatial analysis and mapping | [QGIS 4.2.3 / 3.44.15 LTR](https://qgis.org/download/) | Local maps, geodata and site planning with downloaded data/projection assets. Remote tiles/geocoding still need their services. |
| 14 | A real local business records system | [ERPNext](https://github.com/frappe/erpnext/releases) | Stock, procurement, accounting and CRM can be self-hosted. This brings a Unix/database/service stack and recurring maintenance; add it for a concrete business need rather than as an idle dependency. |

## Particularly fresh discoveries

- **llama.cpp 0.8.0 was released today**, adding LFM3 support and experimental
  realtime/audio routes. **LFM3 arrived October 27**, including a compact Omni
  model. However, installed Ollama 0.40.12 currently limits LFM3 to Apple hardware;
  the new standalone runtime is a separate qualification route on Windows.
  Its existence is not a reason to replace the working server with an untested
  release. [Runtime release](https://github.com/ggml-org/llama.cpp/releases/tag/v0.8.0),
  [Ollama platform limitation](https://github.com/ollama/ollama/releases/tag/v0.40.12).
- **Nemotron 3.5 Bolt E4B**, October 23, is an interesting compact conversational
  and tool-use controller. The official GGUF is 6.18 GB despite its “4GB” name;
  total runtime memory is additional. It uses OpenMDW-1.1 rather than an
  Apache-only weight license. [Publisher card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-4GB).
- **FLUX 3 Image Klein**, October 15, offers newer generation/editing controls;
  its publisher advertises an 8 GB INT8 route. The weight license has revenue and
  other conditions, so the Apache FLUX.2 model is the lower-friction candidate
  for an appliance intended for arbitrary businesses. [Announcement](https://bfl.ai/blog/flux-3-image-klein),
  [weight license](https://huggingface.co/black-forest-labs/flux-3-image-klein/blob/main/LICENSE.md).
- **Nemotron 3 diarization**, September 23, provides small local meeting speaker
  labeling. **VoiceChat 11B** adds full-duplex interaction but its native route
  needs a CUDA source build and is absent from prebuilt runtime archives. Keep
  that heavier integration experimental on this host. [Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization),
  [native release limitations](https://github.com/NVIDIA/NeMo-Speech.cpp/releases/tag/v0.2.0).

## What “offline” must mean here

The complete runtime, model revision, tokenizer/projector/codec, fonts, plugins,
DLLs and document/reference assets must already be stored locally. Startup and
normal operation must use local paths, with no silent model downloads, cloud
fallback, login renewal, update check or hosted embedding dependency. A cold
start and real task while network access is blocked are the acceptance evidence.

Local browser automation, Node-RED and ERP software can operate local services.
Live websites, email delivery, banking, marketplaces, current news and remote
APIs still depend on connectivity. A local library contains its downloaded
snapshot, and answers should identify its date and source.

Voice/vision models can understand instructions embedded in documents, images
or recordings. Those inputs remain untrusted data. A transcript, wake word,
speaker label or downloaded skill cannot grant tool authority. Existing owner
approval, no-replay and output-verification rules remain part of every proposed
integration. No ambient microphone/camera capture was enabled by this survey.

## Filters that removed attractive but poor defaults

1. **Model and code licenses differ.** Qwen-Image 2.1/Turbo use research terms;
   openWakeWord's included weights are noncommercial. LFM3, FLUX 3 and LTX 2.5 have
   custom terms. A permissive runtime cannot relicense its model zoo.
2. **Parameter names are not memory budgets.** An 80B MoE with 3B active parameters
   still stores much more than 3B weights. Qwen3.8-27B and similar models need
   offload/memory measurements; LTX 2.5's publisher recommends more VRAM than
   this PC has. Longer advertised context also consumes extra memory.
3. **“Local” frontends can still call clouds.** Hosted search/code execution,
   SaaS nodes, remote image URLs and cloud-tagged models are not disconnected
   capabilities. Downloads and optional integrations need explicit separation.
4. **Abandoned or ambiguous releases are not good appliance foundations.**
   Frappe Books desktop is archived/unsupported. Tika's current source/download
   version pointers disagree. FreeCAD RC/weekly builds are not the same as its
   stable 1.1.4 artifact. These findings are preserved in the detailed reports.
5. **More frameworks add maintenance.** Keep the current Ariadne engine; add
   missing capabilities through small adapters. A second general agent, graph
   pipeline or database service needs a demonstrated advantage before it adds
   permanent compute/storage and another permission system.

## Proposed qualification order

First compare Qwen3.5/Gemma 4 against the installed model using the same checked
missions. In parallel, evaluate one streaming-speech stack and hybrid retrieval.
Then qualify Monty, office output and OR-Tools. Add richer parsing, a curated
offline library, image generation and local web-app automation as separately
bounded capabilities. ERP/CAD/GIS/device integrations follow actual use cases.

For each candidate, record task correctness and failed cases, cold-start and
completed-task time, peak RAM/VRAM, dependency/model storage, idle activity and
maintenance burden. Measure watts or energy per successful task before claiming
power savings. No performance or energy saving was measured during this survey.

Use on-demand CPU workers for light tools and a bounded queue for heavier GPU
jobs. Benchmark the full pipeline rather than adding weight-file sizes to
guess peak VRAM. Keep the existing app/runtime rollback available during any
future qualification. This research does not install or activate its proposals.

## Detailed evidence and reusable inventory

- [Models and runtimes](OFFLINE_RESEARCH_MODELS_2026_10_28.md): eight primary
  candidates plus larger/deferred models, modality and Windows limitations.
- [Speech, perception and media](OFFLINE_RESEARCH_MEDIA_2026_10_28.md): ten ranked
  candidates plus VAD, cameras, segmentation and duplex-video/voice limits.
- [Systems, retrieval and business tools](OFFLINE_RESEARCH_SYSTEMS_2026_10_28.md):
  ten ranked candidates plus sandbox, licensing and deployment details.
- [Engineering, optimization, maps and media utilities](OFFLINE_RESEARCH_ENGINEERING_2026_10_28.md):
  seven additional tools and concrete adapter boundaries.
- [Machine-readable inventory](OFFLINE_CAPABILITIES_2026_10_28.json): research
  priorities only, separate from Ariadne's installed capability catalog.

Sources are primary publisher repositories, model cards, licenses, release
metadata and documentation. Discovery search results from aggregators were not
used as technical evidence. Rankings reflect hardware fit, incremental value,
offline completeness, license flexibility and recurring operating burden.
Repository popularity and recent activity do not establish freedom from flaws;
this is not an exhaustive vulnerability audit or a deployment certificate.
