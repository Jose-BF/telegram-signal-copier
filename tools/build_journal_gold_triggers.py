"""Gold (current channel) entry triggers from the bot journal's telegram_raw events.

The journal records every version of every message with its original publication
time (date_utc) and the bot's receipt time (ts). A trigger is a root message whose
FIRST seen text is an explicit BUY/SELL ... NOW command (parser.is_canal2_entry),
as the live bot decides. Same-direction triggers <=10 s apart are double posts.
"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser import is_canal2_entry, parse_canal2  # noqa: E402

CHAT = -1003908582492
DUP_S = 10


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    versions = {}
    for line in open(a.journal, encoding="utf-8"):
        d = json.loads(line)
        if d["channel"] != "canal2" or d["chat_id"] != CHAT or not d.get("date_utc"):
            continue
        versions.setdefault(d["message_id"], []).append(d)
    rows, flips, dups = [], 0, []
    for mid, vs in versions.items():
        vs.sort(key=lambda v: v["ts"])
        first = vs[0]
        text = first.get("text") or ""
        if first.get("is_reply") or not text or not is_canal2_entry(text):
            continue
        direction = parse_canal2(text).get("direction")
        if direction not in ("BUY", "SELL"):
            continue
        later = {parse_canal2(v["text"]).get("direction") for v in vs[1:] if v.get("text") and is_canal2_entry(v["text"])}
        flip = bool(later - {direction, None})
        flips += flip
        rows.append({"channel": "canal2_journal", "chat_id": CHAT, "message_id": mid, "direction": direction,
                     "published_utc": first["date_utc"], "bot_first_seen_utc": first["ts"],
                     "direction_changed_later": flip, "first_text": text[:80]})
    rows.sort(key=lambda r: r["published_utc"])
    kept = []
    for r in rows:
        prev = kept[-1] if kept else None
        if (prev and prev["direction"] == r["direction"]
                and (datetime.fromisoformat(r["published_utc"]) - datetime.fromisoformat(prev["published_utc"])).total_seconds() <= DUP_S):
            dups.append(r["message_id"]); continue
        kept.append(r)
    if a.output.exists():
        raise SystemExit(f"refusing to overwrite {a.output}")
    a.output.write_text("".join(json.dumps(r) + "\n" for r in kept), encoding="utf-8")
    by_month = {}
    for r in kept:
        by_month[r["published_utc"][:7]] = by_month.get(r["published_utc"][:7], 0) + 1
    print(json.dumps({"triggers": len(kept), "double_posts_dropped": dups, "direction_flips": flips,
                      "first": kept[0]["published_utc"], "last": kept[-1]["published_utc"], "by_month": by_month}))


if __name__ == "__main__":
    main()
