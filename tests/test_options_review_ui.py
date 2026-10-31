"""Offline read-only paper-journal interaction checks."""
import json
from pathlib import Path
import subprocess
import unittest

class OptionsReviewUIContractTests(unittest.TestCase):
    def test_readonly_interaction_contracts(self):
        root=Path(__file__).resolve().parents[1]
        node=root/"runtime/automation/node-v24.22.1-win-x64/node.exe"
        if not node.is_file():self.skipTest("Bundled Node runtime not installed in this checkout")
        result=subprocess.run([str(node),str(root/"tests/fixtures/options_review_ui_contract.cjs")],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=30)
        self.assertTrue(result.stdout,result.stderr)
        receipt=json.loads(result.stdout)
        self.assertEqual(result.returncode,0,str(receipt.get("failed"))+result.stderr)
        self.assertEqual(receipt["failures"],0)
        self.assertGreaterEqual(len(receipt["checks"]),12)
