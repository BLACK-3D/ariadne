"""Dependency-free local desk contracts; no owner state or model requests."""
import json
from pathlib import Path
import subprocess
import unittest


class HandoffUIContractTests(unittest.TestCase):
    def test_source_and_interaction_contracts(self):
        root = Path(__file__).resolve().parents[1]
        runtime = root / "runtime/automation/node-v24.22.1-win-x64/node.exe"
        if not runtime.is_file():
            self.skipTest("Bundled Node runtime is not installed in this source checkout.")
        result = subprocess.run([str(runtime), str(root / "tests/fixtures/handoff_ui_contract.cjs")],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertGreaterEqual(receipt["passed"], 24)
        self.assertEqual(receipt["failures"], 0)


if __name__ == "__main__":
    unittest.main()
