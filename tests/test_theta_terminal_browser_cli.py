"""Browser setup dispatch uses the guarded data path and no model/vendor client."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ariadne.theta_terminal import ThetaTerminalError
from scripts import theta_terminal as cli

ROOT = Path(__file__).resolve().parents[1]


class ThetaBrowserCliTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / 'runtime' / 'tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='theta-browser-cli-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data' / 'workspace').mkdir(parents=True)

    def test_browser_action_uses_guarded_local_store_without_tty_or_provider(self):
        args = cli.parser().parse_args(['setup-browser'])
        with patch('ariadne.theta_setup_browser.serve', return_value={'configured': True}) as serve, \
                patch.object(cli, '_hidden', side_effect=AssertionError('No terminal input')):
            value = cli.run(args, root=self.root,
                            client_factory=lambda *a, **k: self.fail('No provider client'))
        self.assertTrue(value['configured'])
        self.assertEqual(serve.call_args.args[0].root, self.root / 'data')
        self.assertTrue(callable(serve.call_args.kwargs['ready']))
        self.assertFalse((self.root / 'data' / 'ariadne.sqlite3').exists())

    def test_browser_action_refuses_online_flag_before_server(self):
        args = cli.parser().parse_args(['setup-browser', '--online'])
        with patch.dict(os.environ, {'ARIADNE_OFFLINE': '0'}), \
                patch('ariadne.theta_setup_browser.serve', side_effect=AssertionError('No server')):
            with self.assertRaises(ThetaTerminalError):
                cli.run(args, root=self.root)

    def test_browser_action_cannot_move_store_outside_project(self):
        args = cli.parser().parse_args(['setup-browser', '--data-dir', '../outside'])
        with patch('ariadne.theta_setup_browser.serve', side_effect=AssertionError('No server')):
            with self.assertRaises((ValueError, OSError)):
                cli.run(args, root=self.root)


if __name__ == '__main__':
    unittest.main()
