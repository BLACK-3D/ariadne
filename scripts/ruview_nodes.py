"""Offline XIAO firmware-kit verifier/preparer; prints plans and never flashes."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.ruview_setup import flash_plan, prepare, status, verify_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    verify = commands.add_parser("verify", help="Read and verify a sealed extracted firmware directory")
    verify.add_argument("bundle", help="Directory inside this Ariadne folder")
    create = commands.add_parser("prepare", help="Create one exclusive, offline three-node kit; no hardware action")
    create.add_argument("bundle", help="Directory inside this Ariadne folder")
    create.add_argument("--name", default="xiao-three-node")
    saved = commands.add_parser("status", help="Read and verify an existing kit without replay")
    saved.add_argument("--name", default="xiao-three-node")
    plan = commands.add_parser("plan", help="Print inert manual commands only; never open a COM port")
    plan.add_argument("--name", default="xiao-three-node")
    plan.add_argument("--node", type=int, required=True, choices=(1, 2, 3))
    plan.add_argument("--port", required=True, help="Owner-confirmed uppercase COM port, e.g. COM5")
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            result = verify_bundle(args.bundle, root=ROOT)
        elif args.action == "prepare":
            result = prepare(args.bundle, args.name, root=ROOT)
        elif args.action == "status":
            result = status(args.name, root=ROOT)
        else:
            result = flash_plan(args.name, args.node, args.port, root=ROOT)
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "held", "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, ValueError) else "Local setup files could not be read or written.",
                          "automatic_retry": False}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
