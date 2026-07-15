"""CLI for auditable reliability-pattern review decisions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from log_pattern_review import (  # noqa: E402
    DEFAULT_LEDGER,
    ReviewError,
    cover_pattern,
    dismiss_pattern,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify and record an offline reliability-pattern review",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER,
        help="versioned review ledger path",
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    cover = subparsers.add_parser(
        "cover",
        help="prove that source code and tests cover a pattern",
    )
    cover.add_argument("pattern_id")
    cover.add_argument("--rule-version", required=True)
    cover.add_argument("--fix-commit", required=True)
    cover.add_argument("--test", dest="regression_test", required=True)
    cover.add_argument("--reviewer", required=True)

    dismiss = subparsers.add_parser(
        "dismiss",
        help="record a reviewed non-code pattern decision",
    )
    dismiss.add_argument("pattern_id")
    dismiss.add_argument("--reason", required=True)
    dismiss.add_argument("--reviewer", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "cover":
            decision = cover_pattern(
                pattern_id=args.pattern_id,
                rule_version=args.rule_version,
                fix_commit=args.fix_commit,
                regression_test=args.regression_test,
                reviewer=args.reviewer,
                ledger_path=args.ledger,
            )
        else:
            decision = dismiss_pattern(
                pattern_id=args.pattern_id,
                reason=args.reason,
                reviewer=args.reviewer,
                ledger_path=args.ledger,
            )
    except ReviewError as exc:
        print(f"Review rejected: {exc}", file=sys.stderr)
        return 2

    print(f"Pattern: {decision.pattern_id}")
    print(f"Status: {decision.status}")
    print(f"Evidence corpus: {decision.source_fingerprint}")
    print(f"Ledger: {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
