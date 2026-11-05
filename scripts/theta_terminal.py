"""Prepare a private Theta key and reviewed application artifact; never launch Java."""
from __future__ import annotations
import argparse
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
import warnings
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ariadne.theta_terminal import ThetaTerminalError


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        raise ThetaTerminalError('Use setup, status or prepare with the documented local options. Never put an API key in command arguments.')


def parser():
    value = PrivateParser(description=__doc__)
    value.add_argument('action',choices=('setup','status','prepare'))
    value.add_argument('--data-dir',default='data')
    value.add_argument('--workspace',default='data/workspace')
    value.add_argument('--online',action='store_true')
    return value


def _hidden():
    if not sys.stdin.isatty():
        raise ThetaTerminalError('Enter the Theta API key only in an interactive local terminal. Piped credentials are refused.')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        try:
            return getpass.getpass('Theta API key (hidden; never your account password): ')
        except getpass.GetPassWarning:
            raise ThetaTerminalError('A hidden local console is required; no key was saved.') from None


def run(args, *, root=ROOT, client_factory=None):
    from ariadne.tools import ToolRegistry
    from ariadne.theta_terminal import save_key,status,prepare
    from ariadne.network_policy import network_policy_status
    from scripts.alpaca_market import _guards,_network_allowed
    if args.online and (os.environ.get('ARIADNE_OFFLINE') not in (None,'0')
                        or network_policy_status()['mode']=='offline'):
        raise ThetaTerminalError('Inherited or installed offline policy blocks preparation.')
    if args.action in ('setup','status') and args.online:
        raise ThetaTerminalError('Setup and status are local-only and do not accept --online.')
    if args.action=='prepare' and not args.online:
        raise ThetaTerminalError('Application preparation requires explicit --online.')
    data,workspace = _guards(args,root)
    root_guard = object.__new__(ToolRegistry);root_guard.root = Path(root).resolve()
    if args.action=='setup':
        key = _hidden()
        try: return save_key(data,key)
        finally: key = None
    if args.action=='status':
        return status(root_guard,data)
    if args.action=='prepare':
        return prepare(root_guard,data,check=lambda:_network_allowed(data),client_factory=client_factory)
    raise ThetaTerminalError('Unsupported Theta preparation action.')


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if args.online and os.environ.get('ARIADNE_OFFLINE') not in (None,'0'):
            raise ThetaTerminalError('Inherited offline policy blocks preparation.')
        os.environ['ARIADNE_OFFLINE']='0' if args.online else '1'
        from ariadne.network_policy import install_network_policy
        install_network_policy()
        result = run(args)
    except ThetaTerminalError as error:
        print(str(error),file=sys.stderr);return 2
    except (OSError,ValueError,TypeError,UnicodeError,RecursionError,OverflowError,sqlite3.Error,EOFError):
        print('Theta preparation failed validation or access. Inspect local evidence privately; no application or paper trade was started.',file=sys.stderr)
        return 2
    print(json.dumps(result,ensure_ascii=True,allow_nan=False,indent=2));return 0


if __name__=='__main__':
    raise SystemExit(main())
