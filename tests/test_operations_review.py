"""Independent adversarial evidence for business/trading/local-control changes."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ariadne.business_ops import _digest
from ariadne.config import Settings
from ariadne.market_research import audit_csv, run_study, validate_metadata
from ariadne.operations import OperationsDesk
from ariadne.store import Store
from ariadne.tools import ToolRegistry


class OperationsIndependentReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix=".operations-review-", dir=Path.cwd())
        self.addCleanup(folder.cleanup)
        self.settings = Settings(data_dir=Path(folder.name), api_token="independent-fixture-key")
        self.registry = ToolRegistry(self.settings)
        self.store = Store(self.settings.data_dir / "ariadne.sqlite3")
        self.registry.local_controls.store = self.store

    def control_fixture(self):
        self.registry._connectors.update({
            "connector_review_states": {"id": "review_states", "description": "Independent fixture read", "method": "GET", "read_only": True,
                "url": "http://127.0.0.1:8123/api/states", "headers": {}, "timeout_seconds": 1},
            "connector_review_action": {"id": "review_action", "description": "Independent fixture action", "method": "POST", "read_only": False,
                "url": "http://127.0.0.1:8123/api/services/light/turn_on", "headers": {}, "timeout_seconds": 1}})
        cfg = {"schema": 1, "enabled": True, "read_connector": "connector_review_states", "entities": ["light.review"],
            "stale_seconds": 300, "actions": [{"id": "review_on", "entity_id": "light.review", "service": "light.turn_on",
                "connector": "connector_review_action", "data": {"brightness_pct": 25}}]}
        self.registry.local_controls.config_path.write_text(json.dumps(cfg), encoding="utf-8")
        return {"action": "apply", "action_id": "review_on", "request_id": "independent-action"}

    def test_forged_business_replay_with_recomputed_self_hash_is_rejected(self):
        args = {"action": "snapshot", "request_id": "independent-report"}
        original = self.registry.business_ops.execute("business_ops", args)
        path = self.registry._path(original["artifacts"][0]["path"])
        report = json.loads(path.read_text(encoding="utf-8"))
        report["result"]["item_count"] = 999
        report["result"]["indicative_inventory_sales_value_cents"] = "999999999"
        report["result_sha256"] = _digest(report["result"])
        rewritten = self.registry.execute("file_write", {"path": original["artifacts"][0]["path"], "content": json.dumps(report), "overwrite": True})
        self.assertTrue(rewritten["ok"])
        with self.assertRaises(ValueError):
            self.registry.business_ops.execute("business_ops", args)

    def test_clean_business_replay_is_byte_stable_and_does_not_mutate_ledger(self):
        args = {"action": "snapshot", "request_id": "independent-clean"}
        first = self.registry.business_ops.execute("business_ops", args)
        path = self.registry._path(first["artifacts"][0]["path"])
        before = path.read_bytes()
        second = self.registry.business_ops.execute("business_ops", args)
        self.assertTrue(second["replayed"])
        self.assertEqual(second["artifacts"], first["artifacts"])
        self.assertEqual(path.read_bytes(), before)
        with self.registry.business.database() as db:
            self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0], 0)

    def test_business_replay_rejects_forged_provenance_and_authority_even_when_result_unchanged(self):
        args = {"action": "snapshot", "request_id": "independent-provenance"}
        original = self.registry.business_ops.execute("business_ops", args)
        relative = original["artifacts"][0]["path"]
        raw = self.registry._path(relative).read_bytes()
        replacements = {"sources": [{"kind": "invented-source", "sha256": "0" * 64}],
            "authority": "Invoice was posted to an external ERP", "primary_contracts": ["https://untrusted.example/claims"]}
        for key, replacement in replacements.items():
            with self.subTest(key=key):
                forged = json.loads(raw)
                forged[key] = replacement
                self.assertTrue(self.registry.execute("file_write", {"path": relative, "content": json.dumps(forged), "overwrite": True})["ok"])
                with self.assertRaises(ValueError):
                    self.registry.business_ops.execute("business_ops", args)

    def test_private_credential_file_is_fully_validated_before_any_environment_mutation(self):
        from ariadne.connector_credentials import load_connector_credentials
        name = "ARIADNE_CONNECTOR_INDEPENDENT_REVIEW"
        self.registry._connectors["connector_review_credential"] = {"headers": {"Authorization": {"env": name}, "X-Unsafe": {"env": "PYTHONPATH"}}}
        path = self.settings.data_dir / "connector-secrets.json"
        path.write_text(json.dumps({"schema": 1, "environment": {name: "fixture header", "PYTHONPATH": "untrusted"}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(name, None)
            with self.assertRaises(ValueError):
                load_connector_credentials(self.registry)
            self.assertNotIn(name, os.environ)

    def test_direct_control_call_cannot_infer_approval_from_policy_or_proposal(self):
        args = self.control_fixture()
        with mock.patch.object(self.registry, "_request", side_effect=AssertionError("No dispatch without approval")):
            proposal = self.registry.local_controls.execute("home_control", {"action": "propose", "action_id": "review_on"})
            self.assertFalse(proposal["performed"])
            result = self.registry.execute("home_control", args)
            self.assertFalse(result["ok"])
            self.assertFalse(self.registry.local_controls.execute("home_control", {"action": "receipt", "request_id": args["request_id"]})["found"])

    def test_uncertain_control_survives_new_provider_and_cannot_dispatch_again(self):
        args = self.control_fixture()
        fingerprint = self.registry.fingerprint("home_control", args)
        with mock.patch.object(self.registry, "_request", side_effect=OSError("Uncertain simulated transport")) as request:
            first = self.registry.execute("home_control", args, approval_fingerprint=fingerprint)
            self.assertTrue(first["uncertain"])
            self.assertEqual(request.call_count, 1)
        from ariadne.local_controls import LocalControls
        provider = LocalControls(self.registry)
        provider.store = Store(self.store.path)
        with mock.patch.object(self.registry, "_request", side_effect=AssertionError("Never repeat an uncertain POST")):
            second = provider.execute("home_control", args, approval_fingerprint=fingerprint)
        self.assertTrue(second["uncertain"])
        self.assertTrue(second["replayed"])

    def test_pause_after_action_reservation_consumes_request_without_sending(self):
        args = self.control_fixture()
        fingerprint = self.registry.fingerprint("home_control", args)
        provider = self.registry.local_controls
        original = provider._dispatch_lease
        @contextmanager
        def pause_before_lease(cancelled):
            self.store.set_setting("paused", True)
            with original(cancelled):
                yield
        with mock.patch.object(provider, "_dispatch_lease", pause_before_lease), mock.patch.object(self.registry, "_request", side_effect=AssertionError("Revocation must stop POST")):
            result = self.registry.execute("home_control", args, approval_fingerprint=fingerprint)
        self.assertEqual(result["status"], "not_sent")
        self.assertFalse(result["uncertain"])
        self.store.set_setting("paused", False)
        replay = self.registry.execute("home_control", args, approval_fingerprint=fingerprint)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["status"], "not_sent")

    def test_desk_whitelist_is_stronger_than_provider_auto_policy(self):
        desk = OperationsDesk(self.registry, self.store)
        with mock.patch.object(self.registry, "policy", return_value="auto"), mock.patch.object(self.registry, "execute", side_effect=AssertionError("No forbidden dispatch")):
            for action in ("apply", "refresh", "configure"):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    desk.run({"tool": "home_control", "arguments": {"action": action}})

    def test_holdout_prices_cannot_choose_parameters_or_change_walkforward_fills(self):
        meta = {"symbol": "REVIEW", "currency": "USD", "source": "Independent synthetic fixture", "adjustment": "raw",
            "timeframe_minutes": 1, "timezone": "UTC", "session_open": "09:30", "session_close": "09:34", "weekdays": [0, 1, 2, 3, 4],
            "expected_sessions": ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"],
            "study": {"initial_cash": "1000", "shares": 1, "commission_per_order": "0", "fee_bps": "0", "spread_bps": "0", "slippage_bps": "0",
                "train_sessions": 1, "test_sessions": 1, "holdout_sessions": 1, "parameter_grid": [{"fast": 1, "slow": 2}, {"fast": 1, "slow": 3}]}}
        def make(multiplier):
            rows = ["timestamp,open,high,low,close,volume"]
            for day in range(4):
                for minute, number in enumerate((10, 12, 11, 14)):
                    price = Decimal(number) * (multiplier if day == 3 else 1)
                    rows.append(f"2026-10-{26+day}T09:{30+minute}:00+00:00,{price},{price+1},{price-1},{price},1000")
            checked = validate_metadata(meta)
            bars, audit = audit_csv(("\n".join(rows)+"\n").encode(), checked)
            return run_study(bars, audit, checked)
        first, changed = make(1), make(10)
        self.assertEqual(first["walk_forward"], changed["walk_forward"])
        self.assertEqual(first["reserved_holdout"]["parameters_selected_before_holdout"], changed["reserved_holdout"]["parameters_selected_before_holdout"])
        self.assertNotEqual(first["reserved_holdout"]["sma"], changed["reserved_holdout"]["sma"])


if __name__ == "__main__":
    unittest.main()
