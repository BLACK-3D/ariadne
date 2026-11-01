"""One shared-account, date-only F paper experiment with retained legacy evidence.

The offline owner never acquires quotes directly.  A fixed bounded worker may
return raw public research data; the owner independently normalizes those bytes
before publishing any simulated state.  No brokerage/order interface exists.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
from datetime import date, datetime, timedelta
import json
import threading

from .handoff import HandoffConflict, _identifier, _load
from .internet import SETTINGS_KEY
from .options_workflow import _json, _now, _digest, MAX_DATA, MAX_EVENTS

SOURCE_ID = "onclick_f_eod_v1"
DEFAULT = {"schema": 1, "initialized": False, "enabled": False, "revision": 0,
           "activation_at": None, "provider": SOURCE_ID, "symbol": "F",
           "timezone": "America/New_York", "hour": 9, "minute": 35}
MAX_BASELINE = 2 * 1024 * 1024
MAX_CAPTURE = 12 * 1024 * 1024
_MIGRATION_FILES = {"previous-state.json", "previous-forward.json", "previous-feed.json",
                    "baseline-source.json", "baseline.json", "baseline-manifest.json", "migration.json"}
_CAPTURE_FILES = {"bundle.json", "snapshot.json", "provenance.json", "paper.json"}


def _raw(value):
    return _json(value).encode("utf-8")


def _basis(account):
    """Compare owner intent/state, not clock-derived display summaries."""
    return {key: account[key] for key in ("id", "state", "started_at", "policy")}


def _stress_extension(previous, current):
    """Bind each retained policy to the preceding immutable gate cursor."""
    delta = current["context_count"] - previous["context_count"]
    if current["activation"] != previous["activation"] or not 0 <= delta <= 2:
        raise ValueError("Retained stress policy lost its activation or ordered gate count.")
    old = {row["sequence"]: row for row in previous["context_history"]}
    for row in current["context_history"]:
        if row["sequence"] in old and row != old[row["sequence"]]:
            raise ValueError("An older retained stress gate was rewritten.")
    added = [row for row in current["context_history"] if row["sequence"] > previous["context_count"]]
    if delta == 0:
        if current != previous:
            raise ValueError("Retained stress policy changed without a new gate.")
    elif (len(added) != delta or added[0]["sequence"] != previous["context_count"]+1
          or added[0]["previous_sha256"] != previous["context_prefix_sha256"]):
        raise ValueError("Retained stress policy gates do not extend the preceding cursor.")


class OptionsEodWorkflow:
    def __init__(self, owner):
        self.owner = owner
        self.lock = threading.RLock()
        self.processes = set()
        from .options_stress_workflow import OptionsStressWorkflow
        self.stress = OptionsStressWorkflow(self)

    def stop(self):
        with self.lock:
            for process in tuple(self.processes):
                if process.poll() is None:
                    process.kill()

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opt_eod_meta'").fetchone() is not None

    def _schema(self, db):
        db.execute("CREATE TABLE IF NOT EXISTS opt_eod_meta(id INTEGER PRIMARY KEY CHECK(id=1),config TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_eod_migrations(account_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,old_state TEXT NOT NULL,old_state_sha256 TEXT NOT NULL,old_data_sha256 TEXT NOT NULL,old_feed TEXT NOT NULL,expected_legacy_feed TEXT NOT NULL,baseline TEXT NOT NULL,initial_state TEXT NOT NULL,initial_state_sha256 TEXT NOT NULL,activated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_eod_data(source_date TEXT PRIMARY KEY,digest TEXT NOT NULL,value BLOB NOT NULL,bytes INTEGER NOT NULL,request_id TEXT UNIQUE NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS opt_eod_state(account_id TEXT PRIMARY KEY,request_id TEXT NOT NULL,state_sha256 TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO opt_eod_meta VALUES(1,?)", (_json(DEFAULT),))

    def config(self, db):
        if not self._exists(db):
            return deepcopy(DEFAULT)
        row = db.execute("SELECT config FROM opt_eod_meta WHERE id=1").fetchone()
        if not row:
            raise ValueError("The EOD source configuration is missing; no reset is permitted.")
        cfg = _load(row[0])
        if (type(cfg) is not dict or set(cfg) != set(DEFAULT)
                or any(type(cfg[key]) is not bool for key in ("initialized", "enabled"))
                or type(cfg["revision"]) is not int or cfg["revision"] < 0
                or any(cfg[key] != DEFAULT[key] for key in ("schema", "provider", "symbol", "timezone", "hour", "minute"))
                or type(cfg["schema"]) is not int
                or cfg["initialized"] != (cfg["activation_at"] is not None)
                or cfg["enabled"] and not cfg["initialized"]):
            raise ValueError("The fixed experimental EOD source contract changed.")
        if cfg["activation_at"] is not None:
            _now(datetime.fromisoformat(cfg["activation_at"]))
        return cfg

    def _permissions(self, db):
        cfg, brief = self.owner._policy(db)
        feed = self.config(db)
        internet = self.owner.store.setting(SETTINGS_KEY, None)
        if (not feed["initialized"] or not feed["enabled"] or type(internet) is not dict
                or internet.get("enabled") is not True or type(internet.get("revision")) is not int):
            raise HandoffConflict("The experimental EOD source or owner Internet permission is disabled.")
        return cfg, feed, internet, brief

    def _account_authority(self, db, account, cfg, brief):
        if (not account or account["state"].get("schema") not in (3,4)
                or account["state"].get("source_id") != SOURCE_ID
                or account["policy"] != self.owner._epoch(db, cfg, brief)):
            raise HandoffConflict("The EOD account or permission epoch changed.")

    def _policy(self, db):
        cfg, feed, internet, brief = self._permissions(db)
        self._account_authority(db, self.owner._account(db), cfg, brief)
        return cfg, feed, internet, brief

    @staticmethod
    def _window(now):
        from .briefing import _tz
        from .qqq_data import exchange_day, is_session, last_closed_session, session_close_utc
        day = exchange_day(now)
        slot = datetime(day.year, day.month, day.day, 8, 35, tzinfo=_tz("America/Chicago"))
        ready = is_session(day) and now >= slot and now < session_close_utc(day) - timedelta(minutes=5)
        return ready, last_closed_session(now), slot

    @staticmethod
    def _legacy_witnesses(db, account_id):
        values = {}
        for table in ("opt_migrations", "opt_feed_activation"):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                values[table] = None
                continue
            row = db.execute("SELECT * FROM " + table + " WHERE account_id=?", (account_id,)).fetchone()
            values[table] = dict(row) if row else None
        return values

    def _recorded(self, db, request_id):
        _identifier(request_id)
        run = db.execute("SELECT status,substr(receipt,1,16001) receipt FROM opt_runs WHERE request_id=?", (request_id,)).fetchone()
        report = db.execute("SELECT substr(receipt,1,16001) receipt FROM opt_reports WHERE request_id=?", (request_id,)).fetchone()
        if (not run or run["status"] != "completed" or not report
                or len(run["receipt"].encode()) > 16000 or len(report["receipt"].encode()) > 16000
                or _load(run["receipt"]) != _load(report["receipt"])):
            raise ValueError("The EOD state lacks its completed immutable evidence receipt.")
        receipt = _load(run["receipt"])
        if receipt.get("request_id") != request_id or receipt.get("status") != "completed":
            raise ValueError("The EOD completed receipt identity changed.")
        return receipt

    def _completed(self, db, request_id):
        receipt = self._recorded(db, request_id)
        self.verify(receipt)
        return receipt

    def verify(self, receipt):
        from .options_eod import RULE_SHA256
        _identifier(receipt.get("request_id"))
        action = receipt.get("action")
        if action == "stress_activate":
            return self.stress.verify(receipt)
        if action == "eod_config":
            if receipt.get("artifacts") != []:
                raise ValueError("EOD configuration cannot adopt external artifacts.")
            return {}
        if action == "eod_collect" and receipt.get("source_failed") is True:
            names = {"bundle.json", "failure.json"}
        else:
            names = _MIGRATION_FILES if action == "eod_migrate" else _CAPTURE_FILES if action == "eod_collect" else None
        if names is None or receipt.get("rule_sha256") != RULE_SHA256:
            raise ValueError("The EOD receipt does not match its fixed action/rule.")
        base = "reports/options-recovery/" + receipt["request_id"] + "/"
        artifacts = receipt.get("artifacts")
        if (type(artifacts) is not list or len(artifacts) != len(names)
                or {item.get("path") for item in artifacts} != {base + name for name in names}):
            raise ValueError("The experimental EOD artifact contract is incomplete.")
        saved = {}
        for item in artifacts:
            raw = self.owner._read(item["path"], MAX_DATA)
            if len(raw) != item["bytes"] or _digest(raw) != item["sha256"]:
                raise ValueError("EOD evidence is missing or changed; it is never regenerated.")
            saved[item["path"][len(base):]] = raw
        if action == "eod_collect" and receipt.get("source_failed") is True:
            failure = _load(saved["failure.json"])
            if (_digest(saved["bundle.json"]) != receipt.get("bundle_sha256")
                    or failure.get("dataset_adopted") is not False or failure.get("fills_adopted") != 0):
                raise ValueError("Rejected EOD evidence lost its identity or non-adoption scope.")
            return saved
        if action == "eod_migrate":
            report = _load(saved["migration.json"])
            if (report.get("rule_sha256") != RULE_SHA256 or report.get("new_funding") != "0"
                    or report.get("monthly_budget_reset") is not False
                    or report.get("old_state_sha256") != receipt.get("old_state_sha256")
                    or _digest(saved["previous-state.json"]) != receipt.get("old_state_sha256")
                    or _digest(saved["previous-forward.json"]) != receipt.get("old_data_sha256")
                    or _digest(saved["previous-feed.json"]) != report.get("old_feed_sha256")
                    or _digest(saved["baseline.json"]) != report.get("baseline_sha256")
                    or _digest(saved["baseline-source.json"]) != report.get("baseline_source_sha256")
                    or _digest(saved["baseline-manifest.json"]) != report.get("baseline_manifest_sha256")):
                raise ValueError("The EOD migration lost its capital or input identity.")
        else:
            snapshot = _load(saved["snapshot.json"])
            paper = _load(saved["paper.json"])
            if (_digest(saved["bundle.json"]) != receipt.get("bundle_sha256")
                    or _digest(saved["snapshot.json"]) != receipt.get("snapshot_sha256")
                    or _digest(saved["provenance.json"]) != receipt.get("provenance_sha256")
                    or snapshot.get("source_date") != receipt.get("source_date")
                    or snapshot.get("quote_observed_at") is not None
                    or _digest(_raw(paper.get("state"))) != receipt.get("state_sha256")
                    or paper.get("summary", {}).get("research_only") is not True
                    or paper.get("summary", {}).get("execution_qualified") is not False):
                raise ValueError("The EOD capture lost its source/state or research-only identity.")
        return saved

    def _audit_prefix(self, db, account_id, state, *, full=False):
        """Bind the complete bounded accepted prefix to immutable receipts.

        Passive account reads validate row lengths/digests against completed
        receipt metadata and the ordered state hash chain. Mutation boundaries
        additionally rehash every retained artifact and revalidate actual cache
        bytes; no older accepted source can silently disappear or be revised.
        """
        from .options_eod import RULE_SHA256, validate_state, validate_snapshot
        committed_stress = None
        if type(state) is dict and state.get("schema")==4:
            from .options_stress_eod import legacy_state
            committed_stress = state
            state=legacy_state(state)
        checked = validate_state(state)
        published, previous_policy, activation_count = None, None, None
        if full and committed_stress is not None:
            from .options_stress_eod import validate_state as validate_stress
            migration = db.execute("SELECT initial_state,initial_state_sha256 FROM opt_stress_migrations WHERE account_id=?", (account_id,)).fetchone()
            if not migration or _digest(migration["initial_state"].encode()) != migration["initial_state_sha256"]:
                raise ValueError("Retained stress policy lost its immutable initial state.")
            initial = validate_stress(_load(migration["initial_state"]))
            previous_policy = initial["stress_policy"]
            activation_count = previous_policy["activation"]["initial_processed_count"]
            published = {w["witness_sha256"]: w for w in self.stress._verified_context_rows(db, account_id)}
        total = db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM opt_eod_data").fetchone()
        if (type(total[0]) is not int or not 0 <= total[0] <= 100 or total[0] != checked["processed_count"]
                or type(total[1]) is not int or not 0 <= total[1] <= MAX_DATA):
            raise ValueError("The bounded accepted EOD prefix lost its count/byte identity.")
        rows = db.execute("SELECT source_date,digest,bytes,length(value) actual_bytes,request_id FROM opt_eod_data ORDER BY source_date LIMIT 101").fetchall()
        if len(rows) != total[0]:
            raise ValueError("The accepted EOD source prefix changed during inspection.")
        cursor = _digest(_raw({"rule_sha256": RULE_SHA256, "migration": checked["migration"]}))
        chain, references, previous_day, previous_received = [], list(checked["migration"]["baseline"]), None, None
        for index, row in enumerate(rows):
            if (type(row["bytes"]) is not int or not 1 <= row["bytes"] <= MAX_DATA
                    or row["actual_bytes"] != row["bytes"]):
                raise ValueError("An accepted EOD prefix row lost its bounded byte identity.")
            try:
                day = date.fromisoformat(row["source_date"])
            except (TypeError, ValueError):
                raise ValueError("An accepted EOD prefix source date changed.") from None
            from .qqq_data import is_session, session_dates
            if (day.isoformat() != row["source_date"] or not is_session(day)
                    or previous_day is not None and len(session_dates(previous_day+timedelta(days=1), day)) != 1):
                raise ValueError("The accepted EOD prefix has a missing or reordered source session.")
            receipt = self._recorded(db, row["request_id"])
            base = "reports/options-recovery/"+row["request_id"]+"/"
            artifacts = receipt.get("artifacts")
            if (receipt.get("action") != "eod_collect" or receipt.get("accepted_snapshot") is not True
                    or receipt.get("source_failed") is True or receipt.get("account_id") != account_id
                    or receipt.get("rule_sha256") != RULE_SHA256 or receipt.get("source_date") != row["source_date"]
                    or receipt.get("snapshot_sha256") != row["digest"]
                    or receipt.get("quote_observed_at") is not None
                    or receipt.get("research_only") is not True or receipt.get("execution_qualified") is not False
                    or type(artifacts) is not list or len(artifacts) != len(_CAPTURE_FILES)
                    or {item.get("path") for item in artifacts} != {base+name for name in _CAPTURE_FILES}):
                raise ValueError("An accepted EOD prefix row differs from its completed receipt.")
            snapshot_item = next(item for item in artifacts if item["path"] == base+"snapshot.json")
            if snapshot_item.get("bytes") != row["bytes"] or snapshot_item.get("sha256") != row["digest"]:
                raise ValueError("An accepted EOD prefix snapshot lost its immutable artifact metadata.")
            if type(receipt.get("received_at")) is not str or len(receipt["received_at"]) > 40:
                raise ValueError("An accepted EOD prefix lacks its bounded actual receipt clock.")
            received = _now(datetime.fromisoformat(receipt["received_at"].replace("Z", "+00:00")))
            if previous_received is not None and received < previous_received:
                raise ValueError("Accepted EOD prefix receipt clocks moved backwards.")
            next_cursor = _digest(_raw({"previous_sha256": cursor, "snapshot_sha256": row["digest"]}))
            chain.append({"source_date": row["source_date"], "snapshot_sha256": row["digest"],
                          "previous_sha256": cursor, "prefix_sha256": next_cursor})
            if full:
                saved = self.verify(receipt)
                data_row = db.execute("SELECT substr(value,1,?) FROM opt_eod_data WHERE source_date=?", (row["bytes"]+1, row["source_date"])).fetchone()
                value = bytes(data_row[0]) if data_row else b""
                if (len(value) != row["bytes"] or _digest(value) != row["digest"] or saved["snapshot.json"] != value):
                    raise ValueError("An older accepted EOD prefix cache row changed or disappeared.")
                snapshot = validate_snapshot(_load(value))
                if (snapshot["source_date"] != row["source_date"] or snapshot["received_at"] != receipt["received_at"]
                        or _raw(snapshot) != value):
                    raise ValueError("The EOD prefix snapshot disagrees with its retained source identity.")
                retained_state=_load(saved["paper.json"])["state"]
                if type(retained_state) is dict and retained_state.get("schema")==4:
                    from .options_stress_eod import legacy_state
                    if previous_policy is None or index < activation_count:
                        raise ValueError("An EOD source transition has an unexpected stress policy.")
                    policy = retained_state["stress_policy"]
                    retained_state=legacy_state(retained_state)
                    for record in policy["context_history"]:
                        witness = record["gate"]["witness"]
                        if witness is not None and published.get(witness["witness_sha256"]) != witness:
                            raise ValueError("An older financial policy gate lost its retained published context.")
                    _stress_extension(previous_policy, policy)
                    previous_policy = policy
                elif previous_policy is not None and index >= activation_count:
                    raise ValueError("An accepted EOD transition dropped its activated stress policy.")
                prior_state = validate_state(retained_state)
                if (prior_state["migration"] != checked["migration"] or prior_state["processed_count"] != index+1
                        or prior_state["prefix_sha256"] != next_cursor or prior_state["last_source_date"] != row["source_date"]
                        or prior_state["last_snapshot_sha256"] != row["digest"]):
                    raise ValueError("An accepted EOD prefix transition lost its ordered state cursor.")
                references.append({key: snapshot[key] for key in ("source_date", "underlying_close", "received_at", "stock_sha256")})
            previous_day, previous_received, cursor = day, received, next_cursor
        if (cursor != checked["prefix_sha256"] or chain[-15:] != checked["cursor_history"]
                or (rows[-1]["source_date"] if rows else None) != checked["last_source_date"]
                or (rows[-1]["digest"] if rows else None) != checked["last_snapshot_sha256"]
                or (previous_received.isoformat() if previous_received else None) != checked["last_received_at"]):
            raise ValueError("The EOD accepted prefix no longer matches the active state cursor.")
        if full and references[-15:] != checked["history"]:
            raise ValueError("The EOD indicator history differs from retained accepted source prices.")
        if full and committed_stress is not None and committed_stress["stress_policy"] != previous_policy:
            raise ValueError("The active stress policy differs from its retained gate history.")
        return {"snapshots": len(rows), "bytes": total[1], "prefix_sha256": cursor}

    def verify_account(self, db, account_id, state, *, committed_state=None):
        from .options_eod import migrate_flat, validate_state
        checked = validate_state(state)
        if committed_state is not None:
            from .options_stress_eod import legacy_state
            if legacy_state(committed_state)!=checked:
                raise ValueError("The stress account changed its original ledger projection.")
        cfg = self.config(db)
        if not cfg["initialized"]:
            raise ValueError("An EOD account has no source activation witness.")
        row = db.execute("SELECT * FROM opt_eod_migrations WHERE account_id=?", (account_id,)).fetchone()
        anchor = db.execute("SELECT * FROM opt_eod_state WHERE account_id=?", (account_id,)).fetchone()
        if not row or not anchor or row["activated_at"] != cfg["activation_at"]:
            raise ValueError("The EOD account migration/state witness is missing or changed.")
        old_raw = row["old_state"].encode()
        if _digest(old_raw) != row["old_state_sha256"]:
            raise ValueError("The retained original options state changed.")
        baseline = _load(row["baseline"])
        initial = migrate_flat(_load(old_raw), activated_at=row["activated_at"], source_id=SOURCE_ID,
                               old_state_sha256=row["old_state_sha256"], old_data_sha256=row["old_data_sha256"], baseline=baseline)
        if (_raw(initial) != row["initial_state"].encode() or _digest(_raw(initial)) != row["initial_state_sha256"]
                or checked["migration"] != initial["migration"]
                or any(checked[key] != initial[key] for key in ("started_at", "portfolio_activation_at", "source_id"))):
            raise ValueError("The EOD migration lost its original account/funding provenance.")
        migration = self._completed(db, row["request_id"])
        report_path = next(item["path"] for item in migration["artifacts"] if item["path"].endswith("/migration.json"))
        report = _load(self.owner._read(report_path, 128 * 1024))
        if (report["legacy_witnesses_sha256"] != _digest(_raw(self._legacy_witnesses(db, account_id)))
                or self.owner.feed.config(db) != _load(row["expected_legacy_feed"])):
            raise ValueError("The retained legacy source/upgrade witnesses changed.")
        cache = db.execute("SELECT digest FROM opt_data WHERE id=2").fetchone()
        if (cache[0] if cache else _digest(b"null")) != row["old_data_sha256"]:
            raise ValueError("The retained original forward source changed.")
        account_row = db.execute("SELECT started_at FROM opt_accounts WHERE id=?", (account_id,)).fetchone()
        if not account_row or account_row[0] != checked["started_at"]:
            raise ValueError("The shared account identity changed.")
        transition = migration if anchor["request_id"] == row["request_id"] else self._completed(db, anchor["request_id"])
        if (transition.get("account_id") != account_id or _digest(_raw(checked if committed_state is None else committed_state)) != anchor["state_sha256"]
                or transition.get("state_sha256") != anchor["state_sha256"]):
            raise ValueError("The active EOD state differs from its completed transition.")
        self._audit_prefix(db, account_id, checked)

    def snapshot(self, db, now, account):
        from .options_eod import RULE, RULE_SHA256, migrate_flat
        cfg = self.config(db)
        ready, expected, slot = self._window(now)
        last = db.execute("SELECT receipt FROM opt_runs WHERE action='eod_collect' ORDER BY rowid DESC LIMIT 1").fetchone() if self.owner._exists(db) else None
        accepted = db.execute("SELECT receipt FROM opt_runs WHERE action='eod_collect' AND status='completed' ORDER BY rowid DESC LIMIT 1").fetchone() if self.owner._exists(db) else None
        if accepted:
            self.verify(_load(accepted[0]))
        row = db.execute("SELECT digest,value,bytes,request_id FROM opt_eod_data ORDER BY source_date DESC LIMIT 1").fetchone() if self._exists(db) else None
        quality = None
        if row:
            raw = bytes(row["value"])
            if len(raw) != row["bytes"] or len(raw) > MAX_DATA or _digest(raw) != row["digest"]:
                raise ValueError("The accepted EOD snapshot failed its retained byte identity.")
            source_receipt = self._completed(db, row["request_id"])
            if source_receipt.get("accepted_snapshot") is not True or source_receipt.get("snapshot_sha256") != row["digest"]:
                raise ValueError("The EOD source cache lost its completed accepted snapshot.")
            from .options_eod_data import validate_snapshot
            value = validate_snapshot(_load(raw))
            total = db.execute("SELECT count(*),min(source_date) FROM opt_eod_data").fetchone()
            quality = {"symbol": "F", "snapshots": total[0], "first_source_date": total[1],
                       "last_source_date": value["source_date"], "last_observed_at": None,
                       "last_received_at": value["received_at"], "quote_time_basis": "vendor_eod_date_only",
                       "partial_chain": value["partial_chain"], "quotes": len(value["quotes"]),
                       "fresh_now": value["source_date"] == expected.isoformat(),
                       "expected_last_session_now": expected.isoformat(), "execution_qualified": False,
                       "source_independently_verified": False, "source_sha256": value["source_sha256"],
                       "stock_sha256": value["stock_sha256"], "scope": "Saved partial vendor EOD dates and price proxies; unknown option quote-event clocks, no live execution or guaranteed source reliability."}
        used = db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM opt_reports").fetchone() if self.owner._exists(db) else (0, 0)
        position = (account or {}).get("state", {}).get("position")
        deadline = (position or {}).get("forced_exit_session")
        last_source = (account or {}).get("state", {}).get("last_source_date")
        pending = bool(deadline and expected.isoformat() >= deadline and (last_source is None or last_source < deadline))
        eligible = False
        if not cfg["initialized"] and account and account["state"].get("schema") == 2:
            try:
                forward = db.execute("SELECT digest FROM opt_data WHERE id=2").fetchone()
                migrate_flat(account["state"], activated_at=now.isoformat(), source_id=SOURCE_ID,
                             old_state_sha256=_digest(_raw(account["state"])),
                             old_data_sha256=forward[0] if forward else _digest(b"null"))
                eligible = account["policy"] == self.owner._epoch(db, self.owner._meta(db)[1], self.owner.controls())
            except (ValueError, HandoffConflict):
                eligible = False
        baseline_count = len((account or {}).get("state", {}).get("migration", {}).get("baseline", [])) if (account or {}).get("state", {}).get("schema") in (3,4) else 0
        return {"schema": 1, "config": cfg, "last_run": _load(last[0]) if last else None,
                "quality": quality, "collection_window_open": ready, "expected_market_session": expected.isoformat(),
                "collection_check_at": slot.isoformat(), "rule": deepcopy(RULE), "rule_sha256": RULE_SHA256,
                "migration_eligible": eligible, "baseline_count": baseline_count,
                "stress": self.stress.snapshot(db, now, account),
                "source_scope": "Personal educational research using a vendor's partial date-only chain and unadjusted stock references; original-provider rights, option quote-event clocks, standard deliverables and ongoing reliability are not independently established. No execution qualification or real orders.",
                "publication": "One configured collection cycle after09:35Eastern on a market session, for the previous completed source session; provider publication time is not guaranteed. Failed/uncertain work is never automatically retried.",
                "quote_scope": "Experimental partial date-only F EOD paper price proxies; not live quotes or execution-qualified observations.",
                "source_url": "https://www.onclickmedia.com/About", "model_calls": 0,
                "execution_qualified": False, "research_only": True,
                "retention": {"remaining_evidence_operations": max(0, 100-used[0]), "remaining_evidence_bytes": max(0, 128*1024*1024-used[1])},
                "publication_assurance": {"waiting_for_disposal_source": pending, "deadline_source_date": deadline,
                                          "last_source_date": last_source, "scope": "Waiting for EOD publication is not a verified sale or authority for funding rollover. Missing/skipped mandatory source evidence holds the whole account."}}

    def _baseline(self, path, clock):
        if path is None:
            return [], b"null", b"null"
        manifest_raw = self.owner._read(path, 64 * 1024)
        manifest = _load(manifest_raw)
        keys = {"schema", "kind", "raw_path", "raw_sha256", "received_at", "through_date"}
        if (type(manifest) is not dict or set(manifest) != keys or type(manifest["schema"]) is not int
                or manifest["schema"] != 1 or manifest["kind"] != "onclick_f_stock_baseline"):
            raise ValueError("Use the exact onclick_f_stock_baseline manifest.")
        self.owner.registry._path(manifest["raw_path"])
        if type(manifest["raw_sha256"]) is not str or len(manifest["raw_sha256"]) != 64 or any(char not in "0123456789abcdef" for char in manifest["raw_sha256"]):
            raise ValueError("The stock baseline requires its exact lowercase SHA-256.")
        if type(manifest["received_at"]) is not str or len(manifest["received_at"]) > 40:
            raise ValueError("Use an aware actual stock-baseline receipt clock.")
        received = _now(datetime.fromisoformat(manifest["received_at"].replace("Z", "+00:00")))
        if received > clock:
            raise ValueError("The stock baseline cannot be received after activation.")
        raw = self.owner._read(manifest["raw_path"], MAX_BASELINE)
        if _digest(raw) != manifest["raw_sha256"]:
            raise ValueError("The original stock baseline bytes changed.")
        from .options_eod_data import load_baseline
        baseline = load_baseline(raw, received_at=received, through_date=manifest["through_date"])
        return baseline, raw, manifest_raw

    def migrate(self, body, now=None):
        owner = self.owner
        owner._arguments(body, {"acknowledge_simulated", "acknowledge_partial_eod", "baseline_path"})
        if body["acknowledge_simulated"] is not True or body["acknowledge_partial_eod"] is not True:
            raise ValueError("Explicitly acknowledge the simulated partial date-only EOD model.")
        if body["baseline_path"] is not None:
            owner.registry._path(body["baseline_path"])
        clock, receipt = _now(now), None
        from .options_eod import migrate_flat, RULE_SHA256
        with owner._ownership():
            try:
                with owner.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    owner._schema(db)
                    cfg, brief = owner._policy(db)
                    replay = owner._reserve(db, "eod_migrate", body, None, clock)
                    if replay:
                        return replay
                    receipt = {"request_id": body["request_id"], "action": "eod_migrate", "status": "running", "created_at": clock.isoformat(), "artifacts": [], "model_calls": 0, "replayed": False, "rule_sha256": RULE_SHA256}
                    self._schema(db)
                    feed = self.config(db)
                    account = owner._account(db, clock)
                    if feed["initialized"] or not account or account["state"].get("schema") != 2:
                        raise HandoffConflict("Migrate the one existing healthy schema2 portfolio; no extra account or repeated migration.")
                    if account["policy"] != owner._epoch(db, cfg, brief):
                        raise HandoffConflict("Paper authority changed; migration cannot cross a pause.")
                    old_row = db.execute("SELECT state FROM opt_accounts WHERE id=?", (account["id"],)).fetchone()
                    old_raw = old_row[0].encode()
                    if _load(old_raw) != account["state"]:
                        raise ValueError("The original account state changed during capture.")
                    forward = owner._data(db, forward=True)
                    forward_raw = forward["raw"] if forward else b"null"
                    legacy_feed = owner.feed.config(db)
                    expected_legacy = deepcopy(legacy_feed)
                    if legacy_feed["initialized"]:
                        expected_legacy.update(enabled=False, revision=legacy_feed["revision"]+1)
                    witnesses_sha = _digest(_raw(self._legacy_witnesses(db, account["id"])))
                def checkpoint():
                    with owner.database() as db:
                        current, current_brief = owner._policy(db, cfg)
                        if current_brief != brief or owner._epoch(db, current, current_brief) != account["policy"] or self.config(db) != feed:
                            raise HandoffConflict("EOD migration permissions changed or were revoked.")
                checkpoint()
                baseline, baseline_raw, manifest_raw = self._baseline(body["baseline_path"], clock)
                initial = migrate_flat(account["state"], activated_at=clock.isoformat(), source_id=SOURCE_ID,
                                       old_state_sha256=_digest(old_raw), old_data_sha256=_digest(forward_raw), baseline=baseline)
                checkpoint()
                initial_raw = _raw(initial)
                report = {"schema": 1, "account_id": account["id"], "activated_at": clock.isoformat(),
                          "rule_sha256": RULE_SHA256, "source_id": SOURCE_ID, "symbol": "F",
                          "old_state_sha256": _digest(old_raw), "old_data_sha256": _digest(forward_raw),
                          "old_feed_sha256": _digest(_raw(legacy_feed)), "legacy_witnesses_sha256": witnesses_sha,
                          "baseline_sha256": _digest(_raw(baseline)), "baseline_source_sha256": _digest(baseline_raw),
                          "baseline_manifest_sha256": _digest(manifest_raw), "baseline_rows": len(baseline),
                          "initial_state_sha256": _digest(initial_raw), "new_funding": "0", "monthly_budget_reset": False,
                          "research_only": True, "execution_qualified": False}
                receipt.update(account_id=account["id"], old_state_sha256=_digest(old_raw), old_data_sha256=_digest(forward_raw),
                               state_sha256=_digest(initial_raw), baseline_rows=len(baseline), source_id=SOURCE_ID,
                               simulated_funding_added="0", monthly_budget_reset=False, research_only=True, execution_qualified=False)
                def final(db):
                    checkpoint()
                    current = owner._account(db, clock)
                    if not current or _basis(current) != _basis(account) or owner.feed.config(db) != legacy_feed:
                        raise HandoffConflict("The original shared account/source changed before migration publication.")
                    if _digest(_raw(self._legacy_witnesses(db, account["id"]))) != witnesses_sha:
                        raise HandoffConflict("The original source/upgrade witness changed before publication.")
                    if legacy_feed["initialized"]:
                        db.execute("UPDATE opt_feed_meta SET config=? WHERE id=1", (_json(expected_legacy),))
                    db.execute("INSERT INTO opt_eod_migrations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               (account["id"], body["request_id"], old_raw.decode(), _digest(old_raw), _digest(forward_raw),
                                _json(legacy_feed), _json(expected_legacy), _json(baseline), initial_raw.decode(), _digest(initial_raw), clock.isoformat()))
                    db.execute("UPDATE opt_accounts SET state=? WHERE id=?", (initial_raw.decode(), account["id"]))
                    db.execute("INSERT INTO opt_eod_state VALUES(?,?,?)", (account["id"], body["request_id"], _digest(initial_raw)))
                    active = {**feed, "initialized": True, "enabled": True, "revision": feed["revision"]+1, "activation_at": clock.isoformat()}
                    db.execute("UPDATE opt_eod_meta SET config=? WHERE id=1", (_json(active),))
                owner._publish(cfg, brief, receipt,
                               (("previous-state.json", old_raw), ("previous-forward.json", forward_raw),
                                ("previous-feed.json", _raw(legacy_feed)), ("baseline-source.json", baseline_raw),
                                ("baseline.json", _raw(baseline)), ("baseline-manifest.json", manifest_raw), ("migration.json", _raw(report))),
                               final=final, extra_check=checkpoint)
                return receipt
            except Exception as error:
                owner._failed(receipt, error)
                raise

    def configure(self, body, now=None):
        owner = self.owner
        owner._arguments(body, {"enabled", "acknowledge_source"})
        if type(body["enabled"]) is not bool or body["acknowledge_source"] is not True:
            raise ValueError("Choose an explicit source setting and acknowledge date-only partial research.")
        clock, receipt = _now(now), None
        with owner._ownership():
            try:
                with owner._committing(owner.controls()) as db:
                    cfg, brief = owner._policy(db)
                    replay = owner._reserve(db, "eod_config", body, None, clock)
                    if replay:
                        return replay
                    feed = self.config(db)
                    if not feed["initialized"]:
                        raise HandoffConflict("The EOD source has not been migrated onto the shared account.")
                    account = owner._account(db)
                    if not account or account["state"].get("schema") not in (3,4) or account["policy"] != owner._epoch(db, cfg, brief):
                        raise HandoffConflict("The source lost its shared-account authority.")
                    feed.update(enabled=body["enabled"], revision=feed["revision"]+1)
                    db.execute("UPDATE opt_eod_meta SET config=? WHERE id=1", (_json(feed),))
                    completed = {"request_id": body["request_id"], "action": "eod_config", "status": "completed", "account_id": account["id"],
                               "source": feed, "simulated_funding_added": "0", "monthly_budget_reset": False, "artifacts": [], "model_calls": 0, "replayed": False}
                    owner._finish(db, completed)
                return completed
            except Exception as error:
                owner._failed(receipt, error)
                raise

    def collect(self, body, now=None, *, collector=None):
        owner = self.owner
        owner._arguments(body)
        clock, receipt = _now(now), None
        ready, session, _ = self._window(clock)
        if not ready:
            raise HandoffConflict("Wait for the configured09:35Eastern prior-EOD collection window; no request made.")
        from .options_eod import advance_paper, RULE_SHA256
        from .options_eod_data import normalize_bundle, canonical
        with owner._ownership(), ExitStack() as held:
            try:
                with owner.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    cfg, feed, internet, brief = self._permissions(db)
                    replay = owner._reserve(db, "eod_collect", body, "eod-feed-"+session.isoformat(), clock)
                    if replay:
                        return replay
                    receipt = {"request_id": body["request_id"], "action": "eod_collect", "status": "running", "created_at": clock.isoformat(),
                               "day": "eod-feed-"+session.isoformat(), "artifacts": [], "rule_sha256": RULE_SHA256,
                               "model_calls": 0, "replayed": False}
                # The reservation is durable before inspecting retained source
                # evidence. An integrity failure can therefore be recorded and
                # stop recurrence without ever launching the network worker.
                with owner.database() as db:
                    account = owner._account(db)
                    self._account_authority(db, account, cfg, brief)
                    receipt["account_id"] = account["id"]
                    if account["state"]["hold"]:
                        raise HandoffConflict("The shared EOD portfolio is held; a new source cannot heal it.")
                    required = sorted({item["contract_id"] for item in (account["state"]["position"], account["state"]["pending"]) if item})
                    self._audit_prefix(db, account["id"], account["state"], full=True)
                    stress_mode = account["state"].get("schema")==4
                    runtime_check = held.enter_context(self.stress._runtime_lease()) if stress_mode else (lambda:None)
                    contexts = self.stress.contexts(db,account["id"]) if stress_mode else ()
                    contexts_sha = _digest(_raw(contexts))
                def checkpoint():
                    runtime_check()
                    with owner.database() as db:
                        current = self._policy(db)
                        if current != (cfg, feed, internet, brief) or owner._epoch(db, current[0], current[3]) != account["policy"]:
                            raise HandoffConflict("EOD source/Internet/brief permissions changed or were revoked.")
                bundle = collector(check=checkpoint, now=clock, expected_session=session, required_contracts=required) if collector else self._collect(cfg, feed, internet, brief, body["request_id"], required)
                checkpoint()
                bundle_raw = canonical(bundle)
                if len(bundle_raw) > MAX_CAPTURE:
                    raise ValueError("The raw EOD capture exceeds its bound.")
                try:
                    normalized = normalize_bundle(bundle, expected_session=session, required_contracts=required)
                except (ValueError, TypeError, KeyError) as error:
                    receipt.update(bundle_sha256=_digest(bundle_raw), source_failed=True,
                                   dataset_adopted=False, fills_adopted=0)
                    failure = {"reason": str(error)[:1000], "bundle_sha256": _digest(bundle_raw),
                               "dataset_adopted": False, "fills_adopted": 0}
                    def reject(db):
                        raise ValueError("The EOD source failed validation; rejected raw evidence is retained and no quotes or fills were adopted.")
                    owner._publish(cfg, brief, receipt, (("bundle.json", bundle_raw), ("failure.json", _raw(failure))),
                                   final=reject, extra_check=checkpoint)
                    raise AssertionError("A rejected source cannot complete publication.")
                decision_clock = clock if now is not None else _now()
                snapshot = normalized["snapshot"]
                if _now(datetime.fromisoformat(snapshot["received_at"].replace("Z", "+00:00"))) > decision_clock:
                    raise ValueError("The actual EOD receipt is later than its decision clock.")
                if stress_mode:
                    from .options_stress_eod import advance_paper as stress_advance, RULE_SHA256 as stress_rule
                    advancement = stress_advance(snapshot,account["state"],decided_at=decision_clock.isoformat(),contexts=contexts)
                    receipt.update(paper_schema=4,stress_rule_sha256=stress_rule,context_ring_sha256=contexts_sha)
                else:
                    advancement = advance_paper(snapshot, account["state"], decided_at=decision_clock.isoformat())
                creates_entry = any(event.get("action")=="BUY" and event.get("type") in ("decision","fill")
                                    for event in advancement["events"])
                snapshot_raw, provenance_raw, paper_raw = canonical(snapshot), canonical(normalized["provenance"]), _raw(advancement)
                state_raw = _raw(advancement["state"])
                adopted = advancement["state"]["processed_count"] == account["state"]["processed_count"]+1
                if advancement["state"]["processed_count"] not in (account["state"]["processed_count"], account["state"]["processed_count"]+1):
                    raise ValueError("Only one actual new EOD source session may advance the shared account.")
                receipt.update(source_date=snapshot["source_date"], received_at=snapshot["received_at"], quote_observed_at=None,
                               bundle_sha256=_digest(bundle_raw), snapshot_sha256=_digest(snapshot_raw), provenance_sha256=_digest(provenance_raw),
                               state_sha256=_digest(state_raw), paper_status=advancement["status"], paper_events=len(advancement["events"]),
                               accepted_snapshot=adopted, research_only=True, execution_qualified=False, partial_chain=snapshot["partial_chain"],
                               required_contracts=required, recorded_at=decision_clock.isoformat())
                with owner.database() as db:
                    checkpoint()
                    self._audit_prefix(db, account["id"], account["state"], full=True)
                def final(db):
                    checkpoint()
                    current = owner._account(db)
                    if not current or _basis(current) != _basis(account):
                        raise HandoffConflict("The shared portfolio changed before EOD state publication.")
                    if stress_mode and creates_entry and _digest(_raw(self.stress.contexts(db,account["id"])))!=contexts_sha:
                        raise HandoffConflict("Retained stress context changed before publication; no state was adopted.")
                    self._audit_prefix(db, account["id"], account["state"], full=True)
                    self.verify(receipt)
                    if db.execute("SELECT count(*) FROM opt_events").fetchone()[0]+len(advancement["events"]) > MAX_EVENTS:
                        raise ValueError("Options event history is full; no deletion or extra quota.")
                    if adopted:
                        total = db.execute("SELECT coalesce(sum(bytes),0) FROM opt_eod_data").fetchone()[0]
                        if total+len(snapshot_raw) > MAX_DATA:
                            raise ValueError("The retained EOD source prefix is full; reviewed maintenance is required.")
                        db.execute("INSERT INTO opt_eod_data VALUES(?,?,?,?,?)", (snapshot["source_date"], _digest(snapshot_raw), snapshot_raw, len(snapshot_raw), body["request_id"]))
                    db.execute("UPDATE opt_accounts SET state=? WHERE id=?", (state_raw.decode(), account["id"]))
                    db.execute("UPDATE opt_eod_state SET request_id=?,state_sha256=? WHERE account_id=?", (body["request_id"], _digest(state_raw), account["id"]))
                    for event in advancement["events"]:
                        db.execute("INSERT INTO opt_events(account_id,run_id,value) VALUES(?,?,?)", (account["id"], body["request_id"], _json(event)))
                owner._publish(cfg, brief, receipt, (("bundle.json", bundle_raw), ("snapshot.json", snapshot_raw), ("provenance.json", provenance_raw), ("paper.json", paper_raw)), final=final, extra_check=checkpoint)
                return receipt
            except Exception as error:
                owner._failed(receipt, error)
                raise

    def run_due(self, now=None):
        owner, clock = self.owner, _now(now)
        ready, session, _ = self._window(clock)
        if not ready:
            return None
        with owner.database() as db:
            try:
                cfg, feed, _, brief = self._permissions(db)
            except HandoffConflict:
                return None
            if clock < datetime.fromisoformat(feed["activation_at"]):
                return None
            if db.execute("SELECT 1 FROM opt_runs WHERE day=? OR status IN ('running','failed') LIMIT 1", ("eod-feed-"+session.isoformat(),)).fetchone():
                return None
            try:
                account = owner._account(db)
                self._account_authority(db, account, cfg, brief)
            except HandoffConflict:
                return None
            except ValueError:
                # A new guarded operation records this known integrity failure
                # before any worker. Its failed receipt stops future recurrence.
                account = None
            if account is not None:
                if account["state"]["hold"]:
                    return None
                history = account["state"]["history"]
                if history and session.isoformat() <= history[-1]["source_date"]:
                    return None
            revision = owner._meta(db)[0]
        try:
            return self.collect({"request_id": "options-eod-"+session.isoformat(), "expected_revision": revision}, now)
        except HandoffConflict:
            return None

    def _collect(self, cfg, feed, internet, brief, request_id, required):
        from .options_eod_worker import run_collector
        return run_collector(self, cfg, feed, internet, brief, request_id, required)
