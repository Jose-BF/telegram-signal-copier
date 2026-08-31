"""Build the Gold Signals published-result scorecard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import runtime_paths
from provider_result_scorecard import build_scorecard


DATA_DIR = runtime_paths.active_data_dir(REPO_DIR)
DEFAULT_CATALOG = DATA_DIR / "provider_signal_catalog.json"
DEFAULT_OUTPUT = DATA_DIR / "provider_result_scorecard.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Structure Gold Signals daily and weekly result claims"
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if not args.quiet:
            print(f"Provider scorecard input unavailable: {exc}")
        return 1
    report = build_scorecard(catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not args.quiet:
        print(
            "Provider scorecard: "
            f"{report['summary']['records']} summaries, "
            f"{report['summary']['calibration_ready']} strict-ready"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
