"""Isolate drawdown lost by shadow-transition sampling on one native money path.

Offline diagnostic: both grids use the same actual fills, FX, event ordering,
and reconstructed valuation. Neither grid is independent MT5 account equity.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.risk_trajectory import RiskSpec, reconstruct_risk
from tools.audit_native_equity_snapshots import _tape
from tools.audit_native_money_anchor import digest, read
from tools.audit_native_week_risk_path import (
    MARKET_MAX_AGE_MS, native_events, quote_grid, summarize_path,
)


MAX_SECONDS = 600


def _path_reduction(samples):
    peak = maximum = Decimal(0)
    peak_at = trough_at = None
    for sample in samples:
        total = sample["total"]
        if total is None:
            raise ValueError("incomplete native valuation cannot measure grid sampling")
        if total > peak:
            peak, peak_at = total, sample["at"]
        if peak - total > maximum:
            maximum, trough_at = peak - total, sample["at"]
    return maximum, peak_at, trough_at


def compare_sample_grids(full_samples, events, transition_times):
    """Reduce a single native trajectory on full and transition/event grids."""
    full_samples, events, transition_times = tuple(full_samples), tuple(events), set(transition_times)
    if not full_samples or any(a["at"] > b["at"] for a, b in zip(full_samples, full_samples[1:])):
        raise ValueError("full risk samples missing or unordered")
    if any(stamp.tzinfo is None for stamp in transition_times):
        raise ValueError("transition timestamps must be timezone-aware")
    full_times = {sample["at"] for sample in full_samples}
    event_times = {event.at for event in events}
    if not event_times <= full_times:
        raise ValueError("native event boundary absent from full risk grid")
    in_span = {stamp for stamp in transition_times
               if full_samples[0]["at"] <= stamp <= full_samples[-1]["at"]}
    if not in_span <= full_times:
        raise ValueError("shadow transition absent from full native market grid")
    selected_times = in_span | event_times
    transition_only = [sample for sample in full_samples if sample["at"] in in_span]
    selected = [sample for sample in full_samples if sample["at"] in selected_times]
    full_dd, full_peak, full_trough = _path_reduction(full_samples)
    transition_dd, _, _ = _path_reduction(transition_only)
    sparse_dd, sparse_peak, sparse_trough = _path_reduction(selected)
    if transition_dd > sparse_dd or sparse_dd > full_dd:
        raise ValueError("subsample drawdown exceeds its identical larger grid")
    return {
        "full_sample_count": len(full_samples),
        "transition_timestamp_count": len(transition_times),
        "transition_timestamp_in_path_count": len(in_span),
        "event_boundary_count": len(event_times),
        "transition_only_sample_count": len(transition_only),
        "transition_plus_event_sample_count": len(selected),
        "full_tick_max_drawdown_eur": f"{full_dd:.2f}",
        "transition_only_max_drawdown_eur": f"{transition_dd:.2f}",
        "transition_plus_event_max_drawdown_eur": f"{sparse_dd:.2f}",
        "event_boundary_drawdown_contribution_eur": f"{sparse_dd - transition_dd:.2f}",
        "grid_omission_drawdown_gap_eur": f"{full_dd - sparse_dd:.2f}",
        "full_peak_at": full_peak.isoformat() if full_peak else None,
        "full_trough_at": full_trough.isoformat() if full_trough else None,
        "transition_plus_event_peak_at": sparse_peak.isoformat() if sparse_peak else None,
        "transition_plus_event_trough_at": sparse_trough.isoformat() if sparse_trough else None,
        "full_trough_present_in_transition_plus_event_grid": full_trough in selected_times,
    }


def _verify_inputs(report):
    inputs = report.get("inputs_sha256")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError("report lacks source hashes")
    for name, expected in inputs.items():
        if digest(Path(name)) != expected:
            raise ValueError(f"source changed since report: {name}")


def audit(full_path, shadow_path, raw_dir, native_deals_path, native_baskets_path,
          broker_contract_path, anchor_path, money_path, signals=()):
    started = time.monotonic()
    full_path, shadow_path, raw_dir, native_deals_path, native_baskets_path, broker_contract_path, anchor_path, money_path = map(
        Path, (full_path, shadow_path, raw_dir, native_deals_path, native_baskets_path,
               broker_contract_path, anchor_path, money_path))
    full, shadow = read(full_path), read(shadow_path)
    if (full.get("contract") != "native_week_full_tick_risk_diagnostic_v1"
            or shadow.get("contract") != "week_shadow_live_control_path_diagnostic_v1"
            or full.get("status") != "diagnostic_only"
            or shadow.get("status") != "diagnostic_only"
            or full.get("simulator_path_parity_verified") is not False):
        raise ValueError("expected full native risk and shadow comparison contracts")
    _verify_inputs(full)
    _verify_inputs(shadow)
    shared = (native_deals_path, native_baskets_path, broker_contract_path, anchor_path, money_path)
    for path in shared:
        for report in (full, shadow):
            matches = [sha for name, sha in report["inputs_sha256"].items()
                       if Path(name).resolve() == path.resolve()]
            if len(matches) != 1 or matches[0] != digest(path):
                raise ValueError(f"shared source mismatch: {path}")
    native, baskets, contract, anchor, money = map(read, shared)
    if baskets["source_sha256"] != digest(native_deals_path):
        raise ValueError("native basket source changed")
    comparisons = {row["signal_id"]: row for row in shadow["comparisons"]}
    full_rows = {row["signal_id"]: row for row in full["baskets"]}
    selected = tuple(signals) if signals else tuple(comparisons)
    if len(set(selected)) != len(selected) or not set(selected) <= (set(comparisons) & set(full_rows)):
        raise ValueError("requested control signal missing or duplicated")
    start = datetime.fromisoformat(anchor["scope"]["source_epoch_start"])
    end = datetime.fromisoformat(anchor["scope"]["source_epoch_end_exclusive"])
    watched = {}
    market = _tape(raw_dir, anchor, "XAUUSD", start, end, watched)
    conversion = _tape(raw_dir, anchor, "EURUSD", start, end, watched)
    spec = RiskSpec(money["account_currency"], contract["account"]["currency_digits"],
                    Decimal(str(contract["instrument"]["contract_size"])),
                    contract["conversion"]["orientation"],
                    contract["conversion"]["max_quote_age_ms"], MARKET_MAX_AGE_MS)
    positions_by_signal = {}
    for position in money["positions"]:
        positions_by_signal.setdefault(position["signal_id"], []).append(position)
    events_by_position = native_events(native["deals"], money["positions"])
    rows = []
    for signal in selected:
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("grid sampling audit wall budget exceeded")
        original, observed = full_rows[signal], comparisons[signal]
        if original["native_net_eur"] != observed["native_final_net_eur"]:
            raise ValueError(f"native money identity changed across reports: {signal}")
        if original["status"] != "retrospective_complete":
            rows.append({"signal_id": signal, "status": "blocked_full_native_path"})
            continue
        events = [event for position in positions_by_signal[signal]
                  for event in events_by_position[position["position_id"]]]
        path = reconstruct_risk(events, quote_grid(market, conversion, events), spec=spec,
                                retrospective_fx_interval_ms=contract["conversion"]["max_quote_interval_ms"])
        summary = summarize_path(path)
        if (summary["sample_stream_sha256"] != original["path"]["sample_stream_sha256"]
                or summary["metrics"] != original["path"]["metrics"]
                or bool(path["retrospective_fx_bracketed_samples"] == 0)
                != original["strict_causal_fx_path_complete"]):
            raise ValueError(f"full native model did not reproduce frozen report: {signal}")
        transitions = {datetime.fromtimestamp(item["utc_msc"] // 1000, timezone.utc)
                       + timedelta(milliseconds=item["utc_msc"] % 1000)
                       for item in observed["sample_stream"]}
        comparison = compare_sample_grids(path["samples"], events, transitions)
        rows.append({"signal_id": signal, "status": "diagnostic_only",
                     "direct_clock_anchor": original["direct_clock_anchor_for_all_event_days"],
                     "strict_causal_fx": original["strict_causal_fx_path_complete"],
                     "prior_common_mark_drawdown_eur": observed["native_common_mark_max_drawdown_eur"],
                     **comparison})
    return {"contract": "native_risk_grid_sampling_diagnostic_v1", "status": "diagnostic_only",
            "simulator_path_parity_verified": False, "signals_requested": list(selected),
            "signals": rows, "inputs_sha256": {str(full_path): digest(full_path),
                                             str(shadow_path): digest(shadow_path),
                                             **{str(path): digest(path) for path in shared},
                                             **watched, str(Path(__file__).resolve()): digest(Path(__file__))},
            "limitations": ["Both grids use modeled native-fill money, not independent MT5 equity.",
                            "Native event boundaries and same-time ordering remain fixed on both grids.",
                            "A grid gap quantifies omitted retained ticks, not unobserved between-tick extrema.",
                            "The prior common-mark figure uses a different boundary convention and is context only."],
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("full-risk-report", "shadow-report", "raw-dir", "native-deals",
                 "native-baskets", "broker-contract", "native-anchor", "native-money", "output"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    parser.add_argument("--signal", action="append", default=[])
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("immutable grid sampling output already exists")
    report = audit(args.full_risk_report, args.shadow_report, args.raw_dir, args.native_deals,
                   args.native_baskets, args.broker_contract, args.native_anchor,
                   args.native_money, args.signal)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "signal_count": len(report["signals"]),
                      "elapsed_seconds": report["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
