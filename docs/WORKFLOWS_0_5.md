# Local workflow research and implementation

Primary sources were checked October 28, 2026. The selected patterns address concrete offline operations; they are not a benchmark ranking of every available automation platform.

| Source | Applied decision |
|---|---|
| [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents) | Use explicit code paths for predictable reports. Keep model reasoning available for synthesis and ambiguous tasks. This article is dated December 2024 and itself notes later platform changes; its patterns are not treated as current product setup instructions. |
| [Apache Airflow: Best practices](https://airflow.apache.org/docs/apache-airflow/stable/best-practices.html) | Give each invocation a stable identity, isolate outputs, validate artifacts, and avoid treating an uncertain execution as permission for a blind retry. No Airflow service is installed. |
| [Node-RED: Working with context](https://nodered.org/docs/user-guide/context) | Reuse the existing local flow runtime where needed. Its default file context caches writes for 30 seconds, so it is not used as a substitute for transactional procedure receipts. |
| [Node-RED official startup cookbook](https://github.com/node-red/cookbook.nodered.org/blob/master/basic/trigger-on-start.md) | Startup-trigger patterns are available, but owner reports are explicit runs and never auto-repeat after an interruption. No community flow code or nodes are installed from search results. |
| [FEMA continuity exercise starters](https://preptoolkit.fema.gov/web/em-toolkits/continuity-of-essential-functions) | Include a tabletop draft and after-action evidence workflow around essential functions and dependencies. A generated scenario is clearly distinct from an executed exercise. |
| [SQLite online backup](https://www.sqlite.org/backup.html) | Keep live database snapshots as deliberate backup operations; report exports are not represented as restorable private-data backups. |

Fixed local procedures take snapshots of actual local records, generate named reports, record artifact hashes and elapsed wall time, and make **zero model calls**. They require no new orchestration server, recurring license or API. This is an implementation fact tested by rejecting model calls, not a measured energy or monetary saving. The UI shows the exact fixed outputs before an owner runs one. Inputs and outputs remain private local artifacts.

The procedure collection covers readiness, stock rotation, daily power budgets, rebuilding dependencies, reference integrity and owner-book text presence, business records, knowledge metadata, and the offline operations checklist. Empty data is reported explicitly; it is not replaced with fictional inventories or financial facts. Each run uses its own output directory. Completed request IDs return the original receipt. Reusing an ID for another procedure is refused; an interrupted or failed request remains stopped for review instead of being silently replayed.

Three additional optional AI workflows are seeded once without overwriting owner workflows: Preparedness evidence review, Continuity tabletop exercise, and Business operations handoff. Each has real output-file checks, bounded evidence requests and explicit action limits. These use the selected local model and can take longer than the fixed reports; no token, energy or latency savings are asserted for them.

Each model step receives authoritative server UTC for dating generated artifacts. Generation time remains distinct from an underlying source's observation/publication date. Templates forbid guessed dates and facts about records the model did not read. File predicates verify the requested artifacts and their declared formats; they do not certify every factual statement in generated prose. Owner review remains necessary.

These implementations adapt documented patterns in original code. They do not import arbitrary online workflows, embed credentials, install unreviewed nodes, connect accounts, contact people, buy supplies, certify health/safety decisions or operate physical utilities.
