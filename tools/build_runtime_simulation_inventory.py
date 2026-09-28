"""Build the sanitized runtime/simulation evidence inventory."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.runtime_simulation_inventory import build_inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", action="append", required=True)
    parser.add_argument("--weekly-shadow")
    parser.add_argument("--weekly-parity")
    parser.add_argument("--native")
    parser.add_argument("--market-status")
    parser.add_argument("--money-status")
    parser.add_argument("--since", required=True)
    parser.add_argument("--until-exclusive", required=True)
    parser.add_argument("--generated-at")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build_inventory(
        event_paths=args.events,
        weekly_shadow_path=args.weekly_shadow,
        weekly_parity_path=args.weekly_parity,
        native_path=args.native,
        market_status_path=args.market_status,
        money_status_path=args.money_status,
        since=args.since,
        until_exclusive=args.until_exclusive,
        generated_at=args.generated_at,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "signals": len(result["signals"]), "days": len(result["daily"])}, sort_keys=True))


if __name__ == "__main__":
    main()
