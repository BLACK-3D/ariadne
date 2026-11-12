# Local vision (0.39)

The optional Windows x64 vision pack detects COCO object classes, including people, in an explicitly selected local video or a bounded USB-camera session. It uses YOLOX-Nano with OpenCV DNN on the CPU. Detection does not call Ollama, a vision language model, or an online API. An idle installation does not run a detector. The pack does not identify people, recognize faces, explain actions, or supply safety-critical monitoring.

The weights are the official Megvii YOLOX-Nano ONNX release `0.1.1rc0`, 3,659,407 bytes, SHA-256 `c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d`. The upstream repository and model release are Apache-2.0 licensed. This is a separately licensed YOLOX model; the pack contains no Ultralytics model or AGPL/noncommercial detector weights. Its 80-class COCO training and small 416-pixel input limit its coverage and accuracy. Scores are model confidence values, not calibrated probabilities.

The inference environment is isolated in `runtime/vision/python`: `opencv-python-headless==5.0.0.93` and `numpy==2.5.4`. The core Python environment is unchanged. SHA-256 pins, exact official URLs, preprocessing, source revisions and package license information are in [the asset lock](../config/vision-assets.json) and [the wheel lock](../requirements-vision.txt). A source-controlled manifest seals every admitted runtime file; provisioning must reproduce that seal before declaring the pack prepared. Imports and inference happen in an on-demand worker. Runtime operation has no download path.

## Install a prepared offline pack

Use the 0.39.0 Windows x64 Ariadne starter/core and its matching `ariadne-offline-vision-0.39.0.zip`. Keep the add-on ZIP in that Ariadne folder and use its published checksum with the offline installer:

```powershell
.\runtime\python\python.exe -I -B scripts\install_vision_pack.py --archive ariadne-offline-vision-0.39.0.zip --sha256 PUBLISHED_64_CHARACTER_SHA256
```

The installer verifies the entire ZIP against the supplied checksum and the starter's shipped file seal before writing. It rejects missing/extra/private/linked/duplicate paths and differing existing assets, installs only runtime files, and publishes readiness last. The add-on contains the qualified runtime, detector weights, wheel cache, FFmpeg adapter, notices and corresponding FFmpeg source. It contains no camera selection, owner database, recordings, photos, credentials or inference evidence. Use the installer rather than extracting an unverified ZIP over a working installation.

With that copy's bundled Python, verify the installed bytes:

```powershell
.\runtime\python\python.exe -I -B scripts\provision_vision.py --verify
```

Verification reads local files and does not open a camera or run inference. Running the provisioner without arguments also performs an actual CPU model forward pass on a generated constant tensor. Neither command enables a camera or changes owner settings.

```powershell
.\runtime\python\python.exe -I -B scripts\provision_vision.py
```

Open the Vision controls in Ariadne. Select a local recorded video and inspect its source receipt before starting analysis. USB camera discovery is an explicit action; select the reported device and start a bounded session separately. The stable DirectShow device alternative ID binds the choice rather than a numerical camera index. Camera input is disabled until started. Stop controls and the session lease bound capture; device/driver qualification remains specific to the receiving PC. This release was qualified with a public recorded fixture without opening the owner's camera or microphone.

## Prepare from source with one-time Internet access

Double-click **Enable Vision.cmd** after core Setup completes, or run the commands below. These explicit commands install only inside this Ariadne folder. They do not run pip or change global packages, services, drivers or application settings. The first command builds the small decoder from unmodified official FFmpeg source using the pinned portable LLVM-MinGW compiler, make and shell. Its compiler cache is about 191 MB compressed and substantially larger after extraction; it is development/setup material, excluded from portable exports. The source archive is retained for redistribution.

```powershell
.\runtime\python\python.exe -I -B scripts\build_vision_decoder.py --download
.\runtime\python\python.exe -I -B scripts\provision_vision.py --download
```

Without `--download`, both commands use only the pinned local cache. Interrupted builds/downloads and mismatching bytes stop for inspection. They do not silently retry or accept changed hashes. A locally rebuilt executable must match the reviewed byte seal. A different compiler/build result needs independent qualification and an updated reviewed seal; editing a runtime manifest does not make it trusted.

The minimal FFmpeg 9.0.2 executable is LGPL-2.1-or-later and communicates with Ariadne through a subprocess. It is built without network support or external codec libraries. Only `fd` and `pipe` protocols are compiled in. Local video is supplied as an already-open, seekable regular-file snapshot on standard input using `fd:`, with the worker also restricting protocols and formats. It admits AVI, Matroska/WebM and MOV/MP4 containers and common CPU H.264, HEVC, MPEG-4, MJPEG, VP8, VP9 and raw video decoders. Unsupported codecs or malformed videos fail visibly. The DirectShow input captures only the selected video device; audio is not requested. The runtime does not accept URL/playlist input.

The unmodified source archive is `runtime/vision/sources/ffmpeg-9.0.2.tar.xz`. [The complete build recipe](../scripts/build_vision_decoder.sh), [builder](../scripts/build_vision_decoder.py), asset lock, FFmpeg license and MinGW/LLVM runtime notices accompany the binary. Preserve these when redistributing. This software uses code of FFmpeg licensed under LGPLv2.1-or-later; its corresponding source is included in the same vision pack. Toolchain binaries and private build logs are not part of the distributed pack.

The untouched OpenCV wheel also includes its separately licensed FFmpeg plugin, which this worker does not use for video decoding. Its DLL matches the exact OpenCV 5.0.0 upstream binary pin. The matching official `opencv_3rdparty` source revision `664c0098dcb47b361f20c1d6a518653c23f5f2b5`, including FFmpeg 7.1, AOM, VPX, wrappers, original build recipes and notices, is retained under `runtime/vision/sources/opencv-ffmpeg-664c0098dcb47b361f20c1d6a518653c23f5f2b5`. These approximately 19.7 MB of corresponding source accompany both the installed plugin and the offline wheel cache. They are hash-pinned and included in every vision export/install inventory.

## Export a clean portable pack

After local provisioning succeeds, inventory and export only the sealed public assets:

```powershell
.\runtime\python\python.exe -I -B scripts\export_vision.py --plan
.\runtime\python\python.exe -I -B scripts\export_vision.py --output dist\ariadne-offline-vision-0.39.0.zip
```

The standalone add-on avoids rebuilding the historical full optional-tools appliance. To include vision in a new prepared core export, use `scripts/export_core.py --with-vision --output dist\ariadne-offline-core-with-vision.zip`; it still requires the released starter's verified source/core assets. Full appliance export includes vision only when its complete seal verifies. Exporters admit a closed list of hashed assets, omit unknown files and test recordings, and fail on mismatches. They never export arbitrary contents of the vision directory.

## Qualification and operating cost

Qualification uses the public OpenCV 4.12.0 `samples/data/basketball1.png`; the repository publishes an Apache-2.0 license, while image-specific redistribution rights were not separately established. The selected image's SHA-256 is `ba06f6701f7260998b430c39b6557f775497e6ce7b1a74f0b7ea6af371bf54a6`. A four-second, eight-frame MJPEG AVI uses this still for its first four frames and black frames for its last four. It tests actual container decoding, person detection, a detected/empty transition and timestamp handling; it does not establish natural-motion or owner-environment performance. All fixture/evidence files are private qualification artifacts excluded from portable packs.

The complete actual analysis path processed that eight-frame clip in 3.686 seconds on this development PC, including seal verification, worker startup and native decoding. Its first four frames found two people (approximately 0.879 and 0.827 confidence); the last four found no people and the frame-change measure detected the transition. A separate single-image reference forward pass took about 44 ms, excluding decoding, startup and integrity checks. These are single-PC measurements, not general speed or power guarantees. The detector is CPU-only and has zero model/API inference charges. CPU time, memory, storage and electricity still have costs. Sampling and bounded sessions control recurring work; no monetary or energy saving is claimed without a comparison measurement. Camera performance, occlusion, lighting, small/distant objects and false detections need evaluation on the intended hardware and setting.

Primary sources: [official YOLOX ONNX model card](https://github.com/Megvii-BaseDetection/YOLOX/blob/6ddff4824372906469a7fae2dc3206c7aa4bbaee/demo/ONNXRuntime/README.md), [YOLOX license](https://github.com/Megvii-BaseDetection/YOLOX/blob/6ddff4824372906469a7fae2dc3206c7aa4bbaee/LICENSE), [OpenCV wheel](https://pypi.org/project/opencv-python-headless/5.0.0.93/), [NumPy wheel](https://pypi.org/project/numpy/2.5.4/), [FFmpeg source](https://ffmpeg.org/download.html), [FFmpeg licensing](https://ffmpeg.org/legal.html), and [LLVM-MinGW release](https://github.com/mstorsjo/llvm-mingw/releases/tag/20261104).
