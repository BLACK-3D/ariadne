"""Offline interaction contracts for explicit native Vision capture and evidence."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class VisionUIContractTests(unittest.TestCase):
    def test_offline_native_vision_interactions(self):
        root = Path(__file__).resolve().parents[1]
        bundled = root / "runtime/automation/node-v24.22.1-win-x64/node.exe"
        node = str(bundled) if bundled.is_file() else shutil.which("node")
        if not node:
            self.skipTest("Node runtime is unavailable.")
        result = subprocess.run(
            [node, str(root / "scripts/check_vision_ui.cjs")], cwd=root,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertTrue(result.stdout, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, json.dumps(receipt.get("failed", []), indent=2) + result.stderr)
        self.assertEqual(receipt["failures"], 0)
        self.assertGreaterEqual(len(receipt["checks"]), 28)
        for name in (
            "Opening and global status ticks never discover or start a sensor",
            "Stale or disconnected evidence never shows current presence or an all clear",
            "Only the explicitly started session renews a private capture lease",
            "Late status and detail reads cannot restore locked evidence",
            "Malicious device and object labels remain inert text",
        ):
            self.assertIn(name, receipt["checks"])
