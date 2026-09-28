"""Retrospective risk sensitivity to predeclared passive-TP fill worlds."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from broker_money import convert_profit_amount
from research.causal_comparison import SequenceEvent
from research.causal_replay import time_ns, utc
from research.dubai_iterative.broker_execution import (
    BrokerExecutionModel, EventTime, Position, Quote, SymbolConstraints,
)
from research.dubai_iterative.passive_fill_contract import PassiveFillScenario
from research.risk_trajectory import RiskSpec, compare_risk
from tools.compare_risk_trajectories import paired_quotes
from tools.probe_canal1_incremental_window import digest, load_day
from tools.run_causal_controls import encode, save


PROJECT = Path(__file__).resolve().parents[1]
OFFSET_SECONDS = 10_800
DELAYS_MS = (0, 100, 1_000, 5_000, 30_000)
PRICE_MODES = ("installed_level", "executable_quote")
MAX_POSITIONS = 100
MAX_QUOTE_UPDATES = 30_000
MAX_SECONDS = 180


def causal_fill_money(position, price, fill_ns, conversion, spec):
    fx_times, fx_bids, fx_asks = conversion
    index = int(np.searchsorted(fx_times, fill_ns, side="right")) - 1
    if index < 0 or (fill_ns - int(fx_times[index])) > spec.max_fx_age_ms * 1_000_000:
        raise ValueError("stale_or_missing_causal_fx_at_fill")
    direction = 1 if position["direction"] == "BUY" else -1
    amount = ((Decimal(str(price)) - Decimal(str(position["entry_price"])))
              * direction * Decimal(str(position["volume"])) * spec.contract_size)
    fx = Decimal(str(float(fx_asks[index] if amount >= 0 else fx_bids[index])))
    return convert_profit_amount(amount, fx, orientation=spec.orientation,
                                 currency_digits=spec.currency_digits)


def simulate_fill(row, position, market, flags, *, delay_ms, price_mode, cutoff_ns):
    accepted = utc(row["accepted_response_utc"])
    accepted_ns = time_ns(accepted)
    native_exit_ns = time_ns(utc(row["native_exit_utc"]))
    if accepted_ns >= native_exit_ns:
        raise ValueError("accepted_tp_response_not_before_native_exit")
    times, bids, asks = market
    first = int(np.searchsorted(times, accepted_ns, side="right"))
    last = int(np.searchsorted(times, cutoff_ns, side="left"))
    side_flag = 2 if row["direction"] == "BUY" else 4
    if last - first > MAX_QUOTE_UPDATES:
        raise ValueError("scenario_quote_update_budget_exceeded")
    broker = BrokerExecutionModel(
        SymbolConstraints("XAUUSD", 0.01, 2, 0, 0),
        modify_processing_delay_ns=0,
        passive_exit_mode="quote_delay_scenario",
        passive_fill_scenario=PassiveFillScenario(delay_ms * 1_000_000,
                                                  price_mode))
    ticket = str(row["position_id"])
    broker.add_position(Position(ticket, row["direction"], EventTime(accepted),
                                 float(position["entry_price"]), tp=float(row["target"])))
    touch, fill = None, None
    processed = 0
    for index in range(first, last):
        if touch is None:
            if times[index] >= native_exit_ns:
                break
            if not flags[index] & side_flag:
                continue
        quote_at = pd.Timestamp(int(times[index]), tz="UTC").to_pydatetime()
        update = broker.on_quote(Quote(EventTime(quote_at),
                                       float(bids[index]), float(asks[index])))
        processed += 1
        if update.touches:
            if touch is not None or len(update.touches) != 1:
                raise ValueError("scenario_multiple_tp_touches")
            touch = update.touches[0].observed_at
        if update.exits:
            if fill is not None or len(update.exits) != 1:
                raise ValueError("scenario_multiple_tp_fills")
            fill = update.exits[0]
            break
    return {"status": ("filled" if fill is not None else
                        "open_at_cutoff" if touch is not None else
                        "no_pre_exit_touch"),
            "first_touch_at": touch.at if touch else None,
            "fill_at": fill.effective_at.at if fill else None,
            "fill_price": fill.price if fill else None,
            "processed_quote_updates": processed,
            "pending_passive_count": broker.pending_passive_count,
            "position_status": broker.position(ticket).status}


def risk_world(row, position, market, conversion, flags, *, delay_ms,
               price_mode, cutoff_ns, spec):
    result = {"delay_ms": delay_ms, "price_mode": price_mode}
    try:
        fill = simulate_fill(row, position, market, flags, delay_ms=delay_ms,
                             price_mode=price_mode, cutoff_ns=cutoff_ns)
        result.update(fill)
        if fill["status"] != "filled":
            return result
        fill_ns = time_ns(fill["fill_at"])
        money = causal_fill_money(position, fill["fill_price"], fill_ns,
                                  conversion, spec)
        entry = utc(row["native_entry_utc"])
        native_exit = utc(row["native_exit_utc"])
        direction = row["direction"]
        volume = Decimal(str(position["volume"]))
        observed = (
            SequenceEvent(1, "entry", entry, direction,
                          Decimal(str(position["entry_price"])), volume,
                          Decimal(0), "native_entry"),
            SequenceEvent(1, "exit", native_exit, direction,
                          Decimal(str(row["target"])), volume,
                          Decimal(str(position["actual_net_eur"])), "native_tp_deal"),
        )
        simulated = (
            observed[0],
            SequenceEvent(1, "exit", fill["fill_at"], direction,
                          Decimal(str(fill["fill_price"])), volume,
                          money, "hypothetical_passive_tp_fill"),
        )
        market_pair = (market[0], np.column_stack(market[1:]))
        conversion_pair = (conversion[0], np.column_stack(conversion[1:]))
        quotes = paired_quotes(market_pair, conversion_pair,
                               observed + simulated, start=entry,
                               end=max(native_exit, fill["fill_at"]))
        comparison = compare_risk(observed, simulated, quotes, spec=spec)
        result.update(status=("risk_path_compared" if not comparison["blockers"]
                              else "blocked_risk_path"),
                      risk_blockers=comparison["blockers"],
                      native_metrics=comparison["observed"]["metrics"],
                      scenario_metrics=comparison["simulated"]["metrics"],
                      paired_samples=len(comparison["observed"]["samples"]),
                      mismatched_pairs=comparison["mismatched_pairs"],
                      unknown_pairs=comparison["unknown_pairs"],
                      max_abs_total_difference_eur=comparison[
                          "max_abs_total_difference"],
                      native_path_sha256=hashlib.sha256(
                          encode(comparison["observed"]["samples"])).hexdigest(),
                      scenario_path_sha256=hashlib.sha256(
                          encode(comparison["simulated"]["samples"])).hexdigest(),
                      scenario_booked_eur=money,
                      scenario_minus_native_exit_ms=int((fill["fill_at"]
                          - native_exit).total_seconds() * 1000))
    except (ValueError, KeyError, TypeError) as exc:
        result.update(status="blocked_world", blockers=[str(exc)])
    return result


def audit(population_path, money_path, broker_path, native_path, root, output):
    started = time.monotonic()
    population_path, money_path, broker_path, native_path, root, output = (
        Path(path).resolve() for path in
        (population_path, money_path, broker_path, native_path, root, output))
    if output.exists():
        raise ValueError("immutable output already exists")
    population, money, broker, native = [
        json.loads(path.read_text(encoding="utf-8")) for path in
        (population_path, money_path, broker_path, native_path)]
    if (population.get("contract") != "native_gold_tp_terminal_touch_population_v1"
            or population.get("positions") != len(population.get("rows", []))
            or not 1 <= population["positions"] <= MAX_POSITIONS
            or money.get("account_currency") != "EUR"
            or broker["account"]["currency"] != "EUR"
            or broker["conversion"]["max_quote_age_ms"] != 5_000):
        raise ValueError("scenario risk input contract differs")
    for name, expected in population["sources"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"native TP population source changed: {name}")
    if (population["sources"].get(str(money_path)) != digest(money_path)
            or population["sources"].get(str(native_path)) != digest(native_path)
            or native["inputs_sha256"].get(str(broker_path.relative_to(PROJECT)))
            != digest(broker_path)):
        raise ValueError("money or broker contract source not bound")
    positions = {row["position_id"]: row for row in money["positions"]}
    if len(positions) != len(money["positions"]):
        raise ValueError("duplicate native position")
    accepted_rows = [row for row in population["rows"]
                     if row.get("receipt_status")
                     == "client_target_accepted_response_before_native_exit"]
    if not accepted_rows:
        raise ValueError("empty declared accepted-TP cohort")
    spec = RiskSpec("EUR", broker["account"]["currency_digits"],
                    Decimal(str(broker["instrument"]["contract_size"])),
                    broker["conversion"]["orientation"],
                    broker["conversion"]["max_quote_age_ms"], 5_000)
    tapes, proofs = {}, {}
    for day in sorted({row["day"] for row in accepted_rows}):
        scoped = [row for row in accepted_rows if row["day"] == day]
        start_ns = min(time_ns(utc(row["native_entry_utc"])) for row in scoped)
        cutoff_ns = (max(time_ns(utc(row["native_exit_utc"])) for row in scoped)
                     + 31_000_000_000)
        market, xau_proof = load_day(root, "XAUUSD", day,
                                     start_ns=start_ns, cutoff_ns=cutoff_ns,
                                     offset_seconds=OFFSET_SECONDS,
                                     initial_padding_ns=5_000_000_000)
        conversion, fx_proof = load_day(root, "EURUSD", day,
                                        start_ns=start_ns, cutoff_ns=cutoff_ns,
                                        offset_seconds=OFFSET_SECONDS,
                                        initial_padding_ns=5_000_000_000)
        if (xau_proof["parquet_sha256"] != population["tapes"][day]["parquet_sha256"]
                or fx_proof["parquet_sha256"] != native["inputs_sha256"].get(
                    str(Path("runtime_data/week_native_ticks_20260923_v1/EURUSD")
                        / f"{day}.parquet"))):
            raise ValueError("scenario tape differs from native evidence")
        frame = pd.read_parquet(root / "XAUUSD" / f"{day}.parquet",
                                columns=["time_msc", "flags"])
        stamps = (frame.time_msc.to_numpy(dtype=np.int64)
                  - OFFSET_SECONDS * 1000) * 1_000_000
        selected = ((stamps >= start_ns - 5_000_000_000) & (stamps < cutoff_ns))
        if not np.array_equal(stamps[selected], market[0]):
            raise ValueError("scenario side flags differ from XAU tape")
        tapes[day] = (market, conversion,
                      frame["flags"].to_numpy(dtype=np.uint32)[selected],
                      cutoff_ns)
        proofs[day] = {"market": xau_proof, "conversion": fx_proof}
    rows = []
    for source in population["rows"]:
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("scenario risk wall budget exceeded")
        row = {"signal_id": source["signal_id"],
               "position_id": source["position_id"],
               "native_exit_reason": source["native_exit_reason"]}
        if source["status"] == "non_tp_exit":
            row["status"] = "non_tp_exit"
        elif source.get("receipt_status") != "client_target_accepted_response_before_native_exit":
            row["status"] = "blocked_missing_accepted_tp_response"
        else:
            position = positions.get(source["position_id"])
            if (position is None or position["signal_id"] != source["signal_id"]
                    or position["direction"] != source["direction"]
                    or position["status"] != "exact_exit_money"
                    or Decimal(str(position["delta_eur"])) != 0
                    or Decimal(str(position["costs_eur"])) != 0):
                raise ValueError("scenario native position identity or money differs")
            market, conversion, flags, day_cutoff_ns = tapes[source["day"]]
            cutoff_ns = time_ns(utc(source["native_exit_utc"])) + 31_000_000_000
            if cutoff_ns > day_cutoff_ns:
                raise ValueError("scenario case cutoff beyond loaded tape")
            worlds = [risk_world(source, position, market, conversion, flags,
                                 delay_ms=delay_ms, price_mode=price_mode,
                                 cutoff_ns=cutoff_ns, spec=spec)
                      for delay_ms in DELAYS_MS for price_mode in PRICE_MODES]
            if len(worlds) != len(DELAYS_MS) * len(PRICE_MODES):
                raise ValueError("scenario world denominator changed")
            row.update(status="scenario_grid_evaluated", worlds=worlds)
        rows.append(row)
    if len(rows) != population["positions"]:
        raise ValueError("scenario population denominator changed")
    watched = {str(path): digest(path) for path in (
        population_path, money_path, broker_path, native_path, Path(__file__),
        PROJECT / "research/dubai_iterative/broker_execution.py",
        PROJECT / "research/dubai_iterative/passive_fill_contract.py",
        PROJECT / "research/risk_trajectory.py", PROJECT / "broker_money.py",
        PROJECT / "tools/compare_risk_trajectories.py")}
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("scenario risk source changed during run")
    world_statuses = Counter(world["status"] for row in rows
                             for world in row.get("worlds", []))
    report = {"contract": "conditioned_tp_fill_scenario_risk_v1",
              "status": "diagnostic_only", "full_live_parity_verified": False,
              "strategy_decisions_replayed": False,
              "population_positions": population["positions"],
              "accepted_response_cases": len(accepted_rows),
              "worlds_per_accepted_case": len(DELAYS_MS) * len(PRICE_MODES),
              "delays_ms": DELAYS_MS, "price_modes": PRICE_MODES,
              "rows": rows, "statuses": dict(Counter(row["status"] for row in rows)),
              "world_statuses": dict(world_statuses),
              "risk_spec": {"currency": spec.currency,
                            "digits": spec.currency_digits,
                            "contract_size": str(spec.contract_size),
                            "orientation": spec.orientation,
                            "max_fx_age_ms": spec.max_fx_age_ms,
                            "max_market_gap_ms": spec.max_market_gap_ms},
              "sources": watched, "tapes": proofs,
              "limitations": ["World delays and prices are stress hypotheses, not broker estimates.",
                              "Native entries fixed; later strategy decisions are not rerun.",
                              "Account EUR paths are per-position, not shared-account equity.",
                              "A terminal first touch is not a verified broker-server trigger.",
                              "Discovery cohort is retrospective, not untouched validation."],
              "elapsed_seconds": time.monotonic() - started}
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--broker", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.population, args.money, args.broker, args.native,
                   args.root, args.output)
    print({"output": str(args.output), "statuses": report["statuses"],
           "world_statuses": report["world_statuses"]})


if __name__ == "__main__":
    main()
