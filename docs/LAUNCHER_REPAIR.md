# Repair an existing Windows starter

Use this repair if the first Windows starter opens two windows and closes without a page, and `data/host/status.json` reports **Job-bound host refused before any application or owned service startup**. A Python virtual-environment redirector created a different, job-bound process from the one the launcher verified. The repair launches the verified native interpreter while preserving the local virtual environment; it keeps the original process-ownership and job-free checks.

## Keep your completed setup

1. Download [ariadne-launcher-repair.zip](https://github.com/BLACK-3D/ariadne/releases/latest/download/ariadne-launcher-repair.zip) and its [SHA-256 checksum](https://github.com/BLACK-3D/ariadne/releases/latest/download/ariadne-launcher-repair.zip.sha256). Compare the ZIP hash using PowerShell: `Get-FileHash .\ariadne-launcher-repair.zip -Algorithm SHA256`.
2. Close any failed launcher windows. If this copy of Ariadne is already running, stop it normally with its **Stop Ariadne.cmd** before replacing launch files. Do not stop an unrelated Python or model process.
3. Extract the repair ZIP **into the existing Ariadne folder**, the one containing **Start Ariadne.cmd**. Allow replacement of the listed launch files. This small ZIP contains launch code and this guide only; it contains no model, virtual environment, private data or settings.
4. Double-click **Start Ariadne.cmd** in that folder. Pair the page with your existing private `data/server.token` if requested.

Keep `.venv`, `runtime`, `data`, downloaded models and the base Python installation. Do not rerun Setup or delete your database/token to apply this repair. New installations can instead use the [updated full starter](https://github.com/BLACK-3D/ariadne/releases/latest/download/ariadne-windows-starter.zip) in a new folder.

The repair replaces only `Start Ariadne.cmd`, `Stop Ariadne.cmd`, `start.ps1`, `scripts/stop_host.ps1`, `scripts/host_process.ps1`, `scripts/host.py` and `scripts/windows_host.py`, and adds `scripts/host_diagnostics.py`, this guide, the MIT license and a hash manifest. It leaves the rest of your installation in place.

## If startup still fails

The launcher now preserves its exit code and leaves the error window open. Each attempt tries to write `data/host/launcher-<attempt>.json` and a matching private `.log`. The JSON gives the stage, failure type and exit code without environment values, keys or command arguments. If a safe log cannot be created, the error still appears in the window. Host failures can also leave structured evidence in `data/host/status.json` or a per-request failure record.

Do not share your whole `data` folder or full private logs. For support, share the newest launcher's **stage, failure type and exit code**, and any host failure's **stage and error type**. Remove personal paths and identifiers. Never send `server.token`, credentials or connector settings.

The repair does not install a service, change Windows security settings, enable a microphone, resume schedules or adopt existing listeners. A successful local lifecycle test uses simulated application services; it does not certify every PC, model speed or optional capability. See [Get started](GET_STARTED.md) for normal setup and offline limitations.
