"""Bind initial TP requests and MT5 modification attempts to verified shadow legs.

Offline diagnostic only. An accepted response is not a precise server install
timestamp, and a historical shadow tick is not a decision-emission timestamp.
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audit_native_money_anchor import digest, read
from tools.audit_week_shadow_control_path import shadow_rows, utc_ms


MAX_PAIRS = 20


def summarize_tp_attempts(attempts, *, target, tick_msc, emitted_msc):
    attempts = tuple(attempts)
    target = Decimal(str(target))
    if not attempts:
        raise ValueError("no TP-bearing modification attempts")
    if type(tick_msc) is not int or type(emitted_msc) is not int or emitted_msc < tick_msc:
        raise ValueError("invalid virtual tick/emission chronology")
    parsed = []
    seen = set()
    for row in attempts:
        attempt_id = row.get("attempt_id")
        if (row.get("operation") != "MODIFY_SLTP" or row.get("broker_request_sent") is not True
                or row.get("request_tp") is None or not isinstance(attempt_id, str)
                or not attempt_id or attempt_id in seen):
            raise ValueError("invalid or duplicate TP modification attempt")
        seen.add(attempt_id)
        started = utc_ms(row["broker_request_started_utc"])
        responded = utc_ms(row["broker_response_received_utc"])
        if responded < started:
            raise ValueError("TP response precedes request")
        retcode = row.get("result_retcode")
        if type(retcode) is not int:
            raise ValueError("TP modification retcode missing")
        parsed.append({"attempt_id": attempt_id, "started_msc": started,
                       "responded_msc": responded, "retcode": retcode,
                       "target": Decimal(str(row["request_tp"]))})
    parsed.sort(key=lambda row: (row["started_msc"], row["responded_msc"], row["attempt_id"]))
    accepted = [row for row in parsed if row["retcode"] == 10009]
    accepted_target = [row for row in accepted if row["target"] == target]
    first_target = min(accepted_target, key=lambda row: row["responded_msc"]) if accepted_target else None
    in_flight = [row for row in parsed if row["started_msc"] <= tick_msc < row["responded_msc"]]
    return {"tp_modify_attempt_count": len(parsed),
            "tp_modify_rejected_count": len(parsed) - len(accepted),
            "first_tp_request_minus_virtual_tick_ms": parsed[0]["started_msc"] - tick_msc,
            "request_in_flight_at_virtual_tick": [row["attempt_id"] for row in in_flight],
            "inflight_at_tick_later_rejected_count": sum(row["retcode"] != 10009 for row in in_flight),
            "any_tp_accepted_response_before_virtual_tick": any(
                row["responded_msc"] <= tick_msc for row in accepted),
            "target_accepted_response_before_virtual_tick": any(
                row["responded_msc"] <= tick_msc for row in accepted_target),
            "target_accepted_response_before_shadow_emit": any(
                row["responded_msc"] <= emitted_msc for row in accepted_target),
            "first_target_accepted_response_utc_msc": (
                first_target["responded_msc"] if first_target else None),
            "first_target_accepted_response_minus_virtual_tick_ms": (
                first_target["responded_msc"] - tick_msc if first_target else None),
            "first_target_accepted_response_minus_shadow_emit_ms": (
                first_target["responded_msc"] - emitted_msc if first_target else None),
            "attempts": [{"attempt_id": row["attempt_id"],
                          "request_tp": str(row["target"]), "retcode": row["retcode"],
                          "started_utc_msc": row["started_msc"],
                          "responded_utc_msc": row["responded_msc"]} for row in parsed]}


def _verify_report_sources(report):
    sources = report.get("inputs_sha256")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("protection report has no source hashes")
    for name, expected in sources.items():
        if digest(Path(name)) != expected:
            raise ValueError(f"protection source changed: {name}")


def _bound_leg(probe, report, leg, exit_row, shadow_by_event):
    ticket = leg["native_position_id"]
    if (exit_row["leg_index"] != leg["leg_index"]
            or exit_row["native_position_id"] != ticket
            or exit_row["status"] != "broker_tp_exit_matches_fill_relative_target"):
        raise ValueError("exit target leg identity unavailable")
    events = probe["events"]
    results = [row for row in events if row["ev"] == "mt5_order_result"
               and row.get("order") == ticket]
    if len(results) != 1 or results[0].get("retcode") != 10009:
        raise ValueError("initial order result identity unavailable")
    result = results[0]
    if (result.get("deal") != leg["native_entry_deal"]
            or Decimal(str(result.get("price"))) != Decimal(str(leg["entry_price"]))
            or Decimal(str(result.get("volume"))) != Decimal(str(leg["volume"]))):
        raise ValueError("initial order result contradicts native entry")
    requests = [row for row in events if row["ev"] == "mt5_order_requested"
                and row.get("action_id") == result.get("action_id")
                and row.get("attempt_id") == result.get("attempt_id")]
    if len(requests) != 1 or "tp" not in requests[0]:
        raise ValueError("initial TP request missing")
    close = shadow_by_event.get(exit_row["virtual_close_event_id"])
    if (close is None or close.get("sig") != report["signal_id"]
            or close.get("transition") != "virtual_position_closed"
            or leg["leg_index"] not in close.get("transition_details", {}).get("leg_indexes", [])):
        raise ValueError("shadow close identity missing")
    target = exit_row["observed_pre_exit_tp"]
    attempts = [row for row in events if row["ev"] == "mt5_action_attempt"
                and row.get("operation") == "MODIFY_SLTP"
                and row.get("ticket") == ticket and row.get("request_tp") is not None]
    timeline = summarize_tp_attempts(attempts, target=target,
                                     tick_msc=close["transition_tick_msc"],
                                     emitted_msc=utc_ms(close["ts"]))
    snapshot = next((row for row in report["snapshot_alignment"]["rows"]
                     if row.get("event_id") == exit_row["tp_snapshot_event_id"]), None)
    if (snapshot is None or timeline["first_target_accepted_response_utc_msc"]
            != utc_ms(snapshot["broker_response_received_utc"])):
        raise ValueError("accepted TP response contradicts bound snapshot")
    return {"leg_index": leg["leg_index"], "native_position_id": ticket,
            "initial_order_event_id": requests[0]["event_id"],
            "initial_order_requested_tp": requests[0]["tp"],
            "virtual_close_event_id": close["event_id"],
            "virtual_close_tick_utc_msc": close["transition_tick_msc"],
            "virtual_close_emitted_utc_msc": utc_ms(close["ts"]),
            "target_level": target, **timeline}


def audit(pairs, shadow_slice, shadow_manifest):
    pairs = tuple((Path(report), Path(probe)) for report, probe in pairs)
    shadow_slice, shadow_manifest = Path(shadow_slice), Path(shadow_manifest)
    if not pairs or len(pairs) > MAX_PAIRS:
        raise ValueError("bounded report/probe pairs required")
    shadow = shadow_rows(shadow_slice, shadow_manifest)
    by_event = {row["event_id"]: row for row in shadow
                if row.get("ev") == "strategy_shadow_transition"}
    if len(by_event) != sum(row.get("ev") == "strategy_shadow_transition" for row in shadow):
        raise ValueError("duplicate shadow transition event identity")
    rows = []
    signals = set()
    watched = {str(shadow_slice): digest(shadow_slice),
               str(shadow_manifest): digest(shadow_manifest),
               str(Path(__file__).resolve()): digest(Path(__file__))}
    for report_path, probe_path in pairs:
        report, probe = read(report_path), read(probe_path)
        signal = report.get("signal_id")
        if (signal in signals or report.get("contract") != "shadow_live_protection_level_diagnostic_v1"
                or report.get("status") != "diagnostic_only"
                or report.get("native_clock_direct") is not True
                or report.get("live_leg_binding", {}).get("full_entry_identity_verified") is not True
                or probe.get("window", {}).get("signal_id") != signal):
            raise ValueError("protection report/probe identity mismatch")
        signals.add(signal)
        _verify_report_sources(report)
        for path in (probe_path, shadow_slice, shadow_manifest):
            matches = [sha for name, sha in report["inputs_sha256"].items()
                       if Path(name).resolve() == path.resolve()]
            if len(matches) != 1 or matches[0] != digest(path):
                raise ValueError("bound protection input mismatch")
        watched[str(report_path)] = digest(report_path)
        watched[str(probe_path)] = digest(probe_path)
        legs = report["live_leg_binding"]["rows"]
        exits = {row["leg_index"]: row for row in report["exit_target_alignment"]["rows"]}
        if len(legs) != len(exits) or len(exits) != len(set(exits)):
            raise ValueError("exit leg denominator changed")
        for leg in legs:
            rows.append({"signal_id": signal, **_bound_leg(
                probe, report, leg, exits[leg["leg_index"]], by_event)})
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("TP timeline source changed during audit")
    return {"contract": "tp_request_timeline_diagnostic_v1", "status": "diagnostic_only",
            "full_protection_parity_verified": False, "signal_count": len(signals),
            "leg_count": len(rows),
            "initial_order_tp_null_count": sum(row["initial_order_requested_tp"] is None for row in rows),
            "target_accepted_before_virtual_tick_count": sum(
                row["target_accepted_response_before_virtual_tick"] for row in rows),
            "target_accepted_before_shadow_emit_count": sum(
                row["target_accepted_response_before_shadow_emit"] for row in rows),
            "inflight_at_tick_later_rejected_count": sum(
                row["inflight_at_tick_later_rejected_count"] for row in rows),
            "rows": rows, "inputs_sha256": watched,
            "limitations": ["Requested initial TP and broker response are journal facts, not continuous broker-position snapshots.",
                            "A rejected attempt is not an installed level; a successful response does not timestamp exact server installation.",
                            "Shadow tick time and shadow event emission are different clocks.",
                            "A bounded probe cannot rule out all external position modifications outside its window.",
                            "This diagnostic does not replay policy or prove live/virtual drawdown parity."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", nargs=2, metavar=("REPORT", "PROBE"), action="append", required=True)
    parser.add_argument("--shadow-slice", type=Path, required=True)
    parser.add_argument("--shadow-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.pair, args.shadow_slice, args.shadow_manifest)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"signals": result["signal_count"], "legs": result["leg_count"],
                      "initial_tp_null": result["initial_order_tp_null_count"]}), flush=True)


if __name__ == "__main__":
    main()
