"""Render a completed, immutable local experiment as a self-contained HTML report."""
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.options_market import read
from scripts.options_campaign import _root_guard


def _json(raw):
    from scripts.options_market import metadata
    return metadata(raw)


def money(value):
    return 'unresolved' if value is None else f'${Decimal(value):,.2f}'


def render(design, full, selection, development, receipt, *, study_link, oracle=None):
    profiles = tuple(full['results'])
    books = {profile: {row['config']['id']: row for row in full['results'][profile]} for profile in profiles}
    earlier = {profile: {row['config']['id']: row for row in design['results'][profile]} for profile in profiles}
    selected = selection['candidate_id']
    rows, scores = [], []
    for identifier in books[profiles[0]]:
        cases = [books[p][identifier] for p in profiles]
        valid = all(r['complete'] and r['terminal_pnl'] is not None for r in cases)
        worst = min(Decimal(r['terminal_pnl']) for r in cases) if valid else None
        if valid and identifier != 'cash' and all(r['state']['closed_trades'] > 0 for r in cases):
            scores.append((worst, identifier))
        cells = []
        for profile, case in zip(profiles, cases):
            status = ('flat' if case['complete'] else 'HELD' if case['state']['hold'] else 'OPEN')
            cells.append(f'<td>{escape(money(case["terminal_pnl"]))}<small>{status}; {case["state"]["closed_trades"]} closed; fees {money(case["state"]["fees"])}</small></td>')
        rows.append(f'<tr><th>{escape(identifier)}{("<small>DESIGN selection</small>" if identifier == selected else "")}</th>'
                    f'<td>{escape(money(worst))}</td>{"".join(cells)}</tr>')
    scores.sort(key=lambda pair: (-pair[0], pair[1]))
    later = []
    for profile in profiles:
        before, after = earlier[profile][selected], books[profile][selected]
        increment = (str(Decimal(after['terminal_pnl']) - Decimal(before['terminal_pnl']))
                     if before['complete'] and after['complete'] else None)
        later.append(f'<tr><th>{escape(profile)}</th><td>{money(before["terminal_pnl"])}</td><td>{money(after["terminal_pnl"])}</td><td>{money(increment)}</td><td>{after["state"]["closed_trades"]}</td></tr>')
    ranked = ''.join(f'<li><code>{escape(name)}</code>: {money(str(profit))} worst-profile terminal profit</li>' for profit, name in scores[:5])
    if not ranked:
        ranked = '<li>No noncash completed book with a closed trade in both profiles.</li>'
    plot_ids = list(dict.fromkeys([selected] + [name for _, name in scores[:2]]))
    charts = []
    for identifier in plot_ids:
        for profile in profiles:
            case = books[profile][identifier]
            curve = case['curve']
            values = [Decimal(p['trading_pnl']) for p in curve if p['trading_pnl'] is not None] + [Decimal(0)]
            low, high = min(values), max(values)
            span = high - low or Decimal(1)
            segments, current = [], []
            for index, point in enumerate(curve):
                if point['trading_pnl'] is None:
                    if current:
                        segments.append(current)
                        current = []
                    continue
                x = 44 + 632 * index / max(1, len(curve)-1)
                y = 210 - 170 * float((Decimal(point['trading_pnl'])-low)/span)
                current.append(f'{x:.2f},{y:.2f}')
            if current:
                segments.append(current)
            lines = ''.join(f'<polyline points="{" ".join(segment)}"/>' for segment in segments)
            zero = 210 - 170 * float(-low/span)
            charts.append(f'<figure><figcaption>{escape(identifier)}<small>{escape(profile)}; '
                          f'NAV complete: {case["nav_complete"]}; maximum drawdown: {money(case["max_drawdown"])}</small></figcaption>'
                          f'<svg viewBox="0 0 710 245" role="img" aria-label="Historical modeled profit curve; gaps mean unknown NAV">'
                          f'<line x1="44" x2="676" y1="{zero:.2f}" y2="{zero:.2f}"/>{lines}'
                          f'<text x="44" y="20">{escape(money(str(high)))}</text><text x="44" y="238">{escape(money(str(low)))}</text>'
                          f'<text x="535" y="238">May → October 2026</text></svg></figure>')
    oracle_note = ''
    if oracle is not None:
        oracle_note = '<p>The separate backwards-looking audit found 142 overlapping opportunities; 41 had no eligible later exit. Its largest future-selected window was +$5,239.50 on $990.50 commitment. It chose the contract and exit using future knowledge: <strong>an oracle illustration, not a strategy return, win rate or scalable profit estimate.</strong> None of its eight patterns demonstrated robust later evidence.</p>'
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ariadne · Experimental strategy research</title><style>
:root{{color-scheme:dark;font-family:system-ui,sans-serif;background:#0b1113;color:#e5eee8}}body{{margin:auto;max-width:1280px;padding:36px 24px 80px}}h1{{font-size:clamp(2rem,5vw,3.8rem);line-height:1.03;max-width:900px}}h2{{margin-top:38px;font-size:1.35rem}}p,li{{line-height:1.6;max-width:1000px}}.tag{{letter-spacing:.17em;color:#b6d8a0;font-size:.8rem}}.callout{{border:1px solid #52694e;padding:18px 22px;background:#141e19}}small{{display:block;color:#a4b6ad;font-size:.76rem;font-weight:400;margin-top:5px}}code{{overflow-wrap:anywhere;color:#cce2ac}}a{{color:#cce2ac}}table{{border-collapse:collapse;width:100%;font-size:.88rem}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #293735;vertical-align:top}}th{{overflow-wrap:anywhere}}.scroll{{overflow:auto}}figure{{margin:24px 0;background:#10191b;padding:18px}}figcaption{{font-weight:650}}svg{{width:100%;max-height:320px}}svg polyline{{stroke:#b9da91;stroke-width:2;fill:none}}svg line{{stroke:#4c5c56}}svg text{{fill:#9daea5;font-size:11px}}details{{margin-top:24px}}summary{{cursor:pointer;padding:14px;background:#1a2827}}.muted{{color:#a4b6ad}}
</style><div class="tag">ARIADNE / LOCAL RESEARCH / NO LIVE PROMOTION</div>
<h1>New rules. Every attempted case. Actual quote constraints.</h1>
<div class="callout"><strong>DESIGN selection: <code>{escape(selected)}</code></strong><p>Selected from the earlier availability-clock segment before evaluating the full continuous path. The later period was already seen in previous work: it is a development check, not an untouched holdout. No result proves future profitability or changes the owner’s paper account.</p></div>
<h2>What was built</h2><p>Eight fixed experimental price patterns plus stress-filtered puts, raw breakdown puts, recovery calls and cash. The 89 registered cases combine 2×/5× take-profit triggers, 5/10-session holding ceilings and one-contract/budget-sized entries. Both historical timing assumptions are retained: 178 counterfactual paths per phase, not independent observations or pooled capital.</p>
<p>Each path has one $25,000 seed, no top-ups, one funded position, $25,000 monthly gross commitment and entries capped by min($1,000, 4% of flat equity, cash, remaining gross budget), including $0.65 per contract per side. Actual ask buys and actual bid sells use recorded sizes. Partial liquidation is a new research rule; current production contracts remain unchanged. Unknown full-position NAV is shown as a gap, and risk statistics then remain unestimated.</p>
<h2>Chosen earlier, evaluated later</h2><div class="scroll"><table><thead><tr><th>Clock profile</th><th>DESIGN profit</th><th>Full terminal profit</th><th>Later cash increment</th><th>Full closed trades</th></tr></thead><tbody>{''.join(later)}</tbody></table></div>
<p class="muted">DESIGN cutoff: July 31, 2026, end of New York day, using available_at. Full source: May 4–October 29; delayed final availability: October 30, 09:35 New York. A cash increment is reported only if both endpoints are flat and complete. No reset or second funding occurs at the split.</p>
<h2>Full-history leaders — post hoc diagnostic</h2><ol>{ranked}</ol><p>These ranks inspect the full history and cannot replace the frozen earlier selection. Positive terminal returns require complete flat books in both profiles. Losses, holds and no-trade cases remain in the complete table below. No annualized return, Sharpe ratio or statistical edge is claimed from this small sample.</p>
{''.join(charts)}<h2>Working backwards</h2>{oracle_note}
<p>Hindsight is useful for asking which causal features to investigate. Future-selected contracts, exits, overlapping opportunities and excluded unliquidatable positions cannot establish an executable strategy. The first genuine unseen check must use future prospective observations under a separately accepted strategy contract.</p>
<h2>Evidence limits</h2><p>Only 125 sessions of KO monthly-expiry partial chains, 4,000 quotes and 16 strikes per day were captured. Twelve months of usable options history and a broad stock universe are still unavailable. Quote observation/publication times, standard deliverables and price basis remain assumptions; historical sizes do not guarantee future fills. The separate underlying reference failed a global calendar check and price-equivalence checks; only the fixed 125-session projection was admitted. These source defects are not repaired or hidden by profitable model output.</p>
<p>This finite local run uses no model, paid API, recurring service or new market request. Measured run details and source hashes are in its receipt; development effort is separate from recurring product cost.</p>
<details><summary>All registered configurations — both clocks, including failures and no trades</summary><div class="scroll"><table><thead><tr><th>Configuration</th><th>Worse full terminal profit</th>{''.join('<th>'+escape(p)+'</th>' for p in profiles)}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></details>
<h2>Receipts and primary references</h2><ul><li><a href="{escape(study_link)}/completed.json">Completed research receipt</a></li><li><a href="{escape(study_link)}/registration.json">Registered rules, sources and finite limits</a></li><li><a href="{escape(study_link)}/selection.json">Frozen DESIGN selection</a></li><li><a href="{escape(study_link)}/design-results.json">All DESIGN results</a></li><li><a href="{escape(study_link)}/full-results.json">All full results and actual simulated fills</a></li><li><a href="https://www.optionseducation.org/referencelibrary/faq/trade-entry-execution">OIC: displayed size and execution</a></li><li><a href="https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf">Bailey et al.: probability of backtest overfitting</a></li></ul><p class="muted">Research only · No brokerage orders · No account migration · No automatic promotion</p></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--oracle', default='')
    args = parser.parse_args(argv)
    box = _root_guard(ROOT)
    if not args.study.startswith('staging/') or not args.output.startswith('data/workspace/reports/'):
        raise ValueError('Use a completed private study and a new local report folder.')
    receipt_raw = read(box, args.study+'/completed.json', 1024*1024)
    receipt = _json(receipt_raw)
    if receipt.get('status') != 'completed' or receipt.get('source_stable') is not True:
        raise ValueError('A completed source-stable study is required.')
    bindings = {'completed.json': {'sha256': hashlib.sha256(receipt_raw).hexdigest(), 'bytes': len(receipt_raw)}}
    for name, item in receipt['artifacts'].items():
        raw = read(box, args.study+'/'+name, 32*1024**2)
        if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Completed artifact changed.')
        bindings[name] = item
    values = [_json(read(box, args.study+'/'+name, 32*1024**2)) for name in
              ('design-results.json', 'full-results.json', 'selection.json', 'selected-development.json')]
    oracle = _json(read(box, args.oracle, 8*1024**2)) if args.oracle else None
    if args.oracle:
        raw = read(box, args.oracle, 8*1024**2)
        bindings[args.oracle] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        if bindings[args.oracle]['sha256'] != 'fa22de3240d0bf89f9809acc65364a352e8f4b997ba8358965caceb24ceba729':
            raise ValueError('Use the registered immutable oracle diagnostic, not replacement outcomes.')
    output = box._path(args.output)
    with box._directory(output.parent, create=True):
        output.mkdir()
    relative = os.path.relpath(box._path(args.study), output).replace('\\', '/')
    content = render(*values, receipt, study_link=relative, oracle=oracle).encode('utf-8')
    from ariadne.atomic import publish_bytes
    with box._directory(output):
        publish_bytes(output/'report.html', content)
        result = {'schema': 1, 'status': 'completed', 'created_at': datetime.now(timezone.utc).isoformat(),
                  'study': args.study, 'inputs': bindings, 'report': {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()},
                  'owner_operations': 0, 'network_calls': 0, 'model_calls': 0}
        publish_bytes(output/'completed.json', json.dumps(result, sort_keys=True, indent=2).encode())
    print(json.dumps({'report': str(output/'report.html'), **result['report']}))


if __name__ == '__main__':
    main()
