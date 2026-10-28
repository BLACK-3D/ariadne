# Ariadne 0.3 delivery receipt

Delivered locally on 2026-10-28 at http://127.0.0.1:8080. Public health reports
0.3.0. The port's OS process identity was checked before authenticated probes.
The owned server listener is recorded in data/ariadne.pid; no pairing key is
included in this document or the source distribution.

The production queue was idle before upgrade. A consistent private backup was
created and its ZIP integrity verified at
`backups/ariadne-before-0.3-20261028.zip`. It preserves the database, workspace,
token and relevant configuration; model assets remain separate. No restore to
another machine or physical power-loss drill was performed.

The upgraded database preserved the existing mission, seven Knowledge entries,
two selected reference folders, no schedules and the selected local Qwen3:8b
model. Autonomy's existing state was preserved. Both local speech directions
reported available. The new capability catalog showed10 ready of 12 groups:
workspace, tables, analytics, memory/knowledge, imports, PDF text, OCR, speech
input/output and workflows. MCP has a working client and owner setup interface
but zero production servers/tools were registered. Optional Docker is disabled.

## Qualification

- Frozen full release suite:153 tests in 124.815 seconds; 149 passed and 4 skipped
  because symbolic-link creation privileges were unavailable. Actual Windows
  junction, hard-link, directory replacement and held-file identity cases passed.
  Full output: QUALIFICATION_0_3.txt.
- A subsequent backup-only delta added the two new configuration files to the
  archive. All 3 targeted recovery tests passed in 0.234 seconds, including exact
  byte preservation of MCP/OCR configuration. The runtime code stayed frozen.
- The earlier full run encountered 3 OCR processor-drift subtest errors while
  identity code was still changing. That fail-closed result is preserved in
  `staging/qualification-0.3-tests-before-freeze.log`; final qualification was
  rerun after code and receipts were frozen.
- The actual local model mission and its preserved earlier failed attempt are
  documented in VERIFICATION_0_3.md. Independent review allocation, reproduced
  findings/fixes and remaining limits are in REVIEW_0_3.md and its two linked
  scope reports. Operating measurements/limits are in OPERATING_PROFILE_0_3.md.
- Staged browser OCR/MCP flows passed on a separate paused fixture server. Its
  process and synthetic MCP listener were stopped afterward. The installed
  production assets exactly matched those verified staged assets. Production
  browser readiness was checked after restart; its tab remains open.

The source export is `dist/ariadne-box-0.3.0-source.zip`. Its manifest is verified
against every included member, and excluded-state rules are checked. The final
archive hash/member count are in the adjacent delivery JSON/SHA256 receipt.
The archive contains source/locks/public manifests/docs, not private data,
virtual environments, model weights or retained wheelhouses. Preserve the
installation's runtime assets for offline repair. E: was not touched.
