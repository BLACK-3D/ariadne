"""Bounded saved-business and paper-research views, with no side effects."""
import re

_MONEY=re.compile(r"-?\d{1,12}(?:\.\d{1,12})?\Z")

def _money(value):
    return value if type(value) is str and _MONEY.fullmatch(value) else None

def _count(value,maximum=10000):
    return value if type(value) is int and 0<=value<=maximum else None

def _text(value,limit=120):
    if type(value) is not str or any(ord(char)<32 or 0x7f<=ord(char)<=0x9f or 0xd800<=ord(char)<=0xdfff for char in value):return None
    return value[:limit]

def revenue_view(value):
    result={"status":"unavailable","planning_only":True,"actual_profit_verified":False,"offers":[],
            "cash":None,"next_action":"Inspect saved business evidence; no missing sale, payment or cost is inferred."}
    if type(value) is not dict:return result
    status=value.get("status")
    if type(status) is not str or status not in {"available","not_configured","held","unavailable"}:return result
    result["status"]="held" if value.get("hold") else status
    cash=value.get("cash_summary")
    if (type(cash) is dict and cash.get("owner_reported") is True and cash.get("independently_verified") is False
            and cash.get("cash_costs_complete") is False and cash.get("timezone")=="UTC"
            and type(cash.get("month")) is str and re.fullmatch(r"\d{4}-\d{2}",cash["month"])):
        result["cash"]={"month":cash["month"],"timezone":"UTC","owner_reported":True,"complete_profit":False,
            "net_reported_cash":_money(cash.get("net_reported_cash")),"sales_reported":_money(cash.get("sale_reported")),
            "payouts_reported":_money(cash.get("payout_received")),"evidence_count":_count(cash.get("evidence_count"),100),
            "interviews":_count(cash.get("interviews"),100),"pilot_agreements":_count(cash.get("pilot_agreements"),100)}
    if result["status"]!="available":
        if result["status"]=="not_configured":result["next_action"]="Compare one POD offer with one service offer using explicit costs, fulfillment funding and owner time."
        return result
    plan=value.get("plan_summary")
    if type(plan) is not dict or plan.get("planning_only") is not True:
        result["status"]="unavailable";return result
    result.update(label=_text(plan.get("label")),system_cost_used=_money(plan.get("system_cost_used")),
                  monthly_profit_target=_money(plan.get("monthly_profit_target")),
                  system_cost_basis=_text(plan.get("system_cost_basis"),64),trading_income_budgeted="0.00")
    unknown=plan.get("unknown_fixed_cost_fields")
    incomplete=not isinstance(unknown,list) or bool(unknown)
    offers=plan.get("offers") if type(plan.get("offers")) is list else []
    for row in offers[:3]:
        if type(row) is not dict:incomplete=True;continue
        complete=row.get("unit_costs_complete") is True
        contribution=_money(row.get("cash_contribution_per_unit"))
        incomplete=incomplete or not complete or contribution is None
        result["offers"].append({"name":_text(row.get("name"),90),"kind":_text(row.get("kind"),24),
            "cash_contribution_per_unit":contribution,"minimum_units_cash_target":_count(row.get("minimum_units_cash_target")),
            "unit_costs_complete":complete})
    result["unknown_costs"]=incomplete
    if incomplete:result["next_action"]="Obtain the missing supplier, checkout, acquisition and fixed-cost assumptions before treating a scenario as cost coverage."
    elif not result["cash"] or not result["cash"]["interviews"]:result["next_action"]="Test both offers in owner-led buyer conversations and record price acceptance; modeled margins are not demand."
    elif not result["cash"]["payouts_reported"] or result["cash"]["payouts_reported"] in {"0","0.00"}:result["next_action"]="Seek one agreed paid pilot or actual product sale, then record your own received cash and delivery time."
    else:result["next_action"]="Reconcile recorded payouts, separately paid costs, refunds and actual hours before expanding the offer."
    return result

def options_review_view(value):
    result={"status":"unavailable","execution_qualified":False,"research_only":True,
            "scope":"Saved partial EOD paper proxies only; no current trade recommendation or established profitable edge."}
    if type(value) is not dict or value.get("execution_qualified") is not False or value.get("research_only") is not True:return result
    status=value.get("status")
    if type(status) is not str or status not in {"not_started","unsupported_profile","waiting_for_first_capture","recorded_observations","held"}:return result
    result["status"]=status
    performance=value.get("performance")
    if type(performance) is dict:
        result.update(trading_pnl=_money(performance.get("trading_pnl")),fees=_money(performance.get("fees")),
            entries=_count(performance.get("entries")),closed_trades=_count(performance.get("closed_trades")),
            observed_win_rate_pct=_money(performance.get("observed_win_rate_pct")),sample_status=_text(performance.get("sample_status"),64))
    source=value.get("source")
    if type(source) is dict:
        result.update(source_date=_text(source.get("last_source_date"),10),actual_received_at=_text(source.get("actual_received_at"),40),
                      partial_chain=source.get("partial_chain") if type(source.get("partial_chain")) is bool else None)
    return result
