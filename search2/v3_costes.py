"""V3: FTMO costs on the frozen recipes R1 and R2 (criterion fixed in plan-verificacion-reto.md V3:
positive with the real commission and spread +0.1 USD).

Exact fast engine over every signal the recipe takes (all days), scenarios:
  base (C_p50) | spread +0.10 | spread +0.20 (engine `spread_addition`) | 2-minute broker delay.
Commission is subtracted exactly per basket: filled lots x commission per lot round turn / EURUSD,
for the two published FTMO figures (5.81 and 22.50 USD per lot round turn).
usage: python search2/v3_costes.py
"""
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.fast_engine import FastEvaluator
from research.history_replay import make_trigger_path
from research.history_search import build_history, execution_for
from research.signal_filters import load_features, passes

COMMISSIONS = (0.0, 5.81, 22.50)
RECIPES = (("R1", "c2_escalera", 210, "canal2", {"cond": [["asia_loc", ">", 0.8]]}),
           ("R2", "c1_seguir_1pata", 34, "canal1", {"cond": [["rsi_m5", "<", 30]]}))


def scenarios(channel):
    cal = json.load(open('runtime_data/execution_calibration_v2/C_p50_q450.json'))
    if channel == "canal1":
        cal = {**cal, "policy_extension": {"canal1": "none"}}
    base = execution_for(cal, channel)
    slow = {**cal, "delays_ms": {**cal["delays_ms"], "entry_fill_latency_ms": 120_000, "close_processing_ms": 120_000,
                                 "protection_processing_ms": 120_000}}
    return {"base": base, "spread+0.10": dataclasses.replace(base, spread_addition=0.10),
            "spread+0.20": dataclasses.replace(base, spread_addition=0.20), "retraso_2min": execution_for(slow, channel)}


def main():
    feats = load_features('runtime_data/features_v2_real.jsonl')
    hist = build_history('runtime_data/ticks_all')
    out = {}
    for name, family, gi, channel, spec in RECIPES:
        z = np.load(f'runtime_data/search2_engine_v1/{family}__real__C_p50.npz')
        g = StrategyGenome.from_dict(json.loads(z['genomes'][gi]))
        sc = scenarios(channel)
        evs = {k: FastEvaluator(execution=v) for k, v in sc.items()}
        rows = {k: [] for k in sc}
        open_until = []
        for day, market, conv, items in hist.days():
            for trig, sig, te in sorted(items, key=lambda x: x[1].observed_at):
                if sig.channel != channel or not passes(spec, feats.get(trig.trigger_id)):
                    continue
                path, cut = make_trigger_path(sig, g, market, conv, te)
                fx = float(np.nanmedian((np.asarray(path.fx_bid) + np.asarray(path.fx_ask)) / 2))
                for k, ev in evs.items():
                    r = ev(path, g)
                    if not r.filled_volume or r.pnl_eur is None:
                        continue
                    rows[k].append((day[:7], float(r.pnl_eur), float(r.filled_volume), fx))
            for ev in evs.values():
                ev.clear_cache()
        res = {}
        for k, rr in rows.items():
            months = sorted({m for m, *_ in rr})
            for c in COMMISSIONS:
                pnl = np.array([p - v * c / fx for _, p, v, fx in rr])
                mon = np.array([m for m, *_ in rr])
                by = {m: float(pnl[mon == m].sum()) for m in months}
                res[f"{k} | comision {c}"] = {"n": len(rr), "net": round(float(pnl.sum()), 1),
                                              "pos_months": sum(v > 0 for v in by.values()), "months": len(months),
                                              "lots_mean": round(float(np.mean([v for _, _, v, _ in rr])), 3)}
        out[name] = res
        print(name)
        for k, v in res.items():
            print(f"   {k:30s} n {v['n']:4d} neto {v['net']:8.1f} meses+ {v['pos_months']}/{v['months']} lotes medios {v['lots_mean']}")
    dst = Path('out/search2/v3'); dst.mkdir(parents=True, exist_ok=True)
    (dst / 'v3_costes.json').write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
