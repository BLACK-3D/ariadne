# Daily briefing

Version 0.14 adds an opt-in [daily repository watch](REPOSITORY_WATCH_0_14.md) to the same local report. It shares existing Internet/pause controls, uses a fixed shortlist and retains dated results offline. The user's requested 6 AM repository updates do not require Codex to be open.

The [29 October 2026 capability radar](CAPABILITY_RADAR_2026_10_29.md) is a separately dated research addendum covering business systems, trading technology and local controls. Its local copy lives under `data/workspace/briefings/capability-radar-2026-10-29`. It preserves the completed daily report and its verified receipt. The scan creates no automatic repository polling, installations, broker connections or device authority; future repository-update scheduling is a separate preference.

In 0.7, the owner Internet switch in **Internet** also governs this collector, including the independent Windows scheduled job. New installations start with the switch off. With it off, reports use saved dated evidence and make no new public requests. Both the master Internet switch and the briefing's online-source preference must be enabled for refresh. See [Internet operation](INTERNET.md).

Ariadne can produce a deterministic daily brief at 06:00 America/Chicago. The
background timer runs while the application is open; the separate Windows wake
task starts the brief job after sleep when the PC and its firmware allow it.
Neither mechanism can start a powered-off PC. A powered PC, a working clock,
storage and sufficient power remain necessary.

The scheduler uses the bundled IANA America/Chicago time-zone record. It follows
daylight-saving changes, including the 23- and 25-hour reporting windows. A new
schedule starts at its next future local time. A server that restarts after 06:00
can catch up the current day's missed brief. Older days are not automatically
backfilled. Each local date has one durable scheduled request ID. A failed or
interrupted daily request pauses the briefing schedule for explicit review;
uncertain work is never automatically replayed. Global autonomy pause blocks
scheduled runs and source collection. Manual reports use a fresh request ID and
the previous 24 hours; scheduled reports cover the previous local 06:00 to the
current local 06:00.

## Activity coverage

The brief counts all stored Ariadne missions touched and recorded events during
the reporting window. It includes mission outcomes, verification status,
unresolved approvals, fixed local procedures, schedules, and counts of newly
saved memories and document imports. It includes up to 1,000 detail rows per
category and explicitly marks detail truncation. Aggregate mission/event counts
cover the entire queried window. It cannot see unlogged operating-system or
other-application activity. Procedure details are also bounded at 1,000.

Reports make zero model calls. They publish new, exclusive
`workspace/briefings/<request-id>/brief.md` and `brief.json` artifacts with byte
counts and SHA-256 hashes. Reusing a completed request verifies the original
artifacts and returns the existing receipt. Edited artifacts are preserved and
reported as changed. Partial or failed requests remain in history and require a
new manual ID. Database state lives in the private `briefing.sqlite3`; public
distribution must exclude it along with all other owner data.

## Weather, forecast, and impact sources

The owner configures a region label and verified US latitude/longitude. The
collector currently supports:

- NWS latest station observation, with source timestamp and original units.
- NWS seven-day forecast and point-based active weather alerts.
- USGS magnitude 2.5+ events from the past-day feed, filtered within 500 km.
- Up to five owner-configured official `.gov` RSS/Atom feeds. Optional literal
  regional terms filter feed titles/descriptions. These are feed matches, not an
  analysis of every possible regional impact.

This is bounded official-source coverage, not an all-news service or guaranteed
emergency monitoring. No configured regional news feed means regional news is
explicitly unavailable. An empty feed does not mean there are no hazards. Alert
source descriptions and expiration dates are retained; expired alerts are
labeled historical. Source-issued and retrieved timestamps remain distinct.
Freshness thresholds are 2 hours for station observations and earthquake feeds,
6 hours for forecasts, 30 minutes for alerts and 24 hours for news feeds.
Observation/forecast freshness also considers the source-issued time.

The main server retains its offline network policy. Persisting `online_sources`
allows a separate fixed-purpose collector process to fetch public data. This
worker uses certificate-verified HTTPS, pins a DNS-resolved public IP, rejects
private/reserved addresses, credentials, redirects, proxies and compressed
responses, and caps each response at 2 MB. Only the fixed NWS/USGS paths and
explicitly configured official feeds are requested. A 100-second parent timeout
bounds the entire worker; each socket has an 8-second timeout. The worker uses
no model or downloaded executable code. External text is data and grants no
tool authority. UTF-8/ASCII RSS/Atom is accepted; DTDs and entities are rejected.

With the internet unavailable, the service retains the previous usable source
snapshots, including their original timestamps. Failed refreshes and stale
sources are marked in the brief. With no cache, the brief says unavailable and
does not infer weather, headlines or regional conditions. Offline operation
cannot obtain new forecasts or news.

## Command line and integration

Use the bundled interpreter from the box directory:

```text
runtime\python\python.exe -I -B scripts\briefing.py status
runtime\python\python.exe -I -B scripts\briefing.py due
runtime\python\python.exe -I -B scripts\briefing.py run --request-id my-reviewed-request
```

`--data-dir` and `--workspace` can target a relocated installation; E: is rejected.
`--due` is an alias for the `due` action. `collect` respects persisted settings
and global pause. `collect-worker` is an internal child entry point. Server
routes must remain authenticated; the model does not receive a network-policy
bypass tool.

`BriefingService(settings, store, registry)` exposes `status`, `config`,
`configure`, `history`, `run`, `run_due`, `start`, `stop`, and `recover`.
`configure` accepts `enabled`, `paused`, `timezone` (America/Chicago or UTC),
`hour`, `minute`, `region`, `online_sources`, and `news_feeds`. Region fields:
`name`, `latitude`, `longitude`, `country` (US). News feed fields: `title`, `url`,
and optional `region_terms`. Configuration revisions increment on each change;
existing owner settings are preserved. Do not auto-resume a paused schedule.

Primary specifications:

- [NWS API documentation](https://www.weather.gov/documentation/services-web-api)
- [NWS active-alert point filtering](https://www.weather.gov/media/documentation/docs/NWS_Geolocation.pdf)
- [USGS GeoJSON feed format](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php)
- [Python tzdata package](https://github.com/python/tzdata)

The bundled Chicago TZif is from the existing tzdata 2026.6 / IANA 2026f cache.
Its Apache notices are included beside the record in
`ariadne/resources/briefing/`. Changes in future civil-time rules require a
reviewed time-zone data update.
