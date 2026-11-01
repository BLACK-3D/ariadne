"""Generated future-brief facts for a total-bankroll paper account."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_brief_options_eod import fixture, rendered, NOW
from ariadne.briefing import options_eod_brief_view
from ariadne.briefing_summary import compact_summary


def bankroll_fixture():
    value=fixture();options=value['options_paper'];summary=options['paper']['summary']
    summary.update(schema=6,symbol='KO',source_id='onclick_ko_eod_v1',cash='25000',equity='25000',
        contributions='25000',monthly_budget_remaining='25000',bankroll_total='25000',
        entry_equity_fraction='0.04',automatic_topups=False,funding_basis='one_time_total_bankroll',
        funding_added_on_activation='24500',bankroll_policy_activated_at='2026-10-30T13:30:00+00:00',
        strategy_id='ko_bankroll_stress_breakdown_put_v1',next_strategy='ko_bankroll_stress_breakdown_put_v1',
        last_entry_gate={'allowed':True,'benchmark_gate':{'stage':'decision','reason':'generated_test_only',
            'label':'stress_unwind','witness':{'published_at':'2026-10-30T13:30:00+00:00'}}})
    options['eod']['quality']['symbol']='KO'
    options['eod']['stress']={'active':True,'context_status':'stress_ready'}
    return value


class BankrollPresentationTests(unittest.TestCase):
    def test_future_brief_exposes_total_funding_and_both_caps_without_mutation(self):
        value=bankroll_fixture();before=deepcopy(value)
        result=options_eod_brief_view(value['options_paper'])
        self.assertEqual(result['schema'],6)
        self.assertEqual(result['total_bankroll'],'25000')
        self.assertEqual(result['monthly_cap'],'25000.00')
        self.assertEqual(result['entry_all_in_cap'],'1000.00')
        self.assertEqual(result['entry_equity_fraction'],'0.04')
        self.assertEqual(result['funding_added_on_activation'],'24500')
        self.assertEqual(result['funding_basis'],'one_time_total_bankroll')
        self.assertFalse(result['automatic_topups'])
        self.assertEqual(value,before)

    def test_funding_adjustment_does_not_become_daily_or_trading_profit(self):
        result=options_eod_brief_view(bankroll_fixture()['options_paper'])
        self.assertEqual(result['trading_pnl'],'0')
        self.assertEqual(result['monthly_pnl'],'0')
        self.assertIsNone(result['daily_pnl'])
        self.assertIsNone(result['latest_result_date'])
        self.assertEqual(result['trade_count'],0)

    def test_nested_schema6_context_gate_keeps_actual_publication_clock(self):
        result=options_eod_brief_view(bankroll_fixture()['options_paper'])
        self.assertEqual(result['stress_context_label'],'stress_unwind')
        self.assertEqual(result['stress_gate_stage'],'decision')
        self.assertEqual(result['stress_context_published_at'],'2026-10-30T13:30:00+00:00')
        self.assertIsNone(result['quote_observed_at'])
        self.assertFalse(result['execution_qualified'])

    def test_markdown_uses_total_capital_and_no_monthly_deposit_claim(self):
        text=rendered(bankroll_fixture())
        self.assertIn('$25,000 total simulated bankroll',text)
        self.assertIn('no automatic top-ups',text)
        self.assertIn('Entry limit: min($1,000, 4% of current equity',text)
        self.assertIn('including both fees per contract',text)
        self.assertIn('it adds no cash',text)
        self.assertNotIn('existing $500 monthly',text)
        self.assertNotIn('$500 monthly simulated loss budget',text)
        self.assertIn('No forward daily trading result yet',text)

    def test_compact_brief_preserves_six_and_zero_pnl(self):
        result=compact_summary(bankroll_fixture(),NOW)['options_paper']
        self.assertEqual(result['schema'],6)
        self.assertEqual(result['contributions'],'25000')
        self.assertEqual(result['trading_pnl'],'0')

    def test_failed_source_and_hold_do_not_turn_into_current_quote_or_sell(self):
        value=bankroll_fixture();options=value['options_paper']
        options['paper']['summary']['hold']={'code':'missing_position_quote'}
        options['eod']['last_run'].update(status='failed',reason='generated missing required quote')
        options['eod']['quality']['fresh_now']=False
        result=options_eod_brief_view(options)
        self.assertTrue(result['held'])
        self.assertEqual(result['source_status'],'failed_check')
        self.assertFalse(result['fresh_at_report_generation'])
        self.assertEqual(result['trade_count'],0)

    def test_legacy_three_presentation_is_still_500_100(self):
        result=options_eod_brief_view(fixture()['options_paper'])
        self.assertEqual(result['monthly_cap'],'500.00')
        self.assertEqual(result['entry_all_in_cap'],'100.00')
        self.assertNotIn('total_bankroll',result)

    def test_vix_remains_unfunded_and_uses_actual_shared_cap_metadata(self):
        from ariadne.options_vix import VixResearchWorkflow
        class Owner:
            def _exists(self,db):return False
        workflow=VixResearchWorkflow(Owner())
        account={'id':'generated-account','summary':bankroll_fixture()['options_paper']['paper']['summary']}
        value=workflow.snapshot(None,NOW,account)
        self.assertEqual(value['shared_monthly_limit'],'25000.00')
        self.assertEqual(value['shared_total_capital'],'25000.00')
        self.assertFalse(value['automatic_topups'])
        self.assertFalse(value['paper_fills_enabled'])
        self.assertEqual(value['funding_added'],'0')
        account['summary']['schema']=5
        legacy=workflow.snapshot(None,NOW,account)
        self.assertEqual(legacy['shared_monthly_limit'],'500.00')
        self.assertNotIn('shared_total_capital',legacy)

    def test_one_page_poster_uses_total_capital_without_rewriting_saved_briefs(self):
        from ariadne.brief_poster import poster_view
        value=bankroll_fixture()
        value['options_paper']['paper']['summary']['hold']={'code':'generated_missing_quote'}
        before=deepcopy(value)
        svg=poster_view(value,NOW)['svg']
        self.assertIn('$25,000 total',svg)
        self.assertNotIn('$500 monthly budget',svg)
        self.assertEqual(value,before)


if __name__=='__main__':unittest.main()
