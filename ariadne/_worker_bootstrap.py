"""Apply OS limits before loading a fixed optional capability implementation."""
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root.parent))
from ariadne.worker import apply_limits, _ALLOWED, worker_process_limit
if len(sys.argv) not in {4, 5} or sys.argv[1] not in _ALLOWED:
    raise SystemExit(2)
process_limit = int(sys.argv[4]) if len(sys.argv) == 5 else 1
if not 1 <= process_limit <= worker_process_limit(sys.argv[1]):
    raise SystemExit(2)
apply_limits(int(sys.argv[2]), int(sys.argv[3]), process_limit)
target = root / sys.argv[1]
sys.argv = [str(target)]
runpy.run_path(str(target), run_name="__main__")
