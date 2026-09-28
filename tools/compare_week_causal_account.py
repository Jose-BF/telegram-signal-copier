"""Compare aggregated isolated replays against a modeled native account path.

This combines all signal positions on one quote grid. It does not rerun
account-level decisions under shared margin or order queues. The observed
path is reconstructed from deals and ticks, not independent MT5 equity.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
from itertools import combinations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.account_risk_path import compare_account_paths
from research.risk_trajectory import RiskSpec
from tools import run_causal_controls as causal
from tools import run_week_causal_controls as weekly
from tools.audit_native_money_anchor import digest
from tools.audit_week_native_account_path import CONTRACT as NATIVE_CONTRACT
from tools.compare_week_causal_risk import _events, _inputs
from tools.compare_week_causal_sequences import _observed, no_fill_coverage


CONTRACT = "weekly_raw_causal_modeled_account_comparison_v1"
SOURCES = (
    "tools/compare_week_causal_account.py",
    "tools/compare_week_causal_risk.py",
    "research/account_risk_path.py",
    "research/risk_trajectory.py",
    "research/dubai_iterative/portfolio.py",
    "tools/compare_week_causal_sequences.py",
)


def verify_observed_rows(signal_ids, rows, native_events):
    """Reject a forged observed leg, even if its input-file hashes match."""
    if set(native_events) - set(signal_ids):
        raise ValueError("native basket missing from weekly signal universe")
    checked = 0
    for row in rows:
        if row.get("scenario") is None:
            continue
        comparison = row["comparison"]
        if comparison["status"] not in {"exact_facts_only", "mismatch"}:
            continue
        signal_id = row["signal_id"]
        if signal_id not in native_events:
            raise ValueError(f"observed fill status unverified: {signal_id}")
        actual = _events(comparison["comparisons"], "observed")
        expected = native_events[signal_id]
        fill_status = ("reconciled_native_fills" if expected else
                       "verified_no_xau_fill_in_captured_deals")
        if (Counter(actual) != Counter(expected)
                or len(actual) != comparison["observed_event_count"]
                or row.get("observed_position_count")
                   != sum(event.kind == "entry" for event in expected)
                or row.get("observed_fill_status") != fill_status):
            raise ValueError(f"weekly observed legs differ from native deals: {signal_id}")
        checked += 1
    return checked


def native_overlap(positions):
    """Measure simultaneous native signal exposure, not basket span overlap."""
    raw = defaultdict(list)
    for row in positions:
        signal_id = row["signal_id"]
        start, end = row["entry_msc"], row["exit_msc"]
        if (not isinstance(signal_id, str) or signal_id.split("_", 1)[0]
                not in {"canal1", "canal2"} or type(start) is not int
                or type(end) is not int or end < start):
            raise ValueError("native position interval invalid")
        if start < end:
            raw[signal_id].append((start, end))
    merged = {}
    for signal_id, intervals in raw.items():
        joined = []
        for start, end in sorted(intervals):
            if joined and start <= joined[-1][1]:
                joined[-1] = (joined[-1][0], max(joined[-1][1], end))
            else:
                joined.append((start, end))
        merged[signal_id] = joined
    peaks, pairs = {}, []
    for channel in ("canal1", "canal2"):
        selected = {name: intervals for name, intervals in merged.items()
                    if name.startswith(channel + "_")}
        endpoints = sorted((at, delta) for intervals in selected.values()
                           for start, end in intervals for at, delta in ((start, 1), (end, -1)))
        active = peak = 0
        for _, delta in endpoints:
            active += delta
            peak = max(peak, active)
        peaks[channel] = peak
        for (left_id, left), (right_id, right) in combinations(sorted(selected.items()), 2):
            duration = sum(max(0, min(a_end, b_end) - max(a_start, b_start))
                           for a_start, a_end in left for b_start, b_end in right)
            if duration:
                pairs.append({"signal_ids": [left_id, right_id], "overlap_ms": duration})
    return {"max_concurrent_signals_by_channel": peaks,
            "same_channel_overlap_pair_count": len(pairs), "same_channel_overlap_pairs": pairs,
            "scope": "native_position_intervals_not_order_attempts"}


def scenario_events(signal_ids, rows):
    """Require a complete cohort before aggregating any account risk metric."""
    by_signal = {row["signal_id"]: row for row in rows}
    if len(by_signal) != len(rows) or set(by_signal) != set(signal_ids):
        raise ValueError("scenario signal denominator incomplete or duplicated")
    observed, simulated, blockers = {}, {}, []
    for signal_id in signal_ids:
        comparison = by_signal[signal_id]["comparison"]
        if comparison["status"] not in {"exact_facts_only", "mismatch"}:
            blockers.append({"signal_id": signal_id,
                             "reason": comparison.get("blockers", ["sequence not comparable"])})
            continue
        try:
            comparisons = comparison["comparisons"]
            actual = _events(comparisons, "observed")
            virtual = _events(comparisons, "simulated")
            if (len(actual) != comparison["observed_event_count"]
                    or len(virtual) != comparison["simulated_event_count"]):
                raise ValueError("sequence event count differs from comparison")
            observed[signal_id] = actual
            simulated[signal_id] = virtual
        except (ValueError, KeyError, TypeError) as exc:
            blockers.append({"signal_id": signal_id, "reason": [str(exc)]})
    return observed, simulated, blockers


def compare_scenario(signal_ids, rows, market, conversion, spec, interval):
    observed, simulated, blockers = scenario_events(signal_ids, rows)
    if blockers:
        return {"status": "blocked_prior_stage", "included_signal_count": 0,
                "excluded_signal_count": len(blockers), "blockers": blockers,
                "strict_causal": None, "retrospective_bracketed": None}
    strict = compare_account_paths(observed, simulated, market, conversion, spec=spec)
    bracketed = compare_account_paths(observed, simulated, market, conversion, spec=spec,
                                      retrospective_fx_interval_ms=interval)
    return {"status": "diagnostic_only", "included_signal_count": len(signal_ids),
            "excluded_signal_count": 0, "blockers": [],
            "strict_causal": strict, "retrospective_bracketed": bracketed}


def compare(protocol_path, assembled_path, sequence_path, native_account_path, output_path):
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    paths, hashes, protocol, sequence = _inputs(protocol_path, assembled_path, sequence_path)
    native_account_path = Path(native_account_path)
    hashes[str(native_account_path)] = digest(native_account_path)
    native = weekly._read(native_account_path)
    if (native.get("contract") != NATIVE_CONTRACT
            or native.get("observed_account_equity_compared") is not False
            or native.get("independent_replay_compared") is not False
            or native.get("per_basket_metrics_matched") != native.get("basket_count")
            or native.get("per_basket_metrics_mismatched") != []
            or native.get("strict_causal", {}).get("status") not in {
                "blocked", "exact_sampled_path_only"}
            or native.get("retrospective_bracketed", {}).get("status")
               != "exact_sampled_path_only"
            or native.get("source_clock_admitted") is not False
            or native.get("tape_proofs") != protocol["tapes"]):
        raise ValueError("native modeled account baseline is not bound to weekly tapes")
    native_sources = sequence["inputs_sha256"]
    if len(set(native_sources) & set(native["inputs_sha256"])) != 3:
        raise ValueError("native deal, basket and money sources are not jointly bound")
    for name, sha in native["inputs_sha256"].items():
        if name in native_sources and native_sources[name] != sha:
            raise ValueError("native baseline and sequence source hashes disagree")
        if digest(name) != sha:
            raise ValueError("native baseline source changed")
    for name, sha in native["sources_sha256"].items():
        if digest(ROOT / name) != sha:
            raise ValueError("native baseline code changed")
    sources = {name: digest(ROOT / name) for name in SOURCES}
    expected = set(protocol["raw_signal_ids"])
    if (sequence.get("native_basket_count") != native["basket_count"]
            or sequence.get("native_baskets_missing_from_raw")
            or not set(sequence.get("raw_signals_without_native_basket", [])) <= expected):
        raise ValueError("weekly native basket denominator differs from account baseline")
    native_inputs = {name: weekly._read(name) for name in native["inputs_sha256"]}
    shared = [row for name, row in native_inputs.items() if name in native_sources]
    money = [row for row in shared if row.get("contract") == "native_closed_money_anchor_v2"]
    deals = [row for row in shared if "deals" in row and "query_start" in row]
    baskets = [row for row in shared if "baskets" in row and "positions" in row]
    broker = [(name, row) for name, row in native_inputs.items()
              if "account" in row and "conversion" in row]
    anchor = [(name, row) for name, row in native_inputs.items()
              if row.get("contract") == "native_tick_anchor_diagnostic_v1"]
    if any(len(rows) != 1 for rows in (money, deals, baskets, broker, anchor)):
        raise ValueError("native account source types ambiguous")
    money, deals, baskets = (rows[0] for rows in (money, deals, baskets))
    broker_path, broker = broker[0]
    anchor_path, anchor = anchor[0]
    for path in (broker_path, anchor_path):
        matches = [sha for name, sha in protocol["input_paths_sha256"].items()
                   if Path(name).resolve() == Path(path).resolve()]
        if matches != [native["inputs_sha256"][path]]:
            raise ValueError("weekly protocol and native clock/money source differ")
    _, _, days = weekly._days(anchor)
    clock = {day.date().isoformat(): anchor["independent_clock_evidence"]["days"]
             .get(day.date().isoformat(), {}).get("status", "missing") for day in days}
    if (protocol.get("clock_by_source_day") != clock
            or protocol.get("clock_admitted") is not (anchor.get("clock_admitted") is True)
            or protocol.get("broker_epoch_offset_seconds_hypothesis") != weekly.OFFSET_SECONDS):
        raise ValueError("weekly protocol clock status differs from native anchor")
    by_signal = defaultdict(list)
    for position in money["positions"]:
        by_signal[position["signal_id"]].append(position)
    overlap = native_overlap(money["positions"])
    if (set(by_signal) != {row["signal_id"] for row in baskets["baskets"]}
            or len(by_signal) != native["basket_count"]):
        raise ValueError("native account basket identities changed")
    observed = {signal_id: _observed(signal_id, positions, deals)
                for signal_id, positions in by_signal.items()}
    absent = sorted(expected - set(observed))
    no_fill = no_fill_coverage(protocol, deals, money, broker)
    if (sequence.get("no_fill_coverage") != no_fill
            or sequence.get("raw_signals_without_native_basket") != absent
            or sequence.get("verified_no_xau_fill_signal_ids") != (
                absent if no_fill["verified_no_xau_fill_for_absent_signal_ids"] else [])):
        raise ValueError("weekly absent-fill proof differs from native evidence")
    if no_fill["verified_no_xau_fill_for_absent_signal_ids"]:
        observed.update({signal_id: [] for signal_id in absent})
    verified_rows = verify_observed_rows(protocol["raw_signal_ids"], sequence["rows"], observed)
    market, conversion = (weekly._load_tape(protocol["tapes"][symbol])
                          for symbol in ("XAUUSD", "EURUSD"))
    spec = RiskSpec("EUR", protocol["currency_digits"],
                    Decimal(str(protocol["contract_size"])),
                    "account_base_profit_quote", protocol["fx_max_age_ms"], 5_000)
    interval = native["retrospective_fx_interval_ms"]
    if type(interval) is not int or interval <= protocol["fx_max_age_ms"]:
        raise ValueError("native retrospective FX interval invalid")
    rows = []
    for scenario in weekly.SCENARIOS:
        key = weekly._scenario_key(scenario)
        cohort = [row for row in sequence["rows"] if row.get("scenario") is not None
                  and weekly._scenario_key(row["scenario"]) == key]
        result = compare_scenario(protocol["raw_signal_ids"], cohort, market,
                                  conversion, spec, interval)
        rows.append({"scenario": scenario, **result})
    if (any(digest(path) != hashes[str(path)] for path in (*paths, native_account_path))
            or any(digest(ROOT / name) != sha for name, sha in sources.items())):
        raise ValueError("weekly account source changed during comparison")
    report = {"contract": CONTRACT, "status": "diagnostic_only",
              "signal_count": len(protocol["raw_signal_ids"]),
              "scenario_count": len(weekly.SCENARIOS), "rows": rows,
              "native_observed_sequence_rows_rechecked": verified_rows,
              "native_signal_overlap": overlap,
              "observed_overlap_requires_shared_decision_audit": (
                  overlap["same_channel_overlap_pair_count"] > 0),
              "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
              "inputs_sha256": hashes, "sources_sha256": sources,
              "risk_scope": "aggregated_isolated_signal_outputs_common_tick_grid",
              "shared_account_decisions_simulated": False,
              "cross_signal_decision_parity_verified": False,
              "source_clock_admitted": protocol["clock_admitted"],
              "clock_by_source_day": clock,
              "clock_unanchored_days": sorted(day for day, status in clock.items()
                                             if status != "direct_anchor_available"),
              "raw_semantics_complete": protocol["raw_semantics_complete"],
              "observed_account_equity_compared": False,
              "full_live_parity_verified": False,
              "limitations": [
                  "Observed booked money is reconciled; account floating and drawdown are modeled from retained quotes.",
                  "The strict causal FX result blocks unknown money marks; bracketed FX is retrospective only.",
                  "A complete modeled path does not verify MT5 equity, order attempts, or alternative broker fills.",
                  "Signals were replayed independently; shared margin, account guards and order queues were not rerun.",
                  "Live channel entry serialization and ambiguous standalone management need cross-signal replay where baskets overlap.",
                  "Broker clock is hypothetical outside directly anchored days.",
              ]}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as stream:
        stream.write(causal.encode(report))
    return {"signals": report["signal_count"], "statuses": report["statuses"],
            "output_sha256": digest(output_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("protocol", "assembled", "sequence", "native-account", "output"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    print(compare(args.protocol, args.assembled, args.sequence,
                  args.native_account, args.output))


if __name__ == "__main__":
    main()
