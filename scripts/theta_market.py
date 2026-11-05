"""Owner-managed Theta terminal bridge; no installs, credentials or financial writes."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import alpaca_market as core


def parser():
    return core.parser(provider='theta')


def run(args,*,root=ROOT,client_factory=None,now=None):
    return core.run(args,root=root,client_factory=client_factory,now=now,provider='theta')


def main(argv=None):
    return core.main(argv,provider='theta')


if __name__=='__main__':
    raise SystemExit(main())
