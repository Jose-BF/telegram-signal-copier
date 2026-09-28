"""Run or verify the fixed offline Dubai versus R05 comparison."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.dubai_current_study import run, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("run")
    start.add_argument("--history", required=True)
    start.add_argument("--output", required=True)
    sub.add_parser("verify").add_argument("--output", required=True)
    args = parser.parse_args()
    def progress(value):
        print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)
    if args.command == "run":
        result = run(args.history, args.output, progress)
        progress({k: v for k, v in result.items() if k not in {"inputs", "artifacts"}})
    else:
        progress(verify(args.output))


if __name__ == "__main__":
    main()
