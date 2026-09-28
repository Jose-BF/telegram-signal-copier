"""Historical entry triggers (time + direction only) from Telegram exports.

Gold: root messages whose text is an explicit "BUY/SELL ... NOW" command
(parser.is_canal2_entry), timestamped by date_unixtime (original publication,
seconds). The export keeps only the final text, so levels are NOT used; live
logs showed the direction is already in the first version (91/91, 0 flips).
Dubai: the direction stickers from the frozen catalog (catalog_v2), at their
publication time; live logs showed sticker edits never changed the sticker
(49 stickers, 19 with edit marker, 0 changes).
The strategy must supply its own management (no provider levels).
"""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser import is_canal2_entry, parse_canal2  # noqa: E402


def flat(text):
    if isinstance(text, str):
        return text
    return "".join(p if isinstance(p, str) else p.get("text", "") for p in text or [])


def gold(exports):
    seen, rows = {}, []
    for path in exports:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for m in data["messages"]:
            if m.get("type") != "message" or m.get("reply_to_message_id"):
                continue
            text = flat(m.get("text"))
            if not text or not is_canal2_entry(text):
                continue
            direction = parse_canal2(text).get("direction")
            if direction not in ("BUY", "SELL"):
                continue
            key = (data["id"], m["id"])
            row = {"channel": "canal2_hist", "chat_id": data["id"], "message_id": m["id"],
                   "direction": direction,
                   "published_utc": datetime.fromtimestamp(int(m["date_unixtime"]), timezone.utc).isoformat(),
                   "edited_utc": (datetime.fromtimestamp(int(m["edited_unixtime"]), timezone.utc).isoformat()
                                  if m.get("edited_unixtime") else None),
                   "source": Path(path).parent.name}
            if key in seen:
                if seen[key]["direction"] != direction:
                    seen[key]["conflict"] = True
                continue
            seen[key] = row
            rows.append(row)
    return sorted(rows, key=lambda r: r["published_utc"])


def dubai(catalog):
    rows = []
    for line in Path(catalog).read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        if s.get("direction") not in ("BUY", "SELL"):
            continue
        rows.append({"channel": "canal1_hist", "chat_id": s["chat_id"], "message_id": s["message_id"],
                     "direction": s["direction"], "published_utc": s["published_utc"],
                     "edited": bool(s.get("has_edited_snapshot")), "issues": s.get("issues", []),
                     "source": "catalog_v2/sticker_signals.jsonl"})
    return sorted(rows, key=lambda r: r["published_utc"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-export", nargs="+", required=True)
    ap.add_argument("--dubai-catalog", required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    a = ap.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name, rows, srcs in (("gold", gold(a.gold_export), a.gold_export), ("dubai", dubai(a.dubai_catalog), [a.dubai_catalog])):
        out = a.output_dir / f"{name}_triggers.jsonl"
        if out.exists():
            raise SystemExit(f"refusing to overwrite {out}")
        out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        months = {}
        for r in rows:
            months[r["published_utc"][:7]] = months.get(r["published_utc"][:7], 0) + 1
        summary[name] = {"triggers": len(rows), "BUY": sum(r["direction"] == "BUY" for r in rows),
                         "conflicts": sum(bool(r.get("conflict")) for r in rows), "by_month": months,
                         "sources_sha256": {Path(s).parent.name + "/" + Path(s).name: hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in srcs}}
    (a.output_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: {x: v[x] for x in ("triggers", "BUY", "conflicts", "by_month")} for k, v in summary.items()}, indent=1))


if __name__ == "__main__":
    main()
