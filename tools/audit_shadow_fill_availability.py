"""Audit virtual first fills against live request, native fill and journal time.

Virtual tick time and processing time are distinct clocks. This diagnostic
does not assign the VM's observed delay to an independent hypothetical run.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audit_native_money_anchor import digest, read
from tools.audit_week_shadow_control_path import utc_ms


def compare_first_fill(shadow, native, *, offset_seconds):
    if (shadow["signal_id"] != native["signal_id"]
            or shadow["channel"] != native["channel"]
            or shadow.get("clock_direct_for_all_native_event_days") is not True
            or shadow["first_native_entry_source_msc"] != native["native_entry_source_msc"]
            or shadow["first_virtual_fill_utc_msc"] != native["virtual_first_tick_utc_msc"]
            or str(shadow["first_native_entry_price"]) != str(native["native_entry_price"])):
        raise ValueError("first-fill source identity or direct clock mismatch")
    tick_at = shadow["first_virtual_fill_utc_msc"]
    recorded_at = utc_ms(shadow["first_virtual_fill_journal_utc"])
    native_at = native["native_entry_utc_msc"]
    request_at = utc_ms(native["request_utc"])
    if (native_at != shadow["first_native_entry_source_msc"] - offset_seconds * 1000
            or recorded_at < tick_at
            or tick_at + shadow["first_native_minus_virtual_ms"] != native_at
            or recorded_at - tick_at != shadow["first_virtual_fill_emit_minus_tick_ms"]
            or native_at - recorded_at != shadow["first_native_minus_shadow_emit_ms"]
            or native_at - tick_at != native["native_entry_minus_virtual_tick_ms"]
            or request_at - tick_at != native["request_minus_virtual_tick_ms"]):
        raise ValueError("first-fill timestamp arithmetic inconsistent")
    if native_at < request_at and native["status"] != "bound_with_native_call_clock_order_anomaly":
        raise ValueError("native fill precedes request without declared clock anomaly")
    return {"signal_id": shadow["signal_id"], "channel": shadow["channel"],
            "native_call_status": native["status"],
            "first_virtual_tick_utc_msc": tick_at,
            "native_request_utc": native["request_utc"],
            "native_fill_utc_msc": native_at,
            "shadow_recorded_utc": shadow["first_virtual_fill_journal_utc"],
            "native_request_after_virtual_tick_ms": request_at - tick_at,
            "native_fill_after_virtual_tick_ms": native_at - tick_at,
            "shadow_record_after_virtual_tick_ms": recorded_at - tick_at,
            "shadow_record_after_native_fill_ms": recorded_at - native_at,
            "first_entry_binding": shadow.get("entry_pairing", {}).get("status"),
            "availability": ("recorded_after_native_fill" if recorded_at > native_at else
                             "recorded_at_native_fill" if recorded_at == native_at else
                             "recorded_before_native_fill")}


def audit(shadow_path, native_call_path):
    shadow_path, native_call_path = Path(shadow_path), Path(native_call_path)
    shadow, native = read(shadow_path), read(native_call_path)
    if (shadow.get("contract") != "week_shadow_live_control_path_diagnostic_v1"
            or native.get("contract") != "native_vs_shadow_first_entry_mt5_call_v1"
            or native.get("signal_count") != len(native.get("rows", []))
            or native.get("matched_count") != len(native["rows"])):
        raise ValueError("unexpected source report contract or denominator")
    offset = shadow.get("source_clock_offset_seconds_hypothesis")
    if type(offset) is not int:
        raise ValueError("missing direct clock offset")
    by_signal = {row["signal_id"]: row for row in native["rows"]}
    if len(by_signal) != len(native["rows"]):
        raise ValueError("duplicate native first-fill signal")
    compared = {row["signal_id"]: row for row in shadow["comparisons"]}
    if len(compared) != len(shadow["comparisons"]) or set(compared) != set(by_signal):
        raise ValueError("first-fill comparison coverage mismatch")
    rows = [compare_first_fill(compared[signal], by_signal[signal], offset_seconds=offset)
            for signal in sorted(compared)]
    native_coverage = Counter(row["signal_id"].split("_")[0] for row in shadow["coverage"]
                              if row["native_basket_in_extract"] is True)
    compared_coverage = Counter(row["channel"] for row in rows)
    return {"contract": "shadow_first_fill_dual_clock_diagnostic_v1",
            "status": "diagnostic_only", "source_clock_offset_seconds": offset,
            "native_basket_denominator": dict(native_coverage),
            "compared_first_fill_count": dict(compared_coverage),
            "availability_counts": dict(sorted(Counter(row["availability"] for row in rows).items())),
            "rows": rows,
            "inputs_sha256": {str(path): digest(path) for path in
                              (shadow_path, native_call_path, Path(__file__))},
            "limitations": [
                "Journal timestamp is when the shadow event was queued, not the disk write time.",
                "A later record does not mean the earlier market tick was absent; it means the virtual fill was reconstructed after the native fill.",
                "Independent simulation may use historical ticks but needs a declared execution model; this is not live path parity.",
                "Only first entries with both retained reports are checked; later entries, exits, equity and drawdown are not certified."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow", type=Path, required=True)
    parser.add_argument("--native-calls", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.shadow, args.native_calls)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"compared": len(result["rows"]),
                      "availability_counts": result["availability_counts"]}))


if __name__ == "__main__":
    main()
