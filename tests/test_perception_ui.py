"""Offline behavioral contracts for the recording-only awareness desk."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class PerceptionUIContractTests(unittest.TestCase):
    def test_offline_awareness_interactions(self):
        root = Path(__file__).resolve().parents[1]
        bundled = root / "runtime/automation/node-v24.22.1-win-x64/node.exe"
        node = str(bundled) if bundled.is_file() else shutil.which("node")
        if not node:
            self.skipTest("Node runtime is unavailable.")
        result = subprocess.run(
            [node, str(root / "scripts/check_perception_ui.cjs")], cwd=root,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertTrue(result.stdout, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, json.dumps(receipt.get("failed", []), indent=2) + result.stderr)
        self.assertEqual(receipt["failures"], 0)
        self.assertGreaterEqual(len(receipt["checks"]), 43)
        for name in (
            "Historical freshness never becomes present-time room awareness",
            "Malicious labels remain inert text and never create executable elements",
            "Out of order selections cannot replace the newest recording",
            "Lock during a mutation preserves unresolved identity but ignores late success",
            "Explicit RuView import binds JSONL path policy request identity and revision",
            "Synthetic RuView demo is one explicit action and replay never changes its origin",
            "RuView scores clocks and node declarations never become calibrated physical measurements",
            "RuView source hashes profile policy and physical-clock claims must match the verified receipt",
            "RuView pause hold and uncertain outcomes preserve the existing no-retry boundary",
            "Contradictory RuView declarations are flagged without resolving current occupancy",
        ):
            self.assertIn(name, receipt["checks"])
