"""Read bounded VM journal windows for native/shadow entry timing, without VM writes."""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


EVENTS = {
    "signal_received", "gold_555_entry_watch_confirmed", "gold_555_preopen_account_verified",
    "gold_555_first_leg_filled", "mt5_order_requested", "mt5_order_result",
    "mt5_action_attempt", "market_filled", "range_arrived", "strategy_snapshot",
}
FIELDS = {
    "sig", "ev", "ts", "event_id", "session_id", "code_commit", "decision_id",
    "message_revision_id", "action_id", "attempt_id", "operation", "order_kind",
    "attempt_started_utc", "broker_request_started_utc", "broker_response_received_utc",
    "attempt_finished_utc", "duration_ns", "pre_broker_duration_ns", "broker_roundtrip_ns",
    "post_broker_duration_ns", "broker_request_sent", "preflight_status", "preflight_reason",
    "source_tick_lookup_state", "validation_tick_lookup_state", "requested_price", "filled_price",
    "price", "volume", "lot", "retcode", "deal", "order", "ticket", "request_id",
    "confirmed_at", "confirmed_tick_time_msc", "confirmed_quote", "strategy_id",
    "strategy_fingerprint", "live_strategy_id", "live_strategy_fingerprint", "execution_state",
}
MAX_WINDOWS = 20
MAX_WINDOW_BYTES = 64_000_000
MAX_TOTAL_BYTES = 256_000_000
MAX_SECONDS = 120


def _utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC required")
    return parsed


def build_windows(comparison, shadow_rows, *, offset_seconds=10_800):
    ids = {row["signal_id"] for row in comparison["comparisons"]}
    receipts = {}
    for row in shadow_rows:
        if row.get("ev") == "signal_received" and row.get("sig") in ids:
            at = _utc(row["ts"])
            if datetime(2026, 9, 14, tzinfo=timezone.utc) <= at < datetime(2026, 9, 19, tzinfo=timezone.utc):
                if row["sig"] in receipts:
                    raise ValueError(f"ambiguous weekly receipt: {row['sig']}")
                receipts[row["sig"]] = at
    if len(receipts) != len(ids) or len(ids) > MAX_WINDOWS:
        raise ValueError("comparison receipts/window budget mismatch")
    windows = []
    for row in comparison["comparisons"]:
        signal = row["signal_id"]
        native_at = datetime.fromtimestamp(
            (row["first_native_entry_source_msc"] - offset_seconds * 1000) / 1000, timezone.utc)
        receipt_at = receipts[signal]
        start = min(native_at, receipt_at) - timedelta(seconds=15)
        end = max(native_at, receipt_at) + timedelta(seconds=30)
        if end - start > timedelta(minutes=15) or start.date() != end.date():
            raise ValueError("entry timing window too large or crosses UTC day")
        windows.append({"signal_id": signal, "start_utc": start.isoformat(timespec="milliseconds"),
                        "end_utc": end.isoformat(timespec="milliseconds"),
                        "receipt_utc": receipt_at.isoformat(timespec="milliseconds"),
                        "native_first_entry_utc": native_at.isoformat(timespec="milliseconds")})
    return windows


def build_native_week_windows(baskets, shadow_rows, *, offset_seconds=10_800):
    native = {row["signal_id"]: row for row in baskets["baskets"]}
    if len(native) != len(baskets["baskets"]):
        raise ValueError("duplicate native basket identity")
    receipts = {}
    for row in shadow_rows:
        if row.get("ev") == "signal_received" and row.get("sig") in native:
            at = _utc(row["ts"])
            if datetime(2026, 9, 14, tzinfo=timezone.utc) <= at < datetime(2026, 9, 19, tzinfo=timezone.utc):
                if row["sig"] in receipts:
                    raise ValueError(f"ambiguous weekly receipt: {row['sig']}")
                receipts[row["sig"]] = at
    if len(receipts) != len(native):
        raise ValueError("native week receipt denominator mismatch")
    windows = []
    for signal in sorted(native):
        first_source = min(p["first_native_msc"] for p in baskets["positions"] if p["signal_id"] == signal)
        native_at = datetime.fromtimestamp((first_source - offset_seconds * 1000) / 1000, timezone.utc)
        receipt_at = receipts[signal]
        if native_at < receipt_at - timedelta(seconds=1):
            raise ValueError("native fill precedes weekly receipt beyond clock uncertainty")
        start = max(receipt_at - timedelta(seconds=15), native_at - timedelta(minutes=3))
        end = native_at + timedelta(seconds=30)
        windows.append({"signal_id": signal, "start_utc": start.isoformat(timespec="milliseconds"),
                        "end_utc": end.isoformat(timespec="milliseconds"),
                        "receipt_utc": receipt_at.isoformat(timespec="milliseconds"),
                        "native_first_entry_utc": native_at.isoformat(timespec="milliseconds"),
                        "receipt_inside_window": start <= receipt_at < end})
    return windows


def _line_at(source, position):
    source.seek(position)
    if position:
        source.seek(position - 1)
        if source.read(1) != b"\n":
            source.readline()
    line_start = source.tell()
    line = source.readline()
    if not line:
        raise ValueError("journal source ended during index probe")
    row = json.loads(line)
    stamp = _utc(row["ts"])
    return line_start, source.tell(), stamp


def _bound(source, target, lower, upper):
    lo, hi = lower, upper
    for _ in range(48):
        if hi - lo < 4096:
            break
        position, next_position, stamp = _line_at(source, (lo + hi) // 2)
        if stamp < target:
            lo = next_position
        else:
            hi = position
    source.seek(max(lower, lo - 65_536))
    if source.tell():
        source.seek(source.tell() - 1)
        if source.read(1) != b"\n":
            source.readline()
    while source.tell() < min(upper, hi + 65_536):
        line_start = source.tell()
        raw = source.readline()
        if not raw:
            break
        if _utc(json.loads(raw)["ts"]) >= target:
            return line_start
    return upper


def _selected_event(row):
    if row.get("ev") not in EVENTS:
        return None
    if row["ev"] == "mt5_action_attempt" and row.get("operation") != "OPEN_MARKET":
        return None
    return {key: row[key] for key in sorted(FIELDS & row.keys())}


def extract(spec):
    windows = spec["windows"]
    lower, upper = int(spec["scope_start_offset"]), int(spec["scope_end_offset"])
    pad = int(spec.get("pad_bytes", 2_000_000))
    if (not 0 < len(windows) <= MAX_WINDOWS or lower < 0 or upper <= lower
            or not 0 <= pad <= 2_000_000):
        raise ValueError("invalid bounded journal scope")
    started = time.monotonic()
    reports = []
    total_bytes = 0
    with Path(spec["source"]).open("rb") as source:
        file_size = source.seek(0, 2)
        if file_size < upper:
            raise ValueError("journal truncated since prior extract")
        for window in windows:
            if time.monotonic() - started > MAX_SECONDS:
                raise TimeoutError("journal extraction wall budget exceeded")
            signal = window["signal_id"]
            if not isinstance(signal, str) or not signal.startswith(("canal1_", "canal2_")):
                raise ValueError("invalid signal identity")
            start_at, end_at = _utc(window["start_utc"]), _utc(window["end_utc"])
            if start_at >= end_at or end_at - start_at > timedelta(minutes=15):
                raise ValueError("invalid bounded time window")
            first = max(lower, _bound(source, start_at, lower, upper) - pad)
            last = min(upper, _bound(source, end_at, lower, upper) + pad)
            if last - first > MAX_WINDOW_BYTES or total_bytes + last - first > MAX_TOTAL_BYTES:
                raise ValueError("journal read budget exceeded before scan")
            source.seek(first)
            if first:
                source.readline()
            actual_start = source.tell()
            digest = hashlib.sha256()
            count = 0
            events = []
            names = Counter()
            needle = signal.encode("ascii")
            while source.tell() < last:
                raw = source.readline()
                if not raw:
                    raise ValueError("journal ended within indexed window")
                digest.update(raw)
                count += len(raw)
                if count > MAX_WINDOW_BYTES or total_bytes + count > MAX_TOTAL_BYTES:
                    raise ValueError("journal read budget exceeded during scan")
                if needle not in raw:
                    continue
                row = json.loads(raw)
                if row.get("sig") != signal:
                    continue
                stamp = _utc(row["ts"])
                if not start_at <= stamp < end_at:
                    continue
                selected = _selected_event(row)
                if selected is not None:
                    events.append(selected)
                    names[row["ev"]] += 1
                    if len(events) > 100:
                        raise ValueError("selected event budget exceeded")
            total_bytes += count
            reports.append({**window, "scanned_offset_start": actual_start,
                            "scanned_offset_end": source.tell(), "scanned_bytes": count,
                            "source_window_sha256": digest.hexdigest(), "event_counts": dict(names),
                            "events": events})
            time.sleep(0.01)
        if Path(spec["source"]).stat().st_size < file_size:
            raise ValueError("journal truncated during extraction")
    return {"contract": "bounded_vm_entry_timing_diagnostic_v1", "status": "diagnostic_only",
            "source": spec["source"], "source_file_size_at_start": file_size,
            "scope_start_offset": lower, "scope_end_offset": upper,
            "total_scanned_bytes": total_bytes, "windows": reports,
            "limitations": ["Binary time indexing assumes approximately append-ordered UTC events; padded windows are not a full-journal proof of absence.",
                            "Broker roundtrip spans the MT5 call, terminal and network; it does not isolate broker-server latency.",
                            "Only first-entry windows and selected event types are included; management and complete paths remain outside scope."]}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        spec = json.loads(base64.b64decode(sys.argv[2]))
        print(json.dumps(extract(spec), sort_keys=True), flush=True)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--comparison", type=Path)
    source_group.add_argument("--baskets", type=Path)
    parser.add_argument("--shadow-slice", type=Path, required=True)
    parser.add_argument("--remote-source", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope-start-offset", type=int, required=True)
    parser.add_argument("--scope-end-offset", type=int, required=True)
    parser.add_argument("--batch-index", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=10)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    with gzip.open(args.shadow_slice, "rt", encoding="utf-8") as source:
        shadow_rows = [json.loads(line) for line in source if b'"signal_received"' in line.encode("utf-8")]
    input_path = args.comparison or args.baskets
    input_data = json.loads(input_path.read_text(encoding="utf-8"))
    if args.comparison is not None:
        if input_data.get("contract") != "week_shadow_live_control_path_diagnostic_v1":
            raise ValueError("unexpected comparison contract")
        windows = build_windows(input_data, shadow_rows)
        mode = "shadow_control_overlap"
    else:
        if not isinstance(input_data.get("baskets"), list) or not isinstance(input_data.get("positions"), list):
            raise ValueError("invalid native basket source")
        windows = build_native_week_windows(input_data, shadow_rows)
        mode = "all_native_first_entries"
    if args.batch_index < 0 or not 0 < args.batch_size <= MAX_WINDOWS:
        raise ValueError("invalid extraction batch")
    start_index = args.batch_index * args.batch_size
    windows = windows[start_index:start_index + args.batch_size]
    if not windows:
        raise ValueError("empty extraction batch")
    spec = {"source": args.remote_source, "scope_start_offset": args.scope_start_offset,
            "scope_end_offset": args.scope_end_offset, "windows": windows}
    encoded = base64.b64encode(json.dumps(spec, separators=(",", ":")).encode("ascii")).decode("ascii")
    command = ["ssh", "-i", str(args.identity), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=5", args.host, f"python - --worker {encoded}"]
    run = subprocess.run(command, input=Path(__file__).read_bytes(), capture_output=True,
                         timeout=MAX_SECONDS + 15, check=False)
    if run.returncode or run.stderr:
        raise RuntimeError(f"bounded VM extraction failed (exit={run.returncode}); no report written")
    report = json.loads(run.stdout)
    if (report.get("status") != "diagnostic_only" or len(report.get("windows", [])) != len(windows)
            or {row["signal_id"] for row in report["windows"]} != {row["signal_id"] for row in windows}):
        raise ValueError("remote report/window identity mismatch")
    report["local_inputs_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in (input_path, args.shadow_slice, Path(__file__))}
    report["window_mode"] = mode
    report["batch_index"] = args.batch_index
    report["batch_size"] = args.batch_size
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=True)
        output.write("\n")
    print(json.dumps({"status": report["status"], "windows": len(report["windows"]),
                      "total_scanned_bytes": report["total_scanned_bytes"]}))


if __name__ == "__main__":
    main()
