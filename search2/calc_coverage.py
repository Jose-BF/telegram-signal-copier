"""Coverage of the catalogue branches the exact engine cannot run (or that are cheap to screen),
judged with the same honest walk-forward, real vs random-time placebo (calculator, level 1).

Branches: E4 momentum, E5 delay, L3 favourable ladder, X3 partial+runner, X6 break-even,
R4 hard stop EUR/leg, D3 direction decided by the first move, D4 both directions at once,
K2 lot by volatility, S6 learned selector (logistic regression, nested by month).
Calculator caveat (M2): basket rules carry ~±0.5 EUR/basket noise; anything promising here must
be confirmed with the engine (if the engine/bot can express it) before becoming a recipe.

usage: python search2/calc_coverage.py
"""
import itertools
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np

from research.basket_grid import BasketRule, build_paths, evaluate
from research.bracket_grid import SimpleRule, simple_outcomes
from research.signal_filters import SINGLE, SINGLE_V2, load_features
from research.signal_paths import load
from research.walkforward import filter_matrix, month_stats, walk_forward

U = 'runtime_data/signal_universe_v1/'
FILTERS = [{}] + SINGLE + SINGLE_V2
START = {"canal1": 3, "canal2": 2}
NUMERIC = ("pre_move_5", "pre_move_15", "pre_move_60", "pre_range_60", "rsi_m5", "rsi_m15", "atr_m15", "trend_m15",
           "loc_4h", "day_move", "gap_prev_min", "session", "min_in_session", "pd_loc", "pd_break", "asia_loc",
           "sweep_fav", "sweep_adv", "dist_round50", "vol_regime", "news_min", "burst_60", "dir_streak", "prev_outcome")


def families():
    tpsl = list(itertools.product((2.0, 5.0, 10.0), (3.0, 5.0, 10.0)))
    fam = {
        "E4_momentum": [BasketRule(entry="momentum", e_x=x, weights=(0.01,), tp_steps=(tp,), sl_move=sl, time_min=60,
                                   expiry_min=15) for x in (0.5, 1.0, 2.0, 3.0, 5.0) for tp, sl in tpsl],
        "E5_delay": [BasketRule(entry="delay", delay_min=d, weights=(0.01,), tp_steps=(tp,), sl_move=sl, time_min=60,
                                expiry_min=max(30, d + 5)) for d in (1, 3, 5, 10, 20) for tp, sl in tpsl],
        "L3_escalera_a_favor": [BasketRule(weights=(0.01, 0.01, 0.01), ladder="favourable", step=s, tp_steps=(tp,),
                                           sl_move=sl, time_min=120, expiry_min=30)
                                for s in (1.0, 2.0, 3.0, 5.0) for tp in (3.0, 5.0, 10.0) for sl in (5.0, 10.0)],
        "X3_parcial_y_resto": [BasketRule(weights=(0.02,), partial_frac=0.5, partial_tp=p, runner_tp=r, sl_move=sl,
                                          time_min=120, expiry_min=15)
                               for p in (1.0, 2.0, 3.0) for r in (5.0, 10.0, 20.0) for sl in (3.0, 5.0, 10.0)],
        "X6_break_even": [BasketRule(weights=(0.01,), be_trigger=b, tp_steps=(tp,), sl_move=sl, time_min=120, expiry_min=15)
                          for b in (1.0, 2.0, 3.0, 5.0) for tp in (5.0, 10.0, 20.0) for sl in (5.0, 10.0)],
        "R4_stop_duro_555": [BasketRule(entry="adverse_reversal", e_x=x, e_y=1.5, weights=(0.04, 0.03, 0.03, 0.03, 0.03),
                                        step=1.5, tp_steps=(0.5, 1.0, 1.5, 2.0, 2.5), hard_stop_eur_leg=h,
                                        lock_arm_eur=30.0, lock_give_eur=1.0, time_min=180, time_mode="non_negative",
                                        expiry_min=30) for x in (1.0, 2.0) for h in (5.0, 10.0, 20.0, 40.0)],
    }
    return fam


def wf_summary(P, months_arr, ids, feats, channel, n_min=60):
    month_list = sorted(set(months_arr))
    F = filter_matrix(ids, feats, FILTERS)
    S, Q, N = month_stats(P, months_arr, month_list, F)
    out = {}
    for name, sl in (("con_filtros", slice(None)), ("sin_filtro", slice(0, 1))):
        w = walk_forward(S[:, sl], Q[:, sl], N[:, sl], month_list, start=START[channel], n_min=n_min)
        out[name] = {k: w[k] for k in ("total", "pos_months", "n_months", "traded_months")}
    return out


def logistic_fit(X, y, l2=1.0, iters=300, lr=0.1):
    w = np.zeros(X.shape[1]); b = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = p - y
        w -= lr * (X.T @ g / len(y) + l2 * w / len(y)); b -= lr * g.mean()
    return w, b


def selector_wf(pnl, months_arr, ids, feats, channel, threshold=0.55):
    """S6: nested by month. Train on earlier months (label pnl > 0), trade month m only where p > threshold."""
    X = np.array([[float(feats.get(i, {}).get(k, np.nan)) if feats.get(i) and not isinstance(feats[i].get(k), str)
                   and feats[i].get(k) is not None else np.nan for k in NUMERIC] for i in ids])
    miss = np.isnan(X)
    month_list = sorted(set(months_arr))
    traded, base = [], []
    filled = ~np.isnan(pnl)
    for i in range(START[channel], len(month_list)):
        tr = np.isin(months_arr, month_list[:i]) & filled
        te = (months_arr == month_list[i]) & filled
        if tr.sum() < 60 or te.sum() == 0:
            continue
        mu = np.nanmean(X[tr], 0); sd = np.nanstd(X[tr], 0) + 1e-9
        Z = np.where(miss, 0.0, (X - mu) / sd)
        Z = np.hstack([Z, miss.astype(float)])
        w, b = logistic_fit(Z[tr], (pnl[tr] > 0).astype(float))
        p = 1 / (1 + np.exp(-(Z[te] @ w + b)))
        traded.append(float(pnl[te][p > threshold].sum())); base.append(float(pnl[te].sum()))
    return {"total": round(sum(traded), 1), "pos_months": int(sum(t > 0 for t in traded)), "n_months": len(traded),
            "without_selector": round(sum(base), 1)}


def main():
    res = {}
    fam = families()
    uni = {"real": U + 'signals.jsonl', "placebo": U + 'placebo_random_time_s0.jsonl'}
    feats = {"real": load_features('runtime_data/features_v2_real.jsonl'),
             "placebo": load_features('runtime_data/features_v2_placebo_random_time_s0.jsonl')}
    # invert=True on the placebo = random time with flipped direction, the right control for inverted rules
    for label in ("real", "placebo"):
        paths = build_paths('runtime_data/ticks_all', uni[label])
        months_arr = np.array([o[:7] for o in paths.observed])
        for ch in ("canal1", "canal2"):
            m = paths.channel == ch
            ids = paths.ids[m]; mo = months_arr[m]
            for name, rules in fam.items():
                for inv in (False, True):
                    rr = [BasketRule(**{**r.__dict__, "invert": inv}) for r in rules]
                    o = evaluate(paths, rr, mask=m)
                    P = np.where(o["legs"] > 0, o["pnl"], np.nan)
                    key = f"{ch}|{name}|{'invertir' if inv else 'seguir'}"
                    res.setdefault(key, {})[label] = wf_summary(P, mo, ids, feats[label], ch)
            # D3: direction decided by the first move of X (momentum on both sides, the first fill wins)
            for x in (1.0, 2.0, 3.0, 5.0):
                rules = [BasketRule(entry="momentum", e_x=x, weights=(0.01,), tp_steps=(tp,), sl_move=sl, time_min=60, expiry_min=15)
                         for tp, sl in itertools.product((2.0, 5.0, 10.0), (3.0, 5.0, 10.0))]
                a = evaluate(paths, rules, mask=m)
                b = evaluate(paths, [BasketRule(**{**r.__dict__, "invert": True}) for r in rules], mask=m)
                fa = np.where(a["entry_second"] >= -1, a["entry_second"], 10**9)
                fb = np.where(b["entry_second"] >= -1, b["entry_second"], 10**9)
                pick_a = fa <= fb
                P = np.where(pick_a, np.where(a["legs"] > 0, a["pnl"], np.nan), np.where(b["legs"] > 0, b["pnl"], np.nan))
                res.setdefault(f"{ch}|D3_direccion_mercado_x{x}|-", {})[label] = wf_summary(P, mo, ids, feats[label], ch)
            # D4: both directions at once (one basket each way, same rule)
            rules = [BasketRule(weights=(0.01,), tp_steps=(tp,), sl_move=sl, time_min=t, expiry_min=15)
                     for tp, sl, t in itertools.product((2.0, 5.0, 10.0), (3.0, 5.0, 10.0), (30, 60, 120))]
            a = evaluate(paths, rules, mask=m); b = evaluate(paths, [BasketRule(**{**r.__dict__, "invert": True}) for r in rules], mask=m)
            res.setdefault(f"{ch}|D4_dos_direcciones|-", {})[label] = wf_summary(a["pnl"] + b["pnl"], mo, ids, feats[label], ch)
            print(label, ch, 'hecho', flush=True)
    # K2 (lot by volatility) and S6 (selector) on the simple single-leg grid, real vs placebo
    rules = [SimpleRule(inv, tp, sl, t) for inv in (False, True) for tp in (2.0, 5.0, 10.0, 20.0)
             for sl in (3.0, 5.0, 10.0, 20.0) for t in (30, 60, 120)]
    for label, npz in (("real", 'real'), ("placebo", 'placebo_random_time_s0')):
        z = load(f'runtime_data/signal_paths_v1/{npz}.npz')
        P = simple_outcomes(z, rules).astype(float)
        months_arr = np.array([o[:7] for o in z['observed']])
        atr = np.array([float(feats[label].get(i, {}).get("atr_m15", np.nan)) for i in z['ids']])
        for ch in ("canal1", "canal2"):
            m = z['channel'] == ch
            scale = np.nan_to_num(np.nanmedian(atr[m]) / atr[m], nan=1.0)
            res.setdefault(f"{ch}|K2_lote_por_volatilidad|-", {})[label] = wf_summary(P[m] * scale[:, None], months_arr[m], z['ids'][m], feats[label], ch)
            sel = {}
            for j, r in enumerate(rules):
                if (r.tp, r.sl, r.time_min) in ((5.0, 5.0, 60), (10.0, 10.0, 60), (20.0, 10.0, 60), (5.0, 20.0, 120)):
                    sel[r.label()] = selector_wf(P[m, j], months_arr[m], z['ids'][m], feats[label], ch)
            res.setdefault(f"{ch}|S6_selector_aprendido|-", {})[label] = sel
    out = Path('out/search2/cobertura'); out.mkdir(parents=True, exist_ok=True)
    (out / 'calc_coverage.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    for key, v in res.items():
        if "S6" in key:
            for rl, s in v.get("real", {}).items():
                ps = v.get("placebo", {}).get(rl, {})
                print(f"{key:45s} {rl:30s} real {s['total']:+8.1f} ({s['pos_months']}/{s['n_months']}) sin selector {s['without_selector']:+8.1f} | placebo {ps.get('total', 0):+8.1f}")
            continue
        r, p = v.get("real", {}), v.get("placebo", {})
        print(f"{key:45s} real con filtros {r['con_filtros']['total']:+8.1f} ({r['con_filtros']['pos_months']}/{r['con_filtros']['n_months']}) "
              f"sin {r['sin_filtro']['total']:+8.1f} | placebo con {p['con_filtros']['total']:+8.1f} sin {p['sin_filtro']['total']:+8.1f}")


if __name__ == '__main__':
    main()
