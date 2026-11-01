"""Read sealed schema6 accounting; no financial engine replay or collection."""
from copy import deepcopy
from decimal import Decimal
from . import options_bankroll_eod as engine
from .handoff import _load
from .options_workflow import _now,_json

@engine._precision
def review(owner,db,account,*,now,paused,revision):
    clock=_now(now);state=engine.validate_state(account["state"]);owner.bankroll.verify_account(db,account["id"],state,full=True)
    rows=db.execute("SELECT request_id FROM opt_root_data UNION ALL SELECT request_id FROM opt_bankroll_data").fetchall()
    if len(rows)!=state["root_processed_count"] or len(rows)>100:raise ValueError("Bankroll journal lost its accepted capture count.")
    captures=[]
    for row in rows:
        receipt=owner.eod._recorded(db,row[0]);saved=owner.root_eod.verify(receipt)
        captures.append({"receipt":receipt,"paper":_load(saved["paper.json"]),"snapshot":_load(saved["snapshot.json"])})
    captures.sort(key=lambda c:c["snapshot"]["source_date"])
    current=engine.summarize(state,now=clock);wins=losses=breakeven=0;realized=peak=drop=Decimal(0);curve=[];months={}
    for capture in captures:
        paper=capture["paper"];daily=paper["state"]["last_daily_result"];pnl=Decimal(daily["trading_pnl"])
        peak=max(peak,pnl);drop=max(drop,peak-pnl)
        curve.append({"date":daily["date"],"recorded_at":daily["recorded_at"],"trading_pnl":daily["trading_pnl"],"equity":daily["equity"],"contributions":daily["contributions"],"account_schema":paper["state"]["schema"]})
        months[daily["month"]]={"month":daily["month"],"trading_pnl":daily["monthly_pnl"],"monthly_committed":daily["monthly_budget_used"],"monthly_remaining":daily["monthly_budget_remaining"],"account_schema":paper["state"]["schema"]}
        for event in paper["events"]:
            if event.get("type")=="fill" and event.get("action")=="SELL":
                value=Decimal(event["trade_pnl"]);realized+=value;wins+=int(value>0);losses+=int(value<0);breakeven+=int(value==0)
    if wins+losses+breakeven!=state["closed_trade_count"] or realized!=Decimal(current["policy_stats"]["realized_pnl"]):raise ValueError("Bankroll closed outcomes disagree with sealed saved fills.")
    closed=state["closed_trade_count"];performance={k:current[k] for k in ("cash","equity","contributions","trading_pnl","fees")}
    performance.update(entries=state["trade_count"],closed_trades=closed,wins=wins,losses=losses,breakeven=breakeven,
        legacy_closed_trade_count=state["bankroll_policy"]["activation"]["initial_financials"]["closed_trade_count"],
        new6_closed_trade_count=closed-state["bankroll_policy"]["activation"]["initial_financials"]["closed_trade_count"],
        contract_entries=state["contract_entry_count"],contract_exits=state["contract_exit_count"],
        observed_win_rate_pct=None if not closed else engine.base._text(Decimal(wins)*100/closed),win_rate_scope="Observed closed price-proxy outcomes, not future profit probability.",
        sample_status="insufficient_closed_proxy_sample" if closed<=30 else "larger_sample_still_unverified_edge",max_peak_trading_pnl_drop=engine.base._text(drop),
        drawdown_units="USD decline from recorded trading P/L peak; deposits excluded",ledger_month=current["month"],monthly_budget_used=current["monthly_budget_used"],
        monthly_budget_remaining=current["monthly_budget_remaining"],cap_assurance=current["cap_assurance"],curve=curve,months=list(months.values()),
        by_strategy=[{"id":engine.STRATEGY_ID,"entries":state["trade_count"],"closed_trades":closed,"wins":wins,"losses":losses,"breakeven":breakeven,
            "realized_pnl":current["policy_stats"]["realized_pnl"],"fees":state["fees"],"monthly_committed":state["monthly_committed"]}])
    condition="status IN ('running','failed') OR (action='root_collect' AND status='acknowledged')";failures=[]
    total=db.execute("SELECT count(*) FROM opt_runs WHERE "+condition).fetchone()[0]
    for row in db.execute("SELECT action,status,substr(receipt,1,16001) receipt FROM opt_runs WHERE "+condition+" ORDER BY rowid DESC LIMIT 12"):
        if len(row["receipt"].encode())>16000:raise ValueError("Bankroll journal receipt exceeds its bound.")
        receipt=_load(row["receipt"]);verified=False
        if row["action"]=="root_collect" and receipt.get("artifacts"):owner.root_eod.verify(receipt);verified=True
        failures.append({"request_id":receipt["request_id"],"action":row["action"],"status":row["status"],"created_at":receipt.get("created_at"),
            "reason":str(receipt.get("reason","Interrupted work requires inspection."))[:400],"adopted_as_performance":False,"retained_artifacts_verified":verified})
    held=next((v for v in reversed(failures) if v["status"] in ("running","failed")),None)
    from .qqq_data import last_closed_session
    expected=last_closed_session(clock).isoformat();latest=captures[-1] if captures else None
    source={"last_source_date":None,"actual_received_at":None,"recorded_decision_at":None,"quote_observed_at":None,"expected_last_session":expected,
        "latest_matches_expected_session":False,"partial_chain":None,"collection_enabled":owner.root_eod.config(db)["enabled"],"scope":"Saved partial date-only observations; execution unverified."}
    if latest:
        snap=latest["snapshot"];source.update(last_source_date=snap["source_date"],actual_received_at=snap["received_at"],recorded_decision_at=latest["receipt"]["recorded_at"],latest_matches_expected_session=snap["source_date"]==expected,partial_chain=snap["partial_chain"])
    result={"schema":1,"account_schema":6,"profile":"experimental_total_bankroll_stress_put_eod_paper","status":"held" if held or state["hold"] else "recorded_observations" if captures else "waiting_for_first_capture",
        "reviewed_at":clock.isoformat(),"revision":revision,"account_id":account["id"],"symbol":"KO","rule_sha256":engine.RULE_SHA256,
        "bankroll_total":"25000","monthly_gross_commitment_cap":"25000","all_in_entry_cap":"1000","automatic_topups":False,"source":source,"performance":performance,
        "diagnostics":{"status":"stress_policy_only","strategies":[],"last_gate":deepcopy(current["last_entry_gate"]),"scope":"Saved6-bound gates only; no new decision or replay."},
        "holds":{"global_paused":paused,"workflow":held,"paper":deepcopy(state["hold"]),"cap_assurance":current["cap_assurance"]},"failed_checks":failures,"omitted_failed_checks":max(0,total-len(failures)),
        "limitations":["Totals include sealed schema5 history and new schema6 outcomes; each curve row identifies its original account schema. Legacy outcomes do not establish25k-policy performance.","One25000total bankroll including prior cash, no automatic topups. Capital adjustment is not trading profit.","At mostmin(1000,4%NAV,cash,unused monthly gross commitment) per entry, actual whole-contract side sizes required.",
            "Monthly gross allowance may reset while healthy flat; it adds no cash and profits never refill within-month commitment.","Date-only partial quotes, unknown event/publication clocks and unverified deliverables are price proxies, not executable orders.","No proven profitable edge or broker connection."],
        "model_calls":0,"network_calls":0,"execution_qualified":False,"research_only":True}
    if len(_json(result).encode())>256*1024:raise ValueError("Bankroll journal exceeds its fixed output bound.")
    return result
