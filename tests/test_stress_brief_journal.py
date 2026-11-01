"""Saved stress-paper presentation, with no data fetch or owner action."""
from copy import deepcopy
from datetime import date,datetime,timezone
import unittest

from ariadne import options_eod as base
from ariadne import options_stress_eod as stress
from ariadne.options_journal import build_review
from ariadne.briefing import options_eod_brief_view
from ariadne.briefing_summary import compact_summary
from test_options_stress_eod import state,row
from test_options_stress_context import witness
from test_brief_options_eod import fixture,rendered

UTC=timezone.utc


def observations():
    current=state();captures=[];contexts=[]
    for day,price in ((date(2024,1,2),'11.8'),(date(2024,1,3),'11.7'),(date(2024,1,4),'11.6')):
        snapshot=row(day,price=price);contexts.append(witness(day.isoformat()))
        clock=max(current['last_decided_at'],snapshot['received_at'])
        paper=stress.advance_paper(snapshot,current,decided_at=clock,contexts=contexts)
        receipt={'request_id':'stress-'+day.isoformat(),'account_id':'one-paper-account',
            'accepted_snapshot':True,'quote_observed_at':None,'rule_sha256':base.RULE_SHA256,
            'source_date':snapshot['source_date'],'received_at':snapshot['received_at'],'recorded_at':clock,
            'paper_schema':4,'stress_rule_sha256':stress.RULE_SHA256}
        captures.append({'before_state':current,'snapshot':snapshot,'paper':paper,'receipt':receipt})
        current=paper['state']
    return {'id':'one-paper-account','state':current},captures


class StressBriefJournalTests(unittest.TestCase):
    def test_before_first_gate_brief_preserves_context_status(self):
        value=fixture()["options_paper"]
        value["paper"]["summary"]=stress.summarize(state())
        value["eod"]["stress"]={"context_status":"missing","last_context":None}
        self.assertEqual(options_eod_brief_view(value)["stress_context_reason"],"missing")

    def test_pristine_policy_has_zero_returns_and_no_capture_diagnostics(self):
        account={'id':'one-paper-account','state':state()}
        result=build_review(account,[],now=datetime(2024,1,3,15,2,tzinfo=UTC))
        self.assertEqual(result['profile'],'experimental_stress_put_eod_paper')
        self.assertEqual(result['status'],'waiting_for_first_capture')
        self.assertEqual(result['performance']['trading_pnl'],'0')
        self.assertEqual(result['diagnostics']['strategies'],[])

    def test_real_policy_states_keep_exact_single_ledger_economics_in_journal(self):
        account,captures=observations();before=deepcopy((account,captures))
        result=build_review(account,captures,now=datetime(2024,1,5,15,tzinfo=UTC))
        self.assertEqual(result['performance']['entries'],1)
        self.assertEqual(result['performance']['closed_trades'],0)
        self.assertEqual(result['performance']['fees'],'0.65')
        self.assertEqual(result['performance']['by_strategy'][0]['id'],stress.STRATEGY_ID)
        self.assertEqual(result['diagnostics']['last_entry_gate']['stage'],'fill')
        self.assertEqual((account,captures),before)

    def test_changed_stress_summary_or_missing_state_is_an_integrity_error(self):
        account,captures=observations();captures[-1]['paper']['summary']['cash']='999'
        with self.assertRaises(ValueError):build_review(account,captures,now=datetime(2024,1,5,15,tzinfo=UTC))
        account,captures=observations();captures[0]['before_state']=None
        with self.assertRaises(ValueError):build_review(account,captures,now=datetime(2024,1,5,15,tzinfo=UTC))

    def test_daily_brief_and_compact_summary_show_saved_stress_policy_without_collection(self):
        account,captures=observations();value=fixture()
        value['options_paper']['paper']['summary']=stress.summarize(account['state'])
        before=deepcopy(value)
        view=options_eod_brief_view(value['options_paper'])
        self.assertEqual(view['schema'],4);self.assertTrue(view['stress_policy_active'])
        self.assertEqual(view['stress_strategy'],stress.STRATEGY_ID)
        text=rendered(value)
        self.assertIn('stress-filtered long puts only',text)
        self.assertIn('context published before the source session open',text)
        summary=compact_summary(value,datetime(2026,10,30,13,40,tzinfo=UTC))['options_paper']
        self.assertEqual(summary['monthly_cap'],'500.00');self.assertEqual(summary['entry_all_in_cap'],'100.00')
        self.assertTrue(summary['stress_policy_active']);self.assertEqual(value,before)


if __name__=='__main__':unittest.main()
