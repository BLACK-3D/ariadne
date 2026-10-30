# Workflow status reliability · 0.16.1

Windows qualification of 0.16 found a status-read failure after a four-step offline workflow had completed. Independent read-only checks confirmed all eleven artifacts and their SHA-256 identities, including the original accepted market inputs after the workspace CSV was edited. The controller still failed acceptance because its status request returned HTTP 404. Its receipt and successful cleanup remain preserved.

An isolated native probe independently reproduced a product defect. SQLite legitimately removes its `-wal` and `-shm` sidecars when the last connection closes. The routine guard previously checked existence and then inspected the file. A concurrent ordinary connection close could remove the sidecar between those two checks. Both coordinated real SQLite closes and natural worker/status concurrency reproduced the resulting missing-file response. The original failed HTTP response body was not retained, so its precise cause remains unproven; the independently reproduced defect is repaired.

The repair inspects each database file once and tolerates only a missing expected SQLite sidecar. Main-database disappearance, non-regular files, links, reparse points, multiple hard links, permissions and other I/O failures retain their failure behavior. Routine connections additionally require SQLite's existing-file mode, preventing accidental creation of an empty replacement database after inspection. Bootstrap creation keeps its explicit existing behavior. The repair neither resubmits work nor changes a completed, failed or interrupted receipt.

## Model qualification

The complete factory profile selects `qwen3.5:9b`. A separate 0.16 test deliberately selected optional `qwen3:4b`; that file-proof mission reached its 2,048-output-token limit after 86.234 seconds before executing a tool. Its terminal failure remains visible. The pinned artifact identifies Qwen3 4B Thinking 2507, whose [official model card](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507) documents thinking-only operation. Its generation template inserts a thinking prefix even though Ariadne requests `think:false`.

This is evidence about one bounded mission with that exact artifact, rather than a claim that every 4B task fails. Earlier dated verification results retain their original scope. The application keeps its existing response, context, step and time limits; it does not increase them or replay failed work automatically. The portable default is tested separately with a new request and new output path on a clean extraction.

## Release acceptance

The patch requires native regression checks for disappearing sidecars, strict missing-main behavior and preserved file safeguards; the full source suite and UI/API contracts; independent review; and a complete new portable extraction. One-click acceptance must prove the copy's own host, model cache and services, enforced process/native offline controls, all four deterministic steps, accepted-input integrity, Cori WAV synthesis, actual default-model file writes/reads, and clean shutdown preserving the owner's running host.

The complete package can be refreshed from the previously verified full 0.16 archive using the maintained source-refresh utility. That process verifies every old member, retains native bytes exactly, rejects changed runtime recipes, incorporates the new audited source and publishes a separate full ZIP. Prior failed candidates remain unchanged. Physical Wi-Fi disconnection and real broker/device actions are separate from this qualification; fixed offline reports use zero model calls.
