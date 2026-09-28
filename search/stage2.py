"""Stage 2: exact re-run of the chosen combinations (filter applied inside the replay,
companion rule included) on SEARCH + VALIDATION periods, three broker scenarios, with
minute equity curves for the account / prop checks. The FINAL exam period is run
separately, only after the selection is frozen (search/stage2_final.py)."""
import json, sys, time
sys.path.insert(0, '.')
from pathlib import Path
from research.dubai_iterative.contracts import StrategyGenome
from research.history_search import evaluate


def main():

    sel = json.load(open(sys.argv[1]))          # {"canal1": [{"genome": {...}, "filter": {...}, "label": ...}], "canal2": [...]}
    out = Path(sys.argv[2]); period = sys.argv[3]  # "2026-01-01:2026-09-12"
    lo, hi = period.split(':')
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet') if lo <= p.stem < hi)
    U = 'runtime_data/signal_universe_v1/'
    cals = {k: json.load(open(f'runtime_data/execution_calibration_v2/{k}_q450.json')) for k in ('A_p50', 'C_p50', 'C_p90')}
    UNI = {'real': (U + 'signals.jsonl', U + 'features.jsonl'),
           'inverted': (U + 'placebo_flipped_s0.jsonl', U + 'features_placebo_flipped_s0.jsonl')}
    res = {sc: [] for sc in cals}
    for uname, (upath, fpath) in UNI.items():
        idx = {ch: [i for i, x in enumerate(sel.get(ch, [])) if x.get('universe', 'real') == uname] for ch in sel}
        if not any(idx.values()):
            continue
        pops = {ch: [StrategyGenome.from_dict(sel[ch][i]['genome']) for i in ix] for ch, ix in idx.items() if ix}
        filters = {ch: [sel[ch][i]['filter'] for i in ix] for ch, ix in idx.items() if ix}
        for sc, cal in cals.items():
            t = time.time()
            rows = evaluate('runtime_data/ticks_all', days, pops, {sc: cal}, workers=2, universe=upath,
                            filters=filters, features=fpath, curves=(sc == 'C_p50'))
            for r in rows:   # map back to the selection index
                r = list(r); r[2] = idx[r[1]][r[2]]; res[sc].append(r)
            print(uname, sc, 'rows', len(rows), 'secs', round(time.time() - t), flush=True)
    json.dump({'selection': sel, 'period': period, 'rows': res}, open(out, 'w'))
    print('done', flush=True)



if __name__ == '__main__':
    main()
