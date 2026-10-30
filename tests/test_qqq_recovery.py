"""Financial chronology, split/dividend basis and forward-paper invariants."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext, localcontext
import unittest

from ariadne.qqq_data import session_dates, session_close_utc
from ariadne.qqq_recovery import (QQQRecoveryError, RULE_SHA256, advance_paper,
                                  backtest, initial_state, next_session_open, signal, validate_dataset)

UTC = timezone.utc


def fixture(count=130):
    days = session_dates(datetime(2004,1,2).date(),datetime(2005,12,30).date())[:count]
    result = {"schema":1,"as_of":(session_close_utc(days[-1])+timedelta(hours=1)).isoformat(),"currency":"USD",
              "price_basis":"split_adjusted_ohlc","dividend_policy":"split_adjusted_cash_on_ex_date","symbols":{}}
    for symbol in ("QQQ","SPY"):
        bars=[]
        for index, day in enumerate(days):
            close = Decimal(100) if symbol=="QQQ" else Decimal(100)+Decimal(index)/10
            if symbol=="QQQ":
                if 70 <= index <= 89:close=Decimal(90)
                elif index>=90:close=Decimal(101)+Decimal(index-90)/10
            bars.append({"date":day.isoformat(),"open":str(close),"high":str(close+1),"low":str(close-1),"close":str(close),
                         "volume":1000000,"adj_close":str(close),"cash_dividend":"0","split_ratio":"1"})
        result["symbols"][symbol]={"source":{"url":"https://example.com/"+symbol,"sha256":"a"*64,"retrieved_at":result["as_of"]},"bars":bars}
    return result


def through(dataset,count):
    value=deepcopy(dataset)
    for symbol in value["symbols"]:
        value["symbols"][symbol]["bars"]=value["symbols"][symbol]["bars"][:count]
    day=datetime.fromisoformat(value["symbols"]["QQQ"]["bars"][-1]["date"]).date()
    value["as_of"]=(session_close_utc(day)+timedelta(hours=1)).isoformat()
    for entry in value["symbols"].values():entry["source"]["retrieved_at"]=value["as_of"]
    return value


class QQQRecoveryTests(unittest.TestCase):
    def test_frozen_rule_ready_after_warmup_and_strict_breakout(self):
        data=fixture()
        decision=signal(through(data,91))
        self.assertEqual(decision["action"],"BUY")
        self.assertEqual(decision["rule_sha256"],RULE_SHA256)
        self.assertTrue(decision["inputs"]["setup_dates"])
        before=signal(through(data,90))
        self.assertEqual(before["action"],"NO_TRADE")
        self.assertEqual(signal(through(data,82))["reasons"],["insufficient_warmup"])
        changed=through(data,91)
        changed["symbols"]["QQQ"]["bars"][-1].update(open="91",close="91",low="90",high="92")
        self.assertEqual(signal(changed)["action"],"NO_TRADE") # tie with prior high

    def test_spy_sma_includes_current_and_equal_is_not_confirmation(self):
        data=through(fixture(),91)
        for row in data["symbols"]["SPY"]["bars"]:
            row.update(open="100",close="100",high="101",low="99")
        self.assertIn("spy_not_above_20_session_sma",signal(data)["reasons"])
        data["symbols"]["SPY"]["bars"][-1].update(open="101",close="101",high="102",low="100")
        self.assertEqual(signal(data)["action"],"BUY")

    def test_setup_window_does_not_recompute_old_drawdowns_with_a_later_peak(self):
        data=fixture(130)
        decision=signal(data)
        self.assertEqual(decision["inputs"]["setup_dates"],[])
        self.assertIn("no_recent_8_percent_drawdown_setup",decision["reasons"])
        self.assertIn(data["symbols"]["QQQ"]["bars"][84]["date"],signal(through(data,100))["inputs"]["setup_dates"])

    def test_current_incomplete_and_future_bars_are_excluded(self):
        data=fixture(91)
        day=datetime.fromisoformat(data["symbols"]["QQQ"]["bars"][-1]["date"]).date()
        data["as_of"]=(session_close_utc(day)+timedelta(minutes=14)).isoformat()
        for entry in data["symbols"].values():entry["source"]["retrieved_at"]=data["as_of"]
        result=signal(data)
        self.assertEqual(result["session"],data["symbols"]["QQQ"]["bars"][-2]["date"])
        self.assertEqual(result["data"]["excluded_incomplete_or_future_bars"],{"QQQ":1,"SPY":1})
        data["as_of"]=(session_close_utc(day)+timedelta(minutes=15)).isoformat()
        self.assertEqual(signal(data)["action"],"BUY")

    def test_early_close_uses_shared_calendar_finalization_not_regular_close(self):
        data=fixture(250)
        target="2004-11-26"
        index=next(i for i,row in enumerate(data["symbols"]["QQQ"]["bars"]) if row["date"]==target)
        data=through(data,index+1)
        day=datetime.fromisoformat(target).date()
        data["as_of"]=(session_close_utc(day)+timedelta(minutes=15)).isoformat()
        for entry in data["symbols"].values():entry["source"]["retrieved_at"]=data["as_of"]
        self.assertEqual(validate_dataset(data)["last_date"],target)
        self.assertEqual(datetime.fromisoformat(data["as_of"]).hour,18) # 13:15 Eastern, well before regular close

    def test_bad_shapes_prices_ohlc_order_and_calendar_gaps_fail(self):
        base=fixture(100)
        cases=[]
        for key,value in (("schema",True),("price_basis","raw"),("dividend_policy","reinvested")):
            changed=deepcopy(base);changed[key]=value;cases.append(changed)
        for key,value in (("close",float("nan")),("open",True),("volume",1.0),("low","999"),("split_ratio","0"),("cash_dividend","-1")):
            changed=deepcopy(base);changed["symbols"]["QQQ"]["bars"][5][key]=value;cases.append(changed)
        changed=deepcopy(base);changed["symbols"]["QQQ"]["bars"][5]["date"]=changed["symbols"]["QQQ"]["bars"][4]["date"];cases.append(changed)
        changed=deepcopy(base);changed["symbols"]["SPY"]["bars"].pop(10);cases.append(changed)
        changed=deepcopy(base)
        for entry in changed["symbols"].values():entry["bars"].pop(10)
        cases.append(changed)
        for value in cases:
            with self.subTest(value=value.get("price_basis")):
                with self.assertRaises(ValueError):validate_dataset(value)

    def test_source_claims_cannot_have_credentials_or_future_retrieval(self):
        data=fixture()
        for url in ("http://example.com/a","https://user:secret@example.com/a","https://example.com/a#fragment","https://example.com:80/a"):
            changed=deepcopy(data);changed["symbols"]["QQQ"]["source"]["url"]=url
            with self.assertRaises(QQQRecoveryError):validate_dataset(changed)
        changed=deepcopy(data);changed["symbols"]["QQQ"]["source"]["retrieved_at"]="2030-01-01T00:00:00Z"
        with self.assertRaises(QQQRecoveryError):validate_dataset(changed)

    def test_next_open_fills_never_signal_day_open_and_terminal_signal_is_unfilled(self):
        data=fixture()
        terminal=backtest(through(data,91))
        self.assertEqual(terminal["strategy"]["pending"]["action"],"BUY")
        self.assertEqual(terminal["strategy"]["fills"],[])
        data["symbols"]["QQQ"]["bars"][91].update(open="110",high="112",low="101",close="105")
        report=backtest(data)
        buy=report["strategy"]["fills"][0]
        self.assertEqual(buy["signal_session"],data["symbols"]["QQQ"]["bars"][90]["date"])
        self.assertEqual(buy["session"],data["symbols"]["QQQ"]["bars"][91]["date"])
        self.assertEqual(buy["price"],"110")

    def test_prefix_invariance_for_signals_fills_and_equity(self):
        data=fixture(130)
        short=backtest(through(data,112))
        extended=backtest(data)
        cutoff=data["symbols"]["QQQ"]["bars"][111]["date"]
        for key in ("curve","fills","signals"):
            date_key="date" if key=="curve" else "session"
            self.assertEqual(short["strategy"][key],[row for row in extended["strategy"][key] if row[date_key]<=cutoff])

    def test_exit_gap_uses_following_open_and_no_intraday_stop(self):
        data=fixture(115)
        data["symbols"]["QQQ"]["bars"][95].update(open="103",high="104",low="80",close="100")
        # A touched intraday low alone does not exit while close remains above
        # the prior ten-session floor.
        report=backtest(through(data,96))
        self.assertEqual(len(report["strategy"]["fills"]),1)
        data["symbols"]["QQQ"]["bars"][96].update(open="90",high="91",low="79",close="79")
        data["symbols"]["QQQ"]["bars"][97].update(open="60",high="65",low="59",close="64")
        report=backtest(data)
        sell=next(row for row in report["strategy"]["fills"] if row["action"]=="SELL")
        self.assertEqual(sell["price"],"60")
        self.assertEqual(sell["signal_session"],data["symbols"]["QQQ"]["bars"][96]["date"])

    def test_holding_twenty_sessions_exits_next_open(self):
        data=fixture(115)
        report=backtest(data)
        sell=next(row for row in report["strategy"]["fills"] if row["action"]=="SELL")
        self.assertEqual(sell["session"],data["symbols"]["QQQ"]["bars"][111]["date"])
        self.assertIn("20_completed_sessions",sell["reasons"])

    def test_cash_dividend_is_carried_holder_credit_not_adjusted_close_multiplier(self):
        data=fixture(100)
        data["symbols"]["QQQ"]["bars"][91]["cash_dividend"]="50" # same-open buyer is ineligible
        before=backtest(data,cost_bps=0)
        data["symbols"]["QQQ"]["bars"][92]["cash_dividend"]="2"
        for row in data["symbols"]["QQQ"]["bars"]:row["adj_close"]="999"
        after=backtest(data,cost_bps=0)
        self.assertEqual(before["strategy"]["cash_dividends"],"0")
        with localcontext() as context:
            context.prec=40
            self.assertEqual(Decimal(after["strategy"]["cash_dividends"]),Decimal(after["strategy"]["fills"][0]["shares_after"])*2)
        self.assertEqual(before["strategy"]["fills"],after["strategy"]["fills"])

    def test_split_adjusted_input_is_not_split_adjusted_twice(self):
        data=fixture(100)
        before=backtest(data)
        data["symbols"]["QQQ"]["bars"][92]["split_ratio"]="2"
        after=backtest(data)
        self.assertEqual(before["strategy"],after["strategy"])

    def test_cost_stresses_and_benchmarks_have_same_calendar(self):
        report=backtest(fixture())
        self.assertEqual([row["bps_per_leg"] for row in report["cost_sensitivity"]],[5,10,25,50])
        levels=[Decimal(row["metrics"]["ending_equity"]) for row in report["cost_sensitivity"]]
        self.assertEqual(levels,sorted(levels,reverse=True))
        self.assertEqual(report["strategy"]["metrics"]["start_date"],report["benchmarks"]["qqq_buy_and_hold"]["metrics"]["start_date"])
        self.assertEqual(report["benchmarks"]["cash"]["metrics"]["total_return"],0)
        self.assertFalse(report["execution_qualified"])
        self.assertFalse(report["future_profit_proven"])
        self.assertEqual([row["id"] for row in report["folds"]],["train","validation","retrospective_holdout"])

    def test_global_decimal_context_cannot_change_research(self):
        baseline=backtest(fixture(100))
        previous=getcontext().prec
        try:
            getcontext().prec=5
            changed=backtest(fixture(100))
        finally:getcontext().prec=previous
        self.assertEqual(baseline,changed)

    def test_forward_activation_starts_cash_without_historical_signal_adoption(self):
        data=through(fixture(),91)
        state=initial_state(data,data["as_of"])
        self.assertEqual(state["shares"],"0")
        self.assertEqual(state["cash"],"10000")
        self.assertIsNone(state["pending"])
        result=advance_paper(data,state)
        self.assertEqual(result["status"],"no_new_session")
        self.assertEqual(result["events"],[])
        with self.assertRaises(QQQRecoveryError):initial_state(data,"2000-01-01T00:00:00Z")

    def _paper_signal(self):
        data=fixture()
        baseline=through(data,90)
        state=initial_state(baseline,baseline["as_of"])
        result=advance_paper(through(data,91),state)
        self.assertEqual(result["state"]["pending"]["action"],"BUY")
        self.assertEqual(result["state"]["shares"],"0")
        return data,result["state"]

    def test_forward_records_decision_then_next_open_fill_with_decimal_ledger(self):
        data,state=self._paper_signal()
        result=advance_paper(through(data,92),state)
        self.assertEqual(result["state"]["trade_count"],1)
        self.assertEqual(result["state"]["holding_sessions"],1)
        self.assertGreater(Decimal(result["state"]["shares"]),0)
        fill=next(row for row in result["events"] if row["type"]=="paper_fill")
        self.assertGreater(datetime.fromisoformat(fill["assumed_fill_at"]),datetime.fromisoformat(fill["decided_at"]))
        self.assertTrue(fill["simulated"])
        self.assertFalse(fill["actual_broker_fill"])
        self.assertEqual(advance_paper(through(data,92),result["state"])["status"],"no_new_session")

    def test_forward_rejects_gap_backdated_decision_bad_ledger_and_split_transition(self):
        data,state=self._paper_signal()
        with self.assertRaises(QQQRecoveryError):advance_paper(through(data,93),state)
        late=deepcopy(state)
        late["pending"]["decided_at"]=through(data,92)["as_of"]
        with self.assertRaises(QQQRecoveryError):advance_paper(through(data,92),late)
        broken=deepcopy(state);broken["cash"]="9999"
        with self.assertRaises(QQQRecoveryError):advance_paper(through(data,92),broken)
        split=through(data,92);split["symbols"]["QQQ"]["bars"][-1]["split_ratio"]="2"
        with self.assertRaises(QQQRecoveryError):advance_paper(split,state)
        changed=through(data,92)
        changed["symbols"]["QQQ"]["bars"][-2]["close"]="100.5"
        with self.assertRaises(QQQRecoveryError):advance_paper(changed,state)

    def test_forward_buy_exdate_has_no_dividend_then_carried_holder_receives_cash(self):
        data,state=self._paper_signal()
        data["symbols"]["QQQ"]["bars"][91]["cash_dividend"]="2"
        first=advance_paper(through(data,92),state)
        self.assertEqual(first["state"]["total_dividends"],"0")
        data["symbols"]["QQQ"]["bars"][92]["cash_dividend"]="1"
        second=advance_paper(through(data,93),first["state"])
        self.assertEqual(second["state"]["total_dividends"],first["state"]["shares"])
        self.assertEqual(second["state"]["cash"],first["state"]["shares"])

    def test_forward_state_is_not_mutated_when_rejected_or_advanced(self):
        data,state=self._paper_signal()
        before=deepcopy(state)
        advance_paper(through(data,92),state)
        self.assertEqual(state,before)
        wrong=deepcopy(state);wrong["rule_sha256"]="0"*64
        with self.assertRaises(QQQRecoveryError):advance_paper(through(data,92),wrong)
        wrong=deepcopy(state);wrong["pending"]["action"]="SELL"
        with self.assertRaises(QQQRecoveryError):advance_paper(through(data,92),wrong)

    def test_actual_late_decision_clock_cannot_backdate_a_future_open_fill(self):
        data=fixture()
        baseline=through(data,90)
        state=initial_state(baseline,baseline["as_of"])
        next_open=next_session_open(data["symbols"]["QQQ"]["bars"][90]["date"])
        for clock in (next_open+timedelta(minutes=1),next_open-timedelta(seconds=30)):
            with self.assertRaises(QQQRecoveryError):
                advance_paper(through(data,91),state,decided_at=clock.isoformat())
        result=advance_paper(through(data,91),state,decided_at=(next_open-timedelta(seconds=61)).isoformat())
        self.assertEqual(result["state"]["pending"]["decided_at"],(next_open-timedelta(seconds=61)).isoformat())
        with self.assertRaises(QQQRecoveryError):advance_paper(through(data,91),state,decided_at=baseline["as_of"])


if __name__=="__main__":unittest.main()
