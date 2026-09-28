"""Reconcile every weekly first native fill with bounded live MT5 call traces."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_vm_entry_roundtrip import OFFSET_MS, digest, utc_ms


def _receipt_rows(slice_path, manifest_path, signal_ids):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("status") != "complete" or manifest.get("output_sha256") != digest(slice_path):
        raise ValueError("weekly receipt source digest mismatch")
    receipts = {}
    with gzip.open(slice_path, "rt", encoding="utf-8") as source:
        for line in source:
            if '"signal_received"' not in line:
                continue
            row = json.loads(line)
            if row.get("ev") != "signal_received" or row.get("sig") not in signal_ids:
                continue
            if not "2026-09-14" <= row["ts"][:10] <= "2026-09-18":
                continue
            if row["sig"] in receipts:
                raise ValueError(f"ambiguous weekly receipt: {row['sig']}")
            receipts[row["sig"]] = row
    if set(receipts) != signal_ids:
        raise ValueError("weekly native receipts incomplete")
    return receipts


def bind_opening(signal, basket, position, deal, window, receipt, clock_status):
    if (basket["signal_id"] != signal or position["signal_id"] != signal
            or position["position_id"] != deal["position_id"]
            or deal["entry"] != 0 or deal["symbol"] != "XAUUSD"
            or position["first_native_msc"] != deal["time_msc"]
            or window["signal_id"] != signal or receipt["sig"] != signal):
        raise ValueError("opening source identity mismatch")
    results = [row for row in window["events"]
               if row["ev"] == "mt5_order_result" and row.get("deal") == deal["ticket"]]
    if len(results) != 1:
        raise ValueError("one exact result for opening deal not found")
    result = results[0]
    lineage_keys = ("action_id", "attempt_id", "decision_id", "session_id")
    lineage = tuple(result.get(key) for key in lineage_keys)
    if any(not isinstance(value, str) or not value for value in lineage):
        raise ValueError("opening result lineage missing")
    def owned(event_name):
        return [row for row in window["events"] if row["ev"] == event_name
                and tuple(row.get(key) for key in lineage_keys) == lineage]
    requests, attempts = owned("mt5_order_requested"), owned("mt5_action_attempt")
    if len(requests) != 1 or len(attempts) != 1 or attempts[0].get("operation") != "OPEN_MARKET":
        raise ValueError("opening request/attempt not uniquely bound")
    request, attempt = requests[0], attempts[0]
    if receipt["session_id"] != result["session_id"]:
        raise ValueError("opening result is not in receipt session")
    if (result.get("retcode") != 10009 or attempt.get("broker_request_sent") is not True
            or Decimal(str(result.get("price"))) != Decimal(str(deal["price"]))
            or Decimal(str(result.get("volume"))) != Decimal(str(deal["volume"]))
            or Decimal(str(attempt.get("filled_price"))) != Decimal(str(deal["price"]))):
        raise ValueError("opening result price, volume or retcode conflicts with native deal")
    receipt_at = utc_ms(receipt["ts"])
    request_at = utc_ms(request["ts"])
    call_at = utc_ms(attempt["broker_request_started_utc"])
    response_at = utc_ms(attempt["broker_response_received_utc"])
    result_at = utc_ms(result["ts"])
    native_at = deal["time_msc"] - OFFSET_MS
    roundtrip_ns = attempt.get("broker_roundtrip_ns")
    if (type(roundtrip_ns) is not int or roundtrip_ns < 0
            or not receipt_at <= request_at <= call_at <= response_at <= result_at):
        raise ValueError("opening call chronology inconsistent")
    if (type(attempt.get("pre_broker_duration_ns")) is not int
            or type(attempt.get("post_broker_duration_ns")) is not int):
        raise ValueError("opening pre/post timing missing")
    direct = clock_status == "direct_anchor_available"
    if clock_status not in {"direct_anchor_available", "no_direct_anchor"}:
        raise ValueError("unknown independent clock status")
    native_inside = call_at <= native_at <= response_at
    status = ("bound_direct_clock_order" if direct and native_inside
              else "bound_direct_clock_order_anomaly" if direct
              else "bound_broker_clock_hypothesis")
    return {"signal_id": signal, "channel": basket["channel"], "status": status,
            "direct_clock_anchor": direct, "native_inside_call_interval": native_inside,
            "receipt_event_id": receipt["event_id"], "request_event_id": request["event_id"],
            "attempt_event_id": attempt["event_id"], "result_event_id": result["event_id"],
            "session_id": result["session_id"], "code_commit": result.get("code_commit"),
            "decision_id": result["decision_id"], "action_id": result["action_id"],
            "attempt_id": result["attempt_id"], "native_entry_deal": deal["ticket"],
            "native_position_id": position["position_id"], "native_entry_source_msc": deal["time_msc"],
            "native_entry_price": deal["price"], "native_entry_volume": deal["volume"],
            "receipt_utc": receipt["ts"], "request_utc": request["ts"],
            "call_started_utc": attempt["broker_request_started_utc"],
            "call_response_utc": attempt["broker_response_received_utc"], "result_utc": result["ts"],
            "receipt_to_request_ms": request_at - receipt_at,
            "request_to_call_ms": call_at - request_at,
            "mt5_order_send_roundtrip_ms": str(Decimal(roundtrip_ns) / 1_000_000),
            "pre_call_ms": str(Decimal(attempt["pre_broker_duration_ns"]) / 1_000_000),
            "post_call_ms": str(Decimal(attempt["post_broker_duration_ns"]) / 1_000_000),
            "wall_minus_monotonic_call_ms": str(Decimal(response_at - call_at)
                                                 - Decimal(roundtrip_ns) / 1_000_000),
            "native_entry_minus_call_start_ms_hypothesis": native_at - call_at,
            "call_response_minus_native_entry_ms_hypothesis": response_at - native_at,
            "request_price": request.get("requested_price"),
            "native_price_minus_request_price": str(Decimal(str(deal["price"]))
                                                    - Decimal(str(request.get("requested_price")))),
            "source_window_sha256": window["source_window_sha256"]}


def audit(batches, baskets_path, deals_path, shadow_path, manifest_path, anchor_path):
    batches = [Path(path) for path in batches]
    baskets_path, deals_path, shadow_path, manifest_path, anchor_path = map(
        Path, (baskets_path, deals_path, shadow_path, manifest_path, anchor_path))
    baskets, native, anchor = [json.loads(path.read_text(encoding="utf-8"))
                               for path in (baskets_path, deals_path, anchor_path)]
    if (baskets["source_sha256"] != digest(deals_path)
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"):
        raise ValueError("native source identity mismatch")
    native_by_signal = {row["signal_id"]: row for row in baskets["baskets"]}
    if len(native_by_signal) != len(baskets["baskets"]):
        raise ValueError("duplicate native basket")
    receipts = _receipt_rows(shadow_path, manifest_path, set(native_by_signal))
    windows = {}
    total_scanned = 0
    for path in batches:
        batch = json.loads(path.read_text(encoding="utf-8"))
        if (batch.get("contract") != "bounded_vm_entry_timing_diagnostic_v1"
                or batch.get("window_mode") != "all_native_first_entries"
                or batch["local_inputs_sha256"].get(str(baskets_path)) != digest(baskets_path)
                or batch["local_inputs_sha256"].get(str(shadow_path)) != digest(shadow_path)):
            raise ValueError("VM timing batch source mismatch")
        total_scanned += batch["total_scanned_bytes"]
        for window in batch["windows"]:
            if window["signal_id"] in windows:
                raise ValueError("duplicate VM timing signal window")
            windows[window["signal_id"]] = window
    if set(windows) != set(native_by_signal):
        raise ValueError("VM timing windows do not cover native denominator")
    deal_by_ticket = {row["ticket"]: row for row in native["deals"]}
    if len(deal_by_ticket) != len(native["deals"]):
        raise ValueError("duplicate native deal ticket")
    reports = []
    for signal in sorted(native_by_signal):
        basket = native_by_signal[signal]
        positions = [p for p in baskets["positions"] if p["signal_id"] == signal]
        if not positions:
            reports.append({"signal_id": signal, "status": "blocked", "reason": "native_position_missing"})
            continue
        first = min(positions, key=lambda p: p["first_native_msc"])
        entries = [deal_by_ticket[ticket] for ticket in first["deal_tickets"]
                   if deal_by_ticket[ticket]["entry"] == 0]
        try:
            if len(entries) != 1:
                raise ValueError("first native position lacks one opening deal")
            day = datetime.fromtimestamp(entries[0]["time_msc"] / 1000, timezone.utc).date().isoformat()
            clock_status = anchor["independent_clock_evidence"]["days"][day]["status"]
            reports.append(bind_opening(signal, basket, first, entries[0], windows[signal],
                                        receipts[signal], clock_status))
        except (KeyError, TypeError, ValueError) as exc:
            reports.append({"signal_id": signal, "status": "blocked", "reason": str(exc)})
    bound = [row for row in reports if row["status"].startswith("bound_")]
    calls = sorted(Decimal(row["mt5_order_send_roundtrip_ms"]) for row in bound)
    median = (None if not calls else calls[len(calls) // 2] if len(calls) % 2
              else (calls[len(calls) // 2 - 1] + calls[len(calls) // 2]) / 2)
    paths = [*batches, baskets_path, deals_path, shadow_path, manifest_path, anchor_path, Path(__file__)]
    return {"contract": "native_week_first_entry_mt5_call_v1", "status": "diagnostic_only",
            "native_basket_count": len(native_by_signal), "bound_count": len(bound),
            "statuses": dict(Counter(row["status"] for row in reports)),
            "mt5_call_over_1s": sum(value > 1_000 for value in calls),
            "mt5_call_over_5s": sum(value > 5_000 for value in calls),
            "mt5_call_over_10s": sum(value > 10_000 for value in calls),
            "mt5_call_over_60s": sum(value > 60_000 for value in calls),
            "mt5_call_median_ms": str(median) if median is not None else None,
            "mt5_call_max_ms": str(calls[-1]) if calls else None,
            "total_vm_scanned_bytes": total_scanned, "rows": reports,
            "inputs_sha256": {str(path): digest(path) for path in paths},
            "simulator_path_parity_verified": False,
            "limitations": ["First opening deal only, not subsequent entries, management or risk paths.",
                            "Bounded time windows are diagnostic; a missing event cannot prove absence outside the window.",
                            "MT5 order_send duration includes Python bridge, terminal IPC and broker communication; these are not separated.",
                            "Broker epoch UTC offset has no direct anchor on 14 and 16 September, so native wall-time deltas there are hypotheses."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, action="append", required=True)
    parser.add_argument("--baskets", type=Path, required=True)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--shadow-slice", type=Path, required=True)
    parser.add_argument("--shadow-manifest", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(args.batch, args.baskets, args.deals, args.shadow_slice, args.shadow_manifest, args.anchor)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=True)
        output.write("\n")
    print(json.dumps({key: report[key] for key in
                      ("native_basket_count", "bound_count", "statuses", "mt5_call_over_5s")}))


if __name__ == "__main__":
    main()
