# Independent review of Ariadne 0.4 media and production extensions

Review date: 2026-10-28. Scope: `media_tools.py`, `_media_worker.py`, `model.py`, `voice.py`, `production_tools.py`, and `_production_worker.py`, plus the registry/engine/worker boundary and relevant tests. This reviewer implemented the separate knowledge provider, not these media/production modules.

## Executive summary

| Severity | Confirmed findings | Status |
|---|---:|---|
| Critical | 0 | None identified in the reviewed application code |
| High | 0 | None identified in the reviewed application code |
| Medium / P2 | 1 | Resolved: system-speech route and executable/model/configuration identities are bound; independent regression tests pass |

Recommendation: approve the reviewed application boundaries after the verified P2 fix; no confirmed open finding remains in this scope. Native engines remain a material trusted dependency; bounded workers are not general filesystem or network sandboxes. This is a focused source review and fixture validation, not a certification of native parsers or upstream binaries.

## Baseline and changes

The project is not a Git checkout: `git rev-parse --is-inside-work-tree` returned “not a git repository.” Git blame, historical fix commits and commit-level regression claims are unavailable. The reviewer read `model.py` and `voice.py` directly from `dist/ariadne-box-0.3.0-source.zip` and compared them with the working files. The four extension modules are new relative to that source package, so those portions received a standard security review rather than an invented historical differential analysis.

The baseline already had proxy-free, redirect-blocked Ollama requests, bounded model responses, and owner-configured Whisper/Piper/SAPI command adapters. The changed adapter adds image capability checks, normalized image bytes, no tools on the image-specific inference request, and GPU serialization. Voice adds local Sherpa/Kokoro workers and backend selection. New production operations use validated data and fixed constructors/templates/CLI arguments; model input does not become a shell command or a caller-selected script/executable.

The application contains 33 Python source files. This review uses a focused, one-hop strategy for the six requested files, with deeper analysis of native process creation, output commits, path validation and approval fingerprints. UI, business/automation providers and the reviewer's knowledge implementation are outside this independent scope.

## P2: System-speech configuration changes preserve an old approval fingerprint

Initial locations: `ariadne/media_tools.py:226` (`fingerprint_material`) and `ariadne/voice.py:87` onward (system speech route). These line numbers describe the initial reviewed snapshot; remediation changes them.

The media provider initially bound the media profile, selected model and output-existence state. `media_speak` with `backend="system"` instead selected an executable and model from `Settings.piper_bin`/`piper_model`, or the discovered Windows SAPI executable. Those route identities were absent from the fingerprint. Keeping the same data directory, API token, selected conversational model, media profile and arguments while changing the configured Piper binary/model produced **the same full `ToolRegistry.fingerprint`**.

Concrete reproduction used two inert executable/model fixtures under a temporary Yikes directory. It constructed two registries with different Piper paths and identical `{text, output, backend:"system"}` arguments and compared the real HMAC fingerprints. Output: `changed_piper_configuration_same_approval_fingerprint: True`. No fake executable was run and no external data was sent.

An operator configuration change across a restart can therefore leave a pending approval valid for a different local executable/model route than the one previously reviewed. This requires a configuration or runtime-asset change by someone already able to modify the installation; it is **not** a demonstrated arbitrary-code exploit available solely to model-supplied arguments. The impact is an approval contract violation and silent reuse of authorization for a changed execution route.

Verified remediation: the fingerprint now includes the effective speech route, configured Piper binary/model, discovered SAPI executable, hashes of the selected executable/model/sidecar configuration, and trusted adapter/voice/worker/model/bootstrap source identities. The reviewer read the patched implementation and ran `tests/test_extension_review.py`: both independent tests passed in 2.160 seconds. They verify changed executable/model paths and changed binary/model bytes at unchanged paths through the full registry HMAC. The media owner adds complementary provider-level tests. Finding status: **resolved**.

## Controls checked

- **Input paths and output commits:** media/production snapshot reads use the registry's checked relative paths, held parent directories and file descriptors, byte bounds and content hashes. Output commits create exclusive temporary files and exclusive destinations; existing output files are not overwritten. Input snapshots are passed as bytes rather than reopened caller paths by parsers.
- **Approval and source binding:** production writes require approval. Production fingerprints include validated arguments, engine metadata, source hashes, adapter/worker source hashes and manifest identity. The final checked GIS/FFmpeg snapshot is retained for execution so a later source edit does not substitute different input bytes. Engine registry HMAC comparison protects the actual provider route.
- **Code execution:** symbolic expressions are walked as a small allowed AST and built with SymPy constructors; `parse_expr`, string `sympify`, `eval` and `lambdify` are not used. CAD/render programs are fixed application-authored scripts and JSON parameters. There is no model-facing arbitrary native command, Python-script, project, plugin or executable-path argument.
- **Media isolation:** the worker rejects Python networking and child execution through an audit hook. Image normalization allows only bounded single-frame PNG/JPEG, applies the pixel limit before loading, strips metadata and returns normalized bytes. ASR/VAD/diarization require explicit 16 kHz mono PCM16 WAV snapshots. No ambient recording or voice-cloning interface is exposed.
- **Model/image calls:** the adapter checks the installed model's advertised vision capability. The vision tool restricts its configured endpoint to loopback, uses normalized bytes instead of URLs, supplies no tools to the image call, and returns content as untrusted evidence. Both proxy use and redirects remain blocked in the baseline request adapter.
- **FLUX assets:** the worker verifies the pinned manifest and every listed file, rejects unlisted artifacts and unsupported Python/pickle files, and uses a fixed pipeline with `local_files_only=True` and `use_safetensors=True`. It serializes GPU use and releases loaded Ollama models before generation. This review does not substitute for a completed disconnected GPU smoke test on this host.
- **Native production tools:** executable selection comes from an owner-provisioned installation manifest, not PATH or model arguments. The worker rechecks the native executable hash before subprocess creation and receives a minimized environment from the common worker. Generated LibreOffice input has no macros/external links. Blender uses fixed primitives/CPU rendering/factory state. QGIS consumes a normalized bounded GeoJSON collection, fixed algorithm/options, a private profile and disabled Python plugins. FFmpeg selects an explicit demuxer, restricts protocols, disables MP4 external data references and accepts no URL, playlist, filter expression or arbitrary codec arguments.
- **Resources:** fixed worker names, OS memory/CPU/process limits, bounded input/output readers and wall deadlines contain routine resource exhaustion and crashes. Native code is still trusted to honor its restricted input modes; these controls do not confer a general OS permission sandbox. Legacy SAPI/Piper/Whisper subprocess resource/cancellation behavior is inherited from the baseline and remains a separate operational limitation.

## Test coverage and blast radius

Static AST counting in application source found six direct `run_media` call sites, six `native_run` call sites, one `image_chat` caller, two `transcribe` callers, three syntactic `speak` call sites across two functions, and two `chat` call sites. These are direct syntactic counts, not a claim to count every dynamically dispatched registry/API caller. Native process changes have a medium direct blast radius; speech/model paths also serve both API and model tool entry points.

Reviewed tests cover malformed/oversized media, format restrictions, hostile/escaped paths, exclusive output commits, cancellation before work, no tool exposure on vision requests, changed snapshot identities, strict symbolic ASTs, constrained optimization, office formula/macro/external-link avoidance, GeoJSON validation, rejection of media playlists, real native conversions, and approved FFmpeg snapshot retention after source modification.

The independent invocation of `tests.test_media_tools`, `tests.test_model`, and `tests.test_production_tools` passed all **36 tests in 121.974 seconds**, including real Blender CPU rendering, FFmpeg conversions, FreeCAD STEP output, LibreOffice PDF creation, local symbolic math and optimization. That run preceded the narrow fingerprint fix; the two independent P2 regression tests then passed against the patched code. Additional gaps to retain in release notes: arbitrary hostile native parser behavior is not certified; optional unprovisioned native backends are not exercised; a current upstream package/version does not itself prove no downloads or telemetry; legacy voice subprocess cancellation is not equivalent to the new bounded-worker cancellation.

## Review limitations and follow-up

The files were under active development during review. Recheck final source hashes and rerun affected tests after fixes. There is no historical Git context and no binary reverse engineering, comprehensive dependency audit, line-coverage measurement or full external network capture. Host administrators and anyone able to replace trusted runtime dependencies remain outside the model-argument attacker boundary. Confidence is moderate to high for the inspected argument/path/approval flows, and limited for upstream native internals.

This report follows the local [differential-review skill](${USERPROFILE}/.codex/skills/differential-review/SKILL.md), using source-package comparison where Git history is unavailable and standard review for new modules.

Final observed source identities after the P2 fix (later changes require their own assessment):

| File in `ariadne/` | SHA-256 |
|---|---|
| `media_tools.py` | `0a7e6f350c29d8aca5c073cbc0543b2810a8577d40fda85fc8c38baee8fe6ea7` |
| `_media_worker.py` | `7f53fb8efa4be4416aac7640333ed672678547964583f730f8756bd7cfac4b2a` |
| `model.py` | `60ceb29a95af7965cc24ed7263ed7615e6bd9e0d780a662148387857618a9b28` |
| `voice.py` | `516929be195e87b57ade5eb15601d7cc1fd0b7d4ecd8857c42acc3e0ee6c9add` |
| `production_tools.py` | `d23ab76a0d8816d01ee7c39ff1065564b0da9011e98279c61f347934e73a853b` |
| `_production_worker.py` | `35793f1d96292139779cbb3cf8825ec5591256c2070b37a6f60135d9e3b658db` |
