"""Independent offline review of repaired briefs with saved Public accounting.

All reports, option positions and errors are synthetic; no provider or owner state.
"""
from contextlib import ExitStack
from copy import deepcopy
import builtins
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from ariadne.brief_poster import poster_view
from ariadne.briefing import BriefingService
from ariadne.briefing_summary import MAX_SUMMARY_CHARS, compact_summary
from ariadne.options_public_brief import view as public_view
import test_brief_options_eod as eod_cases
import test_brief_poster as poster_cases
import test_public_brief as public_cases


def combined():
    result = public_cases.report()
    example = poster_cases.report()
    result.update(generated_at_utc=example['generated_at_utc'],local_date=example['local_date'])
    result['config'] = deepcopy(example['config'])
    result['public_sources'] = deepcopy(example['public_sources'])
    for source in result['public_sources']['sources']:
        source.setdefault('title','Synthetic saved '+source['kind'])
    result['options_paper']['forward_quality'] = {'fresh_now':True,'last_source_date':'2026-10-29'}
    return result


class IndependentPublicRepairTests(unittest.TestCase):
    def test_positive_poster_projection_keeps_weather_when_financial_summary_overflows(self):
        for schema in (6,7):
            with self.subTest(schema=schema):
                value=combined()
                value['options_paper']['paper']['summary']['schema']=schema
                value['options_paper']['hold']={'request_id':'synthetic-hold'}
                value['options_paper']['vix']={'paper_status':'Unqualified '+('v'*20000)}
                value['qqq_paper']={'config':{'enabled':True},'hold':{'request_id':'qqq-hold'},
                    'paper':{'state':{'equity':'10000'}},'backtest':{'research_gate':'u'*20000}}
                before=deepcopy(value)
                visible=poster_cases.visible(poster_view(value,poster_cases.NOW))
                for text in ('58','Mostly Cloudy','Flood advisory','Road closure','OPTIONS PAPER HELD','$25,000 total / no top-ups'):
                    self.assertIn(text,visible)
                self.assertEqual(value,before)

    def test_pure_preflight_preserves_public_money_and_validates_three_outputs_without_effects(self):
        from ariadne.briefing_diagnostics import render_preflight
        value=combined();before=deepcopy(value)
        # Warm pure import paths before blocking every external-effect seam.
        self.assertTrue(render_preflight(value)[1]['ok'])
        with ExitStack() as stack:
            for owner,name in ((socket,'socket'),(socket,'create_connection'),(subprocess,'Popen'),
                    (sqlite3,'connect'),(builtins,'open'),(Path,'read_bytes'),(Path,'read_text'),
                    (BriefingService,'__init__')):
                stack.enter_context(patch.object(owner,name,side_effect=AssertionError('Pure renderer attempted an external effect')))
            outputs,receipt=render_preflight(value)
        self.assertTrue(receipt['ok']);self.assertEqual(receipt['model_calls'],0)
        self.assertEqual(receipt['network_calls'],0);self.assertEqual(receipt['workflow_calls'],0)
        self.assertEqual(set(outputs),{'brief.json','brief.md','brief.svg'})
        self.assertEqual(json.loads(outputs['brief.json']),before)
        self.assertEqual(value,before)
        svg=ET.fromstring(outputs['brief.svg'])
        self.assertEqual(svg.attrib['viewBox'],'0 0 1080 1350')
        for item in receipt['artifacts']:
            self.assertEqual(item['bytes'],len(outputs[item['path']]))
            self.assertEqual(item['sha256'],hashlib.sha256(outputs[item['path']]).hexdigest())
        text=outputs['brief.md'].decode('utf-8')
        self.assertIn('Public data / $25,000 total simulated bankroll',text)
        self.assertIn('Saved cash: $24800',text)
        self.assertIn('Total contributions: $25000',text)
        self.assertNotIn('$500 monthly simulated loss',text)

    def test_compact_public_projection_preserves_limits_and_unknown_freshness_with_legacy_flags(self):
        value=combined();value['options_paper']['hold']={'request_id':'held'}
        expected=public_view(value['options_paper'])
        result=compact_summary(value,poster_cases.NOW)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)),MAX_SUMMARY_CHARS)
        actual=result['options_paper']
        for key in ('schema','source_id','cash','equity','contributions','trading_pnl','realized_pnl','fees',
                    'monthly_budget_used','monthly_budget_remaining','monthly_cap','entry_all_in_cap',
                    'entry_equity_fraction','automatic_topups','held','daily_pnl','execution_qualified',
                    'fresh_at_report_generation','source_status','result_recorded_at'):
            self.assertEqual(actual[key],expected[key],key)
        self.assertFalse(actual['fresh_at_report_generation']);self.assertFalse(actual['execution_qualified'])
        self.assertIsNone(actual['daily_pnl']);self.assertFalse(actual['automatic_topups'])
        self.assertTrue(actual['held'])

    def test_invalid_public_report_clock_returns_safe_failure_receipt_without_source_text(self):
        from ariadne.briefing_diagnostics import render_preflight
        value=combined();secret='synthetic-sensitive-source-value-do-not-echo'
        value['generated_at_utc']=secret
        outputs,receipt=render_preflight(value)
        self.assertEqual(outputs,{})
        self.assertFalse(receipt['ok']);self.assertEqual(receipt['status'],'failed')
        encoded=json.dumps(receipt)
        self.assertNotIn(secret,encoded)
        self.assertNotIn(str(Path.cwd()),encoded)
        self.assertNotIn('traceback',encoded.lower())
        self.assertIn(receipt['failure_detail']['phase'],('render','validate'))
        self.assertLessEqual(len(receipt['failure_detail']['frames']),8)
        self.assertLess(len(json.dumps(receipt['failure_detail'])),4096)
        self.assertFalse(receipt['artifacts'])

    def test_unrecognized_limit_caveat_is_not_silently_discarded_for_a_small_summary(self):
        from ariadne import briefing_summary as summary
        value=combined();expected=value['options_paper']['paper']['summary']['cash']
        result=compact_summary(value,poster_cases.NOW)
        self.assertEqual(result['options_paper']['cash'],expected)
        custom={'derivation':'Deterministic selection from verified brief.json; not the original artifact bytes or hash.',
                'note':'Unique reviewer approval gate must remain exactly visible.', 'omitted':{},
                'options_paper':{'note':'Owned contract metadata has not been authenticated.','cash':'25000'}}
        before=deepcopy(custom)
        self.assertFalse(summary._compact_repeated_notes(custom))
        self.assertEqual(custom,before)
        with patch.object(summary,'MAX_SUMMARY_CHARS',100):
            with self.assertRaises(ValueError):compact_summary(value,poster_cases.NOW)

    def test_resuming_consumed_day_cannot_invoke_public_execution_or_failed_children(self):
        import test_briefing_resume as resume_cases
        helper=resume_cases.BriefingResumeTests(methodName='runTest')
        helper.setUp();self.addCleanup(helper.doCleanups)
        request,raw=helper.add_receipt();before=helper.row(request)
        helper.resume()
        with helper.forbid_children(), patch.object(helper.service.options.public_paper,'execute',side_effect=AssertionError('No Public paper execution')) as execute:
            self.assertIsNone(helper.service.run_due(helper.now))
            execute.assert_not_called()
        self.assertEqual(helper.row(request),before)
        self.assertEqual(helper.row(request)[2],raw)


if __name__=='__main__':unittest.main()
