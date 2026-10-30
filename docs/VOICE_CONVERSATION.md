# Responsive conversation and wake controls

Say **Ariadne** once after explicitly enabling the PC microphone. Follow-up utterances can then continue the same conversation without repeating her name. The conversation stays awake for two minutes of inactivity after her response finishes. Model processing, acknowledgements and playback do not consume that idle window.

- **Go to sleep**, **Ariadne sleep**, or **sleep Ariadne** ends the conversation and returns to wake-word waiting. Microphone capture remains enabled.
- **Stop listening** or **Ariadne, shutdown** stops microphone capture and disables saved microphone startup. It does not shut down the computer or silently cancel a saved mission.
- The existing Stop control remains available. Microphone listening and startup are never enabled by this update.

The status card distinguishes Awake, Waiting for Ariadne, working, playback, stopped and unavailable states. While capture is enabled, the interface reads only the lightweight wake status every two seconds; there are no such reads while it is off or unpaired. Those status reads make no model calls. Late responses cannot overwrite a newer stop or another pairing.

Capture is muted during Ariadne's own playback and its short trailing suppression window. Give follow-ups or sleep controls after she finishes speaking. Dropped, duplicate, old-sequence and previous-session utterances are never replayed. New wakes after sleep or idle expiry get a new conversation ID; bounded prior replies are shared only within that conversation. Speech does not authenticate a speaker or grant tool permissions. Existing approvals, pauses, queue limits and interrupted-work stops still apply.

## Response latency

The installed `qwen3.5:9b` model remains selected. Simple conversation uses a compact prompt and a single routing function. Requests needing files, fresh information, calculations, business/trading evidence, substantial artifacts or actions use the full mission path. A compact routing call performs no action and consumes a normal step before fallback. Mission context and output budgets remain 16,384 and 2,048 tokens; the conversational reply budget is 384 tokens with a short spoken style.

The selected model can load in the background on app startup, explicit microphone start and a new accepted wake. Loading uses an empty chat request and generates no output tokens. It never polls, downloads a model, queues behind an existing GPU consumer, or unloads another model. An already resident compatible model is left alone, preserving its prompt cache. Active requests retain the model for fifteen minutes instead of five. This reserves GPU memory longer; energy savings were not measured or claimed. The initial load can still take several seconds, and substantial tool work takes longer than casual replies.

Ollama usage events now retain load, prompt processing, cached prompt tokens and output-generation timing separately. Missing cache/timing metadata remains unknown. Model names, output caps and a warm-state badge alone do not prove speed or answer quality.

On this installation's RTX 5060 Ti, matched local probes of the same two capability questions measured 8.172 → 1.950 seconds and 4.768 → 1.274 seconds for full versus compact replies. Prompt tokens fell from 3,763/3,748 to 615/600; output lengths also changed from 294/197 to 71/48 tokens. These are small measured samples, not universal response-time guarantees or exact replays of the earlier live demo. Two synthetic Cori samples took 0.218 and 0.439 seconds to synthesize; they were saved without playback or microphone capture. British Cori and speed 1.12 remain unchanged.

The earlier slow business request contained a 90.9-second first model call. Logs attribute approximately 46.9 seconds to cold runner startup, 40.2 seconds to its first prompt processing and 3.1 seconds to output generation. The later wife-demo requests were 11.8 and 3.7 seconds in recorded mission timing. End-to-end hardware microphone and speaker timing has not been measured by these synthetic checks.

Implementation follows the official [Ollama chat API](https://docs.ollama.com/api/chat), [usage timing fields](https://docs.ollama.com/api/usage) and [model retention guidance](https://docs.ollama.com/faq). The existing full offline ZIP remains version 0.16.1 until separately rebuilt and qualified.
