# Venture Lab

Venture Lab connects private business plans and ETF research to the existing daily brief. It uses fixed local templates and recorded evidence, with no model calls, network requests, extra scheduler or paid service. The owner chooses projects, sources and tasks. At most two unstarted drafts advance per local day.

Open **All systems → Venture Lab** to inspect projects, source dates, draft progress, owner decisions and the latest artifacts. **Create today's check-in** uses the same ledger as the daily brief; another request that day verifies and returns the earlier result. It does not generate another batch. The daily brief includes the check-in in its full report and a compact pointer on its one-page view.

## What progress means

`draft_ready` means a local checklist and source excerpts were published and verified. It does not establish customer demand, sales, consent, revenue or a profitable trading strategy. A task can require owner-reported evidence before dependent drafts become eligible. Evidence is labelled as owner-reported, never independently verified. A seven-day plan cannot turn seven empty worksheets into seven completed market tests.

Plans and research stay in the private workspace. Configuration, accepted source bytes, task state, evidence and receipts are protected in the existing main SQLite database and included in canonical backups. Models cannot revise this board through ordinary workspace file writes. Sources are pinned by SHA-256; edited or missing sources hold execution until an owner explicitly registers a reviewed revision. Started task identities and their source pins cannot be silently changed; revised work needs a new task ID.

Global autonomy pause, briefing pause/disable/revision changes, Venture Lab pause and integrity holds stop advancement. Running, failed or interrupted work is never adopted or automatically repeated. Resolving a hold acknowledges the receipt; it does not replay uncertain work. The weather brief may still complete with a warning when Venture Lab is held. A completed parent brief never repeats its child check-in.

## Monthly cost and capacity scenarios

`scripts/venture_economics.py` creates a private static HTML and JSON report from explicit assumptions. It uses the bundled standard-library runtime, with no model calls, network requests, new subscriptions, scheduler or customer actions. Copy `config/examples/venture-economics.json` into the private workspace, fill in your own assumptions, then run from the installation folder:

```powershell
runtime\python\python.exe -I -B scripts\venture_economics.py business/economics-input.json business/economics-review-001
```

Both paths are relative to the workspace, normally `data/workspace`. An optional `--workspace` must identify an existing directory inside the installation. A new output folder is required each time; completed and partial output is never overwritten or adopted. Outputs are `economics.html` and `economics.json`, verified after publication. Inputs and reports remain private and are included when backing up the workspace. The calculator itself does not register a Venture Lab project, revise source pins or change the daily brief.

The example is an unvalidated service scenario. Its system budget is a planning allowance, not a measured bill. `actual_system_cost_monthly: null` uses that allowance and keeps actual cost unknown; a supplied actual replaces the allowance instead of adding it. `other_fixed_cost_monthly: null` excludes unknown additional fixed costs and marks the result incomplete. Supply all relevant monthly costs before interpreting the result as cost coverage. Payment fees and variable costs remain explicit assumptions.

Cash surplus is before owner compensation and personal tax. Economic surplus also subtracts the stated value of delivery, sales and administrative hours. Imputed sole-proprietor time is not a tax deduction or a taxable-profit calculation. No assumed investment return funds either target. Capacity uses 52/12 weeks per month; onboarding and customer acquisition must fit the nonbillable-hour assumption or be recorded as additional hours.

The report shows minimum client counts for both targets, capacity at each stated weekly-hours limit, one-client loss, and additional fixed-cost headroom. Nonpositive contribution cannot fund a positive target. Zero clients and zero revenue are valid scenarios. A scenario meeting the target does not prove customers, price acceptance, collected revenue or sustainable delivery. Use actual paid pilots and time records to test those assumptions.

Run the calculator's isolated arithmetic, validation and publication checks with:

```powershell
runtime\python\python.exe -I -B scripts\check_venture_economics.py
```

These checks use disposable fixtures inside the installation and do not open the microphone, generate model output, access production databases or contact external services.

## ETF studies

The `etf_research` tool runs three predeclared monthly research rules: a ten-month US trend filter, twelve-month US absolute momentum and US/developed-ex-US rotation with a bond fallback. These are correlated trend adaptations, not three independent sources of return or exact reproductions of published papers.

Input is a canonical JSON file in the workspace with schema `1`, an aware ISO `as_of`, currency `USD`, role-to-symbol mappings and dated positive observations. Every series declares `nav_total_return_index` with `dividend_policy: reinvested`, or `nav_price_only` with excluded/unknown dividends, and dated URL/SHA-256 provenance. The engine checks structure and consistency; a source hash alone does not authenticate financial data. Prices, distributions, splits and coverage must be validated before interpreting results. It does not fetch or repair market data.

The rules use completed-month observations and fill changes at the next available NAV close. The previous holding earns that interval first. NAV is an accounting value, not an executable exchange quote. The engine exposes supplied month endpoints, missing-month coverage and unverified exchange-calendar completeness. It uses zero-interest cash, long-only single-asset allocations, explicit per-leg costs and fixed 5/10/25/50-basis-point stress cases. Expenses already present in NAV should not be charged again. Primary costs may be specified from 0–100 basis points; the tool does not optimize them.

Reports retain a same-calendar buy-and-hold benchmark, daily drawdown, returns, volatility, Sharpe against zero, turnover, trades, losing periods and fixed retrospective folds: through 2014, 2015–2021, and 2022 onward. The last fold is a historical holdout, not an experiment registered before those market events occurred. Price-only inputs cannot establish total-return performance. All NAV studies remain execution-unqualified and cannot be promoted as proven winners. Taxes, actual fills, spreads, liquidity and future paper evidence remain separate work.

Each tool run first captures immutable input bytes in the shared 64 MiB accepted-input budget. A private write-ahead receipt reserves output space before computation. Reports have a separate 64 MiB lifetime budget, at most 1,000 entries, exclusive output paths and SHA-256 readback. Failed/interrupted reservations remain charged. The same completed request ID returns its original accepted snapshot even if the workspace source changed; replay is explicitly not fresh research. Changed arguments conflict. Partial output is never adopted.

## Owner API

All endpoints retain the application's bearer authentication and same-origin rules:

- `GET /api/venture`: passive status, accepted sources, cadence, progress and holds.
- `PUT /api/venture/config`: `{board, expected_revision, resume?}`; compare-and-swap revision required.
- `POST /api/venture/evidence`: `{evidence, expected_revision}`; evidence contains task ID, kind (`review`, `interview`, `customer`), aware observation time and note; an optional local path requires its SHA-256.
- `POST /api/venture/hold`: `{request_id, expected_revision}`; explicit reviewed hold acknowledgement.
- `POST /api/venture/run`: optional bounded `request_id`; advancement also requires the existing brief to be enabled and unpaused.
- `POST /api/operations/run`: `{tool: "etf_research", arguments: {action: "study", path, request_id, cost_bps?}}`.

There are no brokerage connections, orders, external messages, account actions or purchases in this feature. Customer outreach, paid business operation and actual trading require separate decisions and authority.
