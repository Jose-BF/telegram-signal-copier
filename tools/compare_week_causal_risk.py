"""Compare independent weekly replay and reconciled native basket risk paths.

Every row retains its cohort/scenario identity. This is modeled risk from
actual deals on a common retained quote grid, not observed MT5 account equity.
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.causal_comparison import SequenceEvent
from research.risk_trajectory import RiskSpec, compare_risk
from tools import assess_week_causal_portfolio as portfolio
from tools import run_causal_controls as causal
from tools import run_week_causal_controls as weekly
from tools.audit_native_money_anchor import digest
from tools.audit_verified_gold_full_tick_path import summarize_comparison
from tools.compare_risk_trajectories import paired_quotes
from tools.compare_week_causal_sequences import (CONTRACT as SEQUENCE_CONTRACT,
                                                  SOURCES as SEQUENCE_SOURCES)


CONTRACT = "weekly_raw_causal_risk_batch_v1"
ASSEMBLED_CONTRACT = "weekly_raw_causal_risk_comparison_v1"
MAX_WALL_SECONDS = 900
MAX_INPUT_BYTES = 256_000_000
SOURCES = (
    "tools/compare_week_causal_risk.py",
    "tools/compare_risk_trajectories.py",
    "tools/audit_verified_gold_full_tick_path.py",
    "research/risk_trajectory.py",
)


def _source(path):
    path = Path(path)
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("weekly risk input byte budget exceeded")
    return weekly._read(path)


def _events(comparisons, side):
    events = [SequenceEvent(**row[side]) for row in comparisons if row.get(side) is not None]
    entries = {row.slot: row for row in events if row.kind == "entry"}
    if len(entries) != sum(row.kind == "entry" for row in events):
        raise ValueError("risk sequence has duplicate entry slots")
    if any(sum((row.volume for row in events if row.slot == slot and row.kind == "exit"), Decimal(0))
           != entry.volume for slot, entry in entries.items()):
        raise ValueError("risk sequence has an open or overclosed position")
    if any(row.kind == "exit" and row.slot not in entries for row in events):
        raise ValueError("risk sequence has an exit without entry")
    return events


def risk_row(row, market, conversion, spec):
    output = {"signal_id": row["signal_id"], "channel": row["channel"],
              "scenario": row["scenario"], "sequence_status": row["comparison"]["status"]}
    try:
        if row["comparison"]["status"] == "blocked":
            raise ValueError(f"sequence stage blocked: {row['comparison']['blockers']}")
        if row["comparison"]["status"] not in {"mismatch", "exact_facts_only"}:
            raise ValueError("unrecognized sequence comparison status")
        comparisons = row["comparison"]["comparisons"]
        observed, simulated = (_events(comparisons, side) for side in ("observed", "simulated"))
        if (len(observed) != row["comparison"]["observed_event_count"]
                or len(simulated) != row["comparison"]["simulated_event_count"]):
            raise ValueError("risk event counts differ from sequence evidence")
        events = (*observed, *simulated)
        if not events:
            output["comparison"] = {
                "status": "exact_empty_fill_path_only", "common_tick_and_event_marks": 0,
                "full_risk_path_comparable": True, "drawdown_delta_eur": "0.00",
                "exposure_difference_marks": 0, "floating_difference_marks": 0,
                "mismatched_marks": 0, "unknown_marks": 0,
                "decision_sequence_verified": False, "blockers": [],
            }
            return output
        quotes = paired_quotes(market, conversion, events,
                               start=min(event.at for event in events),
                               end=max(event.at for event in events))
        result = summarize_comparison(compare_risk(observed, simulated, quotes, spec=spec))
        result["observed_deal_reconstruction"] = result.pop("native")
        result["independent_replay"] = result.pop("shadow")
        output["comparison"] = result
    except (ValueError, KeyError, TypeError) as exc:
        output["comparison"] = {"status": "blocked", "blockers": [str(exc)],
                                "full_risk_path_comparable": False}
    return output


def _inputs(protocol_path, assembled_path, sequence_path):
    paths = tuple(map(Path, (protocol_path, assembled_path, sequence_path)))
    hashes = {str(path): digest(path) for path in paths}
    protocol, assembled, sequence = map(_source, paths)
    portfolio._validate(protocol, assembled, hashes[str(paths[0])])
    if (sequence.get("contract") != SEQUENCE_CONTRACT
            or protocol.get("account_currency") != "EUR"
            or protocol.get("currency_digits") != 2
            or protocol.get("contract_size") != 100.0
            or sequence.get("inputs_sha256", {}).get(str(paths[0])) != hashes[str(paths[0])]
            or sequence.get("inputs_sha256", {}).get(str(paths[1])) != hashes[str(paths[1])]
            or sequence.get("expected_control_rows")
               != len(protocol["raw_signal_ids"]) * len(weekly.SCENARIOS)
            or sequence.get("raw_signal_count") != len(protocol["raw_signal_ids"])
            or sequence.get("raw_semantics_complete") != protocol["raw_semantics_complete"]
            or sequence.get("source_clock_admitted") != protocol["clock_admitted"]
            or set(sequence.get("sources_sha256", {})) != set(SEQUENCE_SOURCES)):
        raise ValueError("weekly sequence report is not bound to this cohort")
    for name, sha in sequence["inputs_sha256"].items():
        if digest(name) != sha:
            raise ValueError("weekly sequence native source changed")
    for name, sha in sequence["sources_sha256"].items():
        if digest(ROOT / name) != sha:
            raise ValueError("weekly sequence comparator changed")
    expected = {(signal_id, weekly._scenario_key(scenario))
                for signal_id in protocol["raw_signal_ids"] for scenario in weekly.SCENARIOS}
    actual = [(row["signal_id"], weekly._scenario_key(row["scenario"]))
              for row in sequence["rows"] if row.get("scenario") is not None]
    if (len(actual) != len(expected) or len(set(actual)) != len(actual)
            or set(actual) != expected or sequence.get("statuses") != dict(sorted(
                Counter(row["comparison"]["status"] for row in sequence["rows"]).items()))):
        raise ValueError("weekly sequence result matrix incomplete")
    return paths, hashes, protocol, sequence


def run(protocol_path, assembled_path, sequence_path, output_path, *, batch_index):
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    paths, hashes, protocol, sequence = _inputs(protocol_path, assembled_path, sequence_path)
    sources = {name: digest(ROOT / name) for name in SOURCES}
    selected, count = weekly._batch_ids(protocol["raw_signal_ids"], batch_index)
    selected_set = set(selected)
    market_raw, conversion_raw = (weekly._load_tape(protocol["tapes"][symbol])
                                  for symbol in ("XAUUSD", "EURUSD"))
    market = market_raw[0], np.column_stack(market_raw[1:])
    conversion = conversion_raw[0], np.column_stack(conversion_raw[1:])
    spec = RiskSpec(protocol["account_currency"], protocol["currency_digits"],
                    Decimal(str(protocol["contract_size"])), "account_base_profit_quote",
                    protocol["fx_max_age_ms"], 5_000)
    rows, began = [], time.monotonic()
    for row in sequence["rows"]:
        if row["signal_id"] not in selected_set or row.get("scenario") is None:
            continue
        if time.monotonic() - began > MAX_WALL_SECONDS:
            raise TimeoutError("weekly risk batch time budget exhausted; no partial report")
        rows.append(risk_row(row, market, conversion, spec))
    expected = {(signal_id, weekly._scenario_key(scenario))
                for signal_id in selected for scenario in weekly.SCENARIOS}
    actual = [(row["signal_id"], weekly._scenario_key(row["scenario"])) for row in rows]
    if len(actual) != len(expected) or set(actual) != expected or len(set(actual)) != len(actual):
        raise ValueError("weekly risk batch denominator changed")
    if (any(digest(path) != hashes[str(path)] for path in paths)
            or any(digest(ROOT / name) != sha for name, sha in sources.items())):
        raise ValueError("weekly risk source changed during batch")
    report = {"contract": CONTRACT, "status": "diagnostic_only", "batch_index": batch_index,
              "expected_batch_count": count, "signal_ids": selected,
              "scenario_count": len(weekly.SCENARIOS), "rows": rows,
              "statuses": dict(sorted(Counter(row["comparison"]["status"] for row in rows).items())),
              "inputs_sha256": hashes, "sources_sha256": sources,
              "risk_scope": "modeled_per_basket_common_tick_grid",
              "raw_semantics_complete": protocol["raw_semantics_complete"],
              "source_clock_admitted": protocol["clock_admitted"],
              "observed_account_equity_compared": False,
              "shared_account_drawdown_compared": False,
              "full_live_parity_verified": False,
              "limitations": [
                  "Native fills and booked money are observed; floating is reconstructed from retained Bid/Ask and FX ticks.",
                  "Strict causal FX and market age limits block unsupported marks.",
                  "Per-basket path does not establish simultaneous account equity, margin or unobserved extrema.",
                  "Broker clock remains hypothetical where direct anchors are absent.",
              ]}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as stream:
        stream.write(causal.encode(report))
    return {"batch_index": batch_index, "signals": len(selected),
            "statuses": report["statuses"], "output_sha256": digest(output_path)}


def assemble(protocol_path, assembled_path, sequence_path, fragments, output_path):
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    paths, hashes, protocol, sequence = _inputs(protocol_path, assembled_path, sequence_path)
    count = (len(protocol["raw_signal_ids"]) + weekly.BATCH_SIZE - 1) // weekly.BATCH_SIZE
    if len(fragments) != count:
        raise ValueError("weekly risk batch denominator incomplete")
    by_index, proofs = {}, {}
    for path in map(Path, fragments):
        report = _source(path)
        index = report.get("batch_index")
        if type(index) is not int or index in by_index or not 0 <= index < count:
            raise ValueError("weekly risk batch index invalid or duplicate")
        selected, _ = weekly._batch_ids(protocol["raw_signal_ids"], index)
        expected = {(signal_id, weekly._scenario_key(scenario))
                    for signal_id in selected for scenario in weekly.SCENARIOS}
        actual = [(row["signal_id"], weekly._scenario_key(row["scenario"]))
                  for row in report["rows"]]
        if (report.get("contract") != CONTRACT or report.get("signal_ids") != selected
                or report.get("expected_batch_count") != count
                or report.get("scenario_count") != len(weekly.SCENARIOS)
                or report.get("inputs_sha256") != hashes
                or report.get("risk_scope") != "modeled_per_basket_common_tick_grid"
                or report.get("raw_semantics_complete") != protocol["raw_semantics_complete"]
                or report.get("source_clock_admitted") != protocol["clock_admitted"]
                or report.get("full_live_parity_verified") is not False
                or report.get("observed_account_equity_compared") is not False
                or report.get("shared_account_drawdown_compared") is not False
                or set(report.get("sources_sha256", {})) != set(SOURCES)
                or len(actual) != len(expected) or len(set(actual)) != len(actual)
                or set(actual) != expected
                or report.get("statuses") != dict(sorted(Counter(
                    row["comparison"]["status"] for row in report["rows"]).items()))):
            raise ValueError("weekly risk batch identity or rows changed")
        for name, sha in report["sources_sha256"].items():
            if digest(ROOT / name) != sha:
                raise ValueError("weekly risk batch code changed")
        by_index[index] = report
        proofs[str(path)] = digest(path)
    if set(by_index) != set(range(count)):
        raise ValueError("weekly risk batch index missing")
    rows = [row for index in range(count) for row in by_index[index]["rows"]]
    if (any(digest(path) != hashes[str(path)] for path in paths)
            or any(digest(path) != sha for path, sha in proofs.items())):
        raise ValueError("weekly risk assembly source changed")
    expected_rows = len(protocol["raw_signal_ids"]) * len(weekly.SCENARIOS)
    if len(rows) != expected_rows:
        raise ValueError("weekly risk assembled denominator incomplete")
    result = {"contract": ASSEMBLED_CONTRACT, "status": "diagnostic_only",
              "signal_count": len(protocol["raw_signal_ids"]),
              "scenario_count": len(weekly.SCENARIOS), "expected_rows": expected_rows,
              "native_baskets_missing_from_raw": sequence["native_baskets_missing_from_raw"],
              "raw_signals_without_native_basket": sequence["raw_signals_without_native_basket"],
              "rows": rows,
              "statuses": dict(sorted(Counter(row["comparison"]["status"] for row in rows).items())),
              "batch_inputs_sha256": proofs, "inputs_sha256": hashes,
              "risk_scope": "modeled_per_basket_common_tick_grid",
              "raw_semantics_complete": protocol["raw_semantics_complete"],
              "source_clock_admitted": protocol["clock_admitted"],
              "shared_account_drawdown_compared": False,
              "observed_account_equity_compared": False,
              "full_live_parity_verified": False}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as stream:
        stream.write(causal.encode(result))
    return {"signals": result["signal_count"], "rows": len(rows),
            "statuses": result["statuses"], "output_sha256": digest(output_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    run_parser = modes.add_parser("run")
    assemble_parser = modes.add_parser("assemble")
    for item in (run_parser, assemble_parser):
        for name in ("protocol", "assembled", "sequence", "output"):
            item.add_argument(f"--{name}", required=True)
    run_parser.add_argument("--batch-index", type=int, required=True)
    assemble_parser.add_argument("--fragment", action="append", required=True)
    args = parser.parse_args()
    result = (run(args.protocol, args.assembled, args.sequence, args.output,
                  batch_index=args.batch_index) if args.mode == "run" else
              assemble(args.protocol, args.assembled, args.sequence,
                       args.fragment, args.output))
    print(result)


if __name__ == "__main__":
    main()
