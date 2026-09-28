"""Prepare Telegram exports offline without claiming tick/strategy admission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.telegram_export import prepare_exports, write_admission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--expected-sha256", action="append", help="One SHA256 per --source, in the same order")
    parser.add_argument("--start", required=True, help="Inclusive timestamp with explicit UTC offset")
    parser.add_argument("--end", required=True, help="Exclusive timestamp with explicit UTC offset")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        bundle = prepare_exports(args.source, start=args.start, end=args.end)
        if args.expected_sha256 is not None:
            actual = [source["sha256"] for source in bundle["sources"]]
            if actual != args.expected_sha256:
                raise ValueError("source SHA256 mismatch or expected hash count differs")
        manifest = write_admission(bundle, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"admission failed: {exc}\n")
    print(json.dumps({**bundle["summary"], "output_dir": str(args.output_dir.resolve()),
                      "archive_identity_sha256": manifest["archive_identity_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
