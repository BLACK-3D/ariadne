# Third-party components



The MIT license in this repository applies to the original application code. It does not replace the licenses of separately provisioned runtimes, model weights, dependencies or voice assets. Preserve their notices and model cards when redistributing an appliance.



- Python: https://docs.python.org/3/license.html

- Waitress: https://github.com/Pylons/waitress — package notices remain in the installed wheel/environment.

- Optional pypdf: https://pypi.org/project/pypdf/6.21.3/ — BSD-3-Clause; license remains in the installed wheel/environment. See requirements-documents.txt for the pinned wheel hash.

- Ollama: https://github.com/ollama/ollama — official archive and its included dependencies carry their own notices.

- Qwen3 model cards and license: https://huggingface.co/Qwen/Qwen3-8B and https://huggingface.co/Qwen/Qwen3-4B-GGUF — the selected publisher weights are Apache-2.0; the Ollama model manifests also retain their license blobs.

- whisper.cpp: https://github.com/ggml-org/whisper.cpp — MIT; models are provisioned from the repository linked by its upstream instructions.

- Whisper: https://github.com/openai/whisper — review the model/code licensing and model card along with the distributed weights.

The 0.10 appliance retains exact pinned Ollama and whisper.cpp notices, the linked llama.cpp and SDL2 notices, and the Whisper model license/card under `config/runtime-notices/` and the corresponding `runtime/ollama/` or `runtime/whisper/` directories. `config/runtime-notices/catalog.json` records immutable upstream URLs, SHA-256 values and revision context. These notice snapshots do not establish a new upstream binary signature or certify every dependency's redistribution obligations.

- Windows SAPI/System.Speech: supplied by Windows, not redistributed by this project.

- Optional Piper: https://github.com/OHF-Voice/piper1-gpl — GPL-3.0 runtime and separately licensed voices. It is not bundled in this installation.



Exact selected runtime versions, hashes and upstream sources are recorded in docs/RESEARCH.md and data/provision-manifest.json. The provisioning manifest is local installation data and is excluded from source distributions.



Optional 0.3 components are pinned by the three extra requirement locks. The

retained original wheels contain their complete package/native dependency

notices. Do not omit those wheels/notices when distributing a runtime bundle.



- FastMCP4.1.4 client: https://github.com/PrefectHQ/fastmcp — Apache-2.0. Ariadne uses `fastmcp-slim[client]`; its locked dependencies have their own notices.

- DuckDB2.0.0: https://github.com/duckdb/duckdb — MIT; the qualified wheel is retained in `runtime/wheels`.

- RapidOCR3.10.0: https://github.com/RapidAI/RapidOCR — Apache-2.0. The selected PP-OCRv6 small detection/recognition and mobile classification model hashes and license provenance are documented in docs/OCR_0_3.md.

- ONNX Runtime: https://github.com/microsoft/onnxruntime — MIT, with separate bundled third-party notices.

- Pillow: https://github.com/python-pillow/Pillow — HPND with bundled native-library notices.

- pypdfium2: https://github.com/pypdfium2-team/pypdfium2 — Apache-2.0/BSD-3-Clause components and PDFium's bundled third-party notices.



The OCR provisioner preserves package/model/native notices under

`runtime/ocr/notices`, and copies only hash-verified model assets from the

verified upstream wheel. This application does not relicense those assets.



## Components added in 0.4

The full offline package preserves the installed package licenses, original locked wheels, native notices and model-card/license snapshots. Exact versions, immutable asset URLs and SHA-256 values are recorded in the requirement locks and `config/*assets.json`; the export manifest hashes the distributed bytes. Runtime extraction does not change upstream licensing. Redistributing the appliance requires honoring every included component's terms, including copyleft and separate model/native dependencies.

- Qwen3.5:9b: Qwen publisher model, Apache-2.0; Ollama license blobs are retained with the cached model manifests.

The factory export includes only the three public Qwen tags and exact manifest, metadata and referenced blob pins in `config/public-models.json`. Unknown/custom owner models and unreferenced blobs are excluded. During export, each selected blob must match its pinned SHA-256 before the archive is published.
- FLUX.2-klein-4B: https://huggingface.co/black-forest-labs/FLUX.2-klein-4B — publisher card, Apache-2.0 license and immutable revision are retained with the model assets.
- Kokoro ONNX, Sherpa-ONNX, Silero VAD and the selected speaker segmentation/embedding assets: publisher model cards, licenses and exact hashes are retained under `runtime/media/notices` and the media asset locks. eSpeak's GPL notices remain separate from model licensing.
- PyTorch, Diffusers, Transformers, ONNX Runtime and their dependencies retain their own wheel/native-library notices. NVIDIA CUDA dependencies carry separate redistribution terms.
- FastEmbed, BGE-small-en-v1.5, sqlite-vec, Docling and its Heron/TableFormer models: original notices and cards are retained in the knowledge runtime. See `docs/KNOWLEDGE_0_4.md` for selected immutable assets and limits.
- Monty: https://github.com/pydantic/monty — MIT; the selected verified native runtime is retained with its notices.
- Kiwix tools and openZIM: https://github.com/kiwix/kiwix-tools and https://github.com/openzim/libzim — preserve their included GPL/native notices. The two bundled DevDocs reference ZIMs have separate content provenance and licenses; see the library profile and `docs/KNOWLEDGE_0_4.md`.
- OR-Tools and SymPy: https://github.com/google/or-tools and https://github.com/sympy/sympy — Apache-2.0 and BSD-3-Clause respectively, with dependency notices in their retained wheels.
- python-docx, openpyxl, python-pptx and ReportLab: their original wheel notices are retained. These libraries do not replace the licensing of document content supplied by users.
- FreeCAD, Blender, KiCad and QGIS: https://www.freecad.org/license.php, https://www.blender.org/about/license/, https://www.kicad.org/about/licenses/ and https://qgis.org/license/ — separately licensed native engines, including copyleft components and dependencies. Full native distribution notices remain in the package. Blender is supplied through its official `bpy` wheel.
- LibreOffice and FFmpeg: https://www.libreoffice.org/about-us/licenses/ and https://ffmpeg.org/legal.html — preserve the included distribution and bundled-library notices; FFmpeg obligations depend on the supplied build configuration.
- Node.js, Node-RED and Playwright/Chromium: their upstream licenses and dependency notices are retained with the runtime. Node-RED is used only through the two fixed local factory routes; Playwright does not grant authority to unrelated applications.
- ERPNext/Frappe, MariaDB and Redis are referenced by the optional pinned Compose recipe. Their container images are not distributed in this appliance; installing them creates additional licensing and platform obligations.
- `config/erpnext/compose.yaml` adapts `frappe/frappe_docker`'s `pwd.yml`. Copyright (c) 2017 Frappe Technologies Pvt. Ltd.; the upstream MIT license is preserved in [`config/erpnext/LICENSE`](config/erpnext/LICENSE). Upstream: https://github.com/frappe/frappe_docker.

The portable Python runtime was copied from the trusted local CPython 3.13.15 installation, then populated from hash-pinned offline wheels. Its manifest records those local bytes; it is not a claim of a newly verified upstream interpreter signature.
# Survival reference assets (0.5)

The bundled FEMA, CDC and FoodSafety.gov documents remain attributed to their original agencies and retain their notices. CDC materials are also available free on the CDC website. Ariadne is not endorsed by CDC, ATSDR, HHS, FEMA, DHS, USDA, FoodSafety.gov, GPO or the U.S. Government. The repository's MIT License does not relicense these agency documents or any separately protected elements. Exact original URLs, dates, hashes, reuse conditions and third-party exclusions are recorded in `docs/SURVIVAL-SOURCES.md` and `ariadne/resources/survival/catalog.json`. Original Ariadne planning cards use the repository's MIT License. No commercial book text is included; private owner imports are excluded from factory exports.

# Ariadne 0.6 timezone data

The 0.15 market-research adapter also includes `ariadne/resources/market/America_New_York.tzif` from the official Python tzdata 2026.5 distribution (IANA 2026e). Exact member/distribution hashes, source URLs and bundled original licenses are recorded in that directory's `provenance.json`, `TZDATA-LICENSE.txt` and `TZDATA-APACHE-LICENSE.txt`. These data and notices retain their upstream terms. No tzdata Python package is installed by the adapter.

`ariadne/resources/briefing/America_Chicago.tzif` is the IANA America/Chicago record from the cached Python tzdata 2026.6 distribution. Bundled attribution and Apache-2.0 license are in `ariadne/resources/briefing/`. This small local asset supplies DST-aware daily timing on Windows without a runtime dependency download.

# Fast offline speech (0.11)

The optional Piper 1.8.0 engine is GPL-3.0-or-later; its source revision, package hashes and full COPYING notice are retained in `config/speech-assets.json` and `runtime/media/piper/notices/`. Upstream source: https://github.com/OHF-Voice/piper1-gpl/tree/639388b6317fc4731e91d53da42aea68fd4166ff. Piper's and its dependencies' wheel notices remain in the isolated `runtime/media/piper-python` installation. Pathvalidate 3.3.1 is MIT licensed. These components retain their own licenses.

Cori medium is a preset British English voice. Its pinned upstream model card states it was trained from scratch on approximately 24 hours of public-domain LibriVox recordings. The card and model configuration are retained with the local model; no voice cloning, owner recording, or runtime download is involved. Model source: https://huggingface.co/rhasspy/piper-voices/tree/3001aba6a7e29fca16fd6174d4fac367951b7929/en/en_GB/cori/medium.

# Optional local vision (0.39)

- Official Megvii YOLOX-Nano ONNX release 0.1.1rc0 is Apache-2.0 licensed. Its original license and ONNX model card are retained in `runtime/vision/notices`. Exact model/source/wheel pins are in `config/vision-assets.json`; upstream source: https://github.com/Megvii-BaseDetection/YOLOX/tree/6ddff4824372906469a7fae2dc3206c7aa4bbaee. No Ultralytics, AGPL or noncommercial detector weights are distributed by this pack.
- OpenCV-Python headless 5.0.0.93 and NumPy 2.5.4 retain their wheel licenses and complete bundled-component notices in the isolated runtime and wheel cache. OpenCV's unused FFmpeg plugin is separately LGPL licensed. Its exact OpenCV upstream binary pin is matched, and corresponding FFmpeg 7.1, AOM, VPX, wrapper sources and build recipes from official `opencv_3rdparty` revision `664c0098dcb47b361f20c1d6a518653c23f5f2b5` are included under `runtime/vision/sources/opencv-ffmpeg-664c0098dcb47b361f20c1d6a518653c23f5f2b5`. Component terms remain in those archives and notices.
- The standalone minimal FFmpeg 9.0.2 adapter uses LGPL-2.1-or-later, with GPL components, external codec libraries and networking disabled. This software uses code of FFmpeg licensed under LGPLv2.1-or-later; its complete corresponding unmodified source is included in the same pack as `runtime/vision/sources/ffmpeg-9.0.2.tar.xz`. The configure/build recipe, exact compiler/source input pins and original FFmpeg, LLVM runtime and MinGW-w64 runtime notices are preserved. Ariadne does not prohibit reverse engineering or rebuilding these LGPL components. The compiler, shell, make and private build caches are not distributed in the vision pack. Sources: https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz and https://github.com/mstorsjo/llvm-mingw/releases/tag/20261104.

The repository's MIT license does not relicense these assets. Public qualification images, videos, hardware inventories and owner evidence are excluded from the vision add-on. See `docs/VISION_LOCAL.md` for the closed export inventory and capability limits.
