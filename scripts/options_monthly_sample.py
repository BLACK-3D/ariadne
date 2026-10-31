"""Offline monthly-sample qualification, never owner paper/account operations.

Every invocation reserves a fresh output folder. Tentative files or interrupted
receipts are never replayed/adopted. Only completed.json binds the final index.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_SECONDS = 1800
MAX_OUTPUT = 2 * 1024**3
FAILURE_RESERVE = 64 * 1024
SOURCE_FILES = ('ariadne/options_monthly_samples.py', 'ariadne/options_data.py', 'ariadne/options_archive.py',
    'ariadne/qqq_data.py', 'ariadne/resources/market/America_New_York.tzif',
    'ariadne/briefing.py', 'ariadne/resources/briefing/America_Chicago.tzif', 'ariadne/atomic.py',
    'ariadne/config.py', 'ariadne/tools.py', 'ariadne/backup.py', 'ariadne/network_policy.py',
    'scripts/options_monthly_sample.py', 'scripts/options_campaign.py', 'scripts/options_market.py')


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--zip', required=True, help='Box-relative original offered monthly ZIP.')
    result.add_argument('--zip-sha256', required=True)
    result.add_argument('--retrieved-at', required=True, help='Actual source HTTP receipt, aware ISO time.')
    result.add_argument('--assume-standard-100', action='store_true')
    result.add_argument('--workspace', default='data/workspace')
    result.add_argument('--output', required=True, help='New exclusive workspace-relative research archive folder.')
    result.add_argument('--owner-data', default='data')
    result.add_argument('--detached-research', action='store_true', help='Only an isolated checkout without owner DB.')
    return result


def _sources(root):
    from scripts.options_campaign import _root_guard
    from scripts.options_market import read
    box = _root_guard(root)
    return {name: hashlib.sha256(read(box, name, 2*1024**2)).hexdigest() for name in SOURCE_FILES}


def run(args, *, root=ROOT, now=None):
    from ariadne.atomic import publish_bytes
    from ariadne.options_monthly_samples import convert_monthly, source_hash, MAX_ZIP
    from ariadne.options_data import canonical, clock
    from scripts.options_campaign import PauseAuthority, _root_guard
    from scripts.options_market import guard_for, read
    current = clock(now)
    if args.assume_standard_100 is not True:
        raise ValueError('--assume-standard-100 is required; terms are not authenticated.')
    box, workspace = _root_guard(root), guard_for(args.workspace, root=root)
    source_path = box._path(args.zip)
    output = workspace._path(args.output)
    if args.detached_research:
        for relative in dict.fromkeys(('data', args.owner_data)):
            if box._path(relative+'/ariadne.sqlite3').exists():
                raise ValueError('Detached mode cannot bypass a present owner database.')
    authority = PauseAuthority(box, args.owner_data, args.detached_research)
    deadline, last_check = time.monotonic()+MAX_SECONDS, 0
    written, used, reserved, phase = {}, 0, False, 'reservation'

    def checkpoint(force=False):
        nonlocal last_check
        now_clock = time.monotonic()
        if now_clock >= deadline:
            raise TimeoutError('Monthly offline qualification exceeded its fixed1800second deadline; no replay.')
        if force or now_clock-last_check >= .1:
            authority.check()
            last_check = time.monotonic()
        if time.monotonic() >= deadline:
            raise TimeoutError('Authority checkpoint exhausted monthly qualification deadline.')

    def publish(name, raw):
        nonlocal used
        checkpoint()
        if name in written or used+len(raw) > MAX_OUTPUT-FAILURE_RESERVE:
            raise ValueError('Duplicate artifact or2GiB output/failure-reserve limit; no overwrite/truncation.')
        target = workspace._path(args.output+'/'+name)
        with workspace._directory(target.parent, create=True):
            publish_bytes(target, raw, checkpoint=lambda: checkpoint(True))
        written[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        used += len(raw)

    try:
        with workspace._directory(output.parent, create=True):
            output.mkdir(exist_ok=False)
        reserved = True
        with workspace._directory(output), ExitStack() as held:
            checkpoint(True)
            hashes = _sources(root)
            publish('intent.json', canonical({'schema': 1, 'kind': 'offline_monthly_options_qualification',
                'registered_at': current.isoformat(), 'zip': args.zip, 'zip_sha256': args.zip_sha256,
                'actual_source_retrieved_at': args.retrieved_at, 'standard100_assumption': True,
                'module_sha256': hashes, 'owner_authority': authority.scope(),
                'limits': {'seconds': MAX_SECONDS, 'output_bytes': MAX_OUTPUT,
                    'failure_receipt_reserve_bytes': FAILURE_RESERVE},
                'source_execution': False, 'paper_operations': 0, 'backtests_run': 0}))
            directory = held.enter_context(box._directory(source_path.parent))
            stream = held.enter_context(os.fdopen(box._open_read(source_path, directory), 'rb'))
            info = os.fstat(stream.fileno())
            identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            if not 1 <= info.st_size <= MAX_ZIP:
                raise ValueError('Original ZIP size exceeds acquisition scope.')
            phase = 'qualification'
            print(json.dumps({'phase':'source_qualification','source_ZIP_sha256':args.zip_sha256,
                'storage':'bounded_encoded_snapshots_then_monthly_chunks'}),file=sys.stderr,flush=True)
            result = convert_monthly(stream, zip_sha256=args.zip_sha256, retrieved_at=args.retrieved_at,
                standard100_assumption=True, as_of=current, checkpoint=checkpoint,
                emit=publish, output_prefix=args.output)
            print(json.dumps({'phase':'source_qualified_outputs_tentative','artifacts':len(result['artifacts']),
                'qualified_symbols':result['receipt']['qualified_symbols'],
                'encoded_snapshot_bytes':result['receipt']['encoded_snapshot_bytes']}),file=sys.stderr,flush=True)

            def verify_sources():
                checkpoint(True)
                if _sources(root) != hashes:
                    raise ValueError('Converter/dependency bytes changed during qualification; no adoption.')
                with box._directory(source_path.parent) as current_directory:
                    descriptor = box._open_read(source_path, current_directory)
                    try:
                        current_info = os.fstat(descriptor)
                    finally:
                        os.close(descriptor)
                key = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
                if key(current_info) != identity or key(os.fstat(stream.fileno())) != identity:
                    raise ValueError('Source ZIP identity changed during qualification.')
                source_hash(stream, args.zip_sha256, checkpoint)
                if key(os.fstat(stream.fileno())) != identity:
                    raise ValueError('Source changed while final SHA verification was running.')

            phase = 'final_verification'
            if {name:written.get(name) for name in result['artifacts']} != result['artifacts']:
                raise ValueError('Emitted artifact identities differ from converter receipt.')
            publish('qualification.json', canonical({**result['receipt'], 'module_sha256': hashes,
                'owner_authority': authority.scope()}))
            def final_verify():
                verify_sources()
                for name, item in written.items():
                    checkpoint()
                    raw = read(workspace, args.output+'/'+name, item['bytes'])
                    if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                        raise ValueError('Tentative artifact changed before completed publication.')
                checkpoint(True)
            final_verify()
            completed = {'schema': 1, 'status': 'completed', 'artifacts': written.copy(),
                'module_sha256': hashes, 'source_ZIP_sha256': args.zip_sha256,
                'actual_source_retrieved_at': args.retrieved_at, 'owner_authority': authority.scope(),
                'universe_index': {'path': args.output+'/universe.json', **written['universe.json']},
                'all_source_and_output_bytes_reverified': True, 'output_bytes_before_receipt': used,
                'research_only': True, 'execution_qualified': False, 'automatic_adoption': False,
                'owner_settings_written': False, 'paper_operations': 0, 'network_calls': 0,
                'model_calls': 0, 'backtests_run': 0}
            raw = canonical(completed)
            if used+len(raw) > MAX_OUTPUT-FAILURE_RESERVE:
                raise ValueError('Completed receipt exceeds2GiB output/failure-reserve cap; no adoption.')
            publish_bytes(output/'completed.json', raw, checkpoint=final_verify)
            return completed
    except BaseException as error:
        if reserved:
            failure = {'schema': 1, 'status': 'failed', 'phase': phase, 'error_type': type(error).__name__,
                'reason': str(error)[:1000], 'owner_authority': authority.scope(),
                'retained_artifact_count': len(written), 'retained_artifact_bytes': used,
                'written_manifest_sha256': hashlib.sha256(canonical(written)).hexdigest(),
                'written_manifest_basis': 'Exact canonical JSON of all successfully published relative artifact names mapped to their bytes and SHA256. Files stay retained; uncertain/unrecorded files are never adopted.',
                'replay_allowed': False, 'adopted': False, 'owner_settings_written': False,
                'paper_operations': 0, 'network_calls': 0, 'model_calls': 0, 'backtests_run': 0}
            try:
                raw = canonical(failure)
                if len(raw) > FAILURE_RESERVE or used+len(raw) > MAX_OUTPUT:
                    raise ValueError('Compact failure receipt exceeds its reserved quota.')
                with workspace._directory(output):
                    publish_bytes(output/'failed.json', raw)
            except (OSError, ValueError):
                pass
        raise
    finally:
        authority.close()


def main(argv=None):
    from scripts.options_campaign import install_offline_guards
    install_offline_guards()
    args = parser().parse_args(argv)
    try:
        receipt = run(args)
    except (ValueError, OSError, UnicodeError, OverflowError, RecursionError) as error:
        print('Monthly sample qualification stopped: '+str(error), file=sys.stderr)
        return 2
    print(json.dumps({'status': receipt['status'], 'universe_index_path': receipt['universe_index']['path'],
        'universe_index_sha256': receipt['universe_index']['sha256'], 'artifact_count': len(receipt['artifacts']),
        'source_ZIP_sha256': receipt['source_ZIP_sha256'],
        'output_bytes_before_receipt': receipt['output_bytes_before_receipt']}, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
