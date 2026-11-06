"""Finite development-only stress audit of the unchanged options experiment engine.

No files, sockets, models, accounts or broker interface live here. A trusted caller
may stream each full engine report through report_sink; that caller owns storage
and its budget. Stressed quotes are hypothetical views, never recorded quotes.
"""
from copy import deepcopy
from datetime import date
from decimal import Context, Decimal, localcontext
from functools import wraps
import hashlib
import json

from . import options_experiments as engine
from .options_recovery import _clock, _day_at

SCENARIOS = ('recorded_base', 'modeled_adverse_cent', 'modeled_half_depth', 'modeled_combined')
MAX_FRAMES, MAX_QUOTES, MAX_INPUT_BYTES = 1000, 200000, 32 * 1024 * 1024
_FRAME = frozenset(('observed_at', 'available_at', 'underlying_symbol', 'underlying_price', 'quotes'))
_QUOTE = frozenset(('contract_id', 'type', 'expiry', 'strike', 'multiplier', 'bid', 'ask', 'bid_size', 'ask_size', 'volume', 'open_interest'))
_CONFIGS = engine.configs()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


GRID_SHA256 = _hash(_CONFIGS)
RULE = {
    'schema': 1, 'id': 'fixed-options-development-robustness-v1',
    'engine_rule_sha256': engine.RULE_SHA256, 'grid_sha256': GRID_SHA256,
    'configurations': 89, 'clock_profiles': list(engine.CLOCK_PROFILES),
    'scenarios': list(SCENARIOS),
    'price_stress': 'Exactly subtract0.01USD from bid and add0.01USD to ask; no clamp, midpoint or rescue. Nonpositive modeled bid is unavailable under unchanged engine gates.',
    'depth_stress': 'Each known nonnegative integer bid_size and ask_size is floor-divided by2. Unknown staysnull; zero stayszero. No inference from volume/open interest.',
    'execution': 'Unchanged engine sizing, fees, next-session timing, partial exits, marks and holds; each case is a separate25000 counterfactual, never pooled capital.',
    'screen': {'minimum_fully_closed_episodes': 30, 'minimum_distinct_closed_exit_months': 3, 'all_nav_known': True, 'every_clock_and_scenario': True},
    'screen_meaning': 'Descriptive count/coverage/NAV/loss-screen adequacy only; does not require positive PnL and is not an edge test, validation, reliability proof or promotion threshold.',
    'owner_loss_preference': {'initial_bankroll': '25000', 'maximum_total_loss': '20000', 'equity_threshold': '5000', 'active_engine_enforcement': False},
    'loss_screen': 'Observed-frame analysis only: conservative equity floor=cash-remainingquantity*0.65 exit fee; known NAV below5000 is a breach; floor below5000 with unknown NAV is stop_unverifiable. Never a guaranteed future stop.',
    'winner_removal': 'Arithmetic removal of net PnL of largest1/2 positive fully closed episodes, not a cash/quantity trajectory replay. Monthly realization separately includes partial SELL fills.',
    'phase': 'already_seen_development_stress_audit',
    'baseline_reuse': False, 'all_paths_recomputed': True,
    'independent_holdout': False, 'sample_sufficient_for_edge': False,
    'research_only': True, 'execution_qualified': False, 'deployable': False,
    'automatic_adoption': False, 'owner_operations': 0, 'model_calls': 0, 'network_calls': 0,
}
RULE_SHA256 = _hash(RULE)


def _precise(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext(Context(prec=60)):
            return function(*args, **kwargs)
    return wrapped


def _require(value, message):
    if not value:
        raise ValueError(message)


def _verify():
    _require(_hash(RULE) == RULE_SHA256 and _hash(_CONFIGS) == GRID_SHA256
             and len(_CONFIGS) == 89 and _hash(engine.configs()) == GRID_SHA256
             and engine.RULE_SHA256 == RULE['engine_rule_sha256']
             and _hash(engine.RULE) == engine.RULE_SHA256
             and tuple(RULE['clock_profiles']) == engine.CLOCK_PROFILES
             and tuple(RULE['scenarios']) == SCENARIOS, 'Frozen robustness or engine contract changed.')


def _plain(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop(); count += 1
        _require(count <= 3000000 and depth <= 20, 'Input structure exceeds the finite audit bound.')
        kind = type(item)
        if kind is dict:
            _require(all(type(key) is str and len(key) <= 128 for key in item), 'Plain bounded string keys required.')
            stack.extend((child, depth + 1) for child in item.values())
        elif kind in (list, tuple):
            stack.extend((child, depth + 1) for child in item)
        elif kind is str:
            _require(len(item) <= 4096, 'Input text exceeds the audit bound.')
        elif kind is int:
            _require(abs(item) <= 10**12, 'Input integer exceeds the audit bound.')
        else:
            _require(item is None or kind is bool, 'Plain JSON data without floating prices required.')
    _require(len(canonical(value)) <= MAX_INPUT_BYTES, 'Input exceeds32MiB.')


def _frames(rows):
    _plain(rows)
    _require(type(rows) is list and 1 <= len(rows) <= MAX_FRAMES, 'Use1-1000 explicit daily frames.')
    count = 0
    for row in rows:
        _require(type(row) is dict and set(row) == _FRAME and type(row['quotes']) is list
                 and len(row['quotes']) <= 25000, 'Exact bounded research frames required.')
        for quote in row['quotes']:
            _require(type(quote) is dict and set(quote) == _QUOTE, 'Exact research quote schema required.')
            for key in ('bid_size', 'ask_size', 'volume', 'open_interest'):
                value = quote[key]
                _require(value is None or type(value) is int and 0 <= value <= 10**9, 'Unknown or actual nonnegative integer counts required.')
            bid, ask = engine._n(quote['bid']), engine._n(quote['ask'])
            _require(0 <= bid <= ask, 'Original crossed/negative quotes cannot be repaired by a stress view.')
        count += len(row['quotes'])
        _require(count <= MAX_QUOTES, 'Quote count exceeds the finite audit bound.')
    return count


@_precise
def scenario_rows(rows, scenario):
    """Return a detached hypothetical view; source prices, sizes and clocks survive."""
    _verify()
    _require(type(scenario) is str and scenario in SCENARIOS, 'Choose one frozen scenario.')
    _frames(rows)
    result = deepcopy(rows)
    for row in result:
        for quote in row['quotes']:
            if scenario in ('modeled_adverse_cent', 'modeled_combined'):
                quote['bid'] = format(engine._n(quote['bid']) - Decimal('.01'), 'f')
                quote['ask'] = format(engine._n(quote['ask']) + Decimal('.01'), 'f')
            if scenario in ('modeled_half_depth', 'modeled_combined'):
                for key in ('bid_size', 'ask_size'):
                    if quote[key] is not None:
                        quote[key] //= 2
    return result


def _count(value):
    _require(type(value) is int and value >= 0, 'Exact nonnegative engine count required.')
    return value


@_precise
def summarize(report):
    """Summarize one unchanged-engine result; held books never gain terminal PnL."""
    _plain(report)
    engine.validate_config(report['config'])
    state, curve, trades, events = (report[key] for key in ('state', 'curve', 'trades', 'events'))
    _require(type(curve) is list and curve and type(trades) is list and type(events) is list,
             'Nonempty engine curve and detached ledger required.')
    _require(type(report['complete']) is bool and type(report['nav_complete']) is bool
             and report['all_source_frames_accounted'] is True and report['research_only'] is True
             and report['execution_qualified'] is False and report['automatic_adoption'] is False
             and state['contributions'] == '25000' and _count(state['processed_rows']) == len(curve)
             and _count(state['closed_trades']) == len(trades), 'Engine result contradicts its finite research contract.')
    entries, closed = _count(state['entries']), len(trades)
    _require(entries - closed == int(state['position'] is not None), 'Engine episode counts contradict exposure.')
    complete = not (state['hold'] or state['position'] or state['pending'])
    _require(report['complete'] == complete, 'Completion contradicts unresolved exposure or hold.')
    terminal = engine._n(report['terminal_pnl']) if complete else None
    _require((complete and terminal == engine._n(state['cash']) - engine.CAPITAL)
             or (not complete and report['terminal_pnl'] is None), 'Terminal PnL cannot adopt an unresolved position.')
    known = sum(point['equity'] is not None for point in curve)
    nav_complete = known == len(curve)
    _require(report['nav_complete'] == nav_complete, 'Known NAV flag contradicts the complete curve.')
    floors = [engine._n(point['cash']) - Decimal(_count(point['quantity'])) * engine.FEE for point in curve]
    known_equities = [engine._n(point['equity']) for point in curve if point['equity'] is not None]
    known_loss_breach = any(value < Decimal('5000') for value in known_equities)
    stop_unverifiable = any(floor < Decimal('5000') and point['equity'] is None for floor, point in zip(floors, curve))
    loss_screen = not known_loss_breach and not stop_unverifiable
    peak, drawdown = engine.CAPITAL, Decimal(0)
    if nav_complete:
        for point in curve:
            equity = engine._n(point['equity'])
            peak = max(peak, equity); drawdown = max(drawdown, peak - equity)
        _require(report['max_drawdown'] is not None and engine._n(report['max_drawdown']) == drawdown,
                 'Engine drawdown contradicts the known NAV curve.')
    else:
        _require(report['max_drawdown'] is None, 'Unknown NAV cannot support a measured drawdown.')
    monthly = {date.fromisoformat(point['source_date']).strftime('%Y-%m'): Decimal(0) for point in curve}
    realized, event_fees = Decimal(0), Decimal(0)
    for event in events:
        if event['type'] == 'fill':
            event_fees += engine._n(event['fee'])
            if event['action'] == 'SELL':
                day = date.fromisoformat(event['source_date'])
                _require(day.isoformat() == event['source_date'], 'Exact SELL source date required.')
                pnl = engine._n(event['realized_pnl']); month = day.strftime('%Y-%m')
                monthly[month] = monthly.get(month, Decimal(0)) + pnl; realized += pnl
    _require(realized == engine._n(state['realized_pnl']) and event_fees == engine._n(state['fees']),
             'Realized PnL or fees contradict SELL/fill events.')
    episode_pnl = [engine._n(trade['pnl']) for trade in trades]
    closed_pnl = sum(episode_pnl, Decimal(0))
    open_realized = engine._n(state['position']['realized_pnl']) if state['position'] else Decimal(0)
    _require(closed_pnl + open_realized == realized, 'Closed episodes and residual partial realization contradict the ledger.')
    if complete:
        _require(closed_pnl == terminal, 'Completed terminal cash contradicts closed-episode PnL.')
    positive = sorted((pnl for pnl in episode_pnl if pnl > 0), reverse=True)
    gross_positive = sum(positive, Decimal(0))
    exit_months = sorted({_day_at(_clock(trade['exit_at'])).strftime('%Y-%m') for trade in trades})
    screen = (report['config']['signal'] != 'cash' and complete and closed >= 30
              and len(exit_months) >= 3 and nav_complete and loss_screen)
    minus1, minus2 = sum(positive[:1], Decimal(0)), sum(positive[:2], Decimal(0))
    return {
        'status': 'held' if not complete else 'no_trade' if entries == 0 else 'complete',
        'complete': complete, 'hold_code': state['hold']['code'] if state['hold'] else None,
        'terminal_pnl': None if terminal is None else format(terminal, 'f'),
        'entries': entries, 'closed_episodes': closed, 'fees': state['fees'],
        'source_frames': len(curve), 'known_nav_frames': known,
        'known_nav_fraction': format(Decimal(known) / len(curve), 'f'),
        'minimum_conservative_equity_floor': format(min(floors), 'f'),
        'minimum_known_nav': format(min(known_equities), 'f') if known_equities else None,
        'known_nav_loss_budget_breach': known_loss_breach, 'stop_unverifiable': stop_unverifiable,
        'loss_budget_proven_by_conservative_floor': min(floors) >= Decimal('5000'),
        'loss_budget_screen_passed': loss_screen, 'active_total_loss_stop_enforced': False,
        'all_nav_known': nav_complete, 'max_drawdown_dollars': format(drawdown, 'f') if nav_complete else None,
        'monthly_exit_realized_pnl': {month: format(pnl, 'f') for month, pnl in sorted(monthly.items())},
        'monthly_pnl_basis': 'SELL_realization_including_partial_exits; zero_for_source_months_without_SELL; not_monthly_NAV_return',
        'realized_pnl_including_open_partial_exits': format(realized, 'f'),
        'closed_episode_pnl': format(closed_pnl, 'f'),
        'closed_episode_exit_months': exit_months, 'closed_episode_exit_month_count': len(exit_months),
        'positive_closed_episodes': len(positive),
        'largest_positive_episode_pnl': format(positive[0], 'f') if positive else None,
        'largest_winner_share_of_positive_closed_pnl': format(positive[0] / gross_positive, 'f') if positive else None,
        'largest_winner_share_of_net_closed_pnl': format(positive[0] / closed_pnl, 'f') if positive and closed_pnl > 0 else None,
        'closed_episode_pnl_without_largest_1_positive': format(closed_pnl - minus1, 'f'),
        'closed_episode_pnl_without_largest_2_positive': format(closed_pnl - minus2, 'f'),
        'terminal_pnl_without_largest_1_positive': format(terminal - minus1, 'f') if complete else None,
        'terminal_pnl_without_largest_2_positive': format(terminal - minus2, 'f') if complete else None,
        'winner_removal_is_arithmetic_not_replay': True,
        'evidence_screen_passed': screen, 'evidence_screen_is_proof': False,
        'independent_holdout': False, 'sample_sufficient_for_edge': False,
        'research_only': True, 'execution_qualified': False, 'automatic_adoption': False,
    }


@_precise
def audit(rows_by_profile, *, decision_contexts_by_profile=None,
          fill_contexts_by_profile=None, checkpoint=lambda: None, report_sink=None):
    """Run all712 fixed paths. A trusted sink may persist each full engine report.

    Inputs must already be source-validated by the caller. This layer checks their
    bounded structural/economic matching, not provider authenticity. The sink is
    called once per path and must neither mutate the supplied report nor retry.
    Callback/checkpoint exceptions stop the audit; no partial result is adopted.
    """
    _verify()
    _require(callable(checkpoint) and (report_sink is None or callable(report_sink)), 'Trusted callbacks must be callable.')
    _require(type(rows_by_profile) is dict and set(rows_by_profile) == set(engine.CLOCK_PROFILES),
             'Both exact registered clock profiles are required.')
    for contexts in (decision_contexts_by_profile, fill_contexts_by_profile):
        _require(contexts is None or type(contexts) is dict and set(contexts) == set(engine.CLOCK_PROFILES),
                 'Context maps require both exact clock profiles.')
        _plain(contexts)
    inputs, sources, economics = {}, {}, []
    for profile in engine.CLOCK_PROFILES:
        checkpoint()
        rows = rows_by_profile[profile]; _frames(rows)
        inputs[profile] = deepcopy(rows); sources[profile] = _hash(rows)
        economics.append(_hash([{key: value for key, value in row.items() if key != 'available_at'} for row in rows]))
    _require(economics[0] == economics[1], 'Clock profiles must contain identical ordered source economics.')
    decisions, fills = deepcopy(decision_contexts_by_profile), deepcopy(fill_contexts_by_profile)
    cases, views, by_config = [], [], {config['id']: [] for config in _CONFIGS}
    for profile in engine.CLOCK_PROFILES:
        for scenario in SCENARIOS:
            checkpoint()
            rows = scenario_rows(inputs[profile], scenario); view_sha = _hash(rows)
            price_model = scenario in ('modeled_adverse_cent', 'modeled_combined')
            depth_model = scenario in ('modeled_half_depth', 'modeled_combined')
            views.append({'clock_profile': profile, 'scenario': scenario, 'rows_sha256': view_sha,
                          'original_rows_sha256': sources[profile], 'prices_modeled': price_model,
                          'depth_modeled': depth_model, 'source_frames': len(rows)})
            for config in _CONFIGS:
                checkpoint()
                report = engine.run(rows, deepcopy(config),
                    decision_contexts=decisions[profile] if decisions is not None else None,
                    fill_contexts=fills[profile] if fills is not None else None, checkpoint=checkpoint)
                digest = _hash(report); summary = summarize(report)
                if report_sink is not None:
                    report_sink(profile, scenario, deepcopy(config), report)
                    _require(_hash(report) == digest, 'Report sink mutated the engine evidence.')
                case = {'clock_profile': profile, 'scenario': scenario, 'config_id': config['id'],
                        'input_rows_sha256': view_sha, 'engine_report_sha256': digest,
                        'prices_modeled': price_model, 'depth_modeled': depth_model,
                        'engine_actual_fields_basis': 'scenario_supplied_values_not_execution_evidence',
                        **summary}
                cases.append(case); by_config[config['id']].append(case)
            _require(_hash(rows) == view_sha, 'Engine or sink mutated a scenario view.')
    aggregates = []
    for config in _CONFIGS:
        group = by_config[config['id']]; base = [case for case in group if case['scenario'] == 'recorded_base']
        all_complete, base_complete = all(case['complete'] for case in group), all(case['complete'] for case in base)
        worst = min(engine._n(case['terminal_pnl']) for case in group) if all_complete else None
        base_worst = min(engine._n(case['terminal_pnl']) for case in base) if base_complete else None
        aggregates.append({'config': deepcopy(config), 'case_count': len(group),
            'all_cases_complete': all_complete, 'recorded_base_cases_complete': base_complete,
            'recorded_base_worst_terminal_pnl': format(base_worst, 'f') if base_worst is not None else None,
            'worst_terminal_pnl': format(worst, 'f') if worst is not None else None,
            'worst_terminal_pnl_without_largest_1_positive': format(min(engine._n(case['terminal_pnl_without_largest_1_positive']) for case in group), 'f') if all_complete else None,
            'worst_terminal_pnl_without_largest_2_positive': format(min(engine._n(case['terminal_pnl_without_largest_2_positive']) for case in group), 'f') if all_complete else None,
            'minimum_closed_episodes': min(case['closed_episodes'] for case in group),
            'minimum_closed_exit_months': min(case['closed_episode_exit_month_count'] for case in group),
            'all_nav_known': all(case['all_nav_known'] for case in group),
            'minimum_conservative_equity_floor': format(min(engine._n(case['minimum_conservative_equity_floor']) for case in group), 'f'),
            'loss_budget_screen_passed': all(case['loss_budget_screen_passed'] for case in group),
            'known_nav_loss_budget_breach': any(case['known_nav_loss_budget_breach'] for case in group),
            'stop_unverifiable': any(case['stop_unverifiable'] for case in group),
            'active_total_loss_stop_enforced': False,
            'positive_in_every_case': worst is not None and worst > 0,
            'evidence_screen_passed': all(case['evidence_screen_passed'] for case in group),
            'sample_sufficient_for_edge': False, 'automatic_adoption': False})
    noncash = [item for item in aggregates if item['config']['signal'] != 'cash']
    def ranked(items, key):
        return [item['config']['id'] for item in sorted(items, key=lambda item: (-engine._n(item[key]), item['config']['id']))]
    _require(all(_hash(rows_by_profile[profile]) == sources[profile] for profile in engine.CLOCK_PROFILES)
             and decisions == decision_contexts_by_profile and fills == fill_contexts_by_profile,
             'Source inputs or context changed during the audit.')
    _verify()
    return {'schema': 1, 'kind': 'fixed_options_development_robustness_audit',
        'rule': deepcopy(RULE), 'rule_sha256': RULE_SHA256, 'grid_sha256': GRID_SHA256,
        'engine_rule_sha256': engine.RULE_SHA256,
        'original_rows_sha256': sources, 'economic_rows_sha256': economics[0],
        'decision_contexts_sha256': _hash(decision_contexts_by_profile), 'fill_contexts_sha256': _hash(fill_contexts_by_profile),
        'input_views': views, 'case_count': len(cases), 'configuration_count': len(aggregates),
        'cases': cases, 'aggregates': aggregates,
        'recorded_profit_leaders': ranked([item for item in noncash if item['recorded_base_worst_terminal_pnl'] is not None and engine._n(item['recorded_base_worst_terminal_pnl']) > 0], 'recorded_base_worst_terminal_pnl'),
        'all_scenario_profit_leaders': ranked([item for item in noncash if item['positive_in_every_case']], 'worst_terminal_pnl'),
        'evidence_screen_ids': [item['config']['id'] for item in noncash if item['evidence_screen_passed']],
        'cash_control_case_count': len(by_config['cash']),
        'all_paths_recomputed': True, 'independent_holdout': False,
        'sample_sufficient_for_edge': False, 'research_only': True, 'execution_qualified': False,
        'deployable': False, 'automatic_adoption': False, 'owner_operations': 0, 'network_calls': 0, 'model_calls': 0}
