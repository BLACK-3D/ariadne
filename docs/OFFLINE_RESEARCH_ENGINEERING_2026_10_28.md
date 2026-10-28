# Offline engineering and deterministic tools — 2026-10-28

Research only for the existing Windows Ariadne box. No installation, native
execution, new device access or production configuration change was performed.
Official releases, documentation and repository licenses were checked. These
tools expand what an AI can produce without asking a language model to simulate
a solver, geometry kernel, renderer or codec. None is a general AI agent.

## Useful additions

| Candidate / observed version | Offline ability | Resource and integration fit | License / evidence |
| --- | --- | --- | --- |
| **Google OR-Tools 9.15**, Jan 12, 2026 | Staff/job scheduling, assignment, packing, resource allocation and route optimization | CPU, native Windows Python packages. Strong early business addition through bounded JSON problem templates. Local routing needs supplied distances or a local map engine; live map APIs are separate. | Apache-2.0 project; external solver terms differ. [Release](https://github.com/google/or-tools/releases/tag/v9.15), [Windows installation](https://developers.google.com/optimization/install/python), [source/license](https://github.com/google/or-tools). |
| **SymPy 1.14.0**, Apr 27, 2025 | Symbolic equations, calculus, matrices, exact algebra and scientific derivations | CPU, small Python-oriented tool rather than another model. Use trusted constructors and a small validated expression grammar in a resource-limited worker. | New BSD code. Existing stable version is still a useful candidate despite being older. [Release](https://github.com/sympy/sympy/releases/tag/1.14.0), [source](https://github.com/sympy/sympy), [parsing warning](https://docs.sympy.org/latest/modules/parsing.html). |
| **FreeCAD 1.1.4**, Sep 28, 2026 | Parametric parts, enclosures, mechanical models and technical drawings | Native Windows builds and a broad Python API; CPU geometry, optional visualization. Initial adapter should offer fixed parametric templates, exported STEP/STL and independent dimensional checks. | Repository LICENSE is LGPL-2.1; dependencies/addons carry their own terms. This release fixes two malicious-file handling issues. [Release](https://github.com/FreeCAD/FreeCAD/releases/tag/1.1.4), [API/project](https://github.com/FreeCAD/FreeCAD), [license](https://github.com/FreeCAD/FreeCAD/blob/main/LICENSE). |
| **Blender 5.2.3 LTS**, Oct 22, 2026; supported older **4.5.15 LTS**, same date | Product renders, animation, simulations, modeling and video composition | Windows portable distribution; CPU or GPU. Use on-demand bounded render jobs and serialize GPU work with AI inference. Scene complexity, samples and output size determine cost. | GPL-3.0 project; imported assets/addons need separate provenance. [Maintained LTS versions](https://www.blender.org/download/lts/), [4.5 fixes](https://www.blender.org/releases/4-5/), [project/license](https://github.com/blender/blender). |
| **KiCad 10.0.7**, Oct 7, 2026 | Electronic schematics, PCB layouts, bills of materials, design-rule checks and manufacturing exports | Native Windows. Documented `kicad-cli` can check/export local project snapshots without a cloud account. Cache symbol/footprint/3D libraries; disable HTTP library fetching for an offline profile. | Majority GPL-3.0-or-later code; official libraries CC-BY-SA-4.0 with a design-output exception. [Release](https://www.kicad.org/blog/2026/10/KiCad-10.0.7-Release/), [CLI](https://docs.kicad.org/10.0/en/cli/cli.html), [licenses](https://www.kicad.org/about/licenses/), [library terms](https://www.kicad.org/libraries/license/). |
| **QGIS 4.2.3**, Sep 25, 2026; **3.44.15 LTR** also offered | Offline maps, spatial analysis, site planning and local geodata processing | Native Windows standalone installers. Preload local vector/raster data and any required projection grids. Online tiles, geocoding and WMS services require connectivity; plugins can introduce separate dependencies. | GPL-2.0 program license; map/data licenses are separate. Official guidance favors LTR when stability is the priority. [Downloads and versions](https://qgis.org/download/), [license](https://www.qgis.org/license/). |
| **FFmpeg 9.0.2**, Sep 18, 2026 | Inspect, transcode, trim, resample, subtitle and assemble local audio/video | Useful shared media adapter for speech and creative workflows. Fixed local input/output paths and codec choices; no arbitrary URLs or model-authored command lines. Native parsers need time/memory/output bounds. | LGPL-2.1-or-later core, GPL depending on enabled components; exact build and codec terms matter. Upstream lists Windows build providers rather than supplying every binary. [Current release](https://ffmpeg.org/download.html), [tools](https://github.com/FFmpeg/FFmpeg), [build-license details](https://ffmpeg.org/legal.html). |

## Findings that affect implementation

OR-Tools should return the actual solver status, objective/bounds and the input
constraints. A feasible solution found before a deadline must not be labeled
proven optimal. CP-SAT uses integers, so fixed scaling and explicit units are
part of the input contract. This can give a business plan a checked schedule
instead of a plausible-looking invented one. [Solver status and integer model](https://developers.google.com/optimization/cp/cp_solver).

SymPy's general string parser uses Python evaluation; `evaluate=False` is not
an input-security boundary. Never forward raw model text to that parser as if
it were the existing restricted arithmetic tool. The library remains useful
behind explicitly allowed mathematical constructors. [Maintainer warning](https://docs.sympy.org/latest/modules/parsing.html).

FreeCAD's GitHub stable artifact is 1.1.4. Current release listings separately
show 26.3rc2 and weekly builds as **prereleases**. A website/release heading is
insufficient evidence that an RC is the production stable build. Preserve the
stable parser security fixes, and review later major versions before adoption.
[Official release collection](https://github.com/FreeCAD/FreeCAD/releases).

Blender can run scripts and loaded project content can be active. Its 4.5.15
notes include auto-run-related fixes; keeping a script preference disabled is
not a full OS sandbox. A template-based renderer is a smaller initial authority
surface than arbitrary Python scene execution. The same principle applies to
FreeCAD macros, KiCad plugins and QGIS Python plugins.

These are artifact-generation/analysis candidates. Success means checked output
files, reported solver/DRC limitations and reproducible inputs. It does not grant
authority for fabrication, purchasing components, controlling machinery or
changing the user's other applications. There are no measured latency, wattage,
energy savings or output-quality comparisons from this research.
