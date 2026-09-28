"""Dubai (canal1) direction-sticker triggers from the bot journal telegram_raw events.

A trigger is a message whose first seen version carries a known direction sticker
(tools.run_causal_controls.STICKERS). Identical-direction stickers <=5 s apart are
duplicates (the live bot aliases them). Publication time = date_utc.
"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_causal_controls import STICKERS  # noqa: E402

CHAT = -1001642806869


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", required=True)
    ap.add_argument("--since", required=True, help="publication date (UTC) from which to emit")
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    first = {}
    for line in open(a.journal, encoding="utf-8"):
        d = json.loads(line)
        if d["channel"] != "canal1" or d["chat_id"] not in (CHAT, None) or not d.get("date_utc"):
            continue
        k = d["message_id"]
        if k not in first or d["ts"] < first[k]["ts"]:
            first[k] = d
    rows = []
    for mid, d in first.items():
        direction = STICKERS.get(str(d.get("sticker_id") or ""))
        if direction and d["date_utc"] >= a.since:
            rows.append({"channel": "canal1_journal", "chat_id": 1642806869, "message_id": mid, "direction": direction,
                         "published_utc": d["date_utc"], "bot_first_seen_utc": d["ts"], "source": "journal_telegram_raw"})
    rows.sort(key=lambda r: r["published_utc"])
    kept, dups = [], []
    for r in rows:
        if kept and kept[-1]["direction"] == r["direction"] and (
                datetime.fromisoformat(r["published_utc"]) - datetime.fromisoformat(kept[-1]["published_utc"])).total_seconds() <= 5:
            dups.append(mid); continue
        kept.append(r)
    if a.output.exists():
        raise SystemExit(f"refusing to overwrite {a.output}")
    a.output.write_text("".join(json.dumps(r) + "\n" for r in kept), encoding="utf-8")
    print(json.dumps({"triggers": len(kept), "duplicates": len(dups), "first": kept[0]["published_utc"], "last": kept[-1]["published_utc"]}))


if __name__ == "__main__":
    main()
