"""Run four fixed controls on the expanded, evidence-tiered receipt inventory."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.dubai_receipt_controls import run_controls, verify_controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--receipt-coverage", required=True)
    run.add_argument("--prior-controls", required=True)
    run.add_argument("--output", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--output", required=True)
    args = parser.parse_args()
    def progress(value):
        print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)
    if args.command == "run":
        result = run_controls(args.receipt_coverage, args.prior_controls, args.output, progress=progress)
        progress({k: v for k, v in result.items() if k != "groups"})
    else:
        progress(verify_controls(args.output))


if __name__ == "__main__":
    main()
