"""Offline OHLCV audit and fixed intraday research; never a brokerage interface.

Inputs are inert CSV/JSON snapshots. Calculations use Decimal and fixed strategy
templates; no downloaded model, strategy, pickle, script or SQL is evaluated.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
from zoneinfo import ZoneInfo

from .atomic import sync_directory
from .config import _allowed_path

NAMES = {"market_research"}
VERSION = "intraday-research-1"
MAX_BYTES = 2_000_000
MAX_BARS = 5_000
MAX_REPORT_BYTES = 4_000_000
MAX_FOLDS = 20
_NUM = re.compile(r"(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,8})?\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,31}\Z")
_CENT = Decimal("0.01")
_ZERO = Decimal(0)
_BPS = Decimal(10_000)
_NEW_YORK_SHA256 = "d7f2206b3a45989fc9ad63d558922532fa7352280d5f87176bf1db79cb1d1fa9"
_TZFILES = {"America/New_York": ("market/America_New_York.tzif", _NEW_YORK_SHA256, "2026e"),
    "America/Chicago": ("briefing/America_Chicago.tzif", "c27b739ff46a7df0594e120d725b439217e11e44ea9a50cdc49130383b5482e7", "2026f")}


def market_timezone(name):
    """Use deterministic bundled clocks on Windows, without a system TZ DB."""
    if name == "UTC":
        return timezone.utc
    if name not in _TZFILES:
        raise ValueError("Supported offline market timezones: America/New_York, America/Chicago and UTC.")
    relative, expected, _ = _TZFILES[name]
    path = _allowed_path(Path(__file__).parent / "resources" / relative)
    from .tools import ToolRegistry
    guard = object.__new__(ToolRegistry)
    with guard._directory(path.parent) as fd:
        with os.fdopen(guard._open_read(path, fd), "rb") as source:
            raw = source.read(20_001)
    if len(raw) > 20_000 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Bundled market timezone identity differs from its pinned IANA record.")
    return ZoneInfo.from_file(io.BytesIO(raw), key=name)


def _fields(value, allowed, required=()):
    if type(value) is not dict or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError("Missing or unsupported market-research fields.")


def _number(value, label, maximum=Decimal("1000000000"), *, positive=False):
    if type(value) is not str or not _NUM.fullmatch(value):
        raise ValueError(label + " must be a bounded nonnegative decimal string.")
    result = Decimal(value)
    if result > maximum or (positive and result <= 0):
        raise ValueError(label + " is outside the supported range.")
    return result


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(label + " must be a bounded integer.")
    return value


def _plain(value, label, maximum=200):
    if type(value) is not str or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(label + " must be bounded text without control characters.")
    return value.strip()


def _json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def _load_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate metadata keys are not supported.")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite metadata is not supported.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Metadata requires bounded UTF-8 JSON.") from None


def _format(value):
    return format(value, "f")


def _money(value):
    return _format(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def validate_metadata(value):
    required = {"symbol", "currency", "source", "adjustment", "timeframe_minutes", "timezone", "session_open", "session_close", "weekdays"}
    _fields(value, required | {"expected_sessions", "study"}, required)
    result = dict(value)
    if type(value["symbol"]) is not str or not _SYMBOL.fullmatch(value["symbol"]):
        raise ValueError("Invalid single-instrument symbol.")
    if type(value["currency"]) is not str or not re.fullmatch(r"[A-Z]{3}", value["currency"]):
        raise ValueError("Currency requires a three-letter uppercase code.")
    result["source"] = _plain(value["source"], "source")
    if type(value["adjustment"]) is not str or value["adjustment"] not in {"raw", "split_adjusted", "total_return_adjusted"}:
        raise ValueError("Explicit adjustment provenance is required.")
    _integer(value["timeframe_minutes"], "timeframe_minutes", 1, 1440)
    result["timezone"] = _plain(value["timezone"], "timezone", 80)
    try:
        market_timezone(result["timezone"])
    except (KeyError, ValueError):
        raise ValueError("A locally available IANA timezone is required.") from None
    clocks = []
    for key in ("session_open", "session_close"):
        if type(value[key]) is not str or not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", value[key]):
            raise ValueError("Session clocks require HH:MM.")
        try:
            clock = time.fromisoformat(value[key])
        except ValueError:
            raise ValueError("Invalid session clock.") from None
        clocks.append(clock.hour * 60 + clock.minute)
    if clocks[0] >= clocks[1]:
        raise ValueError("Only non-overnight sessions are supported.")
    if value["timeframe_minutes"] != 1440 and (clocks[1] - clocks[0]) % value["timeframe_minutes"]:
        raise ValueError("The session length must divide into whole bars.")
    days = value["weekdays"]
    if type(days) is not list or not days or len(days) > 7 or any(type(x) is not int or not 0 <= x <= 6 for x in days) or len(set(days)) != len(days):
        raise ValueError("weekdays requires unique integers Monday=0 through Sunday=6.")
    result["weekdays"] = sorted(days)
    if "expected_sessions" in value:
        sessions = value["expected_sessions"]
        if type(sessions) is not list or not sessions or len(sessions) > 500 or any(type(x) is not str for x in sessions):
            raise ValueError("expected_sessions requires a bounded list of dates.")
        try:
            if any(date.fromisoformat(x).isoformat() != x for x in sessions) or len(set(sessions)) != len(sessions):
                raise ValueError()
        except ValueError:
            raise ValueError("expected_sessions requires unique ISO dates.") from None
        result["expected_sessions"] = sorted(sessions)
    if "study" in value:
        result["study"] = validate_study(value["study"])
    return result


def validate_study(value):
    required = {"initial_cash", "shares", "commission_per_order", "fee_bps", "spread_bps", "slippage_bps", "train_sessions", "test_sessions", "holdout_sessions", "parameter_grid"}
    _fields(value, required, required)
    result = dict(value)
    _number(value["initial_cash"], "initial_cash", Decimal("1000000000000"), positive=True)
    _integer(value["shares"], "shares", 1, 10000)
    commission = _number(value["commission_per_order"], "commission_per_order", Decimal("1000"))
    if commission != commission.quantize(_CENT, rounding=ROUND_HALF_UP):
        raise ValueError("Fixed commission must be an exact amount in two-decimal currency units.")
    for key in ("fee_bps", "spread_bps", "slippage_bps"):
        _number(value[key], key, Decimal("1000"))
    for key in ("train_sessions", "test_sessions", "holdout_sessions"):
        _integer(value[key], key, 1, 100)
    grid = value["parameter_grid"]
    if type(grid) is not list or not 1 <= len(grid) <= 6:
        raise ValueError("parameter_grid requires one to six fixed SMA pairs.")
    seen = set()
    for pair in grid:
        _fields(pair, {"fast", "slow"}, {"fast", "slow"})
        fast = _integer(pair["fast"], "fast", 1, 99)
        slow = _integer(pair["slow"], "slow", 2, 100)
        if fast >= slow or (fast, slow) in seen:
            raise ValueError("SMA pairs must be distinct and fast must be less than slow.")
        seen.add((fast, slow))
    result["parameter_grid"] = [{"fast": fast, "slow": slow} for fast, slow in sorted(seen)]
    return result


def audit_csv(raw, metadata, cancelled=None):
    """Validate chronology and declared observed sessions without silently sorting."""
    if len(raw) > MAX_BYTES:
        raise ValueError("Market input exceeds the two-megabyte limit.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("Market CSV must be UTF-8.") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    columns = reader.fieldnames
    required = {"open", "high", "low", "close", "volume"}
    if (not columns or len(columns) > 20 or len(set(columns)) != len(columns)
            or not required <= set(columns) or not ({"timestamp", "date"} & set(columns))):
        raise ValueError("CSV requires unique timestamp (or date), open, high, low, close and volume columns.")
    if "timestamp" in columns and "date" in columns:
        raise ValueError("Choose timestamp or date, not both.")
    if set(columns) - (required | {"timestamp", "date", "symbol", "adj_close", "vwap", "split_ratio", "dividend"}):
        raise ValueError("Unsupported CSV columns; export a single-instrument OHLCV view.")
    key = "timestamp" if "timestamp" in columns else "date"
    zone = market_timezone(metadata["timezone"])
    interval = metadata["timeframe_minutes"]
    open_minutes = int(metadata["session_open"][:2])*60 + int(metadata["session_open"][3:])
    close_minutes = int(metadata["session_close"][:2])*60 + int(metadata["session_close"][3:])
    issues = Counter()
    samples, bars, sessions, seen = [], [], {}, set()
    previous = None

    def issue(kind, row, detail):
        issues[kind] += 1
        if len(samples) < 30:
            samples.append({"kind": kind, "row": row, "detail": detail})

    def rows():
        try:
            yield from reader
        except csv.Error:
            raise ValueError("Invalid or oversized CSV cells.") from None

    for index, row in enumerate(rows(), start=1):
        if index > MAX_BARS:
            raise ValueError("Market CSV exceeds the 5,000-bar limit.")
        if cancelled and index % 100 == 0 and cancelled():
            raise ValueError("Market research cancelled.")
        if None in row or any(value is None or len(value) > 200 for value in row.values()):
            issue("invalid_row", index, "Missing, extra or oversized cells.")
            continue
        try:
            stamp_text = row[key]
            if interval == 1440 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp_text):
                stamp = datetime.combine(date.fromisoformat(stamp_text), time(), zone)
            else:
                stamp = datetime.fromisoformat(stamp_text.replace("Z", "+00:00"))
                if stamp.tzinfo is None or stamp.utcoffset() is None:
                    raise ValueError()
            stamp = stamp.astimezone(timezone.utc)
            local = stamp.astimezone(zone)
            prices = {name: _number(row[name], name, positive=True) for name in ("open", "high", "low", "close")}
            volume = _number(row["volume"], "volume", Decimal("100000000000000"))
        except (ValueError, OverflowError):
            issue("invalid_value", index, "Invalid aware timestamp or bounded OHLCV value.")
            continue
        if "symbol" in row and row["symbol"] != metadata["symbol"]:
            issue("symbol_mismatch", index, "Row symbol differs from declared instrument.")
        if not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]:
            issue("invalid_ohlc", index, "OHLC price ordering is inconsistent.")
        if stamp in seen:
            issue("duplicate_timestamp", index, "Repeated bar timestamp.")
        if previous is not None and stamp <= previous:
            issue("non_increasing_timestamp", index, "Input is not strictly increasing; it was not reordered.")
        seen.add(stamp)
        previous = stamp
        session = local.date().isoformat()
        minute = local.hour*60 + local.minute
        if local.weekday() not in metadata["weekdays"]:
            issue("outside_weekday", index, "Bar falls outside declared weekdays.")
        if interval != 1440 and (local.second or local.microsecond or not open_minutes <= minute < close_minutes or (minute-open_minutes) % interval or minute+interval > close_minutes):
            issue("outside_session_grid", index, "Bar-open timestamp falls outside declared session grid.")
        slots = sessions.setdefault(session, set())
        if minute in slots:
            issue("duplicate_session_slot", index, "Repeated local session slot, including ambiguous DST folds.")
        slots.add(minute)
        bars.append({"timestamp": stamp.isoformat(), "end_timestamp": (stamp+timedelta(minutes=interval)).isoformat(),
            "session": session, **prices, "volume": volume})
    if not bars:
        issue("empty_dataset", 0, "No valid bars are available.")
    if interval != 1440:
        expected = set(range(open_minutes, close_minutes, interval))
        for session, actual in sessions.items():
            missing = len(expected - actual)
            if missing:
                issues["missing_session_bars"] += missing
                if len(samples) < 30:
                    samples.append({"kind": "missing_session_bars", "session": session, "count": missing})
    declared = metadata.get("expected_sessions")
    if declared is not None:
        for session in sorted(set(declared) - set(sessions)):
            issue("missing_session", 0, session)
        for session in sorted(set(sessions) - set(declared)):
            issue("undeclared_session", 0, session)
    audit = {"valid": not issues, "rows": reader.line_num-1, "parsed_bars": len(bars), "observed_sessions": sorted(sessions),
        "first_timestamp": bars[0]["timestamp"] if bars else None, "last_timestamp": bars[-1]["timestamp"] if bars else None,
        "issues": dict(sorted(issues.items())), "issue_samples": samples,
        "calendar_basis": "Owner-declared expected sessions" if declared is not None else "Observed sessions only; missing entire sessions and exchange holidays are not inferred",
        "bar_timestamp_convention": "bar_open", "adjustment_basis": metadata["adjustment"],
        "ignored_optional_columns": sorted(set(columns) - required - {key, "symbol"})}
    return bars, audit


def simulate(bars, costs, pair=None, *, initial_cash=None, history=(), benchmark=False, cancelled=None):
    """Long/flat, fixed shares, causal close signal then next same-session open."""
    cash = Decimal(initial_cash if initial_cash is not None else costs["initial_cash"])
    initial = cash
    shares = costs["shares"]
    half_spread_slip = (Decimal(costs["spread_bps"])/2 + Decimal(costs["slippage_bps"]))/ _BPS
    fee_rate = Decimal(costs["fee_bps"])/_BPS
    commission = Decimal(costs["commission_per_order"])
    position, fills, equity, skipped = 0, [], [], 0
    closes = [bar["close"] for bar in history][-100:]
    fees, adverse = _ZERO, _ZERO
    peak, drawdown, exposed = initial, _ZERO, 0
    pending = False
    pending_signal = None

    def fill(side, bar, reference, reason, signal_at):
        nonlocal cash, position, fees, adverse, skipped
        price = reference * (1+half_spread_slip if side == "buy" else 1-half_spread_slip)
        notional = price * shares
        fee = (commission + notional*fee_rate).quantize(_CENT, rounding=ROUND_HALF_UP)
        # Reserve the fixed exit commission so a long position never borrows
        # cash solely to pay its eventual sell fee.
        if side == "buy" and cash < notional+fee+commission:
            skipped += 1
            return
        cash += (-notional-fee) if side == "buy" else (notional-fee)
        position = shares if side == "buy" else 0
        fees += fee
        adverse += abs(price-reference)*shares
        fills.append({"side": side, "quantity": shares, "bar_open_at": bar["timestamp"], "session": bar["session"],
            "filled_at": bar["end_timestamp"] if reason == "planned_session_flatten" else bar["timestamp"],
            "reference_price": _format(reference), "fill_price": _format(price), "notional": _format(notional),
            "fee": _money(fee), "cash_after": _format(cash), "reason": reason, "signal_bar_open_at": signal_at,
            "fill_clock": "bar_close" if reason == "planned_session_flatten" else "bar_open"})

    for index, bar in enumerate(bars):
        if cancelled and index % 100 == 0 and cancelled():
            raise ValueError("Market research cancelled.")
        first = index == 0 or bars[index-1]["session"] != bar["session"]
        last = index == len(bars)-1 or bars[index+1]["session"] != bar["session"]
        if first:
            pending = False
            pending_signal = None
        target = benchmark if first and benchmark else pending
        if target and not position:
            fill("buy", bar, bar["open"], "scheduled_session_open" if benchmark else "prior_close_signal", pending_signal)
        elif not target and position:
            fill("sell", bar, bar["open"], "prior_close_signal", pending_signal)
        if position:
            exposed += 1
        if last and position:
            fill("sell", bar, bar["close"], "planned_session_flatten", None)
        marked = cash + position*bar["close"]
        peak = max(peak, marked)
        drawdown = max(drawdown, (peak-marked)/peak if peak else _ZERO)
        equity.append({"timestamp": bar["timestamp"], "equity": _format(marked), "position": position})
        closes.append(bar["close"])
        if len(closes) > 100:
            closes.pop(0)
        if benchmark:
            pending = not last
        elif len(closes) >= pair["slow"]:
            pending = sum(closes[-pair["fast"]:], _ZERO)/pair["fast"] > sum(closes[-pair["slow"]:], _ZERO)/pair["slow"]
        else:
            pending = False
        pending_signal = bar["timestamp"]
    assert position == 0
    return {"summary": {"initial_cash": _format(initial), "ending_cash": _format(cash), "net_profit": _format(cash-initial),
        "return_pct": _format((cash/initial-1)*100), "max_close_equity_drawdown_pct": _format(drawdown*100),
        "fees_paid": _money(fees), "spread_slippage_cost": _format(adverse), "fills": len(fills),
        "round_trips": sum(row["side"] == "sell" for row in fills), "skipped_unaffordable_entries": skipped,
        "exposed_bar_pct": _format(Decimal(exposed)*100/len(bars)) if bars else "0", "ending_position": 0},
        "fills": fills, "equity": equity}


def run_study(bars, audit, metadata, cancelled=None):
    if not audit["valid"]:
        raise ValueError("Study refused: audit has data-quality issues. Run audit and correct the input first.")
    if metadata["timeframe_minutes"] == 1440:
        raise ValueError("This fixed study supports intraday bars only; daily exports can be audited.")
    if metadata["adjustment"] == "total_return_adjusted":
        raise ValueError("Synthetic total-return prices are not supported as executable intraday prices.")
    if "study" not in metadata:
        raise ValueError("Study requires explicit costs, sizing, split and SMA assumptions in metadata.")
    costs = metadata["study"]
    if any(bar["volume"] < Decimal(costs["shares"])*10 for bar in bars):
        raise ValueError("Study refused: each bar must cover at least ten times the fixed share size; no partial-fill model is available.")
    sessions = audit["observed_sessions"]
    split = len(sessions)-costs["holdout_sessions"]
    if split < costs["train_sessions"]+1:
        raise ValueError("Study requires training, at least one walk-forward test session and a final reserved holdout.")
    if (split-costs["train_sessions"]+costs["test_sessions"]-1)//costs["test_sessions"] > MAX_FOLDS:
        raise ValueError("Study exceeds the twenty-fold computation limit.")
    if max(pair["slow"] for pair in costs["parameter_grid"]) > sum(bar["session"] in sessions[:costs["train_sessions"]] for bar in bars):
        raise ValueError("Initial training data must cover every SMA warmup.")

    def choose(training):
        candidates = []
        for pair in costs["parameter_grid"]:
            result = simulate(training, costs, pair, cancelled=cancelled)
            candidates.append({"parameters": pair, "summary": result["summary"]})
        best = max(candidates, key=lambda row: Decimal(row["summary"]["ending_cash"]))
        return best["parameters"], candidates

    folds, chain_cash, test_fills, test_equity = [], costs["initial_cash"], [], []
    for start in range(costs["train_sessions"], split, costs["test_sessions"]):
        end = min(start+costs["test_sessions"], split)
        training = [bar for bar in bars if bar["session"] < sessions[start]]
        testing = [bar for bar in bars if sessions[start] <= bar["session"] <= sessions[end-1]]
        pair, candidates = choose(training)
        result = simulate(testing, costs, pair, initial_cash=chain_cash, history=training, cancelled=cancelled)
        chain_cash = result["summary"]["ending_cash"]
        folds.append({"training_first": training[0]["timestamp"], "training_last": training[-1]["timestamp"],
            "test_first": testing[0]["timestamp"], "test_last": testing[-1]["timestamp"], "parameters": pair,
            "training_candidates": candidates, "test_summary": result["summary"]})
        test_fills.extend(result["fills"])
        test_equity.extend(result["equity"])
    pre_holdout = [bar for bar in bars if bar["session"] < sessions[split]]
    holdout = [bar for bar in bars if bar["session"] >= sessions[split]]
    fixed_pair, candidates = choose(pre_holdout)
    final = simulate(holdout, costs, fixed_pair, history=pre_holdout, cancelled=cancelled)
    benchmark = simulate(holdout, costs, benchmark=True, cancelled=cancelled)
    return {"strategy": "fixed long/flat SMA crossover; next same-session bar open; preplanned final bar close flatten",
        "selection_metric": "Training ending cash, equal initial cash/costs; ties choose sorted (fast,slow), never test/holdout outcomes",
        "walk_forward": {"folds": folds, "initial_cash": costs["initial_cash"], "ending_cash": chain_cash,
            "net_profit": _format(Decimal(chain_cash)-Decimal(costs["initial_cash"])), "fills": test_fills, "equity": test_equity},
        "reserved_holdout": {"first_timestamp": holdout[0]["timestamp"], "last_timestamp": holdout[-1]["timestamp"],
            "sessions": sessions[split:], "parameters_selected_before_holdout": fixed_pair,
            "selection_training_last_timestamp": pre_holdout[-1]["timestamp"], "training_candidates": candidates,
            "sma": final, "session_open_to_close_baseline": benchmark,
            "cash_baseline": {"ending_cash": costs["initial_cash"], "net_profit": "0"}},
        "assumptions": {"costs_and_splits": costs, "spread_semantics": "spread_bps is full spread; half plus slippage is adverse per fill",
            "fee_rounding": "per-order commission plus notional fee, rounded to two currency decimals, half-up; prices/notionals/cash retain Decimal precision",
            "sizing": "fixed whole shares, no leverage, no shorting; unaffordable entries are skipped",
            "fill_model": "next bar open with adverse costs; planned final bar close with adverse costs; no intrabar path, queue, latency or partial-fill model",
            "liquidity": "every bar volume at least ten times fixed share size; this threshold does not establish real fillability",
            "warmup": "prior training closes may warm the indicator; no pending order crosses a session or evaluation boundary",
            "calendar": audit["calendar_basis"], "holdout": "final whole sessions excluded from every parameter selection; repeated owner research can contaminate a holdout"}}


class MarketResearchTools:
    def __init__(self, registry):
        self.registry = registry

    def schemas(self):
        from .tools import _schema
        return [_schema("market_research", "Offline OHLCV audit or fixed intraday walk-forward SMA study with reserved holdout, explicit costs, no leverage and next-bar fills. Reads local CSV and JSON assumptions; writes a unique verified JSON research artifact. No live data, brokerage, orders, downloaded strategy or model calls.", {
            "action": {"type": "string", "enum": ["audit", "study"]},
            "path": {"type": "string", "description": "Workspace-relative UTF-8 single-instrument OHLCV CSV."},
            "metadata_path": {"type": "string", "description": "Workspace-relative JSON with symbol/currency/source/adjustment/timeframe/timezone/session/weekdays and explicit study assumptions."},
            "request_id": {"type": "string", "maxLength": 64}}, ["action", "path", "metadata_path", "request_id"])]

    def status(self):
        return {"ready": True, "offline": True, "reason": "Bounded local market-data audit and deterministic intraday research; no broker, live feed or model dependency."}

    def policy(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown market-research tool.")
        return "auto"

    def _read(self, value, cap=MAX_BYTES):
        path = self.registry._path(value)
        with self.registry._directory(path.parent) as fd:
            with os.fdopen(self.registry._open_read(path, fd), "rb") as source:
                raw = source.read(cap+1)
        if len(raw) > cap:
            raise ValueError("Market-research snapshot exceeds its size limit.")
        return raw

    def _publish(self, value, encoded):
        """Large derived artifacts use the registry's pinned directory contract."""
        path = self.registry._path(value)
        temporary = ".market-" + secrets.token_hex(12) + ".tmp"
        temp_path = path.parent / temporary
        with self.registry._directory(path.parent, create=True) as directory_fd:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            fd = os.open(temp_path if directory_fd is None else temporary, flags, 0o600,
                **({} if directory_fd is None else {"dir_fd": directory_fd}))
            try:
                with os.fdopen(fd, "wb") as target:
                    target.write(encoded)
                    target.flush()
                    os.fsync(target.fileno())
                self.registry._check_ancestors(path)
                if directory_fd is None:
                    os.rename(temp_path, path)
                else:
                    os.link(temporary, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
                sync_directory(path.parent)
            finally:
                try:
                    if directory_fd is None:
                        temp_path.unlink()
                    else:
                        os.unlink(temporary, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass

    def fingerprint_material(self, name, args):
        if name not in NAMES:
            raise ValueError("Unknown market-research tool.")
        return {"implementation": VERSION, "csv_sha256": hashlib.sha256(self._read(args.get("path"))).hexdigest(),
            "metadata_sha256": hashlib.sha256(self._read(args.get("metadata_path"), 50_000)).hexdigest()}

    def execute(self, name, args, *, cancelled=None):
        if name not in NAMES:
            raise ValueError("Unknown market-research tool.")
        _fields(args, {"action", "path", "metadata_path", "request_id"}, {"action", "path", "metadata_path", "request_id"})
        if type(args["action"]) is not str or args["action"] not in {"audit", "study"} or type(args["request_id"]) is not str or not _ID.fullmatch(args["request_id"]):
            raise ValueError("Invalid research action or request ID.")
        if cancelled and cancelled():
            raise ValueError("Market research cancelled before reading snapshots.")
        raw = self._read(args["path"])
        metadata_raw = self._read(args["metadata_path"], 50_000)
        metadata = validate_metadata(_load_json(metadata_raw))
        with localcontext() as ctx:
            ctx.prec = 40
            bars, audit = audit_csv(raw, metadata, cancelled)
            study = run_study(bars, audit, metadata, cancelled) if args["action"] == "study" else None
        inputs = {"csv": {"path": args["path"], "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            "metadata": {"path": args["metadata_path"], "bytes": len(metadata_raw), "sha256": hashlib.sha256(metadata_raw).hexdigest()}}
        report = {"schema": 1, "implementation": VERSION, "request_id": args["request_id"], "action": args["action"],
            "inputs": inputs, "metadata": metadata, "audit": audit, "study": study, "offline": True, "model_calls": 0,
            "timezone_database": {"zone": metadata["timezone"], "tzif_sha256": _TZFILES[metadata["timezone"]][1] if metadata["timezone"] in _TZFILES else None,
                "iana_version": _TZFILES[metadata["timezone"]][2] if metadata["timezone"] in _TZFILES else "UTC fixed offset"},
            "limits": ["Historical simulation, not a forecast, trading recommendation or evidence of future profit.",
                "No live/paper orders, accounts, provider access or external engine is connected.",
                "Owner is responsible for source rights, corporate-action provenance and session calendar correctness.",
                "Close-marked drawdown omits intrabar excursions; simplified costs do not establish executable prices."]}
        encoded = _json(report)
        if len(encoded) > MAX_REPORT_BYTES:
            raise ValueError("Research report exceeds its bounded artifact size.")
        path = "reports/market-research/"+args["request_id"]+".json"
        try:
            previous = self._read(path, MAX_REPORT_BYTES)
        except FileNotFoundError:
            previous = None
        replayed = previous is not None
        if replayed and previous != encoded:
            raise ValueError("Research request ID already exists with different inputs or modified results; choose a new ID.")
        if cancelled and cancelled():
            raise ValueError("Market research cancelled before artifact publication.")
        if not replayed:
            try:
                self._publish(path, encoded)
            except FileExistsError:
                if self._read(path, MAX_REPORT_BYTES) != encoded:
                    raise ValueError("Concurrent research publication differs; existing artifact was preserved.") from None
                replayed = True
        published = self._read(path, MAX_REPORT_BYTES)
        if published != encoded:
            raise ValueError("Research artifact verification failed; inspect the saved report.")
        summary = {"valid": audit["valid"], "bars": audit["parsed_bars"], "sessions": len(audit["observed_sessions"]), "issues": audit["issues"]}
        if study:
            summary.update(walk_forward_ending_cash=study["walk_forward"]["ending_cash"],
                holdout=study["reserved_holdout"]["sma"]["summary"],
                holdout_baseline=study["reserved_holdout"]["session_open_to_close_baseline"]["summary"])
        return {"ok": True, "action": args["action"], "summary": summary, "artifacts": [{"path": path, "bytes": len(published), "sha256": hashlib.sha256(published).hexdigest()}],
            "replayed": replayed, "offline": True, "model_calls": 0}
