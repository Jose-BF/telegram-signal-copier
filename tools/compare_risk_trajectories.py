"""Bounded post-run risk comparison for archived two-channel causal controls."""

import argparse
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
import hashlib
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.risk_trajectory import RiskQuote, RiskSpec, compare_risk
from tools.compare_causal_controls import observed_events, simulated_events
from tools.run_causal_controls import digest, encode, read_frozen, save, verify_frozen


SOURCES = ("tools/compare_risk_trajectories.py", "research/risk_trajectory.py",
           "research/risk_metrics.py",
           "tools/compare_causal_controls.py", "research/causal_comparison.py",
           "research/causal_replay.py", "research/gold_iterative/ledger_evidence.py",
           "broker_money.py", "tools/run_causal_controls.py", "mt5_deal_reason.py")
MAX_INPUT_BYTES = 128_000_000
MAX_TAPE_ROWS = 3_000_000
MAX_CASES = 200
MAX_SECONDS = 600


def _read(path, watched):
    path = Path(path).resolve()
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input byte budget exceeded")
    value, sha = read_frozen(path)
    watched[path] = sha
    return value, sha


def _tape(binding, offset, watched):
    path = Path(binding["path"]).resolve()
    if path.stat().st_size > MAX_INPUT_BYTES or pq.ParquetFile(path).metadata.num_rows > MAX_TAPE_ROWS:
        raise ValueError("tape budget exceeded")
    verify_frozen(path, binding["sha256"])
    watched[path] = binding["sha256"]
    frame = pd.read_parquet(path, columns=["time_utc", "source_time_msc", "bid", "ask"])
    times = pd.DatetimeIndex(frame.time_utc)
    if times.tz is None or times.hasnans:
        raise ValueError("explicit tape timezone required")
    times = times.tz_convert("UTC").as_unit("ns").asi8
    raw = frame.source_time_msc.to_numpy()
    if not np.issubdtype(raw.dtype, np.integer) or not np.array_equal(times // 1_000_000, raw - offset * 1000):
        raise ValueError("tape clock proof differs from protocol")
    if np.any(np.diff(times) < 0):
        raise ValueError("source tape not ordered")
    values = frame[["bid", "ask"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or np.any(values <= 0) or np.any(values[:, 0] > values[:, 1]):
        raise ValueError("invalid quote tape")
    return times, values


def paired_quotes(market, conversion, events, *, start, end):
    """Retain market observations plus deal boundaries, with causal prior FX.

    This is a common XAUUSD grid, not every intervening EURUSD observation.
    Native and hypothetical paths use exactly the same valuation observations.
    """
    times, values = market
    fx_times, fx_values = conversion
    lower, upper = pd.Timestamp(start).value, pd.Timestamp(end).value
    first, last = np.searchsorted(times, [lower, upper], side="left")
    last = np.searchsorted(times, upper, side="right")
    grid = [(int(times[index]), index) for index in range(first, last)]
    retained_times = set(int(value) for value in times[first:last])
    for when in {pd.Timestamp(row.at).value for row in events} | {lower, upper}:
        if when not in retained_times:
            grid.append((when, int(np.searchsorted(times, when, side="right")) - 1))
    grid.sort(key=lambda row: row[0])
    if len(grid) > 200_000:
        raise ValueError("per-case quote budget exceeded")
    output = []
    for at, index in grid:
        if index < 0:
            # An event before the first quote is retained by reconstruct_risk.
            continue
        fx_index = int(np.searchsorted(fx_times, at, side="right")) - 1
        fx_time = pd.Timestamp(int(fx_times[fx_index]), tz="UTC").to_pydatetime() if fx_index >= 0 else None
        fx = fx_values[fx_index] if fx_index >= 0 else (None, None)
        output.append(RiskQuote(
            pd.Timestamp(at, tz="UTC").to_pydatetime(), *values[index], fx_time, *fx,
            market_at=pd.Timestamp(int(times[index]), tz="UTC").to_pydatetime(),
        ))
    return output


def _summary(trace):
    points = trace.pop("samples")
    trace["samples_count"] = len(points)
    trace["samples_sha256"] = hashlib.sha256(encode(points)).hexdigest()
    return trace


def compare(study, analysis, output, *, max_market_gap_ms):
    study, analysis, output = Path(study), Path(analysis), Path(output)
    if output.exists():
        raise ValueError("immutable output already exists")
    started, watched = time.monotonic(), {}
    sources = {name: digest(ROOT / name) for name in SOURCES}
    protocol, protocol_sha = _read(study / "protocol.json", watched)
    results, _ = _read(study / "independent_results.json", watched)
    metadata, metadata_sha = _read(study / "input_diagnostics.json", watched)
    manifest, _ = _read(analysis / "analysis_manifest.json", watched)
    ledgers, ledger_sha = _read(analysis / "observed_ledgers.json", watched)
    if (protocol.get("contract") != "raw_message_control_diagnostic_v2"
            or protocol.get("account_currency") != "EUR"
            or results.get("protocol_sha256") != protocol_sha
            or metadata_sha != protocol.get("input_diagnostics_sha256")
            or manifest.get("source_capture_manifest_sha256") != protocol.get("source_capture_manifest_sha256")
            or ledgers.get("account_currency") != "EUR"):
        raise ValueError("archived control, metadata or native currency binding mismatch")
    proofs = [row for row in manifest["artifacts"] if row["name"] == "observed_ledgers.json"]
    if len(proofs) != 1 or proofs[0]["sha256"] != ledger_sha or proofs[0]["bytes"] != (analysis / "observed_ledgers.json").stat().st_size:
        raise ValueError("native ledger source binding mismatch")
    directions = {row["signal_id"]: row["direction"] for row in metadata["signals"]}
    if len(directions) != len(metadata["signals"]) or list(directions) != protocol["expected_signal_ids"]:
        raise ValueError("causal universe mismatch")
    actual = {row["sig_id"]: row for row in ledgers["signals"]}
    if len(actual) != len(ledgers["signals"]):
        raise ValueError("duplicate native signal")
    spec = RiskSpec("EUR", protocol["currency_digits"], protocol["contract_size"],
                    "account_base_profit_quote", protocol["fx_max_age_ms"], max_market_gap_ms)
    market = _tape(protocol["tapes"]["XAUUSD"], protocol["broker_epoch_offset_seconds"], watched)
    conversion = _tape(protocol["tapes"]["EURUSD"], protocol["broker_epoch_offset_seconds"], watched)
    expected = {(sig, scenario["latency_ms"], scenario["entry_fill_latency_ms"])
                for sig in directions for scenario in protocol["execution_scenarios"]}
    controls = {}
    for control in results["results"]:
        key = (control["signal_id"], control["latency_ms"], control["entry_fill_latency_ms"])
        if key in controls:
            raise ValueError("duplicate independent control")
        controls[key] = control
    keys = expected | controls.keys() | {(sig, None, None) for sig in actual.keys() - directions.keys()}
    if len(keys) > MAX_CASES:
        raise ValueError("case budget exceeded; no silent truncation")
    rows = []
    for key in sorted(keys, key=str):
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("risk comparison wall budget exceeded")
        sig, observation_delay, fill_delay = key
        row = {"signal_id": sig, "channel": sig.split("_", 1)[0],
               "latency_ms": observation_delay, "entry_fill_latency_ms": fill_delay}
        try:
            if key not in expected or key not in controls or sig not in actual:
                raise ValueError("missing_or_unexpected_control_or_native_signal")
            control, ledger = controls[key], actual[sig]
            if ledger.get("account_currency") != "EUR" or ledger.get("direction") != directions[sig]:
                raise ValueError("native_direction_or_currency_mismatch")
            if any(control["engine_mismatches"].values()):
                raise ValueError("archived_engine_disagreement")
            if ledger["n_positions"] == 0 and ledger.get("no_position_outcome_verified") is not True:
                raise ValueError("no_trade_not_independently_verified")
            for position in ledger["positions"]:
                for deal in position["deals"]:
                    if Decimal(str(deal["swap"])) != 0:
                        raise ValueError("accrued_swap_trajectory_unavailable")
                    if type(deal.get("time_msc")) is not int:
                        raise ValueError("native_millisecond_time_missing")
            observed = observed_events(ledger)
            simulated = simulated_events(control["result"], directions[sig])
            events = observed + simulated
            start = min((row.at for row in events), default=pd.Timestamp(protocol["start_utc"]).to_pydatetime())
            end = max((row.at for row in events), default=pd.Timestamp(protocol["cutoff_utc"]).to_pydatetime())
            # Open simulated positions must be marked through the declared cutoff.
            if sum((r.volume if r.kind == "entry" else -r.volume for r in simulated), Decimal(0)):
                end = max(end, pd.Timestamp(protocol["cutoff_utc"]).to_pydatetime())
            tape = paired_quotes(market, conversion, events, start=start, end=end)
            result = compare_risk(observed, simulated, tape, spec=spec)
            result["observed"] = _summary(result["observed"])
            result["simulated"] = _summary(result["simulated"])
            row["comparison"] = result
        except (ValueError, KeyError, TypeError) as exc:
            row["comparison"] = {"status": "blocked", "blockers": [str(exc)], "full_live_parity_verified": False}
        rows.append(row)
    for path, sha in watched.items():
        verify_frozen(path, sha)
    if any(digest(ROOT / name) != sha for name, sha in sources.items()):
        raise ValueError("comparator sources changed during run")
    report = {"contract": "common_grid_risk_comparison_v1", "status": "diagnostic_only",
              "full_live_parity_verified": False, "strategy_search_performed": False,
              "risk_spec": asdict(spec), "expected_controls": len(expected), "rows": rows,
              "statuses": dict(Counter(row["comparison"]["status"] for row in rows)),
              "inputs": {str(path): sha for path, sha in watched.items()}, "sources": sources,
              "elapsed_seconds": time.monotonic() - started,
              "inherited_admission_blockers": protocol["admission_blockers"],
              "limitations": ["Historical archived hypotheses, not current-engine recertification or OOS.",
                              "Common retained XAUUSD observations plus deal boundaries; not all intervening FX ticks.",
                              "Post-deal same-timestamp convention, not proof of intra-millisecond broker order.",
                              "Booked deal costs; nonzero accrued swap is unsupported and blocked.",
                              "Per-basket EUR path, not shared-account drawdown or margin.",
                              "Sample hashes support reproducibility; source tapes and events remain required."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-market-gap-ms", type=int, required=True)
    args = parser.parse_args()
    report = compare(args.study, args.analysis, args.output, max_market_gap_ms=args.max_market_gap_ms)
    print({"output": str(args.output), "statuses": report["statuses"], "expected_controls": report["expected_controls"]})


if __name__ == "__main__":
    main()
