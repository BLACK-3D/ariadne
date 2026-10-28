# Offline knowledge and constrained code in Ariadne 0.4

The installed Windows x64/Python 3.13 profile adds six tools: `hybrid_search`, `knowledge_reindex`, `document_structure`, `library_search`, `library_read`, and `code_transform`. Optional Python packages live in `runtime/knowledge/python`; they do not change the normal server environment and are imported only in short-lived workers. All artifacts stay under Yikes. E: is excluded by the existing path policy.

## Installed capabilities

| Capability | Actual installed implementation | Authority and limits |
|---|---|---|
| Hybrid imported knowledge search | FastEmbed 0.9.1, BGE small English v1.5 ONNX, SQLite-vec 0.1.9 and existing SQLite FTS; reciprocal-rank fusion | Searches already imported snapshots. Up to 1,000 chunks/2 MB text; 384-dimensional vectors. Returns original source/content SHA-256, version, processor, chunk and citation. An unavailable/stale index falls back to lexical matches. |
| Semantic reindex | Explicit rebuild of `knowledge-vectors.json`, committed only if the imported corpus is unchanged | Approval required. Never registers or reads new source folders. Batches at most 64 chunks per bounded worker, with a 900-second total indexing deadline and cancellation between/during batches. Rebuild after import/update/delete; no background embedding loop. |
| Document structure | Docling 2.144.0; CPU Heron layout and Fast TableFormer models; native DOCX structure | One held, hashed workspace PDF/DOCX snapshot; 10 MB, 50 pages, 100,000 Markdown characters, at most 500 returned text items. Table/reading-order analysis is not a guarantee of perfect extraction. OCR remains the existing separate RapidOCR capability. |
| Offline reference search/read | Native Kiwix-tools 3.8.1; Python DevDocs August 2026 and SQLite DevDocs October 2026 ZIM archives | Owned on-demand loopback server exits after each operation. No idle daemon. Search can select an exact archive or interleave both. Reading accepts a returned archive-relative article path, strips HTML and does not execute scripts or fetch images. Responses carry archive date/hash and article hash. |
| Constrained Python subset | Pydantic Monty client/runtime 1.4.0, explicit verified runtime binary | Approval required. Only explicit finite JSON `data` enters; bounded JSON leaves. No filesystem mounts, host functions, external modules, inherited owner credentials, shell or network callbacks. Fresh interpreter/session per call; 32 MB interpreter heap, 2 seconds interpreter execution, 4-second per-turn host backstop, 32 KB captured printing and 64 KB result JSON. |

The fixed worker has OS resource caps: 4 GiB aggregate memory, 90 CPU seconds, 120 wall seconds, bounded protocol input/output, and at most one child process when Monty or Kiwix needs it. Worker caps are not a general operating-system filesystem/network sandbox. Docling/FastEmbed use local model paths and Python networking fails closed; Kiwix's trusted client contacts only its owned IPv4 loopback server. Native libraries still require current patches and input validation.

## Exact local assets and provenance

The hashed dependency lock is `requirements-knowledge.txt`, with 117 cached wheels verified against the SHA-256 and size published by PyPI. `config/knowledge-wheels-manifest.json` records the filename, version, source URL, size and hash. The source-included `config/knowledge-model-assets.json` freezes all 24 qualified model/native/archive assets, immutable model commits, sizes, hashes and terms. Fresh provisioning uses those exact commits and checksums; it never resolves `main` or a moving tag to choose newer model bytes. Package license/notice files are preserved under `runtime/knowledge/notices`. Normal operation does not run pip or download models.

- Embeddings: [Qdrant's BGE-small ONNX package](https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q), immutable commit `aa8f8b060edb00e03bfdd08813a2949946c8ba55`; model card identifies MIT terms. LFS files were checked against upstream SHA-256; ordinary files against immutable Git blob identities. Local SHA-256 values cover every cached model/tokenizer/notice file.
- Layout: [Docling Heron](https://huggingface.co/docling-project/docling-layout-heron), Apache-2.0, commit `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`.
- Tables: [Docling models](https://huggingface.co/docling-project/docling-models), CDLA-Permissive-2.0, `v2.3.0` resolved to commit `fc0f2d45e2218ea24bce5045f58a389aed16dc23`. Only Fast TableFormer and required configuration/notices are cached. Layout/table artifacts total 317,129,676 bytes.
- Kiwix: [official native Windows distribution](https://download.kiwix.org/release/kiwix-tools/kiwix-tools_win-x86_64-3.8.1.zip), GPL-3.0-or-later software with separate bundled component notices. The observed 3.8.2 release has no newer native Windows binary in the official distribution listing. Upstream publishes an MD5 checksum for this ZIP; provisioning checked it over official HTTPS and recorded a local SHA-256. This is not a signed upstream SHA-256 verification claim.
- Reference content: [Python August 2026](https://download.kiwix.org/zim/devdocs/devdocs_en_python_2026-08.zim) and [SQLite October 2026](https://download.kiwix.org/zim/devdocs/devdocs_en_sqlite_2026-10.zim). Python content retains PSF documentation notices; SQLite documentation is public domain. Archive and individual content notices govern redistribution separately from Kiwix software. These are dated snapshots, not current online documentation.
- [Monty 1.4.0](https://github.com/pydantic/monty/releases/tag/1.4.0) is MIT licensed. Its limited Python subset is intentional; it does not expose arbitrary third-party Python packages. [Upstream resource-limit guidance](https://pydantic.dev/docs/monty/concepts/resource-limits/) requires discarding a session after a limit. Ariadne discards every session after one invocation.

Cached knowledge wheels total 375,850,347 bytes. Kiwix binaries/DLLs plus the two ZIMs total 52,815,481 bytes. Installed dependency storage is additional. No energy or operating-cost percentage savings have been measured. On-demand processes and CPU-only retrieval leave GPU memory available to the selected conversational model while idle.

## Offline setup and recovery

For an existing cache, reinstall without networking:

```powershell
$env:TEMP = (Resolve-Path runtime\tmp).Path
$env:TMP = $env:TEMP
.venv\Scripts\python.exe -m pip install --no-index --find-links runtime/knowledge/wheels --require-hashes --target runtime/knowledge/python -r requirements-knowledge.txt
.venv\Scripts\python.exe scripts/provision_knowledge.py --layout
```

The provisioner verifies the cached wheel/model manifests, preserves notices, checks installed versions, rebases native Kiwix/archive paths from verified relative assets for the current PC/folder, and writes `data/knowledge-config.json`. That file and indexes are private local runtime state, not source ZIP contents. Missing assets cause a clear unavailable result; they do not trigger an online fallback. The installer target must be empty for a fresh reconstruction; deliberate upgrades use pip's explicit `--upgrade` option.

An installation owner can explicitly fetch the fixed optional assets while online using `--fetch-model --online`, `--fetch-layout --online`, or `--fetch-kiwix --online`. Already verified cached artifacts are reused without network requests. `--lock-wheels --online` verifies already cached binary wheels with PyPI and creates the lock. Normal provisioning and all model-facing tools remain offline. Model downloads use the frozen immutable revisions and artifact SHA-256 values. A future model upgrade requires an intentional reviewed change to the source asset lock. Existing cached assets should also be copied as an appliance backup.

## Qualification

`tests/test_knowledge_tools.py` exercises actual installed native components, including semantic paraphrases absent from lexical results, stale-corpus fallback with changed source/version hashes, finite JSON arithmetic, unavailable dependencies, hostile workspace paths, filesystem/third-party import denial, infinite loops and memory exhaustion in Monty, genuine DOCX tables, a generated PDF table with CPU layout models, and Kiwix search/full article reading with path confinement. The private loopback tests require a test environment that permits loopback sockets.

The first complete qualification run passed all 10 tests in 72.651 seconds on this PC. After adding fault/race checks for atomic batched indexing, the **12-test suite passed in 70.561 seconds**. The two added cross-PC-path and immutable-source provisioning tests passed together in 0.304 seconds, bringing current coverage to 14 tests. These are test elapsed times, not per-document throughput or model-quality benchmarks. Runtime networking was blocked for Docling and embedding workers while they processed the fixtures. Kiwix used its owned loopback server and left no retained daemon. Tests establish these fixtures and bounds; they do not certify arbitrary hostile native document parsers or all real-world PDF layouts.

The installation's initial 114-chunk production index completed in 50.751 seconds; a subsequent hybrid query completed in 3.716 seconds. These are single observed elapsed times on this PC, not statistical performance guarantees. Indexing now uses batches of up to 64 chunks so a larger corpus cannot monopolize one worker until it exceeds the CPU bound. The index remains a rebuildable cache; source documents and provenance stay in the existing store.
