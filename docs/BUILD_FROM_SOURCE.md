# Build Ariadne from source

For a guided first installation and a short tour, start with [Get started](GET_STARTED.md). The Windows starter requires one-time online preparation; it is not a preprovisioned offline appliance.

A Git clone contains the application, local UI, tests, dependency locks, asset manifests and bundled reference documents. It does **not** contain Python/Ollama runtimes, model weights, installed native engines, wheel caches or owner data. Prepare those while connected before expecting outage operation.

## Windows quick start

The guided setup targets native **Windows 10/11 x64 and Python 3.13 x64**; use that Python profile for the qualified optional binary packs. Install a currently supported Python 3.13 maintenance release and Git, clone this repository with byte-preserving line endings, and open PowerShell in its root. Use `git -c core.autocrlf=false clone https://github.com/BLACK-3D/ariadne.git`; the starter ZIP already preserves committed file bytes. Keep the project on an allowed local disk; this build excludes **E:** and linked/reparse paths. Core software also supports other platforms, but the optional Windows wheels and native engines need separate platform-specific provisioning elsewhere.

Create the project environment and retain the small core wheelhouse:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-bootstrap.txt
.\.venv\Scripts\python.exe -m pip download --only-binary=:all: --require-hashes --dest runtime/wheels -r requirements-bootstrap.txt -r requirements-server.txt -r requirements-documents.txt
.\.venv\Scripts\python.exe -m pip install --no-index --find-links runtime/wheels --require-hashes -r requirements-server.txt -r requirements-documents.txt
.\.venv\Scripts\python.exe -I -B scripts/setup_windows.py
```

The setup helper is for a new installation. It downloads the pinned `qwen3.5:9b` model, refuses existing private installation state or an occupied model-service port, and cleans up only its own temporary provisioning process. It does not launch Ariadne. For the guided equivalent, double-click **Setup Ariadne.cmd** instead of running the environment preparation block above.

Then **double-click `Start Ariadne.cmd`**, or run:

```powershell
& '.\Start Ariadne.cmd'
```

Open `http://127.0.0.1:8080` and pair using the newly generated key in this installation's `data/server.token`. Open that file locally; the detached launcher does not print its value into logs. The Windows launcher uses bundled `runtime/python/python.exe`, or this project's `.venv/Scripts/python.exe` when no bundle is present. It has no system-Python fallback. It starts the owned model service and opens the browser after the application binds.

The factory bootstrap selects **`qwen3.5:9b`** in a new database. The pinned `qwen3:4b` remains optional, but its Thinking 2507 build exhausted the fixed 2,048-token response budget before tools on one fresh-copy file-proof mission. Do not assume that smaller weights provide the same tool reliability or faster completion. Review [model qualification limits](SQLITE_RELIABILITY_0_16_1.md) before selecting another installed model in **Settings**. An existing owner's choice is preserved. Model downloads record digests; a qualified model tag whose upstream digest changes fails validation rather than silently accepting replacement bytes.

New installations have Internet access and PC wake-word listening **off**. Enable them deliberately in the interface after the corresponding dependencies are ready. The Internet switch controls public web reading and briefing collection; it does not install models or packages. Initial provisioning commands contact their upstream sources directly.

## Optional capability packs

Run only the packs you need, using the same project Python. Provisioning downloads and verifies assets; it does not establish external accounts or grant tools new authority. Read the linked pack documents for limitations and licensing.

**Whisper fallback speech input** ([voice setup](WAKE_VOICE.md)):

```powershell
.\.venv\Scripts\python.exe scripts/provision_voice.py
```

**Fast Cori speech output** ([setup and pinned dependencies](FAST_VOICE_0_11.md)): after installing the existing media dependencies, run `scripts/provision_speech.py --download --install`, then `scripts/provision_speech.py --configure` while the server is stopped. Cached installation and verification work without network access. This add-on's pinned wheel set targets Windows x64.

**CPU OCR** ([details](OCR_0_3.md)); its models are extracted from the verified RapidOCR wheel:

```powershell
.\.venv\Scripts\python.exe -m pip download --only-binary=:all: --require-hashes --dest runtime/ocr/wheels -r requirements-ocr.txt
.\.venv\Scripts\python.exe -m pip install --no-index --find-links runtime/ocr/wheels --require-hashes -r requirements-ocr.txt
.\.venv\Scripts\python.exe scripts/provision_ocr.py
```

**DuckDB analytics and the MCP client** ([analytics](ANALYTICS_0_3.md), [MCP](MCP_0_3.md)):

```powershell
.\.venv\Scripts\python.exe -m pip download --only-binary=:all: --require-hashes --dest runtime/wheels -r requirements-analytics.txt
.\.venv\Scripts\python.exe -m pip install --no-index --find-links runtime/wheels --require-hashes -r requirements-analytics.txt
.\.venv\Scripts\python.exe scripts/provision_mcp.py download
.\.venv\Scripts\python.exe scripts/provision_mcp.py install
```

MCP servers and their allowed tools still require owner registration. Installing the client connects no external service.

**Knowledge, document structure and offline libraries** ([details](KNOWLEDGE_0_4.md)):

```powershell
.\.venv\Scripts\python.exe -m pip download --only-binary=:all: --require-hashes --dest runtime/knowledge/wheels -r requirements-knowledge.txt
.\.venv\Scripts\python.exe -m pip install --no-index --no-deps --find-links runtime/knowledge/wheels --require-hashes --target runtime/knowledge/python -r requirements-knowledge.txt
.\.venv\Scripts\python.exe scripts/provision_knowledge.py --fetch-model --online
.\.venv\Scripts\python.exe scripts/provision_knowledge.py --fetch-layout --online
.\.venv\Scripts\python.exe scripts/provision_knowledge.py --fetch-kiwix --online
.\.venv\Scripts\python.exe scripts/provision_knowledge.py --layout
```

**Streaming speech, natural speech, VAD and anonymous speaker labels** ([details](MEDIA_0_4.md)):

```powershell
.\.venv\Scripts\python.exe scripts/provision_media.py --install
```

This supplies the local Sherpa decoder used by the PC wake listener. FLUX image generation is a separate opt-in download:

```powershell
.\.venv\Scripts\python.exe scripts/provision_media.py --flux --install
```

The selected FLUX model files alone total about **16 GB**, before CUDA packages and installed copies. GPU/driver compatibility needs verification on the target machine. The media provisioner's online wheel step resolves dependencies and rewrites its generated lock; retain and review those changes. An exact outage repair uses retained caches with `--offline --install`, adding `--flux` only for the image pack.

**Office, exact planning and native engineering tools** ([details](PRODUCTION_0_4.md)):

```powershell
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-wheels --install-cached
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-native ffmpeg
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-native freecad
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-native libreoffice
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-blender-python --install-cached-blender
.\.venv\Scripts\python.exe scripts/provision_engineering.py --pack qgis
.\.venv\Scripts\python.exe scripts/provision_engineering.py --pack kicad
```

**Local flows and browser runtime**:

```powershell
.\.venv\Scripts\python.exe scripts/provision_automation.py --pack all
```

This caches Node-RED and Playwright components. Browser tasks still require an explicitly registered local application and approval. The optional ERPNext stack requires separately installed Docker and its own setup; see `scripts/prepare_erpnext.py --help` and `config/erpnext/compose.yaml`. Docker is not installed by Ariadne. Keep Docker storage on an allowed disk.

Restart Ariadne after changing profiles. Inspect **Capabilities** for actual readiness. Configure your own region, briefing schedule and microphone preferences; those owner settings are intentionally absent from the repository. [Daily briefing](DAILY-BRIEFING.md) documents the separately installed Windows wake task.

## Verify and prepare for disconnection

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check_command_ui_contracts.py
.\.venv\Scripts\python.exe scripts/provision_all.py
```

The unit tests may skip absent optional assets. The interface contract check needs no model, network or existing private state. With Node.js available as an optional development dependency, also run:

```powershell
node --check ariadne/static/app.js
node --check ariadne/static/command.js
node scripts/check_command_snapshot.cjs
node scripts/check_routine_ui.cjs
```

Node.js is not required to run the core application. `provision_all.py` only checks the installed asset/configuration set and exits nonzero while any of its checked optional packs are missing. It is **not** a complete online installer or proof that every native operation works. Its `--install-cached` mode repairs selected packs from retained caches; run it only after preparing those caches. Exercise the actual capabilities you plan to depend on, including microphone recognition in your room.

Local inference, references and cached reports can operate without public internet after preparation. Fresh weather, news and external integrations cannot. Retain `runtime/`, model storage under `data/models/`, speech assets under `data/voice/`, locks, manifests and upstream license notices for a disconnected rebuild. Keep private application backups separate and protected.

## Build a portable appliance

The relocatable Python builder requires the base Python 3.13 installation and cached **bootstrap, server, documents, analytics, MCP and OCR** wheels in the directories prepared above. It builds from the installed base interpreter; invoke it with `.venv`, not an existing bundled interpreter:

```powershell
.\.venv\Scripts\python.exe scripts/provision_python.py
.\runtime\python\python.exe -I -B scripts/provision_ocr.py
.\runtime\python\python.exe -I -B scripts/stage_runtime_notices.py
.\runtime\python\python.exe -I -B scripts/export_offline.py --plan
```

Inspect the plan, qualify the intended capabilities, then choose a new output name:

```powershell
.\runtime\python\python.exe -I -B scripts/export_offline.py --output dist/ariadne-offline-local.zip
```

The exporter writes a ZIP64 appliance with payload hashes and a whole-archive checksum, excluding owner records and credentials. The full factory exporter requires the three exact public Qwen tags/manifests in config/public-models.json and their referenced blobs; unknown/private model tags and metadata are excluded. The notice staging command copies only the accepted tiny source notice snapshots, performs no download, and refuses differing existing files. It requires substantial additional disk space for the archive and a fresh extraction. It packages the installed allowlisted assets; it does not download missing capabilities or certify their behavior. Retain applicable third-party licenses and notices when distributing native engines or models.

Test the actual one-click launcher from a new folder on the intended Windows machine with the network disconnected. A source-export smoke test using an existing interpreter is a different check. `scripts/export_source.py` creates only the small source ZIP and cannot produce a complete outage appliance. See [portable operation](PORTABLE_0_4.md) for bootstrap and recovery behavior.
