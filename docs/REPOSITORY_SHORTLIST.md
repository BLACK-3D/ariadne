# Ariadne capability repository shortlist

Checked **2026-10-28**, after the field-console UI was deployed. These are researched integration candidates. None of the repositories below was installed, cloned, executed or activated by this work. The current Capabilities screen continues to report installed functionality only.

Assessment used official repositories, release metadata, licenses, documentation, selected advisories and platform fit. It is not a source/binary audit, exhaustive vulnerability scan or guarantee of safety. Version observations expire; recheck the exact dependency/model bundle before provisioning. Rankings are engineering judgments for this Windows/offline appliance, not measured security or performance scores.

## Best next investments

| Candidate / observed release | Capability added | Offline and recurring-cost fit | Integration decision |
| --- | --- | --- | --- |
| [FastMCP 4.1.4](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.4), Oct 26; Apache-2.0 | Standard tool/resource connections to local services | Client-only subset; no extra LLM or always-running workflow engine required | First general connector foundation, behind Ariadne's existing approval policy |
| [DuckDB 2.0.0](https://github.com/duckdb/duckdb/releases/tag/v2.0.0), Oct 21; MIT | Larger CSV/Parquet datasets, joins, inventory/operations analytics | Embedded/on-demand CPU work; no GPU or separate DB service needed for this use | First business-data extension; bounded query templates in an isolated worker |
| [RapidOCR 3.10.0](https://github.com/RapidAI/RapidOCR/releases/tag/v3.10.0), Oct 8; Apache-2.0 | Text from scans, screenshots and image-only documents | CPU ONNX option; selected local models, loaded on demand | Lean OCR candidate to benchmark first |
| [Docling 2.144.0](https://github.com/docling-project/docling/releases/tag/v2.144.0), Oct 28; MIT code, separate model terms | Layout/reading order, structured tables, charts and richer document formats | Local/air-gapped with prefetched models; broader dependencies and optional GPU stages | Rich document candidate when simpler OCR/extraction fails required fixtures |
| [Playwright 1.64.1](https://github.com/microsoft/playwright/releases/tag/v1.64.1), Oct 14; Apache-2.0 | Browser-based workflows and verification of generated local applications | Local browser engine; internet needed only for internet destinations | Qualify a narrow browser broker before exposing general browser automation |
| [sherpa-onnx 2.0.1](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v2.0.1), Oct 26; Apache-2.0 code | Streaming speech, VAD, keyword spotting and voice processing | CPU detector can avoid keeping the LLM/GPU awake; model/dependency selection still required | Optional voice work after model-license and microphone qualification |

Release dates and licenses identify the observed upstream snapshots, not a deployment approval. The six core repositories were public, active and not archived when checked. Their model/runtime dependencies have separate licenses and security obligations.

## FastMCP: useful, but version and configuration matter

The current project is under **PrefectHQ**, and the current stable line is 4.x. Its [client documentation](https://gofastmcp.com/clients/client#protocol-negotiation) distinguishes modern MCP 2026-07-28 from the legacy initialization/session era. This can replace a hand-built dual-era transport layer, while Ariadne retains tool selection, credential handling and action approval. Prefer only the client dependencies needed for fixed HTTP connections; optional tasks, provider SDKs and interactive apps are unnecessary initially.

Selected recent security findings were checked explicitly:

- [Tool-output schema RCE](https://github.com/PrefectHQ/fastmcp/security/advisories/GHSA-gpgx-58w2-xwwf) affected 4.0 prereleases through 4.0.10 and older release lines; fixed in 4.0.11/3.4.8.
- [Post-transform authorization](https://github.com/PrefectHQ/fastmcp/security/advisories/GHSA-7xgj-8392-5q4r) was fixed in 4.1.2/3.4.10.
- [OAuth issuer binding](https://github.com/PrefectHQ/fastmcp/security/advisories/GHSA-vqqp-x5qm-5qp4) requires 4.1.2 plus an explicitly configured issuer when using those machine-to-machine providers. Upgrading alone is insufficient for that configuration.

Observed 4.1.4 is beyond those patch floors; that is not an exhaustive clean-bill claim. Start with fixed local servers, owner-selected tool/schema snapshots, bounded responses and no arbitrary subprocess server installation. Reject unsupported schemas/interactive flows. A remote read-only hint, description, skill or resource cannot grant authority. Preserve exact capability fingerprints and fail closed when server/tool identity changes.

## DuckDB: business-scale data without a permanent database server

The [2.0 announcement](https://duckdb.org/2026/10/21/announcing-duckdb-200) introduces agent-oriented CLI output, asynchronous I/O and expanded semi-structured-data support, and identifies 2.0 as an LTS line. These are useful evaluation targets; no upstream benchmark is being claimed as an Ariadne saving. Keep SQLite for application state.

Its [security model](https://github.com/duckdb/duckdb/blob/main/SECURITY.md) treats SQL as executable code and the host application as the security boundary. Untrusted/crafted files also require care. `read_only` and [extension restrictions](https://duckdb.org/docs/current/operations_manual/securing_duckdb/securing_extensions) alone are not an OS sandbox. Use a separate confined worker, validated uploaded snapshots, fixed query operations, no arbitrary SQL/ATTACH/INSTALL, disabled external access/extensions, locked settings, and CPU/memory/time/output limits. Preserve exact decimal semantics for money-like values and regression-test them independently.

## OCR and document understanding

RapidOCR's [current installation notes](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/install/) identify the modern `rapidocr` package and a separately selected inference backend. Its [model attribution record](https://rapidai.github.io/RapidOCRDocs/main/en/model_licenses/) provides Apache-2.0 provenance and SHA256 values for specific bundled PaddleOCR-derived models; it does not cover every downloadable model/runtime. [Local model configuration](https://rapidai.github.io/RapidOCRDocs/v3.9.2/en/install_usage/rapidocr/how_to_use_offline_model/) exists, but its published example is an older documentation version and must be checked against the chosen 3.10 build. The [security page](https://github.com/RapidAI/RapidOCR/security) had no detected policy or published advisories; absence of advisories is not evidence of absence of flaws.

Docling's [advanced options](https://docling-project.github.io/docling/usage/advanced_options/) support local artifact paths and explicit remote-service opt-in. Keep remote services and external plugins disabled, prefetch selected models into Yikes, allow only required local formats, and isolate parsing. Do not enable LaTeX rendering, URL inputs or an unauthenticated network service merely to add OCR. Selected [ZIP detection](https://github.com/docling-project/docling/security/advisories/GHSA-cvvj-vhrh-gxp9), [Office archive](https://github.com/docling-project/docling/security/advisories/GHSA-969p-q7h6-6fr8) and [opt-in TikZ file access](https://github.com/docling-project/docling/security/advisories/GHSA-h8jf-9f4f-2v9h) advisories were patched in 2.142.1. Current 2.144.0 is newer, but default archive limits still need tuning to the appliance's worker budget. Partial extraction must stay visible as partial evidence.

Use the same scanned documents/table fixtures to compare the lean and richer pipelines. Measure text/table correctness, seconds, peak memory, model storage and cold-start cost; select the smallest stack that meets actual acceptance criteria. Additional layout/VLM models should not compete with Qwen for VRAM without a measured benefit.

## Browser work needs its own boundary

[Playwright MCP 0.0.83](https://github.com/microsoft/playwright-mcp/releases/tag/v0.0.83), Sep 28, exposes accessibility-based browser tools. Its [README](https://github.com/microsoft/playwright-mcp#security) explicitly states that it is not a security boundary; origin/file-access convenience controls do not establish strong isolation. Current upstream also offers a CLI, but command/token-efficiency claims are not measured product evidence.

Proposed first slice: an isolated disposable profile against owner-approved local test applications, bounded snapshots/downloads, explicit file access, independent network restrictions and approval for consequential submissions/uploads. No owner browser profile, general host evaluation or broad DevTools tool exposure. Browser content remains untrusted. The official [Docker guidance](https://playwright.dev/docs/docker) warns about root disabling Chromium's sandbox and gives a separate-user/seccomp configuration for crawling. This host has no verified Docker deployment; a container example is not a tested boundary here.

## Automation and voice alternatives

The [automation assessment](REPO_RESEARCH_AUTOMATION.md) covers Home Assistant 2026.10.4, Activepieces 0.92.3-hotfix.1 and n8n 2.45.5, including official advisories and licensing. Home Assistant is useful through a fixed entity gateway. Activepieces is an optional business-flow service, not a free-cost addition: its durable deployment brings app/worker/PostgreSQL/Redis maintenance. n8n is a conditional choice for required existing integrations; its Sustainable Use terms are not a permissive open-source blanket for an AI-in-a-box business bundle. Network restrictions, workflow revision binding and uncertain-write recovery remain necessary.

The [speech assessment](REPO_RESEARCH_SPEECH.md) covers sherpa-onnx, openWakeWord, Silero VAD and ONNX Runtime. Qualify CPU VAD first; preserve push-to-talk as the current default. Supplied openWakeWord wake classifiers use CC-BY-NC-SA-4.0, so the attractive `hey_jarvis` model is not a default business-distribution choice. Sherpa code licensing does not clear every KWS/voice model; an identified model-license gap remains unresolved. No ambient recording was enabled.

## Current prerequisite: trustworthy package transport

The [Python security team's certificate incident guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576) and [FastMCP security policy](https://github.com/PrefectHQ/fastmcp/blob/main/SECURITY.md) require attention before online deployment. Yikes' isolated pip was updated to 26.2.2 with the published SHA256, and its wheel retained for offline repair. This fixes that installer's vendored bundle only. The base Python 3.13.15 and OS/container CA stores were not upgraded by this work; qualify them before new HTTPS integrations/provisioning. No machine-wide or E: changes were made.

## Proposed order and qualification

1. Fixed-server FastMCP adapter with local modern/legacy fixtures, schema-poisoning regression, credential redaction, changed-capability approval invalidation and no replay after uncertain writes.
2. Compare lean OCR against Docling on a small owner-approved scan/table corpus. Require explicit completeness/warnings and memory/deadline containment.
3. Add confined DuckDB operations when real datasets exceed current table-tool bounds; compare exact totals against an independent oracle.
4. Browser broker for generated local applications, then a specifically requested external workflow. Test navigation/network/file boundaries and approval before giving it accounts.
5. Optional CPU voice activity detection; only then evaluate a license-cleared wake model and room-specific false-trigger/latency/idle-power results.

These are future implementation proposals. They do not create new activation authority, spending, device access or accepted contracts. Development work may be substantial; recurring operating cost should be measured separately. Do not run all candidate services merely because they are available.

## Reproducible research pins

Tags resolved to these commits through official GitHub metadata on the checked date. Future installation still needs package/model hashes and dependency locks.

| Repository | Tag | Commit |
| --- | --- | --- |
| duckdb/duckdb | v2.0.0 | d54cbbe3df17e8e5ce68312f483b4155836112aa |
| microsoft/playwright | v1.64.1 | cfe490d941f992655948eee8fd4fd94e1c4234bf |
| microsoft/playwright-mcp | v0.0.83 | f183dad4a52965583e3cc1d59b88cdc279e2e57d |
| PrefectHQ/fastmcp | v4.1.4 | 26bf9080ae338d1467dca6471b15c6911f71c75c |
| docling-project/docling | v2.144.0 | a857fd208ccd61fb4a5d230384554ce778f216df |
| RapidAI/RapidOCR | v3.10.0 | bd941dae09da793e76c6ba15c7aaf40f8424232a |
