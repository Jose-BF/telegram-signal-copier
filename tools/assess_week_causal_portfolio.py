"""Reconstruct shared-account risk from a frozen, independent weekly replay.

This is a counterfactual diagnostic, not an observed MT5 equity or a live
strategy verdict. Any incomplete per-signal scenario blocks its account path.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.causal_replay import compile_signals, make_path, time_ns, utc
from research.dubai_iterative.engine import (EntryRecord, ExecutionAssumptions,
                                             ExitRecord, SimulationResult)
from research.dubai_iterative.portfolio import PortfolioAssessment, reconstruct_portfolio
from tools import run_causal_controls as causal
from tools import run_week_causal_controls as weekly
from tools.audit_native_money_anchor import digest


CONTRACT = "weekly_raw_causal_portfolio_diagnostic_v1"
MAX_TOTAL_PATH_ROWS = 12_000_000
MAX_WALL_SECONDS = 1_800


def _money(value):
    return None if value is None else Decimal(str(value))


def _result_object(value):
    entries = tuple(EntryRecord(**{**entry, "opened_at": utc(entry["opened_at"])})
                    for entry in value["entries"])
    exits = tuple(ExitRecord(**{**exit_row, "closed_at": utc(exit_row["closed_at"]),
                                "pnl_eur": _money(exit_row["pnl_eur"])})
                  for exit_row in value["exits"])
    return SimulationResult(
        signal_id=value["signal_id"],
        strategy_fingerprint=value["strategy_fingerprint"],
        confidence_layer=value["confidence_layer"],
        entries=entries, exits=exits, pnl_eur=_money(value["pnl_eur"]),
        exit_reason=value["exit_reason"],
        max_favourable_eur=_money(value["max_favourable_eur"]),
        max_adverse_eur=_money(value["max_adverse_eur"]),
        max_floating_drawdown_eur=_money(value["max_floating_drawdown_eur"]),
        max_favourable_move=float(value["max_favourable_move"]),
        max_adverse_move=float(value["max_adverse_move"]),
        blockers=tuple(value["blockers"]),
        last_tick_index=int(value["last_tick_index"]),
        unfilled=bool(value["unfilled"]),
        filled_volume=float(value["filled_volume"]),
        behavior_digest=value.get("behavior_digest"),
    )


def _validate(protocol, assembled, protocol_hash):
    if (protocol.get("contract") != weekly.CONTRACT
            or protocol.get("implementation") != weekly._identity()
            or protocol.get("risk_scope") != weekly.RISK_SCOPE
            or protocol.get("shared_account_equity_reconstructed") is not False
            or protocol.get("execution_scenarios") != list(weekly.SCENARIOS)
            or protocol.get("max_entry_age_s") != weekly.WEEKLY_ENTRY_MAX_AGE_S
            or protocol.get("search_candidate_budget") != 0
            or assembled.get("contract") != "weekly_raw_causal_control_results_v1"
            or assembled.get("protocol_sha256") != protocol_hash
            or assembled.get("risk_scope") != weekly.RISK_SCOPE
            or assembled.get("shared_account_equity_reconstructed") is not False
            or assembled.get("full_live_parity_verified") is not False
            or assembled.get("execution_money_assumptions")
               != protocol.get("execution_money_assumptions")
            or assembled.get("signal_ids") != protocol.get("raw_signal_ids")
            or assembled.get("scenario_count") != len(weekly.SCENARIOS)
            or assembled.get("signal_count") != len(protocol.get("raw_signal_ids", []))):
        raise ValueError("weekly portfolio source identity mismatch")
    if causal.encode(protocol.get("genomes")) != causal.encode({
            channel: asdict(policy) for channel, policy in weekly.policies().items()}):
        raise ValueError("weekly portfolio control policies changed")
    ids = protocol["raw_signal_ids"]
    rows = assembled["results"]
    expected = {(signal, weekly._scenario_key(scenario))
                for signal in ids for scenario in weekly.SCENARIOS}
    actual = [(row["signal_id"], weekly._scenario_key(row))
              for row in rows]
    counts = dict(sorted(Counter(row["status"] for row in rows).items()))
    if (len(ids) > weekly.MAX_SIGNALS or len(set(ids)) != len(ids)
            or len(actual) != len(expected) or len(set(actual)) != len(actual)
            or set(actual) != expected or assembled.get("row_status_counts") != counts
            or assembled.get("coverage_status") != weekly._coverage_status(counts)
            or assembled.get("censored_row_count") != sum(
                row.get("censored_data_end") is True for row in rows)):
        raise ValueError("weekly portfolio result matrix incomplete")
    for name, sha in protocol["input_paths_sha256"].items():
        if digest(name) != sha:
            raise ValueError("frozen weekly portfolio input changed")
    for name, sha in assembled["batch_inputs_sha256"].items():
        if digest(name) != sha:
            raise ValueError("frozen weekly portfolio batch changed")
    expected_batches = (len(ids) + weekly.BATCH_SIZE - 1) // weekly.BATCH_SIZE
    if (assembled.get("batch_count") != expected_batches
            or len(assembled["batch_inputs_sha256"]) != expected_batches):
        raise ValueError("weekly portfolio batch denominator incomplete")
    fragments = {}
    for name in assembled["batch_inputs_sha256"]:
        fragment = weekly._read(name)
        index = fragment.get("batch_index")
        if (type(index) is not int or index in fragments
                or not 0 <= index < expected_batches
                or fragment.get("protocol_sha256") != protocol_hash
                or fragment.get("contract") != "weekly_raw_causal_control_batch_v1"):
            raise ValueError("weekly portfolio batch identity mismatch")
        selected, _ = weekly._batch_ids(ids, index)
        if fragment.get("signal_ids") != selected:
            raise ValueError("weekly portfolio batch signal identity mismatch")
        fragments[index] = fragment["results"]
    if (set(fragments) != set(range(expected_batches))
            or causal.encode([row for index in range(expected_batches)
                              for row in fragments[index]]) != causal.encode(rows)):
        raise ValueError("weekly portfolio assembled rows differ from frozen batches")
    for items in protocol["tapes"].values():
        for item in items:
            if (digest(item["meta"]) != item["meta_sha256"]
                    or digest(item["data"]) != item["data_sha256"]):
                raise ValueError("frozen weekly portfolio tape changed")


def _raw_signals(protocol):
    sources = [Path(name) for name in protocol["input_paths_sha256"]
               if weekly._read(name).get("contract") == "frozen_raw_telegram_slice_v1"]
    if len(sources) != 1:
        raise ValueError("weekly portfolio raw message source is not unique")
    raw = weekly._read(sources[0])
    signals, diagnostics = compile_signals(
        raw["rows"], start=utc(protocol["start_utc"]),
        cutoff=utc(protocol["end_utc"]), sticker_directions=causal.STICKERS,
        max_entry_age_s=weekly.WEEKLY_ENTRY_MAX_AGE_S)
    if ([row.signal_id for row in signals] != protocol["raw_signal_ids"]
            or causal.encode(diagnostics) != causal.encode(protocol["raw_diagnostics"])):
        raise ValueError("weekly portfolio raw signal universe changed")
    return {row.signal_id: row for row in signals}


def _row_kind(row):
    if row.get("status") == "diagnostic_only":
        return "complete"
    result = row.get("result")
    if (row.get("status") == "blocked_simulation"
            and row.get("censored_data_end") is True
            and isinstance(result, dict)
            and tuple(result.get("blockers", ())) == ("path_ended_before_strategy_exit",)
            and any(item.get("reason") == "data_end" for item in result.get("exits", []))):
        return "terminal_mark"
    return "blocked"


def _scenario_report(protocol, rows, signals, market, conversion, tape_sha):
    missing = [{"signal_id": row["signal_id"], "status": row["status"]}
               for row in rows if _row_kind(row) == "blocked"]
    if missing:
        return {"status": "blocked_prior_stage", "excluded_signals": missing,
                "account": None, "filled_signal_count": 0,
                "zero_fill_signal_ids": [], "censored_terminal_signal_ids": [],
                "hypothetical_exit_complete": False,
                "not_assessed_signal_ids": [row["signal_id"] for row in rows
                                            if _row_kind(row) != "blocked"]}
    results, paths, zero_fill, terminal_ids, path_rows = [], [], [], [], 0
    policy = weekly.policies()
    began = time.monotonic()
    for row in rows:
        signal = signals[row["signal_id"]]
        result = _result_object(row["result"])
        terminal_mark = _row_kind(row) == "terminal_mark"
        genome = policy[signal.channel]
        if (result.signal_id != signal.signal_id
                or result.strategy_fingerprint != genome.fingerprint
                or result.blockers != (("path_ended_before_strategy_exit",) if terminal_mark else ())
                or result.pnl_eur is None):
            raise ValueError("weekly portfolio result identity or money missing")
        if terminal_mark:
            terminal_ids.append(signal.signal_id)
            result = replace(result, blockers=())
        if not result.entries:
            if result.exits or result.pnl_eur != 0 or not result.unfilled:
                raise ValueError("weekly portfolio zero-fill result inconsistent")
            zero_fill.append(signal.signal_id)
            continue
        if not result.exits:
            raise ValueError("weekly portfolio open position was marked complete")
        cutoff = max(exit_row.closed_at for exit_row in result.exits)
        start_index = int(np.searchsorted(market[0], time_ns(signal.observed_at), side="left"))
        end_index = int(np.searchsorted(market[0], time_ns(cutoff), side="right"))
        path_rows += max(0, end_index - start_index)
        if path_rows > MAX_TOTAL_PATH_ROWS:
            return {"status": "blocked_path_budget", "excluded_signals": [],
                    "account": None, "filled_signal_count": len(paths),
                    "zero_fill_signal_ids": zero_fill, "path_rows": path_rows,
                    "censored_terminal_signal_ids": terminal_ids,
                    "hypothetical_exit_complete": False,
                    "not_assessed_signal_ids": [item["signal_id"] for item in rows]}
        if time.monotonic() - began > MAX_WALL_SECONDS:
            return {"status": "blocked_time_budget", "excluded_signals": [],
                    "account": None, "filled_signal_count": len(paths),
                    "zero_fill_signal_ids": zero_fill, "path_rows": path_rows,
                    "censored_terminal_signal_ids": terminal_ids,
                    "hypothetical_exit_complete": False,
                    "not_assessed_signal_ids": [item["signal_id"] for item in rows]}
        path = make_path(signal, genome, market=market,
                         conversion=conversion, cutoff=cutoff,
                         contract_size=protocol["contract_size"],
                         currency_digits=protocol["currency_digits"],
                         max_fx_age_ms=protocol["fx_max_age_ms"],
                         market_sha256=tape_sha["XAUUSD"],
                         conversion_sha256=tape_sha["EURUSD"])
        paths.append(path)
        results.append(result)
    if paths:
        execution = ExecutionAssumptions(**{
            name: rows[0][name] for name in weekly.SCENARIO_FIELDS if name in rows[0]})
        account = reconstruct_portfolio(paths, results, execution=execution)
    else:
        zero = Decimal("0.00")
        account = PortfolioAssessment(zero, zero, zero, zero, 0.0, 0, 0, ())
    status = ("blocked_portfolio" if not account.evidence_complete else
              "censored_terminal_mark" if terminal_ids else "diagnostic_only")
    return {"status": status,
            "account": asdict(account), "filled_signal_count": len(paths),
            "zero_fill_signal_ids": zero_fill, "excluded_signals": [],
            "censored_terminal_signal_ids": terminal_ids,
            "hypothetical_exit_complete": account.evidence_complete and not terminal_ids,
            "account_net_interpretation": ("hypothetical_terminal_liquidation_not_realized"
                                           if terminal_ids else "hypothetical_closed_net"),
            "not_assessed_signal_ids": [] if account.evidence_complete else
                                        [item["signal_id"] for item in rows],
            "path_rows": path_rows}


def assess(protocol_path, assembled_path, output_path):
    protocol_path, assembled_path, output_path = map(
        Path, (protocol_path, assembled_path, output_path))
    if output_path.exists():
        raise FileExistsError(output_path)
    protocol_hash, assembled_hash, implementation_hash = (
        digest(protocol_path), digest(assembled_path), digest(__file__))
    protocol, assembled = weekly._read(protocol_path), weekly._read(assembled_path)
    _validate(protocol, assembled, protocol_hash)
    by_scenario = {}
    for row in assembled["results"]:
        key = weekly._scenario_key(row)
        by_scenario.setdefault(key, {})[row["signal_id"]] = row
    eligible = any(all(_row_kind(row) != "blocked" for row in cohort.values())
                   for cohort in by_scenario.values())
    if eligible:
        signals = _raw_signals(protocol)
        market, conversion = (weekly._load_tape(protocol["tapes"][symbol])
                              for symbol in ("XAUUSD", "EURUSD"))
        tape_sha = weekly._tape_sha(protocol["tapes"])
    else:
        signals, market, conversion, tape_sha = {}, None, None, {}
    scenarios = []
    began = time.monotonic()
    for scenario in weekly.SCENARIOS:
        key = weekly._scenario_key(scenario)
        rows = [by_scenario[key][signal_id] for signal_id in protocol["raw_signal_ids"]]
        if time.monotonic() - began > MAX_WALL_SECONDS:
            outcome = {"status": "blocked_time_budget", "account": None,
                       "filled_signal_count": 0, "zero_fill_signal_ids": [],
                       "excluded_signals": [], "censored_terminal_signal_ids": [],
                       "hypothetical_exit_complete": False,
                       "not_assessed_signal_ids": protocol["raw_signal_ids"]}
        else:
            outcome = _scenario_report(protocol, rows, signals, market, conversion, tape_sha)
        scenarios.append({**scenario, **outcome})
    _validate(protocol, assembled, protocol_hash)
    if (digest(protocol_path) != protocol_hash or digest(assembled_path) != assembled_hash
            or digest(__file__) != implementation_hash):
        raise ValueError("weekly portfolio source changed during assessment")
    any_complete = any(row["status"] == "diagnostic_only" for row in scenarios)
    any_censored = any(row["status"] == "censored_terminal_mark" for row in scenarios)
    all_complete = all(row["status"] == "diagnostic_only" for row in scenarios)
    scenario_counts = dict(sorted(Counter(row["status"] for row in scenarios).items()))
    report = {"contract": CONTRACT,
              "status": "diagnostic_only" if all_complete else
                        "partial_censored" if any_censored else "blocked",
              "protocol_sha256": protocol_hash, "assembled_sha256": assembled_hash,
              "implementation_sha256": implementation_hash,
              "signal_count": len(protocol["raw_signal_ids"]),
              "scenario_count": len(scenarios), "scenarios": scenarios,
              "scenario_status_counts": scenario_counts,
              "risk_scope": "hypothetical_shared_account",
              "shared_account_equity_reconstructed": any_complete or any_censored,
              "all_hypothetical_scenarios_closed": all_complete,
              "full_live_parity_verified": False,
              "observed_broker_equity_compared": False,
              "money_assumptions": protocol["execution_money_assumptions"],
              "limitations": [
                  "Complete closes and strictly bounded terminal marks can supply shared-account risk.",
                  "A sole path-ended blocker may supply a terminal mark, never an observed realized close.",
                  "This is hypothetical account equity without observed commissions, swap, rollover or margin.",
                  "Observed MT5 equity and broker execution remain separate comparison gates.",
              ]}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True,
                  default=causal.default, allow_nan=False)
        stream.write("\n")
    return {"signal_count": report["signal_count"],
            "complete_scenarios": sum(row["status"] == "diagnostic_only"
                                      for row in scenarios),
            "censored_risk_scenarios": sum(row["status"] == "censored_terminal_mark"
                                           for row in scenarios),
            "output_sha256": digest(output_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--assembled", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(assess(args.protocol, args.assembled, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
