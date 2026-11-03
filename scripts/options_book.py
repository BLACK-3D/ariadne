"""Offline saved options-book admission review. Never an account or broker client."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_INPUT = 4 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
MAX_BATCH = 256

def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('action', choices=('review', 'audit-legacy'))
    value.add_argument('--workspace', default='data/workspace')
    value.add_argument('--input', required=True, help='Workspace-relative imported JSON; never a URL.')
    value.add_argument('--output', required=True, help='New workspace-relative reports/<name> directory.')
    return value

def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('utf-8')

def _source_hashes():
    from scripts.options_market import read
    from ariadne.tools import ToolRegistry
    from ariadne.config import _allowed_path
    guard = object.__new__(ToolRegistry)
    guard.root = _allowed_path(ROOT)
    names = ('scripts/options_book.py', 'scripts/options_market.py', 'ariadne/options_liquidity.py', 'ariadne/qqq_data.py', 'ariadne/briefing.py', 'ariadne/tools.py', 'ariadne/config.py', 'ariadne/atomic.py')
    return {name: hashlib.sha256(read(guard, name, 1024 * 1024)).hexdigest() for name in names}

def _legacy(data):
    if type(data) is not dict or type(data.get('snapshots')) is not list or not 1 <= len(data['snapshots']) <= 10000:
        raise ValueError('Legacy audit requires a bounded saved dataset with snapshots.')
    count = {'snapshots': len(data['snapshots']), 'quotes': 0, 'snapshots_without_valid_declared_event_clock': 0, 'snapshots_with_valid_declared_event_clock': 0,
             'quotes_with_missing_bid_size': 0, 'quotes_with_missing_ask_size': 0, 'quotes_with_zero_bid_size': 0, 'quotes_with_zero_ask_size': 0,
             'quotes_with_malformed_bid_size': 0, 'quotes_with_malformed_ask_size': 0,
             'quotes_with_numeric_volume': 0, 'quotes_with_numeric_open_interest': 0}
    for row in data['snapshots']:
        if type(row) is not dict or type(row.get('quotes')) is not list:
            raise ValueError('Legacy snapshots need a quote list.')
        from ariadne.options_liquidity import _clock
        try:
            _clock(row.get('quote_observed_at'))
        except ValueError:
            count['snapshots_without_valid_declared_event_clock'] += 1
        else:
            count['snapshots_with_valid_declared_event_clock'] += 1
        for quote in row['quotes']:
            if type(quote) is not dict: raise ValueError('Legacy quote must be an object.')
            count['quotes'] += 1
            if count['quotes'] > 20000: raise ValueError('Legacy quote count exceeds its bound.')
            for side in ('bid', 'ask'):
                size = quote.get(side + '_size')
                if size is None:
                    count['quotes_with_missing_' + side + '_size'] += 1
                elif type(size) is not int or size < 0:
                    count['quotes_with_malformed_' + side + '_size'] += 1
                elif size == 0:
                    count['quotes_with_zero_' + side + '_size'] += 1
            for key in ('volume', 'open_interest'):
                if type(quote.get(key)) is int and quote[key] >= 0: count['quotes_with_numeric_' + key] += 1
    source = data.get('source') if type(data.get('source')) is dict else {}
    return {'schema': 1, 'kind': 'legacy_dataset_incompatibility_audit', 'status': 'completed',
            'strict_book_compatible': False, 'converted_to_book': False, 'fill_model_ran': False,
            'paper_proposals': 0, 'counts': count,
            'reported_source_time_basis': source.get('quote_time_basis') if type(source.get('quote_time_basis')) is str else None,
            'reason_codes': ['legacy_schema_not_a_strict_book', 'no_authenticated_quote_event_and_publication_chain',
                             'no_admitted_dated_contract_volume_open_interest_or_twenty_session_activity'],
            'limitations': ['Numeric volume or open interest may be present; undated values are not admissible timed metrics.',
                            'observed_at or received_at cannot be substituted for an actual quote-event clock.',
                            'This audit does not validate or repair the old engine/source or search another feed.'],
            'provider_authenticated': False, 'actual_event_time_authenticated': False, 'execution_confirmed': False, 'portfolio_controls_checked': False}

def render(result):
    if result['kind'] == 'legacy_dataset_incompatibility_audit':
        headline = 'Saved history cannot become a fresh book.'
        body = '<p>Strict-book admission: <strong>REFUSED</strong>. No quote was converted or filled.</p><dl>' + ''.join(
            '<dt>' + escape(key.replace('_', ' ')) + '</dt><dd>' + str(value) + '</dd>' for key, value in result['counts'].items()) + '</dl>'
        body += '<ul>' + ''.join('<li>' + escape(text) + '</li>' for text in result['limitations']) + '</ul>'
    else:
        headline = 'Displayed liquidity before a paper proposal.'
        body = '<p>Each row evaluates only the supplied recorded book and decision clock. This is no broker execution report.</p><div class="scroll"><table><thead><tr><th>Request</th><th>Side</th><th>Status</th><th>Quantity</th><th>Book price</th><th>Reasons</th></tr></thead><tbody>'
        for row in result['evaluation']['proposals']:
            body += '<tr>' + ''.join('<td>' + escape(str(value)) + '</td>' for value in [row['request_id'], row['side'], row['status'],
                str(row['proposed_quantity']) + '/' + str(row['requested_quantity']), row.get('quoted_price'), ', '.join(row['reason_codes'])]) + '</tr>'
        body += '</tbody></table></div><p>Buy proposals use the ask; sell proposals use the bid. The price protection supplied with a request is never changed. Rejected requests remain rejected.</p>'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\';style-src \'unsafe-inline\';base-uri \'none\';form-action \'none\'"><title>Ariadne / Options book review</title><style>body{background:#0c120e;color:#e3e8dc;font:16px/1.6 system-ui;margin:0;padding:clamp(20px,4vw,54px)}main{max-width:1080px;margin:auto}h1{font-size:clamp(30px,5vw,54px);line-height:1.1}.tag,a{color:#c3db92}.tag{font:12px monospace;letter-spacing:2px}.note{padding:18px;border-left:3px solid #b8cf8b;background:#172016}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:12px;border-bottom:1px solid #354533;overflow-wrap:anywhere}dt{color:#a7b59e}dd{margin:0 0 12px}</style><main><p class="tag">ARIADNE / RECORDED OPTIONS BOOK / LOCAL RESEARCH</p><h1>' + headline + '</h1><div class="note">Paper proposals only. Source authenticity, actual execution, account cash, inventory, approvals and risk limits are not established by this gate.</div>' + body + '<p><a href="review.json">Complete checked review</a>  |  <a href="completed.json">Input and output fingerprints</a></p><p>This offline tool has no broker, collector, order, model, account-write or recurring worker interface. Existing paper accounts and holds are unchanged.</p></main></html>'

def run(args, *, root=ROOT, now=None):
    from scripts.options_market import guard_for, read, metadata
    from ariadne.atomic import publish_bytes
    clock = datetime.now(timezone.utc) if now is None else now
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None: raise ValueError('Use an aware registration clock.')
    clock = clock.astimezone(timezone.utc)
    guard = guard_for(args.workspace, root=root)
    if not args.output.startswith('reports/') or len(args.output) > 240: raise ValueError('Use a new reports/<name> output directory.')
    destination = guard._path(args.output)
    if destination.exists(): raise FileExistsError('Prior output exists; inspect it rather than replay or overwrite.')
    source_hashes = _source_hashes()
    raw = read(guard, args.input, MAX_INPUT)
    data = metadata(raw)
    if args.action == 'review':
        from ariadne.options_liquidity import evaluate_batch, RULE, RULE_SHA256
        if type(data) is not dict or set(data) != {'schema', 'books', 'requests'} or type(data['schema']) is not int or data['schema'] != 1:
            raise ValueError('Use exactly schema:1, books and requests.')
        if any(type(data[name]) is not list or not 1 <= len(data[name]) <= MAX_BATCH for name in ('books', 'requests')):
            raise ValueError('Use one to 256 books and requests.')
        for request in data['requests']:
            if type(request) is not dict or type(request.get('decision_at')) is not str: raise ValueError('Request decision clock is required.')
            decision = datetime.fromisoformat(request['decision_at'].replace('Z', '+00:00'))
            if decision.tzinfo is None or decision.utcoffset() is None or decision > clock: raise ValueError('No future or naive decision clocks.')
        evaluation = evaluate_batch(data['books'], data['requests'])
        result = {'schema': 1, 'kind': 'recorded_book_replay_admission', 'status': 'completed', 'registered_at': clock.isoformat(),
                  'replay_at_supplied_decision_times': True, 'current_live_market_certified': False, 'rule': RULE, 'rule_sha256': RULE_SHA256,
                  'evaluation': evaluation, 'provider_authenticated': False, 'execution_confirmed': False, 'portfolio_controls_checked': False}
    elif args.action == 'audit-legacy':
        result = _legacy(data)
        result['registered_at'] = clock.isoformat()
    else: raise ValueError('Unsupported options-book review action.')
    if read(guard, args.input, MAX_INPUT) != raw or _source_hashes() != source_hashes: raise ValueError('Saved source or review code changed during review.')
    encoded = json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False).encode('utf-8')
    page = render(result).encode('utf-8')
    if len(encoded) + len(page) > MAX_OUTPUT: raise ValueError('Bounded report output exceeded.')
    paths = {'review.json': encoded, 'report.html': page}
    receipt = {'schema': 1, 'status': 'completed', 'action': args.action, 'registered_at': clock.isoformat(),
               'input': {'path': args.input, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()},
               'artifacts': {name: {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()} for name, value in paths.items()},
               'owner_operations': 0, 'network_calls': 0, 'model_calls': 0, 'brokerage_orders': 0, 'paper_ledger_writes': 0, 'source_sha256': source_hashes,
               'partial_publication_policy': 'Any existing output is retained; do not replay or overwrite.'}
    with guard._directory(destination.parent, create=True):
        destination.mkdir()
        with guard._directory(destination):
            publish_bytes(destination / 'attempt.json', _canonical({'schema': 1, 'action': args.action, 'input_sha256': receipt['input']['sha256']}))
            for name, value in paths.items(): publish_bytes(destination / name, value)
            publish_bytes(destination / 'completed.json', _canonical(receipt))
    return receipt

def main(argv=None):
    import os
    os.environ['ARIADNE_OFFLINE'] = '1'
    from ariadne.network_policy import install_network_policy
    install_network_policy()
    args = parser().parse_args(argv)
    try: receipt = run(args)
    except (ValueError, OSError, UnicodeError, RecursionError, OverflowError) as error:
        print('Options book review refused: ' + type(error).__name__, file=sys.stderr)
        return 2
    print(json.dumps({'status': receipt['status'], 'action': args.action, 'output': args.output, 'input_sha256': receipt['input']['sha256']}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
