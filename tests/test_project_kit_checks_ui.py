"""Prepared workflow predicates must survive the ordinary editor unchanged."""
import json
from pathlib import Path
import subprocess
import unittest


class ProjectKitCheckEditorTests(unittest.TestCase):
    def test_context_navigation_cannot_revive_after_leaving_or_repairing_auth(self):
        root=Path(__file__).resolve().parents[1]
        runtime=root/'runtime/automation/node-v24.22.1-win-x64/node.exe'
        if not runtime.is_file():self.skipTest('Bundled Node runtime is not installed in this checkout.')
        result=subprocess.run([str(runtime),str(root/'tests/fixtures/project_kit_navigation.cjs')],cwd=root,capture_output=True,text=True,encoding='utf8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        receipt=json.loads(result.stdout)
        self.assertEqual(receipt['passed'],7)
        self.assertEqual(receipt['failures'],0)

    def test_typed_and_size_check_editor_contracts(self):
        root=Path(__file__).resolve().parents[1]
        runtime=root/'runtime/automation/node-v24.22.1-win-x64/node.exe'
        if not runtime.is_file():self.skipTest('Bundled Node runtime is not installed in this checkout.')
        result=subprocess.run([str(runtime),str(root/'tests/fixtures/project_kit_checks_ui.cjs')],cwd=root,capture_output=True,text=True,encoding='utf8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        receipt=json.loads(result.stdout)
        self.assertGreaterEqual(receipt['passed'],24)
        self.assertEqual(receipt['failures'],0)
