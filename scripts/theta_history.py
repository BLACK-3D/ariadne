"""Free Theta EOD capture/import with finite research budgets; never trading."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import alpaca_market as paths
from scripts.options_market import read,metadata
from ariadne import theta_history as source
from ariadne.atomic import publish_bytes
from ariadne.locking import InstanceLock
from ariadne.research_window import derive_window
MAX_ATTEMPTS,MAX_REQUESTS,MAX_RESERVED_WIRE=4,256,64*1024*1024
MAX_OUTPUT=64*1024*1024
SOURCE_FILES=('scripts/theta_history.py','ariadne/theta_history.py','ariadne/theta_market_data.py',
    'scripts/alpaca_market.py','scripts/options_market.py','ariadne/research_window.py',
    'ariadne/qqq_data.py','ariadne/locking.py','ariadne/atomic.py','ariadne/tools.py',
    'ariadne/config.py','ariadne/network_policy.py')


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('status','capture','import'))
    p.add_argument('--data-dir',default='data')
    p.add_argument('--workspace',default='data/workspace')
    p.add_argument('--online',action='store_true')
    p.add_argument('--manifest',help='Workspace-relative explicit schema1 request manifest.')
    p.add_argument('--manifest-sha256')
    p.add_argument('--capture',help='Workspace-relative saved capture; import only.')
    p.add_argument('--capture-sha256')
    p.add_argument('--output',help='NEW reports/theta-history-<identifier> directory.')
    return p


def _hash(value):
    if type(value) is not str or not re.fullmatch(r'[a-f0-9]{64}',value):
        raise ValueError('An exact lowercase SHA-256 is required.')
    return value


def _output(workspace,name):
    if type(name) is not str or not re.fullmatch(r'reports/theta-history-[A-Za-z0-9][A-Za-z0-9._-]{0,95}',name):
        raise ValueError('Use a NEW reports/theta-history-<identifier> directory.')
    p=workspace._path(name)
    if p.exists():raise FileExistsError('Prior or incomplete output exists; inspect it instead of replaying.')
    return p


def _pins():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def _control(data,*,online):
    # Reuse guarded read-only owner access, never initialize owner tables.
    if online:return paths._network_allowed(data)
    with data._directory(data.root),closing(paths._readonly(data)) as db:
        values=paths._controls(db)
        if values['paused'] is not None and values['paused']['value'] is True:
            raise ValueError('Global autonomy is paused; no import was started.')
        return hashlib.sha256(source.canonical(values['paused'])).hexdigest()


def _journal(data,*,create=False):
    path=data._path('theta-history.sqlite3')
    if not path.exists() and not create:return None
    for suffix in ('','-wal','-shm','-journal'):
        candidate=data._path('theta-history.sqlite3'+suffix)
        if candidate.exists() and (not candidate.is_file() or candidate.stat().st_nlink!=1 or candidate.stat().st_size>1024*1024):
            raise ValueError('Research journal path or size is unsupported; no reset is permitted.')
    db=sqlite3.connect(path if create else path.as_uri()+'?mode=ro',uri=not create,timeout=5)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA trusted_schema=OFF')
    if create:
        db.execute('CREATE TABLE IF NOT EXISTS theta_history_attempts(id INTEGER PRIMARY KEY,manifest_sha256 TEXT UNIQUE NOT NULL,output TEXT UNIQUE NOT NULL,status TEXT NOT NULL,reserved_requests INTEGER NOT NULL,reserved_wire INTEGER NOT NULL,created_at TEXT NOT NULL,receipt_sha256 TEXT)')
        db.commit()
    else:db.execute('PRAGMA query_only=ON')
    return db


def _rows(db):
    values=[] if db is None else [dict(r) for r in db.execute('SELECT * FROM theta_history_attempts ORDER BY id LIMIT 5')]
    if len(values)>MAX_ATTEMPTS:raise ValueError('Research journal exceeds its fixed attempt bound.')
    for row in values:
        if (set(row)!={'id','manifest_sha256','output','status','reserved_requests','reserved_wire','created_at','receipt_sha256'}
                or type(row['id']) is not int or row['id']<1 or row['status'] not in ('running','completed','failed')
                or type(row['reserved_requests']) is not int or not 1<=row['reserved_requests']<=source.MAX_REQUESTS
                or row['reserved_wire']!=source.MAX_TOTAL_BYTES):
            raise ValueError('Research journal is invalid; no quota reset or repair is permitted.')
        _hash(row['manifest_sha256'])
    return values


def _inspect_previous(workspace,rows):
    for row in rows:
        if row['status']!='completed':raise ValueError('A failed or interrupted historical attempt requires review; it will not be replayed.')
        raw=read(workspace,row['output']+'/completed.json',256*1024)
        if hashlib.sha256(raw).hexdigest()!=row['receipt_sha256']:
            raise ValueError('Prior historical receipt changed or is missing; collection is held.')
        value=metadata(raw)
        if value.get('status')!='completed' or value.get('manifest_sha256')!=row['manifest_sha256']:
            raise ValueError('Prior historical receipt does not match its journal.')
        for item in value['artifacts']:
            body=read(workspace,row['output']+'/'+item['path'],MAX_OUTPUT)
            if len(body)!=item['bytes'] or hashlib.sha256(body).hexdigest()!=item['sha256']:
                raise ValueError('Prior historical artifacts changed; collection is held.')


def _completed_input(data,workspace,name,expected,length):
    if type(name) is not str or not name.endswith('/capture.json'):
        raise ValueError('Import requires a capture.json with its original completed.json receipt.')
    folder=name[:-len('/capture.json')]
    completion=read(workspace,folder+'/completed.json',256*1024)
    value=metadata(completion)
    if (type(value) is not dict or type(value.get('schema')) is not int or value['schema']!=1 or value.get('status')!='completed'
            or value.get('source_id')!=source.SOURCE_ID or value.get('kind')!='free_eod_capture'
            or type(value.get('artifacts')) is not list or not 1<=len(value['artifacts'])<=5):
        raise ValueError('Only a completed historical capture may enter an offline import.')
    captures=[r for r in value['artifacts'] if type(r) is dict and r.get('path')=='capture.json']
    if len(captures)!=1 or captures[0]!={'path':'capture.json','sha256':expected,'bytes':length}:
        raise ValueError('The historical completion proof does not bind these exact capture bytes.')
    with data._directory(data.root):
        db=_journal(data)
        try:rows=_rows(db)
        finally:
            if db is not None:db.close()
    for row in rows:
        if row['output']==folder and (row['status']!='completed' or row['receipt_sha256']!=hashlib.sha256(completion).hexdigest()):
            raise ValueError('The local source attempt is failed, interrupted or uncommitted; no import is allowed.')
    return name[:-len('capture.json')]+'completed.json',hashlib.sha256(completion).hexdigest()


def _publish(workspace,folder,files,receipt,check=None):
    from ariadne.tools import _linked
    destination=workspace._path(folder)
    allowed={'capture.json','normalized.json','selection.json','partial-capture.json','source.json','completed.json','failed.json'}
    with workspace._directory(destination):
        existing=list(destination.iterdir())
        if len(existing)>8:raise ValueError('Historical output reservation has unsupported members.')
        size=0
        for member in existing:
            if member.name not in allowed:raise ValueError('Historical output reservation has unsupported members.')
            path=workspace._path(folder+'/'+member.name);info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or _linked(info):
                raise ValueError('Historical output members must be ordinary single-link files.')
            size+=info.st_size
        if size+sum(len(raw) for raw in files.values())>MAX_OUTPUT-256*1024:
            raise ValueError('Historical output budget exceeded; failure receipt reserve retained.')
        items=[]
        for name,raw in files.items():
            if name not in allowed-{'completed.json','failed.json'}:raise ValueError('Unsupported historical artifact name.')
            if check:check()
            publish_bytes(destination/name,raw,checkpoint=check)
            if read(workspace,folder+'/'+name,MAX_OUTPUT)!=raw:raise ValueError('Historical artifact readback failed.')
            items.append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        receipt={**receipt,'artifacts':items};raw=source.canonical(receipt)
        if len(raw)>256*1024:raise ValueError('Historical completion receipt exceeds its fixed bound.')
        if check:check()
        publish_bytes(destination/('completed.json' if receipt['status']=='completed' else 'failed.json'),raw,checkpoint=check)
        return receipt,hashlib.sha256(raw).hexdigest()


def run(args,*,root=ROOT,client_factory=None,now=None):
    if args.online and os.environ.get('ARIADNE_OFFLINE') not in (None,'0'):
        raise ValueError('Inherited offline policy refuses upstream access, including a loopback proxy.')
    data,workspace=paths._guards(args,root)
    supplied=(args.manifest,args.manifest_sha256,args.capture,args.capture_sha256,args.output)
    if args.action=='status':
        if args.online or any(v is not None for v in supplied):raise ValueError('Status accepts no online or source overrides.')
        with data._directory(data.root):
            db=_journal(data)
            try:rows=_rows(db)
            finally:
                if db is not None:db.close()
        return {'schema':1,'source_id':source.SOURCE_ID,'mode':'research_only','attempts':len(rows),
            'held':any(r['status']!='completed' for r in rows),'artifact_integrity_checked':False,'reserved_requests':sum(r['reserved_requests'] for r in rows),
            'request_limit':MAX_REQUESTS,'reserved_wire_bytes':sum(r['reserved_wire'] for r in rows),
            'wire_reservation_limit':MAX_RESERVED_WIRE,'network_requests':0,'paper_fills':0,'funding_added':'0'}
    destination=_output(workspace,args.output)
    online=args.action=='capture'
    if online and (not args.online or not args.manifest or not args.manifest_sha256 or args.capture or args.capture_sha256):
        raise ValueError('Capture requires --online, a hash-pinned --manifest and a NEW --output only.')
    if not online and (args.online or not args.capture or not args.capture_sha256 or args.manifest or args.manifest_sha256):
        raise ValueError('Import requires only --capture, --capture-sha256 and a NEW --output; it is offline.')
    original=read(workspace,args.manifest if online else args.capture,256*1024 if online else source.MAX_IMPORT_BYTES)
    expected=_hash(args.manifest_sha256 if online else args.capture_sha256)
    if hashlib.sha256(original).hexdigest()!=expected:raise ValueError('The pinned source bytes changed before reservation.')
    pins=_pins();policy=_control(data,online=online)
    window=derive_window(now) if online else None
    manifest=source.validate_manifest(metadata(original),window) if online else None
    normalized=source.import_capture(original,expected_sha256=expected) if not online else None
    completion_proof=_completed_input(data,workspace,args.capture,expected,len(original)) if not online else None
    def check():
        if _control(data,online=online)!=policy:raise ValueError('Owner permission changed during the historical attempt.')
        if _pins()!=pins:raise ValueError('Pinned implementation source changed during the historical attempt.')
        current=read(workspace,args.manifest if online else args.capture,len(original))
        if current!=original:raise ValueError('Pinned research input changed during the historical attempt.')
        if completion_proof is not None and _completed_input(data,workspace,args.capture,expected,len(original))!=completion_proof:
            raise ValueError('Historical completion proof changed during import.')
    if not online:
        check()
        with workspace._directory(destination.parent,create=True):destination.mkdir()
        receipt={'schema':1,'source_id':source.SOURCE_ID,'status':'completed','kind':'offline_eod_import',
            'input_sha256':expected,'source_completion_sha256':completion_proof[1],'source_code_sha256':pins,'model_calls':0,'network_requests':0,
            'paper_fills':0,'funding_added':'0','historical_only':True,'performance_study_created':False}
        return _publish(workspace,args.output,{'capture.json':original,'normalized.json':source.canonical(normalized)},receipt,check)[0]
    lock=InstanceLock(data.root/'theta-history.lock');lock.acquire()
    attempt_id=None;receipt=None
    try:
        with data._directory(data.root),closing(_journal(data,create=True)) as db:
            rows=_rows(db);_inspect_previous(workspace,rows)
            if len(rows)>=MAX_ATTEMPTS or sum(r['reserved_requests'] for r in rows)+len(manifest['requests'])>MAX_REQUESTS or sum(r['reserved_wire'] for r in rows)+source.MAX_TOTAL_BYTES>MAX_RESERVED_WIRE:
                raise ValueError('The fixed historical source budget is exhausted; no quota is renewed.')
            if any(r['manifest_sha256']==expected or r['output']==args.output for r in rows):
                raise ValueError('This selection was already reserved; it will not be replayed.')
            check()
            with db:
                cursor=db.execute('INSERT INTO theta_history_attempts(manifest_sha256,output,status,reserved_requests,reserved_wire,created_at) VALUES(?,?,?,?,?,?)',
                    (expected,args.output,'running',len(manifest['requests']),source.MAX_TOTAL_BYTES,window.registered_at))
                attempt_id=cursor.lastrowid
            with workspace._directory(destination.parent,create=True):destination.mkdir()
            client=None
            partial=captured=None
            try:
                client=(source.ThetaHistoryClient if client_factory is None else client_factory)(online=True)
                captured=client.capture(manifest,window=window,check=check)
                check();normalized=source.normalize_capture(captured)
                receipt={'schema':1,'source_id':source.SOURCE_ID,'kind':'free_eod_capture','status':'completed',
                    'manifest_sha256':expected,'selection_window':window.as_dict(),'source_code_sha256':pins,
                    'model_calls':0,'paper_fills':0,'funding_added':'0','historical_only':True,'performance_study_created':False,
                    'provider_authenticated':False,'quote_event_clock_verified':False,'automatic_topups':False}
                receipt,digest=_publish(workspace,args.output,{'selection.json':original,'capture.json':source.canonical(captured),'normalized.json':source.canonical(normalized)},receipt,check)
                with db:db.execute('UPDATE theta_history_attempts SET status=?,receipt_sha256=? WHERE id=? AND status=?',('completed',digest,attempt_id,'running'))
                return receipt
            except Exception as error:
                partial=getattr(error,'partial_capture',None)
                if partial is None and captured is not None:
                    partial={'status':'failed','kind':'quarantined_completed_response',
                        'capture':captured,'automatic_adoption_allowed':False,
                        'capture_sha256':hashlib.sha256(source.canonical(captured)).hexdigest()}
                # No exception text, URL or user/source body is copied into diagnostics.
                failure={'schema':1,'source_id':source.SOURCE_ID,'kind':'free_eod_capture','status':'failed',
                    'manifest_sha256':expected,'selection_window':window.as_dict(),'source_code_sha256':pins,
                    'failure_code':'capture_validation_or_permission_failed','model_calls':0,'paper_fills':0,'funding_added':'0',
                    'retry_performed':False,'inspect_before_any_new_capture':True}
                files={'source.json':original}
                if partial is not None:
                    retained=destination/'capture.json'
                    if captured is not None and retained.is_file():
                        body=read(workspace,args.output+'/capture.json',source.MAX_IMPORT_BYTES)
                        if body!=source.canonical(captured):raise ValueError('Retained failed capture changed; attempt remains uncertain.')
                        failure['quarantined_artifact']={'path':'capture.json','bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'automatic_adoption_allowed':False}
                    else:files['partial-capture.json']=source.canonical(partial)
                try:_publish(workspace,args.output,files,failure)
                finally:
                    with db:db.execute('UPDATE theta_history_attempts SET status=? WHERE id=? AND status=?',('failed',attempt_id,'running'))
                raise ValueError('Historical capture failed; inspect its private failure receipt. It was not replayed.') from None
            finally:
                if client is not None:client.close()
    finally:lock.release()


def main(argv=None):
    args=parser().parse_args(argv)
    if args.online and os.environ.get('ARIADNE_OFFLINE') not in (None,'0'):
        print('Inherited offline policy blocks historical collection.',file=sys.stderr);return 2
    os.environ['ARIADNE_OFFLINE']='0' if args.online else '1'
    from ariadne.network_policy import install_network_policy
    install_network_policy()
    try:value=run(args)
    except (ValueError,OSError,TypeError,KeyError,UnicodeError,RecursionError,OverflowError,sqlite3.Error):
        print('Theta historical command stopped. Inspect private inputs or failed receipts; no financial work was performed.',file=sys.stderr);return 2
    print(json.dumps(value,ensure_ascii=True,allow_nan=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
