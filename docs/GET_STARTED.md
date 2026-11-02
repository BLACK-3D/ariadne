# Get started with Ariadne

Ariadne is a local AI assistant with a private browser interface, local conversations, business tools, saved knowledge and checked workflows. It runs on your PC; Codex and ChatGPT do not need to stay open. You choose which optional tools and connections it can use.

The Windows starter prepares the core assistant. It needs **Internet for the initial setup** to download the runtime, dependencies and local model. After that, local conversations and prepared offline tools can work without Internet. No AI account, paid API or subscription is required for local inference.

## What to download

- **Guided Windows starter:** [Download ariadne-windows-starter.zip](https://github.com/BLACK-3D/ariadne/releases/latest/download/ariadne-windows-starter.zip) and its [SHA-256 checksum](https://github.com/BLACK-3D/ariadne/releases/latest/download/ariadne-windows-starter.zip.sha256). The starter contains reviewed source and setup scripts, not preinstalled model weights or a complete offline runtime.
- **Source for developers:** [Download the source ZIP](https://github.com/BLACK-3D/ariadne/archive/refs/heads/main.zip), or clone [BLACK-3D/ariadne](https://github.com/BLACK-3D/ariadne). The same setup files are in the repository. See [Build from source](BUILD_FROM_SOURCE.md) for manual setup and optional packs.

Use the checksum supplied beside the same starter download. In PowerShell, `Get-FileHash .\ariadne-windows-starter.zip -Algorithm SHA256` shows the ZIP's hash for comparison. A starter release is different from a fully provisioned offline appliance.

## Before you start

The guided setup targets **Windows 10/11 x64 with Python 3.13 x64 already installed**. Install a current supported Python 3.13 maintenance release from [python.org](https://www.python.org/downloads/windows/) if needed. The setup does not install Python or change Windows startup tasks.

As a planning estimate, start with **16 GB RAM and about 30 GB free disk space for the core setup**. These are estimates, not measured minimum requirements or a speed guarantee. The selected 9B model weights alone are about 6.1 GiB; downloads, the Python environment and temporary extraction need additional space. A GPU is optional, but CPU-only replies can be substantially slower. Response speed also depends on available memory, model load and other applications. Optional media and engineering packs can add tens of gigabytes.

Extract the whole ZIP into a **new ordinary local folder**, such as `C:\Ariadne`. Keep its files together. This build rejects E: and linked/reparse paths. Do not extract over somebody else's installation or put their private data into your new copy.

## Set up once, then start

1. While connected to the Internet, double-click **Setup Ariadne.cmd** in the extracted folder. It creates this folder's `.venv`, installs checksum-pinned core dependencies and downloads the qualified local `qwen3.5:9b` model and its Ollama runtime. Keep the window open until it reports completion; download time depends on your connection. It does not start the assistant automatically.
2. Double-click **Start Ariadne.cmd**. The launcher starts this copy's owned background host and model service, then opens your browser. If necessary, open [http://127.0.0.1:8080/](http://127.0.0.1:8080/).
3. Open **`data/server.token` inside your own extracted folder** with a text editor and paste that pairing key into Ariadne. The file is created locally; the background launcher does not print the key into its logs. Keep it private. **Lock** clears the paired browser session.
4. Open **All systems → Capabilities** to see which tools are actually ready. The core starter does not include every optional engine shown in the catalog.

Internet access, PC wake-word listening and daily briefing collection start disabled. Your region, devices, clients, accounts and recurring work are yours to configure. An imported backup or reused installation can have existing owner settings; setup intentionally refuses an existing private installation rather than resetting it.

For later use, double-click **Start Ariadne.cmd**. Closing the browser, launcher or Codex leaves the background host running. **Stop Ariadne.cmd** stops Ariadne and its owned services. After a reboot, start it again; this setup does not register a Windows service, login task or wake task.

## A five-minute first tour

Start with a tool that needs no model:

1. Open **All systems → Service desk → Fictional demo**.
2. Choose **Load fictional examples** if the demonstration is empty. Inspect the supplied notes, draft amounts and missing-information checks. The sample is fictional; the separate **Real clients** workspace starts empty.
3. Follow the demo's review controls, then use **Export reviewed handoff** when the draft passes its checks. Inspect the local HTML/JSON packet. Review is bound to the current draft; editing it clears that review. Nothing is sent to a customer, posted to an account or collected as payment.

Then try a short local conversation:

> In three short sentences, tell me what you can do offline.

The first reply may include model-loading time. Try a second question after the model has loaded. Service desk arithmetic and fixed local reports do not wait for a language model; open-ended conversations do.

You can next add a reference under **Knowledge**, save a note under **Memory**, or inspect **Workflows**. Review a workflow's inputs, output checks and approvals before enabling recurrence. These are available tools, not a promise that a model will perform every task correctly.

## Add only the capabilities you need

| Capability | Core starter | Next step |
|---|---|---|
| Typed local conversations | Included after setup | Use the installed model; larger prompts and tasks take more compute. |
| Service desk and deterministic local workflows | Included | Supply your own inputs and review controls. |
| PDF text reading | Core dependency installed | Scanned-page OCR and layout analysis are separate packs. |
| Microphone / wake word | Off; speech assets not provisioned by setup | Follow [voice setup](WAKE_VOICE.md) and the [optional media pack](MEDIA_0_4.md). Enable listening explicitly. |
| Natural British Cori speech output | Optional | Follow [fast local voice setup](FAST_VOICE_0_11.md); its engine and assets have separate licenses. |
| OCR, semantic knowledge, image generation, engineering and browser engines | Optional | Choose packs in [Build from source](BUILD_FROM_SOURCE.md#optional-capability-packs), prepare them online and check readiness. |
| Fresh weather, news, repository updates and market data | Requires external sources | Configure your region/source and explicitly allow the relevant connection. Saved reports remain dated offline. |

No brokerage connection, real order, business account, device authority or Windows wake task is created by core setup. Local model inference does not require a recurring service fee; electricity, hardware and any optional external service still have costs.

## Keep an offline copy private

Keep this installation's `.venv`, `runtime`, model storage under `data/models` and any prepared speech assets together. The guided starter uses a local virtual environment; it is not a prebuilt, machine-independent offline appliance. To move to another PC, use a separately prepared and tested factory appliance or perform source setup there. Do not copy a Windows virtual environment to another operating system.

A fresh installation generates its own pairing key and data. **Share the public repository or reviewed starter ZIP, rather than ZIP your working folder.** Your working `data`, backups, conversations, business records, generated reports, tokens and connector settings are private. See [public-source boundaries](PUBLIC_SOURCE.md). Keep the MIT license with original Ariadne code and retain [third-party notices](../THIRD_PARTY_NOTICES.md); optional runtimes, models and voice assets have their own terms.

Local inference, saved references and prepared tools can work after disconnection. New weather, headlines, repository checks and market prices cannot refresh while offline. A powered-off PC cannot run Ariadne; a power backup must also power the PC and any required local devices.

## If something stops setup

- **Python is missing or unsupported:** install Python 3.13 x64, reopen the setup and use the same extracted folder. Other Python versions may run the core manually, but this guided dependency profile targets 3.13 x64.
- **Existing private installation detected:** use **Start Ariadne.cmd** to reopen that installation, or extract a new clean copy elsewhere. Do not delete its database or pairing key to force setup through.
- **Another model service is using port 11434:** setup refuses to adopt it. Close that service yourself if appropriate, or use the advanced operator-managed-server instructions below. Setup does not stop unrelated applications.
- **Two launcher windows close / job-bound host error:** apply the [launcher-only repair](LAUNCHER_REPAIR.md) to keep your existing model and data. Do not repeat Setup.
- **The site does not open:** read the launcher's error and inspect `data/host/status.json`. Another instance may already occupy port 8080. Do not assume a page at that address belongs to this new copy. [Multiple-copy instructions](PORTABLE_0_16.md#multiple-local-copies-without-sharing-services) describe explicit separate ports for provisioned copies.
- **A capability is unavailable:** the optional pack may not be installed. Check **Capabilities**, then its pack's setup document. Do not enable microphones, device actions or recurring work just to test a missing dependency.

For an advanced source installation, `ARIADNE_OLLAMA_URL` selects an explicitly operator-managed model server; it bypasses Ariadne's owned-model startup. The operator must verify that server's model availability, storage, privacy and access policy. This is a separate manual path, not permission for Setup to adopt a listener.

Linux/macOS can run the core with a compatible Python and separately provisioned Ollama service; see [Build from source](BUILD_FROM_SOURCE.md) and `start.sh`. The Windows setup and optional native packs are not a certified one-click Linux/macOS installer.
