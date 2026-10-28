# Bounded source discovery

Publication note: personal directory prefixes use ${USERPROFILE}; relative source names and the original inspection limits are preserved.
Inspected on 2026-10-28. All changes made by this discovery task are documentation under the Yikes project. Other folders were inspected read-only; no discovered application, script, environment, database, credential file, or model was loaded or executed.

## Search boundaries

Initial directory-name inventory covered `${USERPROFILE}\Documents`, `${USERPROFILE}\.codex\worktrees`, and `D:\Downloads`. Documents contained `Codex`, `Bonfire`, and `ChatGPT` containers; D: contained `MMO backend` and `mnmlauncher`. These were not opened for personal content. The search was narrowed to the relevant Jarvis worktrees once the parent task found the main project roots.

`${USERPROFILE}\OneDrive` was identified as a reparse point and excluded from further inspection. Its initial name-only listing returned no entries; no leaf content there was opened. No explicit E: path was requested. Existing reparse entries were omitted from source-directory inventories. The discovery did not scan credential directories, chat state, personal documents, user databases, or the entire filesystem.

The worktree container also contained `p04-wp04-readonly-snapshot-20260929`, `p04-wp04-rollback-consistency-20261016`, `p04-wp04-wal-readonly-20261016`, and `p13-wp09c2-m15-diagnostic-20260928`; these names were recorded without source inspection in this task.

## Source roots actually inspected

1. `${USERPROFILE}\.codex\worktrees\p05-offline-workspace-20261027\Jarvis-Threadline`
2. `${USERPROFILE}\.codex\worktrees\p13-wp10-core92-campaign\Jarvis-Threadline`

Both contain the `ariadne-agent` terminal application, which uses the OpenAI Agents SDK and an existing conversation database. Their README and dependency manifests describe a different runtime from the offline `ariadne-box` project in Yikes. Importing their entry points, environments, state, or cloud dependency stack would conflict with the current appliance's local design.

No top-level redistribution license was present in the bounded root inventory. Discovery of source is not a finding that it may be redistributed under Yikes' license. No source was copied by this task.

## Useful reference assets

Paths in this table are relative to the first source root above.

| Asset | What was inspected | Reuse value and limit |
| --- | --- | --- |
| `src/ariadne/services/documents.py` | Complete 4.6 KB source | Existing PDF, DOCX, CSV, XLSX, text readers; page markers, byte/page/row/output limits, repeated containment checks, and untrusted provenance wrapping. Useful requirements/test cases. Its DOCX reader only collects paragraphs, and truncation happens after extraction; it is not a resource-isolated parser or a complete fidelity guarantee. |
| `src/ariadne/read_adapters.py` | First 230 lines | Typed, immutable, content-addressed read observations, strict source/action matching, bounded UTF-8 content, and explicit untrusted envelopes. Useful evidence/provenance design without importing the old execution kernel. |
| `src/ariadne/services/home_assistant.py` | Definitions/constants and selected integration lines | Fixed `homeassistant://assist/context-snapshot` resource, narrow endpoint policy, environment credential references, timeouts, and a server factory using `MCPServerStreamableHttp` with all MCP tools denied. It is a constrained observation adapter, not a generic discovered-tool bridge. Thermostat write routes in this file have separate physical-action authority; they were not activated or copied. |
| `tests/test_documents.py`, `tests/test_read_adapters.py`, `tests/test_provenance.py`, `tests/test_home_assistant.py` | File names only | Candidate focused regression sources for a later bounded adaptation. Their assertions were not audited or executed in this discovery pass. |
| `moonshot/phases/P05/phase.toml` | Complete manifest | Ambient interface completion is explicitly incomplete; production hardening remains pending. Push-to-talk/offline voice in Yikes must not be described as completion of ambient wake, endurance, or household certification. |

## Acceptance and authority reconciliation

Both inspected worktrees' `moonshot/program.toml` files declare master plan 1.1, current phase P13, a pending P13-WP09R4 governance step, and no campaign/production authority. They describe the accepted P13 surface as read-only integrated evaluation, with authority `NONE` and campaign execution prohibited.

The second root contains newer-looking files whose identities differ from that program snapshot. `moonshot/phases/P13/P13-WP09C1.toml` names R4 tag `p13-wp09r4-accepted` at `e3c9aca061fb209c8cbe70bdf02b2c96aca4bafb`; its status is Builder-complete pending independent verification, with campaign and authority still prohibited/none. The associated `P13-WP09C1-contract.md` names continuation base `cc9be4247b129147b590cd6e830d68c1b8187115` / `p09-wp10d-accepted`, while explicitly preserving failed candidates with zero acceptance credit.

These are mixed historical snapshots. Manifest statements and filenames alone do not establish the current accepted contract, a completed phase, or permission to execute a campaign. The parent task is reconciling the primary `C:\Jarvis-Threadline` source. This discovery does not change any accepted scope, threshold, deadline, stop, independent review, deployment, or authority gate.

The inspected `AGENTS.md` and `moonshot/CODEX_OPERATIONS.md` reinforce external authority, independent verification, risk-based Red Team review, and preservation of explicit H1–H6 gates. They are provenance for understanding the source work, not a reason to silently activate it in a separate runtime.

## Snapshot fingerprints

SHA-256 identifies the bytes inspected; it does not establish acceptance or authorship.

| Source relative to first root | SHA-256 |
| --- | --- |
| `AGENTS.md` | `597c9bbbc990224d3780e02cfb7c6a372eee3d0136ad886b520d8bc019f2a774` |
| `README.md` | `0d601ff1fdc19d1251e10155b8762437b0653e4ea61c191913faab6bf2ea6bfb` |
| `pyproject.toml` | `2e48d729da80fd3eb3577ce3468a20a9bfbe68fbe36b5e8a576514b4494cff2d` |
| `moonshot/program.toml` | `06ebda32b79c8b45e589f2afbf7976b540aaf6d5f1ed336bdeb268c4b03e4cb1` |
| `moonshot/phases/P05/phase.toml` | `aad7269de5b2d9ee3e4d7f356f038b591f5981393d4307413577a9004834d7ba` |
| `src/ariadne/services/documents.py` | `7dcf743d1e1dc04843c2f8c576e68832512a77804494b9a45cc912fefa8dc4d0` |
| `src/ariadne/read_adapters.py` | `64a6578b6b8c7abfa0efd9fdf177973b1e56776c090c0b2a15eb6497bbbe4cf9` |
| `src/ariadne/services/home_assistant.py` | `6a9b3b1aabb0477c856b2af3ac2050aaac2114dac4754cefaec8eb1ae608496b` |

For the second root, `P13-WP09C1.toml` is `fddbacb22ef40fa7a00694a04049719d9435e46bef0c3f359330f5eadc805649`; `P13-WP09C1-contract.md` is `a21ad6ad10dfdc38ee33aa420940a919446ab92d9523cebf3de61c5bf0490a16`.

## Recommended useful adaptation

The current Yikes 0.1 source already has a local model loop, durable work, fixed HTTP connectors, and SQLite knowledge search. The clearest capability gap is owner-selected PDF/DOCX ingestion with source/page provenance, followed by a separately configured MCP tool bridge with narrow tool allowlists and existing approval enforcement. Reuse the discovered parsing requirements and untrusted-source design, while keeping the offline inference backend, Yikes storage, and authority boundary intact. This is an engineering recommendation; implementation and acceptance remain separate work.

## Implemented adoption in Yikes 0.2

The parent implementation added new bounded document readers, read-only source previews/imports, current-snapshot provenance, deterministic business tables and checked workflows. The older document/provenance code informed requirements; its cloud execution kernel and account connections were not imported or activated. Native MCP remains future work under the explicit compatibility research.

After independent review and qualification, two specific files from the primary `C:\Jarvis-Threadline\src\ariadne` tree were imported as untrusted, private Knowledge references: `services/documents.py` and `read_adapters.py`. Their hashes match the corresponding inspected worktree snapshots above. They are searchable reference text, not executable plugins, and are excluded with all private state from the distributable source archive. The source tree remains read-only. The integration/discovery notes are also indexed through a separate Yikes `docs` reference folder. Local import identities/hashes are recorded in `data/reused-references-0.2.json`.
