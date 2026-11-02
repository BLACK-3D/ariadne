"""Project Workroom UI interaction contracts; synthetic DOM/API, no model or network."""
import json
from pathlib import Path
import subprocess
import unittest


class WorkroomUIContractTests(unittest.TestCase):
    def test_project_cycle_review_and_lifecycle_contracts(self):
        root = Path(__file__).resolve().parents[1]
        runtime = root / "runtime/automation/node-v24.22.1-win-x64/node.exe"
        if not runtime.is_file():
            self.skipTest("Bundled Node runtime is not installed in this checkout.")
        result = subprocess.run(
            [str(runtime), str(root / "tests/fixtures/workroom_ui_contract.cjs")],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertGreaterEqual(receipt["passed"], 51)
        self.assertEqual(receipt["failures"], 0)


if __name__ == "__main__":
    unittest.main()
