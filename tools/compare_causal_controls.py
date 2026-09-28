"""Post-run comparison only: observed broker history never enters the simulator."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mt5_deal_reason import DEAL_REASON_NAMES
from research.causal_comparison import SequenceEvent, compare_sequences
from research.causal_replay import utc
from research.gold_iterative.ledger_evidence import ledger_ticket_evidence
from tools.run_causal_controls import digest, read, read_frozen, save, verify_frozen


def observed_slot(signal_id, comment):
    channel, message = signal_id.split("_", 1)
    if channel == "canal2":
        match = re.fullmatch(rf"c2_{re.escape(message)}(?:_B([1-4]))?_g55", comment)
        if match:
            return int(match[1] or 0) + 1
    elif channel == "canal1":
        if comment == f"c1_{message}_dv1":
            return 1
        match = re.fullmatch(rf"DCA_c1_{re.escape(message)}_D([1-2])", comment)
        if match:
            return int(match[1]) + 1
    raise ValueError(f"observed logical slot is not bound: {signal_id}:{comment}")


def observed_events(actual):
    evidence, blockers = ledger_ticket_evidence(actual)
    if blockers:
        raise ValueError(f"observed ledger blocked: {blockers}")
    slots = {}
    for position in actual["positions"]:
        ticket = str(position["position_id"])
        slot = observed_slot(actual["sig_id"], position["open_deal"].get("comment", ""))
        if slot in slots.values():
            raise ValueError("observed logical slot duplicated")
        slots[ticket] = slot
    events = []
    for ticket, position in evidence.items():
        slot = slots[ticket]
        events.append(SequenceEvent(slot, "entry", position["opened_at"], position["direction"],
            position["open_price"], position["volume"], position["opening_net_eur"], "market"))
        for deal in position["exit_deals"]:
            reason = DEAL_REASON_NAMES.get(deal["broker_reason"])
            if reason not in {"tp", "sl", "expert"}:
                raise ValueError(f"observed exit mechanism unsupported: {reason}")
            events.append(SequenceEvent(slot, "exit", deal["closed_at"], position["direction"],
                deal["exit_price"], deal["volume"], deal["net_eur"], reason))
    return events


def simulated_events(result, direction):
    if result["blockers"]:
        raise ValueError(f"engine control blocked: {result['blockers']}")
    events, slots = [], {}
    for row in result["entries"]:
        match = re.fullmatch(r"sim_(?:ladder_)?([1-5])", row["ticket"])
        if not match or row["source"] == "observed_mt5_fill":
            raise ValueError("simulated entry has no independent logical identity")
        slot = int(match[1])
        slots[row["ticket"]] = slot
        events.append(SequenceEvent(slot, "entry", utc(row["opened_at"]), direction,
            row["entry_price"], row["volume"], Decimal(0), "market"))
    passive = {"initial_sl": "sl", "initial_tp": "tp",
               "per_leg_target": "tp", "provider_tp": "tp", "provider_target_all": "tp",
               "trailing_stop": "sl", "fixed_sl": "sl", "provider_sl": "sl",
               "break_even": "sl", "provider_be": "sl"}
    managed = {"basket_stop", "provider_close", "basket_target", "fixed_move_target", "partial_target",
               "runner_target", "profit_lock", "time_exit", "hard_stop_per_leg"}
    for row in result["exits"]:
        reason = row["reason"]
        mechanism = passive.get(reason, "expert" if reason in managed else None)
        if mechanism is None or row["ticket"] not in slots:
            raise ValueError(f"simulated exit mechanism or identity unsupported: {reason}")
        events.append(SequenceEvent(slots[row["ticket"]], "exit", utc(row["closed_at"]), direction,
            row["exit_price"], row["volume"], row["pnl_eur"], mechanism))
    return events


def compare(args):
    protocol, protocol_hash = read_frozen(args.study / "protocol.json")
    results, results_hash = read_frozen(args.study / "independent_results.json")
    if results["protocol_sha256"] != protocol_hash:
        raise ValueError("independent result protocol mismatch")
    if protocol["raw_messages_sha256"] != digest(args.study / "raw_messages.json"):
        raise ValueError("raw control message identity mismatch")
    manifest = read(args.analysis / "analysis_manifest.json")
    if manifest["source_capture_manifest_sha256"] != protocol["source_capture_manifest_sha256"]:
        raise ValueError("observed analysis belongs to another capture")
    ledger_path = args.analysis / "observed_ledgers.json"
    proof = next(row for row in manifest["artifacts"] if row["name"] == ledger_path.name)
    if digest(ledger_path) != proof["sha256"] or ledger_path.stat().st_size != proof["bytes"]:
        raise ValueError("observed ledger identity mismatch")
    ledgers = read(ledger_path)["signals"]
    if len({row["sig_id"] for row in ledgers}) != len(ledgers):
        raise ValueError("duplicate observed signal identity")
    actual = {row["sig_id"]: row for row in ledgers}
    metadata, metadata_hash = read_frozen(args.study / "input_diagnostics.json")
    if metadata_hash != protocol.get("input_diagnostics_sha256"):
        raise ValueError("frozen causal metadata missing or changed; do not reinterpret old runs")
    directions = {row["signal_id"]: row["direction"] for row in metadata["signals"]}
    if len(directions) != len(metadata["signals"]) or list(directions) != protocol["expected_signal_ids"]:
        raise ValueError("frozen causal universe changed")
    rows = []
    seen = set()
    for control in results["results"]:
        signal_id = control["signal_id"]
        key = signal_id, control["latency_ms"], control["entry_fill_latency_ms"]
        if key in seen:
            raise ValueError("duplicate independent control")
        seen.add(key)
        row = {"signal_id": signal_id, "latency_ms": control["latency_ms"],
               "entry_fill_latency_ms": control["entry_fill_latency_ms"],
               "simulated_entries": len(control["result"]["entries"]),
               "observed_entries": actual.get(signal_id, {}).get("n_positions"),
               "engine_mismatches": control["engine_mismatches"]}
        try:
            if signal_id not in actual or signal_id not in directions:
                raise ValueError("raw or observed signal missing; no-trade outcome not inferred")
            if any(control["engine_mismatches"].values()):
                raise ValueError("independent engine disagreement")
            row["comparison"] = compare_sequences(observed_events(actual[signal_id]),
                simulated_events(control["result"], directions[signal_id]))
        except ValueError as exc:
            row["comparison"] = {"status": "blocked", "blockers": [str(exc)],
                                 "full_live_parity_verified": False}
        rows.append(row)
    for signal_id in sorted(actual.keys() - directions.keys()):
        rows.append({"signal_id": signal_id, "latency_ms": None,
            "entry_fill_latency_ms": None,
            "observed_entries": actual[signal_id]["n_positions"], "simulated_entries": None,
            "comparison": {"status": "blocked", "blockers": ["observed_signal_missing_from_raw_controls"],
                           "full_live_parity_verified": False}})
    expected = {(signal_id, scenario["latency_ms"], scenario["entry_fill_latency_ms"])
                for signal_id in directions for scenario in protocol["execution_scenarios"]}
    report = {
        "status": "diagnostic_only", "full_live_parity_verified": False,
        "search_candidates": 0, "mass_search_authorized": False,
        "inputs": {"protocol_sha256": protocol_hash,
                   "independent_results_sha256": results_hash,
                   "input_diagnostics_sha256": metadata_hash,
                   "observed_ledgers_sha256": digest(ledger_path)},
        "comparator_sources": {name: digest(ROOT / name) for name in (
            "tools/compare_causal_controls.py", "tools/run_causal_controls.py",
            "research/causal_comparison.py", "research/causal_replay.py", "parser.py",
            "research/gold_iterative/ledger_evidence.py", "mt5_deal_reason.py")},
        "rows": rows, "statuses": dict(Counter(row["comparison"]["status"] for row in rows)),
        "missing_controls": sorted(expected - seen), "unexpected_controls": sorted(seen - expected),
        "baseline": {"observed_entries": sum(value["n_positions"] for value in actual.values()),
            "simulated_entries": sum(row["simulated_entries"] for row in rows if row["latency_ms"] == row["entry_fill_latency_ms"] == 0),
            "entry_count_mismatches": [row["signal_id"] for row in rows if row["latency_ms"] == row["entry_fill_latency_ms"] == 0
                                       and row["simulated_entries"] != row["observed_entries"]]},
        "admission_blockers": protocol["admission_blockers"],
    }
    verify_frozen(args.study / "protocol.json", protocol_hash)
    verify_frozen(args.study / "independent_results.json", results_hash)
    verify_frozen(args.study / "input_diagnostics.json", metadata_hash)
    verify_frozen(ledger_path, proof["sha256"])
    sha = save(args.study / "first_divergences.json", report)
    print({"statuses": report["statuses"], "baseline": report["baseline"], "report_sha256": sha})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    compare(parser.parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
