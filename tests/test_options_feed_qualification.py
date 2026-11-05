"""Fictional read-only qualification, no source requests or financial writes."""
from copy import deepcopy
import hashlib
import json
import unittest
from ariadne import options_feed_qualification as bridge
from ariadne import alpaca_market_data as source
from test_options_bankroll_eod import state as state6
from test_options_liquidity import book as complete_book, AT

CONTRACT='KO261120C00080000'
WATCH=[{'symbol':CONTRACT,'type':'OPTION'},{'symbol':'KO','type':'EQUITY'}]


def capture():
    def part(symbol,stock=False):
        row={'t':'2026-11-03T15:00:00.000000123Z','bp':79.85 if stock else 1,
             'ap':79.90 if stock else 1.04,'bs':20,'as':20,'bx':'A','ax':'B','c':['R'] if stock else ' '}
        if stock:row['z']='A'
        return {'payload':json.dumps({'quotes':{symbol:row}}),'received_at':'2026-11-03T15:00:00.050000Z'}
    return {'schema':1,'source_id':source.SOURCE_ID,'received_at':'2026-11-03T15:00:00.050000Z',
        'options':part(CONTRACT),'stocks':part('KO',True),'transport':{
            'source_id':source.SOURCE_ID,'operation':'latest_quotes','provider_authenticated':True,
            'options_feed':'opra','stocks_feed':'sip'}}


def snapshot():
    state=state6();raw=bridge.canonical(state).decode()
    epoch={'global_pause_rowid':31,'options_revision':1,'brief_revision':13}
    return {'revision':4,'account_id':'fictional-shared-account','state':state,'state_json':raw,
        'state_sha256':hashlib.sha256(raw.encode()).hexdigest(),'policy_sha256':'b'*64,
        'epoch':epoch,'policy':deepcopy(epoch),'global_paused':False,'internet_enabled':True,
        'options_enabled':True,'options_paused':False,'brief_enabled':True,'brief_paused':False,
        'brief_hold':False,'options_hold':False,'qqq_hold':False}


def body(owner,**changes):
    row={'request_id':'candidate-one','contract_id':CONTRACT,'side':'buy','quantity':2,
        'price_limit':'1.04','allow_partial':False}
    row.update(changes)
    return {**bridge.binding(owner),'request_id':'fictional-check','requests':[row]}


def supplement():
    b=complete_book()
    fields=('underlying_type','standard_deliverable_verified','deliverable_verification_source',
        'underlying_activity','contract_activity','open_interest')
    return {'schema':1,'contracts':[{'contract_id':CONTRACT,**{k:deepcopy(b[k]) for k in fields}}]}


def evaluate(owner=None,request=None,supp=None,**kwargs):
    owner=snapshot() if owner is None else owner
    return bridge.qualify(owner,body(owner) if request is None else request,
        source.normalize_quotes(capture(),expected_instruments=WATCH),
        supplement=supp,decided_at=kwargs.get('at',AT))


class FeedQualificationTests(unittest.TestCase):
    def test_readiness_is_static_passive_and_never_claims_a_live_feed(self):
        value=bridge.readiness_view()
        self.assertEqual(value['status'],'prepared_not_activated')
        for key in ('live_feed_qualified','paper_submission_ready','bankroll_created','automatic_collection_enabled'):
            self.assertFalse(value[key])
        self.assertEqual(value['funding_added'],'0')

    def test_real_normalizer_missing_classification_can_receive_independent_supplement(self):
        owner=snapshot();before=deepcopy(owner)
        result=evaluate(owner,supp=supplement())
        self.assertEqual(result['status'],'evaluated_evidence_only')
        self.assertEqual(result['admission_candidates'],1)
        self.assertEqual(result['proposals'][0]['status'],'admission_candidate')
        self.assertEqual(result['proposals'][0]['quoted_price'],'1.04')
        self.assertEqual(owner,before)
        self.assertFalse(result['paper_submission_ready']);self.assertEqual(result['paper_fills'],0)
        self.assertFalse(result['provider_authenticated']);self.assertFalse(result['ledger_write_performed'])
        self.assertTrue(result['financial_state_projection_verified']);self.assertFalse(result['retained_financial_history_verified'])
        self.assertEqual(result['entry_all_in_limit'],'1000')
        self.assertIn('per_side_last_change_clocks_not_supplied',result['provider_blockers'][0]['reasons'])

    def test_missing_metadata_is_blocked_not_laundered_into_liquidity(self):
        value=evaluate()
        self.assertEqual(value['admission_candidates'],0)
        self.assertEqual(value['blocked_proposals'],1)
        self.assertIn('standard_deliverable_unverified',value['proposals'][0]['reason_codes'])
        self.assertIn('open_interest_missing',value['proposals'][0]['reason_codes'])

    def test_stale_raw_account_or_projection_fails_preflight(self):
        for change in ('hash','projection','schema'):
            owner=snapshot();request=body(owner)
            if change=='hash':owner['state_sha256']='a'*64
            elif change=='projection':owner['state']['cash']='999999'
            else:
                owner['state']['schema']=99;owner['state_json']=bridge.canonical(owner['state']).decode()
                owner['state_sha256']=bridge.digest(owner['state'])
            with self.assertRaises(ValueError):bridge.preflight(owner,request)

    def test_pause_holds_and_disabled_controls_refuse_before_evaluation(self):
        for key,value in (('global_paused',True),('internet_enabled',False),('options_enabled',False),
                ('options_paused',True),('brief_enabled',False),('brief_paused',True),('brief_hold',True),('options_hold',True)):
            with self.subTest(key=key):
                owner=snapshot();request=body(owner);owner[key]=value
                with self.assertRaises(ValueError):bridge.preflight(owner,request)

    def test_epoch_and_qqq_hold_are_reported_without_adoption_or_clear(self):
        owner=snapshot();owner['policy']['brief_revision']=11;owner['qqq_hold']=True
        before=deepcopy(owner);value=evaluate(owner,supp=supplement())
        self.assertIn('paper_permission_epoch_mismatch_requires_explicit_migration',value['blockers'])
        self.assertIn('qqq_workflow_hold_preserved',value['blockers']);self.assertEqual(owner,before)
        self.assertFalse(value['paper_submission_ready'])

    def test_bool_epoch_and_stale_revision_refused(self):
        owner=snapshot()
        for field in ('bool','revision','duplicate','caller_clock'):
            request=body(owner)
            if field=='bool':request['expected_owner_epoch']['brief_revision']=True
            elif field=='revision':request['expected_revision']=0
            elif field=='duplicate':request['requests']*=2
            else:request['requests'][0]['decision_at']=AT
            with self.assertRaises(ValueError):bridge.preflight(owner,request)

    def test_supplement_cannot_replace_quote_or_clock_fields(self):
        for key,value in (('bid','2'),('quote_at',AT),('received_at',AT),('underlying_quote',{}),('bid_size',999)):
            sup=supplement();sup['contracts'][0][key]=value
            with self.assertRaises(ValueError):evaluate(supp=sup)

    def test_malformed_metadata_contract_and_counts_rejected(self):
        for change in ('unhashable','bool','duplicate','date'):
            sup=supplement()
            if change=='unhashable':sup['contracts'][0]['contract_id']=[]
            elif change=='bool':sup['contracts'][0]['open_interest']['value']=True
            elif change=='duplicate':sup['contracts']*=2
            else:sup['contracts'][0]['open_interest']['published_at']='2026-11-03'
            with self.assertRaises(ValueError):bridge.validate_supplement(sup,{CONTRACT})

    def test_portfolio_entry_fee_cap_and_no_automatic_downsize(self):
        owner=snapshot();value=evaluate(owner,body(owner,quantity=10),supplement())
        self.assertIn('entry_cap_or_cash_exceeded',value['proposals'][0]['reason_codes'])
        self.assertEqual(value['proposals'][0]['proposed_quantity'],0)
        self.assertEqual(owner['state']['cash'],'25000');self.assertEqual(value['funding_added'],'0')

    def test_batch_does_not_propose_multiple_owned_positions(self):
        owner=snapshot();request=body(owner)
        request['requests'].append({**request['requests'][0],'request_id':'second'})
        result=evaluate(owner,request,supplement())
        self.assertEqual([p['status'] for p in result['proposals']],['admission_candidate','blocked'])
        self.assertIn('one_position_limit',result['proposals'][1]['reason_codes'])

    def test_sell_cannot_invent_owned_inventory(self):
        owner=snapshot();value=evaluate(owner,body(owner,side='sell',price_limit='1'),supplement())
        self.assertIn('sell_exceeds_owned_inventory',value['proposals'][0]['reason_codes'])

    def test_fixed_price_and_staleness_never_adjusted(self):
        owner=snapshot();value=evaluate(owner,body(owner,price_limit='1.03'),supplement())
        self.assertIn('price_protection_not_met',value['proposals'][0]['reason_codes'])
        value=evaluate(supp=supplement(),at='2026-11-03T15:00:06Z')
        self.assertIn('quote_stale',value['proposals'][0]['reason_codes'])
