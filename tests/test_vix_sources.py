"""Dated provider metadata cannot confer authentication or trading authority."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ariadne import vix_sources as v


def assessment():
    return {"schema": 1, "checked_at": "2026-10-30T22:00:00Z", "existing_account_route": {
        "provider": "Robinhood", "status": "awaiting_authenticated_verification",
        "checked_at": "2026-10-30T22:01:00Z", "official_url": v.OFFICIAL_URL,
        "mcp_url": v.MCP_URL, "read_tools": list(v.READ_TOOLS),
        "connection_status": "not_connected", "observation": "Dated inventory observation only.",
        "observation_scope": "Codex tool inventory and Chrome login page checked during source review; not live Ariadne connection detection"}}


class VixSourceReadinessTests(unittest.TestCase):
    def test_separates_public_no_account_from_existing_account_candidate(self):
        value = v.readiness(assessment())
        self.assertEqual(value["public_no_account"]["status"], "not_qualified")
        route = value["existing_account"]
        self.assertEqual(route["status"], "awaiting_authenticated_verification")
        self.assertEqual(route["connection_status"], "requires_verification")
        self.assertEqual(route["checked_at"], "2026-10-30T22:01:00+00:00")
        self.assertEqual(route["official_url"], v.OFFICIAL_URL)
        self.assertEqual(route["mcp_url"], v.MCP_URL)
        self.assertEqual(route["read_tools"], list(v.READ_TOOLS))
        self.assertFalse(value["runtime_connection_checked"])

    def test_all_gates_blocked_and_one_budget_without_account_authority(self):
        value = v.readiness(assessment())
        self.assertEqual([item["id"] for item in value["gates"]], [
            "authenticated_market_data", "actual_vix_quotes", "side_sizes_quote_clocks",
            "exact_expiry_vx", "official_calendar", "shared_ledger_migration"])
        self.assertTrue(all(item["status"] == "unverified" for item in value["gates"]))
        self.assertFalse(value["paper_fills_enabled"])
        self.assertEqual(value["funding_added"], "0")
        self.assertEqual(value["shared_monthly_cap"], "500.00")
        self.assertNotIn("account_id", value)

    def test_pure_deterministic_detached_result_no_io(self):
        source = assessment(); before = deepcopy(source)
        with patch("socket.create_connection", side_effect=AssertionError("no network")), \
             patch("subprocess.Popen", side_effect=AssertionError("no process")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("no files")), \
             patch.object(Path, "write_bytes", side_effect=AssertionError("no writes")):
            first, second = v.readiness(source), v.readiness(source)
        self.assertEqual(first, second)
        self.assertEqual(source, before)
        first["existing_account"]["read_tools"].append("place_option_order")
        first["gates"][0]["status"] = "qualified"
        self.assertEqual(v.readiness(source), second)

    def test_absent_malformed_metadata_fails_closed(self):
        for source in (None, [], "metadata", {}, {"schema": True}, {"schema": 2},
                       {"schema": 1, "checked_at": "2026-10-30"},
                       {"schema": 1, "checked_at": "2026-10-30T22:00:00"}):
            with self.subTest(source=source):
                value = v.readiness(source)
                self.assertEqual(value["status"], "metadata_unavailable")
                self.assertFalse(value["paper_fills_enabled"])
                self.assertEqual(value["existing_account"]["read_tools"], [])

    def test_candidate_metadata_cannot_expand_tools_endpoints_or_authority(self):
        changes = (("provider", "Other"), ("status", "qualified"),
                   ("connection_status", "connected"), ("checked_at", "yesterday"),
                   ("mcp_url", v.MCP_URL+"?token=secret"),
                   ("official_url", "https://example.com/"),
                   ("read_tools", [*v.READ_TOOLS, "place_option_order"]),
                   ("read_tools", [v.READ_TOOLS[0]]*len(v.READ_TOOLS)),
                   ("read_tools", "get_option_quotes"), ("paper_fills_enabled", True))
        for key, replacement in changes:
            with self.subTest(key=key, replacement=replacement):
                source = assessment(); source["existing_account_route"][key] = replacement
                value = v.readiness(source)
                self.assertEqual(value["status"], "metadata_unavailable")
                self.assertFalse(value["paper_fills_enabled"])
                self.assertTrue(all(item["status"] == "unverified" for item in value["gates"]))

    def test_does_not_copy_provider_content_credentials_or_source_claims(self):
        source = assessment()
        source.update(paper_fills_enabled=True, shared_monthly_cap="999999", account_id="private-account",
                      outcome="execute now", credentials={"token": "private-token"})
        source["existing_account_route"]["observation"] = "private-token "*100000
        value = v.readiness(source)
        raw = json.dumps(value)
        self.assertLess(len(raw), 5000)
        self.assertNotIn("private-token", raw)
        self.assertNotIn("private-account", raw)
        self.assertNotIn("execute now", raw)
        self.assertFalse(value["paper_fills_enabled"])
        self.assertEqual(value["shared_monthly_cap"], "500.00")

    def test_connection_observation_is_dated_and_not_promoted_to_runtime_fact(self):
        for observed in ("not_connected", "unverified", "requires_verification"):
            source = assessment(); source["existing_account_route"]["connection_status"] = observed
            value = v.readiness(source)
            self.assertEqual(value["status"], "awaiting_authenticated_verification")
            self.assertEqual(value["existing_account"]["connection_status"], "requires_verification")
            self.assertIn("no live Ariadne connection detection", value["existing_account"]["observation_scope"])


if __name__ == "__main__":
    unittest.main()
