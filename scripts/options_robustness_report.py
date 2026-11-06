"""Render a completed finite options robustness audit as a standalone offline report."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.options_campaign import _canonical,_root_guard,install_offline_guards
from scripts.options_market import guard_for,read,metadata


def money(value):
    if value is None:return 'Unavailable'
    n=Decimal(value)
    return ('−' if n<0 else '+' if n>0 else '')+'$'+format(n.copy_abs(),',.2f')


def label(identifier):
    return identifier.replace('__',' / ').replace('_',' ')


def render(result,receipt):
    if (type(result) is not dict or result.get('kind')!='fixed_options_development_robustness_audit'
            or result.get('case_count')!=712 or len(result.get('cases',[]))!=712
            or result.get('configuration_count')!=89 or len(result.get('aggregates',[]))!=89
            or result.get('automatic_adoption') is not False or result.get('independent_holdout') is not False
            or result.get('baseline_reports_exact_matches')!=178
            or receipt.get('kind')!='fixed_KO_robustness_audit' or receipt.get('status')!='completed'
            or receipt.get('source_stable') is not True
            or any(type(receipt.get(key)) is not int or receipt[key]!=0
                   for key in ('network_calls','model_calls','paper_operations'))):
        raise ValueError('A completed, source-stable712-case development audit is required.')
    def e(value):return escape(str(value),quote=True)
    aggregates={row['config']['id']:row for row in result['aggregates']}
    leaders=result['all_scenario_profit_leaders'][:8]
    def table(head,rows,identifier):
        return '<div class="scroll"><table id="'+identifier+'"><thead><tr>'+''.join('<th scope="col">'+e(t)+'</th>' for t in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+e(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table></div>'
    ranked=[]
    for identifier in leaders:
        a=aggregates[identifier]
        ranked.append([label(identifier),money(a['recorded_base_worst_terminal_pnl']),money(a['worst_terminal_pnl']),
            a['minimum_closed_episodes'],'Known' if a['all_nav_known'] else 'Gaps / drawdown unavailable',
            money(a['worst_terminal_pnl_without_largest_2_positive']), 'Pass' if identifier in result.get('intent_screen_ids',[]) else 'Insufficient'])
    chart=[];values=[Decimal(aggregates[i]['worst_terminal_pnl']) for i in leaders];maximum=max(values,default=Decimal(1))
    for n,(identifier,value) in enumerate(zip(leaders,values)):
        y=35+n*57;width=float(value/maximum*500) if maximum>0 else 0
        chart.append('<text x="8" y="'+str(y)+'">'+e(label(identifier))+'</text><rect x="8" y="'+str(y+8)+'" width="'+str(width)+'" height="12" rx="3"/><text x="'+str(18+width)+'" y="'+str(y+19)+'">'+e(money(str(value)))+'</text>')
    svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 740 '+str(max(90,55+57*len(leaders)))+'" role="img" aria-label="Worst net profit across both clock assumptions and all four scenarios; each bar is a separate counterfactual"><style>text{fill:#d4e1da;font:12px system-ui}rect{fill:#b4ce6a}</style>'+''.join(chart)+'</svg>'
    allrows=[]
    for a in result['aggregates']:
        allrows.append([label(a['config']['id']),money(a['recorded_base_worst_terminal_pnl']),money(a['worst_terminal_pnl']),
            a['minimum_closed_episodes'],a['minimum_closed_exit_months'],'Yes' if a['all_cases_complete'] else 'Held',
            'Yes' if a['all_nav_known'] else 'No',money(a['minimum_conservative_equity_floor']),
            'Pass' if a['loss_budget_screen_passed'] else 'Breach or unverifiable',
            'Pass' if a['config']['id'] in result.get('intent_screen_ids',[]) else 'No'])
    cases=[]
    for c in result['cases']:
        cases.append([label(c['config_id']),c['clock_profile'],c['scenario'],c['status'],money(c['terminal_pnl']),
            c['entries'],c['closed_episodes'],money(c['fees']),str(c['known_nav_frames'])+'/'+str(c['source_frames']),
            money(c['max_drawdown_dollars']),money(c['terminal_pnl_without_largest_1_positive']),
            money(c['terminal_pnl_without_largest_2_positive']),c['hold_code'] or 'None',c['engine_report_sha256']])
    headline='No strategy meets the registered evidence screen.' if not result.get('intent_screen_ids') else 'Research candidates pass a descriptive screen; independent validation is still absent.'
    css='''body{margin:0;background:#0b1212;color:#e1e9e3;font:15px/1.6 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:40px 30px 80px}h1{font-size:clamp(32px,5vw,60px);line-height:1.1;letter-spacing:-2px;max-width:1000px}h2{margin-top:42px;font-size:22px}.eyebrow{font:12px monospace;color:#b8cb82;letter-spacing:3px}.deck{max-width:880px;color:#acbfb7}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:25px 0}.card,.note{border:1px solid #34463f;background:#111c19;border-radius:8px;padding:18px}.card strong{display:block;font-size:26px;color:#bed681}.card span{font-size:12px;color:#a7bbb0}.note{border-left:3px solid #c5d28c}a{color:#c6db97}svg{width:min(100%,850px);display:block}.scroll{overflow:auto;border:1px solid #30463b;border-radius:6px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{text-align:left;padding:11px 13px;border-bottom:1px solid #27382f;vertical-align:top}th{background:#1a2822;color:#d0dfaf;white-space:nowrap}td:first-child{min-width:220px}td:last-child{overflow-wrap:anywhere}details{margin:24px 0}summary{cursor:pointer;color:#c9dd9b}code{overflow-wrap:anywhere}footer{margin-top:40px;color:#9eb1a7;font-size:12px}@media print{body{background:white;color:#15221b}main{padding:10px}.card,.note,th{background:#f1f4ec;color:#13231b}.scroll{overflow:visible}table{font-size:9px}svg text{fill:#112319}details{display:block}}'''
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ariadne — Options robustness research</title><style>'+css+'</style><main><p class="eyebrow">ARIADNE / STRATEGY LAB / DEVELOPMENT EVIDENCE</p><h1>Profit is the first question.<br>Survival is the second.</h1><p class="deck">KO options, May 4–October 29, 2026. All 125 captured sessions are inside the rolling twelve-month window. This is roughly six months on one partially observed name—not twelve months or a multi-stock validation.</p><div class="cards"><div class="card"><strong>712</strong><span>Recorded research paths; holds retained</span></div><div class="card"><strong>89 × 2 × 4</strong><span>Configurations × clocks × cost/depth views</span></div><div class="card"><strong>$25,000</strong><span>Separate bankroll per path; no top-ups</span></div><div class="card"><strong>'+e(len(result.get('intent_screen_ids',[])))+'</strong><span>Registered descriptive-screen qualifiers</span></div></div><div class="note"><strong>'+e(headline)+'</strong><p>All source periods have already been inspected. This run tests robustness of development ideas; it creates no untouched holdout, paper-account promotion, live orders or profit guarantee. The earlier frozen chronological selection remains cash.</p></div><h2>Leaders that stay profitable in all eight assumptions</h2><p>Ranked by the worst terminal net P&amp;L across both availability clocks and all four quote/depth views. Held books are unranked. Bars are separate accounts: never add their profits.</p>'+svg+table(['Configuration','Base: weaker clock','Worst of all eight','Minimum closed positions','Interim NAV','Without best two wins: worst','Evidence screen'],ranked,'leaders')+'<h2>What was actually tested</h2><p>Eleven fixed signals cover stress-filtered puts, raw breakdown puts, recovery calls, moving-average reclaims, momentum, pullbacks, failed bounces, breakouts, oversold rebounds and volatile downside. Each combines a 2× or 5× price target, a five- or ten-session hold ceiling and one-contract or budget-limited sizing. Cash is the control.</p><p>Recorded-base inputs are compared with a hypothetical one-cent lower bid / one-cent higher ask, floor-halved known bid/ask sizes, and both stresses together. Unknown sizes remain unknown; zero depth cannot fill. All 178 base ledgers exactly match the previous completed experiment.</p><p>Signals use prior completed observations; orders fill on a later observed session at supplied ask for buys and bid for sells. The unchanged engine applies $0.65 fees per side, partial liquidations, expiry/deadline holds and the existing minimum of $1,000, 4% of cash, available cash and remaining monthly commitment. A historical quote is not a guaranteed fill.</p><h2>Reliability checks and current limits</h2><p>The registered screen requires at least 30 fully closed positions in three exit months, all interim NAV known, no observed $20k-loss breach or unverifiable floor, and positive terminal P&amp;L in every clock/stress case. These are coverage checks, not proof of an edge. Dropping the largest wins is arithmetic sensitivity—not a capital-trajectory replay.</p><p>Drawdown is unavailable whenever liquidation-sized NAV is missing; it is never reported as zero by default. The conservative floor subtracts reserved exit fees from cash. This observed-frame analysis does not activate or guarantee a $20k loss stop in the old engine.</p><p>The daily source lacks verified quote-event/publication clocks, revision vintages and standard deliverables. The two timing assumptions are hypothetical. Option chains contain one selected expiry and sixteen strikes per side. Underlying-reference and adjustment-basis caveats remain; original files are preserved.</p><p>The saved Theta Ford capture has 111 observations of one January 2027 put, 72–231 days to expiry, no matching calls or underlying daily series. Zero rows meet the current 7–21-day/same-month policy. It is useful capture evidence; it cannot fairly produce this strategy comparison or activate paper trading.</p><details><summary>All 89 configurations</summary>'+table(['Configuration','Base weaker clock','Worst of eight','Min. closed','Min. exit months','All flat','All NAV known','Min. conservative floor','Loss screen','Evidence screen'],allrows,'all-configurations')+'</details><details><summary>All 712 case summaries, including losing and held paths</summary>'+table(['Configuration','Clock','Scenario','State','Terminal net P&amp;L','Entries','Closed','Fees','Known NAV frames','Max. drawdown $','P&amp;L minus best win','P&amp;L minus best two','Hold','Full ledger SHA-256'],cases,'all-cases')+'</details><h2>Next experiment</h2><p>Freeze the leading mean-reclaim candidate and a simpler recovery comparator before collecting new matched option and underlying observations. Require actual timestamps, spreads and liquidation depth. Keep old outcomes in development evidence and evaluate genuinely new observations separately before proposing a paper-policy change. No data subscription or recurring model/API call is used by this saved-input audit; measured run time is '+e(receipt.get('seconds'))+' seconds. Energy cost is unmeasured.</p><p><a href="results.json">All machine-readable results</a> · <a href="study-completed.json">Original completed study receipt</a></p><footer>Research only. Study registration '+e(receipt.get('registered_at'))+' · Source-stable: true · Independent holdout: false · Network/model/paper operations: 0. This report contains private local research and is excluded from public source exports.</footer></main></html>'


def run(workspace,expected_hash,output,*,root=ROOT):
    from ariadne.atomic import publish_bytes
    box=_root_guard(root);source=guard_for(workspace,root=box.root)
    receipt_raw=read(source,'completed.json',256*1024)
    if hashlib.sha256(receipt_raw).hexdigest()!=expected_hash:raise ValueError('Completed study hash changed.')
    receipt=metadata(receipt_raw)
    if receipt.get('status')!='completed' or receipt.get('source_stable') is not True:raise ValueError('Incomplete study.')
    items=receipt.get('artifacts')
    if type(items) is not dict or not 1<=len(items)<=750:raise ValueError('Bounded full inventory required.')
    total=0;seen=set();results=None
    for name,item in items.items():
        source._parts(name)
        if ('\\' in name or name.casefold() in seen or type(item) is not dict or set(item)!={'bytes','sha256'}
                or type(item['bytes']) is not int or not 1<=item['bytes']<=32*1024*1024):raise ValueError('Invalid artifact inventory.')
        seen.add(name.casefold());total+=item['bytes']
        if total>32*1024*1024:raise ValueError('Study exceeds32MiB.')
        raw=read(source,name,item['bytes'])
        if len(raw)!=item['bytes'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Study artifact changed.')
        if name=='results.json':results=raw
    if results is None:raise ValueError('Results missing.')
    html=render(metadata(results),receipt).encode('utf-8')
    if len(html)>4*1024*1024:raise ValueError('Report exceeds4MiB.')
    parts=box._parts(output)
    if len(parts)!=4 or parts[:3]!=('data','workspace','reports') or '\\' in output:
        raise ValueError('Use a new data/workspace/reports/name folder.')
    destination=box._path(output)
    with box._directory(destination.parent,create=True):destination.mkdir()
    artifacts={}
    for name,raw in (('report.html',html),('results.json',results),('study-completed.json',receipt_raw)):
        path=box._path(output+'/'+name)
        with box._directory(path.parent):publish_bytes(path,raw)
        if read(box,output+'/'+name,len(raw))!=raw:raise ValueError('Published report changed.')
        artifacts[name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    result={'schema':1,'status':'completed','kind':'options_robustness_report','source_completed_sha256':expected_hash,
        'artifacts':artifacts,'rendered_at':datetime.now(timezone.utc).isoformat(),'network_calls':0,'model_calls':0,
        'automatic_adoption':False,'source_only_report':True}
    path=box._path(output+'/completed.json')
    with box._directory(path.parent):publish_bytes(path,_canonical(result))
    return result


def main(argv=None):
    install_offline_guards()
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',required=True)
    p.add_argument('--completed-sha256',required=True);p.add_argument('--output',required=True)
    a=p.parse_args(argv)
    try:value=run(a.workspace,a.completed_sha256,a.output)
    except Exception:
        print('Report held: inspect the completed study and choose a new output folder. No automatic retry.',file=sys.stderr);return 2
    print(json.dumps(value,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
