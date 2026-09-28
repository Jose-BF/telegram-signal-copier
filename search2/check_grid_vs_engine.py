"""M2 exactness check: the level-1 calculator (research/basket_grid) against the exact engine
(research/history_search.evaluate, broker C_p50) on the same real signals, all months.

Pass (fixed in plan-busqueda-2-ejecucion.md M2 before running): on baskets filled by both,
the sign agrees on >= 95 % and the total differs <= 10 % for single-leg rules, <= 20 % for basket
rules (relative to the engine's sum of |pnl|, so a near-zero total does not blow the ratio up).

usage: python search2/check_grid_vs_engine.py out.json [workers]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np

from research.basket_bridge import genome_to_rule, rule_to_genome
from research.basket_grid import BasketRule, build_paths, evaluate as grid_eval
from research.dubai_iterative.contracts import StrategyGenome
from research.history_search import evaluate as engine_eval
from tools.shadow_week_replay import shadow_genomes

UNIVERSE = 'runtime_data/signal_universe_v1/signals.jsonl'


def rules():
    simple = [BasketRule(weights=(0.01,), tp_steps=(tp,), sl_move=sl, time_min=t or 0.0, expiry_min=15)
              for tp, sl, t in ((1, 1, 0), (2, 2, 0), (3, 3, 0), (5, 5, 0), (2, 5, 0), (5, 2, 0), (1, 3, 0), (3, 1, 0),
                                (10, 10, 0), (0.5, 2, 0), (7, 3, 0), (1.5, 1.5, 0), (4, 8, 0), (15, 15, 0), (3, 5, 0),
                                (5, 5, 30), (10, 10, 60), (2, 4, 15), (3, 3, 120), (20, 10, 45))]
    sh = dict(shadow_genomes()["canal2"])
    sel = json.load(open('search/pipeline/selection_stage2.json'))
    gold1 = StrategyGenome.from_dict(sel["canal2"][1]["genome"]).with_change(provider_management_mode="ignore")
    c2 = [("gold_555", genome_to_rule(sh["gold_now_555_v1"])), ("gold_b210", genome_to_rule(sh["gold_now_b210_v1"])),
          ("gold_1_search1", genome_to_rule(gold1)),
          ("ladder3_step2_tp1_sl5", BasketRule(weights=(0.01, 0.01, 0.02), step=2.0, tp_steps=(1.0,), sl_move=5.0, expiry_min=30)),
          ("pullback1_tp2_sl3", BasketRule(entry="pullback", e_x=1.0, weights=(0.01,), tp_steps=(2.0,), sl_move=3.0, expiry_min=15)),
          ("trail5_tp10", BasketRule(weights=(0.01,), tp_steps=(10.0,), trail=5.0, expiry_min=15))]
    c1g = dict(shadow_genomes()["canal1"])
    dubai3 = StrategyGenome.from_dict(sel["canal1"][3]["genome"]).with_change(provider_management_mode="ignore")
    c1 = [("dubai_balanced", genome_to_rule(c1g["dubai_balanced_v1"])),
          ("dubai_frontloaded_30m", genome_to_rule(c1g["dubai_frontloaded_30m_v1"])),
          ("dubai_3_search1", genome_to_rule(dubai3))]
    return ([(f"simple_{i}", "canal2", r, "simple") for i, r in enumerate(simple)]
            + [(n, "canal2", r, "basket") for n, r in c2] + [(n, "canal1", r, "basket") for n, r in c1])


def main():
    out = Path(sys.argv[1]); workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    R = rules()
    t = time.time()
    paths = build_paths('runtime_data/ticks_all', UNIVERSE)
    print('paths', len(paths.ids), round(time.time() - t), 's', flush=True)
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet'))
    cal = {'C_p50': json.load(open('runtime_data/execution_calibration_v2/C_p50_q450.json'))}
    pops = {ch: [rule_to_genome(r) for _, c, r, _ in R if c == ch] for ch in ('canal1', 'canal2')}
    t = time.time()
    rows = engine_eval('runtime_data/ticks_all', days, pops, cal, workers=workers)
    print('engine rows', len(rows), round(time.time() - t), 's', flush=True)
    names = {ch: [n for n, c, _, _ in R if c == ch] for ch in pops}
    eng = {}
    for sc, ch, gi, day, tid, pnl, dd, cens in rows:
        eng[(names[ch][gi], tid)] = pnl
    report = []
    idx = {s: i for i, s in enumerate(paths.ids)}
    for name, ch, rule, kind in R:
        m = paths.channel == ch
        g = grid_eval(paths, [rule], mask=m)
        ids = paths.ids[m]
        gp = {tid: g["pnl"][i, 0] for i, tid in enumerate(ids) if g["legs"][i, 0] > 0}
        ep = {tid: v for (n, tid), v in eng.items() if n == name and v is not None}
        both = sorted(set(gp) & set(ep))
        a = np.array([gp[k] for k in both]); b = np.array([ep[k] for k in both])
        sign = float(np.mean(np.sign(np.round(a, 2)) == np.sign(np.round(b, 2)))) if len(both) else None
        rel = float(abs(a.sum() - b.sum()) / max(np.abs(b).sum(), 1e-9)) if len(both) else None
        limit = 0.10 if kind == "simple" else 0.20
        ok = sign is not None and sign >= 0.95 and rel <= limit
        report.append({"rule": name, "channel": ch, "kind": kind, "grid_filled": len(gp), "engine_filled": len(ep),
                       "both": len(both), "sign_agree": sign, "grid_total": float(a.sum()) if len(both) else None,
                       "engine_total": float(b.sum()) if len(both) else None, "rel_diff_vs_abs_sum": rel,
                       "limit": limit, "pass": bool(ok)})
        print(f"{name:26s} {kind:6s} filled g/e {len(gp):4d}/{len(ep):4d} both {len(both):4d} sign {sign or 0:.3f} "
              f"tot g/e {a.sum() if len(both) else 0:8.1f}/{b.sum() if len(both) else 0:8.1f} rel {rel or 0:.3f} {'OK' if ok else 'FALLA'}",
              flush=True)
    json.dump({"report": report, "passed": sum(r["pass"] for r in report), "total": len(report)}, open(out, 'w'), indent=1)
    print('passed', sum(r["pass"] for r in report), '/', len(report))


if __name__ == '__main__':
    main()
