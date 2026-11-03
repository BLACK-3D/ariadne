"""Passive verified schema7 paper journal; no collection, decisions or repair."""
from copy import deepcopy

from . import options_public_engine as engine
from .handoff import _load
from .options_workflow import _now, _json
from .qqq_data import exchange_day


@engine.precision
def review(owner, db, account, *, now, paused, revision):
    clock = _now(now); state = engine.validate_state(account['state'])
    owner.public_paper.verify_account(db,account['id'],state,full=True)
    current = engine.summarize(state,now=clock)
    rows = db.execute('SELECT request_id FROM opt_public_frames WHERE account_id=? ORDER BY frame_number LIMIT 101',(account['id'],)).fetchall()
    captures = [];curve=[]
    for row in rows:
        receipt=owner.eod._recorded(db,row['request_id']);saved=owner.public_paper.verify(receipt)
        paper=_load(saved['paper.json']);inputs=_load(saved['input.json']);daily=paper['state']['last_daily_result']
        captures.append({'receipt':receipt,'inputs':inputs,'paper':paper})
        curve.append({'date':daily['date'],'recorded_at':daily['recorded_at'],'cash':daily['cash'],
            'equity':daily['equity'],'trading_pnl':daily['trading_pnl'],'fees':daily['fees'],'account_schema':7})
    failures=[]
    total=db.execute("SELECT count(*) FROM opt_runs WHERE status IN ('running','failed')").fetchone()[0]
    for row in db.execute("SELECT action,status,substr(receipt,1,16001) receipt FROM opt_runs WHERE status IN ('running','failed') ORDER BY rowid DESC LIMIT 12"):
        if len(row['receipt'].encode())>16000:raise ValueError('Options failure receipt exceeds its bound.')
        receipt=_load(row['receipt']);failures.append({'request_id':receipt['request_id'],'action':row['action'],'status':row['status'],
            'created_at':receipt.get('created_at'),'reason':str(receipt.get('reason','Interrupted work requires inspection.'))[:400],
            'adopted_as_performance':False,'retained_artifacts_verified':False})
    held=failures[-1] if failures else None
    source={'last_source_date':None,'actual_received_at':None,'recorded_decision_at':None,'quote_observed_at':None,
        'collection_enabled':False,'partial_chain':True,'scope':'Saved explicit Public contract snapshots only; no current market request or automatic scheduler.'}
    if captures:
        latest=captures[-1];received=latest['receipt']['source_received_at']
        clocks=[b['quote_at'] for b in latest['inputs']['books'] if b['quote_at'] is not None]
        source.update(last_source_date=exchange_day(engine.gate._clock(received)).isoformat(),actual_received_at=received,
            recorded_decision_at=latest['inputs']['decided_at'],quote_observed_at=min(clocks) if clocks else None)
    performance={k:current[k] for k in ('cash','equity','trading_pnl','contributions','fees','month')}
    performance.update(entries=state['trade_count'],closed_trades=state['closed_trade_count'],contract_entries=state['contract_entry_count'],
        contract_exits=state['contract_exit_count'],legacy_closed_trade_count=state['ancestor']['closed_trade_count'],
        new7_closed_trade_count=state['closed_trade_count']-state['ancestor']['closed_trade_count'],
        new7_realized_pnl=state['realized_pnl'],observed_win_rate_pct=None,sample_status='paper_only_no_proven_edge',
        monthly_budget_used=state['monthly_committed'],monthly_budget_remaining=current['monthly_budget_remaining'],
        cap_assurance=True,curve=curve,by_strategy=[],mark_basis=current['mark_basis'])
    result={'schema':1,'account_schema':7,'profile':'public_strict_book_paper','status':'held' if held else 'recorded_observations' if rows else 'waiting_for_first_capture',
        'reviewed_at':clock.isoformat(),'revision':revision,'account_id':account['id'],'symbol':None,'rule_sha256':engine.RULE_SHA256,
        'bankroll_total':'25000','monthly_gross_commitment_cap':'25000','all_in_entry_cap':'1000','automatic_topups':False,
        'source':source,'performance':performance,'diagnostics':{'status':'saved_strict_public_paper','strategies':[],
            'scope':'Explicit owner-reviewed candidates only; no strategy promotion inferred from quote access.'},
        'holds':{'global_paused':paused,'workflow':held,'paper':None,'cap_assurance':True},'failed_checks':failures,
        'omitted_failed_checks':max(0,total-len(failures)), 'limitations':[
            'One shared25000 bankroll, no topups; monetary results include retained legacy history and separately labeled schema7 results.',
            'Owner supplemental metadata is distinct from authenticated Public quote transport; unavailable qualification blocks new entries.',
            'Saved top-of-book paper fills are simulated; displayed size can vanish and does not prove a broker execution.',
            'Saved marks may be stale; no current liquidation guarantee or proven profitable strategy.'],
        'model_calls':0,'network_calls':0,'execution_qualified':False,'research_only':True}
    if len(_json(result).encode())>256*1024:raise ValueError('Public paper journal exceeds its fixed output bound.')
    return result
