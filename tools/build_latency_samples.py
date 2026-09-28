"""Empirical per-request delay samples (sorted) from measured MT5 terminal timings.

Input: events.jsonl.gz from tools/audit_mt5_terminal_trade_logs.py. Output
contract latency_samples_v1, consumed by research/dubai_iterative/latency_model.py
(one delay drawn per request, deterministic by seed). Only market-open fill
latency (request -> order done, "done in X ms") is sampled in v1; the other
components stay fixed quantiles from tools/build_execution_calibration.py.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_mt5_terminal_trade_logs import build_market_chains, digest  # noqa: E402

CONTRACT = "latency_samples_v1"


def samples(events, first_day, last_day):
    events = [e for e in events if first_day <= e["day"] <= last_day]
    market, _ = build_market_chains(events)
    opens = [c for c in market if c["request"] and c["done"] and not c["request"].get("close_position")]
    values = sorted(int(round(c["done"]["done_ms"])) for c in opens)
    if not values:
        raise ValueError("no measured market opens in period")
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--first-day", required=True, help="YYYYMMDD inclusive")
    parser.add_argument("--last-day", required=True, help="YYYYMMDD inclusive")
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    with gzip.open(args.events, "rt", encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle]
    values = samples(events, args.first_day, args.last_day)
    report = {"contract": CONTRACT, "label": args.label, "period": [args.first_day, args.last_day],
              "entry_fill_ms": values, "count": len(values),
              "quantiles_ms": {q: values[min(len(values) - 1, int(round(float(q) * (len(values) - 1))))]
                               for q in ("0.25", "0.5", "0.75", "0.9", "0.95", "0.99")},
              "source_sha256": {Path(args.events).as_posix(): digest(args.events)},
              "limitations": ["Draws are independent per request; real slow periods cluster in time.",
                              "Terminal clock: network and server time are not separated."]}
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"count": len(values), "quantiles_ms": report["quantiles_ms"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
