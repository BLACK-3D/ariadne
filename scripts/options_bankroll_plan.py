"""Print an offline25k capital proposal or hypothetical sizing; no side effects."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "size"))
    parser.add_argument("--ask")
    parser.add_argument("--ask-size", type=int)
    parser.add_argument("--cash", default="25000.00")
    parser.add_argument("--equity", default="25000.00")
    parser.add_argument("--monthly-committed", default="0.00")
    args = parser.parse_args(argv)
    from ariadne.options_bankroll_plan import losing_streaks, plan, size
    try:
        if args.action == "plan":
            if args.ask is not None or args.ask_size is not None or (args.cash, args.equity, args.monthly_committed) != ("25000.00", "25000.00", "0.00"):
                raise ValueError("The plan action does not accept sizing inputs.")
            result = {**plan(), "idealized_loss_illustrations": losing_streaks()}
        else:
            if args.ask is None or args.ask_size is None:
                raise ValueError("Sizing requires both --ask and --ask-size; inputs are hypothetical.")
            result = size(ask=args.ask, ask_size=args.ask_size, cash=args.cash,
                          equity=args.equity, monthly_committed=args.monthly_committed)
    except ValueError as exc:
        print("Proposal rejected: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
