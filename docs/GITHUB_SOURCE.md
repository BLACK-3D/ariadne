# GitHub source and private appliance data

The GitHub repository contains Ariadne's application, local web interface, tests, configuration templates, provisioning and packaging scripts, bundled reference resources and documentation. Start with [building from source](BUILD_FROM_SOURCE.md) and the root [README](../README.md).

The source is sufficient to inspect and develop Ariadne. A fresh clone needs its interpreter, dependencies and chosen model/capability assets provisioned before it can serve as an offline appliance. The repository does not include the already provisioned owner installation or its large offline export.

## What stays on the owner PC

- `data/` and `backups/`: pairing credentials, settings, local databases, conversations, mission history, personal documents and generated evidence.
- `runtime/` and `.venv/`: interpreters, executables, downloaded dependencies, model weights and caches.
- `dist/` and `staging/`: generated release archives and temporary build or qualification folders.
- Local environment files, private keys and local test databases.

The root `.gitignore` protects these paths. Initial publication uses an explicit source allowlist as well; ignored state must not be force-added. Anyone publishing a later change should inspect `git diff --cached --stat` and `git diff --cached` before pushing. Do not commit a pairing token, credentials, an owner database or personal reference books.

The root MIT license covers original Ariadne code. Third-party code, reference material and optional runtime/model assets retain their own terms; see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and the notices beside bundled resources. Provisioning a model locally does not grant permission to redistribute its weights under Ariadne's license.

## Local verification

Run core tests and the maintained command-center checks described in [BUILD_FROM_SOURCE.md](BUILD_FROM_SOURCE.md). Native tools, microphone capture, GPU models and a fully provisioned offline export require their own capability qualification on the target PC. Historical qualification documents describe the version and environment actually tested; they do not assert that every fresh clone has those assets installed.
