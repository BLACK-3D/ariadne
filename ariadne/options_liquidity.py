"""Strict saved top-of-book research proposals; never orders or broker executions.

Caller attestations identify evidence but do not authenticate a market provider.
Consumption is conservative within one batch. Continuing across batches requires
an external, durable source/consumption ledger; this pure module supplies none.
Legacy date-only EOD prices cannot be promoted to actual event-time quotes.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
import hashlib
import json
import re

from .qqq_data import exchange_day, is_session, session_close_utc, session_dates

MAX_BOOKS = MAX_REQUESTS = 256
MAX_QUANTITY = 10_000
RULE = {
    'schema': 1, 'id': 'strict-options-top-book-research-v1',
    'quote_kind': 'top_of_book', 'max_quote_age_seconds': 5,
    'underlying_dollar_adv20_minimum': '25000000',
    'contract_same_session_volume_minimum': 500,
    'prior_session_open_interest_minimum': 1000,
    'entry_spread_maximum_fraction_of_midpoint': '.05',
    'entry_spread_maximum_dollars': '.10',
    'entry': 'Actual positive ask and bid; requested quantity covered by remaining displayed size on both sides; stock/ETF below100; exact20prior-session dollar volume; at-decision known current-session volume and latestprior-session OI; fixed price protection.',
    'exit': 'Actual fresh positive bid and known remaining bid size; optional explicit partial SELL; no entry activity/underlying-price/spread floors. Missing ask is permitted; any existing ask must be positive and noncrossed.',
    'identity': 'Exact canonical unadjusted-looking OCC identity with explicitly verified100-share deliverable; contract/type/expiry/strike agree. Caller provenance is not provider authentication.',
    'clocks': 'Actual aware quote-event and received clocks, event<=received<=decision; event and decision in same regular US stock-option session including early closes; age<=5seconds. No source-date or receipt-time substitution.',
    'consumption': 'One batch; contract+actualUTCevent clock shares displayed depth across source/snapshot aliases; contradictory quote bodies fail the complete batch before results. Repeated snapshots never add liquidity. Opposite sides consume independently; new BUY proposals separately reserve remaining bid depth for entry sanity without blocking protective SELL proposals.',
    'price_protection': 'BUY actualask<=fixed callerprice_limit; SELL actualbid>=fixed callerprice_limit. Limits never altered, prices never interpolated, no midpoint/last/threshold fills.',
    'threshold_basis': 'Fixed engineering research defaults, not optimized returns or a universal liquidity guarantee.',
    'paper_only': True, 'execution_confirmed': False,
    'provider_authenticated': False, 'portfolio_controls_checked': False,
}

def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

RULE_SHA256 = hashlib.sha256(_canonical(RULE)).hexdigest()
BOOK_KEYS = frozenset(('schema', 'source_id', 'snapshot_id', 'quote_kind', 'underlying_symbol',
    'underlying_type', 'contract_id', 'expiry', 'type', 'strike', 'multiplier',
    'standard_deliverable_verified', 'deliverable_verification_source', 'bid', 'ask',
    'bid_size', 'ask_size', 'quote_at', 'received_at', 'underlying_quote',
    'underlying_activity', 'contract_activity', 'open_interest'))
REQUEST_KEYS = frozenset(('request_id', 'source_id', 'snapshot_id', 'contract_id',
    'decision_at', 'side', 'quantity', 'price_limit', 'allow_partial'))
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_SYMBOL = re.compile(r'[A-Z][A-Z0-9]{0,5}\Z')
_OCC = re.compile(r'([A-Z][A-Z0-9]{0,5})([0-9]{6})([CP])([0-9]{8})\Z')
_CLOCK = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z')
_NUMBER = re.compile(r'(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,8})?\Z')

class LiquidityError(ValueError):
    """Invalid or contradictory research evidence; no batch result is returned."""

def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise LiquidityError('Use the exact documented research object fields.')
    return value

def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise LiquidityError('Use a bounded ASCII evidence identifier.')
    return value

def _number(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or len(value) > 32 or not _NUMBER.fullmatch(value):
        raise LiquidityError('Use nonnegative bounded decimal strings, never floats or exponents.')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise LiquidityError('Invalid decimal string.') from None
    if not result.is_finite() or result > Decimal('100000000000000'):
        raise LiquidityError('Decimal amount exceeds its research bound.')
    return result

def _integer(value, *, nullable=False, maximum=1_000_000_000_000):
    if value is None and nullable:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        raise LiquidityError('Use a bounded nonnegative integer, never a boolean.')
    return value

def _clock(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or not _CLOCK.fullmatch(value):
        raise LiquidityError('Use actual aware ISO event clocks with seconds; date-only times are refused.')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.utcoffset() is None or abs(result.utcoffset()) > timedelta(hours=14):
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise LiquidityError('Invalid aware event clock.') from None

def _date(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise LiquidityError('Use exact ISO session/expiry dates.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise LiquidityError('Invalid calendar date.') from None

def _text(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or not 1 <= len(value) <= 256 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise LiquidityError('Use bounded printable ASCII provenance text.')
    return value

def _iso(value):
    return value.isoformat() if value is not None else None

def _session(clock):
    """The existing reviewed calendar includes scheduled early market closes."""
    try:
        day = exchange_day(clock)
        if not is_session(day):
            return None
        close = session_close_utc(day)
        # The exchange opens09:30Eastern on both ordinary and early-close days.
        from .briefing import _tz
        opened = datetime(day.year, day.month, day.day, 8, 30, tzinfo=_tz('America/Chicago')).astimezone(timezone.utc)
        return day if opened <= clock < close else None
    except (ValueError, OverflowError):
        return None

def _prior_sessions(day):
    try:
        return session_dates(day - timedelta(days=60), day - timedelta(days=1))[-20:]
    except (ValueError, OverflowError):
        return []

def _book(raw):
    _object(raw, BOOK_KEYS)
    if type(raw['schema']) is not int or raw['schema'] != 1:
        raise LiquidityError('Unsupported book schema.')
    for name in ('source_id', 'snapshot_id'):
        _identifier(raw[name])
    if raw['quote_kind'] != 'top_of_book':
        raise LiquidityError('This protocol admits explicitly labeled top-of-book observations only.')
    if type(raw['underlying_symbol']) is not str or not _SYMBOL.fullmatch(raw['underlying_symbol']):
        raise LiquidityError('Unsupported canonical stock/ETF symbol.')
    _text(raw['underlying_type'])
    contract = raw['contract_id']
    match = _OCC.fullmatch(contract) if type(contract) is str else None
    if not match:
        raise LiquidityError('Use an exact canonical OCC contract identifier.')
    expiry = _date(raw['expiry'])
    strike = _number(raw['strike'])
    if (match[1] != raw['underlying_symbol'] or expiry.strftime('%y%m%d') != match[2]
            or expiry.year < 2000 or expiry.year > 2035 or raw['type'] != ('call' if match[3] == 'C' else 'put')
            or strike <= 0 or strike * 1000 != Decimal(match[4])):
        raise LiquidityError('Contract identity, type, strike or expiry contradicts the evidence.')
    _integer(raw['multiplier'], maximum=100_000)
    if type(raw['standard_deliverable_verified']) is not bool:
        raise LiquidityError('Deliverable verification must be an explicit boolean.')
    _text(raw['deliverable_verification_source'], nullable=True)
    value = {'raw': deepcopy(raw), 'expiry': expiry, 'strike': strike}
    for name in ('bid', 'ask'):
        value[name] = _number(raw[name], nullable=True)
    for name in ('bid_size', 'ask_size'):
        value[name] = _integer(raw[name], nullable=True)
    for name in ('quote_at', 'received_at'):
        value[name] = _clock(raw[name], nullable=True)
    underlying = raw['underlying_quote']
    if underlying is not None:
        _object(underlying, ('price', 'quote_at', 'received_at'))
        value['underlying_quote'] = {'price': _number(underlying['price'], nullable=True),
            'quote_at': _clock(underlying['quote_at'], nullable=True),
            'received_at': _clock(underlying['received_at'], nullable=True)}
    else:
        value['underlying_quote'] = None
    activity = raw['underlying_activity']
    if activity is not None:
        _object(activity, ('sessions',))
        if type(activity['sessions']) is not list or len(activity['sessions']) > 20:
            raise LiquidityError('ADV evidence is a bounded list of at most20session observations.')
        bars = []
        for row in activity['sessions']:
            _object(row, ('session', 'dollar_volume', 'published_at'))
            bars.append({'session': _date(row['session']), 'dollar_volume': _number(row['dollar_volume'], nullable=True),
                         'published_at': _clock(row['published_at'], nullable=True)})
        value['underlying_activity'] = bars
    else:
        value['underlying_activity'] = None
    activity = raw['contract_activity']
    if activity is not None:
        _object(activity, ('volume', 'as_of', 'published_at'))
        value['contract_activity'] = {'volume': _integer(activity['volume'], nullable=True),
            'as_of': _clock(activity['as_of'], nullable=True), 'published_at': _clock(activity['published_at'], nullable=True)}
    else:
        value['contract_activity'] = None
    interest = raw['open_interest']
    if interest is not None:
        _object(interest, ('value', 'session', 'published_at'))
        value['open_interest'] = {'value': _integer(interest['value'], nullable=True),
            'session': _date(interest['session'], nullable=True), 'published_at': _clock(interest['published_at'], nullable=True)}
    else:
        value['open_interest'] = None
    value['lookup'] = (raw['source_id'], raw['snapshot_id'], contract)
    # Source aliases cannot sum the same consolidated displayed liquidity.
    value['depth_identity'] = (contract, _iso(value['quote_at'])) if value['quote_at'] is not None else None
    value['quote_body'] = {name: raw[name] for name in ('underlying_symbol', 'underlying_type', 'contract_id',
        'expiry', 'type', 'multiplier', 'standard_deliverable_verified')}
    value['quote_body'].update(strike=format(strike, 'f'), bid=None if value['bid'] is None else format(value['bid'], 'f'),
        ask=None if value['ask'] is None else format(value['ask'], 'f'), bid_size=value['bid_size'], ask_size=value['ask_size'])
    return value

def _request(raw):
    _object(raw, REQUEST_KEYS)
    for name in ('request_id', 'source_id', 'snapshot_id'):
        _identifier(raw[name])
    if type(raw['contract_id']) is not str or not _OCC.fullmatch(raw['contract_id']):
        raise LiquidityError('Request requires an exact OCC contract identifier.')
    if raw['side'] not in ('buy', 'sell'):
        raise LiquidityError('Use buy or sell research side.')
    quantity = _integer(raw['quantity'], maximum=MAX_QUANTITY)
    if quantity == 0 or type(raw['allow_partial']) is not bool or raw['side'] == 'buy' and raw['allow_partial']:
        raise LiquidityError('Use positive requested quantity; only an explicit SELL can allow a partial proposal.')
    limit = _number(raw['price_limit'])
    if limit <= 0:
        raise LiquidityError('Explicit positive price protection is required.')
    return {**deepcopy(raw), 'at': _clock(raw['decision_at']), 'limit': limit}

def _quote_reasons(book, at):
    raw, event, received = book['raw'], book['quote_at'], book['received_at']
    reasons = []
    day = _session(at)
    if day is None:
        reasons.append('outside_regular_session')
    if raw['underlying_type'] not in ('stock', 'etf'):
        reasons.append('unsupported_underlying_type')
    if not raw['standard_deliverable_verified'] or raw['multiplier'] != 100 or raw['deliverable_verification_source'] is None:
        reasons.append('standard_deliverable_unverified')
    if day is not None and book['expiry'] < day:
        reasons.append('contract_expired')
    if event is None or received is None:
        reasons.append('actual_quote_clock_missing')
    else:
        if received < event:
            reasons.append('quote_clock_inconsistent')
        if event > at or received > at:
            reasons.append('quote_not_available_at_decision')
        if at - event > timedelta(seconds=5):
            reasons.append('quote_stale')
        if _session(event) is None or _session(event) != day:
            reasons.append('quote_outside_decision_session')
    if book['bid'] is None or book['bid'] <= 0:
        reasons.append('positive_bid_missing')
    if book['ask'] is not None and book['ask'] <= 0:
        reasons.append('invalid_existing_ask')
    if book['ask'] is not None and book['bid'] is not None and book['bid'] > book['ask']:
        reasons.append('crossed_quote')
    return reasons

def _entry_reasons(book, at):
    reasons = []
    day = _session(at)
    if book['ask'] is None:
        reasons.append('positive_ask_missing')
    elif book['bid'] is not None and book['bid'] > 0 and book['ask'] >= book['bid']:
        spread = book['ask'] - book['bid']
        if spread > Decimal('.10') or spread > (book['ask'] + book['bid']) / 2 * Decimal('.05'):
            reasons.append('entry_spread_too_wide')
    stock = book['underlying_quote']
    if stock is None or any(stock[k] is None for k in ('price', 'quote_at', 'received_at')):
        reasons.append('underlying_quote_missing')
    else:
        if not 0 < stock['price'] < 100:
            reasons.append('underlying_not_below_100')
        event, received = stock['quote_at'], stock['received_at']
        if received < event or event > at or received > at or at - event > timedelta(seconds=5) or _session(event) is None or _session(event) != day:
            reasons.append('underlying_quote_unavailable_or_stale')
    previous = _prior_sessions(day) if day is not None else []
    bars = book['underlying_activity']
    if bars is None or len(bars) != 20 or len(previous) != 20 or [b['session'] for b in bars] != previous:
        reasons.append('underlying_adv20_sessions_missing')
    elif any(b['dollar_volume'] is None or b['published_at'] is None for b in bars):
        reasons.append('underlying_adv20_evidence_missing')
    elif any(b['published_at'] > at or b['published_at'] < session_close_utc(b['session']) for b in bars):
        reasons.append('underlying_adv20_not_available')
    elif sum((b['dollar_volume'] for b in bars), Decimal(0)) / 20 < Decimal('25000000'):
        reasons.append('underlying_adv20_below_floor')
    activity = book['contract_activity']
    if activity is None or any(activity[k] is None for k in ('volume', 'as_of', 'published_at')):
        reasons.append('contract_volume_missing')
    elif (activity['as_of'] > at or activity['published_at'] > at or activity['published_at'] < activity['as_of']
            or _session(activity['as_of']) is None or _session(activity['as_of']) != day):
        reasons.append('contract_volume_not_available')
    elif activity['volume'] < 500:
        reasons.append('contract_volume_below_floor')
    interest = book['open_interest']
    if interest is None or any(interest[k] is None for k in ('value', 'session', 'published_at')):
        reasons.append('open_interest_missing')
    elif (not previous or interest['session'] != previous[-1] or interest['published_at'] > at
            or interest['published_at'] < session_close_utc(interest['session'])):
        reasons.append('open_interest_not_available')
    elif interest['value'] < 1000:
        reasons.append('open_interest_below_floor')
    return reasons

def evaluate_batch(books, requests):
    """Return quote-bound paper proposals without changing inputs or external state.

    All structure/identity conflicts are validated before any proposal is built.
    Book prices and depth are caller-provided evidence, not authenticated fills.
    """
    with localcontext(Context(prec=60)):
        if hashlib.sha256(_canonical(RULE)).hexdigest() != RULE_SHA256:
            raise LiquidityError('Frozen liquidity rule was changed in memory.')
        if type(books) is not list or not 1 <= len(books) <= MAX_BOOKS or type(requests) is not list or not 1 <= len(requests) <= MAX_REQUESTS:
            raise LiquidityError('Use1..256books and1..256requests in one bounded batch.')
        lookup, resources = {}, {}
        for raw in books:
            book = _book(raw)
            existing = lookup.get(book['lookup'])
            if existing is not None and _canonical(existing['raw']) != _canonical(book['raw']):
                raise LiquidityError('Conflicting source/snapshot/contract reuse; complete batch refused.')
            lookup[book['lookup']] = book
            identity = book['depth_identity']
            if identity is not None:
                body = _canonical(book['quote_body'])
                if identity in resources and resources[identity]['quote_body'] != body:
                    raise LiquidityError('Conflicting aliases for one actual contract quote event; complete batch refused.')
                if identity not in resources:
                    resources[identity] = {'quote_body': body, 'book': book, 'bid_consumed': 0, 'ask_consumed': 0, 'bid_reserved_for_entries': 0,
                        'depth_key': hashlib.sha256(_canonical(list(identity))).hexdigest()}
        parsed, ids, prior = [], set(), None
        for raw in requests:
            request = _request(raw)
            if request['request_id'] in ids:
                raise LiquidityError('Duplicate request identifier; complete batch refused.')
            ids.add(request['request_id'])
            if prior is not None and request['at'] < prior:
                raise LiquidityError('Requests must be in nondecreasing decision-time order.')
            prior = request['at']
            key = (request['source_id'], request['snapshot_id'], request['contract_id'])
            if key not in lookup:
                raise LiquidityError('Request does not reference an exact supplied book.')
            parsed.append((request, lookup[key]))
        proposals = []
        for request, book in parsed:
            side, quantity, at = request['side'], request['quantity'], request['at']
            reasons = _quote_reasons(book, at)
            if side == 'buy':
                reasons.extend(_entry_reasons(book, at))
            resource = resources.get(book['depth_identity'])
            remaining = {s: max(0, (book[s + '_size'] or 0) - (resource[s + '_consumed'] if resource else 0)) for s in ('bid', 'ask')}
            price = book['ask'] if side == 'buy' else book['bid']
            if price is not None and (price > request['limit'] if side == 'buy' else price < request['limit']):
                reasons.append('price_protection_not_met')
            proposed = 0
            if side == 'buy':
                if remaining['ask'] < quantity:
                    reasons.append('ask_depth_insufficient')
                entry_bid = max(0, remaining['bid'] - (resource['bid_reserved_for_entries'] if resource else 0))
                if entry_bid < quantity:
                    reasons.append('entry_bid_depth_insufficient')
                if not reasons:
                    proposed = quantity
            else:
                if remaining['bid'] == 0:
                    reasons.append('bid_depth_unavailable')
                elif remaining['bid'] < quantity and not request['allow_partial']:
                    reasons.append('bid_depth_insufficient')
                if not reasons:
                    proposed = min(quantity, remaining['bid'])
            if proposed:
                resource[('ask' if side == 'buy' else 'bid') + '_consumed'] += proposed
                if side == 'buy':
                    resource['bid_reserved_for_entries'] += proposed
            proposals.append({'request_id': request['request_id'], 'status': 'paper_proposal' if proposed else 'no_fill',
                'reason_codes': list(dict.fromkeys(reasons)), 'side': side, 'requested_quantity': quantity,
                'proposed_quantity': proposed, 'remainder_quantity': quantity - proposed,
                'quoted_price': format(price, 'f') if price is not None else None,
                'source_id': book['raw']['source_id'], 'snapshot_id': book['raw']['snapshot_id'],
                'contract_id': book['raw']['contract_id'], 'quote_at': _iso(book['quote_at']),
                'decision_at': _iso(at), 'price_limit': request['price_limit'],
                'depth_key': resource['depth_key'] if resource else None,
                'execution_confirmed': False, 'portfolio_controls_checked': False,
                'provider_authenticated': False, 'top_of_book_only': True})
        consumption = []
        for identity, row in sorted(resources.items()):
            book = row['book']
            consumption.append({'depth_key': row['depth_key'], 'contract_id': identity[0], 'quote_at': identity[1],
                'bid_size': book['bid_size'], 'ask_size': book['ask_size'],
                'bid_consumed': row['bid_consumed'], 'ask_consumed': row['ask_consumed'],
                'bid_reserved_for_entries': row['bid_reserved_for_entries'],
                'entry_bid_remaining': None if book['bid_size'] is None else max(0, book['bid_size'] - row['bid_consumed'] - row['bid_reserved_for_entries']),
                'bid_remaining': None if book['bid_size'] is None else book['bid_size'] - row['bid_consumed'],
                'ask_remaining': None if book['ask_size'] is None else book['ask_size'] - row['ask_consumed']})
        return {'schema': 1, 'status': 'completed', 'rule_sha256': RULE_SHA256,
            'proposals': proposals, 'depth_consumption': consumption, 'paper_only': True,
            'execution_confirmed': False, 'provider_authenticated': False,
            'portfolio_controls_checked': False, 'top_of_book_only': True,
            'depth_scope': 'One batch only; cross-batch continuation requires a separate durable consumption ledger.'}

def assess_book(book, *, at, side, quantity, price_limit, allow_partial=False):
    """Convenience wrapper; the returned single proposal is still not a fill."""
    if type(book) is not dict:
        raise LiquidityError('Use a documented book object.')
    request = {'request_id': 'single-research-request', 'source_id': book.get('source_id'),
        'snapshot_id': book.get('snapshot_id'), 'contract_id': book.get('contract_id'),
        'decision_at': at, 'side': side, 'quantity': quantity, 'price_limit': price_limit,
        'allow_partial': allow_partial}
    return evaluate_batch([book], [request])['proposals'][0]
