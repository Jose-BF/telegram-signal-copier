"""Run or verify the finite Canal 1 absolute-price economic study."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.dubai_range_study import run_study, verify_study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--anatomy", required=True)
    run.add_argument("--old-controls", required=True)
    run.add_argument("--coverage-protocol", required=True)
    run.add_argument("--raw-audit", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--parity-from", help="Retain identical scalar/oracle evidence from a complete matrix after summary-only failure")
    verify = sub.add_parser("verify")
    verify.add_argument("--output", required=True)
    args = parser.parse_args()
    def progress(value):
        print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)
    if args.command == "run":
        result = run_study(args.anatomy, args.old_controls, args.coverage_protocol, args.raw_audit, args.output, progress, parity_from=args.parity_from)
        progress({k:v for k,v in result.items() if k not in {"inputs", "artifacts"}})
    else:
        progress(verify_study(args.output))


if __name__ == "__main__":
    main()
