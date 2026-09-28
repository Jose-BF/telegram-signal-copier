"""Audit original Canal 1 receipt identities against a frozen annual export."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.dubai_clock_audit import run_audit, verify_audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--raw-inputs", required=True)
    run.add_argument("--stream", required=True)
    run.add_argument("--output", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--output", required=True)
    args = parser.parse_args()
    result = (run_audit(args.raw_inputs, args.stream, args.output) if args.command == "run"
              else verify_audit(args.output))
    print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
