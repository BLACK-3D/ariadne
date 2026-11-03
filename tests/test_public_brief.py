"""Public paper brief presentation uses saved facts; no feed, owner writes or prices."""
from copy import deepcopy
import unittest
from ariadne.options_public_brief import view, markdown
from ariadne.briefing_summary import compact_summary
from ariadne.brief_poster import poster_view
import test_brief_options_eod as prior


def report():
    result=prior.fixture()
    result['options_paper']['paper']['summary']={'schema':7,'cash':'24800','equity':'24990','contributions':'25000',
        'trading_pnl':'-10','monthly_pnl':'-10','realized_pnl':'0','fees':'1.30','monthly_budget_used':'201.30',
        'monthly_budget_remaining':'24798.70','trade_count':1,'closed_trade_count':0,'cap_qualified':True,
        'position':{'contract_id':'F261218P00010000'},'last_daily_result':{'date':'2026-11-03','recorded_at':'2026-11-03T15:00:01Z'}}
    return result


class PublicBriefTests(unittest.TestCase):
    def test_markdown_saved_public_branch_has_correct_limits_and_no_legacy_feed(self):
        value=report();before=deepcopy(value);text=prior.rendered(value)
        for expected in ('Public data / $25,000 total simulated bankroll','no automatic top-ups',
            'Saved cash: $24800','Saved equity: $24990','trading P/L excluding contributions: $-10',
            'recorded 2026-11-03T15:00:01+00:00','Daily P/L is not measured',
            'no automatic Public polling','including both fee sides'):
            self.assertIn(expected,text)
        for wrong in ('$500 monthly simulated loss','EXPERIMENTAL DATE-ONLY EOD','Public AAPL source:',
                      'current for that session','Latest saved model result'):
            self.assertNotIn(wrong,text)
        self.assertEqual(value,before)

    def test_compact_reports_dated_snapshot_and_never_calls_it_current(self):
        result=compact_summary(report(),prior.NOW)['options_paper']
        self.assertEqual(result['schema'],7)
        self.assertEqual(result['total_bankroll'],'25000')
        self.assertEqual(result['monthly_cap'],'25000')
        self.assertEqual(result['entry_all_in_cap'],'1000')
        self.assertEqual(result['trading_pnl'],'-10')
        self.assertEqual(result['contributions'],'25000')
        self.assertFalse(result['fresh_at_report_generation'])
        self.assertFalse(result['automatic_topups'])
        self.assertIsNone(result['daily_pnl'])
        self.assertEqual(result['source_status'],'saved_result_not_current_quote')

    def test_poster_schema7_no_false500_budget_or_stale_old_source(self):
        result=poster_view(report())
        text=str(result)
        self.assertIn('PUBLIC OPTIONS PAPER',text)
        self.assertIn('$25,000 total / no top-ups',text)
        self.assertNotIn('$500 monthly budget',text)
        self.assertIn('dated results only',text)

    def test_missing_malformed_facts_stay_unavailable_and_hold_preserved(self):
        result=report();summary=result['options_paper']['paper']['summary']
        summary.update(cash=float('nan'), fees=True, trade_count=True, last_daily_result={'date':'2026-99-99','recorded_at':'2026-11-03'})
        result['options_paper']['hold']={'request_id':'saved-failure'}
        value=view(result['options_paper'])
        self.assertIsNone(value['cash']);self.assertIsNone(value['fees']);self.assertIsNone(value['trade_count'])
        self.assertIsNone(value['latest_result_date']);self.assertIsNone(value['result_recorded_at'])
        self.assertTrue(value['held'])
        text='\n'.join(markdown(result['options_paper']))
        self.assertIn('HELD',text);self.assertIn('No completed Public paper result',text)

    def test_old_schema_not_reinterpreted_and_disabled_not_added(self):
        self.assertIsNone(view(prior.fixture()['options_paper']))
        result=report();result['options_paper']['config']['enabled']=False
        self.assertNotIn('Public data /',prior.rendered(result))
        self.assertNotIn('options_paper',compact_summary(result,prior.NOW))


if __name__=='__main__':unittest.main()
