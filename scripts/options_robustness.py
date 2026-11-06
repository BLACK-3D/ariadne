"""Finite offline robustness study of the existing matched KO observations.

Every path is a separate research counterfactual. No collector, model, account
writer, source repair, paper promotion or new independent holdout is implied.
"""
from __future__ import annotations
from contextlib import ExitStack
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.options_campaign import PauseAuthority, _canonical, _root_guard, install_offline_guards
from scripts.options_market import guard_for, read
from scripts.options_universe import SourceLease
from scripts import options_experiments as prior

MAX_SECONDS=600
MAX_OUTPUT=32*1024*1024
FAILURE_RESERVE=64*1024
HASH=re.compile(r'[a-f0-9]{64}\Z')
SCENARIO_MAP={'recorded_base':'recorded_base','adverse_cent':'modeled_adverse_cent',
              'half_depth':'modeled_half_depth','combined':'modeled_combined'}
SOURCE_FILES=tuple(dict.fromkeys((*prior.SOURCE_FILES,
 'scripts/options_robustness.py','ariadne/options_robustness.py')))


def sha(raw):return hashlib.sha256(raw).hexdigest()


def digest(value):
    if type(value) is not str or HASH.fullmatch(value) is None:
        raise ValueError('An exact lowercase SHA-256 is required.')
    return value


def strict_json(raw):
    from scripts.options_market import metadata
    return metadata(raw)


def inventory(guard,receipt_raw,expected_hash,kind,maximum):
    """Validate metadata only; price bodies remain unopened until registration."""
    if sha(receipt_raw)!=digest(expected_hash):raise ValueError('Completed source receipt changed.')
    value=strict_json(receipt_raw)
    if (type(value) is not dict or type(value.get('schema')) is not int or value['schema']!=1
            or value.get('status')!='completed' or value.get('kind')!=kind
            or value.get('source_stable') is not True or value.get('automatic_adoption') is not False
            or value.get('execution_qualified') is not False
            or type(value.get('paper_operations')) is not int or value['paper_operations']!=0
            or type(value.get('model_calls')) is not int or value['model_calls']!=0 or type(value.get('artifacts')) is not dict
            or not 1<=len(value['artifacts'])<=256):
        raise ValueError('A completed, non-adopting research source is required.')
    total=0;seen=set()
    for name,item in value['artifacts'].items():
        guard._parts(name)
        if ('\\' in name or name.casefold() in seen or type(item) is not dict
                or set(item)!={'bytes','sha256'} or type(item['bytes']) is not int
                or not 1<=item['bytes']<=maximum):
            raise ValueError('Ambiguous or oversized research artifact.')
        seen.add(name.casefold());digest(item['sha256']);total+=item['bytes']
    if total>maximum:raise ValueError('Research source inventory exceeds its bound.')
    return value


def validate_intent(raw,expected_hash):
    from ariadne import options_robustness as audit, options_experiments as engine
    intent=strict_json(raw)
    if (sha(raw)!=digest(expected_hash) or type(intent) is not dict
            or type(intent.get('schema')) is not int or intent['schema']!=1 or intent.get('kind')!='finite_options_robustness_intent'
            or _canonical(intent.get('configurations'))!=_canonical(engine.configs()) or intent.get('paths')!=712
            or type(intent.get('paths')) is not int
            or type(intent.get('max_seconds')) is not int or intent['max_seconds']!=MAX_SECONDS
            or type(intent.get('max_output_bytes')) is not int or intent['max_output_bytes']!=MAX_OUTPUT
            or type(intent.get('scenarios')) is not dict or set(intent['scenarios'])!=set(SCENARIO_MAP)
            or any(type(text) is not str or not text.strip() for text in intent['scenarios'].values())
            or set(audit.SCENARIOS)!=set(SCENARIO_MAP.values())
            or intent.get('engine_rule_sha256')!=engine.RULE_SHA256
            or intent.get('clock_profiles')!=list(prior.PROFILES)):
        raise ValueError('The predeclared finite research intent changed.')
    return intent


def witness_identity(profile,scenario,config):
    from ariadne import options_experiments as engine
    if (profile not in prior.PROFILES or scenario not in SCENARIO_MAP.values()
            or type(config) is not dict or _canonical(config) not in {_canonical(item) for item in engine.configs()}):
        raise ValueError('Unregistered witness identity.')
    return config['id']


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-workspace',default=prior.DEFAULT_WORKSPACE)
    p.add_argument('--source-completed-sha256',required=True)
    p.add_argument('--prior-workspace',default='staging/experimental_options_2026_11_01/causal-v1')
    p.add_argument('--prior-completed-sha256',required=True)
    p.add_argument('--intent',required=True)
    p.add_argument('--intent-sha256',required=True)
    p.add_argument('--owner-data',default='data')
    p.add_argument('--output',required=True)
    return p


def run(args,*,root=ROOT,now=None,_authority=None):
    from ariadne import options_robustness as audit, options_experiments as engine
    from ariadne.atomic import publish_bytes
    from ariadne.options_archive import load_manifest,iter_archive
    from ariadne.options_regime import build_timeline
    from ariadne.qqq_data import session_dates,exchange_day,session_close_utc
    from ariadne.research_window import derive_window,require_archive_manifest,require_option_dataset

    box=_root_guard(root);started=time.monotonic();window=derive_window(now)
    parts=box._parts(args.output)
    if len(parts)<2 or parts[0]!='staging' or '\\' in args.output:
        raise ValueError('Use a new private staging/... study folder.')
    for name in ('source_completed_sha256','prior_completed_sha256','intent_sha256'):
        digest(getattr(args,name))
    source=guard_for(args.source_workspace,root=box.root)
    baseline=guard_for(args.prior_workspace,root=box.root)
    source_raw=read(source,'completed.json',256*1024)
    source_receipt=inventory(source,source_raw,args.source_completed_sha256,
        'fixed_KO_historical_nullable_sizes_reference_research',24*1024*1024)
    prior_raw=read(baseline,'completed.json',256*1024)
    prior_receipt=inventory(baseline,prior_raw,args.prior_completed_sha256,
        'fixed_KO_causal_experiments',32*1024*1024)
    if (type(source_receipt.get('source_records')) is not int or source_receipt['source_records']!=125
            or type(prior_receipt.get('configuration_count')) is not int or prior_receipt['configuration_count']!=89
            or prior_receipt.get('clock_profiles')!=list(prior.PROFILES)):
        raise ValueError('The fixed comparison cohort or prior grid changed.')
    module_raw={name:read(box,name,2*1024*1024) for name in SOURCE_FILES}
    module_hashes={name:sha(body) for name,body in module_raw.items()}
    for name in ('ariadne/options_experiments.py','ariadne/options_experiment_signals.py'):
        item=prior_receipt['artifacts'].get('source_snapshot/'+name.replace('/','_'))
        if item is None or item['sha256']!=module_hashes[name]:
            raise ValueError('Frozen baseline engine/signals differ from the current source.')
    intent_raw=read(box,args.intent,256*1024)
    intent=validate_intent(intent_raw,args.intent_sha256)
    manifests={}
    for profile in prior.PROFILES:
        declared=source_receipt['outputs'][profile]
        name=declared['catalog_path'];item=source_receipt['artifacts'].get(name)
        if item is None or item['sha256']!=declared['catalog_sha256']:
            raise ValueError('Captured profile catalog changed.')
        raw,value=load_manifest(source,name,expected_manifest_sha256=item['sha256'],research_symbols=('KO',))
        require_archive_manifest(value,window)
        if (value['symbol']!='KO' or (value['selection']['start'],value['selection']['end'])!=(prior.START,prior.END)):
            raise ValueError('The fixed observed cohort cannot be clipped or expanded silently.')
        for chunk in value['chunks']:
            if source_receipt['artifacts'].get(chunk['path'])!={'bytes':chunk['bytes'],'sha256':chunk['sha256']}:
                raise ValueError('Archive chunk differs from capture evidence.')
        manifests[profile]=(name,item['sha256'])

    destination=box._path(args.output)
    with box._directory(destination.parent,create=True):destination.mkdir()
    authority=(_authority or PauseAuthority)(box,args.owner_data,False)
    held=ExitStack();written={};used=0;phase='registration';baseline_matches=0

    def check():
        if time.monotonic()-started>=MAX_SECONDS:raise TimeoutError('Finite study exceeded600seconds; preserve the receipt.')
        authority.check()
        if time.monotonic()-started>=MAX_SECONDS:raise TimeoutError('Owner check crossed the study deadline.')

    def publish(name,payload,*,failure=False):
        nonlocal used
        if type(payload) is not bytes or used+len(payload)>MAX_OUTPUT-(0 if failure else FAILURE_RESERVE):
            raise ValueError('Study output exceeds32MiB; no case or witness may be truncated.')
        path=box._path(args.output+'/'+name)
        with box._directory(path.parent,create=True):publish_bytes(path,payload,checkpoint=None if failure else check)
        if read(box,args.output+'/'+name,len(payload))!=payload:raise ValueError('Published research bytes changed.')
        used+=len(payload);written[name]={'bytes':len(payload),'sha256':sha(payload)}

    def verify():
        check();lease.verify()
        for name,item in written.items():
            raw=read(box,args.output+'/'+name,item['bytes'])
            if len(raw)!=item['bytes'] or sha(raw)!=item['sha256']:
                raise ValueError('A previously published research witness changed.')

    try:
        publish('attempt.json',_canonical({'schema':1,'registered_at':window.registered_at,
            'replay_allowed':False,'paper_operations':0}),failure=True)
        check();lease=SourceLease(held,check)
        for name,body in module_raw.items():lease.add(box,name,module_hashes[name],len(body))
        lease.add(box,args.intent,args.intent_sha256,len(intent_raw))
        for guard,raw,receipt,expected in ((source,source_raw,source_receipt,args.source_completed_sha256),
                                        (baseline,prior_raw,prior_receipt,args.prior_completed_sha256)):
            lease.add(guard,'completed.json',expected,len(raw))
            for name,item in receipt['artifacts'].items():lease.add(guard,name,item['sha256'],item['bytes'])
        registration={'schema':1,'kind':'pre_outcome_KO_robustness_registration','registered_at':window.registered_at,
            'study_window':window.as_dict(),'intent_path':args.intent,'intent_sha256':args.intent_sha256,
            'configs':engine.configs(),'clock_profiles':list(prior.PROFILES),'path_count':712,
            'intent_to_module_scenario_ids':SCENARIO_MAP,
            'intent_evidence_screen':'Module adequacy screen AND positive_in_every_case; neither proves edge.',
            'rules':audit.RULE,'rules_sha256':audit.RULE_SHA256,'engine_rule_sha256':engine.RULE_SHA256,
            'module_sha256':module_hashes,'source_workspace':args.source_workspace,
            'source_completed_sha256':args.source_completed_sha256,'source_inventory':source_receipt['artifacts'],
            'prior_workspace':args.prior_workspace,'prior_completed_sha256':args.prior_completed_sha256,
            'prior_inventory':prior_receipt['artifacts'],'owner_authority':authority.scope(),
            'limits':{'seconds':MAX_SECONDS,'output_bytes':MAX_OUTPUT,'paths':712},
            'previously_seen':True,'independent_holdout':False,'automatic_adoption':False,
            'network_calls':0,'model_calls':0,'paper_operations':0}
        publish('registration.json',_canonical(registration));verify()
        publish('study-intent.json',intent_raw)
        phase='source_iteration'
        expected=session_dates(date.fromisoformat(prior.START),date.fromisoformat(prior.END))
        data={};decision={};fill={};context_audit={}
        benchmark_item=source_receipt['artifacts']['benchmark.json']
        timeline=build_timeline(strict_json(read(source,'benchmark.json',benchmark_item['bytes'])),
            clock_profile='assumed_eod_plus_15m',checkpoint=check)
        for profile in prior.PROFILES:
            name,expected_hash=manifests[profile];rows=[]
            for chunk in iter_archive(source,name,expected_manifest_sha256=expected_hash,checkpoint=check,research_symbols=('KO',)):
                require_option_dataset(chunk,window);rows.extend(chunk['snapshots'])
                if len(rows)>125:raise ValueError('Source-frame bound changed.')
            if len(rows)!=125 or [exchange_day(row['observed_at']) for row in rows]!=expected:
                raise ValueError('All125 fixed source sessions must be accounted for.')
            decision[profile]=[];fill[profile]=[];context_audit[profile]={'decision':[],'fill':[]}
            for day,row in zip(expected,rows):
                check();observed=session_close_utc(day)
                available=(observed+timedelta(minutes=15) if profile==prior.PROFILES[0] else
                    prior._session_open(session_dates(day+timedelta(days=1),day+timedelta(days=8))[0])+timedelta(minutes=5))
                if datetime.fromisoformat(row['observed_at'])!=observed or datetime.fromisoformat(row['available_at'])!=available:
                    raise ValueError('The captured hypothetical source/availability convention changed.')
                opened=prior._session_open(day);dc=timeline.at(row['available_at']);fc=timeline.at(opened)
                if fc.get('status')=='ready' and (fc.get('source_session') is None or fc['source_session']>=day.isoformat()
                        or fc.get('available_at') is None or datetime.fromisoformat(fc['available_at'])>opened):
                    raise ValueError('Future context cannot authorize a fill.')
                decision[profile].append(dc);fill[profile].append(fc)
                context_audit[profile]['decision'].append({'query_at':row['available_at'],'context':dc})
                context_audit[profile]['fill'].append({'query_at':opened.isoformat(),'context':fc})
            data[profile]=rows
        for a,b in zip(data[prior.PROFILES[0]],data[prior.PROFILES[1]]):
            if {k:v for k,v in a.items() if k!='available_at'}!={k:v for k,v in b.items() if k!='available_at'}:
                raise ValueError('Both clocks must use identical economic observations.')
        contexts={'schema':1,'benchmark':timeline.describe(),'profiles':context_audit}
        old_context=read(baseline,'contexts.json',prior_receipt['artifacts']['contexts.json']['bytes'])
        if _canonical(contexts)!=old_context:raise ValueError('Reconstructed context differs from the frozen baseline.')
        publish('contexts.json',_canonical(contexts))
        old_full=strict_json(read(baseline,'full-results.json',prior_receipt['artifacts']['full-results.json']['bytes']))
        old_hashes={profile:{report['config']['id']:sha(_canonical(report)) for report in old_full['results'][profile]}
                    for profile in prior.PROFILES}
        if any(len(index)!=89 for index in old_hashes.values()):raise ValueError('Prior baseline case set changed.')
        row_hashes={profile:sha(_canonical(rows)) for profile,rows in data.items()}
        phase='robustness_paths'

        def sink(profile,scenario,config,report):
            nonlocal baseline_matches
            check()
            identifier=witness_identity(profile,scenario,config)
            encoded=_canonical(report)
            if scenario=='recorded_base':
                if sha(encoded)!=old_hashes[profile].get(identifier):
                    raise ValueError('Recorded-base path differs from the frozen prior result.')
                baseline_matches+=1
            publish('cases/'+profile+'/'+scenario+'/'+identifier+'.json',encoded)

        result=audit.audit(data,decision_contexts_by_profile=decision,fill_contexts_by_profile=fill,
            checkpoint=check,report_sink=sink)
        if baseline_matches!=178:raise ValueError('All178 recorded baseline paths must match.')
        if any(sha(_canonical(rows))!=row_hashes[profile] for profile,rows in data.items()):
            raise ValueError('Research mutated an original input view.')
        result['registered_source_sha256']=row_hashes
        result['baseline_reports_exact_matches']=baseline_matches
        result['intent_screen_ids']=[item['config']['id'] for item in result['aggregates']
            if item['evidence_screen_passed'] and item['positive_in_every_case']]
        result['intent_to_module_scenario_ids']=SCENARIO_MAP
        result['registration_sha256']=written['registration.json']['sha256']
        publish('results.json',_canonical(result));phase='verification';verify()
        receipt={'schema':1,'status':'completed','kind':'fixed_KO_robustness_audit','registered_at':window.registered_at,
            'study_window':window.as_dict(),'registration_sha256':written['registration.json']['sha256'],
            'artifacts':dict(written),'source_stable':True,'source_streams_exhausted':True,
            'configuration_count':89,'scenario_count':4,'clock_profile_count':2,'path_count':712,
            'baseline_reports_exact_matches':baseline_matches,'seconds':round(time.monotonic()-started,3),
            'output_bytes':used,'previously_seen':True,'independent_holdout':False,'automatic_adoption':False,
            'execution_qualified':False,'paper_operations':0,'model_calls':0,'network_calls':0,'replay_allowed':False}
        publish('completed.json',_canonical(receipt))
        return receipt
    except BaseException as error:
        try:
            publish('failed.json',_canonical({'schema':1,'status':'failed_or_interrupted','phase':phase,
                'exception_text_retained':False,'automatic_retry':False,'paper_operations':0,'model_calls':0,
                'network_calls':0,'partial_artifacts':dict(written),'replay_allowed':False}),failure=True)
        except BaseException:pass
        raise ValueError('Robustness study held. Preserve the failed receipt; no automatic replay.') from None
    finally:
        held.close();authority.close()


def main(argv=None):
    install_offline_guards()
    try:receipt=run(parser().parse_args(argv))
    except Exception as error:
        print(str(error),file=sys.stderr);return 2
    print(json.dumps({k:v for k,v in receipt.items() if k!='artifacts'},ensure_ascii=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
