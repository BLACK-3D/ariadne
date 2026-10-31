"""Owner-authorized daily QQQ research and forward-only, end-of-day paper ledger."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import time
import threading
import uuid
from decimal import Decimal, localcontext

from .atomic import publish_bytes
from .config import _allowed_path
from .etf_tools import _load
from .handoff import HandoffDesk, HandoffConflict, _identifier
from .internet import SETTINGS_KEY, internet_enabled
from .locking import InstanceLock

UTC = timezone.utc
MAX_DATA = 8*1024*1024
MAX_RUNS = 10000
MAX_EVENTS = 30000
MAX_RECEIPTS = 32*1024*1024
MAX_REPORTS = 64*1024*1024
DEFAULT = {"enabled":False,"paused":False,"revision":0,"activation_at":None,"timezone":"America/Chicago","hour":6}
DIAGNOSTIC_RULE_SHA256 = "4c9f2f46ceda832437603bd813c21e554186133822edc7431dd6753a32ea21f2"
DIAGNOSTIC_ENGINE_SHA256 = "c5e45e68c1b5d6619d7ec27530e5856392ce0bdd779fd3a483c77af8d9ef935b"
HISTORY_INPUT_BYTES = 12*1024*1024  # Existing collector transport bound.
HISTORY_INLINE_BYTES = 4000


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now(value=None):
    result = datetime.now(UTC) if value is None else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Use an aware workflow clock.")
    return result.astimezone(UTC)


def _diagnostic_fingerprint():
    from . import qqq_recovery
    if (qqq_recovery.RULE_SHA256 != DIAGNOSTIC_RULE_SHA256 or
            hashlib.sha256(_allowed_path(Path(qqq_recovery.__file__)).read_bytes()).hexdigest() != DIAGNOSTIC_ENGINE_SHA256):
        raise ValueError("Diagnostic history policy requires its reviewed QQQ rule and engine fingerprint.")


def compare_history(old, dataset):
    """Exact fixed-rule input comparison; no tolerance or owner operation.

    Volume and adjusted close are retained diagnostics, not inputs to this
    particular frozen signal/fill/accounting implementation. A rule or source
    change must explicitly requalify that dependency claim.
    """
    from . import qqq_recovery
    _diagnostic_fingerprint()
    qqq_recovery.validate_dataset(dataset)
    if old is not None: qqq_recovery.validate_dataset(old)
    volume, adjusted, protected = [], [], []
    counts = {}
    if old is not None:
        for key in ("schema","currency","price_basis","dividend_policy","calendar"):
            if old.get(key) != dataset.get(key):
                protected.append({"symbol":None,"date":None,"field":key,"before":old.get(key),"after":dataset.get(key)})
    for symbol in ("QQQ","SPY"):
        before = old["symbols"][symbol]["bars"] if old else []
        after = dataset["symbols"][symbol]["bars"]
        if len(after) < len(before):
            protected.append({"symbol":symbol,"date":None,"field":"history_length","before":len(before),"after":len(after)})
        for previous, current in zip(before, after):
            for key in previous:
                if previous[key] == current[key]: continue
                change = {"symbol":symbol,"date":previous["date"],"field":key,"before":previous[key],"after":current[key]}
                (volume if key == "volume" else adjusted if key == "adj_close" else protected).append(change)
        counts[symbol] = {"overlap_rows":min(len(before),len(after)),"old_rows":len(before),"new_rows":len(after),
            "appended_rows":max(0,len(after)-len(before)),"last_date":after[-1]["date"]}
    return {"schema":1,"kind":"fixed_qqq_history_comparison","rule_sha256":DIAGNOSTIC_RULE_SHA256,
        "engine_sha256":DIAGNOSTIC_ENGINE_SHA256,"accepted_data_sha256":_hash(old) if old else None,
        "candidate_data_sha256":_hash(dataset),"eligible":not protected,"counts":counts,
        "volume_changes":volume,"volume_change_count":len(volume),"volume_changes_sha256":_hash(volume),
        "adj_close_change_count":len(adjusted),"adj_close_changes_sha256":_hash(adjusted),
        "protected_change_count":len(protected),"protected_changes_sha256":_hash(protected),
        "protected_change_sample":protected[:20],"protected_sample_truncated":len(protected)>20,
        "tolerance_used":False,"policy":"Only volume/adj_close diagnostics may change. Dates, OHLC, dividends, split ratios and basis remain exact; no paper state, signal, fill or funding is adopted by this comparison."}


class QQQWorkflow(HandoffDesk):
    """No broker, orders, model, arbitrary URL or executable source text."""
    def __init__(self, registry, store, controls=None, publication_lease=None, history_checkpoint=None):
        super().__init__(registry, store)
        self.controls = controls or (lambda: {"enabled":False,"paused":True,"revision":0})
        self.publication_lease = publication_lease
        self.history_checkpoint = history_checkpoint
        self.stopped=threading.Event()
        self.process_lock=threading.RLock()
        self.processes=set()

    def stop(self):
        self.stopped.set()
        with self.process_lock:
            for process in tuple(self.processes):
                if process.poll() is None:process.kill()

    @contextmanager
    def _publication(self, brief):
        if self.publication_lease:
            with self.publication_lease(brief):yield
        else:
            if self.controls()!=brief:raise HandoffConflict("Brief permission changed.")
            yield

    @contextmanager
    def _committing(self, brief, *, commit_check=None):
        # Match the existing public-source publisher's MAIN -> BRIEF lock order.
        # Commit (or roll back) while the brief permission lease is still held.
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            with self._publication(brief):
                try:
                    yield db
                except BaseException:
                    db.rollback()
                    raise
                else:
                    if commit_check is not None:commit_check()
                    db.commit()

    def busy(self):
        with self.database() as db:
            if not self._exists(db):return False
            _,cfg=self._meta(db)
            if not cfg["enabled"] and not db.execute("SELECT 1 FROM qqq_runs WHERE status='running' LIMIT 1").fetchone():return False
        # An orphaned running receipt is a durable hold, not a live worker. It
        # must not suppress unrelated weather briefs after a process crash.
        lock=InstanceLock(_allowed_path(self.registry.settings.data_dir/"qqq-workflow.lock"))
        try:lock.acquire()
        except ValueError:return True
        else:
            lock.release()
            return False

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qqq_meta'").fetchone() is not None

    @staticmethod
    def _schema(db):
        db.execute("CREATE TABLE IF NOT EXISTS qqq_meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,config TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS qqq_data(id INTEGER PRIMARY KEY CHECK(id=1),digest TEXT NOT NULL,value BLOB NOT NULL,bytes INTEGER NOT NULL,quality TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS qqq_accounts(id TEXT PRIMARY KEY,state TEXT NOT NULL,started_at TEXT NOT NULL,closed_at TEXT,policy TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS qqq_events(id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,run_id TEXT NOT NULL,value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS qqq_runs(request_id TEXT PRIMARY KEY,action TEXT NOT NULL,day TEXT,digest TEXT NOT NULL,status TEXT NOT NULL,receipt TEXT NOT NULL,bytes INTEGER NOT NULL)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS qqq_daily ON qqq_runs(day) WHERE day IS NOT NULL")
        db.execute("CREATE TABLE IF NOT EXISTS qqq_reports(request_id TEXT PRIMARY KEY,bytes INTEGER NOT NULL,receipt TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS qqq_archives(request_id TEXT PRIMARY KEY,bytes INTEGER NOT NULL,receipt TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS qqq_history_resolutions(failed_request_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,failed_receipt_sha256 TEXT NOT NULL,resolution_receipt_sha256 TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS qqq_rate(day TEXT PRIMARY KEY,count INTEGER NOT NULL)")
        db.execute("INSERT OR IGNORE INTO qqq_meta VALUES(1,0,?)", (_json(DEFAULT),))

    def _meta(self, db):
        if not self._exists(db):
            return 0, dict(DEFAULT)
        row = db.execute("SELECT revision,config FROM qqq_meta WHERE id=1").fetchone()
        if not row:
            raise ValueError("QQQ metadata is missing; inspect without resetting it.")
        cfg = _load(row["config"].encode())
        if set(cfg) != set(DEFAULT) or any(type(cfg[k]) is not bool for k in ("enabled","paused")) or type(cfg["revision"]) is not int:
            raise ValueError("QQQ configuration is invalid.")
        return row["revision"], cfg

    @staticmethod
    def _cas(actual, expected):
        if type(expected) is not int or expected < 0:
            raise ValueError("expected_revision must be a nonnegative integer.")
        if actual != expected:
            raise HandoffConflict("QQQ state changed; refresh before another action.", revision=actual)

    def _policy(self, db, config=None, internet=None, *, online=False):
        self._unpaused(db)
        _, cfg = self._meta(db)
        brief = self.controls()
        if not cfg["enabled"] or cfg["paused"] or not brief["enabled"] or brief["paused"] or brief.get("execution_hold"):
            raise HandoffConflict("QQQ work is disabled or held by QQQ/briefing controls.")
        if config is not None and cfg != config:
            raise HandoffConflict("QQQ configuration changed during this operation.")
        if online:
            current = self.store.setting(SETTINGS_KEY, None)
            if not internet_enabled(self.store, default=False) or current != internet:
                raise HandoffConflict("Internet permission changed; market collection stopped.")
        return cfg, brief

    def _data(self, db):
        if not self._exists(db):
            return None
        row = db.execute("SELECT digest,substr(value,1,?) value,bytes,quality FROM qqq_data WHERE id=1", (MAX_DATA+1,)).fetchone()
        if not row:
            return None
        raw = bytes(row["value"])
        if len(raw) != row["bytes"] or len(raw) > MAX_DATA or hashlib.sha256(raw).hexdigest() != row["digest"]:
            raise ValueError("Accepted QQQ data failed its integrity/size check.")
        return {"dataset":_load(raw),"quality":_load(row["quality"].encode()),"sha256":row["digest"]}

    @staticmethod
    def _economic(row):
        return {key:value for key,value in row.items() if key not in ("adj_close","volume")}

    def _merge(self, old, dataset, *, include_review=False):
        review=compare_history(old,dataset)
        if not review["eligible"]:
            raise ValueError("Accepted economic history changed (including possible split/revision); explicit review is required.")
        return (dataset,review) if include_review else dataset

    def _history_authority(self, db, cfg, brief):
        row=db.execute("SELECT rowid,value FROM settings WHERE key='paused'").fetchone()
        return {"global_pause_rowid":row[0] if row else None,"global_pause_value_sha256":hashlib.sha256(row[1].encode()).hexdigest() if row else None,
            "qqq_config_sha256":_hash(cfg),"brief_controls_sha256":_hash(brief),"internet_setting_sha256":_hash(self.store.setting(SETTINGS_KEY,None))}

    def _history_read(self, relative, maximum):
        path=self.registry._path(relative)
        if self.registry._parts(relative)!=tuple(relative.split("/")) or "\\" in relative:
            raise ValueError("History evidence requires canonical workspace paths.")
        with self.registry._directory(path.parent) as directory:
            with os.fdopen(self.registry._open_read(path,directory),"rb") as source:raw=source.read(maximum+1)
        if len(raw)>maximum:raise ValueError("History evidence exceeds its fixed byte bound.")
        return raw

    def _verified_resolution(self, db, failed):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qqq_history_resolutions'").fetchone():return None
        link=db.execute("SELECT * FROM qqq_history_resolutions WHERE failed_request_id=?",(failed["request_id"],)).fetchone()
        if not link:return None
        if failed["status"]!="failed" or hashlib.sha256(failed["receipt"].encode()).hexdigest()!=link["failed_receipt_sha256"]:
            raise ValueError("History resolution no longer matches the unchanged failed receipt.")
        row=db.execute("SELECT action,status,receipt FROM qqq_runs WHERE request_id=?",(link["request_id"],)).fetchone()
        archived=db.execute("SELECT receipt,bytes FROM qqq_archives WHERE request_id=?",(link["request_id"],)).fetchone()
        if not row or row["action"]!="resolve_history" or row["status"]!="completed" or not archived or row["receipt"]!=archived["receipt"] or hashlib.sha256(row["receipt"].encode()).hexdigest()!=link["resolution_receipt_sha256"]:
            raise ValueError("History resolution completed-run/archive binding is invalid.")
        receipt=_load(row["receipt"].encode())
        prefix="reports/qqq-recovery/history-"+_identifier(link["request_id"])+"/"
        names={"accepted-before.json","candidate-bundle.json","comparison.json","failed-before.json","binding.json"}
        artifacts=receipt.get("artifacts")
        if (receipt.get("status")!="completed" or receipt.get("outcome")!="diagnostic_history_resolved" or
                receipt.get("failed_request_id")!=failed["request_id"] or receipt.get("failed_receipt_sha256")!=link["failed_receipt_sha256"] or
                type(artifacts) is not list or len(artifacts)!=len(names) or {item.get("path") for item in artifacts}!={prefix+name for name in names}):
            raise ValueError("History resolution artifact contract is invalid.")
        total=0;retained={}
        for item in artifacts:
            raw=self._history_read(item["path"],HISTORY_INPUT_BYTES)
            retained[item["path"].removeprefix(prefix)]=raw
            total+=len(raw)
            if len(raw)!=item.get("bytes") or hashlib.sha256(raw).hexdigest()!=item.get("sha256"):
                raise ValueError("History resolution evidence is missing or changed.")
            if item["path"].endswith("/failed-before.json") and raw!=failed["receipt"].encode():
                raise ValueError("History resolution did not retain the exact failed receipt.")
            if item["path"].endswith("/comparison.json"):
                review=_load(raw)
                if _hash(review)!=receipt.get("review_sha256") or review.get("eligible") is not True or review.get("protected_change_count")!=0 or review.get("rule_sha256")!=DIAGNOSTIC_RULE_SHA256 or review.get("engine_sha256")!=DIAGNOSTIC_ENGINE_SHA256:
                    raise ValueError("History resolution comparison binding is invalid.")
        if total!=archived["bytes"]:raise ValueError("History resolution evidence accounting changed.")
        comparison=_load(retained["comparison.json"])
        binding=_load(retained["binding.json"])
        original=db.execute("SELECT request_id,action,day,digest,status,receipt,bytes FROM qqq_runs WHERE request_id=?",(failed["request_id"],)).fetchone()
        saved=binding.get("binding",{})
        candidate=_load(retained["candidate-bundle.json"])
        if (binding.get("failed_record")!=dict(original) or saved.get("failed_record_sha256")!=_hash(dict(original)) or
                saved.get("failed_receipt_sha256")!=link["failed_receipt_sha256"] or
                hashlib.sha256(retained["accepted-before.json"]).hexdigest()!=comparison.get("accepted_data_sha256") or
                comparison.get("accepted_data_sha256")!=saved.get("accepted_data_sha256") or
                hashlib.sha256(retained["candidate-bundle.json"]).hexdigest()!=receipt.get("candidate_sha256") or
                _hash(candidate.get("dataset"))!=comparison.get("candidate_data_sha256") or
                comparison.get("candidate_data_sha256")!=receipt.get("data_sha256") or
                _hash(saved.get("authority"))!=saved.get("authority_sha256")):
            raise ValueError("History resolution source/failure/binding artifacts disagree.")
        return receipt

    def _unresolved(self, db):
        if not self._exists(db):return []
        unresolved=[]
        for row in db.execute("SELECT request_id,action,status,receipt FROM qqq_runs WHERE status IN ('running','failed') ORDER BY rowid"):
            try:
                if self._verified_resolution(db,row):continue
            except (ValueError,OSError,KeyError,TypeError) as error:
                unresolved.append({"request_id":row["request_id"],"action":row["action"],"status":row["status"],"resolution_error":str(error)[:500]})
            else:unresolved.append({key:row[key] for key in ("request_id","action","status")})
        return unresolved

    def _history_candidate(self, body):
        digest=body.get("candidate_sha256")
        if type(digest) is not str or len(digest)!=64 or set(digest)-set("0123456789abcdef"):
            raise ValueError("Use the exact lowercase candidate bundle SHA-256.")
        raw=self._history_read(body["candidate_path"],HISTORY_INPUT_BYTES)
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError("Reviewed candidate bundle changed.")
        bundle=_load(raw)
        if type(bundle) is not dict or set(bundle)!={"dataset","quality","raw_payloads"} or type(bundle["quality"]) is not dict or bundle["quality"].get("fresh") is not True:
            raise ValueError("Use one complete fresh fixed-collector review bundle.")
        from .qqq_data import verify_saved_bundle
        verify_saved_bundle(bundle)
        return raw,bundle

    def _history_snapshot(self, db, failed_request_id):
        revision,cfg=self._meta(db)
        data=self._data(db)
        if not data:raise ValueError("An existing accepted QQQ history is required.")
        row=db.execute("SELECT request_id,action,day,digest,status,receipt,bytes FROM qqq_runs WHERE request_id=?",(failed_request_id,)).fetchone()
        if not row or row["action"]!="refresh" or row["status"]!="failed":raise ValueError("Choose the exact failed QQQ refresh for separate history review.")
        failed=dict(row)
        if not 0<len(failed["receipt"].encode())==failed["bytes"]<=16000:
            raise ValueError("Failed QQQ receipt has inconsistent bounds.")
        parsed=_load(failed["receipt"].encode())
        if parsed.get("reason")!="Accepted economic history changed (including possible split/revision); explicit review is required.":
            raise ValueError("This resolver cannot excuse another failure reason.")
        account=self._account(db)
        events=[dict(row) for row in db.execute("SELECT id,run_id,value FROM qqq_events WHERE account_id=? ORDER BY id",(account["id"],))] if account else []
        if len(events)>MAX_EVENTS:raise ValueError("Existing event evidence exceeds its bound.")
        brief=self.controls()
        authority=self._history_authority(db,cfg,brief)
        binding={"revision":revision,"failed_request_id":failed_request_id,"failed_receipt_sha256":hashlib.sha256(failed["receipt"].encode()).hexdigest(),
            "failed_record_sha256":_hash(failed),"accepted_data_sha256":data["sha256"],"accepted_quality_sha256":_hash(data["quality"]),
            "account_id":account["id"] if account else None,"account_state_sha256":_hash(account["state"]) if account else None,
            "account_policy_sha256":_hash(account["policy"]) if account else None,"account_events_sha256":_hash(events),
            "authority":authority,"authority_sha256":_hash(authority)}
        return {"binding":binding,"cfg":cfg,"brief":brief,"data":data,"failed":failed,"account":account}

    def review_history(self, body, now=None):
        """Read-only plan; inspect a new candidate without reserving or clearing work."""
        if type(body) is not dict or set(body)!={"candidate_path","candidate_sha256","failed_request_id"}:
            raise ValueError("Use candidate_path, candidate_sha256 and failed_request_id.")
        _identifier(body["failed_request_id"])
        _,bundle=self._history_candidate(body)
        with self.database() as db:
            db.execute("PRAGMA query_only=ON")
            if not self._exists(db):raise ValueError("QQQ workflow is not initialized.")
            snapshot=self._history_snapshot(db,body["failed_request_id"])
            holds=self._unresolved(db)
        review=compare_history(snapshot["data"]["dataset"],bundle["dataset"])
        validated=self._fresh(bundle["dataset"],_now(now))
        binding=snapshot["binding"]
        account=snapshot["account"]
        if account:
            cursor=next((row for row in bundle["dataset"]["symbols"]["QQQ"]["bars"] if row["date"]==account["state"]["last_processed"]),None)
            if not cursor or Decimal(str(cursor["close"]))!=Decimal(account["state"]["last_mark_price"]):
                raise ValueError("The accepted paper cursor/mark cannot be rebased by metadata review.")
        eligible=review["eligible"] and len(holds)==1 and holds[0]["request_id"]==body["failed_request_id"] and not holds[0].get("resolution_error")
        resolve={**body,"expected_revision":binding["revision"],"failed_receipt_sha256":binding["failed_receipt_sha256"],
            "expected_data_sha256":binding["accepted_data_sha256"],"expected_account_id":binding["account_id"],
            "expected_account_state_sha256":binding["account_state_sha256"],"expected_account_policy_sha256":binding["account_policy_sha256"],
            "expected_events_sha256":binding["account_events_sha256"],"expected_review_sha256":_hash(review),"expected_authority_sha256":binding["authority_sha256"]}
        return {"schema":1,"kind":"qqq_history_resolution_review","eligible":eligible,"comparison":review,"review_sha256":_hash(review),"validated_summary":validated,
            "binding":binding,"unresolved":holds,"resolve_body_without_request_id":resolve,"owner_changes":0,"model_calls":0,"network_calls":0,
            "scope":"Offline inspection only. A new exact resolution request is required; failed work and the paper account remain unchanged."}

    def _history_guard(self, db, expected, *, reserved=False, resolution_request_id=None):
        _diagnostic_fingerprint()
        if self.history_checkpoint is not None:self.history_checkpoint()
        current=self._history_snapshot(db,expected["failed_request_id"])
        before=copy.deepcopy(expected)
        if reserved:before["revision"]+=1
        if current["binding"]!=before:raise HandoffConflict("Reviewed QQQ source, failure, account or authority changed.")
        self._policy(db,current["cfg"])
        if self.store.setting("paused",None) is not False:raise HandoffConflict("Global autonomy pause is missing or active.")
        if current["account"] and current["account"]["policy"]!=self._epoch(db,current["cfg"],current["brief"]):
            raise HandoffConflict("Paper permission epoch changed; this resolver cannot reauthorize it.")
        unresolved=self._unresolved(db)
        permitted={expected["failed_request_id"]}
        if reserved:permitted.add(resolution_request_id)
        if any(row["request_id"] not in permitted or row.get("resolution_error") for row in unresolved):
            raise HandoffConflict("Another uncertain QQQ operation prevents history resolution.")
        return current

    def _history_artifacts(self, request_id, payloads, receipt, check):
        total=sum(len(raw) for raw in payloads.values())
        if any(len(raw)>HISTORY_INPUT_BYTES for raw in payloads.values()):raise ValueError("History artifact exceeds the existing collector bound.")
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE");check(db)
            used=db.execute("SELECT (SELECT coalesce(sum(bytes),0) FROM qqq_reports)+(SELECT coalesce(sum(bytes),0) FROM qqq_archives)").fetchone()[0]
            if used+total>MAX_REPORTS:raise ValueError("QQQ report/evidence quota is full.")
            db.execute("INSERT INTO qqq_archives VALUES(?,?,?)",(request_id,total,_json(receipt)))
        relative="reports/qqq-recovery/history-"+request_id
        folder=self.registry._path(relative)
        with self.registry._directory(folder.parent,create=True):folder.mkdir()
        def checkpoint():
            with self.database() as db:check(db)
        for name,raw in payloads.items():
            checkpoint()
            with self.registry._directory(folder):publish_bytes(folder/name,raw,checkpoint=checkpoint)
            if self._history_read(relative+"/"+name,HISTORY_INPUT_BYTES)!=raw:raise ValueError("History artifact readback changed.")
            receipt["artifacts"].append({"path":relative+"/"+name,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})

    def _history_verify_files(self, receipt):
        for item in receipt["artifacts"]:
            raw=self._history_read(item["path"],HISTORY_INPUT_BYTES)
            if len(raw)!=item["bytes"] or hashlib.sha256(raw).hexdigest()!=item["sha256"]:
                raise ValueError("Published history evidence changed before adoption.")

    def resolve_history(self, body, now=None):
        extra={"candidate_path","candidate_sha256","failed_request_id","failed_receipt_sha256","expected_data_sha256",
            "expected_account_id","expected_account_state_sha256","expected_account_policy_sha256","expected_events_sha256","expected_review_sha256","expected_authority_sha256"}
        self._arguments(body,extra)
        _identifier(body["failed_request_id"])
        if body["request_id"]==body["failed_request_id"]:raise ValueError("History resolution needs a new request ID; never replay the failed daily ID.")
        now=_now(now);receipt=None
        with self._ownership():
            raw,bundle=self._history_candidate(body)
            plan=self.review_history({key:body[key] for key in ("candidate_path","candidate_sha256","failed_request_id")},now)
            if not plan["eligible"]:raise HandoffConflict("This candidate has protected changes or another unresolved hold.")
            expected={**plan["resolve_body_without_request_id"],"request_id":body["request_id"]}
            if body!=expected:raise HandoffConflict("Resolution does not match the exact reviewed hashes/revision.")
            binding=plan["binding"]
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current=self._history_guard(db,binding)
                    if db.execute("SELECT 1 FROM qqq_runs WHERE request_id=?",(body["request_id"],)).fetchone():
                        raise HandoffConflict("A resolution ID is already reserved; no uncertain work is replayed.")
                    self._reserve(db,"resolve_history",body,None,now,inspected_failure=body["failed_request_id"])
                    receipt={"request_id":body["request_id"],"action":"resolve_history","status":"running","created_at":now.isoformat(),
                        "artifacts":[],"model_calls":0,"replayed":False,"failed_request_id":body["failed_request_id"],
                        "failed_receipt_sha256":body["failed_receipt_sha256"],"review_sha256":plan["review_sha256"],"paper_state_changed":False,"funding_added":"0"}
                def check(db):
                    # This operation's own running receipt is the only additional hold.
                    own=db.execute("SELECT status FROM qqq_runs WHERE request_id=?",(body["request_id"],)).fetchone()
                    if not own or own[0]!="running":raise HandoffConflict("Resolution ownership changed.")
                    self._history_guard(db,binding,reserved=True,resolution_request_id=body["request_id"])
                    if any(row["request_id"] not in (body["failed_request_id"],body["request_id"]) for row in self._unresolved(db)):
                        raise HandoffConflict("Another QQQ hold appeared.")
                    if self._history_read(body["candidate_path"],HISTORY_INPUT_BYTES)!=raw:raise HandoffConflict("Candidate file changed during resolution.")
                payloads={"accepted-before.json":_json(current["data"]["dataset"]).encode(),"candidate-bundle.json":raw,
                    "comparison.json":_json(plan["comparison"]).encode(),"failed-before.json":current["failed"]["receipt"].encode(),
                    "binding.json":_json({"binding":binding,"account":current["account"],"failed_record":current["failed"]}).encode()}
                self._history_artifacts(body["request_id"],payloads,receipt,check)
                def final_evidence():
                    _diagnostic_fingerprint()
                    if self.history_checkpoint is not None:self.history_checkpoint()
                    self._history_verify_files(receipt)
                    if self._history_read(body["candidate_path"],HISTORY_INPUT_BYTES)!=raw:
                        raise HandoffConflict("Candidate file changed before resolution commit.")
                with self._committing(current["brief"],commit_check=final_evidence) as db:
                    check(db)
                    self._history_verify_files(receipt)
                    self._fresh(bundle["dataset"],now,validated_summary=plan["validated_summary"])
                    payload=_json(bundle["dataset"]).encode()
                    if len(payload)>MAX_DATA:raise ValueError("Accepted QQQ data exceeds8MiB.")
                    digest=hashlib.sha256(payload).hexdigest()
                    if digest!=plan["comparison"]["candidate_data_sha256"]:raise ValueError("Validated candidate content changed before commit.")
                    db.execute("UPDATE qqq_data SET digest=?,value=?,bytes=?,quality=? WHERE id=1",(digest,payload,len(payload),_json(bundle["quality"])))
                    receipt.update(status="completed",outcome="diagnostic_history_resolved",data_sha256=digest,candidate_sha256=body["candidate_sha256"],
                        volume_change_count=plan["comparison"]["volume_change_count"],adj_close_change_count=plan["comparison"]["adj_close_change_count"],
                        scope="Exact metadata-only data adoption; original failed receipt/status and paper account/policy/events unchanged. No signal, fill, replay, reset or funding.")
                    self._finish(db,receipt)
                    db.execute("UPDATE qqq_archives SET receipt=? WHERE request_id=?",(_json(receipt),body["request_id"]))
                    db.execute("INSERT INTO qqq_history_resolutions VALUES(?,?,?,?)",(body["failed_request_id"],body["request_id"],body["failed_receipt_sha256"],hashlib.sha256(_json(receipt).encode()).hexdigest()))
                return receipt
            except Exception as error:
                if receipt:
                    with self.database() as db:
                        receipt.update(status="failed",reason=str(error)[:1500]);self._finish(db,receipt)
                        db.execute("UPDATE qqq_archives SET receipt=? WHERE request_id=?",(_json(receipt),body["request_id"]))
                raise

    def _fresh(self, dataset, clock, *, validated_summary=None):
        from .qqq_data import last_closed_session
        from .qqq_recovery import validate_dataset
        summary=validate_dataset(dataset) if validated_summary is None else validated_summary
        if summary["last_date"]!=last_closed_session(clock).isoformat() or any(summary["excluded_incomplete_or_future_bars"].values()):
            raise ValueError("Accepted data must contain exactly the latest finalized session and no unfinished bars.")
        if datetime.fromisoformat(dataset["as_of"])>clock:
            raise ValueError("A market snapshot cannot be dated in the future.")
        for entry in dataset["symbols"].values():
            if datetime.fromisoformat(entry["source"]["retrieved_at"])>clock:
                raise ValueError("A market source retrieval cannot be in the future.")
        return summary

    def _epoch(self, db, cfg, brief):
        row=db.execute("SELECT rowid,value FROM settings WHERE key='paused'").fetchone()
        return {"global_pause_rowid":row[0] if row else None,"qqq_revision":cfg["revision"],
                "brief_revision":brief["revision"],"internet":self.store.setting(SETTINGS_KEY,None)}

    def _account(self, db):
        row = db.execute("SELECT id,state,started_at,policy FROM qqq_accounts WHERE closed_at IS NULL ORDER BY rowid DESC LIMIT 1").fetchone() if self._exists(db) else None
        if not row:return None
        state=_load(row["state"].encode())
        from .qqq_recovery import _state
        with localcontext() as context:
            context.prec=40
            _state(state)
        return {"id":row["id"],"state":state,"started_at":row["started_at"],"policy":_load(row["policy"].encode())}

    def _verified_report(self, receipt):
        identifier=_identifier(receipt["request_id"])
        expected={"reports/qqq-recovery/"+identifier+"/backtest.json","reports/qqq-recovery/"+identifier+"/accepted-input.json"}
        artifacts=receipt.get("artifacts")
        if receipt.get("status")!="completed" or type(artifacts) is not list or len(artifacts)!=2 or {item.get("path") for item in artifacts}!=expected:
            raise ValueError("Backtest artifact contract is incomplete.")
        accepted=None
        for item in artifacts:
            path=self.registry._path(item["path"])
            with self.registry._directory(path.parent) as directory:
                with os.fdopen(self.registry._open_read(path,directory),"rb") as source:raw=source.read(8*1024*1024+1)
            if len(raw)!=item["bytes"] or len(raw)>8*1024*1024 or hashlib.sha256(raw).hexdigest()!=item["sha256"]:
                raise ValueError("Historical backtest artifact is missing or changed; do not adopt or regenerate it.")
            if path.name=="accepted-input.json" and hashlib.sha256(raw).hexdigest()!=receipt["data_sha256"]:
                raise ValueError("Historical backtest input identity changed.")
            if path.name=="accepted-input.json":accepted=_load(raw)
            if path.name=="backtest.json":
                from .qqq_recovery import RULE_SHA256
                report=_load(raw)
                if report.get("rule_sha256")!=RULE_SHA256 or report.get("accepted_input_sha256")!=receipt["data_sha256"]:
                    raise ValueError("The historical backtest belongs to another rule/input version.")
        return accepted

    def status(self, now=None):
        from .qqq_recovery import RULE, RULE_SHA256
        now = _now(now)
        with self.database() as db:
            revision, cfg = self._meta(db)
            data = self._data(db)
            account = self._account(db)
            runs = [dict(row) for row in db.execute("SELECT request_id,action,day,status FROM qqq_runs ORDER BY rowid DESC LIMIT 10")] if self._exists(db) else []
            last = db.execute("SELECT receipt FROM qqq_runs ORDER BY rowid DESC LIMIT 1").fetchone() if self._exists(db) else None
            uncertain=self._unresolved(db)
            hold=uncertain[0] if uncertain else None
            report = db.execute("SELECT qqq_reports.receipt FROM qqq_reports JOIN qqq_runs USING(request_id) WHERE qqq_runs.status='completed' ORDER BY qqq_reports.rowid DESC LIMIT 1").fetchone() if self._exists(db) else None
            events = [json.loads(row[0]) for row in db.execute("SELECT value FROM qqq_events WHERE account_id=? ORDER BY id DESC LIMIT 20",(account["id"],))] if account else []
            authority=self._history_authority(db,cfg,self.controls()) if self._exists(db) else None
            latest_resolution=None
            if self._exists(db) and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qqq_history_resolutions'").fetchone():
                resolved=db.execute("SELECT old.request_id,old.action,old.status,old.receipt FROM qqq_history_resolutions links JOIN qqq_runs old ON old.request_id=links.failed_request_id JOIN qqq_runs done ON done.request_id=links.request_id ORDER BY done.rowid DESC LIMIT 1").fetchone()
                if resolved:
                    try:latest_resolution=self._verified_resolution(db,resolved)
                    except (ValueError,OSError,KeyError,TypeError):pass
        historical=json.loads(report[0]) if report else None
        if historical:self._verified_report(historical)
        quality = copy.deepcopy(data["quality"]) if data else None
        if quality:
            from .qqq_data import last_closed_session
            expected = last_closed_session(now).isoformat()
            quality["expected_last_session_now"] = expected
            quality["fresh_now"] = all(rows["bars"][-1]["date"] == expected for rows in data["dataset"]["symbols"].values())
            quality["retrieved_at"] = data["dataset"]["as_of"]
        return {"schema":1,"revision":revision,"config":cfg,"rule":RULE,"rule_sha256":RULE_SHA256,
            "quality":quality,"data_sha256":data["sha256"] if data else None,"paper":account,"events":events,
            "last_run":json.loads(last[0]) if last else None,"history":runs,"hold":dict(hold) if hold else None,
            "latest_history_resolution":latest_resolution,"history_authority":authority,"history_authority_sha256":_hash(authority) if authority else None,
            "backtest":historical,"model_calls":0,"brokerage_connected":False,"internet_enabled":internet_enabled(self.store,default=False),
            "scope":"Simulated $10,000 account, not owner capital. Next-open fills observed from completed daily bars, not broker executions. Cash dividends credited on ex-date, not actual pay-date. No real orders or future-profit qualification."}

    def configure(self, body, now=None):
        if type(body) is not dict or set(body) != {"enabled","paused","expected_revision"} or any(type(body[k]) is not bool for k in ("enabled","paused")):
            raise ValueError("Use enabled, paused and expected_revision only.")
        now = _now(now)
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE");self._unpaused(db);self._schema(db)
            revision, cfg = self._meta(db);self._cas(revision,body["expected_revision"])
            if body["enabled"] and (not cfg["enabled"] or cfg["paused"] and not body["paused"]):
                cfg["activation_at"] = now.isoformat()
            cfg.update(enabled=body["enabled"],paused=body["paused"],revision=cfg["revision"]+1)
            db.execute("UPDATE qqq_meta SET revision=?,config=? WHERE id=1",(revision+1,_json(cfg)))
        return self.status(now)

    @contextmanager
    def _ownership(self):
        lock = InstanceLock(_allowed_path(self.registry.settings.data_dir/"qqq-workflow.lock"))
        try:
            lock.acquire()
        except ValueError:
            raise HandoffConflict("Another QQQ operation owns the workflow; inspect its status.") from None
        try:
            yield
        finally:
            lock.release()

    def _arguments(self, body, extra=()):
        if type(body) is not dict or set(body) != {"request_id","expected_revision",*extra}:
            raise ValueError("Use the documented QQQ operation fields only.")
        _identifier(body["request_id"])
        if type(body["expected_revision"]) is not int or body["expected_revision"] < 0:
            raise ValueError("Use a nonnegative expected_revision.")

    def _reserve(self, db, action, body, day, now, *, inspected_failure=None):
        self._schema(db)
        old = db.execute("SELECT digest,status,receipt FROM qqq_runs WHERE request_id=?",(body["request_id"],)).fetchone()
        digest = _hash({"action":action,"body":body})
        if old:
            if old["digest"] != digest or old["status"] != "completed":
                raise HandoffConflict("Prior QQQ request differs or is uncertain; it will not replay.")
            saved=json.loads(old["receipt"])
            if action=="backtest":self._verified_report(saved)
            return {**saved,"replayed":True}
        uncertain=self._unresolved(db)
        if any(row["request_id"]!=inspected_failure or row["status"]!="failed" or row.get("resolution_error") for row in uncertain):
            raise HandoffConflict("An uncertain QQQ operation requires explicit inspection and acknowledgement.")
        if day and db.execute("SELECT 1 FROM qqq_runs WHERE day=?",(day,)).fetchone():
            raise HandoffConflict("This daily market update already owns the day.")
        total = db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM qqq_runs").fetchone()
        if total[0] >= MAX_RUNS or total[1]+16000 > MAX_RECEIPTS:
            raise ValueError("QQQ receipt quota is full; no history was deleted.")
        revision,cfg = self._meta(db);self._cas(revision,body["expected_revision"])
        receipt={"request_id":body["request_id"],"action":action,"day":day,"status":"running","created_at":now.isoformat(),"artifacts":[],"model_calls":0,"replayed":False}
        raw=_json(receipt)
        db.execute("INSERT INTO qqq_runs VALUES(?,?,?,?,?,?,?)",(body["request_id"],action,day,digest,"running",raw,len(raw.encode())))
        db.execute("UPDATE qqq_meta SET revision=? WHERE id=1",(revision+1,))
        return None

    def _finish(self, db, receipt):
        raw=_json(receipt)
        if len(raw.encode())>16000:
            raise ValueError("QQQ compact receipt exceeds 16 KiB.")
        db.execute("UPDATE qqq_runs SET status=?,receipt=?,bytes=? WHERE request_id=?",(receipt["status"],raw,len(raw.encode()),receipt["request_id"]))
        db.execute("UPDATE qqq_meta SET revision=revision+1 WHERE id=1")

    def _collect(self, cfg, internet, brief, request_id):
        script=_allowed_path(Path(__file__).resolve().parents[1]/"scripts/qqq_market.py")
        folder=_allowed_path(self.registry.settings.data_dir/"qqq-collector")
        with self.registry._directory(folder,create=True):
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
            env.update(ARIADNE_OFFLINE="0",ARIADNE_QQQ_COLLECTOR="1",TEMP=str(folder),TMP=str(folder),HOME=str(folder),USERPROFILE=str(folder),PYTHONDONTWRITEBYTECODE="1")
            command=[sys.executable,"-I","-B",str(script),"worker","--data-dir",str(self.registry.settings.data_dir),"--workspace",str(self.registry.root),"--qqq-revision",str(cfg["revision"]),"--internet-revision",str(internet["revision"]),"--brief-revision",str(brief["revision"])]
            process=None
            try:
                with os.fdopen(descriptor,"wb") as target:
                    process=subprocess.Popen(command,env=env,cwd=script.parent.parent,stdout=target,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                    with self.process_lock:self.processes.add(process)
                    deadline=time.monotonic()+70
                    while process.poll() is None:
                        if self.stopped.is_set():raise ValueError("QQQ collection stopped with the host.")
                        with self.database() as db:
                            _, current_brief=self._policy(db,cfg,internet,online=True)
                            if current_brief!=brief:raise HandoffConflict("Brief controls changed during market collection.")
                        if time.monotonic()>deadline or output.stat().st_size>12*1024*1024:
                            raise ValueError("QQQ collector exceeded its time/output bound.")
                        try:process.wait(timeout=.1)
                        except subprocess.TimeoutExpired:pass
                if process.returncode:raise ValueError("QQQ collector stopped without a verified result.")
                with self.registry._directory(folder) as directory:
                    with os.fdopen(self.registry._open_read(output,directory),"rb") as source:raw=source.read(12*1024*1024+1)
                if len(raw)>12*1024*1024:raise ValueError("QQQ collector result is oversized.")
                result=_load(raw)
                # Raw daily HTTP bytes are not retained as an ever-growing full
                # history. Accepted economic rows and source hashes are retained.
                return result
            finally:
                if process is not None and process.poll() is None:
                    process.kill();process.wait(timeout=5)
                if process is not None:
                    with self.process_lock:self.processes.discard(process)
                # This is unaccepted transport scratch, not a paper/report
                # artifact. Never replay it; remove only this run's own inode.
                try:
                    info=output.lstat()
                    if (info.st_dev,info.st_ino)==(owned.st_dev,owned.st_ino) and stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not getattr(info,"st_file_attributes",0)&0x400:
                        output.unlink()
                except FileNotFoundError:
                    pass

    def refresh(self, body, now=None, *, day=None, collector=None):
        self._arguments(body)
        clock=(lambda:_now()) if now is None else (lambda fixed=_now(now):fixed)
        now=clock()
        with self._ownership():
            receipt=None
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE");self._unpaused(db);self._schema(db)
                    internet=self.store.setting(SETTINGS_KEY,None)
                    cfg,brief=self._policy(db,internet=internet,online=True)
                    replay=self._reserve(db,"refresh",body,day,now)
                    if replay:return replay
                    from .briefing import _tz
                    rate_day=now.astimezone(_tz("America/Chicago")).date().isoformat()
                    rate=db.execute("SELECT count FROM qqq_rate WHERE day=?",(rate_day,)).fetchone()
                    if rate and rate[0]>=4:raise ValueError("Market source limit is four refreshes per local day; automatic cadence is one.")
                    db.execute("INSERT INTO qqq_rate VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1",(rate_day,))
                    receipt={"request_id":body["request_id"],"action":"refresh","day":day,"status":"running","created_at":now.isoformat(),"artifacts":[],"model_calls":0,"replayed":False}
                bundle=(collector or self._collect)(cfg,internet,brief,body["request_id"])
                prepared=None
                if not bundle.get("unavailable") and bundle.get("quality",{}).get("fresh") is True:
                    with self.database() as db:
                        self._policy(db,cfg,internet,online=True)
                        previous=self._data(db);prior_account=self._account(db)
                        previous_digest=previous["sha256"] if previous else None
                        prior_account_sha=_hash(prior_account)
                        authority_sha=_hash(self._history_authority(db,cfg,brief))
                    dataset,comparison=self._merge(previous["dataset"] if previous else None,bundle["dataset"],include_review=True)
                    validated=self._fresh(dataset,clock())
                    def history_check(db):
                        _diagnostic_fingerprint()
                        _,current_brief=self._policy(db,cfg,internet,online=True)
                        current=self._data(db)
                        if (current_brief!=brief or self._meta(db)[0]!=body["expected_revision"]+1 or
                                (current["sha256"] if current else None)!=previous_digest or _hash(self._account(db))!=prior_account_sha or
                                _hash(self._history_authority(db,cfg,brief))!=authority_sha):
                            raise HandoffConflict("QQQ source/account/authority changed during diagnostic history publication.")
                    revision={key:value for key,value in comparison.items() if key!="volume_changes"}
                    volume_raw=_json(comparison["volume_changes"]).encode()
                    receipt["history_revision"]=revision
                    if len(volume_raw)<=HISTORY_INLINE_BYTES:
                        revision["volume_changes"]=comparison["volume_changes"]
                    else:
                        self._history_artifacts(body["request_id"],{"volume-revisions.json":volume_raw},receipt,history_check)
                        revision["volume_changes_artifact"]=receipt["artifacts"][-1]["path"]
                    prepared=(dataset,history_check,validated,comparison["candidate_data_sha256"])
                with self._committing(brief) as db:
                    _,current_brief=self._policy(db,cfg,internet,online=True)
                    if current_brief!=brief:raise HandoffConflict("Brief controls changed before market publication.")
                    if bundle.get("unavailable"):
                        receipt.update(status="completed",outcome="unavailable",reason=str(bundle.get("reason","Public data unavailable"))[:1000])
                        self._finish(db,receipt);return receipt
                    quality=bundle["quality"]
                    if quality.get("fresh") is not True:
                        receipt.update(status="completed",outcome="stale",reason="No fresh completed session; previous accepted data and paper state retained.")
                        self._finish(db,receipt);return receipt
                    dataset,history_check,validated,validated_digest=prepared
                    history_check(db)
                    self._history_verify_files(receipt)
                    self._fresh(dataset,clock(),validated_summary=validated)
                    raw=_json(dataset).encode()
                    if len(raw)>MAX_DATA:raise ValueError("QQQ accepted data exceeds 8 MiB.")
                    if hashlib.sha256(raw).hexdigest()!=validated_digest:raise ValueError("Validated market history changed before commit.")
                    account=self._account(db);advancement=None
                    if account:
                        if account["policy"]!=self._epoch(db,cfg,brief):
                            raise ValueError("Paper permission epoch changed; inspect and explicitly start a new experiment, never fill across a pause.")
                        from .qqq_recovery import advance_paper,next_session_open
                        advancement=advance_paper(dataset,account["state"],cost_bps=5,decided_at=clock().isoformat())
                        events=advancement["events"]
                        if advancement["status"]=="completed":
                            state=advancement["state"]
                            with localcontext() as context:
                                context.prec=40
                                equity=Decimal(state["equity"]);previous_equity=Decimal(account["state"]["equity"])
                                events.append({"type":"paper_daily_result","session":state["last_processed"],"equity":state["equity"],"cash":state["cash"],"shares":state["shares"],
                                    "day_pnl":str(equity-previous_equity),"day_return":str(equity/previous_equity-1),"cumulative_pnl":str(equity-Decimal("10000")),"cumulative_return":str(equity/Decimal("10000")-1),
                                    "total_fees":state["total_fees"],"total_dividends":state["total_dividends"],"simulated":True,"actual_broker_fill":False})
                        if db.execute("SELECT count(*) FROM qqq_events").fetchone()[0]+len(events)>MAX_EVENTS:raise ValueError("QQQ event history is full.")
                        db.execute("UPDATE qqq_accounts SET state=? WHERE id=?",(_json(advancement["state"]),account["id"]))
                        for event in events:db.execute("INSERT INTO qqq_events(account_id,run_id,value) VALUES(?,?,?)",(account["id"],body["request_id"],_json(event)))
                    digest=hashlib.sha256(raw).hexdigest()
                    db.execute("INSERT OR REPLACE INTO qqq_data VALUES(1,?,?,?,?)",(digest,raw,len(raw),_json(quality)))
                    receipt.update(status="completed",outcome="updated",data_sha256=digest,last_sessions={symbol:entry["bars"][-1]["date"] for symbol,entry in dataset["symbols"].items()},source_sha256={symbol:entry["source"]["sha256"] for symbol,entry in dataset["symbols"].items()},paper_status=advancement["status"] if advancement else "not_started",paper_events=len(advancement["events"]) if advancement else 0)
                    if advancement and advancement["state"]["pending"] and any(event["type"]=="paper_decision" for event in advancement["events"]):
                        if next_session_open(advancement["state"]["pending"]["signal_session"])<=clock()+timedelta(seconds=60):raise ValueError("The simulated decision missed its safe pre-open publication window.")
                    self._finish(db,receipt)
                    db.execute("UPDATE qqq_archives SET receipt=? WHERE request_id=?",(_json(receipt),body["request_id"]))
                return receipt
            except Exception as error:
                if receipt:
                    with self.database() as db:
                        receipt.update(status="failed",reason=str(error)[:1500]);self._finish(db,receipt)
                        db.execute("UPDATE qqq_archives SET receipt=? WHERE request_id=?",(_json(receipt),body["request_id"]))
                raise

    def backtest(self, body, now=None):
        self._arguments(body);now=_now(now)
        with self._ownership():
            receipt=None
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE");self._unpaused(db);self._schema(db)
                    cfg,brief=self._policy(db)
                    replay=self._reserve(db,"backtest",body,None,now)
                    if replay:return replay
                    receipt={"request_id":body["request_id"],"action":"backtest","status":"running","created_at":now.isoformat(),"artifacts":[],"model_calls":0,"replayed":False}
                    data=self._data(db)
                    if not data:raise ValueError("Refresh accepted market data before backtesting.")
                    if db.execute("SELECT count(*) FROM qqq_reports").fetchone()[0]>=100:raise ValueError("QQQ backtest count quota is full.")
                from .qqq_recovery import backtest
                report=backtest(data["dataset"],cost_bps=5,initial_cash="10000.00")
                report.update(request_id=body["request_id"],accepted_input_sha256=data["sha256"],scope="Historical research, no future winner or live execution qualification. Forward paper starts separately from cash.")
                payload=_json(report).encode()
                if len(payload)>8*1024*1024:raise ValueError("QQQ backtest report exceeds 8 MiB.")
                relative="reports/qqq-recovery/"+body["request_id"]
                folder=self.registry._path(relative)
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE");self._policy(db,cfg)
                    used=db.execute("SELECT (SELECT coalesce(sum(bytes),0) FROM qqq_reports)+(SELECT coalesce(sum(bytes),0) FROM qqq_archives)").fetchone()[0]
                    if used+len(payload)+MAX_DATA>MAX_REPORTS:raise ValueError("QQQ historical report quota is full.")
                    db.execute("INSERT INTO qqq_reports VALUES(?,?,?)",(body["request_id"],len(payload)+len(_json(data["dataset"]).encode()),_json(receipt)))
                def check():
                    with self.database() as db:self._policy(db,cfg)
                with self.registry._directory(folder.parent,create=True):
                    folder.mkdir()
                    with self.registry._directory(folder):
                        for name,raw in (("backtest.json",payload),("accepted-input.json",_json(data["dataset"]).encode())):
                            check();publish_bytes(folder/name,raw,checkpoint=check)
                            receipt["artifacts"].append({"path":relative+"/"+name,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
                            with self.registry._directory(folder) as directory:
                                with os.fdopen(self.registry._open_read(folder/name,directory),"rb") as source:readback=source.read(8*1024*1024+1)
                            if readback!=raw:raise ValueError("Backtest artifact readback failed.")
                with self._committing(brief) as db:
                    self._policy(db,cfg)
                    metrics=report["strategy"]["metrics"];benchmark=report["benchmarks"]["qqq_buy_and_hold"]["metrics"]
                    receipt.update(status="completed",outcome="historical_research",data_sha256=data["sha256"],metrics=metrics,benchmark_metrics=benchmark,trade_count=report["strategy"]["trade_count"],execution_qualified=False,
                        research_gate="not_supported_by_this_backtest" if metrics["total_return"]<=0 or metrics["cagr"]<=benchmark["cagr"] else "hypothesis_only_forward_evidence_required")
                    db.execute("UPDATE qqq_reports SET receipt=? WHERE request_id=?",(_json(receipt),body["request_id"]))
                    self._finish(db,receipt)
                return receipt
            except Exception as error:
                if receipt:
                    with self.database() as db:
                        receipt.update(status="failed",reason=str(error)[:1500]);self._finish(db,receipt)
                raise

    def start_paper(self, body, now=None):
        self._arguments(body,{"replace","acknowledge_simulated"})
        if type(body["replace"]) is not bool or body["acknowledge_simulated"] is not True:raise ValueError("Acknowledge simulated capital and explicitly choose whether to replace an old experiment.")
        now=_now(now)
        brief=self.controls()
        with self._ownership(),self._committing(brief) as db:
            self._unpaused(db);self._schema(db);self._policy(db)
            replay=self._reserve(db,"start_paper",body,None,now)
            if replay:return replay
            data=self._data(db)
            if not data or not data["quality"]["fresh"]:raise ValueError("Fresh accepted data is required to start paper trading.")
            from .qqq_data import last_closed_session
            if any(entry["bars"][-1]["date"]!=last_closed_session(now).isoformat() for entry in data["dataset"]["symbols"].values()):raise ValueError("Accepted data is stale now; refresh before starting.")
            tested=db.execute("SELECT qqq_reports.receipt FROM qqq_reports JOIN qqq_runs USING(request_id) WHERE qqq_runs.status='completed' ORDER BY qqq_reports.rowid DESC LIMIT 1").fetchone()
            if not tested:raise ValueError("Complete the historical backtest before starting a forward experiment.")
            tested_input=self._verified_report(json.loads(tested[0]))
            self._merge(tested_input,data["dataset"])
            account=self._account(db)
            if account and not body["replace"]:raise HandoffConflict("A paper account already exists; it was not reset.")
            if db.execute("SELECT count(*) FROM qqq_accounts").fetchone()[0]>=32:raise ValueError("Paper experiment history is full.")
            if account:db.execute("UPDATE qqq_accounts SET closed_at=? WHERE id=?",(now.isoformat(),account["id"]))
            from .qqq_recovery import initial_state
            state=initial_state(data["dataset"],now.isoformat(),initial_cash="10000.00")
            identifier="paper-"+uuid.uuid4().hex
            db.execute("INSERT INTO qqq_accounts VALUES(?,?,?,NULL,?)",(identifier,_json(state),now.isoformat(),_json(self._epoch(db,self._meta(db)[1],brief))))
            receipt={"request_id":body["request_id"],"action":"start_paper","status":"completed","account_id":identifier,"started_at":now.isoformat(),"simulated_initial_cash":"10000.00","artifacts":[],"model_calls":0,"replayed":False}
            self._finish(db,receipt)
        return receipt

    def acknowledge(self, body):
        self._arguments(body)
        with self._ownership(),self.database() as db:
            db.execute("BEGIN IMMEDIATE");self._unpaused(db)
            revision,cfg=self._meta(db);self._cas(revision,body["expected_revision"])
            row=db.execute("SELECT receipt,status FROM qqq_runs WHERE request_id=?",(body["request_id"],)).fetchone()
            if not row or row["status"] not in ("running","failed"):raise ValueError("Choose an uncertain QQQ operation to acknowledge.")
            receipt=json.loads(row["receipt"])
            receipt.update(status="acknowledged",acknowledged_at=_now().isoformat(),scope="Failure inspected; no partial files, signals or fills adopted, replayed or deleted.")
            self._finish(db,receipt);db.execute("UPDATE qqq_meta SET revision=? WHERE id=1",(revision+1,))
        return receipt

    def archive(self, body, now=None):
        """Explicitly close a simulation; optionally retain then clear its data basis."""
        self._arguments(body,{"reason","archive_data"})
        if type(body["reason"]) is not str or not 1<=len(body["reason"].strip())<=1000 or any(ord(c)<32 and c not in "\n\t" for c in body["reason"]) or type(body["archive_data"]) is not bool:
            raise ValueError("Supply an archive reason and explicit archive_data boolean.")
        now=_now(now);receipt=None
        with self._ownership():
            try:
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE");self._unpaused(db);self._schema(db)
                    _,cfg=self._meta(db)
                    replay=self._reserve(db,"archive",body,None,now)
                    if replay:return replay
                    account=self._account(db);data=self._data(db)
                    if not account and not (data and body["archive_data"]):raise ValueError("There is no active paper experiment or requested data basis to archive.")
                    receipt={"request_id":body["request_id"],"action":"archive","status":"running","reason":body["reason"].strip(),"artifacts":[],"model_calls":0,"replayed":False,"synthetic_liquidation":False}
                    payload=_json({"schema":1,"archived_at":now.isoformat(),"reason":body["reason"].strip(),"account":account,"data":data if body["archive_data"] else None,"scope":"Retained historical evidence, not a new signal, fill or liquidation."}).encode()
                    used=db.execute("SELECT (SELECT coalesce(sum(bytes),0) FROM qqq_reports)+(SELECT coalesce(sum(bytes),0) FROM qqq_archives)").fetchone()[0]
                    if len(payload)>10*1024*1024 or used+len(payload)>MAX_REPORTS:raise ValueError("The archive/report budget is full; nothing was removed.")
                    db.execute("INSERT INTO qqq_archives VALUES(?,?,?)",(body["request_id"],len(payload),_json(receipt)))
                relative="reports/qqq-recovery/archive-"+body["request_id"]+"/evidence.json"
                path=self.registry._path(relative)
                def check():
                    with self.database() as db:
                        self._unpaused(db)
                        if self._meta(db)[1]!=cfg:raise HandoffConflict("QQQ controls changed during archive.")
                with self.registry._directory(path.parent,create=True):publish_bytes(path,payload,checkpoint=check)
                with self.registry._directory(path.parent) as directory:
                    with os.fdopen(self.registry._open_read(path,directory),"rb") as source:readback=source.read(10*1024*1024+1)
                if readback!=payload:raise ValueError("QQQ archive readback failed; original data retained.")
                receipt["artifacts"]=[{"path":relative,"bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest()}]
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE");self._unpaused(db)
                    if self._meta(db)[1]!=cfg:raise HandoffConflict("QQQ controls changed before archive commit.")
                    if account:db.execute("UPDATE qqq_accounts SET closed_at=? WHERE id=?",(now.isoformat(),account["id"]))
                    if body["archive_data"]:db.execute("DELETE FROM qqq_data WHERE id=1")
                    receipt.update(status="completed",account_id=account["id"] if account else None,active_data_archived=body["archive_data"])
                    db.execute("UPDATE qqq_archives SET receipt=? WHERE request_id=?",(_json(receipt),body["request_id"]))
                    self._finish(db,receipt)
                return receipt
            except Exception as error:
                if receipt:
                    with self.database() as db:
                        receipt.update(status="failed",reason=str(error)[:1500]);self._finish(db,receipt)
                raise

    def run_due(self, now=None):
        now=_now(now)
        from .briefing import _tz
        with self.database() as db:
            revision,cfg=self._meta(db)
            if not cfg["enabled"] or cfg["paused"] or self.store.setting("paused",False):return None
            brief=self.controls()
            if not brief["enabled"] or brief["paused"] or brief.get("execution_hold") or not internet_enabled(self.store,default=False):return None
            local=now.astimezone(_tz(cfg["timezone"]))
            slot=local.replace(hour=6,minute=0,second=0,microsecond=0)
            if now<slot or cfg["activation_at"] and slot<_now(datetime.fromisoformat(cfg["activation_at"])):return None
            if brief.get("activation_at") and slot<datetime.fromisoformat(brief["activation_at"]):return None
            day=local.date().isoformat()
            if self._exists(db) and (db.execute("SELECT 1 FROM qqq_runs WHERE day=? LIMIT 1",(day,)).fetchone() or self._unresolved(db)):return None
        # The due clock chooses the slot; publication uses its actual clock after
        # the network worker finishes, not the earlier scheduler timestamp.
        try:return self.refresh({"request_id":"qqq-daily-"+day,"expected_revision":revision},day=day)
        except HandoffConflict:return None

    def brief_snapshot(self, now=None):
        status=self.status(now)
        paper=status["paper"]
        return {key:status[key] for key in ("config","quality","hold","last_run","backtest","model_calls","scope","latest_history_resolution")} | {"paper":paper,"events":status["events"][:10],"rule_sha256":status["rule_sha256"]}
