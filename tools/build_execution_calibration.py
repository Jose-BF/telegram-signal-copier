"""Turn measured MT5 terminal timings into fixed execution delays for one quantile.

Input is the events.jsonl.gz written by tools/audit_mt5_terminal_trade_logs.py.
The output (contract execution_calibration_v1) feeds
tools/probe_canal1_incremental_window.py --execution-calibration. Each run uses
one fixed quantile; run several quantiles to see the spread. Diagnostic only.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_mt5_terminal_trade_logs import (  # noqa: E402
    build_market_chains, build_modify_chains, digest, rejection_bursts,
)

CONTRACT = "execution_calibration_v1"


def pick(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        raise ValueError("no measurements for a calibrated component")
    return values[min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))]


def calibrate(events, first_day, last_day, quantile):
    events = [e for e in events if first_day <= e["day"] <= last_day]
    market, market_local = build_market_chains(events)
    modify, modify_local = build_modify_chains(events)
    done_market = [c for c in market if c["request"] and c["done"]]
    opens = [c for c in done_market if not c["request"].get("close_position")]
    closes = [c for c in done_market if c["request"].get("close_position")]
    acks = [c["done"]["utc_ms"] - min(d["utc_ms"] for d in c["deals"]) + 1
            for c in done_market if c["deals"]]
    mod = [c for c in modify if c["request"] and c["done"] and c["accepted"]]
    retry_gaps = [b["median_gap_ms"] for b in rejection_bursts(modify_local)
                  if b["reason"] == "Invalid stops" and b["median_gap_ms"]]
    delays = {
        "entry_fill_latency_ms": pick([c["done"]["done_ms"] for c in opens], quantile),
        "entry_ack_ms": pick(acks, quantile),
        "close_processing_ms": pick([c["done"]["done_ms"] for c in closes], quantile),
        "close_ack_ms": pick(acks, quantile),
        "protection_processing_ms": pick([c["accepted"]["utc_ms"] - c["request"]["utc_ms"] for c in mod], quantile),
        "protection_ack_ms": pick([c["done"]["utc_ms"] - c["accepted"]["utc_ms"] + 1 for c in mod], quantile),
        "protection_retry_ms": pick(retry_gaps, 0.5) if retry_gaps else 1000,
    }
    counts = {"open_orders": len(opens), "close_orders": len(closes), "modifies": len(mod),
              "invalid_stops_bursts": len(retry_gaps)}
    return {k: int(round(v)) for k, v in delays.items()}, counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--first-day", required=True, help="YYYYMMDD inclusive")
    parser.add_argument("--last-day", required=True, help="YYYYMMDD inclusive")
    parser.add_argument("--quantile", type=float, default=0.5)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    with gzip.open(args.events, "rt", encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle]
    delays, counts = calibrate(events, args.first_day, args.last_day, args.quantile)
    report = {"contract": CONTRACT, "label": args.label, "quantile": args.quantile,
              "period": [args.first_day, args.last_day], "delays_ms": delays, "counts": counts,
              "source_sha256": {str(args.events): digest(args.events)},
              "limitations": ["One fixed quantile per component; real delays vary per request.",
                              "Terminal clock: network and server time are not separated."]}
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report["delays_ms"]), json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
