"""Describe Canal 1 messages and retained quote paths without executing strategies."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.dubai_signal_anatomy import run_anatomy, verify_anatomy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--inventory", required=True)
    run.add_argument("--coverage-protocol", required=True)
    run.add_argument("--raw-audit", required=True)
    run.add_argument("--output", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--output", required=True)
    args = parser.parse_args()

    def progress(value):
        print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)

    if args.command == "run":
        result = run_anatomy(args.inventory, args.coverage_protocol, args.raw_audit, args.output, progress)
        progress({key: value for key, value in result.items() if key != "summary"})
    else:
        progress(verify_anatomy(args.output))


if __name__ == "__main__":
    main()
