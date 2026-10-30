"""Offline VIX research. This extension never mutates or funds a paper account.

Index histories are signal diagnostics, not option prices. Actual option
histories can be backtested separately; neither path grants forward execution.
"""
from __future__ import annotations

from datetime import datetime
import copy
from pathlib import Path

from .handoff import HandoffConflict, _load
from .options_workflow import _json, _now, _digest

MAX_HISTORY = 2 * 1024 * 1024
MAX_INPUT = 32 * 1024 * 1024
MAX_REPORT = 8 * 1024 * 1024
RESOURCES = Path(__file__).resolve().parent / "resources" / "vix"


def _resource(name):
    raw = (RESOURCES / name).read_bytes()
    if len(raw) > 128 * 1024:
        raise ValueError("VIX product metadata exceeds its bound.")
    return _load(raw)


class VixResearchWorkflow:
    def __init__(self, owner):
        self.owner = owner

    def snapshot(self, db, now, account):
        saved = {}
        for kind in ("vix_signals", "vix_backtest"):
            row = db.execute("SELECT opt_reports.receipt FROM opt_reports JOIN opt_runs USING(request_id) WHERE opt_runs.action=? AND opt_runs.status='completed' ORDER BY opt_reports.rowid DESC LIMIT 1", (kind,)).fetchone() if self.owner._exists(db) else None
            receipt = _load(row[0]) if row else None
            if receipt:
                self.verify(receipt)
            saved[kind] = receipt
        summary = (account or {}).get("summary") or {}
        source = _resource("source-assessment.json")
        signals = saved["vix_signals"]
        source_date = (signals or {}).get("summary", {}).get("source_date")
        from .qqq_data import last_closed_session
        expected = last_closed_session(now).isoformat()
        freshness = {"source_date": source_date, "expected_regular_session": expected,
                     "stale": source_date != expected,
                     "scope": "Date-only saved index history; provider publication times and executable quotes are unknown."}
        return {
            "schema": 1, "symbol": "VIX", "roots": ["VIX", "VIXW"],
            "contract": "European / AM VRO special opening quotation / cash / multiplier 100",
            "paper_status": "waiting_for_qualified_quotes",
            "paper_fills_enabled": False, "paper_trades": 0, "funding_added": "0",
            "shared_account_id": (account or {}).get("id"),
            "shared_monthly_limit": "500.00", "shared_monthly_remaining": summary.get("monthly_budget_remaining"),
            "shared_cap_qualified": summary.get("cap_qualified") is True,
            "readiness": [
                "No permitted free no-account VIX/VIXW option quote feed has been qualified.",
                "Actual option bid/ask, side sizes, exact-expiry VX reference and official contract calendar are required.",
                "VIX forward execution is disabled; a future integrated shared-ledger version requires separate qualification. No second account or allowance exists.",
                "Official VIX/VIX9D index history supports retrospective signal diagnostics only, never option P/L or paper fills."],
            "source_assessment": source,
            "signals": saved["vix_signals"], "backtest": saved["vix_backtest"],
            "index_freshness": freshness,
            "model_calls": 0, "brokerage_connected": False,
            "scope": "Offline research/import support; no active VIX paper trading, orders, synthetic market evidence or profitability assurance.",
        }

    def verify(self, receipt):
        if receipt["action"] == "vix_signals":
            from .vix_signals import RULE_SHA256
        else:
            from .vix_options import RULE_SHA256
        if receipt.get("rule_sha256") != RULE_SHA256:
            raise ValueError("VIX report belongs to another registered rule.")
        base = "reports/options-recovery/" + receipt["request_id"] + "/"
        expected = {base + "vix-report.json", base + "vix-input.json"}
        if receipt["action"] == "vix_signals":
            expected |= {base + name for name in receipt.get("history_files", [])}
            if not receipt.get("history_files") or not set(receipt["history_files"]) <= {"VIX.csv", "VIX9D.csv"}:
                raise ValueError("VIX signal history artifact contract changed.")
        if len(receipt.get("artifacts", [])) != len(expected) or {item.get("path") for item in receipt.get("artifacts", [])} != expected:
            raise ValueError("VIX artifact contract is incomplete.")
        report = None
        for item in receipt["artifacts"]:
            raw = self.owner._read(item["path"], MAX_INPUT)
            if len(raw) != item["bytes"] or _digest(raw) != item["sha256"]:
                raise ValueError("VIX evidence is missing or changed; no regeneration is permitted.")
            if item["path"].endswith("vix-input.json") and _digest(raw) != receipt["input_sha256"]:
                raise ValueError("VIX accepted input identity changed.")
            if item["path"].endswith("vix-report.json"):
                report = _load(raw)
        if not report or report.get("accepted_input_sha256") != receipt["input_sha256"] or report.get("rule_sha256") != receipt["rule_sha256"] or report.get("execution_qualified") is not False or report.get("research_only") is not True:
            raise ValueError("VIX report lost its input/rule or research-only identity.")

    def signals(self, body, now=None):
        return self._run(body, now, kind="vix_signals")

    def backtest(self, body, now=None):
        return self._run(body, now, kind="vix_backtest")

    def _histories(self, raw, checkpoint, clock):
        from .vix_signals import load_history, study
        manifest = _load(raw)
        if type(manifest) is not dict or set(manifest) != {"schema", "kind", "files"} or type(manifest["schema"]) is not int or manifest["schema"] != 1 or manifest["kind"] != "cboe_index_history":
            raise ValueError("Use a schema-1 cboe_index_history manifest, not option quotes.")
        files = manifest["files"]
        if type(files) is not list or not 1 <= len(files) <= 2:
            raise ValueError("Supply VIX and optionally VIX9D history, each exactly once.")
        histories, artifacts = {}, []
        # Validate every path before any file is opened.
        for item in files:
            if type(item) is not dict or set(item) != {"symbol", "path", "sha256", "source_url", "retrieved_at"} or item["symbol"] not in {"VIX", "VIX9D"} or item["symbol"] in histories:
                raise ValueError("Use distinct documented VIX history manifest fields.")
            self.owner.registry._path(item["path"])
            if type(item["sha256"]) is not str or len(item["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in item["sha256"]):
                raise ValueError("Supply the exact lowercase SHA-256 of each CSV.")
            histories[item["symbol"]] = None
        if "VIX" not in histories:
            raise ValueError("VIX history is required; VIX9D alone is insufficient.")
        for item in files:
            checkpoint()
            data = self.owner._read(item["path"], MAX_HISTORY)
            if _digest(data) != item["sha256"]:
                raise ValueError("VIX history does not match its declared SHA-256.")
            if _now(datetime.fromisoformat(item["retrieved_at"])) > clock:
                raise ValueError("History retrieval cannot be in the future of this report.")
            histories[item["symbol"]] = load_history(data, symbol=item["symbol"], source_url=item["source_url"], retrieved_at=item["retrieved_at"])
            artifacts.append((item["symbol"] + ".csv", data))
        checkpoint()
        report = study(histories["VIX"], vix9d_history=histories.get("VIX9D"), as_of=clock)
        return report, artifacts

    def _run(self, body, now, *, kind):
        owner = self.owner
        owner._arguments(body, {"path"})
        owner.registry._path(body["path"])
        clock, receipt = _now(now), None
        with owner._ownership():
            try:
                with owner.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    owner._schema(db)
                    cfg, brief = owner._policy(db)
                    epoch = owner._epoch(db, cfg, brief)
                    replay = owner._reserve(db, kind, body, None, clock)
                    if replay:
                        return replay
                    receipt = {"request_id": body["request_id"], "action": kind, "status": "running", "created_at": clock.isoformat(), "artifacts": [], "model_calls": 0, "replayed": False}
                    before = owner._account(db, clock)
                def checkpoint():
                    with owner.database() as db:
                        current, current_brief = owner._policy(db, cfg)
                        if current_brief != brief or owner._epoch(db, current, current_brief) != epoch:
                            raise HandoffConflict("VIX research permissions changed or were revoked; inspect the receipt.")
                checkpoint()
                accepted = owner._read(body["path"], 64 * 1024 if kind == "vix_signals" else MAX_INPUT)
                if kind == "vix_signals":
                    report, extra = self._histories(accepted, checkpoint, clock)
                    receipt["history_files"] = [name for name, _ in extra]
                else:
                    from .vix_options import load_dataset, compare
                    dataset = load_dataset(accepted)
                    if dataset["synthetic"] is not False:
                        raise ValueError("Owner VIX research requires actual historical quotes; synthetic fixtures are not market evidence.")
                    if _now(datetime.fromisoformat(dataset["as_of"])) > clock:
                        raise ValueError("Option history cannot be as of a future report clock.")
                    checkpoint()
                    report, extra = compare(dataset), []
                checkpoint()
                report.update(accepted_input_sha256=_digest(accepted), request_id=body["request_id"], research_only=True, execution_qualified=False,
                              owner_paper_state_changed=False, active_funding_added="0", generated_at=clock.isoformat())
                if len(_json(report.get("summary", {})).encode()) > 4000 or len(_json(report.get("coverage", {})).encode()) > 4000:
                    raise ValueError("VIX compact summary/coverage exceeds its bound.")
                payload = _json(report).encode()
                if len(payload) > MAX_REPORT:
                    raise ValueError("VIX research report exceeds 8 MiB.")
                receipt.update(input_sha256=_digest(accepted), rule_sha256=report["rule_sha256"], research_only=True, execution_qualified=False,
                               paper_state_changed=False, active_funding_added="0", summary=copy.deepcopy(report.get("summary", {})),
                               coverage=copy.deepcopy(report.get("coverage", {})), research_gate=report.get("research_gate"))
                def final(db):
                    checkpoint()
                    if owner._account(db, clock) != before:
                        raise HandoffConflict("The shared paper account changed; VIX research cannot replace or fund it.")
                owner._publish(cfg, brief, receipt, (("vix-report.json", payload), ("vix-input.json", accepted), *extra), final=final, extra_check=checkpoint)
                return receipt
            except Exception as error:
                owner._failed(receipt, error)
                raise
