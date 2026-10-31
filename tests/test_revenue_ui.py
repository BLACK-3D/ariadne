"""Offline revenue-workbench DOM interaction contracts."""
import json
from pathlib import Path
import subprocess
import unittest


class RevenueUIContractTests(unittest.TestCase):
    def test_offline_revenue_interactions(self):
        root=Path(__file__).resolve().parents[1]
        node=root/"runtime/automation/node-v24.22.1-win-x64/node.exe"
        if not node.is_file():self.skipTest("Bundled Node runtime is not installed in this source checkout.")
        result=subprocess.run([str(node),str(root/"tests/fixtures/revenue_ui_contract.cjs")],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=30)
        self.assertTrue(result.stdout,result.stderr)
        receipt=json.loads(result.stdout)
        self.assertEqual(result.returncode,0,json.dumps(receipt.get("failed",[]),indent=2)+result.stderr)
        self.assertEqual(receipt["failures"],0)
        self.assertGreaterEqual(len(receipt["checks"]),24)
        for name in ("Illustrative comparison only prefills local fields and assumes zero sales",
                     "Lost transport keeps its unique request and cannot retry from reads or status ticks",
                     "Recorded sales never manufacture a payout or collected cash"):
            self.assertIn(name,receipt["checks"])
