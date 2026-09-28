"""Freeze separate canonical and legacy Canal 1 receipt evidence tiers."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.dubai_receipt_inventory import run_inventory, verify_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--prior-audit", required=True)
    run.add_argument("--output", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_inventory(args.prior_audit, args.output) if args.command == "run" else verify_inventory(args.output)
    print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
