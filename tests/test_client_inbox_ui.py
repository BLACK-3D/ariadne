"""Offline inbox/next-action interaction contracts using synthetic records only."""
import json
from pathlib import Path
import subprocess
import unittest


class ClientInboxUIContractTests(unittest.TestCase):
    def test_review_and_lifecycle_contracts(self):
        root = Path(__file__).resolve().parents[1]
        runtime = root / "runtime/automation/node-v24.22.1-win-x64/node.exe"
        if not runtime.is_file():
            self.skipTest("Bundled Node runtime is not installed in this checkout.")
        result = subprocess.run(
            [str(runtime), str(root / "tests/fixtures/client_inbox_ui_contract.cjs")],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertGreaterEqual(receipt["passed"], 26)
        self.assertEqual(receipt["failures"], 0)


if __name__ == "__main__":
    unittest.main()
