"""P3: does Gold (canal 2) place its signal, zone, SL and TPs on support/resistance levels?

For each Gold signal with published levels (runtime_data/gold_channel_v1/signals.jsonl, Jul-Sep),
distance from each of its prices to the nearest known level (research/levels.py, causal) versus a
baseline that keeps the same offsets from the price but applies them to another signal's price of
the same direction (keeps the offset pattern, breaks any alignment with levels).
Also: distance from the market price at the signal moment to the nearest level, real moments vs
random-time placebo moments (both channels).
usage: python search2/p3_niveles.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np
import pandas as pd

from research.levels import LevelBook, nearest

FAMILIES = (None, ("swing_15m",), ("swing_1h",), ("swing_4h",), ("prev_day", "prev_week"), ("asia",), ("round50",), ("round10",))


def price_at(book, t0):
    k = book._upto(t0)
    return float(book.c[k - 1]) if k else np.nan


def share_close(dists, tol):
    d = np.asarray([x for x in dists if np.isfinite(x)])
    return float(np.mean(d <= tol)) if len(d) else np.nan


def main():
    bars = pd.read_parquet('runtime_data/minute_bars_v1.parquet')
    book = LevelBook(bars)
    rng = np.random.default_rng(0)
    sigs = [json.loads(l) for l in open('runtime_data/gold_channel_v1/signals.jsonl', encoding='utf-8')]
    pts = []   # (direction, p0, levels, {name: offset})
    for s in sigs:
        if s.get("kind") != "now" or not s.get("zone"):
            continue
        last = (s.get("level_changes") or [{}])[-1]
        sl = last.get("sl") or s.get("sl")
        tps = last.get("tps") or s.get("tps") or []
        t0 = int((datetime.fromisoformat(s["published_utc"]).timestamp() + 1.4) * 1000)
        p0 = price_at(book, t0)
        lv = book.levels(t0)
        if not lv or not np.isfinite(p0):
            continue
        z = sorted(s["zone"])
        best, near = (z[1], z[0]) if s["direction"] == "SELL" else (z[0], z[1])
        offs = {"zona_mejor_entrada": best - p0, "zona_borde_cercano": near - p0}
        if sl:
            offs["stop"] = sl - p0
        if tps:
            offs["tp1"] = tps[0] - p0
            offs["tp_ultimo"] = tps[-1] - p0
        pts.append((s["direction"], p0, lv, offs))
    print("señales de Gold con niveles publicados:", len(pts))
    res = {}
    for name in ("zona_mejor_entrada", "zona_borde_cercano", "stop", "tp1", "tp_ultimo"):
        rows = [(d, p0, lv, o[name]) for d, p0, lv, o in pts if name in o]
        for fam in FAMILIES:
            real = [nearest(p0 + off, lv, fam) for d, p0, lv, off in rows]
            base = []
            for d, p0, lv, off in rows:
                same = [r for r in rows if r[0] == d]
                for _ in range(10):
                    base.append(nearest(p0 + same[rng.integers(len(same))][3], lv, fam))
            key = f"{name} | {'todos' if fam is None else '+'.join(fam)}"
            res[key] = {"n": len(rows), "real_<=0.5$": round(share_close(real, 0.5), 3), "azar_<=0.5$": round(share_close(base, 0.5), 3),
                        "real_<=1$": round(share_close(real, 1.0), 3), "azar_<=1$": round(share_close(base, 1.0), 3),
                        "mediana_real": round(float(np.nanmedian(real)), 2), "mediana_azar": round(float(np.nanmedian(base)), 2)}
    for k, v in res.items():
        if k.endswith("| todos") or "round" in k or "swing_1h" in k or "prev" in k:
            print(f"  {k:42s} ≤1$: real {v['real_<=1$']:.0%} vs azar {v['azar_<=1$']:.0%} | mediana {v['mediana_real']} vs {v['mediana_azar']}")
    # signal moment vs random moments, both channels
    moment = {}
    for ch in ("canal1", "canal2"):
        for label, path in (("real", 'runtime_data/signal_universe_v1/signals.jsonl'),
                            ("azar", 'runtime_data/signal_universe_v1/placebo_random_time_s0.jsonl')):
            ds = []
            for l in open(path, encoding='utf-8'):
                s = json.loads(l)
                if s["channel"] != ch:
                    continue
                t0 = int((datetime.fromisoformat(s["published_utc"]).timestamp() + 1.3) * 1000)
                p0 = price_at(book, t0); lv = book.levels(t0)
                if lv and np.isfinite(p0):
                    fam = ("swing_15m", "swing_1h", "swing_4h", "prev_day", "prev_week", "asia")
                    ds.append(nearest(p0, lv, fam))
            moment[f"{ch} | {label}"] = {"n": len(ds), "<=1$": round(share_close(ds, 1.0), 3), "<=2$": round(share_close(ds, 2.0), 3),
                                         "mediana": round(float(np.nanmedian(ds)), 2)}
    print("precio en el momento de la señal, distancia al nivel de estructura más cercano:")
    for k, v in moment.items():
        print(f"  {k:16s} n {v['n']:4d} ≤1$ {v['<=1$']:.0%} ≤2$ {v['<=2$']:.0%} mediana {v['mediana']}")
    out = Path('out/bloqueP'); out.mkdir(parents=True, exist_ok=True)
    (out / 'p3_niveles.json').write_text(json.dumps({"precios_publicados": res, "momento": moment}, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
