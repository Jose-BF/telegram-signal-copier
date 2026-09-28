"""Run/verify four fixed offline controls on original/export clock pairs."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.dubai_paired_clocks import run_controls, verify_controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--audit", required=True)
    run.add_argument("--coverage", required=True)
    run.add_argument("--raw-audit", required=True)
    run.add_argument("--output", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--output", required=True)
    args = parser.parse_args()
    def progress(value):
        print(json.dumps(value), flush=True)
    if args.command == "run":
        result = run_controls(args.audit, args.coverage, args.raw_audit, args.output, progress=progress)
        progress({k: v for k, v in result.items() if k != "comparisons"})
    else:
        progress(verify_controls(args.output))


if __name__ == "__main__":
    main()
