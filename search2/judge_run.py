"""M7: walk-forward judgement of every family (real vs placebo, carril A and B).

For each family with engine results in runtime_data/search2_engine_v1:
  - candidates = genome x filter (grid_v2, 1,287 filters) and the no-filter subset;
  - walk-forward (expanding window, n_min baskets) on real and on placebo;
  - carril A allows only genomes with a designed stop (per-leg stop, basket cap or hard stop);
  - final pick on all months, with its full-sample monthly record.
Writes out/search2/juicio/<scenario>.json and prints a table.

usage: python search2/judge_run.py [scenario=C_p50]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np

from research.signal_filters import describe, grid_v2, load_features
from research.walkforward import filter_matrix, final_pick, month_stats, walk_forward
from search2.families import FLIP, PLAC, PLAC_FLIP, REAL, U, families

FEATS = {REAL: 'runtime_data/features_v2_real.jsonl', FLIP: 'runtime_data/features_v2_placebo_flipped_s0.jsonl',
         PLAC: 'runtime_data/features_v2_placebo_random_time_s0.jsonl',
         PLAC_FLIP: 'runtime_data/features_v2_placebo_random_time_s0_flipped.jsonl',
         U + 'placebo_random_time_s1.jsonl': 'runtime_data/features_v2_placebo_random_time_s1.jsonl',
         U + 'placebo_random_time_s1_flipped.jsonl': 'runtime_data/features_v2_placebo_random_time_s1_flipped.jsonl'}
ENGINE = Path('runtime_data/search2_engine_v1')
N_MIN = {"canal1": 30, "canal2": 30}
# first traded month: Dubai has 9 months (choose on >= 3), Gold 6 months (choose on >= 2, ~300 signals)
START = {"canal1": 3, "canal2": 2}
# index of the no-filter + single-condition filters inside grid_v2() (v1 singles and v2 singles)
from research.signal_filters import SINGLE, SINGLE_V2
_G = grid_v2()
SIMPLE_IDX = [i for i, f in enumerate(_G) if f == {} or f in SINGLE or f in SINGLE_V2]


def has_stop(rule):
    return bool(rule.sl_move or rule.basket_stop_eur or rule.hard_stop_eur_leg)


def judge_family(fam, scenario, filters, labels=None):
    """labels: [(label, universe)]; default real + placebo s0. Every universe gets the identical judgement."""
    out = {"family": fam.name, "channel": fam.channel, "carril_family": fam.carril, "codes": fam.codes}
    stop_mask = np.array([has_stop(r) for r in fam.rules])
    for label, uni in (labels or (("real", fam.universe), ("placebo", fam.placebo))):
        f = ENGINE / f'{fam.name}__{label}__{scenario}.npz'
        if not f.exists():
            if label == "real":
                return None
            out[label] = {}
            continue
        z = np.load(f, allow_pickle=False)
        ids, day, pnl = z["ids"], z["day"], z["pnl"]
        months = np.array([d[:7] for d in day])
        month_list = sorted(set(months))
        feats = load_features(FEATS[uni])
        F = filter_matrix(ids, feats, filters)
        S, Q, N = month_stats(pnl, months, month_list, F)
        res = {}
        for carril in ("A", "B"):
            allowed = np.broadcast_to(stop_mask[None, :], S.shape[1:]) if carril == "A" else None
            if carril == "A" and not stop_mask.any():
                res[carril] = None; continue
            wf = walk_forward(S, Q, N, month_list, start=START[fam.channel], n_min=N_MIN[fam.channel], allowed=allowed)
            nofilter = walk_forward(S[:, :1], Q[:, :1], N[:, :1], month_list, start=START[fam.channel], n_min=N_MIN[fam.channel],
                                    allowed=None if allowed is None else allowed[:1])
            pick = final_pick(S, Q, N, n_min=N_MIN[fam.channel], allowed=allowed)
            rec = None
            if pick:
                fi, gi, t = pick
                rec = {"filter": filters[fi], "filter_text": describe(filters[fi]), "genome_index": gi,
                       "rule": repr(fam.rules[gi]), "t": t, "net": float(S[:, fi, gi].sum()), "n": int(N[:, fi, gi].sum()),
                       "monthly": {m: round(float(S[k, fi, gi]), 1) for k, m in enumerate(month_list)}}
            wf["months"] = [{**m, "choice": None if m["choice"] is None else
                             {"filter": describe(filters[m["choice"][0]]), "genome": m["choice"][1]}} for m in wf["months"]]
            # more prudent procedures (all reported, none preferred after the fact)
            variants = {}
            for vname, rows_f, nmin in (("simples_n30", SIMPLE_IDX, 30), ("simples_n60", SIMPLE_IDX, 60),
                                        ("todos_n60", None, 60), ("sin_filtro_n60", [0], 60)):
                sl = slice(None) if rows_f is None else rows_f
                al = None if allowed is None else allowed[sl]
                v = walk_forward(S[:, sl], Q[:, sl], N[:, sl], month_list, start=START[fam.channel], n_min=nmin, allowed=al)
                variants[vname] = {k: v[k] for k in ("total", "pos_months", "traded_months", "n_months")}
            res[carril] = {"walk_forward": wf, "walk_forward_no_filter": {k: nofilter[k] for k in ("total", "pos_months", "traded_months", "n_months")},
                           "variants": variants, "final_pick": rec}
        out[label] = res
    return out


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else 'C_p50'
    filters = grid_v2()
    results = []
    for fam in families():
        r = judge_family(fam, scenario, filters)
        if r is None:
            print('pendiente', fam.name); continue
        results.append(r)
        for carril in ("A", "B"):
            rr, pp = r["real"].get(carril), r["placebo"].get(carril)
            if rr is None:
                continue
            w = rr["walk_forward"]
            plac = (f"placebo {pp['walk_forward']['total']:8.1f} ({pp['walk_forward']['pos_months']}/{pp['walk_forward']['n_months']})"
                    if pp else "placebo pendiente")
            fp = rr["final_pick"]
            print(f"{fam.name:22s} carril {carril}: WF real {w['total']:8.1f} ({w['pos_months']}/{w['n_months']} meses+, "
                  f"{w['traded_months']} operados) | {plac} | sin filtro {rr['walk_forward_no_filter']['total']:8.1f} | "
                  f"elegida: {fp and fp['filter_text']} / {fp and fp['genome_index']} neto {fp and round(fp['net'])}", flush=True)
            print("      variantes: " + " | ".join(f"{k} {v['total']:+.0f} ({v['pos_months']}/{v['n_months']})" for k, v in rr["variants"].items()), flush=True)
    out = Path('out/search2/juicio'); out.mkdir(parents=True, exist_ok=True)
    (out / f'{scenario}.json').write_text(json.dumps(results, indent=1, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
