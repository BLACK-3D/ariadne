# Ariadne 0.4 offline appliance

The full offline appliance includes its own Windows x64 Python 3.13 runtime, application source, installed native engines, model files, Python wheel caches and publisher license notices. It starts from an extracted folder using `Start Ariadne.cmd`; the launcher prefers `runtime/python/python.exe`. No system Python, model download, account login or hosted model API is required for the installed local capabilities. A compatible Windows system and NVIDIA driver remain platform requirements for the CUDA image model.

## Export and integrity

```powershell
runtime\python\python.exe scripts/export_offline.py --plan
runtime\python\python.exe scripts/export_offline.py --output dist\ariadne-box-0.4.0-offline.zip
```

The plan is saved as `dist/offline-export-plan.json`. The October 28 inventory initially contained about 196,748 files and 54,520,801,970 payload bytes (50.78 GiB); exact counts change with source documentation and the final portable runtime. Allow room for both the ZIP and its extracted copy. The export uses ZIP64 with stored entries and streams SHA-256 calculations while writing. It does not load multi-gigabyte models into memory to package them.

`BOX-MANIFEST.json` inside the archive records the relative path, byte count and SHA-256 of every payload file. The adjacent `.zip.sha256` and `.zip.receipt.json` record the complete archive hash. ZIP CRC checks alone do not establish provenance; compare the archive SHA-256 with its separately retained receipt. The exporter refuses existing archive destinations, linked/reparse files, paths outside the source box and E:. A failed export leaves its explicitly named `.zip.partial` file for inspection, rather than deleting an unrelated file or presenting it as complete.

The package retains installed engines and their model assets. It omits redundant downloaded native installer archives, disposable build/cache directories and Python bytecode. The bundled interpreter's complete qualified file set, including its `Scripts` entries, stays consistent with `PORTABLE-MANIFEST.json`; Python tools are invoked through the bundled interpreter and modules. Original hash-locked wheel caches and model license/card snapshots remain available for offline repair and audit.

## Private state stays on the original installation

The exporter uses an explicit source/runtime allowlist. It does not copy the development virtualenv, owner database, pairing/API tokens, private credentials, registered HTTP/MCP applications, owner automation or ERP settings, missions, events, schedules, memories, imported documents, search indexes, source-folder registrations, generated workspace files, browser user profiles, logs, screenshots or backups. From `data`, it copies only model storage and the known Whisper model file.

`config/box-profiles.json` is a sanitized factory template made from only the current voice, OCR, media and knowledge profiles. Absolute source-box asset paths become `${BOX}` markers; paths outside the box, unsupported profile fields and private credential keys are refused. OCR points to the bundled interpreter. Automation owner routes, external services and ERP credentials are not serialized into this template.

## First launch after extraction

The launcher invokes `bootstrap_profiles` before loading ordinary settings. The bootstrap rebases factory paths to the extracted folder and publishes only missing profiles. Each complete file is flushed to a private staging file before an atomic exclusive commit, so an interruption cannot expose an empty final JSON or token. An existing profile or database is never reset. A new database is fully initialized and seeded with `qwen3.5:9b` before its final name is published, with no copied conversations, missions, owner memory or credentials. Ordinary settings loading then creates a new local pairing token through the same atomic publication helper.

When the template enables `factory_local_flows`, the first bootstrap of the default data directory prepares only the two shipped loopback Node-RED routes, using a newly generated private flow token. It requires the shipped Node/Node-RED assets and refuses to overwrite existing flow files, a token or an automation configuration. It registers no owner application or ERP endpoint. The normal launcher subsequently starts the fixed local service; the bootstrap itself starts no process. The Node-RED editor, palette installation and arbitrary flow deployment remain disabled, and model writes still require per-action approval.

A private `.box-bootstrap-pending.json` journal is committed before a new database is published. It records a transaction claim and the single generated flow token. If power stops during first launch, the next launch uses that claim to finish missing fixed files, even if the database was already published. A completed profile or token remains intact, and the selected model is not reset on retry. Recovery accepts an existing factory file only when its bytes match the expected fixed payload; conflicting owner files or an unrelated database stop recovery without replacement. An operating-system file lock serializes simultaneous first launches and releases automatically if a process dies. The journal is removed only after setup completes. A killed process can leave harmless hidden staging files, which are neither loaded as configuration nor exported.

Ollama's home and model storage are local to the extracted box. Launching two boxes against the same fixed local ports requires stopping the existing owned services first; occupied ports never authorize taking over another application's listener.

## Relocation verification

```powershell
runtime\python\python.exe -B -m unittest discover -s tests -p test_box_profiles.py -v
```

The tests create and extract a real miniature ZIP, verify every manifest hash, bootstrap its profiles from a different folder, verify the new database model, assert private sentinel files are absent, and preserve existing owner settings on repeated bootstrap. An interruption matrix retries every profile, journal, database and fixed-flow publication checkpoint, checking complete JSON, stable tokens and journal completion. Conflict tests preserve owner model and file changes made during interrupted setup. Independent qualification also kills real child processes at these checkpoints. These fixtures start no daemon. A complete appliance acceptance run additionally extracts the full archive into a fresh folder and invokes its actual one-click launcher; that run is recorded separately from these fixtures.

Optional Docker-based ERP deployment still requires its documented Linux/container prerequisites and authority. The offline appliance includes the capabilities actually provisioned and qualified on its target platform; the movie-like breadth of an assistant does not make absent operating-system services available.
