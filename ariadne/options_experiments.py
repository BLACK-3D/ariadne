"""Finite causal options experiments; deliberately separate from owner money rules.

This module has no filesystem, network, broker or account-write interface. Quotes
and availability clocks are historical assumptions, not executable prices. Each
configuration is a separate counterfactual with one initial $25,000 contribution.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_DOWN, ROUND_UP, localcontext
from functools import wraps
import hashlib
import json

from . import options_experiment_signals as hypotheses
from .options_recovery import _clock, _day_at, _sessions
from .qqq_data import is_session, last_closed_session, session_close_utc

CENT, FEE, CAPITAL = Decimal('.01'), Decimal('.65'), Decimal('25000')
CLOCK_PROFILES = ('assumed_eod_plus_15m', 'assumed_next_session_0935')
DESIGN_END = '2026-08-01T03:59:59+00:00'
RULE = {
    'schema': 1, 'id': 'causal-options-experiments-v1',
    'signals_sha256': hypotheses.RULE_SHA256,
    'capital': '25000', 'additional_contributions': '0',
    'monthly_gross_commitment': '25000', 'entry_ceiling': '1000',
    'equity_fraction': '.04', 'fee_per_contract_per_side': '.65',
    'grid': {'take_profit': ['2', '5'], 'max_hold_sessions': [5, 10],
             'max_contracts': [1, None]},
    'entry': 'One position; exact contract; stock below100; standard100 multiplier assumed;7-21DTE; expiry in decision NY month;0-5%OTM; positive uncrossed bid/ask; spread<=25%mid; known positive actual side sizes. Rank nearest14DTE, nearestspot, lexicalID, first affordable. No future liquidity screen.',
    'clock': 'Decision at available_at. Fill first eligible later NY source date strictly after NY decision date and observed_at>decision+60s. Same NY day waits. Bad first eligible BUY cancels; no future search. Fill regime is prior information at regular session open. Quantities cannot exceed decision target.',
    'sizing': 'At decision and fill all-in premium rounded UP per100-share contract plus entry fee and reserved exit fee <=min1000,.04flatcash,cash,remainingmonthlygross. Integerq clipped by both supplied side sizes and registered max_contracts. No gross allowance refund on sale.',
    'exit': 'NEW research liquidation policy: positive canonical actual BID, no BUY spread filter on SELL. Latched delayed2x/5x or halfask intent; partial fills minremainingq,knownpositivebid_size once per source frame. No threshold is retroactively executed. Fixed entry-authorized earliest5th/10th following/preexpiry/penultimate entry-month session; residual at deadline permanently held/unranked. No exercise/zero/midpoint rescue.',
    'marks': 'Full remaining depth required for current liquidation NAV, with same-frame sold depth consumed. Missing/unknown/insufficient mark yields nullNAV, not carry-forward or zero. Later valid quotes may recover; terminal cash after actual full closure is knowable. Any unknown NAV makes drawdown unestimated.',
    'month': 'One seed, no topups at split/month. Monthly commitment resets only without funded exposure or hold. An unfunded pending BUY may cancel at month/deadline/study end; no same-frame new entry after cancellation/sale.',
    'selection': 'DESIGN availability<=July31NY end. Freeze selection before full run. Positive completed flat DESIGN books with closed trades in BOTH profiles, ranked by WORSE terminal profit then lexical ID, against cash0. Later already-seen DEVELOPMENT is not an independent holdout. No automatic paper adoption.',
    'research_only': True, 'execution_qualified': False, 'deployable': False,
    'owner_operations': 0, 'model_calls': 0, 'network_calls': 0,
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


RULE_SHA256 = hashlib.sha256(_canonical(RULE)).hexdigest()


def _precise(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext(Context(prec=60)):
            return function(*args, **kwargs)
    return wrapped


def _n(value):
    if type(value) is not str or len(value) > 64:
        raise ValueError('Use bounded decimal strings.')
    number = Decimal(value)
    if not number.is_finite() or abs(number) > Decimal('1e12') or number.as_tuple().exponent < -12:
        raise ValueError('Use finite bounded decimal amounts.')
    return number


def _s(value):
    return format(value, 'f')


def configs():
    result = [{'id': 'cash', 'signal': 'cash', 'take_profit': '2', 'max_hold': 5, 'max_contracts': 1}]
    for signal in hypotheses.CANDIDATES:
        if signal == 'cash':
            continue
        for target in ('2', '5'):
            for hold in (5, 10):
                for quantity in (1, None):
                    result.append({'id': f'{signal}__tp{target}__h{hold}__q{quantity or "budget"}',
                                   'signal': signal, 'take_profit': target,
                                   'max_hold': hold, 'max_contracts': quantity})
    return result


def validate_config(config):
    if (type(config) is not dict or set(config) != {'id', 'signal', 'take_profit', 'max_hold', 'max_contracts'}
            or type(config['id']) is not str or not 1 <= len(config['id']) <= 128
            or config['signal'] not in hypotheses.CANDIDATES or config['take_profit'] not in ('2', '5')
            or type(config['max_hold']) is not int or config['max_hold'] not in (5, 10)
            or not (config['max_contracts'] is None or type(config['max_contracts']) is int and config['max_contracts'] == 1)):
        raise ValueError('Use one fixed bounded experimental configuration.')


def eligible_fill(row, decided_at):
    observed, decision = _clock(row['observed_at']), _clock(decided_at)
    return (_day_at(observed) > _day_at(decision) and observed > decision + timedelta(seconds=60)
            and _clock(row['available_at']) > decision)


def deadline(entryday, expiry, maxhold):
    following = _sessions(entryday, entryday + timedelta(days=30))
    month_end = (date(entryday.year + 1, 1, 1) if entryday.month == 12
                 else date(entryday.year, entryday.month + 1, 1)) - timedelta(days=1)
    month = _sessions(entryday.replace(day=1), month_end)
    before = _sessions(entryday, expiry - timedelta(days=1))
    if maxhold not in (5, 10) or len(following) <= maxhold or len(month) < 2 or not before:
        raise ValueError('No registered disposal session.')
    return min(following[maxhold], before[-1], month[-2])


def _valid_quote(quote):
    bid, ask = _n(quote['bid']), _n(quote['ask'])
    return 0 < bid <= ask


@_precise
def size(cash, committed, quote, max_contracts):
    if not (max_contracts is None or type(max_contracts) is int and max_contracts == 1):
        raise ValueError('Use one contract or the registered budget quantity.')
    cash, committed = _n(cash), _n(committed)
    if not (0 <= committed <= CAPITAL and cash >= 0):
        raise ValueError('Invalid cash or gross commitment.')
    unit = (_n(quote['ask']) * 100).quantize(CENT, rounding=ROUND_UP)
    if unit <= 0:
        raise ValueError('Positive option premium required.')
    ceiling = min(Decimal('1000'), cash * Decimal('.04'), cash, CAPITAL - committed)
    quantity = int(ceiling // (unit + 2 * FEE))
    for side in ('ask_size', 'bid_size'):
        depth = quote.get(side)
        quantity = min(quantity, depth) if type(depth) is int and depth > 0 else 0
    if max_contracts is not None:
        quantity = min(quantity, max_contracts)
    return {'quantity': quantity, 'unit_premium': _s(unit), 'premium': _s(unit * quantity),
            'entry_fee': _s(FEE * quantity), 'reserved_exit_fee': _s(FEE * quantity),
            'all_in_commitment': _s((unit + 2 * FEE) * quantity), 'entry_risk_ceiling': _s(ceiling)}


def _eligible(quote, row, config, state, *, decision_at=None):
    source_day = _day_at(_clock(row['observed_at']))
    decision_day = _day_at(_clock(decision_at or row['available_at']))
    expiry, spot, strike = date.fromisoformat(quote['expiry']), _n(row['underlying_price']), _n(quote['strike'])
    bid, ask = _n(quote['bid']), _n(quote['ask'])
    kind = hypotheses.kind(config['signal'])
    return (quote['type'] == kind and quote['multiplier'] == 100 and 0 < spot < 100
            and 7 <= (expiry - source_day).days <= 21 and expiry.strftime('%Y-%m') == decision_day.strftime('%Y-%m')
            and (spot <= strike <= spot * Decimal('1.05') if kind == 'call' else spot * Decimal('.95') <= strike <= spot)
            and deadline(source_day, expiry, config['max_hold']) > max(source_day, decision_day)
            and _valid_quote(quote) and ask - bid <= (ask + bid) / 2 * Decimal('.25')
            and size(state['cash'], state['monthly_committed'], quote, config['max_contracts'])['quantity'] > 0)


def _gate(config, context):
    expected = hypotheses.regime_label(config['signal'])
    return expected is None or (context is not None and context.get('status') == 'ready' and context.get('label') == expected)


@_precise
def choose(config, history, row, state, context):
    if not _gate(config, context) or not hypotheses.condition(config['signal'], history):
        return None
    candidates = [quote for quote in row['quotes'] if _eligible(quote, row, config, state)]
    day, spot = _day_at(_clock(row['observed_at'])), _n(row['underlying_price'])
    return min(candidates, key=lambda q: (abs((date.fromisoformat(q['expiry']) - day).days - 14),
                   abs(_n(q['strike']) - spot), q['contract_id'])) if candidates else None


def _event(events, row, kind, **values):
    events.append({'type': kind, 'source_date': _day_at(_clock(row['observed_at'])).isoformat(),
                   'observed_at': row['observed_at'], 'available_at': row['available_at'], **values})


def _hold(state, row, events, code):
    state['hold'] = {'code': code, 'source_date': _day_at(_clock(row['observed_at'])).isoformat()}
    _event(events, row, 'held', **state['hold'])


def _context(contexts, index, row, *, fill=False):
    if contexts is None:
        return None
    context = contexts[index] if isinstance(contexts, (list, tuple)) else contexts.get(row['available_at'])
    if context is not None and context.get('status') == 'ready':
        from .briefing import _tz
        day = _day_at(_clock(row['observed_at']))
        bound = (datetime(day.year, day.month, day.day, 8, 30, tzinfo=_tz('America/Chicago')).astimezone(timezone.utc)
                 if fill else _clock(row['available_at']))
        if (context.get('available_at') is None or _clock(context['available_at']) > bound
                or context.get('source_session') != last_closed_session(bound).isoformat()):
            raise ValueError('Regime context uses information unavailable at the decision/session open.')
    return context


@_precise
def run(rows, config, *, decision_contexts=None, fill_contexts=None, checkpoint=lambda: None):
    validate_config(config)
    if hashlib.sha256(_canonical(RULE)).hexdigest() != RULE_SHA256 or hypotheses.RULE_SHA256 != RULE['signals_sha256']:
        raise ValueError('Experimental contract changed.')
    if type(rows) is not list or not 1 <= len(rows) <= 1000:
        raise ValueError('Use1-1000 explicit daily frames.')
    state = {'cash': '25000', 'contributions': '25000', 'monthly_committed': '0', 'month': None,
             'fees': '0', 'realized_pnl': '0', 'position': None, 'pending': None, 'hold': None,
             'entries': 0, 'closed_trades': 0, 'processed_rows': 0}
    events, curve, trades, history = [], [], [], []
    previous_day, previous_clock, root = None, None, None
    for index, row in enumerate(rows):
        checkpoint()
        observed, clock = _clock(row['observed_at']), _clock(row['available_at'])
        day = _day_at(observed)
        if (not is_session(day) or not session_close_utc(day) - timedelta(minutes=30) <= observed <= session_close_utc(day)
                or clock < observed or previous_day is not None and (day <= previous_day or clock <= previous_clock)
                or root is not None and row['underlying_symbol'] != root or _n(row['underlying_price']) <= 0):
            raise ValueError('Unordered, invalid or inconsistent research frame.')
        if len({q['contract_id'] for q in row['quotes']}) != len(row['quotes']):
            raise ValueError('Duplicate exact contract in source frame.')
        gap = previous_day is not None and len(_sessions(previous_day, day)) != 2
        previous_day, previous_clock, root = day, clock, row['underlying_symbol']
        history.append(_n(row['underlying_price']))
        quotes = {q['contract_id']: q for q in row['quotes']}
        busy, sold_depth = bool(state['position'] or state['pending']), 0
        month = day.strftime('%Y-%m')
        if not state['hold'] and gap:
            _hold(state, row, events, 'missing_registered_session')
        if not state['hold'] and month != state['month']:
            if state['position']:
                _hold(state, row, events, 'funded_month_boundary')
            else:
                if state['pending'] and _day_at(_clock(state['pending']['decided_at'])).strftime('%Y-%m') != month:
                    _event(events, row, 'cancelled', reason='unfunded_month_boundary', decision=deepcopy(state['pending']))
                    state['pending'] = None
                state['month'], state['monthly_committed'] = month, '0'
                _event(events, row, 'month_budget_reset', funding_added='0')
        if not state['hold'] and state['position']:
            position = state['position']
            quote, due = quotes.get(position['contract_id']), date.fromisoformat(position['forced_exit_session'])
            if day > due:
                _hold(state, row, events, 'missed_disposal_deadline')
            else:
                pending = state['pending']
                execute = day == due or pending is not None and eligible_fill(row, pending['decided_at'])
                depth = quote.get('bid_size') if quote else None
                if execute and quote and _valid_quote(quote) and type(depth) is int and depth > 0:
                    sold_depth = min(position['quantity'], depth)
                    unit = (_n(quote['bid']) * 100).quantize(CENT, rounding=ROUND_DOWN)
                    gross, fee = unit * sold_depth, FEE * sold_depth
                    profit = gross - fee - (_n(position['unit_premium']) + FEE) * sold_depth
                    state['cash'] = _s(_n(state['cash']) + gross - fee)
                    state['fees'] = _s(_n(state['fees']) + fee)
                    state['realized_pnl'] = _s(_n(state['realized_pnl']) + profit)
                    position['quantity'] -= sold_depth
                    position['realized_pnl'] = _s(_n(position['realized_pnl']) + profit)
                    _event(events, row, 'fill', action='SELL', contract_id=position['contract_id'], quantity=sold_depth,
                           price=quote['bid'], actual_bid_size=depth, fee=_s(fee), gross_credit=_s(gross), net_credit=_s(gross-fee),
                           realized_pnl=_s(profit), decided_at=position['decision_at'] if day == due else pending['decided_at'],
                           reason='precommitted_deadline' if day == due else pending['reason'])
                    if position['quantity'] == 0:
                        trades.append({**deepcopy(position), 'exit_at': row['observed_at'], 'pnl': position['realized_pnl']})
                        state['closed_trades'] += 1
                        state['position'], state['pending'] = None, None
                if day == due and state['position']:
                    _hold(state, row, events, 'unresolved_deadline_quantity')
                elif state['position'] and state['pending'] is None and quote and _valid_quote(quote):
                    bid, entry = _n(quote['bid']), _n(position['entry_ask'])
                    reason = ('take_profit' if bid >= entry * _n(config['take_profit'])
                              else 'half_ask_stop' if bid <= entry / 2 else None)
                    if reason:
                        state['pending'] = {'action': 'SELL', 'contract_id': position['contract_id'], 'decided_at': row['available_at'], 'reason': reason}
                        _event(events, row, 'decision', **deepcopy(state['pending']))
        elif not state['hold'] and state['pending']:
            pending = state['pending']
            if eligible_fill(row, pending['decided_at']):
                quote = quotes.get(pending['contract_id'])
                allowed = _gate(config, _context(fill_contexts, index, row, fill=True))
                if quote is None or not allowed or not _eligible(quote, row, config, state, decision_at=pending['decided_at']):
                    _event(events, row, 'cancelled', reason='first_eligible_fill_rejected', decision=deepcopy(pending))
                    state['pending'] = None
                else:
                    sized = size(state['cash'], state['monthly_committed'], quote, config['max_contracts'])
                    quantity = min(sized['quantity'], pending['target_quantity'])
                    unit, fee = _n(sized['unit_premium']), FEE * quantity
                    commitment = (unit + 2 * FEE) * quantity
                    state['cash'] = _s(_n(state['cash']) - unit * quantity - fee)
                    state['monthly_committed'] = _s(_n(state['monthly_committed']) + commitment)
                    state['fees'] = _s(_n(state['fees']) + fee)
                    state['entries'] += 1
                    state['position'] = {'contract_id': quote['contract_id'], 'type': quote['type'], 'quantity': quantity,
                        'initial_quantity': quantity, 'unit_premium': _s(unit), 'entry_ask': quote['ask'],
                        'entry_at': row['observed_at'], 'decision_at': pending['decided_at'], 'realized_pnl': '0',
                        'entry_risk_commitment': _s(commitment), 'forced_exit_session': deadline(day, date.fromisoformat(quote['expiry']), config['max_hold']).isoformat()}
                    _event(events, row, 'fill', action='BUY', contract_id=quote['contract_id'], quantity=quantity,
                        price=quote['ask'], premium=_s(unit*quantity), fee=_s(fee), reserved_exit_fee=_s(fee),
                        budget_committed=_s(commitment), entry_risk_ceiling=sized['entry_risk_ceiling'],
                        actual_ask_size=quote['ask_size'], actual_bid_size=quote['bid_size'],
                        decision_target_quantity=pending['target_quantity'], decided_at=pending['decided_at'])
                    state['pending'] = None
        elif not state['hold'] and not busy and config['signal'] != 'cash':
            quote = choose(config, history, row, state, _context(decision_contexts, index, row))
            if quote is not None:
                state['pending'] = {'action': 'BUY', 'contract_id': quote['contract_id'], 'decided_at': row['available_at'],
                                    'target_quantity': size(state['cash'], state['monthly_committed'], quote, config['max_contracts'])['quantity']}
                _event(events, row, 'decision', **deepcopy(state['pending']))
        position = state['position']
        nav, mark = _n(state['cash']), None
        if position:
            quote = quotes.get(position['contract_id'])
            depth = quote.get('bid_size') if quote else None
            if (state['hold'] or not quote or not _valid_quote(quote) or type(depth) is not int
                    or depth - sold_depth < position['quantity']):
                nav = None
            else:
                mark = quote['bid']
                nav += (_n(mark)*100).quantize(CENT, rounding=ROUND_DOWN)*position['quantity'] - FEE*position['quantity']
        curve.append({'source_date': day.isoformat(), 'available_at': row['available_at'], 'cash': state['cash'],
            'equity': None if nav is None else _s(nav), 'trading_pnl': None if nav is None else _s(nav-CAPITAL),
            'quantity': position['quantity'] if position else 0, 'marked_bid': mark, 'monthly_committed': state['monthly_committed'],
            'fees': state['fees'], 'held': bool(state['hold'])})
        if (_n(state['cash']) < 0 or not 0 <= _n(state['monthly_committed']) <= CAPITAL
                or state['contributions'] != '25000' or state['entries'] - state['closed_trades'] != int(position is not None)):
            raise ValueError('Experimental funding/quantity invariant failed.')
        state['processed_rows'] += 1
    if state['pending'] and not state['position'] and not state['hold']:
        _event(events, rows[-1], 'cancelled', reason='unfunded_study_end', decision=deepcopy(state['pending']))
        state['pending'] = None
    complete = not (state['hold'] or state['position'] or state['pending'])
    nav_complete = all(item['equity'] is not None for item in curve)
    peak, drop = CAPITAL, Decimal(0)
    if nav_complete:
        for point in curve:
            value = _n(point['equity'])
            peak, drop = max(peak, value), max(drop, max(peak, value)-value)
    return {'config': deepcopy(config), 'state': state, 'events': events, 'curve': curve, 'trades': trades,
            'complete': complete, 'terminal_pnl': _s(_n(state['cash'])-CAPITAL) if complete else None,
            'nav_complete': nav_complete, 'max_drawdown': _s(drop) if nav_complete else None,
            'all_source_frames_accounted': state['processed_rows'] == len(rows),
            'research_only': True, 'execution_qualified': False, 'automatic_adoption': False}


@_precise
def select(reports_by_profile):
    """Call with DESIGN results only; no reference to later prices or check output."""
    if type(reports_by_profile) is not dict or set(reports_by_profile) != set(CLOCK_PROFILES):
        raise ValueError('Both registered clock profiles are required.')
    profiles = []
    for reports in reports_by_profile.values():
        if type(reports) is not list or not 1 <= len(reports) <= len(configs()):
            raise ValueError('Use a bounded nonempty DESIGN report list.')
        index = {}
        for report in reports:
            validate_config(report['config'])
            identifier = report['config']['id']
            if identifier in index:
                raise ValueError('Duplicate configuration in clock profile.')
            if (type(report['complete']) is not bool or type(report['all_source_frames_accounted']) is not bool
                    or type(report['state']['closed_trades']) is not int or report['state']['closed_trades'] < 0
                    or report['state']['contributions'] != '25000'
                    or type(report['curve']) is not list or not report['curve']
                    or any(_clock(point['available_at']) > _clock(DESIGN_END) for point in report['curve'])):
                raise ValueError('Use typed DESIGN-only research reports, never later segment outcomes.')
            if report['complete'] and (any(report['state'][key] is not None for key in ('position', 'pending', 'hold'))
                    or report['terminal_pnl'] is None or _n(report['terminal_pnl']) != _n(report['state']['cash'])-CAPITAL):
                raise ValueError('Completed report contradicts its cash or exposure state.')
            index[identifier] = report
        profiles.append(index)
    if (set(profiles[0]) != set(profiles[1])
            or any(profiles[0][key]['config'] != profiles[1][key]['config'] for key in profiles[0])):
        raise ValueError('Matched unique configuration sets required.')
    scores = {}
    for identifier in profiles[0]:
        cases = [p[identifier] for p in profiles]
        if all(r['complete'] and r['all_source_frames_accounted'] and r['state']['closed_trades'] > 0 and r['terminal_pnl'] is not None for r in cases):
            worst = min(_n(r['terminal_pnl']) for r in cases)
            if worst > 0:
                scores[identifier] = worst
    ranked = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))
    return {'candidate_id': ranked[0] if ranked else 'cash', 'ranked_ids': ranked,
            'worst_terminal_pnl': {key: _s(value) for key, value in scores.items()},
            'cash_control_pnl': '0', 'selection_phase': 'DESIGN_only',
            'independent_holdout': False, 'sample_sufficient_for_edge': False, 'automatic_adoption': False}
