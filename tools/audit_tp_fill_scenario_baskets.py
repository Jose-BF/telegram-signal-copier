"""Compare complete native Gold baskets against predeclared TP fill worlds."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from research.causal_replay import time_ns, utc
from research.risk_trajectory import RiskSpec, compare_risk
from tools.compare_risk_trajectories import paired_quotes
from tools.compare_week_causal_sequences import _observed
from tools.probe_canal1_incremental_window import digest, load_day
from tools.run_causal_controls import encode, save


PROJECT = Path(__file__).resolve().parents[1]
OFFSET_SECONDS = 10_800
MAX_SECONDS = 300


def replace_exits(native, positions, worlds):
    """Substitute exactly one full native TP exit per position identity."""
    replaced = list(native)
    used = set()
    for position in positions:
        pid = position["position_id"]
        world = worlds[pid]
        if world["status"] != "risk_path_compared":
            raise ValueError(f"scenario risk unavailable for position {pid}")
        native_exit_ns = (int(position["exit_msc"]) - OFFSET_SECONDS * 1_000) * 1_000_000
        candidates = [index for index, event in enumerate(native)
                      if event.kind == "exit" and time_ns(event.at) == native_exit_ns
                      and event.direction == position["direction"]
                      and event.price == Decimal(str(position["exit_price"]))
                      and event.volume == Decimal(str(position["volume"]))
                      and event.money == Decimal(str(position["actual_net_eur"]))]
        if len(candidates) != 1 or candidates[0] in used:
            raise ValueError(f"native TP exit identity ambiguous for position {pid}")
        index = candidates[0]
        used.add(index)
        replaced[index] = replace(native[index], at=utc(world["fill_at"]),
                                  price=Decimal(str(world["fill_price"])),
                                  money=Decimal(str(world["scenario_booked_eur"])),
                                  mechanism="hypothetical_passive_tp")
    if len(used) != len(positions) or len(used) != sum(e.kind == "exit" for e in native):
        raise ValueError("basket TP denominator differs from native exits")
    return tuple(replaced)


def audit(scenarios_path, money_path, deals_path, native_path, broker_path,
          root, output):
    started = time.monotonic()
    scenarios_path, money_path, deals_path, native_path, broker_path, root, output = (
        Path(path).resolve() for path in
        (scenarios_path, money_path, deals_path, native_path, broker_path, root, output))
    if output.exists():
        raise ValueError("immutable output already exists")
    scenarios, money, deals, native, broker = [
        json.loads(path.read_text(encoding="utf-8")) for path in
        (scenarios_path, money_path, deals_path, native_path, broker_path)]
    if (scenarios.get("contract") != "conditioned_tp_fill_scenario_risk_v1"
            or scenarios.get("population_positions") != 46
            or scenarios.get("accepted_response_cases") != 9
            or scenarios.get("worlds_per_accepted_case") != 10
            or scenarios.get("world_statuses") != {"risk_path_compared": 90}
            or len(scenarios.get("rows", [])) != 46
            or money.get("contract") != "native_closed_money_anchor_v2"
            or native.get("contract") != "native_week_full_tick_risk_diagnostic_v1"
            or broker["account"]["currency"] != "EUR"):
        raise ValueError("basket scenario input contract differs")
    for name, expected in scenarios["sources"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"basket scenario source changed: {name}")
    if (scenarios["sources"].get(str(money_path)) != digest(money_path)
            or scenarios["sources"].get(str(native_path)) != digest(native_path)
            or scenarios["sources"].get(str(broker_path)) != digest(broker_path)
            or native["inputs_sha256"].get(str(deals_path.relative_to(PROJECT)))
            != digest(deals_path)):
        raise ValueError("basket native money or deals source not bound")
    by_signal = defaultdict(list)
    for position in money["positions"]:
        by_signal[position["signal_id"]].append(position)
    scenario_rows = [row for row in scenarios["rows"] if row.get("worlds")]
    signals = sorted({row["signal_id"] for row in scenario_rows})
    if len(signals) != 3 or sum(len(by_signal[s]) for s in signals) != 9:
        raise ValueError("three complete native basket identities required")
    native_baskets = {row["signal_id"]: row for row in native["baskets"]}
    spec = RiskSpec("EUR", broker["account"]["currency_digits"],
                    Decimal(str(broker["instrument"]["contract_size"])),
                    broker["conversion"]["orientation"],
                    broker["conversion"]["max_quote_age_ms"], 5_000)
    if spec != RiskSpec("EUR", 2, Decimal("100"), "account_base_profit_quote", 5_000, 5_000):
        raise ValueError("basket risk spec differs from scenario contract")
    if scenarios["risk_spec"] != {
        "currency": spec.currency, "digits": spec.currency_digits,
        "contract_size": str(spec.contract_size), "orientation": spec.orientation,
        "max_fx_age_ms": spec.max_fx_age_ms,
        "max_market_gap_ms": spec.max_market_gap_ms,
    }:
        raise ValueError("basket and position risk spec differ")
    day = "2026-09-18"
    if any(row["signal_id"] not in signals for row in scenario_rows):
        raise ValueError("scenario basket grouping differs")
    events_by_signal = {signal: tuple(_observed(signal, by_signal[signal], deals))
                        for signal in signals}
    start_ns = min(time_ns(event.at) for events in events_by_signal.values()
                   for event in events if event.kind == "entry")
    cutoff_ns = max(time_ns(event.at) for events in events_by_signal.values()
                    for event in events if event.kind == "exit") + 31_000_000_000
    market, market_proof = load_day(root, "XAUUSD", day, start_ns=start_ns,
                                     cutoff_ns=cutoff_ns, offset_seconds=OFFSET_SECONDS,
                                     initial_padding_ns=5_000_000_000)
    conversion, fx_proof = load_day(root, "EURUSD", day, start_ns=start_ns,
                                     cutoff_ns=cutoff_ns, offset_seconds=OFFSET_SECONDS,
                                     initial_padding_ns=5_000_000_000)
    if (market_proof["parquet_sha256"] != scenarios["tapes"][day]["market"]["parquet_sha256"]
            or fx_proof["parquet_sha256"] != scenarios["tapes"][day]["conversion"]["parquet_sha256"]):
        raise ValueError("basket tape differs from scenario source")
    market_pair = (market[0], np.column_stack(market[1:]))
    conversion_pair = (conversion[0], np.column_stack(conversion[1:]))
    modes = [(delay, mode) for delay in scenarios["delays_ms"]
             for mode in scenarios["price_modes"]]
    rows = []
    for signal in signals:
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("basket scenario wall budget exceeded")
        positions = by_signal[signal]
        native_events = events_by_signal[signal]
        source_rows = [row for row in scenario_rows if row["signal_id"] == signal]
        source_positions = {row["position_id"]: row for row in source_rows}
        if (len(source_rows) != len(positions)
                or set(source_positions) != {row["position_id"] for row in positions}
                or any(len(row["worlds"]) != len(modes) for row in source_rows)):
            raise ValueError("basket scenario position coverage incomplete")
        anchor = native_baskets[signal]
        if (not anchor["direct_clock_anchor_for_all_event_days"]
                or not anchor["strict_causal_fx_path_complete"]
                or anchor["position_count"] != len(positions)):
            raise ValueError("basket native baseline not strictly anchored")
        worlds = []
        for world_index, (delay, mode) in enumerate(modes):
            selected = {pid: row["worlds"][world_index]
                        for pid, row in source_positions.items()}
            if any((world["delay_ms"], world["price_mode"]) != (delay, mode)
                   for world in selected.values()):
                raise ValueError("basket scenario grid order differs")
            simulated = replace_exits(native_events, positions, selected)
            first = min(event.at for event in native_events)
            last = max(event.at for event in native_events + simulated)
            quotes = paired_quotes(market_pair, conversion_pair,
                                   native_events + simulated, start=first, end=last)
            comparison = compare_risk(native_events, simulated, quotes, spec=spec)
            observed = comparison["observed"]
            if observed["metrics"] is not None:
                for metric in ("final_net", "max_drawdown", "max_gross_volume"):
                    if str(observed["metrics"][metric]) != str(anchor["path"]["metrics"][metric]):
                        raise ValueError(f"basket native baseline {metric} differs: {signal}")
            worlds.append({
                "delay_ms": delay, "price_mode": mode,
                "status": "risk_path_compared" if not comparison["blockers"] else "blocked_risk_path",
                "risk_blockers": comparison["blockers"],
                "native_metrics": observed["metrics"],
                "scenario_metrics": comparison["simulated"]["metrics"],
                "paired_samples": len(observed["samples"]),
                "mismatched_pairs": comparison["mismatched_pairs"],
                "unknown_pairs": comparison["unknown_pairs"],
                "first_divergence": comparison["first_divergence"],
                "max_abs_total_difference_eur": comparison["max_abs_total_difference"],
                "native_path_sha256": hashlib.sha256(encode(observed["samples"])).hexdigest(),
                "scenario_path_sha256": hashlib.sha256(
                    encode(comparison["simulated"]["samples"])).hexdigest(),
            })
        rows.append({"signal_id": signal, "position_count": len(positions),
                     "worlds": worlds})
    watched = {str(path): digest(path) for path in (
        scenarios_path, money_path, deals_path, native_path, broker_path,
        Path(__file__), PROJECT / "research/risk_trajectory.py",
        PROJECT / "tools/compare_risk_trajectories.py",
        PROJECT / "tools/compare_week_causal_sequences.py",
        PROJECT / "tools/probe_canal1_incremental_window.py")}
    if any(digest(Path(name)) != expected for name, expected in watched.items()):
        raise ValueError("basket scenario source changed during run")
    report = {
        "contract": "conditioned_tp_fill_basket_risk_v1", "status": "diagnostic_only",
        "full_live_parity_verified": False, "strategy_decisions_replayed": False,
        "source_population_positions": scenarios["population_positions"],
        "admitted_baskets": len(rows), "admitted_positions": len(scenario_rows),
        "blocked_missing_accepted_tp_response": scenarios["statuses"].get(
            "blocked_missing_accepted_tp_response", 0),
        "non_tp_exit": scenarios["statuses"].get("non_tp_exit", 0),
        "worlds_per_basket": len(modes), "rows": rows,
        "world_statuses": dict(Counter(world["status"] for row in rows
                                       for world in row["worlds"])),
        "sources": watched, "tapes": {"market": market_proof,
                                       "conversion": fx_proof},
        "limitations": [
            "Native entries and all non-TP decisions are fixed; this is not a strategy replay.",
            "World delays and prices are stress hypotheses, not broker estimates.",
            "Basket equity is modeled from terminal quotes and EURUSD, not observed MT5 equity.",
            "The 36 TP without accepted target receipts remain blocked outside these baskets.",
            "The cohort is retrospective discovery, not untouched validation.",
        ],
        "elapsed_seconds": time.monotonic() - started,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("scenarios", "money", "deals", "native", "broker", "root", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.scenarios, args.money, args.deals, args.native,
                   args.broker, args.root, args.output)
    print({"output": str(args.output), "baskets": report["admitted_baskets"],
           "world_statuses": report["world_statuses"]})


if __name__ == "__main__":
    main()
