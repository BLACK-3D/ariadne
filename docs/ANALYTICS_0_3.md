# Bounded local analytics, 0.3

Verified on 2026-10-28 with CPython 3.13.15, Windows AMD64, and the official DuckDB 2.0.0 wheel. Analytics is optional and runs only when called. It creates no persistent database, idle service, GPU workload, model call, or API charge. No energy or broader workload savings were measured.

## Interface and scope

`AnalyticsTools(registry)` provides `schemas()`, `policy(name,args)`, `execute(name,args,*,cancelled=None)`, and `status()`. The read-only `analytics_query` tool uses the existing workspace path validator and held directory/file handles. The optional cancellation callback is supplied by trusted application code, not model arguments.

The request declares one or two `tables` with ASCII names and workspace-relative `.csv`, `.tsv`, or `.json` paths. JSON must be an array of flat objects. All column references use `table_name.exact column header`; arbitrary headers are mapped to generated internal identifiers. Text IDs retain leading zeros. JSON number tokens retain their decimal spelling before numeric conversion; booleans normalize to text `true`/`false`, and null remains null. Nested objects, binary formats, URLs, compressed files, and native database files are unsupported.

Use `numeric_columns` to opt columns into exact fixed-point arithmetic. Every source cell in a numeric column must be nonblank, non-null, finite, and within the supported precision. Other columns remain text. Supported operations are selection, AND filters, grouped `count`, `sum`, `min`, `max`, `avg`, and two-factor `sum_product`. Numeric results are decimal strings; counts are integers. `avg` divides the exact sum by count in Python Decimal, with 38 significant digits and half-even rounding reported in the result. Average sorting is deliberately unavailable rather than using binary floating point.

Two tables require one inner or left equijoin from the first table to the second. The right key must be unique; cross joins and many-to-many expansion are unavailable. A left join may return null selected fields. Numeric aggregation over unmatched/null lookup values fails explicitly instead of silently dropping those values. Numeric join/filter comparisons are checked for a representable common scale.

Example, an inventory valuation by lookup category:

```json
{
  "tables": [
    {"name": "stock", "path": "business/inventory.csv"},
    {"name": "catalog", "path": "business/catalog.json"}
  ],
  "numeric_columns": ["stock.quantity", "stock.unit_cost"],
  "join": {"left": "stock.sku", "right": "catalog.sku", "type": "inner"},
  "group_by": ["catalog.category"],
  "aggregations": [
    {"op": "sum_product", "columns": ["stock.quantity", "stock.unit_cost"], "as": "inventory_value"},
    {"op": "count", "column": "*", "as": "line_count"}
  ],
  "order_by": [{"column": "inventory_value", "direction": "desc"}],
  "limit": 100
}
```

Results include input names, paths, exact-byte SHA256 hashes, byte/row counts, declared native types, matched row count, output row count, pagination/truncation, engine version, and worker elapsed time. The hashes identify the immutable bytes actually analyzed. Multiple source files are not claimed to be one global filesystem transaction. A file whose size or modification time changes during its held-handle read is rejected.

## Fixed limits

| Resource | Ceiling |
|---|---|
| Combined source bytes | 10,000,000 |
| Combined input rows | 100,000 |
| Combined normalized cells | 1,000,000 |
| Tables / equijoins | 2 / 1 |
| Columns per table | 100 |
| Column header / cell text | 100 / 10,000 characters |
| Filters / aggregations / grouping columns | 20 each |
| Sort columns | 5 |
| Result page | 1–200 rows, maximum 60,000 UTF-8 response bytes |
| Worker memory / DuckDB buffer budget | 768 MiB process ceiling / 128 MiB native budget |
| Worker CPU / wall time | 20 seconds / 30 seconds |
| Native per-query timeout | 15 seconds |
| Native processing threads | 1 ordinary thread, 1 async thread |
| Concurrent calls | 1 per adapter, also subject to the shared capability-worker slots |

These are rejection ceilings, not throughput guarantees for every input shape. Extremely wide, string-heavy, or expensive inputs can hit memory/CPU/output limits before the row ceiling. The parent bounds raw input, the Python parser bounds normalized data, and the worker bounds native execution. The output limit can reduce a page below the requested row count; `next_offset` reports progress. A single oversized result/metadata row fails with a request to select fewer columns.

Numeric precision is at most 38 digits, with bounded exponent and scale. Mixed magnitudes/scales that cannot fit one exact column fail. Aggregate bounds reserve enough space for the worst-case sum of absolute intermediates, including products. This deliberately rejects some cancellation-heavy inputs whose final value alone would fit. There is no approximate overflow fallback. Factors are explicitly widened before multiplication to prevent a narrower native integer representation overflowing despite a valid 38-digit result.

## Execution boundary

The parent snapshots regular workspace files using `ToolRegistry._path`, `_directory`, and `_open_read`, preserving the existing link/reparse/hardlink checks. Only snapshot bytes and a validated operation description reach a fixed child script. Python decodes UTF-8 and parses CSV/JSON; DuckDB's C++ file readers never receive a caller filename, binary file, or database. Scalars enter generated in-memory tables through parameterized inserts. SQL identifiers are generated `tN`/`cN`/`rN` names; values and filter values are parameters. Caller SQL, expressions, table functions, `ATTACH`, `COPY`, `INSTALL`, `LOAD`, and configuration overrides are not an API.

The child uses the shared fixed-worker bootstrap and resource limits before optional imports, an isolated Python invocation, a reduced environment without inherited owner credentials, bounded pipes, wall timeout, cancellation, and process cleanup. On this Windows host the process memory/CPU/active-process ceiling uses a Job Object. POSIX uses address-space and CPU rlimits. The native engine disables external access, unsigned/community extensions, automatic extension installation/loading, persistent secrets, and Python replacement scans. Temporary spill is disabled and configuration is locked. No HTTP extension is loaded; an extension-dependent setting was intentionally omitted because trying to set it would itself require that extension.

These controls are **not a full operating-system filesystem/network sandbox**, nor a proof against a native-library vulnerability. The host process retains OS access needed to load its trusted runtime. The security boundary relies on the fixed operation builder, Python normalization, reduced engine capabilities, process separation, resource controls, and reviewed native wheel. Docker was not used or claimed as tested.

## Reproducible dependency and offline install

[Official PyPI release metadata](https://pypi.org/pypi/duckdb/2.0.0/json) identifies the zero-base-dependency wheel. The reviewed host artifact is `duckdb-2.0.0-cp312-abi3-win_amd64.whl`, 15,368,860 bytes, SHA256 `f61c96547ed432f54dae42a717fd88563eda55cb47549715fac9f61f770c205a`. The actual vendored wheel hash matched this value. `requirements-analytics.txt` currently locks this Windows AMD64 artifact; another platform needs a separately verified wheel and lock update, not a source build or global install.

From the project directory, install only in the project environment:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --find-links runtime\wheels --require-hashes --only-binary=:all: --no-deps -r requirements-analytics.txt
```

Keep the vendored wheel, application source, and Python environment/runtime when preparing an offline copy. No runtime package download, extension download, cloud service, or paid account is required. Drive E: is excluded by the application storage policy.

## Qualification evidence

`python -m unittest tests.test_analytics -v`: **20 tests; 19 passed and 1 hardlink-creation privilege skip**, in 19.518 seconds on this host. Tested behavior includes:

- Actual bounded worker: `3×2.5 + 2×4.25 = 16`, with a hash of analyzed source bytes.
- Actual 100,000-row exact sum: 100,000 values of `0.01` returned `1000`, closing the earlier 2,000-row small-table ceiling for this fixture.
- A 6,000-row lookup join and grouped weighted totals matched an independently accumulated `fractions.Fraction` oracle.
- Raw JSON decimal precision and large positive/negative cancellation preserved a remaining `0.01`, including two-factor products.
- Exact numeric filter precision did not round `0.1001` into a match for `0.1`; null/blank/nonfinite/boolean numeric inputs and potential intermediate overflow failed.
- SQL-shaped headers and cells stayed data; unsupported arguments, duplicate JSON keys, deep/nested/binary input, excessive rows/bytes, paths outside the workspace, duplicate lookup keys, and excessive output failed safely.
- Direct trusted test inspection confirmed native external access/extension/replacement-scan/configuration restrictions. Attempts to unlock settings, attach files, read native CSV, install/load HTTP, or copy to a file were rejected; the output file did not exist.
- Cancelling an active real worker returned a cancellation error, reaped the subprocess, and completed in under five seconds.
- A separate operator-only memory probe applied the same 768 MiB limit before a real DuckDB import. Repeated 32 MiB allocations raised `MemoryError` after 771,751,936 bytes of retained probe buffers, below the 805,306,368-byte process ceiling. The difference includes runtime/native allocations and granularity. This demonstrates enforcement for that allocation probe, not every native allocation path.

A separate 10,000-row two-factor fixture (80,020 source bytes) returned exact `75000` in **1,106 ms parent round trip / 769 ms worker-reported elapsed time**. This is one observed CPU fixture, not a comparative benchmark or energy-saving claim. Initial qualification exposed a narrow native DECIMAL multiplication overflow; explicit widening fixed it and the regression now passes. An initial overflow test also incorrectly assumed two `1e37` magnitudes exceed 38 digits; the corrected rejection fixture uses `9e37` magnitudes.

The current user-facing integration, model selection of arguments, and production rollout require the coordinating application's integration/qualification. These module tests do not claim an end-to-end model-driven business analysis or device-wide sandbox.

Upstream behavior references: [DuckDB 2.0 configuration](https://duckdb.org/docs/2.0/configuration/overview), [fixed-point numeric semantics](https://duckdb.org/docs/2.0/sql/data_types/numeric), and [DuckDB security guidance](https://duckdb.org/docs/2.0/operations_manual/securing_duckdb/overview).
