"""One explicit total-bankroll upgrade; old KO/F ledgers remain sealed.

Only the normal selected KO source collects quotes. This host has no network,
broker, model or automatic contribution interface.
"""
from copy import deepcopy
from datetime import datetime

from .handoff import HandoffConflict, _identifier, _load
from .options_workflow import _json, _digest, _now, MAX_DATA, MAX_EVENTS

MIGRATION_FILES={"previous-state.json","initial-state.json","activation.json","source-cutoff.json"}
CAPTURE_FILES={"before-state.json","bundle.json","snapshot.json","provenance.json","contexts.json","paper.json"}


def _raw(value):return _json(value).encode()
def _hash(value):return type(value) is str and len(value)==64 and all(c in "0123456789abcdef" for c in value)
def _basis(account):return {k:account[k] for k in ("id","state","started_at","policy")}


class OptionsBankrollWorkflow:
    def __init__(self,owner):self.owner=owner

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opt_bankroll_migrations'").fetchone() is not None

    @staticmethod
    def _schema(db):
        db.execute("CREATE TABLE IF NOT EXISTS opt_bankroll_migrations(account_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,old_state TEXT NOT NULL,old_state_sha256 TEXT NOT NULL,old_data_sha256 TEXT NOT NULL,initial_state TEXT NOT NULL,initial_state_sha256 TEXT NOT NULL,activated_at TEXT NOT NULL,source_config TEXT NOT NULL,policy TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_bankroll_state(account_id TEXT PRIMARY KEY,request_id TEXT NOT NULL,state_sha256 TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_bankroll_data(source_date TEXT PRIMARY KEY,digest TEXT NOT NULL,value BLOB NOT NULL,bytes INTEGER NOT NULL,request_id TEXT UNIQUE NOT NULL)")

    def cutoff(self,db,account_id):
        """Bound the immutable old5 source tables/anchor, never current money."""
        rows=db.execute("SELECT source_date,digest,bytes,request_id FROM opt_root_data ORDER BY source_date LIMIT 101").fetchall()
        if len(rows)>100 or db.execute("SELECT count(*) FROM opt_root_data").fetchone()[0]!=len(rows):
            raise ValueError("The original KO source cutoff exceeds its bound.")
        anchor=db.execute("SELECT * FROM opt_root_state WHERE account_id=?",(account_id,)).fetchone()
        if not anchor:raise ValueError("The original KO state anchor is missing.")
        value={"schema":1,"account_id":account_id,"legacy_f_source_sha256":self.owner.eod.stress._prefix(db),
            "root_state_anchor":dict(anchor),"root_sources":[dict(r) for r in rows]}
        if len(_raw(value))>64*1024:raise ValueError("The original source cutoff exceeds its metadata bound.")
        return value

    def binding(self,db,account):
        row=db.execute("SELECT state FROM opt_accounts WHERE id=? AND closed_at IS NULL",(account["id"],)).fetchone()
        if not row or _load(row[0])!=account["state"]:raise HandoffConflict("The passive account snapshot changed before approval binding.")
        cfg,brief=self.owner._policy(db)
        return {"expected_account_id":account["id"],"expected_state_sha256":_digest(row[0].encode()),
            "expected_source_sha256":_digest(_raw(self.cutoff(db,account["id"]))),
            "expected_owner_epoch":self.owner._epoch(db,cfg,brief)}

    def _entry_controls(self,db,account_id):
        """One-time CAS over entry-only stops; never a future financial cutoff."""
        failures=[];pending=[]
        if self.owner.eod.stress._exists(db):
            rows=db.execute("SELECT request_id,account_id,created_at,length(reason) size,substr(reason,1,1001) reason FROM opt_stress_context_failures WHERE account_id=? ORDER BY request_id LIMIT 101",(account_id,)).fetchall()
            if len(rows)>100:raise ValueError("Entry-only failure retention exceeds its fixed bound.")
            for row in rows:
                if type(row["size"]) is not int or not 0<=row["size"]<=1000:raise ValueError("Entry-only failure metadata exceeds its fixed bound.")
                failures.append({k:row[k] for k in ("request_id","account_id","created_at","reason")})
            rows=db.execute("SELECT request_id,account_id,data_sha256,status,created_at,bytes,length(receipt) size,substr(receipt,1,16001) receipt FROM opt_stress_contexts WHERE account_id=? AND status!='completed' ORDER BY request_id LIMIT 101",(account_id,)).fetchall()
            if len(rows)>100:raise ValueError("Entry-only pending context retention exceeds its fixed bound.")
            for row in rows:
                if row["status"] not in ("running","failed") or type(row["size"]) is not int or not 0<row["size"]<=16000 or len(row["receipt"].encode())>16000:raise ValueError("Entry-only context metadata changed its status or bound.")
                pending.append({**{k:row[k] for k in ("request_id","account_id","data_sha256","status","created_at","bytes")},"receipt_sha256":_digest(row["receipt"].encode())})
        result={"source":self.owner.root_eod.config(db),"failures":failures,"noncompleted_contexts":pending}
        if len(_raw(result))>256*1024:raise ValueError("Entry-only control snapshot exceeds its fixed bound.")
        return result

    def migrate(self,body,now=None):
        from . import options_bankroll_eod as engine
        owner=self.owner
        owner._arguments(body,{"acknowledge_simulated","acknowledge_total_bankroll","expected_account_id","expected_state_sha256","expected_source_sha256","expected_owner_epoch"})
        _identifier(body["expected_account_id"])
        epoch=body["expected_owner_epoch"]
        if (body["acknowledge_simulated"] is not True or body["acknowledge_total_bankroll"] is not True
                or not _hash(body["expected_state_sha256"]) or not _hash(body["expected_source_sha256"])
                or type(epoch) is not dict or set(epoch)!={"global_pause_rowid","options_revision","brief_revision"}
                or any(type(epoch[k]) is not int or epoch[k]<0 for k in ("options_revision","brief_revision"))
                or epoch["global_pause_rowid"] is not None and (type(epoch["global_pause_rowid"]) is not int or epoch["global_pause_rowid"]<0)):
            raise ValueError("Acknowledge the fixed total bankroll and supply exact account/source/owner bindings.")
        clock,receipt=_now(now),None
        with owner._ownership(),owner.eod.stress._runtime_lease() as runtime_check:
            try:
                with owner.database() as db:
                    db.execute("BEGIN IMMEDIATE");cfg,brief=owner._policy(db)
                    replay=owner._reserve(db,"bankroll_migrate",body,None,clock)
                    if replay:return replay
                    receipt={"request_id":body["request_id"],"action":"bankroll_migrate","status":"running","created_at":clock.isoformat(),"artifacts":[],"rule_sha256":engine.RULE_SHA256,"model_calls":0,"replayed":False}
                    self._schema(db);account=owner._account(db,clock);source=owner.root_eod.config(db)
                    if (not account or account["state"].get("schema")!=5 or not source["initialized"]
                            or db.execute("SELECT 1 FROM opt_bankroll_migrations LIMIT 1").fetchone()):
                        raise HandoffConflict("Upgrade only the existing healthy schema5 account once; no second bankroll.")
                    if account["policy"]!=owner._epoch(db,cfg,brief) or self.binding(db,account)!={k:body[k] for k in ("expected_account_id","expected_state_sha256","expected_source_sha256","expected_owner_epoch")}:
                        raise HandoffConflict("The approved account, source cutoff or owner epoch changed.")
                    owner.root_eod.verify_account(db,account["id"],account["state"],full=True)
                    old_raw=db.execute("SELECT state FROM opt_accounts WHERE id=?",(account["id"],)).fetchone()[0].encode()
                    cutoff_raw=_raw(self.cutoff(db,account["id"]));cutoff_sha=_digest(cutoff_raw)
                    entry_controls=self._entry_controls(db,account["id"])
                initial=engine.migrate_flat(account["state"],activated_at=clock.isoformat(),old_state_sha256=_digest(old_raw),old_data_sha256=cutoff_sha)
                initial_raw=_raw(initial);delta=initial["bankroll_policy"]["activation"]["new_funding"]
                report={"schema":1,"account_id":account["id"],"activated_at":clock.isoformat(),"rule_sha256":engine.RULE_SHA256,
                    "old_state_sha256":_digest(old_raw),"old_data_sha256":cutoff_sha,"initial_state_sha256":_digest(initial_raw),
                    "source_config_sha256":_digest(_raw(source)),"policy":account["policy"],"total_bankroll":"25000.00",
                    "entry_controls":entry_controls,"entry_controls_sha256":_digest(_raw(entry_controls)),
                    "new_funding":delta,"no_automatic_topups":True,"research_only":True,"execution_qualified":False}
                receipt.update(account_id=account["id"],old_state_sha256=_digest(old_raw),old_data_sha256=cutoff_sha,state_sha256=_digest(initial_raw),
                    simulated_funding_added=delta,total_bankroll="25000.00",no_automatic_topups=True,research_only=True,execution_qualified=False)
                def checkpoint():
                    runtime_check()
                    with owner.database() as db:
                        current,current_brief=owner._policy(db,cfg)
                        if current_brief!=brief or owner.root_eod.config(db)!=source or owner._epoch(db,current,current_brief)!=account["policy"]:
                            raise HandoffConflict("Bankroll activation permissions or selected source changed.")
                def final(db):
                    checkpoint();current=owner._account(db,clock)
                    if (not current or _basis(current)!=_basis(account) or _digest(_raw(self.cutoff(db,account["id"])))!=cutoff_sha
                            or self._entry_controls(db,account["id"])!=entry_controls):
                        raise HandoffConflict("The approved old account/source changed before bankroll publication.")
                    owner.root_eod.verify_account(db,account["id"],account["state"],full=True);self.verify(receipt)
                    if db.execute("SELECT count(*) FROM opt_events").fetchone()[0]>=MAX_EVENTS:raise ValueError("The shared event quota is full.")
                    db.execute("INSERT INTO opt_bankroll_migrations VALUES(?,?,?,?,?,?,?,?,?,?)",(account["id"],body["request_id"],old_raw.decode(),_digest(old_raw),cutoff_sha,initial_raw.decode(),_digest(initial_raw),clock.isoformat(),_json(source),_json(account["policy"])))
                    db.execute("INSERT INTO opt_bankroll_state VALUES(?,?,?)",(account["id"],body["request_id"],_digest(initial_raw)))
                    db.execute("UPDATE opt_accounts SET state=? WHERE id=?",(initial_raw.decode(),account["id"]))
                    event={"type":"capital_adjustment","amount":delta,"total_bankroll":"25000.00","automatic":False,"recorded_at":clock.isoformat(),"simulated":True,"research_only":True,"execution_qualified":False}
                    db.execute("INSERT INTO opt_events(account_id,run_id,value) VALUES(?,?,?)",(account["id"],body["request_id"],_json(event)))
                owner._publish(cfg,brief,receipt,(("previous-state.json",old_raw),("initial-state.json",initial_raw),("activation.json",_raw(report)),("source-cutoff.json",cutoff_raw)),final=final,extra_check=checkpoint)
                return receipt
            except Exception as error:
                owner._failed(receipt,error);raise

    def verify(self,receipt):
        from . import options_bankroll_eod as engine
        from . import options_root_eod_data as source
        _identifier(receipt.get("request_id"));action=receipt.get("action")
        if receipt.get("rule_sha256")!=engine.RULE_SHA256:raise ValueError("Bankroll receipt belongs to another fixed rule.")
        failed=action=="root_collect" and receipt.get("source_failed") is True
        names={"bundle.json","failure.json"} if failed else MIGRATION_FILES if action=="bankroll_migrate" else CAPTURE_FILES if action=="root_collect" else None
        base="reports/options-recovery/"+receipt["request_id"]+"/";artifacts=receipt.get("artifacts")
        if (names is None or type(artifacts) is not list or len(artifacts)!=len(names)
                or any(type(a) is not dict or set(a)!={"path","bytes","sha256"} or type(a["bytes"]) is not int or not 0<a["bytes"]<=MAX_DATA for a in artifacts)
                or {a["path"] for a in artifacts}!={base+n for n in names}):raise ValueError("Bankroll artifact contract changed.")
        saved={}
        for item in artifacts:
            raw=self.owner._read(item["path"],item["bytes"])
            if len(raw)!=item["bytes"] or _digest(raw)!=item["sha256"]:raise ValueError("Retained bankroll evidence changed; no regeneration.")
            saved[item["path"][len(base):]]=raw
        if failed:
            failure=_load(saved["failure.json"])
            if _digest(saved["bundle.json"])!=receipt.get("bundle_sha256") or failure.get("dataset_adopted") is not False or failure.get("fills_adopted")!=0:raise ValueError("Rejected bankroll source lost non-adoption identity.")
        elif action=="bankroll_migrate":
            report=_load(saved["activation.json"])
            initial=engine.migrate_flat(_load(saved["previous-state.json"]),activated_at=report["activated_at"],old_state_sha256=_digest(saved["previous-state.json"]),old_data_sha256=_digest(saved["source-cutoff.json"]))
            delta=initial["bankroll_policy"]["activation"]["new_funding"]
            if (saved["initial-state.json"]!=_raw(initial) or report.get("account_id")!=receipt.get("account_id")
                    or report.get("old_state_sha256")!=_digest(saved["previous-state.json"]) or report.get("old_state_sha256")!=receipt.get("old_state_sha256")
                    or report.get("old_data_sha256")!=_digest(saved["source-cutoff.json"]) or report.get("old_data_sha256")!=receipt.get("old_data_sha256")
                    or report.get("initial_state_sha256")!=_digest(saved["initial-state.json"]) or report.get("initial_state_sha256")!=receipt.get("state_sha256")
                    or report.get("rule_sha256")!=engine.RULE_SHA256 or report.get("total_bankroll")!="25000.00" or receipt.get("total_bankroll")!="25000.00"
                    or report.get("new_funding")!=delta or receipt.get("simulated_funding_added")!=delta
                    or type(report.get("entry_controls")) is not dict or report.get("entry_controls_sha256")!=_digest(_raw(report["entry_controls"]))
                    or report.get("source_config_sha256")!=_digest(_raw(report["entry_controls"].get("source")))
                    or report.get("no_automatic_topups") is not True or receipt.get("no_automatic_topups") is not True):raise ValueError("One-time bankroll funding/lineage binding changed.")
        else:
            before=engine.validate_state(_load(saved["before-state.json"]));paper=_load(saved["paper.json"]);after=engine.validate_state(paper["state"])
            snapshot=source.validate_snapshot(_load(saved["snapshot.json"]));contexts=_load(saved["contexts.json"])
            required=sorted({p["contract_id"] for p in (before["position"],before["pending"]) if p})
            normalized=source.normalize_bundle(_load(saved["bundle.json"]),expected_session=snapshot["source_date"],required_contracts=required)
            delta=after["bankroll_processed_count"]-before["bankroll_processed_count"];recorded=_now(datetime.fromisoformat(receipt["recorded_at"]))
            if (normalized["snapshot"]!=snapshot or _raw(normalized["provenance"])!=saved["provenance.json"]
                    or any(_digest(saved[n+".json"])!=receipt.get(n+"_sha256") for n in ("bundle","snapshot","provenance","contexts"))
                    or _digest(_raw(before))!=receipt.get("before_state_sha256") or _digest(_raw(after))!=receipt.get("state_sha256")
                    or paper.get("summary")!=engine.summarize(after) or paper.get("status")!=receipt.get("paper_status") or paper["status"] not in ("completed","held","unchanged")
                    or type(paper.get("events")) is not list or len(paper["events"])>32 or any(type(e) is not dict for e in paper["events"]) or len(paper["events"])!=receipt.get("paper_events")
                    or after["bankroll_policy"]["activation"]!=before["bankroll_policy"]["activation"] or after["root_policy"]!=before["root_policy"]
                    or snapshot["source_date"]!=receipt.get("source_date") or snapshot["received_at"]!=receipt.get("received_at") or receipt.get("required_contracts")!=required
                    or receipt.get("paper_schema")!=6 or receipt.get("quote_observed_at") is not None or receipt.get("partial_chain")!=snapshot["partial_chain"]
                    or type(receipt.get("accepted_snapshot")) is not bool or delta not in (0,1) or receipt["accepted_snapshot"]!=(delta==1) or (paper["status"]=="completed")!=(delta==1)
                    or any(after[k]-before[k]!=delta for k in ("processed_count","root_processed_count"))
                    or _now(datetime.fromisoformat(snapshot["received_at"]))>recorded or _now(datetime.fromisoformat(receipt["created_at"]))>recorded
                    or (paper["status"]=="unchanged" and (before!=after or recorded<_now(datetime.fromisoformat(before["last_decided_at"]))))
                    or (paper["status"]!="unchanged" and after["last_decided_at"]!=recorded.isoformat())
                    or type(contexts) is not list or len(contexts)>16 or receipt.get("research_only") is not True or receipt.get("execution_qualified") is not False):raise ValueError("Bankroll capture source/state/context binding changed.")
            from . import options_stress_context as benchmark
            from .options_eod_workflow import _stress_extension
            _stress_extension(before["bankroll_policy"],after["bankroll_policy"])
            for record in after["bankroll_policy"]["context_history"]:
                if record["sequence"]<=before["bankroll_policy"]["context_count"]:continue
                query={"decision_at":record["recorded_at"],"source_date":record["source_date"] if record["stage"]=="fill" else None};rejected=False
                try:
                    expected=benchmark.validate_gate_result(benchmark.select_gate(contexts,**query))
                    if len(_raw(expected))>engine.MAX_GATE_RECORD_BYTES-1536:raise benchmark.StressContextError("Oversized bankroll gate.")
                except benchmark.StressContextError:
                    rejected=True;expected=benchmark.validate_gate_result(benchmark.select_gate((),**query))
                if record["context_input_rejected"]!=rejected or record["gate"]["benchmark_gate"]!=expected:raise ValueError("A bankroll gate differs from its saved actual benchmark input/query.")
        return saved

    def _migration(self,db,account_id,full=False):
        from . import options_bankroll_eod as engine
        row=db.execute("SELECT * FROM opt_bankroll_migrations WHERE account_id=?",(account_id,)).fetchone()
        if not row or _digest(row["old_state"].encode())!=row["old_state_sha256"]:raise ValueError("Original schema5 cutoff missing or changed.")
        old=_load(row["old_state"]);self.owner.root_eod.verify_account(db,account_id,old,full=full)
        if _digest(_raw(self.cutoff(db,account_id)))!=row["old_data_sha256"]:raise ValueError("The sealed schema5 source cutoff changed.")
        receipt=self.owner.eod._recorded(db,row["request_id"]);saved=self.verify(receipt);report=_load(saved["activation.json"])
        initial=engine.migrate_flat(old,activated_at=row["activated_at"],old_state_sha256=row["old_state_sha256"],old_data_sha256=row["old_data_sha256"])
        if (saved["previous-state.json"]!=row["old_state"].encode() or saved["initial-state.json"]!=row["initial_state"].encode() or saved["initial-state.json"]!=_raw(initial)
                or row["initial_state_sha256"]!=_digest(_raw(initial)) or report["activated_at"]!=row["activated_at"]
                or report["policy"]!=_load(row["policy"]) or report["source_config_sha256"]!=_digest(row["source_config"].encode())):raise ValueError("Bankroll migration table and immutable files disagree.")
        return row,initial

    def verify_account(self,db,account_id,state,*,full=False):
        from . import options_bankroll_eod as engine
        from .options_eod_workflow import _stress_extension
        state=engine.validate_state(state)
        if not self._exists(db):raise ValueError("Bankroll activation witness is missing.")
        migration,initial=self._migration(db,account_id,full)
        identity=db.execute("SELECT started_at,policy FROM opt_accounts WHERE id=? AND closed_at IS NULL",(account_id,)).fetchone()
        if (not identity or identity["started_at"]!=state["started_at"] or identity["policy"]!=migration["policy"]
                or state["bankroll_policy"]["activation"]!=initial["bankroll_policy"]["activation"]):raise ValueError("The total bankroll lost its same-account/authority identity.")
        rows=db.execute("SELECT source_date,digest,bytes,length(value) actual,request_id FROM opt_bankroll_data ORDER BY source_date LIMIT 101").fetchall()
        count=sum(db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in ("opt_eod_data","opt_root_data","opt_bankroll_data"))
        used=sum(db.execute("SELECT coalesce(sum(bytes),0) FROM "+t).fetchone()[0] for t in ("opt_eod_data","opt_root_data","opt_bankroll_data"))
        if (len(rows)!=state["bankroll_processed_count"] or count>100 or used>MAX_DATA
                or any(state[k]!=initial[k]+len(rows) for k in ("processed_count","root_processed_count"))):raise ValueError("Shared source retention count/byte quota changed.")
        cursor=initial["prefix_sha256"];ring=list(initial["cursor_history"]);references=list(initial["history"]);previous=initial
        known={w["witness_sha256"]:w for w in self.owner.eod.stress._verified_context_rows(db,account_id)} if full else None
        for row in rows:
            if type(row["bytes"]) is not int or not 0<row["bytes"]<=MAX_DATA or row["actual"]!=row["bytes"]:raise ValueError("A bankroll cache row changed byte identity.")
            receipt=self.owner.eod._recorded(db,row["request_id"])
            if (receipt.get("action")!="root_collect" or receipt.get("paper_schema")!=6 or receipt.get("rule_sha256")!=engine.RULE_SHA256
                    or receipt.get("account_id")!=account_id or receipt.get("accepted_snapshot") is not True or receipt.get("source_date")!=row["source_date"] or receipt.get("snapshot_sha256")!=row["digest"]):raise ValueError("A bankroll source lost its completed receipt.")
            next_cursor=_digest(_raw({"previous_sha256":cursor,"snapshot_sha256":row["digest"]}));ring.append({"source_date":row["source_date"],"snapshot_sha256":row["digest"],"previous_sha256":cursor,"prefix_sha256":next_cursor})
            if full:
                saved=self.verify(receipt);raw=bytes(db.execute("SELECT substr(value,1,?) FROM opt_bankroll_data WHERE source_date=?",(row["bytes"]+1,row["source_date"])).fetchone()[0])
                if raw!=saved["snapshot.json"] or _digest(raw)!=row["digest"] or _load(saved["before-state.json"])!=previous:raise ValueError("The ordered bankroll source/report chain changed.")
                current=_load(saved["paper.json"])["state"]
                if current["bankroll_processed_count"]!=previous["bankroll_processed_count"]+1 or current["prefix_sha256"]!=next_cursor:raise ValueError("A bankroll frame lost its exact cursor step.")
                _stress_extension(previous["bankroll_policy"],current["bankroll_policy"])
                for witness in _load(saved["contexts.json"]):
                    if type(witness) is not dict or known.get(witness.get("witness_sha256"))!=witness:raise ValueError("A bankroll input ring lost its immutable publication.")
                for record in current["bankroll_policy"]["context_history"]:
                    witness=record["gate"]["benchmark_gate"]["witness"]
                    if witness is not None and known.get(witness["witness_sha256"])!=witness:raise ValueError("A bankroll gate lost its published benchmark.")
                snap=_load(saved["snapshot.json"]);references.append({k:snap[k] for k in ("source_date","underlying_close","received_at","stock_sha256")});previous=current
            cursor=next_cursor
        if cursor!=state["prefix_sha256"] or ring[-15:]!=state["cursor_history"]:raise ValueError("Bankroll source prefix differs from current cursor.")
        anchor=db.execute("SELECT * FROM opt_bankroll_state WHERE account_id=?",(account_id,)).fetchone()
        if not anchor or anchor["state_sha256"]!=_digest(_raw(state)):raise ValueError("Bankroll state changed outside its sealed transition.")
        transition=self.owner.eod._recorded(db,anchor["request_id"])
        if transition.get("account_id")!=account_id or transition.get("state_sha256")!=anchor["state_sha256"]:raise ValueError("Bankroll state has no completed publication.")
        if full:
            saved=self.verify(transition)
            if transition["action"]=="root_collect":
                if _load(saved["paper.json"])["state"]!=state:raise ValueError("Current bankroll differs from its sealed report.")
                if transition.get("accepted_snapshot") is not True:
                    before=_load(saved["before-state.json"]);candidate=deepcopy(state)
                    for k in ("hold","cap_qualified","last_decided_at"):candidate[k]=before[k]
                    if before!=previous or candidate!=before:raise ValueError("An unaccepted bankroll observation changed finances or prior evidence.")
            elif state!=initial:raise ValueError("An untouched bankroll cannot change after activation.")
            if references[-15:]!=state["history"]:raise ValueError("Bankroll stock history differs from accepted original references.")
        return {"snapshots":len(rows),"legacy_snapshots":initial["processed_count"],"prefix_sha256":cursor}

    def snapshot(self,db,now,account,hold=None):
        from . import options_bankroll_eod as engine
        schema=(account or {}).get("state",{}).get("schema");binding=None;eligible=False
        if schema==5 and not hold:
            try:
                binding=self.binding(db,account)
                if account["policy"]!=binding["expected_owner_epoch"]:raise HandoffConflict("Owner epoch differs.")
                engine.migrate_flat(account["state"],activated_at=now.isoformat(),old_state_sha256=binding["expected_state_sha256"],old_data_sha256=binding["expected_source_sha256"])
                eligible=True
            except (ValueError,HandoffConflict):binding=None
        controls=self._entry_controls(db,account["id"]) if schema in (5,6) else None
        return {"active":schema==6,"migration_eligible":eligible,"migration_binding":binding,"rule":deepcopy(engine.RULE),"rule_sha256":engine.RULE_SHA256,
            "source_enabled":controls["source"]["enabled"] if controls else False,"entry_only_stop_present":bool(controls and (controls["failures"] or controls["noncompleted_contexts"])),
            "total_bankroll":"25000.00","automatic_topups":False,"same_account":True,"real_orders":False,"research_only":True,"execution_qualified":False}
