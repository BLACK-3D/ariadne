"""Run only the Windows-task preview, in a fresh box folder; never register it."""
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@unittest.skipUnless(os.name == "nt", "Windows Task Scheduler preview")
class MorningTaskPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="morning task proof & ", dir=Path.cwd())
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.box = Path(cls.temporary.name)
        (cls.box/"scripts").mkdir()
        (cls.box/"runtime"/"python").mkdir(parents=True)
        (cls.box/"runtime"/"python"/"python.exe").write_bytes(b"Preview fixture only; never executed")
        (cls.box/"scripts"/"briefing.py").write_text("# Preview fixture only", encoding="utf-8")
        source = Path(__file__).resolve().parent.parent/"scripts"/"setup_morning_task.ps1"
        target = cls.box/"scripts"/source.name
        shutil.copyfile(source, target)
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            raise unittest.SkipTest("PowerShell unavailable")
        result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(target)], capture_output=True, text=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode and "requires Windows Central Standard Time" in result.stderr:
            raise unittest.SkipTest("This preview supports Central time with DST enabled")
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.output = result.stdout
        cls.tree = ET.parse(cls.box/"data"/"scheduling"/"morning-task.xml")

    def test_preview_is_floating_local_six_am_daily_and_requests_wake_without_power_changes(self):
        boundary = self.tree.find("t:Triggers/t:CalendarTrigger/t:StartBoundary", NS).text
        stamp = datetime.fromisoformat(boundary)
        self.assertIsNone(stamp.tzinfo)
        self.assertEqual((stamp.hour, stamp.minute, stamp.second), (6, 0, 0))
        self.assertEqual(self.tree.find("t:Triggers/t:CalendarTrigger/t:ScheduleByDay/t:DaysInterval", NS).text, "1")
        self.assertEqual(self.tree.find("t:Settings/t:WakeToRun", NS).text, "true")
        self.assertEqual(self.tree.find("t:Settings/t:ExecutionTimeLimit", NS).text, "PT10M")
        self.assertEqual(self.tree.find("t:Settings/t:MultipleInstancesPolicy", NS).text, "IgnoreNew")
        self.assertEqual(self.tree.find("t:Settings/t:RunOnlyIfNetworkAvailable", NS).text, "false")

    def test_task_has_only_current_logged_in_least_privilege_owner_and_no_password(self):
        self.assertEqual(self.tree.find("t:Principals/t:Principal/t:LogonType", NS).text, "InteractiveToken")
        self.assertEqual(self.tree.find("t:Principals/t:Principal/t:RunLevel", NS).text, "LeastPrivilege")
        self.assertTrue(self.tree.find("t:Principals/t:Principal/t:UserId", NS).text.startswith("S-1-5-21-"))
        self.assertEqual(len(self.tree.findall("t:Actions/t:Exec", NS)), 1)
        self.assertIn("not registered", self.output)
        self.assertFalse((self.box/"data"/"scheduling"/"morning-task-receipt.json").exists())

    def test_quoted_fixed_python_action_survives_spaces_and_xml_special_characters(self):
        action = self.tree.find("t:Actions/t:Exec", NS)
        self.assertEqual(action.find("t:Command", NS).text, str(self.box/"runtime"/"python"/"python.exe"))
        self.assertEqual(action.find("t:WorkingDirectory", NS).text, str(self.box))
        self.assertEqual(action.find("t:Arguments", NS).text, '-I -B "'+str(self.box/"scripts"/"briefing.py")+'" due --data-dir "'+str(self.box/"data")+'"')


if __name__ == "__main__":
    unittest.main()
