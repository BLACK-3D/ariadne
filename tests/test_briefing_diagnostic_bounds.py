"""Failure telemetry remains bounded even for virtual traceback filenames."""
import json
from pathlib import Path
import unittest
from ariadne.briefing_diagnostics import failure_detail

ROOT = Path(__file__).absolute().parents[1]


class DiagnosticBoundsTests(unittest.TestCase):
    def detail_from_code(self, source, filename):
        try:
            exec(compile(source, str(ROOT/"ariadne"/filename), "exec"), {})
        except ValueError as error:
            return failure_detail(error, phase="render")
        self.fail("Fixture did not fail")

    def test_deep_trace_keeps_eight_short_application_frames_without_arguments(self):
        detail = self.detail_from_code("def f(n):\n if n: f(n-1)\n else: raise ValueError('SECRET_SENTINEL')\nf(20)", "virtual_fixture.py")
        raw = json.dumps(detail)
        self.assertEqual(len(detail["frames"]), 8)
        self.assertNotIn("SECRET_SENTINEL", raw)
        self.assertNotIn(str(ROOT), raw)
        self.assertLessEqual(len(raw.encode()), 4096)

    def test_unbounded_or_non_ascii_virtual_filename_is_not_emitted(self):
        for filename in ("a"*500+".py", "\u2603"*100+".py"):
            with self.subTest(filename_length=len(filename)):
                detail = self.detail_from_code("raise ValueError('SECRET_SENTINEL')", filename)
                self.assertEqual(detail["frames"], [])
                self.assertLessEqual(len(json.dumps(detail).encode()), 4096)
