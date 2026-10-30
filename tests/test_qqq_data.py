"""Source validation, exchange calendars and narrow collector authority."""
import base64
import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from ariadne.qqq_data import (MarketDataError, MarketDataUnavailable, canonical, collect_bundle,
                             exchange_day, is_session, last_closed_session, parse_chart,
                             session_close_utc, session_dates, source_url)

UTC = timezone.utc
NOW = datetime(2026, 10, 30, 16, tzinfo=UTC)


def fixture(symbol="QQQ", start=date(2026, 10, 26), end=date(2026, 10, 30)):
    days = session_dates(start, end)
    stamps = [int(datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC).timestamp()) for day in days]
    quote = {"open":[100.0]*len(days),"high":[103.0]*len(days),"low":[98.0]*len(days),
             "close":[101.0]*len(days),"volume":[100000]*len(days)}
    return {"chart":{"error":None,"result":[{"meta":{"symbol":symbol,"currency":"USD","instrumentType":"ETF",
                "exchangeTimezoneName":"America/New_York","dataGranularity":"1d"},"timestamp":stamps,
                "indicators":{"quote":[quote],"adjclose":[{"adjclose":[99.0]*len(days)}]},"events":{}}]}}


def payload(value):
    return json.dumps(value, allow_nan=False).encode()


class QQQCalendarTests(unittest.TestCase):
    def test_special_closures_and_regular_holidays(self):
        for day in (date(2001,9,11),date(2001,9,14),date(2004,6,11),date(2007,1,2),
                    date(2012,10,29),date(2012,10,30),date(2018,12,5),date(2025,1,9),
                    date(2026,4,3),date(2026,6,19),date(2026,7,3),date(2026,11,26),date(2026,12,25)):
            with self.subTest(day=day):
                self.assertFalse(is_session(day))

    def test_saturday_new_year_does_not_close_previous_friday(self):
        self.assertTrue(is_session(date(2021,12,31)))
        self.assertTrue(is_session(date(2027,12,31)))
        self.assertFalse(is_session(date(2027,1,1)))
        self.assertFalse(is_session(date(2023,1,2)))

    def test_juneteenth_began_in_2022(self):
        self.assertTrue(is_session(date(2021,6,18)))
        self.assertFalse(is_session(date(2022,6,20)))

    def test_early_close_and_july_observed_holiday(self):
        self.assertEqual(session_close_utc(date(2026,11,27)), datetime(2026,11,27,18,tzinfo=UTC))
        self.assertEqual(session_close_utc(date(2026,12,24)), datetime(2026,12,24,18,tzinfo=UTC))
        self.assertEqual(session_close_utc(date(2026,7,2)), datetime(2026,7,2,20,tzinfo=UTC))
        self.assertEqual(session_close_utc(date(2028,7,3)), datetime(2028,7,3,17,tzinfo=UTC))

    def test_dst_and_finalization_buffer(self):
        self.assertEqual(last_closed_session(NOW),date(2026,10,29))
        self.assertEqual(last_closed_session(datetime(2026,10,30,20,14,59,tzinfo=UTC)),date(2026,10,29))
        self.assertEqual(last_closed_session(datetime(2026,10,30,20,15,tzinfo=UTC)),date(2026,10,30))
        self.assertEqual(last_closed_session(datetime(2026,11,2,14,tzinfo=UTC)),date(2026,10,30))
        self.assertEqual(session_close_utc(date(2026,11,2)),datetime(2026,11,2,21,tzinfo=UTC))
        self.assertEqual(last_closed_session(datetime(2026,11,27,18,15,tzinfo=UTC)),date(2026,11,27))

    def test_calendar_bounds_and_invalid_clock(self):
        for invalid in (date(1998,12,31),date(2036,1,1),"2026-10-30",datetime(2026,10,30)):
            with self.subTest(invalid=invalid), self.assertRaises(MarketDataError):
                is_session(invalid)
        with self.assertRaises(MarketDataError):
            last_closed_session(datetime(2026,10,30))
        for invalid in (False, ""):
            with self.assertRaises(MarketDataError):last_closed_session(invalid)
        with self.assertRaises(MarketDataError):
            session_dates(date(2026,10,30),date(2026,10,29))


class QQQSourceTests(unittest.TestCase):
    def test_completed_ohlcv_is_preserved_and_today_is_explicitly_excluded(self):
        value=fixture()
        value["chart"]["result"][0]["indicators"]["quote"][0]["close"][-1]=None
        raw=payload(value)
        series,quality=parse_chart(raw,"QQQ",NOW)
        self.assertEqual(len(series["bars"]),4)
        self.assertEqual(series["bars"][-1]["date"],"2026-10-29")
        self.assertEqual(series["bars"][-1]["close"],101.0)
        self.assertEqual(series["bars"][-1]["adj_close"],99.0)
        self.assertEqual(series["source"]["sha256"],hashlib.sha256(raw).hexdigest())
        self.assertEqual(quality["excluded_incomplete_dates"],["2026-10-30"])
        self.assertTrue(quality["fresh"])

    def test_today_is_available_only_after_finalization(self):
        series,_=parse_chart(payload(fixture()),"QQQ",datetime(2026,10,30,20,15,tzinfo=UTC))
        self.assertEqual(series["bars"][-1]["date"],"2026-10-30")

    def test_cash_dividend_and_split_are_not_synthetic_adjusted_fill_prices(self):
        value=fixture()
        result=value["chart"]["result"][0]
        stamp=result["timestamp"][1]
        result["events"]={"dividends":{str(stamp):{"date":stamp,"amount":0.5}},
                          "splits":{str(stamp):{"date":stamp,"numerator":2,"denominator":1,"splitRatio":"2:1"}}}
        series,_=parse_chart(payload(value),"QQQ",NOW)
        self.assertEqual(series["bars"][1]["cash_dividend"],0.5)
        self.assertEqual(series["bars"][1]["split_ratio"],2)
        self.assertEqual(series["bars"][1]["open"],100.0)
        self.assertEqual(series["bars"][0]["cash_dividend"],0)

    def test_wrong_metadata_and_missing_arrays_are_rejected(self):
        for field,wrong in (("symbol","OTHER"),("currency","EUR"),("instrumentType","EQUITY"),
                            ("exchangeTimezoneName","UTC"),("dataGranularity","1h")):
            value=fixture();value["chart"]["result"][0]["meta"][field]=wrong
            with self.subTest(field=field),self.assertRaises(MarketDataError):
                parse_chart(payload(value),"QQQ",NOW)
        value=fixture();del value["chart"]["result"][0]["indicators"]["adjclose"]
        with self.assertRaises(MarketDataError):parse_chart(payload(value),"QQQ",NOW)

    def test_semantically_equal_float_split_ratio_is_valid(self):
        value=fixture();result=value["chart"]["result"][0];stamp=result["timestamp"][1]
        result["events"]={"splits":{str(stamp):{"date":stamp,"numerator":2.0,"denominator":1.0,"splitRatio":"2:1"}}}
        series,_=parse_chart(payload(value),"QQQ",NOW)
        self.assertEqual(series["bars"][1]["split_ratio"],2.0)

    def test_completed_bad_ohlcv_or_nulls_are_rejected_without_repair(self):
        for field,wrong in (("open",True),("close",None),("volume",0),("volume",1.5),
                            ("low",102.0),("high",99.0),("adjclose",-1),("close",1e100)):
            value=fixture();result=value["chart"]["result"][0]
            target=result["indicators"]["adjclose"][0] if field=="adjclose" else result["indicators"]["quote"][0]
            target[field][0]=wrong
            with self.subTest(field=field,wrong=wrong),self.assertRaises(MarketDataError):parse_chart(payload(value),"QQQ",NOW)

    def test_array_lengths_and_duplicate_dates_fail(self):
        value=fixture();value["chart"]["result"][0]["indicators"]["quote"][0]["close"].pop()
        with self.assertRaises(MarketDataError):parse_chart(payload(value),"QQQ",NOW)
        value=fixture();value["chart"]["result"][0]["timestamp"][1]=value["chart"]["result"][0]["timestamp"][0]
        with self.assertRaises(MarketDataError):parse_chart(payload(value),"QQQ",NOW)

    def test_missing_session_is_not_skipped(self):
        value=fixture();result=value["chart"]["result"][0]
        result["timestamp"].pop(1)
        for values in result["indicators"]["quote"][0].values():values.pop(1)
        result["indicators"]["adjclose"][0]["adjclose"].pop(1)
        with self.assertRaisesRegex(MarketDataError,"missing"):
            parse_chart(payload(value),"QQQ",NOW)

    def test_stale_last_session_fails_instead_of_becoming_fresh_cache(self):
        with self.assertRaises(MarketDataUnavailable):
            parse_chart(payload(fixture(end=date(2026,10,28))),"QQQ",NOW)

    def test_future_and_weekend_quotes_fail(self):
        for stamp in (int(datetime(2026,11,2,14,30,tzinfo=UTC).timestamp()),int(datetime(2026,10,31,13,30,tzinfo=UTC).timestamp())):
            value=fixture();value["chart"]["result"][0]["timestamp"][-1]=stamp
            with self.assertRaises(MarketDataError):parse_chart(payload(value),"QQQ",NOW)

    def test_invalid_actions_and_unmatched_actions_fail(self):
        for change in ("currency","key","ratio","missing_bar","capital_gains"):
            value=fixture();result=value["chart"]["result"][0];stamp=result["timestamp"][1]
            if change=="ratio":result["events"]={"splits":{str(stamp):{"date":stamp,"numerator":2,"denominator":1,"splitRatio":"3:1"}}}
            elif change=="capital_gains":result["events"]={"capitalGains":{"a":{"amount":1}}}
            else:
                if change=="missing_bar":stamp=int(datetime(2026,10,23,13,30,tzinfo=UTC).timestamp())
                result["events"]={"dividends":{str(stamp):{"date":stamp,"amount":0.5,"currency":"EUR" if change=="currency" else "USD"}}}
                if change=="key":result["events"]["dividends"]["wrong"]=result["events"]["dividends"].pop(str(stamp))
            with self.subTest(change=change),self.assertRaises(MarketDataError):parse_chart(payload(value),"QQQ",NOW)

    def test_duplicate_json_keys_nonfinite_and_oversize_fail(self):
        for raw in (b'{"chart":{},"chart":{}}',b'{"x":NaN}',b'[]',b'\xff',b' '*2_000_001,b'{"x":'+b'9'*5000+b'}'):
            with self.subTest(raw=raw[:40]),self.assertRaises(MarketDataError):parse_chart(raw,"QQQ",NOW)

    def test_public_error_and_full_history_truncation_fail(self):
        with self.assertRaises(MarketDataError):parse_chart(b'{"chart":{"error":{"code":"Unauthorized"},"result":null}}',"QQQ",NOW)
        with self.assertRaisesRegex(MarketDataError,"full history"):
            parse_chart(payload(fixture()),"QQQ",NOW,full_history=True)

    def test_only_fixed_symbol_can_form_a_network_url(self):
        for symbol in ("QQQ/../../","SPY?url=http://localhost",None,[],"AAPL"):
            with self.subTest(symbol=symbol),self.assertRaises(MarketDataError):source_url(symbol,NOW)
        self.assertTrue(source_url("SPY",NOW).startswith("https://query1.finance.yahoo.com/v8/finance/chart/SPY?"))

    def test_real_network_requires_authorized_child_flags(self):
        with patch.dict(os.environ,{"ARIADNE_QQQ_COLLECTOR":"0","ARIADNE_OFFLINE":"1"}),patch("ariadne.internet.public_get") as getter:
            with self.assertRaises(MarketDataUnavailable):collect_bundle(NOW)
            getter.assert_not_called()

    def test_fixed_get_guards_and_readonly_transport(self):
        raw=payload(fixture());series,quality=parse_chart(raw,"QQQ",NOW)
        requested=[];checks=[]
        def fetch(url):requested.append(url);return raw
        def parser(raw,symbol,now,*,full_history):
            result=copy.deepcopy(series);result["source"]["url"]=source_url(symbol,now)
            return result,{**quality,"symbol":symbol}
        with patch("ariadne.qqq_data.parse_chart",side_effect=parser):
            result=collect_bundle(NOW,fetch=fetch,check=lambda:checks.append(True))
        self.assertEqual(len(requested),2)
        self.assertIn("/QQQ?",requested[0]);self.assertIn("/SPY?",requested[1])
        self.assertEqual(len(checks),5)
        self.assertEqual(base64.b64decode(result["raw_payloads"]["QQQ"]),raw)
        self.assertEqual(result["quality"]["requests"],2)
        self.assertEqual(result["dataset"]["price_basis"],"split_adjusted_ohlc")

    def test_policy_revocation_escapes_before_second_get(self):
        calls=[]
        def check():
            calls.append(True)
            if len(calls)==2:raise PermissionError("revoked")
        with self.assertRaises(PermissionError),patch("ariadne.qqq_data.parse_chart") as parser:
            collect_bundle(NOW,fetch=lambda url:payload(fixture()),check=check)
        parser.assert_not_called()

    def test_actual_receipt_clocks_and_common_analysis_clock_are_distinct(self):
        import ariadne.qqq_data as module
        raw=payload(fixture());series,quality=parse_chart(raw,"QQQ",NOW)
        clocks=iter([NOW,NOW+timedelta(seconds=2),NOW+timedelta(seconds=4),NOW+timedelta(seconds=5)])
        original=module._clock
        def clock(value=None):return next(clocks) if value is None else original(value)
        with patch("ariadne.qqq_data._clock",side_effect=clock),patch("ariadne.qqq_data.parse_chart",side_effect=lambda *args,**kwargs:(copy.deepcopy(series),copy.deepcopy(quality))):
            result=collect_bundle(fetch=lambda url:raw)
        self.assertEqual(result["dataset"]["as_of"],(NOW+timedelta(seconds=5)).isoformat())
        self.assertEqual(result["dataset"]["symbols"]["QQQ"]["source"]["retrieved_at"],(NOW+timedelta(seconds=2)).isoformat())
        self.assertEqual(result["dataset"]["symbols"]["SPY"]["source"]["retrieved_at"],(NOW+timedelta(seconds=4)).isoformat())
        self.assertEqual(result["dataset"]["symbols"]["QQQ"]["source"]["url"],source_url("QQQ",NOW))

    def test_fixed_module_worker_request_rejects_boolean_schema_before_collection(self):
        from ariadne.qqq_data import main
        for raw in (b'{"schema":true}',b'{"schema":1,"url":"https://outside.invalid"}',b'{}'):
            with self.subTest(raw=raw),patch.dict(os.environ,{"ARIADNE_QQQ_COLLECTOR":"1","ARIADNE_OFFLINE":"0"}),\
                 patch("ariadne.qqq_data.sys.stdin",SimpleNamespace(buffer=io.BytesIO(raw))),\
                 patch("ariadne.network_policy.install_network_policy"),patch("ariadne.qqq_data.collect_bundle") as collect:
                with self.assertRaises(SystemExit):main()
                collect.assert_not_called()


if __name__=="__main__":unittest.main()
