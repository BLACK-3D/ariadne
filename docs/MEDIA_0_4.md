# Offline media and multimodal models in Ariadne 0.4

All model files, Python wheels, temporary files and qualification evidence stay under Yikes. Runtime media does not download assets, use a cloud API, activate a microphone/camera, clone a voice or infer a person's identity. E: is excluded by the shared path checks.

## Installed and qualified on October 28, 2026

| Capability | Implementation | Evidence and limits |
| --- | --- | --- |
| Image understanding | Ollama Qwen3.5:9b | Correctly read `ARIADNE 42` and described the red square on the left and blue circle on the right of a generated test image. Checks the installed model's `vision` capability before inference. Cold model load plus inference took 142.971 seconds on this host. |
| Native tool calls | Qwen3.5:9b and previous Qwen3:8b | Both emitted the correct `calculate` call for `17*23`. Sequential cold wall times were 16.874 and 18.387 seconds respectively. This single smoke check establishes neither broad model superiority nor throughput. |
| Streaming ASR decoder | Sherpa-ONNX 2.0.1, quantized English Zipformer | Decoded generated speech as `ARIADNE IS READY THE OFF LINE SYSTEM HAS FORTY TWO SUPPLIES` with 11 partial updates. The current HTTP voice endpoint returns the final transcript; the media tool also returns partial records. It does not provide a live microphone WebSocket. Whisper.cpp remains the fallback. |
| Natural speech | Kokoro-ONNX 0.6.1, INT8 Kokoro v1.0, `af_heart` preset | Produced a 204,268-byte PCM WAV. A separate cold worker took 19.809 seconds for the short sentence “Ariadne is ready.” Natural speech is an explicit option; existing OS/Piper speech remains the quick default. |
| Speech timestamps | Silero VAD v6.2.3 ONNX | Found two speech regions in the generated test. Mono PCM16 16 kHz WAV input, at most 90 seconds. Timestamps are approximate. |
| Anonymous speaker labels | Sherpa-ONNX, public MIT Pyannote segmentation conversion and CAM++ embedding | Processed the generated single-voice fixture successfully, returning one anonymous speech segment in 2.381 seconds. This does not validate multi-speaker separation accuracy, identify a speaker, or expose voice embeddings. |
| Image creation | FLUX.2-klein-4B, Diffusers 0.42.1, PyTorch 2.14.1 + CUDA 13.0 | Generated and visually checked a 512 by 512 green console illustration in 189.066 cold seconds. The PNG is 202,561 bytes with SHA-256 `b52f0fd38421bbdcbdd2cce9684e4af87d88390f5607a1ac109950ffef213b82`. This is a working on-demand specialist, not real-time generation. |

Detailed output is in `data/media-qualification/results.json`, with the single-voice diarization check in `data/media-qualification/diarization.json` and image check in `data/media-qualification/flux.json`. The image itself is `data/workspace/media-qualification/flux-console.png`. No live human audio was recorded for qualification. The host is a Core i9-9900K, 64 GiB RAM and RTX 5060 Ti with 16,311 MiB reported VRAM. Native Windows SAPI speech separately produced a valid 125,912-byte WAV in 1.219 seconds, confirming the quick default still works.

## Tools and input boundaries

- `media_inspect_image`: one to three workspace PNG/JPEG snapshots; maximum 8 MB each, 6000 pixels per side and 12 million pixels before resizing. The worker strips metadata, applies orientation, converts to RGB and resizes within 1536 by 1536. Only the normalized pixel image goes to the loopback vision model. The vision call receives no tool definitions; image text is untrusted evidence.
- `media_transcribe`: reads a supplied workspace WAV and returns text, snapshot SHA-256 and duration. Sherpa uses 200 ms decoder chunks and returns bounded partial records through this tool.
- `media_vad`: returns speech activity timestamps for the same finite WAV contract.
- `media_diarize`: returns approximate anonymous `SPEAKER_XX` labels with timestamps. Labels are local to one request and carry no asserted identity.
- `media_speak`: writes a new `.wav` using `natural` or `system` speech. Natural is preferred for this explicit artifact tool when provisioned; the interactive voice endpoint defaults to system speech.
- `media_generate_image`: a finite FLUX.2-klein-4B profile, one PNG, 256–1024 pixel sides in multiples of 64, four inference steps, fixed guidance and a bounded integer seed. No custom code, remote model identifier, URL or arbitrary pipeline parameters are accepted.

Speech and image creation require the application's approval flow and always create a new output. Existing files are never overwritten. Input file handles and directory guards produce immutable byte snapshots, with content hashes in the results. The media profile remains outside the model-writeable workspace.

Schemas omit unavailable optional backends. A configured dependency is distinct from a broadly evaluated model: qualification above describes precisely the limited evidence collected.

## Local installation and outage recovery

Use the project Python environment on native Windows x64 / CPython 3.13:

```powershell
.venv\Scripts\python.exe scripts/provision_media.py --install
.venv\Scripts\python.exe scripts/provision_media.py --flux --install
```

The first command caches and SHA-256 verifies the fixed speech models, dependency wheels and isolated `runtime/media/python` installation. The second also provisions the complete FLUX pipeline from one pinned Hugging Face revision and an isolated `runtime/media/flux-python` CUDA stack. `scripts/media-flux-assets.json` records every required model file, size, URL and hash; the duplicate standalone transformer file is deliberately omitted.

The full FLUX pipeline contains 18 model/config/tokenizer artifacts totaling 15,980,131,745 bytes. Its CUDA wheel cache is separate from the CPU speech cache. These are upfront storage costs; no idle model calls or recurring hosted API fees are introduced.

For a cold repair without internet, retain `runtime/media`, `requirements-media.txt`, `requirements-image.txt`, `data/media-config.json` and the model directories, then run:

```powershell
.venv\Scripts\python.exe scripts/provision_media.py --offline --install
.venv\Scripts\python.exe scripts/provision_media.py --offline --flux --install
```

Offline mode verifies cached files and runs pip with `--no-index --require-hashes --no-deps`. It never queries release metadata. It fails if an artifact is missing or changed. Other OS/Python combinations need their own wheel cache and qualification; these Windows wheels are not portable binaries.

To reproduce the bounded speech, vision and tool checks:

```powershell
.venv\Scripts\python.exe scripts/qualify_media.py
.venv\Scripts\python.exe -m unittest tests.test_media_tools tests.test_model -v
```

The qualification command uses only generated fixtures and the local Ollama API. It does not change the configured conversational model.

## Resource use and runtime integrity

Workers use one media slot, two CPU library threads, an 8 GiB process-memory limit, 180 CPU seconds and 240 wall seconds. FLUX has a separate fixed 32 GiB host-memory ceiling. Native-library GPU allocations do not have an equivalent VRAM sandbox. A process-wide GPU lock serializes Ollama chat with image generation; generation unloads resident models from the configured loopback Ollama service before loading its pipeline. Chat reloads its selected model on demand afterward. External programs can still compete for the GPU.

Each request verifies the pinned model hash before native loading. FLUX additionally checks its complete model-file manifest, rejects extra artifacts, uses safetensors and local-only loading, and keeps remote code disabled. Workers use a minimal environment with Hugging Face offline flags, disabled ONNX telemetry and an audit hook denying Python socket and child-process operations. These measures and OS resource caps are **not an OS filesystem/network sandbox for native libraries**.

Approval fingerprints bind the complete profile, selected model, destination state, implementation source hashes and the resolved speech execution route. System speech additionally binds the Piper binary/model/configuration hashes, or the fixed Windows PowerShell binary hash, so changing an approved execution artifact requires new approval.

## Upstream provenance and licenses

- [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B): Apache 2.0 weights; the installed Ollama manifest digest is recorded in `data/provision-manifest.json`.
- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M): Apache 2.0 weights; [Kokoro-ONNX](https://github.com/thewh1teagle/kokoro-onnx) code is MIT. Its phonemizer and bundled [eSpeak NG](https://github.com/espeak-ng/espeak-ng) are GPLv3 components. Keep their source/license obligations when redistributing an appliance; the complete voice stack is not solely Apache/MIT.
- [Sherpa-ONNX](https://github.com/k2-fsa/sherpa-onnx): Apache 2.0 code; its documented [English streaming model](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26) has a pinned revision. The [public converted segmentation repository](https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0) includes its MIT license. [3D-Speaker](https://github.com/modelscope/3D-Speaker) publishes the CAM++ implementation under Apache 2.0; the embedding file is obtained from Sherpa's documented public release asset, with an observed SHA-256 pin.
- [Silero VAD](https://github.com/snakers4/silero-vad/tree/v6.2.3): MIT; a fixed upstream ONNX asset is used directly, without Torch Hub or a runtime download.
- [FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B): Apache 2.0 selected model. This profile does not substitute custom-licensed FLUX 3 or another FLUX family. [Diffusers](https://github.com/huggingface/diffusers), Transformers and Accelerate are Apache 2.0; [PyTorch](https://github.com/pytorch/pytorch) and its CUDA redistributables have their own included license notices.

The source distribution does not silently bundle third-party model weights or native runtime binaries. Retain their upstream licenses and notices in any separately distributed offline installation image.

`runtime/media/licenses` retains 20 complete publisher license/model-card/build-provenance artifacts pinned by `scripts/media-license-assets.json`, plus 290 original dependency wheel license/notice/metadata artifacts, including 100 from the CUDA PyTorch wheel. `model-notice-provenance.json` and `python-notice-provenance.json` record their source URLs, wheel identities and SHA-256 digests. The selected FLUX repository's full Apache license/card and the separate Qwen3-4B text-encoder license/card are retained; the VAE's `_name_or_path` origin is also preserved in its pinned config. This evidence describes these exact selected artifacts, not other models in their families.
