"""One explicit KO epoch on the existing paper account; no second cashbook.

The frozen Ford cutoff remains independently verifiable. Only a bounded child
may acquire source data; the offline owner validates raw bytes before commit.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
import threading
from urllib.parse import urlencode

from .handoff import HandoffConflict, _load
from .internet import SETTINGS_KEY
from .options_workflow import _json, _digest, _now, MAX_DATA, MAX_EVENTS

SOURCE_ID = "onclick_ko_eod_v1"
DEFAULT = {"schema": 1, "initialized": False, "enabled": False, "revision": 0,
           "activation_at": None, "provider": SOURCE_ID, "symbol": "KO",
           "timezone": "America/New_York", "hour": 9, "minute": 35}
MIGRATION_FILES = {"previous-state.json", "initial-state.json", "activation.json",
                   "baseline-source.json", "baseline-manifest.json", "baseline-receipt.json"}
CAPTURE_FILES = {"before-state.json", "bundle.json", "snapshot.json", "provenance.json", "contexts.json", "paper.json"}


def _raw(value):
    return _json(value).encode()


def _basis(account):
    return {key: account[key] for key in ("id", "state", "started_at", "policy")}


class OptionsRootWorkflow:
    def __init__(self, owner):
        self.owner = owner
        self.lock = threading.RLock()
        self.processes = set()
        self._baseline_cache = None

    def stop(self):
        with self.lock:
            for process in tuple(self.processes):
                if process.poll() is None:
                    process.kill()

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opt_root_meta'").fetchone() is not None

    def _schema(self, db):
        db.execute("CREATE TABLE IF NOT EXISTS opt_root_meta(id INTEGER PRIMARY KEY CHECK(id=1),config TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_root_migrations(account_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,old_state TEXT NOT NULL,old_state_sha256 TEXT NOT NULL,old_data_sha256 TEXT NOT NULL,initial_state TEXT NOT NULL,initial_state_sha256 TEXT NOT NULL,activated_at TEXT NOT NULL,legacy_config TEXT NOT NULL,policy TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_root_state(account_id TEXT PRIMARY KEY,request_id TEXT NOT NULL,state_sha256 TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_root_data(source_date TEXT PRIMARY KEY,digest TEXT NOT NULL,value BLOB NOT NULL,bytes INTEGER NOT NULL,request_id TEXT UNIQUE NOT NULL)")
        db.execute("INSERT OR IGNORE INTO opt_root_meta VALUES(1,?)", (_json(DEFAULT),))

    def config(self, db):
        if not self._exists(db):
            return deepcopy(DEFAULT)
        row = db.execute("SELECT config FROM opt_root_meta WHERE id=1").fetchone()
        cfg = _load(row[0]) if row else None
        if (type(cfg) is not dict or set(cfg) != set(DEFAULT)
                or any(type(cfg[k]) is not bool for k in ("initialized", "enabled"))
                or type(cfg["revision"]) is not int or cfg["revision"] < 0
                or type(cfg["schema"]) is not int
                or any(cfg[k] != DEFAULT[k] for k in ("schema", "provider", "symbol", "timezone", "hour", "minute"))
                or cfg["initialized"] != (cfg["activation_at"] is not None)
                or cfg["enabled"] and not cfg["initialized"]):
            raise ValueError("The fixed KO source configuration changed; no reset is permitted.")
        if cfg["activation_at"] is not None:
            _now(datetime.fromisoformat(cfg["activation_at"]))
        return cfg

    def _permissions(self, db):
        cfg, brief = self.owner._policy(db)
        source = self.config(db)
        internet = self.owner.store.setting(SETTINGS_KEY, None)
        if (not source["initialized"] or not source["enabled"] or type(internet) is not dict
                or internet.get("enabled") is not True or type(internet.get("revision")) is not int):
            raise HandoffConflict("The KO source or owner Internet permission is disabled.")
        return cfg, source, internet, brief

    def _authority(self, db, account, cfg, brief):
        if (not account or account["state"].get("schema") not in (5,6) or account["state"].get("source_id") != SOURCE_ID
                or account["policy"] != self.owner._epoch(db, cfg, brief)):
            raise HandoffConflict("The shared KO account or owner permission epoch changed.")

    def _policy(self, db):
        result = self._permissions(db)
        self._authority(db, self.owner._account(db), result[0], result[3])
        return result

    def _window(self, now):
        return self.owner.eod._window(now)

    def _baseline(self, manifest_raw, raw, clock, source_receipt_raw):
        from . import options_root_eod_data as source
        if manifest_raw == b"null" and raw == b"null" and source_receipt_raw == b"null":
            return []
        manifest = _load(manifest_raw)
        if (len(manifest_raw)>64*1024 or len(raw)>2*1024*1024
                or type(manifest) is not dict or set(manifest) != {"schema", "kind", "raw_path", "raw_sha256", "received_at", "through_date", "source_receipt_path", "source_receipt_sha256"}
                or type(manifest["schema"]) is not int or manifest["schema"] != 1
                or manifest["kind"] != "onclick_ko_stock_baseline" or _digest(raw) != manifest["raw_sha256"]
                or _digest(source_receipt_raw) != manifest["source_receipt_sha256"]):
            raise ValueError("Use the exact KO stock-baseline manifest and original bytes.")
        self.owner.registry._path(manifest["raw_path"])
        self.owner.registry._path(manifest["source_receipt_path"])
        if len(source_receipt_raw)>64*1024:
            raise ValueError("The original baseline response receipt exceeds its bound.")
        received = _now(datetime.fromisoformat(manifest["received_at"].replace("Z", "+00:00")))
        if received > clock:
            raise ValueError("A baseline cannot be available after its activation.")
        # Every artifact was read and rehashed before this point. Cache only
        # the deterministic parse of these exact bytes, never filesystem times,
        # financial state, source permission or a mutable caller-owned list.
        cache_key=(_digest(manifest_raw),_digest(raw),_digest(source_receipt_raw),clock.isoformat(),source.SESSION_GRID_POLICY_SHA256)
        cached=self._baseline_cache
        if cached is not None and cached[0]==cache_key:
            return deepcopy(cached[1])
        original=source._json(source_receipt_raw)
        sessions=source.base._stock_sessions(raw)
        first,last=min(sessions),max(sessions)
        close=source.base._close_minutes(last)
        expected_url=source.ORIGIN+"/stock-data/v2/?"+urlencode({"ticker":"KO","from":first.isoformat()+"-09-30",
            "to":last.isoformat()+f"-{close//60:02d}-{close%60:02d}","extended":"false","bar_size":"1m"})
        if (type(original) is not dict or original.get("url")!=expected_url or type(original.get("http_status")) is not int
                or original["http_status"]!=200 or type(original.get("bytes")) is not int or original["bytes"]!=len(raw)
                or original.get("sha256")!=_digest(raw) or original.get("received_at")!=manifest["received_at"]):
            raise ValueError("KO stock seeds need their exact original KO URL, successful response bytes and actual receipt clock.")
        refs=source.load_baseline(raw, received_at=received, through_date=manifest["through_date"])
        self._baseline_cache=(cache_key,deepcopy(refs))
        return deepcopy(refs)

    def verify(self, receipt):
        if receipt.get("action")=="root_collect" and receipt.get("paper_schema")==6:
            return self.owner.bankroll.verify(receipt)
        from . import options_root_eod as engine
        from . import options_root_eod_data as source
        from .handoff import _identifier
        _identifier(receipt.get("request_id"))
        action = receipt.get("action")
        if receipt.get("rule_sha256") != engine.RULE_SHA256:
            raise ValueError("The KO receipt belongs to another fixed rule.")
        if action == "root_config" and receipt.get("artifacts") == []:
            return {}
        failed_source = action == "root_collect" and receipt.get("source_failed") is True
        names = {"bundle.json", "failure.json"} if failed_source else MIGRATION_FILES if action == "root_migrate" else CAPTURE_FILES if action == "root_collect" else None
        base = "reports/options-recovery/" + receipt["request_id"] + "/"
        artifacts = receipt.get("artifacts")
        if (names is None or type(artifacts) is not list or len(artifacts) != len(names)
                or any(type(a) is not dict or set(a) != {"path", "bytes", "sha256"} or type(a["bytes"]) is not int or not 0 < a["bytes"] <= MAX_DATA for a in artifacts)
                or {a["path"] for a in artifacts} != {base+n for n in names}):
            raise ValueError("The KO artifact contract is incomplete.")
        saved = {}
        for item in artifacts:
            value = self.owner._read(item["path"], MAX_DATA)
            if len(value) != item["bytes"] or _digest(value) != item["sha256"]:
                raise ValueError("Retained KO evidence changed; it cannot be regenerated.")
            saved[item["path"][len(base):]] = value
        if failed_source:
            failure = _load(saved["failure.json"])
            if (_digest(saved["bundle.json"]) != receipt.get("bundle_sha256")
                    or failure.get("dataset_adopted") is not False or failure.get("fills_adopted") != 0):
                raise ValueError("Rejected KO source evidence lost its non-adoption binding.")
        elif action == "root_migrate":
            report = _load(saved["activation.json"])
            clock = _now(datetime.fromisoformat(report["activated_at"]))
            baseline = self._baseline(saved["baseline-manifest.json"], saved["baseline-source.json"], clock, saved["baseline-receipt.json"])
            initial = engine.migrate_flat(_load(saved["previous-state.json"]), activated_at=clock.isoformat(),
                old_state_sha256=_digest(saved["previous-state.json"]), old_data_sha256=report["old_data_sha256"], baseline=baseline)
            if (saved["initial-state.json"] != _raw(initial) or _digest(saved["initial-state.json"]) != receipt.get("state_sha256")
                    or report.get("initial_state_sha256") != receipt.get("state_sha256")
                    or report.get("old_state_sha256") != receipt.get("old_state_sha256")
                    or _digest(saved["previous-state.json"]) != receipt.get("old_state_sha256")
                    or report.get("old_data_sha256") != receipt.get("old_data_sha256")
                    or report.get("rule_sha256") != engine.RULE_SHA256 or report.get("account_id") != receipt.get("account_id")
                    or report.get("new_funding") != "0" or report.get("monthly_budget_reset") is not False
                    or receipt.get("simulated_funding_added") != "0" or receipt.get("monthly_budget_reset") is not False):
                raise ValueError("KO activation lost its original account/funding lineage.")
        else:
            before = engine.validate_state(_load(saved["before-state.json"]))
            snapshot = source.validate_snapshot(_load(saved["snapshot.json"]))
            normalized = source.normalize_bundle(_load(saved["bundle.json"]), expected_session=snapshot["source_date"], required_contracts=receipt["required_contracts"])
            paper = _load(saved["paper.json"])
            after = engine.validate_state(paper["state"])
            contexts = _load(saved["contexts.json"])
            required=sorted({item["contract_id"] for item in (before["position"],before["pending"]) if item})
            delta=after["root_processed_count"]-before["root_processed_count"]
            recorded=_now(datetime.fromisoformat(receipt["recorded_at"]))
            if (normalized["snapshot"] != snapshot or _raw(normalized["provenance"]) != saved["provenance.json"]
                    or any(_digest(saved[name+".json"]) != receipt.get(name+"_sha256") for name in ("bundle", "snapshot", "provenance", "contexts"))
                    or _digest(_raw(before)) != receipt.get("before_state_sha256")
                    or _digest(_raw(after)) != receipt.get("state_sha256")
                    or type(paper.get("events")) is not list or len(paper["events"])>32 or any(type(event) is not dict for event in paper["events"])
                    or paper["summary"] != engine.summarize(after) or paper["status"] != receipt.get("paper_status")
                    or paper["status"] not in ("completed","held","unchanged") or (paper["status"]=="completed")!=(delta==1)
                    or len(paper["events"]) != receipt.get("paper_events")
                    or after["root_policy"]["activation"] != before["root_policy"]["activation"]
                    or snapshot["source_date"] != receipt.get("source_date") or snapshot["received_at"] != receipt.get("received_at")
                    or receipt.get("required_contracts")!=required or receipt.get("paper_schema")!=5
                    or receipt.get("quote_observed_at") is not None or receipt.get("partial_chain")!=snapshot["partial_chain"]
                    or type(receipt.get("accepted_snapshot")) is not bool or delta not in (0,1) or receipt["accepted_snapshot"]!=(delta==1)
                    or after["processed_count"]-before["processed_count"]!=delta
                    or _now(datetime.fromisoformat(snapshot["received_at"]))>recorded
                    or _now(datetime.fromisoformat(receipt["created_at"]))>recorded
                    or (paper["status"]=="unchanged" and (before!=after or recorded<_now(datetime.fromisoformat(before["last_decided_at"]))))
                    or (paper["status"]!="unchanged" and after["last_decided_at"]!=recorded.isoformat())
                    or type(contexts) is not list or len(contexts) > 16
                    or receipt.get("research_only") is not True or receipt.get("execution_qualified") is not False):
                raise ValueError("KO source/state/context report binding changed.")
            from . import options_stress_context as benchmark
            from .options_eod_workflow import _stress_extension
            _stress_extension(before["root_policy"],after["root_policy"])
            for record in after["root_policy"]["context_history"]:
                if record["sequence"]<=before["root_policy"]["context_count"]:
                    continue
                query={"decision_at":record["recorded_at"],"source_date":record["source_date"] if record["stage"]=="fill" else None}
                rejected=False
                try:
                    expected=benchmark.validate_gate_result(benchmark.select_gate(contexts,**query))
                    if len(_raw(expected))>engine.MAX_GATE_RECORD_BYTES-1536:
                        raise benchmark.StressContextError("Oversized root context gate.")
                except benchmark.StressContextError:
                    rejected=True
                    expected=benchmark.validate_gate_result(benchmark.select_gate((),**query))
                if record["context_input_rejected"]!=rejected or record["gate"]["benchmark_gate"]!=expected:
                    raise ValueError("A KO gate differs from its actual saved benchmark input/query.")
        return saved

    def _migration(self, db, account_id):
        row = db.execute("SELECT * FROM opt_root_migrations WHERE account_id=?", (account_id,)).fetchone()
        if not row or _digest(row["old_state"].encode()) != row["old_state_sha256"]:
            raise ValueError("The original Ford cutoff is missing or changed.")
        old = _load(row["old_state"])
        # The old opt_eod_state anchor is deliberately never rewritten by KO.
        self.owner.eod.stress.verify_account(db, account_id, old)
        if self.owner.eod.config(db) != _load(row["legacy_config"]) or self.owner.eod.stress._prefix(db) != row["old_data_sha256"]:
            raise ValueError("The frozen Ford configuration/source cutoff changed.")
        receipt = self.owner.eod._recorded(db, row["request_id"])
        saved = self.verify(receipt)
        report = _load(saved["activation.json"])
        if (saved["previous-state.json"] != row["old_state"].encode() or saved["initial-state.json"] != row["initial_state"].encode()
                or _digest(saved["initial-state.json"]) != row["initial_state_sha256"]
                or report["activated_at"] != row["activated_at"] or report["old_data_sha256"] != row["old_data_sha256"]
                or report["policy"] != _load(row["policy"]) or report["legacy_config_sha256"] != _digest(row["legacy_config"].encode())):
            raise ValueError("KO activation table and retained migration files disagree.")
        return row, _load(saved["initial-state.json"])

    def verify_account(self, db, account_id, state, *, full=False):
        from . import options_root_eod as engine
        from .options_eod_workflow import _stress_extension
        state = engine.validate_state(state)
        if not self._exists(db) or not self.config(db)["initialized"]:
            raise ValueError("KO account has no source activation witness.")
        migration, initial = self._migration(db, account_id)
        if full:
            self.owner.eod._audit_prefix(db,account_id,_load(migration["old_state"]),full=True)
        identity = db.execute("SELECT started_at,policy FROM opt_accounts WHERE id=?", (account_id,)).fetchone()
        if not identity or identity["started_at"] != state["started_at"] or identity["policy"] != migration["policy"]:
            raise ValueError("KO activation lost its original shared-account identity/authority.")
        if (state["root_policy"]["activation"] != initial["root_policy"]["activation"]
                or self.config(db)["activation_at"] != migration["activated_at"]):
            raise ValueError("KO account lost its immutable activation.")
        rows = db.execute("SELECT source_date,digest,bytes,length(value) actual,request_id FROM opt_root_data ORDER BY source_date LIMIT 101").fetchall()
        old_count = initial["processed_count"]
        if (len(rows) != state["root_processed_count"] or len(rows)+old_count > 100
                or db.execute("SELECT count(*) FROM opt_root_data").fetchone()[0] != len(rows)
                or sum(r["bytes"] for r in rows) + db.execute("SELECT coalesce(sum(bytes),0) FROM opt_eod_data").fetchone()[0] > MAX_DATA):
            raise ValueError("The shared source count/byte quota or KO cursor changed.")
        cursor = initial["prefix_sha256"]
        previous = initial
        ring = []
        references = list(initial["history"])
        known = {w["witness_sha256"]: w for w in self.owner.eod.stress._verified_context_rows(db, account_id)} if full else None
        for row in rows:
            if type(row["bytes"]) is not int or not 0 < row["bytes"] <= MAX_DATA or row["actual"] != row["bytes"]:
                raise ValueError("A KO cache row lost its byte bound.")
            receipt = self.owner.eod._recorded(db, row["request_id"])
            if (receipt.get("action") != "root_collect" or receipt.get("account_id") != account_id
                    or receipt.get("rule_sha256") != engine.RULE_SHA256 or receipt.get("accepted_snapshot") is not True
                    or receipt.get("source_date") != row["source_date"] or receipt.get("snapshot_sha256") != row["digest"]):
                raise ValueError("A KO source row lost its completed receipt.")
            prefix = _digest(_raw({"previous_sha256": cursor, "snapshot_sha256": row["digest"]}))
            ring.append({"source_date": row["source_date"], "snapshot_sha256": row["digest"], "previous_sha256": cursor, "prefix_sha256": prefix})
            if full:
                saved = self.verify(receipt)
                cached = bytes(db.execute("SELECT substr(value,1,?) FROM opt_root_data WHERE source_date=?", (row["bytes"]+1, row["source_date"])).fetchone()[0])
                if cached != saved["snapshot.json"] or _digest(cached) != row["digest"] or _load(saved["before-state.json"]) != previous:
                    raise ValueError("The ordered KO source/report chain changed.")
                current = _load(saved["paper.json"])["state"]
                if current["root_processed_count"] != previous["root_processed_count"]+1 or current["prefix_sha256"] != prefix:
                    raise ValueError("A KO frame lost its exact next-source transition.")
                _stress_extension(previous["root_policy"], current["root_policy"])
                for witness in _load(saved["contexts.json"]):
                    if type(witness) is not dict or known.get(witness.get("witness_sha256")) != witness:
                        raise ValueError("A saved KO input ring lost its immutable published benchmark data.")
                for record in current["root_policy"]["context_history"]:
                    witness = record["gate"]["benchmark_gate"]["witness"]
                    if witness is not None and known.get(witness["witness_sha256"]) != witness:
                        raise ValueError("A KO gate lost its actual retained benchmark data.")
                snap = _load(saved["snapshot.json"])
                references.append({key:snap[key] for key in ("source_date", "underlying_close", "received_at", "stock_sha256")})
                previous = current
            cursor = prefix
        if cursor != state["prefix_sha256"] or ring[-15:] != state["cursor_history"]:
            raise ValueError("The KO source prefix no longer matches the active cursor.")
        anchor = db.execute("SELECT * FROM opt_root_state WHERE account_id=?", (account_id,)).fetchone()
        if not anchor or anchor["state_sha256"] != _digest(_raw(state)):
            raise ValueError("The shared KO state changed outside its completed transition.")
        transition = self.owner.eod._recorded(db, anchor["request_id"])
        if transition.get("account_id") != account_id or transition.get("state_sha256") != anchor["state_sha256"]:
            raise ValueError("KO state lacks a completed publication.")
        if full:
            saved = self.verify(transition)
            if transition["action"] == "root_collect":
                final = _load(saved["paper.json"])["state"]
                if final != state:
                    raise ValueError("Current KO state differs from its sealed report.")
                if transition.get("accepted_snapshot") is not True:
                    before = _load(saved["before-state.json"])
                    if before != previous:
                        raise ValueError("Non-adopted KO observation lost its preceding account state.")
                    candidate = deepcopy(final)
                    for key in ("hold", "cap_qualified", "last_decided_at"):
                        candidate[key] = before[key]
                    if candidate != before:
                        raise ValueError("An unaccepted source cannot alter money, gates or prior observations.")
            elif state != initial:
                raise ValueError("An untouched KO account cannot change after activation.")
            if references[-15:] != state["history"]:
                raise ValueError("KO indicator history differs from accepted actual stock references.")
        return {"snapshots": len(rows), "legacy_snapshots": old_count, "prefix_sha256": cursor}

    def migrate(self, body, now=None):
        from . import options_root_eod as engine
        owner = self.owner
        owner._arguments(body, {"acknowledge_simulated", "acknowledge_partial_eod", "baseline_path"})
        if body["acknowledge_simulated"] is not True or body["acknowledge_partial_eod"] is not True:
            raise ValueError("Acknowledge the same simulated account and partial date-only KO prices.")
        if body["baseline_path"] is not None:
            owner.registry._path(body["baseline_path"])
        clock, receipt = _now(now), None
        with owner.eod.stress._runtime_lease() as runtime_check, owner._ownership():
            try:
                with owner.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    cfg, brief = owner._policy(db)
                    replay = owner._reserve(db, "root_migrate", body, None, clock)
                    if replay:
                        return replay
                    receipt = {"request_id":body["request_id"], "action":"root_migrate", "status":"running", "created_at":clock.isoformat(), "artifacts":[], "model_calls":0, "replayed":False, "rule_sha256":engine.RULE_SHA256}
                    self._schema(db)
                    feed, legacy = self.config(db), owner.eod.config(db)
                    account = owner._account(db, clock)
                    if feed["initialized"] or not account or account["state"].get("schema") != 4 or not legacy["initialized"]:
                        raise HandoffConflict("Upgrade only the existing pristine schema4 account once; no new account.")
                    if account["policy"] != owner._epoch(db, cfg, brief):
                        raise HandoffConflict("Original paper authority changed; activation cannot clear it.")
                    owner.eod._audit_prefix(db, account["id"], account["state"], full=True)
                    old_raw = db.execute("SELECT state FROM opt_accounts WHERE id=?", (account["id"],)).fetchone()[0].encode()
                    data_sha = owner.eod.stress._prefix(db)
                manifest_raw = owner._read(body["baseline_path"], 65536) if body["baseline_path"] is not None else b"null"
                manifest = _load(manifest_raw)
                baseline_raw = owner._read(manifest["raw_path"], 2*1024*1024) if manifest is not None else b"null"
                baseline_receipt_raw = owner._read(manifest["source_receipt_path"], 64*1024) if manifest is not None else b"null"
                baseline = self._baseline(manifest_raw, baseline_raw, clock, baseline_receipt_raw)
                initial = engine.migrate_flat(account["state"], activated_at=clock.isoformat(), old_state_sha256=_digest(old_raw), old_data_sha256=data_sha, baseline=baseline)
                initial_raw = _raw(initial)
                report = {"schema":1, "account_id":account["id"], "activated_at":clock.isoformat(), "rule_sha256":engine.RULE_SHA256,
                    "old_state_sha256":_digest(old_raw), "old_data_sha256":data_sha, "initial_state_sha256":_digest(initial_raw),
                    "new_funding":"0", "monthly_budget_reset":False, "legacy_config_sha256":_digest(_raw(legacy)), "policy":account["policy"], "symbol":"KO", "research_only":True, "execution_qualified":False}
                receipt.update(account_id=account["id"], old_state_sha256=_digest(old_raw), old_data_sha256=data_sha, state_sha256=_digest(initial_raw), simulated_funding_added="0", monthly_budget_reset=False, research_only=True, execution_qualified=False)
                def checkpoint():
                    runtime_check()
                    with owner.database() as db:
                        current, current_brief = owner._policy(db, cfg)
                        if current_brief != brief or self.config(db) != feed or owner.eod.config(db) != legacy or owner._epoch(db,current,current_brief) != account["policy"]:
                            raise HandoffConflict("KO activation permissions/source changed.")
                def final(db):
                    checkpoint()
                    current = owner._account(db, clock)
                    if not current or _basis(current) != _basis(account) or owner.eod.stress._prefix(db) != data_sha:
                        raise HandoffConflict("The original account/source changed before KO activation.")
                    self.verify(receipt)
                    db.execute("INSERT INTO opt_root_migrations VALUES(?,?,?,?,?,?,?,?,?,?)", (account["id"],body["request_id"],old_raw.decode(),_digest(old_raw),data_sha,initial_raw.decode(),_digest(initial_raw),clock.isoformat(),_json(legacy),_json(account["policy"])))
                    db.execute("INSERT INTO opt_root_state VALUES(?,?,?)", (account["id"],body["request_id"],_digest(initial_raw)))
                    db.execute("UPDATE opt_accounts SET state=? WHERE id=?", (initial_raw.decode(),account["id"]))
                    active = {**feed,"initialized":True,"enabled":True,"revision":feed["revision"]+1,"activation_at":clock.isoformat()}
                    db.execute("UPDATE opt_root_meta SET config=? WHERE id=1", (_json(active),))
                owner._publish(cfg,brief,receipt,(("previous-state.json",old_raw),("initial-state.json",initial_raw),("activation.json",_raw(report)),("baseline-source.json",baseline_raw),("baseline-manifest.json",manifest_raw),("baseline-receipt.json",baseline_receipt_raw)),final=final,extra_check=checkpoint)
                return receipt
            except Exception as error:
                owner._failed(receipt,error)
                raise

    def configure(self, body, now=None):
        from .options_root_eod import RULE_SHA256
        owner = self.owner
        owner._arguments(body, {"enabled", "acknowledge_source"})
        if type(body["enabled"]) is not bool or body["acknowledge_source"] is not True:
            raise ValueError("Choose an explicit KO source setting and acknowledge its research scope.")
        with owner._ownership():
            with owner._committing(owner.controls()) as db:
                cfg, brief = owner._policy(db)
                self._authority(db, owner._account(db), cfg, brief)
                replay = owner._reserve(db, "root_config", body, None, _now(now))
                if replay:
                    return replay
                source = self.config(db)
                source.update(enabled=body["enabled"], revision=source["revision"]+1)
                db.execute("UPDATE opt_root_meta SET config=? WHERE id=1", (_json(source),))
                receipt = {"request_id":body["request_id"], "action":"root_config", "status":"completed", "source":source,
                    "rule_sha256":RULE_SHA256, "artifacts":[], "model_calls":0, "simulated_funding_added":"0", "monthly_budget_reset":False, "replayed":False}
                owner._finish(db,receipt)
                return receipt

    def collect(self, body, now=None, *, collector=None):
        from . import options_root_eod as engine
        from . import options_root_eod_data as source
        owner, clock, receipt = self.owner, _now(now), None
        owner._arguments(body)
        ready, session, _ = self._window(clock)
        if not ready:
            raise HandoffConflict("Wait for the configured09:35Eastern prior-EOD check; no request made.")
        with owner._ownership(), ExitStack() as held:
            try:
                with owner.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    cfg, feed, internet, brief = self._permissions(db)
                    replay = owner._reserve(db,"root_collect",body,"root-feed-"+session.isoformat(),clock)
                    if replay:
                        return replay
                    receipt = {"request_id":body["request_id"],"action":"root_collect","status":"running","created_at":clock.isoformat(),"day":"root-feed-"+session.isoformat(),"artifacts":[],"model_calls":0,"replayed":False,"rule_sha256":engine.RULE_SHA256}
                with owner.database() as db:
                    account = owner._account(db)
                    self._authority(db,account,cfg,brief)
                    receipt["account_id"] = account["id"]
                    from .options_workflow import _paper_engine
                    engine=_paper_engine(account["state"])
                    bankroll_mode=account["state"].get("schema")==6
                    manager=owner.bankroll if bankroll_mode else self
                    count_key="bankroll_processed_count" if bankroll_mode else "root_processed_count"
                    data_table="opt_bankroll_data" if bankroll_mode else "opt_root_data"
                    state_table="opt_bankroll_state" if bankroll_mode else "opt_root_state"
                    receipt.update(rule_sha256=engine.RULE_SHA256,paper_schema=account["state"]["schema"])
                    if account["state"]["hold"]:
                        raise HandoffConflict("The shared account is held; fresh quotes cannot heal it.")
                    manager.verify_account(db,account["id"],account["state"],full=True)
                    runtime_check = held.enter_context(owner.eod.stress._runtime_lease())
                    contexts = owner.eod.stress.contexts(db,account["id"])
                    required = sorted({x["contract_id"] for x in (account["state"]["position"],account["state"]["pending"]) if x})
                def checkpoint():
                    runtime_check()
                    with owner.database() as db:
                        if self._policy(db) != (cfg,feed,internet,brief) or owner._epoch(db,cfg,brief) != account["policy"]:
                            raise HandoffConflict("KO collection permissions changed or were revoked.")
                bundle = collector(check=checkpoint,now=clock,expected_session=session,required_contracts=required) if collector else self._collect(cfg,feed,internet,brief,body["request_id"],required)
                checkpoint()
                bundle_raw = source.canonical(bundle)
                if len(bundle_raw)>12*1024*1024:
                    raise ValueError("The KO raw capture exceeds its fixed bound.")
                try:
                    normalized = source.normalize_bundle(bundle,expected_session=session,required_contracts=required)
                except (ValueError,TypeError,KeyError) as error:
                    receipt.update(source_failed=True,bundle_sha256=_digest(bundle_raw),dataset_adopted=False,fills_adopted=0)
                    failure = {"reason":str(error)[:1000],"dataset_adopted":False,"fills_adopted":0}
                    def reject(db):
                        raise ValueError("KO source validation failed; raw evidence retained, no financial adoption or automatic retry.")
                    owner._publish(cfg,brief,receipt,(("bundle.json",bundle_raw),("failure.json",_raw(failure))),final=reject,extra_check=checkpoint)
                    raise AssertionError("A rejected KO source cannot complete.")
                decision = clock if now is not None else _now()
                snapshot = normalized["snapshot"]
                advancement = engine.advance_paper(snapshot,account["state"],decided_at=decision.isoformat(),contexts=contexts)
                after = advancement["state"]
                adopted = after[count_key] == account["state"][count_key]+1
                creates_buy = any(e.get("type") in ("decision","fill") and e.get("action")=="BUY" for e in advancement["events"])
                before_raw,snapshot_raw,provenance_raw,contexts_raw,paper_raw,state_raw = _raw(account["state"]),source.canonical(snapshot),source.canonical(normalized["provenance"]),_raw(contexts),_raw(advancement),_raw(after)
                receipt.update(source_date=snapshot["source_date"],received_at=snapshot["received_at"],quote_observed_at=None,recorded_at=decision.isoformat(),
                    before_state_sha256=_digest(before_raw),bundle_sha256=_digest(bundle_raw),snapshot_sha256=_digest(snapshot_raw),provenance_sha256=_digest(provenance_raw),contexts_sha256=_digest(contexts_raw),
                    state_sha256=_digest(state_raw),paper_status=advancement["status"],paper_events=len(advancement["events"]),accepted_snapshot=adopted,
                    required_contracts=required,research_only=True,execution_qualified=False,partial_chain=snapshot["partial_chain"],paper_schema=account["state"]["schema"])
                def final(db):
                    checkpoint()
                    current = owner._account(db)
                    if not current or _basis(current)!=_basis(account):
                        raise HandoffConflict("The shared account changed before KO publication.")
                    if creates_buy and _digest(_raw(owner.eod.stress.contexts(db,account["id"])))!=receipt["contexts_sha256"]:
                        raise HandoffConflict("The benchmark input changed before entry publication.")
                    manager.verify_account(db,account["id"],account["state"],full=True)
                    manager.verify(receipt)
                    if db.execute("SELECT count(*) FROM opt_events").fetchone()[0]+len(advancement["events"])>MAX_EVENTS:
                        raise ValueError("The shared event quota is full; no extra allowance.")
                    if adopted:
                        tables=("opt_eod_data","opt_root_data")+(("opt_bankroll_data",) if owner.bankroll._exists(db) else ())
                        used = sum(db.execute("SELECT coalesce(sum(bytes),0) FROM "+table).fetchone()[0] for table in tables)
                        count = sum(db.execute("SELECT count(*) FROM "+table).fetchone()[0] for table in tables)
                        if used+len(snapshot_raw)>MAX_DATA or count>=100:
                            raise ValueError("The shared source evidence quota is full.")
                        db.execute("INSERT INTO "+data_table+" VALUES(?,?,?,?,?)",(snapshot["source_date"],_digest(snapshot_raw),snapshot_raw,len(snapshot_raw),body["request_id"]))
                    db.execute("UPDATE opt_accounts SET state=? WHERE id=?",(state_raw.decode(),account["id"]))
                    db.execute("UPDATE "+state_table+" SET request_id=?,state_sha256=? WHERE account_id=?",(body["request_id"],_digest(state_raw),account["id"]))
                    for event in advancement["events"]:
                        db.execute("INSERT INTO opt_events(account_id,run_id,value) VALUES(?,?,?)",(account["id"],body["request_id"],_json(event)))
                owner._publish(cfg,brief,receipt,(("before-state.json",before_raw),("bundle.json",bundle_raw),("snapshot.json",snapshot_raw),("provenance.json",provenance_raw),("contexts.json",contexts_raw),("paper.json",paper_raw)),final=final,extra_check=checkpoint)
                return receipt
            except Exception as error:
                owner._failed(receipt,error)
                raise

    def run_due(self, now=None):
        clock = _now(now)
        ready, session, _ = self._window(clock)
        if not ready:
            return None
        with self.owner.database() as db:
            try:
                cfg,feed,_,brief = self._permissions(db)
            except HandoffConflict:
                return None
            if (clock < datetime.fromisoformat(feed["activation_at"])
                    or db.execute("SELECT 1 FROM opt_runs WHERE day=? OR status IN ('running','failed') LIMIT 1",("root-feed-"+session.isoformat(),)).fetchone()):
                return None
            try:
                account = self.owner._account(db)
                self._authority(db,account,cfg,brief)
            except HandoffConflict:
                return None
            except ValueError:
                # Record the known integrity failure once before any worker.
                # The failed reservation stops recurrence; no source retry.
                account = None
            if account is not None and (account["state"]["hold"]
                    or account["state"]["history"] and session.isoformat()<=account["state"]["history"][-1]["source_date"]):
                return None
            revision = self.owner._meta(db)[0]
        try:
            return self.collect({"request_id":"options-ko-"+session.isoformat(),"expected_revision":revision},now)
        except HandoffConflict:
            return None

    def _collect(self, cfg, feed, internet, brief, request_id, required):
        from .options_eod_worker import _run_collector
        return _run_collector(self,cfg,feed,internet,brief,request_id,required,profile="KO")

    def snapshot(self, db, now, account):
        from . import options_root_eod as engine
        bankroll_mode=bool(account and account["state"].get("schema")==6)
        if bankroll_mode:
            from . import options_bankroll_eod as engine
        cfg = self.config(db)
        ready, expected, slot = self._window(now)
        last = db.execute("SELECT receipt FROM opt_runs WHERE action='root_collect' ORDER BY rowid DESC LIMIT 1").fetchone() if self.owner._exists(db) else None
        row = db.execute("SELECT value,bytes,digest FROM opt_bankroll_data ORDER BY source_date DESC LIMIT 1").fetchone() if bankroll_mode else None
        if row is None:
            row = db.execute("SELECT value,bytes,digest FROM opt_root_data ORDER BY source_date DESC LIMIT 1").fetchone() if self._exists(db) else None
        quality = None
        if row:
            raw = bytes(row[0])
            if len(raw)!=row[1] or _digest(raw)!=row[2]:
                raise ValueError("The KO source cache changed.")
            value = engine.validate_snapshot(_load(raw))
            quality = {"symbol":"KO","last_source_date":value["source_date"],"last_received_at":value["received_at"],"last_observed_at":None,
                "quote_time_basis":"vendor_eod_date_only","quotes":len(value["quotes"]),"partial_chain":value["partial_chain"],
                "fresh_now":value["source_date"]==expected.isoformat(),"expected_last_session_now":expected.isoformat(),"execution_qualified":False}
        state = (account or {}).get("state",{})
        activation = state.get("root_policy",{}).get("activation",{})
        deadline = (state.get("position") or {}).get("forced_exit_session")
        last_source = state.get("last_source_date")
        return {"schema":1,"config":cfg,"last_run":_load(last[0]) if last else None,"quality":quality,
            "collection_window_open":ready,"expected_market_session":expected.isoformat(),"collection_check_at":slot.isoformat(),
            "rule":deepcopy(engine.RULE),"rule_sha256":engine.RULE_SHA256,"migration_eligible":False,"baseline_count":len(activation.get("baseline",[])),
            "stress":self.owner.eod.stress.snapshot(db,now,account),"source_url":"https://www.onclickmedia.com/pricing","model_calls":0,
            "quote_scope":"Experimental partial KO date-only EOD paper price proxies; unknown option-event clock and unverified execution.",
            "publication":"One prior-EOD check after09:35Eastern on a regular session. Offline retains saved results; no simulated current quotes or automatic retries.",
            "publication_assurance":{"waiting_for_disposal_source":bool(deadline and expected.isoformat()>=deadline and (last_source is None or last_source<deadline)),"deadline_source_date":deadline,"last_source_date":last_source},
            "research_only":True,"execution_qualified":False,"legacy_root":"F","same_account":True,"new_funding":"0"}
