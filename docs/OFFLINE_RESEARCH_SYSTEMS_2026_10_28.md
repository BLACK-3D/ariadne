# Offline systems and tool capabilities: 2026-10-28

Research for Ariadne on Windows x64, i9-9900K, 64 GiB RAM and RTX 5060 Ti with approximately 16 GiB VRAM. This is a researched shortlist, not an installation, security certification or benchmark. Existing Ariadne capabilities include local inference, SQLite FTS, bounded analytics, OCR and reviewed loopback MCP connections. No runtime configuration was changed during this research, and no access to E: was needed.

The best next systems work is hybrid retrieval, a constrained code interpreter, richer document structure, and finished office artifacts. Those expand what Ariadne can accomplish without requiring another always-running model. Browser, desktop and business-system control need narrower adapters and explicit boundaries before autonomous use.

## Ranked shortlist

Ranking is an engineering judgment about incremental value to Ariadne, offline completeness, fit to this PC and recurring maintenance. It is not a claim that any project is universally best.

| Rank | Capability / candidate | Observed release | Offline and Windows fit | License and decision |
|---|---|---|---|---|
| 1 | Hybrid knowledge retrieval: FastEmbed with a small local embedding model and SQLite-vec | FastEmbed 0.9.1, 2026-10-24; SQLite-vec stable 0.1.9, 2026-03-31 | Local CPU inference plus embedded storage; no Docker or GPU required | FastEmbed Apache-2.0; SQLite-vec MIT/Apache-2.0. First qualification candidate. |
| 2 | Sandboxed Python expressions and short programs: Pydantic Monty | 1.4.0, 2026-10-26 | Local worker; Windows included in project packaging/CI. Exact 1.4.0 Python 3.13 wheel still needs qualification | MIT OSS. High-value code execution candidate, with a deliberately limited Python subset. |
| 3 | Document layout, reading order, tables and structured export: Docling | 2.144.0, 2026-10-28 | Windows supported; model artifacts can be prefetched for disconnected use | MIT code; selected weights carry separate licenses. Optional richer parser above RapidOCR. |
| 4 | Office creation, formula recalculation, rendering and PDF conversion: LibreOffice | 26.8.1 announced 2026-10-02; download availability restored 2026-10-08 | Native Windows x64; headless local operations; no inference model required | MPL-2.0 plus bundled component licenses. Strong business-output adapter candidate. |
| 5 | A real offline reference library: Kiwix/ZIM | Kiwix-tools 3.8.2, 2026-03-02; latest tag verified | Read/search downloaded website archives over a local browser; Windows Reader available | Tools GPL-3.0-or-later; archive content licenses separate. Start with curated, dated corpora. |
| 6 | Local web-app operation and generated-system testing: Playwright | 1.64.1, 2026-10-14 | Native Windows browsers; browser binaries must be cached; local pages work disconnected | Apache-2.0. Build a bounded local-browser adapter before broad browser autonomy. |
| 7 | Deterministic integrations and event flows: Node-RED | 5.0.9, 2026-10-21 | Windows supported; this release requires Node >=22.9; cache all selected nodes | Apache-2.0 core; node licenses vary. Good local HTTP/MQTT bridge, not unrestricted flow generation. |
| 8 | Local coding workbench: OpenCode with local Ollama | 1.19.5 marked latest 2026-10-28; parallel 2.2.0 released 2026-10-27 | Runs natively on Windows; upstream recommends WSL for best experience | MIT. Useful separate workbench; integration requires tool/command and filesystem confinement. |
| 9 | Local business records, stock, CRM and operational workflows: ERPNext | 16.52.1 and 15.124.1, 2026-10-22 | Self-hostable; Linux/WSL/VM/container stack rather than a native Windows plug-in | GPL-3.0. Optional later system with real backup, migration and administration obligations. |
| 10 | Windows GUI access through accessibility controls: pywinauto | Stable 0.6.9; 1.0.0b1 prerelease dated October 15 | Native Windows Win32/UIA; selected app compatibility needs testing | BSD-3-Clause. Narrow app-specific fallback, with the beta kept experimental. |

## 1. Hybrid retrieval that keeps provenance

[FastEmbed](https://github.com/qdrant/fastembed) supplies local embeddings with an Apache-2.0 code license. Its [0.9.1 release](https://github.com/qdrant/fastembed/releases/tag/v0.9.1) adds NumPy execution and memory mapping for the Potion static embedding family. Those are interesting CPU options for a generator-powered appliance; upstream performance claims have not been measured on this PC.

The model publisher identifies [minishlab/potion-base-8M](https://huggingface.co/minishlab/potion-base-8M) as MIT licensed. It is an inexpensive candidate, not an assumed quality winner. Compare it against a contextual embedding model on Ariadne's own queries, identifiers, paraphrases and technical manuals before choosing. Model revision, tokenizer, license and hashes belong in the offline manifest.

[SQLite-vec](https://github.com/asg017/sqlite-vec) is a small C extension that supports Windows and both MIT/Apache-2.0 licensing. [0.1.9](https://github.com/asg017/sqlite-vec/releases/tag/v0.1.9) is the observed stable release; 0.1.10-alpha.4 is a prerelease. Its pre-v1 status warrants versioned indexes and rebuildable storage. Keep existing FTS for exact names and numbers; combine bounded lexical and semantic result sets while returning the original chunk, document version and page reference. No graph extraction is necessary for this first improvement.

If scale later justifies a separate engine, [Qdrant 1.20.0](https://github.com/qdrant/qdrant/releases/tag/v1.20.0), released October 22, is a current candidate with recent security fixes. [Qdrant-client local mode](https://github.com/qdrant/qdrant-client#local-mode) needs no server but is presented upstream for development and experiments. Do not equate it with production server performance or durability. A new daemon is not justified until retrieval measurements establish the need.

## 2. A constrained interpreter before host-shell autonomy

[Monty 1.4.0](https://github.com/pydantic/monty/releases/tag/1.4.0) adds a decimal module among other changes. The [OSS project](https://github.com/pydantic/monty) is MIT licensed; paid Full Monty is a separate product and is not required to evaluate the local interpreter. Python package metadata lists Python >=3.10 and Windows, and [upstream CI](https://github.com/pydantic/monty/blob/main/.github/workflows/ci.yml) contains Windows test/build targets. That supports evaluation on this PC, without proving the exact wheel/worker combination works here.

The [security model](https://pydantic.dev/docs/monty/concepts/security/) is language-level isolation with subprocess crash isolation. Host functions and mounts grant authority; they require their own validation. Use a fixed, verified worker binary, no mounts or host callbacks initially, bounded input/output, execution and wall deadlines, and a total session budget. Default local pools have no request deadline. Discard a session after memory/time limits. Do not restore untrusted snapshots.

The [Python subset](https://pydantic.dev/docs/monty/limitations/) excludes third-party packages and site-packages; it is not a replacement for arbitrary NumPy, shell tools or a full Python environment. First targets are transformations over explicit JSON inputs, short simulations, string processing and deterministic calculations. Preserve the existing exact analytics path for calculations that need its established evidence and limits.

[Wasmtime 50.0.0](https://github.com/bytecodealliance/wasmtime/releases/tag/v50.0.0), released October 20, is a separate candidate for portable compiled plugins. Its [sandbox design](https://docs.wasmtime.dev/security.html) limits interaction to explicitly imported interfaces. WASI imports, host functions and preopened directories determine its practical authority; fuel/epoch/resource limits and current patches still matter. It does not by itself provide the Python package ecosystem.

## 3. Document structure beyond OCR text

[Docling's latest release](https://github.com/docling-project/docling/releases/tag/v2.144.0) adds heading-hierarchy work and replacement of broken PDF text layers with OCR. [Official installation documentation](https://docling-project.github.io/docling/getting_started/installation/) supports Windows; some optional engines have narrower platform requirements. Prefer a separate on-demand parser environment so Torch and layout dependencies do not burden the normal console.

[Offline setup](https://docling-project.github.io/docling/usage/advanced_options/) supports prefetching models and supplying an artifacts directory. Remote services are opt-in, while model downloads are a separate control. Keep local/remote image fetches disabled, limit archive expansion/pages/pixels/output, and run untrusted document parsing in bounded workers. [Code is MIT; individual weights have separate terms](https://github.com/docling-project/docling#license). A selected pipeline needs exact weight-license review rather than a blanket claim that every available VLM is commercially redistributable.

An alternative for very broad format coverage is [Apache Tika](https://github.com/apache/tika). There is a current version discrepancy: [main CHANGES](https://github.com/apache/tika/blob/main/CHANGES.txt) labels 4.2.0 October 25, while the [official download page](https://tika.apache.org/download.html) still calls 4.1.0 the latest and lists signed artifacts. Treat 4.2.0 distribution availability as unresolved. Tika 4.x needs Java >=17 and Apache-2.0 licensing with notices. Its [security model](https://tika.apache.org/security-model.html) specifically treats hostile input and exposed callers as risks. Forked parsing is crash isolation; it is not blanket filesystem/network confinement. Disable cloud VLM and external fetchers for an offline profile.

## 4. Finished business artifacts

[LibreOffice 26.8.1](https://www.libreoffice.org/download/) is the observed current Windows download. The [release announcement](https://blog.documentfoundation.org/blog/2026/10/02/libreoffice-26-8-1/) describes OOXML and text-import fixes and continued 26.2 support through November 30. Availability was restored after packaging/server issues on [October 8](https://de.blog.documentfoundation.org/2026/10/08/libreoffice-26-8-1-ist-endlich-verfuegbar/). Choose a supported branch after artifact-level qualification rather than inferring stability from a download button.

[Official command-line parameters](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html) support headless conversion and a separate user profile. Proposed adapters should accept a reviewed input snapshot and fixed conversion type, run without macros or external-link refresh, write to an isolated Yikes output/profile directory, and render results for inspection. That adds invoices, proposals, decks, recalculated spreadsheets and exported PDFs without an extra model. Output compatibility and formulas require their own verification. [MPL-2.0 and bundled notices](https://www.libreoffice.org/licenses/) apply; there is no recurring core-suite subscription requirement.

## 5. Knowledge that survives disconnection

[Kiwix Server](https://get.kiwix.org/en/solutions/applications/kiwix-server/) serves local ZIM archives to browsers on a local network and supports search. [Kiwix-tools](https://github.com/kiwix/kiwix-tools) includes search and serving tools under GPL-3.0-or-later; [3.8.2](https://github.com/kiwix/kiwix-tools/releases/tag/3.8.2) is the observed latest release. The official GitHub release metadata verifies publication on 2026-03-02. Windows Reader is available through [official applications](https://get.kiwix.org/en/solutions/applications/); qualify the chosen Windows server/reader artifact rather than assuming a Linux package command applies.

The addition is curated reference content, not a new model. Start with engineering manuals, offline development documentation and selected encyclopedic material; capture publication date, archive hash, publisher and content license. Corpus sizes vary widely, so storage must be selected deliberately. Cite archive/article/date in Ariadne answers, and state that the material is a snapshot. Search does not make stale operating procedures current. Serving an archive is not permission to redistribute every article or image under the software's GPL license.

## 6. Browser automation with a real offline scope

[Playwright 1.64.1](https://github.com/microsoft/playwright/releases/tag/v1.64.1) is current as observed. It can exercise local business apps and verify websites created by Ariadne after browser binaries and dependencies are provisioned. An offline browser cannot operate an unavailable bank, email service, marketplace or remote CRM.

[Playwright MCP](https://github.com/microsoft/playwright-mcp#security) explicitly says it is not a security boundary. Origin allow/block lists do not constrain redirects, and workspace file restrictions are not secure confinement. Do not connect a personal browser profile with accounts and secrets. A future adapter should use a dedicated disposable profile, bounded lifetime, approved local origins, no arbitrary evaluation/download/upload tools, and OS/network enforcement where broader input is accepted. A verified step should refer to the actual resulting page or artifact, not merely a successful click.

## 7. Local integrations through deterministic flows

[Node-RED 5.0.9](https://github.com/node-red/node-red/releases/tag/5.0.9) is a maintained Apache-2.0 low-code runtime; its [exact package manifest](https://raw.githubusercontent.com/node-red/node-red/5.0.9/packages/node_modules/node-red/package.json) requires Node >=22.9. Official [Windows guidance](https://nodered.org/docs/getting-started/windows) establishes platform support, but its old sample versions are not current dependency pins.

It can connect local HTTP endpoints, MQTT, files and installed local devices without model calls for each event. Cloud nodes still depend on their services. Start with reviewed fixed flows and expose narrow endpoints through Ariadne's existing reviewed connections. Block automatic palette installation and model-authored Function/Exec nodes. [The editor/admin interface is unsecured by default](https://nodered.org/docs/user-guide/runtime/securing-node-red); bind locally, configure authentication and avoid handing the agent flow-administration credentials. Each added node has its own supply-chain, license and operational costs.

## 8. Local coding workbench, with plugin boundaries

[OpenCode releases](https://github.com/anomalyco/opencode/releases) show parallel 1.x/2.x lines; GitHub marks 1.19.5 latest while 2.2.0 is a separate release. Do not select the numerically largest tag without choosing a branch. The [code license is MIT](https://github.com/anomalyco/opencode), and [local Ollama configuration](https://opencode.ai/docs/providers/#ollama) is documented. [Windows support](https://opencode.ai/docs/windows-wsl/) includes native execution, with WSL recommended upstream.

This can provide repository editing, diff review and local-model code iteration. Offline operation also needs cached provider packages, language servers, formatters and test dependencies; web fetch/search, model catalogs and auto-update paths must be disabled or fulfilled locally. [Permission rules](https://opencode.ai/docs/permissions/) are application policy, not an OS sandbox. Keep it separate from Ariadne's production code and token store until a confined adapter has been reviewed. Local 8B model coding accuracy and tool reliability remain an empirical question.

## 9. A business system rather than business claims

[ERPNext releases](https://github.com/frappe/erpnext/releases) show 16.52.1 and 15.124.1 on October 22. The [GPL-3.0 project](https://github.com/frappe/erpnext) offers accounting, stock, procurement and CRM capabilities. [Frappe installation](https://docs.frappe.io/framework/user/en/installation) uses a supported Unix stack and dependencies such as MariaDB/Redis/Node; native Windows hosting is not the target. This PC has adequate potential capacity, but deployment, backups, migrations and release maintenance are recurring work.

Core records and local workflows can be available with WAN disconnected if every asset/service is local; payment gateways, external banking, email delivery, tax submission and other integrations do not become offline. An initial Ariadne adapter should read records and draft proposals or invoices; final postings, stock adjustments and external effects need dedicated owner policy and application credentials scoped to that action.

Avoid a stale shortcut: [the old Frappe Books desktop repository was archived October 2](https://github.com/frappe/books), and its [official page](https://frappe.io/books) now says the desktop app is unsupported and Books runs on the Frappe Framework. It is not a maintained tiny desktop replacement ready to bundle unchanged.

## 10. Desktop access as a narrow fallback

[Pywinauto releases](https://github.com/pywinauto/pywinauto/releases) list stable 0.6.9 and prerelease 1.0.0b1, the latter dated October 15. The [BSD-3-Clause project](https://github.com/pywinauto/pywinauto) supports Windows controls and text inspection through Win32/UIA; current [documentation](https://pywinauto.readthedocs.io/en/latest/getting_started.html) explains app/process selection.

Prefer an API or fixed command adapter when one exists. A process selector is an automation aid, not a security boundary: Windows UI automation can act on the logged-in desktop and dialog mistakes can cross application boundaries. Qualify selected application versions on a dedicated account/session, allow specific operations, and avoid unrestricted keyboard/mouse or elevated execution. The beta's new DLL-injection backend should remain outside a first production adapter. Neither this library nor a screenshot-capable agent makes every Windows program reliable under autonomy.

## Integration order and qualification

These are implementation recommendations, not authorization to add spending, background services or broad host control. First qualify retrieval and Monty against existing task fixtures, followed by Docling and office rendering. Then qualify the offline library and a fixed local-app browser. Node-RED and business-system adapters follow concrete owner-selected use cases. Desktop fallback comes last.

For every chosen addition, require a local artifact manifest, exact version and model hash, license/notice retention, no hidden runtime downloads, and a disconnected end-to-end exercise. Measure cold-start time, peak RAM/VRAM, elapsed task time, output correctness and idle resource use. Prefer on-demand workers with no warm model resident when idle unless measurements justify keeping one warm. This research made no wattage, throughput or energy-savings measurements.

Do not replace Ariadne's current engine with a large agent framework solely because it has more integrations. LlamaIndex, generic GraphRAG and multi-agent orchestrators can run with local models, but framework installation alone adds no offline evidence quality. Add graph indexing only if real multi-hop questions outperform the simpler lexical/semantic baseline enough to justify extraction inference and maintenance. SearXNG, live crawling, SaaS automation connectors and hosted coding sandboxes are not disconnected knowledge or execution solutions merely because their front ends run locally.
