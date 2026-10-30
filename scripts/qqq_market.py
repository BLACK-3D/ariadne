"""Fixed daily market worker; only the owner-authorized parent enables networking."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("worker",))
    parser.add_argument("--data-dir",required=True)
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--qqq-revision",type=int,required=True)
    parser.add_argument("--internet-revision",type=int,required=True)
    parser.add_argument("--brief-revision",type=int,required=True)
    args=parser.parse_args()
    if os.environ.get("ARIADNE_QQQ_COLLECTOR")!="1" or os.environ.get("ARIADNE_OFFLINE")!="0":
        raise SystemExit("Only an authorized fixed market collector may start this worker.")
    from ariadne.network_policy import install_network_policy
    install_network_policy()
    from ariadne.config import Settings,_allowed_path
    from ariadne.store import Store
    from ariadne.tools import ToolRegistry
    from ariadne.briefing import BriefingService
    from ariadne.qqq_data import collect_bundle,MarketDataUnavailable
    settings=Settings(data_dir=_allowed_path(args.data_dir),workspace=_allowed_path(args.workspace))
    if not settings.data_dir.is_relative_to(ROOT) or not settings.workspace.is_relative_to(ROOT):
        raise SystemExit("Market worker data must remain inside the box.")
    store=Store(settings.data_dir/"ariadne.sqlite3")
    service=BriefingService(settings,store,ToolRegistry(settings))
    def check():
        with service.qqq.database() as db:
            _,cfg=service.qqq._meta(db)
            internet=store.setting("internet_access",None)
            current,brief=service.qqq._policy(db,internet=internet,online=True)
            if cfg["revision"]!=args.qqq_revision or internet["revision"]!=args.internet_revision or brief["revision"]!=args.brief_revision:
                raise ValueError("Market collection permission changed.")
    check()
    try:result=collect_bundle(check=check)
    except (MarketDataUnavailable,OSError,TimeoutError) as error:
        result={"unavailable":True,"reason":str(error)[:1000]}
    check()
    raw=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode()
    if len(raw)>12*1024*1024:raise ValueError("Market worker output exceeds its bound.")
    sys.stdout.buffer.write(raw)

if __name__=="__main__":main()
