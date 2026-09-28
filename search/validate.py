"""Rank coarse results (search period only) and replay the top-K on later periods."""
import json, sys, time
sys.path.insert(0, '.')
from pathlib import Path
from research.dubai_iterative.contracts import StrategyGenome
from research.history_search import evaluate, metrics

SC = ('A_p50', 'C_p50', 'C_p90')


def rank(coarse, ch):
    by = {}
    for m in coarse['metrics']:
        if m['channel'] == ch:
            by.setdefault(m['genome'], {})[m['scenario']] = m
    out = []
    for gi, s in by.items():
        if set(s) != set(SC):
            continue
        out.append({'genome': gi, 'family': coarse['genomes'][ch][gi]['family'],
                    'robust_net': min(s[k]['net'] for k in SC), 'net_C': s['C_p50']['net'], 'net_A': s['A_p50']['net'],
                    'worst_basket': min(s[k]['worst_basket'] for k in SC), 'worst_month': min(s[k]['worst_month'] for k in SC),
                    'pos_months': min(s[k]['pos_months'] for k in SC), 'months': s['C_p50']['months'],
                    'pf_C': s['C_p50']['pf'], 'n': s['C_p50']['n'],
                    'censored_pct': round(100 * max(s[k]['censored'] / max(s[k]['n'], 1) for k in SC), 1)})
    # baskets left open at day end are valued at the last quote, but a strategy
    # that leaves >2 % open overnight is outside what was validated: rank it last
    return sorted(out, key=lambda r: (r['censored_pct'] <= 2.0, r['robust_net']), reverse=True)


if __name__ == '__main__':
    coarse = json.load(open(sys.argv[1])); K = int(sys.argv[2]); out = Path(sys.argv[3])
    cals = {k: json.load(open(f'runtime_data/execution_calibration_v2/{k}_q450.json')) for k in SC}
    pick = {}
    for ch in ('canal1', 'canal2'):
        r = rank(coarse, ch)
        chosen = [0] + [x['genome'] for x in r if x['genome'] != 0][:K]
        pick[ch] = chosen
    pops = {ch: [StrategyGenome.from_dict(coarse['genomes'][ch][i]['genome']) for i in idx] for ch, idx in pick.items()}
    res = {'picked': pick}
    for name, root, lo, hi in (('valid_jul_sep11', 'runtime_data/hist_ticks', '2026-07-01', '2026-09-12'),
                               ('final_2125', 'runtime_data/week_20260921_v1/attached_20260921_25_full_v1', '2026-09-21', '2026-09-26')):
        days = sorted(p.stem for p in Path(root, 'XAUUSD').glob('*.parquet') if lo <= p.stem < hi)
        t = time.time(); rows = evaluate(root, days, pops, cals, workers=2)
        res[name] = [{'scenario': k[0], 'channel': k[1], 'genome': pick[k[1]][k[2]], **v} for k, v in metrics(rows, None).items()]
        print(name, 'days', len(days), 'secs', round(time.time() - t))
    json.dump(res, open(out, 'w'))
