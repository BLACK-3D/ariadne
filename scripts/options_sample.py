"""Offline free-sample qualification; immutable archive, never paper operations.

Keep original ZIPs private. Every invocation reserves a new output folder.
Failure or interruption is a substantive stop; partial folders are never adopted
or replayed. A completed receipt is required before using the catalog.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_SECONDS = 600
MAX_OUTPUT = 300 * 1024**2
SOURCE_FILES = ('ariadne/options_samples.py', 'ariadne/options_data.py', 'ariadne/options_archive.py',
    'ariadne/qqq_data.py', 'ariadne/resources/market/America_New_York.tzif',
    'ariadne/briefing.py', 'ariadne/resources/briefing/America_Chicago.tzif', 'ariadne/atomic.py',
    'ariadne/config.py', 'ariadne/tools.py', 'ariadne/backup.py', 'ariadne/network_policy.py',
    'scripts/options_sample.py', 'scripts/options_campaign.py', 'scripts/options_market.py')


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--options', required=True, help='Box-relative original options ZIP.')
    result.add_argument('--stocks', required=True, help='Box-relative original stock ZIP.')
    result.add_argument('--options-sha256', required=True)
    result.add_argument('--stocks-sha256', required=True)
    result.add_argument('--retrieved-at', required=True, help='Latest actual HTTP source receipt, aware ISO time.')
    result.add_argument('--assume-standard-100', action='store_true', help='Explicit unverified research model assumption.')
    result.add_argument('--workspace', default='data/workspace')
    result.add_argument('--output', required=True, help='New exclusive workspace-relative archive folder.')
    result.add_argument('--owner-data', default='data')
    result.add_argument('--detached-research', action='store_true', help='Only an isolated checkout without an owner DB.')
    return result


def _sources():
    from scripts.options_campaign import _root_guard
    from scripts.options_market import read
    guard = _root_guard(ROOT)
    return {name: hashlib.sha256(read(guard, name, 2 * 1024**2)).hexdigest() for name in SOURCE_FILES}


def run(args, *, root=ROOT, now=None):
    from ariadne.atomic import publish_bytes
    from ariadne.options_samples import convert_samples, _zip_hash, PRIMARY_TERM_CONTEXT
    from ariadne.options_data import canonical, clock
    from ariadne.options_archive import parse_manifest
    from scripts.options_campaign import PauseAuthority, _root_guard
    from scripts.options_market import guard_for, read
    current = clock(now)
    if args.assume_standard_100 is not True:
        raise ValueError('--assume-standard-100 is required; source terms are not independently verified.')
    box, workspace = _root_guard(root), guard_for(args.workspace, root=root)
    for source in (args.options, args.stocks):
        box._path(source)
    if args.options == args.stocks:
        raise ValueError('Two distinct original source ZIPs are required.')
    output = workspace._path(args.output)
    # Detached mode cannot be used to ignore a present owner pause database,
    # including by selecting an alternative --owner-data directory.
    if args.detached_research:
        for relative in dict.fromkeys(('data', args.owner_data)):
            if box._path(relative + '/ariadne.sqlite3').exists():
                raise ValueError('Detached research is only for an isolated checkout without owner state.')
    authority = PauseAuthority(box, args.owner_data, args.detached_research)
    deadline, last_check = time.monotonic() + MAX_SECONDS, 0
    written, used, phase, reserved = {}, 0, 'reservation', False

    def checkpoint(force=False):
        nonlocal last_check
        moment = time.monotonic()
        if moment >= deadline:
            raise TimeoutError('Offline qualification exceeded its600-second budget; no replay.')
        if force or moment - last_check >= .1:
            authority.check()
            last_check = time.monotonic()
        if time.monotonic() >= deadline:
            raise TimeoutError('Qualification authority check exhausted deadline.')

    def publish(name, raw, *, verify=True):
        nonlocal used
        if used + len(raw) > MAX_OUTPUT:
            raise ValueError('Complete conversion output exceeds300MiB; no truncation.')
        path = workspace._path(args.output + '/' + name)
        publish_bytes(path, raw, checkpoint=(lambda: checkpoint(True)) if verify else None)
        written[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        used += len(raw)

    try:
        with workspace._directory(output.parent, create=True):
            output.mkdir(exist_ok=False)
        reserved = True
        with workspace._directory(output), ExitStack() as held:
            checkpoint(True)
            source_hashes = _sources()
            publish('intent.json', canonical({'schema': 1, 'kind': 'offline_KO_sample_qualification',
                'registered_at': current.isoformat(), 'options': args.options, 'stocks': args.stocks,
                'options_sha256': args.options_sha256, 'stocks_sha256': args.stocks_sha256,
                'actual_source_retrieved_at': args.retrieved_at, 'assume_standard100': True,
                'module_sha256': source_hashes, 'owner_authority': authority.scope(),
                'price_repair': False, 'source_execution': False, 'paper_operations': 0, 'backtests_run': 0}))
            inputs = []
            for relative in (args.options, args.stocks):
                path = box._path(relative)
                directory = held.enter_context(box._directory(path.parent))
                stream = held.enter_context(os.fdopen(box._open_read(path, directory), 'rb'))
                info = os.fstat(stream.fileno())
                inputs.append((relative, path, stream, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)))
            phase = 'qualification'
            result = convert_samples(inputs[0][2], inputs[1][2], options_sha256=args.options_sha256,
                stock_sha256=args.stocks_sha256, retrieved_at=args.retrieved_at, standard100_assumption=True,
                as_of=current, checkpoint=checkpoint)

            def verify_sources():
                checkpoint(True)
                if _sources() != source_hashes:
                    raise ValueError('Converter/dependency source changed; no output adoption.')
                for relative, path, stream, identity in inputs:
                    actual = os.fstat(stream.fileno())
                    with box._directory(path.parent) as directory:
                        descriptor = box._open_read(path, directory)
                        try:
                            current_info = os.fstat(descriptor)
                        finally:
                            os.close(descriptor)
                    key = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                    if key(actual) != identity or key(current_info) != identity:
                        raise ValueError('Original source ZIP identity changed; no output adoption.')
                _zip_hash(inputs[0][2], args.options_sha256, 300 * 1024**2, checkpoint)
                _zip_hash(inputs[1][2], args.stocks_sha256, 16 * 1024**2, checkpoint)

            verify_sources()
            phase = 'publication'
            manifest = json.loads(result['manifest'])
            for chunk, metadata in zip(result['chunks'], manifest['chunks']):
                metadata['path'] = args.output + '/' + chunk['name']
                publish(chunk['name'], chunk['raw'])
            manifest_raw = canonical(manifest)
            parse_manifest(manifest_raw)
            publish('reject-issues.jsonl', result['reject_ledger'])
            qualification = {**result['receipt'], 'converter_manifest_sha256': result['receipt']['manifest_sha256'],
                'published_manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
                'module_sha256': source_hashes, 'owner_authority': authority.scope(),
                'primary_context_supplement': {**PRIMARY_TERM_CONTEXT,
                    'issuer_split_history': 'https://investors.coca-colacompany.com/stock-info/splits',
                    'OCC_2022_risks': 'https://www.theocc.com/getmedia/42426879-3a15-4445-b66b-36b2c56ff3fc/march_2022_riskstoc.pdf',
                    'scope': 'Issuer lists its latest split in2012; ordinary cash dividends generally do not adjust standard options. These general records do not authenticate every sampled contract term.'}}
            publish('qualification.json', canonical(qualification))
            verify_sources()
            publish('catalog.json', manifest_raw)

            def final_verify():
                verify_sources()
                for name, item in written.items():
                    checkpoint()
                    raw = read(workspace, args.output + '/' + name, MAX_OUTPUT)
                    if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                        raise ValueError('Published artifact changed before completion.')
                checkpoint(True)

            final_verify()
            completed = {'schema': 1, 'status': 'completed', 'artifacts': written.copy(),
                'output_bytes_before_receipt': used, 'module_sha256': source_hashes,
                'actual_source_retrieved_at': args.retrieved_at, 'owner_authority': authority.scope(),
                'all_sources_and_outputs_reverified': True, 'standard100_independently_verified': False,
                'execution_qualified': False, 'automatic_adoption': False, 'owner_settings_written': False,
                'paper_operations': 0, 'network_calls': 0, 'model_calls': 0, 'backtests_run': 0}
            publish_bytes(output / 'completed.json', canonical(completed), checkpoint=final_verify)
            return completed
    except BaseException as error:
        if reserved:
            failure = {'schema': 1, 'status': 'failed', 'phase': phase, 'error_type': type(error).__name__,
                'reason': str(error)[:1000], 'partial_artifacts': written, 'owner_authority': authority.scope(),
                'replay_allowed': False, 'adopted': False, 'owner_settings_written': False,
                'paper_operations': 0, 'network_calls': 0, 'model_calls': 0, 'backtests_run': 0}
            try:
                with workspace._directory(output):
                    publish_bytes(output / 'failed.json', canonical(failure))
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
        print('Sample qualification stopped: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps(receipt, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
