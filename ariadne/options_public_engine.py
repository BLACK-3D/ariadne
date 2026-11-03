"""Finite-capital, one-position, strict-book paper kernel; no network or orders.

Schema7 explicitly seals the complete prior schema6 account. Quote-event depth
is durable in this state and replayed by the workflow, never reset per poll.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, Context, localcontext, ROUND_UP, ROUND_DOWN
import hashlib
import json
from functools import wraps

from . import options_bankroll_eod as old
from . import options_liquidity as gate
from .qqq_data import exchange_day

MAX_DEPTH = 4096
MAX_FRAMES = 100
RULE = {'schema': 7, 'id': 'public-strict-book-paper-v1', 'old_rule_sha256': old.RULE_SHA256,
    'liquidity_rule_sha256': gate.RULE_SHA256, 'total_contributions': '25000',
    'monthly_gross_ceiling': '25000', 'entry_ceiling': '1000', 'entry_equity_fraction': '0.04',
    'fee_per_contract_each_side': '0.65', 'no_automatic_topups': True,
    'position': 'One fully paid standard100 long option; BUY requires flat account and whole requested quantity. SELL only owned quantity; partial exit requires explicit permission.',
    'depth': 'Durable percontract,side,actual-side-eventUTC identity; conflicting price/size for same event refused. Opposite-side updates never refill unchanged depth. BUY separately reserves matching bid depth; SELL consumes bid without entry-only reservation.',
    'capital': 'Same account and complete sealed schema6 financial/source cutoff; no added capital, no proceeds recycling into same-month gross commitment. Entry includes both fee sides. Cash reserves exit fees.',
    'clocks': 'Real decision clock assigned by collector entrypoint; actual side event clocks preserved. Five-second strict gate; no receipt-date substitution.',
    'evidence': 'Authenticated Public quote transport plus explicit separately supplied metadata. Metadata attestations remain owner evidence. Simulations are not broker executions or proven profitable strategies.',
    'paper_only': True, 'execution_confirmed': False}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


RULE_SHA256 = digest(RULE)
FIELDS = {'schema', 'rule_sha256', 'ancestor', 'activation', 'cash', 'contributions', 'fees',
    'month', 'monthly_committed', 'monthly_start_equity', 'trade_count', 'closed_trade_count',
    'contract_entry_count', 'contract_exit_count', 'funding_count', 'started_at', 'last_decided_at',
    'position', 'pending', 'hold', 'cap_qualified', 'processed_count', 'depth', 'frame_prefix_sha256',
    'realized_pnl', 'last_daily_result', 'research_only', 'execution_qualified'}
FEE = Decimal('.65')
CENT = Decimal('.01')


def precision(fn):
    @wraps(fn)
    def guarded(*args, **kwargs):
        with localcontext(Context(prec=60)):
            return fn(*args, **kwargs)
    return guarded


def require(ok, message):
    if not ok:
        raise ValueError(message)


def money(value, *, signed=False):
    result = old.base._number(value, signed=signed)
    require(result * 100 == (result * 100).to_integral_value(), 'Paper cash must be exact cents.')
    return result


def text(value):
    return format(value, 'f')


def _cent(value, up=False):
    return value.quantize(CENT, rounding=ROUND_UP if up else ROUND_DOWN)


@precision
def migrate_flat(prior, *, activated_at, previous_state_sha256, source_cutoff_sha256):
    prior = old.validate_state(prior)
    at = gate._clock(activated_at)
    require(prior['position'] is None and prior['pending'] is None and prior['hold'] is None
        and prior['cap_qualified'], 'Public migration requires a healthy flat unheld account.')
    require(money(prior['contributions']) == Decimal('25000') and at >= gate._clock(prior['last_decided_at']),
        'Retain the existing25000 total contributions and nondecreasing activation clock.')
    require(old.base._hash(previous_state_sha256) and old.base._hash(source_cutoff_sha256), 'Exact previous state/source hashes required.')
    act = {'activated_at': at.isoformat(), 'previous_state_sha256': previous_state_sha256,
        'source_cutoff_sha256': source_cutoff_sha256, 'ancestor_sha256': digest(prior), 'funding_added': '0'}
    state = {k: deepcopy(prior[k]) for k in ('cash','contributions','fees','month','monthly_committed','monthly_start_equity',
        'trade_count','closed_trade_count','contract_entry_count','contract_exit_count','funding_count','started_at')}
    state.update(schema=7, rule_sha256=RULE_SHA256, ancestor=prior, activation=act, last_decided_at=at.isoformat(),
        position=None, pending=None, hold=None, cap_qualified=True, processed_count=0, depth={},
        frame_prefix_sha256=digest({'rule_sha256': RULE_SHA256, 'activation': act}), realized_pnl='0',
        last_daily_result=None, research_only=True, execution_qualified=False)
    return validate_state(state)


@precision
def validate_state(value):
    require(digest(RULE) == RULE_SHA256, 'The immutable Public paper rule changed.')
    require(type(value) is dict and set(value) == FIELDS and value['schema'] == 7 and type(value['schema']) is int
        and value['rule_sha256'] == RULE_SHA256, 'Use exact Public paper schema7 state.')
    require(len(canonical(value)) <= 2*1024*1024, 'Public paper state retention is full.')
    ancestor = old.validate_state(value['ancestor']); act = value['activation']
    require(type(act) is dict and set(act) == {'activated_at','previous_state_sha256','source_cutoff_sha256','ancestor_sha256','funding_added'}
        and act['funding_added'] == '0' and act['ancestor_sha256'] == digest(ancestor)
        and all(old.base._hash(act[k]) for k in ('previous_state_sha256','source_cutoff_sha256','ancestor_sha256')),
        'The sealed previous bankroll/source cutoff changed.')
    require(ancestor['position'] is None and ancestor['pending'] is None and ancestor['hold'] is None and ancestor['cap_qualified'],
        'Migration ancestry must be healthy and flat.')
    active, decided = gate._clock(act['activated_at']), gate._clock(value['last_decided_at'])
    require(gate._clock(ancestor['last_decided_at']) <= active <= decided, 'Paper clocks moved backwards.')
    require(value['contributions'] == ancestor['contributions'] and money(value['contributions']) == Decimal('25000')
        and value['funding_count'] == ancestor['funding_count'] and value['started_at'] == ancestor['started_at'],
        'The same bankroll cannot add contributions or replace its origin.')
    for k in ('cash','fees','monthly_committed','monthly_start_equity'):
        money(value[k])
    money(value['realized_pnl'], signed=True)
    require(money(value['fees']) >= money(ancestor['fees']) and money(value['monthly_committed']) <= Decimal('25000'),
        'Fees or gross commitment violated the retained capital contract.')
    require(type(value['month']) is str and old.base._month(ancestor['month']) <= old.base._month(value['month'])
        <= old.base._month(exchange_day(decided).strftime('%Y-%m')), 'Invalid account month.')
    if value['month'] == ancestor['month']:
        require(money(value['monthly_committed']) >= money(ancestor['monthly_committed']), 'Same-month spending cannot reset.')
    for k in ('trade_count','closed_trade_count','contract_entry_count','contract_exit_count','funding_count','processed_count'):
        require(type(value[k]) is int and 0 <= value[k] <= 100000000, 'Bounded exact account counts required.')
        if k != 'processed_count':
            require(value[k] >= ancestor[k], 'Previous financial counters cannot be erased.')
    require(value['processed_count'] <= MAX_FRAMES and value['pending'] is None and value['hold'] is None and value['cap_qualified'] is True
        and value['research_only'] is True and value['execution_qualified'] is False, 'Unknown or held paper state.')
    require(value['trade_count']-value['closed_trade_count'] in (0,1) and value['contract_entry_count'] >= value['contract_exit_count'],
        'Inventory counters disagree.')
    require(type(value['depth']) is dict and len(value['depth']) <= MAX_DEPTH and old.base._hash(value['frame_prefix_sha256']), 'Depth/source retention exceeded.')
    for key, row in value['depth'].items():
        require(type(row) is dict and set(row) == {'contract_id','side','event_at','price','size','consumed','reserved'}
            and row['side'] in ('bid','ask') and gate._OCC.fullmatch(row['contract_id'])
            and key == digest([row['contract_id'],row['side'],row['event_at']]), 'Malformed durable quote event.')
        gate._clock(row['event_at']); gate._number(row['price'], nullable=True); gate._integer(row['size'], nullable=True)
        for k in ('consumed','reserved'):
            gate._integer(row[k])
        require(row['consumed'] <= (row['size'] or 0) and row['reserved'] <= (row['size'] or 0)
            and (row['side'] != 'ask' or row['reserved'] == 0), 'Displayed quote depth was overspent.')
    position = value['position']
    if position is not None:
        require(type(position) is dict and set(position) == {'contract_id','quantity','original_quantity','premium_remaining','entry_fee_remaining',
            'entry_ask','entry_at','marked_bid','marked_at','exit_fee_reserved','underlying_symbol','strategy_id'}, 'Malformed owned option inventory.')
        require(gate._OCC.fullmatch(position['contract_id']) and type(position['quantity']) is int
            and type(position['original_quantity']) is int and 0 < position['quantity'] <= position['original_quantity'] <= 10000,
            'Positive owned integer quantity required.')
        for k in ('premium_remaining','entry_fee_remaining','exit_fee_reserved'):
            money(position[k])
        gate._number(position['entry_ask']); gate._number(position['marked_bid']); gate._clock(position['entry_at']); gate._clock(position['marked_at'])
        require(money(position['exit_fee_reserved']) == FEE*position['quantity'] and money(value['cash']) >= money(position['exit_fee_reserved'])
            and value['contract_entry_count']-value['contract_exit_count'] == position['quantity']
            and value['trade_count']-value['closed_trade_count'] == 1, 'Inventory, reserved exit fees and financial counts disagree.')
    else:
        require(value['contract_entry_count'] == value['contract_exit_count'] and value['trade_count'] == value['closed_trade_count'], 'Flat inventory counters disagree.')
    if value['processed_count'] == 0:
        require(value['depth'] == {} and value['position'] is None and value['realized_pnl'] == '0' and value['last_daily_result'] is None
            and value['frame_prefix_sha256'] == digest({'rule_sha256':RULE_SHA256,'activation':act})
            and all(value[k] == ancestor[k] for k in ('cash','fees','month','monthly_committed','monthly_start_equity','trade_count','closed_trade_count','contract_entry_count','contract_exit_count')),
            'An untouched migration cannot alter finances or quote evidence.')
    return deepcopy(value)


def _side_key(contract, side, stamp):
    return digest([contract, side, gate._clock(stamp).isoformat()])


def _prepare_depth(state, books, observations):
    """Validate all event aliases before evaluating even the first request."""
    parsed = {}; observed = {}
    require(type(observations) is list and len(observations) <= 256, 'Bounded Public observations required.')
    for row in observations:
        instrument = row.get('instrument') if type(row) is dict else None
        if type(instrument) is dict and instrument.get('type') == 'OPTION':
            contract = instrument.get('symbol')
            require(contract not in observed, 'Duplicate Public contract observation.')
            observed[contract] = row
    for book in books:
        gate._book(book)
        contract = book['contract_id']
        require(contract not in parsed and contract in observed, 'Unique books must match actual Public option observations.')
        source = observed[contract]; keys = {}
        clocks = [gate._clock(source[side+'_at']) for side in ('bid','ask') if source.get(side) is not None and source.get(side+'_at') is not None]
        complete = all(source.get(side) is None or source.get(side+'_at') is not None for side in ('bid','ask'))
        expected_clock = min(clocks).isoformat() if clocks and complete else None
        require((gate._clock(book['quote_at']).isoformat() if book['quote_at'] is not None else None) == expected_clock, 'Book clock must retain the oldest actual quoted side event.')
        for side in ('bid','ask'):
            stamp = source.get(side+'_at')
            require(source.get(side) == book[side] and source.get(side+'_size') == book[side+'_size'], 'Quote normalization changed prices or sizes.')
            if stamp is None:
                require(book[side] is None or book['quote_at'] is None, 'Missing side event cannot acquire a quote time.')
                keys[side] = None
                continue
            event = gate._clock(stamp)
            require(event <= gate._clock(book['received_at']), 'Quote event is later than receipt.')
            key = _side_key(contract, side, stamp); keys[side] = key
            identity = {'contract_id':contract,'side':side,'event_at':event.isoformat(),'price':book[side],'size':book[side+'_size']}
            prior = state['depth'].get(key)
            if prior is not None:
                require(all(prior[k] == identity[k] for k in identity), 'Same Public side event changed price or size; the complete frame is refused.')
            else:
                require(len(state['depth']) < MAX_DEPTH, 'Durable quote depth retention is full; no history was reset.')
                state['depth'][key] = {**identity,'consumed':0,'reserved':0}
        parsed[contract] = (book,keys)
    return parsed


def advance(state, books, requests, observations, *, decided_at):
    """Apply quote-bound simulated fills. No funding, broker or network operation."""
    with localcontext(Context(prec=60)):
        state = validate_state(state); before = deepcopy(state); at = gate._clock(decided_at)
        require(at >= gate._clock(state['last_decided_at']), 'Paper decisions cannot move backwards.')
        require(type(books) is list and 1 <= len(books) <= 128 and type(requests) is list and 1 <= len(requests) <= 128,
            'Use1..128books and requests per paper frame.')
        require(state['processed_count'] < MAX_FRAMES, 'Paper frame retention is full.')
        parsed = _prepare_depth(state, books, observations)
        ids = set(); gate_requests = []
        for request in requests:
            require(type(request) is dict and set(request) == {'request_id','contract_id','side','quantity','price_limit','allow_partial'}, 'Exact paper request fields required; caller decision clocks are refused.')
            contract = request['contract_id']; require(contract in parsed, 'Requested contract was not collected.')
            b = parsed[contract][0]
            g = {**request,'source_id':b['source_id'],'snapshot_id':b['snapshot_id'],'decision_at':at.isoformat()}
            gate._request(g)
            require(request['request_id'] not in ids, 'Duplicate frame request ID.')
            ids.add(request['request_id']); gate_requests.append(g)
        month = exchange_day(at).strftime('%Y-%m'); events = []
        if month != state['month']:
            require(state['position'] is None, 'Prior-month open inventory requires review; spending never silently resets.')
            state.update(month=month, monthly_committed='0', monthly_start_equity=state['cash'])
            events.append({'type':'monthly_allowance_reset','funding_added':'0','recorded_at':at.isoformat()})
        proposals = []
        for request in gate_requests:
            book, keys = parsed[request['contract_id']]
            evaluated = deepcopy(book)
            if request['side'] == 'sell':
                # A real fresh buyer supports an exit even when ask did not change.
                evaluated['quote_at'] = state['depth'][keys['bid']]['event_at'] if keys['bid'] else None
            proposal = gate.assess_book(evaluated, at=at.isoformat(), side=request['side'], quantity=request['quantity'],
                price_limit=request['price_limit'], allow_partial=request['allow_partial'])
            proposal['request_id'] = request['request_id']
            proposal['quote_clock_basis'] = 'actual_bid_event' if request['side'] == 'sell' else 'oldest_actual_bid_ask_event'
            reasons = list(proposal['reason_codes']); quantity = proposal['proposed_quantity']
            side = request['side']; price = gate._number(proposal['quoted_price'], nullable=True)
            bid = state['depth'].get(keys['bid']); ask = state['depth'].get(keys['ask'])
            bid_remaining = max(0,(bid['size'] or 0)-bid['consumed']) if bid else 0
            ask_remaining = max(0,(ask['size'] or 0)-ask['consumed']) if ask else 0
            if side == 'buy':
                if state['position'] is not None:
                    reasons.append('existing_position')
                if ask_remaining < request['quantity']:
                    reasons.append('durable_ask_depth_insufficient')
                if bid_remaining-(bid['reserved'] if bid else 0) < request['quantity']:
                    reasons.append('durable_entry_bid_depth_insufficient')
                if price is not None:
                    all_in = _cent(price*100*request['quantity'],True) + FEE*2*request['quantity']
                    maximum = min(Decimal('1000'),money(state['cash'])*Decimal('.04'),money(state['cash']),Decimal('25000')-money(state['monthly_committed']))
                    if all_in > maximum:
                        reasons.append('entry_cap_or_cash_exceeded')
            else:
                position = state['position']
                if position is None or position['contract_id'] != request['contract_id'] or request['quantity'] > position['quantity']:
                    reasons.append('sell_exceeds_owned_inventory')
                if bid_remaining < request['quantity']:
                    if request['allow_partial']:
                        quantity = min(quantity,bid_remaining)
                    else:
                        reasons.append('durable_bid_depth_insufficient')
                if quantity <= 0:
                    reasons.append('durable_bid_depth_unavailable')
            reasons = list(dict.fromkeys(reasons))
            if reasons:
                proposal.update(status='no_fill',proposed_quantity=0,remainder_quantity=request['quantity'],reason_codes=reasons)
            else:
                if side == 'buy':
                    premium = _cent(price*100*quantity,True); fee = FEE*quantity
                    state.update(cash=text(money(state['cash'])-premium-fee), fees=text(money(state['fees'])+fee),
                        monthly_committed=text(money(state['monthly_committed'])+premium+2*fee),trade_count=state['trade_count']+1,
                        contract_entry_count=state['contract_entry_count']+quantity)
                    state['position'] = {'contract_id':request['contract_id'],'underlying_symbol':book['underlying_symbol'],
                        'quantity':quantity,'original_quantity':quantity,'premium_remaining':text(premium),'entry_fee_remaining':text(fee),
                        'entry_ask':book['ask'],'entry_at':at.isoformat(),'marked_bid':book['bid'],
                        'marked_at':bid['event_at'],'exit_fee_reserved':text(fee),'strategy_id':'owner_reviewed_public_candidate'}
                    ask['consumed'] += quantity; bid['reserved'] += quantity
                else:
                    position = state['position']; owned = position['quantity']; fee = FEE*quantity
                    gross = _cent(price*100*quantity); premium = money(position['premium_remaining']) if quantity == owned else _cent(money(position['premium_remaining'])*quantity/owned)
                    entry_fee = money(position['entry_fee_remaining']) if quantity == owned else _cent(money(position['entry_fee_remaining'])*quantity/owned)
                    pnl = gross-fee-premium-entry_fee
                    require(money(state['cash'])+gross-fee >= FEE*(owned-quantity), 'Exit cannot spend reserved cash beyond the bankroll.')
                    state.update(cash=text(money(state['cash'])+gross-fee), fees=text(money(state['fees'])+fee),
                        realized_pnl=text(money(state['realized_pnl'],signed=True)+pnl),contract_exit_count=state['contract_exit_count']+quantity)
                    if quantity == owned:
                        state['position'] = None; state['closed_trade_count'] += 1
                    else:
                        position.update(quantity=owned-quantity,premium_remaining=text(money(position['premium_remaining'])-premium),
                            entry_fee_remaining=text(money(position['entry_fee_remaining'])-entry_fee),exit_fee_reserved=text(FEE*(owned-quantity)),
                            marked_bid=book['bid'],marked_at=bid['event_at'])
                    bid['consumed'] += quantity
                proposal.update(status='paper_fill',proposed_quantity=quantity,remainder_quantity=request['quantity']-quantity,
                    reason_codes=[],portfolio_controls_checked=True)
                events.append({'type':'paper_fill','side':side,'contract_id':request['contract_id'],'quantity':quantity,
                    'price':text(price),'fee':text(fee),'recorded_at':at.isoformat(),'simulated':True,'execution_confirmed':False})
            proposals.append(proposal)
        state.update(last_decided_at=at.isoformat(), processed_count=state['processed_count']+1)
        frame = {'books_sha256':digest(books),'requests':requests,'observations_sha256':digest(observations),'decided_at':at.isoformat(),
            'previous_sha256':before['frame_prefix_sha256'],'proposals':proposals,'events':events}
        state['frame_prefix_sha256'] = digest(frame)
        summary = _summary(state)
        state['last_daily_result'] = {'date':exchange_day(at).isoformat(),'recorded_at':at.isoformat(),'cash':state['cash'],
            'equity':summary['equity'],'trading_pnl':summary['trading_pnl'],'fees':state['fees'],'contributions':state['contributions'],
            'monthly_budget_used':state['monthly_committed'],'monthly_budget_remaining':summary['monthly_budget_remaining'],
            'trade_count':state['trade_count'],'closed_trade_count':state['closed_trade_count'],'simulated':True,'research_only':True,
            'execution_qualified':False}
        return {'state':validate_state(state),'proposals':proposals,'events':events,'frame':frame,'funding_added':'0',
            'execution_confirmed':False,'paper_only':True,'status':'completed'}


def _summary(state):
    position = state['position']; reserve = money(position['exit_fee_reserved']) if position else Decimal(0)
    equity = money(state['cash']) + (_cent(gate._number(position['marked_bid'])*100*position['quantity'])-reserve if position else 0)
    return {'schema':7,'cash':state['cash'],'equity':text(equity),'contributions':state['contributions'],
        'trading_pnl':text(equity-money(state['contributions'])),'fees':state['fees'],'month':state['month'],
        'monthly_budget_used':state['monthly_committed'],'monthly_budget_remaining':text(Decimal('25000')-money(state['monthly_committed'])),
        'monthly_pnl':text(equity-money(state['monthly_start_equity'])),'bankroll_total':'25000','monthly_commitment_ceiling':'25000',
        'entry_maximum':'1000','entry_equity_fraction':'0.04','automatic_topups':False,'funding_basis':'one_time_total_bankroll',
        'position':deepcopy(position),'pending':None,'hold':None,'cap_assurance':True,'cap_qualified':True,'strategy_id':'owner_reviewed_public_candidate',
        'trade_count':state['trade_count'],'closed_trade_count':state['closed_trade_count'],'realized_pnl':state['realized_pnl'],
        'last_daily_result':deepcopy(state['last_daily_result']),'execution_qualified':False,'research_only':True,
        'mark_basis':'Last saved actual bid less reserved exit fee; may be stale and is not a liquidation guarantee.',
        'execution':'Simulated strict top-of-book fills, never broker executions or proven profitability.'}


@precision
def summarize(state, now=None):
    return _summary(validate_state(state))
