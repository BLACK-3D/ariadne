"""Read-only review of the single verified F EOD paper account.

There is no collector, ledger mutation, strategy ranking or real-order path.
Recorded source dates and durable decision clocks remain distinct. All prices
are the existing lower-evidence model's proxies, never execution qualification.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from .options_eod import (RULE_SHA256, STRATEGY_IDS, STRATEGIES, validate_state,
    summarize, _signal, _day, _day_at, _deadline, _digest as snapshot_digest)
from .options_eod_data import validate_snapshot
from .options_recovery import _number, _text, _clock, _debit, _precision
from .handoff import _identifier

MAX_CAPTURES = 100
MAX_EVENTS_PER_CAPTURE = 16
MAX_FAILED_CHECKS = 20
MAX_OUTPUT = 128 * 1024
PROFILE = "f-eod-read-only-review-v1"
ENGINE_REFERENCE_SHA256 = "22ea96117b89f2b7c38e3853071363449aacac0074bf83a113f4fa36aec3b0f0"
REJECTION_REASONS = ("dte_outside_7_21", "expiry_outside_decision_month",
    "underlying_at_or_above_100", "strike_outside_0_5_percent_otm",
    "entry_over_100_including_fees", "no_future_disposal_session",
    "nonpositive_or_crossed_bid_ask", "spread_above_25_percent_midpoint", "ask_size_below_one")
_FINANCIAL = ("cash", "contributions", "monthly_committed", "monthly_start_equity", "fees",
              "trade_count", "closed_trade_count", "strategy_stats", "month", "prefix_sha256")
_LIMITATIONS = [
    "Read-only saved paper evidence, not current trade advice, a market-data refresh or brokerage execution.",
    "Date-only partial vendor chains have unknown option quote-event clocks and unverified source reliability. Returned-contract diagnostics do not describe the full market.",
    "Observed closed proxy win rate is a descriptive sample, never a future win probability or established profitable edge. Samples of 30 or fewer closed trades are explicitly insufficient; larger samples remain execution-unqualified.",
    "Contributions are excluded from trading P/L. Peak trading-P/L decline is a dollar change between saved marks, not a broker drawdown or the monthly risk-cap guarantee.",
    "The frozen selector, $100 all-in entry ceiling and single shared $500 monthly gross commitment allowance are unchanged. Profits never replenish commitments.",
    "Fees, bids, asks and source-date fills retain the existing model assumptions. Taxes, actual fills and missing intraday outcomes are not supplied by this journal.",
]


def _decode(raw, maximum):
    if type(raw) not in (bytes, str) or len(raw) > maximum:
        raise ValueError("Options review input exceeds its retained evidence bound.")
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Options review evidence has duplicate JSON keys.")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite journal evidence.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Options review evidence is invalid.") from None


def _now(value):
    stamp = datetime.now(timezone.utc) if value is None else value
    return _clock(stamp.isoformat() if isinstance(stamp, datetime) else stamp)


def _object(value, label, required=()):
    if type(value) is not dict or not set(required) <= set(value):
        raise ValueError("Options journal retained " + label + " requires its original JSON object fields; no metrics were produced.")
    return value


def _receipt_object(value):
    receipt = _object(value, "receipt", ("request_id",))
    _identifier(receipt["request_id"])
    if "artifacts" in receipt:
        items = receipt["artifacts"]
        if type(items) is not list or len(items) > 7:
            raise ValueError("Options journal retained artifact manifest must be a bounded list.")
        for item in items:
            _object(item, "artifact", ("path", "bytes", "sha256"))
            if (type(item["path"]) is not str or type(item["bytes"]) is not int or item["bytes"] < 0
                    or type(item["sha256"]) is not str or len(item["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in item["sha256"])):
                raise ValueError("Options journal retained artifact metadata is malformed.")
    return receipt


def _artifact(owner, receipt, name, maximum):
    receipt = _receipt_object(receipt)
    if "artifacts" not in receipt:
        raise ValueError("Options journal lacks its retained artifact manifest.")
    path = "reports/options-recovery/" + receipt["request_id"] + "/" + name
    items = [item for item in receipt["artifacts"] if item.get("path") == path]
    if len(items) != 1:
        raise ValueError("Options journal lacks its exact saved artifact.")
    item = items[0]
    raw = owner._read(path, maximum)
    if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("Options journal evidence changed or disappeared; nothing was recomputed or adopted.")
    return _decode(raw, maximum)


@_precision
def rejection_reason(quote, snapshot, kind, decided_at):
    """One primary rejection reason, equivalent to the frozen entry gates.

    The caller inspects only the hypothesis's side; this is a subset diagnostic,
    not a new selector or authority to enter a contract.
    """
    if kind not in ("call", "put") or RULE_SHA256 != ENGINE_REFERENCE_SHA256:
        raise ValueError("Diagnostics require the frozen F entry-gate reference.")
    day, expiry = _day(snapshot["source_date"]), _day(quote["expiry"])
    clock = _clock(decided_at.isoformat() if isinstance(decided_at, datetime) else decided_at)
    spot, strike = _number(snapshot["underlying_close"]), _number(quote["strike"])
    bid, ask = _number(quote["bid"]), _number(quote["ask"])
    if not 7 <= (expiry-day).days <= 21:return REJECTION_REASONS[0]
    if expiry.strftime("%Y-%m") != _day_at(clock).strftime("%Y-%m"):return REJECTION_REASONS[1]
    if spot >= 100:return REJECTION_REASONS[2]
    strike_ok = spot <= strike <= spot*Decimal("1.05") if kind == "call" else spot*Decimal(".95") <= strike <= spot
    if quote["type"] != kind or not strike_ok:return REJECTION_REASONS[3]
    if _debit(ask*100)+Decimal("1.30") > 100:return REJECTION_REASONS[4]
    if _deadline(day, expiry) <= max(day, _day_at(clock)):return REJECTION_REASONS[5]
    if bid <= 0 or ask <= 0 or bid > ask:return REJECTION_REASONS[6]
    if ask-bid > (ask+bid)/2*Decimal(".25"):return REJECTION_REASONS[7]
    if quote["ask_size"] < 1:return REJECTION_REASONS[8]
    return None


def _diagnostics(capture):
    if capture is None:
        return {"status":"waiting_for_first_capture", "source_date":None, "recorded_at":None,
                "strategies":[], "scope":"Stock baseline references are not option observations or trading results."}
    snapshot, paper, receipt = capture["snapshot"], capture["paper"], capture["receipt"]
    state, clock = paper["state"], receipt["recorded_at"]
    history = state["history"]
    strategies = []
    for identifier in STRATEGY_IDS:
        kind = STRATEGIES[identifier]["type"]
        quotes = [quote for quote in snapshot["quotes"] if quote["type"] == kind]
        reasons = dict.fromkeys(REJECTION_REASONS, 0)
        eligible = 0
        for quote in quotes:
            reason = rejection_reason(quote, snapshot, kind, clock)
            if reason is None:eligible += 1
            else:reasons[reason] += 1
        strategies.append({"id":identifier, "signal":_signal(identifier, history),
                           "inspected_quotes":len(quotes), "eligible_count":eligible,
                           "rejections":reasons})
    if len(history) < 14:stage = "indicator_warmup"
    elif state["position"]:stage = "position_open"
    elif state["pending"]:stage = "pending_"+state["pending"]["action"].lower()
    else:stage = "flat_no_recorded_entry"
    return {"status":stage, "source_date":snapshot["source_date"], "recorded_at":clock,
            "quote_observed_at":None, "actual_decision_month":_day_at(_clock(clock)).strftime("%Y-%m"),
            "warmup_reference_count":len(history), "source_quote_count":len(snapshot["quotes"]),
            "partial_chain":snapshot["partial_chain"], "strategies":strategies,
            "scope":"Indicator conditions and entry-eligible RETURNED quotes at the original recorded decision clock. Counts are not new decisions, complete-market coverage, win probabilities or permission to trade."}


@_precision
def build_review(account, captures, *, now, paused=False, workflow_hold=None, failed_checks=(), omitted_failed_checks=0, revision=0, collection_enabled=None):
    """Pure bounded analysis of already verified captured snapshots and states."""
    clock = _now(now)
    if RULE_SHA256 != ENGINE_REFERENCE_SHA256:
        raise ValueError("The journal requires its reviewed frozen engine reference.")
    if (type(captures) is not list or len(captures) > MAX_CAPTURES
            or type(failed_checks) not in (tuple, list) or len(failed_checks) > MAX_FAILED_CHECKS
            or type(omitted_failed_checks) is not int or not 0 <= omitted_failed_checks <= 10000):
        raise ValueError("Options journal exceeds its fixed capture/check bound.")
    result = {"schema":1, "profile":PROFILE, "status":"not_started", "reviewed_at":clock.isoformat(),
              "revision":revision, "account_id":None, "symbol":None, "rule_sha256":RULE_SHA256,
              "source":None, "performance":None, "diagnostics":_diagnostics(None),
              "holds":{"global_paused":bool(paused), "workflow":deepcopy(workflow_hold), "paper":None, "cap_assurance":None},
              "failed_checks":deepcopy(list(failed_checks)), "omitted_failed_checks":omitted_failed_checks, "limitations":list(_LIMITATIONS),
              "model_calls":0, "network_calls":0, "execution_qualified":False, "research_only":True}
    if account is None:
        if captures:raise ValueError("Captured paper evidence has no original account.")
        return result
    _object(account, "account", ("id", "state"))
    _object(account["state"], "account state")
    if account["state"].get("schema")==4:
        return _stress_review(account,captures,now=clock,paused=paused,workflow_hold=workflow_hold,
            failed_checks=failed_checks,omitted_failed_checks=omitted_failed_checks,revision=revision,
            collection_enabled=collection_enabled)
    result.update(account_id=account["id"],symbol=account["state"].get("symbol"))
    if account["state"].get("schema") != 3:
        if captures:raise ValueError("F captures cannot be merged into another paper profile.")
        result["status"]="unsupported_profile"
        return result
    state = validate_state(account["state"])
    current = summarize(state, now=clock)
    if len(captures) != state["processed_count"]:
        raise ValueError("Journal capture count disagrees with the retained paper cursor.")
    totals = {identifier:{"wins":0,"losses":0,"breakeven":0,"entries":0,"closed_trades":0,
                         "realized_pnl":Decimal(0),"fees":Decimal(0)} for identifier in STRATEGY_IDS}
    curve, months = [], {}
    peak, maximum_drop = Decimal(0), Decimal(0)
    previous_state = None
    for index,capture in enumerate(captures):
        if type(capture) is not dict or set(capture) != {"receipt","snapshot","paper","before_state"}:
            raise ValueError("Use the exact verified journal capture contract.")
        receipt = _receipt_object(capture["receipt"])
        _object(receipt, "capture receipt", ("recorded_at",))
        snapshot = validate_snapshot(capture["snapshot"])
        paper = _object(capture["paper"], "paper report", ("state",))
        before, after = validate_state(capture["before_state"]), validate_state(paper["state"])
        recorded = _clock(receipt["recorded_at"])
        if (receipt.get("account_id") != account["id"] or receipt.get("accepted_snapshot") is not True
                or receipt.get("quote_observed_at") is not None or receipt.get("rule_sha256") != RULE_SHA256
                or receipt.get("source_date") != snapshot["source_date"] or receipt.get("received_at") != snapshot["received_at"]
                or recorded > clock or recorded.isoformat() != after["last_decided_at"]
                or after["processed_count"] != index+1 or before["processed_count"] != index
                or snapshot_digest(snapshot) != after["last_snapshot_sha256"]
                or before["migration"] != state["migration"] or after["migration"] != state["migration"]
                or previous_state is not None and before != previous_state):
            raise ValueError("A journal observation lost its original account, clock or ordered state identity.")
        saved = summarize(after, now=recorded)
        if paper.get("summary") != saved or paper.get("status") != "completed":
            raise ValueError("The saved paper summary does not reconcile to its sealed state.")
        events = paper.get("events")
        if type(events) is not list or len(events) > MAX_EVENTS_PER_CAPTURE:
            raise ValueError("The accepted event list exceeds its bounded journal contract.")
        for event in events:
            if type(event) is not dict:raise ValueError("Malformed saved paper event.")
            if event.get("type") != "fill":continue
            identifier, action = event.get("strategy_id"), event.get("action")
            if (identifier not in STRATEGY_IDS or action not in ("BUY","SELL") or event.get("source_date") != snapshot["source_date"]
                    or event.get("recorded_at") != receipt["recorded_at"] or event.get("simulated") is not True
                    or event.get("research_only") is not True or event.get("execution_qualified") is not False
                    or event.get("quote_observed_at") is not None or _number(event.get("fee")) != Decimal(".65")):
                raise ValueError("A saved fill lost its dated research-only attribution or fee.")
            stats = totals[identifier]
            stats["fees"] += Decimal(".65")
            if action == "BUY":stats["entries"] += 1
            else:
                pnl = _number(event.get("trade_pnl"), signed=True)
                stats["closed_trades"] += 1
                stats["realized_pnl"] += pnl
                stats["wins" if pnl > 0 else "losses" if pnl < 0 else "breakeven"] += 1
        daily = after["last_daily_result"]
        if daily["date"] != snapshot["source_date"] or daily["recorded_at"] != receipt["recorded_at"]:
            raise ValueError("Daily performance lost its source-date/decision-clock identity.")
        previous_daily = before["last_daily_result"]
        prior_equity = _number(previous_daily["equity"],signed=True) if previous_daily and previous_daily["month"] == after["month"] else _number(after["monthly_start_equity"])
        if (_number(daily["daily_pnl"],signed=True) != _number(saved["equity"],signed=True)-prior_equity
                or daily["trading_pnl"] != saved["trading_pnl"] or daily["monthly_pnl"] != saved["monthly_pnl"]):
            raise ValueError("Saved daily P/L does not reconcile after excluding external funding.")
        pnl = _number(saved["trading_pnl"], signed=True)
        peak = max(peak,pnl);maximum_drop=max(maximum_drop,peak-pnl)
        curve.append({"source_date":snapshot["source_date"],"received_at":snapshot["received_at"],
                      "recorded_at":receipt["recorded_at"], "quote_observed_at":None,
                      "equity":saved["equity"],"contributions":saved["contributions"],
                      "trading_pnl":saved["trading_pnl"],"daily_pnl":daily["daily_pnl"],
                      "fees":saved["fees"],"partial_chain":snapshot["partial_chain"]})
        month = _day_at(recorded).strftime("%Y-%m")
        if month != saved["month"]:raise ValueError("Funding month differs from the actual recorded NY decision month.")
        row = months.setdefault(month,{"month":month,"observations":0,"monthly_committed":"0","within_500_gross_cap":True,"funding_added":"0"})
        added = _number(after["contributions"])-_number(before["contributions"])
        if added < 0:raise ValueError("Recorded external funding moved backwards.")
        row.update(observations=row["observations"]+1,monthly_committed=saved["monthly_budget_used"],
                   monthly_pnl=saved["monthly_pnl"],equity=saved["equity"],contributions=saved["contributions"],
                   funding_added=_text(_number(row["funding_added"])+added),
                   last_source_date=snapshot["source_date"],last_recorded_at=receipt["recorded_at"])
        row["within_500_gross_cap"] &= _number(saved["monthly_budget_used"]) <= 500
        previous_state = after
    if previous_state is not None and any(state[key] != previous_state[key] for key in _FINANCIAL):
        raise ValueError("The current account financials differ from its latest verified accepted journal state.")
    strategy_rows = []
    for identifier in STRATEGY_IDS:
        observed, expected = totals[identifier],state["strategy_stats"][identifier]
        if (observed["entries"] != expected["entry_count"] or observed["closed_trades"] != expected["closed_count"]
                or observed["realized_pnl"] != _number(expected["realized_pnl"],signed=True)
                or observed["fees"] != _number(expected["fees"])):
            raise ValueError("Retained fill events do not reconcile to the single account's fixed strategy attribution.")
        strategy_rows.append({"id":identifier, **{key:observed[key] for key in ("entries","closed_trades","wins","losses","breakeven")},
                              "realized_pnl":_text(observed["realized_pnl"]),"fees":_text(observed["fees"]),
                              "monthly_committed":expected["monthly_committed"]})
    closed = sum(row["closed_trades"] for row in strategy_rows)
    wins = sum(row["wins"] for row in strategy_rows)
    performance = {key:current[key] for key in ("cash","equity","contributions","trading_pnl","fees")}
    performance.update(entries=state["trade_count"],closed_trades=closed,wins=wins,
                       losses=sum(row["losses"] for row in strategy_rows),breakeven=sum(row["breakeven"] for row in strategy_rows),
                       observed_win_rate_pct=None if closed == 0 else _text(Decimal(wins)*100/closed),
                       win_rate_scope="Observed closed proxy win rate: wins/all closed positions, including breakeven; not a predicted probability.",
                       sample_status="insufficient_closed_proxy_sample" if closed <= 30 else "larger_sample_still_unverified_edge",
                       max_peak_trading_pnl_drop=_text(maximum_drop),drawdown_units="USD change from peak recorded trading P/L; not monthly loss-cap assurance",
                       ledger_month=current["month"],monthly_budget_used=current["monthly_budget_used"],
                       monthly_budget_remaining=current["monthly_budget_remaining"],cap_assurance=current["cap_assurance"],
                       curve=curve,months=list(months.values()),by_strategy=strategy_rows)
    from .qqq_data import last_closed_session
    latest = captures[-1] if captures else None
    expected_day = last_closed_session(clock).isoformat()
    source = {"last_source_date":None,"actual_received_at":None,"recorded_decision_at":None,"quote_observed_at":None,
              "expected_last_session":expected_day,"latest_matches_expected_session":False,"partial_chain":None,
              "collection_enabled":collection_enabled,"scope":"Saved partial date-only EOD proxies with unknown quote-event clocks; not fresh executable quotes."}
    if latest:
        row=latest["snapshot"]
        source.update(last_source_date=row["source_date"],actual_received_at=row["received_at"],
                      recorded_decision_at=latest["receipt"]["recorded_at"],partial_chain=row["partial_chain"],
                      latest_matches_expected_session=row["source_date"]==expected_day)
    result.update(status="recorded_observations" if captures else "waiting_for_first_capture", source=source,
                  performance=performance,diagnostics=_diagnostics(latest))
    result["holds"].update(paper=deepcopy(state["hold"]),cap_assurance=current["cap_assurance"])
    if workflow_hold or state["hold"]:result["status"]="held"
    if len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode()) > MAX_OUTPUT:
        raise ValueError("Options journal exceeds its fixed response bound.")
    return result


def _stress_review(account,captures,**kwargs):
    """Audit policy states, then reuse the original journal's exact economics.

    This projection removes only policy metadata and a zero-risk activation
    timestamp. It does not replay trades or alter saved artifacts.
    """
    from . import options_stress_eod as stress
    actual=stress.validate_state(account["state"])
    projected=deepcopy(account);projected["state"]=stress.legacy_state(actual)
    converted=[];previous=None
    for item in captures:
        if type(item) is not dict or set(item)!={"receipt","snapshot","paper","before_state"}:
            raise ValueError("Use the exact verified stress journal capture fields.")
        value=deepcopy(item)
        prior=item["before_state"]
        _object(prior,"before stress state")
        _object(item["paper"],"stress paper report",("state","summary","status"))
        value["before_state"]=stress.legacy_state(prior) if prior.get("schema")==4 else validate_state(prior)
        after=item["paper"]["state"]
        _object(after,"after stress state")
        recorded=_clock(item["receipt"]["recorded_at"])
        actual_summary=stress.summarize(after,now=recorded) if after.get("schema")==4 else summarize(after,now=recorded)
        if item["paper"]["summary"]!=actual_summary:
            raise ValueError("The retained stress journal summary disagrees with its sealed state.")
        value["paper"]["state"]=stress.legacy_state(after) if after.get("schema")==4 else validate_state(after)
        if previous is not None and value["before_state"]!=previous:
            activation=prior.get("stress_policy",{}).get("activation",{})
            candidate=deepcopy(value["before_state"]);candidate["last_decided_at"]=previous["last_decided_at"]
            if (prior.get("schema")!=4 or activation.get("old_canonical_state_sha256")!=snapshot_digest(previous)
                or prior["stress_policy"]["context_count"]!=0 or candidate!=previous):
                raise ValueError("The journal policy transition changed economic/source state.")
            value["before_state"]=candidate
        value["paper"]["summary"]=summarize(value["paper"]["state"],now=recorded)
        previous=value["paper"]["state"];converted.append(value)
    result=build_review(projected,converted,**kwargs)
    result.update(profile="experimental_stress_put_eod_paper",rule_sha256=stress.RULE_SHA256,
        policy_id=stress.STRATEGY_ID,policy_activated_at=actual["stress_policy"]["activation"]["activated_at"])
    if result["performance"]:
        rows=result["performance"]["by_strategy"]
        result["performance"]["by_strategy"]=[{**row,"id":stress.STRATEGY_ID} for row in rows if row["id"]==stress.KERNEL_STRATEGY_ID]
    gate=actual["stress_policy"]["context_history"][-1]["gate"] if actual["stress_policy"]["context_history"] else None
    result["diagnostics"]={"status":"stress_policy_only","strategies":[],
        "last_entry_gate":None if gate is None else {key:deepcopy(gate[key]) for key in
            ("stage","allowed","reason","decision_at","source_date","required_source_session","cutoff_at")},
        "scope":"Saved stress-policy gate evidence. Raw ungated call/put diagnostics do not represent this policy's entry authority."}
    result["limitations"].append("Economics reuse the original ledger projection after validating schema4 gates. A zero-risk policy activation is not a trade, contribution or new allowance.")
    if len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode())>MAX_OUTPUT:
        raise ValueError("Stress journal exceeds its fixed response bound.")
    return result


def _review_options(owner, now=None):
    """Verify the retained account/prefix and read a bounded journal snapshot."""
    clock = _now(now)
    with owner.database() as db:
        # A read transaction gives every witness/cache query one WAL snapshot
        # while allowing the collector's independent writer to keep working.
        # query_only also prevents future helpers from seeding state here.
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        revision,_ = owner._meta(db)
        if owner._exists(db) and db.execute("SELECT count(*) FROM opt_accounts WHERE closed_at IS NULL").fetchone()[0] > 1:
            raise ValueError("The single shared options account invariant is ambiguous; no journal metrics were produced.")
        account=owner._account(db,clock)
        paused_row=db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        paused=_decode(paused_row[0],128) if paused_row else False
        if type(paused) is not bool:
            raise ValueError("Options journal retained autonomy pause is not a boolean; no metrics were produced.")
        if account and account["state"].get("schema")==6:
            from .options_bankroll_journal import review
            return review(owner,db,account,now=clock,paused=paused,revision=revision)
        if account and account["state"].get("schema")==5:
            from .options_root_journal import review
            return review(owner,db,account,now=clock,paused=paused,revision=revision)
        if not account or account["state"].get("schema") not in (3,4):
            return build_review(account,[],now=clock,paused=paused,revision=revision)
        owner.eod._audit_prefix(db,account["id"],account["state"],full=True)
        config=owner.eod.config(db)
        initial_row=db.execute("SELECT initial_state FROM opt_eod_migrations WHERE account_id=?",(account["id"],)).fetchone()
        if not initial_row:raise ValueError("Options journal has no preserved initial migration state.")
        before=_decode(initial_row[0],2*1024*1024)
        source_rows=db.execute("SELECT source_date,request_id FROM opt_eod_data ORDER BY source_date LIMIT ?",(MAX_CAPTURES+1,)).fetchall()
        if len(source_rows)>MAX_CAPTURES:raise ValueError("Options journal capture bound exceeded; history was not deleted.")
        captures=[]
        for row in source_rows:
            receipt=_receipt_object(owner.eod._recorded(db,row["request_id"]))
            snapshot=_object(_artifact(owner,receipt,"snapshot.json",32*1024*1024),"snapshot")
            paper=_object(_artifact(owner,receipt,"paper.json",2*1024*1024),"paper report",("state",))
            captures.append({"receipt":receipt,"snapshot":snapshot,"paper":paper,"before_state":before})
            before=paper["state"]
        # Prefix captures were fully audited above. Also verify each bounded
        # saved non-prefix capture report, even when it is older than the
        # displayed failure window. Failed/partial work never becomes P/L.
        verified = set()
        adopted_ids = {row["request_id"] for row in source_rows}
        reports = db.execute("SELECT r.request_id,r.status,substr(r.receipt,1,16001) receipt FROM opt_reports p JOIN opt_runs r USING(request_id) WHERE r.action='eod_collect' ORDER BY p.rowid LIMIT ?",(MAX_CAPTURES+1,)).fetchall()
        if len(reports)>MAX_CAPTURES:raise ValueError("Options journal capture-report bound exceeded; no history was removed.")
        for row in reports:
            if row["request_id"] in adopted_ids:continue
            receipt=_receipt_object(_decode(row["receipt"],16000))
            if row["status"]=="completed":receipt=_receipt_object(owner.eod._recorded(db,row["request_id"]))
            if receipt.get("artifacts"):
                owner.eod.verify(receipt)
                verified.add(row["request_id"])
        condition="status IN ('running','failed') OR (action='eod_collect' AND status='acknowledged')"
        total=db.execute("SELECT count(*) FROM opt_runs WHERE "+condition).fetchone()[0]
        if total>10000:raise ValueError("Options journal receipt-history bound exceeded.")
        rows=db.execute("SELECT request_id,action,status,substr(receipt,1,16001) receipt FROM opt_runs WHERE "+condition+" ORDER BY rowid DESC LIMIT ?",(MAX_FAILED_CHECKS,)).fetchall()
        def failure(row):
            receipt=_receipt_object(_decode(row["receipt"],16000))
            return {"request_id":row["request_id"],"action":row["action"],"status":row["status"],
                   "created_at":receipt.get("created_at"),"reason":str(receipt.get("reason","Interrupted or incomplete work; inspect before acknowledgement."))[:400],
                   "adopted_as_performance":False,"retained_artifacts_verified":row["request_id"] in verified}
        failures=[failure(row) for row in rows]
        held=db.execute("SELECT request_id,action,status,substr(receipt,1,16001) receipt FROM opt_runs WHERE status IN ('running','failed') ORDER BY rowid LIMIT 1").fetchone()
        hold=failure(held) if held else None
        return build_review(account,captures,now=clock,paused=paused,workflow_hold=hold,failed_checks=failures,
                            omitted_failed_checks=max(0,total-len(failures)),revision=revision,collection_enabled=config["enabled"])


def review_options(owner, now=None):
    """Fail closed with a supported integrity error for malformed saved JSON.

    Frozen upstream readers also dereference retained objects. Normalize their
    shape errors at this new read boundary; never repair or return partial P/L.
    """
    try:
        return _review_options(owner, now)
    except (AttributeError, KeyError, TypeError):
        raise ValueError("Options journal retained evidence has a malformed object structure; no metrics were produced or work replayed.") from None
