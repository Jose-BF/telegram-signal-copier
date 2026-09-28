"""Compare native basket exposure with held shadow state at quiet snapshots.

Snapshot acquisition time was not recorded in the historical journal. A point
is admitted only if neither side changed exposure during its prior five seconds.
This is a diagnostic of selected states, not continuous path certification.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audit_native_money_anchor import digest, read
from tools.audit_native_position_profit_interval import direct_same_day_offset
from tools.audit_week_shadow_control_path import (
    controls_and_states, shadow_rows, utc_ms,
)
from tools.verify_lifecycle_probe_projection import compare_projection


WINDOW_MS = 5_000
MAX_SHADOW_STATE_AGE_MS = 60_000
MAX_PAIRS = 10
MAX_SNAPSHOTS = 1_000


def _bound(report, path):
    matches = [sha for name, sha in report.get("inputs_sha256", {}).items()
               if Path(name).resolve() == Path(path).resolve()]
    if matches != [digest(path)]:
        raise ValueError(f"input hash binding missing or changed: {path}")


def evaluate_snapshot(snapshot, mark, positions, shadow_events, *, offset_seconds,
                      window_ms=WINDOW_MS):
    if type(window_ms) is not int or not 0 < window_ms <= WINDOW_MS:
        raise ValueError("invalid snapshot uncertainty window")
    signal, ticket = snapshot["sig"], snapshot["ticket"]
    if any(position["signal_id"] != signal for position in positions):
        raise ValueError("mixed native basket positions")
    if (mark.get("event_id") != snapshot["event_id"]
            or mark.get("ticket") != ticket or mark.get("signal_id", signal) != signal):
        raise ValueError("native mark and snapshot identity mismatch")
    event_times = [utc_ms(event["ts"]) for event in shadow_events]
    if any(later < earlier for earlier, later in zip(event_times, event_times[1:])):
        raise ValueError("shadow state time regression")
    at = utc_ms(snapshot["ts"])
    base = {"signal_id": signal, "event_id": snapshot["event_id"],
            "ticket": ticket, "snapshot_utc": snapshot["ts"],
            "uncertain_prior_ms": window_ms}
    if snapshot.get("position_exists") is not True:
        return {**base, "status": ("blocked_closed_snapshot" if snapshot.get("position_exists") is False
                                    else "blocked_unknown_position")}
    if (mark.get("native_mark_status") != "native_mark_profit_matches_same_prior_quote"
            or type(mark.get("native_mark_latest_matching_quote_age_ms")) is not int
            or not 0 <= mark["native_mark_latest_matching_quote_age_ms"] <= window_ms):
        return {**base, "status": "blocked_native_mark"}
    if not positions or any(len(position.get("deal_tickets", [])) != 2 for position in positions):
        return {**base, "status": "blocked_partial_native_lifecycle"}
    source_at = at + offset_seconds * 1_000
    selected = [position for position in positions if position["position_id"] == ticket]
    if len(selected) != 1:
        raise ValueError("native snapshot ticket is not uniquely bound")
    if source_at >= selected[0]["exit_msc"]:
        return {**base, "status": "blocked_post_exit_emit_read_order_unknown",
                "journal_after_native_exit_ms": source_at - selected[0]["exit_msc"]}
    if source_at < selected[0]["entry_msc"]:
        return {**base, "status": "blocked_pre_entry_emit_read_order_unknown",
                "native_entry_after_journal_ms": selected[0]["entry_msc"] - source_at}
    if any(source_at - window_ms <= position[key] <= source_at
           for position in positions for key in ("entry_msc", "exit_msc")):
        return {**base, "status": "blocked_recent_native_deal"}
    prior = [event for event, stamp in zip(shadow_events, event_times, strict=True)
             if stamp < at]
    if not prior:
        return {**base, "status": "blocked_no_prior_shadow_state"}
    if any(at - window_ms <= stamp <= at for stamp in event_times):
        return {**base, "status": "blocked_recent_shadow_event"}
    if any(event.get("ev") == "strategy_shadow_transition"
           and event.get("transition") in ("virtual_fill", "virtual_position_closed",
                                            "basket_exit")
           and type(event.get("transition_tick_msc")) is int
           and event["transition_tick_msc"] <= at <= stamp
           for event, stamp in zip(shadow_events, event_times, strict=True)):
        return {**base, "status": "blocked_shadow_tick_emit_order_unknown"}
    last = prior[-1]
    age = at - utc_ms(last["ts"])
    if age > MAX_SHADOW_STATE_AGE_MS:
        return {**base, "status": "blocked_stale_shadow_state",
                "shadow_state_age_ms": age}
    native = sum((Decimal(str(position["volume"])) for position in positions
                  if position["entry_msc"] <= source_at < position["exit_msc"]), Decimal(0))
    shadow = sum((Decimal(str(position["volume"])) for position in last["state"]["positions"]
                  if position["status"] == "open"), Decimal(0))
    delta = shadow - native
    return {**base, "status": ("stable_exposure_equal" if delta == 0
                                else "stable_exposure_difference"),
            "source_asof_msc": source_at,
            "native_open_volume": str(native), "shadow_open_volume": str(shadow),
            "volume_delta": str(delta), "shadow_state_event_id": last["event_id"],
            "shadow_state_age_ms": age,
            "shadow_state_hash": last["state_hash"]}


def audit(shadow_slice, shadow_manifest, old_probes, new_probes, verifications,
          mark_path, money_path, anchor_path):
    old_probes, new_probes, verifications = (
        tuple(map(Path, values)) for values in (old_probes, new_probes, verifications))
    paths = tuple(map(Path, (shadow_slice, shadow_manifest, mark_path, money_path, anchor_path)))
    shadow_slice, shadow_manifest, mark_path, money_path, anchor_path = paths
    if not 0 < len(old_probes) == len(new_probes) == len(verifications) <= MAX_PAIRS:
        raise ValueError("invalid verified probe pair count")
    rows = shadow_rows(shadow_slice, shadow_manifest)
    controls, states, registered, manifest_hash = controls_and_states(rows)
    mark, money, anchor = map(read, (mark_path, money_path, anchor_path))
    if (mark.get("contract") != "native_position_profit_prior_quote_diagnostic_v1"
            or money.get("contract") != "native_closed_money_anchor_v2"
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"):
        raise ValueError("shadow snapshot source contract mismatch")
    for path in (*new_probes, money_path, anchor_path):
        _bound(mark, path)
    marks = {row["event_id"]: row for row in mark["rows"]}
    if len(marks) != len(mark["rows"]):
        raise ValueError("duplicate native mark event")
    native = defaultdict(list)
    for position in money["positions"]:
        native[position["signal_id"]].append(position)
    results, seen = [], set()
    for old_path, new_path, verification_path in zip(
            old_probes, new_probes, verifications, strict=True):
        old, new, verification = map(read, (old_path, new_path, verification_path))
        current = compare_projection(old, new, restore_native_details=True,
                                     allow_schema_expansion=True)
        for path in (old_path, new_path):
            _bound(verification, path)
        if (verification.get("status") != "projection_verified"
                or verification.get("signal_id") != current["signal_id"]
                or verification.get("source_window_sha256") != current["source_window_sha256"]
                or verification.get("snapshot_count") != current["snapshot_count"]
                or verification.get("native_open_position_detail_count")
                != current["native_open_position_detail_count"]):
            raise ValueError("native probe projection verification mismatch")
        signal = current["signal_id"]
        if signal not in states or not any(key[0] == signal for key in registered):
            raise ValueError("verified signal lacks live-control shadow chain")
        snapshots = [row for row in new["events"] if row.get("ev") == "mt5_position_snapshot"]
        for snapshot in snapshots:
            event_id = snapshot["event_id"]
            if event_id in seen or event_id not in marks:
                raise ValueError("duplicate or unbound native snapshot")
            seen.add(event_id)
            try:
                offset = direct_same_day_offset(
                    snapshot["ts"], anchor["independent_clock_evidence"]["days"])
            except ValueError:
                results.append({"signal_id": signal, "event_id": event_id,
                                "ticket": snapshot["ticket"], "status": "blocked_direct_clock"})
                continue
            results.append(evaluate_snapshot(snapshot, marks[event_id], native[signal],
                                             states[signal], offset_seconds=offset))
            if len(results) > MAX_SNAPSHOTS:
                raise ValueError("shadow snapshot budget exceeded")
    watched = {str(path): digest(path) for path in
               (*paths, *old_probes, *new_probes, *verifications, Path(__file__))}
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("shadow snapshot source changed during audit")
    return {"contract": "shadow_native_snapshot_exposure_diagnostic_v1",
            "status": "diagnostic_only", "control_manifest_hash": manifest_hash,
            "controls": controls, "snapshot_count": len(results),
            "statuses": dict(sorted(Counter(row["status"] for row in results).items())),
            "rows": results, "inputs_sha256": watched,
            "full_simulator_path_parity_verified": False,
            "limitations": ["The historical native snapshot acquisition instant was not logged.",
                            "Only five-second quiet windows with a recent shadow state are admitted.",
                            "Shadow journal state is a sampled live control, not an independent replay.",
                            "This checks basket volume, not continuous account equity, money or drawdown."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow", type=Path, required=True)
    parser.add_argument("--shadow-manifest", type=Path, required=True)
    parser.add_argument("--old-probe", type=Path, action="append", required=True)
    parser.add_argument("--new-probe", type=Path, action="append", required=True)
    parser.add_argument("--verification", type=Path, action="append", required=True)
    parser.add_argument("--mark", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(args.shadow, args.shadow_manifest, args.old_probe, args.new_probe,
                   args.verification, args.mark, args.money, args.anchor)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"snapshots": report["snapshot_count"],
                      "statuses": report["statuses"]}), flush=True)


if __name__ == "__main__":
    main()
