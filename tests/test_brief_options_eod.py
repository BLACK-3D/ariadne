"""Saved EOD paper-account presentation; no source acquisition or owner writes."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from ariadne.briefing import BriefingService, options_eod_brief_view
from ariadne.briefing_summary import compact_summary, MAX_SUMMARY_CHARS

NOW = datetime(2026, 10, 30, 13, 40, tzinfo=timezone.utc)


def fixture():
    quality={"symbol":"F", "last_source_date":"2026-10-29", "last_received_at":"2026-10-30T13:36:00+00:00",
             "partial_chain":True, "quotes":32, "fresh_now":True, "expected_last_session_now":"2026-10-29"}
    summary={"schema":3, "symbol":"F", "source_id":"onclick_f_eod_v1", "rule_sha256":"a"*64,
             "equity":"500", "cash":"500", "contributions":"500", "trading_pnl":"0", "monthly_pnl":"0",
             "monthly_budget_used":"0", "monthly_budget_remaining":"500", "fees":"0", "trade_count":0,
             "closed_trade_count":0, "position":None, "pending":None, "hold":None, "cap_qualified":True,
             "last_daily_result":None, "last_source_date":"2026-10-29", "last_observed_at":None,
             "strategy_stats":{}, "next_strategy":"eod_recovery_call_v1"}
    eod={"config":{"initialized":True,"enabled":True}, "quality":quality, "baseline_count":14,
         "expected_market_session":"2026-10-29", "collection_check_at":"2026-10-30T08:35:00-05:00",
         "collection_window_open":True, "last_run":{"status":"completed", "request_id":"eod-test-source",
         "created_at":"2026-10-30T13:35:00+00:00","completed_at":"2026-10-30T13:36:00+00:00"},
         "publication_assurance":{"waiting_for_disposal_source":False,"deadline_source_date":None}}
    return {"generated_at_utc":NOW.isoformat(),"local_date":"2026-10-30","config":{"region":None},
            "activity":{"window_start_utc":"2026-10-29T11:00:00+00:00","window_end_utc":"2026-10-30T11:00:00+00:00",
             "runs_count":0,"events_count":0,"new_memories":0,"new_documents":0,"unresolved_approvals":[],
             "procedures":[],"runs":[],"coverage":"Saved fixture activity only.","details_truncated":False},
            "public_sources":{"sources":[]},"warnings":[],
            "options_paper":{"config":{"enabled":True}, "paper":{"summary":summary}, "forward_quality":quality,
             "eod":eod,"quality":{"historical_only":True},"feed":{"config":{"initialized":True,"enabled":False},
             "publication":"Archived AAPL source"},"inbox":"saved/options"}}


def rendered(report):
    service=BriefingService.__new__(BriefingService)
    with patch('ariadne.brief_poster.poster_view',return_value={'svg':'<svg/>'}):
        return service._render(report)['brief.md'].decode()


class BriefOptionsEODTests(unittest.TestCase):
    def test_full_brief_discloses_model_source_clock_partial_warmup_and_shared_budget(self):
        value=fixture();before=deepcopy(value);text=rendered(value)
        for term in ('Active F source: onclick_f_eod_v1','EXPERIMENTAL DATE-ONLY EOD',
                     'All-in entry ceiling: $100','existing $500','Indicator baseline: 14',
                     'Saved source session: 2026-10-29','Actual receipt: 2026-10-30T13:36:00+00:00',
                     'quote-event clock: UNKNOWN','Returned chain: PARTIAL','Recorded cumulative fees: $0',
                     'Modeled entries: 0','No forward daily trading result yet','Next eligible check window',
                     'failed work is not retried','no execution qualification'):
            self.assertIn(term,text)
        self.assertNotIn('Public AAPL source:',text)
        self.assertEqual(value,before)

    def test_saved_daily_result_uses_actual_accounting_and_does_not_turn_funding_into_returns(self):
        value=fixture();summary=value['options_paper']['paper']['summary']
        summary.update(cash='450',equity='449.35',trading_pnl='-50.65',monthly_pnl='-50.65',fees='0.65',
                       monthly_budget_used='100',monthly_budget_remaining='400',trade_count=1,
                       position={'contract_id':'F261113C00020000'})
        summary['last_daily_result']={'date':'2026-10-29','daily_pnl':'-50.65','recorded_at':'2026-10-30T13:36:01+00:00'}
        text=rendered(value);compact=compact_summary(value,NOW)['options_paper']
        self.assertIn('daily P/L $-50.65',text)
        self.assertIn('Saved cash: $450',text)
        self.assertIn('actually recorded 2026-10-30T13:36:01+00:00',text)
        self.assertEqual(compact['daily_pnl'],'-50.65')
        self.assertEqual(compact['trading_pnl'],'-50.65')
        self.assertEqual(compact['contributions'],'500')
        self.assertEqual(compact['fees'],'0.65')
        self.assertEqual(compact['position'],'F261113C00020000')

    def test_missing_publication_failed_receipt_and_disposal_stay_held_without_sale_or_retry_claim(self):
        value=fixture();options=value['options_paper'];eod=options['eod']
        eod['quality'].update(fresh_now=False,expected_last_session_now='2026-10-30')
        eod['last_run'].update(status='failed',reason='Source check failed; inspect saved evidence.')
        eod['publication_assurance'].update(waiting_for_disposal_source=True,deadline_source_date='2026-10-30')
        options['paper']['summary']['hold']={'code':'source_gap','message':'Missing required session'}
        text=rendered(value);view=compact_summary(value,NOW)['options_paper']
        for term in ('failed_check','STALE / unavailable','Missing publication / source-session gap',
                     'HELD: Missing required session','is not a sale','No failed or interrupted work is automatically replayed'):
            self.assertIn(term,text)
        self.assertTrue(view['held']);self.assertTrue(view['missing_source_session'])
        self.assertTrue(view['waiting_for_disposal_source'])
        self.assertEqual(view['last_check_status'],'failed')

    def test_missing_dates_clocks_or_counts_are_explicit_and_never_invented(self):
        value=fixture();options=value['options_paper'];eod=options['eod']
        eod['quality'].update(last_source_date='invalid',last_received_at='2026-10-30T13:36:00',partial_chain=None,quotes=None)
        options['paper']['summary']['last_source_date']=None
        eod['baseline_count']=None;eod['last_run']={};eod['collection_check_at']=None
        view=options_eod_brief_view(options);text=rendered(value)
        self.assertIsNone(view['source_date']);self.assertIsNone(view['source_received_at'])
        self.assertIsNone(view['baseline_count']);self.assertIsNone(view['quote_observed_at'])
        self.assertFalse(view['fresh_at_report_generation'])
        self.assertIn('Saved source session: unavailable. Actual receipt: unavailable',text)
        self.assertIn('Indicator baseline: unavailable',text)
        self.assertIn('waiting_for_first_source',text)

    def test_compact_is_effect_free_exactly_saved_date_only_facts_and_no_legacy_feed_claim(self):
        value=fixture();before=deepcopy(value)
        with patch('builtins.open',side_effect=AssertionError('No file access')),patch('subprocess.run',side_effect=AssertionError('No process')),patch('socket.socket',side_effect=AssertionError('No network')):
            result=compact_summary(value,NOW)
        view=result['options_paper']
        self.assertEqual(view['symbol'],'F');self.assertTrue(view['shared_portfolio'])
        self.assertEqual(view['source_date'],'2026-10-29')
        self.assertEqual(view['source_received_at'],'2026-10-30T13:36:00+00:00')
        self.assertIsNone(view['quote_observed_at']);self.assertFalse(view['execution_qualified'])
        self.assertEqual(view['entry_all_in_cap'],'100.00')
        self.assertEqual(view['monthly_cap'],'500.00');self.assertEqual(view['trade_count'],0)
        self.assertIsNone(view['latest_result_date']);self.assertIsNone(view['daily_pnl'])
        self.assertNotIn('public_feed_observed_at',view)
        self.assertEqual(value,before)

    def test_compact_bound_with_long_source_content_and_eod_source_failure(self):
        value=fixture();kinds=['weather','forecast','alerts','earthquakes']+['news']*5
        value['public_sources']['sources']=[{'kind':kind,'title':'t'*5000,'url':'https://example.gov/'+'u'*260,
            'status':'available','fetched_at':NOW.isoformat(),'issued_at':NOW.isoformat(),
            'items':[{'title':'t'*5000,'summary':'s'*10000,'url':'https://example.gov/'+'q'*2000,
            'published_at':NOW.isoformat(),'ends':'2026-10-30T20:00:00+00:00'}]*100} for kind in kinds]
        value['options_paper']['eod']['last_run'].update(status='failed',error='private-token '+('w'*9000))
        result=compact_summary(value,NOW)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)),MAX_SUMMARY_CHARS)
        self.assertNotIn('private-token',json.dumps(result))
        self.assertEqual(result['options_paper']['last_check_status'],'failed')
        self.assertIsNone(result['options_paper']['quote_observed_at'])

    def test_legacy_schema2_sections_and_compact_are_preserved(self):
        value=fixture();summary=value['options_paper']['paper']['summary'];summary.update(schema=2,symbol='AAPL')
        text=rendered(value);view=compact_summary(value,NOW)['options_paper']
        self.assertIn('Public AAPL source: paused',text)
        self.assertNotIn('EXPERIMENTAL DATE-ONLY',text)
        self.assertTrue(view['shared_portfolio']);self.assertEqual(view['symbol'],'AAPL')
        self.assertNotIn('entry_all_in_cap',view)


if __name__=='__main__':
    unittest.main()
