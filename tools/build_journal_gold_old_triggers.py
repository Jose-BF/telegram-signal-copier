"""Gold OLD channel (3828356530) entries after the Telegram export ended (2026-07-03).

The bot journal logged that channel first without chat_id (message ids 2784-3515,
3-20 Jul) and then with chat_id -1003828356530 (21 Jul). Rows without chat_id whose
ids are >5000 are a mirror channel of the same provider (5-12 Jun, all matched to
the export within 2 min and same direction) and are excluded. Same rules as the
other builders: first seen version, root message, parser.is_canal2_entry.
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser import is_canal2_entry, parse_canal2  # noqa: E402

OLD = -1003828356530


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", required=True)
    ap.add_argument("--export-triggers", required=True, help="gold_triggers.jsonl from the export (ids to skip)")
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    exported = {json.loads(l)["message_id"] for l in open(a.export_triggers, encoding="utf-8")}
    versions = {}
    for line in open(a.journal, encoding="utf-8"):
        d = json.loads(line)
        if d["channel"] != "canal2" or not d.get("date_utc"):
            continue
        if not (d["chat_id"] == OLD or (d["chat_id"] is None and d["message_id"] < 5000)):
            continue
        versions.setdefault(d["message_id"], []).append(d)
    rows, flips = [], 0
    for mid, vs in versions.items():
        vs.sort(key=lambda v: v["ts"])
        first = vs[0]
        text = first.get("text") or ""
        if mid in exported or first.get("is_reply") or not text or not is_canal2_entry(text):
            continue
        direction = parse_canal2(text).get("direction")
        if direction not in ("BUY", "SELL"):
            continue
        later = {parse_canal2(v["text"]).get("direction") for v in vs[1:] if v.get("text") and is_canal2_entry(v["text"])}
        flips += bool(later - {direction, None})
        rows.append({"channel": "canal2_journal_old", "chat_id": 3828356530, "message_id": mid, "direction": direction,
                     "published_utc": first["date_utc"], "bot_first_seen_utc": first["ts"], "first_text": text[:80],
                     "source": "journal_telegram_raw_old_channel"})
    rows.sort(key=lambda r: r["published_utc"])
    if a.output.exists():
        raise SystemExit(f"refusing to overwrite {a.output}")
    a.output.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(json.dumps({"triggers": len(rows), "direction_flips": flips, "first": rows[0]["published_utc"],
                      "last": rows[-1]["published_utc"]}))


if __name__ == "__main__":
    main()
