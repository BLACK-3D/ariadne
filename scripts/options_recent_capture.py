"""Finite anonymous KO research capture; never a paper-account operation.

The immutable experiment contains 26 source sessions and one monthly expiry,
inside the owner's rolling twelve-month window. It is NOT twelve months of
history. There are no ticker, URL, date, credential or retry overrides.
"""
from contextlib import ExitStack, closing
from datetime import date, datetime, timezone
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.options_campaign import PauseAuthority, _canonical, _root_guard
from scripts.options_market import read, metadata
from scripts.options_universe import SourceLease

MAX_REQUESTS, MAX_WIRE, MAX_OUTPUT, MAX_SECONDS = 64, 16*1024**2, 24*1024**2, 180
FAILURE_RESERVE = 128*1024
SOURCE_FILES = (
    'scripts/options_recent_capture.py', 'ariadne/options_recent_history.py',
    'ariadne/options_eod_data.py', 'ariadne/options_root_eod_data.py',
    'ariadne/options_data.py', 'ariadne/options_archive.py', 'ariadne/research_window.py',
    'ariadne/qqq_data.py', 'ariadne/resources/market/America_New_York.tzif',
    'ariadne/briefing.py', 'ariadne/resources/briefing/America_Chicago.tzif',
    'ariadne/internet.py', 'scripts/options_campaign.py', 'scripts/options_universe.py',
    'scripts/options_market.py', 'ariadne/atomic.py', 'ariadne/tools.py',
    'ariadne/config.py', 'ariadne/backup.py', 'ariadne/network_policy.py',
)


class ResearchInternetAuthority(PauseAuthority):
    """Existing RO pause authority plus an explicit, unchanged Internet switch."""
    def __init__(self, box):
        super().__init__(box, 'data', False)
        self.internet_epoch = None

    def check(self):
        super().check()
        with closing(self._execute("SELECT rowid,typeof(value),length(CAST(value AS BLOB)),"
                "substr(CAST(value AS BLOB),1,65537) FROM settings WHERE key='internet_access'")) as cursor:
            row = cursor.fetchone()
        if not row or row[1] != 'text' or not 1 <= row[2] <= 65536:
            raise ValueError('Explicit master Internet permission is missing or malformed; no collection.')
        raw = bytes(row[3])
        value = metadata(raw)
        if (type(value) is not dict or value.get('enabled') is not True
                or type(value.get('revision')) is not int or value['revision']<0):
            raise ValueError('Master Internet permission is disabled or invalid; no collection.')
        epoch = row[0], hashlib.sha256(raw).hexdigest()
        if self.internet_epoch is not None and epoch != self.internet_epoch:
            raise ValueError('Master Internet permission changed during the attempt; no replay.')
        self.internet_epoch = epoch

    def scope(self):
        return {**super().scope(), 'internet_permission_pinned': self.internet_epoch is not None}


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--output', required=True, help='New box-relative private staging/... directory.')
    value.add_argument('--benchmark', help='Optional box-relative retained local QQQ/SPY context bundle; copied byte-for-byte.')
    value.add_argument('--benchmark-sha256', help='Exact SHA-256 required with --benchmark; no new benchmark collection.')
    return value


def stock_url(start, end):
    return 'https://api.onclickmedia.com/stock-data/v2/?'+urlencode({
        'ticker':'KO', 'from':start+'-09-30', 'to':end+'-16-00',
        'extended':'false', 'bar_size':'1m'})


def run(args, *, root=ROOT, now=None, _fetch=None, _authority=None):
    from ariadne import options_eod_data as base, options_root_eod_data as provider
    from ariadne import options_recent_history as normalizer
    from ariadne.atomic import publish_bytes
    from ariadne.options_data import canonical
    from ariadne.qqq_data import session_dates as sessions
    from ariadne.research_window import derive_window, require_range

    window = derive_window(now)
    require_range('2026-09-11', '2026-10-16', window)
    days = [day.isoformat() for day in sessions(date(2026,9,11), date(2026,10,16))]
    if len(days) != 26:
        raise ValueError('The fixed cohort no longer contains the registered26 sessions.')
    expiry = '2026-10-16'
    stock_ranges = [('2026-09-11','2026-09-30'), ('2026-10-01','2026-10-16')]
    urls = [stock_url(*pair) for pair in stock_ranges]
    for day in days:
        urls.extend((provider.expiration_url(day), provider.chain_url(day, expiry)))
    allowed_urls = frozenset(urls)
    box = _root_guard(root)
    benchmark_raw=None
    if (args.benchmark is None)!=(args.benchmark_sha256 is None):
        raise ValueError('The local benchmark path and exact hash must be supplied together.')
    if args.benchmark is not None:
        if type(args.benchmark_sha256)is not str or not re.fullmatch('[0-9a-f]{64}',args.benchmark_sha256):
            raise ValueError('Use the exact lowercase local benchmark SHA-256.')
        benchmark_raw=read(box,args.benchmark,12*1024**2)
        if hashlib.sha256(benchmark_raw).hexdigest()!=args.benchmark_sha256:
            raise ValueError('The retained benchmark bytes changed; no acquisition.')
    module_raw = {name:read(box,name,2*1024**2) for name in SOURCE_FILES}
    module_hashes = {name:hashlib.sha256(raw).hexdigest() for name,raw in module_raw.items()}
    parts = box._parts(args.output)
    if len(parts)<2 or parts[0]!='staging' or '\\' in args.output:
        raise ValueError('Use a new canonical private staging/... output.')
    destination = box._path(args.output)
    with box._directory(destination.parent, create=True):
        destination.mkdir()
    authority = (_authority or ResearchInternetAuthority)(box)
    started, total_wire, total_output, request_count = time.monotonic(), 0, 0, 0
    artifacts, receipts, records = {}, [], []
    pins = ExitStack()

    def check():
        if time.monotonic()-started >= MAX_SECONDS:
            raise TimeoutError('The single finite research acquisition exceeded180 seconds; no replay.')
        offline=os.environ.get('ARIADNE_OFFLINE','').lower()
        if offline not in ('','0','false','no','off'):
            raise PermissionError('Explicit offline environment or invalid network setting; no research acquisition.')
        authority.check()
        if time.monotonic()-started >= MAX_SECONDS:
            raise TimeoutError('The single finite research acquisition exceeded180 seconds during its authority check; no replay.')

    def publish(name, raw, *, failure=False, retain_only=False, verify=None):
        nonlocal total_output
        if total_output+len(raw)>MAX_OUTPUT-(0 if failure else FAILURE_RESERVE):
            raise ValueError('Research capture exceeds24MiB; no truncation.')
        target = box._path(args.output+'/'+name)
        if target.exists():
            raise FileExistsError('Research artifacts cannot be overwritten or replayed.')
        with box._directory(target.parent, create=True):
            publish_bytes(target,raw,checkpoint=None if failure or retain_only else (verify or check))
        if read(box,args.output+'/'+name,MAX_OUTPUT)!=raw:
            raise ValueError('Research capture readback changed.')
        artifacts[name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        total_output += len(raw)

    intent = {'schema':1,'kind':'fixed_recent_KO_single_expiry_research_capture',
        'registered_at':window.registered_at,'study_window':window.as_dict(),
        'source_dates':days,'expiry':expiry,'stock_ranges':stock_ranges,
        'requested_urls_in_order':urls,'expected_GETs':len(urls),
        'clock_profiles':['assumed_eod_plus_15m','assumed_next_session_0935'],
        'selection':'Fixed one monthly expiry, both calls and puts exactly as returned, no liquidity or outcome screening. Includes14 pre-October source sessions for causal close warmup.',
        'limitations':['26 sessions inside last12months, not12months captured history',
            'Partial chains remain partial; missing contract exits remain held',
            'Historical event/publication clocks unknown; named counterfactual timing assumptions only',
            'Next0935 sensitivity is not an exact production-clock replica',
            'Provider authenticity, upstream rights and standard100-share deliverables unverified; private educational use only'],
        'source_sha256':module_hashes,
        'benchmark':None if benchmark_raw is None else {'path':args.benchmark,'sha256':args.benchmark_sha256,'copied_path':'benchmark.json'},
        'limits':{'GETs':MAX_REQUESTS,'wire_bytes':MAX_WIRE,'output_bytes':MAX_OUTPUT,'seconds':MAX_SECONDS,'per_GET_seconds':10,'body_bytes':base.MAX_RAW},
        'permission_urls':['https://www.onclickmedia.com/pricing','https://www.onclickmedia.com/About'],
        'retries':0,'redirects':0,'paid_fields':False,'paper_operations':0,
        'automatic_adoption':False,'execution_qualified':False,'model_calls':0}

    def fetch(url):
        nonlocal total_wire, request_count
        check()
        if url not in allowed_urls or request_count>=MAX_REQUESTS or total_wire>=MAX_WIRE:
            raise ValueError('Unregistered URL or aggregate source budget exhausted.')
        if total_output+base.MAX_RAW>MAX_OUTPUT-FAILURE_RESERVE:
            raise ValueError('Cannot reserve space for the next complete raw response and failure receipt; no request.')
        request_count += 1
        item={'ordinal':request_count,'url':url,'started_at':datetime.now(timezone.utc).isoformat()}
        receipts.append(item)
        budget={'requests':0,'remaining':min(base.MAX_RAW+65536,MAX_WIRE-total_wire)}
        before=budget['remaining']
        def approved(value):
            if value not in allowed_urls:
                raise ValueError('Only fixed preregistered public historical URLs are admitted.')
            parsed=urlsplit(value)
            if parsed.scheme!='https' or parsed.hostname!=base.HOST or parsed.port is not None or parsed.username or parsed.password or parsed.fragment:
                raise ValueError('Historical source identity changed.')
            return parsed
        try:
            evidence=(_fetch or base._fetch_common)(url,deadline=min(started+MAX_SECONDS,time.monotonic()+10),
                check=check,budget=budget,approved_url=approved)
        except BaseException as error:
            evidence=getattr(error,'source_evidence',None)
            if evidence is not None:
                save_evidence(evidence,item)
            item['failed_stop']=type(error).__name__+': '+str(error)[:500]
            raise
        finally:
            total_wire += before-budget['remaining']
        save_evidence(evidence,item)
        check()
        if evidence['http_status']!=200:
            raise ValueError('Non200 source response; no retry or alternate path.')
        return base64.b64decode(evidence['content_base64'],validate=True),evidence

    def save_evidence(evidence,item):
        if type(evidence)is not dict or set(evidence)!=base.EVIDENCE_KEYS or evidence['url']!=item['url']:
            raise ValueError('Exact original source evidence fields required.')
        raw=base64.b64decode(evidence['content_base64'],validate=True)
        if len(raw)>base.MAX_RAW or len(raw)!=evidence['bytes'] or hashlib.sha256(raw).hexdigest()!=evidence['sha256']:
            raise ValueError('Source evidence bytes/hash changed.')
        filename='raw/request-'+str(item['ordinal']).zfill(3)+'.bin'
        # Retain an already completed bounded response even if permission was
        # revoked in flight. This writes audit bytes only; the next check stops
        # all further acquisition/normalization and leaves a failure receipt.
        publish(filename,raw,retain_only=True)
        item.update({key:evidence[key] for key in ('url','received_at','sha256','bytes','http_status')})
        item['raw_path']=filename

    try:
        publish('intent.json',_canonical(intent),failure=True)
        check()
        lease=SourceLease(pins,check)
        for name,digest in module_hashes.items():
            lease.add(box,name,digest,len(module_raw[name]))
        if benchmark_raw is not None:
            lease.add(box,args.benchmark,args.benchmark_sha256,len(benchmark_raw))
            publish('benchmark.json',benchmark_raw)
        stocks=[]
        for pair in stock_ranges:
            raw,evidence=fetch(stock_url(*pair))
            actual=base._stock_sessions(raw)
            expected=sessions(*(date.fromisoformat(value) for value in pair))
            if list(actual)!=expected:
                raise ValueError('Stock source dates differ from the fixed full-grid window; no repair.')
            stocks.append((pair,raw,evidence))
        for day in days:
            raw,metadata_receipt=fetch(provider.expiration_url(day))
            available,_=base._expirations(raw,base._day(day),received_at=metadata_receipt['received_at'])
            if expiry not in available['call'] or expiry not in available['put']:
                raise ValueError('The fixed expiry is absent from either actual metadata list; no replacement.')
            chain,chain_receipt=fetch(provider.chain_url(day,expiry))
            quotes,warning,counts=provider._chain(chain,base._day(day),base._day(expiry))
            if {quote['type']for quote in quotes}!={'call','put'}:
                raise ValueError('Both option types must be supplied; no compensating request.')
            receipts[-1].update(partial_chain=warning is not None,coverage=counts,quotes=len(quotes))
            pair,stock,stock_receipt=next(row for row in stocks if row[0][0]<=day<=row[0][1])
            records.append({'source_date':day,'received_at':chain_receipt['received_at'],
                'chain_raw':chain,'chain_url':chain_receipt['url'],'stock_raw':stock,
                'stock_received_at':stock_receipt['received_at'],'stock_url':stock_receipt['url']})
        # Retrieval clocks must precede normalization registration. The earlier
        # acquisition intent freezes scope; it cannot backdate captured evidence.
        normalization_window=derive_window(now)
        require_range(days[0],days[-1],normalization_window)
        outputs={}
        for profile in intent['clock_profiles']:
            check()
            dataset,audit=normalizer.normalize(records,window=normalization_window,clock_profile=profile)
            raw=canonical(dataset)
            path=profile+'/dataset.json'
            publish(path,raw)
            publish(profile+'/audit.json',_canonical(audit))
            manifest={'schema':1,'as_of':dataset['as_of'],'evidence_kind':dataset['evidence_kind'],
                'symbol':'KO','source':dataset['source'],
                'selection':{'start':days[0],'end':days[-1],'max_dte':None,
                    'reason':'Preregistered26session singleOct16expiry cohort; no outcome/size/continuity filtering. '+profile},
                'limitations':intent['limitations'],
                'chunks':[{'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                    'snapshots':len(dataset['snapshots']),'quotes':sum(len(row['quotes'])for row in dataset['snapshots']),
                    'first_observed_at':dataset['snapshots'][0]['observed_at'],
                    'last_observed_at':dataset['snapshots'][-1]['observed_at']}]}
            from ariadne.options_archive import parse_manifest
            from ariadne.research_window import require_archive_manifest
            manifest_raw=_canonical(manifest)
            parse_manifest(manifest_raw,research_symbols=('KO',))
            require_archive_manifest(manifest,normalization_window)
            publish(profile+'/catalog.json',manifest_raw)
            outputs[profile]={'catalog_path':profile+'/catalog.json','catalog_sha256':hashlib.sha256(manifest_raw).hexdigest()}
        def verify():
            lease.verify()
            for name,item in artifacts.items():
                raw=read(box,args.output+'/'+name,MAX_OUTPUT)
                if len(raw)!=item['bytes']or hashlib.sha256(raw).hexdigest()!=item['sha256']:
                    raise ValueError('An earlier capture artifact changed before completion.')
            check()
        verify()
        receipt={'schema':1,'status':'completed','kind':intent['kind'],
            'intent_sha256':artifacts['intent.json']['sha256'],'artifacts':dict(artifacts),
            'study_window':normalization_window.as_dict(),'acquisition_window':window.as_dict(),
            'workspace':args.output,
            'outputs':outputs,'requests':receipts,
            'GETs_attempted':request_count,'wire_bytes':total_wire,
            'seconds':round(time.monotonic()-started,3),'source_stable':True,
            'owner_authority':authority.scope(),'paper_operations':0,'model_calls':0,
            'automatic_adoption':False,'execution_qualified':False,'replay_allowed':False}
        publish('completed.json',_canonical(receipt),verify=verify)
        return receipt
    except BaseException as error:
        publish('failed.json',_canonical({'schema':1,'status':'failed_stopped_no_retry',
            'error_type':type(error).__name__,'reason':str(error)[:1000],
            'requests':receipts,'GETs_attempted':request_count,'wire_bytes':total_wire,
            'artifacts':dict(artifacts),'replay_allowed':False,'paper_operations':0,
            'automatic_adoption':False,'execution_qualified':False}),failure=True)
        raise
    finally:
        try: pins.close()
        finally: authority.close()


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        result=run(args)
    except (ValueError,OSError,TimeoutError)as error:
        print('Recent-history capture stopped: '+str(error),file=sys.stderr)
        return 2
    print(json.dumps({key:result[key]for key in ('status','workspace','GETs_attempted','wire_bytes','seconds','paper_operations','outputs')}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
