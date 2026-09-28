"""Reconstruct accepted TP-to-deal lifecycle without treating terminal touches as fills."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from research.causal_replay import time_ns, utc
from research.dubai_iterative.broker_execution import (
    BrokerExecutionModel, EventTime, Position, Quote, SymbolConstraints,
)
from tools.probe_canal1_incremental_window import digest, load_day
from tools.run_causal_controls import save


PROJECT = Path(__file__).resolve().parents[1]
OFFSET_SECONDS = 10_800
MAX_POSITIONS = 100
MAX_SIDE_UPDATES_PER_POSITION = 20_000
MAX_SECONDS = 120


def observed_case(row, position, tape, flags):
    accepted_at = utc(row["accepted_response_utc"])
    exit_at = utc(row["native_exit_utc"])
    accepted_ns, exit_ns = time_ns(accepted_at), time_ns(exit_at)
    if not accepted_ns < exit_ns:
        raise ValueError("TP response is not before native deal")
    times, bids, asks = tape
    first = int(np.searchsorted(times, accepted_ns, side="right"))
    last = int(np.searchsorted(times, exit_ns, side="left"))
    side_bit = 2 if row["direction"] == "BUY" else 4
    selected = np.flatnonzero((flags[first:last] & side_bit) != 0) + first
    if len(selected) > MAX_SIDE_UPDATES_PER_POSITION:
        raise ValueError("TP quote budget exceeded")
    if len(selected) and np.any(np.diff(times[selected]) <= 0):
        raise ValueError("same-clock side-update order unproven")
    broker = BrokerExecutionModel(
        SymbolConstraints("XAUUSD", 0.01, 2, 0, 0),
        modify_processing_delay_ns=0, passive_exit_mode="observed_fill")
    ticket = str(row["position_id"])
    broker.add_position(Position(ticket, row["direction"], EventTime(accepted_at),
                                 float(position["entry_price"]), tp=float(row["target"])))
    touches = []
    for index in selected:
        quote_at = pd.Timestamp(int(times[index]), tz="UTC").to_pydatetime()
        update = broker.on_quote(Quote(EventTime(quote_at),
                                       float(bids[index]), float(asks[index])))
        if update.exits:
            raise ValueError("terminal quote produced unobserved fill")
        touches.extend(update.touches)
    if broker.position(ticket).status != "open":
        raise ValueError("position closed before broker deal")
    observed = broker.confirm_observed_exit(ticket, at=EventTime(exit_at),
                                            price=float(row["target"]),
                                            reason="take_profit")
    if (observed.effective_at.at != exit_at
            or broker.position(ticket).status != "closed"
            or len(touches) > 1):
        raise ValueError("observed deal or TP touch chronology differs")
    expected_lag = row["terminal_corridor"]["first_post_accept_touch_to_native_exit_ms"]
    actual_lag = (int((exit_at - observed.first_terminal_touch_at.at).total_seconds() * 1000)
                  if observed.first_terminal_touch_at is not None else None)
    if actual_lag != expected_lag:
        raise ValueError("post-accept terminal touch differs from native population")
    return {"status": ("observed_fill_after_terminal_touch" if touches
                       else "observed_fill_without_retained_terminal_touch"),
            "accepted_response_utc": accepted_at, "first_terminal_touch_utc": (
                touches[0].observed_at.at if touches else None),
            "native_exit_utc": exit_at, "terminal_to_fill_ms": actual_lag,
            "relevant_side_updates": len(selected),
            "position_open_after_touch_until_deal": True,
            "broker_exit_price": observed.price}


def audit(population_path, money_path, root, output):
    started = time.monotonic()
    population_path, money_path, root, output = (
        Path(path).resolve() for path in (population_path, money_path, root, output))
    if output.exists():
        raise ValueError("immutable output already exists")
    population = json.loads(population_path.read_text(encoding="utf-8"))
    money = json.loads(money_path.read_text(encoding="utf-8"))
    if (population.get("contract") != "native_gold_tp_terminal_touch_population_v1"
            or population.get("status") != "diagnostic_only"
            or money.get("contract") != "native_closed_money_anchor_v2"
            or money.get("account_currency") != "EUR"
            or population["positions"] != len(population["rows"])
            or not 1 <= population["positions"] <= MAX_POSITIONS):
        raise ValueError("native TP population contract differs")
    for name, expected in population["sources"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"native population source changed: {name}")
    if population["sources"].get(str(money_path)) != digest(money_path):
        raise ValueError("native position money source differs")
    positions = {row["position_id"]: row for row in money["positions"]}
    if len(positions) != len(money["positions"]):
        raise ValueError("duplicate native money position")
    accepted_rows = [row for row in population["rows"]
                     if row.get("receipt_status")
                     == "client_target_accepted_response_before_native_exit"]
    days = {row["day"] for row in accepted_rows}
    if not accepted_rows or len(days) > 3:
        raise ValueError("accepted TP cohort or day budget invalid")
    tapes, tape_proofs = {}, {}
    for day in sorted(days):
        rows = [row for row in accepted_rows if row["day"] == day]
        start = min(time_ns(utc(row["accepted_response_utc"])) for row in rows)
        cutoff = max(time_ns(utc(row["native_exit_utc"])) for row in rows) + 1_000_000
        tape, proof = load_day(root, "XAUUSD", day, start_ns=start,
                               cutoff_ns=cutoff, offset_seconds=OFFSET_SECONDS)
        if proof["parquet_sha256"] != population["tapes"][day]["parquet_sha256"]:
            raise ValueError("accepted TP tape differs from native population")
        frame = pd.read_parquet(root / "XAUUSD" / f"{day}.parquet",
                                columns=["time_msc", "flags"])
        stamps = (frame.time_msc.to_numpy(dtype=np.int64)
                  - OFFSET_SECONDS * 1000) * 1_000_000
        selected = (stamps >= start) & (stamps < cutoff)
        if not np.array_equal(stamps[selected], tape[0]):
            raise ValueError("TP side flags and priced tape differ")
        tapes[day] = (tape, frame["flags"].to_numpy(dtype=np.uint32)[selected])
        tape_proofs[day] = proof
    rows = []
    for source in population["rows"]:
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("observed TP lifecycle wall budget exceeded")
        row = {"signal_id": source["signal_id"],
               "position_id": source["position_id"],
               "receipt_status": source.get("receipt_status"),
               "native_exit_reason": source["native_exit_reason"]}
        if source["status"] == "non_tp_exit":
            row["status"] = "non_tp_exit"
        elif source.get("receipt_status") != "client_target_accepted_response_before_native_exit":
            row["status"] = "blocked_missing_accepted_tp_response"
        else:
            position = positions.get(source["position_id"])
            if (position is None or position["signal_id"] != source["signal_id"]
                    or position["direction"] != source["direction"]):
                raise ValueError("accepted TP native position identity differs")
            try:
                tape, flags = tapes[source["day"]]
                row.update(observed_case(source, position, tape, flags))
            except ValueError as exc:
                row.update(status="blocked_observed_lifecycle", blockers=[str(exc)])
        rows.append(row)
    if len(rows) != population["positions"]:
        raise ValueError("observed TP denominator changed")
    watched = {str(path): digest(path) for path in (
        population_path, money_path, Path(__file__),
        PROJECT / "research/dubai_iterative/broker_execution.py",
        PROJECT / "tools/probe_canal1_incremental_window.py")}
    if any(digest(Path(name)) != expected for name, expected in watched.items()):
        raise ValueError("observed TP source changed during run")
    report = {"contract": "native_tp_observed_fill_lifecycle_v1",
              "status": "diagnostic_only", "full_live_parity_verified": False,
              "counterfactual_fill_prediction_verified": False,
              "native_population_positions": population["positions"],
              "accepted_response_cases": len(accepted_rows),
              "rows": rows, "statuses": dict(Counter(row["status"] for row in rows)),
              "sources": watched, "tapes": tape_proofs,
              "limitations": ["Starts local TP lifecycle at accepted client response, not native entry.",
                              "Client response may not date server-side installation exactly.",
                              "Observed native deal is an input, never predicted by terminal ticks.",
                              "Missing receipt cases remain blocked for this lifecycle contract."],
              "elapsed_seconds": time.monotonic() - started}
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.population, args.money, args.root, args.output)
    print({"output": str(args.output), "statuses": report["statuses"]})


if __name__ == "__main__":
    main()
