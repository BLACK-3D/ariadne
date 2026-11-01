"""Review a completed options CSV offline; never selects or funds a paper strategy.

The publisher receipt and CSV both need exact owner-supplied SHA-256 values.
This is a derived comparison of existing backtests, not a new backtest or proof
of source authenticity. Every attempt reserves a new staging directory.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import hashlib
from html import escape
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.options_campaign import PauseAuthority, _canonical, _root_guard, install_offline_guards
from scripts.options_market import read
from scripts.options_universe import SourceLease, _json

MAX_INPUT = 48 * 1024**2
MAX_OUTPUT = 3 * 1024**2
MAX_SECONDS = 180
HASH = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_FILES = (
    "scripts/options_paired_review.py", "ariadne/options_paired_review.py",
    "scripts/options_universe.py", "scripts/options_campaign.py", "scripts/options_market.py",
    "ariadne/atomic.py", "ariadne/tools.py", "ariadne/config.py", "ariadne/backup.py",
    "ariadne/network_policy.py",
)


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--input", required=True, help="Box-relative completed all-results.csv.")
    value.add_argument("--input-sha256", required=True)
    value.add_argument("--publication", required=True, help="Box-relative completed publisher receipt beside the CSV.")
    value.add_argument("--publication-sha256", required=True)
    value.add_argument("--output", required=True, help="New box-relative staging/... folder.")
    value.add_argument("--owner-data", default="data")
    value.add_argument("--detached-research", action="store_true", help="Isolated boxes without an owner database only.")
    return value


def render(report):
    """Escape the complete result as a readable standalone offline document."""
    payload = escape(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    safe=lambda value: escape(str(value))
    sections=[]
    for item in report.get("strata",[]):
        context=item["stratum"]
        title=" / ".join(str(context[key]) for key in ("source_window","mode","profile","preset"))
        rows=[]
        for candidate,value in item["families"].items():
            counts=value["status_counts"]
            cells=(candidate,counts["gain"],counts["loss"],counts["held"],counts["incomplete"],counts["no_trade"],
                value["eligible_traded_net_pnl"]["median"],value["eligible_traded_net_pnl"]["count"],
                value["source_rows_below_registered_warmup"])
            rows.append("<tr>"+"".join("<td>"+safe(v if v is not None else "unavailable")+"</td>" for v in cells)+"</tr>")
        pairs=[]
        for candidate,value in item["recovery_pairs"].items():
            pairs.append("<tr>"+"".join("<td>"+safe(v)+"</td>" for v in (candidate,
                value["eligibility_matrix"]["both_eligible"],value["all_eligible_pairs"]["competitor_higher"],
                value["all_eligible_pairs"]["reference_higher"],value["all_eligible_pairs"]["equal"],
                value["both_traded_pairs"]["competitor_minus_reference_net_pnl"]["count"]))+"</tr>")
        pair_table=("<h3>Paired against raw recovery</h3><p>Higher/lower counts include complete no-trade zeros. "
            "Both-traded counts show how little direct traded evidence can underlie those comparisons.</p>"
            "<div class=scroll><table><tr><th>Peer</th><th>Both eligible</th><th>Peer higher</th><th>Recovery higher</th>"
            "<th>Equal</th><th>Both traded</th></tr>"+"".join(pairs)+"</table></div>") if pairs else ""
        sections.append("<section><h2>"+safe(title)+"</h2><p>"+safe(item["cohorts"])+" matched portfolios; "+
            safe(context["start"])+" through "+safe(context["source_end"])+". "+safe(context["quote_time_basis"])+
            " quote clocks.</p><div class=scroll><table><tr><th>Candidate</th><th>Gain</th><th>Loss</th><th>Held</th>"
            "<th>Incomplete</th><th>No trade</th><th>Median traded net $</th><th>Eligible traded cases</th>"
            "<th>Short warmup</th></tr>"+"".join(rows)+"</table></div>"+pair_table+"</section>")
    return ("<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Ariadne / recovery comparison</title><style>"
        "body{background:#101919;color:#e1e7dc;font:16px/1.6 system-ui;margin:0 auto;padding:36px;max-width:1100px}"
        "h1,h2,h3{font-weight:500;color:#c7dc9b}h2{font-size:19px}p{max-width:80ch}.scroll{overflow:auto}"
        "table{border-collapse:collapse;font-size:13px;width:100%}td,th{text-align:left;padding:9px;border-bottom:1px solid #354540}"
        "section{border-top:1px solid #536047;padding-top:18px;margin-top:36px}pre{white-space:pre-wrap;overflow-wrap:anywhere;"
        "font:13px/1.5 ui-monospace,monospace;border:1px solid #354540;padding:24px}"
        "</style><h1>Recovery calls / paired evidence review</h1>"
        "<p>Existing historical research, with incomplete exits retained as censored observations. "
        "Paired differences use the same portfolio, full window and liquidity profile. "
        "Status counts include context gaps and short warmups; medians exclude those cases. "
        "The complete evidence below records each exclusion and censoring matrix. "
        "No independent accounts or profits are added. No strategy is activated by this report.</p>"
        "<p>These descriptive comparisons do not establish future profitability, historical publication "
        "availability, contract terms or executable fills. The active shared paper allowance stays "
        "$500 per month, with a $100 all-in entry ceiling.</p>"+"".join(sections)+
        "<details><summary>Complete comparison evidence and censoring matrices</summary><pre>"+payload+"</pre></details></html>").encode()


def run(args, *, root=ROOT, now=None):
    from ariadne.atomic import publish_bytes
    from ariadne.options_paired_review import COLUMNS, compare_rows
    box = _root_guard(root)
    if any(type(item) is not str or not HASH.fullmatch(item)
           for item in (args.input_sha256, args.publication_sha256)):
        raise ValueError("Both input files require exact lowercase SHA-256 values.")
    parts = box._parts(args.output)
    if len(parts)<2 or parts[0]!="staging" or "\\" in args.output:
        raise ValueError("Use a new canonical staging/... output directory.")
    if type(args.detached_research) is not bool:
        raise ValueError("Detached research must be an explicit boolean.")
    if args.detached_research and (args.owner_data!="data" or box._path("data/ariadne.sqlite3").exists()):
        raise ValueError("Detached research cannot bypass an existing owner database.")
    source = box._path(args.input)
    publication = box._path(args.publication)
    if source.name!="all-results.csv" or source.parent!=publication.parent or source==publication:
        raise ValueError("Use the named CSV beside its publisher receipt.")
    stamp = datetime.now(timezone.utc) if now is None else now
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Use an aware review timestamp.")
    output=box._path(args.output)
    with box._directory(output.parent, create=True): output.mkdir()
    authority=PauseAuthority(box,args.owner_data,args.detached_research)
    held=ExitStack()
    written, used, phase = {}, 0, "registration"
    started=time.monotonic()

    def check():
        if time.monotonic()-started>MAX_SECONDS: raise TimeoutError("Paired review exceeded180seconds.")
        authority.check()

    def publish(name, raw, *, failure=False, verify=None):
        nonlocal used
        if used+len(raw)>MAX_OUTPUT-(0 if failure else 16384): raise ValueError("Review output exceeds3MiB.")
        path=box._path(args.output+"/"+name)
        with box._directory(path.parent,create=True):
            publish_bytes(path,raw,checkpoint=None if failure else (verify or check))
        if read(box,args.output+"/"+name,len(raw))!=raw: raise ValueError("Review publication changed.")
        used+=len(raw);written[name]={"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}

    try:
        publish("attempt.json",_canonical({"schema":1,"registered_at":stamp.isoformat(),"replay_allowed":False,
            "backtests_run":0,"paper_operations":0}),failure=True)
        check()
        lease=SourceLease(held,check)
        identities={}
        for name in SOURCE_FILES:
            raw=read(box,name,2*1024**2);digest=hashlib.sha256(raw).hexdigest()
            lease.add(box,name,digest,len(raw));identities[name]=digest
        pub_raw=read(box,args.publication,256*1024)
        if hashlib.sha256(pub_raw).hexdigest()!=args.publication_sha256: raise ValueError("Publisher receipt changed.")
        pub=_json(pub_raw)
        if (type(pub) is not dict or type(pub.get("schema")) is not int or pub["schema"]!=1
            or pub.get("status")!="completed" or pub.get("kind")!="derived_options_universe_report_publication"
            or pub.get("research_only") is not True or pub.get("execution_qualified") is not False
            or pub.get("automatic_adoption") is not False or pub.get("all_source_and_output_artifacts_reverified") is not True):
            raise ValueError("A completed, verified research publication is required.")
        artifacts=pub.get("artifacts")
        if type(artifacts) is not dict: raise ValueError("Publisher artifacts must be an object.")
        item=artifacts.get("all-results.csv")
        if (type(item) is not dict or set(item)!={"bytes","sha256"} or type(item["bytes"]) is not int
            or not 1<=item["bytes"]<=MAX_INPUT or item["sha256"]!=args.input_sha256
            or type(pub.get("comparison_rows")) is not int or not 1<=pub["comparison_rows"]<=150000):
            raise ValueError("Publication does not bind the exact bounded CSV.")
        lease.add(box,args.publication,args.publication_sha256,len(pub_raw))
        lease.add(box,args.input,args.input_sha256,item["bytes"])
        raw=read(box,args.input,item["bytes"])
        if hashlib.sha256(raw).hexdigest()!=args.input_sha256: raise ValueError("Input CSV changed.")
        publish("registration.json",_canonical({"schema":1,"kind":"derived_paired_review","registered_at":stamp.isoformat(),
            "input":{"path":args.input,**item},"publication":{"path":args.publication,"sha256":args.publication_sha256},
            "module_sha256":identities,"owner_authority":authority.scope(),
            "not_a_new_backtest":True,"paper_operations":0,"automatic_adoption":False}))
        phase="paired_comparison"
        reader=csv.DictReader(io.StringIO(raw.decode("utf-8"),newline=""),strict=True)
        if tuple(reader.fieldnames or ())!=COLUMNS: raise ValueError("Use the exact completed research CSV header.")
        def checked_rows():
            for index,row in enumerate(reader):
                if index%256==0: check()
                yield row
        report=compare_rows(checked_rows())
        if report["input_rows"]!=pub["comparison_rows"]: raise ValueError("CSV row count disagrees with publication.")
        def verify():
            check();lease.verify()
            for name,identity in written.items():
                content=read(box,args.output+"/"+name,identity["bytes"])
                if hashlib.sha256(content).hexdigest()!=identity["sha256"]: raise ValueError("Review output changed before completion.")
        phase="publication"
        verify();publish("review.json",_canonical(report),verify=verify)
        publish("report.html",render(report),verify=verify)
        completed={"schema":1,"status":"completed","artifacts":written.copy(),"input_sha256":args.input_sha256,
            "publication_sha256":args.publication_sha256,"module_sha256":identities,"input_rows":report["input_rows"],
            "owner_authority":authority.scope(),"backtests_run":0,"paper_operations":0,"network_calls":0,
            "model_calls":0,"automatic_adoption":False,"execution_qualified":False,"all_inputs_outputs_reverified":True}
        publish("completed.json",_canonical(completed),verify=verify)
        return completed
    except BaseException as error:
        publish("failed.json",_canonical({"schema":1,"status":"failed","phase":phase,"error_type":type(error).__name__,
            "reason":str(error)[:1000],"partial_artifacts":written.copy(),"replay_allowed":False,"adopted":False,
            "paper_operations":0}),failure=True)
        raise
    finally:
        try: authority.close()
        finally: held.close()


def main(argv=None):
    install_offline_guards()
    args=parser().parse_args(argv)
    try: receipt=run(args)
    except (ValueError,OSError,TimeoutError,UnicodeError,OverflowError,RecursionError,sqlite3.Error,csv.Error) as error:
        print("Paired review stopped: "+str(error),file=sys.stderr);return 2
    print(json.dumps({key:receipt[key] for key in ("status","input_rows","backtests_run","paper_operations")}))
    return 0


if __name__=="__main__": raise SystemExit(main())
