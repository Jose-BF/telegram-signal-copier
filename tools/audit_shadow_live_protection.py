"""Pair bounded live MT5 protection observations with emitted shadow states."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audit_native_money_anchor import digest, read
from tools.audit_native_week_risk_path import OFFSET_SECONDS
from tools.audit_week_shadow_control_path import controls_and_states, shadow_rows, utc_ms
from tools.plan_vm_week_lifecycle import native_at
from tools.probe_vm_signal_lifecycle import MAX_PER_KIND
from mt5_deal_reason import DEAL_REASON_NAMES


REQUIRED_EVENTS = ("gold_555_first_leg_filled", "dca_filled", "mt5_order_result",
                   "mt5_modify_requested", "mt5_action_attempt",
                   "mt5_modify_confirmed", "mt5_position_snapshot",
                   "strategy_shadow_transition")


def validate_shadow_transition_coverage(probe, shadow, control_rows, *, signal,
                                        fingerprint, window_start, window_end):
    declared = probe["event_counts"].get("strategy_shadow_transition", 0)
    captured = [row for row in probe["events"]
                if row["ev"] == "strategy_shadow_transition"]
    source = [row for row in shadow if row.get("ev") == "strategy_shadow_transition"
              and row.get("sig") == signal
              and window_start <= utc_ms(row["ts"]) < window_end]
    source_ids = {row.get("event_id") for row in source}
    captured_ids = {row.get("event_id") for row in captured}
    control_ids = {row.get("event_id") for row in control_rows}
    source_control_ids = {row.get("event_id") for row in source
                          if row.get("strategy_fingerprint") == fingerprint}
    expected_sampled = declared if declared > MAX_PER_KIND else None
    if (not declared or len(captured) != min(declared, MAX_PER_KIND)
            or probe.get("sampled_kinds", {}).get("strategy_shadow_transition") != expected_sampled
            or len(source) != declared or len(source_ids) != declared
            or None in source_ids or not captured_ids <= source_ids
            or control_ids != source_control_ids):
        raise ValueError("probe and shadow slice transition coverage differs")
    return {"probe_captured": len(captured), "probe_declared": declared,
            "hashed_shadow_slice_count": len(source),
            "probe_shadow_transition_sampled": declared > MAX_PER_KIND,
            "control_transition_count": len(control_rows)}


def bind_live_legs(events, positions, native_deals, *, strategy_id, fingerprint):
    fills = [row for row in events if row["ev"] in {"gold_555_first_leg_filled", "dca_filled"}]
    results = [row for row in events if row["ev"] == "mt5_order_result" and row.get("retcode") == 10009]
    entries = [row for row in native_deals if row.get("entry") == 0]
    blocked = {"status": "blocked_native_entry", "leg_to_ticket": {}, "rows": [],
               "full_entry_identity_verified": False}
    if not positions or len(fills) != len(results) or len(fills) != len(entries) or len(fills) != len(positions):
        return blocked
    legs = {}
    for fill in fills:
        leg = 0 if fill["ev"] == "gold_555_first_leg_filled" else fill.get("candidate_leg_index")
        if (type(leg) is not int or leg in legs or fill.get("strategy_id") != strategy_id
                or fill.get("strategy_fingerprint") != fingerprint):
            return blocked
        legs[leg] = fill
    if set(legs) != set(range(len(positions))):
        return blocked
    by_ticket = {row["position_id"]: row for row in positions}
    if len(by_ticket) != len(positions):
        return blocked
    pairs = []
    for leg, fill in sorted(legs.items()):
        ticket = fill.get("ticket")
        position = by_ticket.get(ticket)
        deals = [row for row in entries if row.get("position_id") == ticket]
        if position is None or len(deals) != 1:
            return blocked
        deal = deals[0]
        matched = [row for row in results if row.get("deal") == deal.get("ticket")
                   and row.get("order") == ticket]
        if len(matched) != 1:
            return blocked
        result = matched[0]
        try:
            value = Decimal(str(fill["fill_price"]))
            volume = Decimal(str(fill.get("volume", fill.get("lot"))))
            if (value != Decimal(str(position["entry_price"])) or value != Decimal(str(deal["price"]))
                    or value != Decimal(str(result["price"])) or volume <= 0
                    or any(volume != Decimal(str(row["volume"])) for row in
                           (position, deal, result))
                    or not int(native_at(deal["time_msc"]).timestamp() * 1000)
                    <= utc_ms(result["ts"]) <= utc_ms(fill["ts"])):
                return blocked
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return blocked
        pairs.append({"leg_index": leg, "native_position_id": ticket,
                      "native_entry_deal": deal["ticket"], "entry_price": str(value),
                      "volume": str(volume)})
    return {"status": "verified_live_leg_journal_binding",
            "leg_to_ticket": {row["leg_index"]: row["native_position_id"] for row in pairs},
            "rows": pairs, "full_entry_identity_verified": True}


def compare_level_snapshots(events, shadow_transitions, positions, leg_to_ticket):
    by_ticket = {row["position_id"]: row for row in positions}
    rows = []
    for snapshot in (row for row in events if row["ev"] == "mt5_position_snapshot"):
        item = {"event_id": snapshot.get("event_id"), "ticket": snapshot.get("ticket"),
                "snapshot_logged_at": snapshot.get("ts"),
                "status": "blocked_snapshot_lineage",
                "broker_install_time_verified": False}
        ticket = snapshot.get("ticket")
        position = by_ticket.get(ticket)
        attempts = [row for row in events if row["ev"] == "mt5_action_attempt"
                    and row.get("operation") == "MODIFY_SLTP"
                    and row.get("action_id") == snapshot.get("action_id")
                    and row.get("attempt_id") == snapshot.get("attempt_id")
                    and row.get("ticket") == ticket]
        confirms = [row for row in events if row["ev"] == "mt5_modify_confirmed"
                    and row.get("action_id") == snapshot.get("action_id")
                    and row.get("attempt_id") == snapshot.get("attempt_id")
                    and row.get("ticket") == ticket]
        requests = [row for row in events if row["ev"] == "mt5_modify_requested"
                    and row.get("action_id") == snapshot.get("action_id")
                    and row.get("ticket") == ticket]
        if position is None or len(attempts) != 1 or len(confirms) != 1 or len(requests) != 1:
            rows.append(item)
            continue
        attempt, confirm, request = attempts[0], confirms[0], requests[0]
        lineage = ("action_id", "ticket", "decision_id", "session_id")
        try:
            at = utc_ms(snapshot["ts"])
            request_at = utc_ms(request["ts"])
            started = utc_ms(attempt["broker_request_started_utc"])
            response_at = utc_ms(attempt["broker_response_received_utc"])
            confirm_at = utc_ms(confirm["ts"])
            levels = all(Decimal(str(snapshot[level])) == Decimal(str(attempt[f"request_{level}"]))
                         for level in ("sl", "tp"))
            confirmation_levels = all(
                confirm.get(f"new_{level}") is None
                or Decimal(str(confirm[f"new_{level}"])) ==
                   Decimal(str(attempt[f"request_{level}"]))
                for level in ("sl", "tp"))
            valid = (attempt.get("broker_request_sent") is True
                     and attempt.get("result_retcode") == confirm.get("retcode") == snapshot.get("retcode") == 10009
                     and all(all(row.get(field) == request.get(field) and request.get(field) is not None
                                 for field in lineage) for row in (attempt, confirm, snapshot))
                     and attempt.get("attempt_id") and request_at <= started <= response_at <= confirm_at <= at
                     and levels and confirmation_levels
                     and Decimal(str(snapshot["volume"])) == Decimal(str(position["volume"])))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            valid = False
        if not valid:
            rows.append(item)
            continue
        item["action_id"] = snapshot["action_id"]
        item["attempt_id"] = snapshot["attempt_id"]
        item["broker_request_started_utc"] = attempt["broker_request_started_utc"]
        item["broker_response_received_utc"] = attempt["broker_response_received_utc"]
        item["observed_sl"] = str(Decimal(str(snapshot["sl"])))
        item["observed_tp"] = str(Decimal(str(snapshot["tp"])))
        if snapshot.get("position_exists") is False:
            item["status"] = "blocked_position_absent"
        elif at >= position["exit_msc"] - OFFSET_SECONDS * 1000:
            item["status"] = "blocked_native_exit_before_snapshot_log"
        elif at < position["entry_msc"] - OFFSET_SECONDS * 1000:
            item["status"] = "blocked_snapshot_before_native_entry"
        else:
            leg = next((index for index, native_ticket in leg_to_ticket.items()
                        if native_ticket == ticket), None)
            if leg is None:
                item["status"] = "blocked_live_leg_identity"
            else:
                eligible = [row for row in shadow_transitions
                            if utc_ms(row["ts"]) <= at
                            and row.get("transition_tick_msc") is not None
                            and row["transition_tick_msc"] <= utc_ms(row["ts"])]
                if not eligible:
                    item["status"] = "blocked_no_prior_emitted_shadow_state"
                else:
                    shadow = max(enumerate(eligible), key=lambda pair: (utc_ms(pair[1]["ts"]), pair[0]))[1]
                    virtual = [p for p in shadow["state"]["positions"] if p["leg_index"] == leg]
                    if len(virtual) != 1 or virtual[0]["status"] != "open":
                        item["status"] = "blocked_shadow_leg_not_open"
                    elif virtual[0].get("stop_price") is None:
                        item["status"] = "blocked_virtual_stop_missing"
                    else:
                        item.update(status=("compared_level" if snapshot.get("position_exists") is True
                                            else "compared_level_without_existence_flag"),
                                    leg_index=leg, shadow_state_event_id=shadow.get("event_id"),
                                    shadow_tick_at_msc=shadow["transition_tick_msc"],
                                    shadow_emitted_at=shadow["ts"],
                                    shadow_emit_age_ms=at - utc_ms(shadow["ts"]),
                                    virtual_sl=str(Decimal(str(virtual[0]["stop_price"]))),
                                    virtual_minus_observed_sl=str(
                                        Decimal(str(virtual[0]["stop_price"])) -
                                        Decimal(str(snapshot["sl"]))))
                        target = virtual[0].get("target_price")
                        if target is not None:
                            item["virtual_tp"] = str(Decimal(str(target)))
                            item["virtual_minus_observed_tp"] = str(
                                Decimal(str(target)) - Decimal(str(snapshot["tp"])))
        rows.append(item)
    return {"snapshot_count": len(rows),
            "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
            "rows": rows, "full_protection_path_parity_verified": False}


def compare_exit_targets(positions, native_deals, shadow_transitions, bound_legs,
                         snapshot_rows, events):
    by_ticket = {row["position_id"]: row for row in positions}
    rows = []
    for bound in bound_legs:
        leg = bound["leg_index"]
        ticket = bound["native_position_id"]
        item = {"leg_index": leg, "native_position_id": ticket,
                "status": "blocked_exit_lineage", "broker_tp_install_verified": False}
        position = by_ticket.get(ticket)
        exits = [row for row in native_deals if row.get("position_id") == ticket
                 and row.get("entry") == 1]
        fills = [row for row in shadow_transitions if row.get("transition") == "virtual_fill"
                 and row.get("transition_details", {}).get("leg_index") == leg]
        closes = [row for row in shadow_transitions
                  if row.get("transition") == "virtual_position_closed"
                  and leg in row.get("transition_details", {}).get("leg_indexes", [])]
        if position is None or len(exits) != 1 or len(fills) != 1 or len(closes) != 1:
            rows.append(item)
            continue
        deal, fill, close = exits[0], fills[0], closes[0]
        try:
            native_entry = Decimal(str(bound["entry_price"]))
            native_exit = Decimal(str(deal["price"]))
            virtual_entry = Decimal(str(fill["transition_details"]["entry_price"]))
            virtual_target = Decimal(str(fill["transition_details"]["target_price"]))
            closed = [p for p in close["state"]["positions"] if p["leg_index"] == leg]
            native_exit_at = position["exit_msc"] - OFFSET_SECONDS * 1000
            native_entry_at = position["entry_msc"] - OFFSET_SECONDS * 1000
            valid = (len(closed) == 1 and closed[0]["status"] == "closed"
                     and Decimal(str(closed[0]["close_price"])) == virtual_target
                     and closed[0]["closed_tick_msc"] == close["transition_tick_msc"]
                     and Decimal(str(position["exit_price"])) == native_exit
                     and Decimal(str(position["volume"])) == Decimal(str(deal["volume"]))
                     and position["exit_msc"] == deal["time_msc"]
                     and native_entry_at < native_exit_at
                     and fill["transition_tick_msc"] < close["transition_tick_msc"]
                     and utc_ms(fill["ts"]) <= utc_ms(close["ts"])
                     and utc_ms(fill["ts"]) >= fill["transition_tick_msc"]
                     and utc_ms(close["ts"]) >= close["transition_tick_msc"])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            valid = False
        if not valid:
            rows.append(item)
            continue
        eligible = [row for row in snapshot_rows if row.get("ticket") == ticket
                    and row.get("observed_tp") is not None
                    and native_entry_at <= utc_ms(row["snapshot_logged_at"]) < native_exit_at]
        if not eligible:
            item["status"] = "blocked_no_pre_exit_tp_snapshot"
            rows.append(item)
            continue
        snapshot = min(eligible, key=lambda row: utc_ms(row["snapshot_logged_at"]))
        observed_tp = Decimal(snapshot["observed_tp"])
        response_at = utc_ms(snapshot["broker_response_received_utc"])
        close_emitted_at = utc_ms(close["ts"])
        rejected_before = [row for row in events if row["ev"] == "mt5_action_attempt"
                           and row.get("operation") == "MODIFY_SLTP"
                           and row.get("ticket") == ticket
                           and row.get("broker_request_sent") is True
                           and row.get("result_retcode") != 10009
                           and row.get("request_tp") is not None
                           and Decimal(str(row.get("request_tp"))) == observed_tp
                           and native_entry_at <= utc_ms(row["broker_response_received_utc"])
                           < response_at]
        entry_shift = native_entry - virtual_entry
        target_shift = observed_tp - virtual_target
        item.update(virtual_entry=str(virtual_entry), native_entry=str(native_entry),
                    virtual_target=str(virtual_target), observed_pre_exit_tp=str(observed_tp),
                    native_exit_price=str(native_exit), native_exit_reason_code=deal.get("reason"),
                    native_exit_reason=DEAL_REASON_NAMES.get(deal.get("reason"), "unknown"),
                    tp_snapshot_event_id=snapshot["event_id"],
                    tp_snapshot_has_position_exists=snapshot["status"] == "compared_level",
                    tp_rejected_attempts_before_confirmation=len(rejected_before),
                    tp_confirmation_response_minus_native_entry_ms=response_at - native_entry_at,
                    tp_confirmation_response_minus_virtual_close_tick_ms=(response_at
                                                                          - close["transition_tick_msc"]),
                    tp_response_minus_shadow_close_emit_ms=response_at - close_emitted_at,
                    shadow_close_emit_minus_tick_ms=close_emitted_at - close["transition_tick_msc"],
                    virtual_close_tick_before_accepted_tp_response=(close["transition_tick_msc"]
                                                                    < response_at),
                    virtual_close_emitted_before_accepted_tp_response=(close_emitted_at
                                                                        < response_at),
                    virtual_close_event_id=close["event_id"],
                    virtual_close_emitted_utc=close["ts"],
                    entry_shift=str(entry_shift), target_shift=str(target_shift),
                    target_shift_minus_entry_shift=str(target_shift - entry_shift),
                    native_exit_minus_virtual_close_tick_ms=(native_exit_at
                                                             - close["transition_tick_msc"]),
                    native_exit_minus_shadow_close_emit_ms=(native_exit_at
                                                             - utc_ms(close["ts"])),
                    status=("broker_tp_exit_matches_fill_relative_target"
                            if (target_shift == entry_shift and native_exit == observed_tp
                                and deal.get("reason") == 5) else
                            "observed_target_or_exit_mechanism_diverges"))
        rows.append(item)
    return {"statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
            "rows": rows, "full_exit_causal_path_verified": False}


def audit(signal, probe_path, shadow_path, shadow_manifest_path, money_path,
          native_path, anchor_path):
    paths = [Path(p) for p in (probe_path, shadow_path, shadow_manifest_path,
                               money_path, native_path, anchor_path)]
    reason_source = Path(__file__).resolve().parents[1] / "mt5_deal_reason.py"
    inputs = {path: digest(path) for path in paths + [Path(__file__), reason_source]}
    probe, money, native, anchor = (read(paths[index]) for index in (0, 3, 4, 5))
    shadow = shadow_rows(paths[1], paths[2])
    controls, states, registered, manifest_hash = controls_and_states(shadow)
    events = probe["events"]
    if (probe.get("status") != "diagnostic_only" or probe.get("window", {}).get("signal_id") != signal
            or controls.get("canal2") != "gold_now_555_v1"
            or (signal, "gold_now_555_v1") not in registered
            or money.get("contract") != "native_closed_money_anchor_v2"
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"):
        raise ValueError("probe, control, native money or clock contract mismatch")
    for path in (paths[4], paths[5]):
        proof = [sha for name, sha in money.get("inputs_sha256", {}).items()
                 if Path(name).resolve() == path.resolve()]
        if len(proof) != 1 or proof[0] != inputs[path]:
            raise ValueError("native money source or clock hash mismatch")
    if any(row.get("sig") != signal for row in events):
        raise ValueError("probe event signal identity mismatch")
    if (any(not isinstance(row.get("event_id"), str) or not row["event_id"] for row in events)
            or len({row["event_id"] for row in events}) != len(events)):
        raise ValueError("probe event ids duplicated or missing")
    counts = {kind: sum(row["ev"] == kind for row in events) for kind in REQUIRED_EVENTS}
    if any(counts[kind] != probe["event_counts"].get(kind, 0) for kind in REQUIRED_EVENTS
           if kind != "strategy_shadow_transition"):
        raise ValueError("probe relevant event kind truncated")
    control_rows = [row for row in states[signal] if row["ev"] == "strategy_shadow_transition"]
    if not control_rows:
        raise ValueError("shadow control transitions missing")
    fingerprint = control_rows[0]["strategy_fingerprint"]
    window_start = utc_ms(probe["window"]["start_utc"])
    window_end = utc_ms(probe["window"]["end_utc"])
    if window_start >= window_end:
        raise ValueError("probe window invalid")
    control_rows = [row for row in control_rows if window_start <= utc_ms(row["ts"]) < window_end]
    shadow_coverage = validate_shadow_transition_coverage(
        probe, shadow, control_rows, signal=signal, fingerprint=fingerprint,
        window_start=window_start, window_end=window_end)
    positions = [row for row in money["positions"] if row["signal_id"] == signal]
    deal_ids = {row["ticket"] for row in native["deals"] if row.get("entry") == 0
                and row.get("position_id") in {p["position_id"] for p in positions}}
    deals = [row for row in native["deals"] if row["ticket"] in deal_ids]
    if len(positions) != len(deals) or not positions:
        raise ValueError("native position/deal denominator missing")
    days = anchor["independent_clock_evidence"]["days"]
    direct = all(days.get(datetime.fromtimestamp(
        p[key] / 1000, timezone.utc).date().isoformat(), {}).get("status")
        == "direct_anchor_available" for p in positions for key in ("entry_msc", "exit_msc"))
    legs = (bind_live_legs(events, positions, deals, strategy_id="gold_now_555_v1",
                           fingerprint=fingerprint) if direct else
            {"status": "blocked_clock", "leg_to_ticket": {}, "rows": [],
             "full_entry_identity_verified": False})
    aligned = compare_level_snapshots(events, control_rows, positions, legs["leg_to_ticket"])
    exits = compare_exit_targets(positions, native["deals"], control_rows,
                                 legs["rows"], aligned["rows"], events)
    rejected = Counter(str(row.get("result_retcode")) for row in events
                       if row["ev"] == "mt5_action_attempt" and row.get("operation") == "MODIFY_SLTP"
                       and row.get("result_retcode") != 10009)
    rejected_rows = [{"action_id": row.get("action_id"), "attempt_id": row.get("attempt_id"),
                      "ticket": row.get("ticket"), "retcode": row.get("result_retcode"),
                      "at": row.get("ts")} for row in events
                     if row["ev"] == "mt5_action_attempt" and row.get("operation") == "MODIFY_SLTP"
                     and row.get("result_retcode") != 10009]
    if any(digest(path) != sha for path, sha in inputs.items()):
        raise ValueError("protection audit input changed during reconstruction")
    return {"contract": "shadow_live_protection_level_diagnostic_v1",
            "status": "diagnostic_only", "signal_id": signal,
            "control_manifest_hash": manifest_hash,
            "probe_relevant_event_counts": counts,
            "shadow_transition_coverage": shadow_coverage,
            "native_clock_direct": direct, "live_leg_binding": legs,
            "snapshot_alignment": aligned, "exit_target_alignment": exits,
            "rejected_modify_retcodes": dict(sorted(rejected.items())),
            "rejected_modify_attempts": rejected_rows,
            "full_source_time_coverage_verified": False,
            "full_protection_path_parity_verified": False,
            "limitations": ["Bounded probe and selected shadow slice do not establish full journal coverage.",
                            "Snapshots without position_exists compare level fields only.",
                            "Journal snapshot emission is not the precise MT5 read or server installation time.",
                            "A shadow market tick may predate its emitted decision; tick and processing clocks are separate.",
                            "Latest emitted shadow state is not continuous virtual/broker protection parity.",
                            "Broker TP deal reason establishes the exit mechanism, not its precise installation time or the policy decision source."],
            "inputs_sha256": {str(path): sha for path, sha in inputs.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal", required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--shadow-slice", type=Path, required=True)
    parser.add_argument("--shadow-manifest", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--native-deals", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(args.signal, args.probe, args.shadow_slice, args.shadow_manifest,
                   args.money, args.native_deals, args.anchor)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, ensure_ascii=True, allow_nan=False)
        target.write("\n")
    print(json.dumps({"signal": args.signal, "live_leg_binding": report["live_leg_binding"]["status"],
                      "snapshot_statuses": report["snapshot_alignment"]["statuses"],
                      "exit_target_statuses": report["exit_target_alignment"]["statuses"]}))


if __name__ == "__main__":
    main()
