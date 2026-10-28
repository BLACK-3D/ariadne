# Internet access in Ariadne 0.7

Open **Internet** in the console and choose **Enable Internet**. The owner can then test a public connection, search, or read a public HTTPS page. Agents receive `web_search` and `web_fetch` while enabled. You can also say “Ariadne, read https://…” when PC voice is listening. A wake word is not speaker authentication.

This installation can be enabled at the owner's request. Fresh installations default to off. The persisted owner switch also governs the daily news/weather collector, including its independent Windows scheduled job. **Go offline** revokes active public web reads; the collector stops before further source requests and rejects results if the switch changes during collection. Offline daily reports can still use their existing dated cache. The separate briefing `online_sources` preference must also be enabled to refresh its regional sources.

## Public reading

- Public HTTPS port 443 only, using certificate verification, validated public DNS addresses, pinned destination sockets, and at most three revalidated redirects.
- Fixed GET operations only. No sign-in, cookies, arbitrary headers, uploads, purchases, remote page scripts, or executable downloads.
- Text, HTML, JSON, XML and feeds are accepted. Responses are capped at 2 MB and extracted text at 12,000 characters. The local model receives a smaller excerpt with clipping identified.
- Search uses DuckDuckGo's HTML interface. If unavailable or challenged, a clearly labeled Wikipedia-only fallback uses the public MediaWiki search API. A snippet is a lead, not proof that Ariadne read the destination page. Challenges and access restrictions are not bypassed.
- The connection test reads the National Weather Service API root. A successful request proves that endpoint was reachable at that time; it is not continuous connectivity monitoring.

Public requests disclose your query or requested URL and public IP to the destination. Avoid putting private information in searches and URLs. The installation's pairing key and credential-style URL parameters are blocked, but this is not a general private-data detector. Remote content remains untrusted data; it does not authorize tools or change approval policy. HTTPS verifies transport and server identity, not truth or harmlessness of source content.

## Offline evidence and operating cost

Matching results are reused for 15 minutes for searches and six hours for pages. Select **Request fresh evidence** when freshness matters. When disabled or a refresh fails, saved evidence keeps its original retrieval time, cache age and stale/failed-refresh labels. No missing result is presented as a successful fresh request. Saved snippets, source URLs and SHA-256 hashes identify their originating HTTP responses; extracted text is a transformation of those bytes.

The cache is bounded to 100 results and 20 MB; request history retains at most 1,000 entries. These are private files under `data/internet.sqlite3`, included in the local backup script. History can include queries and URLs. There are at most two concurrent public workers, each limited to 65 seconds. Status polling is local: it does not probe the network. No paid API, new model or idle language-model calls are needed for this feature. No measured energy or monetary savings are claimed.

The main Python process retains its offline socket policy. An explicitly enabled fixed child performs public HTTPS reads. Bundled Mozilla certificate roots supplement Windows trust when available. This boundary is not an OS firewall; configured connectors and native integrations retain their existing independent settings and approval policies. This feature does not change Wi-Fi, Ethernet, router, firewall, Windows power, or adapter settings. A working internet connection on the PC is still necessary.

## API

All routes require the normal same-origin owner bearer credential:

| Route | Body / result |
| --- | --- |
| `GET /api/internet` | Local configuration, cache count, last successful request, limits |
| `PUT /api/internet/config` | `{enabled: boolean, expected_revision?: integer}`; stale revisions fail |
| `POST /api/internet/probe` | `{refresh?: boolean}`; defaults to fresh |
| `POST /api/internet/search` | `{query: string, max_results?: 1..5, refresh?: boolean}`; query up to 512 characters |
| `POST /api/internet/fetch` | `{url: string, refresh?: boolean}`; URL up to 2,048 characters |
| `GET /api/internet/history?limit=30` | Recent local request receipts, capped at 100 per read |

The model cannot enable Internet or change this configuration. Existing scope checks, approvals, pauses and substantive failure stops remain in force.

Primary references: [MediaWiki Search API](https://www.mediawiki.org/wiki/API:Search), [National Weather Service API](https://www.weather.gov/documentation/services-web-api), [OWASP SSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).
