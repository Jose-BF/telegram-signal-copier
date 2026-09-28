"""M6: run one family (or all) with the exact engine on its real and placebo universes.
Saves runtime_data/search2_engine_v1/<family>__<real|placebo>__<scenario>.npz with
ids, day, pnl [signal x genome] (NaN = not filled), dd, censored. Resumable: existing files are skipped.

usage: python search2/run_family.py <family|all> [scenario=C_p50] [workers=4]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np

from research.history_search import evaluate
from search2.families import FLIP, ensure_flipped_placebo, families, placebo_universes

OUT = Path('runtime_data/search2_engine_v1')


def run(fam, scenario='C_p50', workers=4, labels=('real', 'placebo')):
    OUT.mkdir(parents=True, exist_ok=True)
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet'))
    cal = json.load(open(f'runtime_data/execution_calibration_v2/{scenario}_q450.json'))
    # canal1 families with per-leg targets need the plain protection profile (the live guard has no TP)
    if fam.channel == 'canal1' and any(r.tp_steps or r.sl_move for r in fam.rules):
        cal = {**cal, "policy_extension": {"canal1": "none"}}
    genomes = fam.genomes()
    s1_base, s1_flip = placebo_universes(1)
    universes = (('real', fam.universe), ('placebo', fam.placebo),
                 ('placebo_s1', s1_flip if fam.universe == FLIP else s1_base))
    for label, uni in universes:
        if label not in labels:
            continue
        dst = OUT / f'{fam.name}__{label}__{scenario}.npz'
        if dst.exists():
            print('skip', dst.name, flush=True); continue
        t = time.time()
        rows = evaluate('runtime_data/ticks_all', days, {fam.channel: genomes}, {scenario: cal}, workers=workers, universe=uni)
        ids = sorted({r[4] for r in rows})
        idx = {s: i for i, s in enumerate(ids)}
        pnl = np.full((len(ids), len(genomes)), np.nan, dtype=np.float32)
        dd = np.full_like(pnl, np.nan); cens = np.zeros(pnl.shape, dtype=bool)
        day = np.empty(len(ids), dtype='<U10')
        for sc, ch, gi, d, tid, p, ddv, c in rows:
            i = idx[tid]; day[i] = d
            pnl[i, gi] = np.nan if p is None else p
            dd[i, gi] = np.nan if ddv is None else ddv
            cens[i, gi] = c
        np.savez_compressed(dst, ids=np.array(ids), day=day, pnl=pnl, dd=dd, censored=cens,
                            genomes=np.array([json.dumps(g.to_dict()) for g in genomes]))
        print(f'{fam.name} {label}: {len(ids)} signals x {len(genomes)} genomes, {round(time.time() - t)} s', flush=True)


def main():
    ensure_flipped_placebo()
    which = sys.argv[1]
    scenario = sys.argv[2] if len(sys.argv) > 2 else 'C_p50'
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    labels = tuple(sys.argv[4].split(',')) if len(sys.argv) > 4 else ('real', 'placebo')
    for fam in families():
        if which in ('all', fam.name) or fam.name in which.split(','):
            run(fam, scenario, workers, labels)


if __name__ == '__main__':
    main()
