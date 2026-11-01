"""Passive schema6 risk/source view; catalog declarations grant no authority."""
from copy import deepcopy
from decimal import Context,Decimal,localcontext
from . import options_bankroll_eod as engine
from . import options_paper_readiness as inventory

RULE={"schema":3,"id":"same-account-total-bankroll-readiness-v1","engine_rule_sha256":engine.RULE_SHA256,
    "inventory_rule_sha256":inventory.RULE_SHA256,"configured_root":"KO","funding":"One25000total, no automatic topups; metadata grants no entry."}
RULE_SHA256=engine.base._digest(RULE)

def build_readiness(index=None,*,account,source,checked_at,workflow_hold=None):
    if engine.base._digest(RULE)!=RULE_SHA256:raise ValueError("The fixed bankroll readiness rule changed.")
    state=engine.validate_state(account["state"])
    if (type(account.get("id")) is not str or not 1<=len(account["id"])<=128 or type(source) is not dict
            or source.get("symbol")!="KO" or source.get("provider")!="onclick_ko_eod_v1" or source.get("initialized") is not True or type(source.get("enabled")) is not bool):
        raise ValueError("Supply the verified same KO bankroll account and selected source.")
    result=inventory.build_readiness(index,checked_at=checked_at)
    result.update(schema=3,account_schema=6,rule_sha256=RULE_SHA256,configured_active_paper_roots=["KO"],
        bankroll_total="25000",monthly_gross_commitment_cap="25000",all_in_entry_cap="1000",automatic_topups=False,
        active_paper={"account_id":account["id"],"schema":6,"source_id":state["source_id"],"policy_id":engine.STRATEGY_ID,
            "source_enabled":source["enabled"],"context":None,"quote_scope":"experimental_date_only_partial_chain",
            "runtime_permissions_and_full_entry_gates_required":True,"entry_authorized":False,"execution_qualified":False})
    with localcontext(Context(prec=40)):remaining=format(Decimal("25000")-Decimal(state["monthly_committed"]),"f")
    result["shared_risk"].update(monthly_gross_commitment_cap="25000",all_in_entry_cap="1000",bankroll_total="25000",automatic_topups=False,
        instrument="one_fully_paid_standard_100_long_put_position_variable_integer_contract_quantity",
        entry_equity_fraction="0.04",cash=state["cash"],monthly_committed=state["monthly_committed"],monthly_remaining=remaining,
        position_present=state["position"] is not None,pending_present=state["pending"] is not None,hold_present=state["hold"] is not None or workflow_hold is not None)
    for row in result["roots"]:row.update(configured_active_paper_root=False,current_options_source="not_selected_for_this_owner_account",blockers=["not_selected_active_paper_root"],entry_authorized=False,execution_qualified=False)
    ko=next((r for r in result["roots"] if r["symbol"]=="KO"),None)
    if ko is None and len(result["roots"])<inventory.MAX_ROOTS:
        ko={"symbol":"KO","declared_window_count":0,"declared_windows":[],"coverage_envelope_start":None,"coverage_envelope_end":None,
            "continuous_history_established":False,"catalogs_verified_by_this_module":False,"provenance_flags_are_declarations":True,
            "current_price_status":"current_price_unmeasured","dated_spot":None,"below_100_at_saved_spot":None,"current_eligibility_established":False,"asset_type_inferred_from_symbol":False}
        result["roots"].append(ko);result["roots"].sort(key=lambda r:r["symbol"])
    if ko is not None:ko.update(configured_active_paper_root=True,current_options_source="configured_experimental_date_only_KO",entry_authorized=False,execution_qualified=False,
        blockers=["full_runtime_and_causal_entry_checks_remain_required"]+(["configured_source_disabled"] if not source["enabled"] else [])+(["existing_account_hold"] if result["shared_risk"]["hold_present"] else []))
    result["limitations"]=[s for s in result["limitations"] if not s.startswith("Only the existing F")]
    result["limitations"].append("Schema6 uses one25000total bankroll including old cash. No automatic topups. A flat new month can reset gross commitment only; proceeds never replenish within-month commitment. Up to4%NAV/1000 per entry, actual supplied whole-contract sizes required.")
    result["metadata_rule_sha256"]=inventory.RULE_SHA256
    inventory._encoded(result,inventory.MAX_OUTPUT_BYTES)
    return deepcopy(result)
