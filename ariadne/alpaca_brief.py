"""Pure presentation of the prepared data-only connection; never fetch or trade."""


def markdown(options):
    if type(options) is not dict:
        return []
    theta = options.get("theta_feed_connection")
    theta_lines = []
    if (type(theta) is dict and type(theta.get("schema")) is int and theta["schema"] == 1
            and theta.get("source_id") == "theta_terminal_v3"
            and theta.get("status") == "prepared_not_activated"
            and theta.get("live_feed_qualified") is False
            and theta.get("paper_submission_ready") is False):
        theta_lines = ["", "## ThetaData local-terminal qualification", "",
            "- Prepared fixed-loopback quote connection only. No authenticated live feed, paper fill or installed Terminal is inferred.",
            "- Free access is historical EOD, not a five-second live options feed. Paid access, stock evidence and actual timestamp semantics need qualification.",
            "- Terminal responses remain locally unattested. Missing event timezone, displayed depth or dated liquidity evidence blocks admission; receipt time never substitutes for an event.",
            "- Existing capital limits, no top-ups and holds remain unchanged. No Terminal launch, subscription purchase or automatic polling occurs.",
            "- Guide: docs/THETA_PAPER_DATA.md. Saved admission checks are not ledger executions or measured trading profit."]
    value = options.get("data_feed_connection")
    if (type(value) is not dict or type(value.get("schema")) is not int or value["schema"] != 1
            or value.get("source_id") != "alpaca_opra_v1"
            or value.get("status") != "prepared_not_activated"
            or value.get("live_feed_qualified") is not False
            or value.get("paper_submission_ready") is not False):
        return theta_lines
    return theta_lines + ["", "## Alternative market data: Alpaca qualification only", "",
        "- Prepared data-only OPRA/SIP connection; no verified live capture, ledger migration or Alpaca paper fills are inferred.",
        "- Requires private owner API credentials and actual OPRA/SIP entitlement. Initial paper signup is email-only; later paid-access phone requirements remain unverified.",
        "- Quotes retain actual NBBO-message timestamps and displayed sizes. Indicative/delayed data and missing dated liquidity evidence cannot qualify a new entry.",
        "- The existing account, capital limits, no-top-up rule and holds remain unchanged. No automatic Alpaca polling or real-order interface is enabled.",
        "- Guide: docs/ALPACA_PAPER_DATA.md. Qualification reports are admission diagnostics, not daily trading profit or executions."]
