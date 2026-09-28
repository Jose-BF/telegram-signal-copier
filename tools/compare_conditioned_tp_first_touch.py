"""Bind conditioned Gold exits to native TP first-touch observations."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path

from research.causal_replay import utc
from tools.probe_canal1_incremental_window import digest
from tools.run_causal_controls import save


PROJECT = Path(__file__).resolve().parents[1]


def compare(model, population):
    if (model.get("contract") != "conditioned_fx_interval_path_sensitivity_v1"
            or population.get("contract") != "native_gold_tp_terminal_touch_population_v1"
            or model.get("full_live_parity_verified") is not False
            or population.get("full_live_parity_verified") is not False):
        raise ValueError("conditioned or native TP contract differs")
    cases = model["cases"]
    if len(cases) != 4 or len({row["signal_id"] for row in cases}) != 4:
        raise ValueError("conditioned comparison cohort differs")
    positions = {}
    for row in population["rows"]:
        positions.setdefault(row["signal_id"], []).append(row)
    rows = []
    for case in cases:
        if case["status"] != "retrospective_path_compared":
            raise ValueError("conditioned path not compared")
        legs = sorted(positions[case["signal_id"]], key=lambda row: utc(row["native_entry_utc"]))
        exits = case["exit_timing"]
        if len(legs) != len(exits) or len({row["slot"] for row in exits}) != len(exits):
            raise ValueError("logical slot and native position denominator differs")
        for exit_row in exits:
            slot = exit_row["slot"]
            if not 1 <= slot <= len(legs):
                raise ValueError("logical slot outside native basket")
            position = legs[slot - 1]
            if (position["status"] != "retained_tp_side_touch"
                    or position["native_exit_reason"] != 5
                    or utc(position["native_exit_utc"]) != utc(exit_row["native_exit_at"])
                    or Decimal(str(position["target"]))
                    != Decimal(str(exit_row["native_exit_price"]))
                    or Decimal(str(position["target"]))
                    != Decimal(str(exit_row["model_exit_price"]))
                    or exit_row["model_mechanism"] != "per_leg_target"):
                raise ValueError("native TP deal and modeled exit identity differs")
            first = position["terminal_corridor"]["first_qualifying_side_update_utc"]
            if first is None:
                raise ValueError("retained TP first touch missing")
            model_at = utc(exit_row["model_exit_at"])
            first_at = utc(first)
            delta_ms = int((model_at - first_at).total_seconds() * 1000)
            native_lag_ms = int((utc(position["native_exit_utc"]) - first_at).total_seconds() * 1000)
            if native_lag_ms != position["terminal_corridor"]["first_touch_to_native_exit_ms"]:
                raise ValueError("native terminal touch lag differs")
            rows.append({"signal_id": case["signal_id"], "slot": slot,
                         "native_position_id": position["position_id"],
                         "model_exit_at": model_at, "first_terminal_touch_at": first_at,
                         "native_exit_at": utc(position["native_exit_utc"]),
                         "model_minus_first_touch_ms": delta_ms,
                         "first_touch_to_native_exit_ms": native_lag_ms,
                         "model_minus_native_ms": exit_row["model_minus_native_ms"],
                         "receipt_status": position["receipt_status"],
                         "retained_qualifying_side_updates": position["terminal_corridor"][
                             "qualifying_side_update_count"],
                         "status": ("model_at_first_terminal_touch" if delta_ms == 0
                                    else "model_not_at_first_terminal_touch")})
    if len(rows) != sum(len(case["exit_timing"]) for case in cases):
        raise ValueError("conditioned exit denominator changed")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conditioned", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = (args.conditioned.resolve(), args.population.resolve())
    output = args.output.resolve()
    if output.exists():
        raise ValueError("immutable output already exists")
    model, population = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    for name, expected in model["sources"].items():
        if digest(PROJECT / name) != expected:
            raise ValueError(f"conditioned auditor source changed: {name}")
    for name, expected in population["sources"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"native population source changed: {name}")
    watched = {str(path): digest(path) for path in
               (*paths, Path(__file__), PROJECT / "research/causal_replay.py")}
    rows = compare(model, population)
    if any(digest(Path(name)) != expected for name, expected in watched.items()):
        raise ValueError("TP comparison input changed during run")
    report = {"contract": "conditioned_tp_first_terminal_touch_comparison_v1",
              "status": "diagnostic_only", "full_live_parity_verified": False,
              "conditioned_baskets": len(model["cases"]),
              "native_population_baskets": population["baskets"],
              "native_population_positions": population["positions"],
              "compared_exits": len(rows),
              "statuses": dict(Counter(row["status"] for row in rows)),
              "rows": rows, "inputs_sha256": watched,
              "limitations": ["Conditioned native entries, not a causal entry-policy replay.",
                              "Terminal first side update is not a broker-server TP trigger.",
                              "Client TP installation receipts are incomplete for this cohort.",
                              "No fixed TP execution delay or live policy change is inferred."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, report)
    print({"output": str(output), "statuses": report["statuses"]})


if __name__ == "__main__":
    main()
