"""Build a private, offline monthly business cost-and-capacity scenario.

Use the bundled Python with -I -B. This calculator does not contact customers,
load models, change Ariadne settings, or treat budgets as actual expenditure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ariadne.atomic import publish_bytes
from ariadne.config import _allowed_path
from ariadne.tools import ToolRegistry

MAX_INPUT = 64 * 1024
D = Decimal
WEEKS_PER_MONTH = D(52) / D(12)
TOP_FIELDS = {"schema", "label", "system_budget_monthly", "actual_system_cost_monthly",
              "other_fixed_cost_monthly", "monthly_profit_target", "hourly_value",
              "weekly_hours_profiles", "fixed_nonbillable_hours_monthly", "offers"}
OFFER_FIELDS = {"id", "name", "price", "clients", "hours_per_client",
                "variable_cash_per_client", "payment_fee_percent"}


def _decimal(value):
    if len(value) > 48:
        raise ValueError("Numeric literals must be bounded.")
    result = D(value)
    if not result.is_finite() or not -6 <= result.as_tuple().exponent <= 6 or abs(result) > D("1000000000"):
        raise ValueError("Use finite, bounded numbers with at most six decimal places.")
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON keys are not accepted.")
        result[key] = value
    return result


def load_document(raw):
    if type(raw) is not bytes or len(raw) > MAX_INPUT:
        raise ValueError("Input must contain at most 64 KiB of UTF-8 JSON.")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
                           parse_float=_decimal, parse_int=_decimal,
                           parse_constant=lambda unused: (_ for _ in ()).throw(ValueError("Nonfinite JSON numbers are not accepted.")))
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Input must be bounded UTF-8 JSON.") from error
    return validate(value)


def number(value, name, maximum=D("1000000"), *, positive=False):
    # In particular, bool is not a number and strings are not silently coerced.
    if type(value) not in (int, Decimal):
        raise ValueError(name + " must be a JSON number.")
    result = D(value)
    if not result.is_finite() or not -6 <= result.as_tuple().exponent <= 6 or result < 0 or result > maximum or positive and not result:
        raise ValueError(name + " is outside its finite nonnegative range.")
    return result


def _text(value, name, maximum):
    if type(value) is not str or not 1 <= len(value.strip()) <= maximum or any(ord(char) < 32 for char in value):
        raise ValueError(name + " must be bounded nonempty text without control characters.")
    return value.strip()


def validate(value):
    if type(value) is not dict or set(value) != TOP_FIELDS:
        raise ValueError("Use the exact documented top-level economics fields.")
    if type(value["schema"]) not in (int, Decimal) or value["schema"] != 1:
        raise ValueError("Use economics schema 1.")
    result = {"schema": 1, "label": _text(value["label"], "label", 200)}
    for name in ("system_budget_monthly", "monthly_profit_target", "hourly_value", "fixed_nonbillable_hours_monthly"):
        result[name] = number(value[name], name, D(744) if name == "fixed_nonbillable_hours_monthly" else D("1000000"))
    for name in ("actual_system_cost_monthly", "other_fixed_cost_monthly"):
        result[name] = None if value[name] is None else number(value[name], name)
    profiles = value["weekly_hours_profiles"]
    if type(profiles) is not list or not 1 <= len(profiles) <= 4:
        raise ValueError("Supply one to four weekly-hours capacity profiles.")
    result["weekly_hours_profiles"] = [number(item, "weekly hours", D(168), positive=True) for item in profiles]
    if len(set(result["weekly_hours_profiles"])) != len(profiles):
        raise ValueError("Weekly-hours capacity profiles must be unique.")
    offers = value["offers"]
    if type(offers) is not list or not 1 <= len(offers) <= 12:
        raise ValueError("Supply one to twelve hypothetical offers.")
    result["offers"], identifiers = [], set()
    for offer in offers:
        if type(offer) is not dict or set(offer) != OFFER_FIELDS:
            raise ValueError("Use the exact documented offer fields.")
        identifier = _text(offer["id"], "offer id", 64)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identifier) or identifier in identifiers:
            raise ValueError("Offer IDs must be unique letters, numbers, underscores or hyphens.")
        identifiers.add(identifier)
        item = {"id": identifier, "name": _text(offer["name"], "offer name", 200)}
        for name in ("price", "variable_cash_per_client", "hours_per_client", "payment_fee_percent"):
            maximum = D(100) if name == "payment_fee_percent" else D(744) if name == "hours_per_client" else D("1000000")
            item[name] = number(offer[name], name, maximum, positive=name == "hours_per_client")
        clients = number(offer["clients"], "clients", D(1000))
        if clients != clients.to_integral_value():
            raise ValueError("Client counts must be whole numbers.")
        item["clients"] = int(clients)
        result["offers"].append(item)
    return result


def money(value):
    return str(value.quantize(D("0.01"), rounding=ROUND_HALF_UP))


def quantity(value):
    return str(value.quantize(D("0.01"), rounding=ROUND_HALF_UP))


def _count(need, contribution):
    if need <= 0:
        return 0
    return None if contribution <= 0 else int((need / contribution).to_integral_value(rounding=ROUND_CEILING))


def calculate(document):
    cfg = validate(document)
    actual = cfg["actual_system_cost_monthly"]
    other = cfg["other_fixed_cost_monthly"]
    assumed_system = cfg["system_budget_monthly"] if actual is None else actual
    fixed = assumed_system + (other if other is not None else D(0))
    fixed_hours, wage, target = (cfg[key] for key in ("fixed_nonbillable_hours_monthly", "hourly_value", "monthly_profit_target"))
    unknown = [name for name in ("actual_system_cost_monthly", "other_fixed_cost_monthly") if cfg[name] is None]
    result = {"schema": 1, "label": cfg["label"], "currency": "USD", "status": "hypothetical_scenario_only",
              "actual_costs_complete": not unknown, "unknown_cost_fields": unknown,
              "system_cost_basis": "budget_assumption_not_measured" if actual is None else "owner_supplied_actual",
              "system_budget_monthly": money(cfg["system_budget_monthly"]),
              "actual_system_cost_monthly": None if actual is None else money(actual),
              "other_fixed_cost_monthly": None if other is None else money(other),
              "fixed_cash_cost_used": money(fixed), "monthly_profit_target": money(target),
              "hourly_value": money(wage), "fixed_nonbillable_hours_monthly": quantity(fixed_hours),
              "trading_income_budgeted": "0.00", "model_calls": 0, "network_calls": 0,
              "notes": ["Prices, sales, client counts, delivery time and fees are assumptions, not observed demand or revenue.",
                        "The system budget is not a measured bill. Unknown other fixed costs are excluded from these scenarios.",
                        "Cash surplus is before owner compensation and personal taxes. Economic surplus additionally subtracts the stated value of all owner hours; this is not a tax deduction or taxable-profit calculation.",
                        "Monthly capacity uses 52/12 weeks; sales, onboarding and support must fit the fixed nonbillable hours or be added explicitly.",
                        "No trade gains, future investment returns, external actions or new paid services are included."], "offers": []}
    for offer in cfg["offers"]:
        price, hours, variable, fee_percent = (offer[key] for key in ("price", "hours_per_client", "variable_cash_per_client", "payment_fee_percent"))
        fee = price * fee_percent / D(100)
        cash_contribution = price - variable - fee
        economic_contribution = cash_contribution - hours * wage
        minimum_cash = _count(fixed + target, cash_contribution)
        minimum_economic = _count(fixed + fixed_hours * wage + target, economic_contribution)
        clients = offer["clients"]
        total_hours = hours * clients + fixed_hours
        cash = cash_contribution * clients - fixed
        economic = cash - total_hours * wage
        churn_clients = max(0, clients - 1)
        churn_hours = hours * churn_clients + fixed_hours
        churn_cash = cash_contribution * churn_clients - fixed
        row = {"id": offer["id"], "name": offer["name"], "clients_assumed": clients,
               "price_per_client": money(price), "hours_per_client": quantity(hours),
               "payment_fee_percent": quantity(fee_percent), "payment_fee_per_client": money(fee),
               "variable_cash_per_client": money(variable), "cash_contribution_per_client": money(cash_contribution),
               "economic_contribution_per_client": money(economic_contribution),
               "revenue": money(price * clients), "payment_fees": money(fee * clients),
               "variable_cash_cost": money(variable * clients), "cash_surplus_before_owner_compensation_and_personal_taxes": money(cash),
               "total_owner_hours": quantity(total_hours), "owner_time_value": money(total_hours * wage),
               "economic_surplus_before_personal_taxes": money(economic),
               "cash_target_met_under_assumptions": cash >= target,
               "economic_target_met_under_assumptions": economic >= target,
               "minimum_clients_cash_target": minimum_cash, "minimum_clients_economic_target": minimum_economic,
               "positive_cash_contribution": cash_contribution > 0, "positive_economic_contribution": economic_contribution > 0,
               "additional_fixed_cash_cost_headroom_for_economic_target": money(economic - target),
               "one_client_churn": {"remaining_clients": churn_clients, "revenue": money(price * churn_clients),
                    "cash_surplus_before_owner_compensation_and_personal_taxes": money(churn_cash),
                    "economic_surplus_before_personal_taxes": money(churn_cash - churn_hours * wage),
                    "economic_target_met_under_assumptions": churn_cash - churn_hours * wage >= target},
               "capacity": []}
        for weekly in cfg["weekly_hours_profiles"]:
            budget = weekly * WEEKS_PER_MONTH
            maximum = max(0, int(((budget - fixed_hours) / hours).to_integral_value(rounding=ROUND_FLOOR)))
            row["capacity"].append({"weekly_owner_hours_available": quantity(weekly), "monthly_owner_hours_available": quantity(budget),
                "maximum_clients_under_assumptions": maximum, "fixed_hours_alone_exceed_capacity": fixed_hours > budget,
                "assumed_clients_fit": total_hours <= budget,
                "cash_target_clients_fit": minimum_cash is not None and fixed_hours + minimum_cash * hours <= budget,
                "economic_target_clients_fit": minimum_economic is not None and fixed_hours + minimum_economic * hours <= budget})
        result["offers"].append(row)
    return result


def render_html(report):
    escape = lambda value: html.escape(str(value), quote=True)
    currency = lambda value: "$" + escape(value)
    counts = lambda value: "Not attainable: nonpositive contribution" if value is None else str(value)
    cards = []
    for row in report["offers"]:
        capacity = "".join("<li>" + escape(item["weekly_owner_hours_available"]) + " h/week: up to " + str(item["maximum_clients_under_assumptions"]) +
                           " clients; economic target " + ("fits" if item["economic_target_clients_fit"] else "does not fit") + ".</li>" for item in row["capacity"])
        cards.append(f'''<article><h2>{escape(row["name"])}</h2><p>{row["clients_assumed"]} hypothetical clients × {currency(row["price_per_client"])} / month</p>
<dl><dt>Revenue</dt><dd>{currency(row["revenue"])}</dd><dt>Cash surplus before owner compensation and personal taxes</dt><dd>{currency(row["cash_surplus_before_owner_compensation_and_personal_taxes"])}</dd>
<dt>All owner hours / month</dt><dd>{escape(row["total_owner_hours"])}</dd><dt>Value of owner time</dt><dd>{currency(row["owner_time_value"])}</dd>
<dt>Economic surplus before personal taxes</dt><dd>{currency(row["economic_surplus_before_personal_taxes"])}</dd><dt>Clients for cash target</dt><dd>{escape(counts(row["minimum_clients_cash_target"]))}</dd>
<dt>Clients for economic target</dt><dd>{escape(counts(row["minimum_clients_economic_target"]))}</dd><dt>Further fixed-cost headroom above economic target</dt><dd>{currency(row["additional_fixed_cash_cost_headroom_for_economic_target"])}</dd>
<dt>Economic surplus after one client leaves</dt><dd>{currency(row["one_client_churn"]["economic_surplus_before_personal_taxes"])}</dd></dl><ul>{capacity}</ul>
<p class="note">Per client: {escape(row["hours_per_client"])} hours, {currency(row["variable_cash_per_client"])} direct cash costs and {escape(row["payment_fee_percent"])}% payment-fee assumption.</p></article>''')
    notes = "".join("<li>" + escape(note) + "</li>" for note in report["notes"])
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(report["label"])}</title><style>body{{margin:0;background:#081110;color:#e5efea;font:16px/1.55 system-ui,sans-serif}}main{{max-width:1150px;margin:auto;padding:40px 24px}}h1{{font-size:clamp(28px,5vw,48px);line-height:1.1}}h2{{font-size:24px}}.tag{{color:#98dab8;letter-spacing:.15em;font-size:12px;text-transform:uppercase}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:20px}}article,.summary{{background:#10201d;border:1px solid #345149;border-radius:16px;padding:24px;margin:20px 0}}dl{{display:grid;grid-template-columns:1fr auto;gap:12px}}dt{{color:#aebfb7}}dd{{margin:0;font-variant-numeric:tabular-nums;font-weight:700}}.note,footer{{font-size:13px;color:#b0beb8}}.warning{{color:#ffd39a}}@media print{{body{{background:white;color:black}}article,.summary{{background:white;border-color:#777;break-inside:avoid}}dt,.tag,.note,footer{{color:#333}}}}</style></head><body><main>
<p class="tag">Ariadne · offline venture economics · hypothetical scenario</p><h1>{escape(report["label"])}</h1>
<section class="summary"><p>Monthly target: <strong>{currency(report["monthly_profit_target"])}</strong> before personal taxes. System budget: <strong>{currency(report["system_budget_monthly"])}</strong>.</p>
<p class="warning">Actual complete costs: {"owner supplied; not independently verified" if report["actual_costs_complete"] else "unknown"}. Cost basis: {escape(report["system_cost_basis"])}. Fixed cash costs used: {currency(report["fixed_cash_cost_used"])}.</p>
<p>Owner time valued at {currency(report["hourly_value"])} / hour. Fixed sales/admin time: {escape(report["fixed_nonbillable_hours_monthly"])} hours / month. Trading income budgeted: $0.</p></section>
<div class="grid">{"".join(cards)}</div><h2>How to interpret this</h2><ul>{notes}</ul><footer>Generated locally without models, network requests, cookies or scripts. This report does not establish customers, revenue, legal readiness or future returns. Change the input assumptions and generate a new output folder to compare scenarios.</footer>
</main></body></html>'''


def run(input_relative, output_relative, workspace):
    root = _allowed_path(workspace)
    try:
        root.relative_to(ROOT)
    except ValueError:
        raise ValueError("The economics workspace must remain inside this Ariadne installation.") from None
    if not root.is_dir():
        raise ValueError("Choose an existing economics workspace directory.")
    # Reuse the path guard without constructing any optional tools or databases.
    guard = object.__new__(ToolRegistry)
    guard.root = root
    source, destination = guard._path(input_relative), guard._path(output_relative)
    if source == destination:
        raise ValueError("Input and output must differ.")
    with guard._directory(source.parent) as directory:
        with os.fdopen(guard._open_read(source, directory), "rb") as content:
            before = os.fstat(content.fileno())
            raw = content.read(MAX_INPUT + 1)
            after = os.fstat(content.fileno())
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if len(raw) > MAX_INPUT or before.st_size != len(raw) or identity(before) != identity(after):
        raise ValueError("Economics input is oversized or changed during reading.")
    document = load_document(raw)
    report = calculate(document)
    report["input_sha256"] = hashlib.sha256(raw).hexdigest()
    report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    payloads = {"economics.json": (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"),
                "economics.html": render_html(report).encode("utf-8")}
    # An existing/partial output is never overwritten, adopted, or replayed.
    with guard._directory(destination.parent, create=True):
        destination.mkdir()
        with guard._directory(destination) as directory:
            artifacts = []
            for name, payload in payloads.items():
                path = destination / name
                publish_bytes(path, payload)
                with os.fdopen(guard._open_read(path, directory), "rb") as content:
                    actual = content.read(len(payload) + 1)
                if actual != payload:
                    raise ValueError("Economics publication readback failed; inspect the partial output without replay.")
                artifacts.append({"path": path.relative_to(root).as_posix(), "bytes": len(payload), "sha256": hashlib.sha256(actual).hexdigest()})
    return {"ok": True, "status": report["status"], "actual_costs_complete": report["actual_costs_complete"], "model_calls": 0, "network_calls": 0, "artifacts": artifacts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Workspace-relative JSON assumptions file")
    parser.add_argument("output", help="New workspace-relative output folder; existing output is never replaced")
    parser.add_argument("--workspace", default=str(ROOT / "data/workspace"))
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.input, args.output, args.workspace), indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, "Economics report stopped: " + str(error) + "\n")


if __name__ == "__main__":
    main()
