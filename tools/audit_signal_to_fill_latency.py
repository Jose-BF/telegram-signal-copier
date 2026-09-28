"""Measure every stage from a Telegram signal to the broker fill and first protection.

Reads the bot journal (JSONL, streamed) and optionally the MT5 terminal logs.
For each entry signal it rebuilds one timeline:

  publish (Telegram date, 1 s resolution) -> first seen by the bot (push or poll)
  -> decision -> order attempt starts (Python) -> request leaves the terminal
  -> broker answer ("done in") -> Python gets the result -> first SL/TP installed.

Journal and terminal share the VM clock. Telegram's message date is truncated to
the second, so the first stage carries +-1 s of uncertainty. Diagnostic only.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_mt5_terminal_trade_logs import (  # noqa: E402
    DEFAULT_ZONE, build_market_chains, digest, quantiles, read_log,
)
from zoneinfo import ZoneInfo  # noqa: E402

CONTRACT = "signal_to_fill_latency_v1"
EVENTS = ("handler_entry", "signal_received", "mt5_order_requested", "mt5_action_attempt",
          "mt5_order_result", "market_filled")
NEEDLES = tuple(f'"ev": "{name}"'.encode() for name in EVENTS)
SEGMENTS = ("publish_to_seen_ms", "seen_to_decision_ms", "decision_to_attempt_ms",
            "attempt_to_terminal_request_ms", "terminal_roundtrip_ms", "terminal_done_to_python_ms",
            "attempt_duration_ms", "publish_to_fill_result_ms", "fill_to_first_protection_ms",
            "source_tick_age_at_attempt_ms")


def ts_ms(value: str | None) -> int | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(round(parsed.timestamp() * 1000))


def read_journal(paths, start_ms, end_ms):
    events = []
    for path in paths:
        with open(path, "rb") as handle:
            for raw in handle:
                if not any(needle in raw for needle in NEEDLES):
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                stamp = ts_ms(event.get("ts"))
                if stamp is None or not start_ms <= stamp < end_ms:
                    continue
                event["_ms"] = stamp
                events.append(event)
    events.sort(key=lambda e: e["_ms"])
    return events


def terminal_orders(log_dir: Path | None, days: set[str], zone: str):
    if log_dir is None:
        return {}
    events = []
    for day in sorted(days):
        path = Path(log_dir) / f"{day}.log"
        if path.exists():
            events.extend(read_log(path, ZoneInfo(zone)))
    events.sort(key=lambda e: (e["utc_ms"], e["day"], e["line"]))
    chains, _ = build_market_chains(events)
    return {c["done"]["order"]: c for c in chains if c["done"]}


def build_rows(events, orders, broker_offset_seconds):
    by_sig = defaultdict(list)
    for event in events:
        if event.get("sig"):
            by_sig[event["sig"]].append(event)
    rows = []
    for sig, items in by_sig.items():
        signals = [e for e in items if e["ev"] == "signal_received"]
        if not signals:
            continue
        signal = signals[0]
        publish = ts_ms(signal.get("tg_ts"))
        seen = [e for e in items if e["ev"] == "handler_entry" and e.get("tg_ts") == signal.get("tg_ts")
                and e.get("kind") in {"new", "poll_new"}]
        first_seen = min(seen, key=lambda e: ts_ms(e.get("handler_entry_ts")) or e["_ms"]) if seen else None
        row = {"sig": sig, "channel": signal.get("channel"), "trigger": signal.get("trigger"),
               "day": signal["ts"][:10], "publish_ms": publish, "decision_ms": signal["_ms"],
               "seen_path": first_seen.get("kind") if first_seen else None,
               "seen_ms": (ts_ms(first_seen.get("handler_entry_ts")) or first_seen["_ms"]) if first_seen else None}
        request = next((e for e in items if e["ev"] == "mt5_order_requested"
                        and e.get("order_kind") == "market" and e["_ms"] >= signal["_ms"]), None)
        if request is None:
            row["status"] = "no_market_order"
            rows.append(row)
            continue
        attempt = next((e for e in items if e["ev"] == "mt5_action_attempt"
                        and e.get("attempt_id") == request.get("attempt_id")), None)
        result = next((e for e in items if e["ev"] == "mt5_order_result"
                       and e.get("attempt_id") == request.get("attempt_id")), None)
        row.update(status="ordered", request_ms=request["_ms"],
                   attempt_start_ms=ts_ms(attempt.get("attempt_started_utc")) if attempt else None,
                   attempt_end_ms=ts_ms(attempt.get("attempt_finished_utc")) if attempt else None,
                   retcode=result.get("retcode") if result else None,
                   order=result.get("order") if result else None)
        if attempt and (attempt.get("source_tick") or {}).get("time_msc"):
            row["source_tick_utc_ms"] = attempt["source_tick"]["time_msc"] - broker_offset_seconds * 1000
        chain = orders.get(row["order"]) if row.get("order") else None
        if chain is not None:
            row["terminal_request_ms"] = chain["request"]["utc_ms"] if chain["request"] else None
            row["terminal_done_ms"] = chain["done"]["utc_ms"]
            row["terminal_roundtrip_ms"] = chain["done"]["done_ms"]
        protection = next((e for e in items if e["ev"] == "mt5_action_attempt"
                           and e.get("operation") == "MODIFY_SLTP" and e.get("ticket") == row.get("order")
                           and (e.get("result") or {}).get("retcode") == 10009
                           and row.get("attempt_end_ms") and e["_ms"] >= row["attempt_end_ms"]), None)
        if protection:
            row["first_protection_done_ms"] = ts_ms(protection.get("attempt_finished_utc"))
        rows.append(row)
    for row in rows:
        segment(row)
    return sorted(rows, key=lambda r: r["decision_ms"])


def _diff(row, a, b):
    return row[b] - row[a] if row.get(a) is not None and row.get(b) is not None else None


def segment(row):
    row["publish_to_seen_ms"] = _diff(row, "publish_ms", "seen_ms")
    row["seen_to_decision_ms"] = _diff(row, "seen_ms", "decision_ms")
    row["decision_to_attempt_ms"] = _diff(row, "decision_ms", "attempt_start_ms")
    row["attempt_to_terminal_request_ms"] = _diff(row, "attempt_start_ms", "terminal_request_ms")
    row["terminal_done_to_python_ms"] = _diff(row, "terminal_done_ms", "attempt_end_ms")
    row["attempt_duration_ms"] = _diff(row, "attempt_start_ms", "attempt_end_ms")
    row["publish_to_fill_result_ms"] = _diff(row, "publish_ms", "attempt_end_ms")
    row["fill_to_first_protection_ms"] = _diff(row, "attempt_end_ms", "first_protection_done_ms")
    row["source_tick_age_at_attempt_ms"] = _diff(row, "source_tick_utc_ms", "attempt_start_ms")
    row.setdefault("terminal_roundtrip_ms", None)


def summarize(rows, period_days):
    periods = {}
    for name, (first, last) in period_days.items():
        chosen = [r for r in rows if first <= r["day"] <= last and r["status"] == "ordered"]
        periods[name] = {"signals": sum(1 for r in rows if first <= r["day"] <= last),
                         "ordered": len(chosen),
                         **{seg: quantiles([r[seg] for r in chosen]) for seg in SEGMENTS}}
    return periods


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, action="append", required=True)
    parser.add_argument("--start", required=True, help="UTC date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="UTC date YYYY-MM-DD (exclusive)")
    parser.add_argument("--terminal-log-dir", type=Path)
    parser.add_argument("--broker-offset-seconds", type=int, default=10_800)
    parser.add_argument("--period", action="append", default=[],
                        help="name=YYYY-MM-DD:YYYY-MM-DD (inclusive) for the summary")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    start = ts_ms(args.start + "T00:00:00+00:00")
    end = ts_ms(args.end + "T00:00:00+00:00")
    events = read_journal(args.journal, start, end)
    days = {e["ts"][:10].replace("-", "") for e in events}
    # Terminal logs are local-day files; include neighbours for UTC/local edges.
    extra = set()
    for day in days:
        stamp = datetime.strptime(day, "%Y%m%d")
        for delta in (-1, 1):
            extra.add((stamp.fromordinal(stamp.toordinal() + delta)).strftime("%Y%m%d"))
    orders = terminal_orders(args.terminal_log_dir, days | extra, DEFAULT_ZONE)
    rows = build_rows(events, orders, args.broker_offset_seconds)
    periods = dict(p.split("=", 1) for p in args.period)
    period_days = {k: tuple(v.split(":")) for k, v in periods.items()} or {"all": (args.start, args.end)}
    report = {"contract": CONTRACT, "status": "diagnostic_only", "window_utc": [args.start, args.end],
              "rows": rows, "summary": summarize(rows, period_days),
              "inputs_sha256": {str(p): digest(p) for p in args.journal},
              "limitations": ["Telegram message date has 1 s resolution: publish_to_seen carries +-1 s.",
                              "Journal and terminal log share the VM clock; broker tick times use the offset hypothesis.",
                              "Only the first market order per signal is timed here."]}
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report["summary"], indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
