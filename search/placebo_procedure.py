"""Null test of the WHOLE procedure: pick finalists from the placebo (random-time) universe with the
same rules, re-run them on placebo signals over search+validation, and count how many pass the same
validation checks. Tells how often the procedure 'approves' something by pure luck."""
import json, sys, time
sys.path.insert(0, '.')
from pathlib import Path
import numpy as np
from research.search_judge import judge
from research.dubai_iterative.contracts import StrategyGenome
from research.history_search import evaluate
from research.report_stage2 import summarize


def main():
    U = 'runtime_data/signal_universe_v1/'
    FEAT = {'placebo': U + 'features_placebo_random_time_s0.jsonl'}
    SPLIT = {'canal1': '2026-04-01', 'canal2': '2026-05-20'}
    G = json.load(open('search/stage1/genomes.json'))
    res, filters = judge('search/stage1', FEAT, None, SPLIT)
    K = 10
    sel = {}
    for ch in ('canal1', 'canal2'):
        t = res[ch]['placebo']['t']; picked, pg, pf = [], {}, {}
        for fi, gi in zip(*np.unravel_index(np.argsort(-t, axis=None), t.shape)):
            if not np.isfinite(t[fi, gi]): break
            kf = json.dumps(filters[fi], sort_keys=True)
            if pg.get(gi, 0) >= 2 or pf.get(kf, 0) >= 2: continue
            picked.append({'genome': G[ch][gi]['genome'], 'filter': filters[fi], 'universe': 'placebo', 'score': float(t[fi, gi])})
            pg[gi] = pg.get(gi, 0) + 1; pf[kf] = pf.get(kf, 0) + 1
            if len(picked) >= K: break
        sel[ch] = picked
        print(ch, [round(p['score'], 1) for p in picked], flush=True)
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet') if '2026-01-01' <= p.stem < '2026-09-12')
    out = {'selection': sel, 'period': '2026-01-01:2026-09-12', 'rows': {}}
    for sc in ('A_p50', 'C_p50', 'C_p90'):
        cal = {sc: json.load(open(f'runtime_data/execution_calibration_v2/{sc}_q450.json'))}
        t0 = time.time()
        out['rows'][sc] = evaluate('runtime_data/ticks_all', days, {ch: [StrategyGenome.from_dict(x['genome']) for x in sel[ch]] for ch in sel},
                                   cal, workers=2, universe=U + 'placebo_random_time_s0.jsonl',
                                   filters={ch: [x['filter'] for x in sel[ch]] for ch in sel}, features=FEAT['placebo'],
                                   curves=(sc == 'C_p50'))
        print(sc, len(out['rows'][sc]), round(time.time() - t0), flush=True)
    json.dump(out, open('search/pipeline/placebo_procedure.json', 'w'))
    S = summarize('search/pipeline/placebo_procedure.json')
    ok = 0
    for r in S:
        v = [r.get(sc, {}).get('validacion', {}).get('net') for sc in ('A_p50', 'C_p50', 'C_p90')]
        wp, w = r.get('C_p50', {}).get('validacion', {}).get('weeks_pos'), r.get('C_p50', {}).get('validacion', {}).get('weeks')
        passed = all(x is not None and x > 0 for x in v) and bool(w and wp / w >= 0.5)
        ok += passed
        print(r['channel'], r['index'], 'valid', v, 'weeks', wp, w, 'PASS' if passed else '')
    print('placebo finalists passing validation:', ok, 'of', len(S), flush=True)



if __name__ == '__main__':
    main()
