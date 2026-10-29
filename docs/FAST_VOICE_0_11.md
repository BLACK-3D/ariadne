# Fast local voice (0.11)

Settings & setup → Local voice controls the speech engine, preset and pace for both PC wake replies and browser read-aloud. Cori is the default neural preset when the fast pack is installed. Its British English voice runs on two CPU inference threads without allocating a GPU model. The four existing Kokoro voices remain optional and are labeled slower. An existing explicit system-speech selection is preserved until the owner changes it.

The engine warms asynchronously when Ariadne starts with natural speech selected. It retains one owned process and caches only fixed “Listening.” acknowledgments, with at most four cached voice/rate combinations. Ordinary response text is not cached or logged by the speech worker. Initial warm-up still takes several seconds; subsequent replies reuse the initialized model. Stopping Ariadne closes the shared browser/wake/media-tool speech adapter. The media tool reuses that adapter rather than starting another resident engine for each artifact.

Spoken missions carry a trusted channel marker committed with the queued mission. The model is asked for roughly 40 words in one to three sentences unless the owner requests detail. Tool arguments, file contents, output budgets, permissions and approval requirements retain their existing behavior. Spoken excerpts remove presentation markup and stay short; full results remain in the console. Failed file checks and pending approvals are announced explicitly.

## Provisioning

The Windows x64 add-on uses the existing pinned media ONNX Runtime/NumPy installation. All downloads, cached wheels, notices and installed packages remain inside the box. Provision the media dependencies first on a source installation. Provisioning opens no microphone and plays no sound.

```powershell
# Explicit online provisioning, using public pinned assets.
.\runtime\python\python.exe -I -B scripts\provision_speech.py --download --install

# With cached files available, verify/install without an Internet request.
.\runtime\python\python.exe -I -B scripts\provision_speech.py --install

# While the server is stopped, add only the Piper backend to the local profile.
.\runtime\python\python.exe -I -B scripts\provision_speech.py --configure
```

`config/speech-assets.json` pins the official engine wheel, its dependency, the Cori model/configuration and upstream notices. Cori's model card states that it was trained from scratch on public-domain LibriVox recordings. Piper is separately licensed GPL-3.0-or-later; its full COPYING notice and source revision are retained. Existing owner media backends and voice selections are preserved. A noncanonical existing Piper profile is held for review rather than replaced. Existing package files must match their installation manifest before configuration.

The worker verifies accepted asset hashes before initialization and file identities before/after synthesis. It accepts only curated presets and finite pace values from 0.85 to 1.30. Requests remain bounded to 2,000 characters, 60 seconds, mono PCM16 WAV within 90 seconds/3 MB, a two-GiB process job and one process. Its Python network/child-execution denials are not an OS-wide firewall. An engine failure is held until the owner explicitly reapplies voice settings; it never silently retries a spoken mission or switches voices.

## Measurements and limits

On this i9-9900K Windows PC, three warm Cori generations of the same 23-word sample took 0.540–0.588 seconds for 6.51–6.78 seconds of audio at pace 1.12. Initial warm-up took 4.55 seconds; a cached acknowledgment took 2.65 milliseconds. The same phrase required approximately 35–42 seconds with warm Kokoro presets. These are local samples, not universal latency, energy or subjective-quality guarantees.

In one fixed local 9B-model question, the previous reply policy produced 204 words in 11.05 seconds; the final voice policy produced 40 words in 2.25 seconds. The model/options were held constant and the model was already loaded. This ordered single-question comparison does not establish general throughput or end-to-end microphone latency.

The owner auditioned Cori and selected it. Automated back-transcription with the small streaming recognizer and Whisper misheard the proper name and “stay concise” in that sample while recognizing its other principal clauses. Those failed exact-match probes remain in qualification evidence; no perfect pronunciation or recognition claim is made. Live room acoustics, false wakes and generator/physical-network-outage operation remain separate tests.
