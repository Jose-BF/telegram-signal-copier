"""Prepare annual Dubai directional messages without enabling engine admission."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.dubai_export_catalog import build_catalog, write_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission-dir", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--companion-seconds", type=int, default=300)
    args = parser.parse_args()
    try:
        catalog = build_catalog(args.admission_dir, args.labels, companion_seconds=args.companion_seconds)
        manifest = write_catalog(catalog, args.output_dir)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"catalog preparation failed: {exc}\n")
    print(json.dumps({**catalog["summary"], "catalog_identity_sha256": manifest["catalog_identity_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
