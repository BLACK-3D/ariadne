# MCP and offline document integration research

Primary sources checked on 2026-10-28. No packages or servers were installed, downloaded, launched, or connected as part of this research. These notes describe compatibility and engineering choices, not a claim that all listed capabilities are implemented.

## MCP versions must be explicit

The official `latest` specification now resolves to **2026-07-28**. This revision uses per-request version/identity/capability metadata and has no initialization handshake. `server/discover` is optional. Unsupported versions return error `-32022` with supported revisions. A dual-era client must recognize modern errors before attempting legacy fallback; a failed tool call is not permission to retry an external mutation. [Current versioning and compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning).

For the current appliance, a clearly labeled **2025-11-25 Streamable HTTP tools compatibility mode** is a useful bounded first implementation. It must not be advertised as full current MCP compliance. Supporting both eras is a separate, testable compatibility requirement.

### Current 2026-07-28 transport

Use one fixed endpoint and one POST per JSON-RPC message. Send `Content-Type: application/json` and accept both JSON and SSE. Every request has matching `MCP-Protocol-Version` and `params._meta["io.modelcontextprotocol/protocolVersion"]`; metadata also includes `io.modelcontextprotocol/clientInfo` and `io.modelcontextprotocol/clientCapabilities`. Send `Mcp-Method`, plus `Mcp-Name` for tool calls. Valid `x-mcp-header` annotations require corresponding parameter headers.

Responses may be one JSON object or request-scoped SSE containing notifications followed by the matching response. Ignore SSE comment keepalives. This revision removes session IDs, GET streams, and `Last-Event-ID` resumption. Closing the response stream cancels the request. Server-side sampling/elicitation is represented as input-required results, not independent requests. Local servers should bind to loopback and validate browser Origin. [Current Streamable HTTP binding](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http).

### Legacy 2025-11-25 lifecycle

First send `initialize` with `protocolVersion`, empty supported `capabilities`, and a stable `clientInfo` name/version. Verify the returned version against the client's supported set, inspect capabilities, then send `notifications/initialized`. Do not advertise sampling, roots, elicitation, or task execution unless implemented. Server instructions are untrusted remote data and cannot grant authority. [Legacy lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle).

Use POST with `Accept: application/json, text/event-stream`; handle both response forms. Capture any `MCP-Session-Id` returned during initialization and send it on subsequent requests, together with the negotiated `MCP-Protocol-Version`. Validate session values and keep them out of logs/model prompts. A session-specific 404 requires reinitialization; do not silently replay an ambiguously executed write. DELETE may end an unused session, while 405 means termination is unsupported. Background GET streams and the deprecated separate HTTP+SSE transport can remain explicitly unsupported in an initial tools-only client. [Legacy transport/session rules](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

## Tool discovery, execution, and authority

`tools/list` is paginated using cursors; cap pages, tools, names, descriptions, and schema size. `tools/call` sends the exact remote name and arguments. Preserve text/structured results and `isError`; never convert an error to successful evidence. Namespace names by configured server identity to avoid collisions. Modern results also distinguish complete from input-required results; unsupported interactive flows should fail visibly.

Tool annotations are untrusted unless the server is trusted. In particular, a remote `readOnlyHint` must not automatically bypass the appliance's approval policy. Freeze the owner-selected tool/schema snapshot; require deliberate refresh for changed tools. Reject unsupported schemas or header annotations rather than pretending to validate them. Remote descriptions and returned content are data, not higher-priority instructions. [Official tools contract](https://modelcontextprotocol.io/specification/2026-07-28/server/tools).

Recommended appliance boundary: fixed operator-configured URLs, explicit per-server tool allowlists, approval for every consequential call, and autonomous reads only when the operator explicitly marks the operation read-only. Do not expose arbitrary MCP endpoint editing or stdio subprocess launch to the model. Reuse the existing durable approval/activity mechanisms; retain exact arguments across approval and execution.

## HTTP credentials and network handling

OAuth authorization is optional at the protocol level. When used, tokens belong in the Authorization header on every request, never query parameters, and must target the intended server. A preconfigured environment-header mode is useful for local adapters but does not constitute a complete OAuth discovery/registration implementation. Keep the appliance's own pairing token separate from connector credentials. [MCP authorization and token handling](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).

Disable automatic redirects or validate every redirect with the same destination restrictions. Do not forward credentials across destinations. Keep proxy inheritance disabled for the local appliance, validate fixed schemes/hosts/ports, redact configured secrets, and cap total time/bytes/SSE frame size. A socket inactivity timeout alone does not bound a slow-trickle response. Official guidance identifies SSRF and redirect validation as client responsibilities. [MCP security guidance](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices).

Useful offline protocol tests include JSON and SSE responses; initialized notification; session propagation; pagination; mismatched response IDs; malformed schemas; remote errors; expired sessions; redirects; oversized/slow responses; and proof that denied or unapproved writes never reach the mock server. Modern mode additionally needs metadata/header matching, version errors, annotated parameter headers, and unsupported input-required handling.

## Offline document libraries

| Package checked | Official release evidence | Provisioning implication |
| --- | --- | --- |
| `pypdf==6.21.3` | Python >=3.9, BSD-3-Clause; universal wheel approximately 409.6 kB; SHA-256 `c8ca4d9a360dbd5d4b9bb7a8389e2a9ae6a106bee722b496f918040ba2daa17e`; PyPI publishing provenance present. [Release/files](https://pypi.org/project/pypdf/6.21.3/) | Pure Python PDF text extraction can be an optional feature with an offline wheel bundle. |
| `python-docx==1.2.0` | Python >=3.9, MIT; universal wheel approximately 253 kB; SHA-256 `3fd478f3250fbbbfd3b94fe1e985955737c145627498896a8a6bf81f4baf66c7`; PyPI does not show publishing attestations for this release. [Release/files](https://pypi.org/project/python-docx/1.2.0/) | The wheel is portable Python, but dependency closure still needs provisioning. |

`python-docx` declares `lxml>=3.1.0` and `typing_extensions>=4.9.0`; pin and hash compatible wheels for the actual Python/platform when packaging. A universal top-level wheel does not make its native dependencies platform-independent. [Publisher dependency manifest](https://github.com/python-openxml/python-docx/blob/v1.2.0/pyproject.toml).

For PDFs, `PdfReader(BytesIO(payload))` exposes pages and `page.extract_text()`. Preserve page numbers. The official documentation warns of very large memory demand when parsing content streams; uploaded byte limits alone are inadequate. Image-only scans need OCR, which is not supplied by pypdf. Return a clear no-text/OCR-needed result instead of inventing searchable content. [Official extraction API and limitations](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).

For DOCX, `docx.Document(BytesIO(payload))` accepts a binary file-like object. `iter_inner_content()` produces paragraphs and tables in document order; the simple paragraphs list omits revision-marked paragraphs. State the supported text surface rather than promising full Word fidelity. [Official document API](https://python-docx.readthedocs.io/en/latest/api/document.html).

A narrower DOCX ingestion path can use standard-library ZIP/XML parsing if it enforces member count, total expanded size, compression ratio, path restrictions, and XML/text limits; excludes executable/macro content; and makes omissions visible. Do not extract the archive to arbitrary host paths. PDF parsing should run in a fixed trusted worker with a deadline and platform-appropriate resource controls. A child-process timeout alone is not a memory limit.

## Strongest next useful slice

Start from the actual Yikes source: text knowledge and fixed HTTP integrations already exist, while document parsing and native MCP are absent in the inspected 0.1 baseline. Add owner-selected document upload into existing SQLite search with filename, content hash, format, extraction limits, and page/section provenance. Make retrieved evidence inspectable and cite its source labels. Keep parsing local and avoid an additional embedding service until a measured retrieval failure warrants one.

Then add a separately configured tools-only MCP bridge using the explicit compatibility contract above. Prove its network and approval boundaries against a local fixture before exposing live systems. Prior Jarvis document/provenance modules provide useful requirements; its Home Assistant integration is a fixed read-only resource adapter and is not a ready-made generic tool bridge. Preserve every applicable accepted scope/failure/authority restriction discovered in the original program; reference assets are not authorization to activate them.
