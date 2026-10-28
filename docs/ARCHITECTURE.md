# Runtime architecture

```text
Browser (all assets local)
    | same-origin HTTP + bearer token
Authenticated WSGI application
    |                 |                  |
SQLite state     Voice adapters     Fixed HTTP connectors
    |
One mission worker --> Local Ollama --> local GGUF model
    |
Validated tools --> confined workspace / arithmetic / optional Docker snapshot
```

The model proposes function calls. Deterministic Python code validates arguments, enforces capability boundaries, persists approvals and invokes tools. Retrieved text and model output cannot modify configuration or grant tool authority. There is no dynamic repository-code loader.

The event loop claims queued missions in SQLite transactions. A running mission persists its conversation and pending tool calls after each step. External writes stop in `waiting_approval`; an owner decision queues continuation. State changes cannot overwrite a cancellation or another terminal state. Only one worker owns a state directory through an operating-system file lock.

On startup, in-flight missions become interrupted and executing approvals become uncertain. Queued missions and unexecuted approvals remain available. This intentionally prioritizes avoiding accidental side-effect replay over automatic completion after a crash. Adapters for consequential operations should use idempotency keys and reconcile outcomes against their own source of truth.

Memory and knowledge use FTS5. This is a low-overhead lexical retrieval system, so synonyms and semantically similar phrases may be missed. Knowledge chunks carry document titles; the model receives bounded excerpts. There is no separate vector database, embedding worker or continual training process.

The worker is bounded by model-step, call-count, response-size, context-size, network-timeout and tool-specific limits. The native Docker runner uses a bounded read-only snapshot, a nonroot user, no network, dropped capabilities, and CPU/memory/process/output limits. It is an optional execution boundary, not a guarantee against kernel/runtime vulnerabilities. Native host execution is not offered to the model.

The deployment is a single-owner local appliance. Bearer-token authentication, same-origin checks, a Host allowlist, CSP, no remote browser assets and loopback defaults protect its browser surface. State is private plaintext on disk and must inherit appropriate OS permissions. LAN deployment needs HTTPS and operator-managed access controls. Multi-tenant hosting requires a different identity, authorization and storage design.

To extend capabilities, add a narrow ToolRegistry schema/validator/policy/handler or a fixed endpoint adapter. Keep read authority distinct from write authority, redact secrets at the boundary, bound all I/O, and add tests for invalid inputs, timeouts, cancellation, rejection and uncertain outcomes. Do not add a general shell tool or let prompt text select arbitrary URLs.
