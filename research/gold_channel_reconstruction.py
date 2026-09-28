"""Reconstruct every Gold (canal2) signal as the channel publishes and scores it.

Source: bot journal telegram_raw (every version of every message, bot receipt
time `ts`, publication `date_utc`). Period with full versions: old channel from
2026-07-03 (no chat_id rows with ids < 5000, then chat -1003828356530) and the new
channel -1003908582492 from 2026-07-22. For each root signal:
  - kind: "now" (parser.is_canal2_entry, what the live bot trades) or "zone"
    (direction + range but not an entry command, e.g. "GOLD BUY ZONE 4230-4225");
  - first_seen: first version received by the bot; levels_seen: first version
    carrying a range (zone), with its TPs/SL; later level changes are recorded;
  - replies (reply_to the root or to its aliases) classified: tp_hit(n), sl_hit,
    pips(+n), move_sl, be/risk free, close.
Nothing here looks at prices; tick checks are done separately.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from parser import is_canal2_entry, parse_canal2

NEW, OLD = -1003908582492, -1003828356530


def chat_of(d):
    if d["chat_id"] in (NEW, OLD):
        return d["chat_id"]
    if d["chat_id"] is None and d["message_id"] < 5000:
        return OLD
    return None


def classify_reply(text: str) -> dict:
    t = (text or "").lower()
    out = {}
    m = re.findall(r"tp\s*(\d)\s*(?:hit|smashed)", t) or re.findall(r"target\s*(\d)\s*✅", t)
    if m:
        out["tp_hit"] = max(int(x) for x in m)
    if re.search(r"\bsl\s*hit\b|stop\s*loss\s*hit|stopped out", t):
        out["sl_hit"] = True
    p = re.findall(r"\+\s*(\d+(?:\.\d+)?)\s*(?:pips)?", t)
    if p:
        out["pips"] = max(float(x) for x in p)
    if "best entr" in t:
        out["from_best_entry"] = True
    if re.search(r"move (?:my )?sl|i will move my sl", t):
        out["move_sl"] = True
    if "risk free" in t or re.search(r"\bbe\b|break ?even", t):
        out["risk_free"] = True
    if re.search(r"\bclos(e|ing)\b", t):
        out["close"] = True
    return out


def build(journal: Path, start_utc: str = "2026-07-03T00:00:00+00:00"):
    versions = {}
    for line in open(journal, encoding="utf-8"):
        d = json.loads(line)
        if d["channel"] != "canal2" or not d.get("date_utc") or d["date_utc"] < start_utc:
            continue
        c = chat_of(d)
        if c is None:
            continue
        versions.setdefault((c, d["message_id"]), []).append(d)
    for vs in versions.values():
        vs.sort(key=lambda v: v["ts"])
    roots = {}
    for key, vs in versions.items():
        first = vs[0]
        if first.get("is_reply"):
            continue
        texts = [v.get("text") or "" for v in vs]
        parsed_first = parse_canal2(texts[0]) if texts[0] else {}
        direction = parsed_first.get("direction")
        now = bool(texts[0]) and is_canal2_entry(texts[0])
        zone_v = next((v for v in vs if v.get("text") and parse_canal2(v["text"]).get("range")), None)
        if not direction or not (now or zone_v):
            continue
        rec = {"chat": key[0], "id": key[1], "kind": "now" if now else "zone", "direction": direction,
               "published_utc": first["date_utc"], "first_seen_utc": first["ts"], "first_text": texts[0][:120],
               "high_risk": "high risk" in texts[0].lower(), "replies": []}
        if zone_v:
            p = parse_canal2(zone_v["text"])
            rec.update(levels_seen_utc=zone_v["ts"], zone=list(p["range"]), tps=p.get("tps", []), sl=p.get("sl"),
                       tp_open="tp open" in zone_v["text"].lower())
            changes = []
            last = (tuple(p["range"]), tuple(p.get("tps", [])), p.get("sl"))
            for v in vs:
                if v["ts"] <= zone_v["ts"] or not v.get("text"):
                    continue
                q = parse_canal2(v["text"])
                cur = (tuple(q.get("range") or ()), tuple(q.get("tps", [])), q.get("sl"))
                if cur != last and q.get("range"):
                    changes.append({"ts": v["ts"], "zone": list(q["range"]), "tps": q.get("tps", []), "sl": q.get("sl")})
                    last = cur
            rec["level_changes"] = changes
            # full timeline of parsed levels, each with the bot receipt time of that version
            timeline = []
            for v in vs:
                if not v.get("text"):
                    continue
                q = parse_canal2(v["text"])
                if not q.get("range"):
                    continue
                cur = {"ts": v["ts"], "zone": list(q["range"]), "tps": q.get("tps", []), "sl": q.get("sl"),
                       "tp_open": "tp open" in v["text"].lower()}
                if not timeline or (cur["zone"], cur["tps"], cur["sl"]) != (timeline[-1]["zone"], timeline[-1]["tps"], timeline[-1]["sl"]):
                    timeline.append(cur)
            rec["timeline"] = timeline
            rec["dir_changed"] = any(parse_canal2(v["text"]).get("direction") not in (None, direction) for v in vs if v.get("text"))
        roots[key] = rec
    for key, vs in versions.items():
        first = vs[0]
        if not first.get("is_reply"):
            continue
        target = (key[0], first.get("reply_to_msg_id"))
        if target in roots:
            roots[target]["replies"].append({"id": key[1], "ts": first["ts"], "text": (first.get("text") or "")[:160],
                                            **classify_reply(first.get("text"))})
    out = sorted(roots.values(), key=lambda r: r["published_utc"])
    for r in out:
        r["replies"].sort(key=lambda x: x["ts"])
        r["claim_tp_max"] = max((x.get("tp_hit", 0) for x in r["replies"]), default=0)
        r["claim_sl_hit"] = any(x.get("sl_hit") for x in r["replies"])
        r["claim_pips_max"] = max((x.get("pips", 0) for x in r["replies"]), default=0)
    return out


if __name__ == "__main__":
    import sys
    rows = build(Path(sys.argv[1]))
    Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[2]).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    from collections import Counter
    print(len(rows), Counter((r["kind"], "zone" in r) for r in rows))
    print("with replies", sum(1 for r in rows if r["replies"]), "claim tp>0", sum(r["claim_tp_max"] > 0 for r in rows),
          "claim sl", sum(r["claim_sl_hit"] for r in rows), "level changes", sum(1 for r in rows if r.get("level_changes")),
          "dir changed", sum(1 for r in rows if r.get("dir_changed")))
