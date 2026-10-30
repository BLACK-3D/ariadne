"""Fixed QQQ/SPY daily public-data intake; never orders or parent policy changes.

Yahoo's public chart endpoint is an unofficial, revocable personal-research
source, not an exchange feed or an execution guarantee. Original payloads travel
with normalized bars so the owner service can retain immutable evidence. Prices
are vendor split-adjusted OHLC; dividends are cash events on that same basis.
Adjusted close is diagnostic only and is never substituted for a fill price.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from fractions import Fraction
from urllib.parse import urlencode

UTC = timezone.utc
SYMBOLS = ("QQQ", "SPY")
START_DATE = date(1999, 3, 10)
MAX_ROWS = 8000
MAX_PAYLOAD = 2_000_000
MAX_OUTPUT = 12*1024*1024
FINALIZATION_MINUTES = 15
CALENDAR_START = 1999
CALENDAR_END = 2035
HOST = "query1.finance.yahoo.com"
_SPECIAL_CLOSURES = {
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11), date(2007, 1, 2), date(2012, 10, 29), date(2012, 10, 30),
    date(2018, 12, 5), date(2025, 1, 9),
}


class MarketDataError(ValueError):
    """Unusable source data; cached evidence may be read but not treated as fresh."""


class MarketDataUnavailable(MarketDataError):
    """A public source is unavailable, stale or access-restricted; no bypass."""


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _clock(value=None):
    value = datetime.now(UTC) if value is None else value
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise MarketDataError("An aware market-data clock is required.") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError("An aware market-data clock is required.")
    return value.astimezone(UTC)


def _day(value):
    if not isinstance(value, date) or isinstance(value, datetime) or not CALENDAR_START <= value.year <= CALENDAR_END:
        raise MarketDataError("The reviewed US-equity calendar supports 1999–2035 only.")
    return value


def _nth_weekday(year, month, weekday, ordinal):
    first = date(year, month, 1)
    return first+timedelta(days=(weekday-first.weekday()) % 7+7*(ordinal-1))


def _last_weekday(year, month, weekday):
    following = date(year+1, 1, 1) if month == 12 else date(year, month+1, 1)
    last = following-timedelta(days=1)
    return last-timedelta(days=(last.weekday()-weekday) % 7)


def _observed(day):
    return day-timedelta(days=1) if day.weekday() == 5 else day+timedelta(days=1) if day.weekday() == 6 else day


def _easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b+8)//25
    g = (b-f+1)//3
    h = (19*a+b-d-g+15) % 30
    i, k = c//4, c % 4
    l = (32+2*e+2*i-h-k) % 7
    m = (a+11*h+22*l)//451
    return date(year, (h+l-7*m+114)//31, (h+l-7*m+114) % 31+1)


@lru_cache(maxsize=40)
def _holidays(year):
    result = {_nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3),
              _easter(year)-timedelta(days=2), _last_weekday(year, 5, 0),
              _observed(date(year, 7, 4)), _nth_weekday(year, 9, 0, 1),
              _nth_weekday(year, 11, 3, 4), _observed(date(year, 12, 25))}
    new_year = date(year, 1, 1)
    # A Saturday New Year's Day has no Friday stock-market observance.
    if new_year.weekday() != 5:
        result.add(_observed(new_year))
    if year >= 2022:
        result.add(_observed(date(year, 6, 19)))
    return frozenset(result)


def is_session(day):
    day = _day(day)
    return day.weekday() < 5 and day not in _holidays(day.year) and day not in _SPECIAL_CLOSURES


def session_dates(start, end):
    start, end = _day(start), _day(end)
    if end < start or (end-start).days > 14000:
        raise MarketDataError("Use an ordered bounded session interval.")
    return [start+timedelta(days=offset) for offset in range((end-start).days+1) if is_session(start+timedelta(days=offset))]


def exchange_day(now=None):
    from .briefing import _tz
    # Eastern and Chicago share US DST transition dates throughout this range.
    return (_clock(now).astimezone(_tz("America/Chicago"))+timedelta(hours=1)).date()


def session_close_utc(day):
    from .briefing import _tz
    day = _day(day)
    if not is_session(day):
        raise MarketDataError("A non-session has no market close.")
    thanksgiving = _nth_weekday(day.year, 11, 3, 4)
    early = (day == thanksgiving+timedelta(days=1) or
             day.month == 12 and day.day == 24 or
             day.month == 7 and day.day == 3)
    # Historical early closes before Saturday holidays varied; using a regular
    # close there is conservative. Current scheduled early closes above are 1ET.
    return datetime(day.year, day.month, day.day, 12 if early else 15,
                    tzinfo=_tz("America/Chicago")).astimezone(UTC)


def last_closed_session(now=None):
    now = _clock(now)
    day = _day(exchange_day(now))
    for offset in range(15):
        candidate = day-timedelta(days=offset)
        if is_session(candidate) and now >= session_close_utc(candidate)+timedelta(minutes=FINALIZATION_MINUTES):
            return candidate
    raise MarketDataError("No completed session exists within the bounded calendar lookback.")


def source_url(symbol, now=None):
    if symbol not in SYMBOLS or type(symbol) is not str:
        raise MarketDataError("Only the fixed QQQ and SPY research symbols are supported.")
    now = _clock(now)
    _day(exchange_day(now))
    query = urlencode({"period1":int(datetime(1999, 3, 10, tzinfo=UTC).timestamp()),
                       "period2":int(now.timestamp()), "interval":"1d", "events":"div,splits", "includeAdjustedClose":"true"})
    return "https://"+HOST+"/v8/finance/chart/"+symbol+"?"+query


def _strict_json(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PAYLOAD:
        raise MarketDataError("A market response must contain at most 2 MB of JSON.")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MarketDataError("Duplicate market-response keys are rejected.")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(MarketDataError("Nonfinite market values are rejected.")))
    except (UnicodeError, ValueError, RecursionError, OverflowError):
        raise MarketDataError("The market response is not bounded ordinary JSON.") from None


def _number(value, *, zero=False, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise MarketDataError("Market numbers must be finite, typed and nonnegative or positive as appropriate.")
    if integer and (type(value) is not int or value > 10**13) or not integer and value > 10**7:
        raise MarketDataError("A market number exceeds its supported bound.")
    return value


def _timestamp(value):
    if type(value) is not int or not 0 < value < 2**34:
        raise MarketDataError("Market timestamps must be bounded integer seconds.")
    return exchange_day(datetime.fromtimestamp(value, UTC))


def parse_chart(raw, symbol, now=None, *, full_history=False):
    """Validate source bytes; return a symbol series and explicit quality notes."""
    if symbol not in SYMBOLS or type(full_history) is not bool:
        raise MarketDataError("A fixed symbol and explicit full-history policy are required.")
    now = _clock(now)
    expected, today = last_closed_session(now), exchange_day(now)
    value = _strict_json(raw)
    try:
        chart = value["chart"]
        if type(chart) is not dict or chart.get("error") is not None or type(chart["result"]) is not list or len(chart["result"]) != 1:
            raise MarketDataError("The provider returned no single successful symbol response.")
        result = chart["result"][0]
        meta = result["meta"]
        if (meta["symbol"] != symbol or meta["currency"] != "USD" or meta["instrumentType"] != "ETF" or
                meta["exchangeTimezoneName"] != "America/New_York" or meta["dataGranularity"] != "1d"):
            raise MarketDataError("Symbol, currency, instrument, interval or exchange time zone disagrees with this workflow.")
        stamps = result["timestamp"]
        quotes = result["indicators"]["quote"]
        adjusted = result["indicators"]["adjclose"]
        if type(stamps) is not list or not 1 <= len(stamps) <= MAX_ROWS or type(quotes) is not list or len(quotes) != 1 or type(adjusted) is not list or len(adjusted) != 1:
            raise MarketDataError("Expected one bounded daily quote and adjusted-close array.")
        arrays = {key:quotes[0][key] for key in ("open","high","low","close","volume")}
        arrays["adj_close"] = adjusted[0]["adjclose"]
        if any(type(rows) is not list or len(rows) != len(stamps) for rows in arrays.values()):
            raise MarketDataError("Daily market arrays have mismatched lengths.")
        events = result.get("events", {})
        if type(events) is not dict or set(events)-{"dividends","splits","capitalGains"} or events.get("capitalGains"):
            raise MarketDataError("Unsupported source cash actions cannot be ignored.")
        actions = {}
        for kind in ("dividends","splits"):
            supplied = events.get(kind,{})
            if type(supplied) is not dict or len(supplied) > MAX_ROWS:
                raise MarketDataError("Source actions must be bounded objects.")
            for key, item in supplied.items():
                if type(item) is not dict or key != str(item["date"]):
                    raise MarketDataError("Action identity and timestamp disagree.")
                day = _timestamp(item["date"])
                if day > today or not is_session(day):
                    raise MarketDataError("A source action has a future or non-session date.")
                if kind in actions.setdefault(day, {}):
                    raise MarketDataError("Duplicate source actions are rejected.")
                if kind == "dividends":
                    if item.get("currency","USD") != "USD":
                        raise MarketDataError("A source dividend uses another currency.")
                    actions[day][kind] = _number(item["amount"])
                else:
                    numerator, denominator = _number(item["numerator"]), _number(item["denominator"])
                    ratio = numerator/denominator
                    text = item.get("splitRatio")
                    if (type(text) is not str or len(text) > 64 or
                            not re.fullmatch(r"[1-9][0-9]{0,8}(?:\.[0-9]{1,8})?:[1-9][0-9]{0,8}(?:\.[0-9]{1,8})?",text)):
                        raise MarketDataError("A source split needs a bounded explicit ratio.")
                    left,right = text.split(":")
                    if not 0.01 <= ratio <= 100 or Fraction(left)/Fraction(right) != Fraction(str(numerator))/Fraction(str(denominator)):
                        raise MarketDataError("A source split has an inconsistent or unsupported ratio.")
                    actions[day][kind] = ratio
        bars, excluded, previous = [], [], None
        for index, stamp in enumerate(stamps):
            day = _timestamp(stamp)
            if previous is not None and day <= previous:
                raise MarketDataError("Market sessions must be unique and strictly ascending.")
            previous = day
            if day > today or not is_session(day):
                raise MarketDataError("A quote has a future or non-session date.")
            if day > expected:
                excluded.append(day.isoformat())
                continue
            row = {"date":day.isoformat(), **{key:_number(values[index], integer=key=="volume") for key,values in arrays.items()}}
            if row["low"] > min(row["open"],row["close"]) or row["high"] < max(row["open"],row["close"]) or row["low"] > row["high"]:
                raise MarketDataError("A completed bar violates its OHLC bounds; it is never repaired silently.")
            if not 0.05 <= row["adj_close"]/row["close"] <= 2:
                raise MarketDataError("Adjusted close is outside the supported diagnostic range.")
            row["cash_dividend"] = actions.get(day,{}).get("dividends",0)
            row["split_ratio"] = actions.get(day,{}).get("splits",1)
            if row["cash_dividend"] >= row["close"]:
                raise MarketDataError("A source distribution exceeds this workflow's supported range.")
            bars.append(row)
        if not bars:
            raise MarketDataError("No completed daily sessions were supplied.")
        actual = {date.fromisoformat(row["date"]) for row in bars}
        required = set(session_dates(min(actual),max(actual)))
        if actual != required:
            raise MarketDataError("A required exchange session is missing; no bar is fabricated or skipped.")
        if any(day <= expected and day not in actual for day in actions):
            raise MarketDataError("A completed source action has no matching market bar.")
        if full_history and (bars[0]["date"] != START_DATE.isoformat() or len(bars) < 5000 or len(events.get("dividends",{})) < 20):
            raise MarketDataError("The requested full history or cash-action coverage is incomplete.")
        if max(actual) != expected:
            raise MarketDataUnavailable("The provider has not supplied the latest completed exchange session; cached data remains stale.")
        series = {"source":{"url":source_url(symbol,now),"sha256":hashlib.sha256(raw).hexdigest(),"retrieved_at":now.isoformat()},"bars":bars}
        quality = {"symbol":symbol,"rows":len(bars),"start_date":bars[0]["date"],"last_date":bars[-1]["date"],"expected_last_session":expected.isoformat(),
                   "excluded_incomplete_dates":excluded,"missing_sessions":[],"fresh":True,"source_actions_independently_verified":False}
        return series, quality
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError):
        raise MarketDataError("The source response does not match the strict daily chart contract.") from None


def collect_bundle(now=None, *, fetch=None, check=None):
    """Two fixed GETs in an authorized online child; parent retains offline policy."""
    fixed_clock = now is not None
    started = _clock(now)
    if fetch is None:
        if os.environ.get("ARIADNE_QQQ_COLLECTOR") != "1" or os.environ.get("ARIADNE_OFFLINE") != "0":
            raise MarketDataUnavailable("Real market collection requires the explicitly authorized online child.")
        from .internet import public_get
        deadline = time.monotonic()+45
        def fetch(url):
            response = public_get(url, deadline=deadline, max_redirects=0, max_bytes=MAX_PAYLOAD)
            if response["url"] != url or response["content_type"].split(";",1)[0].strip().lower() != "application/json":
                raise MarketDataUnavailable("The fixed market source changed its URL or JSON content type.")
            return response["payload"]
    check = check or (lambda:None)
    symbols, payloads, quality, captures = {}, {}, [], []
    for symbol in SYMBOLS:
        check()
        url = source_url(symbol, started)
        raw = fetch(url)
        check()
        captured_at = _clock(started if fixed_clock else None)
        captures.append((symbol,url,raw,captured_at))
    analysis_at = _clock(started if fixed_clock else None)
    for symbol,url,raw,captured_at in captures:
        series, notes = parse_chart(raw, symbol, analysis_at, full_history=True)
        series["source"]["url"] = url
        series["source"]["retrieved_at"] = captured_at.isoformat()
        symbols[symbol] = series
        payloads[symbol] = base64.b64encode(raw).decode("ascii")
        quality.append(notes)
    if symbols["QQQ"]["bars"][0]["date"] != symbols["SPY"]["bars"][0]["date"] or [row["date"] for row in symbols["QQQ"]["bars"]] != [row["date"] for row in symbols["SPY"]["bars"]]:
        raise MarketDataError("The two fixed ETF histories do not share the same complete session calendar.")
    result = {"dataset":{"schema":1,"as_of":analysis_at.isoformat(),"currency":"USD","price_basis":"split_adjusted_ohlc",
                         "dividend_policy":"split_adjusted_cash_on_ex_date","symbols":symbols},
              "raw_payloads":payloads,"quality":{"symbols":quality,"fresh":True,"model_calls":0,"requests":2,
                  "collection_started_at":started.isoformat(),"collection_completed_at":analysis_at.isoformat(),
                  "source":"Yahoo Finance public chart endpoint, unofficial and revocable",
                  "usage":"Private personal research only; no exchange-grade or commercial redistribution rights asserted.",
                  "calendar_scope":"Reviewed US-equity calendar 1999–2035; unforeseen exchange closures require a reviewed update.",
                  "data_limit":"Structural validation and retained hashes do not independently establish price/action correctness or executable fills."}}
    check()
    if len(canonical(result)) > MAX_OUTPUT:
        raise MarketDataError("The complete market collector result exceeds 12 MiB.")
    return result


def main():
    if os.environ.get("ARIADNE_QQQ_COLLECTOR") != "1" or os.environ.get("ARIADNE_OFFLINE") != "0":
        raise SystemExit("The fixed market collector runs only as an authorized online child.")
    from .network_policy import install_network_policy
    install_network_policy()
    supplied = sys.stdin.buffer.read(1025)
    request = _strict_json(supplied or b"{}")
    if len(supplied) > 1024 or type(request) is not dict or set(request) != {"schema"} or type(request["schema"]) is not int or request["schema"] != 1:
        raise SystemExit("The market collector accepts only its fixed schema request.")
    sys.stdout.buffer.write(canonical(collect_bundle()))


if __name__ == "__main__":
    main()
