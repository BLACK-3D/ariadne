# Public source and private installation data

This repository publishes Ariadne source code, tests, examples and documentation. Private installation data is kept outside the public source history. Research capital amounts in the software are simulated policy profiles, not a disclosure of an individual's financial accounts or a promise of returns.

## What is excluded

Owner databases, pairing tokens, credentials, local connector settings, private business records, market-data captures, generated reports, audit evidence, backups, model weights, bundled runtimes and generated appliance archives are excluded. Example configuration files refer to environment variables or clearly labeled placeholders. Do not put real credentials into an example file.

The public history was prepared separately from private development records. Installation-specific previews and personal planning provenance were excluded or generalized. Public commit identifiers can differ from identifiers retained in private research receipts; those receipts remain local evidence and are not public deployment or profitability claims.

## Source downloads and offline appliances

GitHub's Code download contains source, not a preprovisioned offline appliance. Follow the README to prepare the runtime and permitted model/voice assets before disconnecting the deployment machine. A complete offline package requires its own inventory and disconnected-copy qualification. Source tests do not establish a physical power-outage or Wi-Fi-disconnection test.

The source-export tool is a developer release command requiring a normal local Git checkout and Git executable. It packs exact committed HEAD blobs. Untracked, staged-only and modified working-tree bytes are excluded. Commit intended public changes before exporting:

```powershell
runtime\python\python.exe -I -B scripts\export_source.py --output dist\ariadne-public-source.zip
```

Exports use the same path/privacy policy as the strict offline source-refresh consumer and additionally reject credential containers, runtime/state payloads, links, submodules and Git LFS pointers. Output is exclusive and remains inside the checkout. Normal Ariadne launch does not require Git.

A path filter cannot identify every possible secret or personal sentence in an intentionally committed source file. Review current files, complete reachable history, commit metadata, binary metadata and any GitHub releases or workflow logs before sharing a new public distribution. Never reintroduce private development history through a merge, mirror push or backup ref.
