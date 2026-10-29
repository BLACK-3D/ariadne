# Business capabilities: repository scan · 29 October 2026

Checked **2026-10-29 17:23:25 UTC**. This review covers **10 canonical repositories**, their current release metadata, actual license files, maintenance metadata and relevant deployment documentation. It is a bounded, primary-source scan, not an exhaustive crawl of GitHub or a security certification. No package, repository skill, model, flow or installer was executed, and no project runtime setting changed.

The strongest next business addition is a **read-first adapter to one operational records system**. Compare ERPNext with Dolibarr, select one, and keep routine calculations and artifact generation in Ariadne's existing deterministic workers. A separate CRM or document-management stack earns its place only when it adds a missing workflow. Installing three ERPs or several workflow engines would increase recurring administration without an established capability benefit.

Ariadne already has local contacts, stock movements and balanced journals; table/analytics and office workers; optimization; OCR and provenance-aware knowledge; reviewed HTTP/MCP integration; fixed Node-RED routes; and checked zero-model routines. ERPNext preparation already exists. Those are working building blocks, not evidence that a complete external ERP or CRM has been deployed.

## Changes worth attention now

- **ERPNext 16.53.0**, released yesterday, adds STEP/CSV-based BOM design and revision comparison. That provides a concrete future CAD-to-manufacturing workflow rather than another generic agent framework. [Upstream release](https://github.com/frappe/erpnext/releases/tag/v16.53.0)
- **Twenty 2.52.0** combines a current CRM with versioned app objects/views/workflows/agents, but its code has AGPL, MIT and commercial surfaces. Review the actual license rather than a badge before packaging it. [Repository](https://github.com/twentyhq/twenty), [license](https://github.com/twentyhq/twenty/blob/main/LICENSE)
- **Activepieces 0.92.3-hotfix.2** fixes an unauthenticated webhook-triggered crash. Its MCP-capable pieces are useful research material; pinned patches, selected Community features and Ariadne's own approval rules still matter. [Hotfix](https://github.com/activepieces/activepieces/releases/tag/0.92.3-hotfix.2)
- **Windmill's prebuilt Community binaries are not equivalent to its non-Enterprise AGPL source build.** The current license places additional limits on wrapping, selling and product embedding of those binaries. n8n and Invoice Ninja also need explicit distribution-term selection for a reusable appliance. [Windmill terms](https://github.com/windmill-labs/windmill/blob/main/LICENSE), [n8n terms](https://github.com/n8n-io/n8n/blob/master/LICENSE.md), [Invoice Ninja terms](https://github.com/invoiceninja/invoiceninja/blob/v5-stable/LICENSE)

## Coverage and observed versions

GitHub REST metadata was read directly for all ten repository identities and `releases/latest`. All were non-archived and enabled when checked. Nine reported a push on 29 October; LightRAG reported 27 October. A push timestamp is activity evidence, not proof of a reviewed release or freedom from vulnerabilities. License classification below comes from actual upstream files, including cases where GitHub's detected license field said `NOASSERTION`.

| Candidate | Observed version / UTC publication | Decision for Ariadne |
|---|---|---|
| [ERPNext](https://github.com/frappe/erpnext) | v16.53.0 · 2026-10-28 | qualify adapter |
| [Dolibarr](https://github.com/Dolibarr/dolibarr) | 24.0.1 · 2026-09-07 | compare erp alternative |
| [Odoo Community](https://github.com/odoo/odoo) | 20.0 (source series, FINAL) · release date unverified | defer unless selected |
| [Twenty](https://github.com/twentyhq/twenty) | twenty/v2.52.0 · 2026-10-27 | qualify crm if needed |
| [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) | v3.3.4 · 2026-10-22 | qualify document lifecycle |
| [Activepieces](https://github.com/activepieces/activepieces) | 0.92.3-hotfix.2 · 2026-10-28 | qualify selected pieces not second default engine |
| [Invoice Ninja](https://github.com/invoiceninja/invoiceninja) | v5.13.50 · 2026-10-21 | optional owner service not default bundle |
| [n8n](https://github.com/n8n-io/n8n) | n8n@2.45.6 · 2026-10-29 | defer default distribution |
| [Windmill](https://github.com/windmill-labs/windmill) | v1.853.1 · 2026-10-29 | source only evaluation or owner managed service |
| [LightRAG](https://github.com/HKUDS/LightRAG) | v1.5.7 · 2026-09-02 | benchmark only |

Odoo's `releases/latest` endpoint returned 404. Its source `release.py` identifies the 20.0 series as FINAL; no precise release date is inferred from that. The [machine-readable inventory](repo-scan-business-2026-10-29.json) retains timestamps, endpoint URLs, source-file Git blob IDs and the distinctions between evidence and engineering estimates.

## 1. ERPNext

**Observed:** v16.53.0, published 2026-10-28T20:44:12Z. [Release](https://github.com/frappe/erpnext/releases/tag/v16.53.0) The repository reported its most recent push at 2026-10-29T17:18:40Z. [Repository metadata](https://api.github.com/repos/frappe/erpnext)

One operational system for sales, purchasing, inventory, manufacturing and accounting. The observed release adds a BOM design workspace for STEP/CSV imports and design revision comparison: a useful bridge from Ariadne's CAD artifacts into manufacturing planning. [Upstream description](https://github.com/frappe/erpnext/blob/develop/README.md)

**Deployment and disconnected use:** Linux/WSL/VM or container stack with Frappe, database and workers; not a native Windows plug-in. Local operations after complete provisioning; payment gateways, remote email and fresh exchange rates still require their services. [Deployment evidence](https://github.com/frappe/erpnext/blob/develop/README.md)

**License:** GPL-3.0; upstream and dependency notices retained. Observed upstream license.txt header. Framework and each added app keep their own terms. [Actual license file](https://github.com/frappe/erpnext/blob/develop/license.txt)

**Fit and proposed adapter:** Ariadne already has balanced local business records, fixed reports and an ERPNext preparation route. Preparation is not a running ERP installation. Start with read-only customers/items/stock and dated document IDs; then approval-bound draft quotations and purchase requests. Posting, payment, deletion and delivery need separate explicit authority. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: appreciable always-on database/queue/worker maintenance; no LLM required for routine ERP reads. Choose one ERP, not three. Owner company chart of accounts, test company and scoped API credentials; separate database/files/encryption-key backup and restore plan. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- Ariadne's SQLite-only backup does not back up an external ERP database.
- Versioned schema migrations and apps need pinned compatible versions.
- Do not treat generated bookkeeping or tax reports as independently certified.

## 2. Dolibarr

**Observed:** 24.0.1, published 2026-09-07T12:58:22Z. [Release](https://github.com/Dolibarr/dolibarr/releases/tag/24.0.1) The repository reported its most recent push at 2026-10-29T16:35:37Z. [Repository metadata](https://api.github.com/repos/Dolibarr/dolibarr)

Small-business CRM, quotes, invoices, orders, stock and manufacturing modules. Windows packaging is a practical alternative when the owner values a conventional business application over a larger Frappe stack; actual resource advantage remains unmeasured. [Upstream description](https://github.com/Dolibarr/dolibarr/blob/develop/README.md)

**Deployment and disconnected use:** PHP web server and MariaDB/MySQL/PostgreSQL; official README offers DoliWamp for Windows and Docker alternatives. LAN/local records after PHP/server/database provisioning; external bank, mail and payment connectors remain connected functions. [Deployment evidence](https://github.com/Dolibarr/dolibarr/blob/develop/README.md)

**License:** GPL-3.0-or-later; dependency licenses separate. README explicitly permits GPL version 3 or later; COPYING observed. [Actual license file](https://github.com/Dolibarr/dolibarr/blob/develop/COPYING)

**Fit and proposed adapter:** Overlaps the proposed ERPNext system and Ariadne's lightweight business records. Read-only stock, counterparties and outstanding-document summaries first; validate API permissions before separately approved draft invoice creation. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: additional web/database maintenance. Benchmark against ERPNext using the same invoicing/stock tasks before selecting. Test company, module choices, owner-created credentials and a coordinated database/documents backup. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- Do not install both ERP alternatives without a concrete need.
- Plug-in/module licenses and security state differ from core.
- A recent push is not a security assessment.

## 3. Odoo Community

**Observed:** 20.0 (source series, FINAL); publication date unknown. [Version source](https://github.com/odoo/odoo/blob/20.0/odoo/release.py) The repository reported its most recent push at 2026-10-29T17:22:34Z. [Repository metadata](https://api.github.com/repos/odoo/odoo)

Modular ERP/CRM and sales/stock/project workflows. Broad module ecosystem and new20.0 source series; select for a specific required module rather than marketing breadth. [Upstream description](https://github.com/odoo/odoo/blob/20.0/README.md)

**Deployment and disconnected use:** Observed release.py requires Python 3.12–3.14 and PostgreSQL ≥16. Source exposes a Windows service name; exact Windows deployment not qualified here. Self-hosted core records can be local; external services, hosted AI and payment/email functionality are not disconnected capabilities. [Deployment evidence](https://github.com/odoo/odoo/blob/20.0/README.md)

**License:** Community core LGPL-3.0; Enterprise and add-ons have separate terms. Actual LICENSE file inspected; GitHub's detected NOASSERTION is not a substitute for the file. Community repository is not a grant to Enterprise features. [Actual license file](https://github.com/odoo/odoo/blob/20.0/LICENSE)

**Fit and proposed adapter:** Substantial ERPNext/Dolibarr overlap; do not add as a third always-on business platform. A version-pinned service API wrapper with test records and owner-selected fields; no direct model SQL, application installation or generic administrator access. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: database, module upgrades and migrations add operational work; some desired features or add-ons can require commercial licensing. Chosen Community modules, scoped service user and separate PostgreSQL/filestore recovery plan. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- GitHub releases/latest returned404: source 20.0 is observed, but no precise release timestamp was established.
- Not every capability listed by Odoo marketing belongs to Community.
- Source/dependency support on this specific Windows box remains untested.

## 4. Twenty

**Observed:** twenty/v2.52.0, published 2026-10-27T16:54:00Z. [Release](https://github.com/twentyhq/twenty/releases/tag/twenty/v2.52.0) The repository reported its most recent push at 2026-10-29T17:21:34Z. [Repository metadata](https://api.github.com/repos/twentyhq/twenty)

Structured CRM objects, opportunity pipelines and reviewable CRM app extensions. Current README presents objects, views, workflows and agents as versioned application code, plus official app skills. The release includes changed application-account authorization and security dependency updates. [Upstream description](https://github.com/twentyhq/twenty/blob/main/README.md)

**Deployment and disconnected use:** Docker Compose server/worker, PostgreSQL and Redis. Upstream documents at least 2 GB RAM; that is a publisher minimum, not measured full-stack consumption. Local CRM objects and views after provisioning; provider account authorization, email/calendar sync and cloud AI need external services. [Deployment evidence](https://docs.twenty.com/developers/self-host/capabilities/docker-compose)

**License:** Mostly AGPL-3.0 with Twenty Application Exception; selected SDK/UI/app packages MIT; Enterprise-marked code commercial. LICENSE qualifications and the additional application-interface permission were read; this is not a claim every repository file is AGPL or MIT. [Actual license file](https://github.com/twentyhq/twenty/blob/main/LICENSE)

**Fit and proposed adapter:** Adds durable deal/company/contact pipelines beyond Ariadne's simpler contacts; may duplicate the selected ERP's CRM. Read selected company/deal objects through fixed REST/GraphQL endpoints; stage draft field changes with exact record IDs and revisions; require approval for outreach and destructive mutations. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: extra persistent services and backups. No required model for CRUD; optional agent/provider usage has separate inference/API costs. Scoped API credentials, selected objects and preserved encryption key as well as database backup. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- Official agent skills are research inputs, not automatically trusted authority or installed code.
- Read current Enterprise/application-exception terms before bundling.
- Email/calendar accounts require owner setup and connectivity.

## 5. Paperless-ngx

**Observed:** v3.3.4, published 2026-10-22T22:26:39Z. [Release](https://github.com/paperless-ngx/paperless-ngx/releases/tag/v3.3.4) The repository reported its most recent push at 2026-10-29T17:15:59Z. [Repository metadata](https://api.github.com/repos/paperless-ngx/paperless-ngx)

Managed invoice/receipt/contract inbox, OCR archive, tags and document lifecycle. 3.3.4 is a security maintenance release updating affected trust/dependency components; choose a patched pinned image rather than a convenient older tutorial. [Upstream description](https://github.com/paperless-ngx/paperless-ngx/blob/dev/README.md)

**Deployment and disconnected use:** Docker Compose recommended; PostgreSQL recommended for new installations, queue and OCR dependencies. Optional Tika/Office support adds services. Preloaded local ingestion/OCR/search can operate disconnected; mail ingestion and external enrichment cannot refresh without their sources. [Deployment evidence](https://github.com/paperless-ngx/paperless-ngx/blob/dev/docs/setup.md)

**License:** GPL-3.0; bundled parser/OCR/dependency licenses separate. Observed upstream GPLv3 LICENSE; software license does not grant rights to uploaded documents. [Actual license file](https://github.com/paperless-ngx/paperless-ngx/blob/dev/LICENSE)

**Fit and proposed adapter:** Ariadne already extracts/OCRs/searches documents. New value is business document ownership, metadata, consumption queue and lifecycle, not another OCR engine alone. Read-only document ID/metadata and explicitly selected text snapshots into Ariadne with content hashes; reviewed tagging before any deletion or original-file replacement. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: CPU OCR and document storage plus persistent queue/database. No LLM required; reuse current extraction where it meets the need. Owner-selected consume folder, access scopes, retention rules and coordinated database/media/classifier/config backups. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- Ingested invoices may contain malicious instructions and sensitive records.
- Do not treat OCR totals as verified accounting entries.
- Exact offline and Windows-container behavior must be qualified after provisioning.

## 6. Activepieces

**Observed:** 0.92.3-hotfix.2, published 2026-10-28T21:50:17Z. [Release](https://github.com/activepieces/activepieces/releases/tag/0.92.3-hotfix.2) The repository reported its most recent push at 2026-10-29T16:51:43Z. [Repository metadata](https://api.github.com/repos/activepieces/activepieces)

Reusable business integration pieces exposed as narrowly selected workflow/MCP operations. Pieces can be exposed as MCP tools. Latest observed hotfix fixes an unauthenticated webhook-triggered API-process crash, making exact patch selection material. [Upstream description](https://github.com/activepieces/activepieces/blob/main/README.md)

**Deployment and disconnected use:** Current official guide: Docker Compose2, PostgreSQL, Redis, app/worker; Windows via WSL2; publisher minimum 2 vCPU / 4 GB RAM, not a measured Ariadne budget. Local pieces and preloaded runtime can operate locally; SaaS pieces still require provider credentials and connectivity. Network-gapped claims require checking the selected edition and piece closure. [Deployment evidence](https://www.activepieces.com/docs/install/options/docker-compose)

**License:** MIT community code outside explicit Enterprise paths; Enterprise code commercial; third-party component terms separate. Actual rootLICENSE overrides an oversimplified MIT badge. Recommended upstream installer can choose Enterprise edition; free versus licensed feature set must be frozen. [Actual license file](https://github.com/activepieces/activepieces/blob/main/LICENSE)

**Fit and proposed adapter:** Ariadne already has fixed Node-RED routes, connectors, schedules and checked routines. Reuse specific pieces only when they add a missing integration. Freeze a small tool/schema allowlist; keep provider secrets out of prompts; reuse Ariadne approvals and no-replay receipts instead of inheriting auto-retries blindly. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: four service roles, database/queue storage and upgrade work. Prefer existing Node-RED/fixed adapters unless a needed piece earns the overhead. Reviewed Community-only build or clearly selected edition, encryption keys, scoped provider credentials and database-volume backups. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- Do not pipe the upstream install script into a shell as part of this research.
- Upstream defaults can launch multiple workers and collide with Ariadne port8080.
- Backing up only the install folder omits PostgreSQL Docker-volume state.

## 7. Invoice Ninja

**Observed:** v5.13.50, published 2026-10-21T22:46:24Z. [Release](https://github.com/invoiceninja/invoiceninja/releases/tag/v5.13.50) The repository reported its most recent push at 2026-10-29T03:03:02Z. [Repository metadata](https://api.github.com/repos/invoiceninja/invoiceninja)

Quotes, invoicing, time/expense/project records and client billing portal. Current release has scheduler/API fixes; mature API could support draft quote/invoice production. [Upstream description](https://github.com/invoiceninja/invoiceninja/blob/v5-stable/README.md)

**Deployment and disconnected use:** Laravel/PHP self-hosted server/VM or Docker and database; Windows desktop client is not the complete server stack. Local draft billing records/PDFs after provisioning; actual payment processing, delivery and remote client access require their networks/services. [Deployment evidence](https://github.com/invoiceninja/invoiceninja/blob/v5-stable/README.md)

**License:** Elastic License2.0, source-available; not a permissive/OSI-open-source default. ELv2 restricts providing substantial functionality as a hosted/managed service and removing protected notices/license mechanisms. Additional commercial embedding/branding questions need explicit terms. [Actual license file](https://github.com/invoiceninja/invoiceninja/blob/v5-stable/LICENSE)

**Fit and proposed adapter:** Invoicing may already belong to the chosen ERP; this is a narrower alternative, not a reason for two ledgers. Owner-managed optional endpoint for read-only invoices and approval-bound draft quotes. Sending, charging cards, refunds and invoice finalization remain distinct actions. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: PHP/database/queue administration; optional branding/commercial terms and payment-provider fees are separate from model cost. Owner company/tax settings, scoped API token, test customer and preserved application encryption secret. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- Do not label the whole app MIT/open-source because a client component has different terms.
- No hosted resale/wrapping assumption for an appliance used by anyone.
- Exact offline PDF renderer closure still needs qualification.

## 8. n8n

**Observed:** n8n@2.45.6, published 2026-10-29T08:30:41Z. [Release](https://github.com/n8n-io/n8n/releases/tag/n8n%402.45.6) The repository reported its most recent push at 2026-10-29T17:21:51Z. [Repository metadata](https://api.github.com/repos/n8n-io/n8n)

Owner-operated business workflow canvas and many service integrations. Latest observed patch is today; extensive AI/workflow integrations are useful discovery material, but breadth does not overcome license or maintenance constraints. [Upstream description](https://github.com/n8n-io/n8n/blob/master/README.md)

**Deployment and disconnected use:** Docker or self-hosted Node deployment, persistent data and optional scaled workers/database; exact selected architecture must be pinned. Locally provisioned engine and local nodes can run disconnected; provider APIs, live SaaS triggers and external AI are connected dependencies. [Deployment evidence](https://github.com/n8n-io/n8n/blob/master/README.md)

**License:** Sustainable Use License; Enterprise-marked files separately licensed. Current license permits own internal business or non-commercial/personal use; distribution/provision to others is restricted to free non-commercial purposes under those terms. Not a blanket permissive appliance redistribution license. [Actual license file](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)

**Fit and proposed adapter:** Duplicates Ariadne routines/schedules plus existing Node-RED and connector administration. If owner selects it separately, expose specific reviewed read operations and bounded commands through fixed endpoints; keep Ariadne approval as the authority boundary. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: another service, secret store and workflow execution history; optional Enterprise/API costs. No measured advantage over existing routines established. Chosen licensing arrangement, provider credentials, encryption-key backup and reviewed node supply chain. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- Many integrations and AI nodes need Internet/API accounts.
- Automatic retries can duplicate consequential external actions.
- Community node code is not implicitly trusted by repository popularity.

## 9. Windmill

**Observed:** v1.853.1, published 2026-10-29T12:03:46Z. [Release](https://github.com/windmill-labs/windmill/releases/tag/v1.853.1) The repository reported its most recent push at 2026-10-29T17:21:13Z. [Repository metadata](https://api.github.com/repos/windmill-labs/windmill)

Versioned internal scripts, forms, API jobs and reviewed code workflows. Current docs describe AI building in workspace forks with review/promotion and separated workers; latest patch includes MCP runnable fixes. [Upstream description](https://github.com/windmill-labs/windmill/blob/main/README.md)

**Deployment and disconnected use:** Server, workers and persistent database; explicit language/package cache and isolated worker boundaries required. Cached code, language runtimes, packages and local targets can be local; Git sync, remote packages, SaaS and hosted AI remain external. [Deployment evidence](https://github.com/windmill-labs/windmill/blob/main/README.md)

**License:** Non-Enterprise source generally AGPLv3; clients/OpenAPI Apache-2.0; proprietary Enterprise portions; prebuilt Community images/binaries have additional restrictions. Actual LICENSE states prebuilt Community includes non-public/proprietary material and restricts selling, wrapping, managed service and commercial embedding absent agreement. A non-Enterprise source build is a different licensing route. [Actual license file](https://github.com/windmill-labs/windmill/blob/main/LICENSE)

**Fit and proposed adapter:** A second workflow scheduler/code execution authority alongside Ariadne and existing fixed workers. Take the review-before-promotion pattern; evaluate only a preapproved typed internal job endpoint. Never grant an LLM a generic Bash/PowerShell resource by default. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: service/database/worker and multi-language cache maintenance; hosted/commercial features add separate recurring obligations. Explicit source/build or product license choice, separate worker identities, scoped resource secrets and backups. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- Do not redistribute or rebrand a downloaded Community image as unrestricted AGPL software.
- Script sandboxing still depends on worker deployment configuration.
- Adding it merely for agents duplicates existing lifecycle/approval surfaces.

## 10. LightRAG

**Observed:** v1.5.7, published 2026-09-02T07:23:55Z. [Release](https://github.com/HKUDS/LightRAG/releases/tag/v1.5.7) The repository reported its most recent push at 2026-10-27T18:08:12Z. [Repository metadata](https://api.github.com/repos/HKUDS/LightRAG)

Relationship-aware answers over a sufficiently complex business document corpus. Upstream has role-specific LLM configuration, contexts/citation support and an end-user workspace; useful isolation/evaluation ideas for business knowledge. [Upstream description](https://github.com/HKUDS/LightRAG/blob/main/README.md)

**Deployment and disconnected use:** Python/API service or Docker; official offline group excludes heavy transformers/Torch/CUDA dependencies and recommends separating expensive parsing services. Possible with local LLM/embedding/storage and complete package/tokenizer caches. Defaults or dynamically installed providers must be replaced/prefetched deliberately. [Deployment evidence](https://github.com/HKUDS/LightRAG/blob/main/docs/OfflineDeployment.md)

**License:** MIT code; selected models/dependencies/data have separate terms. Model/code licenses differ; official offline extras are not a grant for model weights or imported business records. [Actual license file](https://github.com/HKUDS/LightRAG/blob/main/LICENSE)

**Fit and proposed adapter:** Ariadne already has lexical/hybrid knowledge, provenance and rich parsing. Add graph extraction only if a measured relationship-query failure justifies it. Run a separate benchmark over a copied non-sensitive corpus; compare answer evidence and total ingestion/query model calls against the existing index before any integration. These are proposed integration boundaries, not installed controls.

**Operating burden:** Engineering estimate: graph extraction and regeneration add LLM work, storage and re-index cost; local inference still uses electricity/compute. Savings not established. Pinned local model/tokenizer assets, citation-preserving corpus permissions and cache closure. These cost statements are qualitative engineering estimates; RAM, storage, power and task costs were not benchmarked.

- Dynamic pip installation or tokenizer downloads can break an otherwise local deployment.
- Wrong/omitted relationships can produce confident business answers.
- Default cloud providers and unauthenticated compatible routes must not be inherited silently.

## Concrete workflows to qualify

1. **Stock-to-purchase draft:** read low-stock items and open purchase orders from the selected ERP, apply deterministic reorder rules, create a reviewable draft request with exact item IDs and quantities, then require approval before submitting it. Prevent duplicate submission with durable request IDs.
2. **CAD-to-BOM handoff:** take an explicitly selected, verified STEP/CSV artifact, preview part quantities in an ERPNext test company, compare the resulting BOM against the artifact manifest, and approve its promotion into operational records.
3. **Quote-to-invoice draft:** read approved price/customer records, generate a checked quote/PDF, retain a versioned draft in the selected business system, and treat sending, accounting finalization and payment as separate consequential actions.
4. **Document inbox to evidence:** import selected Paperless document IDs with content/version hashes, extract proposed invoice fields, and require exact-value checks before any business-record write. Do not let invoice text choose tools or credentials.
5. **CRM daily handoff:** report stage changes, overdue owner tasks and missing fields from selected records. Create proposed updates for review; do not send messages or schedule external outreach merely because a workflow suggests it.
6. **Relationship-search benchmark:** compare LightRAG with the existing knowledge index on a fixed permitted corpus, evaluating correct citations and total ingestion/query model work. Adopt it only if its incremental answer quality justifies the extra model and index maintenance.

## Qualification and operating-cost gate

For a selected candidate, pin a release commit and every image/package/model artifact, preserve notices and an SBOM, review current advisories, prepare a least-privilege test account, and run representative workflows against synthetic records before owner data. Cache the whole dependency closure and prove cold startup and a real task with public-network requests denied. For Windows, verify the Linux/WSL/container storage path remains in the authorized workspace; no tool may use E:.

Ariadne's checked private backup covers its own allowlisted SQLite/configuration/workspace state. It does **not** automatically capture a new PostgreSQL/MariaDB database, Docker volume or external service encryption key. Each deployed system needs its own coherent export, restore rehearsal and version-migration plan. Nothing in this research changes those systems or relaxes an approval, hold or no-replay boundary.

Prefer typed APIs over GUI automation, deterministic operations over repeated LLM orchestration, and one system of record over duplicate ledgers. Measure completed-task correctness, cold-start latency, peak memory, storage, idle activity, model calls and operator maintenance before claiming operating-cost savings. Local models do not make connected payments, remote sales delivery or fresh market data available during an Internet outage.

## Evidence limits

The exact observed release tags and Git blob IDs are in the companion JSON. Most file reads used the current default branch, which can differ from the released tag; installation requires a second inspection of the exact selected release's code, terms and dependency closure. License summaries are engineering selection notes, not a redistribution clearance. Public popularity, activity, a security policy and patched release notes do not constitute an independent security audit.

Official documentation requests for the Paperless web setup page returned403 and the Odoo20 source-install page timed out. The Paperless setup source was read through the official GitHub repository instead; Odoo runtime/version facts come from its actual source file. No missing page was treated as proof of support.

No repository was cloned or installed, no executable or model was downloaded, no credentials were requested, no business action occurred, and no services, microphone state, schedules or network/firewall settings changed. All new files are in Yikes; E: was not accessed.
