"""V2: robustness of the frozen recipes R1 and R2 (criteria fixed in plan-verificacion-reto.md V2:
>= 70 % of neighbours positive, and positive without its best 5 % baskets). Uses the exact-engine
results already stored in runtime_data/search2_engine_v1 (broker C_p50, real signals).

Neighbours = every genome of the family that differs from the recipe in exactly one parameter,
plus the recipe with the filter threshold moved one step each way. Also reported (descriptive):
result by volatility regime (vol_regime) and by quarter.
usage: python search2/v2_robustez.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np

from research.signal_filters import load_features, passes
from search2.families import families

FEATS = load_features('runtime_data/features_v2_real.jsonl')
FIELDS = ("entry", "e_x", "e_y", "expiry_min", "weights", "ladder", "step", "tp_steps", "sl_move", "trail",
          "be_trigger", "basket_stop_eur", "lock_arm_eur", "lock_give_eur", "time_min", "time_mode", "hard_stop_eur_leg")


def mask_for(ids, spec):
    return np.array([passes(spec, FEATS.get(i)) for i in ids])


def stats(p, months):
    ok = ~np.isnan(p)
    p, months = p[ok], months[ok]
    if not len(p):
        return {"n": 0, "net": 0.0, "pos_months": 0, "months": 0}
    ms = sorted(set(months))
    by = {m: float(p[months == m].sum()) for m in ms}
    srt = np.sort(p)[::-1]
    k = int(np.ceil(0.05 * len(p)))
    return {"n": int(len(p)), "net": round(float(p.sum()), 1), "pos_months": sum(v > 0 for v in by.values()),
            "months": len(ms), "net_without_top5pct": round(float(srt[k:].sum()), 1),
            "monthly": {m: round(v, 1) for m, v in by.items()}}


def one_param_neighbours(rules, gi):
    base = rules[gi]
    out = []
    for j, r in enumerate(rules):
        if j == gi:
            continue
        diff = [f for f in FIELDS if getattr(r, f) != getattr(base, f)]
        # weights/legs move together with the number of legs: count as one parameter
        if len(diff) == 1:
            out.append((j, diff[0], getattr(r, diff[0])))
    return out


def recipe(name, family, gi, spec, thr_key, thr_values):
    fam = {f.name: f for f in families()}[family]
    z = np.load(f'runtime_data/search2_engine_v1/{family}__real__C_p50.npz')
    ids, months = z['ids'], np.array([d[:7] for d in z['day']])
    m = mask_for(ids, spec)
    base = stats(np.where(m, z['pnl'][:, gi], np.nan), months)
    neigh = []
    for j, field, value in one_param_neighbours(fam.rules, gi):
        s = stats(np.where(m, z['pnl'][:, j], np.nan), months)
        neigh.append({"cambio": f"{field}={value}", **{k: s[k] for k in ("n", "net", "pos_months", "months")}})
    for v in thr_values:
        spec2 = {"cond": [[thr_key, spec["cond"][0][1], v]]}
        s = stats(np.where(mask_for(ids, spec2), z['pnl'][:, gi], np.nan), months)
        neigh.append({"cambio": f"filtro {thr_key} {spec['cond'][0][1]} {v}", **{k: s[k] for k in ("n", "net", "pos_months", "months")}})
    pos = sum(x["net"] > 0 for x in neigh)
    vr = np.array([float(FEATS.get(i, {}).get("vol_regime", np.nan)) for i in ids])
    regimes = {}
    for lab, lo, hi in (("volatilidad baja (<0.8)", -1, 0.8), ("normal (0.8-1.2)", 0.8, 1.2), ("alta (>1.2)", 1.2, 99)):
        sel = m & (vr >= lo) & (vr < hi)
        regimes[lab] = {k: v for k, v in stats(np.where(sel, z['pnl'][:, gi], np.nan), months).items() if k != "monthly"}
    quarters = {}
    for q, ms in (("T1", ("2026-01", "2026-02", "2026-03")), ("T2", ("2026-04", "2026-05", "2026-06")), ("T3", ("2026-07", "2026-08", "2026-09"))):
        sel = m & np.isin(months, ms)
        if sel.any():
            quarters[q] = {k: v for k, v in stats(np.where(sel, z['pnl'][:, gi], np.nan), months).items() if k != "monthly"}
    verdict = {"vecinos_positivos": f"{pos}/{len(neigh)}", "pct": round(pos / max(len(neigh), 1), 2),
               "pasa_vecinos": pos / max(len(neigh), 1) >= 0.70, "pasa_sin_top5": base["net_without_top5pct"] > 0}
    return {"receta": name, "base": base, "vecinos": neigh, "regimen": regimes, "trimestres": quarters, "veredicto": verdict}


def main():
    res = [recipe("R1", "c2_escalera", 210, {"cond": [["asia_loc", ">", 0.8]]}, "asia_loc", (0.7, 0.9)),
           recipe("R2", "c1_seguir_1pata", 34, {"cond": [["rsi_m5", "<", 30]]}, "rsi_m5", (25, 35))]
    out = Path('out/search2/v2'); out.mkdir(parents=True, exist_ok=True)
    (out / 'v2_robustez.json').write_text(json.dumps(res, indent=1, default=str), encoding='utf-8')
    for r in res:
        b = r["base"]
        print(f"\n{r['receta']}: n {b['n']} neto {b['net']} meses+ {b['pos_months']}/{b['months']} sin top5% {b['net_without_top5pct']}")
        print("  veredicto:", r["veredicto"])
        for x in r["vecinos"]:
            print(f"   {x['cambio']:40s} n {x['n']:4d} neto {x['net']:8.1f} meses+ {x['pos_months']}/{x['months']}")
        print("  régimen:", {k: (v.get('n'), v.get('net')) for k, v in r["regimen"].items()})
        print("  trimestres:", {k: (v.get('n'), v.get('net'), f"{v.get('pos_months')}/{v.get('months')}") for k, v in r["trimestres"].items()})


if __name__ == '__main__':
    main()
