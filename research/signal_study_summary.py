"""Summaries of per_signal.jsonl: groups with counts, race win rates (Wilson 90 %) and mean results."""
import json, math, sys
from collections import defaultdict


def wilson(k, n, z=1.645):
    if not n:
        return (None, None)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(100 * (c - r) / d, 1), round(100 * (c + r) / d, 1))


def summarize(rows, key):
    g = defaultdict(list)
    for r in rows:
        if r["status"] == "ok":
            g[key(r)].append(r)
    out = []
    for k in sorted(g, key=lambda x: str(x)):
        rs = g[k]; o = {"group": k, "n": len(rs)}
        for x in ("3", "5", "10"):
            w = sum(r.get(f"race_{x}") == "win" for r in rs); l = sum(r.get(f"race_{x}") == "loss" for r in rs)
            o[f"race{x}_win%"] = round(100 * w / (w + l), 1) if w + l else None
            o[f"race{x}_ci90"] = wilson(w, w + l)
        for h in (15, 60, 240):
            v = [r[f"end_{h}"] for r in rs if f"end_{h}" in r and f"closed_gap_{h}" not in r]
            o[f"end{h}_mean"] = round(sum(v) / len(v), 2) if v else None
            o[f"end{h}_pos%"] = round(100 * sum(x > 0 for x in v) / len(v), 1) if v else None
        for h in (60,):
            mf = sorted(r[f"mfe_{h}"] for r in rs if f"mfe_{h}" in r); ma = sorted(r[f"mae_{h}"] for r in rs if f"mae_{h}" in r)
            o[f"mfe{h}_med"] = mf[len(mf) // 2] if mf else None; o[f"mae{h}_med"] = ma[len(ma) // 2] if ma else None
        out.append(o)
    return out


if __name__ == "__main__":
    rows = [json.loads(l) for l in open(sys.argv[1])]
    keys = {
        "month": lambda r: (r["channel"], r["published_utc"][:7]),
        "gold_source": lambda r: (r["channel"], r["source"]),
        "dubai_kind_forward": lambda r: (r["channel"], r["kind"], r["forward"]),
        "hour_utc": lambda r: (r["channel"], f"{r['hour_utc']:02d}"),
        "weekday": lambda r: (r["channel"], r["weekday"]),
        "direction": lambda r: (r["channel"], r["direction"]),
        "pre_move_60": lambda r: (r["channel"], "sin dato" if "pre_move_60" not in r else ("a favor del ultimo movimiento" if r["pre_move_60"] > 1 else ("en contra del ultimo movimiento" if r["pre_move_60"] < -1 else "plano (+-1$)"))),
        "all": lambda r: (r["channel"],),
    }
    json.dump({k: summarize(rows, f) for k, f in keys.items()}, open(sys.argv[2], "w"), indent=1)
