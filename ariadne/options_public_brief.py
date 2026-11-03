"""Pure saved Public paper-account presentation; never fetches or schedules data."""
from datetime import datetime, timezone
import re


def view(options):
    if type(options) is not dict:
        return None
    paper = options.get('paper')
    summary = paper.get('summary') if type(paper) is dict else None
    if type(summary) is not dict or type(summary.get('schema')) is not int or summary['schema'] != 7:
        return None
    def amount(value):
        return value if type(value) is str and len(value) <= 44 and re.fullmatch(r'-?(?:0|[1-9][0-9]{0,20})(?:\.[0-9]{1,8})?', value) else None
    def stamp(value):
        if type(value) is not str or len(value) > 40:
            return None
        try:
            clock = datetime.fromisoformat(value.replace('Z','+00:00'))
            return clock.astimezone(timezone.utc).isoformat() if clock.utcoffset() is not None else None
        except (ValueError, OverflowError):
            return None
    def day(value):
        if type(value) is not str or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):
            return None
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            return None
    daily = summary.get('last_daily_result') if type(summary.get('last_daily_result')) is dict else {}
    position = summary.get('position') if type(summary.get('position')) is dict else {}
    return {'schema':7, 'source_id':'public', 'profile':'strict_book_public_paper', 'simulated_only':True,
        **{key:amount(summary.get(key)) for key in ('cash','equity','contributions','trading_pnl','realized_pnl',
            'monthly_pnl','fees','monthly_budget_used','monthly_budget_remaining')},
        'trade_count':summary.get('trade_count') if type(summary.get('trade_count')) is int and 0<=summary['trade_count']<=1000000000 else None,
        'closed_trade_count':summary.get('closed_trade_count') if type(summary.get('closed_trade_count')) is int and 0<=summary['closed_trade_count']<=1000000000 else None,
        'held':bool(options.get('hold') or summary.get('hold')), 'cap_qualified':summary.get('cap_qualified') is True,
        'total_bankroll':'25000', 'monthly_cap':'25000', 'entry_all_in_cap':'1000', 'entry_equity_fraction':'0.04',
        'automatic_topups':False, 'shared_portfolio':True,
        'position':position.get('contract_id') if type(position.get('contract_id')) is str and re.fullmatch(r'[A-Z][A-Z0-9]{0,5}[0-9]{6}[CP][0-9]{8}',position['contract_id']) else None,
        'latest_result_date':day(daily.get('date')), 'result_recorded_at':stamp(daily.get('recorded_at')),
        'daily_pnl':None, 'execution_qualified':False, 'fresh_at_report_generation':False,
        'source_status':'saved_result_not_current_quote' if daily else 'waiting_for_first_public_result',
        'automatic_collection_enabled':False,
        'note':'Saved strict-book paper accounting. Last saved bid marks may be stale; contributions are not returns. No current quote, brokerage execution, daily P/L or proven strategy is inferred.'}


def markdown(options):
    value = view(options)
    if value is None:
        return []
    def show(key):
        return value[key] if value[key] is not None else 'not measured'
    lines = ['', '## Options: Public data / $25,000 total simulated bankroll', '',
        'SIMULATED ONLY. One existing shared bankroll; no automatic top-ups, real orders or exercise.',
        '- Entry ceiling: min($1,000, 4% of equity, available cash, remaining shared monthly gross commitment), including both fee sides. The $25,000 monthly commitment ceiling adds no cash; same-month proceeds do not replenish it.',
        '- Saved cash: $'+show('cash')+'. Saved equity: $'+show('equity')+'. Total contributions: $'+show('contributions')+'.',
        '- Cumulative trading P/L excluding contributions: $'+show('trading_pnl')+'. Realized P/L since Public activation: $'+show('realized_pnl')+'. Cumulative fees: $'+show('fees')+'.',
        '- Shared monthly committed: $'+show('monthly_budget_used')+'; remaining: $'+show('monthly_budget_remaining')+'.',
        '- Entries: '+str(show('trade_count'))+'; closed positions: '+str(show('closed_trade_count'))+'. Position: '+(value['position'] or 'none')+'.',
        '- '+value['note'],
        '- Collection is explicit and authenticated; no automatic Public polling is enabled. Offline briefs read only the saved ledger.']
    if value['result_recorded_at']:
        lines.append('- Latest saved paper result: '+show('latest_result_date')+'; recorded '+show('result_recorded_at')+'. Daily P/L is not measured by this cumulative snapshot.')
    else:
        lines.append('- No completed Public paper result is available; starting cash is not profit.')
    if value['held']:
        lines.append('- HELD: inspect the protected account/briefing receipt. No uncertain work was replayed.')
    return lines
