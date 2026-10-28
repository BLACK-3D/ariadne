# Ariadne 0.3 delivery review

The work package is BUILD_0_3.md. This is an additive release of the Yikes
application. It makes no acceptance or promotion claim about the separate
Jarvis-Threadline repositories. No spending, external account connection,
public deployment or host-shell capability was added.

## Independent review allocation

- The MCP builder independently inspected the analytics implementation and
  shared worker/owner configuration paths. Its separate high-precision weighted
  cancellation and average fixture matched independent Decimal arithmetic.
- The OCR builder independently inspected MCP and its application integration;
  see REVIEW_0_3_MCP.md. The reviewer did not certify its own OCR code.
- The root delivery agent inspected OCR, its provisioning/model provenance,
  source/upload integration and processor-aware knowledge migration, and
  separately exercised browser controls and the real local model.

This is a focused code/boundary review with executable fixtures, not a complete
dependency audit or security certification. There is no Git baseline here.

## Findings resolved before delivery

1. Cooperating CLI/API configuration mutations could race. Both now hold the
   same OS file lock; approved MCP dispatch holds it across fingerprint recheck
   and the bounded network operation. Discovery evidence alone grants nothing.
2. A lock could write before validating hard links or its opened handle.
   Type/link checks and final opened-handle identity checks now precede writes.
   Synthetic wrong-handle and hard-link fixtures preserve original bytes.
3. Windows zero-access directory handles did not prevent renames. An actual
   CreateFileW test matrix established that FILE_LIST_DIRECTORY access does.
   Shared source/workspace/lock and worker-temp ancestor handles now use that
   access, without FILE_SHARE_DELETE. Replacement tests pass with WinError32.
4. Known credentials echoed as JSON numbers escaped string-only redaction.
   Exact matching numeric scalars are also redacted; live text/JSON fixtures pass.
   Arbitrary secret transformations are not universally detectable.
5. FastMCP could add internal extensions despite an empty requested list.
   The fixed client subclass explicitly disables automatic internal extensions.
6. Individually valid tool schemas could exceed the combined model schema
   budget after registration. The entire proposed catalog is validated before
   owner API/CLI commits.
7. Real-model analytics use alternated between invalid projection arguments.
   An empty optional projection now means omitted, and schema guidance explains
   aggregate use. The original acceptance expectations were preserved.
8. Source refresh previously deduplicated only file bytes. Processor identity
   and extracted-content hash now also participate; OCR imports bind a reviewed
   extraction fingerprint. Historical source metadata remains bounded.
9. OCR provisioning relied on an excluded installation-only wheel manifest.
   The verified public manifest is now in config for source-only deployment.

## Practical limits

Resource-limited child processes are not full OS sandboxes. The application
trusts its pinned Python/native dependencies and the owner-maintained host.
Loopback URLs and metadata snapshots do not cryptographically attest a remote
process. Owner read-only declarations cannot enforce service behavior.
Unexpected remote outcomes stop instead of replaying, but cannot undo effects.
OCR accuracy, all document layouts, every MCP extension/server, physical mobile
use, actual power loss, offline cold boot and restoring this backup on another
machine are not established by these tests.
