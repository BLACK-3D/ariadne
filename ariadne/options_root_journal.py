"""Read-only KO epoch journal using sealed observations; never replays trading."""
from copy import deepcopy
from decimal import Decimal

from . import options_root_eod as engine
from .handoff import _load
from .options_workflow import _now, _digest, _json


@engine.base._precision
def review(owner, db, account, *, now, paused, revision):
    clock = _now(now)
    state = engine.validate_state(account["state"])
    source = owner.root_eod
    source.verify_account(db,account["id"],state,full=True)
    rows = db.execute("SELECT request_id FROM opt_root_data ORDER BY source_date LIMIT 101").fetchall()
    if len(rows)!=state["root_processed_count"] or len(rows)>100:
        raise ValueError("KO journal lost its bounded accepted capture count.")
    captures=[]
    for row in rows:
        receipt=owner.eod._recorded(db,row[0])
        saved=source.verify(receipt)
        captures.append({"receipt":receipt,"paper":_load(saved["paper.json"]),"snapshot":_load(saved["snapshot.json"])})
    current=engine.summarize(state,now=clock)
    wins=losses=breakeven=0
    realized=Decimal(0)
    peak=maximum_drop=Decimal(0)
    curve=[]
    months={}
    for capture in captures:
        paper=capture["paper"]
        daily=paper["state"]["last_daily_result"]
        pnl=Decimal(daily["trading_pnl"])
        peak=max(peak,pnl)
        maximum_drop=max(maximum_drop,peak-pnl)
        curve.append({"date":daily["date"],"recorded_at":daily["recorded_at"],"trading_pnl":daily["trading_pnl"],"equity":daily["equity"],"contributions":daily["contributions"]})
        months[daily["month"]]={"month":daily["month"],"trading_pnl":daily["monthly_pnl"],"monthly_committed":daily["monthly_budget_used"],"monthly_remaining":daily["monthly_budget_remaining"]}
        for event in paper["events"]:
            if event.get("type")=="fill" and event.get("action")=="SELL":
                value=Decimal(event["trade_pnl"])
                realized+=value
                wins+=int(value>0)
                losses+=int(value<0)
                breakeven+=int(value==0)
    if wins+losses+breakeven!=state["closed_trade_count"] or realized!=Decimal(current["policy_stats"]["realized_pnl"]):
        raise ValueError("KO closed performance does not reconcile to sealed saved fills.")
    closed=state["closed_trade_count"]
    performance={key:current[key] for key in ("cash","equity","contributions","trading_pnl","fees")}
    performance.update(entries=state["trade_count"],closed_trades=closed,wins=wins,losses=losses,breakeven=breakeven,
        observed_win_rate_pct=None if closed==0 else engine.base._text(Decimal(wins)*100/closed),
        win_rate_scope="Observed closed price-proxy outcomes, not a forecast or probability of future profit.",
        sample_status="insufficient_closed_proxy_sample" if closed<=30 else "larger_sample_still_unverified_edge",
        max_peak_trading_pnl_drop=engine.base._text(maximum_drop),drawdown_units="USD decline from recorded trading P/L peak; separate from monthly gross commitment cap",
        ledger_month=current["month"],monthly_budget_used=current["monthly_budget_used"],monthly_budget_remaining=current["monthly_budget_remaining"],
        cap_assurance=current["cap_assurance"],curve=curve,months=list(months.values()),by_strategy=[{
            "id":engine.STRATEGY_ID,"entries":state["trade_count"],"closed_trades":closed,"wins":wins,"losses":losses,"breakeven":breakeven,
            "realized_pnl":current["policy_stats"]["realized_pnl"],"fees":state["fees"],"monthly_committed":state["monthly_committed"]}])
    failures=[]
    condition="status IN ('running','failed') OR (action='root_collect' AND status='acknowledged')"
    total=db.execute("SELECT count(*) FROM opt_runs WHERE "+condition).fetchone()[0]
    for row in db.execute("SELECT action,status,substr(receipt,1,16001) receipt FROM opt_runs WHERE "+condition+" ORDER BY rowid DESC LIMIT 12"):
        if len(row["receipt"].encode())>16000:
            raise ValueError("KO journal receipt exceeds its bound.")
        receipt=_load(row["receipt"])
        verified=False
        if row["action"]=="root_collect" and receipt.get("artifacts"):
            source.verify(receipt)
            verified=True
        failures.append({"request_id":receipt["request_id"],"action":row["action"],"status":row["status"],"created_at":receipt.get("created_at"),
            "reason":str(receipt.get("reason","Interrupted work requires inspection."))[:400],"adopted_as_performance":False,"retained_artifacts_verified":verified})
    held=next((value for value in reversed(failures) if value["status"] in ("running","failed")),None)
    from .qqq_data import last_closed_session
    expected=last_closed_session(clock).isoformat()
    latest=captures[-1] if captures else None
    source_view={"last_source_date":None,"actual_received_at":None,"recorded_decision_at":None,"quote_observed_at":None,
        "expected_last_session":expected,"latest_matches_expected_session":False,"partial_chain":None,
        "collection_enabled":source.config(db)["enabled"],"scope":"Partial dated EOD quotes; unknown option-event clock, not current executable quotes."}
    if latest:
        snapshot=latest["snapshot"]
        source_view.update(last_source_date=snapshot["source_date"],actual_received_at=snapshot["received_at"],recorded_decision_at=latest["receipt"]["recorded_at"],
            latest_matches_expected_session=snapshot["source_date"]==expected,partial_chain=snapshot["partial_chain"])
    result={"schema":1,"profile":"experimental_root_stress_put_eod_paper","status":"held" if held or state["hold"] else "recorded_observations" if captures else "waiting_for_first_capture",
        "reviewed_at":clock.isoformat(),"revision":revision,"account_id":account["id"],"symbol":"KO","rule_sha256":engine.RULE_SHA256,
        "source":source_view,"performance":performance,"diagnostics":{"status":"stress_policy_only","strategies":[],"last_gate":deepcopy(current["last_entry_gate"]),
            "scope":"Saved root-bound decision/fill gates over immutable benchmark data; no new decision, model call or financial replay."},
        "holds":{"global_paused":paused,"workflow":held,"paper":deepcopy(state["hold"]),"cap_assurance":current["cap_assurance"]},
        "failed_checks":failures,"omitted_failed_checks":max(0,total-len(failures)),"legacy_root":"F","legacy_captures":state["root_policy"]["activation"]["legacy_processed_count"],
        "limitations":["One original paper account with a separate KO source epoch; Ford's retained evidence is immutable and grants no extra allowance.",
            "Starting funds, monthly contributions and stock warmup are excluded from trading profits. No profitable edge has been established.",
            "Only actually returned partial-chain quotes are used. EOD source dates never become quote-event or historical receipt clocks.",
            "The shared500 monthly gross commitment and100 all-in entry limits include both fees; profits do not replenish commitments.",
            "Unknown provider provenance, contract deliverables, intraday paths and actual fills prevent execution qualification. No brokerage or real orders."],
        "model_calls":0,"network_calls":0,"execution_qualified":False,"research_only":True}
    if len(_json(result).encode())>256*1024:
        raise ValueError("KO journal exceeds its fixed output bound.")
    return result
