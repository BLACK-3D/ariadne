# Offline agents and verified changes — 0.10

Ariadne 0.10 adds three local-only specialists, six checked workflow starters and deterministic comparison of saved routine reports. They reuse the existing single worker and local model, with no additional resident model service, subscription or idle language-model calls. AI workflows consume local inference; fixed reports and structural comparisons make zero model calls. No energy or speed savings have been measured for these changes.

## Local-only specialists

Select **Offline operations**, **Offline research** or **Offline software** when assigning a mission. Their fixed tool scopes exclude public web search/fetch, browser automation, local HTTP flows, integrations and dynamic MCP tools, even when the owner's shared Internet switch is enabled. This is an application tool restriction, not OS process isolation. Existing tool approvals, pause, cancellation and step limits still apply. The other 16 profiles retain their existing owner-controlled connectivity.

The new starters cover verified routine changes, household continuity gaps, a business restart checklist, an offline reference evidence map, a knowledge gap ledger and a software delivery plan. They inspect actual local evidence and record unavailable sources explicitly. A draft, a file check and an implemented physical or business action remain different outcomes. Existing owner workflows are preserved. New starters seed once, subject to the 100-workflow limit.

## A new checked output folder per run

In a workflow editor, enable **New checked output folder each run**. Put the literal `{{run_dir}}` in the instructions and start every check path with `{{run_dir}}/`, for example:

```json
{"kind":"json_valid","path":"{{run_dir}}/evidence.json"}
```

At enqueue, Ariadne substitutes `missions/<invocation UUID>` in the prompt and check paths. It expands no other template language or environment variable and never changes literal expected check values. Each invocation, including a schedule invocation, receives its own path. A prior invocation's artifact therefore cannot satisfy the new check. This does not confine every model write to that directory or prevent it from reading other allowed workspace files. Checks establish selected file predicates, not overall factual quality.

The saved workflow keeps its placeholders. A workflow-backed schedule freezes instructions, checks, specialist and output mode when created; later workflow edits do not change it. Older workflows default to the original shared-path behavior. Saving a run as a workflow restores placeholders only for that run's recorded output prefix.

## Compare verified routine reports

Under **Workflows → Offline routines**, open a completed invocation and choose **Compare prior run**. The default baseline is the most recent older completed invocation of the same routine with the same ordered procedure IDs. Renames, interval changes and revision numbers do not alter that compatibility rule. An explicit baseline must be distinct, older, completed and compatible.

Both invocations are reverified using their recorded artifact hashes. An integrity failure uses the existing recurrence hold; a comparison never repairs or overwrites evidence. Results identify exact parent invocation IDs and child JSON paths, bytes and SHA256 hashes. They compare recorded JSON values, including exact numeric types and array positions. They do not establish current stock, physical work, source freshness or safety. Only valid generation dates at the documented exact metadata paths count as timestamp metadata; expiry, schema and arbitrary date fields are substantive.

States distinguish a first run, unavailable current evidence, incompatible inputs, a complete comparison and a partial comparison. Limits are 4 MB per primary report, 20,000 visited nodes, 100 observed differences and 12,000 UTF-8 bytes per API result. Omitted previews, unvisited values, partial source reports and lower-bound counts are explicit. The model receives a further bounded structured view; previews may be omitted without reducing observed counts. No new reports, network requests or model calls occur inside comparison itself. AI synthesis of the result uses the chosen model.

The authenticated endpoint is `GET /api/routines/runs/<current-id>/changes`, optionally with one `baseline=<prior-id>` query parameter. The local `routine_read` tool supports `status`, `latest` and `changes`; only `changes` accepts optional `current_id` and `baseline_id`. A recorded `latest` receipt alone is not a fresh hash check. Comparisons are on demand, with no new browser polling.

## Complete portable package

`scripts/export_offline.py` packages the current source, cached engines, wheels, offline libraries and pinned public models. `config/public-models.json` identifies the three accepted public Qwen tags and exact required manifest/blob identities. Unknown private model tags and metadata are excluded. Missing or modified pinned assets fail closed; every exported file receives a SHA256 entry in `BOX-MANIFEST.json`. The ZIP and adjacent checksum/receipt are separate from GitHub source.

For a new box, extract the full ZIP into an allowed local folder and double-click **Start Ariadne.cmd**. It creates a fresh pairing token and owner state. No Internet or microphone listening is enabled by default. Keep the checksum with the ZIP. Verify it before extraction with `Get-FileHash -Algorithm SHA256`. Do not extract over an existing owner's data. Preserve existing backups before updating an installation.

The source Git repository excludes model weights, native binaries, owner databases, private documents, pairing tokens, credentials, backup archives, generated reports and qualification state. A clone needs provisioning before it can work during an outage. Windows x64 and compatible hardware/drivers are required for the bundled optional engines. Cached weather and news keep their original dates.

The retained core Python qualification manifest covers its original generated pip console launchers, which embed their build interpreter path. Ariadne and the documented offline repairs invoke the bundled interpreter and modules directly; those console launchers are not used as portable entry points. Removing owner application state is distinct from removing every build path inside a third-party binary.

Application offline policy and disabled Internet preferences do not prove host-wide network isolation: native processes can have their own networking. Full outage qualification must additionally use an actual disconnected or explicitly blocked network boundary. Local release evidence records the boundary and tested capabilities; do not infer an untested platform or capability from archive inventory alone.
