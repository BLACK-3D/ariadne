# Get started with Ariadne on a fresh Windows PC

Ariadne is a local AI assistant with a private browser interface, local conversations, business tools, saved knowledge and checked workflows. It runs on your PC; Codex and ChatGPT do not need to stay open. You choose which optional tools and connections it can use.

The guided route prepares its own Python runtime and model inside the Ariadne folder. **You do not need to install Python, Git, Docker or Node, create an AI account, buy an API subscription or run setup as administrator.** It needs Internet for its initial downloads. Installed local capabilities can then run without public Internet.

Optional local vision is a separate pack. After core Setup, **Enable Vision.cmd** explicitly prepares its pinned CPU detector and small video adapter without opening a camera. A prepared offline vision add-on can instead be installed with the checksum-verified installer on a never-connected PC. See [local vision](VISION_LOCAL.md) for the exact commands, supported recordings, camera controls, measurements and limitations.

## Download and extract the whole starter

Download [ariadne-windows-starter.zip](https://github.com/BLACK-3D/ariadne/releases/latest/download/ariadne-windows-starter.zip) and its [matching SHA-256 checksum](https://github.com/BLACK-3D/ariadne/releases/latest/download/ariadne-windows-starter.zip.sha256) from this repository's release. The starter contains reviewed source and setup scripts; its first setup downloads the runtime, dependencies and model. It is different from a preloaded offline appliance.

In Explorer, right-click the ZIP and choose **Extract all**. Open the extracted folder rather than running files inside the compressed-folder view. Choose a new writable ordinary local folder, such as a new `Ariadne` folder inside Downloads. This build excludes E:, network shares and linked/reparse paths.

GitHub's source ZIP is also available from [Code → Download ZIP](https://github.com/BLACK-3D/ariadne/archive/refs/heads/main.zip). It may add an outer folder. Open the inner folder containing these files:

```text
Ariadne/
  Setup Ariadne.cmd
  Start Ariadne.cmd
  Stop Ariadne.cmd
  Check Ariadne.cmd
  Prepare Offline Copy.cmd
  START_HERE.html
  README.md
  requirements-bootstrap.txt
  requirements-server.txt
  requirements-documents.txt
  scripts/
  ariadne/
```

Keep the files together. The setup script selects its own folder; you do not need to open a terminal or change its working directory.

Both downloads support Setup and Start without manually installing Python. **Clean offline export requires `SOURCE-MANIFEST.json`, supplied by the released Windows starter.** The plain GitHub Code source ZIP does not contain that reviewed export inventory.

To compare the downloaded ZIP with its supplied checksum, open Windows PowerShell in the download folder and run `Get-FileHash .\ariadne-windows-starter.zip -Algorithm SHA256`. Use the checksum from the same release. Windows security warnings deserve inspection: verify the repository, file and checksum before proceeding. If Windows or an organization policy blocks a script or runtime, inspect that warning or ask your normal IT support. Keep security protections enabled.

## Before you start

The guided setup targets **Windows 10/11 x64** with its built-in Windows PowerShell. It keeps a checksum-pinned Python 3.13 runtime in `runtime/python`; it does not register system Python or change PATH. ARM64, Linux and macOS need a separately supported deployment path; the Windows starter is not a one-click installer for every operating system.

Plan for **16 GB RAM and about 30 GB free disk for the core**, plus your own data. These are estimates, not measured minimum requirements or a speed guarantee. The default 9B model weights alone are about **6.1 GiB**; the Ollama download is about **1.5 GB**, with Python, dependencies, caches and extraction needing additional room. A GPU is optional. CPU-only replies can be substantially slower, and other applications, memory pressure and long prompts also affect speed. Optional specialist packs can add tens of gigabytes.

Use a fresh folder for a new instance. An existing installation should use **Start Ariadne.cmd** and, if needed, the [launcher repair guide](LAUNCHER_REPAIR.md). Keep its models, pairing key and databases.

## Set up once, then start

1. Keep Internet connected and double-click **Setup Ariadne.cmd**. It prepares private Python, checksum-pinned core packages, the local Ollama runtime and the qualified `qwen3.5:9b` model. Wait for the completion message; initial downloads can take a while. Setup leaves the assistant stopped.
2. Optionally run **Check Ariadne.cmd** first. It reports requirements and readiness without installing, downloading or starting the assistant. Then double-click **Start Ariadne.cmd**. It starts this copy's owned background host and model service, then opens your browser. If needed, open [http://127.0.0.1:8080/](http://127.0.0.1:8080/). Check that you launched this copy; another local instance can already own that address.
3. Open **`data/server.token` in your own extracted folder** with a text editor. Paste that locally generated key into the pairing screen. Keep it private. **Lock** clears the paired browser session.
4. Open **All systems → Capabilities** to inspect actual readiness. Optional engines shown in the catalog are not all installed by the core starter.

The new route uses the bundled `runtime/python`, rather than a virtual environment tied to a separately installed Python. Legacy installations that use `.venv` retain their existing dependencies; use their repair instructions instead of replacing or deleting them.

Internet access in the assistant, wake-word listening and daily briefing collection begin disabled. Downloading setup assets is a separate explicit online operation. Configure your own region, devices, clients and recurring work afterward. The starter does not register a Windows service, login task or wake task, and does not enable a microphone or camera.

For later use and after a reboot, double-click **Start Ariadne.cmd**. Closing Codex, the browser or the launcher leaves the background host running. **Stop Ariadne.cmd** stops Ariadne and its owned services.

## A five-minute first tour

Start with a deterministic tool:

1. Open **All systems → Service desk → Fictional demo**.
2. Choose **Load fictional examples** if it is empty. Inspect the notes, amounts and missing-information checks. The separate **Real clients** workspace begins empty.
3. Follow the demo's review controls, then choose **Export reviewed handoff** when the draft passes its checks. Open the local HTML/JSON packet. Editing a draft invalidates its review. This demonstration sends nothing to a customer, posts nothing to an account and collects no payment.

Then try a short local conversation:

> In three short sentences, tell me what you can do offline.

The first reply can include model-loading time. Try another question after the model loads. Service desk arithmetic and fixed local reports do not wait for a language model; open-ended conversations do.

Next, add a reference under **Knowledge**, save a note under **Memory**, or inspect **Workflows**. Review a workflow's inputs, expected outputs and approvals before enabling recurrence. A model's completion claim is distinct from a verified output or a correct business result.

For recording-first perception, open **Awareness** and choose **Create synthetic demo**, or explicitly import a supported local JSON recording. Replay uses saved observations without a model or network; it does not activate a microphone or camera. Read the [recording format, review flow and evidence limits](PERCEPTION_RECORDINGS.md).

## Check it without Internet

1. Finish setup, start Ariadne and complete a short local conversation and fictional Service desk export.
2. Stop Ariadne cleanly with **Stop Ariadne.cmd**.
3. Disconnect the test PC from public Internet yourself, while keeping any local network you need powered.
4. Start Ariadne again and repeat the same local tasks. Confirm the page opens, the prepared model answers and the local handoff export is readable.
5. Reconnect when you want fresh external data.

A package inventory or source test is different from this check on your own PC. Fresh weather, headlines, repository updates, quotes and connected cloud services cannot refresh offline; saved evidence keeps its dates and stale/missing labels. A powered-off PC cannot run software, so backup power must also cover the PC and any required local devices.

## Add only the capabilities you need

| Capability | Core setup | Next step |
|---|---|---|
| Typed local conversations | Prepared local model | Use the installed model; large prompts and tasks need more compute. |
| Service desk and deterministic local workflows | Included | Supply your own inputs and review controls. |
| PDF text reading | Core dependency installed | Scanned-page OCR and layout analysis are separate packs. |
| Microphone / wake word | Off; speech assets separate | Follow [voice setup](WAKE_VOICE.md) and the [optional media pack](MEDIA_0_4.md); enable listening explicitly. |
| Natural British Cori speech | Optional | Follow [fast voice setup](FAST_VOICE_0_11.md); engine and assets have their own terms. |
| OCR, semantic knowledge, image generation, engineering and browser engines | Optional | Choose packs in [Build from source](BUILD_FROM_SOURCE.md#optional-capability-packs), prepare them online and check readiness. |
| Fresh weather, news, repository checks and market data | External sources required | Configure the source/region and deliberately allow the relevant connection. |

No broker, real order, business account or device authority is created. Local model inference needs no recurring API fee; electricity, hardware and optional external services still have costs.

## Keep an offline copy private

Keep the complete application, `runtime`, prepared model storage under `data/models`, notices and any installed optional assets together. The fresh-PC runtime is inside the folder; it does not depend on a separately installed system Python. Keep backups of your private records separately.

### Prepare a clean offline core for another PC

Use a fully set-up copy of the **released Windows starter**, including its **`SOURCE-MANIFEST.json`**. The exporter refuses a missing source manifest instead of scanning arbitrary files that could contain private material. A plain GitHub source ZIP can run Setup and Start, but does not supply this export inventory.

1. Finish Setup and the first local checks. Keep the source and installed runtime/model files intact.
2. Double-click **Prepare Offline Copy.cmd**. It creates `dist/ariadne-offline-core-<UTC>.zip`, a matching `.sha256` checksum and `.receipt.json`. The command packages prepared assets; it does not fetch missing models or turn an incomplete setup into a ready appliance.
3. Keep the ZIP, checksum and receipt together. The export contains source bound to its source manifest, sealed core Python/dependencies, verified Ollama binaries, the pinned 9B model and notices. It uses empty factory profiles and excludes your chats, pairing key, databases, clients, logs, backups, connector credentials and imported documents. Optional packs are outside the default **core** export. Local vision has a separate sealed add-on export; operators can also explicitly include its verified assets with `scripts/export_core.py --with-vision`.
4. Transfer it using storage that supports large files, such as **NTFS or exFAT**. The ZIP is many gigabytes; FAT32 cannot hold a single file larger than 4 GB. Allow additional disk room for both the ZIP and its extracted copy, beyond the installed-core planning estimate. No public fully preloaded GitHub download is promised by the starter link.
5. On the receiving Windows x64 PC, compare the ZIP's SHA-256 with the supplied checksum and use **Extract all** into a new allowed ordinary local folder. Keep every file. Double-click **Start Ariadne.cmd** directly: this prepared factory copy needs neither Setup nor Internet. Pair using that copy's newly created `data/server.token`.
6. Repeat the local conversation and Service desk checks on that PC. Hardware and optional drivers still affect capability and speed; a package manifest does not establish performance on every machine.

A never-connected PC needs this prepared package. The small source/starter ZIP still needs Internet for its initial downloads. The core exporter requires its verified source/runtime inventory; if assets or seals differ, preserve the error and use a clean current starter rather than editing manifests to force acceptance. A legacy `.venv` installation is a different setup path; keep using it or prepare a separate fresh starter for a factory export.

**Share the public starter or this verified clean factory export, rather than ZIP your working folder.** A prepared core package and the historical full optional-tools appliance are separate builds. Keep the MIT license and [third-party notices](../THIRD_PARTY_NOTICES.md); bundled engines, models and reference material retain their own terms. See [public-source boundaries](PUBLIC_SOURCE.md).

## If something stops setup

- **Missing requirements file / incomplete folder:** use **Extract all**, then open the folder containing Setup, README, requirements files and `scripts`. Run **Setup Ariadne.cmd** from Explorer. Old manual commands pasted into Git Bash or run from its home directory do not select the project folder.
- **Python was not found:** the current starter prepares its own Python. Check that you have the current complete starter and read the bootstrap error; no separate Python installation is part of this route. Earlier versions that required system Python are a different setup path.
- **Interrupted Python download or extraction:** bootstrap preserves a partial download or staging folder as a hold rather than silently retrying or overwriting it. Keep the error and inspect the named files. Avoid repeated setup attempts or deleting state to force progress.
- **Checksum or model-digest mismatch:** retain the error and stop using that download. The setup refuses changed bytes rather than silently accepting them; use a reviewed updated starter or support instructions.
- **Existing private installation detected:** use **Start Ariadne.cmd**, the existing-installation repair guide, or a new extracted folder. Preserve its database and pairing key.
- **Port 11434 already in use:** setup refuses to adopt that model service. Close it yourself if appropriate, or use the advanced operator-managed-server route below. Setup does not stop unrelated applications.
- **A launcher closes without a page / a job-bound host error:** follow the [launcher repair guide](LAUNCHER_REPAIR.md) for an earlier installed copy; avoid repeating Setup.
- **Start reports a failure:** read the retained error window. The newest `data/host/launcher-<attempt>.json` gives a static stage, failure type and exit code. Its matching `.log` is private and can contain local paths or arbitrary exception details. Share only the safe stage/type/exit summary, never your token or whole data folder. If safe diagnostics cannot be saved, the console still shows failure.
- **The site does not open:** inspect the launcher error and `data/host/status.json`. Another copy can own port 8080. [Multiple-copy instructions](PORTABLE_0_16.md#multiple-local-copies-without-sharing-services) describe explicit ports for prepared copies.
- **A capability is unavailable:** inspect **Capabilities**, then its pack guide. Enable only the devices or recurrence you intend to use.

For an advanced manual source installation, `ARIADNE_OLLAMA_URL` selects an operator-managed model server. The operator verifies that server's model availability, storage and access policy. This bypasses owned-model startup; it is a separate route from fresh setup.

Linux/macOS can run the core with compatible Python and a separately prepared Ollama service; see [Build from source](BUILD_FROM_SOURCE.md) and `start.sh`. The Windows starter and its optional native packs are not a certified installer for other operating systems.
