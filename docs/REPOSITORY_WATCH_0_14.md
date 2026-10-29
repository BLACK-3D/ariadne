# Daily repository watch · 0.14

The local 6 AM brief can check a fixed shortlist of upstream releases, commits and model revisions. This runs inside Ariadne's existing isolated public-source collector, with **zero language-model calls**. It does not depend on Codex being open. The established Windows morning task and local scheduler keep their existing roles and timing.

Use **Daily brief → Daily repository updates** to select this section. **Refresh public sources when connected** and the shared Internet switch must also permit collection. New installations default to off. This owner's daily updates were requested explicitly; enabling them does not enable microphone listening, install software or connect accounts/devices.

## Coverage

The reviewed, hash-pinned `config/repository-watchlist.json` selects 15 upstreams across business, trading research, controls and AI. GitHub release checks track release identity, publication time, title and a notes digest; LEAN uses its selected branch's commit; Hugging Face entries track model revision and declared license metadata. This is a daily update watch, not a new exhaustive repository search, source-code security audit or trading signal.

The [49-candidate research report](CAPABILITY_RADAR_2026_10_29.md) remains the broader reference. Changing the fixed watchlist requires a reviewed source change and a matching pin. Remote responses cannot add repositories, choose arbitrary endpoints, grant permission or execute instructions.

## What the report says

- **First observation / baseline:** the first verified snapshot for an upstream. It is not described as a newly released product.
- **Changed:** selected metadata differs from a prior successful observation; the report preserves the previous identity and primary link.
- **Unchanged:** the source was checked successfully and selected metadata matches. This says nothing about every branch, vulnerability or project file.
- **Failed refresh / unavailable:** no new facts are asserted. A previous successful snapshot retains its original checked time.
- **Offline / stale / disabled:** the brief can use saved evidence with its age, or state that none is available. Data older than 36 hours is stale. Viewing a saved report never initiates a scan.

The full Markdown/JSON report contains the bounded per-upstream details. The portrait brief adds a compact repository-watch summary; omitted items remain in the full report. A finished brief's artifacts and receipt are not rewritten when later upstream data changes.

## Network, cost and authority

The collector makes fixed public HTTPS metadata requests, with normal certificate validation, public-address DNS pinning and the existing transport restrictions. It uses no GitHub, Hugging Face, broker or exchange credential. Public services see the requested public repository/model IDs and the public IP. Rate limits and service failures are reported; access restrictions are not bypassed.

The radar has a 45-second work budget, at most 16 allowed watch entries, a 256 KiB response limit per entry enforced by the HTTPS transport and parser, and a 256 KiB private-cache limit. Repository checks follow no redirects and cannot contact a remote `Location` target. Regular public web reading retains its existing separate defaults. The collector parent retains its 100-second lifetime bound, including regional sources. Native DNS resolution itself is bounded by that parent lifetime rather than an independent interruptible DNS deadline.

The cache is reused after an attempt for the same configured local day. Repeated manual reports do not flood upstreams; a failed day's checks are reported and the next day can try again. This design adds daily metadata I/O and bounded storage, with no resident model or extra always-on service. No measured power or monetary saving is claimed. Data-provider, broker or commercial-license costs in the research report are separate; none is purchased by this feature.

The global autonomy pause, briefing pause/disable state, Internet revision and briefing configuration are checked around collection and publication. Cache publication shares the canonical settings leases, preventing a revoked operation from publishing a fresh result. Private cache corruption is held and reported rather than silently overwritten. Uncertain, interrupted or failed daily report IDs are not replayed by this feature.

## Local state and portability

`data/repository-radar-cache.json` stores bounded metadata and comparison state; `data/repository-radar.lock` coordinates reporters. The optional cache is included in checked private backups. Factory/source exports include the public watchlist and implementation, while owner cache/history and credentials stay private. Models, runtime binaries and existing offline bundles are separate.

The app must be running, or the previously installed morning task must launch the local briefing command. Software cannot run while the PC is powered off. A powered local installation can still create its report without public Internet; repository evidence then keeps its original dates and missing/stale labels.

The Codex 6 AM companion review reads this completed local section and reports meaningful changes or collection failures. It does not perform a second repository scan, alter settings, install updates or broaden authority.

Primary endpoint references: [GitHub releases API](https://docs.github.com/en/rest/releases/releases), [GitHub commits API](https://docs.github.com/en/rest/commits/commits), [Hugging Face Hub API](https://huggingface.co/docs/hub/api).
