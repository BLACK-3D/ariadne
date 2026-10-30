"""Pure presentation of a dated VIX source review, never trading authority.

Packaged source metadata describes a reviewed candidate. It does not detect an
authenticated runtime connection, verify quotes, or qualify a paper ledger.
All executable-data gates remain explicitly unverified in this product version.
"""
from __future__ import annotations

from datetime import datetime, timezone


OFFICIAL_URL = "https://robinhood.com/us/en/support/articles/onboarding-an-external-agent/"
MCP_URL = "https://agent.robinhood.com/mcp/trading"
READ_TOOLS = (
    "get_option_chains", "get_option_instruments", "get_option_quotes",
    "get_option_historicals", "get_indexes", "get_index_quotes",
    "get_index_historicals",
)
_GATES = (
    ("authenticated_market_data", "Verify an existing authorized market-data connection; this dated review does not inspect runtime authentication."),
    ("actual_vix_quotes", "Observe actual VIX/VIXW contract bid/ask through documented market-data tools; a visible chain alone is insufficient."),
    ("side_sizes_quote_clocks", "Verify actual bid/ask sizes and provider quote timestamps, distinct from receipt times, last trades, volume and open interest."),
    ("exact_expiry_vx", "Supply an observed individual VX future for each exact option expiry; VIX spot, continuous futures and ETFs are not substitutes."),
    ("official_calendar", "Verify official contract identity, last trading cutoff, European exercise and AM VRO cash-settlement calendar."),
    ("shared_ledger_migration", "Qualify an atomic migration preserving the existing account, history and single shared $500 monthly allowance; add no second account or funding."),
)
_SCOPE = "Dated source-review observation only; no live Ariadne connection detection."
_ROUTE_KEYS = {"provider", "status", "checked_at", "official_url", "mcp_url", "read_tools",
               "connection_status", "observation", "observation_scope"}


def _stamp(value):
    if type(value) is not str or not 1 <= len(value) <= 64 or any(ord(char) < 32 for char in value):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def _reviewed_route(value):
    """Recognize this reviewed static candidate; never consume authority flags."""
    if type(value) is not dict or set(value) - _ROUTE_KEYS:
        return None
    required = {"provider", "status", "checked_at", "official_url", "mcp_url", "read_tools", "connection_status"}
    if not required <= set(value):
        return None
    if (value["provider"] != "Robinhood" or value["status"] != "awaiting_authenticated_verification"
            or value["official_url"] != OFFICIAL_URL or value["mcp_url"] != MCP_URL
            or value["connection_status"] not in ("not_connected", "unverified", "requires_verification")):
        return None
    names = value["read_tools"]
    if (type(names) is not list or len(names) != len(READ_TOOLS)
            or any(type(name) is not str for name in names)
            or set(names) != set(READ_TOOLS)):
        return None
    return _stamp(value["checked_at"])


def readiness(assessment):
    """Return bounded public facts from packaged metadata without side effects.

    Unknown/malformed metadata fails closed. Even a claimed connected or
    qualified source cannot pass any gate, enable fills or create funding.
    Raw observations, account details, provider responses and arbitrary URLs
    are deliberately not copied into the result.
    """
    valid = type(assessment) is dict and type(assessment.get("schema")) is int and assessment["schema"] == 1
    checked_at = _stamp(assessment.get("checked_at")) if valid else None
    reviewed_at = _reviewed_route(assessment.get("existing_account_route")) if valid and checked_at else None
    available = reviewed_at is not None
    status = "awaiting_authenticated_verification" if available else "metadata_unavailable"
    return {
        "schema": 1,
        "checked_at": checked_at,
        "status": status,
        "public_no_account": {
            "status": "not_qualified" if checked_at else "metadata_unavailable",
            "checked_at": checked_at,
            "summary": "No recurring free no-account VIX options feed has been qualified by this dated review.",
        },
        "existing_account": {
            "provider": "Robinhood",
            "status": status,
            "connection_status": "requires_verification",
            "checked_at": reviewed_at,
            "observation_scope": _SCOPE,
            "official_url": OFFICIAL_URL,
            "mcp_url": MCP_URL,
            "read_tools": list(READ_TOOLS) if available else [],
            "summary": "The documented existing-account MCP route is a candidate, not a no-account feed or qualified paper-execution source.",
        },
        "gates": [{"id": name, "status": "unverified", "detail": detail} for name, detail in _GATES],
        "paper_fills_enabled": False,
        "funding_added": "0",
        "shared_monthly_cap": "500.00",
        "runtime_connection_checked": False,
    }
