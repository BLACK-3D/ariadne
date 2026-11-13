# Live preview and Ask Ariadne (0.40)

Start a video-only USB session to see a local preview. Ask an explicit question to describe one frame using the selected installed local image model. Showing the preview does not invoke a language model. There is no cloud fallback, automatic model download, audio capture or continuous captioning.

## Start, look, ask, stop

1. Open **Awareness → Camera / Video**. Press **Discover cameras**, then select the USB camera. Windows may call a Razer Kiyo **USB Video Device**. Discovery lists devices without opening capture.
2. Leave **Live preview** enabled to see the camera image. Its frames stay in memory. **Retain a thumbnail** is a separate option, unchecked by default.
3. Choose a duration and press **Start camera**. The default is 30 seconds; the maximum is 120 seconds. Preview updates at most once per second. This is a sampled awareness view, not a high-frame-rate video call.
4. While a fresh preview is available, use **Ask Ariadne about this view**, enter a question and press **Describe current frame**. The service freezes one frame for that request. Its answer records the frame time and local model. A description can be wrong; it is a model interpretation, not verified scene truth.
5. **Stop camera** stays available while a description runs. Stopping, leaving, hiding or locking the view ends its owner lease and clears the preview. Expiry also stops capture. Nothing resumes automatically. An explicitly stopped, revoked or paused source cannot publish a late answer. An already frozen question may finish after ordinary duration expiry; it remains labelled as an interpretation of that earlier frame.

The application uses native video capture and never requests browser camera/microphone permission, opens a microphone or synthesizes speech in this workflow. Existing voice settings are preserved. No camera image is sent to Codex, a cloud model or a connected account by this feature. Live images and descriptions require a same-PC loopback client plus the existing authentication, Host and Origin controls.

## The local model

The selected model must be downloaded locally and advertise image understanding in its runtime metadata. The default prepared `qwen3.5:9b` installation supports this when the metadata includes `vision`; its name alone is not proof. Image inference accepts only a numeric-loopback HTTP model endpoint. Proxy, redirect, remote/cloud-selector and unverified model identities are rejected. The downloaded model digest is checked before and after inference. The model receives no tools or action authority; text seen in an image is untrusted data, not an instruction.

If the selected model is unavailable, unsupported, busy or remotely configured, the question stops. The feature does not download, switch models or queue behind another model request. Prepare or select another model only through the existing separate owner controls. This can work without Internet after the core and optional vision runtime have been prepared. See [vision installation and portable exports](VISION_LOCAL.md).

## What is retained

Only the newest preview JPEG is retained in Ariadne RAM. A camera receipt contains detections, hashes and timestamps, not preview images. The separate thumbnail option can retain an image only when explicitly chosen. Description receipts retain bounded question/answer text, source-frame hashes and model identity; they do not retain image bytes. The local model service may hold its inference context in memory. Stop does not unload the owner's selected model.

Failed or interrupted descriptions are held for exact-hash owner review. They do not silently retry. Reading an already completed receipt verifies saved evidence and never regenerates an answer. Captions stay in the owner Vision desk, outside general agent context, so a later model change cannot forward them through a resumed agent conversation. The existing `vision_read` tool still reads bounded detector evidence; it cannot export captions, start capture, renew its lease or generate a new description. New image interpretation requires an explicit question in the Vision desk.

Private caption storage must be outside the configured Workspace; otherwise new descriptions are unavailable. Owner-selected file and knowledge-source scopes remain authoritative. Explicitly broadening those scopes later can make previously private files readable by the general tools; the feature does not override that local owner reconfiguration.

Do not treat object scores as calibrated probabilities, a missing detection as proof of absence, or an image interpretation as identity, a safety assessment or authority to operate a device. No physical action follows from these results.

## Freshness and resource bounds

Preview validity includes source lag, received-frame age, monotonic age, owner lease and global control revision. A delayed frame is withheld rather than frozen on screen as a current view. USB initialization and driver buffering can reduce the available freshness window. Driver latency and the physical exposure clock remain unverified. A frame's acquisition time and the model's later completion time are distinct.

Capture keeps the existing CPU, memory, duration, sample, retention and 2 MiB metadata-output bounds. Opting into preview adds a separate bounded channel: JPEGs are at most 64 KiB and 640×480, at most one per second, with at most 16 MiB of encoded preview output per session. Images are replaced as new frames arrive, cleared when capture authority ends, and excluded from saved evidence and exports.

Descriptions run only on explicit request, with a bounded local-model deadline and output budget. There is no resident captioning loop. CPU/GPU time, memory and electricity still have operating costs even without a paid subscription or model API. Camera visibility, lighting, false detections, local model quality and startup time need qualification on the intended PC; no universal latency or accuracy is promised.
