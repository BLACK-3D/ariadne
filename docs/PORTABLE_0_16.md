# Complete one-click offline package · 0.16.1

The complete factory appliance is `dist/ariadne-box-0.16.1-offline.zip`. It contains the application, bundled Windows Python and native runtimes, the three pinned public local model tags (`qwen3.5:9b`, `qwen3:8b`, `qwen3:4b`), cached reference/engineering/media components, and the fast British Cori voice. It is a full ZIP, not a source-only patch. The adjacent `.sha256` and `.receipt.json` files describe the actual completed build. Large binary appliances stay local; GitHub contains the reviewed source, recipes, tests and public configuration.

Keep the factory `qwen3.5:9b` selection for the package's default operating profile. During 0.16 qualification the optional pinned `qwen3:4b` Thinking 2507 build exhausted the fixed 2,048-token response budget before any tool ran on one file-proof mission. That failure remains recorded; smaller weights do not establish faster or more reliable tool use. Existing owner selections are preserved. See [the qualification and SQLite repair notes](SQLITE_RELIABILITY_0_16_1.md).

## First use

1. Verify the ZIP's SHA-256 against its adjacent checksum. Extract it into a **new** local folder with sufficient disk space; do not overlay another installation.
2. Double-click **Start Ariadne.cmd**. The launcher selects this folder's bundled Python and starts the checked independent background host. No installation, package download or browser plug-in is required for an already complete appliance.
3. Pair the local browser using this extracted folder's newly generated `data/server.token`. Keep the key private. The default local page is `http://127.0.0.1:8080/`.
4. Open **All systems → Operations** for deterministic business/market tools and **Workflows** for checked local autonomy. Review input files and recurrence before enabling a workflow. The default factory microphone remains off.

The background host continues after the launcher and Codex close. **Stop Ariadne.cmd** stops only the verified host and its owned services. A powered-off PC cannot execute software; a generator must also power the machine and any required local devices. Existing scheduled Windows wake tasks are not copied or registered by an archive extraction.

For a headless local server or automated qualification, set the **process-local** environment variable `ARIADNE_NO_BROWSER=1` before invoking the same `Start Ariadne.cmd`. The PowerShell entry point accepts only `0` or `1`; it passes its existing fixed no-browser switch without interpolating caller arguments through CMD. This changes browser opening only, not access control, service ownership or microphone state. Clear the variable or use `0` to retain the normal browser-opening behavior.

The factory starts without owner missions, private Knowledge, ledger records, device registrations, broker credentials, browser profiles or old tokens. It creates private settings only in its own `data` directory. Import private backups separately and review their scope before any recovery; verification alone never replays work.

## Multiple local copies without sharing services

Defaults remain **8080** for the browser API, **11434** for the owned Ollama runtime and **1880** for the fixed local flow service. A new copy refuses an occupied owned-service port rather than silently using another installation's model storage or service permissions.

For an explicit second installation, choose three free ports in PowerShell before invoking its ordinary launcher:

```powershell
$env:ARIADNE_PORT = '8088'
$env:ARIADNE_MODEL_PORT = '11448'
$env:ARIADNE_FLOW_PORT = '1888'
& '.\Start Ariadne.cmd'
```

The model and flow settings accept only decimal port numbers from 1 to 65535; their hosts remain numeric loopback. The detached host forwards these validated settings and keeps this copy's native executable, model cache and private state inside this box. The first factory flow configuration uses the selected port. An explicit flow-port change that conflicts with existing owner routes fails without rewriting those routes. Preserve the same port choices for subsequent launches of that installation.

`ARIADNE_OLLAMA_URL` retains its separate meaning: an explicitly operator-managed model server. It bypasses owned-model startup; it is not evidence that a second copy starts its own model runtime. Qualification of the full appliance uses a separate owned model process instead.

The independent launcher never adopts an existing owned-model listener. The legacy foreground provisioner can recognize its original default-port PID record; explicit alternate ports refuse that weaker reuse path because the old record does not identify a listener port.

## Offline scope

The application and its deterministic local workflows need no public Internet or API subscription. Saved evidence retains its dates and missing/stale labels. Fresh weather, repository releases and current market prices remain unavailable without their external sources. Local Home Assistant actions additionally require the separately powered, explicitly configured service and device scope; no endpoint or credential is fabricated by the package.

The local Python network policy remains enabled by default. Owned Ollama uses its offline/cloud-disabled environment, fixed flows disable editor/module installation and use their cached runtime, and native components use their existing offline settings. These are application/process controls, not an OS firewall. Fresh-folder qualification records these controls and exact process/storage identities. A test performed with those controls while the PC's public uplink is present is not represented as a physical Wi-Fi-off test.

## Build integrity

The full current factory exporter uses its explicit source/runtime allowlist, sanitized profiles, pinned public models and original runtime notices. A selected fast voice additionally requires the complete Cori weights/config, engine wheels, licenses and qualified installed-package manifest. Their size/hash pins are checked during the same export stream. An absent, extra or changed qualified voice file prevents publication; no new archive silently reuses a partial voice installation.

Every member has a size and SHA-256 in `BOX-MANIFEST.json`; the receipt/checksum bind the complete ZIP. Files are read through the existing guarded handles, source changes during streaming fail, and publication never overwrites an existing archive or prior partial. The previous complete appliance remains unchanged. New York/Chicago time-zone resources, demonstration inputs and 0.16 workflow documentation are included for offline use.

See [autonomous local operations](AUTONOMOUS_OPERATIONS_0_16.md), [operations contracts](OPERATIONS_0_15.md), [independent hosting](INDEPENDENT_HOST_0_12.md) and [private checked backups](CHECKED_BACKUPS_0_13.md).
