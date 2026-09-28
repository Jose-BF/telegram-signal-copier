"""Build the list of channel entry triggers (BUY/SELL, channel, times) from bot journals.

Source of truth is the bot's own signal_received event: what the live bot
recognised as a new entry signal, with Telegram publish time and receipt time.
Optionally cross-checks against provider_signal_catalog.json formal signals so
signals the bot never received (downtime, parsing gaps) stay visible.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_mt5_terminal_trade_logs import digest  # noqa: E402

CONTRACT = "signal_trigger_catalog_v1"


def read_signals(paths, start, end):
    """First entry_signal understanding per message, flagged if the bot acted on it."""
    rows, acted = {}, {}
    for path in paths:
        with open(path, "rb") as handle:
            for raw in handle:
                is_signal = b'"ev": "signal_received"' in raw
                if not is_signal and not (b'"ev": "telegram_understood"' in raw and b'"entry_signal"' in raw):
                    continue
                event = json.loads(raw)
                day = event["ts"][:10]
                if not start <= day < end:
                    continue
                sig = event["sig"]
                if is_signal:
                    acted.setdefault(sig, event)
                    continue
                if event.get("kind") != "entry_signal" or (sig in rows and rows[sig]["understood_utc"] <= event["ts"]):
                    continue
                tail = sig.rsplit("_", 1)[1]
                rows[sig] = {"sig": sig, "channel": event.get("channel"),
                             "message_id": int(tail) if tail.isdigit() else None,
                             "direction": event.get("direction"), "parser": event.get("parser"),
                             "published_utc": event.get("tg_ts"), "understood_utc": event["ts"],
                             "is_edit": event.get("is_edit"), "code_commit": event.get("code_commit")}
    for sig, event in acted.items():
        row = rows.setdefault(sig, {"sig": sig, "channel": event.get("channel"), "message_id": None,
                                    "direction": event.get("direction"), "parser": None,
                                    "published_utc": event.get("tg_ts"), "understood_utc": event["ts"],
                                    "is_edit": None, "code_commit": event.get("code_commit")})
        row.update(acted=True, trigger=event.get("trigger"), received_utc=event["ts"],
                   tg_to_bot_ms=event.get("tg_to_bot_ms"))
    for row in rows.values():
        row.setdefault("acted", False)
        row["received_utc"] = row.get("received_utc") or row["understood_utc"]
    return sorted(rows.values(), key=lambda r: r["understood_utc"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, action="append", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--provider-catalog", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    rows = read_signals(args.journal, args.start, args.end)
    weekly = Counter()
    for row in rows:
        iso = datetime.fromisoformat(row["received_utc"]).isocalendar()
        weekly[(f"{iso[0]}-W{iso[1]:02d}", row["channel"])] += 1
    cross = None
    if args.provider_catalog:
        catalog = json.loads(args.provider_catalog.read_text(encoding="utf-8"))["signals"]
        formal = {s["provider_signal_id"]: s for s in catalog if s["record_type"] == "formal_signal"
                  and args.start <= (s["signal_ts_utc"] or "")[:10] < args.end}
        last = max((s["signal_ts_utc"] for s in catalog if s["signal_ts_utc"]), default="")[:10]
        seen = {r["sig"] for r in rows if r["received_utc"][:10] <= last}
        cross = {"provider_catalog_last_day": last, "formal_signals": len(formal),
                 "formal_without_bot_signal": sorted(set(formal) - seen),
                 "bot_signal_without_formal": sorted(s for s in seen if s not in formal)}
    with (args.output_dir / "signals.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    summary = {"contract": CONTRACT, "window": [args.start, args.end], "signals": len(rows),
               "by_channel": dict(Counter(r["channel"] for r in rows)),
               "by_parser": dict(Counter(f"{r['channel']}:{r.get('parser')}" for r in rows)),
               "acted_by_bot": dict(Counter(f"{r['channel']}:{r['acted']}" for r in rows)),
               "by_direction": dict(Counter(f"{r['channel']}:{r['direction']}" for r in rows)),
               "weekly": {f"{w} {c}": n for (w, c), n in sorted(weekly.items())},
               "cross_check": cross,
               "inputs_sha256": {str(p): digest(p) for p in args.journal},
               "outputs_sha256": {"signals.jsonl": digest(args.output_dir / "signals.jsonl")},
               "limitations": ["Entry signals understood by the live bot; messages lost during downtime show up in the cross-check.",
                               "Telegram publish time has 1 s resolution."]}
    with (args.output_dir / "summary.json").open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(json.dumps({k: summary[k] for k in ("signals", "by_channel", "acted_by_bot", "weekly")}, indent=1))
    if cross:
        print("cross:", cross["provider_catalog_last_day"], cross["formal_signals"],
              len(cross["formal_without_bot_signal"]), len(cross["bot_signal_without_formal"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
