# Ariadne 0.3 qualification

Qualification performed locally on 2026-10-28 with Windows x64, project Python
3.13 and the hash-locked optional runtime wheels. Test artifacts and state stay
in Yikes. E: was not inspected or modified; E: rejection fixtures use strings
that fail validation before filesystem access.

## Real model and independent output check

`scripts/qualify_0_3.py` creates a fresh private fixture database/workspace for
each attempt. It uploads a synthetic image through SourceManager, searches the
recognized Knowledge text, and asks local Qwen3:8b to use analytics_query over
6,000 order rows and a 12-row price lookup, then save/read a summary and report.
The fixture computes expected values separately with Fraction arithmetic.

Original attempt `0865eef3f30a4f138d8f1e0069318ac1` exhausted its unchanged
12-step budget in 49.535 seconds. Its failed evidence is retained under
`data/verification-0.3`; no acceptance credit is given. Tool results showed a
repeated invalid/empty projection loop, which was corrected in the validator
and schema guidance. No expected numeric value was relaxed.

Corrected attempt `3f3d3cff19d545da9089771342a52a89` completed in 52.043 seconds,
12 steps. All five owner checks passed, successful analytics and Knowledge tool
calls were recorded, and the independent numeric check passed:

| Fact | Expected and actual |
| --- | --- |
| Total units | 18000 |
| Weighted stock value | 29250 |
| OCR service fee | 16.00 |

The output's two input hashes also matched the independently hashed source
files. Evidence: `data/verification-0.3/3f3d3cff19d545da9089771342a52a89/result.json`.
This is a specific tool-use fixture, not proof of general business correctness.
The subsequent directory-pinning hardening was separately requalified; the
same model mission was not repeatedly rerun after unrelated safety changes.

## Native and authority evidence

- Analytics: real DuckDB; 100,000-row exact total; 6,000-row grouped join against
  Fraction oracle; exact cancellation; precision/overflow rejection; locked
  external access and extensions; injection-shaped headers as plain data;
  bounded results, cancellation and actual OS memory ceiling.
- MCP: real FastMCP client against synthetic local modern/legacy HTTP services;
  no discovery at registration; explicit owner selection; no call before
  approval; stale configuration/identity/schema rejection; credential binding
  and redaction; wire/output/deadline bounds; uncertain write fails the run and
  stops its schedule without a model retry; competing configuration locks.
- OCR: real PNG, JPEG and raster PDF recognition; malformed/blank/encrypted
  inputs including empty-password encryption refused; page/pixel bounds;
  cancellation; missing/drifted packages/models/processor reported unavailable.
  Final path-dependent processor identity is recorded in the local receipt and
  `data/ocr-qualification/final-path-guard-identity/result.json`.
- Shared filesystem: actual Windows junction/hard-link checks, wrong opened
  lock-handle injection and temp directory replacement denial. Symbolic-link
  cases that require an unavailable privilege are reported as skips.

## Browser verification

Used a separate paused server on127.0.0.1:8089 with synthetic fixtures, no
production data. Existing approval fixtures were not approved or resumed.
Registered a local synthetic MCP service with zero selected tools; discovered
and inspected its schema; verified every selection starts unchecked and defaults
to per-call approval; applied one selected synthetic tool. The server remained
honest about configuration versus service availability.

Added an allowed synthetic reference folder, explicitly selected scanned-PDF
OCR, read recognized `ARIADNE WAREHOUSE / Service fee16.00`, inspected source,
extraction and processor fingerprints, and imported the reviewed result.
The Knowledge count increased and the button reported Imported.
Images: `ariadne-mcp-console-0.3.png` and final production console capture.

Final full-suite totals and local upgrade/archive verification are recorded in
the delivery receipt. A source ZIP contains no installation state or model
weights. Preserve the verified local runtime/wheel/model assets for an offline
appliance; source alone is not an offline disk image.
