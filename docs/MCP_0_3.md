# Local MCP bridge (0.3)

The optional bridge connects Ariadne to **owner-configured loopback HTTP services**. It does not install/start MCP servers, discover tools for the model, browse server resources, load remote skills, or provide model access to connection configuration. FastMCP is imported only in a fresh bounded worker when an owner discovers tools or a registered tool is called. There is no idle model call or background discovery loop.

This implementation uses the official [FastMCP slim client extra](https://github.com/PrefectHQ/fastmcp/blob/v4.1.4/fastmcp_slim/pyproject.toml), pinned to **4.1.4**, and the SDK's explicit [HTTP transport and client protocol modes](https://gofastmcp.com/clients/client#protocol-negotiation). Server/apps/tasks/AI-provider extras are not requested. Some server-oriented dependencies are still mandatory transitive SDK dependencies. The lock and retained wheelhouse were produced from official PyPI wheels on 2026-10-28 for **CPython 3.13, Windows x64**. Another Python/platform needs its own reviewed lock; this file does not claim a portable Linux dependency bundle.

## Owner setup

All commands run from the Yikes project directory. The optional dependencies are provisioned separately from application startup:

```powershell
.\.venv\Scripts\python.exe scripts/provision_mcp.py download
.\.venv\Scripts\python.exe scripts/provision_mcp.py install
.\.venv\Scripts\python.exe scripts/provision_mcp.py status
```

`download` uses the official PyPI index, binary wheels only, and `requirements-mcp.txt` with `--require-hashes`. `install` uses only `runtime/downloads/mcp`, without an index. It modifies the project's `.venv`, not the OS/global Python. Retain the lock and wheelhouse for offline repair. The script pins the local temporary/cache directories inside Yikes and never provisions packages at application runtime. Serialize dependency installation with other provisioning work.

Copy `config/mcp.example.json` to `data/mcp.json` and edit it as the owner. The example activates **zero tools**. A server entry has:

```json
{
  "id": "inventory",
  "url": "http://127.0.0.1:8765/mcp",
  "mode": "modern",
  "headers": {
    "Authorization": {
      "env": "ARIADNE_MCP_INVENTORY_TOKEN",
      "prefix": "Bearer "
    }
  },
  "tools": []
}
```

Set the credential in the application process environment through the owner's normal local launch configuration. Dedicated `ARIADNE_MCP_...` environment names are required. Only `Authorization` and `X-Api-Key` headers are supported, with an empty or `Bearer ` prefix. No literal credential, arbitrary inherited header, OAuth browser flow, credential discovery, or credential-writing API is accepted. Omit `headers` for a synthetic unauthenticated fixture. Never paste credentials into chat, tool arguments, schema defaults, or descriptions.

`modern` explicitly pins **2026-07-28**. `legacy` explicitly selects the initialize/session era and requires negotiation of **2025-11-25**. Both use Streamable HTTP. There is no automatic downgrade, stdio command inference, SSE-only transport, or model-supplied server URL. Only literal `http://127.0.0.1:PORT/path` and `http://[::1]:PORT/path` endpoints are accepted. Hostnames, HTTPS, remote/LAN IPs, URL credentials, query strings, fragments, and redirects are rejected. External HTTPS integration remains a separate TLS/authority qualification scope.

Discover metadata, review the saved file, and register each selected tool explicitly:

```powershell
.\.venv\Scripts\python.exe scripts/provision_mcp.py discover --server inventory
# Read data/mcp-discovery/inventory.json and use the exact SHA256 printed above.
.\.venv\Scripts\python.exe scripts/provision_mcp.py register --server inventory --tool stock_count --snapshot-sha256 REVIEWED_SHA256 --read-only
# For a tool with side effects, use --approval instead of --read-only.
```

Discovery only writes bounded review evidence under `data/mcp-discovery`; it activates nothing. Registration requires the exact file hash, unchanged configured server identity, a supported input/output schema, and an explicit owner policy. The registered metadata and owner policy are stored in `data/mcp.json`. Remove old tool registrations before accepting a changed server identity through the CLI. Owner operations hold a shared OS mutation lock (`data/mcp-config.lock`) across discovery persistence or config read/modify/write; a concurrent owner action fails instead of overwriting another action. The lock file remains in place. The CLI's `--data-dir` must remain inside Yikes. E: and paths containing symlinks/reparse points are rejected; configuration/snapshot files also reject hard links. File operations hold directory handles during reads/atomic writes.

## Authority and change handling

Registered names have the form `mcp_<server>_<toolslug>_<digest>`. The digest binds endpoint/mode, server identity, tool name/description/schema/annotations, credential-reference names, and owner read/write policy. A configuration/metadata change creates a different local tool name. Approval fingerprint material additionally HMAC-binds current credential values with the owner's server token, without persisting a guessable plain credential hash. The engine must enforce `policy()` and persisted approval before calling a write tool; invoking `execute()` directly is a trusted application API, not an authorization mechanism by itself.

Every call opens a new fixed worker, re-reads current server identity and the complete tool listing, and compares the chosen tool's full metadata to its approved snapshot **before** sending `tools/call`. Changed descriptions, schemas, annotations, identity, or missing tools reject the call. Remote `readOnlyHint`/annotations never grant automatic policy: only the owner's `read_only` declaration does. An owner must trust the actual server implementation; a dishonest server can perform side effects while claiming to be a read tool. Comparing metadata does not attest implementation bytes or close a malicious server's change-between-list-and-call race.

The client disables response caching, proxy/environment transport configuration, redirects, compression, SDK extensions, and interactive callbacks. Resources, roots, sampling, elicitation, remote skill instructions, binary/media output, and background-task continuations are not exposed as product capabilities. The worker uses raw `call_tool_mcp` output rather than the higher-level schema-to-Python result conversion. Text/structured JSON is treated as untrusted data and never executed.

Known credential values, their common URL/base64 encodings, and credential-like JSON fields are redacted from results. Network/protocol/parser errors return fixed messages, with no raw exception, header, URL, or traceback. This is defense in depth, not a proof against arbitrary deliberate encoding by a compromised credential-bearing service. Such a service must remain within the owner's trust boundary.

For writes, a tool error after submission, worker loss, deadline, or cancellation can leave the external result unknown. `execute()` returns `ok:false, uncertain:true` when the write may have reached the service. The engine must stop automatic retries, preserve the approval/result evidence, and require reconciliation before another write. Metadata rejection before submission returns `uncertain:false`. There is no exactly-once claim.

## Explicit limits

| Boundary | Current limit |
| --- | --- |
| Config/snapshot/worker output | 256 KiB each |
| Configured servers / selected tools | 4 servers / 16 selected tools total |
| Discovered tools | 64; paginated/partial listings reject |
| Per-tool schema / combined exposed schemas | 12,000 bytes / 64,000 bytes |
| Input schema nesting / object properties | 6 levels / 64 properties per object |
| Argument JSON / strings / arrays | 24,000 bytes / 12,000 characters / 256 items |
| Tool output | 48,000 bytes; at most 32 text blocks |
| Aggregate HTTP body bytes per worker | 512 KiB, including SSE comments/events |
| End-to-end worker deadline / CPU | 20 seconds / 10 CPU seconds |
| Worker memory ceiling | 384 MiB |

The supported JSON-Schema subset has explicit primitive/object/array types, nullable single types, properties/required, closed objects, bounded arrays/strings/numbers, scalar enums, and descriptive/default metadata. Unknown keywords, references/definitions, regexes, formats, unions/combinators, open-ended properties, and unsupported schemas reject registration. No schema code generation or remote reference fetching occurs. Object arguments reject unknown fields even where the schema omits `additionalProperties`. Finite JSON values, duplicate-key rejection, nesting and element limits apply independently of schema constraints.

The shared fixed-worker helper applies OS memory/CPU/process limits before SDK imports, bounds captured output and wall time, strips inherited owner credentials/proxies/Python hooks, and supports cancellation. These limits contain resources; they are **not an OS filesystem/network sandbox**. Two worker slots are shared with other optional capabilities; per-worker memory ceilings are not a measured aggregate appliance RAM requirement. Profile the deployed process/container budget before enabling concurrent heavy capabilities. A slow SSE drip cannot extend the parent deadline, and a large stream cannot exceed its aggregate byte budget indefinitely.

## Qualification evidence and remaining scope

`tests/test_mcp_tools.py` uses only temporary files inside Yikes and synthetic loopback HTTP servers. Cases cover modern and legacy JSON **and successful SSE** calls; owner-only policy; schema/argument rejection; metadata/identity mutation; credential binding/redaction (including numeric JSON echoes); redirect, pagination and count limits; output limits; SSE drip deadlines; no automatic retry; cancellation; and hash-bound owner discovery/registration. It also checks combined catalog limits before commit, internal SDK-extension disablement, cross-process mutation coordination, held-file identity before lock writes, and Windows temp-directory replacement denial during a real child. Link cases depend on host privileges and are reported as skips when unavailable.

No external MCP server, account, business system, or device has been activated by this implementation. It does not implement remote TLS/OAuth, automatic server installation, server implementation attestation, all JSON-Schema dialects, resumable/background jobs, or browser authority. Runtime readiness is reported separately from selected-tool count. A package version check and hash-locked provisioning do not constitute a security audit of the dependency tree or remote service.
