"""Fixed recent KO quotes paired with a separately supplied LOCAL close reference.

Personal retrospective research only. No underlying-provider network access,
paper account changes, date/ticker overrides, quote repair or automatic adoption.
"""
from contextlib import ExitStack
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.options_campaign import _canonical, _root_guard
from scripts.options_market import read, metadata
from scripts.options_universe import SourceLease
from scripts.options_recent_capture import ResearchInternetAuthority

MAX_REQUESTS, MAX_WIRE, MAX_OUTPUT, MAX_SECONDS = 128,16*1024**2,24*1024**2,600
SOURCE_FILES=('scripts/options_reference_capture.py','ariadne/options_reference_history.py',
    'scripts/options_recent_capture.py','scripts/options_campaign.py','scripts/options_market.py',
    'scripts/options_universe.py','ariadne/options_reference_projection.py',
    'ariadne/options_eod_data.py','ariadne/options_root_eod_data.py',
    'ariadne/options_data.py','ariadne/options_archive.py','ariadne/research_window.py',
    'ariadne/qqq_data.py','ariadne/resources/market/America_New_York.tzif','ariadne/briefing.py',
    'ariadne/resources/briefing/America_Chicago.tzif','ariadne/atomic.py','ariadne/tools.py',
    'ariadne/config.py','ariadne/backup.py','ariadne/network_policy.py','ariadne/internet.py')

def parser():
    value=argparse.ArgumentParser(description=__doc__)
    for name in ('reference','reference-metadata','reference-proof','benchmark'):
        value.add_argument('--'+name,required=True,help='Existing box-relative local input; never a URL.')
        value.add_argument('--'+name+'-sha256',required=True,help='Exact lowercase SHA-256 of the local input.')
    value.add_argument('--output',required=True,help='New private staging/... folder, never resumed.')
    return value

def run(args,*,root=ROOT,now=None,_fetch=None,_authority=None):
    from ariadne import options_reference_projection as normalizer,options_root_eod_data as provider,options_eod_data as base
    from ariadne.options_archive import parse_manifest
    from ariadne.options_data import canonical,validate_dataset
    from ariadne.qqq_data import session_close_utc
    from ariadne.atomic import publish_bytes
    from ariadne.research_window import derive_window,require_range,require_archive_manifest
    box=_root_guard(root)
    window=derive_window(now)
    require_range(normalizer.START,normalizer.END,window)
    modules={name:read(box,name,2*1024**2)for name in SOURCE_FILES}
    module_hashes={name:hashlib.sha256(raw).hexdigest()for name,raw in modules.items()}
    sources={}
    for label,maximum in (('reference',2_000_000),('reference_metadata',64*1024),('reference_proof',256*1024),('benchmark',12*1024**2)):
        name,digest=getattr(args,label),getattr(args,label+'_sha256')
        if type(digest)is not str or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Exact lowercase input SHA-256 required.')
        raw=read(box,name,maximum)
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('A registered local input changed: '+label)
        sources[label]={'path':name,'sha256':digest,'raw':raw}
    reference_meta=metadata(sources['reference_metadata']['raw'])
    proof=metadata(sources['reference_proof']['raw'])
    if (type(reference_meta)is not dict or type(proof)is not dict
            or proof.get('status')!='completed_private_reference_qualification'
            or proof.get('sha256')!=sources['reference']['sha256']or proof.get('url')!=reference_meta.get('url')
            or proof.get('received_at')!=reference_meta.get('received_at')
            or reference_meta.get('reconciliation_sha256')!=sources['reference_proof']['sha256']
            or proof.get('overlap_all_equal')is not False):
        raise ValueError('Preserve the exact qualified raw reference and FAILED source-equivalence evidence; no relabeling.')
    reference_preflight=normalizer.validate_reference(sources['reference']['raw'],reference_meta,window)
    reference_closes=reference_preflight['canonical_closes']
    # Resolve all deterministic plan details before reserving an output or
    # opening authority handles. A plan error is not an interrupted capture.
    urls=[provider.chain_url(day.isoformat(),normalizer.expiry_for(day).isoformat())for day in normalizer.DAYS]
    allowed=frozenset(urls)
    if len(urls)!=125 or len(allowed)!=125 or len(urls)>MAX_REQUESTS:
        raise ValueError('The fixed125-session quote plan changed.')
    for url in urls:
        parsed=urlsplit(url)
        if (parsed.scheme!='https'or parsed.hostname!=base.HOST or parsed.port is not None
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError('The fixed historical source identity changed.')
    parts=box._parts(args.output)
    if len(parts)<2 or parts[0]!='staging'or '\\'in args.output:raise ValueError('Use a new canonical private staging/... output.')
    output=box._path(args.output)
    with box._directory(output.parent,create=True):output.mkdir()
    authority=(_authority or ResearchInternetAuthority)(box)
    pins=ExitStack()
    started,used,wire,requests=time.monotonic(),0,0,0
    artifacts,receipts,records={},{},[]
    def check():
        import os
        if os.environ.get('ARIADNE_OFFLINE','').lower()not in ('','0','false','no','off'):
            raise PermissionError('Explicit offline environment; no source acquisition.')
        if time.monotonic()-started>=MAX_SECONDS:raise TimeoutError('Finite research capture exceeded600seconds; no replay.')
        authority.check()
        if time.monotonic()-started>=MAX_SECONDS:raise TimeoutError('Authority check crossed the600second deadline.')
    def publish(name,raw,*,failure=False,retain=False,verify=None):
        nonlocal used
        if used+len(raw)>MAX_OUTPUT-(0 if failure else 128*1024):raise ValueError('Output quota exceeded; no truncation.')
        path=box._path(args.output+'/'+name)
        if path.exists():raise FileExistsError('Never overwrite or replay research artifacts.')
        with box._directory(path.parent,create=True):
            publish_bytes(path,raw,checkpoint=None if failure or retain else(verify or check))
        if read(box,args.output+'/'+name,MAX_OUTPUT)!=raw:raise ValueError('Output readback changed.')
        artifacts[name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        used+=len(raw)
    def retain(ev,item):
        if type(ev)is not dict or set(ev)!=base.EVIDENCE_KEYS or ev['url']!=item['url']:raise ValueError('Exact source response identity required.')
        raw=base64.b64decode(ev['content_base64'],validate=True)
        if len(raw)>base.MAX_RAW or len(raw)!=ev['bytes']or hashlib.sha256(raw).hexdigest()!=ev['sha256']:raise ValueError('Source bytes changed.')
        name='raw/chain-'+str(item['ordinal']).zfill(3)+'.bin'
        publish(name,raw,retain=True)
        item.update({key:ev[key]for key in('received_at','bytes','sha256','http_status')})
        item['raw_path']=name
        return raw
    intent={'schema':1,'kind':'fixed_KO_quotes_independent_local_reference_research',
        'registered_at':window.registered_at,'permitted_window':window.as_dict(),
        'source_dates':[day.isoformat()for day in normalizer.DAYS],
        'coverage':'125sessions May4–Oct29 INSIDE last12months; first half of year options coverage UNVERIFIED, not absent. Oct30 excluded BEFORE capture so both hypothetical availability profiles are historical by registration.',
        'expiry_rule':'Next standard monthly expiry, strictly after source day; holiday preceding session. Exactly one expiry per day, both types/all returned strikes, no future liquidity/outcome screening.',
        'urls':urls,'clock_profiles':list(normalizer.CLOCK_PROFILES),
        'normalization_rule_sha256':normalizer.RULE_SHA256,'module_sha256':module_hashes,
        'inputs':{key:{field:row[field]for field in('path','sha256')}for key,row in sources.items()},
        'reference_assumption':'NEWversioned125session scope projection of independent reported daily close. FAILED whole-source calendar and equality with old minute closes remain FAILED. Full raw retained; outside-cohort anomalies cannot enter financial/context maps. Any in-range bad date remains fatal. Declared8decimal HALF_EVEN only, no cent repair or splicing.',
        'limits':{'GETs':MAX_REQUESTS,'wire_bytes':MAX_WIRE,'output_bytes':MAX_OUTPUT,'seconds':MAX_SECONDS,'per_GET_seconds':15},
        'research_only':True,'execution_qualified':False,'automatic_adoption':False,
        'owner_operations':0,'model_calls':0,'reference_network_requests':0,'retries':0,'redirects':0}
    try:
        publish('intent.json',_canonical(intent),failure=True)
        check();lease=SourceLease(pins,check)
        for name,digest in module_hashes.items():lease.add(box,name,digest,len(modules[name]))
        for key,row in sources.items():
            lease.add(box,row['path'],row['sha256'],len(row['raw']))
            publish({'reference':'reference.csv','reference_metadata':'reference-metadata.json',
                'reference_proof':'reference-proof.json','benchmark':'benchmark.json'}[key],row['raw'])
        for day,url in zip(normalizer.DAYS,urls):
            check()
            if requests>=MAX_REQUESTS or wire>=MAX_WIRE or used+base.MAX_RAW>MAX_OUTPUT-128*1024:
                raise ValueError('Aggregate capture budget exhausted; no next request.')
            requests+=1
            item={'ordinal':requests,'url':url,'started_at':datetime.now(timezone.utc).isoformat()}
            receipts[str(requests)]=item
            budget={'requests':0,'remaining':min(MAX_WIRE-wire,base.MAX_RAW+65536)}
            initial=budget['remaining']
            def approved(value):
                if value not in allowed:raise ValueError('Unregistered quote URL; no general Internet tool.')
                return urlsplit(value)
            try:
                ev=(_fetch or base._fetch_common)(url,deadline=min(started+MAX_SECONDS,time.monotonic()+15),check=check,budget=budget,approved_url=approved)
            except BaseException as error:
                ev=getattr(error,'source_evidence',None)
                if ev is not None:retain(ev,item)
                item['failed_stop']=type(error).__name__+': '+str(error)[:500]
                raise
            finally:wire+=initial-budget['remaining']
            raw=retain(ev,item);check()
            if ev['http_status']!=200:raise ValueError('Non200 quote source response; no retry or alternate expiry.')
            quotes,warning,counts=provider._chain(raw,day,normalizer.expiry_for(day))
            if {row['type']for row in quotes}!={'call','put'}:raise ValueError('Missing actual option type; no compensating request.')
            # Reject duplicate, crossed, impossible or malformed quotes NOW,
            # rather than spending the rest of the source budget first. This
            # does not authenticate the source or create a forward snapshot.
            supplied=base._json(raw)
            if type(supplied[0])is dict and set(supplied[0])=={'warning'}:supplied=supplied[1:]
            checked_quotes=[{**quote,'multiplier':100,'volume':original.get('volume'),
                'open_interest':original.get('open_interest')}for quote,original in zip(quotes,supplied)]
            checked_quotes.sort(key=lambda quote:quote['contract_id'])
            admitted=derive_window(now)
            observed=session_close_utc(day)
            snapshot={'schema':1,'evidence_kind':'historical_quotes','currency':'USD','as_of':admitted.registered_at,
                'source':{'provider':'OnClick single-record admission with independent local research reference',
                    'url':url,'sha256':ev['sha256'],'retrieved_at':ev['received_at'],
                    'usage':'Provisional per-record validation only. Explicit historical clock and independent unknown-basis close assumptions; no paper qualification.',
                    'quote_time_basis':'assumed_eod','independently_verified':False},
                'snapshots':[{'observed_at':observed.isoformat(),'available_at':(observed+timedelta(minutes=15)).isoformat(),
                    'underlying_symbol':'KO','underlying_price':reference_closes[day.isoformat()],'quotes':checked_quotes}]}
            validate_dataset(snapshot,admitted.registered_at,research_symbols=('KO',))
            item.update(quotes=len(quotes),partial_warning=warning,partial_counts=counts)
            records.append({'source_date':day.isoformat(),'received_at':ev['received_at'],'chain_raw':raw,'chain_url':url})
        normalization_window=derive_window(now)
        outputs={}
        for profile in normalizer.CLOCK_PROFILES:
            check()
            dataset,audit=normalizer.normalize(records,stock_raw=sources['reference']['raw'],stock_metadata=reference_meta,
                window=normalization_window,clock_profile=profile)
            raw=canonical(dataset);path=profile+'/dataset.json'
            publish(path,raw);publish(profile+'/audit.json',_canonical(audit))
            manifest={'schema':1,'as_of':dataset['as_of'],'evidence_kind':dataset['evidence_kind'],'symbol':'KO','source':dataset['source'],
                'selection':{'start':normalizer.START,'end':normalizer.END,'max_dte':None,'reason':'Preregistered fixed monthly-expiry actual partial quotes with independent LOCAL close reference; '+profile},
                'limitations':['125sessions INSIDElast12months, not12months options history','Independent reported closes differ from old minute bars; adjustment basis unknown',
                    'NEWscope projection; outside-cohort source calendar anomalies retained as unusable audit only; global source calendar quality is not declared clean',
                    'Eight-decimal declared close canonicalization; raw values and errors retained','Actual quote/publication clocks and100-share deliverables unverified',
                    'Partial strikes and missing required full-size exit quotes remain held; never filled or repaired','Personal retrospective research; no source/candidate/account promotion'],
                'chunks':[{'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'snapshots':len(dataset['snapshots']),
                    'quotes':sum(len(row['quotes'])for row in dataset['snapshots']),
                    'first_observed_at':dataset['snapshots'][0]['observed_at'],'last_observed_at':dataset['snapshots'][-1]['observed_at']}]}
            catalog=_canonical(manifest);parse_manifest(catalog,research_symbols=('KO',));require_archive_manifest(manifest,normalization_window)
            publish(profile+'/catalog.json',catalog)
            outputs[profile]={'catalog_path':profile+'/catalog.json','catalog_sha256':hashlib.sha256(catalog).hexdigest()}
        def verify():
            lease.verify()
            for name,item in artifacts.items():
                raw=read(box,args.output+'/'+name,MAX_OUTPUT)
                if len(raw)!=item['bytes']or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Earlier research artifact changed.')
            check()
        verify()
        receipt={'schema':1,'status':'completed','kind':intent['kind'],'workspace':args.output,
            'intent_sha256':artifacts['intent.json']['sha256'],'artifacts':dict(artifacts),'outputs':outputs,
            'study_window':normalization_window.as_dict(),'requests':receipts,'GETs_attempted':requests,
            'wire_bytes':wire,'seconds':round(time.monotonic()-started,3),'source_stable':True,
            'owner_authority':authority.scope(),'paper_operations':0,'model_calls':0,'automatic_adoption':False,'execution_qualified':False}
        publish('completed.json',_canonical(receipt),verify=verify)
        return receipt
    except BaseException as error:
        publish('failed.json',_canonical({'schema':1,'status':'failed_stopped_no_retry','reason':str(error)[:1000],
            'error_type':type(error).__name__,'GETs_attempted':requests,'wire_bytes':wire,'requests':receipts,
            'artifacts':dict(artifacts),'paper_operations':0,'automatic_adoption':False,'replay_allowed':False}),failure=True)
        raise
    finally:
        try:pins.close()
        finally:authority.close()

def main(argv=None):
    import json
    try:receipt=run(parser().parse_args(argv))
    except (ValueError,OSError,TimeoutError)as error:
        print('Reference research capture stopped: '+str(error),file=sys.stderr);return 2
    print(json.dumps({key:receipt[key]for key in('status','workspace','GETs_attempted','wire_bytes','seconds','outputs','paper_operations')}))
    return 0

if __name__=='__main__':raise SystemExit(main())
