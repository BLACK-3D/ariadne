"""Offline, source-bound QQQ history review and explicit new resolution requests.

Review never initializes or mutates owner state. Resolve requires the exact
review-file hash and a new request ID; it does not fetch, replay or paper trade.
"""
from __future__ import annotations
import argparse
from contextlib import closing,contextmanager
import hashlib
import json
import secrets
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SOURCE_FILES=("ariadne/qqq_workflow.py","ariadne/qqq_data.py","ariadne/qqq_recovery.py",
    "ariadne/handoff.py","ariadne/etf_tools.py","ariadne/config.py","ariadne/tools.py",
    "ariadne/backup.py","ariadne/atomic.py","ariadne/store.py","ariadne/internet.py",
    "ariadne/briefing.py","ariadne/resources/briefing/America_Chicago.tzif","scripts/qqq_history.py")
MAX_REVIEW=2*1024*1024

def parser():
    value=argparse.ArgumentParser(description=__doc__)
    value.add_argument("action",choices=("review","resolve"))
    value.add_argument("--data-dir",default="data")
    value.add_argument("--workspace",default="data/workspace")
    value.add_argument("--candidate")
    value.add_argument("--candidate-sha256")
    value.add_argument("--failed-request-id")
    value.add_argument("--review")
    value.add_argument("--review-sha256")
    value.add_argument("--request-id")
    value.add_argument("--output",required=True,help="New private staging/... evidence file, never overwritten.")
    return value

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()

def guard(root):
    from ariadne.tools import ToolRegistry
    from ariadne.config import _allowed_path
    result=object.__new__(ToolRegistry);result.root=_allowed_path(root)
    return result

def read(box,name,maximum):
    import os
    if type(name) is not str or "\\" in name or box._parts(name)!=tuple(name.split("/")):
        raise ValueError("Use canonical relative box paths.")
    path=box._path(name)
    with box._directory(path.parent) as directory:
        with os.fdopen(box._open_read(path,directory),"rb") as source:raw=source.read(maximum+1)
    if len(raw)>maximum:raise ValueError("Review evidence exceeds its byte bound.")
    return raw

def source_hashes():
    box=guard(ROOT)
    return {name:hashlib.sha256(read(box,name,2*1024*1024)).hexdigest() for name in SOURCE_FILES}

class ExistingStore:
    """No Store initializer, create-if-missing mode, migrations or setting writes."""
    def __init__(self,path,box,*,readonly):
        self.path,self.box,self.readonly=path,box,readonly

    @contextmanager
    def connect(self,*,require_existing=False,readonly=None):
        from ariadne.backup import database_path,_pin_database_files
        selected=self.readonly if readonly is None else readonly
        with self.box._directory(self.path.parent):
            path,present=database_path(self.path)
            if not present:raise ValueError("Existing owner database is missing; nothing was initialized.")
            with _pin_database_files(path):
                with closing(sqlite3.connect(path.as_uri()+("?mode=ro" if selected else "?mode=rw"),uri=True,timeout=1)) as db:
                    db.row_factory=sqlite3.Row
                    db.execute("PRAGMA trusted_schema=OFF")
                    if selected:db.execute("PRAGMA query_only=ON")
                    deadline=time.monotonic()+5
                    db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
                    with db:yield db

    def setting(self,key,default=None):
        with self.connect(readonly=True) as db:
            row=db.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
        return json.loads(row[0]) if row else default

def run(args,*,root=ROOT,now=None):
    from ariadne.atomic import publish_bytes
    from ariadne.briefing import _DEFAULT,BriefingService
    from ariadne.etf_tools import _load
    from ariadne.handoff import _identifier
    from ariadne.qqq_workflow import QQQWorkflow
    box=guard(root)
    for relative in (args.data_dir,args.workspace,args.output):
        if "\\" in relative or box._parts(relative)!=tuple(relative.split("/")):
            raise ValueError("Use canonical relative box paths.")
    if box._parts(args.output)[0]!="staging":raise ValueError("CLI evidence must be a new private staging/... file.")
    destination=box._path(args.output)
    if destination.exists():raise ValueError("Evidence output already exists; no uncertain work is reused.")
    if args.action=="review":
        if not all((args.candidate,args.candidate_sha256,args.failed_request_id)) or any((args.review,args.review_sha256,args.request_id)):
            raise ValueError("Review requires only candidate/hash/failed request and output.")
        expected_sources=source_hashes()
    else:
        if not all((args.review,args.review_sha256,args.request_id)) or any((args.candidate,args.candidate_sha256,args.failed_request_id)):
            raise ValueError("Resolve requires only exact review/hash/new request ID and output.")
        raw=read(box,args.review,MAX_REVIEW)
        if hashlib.sha256(raw).hexdigest()!=args.review_sha256:raise ValueError("Reviewed resolution file changed.")
        envelope=_load(raw)
        if envelope.get("kind")!="qqq_history_cli_review" or envelope.get("schema")!=1:raise ValueError("Choose a complete QQQ CLI review file.")
        expected_sources=envelope["source_sha256"]
        if source_hashes()!=expected_sources:raise ValueError("QQQ history review source changed; inspect a new review.")
        _identifier(args.request_id)
    reservation=args.output+".reserved"
    reservation_raw=canonical({"schema":1,"status":"reserved","action":args.action,
        "token":secrets.token_hex(16),"output":args.output,"request_id":args.request_id})
    reservation_path=box._path(reservation)
    # A permanent exclusive claim is evidence, not a reusable lock. A failed
    # invocation keeps its reservation; a new attempt needs a different output.
    with box._directory(reservation_path.parent,create=True):publish_bytes(reservation_path,reservation_raw)
    def output_checkpoint():
        if read(box,reservation,4096)!=reservation_raw:raise ValueError("CLI evidence reservation changed.")
        if destination.exists():raise ValueError("CLI evidence output was claimed before publication.")
    output_checkpoint()
    settings=SimpleNamespace(data_dir=box._path(args.data_dir),workspace=box._path(args.workspace))
    registry=guard(settings.workspace);registry.settings=settings
    store=ExistingStore(settings.data_dir/"ariadne.sqlite3",box,readonly=args.action=="review")
    briefing=ExistingStore(settings.data_dir/"briefing.sqlite3",box,readonly=True)
    def controls(db=None):
        if db is None:
            with briefing.connect(readonly=True) as current:return controls(current)
        row=db.execute("SELECT value FROM briefing_settings WHERE id=1").fetchone()
        if not row:raise ValueError("Existing briefing permission is missing.")
        cfg={**_DEFAULT,**json.loads(row[0])}
        return {**cfg,"execution_hold":BriefingService._qqq_brief_hold(db,cfg)}
    @contextmanager
    def publication(expected):
        with briefing.connect(readonly=False) as db:
            db.execute("BEGIN IMMEDIATE")
            if controls(db)!=expected:raise ValueError("Brief permissions changed before resolution commit.")
            if source_hashes()!=expected_sources:raise ValueError("Review source changed before resolution commit.")
            yield
    def source_checkpoint():
        output_checkpoint()
        if source_hashes()!=expected_sources:raise ValueError("Review source changed before resolution commit.")
    service=QQQWorkflow(registry,store,controls=controls,publication_lease=publication,history_checkpoint=source_checkpoint)
    if args.action=="review":
        result={"schema":1,"kind":"qqq_history_cli_review","source_sha256":expected_sources,
            "review":service.review_history({"candidate_path":args.candidate,"candidate_sha256":args.candidate_sha256,"failed_request_id":args.failed_request_id},now),
            "owner_operations":0,"network_calls":0,"model_calls":0}
    else:
        if envelope["review"].get("eligible") is not True:raise ValueError("Review is not eligible for metadata-only resolution.")
        body={**envelope["review"]["resolve_body_without_request_id"],"request_id":args.request_id}
        result={"schema":1,"kind":"qqq_history_cli_resolution","receipt":service.resolve_history(body,now),"network_calls":0,"model_calls":0}
    if source_hashes()!=expected_sources:raise ValueError("QQQ history source changed before evidence publication.")
    raw=canonical(result)
    if len(raw)>MAX_REVIEW:raise ValueError("Compact review/evidence output exceeds2MiB.")
    output_checkpoint()
    with box._directory(destination.parent,create=True):publish_bytes(destination,raw,checkpoint=output_checkpoint)
    if read(box,args.output,MAX_REVIEW)!=raw:raise ValueError("Review output readback changed.")
    return result

def main(argv=None):
    def audit(event,arguments):
        if event.startswith("socket.") or event in ("subprocess.Popen","os.system","os.posix_spawn","os.exec"):
            raise PermissionError("Offline QQQ review forbids network, models and child processes.")
    sys.addaudithook(audit)
    args=parser().parse_args(argv)
    try:result=run(args)
    except (ValueError,OSError,sqlite3.Error,KeyError,TypeError) as error:
        print("QQQ history stopped: "+str(error),file=sys.stderr);return 2
    print(json.dumps({"kind":result["kind"],"output":args.output,"status":"completed","network_calls":0,"model_calls":0}))
    return 0

if __name__=="__main__":raise SystemExit(main())
