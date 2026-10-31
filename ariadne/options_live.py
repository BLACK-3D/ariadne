"""One opt-in public AAPL EOD source on the existing shared paper account.

Collection is isolated from the offline parent. No account, order, credential,
funding or source-text execution interface exists here.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import threading
import time

from .config import _allowed_path
from .handoff import HandoffConflict, _load
from .internet import SETTINGS_KEY
from .options_workflow import _json, _now, _digest, MAX_DATA, MAX_EVENTS

DEFAULT = {"initialized":False,"enabled":False,"revision":0,"activation_at":None,
           "provider":"marketdata_aapl_eod_public_v1","symbol":"AAPL","timezone":"America/New_York","hour":9,"minute":35}


def retarget_pristine(state, *, activated_at):
    from .options_portfolio import validate_state
    from .options_recovery import _number
    state=validate_state(state)
    clock=_now(datetime.fromisoformat(activated_at))
    if clock<datetime.fromisoformat(state["last_decided_at"]):raise ValueError("Feed activation cannot move the decision clock backwards.")
    from .options_portfolio import STRATEGY_IDS
    if (state["symbol"]!="QQQ" or state["processed_count"] or state["trade_count"] or state["closed_trade_count"] or state["history"] or state["prefix_sha256"]!=hashlib.sha256(b"[]").hexdigest() or state["next_strategy"]!=0 or state["enabled_strategies"]!=list(STRATEGY_IDS) or
        any(state[key] is not None for key in ("position","pending","hold","last_daily_result","last_observed_at")) or not state["cap_qualified"] or
        any(_number(state[key])!=500 for key in ("cash","contributions","monthly_start_equity")) or any(_number(state[key]) for key in ("fees","monthly_committed")) or
        any(state["milestone_counts"].values()) or any(stats["entry_count"] or stats["closed_count"] or _number(stats["fees"]) or _number(stats["monthly_committed"]) or _number(stats["realized_pnl"],signed=True) or any(stats["milestone_counts"].values()) for stats in state["strategy_stats"].values())):
        raise ValueError("Only the pristine unobserved $500 QQQ portfolio can retarget; no funding or allowance reset.")
    state["symbol"]="AAPL"
    state["last_decided_at"]=clock.isoformat()
    return validate_state(state)


class OptionsFeedWorkflow:
    def __init__(self, owner):
        self.owner=owner
        self.lock=threading.RLock()
        self.processes=set()

    def stop(self):
        with self.lock:
            for process in tuple(self.processes):
                if process.poll() is None:process.kill()

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opt_feed_meta'").fetchone() is not None

    def _schema(self,db):
        db.execute("CREATE TABLE IF NOT EXISTS opt_feed_meta(id INTEGER PRIMARY KEY CHECK(id=1),config TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_feed_activation(account_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,old_state TEXT NOT NULL,old_sha256 TEXT NOT NULL,activated_at TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO opt_feed_meta VALUES(1,?)",(_json(DEFAULT),))

    def config(self,db):
        if not self._exists(db):return dict(DEFAULT)
        row=db.execute("SELECT config FROM opt_feed_meta WHERE id=1").fetchone()
        if not row:raise ValueError("The feed configuration is missing; no reset permitted.")
        cfg=_load(row[0])
        if (set(cfg)!=set(DEFAULT) or any(type(cfg[key]) is not bool for key in ("initialized","enabled")) or type(cfg["revision"]) is not int or cfg["revision"]<0 or
            any(cfg[key]!=DEFAULT[key] for key in ("provider","symbol","timezone","hour","minute")) or cfg["initialized"]!=(cfg["activation_at"] is not None) or cfg["enabled"] and not cfg["initialized"]):
            raise ValueError("The fixed public feed contract changed.")
        if cfg["activation_at"] is not None:_now(datetime.fromisoformat(cfg["activation_at"]))
        return cfg

    def verify_account(self,db,account_id,state):
        cfg=self.config(db)
        if not cfg["initialized"]:return
        row=db.execute("SELECT * FROM opt_feed_activation WHERE account_id=?",(account_id,)).fetchone()
        if not row or _digest(row["old_state"].encode())!=row["old_sha256"] or row["activated_at"]!=cfg["activation_at"]:
            raise ValueError("The public feed account witness is missing or changed.")
        initial=retarget_pristine(_load(row["old_state"]),activated_at=row["activated_at"])
        if state["schema"]!=2 or state["symbol"]!="AAPL" or state["started_at"]!=initial["started_at"] or state["portfolio_activation_at"]!=initial["portfolio_activation_at"] or state["migration"]!=initial["migration"] or datetime.fromisoformat(state["last_decided_at"])<datetime.fromisoformat(row["activated_at"]):
            raise ValueError("The feed lost its original account/rule/activation identity.")
        if state["processed_count"]==0 and state["trade_count"]==0 and any(state[key]!=initial[key] for key in ("cash","contributions","month","monthly_start_equity","monthly_committed","fees")):
            raise ValueError("An untouched feed account cannot change inherited funding or allowance.")

    def _policy(self,db,*,expected_feed_revision=None):
        if self.owner.eod.config(db)["initialized"]:
            raise HandoffConflict("The retained AAPL source is inactive after the Ford EOD migration; no fallback or second paper book.")
        cfg,brief=self.owner._policy(db)
        feed=self.config(db)
        internet=self.owner.store.setting(SETTINGS_KEY,None)
        if not feed["initialized"] or not feed["enabled"] or type(internet) is not dict or internet.get("enabled") is not True or type(internet.get("revision")) is not int:
            raise HandoffConflict("The public options feed or owner Internet permission is disabled.")
        if expected_feed_revision is not None and feed["revision"]!=expected_feed_revision:raise HandoffConflict("Feed permission changed.")
        return cfg,feed,internet,brief

    @staticmethod
    def _window(now):
        from .briefing import _tz
        from .qqq_data import is_session,session_close_utc,last_closed_session,exchange_day
        # Use the same bundled Chicago + one-hour exchange clock as qqq_data;
        # no host tz database or unsupported briefing timezone is required.
        zone=_tz("America/Chicago");day=exchange_day(now)
        slot=datetime(day.year,day.month,day.day,8,35,tzinfo=zone)
        # A late restart can still collect the same EOD observation. Leave a
        # five-minute buffer before today's close, beyond the frozen engine's
        # >60-second future-observation requirement and35-second worker bound.
        ready=is_session(day) and now>=slot and now<session_close_utc(day)-timedelta(minutes=5)
        return ready,last_closed_session(now),slot

    def snapshot(self,db,now):
        cfg=self.config(db)
        ready,expected,slot=self._window(now)
        row=db.execute("SELECT receipt FROM opt_runs WHERE action='feed_collect' ORDER BY rowid DESC LIMIT 1").fetchone() if self.owner._exists(db) else None
        accepted=db.execute("SELECT receipt FROM opt_runs WHERE action='feed_collect' AND status='completed' ORDER BY rowid DESC LIMIT 1").fetchone() if self.owner._exists(db) else None
        if accepted:self.owner._verify_artifacts(_load(accepted[0]))
        used=db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM opt_reports").fetchone() if self.owner._exists(db) else (0,0)
        capacity={"remaining_evidence_operations":max(0,100-used[0]),"remaining_evidence_bytes":max(0,128*1024*1024-used[1]),"limit_evidence_operations":100,"limit_evidence_bytes":128*1024*1024,"scope":"Shared retained source/research/archive reservations; failed reservations count. Collection stops for reviewed maintenance when full; no automatic deletion."}
        return {"config":cfg,"last_run":_load(row[0]) if row else None,"collection_window_open":ready,"expected_market_session":expected.isoformat(),
            "publication":"Previous session options EOD becomes historical after the next exchange session opens. Collection starts09:35Eastern;6AM briefs show last saved dated evidence.",
            "quote_scope":"Actual vendor EOD snapshots, not live quotes, independent verification or guaranteed fills. No account/key/paid feed.",
            "retention":capacity,
            "source_url":"https://www.marketdata.app/docs/api/authentication/","terms_url":"https://www.marketdata.app/terms/","model_calls":0}

    def configure(self,body,now=None):
        owner=self.owner;owner._arguments(body,{"enabled","acknowledge_feed"})
        if type(body["enabled"]) is not bool or body["acknowledge_feed"] is not True:raise ValueError("Explicitly acknowledge the public AAPL delayed EOD feed and choose enabled.")
        clock=_now(now)
        with owner._ownership():
            with owner.database() as db:cfg,brief=owner._policy(db)
            with owner._committing(brief) as db:
                owner._policy(db,cfg)
                if owner.eod.config(db)["initialized"]:
                    raise HandoffConflict("Ford EOD owns the existing shared account. The retained AAPL source cannot be reactivated.")
                replay=owner._reserve(db,"feed_config",body,None,clock)
                if replay:return replay
                self._schema(db);feed=self.config(db);account=owner._account(db)
                if not account or account["state"].get("schema")!=2:raise HandoffConflict("Start or retain the existing shared portfolio before enabling this feed.")
                if account["policy"]!=owner._epoch(db,cfg,brief):raise HandoffConflict("The account authority changed; no feed migration across a pause.")
                if not feed["initialized"]:
                    if not body["enabled"]:raise ValueError("The public feed has not been activated.")
                    if owner._data(db,forward=True):raise HandoffConflict("An accepted forward source already exists; no silent provider fallback.")
                    events=[_load(row[0]) for row in db.execute("SELECT value FROM opt_events WHERE account_id=?",(account["id"],))]
                    if len(events)>1 or any(event.get("type")!="portfolio_upgrade" for event in events):raise HandoffConflict("Prior forward activity blocks pristine retargeting.")
                    old=_json(account["state"]);state=retarget_pristine(account["state"],activated_at=clock.isoformat())
                    db.execute("INSERT INTO opt_feed_activation VALUES(?,?,?,?,?)",(account["id"],body["request_id"],old,_digest(old.encode()),clock.isoformat()))
                    db.execute("UPDATE opt_accounts SET state=? WHERE id=?",(_json(state),account["id"]))
                    if db.execute("SELECT count(*) FROM opt_events").fetchone()[0]>=MAX_EVENTS:raise ValueError("Options event history is full.")
                    db.execute("INSERT INTO opt_events(account_id,run_id,value) VALUES(?,?,?)",(account["id"],body["request_id"],_json({"type":"feed_activation","at":clock.isoformat(),"from_symbol":"QQQ","symbol":"AAPL","old_state_sha256":_digest(old.encode()),"simulated_funding_added":"0","monthly_budget_reset":False})))
                    feed.update(initialized=True,activation_at=clock.isoformat())
                feed.update(enabled=body["enabled"],revision=feed["revision"]+1)
                db.execute("UPDATE opt_feed_meta SET config=? WHERE id=1",(_json(feed),))
                receipt={"request_id":body["request_id"],"action":"feed_config","status":"completed","account_id":account["id"],"feed":feed,"simulated_funding_added":"0","monthly_budget_reset":False,"artifacts":[],"model_calls":0,"replayed":False}
                owner._finish(db,receipt)
        return receipt

    def collect(self,body,now=None,*,collector=None):
        owner=self.owner;owner._arguments(body);clock=_now(now);receipt=None
        ready,session,_=self._window(clock)
        if not ready:raise HandoffConflict("Wait for the09:35Eastern source publication window on an exchange session; no request made.")
        with owner._ownership():
            try:
                with owner.database() as db:
                    db.execute("BEGIN IMMEDIATE");cfg,feed,internet,brief=self._policy(db)
                    replay=owner._reserve(db,"feed_collect",body,"feed-"+session.isoformat(),clock)
                    if replay:return replay
                    account=owner._account(db)
                    if not account or account["state"]["symbol"]!="AAPL" or account["policy"]!=owner._epoch(db,cfg,brief):raise HandoffConflict("The feed account or paper permission epoch changed.")
                    if account["state"]["hold"]:raise HandoffConflict("The paper account is held; a new source cannot heal it.")
                    required=sorted({item["contract_id"] for item in (account["state"]["position"],account["state"]["pending"]) if item})
                    old=owner._data(db,forward=True)
                    receipt={"request_id":body["request_id"],"action":"feed_collect","status":"running","created_at":clock.isoformat(),"day":"feed-"+session.isoformat(),"artifacts":[],"model_calls":0,"replayed":False}
                def checkpoint():
                    with owner.database() as db:
                        current=self._policy(db)
                        if current!=(cfg,feed,internet,brief):raise HandoffConflict("Options/feed/Internet/brief permission changed during collection.")
                        if owner._epoch(db,current[0],current[3])!=account["policy"]:
                            raise HandoffConflict("The global paper permission epoch changed during collection; no work is adopted across a pause.")
                result=collector(check=checkpoint,now=clock,required_contracts=required) if collector else self._collect(cfg,feed,internet,brief,body["request_id"],required)
                checkpoint()
                if result.get("unavailable"):
                    if result.get("raw"):
                        evidence=result["raw"]
                        failed_raw=base64.b64decode(evidence["content_base64"],validate=True)
                        if len(failed_raw)>8*1024*1024 or len(failed_raw)!=evidence["bytes"] or _digest(failed_raw)!=evidence["sha256"]:
                            raise ValueError("Rejected source evidence lost its byte identity.")
                        receipt.update(source_sha256=evidence["sha256"],source_failed=True,dataset_adopted=False)
                        failure={"reason":str(result.get("reason","Source normalization failed"))[:1000],"raw_sha256":evidence["sha256"],"retrieved_at":evidence["retrieved_at"],"dataset_adopted":False,"trades_adopted":0}
                        def reject(db):
                            raise ValueError("The public options source failed validation; raw evidence retained, no quotes or fills adopted.")
                        owner._publish(cfg,brief,receipt,(("source.json",failed_raw),("failure.json",_json(failure).encode())),final=reject,extra_check=checkpoint)
                    raise ValueError("The public options source is unavailable; inspect its failed receipt before another attempt.")
                from .options_feed import normalize_chain
                raw=base64.b64decode(result["raw"]["content_base64"],validate=True)
                if len(raw)>8*1024*1024 or _digest(raw)!=result["raw"]["sha256"] or len(raw)!=result["raw"]["bytes"]:raise ValueError("Public source raw identity changed.")
                normalized=normalize_chain(raw,retrieved_at=result["raw"]["retrieved_at"],expected_session=session,required_contracts=required)
                if normalized["dataset"]!=result["dataset"]:raise ValueError("The child result differs from locally validated source bytes.")
                if normalized["provenance"]!=result["provenance"]:raise ValueError("The child quarantine/provenance differs from raw source validation.")
                dataset=deepcopy(result["dataset"])
                if old:
                    dataset["snapshots"]=deepcopy(old["dataset"]["snapshots"])+dataset["snapshots"]
                from .options_data import canonical,validate_dataset
                decision_clock=_now(now) if now is not None else _now()
                encoded=canonical(dataset)
                if len(encoded)>MAX_DATA:raise ValueError("Forward quote cache is full; preserve history for reviewed archival.")
                quality=validate_dataset(dataset,decision_clock)
                quality.update(as_of=dataset["as_of"],provider=dataset["source"]["provider"],source_url=dataset["source"]["url"],retrieved_at=dataset["source"]["retrieved_at"])
                owner._merge(old["dataset"] if old else None,dataset)
                from .options_portfolio import advance_paper
                advancement=advance_paper(dataset,account["state"],decided_at=decision_clock.isoformat())
                compact_provenance=deepcopy(normalized["provenance"])
                all_quarantined=compact_provenance["quarantined"]
                compact_provenance["quarantined"]=all_quarantined[:16]
                compact_provenance["quarantine_rows_omitted_from_receipt"]=max(0,len(all_quarantined)-16)
                compact_provenance["full_provenance_sha256"]=_digest(canonical(normalized["provenance"]))
                receipt.update(outcome="captured",data_sha256=_digest(encoded),previous_data_sha256=old["sha256"] if old else None,source_sha256=_digest(raw),source_observed_at=result["dataset"]["snapshots"][0]["observed_at"],retrieved_at=result["raw"]["retrieved_at"],paper_status=advancement["status"],paper_events=len(advancement["events"]),source="marketdata_aapl_eod_public_v1",provenance=compact_provenance)
                def final(db):
                    checkpoint()
                    current=owner._account(db)
                    if current!=account:raise HandoffConflict("The paper account changed before feed publication.")
                    if db.execute("SELECT count(*) FROM opt_events").fetchone()[0]+len(advancement["events"])>MAX_EVENTS:raise ValueError("Options event quota full.")
                    db.execute("UPDATE opt_accounts SET state=? WHERE id=?",(_json(advancement["state"]),account["id"]))
                    for event in advancement["events"]:db.execute("INSERT INTO opt_events(account_id,run_id,value) VALUES(?,?,?)",(account["id"],body["request_id"],_json(event)))
                    db.execute("INSERT OR REPLACE INTO opt_data VALUES(?,?,?,?,?)",(2,_digest(encoded),encoded,len(encoded),_json(quality)))
                owner._publish(cfg,brief,receipt,(("source.json",raw),("snapshot.json",canonical(result["dataset"])),("provenance.json",canonical(normalized["provenance"]))),final=final,extra_check=checkpoint)
                return receipt
            except Exception as error:
                owner._failed(receipt,error)
                raise

    def run_due(self,now=None):
        owner=self.owner;clock=_now(now);ready,session,_=self._window(clock)
        if not ready:return None
        with owner.database() as db:
            try:cfg,feed,internet,brief=self._policy(db)
            except HandoffConflict:return None
            revision=owner._meta(db)[0]
            if clock<datetime.fromisoformat(feed["activation_at"]):return None
            account=owner._account(db)
            if not account or account["state"]["hold"]:return None
            if db.execute("SELECT 1 FROM opt_runs WHERE day=? OR status IN ('running','failed') LIMIT 1",("feed-"+session.isoformat(),)).fetchone():return None
        try:return self.collect({"request_id":"options-feed-"+session.isoformat(),"expected_revision":revision},now)
        except HandoffConflict:return None

    def _collect(self, cfg, feed, internet, brief, request_id, required):
        script=_allowed_path(Path(__file__).resolve().parents[1]/"scripts/options_feed.py")
        folder=_allowed_path(self.owner.registry.settings.data_dir/"options-feed-collector")
        with self.owner.registry._directory(folder,create=True):
            retained=list(folder.iterdir())
            if len(retained)>8 or sum(path.lstat().st_size for path in retained)>32*1024*1024:
                raise ValueError("Collector scratch needs reviewed cleanup before another request.")
            output=folder/(request_id+".json")
            # Only one bounded scratch response survives until its guarded read;
            # it is removed after successful consumption, never used as a retry.
            flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_BINARY",0)
            descriptor=os.open(output,flags,0o600)
            owned=os.fstat(descriptor)
            env={key:os.environ[key] for key in ("SYSTEMROOT","WINDIR","COMSPEC","PATH") if key in os.environ}
            env.update(ARIADNE_OFFLINE="0",ARIADNE_OPTIONS_COLLECTOR="1",TEMP=str(folder),TMP=str(folder),HOME=str(folder),USERPROFILE=str(folder),PYTHONDONTWRITEBYTECODE="1")
            command=[sys.executable,"-I","-B",str(script),"worker","--data-dir",str(self.owner.registry.settings.data_dir),"--workspace",str(self.owner.registry.root),"--options-revision",str(cfg["revision"]),"--internet-revision",str(internet["revision"]),"--brief-revision",str(brief["revision"]),"--feed-revision",str(feed["revision"])]
            for identifier in required:command.extend(["--required-contract",identifier])
            process=None
            try:
                with os.fdopen(descriptor,"wb") as target:
                    process=subprocess.Popen(command,env=env,cwd=script.parent.parent,stdout=target,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                    with self.lock:self.processes.add(process)
                    deadline=time.monotonic()+35
                    while process.poll() is None:
                        if self.owner.stopped.is_set():raise ValueError("Options feed collection stopped with the host.")
                        with self.owner.database() as db:
                            current=self._policy(db)
                            if current!=(cfg,feed,internet,brief):raise HandoffConflict("Brief controls changed during market collection.")
                        if time.monotonic()>deadline or output.stat().st_size>12*1024*1024:
                            raise ValueError("Options feed collector exceeded its time/output bound.")
                        try:process.wait(timeout=.1)
                        except subprocess.TimeoutExpired:pass
                if process.returncode:raise ValueError("Options feed collector stopped without a verified result.")
                with self.owner.registry._directory(folder) as directory:
                    with os.fdopen(self.owner.registry._open_read(output,directory),"rb") as source:raw=source.read(12*1024*1024+1)
                if len(raw)>12*1024*1024:raise ValueError("Options feed collector result is oversized.")
                result=_load(raw)
                # Raw daily HTTP bytes are not retained as an ever-growing full
                # history. Accepted economic rows and source hashes are retained.
                return result
            finally:
                if process is not None and process.poll() is None:
                    process.kill();process.wait(timeout=5)
                if process is not None:
                    with self.lock:self.processes.discard(process)
                # This is unaccepted transport scratch, not a paper/report
                # artifact. Never replay it; remove only this run's own inode.
                try:
                    info=output.lstat()
                    if (info.st_dev,info.st_ino)==(owned.st_dev,owned.st_ino) and stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not getattr(info,"st_file_attributes",0)&0x400:
                        output.unlink()
                except FileNotFoundError:
                    pass

