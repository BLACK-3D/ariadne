# Optional local OCR, 0.3

This implementation adds CPU-only text recognition for owner-supplied PNG/JPEG images and rasterized PDF pages. It does not enable a microphone, camera, screen capture, external URL fetch, autonomous directory selection, or document execution. The root application integration controls when OCR is requested and how previews/imports are reviewed.

## Implemented interface

`ariadne.ocr.ocr_status(settings_or_configpath)` returns readiness, supported formats, limits, and processor identity without importing imaging or inference packages. `extract_ocr(name, payload, configpath, *, cancelled=None)` accepts a leaf filename and bytes and returns `text`, `format`, `warnings`, source `sha256`, source `bytes`, and `processor`. Input URLs and filenames containing paths are rejected. `cancelled` is an optional callback passed to the common worker supervisor.

Heavy libraries are imported only by fixed `ariadne/_ocr_worker.py`, through the shared `worker.py`/`_worker_bootstrap.py` resource-limited path. The server must run from the project's provisioned `.venv`; status reports unavailable otherwise. Missing packages, altered package versions, changed model hashes, changed lock files, or changed worker/adapter code make the configuration unavailable until explicitly provisioned again.

Processor identity includes the full package-version receipt, dependency-lock digest, all three model digests, adapter/worker source digests, shared supervisor/bootstrap source digests, the shared path-guard and path-policy source digests (`tools.py` and `config.py`), and extraction limits. Root integration must bind a reviewed preview/import to this identity as well as the input-byte hash. Different processors may extract different text from the same source bytes.

## Fixed operating limits

| Limit | Setting |
| --- | --- |
| Source bytes | 1 byte through 10,000,000 bytes |
| Raster size | At most 12,000,000 pixels and 6,000 pixels per side |
| PDF pages | 1–10; longer PDFs fail rather than silently omit pages |
| PDF rendering | 2 pixels per PDF point (144 DPI), one page at a time |
| Output | 250,000 characters, 1,100,000 wire bytes |
| Process | 2 GiB memory, 90 seconds CPU and wall-time budget |
| CPU inference | ONNX Runtime CPU provider, two intra-op threads, one inter-op thread |
| Concurrency | One OCR request per server process; shared capability pool also applies |

The supervisor applies operating-system limits before loading the worker module and passes a fresh restricted environment. No package/model/font provisioning occurs at runtime. Every ONNX graph has an explicit verified local path; image arrays, never URL strings, reach the OCR engine. ONNX telemetry is explicitly disabled. Python audit hooks refuse socket/network APIs and child-process execution within the worker. Those hooks and memory limits are defense in depth, not an OS filesystem/network sandbox or a guarantee against native-library exploitation.

Image format must match its extension. Animated/multiframe images, encrypted PDFs (including an empty user password), malformed documents, excessive page/pixel geometry, no recognized text, and output overflow are failures. Other extraction results always include a note that OCR can misread text or ordering. Recognition is an observation, not proof that a financial value is correct. This profile does not reconstruct spreadsheet formulas, guarantee table structure, or interpret general visual scenes.

## Explicit provisioning and offline artifacts

Qualified platform: **Windows x64, CPython 3.13**. The exact binary-only `requirements-ocr.txt` covers 22 wheels. Each of the downloaded wheel hashes was checked against its exact public PyPI release JSON before the lock was written; no known vulnerability record was returned by those metadata responses at that time. That point-in-time metadata check is not a dependency security audit.

The wheelhouse is `runtime/ocr/wheels`, with source URLs, sizes, versions, and digests in the shipped `config/ocr-wheels-manifest.json`. Downloaded wheel bytes total **113,464,910 bytes**. Installed files, extracted models, and retained notices use additional disk space. No package installation occurred in global Python. The root delivery agent serialized the project `.venv` installation with the other optional capabilities and reported `pip check` passed.

For a fresh Windows x64 CPython 3.13 source installation, first follow the base application's `.venv` bootstrap. While online, populate the OCR wheelhouse using the shipped exact lock; binary-only resolution refuses source builds:

```powershell
.\.venv\Scripts\python.exe -m pip download --index-url https://pypi.org/simple --only-binary=:all: --require-hashes --dest runtime\ocr\wheels -r requirements-ocr.txt
```

For a disconnected installation, copy the previously verified `runtime/ocr/wheels` directory alongside the source bundle before installation. The source export includes the lock and `config/ocr-wheels-manifest.json`; it intentionally does not contain the runtime wheel bytes. Preserve those separately when preparing an offline box. Once the wheelhouse is present, install without any index access, then explicitly provision:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --find-links runtime\ocr\wheels --require-hashes -r requirements-ocr.txt
.\.venv\Scripts\python.exe -B scripts\provision_ocr.py
```

`scripts/provision_ocr.py` performs no network access and does not invoke pip. It verifies every cached wheel against the lock, extracts the three bundled models, checks their separate official registry hashes, preserves package/model/native license notices, checks every installed distribution version against the full lock, and writes `data/ocr-config.json` atomically. `--prepare-only` verifies and extracts artifacts without enabling OCR; `--config` selects another configuration file inside this installation. Existing links/reparse points, hard-linked files, outside-installation paths, and E: paths are refused by the local file validators. All persisted artifacts stay under Yikes.

The profile uses [RapidOCR 3.10.0](https://github.com/RapidAI/RapidOCR/releases/tag/v3.10.0), [ONNX Runtime 1.31.1](https://github.com/microsoft/onnxruntime/releases/tag/v1.31.1), [Pillow 13.0.0](https://pypi.org/project/pillow/13.0.0/), and [pypdfium2 5.14.0](https://pypi.org/project/pypdfium2/5.14.0/). pypdfium2 provides PDFium rendering; this implementation does not use MuPDF/PyMuPDF.

## Model provenance and notices

The three default models are bundled in the verified RapidOCR wheel, so no separate model-host download is needed. Their pinned hashes match the official [v3.10.0 registry](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/python/rapidocr/default_models.yaml). The project's [model license notice](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/python/MODEL_LICENSES.md) specifically covers these PaddleOCR-derived weights under Apache-2.0, with upstream ownership retained; this is distinct from the [RapidOCR source license](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/LICENSE).

| Model | Bytes | SHA-256 |
| --- | ---: | --- |
| `PP-OCRv6_det_small.onnx` | 9,929,594 | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` |
| `PP-OCRv6_rec_small.onnx` | 21,234,383 | `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` |
| `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | 585,532 | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` |

Provisioning preserved **61 notice/license files** in `runtime/ocr/notices`, including the model notice and PDFium native-component notices. Do not infer that every transitive dependency uses the top-level project's license. Redistribution must retain applicable package, model, and native notices; the preserved directory supplies the original texts. Fonts/sample documents from upstream demos are not downloaded or bundled by this implementation. Test images use Pillow's built-in font and locally generated text.

## Actual verification

The builder ran the final shared-worker `tests/test_ocr.py` module: **11 tests passed in 18.686 seconds**. An additional pixel-bomb target then passed both its dimension test and new actual worker pixel-bomb/PDF-geometry test, giving **12 distinct tested cases** across those runs. Tests include:

- Actual CPU recognition of a generated PNG, JPEG, and raster PDF; each had to contain `ARIADNE`, `314159`, and `16.00`.
- Malformed PNG/PDF, a blank image, password-encrypted and empty-password-encrypted PDFs, an 11-page PDF, a 16-million-pixel PNG header, and an oversized PDF page all rejected.
- Prestart and in-flight cancellation, response/type/text-limit validation, forbidden URL/path/drive inputs, and Python network/child-process guard denial.
- Altered/missing transitive distribution metadata and a changed model digest make status unavailable.

A clean parent-interpreter probe reported OCR ready with **no `rapidocr`, `onnxruntime`, `PIL`, `cv2`, `numpy`, or `pypdfium2` module loaded**. The first pre-supervisor-switch smoke test and source PNG remain under `data/ocr-qualification/initial`; its successful 3.392-second observation is historical evidence for that earlier processor, not the final worker's latency qualification. Final test evidence is summarized under `data/ocr-qualification/final`. No failures were discarded or converted into passes.

After the shared supervisor was hardened to hold temporary/cache directory ancestors for the entire child lifetime, OCR was explicitly re-provisioned. The same real PNG passed all three expected tokens in 3.128 seconds; raw recognized text, input digest, timing, and that intermediate processor identity are retained in `data/ocr-qualification/post-temp-fix/result.json`. The source-export repair also moved the required public manifest into `config/ocr-wheels-manifest.json`; the explicit provisioner successfully ran against that shipped location.

The final Windows directory-handle fix uses `FILE_LIST_DIRECTORY` access so omission of delete sharing actually prevents directory replacement. Processor identity was extended to bind this shared path-guard implementation and `config.py`, then explicitly provisioned again. The actual PNG passed `ARIADNE`, `314159`, and `16.00` in **2.687 seconds**, with no heavy imaging/inference packages imported in the parent before or after extraction. The final identity is `c4d8eaaeba2acfc07ca8c9c4f118b7464d75c6c4eb0847740b62978967375ffe`. The complete result and matching configuration receipt are retained in `data/ocr-qualification/final-path-guard-identity`. Earlier records retain their original identities; these sequential smoke timings are observations, not a controlled performance comparison.

These tests establish this fixture and the exercised limits on this PC. They do not establish a general OCR accuracy rate, production throughput, energy savings, concurrent OCR/LLM peak memory, all language coverage, or correctness of arbitrary financial calculations. CPU execution and process exit avoid retaining OCR models or occupying GPU VRAM while idle; actual energy and operating-cost gains have not been measured. Independent review and the root integration's API/import/browser checks remain separate delivery gates.
