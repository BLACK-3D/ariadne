"""Inspect direct Theta assets or preview one EOD selection; never discover keys.

Authenticated work is deliberately separate: a reviewed caller registers a
source-pinned worker and owns its global attempt reservation. supervise() gives
that worker a Windows lifetime job, private environment and hard deadline.
Neither the CLI nor this module grants a new setup budget or trading authority.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_SECONDS = 300


class SupervisionError(ValueError):
    def __init__(self):
        super().__init__('Theta direct worker stopped. Preserve its reservation and inspect the sanitized local receipt; no automatic retry.')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _guard(root):
    from ariadne.config import _allowed_path
    from ariadne.tools import ToolRegistry
    box = object.__new__(ToolRegistry)
    box.root = _allowed_path(root)
    return box


def _read(box, name, maximum=4*1024*1024):
    from scripts.options_market import read
    return read(box, name, maximum)


def _pins(box, values):
    if type(values) is not dict or not 3 <= len(values) <= 64:
        raise SupervisionError()
    for name, expected in values.items():
        if (type(name) is not str or re.fullmatch(r'(?:ariadne|scripts|staging)/[A-Za-z0-9_/.-]+\.py', name) is None
                or type(expected) is not str or re.fullmatch('[a-f0-9]{64}', expected) is None
                or hashlib.sha256(_read(box, name)).hexdigest() != expected):
            raise SupervisionError()


def owner_policy(box):
    """Read existing controls with a short busy/progress deadline; never write."""
    from ariadne.config import _allowed_path
    from scripts.alpaca_market import _controls
    path = box._path('data/ariadne.sqlite3')
    for suffix in ('', '-wal', '-shm', '-journal'):
        item = _allowed_path(str(path)+suffix)
        if item.exists() and (not item.is_file() or item.stat().st_nlink != 1):
            raise SupervisionError()
    with box._directory(path.parent), closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=0.05)) as db:
        limit = time.monotonic()+0.05
        db.set_progress_handler(lambda: int(time.monotonic() >= limit), 100)
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA query_only=ON')
        state = _controls(db)
    if (state['paused'] is not None and state['paused']['value'] is True
            or state['internet_access'] is None or state['internet_access']['value']['enabled'] is not True):
        raise SupervisionError()
    return hashlib.sha256(canonical(state)).hexdigest()


def _environment(root, home):
    import ctypes
    from ariadne.config import _allowed_path
    system = os.environ.get('SystemRoot')
    if (not system or not Path(system).is_absolute() or system.startswith(('\\\\', '//'))
            or Path(system).drive.upper() == 'E:'):
        raise SupervisionError()
    system_path = _allowed_path(system)
    buffer = ctypes.create_unicode_buffer(32768)
    get_directory = ctypes.WinDLL('kernel32', use_last_error=True).GetWindowsDirectoryW
    get_directory.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
    get_directory.restype = ctypes.c_uint
    count = get_directory(buffer, len(buffer))
    if (not 0 < count < len(buffer)
            or os.path.normcase(str(system_path)) != os.path.normcase(buffer.value)):
        raise SupervisionError()
    return {'SystemRoot': system, 'WINDIR': system,
        'PATH': str(root/'runtime/python')+';'+str(Path(system)/'System32'),
        'TEMP': str(home/'tmp'), 'TMP': str(home/'tmp'), 'HOME': str(home),
        'USERPROFILE': str(home), 'APPDATA': str(home), 'LOCALAPPDATA': str(home),
        'ARIADNE_OFFLINE': '0', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONNOUSERSITE': '1', 'PYTHONUTF8': '1'}


def supervise(profile, *, root=ROOT):
    """Execute one reviewed Python worker, never credentials or arbitrary argv.

    profile's worker validates its own reservation/authorization. A successful
    exit means only that the process exited; callers must verify its evidence.
    Job closure kills this newly owned process tree even if supervision fails.
    Native gRPC is not constrained by Python's socket hook or an OS sandbox.
    """
    from ariadne.atomic import publish_bytes
    from ariadne.network_policy import network_policy_status
    from scripts.windows_host import ServiceJob, process_identity
    if os.name != 'nt' or os.environ.get('ARIADNE_OFFLINE') != '0' or network_policy_status()['mode'] != 'online':
        raise SupervisionError()
    fields = {'worker', 'binding', 'home', 'ordinal', 'source_sha256', 'asset_review_sha256',
        'protocol_amendment', 'protocol_amendment_sha256', 'python_sha256',
        'selection', 'allowed_symbols', 'research_window', 'registered_at', 'owner_policy_sha256'}
    if (type(profile) is not dict or set(profile) != fields
            or type(profile['ordinal']) is not int or profile['ordinal'] not in (2, 3)):
        raise SupervisionError()
    box = _guard(root)
    for key in ('worker', 'binding', 'home', 'protocol_amendment'):
        value = profile[key]
        if (type(value) is not str or not value.startswith('staging/')
                or '\\' in value or ':' in value or '..' in value.split('/')):
            raise SupervisionError()
        box._path(value)
    if (not profile['worker'].endswith('.py') or not profile['binding'].endswith('.json')
            or not profile['protocol_amendment'].endswith('.json')
            or profile['worker'] not in profile['source_sha256']
            or 'scripts/theta_direct.py' not in profile['source_sha256']
            or 'scripts/windows_host.py' not in profile['source_sha256']):
        raise SupervisionError()
    python = box._path('runtime/python/python.exe')
    if hashlib.sha256(_read(box, 'runtime/python/python.exe', 8*1024*1024)).hexdigest() != profile['python_sha256']:
        raise SupervisionError()
    amendment = _read(box, profile['protocol_amendment'], 256*1024)
    if hashlib.sha256(amendment).hexdigest() != profile['protocol_amendment_sha256']:
        raise SupervisionError()
    _pins(box, profile['source_sha256'])
    if owner_policy(box) != profile['owner_policy_sha256']:
        raise SupervisionError()
    home = box._path(profile['home'])
    with box._directory(home.parent):
        home.mkdir(exist_ok=False)
    with box._directory(home):
        (home/'tmp').mkdir(exist_ok=False)
    environment = _environment(box.root, home)
    started = time.monotonic()
    binding = {key: profile[key] for key in ('ordinal', 'source_sha256', 'asset_review_sha256',
        'protocol_amendment_sha256', 'python_sha256', 'selection', 'allowed_symbols',
        'research_window', 'registered_at')}
    binding.update(schema=1, kind='theta_direct_supervisor_binding_v1',
        parent_identity=process_identity(os.getpid()), started_monotonic=started,
        deadline_monotonic=started+MAX_SECONDS, maximum_seconds=MAX_SECONDS)
    payload = canonical(binding)
    destination = box._path(profile['binding'])
    with box._directory(destination.parent):
        publish_bytes(destination, payload)

    def check():
        if time.monotonic() >= binding['deadline_monotonic']:
            raise SupervisionError()
        if _read(box, profile['binding'], 256*1024) != payload:
            raise SupervisionError()
        if _read(box, profile['protocol_amendment'], 256*1024) != amendment:
            raise SupervisionError()
        _pins(box, profile['source_sha256'])
        if owner_policy(box) != profile['owner_policy_sha256']:
            raise SupervisionError()
        if time.monotonic() >= binding['deadline_monotonic']:
            raise SupervisionError()

    job = ServiceJob()
    expired = threading.Event()
    handle_lock = threading.Lock()

    def expire():
        # Close just the lifetime handle here, not process.wait(): native file,
        # SQLite or RPC stalls in the main thread cannot delay tree termination.
        # Final cleanup joins this callback before touching the handle again.
        expired.set()
        with handle_lock:
            handle, job.handle = job.handle, None
            if handle:
                job.kernel.CloseHandle(handle)

    timer = threading.Timer(max(0, binding['deadline_monotonic']-time.monotonic()), expire)
    timer.daemon = True
    try:
        timer.start()
        check()
        if expired.is_set():
            raise SupervisionError()
        child = job.popen([str(python), '-I', '-B', str(box._path(profile['worker'])),
            str(profile['ordinal']), profile['binding']], cwd=home, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000)
        while child.poll() is None:
            if expired.is_set():
                raise SupervisionError()
            check()
            time.sleep(min(0.5, max(0, binding['deadline_monotonic']-time.monotonic())))
        check()
        if expired.is_set():
            raise SupervisionError()
        result = {'schema':1, 'ordinal':profile['ordinal'], 'exit_code':child.returncode,
            'wall_seconds':round(time.monotonic()-started, 3), 'owned_worker_exit_verified':True,
            'worker_evidence_verified':False, 'automatic_retry':False, 'paper_fills':0}
    finally:
        timer.cancel()
        if timer.ident is not None:
            timer.join()
        job.close()
    return result


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        raise SupervisionError()


def parser():
    result = PrivateParser(description=__doc__)
    result.add_argument('action', choices=('status', 'preview'))
    result.add_argument('--symbol')
    result.add_argument('--contract')
    result.add_argument('--start')
    result.add_argument('--end')
    return result


def run(args):
    from ariadne.theta_direct import DirectEODClient, asset_status
    if args.action == 'status':
        if any((args.symbol, args.contract, args.start, args.end)):
            raise SupervisionError()
        return {'assets':asset_status(), 'authentication_performed':False,
            'credential_store_read':False, 'paper_fills':0}
    if bool(args.symbol) == bool(args.contract) or not args.start or not args.end:
        raise SupervisionError()
    from ariadne.theta_market_data import _contract
    symbol = args.symbol if args.symbol else _contract(args.contract)['symbol']
    selection = {'kind':'stock' if args.symbol else 'option',
        'symbol' if args.symbol else 'contract_id':args.symbol or args.contract,
        'start_date':args.start, 'end_date':args.end}
    return DirectEODClient().preflight(selection, allowed_symbols=(symbol,))


def main(argv=None):
    try:
        os.environ['ARIADNE_OFFLINE'] = '1'
        from ariadne.network_policy import install_network_policy
        install_network_policy()
        result = run(parser().parse_args(argv))
    except Exception:
        print('Theta direct inspection stopped. No authentication or trading was requested.', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
