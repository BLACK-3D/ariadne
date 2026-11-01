"""Versioned same-account stress activation and independent local context capture.

Context capture never fetches data or enters opt_runs: missing or uncertain new
context denies entries without creating an unrelated financial-exit hold.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from .atomic import publish_bytes
from .config import _allowed_path
from .handoff import HandoffConflict, _identifier, _load
from .options_workflow import _json, _now, _digest, MAX_DATA
from . import options_stress_context as context
from . import options_stress_eod as engine

MAX_CONTEXTS, MAX_CONTEXT_BYTES, MAX_CAPTURE_SECONDS = 100, 8*1024*1024, 5
RESERVATION_BYTES = 2*context.MAX_WITNESS_BYTES+context.MAX_RECEIPT_BYTES+8192
MAX_CONTEXT_RECEIPT=8192
_FILES = {"previous-state.json","activation.json","initial-state.json"}
_RUNTIME = {"ariadne/qqq_data.py":"qqq_data_sha256", "ariadne/qqq_recovery.py":"qqq_recovery_sha256",
            "ariadne/resources/briefing/America_Chicago.tzif":"chicago_tzif_sha256"}


def _raw(value):return _json(value).encode()


def _market_load(raw):
    """Market OHLC may contain floats; do not relax the owner's money codec."""
    if type(raw) not in (str,bytes) or len(raw)>MAX_DATA:raise ValueError("Bounded market JSON required.")
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError("Duplicate market JSON field.")
            value[key]=item
        return value
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError("Nonfinite market JSON.")))


class OptionsStressWorkflow:
    def __init__(self,eod):
        self.eod,self.owner=eod,eod.owner
        self._runtime_root=Path(__file__).resolve().parents[1]

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opt_stress_migrations'").fetchone() is not None

    @staticmethod
    def _schema(db):
        db.execute("CREATE TABLE IF NOT EXISTS opt_stress_migrations(account_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,old_state TEXT NOT NULL,old_state_sha256 TEXT NOT NULL,old_data_sha256 TEXT NOT NULL,initial_state TEXT NOT NULL,initial_state_sha256 TEXT NOT NULL,activated_at TEXT NOT NULL,eod_config TEXT NOT NULL,policy TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_stress_contexts(request_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,data_sha256 TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,receipt TEXT NOT NULL,bytes INTEGER NOT NULL,UNIQUE(account_id,data_sha256))")
        db.execute("CREATE TABLE IF NOT EXISTS opt_stress_context_failures(request_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,created_at TEXT NOT NULL,reason TEXT NOT NULL)")

    @contextmanager
    def _runtime_lease(self):
        from .tools import ToolRegistry
        guard=object.__new__(ToolRegistry);guard.root=_allowed_path(self._runtime_root)
        with ExitStack() as held:
            opened=[]
            for name,key in _RUNTIME.items():
                path=guard._path(name);directory=held.enter_context(guard._directory(path.parent))
                stream=held.enter_context(os.fdopen(guard._open_read(path,directory),"rb"))
                stat=os.fstat(stream.fileno())
                if stat.st_size>1024*1024:raise ValueError("Stress calendar/runtime dependency is unbounded.")
                opened.append((path,stream,(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns),context.CALENDAR[key]))
            def check():
                for path,stream,identity,expected in opened:
                    stream.seek(0);raw=stream.read(identity[2]+1);info=path.stat();fd=os.fstat(stream.fileno())
                    if (len(raw)!=identity[2] or _digest(raw)!=expected or
                        (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=identity or
                        (fd.st_dev,fd.st_ino,fd.st_size,fd.st_mtime_ns)!=identity):
                        raise ValueError("Stress calendar/runtime dependency changed.")
            check();yield check;check()

    @staticmethod
    def _basis(account):
        return {"id":account["id"],"state_sha256":_digest(_raw(account["state"])),"started_at":account["started_at"],"policy":account["policy"]}

    @staticmethod
    def _prefix(db,count=None):
        rows=db.execute("SELECT source_date,digest,bytes,request_id FROM opt_eod_data ORDER BY source_date LIMIT 101").fetchall()
        if len(rows)>100:raise ValueError("The retained EOD source prefix exceeds its bound.")
        if count is not None:
            if not 0<=count<=len(rows):raise ValueError("The stress activation prefix disappeared.")
            rows=rows[:count]
        return _digest(_raw([dict(row) for row in rows]))

    def verify(self,receipt):
        _identifier(receipt.get("request_id"))
        base="reports/options-recovery/"+receipt["request_id"]+"/"
        artifacts=receipt.get("artifacts")
        if (receipt.get("action")!="stress_activate" or receipt.get("rule_sha256")!=engine.RULE_SHA256 or
            type(artifacts) is not list or len(artifacts)!=3 or {v.get("path") for v in artifacts}!={base+n for n in _FILES}):
            raise ValueError("The stress activation artifact contract changed.")
        saved={}
        for item in artifacts:
            raw=self.owner._read(item["path"],engine.MAX_STATE_BYTES)
            if len(raw)!=item["bytes"] or _digest(raw)!=item["sha256"]:raise ValueError("Stress activation evidence changed; no regeneration.")
            saved[item["path"][len(base):]]=raw
        report=_load(saved["activation.json"])
        old=_load(saved["previous-state.json"])
        initial=engine.migrate_flat(old,activated_at=report["activated_at"],old_state_sha256=_digest(saved["previous-state.json"]),old_data_sha256=report["old_data_sha256"])
        if (saved["initial-state.json"]!=_raw(initial) or report.get("new_funding")!="0" or report.get("monthly_budget_reset") is not False or
            report.get("rule_sha256")!=engine.RULE_SHA256 or report.get("calendar_sha256")!=context.CALENDAR_SHA256 or
            report.get("research_only") is not True or report.get("execution_qualified") is not False or
            report.get("account_id")!=receipt.get("account_id") or report.get("initial_state_sha256")!=receipt.get("state_sha256") or
            _digest(saved["initial-state.json"])!=receipt.get("state_sha256") or report.get("old_state_sha256")!=receipt.get("old_state_sha256") or
            report.get("old_data_sha256")!=receipt.get("old_data_sha256") or receipt.get("simulated_funding_added")!="0" or
            receipt.get("monthly_budget_reset") is not False or receipt.get("research_only") is not True or receipt.get("execution_qualified") is not False):
            raise ValueError("Stress activation lost its original account or funding lineage.")
        return saved

    def activate(self,body,now=None):
        owner=self.owner;owner._arguments(body,{"acknowledge_simulated","acknowledge_stress_only"})
        if body["acknowledge_simulated"] is not True or body["acknowledge_stress_only"] is not True:
            raise ValueError("Acknowledge the existing simulated account and new stress-only policy.")
        clock=_now(now);receipt=None;reserved=False
        with self._runtime_lease() as runtime_check,owner._ownership():
            try:
                with owner.database() as db:
                    db.execute("BEGIN IMMEDIATE");owner._schema(db)
                    cfg,brief=owner._policy(db)
                    replay=owner._reserve(db,"stress_activate",body,None,clock)
                    if replay:return replay
                    receipt={"request_id":body["request_id"],"action":"stress_activate","status":"running","created_at":clock.isoformat(),
                        "artifacts":[],"model_calls":0,"replayed":False,"rule_sha256":engine.RULE_SHA256}
                    self._schema(db);feed=self.eod.config(db);account=owner._account(db,clock)
                    if not account or account["state"].get("schema")!=3 or not feed["initialized"]:
                        raise HandoffConflict("Activate only the one existing healthy schema3 F account.")
                    if account["policy"]!=owner._epoch(db,cfg,brief):raise HandoffConflict("The original paper authority changed.")
                    self.eod.verify_account(db,account["id"],account["state"])
                    self.eod._audit_prefix(db,account["id"],account["state"],full=True)
                    old_raw=db.execute("SELECT state FROM opt_accounts WHERE id=?",(account["id"],)).fetchone()[0].encode()
                    if _load(old_raw)!=account["state"]:raise ValueError("The original account state changed.")
                    data_sha=self._prefix(db);basis=self._basis(account)
                reserved=True
                initial=engine.migrate_flat(account["state"],activated_at=clock.isoformat(),old_state_sha256=_digest(old_raw),old_data_sha256=data_sha)
                initial_raw=_raw(initial)
                report={"schema":1,"account_id":account["id"],"activated_at":clock.isoformat(),"rule_sha256":engine.RULE_SHA256,
                    "old_state_sha256":_digest(old_raw),"old_data_sha256":data_sha,"initial_state_sha256":_digest(initial_raw),
                    "new_funding":"0","monthly_budget_reset":False,"eod_config_sha256":_digest(_raw(feed)),
                    "policy":account["policy"],"calendar_sha256":context.CALENDAR_SHA256,"research_only":True,"execution_qualified":False}
                receipt.update(account_id=account["id"],old_state_sha256=_digest(old_raw),old_data_sha256=data_sha,state_sha256=_digest(initial_raw),
                    simulated_funding_added="0",monthly_budget_reset=False,research_only=True,execution_qualified=False)
                def checkpoint():
                    runtime_check()
                    with owner.database() as db:
                        current,current_brief=owner._policy(db,cfg)
                        if current_brief!=brief or self.eod.config(db)!=feed or owner._epoch(db,current,current_brief)!=account["policy"]:
                            raise HandoffConflict("Stress activation permissions/source changed.")
                def final(db):
                    checkpoint()
                    current=owner._account(db,clock)
                    if not current or self._basis(current)!=basis or self._prefix(db)!=data_sha:
                        raise HandoffConflict("Original stress activation account/source changed before commit.")
                    self.verify(receipt)
                    db.execute("INSERT INTO opt_stress_migrations VALUES(?,?,?,?,?,?,?,?,?,?)",(account["id"],body["request_id"],old_raw.decode(),
                        _digest(old_raw),data_sha,initial_raw.decode(),_digest(initial_raw),clock.isoformat(),_json(feed),_json(account["policy"])))
                    db.execute("UPDATE opt_accounts SET state=? WHERE id=?",(initial_raw.decode(),account["id"]))
                    db.execute("UPDATE opt_eod_state SET request_id=?,state_sha256=? WHERE account_id=?",(body["request_id"],_digest(initial_raw),account["id"]))
                owner._publish(cfg,brief,receipt,(("previous-state.json",old_raw),("activation.json",_raw(report)),("initial-state.json",initial_raw)),final=final,extra_check=checkpoint)
                return receipt
            except Exception as error:
                if reserved:owner._failed(receipt,error)
                raise

    def verify_account(self,db,account_id,state):
        checked=engine.validate_state(state)
        if not self._exists(db):raise ValueError("Stress account has no activation witness.")
        row=db.execute("SELECT * FROM opt_stress_migrations WHERE account_id=?",(account_id,)).fetchone()
        if not row or _digest(row["old_state"].encode())!=row["old_state_sha256"]:raise ValueError("Original stress account state changed.")
        old=_load(row["old_state"])
        initial=engine.migrate_flat(old,activated_at=row["activated_at"],old_state_sha256=row["old_state_sha256"],old_data_sha256=row["old_data_sha256"])
        if (_raw(initial)!=row["initial_state"].encode() or _digest(_raw(initial))!=row["initial_state_sha256"] or
            initial["stress_policy"]["activation"]!=checked["stress_policy"]["activation"] or
            self._prefix(db,old["processed_count"])!=row["old_data_sha256"]):
            raise ValueError("Stress activation lost its original prefix/funding lineage.")
        receipt=self.eod._recorded(db,row["request_id"]);saved=self.verify(receipt)
        report=_load(saved["activation.json"])
        if saved["previous-state.json"]!=row["old_state"].encode() or saved["initial-state.json"]!=row["initial_state"].encode():
            raise ValueError("Stress activation table and retained files disagree.")
        if report["activated_at"]!=row["activated_at"] or report["eod_config_sha256"]!=_digest(row["eod_config"].encode()) or _raw(report["policy"])!=row["policy"].encode():
            raise ValueError("Original stress activation configuration/authority metadata changed.")
        self.eod.verify_account(db,account_id,engine.legacy_state(checked),committed_state=checked)
        witnesses=self._verified_context_rows(db,account_id)
        known={w["witness_sha256"]:w for w in witnesses}
        for record in checked["stress_policy"]["context_history"]:
            witness=record["gate"]["witness"]
            if witness is not None and known.get(witness["witness_sha256"])!=witness:
                raise ValueError("A financial policy gate lost its retained published context.")

    def _verified_context_rows(self,db,account_id):
        with self._runtime_lease() as check:
            result=self._verified_context_data(db,account_id)
            check();return result

    @staticmethod
    def _sealed(prepared,published):
        clock=_now(datetime.fromisoformat(published))
        if clock<_now(datetime.fromisoformat(prepared["published_at"])):raise ValueError("Context publication clock moved backwards.")
        value=deepcopy(prepared)
        value.update(published_at=clock.isoformat(),data_available_at=clock.isoformat())
        value["witness_sha256"]=context._digest({k:v for k,v in value.items() if k!="witness_sha256"})
        return context.validate_witness(value)

    def _verified_context_data(self,db,account_id):
        if not self._exists(db):return []
        total=db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM opt_stress_contexts").fetchone()
        if not 0<=total[0]<=MAX_CONTEXTS or not 0<=total[1]<=MAX_CONTEXT_BYTES:raise ValueError("Stress context retention exceeds its fixed bound.")
        if db.execute("SELECT 1 FROM opt_stress_contexts WHERE status NOT IN ('running','completed','failed') OR bytes<=0 OR bytes>? OR length(receipt)>? OR length(request_id)>128 OR length(data_sha256)!=64 OR length(created_at)>40 LIMIT 1",(RESERVATION_BYTES,MAX_CONTEXT_RECEIPT)).fetchone():
            raise ValueError("Stress context row status/byte bounds changed.")
        rows=db.execute("SELECT request_id,account_id,data_sha256,status,created_at,bytes,length(receipt) actual,substr(receipt,1,?) receipt FROM opt_stress_contexts WHERE account_id=? AND status='completed' ORDER BY rowid LIMIT 101",(MAX_CONTEXT_RECEIPT+1,account_id)).fetchall()
        result=[]
        for row in rows:
            _identifier(row["request_id"])
            if not 0<row["actual"]<=MAX_CONTEXT_RECEIPT or len(row["receipt"].encode())>MAX_CONTEXT_RECEIPT:
                raise ValueError("Stress context receipt exceeds its bounded storage contract.")
            receipt=_load(row["receipt"]);base="reports/options-stress-context/"+row["request_id"]+"/"
            if (receipt.get("status")!="completed" or receipt.get("request_id")!=row["request_id"] or receipt.get("created_at")!=row["created_at"] or
                receipt.get("account_id")!=account_id or receipt.get("data_sha256")!=row["data_sha256"] or _raw(receipt)!=row["receipt"].encode()):
                raise ValueError("The completed context table/receipt changed.")
            artifacts=receipt.get("artifacts")
            if (type(artifacts) is not list or len(artifacts)!=3 or not all(type(a) is dict and set(a)=={"path","bytes","sha256"} and
                type(a["bytes"]) is int and 0<a["bytes"]<=context.MAX_RECEIPT_BYTES and context._hash(a["sha256"]) for a in artifacts) or
                {a.get("path") for a in artifacts}!={base+"prepared-witness.json",base+"source-receipt.json",base+"sealed-witness.json"}):
                raise ValueError("Published context artifact contract changed.")
            if row["bytes"]!=sum(a.get("bytes",-RESERVATION_BYTES) for a in artifacts)+len(row["receipt"].encode()):
                raise ValueError("Completed context bytes do not reconcile to retained artifacts and receipt.")
            saved={}
            for item in artifacts:
                raw=self.owner._read(item["path"],context.MAX_RECEIPT_BYTES)
                if len(raw)!=item["bytes"] or _digest(raw)!=item["sha256"]:raise ValueError("Published context file changed; no regeneration.")
                saved[item["path"][len(base):]]=raw
            prepared=context.validate_witness(_load(saved["prepared-witness.json"]))
            witness=self._sealed(prepared,receipt["published_at"])
            if _now(datetime.fromisoformat(receipt["created_at"]))>_now(datetime.fromisoformat(receipt["published_at"])):
                raise ValueError("Context publication predates its new local reservation.")
            original=_market_load(saved["source-receipt.json"])
            if (witness["data_sha256"]!=row["data_sha256"] or _digest(saved["source-receipt.json"])!=witness["source_receipt_original_sha256"] or
                context.receipt_projection(original)!=witness["source_receipt"] or receipt.get("witness_sha256")!=witness["witness_sha256"] or
                receipt.get("prepared_witness_sha256")!=prepared["witness_sha256"] or receipt.get("published_at")!=witness["published_at"] or
                saved["sealed-witness.json"]!=_raw(witness) or receipt.get("source_session")!=witness["source_session"] or
                receipt.get("label")!=witness["classification"]["label"] or receipt.get("research_only") is not True or
                receipt.get("execution_qualified") is not False or receipt.get("financial_account_changed") is not False or
                receipt.get("model_calls")!=0 or receipt.get("network_calls")!=0):
                raise ValueError("Published normalized context lost its original receipt/clock binding.")
            result.append(witness)
        return result

    def contexts(self,db,account_id):
        values=self._verified_context_rows(db,account_id)
        if self._benchmark_hold(db):return []
        if self._exists(db) and (db.execute("SELECT 1 FROM opt_stress_context_failures WHERE account_id=? LIMIT 1",(account_id,)).fetchone() or
            db.execute("SELECT 1 FROM opt_stress_contexts WHERE account_id=? AND status!='completed' LIMIT 1",(account_id,)).fetchone()):return []
        return values[-context.MAX_WITNESSES:]

    def _benchmark_hold(self,db):
        guard=getattr(self.owner,"benchmark_guard",None)
        if guard is not None:
            rows=guard(db)
            if type(rows) is not list:raise ValueError("Benchmark source guard returned invalid state.")
        elif db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qqq_runs'").fetchone():
            rows=[dict(v) for v in db.execute("SELECT request_id,action,status FROM qqq_runs WHERE status IN ('running','failed') ORDER BY rowid LIMIT 101")]
        else:rows=[]
        if not rows:return None
        item=rows[0]
        if type(item) is not dict:raise ValueError("Benchmark source guard returned invalid hold metadata.")
        return {"status":str(item.get("status","held"))[:40],"request_id":str(item.get("request_id",""))[:128],
            "reason":str(item.get("resolution_error") or "QQQ benchmark source has unresolved or running work; entries wait.")[:500]}

    def _source(self,db):
        present=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qqq_data'").fetchone()
        if not present:return None
        row=db.execute("SELECT digest,bytes,length(value) actual,substr(value,1,?) value FROM qqq_data WHERE id=1",(MAX_DATA+1,)).fetchone()
        if not row:return None
        if not 0<row["bytes"]<=MAX_DATA or row["actual"]!=row["bytes"]:raise ValueError("Accepted benchmark data size changed.")
        raw=bytes(row["value"])
        if len(raw)!=row["bytes"] or _digest(raw)!=row["digest"]:raise ValueError("Accepted benchmark data digest changed.")
        receipt=db.execute("SELECT request_id,bytes,receipt FROM qqq_runs WHERE status='completed' AND action IN ('refresh','resolve_history') AND json_valid(receipt) AND json_extract(receipt,'$.data_sha256')=? ORDER BY rowid DESC LIMIT 1",(row["digest"],)).fetchone()
        if not receipt or not 0<receipt["bytes"]<=context.MAX_RECEIPT_BYTES or len(receipt["receipt"].encode())!=receipt["bytes"]:
            raise ValueError("Accepted benchmark data has no matching completed source receipt.")
        parsed=_market_load(receipt["receipt"])
        if parsed.get("request_id")!=receipt["request_id"]:raise ValueError("The accepted benchmark receipt identity changed.")
        return {"raw":raw,"data_sha256":row["digest"],"receipt":parsed,"receipt_raw":_raw(parsed)}

    def _context_failure(self,request_id,account_id,error,clock):
        receipt={"request_id":request_id,"account_id":account_id,"status":"failed","reason":str(error)[:1000],
            "automatic_retry_allowed":False,"financial_account_changed":False,"recorded_at":clock.isoformat(),"model_calls":0,"network_calls":0}
        with self.owner.database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE opt_stress_contexts SET status='failed',receipt=? WHERE request_id=?",(_json(receipt),request_id))
            db.execute("INSERT INTO opt_stress_context_failures VALUES(?,?,?,?)",(request_id,account_id,clock.isoformat(),str(error)[:1000]))
        return receipt

    def capture_due(self,now=None):
        owner=self.owner;clock=_now(now)
        with owner.database() as db:
            try:cfg,brief=owner._policy(db)
            except HandoffConflict:return None
            if not self._exists(db):return None
            row=db.execute("SELECT id,state FROM opt_accounts WHERE closed_at IS NULL ORDER BY rowid DESC LIMIT 1").fetchone()
            if not row:return None
            active_schema=_load(row["state"]).get("schema")
            if active_schema not in (4,5):return None
            selected=self.owner.root_eod if active_schema==5 else self.eod
            if not selected.config(db)["enabled"]:return None
            account_id=row["id"]
            if db.execute("SELECT 1 FROM opt_stress_context_failures WHERE account_id=? LIMIT 1",(account_id,)).fetchone() or db.execute("SELECT 1 FROM opt_stress_contexts WHERE account_id=? AND status!='completed' LIMIT 1",(account_id,)).fetchone():return None
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qqq_data'").fetchone():return None
            data=db.execute("SELECT digest FROM qqq_data WHERE id=1").fetchone()
            if not data or db.execute("SELECT 1 FROM opt_stress_contexts WHERE account_id=? AND data_sha256=?",(account_id,data[0])).fetchone():return None
            dates=db.execute("SELECT json_extract(value,'$.symbols.QQQ.bars[#-1].date'),json_extract(value,'$.symbols.SPY.bars[#-1].date') FROM qqq_data WHERE id=1 AND bytes>0 AND bytes<=?",(MAX_DATA,)).fetchone()
            expected=context.last_closed_session(clock).isoformat()
            if not dates or tuple(dates)!=(expected,expected):return None
            if self._benchmark_hold(db):return None
        started=time.monotonic();request_id="context-"+uuid.uuid4().hex;reserved=False
        try:
            with owner._ownership(),self._runtime_lease() as runtime_check:
                with owner.database() as db:
                    db.execute("BEGIN IMMEDIATE");cfg,brief=owner._policy(db,cfg);feed=selected.config(db)
                    account=owner._account(db,clock)
                    if not account or account["id"]!=account_id or account["state"].get("schema")!=active_schema or account["state"]["hold"] or account["policy"]!=owner._epoch(db,cfg,brief):return None
                    basis=self._basis(account);self._verified_context_rows(db,account_id)
                    if db.execute("SELECT 1 FROM opt_stress_contexts WHERE account_id=? AND (data_sha256=? OR status!='completed')",(account_id,data[0])).fetchone():return None
                    used=db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM opt_stress_contexts").fetchone()
                    if used[0]>=MAX_CONTEXTS or used[1]+RESERVATION_BYTES>MAX_CONTEXT_BYTES:raise ValueError("Stress context retention is full; no deletion or extra quota.")
                    pending={"request_id":request_id,"account_id":account_id,"data_sha256":data[0],"status":"running","created_at":clock.isoformat(),"artifacts":[]}
                    db.execute("INSERT INTO opt_stress_contexts VALUES(?,?,?,?,?,?,?)",(request_id,account_id,data[0],"running",clock.isoformat(),_json(pending),RESERVATION_BYTES));reserved=True
                with owner.database() as db:
                    source=self._source(db)
                    if not source or source["data_sha256"]!=data[0]:raise HandoffConflict("The accepted benchmark changed before capture.")
                def checkpoint(db=None):
                    runtime_check()
                    if time.monotonic()-started>MAX_CAPTURE_SECONDS:raise ValueError("Local context capture exceeded its5second bound; no automatic replay.")
                    if db is None:
                        with owner.database() as current:checkpoint(current)
                        return
                    current,current_brief=owner._policy(db,cfg)
                    state=db.execute("SELECT state,policy FROM opt_accounts WHERE id=? AND closed_at IS NULL",(account_id,)).fetchone()
                    benchmark=self._source(db)
                    if (current_brief!=brief or selected.config(db)!=feed or owner._epoch(db,current,current_brief)!=account["policy"] or not state or
                        _digest(state["state"].encode())!=basis["state_sha256"] or _load(state["policy"])!=basis["policy"] or
                        self._benchmark_hold(db) or not benchmark or benchmark["raw"]!=source["raw"] or benchmark["receipt_raw"]!=source["receipt_raw"]):
                        raise HandoffConflict("Context account/source/permission changed during publication.")
                checkpoint()
                prepared=context.build_witness(_market_load(source["raw"]),source_receipt=source["receipt"],recorded_at=clock.isoformat())
                witness_raw=_raw(prepared);base="reports/options-stress-context/"+request_id;folder=owner.registry._path(base)
                artifacts=[]
                with owner.registry._directory(folder.parent,create=True):folder.mkdir()
                with owner.registry._directory(folder):
                    for name,raw in (("prepared-witness.json",witness_raw),("source-receipt.json",source["receipt_raw"])):
                        publish_bytes(folder/name,raw,checkpoint=checkpoint)
                        item={"path":base+"/"+name,"bytes":len(raw),"sha256":_digest(raw)}
                        if owner._read(item["path"],context.MAX_RECEIPT_BYTES)!=raw:raise ValueError("New context evidence readback changed.")
                        artifacts.append(item)
                checkpoint()
                witness=self._sealed(prepared,_now(now).isoformat())
                with owner.registry._directory(folder):
                    sealed_raw=_raw(witness)
                    publish_bytes(folder/"sealed-witness.json",sealed_raw,checkpoint=checkpoint)
                    if owner._read(base+"/sealed-witness.json",context.MAX_RECEIPT_BYTES)!=sealed_raw:raise ValueError("Sealed context readback changed.")
                    artifacts.append({"path":base+"/sealed-witness.json","bytes":len(sealed_raw),"sha256":_digest(sealed_raw)})
                receipt={**pending,"status":"completed","published_at":witness["published_at"],"source_session":witness["source_session"],
                    "witness_sha256":witness["witness_sha256"],"prepared_witness_sha256":prepared["witness_sha256"],"label":witness["classification"]["label"],"artifacts":artifacts,
                    "research_only":True,"execution_qualified":False,"financial_account_changed":False,"model_calls":0,"network_calls":0}
                with owner._committing(brief) as db:
                    checkpoint(db)
                    for item in artifacts:
                        raw=owner._read(item["path"],context.MAX_RECEIPT_BYTES)
                        if len(raw)!=item["bytes"] or _digest(raw)!=item["sha256"]:raise ValueError("Context artifact changed before final commit.")
                    size=sum(a["bytes"] for a in artifacts)+len(_raw(receipt))
                    if size>RESERVATION_BYTES:raise ValueError("Context exceeded its reserved bytes.")
                    db.execute("UPDATE opt_stress_contexts SET status='completed',receipt=?,bytes=? WHERE request_id=?",(_json(receipt),size,request_id))
                return receipt
        except HandoffConflict as error:
            if not reserved:return None
            return self._context_failure(request_id,account_id,error,_now(now))
        except Exception as error:
            if not reserved:raise
            return self._context_failure(request_id,account_id,error,_now(now))

    def snapshot(self,db,now,account):
        benchmark_hold=self._benchmark_hold(db)
        schema=(account or {}).get("state",{}).get("schema")
        if not account or schema not in (4,5):
            eligible=False
            if account and account["state"].get("schema")==3:
                try:
                    cfg,brief=self.owner._policy(db)
                    eligible=(account["policy"]==self.owner._epoch(db,cfg,brief))
                    engine.migrate_flat(account["state"],activated_at=now.isoformat(),old_state_sha256=_digest(_raw(account["state"])),old_data_sha256=self._prefix(db))
                except (ValueError,HandoffConflict):eligible=False
            return {"active":False,"migration_eligible":eligible,"mode":"inactive","policy_id":engine.STRATEGY_ID,
                "rule":engine.RULE,"rule_sha256":engine.RULE_SHA256,"entry_allowed":False,"last_context":None,
                "context_hold":benchmark_hold,"context_count":0,"reason":"No schema4 stress policy is active."}
        selected=self.owner.root_eod if schema==5 else self.eod
        policy_engine=engine
        if schema==5:
            from . import options_root_eod as policy_engine
        feed=selected.config(db)
        failure=db.execute("SELECT reason FROM opt_stress_context_failures WHERE account_id=? ORDER BY rowid DESC LIMIT 1",(account["id"],)).fetchone()
        uncertain=db.execute("SELECT 1 FROM opt_stress_contexts WHERE account_id=? AND status!='completed' LIMIT 1",(account["id"],)).fetchone()
        row=db.execute("SELECT receipt FROM opt_stress_contexts WHERE account_id=? AND status='completed' ORDER BY rowid DESC LIMIT 1",(account["id"],)).fetchone()
        last=_load(row[0]) if row else None
        expected=context.last_closed_session(now).isoformat()
        ready=bool(last and last["source_session"]==expected and _now(datetime.fromisoformat(last["published_at"]))<=now)
        count=db.execute("SELECT count(*) FROM opt_stress_contexts WHERE account_id=? AND status='completed'",(account["id"],)).fetchone()[0]
        return {"active":True,"migration_eligible":False,"mode":"forward_stress_put_only","enabled":feed["enabled"],"policy_id":policy_engine.STRATEGY_ID,
            "rule":policy_engine.RULE,"rule_sha256":policy_engine.RULE_SHA256,"last_context":{k:last[k] for k in ("source_session","published_at","label","witness_sha256")} if last else None,
            "context_hold":benchmark_hold or ({"status":"failed" if failure else "uncertain","reason":failure[0] if failure else "Uncertain context capture requires inspection."} if failure or uncertain else None),
            "context_count":count,"expected_source_session":expected,
            "context_status":"held" if benchmark_hold or failure or uncertain else "missing" if not last else "stale" if not ready else "stress_ready" if last["label"]=="stress_unwind" else "not_stress",
            "reason":failure[0] if failure else "Uncertain local context capture requires inspection." if uncertain else
                "New exposure requires a full current decision/fill gate; exits remain independent.",
            "automatic_context_retry_allowed":not bool(failure or uncertain),"next_entry_requires_full_gate_check":True,
            "research_only":True,"execution_qualified":False,"new_funding":"0","monthly_budget_reset":False}
