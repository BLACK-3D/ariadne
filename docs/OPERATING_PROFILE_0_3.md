# Operating profile of the 0.3 additions

This release spends upfront installation/review effort on optional native tools.
It adds no mandatory paid API, account or recurring service license. That is not
an estimate of electricity savings, throughput gains or overall ownership cost.
Power draw and generator runtime have not been measured.

The server imports only the small adapters. A fresh Application construction
probe found none of FastMCP, DuckDB, ONNX Runtime, NumPy, Pillow, RapidOCR or
pypdfium2 loaded in the parent. Native dependencies load in short-lived workers
on explicit requests; there is no OCR daemon, analytics database service,
background MCP discovery, embedding server or extra idle inference loop.
The existing local model keeps its existing five-minute keep-alive setting.

| Addition | Per-operation ceiling |
| --- | --- |
| MCP | 384 MiB process memory, 10 CPU seconds, 20 wall seconds |
| Analytics | 768 MiB process memory, DuckDB 128 MiB budget, 20 CPU seconds, 30 wall seconds |
| OCR | 2 GiB process memory, 90 CPU/wall seconds; ONNX 2 intra-op / 1 inter-op threads |

The shared supervisor allows at most two capability workers in one server;
OCR and analytics each also limit their own concurrent operation. These ceilings
are limits rather than expected consumption or a machine-wide RAM guarantee.
Other programs, the local model and multiple separate Ariadne installations
consume additional resources.

Observed native fixtures on this PC: the final one-image OCR smoke completed in
2.687 seconds; a 10,000-row weighted analytics fixture took 1,106 ms roundtrip
(769ms in its worker). These are individual observations under varying load,
not statistically controlled benchmarks or performance promises. The combined
local-model/OCR/6,000-row join mission took 52.043 seconds over 12 model steps.

Retained installation assets measured during delivery:

| Folder | Count | Bytes |
| --- | --- | --- |
| runtime/downloads/mcp | 47 wheels | 20,762,803 |
| runtime/ocr/wheels | 22 wheels | 113,464,910 |
| runtime/ocr/models | 3 assets | 31,749,509 |

These are partial storage footprints; installed libraries, DuckDB's retained
wheel, source, native runtimes, model weights, caches and state are additional.
Wheels deliberately duplicate installed files to permit offline repair. Source
exports exclude those assets and private data. Preserve them with their notices
when assembling an offline appliance. The qualified optional locks currently
target Windows x64 Python 3.13; other platforms need separate lock generation
and native-runtime qualification.
