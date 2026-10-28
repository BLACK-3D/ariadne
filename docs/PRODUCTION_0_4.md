# Offline production and engineering tools

These tools extend Ariadne with finite operations, rather than a general shell or arbitrary Python executor. All engines load on demand. There is no background office server, database, CAD process or render daemon. Tool calls do not download packages, executables, maps or codecs.

## Qualified on this installation

| Tool | Actual implementation and qualification |
| --- | --- |
| `production_optimize` | OR-Tools 9.15.6755: worker/task assignment, machine scheduling with precedence, bin packing, and vehicle routing over a caller-supplied integer distance matrix. Assignment is compared with an independent permutation oracle. Scheduling, packing and routing verify constraints and objective totals. CP-SAT distinguishes optimal, feasible, infeasible and unknown. Routing is explicitly reported as feasible, not proven optimal. |
| `production_math` | SymPy 1.14.0: simplify, expand, factor, differentiate, integrate, solve over the real domain and exact decimal substitution. A small validated AST constructs trusted SymPy objects. No `eval`, `parse_expr`, text `sympify`, attributes, subscripts, comprehensions, imports, lambdify or user functions. Actual derivative, root and rational substitution tests pass. |
| `office_create` | Built-in DOCX reports, XLSX tables and PPTX decks. XML and ZIP relationships are parsed in tests. Spreadsheet strings beginning with `=` remain literal strings. Typed numeric cells and fixed local SUM/SUMPRODUCT formulas are supported, with 15 significant digit bounds; formula ranges may reference only explicit numeric input cells. There are no macros, external relationships, arbitrary formulas or imported document payloads. Outputs are newly created workspace files. |
| PDF report output | LibreOffice 26.8.1, extracted from its official SHA-256-verified MSI with lessmsi 2.12.9. No Windows MSI installation, service or registry registration. Only internally generated DOCX files are converted with a fresh explicit profile. A real generated PDF passes its file signature test. |
| `geo_analyze` | Built-in WGS84 GeoJSON feature counts, geometry types, bounds and a bounding-box filter. A feature is selected only when its entire bounding box lies inside the requested box. Flat scalar properties and two-dimensional bounded coordinates only. No remote providers, URI inputs or project files. Snapshot provenance is returned. |
| `cad_part` | Official FreeCAD 1.1.4 portable package. Fixed box, cylinder and four-hole plate templates with dimensions in millimeters; STEP or STL output. A real four-hole plate STEP export passes. Model arguments are JSON data passed to a fixed internal script. No imported FCStd files or user macros. |
| `media_transform` | Pinned FFmpeg 9.0.2 branch Windows build from the BtbN provider linked by upstream FFmpeg. Exact dated release and GitHub-published archive SHA-256 are recorded. Real 16 kHz mono WAV, PNG thumbnail and H.264 MP4 conversions pass. Explicit demuxers, fixed codecs/filters, disabled MOV external references, restricted protocol list and no playlists or user flags. |
| `render_product` | Official `bpy` 5.2.3 wheel and hash-locked dependencies, installed separately from both the core and optimization environments. A fixed CPU Cycles primitive scene renders to PNG, with 16 samples and a 128–1024 pixel bound. An actual 128×128 render passes file, size and visible-pixel-variation checks. No scene import or user Python. |
| `gis_buffer` | Actual QGIS 3.44.15-2 native buffer over sanitized WGS84 GeoJSON. Explicit degree units; Python and plugins disabled, local GDAL/PROJ assets selected, network projection downloads disabled. A point buffer's numeric bounds are checked against the requested distance. |
| `pcb_manufacture` | Actual KiCad 10.0.7 generated rectangular outline, DRC JSON and five copper/mask/outline Gerbers packaged in ZIP. Native DRC, export and Gerber file content checks pass. Configuration, documents and caches stay in the job staging folder. This is a blank starting board template, not a complete circuit or manufacturing certification. |

The optimization/math wheel profile lives in `runtime/production/python`, isolated from the core `.venv`. This is intentional: OR-Tools requires protobuf 6.x, while the existing core environment uses 7.x. Twelve exact wheels were checked against official PyPI SHA-256 digests. Source-distributed `config/production-assets.json` records the profile, source URLs and binary archive hashes.

## Artifact qualification

Generated OOXML is checked beyond ZIP validity. Actual LibreOffice opens both generated XLSX and PPTX and renders them to PDF; extracted text verifies visible spreadsheet and slide content. Workbooks containing fixed formulas are opened and recalculated through Calc when the local LibreOffice engine is available. Tests assert both the stored formula and its native recalculated numeric cache. Without LibreOffice, deterministic cached values are included and the result explicitly reports `recalculated: false`.

The standalone Blender download endpoint returned HTTP 403 during initial provisioning. The official Windows CPython 3.13 `bpy` wheel supplies the same fixed scene capability without requiring that binary endpoint. The native Blender executable adapter also remains available for an explicitly reviewed portable installation; the qualified path on this PC uses the isolated `bpy` engine.

An executable hash verifies the selected file; it is not a claim that an application is safe for arbitrary imported files. The adapters deliberately avoid general scene/project/document import. Runtime assets remain outside the model-writable workspace.

## Provisioning and offline use

Run with the project's Python on Windows x64 Python 3.13:

```powershell
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-wheels --install-cached
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-native ffmpeg
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-native freecad
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-native libreoffice
.\.venv\Scripts\python.exe scripts/provision_production.py --fetch-blender-python --install-cached-blender
.\.venv\Scripts\python.exe scripts/provision_engineering.py --pack qgis
.\.venv\Scripts\python.exe scripts/provision_engineering.py --pack kicad
```

Only explicit provisioning commands use the network. All URLs, versions and SHA-256 expectations come from source-distributed asset locks. Existing assets with a mismatched digest fail closed. FreeCAD extraction uses Windows' bundled bsdtar; LibreOffice and QGIS use a hash-verified lessmsi archive; KiCad's installer is unpacked with a verified portable 7-Zip executable. No Windows application installation, system registration or service is performed. Package notices remain in wheelhouses and extracted native distributions.

After copying cached wheelhouses to another matching PC, `--install-cached` and `--install-cached-blender` install without an index or dependency resolution. The engineering provisioner accepts `--offline`. Preserve full native directories and wheelhouses for outage operation; the source ZIP alone does not contain these large runtime assets. Source locks are `config/production-assets.json`, `config/production-blender-assets.json` and `config/engineering-assets.json`.

For an additional reviewed native package already placed in `runtime/production`, explicit owner registration is available:

```powershell
.\.venv\Scripts\python.exe scripts/provision_production.py --register blender C:\path\to\Yikes\runtime\production\blender\blender.exe --version VERSION --sha256 LOWERCASE_SHA256
```

The path must stay inside this installation's production runtime. No PATH search or E: access occurs. Registering a binary does not replace the need to run and inspect its corresponding native qualification fixture.

## Authority, resource and output bounds

Artifact-producing tools require the agent engine's existing approval flow; mathematics, optimization and read-only GeoJSON analysis use automatic policy. Approval fingerprint material includes the exact input snapshot digest, selected engine/version, manifest, adapter and worker hashes, arguments and destination existence. The last checked immutable input bytes and engine selection are held for the actual approved call, closing the edit-between-check-and-use gap. Source hashes are returned with results.

Workers accept at most 20 MB of source data, return bounded JSON and generate at most a 20 MB artifact. GeoJSON has a tighter 2 MB / 5,000 feature / 50,000 coordinate bound. Only one production job runs at a time. Defaults are 2 GiB worker memory, 45 CPU seconds and a 90 second wall deadline. Native jobs may launch at most four processes inside the shared worker resource controls. Windows applies the limits to the job; POSIX memory/CPU address-space limits are per process, with process-group termination. These controls are resource bounds, not an OS filesystem or network sandbox.

The parent holds the staging directory, reads only a regular, single-link generated artifact and atomically commits it to a new workspace file. Outputs never overwrite existing files. The model cannot choose executable paths, staging directories, native flags, scripts, plugin names, output codecs or arbitrary solver expressions.

No energy, throughput or API-cost savings were measured. The architectural operating-cost benefit is avoiding permanent native processes and using deterministic CPU solvers for tasks that do not require model inference; actual whole-PC measurements remain future work.
