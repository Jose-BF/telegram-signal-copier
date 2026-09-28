"""Check fixed receipt horizons against unchanged historical quote contracts."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.dubai_receipt_coverage import run_coverage, verify_coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--receipts", required=True)
    run.add_argument("--coverage", required=True)
    run.add_argument("--raw-audit", required=True)
    run.add_argument("--output", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--output", required=True)
    args = parser.parse_args()
    def progress(value):
        print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)
    if args.command == "run":
        result = run_coverage(args.receipts, args.coverage, args.raw_audit, args.output, progress=progress)
        progress({k: v for k, v in result.items() if k not in {"by_evidence_tier", "by_receipt_month"}})
    else:
        progress(verify_coverage(args.output))


if __name__ == "__main__":
    main()
