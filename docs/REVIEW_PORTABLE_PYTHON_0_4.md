# Portable Python deployment review and relocation proof

Reviewed 2026-10-28 by an agent separate from the portable-runtime builder. Scope: `scripts/provision_python.py`, the generated `runtime/python` tree and its manifest, and a runtime-only relocation test. Application activation and a whole-appliance relocation are separate integration steps.

## Verified outcome

The generated **Windows x64 CPython 3.13.15 runtime works after relocation to a different folder containing spaces**. Its standard library and tested core packages do not resolve through the original AppData Python installation or `.venv`.

`tests/test_portable_python.py` passed both its current-runtime identity test and explicit relocation test in **76.304 seconds**. The relocation copied **7,160 files / 419,534,358 bytes**, excluding bytecode caches, into a temporary `another PC with spaces/runtime/python` directory under Yikes. Copy time was 59.813 seconds and the relocated import probe took 10.674 seconds. These are single measured elapsed times on this PC, not cross-device performance guarantees. The temporary runtime copy was removed after success; the proof receipt remains at `staging/portable-python-relocation-proof.json`.

The fixed probe ran the relocated executable with `-I -B`. It cleared the original user's PATH/AppData context and set deliberately invalid `PYTHONHOME`/`PYTHONPATH` directories. It checked:

- `sys.prefix == sys.base_prefix ==` the relocated runtime directory.
- `sys.executable` and `sys._base_executable` both remain under that directory.
- 64-bit pointers, Python 3.13.15, and isolated / ignore-environment / no-user-site flags.
- Imported files for `os`, `json`, `sqlite3`, `_sqlite3`, `ssl`, `_ssl`, Waitress, pypdf, DuckDB, FastMCP, RapidOCR, ONNX Runtime, NumPy, Pillow and cryptography all resolve under the relocated runtime.
- `ariadne.server` and `ariadne.worker` import successfully from the project source, which was intentionally supplied separately. The test relocated the interpreter/core packages, not the entire application.
- Every non-application `sys.path` entry remains inside the relocated runtime. No `.venv` path is present.
- The current runtime's Python executable, Python DLL, `Lib/os.py`, `_ssl.pyd` and `_sqlite3.pyd` match the generated portable manifest hashes.

Run the quick identity check normally; opt into the larger copy only when relocation needs qualification:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_portable_python -v
$env:ARIADNE_TEST_RELOCATION = '1'
.venv\Scripts\python.exe -m unittest tests.test_portable_python -v
```

The test runner itself may use the development environment; the child being qualified always uses the fixed `runtime/python/python.exe` or its relocated copy. Normal release tests skip the full copy unless the explicit environment flag is set.

## Builder review

The builder copies the active installed Python executable, standard library and DLLs, omitting source `site-packages`, tests, development UI and bytecode caches. It reconstructs core packages from local hash-pinned wheels using `--no-index --require-hashes`, then records file hashes. Its copied interpreter origin is the already trusted local installation; the builder does not claim a fresh upstream Python signature verification.

No model-facing path, executable or shell interface calls this owner-only builder. `_allowed_path` rejects E: and linked/reparse path ancestry. The relocation helper independently rejects linked/reparse entries before descending. All test files and writes stayed in Yikes; E: was not accessed.

Review recommendations communicated to the builder owner:

1. Tighten the builder's own qualification from a substring match on printed `sys.prefix` to exact JSON identity/module-origin checks. A stale `pyvenv.cfg` can keep `sys.prefix` in the target while `sys.base_prefix` and the standard library use another installation; the initial builder check would not detect that. The independent test above checks both prefixes and module files and passed for the actual clean build.
2. Reject nonregular/hard-linked existing copy targets before overwriting and check source directories for reparse points before walking into them. The source installation is trusted in this workflow; these are protective checks for future reuse or interrupted/rebuilt targets.
3. Use `python.exe -m ...` or explicit fixed script invocations as relocation entrypoints. Windows console launchers generated in `Scripts/*.exe` can embed the original absolute interpreter path; they are not independently relocatable entrypoints.

## Activation dependencies and limits

Import success does not prove that every capability is enabled under a new interpreter. At review time `ariadne/ocr.py` and `scripts/provision_ocr.py` still explicitly required the `.venv` executable as well as `sys.executable`; switching the application to portable Python requires accepting the qualified portable interpreter and reprovisioning OCR configuration. The owner MCP provisioning CLI also selected `.venv` directly, although its already installed runtime client imports correctly under portable Python. Both were reported to the activation/bootstrap owners before delivery.

The test ran on this Windows host, not on a freshly installed Windows VM. Windows system libraries remain legitimate OS dependencies. Optional specialist environments, model files, browser/Node runtimes, native applications and their rebased configuration need the separate whole-appliance bootstrap/qualification. This proof makes no claim of Linux/macOS compatibility or of functioning without the required Windows CPU/runtime support.
