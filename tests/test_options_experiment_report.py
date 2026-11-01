from copy import deepcopy
from decimal import Decimal
import unittest

from scripts.options_experiment_report import money, render


class ExperimentReportTests(unittest.TestCase):
    def fixtures(self):
        def book(name, pnl, *, gap=False):
            return {'config': {'id': name}, 'complete': True, 'terminal_pnl': pnl,
                    'state': {'closed_trades': int(name != 'cash'), 'fees': '1.30', 'hold': None},
                    'nav_complete': not gap, 'max_drawdown': None if gap else '2',
                    'curve': [{'trading_pnl': '0'}, {'trading_pnl': None if gap else '4'}, {'trading_pnl': pnl}]}
        profiles = ('assumed_eod_plus_15m', 'assumed_next_session_0935')
        full = {'results': {p: [book('cash', '0'), book('alpha', str(10-i*2), gap=True)] for i, p in enumerate(profiles)}}
        design = deepcopy(full)
        for cases in design['results'].values():
            cases[1]['terminal_pnl'] = '3'
        return design, full, {'candidate_id': 'cash'}, {}, {}

    def test_frozen_cash_selection_is_not_replaced_by_full_winner(self):
        html = render(*self.fixtures(), study_link='../../study')
        self.assertIn('DESIGN selection: <code>cash</code>', html)
        self.assertIn('<code>alpha</code>: $8.00 worst-profile', html)
        self.assertIn('post hoc diagnostic', html)

    def test_unknown_nav_breaks_lines_and_risk_is_not_fabricated(self):
        html = render(*self.fixtures(), study_link='../../study')
        self.assertEqual(html.count('<polyline points='), 6)  # two cash lines plus four disjoint alpha segments
        self.assertIn('NAV complete: False; maximum drawdown: unresolved', html)
        self.assertIn('gaps mean unknown NAV', html)

    def test_no_trade_and_held_cases_remain_visible(self):
        values = self.fixtures()
        for cases in values[1]['results'].values():
            cases[1].update(complete=False, terminal_pnl=None)
            cases[1]['state']['hold'] = {'code': 'missing_bid'}
        html = render(*values, study_link='../../study')
        self.assertIn('HELD', html)
        self.assertIn('unresolved', html)
        self.assertIn('No noncash completed book', html)
        self.assertIn('0 closed', html)

    def test_configuration_text_cannot_create_active_html(self):
        values = self.fixtures()
        for obj in (values[0], values[1]):
            for cases in obj['results'].values():
                cases[1]['config']['id'] = '<script>alert(1)</script>'
        html = render(*values, study_link='../../study')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_no_network_assets_and_receipt_links_present(self):
        html = render(*self.fixtures(), study_link='../../study')
        self.assertNotIn('<script', html)
        self.assertNotIn('<link', html)
        self.assertNotIn('url(', html)
        self.assertIn('../../study/completed.json', html)
        self.assertIn('unknown', html.lower())
        self.assertIn('Twelve months of usable options history', html)

    def test_later_increment_is_not_reported_for_incomplete_endpoints(self):
        values = list(self.fixtures())
        values[2] = {'candidate_id': 'alpha'}
        for cases in values[0]['results'].values():
            cases[1].update(complete=False, terminal_pnl=None)
        html = render(*values, study_link='../../study')
        self.assertIn('<td>unresolved</td><td>$10.00</td><td>unresolved</td>', html)

    def test_money_uses_decimal_without_a_float_round_trip(self):
        self.assertEqual(money(str(Decimal('1000.125'))), '$1,000.12')
        self.assertEqual(money('-183.90'), '$-183.90')
        self.assertEqual(money(None), 'unresolved')


if __name__ == '__main__':
    unittest.main()
