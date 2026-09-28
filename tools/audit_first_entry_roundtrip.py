"""Join modeled first entries to observed MT5 call stages by native deal."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from tools.probe_canal1_incremental_window import digest


def audit_first_entries(tick_reports, terminal, money):
    if (not tick_reports or terminal.get("status") != "diagnostic_only"
            or money.get("account_currency") != "EUR"):
        raise ValueError("first-entry source contract unavailable")
    terminal_rows = terminal.get("rows", [])
    by_terminal = {row["signal_id"]: row for row in terminal_rows}
    if len(by_terminal) != len(terminal_rows):
        raise ValueError("duplicate terminal signal identity")
    by_money = {}
    for position in money.get("positions", []):
        by_money.setdefault(position["signal_id"], []).append(position)
    rows, seen, excluded = [], set(), Counter()
    input_rows = 0
    for report in tick_reports:
        if (report.get("contract") != "incremental_common_tick_path_comparison_v1"
                or report.get("status") != "diagnostic_only"
                or report.get("full_live_parity_verified") is not False):
            raise ValueError("tick-path diagnostic contract unavailable")
        if report.get("counts") != dict(Counter(row["status"] for row in report["rows"])):
            raise ValueError("tick-path row denominator inconsistent")
        for basket in report["rows"]:
            input_rows += 1
            if basket["status"] not in {"path_discrepant", "same_grid_fields_equal"}:
                excluded[basket["status"]] += 1
                continue
            signal_id = basket["signal_id"]
            if signal_id in seen:
                raise ValueError("duplicate first-entry basket across windows")
            seen.add(signal_id)
            first = basket["first_entry_evidence"]
            positions = by_money.get(signal_id, [])
            if not positions:
                raise ValueError("native first position unavailable")
            position = min(positions, key=lambda item: item["entry_msc"])
            terminal_row = by_terminal.get(signal_id)
            if (terminal_row is None
                    or terminal_row.get("status") != "terminal_deal_and_done_bound"
                    or terminal_row.get("terminal_stage_order_consistent") is not True
                    or position["deal_tickets"][0] != terminal_row["native_entry_deal"]):
                raise ValueError("first native deal not bound to terminal stages")
            native_at = datetime.fromtimestamp(
                position["entry_msc"] / 1000 - 10_800, timezone.utc)
            model_at = datetime.fromisoformat(first["model_requested_utc"])
            model_minus_native = int((model_at - native_at).total_seconds() * 1000)
            if (first["native_fill_utc"] != native_at.isoformat()
                    or model_minus_native != first["model_minus_native_ms"]
                    or Decimal(first["native_entry_price"]) != Decimal(str(position["entry_price"]))
                    or Decimal(first["native_entry_volume"]) != Decimal(str(position["volume"]))):
                raise ValueError("first-entry money or clock anchor changed")
            stage_fields = ("terminal_market_minus_python_call_ms",
                            "market_to_accepted_ms", "accepted_to_deal_ms",
                            "terminal_deal_minus_python_start_ms")
            stages = [terminal_row[name] for name in stage_fields]
            call_ms = Decimal(str(terminal_row["python_mt5_call_ms"]))
            if (any(type(value) is not int or value < 0 for value in stages)
                    or sum(stages[:3]) != stages[3]
                    or not call_ms.is_finite() or call_ms < 0
                    or call_ms != call_ms.to_integral_value()):
                raise ValueError("terminal first-call stages inconsistent")
            rows.append({"signal_id": signal_id, "channel": basket["channel"],
                         "native_entry_deal": terminal_row["native_entry_deal"],
                         "model_minus_native_fill_ms": model_minus_native,
                         "terminal_deal_from_python_start_ms": stages[3],
                         "model_request_from_python_start_ms": model_minus_native + stages[3],
                         "python_mt5_call_ms": int(call_ms),
                         "terminal_market_from_python_start_ms": stages[0],
                         "terminal_market_to_accepted_ms": stages[1],
                         "terminal_accepted_to_deal_ms": stages[2],
                         "model_minus_native_entry_price": first["model_minus_native_price"],
                         "same_entry_volume": first["same_entry_volume"],
                         "terminal_cross_clock_order_consistent": terminal_row.get(
                             "call_clock_order_consistent")})
    if not rows:
        raise ValueError("no comparable first entries")
    rows.sort(key=lambda row: row["signal_id"])
    residuals = [row["model_request_from_python_start_ms"] for row in rows]
    return {"contract": "first_entry_roundtrip_crossclock_diagnostic_v1",
            "status": "diagnostic_only", "input_report_count": len(tick_reports),
            "input_basket_row_count": input_rows,
            "excluded_statuses": dict(sorted(excluded.items())),
            "matched_basket_count": len(rows),
            "model_request_from_python_start_min_ms": min(residuals),
            "model_request_from_python_start_max_ms": max(residuals),
            "rows": rows, "full_live_path_parity_verified": False,
            "limitations": [
                "Terminal and Python clocks are cross-source diagnostics, not exact server timestamps.",
                "A synchronous MT5 call includes terminal, network and trade-server time; these stages cannot be isolated here.",
                "First-entry timing does not explain later ladder, protection or close divergence.",
                "Only baskets admitted by the tick-path comparison are in this numerator.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tick-report", type=Path, action="append", required=True)
    parser.add_argument("--terminal", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    terminal = json.loads(args.terminal.read_text(encoding="utf-8"))
    money = json.loads(args.money.read_text(encoding="utf-8"))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.tick_report]
    if not terminal.get("inputs_sha256") or len(terminal["inputs_sha256"]) > 64:
        raise ValueError("terminal source manifest unavailable")
    for name, sha in terminal["inputs_sha256"].items():
        if digest(name) != sha:
            raise ValueError(f"terminal source changed: {name}")
    for report in reports:
        if (report["sources"]["money_sha256"] != digest(args.money)
                or report["sources"]["comparator_sha256"]
                != digest(Path(__file__).with_name("compare_incremental_tick_paths.py"))):
            raise ValueError("tick report source changed")
    result = audit_first_entries(reports, terminal, money)
    paths = [*args.tick_report, args.terminal, args.money, Path(__file__)]
    result["inputs_sha256"] = {str(path): digest(path) for path in paths}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2, sort_keys=True, allow_nan=False)
        target.write("\n")
    print(json.dumps({"status": result["status"],
                      "matched_basket_count": result["matched_basket_count"]}))


if __name__ == "__main__":
    main()
