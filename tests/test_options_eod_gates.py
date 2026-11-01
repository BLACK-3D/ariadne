"""Frozen legacy traces plus trusted entry-gate boundaries; synthetic only."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import unittest

from ariadne import options_eod as engine
from ariadne.qqq_data import session_dates
from test_options_eod import state, snapshot, quote, opened, received, next_session


def trace(seed):
    current=state();results=[]
    days=session_dates(date(2024,1,2),date(2024,2,9))
    for index,day in enumerate(days):
        price=Decimal(12)+Decimal(((index*(seed+3))%31)-15)/20
        expiry="2024-01-19" if day<=date(2024,1,16) else "2024-01-31" if day<=date(2024,1,31) else "2024-02-16"
        chain=[quote(expiry=expiry,kind=kind,strike=str(Decimal(strike)/2),
            bid=str(Decimal(25+(index+seed)%8)/100),ask=str(Decimal(29+(index+seed)%8)/100),
            bid_size=0 if seed%4==0 and index%7==0 else 4,ask_size=0 if seed%5==0 and index%3==0 else 4)
            for strike in range(21,28) for kind in ("call","put")]
        row=snapshot(day,price=str(price),quotes=chain)
        clock=max(current['last_decided_at'],row['received_at'])
        value=engine.advance_paper(row,current,decided_at=clock)
        results.append(value);current=value['state']
        if current['hold']:break
    return hashlib.sha256(engine._canonical(results)).hexdigest()


class EODEntryGateTests(unittest.TestCase):
    def test_public_legacy_api_matches_frozen_complete_traces(self):
        value=json.loads((Path(__file__).parent/'fixtures/options_eod_legacy_traces.json').read_bytes())
        self.assertEqual(value['legacy_rule_sha256'],engine.RULE_SHA256)
        for seed,expected in value['traces'].items():
            with self.subTest(seed=seed):self.assertEqual(trace(int(seed)),expected)

    def test_non_callable_or_non_boolean_hooks_are_rejected(self):
        row=snapshot();current=state()
        for name in ('decision_gate','fill_gate'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                engine._advance_paper(row,current,decided_at=current['last_decided_at'],**{name:'not code'})
        with self.assertRaises(ValueError):
            engine._advance_paper(row,current,decided_at=current['last_decided_at'],decision_gate=lambda *args:1)

    def test_denied_entry_does_not_create_pending_or_spend(self):
        current=state();row=snapshot(price='12.05')
        value=engine._advance_paper(row,current,decided_at=current['last_decided_at'],decision_gate=lambda *args:False)
        self.assertIsNone(value['state']['pending']);self.assertEqual(value['state']['fees'],'0')
        self.assertEqual(value['state']['cash'],current['cash'])

    def test_denied_fill_cancels_only_pending_buy(self):
        current=state()
        row=snapshot(price='12.05');current=engine.advance_paper(row,current,decided_at=current['last_decided_at'])['state']
        self.assertEqual(current['pending']['action'],'BUY')
        waiting=snapshot(date(2024,1,3),price='12.10')
        current=engine._advance_paper(waiting,current,decided_at=waiting['received_at'],
            decision_gate=lambda *args:False,fill_gate=lambda *args:False)['state']
        row=snapshot(date(2024,1,4),price='12.10')
        value=engine._advance_paper(row,current,decided_at=row['received_at'],
            decision_gate=lambda *args:False,fill_gate=lambda *args:False)
        self.assertIsNone(value['state']['pending']);self.assertIsNone(value['state']['position'])
        self.assertEqual(value['state']['cash'],current['cash'])
        self.assertTrue(any(e.get('reason')=='entry_policy_denied' for e in value['events']))

    def test_denied_entry_policy_never_intercepts_position_disposal(self):
        current=opened();deadline=date.fromisoformat(current['position']['forced_exit_session'])
        # Supply every source session. The entry gate cannot suppress exits.
        last=date.fromisoformat(current['last_source_date'])
        for day in session_dates(next_session(last),deadline):
            row=snapshot(day,price='12',quotes=[quote()])
            value=engine._advance_paper(row,current,decided_at=row['received_at'],
                decision_gate=lambda *args:False,fill_gate=lambda *args:False)
            current=value['state']
        self.assertIsNone(current['position']);self.assertEqual(current['closed_trade_count'],1)


if __name__=='__main__':unittest.main()
