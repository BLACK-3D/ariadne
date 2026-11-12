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
        self.assertGreaterEqual(len(receipt["checks"]), 25)
        for name in (
            "Historical freshness never becomes present-time room awareness",
            "Malicious labels remain inert text and never create executable elements",
            "Out of order selections cannot replace the newest recording",
            "Lock during a mutation preserves unresolved identity but ignores late success",
        ):
            self.assertIn(name, receipt["checks"])
