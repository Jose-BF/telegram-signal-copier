"""One list of provider entry signals (time + direction) for both channels, Jan-Sep 2026.

Dubai (canal1):
  - direction stickers: catalog_v2 (export, to 2026-09-13 15:00 UTC), then bot journal;
  - text-only entries: texts the live bot accepts as entries (parser.is_canal1_signal_text)
    with no same-direction sticker in the previous 300 s (otherwise they are the
    sticker's companion text, not a new signal). The live bot opens a basket from
    such a text only when no canal1 signal is open; the replay's one-basket rule
    reproduces that.
  - forwards are flagged: "repost" = forwarded from the channel itself (an older
    signal re-posted), "forward_other" = forwarded from another channel (VIP 3.0
    since 2026-09-03). The journal extract carries no forward info (flag unknown).
Gold (canal2): export (old channel, to 2026-07-03), journal old channel (to 07-21),
  journal new channel (from 07-22). Same-direction posts <=10 s apart are merged.
"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser import is_canal1_signal_text, parse_canal1_text  # noqa: E402
from tools.run_causal_controls import STICKERS  # noqa: E402

SPLIT = "2026-09-13T15:00:00+00:00"
OWN = "channel1642806869"


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def first_text(entry):
    for snap in entry.get("snapshots", []):
        if snap.get("text"):
            return snap["text"]
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-dir", required=True)
    ap.add_argument("--journal", required=True)
    ap.add_argument("--triggers-dir", required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    cat, trig = Path(a.catalog_dir), Path(a.triggers_dir)
    out = []
    # --- Dubai stickers (catalog) ---
    for line in open(cat / "sticker_signals.jsonl", encoding="utf-8"):
        s = json.loads(line)
        if s.get("direction") not in ("BUY", "SELL") or s["published_utc"].replace("Z", "+00:00") >= SPLIT:
            continue
        origins = [o.get("forwarded_from_id") for o in s.get("forward_origins") or [] if o]
        fwd = "repost" if OWN in origins else ("forward_other" if s.get("forwarded") else "no")
        out.append({"id": f"c1_{s['message_id']}", "channel": "canal1", "kind": "sticker", "direction": s["direction"],
                    "published_utc": ts(s["published_utc"]).isoformat(), "forward": fwd, "source": "catalog_v2"})
    # --- Dubai text-only (catalog) ---
    stickers = sorted((ts(x["published_utc"]), x["direction"]) for x in out)
    for line in open(cat / "text_entries.jsonl", encoding="utf-8"):
        t = json.loads(line)
        if t["published_utc"].replace("Z", "+00:00") >= SPLIT:
            continue
        text = first_text(t)
        if not is_canal1_signal_text(text):
            continue
        direction = parse_canal1_text(text).get("direction") or t.get("direction")
        p = ts(t["published_utc"])
        near = any(d == direction and 0 <= (p - st).total_seconds() <= 300 for st, d in stickers)
        if near or direction not in ("BUY", "SELL"):
            continue
        origins = [o.get("forwarded_from_id") for o in t.get("forward_origins") or [] if o]
        fwd = "repost" if OWN in origins else ("forward_other" if t.get("forwarded") else "no")
        out.append({"id": f"c1_{t['message_id']}", "channel": "canal1", "kind": "text_only", "direction": direction,
                    "published_utc": p.isoformat(), "forward": fwd, "source": "catalog_v2"})
    # --- Dubai after the catalog: journal (first seen version) ---
    first = {}
    for line in open(a.journal, encoding="utf-8"):
        d = json.loads(line)
        if d["channel"] != "canal1" or not d.get("date_utc") or d["date_utc"] < SPLIT:
            continue
        k = d["message_id"]
        if k not in first or d["ts"] < first[k]["ts"]:
            first[k] = d
    jrows = sorted(first.values(), key=lambda d: d["date_utc"])
    jst = []
    for d in jrows:
        direction = STICKERS.get(str(d.get("sticker_id") or ""))
        if direction:
            jst.append((ts(d["date_utc"]), direction))
            out.append({"id": f"c1_{d['message_id']}", "channel": "canal1", "kind": "sticker", "direction": direction,
                        "published_utc": ts(d["date_utc"]).isoformat(), "bot_first_seen_utc": d["ts"],
                        "forward": "unknown", "source": "journal"})
    for d in jrows:
        text = d.get("text") or ""
        if d.get("sticker_id") or d.get("is_reply") or not is_canal1_signal_text(text):
            continue
        direction = parse_canal1_text(text).get("direction")
        p = ts(d["date_utc"])
        if direction not in ("BUY", "SELL") or any(dd == direction and 0 <= (p - st).total_seconds() <= 300 for st, dd in jst):
            continue
        out.append({"id": f"c1_{d['message_id']}", "channel": "canal1", "kind": "text_only", "direction": direction,
                    "published_utc": p.isoformat(), "bot_first_seen_utc": d["ts"], "forward": "unknown", "source": "journal"})
    # --- Dubai: a text-only entry followed by its own sticker (<=300 s, same direction)
    #     is ONE signal: keep the earlier time, drop the sticker, inherit its forward flag.
    texts = [r for r in out if r["channel"] == "canal1" and r["kind"] == "text_only"]
    merged_pairs = []
    for t in texts:
        tp = ts(t["published_utc"])
        follow = [r for r in out if r["channel"] == "canal1" and r["kind"] == "sticker" and r["direction"] == t["direction"]
                  and 0 < (ts(r["published_utc"]) - tp).total_seconds() <= 300]
        if follow:
            st = min(follow, key=lambda r: r["published_utc"])
            out.remove(st)
            if st["forward"] in ("repost", "forward_other"):
                t["forward"] = st["forward"]
            t["kind"] = "text_then_sticker"
            merged_pairs.append((t["id"], st["id"]))
    # --- then identical-direction signals <=5 s apart are duplicates (live bot aliases them) ---
    # --- Gold ---
    gold = []
    for name, src in (("gold_triggers.jsonl", "export_old_channel"), ("gold_journal_old_triggers.jsonl", "journal_old_channel"),
                      ("gold_journal_triggers.jsonl", "journal_new_channel")):
        for line in open(trig / name, encoding="utf-8"):
            r = json.loads(line)
            gold.append({"id": f"c2_{abs(r['chat_id'])}_{r['message_id']}", "channel": "canal2", "kind": "text_now",
                         "direction": r["direction"], "published_utc": ts(r["published_utc"]).isoformat(),
                         "bot_first_seen_utc": r.get("bot_first_seen_utc"), "forward": "no", "source": src})
    out.sort(key=lambda r: r["published_utc"]); gold.sort(key=lambda r: r["published_utc"])
    merged, dropped = [], []
    for series, gap in ((out, 5), (gold, 10)):
        keep = []
        for r in series:
            prev = next((x for x in reversed(keep) if x["channel"] == r["channel"]), None)
            if prev and prev["direction"] == r["direction"] and (ts(r["published_utc"]) - ts(prev["published_utc"])).total_seconds() <= gap:
                dropped.append(r["id"]); continue
            keep.append(r)
        merged += keep
    merged.sort(key=lambda r: r["published_utc"])
    if a.output.exists():
        raise SystemExit(f"refusing to overwrite {a.output}")
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text("".join(json.dumps(r) + "\n" for r in merged), encoding="utf-8")
    from collections import Counter
    print(json.dumps({"signals": len(merged), "dropped_duplicates": dropped, "text_sticker_pairs": merged_pairs,
                      "by": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in sorted(Counter((r["channel"], r["kind"], r["forward"]) for r in merged).items())},
                      "by_month": {f"{k[0]}|{k[1]}": v for k, v in sorted(Counter((r["channel"], r["published_utc"][:7]) for r in merged).items())}}, indent=1))


if __name__ == "__main__":
    main()
