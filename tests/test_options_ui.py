"""Offline behavioral UI contracts for asymmetric options research."""
import json
from pathlib import Path
import subprocess
import unittest


class OptionsUIContractTests(unittest.TestCase):
    def test_offline_interaction_contracts(self):
        root = Path(__file__).resolve().parents[1]
        runtime = root / "runtime/automation/node-v24.22.1-win-x64/node.exe"
        if not runtime.is_file():
            self.skipTest("Bundled Node runtime is not installed in this source checkout.")
        result = subprocess.run([str(runtime), str(root / "tests/fixtures/options_ui_contract.cjs")],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertTrue(result.stdout, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, json.dumps(receipt.get("failed", []), indent=2)+result.stderr)
        self.assertEqual(receipt["failures"], 0)
        self.assertIn("Archival preserves monthly allowance and never invents a sell or funding reset", receipt["checks"])
        self.assertIn("Uncertain transport has no automatic retry and requires inspected receipts", receipt["checks"])
        self.assertIn("Budget and contributions are explicitly simulated and excluded from profits", receipt["checks"])


if __name__ == "__main__":
    unittest.main()
