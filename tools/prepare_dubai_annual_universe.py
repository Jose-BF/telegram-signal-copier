"""Freeze reviewed Dubai entry hypotheses and rolling cohorts, offline only."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.dubai_annual_universe import build_universe, write_universe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    universe = build_universe(args.catalog, args.review)
    manifest = write_universe(universe, args.output)
    print(json.dumps({"identity": manifest["universe_identity_sha256"], **universe["summary"]}, indent=2))


if __name__ == "__main__":
    main()
