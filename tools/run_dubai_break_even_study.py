"""Run or verify the three-management offline Canal 1 study."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.dubai_break_even_study import run, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("run")
    for name in ("anatomy", "base", "snapshot", "raw-audit", "output"):
        start.add_argument("--" + name, required=True)
    sub.add_parser("verify").add_argument("--output", required=True)
    args = parser.parse_args()
    def progress(value):
        print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)
    if args.command == "run":
        result = run(args.anatomy, args.base, args.snapshot, args.raw_audit, args.output, progress)
        progress({k:v for k,v in result.items() if k not in {"inputs", "artifacts"}})
    else:
        progress(verify(args.output))


if __name__ == "__main__":
    main()
