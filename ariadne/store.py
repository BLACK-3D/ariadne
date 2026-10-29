"""Durable state. Each operation owns a short SQLite transaction."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
import hashlib
from contextlib import contextmanager
from pathlib import Path
from .config import _allowed_path


def ident() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


def _isolated_outputs(prompt, checks, enabled):
    if type(enabled) is not bool:
        raise ValueError("isolated_outputs must be a boolean")
    if enabled:
        if "{{run_dir}}" not in prompt or not isinstance(checks, list) or not 1 <= len(checks) <= 10:
            raise ValueError("Isolated workflows require {{run_dir}} in their instructions and 1–10 output checks.")
        for check in checks:
            path = check.get("path") if isinstance(check, dict) else None
            if not isinstance(path, str) or not path.startswith("{{run_dir}}/"):
                raise ValueError("Every isolated output check must start with {{run_dir}}/.")
            tail = path[len("{{run_dir}}/"):].replace("\\", "/")
            if any(part in {"", ".", ".."} for part in tail.split("/")) or "{{run_dir}}" in tail or ":" in tail or "\x00" in tail:
                raise ValueError("Isolated output paths require ordinary child names without traversal or repeated placeholders.")
    return enabled


class Store:
    def __init__(self, path: Path):
        path = _allowed_path(path)
        for suffix in ("", "-wal", "-shm"):
            candidate = _allowed_path(str(path) + suffix)
            if candidate.exists() and candidate.stat().st_nlink > 1:
                raise ValueError("Database files must not be hard links")
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, title TEXT NOT NULL,
                  prompt TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
                  updated_at REAL NOT NULL, error TEXT, step_count INTEGER DEFAULT 0,
                  context TEXT NOT NULL, pending TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
                  data TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
                CREATE TABLE IF NOT EXISTS approvals (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, call_key TEXT NOT NULL,
                  tool TEXT NOT NULL, args TEXT NOT NULL, status TEXT NOT NULL,
                  created_at REAL NOT NULL, UNIQUE(run_id,call_key));
                CREATE TABLE IF NOT EXISTS memories (
                  id TEXT PRIMARY KEY, content TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED,content);
                CREATE TABLE IF NOT EXISTS documents (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks (
                  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(id UNINDEXED,title,content);
                CREATE TABLE IF NOT EXISTS schedules (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL,
                  interval_minutes INTEGER NOT NULL, enabled INTEGER NOT NULL,
                  next_run REAL NOT NULL, last_run_id TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL, checks TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS document_versions (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, sha256 TEXT NOT NULL, version INTEGER NOT NULL, created_at REAL NOT NULL);
            """)
            additions = {
                "runs": {"checks": "TEXT NOT NULL DEFAULT '[]'", "verification": "TEXT NOT NULL DEFAULT '{}'", "verification_status": "TEXT NOT NULL DEFAULT 'not_requested'", "verification_attempts": "INTEGER NOT NULL DEFAULT 0", "workflow_id": "TEXT", "agent_id": "TEXT NOT NULL DEFAULT 'general'", "isolated_outputs": "INTEGER NOT NULL DEFAULT 0", "output_prefix": "TEXT"},
                "approvals": {"capability_fingerprint": "TEXT NOT NULL DEFAULT ''", "capability": "TEXT NOT NULL DEFAULT '{}'"},
                "schedules": {"last_error": "TEXT", "workflow_id": "TEXT", "checks": "TEXT NOT NULL DEFAULT '[]'", "agent_id": "TEXT NOT NULL DEFAULT 'general'", "isolated_outputs": "INTEGER NOT NULL DEFAULT 0"},
                "workflows": {"agent_id": "TEXT NOT NULL DEFAULT 'general'", "isolated_outputs": "INTEGER NOT NULL DEFAULT 0"},
                "document_versions": {"processor": "TEXT NOT NULL DEFAULT '{}'", "content_sha256": "TEXT"},
                "documents": {"source_id": "TEXT", "source_name": "TEXT", "source_path": "TEXT", "sha256": "TEXT", "format": "TEXT", "byte_size": "INTEGER", "version": "INTEGER NOT NULL DEFAULT 1", "updated_at": "REAL", "warnings": "TEXT NOT NULL DEFAULT '[]'", "processor": "TEXT NOT NULL DEFAULT '{}'", "content_sha256": "TEXT"},
            }
            for table, columns in additions.items():
                existing = {r["name"] for r in db.execute("PRAGMA table_info(" + table + ")")}
                for name, definition in columns.items():
                    if name not in existing:
                        db.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + definition)
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS document_origin ON documents(source_id, source_path) WHERE source_id IS NOT NULL")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def recover(self):
        # A crash may have happened after an external side effect. Never replay it.
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted', error=?, updated_at=? WHERE status='running'",
                       ("Server stopped during execution. Inspect the activity trail before starting a new mission.", now()))
            db.execute("UPDATE approvals SET status='uncertain' WHERE status='executing'")
            db.execute("UPDATE schedules SET enabled=0,last_error='The last mission stopped or was interrupted. Review before re-enabling.' WHERE last_run_id IN (SELECT id FROM runs WHERE status IN ('failed','interrupted','exhausted','cancelled') OR verification_status='failed')")

    @staticmethod
    def decode(row):
        if row is None:
            return None
        out = dict(row)
        for key in ("context", "pending", "args", "data", "checks", "verification", "warnings", "capability", "processor"):
            if key in out:
                out[key] = json.loads(out[key])
        if "isolated_outputs" in out:
            out["isolated_outputs"] = bool(out["isolated_outputs"])
        return out

    def new_run(self, prompt, session_id=None, checks=None, workflow_id=None, agent_id=None, isolated_outputs=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run_id = self._insert_run(db, prompt, session_id, checks, workflow_id, agent_id, isolated_outputs)
        return self.run(run_id)

    def _insert_run(self, db, prompt, session_id=None, checks=None, workflow_id=None, agent_id=None, isolated_outputs=False):
        from .agents import resolve_agent
        agent_id = resolve_agent(prompt, agent_id)
        run_id, session_id = ident(), session_id or ident()
        _isolated_outputs(prompt, checks, isolated_outputs)
        output_prefix = "missions/" + run_id if isolated_outputs else None
        if isolated_outputs:
            prompt = prompt.replace("{{run_dir}}", output_prefix)
            checks = [{**check, "path": check["path"].replace("{{run_dir}}", output_prefix)} for check in checks]
        context = []
        rows = db.execute("SELECT prompt,context FROM runs WHERE session_id=? AND status='completed' ORDER BY created_at DESC LIMIT 4", (session_id,)).fetchall()
        for row in reversed(rows):
            previous = json.loads(row["context"])
            answer = next((m.get("content", "") for m in reversed(previous) if m["role"] == "assistant" and not m.get("tool_calls")), "")
            context.extend([{"role": "user", "content": row["prompt"][:4000]}, {"role": "assistant", "content": answer[:6000]}])
        while context and sum(len(m["content"]) for m in context) > 12000:
            context = context[2:]
        context.append({"role": "user", "content": prompt})
        stamp = now()
        if db.execute("SELECT count(*) FROM runs WHERE status IN ('queued','running','waiting_approval')").fetchone()[0] >= 32:
            raise ValueError("Mission queue is full (32). Cancel or complete existing missions first.")
        db.execute("INSERT INTO runs(id,session_id,title,prompt,status,created_at,updated_at,context,agent_id) VALUES (?,?,?,?,?,?,?,?,?)",
                   (run_id, session_id, prompt[:90], prompt, "queued", stamp, stamp, json.dumps(context), agent_id))
        db.execute("INSERT INTO events(run_id,kind,data,created_at) VALUES (?,?,?,?)", (run_id, "queued", json.dumps({"message": "Mission queued for local execution.", "agent_id": agent_id}), stamp))
        db.execute("UPDATE runs SET checks=?,verification_status=?,workflow_id=?,isolated_outputs=?,output_prefix=? WHERE id=?", (json.dumps(checks or []), "pending" if checks else "not_requested", workflow_id, int(isolated_outputs), output_prefix, run_id))
        return run_id

    def run(self, run_id):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def runs(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,session_id,title,status,created_at,updated_at,error,step_count,verification_status,workflow_id,agent_id FROM runs WHERE status IN ('queued','running','waiting_approval') OR id IN (SELECT id FROM runs ORDER BY created_at DESC LIMIT 100) ORDER BY created_at DESC")]

    def update_run(self, run_id, **fields):
        allowed = {"status", "error", "step_count", "context", "pending", "verification", "verification_status", "verification_attempts"}
        if not fields.keys() <= allowed:
            raise ValueError("Invalid update")
        fields["updated_at"] = now()
        for key in ("context", "pending", "verification"):
            if key in fields:
                fields[key] = json.dumps(fields[key])
        with self.connect() as db:
            result = db.execute("UPDATE runs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=? AND status NOT IN ('cancelled','completed','failed','interrupted','exhausted')", (*fields.values(), run_id))
            if result.rowcount and (fields.get("status") in {"failed", "interrupted", "exhausted", "cancelled"} or fields.get("status") == "completed" and fields.get("verification_status") == "failed"):
                db.execute("UPDATE schedules SET enabled=0,last_error=? WHERE last_run_id=?", (fields.get("error") or "The last mission stopped or failed its expected output checks. Review it before re-enabling this schedule.", run_id))

    def event(self, run_id, kind, data):
        encoded = json.dumps(data, ensure_ascii=False)
        if len(encoded) > 16000:
            data = {"tool": data.get("tool"), "preview": encoded[:14000], "truncated": True}
        with self.connect() as db:
            db.execute("INSERT INTO events(run_id,kind,data,created_at) VALUES (?,?,?,?)", (run_id, kind, json.dumps(data), now()))

    def details(self, run_id):
        run = self.run(run_id)
        if not run:
            return None
        with self.connect() as db:
            events = [self.decode(r) for r in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))]
            approvals = [self.decode(r) for r in db.execute("SELECT * FROM approvals WHERE run_id=? ORDER BY created_at", (run_id,))]
        verification = run.get("verification") or {"status": run["verification_status"], "checks": [], "artifacts": []}
        return {"run": run, "events": events, "approvals": approvals, "messages": run["context"], "verification": verification, "artifacts": verification.get("artifacts", [])}

    def next_run(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE runs SET status='running',updated_at=? WHERE id=?", (now(), row["id"]))
            return self.decode(row)

    def approval(self, run_id, key, name, args, fingerprint="", capability=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM approvals WHERE run_id=? AND call_key=?", (run_id, key)).fetchone()
            if old and (old["capability_fingerprint"] != fingerprint or old["tool"] != name or json.loads(old["args"]) != args):
                db.execute("UPDATE approvals SET status='superseded',call_key=? WHERE id=?", (key + "#superseded#" + old["id"], old["id"]))
            db.execute("INSERT OR IGNORE INTO approvals(id,run_id,call_key,tool,args,status,created_at,capability_fingerprint,capability) VALUES (?,?,?,?,?,?,?,?,?)", (ident(), run_id, key, name, json.dumps(args), "pending", now(), fingerprint, json.dumps(capability or {})))
            return self.decode(db.execute("SELECT * FROM approvals WHERE run_id=? AND call_key=?", (run_id, key)).fetchone())

    def existing_approval(self, run_id, key):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM approvals WHERE run_id=? AND call_key=?", (run_id, key)).fetchone())

    def approval_status(self, approval_id, status):
        with self.connect() as db:
            db.execute("UPDATE approvals SET status=? WHERE id=?", (status, approval_id))

    def decide(self, approval_id, decision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if not row:
                raise KeyError("Approval not found")
            if row["status"] != "pending":
                raise ValueError("This approval has already been resolved")
            run = db.execute("SELECT status FROM runs WHERE id=?", (row["run_id"],)).fetchone()
            if run[0] != "waiting_approval":
                raise ValueError("Mission is no longer waiting for approval")
            db.execute("UPDATE approvals SET status=? WHERE id=?", (decision, approval_id))
            db.execute("UPDATE runs SET status='queued',updated_at=? WHERE id=?", (now(), row["run_id"]))
            if decision == "rejected":
                db.execute("UPDATE schedules SET enabled=0,last_error='An action in the last mission was rejected. Review before re-enabling.' WHERE last_run_id=?", (row["run_id"],))

    def cancel(self, run_id):
        with self.connect() as db:
            result = db.execute("UPDATE runs SET status='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running','waiting_approval')", (now(), run_id))
            db.execute("UPDATE approvals SET status='cancelled' WHERE run_id=? AND status='pending'", (run_id,))
            db.execute("UPDATE schedules SET enabled=0,last_error='Last scheduled mission was cancelled. Review before re-enabling.' WHERE last_run_id=?", (run_id,))
        return bool(result.rowcount)

    def memories(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM memories ORDER BY created_at DESC LIMIT 500")]

    def memory_add(self, content):
        memory_id = ident()
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM memories").fetchone()[0] >= 5000:
                raise ValueError("Memory limit reached (5000). Remove older entries first.")
            db.execute("INSERT INTO memories VALUES (?,?,?)", (memory_id, content, now()))
            db.execute("INSERT INTO memory_fts VALUES (?,?)", (memory_id, content))
        return {"id": memory_id, "content": content}

    def memory_delete(self, memory_id):
        with self.connect() as db:
            db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            db.execute("DELETE FROM memory_fts WHERE id=?", (memory_id,))

    @staticmethod
    def query(text):
        words = re.findall(r"[^\W_]+", text, flags=re.UNICODE)[:24]
        return " OR ".join('"' + w + '"' for w in words)

    def search(self, text, knowledge=False):
        query = self.query(text)
        if not query:
            return []
        with self.connect() as db:
            if knowledge:
                rows = db.execute("SELECT c.document_id,c.title,c.content,d.sha256,d.source_name,d.source_path,d.version,d.warnings,d.processor,d.content_sha256 FROM knowledge_fts f JOIN chunks c ON c.id=f.id JOIN documents d ON d.id=c.document_id WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT 6", (query,)).fetchall()
            else:
                rows = db.execute("SELECT id,content FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT 6", (query,)).fetchall()
        return [self.decode(r) for r in rows]

    def documents(self):
        with self.connect() as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM documents ORDER BY created_at DESC LIMIT 500")]

    def document_add(self, title, content):
        return self.document_upsert(title, content, {})["document"]

    def document_upsert(self, title, content, metadata):
        doc_id, stamp = ident(), now()
        content_digest = hashlib.sha256(content.encode()).hexdigest()
        digest = metadata.get("sha256") or content_digest
        processor = json.dumps(metadata.get("processor") or {}, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM documents WHERE source_id=? AND source_path=?", (metadata.get("source_id"), metadata.get("source_path"))).fetchone() if metadata.get("source_id") else None
            if existing and existing["sha256"] == digest and existing["processor"] == processor and existing["content_sha256"] == content_digest:
                return {"document": self.decode(existing), "unchanged": True}
            if db.execute("SELECT count(*) FROM documents").fetchone()[0] >= 1000:
                if not existing:
                    raise ValueError("Document limit reached (1000).")
            version = existing["version"] + 1 if existing else 1
            if existing:
                doc_id = existing["id"]
                db.execute("DELETE FROM knowledge_fts WHERE id IN (SELECT id FROM chunks WHERE document_id=?)", (doc_id,))
                db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
                db.execute("UPDATE documents SET title=?,sha256=?,format=?,byte_size=?,version=?,updated_at=?,source_name=?,warnings=? WHERE id=?", (title, digest, metadata.get("format", "text"), metadata.get("byte_size", len(content.encode())), version, stamp, metadata.get("source_name"), json.dumps(metadata.get("warnings", [])), doc_id))
            else:
                db.execute("INSERT INTO documents(id,title,created_at,source_id,source_name,source_path,sha256,format,byte_size,version,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (doc_id, title, stamp, metadata.get("source_id"), metadata.get("source_name"), metadata.get("source_path"), digest, metadata.get("format", "text"), metadata.get("byte_size", len(content.encode())), version, stamp))
                db.execute("UPDATE documents SET warnings=? WHERE id=?", (json.dumps(metadata.get("warnings", [])), doc_id))
            db.execute("UPDATE documents SET processor=?,content_sha256=? WHERE id=?", (processor, content_digest, doc_id))
            for offset in range(0, len(content), 1200):
                chunk_id, chunk = ident(), content[offset:offset + 1400]
                db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (chunk_id, doc_id, title, chunk))
                db.execute("INSERT INTO knowledge_fts VALUES (?,?,?)", (chunk_id, title, chunk))
            db.execute("INSERT INTO document_versions(id,document_id,sha256,version,created_at,processor,content_sha256) VALUES (?,?,?,?,?,?,?)", (ident(), doc_id, digest, version, stamp, processor, content_digest))
            db.execute("DELETE FROM document_versions WHERE document_id=? AND version<?", (doc_id, version - 99))
            document = self.decode(db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())
        return {"document": document, "unchanged": False}

    def document_delete(self, doc_id):
        with self.connect() as db:
            db.execute("DELETE FROM knowledge_fts WHERE id IN (SELECT id FROM chunks WHERE document_id=?)", (doc_id,))
            db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
            db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            db.execute("DELETE FROM document_versions WHERE document_id=?", (doc_id,))

    def sources(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sources ORDER BY name")]

    def source(self, source_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            return dict(row) if row else None

    def source_add(self, name, path):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM sources WHERE path=?", (path,)).fetchone()
            if old:
                return dict(old)
            if db.execute("SELECT count(*) FROM sources").fetchone()[0] >= 16:
                raise ValueError("At most 16 reference folders may be configured")
            source_id = ident()
            db.execute("INSERT INTO sources VALUES (?,?,?,?)", (source_id, name, path, now()))
            return dict(db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())

    def source_delete(self, source_id):
        with self.connect() as db:
            db.execute("DELETE FROM sources WHERE id=?", (source_id,))

    def workflows(self):
        with self.connect() as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM workflows ORDER BY created_at")]

    def workflow(self, workflow_id):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone())

    def workflow_save(self, name, prompt, checks, workflow_id=None, agent_id=None, isolated_outputs=None):
        from .agents import resolve_agent
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT agent_id,isolated_outputs FROM workflows WHERE id=?", (workflow_id,)).fetchone() if workflow_id else None
            if workflow_id and not existing:
                raise KeyError("Workflow not found")
            if not workflow_id and db.execute("SELECT count(*) FROM workflows").fetchone()[0] >= 100:
                raise ValueError("At most 100 workflows may be saved")
            workflow_id = workflow_id or ident()
            selected = existing["agent_id"] if agent_id is None and existing else "general" if agent_id is None else agent_id
            if selected != "auto":
                selected = resolve_agent(prompt, selected)
            isolation = bool(existing["isolated_outputs"]) if isolated_outputs is None and existing else False if isolated_outputs is None else isolated_outputs
            _isolated_outputs(prompt, checks, isolation)
            db.execute("INSERT INTO workflows(id,name,prompt,checks,created_at,agent_id,isolated_outputs) VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,prompt=excluded.prompt,checks=excluded.checks,agent_id=excluded.agent_id,isolated_outputs=excluded.isolated_outputs", (workflow_id, name, prompt, json.dumps(checks), now(), selected, int(isolation)))
            return self.decode(db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone())

    def workflow_delete(self, workflow_id):
        with self.connect() as db:
            db.execute("DELETE FROM workflows WHERE id=?", (workflow_id,))

    def workflow_run(self, workflow_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            workflow = self.decode(db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone())
            if not workflow:
                raise KeyError("Workflow not found")
            run_id = self._insert_run(db, workflow["prompt"], checks=workflow["checks"], workflow_id=workflow_id, agent_id=workflow["agent_id"], isolated_outputs=workflow["isolated_outputs"])
        return self.run(run_id)

    def schedules(self):
        with self.connect() as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM schedules ORDER BY next_run")]

    def schedule_add(self, name, prompt, interval, workflow_id=None, agent_id=None, checks=None, isolated_outputs=False):
        from .agents import resolve_agent
        if type(interval) is not int or not 5 <= interval <= 525600:
            raise ValueError("interval_minutes must be an integer between 5 and 525600")
        schedule_id = ident()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM schedules").fetchone()[0] >= 100:
                raise ValueError("Schedule limit reached (100).")
            if workflow_id is not None:
                if not isinstance(workflow_id, str):
                    raise ValueError("workflow_id must name a saved workflow")
                workflow = self.decode(db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone())
                if not workflow:
                    raise KeyError("Workflow not found")
                prompt, checks, agent_id = workflow["prompt"], workflow["checks"], workflow["agent_id"]
                isolated_outputs = workflow["isolated_outputs"]
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
                raise ValueError("A schedule requires mission instructions of at most 16000 characters")
            # Resolve automatic routing once into this immutable schedule snapshot.
            selected = resolve_agent(prompt, agent_id)
            _isolated_outputs(prompt, checks, isolated_outputs)
            db.execute("INSERT INTO schedules(id,name,prompt,interval_minutes,enabled,next_run,last_run_id,workflow_id,checks,agent_id,isolated_outputs) VALUES (?,?,?,?,?,?,NULL,?,?,?,?)", (schedule_id, name, prompt, interval, 1, now() + interval * 60, workflow_id, json.dumps(checks or []), selected, int(isolated_outputs)))
        return {"id": schedule_id}

    def schedule_update(self, schedule_id, enabled):
        with self.connect() as db:
            if enabled:
                db.execute("UPDATE schedules SET last_run_id=NULL WHERE id=? AND last_run_id IN (SELECT id FROM runs WHERE status IN ('failed','interrupted','exhausted','cancelled') OR verification_status='failed')", (schedule_id,))
            db.execute("UPDATE schedules SET enabled=?,next_run=? + interval_minutes*60,last_error=NULL WHERE id=?", (int(enabled), now(), schedule_id))

    def schedule_delete(self, schedule_id):
        with self.connect() as db:
            db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))

    def due_schedules(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            schedules = db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run", (now(),)).fetchall()
            for schedule in schedules:
                last = db.execute("SELECT status,verification_status FROM runs WHERE id=?", (schedule["last_run_id"],)).fetchone() if schedule["last_run_id"] else None
                if last and last["status"] in ("running", "queued", "waiting_approval"):
                    continue
                if last and (last["status"] in {"failed", "interrupted", "exhausted", "cancelled"} or last["verification_status"] == "failed"):
                    db.execute("UPDATE schedules SET enabled=0,last_error='Previous mission stopped or failed its expected output checks. Review and explicitly re-enable.' WHERE id=?", (schedule["id"],))
                    continue
                try:
                    run_id = self._insert_run(db, schedule["prompt"], checks=json.loads(schedule["checks"]), workflow_id=schedule["workflow_id"], agent_id=schedule["agent_id"], isolated_outputs=bool(schedule["isolated_outputs"]))
                except ValueError:
                    break
                db.execute("UPDATE schedules SET next_run=?,last_run_id=? WHERE id=?", (now() + schedule["interval_minutes"] * 60, run_id, schedule["id"]))
                db.execute("INSERT INTO events(run_id,kind,data,created_at) VALUES (?,?,?,?)", (run_id, "schedule", json.dumps({"name": schedule["name"]}), now()))
