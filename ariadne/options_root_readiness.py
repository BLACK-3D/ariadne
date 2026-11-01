"""Versioned passive KO account view over the unchanged metadata inventory.

Only the host's verified schema5 state describes its selected source. Declared
catalogs cannot authorize a source, funding, instrument, signal or paper fill.
"""
from copy import deepcopy
from decimal import Context, Decimal, localcontext

from . import options_paper_readiness as inventory
from . import options_root_eod as engine

RULE = {"schema":2,"id":"same-account-ko-readiness-v1","engine_rule_sha256":engine.RULE_SHA256,
        "inventory_rule_sha256":inventory.RULE_SHA256,"configured_root":"KO","source_id":"onclick_ko_eod_v1",
        "scope":"Passive view of a verified schema5 account; metadata is not runtime or source authority.",
        "risk":"Same shared500 monthly gross commitment,100 all-in entry, one fully paid standard100 long put; no new funds or reset."}
RULE_SHA256 = engine.base._digest(RULE)


def build_readiness(index=None, *, account, source, checked_at, workflow_hold=None):
    if engine.base._digest(RULE) != RULE_SHA256:
        raise ValueError("The fixed KO readiness rule changed.")
    now = inventory._clock(checked_at)
    state = engine.validate_state(account["state"])
    if (type(account.get("id")) is not str or not 1<=len(account["id"])<=128
            or type(source) is not dict or source.get("symbol")!="KO" or source.get("provider")!="onclick_ko_eod_v1"
            or source.get("initialized") is not True or type(source.get("enabled")) is not bool):
        raise ValueError("Supply the verified existing KO account and fixed source projection.")
    result = inventory.build_readiness(index,checked_at=now)
    result.update(schema=2,rule_sha256=RULE_SHA256,configured_active_paper_roots=["KO"],
        active_paper={"account_id":account["id"],"schema":5,"source_id":state["source_id"],"policy_id":engine.STRATEGY_ID,
            "source_enabled":source["enabled"],"context":None,"quote_scope":"experimental_date_only_partial_chain",
            "runtime_permissions_and_full_entry_gates_required":True,"entry_authorized":False,"execution_qualified":False})
    with localcontext(Context(prec=40)):
        remaining=format(Decimal("500")-Decimal(state["monthly_committed"]),"f")
    result["shared_risk"].update(cash=state["cash"],monthly_committed=state["monthly_committed"],monthly_remaining=remaining,
        position_present=state["position"] is not None,pending_present=state["pending"] is not None,
        hold_present=state["hold"] is not None or workflow_hold is not None)
    for row in result["roots"]:
        row.update(configured_active_paper_root=False,current_options_source="not_selected_for_this_owner_account",
                   blockers=["not_selected_active_paper_root"],entry_authorized=False,execution_qualified=False)
    ko = next((row for row in result["roots"] if row["symbol"]=="KO"),None)
    if ko is None and len(result["roots"])<inventory.MAX_ROOTS:
        ko={"symbol":"KO","declared_window_count":0,"declared_windows":[],"coverage_envelope_start":None,
            "coverage_envelope_end":None,"continuous_history_established":False,"catalogs_verified_by_this_module":False,
            "provenance_flags_are_declarations":True,"current_price_status":"current_price_unmeasured","dated_spot":None,
            "below_100_at_saved_spot":None,"current_eligibility_established":False,"asset_type_inferred_from_symbol":False}
        result["roots"].append(ko)
        result["roots"].sort(key=lambda row:row["symbol"])
    if ko is not None:
        ko.update(configured_active_paper_root=True,current_options_source="configured_experimental_date_only_KO",
            blockers=["full_runtime_and_causal_entry_checks_remain_required"]+(["configured_source_disabled"] if not source["enabled"] else [])+
                (["existing_account_hold"] if result["shared_risk"]["hold_present"] else []),entry_authorized=False,execution_qualified=False)
    result["limitations"] = [line for line in result["limitations"] if not line.startswith("Only the existing F")]
    result["limitations"].append("KO is the sole selected source on the original account. Ford remains an immutable historical epoch, with no parallel allowance.")
    result["metadata_rule_sha256"]=inventory.RULE_SHA256
    inventory._encoded(result,inventory.MAX_OUTPUT_BYTES)
    return deepcopy(result)
