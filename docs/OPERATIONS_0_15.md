# Operations desk · 0.15

Open **All systems → Operations**. The desk runs fixed local calculations and publishes checked artifacts without calling a language model. The existing global pause holds desk operations. An interrupted browser request is not automatically retried; inspect its request ID and workspace output first.

## Business

**Review local business records** reads the existing contacts, item catalog, stock and balanced journals in one transaction. Empty records remain unknown coverage, not evidence that a physical business has no stock. Cash accounts must be explicitly identified through the tool to produce a cash selection; the desk's default review does not infer them.

**Audit an ERP export** reads a workspace JSON file using the documented ERPNext export envelope, an explicit capture timestamp and an explicit audit date. It checks submitted invoice balances and produces an ageing report. **Prepare a quote** takes a request file referring to actual local customer and item IDs, explicit quantities, dates, discount and tax rules. It creates exact integer-cent totals, stock exceptions and an ERPNext-shaped draft invoice. It never posts or sends an invoice, reserves stock, changes the ledger or moves money.

The synthetic ERP example is a separate, clearly labeled fixture. It copies public demonstration data into a new workspace directory and audits it without adding contacts or inventory. [Business contracts and examples](BUSINESS_OPS_0_15.md).

## Historical trading research

Supply a local historical OHLCV CSV and a metadata JSON file. An OpenBB-compatible column layout is accepted; the source, symbol, currency, adjustment policy, time zone, sessions and data rights remain operator responsibilities. Audit first. A study additionally needs explicit initial capital, share sizing, fees, spread, slippage and train/test/holdout splits.

The fixed long/flat SMA replay fills signals at the next same-session bar open, flattens at a preplanned session close and records cash, costs, fills, exposure and close-marked drawdown. Expanding training folds choose parameters before each test; the reserved final holdout never selects them. Reports include baseline comparisons, exact source hashes and limitations. This is Ariadne's own bounded research implementation; OpenBB, Qlib and LEAN engines are not installed or invoked. No live market feed, paper account, brokerage credential or order route is added.

The synthetic market example demonstrates the whole pipeline using fictional prices. It does not measure performance on a real market. New York, Chicago and UTC sessions work with pinned local time-zone data; unsupported zones fail explicitly. [Market-data and replay contracts](MARKET_RESEARCH_0_15.md).

## Local systems

The Home Assistant adapter reads saved observations offline, imports selected states with their source hashes and actual observation clocks, and exposes an exact owner-configured action catalog. No device connection or entity scope is inferred. The default configuration is disabled.

Configure the documented private connector and entity/action files, then explicitly enable the gateway after qualification. Credentials can be loaded from private `data/connector-secrets.json` at engine startup, which also works under Ariadne's detached host. Only referenced `ARIADNE_CONNECTOR_*` names and bounded header values are accepted; Python/runtime/proxy variables cannot be injected. Existing nonempty process values take precedence. Restart after changing credentials or connectors. The file contains plaintext owner secrets; checked private backups include it, while factory/source exports exclude it.

The desk offers saved state reads and imports. **Prepare a device-control mission** opens a draft for the local systems agent; it does not send a command. Refreshes and approved light/switch/scene operations use fixed existing connectors. A device change requires a current argument/configuration/credential-bound approval. The adapter commits an uncertain receipt before dispatch and never repeats uncertain work. Acknowledgement is distinguished from physical verification. [Scope, setup and reconciliation](LOCAL_CONTROLS_0_15.md).

## Offline operation and maintenance

These business and market tools use the bundled runtime, bounded local inputs and zero new resident services. Saved device observations require no network. Live local-device communication requires the separately powered Home Assistant service and permitted network mode; Ariadne's default process policy still permits only numeric loopback. Public Internet access is not required for these local calculations. Fresh external data cannot be invented offline.

Private backups include the optional control configuration, action/snapshot database and connector secrets, alongside workspace reports. Factory exports include public examples, code, docs and pinned time-zone assets. This release adds no model download, service subscription, broker action or microphone change. Recurring energy or monetary savings have not been measured.

The business, trading research and local systems specialists discover the tools automatically. Three new checked AI workflows can interpret their output; those optional workflows use the selected model separately. They do not run or enable a recurring schedule merely by being added.
