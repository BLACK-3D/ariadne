# Current trading study window

The owner limited new trading performance studies to the **last twelve calendar months** on November 1, 2026. Apply this direction to future study planning and source acquisition. Older reports remain historical reference and must not be promoted as evidence about the current window.

The current options campaign, `scripts/options_bankroll_research.py`, enforces the window separately from the frozen strategy and paper-account rules:

- Freeze the actual registration clock once, using the New York calendar date. There is no command-line override to backdate a new study.
- The oldest included observation is the same calendar date one year earlier. February 29 clamps to February 28 in a non-leap year; this is not a fixed 365-day subtraction.
- The newest included observation is the latest completed market session after the existing 15-minute finalization allowance. This avoids treating an unfinished session as historical evidence.
- Check manifest selection, declared chunk observation bounds and **every actual option snapshot**. Reject older, mixed-outside-window or future inputs. Do not silently crop dates, repair gaps, invent quotes or turn a rejected cohort into a zero-trade result.
- Actual capture and availability timestamps may be later than the last market session, but must not be later than registration. Do not replace retrieval/publication clocks with the source date.
- Older benchmark price observations may supply causal indicator warmup only. No pre-window option trades, performance outcomes or crisis-era comparisons enter the new study's returns.
- Record the frozen window and policy hash in the attempt, registration, results and completion receipt. Preserve the existing source leases, output limits, pause checks and no-replay behavior.

For example, a study registered on **November 1, 2026** admits observation dates from **November 1, 2025 through October 30, 2026**, inclusive. The first eligible exchange session in that span is November 3, 2025. Shorter cohorts inside the allowed span remain shorter cohorts; the window does not establish twelve months of available data.

Old 2008/2009/2011/2019/2022/2024 studies and their original receipts remain immutable. Pure historical engines and verification/read paths continue to verify those records under their original contracts. The supported current options-study command performs the new admission check before evaluating any prices. Do not use legacy study commands or new request IDs to evade the owner's recent-only research direction. Any future ETF/VIX study integration must admit the scoring window before reserving a request; rejecting research after reservation can create a failure hold on the active paper account. Do not truncate the owner's accepted benchmark dataset or alter financial verification to impose a research window.

Recent data is still incomplete: KO availability labels are metadata, while the saved actual option observations cover only October 28–30, 2026, with partial chains and unknown quote-event clocks. Their acquisition order also differs from source-date order. These captures do not establish a complete chronological twelve-month options backtest, and their publication clocks must not be reordered or invented.

The [working recent-history observations and comparisons](OPTIONS_WORKING_HISTORY.md) preserve this window. A later, separately registered 125-session May–October cohort was acquired and compared; it is not twelve months of options history. Incomplete held ledgers remain unranked. Original failed acquisition attempts stay preserved.

The date policy changes no cash, funding, fees, risk limits, paper positions, microphone state or services. The existing one-time $25,000 paper bankroll and separately frozen schema6 controls remain in force. Saved reports remain available offline; obtaining new market data requires its existing authorized source controls.
