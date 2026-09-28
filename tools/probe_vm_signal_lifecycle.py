"""Read one bounded live signal lifecycle from the VM journal, without VM writes."""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time


OFFSET_MS = 10_800_000
MAX_WINDOW_BYTES = 64_000_000
MAX_EVENTS = 5_000
MAX_PER_KIND = 100
MAX_ACTION_ATTEMPTS = 1_000
MAX_MATERIAL_EVENTS = 10_000
MAX_MANAGEMENT_EVENTS = 25_000
MAX_MANAGEMENT_OUTPUT_BYTES = 32_000_000
MAX_SECONDS = 120
MANAGEMENT_KINDS = {"gold_555_trailing", "gold_555_leg_protection",
                    "gold_555_basket_guard", "dubai_basket_guard"}
MANAGEMENT_STATE_FIELDS = {"status", "requested_close_reason", "all_filled_tickets",
                           "pending_tickets", "candidate_hard_stops",
                           "candidate_entry_prices_by_ticket", "basket_guard_armed",
                           "basket_guard_triggered", "basket_guard_peak_pl",
                           "basket_guard_trigger_reason", "basket_guard_recovery_pending",
                           "basket_guard_close_tickets", "candidate_first_fill_at", "timestamp"}
MANAGEMENT_SUMMARY_FIELDS = {"pl", "n_open", "avg_entry", "current_price", "lots_total",
                             "open_tickets", "positions_complete", "floating_pl", "realized_pl",
                             "realized_complete", "missing_realized_tickets", "total_pl",
                             "source_tick_time_msc", "unresolved_entry_indexes",
                             "positions_read_started_utc", "positions_read_completed_utc",
                             "positions_read_elapsed_ms"}
MANAGEMENT_INPUT_FIELDS = {
    "gold_555_trailing": {"bid", "ask", "tick_time_msc", "open_tickets"},
    "gold_555_leg_protection": {"ticket", "fill_price", "leg_index"},
    "gold_555_basket_guard": {"summary", "now_utc"},
    "dubai_basket_guard": {"summary", "now_utc"},
}
MANAGEMENT_OPTIONAL_INPUT_FIELDS = {"gold_555_leg_protection": {"current_sl"}}
MANAGEMENT_GUARD_STATE_FIELDS = {"armed", "triggered", "peak_pl", "trigger_reason", "recovery_pending"}
FIELDS = {
    "ev", "ts", "sig", "event_id", "session_id", "code_commit", "decision_id",
    "action_id", "attempt_id", "operation", "order_kind", "ticket", "deal",
    "order", "position_id", "retcode", "price", "volume", "lot", "profit",
    "requested_price", "filled_price", "sl", "tp", "bid", "ask", "time_msc",
    "broker_request_started_utc", "broker_response_received_utc", "broker_roundtrip_ns",
    "new_sl", "new_tp", "fill_price", "level", "observed_price", "candidate_leg_index",
    "position_index", "strategy_id", "strategy_fingerprint", "decision_status",
    "management_kind", "declared_action_count", "preflight_status", "broker_request_sent",
    "attempt_started_utc", "attempt_finished_utc", "observed_pl", "floating_pl", "total_pl",
    "action_revision", "message_revision_id", "position_exists", "after_action",
    "requested_sl", "requested_tp", "preflight_effective_sl", "preflight_effective_tp",
    "preflight_deferred_sl", "declared_action_ids", "label", "attempts", "direction",
}
SNAPSHOT_DETAIL_FIELDS = {
    "symbol", "magic", "position_type", "price_open", "price_current", "comment",
    "positions_read_started_utc", "positions_read_completed_utc",
    "positions_read_elapsed_ms",
}


def utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC required")
    return parsed


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def enum(value):
    rendered = str(value)
    return rendered if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", rendered) else "<other>"


def selected_fields(row):
    allowed = FIELDS | SNAPSHOT_DETAIL_FIELDS if row.get("ev") == "mt5_position_snapshot" else FIELDS
    selected = {key: row[key] for key in sorted(allowed & row.keys())
                if row[key] is None or isinstance(row[key], (int, float, bool))
                or isinstance(row[key], str) and len(row[key]) <= 128}
    if row.get("ev") == "mt5_action_attempt":
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        request = row.get("request") if isinstance(row.get("request"), dict) else {}
        selected["result_retcode"] = result.get("retcode") if type(result.get("retcode")) is int else None
        for key in ("deal", "order"):
            value = result.get(key)
            selected[f"result_{key}"] = value if type(value) is int and value > 0 else None
        for key in ("price", "volume"):
            value = result.get(key)
            selected[f"result_{key}"] = value if type(value) in (int, float) else None
        for key in ("sl", "tp", "type", "action", "position", "volume"):
            value = request.get(key)
            if value is None or type(value) in (int, float):
                selected[f"request_{key}"] = value
    action_ids = row.get("declared_action_ids")
    if row.get("ev") == "bot_internal_decision" and isinstance(action_ids, list):
        if len(action_ids) <= 100 and all(isinstance(value, str) and len(value) <= 128 for value in action_ids):
            selected["declared_action_ids"] = action_ids
    return selected


def _safe_management_value(value, depth=0):
    if depth > 4:
        return False
    if value is None or type(value) in (bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= 128
    if isinstance(value, list):
        return len(value) <= 100 and all(_safe_management_value(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return (len(value) <= 100 and all(isinstance(key, str) and len(key) <= 128
                                           and _safe_management_value(item, depth + 1)
                                           for key, item in value.items()))
    return False


def selected_management_fields(row):
    kind = row.get("management_kind")
    event = row.get("ev")
    if (row.get("management_contract") != "management_decision_inputs_v1"
            or kind not in MANAGEMENT_KINDS
            or event not in {"bot_internal_decision_started", "bot_internal_decision"}):
        raise ValueError("unsupported management capture contract or kind")
    selected = {key: row.get(key) for key in (
        "ev", "ts", "sig", "event_id", "session_id", "code_commit", "decision_id",
        "message_revision_id", "parent_decision_id", "management_contract", "management_kind",
        "strategy_id", "strategy_fingerprint", "direction", "payload_sha256",
        "decision_status", "declared_action_count", "declared_action_ids", "observed_at_utc")}
    if event == "bot_internal_decision_started":
        inputs = row.get("decision_inputs")
        state = row.get("state_before")
        required = MANAGEMENT_INPUT_FIELDS[kind]
        optional = MANAGEMENT_OPTIONAL_INPUT_FIELDS.get(kind, set())
        if (not isinstance(inputs, dict) or not required <= set(inputs) <= required | optional
                or not isinstance(state, dict) or not set(state) <= MANAGEMENT_STATE_FIELDS
                or kind.endswith("basket_guard") and
                (not isinstance(inputs.get("summary"), dict)
                 or not set(inputs["summary"]) <= MANAGEMENT_SUMMARY_FIELDS)):
            raise ValueError("management decision input or state schema mismatch")
        selected["decision_inputs"] = inputs
        selected["state_before"] = state
    else:
        result = row.get("decision_result")
        state = row.get("state_after")
        if not isinstance(state, dict) or not set(state) <= MANAGEMENT_STATE_FIELDS:
            raise ValueError("management final state schema mismatch")
        if kind.endswith("basket_guard") and result is not None:
            if (not isinstance(result, dict) or set(result) != {"action", "reason", "observed_pl", "state"}
                    or not isinstance(result["state"], dict)
                    or not set(result["state"]) <= MANAGEMENT_GUARD_STATE_FIELDS):
                raise ValueError("management guard result schema mismatch")
        selected["decision_result"] = result
        selected["state_after"] = state
    if not _safe_management_value(selected):
        raise ValueError("management capture contains oversized or unsafe value")
    return selected


def build_window(signal, calls, baskets, native):
    rows = [row for row in calls["rows"] if row.get("signal_id") == signal]
    if len(rows) != 1 or not rows[0]["direct_clock_anchor"]:
        raise ValueError("one direct-clock first-call anchor required")
    owned = [position for position in baskets["positions"] if position["signal_id"] == signal]
    by_ticket = {row["ticket"]: row for row in native["deals"]}
    tickets = [ticket for position in owned for ticket in position["deal_tickets"]]
    if not tickets or len(tickets) != len(set(tickets)):
        raise ValueError("native basket deals missing or duplicated")
    start = utc(rows[0]["receipt_utc"]) - timedelta(seconds=15)
    last_ms = max(by_ticket[ticket]["time_msc"] for ticket in tickets) - OFFSET_MS
    end = datetime.fromtimestamp(last_ms / 1_000, timezone.utc) + timedelta(seconds=30)
    if not start < end or end - start > timedelta(minutes=15) or start.date() != end.date():
        raise ValueError("pilot lifecycle outside bounded direct-clock window")
    return {"signal_id": signal, "start_utc": start.isoformat(timespec="milliseconds"),
            "end_utc": end.isoformat(timespec="milliseconds"), "native_deal_tickets": tickets}


def line_at(source, position):
    source.seek(position)
    if position:
        source.seek(position - 1)
        if source.read(1) != b"\n":
            source.readline()
    line_start = source.tell()
    raw = source.readline()
    if not raw:
        raise ValueError("journal ended during time index")
    return line_start, source.tell(), utc(json.loads(raw)["ts"])


def line_boundary(source, position):
    if position == 0:
        return True
    source.seek(position - 1)
    return source.read(1) == b"\n"


def bound(source, target, lower, upper):
    lo, hi = lower, upper
    for _ in range(48):
        if hi - lo < 4_096:
            break
        position, next_position, stamp = line_at(source, (lo + hi) // 2)
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
        if utc(json.loads(raw)["ts"]) >= target:
            return line_start
    return upper


def extract(spec):
    lower, upper = int(spec["scope_start_offset"]), int(spec["scope_end_offset"])
    window = spec["window"]
    start, end = utc(window["start_utc"]), utc(window["end_utc"])
    if (lower < 0 or upper <= lower or not start < end
            or end - start > timedelta(minutes=15)
            or not window["signal_id"].startswith(("canal1_", "canal2_"))):
        raise ValueError("invalid bounded lifecycle request")
    began = time.monotonic()
    with Path(spec["source"]).open("rb") as source:
        size = source.seek(0, 2)
        if size < upper:
            raise ValueError("journal truncated before lifecycle probe")
        first = max(lower, bound(source, start, lower, upper) - 2_000_000)
        last = min(upper, bound(source, end, lower, upper) + 2_000_000)
        if last - first > MAX_WINDOW_BYTES:
            raise ValueError("lifecycle probe read budget exceeded")
        source.seek(first)
        if first:
            source.readline()
        actual_start = source.tell()
        sha = hashlib.sha256()
        bytes_read = 0
        events = []
        kinds = Counter()
        keys_by_kind = {}
        decision_statuses = Counter()
        attempt_statuses = Counter()
        needle = window["signal_id"].encode("ascii")
        while source.tell() < last:
            if time.monotonic() - began > MAX_SECONDS:
                raise TimeoutError("lifecycle probe wall budget exceeded")
            raw = source.readline()
            if not raw:
                raise ValueError("journal ended during lifecycle probe")
            sha.update(raw)
            bytes_read += len(raw)
            if bytes_read > MAX_WINDOW_BYTES:
                raise ValueError("lifecycle probe read budget exceeded during scan")
            if needle not in raw:
                continue
            row = json.loads(raw)
            if row.get("sig") != window["signal_id"] or not start <= utc(row["ts"]) < end:
                continue
            kind = row.get("ev", "<missing>")
            kinds[kind] += 1
            keys_by_kind.setdefault(kind, set()).update(row)
            if kind == "bot_internal_decision":
                decision_statuses[(enum(row.get("management_kind")), enum(row.get("decision_status")))] += 1
            if kind == "mt5_action_attempt":
                result = row.get("result") if isinstance(row.get("result"), dict) else {}
                attempt_statuses[(enum(row.get("operation")), enum(row.get("preflight_status")),
                                  enum(row.get("broker_request_sent")), enum(result.get("retcode")))] += 1
            limit = MAX_ACTION_ATTEMPTS if kind == "mt5_action_attempt" else MAX_PER_KIND
            if kinds[kind] <= limit:
                events.append(selected_fields(row))
                if len(events) > MAX_EVENTS:
                    raise ValueError("lifecycle event budget exceeded")
        if Path(spec["source"]).stat().st_size < size:
            raise ValueError("journal truncated during lifecycle probe")
        return {"contract": "bounded_vm_signal_lifecycle_probe_v1", "status": "diagnostic_only",
                "window": window, "scope_start_offset": lower, "scope_end_offset": upper,
                "source_file_size_at_start": size, "scanned_offset_start": actual_start,
                "scanned_offset_end": source.tell(), "scanned_bytes": bytes_read,
                "source_window_sha256": sha.hexdigest(), "event_counts": dict(kinds),
                "events": events,
                "field_names_by_kind": {kind: sorted(keys) for kind, keys in keys_by_kind.items()},
                "decision_status_counts": [{"management_kind": kind, "decision_status": status,
                                            "count": count}
                                           for (kind, status), count in sorted(decision_statuses.items())],
                "action_attempt_status_counts": [{"operation": operation, "preflight_status": status,
                                                   "broker_request_sent": sent,
                                                   "result_retcode": retcode, "count": count}
                                                  for (operation, status, sent, retcode), count in sorted(attempt_statuses.items())],
                "sampled_kinds": {kind: count for kind, count in kinds.items()
                                  if count > (MAX_ACTION_ATTEMPTS if kind == "mt5_action_attempt" else MAX_PER_KIND)},
                "limitations": ["Time index assumes append-ordered journal timestamps; absence outside the bounded interval is unproven.",
                                "Only allowlisted fields are exported; raw Telegram text and credentials stay on the VM.",
                                "Event records are limited to the first 100 of each kind (1000 for mt5_action_attempt); event_counts still covers all matching lines.",
                                "This probe does not certify decision or risk-path parity."]}


def material_event(row):
    kind = row.get("ev")
    if kind in {"bot_internal_decision_started", "floating_pl_snapshot", "audit_snapshot"}:
        return False
    if isinstance(kind, str) and kind.startswith("strategy_shadow_"):
        return False
    if kind == "bot_internal_decision":
        return row.get("decision_status") != "completed" or row.get("declared_action_count") not in (0, None)
    return isinstance(kind, str)


def extract_material(spec):
    lower, upper = int(spec["scope_start_offset"]), int(spec["scope_end_offset"])
    indexed_sha = spec.get("indexed_chunk_sha256")
    indexed = indexed_sha is not None
    segment = spec["segment"]
    start, end = utc(segment["start_utc"]), utc(segment["end_utc"])
    signals = set(segment["active_signals"])
    if (lower < 0 or upper <= lower or not start < end or end - start > timedelta(minutes=5)
            or not 0 < len(signals) <= 40 or len(signals) != len(segment["active_signals"])
            or any(not signal.startswith(("canal1_", "canal2_")) for signal in signals)):
        raise ValueError("invalid bounded material segment")
    started = time.monotonic()
    with Path(spec["source"]).open("rb") as source:
        size = source.seek(0, 2)
        if size < upper:
            raise ValueError("journal truncated before material segment")
        if indexed:
            if (not isinstance(indexed_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", indexed_sha)
                    or not line_boundary(source, lower) or not line_boundary(source, upper)):
                raise ValueError("indexed material chunk boundary or hash invalid")
            first, last = lower, upper
        else:
            first = max(lower, bound(source, start, lower, upper) - 2_000_000)
            last = min(upper, bound(source, end, lower, upper) + 2_000_000)
        if last - first > MAX_WINDOW_BYTES:
            raise ValueError("material segment read budget exceeded")
        source.seek(first)
        if first and not indexed:
            source.readline()
        actual_start = source.tell()
        sha = hashlib.sha256()
        bytes_read = 0
        counts = Counter()
        selected_counts = Counter()
        events = []
        management_counts = Counter()
        management_events = []
        management_bytes = 0
        needles = tuple(signal.encode("ascii") for signal in signals)
        while source.tell() < last:
            if time.monotonic() - started > MAX_SECONDS:
                raise TimeoutError("material segment wall budget exceeded")
            raw = source.readline()
            if not raw:
                raise ValueError("journal ended during material segment")
            sha.update(raw)
            bytes_read += len(raw)
            if bytes_read > MAX_WINDOW_BYTES:
                raise ValueError("material segment read budget exceeded during scan")
            if not any(needle in raw for needle in needles):
                continue
            row = json.loads(raw)
            if row.get("sig") not in signals or not start <= utc(row["ts"]) < end:
                continue
            key = (row["sig"], row.get("ev", "<missing>"))
            counts[key] += 1
            if (row.get("management_contract") == "management_decision_inputs_v1"
                    and row.get("ev") in {"bot_internal_decision_started", "bot_internal_decision"}):
                captured = selected_management_fields(row)
                management_counts[key] += 1
                management_events.append(captured)
                management_bytes += len(json.dumps(captured, ensure_ascii=True).encode("utf-8"))
                if (len(management_events) > MAX_MANAGEMENT_EVENTS
                        or management_bytes > MAX_MANAGEMENT_OUTPUT_BYTES):
                    raise ValueError("management capture output budget exceeded")
            if material_event(row):
                selected_counts[key] += 1
                events.append(selected_fields(row))
                if len(events) > MAX_MATERIAL_EVENTS:
                    raise ValueError("material event budget exceeded")
        if Path(spec["source"]).stat().st_size < size:
            raise ValueError("journal truncated during material segment")
        if indexed and sha.hexdigest() != indexed_sha:
            raise ValueError("indexed material source chunk index hash mismatch")
        def count_rows(counter):
            return [{"signal_id": signal, "event": event, "count": count}
                    for (signal, event), count in sorted(counter.items())]
        return {"contract": ("bounded_vm_week_material_chunk_v3" if indexed
                             else "bounded_vm_week_material_segment_v2"), "status": "diagnostic_only",
                "source": str(Path(spec["source"]).resolve()) if indexed else None,
                "segment": segment, "scope_start_offset": lower, "scope_end_offset": upper,
                "source_file_size_at_start": size, "scanned_offset_start": actual_start,
                "scanned_offset_end": source.tell(), "scanned_bytes": bytes_read,
                "source_window_sha256": sha.hexdigest(), "event_counts": count_rows(counts),
                "material_event_counts": count_rows(selected_counts), "events": events,
                "material_records_complete_within_segment": not indexed,
                "records_complete_within_indexed_chunk": indexed,
                "management_event_counts": count_rows(management_counts),
                "management_events": management_events,
                "management_records_complete_within_segment": not indexed,
                "limitations": [*([] if indexed else [
                                    "Bounded time indexing assumes approximately append-ordered UTC journal timestamps."]),
                                "Only allowlisted fields are exported; no Telegram text or raw broker objects.",
                                "Management inputs and outcomes use a separate bounded allowlist, including no-op evaluations.",
                                "Other no-op decisions, high-frequency snapshots and separate shadow events are counted but not exported.",
                                "An indexed chunk proves only its own bytes; all relevant chunks require a complete prefix index."]}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--material-worker":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        spec = json.loads(base64.b64decode(sys.argv[2]))
        print(json.dumps(extract_material(spec), sort_keys=True), flush=True)
        return
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        spec = json.loads(base64.b64decode(sys.argv[2]))
        print(json.dumps(extract(spec), sort_keys=True), flush=True)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal", required=True)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--baskets", type=Path, required=True)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--remote-source", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--scope-start-offset", type=int, required=True)
    parser.add_argument("--scope-end-offset", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    calls, baskets, native = [json.loads(path.read_text(encoding="utf-8"))
                              for path in (args.calls, args.baskets, args.deals)]
    if (calls["inputs_sha256"].get(str(args.baskets)) != digest(args.baskets)
            or calls["inputs_sha256"].get(str(args.deals)) != digest(args.deals)):
        raise ValueError("lifecycle pilot native source mismatch")
    window = build_window(args.signal, calls, baskets, native)
    spec = {"source": args.remote_source, "scope_start_offset": args.scope_start_offset,
            "scope_end_offset": args.scope_end_offset, "window": window}
    encoded = base64.b64encode(json.dumps(spec, separators=(",", ":")).encode("ascii")).decode("ascii")
    command = ["ssh", "-i", str(args.identity), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=5", args.host, f"python - --worker {encoded}"]
    run = subprocess.run(command, input=Path(__file__).read_bytes(), capture_output=True,
                         timeout=MAX_SECONDS + 15, check=False)
    if run.returncode or run.stderr:
        last_line = run.stderr.decode("utf-8", errors="replace").splitlines()[-1:] or [""]
        detail = last_line[0] if re.fullmatch(r"(?:ValueError|TimeoutError|RuntimeError): [\w -]{1,120}", last_line[0]) else "unclassified remote error"
        raise RuntimeError(f"bounded lifecycle probe failed (exit={run.returncode}, {detail}); no report written")
    report = json.loads(run.stdout)
    if report.get("status") != "diagnostic_only" or report.get("window") != window:
        raise ValueError("remote lifecycle probe identity mismatch")
    report["local_inputs_sha256"] = {str(path): digest(path) for path in
                                     (args.calls, args.baskets, args.deals, Path(__file__))}
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=True)
        output.write("\n")
    print(json.dumps({"signal_id": args.signal, "scanned_bytes": report["scanned_bytes"],
                      "events": len(report["events"]), "event_kinds": len(report["event_counts"])}))


if __name__ == "__main__":
    main()
