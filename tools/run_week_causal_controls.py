"""Run bounded week-wide policies from raw Telegram and frozen quotes only.

Observed MT5 deals, shadow states and received-signal controls are not runtime
inputs. A separate comparison must join those outcomes after this run.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.causal_replay import (
    RAW_FIELDS, WEEKLY_ENTRY_MAX_AGE_S, compile_signals, make_path, utc,
)
from tools import run_causal_controls as causal
from tools.audit_native_money_anchor import digest


CONTRACT = "weekly_raw_causal_control_protocol_v1"
MAX_SIGNALS = 64
BATCH_SIZE = 8
MAX_TAPE_ROWS = 5_000_000
MAX_WALL_SECONDS = 1_800
OFFSET_SECONDS = 10_800
SCENARIOS = (*causal.SCENARIOS,
             {"latency_ms": 0, "entry_fill_latency_ms": 0,
              "entry_slippage": 0.10, "exit_slippage": 0.10},
             {"latency_ms": 0, "entry_fill_latency_ms": 0,
              "entry_slippage": 0.50, "exit_slippage": 0.50},
             {"latency_ms": 60_000, "entry_fill_latency_ms": 0},
             {"latency_ms": 180_000, "entry_fill_latency_ms": 0},
             {"latency_ms": 0, "entry_fill_latency_ms": 60_000},
             {"latency_ms": 0, "entry_fill_latency_ms": 180_000})
RISK_SCOPE = "isolated_signal_only"
SCENARIO_FIELDS = ("latency_ms", "entry_fill_latency_ms", "entry_slippage",
                   "exit_slippage", "spread_addition")


def policies():
    controls = causal.policies()
    return {**controls, "canal1": replace(
        controls["canal1"], pending_entry_policy="until_expiry")}


def _scenario_key(row):
    return (row["latency_ms"], row["entry_fill_latency_ms"],
            row.get("entry_slippage", 0.0), row.get("exit_slippage", 0.0),
            row.get("spread_addition", 0.0))


def _coverage_status(counts):
    for status in ("engine_disagreement", "blocked_input", "blocked_simulation",
                   "censored_data_end"):
        if counts.get(status, 0):
            return status
    return "diagnostic_only"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _bound(proof, path, field="inputs_sha256"):
    matches = [sha for name, sha in proof.get(field, {}).items()
               if Path(name).resolve() == Path(path).resolve()]
    if matches != [digest(path)]:
        raise ValueError(f"frozen input hash binding missing: {Path(path).name}")


def _days(anchor):
    scope = anchor["scope"]
    start, end = utc(scope["source_epoch_start"]), utc(scope["source_epoch_end_exclusive"])
    if (not start < end or start.time() != datetime.min.time()
            or end.time() != datetime.min.time() or end - start > timedelta(days=7)):
        raise ValueError("unbounded or unaligned week tape scope")
    return start, end, [start + timedelta(days=index)
                        for index in range((end - start).days)]


def _tape_proofs(raw_dir, anchor, days):
    proofs = {}
    for symbol in ("XAUUSD", "EURUSD"):
        rows = []
        for day in days:
            stem = Path(raw_dir) / symbol / day.date().isoformat()
            meta_path, data_path = stem.with_suffix(".json"), stem.with_suffix(".parquet")
            _bound(anchor, meta_path, "input_sha256")
            _bound(anchor, data_path, "input_sha256")
            meta = _read(meta_path)
            if (meta.get("status") != "raw_reads_consistent"
                    or meta.get("symbol") != symbol
                    or meta.get("source_epoch_day") != day.date().isoformat()
                    or meta.get("sha256") != digest(data_path)
                    or type(meta.get("rows")) is not int or meta["rows"] < 1):
                raise ValueError(f"unverified quote tape day: {symbol} {day.date()}")
            rows.append({"meta": str(meta_path), "meta_sha256": digest(meta_path),
                         "data": str(data_path), "data_sha256": meta["sha256"],
                         "rows": meta["rows"], "day": day.date().isoformat()})
        if sum(row["rows"] for row in rows) > MAX_TAPE_ROWS:
            raise ValueError(f"week quote row budget exceeded: {symbol}")
        proofs[symbol] = rows
    return proofs


def _load_tape(proofs):
    arrays = []
    for item in proofs:
        if (digest(item["meta"]) != item["meta_sha256"]
                or digest(item["data"]) != item["data_sha256"]):
            raise ValueError("frozen quote tape changed")
        frame = pd.read_parquet(item["data"], columns=["time_msc", "bid", "ask"])
        if len(frame) != item["rows"]:
            raise ValueError("quote tape row count changed")
        arrays.append((frame.time_msc.to_numpy(dtype=np.int64),
                       frame.bid.to_numpy(dtype=float), frame.ask.to_numpy(dtype=float)))
    stamps, bids, asks = (np.concatenate([part[index] for part in arrays])
                          for index in range(3))
    if (len(stamps) > MAX_TAPE_ROWS or np.any(np.diff(stamps) < 0)
            or not np.isfinite(bids).all() or not np.isfinite(asks).all()
            or np.any(bids <= 0) or np.any(asks < bids)):
        raise ValueError("invalid week quote ordering or Bid/Ask")
    return (stamps - OFFSET_SECONDS * 1000) * 1_000_000, bids, asks


def _identity():
    return {"existing_control": causal.identity(), "week_runner_sha256": digest(__file__)}


def _tape_sha(tapes):
    return {symbol: hashlib.sha256(json.dumps(
        [(row["day"], row["data_sha256"]) for row in tapes[symbol]],
        separators=(",", ":")).encode()).hexdigest()
        for symbol in ("XAUUSD", "EURUSD")}


def _batch_ids(ids, batch_index):
    count = (len(ids) + BATCH_SIZE - 1) // BATCH_SIZE
    if (not ids or type(batch_index) is not int or not 0 <= batch_index < count):
        raise ValueError("invalid weekly causal batch index")
    return ids[batch_index * BATCH_SIZE:(batch_index + 1) * BATCH_SIZE], count


def prepare(raw_path, readiness_path, anchor_path, contract_path, raw_dir, protocol_path):
    raw_path, readiness_path, anchor_path, contract_path, protocol_path = map(
        Path, (raw_path, readiness_path, anchor_path, contract_path, protocol_path))
    if protocol_path.exists():
        raise FileExistsError(protocol_path)
    raw, readiness, anchor, broker = map(_read, (
        raw_path, readiness_path, anchor_path, contract_path))
    _bound(readiness, raw_path)
    if (readiness.get("contract") != "weekly_raw_causal_input_readiness_v1"
            or readiness.get("status") != "diagnostic_only"
            or readiness.get("all_received_signals_covered") is not True
            or readiness.get("max_entry_age_s") != WEEKLY_ENTRY_MAX_AGE_S
            or readiness.get("frozen_slice_temporal_completeness_proven") is not False
            or raw.get("contract") != "frozen_raw_telegram_slice_v1"
            or raw.get("complete_week_claim") is not False
            or broker["account"]["currency"] != "EUR"
            or broker["account"]["currency_digits"] != 2
            or anchor["scope"]["currency"] != broker["account"]["currency"]
            or anchor["scope"]["server"] != broker["account"]["server"]
            or broker["instrument"]["contract_size"] != 100.0
            or broker["conversion"]["orientation"] != "account_base_profit_quote"
            or broker["conversion"]["max_quote_age_ms"] != 5000):
        raise ValueError("week causal input or money contract not admitted")
    if (raw.get("raw_row_count") != len(raw.get("rows", []))
            or any(not isinstance(row, dict) or set(row) != set(RAW_FIELDS)
                   or row.get("ev") != "telegram_raw" for row in raw["rows"])):
        raise ValueError("noncausal raw fields in week replay input")
    start, end, days = _days(anchor)
    if (utc(readiness["start_utc"]) != start or utc(readiness["end_utc"]) != end
            or utc(raw["start_utc"]) != start or utc(raw["end_utc"]) != end):
        raise ValueError("raw, readiness and quote week windows differ")
    signals, diagnostics = compile_signals(raw["rows"], start=start, cutoff=end,
                                           sticker_directions=causal.STICKERS,
                                           max_entry_age_s=WEEKLY_ENTRY_MAX_AGE_S)
    ids = [row.signal_id for row in signals]
    stale_ids = sorted({f"{row['channel']}_{row['message_id']}" for row in diagnostics
                        if row["reason"] == "stale_entry_candidate"})
    if (not 0 < len(ids) <= MAX_SIGNALS or len(set(ids)) != len(ids)
            or sorted(ids) != readiness["compiled_signal_ids"]
            or stale_ids != readiness.get("stale_entry_candidate_ids")):
        raise ValueError("week causal signal denominator changed or exceeded budget")
    tapes = _tape_proofs(raw_dir, anchor, days)
    clock = {day.date().isoformat(): anchor["independent_clock_evidence"]["days"]
             .get(day.date().isoformat(), {}).get("status", "missing") for day in days}
    protocol = {"contract": CONTRACT, "status": "diagnostic_only",
                "start_utc": start.isoformat(), "end_utc": end.isoformat(),
                "search_candidate_budget": 0, "max_signals": MAX_SIGNALS,
                "batch_size": BATCH_SIZE,
                "max_wall_seconds": MAX_WALL_SECONDS, "max_tape_rows": MAX_TAPE_ROWS,
                "execution_scenarios": SCENARIOS,
                "raw_signal_ids": ids, "raw_diagnostics": diagnostics,
                "max_entry_age_s": WEEKLY_ENTRY_MAX_AGE_S,
                "stale_entry_candidate_ids": stale_ids,
                "raw_semantics_complete": not diagnostics,
                "raw_message_count": len(raw["rows"]),
                "clock_by_source_day": clock,
                "clock_admitted": anchor.get("clock_admitted") is True,
                "all_days_direct_clock": all(value == "direct_anchor_available"
                                             for value in clock.values()),
                "broker_epoch_offset_seconds_hypothesis": OFFSET_SECONDS,
                "input_paths_sha256": {str(path): digest(path) for path in (
                    raw_path, readiness_path, anchor_path, contract_path)},
                "tapes": tapes, "implementation": _identity(),
                "genomes": {channel: asdict(policy) for channel, policy in policies().items()},
                "account_currency": "EUR", "contract_size": 100.0,
                "currency_digits": broker["account"]["currency_digits"],
                "fx_max_age_ms": 5000,
                "execution_money_assumptions": {
                    "commission": "hypothetical_zero",
                    "swap": "not_modeled",
                    "rollover": "not_admitted",
                    "margin": "not_modeled",
                },
                "full_live_parity_verified": False,
                "independent_account_equity_verified": False,
                "risk_scope": RISK_SCOPE,
                "shared_account_equity_reconstructed": False,
                "limitations": [
                    "Frozen byte-slice coverage is not complete-week Telegram coverage proof.",
                    "Broker clock is a declared hypothesis on source days without direct anchor.",
                    "Alternative broker fills, costs, queues and account equity are not observed by this runner.",
                    "Per-signal drawdown is not the drawdown of overlapping signals in one account.",
                ]}
    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    with protocol_path.open("x", encoding="utf-8") as stream:
        json.dump(protocol, stream, indent=2, sort_keys=True, default=str, allow_nan=False)
        stream.write("\n")
    return {"signals": len(ids), "batches": (len(ids) + BATCH_SIZE - 1) // BATCH_SIZE,
            "raw_messages": len(raw["rows"]),
            "all_days_direct_clock": protocol["all_days_direct_clock"]}


def run(protocol_path, output_path, *, batch_index=None):
    from research.dubai_iterative.engine import ExecutionAssumptions, simulate
    from research.dubai_iterative.fast_engine import FastEvaluator
    from research.dubai_iterative.oracle import ExecutionScenario, oracle_simulate

    protocol_path, output_path = Path(protocol_path), Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    protocol_hash = digest(protocol_path)
    protocol = _read(protocol_path)
    if (protocol.get("contract") != CONTRACT
            or protocol.get("implementation") != _identity()
            or protocol.get("execution_scenarios") != list(SCENARIOS)
            or protocol.get("max_signals") != MAX_SIGNALS
            or protocol.get("batch_size") != BATCH_SIZE
            or protocol.get("max_wall_seconds") != MAX_WALL_SECONDS
            or protocol.get("risk_scope") != RISK_SCOPE
            or protocol.get("shared_account_equity_reconstructed") is not False
            or protocol.get("search_candidate_budget") != 0):
        raise ValueError("weekly causal protocol changed")
    if protocol.get("max_entry_age_s") != WEEKLY_ENTRY_MAX_AGE_S:
        raise ValueError("weekly causal entry-age guard changed")
    if (protocol.get("execution_money_assumptions") != {
            "commission": "hypothetical_zero", "swap": "not_modeled",
            "rollover": "not_admitted", "margin": "not_modeled"}):
        raise ValueError("weekly money assumptions changed")
    for name, sha in protocol["input_paths_sha256"].items():
        if digest(name) != sha:
            raise ValueError("frozen weekly causal input changed")
    raw_paths = [Path(name) for name in protocol["input_paths_sha256"]
                 if _read(name).get("contract") == "frozen_raw_telegram_slice_v1"]
    if len(raw_paths) != 1:
        raise ValueError("frozen raw message source is not unique")
    raw_path = raw_paths[0]
    raw = _read(raw_path)
    signals, diagnostics = compile_signals(raw["rows"],
        start=utc(protocol["start_utc"]), cutoff=utc(protocol["end_utc"]),
        sticker_directions=causal.STICKERS,
        max_entry_age_s=WEEKLY_ENTRY_MAX_AGE_S)
    if [row.signal_id for row in signals] != protocol["raw_signal_ids"] or len(signals) > MAX_SIGNALS:
        raise ValueError("weekly raw signal universe changed")
    if causal.encode(diagnostics) != causal.encode(protocol["raw_diagnostics"]):
        raise ValueError("weekly raw diagnostics changed")
    if causal.encode(protocol["genomes"]) != causal.encode({
            channel: asdict(policy) for channel, policy in policies().items()}):
        raise ValueError("weekly frozen policy changed")
    if batch_index is None and len(signals) > BATCH_SIZE:
        raise ValueError("weekly run requires explicit batch index")
    batch_index = 0 if batch_index is None else batch_index
    selected_ids, expected_batch_count = _batch_ids(protocol["raw_signal_ids"], batch_index)
    selected_signals = [row for row in signals if row.signal_id in set(selected_ids)]
    if [row.signal_id for row in selected_signals] != selected_ids:
        raise ValueError("weekly batch signal order changed")
    market, conversion = (_load_tape(protocol["tapes"][symbol])
                          for symbol in ("XAUUSD", "EURUSD"))
    tape_sha = _tape_sha(protocol["tapes"])
    results, began = [], time.monotonic()
    for signal in selected_signals:
        genome = policies()[signal.channel]
        try:
            path = make_path(signal, genome, market=market, conversion=conversion,
                cutoff=utc(protocol["end_utc"]), contract_size=protocol["contract_size"],
                currency_digits=protocol["currency_digits"],
                max_fx_age_ms=protocol["fx_max_age_ms"],
                market_sha256=tape_sha["XAUUSD"], conversion_sha256=tape_sha["EURUSD"])
        except ValueError as exc:
            results.extend({"signal_id": signal.signal_id, **scenario,
                            "status": "blocked_input", "blocker": str(exc)}
                           for scenario in SCENARIOS)
            continue
        for scenario in SCENARIOS:
            if time.monotonic() - began > MAX_WALL_SECONDS:
                raise TimeoutError("weekly causal engine budget exhausted; no final report")
            scalar = asdict(simulate(path, genome, execution=ExecutionAssumptions(**scenario)))
            fast = asdict(FastEvaluator(execution=ExecutionAssumptions(**scenario))(path, genome))
            oracle = asdict(oracle_simulate(path, genome, execution=ExecutionScenario(**scenario)))
            mismatches = {"fast": [key for key in oracle if fast[key] != oracle[key]],
                          "scalar": [key for key in oracle if scalar[key] != oracle[key]]}
            censored = any(row["reason"] == "data_end" for row in scalar["exits"])
            results.append({"signal_id": signal.signal_id, **scenario,
                            "status": "engine_disagreement" if any(mismatches.values())
                            else "blocked_simulation" if scalar["blockers"]
                            else "censored_data_end" if censored
                            else "diagnostic_only", "result": scalar,
                            "fast": fast, "oracle": oracle,
                            "censored_data_end": censored,
                            "engine_mismatches": mismatches,
                            "invalid_fx_ticks": int((~path.fx_valid).sum())})
    if len(results) != len(selected_signals) * len(SCENARIOS):
        raise ValueError("weekly causal result denominator mismatch")
    if (digest(protocol_path) != protocol_hash or protocol["implementation"] != _identity()
            or any(digest(name) != sha for name, sha in protocol["input_paths_sha256"].items())
            or any(digest(item["data"]) != item["data_sha256"]
                   or digest(item["meta"]) != item["meta_sha256"]
                   for rows in protocol["tapes"].values() for item in rows)):
        raise ValueError("weekly causal sources changed during execution")
    forbidden = [name for name in sys.modules if name in {
        "MetaTrader5", "listener", "executor", "gold_555_live_candidate",
        "dubai_live_candidate", "research.gold_iterative.live_parity"}]
    if forbidden:
        raise ValueError("observed or live module imported into independent replay")
    counts = dict(sorted(Counter(row["status"] for row in results).items()))
    report = {"contract": "weekly_raw_causal_control_batch_v1",
              "status": "diagnostic_only", "protocol_sha256": protocol_hash,
              "batch_index": batch_index, "expected_batch_count": expected_batch_count,
              "signal_count": len(selected_signals), "cohort_signal_count": len(signals),
              "scenario_count": len(SCENARIOS),
              "signal_ids": selected_ids,
              "row_status_counts": counts,
              "coverage_status": _coverage_status(counts),
              "censored_row_count": sum(row.get("censored_data_end") is True
                                        for row in results),
              "raw_semantics_complete": protocol["raw_semantics_complete"],
              "clock_admitted": protocol["clock_admitted"],
              "last_market_tick_utc": datetime.fromtimestamp(
                  int(market[0][-1]) / 1_000_000_000, timezone.utc).isoformat(),
              "requested_cutoff_utc": protocol["end_utc"],
              "results": results, "full_live_parity_verified": False,
              "independent_account_equity_verified": False,
              "risk_scope": RISK_SCOPE,
              "shared_account_equity_reconstructed": False,
              "all_days_direct_clock": protocol["all_days_direct_clock"],
              "search_candidates": 0}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, default=causal.default,
                  allow_nan=False)
        stream.write("\n")
    return {"signals": len(selected_signals), "batch_index": batch_index,
            "scenarios": len(SCENARIOS),
            "output_sha256": digest(output_path)}


def assemble(protocol_path, fragments, output_path):
    protocol_path, output_path = Path(protocol_path), Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    protocol_hash = digest(protocol_path)
    protocol = _read(protocol_path)
    if (protocol.get("contract") != CONTRACT or protocol.get("implementation") != _identity()
            or protocol.get("batch_size") != BATCH_SIZE
            or protocol.get("risk_scope") != RISK_SCOPE
            or protocol.get("shared_account_equity_reconstructed") is not False
            or protocol.get("execution_scenarios") != list(SCENARIOS)
            or protocol.get("max_entry_age_s") != WEEKLY_ENTRY_MAX_AGE_S
            or protocol.get("execution_money_assumptions") != {
                "commission": "hypothetical_zero", "swap": "not_modeled",
                "rollover": "not_admitted", "margin": "not_modeled"}):
        raise ValueError("weekly assembly protocol changed")
    ids = protocol["raw_signal_ids"]
    if not 0 < len(ids) <= MAX_SIGNALS or len(set(ids)) != len(ids):
        raise ValueError("weekly assembly cohort identity invalid")
    for name, sha in protocol["input_paths_sha256"].items():
        if digest(name) != sha:
            raise ValueError("weekly assembly source changed")
    for items in protocol["tapes"].values():
        for item in items:
            if (digest(item["meta"]) != item["meta_sha256"]
                    or digest(item["data"]) != item["data_sha256"]):
                raise ValueError("weekly assembly quote source changed")
    expected_count = (len(ids) + BATCH_SIZE - 1) // BATCH_SIZE
    if len(fragments) != expected_count:
        raise ValueError("weekly causal batch denominator incomplete")
    by_index, proofs = {}, {}
    for path in map(Path, fragments):
        fragment = _read(path)
        index = fragment.get("batch_index")
        if (type(index) is not int or index in by_index or not 0 <= index < expected_count
                or fragment.get("contract") != "weekly_raw_causal_control_batch_v1"
                or fragment.get("protocol_sha256") != protocol_hash
                or fragment.get("expected_batch_count") != expected_count
                or fragment.get("cohort_signal_count") != len(ids)
                or fragment.get("scenario_count") != len(SCENARIOS)
                or fragment.get("raw_semantics_complete") != protocol["raw_semantics_complete"]
                or fragment.get("clock_admitted") != protocol["clock_admitted"]
                or fragment.get("all_days_direct_clock") != protocol["all_days_direct_clock"]):
            raise ValueError("weekly causal batch identity mismatch")
        if (fragment.get("risk_scope") != RISK_SCOPE
                or fragment.get("shared_account_equity_reconstructed") is not False
                or fragment.get("full_live_parity_verified") is not False
                or fragment.get("independent_account_equity_verified") is not False):
            raise ValueError("weekly causal batch risk scope mismatch")
        selected, _ = _batch_ids(ids, index)
        if (fragment.get("signal_ids") != selected
                or fragment.get("signal_count") != len(selected)
                or len(fragment.get("results", [])) != len(selected) * len(SCENARIOS)):
            raise ValueError("weekly causal batch signal denominator mismatch")
        expected_keys = {(signal, _scenario_key(scenario))
                         for signal in selected for scenario in SCENARIOS}
        actual_keys = [(row.get("signal_id"), _scenario_key(row))
                       for row in fragment["results"]]
        if len(set(actual_keys)) != len(actual_keys) or set(actual_keys) != expected_keys:
            raise ValueError("weekly causal scenario matrix incomplete")
        counts = dict(sorted(Counter(row["status"] for row in fragment["results"]).items()))
        if (fragment.get("row_status_counts") != counts
                or fragment.get("coverage_status") != _coverage_status(counts)
                or fragment.get("censored_row_count") != sum(
                    row.get("censored_data_end") is True for row in fragment["results"])):
            raise ValueError("weekly causal batch status counts mismatch")
        by_index[index] = fragment
        proofs[str(path)] = digest(path)
    if set(by_index) != set(range(expected_count)):
        raise ValueError("weekly causal batch index missing")
    results = [row for index in range(expected_count) for row in by_index[index]["results"]]
    if (digest(protocol_path) != protocol_hash
            or any(digest(path) != sha for path, sha in proofs.items())
            or any(digest(name) != sha for name, sha in protocol["input_paths_sha256"].items())):
        raise ValueError("weekly causal assembly inputs changed")
    counts = dict(sorted(Counter(row["status"] for row in results).items()))
    report = {"contract": "weekly_raw_causal_control_results_v1",
              "status": "diagnostic_only", "protocol_sha256": protocol_hash,
              "signal_count": len(ids), "scenario_count": len(SCENARIOS),
              "batch_count": expected_count, "signal_ids": ids,
              "row_status_counts": counts,
              "coverage_status": _coverage_status(counts),
              "censored_row_count": sum(row.get("censored_data_end") is True
                                        for row in results),
              "batch_inputs_sha256": proofs, "results": results,
              "full_live_parity_verified": False,
              "independent_account_equity_verified": False,
              "risk_scope": RISK_SCOPE,
              "shared_account_equity_reconstructed": False,
              "execution_money_assumptions": protocol["execution_money_assumptions"],
              "search_candidates": 0}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return {"signals": len(ids), "batches": expected_count,
            "output_sha256": digest(output_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    prep = modes.add_parser("prepare")
    for name in ("raw", "readiness", "anchor", "contract", "raw-dir", "protocol"):
        prep.add_argument(f"--{name}", required=True)
    execute = modes.add_parser("run")
    execute.add_argument("--protocol", required=True)
    execute.add_argument("--output", required=True)
    execute.add_argument("--batch-index", type=int)
    merge = modes.add_parser("assemble")
    merge.add_argument("--protocol", required=True)
    merge.add_argument("--fragments", nargs="+", required=True)
    merge.add_argument("--output", required=True)
    args = parser.parse_args()
    result = (prepare(args.raw, args.readiness, args.anchor, args.contract,
                      args.raw_dir, args.protocol) if args.mode == "prepare"
              else run(args.protocol, args.output, batch_index=args.batch_index)
              if args.mode == "run" else assemble(args.protocol, args.fragments, args.output))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
