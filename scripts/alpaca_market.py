"""Data-only Alpaca quote capture and read-only shared-paper-account qualification.

No paper financial writes, brokerage endpoints, account creation or scheduling.
Credentials belong only in the hidden local prompt, never command arguments.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
MAX_INPUT=256*1024
MAX_ARTIFACT=6*1024*1024


class AlpacaCommandError(ValueError):
    """Fixed local diagnostics only, no raw response, key or OS error text."""


def parser(provider='alpaca'):
    if provider not in ('alpaca','theta'):raise ValueError('Unsupported data-only provider.')
    value=argparse.ArgumentParser(description=__doc__ if provider=='alpaca' else 'Theta owner-managed terminal capture and read-only qualification; no financial writer.')
    actions=('setup','status','binding','collect','qualify') if provider=='alpaca' else ('status','binding','collect','qualify')
    value.add_argument('action',choices=actions)
    value.add_argument('--data-dir',default='data',help='Existing box-relative private data directory.')
    value.add_argument('--workspace',default='data/workspace',help='Existing workspace inside that data directory.')
    value.add_argument('--online',action='store_true',help='Explicit process opt-in; inherited offline policy is never weakened.')
    value.add_argument('--instruments',help='Workspace-relative exact schema1 instrument watchlist.')
    value.add_argument('--request',help='Workspace-relative bound read-only qualification request.')
    value.add_argument('--supplement',help='Workspace-relative independent dated missing metadata.')
    value.add_argument('--output',help='New exclusive reports/alpaca-<name> directory.')
    return value


def _canonical(value):
    from ariadne.options_feed_qualification import canonical
    return canonical(value)


def _now(value=None):
    value=datetime.now(timezone.utc) if value is None else value
    if type(value) is not datetime or value.utcoffset() is None:
        raise AlpacaCommandError('Use an aware actual qualification clock.')
    return value.astimezone(timezone.utc)


def _guards(args,root):
    from ariadne.config import _allowed_path
    from ariadne.tools import ToolRegistry
    from scripts.options_market import guard_for
    root=_allowed_path(root);box=object.__new__(ToolRegistry);box.root=root
    data=box._path(args.data_dir)
    if not data.is_dir():
        raise AlpacaCommandError('Existing initialized Ariadne data is required; no account is created.')
    guard=object.__new__(ToolRegistry);guard.root=data
    workspace=guard_for(args.workspace,root=root)
    if not workspace.root.is_relative_to(data):
        raise AlpacaCommandError('Workspace must remain inside the selected box data directory.')
    return guard,workspace


def _load(guard,path):
    from scripts.options_market import metadata,read
    return metadata(read(guard,path,MAX_INPUT))


def _readonly(guard,name='ariadne.sqlite3'):
    from ariadne.config import _allowed_path
    path=guard._path(name)
    for suffix in ('','-wal','-shm','-journal'):
        file=_allowed_path(str(path)+suffix)
        if not file.exists():
            if not suffix:raise AlpacaCommandError('Existing owner databases are required; none were initialized.')
            continue
        if not file.is_file() or file.stat().st_nlink!=1:
            raise AlpacaCommandError('Owner database files must be regular and single-link.')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)
    db.row_factory=sqlite3.Row
    deadline=time.monotonic()+5
    db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
    db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA query_only=ON')
    return db


def _controls(db):
    from scripts.options_market import metadata
    values={}
    for key in ('paused','internet_access'):
        row=db.execute('SELECT rowid,value FROM settings WHERE key=?',(key,)).fetchone()
        values[key]={'rowid':row[0],'value':metadata(row[1].encode())} if row else None
    paused=values['paused']
    if paused is not None and type(paused['value']) is not bool:
        raise AlpacaCommandError('Owner pause control is invalid; no reset is permitted.')
    internet=values['internet_access']
    if internet is not None:
        value=internet['value']
        if type(value) is not dict or type(value.get('enabled')) is not bool or type(value.get('revision')) is not int or value['revision']<0:
            raise AlpacaCommandError('Owner Internet control is invalid; no reset is permitted.')
    return values


def _network_allowed(guard):
    with guard._directory(guard.root),closing(_readonly(guard)) as db:
        state=_controls(db)
        if state['paused'] is not None and state['paused']['value'] is True:
            raise AlpacaCommandError('Global autonomy is paused; collection was not started.')
        if state['internet_access'] is None or state['internet_access']['value']['enabled'] is not True:
            raise AlpacaCommandError('Owner Internet access is disabled or unconfigured; no control was changed.')
        return hashlib.sha256(_canonical(state)).hexdigest()


def _snapshot(guard,now=None):
    from ariadne.options_workflow import OptionsWorkflow
    from ariadne.options_feed_qualification import _financial_state,digest
    from scripts.options_market import metadata
    from ariadne.briefing import _tz
    at=_now(now)
    with guard._directory(guard.root),closing(_readonly(guard)) as db:
        db.execute('BEGIN')
        controls=_controls(db)
        accessor=object.__new__(OptionsWorkflow)
        revision,cfg=accessor._meta(db)
        if not accessor._exists(db):
            raise AlpacaCommandError('An existing total-bankroll paper account is required; none was created.')
        rows=db.execute('SELECT id,state,policy FROM opt_accounts WHERE closed_at IS NULL LIMIT 2').fetchall()
        if len(rows)!=1:
            raise AlpacaCommandError('Exactly one active shared paper account is required; no account was selected.')
        row=rows[0]
        if type(row['state']) is not str or len(row['state'].encode())>2*1024*1024:
            raise AlpacaCommandError('Retained account state exceeds its protected bound.')
        if type(row['policy']) is not str or len(row['policy'].encode())>8192:
            raise AlpacaCommandError('Retained account policy exceeds its protected bound.')
        state=metadata(row['state'].encode());policy=metadata(row['policy'].encode())
        if type(state) is not dict or type(policy) is not dict:
            raise AlpacaCommandError('Retained account state and policy must be structured objects.')
        snapshot={'account_id':row['id'],'state':state,'state_json':row['state'],
            'state_sha256':hashlib.sha256(row['state'].encode()).hexdigest(),
            'policy':policy,'policy_sha256':hashlib.sha256(row['policy'].encode()).hexdigest(),
            'revision':revision,'global_paused':controls['paused'] is not None and controls['paused']['value'],
            'internet_enabled':controls['internet_access'] is not None and controls['internet_access']['value']['enabled'],
            'options_enabled':cfg['enabled'],'options_paused':cfg['paused'],
            'options_hold':bool(db.execute("SELECT 1 FROM opt_runs WHERE status IN ('running','failed') LIMIT 1").fetchone()),
            'network_state_sha256':digest(controls)}
        snapshot['options_hold']=snapshot['options_hold'] or state.get('hold') is not None
        has_qqq=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qqq_runs'").fetchone()
        snapshot['qqq_hold']=bool(has_qqq and db.execute("SELECT 1 FROM qqq_runs WHERE status IN ('running','failed') LIMIT 1").fetchone())
        _financial_state(snapshot)
        with closing(_readonly(guard,'briefing.sqlite3')) as brief:
            brief.execute('BEGIN')
            setting=brief.execute('SELECT value FROM briefing_settings WHERE id=1').fetchone()
            if not setting or type(setting[0]) is not str or len(setting[0].encode())>MAX_INPUT:
                raise AlpacaCommandError('Initialized bounded briefing controls are required.')
            value=metadata(setting[0].encode())
            if (type(value) is not dict or any(type(value.get(k)) is not bool for k in ('enabled','paused'))
                    or type(value.get('revision')) is not int or value['revision']<0
                    or value.get('timezone') not in ('America/Chicago','UTC')):
                raise AlpacaCommandError('Existing briefing controls are invalid; none were changed.')
            today=at.astimezone(_tz(value['timezone'])).date().isoformat()
            held=brief.execute("SELECT 1 FROM briefing_runs WHERE day IS NOT NULL AND (status='running' OR (day=? AND status IN ('failed','interrupted'))) LIMIT 1",(today,)).fetchone()
            snapshot.update(brief_enabled=value['enabled'],brief_paused=value['paused'],brief_hold=bool(held),
                brief_config_sha256=hashlib.sha256(setting[0].encode()).hexdigest(),
                epoch={'global_pause_rowid':controls['paused']['rowid'] if controls['paused'] is not None else None,
                       'options_revision':cfg['revision'],'brief_revision':value['revision']})
        return snapshot


def _snapshot_signature(value):
    from ariadne.options_feed_qualification import binding,digest
    return digest({'binding':binding(value),**{k:value[k] for k in (
        'network_state_sha256','brief_config_sha256','global_paused','internet_enabled','options_enabled',
        'options_paused','brief_enabled','brief_paused','brief_hold','options_hold','qqq_hold')}})


def _hidden(prompt):
    if not sys.stdin.isatty():
        raise AlpacaCommandError('Use an interactive local terminal for hidden key entry; piped credentials are refused.')
    return getpass.getpass(prompt)


def _output(workspace,relative,provider='alpaca'):
    if provider not in ('alpaca','theta'):raise AlpacaCommandError('Unsupported output provider.')
    if type(relative) is not str or not re.fullmatch('reports/'+provider+r'-[A-Za-z0-9][A-Za-z0-9._-]{0,95}',relative):
        raise AlpacaCommandError('Use a NEW workspace-relative reports/'+provider+'-<name> directory.')
    path=workspace._path(relative)
    if path.exists():
        raise AlpacaCommandError('Earlier output exists; inspect it instead of overwriting or replaying uncertain work.')
    return path


def _publish(workspace,output,files,receipt,provider='alpaca'):
    from ariadne.atomic import publish_bytes
    destination=_output(workspace,output,provider)
    if any(type(raw) is not bytes or len(raw)>MAX_ARTIFACT for raw in files.values()):
        raise AlpacaCommandError('Capture evidence exceeds its protected artifact bound.')
    receipt={**receipt,'artifacts':[{'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in files.items()]}
    with workspace._directory(destination.parent,create=True):
        destination.mkdir()
        with workspace._directory(destination):
            for name,raw in files.items():
                publish_bytes(destination/name,raw)
                if (destination/name).read_bytes()!=raw:
                    raise AlpacaCommandError('Evidence readback failed; incomplete output was retained.')
            publish_bytes(destination/'completed.json',_canonical(receipt))
    return {**receipt,'output':output}


def run(args,*,root=ROOT,client_factory=None,now=None,provider='alpaca'):
    from ariadne.options_feed_qualification import binding,preflight,qualify,readiness_view,verify_clocks
    if provider=='alpaca':
        from ariadne.alpaca_market_data import AlpacaMarketDataClient as Client,instruments,normalize_quotes,SOURCE_ID
        from ariadne.alpaca_credentials import save,load,configured
        expected={'source_id':SOURCE_ID,'operation':'latest_quotes','provider_authenticated':True,'options_feed':'opra','stocks_feed':'sip'}
    elif provider=='theta':
        from ariadne.theta_market_data import ThetaMarketDataClient as Client,instruments,normalize_quotes,SOURCE_ID
        expected={'source_id':SOURCE_ID,'operation':'latest_quotes','provider_authenticated':False,
            'local_terminal_verified':False,'kind':'local_terminal_unattested',
            'options_feed':'opra_unattested','stocks_feed':'nasdaq_basic_unattested'}
    else:raise AlpacaCommandError('Unsupported data-only provider.')
    if args.online:
        if os.environ.get('ARIADNE_OFFLINE') not in (None,'0'):
            raise AlpacaCommandError('Inherited offline policy blocks this online command.')
        from ariadne.network_policy import network_policy_status
        if network_policy_status()['mode']=='offline':
            raise AlpacaCommandError('Installed offline policy blocks this online command.')
    data,workspace=_guards(args,root)
    fields=(args.instruments,args.request,args.supplement,args.output)
    if args.action in ('setup','status','binding') and (args.online or any(x is not None for x in fields)):
        raise AlpacaCommandError('This local action does not accept network or file-input overrides.')
    if args.action=='setup':
        if provider!='alpaca':raise AlpacaCommandError('Theta credentials stay in the owner-managed terminal; this bridge saves none.')
        key=_hidden('Alpaca API key ID (hidden): ');secret=_hidden('Alpaca API secret key (hidden): ')
        try:return save(data,key,secret)
        finally:key=secret=None
    if args.action in ('status','binding'):
        snapshot=_snapshot(data,now)
        if args.action=='binding':return binding(snapshot)
        return {**readiness_view(SOURCE_ID),'credential_configured':configured(data) if provider=='alpaca' else None,'network_requests':0,
            'current_account_schema':snapshot['state']['schema'],'current_cash':snapshot['state']['cash'],
            'contributions':snapshot['state']['contributions'],'automatic_topups':False,
            'financial_state_projection_verified':True,'retained_financial_history_verified':False,
            'global_paused':snapshot['global_paused'],'internet_enabled':snapshot['internet_enabled'],
            'options_hold':snapshot['options_hold'],'brief_hold':snapshot['brief_hold'],'qqq_hold':snapshot['qqq_hold'],
            'account_policy_matches_current_epoch':snapshot['policy']==snapshot['epoch']}
    if args.action not in ('collect','qualify') or not args.online or not args.instruments or not args.output:
        raise AlpacaCommandError('Collect and qualify require --online, --instruments and a new --output.')
    if args.action=='collect' and (args.request or args.supplement):
        raise AlpacaCommandError('Collect accepts the watchlist and output only; it never uses a paper request.')
    if args.action=='qualify' and not args.request:
        raise AlpacaCommandError('Qualify requires a current account-bound --request.')
    _output(workspace,args.output,provider)
    watch=_load(workspace,args.instruments)
    if type(watch) is not dict or set(watch)!={'schema','instruments'} or type(watch['schema']) is not int or watch['schema']!=1:
        raise AlpacaCommandError('Use exactly schema:1 and instruments:[{symbol,type}].')
    requested=instruments(watch['instruments'])
    before=body=supplement=None
    if args.action=='qualify':
        before=_snapshot(data,now);body=_load(workspace,args.request);preflight(before,body,SOURCE_ID)
        requested_options={item['symbol'] for item in requested if item['type']=='OPTION'}
        if any(row['contract_id'] not in requested_options for row in body['requests']):
            raise AlpacaCommandError('Qualification proposals must reference the requested option watchlist.')
        supplement=_load(workspace,args.supplement) if args.supplement else None
        from ariadne.options_feed_qualification import validate_supplement
        validate_supplement(supplement,requested_options)
    policy=_network_allowed(data)
    factory=Client if client_factory is None else client_factory
    if provider=='alpaca':
        key,secret=load(data)
        try:client=factory(key,secret,online=True)
        finally:key=secret=None
    else:
        client=factory(online=True)
    try:capture=client.quotes(requested)
    finally:client.close()
    if _network_allowed(data)!=policy:
        raise AlpacaCommandError('Owner network control changed during collection; capture was not accepted.')
    if type(capture) is not dict or capture.get('transport')!=expected:
        raise AlpacaCommandError('The fixed selected-source capture contract was not satisfied.')
    normalized=normalize_quotes(capture,expected_instruments=requested)
    at=_now(now)
    verify_clocks(normalized,at.isoformat())
    files={'capture.json':_canonical(capture),'normalized.json':_canonical(normalized)}
    result=None
    if args.action=='qualify':
        after=_snapshot(data,now);preflight(after,body,SOURCE_ID)
        if _snapshot_signature(after)!=_snapshot_signature(before):
            raise AlpacaCommandError('Account or controls changed during capture; qualification was not accepted.')
        result=qualify(after,body,normalized,supplement=supplement,decided_at=at.isoformat(),source_id=SOURCE_ID)
        files.update({'request.json':_canonical(body),'supplement.json':_canonical(supplement),'qualification.json':_canonical(result)})
    receipt={'schema':1,'kind':provider+'_feed_'+args.action,'status':'completed','source_id':SOURCE_ID,
        'received_at':normalized['received_at'],'recorded_at':at.isoformat(),
        'transport_observed_by_command':expected,'normalization_authenticates_saved_files':False,
        'live_feed_qualified':False,'paper_submission_ready':False,'paper_fills':0,'ledger_write_performed':False,
        'funding_added':'0','bankroll_created':False,'automatic_topups':False,'model_calls':0,
        'blockers':result['blockers'] if result else readiness_view(SOURCE_ID)['blockers']}
    return _publish(workspace,args.output,files,receipt,provider)


def main(argv=None,provider='alpaca'):
    args=parser(provider).parse_args(argv)
    if args.online and os.environ.get('ARIADNE_OFFLINE') not in (None,'0'):
        print('Data-only command blocked by inherited offline policy.',file=sys.stderr);return 2
    os.environ['ARIADNE_OFFLINE']='0' if args.online else '1'
    from ariadne.network_policy import install_network_policy
    install_network_policy()
    try:result=run(args,provider=provider)
    except AlpacaCommandError as error:
        print(str(error),file=sys.stderr);return 2
    except (ValueError,OSError,TypeError,UnicodeError,RecursionError,OverflowError,sqlite3.Error):
        print('Data-only command failed validation or access; inspect local inputs privately. No paper orders were submitted.',file=sys.stderr)
        return 2
    print(json.dumps(result,ensure_ascii=True,allow_nan=False,indent=2));return 0


if __name__=='__main__':
    raise SystemExit(main())
