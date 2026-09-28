"""Source-bound scalar-path comparison for an incremental shared window."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from tools.probe_canal1_incremental_window import digest


PROJECT = Path(__file__).resolve().parents[1]


def _time(value):
    return datetime.fromisoformat(value)


def _minor(value):
    if value is None:
        return None
    minor = Decimal(str(value)) * 100
    if not minor.is_finite() or minor != minor.to_integral_value():
        raise ValueError("observed money is not exact euro cents")
    return int(minor)


def _leg_comparison(basket, positions, offset_seconds):
    model_entries = sorted(basket["entries"], key=lambda row: _time(row["opened_at"]))
    native_entries = sorted(positions, key=lambda row: (row["first_native_msc"],
                                                      row["position_id"]))
    model_volumes = [Decimal(str(row["volume"])) for row in model_entries]
    native_volumes = [Decimal(str(row["entry_volume"])) for row in native_entries]
    exits = {row["ticket"]: row for row in basket["exits"]}
    unique_exits = len(exits) == len(basket["exits"])
    entry_deltas, exit_deltas = [], []
    if len(model_entries) == len(native_entries):
        for model_entry, native_entry in zip(model_entries, native_entries):
            native_open = datetime.fromtimestamp(
                native_entry["first_native_msc"] / 1000 - offset_seconds,
                timezone.utc)
            entry_deltas.append(int((_time(model_entry["opened_at"])
                                     - native_open).total_seconds() * 1000))
            model_exit = exits.get(model_entry["ticket"]) if unique_exits else None
            if model_exit is None:
                exit_deltas.append(None)
            else:
                native_close = datetime.fromtimestamp(
                    native_entry["last_native_msc"] / 1000 - offset_seconds,
                    timezone.utc)
                exit_deltas.append(int((_time(model_exit["closed_at"])
                                        - native_close).total_seconds() * 1000))
    return {"model_leg_volumes": [str(value) for value in model_volumes],
            "native_leg_volumes": [str(value) for value in native_volumes],
            "leg_volume_profile_match": model_volumes == native_volumes,
            "entry_delta_ms_by_ordinal": entry_deltas,
            "exit_delta_ms_by_ordinal": exit_deltas,
            "ordinal_leg_pairing_not_ticket_identity": True}


def compare_rows(model, native, native_positions):
    """Keep every model scope and every native basket closing in the window."""
    start, cutoff = _time(model["start_utc"]), _time(model["cutoff_utc"])
    if not start < cutoff:
        raise ValueError("invalid comparison window")
    native_by_id = {row["signal_id"]: row for row in native["baskets"]}
    if len(native_by_id) != len(native["baskets"]):
        raise ValueError("duplicate native basket identity")
    positions_by_id = {}
    for position in native_positions:
        positions_by_id.setdefault(position["signal_id"], []).append(position)
    model_ids, rows = set(), []
    for basket in model["basket_rows"]:
        signal_id = basket["signal_id"]
        if signal_id in model_ids:
            raise ValueError("duplicate model basket identity")
        model_ids.add(signal_id)
        risk = model["basket_risk"][signal_id]
        if risk["channel"] != basket["channel"]:
            raise ValueError("model basket risk channel mismatch")
        row = {"channel": basket["channel"], "signal_id": signal_id,
               "model_entry_count": len(basket["entries"]),
               "model_exit_count": len(basket["exits"]),
               "model_first_entry_utc": (basket["entries"][0]["opened_at"]
                                         if basket["entries"] else None),
               "model_last_exit_utc": (basket["exits"][-1]["closed_at"]
                                       if basket["exits"] else None),
               "model_net_minor": risk["final_total_minor_model"],
               "model_drawdown_minor": risk["max_drawdown_minor_model"],
               "model_minimum_minor": risk["minimum_from_origin_minor_model"],
               "model_maximum_minor": risk["maximum_from_origin_minor_model"],
               "model_max_gross_volume": risk["max_gross_volume_model"],
               "model_risk_path_sha256": risk["path_sha256"],
               "model_blockers": [*basket["blockers"], *risk["blockers"]],
               "trajectory_parity_verified": False}
        observed = native_by_id.get(signal_id)
        if observed is None:
            row["status"] = ("model_entry_without_native" if basket["entries"]
                             else "model_no_entry")
            rows.append(row)
            continue
        if observed["channel"] != basket["channel"]:
            raise ValueError("native and model channel mismatch")
        native_legs = positions_by_id.get(signal_id, [])
        if len(native_legs) != observed["position_count"]:
            raise ValueError("native basket position count differs from bound ledger")
        row.update(_leg_comparison(
            basket, native_legs, model["clock_offset_seconds_hypothesis"]))
        metrics = observed["path"].get("metrics")
        row.update(native_position_count=observed["position_count"],
                   native_status=observed["status"],
                   native_strict_causal_fx=observed["strict_causal_fx_path_complete"])
        if (metrics is None or observed["status"] != "retrospective_complete"
                or not observed["strict_causal_fx_path_complete"]
                or row["model_blockers"] or any(
                    row[name] is None for name in (
                        "model_net_minor", "model_drawdown_minor",
                        "model_minimum_minor", "model_maximum_minor"))):
            row["status"] = "blocked_comparison"
            rows.append(row)
            continue
        observed_net = _minor(metrics["final_net"])
        observed_dd = _minor(metrics["max_drawdown"])
        observed_min = _minor(metrics["minimum_from_origin"])
        observed_max = _minor(metrics["maximum_from_origin"])
        exit_delta = (int((_time(row["model_last_exit_utc"])
                           - _time(metrics["final_at"])).total_seconds() * 1000)
                      if row["model_last_exit_utc"] else None)
        volume_delta = (Decimal(row["model_max_gross_volume"])
                        - Decimal(str(metrics["max_gross_volume"])))
        row.update(native_final_utc=metrics["final_at"],
                   native_net_minor=observed_net,
                   native_drawdown_minor=observed_dd,
                   native_minimum_minor=observed_min,
                   native_maximum_minor=observed_max,
                   native_max_gross_volume=str(metrics["max_gross_volume"]),
                   net_delta_minor=row["model_net_minor"] - observed_net,
                   drawdown_delta_minor=row["model_drawdown_minor"] - observed_dd,
                   minimum_delta_minor=row["model_minimum_minor"] - observed_min,
                   maximum_delta_minor=row["model_maximum_minor"] - observed_max,
                   max_gross_volume_delta=str(volume_delta),
                   exit_delta_ms=exit_delta,
                   max_concurrent_volume_match=volume_delta == 0)
        differences = (row[name] for name in (
            "net_delta_minor", "drawdown_delta_minor", "minimum_delta_minor",
            "maximum_delta_minor", "exit_delta_ms"))
        if not row["leg_volume_profile_match"]:
            row["status"] = "matched_different_leg_volume_profile"
        elif volume_delta != 0:
            row["status"] = "matched_concurrent_exposure_mismatch"
        else:
            row["status"] = ("matched_path_discrepant"
                             if any(value != 0 for value in differences)
                             else "matched_scalar_only")
        rows.append(row)
    for observed in native["baskets"]:
        if observed["signal_id"] in model_ids:
            continue
        metrics = observed["path"].get("metrics") or observed["path"].get("known_sample_metrics")
        if metrics is None or metrics.get("final_at") is None:
            rows.append({"channel": observed["channel"],
                         "signal_id": observed["signal_id"],
                         "status": "native_time_unknown",
                         "trajectory_parity_verified": False})
        elif start <= _time(metrics["final_at"]) < cutoff:
            rows.append({"channel": observed["channel"],
                         "signal_id": observed["signal_id"],
                         "status": "native_without_model",
                         "native_final_utc": metrics["final_at"],
                         "native_position_count": observed["position_count"],
                         "trajectory_parity_verified": False})
    counts = Counter(row["status"] for row in rows)
    return rows, {
        "matched": sum(counts[name] for name in (
            "matched_different_leg_volume_profile",
            "matched_concurrent_exposure_mismatch",
            "matched_path_discrepant", "matched_scalar_only")),
        "model_no_entry": counts["model_no_entry"],
        "model_entry_without_native": counts["model_entry_without_native"],
        "native_without_model": counts["native_without_model"],
        "blocked_comparison": counts["blocked_comparison"],
        "native_time_unknown": counts["native_time_unknown"],
        "different_leg_volume_profile": counts["matched_different_leg_volume_profile"],
        "concurrent_exposure_mismatch": counts["matched_concurrent_exposure_mismatch"],
        "path_discrepant": counts["matched_path_discrepant"],
    }


def verify_sources(model, native, *, model_path, native_path, frozen_root):
    """Reject stale or unbound diagnostics before their values are compared."""
    sources = model["sources"]
    if sources["runner_sha256"] != digest(PROJECT / "tools/probe_canal1_incremental_window.py"):
        raise ValueError("model runner hash changed")
    for name, sha in sources["implementation_sha256"].items():
        if digest(PROJECT / name) != sha:
            raise ValueError(f"model implementation hash changed: {name}")
    raw_path = frozen_root / "raw_week_20260914_19_bg_v1.json"
    if digest(raw_path) != sources["raw_sha256"]:
        raise ValueError("model raw Telegram source changed")
    day = _time(model["start_utc"]).date().isoformat()
    for symbol in ("XAUUSD", "EURUSD"):
        stem = frozen_root / symbol / day
        if (digest(stem.with_suffix(".json")) != sources[symbol]["metadata_sha256"]
                or digest(stem.with_suffix(".parquet")) != sources[symbol]["parquet_sha256"]):
            raise ValueError(f"model {symbol} tape source changed")
    native_inputs = native["inputs_sha256"]
    for name, sha in native_inputs.items():
        path = Path(name)
        if digest(path if path.is_absolute() else PROJECT / path) != sha:
            raise ValueError(f"native risk source changed: {name}")
    if (native["source_clock_offset_seconds_hypothesis"]
            != model["clock_offset_seconds_hypothesis"]):
        raise ValueError("native/model clock hypotheses differ")
    for symbol in ("XAUUSD", "EURUSD"):
        matching = [sha for name, sha in native_inputs.items()
                    if Path(name).parent.name == symbol
                    and Path(name).name == f"{day}.parquet"]
        if matching != [sources[symbol]["parquet_sha256"]]:
            raise ValueError(f"native/model {symbol} tape mismatch")
    start = model.get("native_start_control")
    if start is None or start["open_position_count"] != 0:
        raise ValueError("native-flat model start control absent")
    for filename, field in (("native_week_reconciled.json", "reconciled_sha256"),
                            ("native_week_deals_20260919.json", "deals_sha256")):
        matching = [sha for name, sha in native_inputs.items()
                    if Path(name).name == filename]
        if matching != [start[field]]:
            raise ValueError("native start ledger not bound to risk source")
    return {"model_sha256": digest(model_path), "native_sha256": digest(native_path),
            "raw_sha256": sources["raw_sha256"],
            "native_input_count": len(native_inputs),
            "shared_tape_day": day,
            "model_implementation_hash_count": len(sources["implementation_sha256"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--native-reconciled", type=Path, required=True)
    parser.add_argument("--frozen-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model = json.loads(args.model.read_text(encoding="utf-8"))
    native = json.loads(args.native.read_text(encoding="utf-8"))
    reconciled = json.loads(args.native_reconciled.read_text(encoding="utf-8"))
    sources = verify_sources(model, native, model_path=args.model,
                             native_path=args.native, frozen_root=args.frozen_root)
    if digest(args.native_reconciled) != model["native_start_control"]["reconciled_sha256"]:
        raise ValueError("native position ledger changed")
    rows, counts = compare_rows(model, native, reconciled["positions"])
    report = {"contract": "incremental_native_scalar_path_comparison_v1",
              "status": "diagnostic_only", "trajectory_parity_verified": False,
              "start_utc": model["start_utc"], "cutoff_utc": model["cutoff_utc"],
              "optional_close_choice": model["optional_close_choice"],
              "sources": {**sources, "comparator_sha256": digest(__file__)},
              "counts": counts, "rows": rows,
              "limitations": [
                  "Scalar extrema and event times do not establish sample-by-sample risk parity.",
                  "Model uses an uncalibrated execution profile and assumed complete signal universe.",
                  "Native risks reconstruct actual fills; model risks use hypothetical fills.",
                  "The shared account portfolio, margin and unobserved positions are not certified.",
                  "Per-leg entry/exit timing uses ordinal pairing, not common broker ticket identity.",
              ]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, sort_keys=True, allow_nan=False)
        target.write("\n")
    print(json.dumps({"status": report["status"], "counts": counts,
                      "discrepant": [row["signal_id"] for row in rows
                                      if row["status"] in {
                                          "matched_different_leg_volume_profile",
                                          "matched_concurrent_exposure_mismatch",
                                          "matched_path_discrepant"}]}))


if __name__ == "__main__":
    main()
