"""Pure, account-bound options feed admission checks; never a financial writer.

Schema6/7 rules and state are unchanged. This bridge can qualify evidence for a
future reviewed feed migration, but cannot submit a paper fill or allocate funds.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, Context, localcontext
import hashlib
import json

from . import options_liquidity as gate

SOURCE_ID = 'alpaca_opra_v1'
SOURCE_IDS = frozenset((SOURCE_ID,'theta_terminal_v3'))
BINDINGS = frozenset(('expected_revision','expected_account_id','expected_state_sha256',
    'expected_policy_sha256','expected_owner_epoch'))
_METADATA = frozenset(('underlying_type','standard_deliverable_verified',
    'deliverable_verification_source','underlying_activity','contract_activity','open_interest'))


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _source(source_id):
    if source_id not in SOURCE_IDS:
        raise ValueError('Only the prepared Alpaca or Theta data-only source is supported.')
    return source_id


def readiness_view(source_id=SOURCE_ID):
    _source(source_id)
    value= {'schema':1,'source_id':source_id,'status':'prepared_not_activated',
        'live_feed_qualified':False,'paper_submission_ready':False,
        'automatic_collection_enabled':False,'bankroll_created':False,'funding_added':'0',
        'guide':'docs/ALPACA_PAPER_DATA.md','blockers':[
            'owner_key_and_paid_opra_sip_entitlement_required',
            'paid_subscription_phone_flow_not_verified',
            'actual_live_quote_freshness_and_liquidity_not_yet_qualified',
            'independently_dated_activity_and_deliverable_metadata_required',
            'versioned_single_nbbo_message_paper_ledger_required'],
        'scope':'Prepared data-only qualification bridge; no current quotes, paper fills, funds, model calls or broker orders.'}

    if source_id=='theta_terminal_v3':
        value.update(guide='docs/THETA_PAPER_DATA.md',blockers=[
            'owner_free_account_does_not_qualify_live_five_second_quotes',
            'owner_managed_terminal_not_installed_or_verified',
            'loopback_terminal_provenance_and_entitlements_unattested',
            'documented_option_event_timezone_and_consolidation_required',
            'independently_dated_activity_and_deliverable_metadata_required',
            'versioned_single_nbbo_message_paper_ledger_required'])
    return value


def binding(snapshot):
    return {'expected_revision':snapshot['revision'],'expected_account_id':snapshot['account_id'],
        'expected_state_sha256':snapshot['state_sha256'],'expected_policy_sha256':snapshot['policy_sha256'],
        'expected_owner_epoch':deepcopy(snapshot['epoch'])}


def _financial_state(snapshot):
    raw=snapshot.get('state_json')
    if (type(raw) is not str or len(raw.encode())>2*1024*1024
            or hashlib.sha256(raw.encode()).hexdigest()!=snapshot.get('state_sha256')):
        raise ValueError('Current financial state lost its exact retained-byte binding.')
    from scripts.options_market import metadata
    state=metadata(raw.encode())
    if state!=snapshot.get('state'):
        raise ValueError('Financial projection differs from its bound retained bytes.')
    schema=state.get('schema') if type(state) is dict else None
    if type(schema) is not int or schema not in (6,7):
        raise ValueError('Only the existing versioned total-bankroll account is supported.')
    if schema==6:
        from .options_bankroll_eod import validate_state
    else:
        from .options_public_engine import validate_state
    state=validate_state(state)
    if state['contributions']!='25000':
        raise ValueError('The existing total contributions must remain exactly25000; no funds are added.')
    return state


def preflight(snapshot, body, source_id=SOURCE_ID):
    _source(source_id)
    _financial_state(snapshot)
    if type(body) is not dict or set(body) != BINDINGS | {'request_id','requests'}:
        raise ValueError('Use the exact account-bound qualification request fields.')
    gate._identifier(body['request_id'])
    if type(body['expected_revision']) is not int or body['expected_revision'] < 0:
        raise ValueError('Expected revision must be a nonnegative exact integer.')
    for name in ('expected_state_sha256','expected_policy_sha256'):
        value=body[name]
        if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Exact lowercase owner-state SHA-256 bindings are required.')
    gate._identifier(body['expected_account_id'])
    epoch=body['expected_owner_epoch']
    if (type(epoch) is not dict or set(epoch) != {'global_pause_rowid','options_revision','brief_revision'}
            or any(type(epoch[k]) is not int or epoch[k]<0 for k in ('options_revision','brief_revision'))
            or epoch['global_pause_rowid'] is not None and (type(epoch['global_pause_rowid']) is not int or epoch['global_pause_rowid']<0)):
        raise ValueError('Exact current owner epoch bindings are required.')
    if {key:body[key] for key in BINDINGS} != binding(snapshot):
        raise ValueError('The account, policy, current controls or revision changed; review a new binding.')
    if (snapshot['global_paused'] or not snapshot['internet_enabled'] or not snapshot['options_enabled']
            or snapshot['options_paused'] or not snapshot['brief_enabled'] or snapshot['brief_paused']
            or snapshot['brief_hold'] or snapshot['options_hold']):
        raise ValueError('Owner, briefing or options controls are disabled, paused or held; no qualification started.')
    rows=body['requests']
    if type(rows) is not list or not 1<=len(rows)<=16:
        raise ValueError('Use one to sixteen explicit qualification proposals.')
    seen=set()
    for row in rows:
        if type(row) is not dict or set(row)!={'request_id','contract_id','side','quantity','price_limit','allow_partial'}:
            raise ValueError('Use exact explicit request fields; caller decision clocks are refused.')
        gate._request({**row,'source_id':source_id,'snapshot_id':'preflight','decision_at':'2026-11-05T15:00:00+00:00'})
        if row['request_id'] in seen:
            raise ValueError('Duplicate qualification proposal identifiers are refused.')
        seen.add(row['request_id'])
    return deepcopy(rows)


def validate_supplement(supplement, contracts=None):
    if supplement is None:return
    if (type(supplement) is not dict or set(supplement)!={'schema','contracts'}
            or type(supplement['schema']) is not int or supplement['schema']!=1
            or type(supplement['contracts']) is not list or len(supplement['contracts'])>16):
        raise ValueError('Use the bounded schema1 independent metadata supplement.')
    seen=set()
    for row in supplement['contracts']:
        if type(row) is not dict or 'contract_id' not in row or not set(row)-{'contract_id'} or not set(row)-{'contract_id'} <= _METADATA:
            raise ValueError('Supplement accepts only missing independent qualification metadata, never quote fields.')
        contract=row['contract_id']
        if type(contract) is not str or not gate._OCC.fullmatch(contract) or contract in seen or contracts is not None and contract not in contracts:
            raise ValueError('Supplement contracts must uniquely match the collected watchlist.')
        seen.add(contract)
        for key,value in row.items():
            if key=='contract_id':continue
            if key=='underlying_type':gate._text(value)
            elif key=='standard_deliverable_verified':
                if type(value) is not bool:raise ValueError('Deliverable verification must be an explicit boolean.')
            elif key=='deliverable_verification_source':gate._text(value,nullable=True)
            elif value is not None:
                if key=='underlying_activity':
                    gate._object(value,('sessions',))
                    if type(value['sessions']) is not list or len(value['sessions'])>20:
                        raise ValueError('ADV evidence requires at most20dated sessions.')
                    for bar in value['sessions']:
                        gate._object(bar,('session','dollar_volume','published_at'))
                        gate._date(bar['session']);gate._number(bar['dollar_volume'],nullable=True);gate._clock(bar['published_at'],nullable=True)
                elif key=='contract_activity':
                    gate._object(value,('volume','as_of','published_at'))
                    gate._integer(value['volume'],nullable=True);gate._clock(value['as_of'],nullable=True);gate._clock(value['published_at'],nullable=True)
                elif key=='open_interest':
                    gate._object(value,('value','session','published_at'))
                    gate._integer(value['value'],nullable=True);gate._date(value['session'],nullable=True);gate._clock(value['published_at'],nullable=True)


def supplement_books(normalized, supplement):
    books=deepcopy(normalized['books'])
    validate_supplement(supplement,{b['contract_id'] for b in books})
    if supplement is None:
        return books
    if (type(supplement) is not dict or set(supplement)!={'schema','contracts'}
            or type(supplement['schema']) is not int or supplement['schema']!=1
            or type(supplement['contracts']) is not list or len(supplement['contracts'])>16):
        raise ValueError('Use the bounded schema1 independent metadata supplement.')
    lookup={book['contract_id']:book for book in books};seen=set()
    for row in supplement['contracts']:
        if type(row) is not dict or 'contract_id' not in row or not set(row)-{'contract_id'} or not set(row)-{'contract_id'} <= _METADATA:
            raise ValueError('Supplement accepts only missing independent qualification metadata, never quote fields.')
        contract=row['contract_id']
        if type(contract) is not str or contract not in lookup or contract in seen:
            raise ValueError('Supplement contracts must uniquely match collected option identities.')
        seen.add(contract);book=lookup[contract]
        for key,value in row.items():
            if key=='contract_id':continue
            existing=book[key]
            missing=(existing is None or key=='underlying_type' and existing in ('unknown','unverified')
                or key=='standard_deliverable_verified' and existing is False)
            if not missing:
                raise ValueError('Supplement cannot replace provider-supplied or previously known evidence.')
            book[key]=deepcopy(value)
        gate._book(book)
    return books


def verify_clocks(normalized, decided_at):
    # A floored microsecond gate view must never hide a future nanosecond.
    # The provider parser is a pure RFC3339 parser, not a client operation.
    from .alpaca_market_data import _clock as exact
    decision=exact(decided_at)['ns']
    def received(row):
        value=exact(row.get('received_at_raw'))
        if type(row.get('received_at_ns')) is not int or value['ns']!=row['received_at_ns'] or value['iso']!=row.get('received_at'):
            raise ValueError('Actual receipt clock precision or projection changed.')
        if value['ns']>decision:
            raise ValueError('Actual receipt is later than the decision, including nanosecond precision.')
        return value['ns']
    top=received(normalized)
    observations=normalized.get('observations')
    if type(observations) is not list or not 1<=len(observations)<=32:
        raise ValueError('Bounded actual observations are required for clock qualification.')
    for row in observations:
        if type(row) is not dict:raise ValueError('Actual observation clocks are required.')
        receipt=received(row)
        if receipt>top:raise ValueError('Observation receipt is later than capture receipt.')
        if row.get('quote_at_raw') is None:
            if row.get('quote_at_ns') is not None or row.get('quote_at') is not None:
                raise ValueError('Unknown actual event cannot acquire a projected clock.')
            continue
        if row.get('quote_at_ns') is None and row.get('quote_at') is None:
            # Theta preserves an ambiguous local source clock as raw text only.
            continue
        event=exact(row['quote_at_raw'])
        if type(row.get('quote_at_ns')) is not int or event['ns']!=row['quote_at_ns'] or event['iso']!=row.get('quote_at'):
            raise ValueError('Actual event precision or projection changed.')
        if event['ns']>receipt or event['ns']>decision:
            raise ValueError('Actual quote event is in the future, including nanosecond precision.')


def qualify(snapshot, body, normalized, *, supplement=None, decided_at, source_id=SOURCE_ID):
    requests=preflight(snapshot,body,source_id)
    if type(normalized) is not dict:
        raise ValueError('Use structured actual normalization evidence.')
    verify_clocks(normalized,decided_at)
    at=gate._clock(decided_at)
    if type(normalized) is not dict or normalized.get('source_id')!=source_id:
        raise ValueError('Use the actual selected-source normalization contract.')
    books=supplement_books(normalized,supplement)
    if not 1<=len(books)<=16 or len({b['contract_id'] for b in books})!=len(books):
        raise ValueError('Unique bounded collected option books are required.')
    lookup={b['contract_id']:b for b in books}
    rows=[]
    for row in requests:
        if row['contract_id'] not in lookup:
            raise ValueError('Every qualification request must match an actually collected option.')
        book=lookup[row['contract_id']]
        if book['source_id']!=source_id:
            raise ValueError('A quote book cannot change provider identity.')
        rows.append({**row,'source_id':source_id,'snapshot_id':book['snapshot_id'],'decision_at':at.isoformat()})
    result=gate.evaluate_batch(books,rows)
    state=snapshot['state'];position=state.get('position')
    owned=position.get('quantity',0) if position else 0
    buy_reserved=False
    proposals=[]
    with localcontext(Context(prec=60)):
        cash=gate._number(state['cash']);committed=gate._number(state['monthly_committed'])
        # No month reset, proceeds credit, deposit or assumed sale is performed.
        limit=min(Decimal('1000'),cash*Decimal('.04'),cash,max(Decimal(0),Decimal('25000')-committed))
        for request,proposal in zip(requests,result['proposals']):
            proposal=deepcopy(proposal);reasons=list(proposal['reason_codes'])
            quantity=request['quantity'];price=gate._number(proposal['quoted_price'],nullable=True)
            if request['side']=='buy':
                if position is not None or buy_reserved:
                    reasons.append('one_position_limit')
                if price is not None and price*100*quantity+Decimal('1.30')*quantity>limit:
                    reasons.append('entry_cap_or_cash_exceeded')
            elif (position is None or position.get('contract_id')!=request['contract_id'] or quantity>owned):
                reasons.append('sell_exceeds_owned_inventory')
            reasons=list(dict.fromkeys(reasons))
            admitted=not reasons and proposal['proposed_quantity']>0
            if admitted:
                if request['side']=='buy':buy_reserved=True
                else:owned-=proposal['proposed_quantity']
            proposal.update(status='admission_candidate' if admitted else 'blocked',
                reason_codes=reasons,proposed_quantity=proposal['proposed_quantity'] if admitted else 0,
                simulated_admission_only=True,paper_fill=False,execution_confirmed=False,
                provider_authenticated=False,portfolio_controls_checked=False,projected_account_constraints_checked=True)
            proposals.append(proposal)
    blockers=['versioned_'+('alpaca' if source_id==SOURCE_ID else 'theta')+'_paper_ledger_required','retained_financial_history_not_verified_by_this_bridge']
    if source_id=='theta_terminal_v3':blockers.extend(('local_terminal_provenance_unattested','live_terminal_entitlements_unverified'))
    if snapshot['policy']!=snapshot['epoch']:blockers.append('paper_permission_epoch_mismatch_requires_explicit_migration')
    if snapshot.get('qqq_hold'):blockers.append('qqq_workflow_hold_preserved')
    if not all(p['status']=='admission_candidate' for p in proposals):blockers.append('one_or_more_quote_or_portfolio_checks_failed')
    return {'schema':1,'source_id':source_id,'status':'evaluated_evidence_only','request_id':body['request_id'],
        'decided_at':at.isoformat(),'account_binding':binding(snapshot),'account_schema':state['schema'],
        'cash':state['cash'],'contributions':state['contributions'],'bankroll_total':'25000',
        'entry_all_in_limit':format(limit,'f'),'monthly_committed':state['monthly_committed'],
        'automatic_topups':False,'funding_added':'0','bankroll_created':False,
        'paper_submission_ready':False,'paper_fills':0,'ledger_write_performed':False,
        'execution_confirmed':False,'provider_authenticated':False,
        'financial_state_projection_verified':True,'retained_financial_history_verified':False,
        'normalization_authenticates_saved_files':False,'metadata_provider_authenticated':False,
        'quote_clock_basis':'actual_nbbo_message_event_not_individual_side_event',
        'supplement_basis':'Independent owner attestations; not authenticated provider metadata.',
        'liquidity_rule_sha256':gate.RULE_SHA256,'books':books,'proposals':proposals,
        'blockers':blockers,'provider_blockers':deepcopy(normalized.get('blockers',[])),
        'admission_candidates':sum(p['status']=='admission_candidate' for p in proposals),
        'blocked_proposals':sum(p['status']=='blocked' for p in proposals),'model_calls':0,
        'scope':'One bounded current-account admission check only. No durable quote-depth consumption, paper fill, policy adoption, funding, strategy selection or broker execution.'}
