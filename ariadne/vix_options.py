"""Pure, offline VIX/VIXW contract research; no accounts, downloads or orders.

Public API: ``load_dataset(raw)`` validates UTF-8 JSON bytes/text or a dictionary;
``quality(dataset)`` describes coverage; ``compare(dataset)`` runs a single $500
shared RESEARCH ledger, independent counterfactuals and a doubled-fee stress.
Every report is execution-unqualified. None can fund or advance an owner account.

Schema 1 is deliberately explicit. Top-level fields are schema, kind, name,
synthetic, as_of, provenance, calendar and snapshots. Provenance contains provider,
source_url, raw_sha256, usage_rights_status, availability_basis and size_basis.
Calendar contains timezone, source_url, checked_at, version, coverage_start/end,
sessions and contracts. Sessions have date/open_at/close_at. Contract metadata:
contract_id/root/type/strike/expiry/last_trading_date/last_trading_at, exercise_style,
settlement_type/symbol/method, multiplier and deliverable_shares. Snapshots have
session, observed_at/available_at/retrieved_at, vix_close, nullable vix9d_close,
references and quotes. References are observed VX futures with contract_id,
symbol, expiry, price and the three clocks. Quotes have contract_id, bid/ask,
bid_size/ask_size and the three clocks. Monetary/index values are decimal strings;
sizes are actual nonnegative integers, not inferred volume or open interest.

One completed daily snapshot is allowed per session. All supplied clocks must
be aware. A quote/reference must belong to that session; only the last30minutes
of RTH are eligible. Historical availability assumptions remain disclosed and
never confer forward-fill authority. Metadata/provenance assertions supplied in
JSON are validated structurally, not independently authenticated.
Actual option-calendar coverage must begin on/after the verified 2022-04-25 curb
launch. Earlier synthetic calendars remain explicitly software-only. Historical
clocks are never converted; reviewed early-close sessions have no curb. This
validation correction has its own version/hash and does not revise hypotheses.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .qqq_data import is_session, session_dates, session_close_utc
from .briefing import _tz

UTC = timezone.utc
MAX_BYTES = 10 * 1024 * 1024
MAX_SESSIONS = 8000
MAX_SNAPSHOTS = 5000
MAX_CONTRACTS = 600
MAX_QUOTES = 200
PREREGISTRATION_SHA256 = "01dc93f5799f58a6dc35c318ae5d5eecdfcc735be17ac0f7ec1671e58a943027"
VALIDATION_POLICY = {
    "version": "vix-options-validation-v2",
    "minimum_actual_calendar_date": "2022-04-25",
    "actual_era_gate": "Reject actual option calendars beginning before production curb launch; never convert historical clocks. Synthetic fixtures remain software-only.",
    "regular_close": "Reviewed US exchange equity session_close_utc plus15minutes, using the explicit supplied calendar only when it agrees",
    "normal_last_trading_cutoff": "Previous exchange business day, regular close plus45minutes of curb",
    "early_last_trading_cutoff": "Previous exchange business day, early regular close with no curb",
    "no_future_tbd_extension": True,
    "maximum_quote_and_reference_age_minutes": 30,
    "sources": [
        "https://cdn.cboe.com/resources/release_notes/2022/Schedule-Update-Production-Date-for-Cboe-Options-Implementation-of-New-Curb-Trading-Session.pdf",
        "https://www.cboe.com/document/tech-spec/document/technical-specifications/cboe-options-exchange-245-faq",
        "https://cdn.cboe.com/resources/schedule_update/2024/Cboe-Holiday-Reminder-Modified-Trading-Hours-on-Wednesday-July-3-and-Thursday-July-4-2024.pdf",
    ],
    "disclosure": "Contract-validation correction only; frozen preregistered strategy hypotheses and thresholds are unchanged. Supplied metadata is not authenticated.",
}
STRATEGY_IDS = ("vix_shock_breakout_call_v1", "vix_spike_fade_put_v1", "vix_short_term_stress_call_v1")
_KINDS = dict(zip(STRATEGY_IDS, ("call", "put", "call")))
_ROOT = {"schema", "kind", "name", "synthetic", "as_of", "provenance", "calendar", "snapshots"}
_SOURCE = {"provider", "source_url", "raw_sha256", "usage_rights_status", "availability_basis", "size_basis"}
_CALENDAR = {"timezone", "source_url", "checked_at", "version", "coverage_start", "coverage_end", "sessions", "contracts"}
_CONTRACT = {"contract_id", "root", "type", "strike", "expiry", "last_trading_date", "last_trading_at", "exercise_style", "settlement_type", "settlement_symbol", "settlement_method", "multiplier", "deliverable_shares"}
_ROW = {"session", "observed_at", "available_at", "retrieved_at", "vix_close", "vix9d_close", "references", "quotes"}
_CLOCKS = {"observed_at", "available_at", "retrieved_at"}
_QUOTE = {"contract_id", "bid", "ask", "bid_size", "ask_size"} | _CLOCKS
_REFERENCE = {"contract_id", "symbol", "expiry", "price"} | _CLOCKS


class VixOptionsError(ValueError):
    """Unusable or uncertain local VIX options research evidence."""


def _keys(value, keys, label):
    if type(value) is not dict or set(value) != keys:
        raise VixOptionsError(f"{label} must have exactly its registered schema fields.")


def _text(value, label, maximum=240):
    if type(value) is not str or not 1 <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise VixOptionsError(f"Use bounded ordinary text for {label}.")
    return value


def _clock(value):
    _text(value, "clock", 64)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, OverflowError):
        raise VixOptionsError("Use an aware ISO timestamp.") from None


def _day(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise VixOptionsError("Use an ISO exchange date.")
    try:
        result = date.fromisoformat(value)
        if not 2006 <= result.year <= 2035:
            raise ValueError
        return result
    except ValueError:
        raise VixOptionsError("VIX research calendar covers 2006-2035 only.") from None


def _number(value, *, positive=False):
    if type(value) is not str or not re.fullmatch(r"(?:0|[1-9]\d{0,8})(?:\.\d{1,6})?", value):
        raise VixOptionsError("Use bounded unsigned decimal strings, not floats or exponent notation.")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise VixOptionsError("Invalid decimal.") from None
    if result > 1000000 or positive and result <= 0:
        raise VixOptionsError("A price is outside positive finite bounds.")
    return result


def _money(value):
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _debit(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _credit(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)


def _url(value, *, official=False):
    _text(value, "source URL", 2048)
    try:
        parts = urlsplit(value)
        if official:
            valid = parts.scheme == "https" and (parts.hostname == "cboe.com" or (parts.hostname or "").endswith(".cboe.com"))
        else:
            valid = parts.scheme == "https" and bool(parts.hostname) or parts.scheme == "local-file" and bool(parts.path)
        if not valid or parts.username or parts.password or parts.fragment:
            raise ValueError
    except ValueError:
        raise VixOptionsError("Use a noncredentialed source URL; calendar sources must be official Cboe HTTPS.") from None


def _clocks(value, as_of):
    observed, available, retrieved = (_clock(value[key]) for key in ("observed_at", "available_at", "retrieved_at"))
    if not observed <= available <= retrieved <= as_of:
        raise VixOptionsError("Evidence must preserve observed <= available <= retrieved <= as_of.")
    return observed, available, retrieved


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise VixOptionsError("Use bounded ordinary JSON.") from None


RULE_SHA256 = hashlib.sha256(_canonical({"preregistration_sha256": PREREGISTRATION_SHA256, "validation_policy": VALIDATION_POLICY})).hexdigest()


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise VixOptionsError("Duplicate JSON keys are rejected.")
            result[key] = value
        return result
    if type(raw) is dict:
        raw = _canonical(raw)
    elif type(raw) is str:
        try:
            raw = raw.encode("utf-8")
        except UnicodeError:
            raise VixOptionsError("Use UTF-8 JSON.") from None
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES:
        raise VixOptionsError("Research imports are bounded to 10 MiB of JSON.")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(VixOptionsError("Nonfinite JSON is rejected.")))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise VixOptionsError("Invalid bounded research JSON.") from None


def registration():
    """Read the exact frozen research design, without any state mutation."""
    raw = (Path(__file__).resolve().parent / "resources" / "vix" / "preregistration.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != PREREGISTRATION_SHA256:
        raise VixOptionsError("The frozen VIX research registration changed.")
    return json.loads(raw)


def load_dataset(raw):
    """Validate strict local research JSON and return a detached dictionary.

    This performs no file/network access. Calendar assertions remain user-supplied
    evidence; passing schema validation does not authenticate their contents.
    """
    data = _decode(raw)
    _keys(data, _ROOT, "Dataset")
    if type(data["schema"]) is not int or data["schema"] != 1 or data["kind"] != "vix_index_options_history" or type(data["synthetic"]) is not bool:
        raise VixOptionsError("Choose the explicit VIX index-option research schema and synthetic flag.")
    _text(data["name"], "dataset name")
    as_of = _clock(data["as_of"])
    source = data["provenance"]
    _keys(source, _SOURCE, "Provenance")
    _text(source["provider"], "provider")
    _url(source["source_url"])
    if type(source["raw_sha256"]) is not str or not re.fullmatch("[0-9a-f]{64}", source["raw_sha256"]):
        raise VixOptionsError("Supply the raw source SHA-256.")
    if (source["usage_rights_status"] not in ("user_supplied_permitted", "public_research_permitted", "synthetic_fixture") or
            source["availability_basis"] not in ("provider_recorded", "historical_assumption") or source["size_basis"] != "observed"):
        raise VixOptionsError("Explicit permitted usage, availability provenance and observed sizes are required.")
    if (source["usage_rights_status"] == "synthetic_fixture") != data["synthetic"]:
        raise VixOptionsError("Synthetic provenance and dataset label must agree.")
    cal = data["calendar"]
    _keys(cal, _CALENDAR, "Calendar")
    if cal["timezone"] != "America/Chicago":
        raise VixOptionsError("Use the explicit America/Chicago exchange zone.")
    _url(cal["source_url"], official=True)
    _text(cal["version"], "calendar version", 120)
    if _clock(cal["checked_at"]) > as_of:
        raise VixOptionsError("A calendar check cannot be in the future.")
    first, last = _day(cal["coverage_start"]), _day(cal["coverage_end"])
    if not data["synthetic"] and first < date(2022, 4, 25):
        raise VixOptionsError("Actual option calendars before the verified 2022-04-25 curb-launch era are unsupported; do not convert historical clocks.")
    if first > last or (last-first).days > 12000 or type(cal["sessions"]) is not list or not 1 <= len(cal["sessions"]) <= MAX_SESSIONS:
        raise VixOptionsError("Supply a bounded explicit complete session calendar.")
    sessions = {}
    zone = _tz("America/Chicago")
    for item in cal["sessions"]:
        _keys(item, {"date", "open_at", "close_at"}, "Calendar session")
        day = _day(item["date"])
        opened, closed = _clock(item["open_at"]), _clock(item["close_at"])
        local_open, local_close = opened.astimezone(zone), closed.astimezone(zone)
        if (item["date"] in sessions or not is_session(day) or not first <= day <= last or
                local_open.date() != day or local_close.date() != day or local_open.hour != 8 or local_open.minute != 30 or
                local_open.second or local_open.microsecond or not opened < closed or
                closed != session_close_utc(day)+timedelta(minutes=15)):
            raise VixOptionsError("Invalid, duplicate or unsupported official regular-session time.")
        sessions[item["date"]] = item
    expected = [day.isoformat() for day in session_dates(first, last)]
    if list(sessions) != expected:
        raise VixOptionsError("Calendar sessions must be complete and ordered, not inferred from observed data.")
    if type(cal["contracts"]) is not list or len(cal["contracts"]) > MAX_CONTRACTS:
        raise VixOptionsError("Too many registered contracts.")
    contracts = {}
    for contract in cal["contracts"]:
        _keys(contract, _CONTRACT, "Contract")
        expiry, ltd = _day(contract["expiry"]), _day(contract["last_trading_date"])
        strike = _number(contract["strike"], positive=True)
        if contract["root"] not in ("VIX", "VIXW") or contract["type"] not in ("call", "put") or strike * 1000 != (strike * 1000).to_integral_value():
            raise VixOptionsError("Only explicit VIX/VIXW calls or puts with standard strikes are supported.")
        identifier = contract["root"] + expiry.strftime("%y%m%d") + ("C" if contract["type"] == "call" else "P") + f"{int(strike*1000):08d}"
        if contract["contract_id"] != identifier or identifier in contracts:
            raise VixOptionsError("Contract root, expiry, right and strike must match its unique canonical ID.")
        if (contract["exercise_style"] != "European" or contract["settlement_type"] != "cash" or contract["settlement_symbol"] != "VRO" or
                contract["settlement_method"] != "AM_SOQ" or type(contract["multiplier"]) is not int or contract["multiplier"] != 100 or
                type(contract["deliverable_shares"]) is not int or contract["deliverable_shares"] != 0):
            raise VixOptionsError("VIX is European AM VRO cash settlement, $100 multiplier and zero share deliverable.")
        if contract["expiry"] not in sessions or contract["last_trading_date"] not in sessions or expiry.weekday() not in (1, 2):
            raise VixOptionsError("Expiry and last trading day require explicit covered official calendar dates.")
        expiry_index = expected.index(contract["expiry"])
        if not expiry_index or expected[expiry_index-1] != contract["last_trading_date"]:
            raise VixOptionsError("The current effective VIX rule stops on the preceding exchange business day.")
        last_clock = _clock(contract["last_trading_at"])
        local_last = last_clock.astimezone(zone)
        session_close = _clock(sessions[contract["last_trading_date"]]["close_at"])
        early_close = session_close.astimezone(zone).hour < 15
        expected_cutoff = session_close if early_close else session_close+timedelta(minutes=45)
        if (local_last.date() != ltd or local_last.second or local_last.microsecond or
                last_clock != expected_cutoff):
            raise VixOptionsError("Explicit last trading cutoff must match end-of-curb on normal sessions or the early RTH close with no curb; no future TBD extension.")
        contracts[identifier] = contract
    if type(data["snapshots"]) is not list or len(data["snapshots"]) > MAX_SNAPSHOTS:
        raise VixOptionsError("At most 5000 daily research observations are supported.")
    previous_day = previous_available = None
    for row in data["snapshots"]:
        _keys(row, _ROW, "Snapshot")
        day = _day(row["session"])
        if row["session"] not in sessions or previous_day is not None and day <= previous_day:
            raise VixOptionsError("Snapshots must have unique increasing explicit exchange sessions.")
        opened, closed = (_clock(sessions[row["session"]][key]) for key in ("open_at", "close_at"))
        observed, available, retrieved = _clocks(row, as_of)
        if observed != closed or previous_available is not None and available <= previous_available:
            raise VixOptionsError("Use completed regular-close index observations and increasing availability clocks.")
        _number(row["vix_close"], positive=True)
        if row["vix9d_close"] is not None:
            _number(row["vix9d_close"], positive=True)
        previous_day, previous_available = day, available
        if type(row["references"]) is not list or len(row["references"]) > MAX_CONTRACTS or type(row["quotes"]) is not list or len(row["quotes"]) > MAX_QUOTES:
            raise VixOptionsError("Bounded reference and quote lists are required.")
        reference_expiries = set()
        for reference in row["references"]:
            _keys(reference, _REFERENCE, "VX reference")
            _text(reference["contract_id"], "VX contract ID", 80)
            expiry = _day(reference["expiry"])
            if reference["contract_id"] != "VX-"+reference["expiry"] or reference["symbol"] != "VX":
                raise VixOptionsError("Use an actual individual VX future reference, not a continuous roll, spot index or ETF.")
            if reference["expiry"] in reference_expiries or reference["expiry"] not in sessions or expiry < day:
                raise VixOptionsError("Use one unexpired reference per exact expiry date.")
            reference_expiries.add(reference["expiry"])
            _number(reference["price"], positive=True)
            ro, ra, rr = _clocks(reference, as_of)
            if ro.astimezone(zone).date() != day or not opened <= ro <= observed or ra > available or rr > retrieved:
                raise VixOptionsError("VX references must preserve the snapshot's dated availability provenance.")
        quote_ids = set()
        for quote in row["quotes"]:
            _keys(quote, _QUOTE, "Quote")
            cid = quote["contract_id"]
            if type(cid) is not str or cid not in contracts or cid in quote_ids:
                raise VixOptionsError("Quotes must identify unique registered VIX/VIXW contracts.")
            quote_ids.add(cid)
            _number(quote["bid"]); _number(quote["ask"])
            for key in ("bid_size", "ask_size"):
                if type(quote[key]) is not int or not 0 <= quote[key] <= 1000000:
                    raise VixOptionsError("Quote sizes must be observed bounded integers, not volume/open interest proxies.")
            qo, qa, qr = _clocks(quote, as_of)
            if qo.astimezone(zone).date() != day or not opened <= qo <= observed or qa > available or qr > retrieved or qo > _clock(contracts[cid]["last_trading_at"]):
                raise VixOptionsError("Quotes cannot postdate their contract cutoff or snapshot provenance.")
    return data


def _quote_reason(quote, row, side):
    if quote is None:
        return "missing_contract_quote"
    bid, ask = _number(quote["bid"]), _number(quote["ask"])
    if bid <= 0 or ask <= 0 or bid > ask:
        return "nonpositive_or_crossed_quote"
    if ask-bid > (bid+ask)/2*Decimal("0.25"):
        return "spread_above_25_percent"
    if quote[side+"_size"] < 1:
        return "no_observed_executable_size"
    if _clock(row["observed_at"])-_clock(quote["observed_at"]) > timedelta(minutes=30):
        return "stale_quote"
    return None


def _reference(row, expiry):
    for item in row["references"]:
        if item["expiry"] == expiry and _clock(row["observed_at"])-_clock(item["observed_at"]) <= timedelta(minutes=30):
            return item
    return None


def _quality(data):
    rows = data["snapshots"]
    observed = {row["session"] for row in rows}
    covered = [] if not rows else [item["date"] for item in data["calendar"]["sessions"] if rows[0]["session"] <= item["date"] <= rows[-1]["session"]]
    counts = Counter()
    contracts = {item["contract_id"]: item for item in data["calendar"]["contracts"]}
    for row in rows:
        for quote in row["quotes"]:
            counts["option_quotes"] += 1
            reason = _quote_reason(quote, row, "ask")
            if reason:
                counts[reason] += 1
            if _reference(row, contracts[quote["contract_id"]]["expiry"]) is None:
                counts["missing_or_stale_exact_expiry_reference"] += 1
    assumptions = ["Supplied provenance and official-calendar assertions are structurally validated, not authenticated.",
                   "Quotes and contract coverage may have survivorship or selection gaps; this is not a complete exchange chain certification.",
                   "All fills and $500 funding are counterfactual research, with no owner paper-account authority.",
                   "$0.65 per-side fees are a model assumption; $1.30 sensitivity is reported."]
    assumptions.append("Actual option calendars before 2022-04-25 are rejected; current normal curb and no-curb early-close validation are versioned separately from frozen hypotheses.")
    if data["synthetic"]:
        assumptions.insert(0, "SYNTHETIC FIXTURE: fabricated prices test software only and establish no market returns.")
    if data["provenance"]["availability_basis"] == "historical_assumption":
        assumptions.append("Historical availability is assumed, not a contemporaneous provider publication record.")
    return {"schema": 1, "dataset_name": data["name"], "dataset_sha256": hashlib.sha256(_canonical(data)).hexdigest(),
            "synthetic": data["synthetic"], "execution_qualified": False, "forward_paper_authority": False,
            "observations": len(rows), "contracts": len(contracts), "counts": dict(sorted(counts.items())),
            "first_session": rows[0]["session"] if rows else None, "last_session": rows[-1]["session"] if rows else None,
            "expected_sessions_in_observed_span": len(covered), "missing_session_count": sum(day not in observed for day in covered),
            "missing_sessions_first100": [day for day in covered if day not in observed][:100],
            "vix9d_observations": sum(row["vix9d_close"] is not None for row in rows), "assumptions": assumptions,
            "source": deepcopy(data["provenance"]), "as_of": data["as_of"]}


def quality(dataset):
    """Return bounded coverage, provenance and execution-gate diagnostics."""
    with localcontext() as context:
        context.prec = 40
        return _quality(load_dataset(dataset))


def _signals(history):
    result = []
    current = _number(history[-1]["vix_close"])
    if len(history) >= 21:
        previous = [_number(row["vix_close"]) for row in history[-21:-1]]
        if current > max(previous) and current >= previous[-1]*Decimal("1.10"):
            result.append(STRATEGY_IDS[0])
    if len(history) >= 6:
        previous = [_number(row["vix_close"]) for row in history[-6:-1]]
        if previous[-1] >= 25 and current <= previous[-1]*Decimal("0.90") and current < sum(previous)/5:
            result.append(STRATEGY_IDS[1])
        recent = history[-6:]
        if all(row["vix9d_close"] is not None for row in recent):
            ratios = [_number(row["vix9d_close"])/_number(row["vix_close"]) for row in recent]
            if ratios[-1] > Decimal("1.05") and all(value <= 1 for value in ratios[:-1]):
                result.append(STRATEGY_IDS[2])
    return result


def _forced_session(calendar, contract, entry_day):
    days, indexes = calendar["days"], calendar["index"]
    index = indexes[entry_day]
    last_index = indexes[contract["last_trading_date"]]
    month = calendar["months"][entry_day[:7]]
    # Full month coverage is necessary to make the month-end safety deadline.
    end = _day(entry_day).replace(day=28)+timedelta(days=4)
    month_end = end.replace(day=1)-timedelta(days=1)
    last_month_session = session_dates(month_end.replace(day=1), month_end)[-1].isoformat()
    if days[-1] < last_month_session or len(month) < 2 or not last_index:
        return None
    candidates = [days[last_index-1], month[-2]]
    if index+10 < len(days):
        candidates.append(days[index+10])
    return min(candidates)


def _entry_reason(quote, row, contract, calendar, fee):
    reason = _quote_reason(quote, row, "ask")
    if reason:
        return reason
    day, expiry = _day(row["session"]), _day(contract["expiry"])
    if not 7 <= (expiry-day).days <= 21 or expiry.strftime("%Y-%m") != day.strftime("%Y-%m"):
        return "expiry_outside_registered_window"
    reference = _reference(row, contract["expiry"])
    if reference is None:
        return "missing_or_stale_exact_expiry_reference"
    forward, strike = _number(reference["price"]), _number(contract["strike"])
    otm = strike/forward-1 if contract["type"] == "call" else 1-strike/forward
    if not Decimal("0.05") <= otm <= Decimal("0.25"):
        return "strike_outside_forward_otm_band"
    ask = _number(quote["ask"])
    if ask < Decimal("0.10") or _debit(ask*100)+2*fee > 125:
        return "premium_outside_per_entry_cap"
    forced = _forced_session(calendar, contract, row["session"])
    if forced is None or forced <= row["session"]:
        return "missing_calendar_or_disposal_window"
    return None


def _run(data, *, fee, enabled):
    rows = data["snapshots"]
    days = [item["date"] for item in data["calendar"]["sessions"]]
    months = {}
    for day in days:
        months.setdefault(day[:7], []).append(day)
    calendar = {"days": days, "index": {day: index for index, day in enumerate(days)}, "months": months}
    contracts = {item["contract_id"]: item for item in data["calendar"]["contracts"]}
    cash, contributions, committed, fees = Decimal(500), Decimal(500), Decimal(0), Decimal(0)
    monthly_start = cash
    month = rows[0]["session"][:7] if rows else None
    position = pending = hold = None
    history, daily, trades, monthly, signals = [], [], [], [], Counter()
    rejections = Counter()
    cursor = 0
    peak_pnl = max_drawdown = Decimal(0)
    loss_streak = longest_loss_streak = 0
    stats = {key: {"entries": 0, "closed": 0, "realized_pnl": Decimal(0)} for key in enabled}
    partition = rows[int(len(rows)*Decimal("0.7"))]["session"] if rows else None

    def reject(reason):
        rejections[reason] += 1

    def close_position(row, quote, reason):
        nonlocal cash, fees, position, pending, loss_streak, longest_loss_streak
        proceeds = _credit(_number(quote["bid"])*100)
        cash += proceeds-fee
        fees += fee
        pnl = proceeds-_number(position["premium"])-2*fee
        stats[position["strategy_id"]]["closed"] += 1
        stats[position["strategy_id"]]["realized_pnl"] += pnl
        trade = {**position, "exit_session": row["session"], "exit_at": quote["observed_at"], "exit_available_at": quote["available_at"],
                 "exit_bid": quote["bid"], "exit_fee": _money(fee), "net_pnl": _money(pnl), "exit_reason": reason,
                 "payout_multiple": _money(_number(quote["bid"])/_number(position["entry_ask"])),
                 "split": "descriptive_final30_percent" if row["session"] >= partition else "descriptive_first70_percent"}
        trades.append(trade)
        loss_streak = loss_streak+1 if pnl < 0 else 0
        longest_loss_streak = max(longest_loss_streak, loss_streak)
        position = pending = None

    for row in rows:
        day = row["session"]
        if history and calendar["index"][day] != calendar["index"][history[-1]["session"]]+1:
            history = []
            reject("missing_signal_session_resets_warmup")
            if pending and pending["action"] == "entry":
                pending = None
                reject("missing_next_session_cancels_entry")
        history.append(row)
        new_month = day[:7]
        if new_month != month:
            if position is not None:
                hold = hold or "unresolved_position_at_month_boundary"
            if hold is None:
                monthly.append({"month": month, "committed": _money(committed), "remaining": _money(500-committed), "verified_flat": True})
                cash += 500
                contributions += 500
                month, committed, monthly_start, pending = new_month, Decimal(0), cash, None
        quotes = {item["contract_id"]: item for item in row["quotes"]}
        if position is not None and hold is None:
            quote = quotes.get(position["contract_id"])
            reason = _quote_reason(quote, row, "bid")
            if day > position["forced_exit_session"]:
                hold = "missing_mandatory_disposal_quote"
            elif day == position["forced_exit_session"]:
                if reason:
                    hold = "missing_mandatory_disposal_quote"
                    reject(reason)
                else:
                    close_position(row, quote, "precommitted_disposal")
            elif reason:
                reject(reason)
            else:
                position["marked_bid"] = quote["bid"]
                position["marked_session"] = day
                if pending and pending["action"] == "exit" and _clock(quote["observed_at"]) > _clock(pending["decided_at"])+timedelta(seconds=60):
                    close_position(row, quote, pending["reason"])
                elif _number(quote["bid"]) >= _number(position["entry_ask"])*5 or _number(quote["bid"]) <= _number(position["entry_ask"])*Decimal("0.5"):
                    if not pending:
                        pending = {"action": "exit", "reason": "observed_5x_target_or_half_premium_stop", "decided_at": row["available_at"]}
        elif position is None and pending and hold is None:
            contract = contracts[pending["contract_id"]]
            quote = quotes.get(pending["contract_id"])
            reason = _entry_reason(quote, row, contract, calendar, fee)
            if reason is None and _clock(quote["observed_at"]) <= _clock(pending["decided_at"])+timedelta(seconds=60):
                reason = "quote_not_later_than_durable_decision"
            debit = None if quote is None else _debit(_number(quote["ask"])*100)+2*fee
            if reason is None and (committed+debit > 500 or cash < debit):
                reason = "shared_monthly_cap_or_cash_exhausted"
            if reason:
                reject(reason)
                pending = None
            else:
                premium = _debit(_number(quote["ask"])*100)
                position = {"strategy_id": pending["strategy_id"], "contract_id": pending["contract_id"], "entry_session": day,
                            "entry_at": quote["observed_at"], "entry_available_at": quote["available_at"], "decision_at": pending["decided_at"],
                            "entry_ask": quote["ask"], "premium": _money(premium), "entry_fee": _money(fee), "reserved_exit_fee": _money(fee),
                            "budget_debit": _money(debit), "forced_exit_session": _forced_session(calendar, contract, day),
                            "marked_bid": quote["bid"], "marked_session": day}
                cash -= premium+fee
                fees += fee
                committed += debit
                stats[pending["strategy_id"]]["entries"] += 1
                cursor = (enabled.index(pending["strategy_id"])+1) % len(enabled)
                pending = None
        active_signals = _signals(history)
        signals.update(active_signals)
        if position is None and pending is None and hold is None:
            order = enabled[cursor:]+enabled[:cursor]
            for strategy in order:
                if strategy not in active_signals:
                    continue
                candidates = []
                for quote in row["quotes"]:
                    contract = contracts[quote["contract_id"]]
                    if contract["type"] != _KINDS[strategy]:
                        continue
                    reason = _entry_reason(quote, row, contract, calendar, fee)
                    if reason:
                        reject(reason)
                        continue
                    reference = _reference(row, contract["expiry"])
                    forward, strike = _number(reference["price"]), _number(contract["strike"])
                    otm = strike/forward-1 if contract["type"] == "call" else 1-strike/forward
                    candidates.append((abs((_day(contract["expiry"])-_day(day)).days-14), abs(otm-Decimal("0.10")), contract["contract_id"]))
                if candidates:
                    selected = min(candidates)[2]
                    quote = quotes[selected]
                    debit = _debit(_number(quote["ask"])*100)+2*fee
                    if committed+debit > 500 or cash < debit:
                        reject("shared_monthly_cap_or_cash_exhausted")
                        continue
                    pending = {"action": "entry", "strategy_id": strategy, "contract_id": selected, "decided_at": row["available_at"], "signal_session": day}
                    break
                reject("signal_without_eligible_contract")
        mark = Decimal(0) if position is None else _credit(_number(position["marked_bid"])*100)-fee
        equity = cash+mark
        pnl = equity-contributions
        peak_pnl = max(peak_pnl, pnl)
        max_drawdown = max(max_drawdown, peak_pnl-pnl)
        daily.append({"session": day, "observed_at": row["observed_at"], "available_at": row["available_at"], "month": month,
                      "cash": _money(cash), "equity": _money(equity), "contributions": _money(contributions), "trading_pnl": _money(pnl),
                      "monthly_pnl": _money(equity-monthly_start), "monthly_committed": _money(committed), "monthly_remaining": _money(500-committed),
                      "fees": _money(fees), "open_position": position is not None, "mark_stale": position is not None and position["marked_session"] != day,
                      "hold": hold, "synthetic": data["synthetic"], "research_only": True})
    if month is not None:
        monthly.append({"month": month, "committed": _money(committed), "remaining": _money(500-committed), "verified_flat": position is None and hold is None})
    pnls = sorted(Decimal(trade["net_pnl"]) for trade in trades)
    median = None if not pnls else pnls[len(pnls)//2] if len(pnls)%2 else (pnls[len(pnls)//2-1]+pnls[len(pnls)//2])/2
    realized = sum(pnls, Decimal(0))
    last_equity = cash if not daily else Decimal(daily[-1]["equity"])
    for value in stats.values():
        value["realized_pnl"] = _money(value["realized_pnl"])
    return {"label": "SYNTHETIC SOFTWARE TEST" if data["synthetic"] else "HYPOTHETICAL LOCAL QUOTE RESEARCH", "research_only": True,
            "execution_qualified": False, "forward_paper_authority": False, "separate_from_owner_account": True,
            "fee_per_side": _money(fee), "enabled_strategies": list(enabled), "status": "held" if hold else "incomplete_open_position" if position else "completed",
            "cash": _money(cash), "equity": _money(last_equity), "contributions": _money(contributions), "realized_pnl": _money(realized),
            "gross_realized_pnl": _money(realized+sum(Decimal(trade["entry_fee"])+Decimal(trade["exit_fee"]) for trade in trades)),
            "unrealized_pnl": _money(last_equity-contributions-realized), "fees": _money(fees), "max_drawdown_excluding_deposits": _money(max_drawdown),
            "closed_trades": len(trades), "win_count": sum(pnl > 0 for pnl in pnls), "loss_count": sum(pnl < 0 for pnl in pnls),
            "win_rate": None if not pnls else _money(Decimal(sum(pnl > 0 for pnl in pnls))/len(pnls)),
            "median_trade": None if median is None else _money(median), "worst_trade": None if not pnls else _money(pnls[0]),
            "longest_loss_streak": longest_loss_streak, "payout_milestones": {str(m)+"x": sum(Decimal(trade["payout_multiple"]) >= m for trade in trades) for m in (2, 5, 10)},
            "strategy_attribution": stats, "signals": dict(signals), "rejections": dict(sorted(rejections.items())), "hold": hold,
            "position": position, "pending": pending, "monthly": monthly, "daily": daily, "trades": trades,
            "descriptive_split_session": partition, "cash_baseline_trading_pnl": "0", "winning_strategy_claim": False,
            "cap_qualified_model": hold is None, "cap_definition": "500 shared monthly premium plus both fees; no profit recycling; verified flat rollover; not a peak drawdown guarantee"}


def compare(dataset):
    """Backtest frozen hypotheses offline; never activate or fund paper trading.

    The shared ledger is the only combined result. Counterfactual ledgers each
    have their own hypothetical $500 budget and must never be added together.
    Synthetic results demonstrate code paths, not investment performance.
    """
    with localcontext() as context:
        context.prec = 40
        data = load_dataset(dataset)
        registration()
        diagnostics = _quality(data)
        coverage = {key: diagnostics[key] for key in ("dataset_sha256", "synthetic", "execution_qualified", "forward_paper_authority", "observations", "contracts", "counts", "first_session", "last_session", "expected_sessions_in_observed_span", "missing_session_count", "vix9d_observations")}
        coverage.update(missing_sessions_first10=diagnostics["missing_sessions_first100"][:10],
                        availability_basis=data["provenance"]["availability_basis"], size_basis="observed",
                        provenance_authenticated=False, calendar_authenticated=False,
                        validation_policy_version=VALIDATION_POLICY["version"], minimum_actual_calendar_date="2022-04-25")
        shared = _run(data, fee=Decimal("0.65"), enabled=list(STRATEGY_IDS))
        stress = _run(data, fee=Decimal("1.30"), enabled=list(STRATEGY_IDS))
        summary_keys = ("status", "cash", "equity", "contributions", "realized_pnl", "unrealized_pnl", "fees", "closed_trades",
                        "max_drawdown_excluding_deposits", "payout_milestones", "hold", "cap_qualified_model")
        summary = {key: shared[key] for key in summary_keys}
        summary.update(label=shared["label"], research_only=True, execution_qualified=False, forward_paper_authority=False,
                       monthly_shared_cap="500", current_budget_used=shared["monthly"][-1]["committed"] if shared["monthly"] else "0",
                       doubled_fee_stress={key: stress[key] for key in ("status", "realized_pnl", "closed_trades", "fees", "hold")})
        return {"schema": 1, "kind": "vix_options_research_comparison", "registration_sha256": PREREGISTRATION_SHA256,
                "rule_sha256": RULE_SHA256, "validation_policy": deepcopy(VALIDATION_POLICY), "summary": summary, "coverage": coverage,
                "research_gate": "synthetic_fixture_not_market_evidence" if data["synthetic"] else "retrospective_vix_quotes_unverified",
                "qualification": {"execution_qualified": False, "forward_paper_authority": False,
                                  "reason": "Counterfactual research only; no integrated owner-ledger migration or qualified free VIX option feed. Synthetic fixtures establish no returns."},
                "execution_qualified": False, "forward_paper_authority": False, "synthetic": data["synthetic"], "quality": diagnostics,
                "shared_research": shared,
                "doubled_fee_stress": stress,
                "counterfactuals": {key: _run(data, fee=Decimal("0.65"), enabled=[key]) for key in STRATEGY_IDS},
                "counterfactual_warning": "Separate hypothetical budgets; never sum results, fund an active account, rank a winner or infer executable returns."}
