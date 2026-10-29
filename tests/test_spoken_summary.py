"""Spoken excerpts stay brief without hiding verification failure or changing math."""
import copy
import unittest

from ariadne.wake import WakeService


class SpokenSummaryTests(unittest.TestCase):
    def run_with(self, text, **values):
        return {'status': 'completed', 'context': [{'role': 'assistant', 'content': text}], **values}

    def test_short_answer_and_source_context_preserved(self):
        run = self.run_with('The reserve estimate is uncertain; the battery has not been measured.')
        before = copy.deepcopy(run)
        self.assertEqual(WakeService.response_text(run), run['context'][0]['content'])
        self.assertEqual(run, before)

    def test_long_answer_is_bounded_and_discloses_console_excerpt(self):
        text = 'The source records remain incomplete. ' + 'More detail is available for inspection. ' * 100
        result = WakeService.response_text(self.run_with(text))
        self.assertLessEqual(len(result), 450)
        self.assertLessEqual(len(result.split()), 71)
        self.assertTrue(result.endswith('Full result in the console.'))
        self.assertIn('source records remain incomplete', result)

    def test_failed_output_checks_override_model_success_claim(self):
        result = WakeService.response_text(self.run_with('Done. Everything passed.', verification_status='failed'))
        self.assertEqual(result, 'Output checks failed. Review the result in the console.')
        self.assertNotIn('Everything passed', result)

    def test_approval_and_incomplete_status_never_read_success_claim(self):
        for status in ['waiting_approval', 'failed', 'exhausted', 'cancelled', 'interrupted', 'running']:
            with self.subTest(status=status):
                result = WakeService.response_text(self.run_with('All finished.', status=status))
                self.assertNotIn('All finished', result)
                self.assertIn('console', result)
        self.assertIn('needs your approval', WakeService.response_text({'status': 'waiting_approval'}))

    def test_markup_code_and_urls_do_not_become_robotic_narration(self):
        text = '# Result\n**Saved** [the report](https://example.invalid/report).\n```python\nprint("do not read code")\n```'
        result = WakeService.response_text(self.run_with(text))
        self.assertIn('Saved the report.', result)
        self.assertIn('Code is in the console.', result)
        for markup in ['https:', '```', '**', '#', 'print(']:
            self.assertNotIn(markup, result)

    def test_math_operators_and_negative_values_are_not_markdown(self):
        text = '2*3 equals 6. 2**3**4 is an expression.\n-  5 is negative five. file_name.csv remains a file name.'
        result = WakeService.response_text(self.run_with(text))
        self.assertIn('2*3 equals 6.', result)
        self.assertIn('2**3**4 is an expression.', result)
        self.assertIn('- 5 is negative five.', result)
        self.assertIn('file_name.csv', result)

    def test_last_final_answer_selected_and_controls_removed(self):
        run = self.run_with('ignored')
        run['context'] = [{'role': 'tool', 'content': 'Tool output must not be spoken.'},
                          {'role': 'assistant', 'content': 'not final', 'tool_calls': [{}]},
                          {'role': 'assistant', 'content': 'Ready.\x00 The result is saved.'}]
        self.assertEqual(WakeService.response_text(run), 'Ready. The result is saved.')


if __name__ == '__main__':
    unittest.main()
