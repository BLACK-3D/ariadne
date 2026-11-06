"""Synthetic pure research tests; no acquired prices, account, network or models."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, ROUND_UP, localcontext
from pathlib import Path
import hashlib
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_experiments as engine
from ariadne import options_robustness as audit
from ariadne.qqq_data import session_dates, session_close_utc

D = Decimal


def frames(*, delayed=False, depth=100, end='2026-06-17'):
    result = []
    for day in session_dates(date(2026, 5, 11), date.fromisoformat(end)):
        observed = session_close_utc(day)
        available = observed + timedelta(minutes=15)
        if delayed:
            following = session_dates(day + timedelta(days=1), day + timedelta(days=10))[0]
            available = datetime(following.year, following.month, following.day, 13, 35, tzinfo=timezone.utc)
        high_bid = day >= date(2026, 6, 3)
        result.append({'observed_at': observed.isoformat(), 'available_at': available.isoformat(),
            'underlying_symbol': 'KO', 'underlying_price': '20' if day < date(2026, 6, 1) else '19.8',
            'quotes': [{'contract_id': 'KO260618P00019500', 'type': 'put', 'expiry': '2026-06-18',
                        'strike': '19.5', 'multiplier': 100, 'bid': '2' if high_bid else '.95',
                        'ask': '2.1' if high_bid else '1', 'bid_size': depth, 'ask_size': depth,
                        'volume': 100000, 'open_interest': None}]})
    return result


def profiles(*, end='2026-06-17', depth=100):
    return {engine.CLOCK_PROFILES[0]: frames(end=end, depth=depth),
            engine.CLOCK_PROFILES[1]: frames(end=end, depth=depth, delayed=True)}


def config():
    return next(item for item in engine.configs() if item['signal'] == 'raw_breakdown_put'
                and item['max_hold'] == 5 and item['take_profit'] == '2' and item['max_contracts'] == 1)


def synthetic_episodes(pnls, months=None):
    """Exact accounting-summary fixture; values are deliberately fictional."""
    months = months or ['2026-06'] * len(pnls)
    cash, peak, drawdown, events, trades, curve = D(25000), D(25000), D(0), [], [], []
    for index, (pnl, month) in enumerate(zip(pnls, months)):
        day = date.fromisoformat(month + '-03')
        cash += D(pnl); peak = max(peak, cash); drawdown = max(drawdown, peak-cash)
        events.extend([{'type': 'fill', 'action': 'BUY', 'fee': '.65'},
                       {'type': 'fill', 'action': 'SELL', 'fee': '.65', 'source_date': day.isoformat(), 'realized_pnl': pnl}])
        trades.append({'pnl': pnl, 'exit_at': day.isoformat() + 'T20:00:00+00:00'})
        curve.append({'source_date': day.isoformat(), 'cash': str(cash), 'equity': str(cash), 'quantity': 0})
    return {'config': config(), 'state': {'cash': str(cash), 'contributions': '25000',
        'realized_pnl': str(sum((D(x) for x in pnls), D(0))), 'fees': str(D('1.30')*len(pnls)),
        'entries': len(pnls), 'closed_trades': len(pnls), 'position': None, 'pending': None,
        'hold': None, 'processed_rows': len(curve)}, 'curve': curve, 'events': events, 'trades': trades,
        'complete': True, 'terminal_pnl': str(cash-25000), 'nav_complete': True, 'max_drawdown': str(drawdown),
        'all_source_frames_accounted': True, 'research_only': True, 'execution_qualified': False, 'automatic_adoption': False}


def partial_report(*, cash='24500', quantity=2, equity=None):
    result = synthetic_episodes(['0'])
    result['trades'] = []
    result['events'] = [{'type': 'fill', 'action': 'BUY', 'fee': '1.95'},
        {'type': 'fill', 'action': 'SELL', 'fee': '.65', 'source_date': '2026-06-03', 'realized_pnl': '5'}]
    result['curve'] = [{'source_date': '2026-06-03', 'cash': cash, 'equity': equity, 'quantity': quantity}]
    result['state'].update(cash=cash, entries=1, closed_trades=0, fees='2.60', realized_pnl='5',
                           position={'realized_pnl': '5'}, hold={'code': 'unresolved_deadline_quantity'})
    result.update(complete=False, terminal_pnl=None, nav_complete=equity is not None,
                  max_drawdown=str(max(D(0), D(25000)-D(equity))) if equity is not None else None)
    return result


class ScenarioTests(unittest.TestCase):
    def test_base_is_exact_detached_identity_and_all_source_economics_survive(self):
        source = frames(); before = deepcopy(source)
        result = audit.scenario_rows(source, 'recorded_base')
        self.assertEqual(result, before); self.assertIsNot(result, source)
        self.assertIsNot(result[0]['quotes'][0], source[0]['quotes'][0])
        result[0]['quotes'][0]['bid'] = '123'
        self.assertEqual(source, before)

    def test_exact_adverse_cent_no_clamp_midpoint_or_clock_change(self):
        source = frames(end='2026-05-11'); q = source[0]['quotes'][0]
        q.update(bid='.005', ask='.01')
        stressed = audit.scenario_rows(source, 'modeled_adverse_cent')
        self.assertEqual(D(stressed[0]['quotes'][0]['bid']), D('-.005'))
        self.assertEqual(D(stressed[0]['quotes'][0]['ask']), D('.02'))
        self.assertEqual(stressed[0]['observed_at'], source[0]['observed_at'])
        self.assertFalse(engine._valid_quote(stressed[0]['quotes'][0]))
        self.assertEqual(q['bid'], '.005')

    def test_half_depth_floor_unknown_and_zero_never_inferred_from_activity(self):
        source = frames(end='2026-05-11'); q = source[0]['quotes'][0]
        q.update(bid_size=3, ask_size=None)
        half = audit.scenario_rows(source, 'modeled_half_depth')[0]['quotes'][0]
        self.assertEqual(half['bid_size'], 1); self.assertIsNone(half['ask_size'])
        q.update(bid_size=0, ask_size=1)
        half = audit.scenario_rows(source, 'modeled_half_depth')[0]['quotes'][0]
        self.assertEqual((half['bid_size'], half['ask_size']), (0, 0))
        self.assertEqual(half['volume'], q['volume']); self.assertIsNone(half['open_interest'])

    def test_combined_composes_exact_price_and_depth_views(self):
        source = frames(end='2026-05-11', depth=5)
        both = audit.scenario_rows(source, 'modeled_combined')
        q = both[0]['quotes'][0]
        self.assertEqual((D(q['bid']), D(q['ask']), q['bid_size'], q['ask_size']), (D('.94'), D('1.01'), 2, 2))
        self.assertEqual(source[0]['quotes'][0]['ask_size'], 5)

    def test_hostile_decimal_context_cannot_change_exact_stress_or_summary(self):
        source = frames(); report = engine.run(source, config())
        expected = (audit.scenario_rows(source, 'modeled_combined'), audit.summarize(report))
        with localcontext() as context:
            context.prec = 2; context.rounding = ROUND_UP; context.traps[Inexact] = True
            self.assertEqual((audit.scenario_rows(source, 'modeled_combined'), audit.summarize(report)), expected)

    def test_invalid_scenario_unknown_fields_and_boolean_counts_refused(self):
        for name in ('chosen_after_profit', None, True):
            with self.subTest(name=name), self.assertRaises(ValueError):
                audit.scenario_rows(frames(), name)
        for field, value in (('bid_size', True), ('ask_size', -1), ('volume', '100')):
            rows = frames(); rows[0]['quotes'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): audit.scenario_rows(rows, 'recorded_base')
        rows = frames(); rows[0]['secret_extra'] = 'unused'
        with self.assertRaises(ValueError): audit.scenario_rows(rows, 'recorded_base')


class SummaryTests(unittest.TestCase):
    def test_actual_engine_path_keeps_fees_and_realized_terminal_pnl(self):
        report = engine.run(frames(), config()); result = audit.summarize(report)
        self.assertTrue(result['complete']); self.assertEqual(result['closed_episodes'], 1)
        self.assertEqual(D(result['terminal_pnl']), D('98.70'))
        self.assertEqual(D(result['fees']), D('1.30'))
        self.assertEqual(D(result['monthly_exit_realized_pnl']['2026-06']), D('98.70'))
        self.assertEqual(D(result['terminal_pnl_without_largest_1_positive']), 0)
        self.assertFalse(result['evidence_screen_passed'])

    def test_source_months_without_sells_are_explicit_zero_realization_not_nav_return(self):
        result = audit.summarize(engine.run(frames(), engine.configs()[0]))
        self.assertEqual(result['monthly_exit_realized_pnl'], {'2026-05': '0', '2026-06': '0'})
        self.assertIn('not_monthly_NAV_return', result['monthly_pnl_basis'])
        self.assertEqual(result['status'], 'no_trade')

    def test_partial_exit_monthly_pnl_is_not_a_fully_closed_winner(self):
        result = audit.summarize(partial_report())
        self.assertEqual(result['monthly_exit_realized_pnl'], {'2026-06': '5'})
        self.assertEqual(result['closed_episode_pnl'], '0')
        self.assertEqual(result['closed_episodes'], 0); self.assertEqual(result['status'], 'held')
        self.assertIsNone(result['terminal_pnl']); self.assertIsNone(result['max_drawdown_dollars'])
        self.assertIsNone(result['terminal_pnl_without_largest_1_positive'])
        self.assertIsNone(result['largest_positive_episode_pnl'])
        self.assertEqual(result['known_nav_fraction'], '0')

    def test_concentration_and_removal_use_net_full_episodes_not_gross_wins(self):
        result = audit.summarize(synthetic_episodes(['100', '-50', '20']))
        self.assertEqual(result['terminal_pnl'], '70')
        with localcontext() as context:
            context.prec = 60
            self.assertEqual(D(result['largest_winner_share_of_net_closed_pnl']), D(100)/70)
        self.assertEqual(result['terminal_pnl_without_largest_1_positive'], '-30')
        self.assertEqual(result['terminal_pnl_without_largest_2_positive'], '-50')
        self.assertEqual(result['positive_closed_episodes'], 2)
        losing = audit.summarize(synthetic_episodes(['-50', '-2']))
        self.assertIsNone(losing['largest_positive_episode_pnl'])
        self.assertIsNone(losing['largest_winner_share_of_positive_closed_pnl'])
        self.assertEqual(losing['terminal_pnl_without_largest_2_positive'], '-52')

    def test_explicit_screen_requires_thirty_closed_episodes_three_exit_months_known_nav(self):
        months = ['2026-06']*10 + ['2026-07']*10 + ['2026-08']*10
        result = audit.summarize(synthetic_episodes(['1']*30, months))
        self.assertTrue(result['evidence_screen_passed'])
        self.assertFalse(result['sample_sufficient_for_edge']); self.assertFalse(result['automatic_adoption'])
        self.assertFalse(audit.summarize(synthetic_episodes(['1']*29, months[:29]))['evidence_screen_passed'])
        self.assertFalse(audit.summarize(synthetic_episodes(['1']*30))['evidence_screen_passed'])
        report = synthetic_episodes(['1']*30, months); report['curve'][0]['equity'] = None
        report.update(nav_complete=False, max_drawdown=None)
        self.assertFalse(audit.summarize(report)['evidence_screen_passed'])

    def test_twenty_thousand_loss_screen_distinguishes_proven_floor_breach_and_unknown(self):
        safe = audit.summarize(partial_report(cash='5007', quantity=10))
        self.assertTrue(safe['loss_budget_proven_by_conservative_floor'])
        self.assertTrue(safe['loss_budget_screen_passed']); self.assertFalse(safe['stop_unverifiable'])
        uncertain = audit.summarize(partial_report(cash='5005', quantity=10))
        self.assertEqual(uncertain['minimum_conservative_equity_floor'], '4998.50')
        self.assertTrue(uncertain['stop_unverifiable']); self.assertFalse(uncertain['loss_budget_screen_passed'])
        breach = audit.summarize(partial_report(cash='4900', quantity=10, equity='4999'))
        self.assertTrue(breach['known_nav_loss_budget_breach']); self.assertFalse(breach['loss_budget_screen_passed'])
        self.assertFalse(breach['active_total_loss_stop_enforced'])

    def test_inconsistent_financial_or_nav_result_is_refused_not_repaired(self):
        for change in ('fees', 'pnl', 'nav', 'contribution', 'closed_count'):
            report = synthetic_episodes(['100'])
            if change == 'fees': report['state']['fees'] = '0'
            if change == 'pnl': report['terminal_pnl'] = '101'
            if change == 'nav': report['nav_complete'] = False
            if change == 'contribution': report['state']['contributions'] = '50000'
            if change == 'closed_count': report['state']['closed_trades'] = 2
            with self.subTest(change=change), self.assertRaises(ValueError): audit.summarize(report)


class AuditTests(unittest.TestCase):
    def test_all_712_fixed_paths_hash_bound_sink_and_cash_control_without_source_mutation(self):
        source = profiles(end='2026-05-11'); before = deepcopy(source); witnessed = []
        engine_rule = deepcopy(engine.RULE); grid = engine.configs()
        def sink(profile, scenario, config, report):
            witnessed.append((profile, scenario, config['id'], hashlib.sha256(audit.canonical(report)).hexdigest()))
        result = audit.audit(source, report_sink=sink)
        self.assertEqual(result['case_count'], 712); self.assertEqual(result['configuration_count'], 89)
        self.assertEqual(result['cash_control_case_count'], 8); self.assertEqual(len(witnessed), 712)
        self.assertEqual(len({item[:3] for item in witnessed}), 712)
        self.assertEqual([item[3] for item in witnessed], [case['engine_report_sha256'] for case in result['cases']])
        self.assertTrue(all(case['status'] == 'no_trade' for case in result['cases']))
        self.assertEqual(result['recorded_profit_leaders'], []); self.assertEqual(result['evidence_screen_ids'], [])
        self.assertEqual(source, before); self.assertEqual(engine.RULE, engine_rule); self.assertEqual(engine.configs(), grid)
        self.assertEqual(result['owner_operations'], 0); self.assertEqual(result['network_calls'], 0)
        self.assertFalse(result['independent_holdout']); self.assertFalse(result['deployable'])

    def test_price_and_depth_paths_use_unchanged_engine_and_missing_depth_yields_zero_trades(self):
        source = profiles(depth=1); result = audit.audit(source)
        candidate = config()['id']
        cases = [case for case in result['cases'] if case['config_id'] == candidate]
        base = next(case for case in cases if case['clock_profile'] == engine.CLOCK_PROFILES[0] and case['scenario'] == 'recorded_base')
        self.assertEqual(D(base['terminal_pnl']), D('98.70'))
        for case in cases:
            expected = engine.run(audit.scenario_rows(source[case['clock_profile']], case['scenario']), config())
            self.assertEqual(case['engine_report_sha256'], hashlib.sha256(audit.canonical(expected)).hexdigest())
            if case['depth_modeled']:
                self.assertEqual(case['status'], 'no_trade'); self.assertEqual(case['entries'], 0)
        aggregate = next(item for item in result['aggregates'] if item['config']['id'] == candidate)
        self.assertEqual(D(aggregate['worst_terminal_pnl']), min(D(case['terminal_pnl']) for case in cases))
        self.assertLessEqual(D(aggregate['worst_terminal_pnl']), 0)
        self.assertFalse(aggregate['positive_in_every_case']); self.assertFalse(aggregate['evidence_screen_passed'])

    def test_unclosed_books_never_receive_an_aggregate_terminal_rank(self):
        result = audit.audit(profiles(end='2026-06-02'))
        item = next(row for row in result['aggregates'] if row['config']['id'] == config()['id'])
        self.assertFalse(item['all_cases_complete'])
        self.assertIsNone(item['worst_terminal_pnl'])
        self.assertIsNone(item['worst_terminal_pnl_without_largest_1_positive'])
        self.assertNotIn(config()['id'], result['all_scenario_profit_leaders'])
        held = [case for case in result['cases'] if case['config_id'] == config()['id'] and not case['complete']]
        self.assertTrue(held)
        self.assertTrue(all(case['status'] == 'held' and case['terminal_pnl'] is None for case in held))

    def test_both_profiles_must_match_source_economics_and_exact_keys(self):
        for defect in ('missing', 'price', 'order', 'context'):
            source = profiles(end='2026-05-12'); arguments = {}
            if defect == 'missing': source.pop(engine.CLOCK_PROFILES[1])
            if defect == 'price': source[engine.CLOCK_PROFILES[1]][0]['quotes'][0]['bid'] = '.94'
            if defect == 'order': source[engine.CLOCK_PROFILES[1]].reverse()
            if defect == 'context': arguments['decision_contexts_by_profile'] = {engine.CLOCK_PROFILES[0]: None}
            with self.subTest(defect=defect), self.assertRaises(ValueError): audit.audit(source, **arguments)

    def test_sink_mutation_and_sink_failure_stop_without_continuation(self):
        for mutate in (True, False):
            calls = []
            def sink(profile, scenario, config, report):
                calls.append(1)
                if mutate: report['state']['cash'] = '999999'
                else: raise RuntimeError('synthetic storage full')
            with self.subTest(mutate=mutate), self.assertRaises((ValueError, RuntimeError)):
                audit.audit(profiles(end='2026-05-11'), report_sink=sink)
            self.assertEqual(len(calls), 1)

    def test_sink_config_is_detached_and_checkpoint_failure_stops_immediately(self):
        seen = []
        def sink(profile, scenario, config, report):
            config['id'] = 'changed_outside'; seen.append(report['config']['id'])
        result = audit.audit(profiles(end='2026-05-11'), report_sink=sink)
        self.assertEqual(seen[0], 'cash'); self.assertEqual(result['cases'][0]['config_id'], 'cash')
        calls = []
        def stop():
            calls.append(1); raise RuntimeError('explicit pause')
        with self.assertRaises(RuntimeError): audit.audit(profiles(end='2026-05-11'), checkpoint=stop)
        self.assertEqual(len(calls), 1)

    def test_frozen_rule_or_grid_changes_are_rejected_before_any_engine_run(self):
        before = deepcopy(audit.RULE)
        try:
            audit.RULE['screen']['minimum_fully_closed_episodes'] = 1
            with self.assertRaises(ValueError): audit.audit(profiles(end='2026-05-11'))
        finally:
            audit.RULE.clear(); audit.RULE.update(before)
        self.assertEqual(audit.RULE_SHA256, hashlib.sha256(audit.canonical(audit.RULE)).hexdigest())


if __name__ == '__main__':
    unittest.main()
