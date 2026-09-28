"""Bind bounded VM order timing to exact native opening deals and shadow ticks."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path


OFFSET_MS = 10_800_000


def utc_ms(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC required")
    delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def bind_signal(comparison, window, native_position, entry_deal):
    signal = comparison["signal_id"]
    if (window["signal_id"] != signal or comparison["first_native_entry_source_msc"] != entry_deal["time_msc"]
            or native_position["position_id"] != entry_deal["position_id"]
            or entry_deal["entry"] != 0 or entry_deal["symbol"] != "XAUUSD"):
        raise ValueError("native entry/window identity mismatch")
    events = window["events"]
    results = [row for row in events if row["ev"] == "mt5_order_result" and row.get("deal") == entry_deal["ticket"]]
    if len(results) != 1:
        raise ValueError("native opening deal lacks one exact VM result")
    result = results[0]
    identity = (result.get("action_id"), result.get("attempt_id"), result.get("decision_id"), result.get("session_id"))
    if any(not isinstance(value, str) or not value for value in identity):
        raise ValueError("missing order lineage identity")
    request = [row for row in events if row["ev"] == "mt5_order_requested"
               and tuple(row.get(key) for key in ("action_id", "attempt_id", "decision_id", "session_id")) == identity]
    attempt = [row for row in events if row["ev"] == "mt5_action_attempt"
               and tuple(row.get(key) for key in ("action_id", "attempt_id", "decision_id", "session_id")) == identity]
    if len(request) != 1 or len(attempt) != 1 or attempt[0].get("operation") != "OPEN_MARKET":
        raise ValueError("native opening result lacks unique request/attempt")
    request, attempt = request[0], attempt[0]
    receipt = [row for row in events if row["ev"] == "signal_received"]
    if len(receipt) != 1 or receipt[0]["session_id"] != result["session_id"]:
        raise ValueError("opening result lacks same-session receipt")
    if (Decimal(str(result.get("price"))) != Decimal(str(entry_deal["price"]))
            or Decimal(str(result.get("volume"))) != Decimal(str(entry_deal["volume"]))
            or Decimal(str(attempt.get("filled_price"))) != Decimal(str(entry_deal["price"]))
            or result.get("retcode") != 10009 or attempt.get("broker_request_sent") is not True):
        raise ValueError("VM result disagrees with native opening deal")
    sent = utc_ms(attempt["broker_request_started_utc"])
    response = utc_ms(attempt["broker_response_received_utc"])
    requested = utc_ms(request["ts"])
    result_at = utc_ms(result["ts"])
    native_at = entry_deal["time_msc"] - OFFSET_MS
    tick_at = int(comparison["first_virtual_fill_utc_msc"])
    duration_ns = attempt.get("broker_roundtrip_ns")
    if (type(duration_ns) is not int or duration_ns < 0
            or not requested <= sent <= response <= result_at):
        raise ValueError("opening call chronology inconsistent")
    if (attempt.get("pre_broker_duration_ns") is None or attempt.get("post_broker_duration_ns") is None):
        raise ValueError("missing pre/post MT5 call timing")
    roundtrip_ms = Decimal(duration_ns) / 1_000_000
    native_minus_tick = Decimal(native_at - tick_at)
    native_inside_call = sent <= native_at <= response
    return {"signal_id": signal, "channel": comparison["channel"],
            "status": ("exact_opening_deal_and_call_bound" if native_inside_call
                       else "bound_with_native_call_clock_order_anomaly"),
            "native_within_call_interval": native_inside_call,
            "session_id": result["session_id"],
            "code_commit": result.get("code_commit"), "decision_id": result["decision_id"],
            "action_id": result["action_id"], "attempt_id": result["attempt_id"],
            "request_event_id": request["event_id"], "attempt_event_id": attempt["event_id"],
            "result_event_id": result["event_id"], "native_entry_deal_ticket": entry_deal["ticket"],
            "native_entry_source_msc": entry_deal["time_msc"],
            "native_entry_price": entry_deal["price"], "native_entry_volume": entry_deal["volume"],
            "virtual_first_tick_utc_msc": tick_at,
            "virtual_first_price": comparison["first_virtual_fill_price"],
            "request_price": request.get("requested_price"),
            "request_utc": request["ts"], "call_started_utc": attempt["broker_request_started_utc"],
            "native_entry_utc_msc": native_at, "call_response_utc": attempt["broker_response_received_utc"],
            "result_utc": result["ts"],
            "request_minus_virtual_tick_ms": requested - tick_at,
            "call_start_minus_request_ms": sent - requested,
            "native_entry_minus_virtual_tick_ms": native_at - tick_at,
            "mt5_order_send_roundtrip_ms": str(roundtrip_ms),
            "wall_minus_monotonic_call_duration_ms": str(Decimal(response - sent) - roundtrip_ms),
            "native_minus_call_roundtrip_residual_ms": str(native_minus_tick - roundtrip_ms),
            "native_entry_minus_call_start_ms": native_at - sent,
            "call_response_minus_native_entry_ms": response - native_at,
            "pre_call_ms": str(Decimal(attempt["pre_broker_duration_ns"]) / 1_000_000),
            "post_call_ms": str(Decimal(attempt["post_broker_duration_ns"]) / 1_000_000),
            "request_price_equals_virtual_first_price": Decimal(str(request.get("requested_price")))
            == Decimal(str(comparison["first_virtual_fill_price"])),
            "source_window_sha256": window["source_window_sha256"]}


def audit(comparison_path, timing_path, baskets_path, deals_path):
    paths = list(map(Path, (comparison_path, timing_path, baskets_path, deals_path)))
    comparison, timing, baskets, native = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if (comparison.get("contract") != "week_shadow_live_control_path_diagnostic_v1"
            or timing.get("contract") != "bounded_vm_entry_timing_diagnostic_v1"
            or timing["local_inputs_sha256"].get(str(paths[0])) != digest(paths[0])
            or baskets["source_sha256"] != digest(paths[3])
            or len(comparison["comparisons"]) != len(timing["windows"])):
        raise ValueError("crosswalk source identity mismatch")
    by_window = {row["signal_id"]: row for row in timing["windows"]}
    if len(by_window) != len(timing["windows"]):
        raise ValueError("duplicate VM timing window")
    position_rows = baskets["positions"]
    deals_by_ticket = {row["ticket"]: row for row in native["deals"]}
    if len(deals_by_ticket) != len(native["deals"]):
        raise ValueError("duplicate native deal ticket")
    reports = []
    for row in comparison["comparisons"]:
        signal = row["signal_id"]
        if signal not in by_window or row.get("clock_direct_for_all_native_event_days") is not True:
            reports.append({"signal_id": signal, "status": "blocked", "reason": "missing_window_or_direct_clock"})
            continue
        positions = [p for p in position_rows if p["signal_id"] == signal]
        if not positions:
            reports.append({"signal_id": signal, "status": "blocked", "reason": "missing_native_position"})
            continue
        first = min(positions, key=lambda p: p["first_native_msc"])
        deal = [deals_by_ticket[ticket] for ticket in first["deal_tickets"] if deals_by_ticket[ticket]["entry"] == 0]
        try:
            if len(deal) != 1:
                raise ValueError("native first position lacks one entry deal")
            reports.append(bind_signal(row, by_window[signal], first, deal[0]))
        except (KeyError, TypeError, ValueError) as exc:
            reports.append({"signal_id": signal, "status": "blocked", "reason": str(exc)})
    matched = [row for row in reports if row["status"] in
               {"exact_opening_deal_and_call_bound", "bound_with_native_call_clock_order_anomaly"}]
    watched = {str(path): digest(path) for path in (*paths, Path(__file__))}
    return {"contract": "native_vs_shadow_first_entry_mt5_call_v1", "status": "diagnostic_only",
            "signal_count": len(reports), "statuses": dict(Counter(row["status"] for row in reports)),
            "matched_count": len(matched),
            "strict_native_inside_call_count": sum(row["native_within_call_interval"] for row in matched),
            "call_over_5s_count": sum(Decimal(row["mt5_order_send_roundtrip_ms"]) > 5000 for row in matched),
            "rows": reports, "inputs_sha256": watched,
            "simulator_path_parity_verified": False, "mt5_call_root_cause_isolated": False,
            "limitations": ["The measured call is mt5.order_send and includes terminal IPC plus broker communication; its internal cause is not isolated.",
                            "This binds only first opening deals in bounded windows, not later legs or complete management paths.",
                            "Price matching and time correlation do not prove an alternative virtual fill would execute identically."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--timing", type=Path, required=True)
    parser.add_argument("--baskets", type=Path, required=True)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.comparison, args.timing, args.baskets, args.deals)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, ensure_ascii=True)
        output.write("\n")
    print(json.dumps({"status": result["status"], "matched_count": result["matched_count"],
                      "call_over_5s_count": result["call_over_5s_count"]}))


if __name__ == "__main__":
    main()
