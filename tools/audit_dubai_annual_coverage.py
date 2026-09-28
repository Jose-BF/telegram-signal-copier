"""Audit the annual Canal 1 clocks and quote windows without MT5 or strategies."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.dubai_annual_coverage import build_audit, write_audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--raw-audit", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output already exists")
    result = build_audit(args.universe, args.raw_audit, args.protocol)
    manifest = write_audit(result, args.output)
    print(json.dumps({"identity": manifest["audit_identity_sha256"],
                      "entries": result["summary"]["entries"], "groups": result["summary"]["groups"]}), flush=True)


if __name__ == "__main__":
    main()
